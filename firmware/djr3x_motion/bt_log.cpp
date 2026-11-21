// bt_log.cpp: what the Bluetooth stack wants to print goes out as one of our own log lines.
//
// The Bluepad32 board package starts its Bluetooth stack whether or not our gamepad code is
// built in, and prints its messages straight to the serial port from its own task. November 20
// 2026: with no pad switched on it printed "Failed to disconnect HIDS client for hids_cid=0,
// status=2" about three times every two seconds, into the middle of our JSON lines, and one line
// in eight reached the Mac unreadable (159 of about 1,300 in a minute; 1 on a build with no
// Bluetooth stack). The package is pre-built with its logging fixed at "errors", but its print
// routine is a weak symbol: this one replaces it. Nothing is written from the stack's task. The
// newest message and a count are kept, and the Arduino loop reports them as an ordinary log line,
// at most once every BT_LOG_EVERY_MS.
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <Arduino.h>
#include "proto_io.h"
#include "bt_log.h"

static char s_text[96];
static volatile uint32_t s_count = 0;       // messages since boot
static uint32_t s_reported = 0, s_reported_ms = 0;

extern "C" void uni_log(const char* fmt, ...) {
  va_list args;
  va_start(args, fmt);
  vsnprintf(s_text, sizeof(s_text), fmt, args);
  va_end(args);
  s_count = s_count + 1;
}

void bt_log_service(uint32_t now_ms) {
  const uint32_t count = s_count;
  if (count == s_reported) return;
  if (s_reported && (uint32_t)(now_ms - s_reported_ms) < BT_LOG_EVERY_MS) return;
  char text[sizeof(s_text)];
  memcpy(text, s_text, sizeof(text));
  text[sizeof(text) - 1] = 0;
  for (char* c = text; *c; c++) if (*c == '\n' || *c == '\r') *c = ' ';
  char buf[160];
  snprintf(buf, sizeof(buf), "bluetooth (%lu since the last report): %s", (unsigned long)(count - s_reported), text);
  s_reported = count;
  s_reported_ms = now_ms;
  emit_log("info", buf);
}
