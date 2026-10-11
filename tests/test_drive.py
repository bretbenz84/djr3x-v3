"""Drive-base contracts against a simulated V2 ESP32; no serial port or motor opens."""
from collections import deque
import json
import math
import threading
import time
import unittest

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.drive import DriveBase, DrivePlan, DriveStep, Flinch, Radar, LIMITS, CONFIG
from r3x.drive_space import (allowed_turn, clearance, face_range, image_bearing, matrix_sectors,
                             move_allowance, radar_bodies, FACE_WIDTH, HALF_FOV)
from r3x.adapters.esp32 import seam_merge


class Clock:
    """Fake monotonic time; waits advance it and yield briefly to reader threads."""
    def __init__(self):
        self.now = 100.

    def __call__(self):
        return self.now

    def wait(self, seconds):
        self.now += max(0., seconds)
        time.sleep(.002)
        return False


class FakeBase:
    """V2 protocol v1 subset: hello, acks, telemetry, tofmx, finite turn/move, drive, stop."""
    def __init__(self, clock, caps=('drive', 'turn', 'move', 'stop')):
        self.clock, self.caps = clock, list(caps)
        self.lock = threading.Lock()
        self.out = deque()
        self.commands = []
        self.x = self.y = self.theta = 0.   # metres, degrees (+left)
        self.pending = None
        self.turn_scale = 1.
        self.turn_coast = .11               # Seconds the base keeps turning after the firmware ends a turn.
        self.block_after = None
        self.owner, self.state, self.charging, self.fault = 'auto', 'idle', False, None
        self.batt_mv = 12800
        self.tof = {'fl': 3000, 'fr': 3000, 'fl_radial': 3000, 'fr_radial': 3000, 'rl': 2000, 'rr': 2000,
                    'lf': 1500, 'lb': 1500, 'rf': 1500, 'rb': 1500}
        self.front = None                   # callable -> metres to a front obstacle, or None
        self.sensors = None                 # callable -> (ToF overrides in mm, 64-zone grid in mm): shaped rooms
        self.imu_drift = 0.                 # degrees added to IMU yaw per completed turn
        self.matrix = True
        self.assist_gain = 2.               # Firmware wall-steering (V2 calib.h ASSIST_GAIN), config can change it.
        self.assist_on = True               # Off for fixtures whose readings are a pose, not a room that answers motion.
        self.setpoint = None                # (lin, ang, set_at) of the last drive command.
        self.drive_mark = None
        self.rolled = 0.                    # Fraction of the pending move already rolled.
        self.closed = False
        self.last_telemetry = 0.

    def emit(self, **message):
        self.out.append((json.dumps({'v': 1, **message}) + '\n').encode())

    def write(self, data):
        for line in data.decode().splitlines():
            command = json.loads(line)
            with self.lock:
                self.handle(command)
        return len(data)

    def readings(self):
        tof = dict(self.tof)
        front = self.front() if self.front else None
        if front is not None:
            mm = int(max(20, front*1000))
            tof.update(fl=mm, fr=mm, fl_radial=mm, fr_radial=mm)
        grid = None
        if self.sensors:
            overrides, grid = self.sensors()
            tof.update(overrides)
        return tof, grid

    def assist(self, lin):
        """V2 firmware hallway steering (control.cpp hall_assist_correction): centre between
        the side pairs within 450 mm, lean away from what the front pair sees within 900 mm,
        push hard inside 130 mm; capped at 0.6 of max_ang. Forward drive only."""
        if lin <= .02 or not self.assist_on:
            return 0.
        tof, _ = self.readings()
        def nearest(keys, cap):
            return min([tof[k] for k in keys if tof.get(k, -1) >= 0] + [cap])
        turn = self.assist_gain * (nearest(('lf', 'lb'), 450) - nearest(('rf', 'rb'), 450)) / 1000.
        turn += max(-.25, min(.25, self.assist_gain * .7 * (nearest(('fl',), 900) - nearest(('fr',), 900)) / 1000.))
        left, right = nearest(('lf', 'lb'), 32767), nearest(('rf', 'rb'), 32767)
        if left < 130:
            turn -= 12. * (130 - left) / 1000.
        if right < 130:
            turn += 12. * (130 - right) / 1000.
        cap = .6 * math.radians(60)
        return max(-cap, min(cap, turn))

    def _drive_advance(self):
        """Roll the last drive setpoint forward to now; it expires 0.3 s after it was set."""
        if self.setpoint is None:
            return
        lin, ang, set_at = self.setpoint
        until = min(self.clock(), set_at + .3)
        dt = until - self.drive_mark
        if dt > 0:
            self.theta += math.degrees(ang + self.assist(lin)) * dt
            self._roll(lin * dt)
        self.drive_mark = until
        self.linear = lin if self.clock() < set_at + .3 else 0.

    def _finish(self, result, fraction=1.):
        seq, kind, amount = self.pending[:3]
        if kind == 'turn':
            self.theta += amount * fraction * self.turn_scale
            # The real base coasts about 0.11 s past the end of a turn (October logs: 12
            # commanded became 16, 90 became 94-100). Only a full, unstopped turn coasts.
            duration = self.pending[4]
            if fraction >= 1. and duration and abs(amount) >= 5:
                self.theta += math.copysign(abs(amount) / duration * self.turn_coast, amount)
            # Wheel slip: odometry reads more rotation than the base made (October 3 floor logs).
            self.odom_extra = getattr(self, 'odom_extra', 0.) + amount * fraction * getattr(self, 'odom_slip', 0.)
            self.imu_offset = getattr(self, 'imu_offset', 0.) + self.imu_drift
        else:
            self._roll(amount * (fraction - self.rolled))
        self.pending = None
        self.rolled = 0.
        self.state = 'blocked' if result == 'blocked' else 'idle'
        self.emit(type='done', seq=seq, result=result, odom=self.odom())

    def _roll(self, travelled):
        """Move the base in the room, and the wheel odometry in its own (possibly slipped) frame."""
        self.x += travelled * math.cos(math.radians(self.theta))
        self.y += travelled * math.sin(math.radians(self.theta))
        wheel = math.radians(self.theta + getattr(self, 'odom_extra', 0.))
        self.odom_x = getattr(self, 'odom_x', 0.) + travelled * math.cos(wheel)
        self.odom_y = getattr(self, 'odom_y', 0.) + travelled * math.sin(wheel)

    def handle(self, command):
        self.commands.append(command)
        name, seq = command['cmd'], command['seq']
        if name == 'hello':
            self.emit(type='hello', proto=1, fw='test', caps=self.caps, boot_id=7)
        elif name == 'config':
            if isinstance(command.get('assist_gain'), (int, float)):
                self.assist_gain = float(command['assist_gain'])
            # Real firmware echoes every effective parameter: a line well over 512 bytes.
            echo = {key: 0.123456 for key in ('max_lin', 'max_ang', 'slow_zone_m', 'stop_zone_m', 'default_turn_deg',
                'default_turn_rate', 'watchdog_ms', 'drive_expiry_ms', 'manual_idle_return_secs', 'kp', 'ki', 'kd',
                'kff', 'min_duty', 'breakaway_duty', 'accel_lin', 'accel_ang', 'counts_per_meter_l',
                'counts_per_meter_r', 'track_width_m', 'assist_engage_mm', 'assist_gain')}
            self.emit(type='ack', seq=seq, accepted=True, reason=None,
                      config={**echo, 'manual_autoreturn': True, 'assist_enabled': True})
        elif name in {'turn', 'move'}:
            if self.owner == 'manual':
                self.emit(type='ack', seq=seq, accepted=False, reason='manual_override')
                return
            self.emit(type='ack', seq=seq, accepted=True, reason=None)
            if self.pending:
                self._finish('superseded', self._progress())
            self._drive_advance()
            self.setpoint = None
            amount = command['deg'] if name == 'turn' else command['dist']
            rate = command.get('rate', 45.) if name == 'turn' else command.get('speed', .2)
            duration = abs(amount) / rate
            self.pending = (seq, name, amount, self.clock(), duration)
            self.state = 'moving'
        elif name == 'stop':
            self.emit(type='ack', seq=seq, accepted=True, reason=None)
            if self.pending:
                self._finish('superseded', self._progress())
            self._drive_advance()
            self.setpoint, self.linear = None, 0.
            if self.state == 'moving':
                self.state = 'idle'
        elif name == 'drive':
            self.emit(type='ack', seq=seq, accepted=True, reason=None)
            self._drive_advance()
            self.setpoint, self.drive_mark = (command['lin'], command['ang'], self.clock()), self.clock()
            self.linear = command['lin']
            if command['lin'] or command['ang']:
                self.state = 'moving'
            elif self.state == 'moving':
                self.state = 'idle'

    def _progress(self):
        _, _, _, started, duration = self.pending
        return min(1., (self.clock() - started) / duration) if duration else 1.

    def _advance(self):
        self._drive_advance()
        if not self.pending:
            return
        seq, kind, amount, started, duration = self.pending
        if kind == 'move':
            # Rolled as it goes, steered by the firmware's wall assist like the real base.
            progress = self._progress()
            dt = progress * duration - self.rolled * duration
            if dt > 0:
                self.theta += math.degrees(self.assist(amount / duration if amount > 0 else 0.)) * dt
                self._roll(amount * (progress - self.rolled))
                self.rolled = progress
        if kind == 'move' and self.block_after is not None and abs(amount) > self.block_after:
            if self._progress() >= self.block_after / abs(amount):
                self._finish('blocked', self.block_after / abs(amount))
                return
        if self._progress() >= 1.:
            self._finish('completed')

    def odom(self):
        # Without slip the odometry frame is the room; with slip it is consistent only with itself.
        slipped = bool(getattr(self, 'odom_extra', 0.))
        return {'x': round(getattr(self, 'odom_x', 0.) if slipped else self.x, 3),
                'y': round(getattr(self, 'odom_y', 0.) if slipped else self.y, 3),
                'theta': math.radians(self.theta + getattr(self, 'odom_extra', 0.)),
                'lin': getattr(self, 'linear', 0.), 'ang': 0.}

    def read(self, size):
        time.sleep(.0005)
        with self.lock:
            self._advance()
            if time.monotonic() - self.last_telemetry > .002:
                self.last_telemetry = time.monotonic()
                tof, grid = self.readings()
                front = self.front() if self.front else None
                self.emit(type='telemetry', state=self.state, owner=self.owner, fault=self.fault,
                          blocked_dir='front' if self.state == 'blocked' else 'none', odom=self.odom(),
                          tof_mm=tof, batt_mv=self.batt_mv, charging=self.charging,
                          imu={'ok': True, 'pitch': 0, 'roll': 0, 'yaw': self.theta + getattr(self, 'imu_offset', 0.)})
                if self.matrix and grid is not None:
                    self.emit(type='tofmx', g=''.join(f'{min(max(25, v), 4095):03x}' for v in grid), rej=[4095]*8)
                elif self.matrix:
                    mm = int(max(25, front*1000)) if front is not None else 3500
                    self.emit(type='tofmx', g=f'{min(mm, 4095):03x}'*64, rej=[4095]*8)
            lines, self.out = b''.join(self.out), deque()
        return lines

    def close(self):
        self.closed = True


