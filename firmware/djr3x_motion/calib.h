// calib.h — measured drive-base constants + control gains (Phase 1).
//
// ⚠ THE GEOMETRY VALUES BELOW ARE PLACEHOLDERS, NOT MEASUREMENTS. ⚠
// They make odometry self-consistent but NOT accurate. Distances/angles will be
// wrong until you measure them on the real base (docs/motion_system.md §14):
//   1. Spin one wheel output-rev by hand; confirm COUNTS_PER_REV_OUTPUT.
//   2. Drive a measured 1.0 m straight; scale COUNTS_PER_METER by the error.
//   3. Spin a measured 360°; scale TRACK_WIDTH_M by the error.
// None of these affect safety (motion still needs an explicit command and the
// caps/watchdog/estop are independent) — only how far/how accurately it moves.
//
// The geometry values + the PID gains below are the BOOT DEFAULTS: they seed
// MotionParams and are runtime-overridable via the `config` command
// (counts_per_meter, track_width_m, kp/ki/kd), so you can calibrate + tune live with
// firmware/tools/motion_bench.py WITHOUT reflashing each iteration. Bake the winning
// values back here (or push them from config.py / .env) once you're happy.
#pragma once
#include <math.h>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ---- Drive geometry (MEASURE — see above) --------------------------------
// Drivetrain since 2026-11-12 (owner rebuild): two goBILDA 5203 Yellow Jacket
// planetary gear motors, 50.9:1, 117 rpm no-load at 12 V, with 72 mm drive wheels.
// The built-in encoder gives 28 counts per motor revolution in full quadrature, so
// 28 x 50.9 = 1425.1 counts per wheel revolution (goBILDA's published figure).
// The values below are COMPUTED from those numbers, not yet taped on the floor.
// History: the JGB37 60 rpm motors with 100 mm wheels measured 17000 counts/m; that
// value left in place under these motors made every measured speed read 0.37x true,
// so any stick command saturated both wheels (owner: "either really fast or it stops").
#define COUNTS_PER_REV_OUTPUT  1425.1f
#define WHEEL_DIAMETER_M       0.072f
// EMPIRICAL turn calibration from the previous drivetrain (2026-07-21: 0.297 turned
// 270 deg for a commanded 180, so 0.4455). The effective track includes tyre scrub.
// ⚠ Not re-measured since the rebuild: check with a taped 360 deg turn on hardwood.
#define TRACK_WIDTH_M          0.446f

#define WHEEL_CIRCUM_M   ((float)M_PI * WHEEL_DIAMETER_M)
// 1425.1 / (pi x 0.072 m) = 6300 counts per metre. ⚠ Verify: roll the base a taped
// metre by hand and compare the odometry (runtime keys counts_per_meter_l / _r; the
// legacy counts_per_meter key sets both).
#define COUNTS_PER_METER_L 6300.0f
#define COUNTS_PER_METER_R 6300.0f

// Per-wheel count direction. +1 means "driving the wheel forward makes its count
// increase." Flip to -1 (per wheel) if the bench hand-turn test shows the sign
// backwards, instead of rewiring A/B.
// ⚠ 2026-11-12: signs below are the previous motors'. The Yellow Jackets drive the right
// way from the gamepad (owner), so MOTOR_SIGN_* stand; the ENCODER signs are unverified
// until a wheel is turned with telemetry watched (forward travel must count positive).
#define ENC_SIGN_L  (-1.0f)   // flipped 2026-07-17: replacement 60RPM left motor is
                              // mirror-wired vs the original (bench `wheel left`:
                              // + counts but physically BACKWARD — flip BOTH signs)
#define ENC_SIGN_R  (+1.0f)   // 2026-07-20: right motor replaced with the 60RPM SKU. The new
                              // unit reads/drives inverted vs the old right, so flip to +1
                              // (opposite the left's -1: identical motor mounted mirror-image).
                              // Encoder + motor flipped BOTH in lockstep; bench-verify `wheel right`.

// Per-wheel MOTOR direction. +1 means "a positive (forward) duty spins the wheel
// forward." Flip to -1 (per wheel) — the software equivalent of swapping that motor's
// two power leads — if the bench `spin` test runs away / auto-estops or the wheel spins
// BACKWARD on a forward command. MUST agree with ENC_SIGN_* (forward duty -> forward
// travel -> +counts): a mismatch makes the velocity PID positive feedback and trips the
// runaway guard. Fixing direction HERE keeps each channel paired with its own encoder,
// so it does NOT desync odometry the way swapping only the motor leads does.
#define MOTOR_SIGN_L  (-1)    // flipped in lockstep with ENC_SIGN_L (see above)
#define MOTOR_SIGN_R  (+1)    // 2026-07-20: flipped in lockstep with ENC_SIGN_R (60RPM right-motor
                              // swap). Bench-verify with `spin` / `wheel right` — forward duty must
                              // spin FORWARD and not trip the runaway guard.

// ---- Motor PWM (LEDC) -----------------------------------------------------
// 20 kHz is above audible and well within the BTS7960's switching range.
// (Weak-torque investigation 2026-07-12: a 5 kHz experiment produced IDENTICAL
// behavior — same ~1.8 A pack draw at dual near-full duty, same non-rotation —
// so PWM gate-drive loss is RULED OUT. The measured signature — full-duty motor
// current nearly invisible at the pack shunt, no voltage sag — points at the
// BTS7960 B+/B- supply not actually coming from the main pack path; hardware
// trace pending.)
#define PWM_FREQ_HZ    20000
#define PWM_RES_BITS   10                 // 0..1023 duty
#define PWM_DUTY_MAX   ((1 << PWM_RES_BITS) - 1)

