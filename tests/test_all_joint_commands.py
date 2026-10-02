"""Production joint/tool contracts with fake serial, PCM and scripted model choices."""
import unittest
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
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_right'],'look_after':None,'say_before':None,'text':'Looking right.'}))
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
                                                        'look_after':None,'say_before':None,'text':'Looking around.'}))
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
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_right','somersault'],'look_after':None,'say_before':None,'text':'Done.'}))
        before = len(self.port.targets)
        self.assertNotEqual(self.turn('Look right and do a somersault.').completed_text, ['Done.'])
        self.assertFalse(self.rows('robot_pose_result'))
        self.assertEqual(len(self.port.targets), before)

    def test_wave_sweeps_the_hand_and_returns_the_arm_without_holding_it(self):
        from r3x.servo_poses import LIMITS
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['wave'],'look_after':None,'say_before':None,'text':'Hello there!'}))
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
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_down'],'look_after':'What is on the floor?','say_before':None,'text':None}))
        self.brain = self.session.brain = DriveBrain(self.brain.text, 'A dog toy and a sock.')
        view = CurrentView(FakeScene(self.clock), clock=self.clock); view.start()
        self.output.inspection = view
        self.state.trace.clock = self.clock
        result = self.turn('Look down and tell me what you see.')
        self.assertEqual(result.completed_text, ['A dog toy and a sock.'])
        followup = self.brain.snapshots[-1]
        self.assertTrue(followup.images)
        self.assertGreaterEqual(followup.context['inspection']['captured_at'], self.rows('robot_pose_result')[-1]['at'])

    def test_look_after_that_saves_what_it_saw_still_speaks(self):
        # Hosted probe October 2: saving the description changed the store revision the pose
        # guard checked, so the comment about what Rex saw was cancelled unheard.
        from r3x.inspection import CurrentView
        from tests.test_drive_session import FakeScene
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['look_down'],'look_after':'What is on the floor?','say_before':None,'text':None}))
        first=self.brain.text
        class Describe:
            snapshots=[]
            def reply(inner,snapshot,cancel):
                inner.snapshots.append(snapshot)
                yield (ToolCall('d','describe_view',{'observation_id':snapshot.context['inspection']['request_id'],
                    'description':'A sock on the floor.','uncertainty':'Low-resolution frame.',
                    'description_scope':'general','text':'Someone has abandoned a sock. I have questions.'})
                    if snapshot.continuation else first)
        self.brain = self.session.brain = Describe()
        view = CurrentView(FakeScene(self.clock), clock=self.clock); view.start()
        self.output.inspection = view
        self.state.trace.clock = self.clock
        result = self.turn('Look down and tell me what you see.')
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.completed_text, ['Someone has abandoned a sock. I have questions.'])

    def test_mixed_head_arm_and_light_steps_run_in_the_order_asked(self):
        # "Look left then right then up then down then hold your hand out and blink your eyes."
        from r3x.servo_poses import HEAD_DOWN, NAMED_POSES
        from tests.test_leds import rig
        steps = ['look_left','look_right','look_up','look_down','throttle_offer','blink']
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':steps,'look_after':None,'say_before':None,'text':'Ta-da.'}))
        led, ports, _ = rig()
        led.clock = self.state.trace.clock
        led.wait = lambda n: setattr(self.state.trace,'at',self.state.trace.at+n)
        self.addCleanup(led.close)
        self.output.leds = led
        context = grounded_tools({'source_turn_id':'t','robot_pose_enabled':True,'led_enabled':True,'led_patterns':['blink','happy']})
        offered = next(t for t in context if t['name']=='set_robot_pose')['parameters']['properties']['poses']['items']['enum']
        self.assertIn('blink', offered); self.assertNotIn('lights_angry', offered)
        result = self.turn('Look left then right then up then down then hold your hand out and blink your eyes.')
        self.assertEqual(result.completed_text, ['Ta-da.'])
        self.assertEqual([r['pose'] for r in self.rows('robot_pose_result')], steps[:5])
        self.assertEqual([l['pattern'] for l in result.lights if l['request_id'].endswith(':5')], ['blink'])
        self.assertEqual(ports['head'].commands.count('EYE:0,0,0'), 3)
        self.assertEqual({ch:self.port.pose[ch] for ch,_ in HEAD_DOWN}, dict(HEAD_DOWN))
        self.assertEqual({ch:self.port.pose[ch] for ch,_ in NAMED_POSES['throttle_offer']}, dict(NAMED_POSES['throttle_offer']))

    def test_say_before_is_heard_before_any_step_moves(self):
        # Live September 28: "On it." played only after the whole sequence.
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':['throttle_offer','look_left'],'look_after':None,
                                                        'say_before':'On it.','text':None}))
        result = self.turn('Hold out your hand, then look left.')
        self.assertEqual(result.completed_text, ['On it.'])
        events = [r['event'] for r in self.state.trace.rows]
        self.assertLess(events.index('phrase_completed'), events.index('robot_pose_result'))
        self.assertEqual(len(self.rows('robot_pose_result')), 2)

    def test_step_count_is_not_capped(self):
        steps = ['look_left','look_right']*6
        validate('set_robot_pose', {'poses':steps,'look_after':None,'say_before':None,'text':None})
        self.setup_robot(ToolCall('p','set_robot_pose',{'poses':steps,'look_after':None,'say_before':None,'text':'Done.'}))
        self.assertEqual(self.turn('Look left and right six times.').completed_text, ['Done.'])
        self.assertEqual(len(self.rows('robot_pose_result')), 12)


