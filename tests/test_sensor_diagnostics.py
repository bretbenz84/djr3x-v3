"""Sensor diagnostics (owner, November 22): what the window's model makes of the drive controller's
lines. Synthetic lines only; no Qt, no serial port. They prove the arithmetic and the wording, not how
any real sensor behaves."""
import json
import math
import unittest

from r3x.gui.sensor_model import DRIFT_NORMAL, SensorDiagnostics

RING = {'fl': 690, 'fr': 450, 'rl': 3500, 'rr': 1250, 'lf': 150, 'lb': 165, 'rf': 646, 'rb': 1120,
        'fl_radial': 1170, 'fr_radial': 480, 'rl_radial': 4000, 'rr_radial': 1250}


class Robot:
    """A controller sending ten frames a second: a gyroscope with a zero error, wheels that may turn."""
    def __init__(self, model, *, zero_error=0., uptime=600.):
        self.model, self.zero_error = model, zero_error
        self.t, self.started = 0., uptime
        self.yaw = self.theta = self.x = 0.
        self.imu = self.compass = self.climate = self.matrices = True
        self.battery_mv = 13050
        self.ring = dict(RING)
        self.compass_wander = 0.
        self.duty = None
        self.zero = None             # (deg/s, seconds since measured) as firmware 0.2.24 reports it; None: older firmware.

    def frame(self, turn=0., speed=0.):
        self.t += .1
        self.yaw += (self.zero_error + turn) * .1
        self.theta += turn * .1
        self.x += speed * .1
        rate = math.radians(turn)
        left, right = speed - rate * .2, speed + rate * .2
        duty = self.duty or ((-300 if turn else 0, 300 if turn else 0))
        angle = math.radians(-84. + self.compass_wander * math.sin(self.t * 3))
        return {'v': 1, 'type': 'telemetry', 't': int((self.started + self.t) * 1000), 'state': 'idle', 'errs': 0,
                'odom': {'x': round(self.x, 3), 'y': 0., 'theta': round(math.radians(self.theta), 4), 'lin': speed, 'ang': round(rate, 3)},
                'wheels': {'vl': round(left, 3), 'vr': round(right, 3), 'dl': duty[0], 'dr': duty[1]},
                'batt_mv': self.battery_mv,
                'imu': ({'ok': True, 'pitch': -4.6, 'roll': -.9, 'yaw': round((self.yaw + 180) % 360 - 180, 1),
                         **({'zero': self.zero[0], 'zero_age': self.zero[1]} if self.zero else {})}
                        if self.imu else {'ok': False}),
                'env': {'ok': self.climate, 't': 23.4, 'hpa': 1018.},
                'mag': ({'ok': True, 'x': round(1500 * math.cos(angle)), 'y': round(1500 * math.sin(angle)), 'z': -1025}
                        if self.compass else {'ok': False}),
                'tof_mm': self.ring}

    def run(self, seconds, **motion):
        for _ in range(round(seconds * 10)):
            if self.matrices:
                for kind in ('tofmx', 'tofmx_rear'):
                    self.model.feed_line({'type': kind, 'g': '320' * 64, 'rej': []}, self.t)
            self.model.feed_line(json.dumps(self.frame(**motion)).encode(), self.t)
        return self.t


def words(model, now, level=None):
    return ' | '.join(text for row, text in model.notices(now) if level in (None, row))


