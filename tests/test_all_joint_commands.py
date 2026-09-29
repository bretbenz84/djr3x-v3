"""Production joint/tool contracts with fake serial, PCM and scripted model choices."""
from r3x.contracts import Cancellation, Capture, Transcript, ToolCall
from r3x.servo_poses import JOINTS, LIMITS, THROTTLE, joint_targets
from r3x.servos import ServoRequest
from r3x.robot_servos import RobotServos
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.semantics import grounded_tools, validate
from tests.test_servos import Clock, PulsePort
from tests.test_memory_core import MemoryFixture
from tests.test_voice_core import Brain


def positions(**values):
    return {name: values.get(name) for name in JOINTS}


class JointCommands(MemoryFixture):
    def setup_robot(self, call):
        self.clock, self.port = Clock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda:self.port, clock=self.clock, wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait
        self.robot.start()
        self.addCleanup(self.robot.close)
        self.brain = Brain(call)
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, robot=self.robot)
        self.session = Session('s', self.brain, self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)

    def turn(self, text):
        return self.session.process_turn(Capture('c','s',bytes(1024),98,99,99.6),
            Transcript(text,-.01,True), self.session.revision)

    def execute(self, kind, pose=None):
        owner = self.robot.owner
        return owner.execute(ServoRequest('test',owner.version,kind,self.clock()+120,pose=pose),Cancellation())

    def test_head_command_moves_before_acknowledgment_and_holds_through_speech(self):
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_right'],'look_after':None,'text':'Looking right.'}))
        class Sink(SyntheticSink):
            def play(inner,*args):
                self.assertEqual(self.port.pose[0],8000)
                return super().play(*args)
        self.output.sink=Sink()
        self.assertEqual(self.turn('Look to your right.').status,'completed')
        self.assertEqual(self.execute('talking').status,'pulses_reached')
        self.assertEqual(self.port.pose[0],8000)
        self.assertEqual(self.brain.calls,1)

    def test_all_eleven_servos_execute_and_hold_across_idle_talking_and_microphone_refresh(self):
        requested=positions(**{name:50 for name in JOINTS})
        self.setup_robot(ToolCall('j','set_robot_joints',{'positions':requested,'text':None}))
        self.assertEqual(self.turn('Move every joint to the middle of its range.').status,'suppressed')
        expected=joint_targets(requested)
        self.assertEqual(self.port.pose,expected)
        for kind in ('talking','idle'):
            self.assertEqual(self.execute(kind).status,'pulses_reached')
            self.assertEqual(self.port.pose,expected)
        self.clock.wait(40)
        self.robot.owner.refresh_microphone_hold()
        self.assertEqual(self.port.pose,expected)
        self.assertFalse(self.robot.owner.microphone_held_during(self.clock(),self.clock()+1))
        self.brain.text='Still holding.'
        self.turn('Thanks.')
        self.assertIn('hero_arm',self.brain.snapshots[-1].context['robot_pose_state']['held_parts'])
        self.assertEqual(self.execute('pose','release_poses').status,'pulses_reached')
        self.assertFalse(self.robot.owner.held_channels)
        self.clock.wait(2)
        self.robot.owner.refresh_microphone_hold()
        self.clock.wait(2)
        self.assertTrue(self.robot.owner.microphone_held_during(self.clock(),self.clock()+1))

    def test_partial_throttle_request_is_batched_and_unsafe_route_writes_nothing(self):
        self.setup_robot(ToolCall('j','set_robot_joints',{'positions':positions(throttle_wrist=40),'text':None}))
        before={ch:self.port.pose[ch] for ch in THROTTLE}
        self.assertEqual(self.turn('Move your throttle wrist.').status,'suppressed')
        self.assertEqual(self.port.pose[8],before[8]);self.assertEqual(self.port.pose[9],before[9])
        self.assertEqual(self.robot.owner.held_channels,set(THROTTLE))
        count=len(self.port.targets)
        unsafe={0:8000,8:9088,9:2048,10:9984}
        self.assertEqual(self.execute('pose',unsafe).status,'rejected')
        self.assertEqual(len(self.port.targets),count)

    def test_joint_validation_rejects_invalid_values_and_unavailable_capability(self):
        for value in (-1,101,float('nan'),float('inf'),True,'50'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate('set_robot_joints',{'positions':positions(neck=value),'text':None})
        with self.assertRaises(ValueError): joint_targets(positions())
        with self.assertRaises(ValueError): joint_targets({'neck':50})
        self.assertNotIn('set_robot_joints',{x['name'] for x in grounded_tools({'source_turn_id':'t'})})
        call=ToolCall('j','set_robot_joints',{'positions':positions(visor=0),'text':None})
        with self.assertRaises(ValueError):
            self.state.propose_pose(call,{'state_revision':self.store.revision},Cancellation())

    def test_joint_cancellation_and_shutdown(self):
        self.setup_robot(ToolCall('j','set_robot_joints',{'positions':positions(visor=0,hero_arm=25),'text':None}))
        self.turn('Lower your visor and hold your hero arm.')
        self.assertEqual(self.port.pose[3],LIMITS[3][0])
        owner=self.robot.owner
        cancelled=Cancellation();cancelled.event.set()
        before=len(self.port.targets)
        receipt=owner.execute(ServoRequest('c',owner.version,'pose',self.clock()+60,pose={0:8000}),cancelled)
        self.assertEqual(receipt.status,'cancelled');self.assertEqual(len(self.port.targets),before)
        self.robot.close()
        self.assertTrue(self.port.closed)
        self.assertEqual(self.port.pose[0],5472)


class ChainedPoseCommands(MemoryFixture):
    """Live September 28: "look to your right and then down" moved only the neck, and
    "look down and tell me what you see" answered from the old view. The model chains
    steps from the wording; each step keeps the other head axis."""
    setup_robot, turn = JointCommands.setup_robot, JointCommands.turn

    def rows(self, event):
        return [r for r in self.state.trace.rows if r['event'] == event]

    def test_spoken_sequence_runs_every_step_in_order_before_the_acknowledgment(self):
        from r3x.servo_poses import HEAD_DOWN, NECK_LEFT, NECK_RIGHT
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_right','look_left','look_up','look_down'],
                                                        'look_after':None,'text':'Looking around.'}))
        result = self.turn('Look right, then left, then up, then down.')
        self.assertEqual(result.completed_text, ['Looking around.'])
        self.assertEqual([r['pose'] for r in self.rows('robot_pose_result')], ['look_right','look_left','look_up','look_down'])
        neck = [p[2]+128*p[3] for p in self.port.targets if p[:2]==bytes((0x84,0))]
        self.assertLess(neck.index(NECK_RIGHT), neck.index(NECK_LEFT))
        self.assertEqual(self.port.pose[0], NECK_LEFT)            # The later up/down kept the side.
        self.assertEqual({ch:self.port.pose[ch] for ch,_ in HEAD_DOWN}, dict(HEAD_DOWN))
        events = [r['event'] for r in self.state.trace.rows]
        self.assertLess(max(i for i,e in enumerate(events) if e=='robot_pose_result'), events.index('phrase_completed'))

    def test_unknown_step_rejects_the_whole_sequence_before_motion(self):
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_right','somersault'],'look_after':None,'text':'Done.'}))
        before = len(self.port.targets)
        self.assertNotEqual(self.turn('Look right and do a somersault.').completed_text, ['Done.'])
        self.assertFalse(self.rows('robot_pose_result'))
        self.assertEqual(len(self.port.targets), before)

    def test_wave_sweeps_the_hand_and_returns_the_arm_without_holding_it(self):
        from r3x.servo_poses import LIMITS
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['wave'],'look_after':None,'text':'Hello there!'}))
        start = {ch:self.port.pose[ch] for ch in (4,5,7)}
        self.assertEqual(self.turn('Can you wave your hand?').completed_text, ['Hello there!'])
        wrist = [p[2]+128*p[3] for p in self.port.targets if p[:2]==bytes((0x84,5))]
        self.assertGreaterEqual(wrist.count(LIMITS[5][1]), 4)
        self.assertGreaterEqual(wrist.count(LIMITS[5][0]), 4)
        self.assertIn(4800, [p[2]+128*p[3] for p in self.port.targets if p[:2]==bytes((0x84,7))])
        self.assertEqual({ch:self.port.pose[ch] for ch in (4,5,7)}, start)
        self.assertFalse(self.robot.owner.held_channels & {4,5,7})

    def test_look_after_answers_from_a_frame_taken_after_the_head_moved(self):
        from r3x.inspection import CurrentView
        from tests.test_drive_session import DriveBrain, FakeScene
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_down'],'look_after':'What is on the floor?','text':None}))
        self.brain = self.session.brain = DriveBrain(self.brain.text, 'A dog toy and a sock.')
        view = CurrentView(FakeScene(self.clock), clock=self.clock); view.start()
        self.output.inspection = view
        self.state.trace.clock = self.clock
        result = self.turn('Look down and tell me what you see.')
        self.assertEqual(result.completed_text, ['A dog toy and a sock.'])
        followup = self.brain.snapshots[-1]
        self.assertTrue(followup.images)
        self.assertGreaterEqual(followup.context['inspection']['captured_at'], self.rows('robot_pose_result')[-1]['at'])
