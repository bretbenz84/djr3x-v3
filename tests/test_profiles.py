import base64
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
import wave

from r3x import profiles
from r3x.adapters.voice import NAMESPACE
from tests.test_memory_core import MemoryFixture


class ProfileTests(MemoryFixture):
    def test_explicit_voice_reset_removes_all_namespaces_but_preserves_claims(self):
        person=self.enroll()
        source=self.begin("I collect clocks.")
        self.call("record_claim",{"subject_id":person,"predicate":"collects","value":"clocks","kind":"self_report","source_turn_id":source})
        profiles.reset_voice(self.store,person)
        self.assertEqual(self.store.rows("SELECT * FROM recordings"),[])
        self.assertEqual(self.store.rows("SELECT * FROM embeddings"),[])
        self.assertEqual(self.store.person(person)["status"],"provisional")
        self.assertEqual(self.store.claims(person)[0]["value"],"clocks")

    def test_foreign_or_future_database_is_not_modified(self):
        from r3x.memory import Memory
        path=Path(self.temp.name).resolve()/"foreign.sqlite3"
        with sqlite3.connect(path) as db:db.execute("CREATE TABLE unrelated(data TEXT)")
        before=path.read_bytes()
        memory=Memory(path)
        with self.assertRaisesRegex(ValueError,"NotACoreDatabase"):memory.start()
        self.assertEqual(path.read_bytes(),before)
        self.store.connection.execute("UPDATE meta SET value='2' WHERE key='schema'")
        self.store.close()
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,"UnsupportedMemorySchema"):self.store.start()
        self.assertEqual(self.path.read_bytes(),before)

    def fake_source(self):
        path=Path(self.temp.name).resolve()/"source.sqlite3"
        source=sqlite3.connect(path)
        source.execute("PRAGMA journal_mode=WAL")
        source.executescript("""CREATE TABLE people(id INTEGER,name TEXT,nickname TEXT);
            CREATE TABLE biometrics(id INTEGER,person_id INTEGER,type TEXT,encoding BLOB);
            CREATE TABLE voice_recordings(id INTEGER,person_id INTEGER,digest TEXT,wav BLOB,metadata TEXT);
            INSERT INTO people VALUES(7,'Synthetic Person','Synth');""")
        output=io.BytesIO()
        with wave.open(output,"wb") as handle:
            handle.setparams((1,2,16000,0,"NONE","none"))
            handle.writeframes(b"\1\0"*48000)
        wav=output.getvalue()
        source.execute("INSERT INTO voice_recordings VALUES(1,7,?,?,?)",(hashlib.sha256(wav).hexdigest(),wav,'{"transcript":"Synthetic only"}'))
        source.execute("INSERT INTO biometrics VALUES(1,7,'voice_campplus',?)",(struct.pack("<192f",1.,*([0.]*191)),))
        source.commit()
        return path,source,wav

    def test_consistent_wal_import_is_explicit_independent_idempotent_and_suspect(self):
        path,source,wav=self.fake_source()
        preview=Path(self.temp.name).resolve()/"preview.json"
        try:
            data=profiles.preview_v2(path,[7],preview)
            self.assertEqual(data["people"][0]["name"],"Synthetic Person")
            mapping=profiles.apply_import(self.store,preview)
            self.assertEqual(profiles.apply_import(self.store,preview),mapping)
            person=mapping["7"]
            self.assertNotEqual(person,"7")
            self.assertEqual(self.store.person(person)["status"],"suspect")
            self.assertEqual(self.store.profiles(NAMESPACE),{})
            self.assertEqual(self.store.claims(),[])
            self.assertEqual(self.store.rows("SELECT wav FROM originals")[0]["wav"],wav)
            source.execute("UPDATE people SET name='Changed' WHERE id=7")
            source.commit()
            self.assertEqual(self.store.person(person)["name"],"Synthetic Person")
            self.assertEqual(len(self.store.rows("SELECT * FROM legacy_embeddings")),1)
            saved_preview=preview.read_text()
            with self.store.transaction() as conn:self.store.forget(conn,person)
            self.assertEqual(self.store.rows("SELECT * FROM originals"),[])
            self.assertEqual(self.store.rows("SELECT * FROM legacy_embeddings"),[])
            self.assertFalse(preview.exists())
            preview.write_text(saved_preview)  # Even an externally restored old preview cannot replay the import.
            self.assertEqual(profiles.apply_import(self.store,preview),{})
        finally:
            source.close()

    def test_changed_preview_or_late_reembed_cannot_apply(self):
        person=self.enroll()
        recording=self.store.rows("SELECT * FROM recordings")[0]
        preview=Path(self.temp.name).resolve()/"reembed.json"
        data={"version":1,"kind":"reembedding","person_id":person,"revision":self.store.revision,
              "namespace":NAMESPACE,"changes":[{"recording_id":recording["id"],"source_pcm_digest":recording["digest"],
                                                  "vector":[1.]+[0.]*191}]}
        profiles.write_preview(preview,data)
        result=profiles.apply_reembed(self.store,preview)
        self.assertEqual(result["applied"],1)
        self.assertTrue(self.store.profiles(NAMESPACE))
        with self.assertRaisesRegex(ValueError,"StaleReembedding"):profiles.apply_reembed(self.store,preview)
        value=json.loads(preview.read_text())
        value["changes"][0]["vector"][0]=.9
        preview.write_text(json.dumps(value))
        with self.assertRaisesRegex(ValueError,"PreviewDigestMismatch"):profiles.read_preview(preview)

    def test_bad_recording_removal_disables_profile_and_cascades_vectors(self):
        person=self.enroll()
        recording=self.store.rows("SELECT id FROM recordings")[0]["id"]
        profiles.delete_recording(self.store,recording)
        self.assertEqual(self.store.person(person)["status"],"suspect")
        self.assertEqual(self.store.profiles(self.state.tracker.namespace),{})
        self.assertEqual(self.store.rows("SELECT * FROM embeddings WHERE recording_id=?",(recording,)),[])

    def test_forget_removes_registered_audio_and_rejects_changed_artifacts(self):
        person=self.enroll()
        rid=self.store.rows("SELECT id FROM recordings")[0]["id"]
        artifact=Path(self.temp.name).resolve()/"export.wav"
        artifact.write_bytes(b"synthetic managed audio")
        with self.store.transaction() as conn:self.store.register_artifact(conn,artifact,person,rid)
        original=artifact.read_bytes()
        artifact.write_bytes(b"changed file")
        with self.assertRaisesRegex(ValueError,"ArtifactCleanupDigestChanged"):
            with self.store.transaction() as conn:self.store.forget(conn,person)
        self.assertIsNotNone(self.store.person(person))
        artifact.write_bytes(original)
        with self.store.transaction() as conn:self.store.forget(conn,person)
        self.assertFalse(artifact.exists())
        self.assertEqual(self.store.rows("SELECT * FROM artifacts"),[])
