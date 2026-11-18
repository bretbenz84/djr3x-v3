"""Compile the drive firmware's real control loop (control.cpp, robot build) with fake wheels and
serial, and check what reaches the wheels on the charger. No hardware, serial or network.

Owner, November 18: a telepresence caller may turn Rex in place while he is on the charger; a spin
cannot pull the cord, travel can. So on the charger only the Mac's velocity drive with zero linear
turns the wheels. Travel, finite commands and the gamepad stay locked.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "djr3x_motion"
REAL = ("control.cpp", "control.h", "context.h", "protocol.h", "calib.h", "zone_envelope.h", "hal.h",
        "drive_shaping.h", "stick_shaping.h")

ARDUINO = r'''
#pragma once
#include <stdint.h>
#include <cmath>
extern uint32_t clock_ms;
inline uint32_t millis() { return clock_ms; }
typedef void* SemaphoreHandle_t;
#define portMAX_DELAY 0
inline int xSemaphoreTakeRecursive(SemaphoreHandle_t, int) { return 1; }
inline int xSemaphoreGiveRecursive(SemaphoreHandle_t) { return 1; }
'''

PROTO_IO = r'''
#pragma once
#include "context.h"
void emit_done(uint32_t seq, DoneResult r, const Odom& od);
void emit_event(const char* event);
void emit_log(const char* lvl, const char* msg);
'''

HARNESS = r'''
#include <cstdio>
#include <cstring>
#include "control.h"
#include "hal.h"

uint32_t clock_ms = 1000;
MotionContext g_ctx;
SemaphoreHandle_t g_state_mux = nullptr, g_tx_mux = nullptr;

// Fake wheels: whatever the loop commands is what they do.
static float wheel_lin = 0.0f, wheel_ang = 0.0f, max_lin = 0.0f, max_ang = 0.0f;
static int last_done = -1;
void hal_read_odom(Odom& o, float) { o.lin = wheel_lin; o.ang = wheel_ang; }
bool hal_drive_velocity(float lin, float ang, float, bool, float, bool) {
  wheel_lin = lin; wheel_ang = ang;
  if (fabsf(lin) > max_lin) max_lin = fabsf(lin);
  if (fabsf(ang) > max_ang) max_ang = fabsf(ang);
  return true;
}
void hal_drive_wheel_raw(int, float) {}
void hal_motors_off() { wheel_lin = wheel_ang = 0.0f; }
void emit_done(uint32_t, DoneResult r, const Odom&) { last_done = (int)r; }
void emit_event(const char*) {}
void emit_log(const char*, const char*) {}

// One second at the 50 Hz control rate; `each` runs at the host's 10 Hz.
template <typename F> static void run(int ticks, F each) {
  for (int i = 0; i < ticks; ++i) {
    if (i % 5 == 0) each();
    clock_ms += 20;
    control_tick(0.02f);
  }
}

int main(int argc, char** argv) {
  const char* s = argc > 1 ? argv[1] : "";
  g_ctx.charging = strstr(s, "charging") != nullptr;
  uint32_t seq = 1;
  if (!strcmp(s, "spin_charging") || !strcmp(s, "spin_unplugged")) {
    run(50, [&] { ctl_drive(0.0f, 0.5f, seq++); });
  } else if (!strcmp(s, "travel_charging") || !strcmp(s, "travel_unplugged")) {
    run(50, [&] { ctl_drive(0.2f, 0.0f, seq++); });
  } else if (!strcmp(s, "arc_charging")) {
    run(50, [&] { ctl_drive(0.2f, 0.5f, seq++); });
  } else if (!strcmp(s, "gamepad_spin_charging")) {
    run(50, [&] { ctl_manual_drive(0.0f, 0.5f, 0.0f); });
  } else if (!strcmp(s, "finite_turn_charging")) {
    ctl_turn(90.0f, 60.0f, seq++);
    run(50, [] {});
  } else if (!strcmp(s, "spin_then_travel_charging")) {
    run(25, [&] { ctl_drive(0.0f, 0.5f, seq++); });
    run(1, [&] { ctl_drive(0.1f, 0.5f, seq++); });
    max_lin = 0.0f;
  } else {
    return 2;
  }
  printf("%.4f %.4f %.4f %d\n", max_lin, max_ang, wheel_ang, last_done);
  return 0;
}
'''


class ChargerSpinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="rex-charger-spin-")
        work = Path(cls.tmp.name)
        for name in REAL:
            shutil.copyfile(FW / name, work / name)
        (work / "Arduino.h").write_text(ARDUINO)
        (work / "proto_io.h").write_text(PROTO_IO)
        (work / "test.cpp").write_text(HARNESS)
        cls.binary = work / "test"
        subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                        "-DMOTION_HW_PRESENT=1",
                        "-I", str(work), str(work / "test.cpp"), str(work / "control.cpp"), "-o", str(cls.binary)],
                       check=True, capture_output=True, text=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def wheels(self, scenario):
        out = subprocess.run([str(self.binary), scenario], capture_output=True, text=True, check=True).stdout
        max_lin, max_ang, final_ang, done = out.split()
        return float(max_lin), float(max_ang), float(final_ang), int(done)

    def test_a_turn_in_place_from_the_mac_turns_on_the_charger(self):
        max_lin, max_ang, final_ang, _ = self.wheels("spin_charging")
        self.assertEqual(max_lin, 0.)
        self.assertGreater(final_ang, .45)   # Ramped up to the 0.5 rad/s asked.

    def test_any_travel_on_the_charger_moves_nothing(self):
        for scenario in ("travel_charging", "arc_charging"):
            with self.subTest(scenario):
                max_lin, max_ang, _, _ = self.wheels(scenario)
                self.assertEqual((max_lin, max_ang), (0., 0.))

    def test_travel_asked_mid_spin_stops_the_turn_at_once(self):
        max_lin, _, final_ang, _ = self.wheels("spin_then_travel_charging")
        self.assertEqual((max_lin, final_ang), (0., 0.))

    def test_the_gamepad_and_finite_turns_stay_locked_on_the_charger(self):
        max_lin, max_ang, _, _ = self.wheels("gamepad_spin_charging")
        self.assertEqual((max_lin, max_ang), (0., 0.))
        max_lin, max_ang, _, done = self.wheels("finite_turn_charging")
        self.assertEqual((max_lin, max_ang), (0., 0.))
        self.assertEqual(done, 2)   # DONE_ABORTED: the cord outranks Rex's own turn.

    def test_off_the_charger_driving_is_unchanged(self):
        self.assertGreater(self.wheels("travel_unplugged")[0], .15)
        self.assertGreater(self.wheels("spin_unplugged")[2], .45)


if __name__ == "__main__":
    unittest.main()
