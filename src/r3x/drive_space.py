"""Deterministic base geometry: clearance, swing, bearings and radar bodies.

Adapted from V2 intelligence/motion_swing.py, approach.py and hardware/radar.py.
Frames follow the firmware (REP-103): +x forward, +y left, +angle left/CCW.
Nothing here is a map, localization or identity. Missing sensing stays missing.
"""
import math

# Radial ring bearings about the ring centre (V2 docs/motion_system.md §6.2).
RING = (('fl', 22.5), ('fr', -22.5), ('rl', 157.5), ('rr', -157.5),
        ('lf', 67.5), ('lb', 112.5), ('rf', -67.5), ('rb', -112.5))
AXLE_AFT = .23          # V2 estimate; spin pivots about the rear axle midpoint.
RING_RADIUS = .27
ARMS = (('front-left arm', 45., .73), ('back-left arm', 135., .73))  # V2 eyeball estimates, arms out.
# Owner, September 27: the drive stance now tucks the left arms (hero elbow up, throttle parked),
# so they no longer hang out. Estimated 6 cm beyond the ring; the long reach refused left turns
# beside any wall within ~0.9 m. Used only when the servo owner parks the arms before wheels move.
ARMS_TUCKED = (('front-left arm (tucked)', 45., .33), ('back-left arm (tucked)', 135., .33))
SWING_MARGIN = .10
SWING_PAD = 20.
MIN_TURN = 10.
# Wide-angle head camera at the robot's 1920x1080 capture. Measured September 27:
# an IMU-verified 20 degree base turn moved a face 22.5% of the frame width
# (about 44 degrees per half frame), and face range then matched the owner's
# 4-5 ft estimate. V2's 25 degrees came from a commanded turn that overshoots.
HALF_FOV = 45.
from .servo_poses import LIMITS, NECK_CENTER
# Neck pulses per degree the head actually turns. V2's figure (the owner's "sweeps about 90 degrees",
# 77.5 per degree) was never measured. October 23: in runs 1e626e07, e72ab522, 0421a222 and 1072c10a,
# a recognized face seated still moved across the picture only 0.48-0.65 (pooled 0.57) as far as a
# neck change should move it with the IMU-checked 90 degree lens above. The neck turns about 0.57 of
# what was assumed: its whole travel is about 51 degrees, and "look left" (8000/2944) about 19 degrees.
# With the old figure the voice reflex aimed at Bret's face 25 degrees short and crept toward him in
# steps. Idle and talking motion keep their own sizes (servo_poses.NECK_PER_DEG): those were tuned by eye.
NECK_PULSES_PER_DEG = (5472 - 1984) / 45. / .57
FACE_WIDTH = .16
MATRIX_CLEAR = 3.5
FRONT_MARGIN = .35      # Host stop short of a front return; firmware still stops at its zone.
# Approaching a person: knees/chair legs may be this close, never closer. Was 0.22 m until
# October 3, when he stopped 21 cm from the owner's knees: "you're a little too close to me".
# 0.32 m until October 25: approaches to Jeremy ended with feet or legs a foot away.
PERSON_MARGIN = .45
# October 5 hallway run: guarded legs stopped at 0.22 m on the 22.5 degree radial while he
# was angled a dozen degrees into a 1.2 m hallway's wall, and the firmware alone did not
# straighten him. In tight space the host now steers a slow crawl (drive.py _crawl); a
# return this near on the path means the crawl failed to clear it and must stop.
WALL_HOLD_M = .12
SCRAPE_M = .07          # A side return this close is the body about to touch the wall.
WALL_SEEN_M = .6        # Both side sensors within this: they see one wall, so its angle is known.
REAR_MARGIN = .30


def wrap(degrees: float) -> float:
    return (degrees + 180.) % 360. - 180.


