"""What the drive controller's diagnostics windows share: the feed, the look, and how they start.

Each window is started by the battery menu, which keeps the controller's port and passes every line it
reads down the window's stdin (operator_tools/utils/line_relay.py). A window opens no port and sends
nothing. `--replay FILE.jsonl` plays a saved capture instead, and `--snapshot PNG` saves a picture.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

from . import theme

STYLE = theme.DIALOG_STYLE + f'''
    QLabel#tofText {{ color: {theme.TEXT}; font-size: 12px; }}
    QLabel#tofDim {{ color: {theme.TEXT_DIM}; font-size: 11px; }}
    QPlainTextEdit#tofLog {{ background: #0c0f0e; color: {theme.TEXT_DIM}; border: 1px solid {theme.BORDER_DIM};
                            font-family: Menlo, monospace; font-size: 11px; }}'''
LEVEL_COLOR = {'bad': theme.BAD, 'warn': theme.WARN, 'ok': theme.GOOD}


def panel(title: str, widget: QWidget) -> QFrame:
    frame = QFrame(); frame.setObjectName('chromePanel')
    lay = QVBoxLayout(frame); lay.setContentsMargins(12, 9, 12, 10); lay.setSpacing(7)
    head = QLabel(title); head.setObjectName('panelTitle')
    lay.addWidget(head); lay.addWidget(widget, 1)
    return frame


def text(dim: bool = False) -> QLabel:
    label = QLabel('')
    label.setObjectName('tofDim' if dim else 'tofText')
    label.setWordWrap(True)
    label.setTextFormat(Qt.TextFormat.RichText)
    return label


def notice_rows(rows) -> str:
    return '<br>'.join(f'<span style="color:{LEVEL_COLOR[level]}">●</span>&nbsp; {words}' for level, words in rows) \
        or f'<span style="color:{theme.TEXT_DIM}">Nothing to report until readings arrive.</span>'


def bring_forward(window) -> None:
    window.show()
    window.raise_()
    window.activateWindow()
    try:   # Started by a menu bar app, the window otherwise opens behind whatever has focus.
        from AppKit import NSApplication
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    except Exception:
        pass


def read_fd(fd: int, lines: 'queue.Queue') -> None:
    """Lines from the feeding process. Read from the descriptor itself: a thread waiting inside
    Python's buffered stdin aborts the interpreter when the window closes (seen in the first live try)."""
    rest = b''
    while True:
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            chunk = b''
        if not chunk:
            break
        at = time.monotonic()
        *rows, rest = (rest + chunk).split(b'\n')
        for row in rows:
            if row:
                lines.put((at, row))
    lines.put((time.monotonic(), None))


def replay(path: str, speed: float, lines: 'queue.Queue') -> None:
    first, started = None, time.monotonic()
    with open(path, 'rb') as handle:
        for line in handle:
            try:
                at = json.loads(line).get('at')
            except (ValueError, AttributeError):
                continue
            if isinstance(at, (int, float)):
                first = at if first is None else first
                wait = (at - first) / speed - (time.monotonic() - started)
                if wait > 0:
                    time.sleep(wait)
            lines.put((time.monotonic(), line))
    lines.put((time.monotonic(), b'{"type":"feed","mode":"ended"}'))


def run(make_window, title: str, description: str, argv=None) -> int:
    """Start one diagnostics window: fed on stdin, or from a saved capture with --replay."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--replay', metavar='FILE', help='play a saved capture (JSON lines with an "at" time) instead of stdin')
    parser.add_argument('--speed', type=float, default=1., help='replay speed (default 1)')
    parser.add_argument('--snapshot', metavar='PNG', help='save a picture of the window after --after seconds and exit')
    parser.add_argument('--after', type=float, default=5.)
    args = parser.parse_args(argv)

    lines: 'queue.Queue' = queue.Queue()
    if args.replay:
        source = threading.Thread(target=replay, args=(args.replay, max(.01, args.speed), lines), daemon=True)
    else:
        source = threading.Thread(target=read_fd, args=(sys.stdin.fileno(), lines), daemon=True)
    app = QApplication.instance() or QApplication([title])
    app.setApplicationName(title)
    window = make_window(lines, quit_on_end=not args.replay)
    source.start()
    bring_forward(window)
    if args.snapshot:
        def grab():
            window.grab().save(args.snapshot)
            app.quit()
        QTimer.singleShot(int(args.after * 1000), grab)
    return app.exec()
