"""The drive board's compass: its raw counts turned into a heading (owner, November 25: extracted from V2).

The QMC5883 on the drive board sends raw axis counts in telemetry (`mag`: ok, x, y, z, and ovl on overflow);
the firmware does no calibration. Mounted on the robot, its own motors, pack and wiring bend the field (hard
iron shifts it, soft iron squashes it), so the correction is measured in place: tools/compass.py calibrate
records the field while he is turned through a full circle and writes local/calibration/compass.json.

From V2 (hardware/compass.py, tools/compass_calibrate.py, source 95f2403): the min/max box model (offset = the
cloud's centre, scale = each axis's half-range evened out), the refusal of a sweep that never happened, the
field-strength gate, and the tilt-compensated heading. Adapted for a robot on the floor: a sweep that only
turned him flat leaves the vertical axis unmeasured, so its heading uses the two level axes alone (V2 scaled
that axis 3.5x from noise). Which way the sensor's axes point is learned from the sweep itself (the gyro says
which way he turned) and, when someone says which way he faces at the end, the heading is pinned to that.
Samples taken while the motors draw current are left out, as V2's fusion did. Not carried over: the gyro and
compass fusion service, turn correction and cardinal-direction commands (nothing here uses them).
"""
from dataclasses import dataclass, asdict
import json
import math
from pathlib import Path
import time

CALIBRATION_FILE = Path('local/calibration/compass.json')
DECLINATION_DEG = 13.     # True north from magnetic north here (V2's figure); a told facing replaces it.
MIN_FIELD_COUNTS = 50.    # V2, August 22: a robot that never moved "calibrated" to 3 counts; the real field is hundreds.
FLAT_SPAN = .25           # The vertical axis swept less than this share of the level ones: a flat turn.
FIELD_TOLERANCE = .25     # A reading whose strength strays this far from the calibrated field is disturbed.
QUIET_MA = 1600           # Pack current above this means the motors are working: the field is not to be trusted (V2).
MIN_SAMPLES = 10
MIN_TURN_DEG = 300.       # The gyro must see about a full circle for the sweep to count.


@dataclass(frozen=True)
class Calibration:
    offset: tuple
    scale: tuple
    field_norm: float
    flat: bool                    # Only a level turn: the heading uses x and y alone.
    mirrored: bool = False        # The sensor's level axes read the turn backwards (mounted upside down or swapped).
    heading_offset_deg: float = DECLINATION_DEG
    told_facing_deg: float | None = None
    samples: int = 0
    saved_at: str = ''

    def apply(self, x, y, z):
        (ox, oy, oz), (sx, sy, sz) = self.offset, self.scale
        x, y, z = (x - ox) * sx, (y - oy) * sy, (z - oz) * sz
        return (x, -y, z) if self.mirrored else (x, y, z)


def ang_diff(a, b):
    """Shortest signed difference a - b in degrees, in (-180, 180]."""
    d = (a - b) % 360.
    return d - 360. if d > 180. else d


def tilt_compensated_heading(mx, my, mz, pitch, roll):
    """Degrees clockwise from the sensor's x axis to magnetic north, [0, 360). pitch and roll in radians
    (V2's AN4248-style projection onto the level plane)."""
    sp, cp, sr, cr = math.sin(pitch), math.cos(pitch), math.sin(roll), math.cos(roll)
    mx_h = mx * cp + mz * sp
    my_h = mx * sr * sp + my * cr - mz * sr * cp
    return math.degrees(math.atan2(-my_h, mx_h)) % 360.


def compute_calibration(samples):
    """samples: (x, y, z) raw counts from a turn through a full circle. Raises ValueError for too few or a
    cloud with a level axis that never varied."""
    if len(samples) < MIN_SAMPLES:
        raise ValueError(f'need at least {MIN_SAMPLES} samples, got {len(samples)}')
    lows, highs = [min(axis) for axis in zip(*samples)], [max(axis) for axis in zip(*samples)]
    half = [(h - l) / 2. for l, h in zip(lows, highs)]
    if min(half[:2]) <= 0:
        raise ValueError('a level axis never varied: he did not turn')
    flat = half[2] < FLAT_SPAN * max(half[:2])
    offset = [(h + l) / 2. for l, h in zip(lows, highs)]
    if flat:
        # The vertical axis saw only noise: centring or scaling it would invent a field (V2's 3.5x).
        level = (half[0] + half[1]) / 2.
        scale = (level / half[0], level / half[1], 1.)
        offset[2] = sum(s[2] for s in samples) / len(samples)
    else:
        mean = sum(half) / 3.
        scale = tuple(mean / h for h in half)
    cal = Calibration(tuple(offset), tuple(scale), 0., flat, samples=len(samples))
    norms = [_norm(cal.apply(*s), cal.flat) for s in samples]
    return Calibration(cal.offset, cal.scale, sum(norms) / len(norms), flat, samples=len(samples))


