"""ToF diagnostics (owner, November 20): what the window's model makes of the drive controller's
lines, the relay that feeds it without ever waiting on it, and the battery menu's wiring.
Synthetic lines only; no Qt, no serial port, no child process."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from r3x.gui.tof_model import HISTORY_S, RING, TofDiagnostics

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'operator_tools'
sys.path.insert(0, str(TOOLS))

from utils.line_relay import LineRelay  # noqa: E402

RING_OK = {'fl': 690, 'fr': 450, 'rl': 3500, 'rr': 1250, 'lf': 150, 'lb': 165, 'rf': 646, 'rb': 1120,
           'fl_radial': 1170, 'fr_radial': 480, 'rl_radial': 4000, 'rr_radial': 1250}


def telemetry(**changed):
    return json.dumps({'v': 1, 'type': 'telemetry', 'state': 'idle', 'zone': 'clear', 'blocked_dir': 'none',
                       'tof_mm': {**RING_OK, **changed}}).encode()


def matrix(kind='tofmx', value=800):
    return json.dumps({'v': 1, 'type': kind, 'g': f'{value:03x}' * 64,
                       'rej': [4095, 4095, 4095, 4095, 1548, 517, 70, 70]}).encode()


def raw(sensor, t, *, raw_mm=480, status=0, input_mm=480, filtered_mm=480):
    return json.dumps({'v': 1, 'type': 'tofraw', 't': t, 'sensor': sensor, 'raw_mm': raw_mm, 'status': status,
                       'input_mm': input_mm, 'filtered_mm': filtered_mm, 'sig': 19.5, 'amb': .3, 'spads': 72}).encode()


def watch(model, seconds, *, step=.1, start=0., **changed):
    """Telemetry and both depth sensors at their usual pace; returns the time it ended."""
    now = start
    while now < start + seconds:
        model.feed_line(telemetry(**changed), now)
        model.feed_line(matrix(), now)
        model.feed_line(matrix('tofmx_rear'), now)
        now = round(now + step, 3)
    return now - step


class ModelTests(unittest.TestCase):
    def test_a_healthy_ring_says_so_and_nothing_else(self):
        model = TofDiagnostics()
        now = watch(model, 3.)
        self.assertEqual(model.link(now)[0], True)
        self.assertEqual({key: model.status(key, now) for key, *_ in RING},
                         {'fl': 'reading', 'lf': 'reading', 'lb': 'reading', 'rl': 'open', 'rr': 'reading',
                          'rb': 'reading', 'rf': 'reading', 'fr': 'reading'})
        self.assertEqual(model.notices(now), [('ok', 'All eight ring sensors and both 8×8 sensors are reporting.')])

    def test_a_corner_sensor_is_judged_on_its_own_reading_not_the_one_fused_with_the_8x8(self):
        # November 20: fr read 0.45 m in telemetry (the 8x8's right half) while the sensor itself gave nothing.
        model = TofDiagnostics()
        now = watch(model, 2., fr_radial=-1)
        self.assertEqual(model.status('fr', now), 'silent')
        self.assertEqual(model.fused['fr'], 450)
        self.assertEqual(model.sensors['fl'].value, 1170)

    def test_silent_sensors_are_named_with_their_channels(self):
        model = TofDiagnostics()
        now = watch(model, 2., rl_radial=-1, rr_radial=-1)
        level, text = model.notices(now)[0]
        self.assertEqual(level, 'bad')
        self.assertEqual(text, 'Not answering: Rear-left (channel 6), Rear-right (channel 7).')

    def test_a_sensor_that_answers_but_is_thrown_away_is_not_called_a_cabling_fault(self):
        # The front-right that evening: raw 476-481 mm, valid, in every sample; published -1.
        model = TofDiagnostics()
        for i in range(12):
            model.feed_line(raw('fr', 1000 + 227 * i, filtered_mm=-1), i * .227)
            model.feed_line(raw('fl', 1000 + 227 * i, raw_mm=1170, input_mm=1170, filtered_mm=1170), i * .227)
        now = watch(model, 2.5, start=2.8, fr_radial=-1)
        for i in range(12):
            model.feed_line(raw('fr', 20000 + 227 * i, filtered_mm=-1), now)
        self.assertEqual(model.status('fr', now), 'discarded')
        texts = [text for _, text in model.notices(now)]
        self.assertTrue(any('Front-right (channel 5) is answering' in text and 'cabling is not the problem' in text
                            for text in texts), texts)
        self.assertFalse(any('Not answering' in text for text in texts), texts)

    def test_a_slow_round_of_the_ring_is_reported_from_the_raw_stream(self):
        model = TofDiagnostics()
        for i in range(10):
            model.feed_line(raw('fl', 5000 + 227 * i), i * .227)
        now = watch(model, 1., start=2.3)
        self.assertAlmostEqual(model.poll_ms(), 227)
        self.assertTrue(any('every 227 ms' in text for _, text in model.notices(now)))
        quick = TofDiagnostics()
        for i in range(10):
            quick.feed_line(raw('fl', 5000 + 80 * i), i * .08)
        self.assertFalse(any('read every' in text for _, text in quick.notices(watch(quick, 1., start=1.))))

    def test_doubtful_readings_explain_a_swinging_distance(self):
        model = TofDiagnostics()
        for i in range(20):
            model.feed_line(raw('fl', 100 * i, raw_mm=1170, status=7 if i % 4 else 0, input_mm=4000, filtered_mm=3000), i * .1)
        now = watch(model, 1., start=2.)
        self.assertTrue(any(text.startswith('Front-left: 75% of its readings are doubtful')
                            for _, text in model.notices(now)))

    def test_a_dropout_while_watching_is_counted_and_stays_in_the_minute_kept(self):
        model = TofDiagnostics()
        now = watch(model, 2.)
        now = watch(model, 1., start=now + .1, rf=-1)        # A cable is moved...
        now = watch(model, 2., start=now + .1)               # ...and seats again.
        sensor = model.sensors['rf']
        self.assertEqual(sensor.dropouts, 1)
        self.assertEqual(model.status('rf', now), 'reading')
        self.assertTrue(any(mm < 0 for _, mm in sensor.history))
        self.assertIn(('warn', 'Dropped out and came back while watching: Right-front ×1.'), model.notices(now))
        now = watch(model, HISTORY_S + 5., start=now + .1)
        self.assertFalse(any(mm < 0 for _, mm in sensor.history))    # Older than the minute: gone from the trace.
        self.assertEqual(sensor.dropouts, 1)                         # The count is for the whole watch.

    def test_a_depth_sensor_stall_is_timed_and_one_that_stops_is_called_out(self):
        model = TofDiagnostics()
        now = watch(model, 2.)
        for at in (now + .1, now + .2, now + .3):                 # The rear one keeps coming; the front one stalls.
            model.feed_line(telemetry(), at)
            model.feed_line(matrix('tofmx_rear'), at)
        now = watch(model, 1., start=now + 1.2)
        self.assertEqual(model.front.stalls, 1)
        self.assertAlmostEqual(model.front.last_stall[1], 1.2, places=2)
        self.assertEqual(model.rear.stalls, 1)                   # 0.9 s without one.
        self.assertTrue(any(text.startswith('Front 8×8: stalled 1.2 s') for _, text in model.notices(now)))
        for step in range(1, 16):                                 # Telemetry goes on, the front sensor does not.
            model.feed_line(telemetry(), now + step * .1)
            model.feed_line(matrix('tofmx_rear'), now + step * .1)
        self.assertIn(('bad', 'Front 8×8: nothing for 1.5 s.'), model.notices(now + 1.5))

    def test_nearest_ignores_the_floor_and_empty_zones(self):
        model = TofDiagnostics()
        grid = [3500] * 64
        grid[8 * 7 + 3] = 60      # Bottom row, under its 70 mm floor line: counts.
        grid[8 * 6 + 3] = 300     # Row 6 at 300 mm is the floor (rej 70).
        grid[8 * 2 + 5] = 450
        grid[0] = 0               # No return.
        model.feed_line({'type': 'tofmx', 'g': ''.join(f'{v:03x}' for v in grid),
                         'rej': [4095, 4095, 4095, 4095, 1548, 517, 70, 70]}, 0.)
        self.assertEqual(model.front.nearest_mm(), 60)
        self.assertIsNone(model.rear.nearest_mm())

    def test_the_link_line_says_why_there_is_no_data(self):
        model = TofDiagnostics()
        self.assertEqual(model.link(0.), (False, 'Waiting for the drive controller…'))
        model.feed_line(b'{"type":"feed","mode":"dormant","detail":"Rex is running"}', 0.)
        self.assertIn('Rex is running and has the drive controller', model.link(1.)[1])
        self.assertEqual(model.notices(1.), [])                  # Nothing is claimed about sensors nobody can read.
        now = watch(model, 1., start=2.)
        model.feed_line(b'{"type":"feed","mode":"live"}', now)
        self.assertTrue(model.link(now)[0])
        self.assertEqual(model.link(now + 5.)[1], 'No data from the drive controller for 5 s.')

    def test_bad_lines_are_counted_and_change_nothing(self):
        model = TofDiagnostics()
        now = watch(model, 1.)
        before = dict(model.sensors['lf'].__dict__, history=list(model.sensors['lf'].history))
        for line in (b'\xff\xfe{', b'[1, 2]', b'{"type":"tofmx","g":"zz"}', b'{"type":"tofmx","g":"' + b'zzz' * 64 + b'"}',
                     b'{"type":"telemetry","tof_mm":"none"}', b'{"type":"telemetry"}', b''):
            model.feed_line(line, now)
        self.assertEqual(model.unreadable, 4)
        self.assertEqual(dict(model.sensors['lf'].__dict__, history=list(model.sensors['lf'].history)), before)

    def test_controller_messages_and_a_raise_request_are_kept_for_the_window(self):
        model = TofDiagnostics()
        model.feed_line(b'{"v":1,"type":"log","lvl":"warn","msg":"tof[6] rl: 8 consecutive read errors - reporting -1"}', 3.)
        model.feed_line(b'{"type":"feed","raise":true}', 4.)
        self.assertEqual(list(model.log), [(3., 'warn', 'tof[6] rl: 8 consecutive read errors - reporting -1')])
        self.assertEqual(model.log_total, 1)
        self.assertTrue(model.raise_wanted)


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.read_fd, self.write_fd = os.pipe()
        self.addCleanup(lambda: [os.close(fd) for fd in (self.read_fd, self.write_fd) if self._open(fd)])

    @staticmethod
    def _open(fd):
        try:
            os.fstat(fd)
            return True
        except OSError:
            return False

    def _drain(self):
        os.set_blocking(self.read_fd, False)
        data = b''
        while True:
            try:
                chunk = os.read(self.read_fd, 1 << 16)
            except BlockingIOError:
                return data
            if not chunk:
                return data
            data += chunk

    def test_lines_arrive_whole_and_in_order(self):
        relay = LineRelay(self.write_fd)
        self.assertTrue(relay.send(b'{"a":1}\n'))
        self.assertTrue(relay.send(b'{"b":2}'))                  # The newline is supplied.
        self.assertEqual(self._drain(), b'{"a":1}\n{"b":2}\n')

    def test_a_reader_that_stops_reading_never_holds_the_sender_and_lines_stay_whole(self):
        relay = LineRelay(self.write_fd)
        line = b'{"type":"telemetry","pad":"' + b'x' * 700 + b'"}\n'
        taken = sum(relay.send(line) for _ in range(400))        # Far more than a pipe holds; returns at once.
        self.assertLess(taken, 400)
        self.assertEqual(relay.dropped, 400 - taken)
        got = self._drain()
        self.assertTrue(relay.send(b'{"after":true}\n'))         # Finishes the held line first, then carries on.
        got += self._drain()
        rows = got.split(b'\n')
        self.assertEqual(rows[-1], b'')
        self.assertEqual(rows[-2], b'{"after":true}')
        self.assertTrue(all(row == line[:-1] for row in rows[:-2]), 'a line was cut or joined to another')
        self.assertEqual(len(rows) - 2, taken)

    def test_a_reader_that_has_gone_ends_the_relay_quietly(self):
        relay = LineRelay(self.write_fd)
        os.close(self.read_fd)
        self.assertFalse(relay.send(b'{"a":1}\n'))
        self.assertTrue(relay.closed)
        self.assertFalse(relay.send(b'{"a":2}\n'))


class Stdin:
    def __init__(self, fd):
        self.fd = fd

    def fileno(self):
        return self.fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Child:
    """A window process that is only a pipe: what the battery menu would have started."""
    def __init__(self, argv, cwd=None, stdin=None):
        self.argv, self.cwd, self.pid = argv, cwd, 4242
        self.read_fd, write_fd = os.pipe()
        os.set_blocking(self.read_fd, False)
        self.stdin = Stdin(write_fd)
        self.exited = None

    def poll(self):
        return self.exited

    def lines(self):
        try:
            data = os.read(self.read_fd, 1 << 20)
        except BlockingIOError:
            return []
        return [json.loads(row) for row in data.split(b'\n') if row]


class BatteryMenuTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location('rex_battery_menubar_tof_check',
                                                      TOOLS / 'tools' / 'rex_battery_menubar.py')
        self.menu = importlib.util.module_from_spec(spec)
        with patch('logging.basicConfig'):
            spec.loader.exec_module(self.menu)
        self.children = []

        def popen(argv, **kwargs):
            self.children.append(Child(argv, **kwargs))
            return self.children[-1]

        started = patch('subprocess.Popen', side_effect=popen)
        started.start()
        self.addCleanup(started.stop)
        self.addCleanup(lambda: [(c.stdin.close(), os.close(c.read_fd)) for c in self.children])

    def queued(self):
        with self.menu._tx_lock:
            rows = [json.loads(payload) for payload in self.menu._tx_queue]
            self.menu._tx_queue.clear()
        return [(row['cmd'], row.get('seconds')) for row in rows]

    def test_the_menu_starts_the_window_in_v3s_environment_and_a_second_click_raises_it(self):
        self.assertEqual(self.menu._tof_window.open(), 'started')
        child = self.children[0]
        self.assertEqual(child.argv, [sys.executable, '-m', 'r3x.gui.tof_diagnostics'])
        self.assertEqual(child.cwd, str(ROOT))
        self.assertEqual(self.menu._tof_window.open(), 'raised')
        self.assertEqual(len(self.children), 1)
        self.assertEqual(child.lines(), [{'type': 'feed', 'raise': True}])

    def test_every_line_read_reaches_the_window_and_nothing_is_sent_without_one(self):
        self.menu._diagnostics_feed(telemetry())                         # No window: nothing happens.
        self.menu._diagnostics_tick(now=100.)
        self.assertEqual(self.queued(), [])
        self.menu._tof_window.open()
        self.menu._diagnostics_feed(telemetry())
        self.menu._diagnostics_feed(matrix())
        self.assertEqual([row['type'] for row in self.children[0].lines()], ['telemetry', 'tofmx'])

    def test_the_raw_stream_is_asked_for_only_while_the_window_watches_a_live_meter(self):
        menu = self.menu
        menu._tof_window.open()
        menu._update(mode='connecting', detail='listening')
        menu._diagnostics_tick(now=100.)
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.children[0].lines(), [{'type': 'feed', 'mode': 'connecting', 'detail': 'listening'}])
        menu._update(mode='live')
        menu._diagnostics_tick(now=101.)
        self.assertEqual(self.queued(), [('tof_debug', menu._TOF_RAW_SECS)])
        menu._diagnostics_tick(now=104.)
        self.assertEqual(self.queued(), [])                      # Not yet due again.
        menu._diagnostics_tick(now=101. + menu._TOF_RAW_RENEW_SECS)
        self.assertEqual(self.queued(), [('tof_debug', menu._TOF_RAW_SECS)])
        menu._update(mode='dormant', detail='Rex is running — port handed to the robot')   # Rex takes the port.
        menu._diagnostics_tick(now=120.)
        self.assertEqual(self.queued(), [])
        self.assertEqual(self.children[0].lines()[-1]['mode'], 'dormant')
        menu._update(mode='live')                                # Rex is done: asked for afresh.
        menu._diagnostics_tick(now=130.)
        self.assertEqual(self.queued(), [('tof_debug', menu._TOF_RAW_SECS)])

    def test_closing_the_window_switches_the_raw_stream_off_and_a_later_click_starts_a_new_one(self):
        menu = self.menu
        menu._tof_window.open()
        menu._update(mode='live')
        menu._diagnostics_tick(now=10.)
        self.queued()
        self.children[0].exited = 0
        menu._diagnostics_tick(now=11.)
        self.assertEqual(self.queued(), [('tof_debug', 0)])
        menu._diagnostics_feed(telemetry())                              # Nowhere to go, and no error.
        menu._diagnostics_tick(now=12.)
        self.assertEqual(self.queued(), [])
        self.assertEqual(menu._tof_window.open(), 'started')
        self.assertEqual(len(self.children), 2)

    def test_a_window_that_stops_reading_costs_the_serial_worker_nothing(self):
        menu = self.menu
        menu._tof_window.open()
        menu._update(mode='live')
        for _ in range(2000):                                    # About three minutes of telemetry, never read.
            menu._diagnostics_feed(telemetry())
        menu._diagnostics_tick(now=50.)                                  # Still runs, and still asks the firmware.
        self.assertEqual(self.queued(), [('tof_debug', menu._TOF_RAW_SECS)])
        self.assertGreater(menu._tof_window.relay.dropped, 0)

    def test_the_sensor_window_is_fed_the_same_lines_and_asks_the_controller_for_nothing(self):
        menu = self.menu
        self.assertEqual(menu._sensor_window.open(), 'started')
        child = self.children[0]
        self.assertEqual(child.argv, [sys.executable, '-m', 'r3x.gui.sensor_diagnostics'])
        self.assertEqual(child.cwd, str(ROOT))
        menu._update(mode='live')
        menu._diagnostics_tick(now=100.)
        menu._diagnostics_tick(now=100. + menu._TOF_RAW_RENEW_SECS)
        self.assertEqual(self.queued(), [])                      # No raw stream is asked for on its account.
        menu._diagnostics_feed(telemetry())
        self.assertEqual([row['type'] for row in child.lines()], ['feed', 'feed', 'telemetry'])
        self.assertEqual(menu._sensor_window.open(), 'raised')

    def test_the_compass_window_is_fed_the_same_lines_and_asks_the_controller_for_nothing(self):
        # Owner, November 25: "add the calibration routine to the menu bar app as a clickable option".
        menu = self.menu
        self.assertEqual(menu._compass_window.open(), 'started')
        child = self.children[0]
        self.assertEqual(child.argv, [sys.executable, '-m', 'r3x.gui.compass_calibration'])
        menu._update(mode='live')
        menu._diagnostics_tick(now=100.)
        menu._diagnostics_tick(now=100. + menu._TOF_RAW_RENEW_SECS)
        self.assertEqual(self.queued(), [])
        menu._diagnostics_feed(telemetry())
        self.assertEqual(child.lines()[-1]['type'], 'telemetry')

    def test_both_windows_open_together_get_every_line_and_closing_one_leaves_the_other(self):
        menu = self.menu
        menu._tof_window.open(); menu._sensor_window.open()
        tof, sensors = self.children
        menu._update(mode='live')
        menu._diagnostics_tick(now=10.)
        self.assertEqual(self.queued(), [('tof_debug', menu._TOF_RAW_SECS)])   # For the ToF window only.
        menu._diagnostics_feed(telemetry())
        self.assertEqual(tof.lines()[-1]['type'], 'telemetry')
        self.assertEqual(sensors.lines()[-1]['type'], 'telemetry')
        tof.exited = 0
        menu._diagnostics_tick(now=11.)
        self.assertEqual(self.queued(), [('tof_debug', 0)])
        menu._diagnostics_feed(telemetry())
        self.assertEqual(sensors.lines()[-1]['type'], 'telemetry')


if __name__ == '__main__':
    unittest.main()
