"""One sequential conversational owner shared by live input and scenarios."""
from collections import deque
from queue import Queue
import threading
import time
from uuid import uuid4

from .contracts import ASR, Brain, Cancellation, Cancelled, Capture, ProviderFailure, Snapshot, Transcript
from .output import Delivery, OutputOwner, Phrases, put_phrase


class Session:
    def __init__(self, session_id: str, brain: Brain, output: OutputOwner, trace):
        self.session_id, self.brain, self.output, self.trace = session_id, brain, output, trace
        self.revision = 0
        self.history = deque(maxlen=16)
        self._lock = threading.Lock()
        self._processing = threading.Lock()
        self._cancel: Cancellation | None = None
        self.closed = False

    def cancel(self) -> None:
        with self._lock:
            self.revision += 1
            if self._cancel:
                self._cancel.event.set()
        self.output.cancel()
        self.trace.emit("cancel", revision=self.revision)

    def close(self) -> None:
        self.closed = True
        self.cancel()

    def process_turn(self, capture: Capture, transcript: Transcript, submitted_revision: int) -> Delivery:
        if not self._processing.acquire(blocking=False):
            raise RuntimeError("Only one conversation owner may process turns")
        worker = None
        delivery = Delivery()
        turn_id = uuid4().hex
        began = self.trace.clock()
        cancel = Cancellation()
        model_at = None
        first_token_at = None
        model_error = None
        try:
            with self._lock:
                if self.closed or capture.session_id != self.session_id or submitted_revision != self.revision:
                    self.trace.emit("turn_rejected", capture_id=capture.capture_id, reason="stale")
                    delivery.status = "rejected"
                    return delivery
                self._cancel = cancel
            self.trace.emit("turn", turn_id=turn_id, capture_id=capture.capture_id,
                            original_text=transcript.text, avg_logprob=transcript.avg_logprob,
                            trusted=transcript.trusted, speaker="unresolved", revision=self.revision)
            if capture.echo_risk or not transcript.text.strip():
                delivery.status = "rejected"
                self.trace.emit("turn_rejected", turn_id=turn_id,
                                reason="echo_risk" if capture.echo_risk else "empty_transcript")
                return delivery
            # Syntax-only emergency control. No names/facts are parsed in Python.
            if transcript.trusted and transcript.text.strip().lower().rstrip(".!?") in {"stop", "cancel", "stop rex", "rex stop"}:
                self.cancel()
                delivery.status = "interrupted"
                return delivery
            self.history.append({"role": "user", "content": transcript.text})
            snapshot = Snapshot(self.session_id, turn_id, self.revision, tuple(self.history))
            phrases = Queue(maxsize=4)
            worker = threading.Thread(target=self.output.run, args=(phrases, cancel, turn_id, delivery),
                                      name="r3x-output", daemon=True)
            worker.start()
            splitter = Phrases()
            generated = ""
            model_at = self.trace.clock()
            self.trace.emit("model_request", turn_id=turn_id, primary_calls=1,
                            context_messages=len(snapshot.messages))
            stream = self.brain.reply(snapshot, cancel)
            try:
                for delta in stream:
                    cancel.check()
                    if first_token_at is None:
                        first_token_at = self.trace.clock()
                        self.trace.emit("model_first_text", turn_id=turn_id)
                    generated += delta
                    if len(generated) > 4000:
                        raise ProviderFailure("ReplyLengthLimit")
                    for phrase in splitter.add(delta):
                        put_phrase(phrases, phrase, cancel)
                cancel.check()
                self.trace.emit("model_completed", turn_id=turn_id, original_text=generated)
                tail = splitter.finish()
                if tail:
                    put_phrase(phrases, tail, cancel)
                put_phrase(phrases, None, cancel)
                # Provider network timeouts and audio watchdogs bound this work.
                worker.join(timeout=60)
                if worker.is_alive():
                    raise ProviderFailure("OutputWorkerTimeout")
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
        except Cancelled:
            if delivery.status != "failed":
                delivery.status = "interrupted"
        except Exception as exc:
            delivery.status = "failed"
            delivery.error = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
            model_error = delivery.error
            self.trace.emit("turn_failed", turn_id=turn_id, reason=delivery.error)
            cancel.event.set()
        finally:
            if worker and worker.is_alive():
                cancel.event.set()
                self.output.cancel()
                worker.join(timeout=2)
                if worker.is_alive():
                    self.closed = True  # A stuck owner must never overlap another turn.
                    self.trace.emit("worker_stuck", worker="output")
            if model_error:
                delivery.status, delivery.error = "failed", model_error
            # Only completed phrases enter history; no generated unheard tail.
            if worker and not worker.is_alive():
                heard = " ".join(delivery.completed_text)
                if heard:
                    self.history.append({"role": "assistant", "content": heard})
                if delivery.status != "completed":
                    self.history.append({"role": "developer", "content":
                        "The last reply was interrupted or failed. Only completed phrases above are known delivered; any partial phrase is unknown."})
            with self._lock:
                self._cancel = None
            audio_at = delivery.first_audio_at
            self.trace.emit("turn_done", turn_id=turn_id, status=delivery.status, timings={
                "endpoint": capture.ended_at - capture.speech_ended_at,
                "context": model_at - began if model_at is not None else None,
                "model_first_text": first_token_at - model_at if first_token_at is not None else None,
                "end_of_speech_to_first_audio": audio_at - capture.speech_ended_at if audio_at is not None else None,
                "total": self.trace.clock() - began,
            })
            self._processing.release()
        return delivery
