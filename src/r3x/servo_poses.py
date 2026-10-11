"""V3-owned lifecycle choreography; positions are quarter-microsecond pulses.

Source/calibration provenance and physical limitations: docs/SERVO_INTEGRATION.md.
No devices, V2 imports, independent gesture workers, or inferred shaft positions.

October 10: the head rides on two facing servos (channels 2 and 11) whose linkages hold
one centre post. Driven against each other they pitch the head up or down; driven the
same way they shift the post sideways and tilt (cock) the head to one side. Every head
move therefore sets both channels, computed from a pitch and a tilt (head_pulses). The
poker arm gained an elbow (12) and a wrist (13) with a measured clearance ceiling, and
the visor servo now runs inverted (low pulses up).
"""
from dataclasses import dataclass, replace
from types import MappingProxyType


# Calibrated limits per channel. The October 10 servo-console readings (Maestro
# microseconds x 4) are the limits of the two head servos and the poker elbow/wrist.
LIMITS = MappingProxyType({
    0: (1984, 8960), 1: (2624, 7744), 2: (4032, 8128), 3: (4544, 6976),
    4: (6300, 7424), 5: (1984, 9984), 6: (3968, 8000), 7: (3968, 8000),
    8: (2176, 9088), 9: (2048, 9984), 10: (2048, 9984),
    11: (5248, 7616), 12: (3456, 7680), 13: (1984, 9944),
})
THROTTLE = (8, 9, 10)
HEAD_PAIR = (2, 11)          # head_left, head_right: always commanded together.
POKER = (12, 13)             # Poker elbow and wrist: always commanded together (clearance).
HEAD = (0, 1, *HEAD_PAIR)    # Everything the camera rides on.
PAIRS = (THROTTLE, HEAD_PAIR, POKER)


def _clamp(value, low, high):
    return max(low, min(high, value))


# Two-servo head, console readings October 10 (left, right).
HEAD_LEVEL = (5832, 6480)        # 1458 / 1620 us
HEAD_FULL_UP = (8128, 5248)      # 2032 / 1312 us: the left servo rises as the right falls.
HEAD_FULL_DOWN = (4052, 7616)    # 1013 / 1904 us
# A full side tilt moves both servos the same way by about this much in total (the four
# measured tilted poses moved 828-1492, each with one servo already at its limit). What
# one servo cannot take within its limits, the other takes.
TILT_SHIFT = 1100
HEAD_ENVELOPE = 600              # A pair further than this from any pitch/tilt is refused.
# Degrees the full pitch travel covers: V2's gaze-engine estimate, unverified on this linkage.
PITCH_DOWN_DEG, PITCH_UP_DEG = 20., 25.


def _pitch_pulses(pitch: float) -> tuple[float, float]:
    left, right = HEAD_LEVEL
    if pitch >= 0:
        return (left + pitch*(HEAD_FULL_UP[0]-left), right + pitch*(HEAD_FULL_UP[1]-right))
    return (left - pitch*(HEAD_FULL_DOWN[0]-left), right - pitch*(HEAD_FULL_DOWN[1]-right))


def head_pulses(pitch: float, tilt: float = 0.) -> dict[int, int]:
    """Pulses for the two head servos. pitch runs -1 (fully down) to 1 (fully up) and tilt
    -1 (cocked to Rex's left) to 1 (to his right); both are fractions of measured travel.
    Tilting right lowers both pulses, as the console readings showed."""
    pitch, tilt = _clamp(float(pitch), -1., 1.), _clamp(float(tilt), -1., 1.)
    left, right = _pitch_pulses(pitch)
    sign = -1 if tilt > 0 else 1
    shift = abs(tilt) * TILT_SHIFT
    room = [(LIMITS[2][1]-left) if sign > 0 else (left-LIMITS[2][0]),
            (LIMITS[11][1]-right) if sign > 0 else (right-LIMITS[11][0])]
    take = [min(shift/2, room[0]), min(shift/2, room[1])]
    take[0] += min(shift-sum(take), room[0]-take[0])
    take[1] += min(shift-sum(take), room[1]-take[1])
    return {2: round(left + sign*take[0]), 11: round(right + sign*take[1])}


def _fit_head(pulses) -> tuple[float, float, int]:
    """(pitch, tilt, pulse error) that best explain a pair of head pulses."""
    left, right = pulses[2], pulses[11]
    def score(pitch):
        base = _pitch_pulses(pitch)
        tilt = _clamp(-((left-base[0]) + (right-base[1]))/TILT_SHIFT, -1., 1.)
        got = head_pulses(pitch, tilt)
        return abs(got[2]-left) + abs(got[11]-right), pitch, tilt
    best = min(score(i/100.) for i in range(-100, 101))
    best = min(best, *(score(_clamp(best[1] + i/1000., -1., 1.)) for i in range(-10, 11)))
    return best[1], best[2], best[0]


def head_pose(pulses) -> tuple[float, float]:
    """The (pitch, tilt) a pair of head pulses stands for."""
    return _fit_head(pulses)[:2]


