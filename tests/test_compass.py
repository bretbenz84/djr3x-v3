"""The compass (owner, November 25: extracted from V2's hardware/compass.py and tools/compass_calibrate.py).

Synthetic readings only: a field turned through a circle, shifted and squashed the way the robot's own iron
does, with the gyro's yaw beside it. No sensor, serial port or wheels. They prove the arithmetic, not that
this robot's sensor reads true in this house.
"""
import math
import tempfile
import unittest
from pathlib import Path

from r3x import compass
from r3x.compass import Calibration, ang_diff, compute_calibration, coverage, fit, heading, tilt_compensated_heading

OFFSET, SQUASH, FIELD = (-80., -1900., -1400.), (1.2, .8), 230.


def reading(true_heading, mirrored=False, current=30, yaw0=0., z=-1000., declination=compass.DECLINATION_DEG):
    """Telemetry as the board sends it when he faces true_heading (clockwise from north)."""
    magnetic = math.radians(true_heading - declination)
    mx, my = FIELD * math.cos(magnetic), -FIELD * math.sin(magnetic)
    if mirrored:
        my = -my
    return {'type': 'telemetry', 'batt_ma': current,
            'mag': {'ok': True, 'x': round(mx / SQUASH[0] + OFFSET[0]), 'y': round(my / SQUASH[1] + OFFSET[1]),
                    'z': round(z + OFFSET[2] + (true_heading % 7) * .3)},
            'imu': {'ok': True, 'pitch': 0., 'roll': 0., 'yaw': (yaw0 - true_heading + 180.) % 360. - 180.}}


def sweep(start=0., mirrored=False, steps=72, motors_between=False):
    """A full left turn in steps (a left turn takes the heading down), ending facing `start` again."""
    rows = []
    for i in range(steps + 1):
        h = (start - 360. * i / steps) % 360.
        rows.append(reading(h, mirrored))
        if motors_between:
            # The step itself: motors working, the field bent by their current.
            rows.append({**reading(h, mirrored, current=3200), 'mag': {'ok': True, 'x': 900, 'y': 900, 'z': 900}})
    return rows


class TiltTests(unittest.TestCase):
    """V2's projection, kept as it was."""
    def test_level_headings(self):
        for h in (0., 45., 90., 180., 270., 315.):
            mx, my = math.cos(math.radians(h)), -math.sin(math.radians(h))
            self.assertAlmostEqual(ang_diff(tilt_compensated_heading(mx, my, .3, 0., 0.), h), 0., places=5)

    def test_tilted_headings_come_back(self):
        p, r = math.radians(18.), math.radians(-12.)
        sp, cp, sr, cr = math.sin(p), math.cos(p), math.sin(r), math.cos(r)
        rows = ((cp, 0., sp), (sr * sp, cr, -sr * cp), (-cr * sp, sr, cr * cp))
        for h in (0., 77., 191., 333.):
            earth = (math.cos(math.radians(h)), -math.sin(math.radians(h)), .3)
            body = [sum(rows[i][j] * earth[i] for i in range(3)) for j in range(3)]
            self.assertAlmostEqual(ang_diff(tilt_compensated_heading(*body, p, r), h), 0., places=5)


