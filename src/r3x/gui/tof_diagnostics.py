"""ToF diagnostics window: every range reading the drive controller sends, live.

    .venv/bin/python -m r3x.gui.tof_diagnostics                       fed on stdin (the battery menu does this)
    .venv/bin/python -m r3x.gui.tof_diagnostics --replay FILE.jsonl    a saved capture, at its own pace

Owner, November 20: "a GUI diagnostics tool that shows all the ToF data live, launchable from the menu
bar helper app". The battery menu holds the drive controller's port whenever Rex is off, so it starts
this window and passes it every line it reads; this process never opens the port and sends nothing.
While Rex runs he holds the port, and the window says so until he shuts down.

It shows the eight ring sensors from above and one by one (reading, the last minute, dropouts), both
8x8 depth sensors, the two front corner sensors' raw answers, the controller's own messages, and what
stands out in words (tof_model.TofDiagnostics.notices).
"""
from __future__ import annotations

import math
import queue
import time

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .diagnostics_window import STYLE, bring_forward, notice_rows, panel, run, text
from .motivator import TofMatrixWidget
from .tof_model import HISTORY_S, MATRIX_CLEAR_MM, POLL_NORMAL_MS, POLL_SLOW_MS, RANGE_STATUS, RING, TofDiagnostics

STATUS_WORDS = {'waiting': 'waiting', 'reading': '', 'open': 'nothing in range', 'discarded': 'discarded',
                'silent': 'no answer'}
STATUS_COLOR = {'waiting': theme.TEXT_DIM, 'reading': theme.GOOD, 'open': theme.GOOD, 'discarded': theme.WARN,
                'silent': theme.BAD}


def distance(mm) -> str:
    return f'{mm} mm' if mm < 1000 else f'{mm / 1000:.2f} m'