// ---- Per-wheel velocity PID (target m/s → duty) ---------------------------
// ⚠ Starting gains — tune on the bench (docs §14.3): raise KP until the wheel
// tracks a step without buzzing, add KI to kill steady-state error, keep KD ~0
// unless it's oscillating. Units: duty per (m/s) of error.
// RESCALED ÷4.09 on 2026-07-11 when the cpm calibration made target/measured
// speeds REAL (they had been 4.09× inflated): the old gains were duty per
// INFLATED m/s, so keeping them would have quadrupled the loop gain overnight
// (overshoot/oscillation). Anchor: at the old FULL, kff put ~455 duty on the
// wheels for a true 0.716 m/s → plant ≈ 635 duty per real m/s.
// PER-WHEEL GAIN SCALES multiply kp/ki/kff (runtime keys gain_scale_l / _r) so the base
// gains stay in one unit while the motors change. They track the plant: full duty over
// no-load wheel speed. 60 rpm JGB37 on 100 mm wheels: 1023 / 0.31 m/s = 3300 duty per
// m/s = 5.2 x the 640 base. Yellow Jacket 117 rpm on 72 mm wheels: 0.44 m/s at 12 V,
// about 0.47 m/s at the 12.8 V the feedforward assumes, so 1023 / 0.47 = 2180 = 3.4 x.
// Computed 2026-11-12; trim on the floor if a steady run sits above or below its command.
#define WHEEL_GAIN_SCALE_L  3.4f
#define WHEEL_GAIN_SCALE_R  3.4f
#define WHEEL_PID_KP      440.0f
#define WHEEL_PID_KI      220.0f
#define WHEEL_PID_KD        0.0f
// Anti-windup: the integral's duty contribution is clamped to this so it can't
// accumulate while the motor is saturated/stalled.
#define WHEEL_PID_I_CLAMP  (0.8f * PWM_DUTY_MAX)

// ---- Velocity feedforward + stiction compensation -------------------------
// The PID alone starts every move from ZERO duty and only reaches a useful duty
// once the integrator winds up — so low speeds sit below breakaway friction (weak
// + slow to start) and duty scales with speed (strong only when fast). Two terms
// fix that mechanically:
//   KFF  — feedforward duty per commanded m/s. The instant a speed is commanded the
//          wheel gets ~the right duty, so the loop only trims instead of building
//          from nothing. Start ≈ 0.9 * PWM_DUTY_MAX / max_lin (so the top commanded
//          speed maps to ~90% duty, leaving PID headroom). Tune on the bench.
//   MIN_DUTY — a fixed breakaway "kick" added in the travel direction whenever a
//          nonzero speed is commanded, to clear static friction on a heavy base.
//          Raise until the wheel starts moving crisply at creep; lower if it lurches.
#define WHEEL_PID_KFF     640.0f    // duty per REAL m/s of COMMAND (was 2600 in the old
                                    // 4.09×-inflated units; 640 ≈ the measured plant gain)
// 2026-11-12, Yellow Jackets: the floors and breakaway ceilings below are ESTIMATES scaled
// from the old motors' measured ones. A Yellow Jacket stalls at about 6.7 N·m (186 N at a
// 36 mm wheel radius) against roughly 60 N for a 60 rpm JGB37 on a 50 mm radius: three
// times the push per unit of duty, so the same friction needs about a third of the duty.
// Left at the old values the floor alone (22% duty) free-ran a wheel at 0.10 m/s the
// moment any speed was commanded. Old measured values: floor 230, straight breakaway 500,
// pivot floor 380 / kick 700-750 (hardwood), 650 / 1023 (carpet). If a start hums without
// moving, raise the ceiling for that case; the ramped boost only uses what it needs.
#define WHEEL_MIN_DUTY     80.0f    // running duty floor (duty), in travel dir — applies
                                    // while the wheel is ROLLING. MEASURED on the
                                    // full-weight base (2026-07-12): sustained creep
                                    // needs ~240 total duty; at the old 120 the wheel
                                    // re-stalled one tick after breakaway (100 Hz
                                    // stick-slip chatter, "only moves at full throttle")
// ---- Battery-voltage feedforward compensation -----------------------------
// Duty is a fraction of PACK voltage: applied_volts = (duty/PWM_DUTY_MAX)*V_batt.
// KFF/MIN_DUTY/breakaway above are all calibrated in DUTY at a healthy pack, so as
// a LiFePO4 pack sags under sustained drive current (and worse across the ~160mOhm
// junction) the SAME duty delivers fewer volts -> the wheels slow, and near the top
// of the range the ~90% feedforward has too little PID headroom left to claw it back
// (the "full-speed run audibly slows over time" report, 2026-07-20). Compensation
// scales the commanded duty by V_NOMINAL/V_batt so effective volts stay constant as
// the pack droops — bounded so a bad/absent sensor can only ever be a no-op or a
// modest, physically-clamped boost.
//   • batt_mv unknown (-1, no INA226) or outside [MIN,MAX]_VALID -> factor = 1.0 (off).
//   • Factor is clamped to [1.0, COMP_MAX]: only ever BOOSTS a sagging pack, never
//     trims a fresh one (keeps present full-charge top-speed feel unchanged).
//   • The boost is still hard-limited by PWM_DUTY_MAX in wheel_effort — 100% duty is the
//     physical ceiling; comp only recovers headroom BELOW it, it can't exceed the pack.
#define BATT_COMP_NOMINAL_MV   12800   // LiFePO4 4S nominal; the voltage KFF assumes
#define BATT_COMP_MAX          1.30f   // never boost duty more than +30%
#define BATT_COMP_MIN_VALID_MV 9000    // below this, treat the reading as a glitch -> off
#define BATT_COMP_MAX_VALID_MV 16000   // above this, treat the reading as a glitch -> off

// STRAIGHT-drive breakaway CEILING (owner 2026-07-12: the full-weight robot needs a
// substantial duty to leave a dead stop — below it, low commands just hummed; 35% was
// MEASURED marginal, ~49% breaks away decisively). Since 0.2.10 this is the most the
// ramped breakaway boost may reach (drive_shaping.h), not a step applied on the first
// commanded tick: a stalled wheel climbs from WHEEL_MIN_DUTY toward it at
// WHEEL_BOOST_RISE_DUTY_S and gives the excess back once it rolls. Runtime param
// (breakaway_duty); this is the boot default.
#define WHEEL_STRAIGHT_BREAKAWAY_DUTY 200.0f
// PIVOT (spin-in-place) breakaway CEILING: when the two wheel targets have OPPOSITE
// signs the base is scrubbing both tires sideways — a far higher static threshold than
// rolling, and higher again on carpet. The ramped boost (drive_shaping.h) climbs toward
// this only while a wheel is measurably stalled and falls away once it rolls; a constant
// kick this large would massively overspeed the spin after breakaway (equilibrium
// ~5 rad/s vs a ~1.5 rad/s target). The surface modes override it (GAMEPAD_*_SPIN_KICK).
// Owner 2026-10-23: the old step to this value on the first tick, held 0.12 s past
// breakaway, was "too much for turning".
// Field ladder (2026-07-11): the old "turning worked" hardwood baseline was ~780
// stalled duty (the inflated-units loop delivered it unknowingly); this carpet
// SATURATES at 1023 duty with ~zero rotation — in-place spins on that carpet exceed
// the platform's torque ceiling, full stop; use moving arcs there instead.
#define WHEEL_SPIN_BREAKAWAY_DUTY 320.0f
// Pivot RUNNING floor (field fix, same day): stall-gating alone made spins twitch —
// the kick broke the wheels loose, dropped to WHEEL_MIN_DUTY (120) one tick later,
// and the SUSTAINED sideways scrub of a pivot re-stalled them (stick-slip grind;
// the integrator needed seconds to build the missing duty). While pivoting, the floor
// sits here instead of at WHEEL_MIN_DUTY, carrying the scrub load; the per-surface
// profiles (GAMEPAD_*_SPIN_RUN) override this base at mode-apply. A stop fades it out.
#define WHEEL_SPIN_RUN_DUTY       150.0f
#define WHEEL_STALLED_EPS_MS      0.03f   // wheel counts as stalled below this (m/s);
                                          // ~1.9 encoder counts per 10 ms tick — resolvable

