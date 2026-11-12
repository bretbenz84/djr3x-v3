#pragma once
#include <math.h>
#include "calib.h"

// Pure gamepad stick -> drive command mapping. No I/O: gamepad.cpp calls it each poll and
// tests/test_stick_shaping.py compiles this header natively.
//
// Owner, 2026-11-12: at full forward, leaning the stick slightly left or right did
// nothing, then past a point the base nearly stopped and turned; no banking. Three causes,
// all in the old per-axis mapping:
//   - A thumbstick travels in a circle, so leaning it sideways at full push shortens the
//     forward reading (45 degrees: 0.71 on each axis). After the deadzone and the expo
//     curve that halved the forward speed just as the turn arrived.
//   - The turn axis had its own 12% deadzone and the expo curve on top, so the first
//     part of the lean commanded nothing.
//   - Full turn stick asked for more wheel-speed difference than the forward ceiling, so
//     the inside wheel was told to stop (and the bridge brakes a wheel told to stop).
// Now the round travel is stretched to a square (full push anywhere in the forward
// quarter is still full forward), the turn axis has only a small deadzone, and while
// translating the turn authority is held under the forward ceiling so the inside wheel
// keeps rolling at full forward.

static inline float stick_clamp(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// Remove an axis deadzone and rescale so the output still starts at 0 and ends at 1.
static inline float stick_axis_deadzone(float v, float dz) {
  const float a = fabsf(v);
  if (a <= dz) return 0.0f;
  const float s = (a - dz) / (1.0f - dz);
  return v < 0.0f ? -s : s;
}

// Raw stick fractions (-1..1 each, round travel) -> square fractions: a radial deadzone
// around centre, then the circle stretched to the square it sits in. A pad that already
// reports a square simply clamps.
static inline void stick_square(float x, float y, float& ox, float& oy) {
  const float r = sqrtf(x * x + y * y);
  if (r <= GAMEPAD_DEADZONE) { ox = 0.0f; oy = 0.0f; return; }
  const float rs = fminf((r - GAMEPAD_DEADZONE) / (1.0f - GAMEPAD_DEADZONE), 1.0f);
  const float m = fmaxf(fabsf(x), fabsf(y));     // > 0 because r > 0
  ox = stick_clamp(x * rs / m, -1.0f, 1.0f);
  oy = stick_clamp(y * rs / m, -1.0f, 1.0f);
}

struct StickDrive {
  float fwd = 0.0f, turn = 0.0f;   // shaped stick fractions (telemetry mirror); right = +
  float lin = 0.0f, ang = 0.0f;    // m/s, rad/s (+ang = left, REP-103)
  float blend = 0.0f;              // 0 spin-in-place mixing .. 1 arcade mixing
};

// x: stick right = +, y: stick forward = +, both raw -1..1. mode_lin / mode_ang are the
// gamepad's ceilings; track_m is the wheel track.
static inline StickDrive stick_to_drive(float x, float y, float mode_lin, float mode_ang,
                                        float track_m) {
  StickDrive d;
  float sx, sy;
  stick_square(x, y, sx, sy);
  // The forward axis keeps its own deadzone so a spin with the thumb a little off the
  // horizontal stays a spin; the turn axis keeps only a small one so a lean while
  // driving registers early.
  d.fwd  = stick_axis_deadzone(sy, GAMEPAD_DEADZONE);
  d.turn = stick_axis_deadzone(sx, GAMEPAD_TURN_DEADZONE);
  const float fs = copysignf(powf(fabsf(d.fwd),  GAMEPAD_LIN_GAMMA), d.fwd);
  const float ts = copysignf(powf(fabsf(d.turn), GAMEPAD_ANG_GAMMA), d.turn);
  d.lin = fs * mode_lin;
  // Spin↔arcade blend, keyed off the raw forward fraction (smoothstep, flat at both ends).
  float bt = stick_clamp((fabsf(d.fwd) - GAMEPAD_SPIN_BLEND_FWD_LO) /
                         (GAMEPAD_SPIN_BLEND_FWD_HI - GAMEPAD_SPIN_BLEND_FWD_LO), 0.0f, 1.0f);
  bt = bt * bt * (3.0f - 2.0f * bt);
  d.blend = bt;
  // Turn authority: the spin ceiling standing still, easing to the arc ceiling
  // while translating. The arc ceiling is a wheel-speed difference of
  // GAMEPAD_ARC_STEER_FRAC of the forward ceiling, so at full forward and full turn the
  // inside wheel still rolls at (1 - FRAC) of the forward speed.
  const float arc_ang = fminf(mode_ang, GAMEPAD_ARC_STEER_FRAC * mode_lin * 2.0f / track_m);
  d.ang = -ts * (mode_ang + (arc_ang - mode_ang) * bt);   // stick right => -ang
  return d;
}

// How arcade the wheel mixing must be at this linear speed (m/s, magnitude): 0 at or
// under GAMEPAD_SPIN_SPEED_LO (a turn may run the inside wheel backward), 1 at or over
// GAMEPAD_SPIN_SPEED_HI (a turn can only slow the inside wheel to a stop), smoothstep
// between. control_tick takes the larger of this and the stick's own blend, so a heavy
// base still carrying speed never has a wheel thrown into reverse by a sudden stick move.
static inline float drive_speed_blend(float speed_abs) {
  float b = stick_clamp((speed_abs - GAMEPAD_SPIN_SPEED_LO) /
                        (GAMEPAD_SPIN_SPEED_HI - GAMEPAD_SPIN_SPEED_LO), 0.0f, 1.0f);
  return b * b * (3.0f - 2.0f * b);
}
