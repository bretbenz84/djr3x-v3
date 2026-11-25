// DJ-R3X combined LED board: one ESP32 driving the head and chest strips.
#pragma once
#include <stdint.h>

#define LEDS_VERSION   "0.9.4"
#define LEDS_BAUD      115200

// Plain outputs on every classic ESP32 dev board: not boot-strapping pins, not the
// flash pins (6-11), not input-only (34-39), not the USB serial pins (1, 3).
#define HEAD_DATA_PIN  18   // eyes + mouth, 82 pixels
#define CHEST_DATA_PIN 19   // chest panels, 98 pixels

// Startup paced to Rex's own start (owner, November 21, 2026), the same on both strips.
// STARTUP:<ms> carries the time the host expects to need; READY says it is ready. Bare
// STARTUP (the LED menu) times itself. With a host time and no READY, startup waits the
// grace and then finishes anyway.
#define LEDS_STARTUP_DEFAULT_MS 12800UL
#define LEDS_STARTUP_MIN_MS      3000L
#define LEDS_STARTUP_MAX_MS     60000L
#define LEDS_READY_GRACE_MS     10000UL
#define LEDS_FINALE_MS            900UL   // from READY to steady and fully lit

// A repeatable coin for flicker: the same answer for the same moment and place.
static inline uint8_t ledsFlick8(uint32_t a, uint32_t b) {
    uint32_t h = (a + 0x9E3779B9u) * 0x85EBCA6Bu ^ (b + 0x7F4A7C15u) * 0xC2B2AE35u;
    h ^= h >> 15;  h *= 0x2C1B3C6Du;  h ^= h >> 12;
    return (uint8_t)(h >> 8);
}