class World:
    """People and objects at fixed room positions, seen through the head camera."""
    def __init__(self, base):
        self.base, self.people, self.objects = base, [], []
        self.neck = 0.   # Head angle, degrees left of the base: the camera turns with it.

    def relative(self, px, py):
        dx, dy = px - self.base.x, py - self.base.y
        heading = math.radians(self.base.theta)
        forward = dx*math.cos(heading) + dy*math.sin(heading)
        left = -dx*math.sin(heading) + dy*math.cos(heading)
        return math.hypot(forward, left), math.degrees(math.atan2(left, forward)), forward, left

    def faces(self, now):
        rows = []
        for track, identity, px, py in self.people:
            distance, bearing, _, _ = self.relative(px, py)
            bearing -= self.neck   # Camera-relative.
            if abs(bearing) >= HALF_FOV or distance < .3:
                continue
            width = FACE_WIDTH / (2*math.tan(math.radians(HALF_FOV))*distance)
            centre = .5 - bearing/(2*HALF_FOV)
            rows.append({'track_id': track, 'identity': identity, 'face_epoch': 0, 'at': now,
                         'box': [centre - width/2, .3, width, width*1.2]})
        return rows

    def detections(self):
        rows = []
        for target, label, px, py in self.objects:
            distance, bearing, _, _ = self.relative(px, py)
            if abs(bearing) >= HALF_FOV:
                continue
            width = min(.9, .3/max(distance, .2))
            centre = .5 - bearing/(2*HALF_FOV)
            rows.append({'target_id': target, 'label': label, 'box': [centre - width/2, .4, width, width], 'samples': 3})
        return rows

    def front_obstacle(self):
        values = [forward - .27 for _, _, px, py in self.objects
                  for _, _, forward, left in [self.relative(px, py)] if forward > 0 and abs(left) < .35]
        return min(values) if values else None


