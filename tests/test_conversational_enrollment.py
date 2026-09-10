"""Enrollment contracts use synthetic voices and independent temporary stores."""
from dataclasses import replace
from uuid import uuid4

from r3x.contracts import Capture, Transcript, ToolCall
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.session import Session
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tests.test_memory_core import MemoryFixture, A, B, NS, observation


class ConversationalEnrollmentTests(MemoryFixture):
    def name_current(self, name, subject="self"):
        return self.call("introduce_person", {"name":name, "aliases":[], "subject":subject,
                                              "person_id":None, "face_track_id":None})

    def restart(self):
        self.store.close()
        self.store = Memory(self.path)
        self.store.start()
        self.state = CoreState(self.store, SpeakerTracker(NS), self.state.trace)

    def test_alternating_groups_enroll_from_pre_introduction_audio(self):
        originals = {A:[], B:[]}
        for _ in range(3):
            for vector in (A, B):
                self.begin("We are talking about our favorite music.", vector)
                originals[vector].append(self.state.current[1].pcm)
        self.assertEqual(self.store.profiles(NS), {})
        self.assertEqual(len(self.state.evidence), 2)
        self.begin("I'm Bret.", A, seconds=.7)
        bret = self.name_current("Bret").data["person_id"]
        self.begin("I'm Jeff.", B, seconds=1.1)
        jeff = self.name_current("Jeff").data["person_id"]
        for vector, person in ((A,bret),(B,jeff)):
            self.assertEqual({r["pcm"] for r in self.store.rows("SELECT pcm FROM recordings WHERE person_id=?", (person,))}, set(originals[vector]))
            self.assertEqual(len(self.store.profiles(NS)[person]), 3)
        self.assertEqual(self.store.rows("SELECT * FROM voice_candidates"), [])

    def test_named_progress_survives_restart_and_other_speaker(self):
        person = self.introduce()
        self.begin("Here is more evidence from my voice.")
        self.restart()
        self.begin("A different speaker is saying this sentence.", B)
        self.assertEqual(self.store.profiles(NS), {})
        self.begin("I have returned to finish this discussion.", A)
        self.assertEqual(len(self.store.profiles(NS)[person]), 3)

    def test_anonymous_progress_and_short_anchor_survive_restart(self):
        self.begin("Some useful speech before giving my name.")
        self.begin("Another sentence before giving my name.")
        self.restart()
        self.begin("My name is Bret.")
        person = self.name_current("Bret").data["person_id"]
        self.assertIn(person, self.store.profiles(NS))
        self.begin("I'm Jeff.", B, seconds=1.1)
        jeff = self.name_current("Jeff").data["person_id"]
        self.restart()
        for _ in range(3):
            self.begin("Here is a qualifying sentence from Jeff.", B)
        self.assertIn(jeff, self.store.profiles(NS))

    def test_other_person_reference_never_names_current_group(self):
        for _ in range(3):
            self.begin("We are talking about some favorite music.")
        self.begin("That person over there is Jeff.")
        self.name_current("Jeff", "other")
        self.assertEqual(self.store.profiles(NS), {})
        self.assertTrue(all(e.person_id is None for e in self.state.evidence.values()))

    def test_suppressed_and_yielded_replies_still_count(self):
        person = self.introduce()
        class Silent:
            def reply(_, snapshot, cancel):
                yield ToolCall(uuid4().hex, "remain_silent", {"reason":"no_response_needed"})
        session = Session("test", Silent(), TextOutput(self.state.trace), self.state.trace, self.state)
        for i in range(2):
            at = 200. + 10*i
            capture = Capture(uuid4().hex,"test",uuid4().bytes*6000,at,at+3,at+3)
            if i:
                session.note_speech(at+4)
            result = session.process_turn(capture, Transcript("Here is a qualifying conversational sentence.",0,True),0,observation())
            self.assertEqual(result.status, "interrupted" if i else "suppressed")
        self.assertIn(person, self.store.profiles(NS))

    def test_cancel_speech_keeps_progress_but_identity_correction_removes_it(self):
        person = self.introduce()
        session = Session("test",None,TextOutput(self.state.trace),self.state.trace,self.state)
        session.cancel()
        session.close()
        self.restart()
        self.begin("I would like to correct that association.")
        self.assertIn(person, [e.person_id for e in self.state.enrollments.values()])
        self.call("cancel_association", {"person_id":person,"track_id":None})
        self.assertEqual(self.store.rows("SELECT * FROM voice_candidates WHERE person_id=?",(person,)), [])
        self.restart()
        self.begin("This is more speech after the correction.")
        self.assertEqual(self.state.enrollments, {})

    def test_overlap_and_disagreeing_windows_do_not_contaminate_group(self):
        self.begin("A clean sentence for the first speaker.")
        capture = self.state.current[1]
        track = self.state.current[4].track_id
        self.begin("A clean sentence for the second speaker.",B)
        mixed_windows = replace(observation(A), windows=((0.,1.,A),(1.,2.,B)))
        result = self.state.begin(uuid4().hex,replace(capture,capture_id=uuid4().hex),Transcript("There are two voices inside this clip.",0,True),mixed_windows)
        self.assertFalse(result.learning_eligible)
        self.assertEqual(len(self.state.evidence[track].samples),1)
        self.state.begin(uuid4().hex,replace(capture,overlap=True),Transcript("Explicit overlap is also excluded from learning.",0,True),observation(A))
        self.assertEqual(len(self.state.evidence[track].samples),1)

    def test_expired_and_wrong_namespace_candidates_cannot_enroll(self):
        person = self.introduce()
        self.store.connection.execute("UPDATE voice_candidates SET updated_at=0")
        self.restart()
        self.begin("Here is some speech after the expiration.")
        self.assertEqual(self.state.enrollments,{})
        self.assertNotIn(person,self.store.profiles(NS))
        self.store.connection.execute("UPDATE voice_candidates SET namespace='other:model'")
        self.restart()
        self.begin("Here is another sentence in the active model.")
        self.assertEqual(len(next(iter(self.state.evidence.values())).samples),1)

    def test_forgetting_pending_person_removes_original_source_and_candidate(self):
        person = self.introduce()
        source = self.state.current[0]
        with self.store.transaction() as conn:
            self.store.forget(conn,person)
        self.restart()
        self.begin("The previous pending person was forgotten.",B)
        self.assertEqual(self.state.enrollments,{})
        self.assertEqual(self.store.rows("SELECT text FROM turns WHERE id=?",(source,))[0]["text"],"[forgotten]")

    def test_display_name_correction_keeps_same_voice_evidence(self):
        person = self.introduce("Brett")
        self.begin("Please spell my name as Bret.")
        result = self.call("rename_person", {"person_id":person,"name":"Bret","aliases":[]})
        self.assertEqual(result.outcome,"ok")
        self.restart()
        self.begin("Here is another sentence from the same person.")
        self.assertEqual(self.store.person(person)["name"],"Bret")
        self.assertIn(person,self.store.profiles(NS))

    def test_existing_database_gets_additive_candidate_table(self):
        self.store.connection.execute("DROP TABLE voice_candidates")
        self.restart()
        self.begin("A first sentence after the database upgrade.")
        self.assertEqual(len(self.store.rows("SELECT * FROM voice_candidates")),1)

    def test_frozen_replay_neither_loads_nor_changes_pending_evidence(self):
        self.introduce()
        before = self.store.rows("SELECT * FROM voice_candidates")
        self.state = CoreState(self.store,SpeakerTracker(NS),self.state.trace,frozen=True)
        self.begin("Another sentence in a frozen evaluation run.")
        self.assertEqual(self.state.evidence,{})
        self.assertEqual(self.store.rows("SELECT * FROM voice_candidates"),before)
