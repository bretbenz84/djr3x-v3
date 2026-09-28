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
from .servo_poses import NECK_CENTER
NECK_PULSES_PER_DEG = (5472 - 1984) / 45.  # V2 owner: neck sweeps about 90 degrees in total.
FACE_WIDTH = .16
MATRIX_CLEAR = 3.5
FRONT_MARGIN = .35      # Host stop short of a front return; firmware still stops at its zone.
PERSON_MARGIN = .22     # Approaching a person: knees/chair legs may be this close, never closer.
REAR_MARGIN = .30


def wrap(degrees: float) -> float:
    return (degrees + 180.) % 360. - 180.


def _meters(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value / 1000. if math.isfinite(value) and value >= 0 else None


def matrix_sectors(matrix):
    """Second-nearest floor-rejected zone per left/centre/right column band.

    The firmware reduces the 8x8 grid to two fused front values; the host keeps
    three bands so one phantom half cannot hide an open corridor. rej holds the
    firmware's per-row floor thresholds: at/beyond it is floor, not obstacle.
    """
    if not matrix or len(matrix.get('grid', ())) != 64 or len(matrix.get('rej', ())) != 8:
        return None
    bands = {'left': range(0, 3), 'center': range(3, 5), 'right': range(5, 8)}
    result = {}
    for name, columns in bands.items():
        zones = sorted(matrix['grid'][row*8+col] for row in range(8) for col in columns
                       if 25 <= matrix['grid'][row*8+col] < min(matrix['rej'][row], 3500))
        result[name] = zones[1]/1000. if len(zones) >= 2 else MATRIX_CLEAR
    return result


BODY_HALF_WIDTH = .27   # About the sensor ring radius.
PATH_MARGIN = .05       # Lateral gap kept when driving straight past something beside the path.


def _path_front(ring, sectors):
    """Forward room along the body's straight path (September 27 hallway run).

    The matrix counts in full. A 22.5 degree front radial counts in full unless the matrix
    band on its side is clear well beyond its return: then the return lies outside the
    matrix view (at least ~22 degrees off-axis) and counts only when that places it inside
    the body's path. A hallway corner beside the robot no longer blocks the hallway."""
    if sectors is None:
        return None
    limits = list(sectors.values())
    sine = math.sin(math.radians(22.5))
    for side, keys in (('left', ('fl_radial', 'fl')), ('right', ('fr_radial', 'fr'))):
        radial = next((ring[k] for k in keys if ring.get(k) is not None), None)
        if radial is None:
            continue
        beside = (sectors[side] > radial + .15 and
                  RING_RADIUS*sine + radial*sine >= BODY_HALF_WIDTH + PATH_MARGIN)
        if not beside:
            limits.append(radial)
    return min(limits)


def clearance(telemetry, matrix, now, *, telemetry_age=.5, matrix_age=1.):
    """Open distance by direction, with explicit unknowns and sensor health."""
    health = []
    ring = {}
    if telemetry and 0 <= now - telemetry['received_at'] <= telemetry_age:
        tof = telemetry.get('tof_mm') or {}
        ring = {key: _meters(tof.get(key)) for key, _ in RING + (('fl_radial', 0), ('fr_radial', 0))}
    else:
        health.append('drive_telemetry_stale')
    sectors = matrix_sectors(matrix) if matrix and 0 <= now - matrix['received_at'] <= matrix_age else None
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
    result = {
        'front_m': front,
        'path_m': _path_front(ring, sectors),
        'front_left_m': least([ring.get('fl_radial') or ring.get('fl')] + ([sectors['left']] if sectors else [])),
        'front_center_m': sectors['center'] if sectors else None,
        'front_right_m': least([ring.get('fr_radial')] + ([sectors['right']] if sectors else [])),
        'left_m': least([ring.get('lf'), ring.get('lb')]),
        'right_m': least([ring.get('rf'), ring.get('rb')]),
        'rear_m': least([ring.get('rl'), ring.get('rr')]),
        'front_radial_left_m': least([ring.get('fl_radial') or ring.get('fl')]),
        'front_radial_right_m': least([ring.get('fr_radial') or ring.get('fr')]),
        'front_band_left_m': sectors['left'] if sectors else None,
        'front_band_right_m': sectors['right'] if sectors else None,
        'health': health,
    }
    if ring and all(ring.get(key) is None for key, _ in RING):
        health.append('radial_ring_unavailable')
    return result


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