def coverage(cal, turned_deg):
    """(ok, note): whether the sweep measured the field. A robot that never turned gives a tiny field (V2,
    August 22) or, read with the gyro, too little turning."""
    if cal.field_norm < MIN_FIELD_COUNTS:
        return False, (f'the field came out {cal.field_norm:.1f} counts, far below the {MIN_FIELD_COUNTS:.0f} a real '
                       'turn gives: he cannot have turned much. Nothing was written.')
    if turned_deg is not None and abs(turned_deg) < MIN_TURN_DEG:
        return False, f'the gyro saw {abs(turned_deg):.0f} degrees of turning, not a full circle. Nothing was written.'
    return True, ('a flat turn only: the heading uses the two level axes, and is fine while he stands level'
                  if cal.flat else '')


def _norm(v, flat):
    return math.hypot(v[0], v[1]) if flat else math.sqrt(sum(c * c for c in v))


def _raw_heading(cal, mag, imu):
    x, y, z = cal.apply(float(mag['x']), float(mag['y']), float(mag['z']))
    if cal.flat:
        return math.degrees(math.atan2(-y, x)) % 360., _norm((x, y, z), True)
    pitch, roll = (math.radians(float((imu or {}).get(k) or 0.)) for k in ('pitch', 'roll'))
    return tilt_compensated_heading(x, y, z, pitch, roll), _norm((x, y, z), False)


def heading(telemetry, cal):
    """{'heading_deg' (true, clockwise from north), 'field_ok', 'motors_quiet'} from one telemetry message, or
    None without a magnetometer reading."""
    mag = (telemetry or {}).get('mag') or {}
    if not mag.get('ok') or mag.get('ovl') or cal is None:
        return None
    raw, strength = _raw_heading(cal, mag, (telemetry or {}).get('imu'))
    current = (telemetry or {}).get('batt_ma')
    return {'heading_deg': round((raw + cal.heading_offset_deg) % 360., 1),
            'field_ok': cal.field_norm > 0 and abs(strength - cal.field_norm) <= FIELD_TOLERANCE * cal.field_norm,
            'motors_quiet': isinstance(current, (int, float)) and abs(current) <= QUIET_MA}


def fit(readings, told_facing_deg=None):
    """A calibration from a sweep. readings: telemetry messages in order. Quiet ones (motors idle) give the
    field; the gyro yaw (+left) over all of them says how far and which way he turned, so a sensor whose
    headings run the wrong way is mirrored. told_facing_deg: true heading he faces at the end, when a person
    says it; it replaces the declination. Returns (calibration or None, note)."""
    quiet = [r for r in readings if (r.get('mag') or {}).get('ok') and not r['mag'].get('ovl')
             and isinstance(r.get('batt_ma'), (int, float)) and abs(r['batt_ma']) <= QUIET_MA]
    yaws = [r['imu']['yaw'] for r in readings if (r.get('imu') or {}).get('ok') and isinstance(r['imu'].get('yaw'), (int, float))]
    turned = sum(ang_diff(b, a) for a, b in zip(yaws, yaws[1:])) if len(yaws) > 1 else None
    try:
        cal = compute_calibration([(r['mag']['x'], r['mag']['y'], r['mag']['z']) for r in quiet])
    except ValueError as exc:
        return None, str(exc)
    ok, note = coverage(cal, turned)
    if not ok:
        return None, note
    # A left turn (yaw up) takes a compass heading down. Headings that rose with it: the axes are mirrored.
    headings = [_raw_heading(cal, r['mag'], r.get('imu'))[0] for r in quiet]
    swept = sum(ang_diff(b, a) for a, b in zip(headings, headings[1:]))
    mirrored = turned is not None and swept * turned > 0
    cal = Calibration(cal.offset, cal.scale, cal.field_norm, cal.flat, mirrored, DECLINATION_DEG, None, cal.samples)
    if told_facing_deg is not None:
        last = _raw_heading(cal, quiet[-1]['mag'], quiet[-1].get('imu'))[0]
        cal = Calibration(cal.offset, cal.scale, cal.field_norm, cal.flat, mirrored,
                          round(ang_diff(told_facing_deg, last) % 360., 1), float(told_facing_deg), cal.samples)
    return cal, note


def save(cal, path=CALIBRATION_FILE):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {**asdict(cal), 'offset': list(cal.offset), 'scale': list(cal.scale),
           'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S')}
    path.write_text(json.dumps(row, indent=2) + '\n')
    return path


def load(path=CALIBRATION_FILE):
    """The saved calibration, or None when there is none (headings are then not given at all: an uncalibrated
    compass reads plausibly and points nowhere)."""
    try:
        row = json.loads(Path(path).read_text())
        return Calibration(tuple(map(float, row['offset'])), tuple(map(float, row['scale'])), float(row['field_norm']),
                           bool(row['flat']), bool(row.get('mirrored', False)),
                           float(row.get('heading_offset_deg', DECLINATION_DEG)), row.get('told_facing_deg'),
                           int(row.get('samples', 0)), row.get('saved_at', ''))
    except FileNotFoundError:
        return None