def pitch_from_degrees(degrees: float) -> float:
    """Pitch fraction for degrees above level (negative looks down)."""
    return _clamp(degrees / (PITCH_UP_DEG if degrees >= 0 else PITCH_DOWN_DEG), -1., 1.)


def degrees_from_pitch(pitch: float) -> float:
    return pitch * (PITCH_UP_DEG if pitch >= 0 else PITCH_DOWN_DEG)


def head_targets(spec: dict, current) -> dict[int, int]:
    """Pulses for a head pose given as lift/pitch/tilt, keeping whatever it leaves out."""
    pitch, tilt = head_pose(current)
    targets = head_pulses(spec.get('pitch', pitch), spec.get('tilt', tilt))
    if 'lift' in spec:
        targets[1] = spec['lift']
    return targets


# Poker elbow (12) and wrist (13), console readings October 10. The elbow rises fully only
# while the wrist is at or below 1564 us; curled further inward the wrist meets the arm and
# the elbow's ceiling falls (measured pairs, linear between them).
POKER_ELBOW_DOWN, POKER_ELBOW_UP = 3456, 7680
POKER_WRIST_DOWN, POKER_WRIST_STRAIGHT, POKER_WRIST_IN = 1984, 5760, 9944
POKER_ELBOW_CEILING = ((6256, 7680), (7576, 7228), (8544, 6836), (9984, 6388))


def poker_elbow_max(wrist: int) -> int:
    if wrist <= POKER_ELBOW_CEILING[0][0]:
        return POKER_ELBOW_UP
    for (w0, e0), (w1, e1) in zip(POKER_ELBOW_CEILING, POKER_ELBOW_CEILING[1:]):
        if wrist <= w1:
            return round(e0 + (e1-e0)*(wrist-w0)/(w1-w0))
    return POKER_ELBOW_CEILING[-1][1]


def poker_clear(pose) -> bool:
    return pose[12] <= poker_elbow_max(pose[13]) + 2


def poker_route(start, target) -> list[dict[int, int]]:
    """Stages between two clear poker poses that stay clear throughout: the elbow drops to
    the lower of the two first, then the wrist moves, then the elbow rises. Each stage moves
    one joint, so the ceiling (which only falls as the wrist curls in) is never crossed."""
    low = min(start[12], target[12])
    route, previous = [], {ch: start[ch] for ch in POKER}
    for stage in ({12: low, 13: start[13]}, {12: low, 13: target[13]}, {ch: target[ch] for ch in POKER}):
        if stage != previous:
            route.append(stage)
            previous = stage
    return route or [dict(previous)]


# Visor (3). October 10: the servo now runs inverted, low pulses up and high pulses down.
# Owner's console readings, October 11: neutral open 1433 us, surprise 1136 us (the widest it
# opens, the channel's low limit). Closed 1744 us reached on the robot at shutdown.
VISOR_OPEN, VISOR_SURPRISE, VISOR_DOWN = 5732, 4544, 6976
# Speaking and idle keep the September offsets around the open neutral (then 6400, speaking
# 6000-6640, idle 6050-6750, half-blink to 6000), mirrored for the inverted servo.
VISOR_SPEECH = (5492, 6132)      # Widest and most closed speaking targets.
VISOR_IDLE = (5382, 6082)

# name -> (channel or head axis, value at 0 %, value at 100 %)
JOINT_RANGES = {
    'neck': (0, 1984, 8960), 'head_lift': (1, 2624, 7744),
    'head_pitch': ('pitch', -1., 1.), 'head_tilt': ('tilt', -1., 1.),
    'visor': (3, VISOR_DOWN, 4544),
    'hero_elbow': (4, 6300, 7424), 'hero_wrist': (5, 1984, 9984), 'poker_arm': (6, 3968, 8000),
    'hero_arm': (7, 3968, 8000),
    'throttle_shoulder': (8, 2176, 9088), 'throttle_elbow': (9, 2048, 9984), 'throttle_wrist': (10, 2048, 9984),
    'poker_elbow': (12, POKER_ELBOW_DOWN, POKER_ELBOW_UP), 'poker_wrist': (13, POKER_WRIST_DOWN, POKER_WRIST_IN),
}
JOINTS = tuple(JOINT_RANGES)
# Body parts named to the model when their channels are held.
HELD_PARTS = (('neck', {0}), ('head_lift', {1}), ('head_pitch_and_tilt', {2, 11}), ('visor', {3}),
              ('hero_elbow', {4}), ('hero_wrist', {5}), ('poker_arm', {6}), ('hero_arm', {7}),
              ('throttle_arm', {8, 9, 10}), ('poker_elbow_and_wrist', {12, 13}))


