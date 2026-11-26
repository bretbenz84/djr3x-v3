"""A compass calibration as the battery menu's window runs it (Qt-free).

Owner, November 25: "add the calibration routine to the menu bar app as a clickable option ... a small GUI that
shows progress of the calibration. I would ensure the motor controllers were off while I push it." Fed the drive
controller's lines exactly as the diagnostics windows are (controller_feed.py): the battery menu keeps the port
and passes every line on, and this sends nothing.

Recording starts on the person's word. It keeps every telemetry message; the gyroscope's yaw says how far he
has been pushed round and which tenths of the circle have a quiet compass reading (motors drawing current are
left out, compass.QUIET_MA). Finishing fits the calibration with compass.fit (the same checks as the command
line tool) and writes it; afterwards the window shows which way he faces by it.
"""
from __future__ import annotations

from pathlib import Path

from .. import compass
from .controller_feed import ControllerFeed

ROOT = Path(__file__).resolve().parents[3]
SECTOR_DEG = 10.          # The circle is drawn and counted in pieces this wide.


class CompassCalibration(ControllerFeed):
    def __init__(self, path=None):
        super().__init__()
        self.path = Path(path) if path is not None else ROOT / compass.CALIBRATION_FILE
        self.calibration = compass.load(self.path)
        self.phase = 'ready'          # ready, recording, refused (note says why), saved
        self.note = ''
        self.readings = []
        self.started_at = None
        self.turned = 0.              # Degrees he has been turned since recording started (+ left).
        self.sectors = set()          # Tenths of the circle (by the gyroscope) with a quiet compass reading.
        self.quiet = self.busy = 0
        self.points = []              # Raw (x, y) of quiet readings, to draw the cloud forming.
        self._yaw0 = self._last_yaw = None

    # ------------------------------------------------------------------ the person's steps
    def start(self, now):
        self.phase, self.note = 'recording', ''
        self.readings, self.points, self.sectors = [], [], set()
        self.started_at, self.turned, self.quiet, self.busy = now, 0., 0, 0
        self._yaw0 = self._last_yaw = None

    def ready_to_finish(self):
        return self.phase == 'recording' and abs(self.turned) >= compass.MIN_TURN_DEG and self.quiet >= compass.MIN_SAMPLES

    def finish(self, facing=None):
        """Fit and save. facing: his true heading now (0 north, 90 east), if someone can say it."""
        if self.phase != 'recording':
            return False
        cal, note = compass.fit(self.readings, facing)
        if cal is None:
            self.phase, self.note = 'refused', note
            return False
        self.calibration = compass.load(compass.save(cal, self.path))
        self.phase, self.note = 'saved', note
        return True

    # ------------------------------------------------------------------ input
    def _message(self, kind, line, now):
        if kind != 'telemetry':
            return
        self.telemetry, self.telemetry_at = line, now
        if self.phase != 'recording':
            return
        self.readings.append(line)
        imu = line.get('imu') or {}
        yaw = imu.get('yaw') if imu.get('ok') and isinstance(imu.get('yaw'), (int, float)) else None
        if yaw is not None:
            if self._yaw0 is None:
                self._yaw0 = self._last_yaw = float(yaw)
            self.turned += compass.ang_diff(float(yaw), self._last_yaw)
            self._last_yaw = float(yaw)
        mag, current = line.get('mag') or {}, line.get('batt_ma')
        if not mag.get('ok') or mag.get('ovl'):
            return
        if not isinstance(current, (int, float)) or abs(current) > compass.QUIET_MA:
            self.busy += 1
            return
        self.quiet += 1
        self.points.append((float(mag['x']), float(mag['y'])))
        if yaw is not None:
            self.sectors.add(int(((float(yaw) - self._yaw0) % 360.) // SECTOR_DEG))

    # ------------------------------------------------------------------ what the window shows
    def coverage(self):
        return len(self.sectors) / round(360. / SECTOR_DEG)

    def facing(self):
        """compass.heading for the newest telemetry, or None (no calibration, or no reading)."""
        return compass.heading(self.telemetry, self.calibration) if self.calibration and self.telemetry else None

    def progress_words(self, now):
        if self.phase == 'ready':
            saved = (f'Saved calibration from {self.calibration.saved_at}.' if self.calibration
                     else 'No calibration saved yet.')
            return f'{saved} With the motor controllers off, press Start, then push him slowly round a full circle.'
        if self.phase == 'recording':
            seconds = now - self.started_at if self.started_at is not None else 0.
            words = (f'{abs(self.turned):.0f}° turned · {self.coverage():.0%} of the circle read · '
                     f'{self.quiet} readings · {seconds:.0f} s')
            if self.busy:
                words += f' · {self.busy} left out while the motors drew current'
            if self.ready_to_finish():
                words += '. A full circle: press Finish.'
            elif self.quiet == 0 and self.busy:
                words += '. The motors are drawing current: switch the motor controllers off.'
            return words
        if self.phase == 'refused':
            return f'Not saved: {self.note} Press Start to try again.'
        cal = self.calibration
        pinned = 'pinned to the direction given' if cal.told_facing_deg is not None else 'by the declination'
        extra = f' Note: {self.note}.' if self.note else ''
        return (f'Saved: field {cal.field_norm:.0f} counts from {cal.samples} readings'
                f'{", sensor axes mirrored" if cal.mirrored else ""}; north {pinned}.{extra}')
