"""Live orchestration: bounded capture/ASR queues feeding the shared Session."""
from queue import Queue, Empty, Full
from dataclasses import replace
import sys
import threading
import time

from .capture import Endpoint
from .contracts import Cancellation, Cancelled, ProviderFailure
from .session import is_stop_command

# Voiced time within one utterance before it yields an unheard reply. Live September 28:
# every phantom "Oh."/"Okay." noise capture had <= 0.48 s voiced; real speech >= 0.64 s.
YIELD_VOICED_SECONDS = .5

class VoiceRuntime:
    def __init__(self, mic, vad, asr, session, trace, silence=1.2, perception=None, recorder=None, *, lull_seconds=8.0, observations=None, scene_observer=None, robot=None, drive=None):
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
        self.asr_busy = threading.Event()
        from .proactive import Arrivals
        self.arrivals = Arrivals()
        self.camera = getattr(perception,'camera',None)
        self.started_at = self.trace.clock()
        self.audio_seen_at = float('-inf')
        self.lull_seconds = lull_seconds
        self.observations,self.scene_observer = observations,scene_observer
        self.robot = robot
        self.drive = drive
        self.faces_seen_at = float('-inf')
        self.last_unseen = float('-inf')

    def cancel(self):
        if self.robot:
            self.robot.idle.stop()
            self.robot.owner.release_microphone_hold()
        with self.control_lock:
            self.cancel_before = self.trace.clock()
            self.session.cancel()
            self.asr_cancel.event.set()
        self.trace.emit("input_invalidated", captured_before=self.cancel_before)

    def enqueue(self, queue, item, kind):
        if kind=='captures' and item[0].echo_risk:
            # Echo transitions must not masquerade as queued human work in the
            # proactive guard. Evaluation recording has already kept this PCM.
            self.trace.emit('capture_rejected',capture_id=item[0].capture_id,reason='playback_echo_risk')
            return
        if kind=='turns':
            # Do not discard an already transcribed utterance during a slow
            # network request. The ASR worker waits; capture/control remain live.
            reported=False
            while not self.stop.is_set() and not self.session.closed:
                try:
                    queue.put(item,timeout=.05)
                    return
                except Full:
                    if not reported:
                        self.trace.emit('transcript_waiting',reason='conversation_queue_full')
                        reported=True
            return
        try:
            queue.put_nowait(item)
        except Full:
            self.trace.emit("input_overflow", queue=kind, policy="reject_newest")

    def _asr_loop(self):
        while not self.stop.is_set():
            try:
                with self.control_lock:
                    capture, revision = self.captures.get_nowait()
                    self.asr_busy.set()
            except Empty:
                self.stop.wait(.05)
                continue
            if revision != self.session.revision or capture.started_at <= self.cancel_before:
                self.trace.emit("capture_rejected", capture_id=capture.capture_id, reason="cancelled")
                self.asr_busy.clear()
                continue
            if capture.echo_risk:
                self.trace.emit("capture_rejected", capture_id=capture.capture_id, reason="playback_echo_risk")
                self.asr_busy.clear()
                continue
            with self.control_lock:
                if revision != self.session.revision or self.stop.is_set():
                    self.asr_busy.clear()
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
                    command = transcript.text.strip().lower().rstrip('.!?')
                    if transcript.trusted and command in {'shut down','shutdown','rex shut down','rex, shut down','shut down rex'}:
                        self.trace.emit('shutdown_requested',source='voice',capture_id=part.capture_id)
                        self.stop.set()
                        self.cancel()
                        break
                    if transcript.trusted and is_stop_command(transcript.text):
                        self.cancel()  # Bypasses a busy conversational generation.
                        break
                    else:
                        self.enqueue(self.turns, (part, transcript, revision, perceived), "turns")
            except Cancelled:
                self.trace.emit("asr_cancelled", capture_id=capture.capture_id)
            except Exception as exc:
                self.trace.emit("provider_failed", capability="asr", error_type=type(exc).__name__)
            finally:
                self.asr_busy.clear()

    def _turn_loop(self):
        while not self.stop.is_set() and not self.session.closed:
            try:
                capture, transcript, revision, perceived = self.turns.get(timeout=.05)
            except Empty:
                self.proactive_tick()
                continue
            self.session.process_turn(capture, transcript, revision,
                observation=perceived.voice if perceived else None, faces=perceived.faces if perceived else (),
                latest_faces=self.camera.latest if self.camera else None)
        if self.session.closed:
            self.stop.set()

    def observe_visual(self, now):
        if self.camera:
            self.arrivals.observe(self.camera.latest(now),now)
            self.session.check_opportunity()

    def proactive_tick(self):
        """Called only by the idle conversation worker; audio always has priority."""
        if not self.camera or not self.session.state or self.session.state.frozen: return
        now = self.trace.clock()
        with self.control_lock:
            # Build and validate from the same latest cache, not the last UI poll.
            self.arrivals.observe(self.camera.latest(now),now)
            if self.arrivals.faces:
                self.faces_seen_at = now
            # Session maintenance only; does not create an opportunity or an LLM call.
            with self.session._lock:
                intentions = self.session.state.conversation_intent
                if intentions.record:
                    intentions.refresh({'faces': self.camera.latest(now),
                        'participation': self.session.state.initiative.context(now)},
                        intentions.record.memory_ids)
            if (self.stop.is_set() or self.session.closed or now-self.started_at<8 or
                not 0<=now-self.audio_seen_at<=.5 or self.asr_busy.is_set() or
                not self.captures.empty() or not self.turns.empty() or
                now-self.session._latest_speech<self.lull_seconds or now-self.session.last_interaction_at<self.lull_seconds or
                now<self.session.proactive_hold_until): return
            initiative = self.session.state.initiative
            if not initiative.allowed(now): return
            pending = self.session.state.pending
            if pending and now-pending['delivered_at']<=120: return
            event = self.arrivals.take(self.session.session_id,now,self.session._latest_speech)
            if event is None:
                current = self.session.state.current
                source = current[0] if current else None
                audience, audience_key = self.arrivals.audience_snapshot(now)
                from .conversation_intent import audience as intent_audience
                row = intentions.record
                active_intent = (row is not None and row.status == 'active'
                    and row.audience == intent_audience({'faces': audience}))
                if audience and self.session.history.turns and initiative.lull_due(now,source,active_intent):
                    from .proactive import Opportunity
                    from uuid import uuid4
                    event = Opportunity(uuid4().hex,self.session.session_id,now,None,None,audience,
                                        self.session._latest_speech,False,'conversation_lull',source,
                                        min(now-self.started_at,now-self.session._latest_speech,now-self.session.last_interaction_at),
                                        audience_key=audience_key)
            cues=tuple(c for c in self.observations.candidates() if self.session.state.observation_available(c.public())) if self.observations else ()
            scene=self.scene_observer.snapshot() if self.scene_observer else None
            if scene and self.session.state.store.rows('SELECT 1 FROM observation_delivery WHERE cue_id=? AND delivered_at>?',
                    (scene[0],self.session.state.wall_clock()-86400)):scene=None
            if event is None and (cues or scene):
                audience,audience_key=self.arrivals.audience_snapshot(now)
                if audience:
                    from .proactive import Opportunity
                    from uuid import uuid4
                    current=self.session.state.current
                    event=Opportunity(uuid4().hex,self.session.session_id,now,None,None,audience,
                        self.session._latest_speech,False,'ambient',current[0] if current else None,
                        min(now-self.started_at,now-self.session._latest_speech,now-self.session.last_interaction_at),
                        audience_key=audience_key)
            if (event is None and self.drive is not None and self.drive.radar is not None and not self.arrivals.faces
                    and now-self.faces_seen_at>=5 and now-self.last_unseen>=90 and self.drive.availability(now)[0]):
                # A moving body outside the camera view: the model may turn to look or stay put.
                bodies = [b for b in (self.drive.radar.bodies() or ()) if b['moving']]
                if bodies:
                    from .proactive import Opportunity
                    from uuid import uuid4
                    current=self.session.state.current
                    event=Opportunity(uuid4().hex,self.session.session_id,now,None,None,(),
                        self.session._latest_speech,False,'unseen_presence',current[0] if current else None,
                        min(now-self.started_at,now-self.session._latest_speech,now-self.session.last_interaction_at))
                    self.last_unseen=now
                    cues,scene=(),None
            if event is None:return
            event=replace(event,public_cues=cues,scene_id=scene[0] if scene else None,scene_images=scene[1] if scene else ())
            if self.observations:self.observations.attempted([c.cue_id for c in cues])
            if scene:self.scene_observer.attempted(scene[0])
            revision = self.session.revision
        self.trace.emit('proactive_opportunity',event_id=event.event_id,kind=event.kind,
                        face_track_id=event.target_track,person_id=event.person_id)
        def valid():
            nonlocal invalidation_reason
            # Sample the producer timestamp before the clock: a microphone frame
            # arriving between these reads must not look like future audio.
            audio_seen_at = self.audio_seen_at
            now = self.trace.clock()
            audio_age = now-audio_seen_at
            # The camera worker may have published a newer analyzed frame since
            # the main loop last polled. Validate the latest evidence, not that
            # older polling snapshot. This reads cache only; no device work.
            self.arrivals.observe(self.camera.latest(now),now)
            intention = self.session.state.conversation_intent.record
            from .conversation_intent import audience
            committed = self.session.motion_committed
            intent_audience_changed = (not committed and intention is not None and intention.source_id == event.event_id
                and intention.audience != audience({'faces':self.arrivals.faces})
                and (self.session._proactive_delivery is None or
                     self.session._proactive_delivery.first_audio_at is None or
                     self.session._audience_bound_output or bool(intention.memory_ids)))
            reason = ('intent_audience_changed' if intent_audience_changed else
                      'shutdown' if self.stop.is_set() else
                      'human_speech' if self.session._latest_speech!=event.speech_marker else
                      'capture_queued' if not self.captures.empty() else
                      'turn_queued' if not self.turns.empty() else
                      'recognition_busy' if self.asr_busy.is_set() else
                      'audio_timestamp_ahead' if audio_age<0 else
                      'audio_stale' if audio_age>.5 else
                      'audience_changed_or_stale' if (not committed and not self.arrivals.valid(event,now,
                          audience_bound=self.session._audience_bound_output) and
                          (self.session._proactive_delivery is None or
                           self.session._proactive_delivery.first_audio_at is None or
                           self.session._audience_bound_output)) else None)
            if reason and not invalidated.is_set():
                invalidation_reason = reason
                invalidated.set()
                self.trace.emit('proactive_invalidated',event_id=event.event_id,kind=event.kind,
                                reason=reason,audio_age_seconds=audio_age)
            return reason is None
        invalidated = threading.Event()
        invalidation_reason = None
        result = self.session.process_opportunity(event,revision,valid)
        self.arrivals.finished(event,result)
        with self.session._lock:
            if (revision == self.session.revision and self.session._latest_speech == event.speech_marker
                    and not self.stop.is_set() and not self.session.closed
                    and initiative.retry_unheard(event,result,invalidation_reason,self.trace.clock())):
                self.trace.emit('proactive_retry_pending',event_id=event.event_id,
                                source_turn_id=event.source_turn_id,reason=invalidation_reason,
                                expires_at=initiative.lull_retry_until)
        self.trace.emit('proactive_opportunity_done',event_id=event.event_id,kind=event.kind,status=result.status,medium=result.medium,
                        delivered=bool(result.completed_text),failed=result.error)

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
        from .input_health import InputHealth
        health = InputHealth(self.trace,getattr(self.mic,'device_name','configured_microphone'))
        self.start_workers()
        self.trace.emit("listening", controls="Enter or 'stop' cancels; say 'shut down', type 'quit', or Ctrl-C to exit")
        previous_dropped = (0, 0)
        previous_end = None
        previous_echo = False
        vad_fresh_at = None
        next_resources = 0.
        next_visual = 0.
        # Nonblocking terminal input is polled alongside frames; no orphan stdin worker.
        import select
        try:
            while not self.stop.is_set():
                if self.robot:
                    capturing = bool(self.endpoint.frames)
                    self.robot.before_frame(capturing=capturing or self.session._processing.locked(), allow_idle=(
                        not capturing and self.captures.empty() and self.turns.empty()
                        and not self.asr_busy.is_set() and not self.session._processing.locked()
                        and not (self.drive is not None and self.drive.motion_risk())
                        and self.trace.clock()-self.audio_seen_at<=.5))
                now = self.trace.clock()
                if now>=next_visual:
                    self.observe_visual(now)
                    next_visual = now+.25
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
                    if command in {"quit", "exit", "shut down", "shutdown"}:
                        break
                    if command in {"", "stop", "cancel"}:
                        self.cancel()
                frame = self.mic.read()
                if frame is None:
                    continue
                self.audio_seen_at = frame.ended_at
                if self.recorder and frame.started_at>=self.started_at:
                    try:
                        path=self.recorder.record_input(frame)
                        if path:self.trace.emit('input_diagnostic_saved',path=str(path))
                    except Exception as exc:
                        self.trace.emit('recording_failed',kind='input_diagnostic',error_type=type(exc).__name__)
                counts = (self.mic.dropped, self.mic.status_errors)
                if counts != previous_dropped or (previous_end is not None and frame.started_at - previous_end > .05):
                    self.trace.emit("capture_gap", dropped=counts[0], status_errors=counts[1],
                        gap_seconds=frame.started_at-previous_end if previous_end is not None else None,
                        utterance_discarded=bool(self.endpoint.frames))
                    self.endpoint.reset()
                    self.vad.reset()
                    previous_dropped = counts
                previous_end = frame.ended_at
                if frame.started_at <= self.cancel_before:
                    self.endpoint.reset()
                    continue
                # Silero's recurrent state drifted numb after long echo/servo noise
                # (September 27: live 2 speech frames where a fresh model found 30).
                # Reset when playback ends and every 4 s of quiet with nothing pending;
                # the 1 s pre-roll keeps any onset the fresh state has to re-learn.
                if (previous_echo and not frame.echo_risk) or (not self.endpoint.frames and
                        vad_fresh_at is not None and frame.started_at - vad_fresh_at >= 4.):
                    self.vad.reset()
                    vad_fresh_at = frame.started_at
                if vad_fresh_at is None or self.endpoint.frames:
                    vad_fresh_at = frame.started_at
                previous_echo = frame.echo_risk
                speech = self.vad.is_speech(frame.pcm)
                if speech and self.robot:
                    # Even motion-contaminated VAD activity stops decorative idle
                    # motion. Existing echo/hold guards still exclude it from learning.
                    self.robot.note_speech()
                health.observe(frame,speech,self.endpoint.frames,
                    vad_probability=getattr(self.vad,'last_probability',None))
                if speech and not frame.echo_risk and not (self.drive is not None and self.drive.moving.is_set()):
                    # Motor noise or talk while the wheels run does not yield the plan;
                    # the capture still becomes a turn, and a spoken stop still halts.
                    voiced = (self.endpoint.voiced if self.endpoint.frames else 0.) + frame.ended_at - frame.started_at
                    self.session.note_speech(frame.ended_at, sustained=voiced >= YIELD_VOICED_SECONDS)
                capture = self.endpoint.push(frame, speech)
                if capture:
                    capture = replace(capture,device=getattr(self.mic,"source_device",getattr(self.mic,"device_name","configured_microphone")))
                    if self.robot:
                        capture = replace(capture,robot_microphone_hold=self.robot.capture_held(capture))
                    if self.recorder:
                        self.recorder.save(capture)
                    self.trace.emit("capture", capture_id=capture.capture_id,
                        started_at=capture.started_at, ended_at=capture.ended_at,
                        speech_ended_at=capture.speech_ended_at, truncated=capture.truncated,
                        echo_risk=capture.echo_risk, motion_risk=capture.motion_risk, pcm_bytes=len(capture.pcm),
                        vad_voiced_seconds=sum(b-a for a,b in capture.voiced_intervals),
                        robot_microphone_hold=capture.robot_microphone_hold)
                    self.enqueue(self.captures, (capture, self.session.revision), "captures")
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            # A stalled microphone or device error ends the run: record why.
            self.trace.emit('runtime_failed', error_type=type(exc).__name__,
                            reason=str(exc) if isinstance(exc, ProviderFailure) else None)
            raise
        finally:
            self.close()

    def close(self):
        self.stop.set()
        self.cancel()
        self.mic.cancel()
        if self.recorder:
            try:
                path=self.recorder.close()
                if path:self.trace.emit('input_diagnostic_saved',path=str(path))
            except Exception as exc:
                self.trace.emit('recording_failed',kind='input_diagnostic',error_type=type(exc).__name__)
        deadline = time.monotonic() + 18  # Network read bound plus cooperative cleanup.
        for worker in self.workers:
            worker.join(max(0, deadline - time.monotonic()))
        stuck = [w.name for w in self.workers if w.is_alive()]
        output_worker = self.session.output_worker
        if output_worker is not None and output_worker.is_alive():
            stuck.append(output_worker.name)
        if stuck:
            self.trace.emit("worker_stuck", workers=stuck)
            raise ProviderFailure("RuntimeWorkersDidNotStop")
        self.session.closed = True
