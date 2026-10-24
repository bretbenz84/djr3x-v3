// tof_matrix.cpp — DFRobot 8x8 Matrix ToF (SEN0628) front obstacle sensor.
// See tof_matrix.h for the role; calib.h ("8x8 Matrix ToF") for every knob.
//
// ---- Why a vendored mini-driver instead of DFRobot_MatrixLidar ---------------
// The official library is fine on the bench (firmware/tof_matrix_test uses it)
// but is not safe on the robot's 50 Hz sensor task:
//   - its receive path busy-polls the bus with a PRIVATE, unsettable 8000 ms
//     timeout — a wedged RP2040 would stall the sensor task for 8 s (the exact
//     I2C-stall → starved-Bluetooth failure class already fought once on this
//     bus, see djr3x_motion.ino task notes);
//   - it mallocs/frees every call (heap churn in a hot loop);
//   - setRangingMode() hides a blocking delay(5000) we need under FreeRTOS
//     control (vTaskDelay on a background task, not a boot stall).
// The wire protocol is tiny and fully visible in the library source, so we speak
// it directly with hard deadlines and static buffers. Bench firmware keeps using
// the library; only the robot build uses this driver.
//
// ---- Wire protocol (confirmed against DFRobot_MatrixLidar 1.x source) --------
//   Mac->sensor I2C write:  {0x55, argsNumH, argsNumL, cmd, args...}
//                           argsNum = len(args) + 1 (the +1 is the lib's quirk —
//                           copied verbatim; the RP2040 expects it).
//     CMD_SETMODE (1): args {0,0,0, matrix} with matrix=8 for 8x8. Needs ~5 s
//                      settle after the ACK before frames are valid.
//     CMD_ALLDATA (2): no args. Response payload = 128 bytes: 64x uint16 LE
//                      millimetres, row-major (index = y*8 + x). 0 = no return.
//   Response (I2C reads): poll one status byte until != 0xFF —
//     0x53 = success, 0x63 = failed. Then {cmd, lenL, lenH, payload[len]},
//     reads chunked <= 32 bytes (the RP2040's I2C cache size).
#include "hal.h"

#if MOTION_TOF_MATRIX_PRESENT
#include "pins.h"
#include "calib.h"
#include "tof_filter.h"
#include "tof_matrix_floor.h"
#include "proto_io.h"
#include <Arduino.h>
#include <Wire.h>
#include <math.h>

// ---- Protocol constants (mirrors the DFRobot library source) ----------------
static const uint8_t MX_HEAD          = 0x55;
static const uint8_t MX_CMD_SETMODE   = 1;
static const uint8_t MX_CMD_ALLDATA   = 2;
static const uint8_t MX_STATUS_OK     = 0x53;
static const uint8_t MX_STATUS_FAILED = 0x63;
static const uint8_t MX_STATUS_IDLE   = 0xFF;   // "response not ready yet"
static const int     MX_I2C_CHUNK     = 32;     // RP2040-side I2C cache limit
static const uint8_t MX_MODE_8X8      = 8;      // eMatrix_8X8

// ---- Two modules on one private bus -----------------------------------------
// FRONT (0x33) and REAR (0x32, owner 2026-10-19: same height, facing straight
// back) share Wire1. One poll task owns every transaction to both, so a
// stretching module costs only matrix time. Each module runs its own small state
// machine, so the front never waits on the rear's 5 s mode switch and a rear
// module that is absent or unplugged simply keeps being probed once a second.
// Clock stretching only happens on transactions ADDRESSED to the busy module;
// nothing talks to a module during its blind settle window.
enum MxState : uint8_t { MX_PROBE, MX_SETTLE, MX_ACK, MX_READY };

struct MxUnit {
  const char* name;            // log label
  uint8_t addr;
  uint8_t probe_log_every;     // log a missing module every Nth failed probe
  const char* emit_type;       // telemetry line type for the raw grid
  uint8_t emit_every;          // stream every Nth good frame
  MxGeom geom;
  // Poll-task owned:
  MxState state;
  uint32_t state_ms, attempts;
  uint16_t grid[64];
  TofFilt filt_l, filt_r;
  uint8_t err_streak, emit_div;
  // Published to the sensor task (lock-free snapshot):
  volatile bool ready;
  volatile int16_t left, right;   // SENSOR-frame halves, mm; -1 = no data
  volatile uint32_t pub_ms;
};