// A wheel target below this (m/s magnitude) counts as "stopped" → duty zero and the
// controller state reset rather than chasing micro-setpoints. A stop reaches it with
// the floor, boost and integrator already faded out (drive_shaping.h wheel_stop_fade).
#define WHEEL_STOP_EPS_MS  0.01f

// Ramped breakaway boost (drive_shaping.h, owner 2026-10-23). A wheel that has sat
// still for WHEEL_STALL_DEBOUNCE_S while commanded gains duty above its running floor
// at WHEEL_BOOST_RISE_DUTY_S, up to the breakaway ceiling; once it moves the boost falls
// at WHEEL_BOOST_FALL_DUTY_S. Gear lash that twitches a planted wheel only pauses the
// rise. Hardwood pivot: floor 380 -> ceiling 750 takes 0.15 s if the base never moves,
// and the commanded speed's feedforward climbs alongside, so it breaks loose at about
// the duty it needs instead of a 75% step on the first tick.
#define WHEEL_STALL_DEBOUNCE_S    0.03f
#define WHEEL_BOOST_RISE_DUTY_S   2500.0f   // duty per second while stalled
#define WHEEL_BOOST_FALL_DUTY_S   2500.0f   // duty per second once rolling or stopping

// 117 rpm on a 72 mm wheel is 0.44 m/s unloaded at 12 V. Keep mixed wheel-speed
// requests just below that physical ceiling and scale both sides together so a
// combined forward+turn command preserves curvature instead of saturating only the
// outside wheel. (Was 0.90 for the original 176 rpm / 100 mm drivetrain.)
#define WHEEL_TARGET_MAX_MS      0.42f

// ---- Encoder watch (hal.cpp, drive_shaping.h encoder_silent_step) -----------
// A wheel driven at or above ENC_SILENT_DUTY whose encoder has not moved a single count
// for ENC_SILENT_S is not being measured: an unplugged or unpowered encoder, a broken
// lead, or a jammed wheel. The speed loop would otherwise read "stopped", wind its
// integrator and run that wheel to full duty whatever the stick asks. The base cuts the
// motors and raises fault encoder_stall instead (Start on the pad or `clear` releases).
// 45% duty is well past anything that leaves a free Yellow Jacket wheel still. The
// single-wheel `wheel` bring-up jog is not watched: it exists to test an unproven encoder.
#define ENC_SILENT_DUTY   450
#define ENC_SILENT_S      0.5f

// ---- Drive setpoint slew (teleop feel) ------------------------------------
// Acceleration limit applied to every normal drive setpoint so the base
// ramps smoothly toward the stick command in BOTH directions — symmetric, so a
// released stick coasts to a stop over ~(speed/accel) seconds instead of slamming
// to zero and dynamic-braking. Autonomous finite move/turn/come share the ramp.
// Softened repeatedly after field tests ("takes off too fast" x3), then RESCALED
// 2026-07-11 when units became real. The old odometry units DEFLATED motion (lin
// ÷4.09, ang ÷2.75), so the field-approved feel in PHYSICAL units was: lin 0.2×4.09
// ≈ 0.8 m/s², ang 4.0×2.75 ≈ 11 rad/s². lin carries over exactly; ang is set a bit
// under the old physical value (8 < 11) — it was likely friction-limited anyway.
// (First pass wrongly set ang to 1.5 — a backwards conversion — and spins ramped
// like molasses, part of the "left/right has no power" report.)
// Tune with `set --accel-lin` / `--accel-ang` (higher = snappier).
// 2026-11-12 (owner: the pad was far too sensitive and turns jerked): these boot values
// are what the gamepad gets whenever no host has pushed its own (the V3 program pushes
// 0.35 / 2.0). Was 1.2 / 8.0: full speed in 0.2 s and a full spin rate in half a second.
// Now 0 to 0.25 m/s takes 0.6 s and 0 to 1.2 rad/s takes 0.5 s; stops use the same ramp.
#define DRIVE_ACCEL_LIN    0.40f    // m/s^2  (teleop linear setpoint slew, REAL units)
#define DRIVE_ACCEL_ANG    2.5f     // rad/s^2 (teleop angular setpoint slew, REAL units)
// (The old DRIVE_SPIN_LIN_EPS binary spin gate is gone — replaced by the smooth
// GAMEPAD_SPIN_BLEND_FWD_LO/HI band above; the blend factor rides the setpoint.)

// ---- ToF subsystem (8 radial sensors) — only used when MOTION_TOF_PRESENT==1 -
// Mounted at the 540 mm base-ring surface, every 45° starting 22.5° off the forward
// axis: 4× short-range VL53L0X on mux ch 0-3 (LEFT pair lf/lb + RIGHT pair rf/rb) +
// 4× long-range VL53L1X on mux ch 4-7 (FRONT pair fl/fr + REAR pair rl/rr).
// Requires the TCA9548A mux (8 sensors > free XSHUT GPIOs). docs §6.
#define TOF_SHORT_COUNT       4         // VL53L0X (short), mux ch 0..3 — left/right pairs
#define TOF_LONG_COUNT        4         // VL53L1X (long),  mux ch 4..7 — front/rear pairs
#define TOF_COUNT             (TOF_SHORT_COUNT + TOF_LONG_COUNT)   // 8 total
#define TOF_MUX_ADDR          0x70      // TCA9548A I²C address (mux selects one ch at a time)
// Per-read wait for a fresh continuous sample. MUST exceed the slowest sensor's
// inter-measurement period (L1X, below) or a live-but-slow sensor reads as -1 while
// we wait for its next sample. Dead sensors are skipped (s_ok), so this only bounds
// a genuinely stuck live sensor.
#define TOF_TIMEOUT_MS        100
// A sensor whose reads keep failing must not FREEZE its last-good distance forever —
// a sensor that dies while reading "clear" would silently disable the stop reflex in
// its direction (an undetectable safety hole). After this many CONSECUTIVE failed
// reads (each sensor is revisited every ~80 ms, so 8 ≈ 0.6 s) the published distance
// drops to -1: an honest error the GUI radar/telemetry can show. safety.cpp still
// fails OPEN on -1 by documented choice (min2_valid skips it; the pair partner covers).
#define TOF_ERR_STREAK_STALE  8

