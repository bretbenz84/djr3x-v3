// DJ-R3X combined LED board: one ESP32 driving the head and chest strips.
#pragma once

#define LEDS_VERSION   "0.5.0"
#define LEDS_BAUD      115200

// Plain outputs on every classic ESP32 dev board: not boot-strapping pins, not the
// flash pins (6-11), not input-only (34-39), not the USB serial pins (1, 3).
#define HEAD_DATA_PIN  18   // eyes + mouth, 82 pixels
#define CHEST_DATA_PIN 19   // chest panels, 98 pixels
