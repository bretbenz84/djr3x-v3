"""Telepresence stages 3-5 (docs/TELEPRESENCE_PLAN.md): the call's stance and head control, driving over
a network link, and the supervisor's trigger. The software Maestro (tests.test_servos), the simulated
drive controller (tests.test_drive), fake sockets and clocks: no network, serial, audio or model."""
from pathlib import Path
import json
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from r3x.contracts import Cancellation
from r3x.drive import MANUAL_SPEED, MANUAL_TURN
from r3x.servo_poses import (DRIVE_ARMS, LIMITS, PARK, POKER_PARK, TELEPRESENCE_DRIVE_HEAD, telepresence_reading,
                             telepresence_targets)
from r3x.settings import Secrets, Settings
from r3x.telepresence import trigger
from r3x.telepresence.controls import (SETPOINT_STALE_SECONDS, DriveControl, HeadControl, base_readings)
from r3x.telepresence.protocol import encode_json, parse_networks, read_frame, sign_in
from tests.test_telepresence import (TOKEN, Clock as CallClock, FakeLink, FakeMic, FakeSpeaker, Recorder,
                                     socket_pair, wait_until)


def servos():
    from tests.test_servos import Clock, PulsePort
    from r3x.robot_servos import RobotServos
    clock, port = Clock(), PulsePort()
    robot = RobotServos('fake', open_port=lambda: port, clock=clock, wait=clock.wait)
    robot.owner.wait = clock.wait
    robot.start()
    return robot, port, clock


class StanceTests(unittest.TestCase):
    def setUp(self):
        self.robot, self.port, self.clock = servos()
        self.addCleanup(lambda: self.robot.closed or self.robot.close())

    def request(self, kind, **data):
        from r3x.servos import ServoRequest
        return self.robot.owner.execute(ServoRequest(kind + '-1', self.robot.owner.version, kind,
                                                     self.clock() + 30, **data), Cancellation())

    def test_the_stance_tucks_the_arms_and_refuses_rex_s_own_motion(self):
        early = self.robot.telepresence_pose({'neck': .5}, Cancellation(), 'early')
        self.assertEqual(early.reason, 'TelepresenceStanceNotHeld')
        self.assertEqual(self.robot.telepresence_stance(Cancellation()).status, 'pulses_reached')
        for ch, value in (*PARK, *DRIVE_ARMS, *POKER_PARK):
            self.assertEqual(self.port.pose[ch], value, ch)
        for kind, data in (('idle', {}), ('talking', {}), ('pose', {'pose': 'look_left'}), ('listening', {}),
                           ('gesture', {'primitive': 'nod'})):
            self.assertEqual(self.request(kind, **data).reason, 'OperatorOverride', kind)
        arms = {ch: self.port.pose[ch] for ch in (4, 5, 6, 7, 8, 9, 10, 12, 13)}
        receipt = self.robot.telepresence_pose({'neck': -1., 'pitch': .5, 'lift': 2., 'throttle': 1.},
                                               Cancellation(), 'look')
        self.assertEqual(receipt.status, 'pulses_reached')
        self.assertEqual(self.port.pose[0], LIMITS[0][0])     # Fully to his left, at the calibrated limit.
        self.assertEqual(self.port.pose[1], LIMITS[1][1])     # 2.0 is clamped to the top of the lift.
        self.assertEqual({ch: self.port.pose[ch] for ch in arms}, arms)   # No arm moved.
        self.assertAlmostEqual(telepresence_reading(self.robot.owner.commanded)['pitch'], .5, places=1)

    def test_the_head_levels_for_driving_and_shutdown_parks_from_the_stance(self):
        self.robot.telepresence_stance(Cancellation())
        self.robot.telepresence_pose({'neck': .8, 'tilt': .6, 'visor': 1.}, Cancellation(), 'look')
        self.assertEqual(self.robot.telepresence_level(Cancellation()).status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in TELEPRESENCE_DRIVE_HEAD}, dict(TELEPRESENCE_DRIVE_HEAD))
        self.assertIsNone(self.robot.telepresence_level(Cancellation()))   # Already level: nothing moves.
        self.robot.close()
        self.assertEqual(self.robot.owner.state, 'closed')

    def test_the_head_worker_aims_through_the_servo_owner_and_reports_where_it_got_to(self):
        self.robot.telepresence_stance(Cancellation())
        sent = []
        head = HeadControl(self.robot, notify=sent.append)
        head.start()
        self.addCleanup(head.close)
        self.assertIsNone(head.request({'neck': 1., 'visor': 0.}))
        wait_until(lambda: sent)
        self.assertEqual(sent[-1]['status'], 'pulses_reached')
        self.assertEqual((sent[-1]['neck'], sent[-1]['visor']), (1., 0.))
        self.assertEqual(self.port.pose[0], LIMITS[0][1])
        self.assertEqual(head.request({'neck': 'left'}), 'not_a_number')

    def test_head_requests_are_fractions_inside_the_limits(self):
        commanded = {**dict(TELEPRESENCE_DRIVE_HEAD), 3: 5732}
        self.assertEqual(telepresence_targets({'visor': 0.}, commanded), {3: LIMITS[3][1]})
        self.assertEqual(telepresence_targets({'neck': 0.}, commanded), {0: TELEPRESENCE_DRIVE_HEAD[0]})
        with self.assertRaises(ValueError):
            telepresence_targets({'neck': float('nan')}, commanded)
        self.assertEqual(telepresence_reading(commanded)['neck'], 0.)


