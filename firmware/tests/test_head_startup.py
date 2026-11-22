"""Run the LED board's head code on the Mac against the stand-in LED library and read back the mouth
during startup: flickering on in the ordinary amber until fully lit, paced to the host's own start
(owner, November 21, 2026). No board, no serial.

The frames prove what the code sends to the strip, with a fake clock. They do not prove how it looks.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from test_head_charge import HARNESS, ROOT

MOUTH = range(2, 82)
FULL = (35, 64, 0)        # Fully lit: the amber at a quarter brightness, as stored (green and red swapped).


class MouthStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        source = Path(cls.tmp.name) / 'harness.cpp'
        cls.binary = Path(cls.tmp.name) / 'head'
        source.write_text(HARNESS)
        subprocess.run(['clang++', '-std=c++17', '-O1', '-w', '-I', str(ROOT / 'tests' / 'fake_fastled'),
                        '-I', str(ROOT / 'djr3x_leds'), str(source), str(ROOT / 'djr3x_leds' / 'head.cpp'),
                        '-o', str(cls.binary)], check=True)

    def play(self, script):
        """Frames as (ms since the first command, 82 stored (r, g, b) pixels)."""
        out = subprocess.run([str(self.binary)], input=script, text=True,
                             capture_output=True, check=True).stdout.splitlines()
        mark = next(i for i, line in enumerate(out) if line.startswith('start'))
        start = int(out[mark].split()[1])
        frames = []
        for line in out[mark + 1:]:
            fields = line.split()
            frames.append((int(fields[0]) - start, [tuple(bytes.fromhex(p)) for p in fields[1:]]))
        return frames

    @staticmethod
    def lit(frame):
        return [i for i in MOUTH if sum(frame[i])]

    def mean_lit(self, frames, start, end):
        counts = [len(self.lit(f)) for t, f in frames if start <= t < end]
        return sum(counts) / len(counts)

    def test_the_mouth_flickers_on_in_amber_until_fully_lit(self):
        frames = self.play('cmd STARTUP:12800 run 12800 cmd READY run 900')
        before = [(t, f) for t, f in frames if t < 12800]
        # Only the ordinary amber, ever: red over green, no blue (stored green first).
        for t, f in frames:
            for i in self.lit(f):
                r, g, b = f[i]
                self.assertTrue(g > r > 0 and b == 0, f'not amber at {t} ms: {f[i]}')
        # A few lights at first, more and more through the start.
        spans = [self.mean_lit(frames, a, a + 2000) for a in (0, 3500, 7000, 10500)]
        self.assertLess(spans[0], 14)
        self.assertGreater(spans[0], 0.5)
        self.assertTrue(all(a + 12 < b for a, b in zip(spans, spans[1:])), spans)
        self.assertGreater(spans[3], 60)
        # It flickers: lights that were on go out again, most of all early on; a light that has
        # held for a while keeps holding.
        def dropouts(start, end):
            window = [f for t, f in frames if start <= t < end]
            return sum(1 for a, b in zip(window, window[1:]) for i in MOUTH if sum(a[i]) and not sum(b[i]))
        self.assertGreater(dropouts(0, 6000), 150)
        held = [i for i in MOUTH if all(sum(f[i]) for t, f in frames if 9000 <= t < 10000)]
        self.assertGreater(len(held), 30)
        for i in held:
            self.assertTrue(all(sum(f[i]) for t, f in frames if 10000 <= t < 12800), f'light {i} dropped after holding')
        # The lights that are on grow brighter through the start.
        def brightest(start, end):
            return max(max(f[i]) for t, f in frames if start <= t < end for i in MOUTH)
        self.assertLess(brightest(0, 1500), brightest(11000, 12800))
        # Not fully lit before READY: in the last second some light is still out or still shimmering.
        last = [f for t, f in before if t >= 11800]
        self.assertTrue(any(len(self.lit(f)) < 80 or len({f[i] for i in MOUTH}) > 1 for f in last))
        self.assertTrue(all({f[i] for i in MOUTH} != {FULL} for t, f in before))
        # READY: every light on at once, swelling to fully lit within half the finale, and steady.
        after = [(t, f) for t, f in frames if t >= 12800 + 60]
        self.assertTrue(all(len(self.lit(f)) == 80 for t, f in after))
        for t, f in after:
            self.assertEqual(len({f[i] for i in MOUTH}), 1, f'not even at {t} ms')
            if t >= 12800 + 500:
                self.assertEqual(f[2], FULL)
        levels = [f[2][1] for t, f in after if t < 12800 + 500]
        self.assertEqual(levels, sorted(levels))

    def test_the_glow_takes_over_from_the_full_mouth_without_a_jump(self):
        frames = self.play('cmd STARTUP:6000 run 6000 cmd READY run 4500')
        after = [(t, f) for t, f in frames if t >= 6000 + 900]
        levels = [f[2][1] for t, f in after]
        self.assertLessEqual(levels[0], FULL[1])
        self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(levels, levels[1:])), 'the mouth jumped')
        settled = [f[2][1] for t, f in after if t >= 6000 + 900 + 1600]
        self.assertTrue(all(10 <= level <= 27 for level in settled), settled)   # The idle glow: 5 to 10 %.
        self.assertTrue(all(len({f[i] for i in MOUTH}) == 1 for t, f in after))

    def test_eye_refreshes_and_idle_commands_do_not_cut_the_flicker_short(self):
        frames = self.play('cmd STARTUP:12800 cmd EYE:255,200,0 run 3000 cmd ACTIVE cmd EYE:255,200,0 '
                           'cmd IDLE cmd SPEAK_STOP run 4000')
        late = [(t, f) for t, f in frames if t >= 5500]
        counts = [len(self.lit(f)) for t, f in late]
        self.assertTrue(all(count < 70 for count in counts))       # Still part-lit: the start goes on.
        self.assertGreater(len(set(counts)), 3)                    # And still flickering.
        self.assertTrue(all(f[0] == f[1] == (255, 200, 0) for t, f in frames if 200 < t and f[0] != (0, 0, 0)))
        self.assertTrue(any(f[0] == (255, 200, 0) for t, f in late))   # The eyes are the host's as usual.

    def test_a_line_spoken_takes_the_mouth_at_once(self):
        frames = self.play('cmd STARTUP:12800 run 4000 cmd SPEAK:neutral cmd SPEAK_LEVEL:220 run 600 '
                           'cmd READY run 600 cmd SPEAK_LEVEL:0 cmd SPEAK_STOP run 1500')
        speaking = [f for t, f in frames if 4300 <= t < 5200]
        self.assertGreater(max(max(f[i]) for f in speaking for i in MOUTH), 90)    # The speaking bars, far brighter.
        end = [f for t, f in frames if t >= 5200 + 1000]
        self.assertTrue(all(len({f[i] for i in MOUTH}) == 1 for f in end))         # Then the ordinary glow, not the flicker.

    def test_a_start_from_the_menu_times_itself(self):
        frames = self.play('cmd STARTUP run 14500')
        self.assertTrue(all({f[i] for i in MOUTH} != {FULL} for t, f in frames if t < 12800))
        self.assertTrue(all(f[2] == FULL for t, f in frames if 12800 + 500 <= t < 12800 + 900))

    def test_a_host_that_never_says_ready_is_waited_for_only_so_long(self):
        frames = self.play('cmd STARTUP:5000 run 16500')
        waiting = [len(self.lit(f)) for t, f in frames if 6000 <= t < 15000]
        self.assertGreater(min(waiting), 55)                       # Nearly lit, the last lights still flickering,
        self.assertTrue(any({f[i] for i in MOUTH} != {FULL} for t, f in frames if 14000 <= t < 15000))
        self.assertTrue(all(f[2] == FULL for t, f in frames if 15000 + 500 <= t < 15000 + 900))   # then lit by itself.

    def test_ready_outside_startup_changes_nothing(self):
        frames = self.play('cmd ACTIVE run 5000 cmd READY run 1000')
        levels = [f[2][1] for t, f in frames if t >= 4500]
        self.assertTrue(all(abs(a - b) <= 2 for a, b in zip(levels, levels[1:])))

    def test_a_shutdown_fade_still_running_gives_way_to_a_new_start(self):
        frames = self.play('cmd ACTIVE cmd EYE:255,200,0 run 5000 cmd FADEOFF run 1000 cmd STARTUP:6000 '
                           'cmd EYE:255,200,0 run 6000 cmd READY run 900')
        self.assertEqual(frames[-1][1][2], FULL)                   # Full brightness is back, not the fade's.
        self.assertEqual(frames[-1][1][0], (255, 200, 0))
