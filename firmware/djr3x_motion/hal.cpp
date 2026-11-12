#include "hal.h"

#if MOTION_HW_PRESENT
// ===========================================================================
// REAL HARDWARE — Phase 1 closed-loop drive base.
//   - 2× BTS7960 H-bridge via LEDC PWM (RPWM/LPWM per wheel) + enable lines.
//   - 2× Hall quadrature encoder via the PCNT peripheral (ESP32Encoder, x4).
//   - Differential-drive kinematics + per-wheel velocity PID on encoder speed.
//   - Odometry integrated from encoder deltas.
// Pins in pins.h, measured/tuned constants in calib.h.
// ToF (8 radial sensors: 4× VL53L0X + 4× VL53L1X) lives in tof.cpp — see
// hal_read_tof() — gated by MOTION_TOF_PRESENT; obstacle avoidance is inactive until
// that subsystem is wired and the build enables it (docs/motion_system.md §6).
// ===========================================================================
#include "pins.h"
#include "calib.h"
#include "drive_shaping.h"
#include <Arduino.h>
#include <math.h>
#include <ESP32Encoder.h>

// LEDC PWM helpers — one pin-based interface across Arduino-ESP32 core versions.
// Core 3.x replaced the channel-based LEDC API (ledcSetup / ledcAttachPin / ledcWrite
// by channel) with a pin-based one (ledcAttach / ledcWrite by pin). We support BOTH so
// the firmware builds whether a machine has the legacy 2.x core or the current 3.x core
// — the rest of this file only ever calls pwm_attach(pin) / pwm_write(pin, duty).
#if ESP_ARDUINO_VERSION >= ESP_ARDUINO_VERSION_VAL(3, 0, 0)
static inline void pwm_attach(int pin) { ledcAttach(pin, PWM_FREQ_HZ, PWM_RES_BITS); }
static inline void pwm_write(int pin, int duty) { ledcWrite(pin, duty); }
#else
// 2.x: each PWM pin maps to a fixed LEDC channel; attach + write go through the channel.
static int pwm_chan(int pin) {
  if (pin == PIN_L_RPWM) return 0;
  if (pin == PIN_L_LPWM) return 1;
  if (pin == PIN_R_RPWM) return 2;
  return 3;                                  // PIN_R_LPWM
}
static inline void pwm_attach(int pin) {
  const int ch = pwm_chan(pin);
  ledcSetup(ch, PWM_FREQ_HZ, PWM_RES_BITS);
  ledcAttachPin(pin, ch);
}
static inline void pwm_write(int pin, int duty) { ledcWrite(pwm_chan(pin), duty); }
#endif

static ESP32Encoder encL, encR;

// Per-tick measurement state.
static int64_t s_prev_l = 0, s_prev_r = 0;   // encoder counts at the previous tick
static float   s_vmeas_l = 0, s_vmeas_r = 0; // measured wheel speeds (m/s) this tick
static bool    s_motors_enabled = false;

// Per-wheel controller state: PID, breakaway boost, stop fade (drive_shaping.h).
static WheelLoop s_loop_l, s_loop_r;
// Encoder watch (drive_shaping.h): each wheel's counts this tick, forward positive, and
// how long a hard-driven wheel has gone without measured travel the way it is driven.
static long s_dcount_l = 0, s_dcount_r = 0;
static EncoderWatch s_watch_l, s_watch_r;
// Last wheel targets handed to the speed loops (gamepad wheel-target slew).
static float s_tgt_l = 0.0f, s_tgt_r = 0.0f;

static inline void motors_enable(bool en) {
  if (en == s_motors_enabled) return;        // only toggle the GPIO on a real change
  digitalWrite(PIN_L_EN, en ? HIGH : LOW);
  digitalWrite(PIN_R_EN, en ? HIGH : LOW);
  s_motors_enabled = en;
}

// Apply a signed duty (-MAX..+MAX) to one wheel by PWM-ing exactly one half of
// its H-bridge (the other at 0). Forward = RPWM, reverse = LPWM; never both.
static inline void apply_wheel_duty(int rpwm_pin, int lpwm_pin, int duty) {
  if (duty >= 0) { pwm_write(rpwm_pin, duty);  pwm_write(lpwm_pin, 0); }
  else           { pwm_write(rpwm_pin, 0);     pwm_write(lpwm_pin, -duty); }
}

static void reset_pid() {
  s_loop_l = WheelLoop();
  s_loop_r = WheelLoop();
  s_watch_l = EncoderWatch();
  s_watch_r = EncoderWatch();
  s_tgt_l = 0.0f; s_tgt_r = 0.0f;
}