// Fast-attack / slow-release output filter (field fix 2026-07-11): the published
// distance drops to a NEARER reading INSTANTLY (danger is never filtered) but rises
// toward a farther one at most this many mm per revisit (~80 ms/sensor). The long
// sensors sit 45° apart pointing outward, so a narrow obstacle (chair leg) at a
// beam's edge strobed 0.5 m <-> 4 m as it entered/left the cone — flapping the GUI
// radar, the steering assist, AND the stop reflex (BLOCKED released on each "clear"
// frame and the base lurched forward). With the filter the close return HOLDS and
// decays smoothly: ~300 mm/poll ≈ 3.7 m/s of release, a cleared wall reads fully
// open again in ~1 s.
#define TOF_RELEASE_STEP_MM       300

// Glitch confirmation (tof_filter.h, field data 2026-07-21): a reading that
// drops the published distance by more than ATTACK_DROP in one frame is only
// believed when the NEXT frame agrees within ATTACK_CONFIRM — kills the
// single-frame phantom near returns (VL53L1X wrap-around, VL53L7CX speckle)
// that sawtoothed fl/fr/rr between 1 m and 4 m in an empty room. Costs one
// sensor revisit (~75-80 ms) of detection latency on a genuinely sudden
// obstacle; smooth approaches (< DROP_MM per frame) still attack instantly.
#define TOF_ATTACK_DROP_MM        400
#define TOF_ATTACK_CONFIRM_MM     300

// Front-right radial only: false close bursts survived the two-frame guard.
// At the measured 80 ms cadence, 300 ms needs five agreeing fresh readings.
// Ordinary smooth approaches still attack immediately. Reject invalid frames
// as evidence of clearance; require a short clear run before a large release.
#define TOF_FR_NEAR_CONFIRM_MS       300
#define TOF_FR_CLEAR_CONFIRM_MS      160
#define TOF_FR_SAMPLE_GAP_MS         200
#define TOF_FR_CONFIRM_TOLERANCE_MM  200
#define TOF_FR_NEAR_MM              300
#define TOF_FR_NEAR_DROP_MM         100

// VL53L0X (short range, ~1.2 m reliable):
#define TOF_L0X_TIMING_BUDGET_US  33000 // 33 ms measurement budget (speed vs accuracy)
#define TOF_L0X_OUT_OF_RANGE_MM   2000  // clamp "nothing in range" to a far/clear value

// VL53L1X (long range): Long mode reaches ~4 m; needs a larger timing budget than L0X.
// The inter-measurement period must be >= the timing budget (+overhead), else the
// sensor won't produce readings (datasheet); 60 ms > 50 ms budget satisfies that.
#define TOF_L1X_TIMING_BUDGET_US  50000 // 50 ms budget (Long mode wants >= ~33 ms)
#define TOF_L1X_INTERMEASUREMENT_MS 60  // continuous-mode period (> timing budget)
#define TOF_L1X_OUT_OF_RANGE_MM   4000  // clamp "nothing in range" to a far/clear value
// Per-sensor crosstalk compensation (VL53L1X, order fl, fr, rl, rr), kcps per SPAD;
// 0 = untouched. Measured 2026-10-10 on the front-right channel with the tof_debug
// stream: with nothing in front it reported a steady 31-53 mm "target" of 6.5-6.9 MCPS
// over all ~212 SPADs (31-33 kcps/SPAD), scene-independent, 75-85% PHASECONSISTENCY
// (wrap) failures. A hand at 25-32 cm read 67-120 kcps/SPAD. A real surface 4 cm from
// the lens would return hundreds of times more than a wall at 1.2 m (front-left: 14
// kcps/SPAD); this is light returned at the lens itself. The plane offset is subtracted
// by the sensor, and ALGO__RANGE_IGNORE_THRESHOLD_MCPS (within the 255 mm valid
// height the Pololu init sets) drops a near target weaker than RIT_MULT x the
// crosstalk as RANGEIGNORETHRESHOLD (status 12), which tof.cpp reads as no target
// near. 1.25 stays under the weakest hand frame; a very dark object inside 25 cm in
// this one beam could be dropped (the front matrix covers the centre of that cone).
// 2026-10-19: the owner is replacing that faulty front-right module and channel 5 is
// re-enabled. Crosstalk is a property of one module's lens, so the new module starts
// uncompensated (0). If it shows the same steady near "target" in the tof_debug
// stream, measure it again rather than reusing 32.
#define TOF_L1X_XTALK_KCPS_PER_SPAD { 0.f, 0.f, 0.f, 0.f }
#define TOF_L1X_XTALK_RIT_MULT    1.25f

// ---- 8x8 Matrix ToF (DFRobot SEN0628 — MOTION_TOF_MATRIX_PRESENT==1) --------
// Front-mounted, level, VL53L7CX behind an onboard RP2040 (tof_matrix.cpp).
// Range 20-3500 mm, 45°x45° FOV (63° diagonal), 8x8 = 15 Hz max on the sensor.
#define TOF_MATRIX_ADDR            0x33  // DIP-strapped I2C address (bench-confirmed)
#define TOF_MATRIX_FRAME_INTERVAL_MS 75  // ~13 Hz poll (sensor tops out at 15 Hz in 8x8)
#define TOF_MATRIX_READ_TIMEOUT_MS   30  // hard deadline for one frame read — a slow/
                                         // wedged RP2040 costs at most this per attempt
#define TOF_MATRIX_MODE_ACK_TIMEOUT_MS 8000 // SETMODE ack wait (init task only). The
                                         // RP2040 only acks AFTER the ~5 s VL53L7CX
                                         // reconfigure; a short window here re-sends
                                         // SETMODE into a busy sensor, which wedges
                                         // the I2C trunk and hangs the whole firmware
                                         // (field-observed 2026-07-16). Must exceed
                                         // the reconfigure time; vendor lib polls 8 s.
