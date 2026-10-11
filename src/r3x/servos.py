"""One explicitly started motion owner, with versioned requests and pulse receipts.

RobotServos supplies this owner only after explicit live handoff. The supplied port
must have exclusive ownership and bounded I/O. No automatic reconnect or replay.
"""
from dataclasses import dataclass
import math
import random
import threading
import time
from typing import Callable

from .adapters.maestro import Maestro, Port
from .contracts import Cancellation, Cancelled, ProviderFailure
from .providers import Capability
from .servo_poses import (LIMITS, THROTTLE, HEAD, PAIRS, POKER, lifecycle_lanes, motion_profiles, base_lanes,
                          talking_lanes, idle_lanes, throttle_clear, validate_pose, pose_lanes, reflex_lanes, NAMED_POSES,
                          UNPOWERED_REST, expressive_lanes, IdlePhrase, ARM_HOLD, GESTURES, REACHING_ARMS)

BASE_HELD = frozenset({*HEAD, 4, 6, *POKER, *THROTTLE})   # Neck, head, hero elbow, poker arm/elbow/wrist, throttle.
ARM_RETURN = frozenset({4, 6, *POKER, *THROTTLE})   # The arms the drive stance moved: walked back after the wheels stop.
HOLD_YIELD_SECONDS = 90.   # A human-requested pose blocks Rex's own movement this long.


@dataclass(frozen=True)
class ServoRequest:
    request_id: str
    version: int
    kind: str
    expires_at: float
    first_look: str = 'left'
    pose: str | dict[int, int] | None = None
    target: str = 'unfocused'
    primitive: str = 'tilt'
    intensity: str = 'subtle'
    hold_seconds: float = .5
    idle: IdlePhrase | None = None  # Idle: which parts move in this phrase and where the gaze points
    hold_pose: bool = True        # Human poses persist; agency poses never acquire a hold.


@dataclass(frozen=True)
class ServoReceipt:
    request_id: str
    status: str
    version: int
    at: float
    medium: str
    completed_lanes: tuple[int, ...] = ()
    reason: str | None = None
    evidence: str = 'controller_output_pulses_only'
    changed_channels: tuple[int, ...] = ()


