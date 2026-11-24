"""Which Maestro channel each servo id is plugged into (owner, November 23).

Rex's program names every servo by a fixed id (its channel before the November 23 rewire): poses,
limits, holds and logs all use ids. servo_wiring.toml at the repository root says which board
channel carries each id, and only the code that talks to the board (adapters.maestro, the servo
menu) translates. Reading it is explicit (load), never an import side effect. Standard library only,
so the servo menu can use it without the rest of the program.
"""
from __future__ import annotations

import tomllib
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

PATH = Path(__file__).resolve().parents[2] / 'servo_wiring.toml'
CHANNELS = 18   # A Mini Maestro 18.


def load(path: Path = PATH) -> Mapping[int, int]:
    """id -> board channel, every id and channel 0-17 exactly once; ValueError otherwise."""
    servos = tomllib.loads(Path(path).read_text()).get('servos')
    if not isinstance(servos, dict) or not servos:
        raise ValueError('ServoWiringMissing')
    wiring = {}
    for name, entry in servos.items():
        if (not isinstance(entry, dict) or set(entry) != {'id', 'channel'}
                or any(type(entry[key]) is not int or not 0 <= entry[key] < CHANNELS for key in entry)):
            raise ValueError(f'ServoWiringInvalid:{name}')
        if entry['id'] in wiring:
            raise ValueError(f'ServoWiringDuplicateId:{name}')
        wiring[entry['id']] = entry['channel']
    if set(wiring) != set(range(CHANNELS)) or set(wiring.values()) != set(range(CHANNELS)):
        raise ValueError('ServoWiringIncomplete')
    # The throttle's shoulder, elbow and wrist (ids 8, 9, 10) start together in one board command,
    # which takes consecutive channels.
    first = wiring[8]
    if [wiring[8], wiring[9], wiring[10]] != [first, first + 1, first + 2]:
        raise ValueError('ServoWiringThrottleNotConsecutive')
    return MappingProxyType(wiring)

