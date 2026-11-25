// tof_matrix.cpp — DFRobot 8x8 Matrix ToF (SEN0628) front and rear obstacle sensors.
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
// ---- Packets (confirmed against DFRobot_MatrixLidar 1.x source) ---------------
//   request:  {0x55, argsNumH, argsNumL, cmd, args...}, argsNum = len(args) + 1 (the
//             library's quirk; the RP2040 expects it), sent a byte a millisecond.
//     CMD_SETMODE (1): args {0,0,0, matrix} with matrix=8 for 8x8. Needs ~5 s
//                      settle after the ACK before frames are valid.
//     CMD_ALLDATA (2): no args. Response payload = 128 bytes: 64x uint16 LE
//                      millimetres, row-major (index = y*8 + x). 0 = no return.
//   reply:    {status, cmd, lenL, lenH, payload[len]}, status 0x53 success, 0x63 failed,
//             sent by the board as soon as it has one (tof_matrix_uart.h reads it).
//
// ---- Each board on its own serial port (owner, November 24, 2026) ---------------
// Until 0.2.25 both boards shared one I2C bus (GPIO4/5), and a board holding the clock
// line low blinded both until their power was cut (38 stalls an hour on battery). 0.2.26
// moved the front to serial; 0.2.28 moves the rear too, on the same two wires it had on
// the bus. Each board's I2C/UART switch is on UART. A serial line has no address to ACK,
// so the probe is a frame request answered; nothing can hold a serial line for the other
// board, and each board has its own poll task.
#include "hal.h"

#if MOTION_TOF_MATRIX_PRESENT
#include "pins.h"
#include "calib.h"
#include "tof_filter.h"
#include "tof_matrix_floor.h"
#include "tof_matrix_link.h"
#include "tof_matrix_uart.h"
#include "proto_io.h"
#include <Arduino.h>
#include <math.h>

static const uint8_t MX_CMD_SETMODE   = 1;
static const uint8_t MX_CMD_ALLDATA   = 2;
static const uint8_t MX_MODE_8X8      = 8;      // eMatrix_8X8

// ---- The two modules ---------------------------------------------------------
// FRONT and REAR (owner 2026-10-19: same height, facing straight back). Each runs its own
// small state machine (tof_matrix_link.h) on its own task, so neither waits on the other's
// 5 s mode switch, and a module that is absent or unplugged simply keeps being probed once
// a second. Since 0.2.19 a module that stops answering after it was streaming goes back
// through the same setup instead of failing until the next power cycle. Nothing talks to a
// module during its blind settle window.
struct MxUnit {
  const char* name;            // log label
  HardwareSerial* port;
  int rx_pin, tx_pin;          // ESP32 receive (the board's D/T) and send (its C/R)
  uint8_t probe_log_every;     // log a missing module every Nth failed probe
  const char* emit_type;       // telemetry line type for the raw grid
  uint8_t emit_every;          // stream every Nth good frame
  MxGeom geom;
  // Poll-task owned:
  MxLink link;                 // probe / setup / read / setup-again state
  MxUartReply reply;           // the reply being read (kept across steps for the mode ack)
  uint8_t mode_reply[8];       // where the mode request's reply lands
  uint16_t rx_bytes;           // bytes heard by the last read, and how soon the first came
  int16_t rx_first_ms;         //   (-1: none), for the line logged when a frame is not read
  uint32_t frame_fails;        // frames not read since the last good one
  bool first_logged;           // the first good frame's timing was logged
  uint16_t grid[64];
  TofFilt filt_l, filt_r;
  uint8_t emit_div;
  // Published to the sensor task (lock-free snapshot):
  volatile bool ready;
  volatile int16_t left, right;   // SENSOR-frame halves, mm; -1 = no data
  volatile uint32_t pub_ms;
};

static HardwareSerial s_front_port(2), s_rear_port(1);

static MxUnit s_units[2] = {
  {"front", &s_front_port, PIN_MX_FRONT_RX, PIN_MX_FRONT_TX, 10, "tofmx", 2,
   {TOF_MATRIX_FLIP_V != 0, TOF_MATRIX_FLIP_H != 0, TOF_MATRIX_HEIGHT_M,
    TOF_MATRIX_FLOOR_ROW_MM, {0}, {0}},
   MxLink(), MxUartReply(), {0}, 0, -1, 0, false, {0}, TofFilt(), TofFilt(), 0, false, -1, -1, 0},
  {"rear", &s_rear_port, PIN_MX_REAR_RX, PIN_MX_REAR_TX, 60, "tofmx_rear", TOF_MATRIX_REAR_EMIT_EVERY,
   {TOF_MATRIX_REAR_FLIP_V != 0, TOF_MATRIX_REAR_FLIP_H != 0, TOF_MATRIX_REAR_HEIGHT_M,
    TOF_MATRIX_REAR_FLOOR_ROW_MM, {0}, {0}},
   MxLink(), MxUartReply(), {0}, 0, -1, 0, false, {0}, TofFilt(), TofFilt(), 0, false, -1, -1, 0},
};
static MxUnit& s_front = s_units[0];
static MxUnit& s_rear  = s_units[1];

