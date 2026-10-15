"""Open (or screenshot) the dashboard with synthetic data: no devices, models or network.

    .venv/bin/python tools/preview_dashboard.py                 # interactive window
    .venv/bin/python tools/preview_dashboard.py --screenshot out.png [--seconds 3]

The successor of V2's `python -m gui.dashboard --demo`. Every reading is made up: a
painted camera frame, a sweeping servo pose, a scripted conversation and drive-base
telemetry. The manual override and joystick act on these fakes only.
"""
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from r3x.gui.feed import DashboardFeed, LiveParts   # noqa: E402
from r3x.vision import Frame   # noqa: E402


class Settings:
    camera_enabled = True
    servo_port = None   # No controller: the dashboard's simulated body moves the droid.
    head_led_port = chest_led_port = drive_port = radar_port = 'synthetic'


class Result:
    def __init__(self, capability):
        self.capability, self.operation, self.outcome = capability, 'start', 'ok'


class Owner:
    results = [Result(c) for c in ('camera', 'robot_servos', 'robot_leds', 'drive_base', 'radar')]


class Hub:
    def __init__(self):
        self.frame = None

    def peek(self):
        return self.frame


class Camera:
    failure = None

    def __init__(self):
        self.hub = Hub()

    def latest(self, now):
        return [{'track_id': 'face-1', 'identity': 'p-bret', 'at': now, 'box': [.30 + .05*math.sin(now/3), .25, .14, .26],
                 'position': 'center'},
                {'track_id': 'face-2', 'identity': None, 'at': now, 'box': [.66, .3, .1, .19], 'position': 'right'}]


class Memory:
    def people(self):
        return [{'id': 'p-bret', 'name': 'Bret'}]


class Objects:
    def snapshot(self):
        return ({'label': 'chair', 'score': .81, 'box': [.05, .55, .2, .4], 'position': 'left'},)


class Link:
    failure = None

    def __init__(self):
        self.telemetry = self.matrix = None
        self.hello = {'sensors': [{'mount': 180}, {'mount': -60}, {'mount': 60}]}
        self._state = threading.Lock()

    def snapshot(self):
        return self.telemetry, self.matrix


class Drive:
    state = 'ready'

    def __init__(self):
        self.link = Link()
        self.moving = threading.Event()
        self.manual_active = False

    def availability(self, now=None):
        return True, None

    def begin_manual(self):
        self.manual_active = True
        return True, None

    def manual(self, lin, ang, cancel):
        return {'status': 'sent', 'lin': lin, 'ang': ang, 'held': []}

    def end_manual(self):
        self.manual_active = False

    def cancel(self):
        pass


class Radar:
    state = 'ready'

    def __init__(self):
        self.link = Link()


SCRIPT = (
    (.2, {'event': 'startup'}),
    (.4, {'event': 'turn', 'turn_id': 't1', 'original_text': 'Hey Rex, what are you up to?'}),
    (.5, {'event': 'speaker_evidence', 'turn_id': 't1', 'display_name': 'Bret', 'state': 'supported'}),
    (.6, {'event': 'model_request', 'primary_calls': 1}),
    (.9, {'event': 'speech_generated', 'turn_id': 't1', 'text': 'Spinning the galaxy\'s finest hits, as always.'}),
    (1., {'event': 'playback_started', 'turn_id': 't1'}),
    (1.3, {'event': 'speech_generated', 'turn_id': 't1', 'text': 'Want a request?'}),
    (2.4, {'event': 'delivery', 'turn_id': 't1', 'status': 'completed', 'completed_text': ''}),
    (2.6, {'event': 'agency_plan_saved', 'objective': 'Find out what Bret is building in the garage'}),
    # After the simulated body's wake choreography: a heard turn, thinking, then a spoken reply.
    (11., {'event': 'turn', 'turn_id': 't2', 'original_text': 'Play something upbeat.'}),
    (11.5, {'event': 'model_request', 'primary_calls': 1}),
    (12.5, {'event': 'speech_generated', 'turn_id': 't2', 'text': 'Coming right up, one cantina classic.'}),
    (12.6, {'event': 'playback_started', 'turn_id': 't2'}),
    (18., {'event': 'delivery', 'turn_id': 't2', 'status': 'completed', 'completed_text': ''}),
)


