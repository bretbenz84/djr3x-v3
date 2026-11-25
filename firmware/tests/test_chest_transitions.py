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
        self.assertLessEqual(first_spark, reached[-1] + 400)         # Faint and few at first (the opening).
        for t, f in frames:
            if 3000 < t < first_spark:
                self.assertGreater(light(f, METER), 0, f'meter dark at {t} ms')
        # The meter is soon gone: the bars start again from nothing, so for the next second the upper
        # half of every bar shows nothing but white sparks.
        upper = [BAR_STARTS[b] + 7 - level for b in range(3) for level in range(4, 8)]
        for t, f in frames:
            if first_spark + 700 < t < first_spark + 1700:
                self.assertTrue(all(len(set(f[i])) == 1 for i in upper), f'meter colour left at {t} ms')

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

    # Startup rise (owner, November 21): the three bars rise and struggle, paced to the host's own start.

    def bar_height(self, frame, bar):
        """Lights on in a bar, counted from the bottom up to the first dark one. Bar A is red and bar
        B blue, so a white spark landing on them is not counted; bar C is white and may count one."""
        height = 0
        for level in range(8):
            r, g, b = frame[BAR_STARTS[bar] + 7 - level]
            on = (r > g) if bar == 0 else (b > r) if bar == 1 else (r + g + b > 0)
            if not on:
                break
            height += 1
        return height

    def longest_full(self, frames, bar, end):
        """The longest unbroken time (ms) a bar shows all eight lights before `end`."""
        longest, since = 0, None
        for t, f in frames:
            if t >= end:
                break
            if self.bar_height(f, bar) == 8:
                since = t if since is None else since
                longest = max(longest, t - since)
            else:
                since = None
        return longest

    def locked_at(self, frames, bars=(0, 1, 2)):
        """When the bars come on full and stay full for the next 300 ms."""
        for index, (t, f) in enumerate(frames):
            window = [g for u, g in frames[index:] if u < t + 300]
            if len(window) > 5 and all(self.bar_height(g, bar) == 8 for g in window for bar in bars):
                return t
        return None

    def mean_height(self, frames, bar, start, end):
        heights = [self.bar_height(f, bar) for t, f in frames if start <= t < end]
        return sum(heights) / len(heights)

    def test_the_bars_rise_from_the_bottom_and_struggle_to_stay_on(self):
        frames = self.play('cmd STARTUP:12800 run 12800 cmd READY run 3000')
        for bar in range(3):
            with self.subTest(bar=bar):
                # They rise: dark at first, higher through the start, in each bar's own time.
                self.assertLess(self.mean_height(frames, bar, 0, 1200), 1.5)
                early, middle, late = (self.mean_height(frames, bar, a, a + 2500) for a in (1500, 5500, 10000))
                self.assertLess(early + 1, middle)
                self.assertLess(middle + 1, late)
                self.assertGreater(late, 5)
                # They struggle: a bar loses lights it had, again and again, and climbs back.
                heights = [self.bar_height(f, bar) for t, f in frames if t < 12800]
                slips = sum(1 for a, b in zip(heights, heights[1:]) if b < a)
                self.assertGreaterEqual(slips, 6)
                # The top light never holds steady: it shows at many brightnesses.
                tops = {max(f[BAR_STARTS[bar] + 7 - (h - 1)]) for (t, f), h in zip(frames, heights) if h}
                self.assertGreaterEqual(len(tops), 12)
        # No bar holds its eighth light before READY: it only flickers there, trying to catch.
        for bar in range(3):
            self.assertLess(self.longest_full(frames, bar, 12800), 300, f'bar {bar} held full before READY')
        self.assertIsNone(self.locked_at([(t, f) for t, f in frames if t < 12800]))
        # The bars do not all rise together: bar A reaches seven lights well before bar B.
        seventh = [next(t for t, f in frames if self.bar_height(f, bar) >= 7) for bar in (0, 1)]
        self.assertLess(seventh[0] + 1000, seventh[1])
        # READY locks them: all three full within 0.4 s, bright, and no light drops for the rest of the finale.
        for t, f in frames:
            if 12800 + 400 <= t < 12800 + 900:
                self.assertEqual([self.bar_height(f, bar) for bar in range(3)], [8, 8, 8], f'bars not locked at {t} ms')
        locked = [f for t, f in frames if 12800 + 150 <= t < 12800 + 250][-1]
        settled = [f for t, f in frames if 12800 + 800 <= t < 12800 + 900][-1]
        self.assertGreater(locked[7][0], settled[7][0])            # They flare as they lock, then ease to steady.
        # Then the ordinary panel has them, blended, with nothing lit outside its bars and blocks.
        self.assert_no_jump(frames, 12800 + 900, [s + i for s in BAR_STARTS for i in range(8)])
        late = [f for t, f in frames if t > 12800 + 900 + 1500]
        self.assertEqual(max(light(f, UNOWNED) for f in late), 0)
        self.assertTrue(any(self.bar_height(f, 0) < 8 for f in late))   # Bar A is the panel's again, changing length.

    def test_the_sparks_and_blocks_follow_the_time_the_host_gives(self):
        for span in (7000, 16000):
            with self.subTest(span=span):
                frames = self.play(f'cmd STARTUP:{span} run {span} cmd READY run 3000')
                first = sorted(self.startup_block_times(frames).values())
                self.assertNotIn(None, first)
                self.assertGreater(first[0], span - 3100)          # Blocks begin three seconds before the planned end.
                self.assertLess(first[-1], span - 600)
                # Sparks fall on pixels the panel never draws: barely any while the start opens (its first
                # third, four seconds at most), plenty after that, thinning out towards the end.
                def sparks(start, end):
                    window = [light(f, UNOWNED) for t, f in frames if start <= t < end]
                    return sum(window) / len(window)
                opened = min(4000, span // 3)
                self.assertGreater(sparks(opened, opened + span // 4), sparks(span * 3 // 4, span))
                self.assertGreater(sparks(opened, opened + span // 4), 4 * sparks(0, opened // 2))
                self.assertGreater(sparks(0, opened // 2), 0)
                self.assertGreater(sparks(span * 3 // 4, span), 0)

    def test_the_start_is_barely_lit_for_two_seconds_and_builds(self):
        # Owner, November 24: "the chest LED patterns get too bright too quickly ... the first 2 seconds ...
        # needs to be less lit up, more like it's barely starting to light up". The first two seconds were
        # the brightest of the sparkle: about 1,100 a frame on this measure, with lights at full from the start.
        frames = self.play('cmd STARTUP:13200 run 13200 cmd READY run 2500')
        def mean_light(start, end):
            window = [light(f) for t, f in frames if start <= t < end]
            return sum(window) / len(window)
        def brightest(start, end):
            return max(max(max(p) for p in f) for t, f in frames if start <= t < end)
        full = mean_light(4500, 8000)                               # The sparkle once it has opened.
        self.assertGreater(full, 800)                               # As bright as it was from the start, before.
        self.assertLess(mean_light(0, 1000), .05 * full)            # Barely anything in the first second,
        self.assertLess(mean_light(1000, 2000), .15 * full)         # little in the second,
        self.assertGreater(mean_light(300, 1000), 0)                # but not dark: it is starting to light up.
        seconds = [mean_light(a, a + 1000) for a in range(0, 5000, 1000)]
        self.assertEqual(seconds, sorted(seconds))                  # It builds, second by second,
        self.assertGreater(mean_light(3500, 4500), .6 * full)       # and is all there by four seconds.
        # No single light is bright early either: a tenth of full at first (full is 90 on this strip).
        self.assertLessEqual(brightest(0, 1000), 20)
        self.assertLessEqual(brightest(1000, 2000), 36)
        self.assertGreaterEqual(brightest(4500, 8000), 85)
        # The two chest lights come up with it instead of coming on at once.
        def lights(start, end):
            return max(light(f, (96, 97)) for t, f in frames if start <= t < end)
        self.assertLess(lights(0, 1000), .25 * lights(5000, 9000))
        self.assertGreater(lights(300, 1000), 0)
        # READY ends the opening, however early it comes: the lock is as bright as ever.
        early = self.play('cmd STARTUP:13200 run 1000 cmd READY run 1500')
        locked = [f for t, f in early if 1000 + 150 <= t < 1000 + 250][-1]
        self.assertGreater(max(locked[7]), 80)
        for t, f in early:
            if 1000 + 400 <= t < 1000 + 900:
                self.assertEqual([self.bar_height(f, bar) for bar in range(3)], [8, 8, 8], f'bars not locked at {t} ms')

    def test_a_start_from_the_menu_times_itself(self):
        frames = self.play('cmd STARTUP run 16000')
        full = self.locked_at(frames)
        self.assertGreaterEqual(full, 12800)                       # It locks at its own planned end,
        self.assertLess(full, 12800 + 450)
        late = [f for t, f in frames if t > 12800 + 900 + 1500]
        self.assertEqual(max(light(f, UNOWNED) for f in late), 0)  # and the panel follows.

    def test_ready_before_the_planned_end_locks_at_once_and_hurries_the_blocks(self):
        frames = self.play('cmd STARTUP:12800 run 6000 cmd READY run 3000')
        for t, f in frames:
            if 6000 + 400 <= t < 6000 + 900:
                self.assertEqual([self.bar_height(f, bar) for bar in range(3)], [8, 8, 8])
        first = self.startup_block_times(frames, start=6000)
        self.assertNotIn(None, first.values())
        self.assertLess(max(first.values()), 6000 + 900)           # Every block has come on by the end of the finale.
        self.assertGreater(min(first.values()), 6000)

    def test_a_host_that_never_says_ready_is_waited_for_only_so_long(self):
        frames = self.play('cmd STARTUP:5000 run 18000')
        full = self.locked_at(frames)
        self.assertGreaterEqual(full, 15000)                       # Ten seconds past the plan, still struggling;
        self.assertLess(full, 15000 + 450)                         # then it locks by itself.
        waiting = [self.bar_height(f, 0) for t, f in frames if 6000 <= t < 15000]
        self.assertGreater(max(waiting), 5)
        self.assertGreater(sum(1 for a, b in zip(waiting, waiting[1:]) if b < a), 4)   # Still losing lights at the top.
        sparks = sum(1 for t, f in frames if 7000 <= t < 15000 and light(f, UNOWNED))
        self.assertGreater(sparks, 5)                              # A sparse spark keeps it alive meanwhile.
        late = [f for t, f in frames if t > 15000 + 900 + 1500]
        self.assertEqual(max(light(f, UNOWNED) for f in late), 0)

    def test_ready_outside_startup_changes_nothing(self):
        frames = self.play('cmd IDLE run 2000 cmd READY run 1000')
        self.assert_no_jump(frames, 2000)

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
        for emotion in ('excited', 'happy', 'sad', 'angry', 'curious', 'surprised', 'proud', 'skeptical', 'sympathetic'):
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

    def test_shutdown_with_a_battery_level_ends_in_the_charge_meter(self):
        # Owner, November 15: the shutdown fade leads straight into the meter, with no dark gap.
        plain = self.play('cmd IDLE run 3000 cmd FADEOFF run 4500')
        linked = self.play('cmd IDLE run 3000 cmd FADEOFF:50:0 run 2000 cmd FADEOFF:50:0 run 7000')
        # The first two seconds are the ordinary fade, and a repeat does not restart it.
        self.assertEqual([f for t, f in plain if t < 5000], [f for t, f in linked if t < 5000])
        # Then the meter sweeps up from the bottom under the rest of the fade (50 % lights 12).
        reached = [next(t for t, f in linked if t > 5000 and lit(f[METER[level]]) and
                        f[METER[level]] != next(g for u, g in plain if u >= t)[METER[level]]) for level in range(12)]
        self.assertEqual(reached, sorted(reached))
        self.assertLessEqual(reached[-1], 7000 + 40)
        # It is never dark: there is light every frame from the fade through the meter.
        self.assertTrue(all(light(f) > 0 for t, f in linked if t > 3000))
        # From the end of the fade only the meter shows, reading the charge.
        for t, f in linked:
            if t > 7050:
                self.assertTrue(all(lit(f[METER[level]]) for level in range(11)), t)
                self.assertEqual(light(f, [i for i in range(98) if i not in METER[:12]]), 0, t)
        self.assert_no_jump(linked, 7000, METER[:11])

    def test_the_battery_monitor_confirms_the_meter_without_a_change(self):
        frames = self.play('cmd IDLE run 3000 cmd FADEOFF:50:0 run 5500 cmd CHARGE:50:0 run 63000')
        self.assert_no_jump(frames, 8500, METER[:11])
        self.assertTrue(all(lit(frames[-1][1][METER[level]]) for level in range(11)))

    def test_a_meter_nothing_confirms_fades_out_after_a_minute(self):
        frames = self.play('cmd IDLE run 3000 cmd FADEOFF:50:0 run 66000')
        before = [f for t, f in frames if 3000 + 4000 + 59000 < t < 3000 + 4000 + 60000]
        self.assertTrue(all(lit(f[METER[0]]) for f in before))
        self.assert_no_jump(frames, 3000 + 4000 + 60000 + 10, METER[:11])   # it fades, not snaps
        self.assertEqual(max(light(f) for t, f in frames if t > 3000 + 4000 + 60000 + 1100), 0)

    def test_the_two_chest_lights_stay_dark_under_the_meter(self):
        # They used to blip gold for a single frame every few seconds.
        frames = self.play('cmd CHARGE:100:0 run 12000')
        self.assertEqual(max(light(f, (96, 97)) for t, f in frames if t > 500), 0)

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


    # --- The meter moves as a whole (owner, November 24: "discharging just blinks the last LED but it would
    # be cool if the whole panel animated that it was draining or charging") ---------------------------------

    def meter_levels(self, script, start):
        """Each meter level's brightness (its strongest colour) frame by frame from a moment on."""
        frames = [(t, f) for t, f in self.play(script) if t >= start]
        return frames, [[max(f[METER[level]]) for t, f in frames] for level in range(24)]

    def crest_times(self, frames, series, levels, start, end):
        """When each of some levels is at its brightest between two moments."""
        times = []
        for level in levels:
            window = [(value, t) for (t, f), value in zip(frames, series[level]) if start <= t < end]
            top = max(value for value, t in window)
            at = [t for value, t in window if value == top]
            times.append((at[0] + at[-1]) / 2)
        return times

    def test_off_the_charger_the_whole_meter_drains_downwards(self):
        frames, series = self.meter_levels('cmd CHARGE:70:0 run 20000', 4000)    # 70 % lights 17 of 24.
        top = 16
        # Every lit light moves, not only the last one, and none of them ever goes out.
        for level in range(top):
            self.assertGreater(max(series[level]) - min(series[level]), 12, f'level {level} hardly moves')
            self.assertGreater(min(series[level]), 12, f'level {level} went dark')
        # Nothing above the fill line, nothing off the meter: the charge still reads by where the lights stop.
        self.assertEqual(max(max(series[level]) for level in range(top + 1, 24)), 0)
        self.assertEqual(max(light(f, [i for i in range(98) if i not in METER]) for t, f in frames), 0)
        # A drop leaves the top lit light every 4.4 s and falls through every light to the bottom.
        cycle = 4400
        first = 2000 + 2 * cycle                                       # The sweep, then whole cycles.
        for start in (first, first + cycle):
            crests = self.crest_times(frames, series, range(top - 1, -1, -1), start + 900, start + cycle)
            self.assertEqual(crests, sorted(crests))                   # Downwards, light by light,
            self.assertTrue(1000 < crests[-1] - crests[0] < 2600, crests[-1] - crests[0])
            gaps = [b - a for a, b in zip(crests, crests[1:])]
            self.assertLess(sum(gaps[-4:]), sum(gaps[:4]))             # gathering speed as it falls.
        # The top light swells, lets go, is left dim and comes back.
        rest = sorted(series[top])[len(series[top]) // 2]
        self.assertGreater(max(series[top]), 1.3 * rest)
        self.assertLess(min(series[top]), .5 * rest)
        self.assertGreater(min(series[top]), 0)
        # Smoothly: no light jumps between two frames.
        for level in range(24):
            self.assertLessEqual(max(abs(a - b) for a, b in zip(series[level], series[level][1:])), 6, f'level {level} jumped')

    def test_on_the_charger_the_whole_meter_fills_upwards(self):
        frames, series = self.meter_levels('cmd CHARGE:40:1 run 20000', 4000)    # 40 % lights 10 of 24.
        for level in range(10):
            self.assertGreater(max(series[level]) - min(series[level]), 12, f'level {level} hardly moves')
            self.assertGreater(min(series[level]), 12, f'level {level} went dark')
        # A band of light enters at the bottom every 2.4 s and climbs: through the lit lights, then on to the
        # top as a faint glow over the empty ones, which are dark between bands.
        cycle = 2400
        start = 2000 + 3 * cycle                                       # One band enters as each cycle begins,
        crests = self.crest_times(frames, series, range(22), start, start + cycle)   # and passes level 21 within it.
        climbing = [b - a for a, b in zip(crests, crests[1:])]
        self.assertTrue(all(40 <= gap <= 160 for gap in climbing), climbing)       # Ten lights a second, upwards.
        lit_crest = min(max(series[level]) for level in range(10))
        for level in range(10, 24):
            self.assertGreater(max(series[level]), 2, f'level {level} never glows')
            self.assertLess(max(series[level]), .5 * lit_crest)
            self.assertEqual(min(series[level]), 0)
        # In each light's own colour: nothing blue passes over the red end of the meter.
        self.assertEqual(max(f[METER[level]][2] for t, f in frames for level in range(6)), 0)
        self.assertEqual(max(light(f, [i for i in range(98) if i not in METER]) for t, f in frames), 0)
        for level in range(24):
            self.assertLessEqual(max(abs(a - b) for a, b in zip(series[level], series[level][1:])), 6, f'level {level} jumped')

    def test_draining_and_filling_run_opposite_ways(self):
        # A full meter off the charger: the drop falls all twenty-four lights, the top one brightest first.
        frames, series = self.meter_levels('cmd CHARGE:100:0 run 16000', 4000)
        start = 2000 + 2 * 4400
        crests = self.crest_times(frames, series, range(4, 20), start + 900, start + 4400)
        self.assertLess(crests[-1] - crests[0], -500)
        # Full and on the charger nothing more is going in: only a slow ripple is left, and it runs upwards
        # (a crest reaches the next light up about 0.29 s later, once every 2 s).
        frames, series = self.meter_levels('cmd CHARGE:100:1 run 14000', 4000)
        for level in range(24):
            swing = max(series[level]) - min(series[level])
            self.assertTrue(3 <= swing <= 14, (level, swing))
        lower, upper = self.crest_times(frames, series, (9, 10), 6000, 8000)
        self.assertTrue(120 < (upper - lower) % 2000 < 480, (upper - lower) % 2000)

    def test_the_meter_lands_steady_and_the_movement_comes_in_after(self):
        frames, series = self.meter_levels('cmd CHARGE:70:0 run 6000', 2000)     # From the end of the sweep.
        landed = [values for (t, f), values in zip(frames, zip(*series)) if t < 2100]
        moving = [values for (t, f), values in zip(frames, zip(*series)) if 3300 <= t < 6000]
        for level in range(17):
            self.assertLessEqual(max(v[level] for v in landed) - min(v[level] for v in landed), 2)   # Steady as it lands,
            self.assertLessEqual(max(abs(a - b) for a, b in zip(series[level], series[level][1:])), 6)   # no jump after,
            self.assertGreater(max(v[level] for v in moving) - min(v[level] for v in moving), 6)     # and then it moves.

    # --- Feelings added November 15 (owner: emotional reactions on the chest) -----------------

    def feeling(self, emotion, ms=5000):
        """Frames from the moment the feeling is asked for, on an idle panel."""
        frames = self.play(f'cmd IDLE run 3000 cmd SPEAK:{emotion} run {ms}')
        return [(t - 3000, f) for t, f in frames if t > 3000]

    def bar_lit(self, frame, start):
        return [lit(frame[start + i]) for i in range(8)]

    def test_each_new_feeling_shows_in_its_own_colour(self):
        def tone(emotion):
            frames = [f for t, f in self.feeling(emotion) if t > 1500]
            return [sum(f[i][c] for f in frames for i in range(96)) for c in range(3)]
        red, green, blue = tone('curious')
        self.assertTrue(blue > red > green)                    # violet
        red, green, blue = tone('surprised')
        self.assertTrue(blue > green > red)                    # blue with a little white
        red, green, blue = tone('proud')
        self.assertTrue(red > green > blue)                    # gold
        red, green, blue = tone('skeptical')
        self.assertTrue(green > red > blue == 0)               # sour yellow-green
        red, green, blue = tone('sympathetic')
        self.assertTrue(red > blue > green)                    # rose

    def test_surprise_bursts_from_the_middle_of_every_bar_then_settles(self):
        frames = self.feeling('surprised', 6000)
        # Within a quarter of a second every bar is full and every block is on.
        soon = [f for t, f in frames if 230 <= t <= 340]
        self.assertTrue(soon)
        for f in soon:
            self.assertTrue(all(sum(f[s + i]) > 250 for s in BAR_STARTS for i in range(8)))
            self.assertTrue(all(light(f, range(b, b + 4)) > 600 for b in BLOCK_STARTS))
        # It opened from the middle: the middle pair of a bar lit before its ends.
        first = {i: next(t for t, f in frames if sum(f[i]) > 250) for i in (0, 3, 4, 7)}
        self.assertLess(max(first[3], first[4]), min(first[0], first[7]))
        # Then the blocks flicker, fast at first and slower as he settles; the bars stay full.
        def on(f, blk):                                        # bright, not merely the faint blue floor
            return light(f, range(blk, blk + 4)) > 600
        def changes(a, b):
            window = [f for t, f in frames if a < t <= b]
            return sum(on(x, blk) != on(y, blk) for x, y in zip(window, window[1:]) for blk in BLOCK_STARTS)
        self.assertGreater(changes(400, 1400), 2 * changes(4000, 5000))
        self.assertGreater(changes(400, 1400), 20)
        late = [f for t, f in frames if t > 4000]
        self.assertTrue(all(sum(f[s + i]) > 250 for f in late for s in BAR_STARTS for i in range(8)))
        self.assertGreater(sum(on(f, b) for f in late for b in BLOCK_STARTS), .6 * 9 * len(late))
        # Never a white panel: white is kept to the bar tips and a block or two.
        self.assertLessEqual(max(sum(min(f[i]) > 100 for i in range(96)) for _, f in frames), 20)

    def test_pride_fills_the_bars_from_the_bottom_and_then_nothing_blinks(self):
        frames = self.feeling('proud', 6000)
        for start in BAR_STARTS:
            # Gold at pride's brightness, not the idle panel's dim lights blending away.
            first = [next(t for t, f in frames if sum(f[start + i]) > 250) for i in range(8)]
            self.assertEqual(first, sorted(first, reverse=True))        # pixel 7 is the bottom of a bar
            self.assertGreater(first[0] - first[7], 300)
        steady = [f for t, f in frames if t > 1800]
        for f in steady:
            self.assertTrue(all(all(self.bar_lit(f, s)) for s in BAR_STARTS))
            self.assertTrue(all(block_lit(f, b) for b in BLOCK_STARTS))
            self.assertEqual(light(f, UNOWNED), 0)
        # The blocks came on one after another.
        blocks = sorted(next(t for t, f in frames if block_lit(f, b)) for b in BLOCK_STARTS)
        self.assertGreater(blocks[-1] - blocks[0], 600)

    def test_skeptical_stops_the_panel_and_raises_one_eyebrow(self):
        frames = [(t, f) for t, f in self.feeling('skeptical', 9000) if t > 800]
        for t, f in frames:
            self.assertEqual(sum(lit(f[i]) for s in BAR_STARTS for i in range(s, s + 8)), 6, t)    # three flat lines
            self.assertEqual(sum(block_lit(f, b) for b in BLOCK_STARTS), 2, t)
            self.assertEqual(self.bar_lit(f, 0), [False]*3 + [True]*2 + [False]*3)
            self.assertEqual(self.bar_lit(f, 32), [False]*3 + [True]*2 + [False]*3)
        # The last bar's line goes up two pixels (towards pixel 0), stays, and comes back.
        top = [min(i for i in range(8) if lit(f[64 + i])) for _, f in frames]
        self.assertEqual(sorted(set(top)), [1, 2, 3])
        raised = [t for (t, _), at in zip(frames, top) if at == 1]
        self.assertGreater(raised[-1] - raised[0], 3000)               # more than once
        self.assertGreater(sum(at == 3 for at in top), sum(at != 3 for at in top))   # mostly level
        # Dim: a fraction of the idle panel's light.
        idle = self.play('cmd IDLE run 3000')
        self.assertLess(max(light(f) for _, f in frames), .5 * max(light(f) for t, f in idle if t > 1000))

    def test_sympathy_beats_twice_and_rests_and_never_goes_dark(self):
        frames = [(t, f) for t, f in self.feeling('sympathetic', 8000) if t > 1300]
        levels = [(t, light(f, range(32, 40))) for t, f in frames]          # the middle bar
        self.assertGreater(min(v for _, v in levels), 0)
        peaks = [t for (_, a), (t, b), (_, c) in zip(levels, levels[1:], levels[2:]) if a < b >= c and b > 1.5 * min(v for _, v in levels)]
        gaps = [b - a for a, b in zip(peaks, peaks[1:])]
        self.assertGreaterEqual(len(peaks), 8)
        short, long = [g for g in gaps if g < 500], [g for g in gaps if g >= 500]
        self.assertTrue(short and long)
        self.assertTrue(all(230 <= g <= 380 for g in short), gaps)           # the second beat
        self.assertTrue(all(820 <= g <= 980 for g in long), gaps)            # the rest
        # The outer panels' beat lands a moment after the middle panel's (one beat, mid-run).
        window = [(t, f) for t, f in frames if 2400 - 200 <= t <= 2400 + 250]
        peak = lambda pixels: max(window, key=lambda tf: light(tf[1], pixels))[0]
        self.assertTrue(30 <= peak(range(0, 8)) - peak(range(32, 40)) <= 110)
        self.assertTrue(30 <= peak(range(64, 72)) - peak(range(32, 40)) <= 110)

    def test_curious_checks_the_blocks_one_at_a_time(self):
        frames = [(t, f) for t, f in self.feeling('curious', 6000) if t > 800]
        def brightest(f):
            return max(BLOCK_STARTS, key=lambda b: light(f, range(b, b + 4)))
        order = []
        for _, f in frames:
            if not order or order[-1] != brightest(f):
                order.append(brightest(f))
        self.assertEqual(set(order), set(BLOCK_STARTS))
        self.assertEqual(order[:9], [BLOCK_STARTS[(BLOCK_STARTS.index(order[0]) + k) % 9] for k in range(9)])
        # Each bar has one bright head that moves.
        for start in BAR_STARTS:
            heads = {max(range(8), key=lambda i: sum(f[start + i])) for _, f in frames}
            self.assertGreaterEqual(len(heads), 6)

    def test_a_feeling_leaves_no_colour_behind_on_the_idle_panel(self):
        for emotion in ('curious', 'surprised', 'proud', 'skeptical', 'sympathetic'):
            with self.subTest(emotion=emotion):
                frames = self.play(f'cmd IDLE run 3000 cmd SPEAK:{emotion} run 2500 cmd SPEAK_STOP run 2500')
                settled = [f for t, f in frames if t > 5500 + 700]
                self.assertEqual(max(light(f, UNOWNED) for f in settled), 0)
                self.assertTrue(any(block_lit(f, b) for f in settled for b in BLOCK_STARTS))


if __name__ == '__main__':
    unittest.main()
