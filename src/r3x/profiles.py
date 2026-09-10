"""Explicit offline profile review, independent import, and previewed re-embedding.

No v2 imports, mutable connections or implicit baseline data access. Callers hold
the v3 live lease while applying repairs, so no live cache can retain deleted data.
"""
import base64
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import struct
from uuid import uuid4

from .contracts import Cancellation
from .speakers import unit,cosine


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def write_preview(path,body):
    result = {**body,"preview_digest":digest(body)}
    with path.open("x") as handle:
        json.dump(result,handle,indent=2,allow_nan=False)
    path.chmod(0o600)
    return result


def read_preview(path):
    data = json.loads(path.read_text())
    expected = data.pop("preview_digest")
    if digest(data)!=expected:
        raise ValueError("PreviewDigestMismatch")
    return data,expected


def pcm_from_original(wav):
    """Decode only reviewed mono 16 kHz PCM16 / float32 RIFF; preserve WAV separately."""
    if len(wav)>2*1024*1024 or wav[:4]!=b"RIFF" or wav[8:12]!=b"WAVE":
        raise ValueError("InvalidOriginalWave")
    fmt = data = None
    offset = 12
    while offset+8<=len(wav):
        tag,size = wav[offset:offset+4],struct.unpack_from("<I",wav,offset+4)[0]
        offset += 8
        chunk = wav[offset:offset+size]
        if len(chunk)!=size:
            raise ValueError("TruncatedOriginalWave")
        if tag==b"fmt ": fmt = chunk
        if tag==b"data": data = chunk
        offset += size+size%2
    if fmt is None or len(fmt)<16 or data is None:
        raise ValueError("MissingWaveChunks")
    code,channels,rate,_,align,bits = struct.unpack_from("<HHIIHH",fmt)
    if channels!=1 or rate!=16000 or (code,bits) not in {(1,16),(3,32)} or len(data)%align:
        raise ValueError("ImportRequiresMono16kPCM16OrFloat32")
    if code==1:
        pcm = data
    else:
        values = [v[0] for v in struct.iter_unpack("<f",data)]
        if not all(math.isfinite(v) and abs(v)<=1.01 for v in values):
            raise ValueError("InvalidFloatWave")
        pcm = b"".join(struct.pack("<h",max(-32768,min(32767,round(v*32768)))) for v in values)
    if not 6400<=len(pcm)<=640000:
        raise ValueError("OriginalDurationOutOfRange")
    return pcm