def joint_targets(positions):
    """Map bounded model percentages into calibrated pulses, without wire access.

    head_pitch and head_tilt resolve to both head servos together; one given without the
    other puts the other at its middle (level, or straight)."""
    import math
    if not isinstance(positions, dict) or set(positions) != set(JOINTS):
        raise ValueError('InvalidJointFields')
    targets, head = {}, {}
    for name, value in positions.items():
        if value is None:
            continue
        if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError('InvalidJointPosition')
        channel, low, high = JOINT_RANGES[name]
        at = low + (high-low)*value/100
        if isinstance(channel, str):
            head[channel] = at
        else:
            targets[channel] = round(at)
    if head:
        targets.update(head_pulses(head.get('pitch', 0.), head.get('tilt', 0.)))
    if not targets:
        raise ValueError('EmptyJointRequest')
    return targets


def joint_percents(commanded) -> dict[str, int]:
    """Where each joint is, as the percentages set_robot_joints uses."""
    head = head_pose(commanded) if set(HEAD_PAIR).issubset(commanded) else None
    result = {}
    for name, (channel, low, high) in JOINT_RANGES.items():
        if isinstance(channel, str):
            if head is None:
                continue
            value = head[0] if channel == 'pitch' else head[1]
        elif channel in commanded:
            value = commanded[channel]
        else:
            continue
        result[name] = round(100*(value-low)/(high-low))
    return result


PARK = ((8, 9088), (9, 9984), (10, 2048))
TUCK = ((8, 6544), (9, 8400), (10, 2048))
REST = ((8, 4200), (9, 7720), (10, 5120))
FULL_DOWN = ((8, 9088), (9, 5589), (10, 9511))
RAISED = 2176
# Neck pulse where the head physically faces straight along the base. The owner
# saw 5472 (the electrical midpoint and V2 "neutral") sit visibly right while
# driving on September 27; set this from the servo console reading.
NECK_CENTER = 5472
# Head lift and the level, straight head pair held while the base drives (owner: level
# stance when moving). The pair is the owner's "level headed" console reading.
DRIVE_HEAD = ((1, 3600), (2, HEAD_LEVEL[0]), (11, HEAD_LEVEL[1]))
# Owner request September 27: while the base drives, the hero-arm elbow is fully up
# (higher pulse lifts it) and the poker arm at its highest value, held until done.
DRIVE_ARMS = ((4, 7424), (6, 8000))
# Owner, October 10: the poker arm's park, elbow up (1920 us) with the wrist at 1564 us. It
# is where the arm stands when the robot is switched on, the stance while the base drives,
# and where shutdown returns it (first live start: the arm began there, dropped at once to
# the assumed elbow-down rest, and shutdown left it down).
POKER_PARK = ((12, POKER_ELBOW_UP), (13, 6256))
POKER_DRIVE = POKER_PARK
# Owner, October 10 evening: awake, the arm sits 45 degrees up with the hand level (1500 /
# 942 us). Startup takes it there from its park and idle life wanders around it.
POKER_UP = ((12, 6000), (13, 3768))
# Where the arms the drive stance moved go back to once the wheels stop (owner, October 10:
# after a drive the poker arm stayed raised and parked instead of returning to its mid
# points): the hero elbow at its awake neutral (startup), the poker arm at mid swing, and
# the poker elbow/wrist at POKER_UP (base_lanes 'base_release').
AWAKE_ARMS = ((4, 6720), (6, 6000))
# The head lift's lowest pulse is 2624 (656 us): the board's own range clamps anything
# lower, so a park at 2600 never arrived (shutdown ServoArrivalTimeout:ch1@2624->2600,
# October 10 run b954c14c).
HEAD_PARK = ((0, NECK_CENTER), (1, 2624), (2, HEAD_FULL_DOWN[0]), (11, HEAD_FULL_DOWN[1]),
             (3, VISOR_DOWN), (4, 6300))
# Where each joint hangs once the robot has been powered off (the board then reads 0).
UNPOWERED_REST = (*HEAD_PARK, (5, 6000), (6, 6000), (7, 5984), *PARK, *POKER_PARK)


def validate_pose(pose: dict[int, int]) -> None:
    if not pose or any(type(ch) is not int or ch not in LIMITS or
                       type(value) is not int or not LIMITS[ch][0] <= value <= LIMITS[ch][1]
                       for ch, value in pose.items()):
        raise ValueError('InvalidServoPulse')
    for pair, error in ((THROTTLE, 'IncompleteThrottlePose'), (HEAD_PAIR, 'IncompleteHeadPose'),
                        (POKER, 'IncompletePokerPose')):
        if set(pose).intersection(pair) and not set(pair).issubset(pose):
            raise ValueError(error)
    if set(HEAD_PAIR).issubset(pose) and _fit_head(pose)[2] > HEAD_ENVELOPE:
        raise ValueError('HeadPairOutsideEnvelope')
    if set(POKER).issubset(pose) and not poker_clear(pose):
        raise ValueError('PokerElbowWristCollision')


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
    wait: float = 0.        # Seconds this beat's lane rests before the targets are sent.
    reframes: bool = True   # False: a head move too small to count as a new camera view.