#define TOF_MATRIX_MODE_SETTLE_MS  5200  // VL53L7CX reconfigure settle (vendor lib uses 5000)
#define TOF_MATRIX_STALE_MS         500  // publisher silence past this = poll task dead
                                         // (I2C wedge) -> fl/fr report -1, robot stays alive
//
// FLOOR REJECTION geometry — the sensor sits above the floor, so the lower rows
// permanently see floor at short range (h=0.15 m: bottom row ≈ 445 mm along-ray).
// Per-row expected floor distance = HEIGHT / sin(row angle below horizon); readings
// at/beyond FLOOR_TOLERANCE of it are floor (clear), meaningfully shorter = obstacle.
// ⚠ MEASURE TOF_MATRIX_HEIGHT_M on the robot: lens centre to floor, metres. If unsure
// err HIGH — too-high reads the empty floor as an obstacle (nuisance block, obvious);
// too-low classifies real low obstacles as floor (missed, silent).
#define TOF_MATRIX_HEIGHT_M       0.095f // EFFECTIVE optical height — empirically
                                         // calibrated 2026-07-16, RE-calibrated
                                         // 2026-08-11 after the SEN0628 was replaced.
                                         // Tape-measured lens height is 0.16 m, but
                                         // oblique ToF returns under-range at grazing
                                         // angles; the effective height absorbs that.
                                         // Was 0.11 (that module under-read ~0.70x,
                                         // open floor row7≈330/row6≈460 mm). THIS
                                         // module under-reads harder — ~0.59x, open
                                         // floor row7≈286 (min 236) / row6≈386 (min
                                         // 309) mm over 136 frames — which back-solves
                                         // to 0.096 from row7 and 0.094 from row6.
                                         // At 0.11 the row-6 reject (362 mm) sat INSIDE
                                         // the floor's own spread, so 12% of row-6
                                         // returns read as a 340-460 mm obstacle and
                                         // the matrix published a phantom front-left
                                         // block in ~half of all frames while parked
                                         // on open floor. Recalibrate if the mount
                                         // height moves OR the module is swapped again:
                                         // capture the raw grid on empty floor and set
                                         // this to mean(row_mm) * sin(row elevation).
#define TOF_MATRIX_PITCH_DEG       0.0f  // mount pitch trim (+ = tilted up); level = 0
#define TOF_MATRIX_VFOV_DEG        45.0f // VL53L7CX vertical FOV (45° square per ST)
#define TOF_MATRIX_FLOOR_TOLERANCE 0.80f // reading >= this fraction of expected floor = floor
#define TOF_MATRIX_FLOOR_MIN_MM    250   // never call anything closer than this "floor"
// Normalized rows, top to bottom. 0 retains the geometric threshold above.
// Owner-confirmed empty-floor capture 2026-10-10: 53 frames, row 6 valid
// floor returns 81-489 mm, row 7 80-284 mm (zero-based). The old 250 mm
// lower bound made these floor returns obstacles. 70 mm leaves 10 mm below
// the observed minimum; only these two downward rows use this calibration.
// Objects whose returns overlap that floor range in these rows are inseparable
// from floor; upper rows retain their existing obstacle thresholds.
#define TOF_MATRIX_FLOOR_ROW_MM { 0, 0, 0, 0, 0, 0, 70, 70 }
#define TOF_MATRIX_MIN_MM          25    // below sensor min range = speckle, ignore
#define TOF_MATRIX_CLEAR_MM        3500  // "nothing in range" clear value (sensor max)
//
// Orientation — normalize the raw grid so row 0 = physically TOP and column 0 = the
// ROBOT'S LEFT edge of the FOV. ⚠ VERIFY ON THE BENCH with tools/tof_matrix_gui.py:
// (1) tilt the module down — if the NEAR readings move to the TOP rows, set FLIP_V 1;
// (2) hold a hand at the robot's front-LEFT — if fr (not fl) drops, set FLIP_H 1.
#define TOF_MATRIX_FLIP_V          0     // 1 = raw row 0 is physically the BOTTOM
#define TOF_MATRIX_FLIP_H          0     // 1 = raw col 0 is the robot's RIGHT
// Speckle rejection (field 2026-08-01): a parked base flapped BLOCKED/clear ~600
// times in 7 min with >1 m genuinely clear ahead — single-zone phantom near returns
// (VL53L7CX speckle) dipped under the at-rest 0.10 m stop floor, and the two-frame
// attack confirm passed them because consecutive speckle frames agree within
// ATTACK_CONFIRM. A real obstacle near enough to matter subtends MULTIPLE zones of
// the 4x8 half-grid (a chair leg at 0.3 m spans several rows even if one column),
// so each half now publishes its SECOND-nearest qualifying zone: one lone screaming
// zone is discarded as speckle. Deliberate coverage trade: an object thin enough to
// light exactly one zone (a bare cable end-on) is invisible — docs §6.3 already
// disclaims full coverage of thin obstacles.
#define TOF_MATRIX_MIN_OBSTACLE_ZONES 2  // qualifying zones per half before it counts
//
// ---- REAR 8x8 Matrix ToF (owner 2026-10-19) ----------------------------------
// An identical SEN0628 at the front module's height, facing straight back (180°
// from the front, same way up), sharing the front's private bus (GPIO4/5) at its
// own DIP address. Optional: absent, it is probed once a second and rl/rr stay
// radial-only. Orientation is in the SENSOR'S frame like the front (row 0 = top,
// col 0 = the sensor's left = the robot's rear-RIGHT); tof_matrix_read_rear()
// maps the halves to rl/rr. Height and floor rows start as copies of the front's
// calibration. ⚠ The effective height is per-module (two front modules under-read
// at 0.70x and 0.59x): capture this module's empty-floor grid after mounting and
// recalibrate exactly as the front was (Oct 10), then set its own values here.
#define TOF_MATRIX_REAR_ADDR        0x32  // DIP-strapped; must differ from TOF_MATRIX_ADDR
#define TOF_MATRIX_REAR_HEIGHT_M    TOF_MATRIX_HEIGHT_M      // uncalibrated copy
#define TOF_MATRIX_REAR_FLOOR_ROW_MM TOF_MATRIX_FLOOR_ROW_MM // uncalibrated copy
#define TOF_MATRIX_REAR_FLIP_V      TOF_MATRIX_FLIP_V        // same module, same way up
#define TOF_MATRIX_REAR_FLIP_H      TOF_MATRIX_FLIP_H
#define TOF_MATRIX_REAR_EMIT_EVERY  3     // stream every 3rd frame (~4.4 Hz) to the host

