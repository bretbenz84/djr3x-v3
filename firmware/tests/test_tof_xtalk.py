"""Compile the crosstalk register helpers (tof_xtalk.h). No I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CrosstalkRegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rex-tof-xtalk-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name)/'xtalk.cpp'
        cls.binary = Path(cls.temp.name)/'xtalk'
        source.write_text(r'''
#include <iostream>
#include <string>
#include "tof_xtalk.h"
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
  return 1;
}
''')
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'djr3x_motion'),
                        str(source), '-o', str(cls.binary)], check=True, capture_output=True, text=True)

    def test_replacement_front_right_starts_uncompensated(self):
        # October 19: the faulty module's 32 kcps/SPAD was a property of its lens.
        out = subprocess.run([str(self.binary), 'regs'], capture_output=True, text=True, check=True).stdout.split('\n')
        self.assertEqual(out[:4], ['0 0']*4)

    def test_register_conversion_for_a_measured_channel(self):
        plane, rit = (int(v) for v in subprocess.run([str(self.binary), 'conv32'], capture_output=True,
                                                     text=True, check=True).stdout.split())
        self.assertEqual(plane, 32*128)                     # 32 kcps/SPAD in 9.7
        self.assertEqual(rit, round(32*1.25*8.192))         # 40 kcps/SPAD in 3.13 Mcps


if __name__ == '__main__':
    unittest.main()
