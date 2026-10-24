"""Compile production geometry/aggregation; verify floor and real obstacle contracts."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MatrixFloorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary = Path(cls.temp.name) / 'matrix'
        source = Path(cls.temp.name) / 'matrix.cpp'
        # Production geometry/aggregation (tof_matrix_floor.h) for the module named in
        # argv[1], configured exactly as tof_matrix.cpp configures it from calib.h.
        source.write_text('''#include <iostream>
#include <string>
#include <stdint.h>
#include "tof_matrix_floor.h"
int main(int argc, char** argv) {
  const bool rear = argc > 1 && std::string(argv[1]) == "rear";
  MxGeom g = rear
    ? MxGeom{TOF_MATRIX_REAR_FLIP_V != 0, TOF_MATRIX_REAR_FLIP_H != 0, TOF_MATRIX_REAR_HEIGHT_M,
             TOF_MATRIX_REAR_FLOOR_ROW_MM, {0}, {0}}
    : MxGeom{TOF_MATRIX_FLIP_V != 0, TOF_MATRIX_FLIP_H != 0, TOF_MATRIX_HEIGHT_M,
             TOF_MATRIX_FLOOR_ROW_MM, {0}, {0}};
  mx_compute_geometry(g);
  for (float threshold : g.row_reject_mm) std::cout << threshold << ' ';
  std::cout << '\\n';
  uint16_t grid[64];
  unsigned v;
  while (std::cin >> v) {
    for (int i=0; i<64; ++i) {
      if (i && !(std::cin >> v)) return 2;
      grid[mx_raw_index(g, i/8, i%8)] = v;
    }
    int16_t l, r; mx_aggregate(g, grid, &l, &r);
    std::cout << l << ' ' << r << '\\n';
  }
}
''')
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                        '-I', str(ROOT/'djr3x_motion'), str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def replay(self, grids, module='front'):
        result = subprocess.run([str(self.binary), module], input=''.join(' '.join(map(str,g))+'\n' for g in grids),
                                capture_output=True, text=True, check=True).stdout.splitlines()
        return list(map(float,result[0].split())), [list(map(int,l.split())) for l in result[1:]]

    def test_confirmed_bottom_floor_does_not_block_both_halves(self):
        grids=[]
        for distance in (80, 110, 160, 250, 489):
            grid=[4000]*64
            grid[48:64]=[distance]*16
            grids.append(grid)
        thresholds, fronts=self.replay(grids)
        self.assertEqual(thresholds[6:], [70,70])
        self.assertEqual(fronts, [[3500,3500]]*len(grids))
        self.assertGreater(thresholds[4],1500)
        self.assertGreater(thresholds[5],500)

    def test_close_obstacle_above_floor_rows_still_blocks(self):
        grid=[4000]*64
        grid[32:40]=[110]*8
        _,fronts=self.replay([grid])
        self.assertTrue(all(25<=v<=110 for v in fronts[0]))

    def test_return_nearer_than_calibrated_floor_still_blocks(self):
        grid=[4000]*64
        grid[56:64]=[50]*8
        _,fronts=self.replay([grid])
        self.assertTrue(all(25<=v<70 for v in fronts[0]))

    def test_missing_returns_and_single_speckle_do_not_create_obstacle(self):
        grid=[4000]*64
        grid[0]=110
        grid[56:64]=[0]*8
        _,fronts=self.replay([grid])
        self.assertEqual(fronts,[[3500,3500]])

    def test_rear_module_uses_the_same_floor_and_obstacle_rules(self):
        # October 19: identical module, same height, facing back. Until its own
        # floor capture it carries the front calibration.
        floor=[4000]*64
        floor[48:64]=[160]*16
        leg=[4000]*64
        for row in (3, 4, 5):
            leg[row*8+1]=leg[row*8+2]=300      # chair leg in the sensor's left half
        thresholds, halves=self.replay([floor, leg], 'rear')
        self.assertEqual(thresholds, self.replay([], 'front')[0])
        self.assertEqual(halves[0], [3500,3500])
        self.assertTrue(250<=halves[1][0]<=300)
        self.assertEqual(halves[1][1], 3500)
