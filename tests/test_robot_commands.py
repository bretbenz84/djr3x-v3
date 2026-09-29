"""Natural-language model choices executed by production owners; synthetic I/O only."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from r3x.capture import Endpoint, Frame
from r3x.contracts import Cancellation, Cancelled, Capture, Transcript, ToolCall
from r3x.servo_poses import NAMED_POSES, POSE_COMMANDS, pose_lanes, throttle_clear, THROTTLE
from r3x.servos import ServoOwner, ServoRequest
from r3x.robot_servos import RobotServos
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.leds import LightRequest
from tests.test_servos import Clock, PulsePort
from tests.test_leds import rig, choice
from tests import test_leds
from tests.test_memory_core import MemoryFixture, observation
from tests.test_voice_core import Brain


class CaptureMotionTests(MemoryFixture):
    def test_motion_transition_preserves_entire_utterance_and_abstains_identity(self):
        endpoint=Endpoint('s')
        parts=[]
        for i in range(20):
            part=endpoint.push(Frame(bytes([i])*1024,i*.032,(i+1)*.032,motion_risk=i<8),True)
            if part: parts.append(part)
        parts.append(endpoint.finish())
        self.assertEqual(len(parts),1)
        capture=parts[0]
        self.assertEqual(capture.pcm,b''.join(bytes([i])*1024 for i in range(20)))
        self.assertTrue(capture.motion_risk)
        self.assertFalse(capture.echo_risk)
        attribution=self.state.begin('t',capture,Transcript('Show me a happy light animation',-.01,True),observation())
        self.assertIsNone(attribution.person_id)
        self.assertIsNone(attribution.track_id)
        self.assertFalse(attribution.learning_eligible)

    def test_motion_audio_is_analyzed_and_flagged_not_discarded(self):
        # Owner ruling, September 27: motion lowers confidence; evidence is kept.
        from r3x.perception import Perception
        from r3x.speakers import VoiceObservation
        class Encoder:
            capability=SimpleNamespace(model='synthetic:3')
            def analyze(self,*args): return VoiceObservation('synthetic:3',(1.,0.,0.),(),2.,1.,reason='synthetic')
        class ASR:
            def transcribe(self,capture,cancel): return Transcript('Show me a happy light animation',-.01,True)
        capture=Capture('c','s',bytes(1024),0,1,1,motion_risk=True)
        turns=list(Perception(ASR(),Encoder(),self.state.trace).analyze_many(capture,Cancellation()))
        self.assertEqual(turns[0].transcript.text,'Show me a happy light animation')
        self.assertTrue(turns[0].voice.motion)
        self.assertEqual(turns[0].voice.embedding,(1.,0.,0.))


class PoseTests(unittest.TestCase):
    def setUp(self):
        self.port,self.clock=PulsePort(),Clock()
        self.owner=ServoOwner(lambda:self.port,medium='simulation',clock=self.clock,wait=self.clock.wait)
        self.owner.start();self.addCleanup(self.owner.close)
        self.run_pose(kind='startup')

    def run_pose(self,pose=None,kind='pose',cancel=None):
        return self.owner.execute(ServoRequest('test',self.owner.version,kind,self.clock()+120,pose=pose),cancel or Cancellation())

    def test_all_named_pose_transitions_and_park_have_valid_routes(self):
        for start in NAMED_POSES:
            for end in POSE_COMMANDS:
                with self.subTest(start=start,end=end):
                    self.assertEqual(self.run_pose(start).status,'pulses_reached')
                    self.assertEqual(self.run_pose(end).status,'pulses_reached')
        self.assertEqual(self.run_pose(kind='shutdown').status,'pulses_reached')

    def test_look_poses_move_the_head_the_requested_way(self):
        # Live September 28: "look down and to your left" raised the head (head_lift 30%)
        # and a following "look to your left" (old 4000) swung the neck back right.
        # Side and up/down are separate steps; each keeps the other axis.
        from r3x.servo_poses import DRIVE_HEAD, NECK_CENTER
        lift, tilt = dict(DRIVE_HEAD)[1], dict(DRIVE_HEAD)[2]
        self.run_pose('look_down');self.run_pose('look_left')
        self.assertLess(self.port.pose[0], NECK_CENTER)
        self.assertLess(self.port.pose[1], lift)      # Smaller lift lowers the head.
        self.assertGreater(self.port.pose[2], tilt)   # Larger tilt looks lower.
        down = (self.port.pose[1], self.port.pose[2])
        self.run_pose('look_right');self.run_pose('look_left')
        self.assertEqual((self.port.pose[1], self.port.pose[2]), down)  # Neck poses keep up/down.
        left = NECK_CENTER-self.port.pose[0]
        self.run_pose('look_right')
        self.assertEqual(left, self.port.pose[0]-NECK_CENTER)  # As far left as right.
        self.run_pose('look_up')
        self.assertGreater(self.port.pose[1], lift);self.assertLess(self.port.pose[2], tilt)
        self.run_pose('look_level')
        self.assertEqual((self.port.pose[1], self.port.pose[2]), (lift, tilt))

    def test_talking_preserves_requested_visor_and_fully_lowered_arm(self):
        self.run_pose('visor_down');self.run_pose('throttle_down')
        self.assertEqual(self.run_pose(kind='talking').status,'pulses_reached')
        for name in ('visor_down','throttle_down'):
            for ch,pulse in NAMED_POSES[name]: self.assertEqual(self.port.pose[ch],pulse)
        self.assertEqual(self.run_pose('release_poses').status,'pulses_reached')
        self.assertFalse(self.owner.held_channels)
        self.assertEqual(self.run_pose(kind='talking').status,'pulses_reached')
        self.assertNotEqual(self.port.pose[3],4544)

    def test_stale_invalid_and_cancelled_requests(self):
        before=len(self.port.targets)
        self.assertEqual(self.run_pose('invented').status,'rejected')
        token=Cancellation();token.event.set()
        self.assertEqual(self.run_pose('throttle_offer',cancel=token).status,'cancelled')
        request=ServoRequest('stale',-1,'pose',self.clock()+60,pose='throttle_offer')
        self.assertEqual(self.owner.execute(request,Cancellation()).status,'rejected')
        self.assertEqual(len(self.port.targets),before)


class PoseSessionTests(MemoryFixture):
    def test_hand_request_reaches_controller_before_acknowledgement(self):
        clock=Clock();port=PulsePort()
        robot=RobotServos('fake',open_port=lambda:port,clock=clock,wait=clock.wait)
        robot.owner.wait=clock.wait
        robot.start();self.addCleanup(robot.close)
        brain=Brain(ToolCall('pose','set_robot_pose',{'poses':['throttle_offer'],'look_after':None,'text':'Here is my hand.'}))
        class Sink(SyntheticSink):
            def play(inner,*args):
                for ch,value in NAMED_POSES['throttle_offer']:
                    self.assertEqual(port.pose[ch],value)
                return super().play(*args)
        output=OutputOwner(SyntheticTTS(),Sink(),self.state.trace,robot=robot)
        session=Session('s',brain,output,self.state.trace,self.state);self.addCleanup(session.close)
        result=session.process_turn(Capture('c','s',bytes(1024),98,99,99.6),Transcript('Hold out your hand',-.01,True),session.revision)
        self.assertEqual(result.status,'completed')
        self.assertEqual(brain.calls,1)
        self.assertTrue(any(r['event']=='robot_pose_result' and r['status']=='pulses_reached' for r in self.state.trace.rows))


class BlinkTests(unittest.TestCase):
    def test_three_eye_blinks_restore_color_without_chest_or_mouth_commands(self):
        led,ports,clock=rig();self.addCleanup(led.close)
        led.begin('happy');led.finish(led.version)
        head=len(ports['head'].commands);chest=len(ports['chest'].commands)
        result=led.blink(LightRequest('b','blink',1,clock()+5,led.version),Cancellation(),lambda:True)
        self.assertEqual(result,'commands_sent')
        commands=ports['head'].commands[head:]
        self.assertEqual(commands.count('EYE:0,0,0'),3)
        self.assertEqual(commands[-1],'EYE:0,255,100')
        self.assertTrue(all(c.startswith('EYE:') for c in commands))
        self.assertEqual(len(ports['chest'].commands),chest)

    def test_shutdown_during_blink_never_relit(self):
        led,ports,clock=rig();self.addCleanup(led.close)
        def wait(n): clock.wait(n);led.cancel()
        led.wait=wait
        with self.assertRaises(Exception):
            led.blink(LightRequest('b','blink',1,clock()+5,led.version),Cancellation(),lambda:True)
        self.assertEqual(ports['head'].commands[-1],'FADEOFF')
        led.wait=clock.wait


class BlinkSessionTests(MemoryFixture):
    setup_session = test_leds.LEDSessionTests.setup_session
    turn = test_leds.LEDSessionTests.turn
    def test_blink_request_uses_production_output(self):
        self.setup_session(choice('before_speech',None,'blink'))
        result=self.turn()
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(result.lights[0]['status'],'commands_sent')
        self.assertEqual(self.ports['head'].commands.count('EYE:0,0,0'),3)

    def test_blink_cancellation_restores_eyes_and_reports_interruption(self):
        self.setup_session(choice('before_speech',None,'blink'))
        def interrupt(n): self.session.cancel()
        self.led.wait=interrupt
        result=self.turn()
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(result.lights[0]['status'],'cancelled')
        self.assertEqual(self.ports['head'].commands[-1],'EYE:255,200,0')
        self.led.wait=lambda n:None


class PoseRejectionTests(MemoryFixture):
    def test_unavailable_pose_not_offered_or_executed(self):
        from r3x.semantics import grounded_tools
        self.assertNotIn('set_robot_pose',{t['name'] for t in grounded_tools({'source_turn_id':'t'})})
        call=ToolCall('p','set_robot_pose',{'poses':['throttle_offer'],'look_after':None,'text':'Here is my hand.'})
        with self.assertRaises(ValueError):
            self.state.propose_pose(call,{'state_revision':self.store.revision},Cancellation())

    def test_failed_motion_cannot_speak_success_text(self):
        from r3x.servos import ServoReceipt
        robot=SimpleNamespace(owner=SimpleNamespace(state='active'),
            execute_pose=lambda *a:ServoReceipt('p','failed',1,100,'simulation',reason='ServoArrivalTimeout'))
        brain=Brain(ToolCall('p','set_robot_pose',{'poses':['throttle_offer'],'look_after':None,'text':'Here is my hand.'}))
        output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace,robot=robot)
        session=Session('s',brain,output,self.state.trace,self.state);self.addCleanup(session.close)
        result=session.process_turn(Capture('c','s',bytes(1024),98,99,99.6),Transcript('Hold out your hand',-.01,True),session.revision)
        self.assertNotIn('Here is my hand.',result.completed_text)
        self.assertEqual(result.completed_text,["I couldn't complete that movement."])


class HeldPoseContinuationTests(MemoryFixture):
    def test_resume_after_other_turn_releases_hold_before_reply_and_talking(self):
        clock,port=Clock(),PulsePort()
        robot=RobotServos('fake',open_port=lambda:port,clock=clock,wait=clock.wait)
        robot.owner.wait=clock.wait
        robot.start();self.addCleanup(robot.close)
        brain=Brain(ToolCall('offer','set_robot_pose',{'poses':['throttle_offer'],'look_after':None,'text':None}))
        output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace,robot=robot)
        session=Session('s',brain,output,self.state.trace,self.state);self.addCleanup(session.close)
        def turn(index,text):
            return session.process_turn(Capture(str(index),'s',bytes(1024),98+index*10,99+index*10,99.6+index*10),
                Transcript(text,-.01,True),session.revision)
        self.assertEqual(turn(0,'Hold out your hand').status,'suppressed')
        self.assertEqual(robot.owner.held_channels,{8,9,10})
        brain.text='Still here.'
        turn(1,'Thank you.')
        self.assertEqual(brain.snapshots[-1].context['robot_pose_state']['held_parts'],['throttle_arm'])
        self.assertEqual(robot.owner.held_channels,{8,9,10})
        brain.text=ToolCall('resume','set_robot_pose',{'poses':['release_poses'],'look_after':None,'text':'Back to normal.'})
        class Sink(SyntheticSink):
            def play(inner,*args):
                self.assertFalse(robot.owner.held_channels)
                return super().play(*args)
        output.sink=Sink()
        result=turn(2,'You can resume normal motion now.')
        self.assertEqual(result.status,'completed')
        self.assertFalse(robot.owner.held_channels)
        before={ch:port.pose[ch] for ch in THROTTLE}
        receipt=robot.owner.execute(ServoRequest('talk',robot.owner.version,'talking',clock()+60),Cancellation())
        self.assertEqual(receipt.status,'pulses_reached')
        self.assertNotEqual(before,{ch:port.pose[ch] for ch in THROTTLE})
        brain.text='Moving normally.'
        turn(3,'Hello again.')
        self.assertEqual(brain.snapshots[-1].context['robot_pose_state']['held_parts'],[])