class RingView(QWidget):
    """Rex from above, front up: where each ring sensor sits, whether it answers and how far it sees,
    with the two 8x8 sensors as the wide fans at the front and back. Beams are drawn to a square-root
    scale capped at RING_VIEW_MM so 15 cm and 2 m both show; the numbers are the truth."""

    RING_VIEW_MM = 2000

    def __init__(self, model: TofDiagnostics, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self.setMinimumSize(320, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _e) -> None:   # noqa: N802 - Qt override
        now = time.monotonic()
        model = self._model
        w, h = float(self.width()), float(self.height())
        cx, cy = w / 2, h / 2
        outer = min(w / 2 - 50, h / 2 - 36)        # Where the labels sit.
        if outer < 70:
            return
        body = max(30., outer * .26)
        reach = outer - 24 - body
        live = model.live(now)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        small = QFont(p.font()); small.setPointSize(9)
        bold = QFont(small); bold.setBold(True)

        def at(bearing, radius):
            a = math.radians(90. + bearing)       # Front is up, his left is the picture's left.
            return QPointF(cx + radius * math.cos(a), cy - radius * math.sin(a))

        def radius(mm):
            return body + math.sqrt(min(mm, self.RING_VIEW_MM) / self.RING_VIEW_MM) * reach

        def fan(bearing, half, r, colour, alpha):
            fill = QColor(colour); fill.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(fill)
            p.drawPie(QRectF(cx - r, cy - r, 2 * r, 2 * r), int((90. + bearing - half) * 16), int(2 * half * 16))

        p.setFont(small)
        for mm in (500, 1000, 2000):               # Reference rings.
            r = radius(mm)
            p.setPen(QPen(QColor(theme.BORDER_DIM), 1, Qt.PenStyle.DashLine)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)
            p.setPen(QColor(theme.BLUE_DIM))
            # Named along his right, between the two right-hand sensors; staggered so they never touch.
            where = {500: QRectF(cx + r - 40, cy - 14, 38, 13), 1000: QRectF(cx + r - 40, cy + 1, 38, 13),
                     2000: QRectF(cx + r + 3, cy - 14, 38, 13)}[mm]
            p.drawText(where, Qt.AlignmentFlag.AlignLeft if mm == self.RING_VIEW_MM else Qt.AlignmentFlag.AlignRight,
                       f'{mm / 1000:g} m' + ('+' if mm == self.RING_VIEW_MM else ''))
        for matrix, bearing, edge in ((model.front, 0., Qt.AlignmentFlag.AlignTop), (model.rear, 180., Qt.AlignmentFlag.AlignBottom)):
            age, near = matrix.age(now), matrix.nearest_mm()
            fresh = live and age is not None and age <= 1.
            if fresh:
                fan(bearing, 22.5, radius(near), theme.BLUE, 38)
            words = (f'{matrix.name}: nearest {"nothing" if near >= MATRIX_CLEAR_MM else distance(near)}' if fresh
                     else f'{matrix.name}: no frames')
            p.setPen(QColor(theme.TEXT_DIM if fresh else theme.BAD))
            p.setFont(small)
            p.drawText(QRectF(0, 2, w, h - 4), Qt.AlignmentFlag.AlignHCenter | edge, words)
        for key, _name, bearing, *_rest in RING:
            sensor = model.sensors[key]
            status = model.status(key, now) if live else 'waiting'
            colour = QColor(STATUS_COLOR[status])
            if status in ('reading', 'open'):
                fan(bearing, 6.5, radius(sensor.value), theme.GOOD if status == 'reading' else theme.BLUE_DIM,
                    150 if status == 'reading' else 70)
            spot = at(bearing, body)
            if status in ('silent', 'discarded'):
                p.setPen(QPen(colour, 3))
                p.drawLine(spot + QPointF(-6, -6), spot + QPointF(6, 6))
                p.drawLine(spot + QPointF(-6, 6), spot + QPointF(6, -6))
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(colour)
                p.drawEllipse(spot, 4.5, 4.5)
            label = at(bearing, outer)
            box = QRectF(label.x() - 54, label.y() - 15, 108, 30)
            p.setFont(bold); p.setPen(QColor(theme.TEXT))
            p.drawText(box, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, sensor.name)
            p.setFont(small); p.setPen(colour if status != 'reading' else QColor(theme.BLUE))
            p.drawText(box, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom,
                       distance(sensor.value) if status == 'reading' else STATUS_WORDS[status])
        p.setPen(QPen(QColor(theme.BORDER), 2)); p.setBrush(QColor(theme.PANEL_BOTTOM))
        p.drawEllipse(QPointF(cx, cy), body - 9, body - 9)
        p.setPen(QPen(QColor(theme.ORANGE), 2.5))   # The chevron points to his front.
        p.drawLine(QPointF(cx - body * .3, cy + body * .12), QPointF(cx, cy - body * .3))
        p.drawLine(QPointF(cx, cy - body * .3), QPointF(cx + body * .3, cy + body * .12))
        p.end()


class SensorStrips(QWidget):
    """One row per ring sensor: how it is doing now and its last minute as a trace (up is farther).
    A stretch with no answer is a red band, so a dropout while a cable is moved stays visible."""

    def __init__(self, model: TofDiagnostics, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self.setMinimumSize(560, 8 * 46 + 22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _e) -> None:   # noqa: N802 - Qt override
        now = time.monotonic()
        w, h = float(self.width()), float(self.height())
        foot = 18.
        row_h = (h - foot) / len(RING)
        info_w = min(372., w * .56)
        value_x = 188.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        name_font = QFont(p.font()); name_font.setPointSize(12); name_font.setBold(True)
        small = QFont(p.font()); small.setPointSize(9)
        value_font = QFont(p.font()); value_font.setPointSize(13); value_font.setBold(True)
        for index, (key, *_rest) in enumerate(RING):
            sensor = self._model.sensors[key]
            status = self._model.status(key, now)
            colour = QColor(STATUS_COLOR[status])
            top = index * row_h
            if index % 2 == 0:
                p.fillRect(QRectF(0, top, w, row_h), QColor(255, 255, 255, 8))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(colour)
            p.drawEllipse(QPointF(12, top + row_h / 2), 5.5, 5.5)
            p.setPen(QColor(theme.TEXT))
            p.setFont(name_font)
            p.drawText(QRectF(26, top + 3, value_x - 30, row_h / 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, sensor.name)
            p.setPen(QColor(theme.TEXT_DIM))
            p.setFont(small)
            p.drawText(QRectF(26, top + row_h / 2, value_x - 30, row_h / 2 - 3), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                       f'channel {sensor.channel} · {sensor.kind} range')
            p.setFont(value_font)
            p.setPen(colour if status != 'reading' else QColor(theme.BLUE))
            words = distance(sensor.value) if status == 'reading' else STATUS_WORDS[status]
            p.drawText(QRectF(value_x, top, info_w - value_x, row_h / 2 + 4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, words)
            p.setFont(small)
            p.setPen(QColor(theme.TEXT_DIM))
            span = sensor.span()
            if span is None:
                detail = 'no reading in the last minute'
            elif span[0] >= sensor.clear_mm:
                detail = 'nothing all minute'
            else:
                detail = f'{distance(span[0])} to ' + ('nothing' if span[1] >= sensor.clear_mm else distance(span[1]))
            if sensor.silent_since is not None and status in ('silent', 'discarded'):
                detail = ('none while watching' if sensor.read_at is None
                          else f'for {_ago(now - sensor.silent_since)}' + ('' if span is None else f' · was {detail}'))
            if sensor.dropouts:
                detail += f' · dropped ×{sensor.dropouts}'
            p.drawText(QRectF(value_x, top + row_h / 2 + 4, info_w - value_x, row_h / 2 - 6),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, detail)
            self._trace(p, sensor, QRectF(info_w + 6, top + 5, w - info_w - 12, row_h - 10), now)
        p.setFont(small)
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(info_w + 6, h - foot, w - info_w - 12, foot), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f'{HISTORY_S:.0f} s ago')
        p.drawText(QRectF(info_w + 6, h - foot, w - info_w - 12, foot), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 'now')
        p.drawText(QRectF(26, h - foot, info_w - 26, foot), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   'Traces: up is farther, a red band is no answer.')
        p.end()

    @staticmethod
    def _trace(p: QPainter, sensor, box: QRectF, now: float) -> None:
        p.setPen(QPen(QColor(theme.BORDER_DIM), 1))
        p.setBrush(QColor(0, 0, 0, 50))
        p.drawRect(box)
        if not sensor.history:
            return

        def x_at(at):
            return box.right() - (now - at) / HISTORY_S * box.width()

        line, previous = QPolygonF(), None
        red = QColor(theme.BAD); red.setAlpha(110)
        for at, mm in sensor.history:
            x = max(box.left(), x_at(at))
            if mm < 0:
                start = x if previous is None else max(box.left(), x_at(previous))
                p.fillRect(QRectF(start, box.top() + 1, max(1.5, x - start), box.height() - 2), red)
                if line.size() > 1:
                    p.setPen(QPen(QColor(theme.BLUE), 1.4)); p.drawPolyline(line)
                line = QPolygonF()
            else:
                share = min(1., mm / sensor.clear_mm)
                line.append(QPointF(x, box.bottom() - 2 - share * (box.height() - 4)))
            previous = at
        if line.size() > 1:
            p.setPen(QPen(QColor(theme.BLUE), 1.4)); p.drawPolyline(line)
        elif line.size() == 1:
            p.setPen(QPen(QColor(theme.BLUE), 3)); p.drawPoint(line.at(0))


def _ago(seconds: float) -> str:
    return f'{seconds:.0f} s' if seconds < 90 else f'{seconds / 60:.0f} min'


class TofDiagnosticsWindow(QDialog):
    def __init__(self, lines: 'queue.Queue', *, quit_on_end: bool) -> None:
        super().__init__()
        self._lines, self._quit_on_end = lines, quit_on_end
        self.model = TofDiagnostics()
        self._logged = 0
        self.setWindowTitle('Rex ToF Diagnostics')
        self.setWindowFlags(Qt.WindowType.Window)
        self.setStyleSheet(STYLE)
        self.resize(1480, 900)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        self._link = QLabel('…')
        self._link.setObjectName('motivatorConn')
        self._link.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self._link)

        body = QHBoxLayout()
        body.setSpacing(12)
        root.addLayout(body, 1)

        left = QVBoxLayout(); left.setSpacing(12)
        self._ring = RingView(self.model)
        left.addWidget(self._panel("FROM ABOVE · each sensor's own reading", self._ring), 7)
        self._notices = self._text()
        self._notices.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        left.addWidget(self._panel('WHAT STANDS OUT', self._notices), 3)
        body.addLayout(left, 5)

        mid = QVBoxLayout(); mid.setSpacing(12)
        self._strips = SensorStrips(self.model)
        mid.addWidget(self._panel('RING SENSORS · counter-clockwise from the front', self._strips), 7)
        corners = QWidget(); lay = QVBoxLayout(corners); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
        self._raw = {key: self._text() for key in ('fl', 'fr')}
        for label in self._raw.values():
            lay.addWidget(label)
        self._poll = self._text(dim=True); lay.addWidget(self._poll)
        self._fused = self._text(dim=True); lay.addWidget(self._fused)
        lay.addStretch(1)
        mid.addWidget(self._panel('FRONT CORNER SENSORS · what the sensor says, and what the controller passes on', corners), 3)
        body.addLayout(mid, 7)

        right = QVBoxLayout(); right.setSpacing(12)
        self._grids, self._health = {}, {}
        for name, title, orientation in (('front', '8×8 FRONT', 'top=up · left=robot L'),
                                         ('rear', '8×8 REAR · as the sensor sees it', 'top=up · left=robot R')):
            box = QWidget(); lay = QVBoxLayout(box); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(4)
            self._grids[name] = TofMatrixWidget(orientation=orientation)
            self._health[name] = self._text(dim=True)
            lay.addWidget(self._grids[name], 1); lay.addWidget(self._health[name])
            right.addWidget(self._panel(title, box), 1)
        body.addLayout(right, 5)

        self._log = QPlainTextEdit()
        self._log.setObjectName('tofLog')
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(200)
        self._log.setFixedHeight(96)
        self._log.setPlaceholderText('Messages from the drive controller appear here.')
        root.addWidget(self._panel('CONTROLLER MESSAGES', self._log))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)
        self._tick()

    _panel, _text = staticmethod(panel), staticmethod(text)

    # ------------------------------------------------------------------ refresh
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
        if ended and self._quit_on_end:   # The battery menu went away: a window with no feed shows nothing true.
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
        telemetry = model.telemetry or {}
        self._ring.update()
        self._strips.update()
        for name, matrix in (('front', model.front), ('rear', model.rear)):
            age = matrix.age(now)
            if matrix.grid and age is not None and age <= 2.:
                self._grids[name].set_frame(matrix.grid, matrix.rej)
            else:
                self._grids[name].clear()
            self._health[name].setText(self._matrix_words(matrix, now))
        for key, label in self._raw.items():
            label.setText(self._raw_words(key, now))
        poll = model.poll_ms()
        if poll is None:
            self._poll.setText('Read rate of the ring: not known yet (it comes with the raw stream).')
        else:
            colour = theme.WARN if poll >= POLL_SLOW_MS else theme.TEXT_DIM
            self._poll.setText(f'<span style="color:{colour}">Each ring sensor is read every {poll:.0f} ms</span> '
                               f'(about {POLL_NORMAL_MS} ms when none holds up the rest).')
        fused = model.fused
        if fused and live:
            names = (('fl', 'front-left'), ('fr', 'front-right'), ('rl', 'rear-left'), ('rr', 'rear-right'))
            parts = ' · '.join(f'{name} {"none" if fused[k] < 0 else distance(int(fused[k]))}' for k, name in names if k in fused)
            self._fused.setText(f'What the stop reflex uses (corner sensor and 8×8 together): {parts}. '
                                f'Zone {telemetry.get("zone", "?")}, blocked {telemetry.get("blocked_dir", "?")}.')
        else:
            self._fused.setText('')
        self._notices.setText(notice_rows(model.notices(now)))
        fresh = min(model.log_total - self._logged, len(model.log))
        for _at, level, text in list(model.log)[len(model.log) - fresh:]:
            self._log.appendPlainText(f'{time.strftime("%H:%M:%S")}  {level:<5} {text}')
        self._logged = model.log_total

    @staticmethod
    def _matrix_words(matrix, now: float) -> str:
        age = matrix.age(now)
        if age is None:
            return 'No frames yet.'
        words = f'{matrix.rate(now):.1f} frames a second'
        if age > 1.:
            words = f'<span style="color:{theme.BAD}">nothing for {age:.1f} s</span>'
        if matrix.last_stall:
            words += f' · last stall {matrix.last_stall[1]:.1f} s, {_ago(now - matrix.last_stall[0])} ago ({matrix.stalls} so far)'
        else:
            words += ' · no stall while watching'
        return words

    def _raw_words(self, key: str, now: float) -> str:
        sensor, raw = self.model.sensors[key], self.model.raw[key]
        if raw.last is None or raw.at is None or now - raw.at > 3.:
            return f'<b>{sensor.name}</b>: no raw stream (it runs only while the wheels are still).'
        last = raw.last
        status = last.get('status')
        said = RANGE_STATUS.get(status, f'status {status}')
        good = status in (0, 3)
        colour = theme.GOOD if good else theme.WARN
        published = last.get('filtered_mm')
        passed = (f'<span style="color:{theme.BAD}">nothing</span>' if not isinstance(published, (int, float)) or published < 0
                  else distance(int(published)))
        signal = last.get('sig')
        strength = f', signal {signal:.1f}' if isinstance(signal, (int, float)) and signal >= 0 else ''
        raw_mm = last.get('raw_mm')
        reading = distance(int(raw_mm)) if isinstance(raw_mm, (int, float)) and raw_mm >= 0 else 'no distance'
        return (f'<b>{sensor.name}</b>: sensor says {reading}, <span style="color:{colour}">{said}</span>{strength}'
                f' → controller passes on {passed}')


def main(argv=None) -> int:
    return run(TofDiagnosticsWindow, 'Rex ToF Diagnostics', __doc__.split('\n')[0], argv)


if __name__ == '__main__':
    raise SystemExit(main())
