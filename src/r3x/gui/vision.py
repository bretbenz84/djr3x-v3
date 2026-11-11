"""Camera preview (V2 gui/vision_panel.py, adapted to V3's frame mailbox and rows).

The newest frame is peeked from the camera's mailbox (no consumer slot). Face boxes are
the face tracker's latest rows, labelled with the recognized person's name and, when the
expression reader is on, what the face shows; a face name is visibility, never the speaker. Object boxes are the paced detector's tracked rows.
V2's pose skeleton, animal channel and occlusion-zone overlay have no V3 source.
"""

from __future__ import annotations

import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

# Frames older than this read as a dropout: the chip shows "STALE Xs" and the held image
# is dimmed so a frozen frame isn't mistaken for a live one.
CAMERA_STALE_SECONDS = 2.0
FACE_COLOR, OBJECT_COLOR = "#75ef63", "#b98cff"
LOOK_COLOR = "#e0a526"   # The chip under a face showing a look (smiling, surprised, frowning).


class VisionPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._camera: dict[str, Any] = {}
        self._objects: list[dict[str, Any]] = []
        self._image: QImage | None = None
        self._frame_id = None
        self.setMinimumSize(360, 260)

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._camera = snapshot.get("camera") or {}
        self._objects = list(snapshot.get("objects") or [])
        frame = self._camera.get("frame")
        if frame is None:
            self._image, self._frame_id = None, None
        elif frame.frame_id != self._frame_id:
            self._frame_id = frame.frame_id
            self._image = QImage(frame.pixels, frame.width, frame.height, frame.width * 3,
                                 QImage.Format.Format_BGR888).copy()
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        frame_rect = QRectF(self.rect().adjusted(12, 6, -12, -10))
        image_rect = QRectF()
        if self._image is None:
            self._draw_placeholder(painter, frame_rect)
        else:
            image_rect = scaled_rect(self._image.width(), self._image.height(), frame_rect)
            painter.drawImage(image_rect, self._image)
            for obj in self._objects:
                label = str(obj.get("label") or "object")
                score = obj.get("score")
                self._draw_box(painter, image_rect, obj.get("box"), label,
                               f"{float(score) * 100:.0f}%" if isinstance(score, (int, float)) else "", OBJECT_COLOR)
            for face in self._camera.get("faces") or ():
                look = face.get("expression") or ""
                self._draw_box(painter, image_rect, face.get("box"), face.get("name") or "Unknown face", "",
                               FACE_COLOR, below=look,
                               below_color=LOOK_COLOR if look and not look.startswith("neutral") else FACE_COLOR)
            age = self._camera.get("age")
            if age is not None and age > CAMERA_STALE_SECONDS:
                painter.fillRect(image_rect, QColor(7, 17, 26, 150))
            self._draw_timestamp(painter, frame_rect)
        self._draw_camera_meta(painter, image_rect if not image_rect.isEmpty() else frame_rect)
        painter.end()

    def _draw_placeholder(self, painter: QPainter, rect: QRectF) -> None:
        painter.setPen(QPen(QColor("#274564"), 1))
        painter.setBrush(QColor("#0b1622"))
        painter.drawRoundedRect(rect, 4, 4)
        painter.setPen(QColor("#9badbf"))
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        painter.setFont(font)
        state = self._camera.get("state")
        text = {"disabled": "Camera off: camera_enabled is false in local/settings.toml.\n"
                            "Start with --camera to use it for one run.",
                "starting": "Camera starting…",
                "waiting": "Waiting for the first frame…",
                "failed": "Camera failed: " + str(self._camera.get("failure") or "see the Logs tab"),
                }.get(state, "Camera unavailable this run.")
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    def _draw_box(self, painter, image_rect, box, label, details, color_name, below="", below_color=None) -> None:
        try:
            x, y, w, h = (float(v) for v in box)
        except (TypeError, ValueError):
            return
        color = QColor(color_name)
        painter.setPen(QPen(color, 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(image_rect.left() + x * image_rect.width(), image_rect.top() + y * image_rect.height(),
                      w * image_rect.width(), h * image_rect.height())
        painter.drawRect(rect)
        draw_label(painter, rect.topLeft() + QPointF(0, -30), label, details, color)
        if below:   # What the face shows, under the box so close faces' labels stay apart.
            draw_label(painter, rect.bottomLeft() + QPointF(0, 6), below, "", QColor(below_color or color_name))

    def _draw_timestamp(self, painter: QPainter, frame_rect: QRectF) -> None:
        text = time.strftime("%I:%M:%S %p").lstrip("0")
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        box = QRectF(frame_rect.right() - metrics.horizontalAdvance(text) - 18, frame_rect.top() + 8,
                     metrics.horizontalAdvance(text) + 12, 28)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(7, 17, 26, 215))
        painter.drawRoundedRect(box, 3, 3)
        painter.setPen(QColor("#aebccc"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _draw_camera_meta(self, painter: QPainter, frame_rect: QRectF) -> None:
        """Live FPS (measured from new frame ids), or an honest STALE / NO SIGNAL chip."""
        age = self._camera.get("age")
        if age is None:
            text, color = "● NO SIGNAL", "#f0c45a"
        elif age > CAMERA_STALE_SECONDS:
            text, color = f"● STALE {age:.1f}s", "#f0c45a"
        else:
            fps = self._camera.get("fps")
            text, color = (f"● {float(fps):.0f} FPS" if fps else "● LIVE"), "#75ef63"
            if not self._camera.get("expressions"):
                text += "  ·  expressions off"
        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        width = painter.fontMetrics().horizontalAdvance(text) + 16
        chip = QRectF(frame_rect.left() + 6, frame_rect.bottom() - 26, width, 20)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(4, 10, 17, 200))
        painter.drawRoundedRect(chip, 2, 2)
        painter.setPen(QColor(color))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, text)


def scaled_rect(image_w: int, image_h: int, bounds: QRectF) -> QRectF:
    """Fit without cropping or stretching (V2)."""
    if image_w <= 0 or image_h <= 0:
        return QRectF(bounds)
    scale = min(bounds.width() / image_w, bounds.height() / image_h)
    w, h = image_w * scale, image_h * scale
    return QRectF(bounds.left() + (bounds.width() - w) / 2.0, bounds.top() + (bounds.height() - h) / 2.0, w, h)


def draw_label(painter: QPainter, anchor: QPointF, label: str, details: str, color: QColor) -> None:
    text = label if not details else f"{label}  {details}"
    font = QFont()
    font.setPointSize(10)
    font.setBold(True)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    width = min(max(metrics.horizontalAdvance(text) + 18, 70), 360)   # +4: bold text was elided at its exact width
    x = max(8.0, min(anchor.x(), painter.device().width() - width - 8))
    rect = QRectF(x, max(8.0, anchor.y()), width, 24)
    painter.setPen(Qt.PenStyle.NoPen)
    brush = QColor(color)
    brush.setAlpha(210)
    painter.setBrush(brush)
    painter.drawRoundedRect(rect, 2, 2)
    painter.setPen(QColor("#e9ffe6"))
    painter.drawText(rect.adjusted(7, 0, -7, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                     metrics.elidedText(text, Qt.TextElideMode.ElideRight, width - 14))
