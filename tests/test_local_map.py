"""Short-term memory of the floor around Rex; simulated base and synthetic frames only."""
import unittest

from r3x.contracts import Cancellation
from r3x.drive import DriveStep, RECHECK_S
from r3x.local_map import LocalMap, MEMORY_S, RECENT_FREE_S
from tests.test_drive import base_setup, plan, settle


def frame(now, front_mm=3500, left_mm=3000, ahead=None):
    """Telemetry plus a uniform depth matrix: ahead (m) puts a wall that close in every zone."""
    tof = {'fl_radial': front_mm, 'fr_radial': front_mm, 'rl': 2000, 'rr': 2000,
           'lf': left_mm, 'lb': left_mm, 'rf': 1500, 'rb': 1500}
    mm = int(ahead * 1000) if ahead is not None else 3500
    return ({'received_at': now, 'tof_mm': tof},
            {'received_at': now, 'grid': [mm] * 64, 'rej': [4095] * 8})


class LocalMapRules(unittest.TestCase):
    def test_a_sudden_return_on_floor_just_seen_open_is_doubtful(self):
        memory = LocalMap(lambda: 0)
        for k in range(10):
            memory.observe(*frame(100 + k * .2), (0., 0.), 0., 100 + k * .2)
        self.assertTrue(memory.doubtful((0., 0.), 0., 0., .2, 102.2))
        # Something that was there all along is not doubtful.
        for k in range(10):
            memory.observe(*frame(103 + k * .2, ahead=.5), (0., 0.), 0., 103 + k * .2)
        self.assertFalse(memory.doubtful((0., 0.), 0., 0., .5, 105.2))
        # October 17: the trace can say why (a return was remembered there).
        self.assertTrue(memory.last_check['known'])
        self.assertGreater(memory.last_check['hits'], 0)

    def test_nothing_seen_is_not_evidence_and_memory_fades(self):
        memory = LocalMap(lambda: 0)
        self.assertFalse(memory.doubtful((0., 0.), 0., 0., .2, 100))
        for k in range(10):
            memory.observe(*frame(100 + k * .2), (0., 0.), 0., 100 + k * .2)
        self.assertFalse(memory.doubtful((0., 0.), 0., 0., .2, 102 + RECENT_FREE_S + 1))
        memory.observe(*frame(200 + MEMORY_S), (5., 5.), 0., 200 + MEMORY_S)
        self.assertNotIn(memory.key(.6, 0.), memory.cells)   # Old floor forgotten.

    def test_a_way_around_comes_from_floor_seen_open_while_turning(self):
        memory = LocalMap(lambda: 0)
        at = 100.
        for heading in range(0, 61, 5):                  # He swung left, seeing open floor.
            at += .2
            memory.observe(*frame(at), (0., 0.), float(heading), at)
        for k in range(5):                               # Facing the target again: blocked ahead.
            at += .2
            memory.observe(*frame(at, front_mm=400, ahead=.4), (0., 0.), 0., at)
        way = memory.open_way((0., 0.), 0., 0., .8, at)
        self.assertIsNotNone(way)
        self.assertGreaterEqual(way, 20.)                # Left, around the thing ahead.
        self.assertIsNone(LocalMap(lambda: 0).open_way((0., 0.), 0., 0., .8, at))   # Unknown floor is not open.


class RecheckDriveTests(unittest.TestCase):
    def start(self):
        self.clock, self.fake, self.drive, self.world = base_setup(self)
        self.rows = []
        self.drive.trace = type('T', (), {'emit': lambda _, event, **f: self.rows.append({'event': event, **f})})()
        self.drive.start()
        self.addCleanup(lambda: self.drive.state != 'closed' and self.drive.close())
        for _ in range(12):                              # He has been looking at open floor.
            settle(); self.drive.space(); self.clock.wait(.2)

    def rechecks(self):
        return [r['outcome'] for r in self.rows if r['event'] == 'drive_reading_rechecked']

    def test_a_phantom_return_is_rechecked_and_the_move_goes_ahead(self):
        self.start()
        gone = self.clock.now + RECHECK_S / 2
        self.fake.front = lambda: .2 if self.clock.now < gone else None
        settle()
        receipt = self.drive.execute(plan(DriveStep('move', meters=.8)), Cancellation())
        self.assertEqual(receipt.status, 'completed')
        self.assertAlmostEqual(self.fake.x, .8, places=1)
        self.assertEqual(self.rechecks(), ['holding', 'cleared'])

    def test_something_that_stays_still_stops_him(self):
        self.start()
        self.fake.front = lambda: .2
        settle()
        receipt = self.drive.execute(plan(DriveStep('move', meters=.8)), Cancellation())
        self.assertNotEqual(receipt.status, 'completed')
        self.assertAlmostEqual(self.fake.x, 0., places=2)
        self.assertEqual(self.rechecks(), ['holding', 'persisted'])

    def test_an_obstacle_already_there_is_not_second_guessed(self):
        self.clock, self.fake, self.drive, self.world = base_setup(self)
        self.rows = []
        self.drive.trace = type('T', (), {'emit': lambda _, event, **f: self.rows.append({'event': event, **f})})()
        self.fake.front = lambda: .2
        self.drive.start()
        self.addCleanup(lambda: self.drive.state != 'closed' and self.drive.close())
        for _ in range(12):
            settle(); self.drive.space(); self.clock.wait(.2)
        self.drive.execute(plan(DriveStep('move', meters=.8)), Cancellation())
        self.assertEqual(self.rechecks(), [])
        checked = [r for r in self.rows if r['event'] == 'drive_doubtful_checked']
        self.assertTrue(checked and checked[-1]['hits'] > 0 and checked[-1]['known'])   # And the log says why.


if __name__ == '__main__':
    unittest.main()
