"""Holding something light, like a card (owner, November 20): hand_take, hand_grip, hand_let_go.

Synthetic pulse transport, fake clock and fake range readings only; nothing here shows how the
hand looks, whether the fingers grip a card, or what the real sensors read when someone hands
him something.
"""
import unittest

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.hand_hold import FACE_NEAR_PX, LET_GO_S, SETTLE_S, WAIT_S, HandWatch
from r3x.output import OutputOwner
from r3x.robot_servos import RobotServos
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servo_poses import (FINGERS_CLOSED, FINGERS_OPEN, HAND_PARK, HAND_STEPS, LIMITS, NAMED_POSES, NECK_CENTER,
                             PARK, POSE_COMMANDS, REST, THROTTLE, THROTTLE_GRAB, THROTTLE_HAND, head_pose)
from r3x.servos import ServoRequest
from r3x.session import Session
from tests.test_memory_core import MemoryFixture
from tests.test_servos import Clock, PulsePort
from tests.test_voice_core import Brain

CLEAR = {'front_right_m': .9, 'front_radial_right_m': .85}


class HandWatchTests(unittest.TestCase):
    def settle(self, watch, front, face=None):
        for t in (0., .2, .4):
            self.assertIsNone(watch.observe(t, front, face))

    def test_something_coming_into_reach_closes_it_after_two_readings(self):
        watch = HandWatch(0.)
        self.settle(watch, .9)
        self.assertIsNone(watch.observe(SETTLE_S, .85, None))
        self.assertIsNone(watch.observe(1., .4, None))      # One near reading could be a flicker.
        self.assertEqual(watch.observe(1.1, .38, None), 'grip')

    def test_a_flicker_and_the_arm_already_in_view_do_not_close_it(self):
        watch = HandWatch(0.)
        self.settle(watch, .9)
        for t, front in ((1., .4), (1.1, .9), (1.2, .4), (1.3, .9)):
            self.assertIsNone(watch.observe(t, front, None))
        arm_in_view = HandWatch(0.)
        self.settle(arm_in_view, .42)   # The reach itself reads 0.42 m: only something nearer counts.
        self.assertIsNone(arm_in_view.observe(1., .40, None))
        self.assertIsNone(arm_in_view.observe(1.1, .40, None))
        self.assertIsNone(arm_in_view.observe(1.2, .30, None))
        self.assertEqual(arm_in_view.observe(1.3, .29, None), 'grip')

    def test_a_face_coming_close_closes_it_but_one_already_close_does_not(self):
        watch = HandWatch(0.)
        self.settle(watch, None, 150.)
        self.assertEqual(watch.observe(1., None, FACE_NEAR_PX), 'grip')
        already = HandWatch(0.)
        self.settle(already, None, FACE_NEAR_PX + 20)
        self.assertIsNone(already.observe(1., None, FACE_NEAR_PX + 40))

    def test_nothing_brought_ends_the_wait(self):
        watch = HandWatch(0.)
        self.settle(watch, .9)
        self.assertIsNone(watch.observe(WAIT_S - .1, .9, None))
        self.assertEqual(watch.observe(WAIT_S, .9, None), 'timeout')


class HandPoseTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.port = Clock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda: self.port, clock=self.clock, wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait
        self.robot.start(); self.robot.open_eyes(); self.addCleanup(self.robot.close)
        self.space = dict(CLEAR)
        self.robot.arm_space = lambda: self.space
        self.faces = []
        self.robot.hand_faces = lambda: self.faces

    def frames(self, seconds, step=.1):
        end = self.clock() + seconds
        while self.clock() < end:
            self.robot.before_frame()
            if self.robot._hand_worker is not None:
                self.robot._hand_worker.join(5.)
            self.clock.now += step

    def hand(self):
        return {ch: self.port.pose[ch] for ch in THROTTLE_HAND}

    def arm(self):
        return {ch: self.port.pose[ch] for ch in THROTTLE}

    def holding(self):
        self.take()
        self.assertEqual(self.robot.execute_pose('hand_grip', Cancellation(), 'grip').status, 'pulses_reached')
        self.assertEqual((self.arm(), self.port.pose[15]), (dict(PARK), FINGERS_CLOSED))

    def take(self):
        receipt = self.robot.execute_pose('hand_take', Cancellation(), 'take')
        self.assertEqual(receipt.status, 'pulses_reached', receipt.reason)
        return receipt

    def test_steps_are_offered_and_closed_is_the_lowest_pulse(self):
        self.assertTrue(set(HAND_STEPS) <= set(POSE_COMMANDS))
        self.assertEqual((FINGERS_CLOSED, FINGERS_OPEN), LIMITS[15])

    def test_take_reaches_out_open_and_looks_down_to_the_right(self):
        self.assertEqual(dict(THROTTLE_GRAB), {8: 1242 * 4, 9: 1720 * 4, 10: 887 * 4})   # Owner's grab position.
        self.assertEqual(dict(HAND_PARK)[14], 1415 * 4)                              # The hand level.
        self.take()
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(THROTTLE_GRAB))
        self.assertEqual(self.hand(), {14: dict(HAND_PARK)[14], 15: FINGERS_OPEN})
        self.assertGreater(self.port.pose[0], NECK_CENTER)                    # To his right.
        self.assertLess(head_pose(self.port.pose)[0], 0)                      # And down.
        self.assertEqual(self.robot.hand_state(), 'open')
        self.assertTrue(self.robot.owner.holding_requested_look())

    def test_no_room_at_the_front_right_refuses_it(self):
        self.space = {'front_right_m': .4, 'front_radial_right_m': .42}
        receipt = self.robot.execute_pose('hand_take', Cancellation(), 'take')
        self.assertEqual((receipt.status, receipt.reason), ('rejected', 'ThrottleExtensionBlocked:front_right_0.40m'))
        self.assertEqual(self.robot.hand_state(), 'empty')
        self.assertEqual(self.hand(), dict(HAND_PARK))

    def test_something_brought_closes_the_fingers_and_he_holds_it(self):
        self.take()
        self.frames(1.)
        self.assertEqual(self.port.pose[15], FINGERS_OPEN)
        self.space = {'front_right_m': .3, 'front_radial_right_m': .32}   # A hand with a card comes in.
        self.frames(.5)
        self.assertEqual(self.port.pose[15], FINGERS_CLOSED)
        self.assertEqual(self.robot.hand_state(), 'holding')
        self.assertEqual(self.arm(), dict(PARK))                            # Folded in as for a drive.
        self.assertFalse(self.robot.owner.holding_requested_look())        # Eyes back to the people.
        self.assertTrue(set(THROTTLE_HAND) <= self.robot.owner.held_channels)
        self.clock.now += 200.   # Long past the 90 s yield of a person's pose.
        self.robot.owner.yield_stale_hold()
        self.assertTrue(set(THROTTLE_HAND) <= self.robot.owner.held_channels)

    def test_relax_the_drive_and_idle_never_let_it_go(self):
        # Owner, November 20: while he holds something, idle movement must not let it go either.
        import random
        from r3x.servo_poses import IdlePhrase
        self.holding()
        owner = self.robot.owner
        def request(kind, **kw):
            return owner.execute(ServoRequest(kind, owner.version, kind, self.clock() + 120., **kw), Cancellation())
        self.assertEqual(self.robot.execute_pose('release_poses', Cancellation(), 'relax').status, 'pulses_reached')
        self.assertEqual(request('talking').status, 'pulses_reached')
        for seed in range(5):
            owner.rng = random.Random(seed)
            self.assertEqual(request('idle', idle=IdlePhrase((('throttle', 0.),), throttle_rest=True)).status,
                             'pulses_reached')
        self.assertEqual(self.robot.execute_pose('arm_dance', Cancellation(), 'dance').status, 'pulses_reached')
        self.clock.now += 200.   # Past every hold's yield.
        owner.yield_stale_hold()
        self.assertEqual(request('base_park').status, 'pulses_reached')
        owner.begin_arm_return()
        self.assertEqual(owner.return_arm().status, 'pulses_reached')
        self.assertEqual(self.port.pose[15], FINGERS_CLOSED)
        self.assertEqual(self.arm(), dict(PARK))   # Still folded in after the drive.
        self.assertEqual(self.robot.hand_state(), 'holding')
        self.assertTrue({*THROTTLE, *THROTTLE_HAND} <= owner.held_channels)

    def test_give_back_reaches_out_and_opens_when_someone_comes(self):
        self.holding()
        self.assertEqual(self.robot.execute_pose('hand_let_go', Cancellation(), 'go').status, 'pulses_reached')
        self.assertEqual((self.arm(), self.port.pose[15]), (dict(THROTTLE_GRAB), FINGERS_CLOSED))
        self.assertEqual(self.robot.hand_state(), 'offering')
        self.assertTrue(self.robot.owner.holding_requested_look())   # Looking at the hand.
        self.frames(1.)
        self.assertEqual(self.port.pose[15], FINGERS_CLOSED)          # Nobody near yet.
        self.space = {'front_right_m': .3, 'front_radial_right_m': .32}
        self.frames(.4)
        self.assertEqual(self.port.pose[15], FINGERS_OPEN)
        self.space = dict(CLEAR)
        self.frames(LET_GO_S + .3)
        self.assertEqual(self.arm(), dict(REST))
        self.assertEqual(self.hand(), dict(HAND_PARK))
        self.assertEqual(self.robot.hand_state(), 'empty')
        self.assertFalse({*THROTTLE, *THROTTLE_HAND} & self.robot.owner.held_channels)

    def test_nobody_coming_for_it_folds_it_back_in(self):
        self.holding()
        self.robot.execute_pose('hand_let_go', Cancellation(), 'go')
        self.frames(WAIT_S + 1., step=.5)
        self.assertEqual((self.arm(), self.port.pose[15]), (dict(PARK), FINGERS_CLOSED))
        self.assertEqual(self.robot.hand_state(), 'holding')

    def test_give_back_needs_room_and_keeps_holding_otherwise(self):
        self.holding()
        self.space = {'front_right_m': .4, 'front_radial_right_m': .42}
        self.assertEqual(self.robot.execute_pose('hand_let_go', Cancellation(), 'go').status, 'rejected')
        self.assertEqual((self.arm(), self.port.pose[15]), (dict(PARK), FINGERS_CLOSED))
        self.assertEqual(self.robot.hand_state(), 'holding')

    def test_nothing_brought_puts_the_arm_back(self):
        self.take()
        self.frames(WAIT_S + 1., step=.5)
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(REST))
        self.assertEqual(self.hand(), dict(HAND_PARK))
        self.assertEqual(self.robot.hand_state(), 'empty')

    def test_another_arm_pose_ends_the_wait(self):
        self.take()
        self.assertEqual(self.robot.execute_pose('throttle_high_five', Cancellation(), 'five').status, 'pulses_reached')
        self.assertEqual(self.robot.hand_state(), 'empty')
        self.space = {'front_right_m': .3, 'front_radial_right_m': .3}
        self.frames(1.)
        self.assertEqual(self.port.pose[15], FINGERS_OPEN)   # Nothing closes it now.


