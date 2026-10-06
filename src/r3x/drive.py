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
from .drive_space import (ARMS, ARMS_TUCKED, allowed_turn, clearance, describe_bearing, face_range, image_bearing,
                          move_allowance, radar_bodies, steer, wall_angle, wrap, FRONT_MARGIN, PERSON_MARGIN,
                          SCRAPE_M, WALL_HOLD_M)
from .providers import Capability

# Owner-approved limits: 0.25 m/s and 1.5 m legs (first test); requested plans may cover 10 m
# (September 27: the 3 m first-test cap cut door trips short and refused 'go ten feet').
LIMITS = {
    'requested': {'speed_mps': .25, 'step_m': 1.5, 'total_m': 10., 'turn_deg': 360., 'total_turn_deg': 720., 'max_steps': 6},
    # October 2 owner ruling: Rex's own moves need no permission and no smaller limits; he
    # only must not drive into things and must respect the time-of-flight boundaries, which
    # the clearance checks enforce for every origin.
    'self_initiated': {'speed_mps': .25, 'step_m': 1.5, 'total_m': 10., 'turn_deg': 360., 'total_turn_deg': 720., 'max_steps': 6},
}
ACTIONS = ('turn', 'move', 'face', 'approach', 'find_person', 'seek')
SEEK_SWEEP_DEG = 80.      # Just under the ~90 degree camera view, so consecutive looks overlap.
SEEK_LOOKS = 4            # Current view plus three turns: about a full circle.
PLAN_VISION_CALLS = 40    # Hosted looks per plan (search, re-locating while rolling, routes around things).
SELF_VISION_CALLS = 8     # Rex's own curiosity gets fewer paid looks than a human request.
# October 3: he saw the microwave over a counter that juts out and could not plan a way
# around it; he stopped at a table with open floor beside it. When an approach is blocked
# he now looks at the floor (head tipped slightly down), and a hosted look at that picture
# plus the range sensors by direction picks the next leg. He keeps his own position as he
# goes, so afterwards he can turn back to where the target should be and check.
ROUTE_LEGS = 8            # Camera-chosen legs per approach (five ran out part-way round a kitchen island).
ROUTE_LEG_M = 1.5
ROUTE_LOOK_DOWN_DEG = 12. # Enough to bring the floor a metre ahead into the picture.
# Owner, October 5: the head needs about 1.5 s after tipping down before it is still
# enough for a clear picture; the 0.3-0.5 s allowed before gave blurred route looks.
HEAD_SETTLE_S = 1.5
ROUTE_BEYOND_VIEW_DEG = 60.   # The open way lies past the picture's edge: turn this far and go.
ROUTE_SLACK_DEG = 60.     # After a leg, before two sightings fix its position, the thing may sit this far off.
SIDE_WAY_M = .8           # A side sensor this open is a way around when nothing in front is.
# Going around what blocks a vision-guided approach (owner request September 27: he stopped
# 15 ft short of a door with open floor beside the obstacle, or pointed slightly into a wall).
DETOUR_DEG = 30.          # The sought thing stays inside the ~90 degree view after the turn.
DETOUR_MORE_DEG = 25.     # Once more when the first turn leaves the obstacle in the path.
DETOUR_M = .6
MAX_DETOURS = 2
SIDE_OPEN_M = .5          # A front band this much past the stand-off counts as a way around.
PERSON_REACH = .8         # Face range minus a front return within this may be knees, feet or chair legs.
CRUISE_LOOK_S = 2.        # While rolling, a new look starts at most this often (hosted looks take 2-5 s).
AIM_DEG = 15.             # Re-aim only past this while the path ahead is open (legs re-locate anyway).
LOST_AFTER_S = 2.5         # Unseen this long mid-approach: stop and look for them (was 6 s, then gave up).
LOOK_NECK_MAX = 40.        # Head looks while re-finding a person stay inside the talking gaze range.
MAX_REACQUIRES = 3
VOICE_LEG_M = 2.           # With only a voice to go on, roll this far toward it looking for a face.
# October 3 hallway run: pointed slightly into a hallway wall he refused every forward move,
# though straight ahead was open. One short side beside an open centre is a wall to turn
# away from before moving, not an obstacle; tight spaces are driven slower, never refused
# while the wall stays outside the hold distance.
STRAIGHTEN_DEG = 12.      # One turn away from the wall; repeated while it still crosses the path.
MAX_STRAIGHTEN = 3
STRAIGHTEN_WITHIN_M = .8  # Only walls this near ahead: a far one is met by a later leg.
SIDE_OPEN_MORE_M = .3     # Straight ahead and the far side this much more open than the short side.
# October 5 evening: angled a dozen degrees into a 1.2 m hallway's wall, every guarded leg
# stopped within 0.3 m on its hold, the straighten turn did not fire once the matrix saw
# the wall ahead too, and he ended against the door jamb. The firmware was nudging him
# away from the wall all along (10 degrees over those legs); the host kept stopping him
# first. In tight space the host now drives a slow continuous crawl, lets the firmware's
# steering finish its work, and adds only what the firmware cannot read (drive_space.steer).
# The door beyond is narrower still (about 0.8 m for a 0.54 m body): the same crawl.
CRAWL_SPEED = .12         # Top crawl speed; the firmware's stop zone at this speed is about 0.10 m.
CRAWL_MIN_SPEED = .06
CRAWL_TICK_S = .15        # Setpoint refresh, inside the firmware's 300 ms drive deadman.
CRAWL_STALL_S = 2.        # No wheel progress this long: something holds him.
HALLWAY_M = .5            # A side return within this on either side is tight space: crawl.
STRAIGHTEN_MIN_DEG = 8.   # The turn away from a wall is the angle it converges by, within these.
STRAIGHTEN_MAX_DEG = 30.
STRAIGHTEN_MARGIN_DEG = 3.
STRAIGHTEN_SIDE_M = .5    # A wall this near beside him that converges ahead is turned away from first.
TIGHT_SIDE_M = .35        # A side return this close, or...
TIGHT_CORNER_M = .5       # ...a front-corner return this close, is a tight space: go slower.
TIGHT_PACE = .6
# October 3, second run. A voice's direction is remembered as a room heading, so it stays
# right after he turns and for requests spoken while he moves (the model re-used "88 degrees
# left" after he had already turned to face it, and he ended up facing away).
VOICE_MEMORY_S = 120.
# Closing on a target, the firmware's wall-steering treats what is ahead as something to go
# around (owner: "the firmware starts steering him away rather than just slowing him down").
# The host turns that steering off for the last stretch; the firmware still slows and stops.
# October 3, third run: with the owner in view straight ahead, a three-sample bearing 115
# degrees to the left (a recurring phantom near the array's side) became the speaker target
# and he turned away. A bearing that agrees with a visible face is refined to that face (at
# close range the array sits well beside the camera: -25 degrees read for a centred face);
# one that points away from everyone in view moves the remembered voice only when strong.
VOICE_FACE_AGREE_DEG = 50.
VOICE_STRONG_SAMPLES = 8
VOICE_STRONG_SHARE = .8
CLOSING_M = 1.2           # Something this near straight ahead, with the target ahead too.
CLOSING_DEG = 20.
NEAR_AHEAD_M = .4         # Arrival: something straight ahead within this of the stand-off...
NEAR_WIDTH = .3           # ...and the thing at least this wide in view (a box in front of a far door is not it).
# October 3 evening: three microwave trips reached it (30-40% of the view, up from 9%) and
# then spent up to 75 s and 300 degrees looking for a way "around" the counter under it; one
# was reported as a failure. A thing that wide which has at least doubled in the picture
# since he first saw it is near, whatever the sensors are stopping him on.
GROWN_NEAR = 2.
# Locator boxes are not distances: a hallway door 4 m away came back 40% of the view wide.
FILLS_VIEW = .6
TURN_RATE = 45.          # V2 overshoot grows with rate; autonomous V2 turns used 40.
# The base coasts after the firmware ends a turn. Thirty-odd logged turns (October 1-3)
# ended 2-10 degrees past the command, more at the faster rate: about 0.11 s of coasting.
# Turns of 12 degrees or more are commanded that much short, so they end near the request
# (owner: "sometimes the turning goes a little too far and then he loses sight").
TURN_COAST_S = .11
TURN_COAST_FROM_DEG = 12.
# Owner request October 3: his moves need not always be the same speed, turns included.
# The model picks a pace per step; these scale the approved speed and turn rate downward
# only. Unverified on the robot: confirm the slow turn does not stall on carpet.
PACE = {'slow': .6, 'easy': .8, 'normal': 1., None: 1.}
# Owner feedback: 4-5 ft was too far (September 27); 0.8 m by face left him at a seated
# person's knees, "a little too close" (October 3).
DEFAULT_STOP_AT = {'face': 1., 'nearest_person': 1., 'object': .6, 'radar_body': .9, 'voice_direction': .9}
# Pushed at every connect; config can only lower firmware hard caps.
CONFIG = {'max_lin': .25, 'max_ang': round(math.radians(60), 3), 'accel_lin': .35, 'accel_ang': 2.,
          'stop_zone_m': .15, 'slow_zone_m': .6, 'default_turn_rate': TURN_RATE,
          'watchdog_ms': 500, 'drive_expiry_ms': 300,
          # V2 pushes these: firmware defaults keep the gamepad owner until Start is pressed.
          'manual_autoreturn': True, 'manual_idle_return_secs': 4,
          # The firmware's own default wall-steering gain (V2 calib.h ASSIST_GAIN). The host
          # zeroes it while closing on a target; pushing it at every connect means a run
          # that died mid-approach cannot leave the steering off for the next one.
          'assist_gain': 2.}
# Match the firmware's charger-entry threshold and the operator battery gauge.
# A freshly unplugged pack can retain 13.61 V for minutes; 13.6 V reintroduced
# the old surface-charge lockout even after firmware had released its latch.
CHARGER_MV = 14000
CAMERA_KINDS = {'face', 'nearest_person', 'object'}


class ArmParkFailed(ProviderFailure):
    """The throttle arm did not park before wheel motion; the plan is refused unmoved."""