class FakeHeadRobot:
    def __init__(self):
        self.log, self.owner = [], SimpleNamespace(commanded=dict(TELEPRESENCE_DRIVE_HEAD))

    def telepresence_pose(self, spec, cancel, request_id):
        self.log.append(('pose', spec))
        return SimpleNamespace(status='pulses_reached', reason=None)

    def telepresence_level(self, cancel):
        self.log.append(('level',))
        return SimpleNamespace(status='pulses_reached', reason=None)


class DriveControlTests(unittest.TestCase):
    def setUp(self):
        from tests.test_drive import base_setup
        self.clock, self.fake, self.drive, _ = base_setup(self)
        self.drive.start()
        self.addCleanup(lambda: self.drive.state != 'closed' and self.drive.close())
        self.messages = []
        self.control = DriveControl(self.drive, notify=self.messages.append, clock=self.clock)
        self.seq = 0

    def send(self, lin, ang=0.):
        self.seq += 1
        return self.control.setpoint({'seq': self.seq, 'lin': lin, 'ang': ang})

    def tick(self, advance=.1):
        self.clock.now += advance
        time.sleep(.005)   # The simulated controller reports at the new time.
        self.control._tick(self.clock(), .1)

    def last_drive(self):
        return [c for c in self.fake.commands if c['cmd'] == 'drive'][-1]

    def test_only_the_newest_setpoint_counts_and_late_ones_are_dropped(self):
        self.assertIsNone(self.control.setpoint({'seq': 5, 'lin': .2, 'ang': 0.}))
        self.assertEqual(self.control.setpoint({'seq': 4, 'lin': 1., 'ang': 0.}), 'out_of_order')
        self.assertEqual(self.control.setpoint({'seq': 5, 'lin': 1., 'ang': 0.}), 'out_of_order')
        self.assertEqual(self.control.setpoint({'seq': 6, 'lin': True, 'ang': 0.}), 'bad_setpoint')
        for seq in (7, 8, 9):   # A burst after a stall: only seq 9 is applied.
            self.control.setpoint({'seq': seq, 'lin': -.5 if seq < 9 else 1., 'ang': 0.})
        self.tick()
        self.assertEqual(self.control.stats['superseded'], 3)
        self.assertEqual(self.control.stats['out_of_order'], 2)
        drive = self.last_drive()
        self.assertGreater(drive['lin'], 0.)                       # Forward, from seq 9.
        self.assertLess(drive['lin'], MANUAL_SPEED)                # Eased in, as the dashboard does.
        self.assertTrue(self.drive.manual_active)
        for _ in range(20):
            self.send(1., 1.)
            self.tick()
        self.assertLessEqual(self.last_drive()['lin'], MANUAL_SPEED)
        self.assertLessEqual(self.last_drive()['ang'], round(MANUAL_TURN, 3))

    def test_a_stale_setpoint_and_a_lost_link_stop_the_wheels_at_once(self):
        self.send(1.)
        self.tick()
        self.assertGreater(self.last_drive()['lin'], 0.)
        self.tick(SETPOINT_STALE_SECONDS + .05)   # Nothing fresh: zero at once, not eased.
        self.assertEqual(self.last_drive()['lin'], 0.)
        self.assertEqual(self.control.refused, 'setpoint_stale')
        self.send(1.)
        self.tick()
        self.control.link_lost()
        self.assertFalse(self.drive.manual_active)
        self.assertEqual(self.fake.commands[-1]['cmd'], 'stop')
        self.assertIsNone(self.control.setpoint({'seq': 1, 'lin': 0., 'ang': 0.}))   # A new link starts afresh.

    def test_a_slow_link_refuses_forward_and_turning_but_lets_him_back_away(self):
        self.control.link_slow = lambda now: 'link_too_slow'
        self.send(1., .5)
        self.tick()
        self.assertFalse(self.drive.manual_active)   # Nothing was wanted, so the wheels were never taken.
        self.assertEqual(self.control.refused, 'link_too_slow')
        self.assertEqual(self.messages[-1]['refused'], 'link_too_slow')
        self.send(-1.)
        self.tick()
        self.assertLess(self.last_drive()['lin'], 0.)

    def test_stop_holds_the_wheels_until_the_stick_is_released(self):
        self.send(1.)
        self.tick()
        self.control.stop()
        self.assertEqual(self.fake.commands[-1]['cmd'], 'stop')
        self.send(1.)
        self.tick()
        self.assertEqual((self.last_drive()['lin'], self.control.refused), (0., 'stopped'))
        self.send(0.)
        self.tick()
        self.send(1.)
        self.tick()
        self.assertGreater(self.last_drive()['lin'], 0.)

    def test_an_obstacle_holds_forward_and_the_reason_reaches_the_caller(self):
        self.fake.front = lambda: .2
        time.sleep(.05)
        for _ in range(3):
            self.send(1.)
            self.tick()
        self.assertEqual(self.last_drive()['lin'], 0.)
        self.assertTrue(self.messages[-1]['held'])
        self.assertEqual(self.messages[-1]['type'], 'drive_state')

    def test_charging_refuses_driving_with_its_reason(self):
        self.fake.charging = True
        time.sleep(.05)
        self.send(1.)
        self.tick()
        self.assertEqual(self.control.refused, 'charging')
        self.assertFalse(self.drive.manual_active)

    def test_setting_off_levels_the_head_first_and_looks_wait_while_he_rolls(self):
        robot = FakeHeadRobot()
        head = HeadControl(robot)
        control = DriveControl(self.drive, head=head, clock=self.clock)
        head.wheels_busy = lambda: control.engaged
        head.start()
        self.addCleanup(head.close)
        control.setpoint({'seq': 1, 'lin': 1., 'ang': 0.})
        self.clock.now += .1
        control._tick(self.clock(), .1)
        self.assertEqual(robot.log, [('level',)])
        self.assertTrue(self.drive.manual_active)
        self.assertEqual(head.request({'neck': .5}), 'head_held_while_driving')
        control.setpoint({'seq': 2, 'lin': 0., 'ang': 0.})
        for _ in range(12):   # Stopped, and settled past the drive owner's motion margin.
            self.clock.now += .1
            time.sleep(.003)
            control._tick(self.clock(), .1)
        self.assertFalse(control.engaged)
        self.assertIsNone(head.request({'neck': .5}))
        wait_until(lambda: ('pose', {'neck': .5}) in robot.log)

    def test_the_worker_steers_on_its_own_and_closing_it_hands_the_wheels_back(self):
        self.control.start()
        self.send(1.)
        wait_until(lambda: self.drive.manual_active and self.last_drive()['lin'] > 0)
        self.control.close()
        self.assertFalse(self.drive.manual_active)
        self.assertEqual(self.fake.commands[-1]['cmd'], 'stop')
        self.assertIsNone(self.control.thread)

    def test_base_readings_are_display_only(self):
        time.sleep(.05)
        with patch.object(self.drive.local_map, 'observe') as observe:
            readings = base_readings(self.drive, self.clock())
        observe.assert_not_called()   # The drive owner's floor memory is its own callers' to fold.
        self.assertTrue(readings['connected'])
        self.assertEqual(readings['volts'], 12.8)
        self.assertIn('front_m', readings['clearance_m'])
        self.assertNotIn('radar', readings)