void hal_init() {
  // Motor enables: start DISABLED so the base is boot-safe (motors coast, no
  // drive until an explicit command energizes them).
  pinMode(PIN_L_EN, OUTPUT);
  pinMode(PIN_R_EN, OUTPUT);
  digitalWrite(PIN_L_EN, LOW);
  digitalWrite(PIN_R_EN, LOW);
  s_motors_enabled = false;

  // LEDC PWM for the four half-bridge inputs; attach each at the configured
  // freq/resolution, all starting at 0 duty.
  pwm_attach(PIN_L_RPWM);
  pwm_attach(PIN_L_LPWM);
  pwm_attach(PIN_R_RPWM);
  pwm_attach(PIN_R_LPWM);
  pwm_write(PIN_L_RPWM, 0); pwm_write(PIN_L_LPWM, 0);
  pwm_write(PIN_R_RPWM, 0); pwm_write(PIN_R_LPWM, 0);

  // Encoders: full quadrature (x4). Internal weak pull-ups so a disconnected or
  // floating input doesn't generate phantom counts during incremental bring-up.
  ESP32Encoder::useInternalWeakPullResistors = puType::up;
  encL.attachFullQuad(PIN_ENC_L_A, PIN_ENC_L_B);
  encR.attachFullQuad(PIN_ENC_R_A, PIN_ENC_R_B);
  encL.clearCount();
  encR.clearCount();
  s_prev_l = 0; s_prev_r = 0;
  s_vmeas_l = s_vmeas_r = 0;
  reset_pid();
}

void hal_read_odom(Odom& out, float dt) {
  // Caller (control_tick) holds the state lock, so reading the runtime-tunable
  // params here is a consistent snapshot. apply_config clamps both > 0, so the
  // divides below are safe.
  const float cpm_l = g_ctx.params.counts_per_meter_l;
  const float cpm_r = g_ctx.params.counts_per_meter_r;
  const float track = g_ctx.params.track_width_m;
  const int64_t cl = encL.getCount();
  const int64_t cr = encR.getCount();
  // Signed wheel travel (metres) since the previous tick.
  const float d_l = ENC_SIGN_L * (float)(cl - s_prev_l) / cpm_l;
  const float d_r = ENC_SIGN_R * (float)(cr - s_prev_r) / cpm_r;
  s_dcount_l = (long)(ENC_SIGN_L * (float)(cl - s_prev_l));
  s_dcount_r = (long)(ENC_SIGN_R * (float)(cr - s_prev_r));
  s_prev_l = cl;
  s_prev_r = cr;

  // Floor well below the 100 Hz control period (0.01 s) but above any sane tick,
  // so a pathological dt can't divide-by-zero or amplify encoder noise into the
  // velocity estimate. Position (x/y/theta) integrates from the deltas directly
  // and is unaffected.
  const float inv_dt = (dt > 1e-4f) ? (1.0f / dt) : 0.0f;
  s_vmeas_l = d_l * inv_dt;
  s_vmeas_r = d_r * inv_dt;
  g_ctx.wheels.vl = s_vmeas_l;   // telemetry diag (caller holds the state lock)
  g_ctx.wheels.vr = s_vmeas_r;

  const float d_center = 0.5f * (d_l + d_r);
  // Standard REP-103 heading delta (right wheel ahead of left = turning CCW/left), in
  // lockstep with the standard mixing in hal_drive_velocity, so +d_theta = physical
  // CCW/left and odometry heading tracks the real world (voice/D-pad/autonomous turns).
  // History: this was (d_l - d_r) while the channels were cross-wired — see the note in
  // hal_drive_velocity; flip BOTH together or closed-loop turns break.
  const float d_theta  = (d_r - d_l) / track;

  out.theta += d_theta;
  while (out.theta >  (float)M_PI)  out.theta -= 2.0f * (float)M_PI;
  while (out.theta <= -(float)M_PI) out.theta += 2.0f * (float)M_PI;
  out.x += d_center * cosf(out.theta);
  out.y += d_center * sinf(out.theta);
  out.lin = d_center * inv_dt;
  out.ang = d_theta  * inv_dt;
}

// One wheel's velocity controller lives in drive_shaping.h (wheel_effort):
//   u = KFF*target                      feedforward: ~right duty the instant a speed is
//                                       commanded (no waiting for the integrator)
//     + (floor + boost)*sign(target)    running floor plus a breakaway boost that ramps
//                                       up only while the wheel is stalled
//     + kp*err + integ + kd*deriv       closed-loop trim on the encoder-measured error
// While the body ramps to rest the floor, boost and integrator fade to zero with the
// target, so a stop tapers instead of holding the floor and then short-braking.
// vcomp: battery-voltage feedforward compensation (>= 1.0), V_nominal/V_batt, applied
// before the physical PWM_DUTY_MAX clamp so anti-windup sees the true saturation point.