class VoiceReflexTests(unittest.TestCase):
    """Owner request September 28: face a voice from the front before the model acts."""
    def setUp(self):
        from r3x.voice_reflex import voice_neck
        self.voice_neck = voice_neck
        self.clock, self.port = Clock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda:self.port, clock=self.clock, wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait
        self.robot.start()
        self.addCleanup(self.robot.close)
        self.moves = []
        self.robot.owner.on_camera_motion = lambda moving,*_: self.moves.append(self.clock()) if moving else None

    def settle(self):
        self.robot.reflex._thread.join(5)

    def test_front_left_voice_turns_the_neck_to_face_it(self):
        from r3x.drive_space import NECK_CENTER
        self.assertEqual(self.robot.reflex.face(25.), 'started'); self.settle()
        self.assertEqual(self.port.pose[0], self.voice_neck(25.))
        self.assertLess(self.port.pose[0], NECK_CENTER)            # Low pulse is Rex's left.
        self.assertEqual(len(self.moves), 1)                        # Camera told it re-framed.
        self.assertFalse(self.robot.owner.held_channels & {0})      # A reflex holds nothing.
        self.assertEqual(self.robot.reflex.face(27.), 'already_facing')
        self.assertEqual(self.robot.reflex.face(-60.), 'started'); self.settle()
        self.assertEqual(self.port.pose[0], self.voice_neck(-60.))

    def test_voice_behind_does_not_move_the_head(self):
        before = self.port.pose[0]
        self.assertEqual(self.robot.reflex.face(-142.), 'behind')   # The TV in the other room.
        self.assertEqual(self.port.pose[0], before)

    def test_new_voice_takes_over_an_earlier_held_look(self):
        # Live September 28: after a held look left, "I'm over here" left the head away.
        from r3x.contracts import Cancellation
        self.assertEqual(self.robot.execute_pose('look_right', Cancellation(), 'look').status, 'pulses_reached')
        self.assertIn(0, self.robot.owner.held_channels)
        self.assertEqual(self.robot.reflex.face(25.), 'started'); self.settle()
        self.assertEqual(self.port.pose[0], self.voice_neck(25.))
        self.assertNotIn(0, self.robot.owner.held_channels)

    def test_visible_face_near_the_voice_is_aimed_at_instead_of_the_array_bearing(self):
        # Live September 28 (5e97b13a): array said 6 degrees right, the camera saw Bret's
        # face at the left edge of the frame (about 38 degrees left).
        from r3x.drive_space import image_bearing
        box = [.05, .4, .06, .1]
        self.robot.reflex.faces = lambda: [box]
        self.assertEqual(self.robot.reflex.face(-6.), 'started'); self.settle()
        self.assertEqual(self.port.pose[0], self.voice_neck(image_bearing(box, 0.)))
        far = [.9, .4, .06, .1]    # A face far from a voice outside the frame is not it.
        self.robot.reflex.faces = lambda: [far]
        neck = self.robot.owner.neck_degrees()
        self.assertEqual(self.robot.reflex.face(-89.), 'started'); self.settle()
        self.assertEqual(self.port.pose[0], self.voice_neck(-89.))
        self.assertNotEqual(self.voice_neck(-89.), self.voice_neck(image_bearing(far, neck)))

    def test_model_move_replaces_an_unfinished_reflex_instead_of_failing_busy(self):
        from r3x.contracts import Cancellation
        self.assertEqual(self.robot.reflex.face(40.), 'started')
        receipt = self.robot.execute_pose('look_up', Cancellation(), 'up')
        self.assertEqual(receipt.status, 'pulses_reached')