// ---- Hallway steering assist (manual forward drive) ------------------------
// While the gamepad commands FORWARD, the base steers itself away from walls using
// the side short-range pairs (lateral clearance) plus the front long pair
// (anticipatory: approaching a wall at an angle steers toward the open side). Sized
// for a typical US hallway (~915-1220 mm) around the 540 mm base ring: centered in a
// 1 m hall each side reads ~230 mm, well inside ENGAGE, so both walls are "felt" and
// the correction centers the base. Walls beyond ENGAGE are ignored (open rooms drive
// exactly as before). The operator's stick adds on top; the correction itself is
// capped at ASSIST_MAX_ANG_FRAC of max_ang so the human always has override
// authority; the Z_STOP reflex still hard-blocks a head-on wall regardless.
#define ASSIST_ENGAGE_MM      450.0f    // walls farther than this don't steer
#define ASSIST_GAIN           2.0f      // rad/s per METER of left-right imbalance
#define ASSIST_FRONT_ENGAGE_MM 900.0f // front avoidance for forward motion
#define ASSIST_FRONT_WEIGHT   0.7f      // front-pair contribution vs the side pairs
#define ASSIST_MAX_ANG_FRAC   0.6f      // correction cap as a fraction of max_ang
#define ASSIST_MIN_LIN_MS     0.02f     // assist only while actually driving forward
// Close-wall REPULSION (field fix 2026-07-11: base scraped hallway walls the pure
// imbalance term couldn't prevent — centered in a too-narrow gap the left/right
// difference reads ~zero, so no steer, and an equally-close pair got NO correction
// at all). A side wall inside REPEL (~5 in) now pushes back hard on its own,
// proportional to penetration, independent of the other side; both close -> net
// push away from the NEARER wall. Rides the same ASSIST_MAX_ANG_FRAC cap, so the
// operator keeps override authority.
#define ASSIST_REPEL_MM       130.0f    // ~5 in: a side wall inside this repels hard
#define ASSIST_REPEL_GAIN     12.0f     // rad/s per METER of penetration inside REPEL

// ---- Physical turn verification (LSM6DS3 gyro + encoder fallback) ----------
// Encoder odometry alone cannot distinguish chassis rotation from tyres scrubbing
// in place. When the IMU is healthy, finite turns close on integrated gyro yaw
// instead. Encoder angle remains the fallback when the IMU is absent. A verified
// turn that cannot make physical yaw progress is aborted after a generous,
// command-size-aware timeout instead of grinding indefinitely.
#define TURN_IMU_VERIFY_ENABLED       1
#define TURN_VERIFY_TIMEOUT_MIN_MS 8000
#define TURN_VERIFY_TIMEOUT_MULT    3.0f
#define TURN_VERIFY_TOLERANCE_DEG   1.5f
// Finite turns decelerate INTO the target (v = sqrt(2*a*remaining), the angular
// twin of the linear slow-zone taper). Without it the turn ran at full rate until
// the yaw crossed the threshold, `done` fired, and the accel_ang ramp-down coasted
// v^2/(2a) PAST it — a 90° voice turn at 75°/s (accel_ang 2.0) landed ~115°, and a
// 15° trim nearly doubled (field 2026-08-11). The floor keeps the tapered pivot
// above stick-slip: ~20°/s keeps both wheels well clear of WHEEL_STALLED_EPS_MS,
// and the stall-gated spin kick + WHEEL_SPIN_RUN_DUTY carry the scrub load.
#define TURN_DECEL_MIN_RAD_S        0.35f

// (The stalled-pivot "wiggle" assist that lived here — auto-converting a stalled
// pivot into alternating rolling arcs — was removed 2026-07-13 at the owner's
// request: turns should just be turns. If a pivot stalls under full weight,
// that's the torque ceiling talking; the fix is mechanical, not choreography.)

// ---- Battery sense (INA226, battery.cpp) -----------------------------------
// Shunt resistance in MICRO-ohms for the current (batt_ma) reading; 0 disables
// current and reports voltage only. AS BUILT (2026-07-11): an external R002
// (2 mΩ, 20 A) shunt inline in the aggregate battery NEGATIVE lead — pack− →
// shunt → fuse block → devices — with the module's IN+/IN− sensed across it
// (low-side) and VBUS at the fuse block's positive. 2 mΩ across the INA226's
// ±81.9 mV input = ±41 A range at ~1.25 mA/LSB. (First bring-up ran with the
// stock-module R100 value 100000 here — every reading was ÷50.)
#define BATT_SHUNT_MICROOHM  2000
// Sense polarity: +batt_ma = DISCHARGING (protocol §6.1). The as-built IN+/IN−
// orientation reads discharge negative, so flip in software rather than rewire.
#define BATT_CURRENT_SIGN    (-1)

// ---- Battery gauge (coulomb counter + LiFePO4 voltage anchors) --------------
// SOC = coulomb-counted discharge against pack capacity, persisted to ESP32 NVS
// so it survives power-off (the ESP32 is USB-powered from the Mac — it goes DARK
// while the pack charges, and the charger clips to the pack terminals so charge
// current never crosses the shunt). Every boot therefore RECONCILES against rest
// voltage: >= FULL_ANCHOR at rest -> 100% (the normal off->charge->on cycle
// self-corrects); on the flat plateau -> trust the saved ledger; below a knee ->
// clamp DOWN (never over-promise). 4S LiFePO4 rest-voltage knees; "rest" = no
// motor drive (idle electronics ~1 A = C/40 on this pack, sag is negligible).
#define BATT_CAPACITY_MAH        40000   // 2x 12.8 V 20 Ah in parallel
#define BATT_SOC_FULL_ANCHOR_MV  13350   // at/above this at rest = charged full
#define BATT_SOC_KNEE1_MV        12900   // below: clamp SOC to <= KNEE1_PCT
#define BATT_SOC_KNEE1_PCT       20
#define BATT_SOC_KNEE2_MV        12500   // below: clamp SOC to <= KNEE2_PCT
#define BATT_SOC_KNEE2_PCT       8
#define BATT_SOC_QUIET_MA        1500    // |current| below this counts as "rest".
                                         // 2500 counted Rex TALKING/GESTURING (~2 A)
                                         // as rest; with the pack's ~160 mΩ junction
                                         // that load sags 12.9 V-looking readings out
                                         // of a healthy plateau -> phantom knee clamp
                                         // (field 2026-07-17: 85% -> 20% mid-speech).
                                         // 1500 still covers idle electronics (~1.3 A).
