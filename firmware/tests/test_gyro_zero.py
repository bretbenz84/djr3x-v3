"""The gyroscope's zero is measured whenever the base stands still (firmware 0.2.24, November 22, 2026).

Compiles the firmware's own gyro_zero.h and feeds it readings the way imu.cpp does, fifty a second.
Made-up readings; they prove the rule, not how this chip behaves. No I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SOURCE = r'''
#include <iostream>
#include "gyro_zero.h"

// stdin: one reading per line: z-rate  x-rate  wheels_still(0/1)  restart(0/1). argv[1..3]: the zero to start from.
// stdout per reading: updated(0/1) zero_z measured(0/1)
int main(int, char** argv) {
  GyroZero watch;
  float zero[3] = { std::stof(argv[2]), 0.f, std::stof(argv[1]) };
  float z, x; int still, restart;
  while (std::cin >> z >> x >> still >> restart) {
    if (restart) gyro_zero_restart(watch);
    const float g[3] = { x, 0.f, z };
    const bool updated = gyro_zero_note(watch, g, still != 0, zero);
    std::cout << updated << ' ' << zero[2] << ' ' << watch.measured << '\n';
  }
}
'''


class GyroZeroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rex-gyro-zero-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / 'zero.cpp'
        cls.binary = Path(cls.temp.name) / 'zero'
        source.write_text(SOURCE)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'djr3x_motion'),
                        str(source), '-o', str(cls.binary)], check=True, capture_output=True, text=True)

    def run_readings(self, rows, zero=0.):
        """rows: (z, x, still, restart). Returns [(updated, zero_z, measured)]."""
        done = subprocess.run([str(self.binary), str(zero), '0'],
                              input=''.join(f'{z} {x} {int(still)} {int(restart)}\n' for z, x, still, restart in rows),
                              text=True, capture_output=True, check=True)
        return [(bool(int(u)), float(z), bool(int(m))) for u, z, m in (line.split() for line in done.stdout.splitlines())]

    @staticmethod
    def still(seconds, z=-3.2, noise=.1, x=0.):
        """A chip at rest: its zero plus a little sensor noise, fifty readings a second."""
        return [(z + noise * ((i * 37 % 11) / 10. - .5), x, True, False) for i in range(round(seconds * 50))]

    def test_one_still_second_sets_the_zero_and_the_heading_stops_drifting(self):
        out = self.run_readings(self.still(1))
        self.assertEqual([u for u, *_ in out].count(True), 1)
        self.assertTrue(out[-1][0])                                  # On the fiftieth reading.
        self.assertAlmostEqual(out[-1][1], -3.2, delta=.02)
        self.assertTrue(out[-1][2])

    def test_readings_still_settling_after_switch_on_are_not_averaged_in(self):
        # Until 0.2.23: the first readings after switch-on swung 8 deg/s and the zero landed 0.2 off.
        settling = [(-3.2 + 8. * (.5 ** i) * (-1) ** i, 0., True, False) for i in range(8)]
        out = self.run_readings(settling + self.still(1.2))
        first = next(i for i, (u, *_) in enumerate(out) if u)
        self.assertGreaterEqual(first, len(settling) + 40)           # A whole second after the swinging stopped.
        self.assertAlmostEqual(out[first][1], -3.2, delta=.03)

    def test_wheels_driven_or_turning_measure_nothing(self):
        moving = [(z, x, False, False) for z, x, *_ in self.still(3)]
        self.assertFalse(any(u for u, *_ in self.run_readings(moving, zero=-3.1)))
        # A still second interrupted by the wheels starts over.
        rows = self.still(.8) + [(40., 0., False, False)] * 10 + self.still(.8)
        self.assertFalse(any(u for u, *_ in self.run_readings(rows, zero=-3.1)))

    def test_being_rocked_by_his_own_servos_measures_nothing(self):
        rocked = [(-3.2 + (1.5 if i % 9 == 0 else 0.), 0., True, False) for i in range(200)]
        self.assertFalse(any(u for u, *_ in self.run_readings(rocked, zero=-3.1)))
        tilting = self.still(3, x=0.)                                # The yaw axis is quiet but he is being tipped.
        tilting = [(z, 2. * (i % 2), s, r) for i, (z, _, s, r) in enumerate(tilting)]
        self.assertFalse(any(u for u, *_ in self.run_readings(tilting, zero=-3.1)))

    def test_later_seconds_follow_the_chip_as_it_warms(self):
        out = self.run_readings(self.still(1, z=-3.2) + self.still(4, z=-3.0))
        zeros = [z for u, z, _ in out if u]
        self.assertAlmostEqual(zeros[0], -3.2, delta=.02)
        self.assertAlmostEqual(zeros[1], -3.1, delta=.03)            # Half way each still second...
        self.assertAlmostEqual(zeros[-1], -3.0, delta=.03)           # ...and there within a few.

    def test_a_steady_turn_by_hand_is_not_taken_for_a_zero(self):
        out = self.run_readings(self.still(1, z=-3.2) + self.still(3, z=3.0))   # Turned smoothly at 6 deg/s for 3 s.
        self.assertAlmostEqual(out[-1][1], -3.2, delta=.02)

    def test_a_zero_that_was_wrong_is_replaced_after_five_steady_seconds(self):
        out = self.run_readings(self.still(1, z=4.0) + self.still(6, z=-3.2))    # A first second taken while turned.
        zeros = [z for u, z, _ in out if u]
        self.assertAlmostEqual(zeros[0], 4.0, delta=.02)
        self.assertEqual(len(zeros), 3)                              # The wrong one, the replacement, one refinement.
        self.assertAlmostEqual(zeros[1], -3.2, delta=.02)

    def test_after_the_chip_restarts_the_first_still_second_replaces_the_zero_outright(self):
        # November 22: a zero taken from one reading mid-shake, 19.7 deg/s off.
        rows = self.still(1, z=-3.2)
        rows[0] = (*rows[0][:3], True)                               # The chip was just switched on again.
        out = self.run_readings(rows, zero=16.5)
        self.assertAlmostEqual(out[-1][1], -3.2, delta=.02)

    def test_a_reading_no_chip_gives_at_rest_is_never_a_zero(self):
        self.assertFalse(any(u for u, *_ in self.run_readings(self.still(8, z=25.))))


if __name__ == '__main__':
    unittest.main()
