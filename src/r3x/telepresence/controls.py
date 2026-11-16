"""The caller's hands in a call (stages 3 and 5, docs/TELEPRESENCE_PLAN.md): head, lift and visor, and
the wheels, steered over the network link.

Both owners' limits stay in force. The servo owner keeps its calibrated limits, pair routes and the
operator override (RobotServos.telepresence_pose). The drive owner keeps the joystick's caps, front
path check, spin check, the firmware's rear block and its 300 ms deadman (DriveBase.manual). What is
added here is what a network link needs:
  - only the newest setpoint counts, and one that arrives out of order is dropped;
  - a setpoint older than SETPOINT_STALE_SECONDS gives zero;
  - a round trip over LINK_SLOW_SECONDS refuses forward and turning (backing away stays);
  - a Stop holds the wheels until the caller's stick is released;
  - a link that goes stops the wheels at once.
Nothing here moves on its own, interprets speech or opens a device.
"""
import math
import threading
import time
from uuid import uuid4

from ..contracts import Cancellation, ProviderFailure
from ..drive import MANUAL_SPEED, MANUAL_TURN, RAMP_DOWN_SECONDS, RAMP_UP_SECONDS, ramp_toward
from ..drive_space import clearance
from ..servo_poses import telepresence_reading

SETPOINT_HZ = 10.
SETPOINT_STALE_SECONDS = .3   # The dashboard joystick's rule: no fresh setpoint this long sends zero.
LINK_SLOW_SECONDS = .4        # Plan proposal: a round trip above this refuses forward and turning.
HEAD_FIELDS = ('neck', 'pitch', 'tilt', 'lift', 'visor')
HEAD_WAIT_SECONDS = 5.        # A head move in flight when the wheels are wanted gets this long to finish.
CLEARANCE_KEYS = ('front_m', 'path_m', 'left_m', 'right_m', 'rear_m')


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


class HeadControl:
    """The caller's newest head target wins; nothing queues behind a move in flight. While the wheels
    are wanted or turning the head keeps the drive stance's level pose and requests are refused (the
    plan's open point, looks while rolling, stays closed)."""

    def __init__(self, robot, *, wheels_busy=lambda: False, notify=lambda message: None):
        self.robot, self.wheels_busy, self.notify = robot, wheels_busy, notify
        self.condition = threading.Condition()
        self.pending = {}
        self.executing = False
        self.closed = False
        self.cancel = Cancellation()
        self.thread = None
        self.last = None
        self.stats = {'moves': 0, 'refused': 0, 'failed': 0}

    def start(self):
        self.thread = threading.Thread(target=self._run, name='telepresence-head', daemon=True)
        self.thread.start()

    def request(self, message):
        """One caller head message (any of HEAD_FIELDS). Returns why it was refused, or None."""
        spec = {k: message[k] for k in HEAD_FIELDS if k in message}
        if not spec:
            return 'nothing_to_move'
        if not all(_number(v) for v in spec.values()):
            return 'not_a_number'
        if self.wheels_busy():
            self.stats['refused'] += 1
            return 'head_held_while_driving'
        with self.condition:
            if self.closed:
                return 'call_ending'
            self.pending.update(spec)
            self.condition.notify_all()
        return None

    def clear(self):
        """The link went: nothing it asked for runs later."""
        with self.condition:
            self.pending.clear()

    def level(self, cancel):
        """The wheels are wanted: drop waiting moves, let one in flight finish, then level the head.
        Returns None once level (or with nothing to level), else why the wheels must wait."""
        with self.condition:
            self.pending.clear()
            if not self.condition.wait_for(lambda: not self.executing or self.closed, HEAD_WAIT_SECONDS):
                return 'head_still_moving'
        try:
            receipt = self.robot.telepresence_level(cancel)
        except Exception as exc:
            return 'head_level_failed:' + type(exc).__name__
        if receipt is None or receipt.status == 'pulses_reached':
            return None
        return 'head_level_failed:' + str(receipt.reason or receipt.status)

    def _run(self):
        while True:
            with self.condition:
                while not self.pending and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                spec, self.pending = self.pending, {}
                self.executing = True
                cancel = self.cancel
            try:
                if self.wheels_busy():
                    status, reason = 'rejected', 'head_held_while_driving'
                else:
                    receipt = self.robot.telepresence_pose(spec, cancel, 'telepresence:head:' + uuid4().hex[:12])
                    status, reason = receipt.status, receipt.reason
            except Exception as exc:
                status, reason = 'failed', type(exc).__name__
            finally:
                with self.condition:
                    self.executing = False
                    self.condition.notify_all()
            if status == 'rejected' and reason == 'ServoBusy':
                with self.condition:   # Try the same target again unless a newer one came.
                    self.pending = {**spec, **self.pending}
                    self.condition.wait(.05)
                continue
            self.stats['moves' if status == 'pulses_reached' else 'refused' if status == 'rejected' else 'failed'] += 1
            self.last = {'status': status, 'reason': reason}
            self.notify({'type': 'head_state', 'status': status, 'reason': reason, **self.reading()})

    def reading(self):
        return telepresence_reading(dict(self.robot.owner.commanded))

    def status(self):
        return {**self.reading(), 'held_for_driving': bool(self.wheels_busy()), 'last': self.last}

    def close(self):
        with self.condition:
            self.closed = True
            self.pending.clear()
            self.cancel.event.set()
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(16.)   # A move in flight ends at its arrival, cancellation or 15 s expiry.
            if self.thread.is_alive():
                raise ProviderFailure('TelepresenceHeadDidNotStop')
            self.thread = None