def _meters(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value / 1000. if math.isfinite(value) and value >= 0 else None


ZONE_AGREE_M = .10      # A neighbouring zone within this (or 20%) corroborates a return.


def matrix_zones(matrix):
    """Returns above the floor that a neighbouring zone agrees with: zone index -> metres.

    October 10 (run b954c14c): with the person nine degrees away and every column and ring
    sensor reading open, path_m was 0.16 m from a few lone zones scattered across one
    band, and "come here" stalled after six centimetres. A real surface at any range the
    host cares about fills neighbouring zones (each is 5.6 degrees wide, under 4 cm at
    0.4 m), so a return counts only when one of its eight neighbours returns about the
    same range. rej holds the firmware's per-row floor thresholds: at/beyond it is floor.
    """
    if not matrix or len(matrix.get('grid', ())) != 64 or len(matrix.get('rej', ())) != 8:
        return None
    grid, rej = matrix['grid'], matrix['rej']
    ok = {i: value/1000. for i, value in enumerate(grid) if 25 <= value < min(rej[i//8], 3500)}
    zones = {}
    for i, value in ok.items():
        row, col = divmod(i, 8)
        for other in (r*8+c for r in (row-1, row, row+1) for c in (col-1, col, col+1)
                      if 0 <= r < 8 and 0 <= c < 8 and (r, c) != (row, col)):
            if other in ok and abs(ok[other] - value) <= max(ZONE_AGREE_M, .2*value):
                zones[i] = value
                break
    return zones


def matrix_sectors(matrix):
    """Nearest corroborated zone per left/centre/right column band.

    The firmware reduces the 8x8 grid to two fused front values; the host keeps
    three bands so one phantom half cannot hide an open corridor.
    """
    zones = matrix_zones(matrix)
    if zones is None:
        return None
    bands = {'left': range(0, 3), 'center': range(3, 5), 'right': range(5, 8)}
    return {name: min((v for i, v in zones.items() if i % 8 in columns), default=MATRIX_CLEAR)
            for name, columns in bands.items()}


BODY_HALF_WIDTH = .27   # About the sensor ring radius.
PATH_MARGIN = .05       # Lateral gap kept when driving straight past something beside the path.
# The front matrix looks level from the front of the ring across 45 degrees (V2 calib.h),
# so each of its eight columns is 5.6 degrees wide; column 0 is the robot's left.
MATRIX_COLUMN_DEG = tuple(19.6875 - 5.625*col for col in range(8))
PATH_SIDES = {'left': range(0, 3), 'center': range(3, 5), 'right': range(5, 8)}


def matrix_path(matrix):
    """Forward room inside the body's straight corridor, per left/centre/right columns.

    October 3 hallway run: the side bands counted every wall they saw, so a hallway's own
    walls shortened or refused each forward move even when he was pointed straight down it.
    A zone now counts only when its return lies inside the corridor the body sweeps
    (half-width plus margin); a wall beside the path does not. Only corroborated zones
    count (matrix_zones), so a lone speckle cannot block."""
    zones = matrix_zones(matrix)
    if zones is None:
        return None
    result = {}
    for name, columns in PATH_SIDES.items():
        ahead = [value for i, value in zones.items() if i % 8 in columns
                 and value*abs(math.sin(math.radians(MATRIX_COLUMN_DEG[i % 8]))) < BODY_HALF_WIDTH + PATH_MARGIN]
        result[name] = min(ahead) if ahead else MATRIX_CLEAR
    return result


# The rear matrix (owner, October 19) is the same module at the same height and distance
# from the centre, turned to face straight back: its column 0 is its own left, which is
# the robot's rear-right. Its grid arrives in that sensor frame.
REAR_MATRIX_COLUMN_DEG = tuple(wrap(180. + deg) for deg in MATRIX_COLUMN_DEG)


def _rear_radials(ring):
    """The radial rear pair alone; firmware 0.2.9 folds the rear matrix into rl/rr."""
    return [ring.get(f'{key}_radial') if f'{key}_radial' in ring else ring.get(key) for key in ('rl', 'rr')]


def open_directions(telemetry, matrix, now, *, rear_matrix=None, telemetry_age=.5, matrix_age=1.):
    """How far each range sensor sees, by bearing: the room around him as the sensors know it.

    Owner, October 3: the planner needs "which directions have the most open area relative
    to the camera view". The eight matrix columns cross the middle of the picture (nearest
    corroborated zone above the floor, so one speckle does not close a direction); the ring adds
    the front corners, both sides and behind. image_x is where that bearing falls in the
    head camera picture with the neck centred, None when it is outside the view. 3.5 means
    nothing within range. Missing or stale sensors are simply absent."""
    rows = []
    zones = matrix_zones(matrix) if matrix and 0 <= now - matrix['received_at'] <= matrix_age else None
    if zones is not None:
        for col in range(8):
            rows.append((MATRIX_COLUMN_DEG[col], min((v for i, v in zones.items() if i % 8 == col), default=MATRIX_CLEAR),
                         'front matrix'))
    zones = (matrix_zones(rear_matrix)
             if rear_matrix and 0 <= now - rear_matrix['received_at'] <= matrix_age else None)
    if zones is not None:
        for col in range(8):
            rows.append((REAR_MATRIX_COLUMN_DEG[col], min((v for i, v in zones.items() if i % 8 == col),
                                                          default=MATRIX_CLEAR), 'rear matrix'))
    if telemetry and 0 <= now - telemetry['received_at'] <= telemetry_age:
        tof = telemetry.get('tof_mm') or {}
        rear = tuple((f'{key}_radial' if f'{key}_radial' in tof else key, bearing) for key, bearing in RING[2:4])
        for key, bearing in (('fl_radial', 22.5), ('fr_radial', -22.5)) + rear + RING[4:]:
            value = _meters(tof.get(key))
            if value is not None:
                rows.append((bearing, min(value, 4.), 'ring'))
    return [{'bearing_deg': round(bearing, 1), 'open_m': round(value, 2), 'sensor': sensor,
             'image_x': round(.5 - bearing/(2*HALF_FOV), 2) if abs(bearing) < HALF_FOV else None}
            for bearing, value, sensor in sorted(rows, key=lambda r: -r[0])]


def _path_sides(ring, sectors, corridor):
    """Forward room along the body's straight path, by side (September 27 hallway run).

    Matrix zones count when they lie inside the corridor. A 22.5 degree front radial counts
    in full unless the matrix band on its side is clear well beyond its return: then the
    return lies outside the matrix view (at least ~22 degrees off-axis) and counts only
    when that places it inside the body's path. A hallway corner beside the robot no
    longer blocks the hallway."""
    if sectors is None or corridor is None:
        return None
    sides = dict(corridor)
    sine = math.sin(math.radians(22.5))
    for side, keys in (('left', ('fl_radial', 'fl')), ('right', ('fr_radial', 'fr'))):
        radial = next((ring[k] for k in keys if ring.get(k) is not None), None)
        if radial is None:
            continue
        beside = (sectors[side] > radial + .15 and
                  RING_RADIUS*sine + radial*sine >= BODY_HALF_WIDTH + PATH_MARGIN)
        if not beside:
            sides[side] = min(sides[side], radial)
    return sides


def clearance(telemetry, matrix, now, *, rear_matrix=None, telemetry_age=.5, matrix_age=1.):
    """Open distance by direction, with explicit unknowns and sensor health.

    rear_matrix is the backward-facing matrix's latest frame, None when it has never been
    seen (not fitted): then the rear is the radial pair alone, as before October 19. Once
    seen, a stale frame makes the rear unknown, so reversing waits rather than trusting
    two narrow beams to catch a chair leg the matrix was fitted to see."""
    health = []
    ring = {}
    if telemetry and 0 <= now - telemetry['received_at'] <= telemetry_age:
        tof = telemetry.get('tof_mm') or {}
        ring = {key: _meters(tof.get(key)) for key, _ in RING + (('fl_radial', 0), ('fr_radial', 0))}
        ring.update({key: _meters(tof.get(key)) for key in ('rl_radial', 'rr_radial') if key in tof})
    else:
        health.append('drive_telemetry_stale')
    fresh = matrix if matrix and 0 <= now - matrix['received_at'] <= matrix_age else None
    sectors = matrix_sectors(fresh)
    sides = _path_sides(ring, sectors, matrix_path(fresh))
    if sectors is None:
        health.append('front_depth_matrix_unavailable')
    radial_front = [v for v in (ring.get('fl_radial'), ring.get('fr_radial')) if v is not None]
    if ring and not radial_front:
        # Older firmware only reports fused fronts; treat them as radial evidence.
        radial_front = [v for v in (ring.get('fl'), ring.get('fr')) if v is not None]
    if ring and ring.get('fr_radial') is None and 'fr_radial' in (telemetry.get('tof_mm') or {}):
        health.append('front_right_radial_unavailable')
    def least(values):
        values = [v for v in values if v is not None]
        return min(values) if values else None
    front = least(radial_front + (list(sectors.values()) if sectors else []))
    # Behind: both radial beams count in full, as before; a rear matrix zone counts when it
    # lies inside the corridor the body sweeps backing straight up (matrix_path is symmetric
    # about the sensor's axis, so its sensor-frame sides need no mirroring for this).
    rear_radial = _rear_radials(ring) if ring else []
    if rear_matrix is None:
        rear = least(rear_radial)
    else:
        rear_path = matrix_path(rear_matrix if 0 <= now - rear_matrix['received_at'] <= matrix_age else None)
        if rear_path is None:
            health.append('rear_depth_matrix_unavailable')
            rear = None
        else:
            rear = least(rear_radial + [min(rear_path.values())]) if ring else None
    result = {
        'front_m': front,
        'path_m': min(sides.values()) if sides else None,
        # What limits the straight path, by side: one short side beside an open centre is a
        # wall to straighten away from, not an obstacle ahead.
        'path_left_m': sides['left'] if sides else None,
        'path_center_m': sides['center'] if sides else None,
        'path_right_m': sides['right'] if sides else None,
        'front_left_m': least([ring.get('fl_radial') or ring.get('fl')] + ([sectors['left']] if sectors else [])),
        'front_center_m': sectors['center'] if sectors else None,
        'front_right_m': least([ring.get('fr_radial')] + ([sectors['right']] if sectors else [])),
        'left_m': least([ring.get('lf'), ring.get('lb')]),
        'right_m': least([ring.get('rf'), ring.get('rb')]),
        'rear_m': rear,
        'front_radial_left_m': least([ring.get('fl_radial') or ring.get('fl')]),
        'front_radial_right_m': least([ring.get('fr_radial') or ring.get('fr')]),
        'front_band_left_m': sectors['left'] if sectors else None,
        'front_band_right_m': sectors['right'] if sectors else None,
        'health': health,
    }
    if ring and all(ring.get(key) is None for key, _ in RING):
        health.append('radial_ring_unavailable')
    return result


# Owner, October 11: the poker arm reaches about 2 ft (0.61 m) out from the left side
# sensors and stays at its drive stance while anything is within 3.5 ft of them; the
# throttle arm reaches about 2 ft ahead of the front sensors and stays parked while
# anything is that close in front.
POKER_KEEP_CLEAR_M = 1.07
THROTTLE_KEEP_CLEAR_M = .61


def arms_blocked(space: dict) -> frozenset[str]:
    """The reaching arms that must stay tucked: an obstacle within their keep-clear distance,
    or no reading to show there is none (unknown sensing keeps the arm in)."""
    blocked = set()
    if space.get('left_m') is None or space['left_m'] < POKER_KEEP_CLEAR_M:
        blocked.add('poker')
    if space.get('front_m') is None or space['front_m'] < THROTTLE_KEEP_CLEAR_M:
        blocked.add('throttle')
    return frozenset(blocked)


def move_allowance(meters: float, space: dict) -> tuple[float, str | None]:
    """Largest same-sign distance the host will request; firmware still gates."""
    if meters > 0:
        if space.get('path_m') is None or 'front_depth_matrix_unavailable' in space['health']:
            return 0., 'front_clearance_unknown'
        room = space['path_m'] - FRONT_MARGIN
    else:
        if space['rear_m'] is None:
            return 0., 'rear_clearance_unknown'
        room = space['rear_m'] - REAR_MARGIN
    allowed = max(0., min(abs(meters), room))
    if allowed < .05:
        return 0., 'path_blocked'
    return math.copysign(allowed, meters), None if allowed >= abs(meters) - 1e-6 else 'shortened_by_clearance'


def wall_angle(tof, side):
    """Degrees a wall beside him converges toward his path ahead (negative: it opens away).

    From the two side sensors of that side (+1 left, -1 right) on the ring: the line through
    their two return points is the wall. None when either is missing or beyond WALL_SEEN_M,
    when they may be seeing different things."""
    if not isinstance(tof, dict):
        return None
    front = _meters(tof.get('lf' if side > 0 else 'rf'))
    back = _meters(tof.get('lb' if side > 0 else 'rb'))
    if front is None or back is None or max(front, back) > WALL_SEEN_M:
        return None
    pf = _point(side*67.5, RING_RADIUS + front)
    pb = _point(side*112.5, RING_RADIUS + back)
    along = math.degrees(math.atan2(pf[1] - pb[1], pf[0] - pb[0]))
    return -side * along


def _point(bearing, radius):
    b = math.radians(bearing)
    return radius*math.cos(b), radius*math.sin(b)


# Host steering added to the firmware's own during the slow crawl through tight space
# (rad/s, + = left). The firmware already centres him between the side sensors, leans
# away from a wall its front pair sees coming and pushes hard inside 13 cm (V2 calib.h
# ASSIST_*); the host adds only what the firmware cannot read: which way a wall beside
# him slants (from the two side sensors of that side) and what the depth matrix sees
# crossing the path ahead, such as a door jamb on one side.
STEER_ANGLE_GAIN = 2.5       # rad/s per radian a wall converges toward the path.
STEER_PATH_M = 1.5
STEER_PATH_GAIN = .5         # rad/s per metre the corridor is more open on one side.
STEER_TERM_MAX = .25
STEER_MAX = .4


def _clamp(value, limit):
    return max(-limit, min(limit, value))


def steer(space, tof):
    """Host share of the crawl steering from the current readings; 0 when nothing slants
    toward him and the corridor ahead is even."""
    turn = 0.
    for side in (1, -1):
        angle = wall_angle(tof, side)
        if angle is not None and angle > 0:
            turn -= side * STEER_ANGLE_GAIN * math.radians(angle)   # Away from the wall closing in.
    pl, pr = space.get('path_left_m'), space.get('path_right_m')
    if pl is not None and pr is not None:
        turn += _clamp(STEER_PATH_GAIN * (min(pl, STEER_PATH_M) - min(pr, STEER_PATH_M)), STEER_TERM_MAX)
    return _clamp(turn, STEER_MAX)


def _pivot(bearing, radius):
    b = math.radians(bearing)
    x, y = AXLE_AFT + radius*math.cos(b), radius*math.sin(b)
    return math.hypot(x, y), math.degrees(math.atan2(y, x))


def allowed_turn(degrees: float, telemetry, arms=ARMS) -> tuple[float, str | None]:
    """V2 swing check: largest same-sign spin keeping body and arms clear of returns.

    Firmware does not gate spins, and the base pivots about its rear axle with
    arms proud of the ring. Unknown sensing refuses: the host gate already
    requires a live ring, so no reading here means no evidence of clearance.
    """
    if not degrees:
        return 0., None
    tof = (telemetry or {}).get('tof_mm')
    if not isinstance(tof, dict):
        return 0., 'turn_clearance_unknown'
    sign = 1. if degrees > 0 else -1.
    limit, limiter = abs(degrees), None
    extents = [(f'ring@{d}',) + _pivot(float(d), RING_RADIUS) for d in range(0, 360, 45)]
    extents += [(label,) + _pivot(bearing, radius) for label, bearing, radius in arms]
    returns = []
    for key, bearing in RING:
        value = _meters(tof.get(key))
        if value is not None:
            returns.append((key,) + _pivot(bearing, RING_RADIUS + value))
    if not returns:
        return 0., 'turn_clearance_unknown'
    for label, reach, extent_bearing in extents:
        for key, distance, bearing in returns:
            if distance > reach + SWING_MARGIN:
                continue
            delta = (sign * (bearing - extent_bearing)) % 360.
            room = delta - SWING_PAD
            if room < 0:
                room = 0. if delta <= 180. else (delta - SWING_PAD) % 360.
            if room < limit:
                limit, limiter = room, f'{label} near {key} return at {distance:.2f} m'
    if limiter is None:
        return degrees, None
    if limit < MIN_TURN:
        return 0., 'turn_path_blocked: ' + limiter
    return sign * limit, 'turn_shortened: ' + limiter


def image_bearing(box, neck_deg: float = 0.) -> float:
    """Base-relative bearing, +left: camera angle plus the neck's current angle."""
    x, _, w, _ = box
    return (.5 - (x + w/2)) * 2 * HALF_FOV + neck_deg


def neck_pulse(degrees: float) -> int:
    """The neck pulse pointing the head this many degrees left of the base, within its travel."""
    return max(LIMITS[0][0], min(LIMITS[0][1], round(NECK_CENTER - degrees * NECK_PULSES_PER_DEG)))


def neck_degrees(pulse) -> float:
    return (NECK_CENTER - pulse) / NECK_PULSES_PER_DEG   # head_left is the low pulse.


def face_range(box) -> float | None:
    """Approximate range from face width; can be wrong by metres (V2 field notes)."""
    try:
        width = float(box[2])
        value = FACE_WIDTH / (2 * math.tan(math.radians(HALF_FOV)) * width)
    except (TypeError, ValueError, ZeroDivisionError, IndexError):
        return None
    return value if 0 < width <= 1 and .2 <= value <= 10. else None


def radar_bodies(frames, *, min_frames=3, cluster_deg=15., min_confidence=.15):
    """Bodies persisting across radar frames; a single-frame return is not a person.

    LD2450 modules track moving targets, so a still person can drop out. The
    result is a bearing prior for where to look, never identity or presence proof.
    """
    clusters = []
    for index, (at, targets) in enumerate(frames):
        for target in targets:
            if target['confidence'] < min_confidence:
                continue
            match = next((c for c in clusters if abs(wrap(target['bearing_deg'] - c['bearing_deg'])) <= cluster_deg), None)
            if match is None:
                match = {'bearing_deg': target['bearing_deg'], 'rows': [], 'frames': set()}
                clusters.append(match)
            match['rows'].append(target)
            match['frames'].add(index)
            weights = [max(r['confidence'], .01) for r in match['rows']]
            match['bearing_deg'] = wrap(math.degrees(math.atan2(
                sum(w*math.sin(math.radians(r['bearing_deg'])) for w, r in zip(weights, match['rows'])),
                sum(w*math.cos(math.radians(r['bearing_deg'])) for w, r in zip(weights, match['rows'])))))
    bodies = []
    for cluster in clusters:
        if len(cluster['frames']) < min_frames:
            continue
        rows = cluster['rows']
        bearing = round(cluster['bearing_deg'])
        bodies.append({'target_id': f'radar:{bearing:+d}', 'bearing_deg': float(bearing),
                       'range_m': round(sorted(r['range_m'] for r in rows)[len(rows)//2], 1),
                       'confidence': round(max(r['confidence'] for r in rows), 2),
                       'moving': any(abs(r['speed_mps']) >= .1 for r in rows),
                       'frames': len(cluster['frames'])})
    return sorted(bodies, key=lambda b: (-b['confidence'], abs(b['bearing_deg'])))[:6]


def describe_bearing(degrees: float) -> str:
    angle = wrap(degrees)
    magnitude = abs(angle)
    side = 'left' if angle > 0 else 'right'
    if magnitude <= 8:
        return 'straight ahead'
    if magnitude >= 165:
        return 'behind'
    if magnitude > 100:
        return f'behind on the {side}'
    return f'{magnitude:.0f} degrees to the {side}'
