"""Drive controls window: a round drag stick and W A S D keys to drive the base by hand.

    .venv/bin/python -m r3x.gui.drive_controls          started by the battery menu (Drive Controls…)

Owner, November 25: "remove the directional control pad from the menu bar helper app, but instead make a line
item to activate a gui that pops up to control the movement. The GUI should have the directional clickable
control pad, but also have awsd keyboard movement, show that on the GUI so it's obvious to a new person."
Owner, later that day: "your direction pad can't do angled drives. Can you make it a circular pad so I can drag
the center anywhere and it goes that direction". The four arrows became a round stick: press anywhere in the
circle and drag; up is forward, the sides turn, and anywhere between mixes the two in proportion. Letting go
springs it back to the middle and stops.

The battery menu keeps the drive controller's port while Rex is off, feeds this window its lines on stdin (as
the diagnostics windows), and reads back one line on stdout for each change and every 100 ms while a direction
is held: {"type": "drive", "x": right+, "y": forward+, "engaged": bool}, each -1..1. The menu turns that into drive commands
at the dropdown pad's old caps (0.25 m/s, 60°/s at full). It stops the wheels when the window lets go, closes,
or goes quiet for half a second; the firmware stops them itself 300 ms after the last command, and its range
reflexes still slow and stop him near things. With Rex running the controls are off and say why.
"""
from __future__ import annotations

import json
import math
import queue
import sys
import time

from PySide6.QtCore import QEvent, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider,
                               QVBoxLayout, QWidget)

from . import theme
from .controller_feed import ControllerFeed
from .diagnostics_window import STYLE, bring_forward, panel, run, text

STICK_DEADZONE = .08     # Of the circle's radius: a press right in the middle does not creep.
RESEND_S = .1            # While a direction is held, it is said again this often (the menu stops at 0.5 s quiet).
KEYS = {Qt.Key.Key_W: 'forward', Qt.Key.Key_Up: 'forward', Qt.Key.Key_S: 'back', Qt.Key.Key_Down: 'back',
        Qt.Key.Key_A: 'left', Qt.Key.Key_Left: 'left', Qt.Key.Key_D: 'right', Qt.Key.Key_Right: 'right'}
CAPS = (('W', 'forward'), ('A', 'left'), ('S', 'back'), ('D', 'right'))

WINDOW_STYLE = STYLE + f'''
    QPushButton#padStop {{ background: #5a1d1d; color: white; border: 1px solid #d05a5a; border-radius: 8px;
                           font-size: 15px; font-weight: bold; min-width: 120px; min-height: 34px; }}
    QPushButton#padStop:pressed {{ background: #9a2a2a; }}
    QLabel#keyCap {{ background: #232a24; color: {theme.TEXT}; border: 1px solid {theme.BORDER}; border-radius: 6px;
                     font-family: Menlo, monospace; font-size: 18px; font-weight: bold; min-width: 38px; min-height: 38px; }}
    QLabel#keyCap[held="true"] {{ background: #2f6a3a; border-color: {theme.GOOD}; }}
    QLabel#keyWord {{ color: {theme.TEXT_DIM}; font-size: 11px; }}'''


def stick(held, speed):
    """(x right+, y forward+) for the keys held, each in -1..1, scaled by speed (0..1)."""
    y = ('forward' in held) - ('back' in held)
    x = ('right' in held) - ('left' in held)
    return x * speed, y * speed


def describe(x, y):
    """What a stick position does, in words (x right+, y forward+)."""
    size = math.hypot(x, y)
    if size < 1e-6:
        return 'Stopped'
    move = '' if abs(y) < .2 * size else ('forward' if y > 0 else 'back')
    turn = '' if abs(x) < .2 * size else ('right' if x > 0 else 'left')
    if move and turn:
        return f'Rolling {move} while turning {turn}'
    return f'Rolling {move}' if move else f'Turning {turn} on the spot'