def poker_lane(current, target, speed=20, acceleration=5) -> tuple[Beat, ...]:
    """The staged, clearance-safe poker elbow/wrist move to target."""
    start = {ch: current[ch] for ch in POKER}
    return tuple(Beat(tuple(stage.items()), speed=speed, acceleration=acceleration)
                 for stage in poker_route(start, target))


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
        head = (Beat((*DRIVE_HEAD, (3, VISOR_OPEN), (4, 6720)), speed=12, acceleration=3),)
        neck = (Beat(((0, first),), speed=12, acceleration=3, dwell=.3),
                Beat(((0, second),), speed=12, acceleration=3, dwell=.3),
                Beat(((0, NECK_CENTER),), speed=12, acceleration=3, dwell=.2))
        throttle = bridge + (TUCK, ((8, 4800), (9, 8000), (10, 3800)),
                             ((8, 3680), (9, 8720), (10, 5920)), REST)
        lanes = (head, neck, tuple(Beat(p, throttle_duration=1.25) for p in throttle))
        if any(abs(current[ch] - value) > 2 for ch, value in POKER_UP):
            lanes += (poker_lane(current, dict(POKER_UP), speed=12, acceleration=3),)
    else:
        # V2's coordinated head/visor/elbow droop, alongside tuck then park. Decorative
        # channels 5–7 keep their current pose; the poker elbow/wrist return to their park.
        lanes = ((Beat(HEAD_PARK, speed=12, acceleration=3, dwell=.8),),
                 tuple(Beat(p, throttle_duration=1.5) for p in bridge + (TUCK, PARK)))
        if any(abs(current[ch] - value) > 2 for ch, value in POKER_PARK):
            lanes += (poker_lane(current, dict(POKER_PARK), speed=12, acceleration=3),)
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
                  neck: int | None = None, pitch: float | None = None) -> tuple[tuple[Beat, ...], ...]:
    """Independent speech lanes around stable head anchors, not interrupted poses.

    pitch is where the gaze had the head pitched when the reply began (looking down at a
    seated face): the sway centres there instead of snapping back to level for each line.

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
    pitch0, tilt0 = head_pose(origin)
    if pitch is not None:
        pitch0 = _clamp(float(pitch), -.9, .9)
    def offset(ch, amount):
        return max(LIMITS[ch][0], min(LIMITS[ch][1], origin[ch]+amount))
    head = tuple(Beat(((0,offset(0,neck)),(1,offset(1,lift))),
                     speed=45,acceleration=8,dwell=.25)
                 for neck,lift in ((520,160),(-520,-160),(0,0)))  # About +/-7 degrees; V2's 260 barely read.
    # The two head servos sway slowly together: a small nod with a slight cock to each side.
    sway = tuple(Beat(tuple(head_pulses(pitch0+dp, tilt0+dt).items()),speed=20,acceleration=6,dwell=.6)
                 for dp,dt in ((.06,.1),(-.06,-.1),(0.,0.)))
    # V2's speech floor permits visible expression instead of pinning the visor
    # at neutral. Keep it in the upper part of calibrated travel while speaking.
    visor = tuple(Beat(((3,target),),speed=45,acceleration=8,dwell=.15)
                  for target in (5612,6132,5492,5932))
    # V2 neutral-expression elbow/wrist range, with independent pacing. These
    # are channels 4/5, not channel 7 (the microphone-bearing hero-arm axis).
    elbow = tuple(Beat(((4,target),),speed=35,acceleration=8,dwell=.35)
                  for target in (7100,6740))
    wrist = tuple(Beat(((5,target),),speed=35,acceleration=8,dwell=.25)
                  for target in (7120,4880))
    lanes = (head,sway,visor,elbow,wrist,
             tuple(Beat(p,dwell=1.,throttle_duration=1.7) for p in targets))
    lanes = tuple(lane for lane in lanes if lane and not set(dict(lane[0].targets)).intersection(held))
    for lane in lanes:
        for beat in lane:
            validate_pose(dict(beat.targets))
    return lanes


LOOK_NECK_DEG = 35.       # Idle glances stay inside the talking gaze range.
NECK_PER_DEG = (5472 - 1984) / 45.
# A head move inside these pulse deltas (about 5 degrees) leaves the camera view
# effectively unchanged: it does not restart scene settling or arrival tracking.
HEAD_SMALL = {0: 400, 1: 180, 2: 450, 11: 300}
PITCH_SMALL, TILT_SMALL = .12, .12   # The same idea in pitch/tilt fractions.


@dataclass(frozen=True)
class IdlePhrase:
    """One stretch of idle life: which parts move, each after its own short wait, so
    joints start and finish at different moments instead of all at once."""
    parts: tuple[tuple[str, float], ...]
    head: tuple[tuple[int, int], ...] = ()   # Gaze-chosen head targets; empty drifts in place.
    head_speed: int = 9
    head_dwell: float = 0.


IDLE_CHANNELS = {'head': HEAD, 'visor': (3,), 'elbow': (4,), 'wrist': (5,), 'poker': (6, *POKER),
                 'hero': (7,), 'throttle': THROTTLE}
THROTTLE_IDLE = (((8,4400),(9,7480),(10,4760)), ((8,3920),(9,8080),(10,5600)),
                 ((8,4080),(9,7840),(10,4920)), ((8,4480),(9,7640),(10,5360)), REST)
POKER_IDLE = {12: (5200, 6800), 13: (2900, 4700)}   # Around the awake 45-degree pose; under the ceiling.

# Head expressions on the two-servo head (owner, October 10 evening: "dogs cock their heads
# left and right when they're perplexed"; use the new freedom for more expression). Each is
# a finite run of (pitch change, tilt change, dwell) from where the head is, then back to it;
# "subtle" halves the amounts. 'side' expressions cock away from any lean already there.
HEAD_EXPRESSIONS = {
    'nod': {'moves': ((-.3, 0., .25), (.1, 0., .25)), 'speed': 35},
    'tilt': {'moves': ((0., .6, .5),), 'speed': 20, 'side': True},                  # One cock and back.
    'perplexed': {'moves': ((-.08, .6, .7), (-.08, -.6, .7)), 'speed': 25, 'side': True},   # The dog's double cock, looking slightly down.
    'wiggle': {'moves': ((0., .35, .12), (0., -.35, .12), (0., .35, .12)), 'speed': 50, 'acceleration': 10},  # Quick playful side to side.
    'sympathetic': {'moves': ((-.15, .45, 1.2),), 'speed': 12, 'acceleration': 3, 'side': True},   # Slow cock, slightly down, held.
    # The visor snaps wide open to its surprise position as the head lifts a little, holds,
    # and both ease back (owner, October 11: surprise is 1136 us).
    'surprised': {'moves': ((.15, 0., .8),), 'speed': 40, 'visor': VISOR_SURPRISE},
}
GESTURE_PRIMITIVES = (*HEAD_EXPRESSIONS, 'lift', 'arm_emphasis')


def head_reframes(current, targets) -> bool:
    return any(abs(current[ch] - value) > HEAD_SMALL[ch] for ch, value in targets if ch in HEAD_SMALL)


def _elsewhere(rng, low, high, here, least, most=None):
    """A new resting point between `least` and `most` away from here: idle neither dithers
    in place nor swings between the same two ends, and one move stays short enough (a few
    seconds at idle speed) that the head can keep answering what it sees."""
    most = most or high - low
    for _ in range(8):
        value = here + rng.choice((-1, 1)) * rng.randint(least, most)
        if low <= value <= high:
            return value
    middle = (low + high) // 2
    return max(low, min(high, here + (1 if here < middle else -1) * rng.randint(least, most)))


def expressive_lanes(current, kind, target, primitive, intensity, hold_seconds, held):
    """Finite chosen motion using existing head/talking ranges; never moves the mic.

    Direction is robot-relative, not a promise to fixate a detected image object.
    Explicit poses/drive stance reject the whole action instead of partially acting.
    """
    if target not in {'left','right','up','forward','unfocused'}:
        raise ValueError('DirectionalAttentionOnly')
    if intensity not in {'subtle','normal'} or hold_seconds not in (.5, 1.5):
        raise ValueError('InvalidExpression')
    clamp = lambda ch, value: max(LIMITS[ch][0], min(LIMITS[ch][1], round(value)))
    extra = ()
    if kind == 'attention':
        targets = {'left':((0,NECK_CENTER-1040),), 'right':((0,NECK_CENTER+1040),),
                   'up':tuple(head_targets(HEAD_POSES['look_up'], current).items()),
                   'forward':((0,NECK_CENTER),)+DRIVE_HEAD,
                   'unfocused':((0,clamp(0,current[0]+520)),)}[target]
        lane = (Beat(targets,speed=35,acceleration=6,dwell=hold_seconds),)
    elif kind == 'gesture':
        if target != 'unfocused':
            raise ValueError('GestureIsNotDirectionalGaze')
        scale = .5 if intensity=='subtle' else 1.
        if primitive in HEAD_EXPRESSIONS:
            # The two head servos: finite moves from where the head is, then back to it. A
            # sided expression cocks away from any lean the head already has.
            spec = HEAD_EXPRESSIONS[primitive]
            pitch0, tilt0 = head_pose(current)
            side = (-1. if tilt0 > .2 else 1.) if spec.get('side') else 1.
            start = tuple((ch, current[ch]) for ch in HEAD_PAIR)
            lane = tuple(Beat(tuple(head_pulses(pitch0+dp*scale, tilt0+side*dt*scale).items()),
                              speed=spec['speed'],acceleration=spec.get('acceleration',6),dwell=dwell)
                         for dp,dt,dwell in spec['moves'])
            lane += (Beat(start,speed=spec['speed'],acceleration=spec.get('acceleration',6)),)
            if 'visor' in spec:
                # Its own lane: the visor snaps (unlimited acceleration) while the head moves
                # at its own pace; subtle opens it half as far beyond where it is.
                wide = round(current[3] + (spec['visor'] - current[3]) * scale)
                extra = ((Beat(((3, wide),), speed=70, acceleration=0, dwell=spec['moves'][0][2]),
                          Beat(((3, current[3]),), speed=20, acceleration=5)),)
        else:
            channels = {'lift':(1,160), 'arm_emphasis':(4,200)}
            if primitive not in channels:raise ValueError('UnknownExpression')
            ch,amount=channels[primitive]
            lane = tuple(Beat(((ch,clamp(ch,current[ch]+offset*scale)),),
                speed=35,acceleration=6,dwell=.25 if offset else 0.) for offset in (amount,0))
    else:raise ValueError('UnknownExpression')
    lanes = (lane, *extra)
    if any(ch in held for lane in lanes for beat in lane for ch,_ in beat.targets):
        raise ValueError('ExpressionConflictsWithHeldPose')
    for lane in lanes:
        for beat in lane:validate_pose(dict(beat.targets))
    return lanes


def idle_lanes(current: dict[int, int], rng, held=frozenset(), phrase: IdlePhrase | None = None) -> tuple[tuple[Beat, ...], ...]:
    """Slow, measured idle movement for the parts one phrase names (owner, October 3:
    "always some motion going on, slow and measured", never every servo at once).

    Each part goes somewhere new at its own pace rather than back and forth: speeds are a
    third to a half of the September idle. Ranges stay inside what talking and V2 idle
    already use; the visor stays in its speaking range, above the camera. The hero arm
    carries the speaker array, so it swings less far, slowly, and returns to centre."""
    if phrase is None:
        phrase = IdlePhrase(tuple((part, 0.) for part in IDLE_CHANNELS))
    clamp = lambda ch, value: max(LIMITS[ch][0], min(LIMITS[ch][1], round(value)))
    lanes = []
    for part, wait in phrase.parts:
        channels = IDLE_CHANNELS[part]
        if set(channels).intersection(held):
            continue
        if part == 'head':
            if phrase.head:
                targets = tuple((ch, clamp(ch, value)) for ch, value in phrase.head)
            else:
                pitch, tilt = head_pose(current)
                targets = ((0, clamp(0, current[0] + rng.randint(-150, 150))),
                           (1, clamp(1, current[1] + rng.randint(-60, 60))),
                           *head_pulses(pitch + rng.uniform(-.03, .03), tilt + rng.uniform(-.06, .06)).items())
            lane = (Beat(targets, speed=phrase.head_speed, acceleration=3, dwell=phrase.head_dwell,
                         reframes=head_reframes(current, targets)),)
        elif part == 'visor':
            first = _elsewhere(rng, *VISOR_IDLE, current[3], 150, 500)
            lane = (Beat(((3, first),), speed=rng.randint(5, 10), acceleration=2),)
            if rng.random() < .25:   # A slow half-blink: down to the speaking floor and open again.
                lane = (Beat(((3, VISOR_SPEECH[1]),), speed=rng.randint(8, 14), acceleration=3, dwell=rng.uniform(.2, .6)),
                        Beat(((3, rng.randint(VISOR_OPEN - 300, VISOR_OPEN + 50)),), speed=rng.randint(6, 10), acceleration=2))
        elif part == 'elbow':
            lane = (Beat(((4, _elsewhere(rng, 6500, 7200, current[4], 150, 500)),),
                         speed=rng.randint(5, 11), acceleration=2),)
        elif part == 'wrist':
            target = _elsewhere(rng, 4600, 7400, current[5], 500, 1600)   # Just beyond the talking range.
            lane = (Beat(((5, target),), speed=rng.randint(8, 18), acceleration=3),)
            if rng.random() < .3:    # Roll a little past, pause, ease back.
                back = clamp(5, target + (-1 if target > current[5] else 1) * rng.randint(250, 500))
                lane = (Beat(((5, target),), speed=rng.randint(10, 18), acceleration=3, dwell=rng.uniform(.3, .9)),
                        Beat(((5, back),), speed=rng.randint(6, 10), acceleration=2))
        elif part == 'poker':
            # The arm swings a little, and its elbow and wrist drift low and slow, always
            # routed in clearance-safe stages (the first stage rides with the swing).
            target = {ch: _elsewhere(rng, *POKER_IDLE[ch], current[ch], 300, 900) for ch in POKER}
            speed = rng.randint(7, 15)
            route = poker_lane(current, target, speed=speed, acceleration=3)
            lane = (Beat(((6, _elsewhere(rng, 4500, 7500, current[6], 500, 1500)), *route[0].targets),
                         speed=speed, acceleration=3), *route[1:])
        elif part == 'hero':
            side = (-1 if current[7] >= 5984 + 500 else 1 if current[7] <= 5984 - 500 else rng.choice((-1, 1)))
            lane = (Beat(((7, clamp(7, 5984 + side * rng.randint(700, 1400))),), speed=9, acceleration=2,
                         dwell=rng.uniform(.8, 1.6)),
                    Beat(((7, 5984),), speed=9, acceleration=2))
        elif part == 'throttle':
            target = rng.choice([p for p in THROTTLE_IDLE if any(abs(current[ch]-v) > 2 for ch, v in p)])
            route = (() if throttle_clear(current, dict(target)) else pose_lanes('throttle_rest', current)[0])
            previous = dict(route[-1].targets) if route else current
            if not throttle_clear(previous, dict(target)): raise ValueError('IdleRouteOutsideClearance')
            lane = route + (Beat(target, throttle_duration=rng.uniform(3., 4.6), throttle_idle=True),)
        else:
            raise ValueError('UnknownIdlePart')
        if wait > 0:
            lane = (replace(lane[0], wait=wait), *lane[1:])
        for beat in lane:
            validate_pose({**{ch: current[ch] for ch in THROTTLE}, **dict(beat.targets)}
                          if beat.throttle_duration else dict(beat.targets))
        lanes.append(tuple(lane))
    return tuple(lanes)


def base_lanes(kind: str, current: dict[int, int]) -> tuple[tuple[Beat, ...], ...]:
    """Throttle park, centered neck, raised hero elbow/poker arm and the poker elbow/wrist
    drive pose before base motion; afterwards the throttle's return to rest beside the
    hero elbow, poker arm, elbow and wrist going back to their awake poses.

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
        if head and set(HEAD_PAIR).intersection(ch for ch, _ in head):
            head = tuple((ch, value) for ch, value in ((0, NECK_CENTER),) + DRIVE_HEAD if ch in dict(head) or ch in HEAD_PAIR)
        if head:
            lanes += ((Beat(head, speed=20, acceleration=5),),)
        arms = tuple((ch, value) for ch, value in DRIVE_ARMS if abs(current.get(ch, value) - value) > 2)
        if arms:
            lanes += ((Beat(arms, speed=35, acceleration=8),),)
        if set(POKER).issubset(current) and any(abs(current[ch] - value) > 2 for ch, value in POKER_DRIVE):
            lanes += (poker_lane(current, dict(POKER_DRIVE)),)
    else:
        # The other arms the stance moved come back beside the throttle's walk: the hero
        # elbow and the poker arm's swing to their awake mid points, the poker elbow and
        # wrist to the awake 45-degree pose by the staged clearance route.
        arms = tuple((ch, value) for ch, value in AWAKE_ARMS if ch in current and abs(current[ch] - value) > 2)
        if arms:
            lanes += ((Beat(arms, speed=20, acceleration=5),),)
        if set(POKER).issubset(current) and any(abs(current[ch] - value) > 2 for ch, value in POKER_UP):
            lanes += (poker_lane(current, dict(POKER_UP), speed=12, acceleration=3),)
    return lanes


