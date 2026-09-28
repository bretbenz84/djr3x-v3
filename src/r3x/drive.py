"""Sole executor of LLM-selected drive-base plans, plus the back-off reflex.

The conversation owner proposes a bounded plan; CoreState validates it; this
owner executes one step at a time against V2's unchanged ESP32 firmware. The
firmware keeps its reflex stop, watchdog and drive deadman. Receipts report
controller outcomes, odometry and IMU heading; never visual confirmation.
"""
from collections import deque
from dataclasses import asdict, dataclass, field
import math
import threading
import time
from uuid import uuid4

from .adapters.esp32 import DriveLink, RadarLink, open_serial
from .contracts import Cancellation, Cancelled, ProviderFailure
from .drive_space import (allowed_turn, clearance, describe_bearing, face_range, image_bearing,
                          move_allowance, radar_bodies, wrap, FRONT_MARGIN, PERSON_MARGIN)
from .providers import Capability

# Owner-approved first-test limits (September 27): 0.25 m/s, 1.5 m/step, 3 m/plan.
LIMITS = {
    'requested': {'speed_mps': .25, 'step_m': 1.5, 'total_m': 3., 'turn_deg': 360., 'total_turn_deg': 720., 'max_steps': 6},
    'self_initiated': {'speed_mps': .18, 'step_m': .75, 'total_m': 1.5, 'turn_deg': 180., 'total_turn_deg': 360., 'max_steps': 4},
}
ACTIONS = ('turn', 'move', 'face', 'approach', 'find_person', 'seek')
SEEK_SWEEP_DEG = 80.      # Just under the ~90 degree camera view, so consecutive looks overlap.
SEEK_LOOKS = 4            # Current view plus three turns: about a full circle.
PLAN_VISION_CALLS = 8     # Hosted locator calls per plan (search plus vision-guided approach).
TURN_RATE = 45.          # V2 overshoot grows with rate; autonomous V2 turns used 40.
# Owner feedback September 27: stopping 4-5 ft away was too far.
DEFAULT_STOP_AT = {'face': .8, 'nearest_person': .8, 'object': .6, 'radar_body': .9, 'voice_direction': .9}
# Pushed at every connect; config can only lower firmware hard caps.
CONFIG = {'max_lin': .25, 'max_ang': round(math.radians(60), 3), 'accel_lin': .35, 'accel_ang': 2.,
          'stop_zone_m': .15, 'slow_zone_m': .6, 'default_turn_rate': TURN_RATE,
          'watchdog_ms': 500, 'drive_expiry_ms': 300,
          # V2 pushes these: firmware defaults keep the gamepad owner until Start is pressed.
          'manual_autoreturn': True, 'manual_idle_return_secs': 4}
CHARGER_MV = 13600
CAMERA_KINDS = {'face', 'nearest_person', 'object'}


@dataclass(frozen=True)
class DriveStep:
    action: str
    degrees: float | None = None
    meters: float | None = None
    target: str | None = None
    stop_at_m: float | None = None
    description: str | None = None   # seek: what to look for, in the person's words.
    prefer: str | None = None        # seek: sweep left or right first.


@dataclass(frozen=True)
class DrivePlan:
    request_id: str
    steps: tuple
    origin: str
    purpose: str
    targets: dict
    expires_at: float
    look_after: str | None = None
    allow_new_arrivals: bool = False
    park_arm: bool = True


@dataclass
class DriveReceipt:
    request_id: str
    status: str = 'failed'
    reason: str | None = None
    steps: list = field(default_factory=list)
    started_at: float = 0.
    finished_at: float | None = None
    travelled_m: float = 0.
    turned_deg: float = 0.
    medium: str = 'drive_controller'
    evidence: str = 'firmware done results, odometry and IMU heading; no visual confirmation'


class DriveAction:
    """A plan queued behind earlier phrases, so before-motion speech finishes first."""
    def __init__(self, plan: DrivePlan):
        self.plan = plan
        self.receipt: DriveReceipt | None = None
        self.done = threading.Event()


class Flinch:
    """V2 back-off reflex: per-side adaptive baselines, radial corroboration.

    A reading inside the trigger that also dropped sharply from its own open
    baseline, held for three ticks, fires. Baselines freeze while intruded and
    rise only after sustained clear readings, so a slow approach cannot hide.
    """
    trigger, drop, minimum, adapt, confirm, cooldown = .25, .12, .02, .12, 3, 8.

    def __init__(self):
        self.baseline, self.clear, self.ticks, self.last = {}, {}, 0, float('-inf')

    def observe(self, readings: dict, now: float) -> bool:
        intruding = set()
        for key, value in readings.items():
            if value is None:
                continue
            base = self.baseline.get(key)
            if base is None:
                self.baseline[key] = value
                continue
            if self.minimum <= value <= self.trigger and base - value >= self.drop:
                intruding.add(key)
                self.clear[key] = 0
                continue
            if value < base:
                self.baseline[key] = max(value, base - self.adapt)
                self.clear[key] = 0
            else:
                self.clear[key] = self.clear.get(key, 0) + 1
                if self.clear[key] >= 3:
                    self.baseline[key] = value
        # Corroboration: an independent radial sensor must see its own intrusion.
        self.ticks = self.ticks + 1 if intruding & {'fl_radial', 'fr_radial'} else 0
        if self.ticks >= self.confirm and now - self.last >= self.cooldown:
            self.last, self.ticks = now, 0
            return True
        return False


