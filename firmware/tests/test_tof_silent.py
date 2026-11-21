"""A ring sensor that stops answering must not hold up the others (November 20, 2026).

Compiles the firmware's own tof_silent.h and tof_fr_filter.h and runs the ring's round the way
tof.cpp does: one short and one long sensor each 20 ms tick, a read that gets no answer costing
the read timeout. No I/O."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

SOURCE = r'''
#include <iostream>
#include <string>
#include "calib.h"
#include "tof_fr_filter.h"
#include "tof_silent.h"

// argv: mode ("steps" | "ring"), then for ring: backoff(0/1) seconds start_ms, and on stdin one
// line per sensor 0..7: the time it stops answering and the time it answers again (-1 never).
int main(int, char** argv) {
  const std::string mode = argv[1];
  if (mode == "steps") {            // t answered offset -> asked back
    TofSilent s; uint32_t t; int answered; uint32_t offset;
    while (std::cin >> t >> answered >> offset) {
      const bool ask = tof_silent_ask(s, t);
      const bool back = ask ? tof_silent_note(s, answered, t, offset) : false;
      std::cout << ask << ' ' << back << ' ' << tof_silent(s) << '\n';
    }
    return 0;
  }
  const bool backoff = std::stoi(argv[2]);
  const uint32_t end = (uint32_t)std::stoul(argv[3]) * 1000u, start = (uint32_t)std::stoul(argv[4]);
  long long stops[8], returns[8];
  for (int i = 0; i < 8; i++) std::cin >> stops[i] >> returns[i];
  TofSilent silent[8]; TofFrHealth health;
  uint32_t now = start; int next = 0;
  auto answers = [&](int i, uint32_t at) {
    const long long t = (long long)(uint32_t)(at - start);
    return !(stops[i] >= 0 && t >= stops[i] && (returns[i] < 0 || t < returns[i]));
  };
  while ((uint32_t)(now - start) < end) {
    for (int i : {next, 4 + next}) {            // hal_read_tof: one short, one long per call
      const uint32_t offset = (uint32_t)i * 137u;
      if (backoff && !tof_silent_ask(silent[i], now)) continue;
      const bool answered = answers(i, now);
      if (!answered) now += TOF_TIMEOUT_MS;     // the blocking read waits out its timeout
      const bool back = tof_silent_note(silent[i], answered, now, offset);
      std::cout << (uint32_t)(now - start) << ' ' << i << ' ' << answered << ' ' << back;
      if (i == 5) std::cout << ' ' << tof_fr_health_step(health, answered, now);
      std::cout << '\n';
    }
    next = (next + 1) % 4;
    now += 20;                                  // the 50 Hz sensor task
  }
}
'''


class SilentSensorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='rex-tof-silent-')
        cls.addClassCleanup(cls.temp.cleanup)
        source = Path(cls.temp.name) / 'silent.cpp'
        cls.binary = Path(cls.temp.name) / 'silent'
        source.write_text(SOURCE)
        subprocess.run(['clang++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-I', str(ROOT / 'djr3x_motion'),
                        str(source), '-o', str(cls.binary)], check=True, capture_output=True, text=True)

    def steps(self, rows):
        done = subprocess.run([str(self.binary), 'steps'], input=''.join(f'{t & 0xffffffff} {int(a)} {o}\n' for t, a, o in rows),
                              text=True, capture_output=True, check=True)
        return [tuple(map(int, line.split())) for line in done.stdout.splitlines()]

    def ring(self, sensors, *, backoff=True, seconds=20, start=0):
        """sensors: {index: (stops_ms, returns_ms or -1)}. Rows of (ms, sensor, answered, back[, fr healthy])."""
        table = ''.join('{} {}\n'.format(*sensors.get(i, (-1, -1))) for i in range(8))
        done = subprocess.run([str(self.binary), 'ring', str(int(backoff)), str(seconds), str(start)],
                              input=table, text=True, capture_output=True, check=True)
        return [tuple(map(int, line.split())) for line in done.stdout.splitlines()]

    @staticmethod
    def gaps(rows, sensor):
        times = [row[0] for row in rows if row[1] == sensor]
        return [b - a for a, b in zip(times, times[1:])]

    def test_a_sensor_that_answers_is_asked_every_round(self):
        out = self.steps([(i * 80, True, 0) for i in range(100)])
        self.assertEqual(set(out), {(1, 0, 0)})

    def test_a_few_missed_reads_change_nothing(self):
        pattern = [True] * 5 + [False] * 7 + [True] * 5
        out = self.steps([(i * 80, a, 0) for i, a in enumerate(pattern)])
        self.assertEqual([row[0] for row in out], [1] * len(pattern))        # Asked every round throughout...
        self.assertEqual({row[1:] for row in out}, {(0, 0)})                 # ...never called silent, never "back".

    def test_after_eight_misses_it_is_tried_every_two_seconds_and_returns_on_its_first_answer(self):
        rows = [(i * 80, False, 0) for i in range(8)]                        # 0..560 ms: the eighth miss at 560.
        rows += [(640 + i * 80, False, 0) for i in range(100)]               # Eight more seconds of rounds.
        out = self.steps(rows)
        self.assertEqual(out[7], (1, 0, 1))
        asked = [rows[i][0] for i, row in enumerate(out) if row[0] and i >= 8]
        self.assertEqual(asked, [2560, 4560, 6560, 8560])                    # 560 + 2000, then every 2000.
        back = self.steps(rows[:60] + [(rows[60][0] + 80 * i, True, 0) for i in range(40)])
        first = next(i for i, row in enumerate(back) if row[1])
        self.assertEqual(back[first], (1, 1, 0))                             # Answered at its next try: back, no longer silent.
        self.assertEqual({row for row in back[first + 1:]}, {(1, 0, 0)})     # And asked every round again.

    def test_the_clock_wrapping_does_not_strand_a_silent_sensor(self):
        base = 0xffffffff - 3000
        rows = [(base + i * 80, False, 0) for i in range(120)]               # Crosses the 32-bit wrap.
        asked = [i for i, row in enumerate(self.steps(rows)) if row[0]]
        self.assertEqual(asked[:8], list(range(8)))
        spacing = [(b - a) * 80 for a, b in zip(asked[7:], asked[8:])]
        self.assertTrue(spacing and all(s == 2000 for s in spacing), spacing)

    def test_two_silent_rear_sensors_no_longer_slow_the_round_for_the_rest(self):
        # November 20: rear-left (6) and rear-right (7) silent; every sensor was read each ~230 ms.
        silent = {6: (0, -1), 7: (0, -1)}
        before = self.ring(silent, backoff=False)
        self.assertGreaterEqual(sorted(self.gaps(before, 5))[len(self.gaps(before, 5)) // 2], 250)
        after = self.ring(silent)
        for sensor in (0, 1, 2, 3, 4, 5):
            gaps = self.gaps(after, sensor)[20:]                             # Once both are known silent.
            self.assertEqual(sorted(gaps)[len(gaps) // 2], 80, sensor)
            self.assertLessEqual(max(gaps), 180, sensor)                     # A retry adds one timeout, never two.
        for sensor in (6, 7):
            tries = self.gaps(after, sensor)[12:]
            self.assertTrue(tries and all(2000 <= gap <= 2200 for gap in tries), (sensor, tries))

    def test_the_front_right_stays_published_beside_silent_sensors_with_or_without_the_backoff(self):
        for backoff in (False, True):
            with self.subTest(backoff=backoff):
                rows = self.ring({6: (0, -1), 7: (0, -1)}, backoff=backoff)
                healthy = [row[4] for row in rows if row[1] == 5]
                self.assertGreater(len(healthy), 60)
                self.assertEqual(set(healthy), {1})      # 0.2.20: quarantined from the first slow round on.

    def test_a_sensor_that_comes_back_is_noticed_and_the_round_returns_to_normal(self):
        rows = self.ring({6: (1000, 9000)}, seconds=14)
        returned = [row for row in rows if row[1] == 6 and row[3]]
        self.assertEqual(len(returned), 1)
        self.assertLessEqual(returned[0][0] - 9000, 2000 + 100)              # Within one retry of answering again.
        times = [r[0] for r in rows if r[1] == 6 and r[0] > returned[0][0]]
        self.assertEqual({b - a for a, b in zip(times, times[1:])}, {80})

    def test_a_missing_multiplexer_silences_all_eight_without_stalling_the_task(self):
        rows = self.ring({i: (0, -1) for i in range(8)}, seconds=30)
        late = [row for row in rows if row[0] > 5000]
        tries = len(late)
        self.assertLessEqual(tries, 8 * 13 + 8)          # About one try per sensor every 2 s.
        spent = tries * 100 / 25000                      # Share of the task's time waiting on timeouts.
        self.assertLess(spent, .5)


if __name__ == '__main__':
    unittest.main()