class CallControlTests(unittest.TestCase):
    def call(self, **controls):
        from r3x.telepresence.call import Call
        server = SimpleNamespace(on_client=None, on_frame=None, on_closed=None)
        clock = CallClock()
        call = Call(server, FakeMic(clock), FakeSpeaker(), Recorder(), clock=clock, **controls)
        link = FakeLink(clock)
        call._connected(link, '10.8.0.2', 'test')
        return call, link

    def test_the_call_routes_head_drive_and_stop_and_a_lost_link_stops_the_wheels(self):
        head, drive = Mock(), Mock(engaged=False)
        head.request.return_value = 'head_held_while_driving'
        head.status.return_value = {'neck': 0.}
        drive.status.return_value = {'moving': False}
        call, link = self.call(head=head, drive=drive, base=lambda now: {'battery_percent': 80})
        call._frame(link, 'J', encode_json({'type': 'drive', 'seq': 1, 'lin': .5, 'ang': 0.})[5:])
        drive.setpoint.assert_called_once_with({'type': 'drive', 'seq': 1, 'lin': .5, 'ang': 0.})
        call._frame(link, 'J', encode_json({'type': 'stop'})[5:])
        drive.stop.assert_called_once()
        call._frame(link, 'J', encode_json({'type': 'head', 'neck': .5})[5:])
        self.assertEqual(link.json[-1], {'type': 'head_state', 'status': 'refused', 'reason': 'head_held_while_driving'})
        status = call.status(1.)
        self.assertEqual((status['head'], status['drive'], status['base']),
                         ({'neck': 0.}, {'moving': False}, {'battery_percent': 80}))
        call._closed(link, 'peer_closed')
        drive.link_lost.assert_called_once()
        head.clear.assert_called_once()

    def test_the_call_starts_its_controls_and_closes_the_wheels_first(self):
        order = []
        head, drive = Mock(), Mock(engaged=False)
        head.close.side_effect = lambda: order.append('head')
        drive.close.side_effect = lambda: order.append('drive')
        call, _ = self.call(head=head, drive=drive)
        stop = threading.Event()
        stop.set()
        call.run(stop)
        head.start.assert_called_once()
        drive.start.assert_called_once()
        self.assertEqual(order, ['drive', 'head'])

    def test_without_servos_or_wheels_the_caller_is_told_so(self):
        call, link = self.call()
        call._frame(link, 'J', encode_json({'type': 'head', 'neck': .5})[5:])
        call._frame(link, 'J', encode_json({'type': 'drive', 'seq': 1, 'lin': 1., 'ang': 0.})[5:])
        self.assertEqual(link.json, [{'type': 'head_state', 'status': 'refused', 'reason': 'head_not_available'}])
        self.assertIsNone(call.status(1.)['drive'])

    def test_steering_waits_for_a_measured_round_trip_and_a_slow_one_refuses(self):
        call, _ = self.call()
        self.assertEqual(call.link_slow(10.), 'link_not_measured')
        self.assertTrue(call.ping_due)   # A new link is pinged at once.
        call.rtts.append(.05)
        self.assertIsNone(call.link_slow(10.))
        call.pings[7] = 9.5   # Unanswered for 0.5 s.
        self.assertEqual(call.link_slow(10.), 'link_too_slow')
        call.pings.clear()
        call.rtts.append(.6)
        self.assertEqual(call.link_slow(10.), 'link_too_slow')


