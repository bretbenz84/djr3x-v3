"""`r3x live --gui`: Qt owns the main thread (macOS requires it); Rex runs unchanged on
a worker thread and the window observes him through the feed (V2 main._run_gui_mode).

The window opens at once and shows the boot. Shut Down, closing the window, Ctrl-C and
SIGTERM all take Rex's normal shutdown; during model loading the request waits until
the conversation is up, then exits straight away. A failed boot keeps the window open so
the Logs tab stays readable.
"""
from __future__ import annotations

import signal
import threading


def run(app) -> bool:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from ..terminal import LiveConsole
    from .dashboard import DashboardWindow
    from .feed import DashboardFeed

    feed = DashboardFeed(console=LiveConsole())
    qt = QApplication.instance() or QApplication(['DJ-R3X'])
    outcome = {}

    def live():
        try:
            outcome['success'] = app.live(feed)
        except BaseException as exc:   # Re-raised on the main thread after the window closes.
            outcome['error'] = exc

    worker = threading.Thread(target=live, name='r3x-live')
    window = DashboardWindow(feed, worker)
    previous = {sig: signal.signal(sig, lambda *_: window.request_shutdown())
                for sig in (signal.SIGINT, signal.SIGTERM)}
    wake = QTimer()
    wake.timeout.connect(lambda: None)   # Lets Python run signal handlers during Qt's loop.
    wake.start(200)
    qt.setQuitOnLastWindowClosed(True)   # The window refuses to close until Rex has stopped.
    window.showMaximized()
    worker.start()
    try:
        qt.exec()
    finally:
        wake.stop()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    worker.join()
    if 'error' in outcome:
        raise outcome['error']
    return bool(outcome.get('success'))