#define BATT_SOC_ANCHOR_TICKS    20      // consecutive quiet 1 Hz ticks before anchoring
#define BATT_PACK_IR_MOHM        160     // effective pack+junction resistance: rest
                                         // voltage is estimated as mv + ma*IR so the
                                         // sag under idle draw doesn't skew anchors
                                         // (measured ~160 mΩ, 2026-07 charging bench)
#define BATT_SOC_SAVE_DELTA_MAH  200     // persist to NVS every 0.5% of capacity...
#define BATT_SOC_SAVE_SECS       600     // ...or at least every 10 min (NVS wear-safe)

// ---- Charging lockout (battery.cpp detect, control.cpp gate) ----------------
// Plugged into the bench supply/charger = the pack current goes NEGATIVE
// (+ = discharging). While charging, ALL drive is locked out — including manual
// and the R3 sensor-bypass. Cable state is inferred as described below.
// Debounced on the 1 Hz battery tick; both edges emit a "charging" event.
#define BATT_CHARGE_DETECT_MA    250   // sustained charge current at/above this = on charger
#define BATT_CHARGE_DETECT_MV  14000   // ENTER: charger holds ~14.2V (clearly above rest)
// Owner-reported Oct 1 operating range: ~0.01 A at plugged-in taper, 1-3 A
// discharge unplugged/asleep, more when awake. Use a 0.5 A discharge threshold
// for three fresh 1 Hz samples, replacing the 90 s / 0.1 A rule. Near-zero
// current does not release an existing lock. Missing samples reset the count;
// >=14 V still blocks release, but the old 13.6 V surface-charge floor is absent.
// Limitation: a connected charger outdrawn by the load can mimic an unplug.
#define BATT_CHARGE_ENTER_TICKS    3
#define BATT_CHARGE_EXIT_DISCHARGE_MA 500
#define BATT_CHARGE_EXIT_TICKS     3

// ---- Speed-adaptive zone envelope (safety.cpp zones + control.cpp taper) ----
// The front/rear pairs are angled ±22.5° off the travel axis, so at RANGE they see
// obstacles outside the actual collision corridor (at 0.9 m a beam points ~0.35 m
// off-centerline — wider than the 0.27 m half-body), while everything they see CLOSE
// is genuinely in the path. Fixed zones therefore over-brake at distance and
// under-serve precision parking. The effective zones now SCALE WITH MEASURED SPEED
// (context.h stop_zone_eff/slow_zone_eff): the configured slow_zone_m/stop_zone_m
// are the FULL-SPEED envelope (braking distance matters, angled false positives
// cost little), shrinking linearly to the floors below at rest — where the operator
// is deliberately positioning and the angled beams only see true in-path obstacles.
// The STOP floor is the "never able to actually hit the wall" guarantee.
#define STOP_ZONE_MIN_M       0.10f   // hard-stop floor at rest (~4 in) — never hittable
#define SLOW_ZONE_MIN_M       0.18f   // braking-band floor at rest
#define ZONE_SPEED_REF_MS     0.80f   // speed at which the configured zones fully apply
                                      // (tracks the hardwood-mode ceiling; carpet mode
                                      // tops out higher but is drag-limited in practice)

// ---- Finite-command reflex-block grace (control.cpp) ------------------------
// A finite move/come used to terminate done:blocked the instant the reflex latched
// BLOCKED in its travel direction — so a single phantom near frame killed the whole
// command ("move forward 3 feet" refused with >1 m genuinely clear, field
// 2026-08-01). The reflex still starts braking toward the block IMMEDIATELY
// (safety unchanged); the command itself now survives a block that clears within
// this window and resumes on its own. Only a block that persists — a real wall or
// person — gives up with done:blocked. Sized well above the ToF flap period
// (~150-225 ms per phantom episode) and below "feels stuck" for the operator.
#define FINITE_BLOCK_GRACE_MS  900

// ---- Reflex braking (control.cpp + drive_shaping.h reflex_decel) ------------
// A reflex block toward the direction of travel used to cut the speed ramp to zero in
// one tick and short-brake the wheels (field fix 2026-07-11, a coast into a wall).
// Owner 2026-10-23: those slams were mostly false near readings. The ramp now sheds
// speed at the deceleration the remaining room requires, v^2 / 2(room - margin),
// never gentler than the minimum and never harder than the maximum. At 0.25 m/s a block
// at the speed-adapted stop line (~0.12 m) stops in about 4 cm at 0.8 m/s^2; one false
// matrix frame (~77 ms) costs about 0.06 m/s, then the ordinary ramp resumes.
#define REFLEX_DECEL_MIN_MS2   0.8f
#define REFLEX_DECEL_MAX_MS2   6.0f
#define REFLEX_STOP_MARGIN_M   0.04f

// ---- Approach slowdown creep floor (control.cpp slow-zone taper) ------------
// The progressive slow-zone taper scales the commanded speed toward zero at the stop
// line — but the loaded base needs a REAL speed to move at all (stiction; the same
// reason GAMEPAD_SPEED_SLOW had to be raised at full build weight), and the wheel PID
// treats targets under WHEEL_STOP_EPS_MS as a stop. Without a floor the taper stalls
// the base mid-slow-zone, far from the wall (field-logged 2026-07-11: "won't let me
// within 3 feet"). Inside the slow zone the command is floored at this creep speed
// (never MORE than the operator commanded), so the base crawls under control all the
// way to the stop_zone hard block instead of dying early.
#define APPROACH_CREEP_MIN_MS 0.06f     // m/s; comfortably above WHEEL_STOP_EPS_MS + stiction

