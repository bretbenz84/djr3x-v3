"""Compile the gamepad stick mapping natively and check what a lean at speed commands.
No I/O. Wheel speeds use the firmware's own mixing rule (lin -/+ ang * track / 2)."""
import math
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r"""
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "drive_shaping.h"
#include "stick_shaping.h"
static float toward(float v, float target, float step) {
  return v + stick_clamp(target - v, -step, step);
}
int main(int argc, char** argv) {
  if (argc >= 2 && !std::strcmp(argv[1], "jerk")) {
    // Full forward until at speed, then the stick thrown to (x, y). The ramps are
    // control_tick's (boot accelerations); the blend and mixing are the firmware's own.
    const float x = (float)atof(argv[2]), y = (float)atof(argv[3]), DT = 0.01f;
    float lin = 0.0f, ang = 0.0f, pl = 0.0f, pr = 0.0f;
    for (int i = 0; i < 500; ++i) {
      const StickDrive d = i < 200 ? stick_to_drive(0.0f, 1.0f, GAMEPAD_LIN_MS, GAMEPAD_ANG_RADS, TRACK_WIDTH_M)
                                   : stick_to_drive(x, y, GAMEPAD_LIN_MS, GAMEPAD_ANG_RADS, TRACK_WIDTH_M);
      lin = toward(lin, d.lin, DRIVE_ACCEL_LIN * DT);
      ang = toward(ang, d.ang, DRIVE_ACCEL_ANG * DT);
      const float blend = fmaxf(d.blend, drive_speed_blend(fabsf(lin)));
      float l, r;
      mix_wheels(lin, ang, TRACK_WIDTH_M, true, blend, l, r);
      l = pl = wheel_slew_step(pl, l, GAMEPAD_WHEEL_SLEW_MS2 * DT);
      r = pr = wheel_slew_step(pr, r, GAMEPAD_WHEEL_SLEW_MS2 * DT);
      if (i >= 199) std::printf("%.5f %.5f %.5f %.5f\n", lin, ang, l, r);
    }
    std::printf("%.5f %.5f\n", GAMEPAD_SPIN_SPEED_LO, GAMEPAD_SPIN_SPEED_HI);
    return 0;
  }
  if (argc < 3) return 2;
  const StickDrive d = stick_to_drive((float)atof(argv[1]), (float)atof(argv[2]),
                                      GAMEPAD_LIN_MS, GAMEPAD_ANG_RADS, TRACK_WIDTH_M);
  std::printf("%.5f %.5f %.5f %.5f %.5f %.5f %.5f %.5f\n", d.fwd, d.turn, d.lin, d.ang, d.blend,
              GAMEPAD_LIN_MS, GAMEPAD_ANG_RADS, TRACK_WIDTH_M);
}
"""


class StickShapingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rex-stick-shaping-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/'harness.cpp'
        cls.binary = Path(cls.temp.name)/'harness'
        source.write_text(HARNESS)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT/'djr3x_motion'), str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def drive(self, x, y):
        out = subprocess.run([str(self.binary), str(x), str(y)], text=True, capture_output=True, check=True).stdout
        fwd, turn, lin, ang, blend, self.top, self.spin, self.track = map(float, out.split())
        half = ang * self.track / 2
        return {'fwd': fwd, 'turn': turn, 'lin': lin, 'ang': ang, 'blend': blend,
                'left': lin - half, 'right': lin + half}

    def at(self, degrees_right_of_forward, push=1.0):
        a = math.radians(degrees_right_of_forward)
        return self.drive(push*math.sin(a), push*math.cos(a))

    def test_full_push_leaning_keeps_full_speed_and_banks_progressively(self):
        straight = self.at(0)
        self.assertAlmostEqual(straight['lin'], self.top, places=4)
        self.assertEqual(straight['ang'], 0.0)
        gaps = []
        for lean in (5, 10, 20, 30, 45):
            with self.subTest(lean=lean):
                d = self.at(lean)                    # leaning right: the right wheel is inside
                self.assertAlmostEqual(d['lin'], self.top, places=4)   # the old mapping lost half by 45
                self.assertGreater(d['left'], d['right'])
                self.assertGreater(d['right'], 0.15 * self.top)        # the inside wheel keeps rolling
                gaps.append(d['left'] - d['right'])
        self.assertGreater(gaps[0], 0.0)             # a 5 degree lean already steers
        self.assertLess(gaps[0], 0.1 * self.top)     # and only slightly
        self.assertEqual(gaps, sorted(gaps))

    def test_no_step_anywhere_along_a_sweep_from_forward_to_sideways(self):
        sweep = [self.at(lean) for lean in range(0, 91)]
        for a, b in zip(sweep, sweep[1:]):
            self.assertLess(abs(b['left'] - a['left']), 0.03)
            self.assertLess(abs(b['right'] - a['right']), 0.03)

    def test_sideways_is_a_spin_and_a_thumb_slightly_off_axis_stays_one(self):
        for y in (0.0, 0.08, -0.08):
            with self.subTest(y=y):
                d = self.drive(-1.0, y)              # stick left
                self.assertEqual((d['lin'], d['blend']), (0.0, 0.0))
                self.assertAlmostEqual(d['ang'], self.spin, places=3)

    def test_centre_is_still_and_a_straight_push_does_not_wander(self):
        self.assertEqual((self.drive(0.08, -0.08)['lin'], self.drive(0.08, -0.08)['ang']), (0.0, 0.0))
        self.assertEqual(self.drive(0.04, 1.0)['ang'], 0.0)
        self.assertEqual(self.drive(-0.04, -1.0)['ang'], 0.0)

    def jerk(self, x, y):
        out = subprocess.run([str(self.binary), 'jerk', str(x), str(y)], text=True, capture_output=True, check=True).stdout
        rows = [tuple(map(float, line.split())) for line in out.splitlines()]
        lo, hi = rows.pop()
        return rows, lo, hi

    def test_stick_thrown_sideways_at_full_speed_slows_a_wheel_before_any_reversal(self):
        for x in (-1.0, 1.0):
            with self.subTest(x=x):
                rows, lo, hi = self.jerk(x, 0.0)
                inside = [(lin, l if x < 0 else r) for lin, _, l, r in rows]
                outside = [r if x < 0 else l for _, _, l, r in rows]
                self.assertAlmostEqual(inside[0][1], self.drive(0.0, 1.0)['lin'], places=3)   # at speed, straight
                self.assertTrue(all(v >= 0.0 for lin, v in inside if lin >= hi))   # no reversal while moving
                self.assertTrue(any(v == 0.0 for lin, v in inside))           # it does come to a stop first
                self.assertTrue(all(b <= a + 1e-6 for (_, a), (_, b) in zip(inside, inside[1:])))   # only slows
                # No wheel's target changes faster than about 1 m/s^2: a ramp, not a throw.
                for series in ([v for _, v in inside], outside):
                    self.assertLessEqual(max(abs(b - a) for a, b in zip(series, series[1:])), 0.0101)
                self.assertAlmostEqual(inside[-1][1], -outside[-1], places=3)   # ends as a spin in place
                self.assertLess(inside[-1][1], -0.2)

    def test_stick_thrown_to_a_hard_forward_turn_never_reverses_a_wheel(self):
        rows, _, hi = self.jerk(-0.94, 0.34)         # 70 degrees left of forward, full push
        self.assertTrue(all(l >= 0.0 and r >= 0.0 for lin, _, l, r in rows if lin >= hi))
        # Once it has slowed to the crawl that stick asks for, the inside wheel may creep back.
        self.assertTrue(all(l >= -0.05 and r >= 0.0 for _, _, l, r in rows))
        self.assertGreater(min(lin for lin, _, _, _ in rows), 0.0)

    def test_standing_still_the_speed_alone_allows_a_spin(self):
        rows, lo, hi = self.jerk(0.0, 1.0)           # the constants themselves
        self.assertGreater(hi, lo)
        self.assertLess(hi, 0.5 * self.drive(0.0, 1.0)['lin'])   # full speed is well inside "moving"

    def test_reverse_mirrors_forward(self):
        d = self.drive(0.5, -0.866)
        self.assertAlmostEqual(d['lin'], -self.top, places=4)
        self.assertLess(d['ang'], 0.0)               # stick right is clockwise, as when forward


if __name__ == '__main__':
    unittest.main()