def drive_frames(feed, camera, drive, radar, stop):
    import numpy as np
    started = time.monotonic()
    script = list(SCRIPT)
    count = 0
    y, x = np.mgrid[0:540, 0:960]
    images = [np.stack(((40 + 30*np.sin(x/90 + k/5)) + 0*y, 60 + y/9, 80 + x/12), axis=-1).astype(np.uint8).tobytes()
              for k in range(30)]   # Precomputed, so the preview measures the dashboard, not numpy.
    while not stop.is_set():
        now = time.monotonic()
        t = now - started
        while script and script[0][0] <= t:
            feed({'session_id': 'preview', 'at': now, **script.pop(0)[1]})
        count += 1
        camera.hub.frame = Frame(f'f{count}', 'preview', 'preview', now, 960, 540, images[count % len(images)])
        tof = {k: int(600 + 400*math.sin(t + i)) for i, k in enumerate(('fl', 'fr', 'rl', 'rr', 'lf', 'lb', 'rf', 'rb'))}
        drive.link.telemetry = {'received_at': now, 'state': 'idle', 'owner': 'host', 'gamepad': False, 'zone': 'clear',
                                'blocked_dir': None, 'tof_mm': tof, 'odom': {'x': 0, 'y': 0, 'theta': 0, 'lin': 0, 'ang': 0},
                                'batt_mv': 13180, 'batt_ma': -820, 'batt_soc': 71,
                                'imu': {'ok': True, 'pitch': 1.5, 'roll': -.5, 'yaw': 20*math.sin(t/4)},
                                'env': {'ok': True, 't': 22.4, 'rh': 41, 'hpa': 1012}}
        drive.link.matrix = {'grid': [int(400 + 300*abs(math.sin(i/7 + t))) for i in range(64)], 'rej': []}
        radar.link.telemetry = {'rx_monotonic': now, 'radar': {'ok': True, 'up': 3, 'targets': [
            {'b': 20*math.sin(t/2), 'r': 1.8, 'c': .7, 's': .1, 'm': 1}]}, 'sens': [{'ok': True}] * 3}
        feed.lights.command('head', f'SPEAK_LEVEL:{int(120 + 100*math.sin(t*9))}' if 1. < t < 2.4 or 12.6 < t < 18 else 'ACTIVE')
        stop.wait(1 / 15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--screenshot', type=Path)
    parser.add_argument('--seconds', type=float, default=14.)
    args = parser.parse_args()
    # Qt Quick 3D needs the native (Metal) backend; the offscreen platform draws no droid.
    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication
    from r3x.gui.dashboard import DashboardWindow
    qt = QApplication(['DJ-R3X preview'])
    feed = DashboardFeed()
    camera, drive, radar = Camera(), Drive(), Radar()
    feed({'session_id': 'preview', 'at': 0., 'event': 'starting', 'log_path': '(synthetic preview)'})
    feed.attach(LiveParts(settings=Settings(), owner=Owner(), memory=Memory(), camera=camera, objects=Objects(),
                          drive=drive, radar=radar))
    feed.lights.command('head', 'EYE:255,200,0')
    feed.lights.command('head', 'SPEAK:happy')
    feed.lights.command('chest', 'SPEAK:happy')
    stop = threading.Event()
    threading.Thread(target=drive_frames, args=(feed, camera, drive, radar, stop), daemon=True).start()
    window = DashboardWindow(feed)
    window.resize(1600, 960)
    if args.screenshot:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)   # Rendered, never on screen.
        window.show()
        def grab():
            window.grab().save(str(args.screenshot))
            stop.set()
            qt.quit()
        QTimer.singleShot(int(args.seconds * 1000), grab)
    else:
        window.show()
    qt.exec()
    stop.set()


if __name__ == '__main__':
    main()
