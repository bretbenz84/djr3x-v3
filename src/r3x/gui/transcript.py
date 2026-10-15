"""Live transcript (V2 gui/conversation_panel.py, read-only).

Human lines are what the conversation actually took as turns, labelled with the speaker
attribution (a name with "?" is a hypothesis, not a verified voice). Rex's lines grow as
phrases are synthesized; an interrupted or failed reply is marked with what was heard.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFrame, QTextBrowser, QVBoxLayout, QWidget


class ConversationPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # The feed's revision changes when a line is added or grows in place (a reply
        # streaming phrase by phrase, a speaker label resolved, an interruption noted).
        self._last_revision: Optional[int] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        self._log = QTextBrowser()
        self._log.setObjectName("conversationLog")
        self._log.setOpenExternalLinks(False)
        self._log.setFrameShape(QFrame.Shape.NoFrame)
        self._log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self._log, 1)

        # Auto-scroll: keep the newest line pinned to the bottom. We apply the scroll
        # intent from rangeChanged (not right after setHtml) because QTextBrowser relays
        # its HTML out lazily — the scrollbar maximum is stale immediately after setHtml,
        # so pinning there races the layout and leaves new text below the fold.
        self._pin_to_bottom = True
        self._restore_value: Optional[int] = None
        self._log.verticalScrollBar().rangeChanged.connect(self._on_scroll_range_changed)

        self.setMinimumSize(310, 420)

    def set_snapshot(self, snapshot: dict[str, Any]) -> None:
        lines = list(snapshot.get("lines") or [])
        render_key = snapshot.get("revision")
        if render_key == self._last_revision:
            return

        scrollbar = self._log.verticalScrollBar()
        # Capture intent BEFORE the re-render, while the scrollbar still reflects the
        # OLD document: was the reader pinned to the newest line, or scrolled up reading
        # history? setHtml replaces the whole document and resets the scrollbar, and the
        # new range isn't final until the document relays out, so we only RECORD intent
        # here. _on_scroll_range_changed applies it once the range actually updates.
        self._pin_to_bottom = scrollbar.value() >= scrollbar.maximum() - 6
        self._restore_value = None if self._pin_to_bottom else scrollbar.value()

        self._log.setHtml(_format_lines(lines))
        self._last_revision = render_key
        self._apply_scroll_intent()

    def _on_scroll_range_changed(self, _minimum: int, _maximum: int) -> None:
        """Re-pin to the bottom whenever the document's scroll range settles.

        QTextBrowser relays its HTML out lazily, so rangeChanged can fire one or more
        times after setHtml (and on resize). Re-pinning here catches the final layout
        even though the maximum was stale immediately after setHtml. We only re-pin the
        bottom case — the "restore position" target is fixed and applied once below, so
        it must NOT be re-forced on every later relayout (that would fight the reader)."""
        if self._pin_to_bottom:
            self._pin_bottom()

    def _apply_scroll_intent(self) -> None:
        if self._pin_to_bottom:
            self._pin_bottom()
        elif self._restore_value is not None:
            # Reader scrolled up through history — hold their position across the full
            # re-render (setHtml resets to the top) instead of snapping them up.
            scrollbar = self._log.verticalScrollBar()
            scrollbar.setValue(min(self._restore_value, scrollbar.maximum()))

    def _pin_bottom(self) -> None:
        # Move the cursor to the very end and reveal it. ensureCursorVisible forces the
        # lazy document layout to resolve the true bottom and scrolls there — this
        # overrides setHtml's own reset-to-top, which is why simply setting the scrollbar
        # to a (stale) maximum after setHtml left new text below the fold.
        cursor = self._log.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._log.setTextCursor(cursor)
        self._log.ensureCursorVisible()
        scrollbar = self._log.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

def _format_lines(lines: list[dict[str, Any]]) -> str:
    items = [_format_line(line) for line in lines[-80:]]
    if not items:
        items = ['<p style="color:#b8bfad; margin-top:24px;">'
                 'The channel is open.<br>Say hello to R3X.</p>']
    return f"""
    <html><head><style>
        body {{
            margin: 0; background: transparent; color: #f0eadb;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            font-size: 17px;
        }}
        .meta {{ font-size: 11px; color: #b7bfae; margin: 0 0 9px 0; }}
        .text {{ font-size: 17px; color: #f0eadb; line-height: 138%; margin: 0; }}
        .gap {{ font-size: 6px; margin: 0; }}
    </style></head><body>{"".join(items)}</body></html>
    """


def _format_line(line: dict[str, Any]) -> str:
    ts = line.get("ts")
    try:
        stamp = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S")
    except Exception:
        stamp = "--:--:--"

    speaker = str(line.get("speaker") or "System").strip()
    text = _escape(str(line.get("text") or "").strip())
    kind = str(line.get("kind") or "").strip().lower()
    if kind not in {"user", "rex", "system"}:
        kind = "rex" if speaker.lower() in {"rex", "r3x"} else "system"
    label = speaker
    note = str(line.get("note") or "").strip()
    accent = {"user": "#a1d7cf", "rex": "#efb06d", "system": "#b7c69c"}[kind]
    background = "#303930" if kind == "rex" else "#202d28"
    # QTextDocument supports tables and paragraph spacing reliably; browser-only
    # flexbox, div padding, and CSS selectors do not render consistently here.
    return f"""
    <table width="100%" cellspacing="0" cellpadding="11" bgcolor="{background}">
      <tr><td width="3" bgcolor="{accent}" style="padding:0;"></td><td>
        <p class="meta"><span style="color:{accent}; font-weight:700;">{_escape(label)}</span>
        &nbsp; / &nbsp; {stamp}</p>
        <p class="text">{text}</p>{f'<p class="meta" style="margin:6px 0 0 0;">{_escape(note)}</p>' if note else ""}
      </td></tr>
    </table><p class="gap">&nbsp;</p>
    """


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("\n", "<br>")
    )
