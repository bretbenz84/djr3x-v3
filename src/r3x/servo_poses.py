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
JOINTS = {
    'neck': 0, 'head_lift': 1, 'head_tilt': 2, 'visor': 3,
    'hero_elbow': 4, 'hero_wrist': 5, 'poker_arm': 6, 'hero_arm': 7,
    'throttle_shoulder': 8, 'throttle_elbow': 9, 'throttle_wrist': 10,
}
THROTTLE = (8, 9, 10)


def joint_targets(positions):
    """Map bounded model percentages into calibrated pulses, without wire access."""
    import math
    if not isinstance(positions, dict) or set(positions) != set(JOINTS):
        raise ValueError('InvalidJointFields')
    targets = {}
    for name, value in positions.items():
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('InvalidJointPosition')
        channel = JOINTS[name]
        low, high = LIMITS[channel]
        targets[channel] = round(low + (high-low)*value/100)
    if not targets:
        raise ValueError('EmptyJointRequest')
    return targets
PARK = ((8, 9088), (9, 9984), (10, 2048))
TUCK = ((8, 6544), (9, 8400), (10, 2048))
REST = ((8, 4200), (9, 7720), (10, 5120))
FULL_DOWN = ((8, 9088), (9, 5589), (10, 9511))
RAISED = 2176
# Neck pulse where the head physically faces straight along the base. The owner
# saw 5472 (the electrical midpoint and V2 "neutral") sit visibly right while
# driving on September 27; set this from the servo console reading.
NECK_CENTER = 5472
# Head lift/tilt held while the base drives (owner: level stance when moving).
# V2 neutrals until the owner reads level values from the servo console.
DRIVE_HEAD = ((1, 3600), (2, 4320))
# Owner request September 27: while the base drives, the hero-arm elbow is fully up
# (higher pulse lifts it) and the poker arm at its highest value, held until done.
DRIVE_ARMS = ((4, 7424), (6, 8000))
HEAD_PARK = ((0, NECK_CENTER), (1, 2600), (2, 5504), (3, 4544), (4, 6300))
# Where each joint hangs once the robot has been powered off (the board then reads 0).
UNPOWERED_REST = (*HEAD_PARK, (5, 6000), (6, 6000), (7, 5984), *PARK)


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
    throttle_idle: bool = False


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
        # Slower lifecycle glide: V2 also paced intermediate targets in software.
        # Direct V3 targets need lower Maestro speed/acceleration caps.
        head = (Beat(((1, 3600), (2, 4320), (3, 6400), (4, 6720)), speed=12, acceleration=3),)
        neck = (Beat(((0, first),), speed=12, acceleration=3, dwell=.3),
                Beat(((0, second),), speed=12, acceleration=3, dwell=.3),
                Beat(((0, NECK_CENTER),), speed=12, acceleration=3, dwell=.2))
        throttle = bridge + (TUCK, ((8, 4800), (9, 8000), (10, 3800)),
                             ((8, 3680), (9, 8720), (10, 5920)), REST)
        lanes = (head, neck, tuple(Beat(p, throttle_duration=1.25) for p in throttle))
    else:
        # V2's coordinated head/visor/elbow droop, alongside tuck then park.
        # Decorative channels 5–7 keep their current pose, as in V2 shutdown.
        lanes = ((Beat(HEAD_PARK, speed=12, acceleration=3, dwell=.8),),
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
    # Retain V2 shoulder/elbow pacing. Halve throttle-wrist pacing to reduce
    # mechanical noise across idle, speech, requested poses and lifecycle moves.
    # Scale both caps and proportional travel, including acceleration.
    pace = {8: 1.375, 9: 1.76, 10: 1.1}
    speed = {ch: round(cap * pace[ch]) for ch, cap in zip(THROTTLE, (14,32,36) if beat.throttle_idle else (26,60,65))}
    accel = {ch: round(cap * pace[ch]) for ch, cap in zip(THROTTLE, (3,6,7) if beat.throttle_idle else (5,10,12))}
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


def talking_lanes(current: dict[int, int], head_origin: dict[int, int] | None = None, held=frozenset(),
                  neck: int | None = None) -> tuple[tuple[Beat, ...], ...]:
    """Independent speech lanes around stable head anchors, not interrupted poses.

    V2's neck/lift ranges and head/arm profiles, with smooth bounded beats instead
    of random per-frame reversals. Talking leaves microphone-hold channel 7 fixed.
    """
    targets = (((8, 3360), (9, 8560), (10, 7920)),
               ((8, 4000), (9, 7800), (10, 3800)))
    if set(THROTTLE).intersection(held):
        targets = ()
    previous = current
    for pose in targets:
        if not throttle_clear(previous, dict(pose)):
            raise ValueError('TalkingRouteOutsideClearance')
        previous = dict(pose)
    origin = dict(current if head_origin is None else head_origin)
    if neck is not None:
        origin[0] = max(LIMITS[0][0], min(LIMITS[0][1], int(neck)))
    def offset(ch, amount):
        return max(LIMITS[ch][0], min(LIMITS[ch][1], origin[ch]+amount))
    head = tuple(Beat(((0,offset(0,neck)),(1,offset(1,lift))),
                     speed=45,acceleration=8,dwell=.25)
                 for neck,lift in ((520,160),(-520,-160),(0,0)))  # About +/-7 degrees; V2's 260 barely read.
    # The thin headtilt linkage gets a slower, modest sway, never jitter.
    tilt = tuple(Beat(((2,offset(2,amount)),),speed=20,acceleration=6,dwell=.6)
                 for amount in (120,-120,0))
    # V2's speech floor permits visible expression instead of pinning the visor
    # at neutral. Keep it in the upper part of calibrated travel while speaking.
    visor = tuple(Beat(((3,target),),speed=45,acceleration=8,dwell=.15)
                  for target in (6520,6000,6640,6200))
    # V2 neutral-expression elbow/wrist range, with independent pacing. These
    # are channels 4/5, not channel 7 (the microphone-bearing hero-arm axis).
    elbow = tuple(Beat(((4,target),),speed=35,acceleration=8,dwell=.35)
                  for target in (7100,6740))
    wrist = tuple(Beat(((5,target),),speed=35,acceleration=8,dwell=.25)
                  for target in (7120,4880))
    lanes = (head,tilt,visor,elbow,wrist,
             tuple(Beat(p,dwell=1.,throttle_duration=1.7) for p in targets))
    lanes = tuple(lane for lane in lanes if lane and not set(dict(lane[0].targets)).intersection(held))
    for lane in lanes:
        for beat in lane:
            validate_pose(dict(beat.targets))
    return lanes


LOOK_NECK_DEG = 35.       # Idle glances stay inside the talking gaze range.
NECK_PER_DEG = (5472 - 1984) / 45.


def idle_head(current: dict[int, int], rng, face_neck=None) -> Beat:
    """Owner request September 27: when still, look at the face in front of him, or look
    around left/right and up/down. V2's room scan biased pitch downward (people sit)."""
    lift0, tilt0 = dict(DRIVE_HEAD)[1], dict(DRIVE_HEAD)[2]
    if face_neck is not None and rng.random() < .75:
        neck = face_neck + rng.randint(-40, 40)          # Hold on the face, never frozen.
        vertical = rng.uniform(-.1, .25)
        dwell = rng.uniform(1.5, 3.)
    else:
        neck = round(NECK_CENTER + rng.uniform(-LOOK_NECK_DEG, LOOK_NECK_DEG) * NECK_PER_DEG)
        vertical = rng.uniform(-.35, 1.)                 # Negative looks up, positive down.
        dwell = rng.uniform(.8, 2.)
    # Tilt is inverted (higher pulse looks lower); lift lowers the head with smaller pulses.
    tilt = tilt0 + vertical * .65 * ((LIMITS[2][1] - tilt0) if vertical >= 0 else (tilt0 - LIMITS[2][0]))
    lift = lift0 - vertical * 200
    clamp = lambda ch, v: max(LIMITS[ch][0], min(LIMITS[ch][1], round(v)))
    return Beat(((0, clamp(0, neck)), (1, clamp(1, lift)), (2, clamp(2, tilt))), speed=18, acceleration=4, dwell=dwell)


def idle_lanes(current: dict[int, int], rng, held=frozenset(), face_neck=None) -> tuple[tuple[Beat, ...], ...]:
    """V2 arm wander ranges plus a head look; return the microphone axis before listening settles."""
    def target(ch, center, amplitude, minimum):
        direction = (-1 if current[ch]>=center+minimum else
                     1 if current[ch]<=center-minimum else rng.choice((-1,1)))
        return max(LIMITS[ch][0], min(LIMITS[ch][1],
            center+direction*rng.randint(*amplitude)))
    hero = target(7,5984,(1300,2000),900)
    poker = target(6,6000,(800,1500),550)
    lanes = ((Beat(((7,hero),),speed=16,acceleration=4,dwell=.6),
             Beat(((7,5984),),speed=16,acceleration=4)),
            (Beat(((6,poker),),speed=35,acceleration=6),))
    if not set(THROTTLE).intersection(held):
        choices = (((8,4400),(9,7480),(10,4760)),
                   ((8,3920),(9,8080),(10,5600)),
                   ((8,4080),(9,7840),(10,4920)),
                   ((8,4480),(9,7640),(10,5360)),REST)
        target = rng.choice([p for p in choices if any(abs(current[ch]-v)>2 for ch,v in p)])
        route = (() if throttle_clear(current,dict(target)) else pose_lanes('throttle_rest',current)[0])
        previous = dict(route[-1].targets) if route else current
        if not throttle_clear(previous,dict(target)): raise ValueError('IdleRouteOutsideClearance')
        lanes += (route+(Beat(target,throttle_duration=rng.uniform(2.3,3.3),throttle_idle=True),),)
    if not {0, 1, 2}.intersection(held):
        lanes += ((idle_head(current, rng, face_neck),),)
    return tuple(lane for lane in lanes if not set(dict(lane[0].targets)).intersection(held))


def base_lanes(kind: str, current: dict[int, int]) -> tuple[tuple[Beat, ...], ...]:
    """Throttle park, centered neck and raised hero elbow/poker arm before base motion; the return to rest.

    V2 parks the throttle arm before every non-reverse base command. The neck is
    centered so head-camera bearings are base-relative. Return uses the validated
    startup route from PARK. Routes are checked before any lane moves.
    """
    if kind not in {'base_park', 'base_release'}:
        raise ValueError('UnknownBaseSequence')
    validate_pose(current)
    start = {ch: current[ch] for ch in THROTTLE}
    at = lambda pose: all(abs(start[ch] - value) <= 2 for ch, value in pose)
    if kind == 'base_park':
        if at(PARK):
            stages = []
        elif full_down_corridor(start):
            stages = [{8: RAISED, 9: 5589, 10: 9511}, {8: RAISED, 9: 8400, 10: 2048}, dict(TUCK), dict(PARK)]
        elif throttle_clear(start, dict(TUCK)):
            stages = [dict(TUCK), dict(PARK)]
        else:
            stages = [dict(REST), dict(TUCK), dict(PARK)]
    else:
        route = [dict(TUCK), {8: 4800, 9: 8000, 10: 3800}, {8: 3680, 9: 8720, 10: 5920}, dict(REST)]
        stages = [] if at(REST) else route if at(PARK) or throttle_clear(start, route[0]) else [dict(REST)]
    previous = start
    for stage in stages:
        validate_pose(stage)
        if not throttle_clear(previous, stage):
            raise ValueError('ThrottleRouteOutsideClearance')
        previous = stage
    lanes = ()
    if stages:
        lanes += (tuple(Beat(tuple(stage.items()), throttle_duration=1.25) for stage in stages),)
    if kind == 'base_park':
        head = tuple((ch, value) for ch, value in ((0, NECK_CENTER),) + DRIVE_HEAD if abs(current.get(ch, value) - value) > 2)
        if head:
            lanes += ((Beat(head, speed=20, acceleration=5),),)
        arms = tuple((ch, value) for ch, value in DRIVE_ARMS if abs(current.get(ch, value) - value) > 2)
        if arms:
            lanes += ((Beat(arms, speed=35, acceleration=8),),)
    return lanes


# Explicit look requests (September 28). look_left mirrors look_right about the centre:
# the startup scan's 4000 is only ~19 degrees, less than a 20% joint request, so a
# "look left" after one visibly swung right. Down/up use lift (smaller pulse lowers)
# and tilt (higher pulse looks lower), as idle_head does; level is the drive stance.
# A request for "down" through head_lift 30% raised the head above level (3600).
# Named look_* (not head_*) because the model announced "head_right" as "Turning right."
# Side and up/down are separate steps the model chains; each keeps the other axis.
NECK_LEFT, NECK_RIGHT = 2 * NECK_CENTER - 8000, 8000
HEAD_DOWN, HEAD_UP = ((1, 3200), (2, 5300)), ((1, 4200), (2, 4000))
NAMED_POSES = {
    'look_left': ((0,NECK_LEFT),), 'look_right': ((0,NECK_RIGHT),), 'look_center': ((0,NECK_CENTER),),
    'look_down': HEAD_DOWN, 'look_up': HEAD_UP, 'look_level': DRIVE_HEAD,
    'visor_down': ((3,4544),), 'visor_up': ((3,6400),),
    'throttle_offer': ((8,2176),(9,2601),(10,5938)),
    'throttle_high_five': ((8,2176),(9,9361),(10,6302)),
    'throttle_down': FULL_DOWN, 'throttle_rest': REST,
}
# One-shot gestures run and return to where they started; nothing is held afterwards.
GESTURES = ('wave',)
POSE_COMMANDS = (*NAMED_POSES, *GESTURES, 'release_poses')


def wave_lanes(current):
    """V2 wave-back (sequences/animations.py, fb96f08): the hero arm presents the hand
    (7 -> 4800, elbow 6300) and the wrist sweeps its full travel four times at V2's
    auto speed for a 0.32 s swing, then the arm returns to where it was. V2's elbow bob
    is omitted: a beat has one speed, and at the wrist's speed the elbow would snap.
    Sweeps use V2's unlimited acceleration (0)."""
    start = tuple((ch, current[ch]) for ch in (4, 5, 7))
    swing = round((LIMITS[5][1] - LIMITS[5][0]) / (.32 * 100))
    lane = [Beat(((7,4800),(4,6300),(5,6000)), speed=55, acceleration=14)]
    for _ in range(4):
        lane += [Beat(((5,LIMITS[5][1]),), speed=swing, acceleration=0),
                 Beat(((5,LIMITS[5][0]),), speed=swing, acceleration=0)]
    lane += [Beat(((5,6000),), speed=swing, acceleration=0, dwell=.16),
             Beat(start, speed=40, acceleration=8)]
    return (tuple(lane),)


def reflex_lanes(target):
    """Voice reflex: the neck alone, quicker than an idle glance, nothing held."""
    if set(target) != {0}: raise ValueError('ReflexMovesNeckOnly')
    validate_pose(target)
    return ((Beat(((0, target[0]),), speed=60, acceleration=15),),)


def pose_lanes(name, current):
    if isinstance(name, dict):
        target = dict(name)
        if set(target).intersection(THROTTLE):
            target = {**{ch: current[ch] for ch in THROTTLE}, **target}
        validate_pose(target)
    elif name == 'wave':
        return wave_lanes(current)
    elif name == 'release_poses':
        # Resume from rest, so the next speech loop has a valid clearance route.
        target = dict(REST)
    else:
        if name not in NAMED_POSES: raise ValueError('UnknownRobotPose')
        target = dict(NAMED_POSES[name])
    if not set(target).intersection(THROTTLE):
        return ((Beat(tuple(target.items()),speed=20,acceleration=5),),)
    other = {ch: value for ch, value in target.items() if ch not in THROTTLE}
    target = {ch: target[ch] for ch in THROTTLE}
    start = {ch:current[ch] for ch in THROTTLE}
    stages = [target]
    if not throttle_clear(start,target):
        if full_down_corridor(start):
            stages = [{8:RAISED,9:5589,10:9511}, {8:RAISED,9:8400,10:2048},target]
        elif full_down_corridor(target):
            stages = [{**start,8:RAISED},{**target,8:RAISED},target]
        else:
            stages = [dict(REST),target]
    previous = start
    for stage in stages:
        validate_pose(stage)
        if not throttle_clear(previous,stage): raise ValueError('ThrottleRouteOutsideClearance')
        previous = stage
    lanes = (tuple(Beat(tuple(stage.items()),throttle_duration=1.7) for stage in stages),)
    return lanes + (((Beat(tuple(other.items()), speed=20, acceleration=5),),) if other else ())
