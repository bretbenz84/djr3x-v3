"""Compile the front-right crosstalk helpers and replay the 2026-10-10 phantom. No I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class FrontRightCrosstalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rex-tof-xtalk-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/'xtalk.cpp'
        cls.binary = Path(cls.temp.name)/'xtalk'
        source.write_text(r'''
#include <iostream>
#include <string>
#include "tof_fr_filter.h"
int main(int argc, char** argv) {
  (void)argc;
  if (std::string(argv[1]) == "regs") {
    static const float k[] = TOF_L1X_XTALK_KCPS_PER_SPAD;
    for (float v : k) std::cout << tof_xtalk_plane_offset_reg(v) << ' ' << tof_range_ignore_reg(v, TOF_L1X_XTALK_RIT_MULT) << '\n';
    return 0;
  }
  if (std::string(argv[1]) == "conv32") {
    std::cout << tof_xtalk_plane_offset_reg(32.f) << ' ' << tof_range_ignore_reg(32.f, TOF_L1X_XTALK_RIT_MULT) << '\n';
    return 0;
  }
  TofFrFilter fr; uint32_t t; int mm, status;
  // Pololu VL53L1X::RangeStatus: 0 RangeValid, 3 MinRangeClipped, 4 OutOfBoundsFail,
  // 7 WrapTargetFail, 9 XtalkSignalFail (device RANGEIGNORETHRESHOLD).
  while (std::cin >> t >> mm >> status)
    std::cout << tof_fr_filter_step(fr, tof_fr_input_mm(mm, status == 0 || status == 3, status == 4 || status == 9), t) << '\n';
}
''')
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'djr3x_motion'),
                        str(source), '-o', str(cls.binary)], check=True, capture_output=True, text=True)

    def replay(self, frames):
        text = ''.join(f'{i*80} {mm} {st}\n' for i, (mm, st) in enumerate(frames))
        out = subprocess.run([str(self.binary), 'replay'], input=text, capture_output=True, text=True, check=True).stdout
        return [int(v) for v in out.split()]

    def test_replacement_front_right_starts_uncompensated(self):
        # October 19: the faulty module's 32 kcps/SPAD was a property of its lens.
        out = subprocess.run([str(self.binary), 'regs'], capture_output=True, text=True, check=True).stdout.split('\n')
        self.assertEqual(out[:4], ['0 0']*4)

    def test_register_conversion_for_a_measured_channel(self):
        plane, rit = (int(v) for v in subprocess.run([str(self.binary), 'conv32'], capture_output=True,
                                                     text=True, check=True).stdout.split())
        self.assertEqual(plane, 32*128)                     # 32 kcps/SPAD in 9.7
        self.assertEqual(rit, round(32*1.25*8.192))         # 40 kcps/SPAD in 3.13 Mcps

    def test_range_ignored_phantom_reads_clear_not_near(self):
        # The 2026-10-10 phantom once the sensor drops it: a steady 38 mm the device
        # now reports as RANGEIGNORETHRESHOLD, with its wrap failures interleaved.
        frames = [(38, 9), (40, 7), (36, 9), (41, 9), (39, 7), (38, 9), (37, 9), (40, 9)]
        out = self.replay(frames)
        self.assertEqual(out[0], 4000)
        self.assertNotIn(38, out); self.assertNotIn(40, out)
        self.assertEqual(out[-1], 4000)

    def test_real_hand_still_ranges_after_the_phantom(self):
        frames = [(38, 9)]*4 + [(285, 0)]*6
        out = self.replay(frames)
        self.assertEqual(out[3], 4000)
        self.assertEqual(out[-1], 285)                      # confirmed near drop accepted

    def test_ignore_status_never_manufactures_clear_over_a_held_obstacle(self):
        # A real near surface, then one ignore frame: the drop was confirmed, a single
        # ignore frame releases at the bounded rate, never a jump to 4000.
        frames = [(285, 0)]*6 + [(40, 9)]
        out = self.replay(frames)
        self.assertLessEqual(out[-1], 285 + 300)


if __name__ == '__main__':
    unittest.main()
