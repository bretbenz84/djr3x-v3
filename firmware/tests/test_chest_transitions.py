"""Run the LED board's chest code (firmware/djr3x_leds/chest.cpp) on the Mac against a
stand-in LED library and read back every frame it would show. No board, no serial.

The frames prove what the code sends to the strip, with a fake clock and fixed random
numbers. They do not prove how it looks: colour balance, the supply and the real frame
rate are only seen on the robot.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

HARNESS = r'''
#include <iostream>
#include <string>
#include "FastLED.h"
#include "chest.h"

uint32_t fake_ms = 1000;
uint32_t fake_seed = 2463534242u;
FakeFastLED FastLED;

void fake_show(const CRGB *leds, int count, uint8_t brightness) {
    std::printf("%u", fake_ms);
    for (int i = 0; i < count; i++) {
        CRGB c = leds[i];
        c.nscale8(brightness);
        std::printf(" %02x%02x%02x", c.r, c.g, c.b);
    }
    std::printf("\n");
}

int main() {
    chest::begin();
    std::string word;
    while (std::cin >> word) {
        if (word == "cmd") { std::string text; std::cin >> text; chest::command(text.c_str()); }
        else if (word == "run") {            // the board's loop: one pass a millisecond
            int ms; std::cin >> ms;
            for (int i = 0; i < ms; i++) { fake_ms++; chest::tick(); }
        }
    }
}
'''

BAR_STARTS = (0, 32, 64)
BLOCK_STARTS = (12, 24, 28, 40, 44, 48, 84, 88, 92)
OWNED = {s + i for s in BAR_STARTS for i in range(8)} | {s + i for s in BLOCK_STARTS for i in range(4)}
UNOWNED = set(range(96)) - OWNED     # pixels the ordinary panel never draws


def meter_pixel(level):
    """Charge meter level 0 (bottom of panel A) to 23 (top of panel C)."""
    return BAR_STARTS[level >> 3] + 7 - (level & 7)


METER = [meter_pixel(level) for level in range(24)]


def lit(pixel):
    return sum(pixel) > 0


def block_lit(frame, start):
    return all(lit(frame[start + i]) for i in range(4))


def light(frame, pixels=range(98)):
    return sum(sum(frame[i]) for i in pixels)


class ChestTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        source = Path(cls.tmp.name) / 'harness.cpp'
        cls.binary = Path(cls.tmp.name) / 'chest'
        source.write_text(HARNESS)
        subprocess.run(['clang++', '-std=c++17', '-O1', '-w', '-I', str(ROOT / 'tests' / 'fake_fastled'),
                        '-I', str(ROOT / 'djr3x_leds'), str(source), str(ROOT / 'djr3x_leds' / 'chest.cpp'),
                        '-o', str(cls.binary)], check=True)

    def play(self, script):
        """Frames as (ms since the script began, 98 (r, g, b) pixels)."""
        out = subprocess.run([str(self.binary)], input=script, text=True, capture_output=True, check=True).stdout
        frames = []
        for line in out.splitlines():
            fields = line.split()
            frames.append((int(fields[0]) - 1000,
                           [tuple(bytes.fromhex(p)) for p in fields[1:]]))
        return frames

    def around(self, frames, at):
        """The last frame before a moment and the first after it."""
        before = [f for t, f in frames if t <= at][-1]
        after = [f for t, f in frames if t > at][0]
        return before, after

    def assert_no_jump(self, frames, at, pixels=range(98)):
        before, after = self.around(frames, at)
        worst = max(abs(a - b) for i in pixels for a, b in zip(before[i], after[i]))
        self.assertLessEqual(worst, 6, f'the lights jumped by {worst} at {at} ms')

    def test_spark_burns_the_charge_meter_then_the_startup_sparks_begin(self):
        frames = self.play('cmd CHARGE:100:0 run 3000 cmd STARTUP run 3000')
        # The meter does not blink out when startup is asked for (its top pixel blinks by design).
        self.assert_no_jump(frames, 3000, METER[:23])
        # The white spark reaches each meter pixel in turn, bottom to top.
        reached = []
        for pixel in METER:
            reached.append(next(t for t, f in frames if t > 3000 and min(f[pixel]) >= 40))
        self.assertEqual(reached, sorted(reached))
        self.assertGreater(len(set(reached)), 20)
        self.assertGreaterEqual(reached[0], 3000 + 300)
        self.assertLessEqual(reached[-1], 3000 + 300 + 24 * 38 + 60)
        # Nothing else on the panels lights until the spark has reached the top, and the
        # meter is never dark before then.
        others = [i for i in range(96) if i not in METER]
        first_spark = next(t for t, f in frames if t > 3000 and light(f, others))
        self.assertGreaterEqual(first_spark, reached[-1])
        self.assertLessEqual(first_spark, reached[-1] + 150)
        for t, f in frames:
            if 3000 < t < first_spark:
                self.assertGreater(light(f, METER), 0, f'meter dark at {t} ms')
        # The meter's colours are soon gone: what is left on its pixels is white sparks.
        for t, f in frames:
            if t > first_spark + 700:
                self.assertTrue(all(len(set(f[i])) == 1 for i in METER), f'meter colour left at {t} ms')

    def startup_block_times(self, frames, start=0):
        return {b: next((t for t, f in frames if t > start and block_lit(f, b)), None) for b in BLOCK_STARTS}

    def test_panel_comes_on_piece_by_piece_under_the_last_sparks(self):
        frames = self.play('cmd STARTUP run 16000')
        first = self.startup_block_times(frames)
        times = sorted(first.values())
        self.assertNotIn(None, times)
        self.assertGreater(times[0], 9000)                 # sparks alone until the last three seconds
        self.assertLess(times[-1], 13200)
        self.assertGreater(times[-1] - times[0], 1200)     # not all at once
        self.assertEqual(len(set(times)), 9)
        # Each block stutters as it catches: lit, out again within 130 ms, then back.
        for block, t0 in first.items():
            window = [f for t, f in frames if t0 < t <= t0 + 130]
            self.assertTrue(any(not block_lit(f, block) for f in window), f'block {block} came on without a stutter')
            self.assertTrue(any(block_lit(f, block) for t, f in frames if t0 + 330 <= t <= t0 + 700))
        # Afterwards the ordinary panel is running, with nothing lit outside its bars and blocks.
        late = [f for t, f in frames if t > 14500]
        self.assertGreaterEqual(sum(any(block_lit(f, b) for f in late) for b in BLOCK_STARTS), 5)
        self.assertEqual(max(light(f, UNOWNED) for f in late), 0)

    def test_the_boot_line_does_not_cut_the_startup_short(self):
        frames = self.play('cmd STARTUP run 2000 cmd SPEAK:neutral run 3000 cmd SPEAK_STOP run 11000')
        times = sorted(self.startup_block_times(frames).values())
        self.assertGreater(times[0], 9000)
        self.assertGreater(times[-1] - times[0], 1200)

    def test_an_emotion_takes_over_from_startup_and_leaves_no_stale_colour(self):
        frames = self.play('cmd STARTUP run 2000 cmd SPEAK:angry run 1500 cmd SPEAK_STOP run 2000')
        self.assert_no_jump(frames, 2000)
        angry = [f for t, f in frames if 3000 < t <= 3500]
        red = sum(f[i][0] for f in angry for i in range(96))
        rest = sum(f[i][1] + f[i][2] for f in angry for i in range(96))
        self.assertGreater(red, 5 * rest)
        self.assert_no_jump(frames, 3500)
        settled = [f for t, f in frames if t > 3500 + 700]
        self.assertEqual(max(light(f, UNOWNED) for f in settled), 0)   # angry's red simmer is gone
        self.assertTrue(any(block_lit(f, b) for f in settled for b in BLOCK_STARTS))

    def test_speaking_patterns_blend_in_and_out(self):
        for emotion in ('excited', 'happy', 'sad', 'angry'):
            with self.subTest(emotion=emotion):
                frames = self.play(f'cmd IDLE run 3000 cmd SPEAK:{emotion} run 2000 cmd SPEAK_STOP run 1500')
                self.assert_no_jump(frames, 3000)
                self.assert_no_jump(frames, 5000)

    def test_sparkle_blends_in_and_hands_back_without_a_jump(self):
        frames = self.play('cmd IDLE run 3000 cmd COMPLIMENT run 4000 cmd SPEAK_STOP run 1500')
        self.assert_no_jump(frames, 3000)
        blue = [f for t, f in frames if 4000 < t < 5000]
        self.assertTrue(all(sum(f[i][2] for i in range(96)) > sum(f[i][0] for i in range(96)) for f in blue))
        self.assert_no_jump(frames, 3000 + 2500)           # its own end, into the bright panel
        self.assert_no_jump(frames, 7000)
        self.assertEqual(max(light(f, UNOWNED) for t, f in frames if t > 6200), 0)   # blue wash gone

    def test_neutral_speech_changes_nothing_on_the_idle_panel(self):
        plain = self.play('cmd IDLE run 6000')
        spoken = self.play('cmd IDLE run 2000 cmd SPEAK:neutral run 2000 cmd SPEAK_STOP run 2000')
        self.assertEqual(plain, spoken)

    def test_shutdown_fade_runs_four_seconds_in_a_straight_line(self):
        frames = self.play('cmd IDLE run 3000 cmd FADEOFF run 2000 cmd FADEOFF run 2500')
        start = light(self.around(frames, 3000)[0])
        fade = [(t, light(f)) for t, f in frames if t > 3000]
        self.assertGreater(start, 0)
        self.assertTrue(all(b <= a for (_, a), (_, b) in zip(fade, fade[1:])))
        half = next(v for t, v in fade if t >= 5000)
        self.assertTrue(.35 * start < half < .65 * start, (start, half))   # the repeat did not restart it
        self.assertGreater(next(v for t, v in fade if t >= 6500), 0)
        self.assertEqual(max(v for t, v in fade if t >= 7050), 0)

    def test_off_is_instant(self):
        frames = self.play('cmd IDLE run 3000 cmd OFF run 500')
        self.assertGreater(light([f for t, f in frames if t < 3000][-1]), 0)
        self.assertEqual(max(light(f) for t, f in frames if t >= 3000), 0)   # the command shows black itself

    def test_charge_meter_still_reads_the_charge(self):
        frames = self.play('cmd CHARGE:50:0 run 4000')
        for t, f in frames:
            if t > 2600:
                self.assertTrue(all(lit(f[METER[level]]) for level in range(11)))
                self.assertEqual(light(f, METER[12:]), 0)
        # Wake, then shutdown, then the meter again: it returns by blending, not from black.
        frames = self.play('cmd CHARGE:50:0 run 3000 cmd SLEEP run 2000 cmd CHARGE:50:0 run 3000')
        self.assert_no_jump(frames, 3000)
        self.assert_no_jump(frames, 5000)


if __name__ == '__main__':
    unittest.main()
