"""Synthetic arrivals through production scheduling, Session, state and output."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import json
import unittest
from r3x.proactive import Arrivals, Opportunity
from r3x.session import Session
from r3x.output import OutputOwner, TextOutput
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.runtime import VoiceRuntime
from r3x.contracts import ToolCall, Transcript
from r3x.semantics import grounded_tools
from tests.test_memory_core import MemoryFixture, observation, A, B
from tests.test_voice_core import Brain
from tests.test_face_identity import face as learning_face


def face(at,person=None,track='face',width=150):
    return {'at':at,'identity':person,'track_id':track,'face_epoch':0,
            'face_width_px':width,'box':[.2,.2,.3,.3],'position':'center','learning_quality':True}


def event(person=None,at=100,track='face'):
    return Opportunity(uuid4().hex,'session',at,track,person,(face(at,person,track),),float('-inf'),False)


class ArrivalTests(unittest.TestCase):
    def visible(self,arrivals,start,end,person=None,track='face'):
        for i in range(int((end-start)*2)+1):
            at=start+i*.5;arrivals.observe([face(at,person,track)],at)

    def test_stable_known_arrival_and_real_return_respect_cooldown(self):
        a=Arrivals();self.visible(a,0,2,'known')
        self.assertIsNone(a.take('s',2,float('-inf')))
        self.visible(a,2.5,4,'known');first=a.take('s',4,float('-inf'))
        self.assertFalse(first.returning)
        self.visible(a,4.5,90,'known');self.assertIsNone(a.take('s',90,0))
        a.observe([],91);a.observe([],125)
        self.visible(a,130,134,'known');self.assertIsNone(a.take('s',134,0))
        self.visible(a,300,304,'known');second=a.take('s',304,0)
        self.assertTrue(second.returning)

    def test_unknown_track_churn_cannot_repeat_name_request_in_occupied_scene(self):
        a=Arrivals();self.visible(a,0,4)
        self.assertIsNotNone(a.take('s',4,0))
        self.visible(a,4.5,70,track='new-track')
        self.assertIsNone(a.take('s',70,0))
        a.observe([],71);a.observe([],102)
        self.visible(a,105,109,track='visitor')
        self.assertIsNotNone(a.take('s',109,0))

    def test_background_face_settles_before_lull_and_is_not_hidden_from_context(self):
        for width in (32,150):
            with self.subTest(width=width):
                a=Arrivals();self.visible(a,0,4)
                a.observe([face(4),face(4,track='background',width=width)],4)
                self.assertEqual(a.audience(4),())
                for at in (4.5,5,5.5,6,6.5,7):
                    a.observe([face(at),face(at,track='background',width=width)],at)
                audience=a.audience(7)
                self.assertEqual(len(audience),2)

    def test_unheard_cancelled_arrival_can_retry_but_partial_audio_cannot(self):
        from r3x.output import Delivery
        for started in (None,4.1):
            with self.subTest(first_audio_at=started):
                a=Arrivals();self.visible(a,0,4)
                e=a.take('s',4,0)
                a.finished(e,Delivery(status='interrupted',first_audio_at=started))
                self.visible(a,4.5,10)
                self.assertIsNone(a.take('s',10,0))
                self.visible(a,10.5,49)
                retry=a.take('s',49,0)
                self.assertEqual(retry is not None,started is None)

    def test_stale_tiny_and_duplicate_identity_faces_do_not_trigger(self):
        a=Arrivals();self.visible(a,0,4,'known')
        self.assertIsNone(a.take('s',10,0))
        for i in range(10):a.observe([face(20+i,width=20)],20+i)
        self.assertIsNone(a.take('s',29,0))
        for i in range(10):a.observe([face(40+i,'same','a'),face(40+i,'same','b')],40+i)
        self.assertIsNone(a.take('s',49,0))



class ProactiveSessionTests(MemoryFixture):
    def session(self,brain,output=None):
        return Session('session',brain,output or OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace),self.state.trace,self.state)

    def test_llm_greeting_is_one_call_without_fake_user_audio_or_writes(self):
        with self.store.transaction() as conn:person=self.store.add_person(conn,'Jordan',[],None)
        brain=Brain('Jordan! Good to see you around here.');session=self.session(brain)
        result=session.process_opportunity(event(person),0,lambda:True)
        self.assertEqual(result.status,'completed');self.assertEqual(brain.calls,1)
        self.assertEqual(result.completed_text,['Jordan! Good to see you around here.'])
        self.assertEqual(self.store.rows('SELECT * FROM turns'),[])
        self.assertEqual(self.store.rows('SELECT * FROM recordings'),[])
        self.assertEqual(self.store.rows('SELECT * FROM face_references'),[])
        snapshot=brain.snapshots[0]
        self.assertEqual(snapshot.speaker,'unresolved')
        self.assertFalse(any(m.get('role')=='user' for m in snapshot.messages))
        self.assertIn('Jordan',json.dumps(snapshot.context))
        self.assertEqual({t['name'] for t in grounded_tools(snapshot.context)}, {'ask_face_question','respond_with_intent'})
        self.assertTrue(snapshot.context['visual_arrival']['startup_greeting'])

    def test_startup_cannot_choose_silence_and_later_arrival_keeps_that_choice(self):
        from r3x.contracts import Cancellation
        startup=self.state.opportunity_context(event())
        result=self.state.execute_opportunity(ToolCall('quiet','remain_silent',{'reason':'no_response_needed'}),startup,Cancellation())
        self.assertEqual(result.outcome,'rejected')
        self.assertEqual(result.reason,'StartupGreetingRequiresSpeech')
        self.begin("Hello again.")
        later=self.state.opportunity_context(replace(event(),speech_marker=0))
        self.assertFalse(later['visual_arrival']['startup_greeting'])
        self.assertIn('remain_silent',{t['name'] for t in grounded_tools(later)})

    def test_human_floor_silence_retains_hold(self):
        self.begin("Hello again.")
        class Silent(Brain):
            def reply(self,snapshot,cancel):
                yield ToolCall('quiet','remain_silent',{'reason':'background_conversation'})
        session=self.session(Silent());session.note_speech(0)
        result=session.process_opportunity(replace(event(),speech_marker=0),0,lambda:True)
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(session.proactive_hold_until,self.state.trace.clock()+120)

    def test_name_question_is_pending_only_after_audio_and_other_person_can_answer(self):
        class Question(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall('question','ask_face_question',{'text':'Hello there. What should I call you?','face_track_id':'friend'})
        session=self.session(Question())
        result=session.process_opportunity(event(track='friend'),0,lambda:True)
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.state.pending['face_track_id'],'friend')
        self.assertIsNone(self.state.pending['addressee_id'])
        from tests.test_face_identity import FaceStateTests
        FaceStateTests.visible_turn(self,"That's Casey.",[learning_face(110,B,'friend',sole=False),learning_face(110,A,'host',sole=False)])
        result=self.call('introduce_person',{'name':'Casey','aliases':[],'subject':'other','person_id':None,'face_track_id':'friend'})
        self.assertEqual(result.data['face_status'],'saved')
        self.assertEqual(self.store.profiles(self.state.tracker.namespace),{})
        self.state.pending=None
        self.session(Question(),TextOutput(self.state.trace)).process_opportunity(event(track='friend'),0,lambda:True)
        self.assertIsNone(self.state.pending)

    def test_model_can_stay_quiet_at_a_lull_without_tts_or_continuation(self):
        self.begin("Hello again.")
        class Silent(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall('quiet','remain_silent',{'reason':'no_response_needed'})
        brain=Silent();session=self.session(brain);session.note_speech(0)
        lull=replace(event(),speech_marker=0,kind='ambient',target_track=None)
        result=session.process_opportunity(lull,0,lambda:True)
        self.assertEqual(result.status,'suppressed');self.assertEqual(brain.calls,1)
        self.assertEqual(result.completed_text,[])
        self.assertLess(session.proactive_hold_until,self.state.trace.clock())
        self.assertFalse(any(r['event']=='speech_generated' for r in self.state.trace.rows))

    def test_an_arrival_cannot_be_met_with_lazy_silence(self):
        # Review October 3: in live runs arrivals usually ended in remain_silent. Greeting is
        # required; silence stays available only for a real reason such as people talking.
        self.begin("Hello again.")
        class Silent(Brain):
            def reply(self,snapshot,cancel):
                yield ToolCall('quiet','remain_silent',{'reason':'no_response_needed'})
        session=self.session(Silent());session.note_speech(0)
        result=session.process_opportunity(replace(event(),speech_marker=0),0,lambda:True)
        self.assertEqual((result.status,result.error),('failed','GreetingRequiresSpeech'))
        offered={t['name']:t for t in grounded_tools(self.state.opportunity_context(replace(event(),speech_marker=0)))}
        self.assertNotIn('no_response_needed',offered['remain_silent']['parameters']['properties']['reason']['enum'])
        self.assertIn('background_conversation',offered['remain_silent']['parameters']['properties']['reason']['enum'])
        self.assertFalse({'shift_attention','inspect_view','use_observation'} & set(offered))

    def test_visual_tools_cannot_enroll_or_write_even_with_stale_audio_state(self):
        self.begin('I am Jordan.')
        class Bad(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall('bad','introduce_person',{'name':'Jordan','aliases':[],'subject':'self','person_id':None,'face_track_id':'face'})
        brain=Bad();session=self.session(brain)
        result=session.process_opportunity(event(),0,lambda:True)
        self.assertEqual(result.error,'VisualEventDoesNotAuthorizeMutation')
        self.assertEqual(self.store.people(),[]);self.assertEqual(brain.calls,1)
        self.assertEqual(result.completed_text,[])

    def test_new_speech_cancels_before_tts_even_if_audio_timestamp_predates_event(self):
        class Resumed(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1;session.note_speech(99.9)
                yield 'Hello there, welcome back!'
        brain=Resumed();session=self.session(brain)
        result=session.process_opportunity(event(at=100),0,lambda:True)
        self.assertEqual(result.status,'interrupted');self.assertEqual(result.completed_text,[])
        self.assertIsNone(self.state.pending)
        self.assertFalse(any(r['event']=='speech_generated' for r in self.state.trace.rows))

    def test_late_target_loss_and_explicit_cancel_cannot_deliver_question(self):
        visible=[True]
        class Lost(Brain):
            def reply(self,snapshot,cancel):
                visible[0]=False
                yield ToolCall('q','ask_face_question',{'text':'What should I call you?','face_track_id':'face'})
        session=self.session(Lost());result=session.process_opportunity(event(),0,lambda:visible[0])
        self.assertEqual(result.status,'interrupted');self.assertIsNone(self.state.pending)
        session.cancel()
        brain=Brain();session.brain=brain
        self.assertEqual(session.process_opportunity(event(),0,lambda:True).status,'rejected')
        self.assertEqual(brain.calls,0)

    def test_greeting_and_question_history_informs_the_next_spoken_turn(self):
        brain=Brain('Hello Jordan, good to see you.');session=self.session(brain)
        session.process_opportunity(event(),0,lambda:True)
        self.begin('Hello Rex.')
        result=session.process_turn(self.state.current[1],Transcript('Hello Rex.',0,True),0,observation())
        self.assertEqual(result.status,'completed')
        self.assertTrue(any(m.get('role')=='assistant' and 'Jordan' in m.get('content','') for m in brain.snapshots[-1].messages))
        self.assertEqual(brain.calls,2)

    def test_visual_id_change_during_tts_prevents_first_pcm(self):
        visible=[True]
        class TTS:
            def synthesize(self,text,cancel):
                visible[0]=False
                yield b'\0\0'*10
        output=OutputOwner(TTS(),SyntheticSink(),self.state.trace)
        result=self.session(Brain(),output).process_opportunity(event(),0,lambda:visible[0])
        self.assertEqual(result.status,'interrupted')
        self.assertIsNone(result.first_audio_at)


class RuntimeGreetingTests(MemoryFixture):
    def setup_runtime(self):
        brain=Brain();trace=self.state.trace
        trace.at=100.
        session=Session('session',brain,TextOutput(trace),trace,self.state)
        from tests.test_agency_plan import Planner
        session.agency_planner = Planner()
        camera=SimpleNamespace(latest=lambda at:(face(at),))
        runtime=VoiceRuntime(SimpleNamespace(),SimpleNamespace(),None,session,trace,perception=SimpleNamespace(camera=camera))
        for at in (100.,101.,102.,103.,104.):runtime.arrivals.observe([face(at)],at)
        trace.at=108.;runtime.audio_seen_at=108.
        for at in (105.,106.,107.,108.):runtime.arrivals.observe([face(at)],at)
        return runtime,brain

    def test_idle_runtime_dispatches_without_audio_capture_and_continues_plan(self):
        runtime,brain=self.setup_runtime();runtime.proactive_tick()
        self.assertEqual(brain.calls,1)
        self.assertEqual(self.store.rows('SELECT * FROM turns'),[])
        self.state.trace.at=170;runtime.audio_seen_at=170;runtime.arrivals.observe([face(170)],170)
        runtime.proactive_tick();self.assertEqual(brain.calls,2)

    def test_audio_queues_inflight_asr_pending_question_and_silence_holds_win(self):
        for reason in ('queued_capture','queued_turn','asr','speech','hold','dead_mic','shutdown'):
            with self.subTest(reason=reason):
                runtime,brain=self.setup_runtime()
                self.state.pending=None
                if reason=='queued_capture':runtime.captures.put(object())
                if reason=='queued_turn':runtime.turns.put(object())
                if reason=='asr':runtime.asr_busy.set()
                if reason=='speech':runtime.session.note_speech(107)
                if reason=='question':self.state.pending={'delivered_at':105}
                if reason=='hold':runtime.session.proactive_hold_until=200
                if reason=='dead_mic':runtime.audio_seen_at=100
                if reason=='shutdown':runtime.stop.set()
                runtime.proactive_tick();self.assertEqual(brain.calls,0)

class FurtherArrivalTests(unittest.TestCase):
    def test_naming_invited_face_does_not_regreet_it_and_allows_another_shy_visitor(self):
        a=Arrivals()
        for i in range(5):a.observe([face(i,track='first'),face(i,track='second')],i)
        cue=a.take('s',4,0)
        invited=cue.target_track;other='second' if invited=='first' else 'first'
        for i in range(5,51):a.observe([face(i,'new-person',invited),face(i,track=other)],i)
        next_cue=a.take('s',50,0)
        self.assertEqual(next_cue.target_track,other)
        self.assertIsNone(next_cue.person_id)

    def test_frozen_camera_frame_cannot_satisfy_stability(self):
        a=Arrivals()
        for i in range(5):a.observe([face(0)],i)
        self.assertIsNone(a.take('s',4,0))
        a.observe([face(10)],10)
        self.assertIsNone(a.take('s',10,0))