class ProgramControlTests(unittest.TestCase):
    def test_compose_adds_the_wheels_without_their_reflex_or_the_radar(self):
        from r3x.telepresence.program import compose, controls_offer
        settings = Settings(input_device_name='Fixture mic', servo_port='/dev/cu.servo', drive_port='/dev/cu.drive',
                            radar_port='/dev/cu.radar', telepresence_listen='10.0.0.5:47800',
                            telepresence_allowed_networks=['10.8.0.0/24'])
        providers, parts = compose(settings, Secrets(telepresence_token=TOKEN))
        self.assertEqual([p.capability.name for p in providers],
                         ['telepresence_speaker', 'robot_servos', 'drive_base', 'capture', 'telepresence_link'])
        self.assertFalse(parts['drive']._reflex)
        self.assertIsNone(parts['drive'].radar)   # Configured, but never opened for a call (owner, November 16).
        self.assertNotIn('radar', parts)
        self.assertEqual(controls_offer(parts['robot'], parts['drive']), {'head': None, 'drive': None})  # Not started.
        parts['robot'].telepresent = True
        parts['drive'].state = 'ready'
        offer = controls_offer(parts['robot'], parts['drive'])
        self.assertEqual(offer['head']['home']['neck'], 0.)
        self.assertEqual((offer['drive']['stale_ms'], offer['drive']['link_slow_ms']), (300, 400))

    def test_the_wheels_are_offered_only_once_the_arms_hold_the_stance(self):
        from r3x.telepresence.program import call_controls
        drive, robot = Mock(), SimpleNamespace(telepresent=False)
        active = {'drive_base', 'robot_servos'}
        self.assertIsNone(call_controls({'robot': robot, 'drive': drive}, active)['drive'])   # Stance failed.
        self.assertIsNone(call_controls({'robot': robot, 'drive': drive}, {'drive_base'})['drive'])   # Servos down.
        robot.telepresent = True
        controls = call_controls({'robot': robot, 'drive': drive}, active)
        self.assertTrue(controls['drive'].stance_held)
        self.assertIsNotNone(controls['head'])
        unfitted = call_controls({'robot': None, 'drive': drive}, {'drive_base'})['drive']   # No servo board.
        self.assertFalse(unfitted.stance_held)

    def test_each_run_records_how_it_ended(self):
        from r3x.telepresence.program import record_ending
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            record_ending(root, {'reason': 'hangup', 'code': 0})
            reading = trigger.read_json(root / 'telepresence/last-call.json', trigger.LAST_CALL_MAX_AGE)
            self.assertEqual((reading['reason'], reading['code'], reading['seconds_ago']), ('hangup', 0, 0))


