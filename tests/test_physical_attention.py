"""Synthetic pulse transport and images through production owners. No hardware."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from r3x.contracts import Cancellation, Cancelled, ProviderFailure, Capture, Transcript, ToolCall
from r3x.physical_attention import PhysicalAttention, PhysicalGesture
from r3x.robot_servos import RobotServos
from r3x.servos import ServoRequest
from r3x.attention import AttentionIntent
from r3x.performance import Gesture
from r3x.scene import SceneObserver
from r3x import visual_memory
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.session import Session
from r3x.semantics import grounded_tools
from tests import test_servos, test_attention, test_performance, test_memory_core, test_observations, test_voice_core


def robot_fixture(clock, wait):
    port=test_servos.PulsePort()
    robot=RobotServos('injected-only',open_port=lambda:port,clock=clock,wait=wait)
    robot.owner.wait=wait
    robot.owner.start()
    result=robot.owner.execute(ServoRequest('startup',0,'startup',clock()+120),Cancellation())
    assert result.status=='pulses_reached'
    return robot,port


class PhysicalMotionTests(unittest.TestCase):
    def setUp(self):
        self.clock=test_servos.Clock()
        self.robot,self.port=robot_fixture(self.clock,self.clock.wait)
        self.addCleanup(self.robot.owner.close)
        self.adapter=PhysicalAttention(self.robot);self.adapter.start()

    def intent(self,target='left'):
        return AttentionIntent('look',0,target,'consider','An unresolved curiosity',self.clock(),self.clock()+15)

    def test_finite_look_preempts_idle_and_reflex_without_holding_pose_or_moving_mic(self):
        sequence=[]
        self.robot.idle.stop=lambda:sequence.append('idle')
        self.robot.reflex.stop=lambda:sequence.append('reflex')
        self.robot.talking.stop=lambda:sequence.append('talking')
        self.robot.owner.on_camera_motion=lambda moving,*_:sequence.append(moving)
        original=self.port.pose.copy()
        result=self.adapter.execute(self.intent(),Cancellation())
        self.assertEqual((result.outcome,result.medium),('pulses_reached','controller'))
        self.assertEqual(sequence,['idle','reflex','talking',True,False])
        self.assertLess(self.port.pose[0],original[0])
        self.assertEqual({ch:v for ch,v in self.port.pose.items() if ch!=0},{ch:v for ch,v in original.items() if ch!=0})
        self.assertFalse(self.robot.owner.held_channels)
        self.assertFalse(self.robot.owner.camera_moving)
        self.assertEqual(self.robot.owner.state,'active')

    def test_held_pose_and_drive_stance_reject_before_any_targets(self):
        for held in ({0},{0,1,2,4,6,8,9,10}):
            self.robot.owner.held_channels=held.copy()
            before=len(self.port.targets)
            with self.assertRaisesRegex(ProviderFailure,'ExpressionConflictsWithHeldPose'):
                self.adapter.execute(self.intent(),Cancellation())
            self.assertEqual(len(self.port.targets),before)
            self.assertEqual(self.robot.owner.held_channels,held)

    def test_gestures_return_to_start_and_do_not_touch_microphone(self):
        adapter=PhysicalGesture(self.robot);adapter.start()
        from r3x.servo_poses import GESTURE_PRIMITIVES, head_pose
        self.assertEqual(set(GESTURE_PRIMITIVES), {'nod','tilt','perplexed','wiggle','sympathetic','surprised','lift','arm_emphasis'})
        for primitive in GESTURE_PRIMITIVES:
            for intensity in ('subtle','normal'):
                before=self.port.pose.copy();self.port.packets.clear()
                receipt=adapter.execute(Gesture('g',primitive,'unfocused',intensity,self.clock()+30),Cancellation())
                self.assertEqual(receipt.status,'pulses_reached')
                self.assertEqual(self.port.pose,before)
                self.assertFalse(self.robot.owner.held_channels)
                sent=[(p[1],p[2]+128*p[3]) for p in self.port.targets]
                if primitive not in ('lift','arm_emphasis'):
                    # Both head servos move together; a tilt cocks the head (both pulses the
                    # same way, here lower = to the right), a nod pitches it (against each other).
                    scale=1. if intensity=='normal' else .5
                    head=[(ch,value) for ch,value in sent if ch in (2,11)]
                    pairs=[{ch:value for ch,value in head[i:i+2]} for i in range(0,len(head),2)]
                    self.assertTrue(all(set(p)=={2,11} for p in pairs))
                    poses=[head_pose(p) for p in pairs[:-1]]      # The last pair is the return.
                    first=pairs[0];pitch,tilt=poses[0]
                    if primitive=='tilt':
                        self.assertLess(first[2],before[2]);self.assertLess(first[11],before[11])
                        self.assertAlmostEqual(tilt,.6*scale,delta=.03)
                    elif primitive=='nod':
                        self.assertLess(first[2],before[2]);self.assertGreater(first[11],before[11])
                        self.assertAlmostEqual(pitch,-.3*scale,delta=.03)
                    elif primitive=='perplexed':   # The dog's double cock: one side, then the other, slightly down.
                        self.assertEqual([round(t/abs(t)) for _,t in poses],[1,-1])
                        self.assertTrue(all(abs(abs(t)-.6*scale)<.03 and -.1*scale<p<0 for p,t in poses))
                    elif primitive=='wiggle':      # Alternation, level.
                        self.assertEqual([round(t/abs(t)) for _,t in poses],[1,-1,1])
                        self.assertTrue(all(abs(p)<.02 for p,_ in poses))
                    elif primitive=='surprised':   # The head lifts a little while the visor flies open and returns.
                        self.assertAlmostEqual(pitch,.15*scale,delta=.03);self.assertAlmostEqual(tilt,0.,delta=.03)
                        visor=[value for ch,value in sent if ch==3]
                        self.assertEqual(visor[-1],before[3])
                        self.assertEqual(visor[0],round(before[3]+(4544-before[3])*scale))
                    elif primitive=='sympathetic': # One slow cock, slightly down, held.
                        self.assertEqual(len(poses),1)
                        self.assertAlmostEqual(tilt,.45*scale,delta=.03);self.assertAlmostEqual(pitch,-.15*scale,delta=.03)
                        self.assertEqual([p[2]+128*p[3] for p in self.port.packets if p[0]==0x87 and p[1]==2][0],12)

    def test_the_wiggle_eases_in_and_out_instead_of_snapping(self):
        # Owner, October 24: the side-to-side wiggle was too rapid and creaked (board acceleration
        # 10 on both head servos, three reversals in about 1.5 s).
        from r3x.servo_poses import expressive_lanes
        adapter=PhysicalGesture(self.robot);adapter.start()
        before=self.port.pose.copy();self.port.packets.clear()
        (lane,)=expressive_lanes(before,'gesture','unfocused','wiggle','normal',.5,frozenset())
        self.assertGreater(sum(b.seconds+b.dwell for b in lane),2.5)   # About twice as long as before.
        receipt=adapter.execute(Gesture('g','wiggle','unfocused','normal',self.clock()+30),Cancellation())
        self.assertEqual(receipt.status,'pulses_reached')
        self.assertEqual(self.port.pose,before)
        ramps=[p[2]+128*p[3] for p in self.port.packets if p[0]==0x89 and p[1] in (2,11)]
        speeds=[p[2]+128*p[3] for p in self.port.packets if p[0]==0x87 and p[1] in (2,11)]
        self.assertEqual(len(ramps),8)                  # Four swings, both head servos each time.
        self.assertTrue(all(1<=a<=3 for a in ramps))    # A gentle ramp, never the old 10.
        self.assertTrue(all(s<=12 for s in speeds))     # Under half the old peak speed.

    def test_stale_closed_uncalibrated_and_cancelled_actions_cannot_succeed(self):
        before=len(self.port.targets)
        with self.assertRaises(ProviderFailure):self.adapter.execute(replace(self.intent(),expires_at=self.clock()),Cancellation())
        with self.assertRaises(ProviderFailure):self.adapter.execute(replace(self.intent(),anchor={'track_id':'cup'}),Cancellation())
        self.adapter.close()
        with self.assertRaises(ProviderFailure):self.adapter.execute(self.intent(),Cancellation())
        self.assertEqual(len(self.port.targets),before)
        self.adapter.start();token=Cancellation();events=[]
        self.robot.owner.on_camera_motion=lambda moving,*_:events.append(moving)
        self.port.on_target=lambda:token.event.set()
        with self.assertRaises(Cancelled):self.adapter.execute(self.intent(),token)
        self.assertEqual(events,[True,False]);self.assertFalse(self.robot.owner.camera_moving)
        self.assertEqual(self.robot.owner.state,'active')

    def test_stall_releases_camera_motion_and_returns_failure(self):
        self.port.stall=True;events=[]
        self.robot.owner.on_camera_motion=lambda moving,*_:events.append(moving)
        with self.assertRaisesRegex(ProviderFailure,'ServoRequestExpired'):
            self.adapter.execute(self.intent(),Cancellation())
        self.assertEqual(events,[True,False]);self.assertFalse(self.robot.owner.camera_moving)
        # October 15: a stalled look is held where it stopped; later moves are not all refused.
        self.assertEqual(self.robot.owner.state,'active')


class PhysicalSessionTests(test_memory_core.MemoryFixture):
    turn=test_attention.AttentionTests.turn

    def setup_physical(self,choice=None):
        trace=self.state.trace
        def wait(seconds):trace.at+=seconds
        robot,self.port=robot_fixture(trace.clock,wait)
        self.addCleanup(robot.owner.close)
        self.robot=robot
        attention=PhysicalAttention(robot);attention.start()
        gestures=PhysicalGesture(robot);gestures.start()
        self.brain=test_voice_core.Brain(choice or test_attention.choice())
        self.output=OutputOwner(SyntheticTTS(),SyntheticSink(),trace,attention=attention,gestures=gestures)
        self.session=Session('session',self.brain,self.output,trace,self.state)
        self.addCleanup(self.session.close)

    def test_silent_physical_choice_one_call_no_face_no_false_speech(self):
        self.setup_physical()
        result=self.turn()
        self.assertEqual((result.status,result.completed_text),('suppressed',[]))
        self.assertEqual(self.brain.calls,1)
        self.assertEqual(self.state.attention.recent[-1]['outcome'],'pulses_reached')
        self.assertEqual(self.brain.snapshots[0].context['attention']['mode'],'controller')
        self.assertFalse(self.store.claims())
        self.assertIn('pulses_reached',str(self.session.history))

    def test_before_and_after_gestures_keep_receipts_separate_from_audio(self):
        self.setup_physical()
        for timing in ('with_speech','after_speech'):
            call=test_performance.choice(timing);call.arguments['target']='unfocused';self.brain.text=call
            self.state.trace.rows.clear()
            result=self.turn()
            self.assertEqual(result.status,'completed')
            self.assertEqual((result.gestures[0]['status'],result.gestures[0]['medium']),('pulses_reached','controller'))
            events=[r['event'] for r in self.state.trace.rows]
            if timing=='with_speech':self.assertLess(events.index('speech_generated'),events.index('gesture_delivery'))
            else:self.assertLess(events.index('phrase_completed'),events.index('gesture_delivery'))

    def test_human_interrupts_silent_look_without_committing_or_rearming(self):
        self.setup_physical()
        self.port.on_target=lambda:self.session.note_speech(self.state.trace.clock()+1)
        result=self.turn()
        self.assertEqual(result.status,'interrupted')
        self.assertIsNone(self.state.attention.active)
        self.assertFalse(self.robot.owner.camera_moving)

    def test_human_priority_still_cancels_gesture_after_speech_was_heard(self):
        self.setup_physical()
        call=test_performance.choice('after_speech');call.arguments['target']='unfocused';self.brain.text=call
        self.port.on_target=lambda:self.session.note_speech(self.state.trace.clock()+1)
        result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertTrue(result.completed_text)
        self.assertEqual(result.gestures[0]['status'],'cancelled')
        self.assertFalse(self.robot.owner.camera_moving)
        self.assertEqual(self.robot.owner.state,'active')

    def test_look_inspect_and_remember_share_executors_without_speech(self):
        from tests.test_agency import next_request
        from tests.test_proactive import event
        from r3x.inspection import CurrentView
        self.setup_physical()
        trace=self.state.trace
        scene=SceneObserver(SimpleNamespace(session_id='session'),clock=trace.clock)
        self.addCleanup(scene.close)
        self.state.scene_observer=scene
        self.robot.owner.on_camera_motion=lambda moving,request,kind:scene.note_motion(moving,request,'head:'+kind)
        def look(snapshot,cancel):
            self.brain.snapshots.append(snapshot)
            call=test_attention.choice()
            call.arguments['next_opportunity']=next_request(snapshot.context)
            yield call
        self.brain.reply=look
        self.assertEqual(self.turn().status,'suppressed')
        trace.at += 8
        scene.observe(test_observations.frame(trace.at))
        self.output.inspection=CurrentView(scene,clock=trace.clock);self.output.inspection.start()
        def inspect(snapshot,cancel):
            self.brain.snapshots.append(snapshot)
            if not snapshot.continuation:
                yield ToolCall('inspect','inspect_view',{'direction':'current','question':'Is this view worth pursuing?'})
            else:
                yield ToolCall('describe','describe_view',{'observation_id':snapshot.context['inspection']['request_id'],
                    'description':'An unchanged synthetic gray view.', 'uncertainty':'No object identity established.',
                    'description_scope':'general','text':None})
        self.brain.reply=inspect
        opportunity=replace(event(at=trace.at),kind='reconsideration',faces=(),target_track=None)
        result=self.session.process_opportunity(opportunity,self.session.revision,lambda:True)
        self.assertEqual(result.status,'suppressed')
        self.assertFalse(result.completed_text)
        self.assertEqual(len(self.brain.snapshots),3)  # Look, inspection, bounded description continuation.
        rows=visual_memory.context(self.store,'session','[]')
        self.assertEqual(len(rows),1);self.assertFalse(rows[0]['spoken'])
        self.assertFalse(any(r['event']=='speech_generated' for r in trace.rows))

    def test_unoffered_gesture_direction_cannot_move(self):
        self.setup_physical(test_performance.choice())
        before=len(self.port.targets)
        result=self.turn()
        self.assertEqual(result.status,'failed')
        self.assertEqual(len(self.port.targets),before)
        tools=grounded_tools(self.brain.snapshots[0].context)
        gesture=next(t for t in tools if t['name']=='respond_with_gesture')
        self.assertEqual(gesture['parameters']['properties']['target']['enum'],['unfocused'])


class ViewContinuityTests(unittest.TestCase):
    def setUp(self):
        self.now=100.
        self.scene=SceneObserver(SimpleNamespace(session_id='session'),clock=lambda:self.now)
        self.addCleanup(self.scene.close)

    def view(self,red=False):
        self.scene.observe(test_observations.frame(self.now,red))
        return self.scene.snapshot(True)

    def move(self):
        self.scene.note_motion(True,'look','head:attention')
        self.assertIsNone(self.view(True))
        self.scene.note_motion(False,'look','head:attention')
        self.assertIsNone(self.view(True))
        self.now+=1

    def test_turn_away_and_back_does_not_reoffer_but_real_change_does(self):
        first=self.view()[0];self.scene.attempted(first)
        self.move();self.now+=16
        other=self.view(True)[0];self.assertNotEqual(first,other)
        self.assertIsNotNone(self.scene.snapshot());self.scene.attempted(other)
        self.move();self.now+=16
        self.assertEqual(self.view()[0],first)
        self.assertIsNone(self.scene.snapshot())
        self.assertEqual(self.scene.continuity()['relation'],'revisited_similar_view')
        # A deliberate inspection can still retrieve the settled familiar view.
        from r3x.inspection import CurrentView,InspectionRequest
        inspection=CurrentView(self.scene,clock=lambda:self.now);inspection.start()
        data,images=inspection.execute(InspectionRequest('inspect','current','Has it changed?',0,self.now),Cancellation(),lambda:True)
        self.assertEqual(data['status'],'observed');self.assertTrue(images)
        changed=replace(test_observations.frame(self.now),pixels=bytes([120])*(48*27*3))
        # Fixture is 48x27 BGR; a third different view is eligible.
        self.scene.observe(changed)
        self.assertIsNotNone(self.scene.snapshot())

    def test_overlap_and_old_frames_cannot_pass_as_settled(self):
        self.view();old=test_observations.frame(self.now)
        self.scene.note_motion(True,'h','head:pose');self.scene.note_motion(True,'b','base')
        self.scene.note_motion(False,'h','head:pose');self.now+=2
        self.assertIsNone(self.view())
        self.scene.note_motion(False,'b','base');self.now+=2
        self.scene.observe(old);self.assertIsNone(self.scene.snapshot(True))
        self.assertIsNotNone(self.view())
        self.assertEqual(len(self.scene.images),1)

    def test_deliberate_live_capture_waits_for_head_settling_before_encoding(self):
        from unittest.mock import patch
        from r3x.inspection import CurrentView,InspectionRequest
        from r3x.scene import SceneImage
        self.scene.note_motion(True,'h','head:attention')
        camera=SimpleNamespace(hub=SimpleNamespace(peek=lambda:test_observations.frame(self.now)))
        token=Cancellation()
        def wait(seconds):
            self.now+=seconds
            if self.scene.moving:self.scene.note_motion(False,'h','head:attention')
        token.event.wait=wait
        inspection=CurrentView(self.scene,clock=lambda:self.now,camera=camera);inspection.start()
        def encode(frame):
            self.assertTrue(self.scene.frame_allowed(frame.at))
            return SceneImage(frame.frame_id,frame.at,'synthetic')
        with patch('r3x.looking.sharp_image',side_effect=encode) as encoder:
            data,images=inspection.execute(InspectionRequest('i','current','Inspect silently',0,100),token,lambda:True)
        self.assertEqual(data['status'],'observed');self.assertTrue(images)
        self.assertEqual(encoder.call_count,1)
        self.assertGreater(images[0].at,self.scene.settled_after)
        self.assertEqual(data['camera_continuity']['last_commanded_motion']['source'],'head:attention')
        token.event.set()
        with self.assertRaises(Cancelled):
            inspection.execute(InspectionRequest('cancelled','current','Inspect',0,self.now),token,lambda:True)

    def test_memory_and_attempts_are_bounded(self):
        for n in range(40):
            self.scene.attempted('scene:'+str(n))
        self.assertEqual(len(self.scene.attempts),32)
        self.assertEqual(self.scene.views.maxlen,32)


class GeneralVisualMemoryTests(test_memory_core.MemoryFixture):
    def test_general_description_survives_view_audience_changes_without_private_words(self):
        source={'request_id':'o','status':'observed','stage':'complete','frame_id':'f','question':'private question'}
        args={'observation_id':'o','description':'A red cup on a desk','uncertainty':'Identity unknown', 'description_scope':'general'}
        visual_memory.save(self.store,'session','["a"]',source,args,'synthetic',100)
        visual_memory.delivered(self.store,'o','t','Private contextual reply',101,'["a"]')
        rows=visual_memory.context(self.store,'session','[]')
        self.assertEqual(len(rows),1);self.assertNotIn('question',rows[0]['source'])
        self.assertEqual(rows[0]['spoken'],[])
        self.assertTrue(visual_memory.novelty(rows)['observations'][0]['has_heard_remark'])
        self.assertTrue(visual_memory.context(self.store,'session','["a"]')[0]['spoken'])
        visual_memory.delivered(self.store,'o','t2','What an unusual cup.',102,'["a"]',general=True)
        self.assertEqual([r['text'] for r in visual_memory.context(self.store,'session','[]')[0]['spoken']],['What an unusual cup.'])
        self.assertEqual(visual_memory.context(self.store,'restart','[]'),[])
        self.assertEqual(len(visual_memory.correction_candidates(self.store,'session','[]')),1)
        args={**args,'observation_id':'private','description_scope':'audience'}
        visual_memory.save(self.store,'session','["a"]',{**source,'request_id':'private'},args,'synthetic',102)
        self.assertEqual(len(visual_memory.context(self.store,'session','[]')),1)