class HandConversationTests(MemoryFixture):
    """The production session runs the model's chosen steps; the model here is scripted."""

    def test_hold_this_then_let_it_go(self):
        clock, port = Clock(), PulsePort()
        robot = RobotServos('fake', open_port=lambda: port, clock=clock, wait=clock.wait)
        robot.owner.wait = clock.wait
        robot.start(); robot.open_eyes(); self.addCleanup(robot.close)
        space = dict(CLEAR)
        robot.arm_space = lambda: space
        brain = Brain(ToolCall('take', 'set_robot_pose', {'poses': ['hand_take'], 'look_after': None,
                                                          'say_before': 'Sure.', 'text': 'Pop it in my hand.'}))
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, robot=robot)
        session = Session('s', brain, output, self.state.trace, self.state); self.addCleanup(session.close)

        def turn(index, text):
            return session.process_turn(Capture(str(index), 's', bytes(1024), 98 + index * 10, 99 + index * 10,
                                                99.6 + index * 10), Transcript(text, -.01, True), session.revision)
        result = turn(0, 'Can you hold this card?')
        self.assertEqual(result.completed_text, ['Sure.', 'Pop it in my hand.'])
        self.assertEqual(port.pose[15], FINGERS_OPEN)
        for reading in (.9, .9, .9, .9, .9, .9, .9, .3, .3):
            space['front_right_m'] = reading
            robot.before_frame()
            if robot._hand_worker is not None:
                robot._hand_worker.join(5.)
            clock.now += .1
        self.assertEqual(port.pose[15], FINGERS_CLOSED)
        brain.text = 'Got it.'
        turn(1, 'Thanks.')
        self.assertIn('holding', brain.snapshots[-1].context['robot_pose_state']['throttle_hand'])
        space['front_right_m'] = .9   # They step back before asking for it.
        brain.text = ToolCall('go', 'set_robot_pose', {'poses': ['hand_let_go'], 'look_after': None,
                                                       'say_before': None, 'text': 'All yours.'})
        self.assertEqual(turn(2, 'You can let it go now.').completed_text, ['All yours.'])
        self.assertEqual(port.pose[15], FINGERS_CLOSED)   # Out again, opening once they come for it.
        for reading in (.9, .9, .9, .9, .9, .9, .9, .3, .3):
            space['front_right_m'] = reading
            robot.before_frame()
            if robot._hand_worker is not None:
                robot._hand_worker.join(5.)
            clock.now += .1
        self.assertEqual(port.pose[15], FINGERS_OPEN)

    def test_his_own_beats_cannot_hold_out_the_hand(self):
        call = ToolCall('take', 'set_robot_pose', {'poses': ['hand_take'], 'look_after': None, 'say_before': None,
                                                   'text': None})
        context = {'robot_pose_enabled': True, 'state_revision': self.store.revision, 'trigger': 'visual_arrival'}
        with self.assertRaisesRegex(ValueError, 'HandStepsOnlyWhenAsked'):
            self.state.propose_pose(call, context, Cancellation())
        person = {k: v for k, v in context.items() if k != 'trigger'}
        self.assertEqual(self.state.propose_pose(call, person, Cancellation()), ('hand_take',))


if __name__ == '__main__':
    unittest.main()
