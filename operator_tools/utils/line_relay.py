"""Hand complete lines to another process's pipe without ever waiting on it.

The battery menu's serial worker passes every line it reads from the drive controller to the ToF
diagnostics window (owner, November 20). That worker also releases the port when Rex starts and
feeds the joystick's deadman, so a window that stops reading must cost it nothing: the pipe is
non-blocking, a line that does not fit is finished later or dropped whole, and a closed pipe just
ends the relay.
"""
from __future__ import annotations

import os


class LineRelay:
    def __init__(self, fd: int) -> None:
        os.set_blocking(fd, False)
        self.fd = fd
        self.pending = b''      # The unsent rest of one line the pipe took only part of.
        self.dropped = 0
        self.closed = False

    def send(self, line: bytes) -> bool:
        """True when the line was taken (sent, or begun and held to finish); False when it was
        dropped because the reader is behind or gone. Never blocks."""
        if self.closed:
            return False
        if self.pending:
            self._flush()
            if self.pending or self.closed:
                self.dropped += 1
                return False
        self.pending = line if line.endswith(b'\n') else line + b'\n'
        self._flush()
        return not self.closed

    def _flush(self) -> None:
        try:
            sent = os.write(self.fd, self.pending)
        except BlockingIOError:
            return
        except OSError:          # The reader closed its end.
            self.closed, self.pending = True, b''
            return
        self.pending = self.pending[sent:]