def base_setup(test, *, caps=None, radar=None, reflex=False):
    clock = Clock()
    fake = FakeBase(clock, caps) if caps else FakeBase(clock)
    drive = DriveBase('fake', open_port=lambda: fake, clock=clock, wait=clock.wait, radar=radar, reflex=reflex)
    world = World(fake)
    drive.faces = world.faces
    drive.objects = world.detections
    fake.front = world.front_obstacle
    return clock, fake, drive, world


def settle():
    time.sleep(.03)  # Let the simulated controller publish telemetry for a changed room.


def plan(*steps, origin='requested', targets=None, clock=None):
    return DrivePlan('plan-1', tuple(steps), origin, 'test', targets or {}, (clock() if clock else 100.) + 60)


class DriveOwnerTests(unittest.TestCase):
    def start(self, **kwargs):
        self.clock, self.fake, self.drive, self.world = base_setup(self, **kwargs)
        self.drive.start()
        self.addCleanup(self.close)

    def close(self):
        if self.drive.state != 'closed':
            self.drive.close()

    def test_connect_pushes_owner_limits_heartbeats_and_stops_on_close(self):
        self.start()
        self.assertEqual(self.drive.state, 'ready')
        config_ack = next(a for a in self.drive.link.acks.values() if 'config' in a)
        self.assertGreater(len(json.dumps(config_ack)), 512)
        config = next(c for c in self.fake.commands if c['cmd'] == 'config')
        self.assertEqual(config['max_lin'], .25)
        self.assertEqual(config, {**CONFIG, 'v': 1, 'cmd': 'config', 'seq': config['seq']})
        deadline = time.monotonic() + 1.
        while not any(c['cmd'] == 'ping' for c in self.fake.commands) and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(any(c['cmd'] == 'ping' for c in self.fake.commands))
        self.drive.close()
        self.assertEqual(self.fake.commands[-1]['cmd'], 'stop')
        self.assertTrue(self.fake.closed)
        # Connecting never commands motion.
        self.assertFalse(any(c['cmd'] in {'turn', 'move', 'drive'} for c in self.fake.commands))

    def test_missing_firmware_capability_refuses_start(self):
        clock, fake, drive, _ = base_setup(self, caps=('drive', 'stop'))
        with self.assertRaisesRegex(Exception, 'DriveCapabilitiesMissing'):
            drive.start()
        drive.link.close()

    def test_turn_is_measured_but_never_backtracked(self):
        self.start()
        self.fake.turn_scale = .8   # Sensors disagree with the command; no host correction follows.
        receipt = self.drive.execute(plan(DriveStep('turn', degrees=45.)), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        step = receipt.steps[0]
        self.assertEqual(step['commanded_deg'], 45.)
        self.assertAlmostEqual(step['measured_deg'], 36., delta=2.)
        self.assertAlmostEqual(step['odometry_deg'], 36., delta=2.)
        turns = [c for c in self.fake.commands if c['cmd'] == 'turn']
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]['rate'], 45.)

    def test_pace_slows_turns_and_moves_and_never_exceeds_the_approved_speed(self):
        # Owner, October 3: his moves need not always be the same speed, turns included.
        self.start()
        receipt = self.drive.execute(plan(DriveStep('turn', degrees=30., pace='slow'), DriveStep('move', meters=.3, pace='easy'),
                                          DriveStep('turn', degrees=-30.), DriveStep('move', meters=.3, pace='normal')), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        turns = [c['rate'] for c in self.fake.commands if c['cmd'] == 'turn']
        moves = [c['speed'] for c in self.fake.commands if c['cmd'] == 'move']
        self.assertEqual(turns, [27., 45.])
        self.assertEqual([round(v, 3) for v in moves], [.2, .25])

    def test_move_is_shortened_to_front_clearance(self):
        self.start()
        self.world.objects.append(('object-a', 'chair', 1.27, 0.))  # 1.0 m of front clearance.
        settle()
        receipt = self.drive.execute(plan(DriveStep('move', meters=1.5)), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        self.assertEqual(receipt.steps[0]['commanded_m'], .65)
        self.assertEqual(receipt.steps[0]['reason'], 'shortened_by_clearance')
        self.assertAlmostEqual(self.fake.x, .65, places=2)
        move = next(c for c in self.fake.commands if c['cmd'] == 'move')
        self.assertLessEqual(move['speed'], .25)

    def test_blocked_move_ends_the_plan_as_partial(self):
        self.start()
        self.fake.block_after = .3
        receipt = self.drive.execute(plan(DriveStep('move', meters=1.), DriveStep('turn', degrees=90.)), Cancellation())
        self.assertEqual(receipt.status, 'partial')
        self.assertEqual([s['status'] for s in receipt.steps], ['blocked'])
        self.assertAlmostEqual(receipt.travelled_m, .3, places=2)
        self.assertFalse(any(c['cmd'] == 'turn' for c in self.fake.commands))
        # October 10: a move that did not go as asked keeps the clearance that decided it.
        self.assertIn('path_m', receipt.steps[0]['clearance_m'])

    def test_swing_check_refuses_a_turn_that_sweeps_an_arm_into_a_shelf(self):
        self.start()
        self.fake.tof.update(lb=100, rl=300, rr=300)   # Shelf behind-left; no room to back up.
        self.world.objects.append(('wall', 'wall', .7, 0.))   # Nor to pull forward.
        settle()
        receipt = self.drive.execute(plan(DriveStep('turn', degrees=90.)), Cancellation())
        self.assertEqual(receipt.status, 'refused')
        self.assertIn('turn_path_blocked', receipt.reason)
        self.assertFalse(any(c['cmd'] == 'turn' for c in self.fake.commands))

    def test_blocked_turn_backs_up_to_earn_room_then_turns(self):
        self.start()
        self.fake.tof['lb'] = 100
        original = self.fake.handle
        def handle(command):
            if command['cmd'] == 'move':
                self.fake.tof['lb'] = 1500   # Backing away clears the shelf.
            original(command)
        self.fake.handle = handle
        settle()
        receipt = self.drive.execute(plan(DriveStep('turn', degrees=90.)), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        self.assertLess(receipt.steps[0]['escape']['commanded_m'], 0)
        self.assertEqual([c['cmd'] for c in self.fake.commands if c['cmd'] in {'move', 'turn'}], ['move', 'turn'])

    def test_imu_drift_without_odometry_agreement_is_not_corrected(self):
        self.start()
        self.fake.imu_drift = 11.   # IMU says the right turn fell 11 degrees short; wheels disagree.
        receipt = self.drive.execute(plan(DriveStep('turn', degrees=-20.)), Cancellation())
        step = receipt.steps[0]
        self.assertNotIn('correction', step)
        self.assertAlmostEqual(step['odometry_deg'], -20., delta=1.)
        self.assertAlmostEqual(step['measured_deg'], -9., delta=1.)
        self.assertEqual(len([c for c in self.fake.commands if c['cmd'] == 'turn']), 1)

    def test_charging_gamepad_and_missing_depth_refuse_before_motion(self):
        self.start()
        for change, reason in ((lambda: setattr(self.fake, 'charging', True), 'charging'),
                               (lambda: setattr(self.fake, 'owner', 'manual'), 'gamepad_in_control'),
                               (lambda: setattr(self.fake, 'matrix', False), 'front_depth_matrix_unavailable')):
            self.fake.charging, self.fake.owner, self.fake.matrix = False, 'auto', True
            change()
            self.clock.wait(1.2)
            time.sleep(.02)
            receipt = self.drive.execute(plan(DriveStep('move', meters=.3), clock=self.clock), Cancellation())
            self.assertEqual((receipt.status, receipt.reason), ('refused', reason))
        self.assertFalse(any(c['cmd'] in {'turn', 'move'} for c in self.fake.commands))

    def test_unplugged_surface_charge_releases_on_next_telemetry(self):
        self.start()
        self.fake.batt_mv = 13610  # Recorded fresh-pack surface-charge trap.
        self.fake.charging = True
        settle()
        receipt = self.drive.execute(plan(DriveStep('move', meters=.3), clock=self.clock), Cancellation())
        self.assertEqual((receipt.status, receipt.reason), ('refused', 'charging'))
        self.assertFalse(any(c['cmd'] == 'move' for c in self.fake.commands))

        # Firmware has completed discharge proof or accepted an operator assertion.
        # Host must not add voltage-decay time or another release timer.
        self.fake.charging = False
        settle()
        self.assertEqual(self.drive.availability(), (True, None))
        receipt = self.drive.execute(plan(DriveStep('move', meters=.3), clock=self.clock), Cancellation())
        self.assertEqual(receipt.status, 'completed')

    def test_charger_voltage_backstop_refuses_even_before_firmware_latches(self):
        self.start()
        for mv in (14000, 14200):
            with self.subTest(mv=mv):
                self.fake.batt_mv = mv
                settle()
                receipt = self.drive.execute(plan(DriveStep('move', meters=.3), clock=self.clock), Cancellation())
                self.assertEqual((receipt.status, receipt.reason), ('refused', 'charging'))
        self.assertFalse(any(c['cmd'] == 'move' for c in self.fake.commands))

    def test_stop_halts_a_running_plan_and_commands_a_stop(self):
        self.start()
        result = {}
        worker = threading.Thread(target=lambda: result.update(r=self.drive.execute(
            plan(DriveStep('move', meters=1.5)), Cancellation())))
        worker.start()
        deadline = time.monotonic() + 2
        while not any(c['cmd'] == 'move' for c in self.fake.commands) and time.monotonic() < deadline:
            time.sleep(.005)
        self.drive.cancel()
        worker.join(3)
        self.assertEqual(result['r'].status, 'cancelled')
        self.assertIn('stop', [c['cmd'] for c in self.fake.commands[self.fake.commands.index(
            next(c for c in self.fake.commands if c['cmd'] == 'move')):]])
        self.assertLess(self.fake.x, 1.5)

    def test_face_step_turns_to_the_camera_bearing(self):
        self.start()
        self.world.people.append(('face-1', None, 2., math.tan(math.radians(20))*2.))  # ~20 degrees left
        targets = {'face-1': {'target_id': 'face-1', 'kind': 'face', 'person_id': None}}
        receipt = self.drive.execute(plan(DriveStep('face', target='face-1'), targets=targets), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        self.assertAlmostEqual(self.fake.theta, 20., delta=6.)
        self.assertEqual(receipt.steps[0]['faces_visible_after'], 1)

    def test_person_approach_stops_near_the_requested_standoff(self):
        self.start()
        self.world.people.append(('face-1', 'person-1', 2.6, 0.))
        targets = {'face-1': {'target_id': 'face-1', 'kind': 'face', 'person_id': 'person-1'}}
        receipt = self.drive.execute(plan(DriveStep('approach', target='face-1'), targets=targets), Cancellation())
        self.assertEqual(receipt.status, 'completed', receipt)
        distance = math.hypot(2.6 - self.fake.x, self.fake.y)
        self.assertGreater(distance, .85)
        self.assertLess(distance, 1.35)
        self.assertEqual(self.fake.commands[-1]['cmd'], 'stop')
        self.assertTrue(all(c['lin'] <= .25 for c in self.fake.commands if c['cmd'] == 'drive'))

    def test_object_approach_arrives_at_the_front_standoff_not_a_collision(self):
        self.start()
        self.world.objects.append(('object-dog', 'dog', 2., 0.))
        targets = {'object-dog': {'target_id': 'object-dog', 'kind': 'object', 'label': 'dog'}}
        receipt = self.drive.execute(plan(DriveStep('approach', target='object-dog'), targets=targets), Cancellation())
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertGreater(self.world.front_obstacle(), .2)

    def test_find_person_turns_toward_radar_bodies_before_sweeping(self):
        radar = FakeRadar()
        self.start(radar=radar)
        radar.bodies_list = [{'target_id': 'radar:-120', 'bearing_deg': -120., 'range_m': 2., 'confidence': .7,
                              'moving': True, 'frames': 5}]
        self.world.people.append(('face-9', None, -1., -1.73))   # behind on the right, ~-120 degrees
        receipt = self.drive.execute(plan(DriveStep('find_person')), Cancellation())
        self.assertEqual(receipt.status, 'completed', receipt)
        first = next(c for c in self.fake.commands if c['cmd'] == 'turn')
        self.assertLess(first['deg'], -100)
        self.assertEqual(receipt.steps[0]['reason'], 'person_in_view')

    def test_radar_approach_turns_then_approaches_the_face_it_finds(self):
        radar = FakeRadar()
        self.start(radar=radar)
        radar.bodies_list = [{'target_id': 'radar:+150', 'bearing_deg': 150., 'range_m': 2.5, 'confidence': .6,
                              'moving': True, 'frames': 4}]
        self.world.people.append(('face-2', None, -2.6, 1.5))
        targets = {'radar:+150': {'target_id': 'radar:+150', 'kind': 'radar_body', 'bearing_deg': 150., 'range_m': 2.5}}
        receipt = self.drive.execute(plan(DriveStep('approach', target='radar:+150'), targets=targets), Cancellation())
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertLess(math.hypot(-2.6 - self.fake.x, 1.5 - self.fake.y), 1.5)

    def test_flinch_needs_radial_corroboration_and_backs_off_within_rear_room(self):
        self.start()
        flinch = Flinch()
        open_front = {'fl': .9, 'fr': .9, 'fl_radial': .9, 'fr_radial': None}
        self.assertFalse(flinch.observe(open_front, 0.))
        matrix_only = {'fl': .15, 'fr': .9, 'fl_radial': .9, 'fr_radial': None}
        self.assertFalse(any(flinch.observe(matrix_only, t) for t in (1., 2., 3., 4.)))
        self.drive.flinch = Flinch()
        for _ in range(2):
            self.drive.tick_reflex()
        self.fake.tof.update(fl=150, fr=150, fl_radial=150)
        receipts = []
        for _ in range(4):
            self.clock.wait(1.)
            settle()
            receipts.append(self.drive.tick_reflex())
        receipt = next(r for r in receipts if r)
        self.assertEqual(receipt.status, 'completed')
        move = next(c for c in self.fake.commands if c['cmd'] == 'move')
        self.assertAlmostEqual(move['dist'], -.3)


class FakeRadar:
    def __init__(self):
        self.bodies_list = []

    def bodies(self, window=1.5):
        return list(self.bodies_list)


class ArmParkTests(unittest.TestCase):
    def test_arm_parks_and_neck_centers_before_wheels_then_returns_to_rest(self):
        from tests.test_servos import Clock as ServoClock, PulsePort
        from r3x.robot_servos import RobotServos
        from r3x.servo_poses import AWAKE_ARMS, DRIVE_ARMS, PARK, POKER, POKER_DRIVE, POKER_UP, REST
        servo_clock, port = ServoClock(), PulsePort()
        robot = RobotServos('fake', open_port=lambda: port, clock=servo_clock, wait=servo_clock.wait)
        robot.owner.wait = servo_clock.wait
        robot.start()
        self.addCleanup(robot.close)
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        drive.servos = robot
        seen = {}
        original = fake.handle
        def handle(command):
            if command['cmd'] == 'turn' and 'throttle' not in seen:
                seen['throttle'] = {ch: port.pose[ch] for ch, _ in PARK}
                seen['neck'] = port.pose[0]
                seen['arms'] = {ch: port.pose[ch] for ch, _ in DRIVE_ARMS + POKER_DRIVE}
            original(command)
        fake.handle = handle
        receipt = drive.execute(plan(DriveStep('turn', degrees=30.)), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        self.assertEqual(seen['throttle'], dict(PARK))
        self.assertEqual(seen['neck'], 5472)
        self.assertEqual(seen['arms'], dict(DRIVE_ARMS + POKER_DRIVE))   # The whole stance, before the wheels.
        robot._arm_return.join(5)
        self.assertEqual({ch: port.pose[ch] for ch, _ in REST}, dict(REST))
        # October 10: the poker arm and hero elbow come back to their awake mid points too.
        self.assertEqual({ch: port.pose[ch] for ch in (4, 6)}, dict(AWAKE_ARMS))
        self.assertEqual({ch: port.pose[ch] for ch in POKER}, dict(POKER_UP))
        self.assertFalse(robot.owner.held_channels & {0, 4, 6, 8, 9, 10, 12, 13})


class ArmClearanceTests(unittest.TestCase):
    """Owner, October 11: an arm with something within its reach stays tucked after a drive."""
    def setUp(self):
        from tests.test_servos import Clock as ServoClock, PulsePort
        from r3x.robot_servos import RobotServos
        servo_clock, self.port = ServoClock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda: self.port, clock=servo_clock, wait=servo_clock.wait)
        self.robot.owner.wait = servo_clock.wait
        self.robot.start()
        self.addCleanup(self.robot.close)
        self.clock, self.fake, self.drive, self.world = base_setup(self)
        self.drive.start()
        self.addCleanup(self.drive.close)
        self.drive.servos = self.robot

    def pose(self, channels):
        return {ch: self.port.pose[ch] for ch in channels}

    def drive_and_return(self):
        receipt = self.drive.execute(plan(DriveStep('turn', degrees=30.)), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        self.robot._arm_return.join(5)

    def test_a_wall_on_the_left_keeps_the_poker_arm_raised_until_it_is_clear(self):
        from r3x.servo_poses import AWAKE_ARMS, DRIVE_ARMS, POKER, POKER_DRIVE, POKER_UP, REST, THROTTLE
        self.fake.tof.update(lf=900, lb=1300)   # 3 ft from the left side: inside the 3.5 ft keep-clear.
        settle()
        self.drive_and_return()
        self.assertEqual(self.pose((6, *POKER)), {6: dict(DRIVE_ARMS)[6], **dict(POKER_DRIVE)})
        self.assertEqual(self.pose(THROTTLE), dict(REST))              # The front is open: the throttle comes out.
        self.assertEqual(self.pose((4,)), {4: dict(AWAKE_ARMS)[4]})    # The hero elbow always returns.
        self.assertEqual(self.robot.kept_arms, {'poker'})
        self.assertEqual(self.drive.tick_arm_clearance(), None)       # Still blocked: nothing moves.
        out = self.robot.owner.execute(ServoRequestFor(self.robot, 'poker_out'), Cancellation())
        self.assertEqual((out.status, out.reason), ('rejected', 'ArmKeptClearOfObstacle:poker'))
        self.fake.tof.update(lf=1500, lb=1500)
        settle()
        self.assertIsNone(self.drive.tick_arm_clearance())            # Clear, but not yet for long enough.
        self.clock.now += 2.
        settle()
        self.assertEqual(self.drive.tick_arm_clearance(), {'poker'})
        self.robot._arm_return.join(5)
        self.assertEqual(self.pose((6, *POKER)), {6: dict(AWAKE_ARMS)[6], **dict(POKER_UP)})
        self.assertEqual(self.robot.kept_arms, frozenset())
        self.assertFalse(self.robot.owner.held_channels & {6, *POKER})

    def test_something_close_in_front_keeps_the_throttle_parked(self):
        from r3x.servo_poses import AWAKE_ARMS, PARK, POKER, POKER_UP, THROTTLE
        self.fake.front = lambda: .45    # Something 1.5 ft in front of the sensors, wherever he faces.
        settle()
        self.drive_and_return()
        self.assertEqual(self.pose(THROTTLE), dict(PARK))
        self.assertEqual(self.pose((6, *POKER)), {6: dict(AWAKE_ARMS)[6], **dict(POKER_UP)})
        self.assertEqual(self.robot.kept_arms, {'throttle'})
        self.assertTrue(set(THROTTLE) <= self.robot.owner.held_channels)   # Idle and talking leave it.

    def test_beyond_reach_both_arms_come_out_as_before(self):
        from r3x.servo_poses import REST, THROTTLE
        self.fake.tof.update(lf=1200, lb=1200)   # Just beyond 3.5 ft.
        self.fake.front = lambda: .8     # Beyond 2 ft in front.
        settle()
        self.drive_and_return()
        self.assertEqual(self.robot.kept_arms, frozenset())
        self.assertEqual(self.pose(THROTTLE), dict(REST))


def ServoRequestFor(robot, pose):
    from r3x.servos import ServoRequest
    owner = robot.owner
    return ServoRequest('pose-test', owner.version, 'pose', owner.clock() + 30., pose=pose)


class GeometryTests(unittest.TestCase):
    def test_matrix_bands_ignore_floor_and_single_speckles(self):
        grid = [3500]*64
        grid[10] = 400                      # one speckle in the left band
        grid[4*8+3] = grid[5*8+4] = 900     # two centre zones: a real obstacle
        grid[7*8+6] = 300                   # bottom-row floor return beyond its threshold
        rej = [4095]*7 + [250]
        sectors = matrix_sectors({'grid': grid, 'rej': rej})
        self.assertEqual(sectors, {'left': 3.5, 'center': .9, 'right': 3.5})

    def test_lone_zones_scattered_across_a_band_do_not_block_the_path(self):
        # October 10 (run b954c14c): every column read open to the model, yet path_m was
        # 0.16 m from single zones in different columns of one band, and "come here" stalled.
        from r3x.drive_space import matrix_path, open_directions
        grid = [3500]*64
        grid[1*8+5] = grid[4*8+7] = grid[6*8+6] = 160       # Three lone near zones, none touching.
        matrix = {'grid': grid, 'rej': [4095]*8, 'received_at': 0.}
        self.assertEqual(matrix_path(matrix), {'left': 3.5, 'center': 3.5, 'right': 3.5})
        self.assertTrue(all(row['open_m'] >= 3.5 for row in open_directions(None, matrix, 0.)))
        grid[4*8+6] = 170                                   # A neighbour agrees: something is there.
        self.assertAlmostEqual(matrix_path(matrix)['right'], .16)
        self.assertEqual(matrix_sectors(matrix)['right'], .16)

    def test_unknown_rear_or_depth_refuses_instead_of_assuming_clear(self):
        space = {'front_m': None, 'rear_m': None, 'health': ['front_depth_matrix_unavailable']}
        self.assertEqual(move_allowance(.5, space), (0., 'front_clearance_unknown'))
        self.assertEqual(move_allowance(-.5, space), (0., 'rear_clearance_unknown'))
        self.assertEqual(allowed_turn(45., {}), (0., 'turn_clearance_unknown'))

    def test_wide_angle_camera_bearing_range_and_neck_offset(self):
        # September 27 calibration: a 20 degree turn moved a face 22.5% of the frame.
        self.assertAlmostEqual(image_bearing([.275, .3, .0, .0]) - image_bearing([.5, .3, .0, .0]), 20.25)
        self.assertAlmostEqual(image_bearing([.4, .3, .2, .2], neck_deg=15.), 15.)
        self.assertAlmostEqual(face_range([.468, .3, .064, .1]), 1.25, places=2)   # The owner's 4-5 ft.
        self.assertIsNone(face_range([.4, .3, 0, .2]))

    def test_radar_bodies_need_persistence_and_merge_seams(self):
        target = {'bearing_deg': 90., 'range_m': 2., 'confidence': .8, 'speed_mps': .3, 'sensors': 1}
        flicker = {'bearing_deg': -40., 'range_m': 1., 'confidence': .9, 'speed_mps': .3, 'sensors': 2}
        frames = [(t, [dict(target)] + ([dict(flicker)] if t == 0 else [])) for t in range(4)]
        bodies = radar_bodies(frames)
        self.assertEqual([b['target_id'] for b in bodies], ['radar:+90'])
        seam = seam_merge([{'bearing_deg': 118., 'range_m': 2., 'confidence': .4, 'speed_mps': 0., 'sensors': 1},
                           {'bearing_deg': 124., 'range_m': 2.2, 'confidence': .4, 'speed_mps': 0., 'sensors': 2}])
        self.assertEqual(len(seam), 1)
        self.assertEqual(seam[0]['sensors'], 3)

    def test_clearance_reports_front_right_sensor_fault(self):
        telemetry = {'received_at': 1., 'tof_mm': {'fl': 900, 'fr': 700, 'fl_radial': 1800, 'fr_radial': -1,
                                                   'rl': 900, 'rr': 1000, 'lf': 500, 'lb': 600, 'rf': 400, 'rb': 450}}
        space = clearance(telemetry, {'grid': [3500]*64, 'rej': [4095]*8, 'received_at': 1.}, 1.)
        self.assertIn('front_right_radial_unavailable', space['health'])
        self.assertEqual(space['front_m'], 1.8)
        self.assertEqual((space['left_m'], space['right_m'], space['rear_m']), (.5, .4, .9))


if __name__ == '__main__':
    unittest.main()
