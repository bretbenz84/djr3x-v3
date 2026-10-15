"""Motivator console: joystick driving and the drive base's sensors (V2 gui/dashboard.py).

Widgets are V2's, unchanged apart from imports. The dialog drives through the feed's
teleop worker (the drive owner's manual mode), never the serial link directly.
"""
from __future__ import annotations

import math
import time
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ..drive import CONFIG as DRIVE_CONFIG
from . import theme

class JoystickWidget(QWidget):
    """Self-centering analog stick drawn like a game-controller thumbstick.

    Emits normalized coordinates in [-1, 1]: x right-positive, y UP-positive
    (screen Y is inverted internally). Magnitude (distance from center) ramps the
    speed: 0 at center, 1.0 at the edge. Snaps back to center on release."""

    moved = Signal(float, float)
    released = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._kx = 0.0   # knob pixel offset from center
        self._ky = 0.0
        self._x = 0.0    # normalized value
        self._y = 0.0
        self._dragging = False

    def value(self) -> "tuple[float, float]":
        return (self._x, self._y)

    def _geom(self) -> "tuple[float, float, float, float, float]":
        size = float(min(self.width(), self.height()))
        R = size / 2.0 - 8.0
        rk = R * 0.34
        travel = max(1.0, R - rk)
        return self.width() / 2.0, self.height() / 2.0, R, rk, travel

    def _set_from_point(self, px: float, py: float) -> None:
        cx, cy, _R, _rk, travel = self._geom()
        dx, dy = px - cx, py - cy
        d = math.hypot(dx, dy)
        if d > travel and d > 0:
            dx *= travel / d
            dy *= travel / d
        self._kx, self._ky = dx, dy
        self._x = dx / travel
        self._y = -dy / travel        # invert: up is positive
        self.update()
        self.moved.emit(self._x, self._y)

    def _recenter(self) -> None:
        self._kx = self._ky = 0.0
        self._x = self._y = 0.0
        self.update()
        self.moved.emit(0.0, 0.0)

    def mousePressEvent(self, e) -> None:
        self._dragging = True
        self._set_from_point(e.position().x(), e.position().y())

    def mouseMoveEvent(self, e) -> None:
        if self._dragging:
            self._set_from_point(e.position().x(), e.position().y())

    def mouseReleaseEvent(self, e) -> None:
        self._dragging = False
        self._recenter()
        self.released.emit()

    def paintEvent(self, _e) -> None:
        cx, cy, R, rk, _travel = self._geom()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Base bezel.
        p.setPen(QPen(QColor(70, 80, 95), 2))
        p.setBrush(QBrush(QColor(28, 33, 41)))
        p.drawEllipse(QRectF(cx - R, cy - R, 2 * R, 2 * R))
        # Inner well.
        well = R * 0.82
        p.setPen(QPen(QColor(48, 56, 68), 1))
        p.setBrush(QBrush(QColor(18, 22, 28)))
        p.drawEllipse(QRectF(cx - well, cy - well, 2 * well, 2 * well))

        # Direction ticks (N/E/S/W).
        p.setPen(QPen(QColor(90, 150, 200, 160), 2))
        for ang in (0, 90, 180, 270):
            a = math.radians(ang)
            x1, y1 = cx + R * 0.70 * math.cos(a), cy + R * 0.70 * math.sin(a)
            x2, y2 = cx + R * 0.90 * math.cos(a), cy + R * 0.90 * math.sin(a)
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))

        # F / B / L / R labels.
        p.setPen(QColor(150, 175, 200))
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        ac = Qt.AlignmentFlag.AlignCenter
        p.drawText(QRectF(cx - 16, cy - R * 0.92 - 9, 32, 18), ac, "FWD")
        p.drawText(QRectF(cx - 16, cy + R * 0.92 - 9, 32, 18), ac, "BACK")
        p.drawText(QRectF(cx - R * 0.92 - 16, cy - 9, 32, 18), ac, "L")
        p.drawText(QRectF(cx + R * 0.92 - 16, cy - 9, 32, 18), ac, "R")

        # Knob with a 3D radial gradient.
        kx, ky = cx + self._kx, cy + self._ky
        grad = QRadialGradient(kx - rk * 0.3, ky - rk * 0.35, rk * 1.5)
        grad.setColorAt(0.0, QColor(130, 215, 255))
        grad.setColorAt(1.0, QColor(36, 86, 134))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(QColor(190, 235, 255), 2))
        p.drawEllipse(QRectF(kx - rk, ky - rk, 2 * rk, 2 * rk))
        # Specular highlight.
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 70))
        p.drawEllipse(QRectF(kx - rk * 0.45, ky - rk * 0.6, rk * 0.5, rk * 0.38))
        p.end()


