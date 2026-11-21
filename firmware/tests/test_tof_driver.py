"""Compile the drive firmware's real ring-sensor driver (tof.cpp, robot build) with scripted sensors
behind a fake multiplexer, and check what it publishes. No hardware, serial or network.

Owner, November 20: the front-right corner sensor had its own quarantine and filter, written for a
faulty module replaced in October; "that vestige is still there". Since 0.2.22 it is read exactly
like the other three corner sensors. The same evening two rear sensors that had stopped answering
were holding up every round; the driver now tries a silent sensor only every two seconds.
"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FW = ROOT / "djr3x_motion"
REAL = ("tof.cpp", "hal.h", "context.h", "protocol.h", "calib.h", "zone_envelope.h", "tof_filter.h",
        "tof_silent.h", "tof_xtalk.h", "tof_matrix.h", "pins.h")
NAMES = ("lf", "lb", "rf", "rb", "fl", "fr", "rl", "rr")      # The driver's own order: mux channel 0..7.
VALID, WRAP, NOTHING, TIMEOUT = 0, 7, 4, -1                      # VL53L1X range status; -1 here: no answer.

ARDUINO = r'''
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cstdlib>
extern uint32_t clock_ms;
inline uint32_t millis() { return clock_ms; }
typedef void* SemaphoreHandle_t;
'''

WIRE = r'''
#pragma once
#include <Arduino.h>
struct FakeWire {
  void beginTransmission(uint8_t address);
  size_t write(uint8_t value);
  uint8_t endTransmission();
};
extern FakeWire Wire;
'''

SENSORS = r'''
#pragma once
#include <Arduino.h>
// Scripted sensors: every object talks to whichever one the fake multiplexer has selected.
bool fake_init();
int fake_read(int* status);          // mm; *status -1 when the sensor did not answer (the wait is charged)
bool fake_timed_out();

class VL53L0X {
 public:
  void setTimeout(uint16_t) {}
  bool init() { return fake_init(); }
  bool setMeasurementTimingBudget(uint32_t) { return true; }
  void startContinuous(uint32_t = 0) {}
  uint16_t readRangeContinuousMillimeters() { int status; return (uint16_t)fake_read(&status); }
  bool timeoutOccurred() { return fake_timed_out(); }
  uint8_t getAddress() { return 0x29; }
};

class VL53L1X {
 public:
  enum DistanceMode { Short, Medium, Long, Unknown };
  enum RangeStatus : uint8_t { RangeValid = 0, SigmaFail = 1, SignalFail = 2, RangeValidMinRangeClipped = 3,
                               OutOfBoundsFail = 4, HardwareFail = 5, RangeValidNoWrapCheckFail = 6,
                               WrapTargetFail = 7, XtalkSignalFail = 9, SynchronizationInt = 10,
                               MinRangeFail = 13, None = 255 };
  enum regAddr : uint16_t { ALGO__CROSSTALK_COMPENSATION_PLANE_OFFSET_KCPS = 0x16,
                            ALGO__CROSSTALK_COMPENSATION_X_PLANE_GRADIENT_KCPS = 0x18,
                            ALGO__CROSSTALK_COMPENSATION_Y_PLANE_GRADIENT_KCPS = 0x1A,
                            ALGO__RANGE_IGNORE_THRESHOLD_MCPS = 0x3A,
                            RESULT__DSS_ACTUAL_EFFECTIVE_SPADS_SD0 = 0x8C };
  struct RangingData { uint16_t range_mm; RangeStatus range_status;
                       float peak_signal_count_rate_MCPS; float ambient_count_rate_MCPS; } ranging_data{};
  void setTimeout(uint16_t) {}
  bool init(bool = true) { return fake_init(); }
  bool setDistanceMode(DistanceMode) { return true; }
  bool setMeasurementTimingBudget(uint32_t) { return true; }
  void writeReg16Bit(uint16_t, uint16_t) {}
  uint16_t readReg16Bit(uint16_t) { return 0; }
  void startContinuous(uint32_t) {}
  uint16_t read(bool = true) {
    int status; const int mm = fake_read(&status);
    if (status >= 0) { ranging_data.range_mm = (uint16_t)mm; ranging_data.range_status = (RangeStatus)status; }
    return (uint16_t)mm;
  }
  bool timeoutOccurred() { return fake_timed_out(); }
  uint8_t getAddress() { return 0x29; }
};
'''

PROTO_IO = r'''
#pragma once
void emit_log(const char* lvl, const char* msg);
bool tof_debug_active();
void emit_tofraw(const char* sensor, int raw_mm, int status, int input_mm, int filtered_mm,
                 float sig_mcps, float amb_mcps, float spads);
'''

HARNESS = r'''
#include <iostream>
#include <string>
#include <utility>
#include <vector>
#include "hal.h"
#include "calib.h"
#include "proto_io.h"
#include <Wire.h>

uint32_t clock_ms = 5000;
struct Script { bool starts = true, on_bus = true, timed_out = false; std::vector<std::pair<int, int>> rows; size_t next = 0; };
static Script sensors[8];
static bool mux_present = true;
static int channel = 0, tx_address = 0, tx_value = 0, reads[8] = {0};

FakeWire Wire;
void FakeWire::beginTransmission(uint8_t address) { tx_address = address; tx_value = -1; }
size_t FakeWire::write(uint8_t value) { tx_value = value; return 1; }
uint8_t FakeWire::endTransmission() {
  if (tx_address == TOF_MUX_ADDR) {
    if (!mux_present) return 2;
    for (int c = 0; c < 8; c++) if (tx_value == (1 << c)) channel = c;
    return 0;
  }
  return sensors[channel].on_bus ? 0 : 2;          // an address probe behind the selected channel
}
bool fake_init() { return mux_present && sensors[channel].starts && sensors[channel].on_bus; }
int fake_read(int* status) {
  Script& s = sensors[channel];
  reads[channel]++;
  std::pair<int, int> row = s.rows.empty() ? std::make_pair(0, -1) : s.rows[s.next++ % s.rows.size()];
  if (!s.on_bus) row.second = -1;
  *status = row.second;
  if (row.second < 0) { clock_ms += TOF_TIMEOUT_MS; s.timed_out = true; return 0; }   // the blocking wait
  return row.first;
}
bool fake_timed_out() { const bool was = sensors[channel].timed_out; sensors[channel].timed_out = false; return was; }

void emit_log(const char* lvl, const char* msg) { std::cout << "L " << lvl << ' ' << msg << '\n'; }
bool tof_debug_active() { return false; }
void emit_tofraw(const char*, int, int, int, int, float, float, float) {}

int main() {
  std::string word;
  while (std::cin >> word) {
    if (word == "mux") std::cin >> mux_present;
    else if (word == "starts") { int c; std::cin >> c; std::cin >> sensors[c].starts; }
    else if (word == "bus") { int c; std::cin >> c; std::cin >> sensors[c].on_bus; }
    else if (word == "set") {                      // set <channel> <n> then n pairs: mm status
      int c, n; std::cin >> c >> n;
      sensors[c].rows.clear(); sensors[c].next = 0;
      for (int i = 0; i < n; i++) { int mm, status; std::cin >> mm >> status; sensors[c].rows.push_back({mm, status}); }
    }
    else if (word == "boot") hal_tof_init();
    else if (word == "run") {                      // run <calls>: one line per call of the 50 Hz sensor task
      int calls; std::cin >> calls;
      for (int i = 0; i < calls; i++) {
        TofMm out;
        hal_read_tof(out);
        std::cout << "T " << clock_ms << ' ' << out.lf << ' ' << out.lb << ' ' << out.rf << ' ' << out.rb << ' '
                  << out.fl_radial << ' ' << out.fr_radial << ' ' << out.rl_radial << ' ' << out.rr_radial;
        for (int c = 0; c < 8; c++) std::cout << ' ' << reads[c];
        std::cout << '\n';
        clock_ms += 20;
      }
    }
  }
}
'''


class RingDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="rex-tof-driver-")
        cls.addClassCleanup(cls.temp.cleanup)
        work = Path(cls.temp.name)
        for name in REAL:
            shutil.copyfile(FW / name, work / name)
        (work / "Arduino.h").write_text(ARDUINO)
        (work / "Wire.h").write_text(WIRE)
        (work / "VL53L0X.h").write_text(SENSORS)
        (work / "VL53L1X.h").write_text('#pragma once\n#include "VL53L0X.h"\n')
        (work / "proto_io.h").write_text(PROTO_IO)
        (work / "i2c_trunk.h").write_text("#pragma once\n")
        (work / "harness.cpp").write_text(HARNESS)
        cls.binary = work / "driver"
        done = subprocess.run(["clang++", "-std=c++17", "-Wall", "-Wextra", "-DMOTION_TOF_PRESENT=1", "-I", str(work),
                               str(work / "tof.cpp"), str(work / "harness.cpp"), "-o", str(cls.binary)],
                              capture_output=True, text=True)
        if done.returncode:
            raise AssertionError(done.stderr[-4000:])

    def drive(self, script):
        """Run a script; returns (rows, logs). Each row: clock, {sensor: published mm}, {sensor: reads so far}."""
        done = subprocess.run([str(self.binary)], input=script, capture_output=True, text=True, check=True)
        rows, logs = [], []
        for line in done.stdout.splitlines():
            if line.startswith("L "):
                logs.append((len(rows), line[2:]))
            else:
                values = [int(v) for v in line.split()[1:]]
                rows.append((values[0], dict(zip(NAMES, values[1:9])), dict(zip(NAMES, values[9:17]))))
        return rows, logs

    @staticmethod
    def stream(channel, rows):
        return f"set {channel} {len(rows)} " + " ".join(f"{mm} {status}" for mm, status in rows) + "\n"

    def quiet_room(self):
        """Every sensor answering a steady valid range."""
        return "".join(self.stream(c, [(900 + 10 * c, VALID)]) for c in range(8))

    @staticmethod
    def published(rows, name):
        """What a sensor published each time it was read (one value per round)."""
        out, reads = [], 0
        for _, values, counts in rows:
            if counts[name] != reads:
                reads = counts[name]
                out.append(values[name])
        return out

    def test_all_eight_start_and_publish_what_they_read(self):
        rows, logs = self.drive(self.quiet_room() + "boot\nrun 12\n")
        self.assertIn("tof: 8/8 sensors up", [text.split(" ", 1)[1] for _, text in logs])
        self.assertEqual(rows[-1][1], {name: 900 + 10 * c for c, name in enumerate(NAMES)})
        self.assertEqual([b[0] - a[0] for a, b in zip(rows, rows[1:])], [20] * 11)       # No read waited.

    def test_the_front_right_is_treated_exactly_like_the_front_left(self):
        # The same answers from both, in every mix that set the old front-right handling off.
        mixes = {
            "steady": [(480, VALID)],
            "half doubtful": [(480, VALID), (470, WRAP)],
            "mostly doubtful (the front-left that evening)": [(1170, VALID), (1180, WRAP), (1160, WRAP), (1175, WRAP)],
            "a close burst": [(3000, VALID)] * 6 + [(65, VALID)] * 3 + [(3000, VALID)] * 6,
            "nothing in range": [(0, NOTHING)],
            "something arriving": [(3000, VALID)] * 5 + [(400, VALID)] * 10,
        }
        for name, mix in mixes.items():
            with self.subTest(mix=name):
                rows, _ = self.drive(self.quiet_room() + self.stream(4, mix) + self.stream(5, mix) + "boot\nrun 160\n")
                left, right = self.published(rows, "fl"), self.published(rows, "fr")
                self.assertGreater(len(left), 35)
                self.assertEqual(left, right)

    def test_doubtful_readings_no_longer_silence_the_front_right(self):
        # 0.2.21 and earlier: half of the last sixteen doubtful put it at -1 until sixteen good in a row.
        rows, logs = self.drive(self.quiet_room() + self.stream(5, [(480, VALID), (470, WRAP)]) + "boot\nrun 200\n")
        seen = self.published(rows, "fr")
        self.assertNotIn(-1, seen)
        self.assertEqual(set(seen), {480, 780})          # The reading, and one release step while it is unsure.
        self.assertFalse([text for _, text in logs if "quarantin" in text or "consecutive" in text])

    def test_something_close_is_reported_by_its_second_reading_even_after_doubtful_ones(self):
        far_then_near = [(0, WRAP)] * 12 + [(300, VALID)] * 6
        rows, _ = self.drive(self.quiet_room() + self.stream(5, far_then_near) + "boot\nrun 72\n")
        seen = self.published(rows, "fr")
        self.assertEqual(seen[11], 4000)
        self.assertEqual(seen[12], 4000)                 # One reading alone could be a stray; the next confirms.
        self.assertEqual(seen[13:18], [300] * 5)

    def test_a_sensor_that_stops_answering_reports_none_and_stops_holding_up_the_round(self):
        # Rear-left and rear-right answer for a while, then nothing (the evening of November 19).
        script = self.quiet_room() + "boot\nrun 40\n" + self.stream(6, [(0, TIMEOUT)]) + self.stream(7, [(0, TIMEOUT)]) + "run 900\n"
        rows, logs = self.drive(script)
        texts = [text for _, text in logs]
        self.assertIn("warn tof[6] rl: 8 consecutive read errors - reporting -1", texts)
        self.assertIn("warn tof[7] rr: 8 consecutive read errors - reporting -1", texts)
        late = rows[240:]                                # Well after both were found silent.
        self.assertEqual({(row[1]["rl"], row[1]["rr"]) for row in late}, {(-1, -1)})
        for name in ("lf", "lb", "rf", "rb", "fl", "fr"):
            times = []
            reads = None
            for clock, _, counts in late:
                if counts[name] != reads:
                    reads = counts[name]
                    times.append(clock)
            gaps = sorted(b - a for a, b in zip(times, times[1:]))
            self.assertEqual(gaps[len(gaps) // 2], 80, name)
            self.assertLessEqual(gaps[-1], 180, name)    # A retry of one silent sensor, never both at once.
            self.assertEqual(late[-1][1][name], 900 + 10 * NAMES.index(name))
        tries = late[-1][2]["rl"] - late[0][2]["rl"]     # Still tried, about every two seconds.
        seconds = (late[-1][0] - late[0][0]) / 1000
        self.assertTrue(seconds / 2.3 <= tries <= seconds / 1.9 + 1, (tries, seconds))

    def test_an_unplugged_sensor_costs_no_waiting_at_all_and_returns_when_plugged_back(self):
        script = (self.quiet_room() + "boot\nrun 40\nbus 6 0\nrun 400\n")
        rows, _ = self.drive(script)
        late = rows[200:]
        self.assertEqual({b[0] - a[0] for a, b in zip(late, late[1:])}, {20})            # Not one timeout waited out.
        self.assertEqual({row[1]["rl"] for row in late}, {-1})
        rows, logs = self.drive(script + "bus 6 1\nrun 200\n")
        self.assertIn("info tof[6] rl: answering again", [text for _, text in logs])
        self.assertEqual(rows[-1][1]["rl"], 960)

    def test_a_sensor_that_fails_at_start_is_never_read_and_never_waited_on(self):
        rows, logs = self.drive(self.quiet_room() + "starts 6 0\nstarts 7 0\nboot\nrun 100\n")
        texts = [text for _, text in logs]
        self.assertIn("warn tof: 6/8 sensors up", texts)
        self.assertEqual({(row[1]["rl"], row[1]["rr"]) for row in rows}, {(-1, -1)})
        self.assertEqual((rows[-1][2]["rl"], rows[-1][2]["rr"]), (0, 0))
        self.assertEqual({b[0] - a[0] for a, b in zip(rows, rows[1:])}, {20})
        self.assertEqual(rows[-1][1]["fr"], 950)


if __name__ == "__main__":
    unittest.main()
