"""What the drive controller's other sensors are doing, for the sensor diagnostics window (Qt-free).

Owner, November 22: "this sensor needs to be addressed. The menu bar app has a ToF utility menu. Can
you add something similar for the other sensors?" That day's run failed because the gyroscope's zero
was wrong by 19.7 degrees a second: the sensor wiring bus had been dead since 00:25, came back while
Rex's wake animation shook him, and the firmware took "not turning" from one reading. Nothing on
screen showed any of it. The battery menu only looked empty, because the battery gauge on the same
bus had not answered at the controller's start.

Fed the controller's lines exactly as the ToF model is (tof_model.py). It works out, in plain terms:

- whether the wheels are still (encoders and commanded speed), and so how fast the gyroscope's heading
  drifts at rest, which is the one number that says whether its zero is right;
- whether the gyroscope and the wheels agreed about the last move;
- how steady the compass reads at rest (raw and uncalibrated: steadiness, not north);
- which chips on the sensor bus answer right now, and which dropped out while watched;
- when the controller last restarted, because the gyroscope's zero and the battery gauge are only
  set up then.

It decides nothing for the robot and sends nothing.
"""
from __future__ import annotations

from collections import deque
import math

from .controller_feed import ControllerFeed

HISTORY_S = 60.          # How much of each reading is kept and drawn.
REST_NEEDED_S = 3.       # Wheels still this long before a drift figure is given...
DRIFT_WINDOW_S = 10.     # ...and it is measured over at most this much of the stillness.
# Degrees a second the gyroscope's heading moves with the wheels still. November 20, standing: 0.05 to
# 0.19. November 22, zero taken mid-shake: 19.7, which halved left turns and ran right turns away.
DRIFT_NORMAL = .3
DRIFT_BAD = 1.           # From here a quarter turn lands several degrees off.
JUMP_DEG = 15.           # This much between two frames with the wheels still: the sensor started over.
STILL_SPEED = .003       # m/s at a wheel, and
STILL_TURN = .01         # rad/s of the base, under which the wheels count as still.
MOVE_SETTLE_S = .5       # Still this long after moving before the move is summed up.
MOVE_TURN_DEG = 20.      # A move turning less than this says nothing about gyroscope against wheels.
COMPASS_UNSTEADY_DEG = 3.
STUCK_S = 1.             # A wheel driven this long without turning.
STUCK_DUTY = 240         # Of the firmware's 1023 (PWM_DUTY_MAX, 10 bits); below it a wheel may just be holding.
MATRIX_STALE_S = 1.
RING = ('fl', 'lf', 'lb', 'rl', 'rr', 'rb', 'rf', 'fr')
CORNERS = ('fl', 'fr', 'rl', 'rr')
# Controller messages that are about the sensor bus.
BUS_WORDS = ('i2c', 'bus restarted', 'imu:', 'no ack', 'read errors', 'battery:', 'mag:', 'env:', 'not answering')


def wrap(degrees):
    return (degrees + 180.) % 360. - 180.


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def span_words(seconds):
    """A length of time in words: 45 s, 12 min, 3 h 20 min."""
    seconds = max(0., seconds)
    if seconds < 90:
        return f'{seconds:.0f} s'
    if seconds < 5400:
        return f'{seconds / 60:.0f} min'
    return f'{int(seconds // 3600)} h {int(seconds % 3600 // 60)} min'


class Trace:
    """One reading over the kept minute: (host time, value or None for no answer)."""
    def __init__(self):
        self.points = deque()

    def note(self, value, now):
        self.points.append((now, value))
        while self.points and now - self.points[0][0] > HISTORY_S:
            self.points.popleft()

    @property
    def value(self):
        return self.points[-1][1] if self.points else None

    def span(self):
        values = [v for _, v in self.points if v is not None]
        return (min(values), max(values)) if values else None


class Device:
    """One chip on the sensor bus: whether it answers, and how often it stopped while watched."""
    def __init__(self, name, part, startup_only=False):
        self.name, self.part = name, part
        self.startup_only = startup_only   # Set up only when the controller starts: it cannot come back by itself.
        self.ok = None
        self.dropouts = 0
        self.lost_at = None

    def note(self, ok, now):
        ok = bool(ok)
        if self.ok and not ok:
            self.dropouts += 1
        if not ok and self.lost_at is None:
            self.lost_at = now
        if ok:
            self.lost_at = None
        self.ok = ok


