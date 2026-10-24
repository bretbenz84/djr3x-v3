// proto_io.h — serial framing, command parse+dispatch, and all wire emitters.
//
// proto_poll() drains the serial RX, frames NDJSON lines (<=512 B, drop-on-
// overflow), parses, and dispatches commands into control/config. The emit_*
// helpers serialize one JSON line atomically under g_tx_mux — call them only
// while NOT holding g_state_mux (see context.h lock-order rule).
#pragma once
#include "context.h"
#include "protocol.h"

void proto_init();
void proto_poll();        // call frequently from the serial task

// Emitters (each takes g_tx_mux internally):
void emit_hello();
void emit_telemetry();
void emit_ack(uint32_t seq, bool accepted, AckReason reason);
void emit_config_ack(uint32_t seq, bool clamped, const MotionParams& p);
void emit_done(uint32_t seq, DoneResult r, const Odom& od);
void emit_event(const char* event);                                 // no payload
void emit_event_kv(const char* event, const char* key, const char* val);
void emit_event_boot(uint32_t boot_id);
void emit_log(const char* lvl, const char* msg);
// Raw 8x8 matrix ToF frame for the host (normalized: row 0 = top, col 0 = the
// SENSOR'S left — the robot's left for the front "tofmx", the robot's RIGHT for
// the backward-facing "tofmx_rear"). mm[64] row-major; rej[8] = per-row
// floor-rejection threshold (mm, capped 4095). Hex (3 chars/zone), line ~250 B.
void emit_tofmx(const char* type, const uint16_t mm[64], const uint16_t rej[8]);
// Opt-in, time-bounded front-right raw samples, emitted only while stationary.
// Stationary raw diagnostic for one front radial sensor ("fl" or "fr"): the sensor's
// own range/status plus its return strength, ambient light and active SPADs, so a
// phantom near return can be told from a real surface (strong) or cover-glass
// crosstalk (weak, scene-independent). Only while tof_debug is active.
bool tof_debug_active();
void emit_tofraw(const char* sensor, int raw_mm, int status, int input_mm, int filtered_mm,
                 float sig_mcps, float amb_mcps, float spads);