class ServoOwner:
    capability = Capability('servos', 'maestro-injected', 'bounded lifecycle and talking motion',
                            resources=('exclusive injected Maestro transport',),
                            shutdown='cancel; explicit park request; close (never implicit motion)')

    def __init__(self, open_port: Callable[[], Port], *, medium: str,
                 clock=time.monotonic, wait=None, rng=None):
        if medium not in {'simulation', 'controller'}:
            raise ValueError('InvalidServoMedium')
        self.open_port, self.medium, self.clock = open_port, medium, clock
        self.rng = rng or random.Random()
        self._cancel = threading.Event()
        self.wait = wait or self._cancel.wait
        self._lock = threading.Lock()
        self._bus: Maestro | None = None
        self.state = 'new'
        self.version = 0
        self.held_channels = set()
        self.pose_held_at = float('-inf')  # When a human-requested pose hold was last set.
        self.hold_until_released = False   # "Leave your hand there until I say": no 90-second yield.
        self._base_held = False
        self._arm_returning = False        # The throttle arm is walking back to rest after a drive.
        # Arms left at their drive stance after a drive because something was within their
        # reach (October 11). Their channels stay held until the drive owner sees the space
        # clear and returns them, or the next drive or shutdown takes them.
        self.kept_arms: frozenset[str] = frozenset()
        self._arm_cancel = threading.Event()
        self._arm_wait = wait or self._arm_cancel.wait
        self._talking_head_origin: dict[int, int] | None = None
        self._motion = threading.Event()
        self.motion_until = 0.
        self.microphone_hold = (0., 0.)
        self.on_motion = lambda: None
        self.on_camera_motion = lambda moving, request_id, kind: None
        self.camera_moving = False
        self.commanded: dict[int, int] = {}   # Last pulses sent or read back; camera bearings use the neck.
        self.talking_neck: int | None = None  # Gaze anchor for the current reply's talking lanes.
        self.talking_pitch: float | None = None  # Head pitch (-1 down .. 1 up) the talking sway centres on this reply.
        self.gaze_pitch: float | None = None     # Pitch the idle gaze last chose (a face, a held look); None is level.
        self.microphone_moved_until = 0.       # Channel 7 carries the microphone array.
        self.microphone_still_since = float('-inf')  # When the array arm last stopped moving.
        self.head_moved_at = float('-inf')     # When the head last finished a view-changing move.
        self.energized_channels: tuple[int, ...] = ()  # Channels that were off at start.

    def start(self) -> None:
        with self._lock:
            if self.state != 'new':
                raise ProviderFailure('ServoStartAlreadyAttempted')
            self.state = 'starting'
            try:
                self._bus = Maestro(self.open_port())
                readback = self._energize_unpowered(self._bus.positions(LIMITS))
                self.commanded = dict(readback)
                self.state = 'ready'
            except Exception:
                self.state = 'faulted'
                if self._bus:
                    self._bus.close()
                raise

    def _energize_unpowered(self, readback: dict[int, int]) -> dict[int, int]:
        """Hold channels that read 0 (off since power-up) where they hang unpowered.

        After the robot is switched off every servo is limp at its unpowered rest
        pose, so the first pulse each gets is that pose ("stay where you are"), as
        V2 did. Live readings are kept; a live out-of-range reading still rejects
        before anything is written.
        """
        self.energized_channels = tuple(sorted(ch for ch, value in readback.items() if value == 0))
        if not self.energized_channels:
            validate_pose(readback)
            return readback
        rest = dict(UNPOWERED_REST)
        targets = {ch: rest[ch] for ch in self.energized_channels}
        for pair in PAIRS:   # The throttle arm, the head pair and the poker elbow/wrist move together.
            if set(targets).intersection(pair):
                targets.update({ch: readback[ch] or rest[ch] for ch in pair})
        pose = {**readback, **targets}
        validate_pose(pose)
        self._bus.profiles({ch: (10, 2) for ch in self.energized_channels})
        self._bus.targets(targets)
        # The board applies new targets on its next 20 ms servo period; until then
        # a just-energized channel still reads 0.
        for _ in range(50):
            if all(self._bus.positions(self.energized_channels).values()):
                return pose
            self.wait(.02)
        raise ProviderFailure('UnpoweredServoPulseNotApplied')

    def cancel(self) -> None:
        self._cancel.set()
        self._arm_cancel.set()
        self.release_microphone_hold()

    def microphone_moving(self) -> bool:
        return self.clock() < self.microphone_moved_until

    def neck_degrees(self) -> float:
        from .drive_space import neck_degrees
        from .servo_poses import NECK_CENTER
        return neck_degrees(self.commanded.get(0, NECK_CENTER))

    @property
    def kept_channels(self) -> set[int]:
        return {ch for arm in self.kept_arms for ch in REACHING_ARMS[arm]}

    def motion_risk(self) -> bool:
        return self._motion.is_set() or self._arm_returning or self.clock() < self.motion_until

    def human_hold_active(self):
        """A pose a person asked for blocks Rex's own movement only while it is recent.

        Review October 3: one "look right" (often chosen by the reply model itself) left a
        hold that refused every self-initiated pose and drive until someone said to release
        it. A hold now yields to his own initiative after HOLD_YIELD_SECONDS; requests from
        people and "release" work as before."""
        return bool(self.held_channels) and (self.hold_until_released or
                                             self.clock() - self.pose_held_at < HOLD_YIELD_SECONDS)

    def yield_stale_hold(self):
        """Drop an aged pose hold before Rex's own beat; a recent one stays."""
        kept = self.kept_channels
        if (self.held_channels - kept and not self.human_hold_active() and not self._motion.is_set()
                and not self._base_held and not self._arm_returning):  # Never the drive stance or a returning arm.
            self.held_channels.intersection_update(kept)   # An arm kept clear of an obstacle stays.
            return True
        return False

    def release_microphone_hold(self):
        self.microphone_hold = (0., 0.)

    def microphone_held_during(self, start, end):
        ready, expires = self.microphone_hold
        return 0 < ready <= start <= end <= expires

    def refresh_microphone_hold(self):
        """Bounded 30-second lease, centered before any qualifying robot capture.

        The hold is about the array arm (channel 7) resting at its calibrated centre.
        Other joints' idle movement does not move the array and no longer releases the
        hold (October 3: with continuous idle motion it would never have been valid);
        their noise is judged separately per capture (motion_risk). Idle excursions of
        channel 7 itself invalidate the hold; learning waits for recentering and a new
        settled hold.
        """
        if self.clock() < self.microphone_hold[1] or not self._lock.acquire(blocking=False):
            return
        try:
            if self.state != 'active' or not self._bus or 7 in self.held_channels:
                return
            center = sum(LIMITS[7]) // 2
            current = self._bus.positions((7,))[7]
            validate_pose({7:current})
            if abs(current-center) <= 2:
                now = self.clock()
                self.microphone_hold = (max(now, self.microphone_still_since+1.5), now+30.)
                return
            if self.motion_risk():
                return   # Recentre once the other joints have settled.
            self._motion.set()
            self.on_motion()
            self._bus.profiles({7:(22,6)})
            self._bus.targets({7:center})
            self.commanded[7] = center
            self.microphone_moved_until = self.clock() + 5.5
            deadline = self.clock() + 5.
            while abs(self._bus.positions((7,))[7]-center)>2:
                if self._cancel.is_set() or self.clock() >= deadline:
                    self._hold({7})
                    raise ProviderFailure('MicrophoneCenterNotCompleted')
                self.wait(.025)
            now = self.clock()
            self.motion_until = now + 1.5
            self.microphone_still_since = now
            self.microphone_hold = (now+1.5, now+30.)
        except Exception:
            self.release_microphone_hold()
            self.state = 'faulted'
            raise
        finally:
            self._motion.clear()
            self._lock.release()

    def close(self) -> None:
        self.cancel()
        if not self._lock.acquire(timeout=2.):
            raise ProviderFailure('ServoWorkerStillActive')
        try:
            if self._bus:
                self._bus.close()
            self.state = 'closed'
        finally:
            self._lock.release()

    def execute(self, request: ServoRequest, cancel: Cancellation) -> ServoReceipt:
        completed = set()
        changed = set()
        def receipt(status, reason=None):
            return ServoReceipt(request.request_id, status, self.version, self.clock(),
                                self.medium, tuple(sorted(completed)), reason,
                                changed_channels=tuple(sorted(changed)))
        if not self._lock.acquire(blocking=False):
            return receipt('rejected', 'ServoBusy')
        touched = set()
        accepted = False
        reframed = False
        try:
            if self.state not in {'ready', 'active', 'stopped'} or self._bus is None:
                return receipt('rejected', 'ServoUnavailableOrParked')
            if (request.version != self.version or not request.request_id or
                    not math.isfinite(request.expires_at) or self.clock() >= request.expires_at):
                return receipt('rejected', 'StaleServoRequest')
            if request.kind in {'talking','idle','pose','reflex','base_park','base_release','attention','gesture'} and self.state != 'active':
                return receipt('rejected', {'idle':'IdleRequiresCompletedStartup','talking':'TalkingRequiresCompletedStartup','pose':'PoseRequiresCompletedStartup'}.get(request.kind,'BaseMotionRequiresCompletedStartup'))
            if request.kind == 'startup' and self.state != 'ready':
                return receipt('rejected', 'StartupAlreadyConsumed')
            self._cancel.clear()
            cancel.check()
            current = self._bus.positions(LIMITS)
            validate_pose(current)
            lanes = (talking_lanes(current, self._talking_head_origin, self.held_channels, self.talking_neck,
                                   self.talking_pitch) if request.kind == 'talking' else
                     idle_lanes(current,self.rng,self.held_channels,request.idle) if request.kind == 'idle' else
                     expressive_lanes(current,request.kind,request.target,request.primitive,request.intensity,
                                      request.hold_seconds,self.held_channels) if request.kind in {'attention','gesture'} else
                     pose_lanes(request.pose,current,further=request.hold_pose) if request.kind == 'pose' else
                     reflex_lanes(request.pose) if request.kind == 'reflex' else
                     base_lanes(request.kind,current) if request.kind in {'base_park','base_release'} else
                     lifecycle_lanes(request.kind, current, request.first_look))
            if self._arm_returning and (request.kind in {'base_park', 'park', 'shutdown'} or any(
                    ch in ARM_RETURN for lane in lanes for beat in lane for ch, _ in beat.targets)):
                # The arms are still returning from their drive stance; callers that need
                # them (the next drive, shutdown, an arm pose) wait for the return first.
                return receipt('rejected', 'ServoBusy')
            kept = self.kept_channels
            if kept and request.kind not in {'base_park', 'park', 'shutdown'} and any(
                    ch in kept for lane in lanes for beat in lane for ch, _ in beat.targets):
                # Owner, October 11: an arm with something within its reach stays tucked.
                return receipt('rejected', 'ArmKeptClearOfObstacle:' + ','.join(sorted(self.kept_arms)))
            if request.kind == 'pose' and not request.hold_pose:
                channels = {ch for lane in lanes for beat in lane for ch, _ in beat.targets}
                if request.pose == 'release_poses' or (
                        channels.intersection(self.held_channels) and self.human_hold_active()):
                    return receipt('rejected', 'AgencyCannotOverrideHeldPose')
                self.held_channels.difference_update(channels)  # An aged hold gives way.
            self.version += 1
            if request.kind == 'pose' and request.hold_pose and request.pose not in ('release_poses', *GESTURES):  # dict poses too
                self.held_channels.update(ch for lane in lanes for beat in lane for ch, _ in beat.targets)
                self.pose_held_at = self.clock()
                if request.pose == 'hold_arms':
                    # October 3: "leave that hand there until I say you can move it" was
                    # answered by moving the hand to another pose. This holds every arm where
                    # it is, with no time limit, until release_poses.
                    self.held_channels.update(ARM_HOLD)
                    self.hold_until_released = True
            if request.kind == 'base_park':
                # Talking/idle lanes skip held channels while the base drives.
                self.held_channels.update(BASE_HELD)
                self._base_held = True
                self.kept_arms = frozenset()   # The stance holds them; the release decides again.
            accepted = True
            if any(ch == 7 for lane in lanes for beat in lane for ch, _ in beat.targets):
                self.release_microphone_hold()   # Only the array arm's own movement ends its hold.
            self._motion.set()
            self.state = 'parking' if request.kind in {'park', 'shutdown'} else 'moving'
            # Each lane owns disjoint channels. One loop owns every wire operation.
            indices = [0] * len(lanes)
            active = {}
            resting = {}   # (lane, beat) -> when a staggered beat may start
            while len(completed) < len(lanes) or active:
                cancel.check()
                if self._cancel.is_set():
                    raise Cancelled()
                now = self.clock()
                if now >= request.expires_at:
                    raise ProviderFailure('ServoRequestExpired')
                for lane_id, lane in enumerate(lanes):
                    if lane_id in completed:
                        if request.kind != 'talking' or (len(completed)==len(lanes) and lane_id not in active):
                            continue
                    if lane_id not in active:
                        beat = lane[indices[lane_id]]
                        if beat.wait > 0 and now < resting.setdefault((lane_id, indices[lane_id]), now + beat.wait):
                            continue
                        targets = dict(beat.targets)
                        observed = self._bus.positions(targets)
                        validate_pose(observed)
                        changes = {ch for ch,value in targets.items() if abs(observed[ch]-value)>2}
                        if beat.throttle_duration and not throttle_clear(observed, targets):
                            raise ProviderFailure('ThrottleRouteChanged')
                        self._bus.profiles(motion_profiles(beat, observed))
                        cancel.check()
                        if self._cancel.is_set():
                            raise Cancelled()
                        if self.clock() >= request.expires_at:
                            raise ProviderFailure('ServoRequestExpired')
                        if set(HEAD).intersection(changes) and beat.reframes:
                            reframed = True
                            if not self.camera_moving:
                                self.camera_moving = True
                                self.on_camera_motion(True, request.request_id, request.kind)
                        touched.update(targets)  # Include partially sent batches on failure.
                        self._bus.targets(targets)
                        changed.update(changes)
                        self.commanded.update(targets)
                        if 7 in targets:
                            self.microphone_moved_until = float('inf')
                            self.on_motion()   # The array moved: earlier bearings no longer apply.
                        active[lane_id] = [now + 25., None]
                    beat = lane[indices[lane_id]]
                    deadline, arrived = active[lane_id]
                    observed = self._bus.positions(dict(beat.targets))
                    if all(abs(observed[ch] - value) <= 2 for ch, value in beat.targets):
                        if arrived is None:
                            arrived = self.clock()
                            active[lane_id][1] = arrived
                        if self.clock() >= arrived + beat.dwell:
                            indices[lane_id] += 1
                            del active[lane_id]
                            if indices[lane_id] == len(lane):
                                completed.add(lane_id)
                                if request.kind == 'talking':
                                    indices[lane_id] = 0  # Fast lanes repeat while slower ones finish.
                    else:
                        active[lane_id][1] = None
                        if self.clock() >= deadline:
                            # Name the channels still short of their targets: the board's own
                            # stored channel range clamps a target outside it, so the pulse
                            # never arrives (first live run October 10: shutdown head lane).
                            lagging = ','.join(f'ch{ch}@{observed[ch]}->{value}' for ch, value in beat.targets
                                               if abs(observed[ch] - value) > 2)
                            raise ProviderFailure('ServoArrivalTimeout:' + lagging)
                if len(completed) < len(lanes) or active:
                    self.wait(.025)
            cancel.check()
            if self._cancel.is_set():
                raise Cancelled()
            if self.clock() >= request.expires_at:
                raise ProviderFailure('ServoRequestExpired')
            self.state = 'parked' if request.kind in {'park', 'shutdown'} else 'active'
            if request.kind == 'pose' and request.pose == 'release_poses':
                self.held_channels.clear()
                self.hold_until_released = False
                self.held_channels.update(self.kept_channels)
                if self._arm_returning:
                    self.held_channels.update(ARM_RETURN)
            if request.kind == 'base_release':
                self.held_channels.difference_update(BASE_HELD)
                self._base_held = False
            if request.kind == 'startup':
                self._talking_head_origin = self._bus.positions(HEAD)
            return receipt('pulses_reached')
        except (Cancelled, KeyboardInterrupt):
            if accepted:
                try:
                    self._hold(touched)
                    self.state = 'active' if request.kind in {'talking','idle','pose','reflex','base_park','base_release','attention','gesture'} else 'stopped'
                except Exception:
                    self.state = 'faulted'
                    return receipt('failed', 'ServoCancelHoldFailed')
            return receipt('cancelled', 'ServoCancelled')
        except ValueError as exc:
            if accepted:
                self.state = 'faulted'
                return receipt('failed', str(exc))
            return receipt('rejected', str(exc))
        except Exception as exc:
            self.state = 'faulted'
            # A working link can hold on timeout. A failed link is never retried.
            reason = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
            if reason.split(':')[0] in {'ServoArrivalTimeout', 'ServoRequestExpired', 'ThrottleRouteChanged'}:
                try:
                    self._hold(touched)
                except Exception:
                    reason += ':HoldFailed'
            return receipt('failed', reason)
        finally:
            try:
                if self.camera_moving:
                    self.camera_moving = False
                    self.on_camera_motion(False, request.request_id, request.kind)
            finally:
                if accepted:
                    self.motion_until = self.clock() + .5
                    if self.microphone_moved_until == float('inf'):
                        self.microphone_moved_until = self.motion_until
                        self.microphone_still_since = self.clock()
                    if reframed:
                        self.head_moved_at = self.clock()
                    self._motion.clear()
                self._lock.release()

    def begin_arm_return(self) -> None:
        """The drive is over: free the head at once.

        Owner, October 3: after every drive the head and the other servos stayed frozen
        until the throttle arm had finished un-parking. Only the arms the stance moved
        stay held now, until return_arm has walked them back to their awake poses
        (October 10: the poker arm had been let go here and never came back)."""
        self._arm_cancel.clear()
        self._arm_returning = True
        if self._base_held:   # Not when a kept arm comes out later: a person's held look stays.
            self.held_channels.difference_update(BASE_HELD - ARM_RETURN)
            self._base_held = False

    def return_arm(self, keep=frozenset()) -> ServoReceipt:
        """Walk the arms the drive stance moved back to their awake poses beside other motion.

        keep: REACHING_ARMS with something within their reach (owner, October 11). They stay
        at their drive stance, held, and become kept_arms; a later call without them (the
        drive owner saw the space clear) walks them back from there.

        The throttle arm goes from its drive park to rest; the hero elbow and the poker arm,
        elbow and wrist go back to their mid points (owner, October 10: after a drive the
        poker arm stayed raised and parked). Runs outside the single-request lock: its
        channels are held, so no other request commands them (execute rejects one that
        would), and the bus takes each wire exchange whole. The lanes advance together,
        each beat waiting for its own arrival; the routes and their clearance checks are
        base_lanes' own."""
        changed, done = set(), False
        def receipt(status, reason=None):
            return ServoReceipt('base-release', status, self.version, self.clock(), self.medium,
                                (0,) if status == 'pulses_reached' else (), reason,
                                changed_channels=tuple(sorted(changed)))
        try:
            if self.state not in {'active', 'moving'} or self._bus is None:
                return receipt('rejected', 'ServoUnavailableOrParked')
            current = self._bus.positions(sorted(ARM_RETURN))
            keep = frozenset(keep)
            lanes = base_lanes('base_release', current, keep)
            indices = [0] * len(lanes)
            active = {}   # lane -> arrival deadline
            while any(index < len(lane) for index, lane in zip(indices, lanes)):
                if self._arm_cancel.is_set():
                    raise Cancelled()
                for lane_id, lane in enumerate(lanes):
                    if indices[lane_id] >= len(lane):
                        continue
                    beat = lane[indices[lane_id]]
                    targets = dict(beat.targets)
                    if lane_id not in active:
                        observed = self._bus.positions(targets)
                        validate_pose(observed)
                        if beat.throttle_duration and not throttle_clear(observed, targets):
                            raise ProviderFailure('ThrottleRouteChanged')
                        self._bus.profiles(motion_profiles(beat, observed))
                        self._bus.targets(targets)
                        changed.update(ch for ch, value in targets.items() if abs(observed[ch]-value) > 2)
                        self.commanded.update(targets)
                        active[lane_id] = self.clock() + 25.
                        continue
                    observed = self._bus.positions(targets)
                    if all(abs(observed[ch] - value) <= 2 for ch, value in targets.items()):
                        indices[lane_id] += 1
                        del active[lane_id]
                    elif self.clock() >= active[lane_id]:
                        lagging = ','.join(f'ch{ch}@{observed[ch]}->{value}' for ch, value in targets.items()
                                           if abs(observed[ch] - value) > 2)
                        raise ProviderFailure('ServoArrivalTimeout:' + lagging)
                if active:
                    self._arm_wait(.025)
            done = True
            return receipt('pulses_reached')
        except Cancelled:
            try:
                self._hold(set(ARM_RETURN))
            except Exception:
                self.state = 'faulted'
                return receipt('failed', 'ServoCancelHoldFailed')
            return receipt('cancelled', 'ServoCancelled')
        except ValueError as exc:
            return receipt('rejected', str(exc))
        except Exception as exc:
            self.state = 'faulted'
            reason = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
            if reason.split(':')[0] in {'ServoArrivalTimeout', 'ThrottleRouteChanged'}:
                try:
                    self._hold(set(ARM_RETURN))
                except Exception:
                    reason += ':HoldFailed'
            return receipt('failed', reason)
        finally:
            if done:
                self.held_channels.difference_update(ARM_RETURN)
                self.kept_arms = keep
                self.held_channels.update(self.kept_channels)
            self.motion_until = max(self.motion_until, self.clock() + .5)
            self._arm_returning = False

    def _hold(self, channels: set[int]) -> None:
        if channels and self._bus:
            for pair in PAIRS:
                if channels.intersection(pair):
                    channels.update(pair)
            pose = self._bus.positions(sorted(channels))
            validate_pose(pose)
            self._bus.targets(pose)
            self.commanded.update(pose)