class CalibrationTests(unittest.TestCase):
    def test_a_full_sweep_recovers_the_robots_own_iron(self):
        samples = []
        for i in range(36):
            for j in range(18):
                th, ph = math.radians(i * 10), math.radians(-85 + j * 10)
                samples.append((500 * math.cos(th) * math.cos(ph) + 100, 500 * math.sin(th) * math.cos(ph) - 200,
                                250 * math.sin(ph) + 50))
        cal = compute_calibration(samples)
        for got, want in zip(cal.offset, (100., -200., 50.)):
            self.assertAlmostEqual(got, want, delta=5.)
        self.assertAlmostEqual(cal.scale[2] / cal.scale[0], 2., delta=.1)
        self.assertFalse(cal.flat)

    def test_a_flat_turn_leaves_the_vertical_axis_alone(self):
        # V2's flat spin scaled the vertical axis 3.5 times from noise.
        cal, note = fit(sweep())
        self.assertTrue(cal.flat)
        self.assertEqual(cal.scale[2], 1.)
        self.assertIn('flat turn', note)
        self.assertAlmostEqual(cal.offset[0], OFFSET[0], delta=3.)
        self.assertAlmostEqual(cal.offset[1], OFFSET[1], delta=3.)

    def test_a_robot_that_never_turned_is_refused(self):
        # V2, August 22: a still robot "calibrated" to a 3-count field and read plausibly, pointing nowhere.
        still = [reading(10.) for _ in range(60)]
        cal, note = fit(still)
        self.assertIsNone(cal)
        self.assertIn('never varied', note)
        self.assertFalse(coverage(Calibration((0, 0, 0), (1, 1, 1), 3., True), 360.)[0])

    def test_half_a_turn_is_refused(self):
        rows = [reading(h) for h in range(0, 181, 5)]       # Enough field, but the gyro saw half a circle.
        cal, note = fit(rows)
        self.assertIsNone(cal)
        self.assertIn('full circle', note)

    def test_readings_while_the_motors_work_are_left_out(self):
        cal, _ = fit(sweep(motors_between=True))
        self.assertIsNotNone(cal)
        self.assertAlmostEqual(cal.offset[0], OFFSET[0], delta=3.)
        self.assertEqual(cal.samples, 73)


class HeadingTests(unittest.TestCase):
    def test_after_calibrating_every_way_he_faces_reads_true(self):
        cal, _ = fit(sweep())
        for h in (0., 30., 90., 165., 200., 345.):
            got = heading(reading(h), cal)
            self.assertAlmostEqual(ang_diff(got['heading_deg'], h), 0., delta=1.5, msg=h)
            self.assertTrue(got['field_ok'] and got['motors_quiet'])

    def test_a_sensor_mounted_mirrored_is_found_from_the_turn(self):
        cal, _ = fit(sweep(mirrored=True))
        self.assertTrue(cal.mirrored)
        for h in (20., 110., 290.):
            self.assertAlmostEqual(ang_diff(heading(reading(h, mirrored=True), cal)['heading_deg'], h), 0., delta=1.5)

    def test_a_facing_someone_gives_pins_the_heading(self):
        # Owner, November 25: "he's facing roughly 15 degrees left of North". Here the sensor sits 40 degrees
        # round from his front, which the declination alone cannot know.
        rows = [reading((h + 40.) % 360.) for h in [(345. - 360. * i / 72) % 360. for i in range(73)]]
        for row, i in zip(rows, range(73)):
            row['imu']['yaw'] = (360. * i / 72 + 180.) % 360. - 180.
        cal, _ = fit(rows, told_facing_deg=345.)
        self.assertEqual(cal.told_facing_deg, 345.)
        self.assertAlmostEqual(ang_diff(heading(rows[-1], cal)['heading_deg'], 345.), 0., delta=1.5)
        self.assertAlmostEqual(ang_diff(heading(rows[18], cal)['heading_deg'], 255.), 0., delta=2.)   # A quarter left.

    def test_a_disturbed_field_is_flagged_and_no_calibration_gives_no_heading(self):
        cal, _ = fit(sweep())
        near_steel = reading(90.)
        near_steel['mag']['x'] += 400
        self.assertFalse(heading(near_steel, cal)['field_ok'])
        self.assertFalse(heading(reading(90., current=3000), cal)['motors_quiet'])
        self.assertIsNone(heading(reading(90.), None))
        self.assertIsNone(heading({'mag': {'ok': False}}, cal))

    def test_the_calibration_survives_saving(self):
        cal, _ = fit(sweep(mirrored=True), told_facing_deg=90.)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'calibration' / 'compass.json'
            self.assertIsNone(compass.load(path))
            compass.save(cal, path)
            back = compass.load(path)
        self.assertEqual((back.offset, back.scale, back.flat, back.mirrored, back.heading_offset_deg),
                         (cal.offset, cal.scale, cal.flat, cal.mirrored, cal.heading_offset_deg))
        self.assertTrue(back.saved_at)