class TriggerTests(unittest.TestCase):
    def test_start_only_while_asleep_and_stop_only_a_call_it_started(self):
        def ask(kind, **state):
            return trigger.answer({'type': kind}, call_port=47800, **state)
        asleep, awake, calling = ({'running': False, 'call_child': False}, {'running': True, 'call_child': False},
                                  {'running': True, 'call_child': True})
        self.assertEqual(ask('start', **asleep), ({'type': 'starting', 'call_port': 47800, 'already_running': False}, 'launch'))
        self.assertEqual(ask('start', **awake), ({'type': 'refused', 'reason': 'rex_awake'}, None))
        self.assertEqual(ask('start', **calling)[1], None)
        self.assertTrue(ask('start', **calling)[0]['already_running'])
        self.assertEqual(ask('stop', **calling), ({'type': 'stopping'}, 'stop'))
        self.assertEqual(ask('stop', **awake), ({'type': 'refused', 'reason': 'rex_awake'}, None))
        self.assertEqual(ask('stop', **asleep), ({'type': 'not_running'}, None))
        self.assertEqual([ask('status', **s)[0]['state'] for s in (asleep, awake, calling)],
                         ['asleep', 'awake', 'telepresence'])
        self.assertEqual(ask('dance', **asleep), ({'type': 'refused', 'reason': 'unknown_request'}, None))

    def test_the_trigger_answers_one_signed_in_request_from_the_main_loop(self):
        requests = trigger.Requests()
        listener = trigger.TriggerListener('10.0.0.5', 47801, parse_networks(['10.8.0.0/24']), TOKEN, requests.ask)
        decided = []

        def main_loop():
            while not requests.wake.wait(2.):
                pass
            requests.serve(lambda request: decided.append(request) or {'type': 'status', 'state': 'asleep'})
        loop = threading.Thread(target=main_loop, daemon=True)
        loop.start()
        robot, client = socket_pair()
        served = threading.Thread(target=listener.serve, args=(robot, '10.8.0.9'), daemon=True)
        served.start()
        self.assertEqual(sign_in(client, TOKEN, 'test')['service'], 'telepresence_trigger')
        client.sendall(encode_json({'type': 'status'}))
        self.assertEqual(json.loads(read_frame(client)[1]), {'type': 'status', 'state': 'asleep'})
        served.join(2)
        self.assertEqual(decided, [{'type': 'status'}])

    def test_outsiders_and_wrong_tokens_never_reach_the_main_loop(self):
        ask = Mock()
        listener = trigger.TriggerListener('10.0.0.5', 47801, parse_networks(['10.8.0.0/24']), TOKEN, ask)
        robot, client = socket_pair()
        listener.serve(robot, '192.0.2.4')
        self.assertTrue(client.closed or robot.closed)
        robot, client = socket_pair()
        served = threading.Thread(target=listener.serve, args=(robot, '10.8.0.9'), daemon=True)
        served.start()
        from r3x.telepresence.protocol import ProtocolError
        with self.assertRaises(ProtocolError) as refused:
            sign_in(client, 'another-synthetic-token-0123456789abcdef', 'test')
        self.assertEqual(refused.exception.reason, 'wrong_token')
        served.join(2)
        ask.assert_not_called()

    def test_the_trigger_listens_one_port_above_the_call(self):
        self.assertEqual(trigger.control_address('192.168.1.100:47800'), ('192.168.1.100', 47801))
        with self.assertRaises(ValueError):
            trigger.control_address('192.168.1.100:65535')

    def test_old_or_broken_records_are_not_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'battery.json'
            path.write_text(json.dumps({'percent': 80, 'charging': False, 'at': 1000.}))
            self.assertEqual(trigger.read_json(path, 60, now=1030.)['percent'], 80)
            self.assertIsNone(trigger.read_json(path, 60, now=1100.))
            path.write_text('{not json')
            self.assertIsNone(trigger.read_json(path, 60, now=1030.))
            self.assertIsNone(trigger.read_json(Path(folder) / 'missing.json', 60))