class TalkingMotion:
    """Output-owned worker; audio callbacks only start/cancel, never write serial.

    Stop/join this worker before requesting park or yielding the transport. Failed
    or rejected beats terminate the worker; no reconnect or automatic recovery.
    """
    def __init__(self, owner: ServoOwner):
        self.owner = owner
        self._thread = None
        self._token = None
        self.result = None
        self.before_start = lambda: None
        self.gaze = None   # callable -> neck pulse toward the addressee, or None

    def start(self, turn_id: str, cancel: Cancellation) -> None:
        self.before_start()
        if self._thread is not None:
            raise ProviderFailure('TalkingMotionAlreadyActive')
        def valid():
            cancel.check()
            return True
        self._token = Cancellation(guard=valid)
        self.result = None
        try:
            # Look toward the person being answered; talking sway centres there.
            self.owner.talking_neck = self.gaze() if self.gaze else None
        except Exception:
            self.owner.talking_neck = None
        self.owner.talking_pitch = self.owner.gaze_pitch   # Not wherever the last sway stopped.
        def run():
            index = 0
            while not self._token.event.is_set():
                self.result = self.owner.execute(ServoRequest(
                    f'{turn_id}:talk:{index}', self.owner.version, 'talking',
                    self.owner.clock() + 30.), self._token)
                if self.result.status != 'pulses_reached':
                    break
                index += 1
        self._thread = threading.Thread(target=run, name='r3x-talking-motion', daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        if self._token:
            self._token.event.set()

    def stop(self) -> ServoReceipt | None:
        self.cancel()
        if self._thread:
            self._thread.join(timeout=2.)
            if self._thread.is_alive():
                raise ProviderFailure('TalkingMotionStillActive')
            self._thread = None
        return self.result
