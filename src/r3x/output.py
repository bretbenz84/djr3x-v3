"""The sole speech owner. Generated text is never a delivery receipt."""
from dataclasses import dataclass, field
from queue import Empty, Full, Queue
import re
import threading
import time

from .contracts import Cancellation, Cancelled, ProviderFailure, Sink, TTS
from .performance import Performance


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
    gestures: list[dict] = field(default_factory=list)


class OutputOwner:
    def __init__(self, tts: TTS, sink: Sink, trace, *, attention=None, gestures=None, inspection=None):
        self.tts, self.sink, self.trace = tts, sink, trace
        self.attention = attention
        self.gestures = gestures
        self.inspection = inspection
        self._motion_lock = threading.Lock()
        self.active = threading.Event()
        self.echo_until = 0.

    def execute_inspection(self, request, cancel, valid):
        if self.inspection is None:
            raise ProviderFailure('InspectionUnavailable')
        if not self._motion_lock.acquire(blocking=False):
            return {'request_id':request.request_id,'status':'failed','medium':'simulation',
                    'stage':'movement','motion_status':'not_completed','reason':'MotionResourceBusy'}, ()
        try:
            return self.inspection.execute(request,cancel,valid)
        finally:
            self._motion_lock.release()

    def execute_attention(self, intent, cancel):
        if self.attention is None:
            raise ProviderFailure('AttentionUnavailable')
        cancel.check()
        if not self._motion_lock.acquire(blocking=False):
            raise ProviderFailure('MotionResourceBusy')
        try:
            receipt = self.attention.execute(intent, cancel)
            cancel.check()
            return receipt
        finally:
            self._motion_lock.release()

    def _gesture(self, performance, cancel, turn_id, delivery):
        from dataclasses import asdict
        from .performance import GestureReceipt
        gesture = performance.gesture
        started = self.trace.clock()
        acquired = False
        try:
            cancel.check()
            if started >= gesture.expires_at or not performance.valid():
                raise ProviderFailure('GestureEvidenceStale')
            if self.gestures is None:
                raise ProviderFailure('GestureUnavailable')
            acquired = self._motion_lock.acquire(blocking=False)
            if not acquired:
                raise ProviderFailure('MotionResourceBusy')
            receipt = self.gestures.execute(gesture, cancel)
            cancel.check()
            if not performance.valid():
                raise ProviderFailure('GestureEvidenceStale')
            if (not isinstance(receipt, GestureReceipt) or receipt.request_id != gesture.request_id or
                receipt.medium != 'simulation' or receipt.status != 'simulated' or
                not started <= receipt.at <= self.trace.clock() or receipt.at >= gesture.expires_at):
                raise ProviderFailure('InvalidGestureReceipt')
            performance.delivered()
        except Cancelled:
            receipt = GestureReceipt(gesture.request_id, 'cancelled', self.trace.clock(), reason='TurnCancelled')
        except Exception as exc:
            receipt = GestureReceipt(gesture.request_id, 'failed', self.trace.clock(),
                reason=str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__)
        finally:
            if acquired:
                self._motion_lock.release()
        row = {**asdict(receipt), 'primitive':gesture.primitive, 'target':gesture.target,
               'intensity':gesture.intensity, 'timing':performance.timing}
        delivery.gestures.append(row)
        self.trace.emit('gesture_delivery', turn_id=turn_id, **row)
        cancel.check()

    def echo_risk(self) -> bool:
        return self.active.is_set() or self.trace.clock() < self.echo_until

    def cancel(self) -> None:
        self.sink.cancel()

    def run(self, phrases: Queue, cancel: Cancellation, turn_id: str, delivery: Delivery) -> None:
        performance = None
        try:
            while True:
                cancel.check()
                try:
                    text = phrases.get(timeout=.05)
                except Empty:
                    continue
                if text is None:
                    break
                performance = text if isinstance(text, Performance) else None
                if performance:
                    text = performance.text
                    if performance.timing == 'before_speech':
                        self._gesture(performance, cancel, turn_id, delivery)
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
                delivery.completed_text.append(plain_text(text))
                delivery.underruns += receipt.underruns
                self.trace.emit("phrase_completed", turn_id=turn_id, text=plain_text(text),
                                frames=receipt.frames, underruns=receipt.underruns,
                                completed_at=receipt.completed_at)
                cancel.check()
                if performance and performance.timing == 'after_speech':
                    self._gesture(performance, cancel, turn_id, delivery)
                performance = None
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
            if performance and not any(r['request_id']==performance.gesture.request_id for r in delivery.gestures):
                from dataclasses import asdict
                from .performance import GestureReceipt
                row = {**asdict(GestureReceipt(performance.gesture.request_id, 'cancelled', self.trace.clock(),
                        reason='SpeechNotCompleted')), 'primitive':performance.gesture.primitive,
                        'target':performance.gesture.target,'intensity':performance.gesture.intensity,
                        'timing':performance.timing}
                delivery.gestures.append(row)
                self.trace.emit('gesture_delivery',turn_id=turn_id,**row)
            self.active.clear()
            self.trace.emit("delivery", turn_id=turn_id, status=delivery.status,
                            medium=delivery.medium,
                            completed_text=" ".join(delivery.completed_text), error=delivery.error,
                            partial_phrase_text=None, underruns=delivery.underruns)


def put_phrase(queue: Queue, value: str | Performance | None, cancel: Cancellation) -> None:
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