class SupervisorTriggerTests(unittest.TestCase):
    def setUp(self):
        tools = Path(__file__).resolve().parents[1] / 'operator_tools'
        if str(tools) not in sys.path:
            sys.path.insert(0, str(tools))
        import rex_supervisor
        self.supervisor = rex_supervisor

    def test_a_call_launches_the_telepresence_program_not_live(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(self.supervisor, '_CONTROLLER_CONSOLE_LOG', Path(directory) / 'console.log'), \
             patch.object(self.supervisor.subprocess, 'Popen') as popen:
            self.supervisor._launch_controller('Telepresence call requested', mode='telepresence')
        args, kwargs = popen.call_args
        self.assertEqual(args[0][-3:], ['-m', 'r3x', 'telepresence'])
        self.assertEqual(kwargs['env']['DJR3X_LAUNCHED_BY'], 'supervisor-telepresence')

    def test_the_trigger_stays_off_without_an_address_or_token(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory).resolve()
            (local / 'secrets.toml').write_text('ntfy_topic = ""\n')
            (local / 'settings.toml').write_text('telepresence_listen = ""\n')
            with patch.object(self.supervisor.v3_settings, 'SETTINGS', local / 'settings.toml'), \
                 patch.object(self.supervisor.v3_settings, 'LOCAL', local):
                self.assertEqual(self.supervisor._start_telepresence_trigger(trigger.Requests()), (None, None))
                (local / 'settings.toml').write_text('telepresence_listen = "10.0.0.5:47800"\n'
                                                     'telepresence_allowed_networks = ["10.8.0.0/24"]\n')
                self.assertEqual(self.supervisor._start_telepresence_trigger(trigger.Requests()), (None, None))

    def test_the_battery_menu_publishes_its_reading_for_the_status_answer(self):
        import importlib.util
        tools = Path(__file__).resolve().parents[1] / 'operator_tools'
        spec = importlib.util.spec_from_file_location('rex_battery_menubar_publish', tools / 'tools' / 'rex_battery_menubar.py')
        menu = importlib.util.module_from_spec(spec)
        with patch('logging.basicConfig'):
            spec.loader.exec_module(menu)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.supervisor.v3_settings, 'STATE', Path(directory)):
                menu._publish_battery(81.6, True, 13950, now=1000.)
                menu._publish_battery(10, False, 12000, now=1005.)   # Within 10 s: not rewritten.
            record = json.loads((Path(directory) / 'battery.json').read_text())
        self.assertEqual(record, {'percent': 81, 'charging': True, 'volts': 13.95, 'at': 1000.})


if __name__ == '__main__':
    unittest.main()