# Explicit look requests (September 28). look_left mirrors look_right about the centre:
# the startup scan's 4000 is only ~19 degrees, less than a 20% joint request, so a
# "look left" after one visibly swung right. Named look_* (not head_*) because the model
# announced "head_right" as "Turning right." Side, up/down and the side tilt are separate
# steps the model chains; each keeps the other axes. October 10: up/down pitch the two
# head servos against each other; tilt_* cock the head to a side (the new expression).
NECK_LEFT, NECK_RIGHT = 2 * NECK_CENTER - 8000, 8000
# First live run October 10: Rex was following Bret at the neck's left limit when asked to
# "look left", and the fixed 33-degree pose swung his head back to the right. A side look
# now never moves the head the other way: from anywhere nearer the centre it goes to the
# 33-degree pose; already there or beyond, it goes on to the neck's limit on that side.
NECK_POSES = {'look_left': -1, 'look_right': 1}
LOOK_PITCH, LOOK_TILT = .8, .8
HEAD_POSES = {
    'look_down': {'lift': 3200, 'pitch': -LOOK_PITCH}, 'look_up': {'lift': 4200, 'pitch': LOOK_PITCH},
    'look_level': {'lift': 3600, 'pitch': 0., 'tilt': 0.},
    'tilt_left': {'tilt': -LOOK_TILT}, 'tilt_right': {'tilt': LOOK_TILT}, 'tilt_straight': {'tilt': 0.},
}
NAMED_POSES = {
    'look_center': ((0,NECK_CENTER),),
    'visor_down': ((3,VISOR_DOWN),), 'visor_up': ((3,VISOR_OPEN),),
    'throttle_offer': ((8,2176),(9,2601),(10,5938)),
    'throttle_high_five': ((8,2176),(9,9361),(10,6302)),
    'throttle_down': FULL_DOWN, 'throttle_rest': REST,
    # Poker arm, owner's console readings October 10.
    'poker_out': ((12,4228),(13,5556)),      # Straight out: elbow 1057 us, wrist 1389 us.
    'poker_up': POKER_UP,                    # 45 degrees up with the hand level: 1500 / 942 us.
    'poker_down': ((12,POKER_ELBOW_DOWN),(13,POKER_WRIST_STRAIGHT)),   # Elbow down, wrist straight.
}
# One-shot gestures run and return to where they started; nothing is held afterwards. The
# head expressions (not the plain one-cock 'tilt', which tilt_left/tilt_right cover as held
# looks) are also steps people can ask for and Rex can plan.
GESTURES = ('wave', 'nod', 'perplexed', 'wiggle', 'sympathetic', 'surprised')
# Owner request October 3: the model chooses between following faces and looking where it
# decides. look_at_people resumes following faces; any look_* step holds that direction.
GAZE = ('look_at_people',)
# hold_arms moves nothing: every arm stays where it is until a person says it may move.
# The array arm (7) is left to the microphone lease, which only ever rests it at its centre;
# idle movement stops swinging it while the arms are held (idle_motion).
ARM_HOLD = (4, 5, 6, *POKER, *THROTTLE)
POSE_COMMANDS = (*NAMED_POSES, *NECK_POSES, *HEAD_POSES, *GESTURES, *GAZE, 'hold_arms', 'release_poses')