static MxUnit s_units[2] = {
  {"front", TOF_MATRIX_ADDR, 10, "tofmx", 2,
   {TOF_MATRIX_FLIP_V != 0, TOF_MATRIX_FLIP_H != 0, TOF_MATRIX_HEIGHT_M,
    TOF_MATRIX_FLOOR_ROW_MM, {0}, {0}},
   MX_PROBE, 0, 0, {0}, TofFilt(), TofFilt(), 0, 0, false, -1, -1, 0},
  {"rear", TOF_MATRIX_REAR_ADDR, 60, "tofmx_rear", TOF_MATRIX_REAR_EMIT_EVERY,
   {TOF_MATRIX_REAR_FLIP_V != 0, TOF_MATRIX_REAR_FLIP_H != 0, TOF_MATRIX_REAR_HEIGHT_M,
    TOF_MATRIX_REAR_FLOOR_ROW_MM, {0}, {0}},
   MX_PROBE, 0, 0, {0}, TofFilt(), TofFilt(), 0, 0, false, -1, -1, 0},
};
static MxUnit& s_front = s_units[0];
static MxUnit& s_rear  = s_units[1];

// ---- Bounded I2C primitives (hardware I2C1 via Wire1) -------------------------
// The matrices live ALONE on the second I2C controller (GPIO4/5, pins.h) — never
// on the 21/22 trunk with the INA226/IMU. Every transaction runs on the matrix
// poll task, so a slow or stretching sensor costs only this task's time.
static bool mx_probe(uint8_t addr) {
  Wire1.beginTransmission(addr);
  return Wire1.endTransmission() == 0;
}

static bool mx_send_cmd(uint8_t addr, uint8_t cmd, const uint8_t* args, uint8_t args_len) {
  Wire1.beginTransmission(addr);
  Wire1.write(MX_HEAD);
  Wire1.write((uint8_t)(((args_len + 1) >> 8) & 0xFF));  // argsNumH (lib's len+1 quirk)
  Wire1.write((uint8_t)((args_len + 1) & 0xFF));         // argsNumL
  Wire1.write(cmd);
  if (args_len) Wire1.write(args, args_len);
  const bool ok = (Wire1.endTransmission() == 0);
  return ok;
}

// Read len bytes in <=32-byte chunks. Returns false on a short/NACKed read.
static bool mx_read_bytes(uint8_t addr, uint8_t* dst, int len) {
  while (len > 0) {
    const int n = (len > MX_I2C_CHUNK) ? MX_I2C_CHUNK : len;
    if ((int)Wire1.requestFrom(addr, (uint8_t)n) != n) return false;
    for (int i = 0; i < n; i++) {
      const int b = Wire1.read();
      if (b < 0) return false;
      *dst++ = (uint8_t)b;
    }
    len -= n;
  }
  return true;
}

// Await + read one response packet for `cmd` within deadline_ms. On success the
// payload (up to max_payload bytes) is in payload_out and its length returned;
// -1 = timeout / protocol error / failed status. HARD-BOUNDED: this is what
// replaces the library's private 8 s busy-poll.
static int mx_recv_response(uint8_t addr, uint8_t cmd, uint8_t* payload_out, int max_payload,
                            uint32_t deadline_ms) {
  const uint32_t t0 = millis();
  for (;;) {
    uint8_t status;
    if (!mx_read_bytes(addr, &status, 1)) status = MX_STATUS_IDLE;   // NACK = not ready
    if (status == MX_STATUS_OK || status == MX_STATUS_FAILED) {
      uint8_t hdr[3];                                          // {cmd, lenL, lenH}
      if (!mx_read_bytes(addr, hdr, 3)) return -1;
      if (hdr[0] != cmd) return -1;                            // response for someone else
      int len = (int)hdr[1] | ((int)hdr[2] << 8);
      if (len < 0 || len > 1000) return -1;                    // insane length — bail
      // Always DRAIN the payload (even on FAILED / oversize) so the RP2040's
      // response buffer isn't left half-read for the next transaction.
      uint8_t scratch[MX_I2C_CHUNK];
      int want = (len <= max_payload) ? len : max_payload;
      if (want > 0 && !mx_read_bytes(addr, payload_out, want)) return -1;
      for (int left = len - want; left > 0; ) {
        const int n = (left > MX_I2C_CHUNK) ? MX_I2C_CHUNK : left;
        if (!mx_read_bytes(addr, scratch, n)) return -1;
        left -= n;
      }
      return (status == MX_STATUS_OK) ? want : -1;
    }
    if ((uint32_t)(millis() - t0) >= deadline_ms) return -1;
    vTaskDelay(pdMS_TO_TICKS(2));    // yield the core between status polls
  }
}