@dataclass(frozen=True)
class DriveStep:
    action: str
    degrees: float | None = None
    meters: float | None = None
    target: str | None = None
    stop_at_m: float | None = None
    description: str | None = None   # seek: what to look for, in the person's words.
    prefer: str | None = None        # seek: sweep left or right first.
    pace: str | None = None          # slow, easy or normal (None): scales this step's speed and turn rate.


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
        if now - self.started >= max(20., self.max_travel / .08):
            # Was a flat 20 s: at the easy pace that is four metres, and a 6.9 m "come here"
            # ended "timed out" 0.9 m from the person (October 3).
            return 0., 0., 'timeout', 'approach time limit'
        if self.travel >= self.max_travel:
            return 0., 0., 'distance_limit', 'distance limit reached before the target'
        # What the body would drive into; a hallway wall beside the path is not in the way.
        front = space['path_m'] if space.get('path_m') is not None else space['front_m']
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
        # only. Personal distance comes from the calibrated face range below. The closer
        # knee margin applies only when the return could be the person; a return well
        # short of the face is furniture (September 27: nearly drove into something).
        person_ahead = (self.kind == 'face' and
                        (not self.ranges or sorted(self.ranges)[len(self.ranges)//2] - front <= PERSON_REACH))
        budget = front - (PERSON_MARGIN if person_ahead else FRONT_MARGIN)
        if budget <= max(.03, brake):
            self.front_near_since = self.front_near_since or now
            if now - self.front_near_since >= .6 and speed_now <= .08:
                centred = target is not None and abs(target['bearing_deg']) <= 15
                if self.kind != 'face' and centred:
                    return 0., 0., 'completed', 'reached stand-off in front of target'
                return 0., 0., 'blocked', 'front stand-off reached before the target'
            return 0., 0., None, 'braking at front stand-off'
        self.front_near_since = None
        if now - self.last_seen >= LOST_AFTER_S:
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
            return 0., 0., 'distance_limit', 'distance limit reached before the target'
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
        self.sounds = None          # MotionSounds: whir while the wheels move, accents at the end
        self._origin = 'requested'
        self._pace = 1.
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
        self.router = None            # (image, facts, cancel) -> hosted choice of open floor to drive to
        self._at = [0., 0.]           # His own position in metres since connect, kept from wheel travel along the IMU heading.
        self._dead_ends = []          # (x, y, room heading) of legs that went nowhere in this plan.
        self._odom_mark = None
        self.look = None              # (after) -> fresh sharp camera image, or None
        self._sought = None           # description, note and absolute heading of the last thing sought
        self._vision_calls = 0
        self._parked = False
        self._plan_parks = False
        self._view_since = float('-inf')
        self._start_heading = None
        self._voice = None            # Room heading and time of the last located voice.
        self._voice_key = None
        self._assist_gain = None      # The firmware's wall-steering gain, as acknowledged at connect.
        self._assist_on = True

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
        # Only firmware that reports the steering gain can have it switched (0.2.x does).
        self._assist_gain = CONFIG['assist_gain'] if 'assist_gain' in (ack.get('config') or {}) else None
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
        if self.sounds:
            self.sounds.stop(now=True)   # A stop means silence now, not at the clip's end (V2).
        try:
            if self.link.port is not None and not self.link.failure:
                self.link.send({'cmd': 'stop'})
        except ProviderFailure:
            pass

    def close(self):
        self._closing.set()
        self.cancel()
        self._assist(True)
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
        if origin=='self_initiated' and self._pose_held():
            ok, reason = False, 'human_pose_held'
        space = self.space(now)
        targets = []
        faces = self._fresh_faces(now)
        def path(bearing, distance=None):
            # Front sensors cover roughly +/-25 degrees: which part of the path a straight
            # approach crosses. Walls beside the path (a hallway) are not in its way.
            band = 'path_center_m' if abs(bearing) <= 8 else 'path_left_m' if bearing > 0 else 'path_right_m'
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
        # Offered even with nobody in view: "turn around and come to me" resolves it after the
        # turn (September 27: with no face visible the model silently dropped "come to me").
        targets.append({'target_id': 'nearest_person', 'kind': 'nearest_person', 'visible_now': bool(faces),
                        'where': 'largest visible face, resolved when the step runs' if faces else
                                 'nobody in view now; facing or approaching it looks for the person first '
                                 '(toward the last voice, radar bodies, then around)'})
        for row in (self.objects() if self.objects else ())[:8]:
            bearing = image_bearing(row['box'], self._neck())
            targets.append({'target_id': row['target_id'], 'kind': 'object', 'label': row['label'],
                            'bearing_deg': round(bearing), 'where': describe_bearing(bearing), 'path': path(bearing)})
        bodies = self.radar.bodies() if self.radar else None
        for body in bodies or ():
            targets.append({'target_id': body['target_id'], 'kind': 'radar_body', 'bearing_deg': body['bearing_deg'],
                            'range_m': body['range_m'], 'moving': body['moving'], 'where': describe_bearing(body['bearing_deg'])})
        telemetry, _ = self.link.snapshot()
        facing = self._heading(telemetry) if telemetry else None
        heard = (speaker_direction and speaker_direction.get('status') == 'observed'
                 and speaker_direction.get('bearing_deg') is not None)
        if heard and facing is not None and speaker_direction.get('ended_at') != self._voice_key:
            # Each utterance's bearing is taken once, against the heading he had when it was heard.
            self._voice_key = speaker_direction.get('ended_at')
            self._note_voice(speaker_direction, facing, faces, now)
        voice = self._voice_bearing(now)
        if origin == 'requested' and (voice is not None or heard):
            bearing = voice if voice is not None else float(speaker_direction['bearing_deg'])
            targets.append({'target_id': 'speaker', 'kind': 'voice_direction', 'bearing_deg': round(bearing),
                            'where': describe_bearing(bearing),
                            'heard_seconds_ago': round(now - self._voice['at']) if voice is not None else 0,
                            'basis': self._voice.get('basis', 'voice') if voice is not None else 'voice'})
        displacement = None
        if telemetry and self.origin:
            odom = telemetry.get('odom') or {}
            dx, dy = float(odom.get('x', 0)) - self.origin[0], float(odom.get('y', 0)) - self.origin[1]
            heading = self._heading(telemetry)
            displacement = {'distance_m': round(math.hypot(dx, dy), 2),
                            'heading_change_deg': round(wrap(heading - self.origin[2])) if heading is not None and self.origin[2] is not None else None}
        return {'available': ok, 'unavailable_reason': reason, 'base_status': self.base_status(),
                'clearance_m': {k: (round(v, 1) if isinstance(v, float) else v) for k, v in space.items() if k != 'health'},
                'clearance_meaning': ('Nearest time-of-flight return in meters by direction, measured now. path_m is what the '
                    'body would drive into straight ahead (path_left_m, path_center_m and path_right_m say which side of '
                    'it); front_m is the nearest front return even beside the path; '
                    'left_m/right_m are the sides and rear_m behind. 3.5 or more means nothing within range; null means '
                    'not reporting. The sensors see objects, not what they are.'),
                'radar_view': {'status': 'unavailable' if self.radar and bodies is None else ('connected' if self.radar else 'not_configured'),
                    'bodies': [{k: b.get(k) for k in ('bearing_deg', 'range_m', 'moving')} | {'where': describe_bearing(b['bearing_deg'])}
                               for b in bodies or ()],
                    'meaning': ('LD2450 radar ring on its own controller, all around the base. It tracks moving or recently '
                                'moving bodies only: a still person can vanish, and a body is not an identity.')},
                'sensor_notes': space['health'], 'targets': targets,
                'radar': 'unavailable' if self.radar and bodies is None else ('connected' if self.radar else 'not_configured'),
                'vision_seek': self.locator is not None and self.look is not None,
                'last_sought': self._sought['description'] if self._sought else None,
                # step_m is an internal leg length; live October 1 the model read it as the most it
                # could ever move and answered "back up five feet" with 1.5 m.
                'limits': {'max_move_m': LIMITS[origin]['total_m'], 'max_turn_deg': LIMITS[origin]['turn_deg'],
                           'max_total_turn_deg': LIMITS[origin]['total_turn_deg'], 'max_steps': LIMITS[origin]['max_steps'],
                           'speed_mps': LIMITS[origin]['speed_mps'],
                           'meaning': 'max_move_m is the whole distance one plan may cover; long moves run as several '
                                      'legs automatically and clearance still stops the base at obstacles.'},
                'origin': origin,
                'displacement_since_startup': displacement,
                'recent_motion': list(self.history),
                'meaning': ('Robot-relative bearings, positive = left. Face/object bearings come from the head camera '
                            '(approximate, re-measured with the neck centered when a step runs). Radar bodies are moving '
                            'bodies seen by the base radar ring: where to look, not who. speaker is where the last located '
                            'voice came from, kept relative to where the base faces now as it turns (heard_seconds_ago is its age). Clearance is what base sensors report now; there is no map. '
                            'path.direct_approach blocked_early means a straight approach will stop at an obstacle first.')}

    def _note_voice(self, direction, facing, faces, now):
        """Remember where a voice came from, weighing it against who is in view.

        This places a direction to turn toward. It never says that the visible person
        spoke: attribution stays with the speaker tracker."""
        bearing = float(direction['bearing_deg'])
        seen = [image_bearing(f['box'], self._neck()) for f in faces]
        nearest = min(seen, key=lambda b: abs(wrap(bearing - b)), default=None)
        basis = 'voice'
        if nearest is not None and abs(wrap(bearing - nearest)) <= VOICE_FACE_AGREE_DEG:
            bearing, basis = nearest, 'voice_and_face'
        elif nearest is not None and self._voice_bearing(now) is not None:
            clusters = direction.get('clusters') or [{}]
            strong = ((direction.get('samples') or 0) >= VOICE_STRONG_SAMPLES and
                      (clusters[0].get('weight_share') or 0.) >= VOICE_STRONG_SHARE)
            if not strong:
                # Kept as evidence in the turn's record; it only does not move the target.
                if self.trace:
                    self.trace.emit('voice_direction_not_adopted', bearing_deg=round(bearing),
                                    samples=direction.get('samples'), nearest_face_deg=round(nearest),
                                    kept_deg=round(self._voice_bearing(now)))
                return
        self._voice = {'heading': wrap(facing + bearing), 'at': now, 'basis': basis}

    def _voice_bearing(self, now=None):
        """Base-relative bearing of the last located voice, while it is recent."""
        now = self.clock() if now is None else now
        telemetry, _ = self.link.snapshot()
        facing = self._heading(telemetry) if telemetry else None
        if self._voice is None or facing is None or now - self._voice['at'] > VOICE_MEMORY_S:
            return None
        return wrap(self._voice['heading'] - facing)

    def _assist(self, on):
        """Firmware wall-steering on or off (its own slow and stop zones are untouched).

        Off sets the steering gain to zero: the firmware keeps only its last-resort push
        away from a wall inside 13 cm. A partial config is applied at once and does not
        interrupt motion (V2 config_params.cpp)."""
        if self._assist_gain is None or on == self._assist_on:
            return
        try:
            self.link.send({'cmd': 'config', 'assist_gain': self._assist_gain if on else 0.})
            self._assist_on = on
        except ProviderFailure:
            pass

    @staticmethod
    def _near_target(space, bearing, range_m=None, stop_at=0.):
        """The target is ahead and near: what the front sensors see now is the target or
        stands right before it, so slowing and stopping is wanted, not steering around."""
        if bearing is None or abs(bearing) > CLOSING_DEG:
            return False
        ahead = space.get('front_center_m')
        return (range_m is not None and range_m - stop_at <= CLOSING_M) or (ahead is not None and ahead <= CLOSING_M)

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

    def _finite(self, command, timeout, cancel, guard=None):
        """Run one firmware turn or move to its done. guard() may name a reason to stop early."""
        seq = self.link.send(command)
        ack = self._await_ack(seq, .5, self.wait)
        if ack is not None and not ack.get('accepted'):
            return 'refused', 'controller_refused_' + str(ack.get('reason')), None
        deadline = self.clock() + timeout
        if self.sounds:
            self.sounds.start('turn' if command['cmd'] == 'turn' else 'move', self._origin)
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
                self._track()
                why = guard() if guard else None
                if why:
                    self.link.send({'cmd': 'stop'})
                    self._view_since = self.clock() + .3
                    self._sleep(.3, cancel)   # Let the base settle before odometry is read.
                    return 'blocked', why, None
                self._check(cancel)
                self.wait(.05)
        except Cancelled:
            try:
                self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            raise
        finally:
            if self.sounds:
                self.sounds.stop()   # The wheels reported done: the whir ends with them.

    def _telemetry(self):
        telemetry, _ = self.link.snapshot()
        if not telemetry or not 0 <= self.clock() - telemetry['received_at'] <= .5:
            raise ProviderFailure('DriveTelemetryStale')
        self._track(telemetry)
        return telemetry

    def _track(self, telemetry=None):
        """Fold wheel travel since the last call into his own position.

        Distance comes from the wheels; direction from the IMU heading, because wheel
        odometry's own heading over-reads turns. Forward or backward is judged inside the
        odometry's frame, which is consistent with itself. Good for metres, not a map."""
        telemetry = telemetry or self.link.snapshot()[0]
        if not telemetry:
            return
        odom = telemetry.get('odom') or {}
        xy = (float(odom.get('x', 0)), float(odom.get('y', 0)))
        mark, self._odom_mark = self._odom_mark, xy
        heading = self._heading(telemetry)
        if mark is None or heading is None:
            return
        dx, dy = xy[0] - mark[0], xy[1] - mark[1]
        distance = math.hypot(dx, dy)
        if distance < 1e-4:
            return
        theta = odom.get('theta')
        if isinstance(theta, (int, float)) and dx*math.cos(theta) + dy*math.sin(theta) < 0:
            distance = -distance
        self._at[0] += distance * math.cos(math.radians(heading))
        self._at[1] += distance * math.sin(math.radians(heading))

    def _bearing_to(self, point):
        """Base-relative bearing and distance of a remembered room position from where he is now."""
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        if point is None or heading is None:
            return None, None
        self._track(telemetry)
        dx, dy = point[0] - self._at[0], point[1] - self._at[1]
        return wrap(math.degrees(math.atan2(dy, dx)) - heading), math.hypot(dx, dy)

    @staticmethod
    def _odom_heading(telemetry):
        theta = (telemetry.get('odom') or {}).get('theta')
        return math.degrees(float(theta)) if isinstance(theta, (int, float)) else None

    def _escape_for_turn(self, degrees, plan, cancel):
        """V2 swing escape, generalized: earn room by backing up (or pulling forward), then retry once."""
        space = self.space()
        rear, front = space['rear_m'], space.get('path_m')
        if rear is not None and rear - .35 >= .2:
            leg = -min(.5, rear - .35)
        elif front is not None and front - FRONT_MARGIN >= .3:
            leg = min(.4, front - FRONT_MARGIN)
        else:
            return None
        return self._move(leg, plan, cancel)

    def _turn(self, degrees, plan, cancel, verify=True, escape=True):
        telemetry = self._telemetry()
        allowed, note = allowed_turn(degrees, telemetry, self._arms())
        row = {'action': 'turn', 'requested_deg': round(degrees, 1)}
        if not allowed and escape and note and note.startswith('turn_path_blocked'):
            moved = self._escape_for_turn(degrees, plan, cancel)
            if moved is not None and moved['status'] == 'completed':
                retry = self._turn(degrees, plan, cancel, verify, escape=False)
                return {**retry, 'escape': moved, 'travelled_m': moved.get('travelled_m', 0.)}
        if not allowed:
            return self._refused_turn(row, note)
        if not self._parked and self.servos is not None and self._plan_parks:
            self._ensure_parked(cancel)
            telemetry = self._telemetry()
            allowed, note = allowed_turn(degrees, telemetry, self._arms())
            if not allowed:
                return self._refused_turn(row, note)
        before, before_odom = self._heading(telemetry), self._odom_heading(telemetry)
        rate = round(TURN_RATE * self._pace, 1)
        command = allowed
        if abs(allowed) >= TURN_COAST_FROM_DEG:
            command = math.copysign(abs(allowed) - rate * TURN_COAST_S, allowed)
        status, reason, _ = self._finite({'cmd': 'turn', 'deg': round(command, 1), 'rate': rate},
                                         max(8., abs(allowed)/rate + 8.), cancel)
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
        self._step_trace(row)
        return row

    def _refused_turn(self, row, note):
        """October 5: refused turns left no trace, so a hallway run could not be read."""
        row = {**row, 'status': 'refused', 'reason': note}
        self._step_trace(row)
        return row

    def _step_trace(self, row):
        """Every turn and move with where it left him: October 3, a trip turned 657 degrees
        and the log held only the total."""
        if self.trace:
            telemetry, _ = self.link.snapshot()
            heading = self._heading(telemetry) if telemetry else None
            self.trace.emit('drive_step', **{k: v for k, v in row.items() if k != 'legs'},
                            heading_deg=round(heading, 1) if heading is not None else None,
                            at_m=[round(v, 2) for v in self._at])

    def _arms(self):
        """Arm reach for the swing check: tucked when the servo owner parks them before any
        wheel command of this plan; V2's arms-out estimate when their position is unknown."""
        return ARMS_TUCKED if self.servos is not None and self._plan_parks else ARMS

    def _ensure_parked(self, cancel):
        """Arm in and neck centred right before the wheels first move, not at plan start:
        a plan that ends without moving makes no false 'about to go' gesture (September 27)."""
        if self._parked or self.servos is None or not self._plan_parks:
            return
        park = self.servos.prepare_base_motion(cancel)
        if park.status != 'pulses_reached':
            raise ArmParkFailed('ArmParkFailed:' + str(park.reason or park.status))
        self._parked = True
        self._view_since = self.clock() + .3   # Only detections of the centred-neck view.
        self._sleep(.6, cancel)

    def _pose_held(self):
        owner = getattr(self.servos,'owner',None)
        active = getattr(owner,'human_hold_active',None)
        return active() if active else bool(getattr(owner,'held_channels',()))

    def _vision_budget(self):
        return SELF_VISION_CALLS if self._origin == 'self_initiated' else PLAN_VISION_CALLS

    @staticmethod
    def _short_side(space, need):
        """+1 left / -1 right when only that side of the straight path is short of need while
        straight ahead and the other side are clearly more open: a wall beside, not ahead."""
        rooms = {1: space.get('path_left_m'), 0: space.get('path_center_m'), -1: space.get('path_right_m')}
        if any(value is None for value in rooms.values()):
            return None
        for side in (1, -1):
            if (rooms[side] - FRONT_MARGIN < need and
                    min(rooms[0], rooms[-side]) >= rooms[side] + SIDE_OPEN_MORE_M):
                return side
        return None

    def _wall_turn(self, space, need):
        """Degrees to turn in place before rolling (+ left), 0 for none.

        Away from a wall that crosses one side of the path (_short_side), or from a wall
        within half a metre beside him that the side sensors say converges ahead. The size
        is the convergence angle plus a margin, between 8 and 30 degrees; 12 when the side
        sensors cannot tell (a door jamb ahead rather than a wall beside)."""
        tof = (self.link.snapshot()[0] or {}).get('tof_mm')
        side = self._short_side(space, need)
        if side is None:
            for candidate in (1, -1):
                near = space.get('left_m' if candidate > 0 else 'right_m')
                angle = wall_angle(tof, candidate)
                if (near is not None and near <= STRAIGHTEN_SIDE_M and angle is not None
                        and angle >= STRAIGHTEN_MIN_DEG - STRAIGHTEN_MARGIN_DEG):
                    side = candidate
                    break
            if side is None:
                return 0.
        angle = wall_angle(tof, side)
        degrees = STRAIGHTEN_DEG if angle is None or angle <= 0 else angle + STRAIGHTEN_MARGIN_DEG
        return -side * max(STRAIGHTEN_MIN_DEG, min(STRAIGHTEN_MAX_DEG, degrees))

    def _straighten(self, need, plan, cancel):
        """Turn away from a wall beside the path before rolling forward.

        Owner, October 3: he should orient himself to avoid the walls before he moves.
        Turns in place, re-reading the sensors after each; never a reverse, and it stops
        as soon as no wall needs it, the turn is refused or the other side closes. A
        refused turn is not the end: the crawl that follows steers."""
        turned, first = 0., None
        for _ in range(MAX_STRAIGHTEN):
            degrees = self._wall_turn(self.space(), need)
            if not degrees or (first is not None and (degrees > 0) != (first > 0)):
                break
            first = degrees
            turn = self._turn(degrees, plan, cancel, escape=False)
            if turn['status'] != 'completed':
                break
            turned += math.copysign(turn.get('turned_deg', 0.), degrees)
        return turned

    @staticmethod
    def _caution(space):
        """Speed factor: slower where a wall is close beside or at a front corner (owner,
        October 3: in tight clearance hallways he should move slower)."""
        sides = [v for v in (space.get('left_m'), space.get('right_m')) if v is not None]
        corners = [v for v in (space.get('front_left_m'), space.get('front_right_m')) if v is not None]
        tight = (sides and min(sides) < TIGHT_SIDE_M) or (corners and min(corners) < TIGHT_CORNER_M)
        return TIGHT_PACE if tight else 1.

    @staticmethod
    def _tight(space, need):
        """Tight space for a forward move: a wall within half a metre of either side, a front
        corner close, or one side of the path limited while straight ahead is more open. The
        move is then a steered crawl rather than one firmware leg."""
        sides = [v for v in (space.get('left_m'), space.get('right_m')) if v is not None]
        corners = [v for v in (space.get('front_left_m'), space.get('front_right_m')) if v is not None]
        path, centre = space.get('path_m'), space.get('path_center_m')
        return bool((sides and min(sides) < HALLWAY_M) or (corners and min(corners) < TIGHT_CORNER_M)
                    or DriveBase._short_side(space, need) is not None
                    or (path is not None and centre is not None and centre - path >= SIDE_OPEN_MORE_M
                        and path - FRONT_MARGIN < need))

    @staticmethod
    def _crawl_hold(space, tof):
        """Why a crawl may not go on: something in the path straight ahead, a return inside
        the hold distance on the path, or a side wall about to touch while it closes in."""
        centre, path = space.get('path_center_m'), space.get('path_m')
        if centre is None or 'front_depth_matrix_unavailable' in space['health']:
            return 'front_clearance_unknown'
        if centre - FRONT_MARGIN < .05:
            return 'path_blocked'
        if path is not None and path < WALL_HOLD_M:
            return 'wall_beside_path_too_close'
        for side, key in ((1, 'left'), (-1, 'right')):
            near, corner = space.get(key + '_m'), space.get('front_radial_' + key + '_m')
            if near is not None and near < SCRAPE_M and ((corner is not None and corner < .25)
                                                         or (wall_angle(tof, side) or 0.) > 3.):
                return 'wall_too_close_' + key
        return None

    def _crawl(self, meters, plan, cancel, speed, row):
        """A slow forward crawl through tight space as a continuous drive setpoint.

        The firmware keeps steering him away from walls as it does on every move; the host
        no longer stops him for a wall beside the path and adds each tick what the firmware
        cannot read (drive_space.steer): the slant of a wall beside him and what the depth
        matrix sees crossing the path ahead. It runs until the distance is made, something
        is in the path ahead, a wall is about to touch, the firmware blocks, or the wheels
        stop making headway."""
        self._ensure_parked(cancel)
        start = self._telemetry()
        start_odom, start_heading = dict(start.get('odom') or {}), self._heading(start)
        speed = max(CRAWL_MIN_SPEED, min(speed * TIGHT_PACE, CRAWL_SPEED))
        row = {**row, 'commanded_m': round(meters, 2), 'slowed_for_tight_space': True, 'crawled': True}
        def travelled_from(odom):
            return math.hypot(float(odom.get('x', 0)) - float(start_odom.get('x', 0)),
                              float(odom.get('y', 0)) - float(start_odom.get('y', 0)))
        status, reason = 'completed', None
        travelled, progressed_at, last_mark = 0., self.clock(), 0.
        self._assist(True)
        if self.sounds:
            self.sounds.start('move', self._origin)
        try:
            seq = self.link.send({'cmd': 'drive', 'lin': round(speed, 3), 'ang': 0.})
            ack = self._await_ack(seq, .5, self.wait)
            if ack is not None and not ack.get('accepted'):
                return {**row, 'status': 'refused', 'reason': 'controller_refused_' + str(ack.get('reason')), 'travelled_m': 0.}
            while True:
                self._check(cancel)
                if self.link.failure:
                    raise ProviderFailure(self.link.failure)
                telemetry, _ = self.link.snapshot()
                if not telemetry or not 0 <= self.clock() - telemetry['received_at'] <= .5:
                    status, reason = 'blocked', 'drive_telemetry_stale'
                    break
                self._track(telemetry)
                travelled = travelled_from(telemetry.get('odom') or {})
                if travelled >= meters - .02:
                    break
                if travelled - last_mark >= .01:
                    last_mark, progressed_at = travelled, self.clock()
                elif self.clock() - progressed_at > CRAWL_STALL_S:
                    status, reason = 'blocked', 'no_progress'
                    break
                if telemetry.get('blocked_dir') in ('front', 'both'):
                    status, reason = 'blocked', 'blocked'
                    break
                space = self.space()
                why = self._crawl_hold(space, telemetry.get('tof_mm'))
                if why:
                    status, reason = 'blocked', why
                    break
                ang = steer(space, telemetry.get('tof_mm'))
                lin = speed if abs(ang) < .3 else max(CRAWL_MIN_SPEED, speed * .6)
                self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                self.wait(CRAWL_TICK_S)
        finally:
            try:
                self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
                self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            if self.sounds:
                self.sounds.stop()
        self._view_since = self.clock() + .3
        self._sleep(.3, cancel)   # Let the base settle before odometry is read.
        self._track()
        after = self.link.snapshot()[0] or {}
        travelled = max(travelled, travelled_from(after.get('odom') or {}))
        if reason == 'path_blocked' and travelled >= .05:
            status, reason = 'completed', 'shortened_by_clearance'   # Went as far as the room ahead allowed.
        row.update(travelled_m=round(travelled, 2), status=status, reason=reason)
        heading = self._heading(after) if after else None
        if start_heading is not None and heading is not None:
            row['steered_deg'] = round(wrap(heading - start_heading), 1)
        self._step_trace(row)
        return row

    def _move(self, meters, plan, cancel, speed=None, straighten=True):
        row = {'action': 'move', 'requested_m': round(meters, 2)}
        need = min(meters, STRAIGHTEN_WITHIN_M)
        straightened = self._straighten(need, plan, cancel) if meters > 0 and straighten else 0.
        if straightened:
            # Reported so the model can say he lined up with the hallway first.
            row['turned_away_from_wall_deg'] = round(straightened, 1)
            row['turned_deg'] = abs(round(straightened, 1))
        speed = speed or LIMITS[plan.origin]['speed_mps'] * self._pace
        space = self.space()
        if meters > 0 and self._tight(space, need):
            held = self._crawl_hold(space, (self.link.snapshot()[0] or {}).get('tof_mm'))
            if held:
                return {**row, 'status': 'refused', 'reason': held, 'travelled_m': 0.}
            return self._crawl(meters, plan, cancel, speed, row)
        allowed, note = move_allowance(meters, space)
        if not allowed:
            return {**row, 'status': 'refused', 'reason': note, 'travelled_m': 0.}
        self._ensure_parked(cancel)
        allowed, note = move_allowance(meters, self.space())   # Clearance after the park settles.
        if not allowed:
            return {**row, 'status': 'refused', 'reason': note, 'travelled_m': 0.}
        start = self._telemetry().get('odom') or {}
        status, reason, done = self._finite({'cmd': 'move', 'dist': round(allowed, 2), 'speed': round(speed, 3)},
                                            max(8., abs(allowed)/speed + 10.), cancel)
        end = (done or {}).get('odom') or self._telemetry().get('odom') or {}
        travelled = math.hypot(float(end.get('x', 0)) - float(start.get('x', 0)), float(end.get('y', 0)) - float(start.get('y', 0)))
        row = {**row, 'commanded_m': round(allowed, 2), 'travelled_m': round(travelled, 2), 'status': status,
               'reason': note if status == 'completed' else reason}
        self._track()
        self._step_trace(row)
        return row

    def _legs(self, meters, plan, cancel):
        """A long move as firmware legs of at most step_m, each against fresh clearance."""
        step = LIMITS[plan.origin]['step_m']
        remaining, rows = abs(meters), []
        while remaining > .05 and len(rows) < 16:
            leg = math.copysign(min(step, remaining), meters)
            rows.append(self._move(leg, plan, cancel))
            last = rows[-1]
            if last['status'] != 'completed' or last['reason'] == 'shortened_by_clearance':
                break
            if last.get('crawled'):
                # A crawl through tight space: carry on from fresh readings while it makes headway.
                if last.get('travelled_m', 0.) < .05:
                    break
                remaining -= last['travelled_m']
                continue
            remaining -= abs(leg)
        row = {**rows[-1], 'requested_m': round(meters, 2), 'travelled_m': round(sum(r['travelled_m'] for r in rows), 2)}
        turned = sum(r.get('turned_deg', 0.) for r in rows)
        if turned:
            row['turned_deg'] = round(turned, 1)
            row['turned_away_from_wall_deg'] = round(sum(r.get('turned_away_from_wall_deg', 0.) for r in rows), 1)
        if any(r.get('slowed_for_tight_space') for r in rows):
            row['slowed_for_tight_space'] = True
        if any(r.get('crawled') for r in rows):
            row['crawled'] = True
            row['steered_deg'] = round(sum(r.get('steered_deg', 0.) for r in rows), 1)
        if len(rows) > 1:
            row['legs'] = rows
        if row['status'] == 'completed' and remaining > .05:
            row['reason'] = 'shortened_by_clearance'
        return row

    def _rotation(self):
        """Base rotation since the plan started, from the IMU heading (odometry without one).

        October 3: across twenty logged turns the IMU read the commanded angle plus a 3-10
        degree overshoot, while wheel odometry read up to twice that on left turns (wheel
        slip). Predicting where a target should be from odometry then turned him the wrong
        amount ("turned 86 degrees" looking for something 30 degrees away)."""
        telemetry, _ = self.link.snapshot()
        now = self._heading(telemetry) if telemetry else None
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
        offered = plan.targets.get(step.target) or {}
        anyone = step.target == 'nearest_person'
        target = self._acquire(step, plan, cancel, timeout=1.2 if anyone and not offered.get('visible_now', True) else 2.5)
        if target is not None:
            return target, 0., None
        if anyone:
            # "Come here" with nobody in view is a request to find the person, never a
            # refusal (October 3: refused once, then given up on after a wrong turn).
            search = self._find_person(DriveStep('find_person'), plan, cancel)
            target = self._acquire(step, plan, cancel) if search['status'] == 'completed' else None
            return target, search.get('turned_deg', 0.), None if target else 'no_person_found_after_looking_around'
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
        travelled, extra = 0., {}
        if kind in {'radar_body', 'voice_direction'}:
            # Priors: turn toward them, then approach a face the camera actually sees.
            facing = self._face_target(step, plan, cancel)
            turned = facing.get('turned_deg', 0.)
            if facing['status'] != 'completed':
                return {**row, **{k: v for k, v in facing.items() if k not in {'action', 'target'}}, 'travelled_m': 0.}
            ahead = self._faces_ahead()
            if not ahead and kind == 'voice_direction':
                # Owner, September 27: "come towards me" needs only a voice. Roll toward it and
                # switch to the face approach as soon as a face comes into view.
                moved, outcome = self._toward_voice(plan, cancel, min(budget_m, VOICE_LEG_M))
                travelled += moved
                ahead = self._faces_ahead()
                if not ahead:
                    return {**row, 'status': 'blocked' if outcome == 'blocked' else 'completed',
                            'reason': ('path blocked on the way toward the voice; no face seen' if outcome == 'blocked'
                                       else 'moved toward the voice; no face seen'),
                            'travelled_m': round(travelled, 2), 'turned_deg': turned, 'toward_voice': True}
                extra['toward_voice_m'] = round(moved, 2)
            if ahead:
                step = DriveStep('approach', target=max(ahead, key=lambda f: f['box'][2])['track_id'], stop_at_m=stop_at)
                plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose,
                                 {**plan.targets, step.target: {'kind': 'face'}}, plan.expires_at)
                target, kind = self._resolve(step.target, plan, self.clock()), 'face'
                stop_at = step.stop_at_m or DEFAULT_STOP_AT['face']
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
            return {**row, 'status': 'lost', 'reason': 'target_not_redetected', 'travelled_m': round(travelled, 2), 'turned_deg': turned}
        if abs(target['bearing_deg']) > 12:
            facing = self._turn(max(-180., min(180., target['bearing_deg'])), plan, cancel)
            turned += facing.get('turned_deg', 0.)
            if facing['status'] != 'completed':
                return {**row, **{k: v for k, v in facing.items() if k not in {'action'}}, 'travelled_m': 0., 'turned_deg': turned}
            self._sleep(.8, cancel)
        detours, reacquired, tried = [], [], []
        while True:
            result, reason, moved, last = self._run_approach(step, plan, cancel, kind, stop_at, budget_m - travelled)
            travelled += moved
            if (result == 'lost' and kind in {'face', 'nearest_person'} and len(reacquired) < MAX_REACQUIRES
                    and budget_m - travelled > .1):
                # Owner, September 27: after the firmware nudged him around an obstacle the person
                # left the frame. Look with the head first, then turn the body, then carry on.
                found = self._reacquire(plan, cancel, (last or {}).get('heading'))
                reacquired.append(found['how'] if found else 'not_found')
                turned += found['turned_deg'] if found else 0.
                if found:
                    step = DriveStep('approach', target=found['track_id'], stop_at_m=stop_at)
                    plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose,
                                     {**plan.targets, step.target: {'kind': 'face'}}, plan.expires_at)
                    kind = 'face'
                    continue
                reason = 'person not found again after looking'
            far = (last is not None and last.get('range_m') is not None and last['range_m'] - stop_at > .5)
            if result != 'blocked' or kind not in {'face', 'nearest_person'} or not far:
                break
            # September 27: a trash can ahead-left stopped "come to me" with the right side open.
            # October 3: a table across the whole front, open floor beside it. Go around on a
            # leg the camera or the sensors choose, then turn back to where the person is.
            telemetry, _ = self.link.snapshot()
            heading = self._heading(telemetry) if telemetry else None
            person_heading = wrap(heading + last['bearing_deg']) if heading is not None else None
            detour = self._go_around('the person he is driving to', last['bearing_deg'], self.space(), FRONT_MARGIN,
                                     plan, cancel, budget_m - travelled, detours, tried)
            if detour is None:
                break
            turned += detour['turned_deg']
            travelled += detour['travelled_m']
            detours.append({k: detour[k] for k in ('side', 'travelled_m', 'status', 'by', 'note') if k in detour})
            if detour['by'] == 'sensors' and detour['status'] != 'completed':
                break
            back, _ = self._bearing_to(last.get('position'))
            if back is None:
                now_heading = self._heading(self.link.snapshot()[0] or {}) if self.link.snapshot()[0] else None
                back = wrap(person_heading - now_heading) if person_heading is not None and now_heading is not None else None
            if back is not None and abs(back) > 10:
                facing = self._turn(max(-180., min(180., back)), plan, cancel, escape=False)
                turned += facing.get('turned_deg', 0.)
            found = self._acquire(step, plan, cancel) or self._face_near(0., cancel)
            if found is None:
                result, reason = 'lost', 'person_not_refound_after_going_around'
                break
            if abs(found['bearing_deg']) > 12:
                # The turn back may have been cut short beside something: square up on the face itself.
                facing = self._turn(max(-90., min(90., found['bearing_deg'])), plan, cancel, escape=False)
                turned += facing.get('turned_deg', 0.)
                self._sleep(.8, cancel)
            if found.get('track_id') and found['track_id'] != step.target and step.target != 'nearest_person':
                # The track restarted while he moved: carry on with the face now ahead.
                step = DriveStep('approach', target=found['track_id'], stop_at_m=stop_at)
                plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose,
                                 {**plan.targets, step.target: {'kind': 'face'}}, plan.expires_at)
                kind = 'face'
        final = self._resolve(step.target, plan, self.clock(), fresh=False)
        space = self.space()
        reach = (final or last or {}).get('range_m')
        if result in {'blocked', 'timeout'} and kind in {'face', 'nearest_person'} and reach is not None and reach - stop_at <= .3:
            # Stopped by a table, knees or the clock while within arm's reach of the stop
            # distance: he is there. October 3: three such arrivals (0.9-1.2 m from the person)
            # were told as "I didn't make it to you".
            result, reason = 'completed', 'stopped within reach of the person: ' + str(reason)
        out = {**row, 'status': result, 'reason': reason, 'travelled_m': round(travelled, 2),
               'turned_deg': turned, 'stop_at_m': stop_at, **extra,
               'clearance_at_stop_m': {k: (round(v, 2) if isinstance(v, float) else v) for k, v in space.items()},
               'final_approx_range_m': round(final['range_m'], 1) if final and final.get('range_m') else None}
        if detours:
            out['detours'] = detours
        if reacquired:
            out['refound_by'] = reacquired
        return out

    def _head(self, pose, cancel, request_id):
        """A head move the drive owner makes for its own looking. It is not a person's pose:
        October 3, each such look started the 90-second "a person posed me" hold, which then
        refused Rex's own moves and the back-off reflex after every route look."""
        move = getattr(self.servos, 'drive_look', None) or self.servos.execute_pose
        return move(pose, cancel, request_id)

    def _look(self, degrees, cancel):
        """Point the head (neck) degrees left of the base (0 recentres); False without servos."""
        if self.servos is None or not hasattr(self.servos, 'execute_pose'):
            return False
        from .drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
        degrees = max(-LOOK_NECK_MAX, min(LOOK_NECK_MAX, degrees))
        receipt = self._head({0: round(NECK_CENTER - degrees*NECK_PULSES_PER_DEG)}, cancel, 'drive-look')
        self._view_since = self.clock() + HEAD_SETTLE_S   # A hosted look waits for the head to be still.
        return getattr(receipt, 'status', None) == 'pulses_reached'

    def _face_near(self, expected, cancel, wait=1.5):
        """A fresh face near the expected base-relative bearing, waiting for detections."""
        deadline = self.clock() + wait
        while True:
            faces = [f for f in self._fresh_faces(self.clock()) if f['at'] >= self._view_since]
            rows = [(abs(wrap(image_bearing(f['box'], self._neck()) - expected)), f) for f in faces]
            rows = [r for r in rows if r[0] <= 45]
            if rows:
                face = min(rows, key=lambda r: r[0])[1]
                return {'track_id': face['track_id'], 'bearing_deg': image_bearing(face['box'], self._neck())}
            if self.clock() >= deadline:
                return None
            self._sleep(.25, cancel)

    def _reacquire(self, plan, cancel, person_heading):
        """Find a person lost mid-approach: head look toward where they should be, then glances,
        then a body turn; on success face them with the body and recentre the head."""
        telemetry = self._telemetry()
        heading = self._heading(telemetry)
        expected = wrap(person_heading - heading) if person_heading is not None and heading is not None else 0.
        found, how, turned = None, None, 0.
        for degrees in (expected, expected + 30., expected - 30.):
            if abs(degrees) > LOOK_NECK_MAX + 10 or not self._look(degrees, cancel):
                continue
            found = self._face_near(expected, cancel)
            if found:
                how = 'head_look'
                break
        self._look(0., cancel)   # Recentred: bearings below are base-relative.
        if found is None and abs(expected) > 10:
            turn = self._turn(max(-180., min(180., expected)), plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            if turn['status'] == 'completed':
                self._sleep(.8, cancel)
                found = self._face_near(0., cancel)
                how = 'body_turn'
        if found is None:
            return None
        if how == 'head_look' and abs(found['bearing_deg']) > 12:
            turn = self._turn(max(-180., min(180., found['bearing_deg'])), plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            self._sleep(.8, cancel)
            refound = self._face_near(0., cancel)
            if refound is None:
                return None
            found = refound
        return {**found, 'how': how, 'turned_deg': turned}

    def _faces_ahead(self):
        return [f for f in self._fresh_faces(self.clock()) if abs(image_bearing(f['box'], self._neck())) <= 20]

    def _run_approach(self, step, plan, cancel, kind, stop_at, budget_m):
        """One closed-loop run toward a camera target; returns (result, reason, travel, last seen)."""
        self._ensure_parked(cancel)
        controller = Approach(self.clock(), 'face' if kind in {'face', 'nearest_person'} else 'object',
                              stop_at, LIMITS[plan.origin]['speed_mps'] * self._pace, budget_m)
        result, reason, last = None, None, None
        if self.sounds:
            self.sounds.start('move', self._origin)
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
                if current is not None:
                    heading = self._heading(telemetry)
                    # Absolute heading of the person: firmware steering around obstacles
                    # turns the base, and this says which way to look when they drop out.
                    last = {**current, 'heading': wrap(heading + current['bearing_deg']) if heading is not None else None}
                    if last['heading'] is not None and current.get('range_m'):
                        # Where the person is in the room, so he can turn back to them after going around something.
                        last['position'] = (self._at[0] + current['range_m']*math.cos(math.radians(last['heading'])),
                                            self._at[1] + current['range_m']*math.sin(math.radians(last['heading'])))
                space = self.space(now)
                lin, ang, result, reason = controller.step(now, telemetry, space, current)
                lin = min(lin, max(.05, controller.speed * self._caution(space)))
                if current is not None:
                    # Near the target the firmware must slow him, not steer him off it.
                    self._assist(not self._near_target(space, current['bearing_deg'], current.get('range_m'), stop_at))
                if result is None:
                    self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                    self.wait(.15)
        finally:
            self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
            self.link.send({'cmd': 'stop'})
            self._assist(True)
            if self.sounds:
                self.sounds.stop()
        return result, reason, controller.travel, last

    def _detour(self, side, plan, cancel, budget_m):
        """Turn toward the open side (30°, 55°, then 85° while it is still in the path) and roll past."""
        turned = 0.
        for degrees in (DETOUR_DEG, DETOUR_MORE_DEG, DETOUR_DEG):
            turn = self._turn(side * degrees, plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                return {'side': 'left' if side > 0 else 'right', 'status': 'blocked', 'travelled_m': 0., 'turned_deg': turned}
            if move_allowance(DETOUR_M, self.space())[0] >= .15:
                break
        move = self._move(min(DETOUR_M, budget_m), plan, cancel)
        return {'side': 'left' if side > 0 else 'right', 'status': move['status'],
                'travelled_m': move.get('travelled_m', 0.), 'turned_deg': turned}

    def _route_look(self, cancel):
        """A fresh sharp picture with the head tipped slightly down, so the floor and what
        stands on it are in view; the head returns to its level drive stance afterwards."""
        tipped = False
        owner = self.servos
        if owner is not None and hasattr(owner, 'execute_pose'):
            from .servo_poses import DRIVE_HEAD, NECK_CENTER, TILT_DOWN_PER_DEG
            level = dict(DRIVE_HEAD)
            down = {0: NECK_CENTER, 2: round(level[2] + ROUTE_LOOK_DOWN_DEG * TILT_DOWN_PER_DEG)}
            tipped = getattr(self._head(down, cancel, 'drive-route-look'), 'status', None) == 'pulses_reached'
            self._view_since = self.clock() + HEAD_SETTLE_S
        self._sleep(HEAD_SETTLE_S if tipped else .5, cancel)
        try:
            return self.look(max(self._view_since, self.clock() - .5))
        finally:
            if tipped:
                self._head({2: level[2]}, cancel, 'drive-route-look')
                self._view_since = self.clock() + .3

    def _route_leg(self, goal, goal_bearing, plan, cancel, budget_m, tried, in_view=True):
        """One leg around what blocks the way, chosen from a picture of the floor and the
        range sensors by direction. None when no look is possible or it shows no way.

        The hosted look only proposes a direction and a rough distance. The turn still
        passes the arm-swing check and the move the same clearance, wall and firmware
        guards as any other, so a wrong proposal ends as a short or refused leg."""
        if (self.router is None or self.look is None or budget_m < .2
                or self._vision_calls >= self._vision_budget()):
            return None
        image = self._route_look(cancel)
        if image is None:
            return None
        from .drive_space import HALF_FOV, open_directions
        now = self.clock()
        telemetry, matrix = self.link.snapshot()
        space = self.space(now)
        facts = {'goal': goal, 'goal_in_view': bool(in_view),
                 'goal_bearing_deg': round(goal_bearing) if goal_bearing is not None else None,
                 'goal_image_x': (round(.5 - goal_bearing/(2*HALF_FOV), 2)
                                  if goal_bearing is not None and abs(goal_bearing) < HALF_FOV else None),
                 'blocked_ahead_m': space.get('path_m'), 'robot_width_m': .55,
                 'open_by_direction': open_directions(telemetry, matrix, now),
                 'already_tried': tried[-3:]}
        self._vision_calls += 1
        try:
            choice = self.router(image, facts, cancel)
        except ProviderFailure as exc:
            tried.append('the look failed: ' + str(exc))
            return None
        if self.trace:
            self.trace.emit('drive_route_look', goal=goal, facts=facts, choice=choice)
        if not choice.get('way') or choice.get('confidence') == 'low':
            tried.append('no way seen: ' + str(choice.get('note') or '')[:80])
            return None
        beyond = choice.get('beyond_view')
        if beyond in ('left', 'right'):
            bearing, distance = (ROUTE_BEYOND_VIEW_DEG if beyond == 'left' else -ROUTE_BEYOND_VIEW_DEG), ROUTE_LEG_M
        else:
            bearing = (.5 - float(choice['x'])) * 2 * HALF_FOV
            distance = max(.3, min(ROUTE_LEG_M, float(choice.get('distance_m') or ROUTE_LEG_M)))
        said = f"{abs(bearing):.0f} degrees {'left' if bearing > 0 else 'right'}, {distance:.1f} m"
        heading = self._heading(telemetry) if telemetry else None
        way = wrap(heading + bearing) if heading is not None else None
        self._track(telemetry)
        if way is not None and any(math.hypot(self._at[0] - x, self._at[1] - y) < .3 and abs(wrap(way - failed)) < 20.
                                   for x, y, failed in self._dead_ends):
            # October 3 evening: sent right three times from one spot after that way had
            # already been refused. The same way from the same place is not tried again.
            tried.append(said + ': already failed from here; not repeated')
            return None
        row = {'side': 'left' if bearing > 0 else 'right', 'by': 'camera', 'note': str(choice.get('note') or '')[:120],
               'travelled_m': 0., 'turned_deg': 0.}
        if abs(bearing) > 8:
            turn = self._turn(bearing, plan, cancel, escape=False)
            row['turned_deg'] = turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                tried.append(said + ': could not turn that way (' + str(turn.get('reason')) + ')')
                if way is not None:
                    self._dead_ends.append((*self._at, way))
                return {**row, 'status': 'blocked'}
        # No straightening here: the look chose this direction, and turning away from the
        # wall beside it only undid the turn (three 12 degree turns back, then no move).
        move = self._move(min(distance, budget_m), plan, cancel, straighten=False)
        row['travelled_m'] = move.get('travelled_m', 0.)
        row['turned_deg'] += move.get('turned_deg', 0.)
        if row['travelled_m'] < .25 and way is not None:
            self._dead_ends.append((self._at[0] - row['travelled_m']*math.cos(math.radians(way)),
                                    self._at[1] - row['travelled_m']*math.sin(math.radians(way)), way))
        tried.append(said + f": rolled {row['travelled_m']:.1f} m, {move['status']}"
                     + (' (' + str(move['reason']) + ')' if move.get('reason') else ''))
        return {**row, 'status': move['status']}

    def _go_around(self, goal, bearing, space, stand_off, plan, cancel, budget_m, detours, tried, in_view=True):
        """The next leg around an obstacle: chosen by the camera when it can be, otherwise
        from the range sensors alone. None when neither shows a way or the tries are spent."""
        if sum(d.get('by') == 'camera' for d in detours) < ROUTE_LEGS:
            leg = self._route_leg(goal, bearing, plan, cancel, budget_m, tried, in_view)
            if leg is not None:
                return leg
        if sum(d.get('by') != 'camera' for d in detours) >= MAX_DETOURS:
            return None
        side = self._open_side(space, stand_off, bearing if bearing is not None else 0.)
        if side is None:
            return None
        return {**self._detour(side, plan, cancel, budget_m), 'by': 'sensors'}

    def _toward_voice(self, plan, cancel, distance_m):
        """Roll straight on the current heading toward a voice until a face comes into view,
        the path closes, or distance_m; returns (travelled, outcome)."""
        self._ensure_parked(cancel)
        start = self._telemetry().get('odom') or {}
        hold = self._heading(self._telemetry())
        speed = LIMITS[plan.origin]['speed_mps'] * self._pace
        outcome, travelled = 'distance', 0.
        if self.sounds:
            self.sounds.start('move', self._origin)
        try:
            while True:
                self._check(cancel)
                now = self.clock()
                telemetry = self._telemetry()
                ok, _ = self.availability(now)
                if not ok:
                    outcome = 'stopped'
                    break
                odom = telemetry.get('odom') or {}
                travelled = math.hypot(float(odom.get('x', 0)) - float(start.get('x', 0)),
                                       float(odom.get('y', 0)) - float(start.get('y', 0)))
                if travelled >= distance_m - .05:
                    break
                self._view_since = now - 1.6
                if self._faces_ahead():
                    outcome = 'face'
                    break
                room = (self.space(now).get('path_m') or 0.) - FRONT_MARGIN
                speed_now = max(0., float(odom.get('lin', 0)))
                if room <= max(.03, speed_now*speed_now/(2*CONFIG['accel_lin']) + speed_now*.3):
                    outcome = 'blocked'
                    break
                heading = self._heading(telemetry)
                ang = 0. if hold is None or heading is None else max(-.2, min(.2, math.radians(wrap(hold - heading))*1.2))
                lin = max(.05, min(speed * self._caution(self.space(now)), math.sqrt(2*CONFIG['accel_lin']*max(0., room)),
                                   (distance_m - travelled)/.6))
                self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                self.wait(.15)
        finally:
            self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
            self.link.send({'cmd': 'stop'})
            if self.sounds:
                self.sounds.stop()
        return travelled, outcome

    def _locate(self, description, cancel, sought=None, settle=True):
        """One fresh sharp look plus one hosted locate; bearing is base-relative.

        With a remembered sighting, the locator is told which match it chose and where it
        should now be, and a match far from there is a different one (September 27: three
        looks for "door" chose three different doors and the base zig-zagged between them)."""
        if self._vision_calls >= self._vision_budget():
            raise ProviderFailure('PlanVisionBudgetReached')
        if settle:
            self._sleep(.4, cancel)   # Let the head camera settle after motion.
        image = self.look(max(self._view_since, self.clock() - .5))
        if image is None:
            return None, 'no_fresh_camera_frame'
        from .drive_space import HALF_FOV
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        expected = None
        if sought and sought is self._sought:
            expected = self._expected_sought()
        elif sought and sought.get('heading') is not None and heading is not None:
            expected = wrap(sought['heading'] - heading)
        self._vision_calls += 1
        if sought and sought.get('note'):
            hint = 'Earlier you matched it as: ' + sought['note'] + '.'
            if expected is not None and abs(expected - self._neck()) < HALF_FOV:
                hint += f' It should now be near x={.5 - (expected - self._neck())/(2*HALF_FOV):.2f}; if several match, report the one nearest there.'
            result = self.locator(description, image, cancel, hint=hint)
        else:
            result = self.locator(description, image, cancel)
        if not result.get('found') or result.get('confidence') == 'low':
            return None, result.get('note') or 'not_found'
        bearing = (.5 - float(result['x'])) * 2 * HALF_FOV + self._neck()
        if expected is not None and abs(wrap(bearing - expected)) > (sought or {}).get('slack', 25.):
            return None, 'a_different_match_off_to_the_side'
        self._track()
        return {'bearing_deg': bearing, 'box_width': float(result['width']), 'note': result.get('note'),
                'heading': wrap(heading + bearing) if heading is not None else None,
                'from': tuple(self._at)}, None

    def _find_again(self, plan, cancel, shifted=0):
        """Look for the remembered thing: where it should be first, then to either side,
        with the head before the body. Returns (found, why not, degrees the base turned).

        October 3: after each leg he swung his whole body back to a predicted bearing,
        looked twice at that one spot, and gave up; the predictions were off and the turns
        overshot. The head covers 40 degrees either side at once, so the body turns only
        when the thing lies beyond that, and once more, directly, when it has been found."""
        description = self._sought['description']
        expected, turned = self._expected_sought(), 0.
        can_look = self.servos is not None and hasattr(self.servos, 'execute_pose')
        if expected is not None and abs(expected) > (LOOK_NECK_MAX + 25. if can_look else 35.):
            # Never back up to make room here (September 27: a hallway corner beside the
            # arms turned every re-aim into a reverse, and he looped).
            turn = self._turn(max(-120., min(120., expected)), plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            expected = self._expected_sought()
        if not can_look:
            # No head to look around with: a second look from the same spot, as before.
            found, why = self._locate(description, cancel, self._sought)
            if found is None and self._vision_calls < self._vision_budget():
                found, why = self._locate(description, cancel, self._sought)
            return found, why, turned
        centre = max(-LOOK_NECK_MAX, min(LOOK_NECK_MAX, expected or 0.))
        first = -shifted or (-1 if centre > 0 else 1)   # After moving left, look right of the old heading first.
        angles = []
        for angle in (centre, centre + first*LOOK_NECK_MAX, centre - first*LOOK_NECK_MAX):
            angle = max(-LOOK_NECK_MAX, min(LOOK_NECK_MAX, angle))
            if all(abs(angle - seen) >= 15. for seen in angles):
                angles.append(angle)
        found, why, moved = None, 'not_found', False
        for index, angle in enumerate(angles):
            if self._vision_calls >= self._vision_budget():
                break
            if abs(angle - self._neck()) > 2.:
                moved = self._look(angle, cancel) or moved
            if index or shifted:
                self._sought['slack'] = 90.   # Anywhere in this view: the note says which one it is.
            found, why = self._locate(description, cancel, self._sought)
            if found is not None:
                break
        if moved:
            self._look(0., cancel)   # Level and centred again: bearings below are base-relative.
        return found, why, turned

    def _expected_sought(self):
        """Base-relative bearing where the remembered thing should be now.

        Once two sightings from different places have fixed its position, the bearing is
        to that position from where he stands; until then it is the heading he last saw it
        at, which is right only while he has not moved far sideways."""
        if not self._sought:
            return None
        bearing, _ = self._bearing_to(self._sought.get('position'))
        if bearing is not None:
            return bearing
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        if self._sought.get('heading') is None or heading is None:
            return None
        return wrap(self._sought['heading'] - heading)

    def _remember_sought(self, description, found):
        """The thing's room heading survives this plan, so "keep going" finds the same one.

        Sightings from different places can also fix where it is, but only when at least
        three of them agree: October 3, a position taken from two sight lines a metre apart
        sent him turning the wrong way after every leg (657 degrees in one trip)."""
        previous = self._sought if self._sought and self._sought.get('description') == description else {}
        sightings = list(previous.get('sightings', ()))
        if found.get('heading') is not None and found.get('from') is not None:
            sightings = (sightings + [(*found['from'], found['heading'])])[-6:]
        self._sought = {'description': description, 'note': found.get('note'),
                        'heading': found.get('heading'), 'box_width': found['box_width'],
                        'first_width': previous.get('first_width') or found['box_width'],
                        'sightings': sightings, 'position': self._fixed_position(sightings)}

    @staticmethod
    def _fixed_position(sightings):
        """The point nearest all sight lines, when three or more lines from spread-out
        places pass within 8 degrees of it; otherwise None (a direction is all he has)."""
        if len(sightings) < 3:
            return None
        spread = max(abs(wrap(a[2] - b[2])) for a in sightings for b in sightings)
        if spread < 15.:
            return None
        # Least squares over the lines' normals: sum((p - p_i) . n_i)^2 is smallest at p.
        a11 = a12 = a22 = b1 = b2 = 0.
        for x, y, heading in sightings:
            nx, ny = -math.sin(math.radians(heading)), math.cos(math.radians(heading))
            a11 += nx*nx; a12 += nx*ny; a22 += ny*ny
            along = nx*x + ny*y
            b1 += nx*along; b2 += ny*along
        det = a11*a22 - a12*a12
        if abs(det) < 1e-6:
            return None
        point = ((b1*a22 - b2*a12)/det, (a11*b2 - a12*b1)/det)
        for x, y, heading in sightings:
            dx, dy = point[0] - x, point[1] - y
            if math.hypot(dx, dy) < .5 or abs(wrap(math.degrees(math.atan2(dy, dx)) - heading)) > 8.:
                return None
        return point

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
                self._remember_sought(step.description, found)
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

    def _room(self, stand_off):
        space = self.space()
        front = space.get('path_m')
        return space, (front if front is not None else 0.) - stand_off

    @staticmethod
    def _open_side(space, stand_off, bearing):
        """+1 left / -1 right: a front band with a way around, preferring the target's side."""
        room = {1: space.get('front_left_m'), -1: space.get('front_right_m')}
        open_sides = [side for side, value in room.items() if value is not None and value - stand_off >= SIDE_OPEN_M]
        if len(open_sides) == 2:
            if abs(bearing) > 2:
                return 1 if bearing > 0 else -1
            return max(open_sides, key=lambda side: room[side])
        if open_sides:
            return open_sides[0]
        # Nothing open in front (a table across the whole view): the ring's side sensors may
        # still show floor to one side. October 3: 0.3 m ahead, two metres open on the right.
        beside = {1: space.get('left_m'), -1: space.get('right_m')}
        wide = [side for side, value in beside.items() if value is not None and value >= SIDE_WAY_M]
        return max(wide, key=lambda side: beside[side]) if wide else None

    def _cruise(self, plan, cancel, stand_off, budget_m):
        """Roll toward the sought thing while the next locate runs (owner, September 27: one
        continuous motion instead of drive-stop-look legs). Steering follows the remembered
        absolute heading, which each locate refreshes. Returns (travelled, outcome, found):
        found is the locate that was in flight when the wheels stopped, if it finished."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
        description = self._sought['description']
        self._ensure_parked(cancel)
        start = self._telemetry().get('odom') or {}
        speed = LIMITS[plan.origin]['speed_mps'] * self._pace
        pool = ThreadPoolExecutor(1, thread_name_prefix='r3x-cruise-locate')
        future, latest, misses, outcome, travelled = None, None, 0, None, 0.
        looked_at = float('-inf')
        if self.sounds:
            self.sounds.start('move', plan.origin)
        try:
            while outcome is None:
                self._check(cancel)
                now = self.clock()
                telemetry = self._telemetry()
                ok, why = self.availability(now)
                if not ok:
                    outcome = ('stopped', why)
                    break
                if future is None and self._vision_calls < self._vision_budget() and now - looked_at >= CRUISE_LOOK_S:
                    future, looked_at = pool.submit(self._locate, description, cancel, self._sought, False), now
                if future is not None and future.done():
                    found, why = future.result()
                    future = None
                    if found is None:
                        misses += 1
                        if misses >= 2:
                            outcome = ('lost', why)
                            break
                    else:
                        misses, latest = 0, found
                        self._remember_sought(description, found)
                        if found['box_width'] >= FILLS_VIEW:
                            outcome = ('arrived', 'target_fills_the_view')
                            break
                odom = telemetry.get('odom') or {}
                travelled = math.hypot(float(odom.get('x', 0)) - float(start.get('x', 0)),
                                       float(odom.get('y', 0)) - float(start.get('y', 0)))
                if budget_m - travelled <= .05:
                    outcome = ('distance_limit', 'distance_limit_reached')
                    break
                space = self.space(now)
                room = (space.get('path_m') if space.get('path_m') is not None else 0.) - stand_off
                speed_now = max(0., float(odom.get('lin', 0)))
                brake = speed_now*speed_now/(2*CONFIG['accel_lin']) + speed_now*.3
                firmware_blocked = telemetry.get('state') == 'blocked' and telemetry.get('blocked_dir') in ('front', 'both')
                if firmware_blocked or room <= max(.03, brake):
                    outcome = ('blocked', 'front_stand_off')
                    break
                expected = self._expected_sought()
                if expected is not None and abs(expected) > 40:
                    outcome = ('off_course', 'target_outside_steering_range')
                    break
                self._assist(not self._near_target(space, expected))
                ang = 0. if expected is None else max(-.2, min(.2, math.radians(expected)*1.2))
                lin = max(.05, min(speed * self._caution(space), math.sqrt(2*CONFIG['accel_lin']*max(0., room)),
                                   (budget_m - travelled)/.6))
                self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                self.wait(.15)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)   # A stop never waits on a look in flight.
            raise
        finally:
            try:
                self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
                self.link.send({'cmd': 'stop'})
                self._assist(True)
            finally:
                if self.sounds:
                    self.sounds.stop()
        carried = None
        if future is not None:
            try:
                found, _ = future.result(timeout=8.)   # Reuse the look already paid for.
                if found is not None:
                    self._remember_sought(description, found)
                    carried = found
            except (FutureTimeout, Cancelled, ProviderFailure):
                pass
        pool.shutdown(wait=False, cancel_futures=True)
        self._view_since = self.clock() + .3
        end = (self.link.snapshot()[0] or {}).get('odom') or {}
        travelled = math.hypot(float(end.get('x', 0)) - float(start.get('x', 0)),
                               float(end.get('y', 0)) - float(start.get('y', 0))) if end else travelled
        return travelled, outcome, carried or (latest if outcome[0] == 'arrived' else None)

    def _approach_sought(self, step, plan, cancel, budget_m):
        """Vision-guided legs toward what seek found: re-locate, re-aim, move, repeat.

        An obstacle is not arrival: while the thing still looks far away, a blocked leg first
        re-aims precisely (pointed slightly into a wall beside a door), then goes around on
        an open front band, and otherwise reports blocked."""
        row = {'action': 'approach', 'target': 'sought'}
        if step.description and (self._sought is None or self._sought['description'] != step.description):
            self._sought = {'description': step.description}
        if self._sought is None:
            return {**row, 'status': 'refused', 'reason': 'nothing_sought_yet', 'travelled_m': 0.}
        stand_off = max(FRONT_MARGIN, step.stop_at_m or DEFAULT_STOP_AT['object'])
        travelled, turned, misses, detours, swing_refusals, carried, cruises = 0., 0., 0, [], 0, None, 0
        tried = []   # What each leg around something did, in words, for the next look.
        shifted = 0  # +1 after a leg off to the left (the thing has slid right of its old heading), -1 after one to the right.
        def went(detour):
            nonlocal travelled, turned, shifted
            shifted = 1 if detour['side'] == 'left' else -1
            turned += detour['turned_deg']
            travelled += detour['travelled_m']
            detours.append({k: detour[k] for k in ('side', 'travelled_m', 'status', 'by', 'note') if k in detour})
            if detour.get('by') == 'camera' and self._sought.get('position') is None:
                # A sighting gives a direction, not a place: after moving aside the thing may
                # sit well off the old heading until enough sightings fix where it is.
                self._sought['slack'] = ROUTE_SLACK_DEG
        def result(status, reason, **extra):
            if status == 'completed':
                # Arrived: the search is over. October 3: a later, unrelated "come towards me"
                # was steered back to the microwave he had already reached.
                self._sought = None
            out = {**row, 'status': status, 'reason': reason, 'travelled_m': round(travelled, 2),
                   'turned_deg': round(turned, 1), **extra}
            if swing_refusals:
                out['aim_turns_refused_for_arm_clearance'] = swing_refusals
            out['continuous_runs'] = cruises
            if detours:
                out['detours'] = detours
            return out
        while True:
            if self._vision_calls >= self._vision_budget():
                return result('look_limit', 'vision_look_limit_reached')
            if carried is not None:
                found, why, carried = carried, None, None
            else:
                found, why, swung = self._find_again(plan, cancel, shifted)
                turned += swung
            if found is None:
                # Out of sight even after looking to both sides (a corner, a cabinet): one
                # more leg toward where it should be, then he looks for it again.
                onward = (self._route_leg(self._sought['description'], self._expected_sought(), plan, cancel,
                                          budget_m - travelled, tried, in_view=False)
                          if any(d.get('by') == 'camera' for d in detours)
                          and sum(d.get('by') == 'camera' for d in detours) < ROUTE_LEGS else None)
                if onward is None or not onward['travelled_m']:
                    return result('lost', why)
                went(onward)
                continue
            shifted = 0
            self._remember_sought(self._sought['description'], found)
            if found['box_width'] >= FILLS_VIEW:
                return result('completed', 'target_fills_the_view')
            if budget_m - travelled < .1:
                return result('distance_limit', 'distance_limit_reached')
            bearing = found['bearing_deg']
            space, room = self._room(stand_off)
            if abs(bearing) > AIM_DEG or (room < .1 and abs(bearing) > 3):
                turn = self._turn(max(-90., min(90., bearing)), plan, cancel, escape=False)
                turned += turn.get('turned_deg', 0.)
                if turn['status'] == 'completed':
                    bearing = 0.
                elif turn['status'] != 'refused':
                    return result(turn['status'], turn['reason'])
                else:
                    swing_refusals += 1   # Arms near something: keep going forward and aim once past it.
                space, room = self._room(stand_off)
            if min(budget_m - travelled, room) >= .2:   # Braking stops ~0.1 m early; no creeping restarts.
                moved, outcome, carried = self._cruise(plan, cancel, stand_off, budget_m - travelled)
                travelled += moved
                cruises += 1
                if outcome[0] == 'arrived':
                    return result('completed', outcome[1])
                if outcome[0] == 'stopped':
                    return result('stopped', outcome[1])
                continue   # Blocked, off course, lost or budget: re-evaluate from the latest look.
            clearance = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in space.items()}
            ahead = space.get('front_center_m')
            wide = found['box_width'] >= NEAR_WIDTH and abs(bearing) <= 8
            grown = found['box_width'] >= GROWN_NEAR * (self._sought.get('first_width') or found['box_width'])
            if wide and (grown or (ahead is not None and ahead - stand_off < NEAR_AHEAD_M)):
                # Blocked with the thing ahead and near: arrived. Near means either something
                # straight ahead at the stand-off, or the thing itself has at least doubled in
                # the picture since first seen (it sits above or behind what stops him). A
                # side return beside a thing that only looks big from afar is not arrival.
                return result('completed', 'reached_stand_off_at_target', clearance_at_stop_m=clearance,
                              grew_in_view=round(found['box_width'] / (self._sought.get('first_width') or found['box_width']), 1))
            detour = self._go_around(self._sought['description'], bearing, space, stand_off, plan, cancel,
                                     budget_m - travelled, detours, tried)
            if detour is None:
                return result('blocked', 'still_blocked_after_detours' if detours else 'obstacle_before_target',
                              clearance_at_stop_m=clearance, target_still_far=True)
            went(detour)
            if (detour['by'] == 'sensors' and detour['status'] == 'blocked' and not detour['travelled_m']
                    and detour['turned_deg'] == 0.):
                return result('blocked', 'detour_turn_refused', clearance_at_stop_m=clearance, target_still_far=True)
            if detour['status'] not in {'completed', 'blocked', 'refused'}:
                return result(detour['status'], 'detour_' + detour['status'])

    def _find_person(self, step, plan, cancel):
        row = {'action': 'find_person'}
        if self._fresh_faces(self.clock()):
            return {**row, 'status': 'completed', 'reason': 'person_already_visible', 'turned_deg': 0.}
        candidates = [b['bearing_deg'] for b in ((self.radar.bodies() if self.radar else None) or [])]
        # Whoever just spoke is the likeliest person to find: look there before radar bodies.
        voice = self._voice_bearing()
        if voice is None and plan.targets.get('speaker'):
            voice = self._predicted(plan.targets['speaker'])   # Offered at plan time; he may have turned since.
        if voice is not None:
            candidates.insert(0, voice)
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
        self._parked = False
        current = step_odom = None
        try:
            self._halt.clear()
            ok, reason = self.availability()
            if plan.origin=='self_initiated' and self._pose_held():
                ok, reason = False, 'human_pose_held'
            if not ok or self.clock() >= plan.expires_at:
                receipt.status, receipt.reason = 'refused', reason or 'StaleDrivePlan'
                return receipt
            self.moving.set()
            self._notify(plan)
            self._vision_calls = 0   # The last sought thing persists across plans ("keep going").
            self._dead_ends = []
            start = self.link.snapshot()[0]
            self._start_heading = self._heading(start) if start else None
            self._plan_parks = plan.park_arm   # The arm parks right before the first wheel command.
            self._origin = plan.origin
            self._view_since = self.clock()
            budget = LIMITS[plan.origin]['total_m']
            for step in plan.steps:
                self._check(cancel)
                current, step_odom = step, (self.link.snapshot()[0] or {}).get('odom') or {}
                self._pace = PACE.get(step.pace, 1.)
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
                if self.sounds and step.action == 'approach' and row['status'] == 'completed' and row.get('travelled_m'):
                    self.sounds.accent('arrived', plan.origin)
                elif self.sounds and row['status'] == 'blocked':
                    self.sounds.accent('blocked', plan.origin)
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
            if current is not None:
                # The interrupted step still moved: report its odometry, not zero.
                end = (self.link.snapshot()[0] or {}).get('odom') or {}
                moved = math.hypot(float(end.get('x', 0)) - float(step_odom.get('x', 0)),
                                   float(end.get('y', 0)) - float(step_odom.get('y', 0))) if end and step_odom else 0.
                receipt.steps.append({'action': current.action, 'status': 'cancelled', 'reason': 'stop_requested',
                                      'travelled_m': round(moved, 2)})
                receipt.travelled_m = round(receipt.travelled_m + moved, 2)
        except ArmParkFailed as exc:
            receipt.status, receipt.reason = 'refused', str(exc)   # Nothing moved: the park precedes wheels.
        except ProviderFailure as exc:
            receipt.status, receipt.reason = 'failed', str(exc)
        finally:
            try:
                if not self.link.failure and self.link.port is not None:
                    self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            if self.sounds:
                self.sounds.stop(now=True)
            self._assist(True)   # Never left off after a plan, however it ended.
            # He moved, so what the front sensors saw before is no baseline: October 3, ending
            # 11 cm from a kitchen island looked like "someone came very close" and he backed
            # away on his own, through his spoken report, which then failed to finish.
            self.flinch.baseline.clear()
            self.flinch.clear.clear()
            self.flinch.ticks = 0
            if self._parked:
                self._parked = False
                # A picture is wanted after this drive: the head stays still for it first.
                self.servos.finish_base_motion(look_pending=bool(plan.look_after))
            self.motion_until = self.clock() + 1.
            self.moving.clear()
            self._notify(plan)
            receipt.finished_at = self.clock()
            self.history.append({'request_id': plan.request_id, 'origin': plan.origin, 'purpose': plan.purpose,
                                 'steps_run': [f"{r['action']}:{r['status']}" for r in receipt.steps],
                                 'status': receipt.status, 'reason': receipt.reason,
                                 'travelled_m': receipt.travelled_m, 'turned_deg': receipt.turned_deg,
                                 'finished_at': receipt.finished_at})
            if self.trace:
                self.trace.emit('drive_receipt', **asdict(receipt), origin=plan.origin, purpose=plan.purpose)
            self._lock.release()
        return receipt

    def base_status(self, now=None):
        """Live wheel-controller readings for honest answers (battery, charging, climate).

        Values the firmware marks unknown (-1, ok false) stay None; nothing is estimated."""
        now = self.clock() if now is None else now
        telemetry, _ = self.link.snapshot()
        if not telemetry:
            return {'connected': self.state == 'ready', 'readings': None}
        known = lambda v, low=0: v if isinstance(v, (int, float)) and v >= low else None
        mv, ma, soc = known(telemetry.get('batt_mv')), telemetry.get('batt_ma'), known(telemetry.get('batt_soc'))
        imu, env = telemetry.get('imu') or {}, telemetry.get('env') or {}
        celsius = env.get('t') if env.get('ok') else None
        return {
            'connected': self.state == 'ready', 'reading_age_s': round(max(0., now - telemetry['received_at']), 1),
            'battery': {'percent': soc, 'volts': round(mv/1000, 2) if mv else None,
                        # Positive draws from the pack; negative flows into it (V2 menu-bar convention).
                        'amps_drawn': round(ma/1000, 2) if isinstance(ma, (int, float)) and ma else None,
                        'on_charger': bool(telemetry.get('charging')),
                        'percent_meaning': 'coulomb-counted estimate from the controller; null means not yet known'},
            'controller': {'state': telemetry.get('state'), 'owner': telemetry.get('owner'), 'fault': telemetry.get('fault'),
                           'error_count': telemetry.get('errs'), 'gamepad_connected': (telemetry.get('gp') or {}).get('connected')},
            'tilt_deg': {'pitch': imu.get('pitch'), 'roll': imu.get('roll')} if imu.get('ok') else None,
            'room': {'temp_c': celsius, 'temp_f': round(celsius*9/5 + 32, 1) if celsius is not None else None,
                     'pressure_hpa': env.get('hpa'), 'humidity_pct': env.get('rh')} if env.get('ok') else None,
        }

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