def neck_target(name, current, further=True) -> int:
    """look_left/look_right from the current neck: the 33-degree pose, or, when the head
    already looks that far, the limit on that side (a person asking again wants more) or,
    for Rex's own repeated step, no move at all."""
    side = NECK_POSES[name]
    pose = NECK_RIGHT if side > 0 else NECK_LEFT
    neck = current[0]
    if side * (neck - pose) >= -2:          # Already at the pose or beyond it on that side.
        return (LIMITS[0][1] if side > 0 else LIMITS[0][0]) if further else neck
    return pose


def pose_channels(name) -> tuple[int, ...]:
    """The channels a named pose or pose dict commands (gestures and gaze steps: none)."""
    if isinstance(name, dict):
        return tuple(name)
    if name in HEAD_POSES:
        return ((1,) if 'lift' in HEAD_POSES[name] else ()) + HEAD_PAIR
    if name in NECK_POSES:
        return (0,)
    return tuple(ch for ch, _ in NAMED_POSES.get(name, ()))


def at_pose(name, commanded) -> bool:
    """Whether the commanded pulses already stand at a named pose."""
    if name in NECK_POSES:
        # Looking that way at least as far as the pose counts, since the step would only go further.
        return 0 in commanded and NECK_POSES[name] * (commanded[0] - (NECK_RIGHT if NECK_POSES[name] > 0 else NECK_LEFT)) >= -2
    if name in HEAD_POSES:
        spec = HEAD_POSES[name]
        if not set(HEAD_PAIR).issubset(commanded):
            return False
        pitch, tilt = head_pose(commanded)
        return (abs(commanded.get(1, -10000) - spec.get('lift', commanded.get(1, -10000))) <= 2 and
                abs(pitch - spec.get('pitch', pitch)) <= .05 and abs(tilt - spec.get('tilt', tilt)) <= .05)
    return name in NAMED_POSES and all(abs(commanded.get(ch, -10000) - value) <= 2 for ch, value in NAMED_POSES[name])


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


