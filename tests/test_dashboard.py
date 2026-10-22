"""Dashboard contracts: the Qt-free feed, LED mirror and rig, and the operator modes the
dashboard drives through the servo and drive owners. No Qt, devices or network."""
import math
import threading
import time
import unittest

from r3x.contracts import Cancellation
from r3x.gui.feed import DashboardFeed, LiveParts
from r3x.gui.lights import LightMirror, MouthAnimation, chest_render_state
from r3x.gui import rig
from r3x.servo_poses import LIMITS, THROTTLE, VISOR_DOWN, head_pulses


def native_available():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def row(event, **data):
    return {'session_id': 's', 'at': 0., 'event': event, **data}


class Clock:
    def __init__(self):
        self.now = 10.

    def __call__(self):
        return self.now


class FeedTranscriptTests(unittest.TestCase):
    def setUp(self):
        self.printed = []
        self.feed = DashboardFeed(console=self.printed.append, clock=Clock(), wall=lambda: 1_700_000_000.)

    def test_console_still_sees_every_row_and_bad_rows_never_break_the_trace(self):
        self.feed(row('turn'))   # Missing turn_id: the feed swallows its own error.
        self.feed(row('listening'))
        self.assertEqual([r['event'] for r in self.printed], ['turn', 'listening'])

    def test_speaker_label_keeps_attribution_uncertainty(self):
        for turn, evidence in (('t1', {'display_name': 'Bret', 'state': 'supported'}),
                               ('t2', {'display_name': 'Bret', 'state': 'hypothesis'}),
                               ('t3', {'display_name': None, 'state': 'unresolved'})):
            self.feed(row('turn', turn_id=turn, original_text='hello'))
            self.feed(row('speaker_evidence', turn_id=turn, **evidence))
        lines = self.feed.snapshot()['lines']
        self.assertEqual([l['speaker'] for l in lines], ['Bret', 'Bret?', 'Unknown speaker'])
        self.assertEqual(lines[2]['note'], 'voice: unresolved')

    def test_reply_grows_by_phrase_and_an_interruption_shows_what_was_heard(self):
        self.feed(row('speech_generated', turn_id='t1', text='First part.'))
        before = self.feed.revision
        self.feed(row('speech_generated', turn_id='t1', text='Second part.'))
        self.feed(row('delivery', turn_id='t1', status='interrupted', completed_text='First part.'))
        lines = self.feed.snapshot()['lines']
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]['text'], 'First part. Second part.')
        self.assertEqual(lines[0]['note'], 'interrupted; heard: First part.')
        self.assertGreater(self.feed.revision, before)

    def test_activity_follows_thinking_speaking_and_delivery(self):
        self.feed(row('startup'))
        self.feed(row('model_request', primary_calls=1))
        self.assertEqual(self.feed.snapshot()['activity'], 'thinking')
        self.feed(row('playback_started', turn_id='t'))
        self.assertEqual(self.feed.snapshot()['activity'], 'speaking')
        self.feed(row('delivery', turn_id='t', status='completed', completed_text='x'))
        self.assertEqual(self.feed.snapshot()['activity'], 'listening')

    def test_shutdown_before_the_conversation_is_up_waits_for_it(self):
        requested = []
        class Runtime:
            def request_shutdown(self, source):
                requested.append(source)
        self.assertFalse(self.feed.shutdown())
        self.assertEqual(requested, [])
        self.feed.attach(LiveParts(runtime=Runtime()))
        self.assertEqual(requested, ['dashboard'])

    def test_shutdown_row_detaches_owners_and_notifies_the_window(self):
        closed = []
        self.feed.on_closed.append(lambda: closed.append(True))
        self.feed.attach(LiveParts(runtime=object(), log_path='x.jsonl'))
        self.feed(row('shutdown', report={}))
        self.assertIsNone(self.feed.parts.runtime)
        self.assertEqual(self.feed.parts.log_path, 'x.jsonl')
        self.assertEqual(closed, [True])


class LightMirrorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.mirror = LightMirror(self.clock)

    def test_head_commands_drive_eyes_and_mouth(self):
        self.mirror.command('head', 'EYE:255,200,0')
        self.mirror.command('head', 'SPEAK:happy')
        self.mirror.command('head', 'SPEAK_LEVEL:300')
        head = self.mirror.snapshot()['head']
        self.assertEqual((head['eye_color'], head['eyes_active']), ((255, 200, 0), True))
        self.assertEqual((head['mouth_mode'], head['mouth_emotion'], head['mouth_level']), ('speak', 'happy', 255))
        self.mirror.command('head', 'SPEAK_STOP')
        self.assertEqual(self.mirror.snapshot()['head']['mouth_mode'], 'active')
        self.mirror.command('head', 'FADEOFF')
        head = self.mirror.snapshot()['head']
        self.assertEqual((head['mouth_mode'], head['eyes_active']), ('fadeoff', False))

    def test_chest_commands_and_compliment_flash(self):
        self.mirror.command('chest', 'SPEAK:sad')
        chest = self.mirror.snapshot()['chest']
        self.assertEqual((chest['mode'], chest['emotion']), ('speak', 'sad'))
        self.assertEqual(chest_render_state(chest, self.clock())['rate'], 2.5)
        self.mirror.command('chest', 'COMPLIMENT')
        self.assertTrue(chest_render_state(self.mirror.snapshot()['chest'], self.clock() + 1)['flash'])
        self.assertFalse(chest_render_state(self.mirror.snapshot()['chest'], self.clock() + 3)['flash'])
        self.mirror.command('chest', 'SPEAK_STOP')
        self.assertEqual(self.mirror.snapshot()['chest']['mode'], 'active')

    def test_playback_fallback_never_overrides_real_board_commands(self):
        self.mirror.speaking(True, 120)
        self.assertEqual(self.mirror.snapshot()['head']['mouth_level'], 120)
        self.mirror.command('head', 'SPEAK_STOP')
        self.mirror.speaking(True, 200)
        self.assertEqual(self.mirror.snapshot()['head']['mouth_mode'], 'active')

    @unittest.skipUnless(native_available(), 'numpy unavailable (blocked under the contract runner)')
    def test_mouth_is_dark_when_off_and_fades_out_in_four_seconds(self):
        mouth = MouthAnimation()
        self.assertEqual(float(mouth.render({'mouth_mode': 'off'}, 0.).sum()), 0.)
        lit = mouth.render({'mouth_mode': 'speak', 'mouth_level': 200, 'mouth_updated_at': 1., 'mouth_utterance': 1}, 1.)
        self.assertGreater(float(lit.sum()), 0.)
        state = {'mouth_mode': 'fadeoff', 'mouth_updated_at': 1.}
        self.assertGreater(float(mouth.render(state, 2.).sum()), 0.)
        self.assertEqual(float(mouth.render(state, 5.1).sum()), 0.)


@unittest.skipUnless(native_available(), 'numpy unavailable (blocked under the contract runner)')
class RigTests(unittest.TestCase):
    def test_transforms_stay_rigid_and_bounded_for_any_pulses(self):
        for pulses in (rig.BOOT_PULSES, rig.AWAKE_PULSES, {ch: hi for ch, (lo, hi) in LIMITS.items()},
                       {0: float('nan'), 3: 'junk'}):
            for m in rig.pose_matrices(rig.rig_values(pulses)).values():
                r = m[:3, :3]
                self.assertTrue(all(math.isfinite(v) for v in m.ravel()))
                self.assertAlmostEqual(float(abs((r @ r.T) - __import__('numpy').eye(3)).max()), 0., places=6)


class RigValueTests(unittest.TestCase):
    def test_visor_closed_at_park_open_at_its_widest_and_head_pitch_follows_v3(self):
        self.assertEqual(rig.rig_values({3: VISOR_DOWN})['visor'], 27.)
        self.assertEqual(rig.rig_values({3: LIMITS[3][0]})['visor'], 0.)
        up = rig.rig_values(head_pulses(1., 0.))['headtilt']
        level = rig.rig_values(head_pulses(0., 0.))['headtilt']
        down = rig.rig_values(head_pulses(-1., 0.))['headtilt']
        self.assertGreater(up, level)
        self.assertGreater(level, down)
        self.assertGreater(rig.rig_values(head_pulses(0., 1.))['headside'], 0.)


