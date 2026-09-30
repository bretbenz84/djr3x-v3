"""Synthetic camera sequences through the production scheduler and output owner."""
import unittest

from r3x.contracts import ToolCall
from r3x.output import Delivery, OutputOwner
from r3x.proactive import Arrivals, Opportunity, Initiative
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests.test_memory_core import MemoryFixture
from tests.test_proactive import face
from tests import test_proactive_core
from tests.test_voice_core import Brain


class AudienceContinuityTests(unittest.TestCase):
    def settled(self):
        arrivals=Arrivals()
        for at in (0,.5,1,1.5,2,2.5,3):
            arrivals.observe([face(at,'person','old')],at)
        faces,key=arrivals.audience_snapshot(3)
        event=Opportunity('event','session',3,None,None,faces,0,False,
                          'conversation_lull','source',audience_key=key)
        return arrivals,event

    def test_supported_replacement_preserves_general_presence_not_target_authority(self):
        arrivals,event=self.settled()
        arrivals.observe([face(3.5,'person','new')],3.5)
        self.assertTrue(arrivals.valid(event,3.5,audience_bound=False))
        self.assertFalse(arrivals.valid(event,3.5))
        self.assertEqual(arrivals.audience(3.5)[0]['track_id'],'new')

    def test_conflicts_gaps_departures_and_spatial_jumps_cannot_inherit_presence(self):
        for mode in ('unknown','different_person','duplicate','empty','stale','epoch','distant','gap'):
            with self.subTest(mode=mode):
                arrivals,event=self.settled()
                at=5 if mode=='gap' else 3.5
                rows=[face(at,'person','new')]
                if mode=='unknown':rows[0]['identity']=None
                if mode=='different_person':rows[0]['identity']='other'
                if mode=='duplicate':rows.append(face(at,'person','third'))
                if mode=='empty':rows=[]
                if mode=='stale':rows[0]['at']=0
                if mode=='epoch':rows[0]['face_epoch']=1
                if mode=='distant':rows[0]['box']=[.8,.8,.1,.1]
                arrivals.observe(rows,at)
                self.assertFalse(arrivals.valid(event,at,audience_bound=False))


