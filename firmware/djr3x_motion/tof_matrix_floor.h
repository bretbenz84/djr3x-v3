#pragma once
#include <math.h>
#include <stdint.h>
#include "calib.h"

// Pure per-module geometry and aggregation for the 8x8 matrices (front and
// rear SEN0628). No I/O: tof_matrix.cpp owns the bus, the host test compiles
// this header directly.
//
// Shared by the firmware reflex and the host via tofmx.rej. Row is in the
// normalized orientation (0 = top). Explicit measured floor rows take precedence
// over the geometric estimate and its historical 250 mm lower bound.
static inline float tof_matrix_floor_reject_mm(int row, float elevation_rad, float height_m,
                                               const unsigned short measured[8]) {
  if (measured[row]) return (float)measured[row];
  if (elevation_rad >= -0.5f * (float)M_PI / 180.0f) return 1.0e9f;
  const float geometric = height_m * 1000.0f /
      sinf(-elevation_rad) * TOF_MATRIX_FLOOR_TOLERANCE;
  return fmaxf(geometric, (float)TOF_MATRIX_FLOOR_MIN_MM);
}

// One module's mount: orientation flips, effective optical height and measured
// floor rows (calib.h), plus the per-row tables derived from them.
struct MxGeom {
  bool flip_v, flip_h;
  float height_m;
  unsigned short floor_row_mm[8];
  float row_reject_mm[8];   // at/beyond = floor (or too far to matter) -> clear
  float row_cos[8];         // projects a ray reading onto the horizontal plane
};

static inline void mx_compute_geometry(MxGeom& g) {
  const float pitch  = TOF_MATRIX_PITCH_DEG;
  const float rowdeg = TOF_MATRIX_VFOV_DEG / 8.0f;
  for (int r = 0; r < 8; r++) {
    // Row-centre elevation above the horizon: top row (r=0) looks UP, bottom
    // row (r=7) looks DOWN, level mount => symmetric about 0.
    const float elev_deg = (3.5f - (float)r) * rowdeg + pitch;
    const float elev_rad = elev_deg * (float)M_PI / 180.0f;
    g.row_cos[r] = cosf(elev_rad);
    g.row_reject_mm[r] = tof_matrix_floor_reject_mm(r, elev_rad, g.height_m, g.floor_row_mm);
  }
}

// Raw grid index of normalized zone (row 0 = physically TOP, col 0 = the
// SENSOR'S LEFT edge of its FOV).
static inline int mx_raw_index(const MxGeom& g, int r, int c) {
  return (g.flip_v ? (7 - r) : r) * 8 + (g.flip_h ? (7 - c) : c);
}

// ---- Aggregation: 64 zones -> nearest obstacle per half ----------------------
// Halves are the SENSOR'S left/right: for the front module that is the robot's
// left/right; the rear module faces backwards, so its left half is the robot's
// rear-RIGHT (tof_matrix.cpp maps it).
// Each half publishes its SECOND-nearest qualifying zone (TOF_MATRIX_MIN_OBSTACLE_ZONES,
// calib.h): a real obstacle close enough to matter subtends multiple zones, so second ≈
// nearest for anything genuine, while a lone speckle zone — the phantom near return that
// pinned a parked base BLOCKED with >1 m clear (field 2026-08-01) — reads as clear.
static inline void mx_aggregate(const MxGeom& g, const uint16_t grid[64],
                                int16_t* out_left, int16_t* out_right) {
  float best_left = 1.0e9f,  best_right = 1.0e9f;   // nearest qualifying zone per half
  float best2_left = 1.0e9f, best2_right = 1.0e9f;  // second-nearest per half
  for (int r = 0; r < 8; r++) {
    for (int c = 0; c < 8; c++) {
      const uint16_t v = grid[mx_raw_index(g, r, c)];
      if (v == 0) continue;                              // no valid return in this zone
      if (v >= TOF_MATRIX_CLEAR_MM) continue;            // sensor's "no return" marker
                                                         // (reports 4000 for nothing seen)
      if (v < TOF_MATRIX_MIN_MM) continue;               // sub-min-range speckle
      if ((float)v >= g.row_reject_mm[r]) continue;      // the floor (or beyond it)
      const float horiz = (float)v * g.row_cos[r];       // project ray -> horizontal
      if (c < 4) {
        if (horiz < best_left)       { best2_left = best_left;   best_left = horiz; }
        else if (horiz < best2_left) { best2_left = horiz; }
      } else {
        if (horiz < best_right)       { best2_right = best_right;  best_right = horiz; }
        else if (horiz < best2_right) { best2_right = horiz; }
      }
    }
  }
#if TOF_MATRIX_MIN_OBSTACLE_ZONES >= 2
  const float pub_left = best2_left, pub_right = best2_right;
#else
  const float pub_left = best_left,  pub_right = best_right;
#endif
  *out_left  = (pub_left  < 1.0e9f) ? (int16_t)(pub_left  + 0.5f) : (int16_t)TOF_MATRIX_CLEAR_MM;
  *out_right = (pub_right < 1.0e9f) ? (int16_t)(pub_right + 0.5f) : (int16_t)TOF_MATRIX_CLEAR_MM;
}
