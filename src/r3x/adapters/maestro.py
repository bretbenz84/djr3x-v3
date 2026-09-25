"""Compact-protocol codec over an explicitly supplied, exclusively owned port.

Opening is explicit; composition must confirm operator-console handoff first.
"""
from __future__ import annotations
from typing import Protocol
from ..contracts import ProviderFailure
from ..servo_poses import THROTTLE, validate_pose


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

    def _write(self, packet: bytes) -> None:
        if self.closed:
            raise ProviderFailure('MaestroClosed')
        if self.port.write(packet) != len(packet):
            raise ProviderFailure('MaestroShortWrite')

    def _value(self, command: int, channel: int, value: int) -> None:
        self._write(bytes((command, channel, value & 127, value >> 7)))

    def positions(self, channels) -> dict[int, int]:
        result = {}
        for ch in channels:
            self._write(bytes((0x90, ch)))
            data = self.port.read(2)
            if len(data) != 2:
                raise ProviderFailure('MaestroShortRead')
            result[ch] = int.from_bytes(data, 'little')
        return result

    def profiles(self, profiles: dict[int, tuple[int, int]]) -> None:
        for ch, (speed, acceleration) in profiles.items():
            if not 1 <= speed <= 16383 or not 1 <= acceleration <= 255:
                raise ValueError('UnboundedServoProfile')
            self._value(0x89, ch, acceleration)
            self._value(0x87, ch, speed)

    def targets(self, pose: dict[int, int]) -> None:
        validate_pose(pose)
        for ch in sorted(set(pose) - set(THROTTLE)):
            self._value(0x84, ch, pose[ch])
        if set(THROTTLE).issubset(pose):
            packet = bytearray((0x9F, 3, 8))
            for ch in THROTTLE:
                packet.extend((pose[ch] & 127, pose[ch] >> 7))
            self._write(bytes(packet))

    def go_home(self) -> None:
        """Explicit operator action only; board-stored homes are not calibration."""
        self._write(bytes((0xA2,)))

    def close(self) -> None:
        if not self.closed:
            self.port.close()
            self.closed = True
