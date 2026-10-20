"""NDJSON serial links to V2's existing ESP32 drive-base and radar firmware.

Wire contract: V2 docs/motion_protocol.md (v1). No firmware change is required.
Transport only: every motion decision belongs to r3x.drive. No reconnect, no
replay, no port probing; a closed or failed link stays unavailable this run.
"""
from collections import deque
import functools
import json
import operator
import threading
import time

from ..contracts import ProviderFailure

PROTOCOL = 1
MAX_LINE = 512          # Commands we send (protocol v1 limit).
MAX_INBOUND = 4096     # Firmware replies exceed 512: the config ack echoes ~27 values (TX buffer 1024).


def open_serial(path: str):
    import serial
    # Default open on purpose: V2 measured that pre-dropping DTR/RTS resets the
    # drive ESP32 on every connect (V2 hardware/motion.py, 2026-07-13).
    return serial.Serial(path, 115200, timeout=.1, write_timeout=.2, exclusive=True)


class NDJSONLink:
    """Background reader, sequenced writes and a bounded hello handshake."""
    def __init__(self, open_port, *, clock=time.monotonic, name='esp32'):
        self.open_port, self.clock, self.name = open_port, clock, name
        self.port = None
        self.hello = None
        self.failure = None
        self.parse_errors = 0
        self._seq = 0
        self._write = threading.Lock()
        self._state = threading.Lock()
        self._stop = threading.Event()
        self._reader = None
        self._hello_seen = threading.Event()

    def open(self, host_fields: dict, timeout: float = 1.5, settle: float = .3, wait=None) -> dict:
        wait = wait or self._stop.wait
        self.port = self.open_port()
        self._reader = threading.Thread(target=self._read_loop, name='r3x-'+self.name+'-reader', daemon=True)
        self._reader.start()
        wait(settle)  # V2: the ESP32 can still be booting when the port opens.
        self.send({'cmd':'hello','host':'djr3x','proto':PROTOCOL,**host_fields})
        deadline = self.clock() + timeout
        while not self._hello_seen.is_set():
            if self.failure or self.clock() >= deadline:
                raise ProviderFailure(self.failure or (self.name.title()+'HandshakeTimeout'))
            wait(.02)
        if self.hello.get('proto') != PROTOCOL:
            raise ProviderFailure(self.name.title()+'ProtocolMismatch')
        return dict(self.hello)

    def send(self, obj: dict) -> int:
        with self._write:
            if self.port is None or self.failure:
                raise ProviderFailure(self.failure or (self.name.title()+'LinkClosed'))
            self._seq = (self._seq % 0xFFFFFFFF) + 1
            line = (json.dumps({'v':PROTOCOL,**obj,'seq':self._seq},separators=(',',':'))+'\n').encode()
            if len(line) > MAX_LINE:
                raise ProviderFailure('CommandLineTooLong')
            try:
                written = self.port.write(line)
            except Exception as exc:
                self.failure = self.name.title()+'WriteFailed:'+type(exc).__name__
                raise ProviderFailure(self.failure)
            if written is not None and written != len(line):
                self.failure = self.name.title()+'ShortWrite'
                raise ProviderFailure(self.failure)
            return self._seq

    def dispatch(self, message: dict, received_at: float) -> None:
        raise NotImplementedError

    def _read_loop(self):
        buffer = b''
        while not self._stop.is_set():
            try:
                chunk = self.port.read(256)
            except Exception as exc:
                if not self._stop.is_set():
                    self.failure = self.name.title()+'ReadFailed:'+type(exc).__name__
                return
            if not chunk:
                continue
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                line = line.strip()
                if not line:
                    continue
                if len(line) > MAX_INBOUND:
                    self.parse_errors += 1
                    continue
                try:
                    message = json.loads(line.decode('utf-8','replace'))
                except ValueError:
                    self.parse_errors += 1
                    continue
                if not isinstance(message,dict) or message.get('v') != PROTOCOL or 'type' not in message:
                    self.parse_errors += 1
                    continue
                received = self.clock()
                if message['type'] == 'hello':
                    with self._state:
                        self.hello = message
                    self._hello_seen.set()
                try:
                    self.dispatch(message, received)
                except Exception:
                    self.parse_errors += 1
            if len(buffer) > MAX_INBOUND:
                buffer = b''
                self.parse_errors += 1

    def close(self):
        self._stop.set()
        if self._reader and self._reader is not threading.current_thread():
            self._reader.join(1.)
            if self._reader.is_alive():
                raise ProviderFailure(self.name.title()+'ReaderDidNotStop')
        with self._write:
            if self.port is not None:
                self.port.close()
                self.port = None


