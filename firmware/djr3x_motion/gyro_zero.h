// gyro_zero.h: the gyroscope's "not turning" reading, measured again whenever the base stands still.
//
// A gyroscope reports a small turn rate even when perfectly still (about -3.2 deg/s for this chip).
// The heading is that rate summed over time, so whatever is wrong in the zero taken off it becomes
// a steady drift. Until 0.2.23 the zero was taken once, in the first second after the chip was
// switched on, and never again:
//
// - November 22 2026 (run e54062b9): the chip had been without power, came back twelve seconds
//   into a start while the wake animation shook the body, and its zero was taken from the single
//   reading of that moment. The heading ran 19.7 deg/s by itself: left turns stopped at half,
//   "turn right 90" spun about 240 degrees until the turn timed out, and the room map never saw
//   him standing still.
// - Every ordinary start: the second measured began 50 ms after the chip was switched on and its
//   readings swung 8.5 to 8.8 deg/s (the firmware's own start-up report, nineteen starts running),
//   where a still gyroscope swings about a quarter of that at most. The zero landed up to 0.2 deg/s off,
//   differently each start: -0.05, -0.16, -0.09, +0.19, -0.14, +0.215 deg/s of drift, each steady
//   until the next start.
//
// Now a zero is one second (GYRO_ZERO_WINDOW readings) in which the wheels are neither driven nor
// turning and no axis swings more than GYRO_ZERO_STILL_DPS. The first such second after the chip
// (re)starts replaces the zero; later ones move it half way, which follows the chip as it warms.
// A steady reading far from the zero is someone turning him, not a zero, unless it holds for
// GYRO_ZERO_FAR_WINDOWS seconds running. Pure: no I2C and no clock here.
#pragma once
#include <math.h>
#include <stdint.h>

static const uint8_t GYRO_ZERO_WINDOW      = 50;     // readings in one measurement: 1 s at the sensor task's 50 Hz
static const float   GYRO_ZERO_STILL_DPS   = 0.75f;  // a still chip's readings stay within this of each other, each axis
static const float   GYRO_ZERO_MAX_DPS     = 12.0f;  // no chip's zero is farther from 0 (datasheet worst case 10)
static const float   GYRO_ZERO_STEP_DPS    = 1.0f;   // warming moves a zero by tenths; a still second this far off is a turn...
static const uint8_t GYRO_ZERO_FAR_WINDOWS = 5;      // ...unless it reads the same for this many seconds running
static const float   GYRO_ZERO_BLEND       = 0.5f;

struct GyroZero {
  float   sum[3] = {0, 0, 0};
  float   lo[3]  = {0, 0, 0};
  float   hi[3]  = {0, 0, 0};
  uint8_t n = 0;              // readings gathered in the second under way
  uint8_t far = 0;            // still seconds running that read far from the zero
  bool    measured = false;   // a still second has set the zero since the chip last (re)started
  float   spread = 0;         // widest swing of the last second judged, kept or not (for the log)
};

// The chip was switched on again: the next still second replaces the zero outright.
static inline void gyro_zero_restart(GyroZero& z) { z = GyroZero(); }

// One reading in deg/s with nothing subtracted. wheels_still: neither driven nor turning.
// Returns true when a still second completed and zero[] was updated.
static inline bool gyro_zero_note(GyroZero& z, const float g[3], bool wheels_still, float zero[3]) {
  if (!wheels_still) {
    z.n = 0; z.far = 0;
    return false;
  }
  if (z.n == 0) {
    for (int i = 0; i < 3; i++) { z.sum[i] = 0; z.lo[i] = z.hi[i] = g[i]; }
  }
  float widest = 0;
  for (int i = 0; i < 3; i++) {
    if (g[i] < z.lo[i]) z.lo[i] = g[i];
    if (g[i] > z.hi[i]) z.hi[i] = g[i];
    if (z.hi[i] - z.lo[i] > widest) widest = z.hi[i] - z.lo[i];
  }
  if (widest > GYRO_ZERO_STILL_DPS) {
    // He was moved, his own servos rocked him, or the chip is still settling: begin again here.
    z.spread = widest;
    z.n = 1; z.far = 0;
    for (int i = 0; i < 3; i++) { z.sum[i] = g[i]; z.lo[i] = z.hi[i] = g[i]; }
    return false;
  }
  for (int i = 0; i < 3; i++) z.sum[i] += g[i];
  if (++z.n < GYRO_ZERO_WINDOW) return false;

  float mean[3];
  bool plausible = true, near = true;
  for (int i = 0; i < 3; i++) {
    mean[i] = z.sum[i] / z.n;
    if (fabsf(mean[i]) > GYRO_ZERO_MAX_DPS) plausible = false;
    if (fabsf(mean[i] - zero[i]) > GYRO_ZERO_STEP_DPS) near = false;
  }
  z.spread = widest;
  z.n = 0;
  if (!plausible) { z.far = 0; return false; }
  if (z.measured && !near) {
    if (++z.far < GYRO_ZERO_FAR_WINDOWS) return false;
    z.measured = false;       // The same far reading, second after second: the zero itself was wrong.
  }
  z.far = 0;
  for (int i = 0; i < 3; i++)
    zero[i] = z.measured ? zero[i] + GYRO_ZERO_BLEND * (mean[i] - zero[i]) : mean[i];
  z.measured = true;
  return true;
}
