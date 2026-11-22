"""Sensor diagnostics window: the drive controller's gyroscope, tilt, compass, wheels and sensor bus, live.

    .venv/bin/python -m r3x.gui.sensor_diagnostics                       fed on stdin (the battery menu does this)
    .venv/bin/python -m r3x.gui.sensor_diagnostics --replay FILE.jsonl    a saved capture, at its own pace

Owner, November 22: "this sensor needs to be addressed. The menu bar app has a ToF utility menu. Can you
add something similar for the other sensors?" It is fed exactly as the ToF window is: the battery menu
holds the drive controller's port whenever Rex is off and passes this window every line it reads. This
process never opens the port and sends nothing. While Rex runs he holds the port, and the window says so.

It shows the heading by the gyroscope against the heading by the wheels, how fast the gyroscope drifts
with the wheels still (the number that says whether its zero is right), a minute of each reading, which
chips on the sensor bus answer, the wheels and the controller, the controller's own messages, and what
stands out in words (sensor_model.SensorDiagnostics.notices).
"""
from __future__ import annotations

import math
import queue
import time

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from . import theme
from .diagnostics_window import LEVEL_COLOR, STYLE, bring_forward, notice_rows, panel, run, text
from .sensor_model import DRIFT_NORMAL, HISTORY_S, REST_NEEDED_S, SensorDiagnostics, span_words

# One row of the last minute: trace key, name, unit, the least span its trace is scaled to, decimals.
ROWS = (
    ('heading', 'Heading by the gyroscope', '°', 4., 1),
    ('wheel_heading', 'Heading by the wheels', '°', 4., 1),
    ('pitch', 'Tilt forward and back', '°', 2., 1),
    ('roll', 'Tilt side to side', '°', 2., 1),
    ('compass', 'Compass direction (raw)', '°', 4., 1),
    ('field', 'Compass field strength', '', 60., 0),
    ('left', 'Left wheel speed', ' m/s', .1, 2),
    ('right', 'Right wheel speed', ' m/s', .1, 2),
)