class DistancePhotoreceptorsWidget(QWidget):
    """Top-down 'radar' of the drive base's 8 radial distance photoreceptors.

    Front is up. Sensors sit at the 540 mm base-ring surface every 45°, starting 22.5°
    off the forward axis (nothing on the cardinals themselves): two long-range VL53L1X
    pairs straddle FRONT (FL/FR) and REAR (RL/RR), two short-range VL53L0X pairs read
    the LEFT (LF/LB) and RIGHT (RF/RB) wall clearance — the hallway steering assist's
    inputs. Each cone's reach scales with the measured distance and is colored by
    zone — green clear, amber slow (< MOTION_SLOW_ZONE_M), red stop (< MOTION_STOP_ZONE_M)
    — with dashed reference rings at those thresholds. A -1 reading (sensor error / no
    return) draws a faint stub. Read-only: call set_readings() from the telemetry tick.

    NOTE: while the ToF subsystem is a firmware stub (MOTION_TOF_PRESENT=0) every sensor
    reads a constant 'clear'; the panel shows that honestly until the sensors are wired
    and the firmware is built with ToF on. There is no down/cliff sensor in this layout."""

    _FOV_DEG = 25.0                       # ToF cone ~25° (VL53L0X / VL53L1X similar)
    # (bearing°, telemetry key, label) — screen convention: 0 = 3 o'clock, CCW+, front = up = 90°.
    # 8 sensors at 45° steps offset 22.5° from the cardinals; keys match firmware tof.cpp.
    _BEAMS = (
        (112.5, "fl", "FL"),   # long  VL53L1X — front pair
        (67.5,  "fr", "FR"),
        (247.5, "rl", "RL"),   # long  VL53L1X — rear pair
        (292.5, "rr", "RR"),
        (157.5, "lf", "LF"),   # short VL53L0X — left pair (front/back)
        (202.5, "lb", "LB"),
        (22.5,  "rf", "RF"),   # short VL53L0X — right pair (front/back)
        (337.5, "rb", "RB"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(260, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tof: dict = {}
        self._zone = None
        self._blocked = None
        self._mx = None
        self._live = False
        self._stop_m = float(DRIVE_CONFIG['stop_zone_m'])
        self._slow_m = float(DRIVE_CONFIG['slow_zone_m'])
        self._max_m = max(self._slow_m * 1.5, 4.0)        # display reach (m) — VL53L1X room scale

    def set_readings(self, tof_mm, zone=None, blocked=None, mx_mm=None) -> None:
        self._tof = dict(tof_mm or {})
        self._zone = (str(zone).lower() if zone else None)
        self._blocked = (str(blocked).lower() if blocked else None)
        self._mx = mx_mm            # 8x8 matrix nearest front obstacle (mm) or None
        self._live = True
        self.update()

    def clear(self) -> None:
        self._tof = {}
        self._zone = self._blocked = None
        self._mx = None
        self._live = False
        self.update()

    def _mm(self, key):
        try:
            v = self._tof.get(key)
            return None if v is None else int(v)
        except (TypeError, ValueError):
            return None

    def _beam_color(self, mm, alpha=255):
        if mm is None or mm < 0:
            return QColor(95, 105, 120, alpha)                  # error / no data
        d = mm / 1000.0
        if d <= self._stop_m:  return QColor(235, 70, 60, alpha)    # STOP
        if d <= self._slow_m:  return QColor(240, 180, 60, alpha)   # SLOW
        return QColor(70, 200, 130, alpha)                          # CLEAR

    def paintEvent(self, _e) -> None:
        w, h = float(self.width()), float(self.height())
        band = 0.0                                    # no down/cliff sensor in this layout
        top_h = h - band
        cx = w / 2.0
        cy = top_h / 2.0 + 6.0
        maxR = min(w, top_h) / 2.0 - 16.0
        if maxR < 24:
            return
        body_r = maxR * 0.20
        reach = maxR - body_r
        dim = 1.0 if self._live else 0.35

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        def pt(bearing_deg, r):
            a = math.radians(bearing_deg)
            return QPointF(cx + r * math.cos(a), cy - r * math.sin(a))

        def radius_for(mm):
            if mm is None or mm < 0:
                return body_r + reach * 0.12
            d = max(0.0, min(mm / 1000.0, self._max_m))
            return body_r + (d / self._max_m) * reach

        # Cones (filled translucent wedges from the body outward).
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)

        # 8x8 matrix ToF: one wide front-center wedge (45° FOV, bearing 90°=up),
        # drawn first so the two radial-style FL/FR cones stay legible on top.
        if self._mx is not None:
            mm = self._mx if isinstance(self._mx, int) else None
            r = radius_for(mm)
            p.setBrush(QBrush(self._beam_color(mm, int(90 * dim))))
            p.setPen(QPen(self._beam_color(mm, int(200 * dim)), 1, Qt.PenStyle.DashLine))
            p.drawPie(QRectF(cx - r, cy - r, 2 * r, 2 * r),
                      int((90.0 - 22.5) * 16), int(45.0 * 16))
            # Readout goes in a chip under the zone badge (a label on the wedge
            # itself collides with the FL/FR labels at the top of the dial).
            txt = "—" if mm is None else ("err" if mm < 0
                  else (f"{mm} mm" if mm < 1000 else f"{mm / 1000.0:.1f} m"))
            mcol = self._beam_color(mm)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(mcol.red(), mcol.green(), mcol.blue(), int(55 * dim)))
            p.drawRoundedRect(QRectF(6, 29, 158, 19), 4, 4)
            p.setPen(mcol)
            p.drawText(QRectF(6, 29, 158, 19), Qt.AlignmentFlag.AlignCenter,
                       f"MX FRONT {txt}")
        for bearing, key, label in self._BEAMS:
            mm = self._mm(key)
            r = radius_for(mm)
            p.setBrush(QBrush(self._beam_color(mm, int(150 * dim))))
            p.setPen(QPen(self._beam_color(mm, int(255 * dim)), 1))
            p.drawPie(QRectF(cx - r, cy - r, 2 * r, 2 * r),
                      int((bearing - self._FOV_DEG / 2.0) * 16), int(self._FOV_DEG * 16))
            txt = "—" if mm is None else ("err" if mm < 0
                  else (f"{mm}" if mm < 1000 else f"{mm / 1000.0:.1f}m"))
            lp = pt(bearing, r + 15)
            p.setPen(QColor(185, 205, 225, int(255 * dim)))
            p.drawText(QRectF(lp.x() - 28, lp.y() - 9, 56, 18),
                       Qt.AlignmentFlag.AlignCenter, f"{label} {txt}")

        # Reference rings (over the cones): max range + slow + stop thresholds.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for val_m, col in ((self._max_m, QColor(70, 80, 95)),
                           (self._slow_m, QColor(150, 120, 50)),
                           (self._stop_m, QColor(150, 60, 55))):
            rr = body_r + (min(val_m, self._max_m) / self._max_m) * reach
            p.setPen(QPen(col, 1, Qt.PenStyle.DashLine))
            p.drawEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr))

        # Robot body + forward chevron (points up).
        p.setPen(QPen(QColor(120, 140, 165, int(255 * dim)), 2))
        p.setBrush(QBrush(QColor(30, 36, 46, int(255 * dim))))
        p.drawEllipse(QRectF(cx - body_r, cy - body_r, 2 * body_r, 2 * body_r))
        p.setPen(QPen(QColor(130, 215, 255, int(255 * dim)), 2))
        p.drawLine(QPointF(cx - body_r * 0.45, cy), QPointF(cx, cy - body_r * 0.55))
        p.drawLine(QPointF(cx, cy - body_r * 0.55), QPointF(cx + body_r * 0.45, cy))

        # Zone badge (top-left chip).
        zone = self._zone or ("clear" if self._live else "—")
        zcol = {"clear": QColor(70, 200, 130), "slow": QColor(240, 180, 60),
                "stop": QColor(235, 70, 60), "cliff": QColor(235, 70, 60)}.get(
                    zone, QColor(120, 130, 145))
        ztxt = f"ZONE {zone.upper()}"
        if self._blocked and self._blocked not in ("none", "—"):
            ztxt += f" · blk {self._blocked}"
        f.setPointSize(8); f.setBold(True); p.setFont(f)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(zcol.red(), zcol.green(), zcol.blue(), int(55 * dim)))
        p.drawRoundedRect(QRectF(6, 6, 158, 19), 4, 4)
        p.setPen(zcol)
        p.drawText(QRectF(6, 6, 158, 19), Qt.AlignmentFlag.AlignCenter, ztxt)

        if not self._live:
            p.setPen(QColor(150, 165, 185))
            f.setPointSize(10); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(0, cy - 12, w, 24), Qt.AlignmentFlag.AlignCenter, "no link")
        p.end()


