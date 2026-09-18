"""Observable semantic proposal/result/delivery contracts through the real Session."""
from dataclasses import replace
import json
import threading
import unittest
from uuid import uuid4

from r3x.contracts import ToolCall, Transcript, Cancellation
from r3x.session import Session
from r3x.output import OutputOwner, TextOutput
from r3x.scenario import SyntheticTTS, SyntheticSink
from tests.test_memory_core import MemoryFixture, observation, B, C


class Brain:
    def __init__(self,make_call):
        self.make_call,self.requests = make_call,[]

    def reply(self,snapshot,cancel):
        self.requests.append(snapshot)
        if snapshot.continuation:
            result = json.loads(snapshot.continuation[-1]["output"])
            yield "That is saved." if result["outcome"]=="ok" else "I could not save that."
        else:
            yield self.make_call(snapshot)


class SessionTests(MemoryFixture):
    def test_current_view_updates_reply_without_changing_speech_evidence(self):
        person = self.enroll()
        self.begin('Who do you see?', C)
        old = {'track_id':'face-test','identity':None,'face_epoch':0,'at':self.at,
               'box':[.3,.2,.1,.2],'position':'center','evidence':'face_unresolved'}
        brain = Brain(lambda s:'I can see Bret.' if s.context['current_view']['faces'][0]['identity']==person else 'Unknown.')
        session = Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        reads = []
        def latest(now):
            reads.append(now)
            return ({**old,'identity':person,'evidence':'face_match','at':now-.1,'_samples':('private',)},)
        receipt = session.process_turn(self.state.current[1],Transcript('Who do you see?',0,True),0,
            observation(C),faces=(old,),latest_faces=latest)
        self.assertEqual(receipt.completed_text,['I can see Bret.'])
        self.assertEqual(len(brain.requests),1)
        self.assertEqual(len(reads),1)
        context = brain.requests[0].context
        self.assertIsNone(context['faces'][0]['identity'])
        self.assertIsNone(context['speaker']['person_id'])
        self.assertIsNone(self.state.faces[0]['identity'])
        self.assertNotIn('_samples',context['current_view']['faces'][0])
        self.assertEqual(self.state.face_learning,{})
        session.close()

    def test_current_view_rejects_expired_future_and_invalidated_faces(self):
        person = self.enroll()
        for kind in ('expired','future','invalidated','empty'):
            with self.subTest(kind=kind):
                self.begin('Who is here?')
                self.state.min_face_epoch = 1
                brain = Brain(lambda s:'No fresh face evidence.' if not s.context['current_view']['faces'] else 'Visible.')
                session = Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
                def latest(now):
                    if kind=='empty': return ()
                    return ({'track_id':'old','identity':person,'face_epoch':0 if kind=='invalidated' else 1,
                             'at':now+(.1 if kind=='future' else -2 if kind=='expired' else -.1)},)
                receipt = session.process_turn(self.state.current[1],Transcript('Who is here?',0,True),0,
                    observation(),latest_faces=latest)
                self.assertEqual(receipt.completed_text,['No fresh face evidence.'])
                session.close()

    def test_repeated_turns_keep_context_tracks_and_original_archive_bounded(self):
        self.enroll()
        from r3x.scenario import ScriptedBrain
        brain=ScriptedBrain()
        brain.text="I see."
        session=Session("session",brain,TextOutput(self.state.trace),self.state.trace,self.state)
        for i in range(150):
            self.begin("A synthetic ordinary conversation turn.")
            receipt=session.process_turn(self.state.current[1],Transcript("A synthetic ordinary conversation turn.",0,True),0,observation())
            self.assertEqual(receipt.status,"completed")
        self.assertLessEqual(len(session.history.turns),32)
        self.assertLessEqual(session.history.size(session.history_messages()),24_000)
        self.assertEqual(len(self.state.tracker.tracks),1)
        self.assertLessEqual(len(next(iter(self.state.tracker.tracks.values())).references),5)
        self.assertEqual(len(self.store.rows("SELECT id FROM recordings")),3)
        self.assertEqual(self.state.enrollments,{})
        session.close()

    def test_documented_core_scenario_runs_with_blocked_devices(self):
        from contextlib import redirect_stdout
        from pathlib import Path
        import io
        from r3x.scenario import run
        with redirect_stdout(io.StringIO()):
            self.assertTrue(run(Path(__file__).parent/"fixtures/core_memory.json"))

    def run_session(self,brain,text,vector=None,output=None):
        self.begin(text)
        capture = self.state.current[1]
        session = Session("session",brain,output or TextOutput(self.state.trace),self.state.trace,self.state)
        receipt = session.process_turn(capture,Transcript(text,-.01,True),0,observation() if vector is None else observation(vector))
        return receipt,session

    def test_actual_tool_result_drives_one_bounded_continuation(self):
        person=self.enroll()
        brain=Brain(lambda s:ToolCall("request","record_claim",{"subject_id":person,"predicate":"collects","value":"radios",
                        "kind":"self_report","subject_reference":None,"source_turn_id":s.context["source_turn_id"]}))
        receipt,session=self.run_session(brain,"Please remember I collect radios.")
        self.assertEqual(receipt.status,"completed")
        self.assertEqual(receipt.medium,"text")
        self.assertIsNone(receipt.first_audio_at)
        self.assertEqual(len(brain.requests),2)
        self.assertEqual(brain.requests[0].messages[-1]["content"],"Please remember I collect radios.")
        self.assertFalse(brain.requests[1].tools_enabled)
        self.assertEqual(self.store.claims(person)[0]["said_by"],person)

    def test_failed_write_has_failed_result_and_no_success_ack(self):
        person=self.enroll()
        self.store.connection.execute("CREATE TRIGGER reject_claim BEFORE INSERT ON claims BEGIN SELECT RAISE(ABORT,'fail'); END")
        brain=Brain(lambda s:ToolCall("fail","record_claim",{"subject_id":person,"predicate":"collects","value":"radios",
                        "kind":"self_report","subject_reference":None,"source_turn_id":s.context["source_turn_id"]}))
        receipt,_=self.run_session(brain,"Remember I collect radios.")
        self.assertEqual(self.store.claims(),[])
        self.assertEqual(receipt.completed_text,["I could not save that."])
        self.assertEqual(json.loads(brain.requests[1].continuation[-1]["output"])["outcome"],"failed")

    def test_failed_or_text_only_question_is_never_pending(self):
        brain=Brain(lambda s:ToolCall("q","ask_question",{"text":"What should I call you?","addressee_id":None}))
        self.run_session(brain,"Hello there.")
        self.assertIsNone(self.state.pending)
        class Fails:
            def synthesize(self,text,cancel):
                raise RuntimeError("tts_failure")
                yield
        output=OutputOwner(Fails(),SyntheticSink(),self.state.trace)
        receipt,_=self.run_session(brain,"Hello again.",output=output)
        self.assertEqual(receipt.status,"failed")
        self.assertIsNone(self.state.pending)
        self.run_session(brain,"Hello one more time.",output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace))
        self.assertEqual(self.state.pending["text"],"What should I call you?")

    def test_late_tool_after_cancellation_never_writes_or_speaks(self):
        self.begin("I'm Jordan Avery.")
        capture=self.state.current[1]
        class Late:
            def reply(_,snapshot,cancel):
                session.cancel()
                yield ToolCall("late","introduce_person",{"name":"Jordan Avery","aliases":[],"subject":"self","person_id":None,"face_track_id":None})
        session=Session("session",Late(),TextOutput(self.state.trace),self.state.trace,self.state)
        receipt=session.process_turn(capture,Transcript("I'm Jordan Avery.",0,True),0,observation())
        self.assertEqual(receipt.status,"interrupted")
        self.assertEqual(self.store.people(),[])
        self.assertEqual(receipt.completed_text,[])

    def test_tool_with_spoken_preface_and_second_tool_are_rejected(self):
        class Invalid:
            def reply(_,snapshot,cancel):
                yield "Done."
                yield ToolCall("x","introduce_person",{"name":"Jordan","aliases":[],"subject":"self","person_id":None,"face_track_id":None})
        receipt,_=self.run_session(Invalid(),"I am Jordan.")
        self.assertEqual(receipt.error,"SpeechToolProtocolViolation")
        self.assertEqual(self.store.people(),[])
        self.assertEqual(receipt.completed_text,[])

    def test_enrollment_batch_failure_leaves_no_partial_audio_and_can_retry(self):
        person=self.introduce()
        self.begin("Here is another natural sentence about music.")
        self.state.advance_enrollment()
        self.store.connection.execute("CREATE TRIGGER reject_second BEFORE INSERT ON recordings WHEN (SELECT count(*) FROM recordings)>0 BEGIN SELECT RAISE(ABORT,'full'); END")
        self.begin("Today I repaired a speaker in the studio.")
        with self.assertRaises(Exception): self.state.advance_enrollment()
        self.assertEqual(self.store.rows("SELECT * FROM recordings"),[])
        self.assertEqual(self.store.rows("SELECT * FROM embeddings"),[])
        self.assertEqual(self.store.person(person)["status"],"provisional")
        self.store.connection.execute("DROP TRIGGER reject_second")
        self.state.advance_enrollment()
        self.assertEqual(len(self.store.profiles(self.state.tracker.namespace)[person]),3)

    def test_camera_cannot_override_voice_or_invent_a_face(self):
        person=self.enroll()
        self.begin("That is Jeff.",B)
        result=self.call("introduce_person",{"name":"Jeff","aliases":[],"subject":"other","person_id":None,"face_track_id":"imaginary"})
        self.assertEqual(result.reason,"FaceEvidenceMissing")
        capture=self.state.current[1]
        self.state.begin(uuid4().hex,capture,Transcript("That is Bret.",0,True),observation(B),
            ({"track_id":"face-bret","at":capture.ended_at,"identity":person},))
        self.assertIsNone(self.state.current[4].person_id)
        result=self.call("introduce_person",{"name":"Bret","aliases":[],"subject":"other","person_id":person,"face_track_id":"face-bret"})
        self.assertEqual(result.outcome,"ok")
        self.assertEqual(self.state.context()["faces"][0]["person_hypothesis"],person)
        self.assertEqual(self.state.enrollments,{})

    def test_frozen_evaluation_cannot_train_or_write_claims(self):
        person=self.enroll()
        self.state.frozen=True
        turn=self.begin("I collect radios.")
        result=self.call("record_claim",{"subject_id":person,"predicate":"collects","value":"radios","kind":"self_report","source_turn_id":turn})
        self.assertEqual(result.reason,"FrozenEvaluationDisallowsMutation")
        self.assertEqual(self.store.claims(),[])