def pose_lanes(name, current, further=True):
    if isinstance(name, dict):
        target = dict(name)
        for pair in (THROTTLE, POKER):   # A partial arm request keeps the unnamed joints where they are.
            if set(target).intersection(pair):
                target = {**{ch: current[ch] for ch in pair}, **target}
        validate_pose(target)
    elif name == 'wave':
        return wave_lanes(current)
    elif name in GESTURES:
        return expressive_lanes(current, 'gesture', 'unfocused', name, 'normal', .5, frozenset())
    elif name == 'look_at_people':
        # RobotServos aims at a visible face; with none in view, people are usually ahead.
        target = dict(((0, NECK_CENTER),) + DRIVE_HEAD)
    elif name == 'hold_arms':
        return ()   # Nothing moves: the servo owner only marks the arms held.
    elif name == 'release_poses':
        # Resume from rest, so the next speech loop has a valid clearance route.
        target = dict(REST)
    elif name in HEAD_POSES:
        target = head_targets(HEAD_POSES[name], current)
    elif name in NECK_POSES:
        target = {0: neck_target(name, current, further)}
    else:
        if name not in NAMED_POSES: raise ValueError('UnknownRobotPose')
        target = dict(NAMED_POSES[name])
    lanes = ()
    if set(target).intersection(THROTTLE):
        throttle = {ch: target.pop(ch) for ch in THROTTLE}
        start = {ch:current[ch] for ch in THROTTLE}
        stages = [throttle]
        if not throttle_clear(start,throttle):
            if full_down_corridor(start):
                stages = [{8:RAISED,9:5589,10:9511}, {8:RAISED,9:8400,10:2048},throttle]
            elif full_down_corridor(throttle):
                stages = [{**start,8:RAISED},{**throttle,8:RAISED},throttle]
            else:
                stages = [dict(REST),throttle]
        previous = start
        for stage in stages:
            validate_pose(stage)
            if not throttle_clear(previous,stage): raise ValueError('ThrottleRouteOutsideClearance')
            previous = stage
        lanes += (tuple(Beat(tuple(stage.items()),throttle_duration=1.7) for stage in stages),)
    if set(POKER).issubset(target):
        lanes += (poker_lane(current, {ch: target.pop(ch) for ch in POKER}),)
    if target:
        lanes += ((Beat(tuple(target.items()), speed=20, acceleration=5),),)
    return lanes
