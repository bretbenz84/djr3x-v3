"""Compact-protocol codec over an explicitly supplied, exclusively owned port.

Opening is explicit; composition must confirm operator-console handoff first.
"""
from __future__ import annotations
from typing import Protocol
import threading
import time
from ..contracts import ProviderFailure
from ..servo_poses import THROTTLE, validate_pose

LATE_REPLY_SECONDS = .05   # A failed query's answer can still be on its way back over USB.


def open_serial(path: str) -> Port:
    """Call only after confirmed handoff; no discovery, probing or reconnect."""
    import serial
    return serial.Serial(path, 9600, timeout=.1, write_timeout=.1, exclusive=True)


class Port(Protocol):
    def write(self, packet: bytes) -> int: ...
    def read(self, count: int) -> bytes: ...
    def close(self) -> None: ...


class Maestro:
    def __init__(self, port: Port):
        self.port = port
        self.closed = False
        # The throttle arm's return from its drive park runs beside other motion, so each
        # wire exchange (a query and its reply, or one command batch) is taken whole.
        self._wire = threading.RLock()
        self.observers = []   # Callables(packet) after each complete write (the dashboard's ramp mirror).

    def _write(self, packet: bytes) -> None:
        if self.closed:
            raise ProviderFailure('MaestroClosed')
        if self.port.write(packet) != len(packet):
            raise ProviderFailure('MaestroShortWrite')
        for observer in self.observers:
            try:
                observer(packet)
            except Exception:
                pass   # A display observer never affects the servos.

    def _value(self, command: int, channel: int, value: int) -> None:
        self._write(bytes((command, channel, value & 127, value >> 7)))

    def positions(self, channels) -> dict[int, int]:
        result = {}
        with self._wire:
            try:
                for ch in channels:
                    self._write(bytes((0x90, ch)))
                    data = self.port.read(2)
                    if len(data) != 2:
                        raise ProviderFailure('MaestroShortRead')
                    result[ch] = int.from_bytes(data, 'little')
            except Exception:
                self._drop_late_reply()
                raise
        return result

    def _drop_late_reply(self) -> None:
        """A query that failed part-way may still be answered. Its bytes are dropped before the
        wire is let go, or the next query would read them as its own reply."""
        time.sleep(LATE_REPLY_SECONDS)
        try:
            reset = getattr(self.port, 'reset_input_buffer', None)
            if reset:
                reset()
        except Exception:
            pass   # The original failure is what the owner judges.

    def profiles(self, profiles: dict[int, tuple[int, int]]) -> None:
        for ch, (speed, acceleration) in profiles.items():
            # 0 is the Maestro's own "no limit" (owner, September 28: no caps on motion
            # commands). Choreography chooses it explicitly, as V2's wave did.
            if not 0 <= speed <= 16383 or not 0 <= acceleration <= 255:
                raise ValueError('InvalidServoProfile')
        with self._wire:
            for ch, (speed, acceleration) in profiles.items():
                self._value(0x89, ch, acceleration)
                self._value(0x87, ch, speed)

    def targets(self, pose: dict[int, int]) -> None:
        validate_pose(pose)
        with self._wire:
            for ch in sorted(set(pose) - set(THROTTLE)):
                self._value(0x84, ch, pose[ch])
            if set(THROTTLE).issubset(pose):
                packet = bytearray((0x9F, 3, 8))
                for ch in THROTTLE:
                    packet.extend((pose[ch] & 127, pose[ch] >> 7))
                self._write(bytes(packet))

    def close(self) -> None:
        if not self.closed:
            self.port.close()
            self.closed = True
