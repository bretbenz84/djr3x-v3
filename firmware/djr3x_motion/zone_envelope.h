#pragma once
#include <math.h>
#include "calib.h"

// Pure speed-adaptive obstacle envelope (calib.h ZONE_*). context.h wraps these for the
// safety reflex and the control taper; tests/test_zone_envelope.py compiles this natively.
//
// The configured stop/slow distances are the FULL-SPEED envelope. They apply in full at
// ZONE_SPEED_REF_MS and shrink linearly to the calib.h floors at rest, so a fast approach
// brakes early and slow positioning can get close.

static inline float zone_speed_frac(float lin_ms) {
  const float f = fabsf(lin_ms) / ZONE_SPEED_REF_MS;
  return f < 0.0f ? 0.0f : (f > 1.0f ? 1.0f : f);
}

static inline float stop_zone_at(float stop_zone_m, float lin_ms) {
  const float lo = fminf(STOP_ZONE_MIN_M, stop_zone_m);   // a tighter config wins
  return lo + (stop_zone_m - lo) * zone_speed_frac(lin_ms);
}

static inline float slow_zone_at(float stop_zone_m, float slow_zone_m, float lin_ms) {
  const float lo = fminf(SLOW_ZONE_MIN_M, slow_zone_m);
  const float s  = lo + (slow_zone_m - lo) * zone_speed_frac(lin_ms);
  return fmaxf(s, stop_zone_at(stop_zone_m, lin_ms) + 0.05f);   // keep a real band above stop
}

// Full-speed envelope in force: the configured one, but never tighter than the gamepad's
// own while a person is driving from the pad. The host tightens the configured zones for
// Rex's slow approaches to people (0.15 / 0.60 m at 0.25 m/s); the pad drives at up to
// GAMEPAD_LIN_MS, where that envelope cannot stop a heavy base in time.
static inline float zone_stop_full(float cfg_stop_m, bool manual) {
  return manual ? fmaxf(cfg_stop_m, GAMEPAD_STOP_ZONE_M) : cfg_stop_m;
}
static inline float zone_slow_full(float cfg_slow_m, bool manual) {
  return manual ? fmaxf(cfg_slow_m, GAMEPAD_SLOW_ZONE_M) : cfg_slow_m;
}
