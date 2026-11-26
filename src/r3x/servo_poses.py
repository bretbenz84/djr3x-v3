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
import math
import random
from types import MappingProxyType


# Calibrated limits per channel. The October 10 servo-console readings (Maestro
# microseconds x 4) are the limits of the two head servos and the poker elbow/wrist.
# Head lift (1): owner, November 12, 832-2496 us (was 656-1936).
# Throttle shoulder (8): owner, November 14, after its rework, 906-2000 us (was 544-2272); November 22, 1070-1960 us.
# Throttle hand (14 wrist twist, 15 two-finger pincher): owner, November 20, once installed, the
# limits programmed into the Maestro ("throttlehandwrst" 560-1856 us, "throttlefingers" 1264-1856 us).
# Owner, November 22: two more hand servos, the poker hand's twist (16, the Maestro's "pokerhandtwst")
# and the hero hand's fingers (17, "herofingers"), each 496-2496 us; later that day the fingers 1242-1748 us.
# Hero hand wrist (5): owner, November 22, 768-2100 us (was 496-2496), its mid 1436 us.
LIMITS = MappingProxyType({
    0: (1984, 8960), 1: (3328, 9984), 2: (4032, 8128), 3: (4544, 6976),
    4: (6300, 7424), 5: (3072, 8400), 6: (3968, 8000), 7: (3968, 8000),
    8: (4280, 7840), 9: (2048, 9984), 10: (2048, 9984),
    11: (5248, 7616), 12: (3456, 7680), 13: (1984, 9944),
    14: (2240, 7424), 15: (6000, 7900),   # Fingers 1500-1975 us (owner, November 21 night; was 1530).
    16: (1984, 9984), 17: (4968, 6992),
})
THROTTLE = (8, 9, 10)
# The throttle hand's wrist twist and pincher. Each moves on its own: the throttle clearance rule
# (shoulder, elbow, wrist bend) does not cover them, and no collision with the hand is measured yet.
THROTTLE_HAND = (14, 15)
# The poker hand's twist and the hero hand's fingers (November 22). Neither is in a clearance rule, and
# which end turns or closes which way is not yet measured.
POKER_TWIST, HERO_FINGERS = 16, 17
# The hero hand's wrist (channel 5) rests at its mid, 1436 us (owner, November 22).
HERO_WRIST_MID = 5744
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
# October 22 (1072c10a): "raise your visor all the way" sent it to the open neutral, inside the
# range it already wanders while talking, and nothing visibly rose. visor_up now holds it fully
# open, a little short of the surprise limit so the held servo is not pressed against its stop.
VISOR_UP = 4600                  # 1150 us.
# Speaking and idle keep the September offsets around the open neutral (then 6400, speaking
# 6000-6640, idle 6050-6750, half-blink to 6000), mirrored for the inverted servo.
VISOR_SPEECH = (5492, 6132)      # Widest and most closed speaking targets.
VISOR_IDLE = (5382, 6082)

# name -> (channel or head axis, value at 0 %, value at 100 %)
JOINT_RANGES = {
    'neck': (0, *LIMITS[0]), 'head_lift': (1, *LIMITS[1]),
    'head_pitch': ('pitch', -1., 1.), 'head_tilt': ('tilt', -1., 1.),
    'visor': (3, VISOR_DOWN, 4544),
    'hero_elbow': (4, 6300, 7424), 'hero_wrist': (5, *LIMITS[5]), 'poker_swing': (6, 3968, 8000),
    'hero_swing': (7, 3968, 8000),
    'throttle_shoulder': (8, *LIMITS[8]), 'throttle_elbow': (9, 2048, 9984), 'throttle_forearm': (10, 2048, 9984),
    'poker_elbow': (12, POKER_ELBOW_DOWN, POKER_ELBOW_UP), 'poker_forearm': (13, POKER_WRIST_DOWN, POKER_WRIST_IN),
    'throttle_wrist': (14, *LIMITS[14]), 'throttle_fingers': (15, *LIMITS[15]),
    'poker_wrist': (POKER_TWIST, *LIMITS[POKER_TWIST]), 'hero_fingers': (HERO_FINGERS, *LIMITS[HERO_FINGERS]),
}
JOINTS = tuple(JOINT_RANGES)
# Body parts named to the model when their channels are held.
HELD_PARTS = (('neck', {0}), ('head_lift', {1}), ('head_pitch_and_tilt', {2, 11}), ('visor', {3}),
              ('hero_elbow', {4}), ('hero_wrist', {5}), ('poker_swing', {6}), ('hero_swing', {7}),
              ('throttle_arm', {8, 9, 10}), ('poker_elbow_and_forearm', {12, 13}), ('throttle_hand', {14, 15}),
              ('poker_wrist', {POKER_TWIST}), ('hero_fingers', {HERO_FINGERS}))


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