class HeadingDial(QWidget):
    """Which way he faces by the gyroscope (orange) and by the wheels (blue), front up, with the
    gyroscope's drift at rest in the middle. Both needles start at zero when the controller starts and
    should turn together; a needle creeping round with the wheels still is the drift."""

    def __init__(self, model: SensorDiagnostics, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _e) -> None:   # noqa: N802 - Qt override
        model, now = self._model, time.monotonic()
        w, h = float(self.width()), float(self.height())
        cx, cy = w / 2, h / 2 - 8
        radius = min(w / 2 - 34, h / 2 - 40)
        if radius < 60:
            return
        live = model.live(now)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        small = QFont(p.font()); small.setPointSize(9)
        big = QFont(p.font()); big.setPointSize(22); big.setBold(True)

        def at(degrees, r):
            a = math.radians(90. + degrees)       # Zero is up; a turn to his left goes counter-clockwise.
            return QPointF(cx + r * math.cos(a), cy - r * math.sin(a))

        p.setPen(QPen(QColor(theme.BORDER), 2)); p.setBrush(QColor(theme.PANEL_BOTTOM))
        p.drawEllipse(QPointF(cx, cy), radius, radius)
        p.setFont(small)
        for degrees in range(0, 360, 30):
            major = degrees % 90 == 0
            p.setPen(QPen(QColor(theme.BORDER if major else theme.BORDER_DIM), 2 if major else 1))
            p.drawLine(at(degrees, radius - (12 if major else 7)), at(degrees, radius))
            if major:
                label = at(degrees, radius + 15)
                p.setPen(QColor(theme.TEXT_DIM))
                words = {0: '0°', 90: 'left 90°', 180: '180°', 270: 'right 90°'}[degrees]
                p.drawText(QRectF(label.x() - 34, label.y() - 8, 68, 16), Qt.AlignmentFlag.AlignCenter, words)
        for heading, colour, reach in ((model.wheel_heading, theme.BLUE, .78), (model.heading, theme.ORANGE, .92)):
            if heading is None or not live:
                continue
            p.setPen(QPen(QColor(colour), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawLine(at(heading, radius * .30), at(heading, radius * reach))
        rate, level = model.drift(), model.drift_level()
        if not live:
            words, caption, colour = '–', '', theme.TEXT_DIM
        elif model.devices['imu'].ok is False:
            words, caption, colour = 'silent', 'the gyroscope does not answer', theme.BAD
        elif rate is not None:
            words = f'{rate:+.1f}°/s' if abs(rate) >= 1 else f'{rate:+.2f}°/s'
            caption, colour = 'drift with the wheels still', LEVEL_COLOR[level]
        elif model.still_since is None:
            words, caption, colour = 'moving', 'drift is read at rest', theme.TEXT_DIM
        else:
            words, caption, colour = '…', f'measuring ({REST_NEEDED_S:.0f} s at rest)', theme.TEXT_DIM
        hub = radius * .30
        p.setPen(QPen(QColor(theme.BORDER_DIM), 1)); p.setBrush(QColor(0, 0, 0, 120))
        p.drawEllipse(QPointF(cx, cy), hub, hub)
        p.setFont(big if len(words) <= 6 and hub >= 46 else small); p.setPen(QColor(colour))
        p.drawText(QRectF(cx - hub, cy - hub, 2 * hub, 2 * hub), Qt.AlignmentFlag.AlignCenter, words)
        p.setFont(small); p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(0, cy + radius + 22, w, 14), Qt.AlignmentFlag.AlignHCenter, caption)
        legend = QRectF(0, h - 16, w, 14)
        p.setPen(QColor(theme.ORANGE)); p.drawText(legend.adjusted(8, 0, 0, 0), Qt.AlignmentFlag.AlignLeft, '▬ gyroscope')
        p.setPen(QColor(theme.BLUE)); p.drawText(legend.adjusted(0, 0, -8, 0), Qt.AlignmentFlag.AlignRight, 'wheels ▬')
        p.end()


class TraceStrips(QWidget):
    """One row per reading: what it is now, its range over the last minute, and the minute as a trace
    scaled to that range. A stretch with no answer from the sensor is a red band."""

    def __init__(self, model: SensorDiagnostics, parent=None) -> None:
        super().__init__(parent)
        self._model = model
        self.setMinimumSize(520, len(ROWS) * 46 + 22)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def paintEvent(self, _e) -> None:   # noqa: N802 - Qt override
        now = time.monotonic()
        w, h = float(self.width()), float(self.height())
        foot = 18.
        row_h = (h - foot) / len(ROWS)
        info_w = min(300., w * .5)
        value_w = 132.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        name_font = QFont(p.font()); name_font.setPointSize(11); name_font.setBold(True)
        small = QFont(p.font()); small.setPointSize(9)
        value_font = QFont(p.font()); value_font.setPointSize(13); value_font.setBold(True)
        live = self._model.live(now)
        for index, (key, name, unit, least, places) in enumerate(ROWS):
            trace = self._model.traces[key]
            top = index * row_h
            if index % 2 == 0:
                p.fillRect(QRectF(0, top, w, row_h), QColor(255, 255, 255, 8))
            value, span = trace.value if live else None, trace.span()
            # The name on its own line, then the reading with its range over the minute beside it.
            p.setPen(QColor(theme.TEXT)); p.setFont(name_font)
            p.drawText(QRectF(10, top + 2, info_w - 14, row_h / 2 - 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, name)
            p.setFont(value_font)
            p.setPen(QColor(theme.BLUE if value is not None else theme.BAD if live and trace.points else theme.TEXT_DIM))
            words = (f'{round(self._shown(key, value), places) + 0.:.{places}f}{unit}' if value is not None
                     else 'no answer' if live and trace.points else '–')
            p.drawText(QRectF(10, top + row_h / 2, value_w, row_h / 2 - 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, words)
            p.setFont(small); p.setPen(QColor(theme.TEXT_DIM))
            detail = 'no reading' if span is None else f'moved {span[1] - span[0]:.{places}f}{unit}'
            p.drawText(QRectF(10 + value_w, top + row_h / 2 + 3, info_w - value_w - 14, row_h / 2 - 5),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, detail)
            self._trace(p, trace, QRectF(info_w + 6, top + 5, w - info_w - 12, row_h - 10), now, least)
        p.setFont(small); p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(info_w + 6, h - foot, w - info_w - 12, foot), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   f'{HISTORY_S:.0f} s ago')
        p.drawText(QRectF(info_w + 6, h - foot, w - info_w - 12, foot), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 'now')
        p.drawText(QRectF(10, h - foot, info_w - 10, foot), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   'The last minute · red is no answer')
        p.end()

    @staticmethod
    def _shown(key, value):
        """Headings are kept unwrapped for the traces and shown as a direction, -180 to 180."""
        return (value + 180.) % 360. - 180. if key in ('heading', 'wheel_heading', 'compass') else value

    @staticmethod
    def _trace(p: QPainter, trace, box: QRectF, now: float, least: float) -> None:
        p.setPen(QPen(QColor(theme.BORDER_DIM), 1)); p.setBrush(QColor(0, 0, 0, 50))
        p.drawRect(box)
        span = trace.span()
        if not trace.points:
            return
        low, high = span if span else (0., 0.)
        pad = max(0., least - (high - low)) / 2
        low, high = low - pad, high + pad

        def x_at(at):
            return max(box.left(), box.right() - (now - at) / HISTORY_S * box.width())

        line, previous = QPolygonF(), None
        red = QColor(theme.BAD); red.setAlpha(110)

        def draw():
            if line.size() > 1:
                p.setPen(QPen(QColor(theme.BLUE), 1.4)); p.drawPolyline(line)
            elif line.size() == 1:
                p.setPen(QPen(QColor(theme.BLUE), 3)); p.drawPoint(line.at(0))

        for at, value in trace.points:
            x = x_at(at)
            if value is None:
                start = x if previous is None else x_at(previous)
                p.fillRect(QRectF(start, box.top() + 1, max(1.5, x - start), box.height() - 2), red)
                draw()
                line = QPolygonF()
            else:
                share = (value - low) / (high - low) if high > low else .5
                line.append(QPointF(x, box.bottom() - 2 - share * (box.height() - 4)))
            previous = at
        draw()


class SensorDiagnosticsWindow(QDialog):
    def __init__(self, lines: 'queue.Queue', *, quit_on_end: bool) -> None:
        super().__init__()
        self._lines, self._quit_on_end = lines, quit_on_end
        self.model = SensorDiagnostics()
        self._logged = 0
        self.setWindowTitle('Rex Sensor Diagnostics')
        self.setWindowFlags(Qt.WindowType.Window)
        self.setStyleSheet(STYLE)
        self.resize(1400, 900)

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
        self._dial = HeadingDial(self.model)
        left.addWidget(panel('HEADING · the gyroscope against the wheels', self._dial), 6)
        self._notices = text()
        self._notices.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        left.addWidget(panel('WHAT STANDS OUT', self._notices), 4)
        body.addLayout(left, 5)

        self._strips = TraceStrips(self.model)
        body.addWidget(panel('THE LAST MINUTE', self._strips), 7)

        right = QVBoxLayout(); right.setSpacing(12)
        self._bus = text()
        self._bus.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        right.addWidget(panel('SENSOR BUS · which chips answer', self._bus), 5)
        self._wheels = text()
        self._wheels.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        right.addWidget(panel('WHEELS', self._wheels), 3)
        self._controller = text()
        self._controller.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        right.addWidget(panel('CONTROLLER', self._controller), 3)
        body.addLayout(right, 5)

        self._log = QPlainTextEdit()
        self._log.setObjectName('tofLog')
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(200)
        self._log.setFixedHeight(96)
        self._log.setPlaceholderText('Messages from the drive controller appear here.')
        root.addWidget(panel('CONTROLLER MESSAGES', self._log))

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)
        self._tick()

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
        self._dial.update()
        self._strips.update()
        self._notices.setText(notice_rows(model.notices(now)))
        self._bus.setText(self._bus_words(now) if live else '')
        self._wheels.setText(self._wheel_words(now) if live else '')
        self._controller.setText(self._controller_words(now) if live else '')
        fresh = min(model.log_total - self._logged, len(model.log))
        for _at, level, message in list(model.log)[len(model.log) - fresh:]:
            self._log.appendPlainText(f'{time.strftime("%H:%M:%S")}  {level:<5} {message}')
        self._logged = model.log_total

    def _bus_words(self, now: float) -> str:
        model, rows = self.model, []
        for key, device in model.devices.items():
            if device.ok is None:
                colour, state = theme.TEXT_DIM, 'waiting'
            elif device.ok:
                colour, state = theme.GOOD, 'answering'
            else:
                colour = theme.BAD
                state = 'not answering' + (f' for {span_words(now - device.lost_at)}' if device.lost_at is not None else '')
            if key == 'ring' and model.ring_answering is not None:
                state = f'{model.ring_answering} of 8 answering'
            if device.dropouts:
                state += f' · dropped ×{device.dropouts}'
            note = ' <span style="color:%s">(set up only when the controller starts)</span>' % theme.TEXT_DIM \
                if device.startup_only and device.ok is False else ''
            rows.append(f'<span style="color:{colour}">●</span>&nbsp; <b>{device.name}</b> '
                        f'<span style="color:{theme.TEXT_DIM}">{device.part}</span><br>'
                        f'&nbsp;&nbsp;&nbsp;&nbsp;<span style="color:{colour}">{state}</span>{note}')
        telemetry = model.telemetry or {}
        mag, env = telemetry.get('mag') or {}, telemetry.get('env') or {}
        extra = []
        if mag.get('ok'):
            spread = model.compass_spread()
            extra.append(f'Compass raw x {mag.get("x")}, y {mag.get("y")}, z {mag.get("z")}'
                         + (f'; steady within {spread:.1f}° at rest' if spread is not None else '')
                         + '. Uncalibrated: it shows steadiness, not north.')
        if env.get('ok') and env.get('t') is not None:
            extra.append(f'Climate {float(env["t"]) * 9 / 5 + 32:.0f}°F'
                         + (f', {float(env["rh"]):.0f}% humidity' if env.get('rh') is not None else '')
                         + (f', {float(env["hpa"]) * .02953:.2f} inHg' if env.get('hpa') is not None else '') + '.')
        if telemetry.get('batt_mv', -1) is not None and telemetry.get('batt_mv', -1) >= 0:
            extra.append(f'Battery {telemetry["batt_mv"] / 1000:.2f} V.')
        return '<br>'.join(rows) + (f'<br><br><span style="color:{theme.TEXT_DIM}">' + '<br>'.join(extra) + '</span>' if extra else '')

    def _wheel_words(self, now: float) -> str:
        model = self.model
        telemetry = model.telemetry or {}
        odom, wheels = telemetry.get('odom') or {}, telemetry.get('wheels') or {}
        still = model.still_since is not None
        rows = [('<span style="color:%s">●</span>&nbsp; ' % (theme.GOOD if still else theme.WARN))
                + (f'Still for {span_words(now - model.still_since)}' if still else 'Moving')]
        if odom:
            rows.append(f'Position by the wheels: {float(odom.get("x", 0)):+.2f} m forward, {float(odom.get("y", 0)):+.2f} m left')
        if wheels:
            rows.append(f'Drive level: left {wheels.get("dl", 0)}, right {wheels.get("dr", 0)} (of 1023)')
        move = model.last_move
        if move is not None:
            colour = theme.WARN if model.moves_disagree() else theme.TEXT
            rows.append(f'<span style="color:{colour}">Last move, {span_words(now - move["ended"])} ago: gyroscope '
                        f'{move["gyro"]:+.0f}°, wheels {move["wheels"]:+.0f}°, {move["metres"]:.2f} m in {move["seconds"]:.1f} s</span>')
        else:
            rows.append(f'<span style="color:{theme.TEXT_DIM}">Turn him with the battery menu\'s joystick to compare the '
                        'gyroscope with the wheels.</span>')
        return '<br>'.join(rows)

    def _controller_words(self, now: float) -> str:
        model = self.model
        telemetry = model.telemetry or {}
        state = {'comms_lost': 'standing by (nothing is commanding it)'}.get(telemetry.get('state'), telemetry.get('state', '?'))
        fault = telemetry.get('fault')
        rows = [f'State: {state}' + (f' · fault {fault}' if fault and fault != telemetry.get('state') else '')]
        if model.uptime is not None:
            rows.append(f'Running for {span_words(model.uptime)}; the gyroscope\'s zero and the battery gauge were set up then')
        if model.restarts:
            rows.append(f'<span style="color:{theme.WARN}">Restarted ×{model.restarts} while watching</span>')
        rows.append(f'Gamepad: {telemetry.get("gamepad", "?")} · in charge: {telemetry.get("owner", "?")}')
        level = model.drift_level()
        if level is not None:
            rows.append(f'<span style="color:{LEVEL_COLOR[level]}">Gyroscope drift at rest {model.drift():+.2f}°/s</span> '
                        f'(normal under {DRIFT_NORMAL})')
        return '<br>'.join(rows)


def main(argv=None) -> int:
    return run(SensorDiagnosticsWindow, 'Rex Sensor Diagnostics', __doc__.split('\n')[0], argv)


if __name__ == '__main__':
    raise SystemExit(main())
