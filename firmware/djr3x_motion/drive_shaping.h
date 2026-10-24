#pragma once
#include <math.h>
#include "calib.h"

// Pure per-wheel effort shaping and reflex braking. No I/O: hal.cpp and control.cpp
// call these, and tests/test_drive_shaping.py compiles this header natively.
//
// Owner, 2026-10-23: on the 52 lb robot turns lurched in and stops ended in a slam,
// mostly false obstacle stops. The setpoint ramp was gentle; the effort underneath
// stepped. A fixed breakaway kick (about 75% duty on hardwood pivots, 100% on carpet)
// landed on the first commanded tick and held 0.12 s past breakaway. A running floor
// (22% straight, 37% pivot) stayed on until the target fell under WHEEL_STOP_EPS_MS,
// then duty went to zero with the bridge enabled, a short-circuit brake. A reflex block
// cut the speed ramp to zero in one tick, so every false near reading was a slam.

static inline float shape_clamp(float v, float lo, float hi) {
  return v < lo ? lo : (v > hi ? hi : v);
}

// One wheel's controller state. Reset whenever the wheel is commanded to rest.
struct WheelLoop {
  float integ = 0.0f, eprev = 0.0f;
  float stalled_s = 0.0f;   // how long the wheel has sat still while commanded
  float boost = 0.0f;       // breakaway duty above the running floor (ramped)
  float stop_from = 0.0f;   // |target| when the current stop began; 0 = not stopping
};

struct WheelGains {
  float kp, ki, kd, kff;
  float run_floor;      // duty floor while rolling (straight min_duty, pivot run duty)
  float kick_ceiling;   // the most floor + boost may reach (breakaway duty)
  float vcomp;          // battery-sag compensation (>= 1)
};

// Breakaway boost: rises while the wheel is genuinely stalled, falls once it moves, so
// the base gets the push that breaks it loose rather than a fixed punch, and sheds the
// excess as it rolls. Gear lash that twitches a planted wheel only pauses the rise: the
// wheel stalls again and the rise resumes. A stop never boosts.
static inline void wheel_launch_step(WheelLoop& w, float target, float meas, float headroom,
                                     bool stopping, float dt) {
  const float fall = WHEEL_BOOST_FALL_DUTY_S * dt;
  if (stopping) {
    w.stalled_s = 0.0f;
    w.boost -= fall;
  } else {
    // A wheel crawling at a target under the stall threshold is not stalled.
    const float stall_eps = fminf(WHEEL_STALLED_EPS_MS, 0.5f * fabsf(target));
    const bool moving = fabsf(meas) >= stall_eps;
    w.stalled_s = moving ? 0.0f : w.stalled_s + dt;
    if (moving) w.boost -= fall;
    else if (w.stalled_s >= WHEEL_STALL_DEBOUNCE_S) w.boost += WHEEL_BOOST_RISE_DUTY_S * dt;
  }
  w.boost = shape_clamp(w.boost, 0.0f, fmaxf(0.0f, headroom));
}

// Share of the floor, boost and integrator still applied while the ramp falls to rest:
// 1 when the stop begins, 0 as the target reaches WHEEL_STOP_EPS_MS. Effort tapers with
// the ramp instead of holding the floor and then cutting to a short-circuit brake.
static inline float wheel_stop_fade(WheelLoop& w, float target_abs, bool stopping) {
  if (!stopping) { w.stop_from = 0.0f; return 1.0f; }
  if (w.stop_from <= 0.0f) w.stop_from = target_abs;
  const float span = w.stop_from - WHEEL_STOP_EPS_MS;
  if (span <= 1e-4f) return 0.0f;
  return shape_clamp((target_abs - WHEEL_STOP_EPS_MS) / span, 0.0f, 1.0f);
}

// Target m/s -> signed duty: feedforward, floor plus boost in the travel direction, and
// PID trim on the encoder-measured error. vcomp scales before the duty clamp so the
// anti-windup sees the true saturation point. `stopping` = the body is ramping to rest.
static inline int wheel_effort(WheelLoop& w, float target, float meas, float dt,
                               const WheelGains& g, bool stopping) {
  if (fabsf(target) < WHEEL_STOP_EPS_MS) { w = WheelLoop(); return 0; }
  const float dir = target >= 0.0f ? 1.0f : -1.0f;
  const float fade = wheel_stop_fade(w, fabsf(target), stopping);
  wheel_launch_step(w, target, meas, g.kick_ceiling - g.run_floor, stopping, dt);
  const float err = target - meas;
  const float deriv = (dt > 1e-4f) ? (err - w.eprev) / dt : 0.0f;
  w.eprev = err;
  const float floor_duty = (g.run_floor + w.boost) * fade;
  const float base = g.kff * target + floor_duty * dir + g.kp * err + g.kd * deriv;
  if (!stopping) {   // a stop freezes the integrator; its share fades with the floor
    const float next_i = shape_clamp(w.integ + g.ki * err * dt,
                                     -WHEEL_PID_I_CLAMP, WHEEL_PID_I_CLAMP);
    const float candidate = (base + next_i) * g.vcomp;
    // Conditional integration: do not wind farther into saturation.
    if (!((candidate > (float)PWM_DUTY_MAX && err > 0.0f) ||
          (candidate < -(float)PWM_DUTY_MAX && err < 0.0f))) {
      w.integ = next_i;
    }
  }
  float u = (base + w.integ * fade) * g.vcomp;
  // A stop slows the wheel through low duty (the bridge shorts the motor between
  // pulses); it never drives the wheel backward.
  if (stopping) u = dir > 0.0f ? fmaxf(u, 0.0f) : fminf(u, 0.0f);
  return (int)shape_clamp(u, -(float)PWM_DUTY_MAX, (float)PWM_DUTY_MAX);
}

// Deceleration for a reflex block in the direction of travel: as firm as the room left
// before the obstacle requires, never gentler than REFLEX_DECEL_MIN_MS2. A one-frame
// false reading then costs a brief slow-down instead of a slam and relaunch.
static inline float reflex_decel(float speed_abs, float room_m) {
  const float usable = fmaxf(room_m - REFLEX_STOP_MARGIN_M, 0.01f);
  return shape_clamp(speed_abs * speed_abs / (2.0f * usable),
                     REFLEX_DECEL_MIN_MS2, REFLEX_DECEL_MAX_MS2);
}

// One control tick of the linear speed ramp under a reflex block: sheds speed toward
// zero at the reflex deceleration (or the ordinary ramp, if firmer), never past zero.
static inline float reflex_ramp_step(float ramp, float speed_abs, float room_m,
                                     float accel, float dt) {
  const float step = fmaxf(accel, reflex_decel(speed_abs, room_m)) * dt;
  return ramp > 0.0f ? fmaxf(0.0f, ramp - step) : fminf(0.0f, ramp + step);
}