class OperatorServoTests(unittest.TestCase):
    def setUp(self):
        from tests.test_servos import Clock as ServoClock, PulsePort
        from r3x.robot_servos import RobotServos
        self.clock, self.port = ServoClock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda: self.port, clock=self.clock, wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait
        self.robot.start()
        self.addCleanup(self.robot.close)

    def request(self, kind, **data):
        from r3x.servos import ServoRequest
        return self.robot.owner.execute(ServoRequest(kind + '-1', self.robot.owner.version, kind,
                                                     self.clock() + 30, **data), Cancellation())

    def test_rex_own_motion_yields_while_operator_poses_move_through_the_owner(self):
        self.robot.begin_operator()
        self.assertEqual(self.request('idle').reason, 'OperatorOverride')
        self.assertEqual(self.request('pose', pose='look_left').reason, 'OperatorOverride')
        receipt = self.robot.operator_pose({0: 6000}, Cancellation(), 'op-1')
        self.assertEqual(receipt.status, 'pulses_reached')
        self.assertEqual(self.port.pose[0], 6000)
        hold = self.robot.owner.microphone_hold
        self.clock.now += 60
        self.robot.owner.refresh_microphone_hold()   # Rex does not recentre the array arm meanwhile.
        self.assertEqual(self.robot.owner.microphone_hold, hold)
        self.robot.end_operator()   # Rex's own idle life resumes from here...
        if self.robot.idle._thread is not None:
            self.robot.idle._thread.join(2)   # ...and its first phrase may hold the owner (ServoBusy) meanwhile.
        self.assertEqual(self.robot.operator_pose({0: 5000}, Cancellation(), 'op-2').reason, 'OperatorOverrideOff')

    def test_operator_poses_keep_calibrated_limits_and_throttle_routes(self):
        self.robot.begin_operator()
        self.assertEqual(self.robot.operator_pose({0: LIMITS[0][1] + 100}, Cancellation(), 'op').status, 'rejected')
        receipt = self.robot.operator_pose({8: 6544}, Cancellation(), 'op-throttle')   # One throttle joint.
        self.assertIn(receipt.status, {'pulses_reached', 'rejected'})
        if receipt.status == 'rejected':
            self.assertNotEqual(receipt.reason, 'OperatorOverride')
        self.assertTrue(all(LIMITS[ch][0] <= self.port.pose[ch] <= LIMITS[ch][1] for ch in THROTTLE))

    def test_dashboard_override_worker_moves_joints_and_drops_stale_sliders(self):
        feed = DashboardFeed()
        feed.attach(LiveParts(robot=self.robot))
        self.assertEqual(feed.servos.enable(True), (True, None))
        feed.servos.set('neck', 100.)
        deadline = time.monotonic() + 3
        while self.port.pose[0] != LIMITS[0][1] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.port.pose[0], LIMITS[0][1])
        feed.servos.enable(False)
        feed.servos.set('neck', 0.)   # After release: never queued, so it cannot run later.
        self.assertEqual(feed.servos.pending, {})
        self.assertFalse(self.robot.owner.operator)
        feed.servos.thread.join(2)
        self.assertFalse(feed.servos.thread.is_alive())


class TeleopTests(unittest.TestCase):
    def setUp(self):
        from tests.test_drive import base_setup
        self.clock, self.fake, self.drive, self.world = base_setup(self)
        self.drive.start()
        self.addCleanup(lambda: self.drive.state != 'closed' and self.drive.close())

    def drives(self):
        return [c for c in self.fake.commands if c['cmd'] == 'drive']

    def test_manual_mode_refuses_rex_plans_until_released(self):
        from tests.test_drive import plan
        from r3x.drive import DriveStep
        self.assertEqual(self.drive.begin_manual(), (True, None))
        receipt = self.drive.execute(plan(DriveStep('turn', degrees=30.), clock=self.clock), Cancellation())
        self.assertEqual((receipt.status, receipt.reason), ('refused', 'DriveBusy'))
        self.assertIsNone(self.drive.tick_reflex())
        self.drive.end_manual()
        self.assertTrue(self.drive._lock.acquire(blocking=False))
        self.drive._lock.release()

    def test_setpoints_are_clamped_and_forward_holds_at_an_obstacle(self):
        from r3x.drive import MANUAL_SPEED
        self.drive.begin_manual()
        sent = self.drive.manual(5., 0., Cancellation())
        self.assertEqual((sent['status'], sent['lin']), ('sent', MANUAL_SPEED))
        self.assertTrue(self.drive.moving.is_set())
        self.fake.front = lambda: .2
        time.sleep(.05)
        held = self.drive.manual(.2, 0., Cancellation())
        self.assertEqual(held['lin'], 0.)
        self.assertTrue(held['held'])
        self.assertEqual(self.drive.manual(-.1, 0., Cancellation())['lin'], -.1)   # Backing away still works.
        self.drive.end_manual()
        self.assertFalse(self.drive.moving.is_set())
        self.assertEqual(self.fake.commands[-1]['cmd'], 'stop')

    def test_a_stop_holds_the_wheels_until_the_stick_is_released(self):
        self.drive.begin_manual()
        self.drive.manual(.1, 0., Cancellation())
        self.drive.cancel()   # Spoken "stop", the Stop button.
        self.assertEqual(self.drive.manual(.1, 0., Cancellation())['reason'], 'stop_requested')
        self.drive.manual(0., 0., Cancellation())
        self.assertEqual(self.drive.manual(.1, 0., Cancellation())['status'], 'sent')
        self.drive.end_manual()

    def test_closing_the_base_ends_manual_mode(self):
        self.drive.begin_manual()
        self.drive.close()
        self.assertEqual(self.drive.state, 'closed')
        self.assertFalse(self.drive.manual_active)


