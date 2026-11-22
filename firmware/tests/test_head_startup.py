"""Run the LED board's head code on the Mac against the stand-in LED library and read back the mouth
and eyes: at startup the mouth dark for two seconds of the sound, a light or two twinkling with its
loudness, then the sparkle spreading from the centre of the mouth, solid as the chest lands on its
ordinary panel; the eyes dark behind the visor until they blink on; the slow, dim ripple of the resting
mouth; and the wink (owner, November 21, 2026). No board, no serial.

The frames prove what the code sends to the strip, with a fake clock. They do not prove how it looks.
"""
from math import sqrt
from pathlib import Path
from statistics import median
import subprocess
import tempfile
import unittest

from test_head_charge import HARNESS, ROOT

MOUTH = range(2, 82)
FULL = (35, 64, 0)        # Solid: the amber at a quarter brightness, as stored (green and red swapped).
GOLD = (255, 200, 0)      # The host's awake eyes.
READY = 13400             # When the host says READY in these plays; the finale is 900 ms, the solid hold 600.


def distance(pixel):
    """How far a mouth light is from the mouth's centre: 0 at the centre, 1 at the corners."""
    row, place = divmod(pixel - 2, 8)
    col = 7 - place if row & 1 else place
    return sqrt((((col - 3.5) / 3.5) ** 2 + ((row - 4.5) / 4.5) ** 2) / 2)


CENTRE = sorted(MOUTH, key=distance)[:4]
CORNERS = sorted(MOUTH, key=distance)[-4:]


class HeadPlay(unittest.TestCase):
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

    @staticmethod
    def share_lit(frames, pixel, start, end):
        window = [f for t, f in frames if start <= t < end]
        return sum(1 for f in window if sum(f[pixel])) / len(window)


SOUND = 1600             # When the host says the startup sound has begun, after STARTUP.
WAIT, GROW, END = 2000, 6500, 11800   # From the sound's start: dark until, twinkling until, the front out by.


def start(levels=((0, 150),), ready=True, tail=1500):
    """A hosted start: STARTUP, the sound beginning, its loudness at times after that (ms, level), READY."""
    script = f'cmd STARTUP:{READY} run {SOUND} cmd STARTUP_SOUND:{WAIT},{GROW},{END} '
    at = 0
    for when, level in levels:
        script += f'run {when - at} cmd STARTUP_LEVEL:{level} '
        at = when
    script += f'run {READY - SOUND - at} '
    return script + (f'cmd READY run {tail}' if ready else '')


