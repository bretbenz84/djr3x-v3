"""Rebel-style dashboard window (V2 gui/dashboard.py, adapted to the V3 feed).

Layout as in V2: a landscape camera panel above the Systems tabs (Actuators, Scene,
Logs), the live 3D droid in the centre and the transcript on the right, with the state
badge, device lights, pack power and Stop / Shut Down in the top bar. V2's Pause, Sleep,
Memory Banks, typed input and Jeopardy page are not part of this port.
"""

from __future__ import annotations

import html
from typing import Any

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QPushButton,
                               QSizePolicy, QSplitter, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from ..servo_poses import JOINT_RANGES, joint_percents
from . import theme
from .avatar import RexAvatar
from .logs import LogPanel
from .motivator import MotivatorControlDialog
from .theme import HoloPanel as ChromePanel, ServoGauge
from .transcript import ConversationPanel
from .vision import VisionPanel

POLL_HZ = 20   # V2 GUI_FPS
DEVICE_COLORS = {'disabled': '#5b6b7d', 'connected': '#45d85e', 'offline': '#ff6b5e', 'starting': '#f0c45a'}
BADGES = {'starting': ('STARTING', '#f0c45a'), 'listening': ('LISTENING', '#45d85e'),
          'thinking': ('THINKING', '#5396ff'), 'speaking': ('SPEAKING', '#ff9b21'),
          'quiet': ('QUIET', '#f0c45a'), 'stopping': ('SHUTTING DOWN', '#ff6b5e'), 'stopped': ('STOPPED', '#8d9aab')}
STATUS = {'booting': ('●  Booting…', '#f0c45a'), 'online': ('●  Connected', '#45d85e'),
          'failed': ('●  Startup failed — see the Logs tab', '#ff6b5e'),
          'stopping': ('●  Shutting down…', '#f0c45a'), 'stopped': ('●  Rex stopped', '#8d9aab')}


class LandscapeCameraPanel(ChromePanel):
    """Prefer a 16:9 camera viewport as the operator resizes the columns (V2)."""

    def __init__(self, index, title, content):
        super().__init__(index, title, content)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setMinimumHeight(180)

    def heightForWidth(self, width):
        viewport_width = max(1, width - 20 - 24)
        return 16 + (self.HEADER_H - 6) + 4 + 16 + round(viewport_width * 9 / 16)

    def sizeHint(self):
        return QSize(440, self.heightForWidth(self.width()))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()


