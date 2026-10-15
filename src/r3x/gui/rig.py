"""Read-only servo-to-rig mapping for the 3D avatar (V2 gui/avatar_rig.py, adapted).

Coordinates keep the source assembly's Z-up metres. Input is the servo owner's last
commanded pulses (controller output, not joint feedback); nothing here sends commands.
The rig models the neck, lift, head pitch, visor, hero arm/elbow/hand and poker arm.
V3's side tilt is an added rotation; the throttle arm and the poker elbow/wrist are not
separate meshes in the V2 export and stay fixed.
"""
from __future__ import annotations

from functools import lru_cache
import json
import math
from pathlib import Path


from ..servo_poses import (DRIVE_HEAD, HEAD_PAIR, LIMITS, NECK_CENTER, UNPOWERED_REST, VISOR_DOWN, VISOR_OPEN,
                           AWAKE_ARMS, degrees_from_pitch, head_pose)

ASSET_DIR = Path(__file__).resolve().parent / 'assets' / 'rex'
SPEC = json.loads((ASSET_DIR / 'rig.json').read_text())

# Rig joint -> V3 channel whose pulse range spans the rig limits (V2 SERVO_CHANNELS order).
CHANNELS = {'neck': 0, 'headlift': 1, 'elbow': 4, 'hand': 5, 'pokerarm': 6, 'heroarm': 7}
# The rig's head pitch: V2's level head (4320 of 3904..5504) sat at this rig angle, and a
# higher rig angle raises the head (checked on rendered poses, October 14). V3 pitch degrees
# (PITCH_UP_DEG/PITCH_DOWN_DEG, V2 estimates) offset it.
HEAD_LEVEL_DEG = SPEC['limits']['headtilt'][0] + (4320 - 3904) / (5504 - 3904) * (
    SPEC['limits']['headtilt'][1] - SPEC['limits']['headtilt'][0])
VISOR_OPEN_DEG = SPEC['limits']['visor'][0] * (1 - (6560 - 4544) / (6976 - 4544))
SIDE_TILT_DEG = 12.   # Display estimate for a full V3 side tilt; unmeasured on the robot.
# The rig head's forward direction (perpendicular to its pitch axis, level).
HEAD_FORWARD = (-SPEC['head_axis'][1], SPEC['head_axis'][0], 0.)
# Powered-off pose before the first readback (visor down, head parked).
BOOT_PULSES = dict(UNPOWERED_REST)
# Shown when no servo controller is attached (dev Mac): awake, level, visor open.
AWAKE_PULSES = {**BOOT_PULSES, 0: NECK_CENTER, **dict(DRIVE_HEAD), 3: VISOR_OPEN, **dict(AWAKE_ARMS)}