class DriveLink(NDJSONLink):
    """Latest telemetry/matrix snapshots plus acks and dones correlated by seq."""
    def __init__(self, open_port, *, clock=time.monotonic):
        super().__init__(open_port, clock=clock, name='drive')
        self.telemetry = None
        self.matrix = None
        # Optional backward-facing 8x8 matrix (firmware 0.2.9). Never seen means not
        # fitted, and reversing keeps the radial rear pair alone.
        self.rear_matrix = None
        self.acks = {}
        self.dones = {}
        self.events = deque(maxlen=32)
        self.boot_id = None

    def dispatch(self, message, received_at):
        kind = message['type']
        with self._state:
            if kind == 'telemetry':
                self.telemetry = {**message, 'received_at':received_at}
            elif kind in ('tofmx', 'tofmx_rear'):
                grid = message.get('g')
                if isinstance(grid,str) and len(grid) == 192:
                    frame = {'grid':[int(grid[i:i+3],16) for i in range(0,192,3)],
                             'rej':[int(v) for v in (message.get('rej') or [])][:8],
                             'received_at':received_at}
                    if kind == 'tofmx':
                        self.matrix = frame
                    else:
                        self.rear_matrix = frame
            elif kind == 'ack' and isinstance(message.get('seq'),int):
                self.acks[message['seq']] = message
                while len(self.acks) > 64: self.acks.pop(next(iter(self.acks)))
            elif kind == 'done' and isinstance(message.get('seq'),int):
                self.dones[message['seq']] = {**message, 'received_at':received_at}
                while len(self.dones) > 64: self.dones.pop(next(iter(self.dones)))
            elif kind == 'event':
                self.events.append({**message, 'received_at':received_at})
                if message.get('event') == 'boot':
                    # A reboot invalidates odometry and any in-flight command.
                    if self.boot_id is not None and message.get('boot_id') != self.boot_id:
                        self.failure = 'DriveControllerRebooted'
            elif kind == 'hello':
                self.boot_id = message.get('boot_id')

    def snapshot(self):
        with self._state:
            return (dict(self.telemetry) if self.telemetry else None,
                    dict(self.matrix) if self.matrix else None)

    def rear_snapshot(self):
        """Latest rear matrix frame (its grid columns start at the robot's rear-right), or None."""
        with self._state:
            return dict(self.rear_matrix) if self.rear_matrix else None

    def ack(self, seq):
        with self._state:
            return self.acks.get(seq)

    def done(self, seq):
        with self._state:
            return self.dones.get(seq)


class RadarLink(NDJSONLink):
    """LD2450 ring frames. Radar is a bearing prior, never a person detector."""
    def __init__(self, open_port, *, clock=time.monotonic):
        super().__init__(open_port, clock=clock, name='radar')
        self.frames = deque(maxlen=60)
        self.telemetry = None   # Latest raw frame (sensor health) for the dashboard scope.

    def dispatch(self, message, received_at):
        if message['type'] != 'telemetry':
            return
        with self._state:
            self.telemetry = {**message, 'rx_monotonic': received_at}
        targets = []
        for row in ((message.get('radar') or {}).get('targets') or [])[:12]:
            try:
                target = {'bearing_deg':float(row['b']),'range_m':float(row['r']),
                          'confidence':float(row['c']),'speed_mps':float(row.get('s',0.)),
                          'sensors':int(row.get('m',0))}
            except (KeyError,TypeError,ValueError):
                continue
            if all(v == v and abs(v) < 1e6 for v in target.values()):
                targets.append(target)
        with self._state:
            self.frames.append((received_at, seam_merge(targets)))

    def recent(self, since: float):
        with self._state:
            return [(at, [dict(t) for t in rows]) for at, rows in self.frames if at >= since]


def wrap180(degrees: float) -> float:
    return (degrees + 180.) % 360. - 180.


def seam_merge(targets, bearing_gate=25., range_gate=.8, max_confidence=.6):
    """V2 host-side cross-seam dedup: one body seen at two modules' FOV edges."""
    import math
    def mergeable(a, b):
        if not a['sensors'] or not b['sensors'] or a['sensors'] & b['sensors']:
            return False
        if a['confidence'] > max_confidence or b['confidence'] > max_confidence:
            return False
        return (abs(wrap180(a['bearing_deg']-b['bearing_deg'])) <= bearing_gate and
                abs(a['range_m']-b['range_m']) <= range_gate)
    remaining = sorted(targets, key=lambda t: t['confidence'], reverse=True)
    merged = []
    while remaining:
        seed = remaining.pop(0)
        cluster = [seed] + [t for t in remaining if mergeable(seed, t)]
        remaining = [t for t in remaining if t not in cluster]
        if len(cluster) == 1:
            merged.append(seed)
            continue
        weights = [max(t['confidence'], .01) for t in cluster]
        total = sum(weights)
        miss = 1.
        for t in cluster:
            miss *= 1. - min(t['confidence'], 1.)
        merged.append({
            'bearing_deg': wrap180(math.degrees(math.atan2(
                sum(w*math.sin(math.radians(t['bearing_deg'])) for w,t in zip(weights,cluster)),
                sum(w*math.cos(math.radians(t['bearing_deg'])) for w,t in zip(weights,cluster))))),
            'range_m': sum(w*t['range_m'] for w,t in zip(weights,cluster))/total,
            'confidence': 1.-miss,
            'speed_mps': sum(w*t['speed_mps'] for w,t in zip(weights,cluster))/total,
            'sensors': functools.reduce(operator.or_, (t['sensors'] for t in cluster), 0)})
    return sorted(merged, key=lambda t: t['confidence'], reverse=True)
