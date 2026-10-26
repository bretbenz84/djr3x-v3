"""The Jeopardy board for the dashboard (V2 gui/jeopardy_panel.py, adapted; owner, October 26).

The owner's table plays with the Mac mirrored to an Apple TV, so the board is the room's screen:
categories and values left, the clue while it is open (with the answer clock), wagers, Final
Jeopardy and the players' scores. The dashboard switches to it while a game is on. V2's 3D droid
in the mascot column is left out: a second Qt Quick 3D view would double the render cost on the
robot's Mac. Painting only; it reads `feed.snapshot()['jeopardy']` (jeopardy.Jeopardy.snapshot).
"""
from __future__ import annotations

import random
import time
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme

PLAYER_COLORS = ("#006fe8", "#d01818", "#32a326", "#8b24d2")


class JeopardyPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._state: dict[str, Any] = {}
        self._speaking = False
        self._reveal_key: tuple[str, ...] = ()
        self._revealed = 0
        self._reveal_at = 0.0
        self.setMinimumSize(980, 640)

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        self._state = dict(snapshot.get("jeopardy") or {})
        self._speaking = snapshot.get("activity") == "speaking"
        self._update_reveal()
        self.update()

    # V2: on a fresh board the categories light up one by one while Rex reads them.
    def _update_reveal(self) -> None:
        names = tuple(str(c.get("name") or "") for c in self._state.get("categories") or [])
        now = time.monotonic()
        if names != self._reveal_key:
            self._reveal_key, self._revealed, self._reveal_at = names, 0, now
        if not names or self._state.get("phase") not in {"selecting", "answering", "wager"}:
            self._revealed = len(names)
            return
        if self._state.get("phase") != "selecting":
            self._revealed = len(names)
        elif self._speaking:
            if self._revealed <= 0:
                self._revealed, self._reveal_at = 1, now
            elif self._revealed < len(names) and now - self._reveal_at >= 0.85:
                self._revealed, self._reveal_at = self._revealed + 1, now
        elif self._revealed > 0 and now - self._reveal_at > 1.6:
            self._revealed = len(names)
        elif self._revealed == 0 and now - self._reveal_at > 4.0:
            self._revealed = len(names)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._background(painter)
        bounds = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        side_w = min(270.0, bounds.width() * 0.2)
        players_h = min(190.0, max(150.0, bounds.height() * 0.22))
        gap = 10.0
        side = QRectF(bounds.left(), bounds.top(), side_w, bounds.height() - players_h - gap)
        board = QRectF(side.right() + gap, bounds.top(), bounds.width() - side_w - gap, side.height())
        players = QRectF(bounds.left(), board.bottom() + gap, bounds.width(), players_h)
        self._side(painter, side)
        self._board(painter, board)
        self._players(painter, players)
        painter.end()

    # Panels ------------------------------------------------------------------------------------------

    def _background(self, painter: QPainter) -> None:
        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor("#071019"))
        grad.setColorAt(0.55, QColor(theme.SPACE_BLACK))
        grad.setColorAt(1, QColor("#02060c"))
        painter.fillRect(self.rect(), grad)
        rng = random.Random(3263827)   # The same starfield every frame, like the console's.
        painter.setPen(Qt.PenStyle.NoPen)
        for _ in range(int(self.width() * self.height() / 7000)):
            b = rng.randint(60, 170)
            painter.setBrush(QColor(b, b + 10, min(255, b + 30), rng.randint(80, 160)))
            painter.drawEllipse(QPointF(rng.uniform(0, self.width()), rng.uniform(0, self.height())), 0.9, 0.9)

    def _side(self, painter: QPainter, rect: QRectF) -> None:
        theme.paint_panel_chrome(painter, rect)
        inner = rect.adjusted(16, 18, -16, -18)
        painter.setPen(QColor("#f3f7ff"))
        painter.setFont(_font(16))
        painter.drawText(QRectF(inner.left(), inner.top(), inner.width(), 30), Qt.AlignmentFlag.AlignHCenter, "WELCOME TO")
        painter.setPen(QColor(theme.AMBER))
        painter.setFont(_font(int(min(34, inner.width() / 7))))
        painter.drawText(QRectF(inner.left(), inner.top() + 28, inner.width(), 50), Qt.AlignmentFlag.AlignHCenter,
                         "JEOPARDY!")
        round_no = int(self._state.get("round") or 1)
        box = QRectF(inner.left(), inner.top() + 96, inner.width(), 92)
        _blue(painter, box, dim=True)
        painter.setPen(QColor("#f8fbff"))
        painter.setFont(_font(14))
        painter.drawText(box.adjusted(4, 8, -4, -4), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         "FINAL" if round_no == 3 else "ROUND")
        painter.setPen(QColor(theme.AMBER))
        painter.setFont(_font(30 if round_no < 3 else 22))
        painter.drawText(box.adjusted(4, 34, -4, -4), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         {1: "1", 2: "DOUBLE", 3: "JEOPARDY!"}.get(round_no, str(round_no)))
        prompt = QRectF(inner.left(), box.bottom() + 18, inner.width(), 90)
        painter.setPen(QColor("#f3f7ff"))
        painter.setFont(_font(15))
        painter.drawText(prompt, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
                         prompt_text(self._state))
        clock = self._state.get("clock_left")
        if clock is not None:
            self._clock(painter, QRectF(inner.left(), prompt.bottom() + 6, inner.width(), 34), float(clock))
        last = self._state.get("last_ruling")
        if last and self._state.get("phase") in {"selecting", "answering"}:
            note = QRectF(inner.left(), inner.bottom() - 96, inner.width(), 96)
            _blue(painter, note, dim=True)
            painter.setFont(_font(12))
            painter.setPen(QColor("#d8e0ea"))
            change = int(last.get("change") or 0)
            verdict = {"correct": "RIGHT", "wrong_answer": "WRONG", "right_but_not_a_question": "NOT A QUESTION",
                       "pass": "PASS", "time_up": "TIME'S UP"}.get(str(last.get("verdict")), "")
            heard = str(last.get("heard") or "")
            text = f"{str(last.get('player') or '').upper()}: {verdict}"
            if change:
                text += f"  {'+' if change > 0 else '-'}${abs(change):,}"
            if heard:
                text += f"\n“{heard}”"
            painter.drawText(note.adjusted(8, 6, -8, -6), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, text)

    def _clock(self, painter: QPainter, rect: QRectF, left: float) -> None:
        """V2's podium lights as a bar: it empties as the answer clock runs down."""
        total = float(self._state.get("clock_seconds") or 12.0)
        share = max(0.0, min(1.0, left / total))
        painter.setPen(QPen(QColor("#1b3f78"), 1.4))
        painter.setBrush(QColor("#04090f"))
        painter.drawRoundedRect(rect, 4, 4)
        fill = QRectF(rect.left() + 3, rect.top() + 3, (rect.width() - 6) * share, rect.height() - 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#ff4d3d" if left < 4 else theme.AMBER))
        painter.drawRoundedRect(fill, 3, 3)
        painter.setPen(QColor("#f8fbff"))
        painter.setFont(_font(13))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{max(0, round(left))} s")

    def _board(self, painter: QPainter, rect: QRectF) -> None:
        theme.paint_panel_chrome(painter, rect)
        inner = rect.adjusted(18, 18, -18, -18)
        state, phase = self._state, self._state.get("phase")
        if state.get("clue"):
            self._card(painter, inner, title=_clue_title(state["clue"]), body=str(state["clue"].get("clue") or ""))
            return
        if state.get("wager") and phase == "wager":
            self._card(painter, inner, title="DAILY DOUBLE", body=f"{state['wager']['player'].upper()} WAGERS\n"
                       f"${state['wager']['min']:,} TO ${state['wager']['max']:,}", big_title=True)
            return
        if phase == "final_wager":
            self._card(painter, inner, title="FINAL JEOPARDY!", body=str(state.get("final_category") or "").upper(),
                       big_title=True)
            return
        if phase == "over":
            self._final_scores(painter, inner)
            return
        cats = list(state.get("categories") or [])[:6] or [{"name": "", "remaining_values": []} for _ in range(6)]
        values = list(state.get("values") or [200, 400, 600, 800, 1000])
        cols, rows = max(1, len(cats)), max(1, len(values))
        header_h = min(96.0, inner.height() * 0.17)
        cell_gap = 7.0
        col_w = (inner.width() - cell_gap * (cols - 1)) / cols
        row_h = (inner.height() - header_h - cell_gap * rows) / rows
        for col, category in enumerate(cats):
            revealed = col < self._revealed
            x = inner.left() + col * (col_w + cell_gap)
            header = QRectF(x, inner.top(), col_w, header_h)
            _blue(painter, header, dim=not revealed)
            name = str(category.get("name") or "").upper() if revealed else ""
            longest = max((len(word) for word in name.split()), default=len(name))
            painter.setPen(QColor("#f4f6ff"))
            painter.setFont(_font(max(10, int(min(18, col_w / max(7.0, longest * 0.72))))))
            painter.drawText(header.adjusted(8, 4, -8, -4), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, name)
            remaining = {int(v) for v in category.get("remaining_values") or []} if revealed else set()
            for row, value in enumerate(values):
                cell = QRectF(x, inner.top() + header_h + cell_gap + row * (row_h + cell_gap), col_w, row_h)
                available = int(value) in remaining
                _blue(painter, cell, dim=not available)
                if available:
                    painter.setPen(QColor(theme.AMBER))
                    painter.setFont(_font(max(14, int(min(30, row_h * 0.4, col_w / 4.6)))))
                    painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, f"${int(value):,}")

    def _card(self, painter: QPainter, rect: QRectF, *, title: str, body: str, big_title: bool = False) -> None:
        _blue(painter, rect)
        painter.setPen(QPen(QColor(theme.AMBER), 4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 7, 7)
        painter.setPen(QColor(theme.AMBER))
        painter.setFont(_font(max(16, int(min(46 if big_title else 26, rect.height() * (0.12 if big_title else 0.075))))))
        top = rect.adjusted(26, 20, -26, -20)
        painter.drawText(top, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, title)
        painter.setPen(QColor("#f7f8ff"))
        painter.setFont(_font(max(20, int(min(44, rect.height() * 0.11, rect.width() / 20.0)))))
        painter.drawText(rect.adjusted(54, rect.height() * 0.22, -54, -42),
                         Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, body)

    def _final_scores(self, painter: QPainter, rect: QRectF) -> None:
        _blue(painter, rect)
        players = sorted(self._state.get("players") or [], key=lambda p: -int(p.get("score") or 0))
        painter.setPen(QColor(theme.AMBER))
        painter.setFont(_font(int(min(46, rect.height() * 0.1))))
        painter.drawText(rect.adjusted(20, 24, -20, -20), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                         "FINAL SCORES")
        reveal = {r["player"]: r for r in self._state.get("reveal") or []}
        line_h = min(70.0, (rect.height() - 140) / max(1, len(players)))
        for i, player in enumerate(players):
            row = QRectF(rect.left() + 60, rect.top() + 110 + i * line_h, rect.width() - 120, line_h)
            painter.setPen(QColor("#f7f8ff"))
            painter.setFont(_font(int(min(32, line_h * 0.5))))
            name = str(player.get("name") or "").upper()
            said = reveal.get(player.get("name"))
            if said and said.get("heard"):
                name += f"  ·  “{said['heard']}”"
            painter.drawText(row, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, name)
            painter.setPen(QColor(theme.AMBER if i == 0 else "#f7f8ff"))
            painter.drawText(row, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                             _money(int(player.get("score") or 0)))

    def _players(self, painter: QPainter, rect: QRectF) -> None:
        theme.paint_panel_chrome(painter, rect)
        area = rect.adjusted(8, 8, -8, -8)
        players = list(self._state.get("players") or [])
        count = max(1, min(4, len(players)) or 1)
        gap = 10.0
        card_w = (area.width() - gap * (count - 1)) / count
        for idx in range(count):
            player = players[idx] if idx < len(players) else {"name": f"PLAYER {idx + 1}", "score": 0}
            card = QRectF(area.left() + idx * (card_w + gap), area.top(), card_w, area.height())
            self._player(painter, card, player, PLAYER_COLORS[idx % 4], up=bool(player.get("up")))

    def _player(self, painter: QPainter, rect: QRectF, player: dict, color: str, *, up: bool) -> None:
        theme.paint_panel_chrome(painter, rect)
        if up:
            painter.setPen(QPen(QColor(theme.AMBER), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(theme.panel_path(rect.adjusted(1.5, 1.5, -1.5, -1.5)))
        label = QRectF(rect.left() + 14, rect.top() + 10, rect.width() - 28, 40)
        painter.setPen(QPen(QColor("#14191f"), 2))
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(label, 2, 2)
        painter.setPen(QColor("#f8fbff"))
        painter.setFont(_font(max(12, int(min(22, label.width() / 9)))))
        painter.drawText(label.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignCenter, str(player.get("name") or "").upper())
        portrait = QRectF(rect.left() + 14, label.bottom() + 8, rect.width() * 0.3, rect.bottom() - label.bottom() - 20)
        grad = QLinearGradient(portrait.topLeft(), portrait.bottomRight())
        grad.setColorAt(0, QColor(color).darker(260))
        grad.setColorAt(1, QColor(color).darker(120))
        painter.setPen(QPen(QColor(color), 2))
        painter.setBrush(grad)
        painter.drawRoundedRect(portrait, 2, 2)
        _silhouette(painter, portrait, QColor(color))
        score = QRectF(portrait.right() + 12, label.bottom() + 8, rect.right() - portrait.right() - 26, portrait.height())
        painter.setPen(QPen(QColor("#222830"), 2))
        painter.setBrush(QColor("#020304"))
        painter.drawRoundedRect(score, 2, 2)
        value = int(player.get("score") or 0)
        painter.setPen(QColor("#ff6b5e" if value < 0 else theme.AMBER))
        painter.setFont(_font(max(16, int(min(40, score.height() * 0.45, score.width() / 5)))))
        painter.drawText(score, Qt.AlignmentFlag.AlignCenter, _money(value))


def prompt_text(state: dict) -> str:
    phase = state.get("phase")
    up = next((p.get("name") for p in state.get("players") or [] if p.get("up")), "")
    if phase == "players":
        return "WHO'S PLAYING?"
    if phase == "answering":
        return f"{up.upper()}, YOUR RESPONSE. PHRASE IT AS A QUESTION."
    if phase == "wager":
        return f"{up.upper()}, YOUR WAGER."
    if phase == "final_wager":
        return f"{up.upper()}, YOUR FINAL WAGER." if up else "FINAL JEOPARDY!"
    if phase == "final_answer":
        return f"{up.upper()}, YOUR RESPONSE." if up else "THINK..."
    if phase == "over":
        return "THANKS FOR PLAYING!"
    return f"{up.upper()}, CHOOSE A CATEGORY AND VALUE." if up else "CHOOSE A CATEGORY AND VALUE."


def _clue_title(clue: dict) -> str:
    category = str(clue.get("category") or "").upper()
    value = int(clue.get("value") or 0)
    if clue.get("final"):
        return f"FINAL JEOPARDY!  ·  {category}"
    if clue.get("daily_double"):
        return f"DAILY DOUBLE  ·  {category}  ·  ${value:,}"
    return f"{category}  ·  ${value:,}" if value else category


def _money(value: int) -> str:
    return f"-${abs(value):,}" if value < 0 else f"${value:,}"


def _font(size: int) -> QFont:
    font = QFont()
    font.setPointSize(max(6, int(size)))
    font.setBold(True)
    return font


def _blue(painter: QPainter, rect: QRectF, *, dim: bool = False) -> None:
    grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    grad.setColorAt(0, QColor("#081527" if dim else "#0a2f74"))
    grad.setColorAt(1, QColor("#04090f" if dim else "#03153c"))
    painter.setPen(QPen(QColor("#12233a" if dim else "#1b3f78"), 1.4))
    painter.setBrush(grad)
    painter.drawRoundedRect(rect, 2, 2)


def _silhouette(painter: QPainter, rect: QRectF, color: QColor) -> None:
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(color)
    cx = rect.center().x()
    head = min(rect.width(), rect.height()) * 0.2
    painter.drawEllipse(QRectF(cx - head, rect.top() + rect.height() * 0.2, head * 2, head * 2))
    body = QPainterPath()
    body.moveTo(cx, rect.top() + rect.height() * 0.56)
    body.cubicTo(rect.left() + rect.width() * 0.2, rect.top() + rect.height() * 0.62, rect.left() + rect.width() * 0.14,
                 rect.bottom() - 6, rect.left() + rect.width() * 0.14, rect.bottom() - 6)
    body.lineTo(rect.right() - rect.width() * 0.14, rect.bottom() - 6)
    body.cubicTo(rect.right() - rect.width() * 0.14, rect.bottom() - 6, rect.right() - rect.width() * 0.2,
                 rect.top() + rect.height() * 0.62, cx, rect.top() + rect.height() * 0.56)
    painter.drawPath(body)