// ---- Per-module bring-up: probe -> SETMODE 8x8 -> blind settle -> ack ----------
// ⚠ DO NOT status-poll a module while it reconfigures. The RP2040
// clock-stretches during the ~5 s VL53L7CX mode switch, and the ESP32's I2C
// hardware tolerates at most ~13 ms of stretch — hammering status reads into
// that window wedged the bus and hung the whole firmware (field-observed
// 2026-07-16: telemetry died seconds after boot, 4/5 resets). Instead: send
// SETMODE, leave that module alone BLINDLY through the settle, then read the ack
// when the sensor is guaranteed idle again. The other module keeps polling.
static void mx_send_setmode(MxUnit& u, uint32_t now) {
  const uint8_t args[4] = {0, 0, 0, MX_MODE_8X8};
  mx_send_cmd(u.addr, MX_CMD_SETMODE, args, sizeof(args));
  u.state = MX_SETTLE;
  u.state_ms = now;
}

static void mx_bringup_step(MxUnit& u, uint32_t now) {
  switch (u.state) {
    case MX_PROBE:
      if (u.attempts && (uint32_t)(now - u.state_ms) < 1000) return;   // 1 s between probes
      u.state_ms = now;
      if (mx_probe(u.addr)) { mx_send_setmode(u, now); return; }
      if ((u.attempts++ % u.probe_log_every) == 0) {
        char buf[96];
        snprintf(buf, sizeof(buf),
                 "tof_matrix %s: no ACK at 0x%02X (attempt %lu) - check wiring/DIP addr",
                 u.name, u.addr, (unsigned long)u.attempts);
        emit_log("warn", buf);
      }
      return;
    case MX_SETTLE:
      if ((uint32_t)(now - u.state_ms) >= TOF_MATRIX_MODE_SETTLE_MS) {
        u.state = MX_ACK;
        u.state_ms = now;
      }
      return;
    case MX_ACK: {
      uint8_t scratch[8];
      if (mx_recv_response(u.addr, MX_CMD_SETMODE, scratch, sizeof(scratch),
                           TOF_MATRIX_READ_TIMEOUT_MS) >= 0) {
        mx_compute_geometry(u.geom);
        u.err_streak = 0;
        u.pub_ms = millis();   // arm the staleness clock BEFORE readers can see ready
        u.state = MX_READY;
        u.ready = true;
        char buf[80];
        snprintf(buf, sizeof(buf), "tof_matrix %s: 8x8 matrix ready at 0x%02X (floor rejection active)",
                 u.name, u.addr);
        emit_log("info", buf);
      } else if ((uint32_t)(now - u.state_ms) >= TOF_MATRIX_MODE_ACK_TIMEOUT_MS) {
        char buf[72];
        snprintf(buf, sizeof(buf), "tof_matrix %s: 8x8 mode-set not acknowledged - retrying", u.name);
        emit_log("warn", buf);
        u.state = MX_PROBE;     // re-probe in 1 s, then re-send SETMODE
        u.state_ms = now;
        u.attempts = 1;
      }
      return;
    }
    case MX_READY:
      return;
  }
}

// ---- One frame from a ready module ---------------------------------------------
static void mx_frame_step(MxUnit& u) {
  mx_send_cmd(u.addr, MX_CMD_ALLDATA, nullptr, 0);
  uint8_t payload[128];
  const int got = mx_recv_response(u.addr, MX_CMD_ALLDATA, payload, sizeof(payload),
                                   TOF_MATRIX_READ_TIMEOUT_MS);
  if (got == (int)sizeof(payload)) {
    u.err_streak = 0;
    for (int i = 0; i < 64; i++) {                     // uint16 little-endian, row-major
      u.grid[i] = (uint16_t)payload[2 * i] | ((uint16_t)payload[2 * i + 1] << 8);
    }
    int16_t raw_l, raw_r;
    mx_aggregate(u.geom, u.grid, &raw_l, &raw_r);
    u.left  = tof_filter_step(u.filt_l, raw_l);
    u.right = tof_filter_step(u.filt_r, raw_r);
    u.pub_ms = millis();
    // Stream the raw frame to the host in the normalized orientation the
    // aggregator uses (row 0 = top, col 0 = the SENSOR'S left), plus the per-row
    // floor-rejection thresholds so the host can mark which zones are floor.
    if (++u.emit_div >= u.emit_every) {
      u.emit_div = 0;
      uint16_t norm[64], rej[8];
      for (int r = 0; r < 8; r++) {
        for (int c = 0; c < 8; c++) norm[r * 8 + c] = u.grid[mx_raw_index(u.geom, r, c)];
        const float rm = u.geom.row_reject_mm[r];
        rej[r] = (rm >= 4095.0f) ? 4095 : (uint16_t)rm;
      }
      emit_tofmx(u.emit_type, norm, rej);
    }
  } else {
    // Failed/short frame: hold the last-good aggregates through a transient
    // error, then publish an honest -1 (same policy + streak as tof.cpp, same
    // documented fail-open in safety.cpp).
    if (u.err_streak < 255) u.err_streak++;
    if (u.err_streak == TOF_ERR_STREAK_STALE && (u.left != -1 || u.right != -1)) {
      u.left = u.right = -1;
      tof_filter_reset(u.filt_l); tof_filter_reset(u.filt_r);
      char buf[72];
      snprintf(buf, sizeof(buf), "tof_matrix %s: consecutive read errors - reporting -1", u.name);
      emit_log("warn", buf);
    } else if (u.err_streak > TOF_ERR_STREAK_STALE) {
      u.left = u.right = -1;
    }
    u.pub_ms = millis();   // an honest error is still a live publisher
  }
}