class IntroductionAvailabilityTests(MemoryFixture):
    def test_existing_name_opens_association_without_offering_unauthorized_rename(self):
        from r3x.semantics import grounded_tools
        with self.store.transaction() as conn:
            person = self.store.add_person(conn,'Jordan Avery',[],None)
        self.begin('My name is Jordan Avery.')
        tools = {x['name']:x for x in grounded_tools(self.state.context())}
        self.assertIn('introduce_person',tools)
        self.assertNotIn('rename_person',tools)
        self.assertNotIn('forget_person',tools)
        result = self.call('introduce_person',{'name':'Jordan Avery','aliases':[],
            'subject':'self','person_id':person,'face_track_id':None})
        self.assertEqual(result.outcome,'ok')
        self.assertEqual(len(self.store.people()),1)
        self.assertEqual(next(iter(self.state.enrollments.values())).person_id,person)
        tools = {x['name']:x for x in grounded_tools(self.state.context())}
        self.assertEqual(tools['rename_person']['parameters']['properties']['person_id']['enum'],[person])
        self.assertNotIn('forget_person',tools)
        self.begin('My name is Jordan Avery.',B)
        tools = {x['name']:x for x in grounded_tools(self.state.context())}
        self.assertNotIn('rename_person',tools)
        result = self.call('rename_person',{'person_id':person,'name':'Jordon','aliases':[]})
        self.assertEqual(result.outcome,'rejected')
        self.assertEqual(self.store.person(person)['name'],'Jordan Avery')