// ---- Bluetooth gamepad (Bluepad32) — only when MOTION_GAMEPAD_PRESENT==1 ----
// Left stick = arcade drive (Y forward, X turn); L1 creep / R1 boost; B = e-stop;
// Start = clear + return to AUTO; hold BOTH analog triggers = full-override (docs §11).
#define GAMEPAD_DEADZONE       0.12f    // stick fraction ignored around center
// TELEOP SURFACE MODES — the gamepad's own caps, in REAL m/s / rad/s, independent
// of params.max_lin/max_ang (the AUTONOMOUS limits the Mac pushes down on connect).
// control_tick clamps a MANUAL drive to the larger (carpet) profile; autonomous
// motion stays on the params caps.
//
// History: launched as 3 abstract speed LEVELS (slow/med/full, L3 cycling), retuned
// repeatedly (bare base -> full weight -> carpet -> real units). At full build
// weight the whole 3-level ladder was insufficient (owner 2026-07-11: full stick on
// level 3 struggles on carpet, turning difficult even on hardwood) — what actually
// varies is the SURFACE, so L3 now TOGGLES two surface profiles instead:
//   HARDWOOD (boot/reconnect default): a bit above the old level-3 top speed.
//   CARPET: maximum authority — higher speed ceiling and a full-saturation spin
//     breakaway kick. NOTE the measured torque ceiling stands: an in-place spin on
//     the test carpet saturated both wheels at 1023 duty with ~zero rotation, so
//     carpet mode makes turning as strong as the hardware allows (arcs improve;
//     pure pivots on deep pile may still stall — that's motors, not firmware).
// 2026-11-12, Yellow Jackets (owner: "too fast", turns never smooth). Speeds are now
// true m/s. Turn ceilings used to be the whole wheel-speed range (4 rad/s, 230 deg/s)
// because the old motors needed every bit of scrub authority; against a 0.25 m/s
// forward ceiling that meant a quarter of the turn stick already stopped the inside
// wheel, so a moving turn was a lurch into a pivot. The ceilings below keep the wheel
// speed a full turn stick asks for (ang x track / 2 = 0.27 m/s hardwood) close to the
// forward ceiling: at full forward, turn stick now slows the inside wheel in proportion
// and only stops it at full deflection.
#define GAMEPAD_HARDWOOD_LIN_MS    0.25f   // the autonomous walking pace
#define GAMEPAD_HARDWOOD_ANG_RADS  1.2f    // ~70 deg/s spin at full stick
#define GAMEPAD_HARDWOOD_SPIN_KICK 350.0f  // stall-gated pivot breakaway ceiling (estimate)
#define GAMEPAD_HARDWOOD_SPIN_RUN  150.0f  // pivot RUNNING floor (estimate)
#define GAMEPAD_CARPET_LIN_MS      0.38f   // near the drivetrain's 0.44 m/s ceiling
#define GAMEPAD_CARPET_ANG_RADS    1.6f    // ~90 deg/s
#define GAMEPAD_CARPET_SPIN_KICK   650.0f  // estimate; carpet pivots untested on these motors
#define GAMEPAD_CARPET_SPIN_RUN    260.0f
// Stick RESPONSE CURVES: command = sign(stick) * |stick|^GAMMA * mode max, per axis.
// GAMMA > 1 is expo: fine control around centre, full deflection still the mode max.
// At half stick 1.6 gives 33% of the ceiling. The forward axis used 0.60 (anti-expo,
// 66% at half stick) to help the weak old motors break stiction, which on these motors
// made the stick an on/off switch; the turn axis was linear. The spin↔arcade blend
// still keys off the RAW forward stick, so its bands do not shift with the curve.
#define GAMEPAD_LIN_GAMMA      1.6f
#define GAMEPAD_ANG_GAMMA      1.6f
// (GAMEPAD_SPIN_SCALE retired 2026-07-11 with the speed levels: turn authority is now
// always the surface mode's full ang ceiling; the blend below only morphs the wheel
// MIXING between spin-in-place and arcade arc.)
// Spin↔arcade BLEND band, on the forward/back stick fraction (post-deadzone, 0..1).
// Below LO: pure spin-in-place (full authority, inside wheel may reverse). Above HI:
// pure arcade steer (level-scaled authority, inside wheel floored at 0). Between the
// two, BOTH the turn authority and the wheel mixing interpolate smoothly (smoothstep),
// so tilting slightly forward out of a spin eases into a tightening arc instead of the
// turn rate collapsing at a hard threshold (field bug: "mostly left + slightly forward
// acts strange" — the old binary 0.02 m/s gate stepped authority 1.0 -> 0.15 at slow).
#define GAMEPAD_SPIN_BLEND_FWD_LO  0.05f   // stick fraction where the blend starts
#define GAMEPAD_SPIN_BLEND_FWD_HI  0.35f   // stick fraction where it's fully arcade
// D-pad driving nudges (rising edge, one per press): Up/Down = a short finite forward/
// back MOVE (encoder-closed, ToF stop-reflex gated like any move); Left/Right = a
// RELATIVE turn by params.default_turn_deg (90°). Hold L1 + D-pad for the original
// absolute-heading encoder test instead (bring-up/calibration only).
#define GAMEPAD_NUDGE_DIST_M      0.30f  // Up/Down nudge travel (m)
#define GAMEPAD_NUDGE_SPEED_FRAC  0.30f  // nudge speed as a fraction of max_lin
#define GAMEPAD_TRIGGER_MAX    1023.0f  // Bluepad32 analog trigger full-scale
// (GAMEPAD_FULL_OVERRIDE_FRAC retired 2026-07-16: hold-L2+R2 override replaced by the
//  R3-toggled sensor-bypass mode — see gamepad.cpp header.)
#define GAMEPAD_TRIGGER_PRESS_FRAC 0.50f // trigger past this fraction = "pressed" (GUI mirror)
// Rumble (force feedback, playDualRumble): tactile echo of the collision avoidance +
// a hello greeting when a host (main.py) connects. All Bluepad32 calls stay on the
// Arduino loopTask (gamepad_tick); other tasks only set pending flags.
#define GAMEPAD_RUMBLE_ENABLED        1     // build-time master switch (0 = never rumble)
#define GAMEPAD_RUMBLE_BLOCK_MS       320   // hard-stop (BLOCKED) thump duration
#define GAMEPAD_RUMBLE_BLOCK_STRONG   0xE0  // heavy motor: a real thump
#define GAMEPAD_RUMBLE_BLOCK_WEAK     0x30
#define GAMEPAD_RUMBLE_BLOCK_REPEAT_MS 700  // re-thump cadence while still pushing into it
#define GAMEPAD_RUMBLE_SLOW_MS        130   // entering the braking band: light buzz
#define GAMEPAD_RUMBLE_SLOW_WEAK      0x80  // light motor only — clearly distinct from the thump
#define GAMEPAD_RUMBLE_SLOW_STRONG    0x00
#define GAMEPAD_RUMBLE_HELLO_MS       150   // host-connect greeting: friendly double pulse
#define GAMEPAD_RUMBLE_HELLO_GAP_MS   180
#define GAMEPAD_RUMBLE_HELLO_MAG      0x70
#define GAMEPAD_RUMBLE_HELLO_TTL_MS   3000  // drop a stale greet if no pad is on within this