class DriftTests(unittest.TestCase):
    def test_a_steady_gyroscope_and_a_full_bus_say_so_and_nothing_else(self):
        model = SensorDiagnostics()
        now = Robot(model, zero_error=.08).run(8)
        self.assertAlmostEqual(model.drift(), .08, delta=.03)
        self.assertEqual(model.drift_level(), 'ok')
        self.assertEqual({level for level, _ in model.notices(now)}, {'ok'})
        self.assertIn('Every chip on the sensor bus is answering', words(model, now))

    def test_a_wrong_zero_is_measured_at_rest_and_named_with_what_to_do(self):
        # November 22: zero taken from one reading mid-shake, 19.7 degrees a second off.
        model = SensorDiagnostics()
        now = Robot(model, zero_error=19.7).run(6)       # The heading wraps past 180 on the way.
        self.assertAlmostEqual(model.drift(), 19.7, delta=.2)
        self.assertEqual(model.drift_level(), 'bad')
        said = words(model, now, 'bad')
        self.assertIn('+19.7° a second', said)
        self.assertIn('Restart ESP32', said)

    def test_a_little_drift_is_a_warning_not_a_fault(self):
        model = SensorDiagnostics()
        now = Robot(model, zero_error=-.6).run(6)
        self.assertEqual(model.drift_level(), 'warn')
        self.assertIn(f'normal is under {DRIFT_NORMAL}', words(model, now, 'warn'))

    def test_drift_is_only_read_with_the_wheels_still_and_after_a_few_seconds(self):
        model = SensorDiagnostics()
        robot = Robot(model, zero_error=.1)
        robot.run(2)
        self.assertIsNone(model.drift())                         # Not long enough yet.
        self.assertIn('needs 3 s', words(model, robot.t))
        robot.run(5)
        self.assertIsNotNone(model.drift())
        now = robot.run(2, turn=30.)                             # Turning: the heading is meant to move.
        self.assertIsNone(model.drift())
        self.assertIn('wheels are moving', words(model, now))
        robot.run(4)
        self.assertAlmostEqual(model.drift(), .1, delta=.05)     # The turn itself left no trace in it.

    def test_the_last_move_compares_the_gyroscope_with_the_wheels(self):
        model = SensorDiagnostics()
        robot = Robot(model, zero_error=19.7)
        robot.run(4)
        robot.run(2.2, turn=20.)                                 # The wheels turn him 44 degrees.
        now = robot.run(2)
        move = model.last_move
        self.assertAlmostEqual(move['wheels'], 44., delta=3.)
        self.assertAlmostEqual(move['gyro'], 44. + 19.7 * 2.2, delta=5.)
        self.assertAlmostEqual(move['seconds'], 2.2, delta=.3)
        self.assertTrue(model.moves_disagree())
        self.assertIn('wheels +4', words(model, now, 'warn'))
        # With a good zero the two agree and nothing is said about it.
        model = SensorDiagnostics()
        robot = Robot(model, zero_error=.05)
        robot.run(4); robot.run(2.2, turn=20.); now = robot.run(2)
        self.assertFalse(model.moves_disagree())
        self.assertNotIn('last move', words(model, now))