// ---- Poll task: OWNS every matrix I2C transaction ------------------------------
// WEDGE ISOLATION (field bug 2026-07-16): the RP2040 clock-stretches beyond
// what the ESP32 I2C driver tolerates, and a recovery-path deadlock inside
// Wire can block its caller FOREVER. When the frame reads ran inline on the
// 50 Hz sensor task, one wedged transaction froze the sensor task -> the
// state-lock chain -> control/telemetry/serial: the whole firmware went
// silent (both cores' output dead). Confining the I2C to this dedicated task
// means a wedge kills ONLY the matrices; tof_matrix_read*() then time out via
// pub_ms staleness and publish an honest -1 while the robot stays alive.
// The two modules share the bus, so a wedge in either silences both.
static void mx_task(void*) {
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    for (MxUnit& u : s_units) {
      const uint32_t now = millis();
      if (u.state == MX_READY) mx_frame_step(u);
      else mx_bringup_step(u, now);
    }
    vTaskDelayUntil(&last, pdMS_TO_TICKS(TOF_MATRIX_FRAME_INTERVAL_MS));
  }
}

// ---- Public API ---------------------------------------------------------------
void tof_matrix_init() {
  // Second controller, dedicated pins (pins.h): the matrices share a bus with
  // nothing else, and all their transactions run on their own expendable task.
  Wire1.begin(PIN_MX_I2C_SDA, PIN_MX_I2C_SCL);
  Wire1.setTimeOut(500);   // ride out slave clock-stretch; only this task waits
  emit_log("info", "tof_matrix: init deferred (mode switch needs ~5s settle)");
  xTaskCreatePinnedToCore(mx_task, "tofmx", 3584, nullptr, 1, nullptr, 1);
}

bool tof_matrix_ready() { return s_front.ready; }

// Lock-free snapshot for the 50 Hz sensor task: NO I2C here (see the wedge-
// isolation note on the poll task). If the poll task dies mid-transaction the
// publishes stop; the staleness bound below converts that silence into an
// honest -1 (and one log line) instead of freezing distances at their last
// value while the base keeps driving.
static void mx_snapshot(MxUnit& u, bool& warned, int16_t* l, int16_t* r) {
  if (!u.ready) { *l = -1; *r = -1; return; }
  if ((uint32_t)(millis() - u.pub_ms) > TOF_MATRIX_STALE_MS) {
    if (!warned) {
      warned = true;
      char buf[80];
      snprintf(buf, sizeof(buf), "tof_matrix %s: publisher stale (poll task wedged?) - reporting -1", u.name);
      emit_log("warn", buf);
    }
    *l = -1; *r = -1;
    return;
  }
  *l = u.left;
  *r = u.right;
}

void tof_matrix_read(int16_t* fl, int16_t* fr) {
  static bool s_warned = false;
  mx_snapshot(s_front, s_warned, fl, fr);
}

void tof_matrix_read_rear(int16_t* rl, int16_t* rr) {
  // The rear module faces backwards: its own left half is the robot's rear-RIGHT.
  static bool s_warned = false;
  mx_snapshot(s_rear, s_warned, rr, rl);
}

#else
// Matrix not present in this build: header-declared symbols still exist so tof.cpp
// can reference them unconditionally if ever needed (it doesn't today — its calls
// are compiled out), and a stray call is harmless.
void tof_matrix_init() {}
bool tof_matrix_ready() { return false; }
void tof_matrix_read(int16_t* fl, int16_t* fr) { *fl = -1; *fr = -1; }
void tof_matrix_read_rear(int16_t* rl, int16_t* rr) { *rl = -1; *rr = -1; }
#endif  // MOTION_TOF_MATRIX_PRESENT
