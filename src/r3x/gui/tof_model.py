"""What the drive controller's range sensors are doing, for the ToF diagnostics window (Qt-free).

Owner, November 20: "a GUI diagnostics tool that shows all the ToF data live". That evening three
of the four corner sensors had been dark for a day and nothing on screen said so: the session log
keeps no raw readings, and finding it took a hand-written capture.

Fed the controller's own lines (telemetry, both 8x8 depth sensors, the raw stream of the two front
corner sensors, its log) exactly as they arrive on the serial port, plus `feed` lines from whoever
holds that port saying whether data is flowing. It keeps a minute of each ring sensor so a reading
that drops while a cable is wiggled stays on screen, and words what stands out. It decides nothing
for the robot and sends nothing.
"""
from __future__ import annotations

from collections import deque
import statistics

from .controller_feed import STALE_S, ControllerFeed   # noqa: F401 - STALE_S is part of this module's surface.

HISTORY_S = 60.          # How much of each ring sensor is kept and drawn.
STALL_S = .5             # A depth sensor silent this long has stalled (the firmware's own bound).
# How often the firmware gets round to each ring sensor, as the raw stream shows it. The stream itself
# costs time on the serial line: 113 ms measured on 0.2.21 with none holding the others up (about
# 80 ms without the stream), 227 ms on 0.2.20 with two silent sensors waited on every round.
POLL_NORMAL_MS = 110
POLL_SLOW_MS = 170       # A round this slow means a sensor is holding the others up.
RAW_RECENT = 20          # Raw samples of a front corner sensor that decide how it is doing.
MATRIX_CLEAR_MM = 3500

# The ring from above, counter-clockwise from the front: key, name, degrees left of straight ahead,
# multiplexer channel, kind, the value that means "nothing in range".
RING = (
    ('fl', 'Front-left', 22.5, 4, 'long', 4000),
    ('lf', 'Left-front', 67.5, 0, 'short', 2000),
    ('lb', 'Left-back', 112.5, 1, 'short', 2000),
    ('rl', 'Rear-left', 157.5, 6, 'long', 4000),
    ('rr', 'Rear-right', -157.5, 7, 'long', 4000),
    ('rb', 'Right-back', -112.5, 3, 'short', 2000),
    ('rf', 'Right-front', -67.5, 2, 'short', 2000),
    ('fr', 'Front-right', -22.5, 5, 'long', 4000),
)
CORNERS = ('fl', 'fr', 'rl', 'rr')
# VL53L1X range status as the firmware's library reports it (Pololu VL53L1X.h), in plain words.
RANGE_STATUS = {
    0: 'valid', 1: 'too noisy', 2: 'signal too weak', 3: 'valid, at its nearest', 4: 'nothing in range',
    5: 'hardware fault', 6: 'unconfirmed', 7: 'doubtful (wrap check)', 9: 'only its own lens glare',
    10: 'not ready', 13: 'too near to range', 255: 'no result', -1: 'no answer',
}


class Sensor:
    def __init__(self, key, name, bearing, channel, kind, clear_mm):
        self.key, self.name, self.bearing, self.channel = key, name, bearing, channel
        self.kind, self.clear_mm = kind, clear_mm
        self.value = None            # Its own latest reading in mm; -1 the controller has none; None never seen.
        self.history = deque()       # (host time, mm)
        self.dropouts = 0            # Times it went from a reading to none while watched.
        self.silent_since = None
        self.read_at = None          # When it last gave a reading.

    def note(self, mm, now):
        if self.value is not None and self.value >= 0 > mm:
            self.dropouts += 1
        if mm < 0:
            if self.silent_since is None:
                self.silent_since = now
        else:
            self.silent_since, self.read_at = None, now
        self.value = mm
        self.history.append((now, mm))
        while self.history and now - self.history[0][0] > HISTORY_S:
            self.history.popleft()

    def span(self):
        """(nearest, farthest) reading in the kept minute, or None when it gave none."""
        values = [mm for _, mm in self.history if mm >= 0]
        return (min(values), max(values)) if values else None