class TofMatrixWidget(QWidget):
    """Live 8x8 grid of the front matrix ToF (DFRobot SEN0628 / VL53L7CX).

    One cell per zone, color-coded by RAW distance against the motion stop/slow
    thresholds, in the firmware's normalized orientation: row 0 = physically TOP,
    col 0 = the ROBOT'S LEFT edge of the FOV. Cells the firmware's floor
    rejection would discard (reading >= that row's reject threshold, from the
    `rej` array it streams) are drawn as muted blue "floor" cells — exactly the
    view needed to judge whether the lower rows are eating the floor. Read-only:
    call set_frame() from the telemetry tick."""

    _CLEAR_MM = 3500     # sensor's "no return" marker (TOF_MATRIX_CLEAR_MM)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid = None            # list[64] mm or None
        self._rej = []               # list[8] per-row floor thresholds (mm)
        self._stop_m = float(DRIVE_CONFIG['stop_zone_m'])
        self._slow_m = float(DRIVE_CONFIG['slow_zone_m'])

    def set_frame(self, grid, rej=None) -> None:
        self._grid = list(grid) if grid else None
        self._rej = list(rej or [])
        self.update()

    def clear(self) -> None:
        self._grid = None
        self._rej = []
        self.update()

    def nearest_obstacle_mm(self):
        """Nearest non-floor, in-range reading (mm) — the GUI-side analogue of
        the firmware aggregate, for the radar's front wedge. None = no frame."""
        if not self._grid:
            return None
        best = None
        for i, v in enumerate(self._grid):
            if v <= 25 or v >= self._CLEAR_MM:
                continue
            rej = self._rej[i // 8] if i // 8 < len(self._rej) else 4095
            if v >= rej:
                continue                       # the floor
            if best is None or v < best:
                best = v
        return best if best is not None else self._CLEAR_MM

    def _cell_color(self, v, row):
        if v <= 25:
            return QColor(40, 46, 56), QColor(110, 120, 135), "·"      # no return
        rej = self._rej[row] if row < len(self._rej) else 4095
        if v >= self._CLEAR_MM:
            return QColor(26, 34, 30), QColor(110, 130, 118), "far"    # clear
        if v >= rej:
            return QColor(38, 52, 74), QColor(130, 165, 210), None     # floor-rejected
        d = v / 1000.0
        if d <= self._stop_m:
            return QColor(150, 45, 40), QColor(255, 225, 220), None    # STOP
        if d <= self._slow_m:
            return QColor(150, 110, 35), QColor(255, 240, 210), None   # SLOW
        return QColor(35, 95, 62), QColor(215, 245, 228), None         # obstacle, clear zone

    def paintEvent(self, _e) -> None:
        w, h = float(self.width()), float(self.height())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        legend_h = 18.0
        pad, gap = 4.0, 2.0
        cell = min((w - 2 * pad - 7 * gap) / 8.0,
                   (h - legend_h - 2 * pad - 7 * gap) / 8.0)
        if cell < 12:
            p.end()
            return
        gx = (w - (8 * cell + 7 * gap)) / 2.0
        gy = pad

        f = p.font(); f.setBold(True)
        f.setPointSize(max(6, min(9, int(cell / 4.2))))
        p.setFont(f)

        if not self._grid:
            p.setPen(QColor(150, 165, 185))
            f.setPointSize(10); p.setFont(f)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter,
                       "no matrix frames")
            p.end()
            return

        for r in range(8):
            for c in range(8):
                v = int(self._grid[r * 8 + c])
                bg, fg, sym = self._cell_color(v, r)
                x = gx + c * (cell + gap)
                y = gy + r * (cell + gap)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(QRectF(x, y, cell, cell), 3, 3)
                p.setPen(fg)
                txt = sym if sym is not None else (f"{v}" if v < 1000 else f"{v / 1000.0:.1f}")
                p.drawText(QRectF(x, y, cell, cell), Qt.AlignmentFlag.AlignCenter, txt)

        # Legend: orientation + the floor-cell key (the point of this widget).
        ly = gy + 8 * cell + 7 * gap + 3
        f.setPointSize(8); p.setFont(f)
        p.setPen(QColor(150, 165, 185))
        p.drawText(QRectF(gx, ly, 8 * cell + 7 * gap, legend_h),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   "top=up · left=robot L")
        p.setPen(QColor(130, 165, 210))
        p.drawText(QRectF(gx, ly, 8 * cell + 7 * gap, legend_h),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   "blue = floor-rejected")
        p.end()