class DashboardWindow(QMainWindow):
    def __init__(self, feed, worker=None, parent=None) -> None:
        super().__init__(parent)
        self._feed = feed
        self._worker = worker
        self._closing = False
        self.setWindowTitle("DJ-R3X Controller")
        self.resize(1440, 900)
        self.setMinimumSize(1140, 740)

        self.vision = VisionPanel()
        self.scene = ScenePanel()
        self.avatar = RexAvatar(feed)
        self.servos = ActuatorsPanel(feed)
        self.conversation = ConversationPanel()
        self.syslog = LogPanel()
        self.connection = QLabel("●  Booting…")
        self.connection.setObjectName("connectionLabel")
        self.state_badge = QLabel("—")
        self.state_badge.setObjectName("stateBadge")
        self.state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._last = {}

        root = theme.RebelBackdrop()
        root.setObjectName("root")
        shell = QVBoxLayout(root)
        shell.setContentsMargins(18, 14, 18, 12)
        shell.setSpacing(12)

        top_bar = QWidget()
        top = QGridLayout(top_bar)
        top.setContentsMargins(0, 0, 0, 0)
        controls = QWidget()
        cluster = QHBoxLayout(controls)
        cluster.setContentsMargins(0, 0, 0, 0)
        cluster.setSpacing(0)
        cluster.addWidget(self.state_badge)
        self._stop_btn = QPushButton("■  Stop")
        self._stop_btn.setObjectName("topControlButton")
        self._stop_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._stop_btn.setToolTip("Stop the current reply and any movement (like saying “stop” or pressing Enter)")
        self._stop_btn.clicked.connect(self._feed.stop_reply)
        cluster.addWidget(self._stop_btn)
        self._shutdown_btn = QPushButton("⏻  Shut Down")
        self._shutdown_btn.setObjectName("topShutdownButton")
        self._shutdown_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._shutdown_btn.setToolTip("Shut Rex down normally (park, lights off, exit)")
        self._shutdown_btn.clicked.connect(self._confirm_shutdown)
        cluster.addWidget(self._shutdown_btn)

        title_box = QWidget()
        title_col = QVBoxLayout(title_box)
        title_col.setContentsMargins(0, 0, 0, 0)
        title_col.setSpacing(0)
        title = QLabel("DJ–R3X  /  DROID OPERATIONS")
        title.setObjectName("windowTitle")
        subtitle = QLabel("ALLIANCE FIELD CONSOLE     /     BLACK SPIRE OUTPOST")
        subtitle.setObjectName("windowSubtitle")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)

        telemetry = QWidget()
        rc = QHBoxLayout(telemetry)
        rc.setContentsMargins(0, 0, 0, 0)
        rc.setSpacing(18)
        self._power = QLabel('<span style="color:#5b6b7d;">⚡</span>&nbsp;—')
        self._power.setObjectName("deviceStatus")
        self._power.setTextFormat(Qt.TextFormat.RichText)
        self._power.setToolTip("Drive pack (INA226 via the drive base): charge / current / voltage")
        rc.addWidget(self._power)
        self._devices: dict[str, QLabel] = {}
        for name in ('Chest LEDs', 'Head LEDs', 'Maestro', 'ESP32 Motor', 'Radar', 'Camera'):
            label = QLabel(f'<span style="color:#5b6b7d;">●</span>&nbsp;{name}')
            label.setObjectName("deviceStatus")
            label.setTextFormat(Qt.TextFormat.RichText)
            rc.addWidget(label)
            self._devices[name] = label
        rc.addStretch(1)
        rc.addWidget(self.connection)

        identity = QWidget()
        identity_row = QHBoxLayout(identity)
        identity_row.setContentsMargins(0, 0, 0, 0)
        identity_row.setSpacing(12)
        identity_row.addWidget(theme.RebelMark())
        identity_row.addWidget(title_box)
        top.addWidget(identity, 0, 0)
        top.addWidget(controls, 0, 1, Qt.AlignmentFlag.AlignRight)
        top.addWidget(telemetry, 1, 0, 1, 2)
        top.setRowMinimumHeight(0, 52)
        top.setRowMinimumHeight(1, 30)
        top.setColumnStretch(0, 1)
        shell.addWidget(top_bar)

        self.vision.setMinimumSize(260, 0)
        camera_panel = LandscapeCameraPanel("01", "Visual feed", self.vision)
        self.scene.setMinimumHeight(190)
        self._tabs = QTabWidget()
        self._tabs.setObjectName("sensorTabs")
        self._tabs.addTab(self.servos, "ACTUATORS")
        self._tabs.addTab(self.scene, "SCENE")
        self._tabs.addTab(self.syslog, "LOGS")
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().setExpanding(True)
        self._tabs.setElideMode(Qt.TextElideMode.ElideNone)
        self._tabs.setUsesScrollButtons(False)
        self._tabs.setTabToolTip(1, "Room, current goal, faces and objects in view, indoor climate")
        self._tabs.setMinimumHeight(270)

        instruments = QWidget()
        instruments.setMinimumWidth(350)
        instrument_layout = QVBoxLayout(instruments)
        instrument_layout.setContentsMargins(0, 0, 0, 0)
        instrument_layout.setSpacing(12)
        instrument_layout.addWidget(camera_panel)
        instrument_layout.addWidget(ChromePanel("02", "Systems", self._tabs), 1)
        avatar_panel = ChromePanel("03", "Droid bay  /  R3X", self.avatar)
        avatar_panel.setMinimumWidth(320)
        transcript_panel = ChromePanel("04", "Live transcript", self.conversation)
        transcript_panel.setMinimumWidth(350)
        columns = QSplitter(Qt.Orientation.Horizontal)
        columns.setObjectName("dashboardColumns")
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(12)
        for widget in (instruments, avatar_panel, transcript_panel):
            columns.addWidget(widget)
        for i, stretch in enumerate((4, 4, 5)):
            columns.setStretchFactor(i, stretch)
        columns.setSizes([440, 440, 500])

        footer = QHBoxLayout()
        footer.setContentsMargins(4, 0, 4, 0)
        station = QLabel("R3X  //  FIELD OPERATIONS")
        station.setObjectName("consoleFootnote")
        footer.addWidget(station)
        footer.addStretch(1)
        hint = QLabel("Drag panel dividers to adjust your station")
        hint.setObjectName("consoleFootnote")
        footer.addWidget(hint)
        shell.addWidget(columns, 1)
        shell.addLayout(footer)

        self.setCentralWidget(root)
        self.setStyleSheet(theme.STYLE + theme.REBEL_STYLE)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000 // POLL_HZ)

    def _confirm_shutdown(self) -> None:
        dlg = QDialog(self)
        dlg.setObjectName("confirmDialog")
        dlg.setWindowTitle("Shut Down DJ-R3X")
        dlg.setModal(True)
        dlg.setStyleSheet(theme.DIALOG_STYLE)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(18)
        msg = QLabel("Shut down DJ-R3X?\nHe parks, his lights fade and the program exits.")
        msg.setObjectName("confirmText")
        msg.setWordWrap(True)
        msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(msg)
        row = QHBoxLayout()
        row.setSpacing(12)
        no_btn = QPushButton("No")
        no_btn.setObjectName("confirmNo")
        no_btn.clicked.connect(dlg.reject)
        yes_btn = QPushButton("Yes, shut down")
        yes_btn.setObjectName("confirmYes")
        yes_btn.clicked.connect(dlg.accept)
        row.addStretch(1)
        row.addWidget(no_btn)
        row.addWidget(yes_btn)
        lay.addLayout(row)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.request_shutdown()

    def request_shutdown(self) -> None:
        """Shut Down button, window close, Ctrl-C or SIGTERM: Rex's normal exit. The window
        stays up (status shows progress) until the live thread has finished cleanup."""
        self._closing = True
        self._feed.shutdown()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if self._worker is not None and self._worker.is_alive():
            self.request_shutdown()
            event.ignore()
            return
        self._timer.stop()
        super().closeEvent(event)

    def _tick(self) -> None:
        snapshot = self._feed.snapshot()
        self.vision.set_snapshot(snapshot)
        self.scene.set_snapshot(snapshot)
        self.avatar.set_snapshot(snapshot)
        self.servos.set_snapshot(snapshot)
        self.conversation.set_snapshot(snapshot)
        self.syslog.set_snapshot(snapshot)
        status = snapshot['status']
        self._set_label('status', self.connection, *STATUS.get(status, STATUS['booting']), plain=True)
        badge = BADGES.get(snapshot['activity'], ('—', '#5b6b7d'))
        if self._last.get('badge') != badge:
            self._last['badge'] = badge
            self.state_badge.setText(badge[0])
            self.state_badge.setStyleSheet(f"color:{badge[1]}; border:1px solid {badge[1]};"
                                           " border-radius:5px; padding:3px 13px; font-size:14px; font-weight:900;")
        for name, state in snapshot['devices'].items():
            text = f'<span style="color:{DEVICE_COLORS[state]};">●</span>&nbsp;{name}'
            if self._last.get(name) != text:
                self._last[name] = text
                self._devices[name].setText(text)
                self._devices[name].setToolTip(f"{name}: {state}")
        power = power_html((snapshot['drive'] or {}).get('telemetry'))
        if self._last.get('power') != power:
            self._last['power'] = power
            self._power.setText(power)
        if snapshot.get('log_path') and self._last.get('log') != snapshot['log_path']:
            self._last['log'] = snapshot['log_path']
            self._tabs.setTabToolTip(2, snapshot['log_path'])
        worker_done = self._worker is not None and not self._worker.is_alive()
        if worker_done and (self._closing or status != 'failed'):
            self._timer.stop()
            self.close()   # A failed boot stays readable until the operator closes the window.

    def _set_label(self, key, label, text, color, plain=False):
        if self._last.get(key) != (text, color):
            self._last[key] = (text, color)
            label.setText(text)
            label.setStyleSheet(f"color: {color}; font-size: 13px;")