class BusTests(unittest.TestCase):
    def test_a_battery_gauge_silent_at_the_start_explains_the_empty_battery_menu(self):
        model = SensorDiagnostics()
        robot = Robot(model)
        robot.battery_mv = -1
        now = robot.run(5)
        self.assertFalse(model.devices['battery'].ok)
        said = words(model, now)
        self.assertIn('Not answering: Battery gauge (INA226)', said)
        self.assertIn('only set up when the controller starts', said)

    def test_several_silent_chips_point_at_the_bus_not_at_one_sensor(self):
        model = SensorDiagnostics()
        robot = Robot(model)
        robot.matrices, robot.battery_mv, robot.compass = False, -1, False
        robot.ring = {**RING, 'lb': -1, 'rl_radial': -1}
        now = robot.run(5)
        said = words(model, now, 'bad')
        self.assertIn('Ring range sensors (eight VL53L1X), 6 of 8', said)
        self.assertIn('silent together', said)
        self.assertEqual(model.ring_answering, 6)

    def test_a_chip_that_drops_out_and_comes_back_is_counted(self):
        model = SensorDiagnostics()
        robot = Robot(model)
        robot.run(3)
        robot.imu = False
        now = robot.run(1)
        self.assertIn('Not answering: Gyroscope and tilt (LSM6DS3)', words(model, now, 'bad'))
        self.assertIsNone(model.drift())                         # No heading, no drift figure.
        robot.imu = True
        now = robot.run(5)
        self.assertEqual(model.devices['imu'].dropouts, 1)
        self.assertIn('Gyroscope and tilt ×1', words(model, now, 'warn'))

    def test_a_heading_that_jumps_at_rest_means_the_sensor_started_over(self):
        model = SensorDiagnostics()
        robot = Robot(model)
        robot.run(4)
        robot.yaw += 70.                                         # The firmware set its heading afresh.
        now = robot.run(4)
        self.assertEqual(model.heading_jumps, 1)
        self.assertIn('sensor started over', words(model, now, 'warn'))
        self.assertAlmostEqual(model.drift(), 0., delta=.05)     # The jump is not counted as drift.

    def test_a_controller_restart_is_seen_from_its_own_clock(self):
        model = SensorDiagnostics()
        robot = Robot(model, zero_error=19.7, uptime=5400.)
        robot.run(5)
        self.assertEqual(model.drift_level(), 'bad')
        robot.started, robot.zero_error = -robot.t, .05          # Restarted: its clock begins again, a fresh zero.
        now = robot.run(5)
        self.assertEqual(model.restarts, 1)
        self.assertLess(model.uptime, 10.)
        self.assertEqual(model.drift_level(), 'ok')
        self.assertIn('controller restarted', words(model, now, 'warn'))

    def test_a_wandering_compass_and_a_driven_wheel_that_does_not_turn_are_called_out(self):
        model = SensorDiagnostics()
        robot = Robot(model)
        robot.compass_wander = 4.
        robot.duty = (0, 400)                                    # The right wheel is pushed and stays put.
        now = robot.run(5)
        said = words(model, now, 'warn')
        self.assertIn('The compass wandered', said)
        self.assertIn('The right wheel was driven and did not turn', said)
        self.assertNotIn('left wheel', said)

    def test_the_controllers_bus_messages_are_counted_and_quoted(self):
        model = SensorDiagnostics()
        robot = Robot(model)
        robot.run(4)
        model.feed_line({'type': 'log', 'lvl': 'info', 'msg': 'bluetooth (41 since the last report): noise'}, robot.t)
        model.feed_line({'type': 'log', 'lvl': 'warn', 'msg': 'imu: LSM6DS3 recovered and back online'}, robot.t)
        now = robot.run(1)
        self.assertEqual(model.bus_events, 1)
        self.assertIn('imu: LSM6DS3 recovered and back online', words(model, now, 'warn'))

    def test_newer_firmware_says_what_zero_it_uses_and_whether_it_has_measured_it(self):
        model = SensorDiagnostics()
        robot = Robot(model, zero_error=3.2)
        robot.zero = (0., -1)                                    # The chip came back while he was moving.
        now = robot.run(5)
        said = words(model, now)
        self.assertIn('has not been measured since it started', said)
        self.assertIn('first second he stands still', words(model, now, 'bad'))
        self.assertNotIn('Restart ESP32', words(model, now, 'bad'))    # Standing still is the remedy now.
        robot.zero, robot.zero_error = (-3.16, 0), 0.
        now = robot.run(6)
        self.assertEqual((model.zero, model.zero_age), (-3.16, 0))
        self.assertNotIn('has not been measured', words(model, now))

    def test_nothing_is_claimed_without_a_live_feed(self):
        model = SensorDiagnostics()
        self.assertEqual(model.notices(0.), [])
        model.feed_line(b'{"type":"feed","mode":"dormant","detail":"Rex is running"}', 1.)
        self.assertIn('Rex is running', model.link(2.)[1])
        model.feed_line(b'not json', 2.)
        self.assertEqual(model.unreadable, 1)


if __name__ == '__main__':
    unittest.main()