class DriveStick(QWidget):
    """A round stick: press anywhere in the circle and drag; the knob follows, held inside the rim. Up is
    forward, down is back, the sides turn on the spot, and anywhere between mixes the two in proportion. Letting
    go springs it back to the middle. vector is (x right+, y forward+) within the unit circle."""

    moved = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.vector = (0., 0.)
        self.grabbed = False
        self.live = False
        self.shown = (0., 0.)       # Where the knob is drawn: the drag, or the keys held.
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)   # Keys stay with the window.
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def _circle(self):
        w, h = float(self.width()), float(self.height())
        radius = max(min(w, h) / 2 - 30, 40.)
        return w / 2, h / 2, radius, radius * .24

    def vector_at(self, pos):
        cx, cy, radius, _knob = self._circle()
        x, y = (pos.x() - cx) / radius, (cy - pos.y()) / radius
        size = math.hypot(x, y)
        if size < STICK_DEADZONE:
            return 0., 0.
        if size > 1.:
            x, y = x / size, y / size
        return round(x, 3), round(y, 3)

    def mousePressEvent(self, event):   # noqa: N802 - Qt override
        cx, cy, radius, knob = self._circle()
        pos = event.position()
        if (not self.live or event.button() != Qt.MouseButton.LeftButton
                or math.hypot(pos.x() - cx, pos.y() - cy) > radius + knob):
            return super().mousePressEvent(event)
        self.grabbed = True
        self.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.vector = self.vector_at(pos)
        self.moved.emit()
        self.update()

    def mouseMoveEvent(self, event):   # noqa: N802 - Qt override
        if not self.grabbed:
            return super().mouseMoveEvent(event)
        vector = self.vector_at(event.position())
        if vector != self.vector:
            self.vector = vector
            self.moved.emit()
            self.update()

    def mouseReleaseEvent(self, event):   # noqa: N802 - Qt override
        if not self.grabbed or event.button() != Qt.MouseButton.LeftButton:
            return super().mouseReleaseEvent(event)
        self.let_go()
        self.moved.emit()

    def let_go(self):
        self.grabbed, self.vector = False, (0., 0.)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.update()

    def paintEvent(self, _e) -> None:   # noqa: N802 - Qt override
        cx, cy, radius, knob = self._circle()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        dim = not self.live
        # The base, a half-way ring and the cross.
        p.setPen(QPen(QColor(theme.BORDER_DIM if dim else theme.BORDER), 2))
        p.setBrush(QColor('#161b20' if dim else '#1d2a3a'))
        p.drawEllipse(QPointF(cx, cy), radius, radius)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(theme.BORDER_DIM), 1, Qt.PenStyle.DashLine))
        p.drawEllipse(QPointF(cx, cy), radius / 2, radius / 2)
        p.drawLine(QPointF(cx - radius, cy), QPointF(cx + radius, cy))
        p.drawLine(QPointF(cx, cy - radius), QPointF(cx, cy + radius))
        # The words round the rim.
        small = QFont(p.font()); small.setPointSize(10); small.setBold(True)
        p.setFont(small)
        p.setPen(QColor(theme.BORDER if dim else theme.TEXT_DIM))
        for words, (dx, dy) in (('FORWARD', (0, -1)), ('BACK', (0, 1)), ('TURN LEFT', (-1, 0)),
                                ('TURN RIGHT', (1, 0))):
            if dy:
                box = QRectF(cx - 80, cy + dy * (radius + 15) - 9, 160, 18)
            else:
                box = QRectF(cx + dx * (radius - 50) - 45, cy - 22, 90, 18)   # Inside the rim, above the line.
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, words)
        # The knob, and a line from the middle while it is pushed.
        x, y = self.vector if self.grabbed else self.shown
        kx, ky = cx + x * radius, cy - y * radius
        on = abs(x) > 1e-6 or abs(y) > 1e-6
        if on:
            p.setPen(QPen(QColor(theme.GOOD), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(QPointF(cx, cy), QPointF(kx, ky))
        p.setPen(QPen(QColor(theme.GOOD if on else (theme.BORDER_DIM if dim else theme.BORDER)), 2))
        p.setBrush(QColor('#2f6a3a' if on else ('#20262c' if dim else '#34485e')))
        p.drawEllipse(QPointF(kx, ky), knob, knob)
        p.end()


class DriveControlsWindow(QDialog):
    def __init__(self, lines: 'queue.Queue', *, quit_on_end: bool, out=None) -> None:
        super().__init__()
        self._lines, self._quit_on_end = lines, quit_on_end
        self._out = out if out is not None else sys.stdout.buffer
        self.model = ControllerFeed()
        self.model._message = self._message
        self.keys_held = set()
        self._sent, self._sent_at = None, 0.
        self.setWindowTitle('Rex Drive Controls')
        self.setWindowFlags(Qt.WindowType.Window)
        self.setStyleSheet(WINDOW_STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(520, 760)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self._link = QLabel('…')
        self._link.setObjectName('motivatorConn')
        self._link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._link)

        # The stick: press anywhere in the circle and drag; let go to stop.
        pad = QWidget()
        pad_lay = QVBoxLayout(pad)
        pad_lay.setSpacing(8)
        self.stick = DriveStick()
        self.stick.moved.connect(lambda: self._send(force=True))
        pad_lay.addWidget(self.stick, 1)
        stop = QPushButton('STOP')
        stop.setObjectName('padStop')
        stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        stop.clicked.connect(self.release_all)
        stop_row = QHBoxLayout()
        stop_row.addStretch(1); stop_row.addWidget(stop); stop_row.addStretch(1)
        pad_lay.addLayout(stop_row)
        root.addWidget(panel('STICK · press anywhere in the circle and drag the way to go; let go to stop', pad), 1)

        # The keys, drawn as keys, lit while held.
        keys = QWidget()
        keys_lay = QVBoxLayout(keys)
        keys_lay.setSpacing(6)
        caps = QGridLayout()
        caps.setSpacing(6)
        self._caps = {}
        for letter, direction, row, col in (('W', 'forward', 0, 1), ('A', 'left', 1, 0), ('S', 'back', 1, 1),
                                            ('D', 'right', 1, 2)):
            cap = QLabel(letter)
            cap.setObjectName('keyCap')
            cap.setAlignment(Qt.AlignmentFlag.AlignCenter)
            word = QLabel({'left': 'turn left', 'right': 'turn right'}.get(direction, direction))
            word.setObjectName('keyWord')
            word.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell = QVBoxLayout(); cell.setSpacing(2)
            cell.addWidget(cap); cell.addWidget(word)
            holder = QWidget(); holder.setLayout(cell)
            caps.addWidget(holder, row, col)
            self._caps[direction] = cap
        caps_row = QHBoxLayout()
        caps_row.addStretch(1); caps_row.addLayout(caps); caps_row.addStretch(1)
        keys_lay.addLayout(caps_row)
        hint = text(dim=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setText('Hold <b>W</b> forward, <b>S</b> back, <b>A</b> turn left, <b>D</b> turn right (or the arrow '
                     'keys). Two at once, such as <b>W</b>+<b>A</b>, curves; the stick does any angle. <b>Space</b> stops. Letting go stops.')
        keys_lay.addWidget(hint)
        root.addWidget(panel('KEYBOARD · click this window first so it has the keys', keys))

        # Speed and what he is doing.
        speed_row = QHBoxLayout()
        speed_label = QLabel('Speed'); speed_label.setObjectName('tofText')
        self._speed = QSlider(Qt.Orientation.Horizontal)
        self._speed.setRange(20, 100)
        self._speed.setValue(60)
        self._speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._speed_words = QLabel(''); self._speed_words.setObjectName('tofDim')
        self._speed.valueChanged.connect(lambda _v: self._send(force=True))
        speed_row.addWidget(speed_label); speed_row.addWidget(self._speed, 1); speed_row.addWidget(self._speed_words)
        root.addLayout(speed_row)
        self._state = text()
        root.addWidget(panel('NOW', self._state))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(50)
        self._tick()

    # ------------------------------------------------------------------ input
    def _message(self, kind, line, now):
        if kind == 'telemetry':
            self.model.telemetry, self.model.telemetry_at = line, now

    def _live(self):
        return self.model.live(time.monotonic())

    def keyPressEvent(self, event):   # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Space:
            self.release_all()
            return
        direction = KEYS.get(event.key())
        if direction is None:
            return super().keyPressEvent(event)
        if not event.isAutoRepeat() and self._live():
            self.keys_held.add(direction)
            self._send(force=True)

    def keyReleaseEvent(self, event):   # noqa: N802 - Qt override
        direction = KEYS.get(event.key())
        if direction is None:
            return super().keyReleaseEvent(event)
        if not event.isAutoRepeat():
            self.keys_held.discard(direction)
            self._send(force=True)

    def changeEvent(self, event):   # noqa: N802 - Qt override
        # Another window took the keyboard: a key let go there would never reach here, so stop now.
        if event.type() == QEvent.Type.ActivationChange and not self.isActiveWindow():
            self.release_all()
        super().changeEvent(event)

    def closeEvent(self, event):   # noqa: N802 - Qt override
        self.release_all()
        super().closeEvent(event)

    def release_all(self):
        self.keys_held.clear()
        self.stick.let_go()
        self._send(force=True)

    # ------------------------------------------------------------------ output
    def held(self):
        """True while the stick is dragged or a key is down."""
        return self.stick.grabbed or bool(self.keys_held)

    def direction(self):
        """(x right+, y forward+) asked for, before the speed slider: the stick while dragged, else the keys."""
        if self.stick.grabbed:
            return self.stick.vector
        return stick(self.keys_held, 1.)

    def _send(self, force=False):
        now = time.monotonic()
        x, y = self.direction() if self._live() else (0., 0.)
        speed = self._speed.value() / 100.
        x, y = x * speed, y * speed
        engaged = bool(x or y)
        row = {'type': 'drive', 'x': round(x, 3), 'y': round(y, 3), 'engaged': engaged}
        if not force and (not engaged or now - self._sent_at < RESEND_S):
            return    # Changes go at once (force); a held direction is said again every RESEND_S.
        try:
            self._out.write((json.dumps(row) + '\n').encode())
            self._out.flush()
        except (BrokenPipeError, OSError, ValueError):
            self.close()      # The battery menu went away: nothing to drive through.
            return
        self._sent, self._sent_at = row, now

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
        if ended and self._quit_on_end:
            self.close()
            return
        if self.model.raise_wanted:
            self.model.raise_wanted = False
            bring_forward(self)
        live = self._live()
        if not live and self.held():
            self.release_all()
        self._send()
        self._show(live)

    def _show(self, live):
        now = time.monotonic()
        _, words = self.model.link(now)
        self._link.setText(words if live else words + ' Driving is off until it is live.')
        if self._link.property('ok') != live:
            self._link.setProperty('ok', live)
            self._link.style().unpolish(self._link); self._link.style().polish(self._link)
        x, y = self.direction() if live else (0., 0.)
        self.stick.live = live
        size = math.hypot(x, y)
        self.stick.shown = (x / size, y / size) if size > 1. else (x, y)   # W+A sits on the rim, not past it.
        self.stick.setEnabled(live)
        self.stick.update()
        for direction, cap in self._caps.items():
            self._lit(cap, direction in self.keys_held)
        speed = self._speed.value() / 100.
        self._speed_words.setText(f'{.25 * speed:.2f} m/s · {60 * speed:.0f}°/s')
        telemetry = self.model.telemetry or {}
        odom = telemetry.get('odom') or {}
        measured = ''
        if live and isinstance(odom.get('lin'), (int, float)):
            measured = f'<br><span style="color:{theme.TEXT_DIM}">Wheels report {odom["lin"]:+.2f} m/s'
            if isinstance(odom.get('ang'), (int, float)):
                measured += f', turning {odom["ang"] * 57.3:+.0f}°/s'
            measured += '</span>'
        colour = theme.GOOD if size else theme.TEXT
        amount = f' · {min(size, 1.) * speed:.0%} of full' if size else ''
        note = ('' if live else f'<br><span style="color:{theme.WARN}">No live controller: the stick and keys do nothing.</span>')
        self._state.setText(f'<span style="color:{colour}">{describe(x, y)}{amount}</span>{measured}{note}'
                            f'<br><span style="color:{theme.TEXT_DIM}">His range sensors still slow and stop him '
                            'near things.</span>')

    @staticmethod
    def _lit(widget, on):
        if widget.property('held') != on:
            widget.setProperty('held', on)
            widget.style().unpolish(widget); widget.style().polish(widget)


def main(argv=None) -> int:
    return run(DriveControlsWindow, 'Rex Drive Controls', __doc__.split('\n')[0], argv)


if __name__ == '__main__':
    raise SystemExit(main())
