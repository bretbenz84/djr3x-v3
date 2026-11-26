"""Compass calibration window: push him round a full circle and watch it fill in.

    .venv/bin/python -m r3x.gui.compass_calibration                       fed on stdin (the battery menu does this)
    .venv/bin/python -m r3x.gui.compass_calibration --replay FILE.jsonl    a saved capture, at its own pace

Owner, November 25: "add the calibration routine to the menu bar app as a clickable option? it would launch a
small GUI that shows progress of the calibration. I would ensure the motor controllers were off while I push it."
Fed like the diagnostics windows: the battery menu holds the drive controller's port while Rex is off and passes
this window every line. It opens no port and sends nothing; the wheels never move from here.

The ring fills in tenths of the circle as he is turned (by the gyroscope), each one lit once a quiet compass
reading was taken there; the dots in the middle are the raw readings, which trace a circle (shifted and squashed
by his own iron) when the turn is good. Finish fits and saves the calibration (compass_model.CompassCalibration);
the dial then shows which way he faces.
"""
from __future__ import annotations

import math
import queue
import time

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from . import theme
from .compass_model import SECTOR_DEG, CompassCalibration
from .diagnostics_window import STYLE, bring_forward, panel, run, text

CARDINALS = ('N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW')
WINDOW_STYLE = STYLE + f'''
    QPushButton#primaryButton:disabled {{ background: #1a2029; color: {theme.TEXT_DIM}; border-color: {theme.BORDER_DIM}; }}'''


