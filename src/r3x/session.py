"""One sequential conversational owner shared by live input and scenarios."""
from queue import Queue
import threading
import time
from uuid import uuid4

from .contracts import ASR, Brain, Cancellation, Cancelled, Capture, ProviderFailure, Snapshot, Transcript, ToolCall
from .speakers import VoiceObservation
from .history import TurnHistory
from .output import Delivery, OutputOwner, Phrases, put_phrase


class Session:
    def __init__(self, session_id: str, brain: Brain | None, output: OutputOwner, trace, state=None):
        self.session_id, self.brain, self.output, self.trace = session_id, brain, output, trace
        self.revision = 0
        self.history = TurnHistory()
        self._lock = threading.Lock()
        self._processing = threading.Lock()
        self._cancel: Cancellation | None = None
        self.closed = False
        self.state = state
        self._latest_speech = float("-inf")
        self._capture_end = float("inf")

    def cancel(self) -> None:
        with self._lock:
            self.revision += 1
            if self._cancel:
                self._cancel.event.set()
            if self.state:
                self.state.cancel()
        self.output.cancel()
        self.trace.emit("cancel", revision=self.revision)

    def note_speech(self, ended_at: float) -> None:
        """Yield an unheard reply to resumed, non-echo microphone speech.

        This is not an explicit stop: keep revision, pending questions, and the
        original user history so the continuation can be understood normally.
        """
        with self._lock:
            self._latest_speech = max(self._latest_speech, ended_at)
            if self._cancel and ended_at > self._capture_end and not self._cancel.event.is_set():
                self._cancel.event.set()
                self.trace.emit("reply_yielded", reason="user_resumed_speaking")

    def close(self) -> None:
        self.closed = True
        self.cancel()

    def history_messages(self):
        return self.history.messages()

    def process_turn(self, capture: Capture, transcript: Transcript, submitted_revision: int, observation=None, faces=()) -> Delivery:
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
        question = None
        state_started = False
        try:
            with self._lock:
                if self.closed or capture.session_id != self.session_id or submitted_revision != self.revision:
                    self.trace.emit("turn_rejected", capture_id=capture.capture_id, reason="stale")
                    delivery.status = "rejected"
                    return delivery
                self._cancel = cancel
                self._capture_end = float("inf")
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
            self.history.begin_turn()
            context = {}
            if self.state:
                with self._lock:
                    cancel.check()
                    observation = observation or VoiceObservation(self.state.tracker.namespace)
                    attribution = self.state.begin(turn_id,capture,transcript,observation,faces)
                    state_started = True
                    context = self.state.context(faces)
                self.trace.emit("speaker_evidence",turn_id=turn_id,**context["speaker"],
                                namespace=observation.namespace,window_consistency=observation.consistency)
            if self.brain is None:
                delivery.status = "observed"  # Frozen acoustic replay through the same state path.
                delivery.medium = "none"
                return delivery
            if self.state:
                import json
                self.history.append({"role":"developer","content":"Evidence for the immediately following utterance: "+json.dumps({
                    "speaker":context["speaker"],"source_turn_id":turn_id,
                    "speech_ended_at":capture.speech_ended_at,"transcript_trusted":transcript.trusted},ensure_ascii=False)})
            self.history.append({"role": "user", "content": transcript.text})
            # A newer capture can arrive while the previous ASR result waits in
            # the queue. Retain its source/history, but do not answer it belatedly.
            with self._lock:
                self._capture_end = capture.ended_at
                if self._latest_speech > capture.ended_at:
                    cancel.event.set()
                cancel.check()
            snapshot = Snapshot(self.session_id, turn_id, self.revision, self.history_messages(),
                                context.get("speaker",{}).get("person_id") or "unresolved",
                                context, self.state is not None)
            phrases = Queue(maxsize=4)
            worker = threading.Thread(target=self.output.run, args=(phrases, cancel, turn_id, delivery),
                                      name="r3x-output", daemon=True)
            worker.start()
            splitter = Phrases()
            generated = ""
            model_at = self.trace.clock()
            self.trace.emit("model_request", turn_id=turn_id, primary_calls=1,
                            context_messages=len(snapshot.messages),
                            history_turns=len(self.history.turns),
                            history_characters=self.history.size(snapshot.messages),
                            reasoning_effort=getattr(self.brain,"reasoning_effort",None))
            def generate(request):
                nonlocal first_token_at, generated
                calls = []
                stream = self.brain.reply(request, cancel)
                try:
                    for delta in stream:
                        cancel.check()
                        if isinstance(delta, ToolCall):
                            calls.append(delta)
                            continue
                        if first_token_at is None:
                            first_token_at = self.trace.clock()
                            self.trace.emit("model_first_text", turn_id=turn_id)
                        generated += delta
                        if len(generated) > 4000:
                            raise ProviderFailure("ReplyLengthLimit")
                        for phrase in splitter.add(delta):
                            put_phrase(phrases, phrase, cancel)
                    cancel.check()
                    return calls
                finally:
                    close = getattr(stream, "close", None)
                    if close:
                        close()
            calls = generate(snapshot)
            if calls:
                if not self.state or len(calls) != 1 or generated.strip():
                    raise ProviderFailure("SpeechToolProtocolViolation")
                call = calls[0]
                self.trace.emit("tool_proposed",turn_id=turn_id,call_id=call.call_id,name=call.name,arguments=call.arguments)
                with self._lock:
                    cancel.check()
                    result = self.state.execute(call,context["state_revision"],cancel)
                self.trace.emit("tool_executed",turn_id=turn_id,request_id=result.request_id,outcome=result.outcome)
                if result.outcome=="ok" and call.name=="remain_silent":
                    from dataclasses import asdict
                    import json
                    delivery.status, delivery.medium = "suppressed", "none"
                    self.history.extend((
                        {"type":"function_call","call_id":call.call_id,"name":call.name,"arguments":json.dumps(call.arguments)},
                        {"type":"function_call_output","call_id":call.call_id,"output":json.dumps(asdict(result))}))
                    self.trace.emit("reply_suppressed",turn_id=turn_id,reason=result.data["reason"])
                elif result.outcome=="ok" and call.name in {"ask_question","ask_face_question"}:
                    question = result.data
                    put_phrase(phrases,question["question"],cancel)
                else:
                    from dataclasses import asdict
                    import json
                    context = self.state.context(faces)
                    continuation = (*call.preamble,
                        {"type":"function_call","call_id":call.call_id,"name":call.name,
                         "arguments":json.dumps(call.arguments)},
                        {"type":"function_call_output","call_id":call.call_id,
                         "output":json.dumps(asdict(result))})
                    # Forgotten information is not sent back in the continuation history.
                    if self.state.forgotten:
                        self.history.clear()
                        self.history.append({"role":"developer","content":"A person was forgotten. Do not reuse previous personal context."})
                    followup = Snapshot(self.session_id,turn_id,self.revision,self.history_messages(),
                                        snapshot.speaker,context,False,continuation)
                    self.trace.emit("model_request",turn_id=turn_id,primary_calls=0,purpose="tool_result_continuation",
                                    reasoning_effort=getattr(self.brain,"reasoning_effort",None))
                    if generate(followup):
                        raise ProviderFailure("ToolContinuationLimit")
            self.trace.emit("model_completed", turn_id=turn_id, original_text=generated)
            tail = splitter.finish()
            if tail:
                put_phrase(phrases, tail, cancel)
            put_phrase(phrases, None, cancel)
            worker.join(timeout=60)
            if worker.is_alive():
                raise ProviderFailure("OutputWorkerTimeout")
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
                    self.history.append({"role": "assistant", "phase":"final_answer", "content": heard})
                if delivery.status not in {"completed","suppressed"}:
                    self.history.append({"role": "developer", "content":
                        "The last reply was interrupted or failed. Only completed phrases above are known delivered; any partial phrase is unknown."})
            if delivery.status == "interrupted" and delivery.first_audio_at is None and self._latest_speech > capture.ended_at:
                self.history.append({"role":"developer","content":
                    "Rex yielded without speaking because microphone speech resumed after source turn " + turn_id +
                    ". The preceding user words remain part of the conversation; the next fragment may continue them. "
                    "This timing event does not establish that the voices are the same person."})
            if self.state and state_started and not cancel.event.is_set() and not self.state.forgotten and delivery.status!="suppressed":
                try:
                    with self._lock:
                        cancel.check()
                        if question and delivery.status=="completed" and delivery.medium=="audio":
                            self.state.question_delivered(question["question"],question["addressee_id"],self.trace.clock(),question.get("face_track_id"))
                except Exception as exc:
                    self.trace.emit("state_finalize_failed",turn_id=turn_id,error_type=type(exc).__name__)
            with self._lock:
                self._cancel = None
            audio_at = delivery.first_audio_at
            self.trace.emit("turn_done", turn_id=turn_id, status=delivery.status, medium=delivery.medium, timings={
                "endpoint": capture.ended_at - capture.speech_ended_at,
                "context": model_at - began if model_at is not None else None,
                "model_first_text": first_token_at - model_at if first_token_at is not None else None,
                "end_of_speech_to_first_audio": audio_at - capture.speech_ended_at if audio_at is not None else None,
                "total": self.trace.clock() - began,
            })
            self._processing.release()
        return delivery
