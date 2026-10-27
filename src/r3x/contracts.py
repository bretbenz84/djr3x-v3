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
    guard: Callable[[], bool] | None = field(default=None, repr=False)
    # Once true (speech audible), freshness guards no longer cancel; only an
    # explicit event (stop command, shutdown, output failure) does.
    committed: Callable[[], bool] | None = field(default=None, repr=False)
    # The device callback latches submission of the first nonempty PCM buffer.
    # Its heavier on_start notification can arrive later on the producer thread.
    playback_started: Event = field(default_factory=Event, repr=False)

    def is_committed(self) -> bool:
        return self.playback_started.is_set() or bool(self.committed and self.committed())

    def check(self) -> None:
        if self.guard is not None and not self.is_committed() and not self.guard():
            self.event.set()
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
    robot_microphone_hold: bool | None = None  # None: non-robot capture; True: bounded hold covered full PCM.
    motion_risk: bool = False
    self_noise_risk: bool = False  # Only brief sounds, all while Rex's own servos or wheels moved.
    # What ended it: "silence" (the full end-of-speech pause), "finished" (Smart Turn heard a finished
    # turn at a short pause), "truncated" (20 s) or "playback_boundary" (Rex's playback began or ended).
    ended_by: str = "silence"
    # The board output it was heard on: 1 is the reSpeaker's ASR output, used while a Jeopardy clue
    # waits under the think music (October 26). Never learned from: voices are kept from channel 0.
    channel: int = 0


@dataclass(frozen=True)
class Transcript:
    text: str
    avg_logprob: float | None
    trusted: bool
    heard_as: str | None = None   # What the recognizer wrote, when the owner's spelling fixes changed it.
    not_a_voice: bool = False     # The voice check did not hear a human voice in the sound (docs/VOICE_CHECK.md).


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
    stream_tool_text: bool = False   # Speak reply-tool text as it streams (human-turn replies only).


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict
    preamble: tuple[dict, ...] = ()  # Original, unspoken provider commentary with phase.


@dataclass(frozen=True)
class ToolText:
    """Words streaming out of a reply tool's spoken text argument before the call is complete
    (October 14 plan, step 5). The finished ToolCall still follows with the same text."""
    call_id: str
    name: str
    text: str
    done: bool = False   # The text argument is complete: its last phrase can be spoken now.


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
    # mode: one line in a speaking mode's voice (voice_mode line); otherwise the voice of the modes on now.
    def synthesize(self, text: str, cancel: Cancellation, mode: str | None = None) -> Iterator[bytes]: ...


class Sink(Protocol):
    def play(self, chunks: Iterator[bytes], cancel: Cancellation,
             on_start: Callable[[float], None], visualize: bool = True) -> Playback: ...
    def cancel(self) -> None: ...
