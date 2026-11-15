"""A depth matrix that stops answering is set up again by the controller (owner, November 15).

A simulated SEN0628 module is run against the firmware's own link logic (tof_matrix_link.h) at the
poll task's 75 ms step. The module can be held in reset (its button pressed in: it answers
nothing), come up fresh (it answers, but only streams 8x8 frames after a mode request and its
5 s reconfigure), or answer its address while dropping the mode request (still starting up).
No Wire, no motors, no robot."""
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''#include <cstdio>
#include <cstdint>
#include <iostream>
#include <string>
#include "tof_matrix_link.h"

struct Module {
  std::string mode = "up";      // up | reset | ignore
  bool configured = false;
  bool pending = false;
  uint32_t set_at = 0, now = 0, t0 = 0;
  int resets = 0;
  void enter(const std::string& m) { if (m == "reset") { configured = false; pending = false; } mode = m; }
  bool probe() { return mode != "reset"; }
  void send_setmode() { if (mode == "up") { pending = true; configured = false; set_at = now; } }
  bool mode_acked() {
    if (mode != "up" || !pending || (uint32_t)(now - set_at) < 5000) return false;
    pending = false; configured = true; return true;
  }
  bool read_frame() { return mode == "up" && configured; }
  void bus_reset() { ++resets; std::printf("%u bus_reset\n", (uint32_t)(now - t0)); }
};

static const char* NAMES[] = {"none", "no_ack", "ready", "back", "mode_unacked", "frame",
                              "frame_held", "lost", "still_lost", "reinit"};

