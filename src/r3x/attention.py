"""Session-only attention intent, receipts and an explicitly simulated output adapter.

Directions are robot-relative intentions, not bearings or evidence of an object.
Physical execution uses the separately injected RobotServos adapter.
"""
from collections import deque
from dataclasses import asdict, dataclass
from .contracts import Cancellation, ProviderFailure


@dataclass(frozen=True)
class AttentionIntent:
    request_id: str
    version: int
    target: str
    purpose: str
    reason: str
    proposed_at: float
    expires_at: float
    anchor: dict | None = None
    hold_seconds: float = .5


@dataclass(frozen=True)
class AttentionReceipt:
    request_id: str
    outcome: str
    completed_at: float
    medium: str = 'simulation'


class AttentionState:
    def __init__(self, clock):
        self.clock = clock
        self.medium = "simulation"
        from .attention_scene import AttentionScene
        self.scene = AttentionScene(clock)
        self.version = 0
        self.pending: AttentionIntent | None = None
        self.active: AttentionIntent | None = None
        self.recent = deque(maxlen=8)

    def clear(self):
        self.version += 1
        self.pending = self.active = None

    def context(self):
        if self.active and self.clock() >= self.active.expires_at:
            self.clear()
        if self.active and self.active.anchor and not self.scene.awaiting:
            current = self.scene.resolve(self.active.target)
            if current is None or current['calibration_id'] != self.active.anchor['calibration_id']:
                self.clear()
        return {'vision':self.scene.context(), 'version': self.version, 'mode': self.medium,
                'active': asdict(self.active) if self.active else None,
                'recent': list(self.recent),
                'limits': ('Controller output pulses only; mechanical arrival and sight unverified. ' if self.medium=='controller' else 'No physical movement. ') + 'Directions are robot-relative; no new visual evidence. Finite dwell releases motor ownership; expiry never commands recentering.'}

    def propose(self, request_id, args, anchor=None):
        self.context()
        if any(r['request_id'] == request_id for r in self.recent) or self.pending:
            raise ValueError('AttentionRequestAlreadyUsedOrPending')
        now = self.clock()
        self.pending = AttentionIntent(request_id, self.version, args['target'], args['purpose'],
                                       args['reason'], now, now + (15 if self.medium=='controller' else 3 if args['hold']=='brief' else 8), anchor,
                                       .5 if args['hold']=='brief' else 1.5)
        return self.pending

    def complete(self, intent, receipt):
        if (self.pending != intent or intent.version != self.version or
                self.clock() >= intent.expires_at):
            raise ValueError('StaleAttentionIntent')
        if (receipt.request_id != intent.request_id or receipt.medium != self.medium or
                receipt.outcome != ('pulses_reached' if self.medium=='controller' else 'simulated') or
                not intent.proposed_at <= receipt.completed_at < intent.expires_at):
            raise ValueError('InvalidAttentionReceipt')
        if intent.anchor and not self.scene.valid(intent.anchor):
            raise ValueError('AttentionTargetLost')
        self.active, self.pending = intent, None
        self.version += 1
        self.recent.append({**asdict(receipt), 'target': intent.target, 'purpose': intent.purpose})
        self.scene.invalidate(receipt.completed_at)


class SimulatedAttention:
    """Explicitly started, synchronous adapter owned by OutputOwner; no workers/I/O."""
    def __init__(self, clock):
        self.clock = clock
        self.started = False

    def start(self):
        self.started = True

    def execute(self, intent: AttentionIntent, cancel: Cancellation) -> AttentionReceipt:
        cancel.check()
        if not self.started:
            raise ProviderFailure('AttentionSimulatorNotStarted')
        if self.clock() >= intent.expires_at:
            raise ProviderFailure('AttentionIntentExpired')
        return AttentionReceipt(intent.request_id, 'simulated', self.clock())

    def close(self):
        self.started = False