// ---- Bounded serial primitives -------------------------------------------------
// Only a board's own poll task touches its port. Nothing here blocks past its deadline:
// bytes are taken as they arrive, between 1 ms sleeps.
static void mx_uart_start(MxUnit& u, const char* when) {
  u.port->setRxBufferSize(512);   // a frame reply is 132 bytes
  u.port->begin(TOF_MATRIX_UART_BAUD, SERIAL_8N1, u.rx_pin, u.tx_pin);
  char buf[96];
  snprintf(buf, sizeof(buf), "tof_matrix %s: serial %s (receive GPIO%d, send GPIO%d, %d baud)", u.name,
           when, u.rx_pin, u.tx_pin, TOF_MATRIX_UART_BAUD);
  emit_log("info", buf);
}

static void mx_uart_reset(MxUnit& u) {
  u.port->end();
  vTaskDelay(pdMS_TO_TICKS(2));
  mx_uart_start(u, "restarted");
}

// Drop whatever is waiting (noise, a reply that came too late), then send the request a
// byte a millisecond as the vendor library does, and start reading its reply.
static void mx_uart_send(MxUnit& u, uint8_t cmd, const uint8_t* args, uint8_t args_len,
                         uint8_t* dst, uint16_t cap) {
  while (u.port->available() > 0) u.port->read();
  uint8_t pkt[16];
  const int n = mx_uart_request(pkt, cmd, args, args_len);
  for (int i = 0; i < n; i++) {
    u.port->write(pkt[i]);
    vTaskDelay(pdMS_TO_TICKS(1));
  }
  u.reply.start(cmd, dst, cap);
}