int main(int argc, char** argv) {
  uint32_t t = argc > 1 ? (uint32_t)std::stoul(argv[1]) : 0;
  const uint32_t t0 = t;
  MxLink link; Module module; module.t0 = t0;
  unsigned long duration; std::string mode; long frames = 0;
  while (std::cin >> duration >> mode) {
    module.enter(mode);
    for (unsigned long spent = 0; spent < duration; spent += 75, t += 75) {
      module.now = t;
      const MxEvent ev = mx_link_step(link, module, t);
      if (ev == MX_EV_FRAME) ++frames;
      else if (ev != MX_EV_NONE && ev != MX_EV_STILL_LOST && ev != MX_EV_FRAME_HELD)
        std::printf("%u %s\n", (uint32_t)(t - t0), NAMES[ev]);
    }
    std::printf("%u end %s frames=%ld state=%d\n", (uint32_t)(t - t0), mode.c_str(), frames, (int)link.state);
  }
}
'''


class MatrixLinkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        source = Path(cls.tmp.name)/'link.cpp'
        cls.binary = Path(cls.tmp.name)/'link'
        source.write_text(HARNESS)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT/'djr3x_motion'),
                        str(source), '-o', str(cls.binary)], check=True)

    def run_module(self, script, start=0):
        """script: (milliseconds, module mode) spans. Rows of (ms since start, event words...)."""
        text = ''.join(f'{ms} {mode}\n' for ms, mode in script)
        out = subprocess.run([str(self.binary), str(start)], input=text, text=True, capture_output=True, check=True)
        return [(int(line.split()[0]), *line.split()[1:]) for line in out.stdout.splitlines()]

    @staticmethod
    def times(rows, event):
        return [t for t, name, *_ in rows if name == event]

    @staticmethod
    def frames(rows, span):
        """Frames counted by the end of the span-th scripted span."""
        ends = [row for row in rows if row[1] == 'end']
        return int(ends[span][3].split('=')[1])

    def test_boot_is_unchanged(self):
        rows = self.run_module([(20000, 'up')])
        self.assertEqual([name for _, name, *_ in rows], ['ready', 'end'])
        ready = self.times(rows, 'ready')[0]
        self.assertTrue(5200 <= ready <= 5400, ready)
        self.assertGreater(self.frames(rows, 0), 180)   # ~13 frames a second afterwards

    def test_button_held_in_then_let_go_resumes(self):
        rows = self.run_module([(10000, 'up'), (30000, 'reset'), (20000, 'up')])
        lost, reinit, back = (self.times(rows, name) for name in ('lost', 'reinit', 'back'))
        self.assertEqual(len(lost), 1)
        self.assertTrue(10000 < lost[0] <= 10700, lost)           # -1 within 8 failed frames, as before
        self.assertEqual(len(reinit), 1)
        self.assertTrue(12000 <= reinit[0] <= 12200, reinit)      # setup starts over after 2 s of failures
        probes = self.times(rows, 'no_ack')
        self.assertTrue(26 <= len(probes) <= 29, len(probes))      # once a second while it is held
        self.assertEqual(len(back), 1)
        self.assertTrue(40000 <= back[0] <= 46500, back)           # streaming within ~6.5 s of letting go
        self.assertEqual(self.frames(rows, 1), self.frames(rows, 0))   # nothing while held
        self.assertGreater(self.frames(rows, 2) - self.frames(rows, 1), 150)
        self.assertEqual(rows[-1][4], 'state=3')                   # MX_READY

    def test_bus_is_restarted_only_while_recovering(self):
        rows = self.run_module([(10000, 'up'), (30000, 'reset'), (20000, 'up')])
        resets = self.times(rows, 'bus_reset')
        reinit = self.times(rows, 'reinit')[0]
        self.assertEqual(resets[0], reinit)                        # once when setup starts over
        self.assertEqual(len(resets), 3)                           # then every tenth unanswered probe
        self.assertTrue(all(reinit <= t < 40000 for t in resets), resets)

    def test_module_that_reset_by_itself_is_set_up_again(self):
        # A power dip: 300 ms dark, then answering in its power-on mode (no 8x8 frames).
        rows = self.run_module([(10000, 'up'), (300, 'reset'), (15000, 'up')])
        self.assertEqual(len(self.times(rows, 'reinit')), 1)
        back = self.times(rows, 'back')
        self.assertEqual(len(back), 1)
        self.assertTrue(back[0] <= 10000 + 2200 + 5500, back)      # 2 s to decide, then one setup
        self.assertGreater(self.frames(rows, 2) - self.frames(rows, 1), 80)

    def test_brief_glitch_does_not_disturb_setup(self):
        rows = self.run_module([(10000, 'up'), (1000, 'reset'), (10000, 'up')])
        # A module only held in reset for a second lost its mode, so it is set up again...
        self.assertEqual(len(self.times(rows, 'reinit')), 1)
        # ...but half a second of failed reads on a module that keeps its mode is left alone.
        rows = self.run_module([(10000, 'up'), (450, 'ignore'), (5000, 'up')])
        self.assertEqual(self.times(rows, 'reinit'), [])
        self.assertEqual(self.times(rows, 'lost'), [])             # under 8 failed frames: values held
        self.assertGreater(self.frames(rows, 2) - self.frames(rows, 1), 60)

    def test_mode_request_dropped_while_starting_is_sent_again(self):
        rows = self.run_module([(10000, 'up'), (10000, 'reset'), (3000, 'ignore'), (30000, 'up')])
        self.assertEqual(len(self.times(rows, 'mode_unacked')), 1)
        back = self.times(rows, 'back')
        self.assertEqual(len(back), 1)
        self.assertTrue(back[0] - 20000 <= 21000, back)            # worst case about 20 s after letting go

    def test_module_never_fitted_is_only_probed(self):
        rows = self.run_module([(60000, 'reset')])
        self.assertEqual({name for _, name, *_ in rows}, {'no_ack', 'end'})
        self.assertTrue(58 <= len(self.times(rows, 'no_ack')) <= 61)

    def test_lost_again_after_coming_back(self):
        rows = self.run_module([(10000, 'up'), (5000, 'reset'), (15000, 'up'), (5000, 'reset'), (15000, 'up')])
        self.assertEqual(len(self.times(rows, 'reinit')), 2)
        self.assertEqual(len(self.times(rows, 'back')), 2)
        self.assertEqual(len(self.times(rows, 'ready')), 1)        # "ready" is the first setup only

    def test_millisecond_clock_wrap(self):
        script = [(10000, 'up'), (30000, 'reset'), (20000, 'up')]
        self.assertEqual(self.run_module(script, start=0xFFFFFFFF - 25000), self.run_module(script))
