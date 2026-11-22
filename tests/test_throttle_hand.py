"""The throttle hand (owner, November 19): wrist twist on channel 14, pincher on 15.

Synthetic pulse transport only; the hand is not assembled and its channels are not yet set up
on the Maestro, so nothing here says how it looks or whether its range is right.
"""
import random
import unittest

from r3x.contracts import Cancellation
from r3x.servo_poses import (HAND_IDLE_REACH, HAND_PARK, HAND_TALK_REACH, HELD_PARTS, IdlePhrase, LIMITS,
                             SENTRY_STANCE, THROTTLE, THROTTLE_HAND, UNPOWERED_REST, hand_span, idle_lanes,
                             joint_percents, joint_targets, JOINTS, lifecycle_lanes, talking_lanes)
from tests.test_servos import Clock, PulsePort, ServoOwner, ServoRequest


def awake():
    """Where a woken Rex stands, from the simulated board's rest after a startup."""
    port, clock = PulsePort(), Clock()
    owner = ServoOwner(lambda: port, medium='simulation', clock=clock, wait=clock.wait)
    owner.start()
    owner.execute(ServoRequest('wake', owner.version, 'startup', clock() + 120.), Cancellation())
    pose = dict(port.pose)
    owner.close()
    return pose


def hand_targets(lanes):
    return [dict(beat.targets) for lane in lanes for beat in lane if set(dict(beat.targets)) & set(THROTTLE_HAND)]


class HandRangeTests(unittest.TestCase):
    def test_conservative_range_and_middle_park(self):
        self.assertEqual({ch: LIMITS[ch] for ch in THROTTLE_HAND}, {14: (2240, 7424), 15: (6120, 7900)})   # 560-1856, 1530-1975 us.
        self.assertEqual(dict(HAND_PARK), {14: 5804, 15: 6824})   # Wrist level at 1451 us; fingers' mid 1706 us.
        self.assertEqual({ch: dict(UNPOWERED_REST)[ch] for ch in THROTTLE_HAND}, dict(HAND_PARK))
        self.assertEqual({ch: SENTRY_STANCE[ch] for ch in THROTTLE_HAND}, dict(HAND_PARK))
        self.assertIn(('throttle_hand', {14, 15}), HELD_PARTS)

    def test_the_joint_tool_reaches_both_ends(self):
        positions = {name: None for name in JOINTS}
        targets = joint_targets({**positions, 'throttle_twist': 0, 'throttle_pincher': 100})
        self.assertEqual(targets, {14: 2240, 15: 7900})
        self.assertEqual({k: v for k, v in joint_percents(dict(HAND_PARK)).items()},
                         {'throttle_twist': 69, 'throttle_pincher': 40})   # Level wrist 1451 us; fingers 1706 us.

    def test_the_servo_menu_mirrors_the_limits(self):
        # Read the menu's table as written: importing the app would load its serial and menu machinery.
        import ast
        from pathlib import Path
        source = (Path(__file__).resolve().parent.parent / 'operator_tools/tools/rex_servo_menubar.py').read_text()
        values = {node.targets[0].id if isinstance(node, ast.Assign) else node.target.id: node.value
                  for node in ast.parse(source).body
                  if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None
                  and isinstance(node.targets[0] if isinstance(node, ast.Assign) else node.target, ast.Name)}
        table = {cfg['ch']: cfg for cfg in ast.literal_eval(values['_SERVO_DEFAULTS']).values()}
        self.assertEqual(sorted(table), sorted(LIMITS))
        for ch in THROTTLE_HAND:
            self.assertEqual((table[ch]['min'], table[ch]['max'], table[ch]['neutral']), (*LIMITS[ch], dict(HAND_PARK)[ch]))
        self.assertEqual(set(ast.literal_eval(values['_HAND_DIRECTIONS'])), {'throttle_twist', 'throttle_pincher'})


