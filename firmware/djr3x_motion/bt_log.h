// bt_log.h: see bt_log.cpp.
#pragma once
#include <stdint.h>
#define BT_LOG_EVERY_MS 30000u     // The Bluetooth stack's messages are summed up at most this often.
void bt_log_service(uint32_t now_ms);   // Call from the Arduino loop.