class Matrix:
    def __init__(self, name):
        self.name = name
        self.grid, self.rej = None, []
        self.frames = deque()        # Host arrival times, the kept minute.
        self.stalls = 0
        self.last_stall = None       # (when it ended, how long it lasted)

    def note(self, grid, rej, now):
        if self.frames and now - self.frames[-1] > STALL_S:
            self.stalls += 1
            self.last_stall = (now, now - self.frames[-1])
        self.grid, self.rej = grid, rej
        self.frames.append(now)
        while self.frames and now - self.frames[0] > HISTORY_S:
            self.frames.popleft()

    def rate(self, now, over=5.):
        return sum(1 for at in self.frames if now - at <= over) / over

    def age(self, now):
        return now - self.frames[-1] if self.frames else None

    def nearest_mm(self):
        """Nearest reading above the floor, None without a frame, MATRIX_CLEAR_MM when all is open."""
        if not self.grid:
            return None
        near = [v for i, v in enumerate(self.grid)
                if 25 < v < min(MATRIX_CLEAR_MM, self.rej[i // 8] if i // 8 < len(self.rej) else 4095)]
        return min(near) if near else MATRIX_CLEAR_MM


class Raw:
    """The firmware's diagnostic stream for one front corner sensor: what the sensor itself said
    (raw_mm, status), what the firmware made of it (input_mm) and what it published (filtered_mm)."""
    def __init__(self):
        self.last = None
        self.recent = deque(maxlen=RAW_RECENT)    # (firmware ms, input_mm, filtered_mm, status)
        self.at = None

    def note(self, message, now):
        self.last, self.at = message, now
        self.recent.append((message.get('t'), message.get('input_mm'), message.get('filtered_mm'), message.get('status')))

    def poll_ms(self):
        times = [t for t, *_ in self.recent if isinstance(t, (int, float))]
        gaps = [b - a for a, b in zip(times, times[1:]) if 0 < b - a < 5000]
        return statistics.median(gaps) if len(gaps) >= 3 else None

    def discarded(self):
        """It answers with usable readings and the firmware publishes none of them."""
        rows = [(i, f) for _, i, f, _ in self.recent if isinstance(i, (int, float)) and isinstance(f, (int, float))]
        return len(rows) >= 5 and all(f < 0 for _, f in rows) and sum(1 for i, _ in rows if i >= 0) >= .8 * len(rows)

    def doubtful_share(self):
        codes = [s for *_, s in self.recent if isinstance(s, int)]
        return sum(1 for s in codes if s not in (0, 3, 4)) / len(codes) if len(codes) >= 5 else None


class TofDiagnostics(ControllerFeed):
    def __init__(self):
        super().__init__()
        self.sensors = {row[0]: Sensor(*row) for row in RING}
        self.front, self.rear = Matrix('Front 8×8'), Matrix('Rear 8×8')
        self.raw = {'fl': Raw(), 'fr': Raw()}
        self.fused = {}              # fl/fr/rl/rr as the firmware's reflex uses them: corner sensor and 8x8 together.

    # ------------------------------------------------------------------ input
    def _message(self, kind, line, now):
        if kind == 'telemetry':
            self._telemetry(line, now)
        elif kind in ('tofmx', 'tofmx_rear'):
            grid = line.get('g')
            if isinstance(grid, str) and len(grid) == 192:
                try:
                    cells = [int(grid[i:i + 3], 16) for i in range(0, 192, 3)]
                    rej = [int(v) for v in (line.get('rej') or [])][:8]
                except (TypeError, ValueError):
                    self.unreadable += 1
                    return
                (self.front if kind == 'tofmx' else self.rear).note(cells, rej, now)
        elif kind == 'tofraw' and line.get('sensor') in self.raw:
            self.raw[line['sensor']].note(line, now)

    def _telemetry(self, message, now):
        tof = message.get('tof_mm')
        if not isinstance(tof, dict):
            return
        self.telemetry, self.telemetry_at = message, now
        for key, sensor in self.sensors.items():
            # A corner sensor's own reading is the *_radial value; fl/fr/rl/rr have the 8x8 folded in.
            value = tof.get(key + '_radial', tof.get(key)) if key in CORNERS else tof.get(key)
            if isinstance(value, (int, float)):
                sensor.note(int(value), now)
        self.fused = {key: tof.get(key) for key in CORNERS if isinstance(tof.get(key), (int, float))}

    # ------------------------------------------------------------------ reading it
    def status(self, key, now):
        """One of waiting, reading, open (nothing in range), discarded, silent."""
        sensor = self.sensors[key]
        if sensor.value is None:
            return 'waiting'
        if sensor.value >= 0:
            return 'open' if sensor.value >= sensor.clear_mm else 'reading'
        raw = self.raw.get(key)
        if raw is not None and raw.at is not None and now - raw.at <= 3. and raw.discarded():
            return 'discarded'
        return 'silent'

    def poll_ms(self):
        """How often the firmware gets round to each ring sensor, from the raw stream; None unknown."""
        values = [v for v in (raw.poll_ms() for raw in self.raw.values()) if v is not None]
        return statistics.median(values) if values else None

    def notices(self, now):
        """What stands out, in words, worst first: [(level, text)], level 'bad', 'warn' or 'ok'."""
        if not self.live(now):
            return []
        rows = []
        silent = [s for k, s in self.sensors.items() if self.status(k, now) == 'silent']
        if silent:
            names = ', '.join(f'{s.name} (channel {s.channel})' for s in silent)
            rows.append(('bad', f'Not answering: {names}.'))
        for key, sensor in self.sensors.items():
            if self.status(key, now) == 'discarded':
                rows.append(('warn', f'{sensor.name} (channel {sensor.channel}) is answering with usable readings, but '
                                     'the controller is discarding them. Its cabling is not the problem.'))
        poll = self.poll_ms()
        if poll is not None and poll >= POLL_SLOW_MS:
            rows.append(('warn', f'Each ring sensor is being read every {poll:.0f} ms (about {POLL_NORMAL_MS} ms when none '
                                 'holds up the rest): a sensor that does not answer is being waited on.'))
        for key, raw in self.raw.items():
            share = raw.doubtful_share()
            if share is not None and share >= .3 and self.status(key, now) in ('reading', 'open'):
                rows.append(('warn', f'{self.sensors[key].name}: {share:.0%} of its readings are doubtful (a weak '
                                     'reflection), so its distance swings between a value and nothing.'))
        for matrix in (self.front, self.rear):
            age = matrix.age(now)
            if age is None:
                rows.append(('bad', f'{matrix.name}: no frames.'))
            elif age > STALL_S:
                rows.append(('bad', f'{matrix.name}: nothing for {age:.1f} s.'))
            elif matrix.last_stall and now - matrix.last_stall[0] <= HISTORY_S:
                rows.append(('warn', f'{matrix.name}: stalled {matrix.last_stall[1]:.1f} s, '
                                     f'{now - matrix.last_stall[0]:.0f} s ago ({matrix.stalls} while watching).'))
        dropped = [s for s in self.sensors.values() if s.dropouts and s.value is not None and s.value >= 0]
        if dropped:
            rows.append(('warn', 'Dropped out and came back while watching: ' +
                         ', '.join(f'{s.name} ×{s.dropouts}' for s in dropped) + '.'))
        if not rows:
            rows.append(('ok', 'All eight ring sensors and both 8×8 sensors are reporting.'))
        return rows
