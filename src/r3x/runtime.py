"""Live orchestration: bounded capture/ASR queues feeding the shared Session."""
from queue import Queue, Empty, Full
from dataclasses import replace
import sys
import threading
import time

from .capture import Endpoint
from .contracts import Cancellation, Cancelled, ProviderFailure
from .session import is_quiet_command, is_stop_command

# Voiced time within one utterance before it yields an unheard reply. Live September 28:
# every phantom "Oh."/"Okay." noise capture had <= 0.48 s voiced; real speech >= 0.64 s.
YIELD_VOICED_SECONDS = .5
# Voiced time before the head-turn reflex looks up the voice's direction. The
# direction must still be clear (3+ strong samples, one dominant beam), which the
# single-frame noises heard as "Oh." rarely were.
REFLEX_VOICED_SECONDS = .3
# How long the idle tick waits for a just-started goal plan before returning to listening.
# Hosted planning takes seconds and finishes in the background; this bounds the pause.
PLANNING_GLANCE_SECONDS = .25
# A goal is planned at most this long before its first beat is due.
PLAN_LEAD_SECONDS = 20.

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
        self._initiative_gate = None

    def cancel(self, quiet=False):
        if self.robot:
            self.robot.idle.stop()
            self.robot.reflex.stop()
            self.robot.owner.release_microphone_hold()
        with self.control_lock:
            self.cancel_before = self.trace.clock()
            self.session.cancel('shutdown' if self.stop.is_set() else 'explicit_stop',
                                quiet=quiet and not self.stop.is_set())
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
                        # Bypasses a busy conversational generation.
                        if is_quiet_command(transcript.text): self.cancel(quiet=True)
                        else: self.cancel()
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
            self.arrivals.observe(self.camera.latest(now) if self.camera else (),now)
            self.session.check_opportunity()

    def initiative_gate(self, reason):
        """Report state changes, not every idle poll."""
        if reason != self._initiative_gate:
            self._initiative_gate = reason
            policy = self.session.state.initiative if self.session.state else None
            self.trace.emit('initiative_gate', reason=reason,
                            due_at=policy.agency.due_at if policy and policy.agency and policy.agency.due_at!=float('-inf') else None)

    def proactive_tick(self, _after_planning=False):
        """Rex's own initiative, in priority order (owner direction, October 2):
        people talking come first (they never reach this method's work), a person who
        arrives is greeted, and when nothing is happening Rex pursues his goal. Goals are
        planned in the background; a human turn neither waits for nor discards them."""
        if not self.session.state or self.session.state.frozen:
            self.initiative_gate('state_unavailable_or_frozen')
            return
        now = self.trace.clock()
        with self.control_lock:
            self.arrivals.observe(self.camera.latest(now) if self.camera else (), now)
            initiative = self.session.state.initiative
            agency = self.session.state.agency
            agency.observe_faces(self.arrivals.faces, now)
            quiet_seconds = min(now-self.started_at, now-self.session.last_trusted_speech_at,
                                now-self.session.last_interaction_at)
            audience, audience_key = self.arrivals.audience_snapshot(now)
            with self.session._lock:
                intentions = self.session.state.conversation_intent
                if intentions.record:
                    from .memory_opportunities import candidates
                    memories = candidates(self.session.state.store, self.arrivals.faces,
                        self.session.state.calendar_today(), limit=128, include_delivered=True)
                    intentions.refresh({'faces': self.arrivals.faces,
                        'participation': initiative.context(now)}, [r['claim_id'] for r in memories])
            gate = ('shutdown' if self.stop.is_set() or self.session.closed else
                    'startup' if now-self.started_at < 2 else
                    'audio_unavailable' if not 0 <= now-self.audio_seen_at <= .5 else
                    'human_work_pending' if self.asr_busy.is_set() or not self.captures.empty() or
                        not self.turns.empty() or bool(self.endpoint.frames) else
                    'unresolved_speech' if self.session._latest_speech > self.session.resolved_audio_at and
                        now-self.session._latest_speech < self.lull_seconds else
                    'explicit_stop_or_human_floor' if now < self.session.proactive_hold_until else
                    'quiet_requested' if initiative.quiet else
                    'human_floor' if initiative.floor_held(now) else
                    'agency_planner_unavailable' if self.session.agency_planner is None else None)
            if gate:
                self.initiative_gate(gate)
                return
            if quiet_seconds < 2:
                self.initiative_gate('conversation_active')
                return
            planning_only = False
            # People first: a settled new arrival is greeted before any goal work.
            event = self.arrivals.take(self.session.session_id, now, self.session._latest_speech)
            if event is not None and agency.spoke_recently(event.person_id, now):
                # Their face was unrecognized for a while, but they have been talking to Rex:
                # the same visit, not an arrival to greet again.
                self.trace.emit('arrival_skipped', person_id=event.person_id, reason='spoke_recently')
                event = None
            if event is None:
                if not agency.someone_around(now, self.started_at):
                    self.initiative_gate('nobody_around')
                    return
                if agency.needs_plan(now):
                    if self.session.planning_active():
                        self.initiative_gate('planning_goal')
                        return
                    from .agency import PLAN_MIN_INTERVAL_SECONDS
                    if now < agency.due_at - PLAN_LEAD_SECONDS:
                        # Easing off after unanswered beats: plan shortly before it is needed,
                        # so the goal is fresh and no planner call is spent on a long rest.
                        self.initiative_gate('resting')
                        return
                    if (now >= agency.planner_retry_at and not _after_planning and
                            now-agency.plan_started >= PLAN_MIN_INTERVAL_SECONDS):
                        planning_only = True  # Prepared in the background; executed at a lull.
                    elif quiet_seconds < self.lull_seconds or not agency.due(now):
                        self.initiative_gate('planner_retry_wait')
                        return
                elif quiet_seconds < self.lull_seconds or not agency.due(now):
                    self.initiative_gate('goal_waiting_for_lull')
                    return
            from .proactive import Opportunity
            from uuid import uuid4
            source = self.session.state.conversation_source
            if event is None:
                event = Opportunity(uuid4().hex,self.session.session_id,now,None,None,audience,
                    self.session._latest_speech,False,'reconsideration',source,quiet_seconds,
                    audience_key=audience_key)
            # Coalesce perception into the next beat. Each feed item/frame no longer
            # creates its own model attempt or consumes a conversation budget.
            cues = tuple(c for c in self.observations.candidates()
                         if self.session.state.observation_available(c.public())) if self.observations else ()
            # The planner gets the current frame even when it was already offered or the view
            # is unchanged; review October 3 found it planning blind after the first goal.
            scene = (self.scene_observer.snapshot(True) if planning_only else
                     self.scene_observer.snapshot()) if self.scene_observer else None
            event = replace(event, public_cues=cues, scene_id=scene[0] if scene else None,
                            scene_images=scene[1] if scene else ())
            if self.observations and not planning_only:
                self.observations.attempted([c.cue_id for c in cues])
            if scene and not planning_only:
                self.scene_observer.attempted(scene[0])
            revision = self.session.revision
            self.initiative_gate('preparing_goal' if planning_only else 'greeting' if event.kind=='visual_arrival' else 'pursuing_goal')
        self.trace.emit('agency_preparation_opportunity' if planning_only else 'proactive_opportunity',event_id=event.event_id,kind=event.kind,
                        face_track_id=event.target_track,person_id=event.person_id)
        # Faces in view when the beat began: a newcomer is one more face, not a new track ID.
        def company(at):
            # A tiny or weak detection (a portrait, a false positive) is not a newcomer.
            return [f for f in self.arrivals.faces if 0 <= at-f['at'] <= 1.5 and
                    (f.get('identity') or (f.get('face_width_px') or 48) >= 48)]
        visible_at_start = max(len(event.faces), len(company(now)), 1)
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
            self.arrivals.observe(self.camera.latest(now) if self.camera else (),now)
            agency.observe_faces(self.arrivals.faces, now)
            intention = self.session.state.conversation_intent.record
            from .conversation_intent import audience
            # A committed silent move (wheels or a pose) finishes like audible speech: its own
            # motor noise and camera reframe must not cancel it. A spoken stop still halts it.
            committed = self.session.motion_committed
            intent_audience_changed = (not committed and intention is not None and intention.source_id == event.event_id
                and intention.audience != audience({'faces':self.arrivals.faces})
                and (self.session._audience_bound_output or bool(intention.memory_ids)))
            memory_person = self.session._memory_subject
            fresh = company(now)
            known = {f.get('identity') for f in fresh if f.get('identity')}
            question_track = self.session._question_track
            question_lost = False
            if question_track and not any(f['track_id'] == question_track for f in fresh):
                # The camera renumbers one face every few seconds. The question still has its
                # person when someone of the same identity (or, for a stranger, any unrecognized
                # face) is in view; it is lost when they are gone or replaced by someone known.
                asked = next((f.get('identity') for f in event.faces if f['track_id'] == question_track), None)
                question_lost = not any(f.get('identity') == asked for f in fresh)
            # Motor noise is short; only sustained speech interrupts a move already under way.
            spoke = (self.session._resumed_speech > event.speech_marker if committed else
                     self.session._latest_speech != event.speech_marker)
            reason = ('intent_audience_changed' if intent_audience_changed else
                      'shutdown' if self.stop.is_set() else
                      'human_speech' if spoke else
                      'capture_queued' if not committed and not self.captures.empty() else
                      'turn_queued' if not committed and not self.turns.empty() else
                      'recognition_busy' if not committed and self.asr_busy.is_set() else
                      'audio_timestamp_ahead' if audio_age<0 else
                      'audio_stale' if audio_age>.5 else
                      # Identity, not track IDs: renumbered faces of the same person are the same visit.
                      'arrival_left' if (not committed and event.kind=='visual_arrival' and
                          not self.arrivals.still_present(event, now)) else
                      'goal_person_left' if (not committed and self.session.agency_scope=='audience' and
                          not agency.subject_present(now)) else
                      'question_target_left' if (not committed and question_lost) else
                      # Private material waits when someone else joins or replaces its person.
                      'audience_changed_or_stale' if (not committed and (self.session._audience_bound_output or
                          self.session.agency_scope=='audience') and (
                          (memory_person and known and memory_person not in known) or
                          len(fresh) > visible_at_start)) else None)
            if reason and not invalidated.is_set():
                invalidation_reason = reason
                invalidated.set()
                self.trace.emit('proactive_invalidated',event_id=event.event_id,kind=event.kind,
                                reason=reason,audio_age_seconds=audio_age)
            return reason is None
        invalidated = threading.Event()
        invalidation_reason = None
        result = self.session.process_opportunity(event,revision,valid,planning_only=planning_only)
        if planning_only:
            # Planning normally takes seconds and continues in the background. A goal that is
            # ready almost at once is acted on in this same tick when the lull allows.
            self.session.join_planning(PLANNING_GLANCE_SECONDS)
            if not self.session.planning_active():
                return self.proactive_tick(_after_planning=True) or result
            return result
        self.arrivals.finished(event,result)
        if event.kind == 'visual_arrival' and result.status not in {'rejected'}:
            with self.session._lock:
                current = agency.record if agency.applicable(self.trace.clock()) else None
                if not (event.person_id and current and current.get('subject_person') == event.person_id):
                    # A person arriving is the more interesting goal: plan the visit with them.
                    agency.replace('person_arrived', {'person_id': event.person_id, 'track_id': event.target_track})
        if result.status == "interrupted" and not planning_only:
            agency.due_at = max(agency.due_at, self.trace.clock() + self.lull_seconds)
        self.trace.emit('proactive_opportunity_done',event_id=event.event_id,kind=event.kind,status=result.status,medium=result.medium,
                        delivered=bool(result.completed_text),failed=result.error)
        return result

    def start_workers(self):
        def guarded(target):
            try:
                target()
            except BaseException as exc:
                self.stop.set()
                self.session.closed = True
                self.asr_cancel.event.set()
                try:
                    self.session.cancel('shutdown' if self.stop.is_set() else 'explicit_stop')
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
        direction = getattr(self.perception, 'direction', None)
        reflexed = False
        next_resources = 0.
        next_visual = 0.
        # Nonblocking terminal input is polled alongside frames; no orphan stdin worker.
        import select
        try:
            while not self.stop.is_set():
                if self.robot:
                    capturing = bool(self.endpoint.frames)
                    conversation_busy = self.session._processing.locked() and not self.session.preparing_plan
                    self.robot.before_frame(capturing=capturing or conversation_busy, allow_idle=(
                        not capturing and self.captures.empty() and self.turns.empty()
                        and not self.asr_busy.is_set() and not conversation_busy
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
                # ADC timestamps can run slightly ahead of monotonic time after
                # the audio device changes streams. Liveness cannot be newer than
                # receipt; preserve original frame times for capture/attribution.
                self.audio_seen_at = min(frame.ended_at, self.trace.clock())
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
                    if self.robot and direction and not reflexed and voiced >= REFLEX_VOICED_SECONDS:
                        began = self.endpoint.frames[0].started_at if self.endpoint.frames else frame.started_at
                        evidence = direction.during(began, frame.ended_at)
                        if evidence['status'] == 'observed':
                            reflexed = True   # Once per utterance, from its first clear bearing.
                            outcome = self.robot.reflex.face(evidence['bearing_deg'])
                            if outcome != 'started':
                                self.trace.emit('voice_reflex_skipped', reason=outcome,
                                                bearing_deg=round(evidence['bearing_deg'], 1))
                capture = self.endpoint.push(frame, speech)
                if not self.endpoint.frames:
                    reflexed = False
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
