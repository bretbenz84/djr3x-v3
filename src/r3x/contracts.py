"""Observations and delivery receipts, with no inferred person identity."""
from dataclasses import dataclass, field
from threading import Event
from typing import Callable, Iterator, Protocol


class Cancelled(Exception):
    pass


class ProviderFailure(Exception):
    """Safe, machine-readable reason, never raw HTTP exception text."""


@dataclass
class Cancellation:
    event: Event = field(default_factory=Event)

    def check(self) -> None:
        if self.event.is_set():
            raise Cancelled()


@dataclass(frozen=True)
class Capture:
    capture_id: str
    session_id: str
    pcm: bytes  # little-endian signed 16-bit mono
    started_at: float
    speech_ended_at: float
    ended_at: float
    sample_rate: int = 16000
    echo_risk: bool = False
    truncated: bool = False
    voiced_intervals: tuple[tuple[float, float], ...] = ()  # Seconds relative to PCM start.
    overlap: bool | None = None  # Unknown is not a measured absence of overlap.
    device: str = "unspecified"
    parent_capture_id: str | None = None
    source_offset: float = 0.  # Derived PCM offset in the independently retained original.


@dataclass(frozen=True)
class Transcript:
    text: str
    avg_logprob: float | None
    trusted: bool


@dataclass(frozen=True)
class Snapshot:
    session_id: str
    turn_id: str
    revision: int
    messages: tuple[dict[str, str], ...]
    speaker: str = "unresolved"
    context: dict = field(default_factory=dict)
    tools_enabled: bool = False
    continuation: tuple[dict, ...] = ()
    images: tuple = field(default=(), repr=False)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict
    preamble: tuple[dict, ...] = ()  # Original, unspoken provider commentary with phase.


@dataclass(frozen=True)
class ToolResult:
    request_id: str
    outcome: str
    reason: str
    revision: int
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Playback:
    frames: int
    started_at: float | None
    completed_at: float
    underruns: int


class ASR(Protocol):
    def transcribe(self, capture: Capture, cancel: Cancellation) -> Transcript: ...


class Brain(Protocol):
    def reply(self, snapshot: Snapshot, cancel: Cancellation) -> Iterator[str | ToolCall]: ...


class TTS(Protocol):
    def synthesize(self, text: str, cancel: Cancellation) -> Iterator[bytes]: ...


class Sink(Protocol):
    def play(self, chunks: Iterator[bytes], cancel: Cancellation,
             on_start: Callable[[float], None]) -> Playback: ...
    def cancel(self) -> None: ...