// Battery-voltage compensation factor for this tick (see calib.h "Battery-voltage
// feedforward compensation"). Unknown or out-of-range pack voltage -> 1.0 (no-op);
// otherwise V_nominal/V_batt clamped to [1.0, BATT_COMP_MAX] so it only ever boosts a
// sagging pack, never trims a fresh one, and never more than the bound.
static float batt_comp_factor() {
  const int16_t mv = g_ctx.batt_mv;   // caller holds the state lock
  if (mv < BATT_COMP_MIN_VALID_MV || mv > BATT_COMP_MAX_VALID_MV) return 1.0f;
  return clampf((float)BATT_COMP_NOMINAL_MV / (float)mv, 1.0f, BATT_COMP_MAX);
}

bool hal_drive_velocity(float lin, float ang, float dt, bool pivot_steer, float pivot_blend,
                        bool stopping) {
  // Caller holds the state lock — read the runtime params as a consistent snapshot.
  const float track = g_ctx.params.track_width_m;
  const float kp = g_ctx.params.kp, ki = g_ctx.params.ki, kd = g_ctx.params.kd;
  const float kff = g_ctx.params.kff, min_duty = g_ctx.params.min_duty;
  // Differential-drive kinematics (REP-103: +lin forward, +ang CCW/left): a CCW/left
  // turn slows/reverses the LEFT wheel and speeds the RIGHT. STANDARD mixing — kept in
  // lockstep with d_theta in hal_read_odom. History (don't re-negate): an earlier build
  // of the base had the drive channels cross-wired to opposite physical sides and
  // compensated by negating the angular term here + in odometry; the base has since been
  // wired straight per pins.h (proven by the per-wheel `wheel` bench test 2026-07-11:
  // PIN_L_* spins the physical left wheel), which made that negation reverse all turning
  // (stick fwd+left drove fwd+right). Per-wheel direction is MOTOR_SIGN_*'s job, not this.
  // The mixing itself, the joystick's spin↔arcade morph and the shared wheel-speed
  // ceiling are the pure mix_wheels in drive_shaping.h. pivot_blend comes from
  // control_tick: the more arcade of the stick's forward fraction and the base's speed.
  float v_l, v_r;
  mix_wheels(lin, ang, track, pivot_steer, pivot_blend, v_l, v_r);
  if (pivot_steer) {   // gamepad: bound how fast a wheel may speed up or reverse (calib.h)
    v_l = wheel_slew_step(s_tgt_l, v_l, GAMEPAD_WHEEL_SLEW_MS2 * dt);
    v_r = wheel_slew_step(s_tgt_r, v_r, GAMEPAD_WHEEL_SLEW_MS2 * dt);
  }
  s_tgt_l = v_l; s_tgt_r = v_r;

  // Energize only when something should move (commanded OR still rolling, so we
  // actively brake a coasting wheel to a stop before disabling it).
  const bool want_move =
      fabsf(v_l) >= WHEEL_STOP_EPS_MS || fabsf(v_r) >= WHEEL_STOP_EPS_MS ||
      fabsf(s_vmeas_l) >= WHEEL_STOP_EPS_MS || fabsf(s_vmeas_r) >= WHEEL_STOP_EPS_MS;
  if (!want_move) { hal_motors_off(); return true; }

  motors_enable(true);
  // Pivot regime: opposite-sign wheel targets mean both tires scrub sideways — a far
  // higher breakaway threshold than rolling (measured: spins stall at duties that move
  // the base fine in a straight line). Floors and breakaway ceilings per regime
  // (context.h):
  //   pivot    -> run floor spin_run_duty, breakaway ceiling spin_breakaway_duty
  //   straight -> run floor min_duty,      breakaway ceiling params.breakaway_duty
  // Arcade-clamped turns floor the inside wheel at 0, so they stay in the straight
  // regime. The boost toward the ceiling ramps only while a wheel is stalled and falls
  // away once it rolls (drive_shaping.h), so the base gets the push that breaks it
  // loose, not a fixed punch on the first tick.
  const bool pivot = (v_l * v_r < -1e-6f);
  const float run_floor = pivot ? fmaxf(min_duty, g_ctx.spin_run_duty) : min_duty;
  const float ceiling = fmaxf(run_floor, pivot ? g_ctx.spin_breakaway_duty
                                               : g_ctx.params.breakaway_duty);
  // PID runs in the forward=+ convention; MOTOR_SIGN_* maps its effort onto each
  // H-bridge, so a wheel that spins backwards is fixed in software, not by rewiring.
  const float gl = g_ctx.params.gain_scale_l, gr = g_ctx.params.gain_scale_r;
  // Battery-voltage compensation: boost duty as the pack sags so a sustained run holds
  // speed instead of drooping (both wheels share the one pack, so one factor).
  const float vcomp = batt_comp_factor();
  const WheelGains g_l{kp*gl, ki*gl, kd*gl, kff*gl, run_floor, ceiling, vcomp};
  const WheelGains g_r{kp*gr, ki*gr, kd*gr, kff*gr, run_floor, ceiling, vcomp};
  const int effort_l = wheel_effort(s_loop_l, v_l, s_vmeas_l, dt, g_l, stopping);
  const int effort_r = wheel_effort(s_loop_r, v_r, s_vmeas_r, dt, g_r, stopping);
  const int duty_l = MOTOR_SIGN_L * effort_l;
  const int duty_r = MOTOR_SIGN_R * effort_r;
  apply_wheel_duty(PIN_L_RPWM, PIN_L_LPWM, duty_l);
  apply_wheel_duty(PIN_R_RPWM, PIN_R_LPWM, duty_r);
  g_ctx.wheels.dl = (int16_t)duty_l;   // telemetry diag (caller holds the state lock)
  g_ctx.wheels.dr = (int16_t)duty_r;
  const bool silent_l = encoder_watch_step(s_watch_l, effort_l, s_dcount_l, dt);
  const bool silent_r = encoder_watch_step(s_watch_r, effort_r, s_dcount_r, dt);
  return !(silent_l || silent_r);
}