class AudienceRecoveryTests(MemoryFixture):
    setup_core=test_proactive_core.InitiativeCoreTests.setup_core
    turn=test_proactive_core.InitiativeCoreTests.turn
    visible=test_proactive_core.InitiativeCoreTests.visible

    def start(self):
        self.state.trace.at=100
        self.setup_core();self.turn();self.visible(108)
        self.session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.trace)

    def cancel_on_camera(self):
        runtime=self.runtime
        class Changed(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                runtime.arrivals.observe([face(108,track='replacement')],108)
                cancel.check()
                yield 'This abandoned reply must not play.'
        self.session.brain=Changed()
        self.runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'interrupted')
        self.assertTrue(self.state.initiative.lull_retry_used)

    def test_latest_transient_extra_face_defers_without_consuming_opportunity(self):
        self.start()
        latest=[face(108,track='visitor'),dict(face(108,track='noise'),learning_quality=False,
                                            detector_score=.54,embedding_available=False)]
        self.runtime.camera.latest=lambda now:tuple(latest)
        self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls,1)
        self.assertEqual(len(self.state.initiative.attempts),0)
        for at in (108.5,109,109.5,110,110.5,111,111.5):
            self.trace.at=at;self.runtime.audio_seen_at=at
            latest[:]=[face(at,track='visitor')]
            self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls,2)
        self.assertTrue(self.state.initiative.outcomes[-1]['heard_complete_phrases'])
        self.assertEqual(len(self.state.initiative.attempts),1)

    def test_unheard_replacement_retries_once_with_fresh_context_after_pacing(self):
        self.start();self.cancel_on_camera()
        replacement=Brain('A newly considered follow-up.')
        self.session.brain=replacement
        self.visible(115.9,track='replacement');self.runtime.proactive_tick()
        self.assertEqual(replacement.calls,0)
        self.visible(116,track='replacement');self.runtime.proactive_tick()
        self.assertEqual(replacement.calls,1)
        self.assertEqual(replacement.snapshots[0].context['faces'][0]['track_id'],'replacement')
        self.assertTrue(self.state.initiative.outcomes[-1]['heard_complete_phrases'])
        self.assertFalse(any('abandoned reply' in m.get('content','') for m in self.session.history.messages()))
        self.visible(124,track='replacement');self.runtime.proactive_tick()
        self.assertEqual(replacement.calls,1)
        self.assertEqual(len(self.state.initiative.attempts),2)

    def test_second_camera_cancellation_does_not_rearm_retry(self):
        self.start();self.cancel_on_camera()
        runtime=self.runtime
        class Again(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                runtime.arrivals.observe([],116)
                cancel.check()
                yield 'Do not speak.'
        brain=Again();self.session.brain=brain
        self.visible(116,track='replacement');runtime.proactive_tick()
        self.visible(124,track='replacement');runtime.proactive_tick()
        self.assertEqual(brain.calls,1)
        self.assertEqual(len(self.state.initiative.attempts),2)
        self.assertEqual(len([r for r in self.trace.rows if r['event']=='proactive_retry_pending']),1)

    def test_retry_obeys_quiet_floor_stop_input_budget_and_expiry(self):
        for gate in ('quiet','floor','stop','speech','queue','budget','expiry'):
            with self.subTest(gate=gate):
                self.start();self.cancel_on_camera()
                self.session.brain=Brain('Must remain blocked.')
                self.visible(140 if gate=='expiry' else 116,track='replacement')
                policy=self.state.initiative
                if gate=='quiet':policy.quiet=True
                if gate=='floor':policy.feedback.append(dict(action='yield_floor',expires_at=200))
                if gate=='stop':self.runtime.cancel()
                if gate=='speech':self.session.note_speech(115.5)
                if gate=='queue':self.runtime.turns.put(object())
                if gate=='budget':policy.attempts.extend([109,110,111,112,113])
                self.runtime.proactive_tick()
                # After retry expiry, normal ongoing-conversation pacing can offer
                # fresh consideration even without an optional intention record.
                self.assertEqual(self.session.brain.calls,1 if gate=='expiry' else 0)
                # The same fixture state is deliberately reused by this subtest.
                policy.quiet=False;policy.feedback.clear();policy.attempts.clear()
                policy.outcomes.clear();policy.lull_source=None;policy.lull_retry_used=False

    def test_general_output_survives_supported_named_track_replacement(self):
        self.start()
        with self.store.transaction() as conn:person=self.store.add_person(conn,'Jordan',[],None)
        for at in (105,106,107,108):self.runtime.arrivals.observe([face(at,person,'known')],at)
        self.runtime.arrivals.seen[person]['used']=True
        runtime=self.runtime
        class Replacement(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                runtime.trace.at=108.1;runtime.audio_seen_at=108.1
                runtime.arrivals.observe([face(108.1,person,'new-known')],108.1)
                yield 'The spaceship probably runs on toast.'
        self.session.brain=Replacement();runtime.proactive_tick()
        self.assertTrue(self.state.initiative.outcomes[-1]['heard_complete_phrases'])
        self.assertFalse(any(r['event']=='proactive_retry_pending' for r in self.trace.rows))

    def test_targeted_question_revalidates_raw_track_before_execution(self):
        self.start()
        with self.store.transaction() as conn:person=self.store.add_person(conn,'Jordan',[],None)
        for at in (105,106,107,108):self.runtime.arrivals.observe([face(at,person,'known')],at)
        self.runtime.arrivals.seen[person]['used']=True
        runtime=self.runtime
        class Replacement(Brain):
            def reply(self,snapshot,cancel):
                runtime.trace.at=108.1;runtime.audio_seen_at=108.1
                runtime.arrivals.observe([face(108.1,person,'new-known')],108.1)
                yield ToolCall('question','ask_face_question',dict(face_track_id='known',text='What is your name?'))
        self.session.brain=Replacement();runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'interrupted')
        self.assertFalse(self.state.initiative.outcomes[-1]['heard_complete_phrases'])
        self.assertIsNone(self.state.pending)

    def test_private_memory_stops_for_new_face_before_and_during_audio(self):
        from tests import test_memory_opportunities
        person=self.enroll(name='Avery')
        for during_audio in (False,True):
            with self.subTest(during_audio=during_audio):
                self.start()
                # Each arm has a separate cue within this temporary store.
                self.person=person
                from datetime import date
                self.state.calendar_today=lambda:date(2026,9,12)
                result=test_memory_opportunities.MemoryOpportunityTests.remember(self,predicate='test_plan_'+str(during_audio))
                self.assertEqual(result.outcome,'ok')
                runtime=self.runtime
                for at in (105,106,107,108):runtime.arrivals.observe([face(at,self.person,'known')],at)
                runtime.arrivals.seen[self.person]['used']=True
                person=self.person
                def new_face():
                    runtime.arrivals.observe([face(108,person,'known'),
                        dict(face(108,track='new'),learning_quality=False,detector_score=.54)],108)
                class Choice(Brain):
                    def reply(self,snapshot,cancel):
                        self.calls+=1
                        yield ToolCall('private-'+str(during_audio),'use_memory_opportunity',dict(
                            claim_id=snapshot.context['memory_opportunities'][0]['claim_id'],
                            text='How are preparations for your private plan going?'))
                class ChangedTTS(SyntheticTTS):
                    def synthesize(self,text,cancel):
                        new_face()
                        yield bytes(64)
                class ChangedSink(SyntheticSink):
                    def play(self,chunks,cancel,on_start):
                        def started(at):
                            on_start(at);new_face();cancel.check()
                        return super().play(chunks,cancel,started)
                self.session.brain=Choice()
                self.session.output=OutputOwner(SyntheticTTS() if during_audio else ChangedTTS(),
                    ChangedSink() if during_audio else SyntheticSink(),self.trace)
                runtime.proactive_tick()
                self.assertEqual(self.session.brain.calls,1)
                # Before audio a newcomer still withholds the private memory. Once audible,
                # the owner's September 27 rule lets the sentence finish.
                self.assertEqual(self.state.initiative.outcomes[-1]['status'],'completed' if during_audio else 'interrupted')
                if not during_audio:
                    self.assertFalse(self.store.rows('SELECT * FROM memory_followup_delivery'))
                self.assertEqual(self.state.initiative.lull_retry_used,not during_audio)
                self.state.initiative.forget();self.state.initiative.attempts.clear()


class RetryReceiptTests(unittest.TestCase):
    def test_silence_failure_partial_audio_and_non_camera_cancellation_do_not_retry(self):
        event=Opportunity('e','s',100,None,None,(face(100),),0,False,'conversation_lull','source')
        for delivery,reason in ((Delivery(status='suppressed'),'audience_changed_or_stale'),
                                (Delivery(status='failed'),'audience_changed_or_stale'),
                                (Delivery(status='interrupted',first_audio_at=101),'audience_changed_or_stale'),
                                (Delivery(status='interrupted'),'human_speech'),
                                (Delivery(status='interrupted'),'audio_stale')):
            policy=Initiative();policy.attempted(event)
            self.assertFalse(policy.retry_unheard(event,delivery,reason,102))
            self.assertFalse(policy.lull_due(110,'source',False))
