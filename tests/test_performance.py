"""Production Session/output coordination with synthetic audio and simulated motion."""
from dataclasses import replace
from r3x.contracts import Capture, ToolCall, Transcript, ProviderFailure
from r3x.performance import SimulatedGesture, GestureReceipt
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.session import Session
from r3x.semantics import grounded_tools
from tests.test_memory_core import MemoryFixture
from tests.test_voice_core import Brain
from tests.test_proactive import event


def choice(timing='after_speech'):
    return ToolCall('performance','respond_with_gesture',{
        'text':'That is a spectacularly questionable plan.', 'primitive':'tilt',
        'target':'forward','intensity':'subtle','timing':timing})


class PerformanceTests(MemoryFixture):
    def setup_session(self,timing='after_speech',sink=None):
        self.adapter=SimulatedGesture(self.state.trace.clock);self.adapter.start()
        self.brain=Brain(choice(timing))
        self.output=OutputOwner(SyntheticTTS(),sink or SyntheticSink(),self.state.trace,gestures=self.adapter)
        self.session=Session('session',self.brain,self.output,self.state.trace,self.state)
        self.addCleanup(self.session.close)

    def turn(self):
        return self.session.process_turn(Capture('capture','session',bytes(1024),98,99,99.6),
            Transcript('We could make the droid wear a top hat.',-.01,True),self.session.revision)

    def test_after_gesture_follows_completed_audio_with_one_generation(self):
        self.setup_session();result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.completed_text,[choice().arguments['text']])
        self.assertEqual(result.gestures[0]['status'],'simulated')
        events=[r['event'] for r in self.state.trace.rows]
        self.assertLess(events.index('phrase_completed'),events.index('gesture_delivery'))
        self.assertEqual(self.brain.calls,1)
        self.assertFalse(self.store.claims())
        self.assertEqual(self.state.attention.scene.context()['status'],'awaiting_post_movement_frame')

    def test_before_gesture_precedes_playback(self):
        self.setup_session('before_speech');result=self.turn()
        events=[r['event'] for r in self.state.trace.rows]
        self.assertEqual(result.status,'completed')
        self.assertLess(events.index('gesture_delivery'),events.index('playback_started'))

    def test_gesture_failure_does_not_erase_successful_speech(self):
        self.setup_session();self.adapter.close()
        result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.gestures[0]['status'],'failed')
        self.assertEqual(result.gestures[0]['reason'],'GestureSimulatorNotStarted')
        self.assertTrue(result.completed_text)
        self.brain.text='I can still talk.';self.turn()
        self.assertEqual(self.brain.snapshots[-1].context['recent_performances'][0]['status'],'failed')

    def test_speech_failure_cancels_dependent_after_gesture(self):
        class FailedSink(SyntheticSink):
            def play(self,*args,**kwargs):raise ProviderFailure('SyntheticPlaybackFailure')
        self.setup_session(sink=FailedSink());result=self.turn()
        self.assertEqual(result.status,'failed')
        self.assertFalse(result.completed_text)
        self.assertEqual(result.gestures[0]['status'],'cancelled')

    def test_completed_before_gesture_survives_later_speech_failure(self):
        class FailedSink(SyntheticSink):
            def play(self,*args,**kwargs):raise ProviderFailure('SyntheticPlaybackFailure')
        self.setup_session('before_speech',FailedSink());result=self.turn()
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.gestures[0]['status'],'simulated')
        self.assertFalse(result.completed_text)

    def test_cancel_during_after_gesture_retains_heard_phrase(self):
        self.setup_session()
        original=self.adapter.execute
        def interrupted(gesture,cancel):
            receipt=original(gesture,cancel);self.session.cancel();return receipt
        self.adapter.execute=interrupted;result=self.turn()
        self.assertEqual(result.status,'interrupted')
        self.assertTrue(result.completed_text)
        self.assertEqual(result.gestures[0]['status'],'cancelled')

    def test_resource_conflict_skips_gesture_but_preserves_reply(self):
        self.setup_session();self.output._motion_lock.acquire()
        try:result=self.turn()
        finally:self.output._motion_lock.release()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.gestures[0]['reason'],'MotionResourceBusy')

    def test_invalid_physical_receipt_cannot_become_simulated_success(self):
        self.setup_session()
        self.adapter.execute=lambda g,c:GestureReceipt(g.request_id,'simulated',100,'hardware')
        result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.gestures[0]['reason'],'InvalidGestureReceipt')

    def test_unavailable_capability_not_offered_or_executed(self):
        self.setup_session();self.output.gestures=None;self.brain.text='Hello there.';self.turn()
        self.assertNotIn('respond_with_gesture',{t['name'] for t in grounded_tools(self.brain.snapshots[0].context)})
        self.brain.text=choice();self.assertEqual(self.turn().status,'failed')

    def test_proactive_performance_uses_same_path_without_fake_human_turn(self):
        self.setup_session()
        result=self.session.process_opportunity(replace(event(),kind="ambient",target_track=None),0,lambda:True)
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.gestures[0]['status'],'simulated')
        self.assertEqual(self.brain.calls,1)
        self.assertFalse(self.store.rows('SELECT * FROM turns'))

    def test_expired_gesture_is_skipped_after_long_speech(self):
        owner=self
        class SlowSink(SyntheticSink):
            def play(self,*args,**kwargs):
                receipt=super().play(*args,**kwargs);owner.state.trace.at+=31;return receipt
        self.setup_session(sink=SlowSink());result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.gestures[0]['reason'],'GestureEvidenceStale')

    def test_new_speech_at_audio_completion_preserves_receipt_and_cancels_gesture(self):
        owner=self
        class InterruptedSink(SyntheticSink):
            def play(self,*args,**kwargs):
                receipt=super().play(*args,**kwargs);owner.session.note_speech(101);return receipt
        self.setup_session(sink=InterruptedSink());result=self.turn()
        self.assertEqual(result.status,'interrupted')
        self.assertTrue(result.completed_text)
        self.assertEqual(result.gestures[0]['status'],'cancelled')

    def test_visual_target_loss_during_speech_skips_gesture(self):
        from r3x.attention_scene import AttentionFrame,VisualTarget
        owner=self
        class ChangedSink(SyntheticSink):
            def play(self,*args,**kwargs):
                receipt=super().play(*args,**kwargs);owner.state.trace.at+=.1
                owner.session.observe_attention(AttentionFrame('session','empty',100.1,0,'test',()))
                return receipt
        self.setup_session(sink=ChangedSink())
        self.session.observe_attention(AttentionFrame('session','object',100,0,'test',
            (VisualTarget('cup','object','red cup',(.1,.2,.2,.2)),)))
        self.brain.text.arguments['target']='visual:object:cup'
        result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.gestures[0]['reason'],'GestureEvidenceStale')

    def test_proactive_guard_change_after_audio_cancels_the_gesture(self):
        valid=[True]
        class ChangedSink(SyntheticSink):
            def play(self,*args,**kwargs):
                receipt=super().play(*args,**kwargs);valid[0]=False;return receipt
        self.setup_session(sink=ChangedSink())
        result=self.session.process_opportunity(replace(event(),kind="ambient",target_track=None),0,lambda:valid[0])
        self.assertEqual(result.status,'interrupted')
        self.assertTrue(result.completed_text)
        self.assertEqual(result.gestures[0]['status'],'cancelled')
