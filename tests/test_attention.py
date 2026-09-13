"""Synthetic attention choices through the production Session; no devices/models."""
from r3x.attention import AttentionReceipt, AttentionState, SimulatedAttention
from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.semantics import grounded_tools
from r3x.session import Session
from tests.test_memory_core import MemoryFixture
from tests.test_proactive import event
from tests.test_voice_core import Brain


def choice(target='left'):
    return ToolCall('attention-call', 'shift_attention', dict(target=target,
        purpose='disengage_gaze', reason='Briefly look away while considering the conversation.', hold='brief'))


class AttentionTests(MemoryFixture):
    def setup_session(self, adapter=None, brain=None):
        self.adapter = adapter or SimulatedAttention(self.state.trace.clock)
        self.adapter.start()
        self.brain = brain or Brain(choice())
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, attention=self.adapter)
        self.session = Session('session', self.brain, self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)
        return self.session

    def turn(self, text='You can look away while you think.'):
        return self.session.process_turn(Capture('capture','session',bytes(1024),98,99,99.6),
            Transcript(text,-.01,True),self.session.revision)

    def test_silent_choice_one_generation_no_biography_or_fake_delivery(self):
        self.setup_session()
        result = self.turn()
        self.assertEqual((result.status,result.medium,result.completed_text),('suppressed','none',[]))
        self.assertEqual(self.brain.calls,1)
        self.assertEqual(self.state.attention.active.target,'left')
        self.assertFalse(self.store.claims()); self.assertFalse(self.store.people())
        receipts=[r for r in self.state.trace.rows if r['event']=='attention_result']
        self.assertEqual(receipts[0]['outcome'],'simulated')
        self.assertFalse(any(r['event']=='speech_generated' for r in self.state.trace.rows))

    def test_proactive_choice_uses_same_owner_without_inventing_a_speaker(self):
        self.setup_session()
        result=self.session.process_opportunity(event(),0,lambda:True)
        self.assertEqual(result.status,'suppressed')
        self.assertFalse(self.store.rows('SELECT * FROM turns'))
        self.assertFalse(any(m.get('role')=='user' for m in self.brain.snapshots[0].messages))
        self.assertEqual(self.session.proactive_hold_until,float('-inf'))

    def test_following_reply_sees_attention_and_original_words(self):
        self.setup_session(); self.turn()
        self.brain.text='I am still listening.'
        result=self.turn('Are you still listening?')
        self.assertEqual(result.status,'completed')
        snapshot=self.brain.snapshots[-1]
        self.assertEqual(snapshot.context['attention']['active']['target'],'left')
        self.assertIn('You can look away',str(snapshot.messages))
        self.assertEqual(self.brain.calls,2)

    def test_expiry_releases_intent_without_generating_a_return_movement(self):
        self.setup_session();self.turn()
        self.state.trace.at+=4
        context=self.state.attention.context()
        self.assertIsNone(context['active'])
        self.assertEqual(len(context['recent']),1)
        self.assertIsNone(AttentionState(self.state.trace.clock).active)

    def test_ordinary_live_output_has_no_motion_tool(self):
        self.setup_session();self.output.attention=None
        self.brain.text='Hello.';self.turn()
        self.assertNotIn('shift_attention',{s['name'] for s in grounded_tools(self.brain.snapshots[0].context)})
        self.brain.text=choice();result=self.turn()
        self.assertEqual(result.status,'failed')
        self.assertIsNone(self.state.attention.active)

    def test_cancel_during_adapter_cannot_commit_delayed_result(self):
        self.setup_session()
        original=self.adapter.execute
        def interrupted(intent,cancel):
            receipt=original(intent,cancel)
            self.session.cancel()
            return receipt
        self.adapter.execute=interrupted
        self.assertEqual(self.turn().status,'interrupted')
        self.assertIsNone(self.state.attention.active)
        self.assertIsNone(self.state.attention.pending)

    def test_selected_opportunity_invalidation_cancels_before_commit(self):
        self.setup_session();valid=[True]
        original=self.adapter.execute
        def invalidate(intent,cancel):
            result=original(intent,cancel);valid[0]=False;return result
        self.adapter.execute=invalidate
        self.assertEqual(self.session.process_opportunity(event(),0,lambda:valid[0]).status,'interrupted')
        self.assertIsNone(self.state.attention.active)

    def test_receipt_cannot_claim_physical_completion(self):
        self.setup_session()
        self.adapter.execute=lambda intent,cancel: AttentionReceipt(intent.request_id,'completed',100,'hardware')
        self.assertEqual(self.turn().status,'failed')
        self.assertIsNone(self.state.attention.active)
        self.assertIsNone(self.state.attention.pending)

    def test_invalid_target_and_closed_adapter_are_rejected(self):
        self.setup_session(brain=Brain(choice('invented-object')))
        self.assertEqual(self.turn().status,'failed')
        self.brain.text=choice();self.adapter.close()
        self.assertEqual(self.turn().status,'failed')
        self.assertIsNone(self.state.attention.active)

    def test_new_speech_during_simulation_cancels_the_old_choice(self):
        self.setup_session()
        original=self.adapter.execute
        def speech(intent,cancel):
            receipt=original(intent,cancel);self.session.note_speech(101);return receipt
        self.adapter.execute=speech
        self.assertEqual(self.turn().status,'interrupted')
        self.assertIsNone(self.state.attention.active)

    def test_adapter_result_after_expiry_is_not_committed(self):
        self.setup_session()
        original=self.adapter.execute
        def delayed(intent,cancel):
            receipt=original(intent,cancel);self.state.trace.at+=4;return receipt
        self.adapter.execute=delayed
        self.assertEqual(self.turn().status,'failed')
        self.assertIsNone(self.state.attention.active)

    def test_human_floor_hold_suppresses_proactive_attention(self):
        self.setup_session()
        self.state.initiative.floor_held=lambda now:True
        self.assertEqual(self.session.process_opportunity(event(),0,lambda:True).status,'suppressed')
        self.assertEqual(self.brain.calls,0)
        self.assertIsNone(self.state.attention.active)

    def publish(self, targets=None, **changes):
        from r3x.attention_scene import AttentionFrame, VisualTarget
        from dataclasses import replace
        if targets is None:
            targets=(VisualTarget('cup-1','object','red cup',(.1,.2,.2,.2)),)
        frame=AttentionFrame('session','frame-'+str(self.state.trace.at),self.state.trace.at,
            self.state.attention.scene.view_epoch,'synthetic-camera-1',targets)
        frame=replace(frame,**changes)
        self.session.observe_attention(frame)
        return frame

    def test_object_selection_is_grounded_and_does_not_save_a_fact(self):
        self.setup_session(brain=Brain(choice('visual:object:cup-1')))
        self.publish()
        self.assertEqual(self.turn().status,'suppressed')
        anchor=self.state.attention.active.anchor
        self.assertEqual(anchor['label'],'red cup')
        self.assertEqual(anchor['coordinate_frame'],'image_normalized')
        self.assertEqual(self.state.attention.context()['vision']['status'],'awaiting_post_movement_frame')
        self.assertFalse(self.store.claims())
        tools=grounded_tools(self.brain.snapshots[0].context)
        tool=next(t for t in tools if t['name']=='shift_attention')
        self.assertIn('visual:object:cup-1',tool['parameters']['properties']['target']['enum'])

    def test_post_movement_frame_must_follow_completion_in_the_new_view(self):
        self.setup_session(brain=Brain(choice('visual:object:cup-1')))
        old=self.publish();self.turn()
        with self.assertRaises(ValueError):self.session.observe_attention(old)
        with self.assertRaises(ValueError):self.publish()  # same timestamp as completion
        self.state.trace.at+=.2
        self.publish()
        context=self.state.attention.context()
        self.assertEqual(context['vision']['status'],'fresh')
        self.assertEqual(context['vision']['comparison'],'new_view_baseline')
        self.assertEqual(context['active']['target'],'visual:object:cup-1')
        self.brain.text='What would you like to talk about?';self.turn()
        self.assertEqual(self.brain.snapshots[-1].context['attention']['vision']['view_epoch'],1)
        self.assertEqual(self.brain.calls,2)  # no automatic inspection model call

    def test_frame_expiry_removes_offered_targets(self):
        self.setup_session(brain=Brain(choice('visual:object:cup-1')))
        self.publish();self.state.trace.at+=2.1
        self.assertEqual(self.turn().status,'failed')
        self.assertFalse(self.brain.snapshots[0].context['attention']['vision']['targets'])

    def test_target_disappearance_during_execution_rejects_completion(self):
        self.setup_session(brain=Brain(choice('visual:object:cup-1')));self.publish()
        original=self.adapter.execute
        def disappear(intent,cancel):
            receipt=original(intent,cancel);self.state.trace.at+=.1
            self.publish(targets=());return receipt
        self.adapter.execute=disappear
        self.assertEqual(self.turn().status,'failed')
        self.assertIsNone(self.state.attention.active)

    def test_calibration_change_during_execution_rejects_completion(self):
        self.setup_session(brain=Brain(choice('visual:object:cup-1')));self.publish()
        original=self.adapter.execute
        def recalibrate(intent,cancel):
            receipt=original(intent,cancel);self.state.trace.at+=.1
            self.publish(calibration_id='different-camera');return receipt
        self.adapter.execute=recalibrate
        self.assertEqual(self.turn().status,'failed')
        self.assertIsNone(self.state.attention.active)

    def test_unoffered_object_is_rejected_even_with_a_plausible_label(self):
        self.setup_session(brain=Brain(choice('visual:object:invented-cup')));self.publish()
        self.assertEqual(self.turn().status,'failed')
        self.assertFalse(self.state.attention.recent)

    def test_two_face_targets_remain_tracks_without_person_identity(self):
        from r3x.attention_scene import VisualTarget
        faces=(VisualTarget('a','face','visible face',(.1,.2,.1,.1)),
               VisualTarget('b','face','visible face',(.5,.2,.1,.1)))
        self.setup_session(brain=Brain(choice('visual:face:b')));self.publish(faces)
        self.assertEqual(self.turn().status,'suppressed')
        self.assertEqual(self.state.attention.active.anchor['track_id'],'b')
        self.assertFalse(self.store.people())
        self.assertFalse(self.store.rows('SELECT * FROM face_references'))

    def test_bad_frames_are_rejected_without_overwriting_good_evidence(self):
        from r3x.attention_scene import VisualTarget
        self.setup_session();original=self.publish();self.state.trace.at+=.1
        for changes in ({'session_id':'other'}, {'medium':'hardware'}, {'at':float('nan')},
                        {'coordinate_frame':'motor_degrees'}, {'at':102}, {'view_epoch':99},
                        {'targets':(VisualTarget('x','object','cup',(0.,0.,2.,.1)),)},
                        {'targets':original.targets*2}):
            with self.assertRaises(ValueError):self.publish(**changes)
        self.assertEqual(self.state.attention.scene.frame,original)

    def test_empty_post_movement_view_releases_target_without_inventing_departure(self):
        self.setup_session(brain=Brain(choice('visual:object:cup-1')));self.publish();self.turn()
        self.state.trace.at+=.1;self.publish(targets=())
        self.assertIsNone(self.state.attention.active)
        self.assertFalse(self.store.claims())

    def test_newer_frame_of_same_track_supplies_updated_location(self):
        from r3x.attention_scene import VisualTarget
        self.setup_session(brain=Brain(choice('visual:object:cup-1')));self.publish()
        original=self.brain.reply
        def refreshed(snapshot,cancel):
            self.state.trace.at+=.1
            self.publish((VisualTarget('cup-1','object','red cup',(.3,.2,.2,.2)),))
            yield from original(snapshot,cancel)
        self.brain.reply=refreshed
        self.assertEqual(self.turn().status,'suppressed')
        self.assertEqual(self.state.attention.active.anchor['box'],(.3,.2,.2,.2))