def preview_v2(source_path,person_ids,output):
    if not person_ids or len(set(person_ids))!=len(person_ids):
        raise ValueError("ExplicitUniqueSourcePersonIdsRequired")
    # SQLite backup includes committed WAL state; the source connection is read-only.
    snapshot = sqlite3.connect(":memory:")
    snapshot.row_factory = sqlite3.Row
    try:
        with sqlite3.connect(source_path.resolve().as_uri()+"?mode=ro",uri=True) as source:
            source.backup(snapshot)
        source_digest = hashlib.sha256(snapshot.serialize()).hexdigest()
        tables = {r[0] for r in snapshot.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        selected = []
        for source_id in person_ids:
            person = snapshot.execute("SELECT id,name,nickname FROM people WHERE id=?",(source_id,)).fetchone()
            if person is None:
                raise ValueError("UnknownSourcePersonId")
            aliases = [person["nickname"]] if person["nickname"] else []
            if "person_aliases" in tables:
                aliases += [r[0] for r in snapshot.execute("SELECT alias FROM person_aliases WHERE person_id=?",(source_id,))]
            records = []
            if "voice_recordings" in tables:
                for row in snapshot.execute("SELECT * FROM voice_recordings WHERE person_id=? ORDER BY id",(source_id,)):
                    wav = bytes(row["wav"])
                    if hashlib.sha256(wav).hexdigest()!=row["digest"]:
                        raise ValueError("SourceRecordingDigestMismatch")
                    pcm_from_original(wav)
                    records.append({"source_id":row["id"],"digest":row["digest"],"wav":base64.b64encode(wav).decode(),
                                    "metadata":json.loads(row["metadata"])})
            vectors = []
            for row in snapshot.execute("SELECT id,type,encoding FROM biometrics WHERE person_id=? AND type LIKE '%campplus%'",(source_id,)):
                blob = bytes(row["encoding"])
                if len(blob)!=192*4:
                    raise ValueError("SourceEmbeddingDimensionMismatch")
                vector = unit(tuple(v[0] for v in struct.iter_unpack("<f",blob)))
                if vector is None:
                    raise ValueError("InvalidSourceEmbedding")
                vectors.append({"source_id":row["id"],"namespace":"v2:"+row["type"]+":unverified-provenance",
                                "vector":vector,"sha256":hashlib.sha256(blob).hexdigest()})
            if len(records)>20 or len(vectors)>20:
                raise ValueError("SourceProfileExceedsCoreBound")
            selected.append({"source_id":str(source_id),"name":person["name"],"aliases":list(dict.fromkeys(aliases))[:8],
                             "recordings":records,"legacy_embeddings":vectors})
        return write_preview(output,{"version":1,"kind":"v2-import","source_snapshot_sha256":source_digest,
            "people":selected,"policy":"independent selected copies; claims empty; suspect until reviewed; legacy namespaces inactive"})
    finally:
        snapshot.close()


def apply_import(memory,path):
    data,preview_digest = read_preview(path)
    if data.get("kind")!="v2-import" or data.get("version")!=1:
        raise ValueError("WrongImportPreview")
    with memory.transaction() as conn:
        old = conn.execute("SELECT mapping FROM imports WHERE digest=?",(preview_digest,)).fetchone()
        if old:
            return json.loads(old[0])
        mapping = {}
        for person in data["people"]:
            if memory.resolve(person["name"]):
                raise ValueError("ImportNameAlreadyExistsReviewMappingFirst")
            pid = memory.add_person(conn,person["name"],person["aliases"],None,"suspect",
                                    json.dumps({"snapshot":data["source_snapshot_sha256"],"source_id":person["source_id"]}))
            mapping[person["source_id"]] = pid
            for record in person["recordings"]:
                wav = base64.b64decode(record["wav"],validate=True)
                if hashlib.sha256(wav).hexdigest()!=record["digest"]:
                    raise ValueError("ImportedWaveDigestMismatch")
                pcm = pcm_from_original(wav)
                rid = uuid4().hex
                metadata = {"source":"v2_import","source_recording_id":record["source_id"],
                    "source_snapshot_sha256":data["source_snapshot_sha256"],"original_metadata":record["metadata"]}
                conn.execute("INSERT INTO recordings VALUES(?,?,?,?,?,?)",(rid,pid,hashlib.sha256(pcm).hexdigest(),pcm,16000,json.dumps(metadata)))
                conn.execute("INSERT INTO originals VALUES(?,?,?)",(rid,record["digest"],wav))
            for reference in person["legacy_embeddings"]:
                conn.execute("INSERT INTO legacy_embeddings VALUES(?,?,?,?,?)",(uuid4().hex,pid,reference["namespace"],
                    json.dumps(reference["vector"]),json.dumps({"source":reference["source_id"],"sha256":reference["sha256"],
                    "snapshot":data["source_snapshot_sha256"]})))
        conn.execute("INSERT INTO imports VALUES(?,?)",(preview_digest,json.dumps(mapping)))
        for person_id in mapping.values():
            memory.register_artifact(conn,path,person_id)
        memory.advance(conn)
        return mapping


def preview_reembed(memory,encoder,person_id,output):
    if memory.person(person_id) is None:
        raise ValueError("UnknownPersonId")
    rows = memory.rows("SELECT * FROM recordings WHERE person_id=? ORDER BY id",(person_id,))
    if not rows:
        raise ValueError("NoOriginalRecordingsForReembedding")
    from .contracts import Capture
    from .dataset import segment
    # Reuse actual VAD/window analysis and namespace, never silently reinterpret an old vector.
    from .adapters.audio import SileroVAD
    vad = SileroVAD(.5)
    changes = []
    try:
        vad.start()
        for row in rows:
            captures = list(segment(row["pcm"],"reembed",vad))
            if len(captures)!=1:
                raise ValueError("ReembeddingRequiresOneReviewedCapturePerRecording")
            observation = encoder.analyze(captures[0],Cancellation())
            if observation.embedding is None or observation.consistency is None or observation.consistency<.55:
                raise ValueError("RecordingFailedCurrentVoiceQuality")
            old = memory.rows("SELECT vector FROM embeddings WHERE recording_id=? AND namespace=?",(row["id"],encoder.capability.model))
            changes.append({"recording_id":row["id"],"source_pcm_digest":row["digest"],"vector":observation.embedding,
                            "same_namespace_similarity":cosine(tuple(json.loads(old[0]["vector"])),observation.embedding) if old else None})
    finally:
        vad.close()
    preview = write_preview(output,{"version":1,"kind":"reembedding","person_id":person_id,"revision":memory.revision,
                                   "namespace":encoder.capability.model,"changes":changes})
    with memory.transaction() as conn:
        memory.register_artifact(conn,output,person_id)
    return preview


def apply_reembed(memory,path):
    data,preview_digest = read_preview(path)
    from .adapters.voice import NAMESPACE
    if data.get("kind")!="reembedding" or data.get("namespace")!=NAMESPACE:
        raise ValueError("ReembeddingNamespaceMismatch")
    with memory.transaction() as conn:
        if memory.revision!=data["revision"] or memory.person(data["person_id"]) is None:
            raise ValueError("StaleReembeddingPreview")
        for change in data["changes"]:
            row = conn.execute("SELECT digest,person_id FROM recordings WHERE id=?",(change["recording_id"],)).fetchone()
            if row is None or row[0]!=change["source_pcm_digest"] or row[1]!=data["person_id"]:
                raise ValueError("ReembeddingSourceChanged")
            vector = unit(change["vector"])
            if vector is None or len(vector)!=192:
                raise ValueError("InvalidPreviewVector")
            conn.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?,?)",(change["recording_id"],NAMESPACE,json.dumps(vector)))
        memory.advance(conn)
    return {"applied":len(data["changes"]),"preview_digest":preview_digest,"namespace":NAMESPACE,
            "status":"review still required for suspect profiles"}


