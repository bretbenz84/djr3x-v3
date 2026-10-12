"""Cross-capability interruption tests through the production Session and output owner."""
from types import SimpleNamespace
from r3x.contracts import ToolCall, Capture, Transcript, ProviderFailure
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.performance import SimulatedGesture
from r3x import visual_memory
from tests.test_memory_core import MemoryFixture
from tests import test_inspection, test_performance, test_visual_memory


class ActionVisionIntegrationTests(MemoryFixture):
    setup_session=test_inspection.InspectionTests.setup_session
    turn=test_inspection.InspectionTests.turn

    def test_resume_speech_during_capture_abandons_action_and_next_topic_does_not_resume(self):
        self.setup_session();original=self.adapter.capture
        def capture(*args):
            result=original(*args);self.session.note_speech(self.state.trace.clock()+1);return result
        self.adapter.capture=capture
        result=self.turn();self.assertEqual(result.status,'interrupted')
        action=self.state.actions.recent[-1]
        self.assertEqual(action['disposition'],'abandoned')
        self.assertEqual(action['executor']['motion_status'],'simulated')
        self.assertEqual(action['speech']['heard_text'],'')
        self.assertIn('behind',action['purpose']);self.assertIsNone(self.state.actions.active)
        def reply(snapshot,cancel):
            self.brain.snapshots.append(snapshot);yield 'Let us talk about Mars.'
        self.brain.reply=reply;self.events.clear()
        now=self.state.trace.clock()+3;self.state.trace.at=now
        result=self.session.process_turn(Capture('new','session',bytes(1024),now-1,now,now),Transcript('Forget that. Let us talk about Mars.',-.01,True),self.session.revision)
        self.assertEqual(result.status,'completed');self.assertEqual(self.events,[])
        self.assertEqual(self.brain.snapshots[-1].context['action_continuity']['recent'][-1]['disposition'],'abandoned')

    def test_cancel_settling_retains_partial_motion_and_no_capture(self):
        self.setup_session();self.adapter.wait_until=lambda *args:self.session.cancel()
        self.assertEqual(self.turn().status,'interrupted')
        action=self.state.actions.recent[-1]
        self.assertEqual(action['executor']['stage'],'settling')
        self.assertEqual(action['executor']['motion_status'],'simulated')
        self.assertNotIn('capture',self.events)

    def test_capture_success_speech_failure_remains_distinct(self):
        self.setup_session()
        def fail(*args):raise ProviderFailure('SyntheticAudioFailure')
        self.output.sink.play=fail
        self.assertEqual(self.turn().status,'failed')
        action=self.state.actions.recent[-1]
        self.assertEqual(action['executor']['stage'],'complete')
        self.assertEqual(action['speech']['heard_text'],'')
        self.assertEqual(action['disposition'],'abandoned')

    def test_changed_view_before_answer_abandons_completed_inspection(self):
        self.setup_session();original=self.brain.reply
        def reply(snapshot,cancel):
            if snapshot.continuation:self.state.attention.scene.invalidate(self.state.trace.clock())
            yield from original(snapshot,cancel)
        self.brain.reply=reply
        self.assertEqual(self.turn().status,'interrupted')
        self.assertEqual(self.state.actions.recent[-1]['executor']['stage'],'complete')
        self.assertEqual(self.state.actions.recent[-1]['speech']['heard_text'],'')

    def test_corrected_cached_description_during_tts_cannot_reach_playback(self):
        test_visual_memory.VisualMemoryTests.setup_description(self)
        self.turn();self.events.clear()
        oid=self.store.rows('SELECT id FROM visual_observations')[0]['id']
        def reply(snapshot,cancel):
            yield ToolCall('reuse','use_visual_memory',{'observation_id':oid,'text':'That is a drinking cup.'})
        self.brain.reply=reply
        def synthesize(text,cancel):
            with self.store.transaction() as conn:
                visual_memory.revise(conn,[{'observation_id':oid,'description':'A toy mug beside brown hair.','uncertainty':'Corrected.'}],
                                     'synthetic-intervening-correction','That is a toy.',100)
                self.store.advance(conn)
            yield bytes(64)
        self.output.tts.synthesize=synthesize
        result=self.turn();self.assertEqual(result.status,'interrupted');self.assertFalse(result.completed_text)
        selection=self.state.visual_selections[-1]
        self.assertEqual(selection['selected_text'],'That is a drinking cup.');self.assertEqual(selection['heard_text'],'')
        self.assertIn('toy mug',visual_memory.context(self.store,'session','[]')[0]['description'])

    def test_inspection_and_gesture_share_resource_then_speech_survives_gesture_failure(self):
        self.setup_session();self.turn()
        gestures=SimulatedGesture(self.state.trace.clock);gestures.start();self.output.gestures=gestures
        self.brain.reply=lambda snapshot,cancel:iter([test_performance.choice('after_speech')])
        def fail(*args):raise ProviderFailure('SyntheticGestureFailure')
        gestures.execute=fail
        result=self.turn();self.assertTrue(result.completed_text)
        action=self.state.actions.recent[-1]
        self.assertEqual(action['kind'],'respond_with_gesture');self.assertTrue(action['speech']['heard_text'])
        self.assertEqual(action['executor']['status'],'failed')
        self.assertEqual(len(self.state.actions.recent),2)

    def test_action_history_is_audience_scoped_bounded_and_clearable(self):
        self.setup_session()
        for _ in range(10):self.turn()
        self.assertEqual(len(self.state.actions.recent),8)
        self.assertEqual(self.state.actions.context('["another-person"]')['recent'],[])
        self.state.actions.clear();self.assertEqual(self.state.actions.context('[]')['recent'],[])

    def test_revision_speaks_after_commit_without_losing_hair_recall(self):
        test_visual_memory.VisualMemoryTests.setup_description(self);self.turn();self.events.clear();seen=[]
        def reply(snapshot,cancel):
            seen.append(snapshot)
            row=snapshot.context['visual_correction_candidates'][0]
            yield ToolCall('revise','revise_visual',{'revisions':[{'observation_id':row['id'],
                'description':'A brown-haired figure beside a toy mug.','uncertainty':'Object corrected by user.'}],
                'source_turn_id':snapshot.context['source_turn_id'],'text':'I corrected the mug and kept the hair detail.'})
        self.brain.reply=reply
        self.assertEqual(self.turn().status,'completed');self.assertEqual(len(seen),1);self.assertEqual(self.events,[])
        row=visual_memory.context(self.store,'session','[]')[0]
        self.assertIn('brown-haired',row['description']);self.assertEqual(row['source']['interpretation_revision'],1)
        self.assertIn('revision executed',str(self.session.history_messages()))

    def test_target_disappears_after_inspection_and_speech_skips_dependent_gesture(self):
        from r3x.attention_scene import AttentionFrame,VisualTarget
        self.setup_session();self.turn();self.state.trace.at+=.1
        epoch=self.state.attention.scene.view_epoch
        gestures=SimulatedGesture(self.state.trace.clock);gestures.start();self.output.gestures=gestures
        self.session.observe_attention(AttentionFrame('session','object',self.state.trace.clock(),epoch,'test',
            (VisualTarget('cup','object','red cup',(.1,.2,.2,.2)),)))
        command=test_performance.choice();command.arguments['target']='visual:object:cup'
        self.brain.reply=lambda snapshot,cancel:iter([command])
        original=self.output.sink.play
        def play(*args,**kwargs):
            result=original(*args,**kwargs);self.state.trace.at+=.1
            self.session.observe_attention(AttentionFrame('session','empty',self.state.trace.clock(),epoch,'test',()))
            return result
        self.output.sink.play=play
        result=self.turn()
        self.assertTrue(result.completed_text)
        self.assertEqual(result.gestures[0]['reason'],'GestureEvidenceStale')
        self.assertTrue(self.state.actions.recent[-1]['speech']['heard_text'])
        self.assertEqual(self.state.actions.recent[-1]['executor']['reason'],'GestureEvidenceStale')

    def test_completed_pre_speech_gesture_survives_failed_speech_in_action_history(self):
        class FailedSink(SyntheticSink):
            def play(self,*args,**kwargs):raise ProviderFailure('SyntheticPlaybackFailure')
        test_performance.PerformanceTests.setup_session(self,'with_speech',FailedSink())
        result=self.turn()
        self.assertEqual(result.status,'failed')
        action=self.state.actions.recent[-1]
        self.assertEqual(action['executor']['status'],'simulated')
        self.assertEqual(action['speech']['heard_text'],'')
        self.assertEqual(action['disposition'],'abandoned')
