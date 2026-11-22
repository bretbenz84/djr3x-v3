"""Lines from the drive controller, as whoever holds its port passes them on (Qt-free).

The battery menu keeps the drive controller's port whenever Rex is off and hands each line it reads to
the diagnostics windows, with `feed` lines of its own saying whether data is flowing. The windows'
models share this much: decoding a line, the controller's own messages, the firmware version, and the
one line saying where the data comes from or why there is none.
"""
from __future__ import annotations

from collections import deque
import json

STALE_S = 1.5            # No telemetry for this long: the link is not live.


class ControllerFeed:
    def __init__(self):
        self.telemetry = None
        self.telemetry_at = None
        self.firmware = None         # Version, once a hello is seen.
        self.log = deque(maxlen=200) # (host time, level, text)
        self.log_total = 0           # Messages ever seen, so a reader can tell which are new.
        self.feed = {}               # The port holder's last word: mode (live, dormant, connecting, no_port) and detail.
        self.raise_wanted = False
        self.lines = self.unreadable = 0

    def feed_line(self, line, now):
        """One line as it came off the serial port (bytes, str or a decoded dict)."""
        if not isinstance(line, dict):
            try:
                line = json.loads(line.decode('utf-8', 'replace') if isinstance(line, (bytes, bytearray)) else line)
            except ValueError:
                self.unreadable += 1
                return
        if not isinstance(line, dict):
            self.unreadable += 1
            return
        self.lines += 1
        kind = line.get('type')
        if kind == 'log' and isinstance(line.get('msg'), str):
            self.log.append((now, str(line.get('lvl') or 'info'), line['msg'][:200]))
            self.log_total += 1
        elif kind == 'hello':
            self.firmware = line.get('fw')
        elif kind == 'feed':
            if line.get('raise'):
                self.raise_wanted = True
            if 'mode' in line:
                self.feed = {'mode': line.get('mode'), 'detail': line.get('detail'), 'at': now}
        else:
            self._message(kind, line, now)

    def _message(self, kind, line, now):
        """A controller line this model reads (telemetry and the like)."""

    def live(self, now):
        return self.telemetry_at is not None and now - self.telemetry_at <= STALE_S

    def link(self, now):
        """(live, one line saying where the data is coming from or why there is none)."""
        if self.live(now):
            fw = f' · firmware {self.firmware}' if self.firmware else ''
            return True, f'Live from the drive controller{fw}'
        mode = self.feed.get('mode')
        if mode == 'dormant':
            return False, 'Rex is running and has the drive controller. Readings come back when he shuts down.'
        if mode == 'no_port':
            return False, 'No drive controller port is set (drive_port in local/settings.toml).'
        if mode == 'ended':
            return False, 'The recording has ended.'
        if self.telemetry_at is None:
            return False, 'Waiting for the drive controller…'
        return False, f'No data from the drive controller for {now - self.telemetry_at:.0f} s.'