class CalibrationWindowTests(unittest.TestCase):
    """The battery menu's window (owner, November 25): what its model makes of the lines it is fed."""
    def setUp(self):
        from r3x.gui.compass_model import CompassCalibration
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'compass.json'
        self.model = CompassCalibration(self.path)

    def feed(self, rows, start=0.):
        import json
        for i, row in enumerate(rows):
            self.model.feed_line(json.dumps(row).encode(), start + i * .1)

    def test_nothing_is_kept_before_start_and_progress_fills_in_as_he_turns(self):
        self.feed(sweep()[:10])
        self.assertEqual((self.model.phase, self.model.readings), ('ready', []))
        self.assertIn('No calibration saved yet', self.model.progress_words(1.))
        self.model.start(1.)
        rows = sweep()
        self.feed(rows[:37], 1.)                                   # Half way round.
        self.assertAlmostEqual(abs(self.model.turned), 180., delta=6.)
        self.assertAlmostEqual(self.model.coverage(), .5, delta=.06)
        self.assertFalse(self.model.ready_to_finish())
        self.assertFalse(self.model.finish())                     # Not offered yet; and refused if forced.
        self.assertEqual(self.model.phase, 'refused')
        self.model.start(5.)
        self.feed(rows, 5.)
        self.assertTrue(self.model.ready_to_finish())
        self.assertIn('press Finish', self.model.progress_words(20.))

    def test_finishing_saves_and_then_shows_which_way_he_faces(self):
        self.model.start(0.)
        self.feed(sweep(start=345.))
        self.assertTrue(self.model.finish(facing=345.))
        self.assertEqual(self.model.phase, 'saved')
        self.assertEqual(compass.load(self.path).told_facing_deg, 345.)
        self.feed([reading(90.)], 30.)
        self.assertAlmostEqual(ang_diff(self.model.facing()['heading_deg'], 90.), 0., delta=1.5)
        self.assertIn('pinned to the direction given', self.model.progress_words(31.))

    def test_readings_while_the_motors_draw_current_are_counted_apart(self):
        self.model.start(0.)
        self.feed([reading(h, current=3000) for h in range(0, 40, 5)])
        self.assertEqual((self.model.quiet, self.model.busy), (0, 8))
        self.assertIn('switch the motor controllers off', self.model.progress_words(1.))

    def test_a_stuck_sensor_bus_is_said_rather_than_showing_nothing(self):
        # November 25, the first try: after a drive the board's sensor bus hung; the gyroscope said "not ok" and
        # the compass and battery current repeated their last values, and the window showed nothing.
        self.feed(sweep()[:20])
        self.assertIsNone(self.model.trouble(2.))
        frozen = {**reading(80., current=1695), 'imu': {'ok': False}}
        self.feed([frozen] * 30, 10.)
        self.assertIn('gyroscope is not answering', self.model.progress_words(13.))
        self.assertIn('Restart ESP32', self.model.progress_words(13.))
        still = reading(80.)                                     # Gyroscope fine, compass repeating itself.
        self.feed([still] * 70, 20.)
        self.assertIn('has not changed at all', self.model.trouble(26.9))
        self.model.start(27.)
        self.assertIn('has not changed at all', self.model.progress_words(27.))
        self.feed(sweep()[:5], 27.)                              # It moves again: nothing to say.
        self.assertIsNone(self.model.trouble(27.5))
        self.feed([{'type': 'telemetry', 'batt_ma': 30, 'mag': {'ok': False}, 'imu': {'ok': True, 'yaw': 1.}}], 28.)
        self.assertIn('compass is not answering', self.model.trouble(28.))


if __name__ == '__main__':
    unittest.main()