// Read the reply started by mx_uart_send until it is whole or deadline_ms has passed.
// The payload length on success, else -1.
static int mx_uart_recv(MxUnit& u, uint32_t deadline_ms) {
  const uint32_t t0 = millis();
  u.rx_bytes = 0;
  u.rx_first_ms = -1;
  for (;;) {
    while (u.port->available() > 0) {
      if (u.rx_first_ms < 0) u.rx_first_ms = (int16_t)(millis() - t0);
      ++u.rx_bytes;
      const MxUartResult r = u.reply.feed((uint8_t)u.port->read());
      if (r == MX_UART_OK) return u.reply.length();
      if (r != MX_UART_MORE) return -1;
    }
    if ((uint32_t)(millis() - t0) >= deadline_ms) return -1;
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

// ---- One frame from a ready module ---------------------------------------------
// True when a whole frame was read and published. What a failure means (hold the
// last values, report -1, set the module up again) is decided in mx_link_step.
static bool mx_read_frame(MxUnit& u) {
  uint8_t payload[128];
  mx_uart_send(u, MX_CMD_ALLDATA, nullptr, 0, payload, sizeof(payload));
  const int got = mx_uart_recv(u, TOF_MATRIX_UART_READ_TIMEOUT_MS);
  {
    // Say what came back when a frame is not read: nothing, a late reply, a short one, or a
    // failure status (the first and every 50th), and when the first good one came.
    char buf[140];
    if (got != (int)sizeof(payload)) {
      if (u.frame_fails++ % 50 == 0) {
        snprintf(buf, sizeof(buf), "tof_matrix %s: frame not read (%u bytes in %d ms, first after %d ms, "
                 "status 0x%02X, length %u, %d fails)", u.name, u.rx_bytes, TOF_MATRIX_UART_READ_TIMEOUT_MS,
                 u.rx_first_ms, u.reply.status, u.reply.len, (int)u.frame_fails);
        emit_log("warn", buf);
      }
    } else {
      u.frame_fails = 0;
      if (!u.first_logged) {
        u.first_logged = true;
        snprintf(buf, sizeof(buf), "tof_matrix %s: first frame on serial, reply began after %d ms",
                 u.name, u.rx_first_ms);
        emit_log("info", buf);
      }
    }
  }
  // A module that reset comes back in its power-on mode and answers with a shorter
  // frame: that is a failure here, and what sends it back through setup.
  if (got != (int)sizeof(payload)) return false;
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
  return true;
}

// ---- The board as tof_matrix_link.h sees it ---------------------------------------
// The board is not talked to through its ~5 s mode switch: the mode reply it sends when
// done waits in the receive buffer, read by the same parser after the settle.
struct MxIo {
  MxUnit& u;
  bool probe() {
    // No address on a serial line: a board is there when a frame request gets any reply.
    uint8_t scratch[128];
    mx_uart_send(u, MX_CMD_ALLDATA, nullptr, 0, scratch, sizeof(scratch));
    mx_uart_recv(u, TOF_MATRIX_UART_READ_TIMEOUT_MS);
    return u.reply.answered;
  }
  void send_setmode() {
    const uint8_t args[4] = {0, 0, 0, MX_MODE_8X8};
    mx_uart_send(u, MX_CMD_SETMODE, args, sizeof(args), u.mode_reply, sizeof(u.mode_reply));
  }
  bool mode_acked() { return mx_uart_recv(u, TOF_MATRIX_UART_READ_TIMEOUT_MS) >= 0; }
  bool read_frame() { return mx_read_frame(u); }
  void bus_reset() { mx_uart_reset(u); }   // the link's "restart the bus": reopen the port
};

static void mx_drop_values(MxUnit& u) {
  u.left = u.right = -1;
  tof_filter_reset(u.filt_l); tof_filter_reset(u.filt_r);
}

// ---- One step for one module: act on what the link decided ------------------------
static void mx_unit_step(MxUnit& u) {
  MxIo io{u};
  char buf[128];
  const MxEvent ev = mx_link_step(u.link, io, millis());
  switch (ev) {
    case MX_EV_NO_ACK:
      if (((u.link.attempts - 1) % u.probe_log_every) == 0) {
        snprintf(buf, sizeof(buf),
                 "tof_matrix %s: no reply on serial (attempt %lu) - check wiring, power, I2C/UART switch",
                 u.name, (unsigned long)u.link.attempts);
        emit_log("warn", buf);
      }
      break;
    case MX_EV_READY:
    case MX_EV_BACK:
      mx_compute_geometry(u.geom);
      u.pub_ms = millis();   // arm the staleness clock BEFORE readers can see ready
      u.ready = true;
      if (ev == MX_EV_BACK) {
        snprintf(buf, sizeof(buf), "tof_matrix %s: back - 8x8 matrix set up again on serial", u.name);
      } else {
        snprintf(buf, sizeof(buf), "tof_matrix %s: 8x8 matrix ready on serial (floor rejection active)", u.name);
      }
      emit_log("info", buf);
      break;
    case MX_EV_MODE_UNACKED:
      snprintf(buf, sizeof(buf), "tof_matrix %s: 8x8 mode-set not acknowledged - retrying", u.name);
      emit_log("warn", buf);
      break;
    case MX_EV_LOST:
      // Failed/short frames: the last-good aggregates were held through a transient
      // error; from here publish an honest -1 (same policy + streak as tof.cpp, same
      // documented fail-open in safety.cpp).
      if (u.left != -1 || u.right != -1) {
        snprintf(buf, sizeof(buf), "tof_matrix %s: consecutive read errors - reporting -1", u.name);
        emit_log("warn", buf);
      }
      mx_drop_values(u);
      u.pub_ms = millis();   // an honest error is still a live publisher
      break;
    case MX_EV_STILL_LOST:
      u.left = u.right = -1;
      u.pub_ms = millis();
      break;
    case MX_EV_FRAME_HELD:
      u.pub_ms = millis();
      break;
    case MX_EV_REINIT:
      u.ready = false;       // readers get -1 until the module streams again
      mx_drop_values(u);
      u.emit_div = 0;
      snprintf(buf, sizeof(buf), "tof_matrix %s: not answering - setting it up again", u.name);
      emit_log("warn", buf);
      break;
    case MX_EV_FRAME:
    case MX_EV_NONE:
      break;
  }
}

// ---- Poll tasks: one per board, each owning its port -------------------------------
// WEDGE ISOLATION (field bug 2026-07-16): when the frame reads ran inline on the 50 Hz
// sensor task, one wedged I2C transaction froze the whole firmware. The reads stay on
// their own expendable tasks: if one stops, tof_matrix_read*() time out via pub_ms
// staleness and publish an honest -1 while the robot stays alive. One task per board
// since 0.2.27: on a shared task a silent board's waits held up the other's frames.
static void mx_task(void* arg) {
  MxUnit& u = *static_cast<MxUnit*>(arg);
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    mx_unit_step(u);
    vTaskDelayUntil(&last, pdMS_TO_TICKS(TOF_MATRIX_FRAME_INTERVAL_MS));
  }
}

// ---- Public API ---------------------------------------------------------------
void tof_matrix_init() {
  // Each board on its own serial port and task (pins.h).
  mx_uart_start(s_front, "started");
  mx_uart_start(s_rear, "started");
  emit_log("info", "tof_matrix: init deferred (mode switch needs ~5s settle)");
  xTaskCreatePinnedToCore(mx_task, "tofmx", 4096, &s_front, 1, nullptr, 1);
  xTaskCreatePinnedToCore(mx_task, "tofmx_rear", 4096, &s_rear, 1, nullptr, 1);
}

bool tof_matrix_ready() { return s_front.ready; }

// Lock-free snapshot for the 50 Hz sensor task: no port reads here (see the wedge-
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
  warned = false;   // publishing again: a later stall is logged again
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