def _fraction(value, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return .5
    if not math.isfinite(value) or high == low:
        return .5
    return max(0., min(1., (value - low) / (high - low)))


def rig_values(pulses) -> dict[str, float]:
    """Rig degrees (lift in metres) for commanded pulses; missing channels use the boot pose."""
    pulses = {**BOOT_PULSES, **{ch: v for ch, v in dict(pulses or {}).items() if ch in LIMITS
                                and isinstance(v, (int, float)) and math.isfinite(v)}}
    values = {}
    for name, channel in CHANNELS.items():
        lo, hi = SPEC['limits'][name]
        values[name] = lo + (hi - lo) * _fraction(pulses[channel], *LIMITS[channel])
    # 27 (closed) .. 0 (open). V3's visor runs inverted; its open neutral shows at V2's open
    # neutral angle (6560 of 4544..6976), the surprise limit fully open.
    closed, opened = SPEC['limits']['visor']
    if pulses[3] >= VISOR_OPEN:
        values['visor'] = VISOR_OPEN_DEG + (closed - VISOR_OPEN_DEG) * _fraction(pulses[3], VISOR_OPEN, VISOR_DOWN)
    else:
        values['visor'] = opened + (VISOR_OPEN_DEG - opened) * _fraction(pulses[3], LIMITS[3][0], VISOR_OPEN)
    pitch, tilt = _head(*(int(pulses[ch]) for ch in HEAD_PAIR))
    values['headtilt'] = HEAD_LEVEL_DEG + degrees_from_pitch(pitch)
    values['headside'] = SIDE_TILT_DEG * tilt
    values['headlift'] -= SPEC['lift_reference'] * (SPEC['limits']['headlift'][1] - SPEC['limits']['headlift'][0])
    return values


@lru_cache(maxsize=256)
def _head(left, right):
    """head_pose searches 201 pitches; the avatar asks 30 times a second for few distinct pairs."""
    try:
        return head_pose({HEAD_PAIR[0]: left, HEAD_PAIR[1]: right})
    except (TypeError, ValueError):
        return 0., 0.


def smooth(current: dict, target: dict, fraction: float) -> dict:
    """Ease displayed rig values toward their targets (V2 GUI_AVATAR_SMOOTHING .25 per frame)."""
    return {k: current.get(k, v) + (v - current.get(k, v)) * fraction for k, v in target.items()}


def translation(p):
    import numpy as np
    m = np.eye(4)
    m[:3, 3] = p
    return m


def rotation(axis, degrees):
    import numpy as np
    a = np.asarray(axis, dtype=float)
    a /= np.linalg.norm(a)
    x, y, z = a
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    m = np.eye(4)
    m[:3, :3] = c * np.eye(3) + (1-c) * np.outer(a, a) + s*k
    return m


def around(p, axis, angle):
    import numpy as np
    return translation(p) @ rotation(axis, angle) @ translation(-np.asarray(p))


def point(m, p):
    import numpy as np
    return (m @ np.r_[p, 1])[:3]


def align(a, b):
    import numpy as np
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    a, b = a / np.linalg.norm(a), b / np.linalg.norm(b)
    axis = np.cross(a, b)
    dot = float(np.clip(a @ b, -1, 1))
    if np.linalg.norm(axis) < 1e-9:
        if dot > 0:
            return np.eye(4)
        axis = np.cross(a, [1, 0, 0] if abs(a[0]) < .9 else [0, 1, 0])
    return rotation(axis, math.degrees(math.acos(dot)))


def pose_matrices(v, spec=SPEC):
    """Group transforms for rig values (rig_values output)."""
    import numpy as np
    v = {k: (x if math.isfinite(x) else 0.) for k, x in v.items()}
    m = {'fixed': np.eye(4)}
    m['poker'] = rotation([0, 0, 1], v['pokerarm'])
    m['hero'] = rotation([0, 0, 1], v['heroarm'])
    m['elbow'] = m['hero'] @ around(spec['elbow_pivot'], spec['elbow_axis'], v['elbow'])
    m['wrist'] = m['elbow'] @ around(spec['wrist_pivot'], spec['wrist_axis'], v['hand'])
    m['neck'] = translation([0, 0, v['headlift']]) @ rotation([0, 0, 1], v['neck'])
    m['head'] = (m['neck'] @ around(spec['head_pivot'], HEAD_FORWARD, v.get('headside', 0.))
                 @ around(spec['head_pivot'], spec['head_axis'], -v['headtilt']))
    m['visor'] = m['head'] @ around(spec['visor_pivot'], spec['visor_axis'], v['visor'])
    lower, upper = np.array(spec['piston_lower']), np.array(spec['piston_upper'])
    a, b = point(m['hero'], lower), point(m['elbow'], upper)
    r = m['hero'] @ align(upper-lower, point(np.linalg.inv(m['hero']), b)-lower)
    m['piston_body'] = translation(b) @ r @ translation(-upper)
    m['piston_rod'] = translation(a) @ r @ translation(-lower)
    return m


def spring_points(matrices, spec=SPEC, count=181):
    import numpy as np
    a = np.array(spec['spring_bottom'])
    b = point(matrices['head'], spec['spring_top'])
    t = np.linspace(0, 1, count)
    p = a[None, :] + t[:, None] * (b-a)[None, :]
    p[:, 0] += .045 * np.cos(t * 5 * math.pi)
    p[:, 1] += .045 * np.sin(t * 5 * math.pi)
    return p
