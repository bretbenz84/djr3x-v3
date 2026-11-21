// tof_silent.h: a ring sensor that has stopped answering is asked less often.
//
// Each read of a sensor that does not answer waits TOF_TIMEOUT_MS. Live, November 20 2026:
// the two rear corner sensors had stopped answering the evening before, and the round that
// visits every ring sensor went from about 80 ms to about 230 ms. Every other ring sensor was
// three times staler, and the front-right's health rule of the time (retired in 0.2.22), which
// read any pause over 200 ms as a fault, discarded a sensor giving a clean valid range.
//
// A sensor that has missed TOF_ERR_STREAK_STALE reads in a row already publishes -1. From
// then on it is tried once every TOF_SILENT_RETRY_MS (each sensor at its own offset, so two
// silent ones are not tried in the same round) and comes back by itself on its first answer.
// Pure: no I2C here. Requires calib.h first.
#pragma once
#include <stdint.h>

struct TofSilent {
  uint8_t misses = 0;        // reads in a row that got no answer
  uint32_t retry_at = 0;     // while silent: when to ask again
};

static inline bool tof_silent(const TofSilent& s) { return s.misses >= TOF_ERR_STREAK_STALE; }

// Ask this sensor now? Always while it answers; once silent, only when its retry is due.
static inline bool tof_silent_ask(TofSilent& s, uint32_t now_ms) {
  if (!tof_silent(s)) return true;
  if ((int32_t)(now_ms - s.retry_at) < 0) return false;
  s.retry_at = now_ms + TOF_SILENT_RETRY_MS;
  return true;
}

// Record whether a read was answered (any result the sensor gave, valid or not). Returns
// true when a silent sensor has just answered again. offset_ms spreads the retries.
static inline bool tof_silent_note(TofSilent& s, bool answered, uint32_t now_ms, uint32_t offset_ms) {
  if (answered) {
    const bool back = tof_silent(s);
    s.misses = 0;
    return back;
  }
  if (s.misses < 255) ++s.misses;
  if (s.misses == TOF_ERR_STREAK_STALE) s.retry_at = now_ms + TOF_SILENT_RETRY_MS + offset_ms;
  return false;
}