class SensorDiagnostics(ControllerFeed):
    def __init__(self):
        super().__init__()
        self.traces = {key: Trace() for key in ('heading', 'wheel_heading', 'pitch', 'roll', 'compass', 'field',
                                                 'left', 'right')}
        self.devices = {
            'imu': Device('Gyroscope and tilt', 'LSM6DS3'),
            'compass': Device('Compass', 'QMC5883L'),
            'climate': Device('Climate', 'BME280'),
            'battery': Device('Battery gauge', 'INA226', startup_only=True),
            'ring': Device('Ring range sensors', 'eight VL53L1X'),
            'front': Device('Front 8×8 depth', 'VL53L5CX'),
            'rear': Device('Rear 8×8 depth', 'VL53L5CX'),
        }
        self.ring_answering = None
        self.matrix_at = {'front': None, 'rear': None}
        # Heading by the gyroscope and by the wheels, unwrapped so a trace does not jump at 180.
        self.heading = self.wheel_heading = self.compass = None
        self._raw = {}                 # Last wrapped value of each angle, for unwrapping.
        self.still_since = None        # Host time the wheels were last seen to stop; None while they move.
        self.rest = deque()            # (controller seconds, unwrapped heading) of this stillness.
        self.compass_rest = deque()    # (host time, unwrapped compass direction) of this stillness.
        self.heading_jumps = 0
        self.jumped_at = None
        # Firmware 0.2.24 and later: the zero it subtracts (deg/s) and how long ago it measured it standing
        # still (seconds; -1 not since the controller or the chip started). None on older firmware.
        self.zero = self.zero_age = None
        self._move = None              # The move under way: where it began.
        self.last_move = None          # {'gyro', 'wheels', 'seconds', 'metres', 'ended', 'drift'}
        self._drift_before = None      # The drift measured before the move under way began.
        self.stuck = {'left': None, 'right': None}      # Host time a driven wheel stopped turning.
        self.stuck_seen = {'left': None, 'right': None} # Host time it was last caught at it.
        self.uptime = None             # Seconds since the controller started.
        self.restarts = 0
        self.restarted_at = None
        self._errs0 = None
        self.errs = 0                  # Garbled lines the controller counted since the window opened.
        self.bus_events = 0            # Controller messages about the sensor bus while watching.
        self.last_bus = None           # (host time, text)

    # ------------------------------------------------------------------ input
    def feed_line(self, line, now):
        seen = self.log_total
        super().feed_line(line, now)
        if self.log_total != seen:
            text = self.log[-1][2]
            if any(word in text.lower() for word in BUS_WORDS):
                self.bus_events += 1
                self.last_bus = (now, text)

    def _message(self, kind, line, now):
        if kind == 'telemetry':
            self._telemetry(line, now)
        elif kind in ('tofmx', 'tofmx_rear'):
            self.matrix_at['front' if kind == 'tofmx' else 'rear'] = now

    def _unwrapped(self, key, degrees, current):
        last = self._raw.get(key)
        self._raw[key] = degrees
        if last is None or current is None:
            return degrees, 0.
        step = wrap(degrees - last)
        return current + step, step

    def _telemetry(self, message, now):
        self.telemetry, self.telemetry_at = message, now
        t = message.get('t')
        seconds = t / 1000. if number(t) else None
        if seconds is not None:
            if self.uptime is not None and seconds < self.uptime - 1.:
                # The controller started over: its gyroscope zero and battery gauge were just set up afresh.
                self.restarts += 1
                self.restarted_at = now
                self._raw.clear()
                self.heading = self.wheel_heading = self.compass = None
                self.rest.clear(); self.compass_rest.clear()
                self._move = self.last_move = None
            self.uptime = seconds
        errs = message.get('errs')
        if number(errs):
            if self._errs0 is None or errs < self._errs0:
                self._errs0 = errs
            self.errs = errs - self._errs0

        odom = message.get('odom') if isinstance(message.get('odom'), dict) else {}
        wheels = message.get('wheels') if isinstance(message.get('wheels'), dict) else {}
        left, right = wheels.get('vl'), wheels.get('vr')
        self.traces['left'].note(float(left) if number(left) else None, now)
        self.traces['right'].note(float(right) if number(right) else None, now)
        moving = any(number(v) and abs(v) > limit for v, limit in (
            (left, STILL_SPEED), (right, STILL_SPEED), (odom.get('lin'), STILL_SPEED), (odom.get('ang'), STILL_TURN)))
        was_still = self.still_since is not None
        stopped = not moving and not was_still     # The first frame with the wheels still.
        if moving:
            self.still_since = None
        elif not was_still:
            self.still_since = now
        for side, speed, duty in (('left', left, wheels.get('dl')), ('right', right, wheels.get('dr'))):
            driven = number(duty) and abs(duty) >= STUCK_DUTY and number(speed) and abs(speed) <= STILL_SPEED
            if not driven:
                self.stuck[side] = None
            elif self.stuck[side] is None:
                self.stuck[side] = now
            elif now - self.stuck[side] >= STUCK_S:
                self.stuck_seen[side] = now

        imu = message.get('imu') if isinstance(message.get('imu'), dict) else {}
        ok = bool(imu.get('ok')) and number(imu.get('yaw'))
        self.devices['imu'].note(ok, now)
        self.zero = float(imu['zero']) if ok and number(imu.get('zero')) else None
        self.zero_age = int(imu['zero_age']) if ok and number(imu.get('zero_age')) else None
        if ok:
            self.heading, step = self._unwrapped('heading', float(imu['yaw']), self.heading)
            if abs(step) >= JUMP_DEG and was_still and not moving:
                self.heading_jumps += 1
                self.jumped_at = now
                self.rest.clear()
            self.traces['heading'].note(self.heading, now)
            self.traces['pitch'].note(float(imu['pitch']) if number(imu.get('pitch')) else None, now)
            self.traces['roll'].note(float(imu['roll']) if number(imu.get('roll')) else None, now)
        else:
            self._raw.pop('heading', None)
            self.rest.clear()
            for key in ('heading', 'pitch', 'roll'):
                self.traces[key].note(None, now)
        theta = odom.get('theta')
        if number(theta):
            self.wheel_heading, _ = self._unwrapped('wheels', math.degrees(theta), self.wheel_heading)
            self.traces['wheel_heading'].note(self.wheel_heading, now)

        mag = message.get('mag') if isinstance(message.get('mag'), dict) else {}
        ok = bool(mag.get('ok')) and all(number(mag.get(k)) for k in ('x', 'y', 'z'))
        self.devices['compass'].note(ok, now)
        if ok:
            self.compass, _ = self._unwrapped('compass', math.degrees(math.atan2(mag['y'], mag['x'])), self.compass)
            self.traces['compass'].note(self.compass, now)
            self.traces['field'].note(math.sqrt(mag['x'] ** 2 + mag['y'] ** 2 + mag['z'] ** 2), now)
        else:
            self._raw.pop('compass', None)
            self.compass_rest.clear()
            self.traces['compass'].note(None, now); self.traces['field'].note(None, now)

        env = message.get('env') if isinstance(message.get('env'), dict) else {}
        self.devices['climate'].note(env.get('ok'), now)
        self.devices['battery'].note(number(message.get('batt_mv')) and message['batt_mv'] >= 0, now)
        tof = message.get('tof_mm') if isinstance(message.get('tof_mm'), dict) else None
        if tof is not None:
            own = [tof.get(k + '_radial', tof.get(k)) if k in CORNERS else tof.get(k) for k in RING]
            self.ring_answering = sum(1 for v in own if number(v) and v >= 0)
            self.devices['ring'].note(self.ring_answering == len(RING), now)
        for name in ('front', 'rear'):
            at = self.matrix_at[name]
            self.devices[name].note(at is not None and now - at <= MATRIX_STALE_S, now)

        # At rest: the heading should not move at all, so what it does is the gyroscope's drift.
        clock = seconds if seconds is not None else now      # The controller's own clock times a move.
        if stopped and self._move is not None:
            self._move.update(end_gyro=self.heading, end_wheels=self.wheel_heading, end_at=clock)
        if moving:
            self.rest.clear(); self.compass_rest.clear()
            if self._move is None and self.heading is not None and self.wheel_heading is not None:
                self._move = {'at': clock, 'gyro': self.heading, 'wheels': self.wheel_heading, 'metres': 0.,
                              'xy': (odom.get('x'), odom.get('y')), 'drift': self._drift_before}
            if self._move is not None and number(odom.get('x')) and number(odom.get('y')):
                x0, y0 = self._move['xy']
                if number(x0) and number(y0):
                    self._move['metres'] += math.hypot(odom['x'] - x0, odom['y'] - y0)
                self._move['xy'] = (odom['x'], odom['y'])
            return
        if self.devices['imu'].ok and self.heading is not None:
            self.rest.append((seconds if seconds is not None else now, self.heading))
            while self.rest and self.rest[-1][0] - self.rest[0][0] > DRIFT_WINDOW_S:
                self.rest.popleft()
        if self.devices['compass'].ok and self.compass is not None:
            self.compass_rest.append((now, self.compass))
            while self.compass_rest and now - self.compass_rest[0][0] > DRIFT_WINDOW_S:
                self.compass_rest.popleft()
        self._drift_before = self.drift()
        if (self._move is not None and self.still_since is not None and now - self.still_since >= MOVE_SETTLE_S
                and self._move.get('end_gyro') is not None and self._move.get('end_wheels') is not None):
            # Summed up from where the wheels stopped, so the wait to be sure adds no drift to it.
            move, self._move = self._move, None
            self.last_move = {'gyro': move['end_gyro'] - move['gyro'], 'wheels': move['end_wheels'] - move['wheels'],
                              'seconds': move['end_at'] - move['at'], 'metres': move['metres'], 'ended': self.still_since,
                              'drift': move['drift']}

    # ------------------------------------------------------------------ reading it
    def drift(self):
        """Degrees a second the gyroscope's heading moves with the wheels still, or None before
        REST_NEEDED_S of stillness."""
        if len(self.rest) < 2:
            return None
        seconds = self.rest[-1][0] - self.rest[0][0]
        if seconds < REST_NEEDED_S:
            return None
        return (self.rest[-1][1] - self.rest[0][1]) / seconds

    def drift_level(self):
        rate = self.drift()
        if rate is None:
            return None
        return 'bad' if abs(rate) >= DRIFT_BAD else 'warn' if abs(rate) >= DRIFT_NORMAL else 'ok'

    def compass_spread(self):
        """How far the raw compass direction wandered over the last seconds at rest, or None."""
        if len(self.compass_rest) < 20:
            return None
        values = [v for _, v in self.compass_rest]
        return max(values) - min(values)

    def moves_disagree(self):
        """The last move turned him, and the gyroscope and the wheels differ by a quarter of it or more."""
        move = self.last_move
        if move is None:
            return False
        most = max(abs(move['gyro']), abs(move['wheels']))
        return most >= MOVE_TURN_DEG and abs(move['gyro'] - move['wheels']) >= max(10., .25 * most)

    def notices(self, now):
        """What stands out, in words, worst first: [(level, text)], level 'bad', 'warn' or 'ok'."""
        if not self.live(now):
            return []
        rows = []
        silent = [d for d in self.devices.values() if d.ok is False]
        if silent:
            rows.append(('bad', 'Not answering: ' + ', '.join(
                f'{d.name} ({d.part})' + (f', {self.ring_answering} of 8' if d is self.devices['ring'] else '')
                for d in silent) + '.'))
            if len(silent) >= 3:
                rows.append(('bad', 'Several chips on the sensor bus are silent together: look at the bus wiring and '
                                    'the sensors\' power before any one sensor.'))
        battery = self.devices['battery']
        if battery.ok is False:
            rows.append(('warn', 'The battery gauge is only set up when the controller starts. It was not answering '
                                 'then, so the battery menu shows no charge or volts until the controller is restarted '
                                 '(battery menu, Restart ESP32).'))
        imu = self.devices['imu']
        rate = self.drift()
        if imu.ok and self.zero_age is not None and self.zero_age < 0:
            rows.append(('warn', 'The gyroscope\'s zero has not been measured since it started: it is using the last one '
                                 'saved. It measures a new one in the first second he stands still with nothing moving.'))
        if imu.ok and rate is not None:
            level = self.drift_level()
            if level == 'bad':
                remedy = ('It measures a new one in the first second he stands still with nothing moving.'
                          if self.zero_age is not None else
                          'Restart the controller with Rex standing still and nothing moving (battery menu, Restart '
                          'ESP32), then watch this number.')
                rows.append(('bad', f'The gyroscope\'s zero is wrong: his heading drifts {rate:+.1f}° a second with the '
                                    f'wheels still. Turns one way will come up short and the other way will overrun. {remedy}'))
            elif level == 'warn':
                rows.append(('warn', f'His heading drifts {rate:+.2f}° a second with the wheels still '
                                     f'(normal is under {DRIFT_NORMAL}). Turns will be a few degrees off.'))
            else:
                rows.append(('ok', f'Gyroscope steady: {abs(rate):.2f}° a second of drift with the wheels still '
                                   f'(normal is under {DRIFT_NORMAL}).'))
        elif imu.ok and self.still_since is None:
            rows.append(('ok', 'The wheels are moving: drift is measured only while they are still.'))
        elif imu.ok:
            rows.append(('ok', f'Measuring the gyroscope\'s drift: it needs {REST_NEEDED_S:.0f} s with the wheels still.'))
        if self.heading_jumps:
            rows.append(('warn', f'The heading jumped with the wheels still, {span_words(now - self.jumped_at)} ago '
                                 f'(×{self.heading_jumps} while watching): the sensor started over. Its zero may have '
                                 'been taken from one reading, so check the drift above.'))
        if self.restarted_at is not None:
            rows.append(('warn', f'The controller restarted {span_words(now - self.restarted_at)} ago '
                                 f'(×{self.restarts} while watching). The gyroscope\'s zero was taken then.'))
        dropped = [d for d in self.devices.values() if d.dropouts and d.ok]
        if dropped:
            rows.append(('warn', 'Dropped out and came back while watching: ' +
                         ', '.join(f'{d.name} ×{d.dropouts}' for d in dropped) + '.'))
        if self.moves_disagree():
            move = self.last_move
            rows.append(('warn', f'On the last move the gyroscope turned {move["gyro"]:+.0f}° and the wheels '
                                 f'{move["wheels"]:+.0f}°. A wrong gyroscope zero or a slipping wheel does that.'))
        for side in ('left', 'right'):
            at = self.stuck_seen[side]
            if at is not None and now - at <= HISTORY_S:
                rows.append(('warn', f'The {side} wheel was driven and did not turn, {span_words(now - at)} ago: '
                                     'stalled, or its encoder is not counting.'))
        spread = self.compass_spread()
        if self.devices['compass'].ok and spread is not None and spread >= COMPASS_UNSTEADY_DEG:
            rows.append(('warn', f'The compass wandered {spread:.0f}° in ten seconds with the wheels still: something '
                                 'magnetic nearby is changing, or its wiring is noisy.'))
        if isinstance(self.telemetry, dict) and (self.telemetry.get('mag') or {}).get('ovl'):
            rows.append(('warn', 'The compass is overloaded: a magnet or motor is too close for it to read.'))
        if self.bus_events and self.last_bus is not None:
            rows.append(('warn', f'The controller reported sensor bus trouble ×{self.bus_events} while watching. '
                                 f'Latest, {span_words(now - self.last_bus[0])} ago: {self.last_bus[1]}'))
        if self.errs:
            rows.append(('warn', f'The controller counted {self.errs} garbled lines from this Mac while watching.'))
        if not any(level != 'ok' for level, _ in rows) and all(d.ok for d in self.devices.values()):
            rows.append(('ok', 'Every chip on the sensor bus is answering.'))
        return sorted(rows, key=lambda row: ('bad', 'warn', 'ok').index(row[0]))