class RadarRingWidget(QWidget):
    """Top-down scope of the LD2450 bearing-prior ring (firmware/djr3x_radar).

    Front is up, + bearing = left/CCW (docs/motion_protocol.md §4). Each sensor's
    ±60° FOV is a faint wedge at its mount angle (mounts come from the board's
    hello; wedges go red when that sensor stops delivering frames), dashed rings
    mark 2/4/6/8 m, and every fused target is plotted at (bearing, range): dot
    sized+colored by confidence, radial tail showing radial speed (outward = moving
    away), thin halo when two sensors agreed across a seam. When the live frame is
    empty but hardware.radar is still latching the last list (the LD2450 drops
    people who freeze), targets draw hollow/dashed under a LATCHED chip —
    "remembering", not "seeing". Per-sensor transport health (frames/bad/drop)
    lines the footer. Read-only: call set_state() from the telemetry tick.
    """

    _MAX_M = 8.0                          # LD2450 spec range — the display reach
    _HALF_FOV_DEG = 60.0                  # ±60° azimuth per module
    # pins.h defaults, until hello arrives: S0 rear (180°), S1 front-right
    # (−60°), S2 front-left (+60°) — wedges meet dead ahead and at ±120°.
    # Index order matters: wedge i is colored by sens[i]'s health.
    _FALLBACK_MOUNTS = (180.0, -60.0, 60.0)
    _STALE_SECS = 1.0                     # telemetry freshness bar (radar_ok())

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(280, 320)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._connected = False
        self._fresh = False
        self._ok = False
        self._up = 0
        self._errs = 0
        self._targets: list = []          # live fused targets (normalized dicts)
        self._latched: list = []          # latch-only display (live frame empty)
        self._sens: list = []             # per-sensor {ok, frames, bad, drop}
        self._mounts: tuple = self._FALLBACK_MOUNTS
        self._cfg: tuple = ()             # per-sensor hello cfg_ok flags

    @staticmethod
    def _norm_target(t) -> "dict | None":
        """Wire target {b,r,c,s,m} -> display dict (same normalization as
        hardware/radar.py; the wire schema is the stable contract)."""
        try:
            return {
                "bearing_deg": float(t["b"]),
                "range_m": float(t["r"]),
                "confidence": float(t["c"]),
                "speed_mps": float(t.get("s", 0.0)),
                "sensors": int(t.get("m", 0)),
            }
        except (KeyError, TypeError, ValueError):
            return None

    def set_state(self, tel, latched=None, hello=None, connected: bool = False) -> None:
        """Feed the raw radar telemetry frame, hardware.radar.targets() (the
        latched list), hello_info(), and link state. Any piece may be None."""
        self._connected = bool(connected)
        rx = (tel or {}).get("rx_monotonic")
        self._fresh = bool(tel) and rx is not None and (time.monotonic() - rx) <= self._STALE_SECS
        radar_obj = (tel or {}).get("radar") or {}
        self._ok = bool(radar_obj.get("ok")) and self._fresh
        self._up = int(radar_obj.get("up") or 0) if self._fresh else 0
        self._errs = int((tel or {}).get("errs") or 0)
        self._sens = list((tel or {}).get("sens") or []) if self._fresh else []
        if self._fresh:
            self._targets = [
                t for t in (self._norm_target(x) for x in radar_obj.get("targets") or [])
                if t is not None
            ]
        else:
            self._targets = []
        # Latch display only when the ring is live but the current frame is empty.
        self._latched = []
        if self._fresh and not self._targets:
            self._latched = [
                t for t in (latched or [])
                if isinstance(t, dict) and "bearing_deg" in t and "range_m" in t
            ]
        sens_info = (hello or {}).get("sensors") or []
        mounts = []
        for s in sens_info:
            try:
                mounts.append(float(s.get("mount")))
            except (TypeError, ValueError):
                pass
        self._mounts = tuple(mounts) if mounts else self._FALLBACK_MOUNTS
        self._cfg = tuple(bool(s.get("cfg", True)) for s in sens_info)
        self.update()

    def clear(self) -> None:
        self._connected = self._fresh = self._ok = False
        self._up = self._errs = 0
        self._targets = []
        self._latched = []
        self._sens = []
        self._mounts = self._FALLBACK_MOUNTS
        self._cfg = ()
        self.update()

    @staticmethod
    def _polar_px(cx, cy, body_r, reach, max_m, bearing_deg, range_m) -> QPointF:
        """Robot-frame polar -> widget pixels. Front = up; + bearing = left/CCW,
        so screen angle = 90° + bearing in Qt's 0°=3-o'clock CCW+ convention."""
        r = body_r + (max(0.0, min(float(range_m), max_m)) / max_m) * reach
        a = math.radians(90.0 + float(bearing_deg))
        return QPointF(cx + r * math.cos(a), cy - r * math.sin(a))

    @staticmethod
    def _conf_color(c: float, alpha: int = 255) -> QColor:
        if c >= 0.55:
            return QColor(70, 200, 130, alpha)     # confident
        if c >= 0.30:
            return QColor(240, 180, 60, alpha)     # marginal (FOV edge / young track)
        return QColor(120, 130, 145, alpha)        # barely there

    def _chip(self, p: QPainter, rect: QRectF, color: QColor, text: str, dim: float) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color.red(), color.green(), color.blue(), int(55 * dim)))
        p.drawRoundedRect(rect, 4, 4)
        p.setPen(QColor(color.red(), color.green(), color.blue(), int(255 * dim)))
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)

    def paintEvent(self, _e) -> None:
        w, h = float(self.width()), float(self.height())
        n_sens = max(len(self._mounts), len(self._sens), 1)
        foot_h = 14.0 * n_sens + 20.0             # per-sensor rows + legend line
        cx = w / 2.0
        cy = (h - foot_h) / 2.0 + 6.0
        maxR = min(w, h - foot_h) / 2.0 - 16.0
        if maxR < 24:
            return
        body_r = maxR * 0.12
        reach = maxR - body_r
        live = self._connected and self._fresh
        dim = 1.0 if live else 0.35

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)

        # Sensor FOV wedges: blue = delivering, red = link up but sensor dead,
        # neutral gray = link down (unknown, not an accusation).
        for i, mount in enumerate(self._mounts):
            if not live:
                col = QColor(120, 130, 145)
            else:
                s_ok = bool(self._sens[i].get("ok")) if i < len(self._sens) else self._ok
                col = QColor(90, 150, 200) if s_ok else QColor(235, 70, 60)
            p.setBrush(QColor(col.red(), col.green(), col.blue(), int(34 * dim)))
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), int(90 * dim)), 1))
            span = 2.0 * self._HALF_FOV_DEG
            p.drawPie(QRectF(cx - maxR, cy - maxR, 2 * maxR, 2 * maxR),
                      int((90.0 + mount - self._HALF_FOV_DEG) * 16), int(span * 16))

        # Range rings every 2 m, labeled up the forward axis.
        p.setBrush(Qt.BrushStyle.NoBrush)
        for m in (2.0, 4.0, 6.0, 8.0):
            rr = body_r + (m / self._MAX_M) * reach
            p.setPen(QPen(QColor(70, 80, 95, int(255 * dim)), 1, Qt.PenStyle.DashLine))
            p.drawEllipse(QRectF(cx - rr, cy - rr, 2 * rr, 2 * rr))
            p.setPen(QColor(120, 135, 155, int(170 * dim)))
            p.drawText(QRectF(cx + 3, cy - rr - 6, 34, 12),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{m:.0f}m")

        # Robot body + forward chevron (matches the photoreceptors dial).
        p.setPen(QPen(QColor(120, 140, 165, int(255 * dim)), 2))
        p.setBrush(QBrush(QColor(30, 36, 46, int(255 * dim))))
        p.drawEllipse(QRectF(cx - body_r, cy - body_r, 2 * body_r, 2 * body_r))
        p.setPen(QPen(QColor(130, 215, 255, int(255 * dim)), 2))
        p.drawLine(QPointF(cx - body_r * 0.45, cy), QPointF(cx, cy - body_r * 0.55))
        p.drawLine(QPointF(cx, cy - body_r * 0.55), QPointF(cx + body_r * 0.45, cy))

        # Targets — live solid, latched hollow/dashed.
        latch_mode = not self._targets and bool(self._latched)
        for t in (self._latched if latch_mode else self._targets):
            c = max(0.0, min(1.0, t.get("confidence", 0.0)))
            pos = self._polar_px(cx, cy, body_r, reach, self._MAX_M,
                                 t.get("bearing_deg", 0.0), t.get("range_m", 0.0))
            dot_r = 4.0 + 5.0 * c
            col = self._conf_color(c, int(255 * dim))
            if latch_mode:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(self._conf_color(c, int(170 * dim)), 1.4, Qt.PenStyle.DashLine))
                p.drawEllipse(QRectF(pos.x() - dot_r, pos.y() - dot_r, 2 * dot_r, 2 * dot_r))
            else:
                # Radial speed tail: outward = moving away (s > 0), inward = approaching.
                spd = float(t.get("speed_mps", 0.0))
                if abs(spd) >= 0.05:
                    a = math.radians(90.0 + t.get("bearing_deg", 0.0))
                    ux, uy = math.cos(a), -math.sin(a)      # outward unit vector (screen)
                    L = min(24.0, 6.0 + abs(spd) * 22.0) * (1.0 if spd > 0 else -1.0)
                    p.setPen(QPen(self._conf_color(c, int(200 * dim)), 2))
                    p.drawLine(pos, QPointF(pos.x() + ux * L, pos.y() + uy * L))
                p.setPen(QPen(col, 1.5))
                p.setBrush(QColor(col.red(), col.green(), col.blue(), int(180 * dim)))
                p.drawEllipse(QRectF(pos.x() - dot_r, pos.y() - dot_r, 2 * dot_r, 2 * dot_r))
                # Seam agreement: two+ sensors merged into this target.
                if bin(int(t.get("sensors", 0))).count("1") >= 2:
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    p.setPen(QPen(QColor(230, 240, 250, int(190 * dim)), 1.2))
                    hr = dot_r + 2.5
                    p.drawEllipse(QRectF(pos.x() - hr, pos.y() - hr, 2 * hr, 2 * hr))
            label = f"{t.get('range_m', 0.0):.1f}m {t.get('bearing_deg', 0.0):+.0f}°"
            p.setPen(QColor(185, 205, 225, int(230 * dim)))
            ly = pos.y() + dot_r + 3 if pos.y() < cy else pos.y() - dot_r - 16
            p.drawText(QRectF(pos.x() - 40, ly, 80, 14),
                       Qt.AlignmentFlag.AlignCenter, label)

        # Status chips: ring health (left) and target count / latch (right).
        if not self._connected:
            self._chip(p, QRectF(6, 6, 118, 19), QColor(120, 130, 145), "RING NO LINK", dim)
        elif not self._fresh:
            self._chip(p, QRectF(6, 6, 118, 19), QColor(240, 180, 60), "RING STALE", dim)
        elif self._ok:
            self._chip(p, QRectF(6, 6, 118, 19), QColor(70, 200, 130),
                       f"RING OK · {self._up}/{n_sens}", dim)
        else:
            self._chip(p, QRectF(6, 6, 118, 19), QColor(235, 70, 60),
                       f"RING DOWN 0/{n_sens}", dim)
        if latch_mode:
            self._chip(p, QRectF(w - 106, 6, 100, 19), QColor(240, 180, 60),
                       f"LATCHED ×{len(self._latched)}", dim)
        elif self._targets:
            self._chip(p, QRectF(w - 106, 6, 100, 19), QColor(130, 215, 255),
                       f"TARGETS {len(self._targets)}", dim)

        # Footer: per-sensor transport health + orientation legend.
        fy = h - foot_h + 4.0
        for i in range(n_sens):
            mount = self._mounts[i] if i < len(self._mounts) else 0.0
            s = self._sens[i] if i < len(self._sens) else None
            if s is None:
                col, stats = QColor(120, 130, 145), "—"
            else:
                col = QColor(70, 200, 130) if s.get("ok") else QColor(235, 70, 60)
                stats = (f"{int(s.get('frames', 0))} fr · {int(s.get('bad', 0))} bad · "
                         f"{int(s.get('drop', 0))} drop")
            if i < len(self._cfg) and not self._cfg[i]:
                stats += " · cfg✗"
            p.setPen(QColor(col.red(), col.green(), col.blue(), int(255 * dim)))
            p.drawText(QRectF(8, fy + 14.0 * i, w - 16, 14),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"S{i} {mount:+.0f}°  {stats}")
        p.setPen(QColor(120, 135, 155, int(170 * dim)))
        legend = "front=up · +bearing=CCW · 8 m full scale"
        if self._errs:
            legend = f"errs {self._errs} · " + legend
        p.drawText(QRectF(8, h - 16, w - 16, 14),
                   Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, legend)

        if not live:
            p.setPen(QColor(150, 165, 185))
            f.setPointSize(10); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(0, cy - 12, w, 24), Qt.AlignmentFlag.AlignCenter,
                       "no link" if not self._connected else "no telemetry")
        p.end()