def review(memory,person_id,status):
    if status not in {"suspect","supported"} or memory.person(person_id) is None:
        raise ValueError("InvalidPersonReview")
    from .adapters.voice import NAMESPACE
    with memory.transaction() as conn:
        count = conn.execute("SELECT count(*) FROM embeddings e JOIN recordings r ON r.id=e.recording_id WHERE r.person_id=? AND e.namespace=?",(person_id,NAMESPACE)).fetchone()[0]
        if status=="supported" and count<3:
            raise ValueError("ReviewRequiresThreeCurrentNamespaceRecordings")
        conn.execute("UPDATE people SET status=? WHERE id=?",(status,person_id))
        memory.advance(conn)


def delete_recording(memory,recording_id):
    with memory.transaction() as conn:
        row = conn.execute("SELECT person_id FROM recordings WHERE id=?",(recording_id,)).fetchone()
        if row is None:
            raise ValueError("UnknownRecordingId")
        memory.remove_artifacts(conn,row[0],recording_id)
        conn.execute("DELETE FROM voice_candidates WHERE person_id=?",(row[0],))
        conn.execute("DELETE FROM recordings WHERE id=?",(recording_id,))
        conn.execute("UPDATE people SET status='suspect' WHERE id=?",(row[0],))
        conn.execute("UPDATE operations SET result=?",(json.dumps({"outcome":"redacted","reason":"voice_data_removed"}),))
        memory.advance(conn)
    return {"removed":recording_id,"person_id":row[0],"profile_status":"suspect"}


def reset_voice(memory,person_id):
    if memory.person(person_id) is None:
        raise ValueError("UnknownPersonId")
    with memory.transaction() as conn:
        memory.remove_artifacts(conn,person_id)
        conn.execute("DELETE FROM voice_candidates WHERE person_id=?",(person_id,))
        conn.execute("DELETE FROM recordings WHERE person_id=?",(person_id,))
        conn.execute("DELETE FROM legacy_embeddings WHERE person_id=?",(person_id,))
        conn.execute("UPDATE people SET status='provisional' WHERE id=?",(person_id,))
        conn.execute("UPDATE operations SET result=?",(json.dumps({"outcome":"redacted","reason":"voice_data_removed"}),))
        memory.advance(conn)
    return {"person_id":person_id,"voice_data":"removed","profile_status":"provisional","claims":"preserved"}
