"""October 31 run f6d5a0b7: asked what he was looking at he turned to Bret and said "you", a look he was
asked for never let go of his head, and an arm dance came out as head and reaching poses.

Fake serial, fake clock, synthetic frames and scripted model calls only. Whether the reply model now picks
arm_dance or throttle_high_five from the new descriptions is the paid probe's job
(tools/check_action_requests.py)."""
from uuid import uuid4

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.inspection import CurrentView
from r3x.output import OutputOwner
from r3x.robot_servos import RobotServos
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servo_poses import HEAD, POKER, REST, THROTTLE, talking_lanes
from r3x.session import LOOK_HOLD_SECONDS, Session
from tests.test_drive_session import FakeScene
from tests.test_memory_core import MemoryFixture
from tests.test_servos import Clock, PulsePort

ARMS = (4, 5, 6, *THROTTLE, *POKER)


class Scripted:
    """Answers the first request with `call` and any continuation with `answer`."""
    def __init__(self, call, answer, on_answer):
        self.call, self.answer, self.on_answer, self.snapshots = call, answer, on_answer, []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        if snapshot.continuation:
            self.on_answer()
            yield self.answer
        else:
            yield self.call


class October31Tests(MemoryFixture):
    def setup_robot(self, call, answer='Oh, those shelves caught my eye.', on_answer=lambda: None):
        self.clock, self.port = Clock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda: self.port, clock=self.clock, wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait
        self.robot.start()
        self.addCleanup(self.robot.close)
        self.brain = Scripted(call, answer, on_answer)
        view = CurrentView(FakeScene(self.clock), clock=self.clock)
        view.start()
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, robot=self.robot, inspection=view)
        self.state.trace.clock = self.clock
        self.session = Session('s', self.brain, self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)

    def turn(self, text, start=98.):
        return self.session.process_turn(Capture(uuid4().hex, 's', bytes(1024), start, start + 1, start + 1.6),
                                         Transcript(text, -.01, True), self.session.revision)

    def rows(self, event):
        return [r for r in self.state.trace.rows if r['event'] == event]

    def commanded(self, since=0):
        """(channel, pulse) for every target sent to the board since packet `since`."""
        sent = []
        for p in self.port.packets[since:]:
            if p[0] == 0x84:
                sent.append((p[1], p[2] + 128 * p[3]))
            elif p[0] == 0x9F:
                sent.extend((p[2] + i, p[3 + 2 * i] + 128 * p[4 + 2 * i]) for i in range(p[1]))
        return sent

    def test_a_look_he_was_asked_for_lets_go_of_his_head_about_20_s_after_his_answer(self):
        # "Look to your left, and then up a little bit": the head stayed up and left for 41 s, through his
        # answer and on, until "You can resume normal operations now."
        self.setup_robot(ToolCall('p', 'set_robot_pose', {'poses': ['look_left', 'look_up'], 'say_before': 'On it.',
                                                          'look_after': 'What do I see?', 'text': None}),
                         answer='The ceiling, mostly.')
        result = self.turn('Okay. So look to your left, and then up a little bit, and tell me what you see.')
        self.assertEqual(result.completed_text, ['On it.', 'The ceiling, mostly.'])
        owner = self.robot.owner
        self.clock.wait(LOOK_HOLD_SECONDS - 2)
        self.robot.before_frame()
        self.assertTrue(set(HEAD) <= owner.held_channels)          # Still the look he was asked for...
        self.assertEqual(self.robot.idle.mode(), 'direction')
        self.assertFalse(any(0 in dict(lane[0].targets) for lane in talking_lanes(dict(self.port.pose), held=owner.held_channels)))
        self.clock.wait(3)
        self.robot.before_frame()
        self.assertFalse(set(HEAD) & owner.held_channels)          # ...then his own again, unasked.
        self.assertEqual(self.robot.idle.mode(), 'people')
        self.assertTrue(any(0 in dict(lane[0].targets) for lane in talking_lanes(dict(self.port.pose), held=owner.held_channels)))
        self.assertEqual(owner.requested_look['where'], 'look up')  # "Look there again" still knows where.

    def test_a_head_cock_he_is_asked_for_lets_go_the_same_way(self):
        self.setup_robot(ToolCall('p', 'set_robot_pose', {'poses': ['tilt_right'], 'say_before': None,
                                                          'look_after': None, 'text': 'Like this?'}))
        self.assertEqual(self.turn('Tilt your head.').completed_text, ['Like this?'])
        self.assertTrue({2, 11} <= self.robot.owner.held_channels)
        self.clock.wait(LOOK_HOLD_SECONDS + 1)
        self.robot.before_frame()
        self.assertFalse(set(HEAD) & self.robot.owner.held_channels)

    def test_asked_what_he_is_looking_at_he_answers_from_where_his_head_points_and_keeps_it_there(self):
        held = []
        self.setup_robot(ToolCall('v', 'inspect_view', {'direction': 'current', 'question': 'What am I looking at?'}),
                         on_answer=lambda: held.append(set(self.robot.owner.held_channels)))
        head = {ch: self.port.pose[ch] for ch in HEAD}
        result = self.turn('What are you looking at?')
        self.assertEqual(result.completed_text, ['Oh, those shelves caught my eye.'])
        self.assertEqual(self.rows('inspection_result')[-1]['status'], 'observed')
        self.assertTrue(set(HEAD) <= held[0])                    # Talking leaves his head where the picture was taken.
        self.assertEqual({ch: self.port.pose[ch] for ch in HEAD}, head)
        self.robot.before_frame()
        self.assertFalse(set(HEAD) & self.robot.owner.held_channels)   # Answered: the head is his again at once.

    def test_someone_at_the_edge_of_his_view_is_not_what_he_is_looking_at(self):
        # At the question his head had wandered off to the left of Bret, who sat at the right edge of his view,
        # and he was told "looking at Bret Benziger".
        self.setup_robot(ToolCall('v', 'inspect_view', {'direction': 'current', 'question': '?'}))
        names = {'bret': 'Bret'}
        self.robot.idle.activity = ('following_people', self.clock())
        looking = self.session._looking([{'box': [.86, .45, .07, .16], 'identity': 'bret'}], names)
        self.assertNotIn('looking at Bret', looking['why'])
        self.assertIn('wandered off Bret', looking['why'])
        self.assertIn('straight ahead of your head', looking['if_asked'])
        self.assertEqual(looking['people_in_his_view'], ['Bret at the right edge of his view'])
        centred = self.session._looking([{'box': [.46, .4, .08, .16], 'identity': 'bret'}], names)
        self.assertEqual(centred['why'], 'looking at Bret')
        self.assertNotIn('if_asked', centred)
        # A room look with someone still in sight does not say nobody is there.
        self.robot.idle.activity = ('looking_around_the_room', self.clock())
        self.assertEqual(self.session._looking([{'box': [.86, .45, .07, .16], 'identity': 'bret'}], names)['why'],
                         'just looking around the room')

    def test_a_dance_with_his_arms_moves_all_three_and_keeps_the_throttle_hand_up(self):
        # "Can you do a little dance with your arms?" got the head's wiggle; asked again, throttle_offer's hand
        # reached forward and down into something on the floor.
        self.setup_robot(ToolCall('p', 'set_robot_pose', {'poses': ['arm_dance'], 'say_before': 'Watch this.',
                                                          'look_after': None, 'text': None}))
        since = len(self.port.packets)
        self.assertEqual(self.turn('Can you do a little dance with your arms?').completed_text, ['Watch this.'])
        receipt = self.rows('robot_pose_result')[-1]
        self.assertEqual((receipt['pose'], receipt['status']), ('arm_dance', 'pulses_reached'))
        self.assertTrue(set(ARMS) <= set(receipt['changed_channels']))
        self.assertNotIn(7, receipt['changed_channels'])            # The microphone arm stays still.
        sent = self.commanded(since)
        self.assertTrue(all(pulse <= dict(REST)[8] for ch, pulse in sent if ch == 8))   # Shoulder never lower than rest...
        self.assertTrue(all(pulse >= dict(REST)[9] for ch, pulse in sent if ch == 9))   # ...and the forearm stays up.
        self.assertFalse(self.robot.owner.held_channels)              # A dance holds nothing.

    def test_the_dance_ends_with_each_arm_where_it_was(self):
        self.setup_robot(ToolCall('p', 'set_robot_pose', {'poses': ['arm_dance'], 'say_before': None,
                                                          'look_after': None, 'text': None}))
        for start in ('throttle_high_five', 'throttle_down', 'poker_out'):
            self.assertEqual(self.robot.execute_pose(start, Cancellation(), start).status, 'pulses_reached')
            before = {ch: self.port.pose[ch] for ch in ARMS}
            receipt = self.robot.execute_pose('arm_dance', Cancellation(), 'dance-' + start)
            self.assertEqual(receipt.status, 'pulses_reached')
            self.assertEqual({ch: self.port.pose[ch] for ch in ARMS}, before)
