"""Observable initiative contracts through production Session and runtime."""
from types import SimpleNamespace
from uuid import uuid4

from r3x.contracts import Capture, ToolCall, Transcript, ProviderFailure
from r3x.output import OutputOwner, TextOutput
from r3x.proactive import Opportunity
from r3x.runtime import VoiceRuntime
from r3x.semantics import grounded_tools
from r3x.session import Session
from r3x.settings import Settings
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests.test_memory_core import MemoryFixture, observation, B
from tests.test_proactive import face
from tests.test_voice_core import Brain


class InitiativeCoreTests(MemoryFixture):
    def setup_core(self, brain=None, output=None):
        self.brain = brain or Brain('Those cameras are earning their dramatic reputation.')
        self.trace = self.state.trace
        self.session = Session('session',self.brain,output or TextOutput(self.trace),self.trace,self.state)
        self.runtime = VoiceRuntime(SimpleNamespace(),SimpleNamespace(),None,self.session,self.trace,
                                    perception=SimpleNamespace(camera=SimpleNamespace()))
        self.runtime.started_at = 0
        self.runtime.camera.latest=lambda now:self.runtime.arrivals.faces

    def turn(self,text='Rex, we finally fixed the camera.',voice=None):
        now = self.trace.at
        capture = Capture(uuid4().hex,'session',bytes(96000),now-4,now-1,now,
                          voiced_intervals=((0,3),))
        self.session.note_speech(now-1)
        return self.session.process_turn(capture,Transcript(text,-.01,True),0,voice or observation())

    def visible(self,now,track='visitor'):
        for at in (now-3,now-2,now-1,now):
            self.runtime.arrivals.observe([face(at,track=track)],at)
        # Already greeted in this visit; test the lull rather than arrival selection.
        self.runtime.arrivals.seen[track]['used'] = True
        self.trace.at = now
        self.runtime.audio_seen_at = now

    def lull(self):
        return Opportunity(uuid4().hex,'session',self.trace.at,None,None,(face(self.trace.at),),
                           self.session._latest_speech,False,'conversation_lull',self.state.current[0])

    def test_eight_second_lull_uses_same_history_one_generation_and_no_identity_writes(self):
        self.setup_core(); self.turn()
        sources = self.store.rows('SELECT * FROM turns')
        self.visible(107.9);self.runtime.proactive_tick();self.assertEqual(self.brain.calls,1)
        self.visible(108);self.runtime.proactive_tick();self.assertEqual(self.brain.calls,2)
        snapshot = self.brain.snapshots[-1]
        self.assertEqual(snapshot.context['trigger'],'conversation_lull')
        self.assertEqual(snapshot.speaker,'unresolved')
        self.assertEqual({t['name'] for t in grounded_tools(snapshot.context)},{'remain_silent','ask_face_question','respond_with_intent'})
        users = [m['content'] for m in snapshot.messages if m.get('role')=='user']
        self.assertEqual(users,['Rex, we finally fixed the camera.'])
        self.assertEqual(self.store.rows('SELECT * FROM turns'),sources)
        self.assertEqual(self.store.people(),[])
        self.assertEqual(self.store.claims(),[])
        self.assertEqual(self.store.rows('SELECT * FROM recordings'),[])
        self.visible(170);self.runtime.proactive_tick();self.assertEqual(self.brain.calls,2)

    def test_rolling_attempt_cap_does_not_reset_on_new_human_turns(self):
        self.setup_core()
        for i in range(7):
            self.trace.at=100+i*20;self.turn()
            self.visible(self.trace.at+8);self.runtime.proactive_tick()
        self.assertEqual(len(self.state.initiative.outcomes),6)
        self.assertEqual(self.brain.calls,13)  # Seven replies, six optional generations.

    def test_stale_camera_unknown_audience_and_frozen_evaluation(self):
        for mode in ('stale','no_camera','frozen'):
            with self.subTest(mode=mode):
                self.setup_core();self.turn();self.visible(108)
                if mode=='stale':self.trace.at=110;self.runtime.audio_seen_at=110
                if mode=='no_camera':self.runtime.camera=None
                if mode=='frozen':self.state.frozen=True
                self.runtime.proactive_tick();self.assertEqual(self.brain.calls,1)
                self.state.frozen=False

    def test_new_speech_during_lull_cancels_without_delivery(self):
        self.setup_core();self.turn();self.visible(108)
        session=self.session
        class Resumed(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                session.note_speech(108.1)
                yield 'I was about to offer another thought.'
        self.session.brain=Resumed()
        self.runtime.proactive_tick()
        outcome=self.state.initiative.outcomes[-1]
        self.assertEqual(outcome['status'],'interrupted')
        self.assertFalse(outcome['heard_complete_phrases'])
        self.assertEqual(outcome['response'],'not_applicable')

    def test_lull_can_ask_visible_person_name_and_wait_for_audible_answer(self):
        self.setup_core(); self.turn(); self.visible(108)
        class Question(Brain):
            def reply(self,snapshot,cancel):
                yield ToolCall('name','ask_face_question',
                    {'face_track_id':'visitor','text':'We have not met yet. What should I call you?'})
        self.session.brain=Question()
        self.session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.trace)
        self.runtime.proactive_tick()
        self.assertEqual(self.state.pending['face_track_id'],'visitor')
        self.assertIsNone(self.state.pending['addressee_id'])
        self.assertEqual(self.store.people(),[])
        self.visible(130); before=len(self.state.initiative.outcomes)
        self.runtime.proactive_tick()
        self.assertEqual(len(self.state.initiative.outcomes),before)

    def test_audio_timestamp_update_during_clock_read_does_not_cancel(self):
        self.setup_core();self.turn();self.visible(108)
        runtime,trace=self.runtime,self.trace
        armed=[False]
        def clock():
            now=trace.at
            if armed[0]:
                armed[0]=False
                trace.at+=.01
                runtime.audio_seen_at=trace.at
            return now
        trace.clock=clock
        class Updating(Brain):
            def reply(self,snapshot,cancel):
                armed[0]=True
                cancel.check()
                yield 'I have a thought to share with everyone.'
        self.session.brain=Updating()
        self.runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'completed')
        self.assertFalse(any(r['event']=='proactive_invalidated' for r in trace.rows))

    def test_stale_audio_reports_cancellation_reason(self):
        self.setup_core();self.turn();self.visible(108)
        runtime=self.runtime
        class Stalled(Brain):
            def reply(self,snapshot,cancel):
                runtime.audio_seen_at=100
                cancel.check()
                yield 'This must not play.'
        self.session.brain=Stalled();self.runtime.proactive_tick()
        rows=[r for r in self.trace.rows if r['event']=='proactive_invalidated']
        self.assertTrue(rows)
        self.assertEqual(rows[0]['reason'],'audio_stale')
        self.assertFalse(self.state.initiative.outcomes[-1]['heard_complete_phrases'])

    def test_new_camera_cache_prevents_false_stale_poll_cancellation(self):
        self.setup_core();self.turn();self.visible(108)
        runtime,trace=self.runtime,self.trace
        latest=[face(108,track='visitor')]
        runtime.camera.latest=lambda now:tuple(latest)
        class Slow(Brain):
            def reply(self,snapshot,cancel):
                # No main-loop visual poll has occurred. The camera has already
                # supplied a fresh frame before the old snapshot expires.
                trace.at=109.1;runtime.audio_seen_at=109.1
                latest[:]=[face(108.7,track='visitor')]
                cancel.check()
                yield 'Good to see everyone here.'
        self.session.brain=Slow();runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'completed')
        self.assertFalse(any(r['event']=='proactive_invalidated' for r in trace.rows))

    def test_new_audience_member_during_tts_cancels_before_first_pcm(self):
        self.setup_core();self.turn();self.visible(108)
        runtime=self.runtime
        class Changed:
            def synthesize(self,text,cancel):
                runtime.arrivals.observe([face(108,track='visitor'),face(108,track='new')],108)
                yield bytes(64)
        self.session.output=OutputOwner(Changed(),SyntheticSink(),self.trace)
        self.runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'interrupted')
        self.assertFalse(self.state.initiative.outcomes[-1]['heard_complete_phrases'])

    def test_camera_change_after_first_audio_finishes_general_reply(self):
        self.setup_core();self.turn();self.visible(108)
        runtime=self.runtime
        class MovingSink(SyntheticSink):
            def play(self,chunks,cancel,on_start):
                def started(at):
                    on_start(at)
                    runtime.arrivals.observe([face(108,track='new')],108)
                    cancel.check()
                return super().play(chunks,cancel,started)
        self.session.output=OutputOwner(SyntheticTTS(),MovingSink(),self.trace)
        self.runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'completed')
        self.assertTrue(self.state.initiative.outcomes[-1]['heard_complete_phrases'])

    def test_human_speech_after_first_audio_still_interrupts(self):
        self.setup_core();self.turn();self.visible(108)
        session=self.session
        class InterruptingSink(SyntheticSink):
            def play(self,chunks,cancel,on_start):
                def started(at):
                    on_start(at)
                    session.note_speech(108.1)
                    cancel.check()
                return super().play(chunks,cancel,started)
        self.session.output=OutputOwner(SyntheticTTS(),InterruptingSink(),self.trace)
        self.runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'interrupted')
        self.assertFalse(self.state.initiative.outcomes[-1]['heard_complete_phrases'])

    def test_queue_race_invalidates_opportunity_before_generation(self):
        self.setup_core();self.turn();self.visible(108)
        event=self.lull()
        self.runtime.turns.put(object())
        result=self.session.process_opportunity(event,0,lambda:self.runtime.turns.empty())
        self.assertEqual(result.status,'interrupted');self.assertEqual(self.brain.calls,1)

    def test_explicit_quiet_is_silent_and_survives_other_speech_and_history_eviction(self):
        class Quiet(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall(uuid4().hex,'set_initiative',{'mode':'quiet','source_turn_id':snapshot.context['source_turn_id']})
        self.setup_core(Quiet())
        result=self.turn('Please stop starting conversations until I ask you to again.')
        self.assertEqual(result.status,'suppressed');self.assertEqual(self.brain.calls,1)
        self.assertTrue(self.state.initiative.quiet)
        self.assertEqual(result.completed_text,[])
        self.session.brain=Brain();self.trace.at=120
        self.assertEqual(self.turn('Rex, what does that mean?',observation(B)).status,'completed')
        self.session.history.clear()
        self.visible(500);self.runtime.proactive_tick()
        self.assertEqual(self.session.brain.calls,1)
        self.assertTrue(self.state.initiative.quiet)

    def test_explicit_resume_updates_policy_after_success_and_uses_bounded_continuation(self):
        class Resume(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                if snapshot.tools_enabled:
                    yield ToolCall(uuid4().hex,'set_initiative',{'mode':'available','source_turn_id':snapshot.context['source_turn_id']})
                else: yield 'I have a few thoughts worth sharing.'
        self.setup_core(Resume());self.state.initiative.quiet=True
        result=self.turn('You can start conversations again, Rex.')
        self.assertEqual(result.status,'completed');self.assertEqual(self.brain.calls,2)
        self.assertFalse(self.state.initiative.quiet)

    def test_untrusted_or_stale_policy_cannot_change_state(self):
        self.setup_core();self.turn()
        from r3x.contracts import Cancellation
        args={'mode':'quiet','source_turn_id':'wrong-source'}
        call=ToolCall('invalid','set_initiative',args)
        self.assertEqual(self.state.execute(call,self.store.revision,Cancellation()).reason,'SourceTurnMismatch')
        self.assertFalse(self.state.initiative.quiet)
        args['source_turn_id']=self.state.current[0]
        self.assertEqual(self.state.execute(call,-1,Cancellation()).reason,'StaleStateRevision')
        self.assertFalse(self.state.initiative.quiet)

    def test_lull_cannot_use_prior_speaker_to_mutate_policy_or_people(self):
        self.setup_core();self.turn()
        class Bad(Brain):
            def reply(self,snapshot,cancel):
                yield ToolCall('bad','set_initiative',{'mode':'quiet','source_turn_id':snapshot.context['opportunity']['source_turn_id']})
        self.session.brain=Bad()
        result=self.session.process_opportunity(self.lull(),0,lambda:True)
        self.assertEqual(result.error,'VisualEventDoesNotAuthorizeMutation')
        self.assertFalse(self.state.initiative.quiet)

    def test_receipts_distinguish_audio_text_failure_and_never_assume_an_answer(self):
        self.setup_core();self.turn()
        self.session.process_opportunity(self.lull(),0,lambda:True)
        self.assertEqual(self.state.initiative.outcomes[-1]['response'],'not_applicable')
        self.session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.trace)
        self.session.process_opportunity(self.lull(),0,lambda:True)
        self.assertTrue(self.state.initiative.outcomes[-1]['heard_complete_phrases'])
        self.assertEqual(self.state.initiative.outcomes[-1]['response'],'unknown')
        class Failed:
            def synthesize(self,text,cancel):
                raise ProviderFailure('SyntheticTTSFailure')
                yield b''
        self.session.output=OutputOwner(Failed(),SyntheticSink(),self.trace)
        self.session.process_opportunity(self.lull(),0,lambda:True)
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'failed')
        self.assertEqual(self.state.initiative.outcomes[-1]['response'],'not_applicable')

    def test_lull_setting_validates_and_is_consumed(self):
        self.assertEqual(Settings().proactive_lull_seconds,8)
        for invalid in (True,0,float('nan'),121):
            with self.assertRaises(ValueError):Settings(proactive_lull_seconds=invalid)
        self.setup_core();self.runtime.lull_seconds=10;self.turn()
        self.visible(108);self.runtime.proactive_tick();self.assertEqual(self.brain.calls,1)
        self.visible(110);self.runtime.proactive_tick();self.assertEqual(self.brain.calls,2)

    def test_documented_scenario_uses_shared_session_and_preserves_source_counts(self):
        from pathlib import Path
        from contextlib import redirect_stdout
        from io import StringIO
        from r3x.scenario import run
        with redirect_stdout(StringIO()):
            self.assertTrue(run(Path(__file__).parent/'fixtures'/'proactive_core.json'))

    def test_forget_removes_initiative_source_links_without_resetting_quiet_or_pacing(self):
        person=self.enroll('Jordan')
        self.setup_core();self.turn('Rex, that was a good afternoon.')
        self.session.process_opportunity(self.lull(),0,lambda:True)
        self.state.initiative.quiet=True
        self.state.initiative.policy_source=self.state.current[0]
        attempts=list(self.state.initiative.attempts)
        result=self.call('forget_person',{'person_id':person})
        self.assertEqual(result.outcome,'ok')
        self.assertEqual(list(self.state.initiative.outcomes),[])
        self.assertIsNone(self.state.initiative.policy_source)
        self.assertTrue(self.state.initiative.quiet)
        self.assertEqual(list(self.state.initiative.attempts),attempts)

    def test_restart_does_not_restore_a_session_quiet_boundary(self):
        from r3x.state import CoreState
        from r3x.speakers import SpeakerTracker
        self.state.initiative.quiet=True
        restarted=CoreState(self.store,SpeakerTracker(self.state.tracker.namespace),self.state.trace)
        self.assertFalse(restarted.initiative.quiet)

    def test_empty_model_output_is_failure_not_a_silence_decision(self):
        self.setup_core();self.turn()
        self.session.brain=Brain('')
        result=self.session.process_opportunity(self.lull(),0,lambda:True)
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.error,'EmptyModelResponse')
        self.assertEqual(self.state.initiative.outcomes[-1]['response'],'not_applicable')

    def test_invalid_audience_attempt_is_paced_instead_of_retried_each_tick(self):
        self.setup_core();self.turn();self.visible(108)
        self.runtime.arrivals.valid=lambda event,now:False
        for _ in range(5):self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls,1)
        self.assertEqual(len(self.state.initiative.attempts),1)
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'interrupted')

    def plan_brain(self, status='active', silent=False):
        from tests.test_conversation_intent import proposal
        class Planned(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1;self.snapshots.append(snapshot)
                update=proposal(snapshot.context,status=status,
                    objective='Share interesting camera ideas.' if status in {'active','suspended'} else None,
                    openings=[])
                if status in {'completed','abandoned'}:
                    update.update(target_ids=[],memory_ids=[])
                yield ToolCall(uuid4().hex,'remain_silent' if silent else 'respond_with_intent',
                    dict(reason='no_response_needed',intent_update=update) if silent else
                    dict(text='Here is a fresh camera idea.',intent_update=update))
        self.session.brain=Planned()
        return self.session.brain

    def start_plan_lull(self):
        self.setup_core();self.turn();self.visible(108)
        brain=self.plan_brain()
        self.session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.trace)
        self.runtime.proactive_tick()
        self.assertEqual(brain.calls,1)
        return brain

    def test_plan_continues_across_multiple_lulls_without_new_human_turn(self):
        brain=self.start_plan_lull();sources=self.store.rows('SELECT * FROM turns')
        for at,calls in ((115,1),(116,2),(130,2),(131,3),(160,3),(161,4),(220,4),(221,5)):
            self.visible(at);self.runtime.proactive_tick();self.assertEqual(brain.calls,calls)
        self.assertEqual(self.store.rows('SELECT * FROM turns'),sources)
        self.assertEqual(self.store.claims(),[])
        context=brain.snapshots[-1].context
        self.assertEqual(context['conversation_intent']['current']['objective'],'Share interesting camera ideas.')
        self.assertEqual(context['participation']['lull_attempts_since_source'],4)
        self.assertTrue(context['participation']['recent_opportunities'][-1]['heard_complete_phrases'])
        self.assertEqual(context['participation']['recent_opportunities'][-1]['delivered_text'],'Here is a fresh camera idea.')

    def test_greeting_plan_can_continue_without_any_human_source(self):
        self.setup_core();self.visible(100);brain=self.plan_brain()
        greeting=Opportunity(uuid4().hex,'session',100,'visitor',None,(face(100,track='visitor'),),
            self.session._latest_speech,False)
        self.session.process_opportunity(greeting,0,lambda:True)
        self.visible(107);self.runtime.proactive_tick();self.assertEqual(brain.calls,1)
        self.visible(108);self.runtime.proactive_tick();self.assertEqual(brain.calls,2)
        self.assertIsNone(self.state.current)
        self.assertEqual(brain.snapshots[-1].context['trigger'],'conversation_lull')

    def test_plan_silence_reconsiders_but_completion_stops_repeated_lulls(self):
        self.start_plan_lull();brain=self.plan_brain(silent=True)
        self.visible(116);self.runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'suppressed')
        self.visible(130);self.runtime.proactive_tick();self.assertEqual(brain.calls,1)
        ending=self.plan_brain(status='completed',silent=True)
        self.visible(131);self.runtime.proactive_tick();self.assertEqual(ending.calls,1)
        self.visible(500);self.runtime.proactive_tick();self.assertEqual(ending.calls,1)

    def test_suspended_or_changed_audience_plan_does_not_reopen_lull(self):
        brain=self.start_plan_lull()
        self.visible(120,track='other');self.runtime.proactive_tick()
        self.assertEqual(brain.calls,1)
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')
        self.visible(140);self.runtime.proactive_tick();self.assertEqual(brain.calls,1)

    def test_quiet_and_pending_question_block_repeated_plan_lulls(self):
        brain=self.start_plan_lull();self.state.initiative.quiet=True
        self.visible(116);self.runtime.proactive_tick();self.assertEqual(brain.calls,1)
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')
        self.state.initiative.quiet=False
        self.state.conversation_intent.record.status='active'
        self.state.pending={'delivered_at':138}
        self.visible(150);self.runtime.proactive_tick();self.assertEqual(brain.calls,1)

    def test_failed_plan_attempt_backs_off_instead_of_polling_each_tick(self):
        self.start_plan_lull()
        class Failed(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                raise ProviderFailure('SyntheticProviderFailure')
                yield ''
        failing=Failed();self.session.brain=failing
        self.visible(116);self.runtime.proactive_tick();self.assertEqual(failing.calls,1)
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'failed')
        for at in (117,120,125,130):
            self.visible(at);self.runtime.proactive_tick()
        self.assertEqual(failing.calls,1)
        self.visible(131);self.runtime.proactive_tick();self.assertEqual(failing.calls,2)

    def test_new_human_turn_reopens_normal_lull_without_resetting_global_budget(self):
        self.start_plan_lull();self.visible(116);self.runtime.proactive_tick()
        self.session.brain=Brain('Sure.');self.trace.at=150;self.turn('Another camera question.')
        brain=self.plan_brain();self.visible(158);self.runtime.proactive_tick()
        self.assertEqual(brain.calls,1)
        self.assertEqual(self.state.initiative.lull_attempts,1)
        self.assertEqual(len(self.state.initiative.attempts),3)


    def test_plan_delay_starts_after_output_finishes(self):
        self.start_plan_lull()
        trace=self.trace
        class SlowSink(SyntheticSink):
            def play(self,chunks,cancel,on_start):
                result=super().play(chunks,cancel,on_start)
                trace.at+=10
                return result
        self.session.output=OutputOwner(SyntheticTTS(),SlowSink(),trace)
        self.visible(116);self.runtime.proactive_tick()
        self.assertEqual(self.state.initiative.outcomes[-1]['at'],126)
        brain=self.session.brain
        self.visible(140);self.runtime.proactive_tick();self.assertEqual(brain.calls,2)
        self.session.output=TextOutput(trace)
        self.visible(141);self.runtime.proactive_tick();self.assertEqual(brain.calls,3)