class Approach:
    """Mac-streamed approach (adapted from V2 intelligence/approach.py).

    Face width gives an approximate person range; objects have none, so their
    arrival is a centred target at the front stand-off. Blocked never becomes
    completed. V2's fixed 0.55 m fused-front subtraction (which stalled field
    approaches at 26 cm) is replaced by the shared FRONT_MARGIN stand-off.
    """
    def __init__(self, now, kind, stop_at, speed, max_travel, accel=.35):
        self.started, self.kind, self.stop_at, self.speed = now, kind, stop_at, speed
        self.max_travel, self.accel = max(.05, max_travel), accel
        self.last_seen, self.ranges, self.samples = now, [], 0
        self.near_since = self.near_sample = self.block_since = self.front_near_since = self.clear_since = None
        self.travel, self.last_xy = 0., None

    def step(self, now, telemetry, space, target):
        odom = telemetry.get('odom') or {}
        xy = (float(odom.get('x', 0)), float(odom.get('y', 0)))
        if self.last_xy is not None:
            self.travel += math.hypot(xy[0]-self.last_xy[0], xy[1]-self.last_xy[1])
        self.last_xy = xy
        if now - self.started >= 20:
            return 0., 0., 'timeout', 'approach time limit'
        if self.travel >= self.max_travel:
            return 0., 0., 'budget_reached', 'travel budget reached before the target'
        front = space['front_m']
        if front is None:
            return 0., 0., 'aborted', 'front clearance unavailable'
        speed_now = max(0., float(odom.get('lin', 0)))
        if target is not None:
            self.last_seen = now
            self.samples += 1
            if target.get('range_m') is not None:
                self.ranges = (self.ranges + [target['range_m']])[-3:]
        blocked = (telemetry.get('state') == 'blocked' and telemetry.get('blocked_dir') in ('front', 'both')) or front <= .2
        if blocked:
            self.near_since = self.clear_since = None
            self.block_since = self.block_since or now
            return 0., 0., ('blocked' if now - self.block_since >= 5 else None), 'front obstacle; holding'
        self.block_since = None
        brake = speed_now*speed_now/(2*self.accel) + speed_now*.3
        # Front sensors see knees and chair legs, not the face: they bound collision
        # only. Personal distance comes from the calibrated face range below.
        budget = front - (PERSON_MARGIN if self.kind == 'face' else FRONT_MARGIN)
        if budget <= max(.03, brake):
            self.front_near_since = self.front_near_since or now
            if now - self.front_near_since >= .6 and speed_now <= .08:
                centred = target is not None and abs(target['bearing_deg']) <= 15
                if self.kind != 'face' and centred:
                    return 0., 0., 'completed', 'reached stand-off in front of target'
                return 0., 0., 'blocked', 'front stand-off reached before the target'
            return 0., 0., None, 'braking at front stand-off'
        self.front_near_since = None
        if now - self.last_seen >= 6:
            return 0., 0., 'lost', 'target lost from camera'
        if now - self.last_seen > 1.2:
            self.near_since = None
            return 0., 0., None, 'waiting for target in camera'
        if self.kind == 'face':
            if not self.ranges:
                return 0., 0., None, 'waiting for face range'
            remaining = sorted(self.ranges)[len(self.ranges)//2] - self.stop_at - .08
            if remaining <= 0:
                if target is None:
                    self.near_since = None
                    return 0., 0., None, 'confirming at stand-off'
                if self.near_since is None:
                    self.near_since, self.near_sample = now, self.samples
                if now - self.near_since >= .6 and self.samples - self.near_sample >= 2 and speed_now <= .08:
                    return 0., 0., 'completed', 'person at camera stand-off'
                return 0., 0., None, 'settling at person'
            self.near_since = None
        elif target is not None and target.get('box_width', 0) >= .5:
            return 0., 0., 'completed', 'target fills the camera view'
        else:
            remaining = budget
        if self.max_travel - self.travel - .03 <= .02:
            return 0., 0., 'budget_reached', 'travel budget reached before the target'
        lin = min(self.speed, max(.04, math.sqrt(2*self.accel*max(0., remaining))),
                  max(0., budget)/.6, max(0., self.max_travel - self.travel - .03)/.6)
        clear = front >= 1. and all(v is not None and v >= .4 for v in (space['left_m'], space['right_m']))
        if not clear:
            self.clear_since = None
        elif self.clear_since is None:
            self.clear_since = now
        ang = 0.
        if target is not None and self.clear_since is not None and now - self.clear_since >= .4:
            # Firmware adds hallway avoidance; steer only after a clear corridor persists.
            ang = max(-.25, min(.25, math.radians(target['bearing_deg'])*1.2))
        return lin, ang, None, 'approaching'


class Radar:
    """Optional LD2450 ring; its bodies are bearing priors for where to look."""
    capability = Capability('radar', 'ESP32-S3 LD2450 radar ring (V2 protocol v1)',
        'bearing prior for people outside the camera view', dependencies=('pyserial',),
        resources=('exclusive radar ESP32 serial',), required=False, shutdown='close port')

    def __init__(self, port, *, open_port=None, clock=time.monotonic):
        self.link = RadarLink(open_port or (lambda: open_serial(port)), clock=clock)
        self.clock = clock
        self.state = 'new'

    def start(self):
        hello = self.link.open({})
        if 'radar' not in (hello.get('caps') or ()):
            raise ProviderFailure('RadarCapabilityMissing')
        self.state = 'ready'

    def bodies(self, window=1.5):
        if self.state != 'ready' or self.link.failure:
            return None
        return radar_bodies(self.link.recent(self.clock() - window))

    def cancel(self):
        pass

    def close(self):
        self.state = 'closed'
        self.link.close()


class DriveBase:
    capability = Capability('drive_base', 'ESP32 differential base (V2 protocol v1)',
        'LLM-selected turns, moves and approaches; back-off reflex', dependencies=('pyserial',),
        resources=('exclusive drive ESP32 serial; 150 ms heartbeat',), required=False,
        shutdown='halt plan; stop; end heartbeat; close port')

    def __init__(self, port, *, open_port=None, clock=time.monotonic, wait=None, radar=None, reflex=True):
        self.link = DriveLink(open_port or (lambda: open_serial(port)), clock=clock)
        self.clock = clock
        self._closing = threading.Event()
        self._halt = threading.Event()
        self.wait = wait or self._closing.wait
        self.radar = radar
        self.servos = None          # RobotServos: throttle park and neck centering
        self.faces = None           # camera.latest(now) -> rows
        self.objects = None         # objects.snapshot() -> rows
        self.on_motion = []         # callables(plan_or_none), before and after base motion
        self.trace = None
        self.reflex_allowed = lambda: True
        self._lock = threading.Lock()
        self._threads = []
        self._reflex = reflex
        self.state = 'new'
        self.moving = threading.Event()
        self.motion_until = 0.
        self.history = deque(maxlen=6)
        self.flinch = Flinch()
        self.origin = None
        self.object_tracks = None     # objects including single-frame re-detections after motion
        self.locator = None           # (description, image, cancel) -> hosted vision geometry
        self.look = None              # (after) -> fresh sharp camera image, or None
        self._sought = None
        self._vision_calls = 0
        self._view_since = float('-inf')
        self._start_heading = None

    # Lifecycle -----------------------------------------------------------
    def start(self):
        hello = self.link.open({})
        if not {'drive', 'turn', 'move', 'stop'}.issubset(hello.get('caps') or ()):
            raise ProviderFailure('DriveCapabilitiesMissing')
        # Startup is real serial I/O: bound its waits by wall time, not the injected clock.
        ack = self._await_ack(self.link.send({'cmd': 'config', **CONFIG}), 1.5, self._closing.wait, time.monotonic)
        if ack is None:
            raise ProviderFailure('DriveConfigUnacknowledged')
        if not ack.get('accepted'):
            raise ProviderFailure('DriveConfigRejected:' + str(ack.get('reason')))
        self._spawn('heartbeat', self._heartbeat)
        deadline = time.monotonic() + 1.5
        while self.link.snapshot()[0] is None:
            if self.link.failure or time.monotonic() >= deadline:
                raise ProviderFailure(self.link.failure or 'DriveTelemetryMissing')
            self._closing.wait(.02)
        odom = (self.link.snapshot()[0].get('odom') or {})
        self.origin = (float(odom.get('x', 0)), float(odom.get('y', 0)), self._heading(self.link.snapshot()[0]))
        self.state = 'ready'
        if self._reflex:
            self._spawn('reflex', self._reflex_loop)

    def _spawn(self, name, target):
        thread = threading.Thread(target=target, name='r3x-drive-'+name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _heartbeat(self):
        while not self._closing.wait(.15):
            try:
                self.link.send({'cmd': 'ping'})
            except ProviderFailure:
                self.state = 'faulted'
                return

    def cancel(self):
        """Explicit stop: halt the current plan and command a controlled stop now."""
        self._halt.set()
        try:
            if self.link.port is not None and not self.link.failure:
                self.link.send({'cmd': 'stop'})
        except ProviderFailure:
            pass

    def close(self):
        self._closing.set()
        self.cancel()
        if not self._lock.acquire(timeout=5.):
            raise ProviderFailure('DrivePlanStillActive')
        try:
            for thread in self._threads:
                thread.join(2.)
            if any(t.is_alive() for t in self._threads):
                raise ProviderFailure('DriveWorkerDidNotStop')
            self.link.close()
            self.state = 'closed'
        finally:
            self._lock.release()

    # Evidence ------------------------------------------------------------
    def motion_risk(self):
        return self.moving.is_set() or self.clock() < self.motion_until

    def space(self, now=None):
        telemetry, matrix = self.link.snapshot()
        return clearance(telemetry, matrix, self.clock() if now is None else now)

    def availability(self, now=None):
        now = self.clock() if now is None else now
        if self.state != 'ready':
            return False, 'drive_not_ready'
        if self.link.failure:
            return False, 'drive_link_failed'
        telemetry, _ = self.link.snapshot()
        if not telemetry or not 0 <= now - telemetry['received_at'] <= .5:
            return False, 'drive_telemetry_stale'
        if telemetry.get('owner') == 'manual':
            return False, 'gamepad_in_control'
        if telemetry.get('state') in {'estop', 'fault', 'comms_lost'}:
            return False, 'controller_' + telemetry['state']
        if telemetry.get('fault'):
            return False, 'controller_fault_' + str(telemetry['fault'])
        if telemetry.get('charging') or (telemetry.get('batt_mv') or 0) >= CHARGER_MV:
            return False, 'charging'
        space = self.space(now)
        for reason in ('front_depth_matrix_unavailable', 'radial_ring_unavailable'):
            if reason in space['health']:
                return False, reason
        return True, None

    @staticmethod
    def _heading(telemetry):
        imu = telemetry.get('imu') or {}
        if imu.get('ok') and isinstance(imu.get('yaw'), (int, float)):
            return float(imu['yaw'])
        theta = (telemetry.get('odom') or {}).get('theta')
        return math.degrees(float(theta)) if isinstance(theta, (int, float)) else None

    def _neck(self):
        """Current neck angle; plans centre the neck before resolving camera targets."""
        try:
            return self.servos.owner.neck_degrees() if self.servos is not None else 0.
        except Exception:
            return 0.

    def _fresh_faces(self, now):
        from .faces import VISIBLE_FACE_MAX_AGE
        if self.faces is None:
            return []
        return [f for f in self.faces(now) if 0 <= now - f['at'] <= VISIBLE_FACE_MAX_AGE and f.get('box')]

    def context(self, origin, speaker_direction=None):
        """Robot-relative targets and clearance for the conversation model."""
        now = self.clock()
        ok, reason = self.availability(now)
        space = self.space(now)
        targets = []
        faces = self._fresh_faces(now)
        def path(bearing, distance=None):
            # Front sensors cover roughly +/-25 degrees: which band a straight approach crosses.
            band = 'front_center_m' if abs(bearing) <= 8 else 'front_left_m' if bearing > 0 else 'front_right_m'
            clear = space.get(band) if space.get(band) is not None else space['front_m']
            if clear is None:
                return {'clear_to_m': None, 'direct_approach': 'unknown'}
            blocked = clear - FRONT_MARGIN < (distance - 1. if distance else 1.)
            return {'clear_to_m': round(clear, 1), 'direct_approach': 'blocked_early' if blocked else 'clear'}
        for face in faces:
            bearing = image_bearing(face['box'], self._neck())
            rng = face_range(face['box'])
            targets.append({'target_id': face['track_id'], 'kind': 'face', 'person_id': face.get('identity'),
                            'bearing_deg': round(bearing), 'where': describe_bearing(bearing),
                            'approx_range_m': round(rng, 1) if rng else None, 'path': path(bearing, rng)})
        if faces:
            targets.append({'target_id': 'nearest_person', 'kind': 'nearest_person',
                            'where': 'largest visible face, resolved when the step runs'})
        for row in (self.objects() if self.objects else ())[:8]:
            bearing = image_bearing(row['box'], self._neck())
            targets.append({'target_id': row['target_id'], 'kind': 'object', 'label': row['label'],
                            'bearing_deg': round(bearing), 'where': describe_bearing(bearing), 'path': path(bearing)})
        bodies = self.radar.bodies() if self.radar else None
        for body in bodies or ():
            targets.append({'target_id': body['target_id'], 'kind': 'radar_body', 'bearing_deg': body['bearing_deg'],
                            'range_m': body['range_m'], 'moving': body['moving'], 'where': describe_bearing(body['bearing_deg'])})
        if speaker_direction and speaker_direction.get('status') == 'observed' and speaker_direction.get('bearing_deg') is not None:
            bearing = float(speaker_direction['bearing_deg'])
            targets.append({'target_id': 'speaker', 'kind': 'voice_direction', 'bearing_deg': round(bearing),
                            'where': describe_bearing(bearing)})
        telemetry, _ = self.link.snapshot()
        displacement = None
        if telemetry and self.origin:
            odom = telemetry.get('odom') or {}
            dx, dy = float(odom.get('x', 0)) - self.origin[0], float(odom.get('y', 0)) - self.origin[1]
            heading = self._heading(telemetry)
            displacement = {'distance_m': round(math.hypot(dx, dy), 2),
                            'heading_change_deg': round(wrap(heading - self.origin[2])) if heading is not None and self.origin[2] is not None else None}
        return {'available': ok, 'unavailable_reason': reason,
                'clearance_m': {k: (round(v, 1) if isinstance(v, float) else v) for k, v in space.items() if k != 'health'},
                'sensor_notes': space['health'], 'targets': targets,
                'radar': 'unavailable' if self.radar and bodies is None else ('connected' if self.radar else 'not_configured'),
                'vision_seek': self.locator is not None and self.look is not None,
                'limits': dict(LIMITS[origin]), 'origin': origin,
                'displacement_since_startup': displacement,
                'recent_motion': list(self.history),
                'meaning': ('Robot-relative bearings, positive = left. Face/object bearings come from the head camera '
                            '(approximate, re-measured with the neck centered when a step runs). Radar bodies are moving '
                            'bodies seen by the base radar ring: where to look, not who. speaker is the microphone-array '
                            'direction of the last utterance. Clearance is what base sensors report now; there is no map. '
                            'path.direct_approach blocked_early means a straight approach will stop at an obstacle first.')}

    # Execution -----------------------------------------------------------
    def _check(self, cancel):
        cancel.check()
        if self._halt.is_set() or self._closing.is_set():
            raise Cancelled()

    def _sleep(self, seconds, cancel):
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            self._check(cancel)
            self.wait(min(.05, deadline - self.clock()))

    def _await_ack(self, seq, timeout, wait, clock=None):
        clock = clock or self.clock
        deadline = clock() + timeout
        while clock() < deadline:
            ack = self.link.ack(seq)
            if ack is not None or self.link.failure:
                return ack
            wait(.02)
        return None

    def _finite(self, command, timeout, cancel):
        seq = self.link.send(command)
        ack = self._await_ack(seq, .5, self.wait)
        if ack is not None and not ack.get('accepted'):
            return 'refused', 'controller_refused_' + str(ack.get('reason')), None
        deadline = self.clock() + timeout
        try:
            while True:
                done = self.link.done(seq)
                if done is not None:
                    self._view_since = self.clock() + .3   # The camera view changed; wait for it.
                    result = done.get('result')
                    return ({'completed': 'completed', 'blocked': 'blocked', 'aborted': 'aborted'}.get(result, 'stopped'),
                            result, done)
                if self.link.failure:
                    raise ProviderFailure(self.link.failure)
                if self.clock() >= deadline:
                    self.link.send({'cmd': 'stop'})
                    return 'timeout', 'no_done_before_deadline', None
                self._check(cancel)
                self.wait(.05)
        except Cancelled:
            try:
                self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            raise

    def _telemetry(self):
        telemetry, _ = self.link.snapshot()
        if not telemetry or not 0 <= self.clock() - telemetry['received_at'] <= .5:
            raise ProviderFailure('DriveTelemetryStale')
        return telemetry

    @staticmethod
    def _odom_heading(telemetry):
        theta = (telemetry.get('odom') or {}).get('theta')
        return math.degrees(float(theta)) if isinstance(theta, (int, float)) else None

    def _escape_for_turn(self, degrees, plan, cancel):
        """V2 swing escape, generalized: earn room by backing up (or pulling forward), then retry once."""
        space = self.space()
        rear, front = space['rear_m'], space['front_m']
        if rear is not None and rear - .35 >= .2:
            leg = -min(.5, rear - .35)
        elif front is not None and front - FRONT_MARGIN >= .3:
            leg = min(.4, front - FRONT_MARGIN)
        else:
            return None
        return self._move(leg, plan, cancel)

    def _turn(self, degrees, plan, cancel, verify=True, escape=True):
        telemetry = self._telemetry()
        allowed, note = allowed_turn(degrees, telemetry)
        row = {'action': 'turn', 'requested_deg': round(degrees, 1)}
        if not allowed and escape and note and note.startswith('turn_path_blocked'):
            moved = self._escape_for_turn(degrees, plan, cancel)
            if moved is not None and moved['status'] == 'completed':
                retry = self._turn(degrees, plan, cancel, verify, escape=False)
                return {**retry, 'escape': moved, 'travelled_m': moved.get('travelled_m', 0.)}
        if not allowed:
            return {**row, 'status': 'refused', 'reason': note}
        before, before_odom = self._heading(telemetry), self._odom_heading(telemetry)
        status, reason, _ = self._finite({'cmd': 'turn', 'deg': round(allowed, 1), 'rate': TURN_RATE},
                                         max(8., abs(allowed)/TURN_RATE + 8.), cancel)
        row.update(commanded_deg=round(allowed, 1), status=status, reason=note if status == 'completed' else reason)
        measured = odom = None
        if status == 'completed' and verify and before is not None and abs(allowed) <= 170:
            self._sleep(.4, cancel)
            after = self._telemetry()
            measured = wrap(self._heading(after) - before) if self._heading(after) is not None else None
            if before_odom is not None and self._odom_heading(after) is not None:
                odom = wrap(self._odom_heading(after) - before_odom)
            # No host correction: the firmware closes turns on its own IMU loop. The owner saw
            # turns reach the right angle and then backtrack under September 27 host corrections
            # driven by a drifting telemetry heading. Both readings are recorded for diagnosis.
        row['measured_deg'] = round(measured, 1) if measured is not None else None
        row['odometry_deg'] = round(odom, 1) if odom is not None else None
        # Report the controller's closed-loop turn unless both sensors agree otherwise.
        agreed = measured is not None and odom is not None and abs(measured - odom) <= 5
        row['turned_deg'] = abs(measured if agreed else allowed if status == 'completed' else 0.)
        return row

    def _move(self, meters, plan, cancel, speed=None):
        allowed, note = move_allowance(meters, self.space())
        row = {'action': 'move', 'requested_m': round(meters, 2)}
        if not allowed:
            return {**row, 'status': 'refused', 'reason': note, 'travelled_m': 0.}
        start = self._telemetry().get('odom') or {}
        speed = speed or LIMITS[plan.origin]['speed_mps']
        status, reason, done = self._finite({'cmd': 'move', 'dist': round(allowed, 2), 'speed': speed},
                                            max(8., abs(allowed)/speed + 10.), cancel)
        end = (done or {}).get('odom') or self._telemetry().get('odom') or {}
        travelled = math.hypot(float(end.get('x', 0)) - float(start.get('x', 0)), float(end.get('y', 0)) - float(start.get('y', 0)))
        return {**row, 'commanded_m': round(allowed, 2), 'travelled_m': round(travelled, 2), 'status': status,
                'reason': note if status == 'completed' else reason}

    def _legs(self, meters, plan, cancel):
        """A long move as firmware legs of at most step_m, each against fresh clearance."""
        step = LIMITS[plan.origin]['step_m']
        remaining, rows = abs(meters), []
        while remaining > .05:
            leg = math.copysign(min(step, remaining), meters)
            rows.append(self._move(leg, plan, cancel))
            remaining -= abs(leg)
            if rows[-1]['status'] != 'completed' or rows[-1]['reason'] == 'shortened_by_clearance':
                break
        row = {**rows[-1], 'requested_m': round(meters, 2), 'travelled_m': round(sum(r['travelled_m'] for r in rows), 2)}
        if len(rows) > 1:
            row['legs'] = rows
        if row['status'] == 'completed' and remaining > .05:
            row['reason'] = 'shortened_by_clearance'
        return row

    def _rotation(self):
        """Base rotation since the plan started, from wheel odometry (the IMU drifts)."""
        telemetry, _ = self.link.snapshot()
        now = self._odom_heading(telemetry) if telemetry else None
        return wrap(now - self._start_heading) if now is not None and self._start_heading is not None else 0.

    def _predicted(self, offered):
        return wrap(offered['bearing_deg'] - self._rotation()) if offered.get('bearing_deg') is not None else None

    def _resolve(self, target, plan, now, fresh=True):
        """Re-find an offered target after motion: same ID, else same identity/label near
        where it should now be. Detections from before the latest motion are ignored."""
        offered = plan.targets.get(target) or {}
        kind = offered.get('kind') or ('nearest_person' if target == 'nearest_person' else None)
        since = self._view_since if fresh else float('-inf')
        predicted = self._predicted(offered)
        def near(rows, key):
            if predicted is None:
                return rows[0] if len(rows) == 1 else None
            close = [r for r in rows if abs(wrap(image_bearing(r['box'], self._neck()) - predicted)) <= 20]
            return min(close, key=lambda r: abs(wrap(image_bearing(r['box'], self._neck()) - predicted)), default=None)
        if kind in {'face', 'nearest_person'}:
            faces = [f for f in self._fresh_faces(now) if f['at'] >= since]
            if kind == 'nearest_person':
                match = max(faces, key=lambda f: f['box'][2]*f['box'][3], default=None)
            else:
                match = next((f for f in faces if f['track_id'] == target), None)
                identity = offered.get('person_id')
                if match is None and identity:
                    same = [f for f in faces if f.get('identity') == identity]
                    match = same[0] if len(same) == 1 else near(same, 'identity')
                if match is None and not identity:
                    match = near(faces, 'face')  # Track IDs change as the view rotates.
            if match is None:
                return None
            return {'kind': 'face', 'bearing_deg': image_bearing(match['box'], self._neck()), 'range_m': face_range(match['box']),
                    'box_width': match['box'][2], 'track_id': match['track_id'], 'person_id': match.get('identity')}
        if kind == 'object':
            source = self.object_tracks or self.objects
            rows = [r for r in (source() if source else ()) if r.get('at', now) >= since]
            match = next((r for r in rows if r['target_id'] == target), None)
            if match is None:
                match = near([r for r in rows if r['label'] == offered.get('label')], 'label')
            if match is None:
                return None
            return {'kind': 'object', 'bearing_deg': image_bearing(match['box'], self._neck()), 'range_m': None,
                    'box_width': match['box'][2], 'label': match['label']}
        if kind == 'radar_body':
            bodies = (self.radar.bodies() if self.radar else None) or []
            expected = predicted if predicted is not None else offered['bearing_deg']
            match = min((b for b in bodies if abs(wrap(b['bearing_deg'] - expected)) <= 25),
                        key=lambda b: abs(wrap(b['bearing_deg'] - expected)), default=None)
            return {'kind': 'radar_body', 'bearing_deg': match['bearing_deg'] if match else expected,
                    'range_m': (match or offered).get('range_m'), 'current': match is not None}
        if kind == 'voice_direction':
            return {'kind': 'voice_direction', 'bearing_deg': predicted, 'range_m': None}
        return None

    def _acquire(self, step, plan, cancel, timeout=2.5):
        """Wait for a detection from after the latest motion (objects refresh every 1.5 s)."""
        deadline = self.clock() + timeout
        while True:
            target = self._resolve(step.target, plan, self.clock())
            if target is not None or self.clock() >= deadline:
                return target
            self._sleep(.25, cancel)

    def _find_offered(self, step, plan, cancel):
        """Acquire a camera target; if unseen, turn to where it should be and look again."""
        target = self._acquire(step, plan, cancel)
        if target is not None:
            return target, 0., None
        offered = plan.targets.get(step.target) or {}
        predicted = self._predicted(offered)
        if predicted is None or offered.get('kind') not in {'face', 'object'}:
            return None, 0., 'target_not_currently_detected'
        turned = 0.
        if abs(predicted) > 10:
            turn = self._turn(max(-180., min(180., predicted)), plan, cancel)
            turned = turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                return None, turned, 'turn_toward_target_' + turn['status']
        target = self._acquire(step, plan, cancel, timeout=3.)
        return target, turned, None if target else 'target_not_found_where_it_was_seen'

    def _face_target(self, step, plan, cancel):
        row = {'action': 'face', 'target': step.target}
        target, turned, missing = self._find_offered(step, plan, cancel)
        if target is None:
            return {**row, 'status': 'lost' if turned else 'refused', 'reason': missing, 'turned_deg': turned}
        for attempt in range(3):
            bearing = wrap(target['bearing_deg'])
            if abs(bearing) <= 10:
                break
            turn = self._turn(max(-180., min(180., bearing)), plan, cancel)
            turned += turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                return {**row, **{k: v for k, v in turn.items() if k != 'action'}, 'turned_deg': turned}
            if target['kind'] not in {'face', 'object'}:
                self._sleep(1., cancel)
                break  # Priors: one turn toward them, then report what the camera sees.
            target = self._acquire(step, plan, cancel)
            if target is None:
                return {**row, 'status': 'lost', 'reason': 'target_not_redetected_after_turn', 'turned_deg': turned}
        faces = self._fresh_faces(self.clock())
        return {**row, 'status': 'completed', 'reason': None, 'turned_deg': turned,
                'final_bearing_deg': round(wrap(target['bearing_deg'])) if target['kind'] in CAMERA_KINDS else None,
                'faces_visible_after': len(faces)}

    def _approach(self, step, plan, cancel, budget_m):
        row = {'action': 'approach', 'target': step.target}
        target, turned, missing = self._find_offered(step, plan, cancel)
        if target is None:
            return {**row, 'status': 'lost' if turned else 'refused', 'reason': missing, 'travelled_m': 0., 'turned_deg': turned}
        kind = target['kind']
        stop_at = step.stop_at_m or DEFAULT_STOP_AT[kind]
        if kind in {'radar_body', 'voice_direction'}:
            # Priors only: turn toward them, then approach a face the camera actually sees.
            facing = self._face_target(step, plan, cancel)
            turned = facing.get('turned_deg', 0.)
            if facing['status'] != 'completed':
                return {**row, **{k: v for k, v in facing.items() if k not in {'action', 'target'}}, 'travelled_m': 0.}
            ahead = [f for f in self._fresh_faces(self.clock()) if abs(image_bearing(f['box'], self._neck())) <= 20]
            if ahead:
                step = DriveStep('approach', target=max(ahead, key=lambda f: f['box'][2])['track_id'], stop_at_m=stop_at)
                plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose,
                                 {**plan.targets, step.target: {'kind': 'face'}}, plan.expires_at)
                target, kind = self._resolve(step.target, plan, self.clock()), 'face'
            elif target.get('range_m'):
                distance = min(budget_m, target['range_m'] - stop_at)
                if distance < .05:
                    return {**row, 'status': 'completed', 'reason': 'already near radar range; no face in view',
                            'travelled_m': 0., 'turned_deg': turned}
                move = self._move(distance, plan, cancel)
                return {**row, 'status': move['status'], 'reason': 'no face in view; moved toward radar range',
                        'travelled_m': move.get('travelled_m', 0.), 'turned_deg': turned}
            else:
                return {**row, 'status': 'lost', 'reason': 'no_face_in_view_after_turn', 'travelled_m': 0., 'turned_deg': turned}
        if target is None:
            return {**row, 'status': 'lost', 'reason': 'target_not_redetected', 'travelled_m': 0., 'turned_deg': turned}
        if abs(target['bearing_deg']) > 12:
            facing = self._turn(max(-180., min(180., target['bearing_deg'])), plan, cancel)
            turned += facing.get('turned_deg', 0.)
            if facing['status'] != 'completed':
                return {**row, **{k: v for k, v in facing.items() if k not in {'action'}}, 'travelled_m': 0., 'turned_deg': turned}
            self._sleep(.8, cancel)
        controller = Approach(self.clock(), 'face' if kind in {'face', 'nearest_person'} else 'object',
                              stop_at, LIMITS[plan.origin]['speed_mps'], budget_m)
        result, reason = None, None
        try:
            while result is None:
                self._check(cancel)
                now = self.clock()
                telemetry = self._telemetry()
                ok, why = self.availability(now)
                if not ok:
                    result, reason = 'stopped', why
                    break
                self._view_since = now - 1.6   # Continuous motion: the latest detections are the view.
                current = self._resolve(step.target, plan, now)
                lin, ang, result, reason = controller.step(now, telemetry, self.space(now), current)
                if result is None:
                    self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                    self.wait(.15)
        finally:
            self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
            self.link.send({'cmd': 'stop'})
        final = self._resolve(step.target, plan, self.clock(), fresh=False)
        space = self.space()
        return {**row, 'status': result, 'reason': reason, 'travelled_m': round(controller.travel, 2),
                'turned_deg': turned, 'stop_at_m': stop_at,
                'clearance_at_stop_m': {k: (round(v, 2) if isinstance(v, float) else v) for k, v in space.items()},
                'final_approx_range_m': round(final['range_m'], 1) if final and final.get('range_m') else None}

    def _locate(self, description, cancel):
        """One fresh sharp look plus one hosted locate; bearing is base-relative."""
        if self._vision_calls >= PLAN_VISION_CALLS:
            raise ProviderFailure('PlanVisionBudgetReached')
        self._sleep(.4, cancel)   # Let the head camera settle after motion.
        image = self.look(max(self._view_since, self.clock() - .5))
        if image is None:
            return None, 'no_fresh_camera_frame'
        self._vision_calls += 1
        result = self.locator(description, image, cancel)
        if not result.get('found') or result.get('confidence') == 'low':
            return None, result.get('note') or 'not_found'
        from .drive_space import HALF_FOV
        bearing = (.5 - float(result['x'])) * 2 * HALF_FOV + self._neck()
        return {'bearing_deg': bearing, 'box_width': float(result['width']), 'note': result.get('note')}, None

    def _seek(self, step, plan, cancel):
        """Look for a described thing the detector cannot name; sweep until found."""
        row = {'action': 'seek', 'description': step.description}
        if self.locator is None or self.look is None:
            return {**row, 'status': 'refused', 'reason': 'vision_seek_unavailable', 'turned_deg': 0.}
        sign = -1. if step.prefer == 'right' else 1.
        turned, looks, notes = 0., 0, []
        while looks < SEEK_LOOKS:
            found, why = self._locate(step.description, cancel)
            looks += 1
            if found is not None:
                if abs(found['bearing_deg']) > 8:
                    turn = self._turn(max(-180., min(180., found['bearing_deg'])), plan, cancel)
                    turned += turn.get('turned_deg', 0.)
                    if turn['status'] != 'completed':
                        return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': turned, 'looks': looks}
                self._sought = {'description': step.description, 'box_width': found['box_width']}
                return {**row, 'status': 'completed', 'reason': 'found', 'note': found['note'],
                        'turned_deg': turned, 'looks': looks, 'vision_calls': self._vision_calls}
            notes.append(why)
            if looks < SEEK_LOOKS:
                turn = self._turn(sign * SEEK_SWEEP_DEG, plan, cancel)
                turned += turn.get('turned_deg', 0.)
                if turn['status'] != 'completed':
                    return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': turned, 'looks': looks}
        return {**row, 'status': 'not_found', 'reason': 'not_seen_in_any_look', 'turned_deg': turned,
                'looks': looks, 'notes': notes[-2:], 'vision_calls': self._vision_calls}

    def _approach_sought(self, step, plan, cancel, budget_m):
        """Vision-guided legs toward what seek found: re-locate, re-aim, move, repeat."""
        row = {'action': 'approach', 'target': 'sought'}
        if self._sought is None:
            return {**row, 'status': 'refused', 'reason': 'nothing_sought_yet', 'travelled_m': 0.}
        stand_off = max(FRONT_MARGIN, step.stop_at_m or DEFAULT_STOP_AT['object'])
        travelled, turned, misses = 0., 0., 0
        while True:
            found, why = self._locate(self._sought['description'], cancel)
            if found is None:
                misses += 1
                if misses >= 2:
                    return {**row, 'status': 'lost', 'reason': why, 'travelled_m': round(travelled, 2), 'turned_deg': turned}
                continue
            misses = 0
            if found['box_width'] >= .45:
                return {**row, 'status': 'completed', 'reason': 'target_fills_the_view', 'travelled_m': round(travelled, 2), 'turned_deg': turned}
            if abs(found['bearing_deg']) > 8:
                turn = self._turn(max(-90., min(90., found['bearing_deg'])), plan, cancel)
                turned += turn.get('turned_deg', 0.)
                if turn['status'] != 'completed':
                    return {**row, 'status': turn['status'], 'reason': turn['reason'], 'travelled_m': round(travelled, 2), 'turned_deg': turned}
            front = self.space()['front_m']
            leg = min(.8, budget_m - travelled, (front if front is not None else 0.) - stand_off)
            if leg < .1:
                reason = 'reached_front_stand_off' if budget_m - travelled >= .1 else 'travel_budget_reached'
                return {**row, 'status': 'completed' if reason.startswith('reached') else 'budget_reached',
                        'reason': reason, 'travelled_m': round(travelled, 2), 'turned_deg': turned}
            move = self._move(leg, plan, cancel)
            travelled += move.get('travelled_m', 0.)
            if move['status'] != 'completed':
                return {**row, 'status': move['status'], 'reason': move['reason'], 'travelled_m': round(travelled, 2), 'turned_deg': turned}

    def _find_person(self, step, plan, cancel):
        row = {'action': 'find_person'}
        if self._fresh_faces(self.clock()):
            return {**row, 'status': 'completed', 'reason': 'person_already_visible', 'turned_deg': 0.}
        candidates = [b['bearing_deg'] for b in ((self.radar.bodies() if self.radar else None) or [])]
        speaker = plan.targets.get('speaker')
        if speaker:
            candidates.append(speaker['bearing_deg'])
        prior = self._resolve(step.target, plan, self.clock()) if step.target else None
        if prior is not None:
            candidates.insert(0, prior['bearing_deg'])  # The model's chosen first place to look.
        candidates += [90., -90., 180.]
        heading, turned, looked = 0., 0., []
        for bearing in candidates:
            if len(looked) >= 4:
                break
            if any(abs(wrap(bearing - seen)) <= 30 for seen in looked + [0.]):
                continue
            delta = wrap(bearing - heading)
            turn = self._turn(max(-180., min(180., delta)), plan, cancel)
            if turn['status'] != 'completed':
                if turn['status'] == 'refused':
                    looked.append(bearing)
                    continue
                return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': turned}
            moved = turn['measured_deg'] if turn.get('measured_deg') is not None else turn['commanded_deg']
            heading, turned = wrap(heading + moved), turned + abs(moved)
            looked.append(bearing)
            self._sleep(1.2, cancel)
            faces = self._fresh_faces(self.clock())
            if faces:
                return {**row, 'status': 'completed', 'reason': 'person_in_view', 'turned_deg': turned,
                        'faces_visible_after': len(faces)}
        return {**row, 'status': 'not_found', 'reason': 'no_face_after_search', 'turned_deg': turned}

    def execute(self, plan: DrivePlan, cancel: Cancellation) -> DriveReceipt:
        receipt = DriveReceipt(plan.request_id, started_at=self.clock())
        if not self._lock.acquire(blocking=False):
            receipt.status, receipt.reason = 'refused', 'DriveBusy'
            return receipt
        prepared = False
        try:
            self._halt.clear()
            ok, reason = self.availability()
            if not ok or self.clock() >= plan.expires_at:
                receipt.status, receipt.reason = 'refused', reason or 'StaleDrivePlan'
                return receipt
            self.moving.set()
            self._notify(plan)
            self._sought, self._vision_calls = None, 0
            start = self.link.snapshot()[0]
            self._start_heading = self._odom_heading(start) if start else None
            if self.servos is not None and plan.park_arm:
                park = self.servos.prepare_base_motion(cancel)
                if park.status != 'pulses_reached':
                    receipt.status, receipt.reason = 'refused', 'ArmParkFailed:' + str(park.reason or park.status)
                    return receipt
                prepared = True
                self._view_since = self.clock() + .3   # Only detections of the centred-neck view.
                self._sleep(.6, cancel)
            budget = LIMITS[plan.origin]['total_m']
            for step in plan.steps:
                self._check(cancel)
                ok, reason = self.availability()
                if not ok:
                    receipt.steps.append({'action': step.action, 'status': 'refused', 'reason': reason})
                    break
                if step.action == 'turn':
                    row = self._turn(step.degrees, plan, cancel)
                elif step.action == 'move':
                    row = self._legs(step.meters, plan, cancel)
                elif step.action == 'face':
                    row = self._face_target(step, plan, cancel)
                elif step.action == 'approach' and step.target == 'sought':
                    row = self._approach_sought(step, plan, cancel, max(0., budget - receipt.travelled_m))
                elif step.action == 'approach':
                    # Closed-loop approach is bounded by the plan total, not the straight-move step cap.
                    row = self._approach(step, plan, cancel, max(0., budget - receipt.travelled_m))
                elif step.action == 'seek':
                    row = self._seek(step, plan, cancel)
                else:
                    row = self._find_person(step, plan, cancel)
                receipt.steps.append(row)
                receipt.travelled_m = round(receipt.travelled_m + row.get('travelled_m', 0.), 2)
                receipt.turned_deg = round(receipt.turned_deg + row.get('turned_deg', 0.), 1)
                self._check(cancel)  # A stop's 'superseded' done is a cancellation, not a partial plan.
                if row['status'] != 'completed':
                    break
            done = [r['status'] == 'completed' for r in receipt.steps]
            if len(done) == len(plan.steps) and all(done):
                receipt.status = 'completed'
            elif any(done) or receipt.travelled_m or receipt.turned_deg:
                receipt.status, receipt.reason = 'partial', receipt.steps[-1].get('reason')
            else:
                last = receipt.steps[-1] if receipt.steps else {}
                receipt.status = 'refused' if last.get('status') == 'refused' else last.get('status', 'failed')
                receipt.reason = last.get('reason')
        except Cancelled:
            receipt.status, receipt.reason = 'cancelled', 'stop_requested'
        except ProviderFailure as exc:
            receipt.status, receipt.reason = 'failed', str(exc)
        finally:
            try:
                if not self.link.failure and self.link.port is not None:
                    self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            if prepared:
                self.servos.finish_base_motion()
            self.motion_until = self.clock() + 1.
            self.moving.clear()
            self._notify(plan)
            receipt.finished_at = self.clock()
            self.history.append({'request_id': plan.request_id, 'origin': plan.origin, 'purpose': plan.purpose,
                                 'status': receipt.status, 'reason': receipt.reason,
                                 'travelled_m': receipt.travelled_m, 'turned_deg': receipt.turned_deg,
                                 'finished_at': receipt.finished_at})
            if self.trace:
                self.trace.emit('drive_receipt', **asdict(receipt), origin=plan.origin, purpose=plan.purpose)
            self._lock.release()
        return receipt

    def _notify(self, plan):
        for hook in self.on_motion:
            try:
                hook(plan)
            except Exception as exc:
                if self.trace:
                    self.trace.emit('drive_hook_failed', error_type=type(exc).__name__)

    # Reflex --------------------------------------------------------------
    def tick_reflex(self, now=None):
        """One back-off evaluation. Idle only; plans and the gamepad take priority."""
        now = self.clock() if now is None else now
        telemetry, _ = self.link.snapshot()
        if not telemetry or not self.reflex_allowed() or self.moving.is_set() or not self.availability(now)[0]:
            self.flinch.ticks = 0
            return None
        tof = telemetry.get('tof_mm') or {}
        readings = {k: (tof[k]/1000. if isinstance(tof.get(k), (int, float)) and tof[k] >= 0 else None)
                    for k in ('fl', 'fr', 'fl_radial', 'fr_radial') if k in tof}
        if not self.flinch.observe(readings, now):
            return None
        rear = self.space(now)['rear_m']
        distance = min(.30, (rear or 0.) - .30)
        if rear is None or distance < .10:
            if self.trace:
                self.trace.emit('drive_reflex', status='held', reason='rear_blind_or_cornered')
            return None
        # Straight reverse needs no throttle park (V2 interlock rule).
        plan = DrivePlan('reflex:' + uuid4().hex, (DriveStep('move', meters=-distance),), 'self_initiated',
                         'back off: someone came very close in front', {}, now + 5., park_arm=False)
        return self.execute(plan, Cancellation())

    def _reflex_loop(self):
        while not self._closing.wait(1.):
            try:
                self.tick_reflex()
            except Exception as exc:
                if self.trace:
                    self.trace.emit('drive_reflex', status='failed', error_type=type(exc).__name__)
