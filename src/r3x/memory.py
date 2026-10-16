"""Small attributed SQLite store. Original audio and embeddings commit together."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import unicodedata
from uuid import uuid4


def label(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


CLAIM_INSERT = ("INSERT INTO claims(id,subject_id,predicate,value,said_by,source_track,source_turn,kind,status,"
                "supersedes,revision,said_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES('schema','1');
INSERT OR IGNORE INTO meta VALUES('revision','0');
INSERT OR IGNORE INTO meta VALUES('face_schema','1');
CREATE TABLE IF NOT EXISTS people(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases TEXT NOT NULL, status TEXT NOT NULL,
 source_turn TEXT, import_ref TEXT);
CREATE TABLE IF NOT EXISTS turns(
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL, capture_id TEXT NOT NULL,
 text TEXT NOT NULL, evidence TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS claims(
 id TEXT PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 predicate TEXT NOT NULL,value TEXT NOT NULL,said_by TEXT REFERENCES people(id) ON DELETE CASCADE,
 source_track TEXT,source_turn TEXT NOT NULL REFERENCES turns(id),kind TEXT NOT NULL,
 status TEXT NOT NULL,supersedes TEXT,revision INTEGER NOT NULL,said_at REAL);
CREATE INDEX IF NOT EXISTS claims_subject ON claims(subject_id,status);
CREATE TABLE IF NOT EXISTS memory_followups(
 claim_id TEXT PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,
 kind TEXT NOT NULL,on_date TEXT,audience TEXT NOT NULL,said_at REAL,due_at REAL);
CREATE TABLE IF NOT EXISTS interests(
 claim_id TEXT PRIMARY KEY REFERENCES claims(id) ON DELETE CASCADE,topic TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS memory_followup_delivery(
 claim_id TEXT NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
 occasion TEXT NOT NULL,delivered_on TEXT NOT NULL, PRIMARY KEY(claim_id,occasion));
CREATE TABLE IF NOT EXISTS visual_observations(
 id TEXT PRIMARY KEY,session_id TEXT NOT NULL,audience TEXT NOT NULL,source TEXT NOT NULL,
 description TEXT NOT NULL,uncertainty TEXT NOT NULL,model TEXT NOT NULL,created_at REAL NOT NULL,
 status TEXT NOT NULL,spoken TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observation_delivery(
 cue_id TEXT PRIMARY KEY,kind TEXT NOT NULL,delivered_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS recordings(
 id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 digest TEXT NOT NULL,pcm BLOB NOT NULL,sample_rate INTEGER NOT NULL,metadata TEXT NOT NULL,
 UNIQUE(person_id,digest));
CREATE TABLE IF NOT EXISTS embeddings(
 recording_id TEXT NOT NULL REFERENCES recordings(id) ON DELETE CASCADE,
 namespace TEXT NOT NULL,vector TEXT NOT NULL, PRIMARY KEY(recording_id,namespace));
CREATE TABLE IF NOT EXISTS voice_candidates(
 track_id TEXT PRIMARY KEY, person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
 namespace TEXT NOT NULL, updated_at REAL NOT NULL, evidence TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS face_learning_blocks(
 person_id TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS face_references(
 id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 namespace TEXT NOT NULL,digest TEXT NOT NULL,aligned BLOB NOT NULL,original BLOB NOT NULL,
 vector TEXT NOT NULL,metadata TEXT NOT NULL,source_turn TEXT NOT NULL REFERENCES turns(id),
 UNIQUE(namespace,digest));
CREATE TABLE IF NOT EXISTS operations(
 request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,result TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS originals(
 recording_id TEXT PRIMARY KEY REFERENCES recordings(id) ON DELETE CASCADE,
 source_digest TEXT NOT NULL,wav BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS legacy_embeddings(
 id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 namespace TEXT NOT NULL,vector TEXT NOT NULL,provenance TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS imports(digest TEXT PRIMARY KEY,mapping TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(
 path TEXT NOT NULL,digest TEXT NOT NULL,person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
 recording_id TEXT REFERENCES recordings(id) ON DELETE CASCADE,capture_id TEXT,
 UNIQUE(path,person_id));
"""


