// tof_matrix_link.h — when one 8x8 matrix module is probed, set up, read, and set
// up AGAIN. Pure: no Wire, no FreeRTOS, so the native checks replay a module being
// held in reset and let go (firmware/tests/test_tof_matrix_link.py).
//
// Why (owner, November 15, 2026; logs c85e39dc, e1095922, 00748116): until 0.2.18 a
// module was set up once per controller boot. One that reset afterwards (its reset
// button pressed in, a power dip) answered every frame request with errors for
// good, the host locked the wheels, and only switching the base off and on brought
// it back. Now a module that has failed for TOF_MATRIX_REINIT_AFTER_MS goes back to
// the boot sequence: probe once a second until it answers, send the 8x8 mode, leave
// it alone through the settle, read the acknowledgement, stream.
//
// The boot path is unchanged: nothing here acts differently until a module that was
// streaming has already failed for two seconds. The bus reset in particular runs
// only on that recovery path, never for a healthy pair or a module that was never
// fitted.
#pragma once
#include <stdint.h>
#include "calib.h"

enum MxState : uint8_t { MX_PROBE, MX_SETTLE, MX_ACK, MX_READY };

enum MxEvent : uint8_t {
  MX_EV_NONE,          // nothing to report this step
  MX_EV_NO_ACK,        // a probe went unanswered (MxLink.attempts counts them)
  MX_EV_READY,         // first setup acknowledged: streaming from the next step
  MX_EV_BACK,          // setup acknowledged again after a loss: streaming resumes
  MX_EV_MODE_UNACKED,  // setup not acknowledged in time: probing again
  MX_EV_FRAME,         // a good frame
  MX_EV_FRAME_HELD,    // a failed frame inside the hold window: keep the last good values
  MX_EV_LOST,          // failures reached the stale streak: report -1 from now
  MX_EV_STILL_LOST,    // still failing, already reporting -1
  MX_EV_REINIT,        // failing for TOF_MATRIX_REINIT_AFTER_MS: setup starts over
};

struct MxLink {
  MxState state = MX_PROBE;
  uint32_t state_ms = 0, attempts = 0;
  uint8_t err_streak = 0;
  uint32_t err_since_ms = 0;
  bool recovering = false;   // was streaming and stopped: this setup is a recovery
};

// One step for one module. Io supplies the bus:
//   bool probe()          the module ACKs its address
//   void send_setmode()   ask for 8x8 (the module then needs the blind settle)
//   bool mode_acked()     the 8x8 request was acknowledged
//   bool read_frame()     one whole, valid frame was read
//   void bus_reset()      restart the I2C controller and clock a stuck line free
template <class Io>
MxEvent mx_link_step(MxLink& u, Io& io, uint32_t now) {
  switch (u.state) {
    case MX_PROBE:
      if (u.attempts && (uint32_t)(now - u.state_ms) < TOF_MATRIX_PROBE_INTERVAL_MS) return MX_EV_NONE;
      u.state_ms = now;
      if (io.probe()) {
        io.send_setmode();
        u.state = MX_SETTLE;
        return MX_EV_NONE;
      }
      ++u.attempts;
      // A module that used to answer and now does not may be holding a bus line.
      if (u.recovering && u.attempts % TOF_MATRIX_BUS_RESET_PROBES == 0) io.bus_reset();
      return MX_EV_NO_ACK;
    case MX_SETTLE:
      if ((uint32_t)(now - u.state_ms) >= TOF_MATRIX_MODE_SETTLE_MS) {
        u.state = MX_ACK;
        u.state_ms = now;
      }
      return MX_EV_NONE;
    case MX_ACK:
      if (io.mode_acked()) {
        const bool back = u.recovering;
        u.err_streak = 0;
        u.recovering = false;
        u.state = MX_READY;
        return back ? MX_EV_BACK : MX_EV_READY;
      }
      if ((uint32_t)(now - u.state_ms) >= TOF_MATRIX_MODE_ACK_TIMEOUT_MS) {
        u.state = MX_PROBE;     // re-probe in 1 s, then re-send the mode
        u.state_ms = now;
        u.attempts = 1;
        return MX_EV_MODE_UNACKED;
      }
      return MX_EV_NONE;
    case MX_READY:
      if (io.read_frame()) {
        u.err_streak = 0;
        return MX_EV_FRAME;
      }
      if (u.err_streak == 0) u.err_since_ms = now;
      if (u.err_streak < 255) ++u.err_streak;
      if (u.err_streak >= TOF_ERR_STREAK_STALE &&
          (uint32_t)(now - u.err_since_ms) >= TOF_MATRIX_REINIT_AFTER_MS) {
        u.state = MX_PROBE;
        u.state_ms = now;
        u.attempts = 0;         // probe on the very next step
        u.err_streak = 0;
        u.recovering = true;
        io.bus_reset();
        return MX_EV_REINIT;
      }
      if (u.err_streak == TOF_ERR_STREAK_STALE) return MX_EV_LOST;
      return u.err_streak > TOF_ERR_STREAK_STALE ? MX_EV_STILL_LOST : MX_EV_FRAME_HELD;
  }
  return MX_EV_NONE;
}
