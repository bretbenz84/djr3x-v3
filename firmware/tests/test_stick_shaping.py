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
#include "stick_shaping.h"
int main(int argc, char** argv) {
  if (argc < 3) return 2;
  const StickDrive d = stick_to_drive((float)atof(argv[1]), (float)atof(argv[2]),
                                      GAMEPAD_HARDWOOD_LIN_MS, GAMEPAD_HARDWOOD_ANG_RADS, TRACK_WIDTH_M);
  std::printf("%.5f %.5f %.5f %.5f %.5f %.5f %.5f %.5f\n", d.fwd, d.turn, d.lin, d.ang, d.blend,
              GAMEPAD_HARDWOOD_LIN_MS, GAMEPAD_HARDWOOD_ANG_RADS, TRACK_WIDTH_M);
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

    def test_reverse_mirrors_forward(self):
        d = self.drive(0.5, -0.866)
        self.assertAlmostEqual(d['lin'], -self.top, places=4)
        self.assertLess(d['ang'], 0.0)               # stick right is clockwise, as when forward


if __name__ == '__main__':
    unittest.main()
