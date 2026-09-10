"""The sole speech owner. Generated text is never a delivery receipt."""
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
import re
import threading
import time

from .contracts import Cancellation, Cancelled, ProviderFailure, Sink, TTS


TAGS = frozenset({"sarcastic", "curious", "excited", "mischievously", "laughs",
                  "sighs", "whispers", "snorts", "exhales"})


def speech_text(text: str) -> str:
    """Tag filtering is output syntax, never language interpretation."""
    count = 0
    def tag(match):
        nonlocal count
        if match.group(1).lower() in TAGS and count < 2:
            count += 1
            return match.group(0)
        return ""
    return re.sub(r"\[([^\]]*)\]", tag, text).strip()


def plain_text(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", text).strip()


class Phrases:
    """Bounded speech chunks while the one model generation is still streaming."""
    def __init__(self):
        self.pending = ""

    def add(self, delta: str) -> list[str]:
        self.pending += delta
        chunks = []
        while self.pending:
            cut = next((m.end() for m in re.finditer(r"[.!?](?:\s|$)", self.pending)
                        if m.end() >= 25), 0)
            if not cut and len(self.pending) >= 320:
                cut = self.pending.rfind(" ", 0, 320)
                if cut < 1:
                    cut = 320
            if not cut:
                break
            chunks.append(self.pending[:cut].strip())
            self.pending = self.pending[cut:]
        return chunks

    def finish(self) -> str:
        result, self.pending = self.pending.strip(), ""
        return result


@dataclass
class Delivery:
    status: str = "requested"
    completed_text: list[str] = field(default_factory=list)
    first_audio_at: float | None = None
    error: str | None = None
    underruns: int = 0
    medium: str = "audio"


class OutputOwner:
    def __init__(self, tts: TTS, sink: Sink, trace):
        self.tts, self.sink, self.trace = tts, sink, trace
        self.active = threading.Event()
        self.echo_until = 0.

    def echo_risk(self) -> bool:
        return self.active.is_set() or self.trace.clock() < self.echo_until

    def cancel(self) -> None:
        self.sink.cancel()

    def run(self, phrases: Queue, cancel: Cancellation, turn_id: str, delivery: Delivery) -> None:
        try:
            while True:
                cancel.check()
                try:
                    text = phrases.get(timeout=.05)
                except Empty:
                    continue
                if text is None:
                    break
                text = speech_text(text)
                if not text:
                    continue
                self.trace.emit("speech_generated", turn_id=turn_id, text=text)
                synthesis_at = self.trace.clock()
                first_pcm_at = None
                def chunks():
                    nonlocal first_pcm_at
                    stream = self.tts.synthesize(text,cancel)
                    try:
                        for chunk in stream:
                            cancel.check()  # Speech may have resumed during the TTS request.
                            if first_pcm_at is None:
                                first_pcm_at = self.trace.clock()
                                # Network generation is silent. Gate capture only
                                # once PCM can enter the playback queue.
                                self.active.set()
                                self.trace.emit("tts_first_pcm",turn_id=turn_id,tts_first_pcm_seconds=first_pcm_at-synthesis_at)
                            yield chunk
                    finally:
                        close = getattr(stream,"close",None)
                        if close:
                            close()
                def started(at):
                    delivery.first_audio_at = delivery.first_audio_at or at
                    delivery.status = "started"
                    self.trace.emit("playback_started", turn_id=turn_id, at_device=at,
                                    playback_queue_seconds=at-first_pcm_at if first_pcm_at is not None else None)
                try:
                    receipt = self.sink.play(chunks(), cancel, started)
                finally:
                    if first_pcm_at is not None:
                        self.echo_until = self.trace.clock() + .3
                    self.active.clear()
                cancel.check()
                delivery.completed_text.append(plain_text(text))
                delivery.underruns += receipt.underruns
                self.trace.emit("phrase_completed", turn_id=turn_id, text=plain_text(text),
                                frames=receipt.frames, underruns=receipt.underruns,
                                completed_at=receipt.completed_at)
            cancel.check()
            if delivery.status != "suppressed":
                delivery.status = "completed" if delivery.completed_text else "failed"
            if not delivery.completed_text and delivery.status!="suppressed":
                delivery.error = "EmptySpeech"
        except Cancelled:
            delivery.status = "interrupted"
        except Exception as exc:
            delivery.status = "failed"
            delivery.error = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
            cancel.event.set()  # Stop further model text on output failure.
        finally:
            self.active.clear()
            self.trace.emit("delivery", turn_id=turn_id, status=delivery.status,
                            medium=delivery.medium,
                            completed_text=" ".join(delivery.completed_text), error=delivery.error,
                            partial_phrase_text=None, underruns=delivery.underruns)


def put_phrase(queue: Queue, value: str | None, cancel: Cancellation) -> None:
    while True:
        cancel.check()
        try:
            queue.put(value, timeout=.05)
            return
        except Full:
            continue


class TextOutput:
    """Explicit silent replay output. Never creates an audio delivery receipt."""
    def __init__(self,trace):
        self.trace = trace

    def cancel(self):
        pass

    def run(self,phrases,cancel,turn_id,delivery):
        if delivery.status != "suppressed":
            delivery.medium = "text"
        try:
            while True:
                cancel.check()
                try:
                    value = phrases.get(timeout=.05)
                except Empty:
                    continue
                if value is None:
                    break
                delivery.completed_text.append(plain_text(value))
                self.trace.emit("text_reply",turn_id=turn_id,text=plain_text(value),audio_played=False)
            if delivery.status != "suppressed":
                delivery.status = "completed" if delivery.completed_text else "failed"
        except Cancelled:
            delivery.status = "interrupted"
