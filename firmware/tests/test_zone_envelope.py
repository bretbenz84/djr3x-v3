"""Compile the obstacle envelope natively: how far out braking starts and where the stop
line sits at a given speed, and a wall approach by a base that lags its commanded slowdown.
No I/O. The approach loop copies control_tick's taper and reflex order; the envelope, the
reflex deceleration and the constants are the firmware's own."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>
#include "drive_shaping.h"
#include "zone_envelope.h"
int main(int argc, char** argv) {
  if (argc >= 2 && !std::strcmp(argv[1], "zones")) {
    const float speed = (float)atof(argv[2]), stop = (float)atof(argv[3]), slow = (float)atof(argv[4]);
    const bool manual = atoi(argv[5]) != 0;
    const float fs = zone_stop_full(stop, manual), fl = zone_slow_full(slow, manual);
    std::printf("%.4f %.4f %.4f\n", stop_zone_at(fs, speed), slow_zone_at(fs, fl, speed), GAMEPAD_LIN_MS);
    return 0;
  }
  // approach <speed> <cfg_stop> <cfg_slow> <manual> <accel> <sensor latency s> <speed lag s>
  const float v0 = (float)atof(argv[2]), cfg_stop = (float)atof(argv[3]), cfg_slow = (float)atof(argv[4]);
  const bool manual = atoi(argv[5]) != 0;
  const float accel = (float)atof(argv[6]), latency = (float)atof(argv[7]), lag = (float)atof(argv[8]);
  const float DT = 0.01f;
  float d = 2.5f, v = v0, ramp = v0, seen = d, last_frame = -1.0f;
  bool blocked = false;
  std::vector<float> hist(400, d);
  for (float t = 0.0f; t < 20.0f; t += DT) {
    hist.push_back(d);
    if (t - last_frame >= 0.077f) { last_frame = t; seen = hist[hist.size() - 1 - (size_t)(latency / DT)]; }
    const float fs = zone_stop_full(cfg_stop, manual), fl = zone_slow_full(cfg_slow, manual);
    const float stop_m = stop_zone_at(fs, v), slow_m = slow_zone_at(fs, fl, v);
    if (!blocked && seen < stop_m) blocked = true;
    else if (blocked && seen >= slow_m) blocked = false;
    float lin_t = blocked ? 0.0f : v0;
    if (!blocked && seen < slow_m) {
      float k = (seen - stop_m) / (slow_m - stop_m); k = k < 0.0f ? 0.0f : (k > 1.0f ? 1.0f : k);
      lin_t = fmaxf(fminf(APPROACH_CREEP_MIN_MS, v0), v0 * k);
    }
    if (blocked && ramp > 0.0f) ramp = reflex_ramp_step(ramp, fmaxf(ramp, v), seen, accel, DT);
    else ramp += shape_clamp(lin_t - ramp, -accel * DT, accel * DT);
    v += (ramp - v) * DT / lag;
    d -= v * DT;
    if (d <= 0.0f) { std::printf("hit %.3f\n", v); return 0; }
    if (ramp == 0.0f && v < 0.01f) { std::printf("stopped %.3f\n", d); return 0; }
  }
  std::printf("creeping %.3f\n", d);
}
"""


class ZoneEnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rex-zone-envelope-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/'harness.cpp'
        cls.binary = Path(cls.temp.name)/'harness'
        source.write_text(HARNESS)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT/'djr3x_motion'), str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def run_harness(self, *args):
        return subprocess.run([str(self.binary), *map(str, args)], text=True, capture_output=True, check=True).stdout.split()

    def zones(self, speed, stop, slow, manual):
        stop_m, slow_m, self.top = map(float, self.run_harness('zones', speed, stop, slow, int(manual)))
        return stop_m, slow_m

    def approach(self, speed, stop, slow, manual, accel, latency, lag):
        kind, value = self.run_harness('approach', speed, stop, slow, int(manual), accel, latency, lag)
        return kind, float(value)

    def test_the_whole_configured_envelope_applies_at_the_gamepad_top_speed(self):
        self.zones(0.0, 0.30, 0.75, True)
        stop_m, slow_m = self.zones(self.top, 0.30, 0.75, True)
        self.assertGreaterEqual(stop_m, 0.29)        # it was 0.21 m at 0.45 m/s
        self.assertGreaterEqual(slow_m, 0.73)        # it was 0.50 m

    def test_at_rest_the_base_can_still_park_close(self):
        self.assertEqual(self.zones(0.0, 0.30, 0.75, True), (0.10, 0.18))
        self.assertEqual(self.zones(0.0, 0.15, 0.60, False), (0.10, 0.18))

    def test_the_pad_keeps_its_own_envelope_when_the_host_tightened_rexs(self):
        self.zones(0.0, 0.15, 0.60, True)
        self.assertEqual(self.zones(self.top, 0.15, 0.60, True), self.zones(self.top, 0.30, 0.75, True))
        rex_stop, rex_slow = self.zones(0.25, 0.15, 0.60, False)     # Rex himself keeps the host's
        self.assertLess(rex_stop, 0.14)
        self.assertLess(rex_slow, 0.45)

    def test_a_lagging_heavy_base_at_full_pad_speed_stops_short_of_the_wall(self):
        self.zones(0.0, 0.30, 0.75, True)
        for cfg in ((0.30, 0.75, 0.40), (0.15, 0.60, 0.35)):         # pad alone; pad while Rex runs
            for latency, lag in ((0.15, 0.15), (0.15, 0.30), (0.30, 0.30)):
                with self.subTest(cfg=cfg, latency=latency, lag=lag):
                    kind, left = self.approach(self.top, cfg[0], cfg[1], True, cfg[2], latency, lag)
                    self.assertEqual(kind, 'stopped')
                    self.assertGreaterEqual(left, 0.05)

    def test_rex_at_his_own_speed_still_stops_short(self):
        for latency, lag in ((0.15, 0.15), (0.15, 0.30), (0.30, 0.30)):
            with self.subTest(latency=latency, lag=lag):
                kind, left = self.approach(0.25, 0.15, 0.60, False, 0.35, latency, lag)
                self.assertEqual(kind, 'stopped')
                self.assertGreaterEqual(left, 0.04)


if __name__ == '__main__':
    unittest.main()
