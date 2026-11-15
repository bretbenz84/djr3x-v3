"""Run the LED board's head code (firmware/djr3x_leds/head.cpp) on the Mac against the
stand-in LED library and read back every frame the mouth would show: its charge meter, and
the colour it speaks in for each feeling. No board, no serial.

The frames prove what the code sends to the strip, with a fake clock. They do not prove
how it looks, nor which end of the mouth is physically the top: the firmware takes that
from the dashboard's model of the head (MOUTH_ROW0_AT_TOP).
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
#include "head.h"

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
    head::begin();                           // blanks the strip: frames before "start" are its own
    std::printf("start %u\n", fake_ms);
    std::string word;
    while (std::cin >> word) {
        if (word == "cmd") { std::string text; std::cin >> text; head::command(text.c_str()); }
        else if (word == "run") {            // the board's loop: one pass a millisecond
            int ms; std::cin >> ms;
            for (int i = 0; i < ms; i++) { fake_ms++; head::tick(); }
        }
    }
}
'''

ROWS, COLS = 10, 8
INTRO, CYCLE, TRAVEL = 1200, 3000, 2000


def mouth_pixel(row, col):
    """Strip index of a mouth light: rows are wired back and forth, after the two eyes."""
    return 2 + row * COLS + (COLS - 1 - col if row & 1 else col)


def levels(frame):
    """The mouth as the colour of each row from the bottom up, or None for a row whose
    eight lights differ. Colours are physical (red, green, blue): the mouth strip takes
    green first, so the code stores red and green swapped."""
    rows = []
    for level in range(ROWS):
        row = ROWS - 1 - level                     # row 0 is the top of the mouth
        seen = {frame[mouth_pixel(row, col)] for col in range(COLS)}
        if len(seen) != 1:
            rows.append(None)
            continue
        wire = seen.pop()
        rows.append((wire[1], wire[0], wire[2]))
    return rows


def lit(colour):
    return sum(colour) > 0


class MouthChargeTests(unittest.TestCase):
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
        """Frames as (ms since the first command, the mouth's rows bottom to top, the two eyes)."""
        out = subprocess.run([str(self.binary)], input=script, text=True,
                             capture_output=True, check=True).stdout.splitlines()
        mark = next(i for i, line in enumerate(out) if line.startswith('start'))
        start = int(out[mark].split()[1])
        frames = []
        for line in out[mark + 1:]:
            fields = line.split()
            pixels = [tuple(bytes.fromhex(p)) for p in fields[1:]]
            frames.append((int(fields[0]) - start, levels(pixels), pixels[:2]))
        return frames

    def resting(self, frames, cycle=1):
        """The picture while the band rests, late in the given cycle."""
        at = INTRO + cycle * CYCLE - 200
        return [rows for t, rows, _ in frames if t <= at][-1]

    def test_full_charge_is_a_rainbow_red_at_the_bottom_to_blue_at_the_top(self):
        rows = self.resting(self.play('cmd CHARGE:100 run 5000'))
        self.assertNotIn(None, rows)                           # every row one colour across
        self.assertTrue(all(lit(c) for c in rows))
        red, green, blue = zip(*rows)
        self.assertTrue(red[0] > 0 and green[0] == 0 and blue[0] == 0)      # bottom: red
        self.assertTrue(blue[9] > 0 and red[9] == 0 and green[9] == 0)      # top: blue
        self.assertEqual(len(set(rows)), ROWS)                              # ten different colours
        # Red gives way to green and green to blue on the way up, in that order.
        self.assertEqual(list(red), sorted(red, reverse=True))
        self.assertEqual(list(blue), sorted(blue))
        peak = green.index(max(green))
        self.assertEqual(list(green[:peak + 1]), sorted(green[:peak + 1]))
        self.assertEqual(list(green[peak:]), sorted(green[peak:], reverse=True))

    def test_rows_light_from_the_bottom_up_to_the_charge(self):
        for soc, expected in ((0, 0), (1, 1), (10, 1), (11, 2), (50, 5), (95, 10), (100, 10)):
            with self.subTest(soc=soc):
                rows = self.resting(self.play(f'cmd CHARGE:{soc} run 5000'))
                self.assertEqual([lit(c) for c in rows], [level < expected for level in range(ROWS)])

    def test_a_band_of_light_climbs_from_the_bottom_to_the_top_then_rests(self):
        frames = self.play('cmd CHARGE:100 run 12000')
        steady = max(self.resting(frames)[4])
        for cycle in range(3):
            begin = INTRO + cycle * CYCLE
            # The moment each row is brightest comes later the higher the row.
            best = []
            for level in range(ROWS):
                window = [(max(rows[level]), t) for t, rows, _ in frames if begin <= t < begin + CYCLE]
                best.append(max(window)[1] if max(window)[0] > max(self.resting(frames)[level]) else None)
            self.assertNotIn(None, best)
            self.assertEqual(best, sorted(best))
            self.assertGreater(best[-1] - best[0], 1000)
            self.assertLess(best[-1], begin + TRAVEL)
            # Nothing is sent while it rests: the picture does not change.
            self.assertEqual([t for t, _, _ in frames if begin + TRAVEL + 60 < t < begin + CYCLE], [])
        # The band is brighter than a lit row's steady light, and stays under the speaking bars (45 %).
        brightest = max(max(c) for _, rows, _ in frames for c in rows)
        self.assertGreater(brightest, 3 * steady)
        self.assertLess(brightest, .45 * 255)
        # At most 25 frames a second.
        times = [t for t, _, _ in frames]
        self.assertGreaterEqual(min(b - a for a, b in zip(times, times[1:])), 40)

    def test_above_the_charge_the_band_shows_faintly_in_each_rows_own_colour(self):
        full = self.play('cmd CHARGE:100 run 6000')
        half = self.play('cmd CHARGE:50 run 6000')
        after = [(t, rows) for t, rows, _ in half if t > INTRO]
        for level in range(5, ROWS):
            peak = max((rows[level] for _, rows in after), key=sum)
            lit_peak = max((rows[level] for t, rows, _ in full if t > INTRO), key=sum)
            self.assertTrue(lit(peak), level)
            self.assertLess(sum(peak), .4 * sum(lit_peak))               # fainter than on a lit row
            self.assertEqual([c > 0 for c in peak], [c > 0 for c in lit_peak])   # the row's own colour
        # The lit rows never go dark as the band passes or rests.
        self.assertTrue(all(lit(rows[level]) for _, rows in after for level in range(5)))

    def test_the_rows_come_on_from_the_bottom_and_a_new_level_does_not_replay_it(self):
        frames = self.play('cmd CHARGE:100 run 1300')
        first = [next(t for t, rows, _ in frames if lit(rows[level])) for level in range(ROWS)]
        self.assertEqual(first, sorted(first))
        self.assertEqual(len(set(first)), ROWS)
        self.assertLessEqual(first[-1], INTRO + 40)
        # The charge falls a row while the meter shows: the top row goes out, nothing else blinks.
        frames = self.play('cmd CHARGE:100 run 4000 cmd CHARGE:90 run 4000')
        later = [rows for t, rows, _ in frames if t > 4000]
        self.assertTrue(all(lit(rows[level]) for rows in later for level in range(9)))
        self.assertFalse(lit(self.resting([f for f in frames if f[0] > 4000], cycle=2)[9]))

    def test_the_eyes_stay_dark_and_leaving_the_mode_and_returning_starts_afresh(self):
        frames = self.play('cmd EYE:255,200,0 run 500 cmd CHARGE:80 run 5000 cmd OFF run 500 cmd CHARGE:80 run 1300')
        charging = [(t, rows, eyes) for t, rows, eyes in frames if 500 < t <= 5500]
        self.assertTrue(all(eyes == [(0, 0, 0), (0, 0, 0)] for _, _, eyes in charging))
        back = [(t - 6000, rows) for t, rows, _ in frames if t >= 6000]   # from the command's own blank frame
        self.assertFalse(any(lit(c) for c in back[0][1]))                 # dark, then the rows come on again
        first = [next(t for t, rows in back if lit(rows[level])) for level in range(8)]
        self.assertEqual(first, sorted(first))


class MouthFeelingTests(MouthChargeTests):
    """The colour the mouth speaks in for each feeling (owner, November 15: four more)."""

    def colour(self, emotion):
        """The brightest mouth light while he speaks loudly in that feeling, as physical (r, g, b)."""
        frames = self.play(f'cmd SPEAK:{emotion} cmd SPEAK_LEVEL:255 run 700')
        best = (0, 0, 0)
        for _, rows, _ in frames:
            for row in rows:
                if row is not None and sum(row) > sum(best):
                    best = row
        return best

    def test_each_feeling_speaks_in_its_own_colour(self):
        seen = {e: self.colour(e) for e in ('neutral', 'happy', 'excited', 'sad', 'angry', 'curious',
                                            'surprised', 'proud', 'skeptical', 'sympathetic')}
        self.assertEqual(len(set(seen.values())), 10)
        r, g, b = seen['surprised']
        self.assertTrue(b >= g >= r > .7 * b)                  # icy white
        r, g, b = seen['proud']
        self.assertTrue(r > g > b > 0 and g > .55 * r)         # rich gold
        r, g, b = seen['skeptical']
        self.assertTrue(g > r > 0 and b == 0)                  # sour yellow-green
        r, g, b = seen['sympathetic']
        self.assertTrue(r > b > g > 0)                         # rose
        r, g, b = seen['angry']
        self.assertTrue(r > 0 and g == 0 and b == 0)           # red, as before

    def test_an_unknown_feeling_speaks_in_the_neutral_colour(self):
        self.assertEqual(self.colour('bewildered'), self.colour('neutral'))


if __name__ == '__main__':
    unittest.main()