class GamepadMirrorWidget(QWidget):
    """Read-only mirror of the PHYSICAL gamepad paired to the ESP32.

    Driven by the `gp` object in the motion telemetry (lx/ly stick + a pressed-button
    bitmask), so moving the real stick moves the on-screen dot and held buttons light
    up. Display-only — it never drives the base (the software JoystickWidget owns that).
    Convention matches JoystickWidget: lx right-positive, ly UP-positive."""

    # Bit order MUST match the firmware mask in gamepad.cpp (GP_BTN_* comment).
    _GP_BTN_LABELS = ["A", "B", "X", "Y", "L1", "R1", "L2", "R2",
                      "↑", "↓", "←", "→", "Sel", "Strt", "Home", "L3", "R3"]

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 224)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._lx = 0.0
        self._ly = 0.0
        self._btn = 0
        self._live = False

    def set_state(self, lx, ly, btn_mask, connected: bool = True) -> None:
        try:
            self._lx = max(-1.0, min(1.0, float(lx)))
            self._ly = max(-1.0, min(1.0, float(ly)))
        except (TypeError, ValueError):
            self._lx = self._ly = 0.0
        try:
            self._btn = int(btn_mask)
        except (TypeError, ValueError):
            self._btn = 0
        self._live = bool(connected)
        self.update()

    def clear(self) -> None:
        self._lx = self._ly = 0.0
        self._btn = 0
        self._live = False
        self.update()

    def paintEvent(self, _e) -> None:
        w, h = float(self.width()), float(self.height())
        dim = 1.0 if self._live else 0.35
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # --- Stick dial (top portion) ---
        dial_h = h * 0.58
        cx = w / 2.0
        cy = dial_h / 2.0 + 4.0
        R = min(w, dial_h) / 2.0 - 10.0
        if R >= 20:
            rk = R * 0.28
            travel = max(1.0, R - rk)
            # Bezel + inner well (mirrors JoystickWidget styling, dimmable).
            p.setPen(QPen(QColor(70, 80, 95, int(255 * dim)), 2))
            p.setBrush(QBrush(QColor(28, 33, 41, int(255 * dim))))
            p.drawEllipse(QRectF(cx - R, cy - R, 2 * R, 2 * R))
            well = R * 0.82
            p.setPen(QPen(QColor(48, 56, 68, int(255 * dim)), 1))
            p.setBrush(QBrush(QColor(18, 22, 28, int(255 * dim))))
            p.drawEllipse(QRectF(cx - well, cy - well, 2 * well, 2 * well))
            # Crosshair + N/E/S/W ticks.
            p.setPen(QPen(QColor(90, 150, 200, int(120 * dim)), 1))
            p.drawLine(QPointF(cx - well, cy), QPointF(cx + well, cy))
            p.drawLine(QPointF(cx, cy - well), QPointF(cx, cy + well))
            # Live knob position (dot).
            kx, ky = cx + self._lx * travel, cy - self._ly * travel   # up = +ly
            grad = QRadialGradient(kx - rk * 0.3, ky - rk * 0.35, rk * 1.5)
            grad.setColorAt(0.0, QColor(130, 215, 255, int(255 * dim)))
            grad.setColorAt(1.0, QColor(36, 86, 134, int(255 * dim)))
            p.setBrush(QBrush(grad))
            p.setPen(QPen(QColor(190, 235, 255, int(255 * dim)), 2))
            p.drawEllipse(QRectF(kx - rk, ky - rk, 2 * rk, 2 * rk))

        # --- Button pips (bottom portion) ---
        labels = self._GP_BTN_LABELS
        pad, gap, pip_h = 6.0, 5.0, 18.0
        grid_top = dial_h + 4.0
        avail_w = w - 2 * pad
        pip_w = 36.0
        cols = max(1, int((avail_w + gap) // (pip_w + gap)))
        pip_w = (avail_w - (cols - 1) * gap) / cols   # stretch to fill the row
        f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
        for i, lab in enumerate(labels):
            r, c = divmod(i, cols)
            x = pad + c * (pip_w + gap)
            y = grid_top + r * (pip_h + gap)
            pressed = bool(self._btn & (1 << i))
            if pressed and self._live:
                p.setBrush(QColor(70, 200, 130))
                p.setPen(QPen(QColor(140, 240, 180), 1))
                txtcol = QColor(8, 20, 14)
            else:
                p.setBrush(QColor(34, 40, 50, int(255 * dim)))
                p.setPen(QPen(QColor(70, 80, 95, int(255 * dim)), 1))
                txtcol = QColor(150, 165, 185, int(255 * dim))
            p.drawRoundedRect(QRectF(x, y, pip_w, pip_h), 4, 4)
            p.setPen(txtcol)
            p.drawText(QRectF(x, y, pip_w, pip_h), Qt.AlignmentFlag.AlignCenter, lab)

        if not self._live:
            p.setPen(QColor(150, 165, 185))
            f.setPointSize(10); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(0, cy - 12, w, 24),
                       Qt.AlignmentFlag.AlignCenter, "no pad connected")
        p.end()


class AttitudeWidget(QWidget):
    """3D attitude indicator for the IMU (LSM6DS3) on the drive base.

    Renders a wireframe of the base (drive deck + droid body + heading arrow) rotated
    by the IMU's pitch/roll/yaw over a fixed ground ring, so tilt reads at a glance
    against the "floor". Pure QPainter (rotation matrices + orthographic projection
    through a fixed isometric camera) — no OpenGL dependency. Read-only: call
    set_attitude() from the telemetry tick; yaw is relative to the ESP32's boot
    heading, pitch/roll are gravity-referenced (firmware imu.cpp).
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(190)
        self._ok = False
        self._pitch = 0.0   # deg, + = nose up
        self._roll = 0.0    # deg, + = right side down
        self._yaw = 0.0     # deg, + = CCW from boot heading

        # Body wireframe in the body frame: x forward, y left, z up (REP-103-ish),
        # unit ≈ base radius. Deck = flat box; body = narrower tall box; arrow = front.
        deck = self._box(1.00, 1.00, 0.16, z0=0.0)
        body = self._box(0.52, 0.52, 0.85, z0=0.16)
        arrow = [((0.62, 0.22, 0.05), (1.05, 0.0, 0.05)),
                 ((0.62, -0.22, 0.05), (1.05, 0.0, 0.05))]
        self._edges = deck + body
        self._arrow = arrow

    @staticmethod
    def _box(lx: float, ly: float, lz: float, z0: float) -> list:
        """Edge list of an axis-aligned box: half-extents lx/ly, height lz above z0."""
        b, t = z0, z0 + lz
        c = [(sx * lx, sy * ly, z) for z in (b, t) for sx in (-1, 1) for sy in (-1, 1)]
        idx = [(0, 1), (1, 3), (3, 2), (2, 0),        # bottom ring
               (4, 5), (5, 7), (7, 6), (6, 4),        # top ring
               (0, 4), (1, 5), (2, 6), (3, 7)]        # verticals
        return [(c[i], c[j]) for i, j in idx]

    def set_attitude(self, pitch: float, roll: float, yaw: float, ok: bool) -> None:
        if (ok != self._ok or abs(pitch - self._pitch) > 0.05
                or abs(roll - self._roll) > 0.05 or abs(yaw - self._yaw) > 0.05):
            self._ok, self._pitch, self._roll, self._yaw = ok, pitch, roll, yaw
            self.update()

    def clear(self) -> None:
        if self._ok:
            self._ok = False
            self.update()

    # ---- 3D math (row-vector · matrix chains, orthographic projection) ----
    @staticmethod
    def _rot(p, pitch_r: float, roll_r: float, yaw_r: float):
        x, y, z = p
        cy, sy = math.cos(yaw_r), math.sin(yaw_r)      # yaw about z (CCW+)
        x, y = x * cy - y * sy, x * sy + y * cy
        cp, sp = math.cos(pitch_r), math.sin(pitch_r)  # pitch about y (+ = nose up)
        x, z = x * cp + z * sp, -x * sp + z * cp
        cr, sr = math.cos(roll_r), math.sin(roll_r)    # roll about x (+ = right down)
        y, z = y * cr - z * sr, y * sr + z * cr
        return x, y, z

    def _project(self, p, cx: float, cy: float, scale: float) -> QPointF:
        # Fixed isometric-ish camera: yaw the scene 35° for depth, tilt 55° down.
        x, y, z = p
        ca, sa = math.cos(math.radians(35)), math.sin(math.radians(35))
        x, y = x * ca - y * sa, x * sa + y * ca
        ct, st = math.cos(math.radians(55)), math.sin(math.radians(55))
        sy_ = y * ct + z * st          # screen-vertical component
        return QPointF(cx + x * scale, cy - sy_ * scale)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        cx, cy = w / 2.0, h / 2.0 + 8
        scale = min(w, h) * 0.34
        dim = 1.0 if self._ok else 0.35

        # Ground reference ring + cardinal ticks (world frame — never rotates).
        p.setPen(QPen(QColor(90, 150, 200, int(70 * dim)), 1))
        ring = [self._project((math.cos(a) * 1.35, math.sin(a) * 1.35, 0.0), cx, cy, scale)
                for a in [i * math.pi / 24 for i in range(49)]]
        for a, b in zip(ring, ring[1:]):
            p.drawLine(a, b)
        for ang, lab in ((0.0, "F"), (math.pi / 2, "L"), (math.pi, "B"), (-math.pi / 2, "R")):
            tp = self._project((math.cos(ang) * 1.52, math.sin(ang) * 1.52, 0.0), cx, cy, scale)
            p.setPen(QColor(150, 165, 185, int(150 * dim)))
            f = p.font(); f.setPointSize(8); f.setBold(True); p.setFont(f)
            p.drawText(QRectF(tp.x() - 8, tp.y() - 8, 16, 16), Qt.AlignmentFlag.AlignCenter, lab)

        pr, rr, yr = (math.radians(self._pitch), math.radians(self._roll),
                      math.radians(self._yaw))

        def draw_edges(edges, pen):
            p.setPen(pen)
            for a, b in edges:
                pa = self._project(self._rot(a, pr, rr, yr), cx, cy, scale)
                pb = self._project(self._rot(b, pr, rr, yr), cx, cy, scale)
                p.drawLine(pa, pb)

        draw_edges(self._edges, QPen(QColor(90, 170, 220, int(210 * dim)), 1.6))
        draw_edges(self._arrow, QPen(QColor(240, 180, 70, int(230 * dim)), 2.2))

        # Numeric readout strip.
        p.setPen(QColor(150, 165, 185, int(220 * dim)))
        f = p.font(); f.setPointSize(9); f.setBold(False); p.setFont(f)
        txt = (f"pitch {self._pitch:+.1f}°   roll {self._roll:+.1f}°   yaw {self._yaw:+.1f}°"
               if self._ok else "no IMU detected")
        p.drawText(QRectF(0, h - 20, w, 18), Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


def ramp_toward(current: float, target: float, accel_step: float, decel_step: float) -> float:
    """V2 motion_controller.ramp_toward: gentle up, faster (never abrupt) down; a reversal
    decelerates through zero first."""
    if target == current:
        return target
    speeding_up = abs(target) > abs(current) and (current == 0.0 or (target > 0.0) == (current > 0.0))
    step = accel_step if speeding_up else decel_step
    if step <= 0.0:
        return target
    delta = target - current
    if delta > step:
        return current + step
    if delta < -step:
        return current - step
    return target


RAMP_UP_SECONDS, RAMP_DOWN_SECONDS = 1.2, .5   # V2 MOTION_MANUAL_RAMP_UP/DOWN_SECS


class MotivatorControlDialog(QDialog):
    """Joystick console with live drive-base and radar readouts.

    Opening it does not take the wheels: TAKE THE WHEELS asks the drive owner for manual
    mode (Rex's plans are then refused and the arms park before the first move); RELEASE,
    STOP, closing the window or shutdown give them back. Arcade mixing: stick up drives
    forward, stick right turns right; sent as lin m/s / ang rad/s at 10 Hz by the feed."""

    def __init__(self, feed, parent=None) -> None:
        super().__init__(parent)
        from ..drive import MANUAL_SPEED, MANUAL_TURN
        self._feed = feed
        self._max_lin, self._max_ang = MANUAL_SPEED, MANUAL_TURN
        self.setWindowTitle("Motivator Control")
        self.setObjectName("motivatorDialog")
        self.setModal(False)
        self.setStyleSheet(theme.DIALOG_STYLE)
        self.resize(1500, 780)
        self._x = self._y = 0.0
        self._cmd_lin = self._cmd_ang = 0.0

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)
        self._conn = QLabel("…")
        self._conn.setObjectName("motivatorConn")
        self._conn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._conn.setWordWrap(True)
        root.addWidget(self._conn)

        body = QHBoxLayout()
        body.setSpacing(14)
        root.addLayout(body, 1)

        def _panel(title: str, widget: QWidget) -> QFrame:
            frame = QFrame()
            frame.setObjectName("chromePanel")
            lay = QVBoxLayout(frame)
            lay.setContentsMargins(12, 10, 12, 10)
            lay.setSpacing(8)
            head = QLabel(title)
            head.setObjectName("panelTitle")
            lay.addWidget(head)
            lay.addWidget(widget, 1)
            return frame

        left = QVBoxLayout()
        left.setSpacing(12)
        self._take = QPushButton("TAKE THE WHEELS")
        self._take.setObjectName("servoOverrideButton")
        self._take.setCheckable(True)
        self._take.setToolTip("Hold manual control of the drive base; Rex's own and requested drives are refused meanwhile.")
        self._take.toggled.connect(self._toggle_manual)
        left.addWidget(self._take)
        self.joystick = JoystickWidget()
        self.joystick.moved.connect(self._on_move)
        self.joystick.released.connect(lambda: self._on_move(0.0, 0.0))
        left.addWidget(self.joystick, 1)
        cmd = self._section("COMMANDED (this console)")
        self._lbl_lin = self._row(cmd, "Linear")
        self._lbl_ang = self._row(cmd, "Angular")
        self._lbl_sent = self._row(cmd, "Drive owner")
        left.addWidget(cmd["frame"])
        body.addLayout(left, 4)

        self._radar = RadarRingWidget()
        body.addWidget(_panel("RADAR RING (3× LD2450)", self._radar), 4)

        mid = QVBoxLayout()
        mid.setSpacing(12)
        self._photoreceptors = DistancePhotoreceptorsWidget()
        mid.addWidget(_panel("DISTANCE PHOTORECEPTORS", self._photoreceptors), 5)
        self._matrix = TofMatrixWidget()
        mid.addWidget(_panel("8×8 MATRIX ToF (front)", self._matrix), 5)
        body.addLayout(mid, 4)

        right = QVBoxLayout()
        right.setSpacing(12)
        self._gamepad = GamepadMirrorWidget()
        right.addWidget(_panel("PHYSICAL CONTROLLER", self._gamepad), 3)
        self._attitude = AttitudeWidget()
        right.addWidget(_panel("ATTITUDE (LSM6DS3)", self._attitude), 2)
        fb = self._section("ESP32 FEEDBACK")
        self._fb_state = self._row(fb, "State")
        self._fb_owner = self._row(fb, "Owner / gamepad")
        self._fb_zone = self._row(fb, "Zone / blocked")
        self._fb_odom = self._row(fb, "Odom lin / ang")
        self._fb_pose = self._row(fb, "Pose x / y / θ")
        self._fb_tof = self._row(fb, "ToF FL/FR/RL/RR (long)")
        self._fb_tof2 = self._row(fb, "ToF LF/LB/RF/RB (short)")
        self._fb_batt = self._row(fb, "Battery")
        self._fb_fault = self._row(fb, "Fault / errs")
        right.addWidget(fb["frame"])
        body.addLayout(right, 4)

        self._stop_btn = QPushButton("■  STOP  ■")
        self._stop_btn.setObjectName("motivatorStop")
        self._stop_btn.clicked.connect(self._stop)
        root.addWidget(self._stop_btn)

        self._send_timer = QTimer(self)
        self._send_timer.timeout.connect(self._tick_send)
        self._tel_timer = QTimer(self)
        self._tel_timer.timeout.connect(self._tick_telemetry)

    def _section(self, title: str) -> dict:
        frame = QFrame()
        frame.setObjectName("chromePanel")
        lay = QGridLayout(frame)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setVerticalSpacing(4)
        lay.setHorizontalSpacing(10)
        head = QLabel(title)
        head.setObjectName("panelTitle")
        lay.addWidget(head, 0, 0, 1, 2)
        return {"lay": lay, "frame": frame, "row": 1}

    def _row(self, section: dict, label: str) -> QLabel:
        lay = section["lay"]
        r = section["row"]
        section["row"] = r + 1
        name = QLabel(label)
        name.setObjectName("servoName")
        val = QLabel("—")
        val.setObjectName("servoValue")
        val.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(name, r, 0)
        lay.addWidget(val, r, 1)
        lay.setColumnStretch(0, 1)
        return val

    def _toggle_manual(self, on: bool) -> None:
        self._zero()
        ok, reason = self._feed.teleop.enable(on)
        if not ok:
            self._take.blockSignals(True)
            self._take.setChecked(False)
            self._take.blockSignals(False)
            self._lbl_sent.setText(f"refused: {reason}")

    def _on_move(self, x: float, y: float) -> None:
        self._x, self._y = x, y

    def _zero(self) -> None:
        self.joystick._recenter()
        self._x = self._y = 0.0
        self._cmd_lin = self._cmd_ang = 0.0

    def _tick_send(self) -> None:
        if math.hypot(self._x, self._y) > 0.02:   # Deadzone: ignore resting jitter.
            lin_t, ang_t = self._y * self._max_lin, -self._x * self._max_ang   # Stick left turns left (+ang).
        else:
            lin_t = ang_t = 0.0
        dt = (self._send_timer.interval() / 1000.0) or 0.1
        self._cmd_lin = ramp_toward(self._cmd_lin, lin_t, self._max_lin * dt / RAMP_UP_SECONDS,
                                    self._max_lin * dt / RAMP_DOWN_SECONDS)
        self._cmd_ang = ramp_toward(self._cmd_ang, ang_t, self._max_ang * dt / RAMP_UP_SECONDS,
                                    self._max_ang * dt / RAMP_DOWN_SECONDS)
        self._lbl_lin.setText(f"{self._cmd_lin:+.2f} m/s")
        self._lbl_ang.setText(f"{math.degrees(self._cmd_ang):+.0f}°/s")
        if self._feed.teleop.active:
            self._feed.teleop.set(self._cmd_lin, self._cmd_ang)

    def _tick_telemetry(self) -> None:
        drive = self._feed.snapshot_drive()
        manual = drive.get("manual") or {}
        active = bool(manual.get("active"))
        if self._take.isChecked() != active or self._take.property("active") != active:
            self._take.blockSignals(True)
            self._take.setChecked(active)
            self._take.blockSignals(False)
            self._take.setProperty("active", active)   # The override button's lit style (theme).
            self._take.style().unpolish(self._take)
            self._take.style().polish(self._take)
        last = manual.get("last") or {}
        if last:
            held = ", ".join(last.get("held") or ())
            self._lbl_sent.setText(last.get("status", "—") + (f": {last['reason']}" if last.get("reason") else "")
                                   + (f" (held: {held})" if held else ""))
        radar = drive.get("radar")
        if radar:
            self._radar.set_state(radar.get("telemetry"), None, radar.get("hello"), True)
        else:
            self._radar.set_state(None, [], None, False)
        connected = drive.get("connected")
        available, reason = drive.get("available") or (False, None)
        if connected:
            text = "Drive ESP32 connected" + (" + radar ring" if radar else " (radar ring down)")
            text += (" — you hold the wheels; Rex's drives are refused" if active else
                     " — Rex has the wheels; TAKE THE WHEELS to drive by hand")
            if not available and reason:
                text += f" — not available now: {reason.replace('_', ' ')}"
        else:
            text = ("Radar ring connected; drive ESP32 down" if radar else
                    "Drive ESP32 + radar ring disconnected — set drive_port / radar_port in local/settings.toml")
        self._conn.setText(text)
        self._conn.setProperty("ok", bool(connected))
        self._conn.style().unpolish(self._conn)
        self._conn.style().polish(self._conn)
        self._take.setEnabled(bool(connected))

        tel = drive.get("telemetry")
        if not tel:
            for lbl in (self._fb_state, self._fb_owner, self._fb_zone, self._fb_odom,
                        self._fb_pose, self._fb_tof, self._fb_tof2, self._fb_batt, self._fb_fault):
                lbl.setText("—")
            self._photoreceptors.clear()
            self._matrix.clear()
            self._gamepad.clear()
            self._attitude.clear()
            return
        odom = tel.get("odom") or {}
        tof = tel.get("tof_mm") or {}

        def g(d, k, default=0.0):
            try:
                return float(d.get(k, default))
            except (TypeError, ValueError):
                return default

        self._fb_state.setText(str(tel.get("state", "—")))
        self._fb_owner.setText(f"{tel.get('owner', '—')} / {tel.get('gamepad', '—')}")
        self._fb_zone.setText(f"{tel.get('zone', '—')} / {tel.get('blocked_dir', '—')}")
        self._fb_odom.setText(f"{g(odom, 'lin'):+.2f} m/s / {math.degrees(g(odom, 'ang')):+.0f}°/s")
        self._fb_pose.setText(f"{g(odom, 'x'):+.2f} / {g(odom, 'y'):+.2f} / {math.degrees(g(odom, 'theta')):+.0f}°")
        self._fb_tof.setText(f"{tof.get('fl', '—')} / {tof.get('fr', '—')} / {tof.get('rl', '—')} / {tof.get('rr', '—')} mm")
        self._fb_tof2.setText(f"{tof.get('lf', '—')} / {tof.get('lb', '—')} / {tof.get('rf', '—')} / {tof.get('rb', '—')} mm")
        mv, ma, soc = g(tel, "batt_mv", -1.0), g(tel, "batt_ma"), g(tel, "batt_soc", -1.0)
        volt_s = f"{mv / 1000.0:.2f} V" if mv > 0 else "— V"
        amp_s = f" / {ma / 1000.0:+.2f} A" if abs(ma) >= 1000 else (f" / {ma:+.0f} mA" if ma != 0 else "")
        self._fb_batt.setText(volt_s + amp_s + (f"  ·  {soc:.0f}%" if soc >= 0 else ""))
        self._fb_fault.setText(f"{tel.get('fault') or 'none'} / errs {tel.get('errs', 0)}")
        matrix = drive.get("matrix")
        if matrix:
            self._matrix.set_frame(matrix.get("grid"), matrix.get("rej"))
        else:
            self._matrix.clear()
        self._photoreceptors.set_readings(tof, tel.get("zone"), tel.get("blocked_dir"),
                                          mx_mm=self._matrix.nearest_obstacle_mm())
        gp = tel.get("gp") or {}
        if gp.get("connected"):
            self._gamepad.set_state(gp.get("lx", 0.0), gp.get("ly", 0.0), gp.get("btn", 0), True)
        else:
            self._gamepad.clear()
        imu = tel.get("imu") or {}
        if imu.get("ok"):
            self._attitude.set_attitude(g(imu, "pitch"), g(imu, "roll"), g(imu, "yaw"), True)
        else:
            self._attitude.clear()

    def _stop(self) -> None:
        """Immediate stop (releasing the stick ramps down instead) and the wheels go back to Rex."""
        self._zero()
        self._feed.stop_wheels()
        if self._take.isChecked():
            self._take.setChecked(False)

    def showEvent(self, e) -> None:
        self._zero()
        if not self._send_timer.isActive():
            self._send_timer.start(100)
        if not self._tel_timer.isActive():
            self._tel_timer.start(150)
        self._tick_telemetry()
        super().showEvent(e)

    def closeEvent(self, e) -> None:
        self._send_timer.stop()
        self._tel_timer.stop()
        self._zero()
        if self._feed.teleop.active:
            self._feed.teleop.enable(False)
        super().closeEvent(e)