# Throttle shoulder, owner, November 14, after its rework: straight down 1850 us (park and the
# relaxed rest), 45 degrees up 1240 us (the awake rest) and fully up 906 us (the upper limit).
# 2000 us, the lower limit, is past straight down and only the joint tool and servo menu reach it.
# Owner, November 22: the range is 1070-1960 us, so fully up (RAISED: the offer, the high five and the
# way out of the lowered full-down pose) is 1070 us; 1960 us is still past straight down.
# Every other shoulder position below, and the clearance rule's shoulder thresholds, were scaled
# from the old ones to the same place in the swing between those three points (owner's choice;
# old fully up 544, awake rest 1050 and fully down 2272 us).
SHOULDER_DOWN, SHOULDER_45, RAISED = 7400, 4960, LIMITS[8][0]
PARK = ((8, SHOULDER_DOWN), (9, 9984), (10, 2048))
TUCK = ((8, 6132), (9, 8400), (10, 2048))
REST = ((8, SHOULDER_45), (9, 7720), (10, 5120))
FULL_DOWN = ((8, SHOULDER_DOWN), (9, 5589), (10, 9511))
# The throttle hand parks, and rests while awake, here. The wrist at 1451 us, where the hand is level
# (owner, November 21: its mid point; not the middle of its range, 1208 us); the fingers at 1706 us,
# the owner's mid (November 21; their range is 1500-1975 us since that night). Idle and talking motion centre on these.
HAND_PARK = ((14, 5804), (15, 6824))
# The poker hand's twist rests at the middle of its range (1496 us; owner, November 22, until its
# directions and rest point are measured); the hero fingers at their mid, 1500 us (owner, later that day).
TIPS_PARK = ((POKER_TWIST, sum(LIMITS[POKER_TWIST]) // 2), (HERO_FINGERS, 6000))
# Neck pulse where the head physically faces straight along the base. The owner
# saw 5472 (the electrical midpoint and V2 "neutral") sit visibly right while
# driving on September 27; set this from the servo console reading.
NECK_CENTER = 5472
# The head lift's normal height: startup, idle, talking and driving stand here. Owner,
# November 15: 1093 us (was the midpoint of the range, 1664 us, since November 12).
HEAD_LIFT_NORMAL = 4372
# Surprised, the head shoots up to 2310 us (owner, November 15), then settles back.
HEAD_LIFT_SURPRISE = 9240
# The lift's pace when it moves in a lane of its own (a pose, a joint request, the way back down
# from a surprise). Owner, November 15: slightly quicker (speed was 20).
HEAD_LIFT_SPEED, HEAD_LIFT_ACCEL = 25, 5
SURPRISE_LIFT_SPEED, SURPRISE_LIFT_ACCEL = 45, 8   # The way up: about 1.5 s from the normal height.
# Head lift and the level, straight head pair held while the base drives (owner: level
# stance when moving). The pair is the owner's "level headed" console reading.
DRIVE_HEAD = ((1, HEAD_LIFT_NORMAL), (2, HEAD_LEVEL[0]), (11, HEAD_LEVEL[1]))
# Owner request September 27: while the base drives, the hero-arm elbow is fully up
# (higher pulse lifts it) and the poker arm at its highest value, held until done.
DRIVE_ARMS = ((4, 7424), (6, 8000))
# Owner, October 10: the poker arm's park, elbow up (1920 us) with the wrist at 1564 us. It
# is where the arm stands when the robot is switched on, the stance while the base drives,
# and where shutdown returns it (first live start: the arm began there, dropped at once to
# the assumed elbow-down rest, and shutdown left it down). Owner, November 24: the forearm parks
# at 1429 us (was 1564), still low enough for the elbow to rise fully.
POKER_PARK = ((12, POKER_ELBOW_UP), (13, 5716))
POKER_DRIVE = POKER_PARK
# Owner, October 10 evening: awake, the arm sits 45 degrees up with the hand level (1500 /
# 942 us). Startup takes it there from its park and idle life wanders around it.
POKER_UP = ((12, 6000), (13, 3768))
# Where the arms the drive stance moved go back to once the wheels stop (owner, October 10:
# after a drive the poker arm stayed raised and parked instead of returning to its mid
# points): the hero elbow at its awake neutral (startup), the poker arm at mid swing, and
# the poker elbow/wrist at POKER_UP (base_lanes 'base_release').
AWAKE_ARMS = ((4, 6720), (6, 6000))
# The arms that reach out from the body, by the channels that carry them. Owner, October 11:
# either stays at its drive stance after a drive while something is within its reach
# (drive_space.arms_blocked); base_lanes 'base_release' then leaves it where it is.
# Owner, November 20: the throttle hand's twist and fingers move freely with the arm folded, so a
# kept throttle arm keeps only its shoulder, elbow and wrist bend; the hand goes on moving.
REACHING_ARMS = MappingProxyType({'poker': (6, *POKER), 'throttle': THROTTLE})
# The head lift parks at its lowest pulse, never below: the board's own range clamps anything
# lower, so a park under it never arrives (shutdown ServoArrivalTimeout:ch1@2624->2600,
# October 10 run b954c14c). Owner, November 12: 3328 (832 us), the new low limit.
HEAD_PARK = ((0, NECK_CENTER), (1, LIMITS[1][0]), (2, HEAD_FULL_DOWN[0]), (11, HEAD_FULL_DOWN[1]),
             (3, VISOR_DOWN), (4, 6300))
# Where each joint hangs once the robot has been powered off (the board then reads 0).
UNPOWERED_REST = (*HEAD_PARK, (5, HERO_WRIST_MID), (6, 6000), (7, 5984), *PARK, *POKER_PARK, *HAND_PARK, *TIPS_PARK)


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
    return (RAISED - 2 <= pose[8] <= SHOULDER_DOWN + 2 and
            abs(pose[9] - 5589) <= 2 and abs(pose[10] - 9511) <= 2)


def throttle_route(current, target) -> list[dict[int, int]]:
    """Clearance-checked throttle stages from current to target (target last): direct when
    clear, else through the waypoints that are. The lowered rest (October 21: shoulder fully
    down, wrist folded) is reached and left through the tuck."""
    start, target = {ch: current[ch] for ch in THROTTLE}, dict(target)
    if throttle_clear(start, target):
        routes = ([target],)
    elif full_down_corridor(start):
        routes = ([{8:RAISED,9:5589,10:9511}, {8:RAISED,9:8400,10:2048}, target],)
    elif full_down_corridor(target):
        routes = ([{**start,8:RAISED}, {**target,8:RAISED}, target],)
    else:
        routes = tuple([*map(dict, via), target] for via in ((REST,), (TUCK,), (TUCK, REST), (REST, TUCK)))
    for stages in routes:
        for stage in stages:
            validate_pose(stage)
        if all(throttle_clear(a, b) for a, b in zip([start, *stages], stages)):
            return stages
    raise ValueError('ThrottleRouteOutsideClearance')


def throttle_clear(start: dict[int, int], end: dict[int, int]) -> bool:
    """Empirical clearance for independent joint progress, not Cartesian safety."""
    if full_down_corridor(start) and full_down_corridor(end):
        return True
    shoulder = max(start[8], end[8])
    elbow = min(start[9], end[9])
    wrist = max(start[10], end[10])
    minimum = 1546 if shoulder > 6263 else 636 if shoulder > 6134 else 500
    if elbow < minimum * 4 - 2:
        return False
    if shoulder <= RAISED + 2:
        return True
    if shoulder <= 5629 and elbow >= 2046 and wrist <= 9019:
        return True
    if shoulder <= 6134:
        return wrist <= 6002 or elbow >= 3598
    return elbow >= 6182 and wrist <= 2050


@dataclass(frozen=True)
class Beat:
    targets: tuple[tuple[int, int], ...]
    speed: int = 40
    acceleration: int = 8
    dwell: float = 0.
    throttle_duration: float | None = None
    wrist_speed: int | None = None   # A slower cap for the throttle wrist alone (talking and idle).
    wait: float = 0.        # Seconds this beat's lane rests before the targets are sent.
    at: float | None = None   # Not sent before this many seconds into the request: lanes keeping one clock (the wake).
    reframes: bool = True   # False: a head move too small to count as a new camera view.
    seconds: float | None = None   # Travel time of an eased beat (eased_beat): its profiles arrive together.
    profiles: tuple[tuple[int, int, int], ...] = ()   # (channel, speed, acceleration) per joint, when each eases differently.


# Owner, November 24: an obstacle within the poker arm's keep-clear distance on his left no longer
# keeps it parked outright. Its elbow may come out of the straight-up park only as far as 1778 us
# and its forearm (13) moves between 1508 and 1668 us; the swing (6) stays where it is, and the
# hand's twist (16) moves as always. Once the left side reads clear the arm comes out as usual.
# Driving still parks it fully first (base_lanes 'base_park').
POKER_NEAR_OBSTACLE = MappingProxyType({12: (7112, LIMITS[12][1]), 13: (6032, 6672)})


def poker_near_obstacle(lanes, current) -> tuple[tuple[Beat, ...], ...]:
    """lanes with the poker swing left where it is and the elbow and forearm kept inside
    POKER_NEAR_OBSTACLE. A beat whose clamped pose is not a single-joint move from the one
    before is reached by poker_route's staged clearance route."""
    out = []
    for lane in lanes:
        at, beats = {ch: current[ch] for ch in POKER}, []
        for beat in lane:
            targets = {ch: value for ch, value in beat.targets if ch != 6}
            if set(POKER).issubset(targets):
                pose = {ch: _clamp(targets[ch], *POKER_NEAR_OBSTACLE[ch]) for ch in POKER}
                targets.update(pose)
                for stage in poker_route(at, pose)[:-1]:
                    beats.append(Beat(tuple(stage.items()), speed=20, acceleration=5, wait=beat.wait, at=beat.at))
                at = pose
            if targets:
                beats.append(replace(beat, targets=tuple(targets.items()),
                                     profiles=tuple(p for p in beat.profiles if p[0] in targets)))
        if beats:
            out.append(tuple(beats))
    return tuple(out)


def poker_lane(current, target, speed=20, acceleration=5) -> tuple[Beat, ...]:
    """The staged, clearance-safe poker elbow/wrist move to target."""
    start = {ch: current[ch] for ch in POKER}
    return tuple(Beat(tuple(stage.items()), speed=speed, acceleration=acceleration)
                 for stage in poker_route(start, target))


def hand_home(current, speed=12, acceleration=3, park=HAND_PARK) -> tuple[tuple[Beat, ...], ...]:
    """A hand's lane back to its park (the throttle hand's by default), or none when it is already there."""
    if all(abs(current[ch] - value) <= 2 for ch, value in park):
        return ()
    return ((Beat(park, speed=speed, acceleration=acceleration),),)


# The hands' living motion (owner, November 19 and 22; made more expressive November 24). Each wrist
# twists in a lane of its own and each hand's fingers gesture in another, starting together: eased
# curls somewhere new and, now and then, a quick flick toward the far end and back part way, so the
# fingers keep working for as long as their arm moves instead of gliding once. Reaches are shares of
# the half range either side of the park, cut at the limits; talking reaches further and quicker.
HAND_IDLE_REACH = {14: .5, 15: .55}     # Was .3 / .25.
HAND_TALK_REACH = {14: .75, 15: .85}    # Was .5 / .45.
HAND_IDLE_SECONDS, HAND_IDLE_DWELL = (1.5, 2.5), (0., .3)   # One wrist twist (was 1.1-2 s: wider now, as slow).
HAND_TALK_SECONDS, HAND_TALK_DWELL = (.6, 1.), (.1, .3)
FINGER_IDLE = MappingProxyType({'seconds': (.5, 1.1), 'dwell': (.1, .6), 'flick': .3})
FINGER_TALK = MappingProxyType({'seconds': (.3, .6), 'dwell': (.05, .25), 'flick': .45})
FLICK_SECONDS = (.28, .4)   # Out to the far end; back part way takes half as long again.
HAND_TALK_SECONDS_PLANNED = 5.   # Finger gestures planned per talking cycle (like the head's), so a repeat is rare.


# The poker hand's twist and the hero fingers (owner, November 22): the same kind of motion, each
# moving with its own arm while idle and on their own while he speaks.
TIPS_IDLE_REACH = {POKER_TWIST: .45, HERO_FINGERS: .55}   # Was .25 each.
TIPS_TALK_REACH = {POKER_TWIST: .65, HERO_FINGERS: .85}   # Was .4 each.
FINGERS = (15, HERO_FINGERS)


def hand_span(ch, reach) -> tuple[int, int]:
    middle, half = {**dict(HAND_PARK), **dict(TIPS_PARK)}[ch], (LIMITS[ch][1] - LIMITS[ch][0]) / 2
    return max(LIMITS[ch][0], round(middle - half * reach[ch])), min(LIMITS[ch][1], round(middle + half * reach[ch]))


def hand_moves(rng, start, reach, count, seconds, dwell, pace=1., channels=THROTTLE_HAND, until=0.) -> tuple[Beat, ...]:
    """At least count eased moves of a hand's channels together (the throttle hand's by default), each
    somewhere new inside reach, and more until about until seconds are planned."""
    at, lane, planned = {ch: start[ch] for ch in channels}, [], 0.
    while len(lane) < count or (planned < until and len(lane) < 12):
        aim = []
        for ch in channels:
            low, high = hand_span(ch, reach)
            aim.append((ch, _elsewhere(rng, low, high, at[ch], max(1, (high - low) // 6), max(2, (high - low) // 2))))
        lane.append(eased_beat(at, aim, rng.uniform(*seconds) * pace, rng.uniform(*dwell)))
        planned += lane[-1].seconds + lane[-1].dwell
        at.update(aim)
    return tuple(lane)


def finger_moves(rng, start, ch, reach, style, until, pace=1.) -> tuple[Beat, ...]:
    """A hand's fingers gesturing for about until seconds (at least one move): eased curls somewhere
    new and, with style['flick'] odds, a quick flick to the far end of reach and back part way."""
    low, high = hand_span(ch, reach)
    at, lane, planned = start[ch], [], 0.
    while not lane or (planned < until and len(lane) < 24):
        if rng.random() < style['flick']:
            far = high if high - at >= at - low else low
            flick = rng.uniform(*FLICK_SECONDS)
            moves = ((far, flick, rng.uniform(.03, .12)),
                     (round(far + (at - far) * rng.uniform(.5, .8)), flick * 1.5, rng.uniform(*style['dwell'])))
        else:
            span = high - low
            moves = ((_elsewhere(rng, low, high, at, max(1, span // 5), span), rng.uniform(*style['seconds']),
                      rng.uniform(*style['dwell'])),)
        for value, seconds, dwell in moves:
            lane.append(eased_beat({ch: at}, ((ch, value),), seconds * pace, dwell))
            planned += lane[-1].seconds + lane[-1].dwell
            at = value
    return tuple(lane)


def hand_idle_lanes(rng, current, wrist, fingers, reach, until, wait, pace, held) -> list[tuple[Beat, ...]]:
    """An idle hand: its wrist twisting and its fingers gesturing, each in a lane of its own, for about
    as long as its arm moves (until; 0 for one twist), both starting together."""
    start = wait + rng.uniform(0., .4)
    lanes = []
    if wrist is not None and wrist not in held:
        lanes.append(hand_moves(rng, current, reach, 1, HAND_IDLE_SECONDS, HAND_IDLE_DWELL, pace=pace,
                                channels=(wrist,), until=until))
    if fingers is not None and fingers not in held:
        lanes.append(finger_moves(rng, current, fingers, reach, FINGER_IDLE, until or HAND_IDLE_SECONDS[1], pace))
    return [(replace(lane[0], wait=start), *lane[1:]) for lane in lanes]


def throttle_park_stages(current) -> list[dict[int, int]] | None:
    """Clearance-checked throttle stages to PARK for a park or shutdown, or None when none is clear.

    November 19: the shutdown park had one route (tuck, then park; the full-down bridge first). From
    anywhere a straight move to the tuck was not clear it was refused before anything moved, the head
    and visor included, and the arm, kept where it was at each wake, refused every shutdown after
    (runs 6c1f1cdf, 89d66b1c, 45da7ce9). That route is still tried first; then through the rest pose,
    then the throttle_route waypoints. None leaves the arm where it is and the rest still parks."""
    start = {ch: current[ch] for ch in THROTTLE}
    first = [dict(TUCK), dict(PARK)]
    if full_down_corridor(start):
        first = [{8: RAISED, 9: 5589, 10: 9511}, {8: RAISED, 9: 8400, 10: 2048}, *first]
    routes = [first, [dict(REST), dict(TUCK), dict(PARK)]]
    try:
        routes.append(throttle_route(start, dict(PARK)))
    except ValueError:
        pass
    for stages in routes:
        try:
            for stage in stages:
                validate_pose(stage)
        except ValueError:
            continue
        if all(throttle_clear(a, b) for a, b in zip([start, *stages], stages)):
            return stages
    return None


def throttle_parks_nowhere(current) -> bool:
    """Not already parked, and no clear route there: a park leaves the throttle where it is."""
    at_park = all(abs(current[ch] - value) <= 2 for ch, value in PARK)
    return not at_park and throttle_park_stages(current) is None


# The wake (owner, November 21): "like a person waking up and stretching", in step with the startup
# sound and the lights. Before, the head rose slowly once and levelled, then the neck looked left and
# right "while nothing else happens". The sound's build is heard about 1.5 s after the wake begins and
# lasts 11.8 s; as heard it stirs, pushes to a first peak 3.5 s in, dies away around 4 s, comes back
# by 5 s, is strong from 6.5 s and loudest from 10 s until it cuts to the chime (startup_audio.loudness),
# and the mouth lights twinkle and then grow with it. So, in seconds from the wake's start: he stirs as
# the sound begins (1.5) and sinks back; tries harder as it pushes (3.5 to 4.8: the head comes up and
# cocks, the pole lifts, an arm half stretches, the visor lifts a crack) and droops as it dies (to 6.2);
# shifts about, restless, with a full stretch of the hero elbow; tries again, cocked the other way, as
# the sound grows strong (7.7 to 9.0), and sags once more; and as it reaches its loudest the head comes
# up level, the neck back from a groggy look to each side, the arm settling to its awake rest. He is
# up about half a second before the chime (WAKE_SECONDS; the chime is 13.3 s after the wake begins).
# Owner, after the first try: the head and arm "look great, exactly what I wanted", but they were
# done four seconds before the chime, so "one more round of up and down struggle".
# It ends where the old wake did: level head at the normal height, neck centred, visor shut, hero elbow
# at its awake neutral. Every move eases in and out (eased_beat). The lanes keep one clock (Beat.at),
# so a slow arrival in one move does not push the rest late.
#
# Each row: (seconds into the wake it begins, seconds to arrive, hold once there, the pose).
WAKE_SECONDS = 12.8
WAKE_HEAD = (   # (pitch, side tilt, lift): -1 is the head fully down, 0 level; tilt below zero is to his left.
    (1.5, 1.0, 0., (-.72, .15, None)),            # A first stir,
    (2.6, .8, 0., (-.95, 0., None)),              # and back down.
    (3.5, 1.3, .2, (-.35, -.3, 3900)),            # The sound pushes: a real try, cocked to his left, the pole lifting.
    (5.15, 1.0, 0., (-.85, .2, 3450)),            # It dies away: he droops.
    (6.25, .7, 0., (-.6, -.18, None)),            # Restless,
    (7.05, .5, 0., (-.72, .1, None)),             # this way and that.
    (7.7, 1.3, .3, (-.28, .28, 4000)),            # The sound is strong now: another try, cocked to his right.
    (9.5, 1.0, 0., (-.78, -.12, 3550)),           # Not yet: he sags again,
    (10.6, .55, 0., (-.5, .15, 3700)),            # shakes it off,
    (11.3, 1.4, 0., (0., 0., HEAD_LIFT_NORMAL)),  # and with the sound at its loudest: up, level, awake.
)
# The visor lifts a crack and drops again, never far enough to show his eyes. The owner, November 21:
# "1680 and 1700 are both visor positions that look like it's trying to open but you can't see the eyes yet".
WAKE_VISOR_CRACKS = (6800, 6720)   # 1700 and 1680 us; lower pulses are higher.
WAKE_VISOR = ((4.0, .4, .15, 6800), (4.65, .35, 0., VISOR_DOWN),
              (6.4, .45, .1, 6720), (7.05, .4, 0., VISOR_DOWN),
              (8.6, .45, .25, 6720), (9.4, .4, 0., VISOR_DOWN),
              (10.6, .35, 0., 6800), (11.05, .35, 0., VISOR_DOWN))
# The hero elbow (higher pulses lift it): a twitch, half a stretch with the first try, the full
# stretch, a sag, one more reach, and down to its awake neutral as the head comes up.
WAKE_ELBOW = ((2.0, .6, 0., 6520), (2.7, .5, 0., 6340),
              (3.6, 1.1, .2, 6950), (5.0, 1.0, 0., 6380),
              (6.5, 1.2, .35, 7380), (8.2, .9, 0., 6420),
              (9.6, 1.1, .3, 7300), (11.3, 1.1, 0., 6720))
# The neck: still while he first struggles, then a groggy look to each side, and back to the middle
# as the head comes up. Pulses from the centre, first to the side he looks first.
WAKE_NECK = ((5.9, 1.0, 0., 800), (7.0, 1.0, 0., -900), (9.5, 1.0, 0., 500), (11.3, .9, 0., 0))


def wake_lanes(current: dict[int, int], first_look: str = 'left') -> tuple[tuple[Beat, ...], ...]:
    """The head, visor, neck and hero elbow through the wake (see WAKE_HEAD): four lanes of eased
    moves on one clock."""
    def lane(channels, rows, pulses):
        at, beats = {ch: current[ch] for ch in channels}, []
        for begins, seconds, hold, pose in rows:
            targets = pulses(pose, at)
            beats.append(replace(eased_beat(at, targets.items(), seconds, hold), at=begins))
            at.update(targets)
        return tuple(beats)
    def head(pose, at):
        pitch, tilt, lift = pose
        return {**head_pulses(pitch, tilt), 1: at[1] if lift is None else lift}
    side = -1 if first_look == 'left' else 1   # Lower neck pulses turn him to his left.
    shut = Beat(((3, VISOR_DOWN),), speed=12, acceleration=3)   # Shut first, if it is not (owner, November 15).
    return (lane((1, *HEAD_PAIR), WAKE_HEAD, head),
            (shut, *lane((3,), WAKE_VISOR, lambda pulse, at: {3: pulse})),
            lane((0,), WAKE_NECK, lambda offset, at: {0: NECK_CENTER + side * offset}),
            lane((4,), WAKE_ELBOW, lambda pulse, at: {4: pulse}))


# The reaching arms' part of the wake, for an arm whose space reads clear while he wakes. Owner,
# November 21, after seeing both arms unfold all the way 4.5 s into the wake (run de486648): "a smaller
# stretch routine on those that match the startup sound clip rather than a full movement. It would
# eventually get to the standard poses". The wheel board's sensors cannot say an arm's space is clear
# before about 4.3 s (it connects in 1.4 s, then two seconds of clear readings), so the arms join as the
# sound comes back from its dip: a shrug and a sag, more with the second try, and out to the awake
# poses with the head's last rise. Every pose here is inside the arms' own clearance rules
# (throttle_clear: below the tuck's shoulder height the wrist stays folded; poker_clear).
#
# Each row: (seconds into the wake it begins, seconds to arrive, the pose).
WAKE_THROTTLE = (   # (shoulder, elbow, wrist bend); the shoulder lifts as its pulse falls.
    (5.8, 1.0, (6600, 9200, 2048)),             # A shrug as the sound comes back,
    (6.9, .8, (7100, 9700, 2048)),              # and most of the way down again.
    (7.8, 1.3, (5900, 8400, 2048)),             # With his second try the shoulder comes up past the tuck,
    (9.1, .8, (5900, 8300, 3400)),              # where the wrist can flex a little,
    (10.0, .9, (6100, 8600, 2500)),             # and sags with him.
    (11.2, 1.4, tuple(value for _, value in REST)),       # Out to its awake rest with the last rise.
)
WAKE_POKER = (      # (elbow, wrist), from straight up at its park: down a little and back, further, back, and to the awake 45 degrees.
    (5.9, .9, (7100, 5900)),
    (6.9, .7, (7450, 6100)),
    (7.9, 1.2, (6500, 4800)),
    (9.5, .9, (6950, 5300)),
    (11.3, 1.2, tuple(value for _, value in POKER_UP)),
)


def _poker_move_clear(start, end) -> bool:
    """Elbow and wrist moving together stay under the elbow's ceiling only where it is flat."""
    return poker_clear(end) and max(start[13], end[13]) <= POKER_ELBOW_CEILING[0][0]


def wake_arm_lanes(current: dict[int, int], arms, elapsed: float) -> tuple[tuple[Beat, ...], ...] | None:
    """The wake's stretch for each of `arms`, joined `elapsed` seconds into the wake: the moves
    still to come, on the wake's clock (Beat.at), ending at the awake poses. Moves already over are
    left out when the rest is still a clear route from where the arm is. None when the routine has
    no place for this start: an arm away from its park, the last move already due, or no clear
    route. The caller then brings the arms out the usual way (base_lanes)."""
    lanes = []
    for arm, channels, park, rows, clear in (('throttle', THROTTLE, PARK, WAKE_THROTTLE, throttle_clear),
                                             ('poker', POKER, POKER_PARK, WAKE_POKER, _poker_move_clear)):
        if arm not in arms:
            continue
        start = {ch: current[ch] for ch in channels}
        if any(abs(start[ch] - value) > 2 for ch, value in park) or elapsed >= rows[-1][0]:
            return None
        over = sum(1 for begins, _, _ in rows[1:] if begins <= elapsed)   # Moves whose successor is already due.
        for skipped in range(over, -1, -1):
            poses = [dict(zip(channels, pose)) for _, _, pose in rows[skipped:]]
            if all(clear(a, b) for a, b in zip([start, *poses], poses)):
                break
        else:
            return None
        at, beats = dict(start), []
        for (begins, seconds, _), pose in zip(rows[skipped:], poses):
            validate_pose(pose)
            beats.append(replace(eased_beat(at, pose.items(), seconds), at=begins))
            at.update(pose)
        lanes.append(tuple(beats))
        swing = dict(AWAKE_ARMS)[6]
        if arm == 'poker' and abs(current[6] - swing) > 2:   # Its swing, as base_lanes brings it back.
            lanes.append((Beat(((6, swing),), speed=20, acceleration=5),))
    return tuple(lanes) or None


def lifecycle_lanes(kind: str, current: dict[int, int], first_look: str = 'left',
                    arms_home: bool = False, keep=frozenset()) -> tuple[tuple[Beat, ...], ...]:
    """Parallel lanes, advanced by a single executor. Park never enters a gesture.

    arms_home: the park also brings the poker arm's swing back to where it sleeps (a telepresence
    call, whose drive stance raised it; owner, November 16: park positions when the call ends).
    keep: on startup, the REACHING_ARMS the wake leaves where they are (owner, November 17: the
    poker arm came out at the wake into something beside him). The drive owner brings each out
    once its range sensors read the arm's space clear, by base_lanes' release route."""
    if kind not in {'startup', 'park', 'shutdown'} or first_look not in {'left', 'right'}:
        raise ValueError('UnknownServoSequence')
    if not set(keep) <= set(REACHING_ARMS) or (keep and kind != 'startup'):
        raise ValueError('UnknownKeptArm')
    validate_pose(current)
    if set(current) != set(LIMITS):
        raise ValueError('IncompleteServoReadback')
    throttle_start = {ch: current[ch] for ch in THROTTLE}
    bridge = ()
    if full_down_corridor(throttle_start):
        bridge = (((8, RAISED), (9, 5589), (10, 9511)),
                  ((8, RAISED), (9, 8400), (10, 2048)))
    if kind == 'startup':
        throttle = bridge + (TUCK, ((8, 5260), (9, 8000), (10, 3800)),
                             ((8, 4616), (9, 8720), (10, 5920)), REST)
        lanes = wake_lanes(current, first_look)
        if 'throttle' not in keep:
            lanes += (tuple(Beat(p, throttle_duration=1.25) for p in throttle),)
        if 'poker' not in keep and any(abs(current[ch] - value) > 2 for ch, value in POKER_UP):
            lanes += (poker_lane(current, dict(POKER_UP), speed=12, acceleration=3),)
        if 'throttle' not in keep:
            lanes += hand_home(current)
        lanes += hand_home(current, park=TIPS_PARK)
    else:
        # V2's coordinated head/visor/elbow droop, alongside tuck then park. Decorative
        # channels 5–7 keep their current pose; the poker elbow/wrist return to their park.
        lanes = ((Beat(HEAD_PARK, speed=12, acceleration=3, dwell=.8),),)
        stages = throttle_park_stages(current)
        if stages is not None:
            lanes += (tuple(Beat(tuple(stage.items()), throttle_duration=1.5) for stage in stages),)
        if any(abs(current[ch] - value) > 2 for ch, value in POKER_PARK):
            lanes += (poker_lane(current, dict(POKER_PARK), speed=12, acceleration=3),)
        home = dict(UNPOWERED_REST)[6]
        if arms_home and abs(current[6] - home) > 2:
            lanes += ((Beat(((6, home),), speed=12, acceleration=3),),)
        lanes += hand_home(current) + hand_home(current, park=TIPS_PARK)
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
    if beat.profiles:
        return {ch: (speed, acceleration) for ch, speed, acceleration in beat.profiles}
    if beat.throttle_duration is None:
        return {ch: (beat.speed, beat.acceleration) for ch in target}
    # Retain V2 shoulder/elbow pacing. Halve throttle-wrist pacing to reduce
    # mechanical noise across idle, speech, requested poses and lifecycle moves.
    # Scale both caps and proportional travel, including acceleration.
    pace = {8: 1.375, 9: 1.76, 10: 1.1}
    # Owner, October 21: the throttle's joints are stiff, and the slow idle pace (caps 14/32/36,
    # 3/6/7) let them slip and creak; idle now uses the talking pace, and no moving joint goes
    # slower than THROTTLE_MIN_SPEED (the clearance check allows independent joint progress).
    speed = {ch: round(cap * pace[ch]) for ch, cap in zip(THROTTLE, (26,60,65))}
    accel = {ch: round(cap * pace[ch]) for ch, cap in zip(THROTTLE, (5,10,12))}
    distance = {ch: abs(target[ch] - current[ch]) for ch in THROTTLE if target[ch] != current[ch]}
    def gentle(profiles):
        if beat.wrist_speed:
            # Owner, October 21: the wrist was too fast. Only it goes gentler (the shoulder and
            # elbow keep their pace) and may arrive after them; the clearance rule allows
            # independent joint progress.
            profiles[10] = (min(profiles[10][0], beat.wrist_speed), min(profiles[10][1], THROTTLE_WRIST_ACCEL))
        return profiles
    if not distance:
        return gentle({ch: (speed[ch], accel[ch]) for ch in THROTTLE})
    scale = max(beat.throttle_duration * 100,
                max(distance[ch] * pace[ch] / speed[ch] for ch in distance))
    ramp = max(distance[ch] * pace[ch] / accel[ch] for ch in distance)
    result = {ch: (max(THROTTLE_MIN_SPEED, min(speed[ch], round(distance[ch] * pace[ch] / scale))),
                 max(1, min(accel[ch], round(distance[ch] * pace[ch] / ramp))))
            for ch in distance}
    return gentle({ch: result.get(ch, (speed[ch], accel[ch])) for ch in THROTTLE})


TALK_HEAD_SECONDS = 6.     # Head moves planned per talking cycle, so a repeat is rare.
# The poker arm's sway while he speaks, against its idle sway. October 21 (later): at 0.6 of
# the idle timing and full size it moved too much; now a small, unhurried sway with pauses.
TALK_TAIL_PACE = 1.
TALK_TAIL_REACH = .35
TALK_TAIL_PAUSE = (.4, 1.2)
THROTTLE_TALK = (((8, 4404), (9, 8560), (10, 7920)), ((8, 4828), (9, 7800), (10, 3800)))
# Owner, October 21: held half-raised for long, the big throttle arm looks like someone
# straining to hold it up. Between bouts of movement it rests lower: the halfway-down and
# tucked waypoints of the startup/park route (fully parked would block talking's route).
# Later that night: the shoulder relaxes fully (its lowest), the elbow stays partway as it was;
# fully down the wrist must be folded (clearance), and the tuck is the way in and out.
THROTTLE_RELAXED = (((8, SHOULDER_DOWN), (9, 8400), (10, 2048)), ((8, SHOULDER_DOWN), (9, 8000), (10, 2048)))
THROTTLE_MIN_SPEED = 4      # No throttle joint creeps slower than this (board units).
THROTTLE_WRIST_SPEED, THROTTLE_WRIST_ACCEL = 12, 5   # The gentler wrist while talking and idle.
TALK_NECK_DEG = (3., 7.)    # Emphasis either side of the addressee (V2's 260 pulses barely read).


def talking_visor(rng) -> tuple[Beat, ...]:
    """The visor's lane of talking motion; alone while the wheels run (the drive stance holds the rest).
    V2's speech floor permits visible expression instead of pinning the visor at neutral. Keep it
    in the upper part of calibrated travel while speaking."""
    return tuple(Beat(((3,target),),speed=45,acceleration=8,dwell=.15,wait=wait)
                 for target, wait in zip((5612,6132,5492,5932), (rng.uniform(.1, .3), 0., 0., 0.)))


# Pride mode talks with its hands (V2's PRIDE_SPEECH_*, ported October 25): "voice going full camp
# while the body kept its small polite gestures read as half a costume". The hero elbow and hand
# swing about twice as wide and twice as often, and the throttle wrist holds V2's downward curl
# (inside the clearance box where the shoulder is raised).
FLAIR_ARM = (((4, 7380), (5, LIMITS[5][1])), ((4, 6450), (5, 3300)))   # The wrist's top, 2100 us (was 2175).
FLAIR_SWING_SECONDS = (.45, .6)
FLAIR_THROTTLE_WRIST = 2254 * 4


def talking_lanes(current: dict[int, int], head_origin: dict[int, int] | None = None, held=frozenset(),
                  neck: int | None = None, pitch: float | None = None, rng=None,
                  flair: bool = False) -> tuple[tuple[Beat, ...], ...]:
    """Speech lanes around stable head anchors, not interrupted poses.

    pitch is where the gaze had the head pitched when the reply began (looking down at a
    seated face): the sway centres there instead of snapping back to level for each line.

    Owner, October 21: speaking motion was rapid and robotic next to the eased idle head, and
    jarring as it began. The head now moves as one (neck, lift, nod and cock on one smooth
    curve, servo by servo arriving together), somewhere different each time, livelier than
    idle; the hero elbow and wrist move together on a curve; the visor, arm and throttle
    start a moment after the head instead of all at once. Channel 7 (microphone) stays fixed.
    flair: pride mode's bigger, quicker hero arm and curled throttle wrist.
    """
    rng = rng or random.Random()
    targets = ()
    talk = (tuple(tuple((ch, FLAIR_THROTTLE_WRIST if ch == 10 else value) for ch, value in pose) for pose in THROTTLE_TALK)
            if flair else THROTTLE_TALK)
    if not set(THROTTLE).intersection(held):
        try:   # From the lowered rest the arm rises through the tuck first.
            targets = tuple(tuple(stage.items()) for stage in throttle_route(current, dict(talk[0]))[:-1]) + talk
        except ValueError:
            raise ValueError('TalkingRouteOutsideClearance')
        if not throttle_clear(dict(talk[0]), dict(talk[1])):
            raise ValueError('TalkingRouteOutsideClearance')
    origin = dict(current if head_origin is None else head_origin)
    if neck is not None:
        origin[0] = max(LIMITS[0][0], min(LIMITS[0][1], int(neck)))
    pitch0, tilt0 = head_pose(origin)
    if pitch is not None:
        pitch0 = _clamp(float(pitch), -.9, .9)
    def offset(ch, amount):
        return max(LIMITS[ch][0], min(LIMITS[ch][1], round(origin[ch]+amount)))
    # Emphasis to alternate sides of the addressee, now and then back to them, with a small
    # nod and a slight cock toward the side; offsets always from the anchor, never ratcheting.
    home = ((0, origin[0]), (1, origin[1]), *head_pulses(pitch0, tilt0).items())
    at, side, head, planned = {ch: current[ch] for ch in HEAD}, rng.choice((-1, 1)), [], 0.
    while planned < TALK_HEAD_SECONDS:
        if head and rng.random() < .25:
            aim = home
        else:
            side = -side if not head or rng.random() < .75 else side
            aim = ((0, offset(0, side*rng.uniform(*TALK_NECK_DEG)*NECK_PER_DEG)), (1, offset(1, side*rng.randint(60, 160))),
                   *head_pulses(pitch0 + rng.uniform(-.06, .06), tilt0 + side*rng.uniform(.03, .1)).items())
        beat = eased_head_beat(at, aim, dwell=rng.uniform(.1, .35), pace=TALK_EASE)
        head.append(beat)
        at.update(aim)
        planned += beat.seconds + beat.dwell
    head.append(eased_head_beat(at, home, dwell=.15, pace=TALK_EASE))
    visor = talking_visor(rng)
    # V2 neutral-expression elbow/wrist range, channels 4/5, not channel 7 (the
    # microphone-bearing hero-arm axis). The arm moves as a whole, as in idle.
    out, back = ((4,7100),(5,7600)), ((4,6740),(5,4300))   # Wrist 1900 / 1075 us (November 24; was 1780 / 1220).
    arm = (replace(eased_beat(dict(back), out, rng.uniform(.9, 1.2), rng.uniform(.25, .4)), wait=rng.uniform(.2, .5)),
           eased_beat(dict(out), back, rng.uniform(.9, 1.2), rng.uniform(.25, .4)))
    if flair:
        out, back = FLAIR_ARM
        arm = tuple(eased_beat(dict(start), end, rng.uniform(*FLAIR_SWING_SECONDS), rng.uniform(.05, .2))
                    for start, end in ((back, out), (out, back)) * 2)
        arm = (replace(arm[0], wait=rng.uniform(.1, .3)), *arm[1:])
    # Short pauses (were 1 s): the arm is rarely standing still when a line ends.
    throttle = tuple(Beat(p,dwell=rng.uniform(.2, .5),throttle_duration=1.7,wrist_speed=THROTTLE_WRIST_SPEED) for p in targets)
    if throttle:
        throttle = (replace(throttle[0], wait=rng.uniform(.3, .6)), *throttle[1:])
    # Owner, October 21: the poker arm did nothing while he spoke. Its cat's-tail sway, quicker.
    poker = ()
    if not set((6, *POKER)).intersection(held):
        reached = {ch: current[ch] for ch in (6, *POKER)}
        sway = lambda at: poker_tail(rng, at, pace=TALK_TAIL_PACE, reach=TALK_TAIL_REACH)
        first = sway(reached)
        if first is None:   # Outside the sway (after a drive or a pose): the staged route back first.
            route = poker_lane(current, {ch: POKER_TAIL[ch][0] for ch in POKER}, speed=15, acceleration=5)
            poker = (Beat(((6, POKER_TAIL[6][0]), *route[0].targets), speed=15, acceleration=5), *route[1:])
        else:
            poker = (replace(first, dwell=rng.uniform(*TALK_TAIL_PAUSE)),)
        while _lane_seconds(poker, current) < TALK_HEAD_SECONDS:
            reached.update({ch: value for beat in poker for ch, value in beat.targets})
            more = sway(reached)
            if more is None:
                break
            poker += (replace(more, dwell=rng.uniform(*TALK_TAIL_PAUSE)),)
        poker = (replace(poker[0], wait=rng.uniform(.2, .5)), *poker[1:])
    # Each wrist twists and each hand's fingers gesture with the line (November 24: in lanes of their own,
    # the fingers curling and flicking), then come back to where they began, so a lane can repeat while
    # slower lanes finish.
    hands = []
    for wrist, fingers, reach in ((14, 15, HAND_TALK_REACH), (POKER_TWIST, None, TIPS_TALK_REACH),
                                  (None, HERO_FINGERS, TIPS_TALK_REACH)):
        lead = rng.uniform(.2, .6)
        for ch in (wrist, fingers):
            if ch is None or ch in held:
                continue
            lane = (hand_moves(rng, current, reach, rng.randint(3, 4), HAND_TALK_SECONDS, HAND_TALK_DWELL, channels=(ch,))
                    if ch == wrist else finger_moves(rng, current, ch, reach, FINGER_TALK, HAND_TALK_SECONDS_PLANNED))
            reached = {**current, **{c: value for beat in lane for c, value in beat.targets}}
            lane += (eased_beat(reached, ((ch, current[ch]),), rng.uniform(*HAND_TALK_SECONDS)),)
            hands.append((replace(lane[0], wait=lead), *lane[1:]))
    lanes = (tuple(head),visor,arm,throttle,poker,*hands)
    lanes = tuple(lane for lane in lanes if lane and not set(dict(lane[0].targets)).intersection(held))
    for lane in lanes:
        for beat in lane:
            validate_pose(dict(beat.targets))
    return lanes


LOOK_NECK_DEG = 35.       # Idle glances stay inside the talking gaze range.
# Motion sizes in V2's assumed neck degrees, tuned by eye on the robot. The neck actually turns about
# 0.87 of a degree per such unit (78 degrees of travel, November 25); drive_space.NECK_PULSES_PER_DEG is
# the camera geometry.
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
    head_dwell: float = 0.
    swing: bool = False   # The hero arm's move includes its shoulder, which carries the microphone array.
    tail_settle: bool = False   # The poker arm's sway ends: it eases back to its awake pose and rests.
    throttle_rest: bool = False   # The throttle arm's bout ends: it lowers to rest (THROTTLE_RELAXED).
    throttle_run_on: tuple[tuple[int, int, int, int], ...] = ()   # (channel, target, speed, acceleration) still under way.
    energy: float = 0.   # 1 just after speech, fading to 0: livelier, quicker arm moves (idle_motion).
    listening: bool = False   # The head is cocked to show he heard: it moves on the cue's gentler curve.


# Each arm is one part: when it moves, all of its joints move a little together (owner,
# October 12: the joints had moved one at a time, which no real arm does).
# The throttle hand moves with its arm (November 19): as its own part it took phrase slots from the other arms.
IDLE_CHANNELS = {'head': HEAD, 'visor': (3,), 'hero': (4, 5, 7), 'poker': (6, *POKER),
                 'throttle': (*THROTTLE, *THROTTLE_HAND)}
THROTTLE_IDLE = (((8,5060),(9,7480),(10,4760)), ((8,4776),(9,8080),(10,5600)),
                 ((8,4880),(9,7840),(10,4920)), ((8,5100),(9,7640),(10,5360)), REST)
POKER_IDLE = {12: (5200, 6800), 13: (2900, 4700)}   # Around the awake 45-degree pose; under the ceiling.
# The poker arm's idle sway, like a cat's tail (owner, October 13): (centre, least, most
# swing either side). The centres are the awake pose (swing 6000, poker_up 6000/3768).
POKER_TAIL = {6: (6000, 300, 600), 12: (6000, 350, 550), 13: (3768, 500, 800)}
POKER_TAIL_SECONDS = (1.7, 2.3)    # The swing and elbow's half sway.
POKER_TAIL_TRAIL = (.5, .8)        # The wrist arrives this much later.

# Head expressions on the two-servo head (owner, October 10 evening: "dogs cock their heads
# left and right when they're perplexed"; use the new freedom for more expression). Each is
# a finite run of (pitch change, tilt change, dwell) from where the head is, then back to it;
# "subtle" halves the amounts. 'side' expressions cock away from any lean already there.
# 'ease' (least seconds, seconds per degree) moves each step on one smooth curve timed by its
# size, like idle and talking head moves, instead of the board's fixed speed and acceleration.
# 'visor' (pulse, speed, acceleration) and 'lift' ('to' a pulse or 'by' pulses from where it
# stands, speed, acceleration) join in on lanes of their own and hold until the head starts back.
VISOR_SQUINT = 6350   # Half shut: a narrowed, unimpressed eye (about midway between open and closed).
HEAD_EXPRESSIONS = {
    'nod': {'moves': ((-.3, 0., .25), (.1, 0., .25)), 'speed': 35},
    'tilt': {'moves': ((0., .6, .5),), 'speed': 20, 'side': True},                  # One cock and back.
    # Owner, November 21: a wink. A slight cock of the head, half the tilt's, as one eye goes dark for a
    # moment (the LED board's WINK, sent by the output owner as the move begins). The head cocks to his
    # left, the side of the eye that winks: the first one cocked right while the left eye went dark
    # (owner, the same day). Not a sided expression: it always leans toward that eye.
    'wink': {'moves': ((0., -.3, .45),), 'speed': 20},
    # The dog's double cock, looking slightly down. Owner, October 24: eased like the wiggle.
    'perplexed': {'moves': ((-.08, .6, .7), (-.08, -.6, .7)), 'ease': (.35, .03), 'side': True},
    # Playful side to side. Owner, October 24: too rapid, and it creaked (three reversals in about
    # 1.5 s on the steepest head ramp of any expression). Each swing now eases in and out: about 3 s.
    'wiggle': {'moves': ((0., .35, .15), (0., -.35, .15), (0., .35, .15)), 'ease': (.35, .03)},
    'sympathetic': {'moves': ((-.15, .45, 1.2),), 'speed': 12, 'acceleration': 3, 'side': True},   # Slow cock, slightly down, held.
    # The visor opens wide to its surprise position as the head tips up a little and the head
    # lift rises to its surprise height; all hold, then ease back (owner, October 11: visor
    # surprise is 1136 us; November 15: the lift goes to 2310 us). Owner, November 15: the visor
    # shot up way too fast (speed 70, no ramp: about 0.2 s). It now eases up in about 1 s from
    # the open neutral, a little ahead of the lift's 1.5 s.
    'surprised': {'moves': ((.15, 0., .8),), 'speed': 40, 'visor': (VISOR_SURPRISE, 20, 4),
                  'lift': ('to', HEAD_LIFT_SURPRISE, SURPRISE_LIFT_SPEED, SURPRISE_LIFT_ACCEL)},
    # Owner, November 15: "I'd like the servo animations to be more emotionally based." Four more
    # feelings, each using the whole head: the two head servos, the lift pole and the visor.
    # A laugh: the head rocks back twice, riding a little higher, each rock eased like the wiggle.
    'laugh': {'moves': ((.2, 0., .05), (-.06, 0., .05), (.2, 0., .05), (-.06, 0., .05)), 'ease': (.3, .03),
              'lift': ('by', 200, HEAD_LIFT_SPEED, HEAD_LIFT_ACCEL)},
    # Unimpressed: a slow cock to one side with the visor narrowed, held (the deadpan look).
    'skeptical': {'moves': ((-.05, .4, 1.1),), 'speed': 14, 'acceleration': 3, 'side': True, 'visor': (VISOR_SQUINT, 25, 5)},
    # Crestfallen: the head sinks, the pole drops and the visor droops, slowly, held, then he gathers himself.
    'deflated': {'moves': ((-.45, 0., 1.2),), 'speed': 12, 'acceleration': 3, 'visor': (VISOR_SQUINT, 12, 3),
                 'lift': ('by', -500, 12, 3)},
    # Pleased with himself: chin up and standing taller, held.
    'proud': {'moves': ((.3, 0., 1.),), 'speed': 14, 'acceleration': 3, 'lift': ('by', 700, HEAD_LIFT_SPEED, HEAD_LIFT_ACCEL)},
    # Owner, November 15: "if we make him mad by insulting him his eyes could turn red". A glare: the
    # head drops and the visor narrows, held, then eases back. The lights give it its red
    # (leds.FEELING_LIGHTS); the pole stays where it is. The visor eases (the same day the owner found
    # the surprise's snap far too fast), a little quicker than the skeptical look's.
    'angry': {'moves': ((-.2, 0., 1.1),), 'speed': 30, 'visor': (VISOR_SQUINT, 30, 6)},
}
GESTURE_PRIMITIVES = (*HEAD_EXPRESSIONS, 'lift', 'arm_emphasis')


def head_reframes(current, targets) -> bool:
    return any(abs(current[ch] - value) > HEAD_SMALL[ch] for ch, value in targets if ch in HEAD_SMALL)


# Owner, October 21 (after a fluid robot-head demo, Microduck's idle look-around): an idle
# head move follows one smooth curve (minimum jerk: no sudden start or stop), every head
# servo arrives at the same moment, and a bigger move takes longer. The board's own ramp
# draws each servo's curve: it speeds up at its acceleration, holds at most its speed limit
# and slows down again. Board units are whole numbers and the gentlest acceleration is
# still steep for a short, slow move, so each servo gets whichever nearby setting keeps
# its ramp closest to the shared curve.
# Owner, the same evening on the robot: fluid, but slow and hardly looking around; quicker now.
HEAD_EASE_SECONDS = .35     # Every idle head move takes at least this long...
HEAD_EASE_PER_DEG = .035    # ...plus this per degree.
TALK_EASE = (.3, .025)      # Talking moves the same way, livelier: (least seconds, per degree).
TILT_DEG = 20.              # Degrees of a full side tilt: an estimate, like the pitch's.
SPEED_UNIT = 100.           # Pulses per second for one board speed unit (0.25 us per 10 ms).
ACCEL_UNIT = 1250.          # Pulses per second squared for one board acceleration unit (per 80 ms).


def minimum_jerk(x: float) -> float:
    """Share of a move done at share x of its time; no speed or acceleration at either end."""
    x = max(0., min(1., x))
    return x ** 3 * (10 + x * (-15 + 6 * x))


def board_seconds(travel: float, speed: int, acceleration: int) -> float:
    """How long the board's ramp takes over travel pulses at this speed and acceleration."""
    v, a = speed * SPEED_UNIT, acceleration * ACCEL_UNIT
    if v * v >= a * travel:
        return 2 * math.sqrt(travel / a)    # Never reaches the speed limit: up, then straight down.
    return travel / v + v / a


def board_travel(t: float, travel: float, speed: int, acceleration: int) -> float:
    """Pulses the board's ramp has covered t seconds into a move."""
    a = acceleration * ACCEL_UNIT
    v = min(speed * SPEED_UNIT, math.sqrt(a * travel))
    ramp, total = v / a, board_seconds(travel, speed, acceleration)
    if t >= total:
        return travel
    if t <= ramp:
        return a * t * t / 2
    if t <= total - ramp:
        return v * (t - ramp / 2)
    return travel - a * (total - t) ** 2 / 2


def _head_ramp(travel: float, seconds: float) -> tuple[int, int]:
    """(speed, acceleration) whose ramp stays closest to a minimum-jerk move over seconds."""
    ideal = 4 * travel / seconds ** 2 / ACCEL_UNIT    # A ramp straight up and straight down.
    best = None
    for units in {max(1, min(255, math.floor(ideal))), max(1, min(255, math.ceil(ideal)))}:
        a = units * ACCEL_UNIT
        speeds = {math.ceil(math.sqrt(a * travel) / SPEED_UNIT)}    # No speed limit reached.
        room = (a * seconds) ** 2 - 4 * a * travel
        if room >= 0:   # Too steep: a speed limit stretches the ramp to the shared time.
            level = (a * seconds - math.sqrt(room)) / 2 / SPEED_UNIT
            speeds |= {max(1, math.floor(level)), max(1, math.ceil(level))}
        for speed in speeds:
            end = max(seconds, board_seconds(travel, speed, units))
            miss = max(abs(board_travel(end * k / 40, travel, speed, units) - travel * minimum_jerk(end * k / 40 / seconds))
                       for k in range(41))
            best = min(best or (miss, units, speed), (miss, units, speed))
    return best[2], best[1]


def head_degrees(start, targets) -> float:
    """Roughly how far a head move turns: neck, pitch and side tilt together."""
    end = {**start, **dict(targets)}
    (pitch0, tilt0), (pitch1, tilt1) = head_pose(start), head_pose(end)
    return math.hypot((end[0] - start[0]) / NECK_PER_DEG, degrees_from_pitch(pitch1) - degrees_from_pitch(pitch0),
                      (tilt1 - tilt0) * TILT_DEG)


def eased_beat(start, targets, seconds, dwell=0.) -> Beat:
    """Joints moving together on one smooth curve over seconds, arriving together."""
    targets = tuple(targets)
    profiles = tuple((ch, *(_head_ramp(abs(value - start[ch]), seconds) if abs(value - start[ch]) > 2 else (1, 1)))
                     for ch, value in targets)
    return Beat(targets, dwell=dwell, seconds=seconds, profiles=profiles)


def eased_head_beat(start, targets, dwell=0., pace=(HEAD_EASE_SECONDS, HEAD_EASE_PER_DEG)) -> Beat:
    """One head move on a shared smooth curve, timed by its size."""
    targets = tuple(targets)
    seconds = pace[0] + pace[1] * head_degrees(start, targets)
    return replace(eased_beat(start, targets, seconds, dwell), reframes=head_reframes(start, targets))


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
    if target not in {'left','right','up','down','forward','unfocused'}:
        raise ValueError('DirectionalAttentionOnly')
    if intensity not in {'subtle','normal'} or hold_seconds not in (.5, 1.5):
        raise ValueError('InvalidExpression')
    clamp = lambda ch, value: max(LIMITS[ch][0], min(LIMITS[ch][1], round(value)))
    extra = ()
    if kind == 'attention':
        targets = {'left':((0,NECK_CENTER-1040),), 'right':((0,NECK_CENTER+1040),),
                   'up':tuple(head_targets(HEAD_POSES['look_up'], current).items()),
                   'down':tuple(head_targets(HEAD_POSES['look_down'], current).items()),
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
            rise, high = 0., None
            if 'lift' in spec:
                how, amount, lift_speed, lift_accel = spec['lift']
                high = clamp(1, current[1] + (amount - current[1] if how == 'to' else amount) * scale)
                if len(spec['moves']) == 1:
                    # A held pose: the lift pole takes longer than the visor and the head pair, which
                    # wait for it, so everything is in place together and settles back together.
                    rise = board_seconds(abs(high - current[1]), lift_speed, lift_accel)
            pitch0, tilt0 = head_pose(current)
            side = (-1. if tilt0 > .2 else 1.) if spec.get('side') else 1.
            start = tuple((ch, current[ch]) for ch in HEAD_PAIR)
            aims = [tuple(head_pulses(pitch0+dp*scale, tilt0+side*dt*scale).items()) for dp,dt,_ in spec['moves']]
            if 'ease' in spec:
                at, lane = dict(current), ()
                for aim, (_, _, dwell) in zip(aims, spec['moves']):
                    lane += (eased_head_beat(at, aim, dwell, pace=spec['ease']),)
                    at.update(aim)
                lane += (eased_head_beat(at, start, pace=spec['ease']),)
            else:
                lane = tuple(Beat(aim,speed=spec['speed'],acceleration=spec.get('acceleration',6),dwell=dwell+rise)
                             for aim,(_,_,dwell) in zip(aims, spec['moves']))
                lane += (Beat(start,speed=spec['speed'],acceleration=spec.get('acceleration',6)),)
            # The visor and the lift each move in a lane of their own, at their own pace, and stay
            # until the head starts back; subtle takes them half as far from where they are.
            held_for = _lane_seconds(lane[:-1], current)
            def beside(ch, target, speed, acceleration, back):
                there = Beat(((ch, target),), speed=speed, acceleration=acceleration)
                stay = max(.1, held_for - (board_seconds(abs(target - current[ch]), speed, acceleration)
                                           if acceleration else _lane_seconds((there,), current)))
                return (replace(there, dwell=stay), Beat(((ch, current[ch]),), speed=back[0], acceleration=back[1]))
            if 'visor' in spec:
                # Acceleration 0 moves it at once (unlimited); every way back is gentle.
                target, speed, acceleration = spec['visor']
                extra += (beside(3, round(current[3] + (target - current[3]) * scale), speed, acceleration,
                                 (min(20, speed), min(5, acceleration or 5))),)
            if high is not None:
                extra += (beside(1, high, lift_speed, lift_accel,
                                 (min(HEAD_LIFT_SPEED, lift_speed), min(HEAD_LIFT_ACCEL, lift_accel))),)
        elif primitive == PRIDE_FLOURISH:
            lane, *extra = flourish_lanes(current)
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


# Pride mode's one-shot as it comes on (V2's animations.pride_flourish, ported October 25): the head
# pitches right down while the visor opens wide and the hero hand flicks to the end of its travel, all
# held a beat, then back to where they were. Not offered to the model as a gesture of its own.
PRIDE_FLOURISH = 'pride_flourish'
FLOURISH_HOLD = 1.   # V2's hold once the pose has arrived.


def flourish_lanes(current):
    """Head, visor and hand lanes, arriving together on the talking pace (no slam on the heavy head)."""
    down = tuple(head_pulses(-1.).items())
    into = eased_head_beat(current, down, FLOURISH_HOLD, pace=TALK_EASE)
    head = (into, eased_head_beat({**current, **dict(down)}, tuple((ch, current[ch]) for ch in HEAD_PAIR), pace=TALK_EASE))
    def there_and_back(ch, value):
        return (eased_beat(current, ((ch, value),), into.seconds, FLOURISH_HOLD),
                eased_beat({ch: value}, ((ch, current[ch]),), into.seconds))
    return head, there_and_back(3, VISOR_UP), there_and_back(5, LIMITS[5][1])


def _eased(travel, seconds, acceleration):
    """A whole board speed for a move that eases in and out at `acceleration` and takes about
    `seconds`, and the travel that speed covers in exactly that time. The board's acceleration
    adds that many speed units every 80 ms; a speed unit is 100 quarter-microseconds a second."""
    if travel <= 2:
        return 1, travel
    rate = 1250. * acceleration
    room = (rate * seconds) ** 2 - 4 * rate * travel
    velocity = (rate * seconds - room ** .5) / 2 if room >= 0 else rate * seconds / 2
    velocity = 100 * max(1, round(velocity / 100))
    covered = (velocity * seconds - velocity ** 2 / rate if velocity <= rate * seconds / 2
               else rate * seconds ** 2 / 4)
    return velocity // 100, round(covered)


def poker_tail(rng, start, settle=False, pace=1., reach=1.):
    """Half of the poker arm's idle sway, like a cat's tail (owner, October 13): the swing and
    elbow cross to the other side of the awake pose while the wrist bends the opposite way,
    trailing them. Elbow and wrist share one beat, as the clearance check requires; the wrist
    picks up speed slowly and arrives a little after the elbow, so the hand follows through.
    settle: the bout of swaying is over; ease back to the awake pose itself, still trailing.
    None when the arm starts outside the sway (after a drive or a pose), which needs a route.
    pace scales its timing (idle just after speech a little quicker); reach its size (talking)."""
    side = -1 if start[12] >= POKER_TAIL[12][0] else 1
    ranges = {6: (4500, 7500), **POKER_IDLE}
    aim = {ch: centre + (0 if settle else (-side if ch == 13 else side) * round(rng.randint(least, most) * reach))
           for ch, (centre, least, most) in POKER_TAIL.items()}
    if max(start[12], aim[12]) > poker_elbow_max(max(start[13], aim[13])):
        return None
    seconds = rng.uniform(*POKER_TAIL_SECONDS) * pace
    timing = {6: (seconds, 3), 12: (seconds, 3), 13: (seconds + rng.uniform(*POKER_TAIL_TRAIL) * pace, 1)}
    targets, profiles = [], []
    for ch, (time, acceleration) in timing.items():
        speed, covered = _eased(abs(aim[ch] - start[ch]), time, acceleration)
        way = 1 if aim[ch] >= start[ch] else -1
        targets.append((ch, aim[ch] if settle else max(ranges[ch][0], min(ranges[ch][1], start[ch] + way * covered))))
        profiles.append((ch, speed, acceleration))
    return Beat(tuple(targets), speed=max(s for _, s, _ in profiles), profiles=tuple(profiles))


def _lane_seconds(lane, current):
    """About how long a lane takes: travel at each beat's speed, its waits and dwells."""
    position, total = dict(current), 0.
    for beat in lane:
        total += beat.wait + beat.dwell
        if beat.throttle_duration:
            total += beat.throttle_duration
        elif beat.seconds:
            total += beat.seconds
        elif beat.profiles:
            total += max(abs(dict(beat.targets)[ch] - position[ch]) / (100 * speed) for ch, speed, _ in beat.profiles)
        else:
            total += max((abs(value - position[ch]) / (100 * max(1, beat.speed)) for ch, value in beat.targets), default=0.)
        position.update(beat.targets)
    return total


def idle_lanes(current: dict[int, int], rng, held=frozenset(), phrase: IdlePhrase | None = None) -> tuple[tuple[Beat, ...], ...]:
    """Slow, measured idle movement for the parts one phrase names (owner, October 3:
    "always some motion going on, slow and measured", never every servo at once).

    Each part goes somewhere new at its own pace rather than back and forth, between the
    old calm idle and talking (October 12: idle had read as frozen). Ranges stay inside what talking and V2 idle
    already use; the visor stays in its speaking range, above the camera. An arm moves as
    a whole: every joint a little, together. The hero arm carries the speaker array, so its
    shoulder swings rarely, less far, slowly, and returns to centre."""
    if phrase is None:
        phrase = IdlePhrase(tuple((part, 0.) for part in IDLE_CHANNELS), swing=True)
    clamp = lambda ch, value: max(LIMITS[ch][0], min(LIMITS[ch][1], round(value)))
    quicken = lambda by: 1 - by * phrase.energy   # Just after speech the arms still move near its pace.
    lanes, head_at, extra = [], None, []
    for part, wait in phrase.parts:
        channels = IDLE_CHANNELS[part]
        if set(channels).intersection(held):
            if part == 'throttle' and not set(THROTTLE_HAND).intersection(held):
                # The arm is held (kept in, parked for a drive); its hand still twists and flexes.
                extra += hand_idle_lanes(rng, current, 14, 15, HAND_IDLE_REACH, 0., wait, quicken(.4), held)
            continue
        if part == 'head':
            head_at = len(lanes)
            def drift(start):
                # A small wander from where the head is, inside the camera's settled view.
                pitch, tilt = head_pose(start)
                moved = ((0, clamp(0, start[0] + rng.randint(-380, 380))),
                         (1, clamp(1, start[1] + rng.randint(-110, 110))),
                         *head_pulses(pitch + rng.uniform(-.05, .05), tilt + rng.uniform(-.08, .08)).items())
                return eased_head_beat(start, moved)
            if phrase.head:
                targets = tuple((ch, clamp(ch, value)) for ch, value in phrase.head)
                lane = (eased_head_beat(current, targets, dwell=phrase.head_dwell,
                                        **({'pace': LISTEN_EASE} if phrase.listening else {})),)
            else:
                lane = (drift(current),)
        elif part == 'visor':
            first = _elsewhere(rng, *VISOR_IDLE, current[3], 200, 600)
            lane = (Beat(((3, first),), speed=rng.randint(8, 14) + round(10 * phrase.energy), acceleration=3),)
            if rng.random() < .25:   # A slow half-blink: down to the speaking floor and open again.
                lane = (Beat(((3, VISOR_SPEECH[1]),), speed=rng.randint(8, 14), acceleration=3, dwell=rng.uniform(.2, .6)),
                        Beat(((3, rng.randint(VISOR_OPEN - 300, VISOR_OPEN + 50)),), speed=rng.randint(6, 10), acceleration=2))
        elif part == 'hero':
            # Elbow and wrist always move together, a little each, starting and arriving
            # together. The shoulder carries the microphone array, and each of its moves
            # pauses voice learning, so it joins only when the scheduler says (swing): then
            # the whole arm reaches out, pauses and comes back with the array at centre.
            ranges = {4: (6500, 7200), 5: (4000, 7900)}   # Wrist 1000-1975 us (November 24; was 1150-1850).
            def bend(start, least):
                return ((4, _elsewhere(rng, *ranges[4], start[4], least, 550)),
                        (5, _elsewhere(rng, *ranges[5], start[5], 3 * least, 2200)))
            def beat(start, targets, seconds, dwell=0.):
                # October 21: on the head's smooth curve, every joint easing in and out together.
                return eased_beat(start, targets, seconds, dwell)
            if phrase.swing:
                side = (-1 if current[7] >= 5984 + 500 else 1 if current[7] <= 5984 - 500 else rng.choice((-1, 1)))
                out = beat(current, ((7, clamp(7, 5984 + side * rng.randint(700, 1400))), *bend(current, 150)),
                           rng.uniform(2.2, 3.2), dwell=rng.uniform(.8, 1.6))
                reached = {**current, **dict(out.targets)}
                lane = (out, beat(reached, ((7, 5984), *bend(reached, 100)), rng.uniform(2.2, 3.2)))
            else:
                first = beat(current, bend(current, 200), rng.uniform(1.1, 2.) * quicken(.4))
                lane = (first,)
                if rng.random() < .3:    # Roll a little past, pause, ease back: both joints again.
                    reached = {**current, **dict(first.targets)}
                    ease = tuple((ch, clamp(ch, reached[ch] + (-1 if reached[ch] > current[ch] else 1) * rng.randint(*by)))
                                 for ch, by in ((4, (60, 150)), (5, (300, 650))))
                    lane = (replace(first, dwell=rng.uniform(.3, .9)), beat(reached, ease, rng.uniform(.9, 1.5) * quicken(.4)))
            tip = HERO_FINGERS
        elif part == 'poker':
            # During a bout of swaying the scheduler moves it in every phrase: half a tail sway
            # each time, then a last move back to the awake pose before it rests. Starting
            # outside the sway (after a drive or a pose), the elbow and wrist take their staged
            # route back to the awake pose first, the first stage riding with the swing.
            tail = poker_tail(rng, current, settle=phrase.tail_settle, pace=quicken(.35))
            if tail is not None:
                lane = (tail,)
            else:
                route = poker_lane(current, {ch: POKER_TAIL[ch][0] for ch in POKER}, speed=rng.randint(7, 15), acceleration=3)
                lane = (Beat(((6, POKER_TAIL[6][0]), *route[0].targets), speed=12, acceleration=3), *route[1:])
            tip = POKER_TWIST
        elif part == 'throttle':
            start, lane = current, ()
            if phrase.throttle_run_on:
                # Just after a line: finish the talking move at its own pace, then straight on.
                lane = (Beat(tuple((ch, value) for ch, value, _, _ in phrase.throttle_run_on),
                             profiles=tuple((ch, speed, acceleration) for ch, _, speed, acceleration in phrase.throttle_run_on)),)
                start = {**current, **{ch: value for ch, value, _, _ in phrase.throttle_run_on}}
            poses = THROTTLE_RELAXED if phrase.throttle_rest else THROTTLE_IDLE + THROTTLE_TALK
            target = rng.choice([p for p in poses if any(abs(start[ch]-v) > 2 for ch, v in p)])
            try:
                stages = throttle_route(start, dict(target))
            except ValueError:
                raise ValueError('IdleRouteOutsideClearance')
            # Firmer than the old 2-3.2 s glide, which let the stiff joints slip (October 21).
            lane += tuple(Beat(tuple(stage.items()), throttle_duration=rng.uniform(1.3, 2.) * quicken(.3),
                               wrist_speed=THROTTLE_WRIST_SPEED) for stage in stages)
            # Its hand moves with it in a lane of its own (added after the parts' lanes): a small
            # twist and flex of the fingers together, or back to the middle as the arm lowers to rest.
            if phrase.throttle_rest:
                hand = (eased_beat(current, HAND_PARK, rng.uniform(*HAND_IDLE_SECONDS)),)
                extra.append((replace(hand[0], wait=wait + rng.uniform(0., .4)), *hand[1:]))
            else:   # Twisting and gesturing for about as long as the arm moves.
                extra += hand_idle_lanes(rng, current, 14, 15, HAND_IDLE_REACH, max(0., _lane_seconds(lane, current) - 1.),
                                         wait, quicken(.4), held)
        else:
            raise ValueError('UnknownIdlePart')
        if part in ('hero', 'poker'):
            # Its hand moves with it in a lane of its own, for about as long as the arm does: the hero
            # fingers gesture, the poker hand twists.
            until = max(0., _lane_seconds(lane, current) - 1.)
            extra += hand_idle_lanes(rng, current, *((None, tip) if tip == HERO_FINGERS else (tip, None)),
                                     TIPS_IDLE_REACH, until, wait, quicken(.4), held)
        if wait > 0:
            lane = (replace(lane[0], wait=wait), *lane[1:])
        for beat in lane:
            validate_pose({**{ch: current[ch] for ch in THROTTLE}, **dict(beat.targets)}
                          if beat.throttle_duration else dict(beat.targets))
        lanes.append(tuple(lane))
    tail = next((i for i, (part, _) in enumerate(p for p in phrase.parts if not set(IDLE_CHANNELS[p[0]]).intersection(held))
                 if part == 'poker'), None)
    if tail is not None and not phrase.tail_settle:
        # A cat's tail does not stop while the rest of him moves: when another part takes
        # longer (a throttle move, the hero arm's reach), the tail keeps swaying until it ends.
        longest = max((_lane_seconds(lane, current) for i, lane in enumerate(lanes) if i != tail), default=0.)
        lane = lanes[tail]
        for _ in range(4):
            if _lane_seconds(lane, current) + 1. > longest:
                break
            reached = {**current, **{ch: value for beat in lane for ch, value in beat.targets}}
            more = poker_tail(rng, reached, pace=quicken(.35))
            if more is None:
                break
            validate_pose(dict(more.targets))
            lane += (more,)
        lanes[tail] = lane
    if head_at is not None and len(lanes) > 1:
        # Owner, October 12: the head stopped and waited while an arm finished, which read as
        # frozen; October 21: still too little motion. It keeps wandering until the rest ends.
        longest = max(_lane_seconds(lane, current) for i, lane in enumerate(lanes) if i != head_at)
        lane = lanes[head_at]
        for _ in range(8):   # Never past the rest of the phrase: that would hold up the other parts.
            more = drift({**current, **{ch: value for beat in lane for ch, value in beat.targets}})
            if _lane_seconds(lane + (more,), current) > longest:
                break
            lane += (more,)
        lanes[head_at] = lane
    for lane in extra:
        for beat in lane:
            validate_pose(dict(beat.targets))
    return tuple(lanes) + tuple(extra)


def base_lanes(kind: str, current: dict[int, int], keep=frozenset(), hero=True) -> tuple[tuple[Beat, ...], ...]:
    """Throttle park, centered neck, raised hero elbow/poker arm and the poker elbow/wrist
    drive pose before base motion; afterwards the throttle's return to rest beside the
    hero elbow, poker arm, elbow and wrist going back to their awake poses.

    keep names the REACHING_ARMS that stay at their drive stance on release (an obstacle
    within reach); the hero elbow returns unless hero is False (a kept arm coming out later,
    when the elbow is idle motion's again).

    V2 parks the throttle arm before every non-reverse base command. The neck is
    centered so head-camera bearings are base-relative. Return uses the validated
    startup route from PARK. Routes are checked before any lane moves.
    """
    if kind not in {'base_park', 'base_release'}:
        raise ValueError('UnknownBaseSequence')
    if not set(keep) <= set(REACHING_ARMS) or (keep and kind != 'base_release'):
        raise ValueError('UnknownKeptArm')
    validate_pose(current)
    start = {ch: current[ch] for ch in THROTTLE}
    at = lambda pose: all(abs(start[ch] - value) <= 2 for ch, value in pose)
    if kind == 'base_park':
        # November 19 (run a5963bfa): this park had one fallback route, so from where the arm sat
        # every drive was refused before a wheel moved. It now tries the shutdown park's routes;
        # with none clear the throttle stays where it is and the drive goes ahead with the swing
        # check counting that arm as out (drive owner, throttle_parks).
        stages = [] if at(PARK) else (throttle_park_stages(current) or [])
    else:
        route = [dict(TUCK), {8: 5260, 9: 8000, 10: 3800}, {8: 4616, 9: 8720, 10: 5920}, dict(REST)]
        # From anywhere else (a throttle the wake found out, lowered by a pose) by the checked waypoints;
        # one the park left where it was, with no clear way to rest, stays there.
        if at(REST) or 'throttle' in keep:
            stages = []
        elif at(PARK) or throttle_clear(start, route[0]):
            stages = route
        else:
            try:
                stages = throttle_route(start, REST)
            except ValueError:
                stages = []
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
        if set(THROTTLE_HAND).issubset(current):
            lanes += hand_home(current, speed=20, acceleration=5)
    else:
        # The other arms the stance moved come back beside the throttle's walk: the hero
        # elbow and the poker arm's swing to their awake mid points, the poker elbow and
        # wrist to the awake 45-degree pose by the staged clearance route.
        kept = {ch for arm in keep for ch in REACHING_ARMS[arm]}
        arms = tuple((ch, value) for ch, value in AWAKE_ARMS
                     if ch in current and ch not in kept and (hero or ch != 4) and abs(current[ch] - value) > 2)
        if arms:
            lanes += ((Beat(arms, speed=20, acceleration=5),),)
        if ('poker' not in keep and set(POKER).issubset(current)
                and any(abs(current[ch] - value) > 2 for ch, value in POKER_UP)):
            lanes += (poker_lane(current, dict(POKER_UP), speed=12, acceleration=3),)
    return lanes


# Explicit look requests (September 28). The "33-degree" poses below turn the head about 28 degrees
# (drive_space.NECK_PULSES_PER_DEG, November 25); their pulses are unchanged. look_left mirrors look_right about the centre:
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
LOOK_SPEED = 40   # A person's look: twice the ordinary pose speed, still below the voice reflex's 60.
# look_down/look_up drop or raise the pole a little from its normal height (400 and 600 pulses).
HEAD_POSES = {
    'look_down': {'lift': HEAD_LIFT_NORMAL - 400, 'pitch': -LOOK_PITCH},
    'look_up': {'lift': HEAD_LIFT_NORMAL + 600, 'pitch': LOOK_PITCH},
    'look_level': {'lift': HEAD_LIFT_NORMAL, 'pitch': 0., 'tilt': 0.},
    'tilt_left': {'tilt': -LOOK_TILT}, 'tilt_right': {'tilt': LOOK_TILT}, 'tilt_straight': {'tilt': 0.},
}
NAMED_POSES = {
    'look_center': ((0,NECK_CENTER),),
    # The owner calls the head-lift pole the neck. October 13: "raise your neck" and "highest
    # neck pose" got look_up, a head tip on a pole raised only a little (then 4200 of its 7744).
    'neck_up': ((1,LIMITS[1][1]),), 'neck_down': ((1,LIMITS[1][0]),),
    'visor_down': ((3,VISOR_DOWN),), 'visor_up': ((3,VISOR_UP),),
    # Owner, October 22: "put it back to the way it normally goes". Its usual open point, then it moves
    # with speech and idle again (never held).
    'visor_normal': ((3,VISOR_OPEN),),
    'throttle_offer': ((8,RAISED),(9,2601),(10,5938)),
    'throttle_high_five': ((8,RAISED),(9,9361),(10,6302)),
    'throttle_down': FULL_DOWN, 'throttle_rest': REST,
    # Poker arm, owner's console readings October 10.
    'poker_out': ((12,4228),(13,5556)),      # Straight out: elbow 1057 us, wrist 1389 us.
    'poker_up': POKER_UP,                    # 45 degrees up with the hand level: 1500 / 942 us.
    'poker_down': ((12,POKER_ELBOW_DOWN),(13,POKER_WRIST_STRAIGHT)),   # Elbow down, wrist straight.
}
# One-shot gestures run and return to where they started; nothing is held afterwards. The
# head expressions (not the plain one-cock 'tilt', which tilt_left/tilt_right cover as held
# looks) are also steps people can ask for and Rex can plan.
GESTURES = ('wave', 'arm_dance', 'finger_wiggle', 'nod', 'wink', 'perplexed', 'wiggle', 'sympathetic', 'surprised', 'laugh', 'skeptical',
            'deflated', 'proud', 'angry')
# Owner request October 3: the model chooses between following faces and looking where it
# decides. look_at_people resumes following faces; any look_* step holds that direction.
GAZE = ('look_at_people',)
# hold_arms moves nothing: every arm stays where it is until a person says it may move.
# The array arm (7) is left to the microphone lease, which only ever rests it at its centre;
# idle movement stops swinging it while the arms are held (idle_motion).
ARM_HOLD = (4, 5, 6, *POKER, *THROTTLE, *THROTTLE_HAND, POKER_TWIST, HERO_FINGERS)
# Holding something light, like a card (owner, November 20). The fingers close at the lowest pulse
# the Maestro allows and open at the highest (the owner: closed is the lowest value). hand_take reaches
# the throttle arm to the owner's grab position (shoulder 1136, elbow 1554, wrist 743 us since November 21; it needs the
# front right clear) with the hand level and the fingers open; RobotServos also looks down and to the right and
# closes the fingers three seconds after the arm is out (hand_hold.CLOSE_AFTER_S). hand_grip closes them now;
# hand_let_go opens them, and the arm goes back to rest a moment later; hand_put_down lowers it to the floor first.
FINGERS_CLOSED, FINGERS_OPEN = LIMITS[15][0], LIMITS[15][1]
THROTTLE_GRAB = ((8, 4544), (9, 6216), (10, 2972))   # Owner, November 21: 1136 / 1554 / 743 us (was 1242 / 1720 / 887).
FINGER_SPEED, FINGER_ACCEL = 80, 0   # The whole travel in about a quarter second (0: no acceleration limit).
HAND_POSES = {
    'hand_take': (*THROTTLE_GRAB, (14, dict(HAND_PARK)[14]), (15, FINGERS_OPEN)),
    'hand_grip': ((15, FINGERS_CLOSED),),
    'hand_let_go': ((15, FINGERS_OPEN),),
    # Put it on the floor: RobotServos lowers the arm here (throttle_down), then opens the fingers.
    'hand_put_down': FULL_DOWN,
}
HAND_STEPS = tuple(HAND_POSES)
# Dancing (owner, November 25; dance.py): 'dance' starts a dance of dance.DANCE_SECONDS that carries on
# beside the conversation; 'stop_dancing' ends it, and stops dancing to the song now playing.
DANCE_STEPS = ('dance', 'stop_dancing')
POSE_COMMANDS = (*NAMED_POSES, *NECK_POSES, *HEAD_POSES, *GESTURES, *GAZE, 'hold_arms', 'release_poses', *HAND_STEPS,
                 *DANCE_STEPS)


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


# Owner, November 20: a fully extended throttle needs 0.5 m clear at the front right. Every awake,
# idle and talking pose keeps the elbow folded at 7480 or more; the offer opens it to 2601.
# Lowered fully, the shoulder points the arm at the floor, not ahead.
THROTTLE_EXTENDED_ELBOW = 6000


def throttle_extends(name, current) -> bool:
    """Whether this pose request ends with the throttle arm reaching out ahead."""
    pose = dict(name) if isinstance(name, dict) else dict(NAMED_POSES.get(name, HAND_POSES.get(name, ())))
    if not set(pose).intersection(THROTTLE):
        return False
    arm = {**{ch: current.get(ch) for ch in THROTTLE}, **{ch: v for ch, v in pose.items() if ch in THROTTLE}}
    return (arm[9] is not None and arm[9] < THROTTLE_EXTENDED_ELBOW
            and (arm[8] is None or arm[8] < SHOULDER_DOWN - 600))


def pose_channels(name) -> tuple[int, ...]:
    """The channels a named pose or pose dict commands (gestures and gaze steps: none)."""
    if isinstance(name, dict):
        return tuple(name)
    if name in HEAD_POSES:
        return ((1,) if 'lift' in HEAD_POSES[name] else ()) + HEAD_PAIR
    if name in NECK_POSES:
        return (0,)
    return tuple(ch for ch, _ in NAMED_POSES.get(name, HAND_POSES.get(name, ())))


# Owner, October 22: once a person has shut his visor and this long has passed with no newer pose
# asked of him, Rex may raise it himself ("I kept it down for you, but I really wanted to see again").
VISOR_RAISE_SECONDS = 15.


def visor_over_eyes(commanded) -> bool:
    """The visor is shut over the camera, his eyes. Owner, October 22: closing it "effectively blinds
    him". Where it starts to cover the camera is not measured, so only (nearly) fully down counts."""
    return commanded.get(3, VISOR_OPEN) >= VISOR_DOWN - 100


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
    lane = [Beat(((7,4800),(4,6300),(5,HERO_WRIST_MID)), speed=55, acceleration=14)]
    for _ in range(4):
        lane += [Beat(((5,LIMITS[5][1]),), speed=swing, acceleration=0),
                 Beat(((5,LIMITS[5][0]),), speed=swing, acceleration=0)]
    lane += [Beat(((5,HERO_WRIST_MID),), speed=swing, acceleration=0, dwell=.16),
             Beat(start, speed=40, acceleration=8)]
    return (tuple(lane),)


# Owner, October 31 (f6d5a0b7): "Can you do a little dance with your arms?" got the head's wiggle, and
# asked again he chained throttle_offer, which reaches forward and down: the hand ran into something on
# the floor, and "put your arm back up" chose it again and pushed further down. The dance moves all three
# arms to a beat through poses talking and idle already use: the hero hand swings wide (pride mode's
# flair), the poker arm sways high and low, and the throttle hand pumps between the raised high five and
# a talking pose, never lower. Each arm then goes back to where it was; nothing is held, and the
# microphone arm (7) stays still.
DANCE_BEATS = 4
DANCE_SWING_SECONDS = .5
DANCE_THROTTLE = (NAMED_POSES['throttle_high_five'], THROTTLE_IDLE[1])
DANCE_POKER = ({6: 6600, 12: 6800, 13: 3000}, {6: 5400, 12: 5600, 13: 4500})   # The hand flicks against the elbow.
DANCE_POKER_SECONDS = .7


def _poker_stages(start, target):
    """Clear poker elbow/wrist stages from start to target, the swing (6) moving with the first."""
    stages = [dict(stage) for stage in poker_route(start, target)]
    stages[0][6] = target[6]
    return stages


def arm_dance_lanes(current):
    """A few seconds of dancing arms, then each arm back where it started."""
    hero_start = tuple((ch, current[ch]) for ch in (4, 5))
    at, hero = dict(current), []
    for i in range(DANCE_BEATS * 2 - 1):
        aim = FLAIR_ARM[i % 2]
        hero.append(eased_beat(at, aim, DANCE_SWING_SECONDS))
        at.update(aim)
    hero.append(eased_beat(at, hero_start, DANCE_SWING_SECONDS))
    throttle_start = {ch: current[ch] for ch in THROTTLE}
    stages = throttle_route(current, dict(DANCE_THROTTLE[0]))
    stages += [dict(DANCE_THROTTLE[i % 2]) for i in range(1, DANCE_BEATS + 1)]
    stages += throttle_route(stages[-1], throttle_start)
    previous = throttle_start
    for stage in stages:
        if not throttle_clear(previous, stage):
            raise ValueError('ThrottleRouteOutsideClearance')
        previous = stage
    throttle = tuple(Beat(tuple(stage.items()), throttle_duration=.8, dwell=.05) for stage in stages)
    poker_start = {ch: current[ch] for ch in (6, *POKER)}
    stages = _poker_stages(poker_start, DANCE_POKER[0])
    stages += [dict(DANCE_POKER[i % 2]) for i in range(1, DANCE_BEATS)]
    stages += _poker_stages(stages[-1], poker_start)
    at, poker = dict(current), []
    for stage in stages:
        poker.append(eased_beat(at, tuple(stage.items()), DANCE_POKER_SECONDS))
        at.update(stage)
    lanes = (tuple(hero), throttle, tuple(poker))
    for lane in lanes:
        for beat in lane:
            validate_pose(dict(beat.targets))
    return lanes


# "Wiggle your fingers" (owner, November 25): both hands' fingers open and close and the three hand joints
# that turn or bend (hero wrist, throttle wrist, poker twist) go back and forth, WIGGLES times, then each
# goes back where it was. Only hand joints move: no arm, so nothing needs clearance. A hand holding something
# keeps its fingers closed (the servo owner strips them from every request but the hand's own).
WIGGLES = 4
WIGGLE_SECONDS = .3
WIGGLE_TRAVEL = {5: 900, 14: 800, 16: 1200}   # Either side of where the joint is now, inside its limits.
WIGGLE_FINGERS = {15: (6100, 7800), 17: (5100, 6850)}   # Throttle and hero fingers: nearly open, nearly closed.


def finger_wiggle_lanes(current):
    """Fingers and wrists back and forth a few times, each lane on its own hand, then home."""
    def swing(ch, i):
        if ch in WIGGLE_FINGERS:
            return WIGGLE_FINGERS[ch][i % 2]
        low, high = LIMITS[ch]
        side = WIGGLE_TRAVEL[ch] * (1 if i % 2 == 0 else -1)
        return max(low, min(high, current[ch] + side))
    lanes = []
    for group in ((5, HERO_FINGERS), THROTTLE_HAND, (POKER_TWIST,)):
        at, lane = dict(current), []
        for i in range(WIGGLES * 2):
            aim = tuple((ch, swing(ch, i + (1 if ch in WIGGLE_FINGERS else 0))) for ch in group)
            lane.append(eased_beat(at, aim, WIGGLE_SECONDS))
            at.update(aim)
        lane.append(eased_beat(at, tuple((ch, current[ch]) for ch in group), WIGGLE_SECONDS))
        lanes.append(tuple(lane))
    for lane in lanes:
        for beat in lane:
            validate_pose(dict(beat.targets))
    return tuple(lanes)


def reflex_lanes(target):
    """Voice reflex: the neck alone, quicker than an idle glance, nothing held."""
    if set(target) != {0}: raise ValueError('ReflexMovesNeckOnly')
    validate_pose(target)
    return ((Beat(((0, target[0]),), speed=60, acceleration=15),),)


# Owner, October 11: the moment Rex has understood what was said, his head cocks to one
# side and stays there while he thinks, so people see he heard them before his answer
# (about 2.5 s later) begins. Talking sway straightens the head when the reply starts.
# October 21 (owner): it snapped over at board speed 35, the camera wobbled, and it was the
# same cock every time. It eases in and out on the head's curve and varies.
# November 24 (owner): the cock was its own request and held every other servo still, so he
# looked frozen from the moment a person spoke until he answered. It is now idle movement's
# head (IdleMotion.listen): the arms go on moving gently while the head is cocked.
LISTEN_TILT = (.2, .35)      # Fraction of side travel, different each time: up to idle's cock and a bit more.
LISTEN_DIP = (.03, .08)      # A touch down, also varied.
LISTEN_HOLD_SECONDS = 3.     # Roughly model plus voice time; a reply starting sooner takes over.
LISTEN_EASE = (.6, .05)      # Gentler than idle's head moves: about 0.8-1 s in.


# Sentry mode (owner, October 22): the arms stop moving completely, the head stays level and the neck
# sweeps slowly from stop to stop. The stance rests the throttle arm (a held-out arm strains its servo
# for hours; it swings out only at an intruder), lowers the poker arm and levels the head.
SENTRY_STANCE = MappingProxyType(dict((*REST, (12, POKER_ELBOW_DOWN), (13, POKER_WRIST_STRAIGHT),
                                       *AWAKE_ARMS, (0, NECK_CENTER), *DRIVE_HEAD, *HAND_PARK)))
SENTRY_ARM_OUT = MappingProxyType(dict(NAMED_POSES['throttle_offer']))   # The throttle arm held out at them.
SENTRY_SECONDS_PER_DEG = .2   # A 15-degree sweep step takes about 3 s: slow, one smooth curve.


def sentry_head(neck: int) -> dict[int, int]:
    """The neck at this pulse with the head level and straight."""
    return {0: max(LIMITS[0][0], min(LIMITS[0][1], int(neck))), **dict(DRIVE_HEAD)}


def sentry_lanes(target, current, seconds):
    """A sentry head move on one smooth curve over seconds (0: timed by its size)."""
    if not target or set(target) - set(HEAD):
        raise ValueError('SentryMovesHeadOnly')
    validate_pose({**{ch: current[ch] for ch in HEAD}, **target})
    targets = tuple(dict(target).items())
    seconds = seconds or max(.6, SENTRY_SECONDS_PER_DEG * head_degrees(current, targets))
    return ((replace(eased_beat(current, targets, seconds), reframes=head_reframes(current, targets)),),)


def pose_lanes(name, current, further=True):
    if isinstance(name, dict):
        target = dict(name)
        for pair in (THROTTLE, POKER):   # A partial arm request keeps the unnamed joints where they are.
            if set(target).intersection(pair):
                target = {**{ch: current[ch] for ch in pair}, **target}
        validate_pose(target)
    elif name == 'wave':
        return wave_lanes(current)
    elif name == 'arm_dance':
        return arm_dance_lanes(current)
    elif name == 'finger_wiggle':
        return finger_wiggle_lanes(current)
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
    elif name in ('hand_grip', 'hand_let_go'):
        # Owner, November 20 (run fe1d4fea): the fingers close or open briskly, not at the slow pose pace.
        return ((Beat(HAND_POSES[name], speed=FINGER_SPEED, acceleration=FINGER_ACCEL),),)
    elif name in HAND_POSES:
        target = dict(HAND_POSES[name])
    else:
        if name not in NAMED_POSES: raise ValueError('UnknownRobotPose')
        target = dict(NAMED_POSES[name])
    lanes = ()
    if set(target).intersection(THROTTLE):
        stages = throttle_route(current, {ch: target.pop(ch) for ch in THROTTLE})
        lanes += (tuple(Beat(tuple(stage.items()),throttle_duration=1.7) for stage in stages),)
    if set(POKER).issubset(target):
        lanes += (poker_lane(current, {ch: target.pop(ch) for ch in POKER}),)
    if target:
        # Owner ruling October 23: a look a person asks for is brisk. At speed 20 the head took about
        # 3 s to swing across, and the answer came 9-11 s after the request (1e626e07).
        # The neck and the head servos only: the lift pole keeps its own pace in a lane of its own.
        look = further and set(target) <= set(HEAD)
        if 1 in target:
            lanes += ((Beat(((1, target.pop(1)),), speed=HEAD_LIFT_SPEED, acceleration=HEAD_LIFT_ACCEL),),)
        if target:
            lanes += ((Beat(tuple(target.items()), speed=LOOK_SPEED if look else 20, acceleration=10 if look else 5),),)
    return lanes


# Telepresence (owner, November 16; docs/TELEPRESENCE_PLAN.md): the caller aims the head, lift and
# visor as fractions and never sees a pulse. neck -1 is fully to Rex's left (the picture's left) and
# 1 fully right; pitch -1 down to 1 up and tilt -1 cocked to his left to 1 right (head_pulses); lift 0
# lowest to 1 highest; visor 0 shut to 1 fully open (VISOR_UP, short of the surprise stop). The
# caller moves only these channels; the arms hold the drive stance all call (telepresence_wake_lanes).
TELEPRESENCE_CHANNELS = frozenset({*HEAD, 3})
# Where the head stands while the wheels turn: the drive stance's level head, neck centred.
TELEPRESENCE_DRIVE_HEAD = MappingProxyType({0: NECK_CENTER, **dict(DRIVE_HEAD)})


def telepresence_wake_lanes(current: dict[int, int]) -> tuple[tuple[Beat, ...], ...]:
    """A telepresence call's wake (owner, November 16: Rex's wake took 9 s, then the visor and the
    stance 5 s more, for a call): straight to the drive stance, all at once, with the visor open. No
    wake choreography. The head goes level and centred at its normal height and the hero elbow and
    poker arm go up (the first call's first drive waited for the elbow; owner: no delay for driving).
    Asleep, the throttle and the poker elbow and wrist are already parked; from anywhere else they
    tuck by the drive stance's routes. The stance holds until the call's shutdown parks him."""
    lanes = base_lanes('base_park', current)
    if abs(current[3] - VISOR_OPEN) > 2:
        lanes += ((Beat(((3, VISOR_OPEN),), speed=20, acceleration=5),),)
    return lanes


def _fraction(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('TelepresenceHeadValueNotANumber')
    return _clamp(float(value), low, high)


def telepresence_targets(spec, commanded) -> dict[int, int]:
    """Pulses for a caller's head request (any of neck, pitch, tilt, lift, visor); a part left out
    stays where it is. The pulses stay inside the calibrated limits."""
    targets = {}
    if 'neck' in spec:
        neck = _fraction(spec['neck'], -1., 1.)
        low, high = LIMITS[0]
        targets[0] = round(NECK_CENTER + neck * ((high - NECK_CENTER) if neck >= 0 else (NECK_CENTER - low)))
    if 'pitch' in spec or 'tilt' in spec:
        pitch, tilt = head_pose({ch: commanded[ch] for ch in HEAD_PAIR})
        targets.update(head_pulses(_fraction(spec.get('pitch', pitch), -1., 1.), _fraction(spec.get('tilt', tilt), -1., 1.)))
    if 'lift' in spec:
        low, high = LIMITS[1]
        targets[1] = round(low + _fraction(spec['lift'], 0., 1.) * (high - low))
    if 'visor' in spec:
        targets[3] = round(VISOR_DOWN + _fraction(spec['visor'], 0., 1.) * (VISOR_UP - VISOR_DOWN))
    return targets


def telepresence_reading(commanded) -> dict:
    """Where the head, lift and visor were last sent, on the caller's scale (None: not known yet)."""
    def share(value, low, high):
        return round(_clamp((value - low) / (high - low), 0., 1.), 3)
    reading = {'neck': None, 'pitch': None, 'tilt': None, 'lift': None, 'visor': None}
    if 0 in commanded:
        low, high = LIMITS[0]
        value = commanded[0]
        reading['neck'] = round((value - NECK_CENTER) / ((high - NECK_CENTER) if value >= NECK_CENTER
                                                         else (NECK_CENTER - low)), 3)
    if all(ch in commanded for ch in HEAD_PAIR):
        pitch, tilt = head_pose({ch: commanded[ch] for ch in HEAD_PAIR})
        reading['pitch'], reading['tilt'] = round(pitch, 3) + 0., round(tilt, 3) + 0.   # Never -0.0.
    if 1 in commanded:
        reading['lift'] = share(commanded[1], *LIMITS[1])
    if 3 in commanded:
        reading['visor'] = share(VISOR_DOWN - commanded[3], 0, VISOR_DOWN - VISOR_UP)
    return reading