void hal_motors_off() {
  pwm_write(PIN_L_RPWM, 0); pwm_write(PIN_L_LPWM, 0);
  pwm_write(PIN_R_RPWM, 0); pwm_write(PIN_R_LPWM, 0);
  motors_enable(false);
  reset_pid();
  g_ctx.wheels.dl = 0; g_ctx.wheels.dr = 0;   // duties off (caller holds the state lock)
}

// Drive ONE wheel's H-bridge at a raw signed fraction of full duty, bypassing BOTH the
// differential kinematics AND the velocity PID — the bring-up diagnostic for "is this
// wheel wired, and does it spin the right way?". Deliberately open-loop so a mis-wired
// or unread encoder cannot corrupt the test (unlike hal_drive_velocity, whose PID would
// fight a bad encoder). side: 0 = left, 1 = right. frac: signed, -1..1; + = that wheel
// FORWARD per its MOTOR_SIGN_* (so a wrong physical direction means the motor leads /
// MOTOR_SIGN are off). The magnitude is floored at the stiction breakaway (params.min_duty)
// so a small frac still turns a free wheel on a stand, and clamped to PWM_DUTY_MAX; the
// OTHER wheel is held off. Caller (control_tick) holds the state lock, so writing
// g_ctx.wheels for telemetry is safe (mirrors hal_drive_velocity).
void hal_drive_wheel_raw(int side, float frac) {
  frac = clampf(frac, -1.0f, 1.0f);
  int duty = 0;
  if (fabsf(frac) > 1e-3f) {
    float mag = fabsf(frac) * (float)PWM_DUTY_MAX;
    if (mag < g_ctx.params.min_duty) mag = g_ctx.params.min_duty;   // stiction breakaway floor
    duty = (int)clampf(mag, 0.0f, (float)PWM_DUTY_MAX);
    if (frac < 0.0f) duty = -duty;
  }
  motors_enable(true);
  if (side == 0) {                                   // LEFT wheel; right held off
    const int d = MOTOR_SIGN_L * duty;
    apply_wheel_duty(PIN_L_RPWM, PIN_L_LPWM, d);
    apply_wheel_duty(PIN_R_RPWM, PIN_R_LPWM, 0);
    g_ctx.wheels.dl = (int16_t)d; g_ctx.wheels.dr = 0;
  } else {                                           // RIGHT wheel; left held off
    const int d = MOTOR_SIGN_R * duty;
    apply_wheel_duty(PIN_R_RPWM, PIN_R_LPWM, d);
    apply_wheel_duty(PIN_L_RPWM, PIN_L_LPWM, 0);
    g_ctx.wheels.dl = 0; g_ctx.wheels.dr = (int16_t)d;
  }
}

// hal_read_tof()/hal_tof_init() live in tof.cpp — they are gated by MOTION_TOF_PRESENT
// independently of the motor drivers (the base can drive before the ToF is wired).

#else
// ===========================================================================
// STUB — no peripherals. Motors are a no-op; ToF reports a clear room so the
// reflex/zone logic stays in CLEAR and nothing blocks. The plant model in
// control.cpp synthesizes odometry from the commanded velocity.
// ===========================================================================
void hal_init() {
  // Nothing to initialize in the stub.
}

void hal_apply_velocity(float lin, float ang) {
  (void)lin;
  (void)ang;   // no motors wired; the plant model integrates these in control.cpp
}
#endif