class CircleDial(QWidget):
    """Recording: the circle by the gyroscope, front up, a lit piece wherever a quiet reading was taken, the
    raw readings as dots. Saved: a compass rose with the direction he faces."""

    def __init__(self, model: CompassCalibration, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self.setMinimumSize(340, 340)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _e) -> None:   # noqa: N802 - Qt override
        model = self._model
        w, h = float(self.width()), float(self.height())
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 30
        if radius < 60:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        small = QFont(p.font()); small.setPointSize(10)
        big = QFont(p.font()); big.setPointSize(24); big.setBold(True)

        def at(degrees, r):   # Zero up; positive degrees counter-clockwise (a turn to his left).
            a = math.radians(90. + degrees)
            return QPointF(cx + r * math.cos(a), cy - r * math.sin(a))

        p.setPen(QPen(QColor(theme.BORDER), 2)); p.setBrush(QColor(theme.PANEL_BOTTOM))
        p.drawEllipse(QPointF(cx, cy), radius, radius)
        if model.phase == 'saved' or (model.phase == 'ready' and model.calibration):
            self._rose(p, at, cx, cy, radius, small, big)
            p.end()
            return
        # The circle in pieces: lit where a quiet reading was taken.
        pieces = round(360. / SECTOR_DEG)
        ring = QRectF(cx - radius + 9, cy - radius + 9, 2 * radius - 18, 2 * radius - 18)
        for index in range(pieces):
            lit = index in model.sectors
            p.setPen(QPen(QColor(theme.GOOD if lit else theme.BORDER_DIM), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            # Qt angles: 0 at three o'clock, counter-clockwise, in sixteenths. Piece 0 starts at the top.
            p.drawArc(ring, int((90. + index * SECTOR_DEG + .6) * 16), int((SECTOR_DEG - 1.2) * 16))
        # The raw readings, scaled to the middle: a good turn draws a ring.
        if model.points:
            xs, ys = [q[0] for q in model.points], [q[1] for q in model.points]
            mx, my = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1.)
            scale = (radius * .55) / (span / 2 if span > 40 else 40.)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(theme.BLUE))
            for x, y in model.points[-600:]:
                p.drawEllipse(QPointF(cx + (x - mx) * scale, cy - (y - my) * scale), 2.2, 2.2)
        if model.phase == 'recording':
            p.setPen(QPen(QColor(theme.ORANGE), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(at(model.turned, radius * .70), at(model.turned, radius - 18))
            words = f'{abs(model.turned):.0f}°'
            caption = 'turned · keep going' if not model.ready_to_finish() else 'a full circle'
        elif model.phase == 'refused':
            words, caption = 'not saved', 'see below'
        else:
            words, caption = '–', 'press Start'
        p.setFont(big); p.setPen(QColor(theme.GOOD if model.ready_to_finish() else theme.TEXT))
        p.drawText(QRectF(cx - radius, cy - 24, 2 * radius, 40), Qt.AlignmentFlag.AlignCenter, words)
        p.setFont(small); p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(cx - radius, cy + 16, 2 * radius, 20), Qt.AlignmentFlag.AlignCenter, caption)
        p.end()

    def _rose(self, p, at, cx, cy, radius, small, big) -> None:
        """North up; his heading as a needle (clockwise from north, as a compass reads)."""
        p.setFont(small)
        for index, name in enumerate(CARDINALS):
            degrees = -45. * index                          # Clockwise on screen.
            major = index % 2 == 0
            p.setPen(QPen(QColor(theme.BORDER if major else theme.BORDER_DIM), 2 if major else 1))
            p.drawLine(at(degrees, radius - (14 if major else 8)), at(degrees, radius))
            label = at(degrees, radius - 28)
            p.setPen(QColor(theme.ORANGE if name == 'N' else theme.TEXT_DIM))
            p.drawText(QRectF(label.x() - 18, label.y() - 9, 36, 18), Qt.AlignmentFlag.AlignCenter, name)
        reading = self._model.facing()
        if reading is None:
            words, caption, colour = '–', 'waiting for a compass reading', theme.TEXT_DIM
        else:
            heading = reading['heading_deg']
            p.setPen(QPen(QColor(theme.ORANGE), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(at(-heading, radius * .25), at(-heading, radius - 40))
            words = f'{heading:.0f}° {CARDINALS[int((heading + 22.5) // 45) % 8]}'
            if not reading['field_ok']:
                caption, colour = 'the field is not as calibrated: steel nearby, or recalibrate', theme.WARN
            elif not reading['motors_quiet']:
                caption, colour = 'motors drawing current: not to be trusted now', theme.WARN
            else:
                caption, colour = 'which way he faces', theme.TEXT
        p.setFont(big); p.setPen(QColor(colour))
        p.drawText(QRectF(cx - radius, cy - 20, 2 * radius, 40), Qt.AlignmentFlag.AlignCenter, words)
        p.setFont(small); p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(cx - radius, cy + 20, 2 * radius, 20), Qt.AlignmentFlag.AlignCenter, caption)


class CompassCalibrationWindow(QDialog):
    def __init__(self, lines: 'queue.Queue', *, quit_on_end: bool, path=None) -> None:
        super().__init__()
        self._lines, self._quit_on_end = lines, quit_on_end
        self.model = CompassCalibration(path)
        self.setWindowTitle('Rex Compass Calibration')
        self.setWindowFlags(Qt.WindowType.Window)
        self.setStyleSheet(WINDOW_STYLE)
        self.resize(560, 720)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self._link = QLabel('…')
        self._link.setObjectName('motivatorConn')
        self._link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._link)

        self._dial = CircleDial(self.model)
        root.addWidget(panel('THE TURN · front up while recording, north up once saved', self._dial), 1)

        self._progress = text()
        root.addWidget(panel('PROGRESS', self._progress))

        facing_row = QHBoxLayout()
        label = QLabel('Facing at the end (optional):'); label.setObjectName('tofText')
        self._facing = QLineEdit()
        self._facing.setObjectName('messageEntry')
        self._facing.setPlaceholderText('true heading in degrees, e.g. 345 = 15° left of north')
        facing_row.addWidget(label); facing_row.addWidget(self._facing, 1)
        root.addLayout(facing_row)

        buttons = QHBoxLayout()
        self._start = QPushButton('Start'); self._start.setObjectName('primaryButton')
        self._finish = QPushButton('Finish && Save'); self._finish.setObjectName('primaryButton')
        self._again = QPushButton('Start Over'); self._again.setObjectName('topControlButton')
        for button in (self._start, self._finish, self._again):
            buttons.addWidget(button)
        root.addLayout(buttons)
        self._start.clicked.connect(self._on_start)
        self._again.clicked.connect(self._on_start)
        self._finish.clicked.connect(self._on_finish)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)
        self._tick()

    def _on_start(self) -> None:
        self.model.start(time.monotonic())
        self._show(time.monotonic())

    def _on_finish(self) -> None:
        raw = self._facing.text().strip().rstrip('°').strip()
        facing = None
        if raw:
            try:
                facing = float(raw) % 360.
            except ValueError:
                self._progress.setText(f'<span style="color:{theme.WARN}">"{raw}" is not a number of degrees '
                                       '(0 north, 90 east). Leave it empty if nobody knows.</span>')
                return
        self.model.finish(facing)
        self._show(time.monotonic())

    def _tick(self) -> None:
        ended = False
        while True:
            try:
                at, line = self._lines.get_nowait()
            except queue.Empty:
                break
            if line is None:
                ended = True
                break
            self.model.feed_line(line, at)
        if ended and self._quit_on_end:   # The battery menu went away: nothing true left to show.
            self.close()
            return
        if self.model.raise_wanted:
            self.model.raise_wanted = False
            bring_forward(self)
        self._show(time.monotonic())

    def _show(self, now: float) -> None:
        model = self.model
        live, words = model.link(now)
        self._link.setText(words)
        if self._link.property('ok') != live:
            self._link.setProperty('ok', live)
            self._link.style().unpolish(self._link); self._link.style().polish(self._link)
        colour = {'refused': theme.BAD, 'saved': theme.GOOD}.get(model.phase, theme.TEXT)
        self._progress.setText(f'<span style="color:{colour}">{model.progress_words(now)}</span>')
        recording = model.phase == 'recording'
        self._start.setVisible(not recording and model.phase != 'saved' and model.phase != 'refused')
        self._start.setEnabled(live)
        self._again.setVisible(recording or model.phase in ('saved', 'refused'))
        self._again.setEnabled(live)
        self._finish.setVisible(recording)
        self._finish.setEnabled(model.ready_to_finish())
        self._dial.update()


def main(argv=None) -> int:
    return run(CompassCalibrationWindow, 'Rex Compass Calibration', __doc__.split('\n')[0], argv)


if __name__ == '__main__':
    raise SystemExit(main())
