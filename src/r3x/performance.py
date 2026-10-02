"""One speech phrase plus one optional, receipt-backed expressive primitive."""
from dataclasses import dataclass
from typing import Callable
from .contracts import Cancellation, ProviderFailure


@dataclass(frozen=True)
class Gesture:
    request_id: str
    primitive: str
    target: str
    intensity: str
    expires_at: float
    anchor: dict | None = None


@dataclass(frozen=True)
class GestureReceipt:
    request_id: str
    status: str
    at: float
    medium: str = 'simulation'
    reason: str | None = None


@dataclass(frozen=True)
class Performance:
    text: str
    gesture: Gesture
    timing: str
    valid: Callable[[], bool]
    delivered: Callable[[], None]


class SimulatedGesture:
    """No workers, hardware, trajectories or sleep. Cancellation uses the turn token."""
    def __init__(self, clock):
        self.clock, self.started = clock, False

    def start(self):
        self.started = True

    def close(self):
        self.started = False

    def execute(self, gesture, cancel: Cancellation):
        cancel.check()
        if not self.started:
            raise ProviderFailure('GestureSimulatorNotStarted')
        if self.clock() >= gesture.expires_at:
            raise ProviderFailure('GestureExpired')
        return GestureReceipt(gesture.request_id, 'simulated', self.clock())
