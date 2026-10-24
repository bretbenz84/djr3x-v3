"""Compile the drive firmware's wheel shaping header natively and drive it through
turn starts, stops and reflex blocks against a toy wheel. No I/O.

The toy is synthetic (stiction, a kinetic duty floor, one time constant); it proves the
shaping's contracts, not the real base's feel.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <cstdio>
#include <cstring>
#include "drive_shaping.h"

static const float DT = 0.01f;
// Hardwood pivot gains as the firmware composes them (base gains x 5.2 wheel scale).
static WheelGains pivot(float floor_duty, float ceiling) {
  return WheelGains{440.0f*5.2f, 220.0f*5.2f, 0.0f, 640.0f*5.2f, floor_duty, ceiling, 1.0f};
}

// Toy wheel: planted until |duty| reaches `breakaway`, then speed relaxes toward what the
// duty above `kinetic` sustains; re-sticks when nearly still and under breakaway.
struct Toy {
  float v = 0.0f; bool loose = false;
  float breakaway, kinetic, tau = 0.08f;
  void step(int duty) {
    const float d = (float)duty;
    if (!loose && d >= breakaway) loose = true;
    if (loose) {
      float eq = (d - kinetic) / (640.0f*5.2f);
      if (eq < 0.0f) eq = 0.0f;
      v += (eq - v) * DT / tau;
      if (v < 0.003f && d < breakaway) { v = 0.0f; loose = false; }
    }
  }
};

static void row(int i, float target, float meas, int duty, const WheelLoop& w) {
  std::printf("%d %.5f %.5f %d %.3f\n", i, target, meas, duty, w.boost);
}

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const char* s = argv[1];
  WheelLoop w;
  if (!std::strcmp(s, "start_pivot") || !std::strcmp(s, "stuck_carpet")) {
    const bool stuck = !std::strcmp(s, "stuck_carpet");
    const WheelGains g = stuck ? pivot(650.0f, 1023.0f) : pivot(380.0f, 750.0f);
    Toy toy; toy.breakaway = stuck ? 1e9f : 620.0f; toy.kinetic = 380.0f;
    float target = 0.0f;
    for (int i = 0; i < 100; ++i) {          // 2 rad/s^2 ramp to 45 deg/s at 0.1485 m arm
      target = target + 0.297f * DT; if (target > 0.117f) target = 0.117f;
      const int duty = wheel_effort(w, target, toy.v, DT, g, false);
      row(i, target, toy.v, duty, w);
      toy.step(duty);
    }
  } else if (!std::strcmp(s, "stop_tracking") || !std::strcmp(s, "stop_friction")) {
    const bool friction = !std::strcmp(s, "stop_friction");
    const WheelGains g = pivot(380.0f, 750.0f);
    float target = 0.052f, meas = 0.052f;    // the turn's 20 deg/s finish
    for (int i = 0; i < 60; ++i) wheel_effort(w, target, meas + 0.004f, DT, g, false);
    for (int i = 0; i < 40; ++i) {
      const float prev = target;
      target -= 0.297f * DT; if (target < 0.0f) target = 0.0f;
      meas = friction ? (i < 6 ? prev : 0.0f) : prev;
      const int duty = wheel_effort(w, target, meas, DT, g, true);
      row(i, target, meas, duty, w);
    }
  } else if (!std::strcmp(s, "crawl")) {
    const WheelGains g{440.0f*5.2f, 220.0f*5.2f, 0.0f, 640.0f*5.2f, 230.0f, 500.0f, 1.0f};
    for (int i = 0; i < 100; ++i) {          // encoder quantization: 1 or 2 counts a tick
      const float meas = (i % 2) ? 0.026f : 0.013f;
      const int duty = wheel_effort(w, 0.02f, meas, DT, g, false);
      row(i, 0.02f, meas, duty, w);
    }
  } else if (!std::strcmp(s, "reflex")) {    // speed room -> travelled distance
    const float cases[][2] = {{0.25f, 0.116f}, {0.28f, 0.12f}, {0.80f, 0.30f}, {0.25f, 0.06f}};
    for (const auto& c : cases) {
      float v = c[0], room = c[1], travelled = 0.0f; int ticks = 0;
      while (v > 0.0f && ticks < 1000) {
        v = reflex_ramp_step(v, v, room - travelled, 0.35f, DT);
        travelled += v * DT; ++ticks;
      }
      std::printf("%.2f %.3f %.4f %d\n", c[0], c[1], travelled, ticks);
    }
    std::printf("cross %.4f %.4f\n", reflex_ramp_step(0.001f, 0.25f, 0.05f, 0.35f, DT),
                reflex_ramp_step(-0.001f, 0.25f, 0.05f, 0.35f, DT));
  } else if (!std::strcmp(s, "phantom")) {   // one false matrix frame at the stop line
    float v = 0.25f, lowest = v;
    for (int i = 0; i < 60; ++i) {
      const bool blocked = (i >= 10 && i < 18);   // ~77 ms frame
      if (blocked) v = reflex_ramp_step(v, v, 0.11f, 0.35f, DT);
      else v += (0.25f - v > 0.35f*DT) ? 0.35f*DT : (0.25f - v);
      if (v < lowest) lowest = v;
    }
    std::printf("%.4f %.4f\n", lowest, v);
  }
  return 0;
}
'''


class DriveShapingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rex-drive-shaping-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/'harness.cpp'
        cls.binary = Path(cls.temp.name)/'harness'
        source.write_text(HARNESS)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT/'djr3x_motion'), str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def rows(self, scenario):
        out = subprocess.run([str(self.binary), scenario], text=True, capture_output=True, check=True).stdout
        return [line.split() for line in out.splitlines()]

    def ticks(self, scenario):
        return [(int(i), float(t), float(m), int(d), float(b)) for i, t, m, d, b in self.rows(scenario)]

    def test_turn_start_climbs_instead_of_stepping_to_the_kick(self):
        rows = self.ticks('start_pivot')
        duties = [d for _, _, _, d, _ in rows]
        first = next(i for i, d in enumerate(duties) if d)
        self.assertLess(duties[first], 500)          # the old first tick was ~817 (80%)
        self.assertLessEqual(max(b - a for a, b in zip(duties[first:], duties[first+1:])), 60)
        loose = next(i for i, (_, _, m, _, _) in enumerate(rows) if m > 0)
        self.assertLessEqual(duties[loose-1], 620 + 60)   # broke loose at about what it needed
        self.assertTrue(all(b == 0 for _, _, _, _, b in rows[loose+30:]))   # excess shed

    def test_a_wheel_that_will_not_turn_still_gets_full_carpet_authority(self):
        rows = self.ticks('stuck_carpet')
        reached = next(i for i, (_, _, _, d, _) in enumerate(rows) if d >= 1000)
        self.assertLessEqual(reached, 35)            # within 0.35 s
        self.assertAlmostEqual(rows[-1][4], 1023 - 650, places=1)

    def test_a_turn_stop_tapers_to_nothing_before_the_cut(self):
        rows = self.ticks('stop_tracking')
        duties = [d for _, t, _, d, _ in rows if t >= 0.01]
        self.assertGreater(duties[0], 380)           # it was carrying the scrub load
        self.assertLessEqual(duties[-1], 80)         # the old last tick held the 380 floor
        self.assertTrue(all(b <= a + 5 for a, b in zip(duties, duties[1:])))
        self.assertTrue(all(d >= 0 for _, _, _, d, _ in rows))   # never drives backward

    def test_a_turn_stopped_early_by_friction_is_not_kicked_again(self):
        rows = self.ticks('stop_friction')
        duties = [d for _, t, _, d, _ in rows if t >= 0.01]
        self.assertTrue(all(d <= duties[0] for d in duties))
        self.assertTrue(all(b == 0 for _, _, _, _, b in rows))
        self.assertLessEqual(duties[-1], 80)

    def test_a_slow_inside_wheel_is_not_mistaken_for_stalled(self):
        self.assertTrue(all(b == 0 for _, _, _, _, b in self.ticks('crawl')))

    def test_reflex_block_stops_short_of_the_obstacle_without_slamming(self):
        rows = self.rows('reflex')
        cross = rows.pop()
        for speed, room, travelled, ticks in ((float(a), float(b), float(c), int(d)) for a, b, c, d in rows):
            with self.subTest(speed=speed, room=room):
                self.assertLess(travelled, max(room - 0.02, 0.0) + 0.005)
                if room > 0.1:                       # room to spare: not a one-tick slam
                    self.assertGreaterEqual(ticks, 10)
        self.assertEqual([abs(float(x)) for x in cross[1:]], [0.0, 0.0])   # never past zero

    def test_one_false_frame_costs_a_dip_not_a_stop(self):
        lowest, final = map(float, self.rows('phantom')[0])
        self.assertGreater(lowest, 0.18)
        self.assertAlmostEqual(final, 0.25, places=3)


if __name__ == '__main__':
    unittest.main()