class MouthStartupTests(HeadPlay):
    def runs(self, frames, start, end):
        """How long (ms) each light stayed lit, for every time one came on between two moments."""
        window = [(t, f) for t, f in frames if start <= t < end]
        lasted, since = [], {}
        for t, f in window:
            for i in MOUTH:
                if sum(f[i]):
                    since.setdefault(i, t)
                elif i in since:
                    lasted.append((i, t - since.pop(i)))
        return lasted

    def test_the_mouth_waits_two_seconds_into_the_sound_then_twinkles_a_light_or_two(self):
        frames = self.play(start())
        # Dark until two seconds after the sound begins, whatever the host sends meanwhile.
        for t, f in frames:
            if t < SOUND + WAIT - 40:
                self.assertEqual(self.lit(f), [], f'the mouth lit at {t} ms')
        # Then a light or two, here and there, for the seconds until the sound comes back up.
        twinkling = [(t, f) for t, f in frames if SOUND + WAIT + 200 <= t < SOUND + GROW]
        counts = [len(self.lit(f)) for t, f in twinkling]
        self.assertLessEqual(max(counts), 7)
        self.assertTrue(.8 < sum(counts) / len(counts) < 3.5, sum(counts) / len(counts))
        self.assertIn(0, counts)                                          # At moments, none at all.
        # Only the ordinary amber.
        for t, f in frames:
            for i in self.lit(f):
                r, g, b = f[i]
                self.assertTrue(g > r > 0 and b == 0 or (g, b) == (g, 0) and g <= 8, f'not amber at {t} ms: {f[i]}')
        # Each twinkle fades in and out over about a third of a second; no light stays on.
        lasted = [ms for i, ms in self.runs(frames, SOUND + WAIT, SOUND + GROW)]
        self.assertGreater(len(lasted), 12)
        self.assertTrue(200 <= median(lasted) <= 420, median(lasted))
        self.assertLess(max(lasted), 800)
        one = next(i for i, ms in self.runs(frames, SOUND + WAIT, SOUND + GROW) if ms >= 300)
        levels = [f[one][1] for t, f in twinkling if sum(f[one])][:9]
        self.assertGreater(max(levels), levels[0] + 10)                   # It swells, it does not snap on.
        # Here and there: near the centre and out at the edges alike.
        where = {i for i, ms in self.runs(frames, SOUND + WAIT, SOUND + GROW)}
        self.assertTrue(any(distance(i) < .4 for i in where) and any(distance(i) > .7 for i in where))

    def test_the_twinkles_follow_how_loud_the_sound_is(self):
        # The sound rises, dies away a little, and comes back: more twinkles, almost none, more again.
        frames = self.play(start(levels=((0, 160), (3600, 70), (4800, 160))))
        def mean(a, b):
            return self.mean_lit(frames, SOUND + a, SOUND + b)
        loud, lull, back = mean(2300, 3600), mean(4000, 4800), mean(5200, 6500)
        self.assertGreater(loud, 1.2)
        self.assertLess(lull, .6)
        self.assertGreater(back, lull + .5)
        silent = self.play(start(levels=((0, 40),)))
        self.assertEqual(max(len(self.lit(f)) for t, f in silent if t < SOUND + GROW), 0)   # Nothing in silence.

    def test_then_the_mouth_sparkles_on_from_the_centre_outwards(self):
        frames = self.play(start(levels=((0, 150), (6500, 180), (9000, 255))))
        before = [(t, f) for t, f in frames if t < READY]
        # It grows from the moment the sound comes back up: a light or two until then, under half by the end.
        self.assertLess(self.mean_lit(frames, SOUND + GROW - 1500, SOUND + GROW), 4)
        self.assertLess(self.mean_lit(frames, SOUND + GROW, SOUND + GROW + 1500), 8)
        late = self.mean_lit(frames, READY - 1000, READY)
        self.assertTrue(22 < late < 48, late)
        middle = self.mean_lit(frames, SOUND + GROW + 2000, SOUND + GROW + 3500)
        self.assertTrue(self.mean_lit(frames, SOUND + GROW, SOUND + GROW + 1500) < middle < late)
        # From the centre outwards: by the end the centre holds and the corners have not caught.
        for pixel in CENTRE:
            self.assertGreater(self.share_lit(frames, pixel, READY - 1500, READY), .95)
        for pixel in CORNERS:
            self.assertLess(self.share_lit(frames, pixel, READY - 1500, READY), .3)
        holding = [i for i in MOUTH if self.share_lit(frames, i, READY - 1200, READY) > .95]
        dark = [i for i in MOUTH if self.share_lit(frames, i, READY - 1200, READY) < .3]
        self.assertLess(sum(map(distance, holding)) / len(holding) + .3, sum(map(distance, dark)) / len(dark))
        def holds_from(pixel):
            return next((t for t in range(SOUND + GROW, READY, 200) if self.share_lit(frames, pixel, t, t + 600) > .95), READY)
        inner = sorted(holding, key=distance)[:len(holding) // 2]
        outer = sorted(holding, key=distance)[len(holding) // 2:]
        self.assertLess(median(map(holds_from, inner)) + 600, median(map(holds_from, outer)))
        # The held lights stay dim, and the sparkles ahead of the front are brighter.
        held_level = max(f[CENTRE[0]][1] for t, f in before if t > READY - 1000)
        self.assertLess(held_level, 40)
        self.assertGreater(max(f[i][1] for t, f in before if t > SOUND + GROW for i in MOUTH if i not in holding), held_level + 30)
        self.assertTrue(all(len(self.lit(f)) < 70 for t, f in before))     # Not solid before READY.
        # READY: the sparkle runs out to the corners, the lit lights brighten, and exactly as the chest's
        # bars are handed to its ordinary panel (0.9 s) every light is solid, for the 0.6 s of that blend.
        for t, f in frames:
            if READY + 700 <= t < READY + 880:
                self.assertGreaterEqual(len(self.lit(f)), 78, f'lights still out at {t} ms')
                self.assertNotEqual({f[i] for i in MOUTH}, {FULL}, f'solid too soon at {t} ms')
            if READY + 920 <= t < READY + 1480:
                self.assertEqual({f[i] for i in MOUTH}, {FULL}, f'not solid at {t} ms')
        centre = [f[CENTRE[0]][1] for t, f in frames if READY <= t < READY + 920]
        self.assertEqual(centre, sorted(centre))

    def test_a_louder_sound_makes_brighter_sparkles(self):
        def brightest(level):
            frames = self.play(start(levels=((0, level),), ready=False))
            return max(f[i][1] for t, f in frames if SOUND + GROW + 1000 < t for i in MOUTH)
        self.assertGreater(brightest(255), brightest(90) + 10)

    def test_a_host_that_never_says_the_sound_began_gets_the_usual_timing(self):
        frames = self.play(f'cmd STARTUP:{READY} run {READY} cmd READY run 1500')
        self.assertTrue(all(not self.lit(f) for t, f in frames if t < SOUND + WAIT - 40))
        self.assertTrue(any(self.lit(f) for t, f in frames if 4000 <= t < 7000))            # Twinkling, by its own guess,
        self.assertLess(self.mean_lit(frames, 4000, 8000), 4)
        for pixel in CENTRE:
            self.assertGreater(self.share_lit(frames, pixel, READY - 1500, READY), .95)       # and grown from the centre.
        self.assertTrue(all({f[i] for i in MOUTH} == {FULL} for t, f in frames if READY + 920 <= t < READY + 1480))

    def test_the_glow_takes_over_from_the_solid_mouth_without_a_jump(self):
        frames = self.play('cmd STARTUP:6000 run 6000 cmd READY run 6000')
        after = [(t, f) for t, f in frames if t >= 6000 + 1500]
        for pixel in (CENTRE[0], CORNERS[0]):
            levels = [f[pixel][1] for t, f in after]
            self.assertLessEqual(levels[0], FULL[1])
            self.assertTrue(all(abs(a - b) <= 3 for a, b in zip(levels, levels[1:])), 'the mouth jumped')
        settled = [f for t, f in after if t >= 6000 + 1500 + 1600]
        self.assertTrue(all(7 <= f[i][1] <= 20 for f in settled for i in MOUTH))   # The resting ripple: 3 to 8 %.

    def test_refreshes_and_idle_commands_do_not_cut_the_startup_short(self):
        frames = self.play(f'cmd STARTUP:{READY} cmd EYE:255,200,0 run {SOUND} cmd STARTUP_SOUND:{WAIT},{GROW},{END} '
                           'cmd STARTUP_LEVEL:150 run 2400 cmd ACTIVE cmd EYE:255,200,0 cmd IDLE cmd SPEAK_STOP run 4000')
        late = [(t, f) for t, f in frames if t >= 5000]
        counts = [len(self.lit(f)) for t, f in late]
        self.assertTrue(all(count < 8 for count in counts))        # Still a light or two: the start goes on.
        self.assertGreater(len(set(counts)), 2)                    # And still twinkling.

    def test_a_line_spoken_takes_the_mouth_at_once(self):
        frames = self.play(f'cmd STARTUP:{READY} run 4000 cmd SPEAK:neutral cmd SPEAK_LEVEL:220 run 600 '
                           'cmd READY run 600 cmd SPEAK_LEVEL:0 cmd SPEAK_STOP cmd ACTIVE run 1500')
        speaking = [f for t, f in frames if 4300 <= t < 5200]
        self.assertGreater(max(max(f[i]) for f in speaking for i in MOUTH), 90)    # The speaking bars, far brighter.
        end = [f for t, f in frames if t >= 5200 + 1000]
        self.assertTrue(all(len(self.lit(f)) == 80 for f in end))                  # Then the resting mouth, not the startup.
        self.assertTrue(all(f[i][1] <= 20 for f in end for i in MOUTH))

    def test_a_start_from_the_menu_times_itself(self):
        frames = self.play('cmd STARTUP run 15000')
        self.assertTrue(all(not self.lit(f) for t, f in frames if t < SOUND + WAIT - 40))   # The same wait, by its own clock.
        self.assertLess(self.mean_lit(frames, SOUND + WAIT, SOUND + GROW), 4)
        self.assertTrue(all(len(self.lit(f)) < 70 for t, f in frames if t < 12800))
        self.assertTrue(all({f[i] for i in MOUTH} == {FULL} for t, f in frames if 12800 + 920 <= t < 12800 + 1480))
        self.assertTrue(all(f[0] == f[1] == GOLD for t, f in frames if 12800 + 700 <= t < 12800 + 1480))   # Eyes too.

    def test_a_host_that_never_says_ready_is_waited_for_only_so_long(self):
        frames = self.play('cmd STARTUP:5000 run 400 cmd STARTUP_SOUND:500,1500,4000 cmd STARTUP_LEVEL:200 run 16600')
        waiting = [len(self.lit(f)) for t, f in frames if 6000 <= t < 15000]
        self.assertTrue(20 < min(waiting) and max(waiting) < 62, (min(waiting), max(waiting)))   # About half lit, the edges waiting,
        self.assertGreater(len(set(waiting)), 5)                                                 # still sparkling;
        self.assertTrue(all({f[i] for i in MOUTH} == {FULL} for t, f in frames if 15000 + 920 <= t < 15000 + 1480))   # then solid by itself.

    def test_ready_before_the_mouth_has_grown_still_ends_solid(self):
        frames = self.play(f'cmd STARTUP:{READY} run {SOUND} cmd STARTUP_SOUND:{WAIT},{GROW},{END} run 3000 cmd READY run 1500')
        ready = SOUND + 3000
        self.assertTrue(all({f[i] for i in MOUTH} == {FULL} for t, f in frames if ready + 920 <= t < ready + 1480))

    def test_ready_outside_startup_changes_nothing(self):
        frames = self.play('cmd ACTIVE run 5000 cmd READY cmd STARTUP_SOUND:1,2,3 cmd STARTUP_LEVEL:255 run 1000')
        levels = [f[CENTRE[0]][1] for t, f in frames if t >= 4500]
        self.assertTrue(all(abs(a - b) <= 1 for a, b in zip(levels, levels[1:])))

    def test_a_shutdown_fade_still_running_gives_way_to_a_new_start(self):
        frames = self.play('cmd ACTIVE cmd EYE:255,200,0 run 5000 cmd FADEOFF run 1000 cmd STARTUP:6000 '
                           'cmd EYE:255,200,0 run 6000 cmd READY run 1200')
        self.assertEqual(frames[-1][1][2], FULL)                   # Full brightness is back, not the fade's.
        self.assertEqual(frames[-1][1][0], GOLD)


class EyeStartupTests(HeadPlay):
    def test_the_eyes_stay_dark_behind_the_visor_and_blink_on_when_he_is_ready(self):
        frames = self.play('cmd ACTIVE cmd EYE:0,180,255 run 500 cmd STARTUP:6000 cmd EYE:255,200,0 run 3000 '
                           'cmd EYE:255,200,0 cmd ACTIVE run 3000 cmd READY run 1500')
        start = 500
        for t, f in frames:
            if start < t < start + 6000 + 240:
                self.assertEqual((f[0], f[1]), ((0, 0, 0), (0, 0, 0)), f'eyes lit at {t} ms')   # Whatever the host sends.
        def eyes(a, b):
            return [f[0] for t, f in frames if start + 6000 + a <= t < start + 6000 + b]
        first, gap, second, again = eyes(265, 305), eyes(345, 415), eyes(445, 505), eyes(545, 635)
        self.assertTrue(first and all(sum(e) for e in first))              # A first flash,
        self.assertTrue(gap and not any(sum(e) for e in gap))              # dark,
        self.assertTrue(second and all(sum(e) for e in second))            # a brighter one,
        self.assertTrue(again and not any(sum(e) for e in again))          # dark,
        self.assertGreater(second[0][0], first[0][0])
        for e in first + second:
            self.assertTrue(e[0] > e[1] > 0 == e[2], e)                    # In the colour the host asked for, dimmer.
        on = [(f[0], f[1]) for t, f in frames if start + 6000 + 700 <= t]
        self.assertTrue(on and all(pair == (GOLD, GOLD) for pair in on))   # Then on for good, both eyes.

    def test_a_line_spoken_during_startup_brings_the_eyes_on_with_it(self):
        frames = self.play('cmd STARTUP:13400 cmd EYE:255,200,0 run 4000 cmd EYE:0,180,255 cmd SPEAK:neutral '
                           'cmd SPEAK_LEVEL:200 run 500')
        self.assertTrue(all(f[0] == (0, 0, 0) for t, f in frames if t < 4000))
        self.assertTrue(all(f[0] == f[1] == (0, 180, 255) for t, f in frames if t > 4030))

    def test_eyes_open_in_warm_gold_when_no_colour_was_ever_sent(self):
        frames = self.play('cmd STARTUP:4000 run 4000 cmd READY run 1000')
        self.assertEqual(frames[-1][1][0], GOLD)


class RestingMouthTests(HeadPlay):
    def test_the_resting_mouth_is_a_slow_dim_ripple_from_the_centre(self):
        frames = self.play('cmd ACTIVE cmd EYE:255,200,0 run 24000')
        awake = [(t, f) for t, f in frames if t >= 5000]               # After the four-second rise from dark.
        # Dim: every light between 3 and 8 % (it was a 5 to 10 % pulse), in the ordinary amber.
        for t, f in awake:
            for i in MOUTH:
                r, g, b = f[i]
                self.assertTrue(7 <= g <= 20 and g > r > 0 and b == 0, f'{f[i]} at {t} ms')
        # Slow: no light moves by more than one step from one frame to the next.
        for (t, a), (_, b) in zip(awake, awake[1:]):
            self.assertLessEqual(max(abs(a[i][1] - b[i][1]) for i in MOUTH), 1, f'a jump at {t} ms')
        # A ripple, not the whole mouth at once: the centre is brightest first and the corners seconds later.
        def peaks(pixel):
            levels = [(t, f[pixel][1]) for t, f in awake]
            top = max(level for _, level in levels)
            times, last = [], None
            for t, level in levels:
                if level == top and (last is None or t - last > 2000):
                    times.append(t)
                last = t if level == top else last
            return times
        centre, corner = peaks(CENTRE[0]), peaks(CORNERS[0])
        self.assertGreaterEqual(len(centre), 2)
        period = centre[1] - centre[0]
        self.assertTrue(5500 < period < 7200, period)                     # One ripple about every six seconds.
        lag = min((c - centre[0]) % period for c in corner)
        self.assertTrue(2000 < lag < 4800, lag)                           # It reaches the corners seconds after the centre.
        self.assertTrue(any(len({f[i][1] for i in MOUTH}) > 4 for t, f in awake))

    def test_only_speech_is_quick(self):
        frames = self.play('cmd ACTIVE run 6000 cmd SPEAK:neutral cmd SPEAK_LEVEL:200 run 1000 '
                           'cmd SPEAK_LEVEL:0 cmd SPEAK_STOP cmd ACTIVE run 2000')
        def biggest_step(start, end):
            window = [f for t, f in frames if start <= t < end]
            return max(abs(a[i][1] - b[i][1]) for a, b in zip(window, window[1:]) for i in MOUTH)
        self.assertLessEqual(biggest_step(5000, 6000), 1)
        self.assertGreater(biggest_step(6100, 7000), 10)                  # The speaking bars jump about.
        self.assertGreater(max(f[i][1] for t, f in frames if 6100 <= t < 7000 for i in MOUTH), 60)
        self.assertLessEqual(max(f[i][1] for t, f in frames if t >= 8000 for i in MOUTH), 20)   # Back to the dim ripple.


class WinkTests(HeadPlay):
    def wink(self, command, eye):
        frames = self.play(f'cmd ACTIVE cmd EYE:255,200,0 run 1000 cmd {command} run 1500')
        other = 1 - eye
        during = [f for t, f in frames if 1000 < t <= 1000 + 430]
        self.assertTrue(during)
        self.assertTrue(all(f[eye] == (0, 0, 0) for f in during))         # One eye dark for a short time,
        self.assertTrue(all(f[other] == GOLD for f in during))            # the other still lit;
        after = [f for t, f in frames if t >= 1000 + 480]
        self.assertTrue(after and all(f[eye] == f[other] == GOLD for f in after))   # then both as they were.
        self.assertTrue(all(sum(f[i]) for f in during for i in MOUTH))    # The mouth is not part of it.

    def test_a_wink_darkens_one_eye_for_a_short_time(self):
        self.wink('WINK', 1)

    def test_either_eye_can_wink(self):
        self.wink('WINK:R', 1)
        self.wink('WINK:L', 0)

    def test_a_wink_keeps_the_colour_set_meanwhile(self):
        frames = self.play('cmd ACTIVE cmd EYE:255,200,0 run 1000 cmd WINK run 200 cmd EYE:0,180,255 run 1000')
        during = [f for t, f in frames if 1200 < t <= 1430]
        self.assertTrue(all(f[1] == (0, 0, 0) and f[0] == (0, 180, 255) for f in during))   # The other eye takes the colour;
        self.assertEqual(frames[-1][1][1], (0, 180, 255))                                   # the winking one opens in it.

    def test_no_wink_while_the_eyes_are_dark(self):
        for script in ('cmd OFF run 500 cmd WINK run 1000',
                       'cmd STARTUP:6000 cmd EYE:255,200,0 run 2000 cmd WINK run 1000'):
            frames = self.play(script)
            self.assertTrue(all(f[0] == f[1] == (0, 0, 0) for t, f in frames))
