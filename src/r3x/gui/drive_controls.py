"""Drive controls window: a clickable direction pad and W A S D keys to drive the base by hand.

    .venv/bin/python -m r3x.gui.drive_controls          started by the battery menu (Drive Controls…)

Owner, November 25: "remove the directional control pad from the menu bar helper app, but instead make a line
item to activate a gui that pops up to control the movement. The GUI should have the directional clickable
control pad, but also have awsd keyboard movement, show that on the GUI so it's obvious to a new person."

The battery menu keeps the drive controller's port while Rex is off, feeds this window its lines on stdin (as
the diagnostics windows), and reads back one line on stdout for each change and every 100 ms while a direction
is held: {"type": "drive", "x": right+, "y": forward+, "engaged": bool}. The menu turns that into drive commands
at the dropdown pad's old caps (0.25 m/s, 60°/s at full). It stops the wheels when the window lets go, closes,
or goes quiet for half a second; the firmware stops them itself 300 ms after the last command, and its range
reflexes still slow and stop him near things. With Rex running the controls are off and say why.
"""
from __future__ import annotations

import json
import queue
import sys
import time

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout,
                               QWidget)

from . import theme
from .controller_feed import ControllerFeed
from .diagnostics_window import STYLE, bring_forward, panel, run, text

RESEND_S = .1            # While a direction is held, it is said again this often (the menu stops at 0.5 s quiet).
KEYS = {Qt.Key.Key_W: 'forward', Qt.Key.Key_Up: 'forward', Qt.Key.Key_S: 'back', Qt.Key.Key_Down: 'back',
        Qt.Key.Key_A: 'left', Qt.Key.Key_Left: 'left', Qt.Key.Key_D: 'right', Qt.Key.Key_Right: 'right'}
CAPS = (('W', 'forward'), ('A', 'left'), ('S', 'back'), ('D', 'right'))

WINDOW_STYLE = STYLE + f'''
    QPushButton#padButton {{ background: #1d2a3a; color: {theme.TEXT}; border: 1px solid {theme.BORDER};
                             border-radius: 10px; font-size: 22px; font-weight: bold; min-width: 84px; min-height: 84px; }}
    QPushButton#padButton:pressed, QPushButton#padButton[held="true"] {{ background: #2f6a3a; border-color: {theme.GOOD}; }}
    QPushButton#padButton:disabled {{ background: #161b20; color: {theme.BORDER}; border-color: {theme.BORDER_DIM}; }}
    QPushButton#padStop {{ background: #5a1d1d; color: white; border: 1px solid #d05a5a; border-radius: 42px;
                           font-size: 15px; font-weight: bold; min-width: 84px; min-height: 84px; }}
    QPushButton#padStop:pressed {{ background: #9a2a2a; }}
    QLabel#keyCap {{ background: #232a24; color: {theme.TEXT}; border: 1px solid {theme.BORDER}; border-radius: 6px;
                     font-family: Menlo, monospace; font-size: 18px; font-weight: bold; min-width: 38px; min-height: 38px; }}
    QLabel#keyCap[held="true"] {{ background: #2f6a3a; border-color: {theme.GOOD}; }}
    QLabel#keyWord {{ color: {theme.TEXT_DIM}; font-size: 11px; }}'''


def stick(held, speed):
    """(x right+, y forward+) for the directions held, each in -1..1, scaled by speed (0..1)."""
    y = ('forward' in held) - ('back' in held)
    x = ('right' in held) - ('left' in held)
    return x * speed, y * speed


def describe(held):
    y = ('forward' in held) - ('back' in held)
    x = ('right' in held) - ('left' in held)
    if not x and not y:
        return 'Stopped'
    move = {1: 'forward', -1: 'back', 0: ''}[y]
    turn = {1: 'right', -1: 'left', 0: ''}[x]
    if move and turn:
        return f'Rolling {move} while turning {turn}'
    return f'Rolling {move}' if move else f'Turning {turn} on the spot'


class DriveControlsWindow(QDialog):
    def __init__(self, lines: 'queue.Queue', *, quit_on_end: bool, out=None) -> None:
        super().__init__()
        self._lines, self._quit_on_end = lines, quit_on_end
        self._out = out if out is not None else sys.stdout.buffer
        self.model = ControllerFeed()
        self.model._message = self._message
        self.pad_held, self.keys_held = set(), set()
        self._sent, self._sent_at = None, 0.
        self.setWindowTitle('Rex Drive Controls')
        self.setWindowFlags(Qt.WindowType.Window)
        self.setStyleSheet(WINDOW_STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(520, 640)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self._link = QLabel('…')
        self._link.setObjectName('motivatorConn')
        self._link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._link)

        # The pad: hold a button to drive; let go to stop.
        pad = QWidget()
        grid = QGridLayout(pad)
        grid.setSpacing(10)
        self._buttons = {}
        for direction, label, row, col in (('forward', '▲', 0, 1), ('left', '◀', 1, 0), ('right', '▶', 1, 2),
                                           ('back', '▼', 2, 1)):
            button = QPushButton(label)
            button.setObjectName('padButton')
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)    # Keys stay with the window.
            button.pressed.connect(lambda d=direction: self._pad(d, True))
            button.released.connect(lambda d=direction: self._pad(d, False))
            grid.addWidget(button, row, col)
            self._buttons[direction] = button
        stop = QPushButton('STOP')
        stop.setObjectName('padStop')
        stop.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        stop.clicked.connect(self.release_all)
        grid.addWidget(stop, 1, 1)
        root.addWidget(panel('DIRECTION PAD · hold a button to drive, let go to stop', pad), 1)

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
                     'keys). Two at once, such as <b>W</b>+<b>A</b>, curves. <b>Space</b> stops. Letting go stops.')
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

    def _pad(self, direction, down):
        if down and self._live():
            self.pad_held.add(direction)
        else:
            self.pad_held.discard(direction)
        self._send(force=True)

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
        self.pad_held.clear()
        self.keys_held.clear()
        for button in self._buttons.values():
            if button.isDown():
                button.setDown(False)
        self._send(force=True)

    # ------------------------------------------------------------------ output
    def held(self):
        return self.pad_held | self.keys_held

    def _send(self, force=False):
        now = time.monotonic()
        held = self.held() if self._live() else set()
        x, y = stick(held, self._speed.value() / 100.)
        row = {'type': 'drive', 'x': round(x, 3), 'y': round(y, 3), 'engaged': bool(held)}
        if not force and (not held or now - self._sent_at < RESEND_S):
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
        held = self.held()
        for direction, button in self._buttons.items():
            button.setEnabled(live)
            self._lit(button, direction in held)
        for direction, cap in self._caps.items():
            self._lit(cap, direction in held)
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
        colour = theme.GOOD if held else theme.TEXT
        note = ('' if live else f'<br><span style="color:{theme.WARN}">No live controller: the pad and keys do nothing.</span>')
        self._state.setText(f'<span style="color:{colour}">{describe(held)}</span>{measured}{note}'
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