class HandMotionTests(unittest.TestCase):
    def setUp(self):
        self.current = awake()

    def within(self, targets, reach):
        for pose in targets:
            for ch, value in pose.items():
                low, high = hand_span(ch, reach)
                self.assertTrue(low <= value <= high, (ch, value, low, high))

    def test_talking_moves_more_than_idle_and_ends_where_it_began(self):
        talk_spread, idle_spread = [], []
        for seed in range(40):
            rng = random.Random(seed)
            lanes = talking_lanes(self.current, rng=rng)
            hand = [lane for lane in lanes if set(dict(lane[0].targets)) == set(THROTTLE_HAND)]
            self.assertEqual(len(hand), 1)
            targets = [dict(beat.targets) for beat in hand[0]]
            self.within(targets[:-1], HAND_TALK_REACH)
            self.assertEqual(targets[-1], {ch: self.current[ch] for ch in THROTTLE_HAND})   # Repeats smoothly.
            talk_spread += [abs(t[ch] - dict(HAND_PARK)[ch]) for t in targets[:-1] for ch in THROTTLE_HAND]
            phrase = IdlePhrase((('throttle', 0.),))
            idle = hand_targets(idle_lanes(self.current, random.Random(seed), phrase=phrase))
            self.assertEqual(len(idle), 1)
            self.within(idle, HAND_IDLE_REACH)
            idle_spread += [abs(t[ch] - dict(HAND_PARK)[ch]) for t in idle for ch in THROTTLE_HAND]
        self.assertGreater(max(talk_spread), max(idle_spread))
        self.assertGreater(sum(talk_spread) / len(talk_spread), sum(idle_spread) / len(idle_spread))

    def test_held_hand_stays_still(self):
        lanes = talking_lanes(self.current, held=frozenset(THROTTLE_HAND), rng=random.Random(1))
        self.assertEqual(hand_targets(lanes), [])
        idle = idle_lanes(self.current, random.Random(1), held=frozenset(THROTTLE_HAND),
                          phrase=IdlePhrase((('throttle', 0.),)))
        self.assertEqual(hand_targets(idle), [])

    def test_resting_arm_relaxes_the_hand_to_the_middle(self):
        moved = {**self.current, 14: 6400, 15: 6300}
        lanes = idle_lanes(moved, random.Random(3), phrase=IdlePhrase((('throttle', 0.),), throttle_rest=True))
        self.assertEqual(hand_targets(lanes), [dict(HAND_PARK)])

    def test_park_brings_the_hand_back_and_a_kept_arm_leaves_it(self):
        moved = {**self.current, 14: 7000, 15: 7600}
        self.assertIn(dict(HAND_PARK), hand_targets(lifecycle_lanes('shutdown', moved)))
        self.assertEqual(hand_targets(lifecycle_lanes('park', self.current)), [])   # Already at its park.
        self.assertEqual(hand_targets(lifecycle_lanes('startup', moved, keep=frozenset({'throttle'}))), [])
        self.assertIn(dict(HAND_PARK), hand_targets(lifecycle_lanes('startup', moved)))


class HandDriveTests(unittest.TestCase):
    def setUp(self):
        self.port, self.clock = PulsePort(), Clock()
        self.owner = ServoOwner(lambda: self.port, medium='simulation', clock=self.clock, wait=self.clock.wait)
        self.addCleanup(self.owner.close)
        self.owner.start()
        self.assertEqual(self.execute('startup').status, 'pulses_reached')

    def execute(self, kind, **kwargs):
        return self.owner.execute(ServoRequest(kind, self.owner.version, kind, self.clock() + 120., **kwargs),
                                  Cancellation())

    def test_drive_centres_the_hand_and_leaves_it_free(self):
        # Owner, November 20: the twist and fingers move freely with the arm folded.
        self.assertEqual(self.execute('pose', pose={14: 3000, 15: 7200}, hold_pose=False).status, 'pulses_reached')
        self.assertEqual(self.execute('base_park').status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE_HAND}, dict(HAND_PARK))
        self.assertTrue(set(THROTTLE) <= self.owner.held_channels)
        self.assertFalse(set(THROTTLE_HAND) & self.owner.held_channels)
        self.owner.begin_arm_return()
        self.assertFalse(set(THROTTLE_HAND) & self.owner.held_channels)   # Free while its arm walks back.
        self.assertEqual(self.owner.return_arm().status, 'pulses_reached')

    def test_a_kept_throttle_arm_leaves_its_hand_free_to_idle(self):
        self.assertEqual(self.execute('base_park').status, 'pulses_reached')
        self.owner.begin_arm_return()
        self.owner.return_arm(keep={'throttle'})
        self.assertTrue(set(THROTTLE) <= self.owner.held_channels)
        self.assertFalse(set(THROTTLE_HAND) & self.owner.held_channels)
        import random
        from r3x.servo_poses import IdlePhrase, idle_lanes
        current = dict(self.port.pose)
        lanes = idle_lanes(current, random.Random(3), frozenset(self.owner.held_channels),
                           IdlePhrase((('throttle', 0.),)))
        moved = {ch for lane in lanes for beat in lane for ch, _ in beat.targets}
        self.assertTrue(moved and moved <= set(THROTTLE_HAND), moved)   # The arm stays; the hand moves.

    def test_hold_arms_holds_the_hand(self):
        self.assertEqual(self.execute('pose', pose='hold_arms').status, 'pulses_reached')
        self.assertTrue(set(THROTTLE_HAND) <= self.owner.held_channels)


if __name__ == '__main__':
    unittest.main()
