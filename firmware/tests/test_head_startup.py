"""Run the LED board's head code on the Mac against the stand-in LED library and read back the mouth
and eyes: at startup the mouth dark for two seconds of the sound, a light or two twinkling with its
loudness, then the sparkle spreading from the centre of the mouth, solid as the chest lands on its
ordinary panel; the eyes dark behind the visor until they blink on; and the wink (owner, November 21,
2026). The resting mouth, dark at its edges and brighter towards the middle, and the talking bars, measured
against how loud the voice has lately been and rolling out from the middle (owner, November 24, 2026). No
board, no serial.

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


def light_at(row, col):
    """Strip index of the mouth light in a row (0 the top) and a column (0 the left)."""
    return 2 + row * 8 + (7 - col if row & 1 else col)


RING = [light_at(row, col) for row in range(10) for col in range(8) if row in (0, 9) or col in (0, 7)]   # The outermost lights.


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
        self.assertTrue(all(10 <= f[i][1] <= 22 for f in settled for i in CENTRE))   # The resting glow: its dim middle,
        self.assertTrue(all(f[i] == (0, 0, 0) for f in settled for i in RING))       # and its dark edges.

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
        self.assertTrue(all(sum(f[i]) for f in end for i in CENTRE))               # Then the resting mouth, not the startup:
        self.assertTrue(all(not sum(f[i]) for f in end for i in RING))             # lit in the middle, dark at the edges.
        self.assertTrue(all(f[i][1] <= 22 for f in end for i in MOUTH))

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
    def test_the_resting_mouth_is_dark_at_the_edges_and_brighter_towards_the_middle(self):
        # Owner, November 24: "the edges are dark and it is brighter towards the middle, keeping the whole
        # not-talking animation dark". It was a ripple over all eighty lights, each between 3 and 8 %.
        frames = self.play('cmd ACTIVE cmd EYE:255,200,0 run 24000')
        awake = [(t, f) for t, f in frames if t >= 5000]               # After the four-second rise from dark.
        levels = {i: [f[i][1] for t, f in awake] for i in MOUTH}
        # Dark at the edges: no light of the outermost ring is ever lit, and under half the mouth is.
        self.assertTrue(all(max(levels[i]) == 0 for i in RING))
        lit = [i for i in MOUTH if max(levels[i])]
        self.assertTrue(20 <= len(lit) <= 40, len(lit))
        self.assertTrue(all(min(levels[i]) >= 2 for i in lit))         # A lit light stays lit, and keeps its colour:
        for t, f in awake:
            for i in lit:
                r, g, b = f[i]
                self.assertTrue(g > r > 0 and b == 0, f'{f[i]} at {t} ms')   # the ordinary amber.
        # Brighter towards the middle: the four middle lights are the brightest, and a light further out
        # is never brighter than one nearer in. The glow is as wide as it is tall, measured in lights.
        def out(pixel):
            row, place = divmod(pixel - 2, 8)
            col = 7 - place if row & 1 else place
            return sqrt(((col - 3.5) / 3.5) ** 2 + ((row - 4.5) / 3.6) ** 2)
        mean = {i: sum(levels[i]) / len(levels[i]) for i in MOUTH}
        ordered = sorted(MOUTH, key=out)
        self.assertEqual(set(ordered[:4]), set(CENTRE))
        for near, far in zip(ordered, ordered[1:]):
            if out(far) > out(near) + .01:
                self.assertLessEqual(mean[far], mean[near] + .01, (near, far))
        self.assertGreater(mean[CENTRE[0]], 4 * min(mean[i] for i in lit))
        self.assertEqual(sum(1 for i in lit if (i - 2) // 8 in (4, 5)), 12)    # Six lights across the middle rows,
        self.assertEqual(sum(1 for i in lit if light_at((i - 2) // 8, 3) == i), 8)   # eight down the middle columns.
        # Owner, November 24 (0.9.3): "very dim ... bring up the brightness on the edges a little". Lights two
        # out from the middle hold over a third of the middle's brightness (they had a quarter), the middle unchanged.
        self.assertGreater(mean[light_at(4, 1)], .4 * mean[CENTRE[0]])
        self.assertGreater(mean[light_at(2, 3)], .4 * mean[CENTRE[0]])
        # Kept dim: the middle never passes 8.5 % (22 of 255; it reaches 7.5), and the whole mouth gives well under half the light
        # it did (eighty lights at 14 of 255 on average).
        self.assertTrue(10 <= min(levels[CENTRE[0]]) and max(levels[CENTRE[0]]) <= 22, (min(levels[CENTRE[0]]), max(levels[CENTRE[0]])))
        self.assertLess(max(sum(f[i][1] for i in MOUTH) for t, f in awake), .4 * 80 * 14)
        # Slow: no light moves by more than one step from one frame to the next.
        for (t, a), (_, b) in zip(awake, awake[1:]):
            self.assertLessEqual(max(abs(a[i][1] - b[i][1]) for i in MOUTH), 1, f'a jump at {t} ms')
        # It still breathes, about once every six seconds, each breath spreading from the middle outwards.
        def peaks(pixel):
            """The middle of each stretch of time the light spends at its brightest."""
            top = max(levels[pixel])
            stretches = []
            for (t, f), level in zip(awake, levels[pixel]):
                if level == top:
                    if stretches and t - stretches[-1][1] < 2000:
                        stretches[-1][1] = t
                    else:
                        stretches.append([t, t])
            return [(a + b) / 2 for a, b in stretches if a > awake[0][0] and b < awake[-1][0]]
        centre = peaks(CENTRE[0])
        self.assertGreaterEqual(len(centre), 2)
        period = centre[1] - centre[0]
        self.assertTrue(5500 < period < 7200, period)
        further = light_at(3, 2)                                          # Lit, two lights out from the middle.
        self.assertIn(further, lit)
        lag = min((t - centre[0]) % period for t in peaks(further))
        self.assertTrue(250 < lag < 1500, lag)
        self.assertGreater(max(levels[CENTRE[0]]) - min(levels[CENTRE[0]]), 5)

    def test_every_feeling_rests_with_dark_edges(self):
        for feeling in ('happy', 'sad', 'angry', 'surprised'):
            with self.subTest(feeling=feeling):
                frames = self.play(f'cmd ACTIVE run 5000 cmd SPEAK:{feeling} cmd SPEAK_LEVEL:0 cmd SPEAK_STOP cmd ACTIVE run 3000')
                rest = [f for t, f in frames if t >= 6500]
                self.assertTrue(all(sum(f[i]) == 0 for f in rest for i in RING))
                self.assertTrue(all(sum(f[i]) > 0 for f in rest for i in CENTRE))

    def test_only_speech_is_quick(self):
        frames = self.play('cmd ACTIVE run 6000 cmd SPEAK:neutral cmd SPEAK_LEVEL:200 run 1000 '
                           'cmd SPEAK_LEVEL:0 cmd SPEAK_STOP cmd ACTIVE run 2000')
        def biggest_step(start, end):
            window = [f for t, f in frames if start <= t < end]
            return max(abs(a[i][1] - b[i][1]) for a, b in zip(window, window[1:]) for i in MOUTH)
        self.assertLessEqual(biggest_step(5000, 6000), 1)
        self.assertGreater(biggest_step(6100, 7000), 10)                  # The speaking bars jump about.
        self.assertGreater(max(f[i][1] for t, f in frames if 6100 <= t < 7000 for i in MOUTH), 60)
        self.assertLessEqual(max(f[i][1] for t, f in frames if t >= 8000 for i in MOUTH), 22)   # Back to the dim glow.


AWAKE = 'cmd ACTIVE cmd EYE:255,200,0 run 6000 '     # The resting mouth, risen from dark.


def bar(frame, col):
    """How many half-rows of a column's bar are lit, counted out from the middle pair of rows (0 to 5).
    The dim line of a shut mouth does not count, nor does a peak dot floating above the bar."""
    height = 0
    for step in range(5):
        if frame[light_at(4 - step, col)][1] <= (30 if step == 0 else 8):
            break
        height += 1
    return height


def voice(peak, seconds=5):
    """A voice as the host reports it: a new level every tenth of a second, each sound at its loudest,
    then half of that, then trailing off, with a short gap."""
    script = AWAKE + 'cmd SPEAK:neutral '
    for _ in range(seconds * 2):
        for share in (1, .5, .7, .35, .12):
            script += f'cmd SPEAK_LEVEL:{int(peak * share)} run 100 '
    return script


class SpeakingMouthTests(HeadPlay):
    """Owner, November 24: "sometimes it's got this cool pattern and other times it's not as cool". The bars
    followed the level against the top of its range, and his voice sits far below that (half the voiced
    moments of 151 cached lines under level 48), so they stood tall only in the loudest lines."""

    def test_a_quiet_line_fills_the_mouth_like_a_loud_one(self):
        def shape(peak):
            frames = [f for t, f in self.play(voice(peak)) if t >= 6000 + 2000]
            tallest = [max(bar(f, col) for col in range(8)) for f in frames]
            lit = [len(self.lit(f)) for f in frames]
            return max(tallest), sum(tallest) / len(tallest), sum(lit) / len(lit)
        quiet, loud = shape(95), shape(230)          # His quietest and loudest cached lines peak at 91 and 230.
        for top, mean, lit in (quiet, loud):
            self.assertGreaterEqual(top, 4)          # The tall bars show in both. (At 95 they stopped at two.)
            self.assertGreater(mean, 2)
            self.assertGreater(lit, 22)
        self.assertLess(abs(quiet[1] - loud[1]), .8)
        self.assertLess(abs(quiet[2] - loud[2]), 9)

    def test_silence_shuts_the_mouth_to_a_thin_line_brightest_in_the_middle(self):
        frames = self.play(voice(160, seconds=2) + 'cmd SPEAK_LEVEL:0 run 400 cmd SPEAK_LEVEL:0 run 400 cmd SPEAK_LEVEL:0 run 400')
        shut = [f for t, f in frames if t >= 6000 + 2000 + 900]
        line = [light_at(row, col) for row in (4, 5) for col in range(8)]
        for f in shut:
            self.assertEqual(sorted(self.lit(f)), sorted(line))
            self.assertTrue(f[light_at(4, 0)][1] < f[light_at(4, 3)][1] <= 30)
            self.assertTrue(f[light_at(4, 7)][1] < f[light_at(4, 4)][1])

    def test_room_hiss_is_not_blown_up_into_speech(self):
        # What counts as loud never falls under level 77, however long nothing louder is heard, and under
        # level 13 is silence: a steady hiss (a call's open line) does not open the mouth.
        for level, tallest in ((12, 0), (16, 1)):
            script = AWAKE + 'cmd SPEAK:neutral ' + f'cmd SPEAK_LEVEL:{level} run 400 ' * 40
            late = [f for t, f in self.play(script) if t >= 6000 + 10000]
            self.assertLessEqual(max(bar(f, col) for f in late for col in range(8)), tallest)
            self.assertEqual({(i - 2) // 8 for f in late for i in self.lit(f)}, {4, 5})   # Only the middle pair of rows.

    def test_each_sound_opens_the_middle_first_and_rolls_out_to_the_corners(self):
        script = AWAKE + 'cmd SPEAK:neutral cmd SPEAK_LEVEL:0 run 500 ' + 'cmd SPEAK_LEVEL:200 run 100 ' * 6 + 'cmd SPEAK_LEVEL:0 run 400 cmd SPEAK_LEVEL:0 run 400'
        frames = self.play(script)
        def opens(col):                              # When the column's bar first stands above the shut line.
            return next(t for t, f in frames if t > 6500 and bar(f, col) >= 1)
        left, right = [opens(col) for col in (3, 2, 1, 0)], [opens(col) for col in (4, 5, 6, 7)]
        self.assertEqual(left, sorted(left))
        self.assertEqual(right, sorted(right))
        for side in (left, right):
            self.assertTrue(100 <= side[-1] - side[0] <= 320, side)       # About 60 ms a column.
        self.assertLess(opens(3) - 6500, 80)                              # The middle answers the voice at once.
        # And the sound's end leaves the middle first: the middle bars are falling while the corners still stand.
        def tallest_at(col):
            heights = [(f[light_at(3, col)][1] + f[light_at(2, col)][1] + f[light_at(1, col)][1], t) for t, f in frames if 6500 < t < 7600]
            return max(heights)[1]
        self.assertTrue(any(bar(f, 3) < bar(f, 0) or (bar(f, 3) == 0 and bar(f, 1) > 0) for t, f in frames if 7100 < t < 7500))

    def test_a_bar_is_brightest_at_the_middle_and_dimmer_towards_its_tip(self):
        frames = [f for t, f in self.play(AWAKE + 'cmd SPEAK:neutral ' + 'cmd SPEAK_LEVEL:210 run 100 ' * 40) if t >= 7000]
        full = [f for f in frames if bar(f, 3) == 5]
        self.assertGreater(len(full), 5)
        for f in full:
            inner, tip = f[light_at(4, 3)][1], f[light_at(0, 3)][1]
            self.assertGreater(f[light_at(4, 3)][1], f[light_at(2, 3)][1])
            self.assertGreaterEqual(f[light_at(2, 3)][1], f[light_at(1, 3)][1])
            self.assertEqual(f[light_at(4, 3)], f[light_at(5, 3)])        # The same above and below the middle.
            self.assertEqual(f[light_at(1, 3)], f[light_at(8, 3)])
        self.assertTrue(any(f[light_at(4, 3)][1] > 2.5 * f[light_at(0, 3)][1] for f in full))
        self.assertLessEqual(max(max(f[i]) for f in frames for i in MOUTH), 115)   # Never over 45 %.
        # The bars still dance apart: neighbours are often of different heights.
        self.assertGreater(sum(1 for f in frames if bar(f, 3) != bar(f, 4)) / len(frames), .3)
        # A louder voice gives brighter bars as well as taller ones.
        def middle(level):
            late = [f for t, f in self.play(AWAKE + 'cmd SPEAK:neutral cmd SPEAK_LEVEL:255 run 300 ' + f'cmd SPEAK_LEVEL:{level} run 100 ' * 20) if t >= 7300]
            return max(f[light_at(4, 3)][1] for f in late)
        self.assertGreater(middle(255), middle(60) + 15)

    def test_the_glow_fades_out_under_the_first_bars_and_the_last_bars_close_into_it(self):
        frames = self.play(AWAKE + 'cmd SPEAK:neutral cmd SPEAK_LEVEL:0 run 600 ' + 'cmd SPEAK_LEVEL:220 run 100 ' * 12
                           + 'cmd SPEAK_STOP cmd ACTIVE run 1500')
        above = light_at(2, 3)                       # Lit by the resting glow, off the shut line.
        before = [f for t, f in frames if t <= 6000][-1][above][1]
        opening = [(t, f[above][1]) for t, f in frames if 6000 < t <= 6400]
        self.assertGreater(before, 2)
        self.assertGreater(opening[0][1], 0)                              # It does not vanish as the line begins:
        self.assertEqual([level for t, level in opening], sorted((level for t, level in opening), reverse=True))
        gone = next(t for t, level in opening if level == 0) - 6000
        self.assertTrue(100 <= gone <= 260, gone)                         # It is gone in a fifth of a second.
        # The line ends with the bars standing: they close into the glow over a third of a second.
        stop = 6000 + 600 + 1200
        top = max(RING, key=lambda i: [f for t, f in frames if t <= stop][-1][i][1])
        standing = [f for t, f in frames if t <= stop][-1][top][1]
        self.assertGreater(standing, 20)
        closing = [(t, f[top][1]) for t, f in frames if stop < t <= stop + 600]
        self.assertGreater(closing[0][1], .7 * standing)                  # No snap from the bars to the glow:
        self.assertEqual([level for t, level in closing], sorted((level for t, level in closing), reverse=True))
        gone = next(t for t, level in closing if level == 0)
        self.assertTrue(250 <= gone - stop <= 420, gone - stop)
        rest = [f for t, f in frames if t >= stop + 600]
        self.assertTrue(all(sum(f[i]) == 0 for f in rest for i in RING))  # Then the resting mouth again.
        self.assertTrue(all(10 <= f[i][1] <= 22 for f in rest for i in CENTRE))

    def test_a_line_whose_stop_is_lost_still_closes_into_the_glow(self):
        frames = self.play(AWAKE + 'cmd SPEAK:neutral ' + 'cmd SPEAK_LEVEL:220 run 100 ' * 8 + 'run 3000')
        rest = [f for t, f in frames if t >= 6000 + 800 + 1500 + 600]     # Nothing heard for a second and a half.
        self.assertTrue(rest)
        self.assertTrue(all(sum(f[i]) == 0 for f in rest for i in RING))
        self.assertTrue(all(10 <= f[i][1] <= 22 for f in rest for i in CENTRE))


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
        # The mouth is not part of it: it shows what it would have shown without the wink.
        plain = self.play('cmd ACTIVE cmd EYE:255,200,0 run 2500')
        for t, f in frames:
            if 1000 < t <= 1000 + 430:
                self.assertEqual(f[2:], [g for u, g in plain if u <= t][-1][2:], f'the mouth changed at {t} ms')
        self.assertTrue(all(sum(f[i]) for f in during for i in CENTRE))

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
