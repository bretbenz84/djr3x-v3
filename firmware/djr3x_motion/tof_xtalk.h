// tof_xtalk.h: crosstalk compensation registers for a VL53L1X whose lens returns light on its own.
//
// Until 0.2.22 this file (tof_fr_filter.h) also held a quality quarantine and a persistence filter
// for the front-right channel only, written for the faulty module that sat there until October 19
// 2026. The replacement reads like the other three corner sensors and is now treated like them
// (owner, November 20: "I think we only did that because of a bad sensor ... that vestige is
// still there"). Requires calib.h first.
#pragma once
#include <stdint.h>
#include "calib.h"

// VL53L1X crosstalk registers from a measured kcps/SPAD figure (calib.h): the plane
// offset is 9.7 fixed-point kcps/SPAD, the range-ignore threshold 3.13 fixed-point
// Mcps/SPAD (VL53L1 core: kcps x 16 / 1000). Both saturate at the register width.
static inline uint16_t tof_xtalk_plane_offset_reg(float kcps_per_spad) {
  const float v = kcps_per_spad * 128.f + .5f;
  return v <= 0.f ? 0 : (v >= 65535.f ? 65535 : (uint16_t)v);
}
static inline uint16_t tof_range_ignore_reg(float kcps_per_spad, float mult) {
  const float v = kcps_per_spad * mult * 8.192f + .5f;
  return v <= 0.f ? 0 : (v >= 65535.f ? 65535 : (uint16_t)v);
}