def power_html(telemetry) -> str:
    """Drive-pack charge / current / voltage, as V2's top bar."""
    telemetry = telemetry or {}
    def number(key):
        try:
            return float(telemetry[key])
        except (KeyError, TypeError, ValueError):
            return None
    mv, ma, soc = number('batt_mv'), number('batt_ma'), number('batt_soc')
    parts = []
    if soc is not None and soc >= 0:
        color = "#45d85e" if soc > 30 else ("#f0c45a" if soc > 12 else "#ff6b5e")
        parts.append(f'<span style="color:{color};">{soc:.0f}%</span>')
    if ma:
        parts.append(f"{ma / 1000.0:.2f} A" if abs(ma) >= 1000 else f"{ma:.0f} mA")
    if mv is not None and mv > 0:
        parts.append(f"{mv / 1000.0:.2f} V")
    if not parts:
        return '<span style="color:#5b6b7d;">⚡</span>&nbsp;—'
    return f'<span style="color:#f0c45a;">⚡</span>&nbsp;{" · ".join(parts)}'


class ActuatorsPanel(QWidget):
    """Commanded joint positions, and the manual override that drives them by hand.

    Gauges show each joint's ramped output pulse (the owner's packets replayed at their
    speed and acceleration) as a percentage of each joint's
    calibrated range (the scale of the model's set_robot_joints). With MANUAL OVERRIDE on,
    Rex's own servo motion yields and the sliders move joints through the same owner:
    limits, paired-joint routes and clearance checks still apply."""

    def __init__(self, feed, parent=None) -> None:
        super().__init__(parent)
        self._feed = feed
        self._sliders: dict[str, ServoGauge] = {}
        self._values: dict[str, QLabel] = {}
        self._override = False
        self._commanded_key, self._percents = None, {}
        layout = QGridLayout(self)
        layout.setContentsMargins(14, 4, 14, 10)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(3)
        self._override_button = QPushButton("MANUAL OVERRIDE")
        self._override_button.setObjectName("servoOverrideButton")
        self._override_button.setCheckable(True)
        self._override_button.setToolTip("Rex's own servo motion yields; drive joints with the sliders.")
        self._override_button.toggled.connect(self._set_override)
        layout.addWidget(self._override_button, 0, 0, 1, 2)
        motivator = QPushButton("🕹 MOTIVATOR")
        motivator.setObjectName("servoOverrideButton")
        motivator.setToolTip("Drive-base sensors, radar ring and joystick driving.")
        motivator.clicked.connect(self._open_motivator)
        layout.addWidget(motivator, 0, 2, 1, 2)
        self._motivator = None
        self._result = QLabel("")
        self._result.setObjectName("servoState")
        layout.addWidget(self._result, 1, 0, 1, 4)
        for row, joint in enumerate(JOINT_RANGES, start=2):
            label = QLabel(joint.replace('_', ' '))
            label.setObjectName("servoName")
            layout.addWidget(label, row, 0)
            slider = ServoGauge()
            slider.setEnabled(False)
            slider.setFixedHeight(17)
            slider.setRange(0, 1000)
            slider.setObjectName("servoSlider")
            slider.valueChanged.connect(lambda value, name=joint: self._slider_changed(name, value))
            layout.addWidget(slider, row, 1, 1, 2)
            self._sliders[joint] = slider
            value = QLabel("")
            value.setObjectName("servoValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setMinimumWidth(42)
            layout.addWidget(value, row, 3)
            self._values[joint] = value
        layout.setColumnStretch(1, 1)
        self.setMinimumHeight(220)

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        servos = snapshot.get('servos') or {}
        override = bool(servos.get('operator'))
        if override != self._override:
            self._apply_override(override)
        self._override_button.setEnabled(bool(servos.get('controller')) and servos.get('state') == 'active'
                                         or override)
        last = servos.get('last')
        note = 'Simulated body (no servo controller configured): the droid shows the motion Rex would make. ' \
            if servos.get('simulated') else ''
        self._result.setWordWrap(True)
        self._result.setText(note if not override else note +
                             'Rex\'s own motion is paused' + (f" — last move: {last['status']}"
                                                              + (f" ({last['reason']})" if last.get('reason') else '')
                                                              if last else ''))
        positions = servos.get('positions') or {}
        key = tuple(sorted(positions.items()))
        if key != self._commanded_key:   # joint_percents fits the head pair: only on change.
            self._commanded_key, self._percents = key, joint_percents(positions)
        percents = self._percents
        for joint, slider in self._sliders.items():
            percent = percents.get(joint)
            self._values[joint].setText('—' if percent is None else f"{percent}%")
            if percent is not None and not override:
                slider.blockSignals(True)
                slider.setValue(int(percent * 10))
                slider.blockSignals(False)

    def _apply_override(self, enabled: bool) -> None:
        self._override = enabled
        if self._override_button.isChecked() != enabled:
            self._override_button.blockSignals(True)
            self._override_button.setChecked(enabled)
            self._override_button.blockSignals(False)
        self._override_button.setProperty("active", enabled)
        self._override_button.style().unpolish(self._override_button)
        self._override_button.style().polish(self._override_button)
        for slider in self._sliders.values():
            slider.setEnabled(enabled)

    def _set_override(self, enabled: bool) -> None:
        ok, reason = self._feed.servos.enable(enabled)
        if not ok:
            self._result.setText(f"Manual override refused: {reason}")
            enabled = False
        self._apply_override(enabled)

    def _slider_changed(self, joint: str, value: int) -> None:
        if self._override:
            self._feed.servos.set(joint, value / 10)
            self._values[joint].setText(f"{value / 10:.0f}%")

    def _open_motivator(self) -> None:
        if self._motivator is None:
            self._motivator = MotivatorControlDialog(self._feed, self)
        self._motivator.show()
        self._motivator.raise_()
        self._motivator.activateWindow()


class ScenePanel(QWidget):
    """What Rex currently has to go on: room, goal, faces and objects in view, climate."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._last_html = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 18)
        self._body = QTextBrowser()
        self._body.setObjectName("visionDescription")
        self._body.setFrameShape(QFrame.Shape.NoFrame)
        self._body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._body, 1)
        self.setMinimumHeight(300)

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        rendered = scene_html(snapshot)
        if rendered != self._last_html:
            self._last_html = rendered
            self._body.setHtml(rendered)


def _kv(rows) -> str:
    cells = ''.join(f'<tr><td class="key">{html.escape(str(k))}</td><td class="value">{html.escape(str(v))}</td></tr>'
                    for k, v in rows if v not in (None, ''))
    return f'<table class="kv">{cells}</table>' if cells else '<p class="empty">Nothing yet.</p>'


def scene_html(snapshot: dict[str, Any]) -> str:
    room = snapshot.get('room')
    if room is None:
        room_html = '<p class="empty">Room recognition is off.</p>'
    else:
        room_html = _kv((('room', room.get('name') or '—'), ('status', room.get('status')),
                         ('looks like', ', '.join(room.get('looks_like') or ()))))
    goal = snapshot.get('goal')
    goal_html = _kv((('goal', goal),)) if goal else '<p class="empty">No goal of his own right now.</p>'
    faces = (snapshot.get('camera') or {}).get('faces') or []
    if faces:
        faces_html = ''.join('<div class="face"><div class="face-title">'
                             + html.escape(f.get('name') or 'Unknown face')
                             + '</div>' + _kv((('position', f.get('position')), ('track', f.get('track_id')))) + '</div>'
                             for f in faces)
    else:
        faces_html = '<p class="empty">No faces in view.</p>'
    objects = snapshot.get('objects') or []
    objects_html = _kv([(o.get('label'), o.get('position') or '') for o in objects]) if objects else \
        '<p class="empty">No tracked objects.</p>'
    env = ((snapshot.get('drive') or {}).get('telemetry') or {}).get('env') or {}
    if env.get('ok') and env.get('t') is not None:
        inside = f"{float(env['t']) * 9 / 5 + 32:.1f}°F"
        if env.get('rh') is not None:
            inside += f", {float(env['rh']):.0f}% RH"
        if env.get('hpa') is not None:
            inside += f", {float(env['hpa']) * 0.029530:.2f} inHg"
    else:
        inside = 'climate sensor offline'
    return f"""<html><head><style>
body {{ margin:0; background:transparent; color:#d9e3ee; font-family:-apple-system, sans-serif; font-size:12px; }}
.section {{ margin:0 0 13px 0; }}
.eyebrow {{ color:#e08428; font-size:11px; font-weight:900; letter-spacing:2px; text-transform:uppercase; }}
.face {{ margin:8px 0 0 0; padding:8px 0 0 0; border-top:1px solid #1c344e; }}
.face-title {{ color:#e7f0fa; font-size:12px; font-weight:900; }}
table.kv {{ margin-top:4px; border-collapse:collapse; }}
td.key {{ padding:1px 14px 1px 0; color:#4e94ff; font-weight:800; white-space:nowrap; }}
td.value {{ padding:1px 0; color:#ccd8e5; }}
.empty {{ margin:6px 0 0 0; color:#91a4b8; }}
</style></head><body>
<div class="section"><div class="eyebrow">◢ Initiative</div>{goal_html}</div>
<div class="section"><div class="eyebrow">◢ Room</div>{room_html}</div>
<div class="section"><div class="eyebrow">◢ Faces in view</div>{faces_html}</div>
<div class="section"><div class="eyebrow">◢ Objects</div>{objects_html}</div>
<div class="section"><div class="eyebrow">◢ Environment</div>{_kv((('inside', inside),))}</div>
</body></html>"""
