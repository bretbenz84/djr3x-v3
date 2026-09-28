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
from .servo_poses import (LIMITS, THROTTLE, lifecycle_lanes, motion_profiles, base_lanes,
                          talking_lanes, idle_lanes, throttle_clear, validate_pose, pose_lanes, NAMED_POSES)

BASE_HELD = frozenset({0, 1, 2, *THROTTLE})


@dataclass(frozen=True)
class ServoRequest:
    request_id: str
    version: int
    kind: str
    expires_at: float
    first_look: str = 'left'
    pose: str | dict[int, int] | None = None


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
        self._talking_head_origin: dict[int, int] | None = None
        self._motion = threading.Event()
        self.motion_until = 0.
        self.microphone_hold = (0., 0.)
        self.on_motion = lambda: None
        self.commanded: dict[int, int] = {}   # Last pulses sent or read back; camera bearings use the neck.
        self.talking_neck: int | None = None  # Gaze anchor for the current reply's talking lanes.

    def start(self) -> None:
        with self._lock:
            if self.state != 'new':
                raise ProviderFailure('ServoStartAlreadyAttempted')
            self.state = 'starting'
            try:
                self._bus = Maestro(self.open_port())
                # Read-only startup. Zero/partial readback cannot locate a cold joint.
                readback = self._bus.positions(LIMITS)
                validate_pose(readback)
                self.commanded = dict(readback)
                self.state = 'ready'
            except Exception:
                self.state = 'faulted'
                if self._bus:
                    self._bus.close()
                raise

    def cancel(self) -> None:
        self._cancel.set()
        self.release_microphone_hold()

    def neck_degrees(self) -> float:
        from .drive_space import neck_degrees
        from .servo_poses import NECK_CENTER
        return neck_degrees(self.commanded.get(0, NECK_CENTER))

    def motion_risk(self) -> bool:
        return self._motion.is_set() or self.clock() < self.motion_until

    def release_microphone_hold(self):
        self.microphone_hold = (0., 0.)

    def microphone_held_during(self, start, end):
        ready, expires = self.microphone_hold
        return 0 < ready <= start <= end <= expires

    def refresh_microphone_hold(self):
        """Bounded 30-second lease, centered before any qualifying robot capture.

        Idle excursions can target channel 7 and invalidate the hold. Motion wins
        the lock; learning waits for recentering and a new settled hold.
        """
        if self.clock() < self.microphone_hold[1] or not self._lock.acquire(blocking=False):
            return
        try:
            if self.state != 'active' or not self._bus or self.motion_risk() or 7 in self.held_channels:
                return
            center = sum(LIMITS[7]) // 2
            current = self._bus.positions((7,))[7]
            validate_pose({7:current})
            if abs(current-center) > 2:
                self._motion.set()
                self.on_motion()
                self._bus.profiles({7:(22,6)})
                self._bus.targets({7:center})
                self.commanded[7] = center
                deadline = self.clock() + 5.
                while abs(self._bus.positions((7,))[7]-center)>2:
                    if self._cancel.is_set() or self.clock() >= deadline:
                        self._hold({7})
                        raise ProviderFailure('MicrophoneCenterNotCompleted')
                    self.wait(.025)
            now = self.clock()
            self.motion_until = now + 1.5
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
        def receipt(status, reason=None):
            return ServoReceipt(request.request_id, status, self.version, self.clock(),
                                self.medium, tuple(sorted(completed)), reason)
        if not self._lock.acquire(blocking=False):
            return receipt('rejected', 'ServoBusy')
        touched = set()
        accepted = False
        try:
            if self.state not in {'ready', 'active', 'stopped'} or self._bus is None:
                return receipt('rejected', 'ServoUnavailableOrParked')
            if (request.version != self.version or not request.request_id or
                    not math.isfinite(request.expires_at) or self.clock() >= request.expires_at):
                return receipt('rejected', 'StaleServoRequest')
            if request.kind in {'talking','idle','pose','base_park','base_release'} and self.state != 'active':
                return receipt('rejected', {'idle':'IdleRequiresCompletedStartup','talking':'TalkingRequiresCompletedStartup','pose':'PoseRequiresCompletedStartup'}.get(request.kind,'BaseMotionRequiresCompletedStartup'))
            if request.kind == 'startup' and self.state != 'ready':
                return receipt('rejected', 'StartupAlreadyConsumed')
            self._cancel.clear()
            cancel.check()
            current = self._bus.positions(LIMITS)
            validate_pose(current)
            lanes = (talking_lanes(current, self._talking_head_origin, self.held_channels, self.talking_neck) if request.kind == 'talking' else
                     idle_lanes(current,self.rng,self.held_channels) if request.kind == 'idle' else
                     pose_lanes(request.pose,current) if request.kind == 'pose' else
                     base_lanes(request.kind,current) if request.kind in {'base_park','base_release'} else
                     lifecycle_lanes(request.kind, current, request.first_look))
            self.version += 1
            if request.kind == 'pose' and request.pose != 'release_poses':
                self.held_channels.update(ch for lane in lanes for beat in lane for ch, _ in beat.targets)
            if request.kind == 'base_park':
                # Talking/idle lanes skip held channels while the base drives.
                self.held_channels.update(BASE_HELD)
            accepted = True
            self.release_microphone_hold()
            self._motion.set()
            self.on_motion()
            self.state = 'parking' if request.kind in {'park', 'shutdown'} else 'moving'
            # Each lane owns disjoint channels. One loop owns every wire operation.
            indices = [0] * len(lanes)
            active = {}
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
                        targets = dict(beat.targets)
                        observed = self._bus.positions(targets)
                        validate_pose(observed)
                        if beat.throttle_duration and not throttle_clear(observed, targets):
                            raise ProviderFailure('ThrottleRouteChanged')
                        self._bus.profiles(motion_profiles(beat, observed))
                        cancel.check()
                        if self._cancel.is_set():
                            raise Cancelled()
                        if self.clock() >= request.expires_at:
                            raise ProviderFailure('ServoRequestExpired')
                        touched.update(targets)  # Include partially sent batches on failure.
                        self._bus.targets(targets)
                        self.commanded.update(targets)
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
                            raise ProviderFailure('ServoArrivalTimeout')
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
            if request.kind == 'base_release':
                self.held_channels.difference_update(BASE_HELD)
            if request.kind == 'startup':
                self._talking_head_origin = self._bus.positions((0,1,2))
            return receipt('pulses_reached')
        except (Cancelled, KeyboardInterrupt):
            if accepted:
                try:
                    self._hold(touched)
                    self.state = 'active' if request.kind in {'talking','idle','pose','base_park','base_release'} else 'stopped'
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
            if reason in {'ServoArrivalTimeout', 'ServoRequestExpired', 'ThrottleRouteChanged'}:
                try:
                    self._hold(touched)
                except Exception:
                    reason += ':HoldFailed'
            return receipt('failed', reason)
        finally:
            if accepted:
                self.motion_until = self.clock() + .5
                self._motion.clear()
            self._lock.release()

    def _hold(self, channels: set[int]) -> None:
        if channels and self._bus:
            if channels.intersection(THROTTLE):
                channels.update(THROTTLE)
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
