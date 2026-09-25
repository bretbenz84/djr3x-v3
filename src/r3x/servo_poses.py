"""V3-owned lifecycle choreography; positions are quarter-microsecond pulses.

Source/calibration provenance and physical limitations: docs/SERVO_INTEGRATION.md.
No devices, V2 imports, independent gesture workers, or inferred shaft positions.
"""
from dataclasses import dataclass
from types import MappingProxyType


LIMITS = MappingProxyType({
    0: (1984, 8960), 1: (2600, 7744), 2: (3904, 5504), 3: (4544, 6976),
    4: (6300, 7424), 5: (1984, 9984), 6: (3968, 8000), 7: (3968, 8000),
    8: (2176, 9088), 9: (2048, 9984), 10: (2048, 9984),
})
THROTTLE = (8, 9, 10)
PARK = ((8, 9088), (9, 9984), (10, 2048))
TUCK = ((8, 6544), (9, 8400), (10, 2048))
REST = ((8, 4200), (9, 7720), (10, 5120))
FULL_DOWN = ((8, 9088), (9, 5589), (10, 9511))
RAISED = 2176
HEAD_PARK = ((0, 5472), (1, 2600), (2, 5504), (3, 4544), (4, 6300))


def validate_pose(pose: dict[int, int]) -> None:
    if not pose or any(type(ch) is not int or ch not in LIMITS or
                       type(value) is not int or not LIMITS[ch][0] <= value <= LIMITS[ch][1]
                       for ch, value in pose.items()):
        raise ValueError('InvalidServoPulse')
    if set(pose).intersection(THROTTLE) and not set(THROTTLE).issubset(pose):
        raise ValueError('IncompleteThrottlePose')


def full_down_corridor(pose: dict[int, int]) -> bool:
    return (RAISED - 2 <= pose[8] <= 9088 + 2 and
            abs(pose[9] - 5589) <= 2 and abs(pose[10] - 9511) <= 2)


def throttle_clear(start: dict[int, int], end: dict[int, int]) -> bool:
    """Empirical clearance for independent joint progress, not Cartesian safety."""
    if full_down_corridor(start) and full_down_corridor(end):
        return True
    shoulder = max(start[8], end[8])
    elbow = min(start[9], end[9])
    wrist = max(start[10], end[10])
    minimum = 1546 if shoulder > 6810 else 636 if shoulder > 6546 else 500
    if elbow < minimum * 4 - 2:
        return False
    if shoulder <= RAISED + 2:
        return True
    if shoulder <= 5540 and elbow >= 2046 and wrist <= 9019:
        return True
    if shoulder <= 6546:
        return wrist <= 6002 or elbow >= 3598
    return elbow >= 6182 and wrist <= 2050


@dataclass(frozen=True)
class Beat:
    targets: tuple[tuple[int, int], ...]
    speed: int = 40
    acceleration: int = 8
    dwell: float = 0.
    throttle_duration: float | None = None


def lifecycle_lanes(kind: str, current: dict[int, int], first_look: str = 'left') -> tuple[tuple[Beat, ...], ...]:
    """Parallel lanes, advanced by a single executor. Park never enters a gesture."""
    if kind not in {'startup', 'park', 'shutdown'} or first_look not in {'left', 'right'}:
        raise ValueError('UnknownServoSequence')
    validate_pose(current)
    if set(current) != set(LIMITS):
        raise ValueError('IncompleteServoReadback')
    throttle_start = {ch: current[ch] for ch in THROTTLE}
    bridge = ()
    if full_down_corridor(throttle_start):
        bridge = (((8, RAISED), (9, 5589), (10, 9511)),
                  ((8, RAISED), (9, 8400), (10, 2048)))
    if kind == 'startup':
        first, second = (4000, 8000) if first_look == 'left' else (8000, 4000)
        head = (Beat(((1, 3600), (2, 4320), (3, 6400)), speed=30),)
        neck = (Beat(((0, first),), speed=28, dwell=.3),
                Beat(((0, second),), speed=28, dwell=.3),
                Beat(((0, 5472),), speed=28, dwell=.2))
        throttle = bridge + (TUCK, ((8, 4800), (9, 8000), (10, 3800)),
                             ((8, 3680), (9, 8720), (10, 5920)), REST)
        lanes = (head, neck, tuple(Beat(p, throttle_duration=1.25) for p in throttle))
    else:
        # V2's coordinated head/visor/elbow droop, alongside tuck then park.
        # Decorative channels 5–7 keep their current pose, as in V2 shutdown.
        lanes = ((Beat(HEAD_PARK, speed=70, acceleration=14, dwell=.8),),
                 tuple(Beat(p, throttle_duration=1.5) for p in bridge + (TUCK, PARK)))
    # Reject an unsafe route BEFORE any lane begins to move.
    for lane in lanes:
        previous = dict(current)
        for beat in lane:
            target = dict(beat.targets)
            validate_pose(target)
            if beat.throttle_duration and not throttle_clear(previous, target):
                raise ValueError('ThrottleRouteOutsideClearance')
            previous.update(target)
    return lanes


def motion_profiles(beat: Beat, current: dict[int, int]) -> dict[int, tuple[int, int]]:
    target = dict(beat.targets)
    if beat.throttle_duration is None:
        return {ch: (beat.speed, beat.acceleration) for ch in target}
    # V2's current working-tree pace, applied both to caps and proportional travel.
    pace = {8: 1.375, 9: 1.76, 10: 2.2}
    speed = {ch: round(cap * pace[ch]) for ch, cap in zip(THROTTLE, (26, 60, 65))}
    accel = {ch: round(cap * pace[ch]) for ch, cap in zip(THROTTLE, (5, 10, 12))}
    distance = {ch: abs(target[ch] - current[ch]) for ch in THROTTLE if target[ch] != current[ch]}
    if not distance:
        return {ch: (speed[ch], accel[ch]) for ch in THROTTLE}
    scale = max(beat.throttle_duration * 100,
                max(distance[ch] * pace[ch] / speed[ch] for ch in distance))
    ramp = max(distance[ch] * pace[ch] / accel[ch] for ch in distance)
    result = {ch: (max(1, min(speed[ch], round(distance[ch] * pace[ch] / scale))),
                 max(1, min(accel[ch], round(distance[ch] * pace[ch] / ramp))))
            for ch in distance}
    return {ch: result.get(ch, (speed[ch], accel[ch])) for ch in THROTTLE}


def talking_lanes(current: dict[int, int], lift_origin: int | None = None) -> tuple[tuple[Beat, ...], ...]:
    """Phrase-scale motion; no emotion inference and no microphone-arm movement."""
    targets = (((8, 3360), (9, 8560), (10, 7920)),
               ((8, 4000), (9, 7800), (10, 3800)))
    previous = current
    for pose in targets:
        if not throttle_clear(previous, dict(pose)):
            raise ValueError('TalkingRouteOutsideClearance')
        previous = dict(pose)
    # Small headlift emphasis. Heavy headtilt is deliberately not oscillated.
    # Visor and gaze remain where the lifecycle/attention owner put them.
    origin = current[1] if lift_origin is None else lift_origin
    lift = min(LIMITS[1][1], origin + 180)
    return ((Beat(((1, lift),), speed=30, dwell=.25),
             Beat(((1, origin),), speed=30, dwell=.5)),
            tuple(Beat(p, dwell=1., throttle_duration=1.7) for p in targets))