class Memory:
    def __init__(self, path: Path | str):
        self.path = path
        self.connection = None
        self.lock = threading.RLock()

    def start(self):
        if self.connection is not None:
            raise RuntimeError("MemoryAlreadyStarted")
        if str(self.path) != ":memory:":
            path = Path(self.path)
            if path.resolve() != path or path.suffix != ".sqlite3":
                raise ValueError("MemoryRequiresIndependentSQLitePath")
            if path.exists() and path.stat().st_nlink!=1:
                raise ValueError("MemoryMustNotShareAnInode")
        self.connection = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            try:
                self.validate_schema()
            except Exception:
                self.close()
                raise
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.execute("PRAGMA secure_delete=ON")
        self.connection.executescript(SCHEMA)
        # October 14: a follow-up keeps wall-clock times for when it was said and when its time
        # comes. Turn times are the session's monotonic clock and mean nothing after a restart.
        columns = {r[1] for r in self.connection.execute('PRAGMA table_info(memory_followups)')}
        for column in ('said_at', 'due_at'):
            if column not in columns:
                self.connection.execute(f'ALTER TABLE memory_followups ADD COLUMN {column} REAL')
        # October 15: every fact keeps when it was told, so "in five minutes, I'm going to the
        # store" is not offered as current a day later. Older facts fall back to their exchange.
        if 'said_at' not in {r[1] for r in self.connection.execute('PRAGMA table_info(claims)')}:
            self.connection.execute('ALTER TABLE claims ADD COLUMN said_at REAL')
        from .conversation_memory import SCHEMA as conversation_schema
        self.connection.executescript(conversation_schema)
        from .agency import SCHEMA as agency_schema
        self.connection.executescript(agency_schema)
        from .places import SCHEMA as places_schema
        self.connection.executescript(places_schema)
        from .jokes import SCHEMA as jokes_schema
        self.connection.executescript(jokes_schema)
        self.validate_schema()

    def validate_schema(self):
        tables = {row[0] for row in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"meta","people","turns","claims","recordings","embeddings","operations"}.issubset(tables):
            raise ValueError("NotACoreDatabase")
        row = self.connection.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if not row or row[0]!="1":
            raise ValueError("UnsupportedMemorySchema")
        face = self.connection.execute("SELECT value FROM meta WHERE key='face_schema'").fetchone()
        if face and face[0]!="1":
            raise ValueError("UnsupportedFaceSchema")

    def close(self):
        with self.lock:
            if self.connection:
                self.connection.close()
                self.connection = None

    @contextmanager
    def transaction(self):
        with self.lock:
            if self.connection is None:
                raise RuntimeError("MemoryNotStarted")
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
                self.connection.commit()
            except BaseException:
                self.connection.rollback()
                raise

    def rows(self, sql, parameters=()):
        with self.lock:
            return [dict(r) for r in self.connection.execute(sql, parameters).fetchall()]

    @property
    def revision(self):
        return int(self.rows("SELECT value FROM meta WHERE key='revision'")[0]["value"])

    def advance(self, conn):
        conn.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
        return int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    def source(self, turn_id, capture, transcript, attribution, observation,association_person_id=None):
        from dataclasses import asdict
        # Original audio is committed only for accepted enrollment samples.
        evidence = {"attribution":asdict(attribution), "trusted":transcript.trusted,
                    "avg_logprob":transcript.avg_logprob, "namespace":observation.namespace,
                    "voiced_seconds":observation.voiced_seconds, "mixed":observation.mixed,
                    "direction":observation.direction,
                    "echo_risk":capture.echo_risk, "device":capture.device,
                    "started_at":capture.started_at,"ended_at":capture.ended_at,
                    "parent_capture_id":capture.parent_capture_id,"source_offset":capture.source_offset,
                    "association_person_id":association_person_id}
        with self.transaction() as conn:
            conn.execute("INSERT INTO turns VALUES(?,?,?,?,?,?)", (turn_id,capture.session_id,capture.capture_id,
                         transcript.text,json.dumps(evidence),capture.ended_at))
            if str(self.path)!=":memory:":
                artifact = Path(self.path).parent/"recordings"/capture.session_id/((capture.parent_capture_id or capture.capture_id)+".wav")
                if artifact.is_file():
                    self.register_artifact(conn,artifact,attribution.person_id or association_person_id,capture_id=capture.capture_id)

    def people(self,include_retired=False):
        sql = "SELECT * FROM people" + ("" if include_retired else " WHERE status<>'retracted'") + " ORDER BY name LIMIT 100"
        return [{**row,"aliases":json.loads(row["aliases"])} for row in self.rows(sql)]

    def person(self, person_id):
        rows = self.rows("SELECT * FROM people WHERE id=?",(person_id,))
        return {**rows[0],"aliases":json.loads(rows[0]["aliases"])} if rows else None

    def resolve(self, reference):
        target = label(reference)
        return [p for p in self.people() if target in {label(p["name"]), *(label(a) for a in p["aliases"])}]

    def add_person(self, conn, name, aliases, source_turn, status="provisional", import_ref=None):
        if (not isinstance(name,str) or not 0<len(name.strip())<=120 or any(ord(c)<32 for c in name) or
            not isinstance(aliases,list) or len(aliases)>8 or
            any(not isinstance(a,str) or not 0<len(a.strip())<=120 for a in aliases) or
            status not in {"provisional","supported","suspect","retracted"}):
            raise ValueError("InvalidPersonLabelOrStatus")
        if conn.execute("SELECT count(*) FROM people WHERE status<>'retracted'").fetchone()[0]>=100:
            raise ValueError("PersonCapacityReached")
        person_id = uuid4().hex
        conn.execute("INSERT INTO people VALUES(?,?,?,?,?,?)",
                     (person_id,name,json.dumps(aliases),status,source_turn,import_ref))
        return person_id

    def claims(self, person_id=None, include_candidates=False):
        states = ("accepted", "candidate") if include_candidates else ("accepted",)
        sql = ("SELECT c.*,t.text AS source_text,COALESCE(c.said_at,x.created_at) AS told_at,"
               "f.kind AS followup_kind,f.due_at FROM claims c JOIN turns t ON t.id=c.source_turn "
               "LEFT JOIN conversation_exchanges x ON x.id=c.source_turn LEFT JOIN memory_followups f ON f.claim_id=c.id "
               "WHERE c.status IN (" + ",".join("?" for _ in states) + ")")
        params = list(states)
        if person_id:
            sql += " AND c.subject_id=?"
            params.append(person_id)
        return self.rows(sql+" ORDER BY c.revision DESC LIMIT 32", params)

    def profiles(self, namespace):
        profiles = {}
        for row in self.rows("SELECT r.person_id,e.vector FROM recordings r JOIN embeddings e ON e.recording_id=r.id JOIN people p ON p.id=r.person_id WHERE namespace=? AND p.status='supported'", (namespace,)):
            profiles.setdefault(row["person_id"], []).append(tuple(json.loads(row["vector"])))
        return profiles

    def face_profiles(self, namespace):
        profiles = {}
        for row in self.rows("SELECT f.person_id,f.vector FROM face_references f JOIN people p ON p.id=f.person_id WHERE namespace=? AND p.status NOT IN ('retracted','suspect')", (namespace,)):
            profiles.setdefault(row["person_id"], []).append(tuple(json.loads(row["vector"])))
        return profiles

    def save_faces(self, conn, person_id, samples, source_turn, association):
        from dataclasses import asdict
        count = conn.execute("SELECT count(*) FROM face_references WHERE person_id=?", (person_id,)).fetchone()[0]
        saved = 0
        for sample in samples:
            if count >= 6: break
            if not sample.eligible or sample.vector is None or not sample.original or not sample.aligned:
                raise ValueError("FaceSampleNotEligible")
            digest = hashlib.sha256(sample.aligned).hexdigest()
            previous = conn.execute("SELECT person_id FROM face_references WHERE namespace=? AND digest=?", (sample.namespace,digest)).fetchone()
            if previous:
                if previous[0] != person_id: raise ValueError("FaceCropAlreadyAssigned")
                continue
            metadata = {k:v for k,v in asdict(sample).items() if k not in {"vector","aligned","original"}}
            metadata.update(association=association, original_sha256=hashlib.sha256(sample.original).hexdigest(),
                            acceptance="explicit_semantic_target_and_compatible_quality_views", revision=self.revision+1)
            conn.execute("INSERT INTO face_references VALUES(?,?,?,?,?,?,?,?,?)", (uuid4().hex,person_id,sample.namespace,
                digest,sample.aligned,sample.original,json.dumps(sample.vector),json.dumps(metadata),source_turn))
            saved += 1
            count += 1
        return saved

    def save_voice(self, conn, person_id, samples, namespace, acceptance='compatible_anchor_and_cluster'):
        count = conn.execute("SELECT count(*) FROM recordings WHERE person_id=?",(person_id,)).fetchone()[0]
        for sample in samples:
            capture, transcript, observation, turn_id = sample
            digest = hashlib.sha256(capture.pcm).hexdigest()
            if conn.execute("SELECT 1 FROM recordings WHERE person_id=? AND digest=?",(person_id,digest)).fetchone():
                continue
            if count >= 20:
                break  # Bounded growth; never silently delete accepted original recordings.
            recording_id = uuid4().hex
            metadata = {"source_turn":turn_id,"session_id":capture.session_id,"capture_id":capture.capture_id,
                        "direction":observation.direction,
                        "transcript":transcript.text,"voiced_seconds":observation.voiced_seconds,
                        "started_at":capture.started_at,"ended_at":capture.ended_at,"device":capture.device,
                        "avg_logprob":transcript.avg_logprob,"namespace":namespace,
                        "acceptance":acceptance}
            conn.execute("INSERT INTO recordings VALUES(?,?,?,?,?,?)",(recording_id,person_id,digest,
                         capture.pcm,capture.sample_rate,json.dumps(metadata)))
            conn.execute("INSERT INTO embeddings VALUES(?,?,?)",(recording_id,namespace,json.dumps(observation.embedding)))
            conn.execute("UPDATE artifacts SET person_id=?,recording_id=? WHERE capture_id=?",
                         (person_id,recording_id,capture.capture_id))
            count += 1
        conn.execute("UPDATE people SET status='supported' WHERE id=?",(person_id,))

    def forget(self, conn, person_id):
        conn.execute('DELETE FROM agency_plan')  # Derived narrative must not retain forgotten personal data.
        # Claims from or about this person cannot survive as unattributed facts.
        affected = {row[0] for row in conn.execute("SELECT source_turn FROM claims WHERE subject_id=? OR said_by=?",(person_id,person_id))}
        affected.update(row[0] for row in conn.execute(
            'SELECT source_turn FROM conversation_exchanges WHERE person_id=? AND source_turn IS NOT NULL', (person_id,)))
        for row in conn.execute("SELECT evidence FROM voice_candidates WHERE person_id=?", (person_id,)):
            affected.update(sample[3] for sample in json.loads(row[0])["samples"])
        for row in conn.execute("SELECT metadata FROM recordings WHERE person_id=?",(person_id,)):
            affected.add(json.loads(row[0]).get("source_turn"))
        for row in conn.execute("SELECT id,evidence FROM turns"):
            evidence = json.loads(row[1])
            if evidence.get("attribution",{}).get("person_id") == person_id or evidence.get("association_person_id")==person_id:
                affected.add(row[0])
        affected.update(row[0] for row in conn.execute("SELECT source_turn FROM face_references WHERE person_id=?", (person_id,)))
        person = conn.execute("SELECT source_turn FROM people WHERE id=?",(person_id,)).fetchone()
        if person:
            affected.add(person[0])
        for source in affected:
            conn.execute('DELETE FROM conversation_exchanges WHERE source_turn=?',(source,))
        capture_ids=[]
        for turn_id in affected:
            capture = conn.execute("SELECT capture_id FROM turns WHERE id=?",(turn_id,)).fetchone()
            if capture:
                capture_ids.append(capture[0])
        self.remove_artifacts(conn,person_id,capture_ids=capture_ids)
        conn.execute("DELETE FROM people WHERE id=?",(person_id,))
        for row in conn.execute("SELECT digest,mapping FROM imports").fetchall():
            mapping = {k:v for k,v in json.loads(row[1]).items() if v!=person_id}
            conn.execute("UPDATE imports SET mapping=? WHERE digest=?",(json.dumps(mapping),row[0]))
        for turn_id in affected:
            conn.execute("UPDATE turns SET text='[forgotten]',evidence='{}' WHERE id=?",(turn_id,))
        # Cached result payloads may contain the deleted person's label/claims.
        conn.execute("UPDATE operations SET result=?",(json.dumps({"outcome":"redacted","reason":"person_forgotten","data":{}}),))

    def register_artifact(self,conn,path,person_id,recording_id=None,capture_id=None):
        path = path.absolute()
        if str(self.path)==":memory:" or path.resolve()!=path or not path.is_relative_to(Path(self.path).parent):
            raise ValueError("ArtifactOutsideOwnedStore")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        conn.execute("DELETE FROM artifacts WHERE path=? AND person_id IS ?",(str(path),person_id))
        conn.execute("INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?,?)",(str(path),digest,person_id,recording_id,capture_id))

    def remove_artifacts(self,conn,person_id,recording_id=None,*,capture_ids=()):
        sql = "SELECT path,digest FROM artifacts WHERE person_id=?"
        params = [person_id]
        if recording_id:
            sql += " AND (recording_id=? OR recording_id IS NULL)"
            params.append(recording_id)
        if capture_ids:
            sql += ' OR capture_id IN ('+','.join('?' for _ in capture_ids)+')'
            params.extend(capture_ids)
        rows = conn.execute(sql,params).fetchall()
        # Verify all paths first. Missing files mean an earlier interrupted deletion
        # already removed that copy; changed/unowned files are never deleted blindly.
        paths = []
        for row in rows:
            path = Path(row[0])
            if str(self.path)==":memory:" or path.resolve()!=path or not path.is_relative_to(Path(self.path).parent):
                raise ValueError("ArtifactCleanupPathChanged")
            if path.exists():
                if path.stat().st_nlink!=1 or hashlib.sha256(path.read_bytes()).hexdigest()!=row[1]:
                    raise ValueError("ArtifactCleanupDigestChanged")
                paths.append(path)
        for path in dict.fromkeys(paths):
            path.unlink()
        for row in rows:
            conn.execute("DELETE FROM artifacts WHERE path=?",(row[0],))
