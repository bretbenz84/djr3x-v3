// tof_matrix_uart.h — one SEN0628 reply read byte by byte off a serial line. Pure: no
// Serial, no FreeRTOS, so the native checks feed it noise, wrong replies and frames cut
// short (firmware/tests/test_tof_matrix_uart.py).
//
// Why (owner, November 24, 2026): the two 8x8 boards shared one I2C bus, and a board that
// hung holding the clock line blinded both until their power was cycled. The front board
// now has its own serial port (GPIO34 receive, GPIO15 send, pins.h); the rear stays on I2C.
//
// The board's switch set to UART, it speaks the same packets as over I2C (DFRobot_MatrixLidar
// 1.x, DFRobot_MatrixLidar_UART): the request {0x55, argsNumH, argsNumL, cmd, args...} sent a
// byte a millisecond, and the reply {status, cmd, lenL, lenH, payload[len]} sent by the board
// as soon as it has one, with no status to poll. Status 0x53 is success, 0x63 failure.
// Anything before a status byte (a floating line with the board unplugged, the tail of a
// reply that came too late) is skipped.
#pragma once
#include <stdint.h>

enum MxUartResult : uint8_t {
  MX_UART_MORE,     // keep feeding
  MX_UART_OK,       // a whole success reply for the command; length() bytes stored
  MX_UART_FAILED,   // a whole reply with the failure status (payload drained)
  MX_UART_BAD,      // a reply for another command, or a length no reply has
};

static const uint8_t MX_UART_STATUS_OK     = 0x53;
static const uint8_t MX_UART_STATUS_FAILED = 0x63;
static const uint16_t MX_UART_MAX_LEN      = 1000;   // the vendor library's own sanity bound

struct MxUartReply {
  enum Phase : uint8_t { HUNT, CMD, LEN_L, LEN_H, BODY };
  Phase phase = HUNT;
  uint8_t want = 0, status = 0;
  uint16_t len = 0, got = 0, max = 0;
  uint8_t* out = nullptr;
  bool answered = false;      // a status byte arrived: something on the line is a board

  void start(uint8_t cmd, uint8_t* dst, uint16_t cap) {
    phase = HUNT; want = cmd; status = 0; len = got = 0; out = dst; max = cap; answered = false;
  }
  uint16_t length() const { return got < max ? got : max; }

  MxUartResult finish() const { return status == MX_UART_STATUS_OK ? MX_UART_OK : MX_UART_FAILED; }

  MxUartResult feed(uint8_t b) {
    switch (phase) {
      case HUNT:
        if (b == MX_UART_STATUS_OK || b == MX_UART_STATUS_FAILED) {
          status = b; answered = true; phase = CMD;
        }
        return MX_UART_MORE;
      case CMD:
        if (b != want) { phase = HUNT; return MX_UART_BAD; }
        phase = LEN_L;
        return MX_UART_MORE;
      case LEN_L:
        len = b; phase = LEN_H;
        return MX_UART_MORE;
      case LEN_H:
        len |= (uint16_t)b << 8;
        if (len > MX_UART_MAX_LEN) { phase = HUNT; return MX_UART_BAD; }
        if (len == 0) { phase = HUNT; return finish(); }
        phase = BODY;
        return MX_UART_MORE;
      case BODY:
        if (got < max && out) out[got] = b;
        ++got;
        if (got < len) return MX_UART_MORE;
        phase = HUNT;
        got = len;
        return finish();
    }
    return MX_UART_MORE;
  }
};

// The request packet for cmd into dst (at least 4 + args_len bytes); returns its length.
static inline int mx_uart_request(uint8_t* dst, uint8_t cmd, const uint8_t* args, uint8_t args_len) {
  dst[0] = 0x55;
  dst[1] = (uint8_t)(((args_len + 1) >> 8) & 0xFF);   // argsNum = len(args) + 1, the library's quirk
  dst[2] = (uint8_t)((args_len + 1) & 0xFF);
  dst[3] = cmd;
  for (uint8_t i = 0; i < args_len; i++) dst[4 + i] = args[i];
  return 4 + args_len;
}
