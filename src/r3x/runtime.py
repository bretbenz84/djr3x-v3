"""Live orchestration: bounded capture/ASR queues feeding the shared Session."""
from queue import Queue, Empty, Full
from dataclasses import replace
import sys
import threading
import time

from .capture import Endpoint
from .contracts import Cancellation, Cancelled, ProviderFailure


class VoiceRuntime:
    def __init__(self, mic, vad, asr, session, trace, silence=1.2, perception=None, recorder=None):
        self.mic, self.vad, self.asr, self.session, self.trace = mic, vad, asr, session, trace
        self.endpoint = Endpoint(session.session_id, silence)
        self.perception, self.recorder = perception, recorder
        self.captures = Queue(maxsize=2)
        self.turns = Queue(maxsize=3)
        self.stop = threading.Event()
        self.asr_cancel = Cancellation()
        self.workers = []
        self.cancel_before = float("-inf")
        self.control_lock = threading.Lock()

    def cancel(self):
        with self.control_lock:
            self.cancel_before = self.trace.clock()
            self.session.cancel()
            self.asr_cancel.event.set()
        self.trace.emit("input_invalidated", captured_before=self.cancel_before)

    def enqueue(self, queue, item, kind):
        try:
            queue.put_nowait(item)
        except Full:
            self.trace.emit("input_overflow", queue=kind, policy="reject_newest")

    def _asr_loop(self):
        while not self.stop.is_set():
            try:
                capture, revision = self.captures.get(timeout=.05)
            except Empty:
                continue
            if revision != self.session.revision or capture.started_at <= self.cancel_before:
                self.trace.emit("capture_rejected", capture_id=capture.capture_id, reason="cancelled")
                continue
            if capture.echo_risk:
                self.trace.emit("capture_rejected", capture_id=capture.capture_id, reason="playback_echo_risk")
                continue
            with self.control_lock:
                if revision != self.session.revision or self.stop.is_set():
                    continue
                self.asr_cancel = Cancellation()
            began = self.trace.clock()
            try:
                perceived_turns = self.perception.analyze_many(capture,self.asr_cancel) if self.perception else (None,)
                for perceived in perceived_turns:
                    part = perceived.capture if perceived else capture
                    transcript = perceived.transcript if perceived else self.asr.transcribe(part, self.asr_cancel)
                    if not perceived:
                        self.trace.emit("transcript", capture_id=part.capture_id, original_text=transcript.text,
                            avg_logprob=transcript.avg_logprob, trusted=transcript.trusted,
                            asr_seconds=self.trace.clock() - began, queue_seconds=began - part.ended_at)
                    if revision != self.session.revision or self.stop.is_set():
                        self.trace.emit("transcript_rejected", capture_id=part.capture_id, reason="stale")
                        break
                    if transcript.trusted and transcript.text.strip().lower().rstrip(".!?") in {"stop", "cancel", "stop rex", "rex stop"}:
                        self.cancel()  # Bypasses a busy conversational generation.
                        break
                    else:
                        self.enqueue(self.turns, (part, transcript, revision, perceived), "turns")
            except Cancelled:
                self.trace.emit("asr_cancelled", capture_id=capture.capture_id)
            except Exception as exc:
                self.trace.emit("provider_failed", capability="asr", error_type=type(exc).__name__)

    def _turn_loop(self):
        while not self.stop.is_set() and not self.session.closed:
            try:
                capture, transcript, revision, perceived = self.turns.get(timeout=.05)
            except Empty:
                continue
            self.session.process_turn(capture, transcript, revision,
                observation=perceived.voice if perceived else None, faces=perceived.faces if perceived else ())
        if self.session.closed:
            self.stop.set()

    def start_workers(self):
        def guarded(target):
            try:
                target()
            except BaseException as exc:
                self.stop.set()
                self.session.closed = True
                self.asr_cancel.event.set()
                try:
                    self.session.cancel()
                    self.trace.emit("worker_stuck",worker=threading.current_thread().name,reason=type(exc).__name__)
                except Exception:
                    print("Core worker stopped: "+type(exc).__name__,file=sys.stderr)
        for name, target in (("asr", self._asr_loop), ("conversation", self._turn_loop)):
            thread = threading.Thread(target=guarded,args=(target,), name="r3x-" + name, daemon=True)
            self.workers.append(thread)
            thread.start()

    def run(self):
        self.start_workers()
        self.trace.emit("listening", controls="Enter or 'stop' cancels; 'quit' or Ctrl-C exits")
        previous_dropped = (0, 0)
        previous_end = None
        next_resources = 0.
        # Nonblocking terminal input is polled alongside frames; no orphan stdin worker.
        import select
        try:
            while not self.stop.is_set():
                now = self.trace.clock()
                if now>=next_resources:
                    import resource
                    usage = resource.getrusage(resource.RUSAGE_SELF)
                    self.trace.emit("resources",peak_rss_bytes_macos=usage.ru_maxrss,
                                    user_cpu_seconds=usage.ru_utime,system_cpu_seconds=usage.ru_stime,
                                    captures_queued=self.captures.qsize(),turns_queued=self.turns.qsize(),
                                    threads=threading.active_count())
                    next_resources = now+30
                if sys.stdin.isatty() and select.select([sys.stdin], [], [], 0)[0]:
                    command = sys.stdin.readline().strip().lower()
                    if command in {"quit", "exit"}:
                        break
                    if command in {"", "stop", "cancel"}:
                        self.cancel()
                frame = self.mic.read()
                if frame is None:
                    continue
                counts = (self.mic.dropped, self.mic.status_errors)
                if counts != previous_dropped or (previous_end is not None and frame.started_at - previous_end > .05):
                    self.trace.emit("capture_gap", dropped=counts[0], status_errors=counts[1])
                    self.endpoint.reset()
                    self.vad.reset()
                    previous_dropped = counts
                previous_end = frame.ended_at
                if frame.started_at <= self.cancel_before:
                    self.endpoint.reset()
                    continue
                speech = self.vad.is_speech(frame.pcm)
                if speech and not frame.echo_risk:
                    self.session.note_speech(frame.ended_at)
                capture = self.endpoint.push(frame, speech)
                if capture:
                    capture = replace(capture,device=getattr(self.mic,"device_name","configured_microphone"))
                    if self.recorder:
                        self.recorder.save(capture)
                    self.trace.emit("capture", capture_id=capture.capture_id,
                        started_at=capture.started_at, ended_at=capture.ended_at,
                        speech_ended_at=capture.speech_ended_at, truncated=capture.truncated,
                        echo_risk=capture.echo_risk, pcm_bytes=len(capture.pcm))
                    self.enqueue(self.captures, (capture, self.session.revision), "captures")
        except KeyboardInterrupt:
            pass
        finally:
            self.close()

    def close(self):
        self.stop.set()
        self.cancel()
        self.mic.cancel()
        deadline = time.monotonic() + 18  # Network read bound plus cooperative cleanup.
        for worker in self.workers:
            worker.join(max(0, deadline - time.monotonic()))
        stuck = [w.name for w in self.workers if w.is_alive()]
        if stuck:
            self.trace.emit("worker_stuck", workers=stuck)
            raise ProviderFailure("RuntimeWorkersDidNotStop")
        self.session.closed = True