class DriveControl:
    """The caller's stick, SETPOINT_HZ times a second, into DriveBase.manual. A setpoint is
    {"seq": n, "lin": -1..1, "ang": -1..1}: fractions of the joystick's caps, positive ang turning
    left. The robot eases toward it like the dashboard joystick and zeroes it at once on Stop, a
    stale setpoint, a slow link or a lost one."""

    def __init__(self, drive, *, head=None, link_slow=lambda now: None, notify=lambda message: None,
                 clock=time.monotonic, stance_held=True):
        self.drive, self.head, self.link_slow, self.notify = drive, head, link_slow, notify
        self.clock, self.stance_held = clock, stance_held
        self.lock = threading.Lock()          # The newest setpoint and the stop flag.
        self.drive_lock = threading.Lock()    # Every call into the drive owner.
        self.newest = None                    # The newest setpoint: [lin, ang, received at, applied].
        self.sequence = None                  # Newest seq taken on this link.
        self.halted = False                   # Stop: zero until the caller's stick reads zero.
        self.engaged = False                  # Wheels wanted, turning or settling: the head holds level.
        self.cmd = (0., 0.)
        self.refused, self.held = None, ()
        self.reported = None
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.cancel = Cancellation()
        self.thread = None
        self.stats = {'setpoints': 0, 'out_of_order': 0, 'superseded': 0, 'stale': 0, 'link_slow': 0, 'stops': 0}

    def start(self):
        self.thread = threading.Thread(target=self._run, name='telepresence-drive', daemon=True)
        self.thread.start()

    # Caller messages (the link's reader thread) -------------------------------
    def setpoint(self, message):
        """Returns why the setpoint was dropped, or None."""
        seq, lin, ang = message.get('seq'), message.get('lin'), message.get('ang')
        if type(seq) is not int or seq < 0 or not _number(lin) or not _number(ang):
            return 'bad_setpoint'
        lin, ang = max(-1., min(1., float(lin))), max(-1., min(1., float(ang)))
        with self.lock:
            if self.sequence is not None and seq <= self.sequence:
                self.stats['out_of_order'] += 1
                return 'out_of_order'
            if self.newest is not None and not self.newest[3]:
                self.stats['superseded'] += 1   # A burst: only the newest is applied.
            self.sequence = seq
            self.newest = [lin, ang, self.clock(), False]
            self.stats['setpoints'] += 1
            if self.halted and not lin and not ang:
                self.halted = False   # The stick is released: a Stop's hold ends.
        return None

    def stop(self):
        """The caller's Stop (space bar): zero now, and held until their stick reads zero."""
        with self.lock:
            self.halted = True
            self.newest = None
            self.stats['stops'] += 1
        with self.drive_lock:
            self.cmd = (0., 0.)
            self.drive.cancel()   # Sends the controller's stop at once.
        self.wake.set()

    def link_lost(self):
        """The link went: the wheels stop now (the firmware's deadman is behind this) and the next
        link starts its sequence afresh."""
        with self.lock:
            self.newest, self.sequence, self.halted = None, None, False
        with self.drive_lock:
            self.cmd = (0., 0.)
            if self.drive.manual_active:
                self.drive.end_manual()
        self.engaged = False
        self.wake.set()

    # The worker -------------------------------------------------------------
    def _run(self):
        period = 1. / SETPOINT_HZ
        while not self.stop_event.is_set():
            self.wake.wait(period)
            self.wake.clear()
            if self.stop_event.is_set():
                break
            try:
                self._tick(self.clock(), period)
            except Exception as exc:
                self.refused = 'drive_control_failed:' + type(exc).__name__
                with self.drive_lock:
                    self.cmd = (0., 0.)
                    try:
                        if self.drive.manual_active:
                            self.drive.manual(0., 0., self.cancel)
                    except Exception:
                        pass

    def _tick(self, now, period):
        with self.lock:
            setpoint, halted = self.newest, self.halted
            if setpoint is not None:
                setpoint[3] = True
        refused, lin, ang, immediate = None, 0., 0., True
        if halted:
            refused = 'stopped'
        elif setpoint is not None and now - setpoint[2] <= SETPOINT_STALE_SECONDS:
            lin, ang, immediate = setpoint[0] * MANUAL_SPEED, setpoint[1] * MANUAL_TURN, False
        elif setpoint is not None and (setpoint[0] or setpoint[1]):
            self.stats['stale'] += 1
            refused = 'setpoint_stale'
        if lin > 0 or ang:
            slow = self.link_slow(now)
            if slow:
                self.stats['link_slow'] += 1
                refused, lin, ang = slow, min(lin, 0.), 0.
        if immediate:
            cmd = (0., 0.)
        else:
            cmd = (ramp_toward(self.cmd[0], lin, MANUAL_SPEED * period / RAMP_UP_SECONDS,
                               MANUAL_SPEED * period / RAMP_DOWN_SECONDS),
                   ramp_toward(self.cmd[1], ang, MANUAL_TURN * period / RAMP_UP_SECONDS,
                               MANUAL_TURN * period / RAMP_DOWN_SECONDS))
            if refused:   # What the link refuses stops at once; the firmware still tapers it.
                cmd = (min(cmd[0], 0.) if lin <= 0 else cmd[0], 0. if not ang else cmd[1])
        wanted = bool(cmd[0] or cmd[1])
        if wanted and not self.engaged:
            # Setting off from still: the head returns to the drive stance's level pose first.
            self.engaged = True
            why = self.head.level(self.cancel) if self.head is not None else None
            if why:
                refused, cmd, wanted = why, (0., 0.), False
        held = ()
        with self.drive_lock:
            with self.lock:
                if self.halted:
                    cmd, wanted, refused = (0., 0.), False, 'stopped'
            if wanted and not self.drive.manual_active:
                ok, reason = self.drive.begin_manual(stance_held=self.stance_held)
                if not ok:
                    refused, cmd, wanted = reason, (0., 0.), False
            if self.drive.manual_active:
                result = self.drive.manual(cmd[0], cmd[1], self.cancel)
                if result.get('status') == 'sent':
                    self.cmd = (result['lin'], result['ang'])
                    held = tuple(result.get('held') or ())
                else:
                    self.cmd = (0., 0.)
                    refused = refused or result.get('reason')
            else:
                self.cmd = (0., 0.)
        self.engaged = bool(self.cmd[0] or self.cmd[1] or wanted) or self.drive.motion_risk()
        self.refused, self.held = refused, held
        report = (refused, held, bool(self.cmd[0] or self.cmd[1]))
        if report != self.reported:
            self.reported = report
            self.notify({'type': 'drive_state', **self.status()})

    def status(self):
        return {'active': self.drive.manual_active, 'moving': bool(self.cmd[0] or self.cmd[1]),
                'lin_mps': round(self.cmd[0], 3), 'ang_dps': round(math.degrees(self.cmd[1]), 1),
                'refused': self.refused, 'held': list(self.held), 'stopped': self.halted}

    def close(self):
        self.stop_event.set()
        self.cancel.event.set()
        self.wake.set()
        if self.thread is not None:
            self.thread.join(8.)   # A begin_manual may wait up to 5 s for the plan lock.
            if self.thread.is_alive():
                raise ProviderFailure('TelepresenceDriveDidNotStop')
            self.thread = None
        with self.drive_lock:
            if self.drive.manual_active:
                self.drive.end_manual()


def base_readings(drive, radar, now):
    """The caller's status strip: charge, open space around him and radar bodies. Display only: it
    reads the latest frames and never folds them into the drive owner's memory of the floor."""
    if drive is None:
        return None
    telemetry, matrix = drive.link.snapshot()
    space = clearance(telemetry, matrix, now, rear_matrix=drive.link.rear_snapshot()) if telemetry else None
    charge = drive.charge_reading(now)
    millivolts = (telemetry or {}).get('batt_mv')
    bodies = radar.bodies() if radar is not None else None
    return {'connected': drive.state == 'ready' and not drive.link.failure,
            'battery_percent': charge[0] if charge else None,
            'charging': charge[1] if charge else None,
            'volts': round(millivolts / 1000, 2) if _number(millivolts) and millivolts > 0 else None,
            'blocked': (telemetry or {}).get('blocked_dir'),
            'clearance_m': {k: (round(space[k], 2) if _number(space.get(k)) else None) for k in CLEARANCE_KEYS}
                           if space else None,
            'sensor_notes': list(space['health']) if space else ['drive_telemetry_missing'],
            'radar': None if radar is None else
                     [{k: b.get(k) for k in ('bearing_deg', 'range_m', 'moving')} for b in bodies or ()]}