class OwnerHookTests(unittest.TestCase):
    def test_led_observers_see_only_commands_written(self):
        from r3x.leds import LEDs
        written, seen = [], []
        class Port:
            def write(self, data):
                written.append(data)
                return len(data)
            def close(self):
                pass
        leds = LEDs({'head': 'h'}, opener=lambda path: Port(), wait=lambda s: None)
        leds.on_command.append(lambda zone, command: seen.append((zone, command)))
        leds.start()
        self.addCleanup(leds.close)
        self.assertIn(('head', 'EYE:255,200,0'), seen)
        self.assertNotIn('chest', {zone for zone, _ in seen})   # No chest board, nothing mirrored.
        self.assertEqual(len(seen), len(written))

    def test_runtime_shutdown_request_is_emitted_once(self):
        from r3x.runtime import VoiceRuntime
        rows = []
        runtime = VoiceRuntime.__new__(VoiceRuntime)
        runtime.stop = threading.Event()
        runtime.trace = type('Trace', (), {'emit': lambda self, event, **data: rows.append((event, data))})()
        runtime.cancel = lambda quiet=False: rows.append(('cancel', {}))
        runtime.request_shutdown('dashboard')
        runtime.request_shutdown('dashboard')
        self.assertEqual(rows, [('shutdown_requested', {'source': 'dashboard'}), ('cancel', {})])


if __name__ == '__main__':
    unittest.main()


class SimulatedBodyTests(unittest.TestCase):
    def test_simulated_maestro_moves_at_the_commanded_speed(self):
        from r3x.gui.sim import SimulatedMaestro
        clock = Clock()
        port = SimulatedMaestro({0: 5000}, clock)
        port.write(bytes((0x87, 0, 10, 0)))             # 10 quarter-us per 10 ms
        port.write(bytes((0x84, 0, 6000 & 127, 6000 >> 7)))
        port.write(bytes((0x90, 0)))
        clock.now += .1
        self.assertEqual(int.from_bytes(port.read(2), 'little'), 5100)
        clock.now += 1.
        self.assertEqual(int.from_bytes(port.read(2), 'little'), 6000)

    def test_dashboard_body_appears_only_without_a_controller_and_never_reaches_rex(self):
        from unittest.mock import patch
        posted = []
        class Body:
            state = 'active'
            def __init__(self, faces=None):
                self.robot = None
            def start(self):
                posted.append('start')
            def post(self, event, turn_id=None):
                posted.append(event)
            def close(self, wait=0.):
                posted.append('close')
        class Settings:
            servo_port = None
        with patch('r3x.gui.sim.SimulatedBody', Body):
            feed = DashboardFeed()
            parts = LiveParts(settings=Settings())
            feed.attach(parts)
            feed(row('turn', turn_id='t', original_text='hi'))
            feed(row('playback_started', turn_id='t'))
            feed(row('delivery', turn_id='t', status='completed', completed_text='x'))
            self.assertIsNone(parts.robot)   # Rex is never handed the display body.
            Settings.servo_port = '/dev/cu.maestro'
            other = DashboardFeed()
            other.attach(LiveParts(settings=Settings()))
            self.assertIsNone(other.body)
        self.assertEqual(posted, ['start', 'heard', 'speaking', 'done'])


class RampMirrorTests(unittest.TestCase):
    def test_droid_follows_the_board_ramp_not_the_commanded_target(self):
        from tests.test_servos import Clock as ServoClock, PulsePort
        from r3x.robot_servos import RobotServos
        servo_clock, port = ServoClock(), PulsePort()
        robot = RobotServos('fake', open_port=lambda: port, clock=servo_clock, wait=servo_clock.wait)
        robot.owner.wait = servo_clock.wait
        clock = Clock()
        feed = DashboardFeed(clock=clock)
        feed.attach(LiveParts(robot=robot))   # Before start, as App.live attaches early.
        robot.start()
        self.addCleanup(robot.close)
        feed.snapshot()   # Seeds the mirror from the owner's readback.
        robot.begin_operator()
        receipt = robot.operator_pose({0: 8000}, Cancellation(), 'op')
        self.assertEqual(receipt.status, 'pulses_reached')   # The fake board arrives at once.
        servos = feed.snapshot()['servos']
        self.assertEqual(servos['commanded'][0], 8000)
        self.assertLess(servos['positions'][0], 8000)          # The droid has not got there yet.
        clock.now += 10.
        self.assertEqual(feed.snapshot()['servos']['positions'][0], 8000)

    def test_a_failing_observer_never_affects_the_servos(self):
        from r3x.adapters.maestro import Maestro
        from tests.test_servos import PulsePort
        port = PulsePort()
        bus = Maestro(port)
        def broken(packet):
            raise RuntimeError('display failed')
        bus.observers = [broken]
        bus.targets({0: 6000})
        self.assertEqual(port.pose[0], 6000)
