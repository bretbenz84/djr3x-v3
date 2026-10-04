"""One sequential conversational owner shared by live input and scenarios."""
from queue import Queue
import threading
import time
from uuid import uuid4

from .contracts import ASR, Brain, Cancellation, Cancelled, Capture, ProviderFailure, Snapshot, Transcript, ToolCall
from .speakers import VoiceObservation
from .history import TurnHistory
from .output import Delivery, OutputOwner, Phrases, put_phrase

# Syntax-only emergency controls; the only way to cut off audible speech.
STOP_COMMANDS = frozenset({"stop","cancel","halt","whoa","freeze","shut up","stop talking","be quiet","quiet","hush"})
# Strict fast path: the whole utterance is a stop phrase, optionally wrapped in a few
# exclamations or Rex's name and repeated ("Oh, stop!", "Stop, stop, stop!", "Whoa, Rex, stop").
# September 27: "Oh, stop!" missed the exact-match list while he drove toward an obstacle.
STOP_WRAPPERS = frozenset({"oh","hey","no","whoa","rex","okay","ok","please","now","wait"})


# Stop phrases that ask for silence rather than a halt. They also leave Rex quiet (no
# self-started talk or movement) until someone invites him to talk again; a plain "stop"
# only pauses his initiative for two minutes. Review October 3: with goals always running,
# "be quiet" bought two minutes before the next unprompted line.
QUIET_COMMANDS = frozenset({"shut up","stop talking","be quiet","quiet","hush"})


def is_quiet_command(text: str) -> bool:
    """A stop command made only of quiet phrases (syntax only, like is_stop_command)."""
    if not is_stop_command(text):
        return False
    words = text.lower().replace(",", " ").replace(".", " ").replace("!", " ").replace("?", " ").split()
    i = 0
    while i < len(words):
        pair = " ".join(words[i:i+2])
        if pair in STOP_COMMANDS:
            if pair not in QUIET_COMMANDS: return False
            i += 2
        elif words[i] in STOP_COMMANDS:
            if words[i] not in QUIET_COMMANDS: return False
            i += 1
        else:
            i += 1
    return True


def is_stop_command(text: str) -> bool:
    words = text.lower().replace(",", " ").replace(".", " ").replace("!", " ").replace("?", " ").split()
    phrases, i = 0, 0
    while i < len(words):
        pair = " ".join(words[i:i+2])
        if pair in STOP_COMMANDS:
            phrases, i = phrases + 1, i + 2
        elif words[i] in STOP_COMMANDS:
            phrases, i = phrases + 1, i + 1
        elif words[i] in STOP_WRAPPERS:
            i += 1
        else:
            return False
    return phrases > 0


class Session:
    def __init__(self, session_id: str, brain: Brain | None, output: OutputOwner, trace, state=None):
        self.session_id, self.brain, self.output, self.trace = session_id, brain, output, trace
        self.agency_planner = None
        self.revision = 0
        self.history = TurnHistory()
        self._lock = threading.RLock()
        self._processing = threading.Lock()
        self._cancel: Cancellation | None = None
        self._delivery = None
        self._opportunity_guard = None
        self._proactive_delivery = None
        self._audience_bound_output = False
        self.closed = False
        self.output_worker = None
        self.state = state
        if state is not None:
            state.agency.new_session()
            state.attention.scene.session_id = session_id
            state.conversation_intent.clear("new_session")
        self._latest_speech = float("-inf")
        self._resumed_speech = float("-inf")  # Latest sustained speech; only this yields.
        self._owed_reply = False  # A trusted human turn was yielded before Rex spoke.
        self._capture_end = float("inf")
        self.proactive_hold_until = float("-inf")
        self.last_interaction_at = float("-inf")
        self.last_spoken_reply = None
        self.last_trusted_speech_at = float("-inf")
        self.resolved_audio_at = float("-inf")
        self.motion_committed = False  # Wheels were handed a plan in the current turn.
        self.preparing_plan = False
        self.agency_scope = None
        self._memory_subject = None  # Person whose private memory the current output uses.
        self._question_track = None  # Face a pending proactive question is aimed at.
        # Goal planning runs beside conversation, never in front of it (October 2).
        self._planning_thread = None
        self._planning_cancel = Cancellation()

    def planning_active(self) -> bool:
        return self._planning_thread is not None and self._planning_thread.is_alive()

    def join_planning(self, timeout=10.) -> None:
        thread = self._planning_thread
        if thread is not None:
            thread.join(timeout)

    def _repeats_recent_line(self, call):
        """Exact repetition of one of Rex's last heard lines; wording, not meaning."""
        text = call.arguments.get('text') if isinstance(call.arguments, dict) else None
        if not isinstance(text, str) or not text.strip():
            return False
        from .output import plain_text
        def norm(value):
            return ' '.join(plain_text(value).lower().split())  # Delivery tags are not words.
        said = [norm(m['content']) for m in self.history_messages()
                if m.get('role') == 'assistant' and isinstance(m.get('content'), str)][-4:]
        return norm(text) in said

    def _subject_person(self, context):
        """Who Rex's next goal is about: an arrival, the private-memory person, or one known face."""
        pending = self.state.agency.pending_subject or {}
        if pending.get('person_id'):
            return pending['person_id']
        memory = (context.get('personal_memory') or {}).get('person_id')
        if memory:
            return memory
        known = {f.get('identity') for f in context.get('faces', []) if f.get('identity')}
        if len(known) == 1:
            return next(iter(known))
        if not context.get('faces') and self.state.current and getattr(self.state.current[4], 'person_id', None):
            return self.state.current[4].person_id
        return None

    def _planning_job(self, turn_id, context, planning_context, audience, capabilities, opportunity):
        """Everything the planner sees, captured now; planning itself may finish later."""
        from .agency import evidence_catalog, recent_dialogue
        from .servo_poses import POSE_COMMANDS
        agency = self.state.agency
        now = self.trace.clock()
        evidence = evidence_catalog(context)
        subject_person = self._subject_person(context)
        people = {p['id']: p for p in context.get('people', [])}
        faces = context.get('faces', [])
        memory = context.get('personal_memory') or {}
        subject = None
        if subject_person:
            same = memory.get('person_id') == subject_person
            subject = {'person_id': subject_person, 'display_name': people.get(subject_person, {}).get('name'),
                'visible_now': any(f.get('identity') == subject_person for f in faces),
                'known_facts': [{'id': r.get('id'), 'predicate': r.get('predicate'), 'value': r.get('value')}
                                for r in memory.get('interests_and_facts', [])] if same else [],
                'recent_exchanges': memory.get('recent_exchanges', []) if same else [],
                'episodes': [{'id': r.get('id'), 'summary': r.get('summary')} for r in memory.get('episodes', [])] if same else [],
                'meaning': 'A known person. Saved facts are private to them: audience scope only.'}
        elif faces:
            subject = {'person_id': None, 'unknown_visitor': True, 'visible_now': True,
                'meaning': 'Someone Rex does not know yet. Visibility is not identity.'}
        images = ()
        if opportunity is not None and opportunity.scene_images:
            images = tuple(i for i in opportunity.scene_images[-1:] if 0 <= now - i.at <= 30)
        targets = (context.get('drive') or {}).get('targets', [])
        facts = {'request_id': turn_id, 'revision': agency.revision,
            'goal_trigger': agency.ending_reason(now),
            'subject': subject,
            'visible_people': [{'track_id': f.get('track_id'), 'person_id': f.get('identity'),
                                'display_name': people.get(f.get('identity'), {}).get('name'),
                                'position': f.get('position')} for f in faces],
            'conversation': recent_dialogue(self.history_messages()),
            'evidence': evidence, 'capabilities': capabilities,
            'capability_contract': {'gesture': 'A short spoken thought with a finite nod/tilt; not a silent pose.',
                'attention_mode': context.get('attention', {}).get('mode'),
                'pose': {'enabled': 'pose' in capabilities, 'commands': list(POSE_COMMANDS),
                         'reach_out_hand': 'throttle_offer', 'look_and_comment': 'pose with look_after',
                         **context.get('robot_pose_state', {})},
                'drive': context.get('drive'),
                'inspection_directions': context.get('inspection_directions', [])},
            'room': {'image_attached': bool(images),
                     'objects': [t for t in targets if t.get('kind') == 'object'],
                     'visual_memory': [r for r in context.get('visual_memory', []) if r.get('audience') == 'general'][:8],
                     'meaning': 'Detector labels are coarse and miss photos, paintings and small things; the image shows more. '
                                'Seek a described thing to drive to something without a target.'},
            'current_goal': agency.context(now),
            'recent_goals': agency.goal_history(now),
            'engagement': agency.engagement(),
            'boundaries': context.get('participation', {}),
            'actual_actions': context.get('action_continuity', {}),
            'pending_question': context.get('pending_question'),
            'invitations': context.get('invitations'),
            'dialogue_use': 'Private continuity and feedback, not proof an earlier speaker is present. '
                            'Do not disclose prior personal facts to a changed audience.'}
        return {'turn_id': turn_id, 'facts': facts, 'evidence': evidence, 'capabilities': capabilities,
                'audience': audience, 'subject_person': subject_person, 'revision': agency.revision, 'images': images}

    def _start_planning(self, job):
        cancel = Cancellation()
        self._planning_cancel = cancel
        def run():
            try:
                self._run_planning(job, cancel)
            except BaseException as exc:  # A planner bug must never take the conversation down.
                with self._lock:  # Back off like any failure; never a paid retry every few seconds.
                    self.state.agency.failed('PlanningCrashed:'+type(exc).__name__, self.trace.clock())
                self.trace.emit('agency_planning_crashed', turn_id=job['turn_id'], error_type=type(exc).__name__)
        thread = threading.Thread(target=run, name='r3x-agency-planner', daemon=True)
        self._planning_thread = thread
        thread.start()

    def _run_planning(self, job, cancel):
        """One planner call, one bounded repair; failure leaves a timed retry, never silence."""
        agency = self.state.agency
        facts = job['facts']
        started = self.trace.clock()
        agency.plan_started = started
        try:
            for attempt in range(2):
                self.trace.emit('model_request', turn_id=job['turn_id'], primary_calls=0,
                    purpose='agency_planning' if attempt == 0 else 'agency_plan_repair',
                    model=self.agency_planner.capability.model, image_count=len(job['images']),
                    reasoning_effort=self.agency_planner.reasoning_effort, goal_trigger=facts.get('goal_trigger'))
                proposed = self.agency_planner.plan(facts, cancel, images=job['images'])
                cancel.check()
                try:
                    with self._lock:
                        if self.closed: raise Cancelled()
                        agency.install(proposed, job['audience'], job['evidence'], job['capabilities'],
                                       job['revision'], self.trace.clock(), subject_person=job['subject_person'])
                    return True
                except ValueError as exc:
                    if str(exc) == 'StaleAgencyPlan': raise Cancelled() from None
                    self.trace.emit('agency_plan_rejected', turn_id=job['turn_id'], attempt=attempt+1,
                        reason=str(exc), proposal=proposed, offered_evidence_ids=list(job['evidence']))
                    if attempt: raise
                    facts = {**facts, 'repair': {'validation_error': str(exc), 'rejected_proposal': proposed,
                        'instruction': 'Correct this proposal. Use only offered evidence and capabilities.'}}
        except Cancelled:
            self.trace.emit('agency_planning_cancelled', turn_id=job['turn_id'])
            if cancel is not self._planning_cancel and cancel.event.is_set():
                raise  # Inline planning inside an opportunity follows that opportunity.
            return False
        except (ValueError, ProviderFailure) as exc:
            with self._lock:
                agency.failed(str(exc), self.trace.clock())
            return False
        finally:
            self.trace.emit('agency_planning_finished', turn_id=job['turn_id'],
                model=self.agency_planner.capability.model, planning_seconds=self.trace.clock()-started)

    def cancel(self, reason='explicit_stop', *, quiet=False) -> None:
        with self._lock:
            self.proactive_hold_until = self.trace.clock()+120
            self.revision += 1
            self._planning_cancel.event.set()
            if self._cancel:
                self._cancel.event.set()
            if self.state:
                self.state.initiative.cancel_schedule(reason)
                self.state.conversation_intent.suspend(reason)
                self.state.cancel()
                if quiet and not self.state.frozen:
                    # Same switch the model uses for "stop starting conversations". Direct
                    # questions still get answers; an invitation to talk again resumes him.
                    self.state.initiative.quiet = True
                    self.state.initiative.policy_source = None
                    self.state.initiative.cancel_schedule('initiative_quiet')
        self.output.cancel()
        self.trace.emit("cancel", revision=self.revision)
        if quiet and self.state and self.state.initiative.quiet:
            self.trace.emit('initiative_quiet', source='stop_phrase')

    def note_speech(self, ended_at: float, sustained: bool = True) -> None:
        """Yield an unheard reply to resumed, non-echo microphone speech.

        This is not an explicit stop: keep revision, pending questions, and the
        original user history so the continuation can be understood normally.
        Only sustained speech yields. September 28: single noise frames that ASR
        heard as "Oh." cancelled five unheard replies in a row; the noise still
        becomes its own turn, it just no longer silences the answer before it.
        """
        with self._lock:
            self._latest_speech = max(self._latest_speech, ended_at)
            if not sustained:
                return
            if self.state and ended_at > self._resumed_speech:
                self.state.initiative.cancel_schedule("human_speech")
            self._resumed_speech = max(self._resumed_speech, ended_at)
            audible = self._cancel is not None and self._cancel.is_committed()
            if self._cancel and ended_at > self._capture_end and not self._cancel.event.is_set() and not audible:
                self._cancel.event.set()
                self.trace.emit("reply_yielded", reason="user_resumed_speaking")

    def check_opportunity(self):
        cancel_output = False
        with self._lock:
            audible = self._cancel is not None and self._cancel.is_committed()
            if self._opportunity_guard and self._cancel and not self._cancel.event.is_set() and not audible and not self._opportunity_guard():
                self._cancel.event.set()
                cancel_output = True
        if cancel_output: self.output.cancel()

    def close(self) -> None:
        self.closed = True
        self.cancel('shutdown')
        self.join_planning(2.)
        for adapter in (getattr(self.output,"attention",None),getattr(self.output,"gestures",None),getattr(self.output,"inspection",None)):
            if adapter is not None:
                adapter.close()

    def observe_attention(self, frame):
        """Publish explicit simulated perception through the owner, never from model text."""
        with self._lock:
            if self.closed or self.state is None or self.state.frozen or (getattr(self.output, 'attention', None) is None and getattr(self.output,'gestures',None) is None):
                raise ValueError('AttentionUnavailable')
            self.state.attention.scene.publish(frame)
            self.state.attention.context()
            self.trace.emit('attention_frame', observation_id=frame.observation_id,
                            view_epoch=frame.view_epoch, target_count=len(frame.targets), medium=frame.medium)

    def history_messages(self):
        return self.history.messages()

    def process_turn(self, capture: Capture, transcript: Transcript, submitted_revision: int, observation=None, faces=(), *, latest_faces=None) -> Delivery:
        return self._process(capture,transcript,submitted_revision,observation,faces,latest_faces=latest_faces)

    def process_opportunity(self, event, submitted_revision, still_valid, *, planning_only=False):
        return self._process(None,None,submitted_revision,faces=event.faces,opportunity=event,still_valid=still_valid,
                             planning_only=planning_only)

    def _process(self, capture, transcript, submitted_revision, observation=None, faces=(), opportunity=None, still_valid=None, latest_faces=None, planning_only=False):
        if not self._processing.acquire(blocking=False):
            raise RuntimeError("Only one conversation owner may process turns")
        worker = None
        delivery = Delivery()
        turn_id = uuid4().hex
        began = self.trace.clock()
        from .proactive import OpportunityCancellation
        cancel = OpportunityCancellation(still_valid) if opportunity else Cancellation()
        cancel_reason = [None]  # Why a human reply was withheld; always traced.
        # Owner rule: audible speech finishes unless explicitly stopped.
        cancel.committed = lambda: delivery.first_audio_at is not None
        source_session = opportunity.session_id if opportunity else capture.session_id
        source_id = opportunity.event_id if opportunity else capture.capture_id
        source_end = opportunity.at if opportunity else capture.ended_at
        speech_end = None if opportunity else capture.speech_ended_at
        model_at = None
        first_token_at = None
        model_error = None
        question = None
        memory_selected = None
        observation_selected = None
        def refresh_current_view(context):
            if latest_faces is None or opportunity or not self.state:
                return
            from .face_attribution import public_face
            from .faces import VISIBLE_FACE_MAX_AGE
            now = self.trace.clock()
            rows = latest_faces(now)
            people = {p['id'] for p in self.state.store.people()}
            visible = []
            for row in rows:
                if not 0 <= now-row['at'] <= VISIBLE_FACE_MAX_AGE or row.get('face_epoch',0) < self.state.min_face_epoch:
                    continue
                face = public_face(row)
                if face.get('identity') not in people:
                    face['identity'] = None
                    face['evidence'] = 'face_unresolved'
                visible.append(face)
            context['current_view'] = {'sampled_at':now,'faces':visible,
                'meaning':'Latest fresh face evidence for who is visible now. Empty means no fresh detected face, not proof the room is empty. Separate from speech-time faces; never authorizes speaker attribution or enrollment.'}
            self.trace.emit('reply_visual_context',turn_id=turn_id,**context['current_view'])
            self.state.note_audience(context)   # The same visit survives a restarted face track.

        def observation_ready():
            # Camera/feed freshness authorizes starting this remark. Ordinary
            # scene movement or cue expiry must not interrupt words already audible.
            if delivery.first_audio_at is not None:
                return True
            ready = self.state.observation_available(observation_selected)
            if not ready and not cancel.event.is_set():
                self.trace.emit('observation_output_rejected', turn_id=turn_id,
                                cue_id=observation_selected['cue_id'],
                                reason='observation_changed_or_expired_before_speech')
            return ready
        attention_intent = None
        performance_gesture = None
        light_request = None
        visual_selected = None
        visual_proposed_text = None
        action_started = False
        state_started = False
        opportunity_attempted = False
        drive_action = None
        drive_noted = False
        invitation_proposal = None
        schedule_proposal = None
        schedule_accepted = False
        schedule_guard = None
        schedule_action_ok = True
        agency_step = None
        agency_revision_reason = None
        action_outcome = None
        try:
            with self._lock:
                if self.closed or source_session != self.session_id or submitted_revision != self.revision:
                    self.trace.emit("turn_rejected", capture_id=source_id, reason="stale")
                    delivery.status = "rejected"
                    return delivery
                self._cancel = cancel
                self._delivery = delivery
                self._opportunity_guard = still_valid if opportunity else None
                self._proactive_delivery = delivery if opportunity else None
                self._audience_bound_output = False
                self._capture_end = opportunity.speech_marker if opportunity else float("inf")
                if opportunity and self.state and self.state.initiative.floor_held(self.trace.clock()):
                    cancel.check()
                    delivery.status,delivery.medium = 'suppressed','none'
                    self.trace.emit('proactive_deferred',event_id=opportunity.event_id,reason='human_floor_held')
                    return delivery
                if opportunity and self.state and not planning_only:
                    self.state.initiative.attempted(opportunity)
                    opportunity_attempted = True
                if opportunity and self._latest_speech!=opportunity.speech_marker: cancel.event.set()
                cancel.check()
            if opportunity:
                self.trace.emit('agency_preparation' if planning_only else 'proactive_turn',turn_id=turn_id,event_id=opportunity.event_id,kind=opportunity.kind,
                    face_track_id=opportunity.target_track,person_id=opportunity.person_id,revision=self.revision)
            else:
                self.trace.emit("turn", turn_id=turn_id, capture_id=capture.capture_id, parent_capture_id=capture.parent_capture_id,
                            original_text=transcript.text, avg_logprob=transcript.avg_logprob,
                            trusted=transcript.trusted, speaker="unresolved", revision=self.revision)
            if not opportunity:
                self.resolved_audio_at = max(self.resolved_audio_at, source_end)
            if not opportunity and (capture.echo_risk or not transcript.text.strip()):
                delivery.status = "rejected"
                self.trace.emit("turn_rejected", turn_id=turn_id,
                                reason="echo_risk" if capture.echo_risk else "empty_transcript")
                return delivery
            # Syntax-only emergency control. No names/facts are parsed in Python.
            if not opportunity and transcript.trusted and is_stop_command(transcript.text):
                self.cancel(quiet=is_quiet_command(transcript.text))
                delivery.status = "interrupted"
                return delivery
            context = {}
            if self.state:
                with self._lock:
                    cancel.check()
                    if opportunity:
                        if self.state.initiative.quiet:
                            raise ProviderFailure('InitiativeQuietRequested')
                        context = self.state.opportunity_context(opportunity,advance_memory_cursor=not planning_only)
                    else:
                        observation = observation or VoiceObservation(self.state.tracker.namespace)
                        attribution = self.state.begin(turn_id,capture,transcript,observation,faces)
                        context = self.state.context(faces)
                        if self.last_spoken_reply:
                            context['reply_timing'] = {
                                **self.last_spoken_reply,
                                'human_speech_ended_at':capture.speech_ended_at,
                                'human_preceded_previous_reply':capture.speech_ended_at < self.last_spoken_reply['started_at']}
                    state_started = not planning_only
                # Keep capture-time attribution/audience across tool continuations
                # and camera changes during the response.
                from copy import deepcopy
                archive_context = deepcopy(context)
                if not opportunity:
                    self.trace.emit("speaker_evidence",turn_id=turn_id,**context["speaker"],
                                    namespace=observation.namespace,window_consistency=observation.consistency)
            if self.brain is None:
                delivery.status = "observed"  # Frozen acoustic replay through the same state path.
                delivery.medium = "none"
                return delivery
            if not opportunity and transcript.trusted:
                self.last_trusted_speech_at = max(self.last_trusted_speech_at, speech_end)
                if self.state:
                    with self._lock:
                        # Presence for Rex's own goal: a person talking is a person here.
                        speaker = context.get('speaker') or {}
                        self.state.agency.observe_speech(
                            speaker.get('person_id') if speaker.get('state') == 'supported' else None, speech_end)
                        self.state.agency.observe_faces(context.get('faces', []), self.trace.clock())
            if not opportunity and not transcript.trusted and not self._owed_reply:
                # Preserve the source in the trace/store, not as a conversational turn.
                # Rejected audio cannot displace real dialogue or postpone the next lull.
                delivery.status, delivery.medium = 'suppressed', 'none'
                self.trace.emit('untrusted_not_answered', turn_id=turn_id, capture_id=capture.capture_id)
                return delivery
            if not opportunity and transcript.trusted and self.state:
                with self._lock:
                    self.state.initiative.cancel_schedule("human_turn")
            if not planning_only: self.history.begin_turn()
            if opportunity:
                import json
                last_reply = next((m['content'] for m in reversed(self.history_messages())
                                   if m.get('role') == 'assistant' and isinstance(m.get('content'), str)), None)
                context['conversation_continuity'] = {
                    'last_completed_reply': last_reply,
                    'new_human_utterance': False,
                }
                evidence = {"kind":opportunity.kind,"event_id":opportunity.event_id,
                            "quiet_seconds":opportunity.quiet_seconds,
                            "source_turn_id":opportunity.source_turn_id,
                            "faces":context['faces']}
                if opportunity.kind=='visual_arrival': evidence['arrival']=context['visual_arrival']
                if not planning_only: self.history.append({'role':'developer','content':
                    ('Startup greeting: a stable person is visible and no human has spoken in this run. '
                     'Say a brief hello now; choose natural wording and optionally invite an unknown person to share their name. '
                     if context.get('visual_arrival',{}).get('startup_greeting') else
                    'Proactive opportunity; nobody spoke and no face is visible, but the base radar reports a moving body '
                    'outside the camera view (see drive.targets). Nobody has been identified. You may turn to look with '
                    'drive_base (face or find_person), optionally saying something, or use remain_silent. '
                     if opportunity.kind=='unseen_presence' else
                    'Someone just came into view (see arrival). Greet them now, briefly and warmly, in your own words: '
                    'by name with a "how are you" when you know them, or a hello and a name question when you do not. '
                    'This is a greeting, not a reply to the earlier conversation. '
                     if opportunity.kind=='visual_arrival' else
                    'Proactive opportunity; nobody spoke. The previous exchange was already processed. '
                    'There is no new human reply; do not answer the earlier utterance again. '
                    'Continue the next grounded step of the private agency plan; do not repeat the previous answer. ')+'Observation: '+json.dumps(evidence)})
            elif self.state:
                import json
                self.history.append({"role":"developer","content":"Evidence for the immediately following utterance: "+json.dumps({
                    "speaker":context["speaker"],"source_turn_id":turn_id,
                    "speech_ended_at":capture.speech_ended_at,"transcript_trusted":transcript.trusted,
                    "reply_timing":context.get('reply_timing'),
                    "faces":context["faces"]},ensure_ascii=False)})
            if not opportunity: self.history.append({"role": "user", "content": transcript.text})
            # A newer capture can arrive while the previous ASR result waits in
            # the queue. Retain its source/history, but do not answer it belatedly.
            with self._lock:
                self._capture_end = opportunity.speech_marker if opportunity else source_end
                if self._resumed_speech > self._capture_end:
                    cancel_reason[0] = cancel_reason[0] or 'newer_speech_arrived'
                    cancel.event.set()
                cancel.check()
            if self.state and not self.state.frozen and (getattr(self.output, 'attention', None) is not None or getattr(self.output, 'gestures', None) is not None or getattr(self.output,'inspection',None) is not None):
                with self._lock:
                    cancel.check()
                    context['attention_enabled'] = getattr(self.output,'attention',None) is not None
                    context['inspection_enabled'] = getattr(self.output,'inspection',None) is not None
                    context['inspection_directions'] = list(getattr(getattr(self.output,'inspection',None),'directions',('current','left','right','behind')))
                    context['inspection_mode'] = 'current_camera_only' if context['inspection_directions']==['current'] else 'simulation'
                    context['gesture_enabled'] = getattr(self.output,'gestures',None) is not None
                    context['recent_performances'] = list(self.state.performances)
                    self.state.attention.medium = getattr(self.output.attention or self.output.gestures,'medium','simulation')
                    context['attention'] = self.state.attention.context()
                    for key,adapter in (('attention_targets',self.output.attention),('gesture_targets',self.output.gestures)):
                        if hasattr(adapter,'targets'):context[key] = list(adapter.targets)
            if self.state and not self.state.frozen and getattr(self.output,'leds',None):
                from .leds import PATTERNS
                led = self.output.leds
                context.update(led_enabled=led.state=='ready',led_version=led.version,
                    led_patterns=[p for p in PATTERNS if (p!='compliment' or 'chest' in led.ports) and (p!='blink' or 'head' in led.ports)])
            context['robot_pose_enabled'] = bool(self.state and not self.state.frozen and getattr(self.output,'robot',None) and self.output.robot.owner.state=='active')
            if context['robot_pose_enabled']:
                from .servo_poses import LIMITS, NAMED_POSES
                owner = self.output.robot.owner
                if opportunity and hasattr(owner,'yield_stale_hold'):
                    owner.yield_stale_hold()  # An aged human pose no longer blocks his own beats.
                held = set(getattr(owner,'held_channels',()))
                commanded = getattr(self.output.robot.owner,'commanded',{})
                context['robot_pose_state'] = {'already_at':[
                    name for name,targets in NAMED_POSES.items()
                    if all(abs(commanded.get(ch,-10000)-value)<=2 for ch,value in targets)],
                    'joint_percent':{str(ch):round(100*(value-LIMITS[ch][0])/(LIMITS[ch][1]-LIMITS[ch][0]))
                                     for ch,value in commanded.items() if ch in LIMITS},
                    'gaze': ({'people': 'following_faces', 'direction': 'holding_chosen_direction'}.get(
                        self.output.robot.idle.mode()) if hasattr(getattr(self.output.robot, 'idle', None), 'mode') else None), 'held_parts':
                    [name for name,channels in (('neck',{0}),('head_lift',{1}),('head_tilt',{2}),('visor',{3}),('hero_elbow',{4}),('hero_wrist',{5}),('poker_arm',{6}),('hero_arm',{7}),('throttle_arm',{8,9,10})) if held.intersection(channels)],
                    'arms_held_until_told': bool(getattr(owner,'hold_until_released',False))}
                if opportunity and held: context['robot_pose_enabled']=False
            if self.state and not self.state.frozen and getattr(self.output,'drive',None) is not None:
                context['drive'] = self.output.drive.context('self_initiated' if opportunity else 'requested',
                    speaker_direction=None if opportunity else (context.get('speaker') or {}).get('direction'))
            elif self.state and not self.state.frozen and getattr(self.output,'drive_unavailable',None):
                from .drive import LIMITS
                origin = 'self_initiated' if opportunity else 'requested'
                context['drive'] = {'available':False,'unavailable_reason':self.output.drive_unavailable,
                    'base_status':{'connected':False,'readings':None},
                    'origin':origin,'targets':[],'limits':dict(LIMITS[origin]),
                    'meaning':'Rex has a wheeled base, but its controller did not connect at startup this run.'}
            scene_images = opportunity.scene_images if opportunity and any(c['kind']=='scene' for c in context.get('observations',[])) else ()
            if self.state and not self.state.frozen:
                from . import visual_memory
                context['visual_memory'] = visual_memory.context(self.state.store,self.session_id,visual_memory.audience(context))
                scene_observer = self.state.scene_observer
                if scene_observer and hasattr(scene_observer,'continuity'):
                    context['camera_continuity'] = scene_observer.continuity()
                if not opportunity:
                    context['visual_correction_candidates'] = visual_memory.correction_candidates(self.state.store,self.session_id,visual_memory.audience(context))
                if 0<len(scene_images)<=2 and all(0<=self.trace.clock()-i.at<=2 for i in scene_images):
                    from hashlib import sha256
                    latest = scene_images[-1]
                    context['scene_description'] = {
                        'request_id':turn_id+':scene', 'status':'observed', 'stage':'complete',
                        'medium':'camera', 'acquisition':'proactive_snapshot', 'motion_status':'not_requested',
                        'frame_id':latest.frame_id, 'captured_at':latest.at,
                        'frames':[{'frame_id':i.frame_id,'captured_at':i.at} for i in scene_images],
                        'image_digest':sha256(latest.data_url.encode()).hexdigest() if len(scene_images)==1 else None,
                        'event_id':opportunity.event_id, 'source_turn_id':opportunity.source_turn_id,
                        'cue_id':opportunity.scene_id, 'question':None,
                        'camera_continuity':context.get('camera_continuity')}
                else:
                    scene_images = ()
            if self.state and not self.state.frozen:
                context['visual_novelty'] = visual_memory.novelty(context['visual_memory'],scene_images)
                context['visual_novelty']['recent_selections'] = [r for r in self.state.visual_selections if r['audience']==visual_memory.audience(context)]
                context['action_continuity'] = self.state.actions.context(visual_memory.audience(context))
            refresh_current_view(context)
            if self.state:
                from . import conversation_memory
                context["personal_memory"] = conversation_memory.context(
                    self.state.store, context, self.session_id, self.state.wall_clock())
            if self.state and not self.state.frozen:
                with self._lock:
                    self.state.intent_context(context, human=not opportunity)
                from .photo_flow import context as photo_context
                context.update(photo_context(self.state))
            memory_subject = context.get('personal_memory',{}).get('person_id')
            memory_accessed = [any(context.get('personal_memory',{}).get(key) for key in
                                   ('recent_exchanges','interests_and_facts','episodes','current_session_exchanges'))]
            self._audience_bound_output = bool(memory_subject and memory_accessed[0])
            self._memory_subject = memory_subject
            if memory_subject and latest_faces:
                previous_memory_guard = cancel.guard
                from .faces import VISIBLE_FACE_MAX_AGE
                def fresh_faces():
                    now = self.trace.clock()
                    # A tiny or weak detection (a portrait, a false positive) is not a newcomer.
                    return [f for f in latest_faces(now) if 0 <= now-f['at'] <= VISIBLE_FACE_MAX_AGE
                            and f.get('face_epoch', 0) >= self.state.min_face_epoch
                            and (f.get('identity') or (f.get('face_width_px') or 48) >= 48)]
                # The reply's own current-view sample: no extra camera read.
                faces_at_start = max(len(context.get('current_view', {}).get('faces', [])), 1)
                def memory_audience_valid():
                    if previous_memory_guard and not previous_memory_guard(): return False
                    if not memory_accessed[0]: return True
                    # Live October 1: two direct requests lost their replies, without a logged
                    # reason, because the owner's face flickered unrecognized for a moment.
                    # Private memory now waits only for a real audience change: someone else,
                    # recognized, in place of its person, or one more face than at the start.
                    # A dropout or a renumbered track is the same visit.
                    current = fresh_faces()
                    known = {f.get('identity') for f in current if f.get('identity')}
                    if (known and memory_subject not in known) or len(current) > faces_at_start:
                        cancel_reason[0] = cancel_reason[0] or 'private_memory_audience_changed'
                        return False
                    return True
                cancel.guard = memory_audience_valid
            from .conversation_intent import audience as intent_audience
            request_audience = intent_audience(context)
            if (self.state and self.agency_planner and opportunity
                    and opportunity.kind not in {'visual_arrival', 'unseen_presence'}):
                # Arrivals are greeted directly (people first); the runtime then hands the
                # person to a new goal. Every other opportunity advances Rex's own goal.
                from .agency import audience_key, capabilities_for
                agency = self.state.agency
                planning_context = context
                if not context.get('faces') and self.state.current:
                    planning_context = {**context,'speaker':{'track_id':self.state.current[4].track_id,
                                                            'person_id':self.state.current[4].person_id}}
                bound_audience = audience_key(planning_context)
                capabilities = capabilities_for(context)
                now = self.trace.clock()
                with self._lock:
                    agency.observe_faces(context.get('faces', []), now)
                    job = None
                    from .agency import PLAN_MIN_INTERVAL_SECONDS
                    if (agency.needs_plan(now) and not self.planning_active() and now >= agency.planner_retry_at
                            and now-agency.plan_started >= PLAN_MIN_INTERVAL_SECONDS):
                        job = self._planning_job(turn_id, context, planning_context, bound_audience, capabilities, opportunity)
                if job and planning_only:
                    # Runs beside conversation: a human turn neither waits for it nor cancels it.
                    self._start_planning(job)
                elif job:
                    self._run_planning(job, cancel)  # Direct callers without a prepared goal.
                if planning_only:
                    delivery.status,delivery.medium='prepared','none'
                    return delivery
                with self._lock:
                    cancel.check()
                    if agency.needs_plan(self.trace.clock()):
                        if self.planning_active():
                            delivery.status,delivery.medium='suppressed','none'
                            self.trace.emit('agency_waiting_for_plan',turn_id=turn_id)
                            return delivery
                        # The planner is unavailable or failed: still do something people notice.
                        agency.install_recovery_step(bound_audience,agency.revision,self.trace.clock(),capabilities,
                            subject_person=self._subject_person(context))
                    agency_step = agency.step(self.trace.clock())
                    if agency_step is None:
                        delivery.status,delivery.medium='suppressed','none'
                        self.trace.emit('agency_no_runnable_beat',turn_id=turn_id,reason=agency.ending_reason(self.trace.clock()))
                        return delivery
                self.agency_scope=agency_step['scope']
                cancel.check()
                context['agency_step'] = agency_step
                if agency_step['scope']=='general':
                    context['memory_opportunities'] = []
                    context['visual_memory'] = [r for r in context.get('visual_memory',[]) if r.get('audience')=='general']
                if agency_step['goal_kind'] not in {'get_to_know','investigate'}:
                    # Hosted probe, October 2: a catch-up beat with a camera frame attached opened
                    # with a remark about the frame instead of the planned question. Only beats
                    # that need to see get one; looks and inspections fetch their own.
                    scene_images = ()
                    context.pop('scene_description', None)
                    context['observations'] = [c for c in context.get('observations', []) if c.get('kind') != 'scene']
                self.trace.emit('agency_step_started',turn_id=turn_id,**agency_step)
            elif self.state and self.agency_planner and not opportunity:
                # The goal Rex chose steers this reply only when it fits; the human comes first.
                speaker = context.get('speaker') or {}
                context['agency_plan'] = self.state.agency.summary(self.trace.clock(),
                    audience_person=memory_subject,
                    recognized=[f.get('identity') for f in context.get('faces', [])] +
                               [speaker.get('person_id') if speaker.get('state') == 'supported' else None])
            snapshot = Snapshot(self.session_id, turn_id, self.revision, self.history_messages(),
                                context.get("speaker",{}).get("person_id") or "unresolved",
                                context, self.state is not None, images=scene_images)
            phrases = Queue(maxsize=4)
            worker = threading.Thread(target=self.output.run, args=(phrases, cancel, turn_id, delivery),
                                      name="r3x-output", daemon=True)
            self.output_worker = worker
            worker.start()
            splitter = Phrases()
            generated = ""
            model_at = self.trace.clock()
            self.trace.emit("model_request", turn_id=turn_id, primary_calls=1,
                            image_count=len(snapshot.images),image_bytes=sum(len(i.data_url) for i in snapshot.images),
                            observation_count=len(context.get("observations",[])),
                            purpose=opportunity.kind if opportunity else "conversation",
                            context_messages=len(snapshot.messages),
                            history_turns=len(self.history.turns),
                            history_characters=self.history.size(snapshot.messages),
                            reasoning_effort=getattr(self.brain,"reasoning_effort",None))
            went_silent, withheld = [False], [False]
            def generate(request):
                nonlocal first_token_at, generated
                calls = []
                stream = self.brain.reply(request, cancel)
                try:
                    for delta in stream:
                        cancel.check()
                        if isinstance(delta, ToolCall):
                            # General intent replies follow ordinary prose once audible.
                            # Questions and memory-bearing tools retain audience guards.
                            update = delta.arguments.get('intent_update') or {}
                            if delta.name == 'ask_face_question':
                                self._question_track = delta.arguments.get('face_track_id')
                            self._audience_bound_output = (memory_accessed[0] or
                                bool(agency_step and agency_step['scope']=='audience') or delta.name in
                                {'ask_face_question', 'use_memory_opportunity', 'use_visual_memory'}
                                or bool(update.get('memory_ids')))
                            calls.append(delta)
                            continue
                        if agency_step and agency_step['kind'] != 'contribute' and not request.continuation:
                            raise ProviderFailure('AgencyStepRequiresActionTool')
                        if first_token_at is None:
                            first_token_at = self.trace.clock()
                            self.trace.emit("model_first_text", turn_id=turn_id)
                        if request.context.get('photo_inspection',{}).get('intent')=='teach':
                            raise ProviderFailure('PhotoTeachingRequiresDecision')
                        if (self.state and self.state.initiative.silent and not went_silent[0]
                                and not opportunity):
                            # Told not to speak until released (October 3: he answered an
                            # "okay" two minutes into such a request). Words are withheld
                            # unless this very turn set the silence or lifted it.
                            if not withheld[0]:
                                withheld[0] = True
                                self.trace.emit('silent_reply_withheld', turn_id=turn_id)
                            continue
                        generated += delta
                        if len(generated) > 4000:
                            raise ProviderFailure("ReplyLengthLimit")
                        for phrase in splitter.add(delta):
                            put_phrase(phrases, phrase, cancel)
                    cancel.check()
                    if calls and generated.strip():raise ProviderFailure("SpeechToolProtocolViolation")
                    return calls
                finally:
                    close = getattr(stream, "close", None)
                    if close:
                        close()
            calls = generate(snapshot)
            if agency_step and len(calls) == 1 and not generated.strip() and self._repeats_recent_line(calls[0]):
                # Hosted probe, October 2: a beat about a no-smiling challenge was delivered as a
                # word-for-word repeat of the previous line. One bounded retry, then the beat fails.
                self.trace.emit('agency_repeat_rejected', turn_id=turn_id, name=calls[0].name)
                from dataclasses import replace as replace_snapshot
                self.trace.emit('model_request',turn_id=turn_id,primary_calls=0,purpose='agency_repeat_retry',
                    reasoning_effort=getattr(self.brain,'reasoning_effort',None))
                calls = generate(replace_snapshot(snapshot, messages=(*snapshot.messages, {'role':'developer','content':
                    'You just said exactly that. Do this beat instead, in new words: '+agency_step['instruction']})))
                if len(calls) == 1 and self._repeats_recent_line(calls[0]):
                    raise ProviderFailure('AgencyBeatRepeatedRecentLine')
            if len(calls)==1 and calls[0].name=='revise_agency_plan' and not generated.strip():
                from .semantics import validate
                from dataclasses import replace
                validate(calls[0].name,calls[0].arguments)
                if not agency_step or agency_step.get('origin')=='recovery':
                    raise ProviderFailure('AgencyStepUnavailable')
                reason=calls[0].arguments['reason']
                with self._lock:
                    cancel.check()
                    self.trace.emit('agency_revision_requested',turn_id=turn_id,reason=reason)
                    agency.finish(agency_step,Delivery(status='suppressed',medium='none'),None,self.trace.clock(),reason)
                    # The goal's premise failed: it ends (a fresh goal is planned next) and
                    # this lull still gets one improvised beat instead of silence.
                    agency.replace('revised')
                    agency.install_recovery_step(bound_audience,agency.revision,self.trace.clock(),capabilities,
                        subject_person=self._subject_person(context))
                    agency_step=agency.step(self.trace.clock())
                    if agency_step is None:
                        raise ProviderFailure('AgencyNoRunnableBeat')
                    self.agency_scope=agency_step['scope']
                context={**context,'agency_step':agency_step,'memory_opportunities':[],
                    'visual_memory':[r for r in context.get('visual_memory',[]) if r.get('audience')=='general']}
                self.trace.emit('agency_step_started',turn_id=turn_id,**agency_step)
                self.trace.emit('model_request',turn_id=turn_id,primary_calls=0,purpose='agency_step_recovery',
                    reasoning_effort=getattr(self.brain,'reasoning_effort',None))
                # The improvised beat is an entertain beat: no camera frame (see goal frame filtering).
                context.pop('scene_description',None)
                recovery=replace(snapshot,context=context,images=(),messages=(*snapshot.messages,{'role':'developer','content':
                    'The previous beat was abandoned and nothing from it was spoken or performed. '
                    'Do the replacement beat now. Do not repeat the abandoned premise or explain the planning correction.'}))
                calls=generate(recovery)
            if not calls and not generated.strip() and withheld[0]:
                delivery.status, delivery.medium = 'suppressed', 'none'   # Silence kept, not a failure.
                self.trace.emit('reply_suppressed', turn_id=turn_id, reason='silent_until_released')
            elif not calls and not generated.strip():
                raise ProviderFailure('EmptyModelResponse')
            if calls and len(calls)==1 and self.state and not generated.strip():
                from .photo_memory import PHOTO_TOOLS
                if calls[0].name in PHOTO_TOOLS:
                    from .photo_flow import run as photo_run
                    reply = photo_run(self,calls[0],snapshot,generate,cancel)
                    if reply:
                        generated += reply
                        for phrase in splitter.add(reply):put_phrase(phrases,phrase,cancel)
                    calls = []
            if calls:
                if not self.state or len(calls) != 1 or generated.strip():
                    raise ProviderFailure("SpeechToolProtocolViolation")
                call = calls[0]
                if call.name == 'revise_agency_plan':
                    raise ProviderFailure('AgencyRevisionRecoveryLimit')
                from .semantics import SCHEDULE_TOOLS
                if call.name in SCHEDULE_TOOLS and call.arguments.get('next_opportunity') is not None:
                    from dataclasses import replace
                    try:
                        with self._lock:
                            schedule_proposal = self.state.initiative.prepare_schedule(call.arguments['next_opportunity'], context)
                    except ValueError as exc:
                        self.trace.emit('reconsideration_rejected', turn_id=turn_id, reason=str(exc))
                        call = replace(call, arguments={**call.arguments, 'next_opportunity': None})
                from .invitations import TOOLS as INVITATION_TOOLS
                if call.name in INVITATION_TOOLS and call.arguments.get('invitation_update') is not None:
                    from dataclasses import replace
                    try:
                        with self._lock:
                            if (isinstance(call.arguments['invitation_update'],dict) and call.arguments['invitation_update'].get('scope')=='general' and
                                (call.name=='use_memory_opportunity' or (call.arguments.get('intent_update') or {}).get('memory_ids') or
                                 (call.arguments.get('intent_update') or {}).get('target_ids'))):
                                raise ValueError('GeneralInvitationCannotUsePrivateMemoryOrTargets')
                            if (isinstance(call.arguments['invitation_update'],dict) and call.arguments['invitation_update'].get('scope')=='general' and
                                call.name=='use_visual_memory' and not any(r['id']==call.arguments.get('observation_id') and
                                    r.get('audience')=='general' for r in context.get('visual_memory',[]))):
                                raise ValueError('GeneralInvitationCannotUsePrivateVisualMemory')
                            invitation_proposal = self.state.initiative.invitations.prepare(
                                call.arguments['invitation_update'],context,call.arguments.get('text'),
                                schedule_proposal,self.trace.clock())
                    except ValueError as exc:
                        self.trace.emit('invitation_rejected',turn_id=turn_id,reason=str(exc))
                        call = replace(call,arguments={**call.arguments,'invitation_update':None})
                from .semantics import greeting_violation
                violation = None if agency_step else greeting_violation(context, call)
                if violation:
                    raise ProviderFailure(violation)
                self.trace.emit("tool_proposed",turn_id=turn_id,call_id=call.call_id,name=call.name,arguments=dict(call.arguments))
                with self._lock:
                    cancel.check()
                    if call.name in {"recall_conversations","remember_episode","retract_episode"}:
                        memory_accessed[0] = True
                        refresh_current_view(context)
                        cancel.check()
                    if call.arguments.get('intent_update') is not None or call.name == 'respond_with_intent':
                        refresh_current_view(context)
                        if intent_audience(context) != request_audience:
                            if (call.name != 'respond_with_intent' or
                                    (call.arguments.get('intent_update') or {}).get('memory_ids')):
                                raise ProviderFailure('IntentAudienceChanged')
                            # Optional planning has no authority to withhold an ordinary reply.
                            # Do not transplant the old audience's intention to the new view.
                            from dataclasses import replace
                            call = replace(call, arguments={**call.arguments, 'intent_update': None})
                            self.trace.emit('conversation_intent_rejected', source_id=turn_id,
                                reason='IntentAudienceChanged', operation='continued_without_intent')
                    try:
                        if call.name == 'describe_view':
                            from .contracts import ToolResult
                            model = getattr(getattr(self.brain,'capability',None),'model',None) or 'scripted-or-unspecified'
                            try:
                                revision = self.state.save_visual(call,context,self.session_id,model,cancel)
                            except ValueError as exc:
                                if str(exc) != 'VisualEvidenceUnavailable': raise
                                result = ToolResult(call.call_id,'rejected','VisualEvidenceUnavailable',self.state.store.revision)
                                self.trace.emit('tool_result',name=call.name,request_id=result.request_id,
                                    outcome=result.outcome,reason=result.reason,revision=result.revision,data={})
                                delivery.status,delivery.medium='suppressed','none'
                                put_phrase(phrases,None,cancel)
                                worker.join(timeout=2)
                                return delivery
                            visual_selected = context['scene_description']['request_id']
                            result = ToolResult(visual_selected,'ok','VisualDescriptionSaved',revision)
                            self.trace.emit('visual_description_saved',turn_id=turn_id,observation_id=visual_selected,model=model)
                        elif call.name == 'inspect_view':
                            inspection_request = self.state.propose_inspection(call,context,cancel)
                            from .contracts import ToolResult
                            result = ToolResult(inspection_request.request_id,'ok','InspectionProposed',self.state.store.revision)
                        elif call.name == 'drive_base':
                            from .contracts import ToolResult
                            try:
                                drive_plan = self.state.propose_drive(call,context,cancel)
                                result = ToolResult(drive_plan.request_id,'ok','DriveProposed',self.state.store.revision)
                            except ValueError as exc:
                                drive_plan = None
                                result = ToolResult(call.call_id,'rejected',str(exc),self.state.store.revision)
                                self.trace.emit('drive_rejected',turn_id=turn_id,reason=str(exc))
                        elif call.name in {'set_robot_pose','set_robot_joints'}:
                            pose = self.state.propose_pose(call,context,cancel)
                            from .contracts import ToolResult
                            result = ToolResult(call.call_id,'ok','PoseProposed',self.state.store.revision)
                        elif call.name == 'animate_lights':
                            if isinstance(call.arguments.get('text'), str) and not call.arguments['text'].strip():
                                call.arguments['text'] = None
                            if call.arguments.get('text') is None or call.arguments.get('pattern') in {'compliment','blink'}:
                                call.arguments['timing'] = 'before_speech'
                            self.trace.emit('light_request_prepared',turn_id=turn_id,arguments=dict(call.arguments))
                            light_request = self.state.propose_lights(call,context,cancel)
                            from .contracts import ToolResult
                            result = ToolResult(light_request.request_id,'ok','LEDProposed',self.state.store.revision)
                        elif call.name == 'respond_with_gesture':
                            gesture, anchor = self.state.propose_performance(call, context, cancel)
                            performance_gesture = gesture
                            from .contracts import ToolResult
                            result = ToolResult(gesture.request_id,'ok','PerformanceProposed',self.state.store.revision)
                        elif call.name == 'shift_attention':
                            attention_intent = self.state.propose_attention(call, context, cancel)
                            from .contracts import ToolResult
                            result = ToolResult(attention_intent.request_id, 'ok', 'AttentionProposed', self.state.store.revision)
                        else:
                            result = self.state.execute_opportunity(call,context,cancel) if opportunity else self.state.execute(call,context["state_revision"],cancel,context=context)
                    except ValueError as exc:
                        if opportunity:
                            raise
                        # October 3: a look aimed at a face id the tool does not take raised an
                        # error, the whole turn failed, and the person's remark got no reply. A
                        # refused proposal goes back to the model, which still answers them.
                        from .contracts import ToolResult
                        result = ToolResult(call.call_id,'rejected',str(exc) or type(exc).__name__,self.state.store.revision)
                        self.trace.emit('tool_refused',turn_id=turn_id,name=call.name,reason=result.reason)
                    if result.outcome!='ok' and opportunity:
                        raise ProviderFailure(result.reason)
                    if (result.outcome=='ok' and call.arguments.get('intent_update') is not None and latest_faces
                            and (call.name != 'respond_with_intent' or call.arguments['intent_update'].get('memory_ids'))):
                        previous_guard = cancel.guard
                        intent_bound = (call.name in {'ask_face_question','use_memory_opportunity'}
                            or bool(call.arguments['intent_update'].get('memory_ids')))
                        intent_rejection_logged = False
                        def intent_output_valid():
                            nonlocal intent_rejection_logged
                            if previous_guard is not None and not previous_guard():return False
                            # General words already playing may finish through a camera gap.
                            # Targeted questions/private memory retain their audience guard.
                            if delivery.first_audio_at is not None and not intent_bound:return True
                            fresh = latest_faces(self.trace.clock())
                            current = {'current_view':{'faces':fresh}, 'speaker':context.get('speaker',{})}
                            valid = intent_audience(current) == request_audience
                            if not valid and not intent_rejection_logged:
                                intent_rejection_logged = True
                                self.trace.emit('intent_output_rejected',turn_id=turn_id,
                                    reason='audience_changed_or_stale',speech_started=delivery.first_audio_at is not None)
                            return valid
                        cancel.guard = intent_output_valid
                    if result.outcome=='ok' and call.name in {'inspect_view','shift_attention','respond_with_gesture','drive_base','set_robot_pose','set_robot_joints'}:
                        from .visual_memory import audience
                        self.state.actions.begin(result.request_id,call.name,call.arguments.get('question') or call.arguments.get('purpose') or
                            (agency_step or {}).get('instruction') or call.arguments.get('text') or str(call.arguments.get('poses') or call.arguments.get('positions')),turn_id,audience(context),self.trace.clock())
                        action_started=True
                        self.trace.emit('action_started',turn_id=turn_id,request_id=result.request_id,kind=call.name)
                if agency_step and result.outcome != 'ok':
                    agency_revision_reason = 'ToolRejected:'+result.reason
                schedule_accepted = result.outcome == 'ok'
                invitation_changed = False
                if invitation_proposal and invitation_proposal.action!='open' and result.outcome=='ok':
                    with self._lock:
                        cancel.check()
                        pending_invitation = self.state.initiative.invitations.pending
                        invitation_audience_valid = True
                        if pending_invitation and pending_invitation['scope']=='audience' and latest_faces:
                            from .conversation_intent import audience
                            invitation_audience_valid = audience({'faces':latest_faces(self.trace.clock()),
                                'speaker':context.get('speaker',{})})==invitation_proposal.audience
                        if invitation_audience_valid:
                            invitation_changed = self.state.initiative.invitations.transition(invitation_proposal,self.trace.clock())
                        else:
                            self.trace.emit('invitation_rejected',turn_id=turn_id,reason='InvitationAudienceChanged')
                        if invitation_changed and schedule_proposal is None:
                            self.state.initiative.cancel_schedule('invitation_closed')
                        invitation_proposal = None
                schedule_guard = cancel.guard
                self.trace.emit("tool_validated" if call.name in {"shift_attention","respond_with_gesture","inspect_view","animate_lights","set_robot_pose","set_robot_joints","drive_base"} else "tool_executed",
                                turn_id=turn_id,request_id=result.request_id,outcome=result.outcome)
                if (result.outcome=='ok' and call.name=='conversation_feedback' and
                    result.data['action']=='floor_resolved' and not self.state.initiative.floor_held(self.trace.clock())):
                    self.proactive_hold_until = float('-inf')
                if result.outcome=='ok' and call.name=='describe_view':
                    selected_revision=result.revision
                    previous_guard=cancel.guard
                    cancel.guard=lambda: (previous_guard is None or previous_guard()) and self.state.store.revision==selected_revision
                    observation_selected = next(c for c in context['observations'] if c['cue_id']==context['scene_description']['cue_id'])
                    previous_valid = cancel.valid
                    cancel.valid = lambda: previous_valid() and observation_ready()
                    if call.arguments['text'] is not None:
                        visual_proposed_text=call.arguments['text']
                        put_phrase(phrases,call.arguments['text'],cancel)
                    else:
                        delivery.status,delivery.medium='suppressed','none'
                elif result.outcome=='ok' and call.name=='inspect_view':
                    from dataclasses import asdict
                    import json
                    expected_revision = result.revision
                    expected_epoch = inspection_request.view_epoch
                    def inspection_valid():
                        with self._lock:
                            return self.state.store.revision==expected_revision and self.state.attention.scene.view_epoch==expected_epoch
                    cancel.guard = inspection_valid
                    data, images = self.output.execute_inspection(inspection_request,cancel,inspection_valid)
                    schedule_action_ok = data.get('status') in {'observed','simulated'} and data.get('stage') == 'complete' and bool(images)
                    self.trace.emit('inspection_result',turn_id=turn_id,**data,image_count=len(images))
                    with self._lock:self.state.actions.observed({**data,'images_acquired':bool(images)})
                    cancel.check()
                    if images and data.get('image_digest'):
                        with self._lock:
                            prior = self.state.last_inspection
                            data['image_comparison'] = ('identical_transmitted_image' if prior and prior.get('image_digest')==data['image_digest'] else 'different_or_first_image')
                            data['compared_request_id'] = prior['request_id'] if prior else None
                            self.state.last_inspection = {key:data[key] for key in ('request_id','image_digest')}
                    data = {**data,'source_turn_id':context.get('source_turn_id'),'event_id':context.get('event_id'),
                            'question':inspection_request.question}
                    inspection_novelty=visual_memory.novelty(context.get('visual_memory',[]),images)
                    inspection_novelty['recent_selections']=context.get('visual_novelty',{}).get('recent_selections',[])
                    cached = next((r for r in context.get('visual_memory',[]) if data.get('image_digest') and
                        r['source'].get('image_digest')==data['image_digest']),None)
                    from .photo_flow import comparison
                    comparison_context,comparison_images=comparison(self.state,images)
                    if comparison_context:
                        self.trace.emit('photo_inspection',turn_id=turn_id,**comparison_context['photo_inspection'],image_count=len(comparison_images))
                    if images and cached and not comparison_context:
                        data['cached_description'] = {key:cached[key] for key in ('id','description','uncertainty','spoken')}
                        data['image_reused_without_upload'] = True
                        images = ()
                        visual_selected = cached['id']
                    result = ToolResult(result.request_id,'ok' if images or cached else 'failed',data.get('reason','InspectionFailed'),result.revision,data)
                    continuation = (*call.preamble,
                        {'type':'function_call','call_id':call.call_id,'name':call.name,'arguments':json.dumps(call.arguments)},
                        {'type':'function_call_output','call_id':call.call_id,'output':json.dumps(asdict(result))})
                    images=comparison_images if comparison_context else images
                    followup = Snapshot(self.session_id,turn_id,self.revision,self.history_messages(),
                        snapshot.speaker,{**{k:v for k,v in context.items() if k!='scene_description'},'attention':self.state.attention.context(),'inspection':data,
                            **comparison_context,'inspection_description_allowed':bool(images),'visual_novelty':inspection_novelty,'action_continuity':self.state.actions.context(visual_memory.audience(context))},bool(images),continuation,images)
                    self.trace.emit('model_request',turn_id=turn_id,primary_calls=0,
                        purpose='inspection_result_continuation',image_count=len(images),image_bytes=sum(len(i.data_url) for i in images))
                    description_calls = generate(followup)
                    if description_calls:
                        if len(description_calls)!=1 or generated.strip() or not images or description_calls[0].name!='describe_view':
                            raise ProviderFailure('ToolContinuationLimit')
                        description_call = description_calls[0]
                        with self._lock:
                            cancel.check()
                            model = getattr(getattr(self.brain,'capability',None),'model',None) or 'scripted-or-unspecified'
                            expected_revision = self.state.save_visual(description_call,followup.context,self.session_id,model,cancel)
                        visual_selected = data['request_id']
                        self.trace.emit('visual_description_saved',turn_id=turn_id,observation_id=visual_selected,model=model)
                        if description_call.arguments['text'] is not None:
                            visual_proposed_text=description_call.arguments['text']
                            put_phrase(phrases,description_call.arguments['text'],cancel)
                        else:
                            delivery.status,delivery.medium='suppressed','none'
                    self.history.append({'role':'developer','content':'Inspection result (see medium and motion status): '+json.dumps(data)})
                elif call.name=='drive_base':
                    import json
                    from dataclasses import asdict
                    from .drive import DriveAction
                    args = call.arguments
                    spoke = False
                    origin = 'self_initiated' if opportunity else 'requested'
                    def speak(text):
                        nonlocal spoke
                        spoke = True
                        delivery.status, delivery.medium = 'requested', 'audio'
                        put_phrase(phrases,text,cancel)
                    def drive_followup(call, output, images=(), inspection=None, replan=False, wordless=False):
                        # One bounded continuation: explain a refusal/incomplete motion,
                        # or answer look_after from a frame captured after motion.
                        continuation = (*call.preamble,
                            {'type':'function_call','call_id':call.call_id,'name':call.name,'arguments':json.dumps(call.arguments)},
                            {'type':'function_call_output','call_id':call.call_id,'output':json.dumps(output)})
                        followup_context = {**{k:v for k,v in context.items() if k!='scene_description'},
                            'drive':self.output.drive.context(origin) if self.output.drive else None}
                        if inspection is not None:
                            followup_context.update(inspection=inspection,inspection_description_allowed=bool(images),
                                                    drive_replan_allowed=replan and bool(images))
                        status = (output.get('drive') or {}).get('status') if isinstance(output, dict) else None
                        messages = self.history_messages()
                        if inspection is not None:
                            # September 27: every arrival became a paragraph about the hallway.
                            messages = (*messages, {'role':'developer','content':
                                'Answer the look question in one short sentence about what matters for the request. '
                                'Describe the scene in detail only if the person asked what you see.'})
                        if wordless:
                            # October 3: "do you have access to your compass?" was answered by a
                            # silent 17 degree turn; the person waited for words that never came.
                            messages = (*messages, {'role':'developer','content':
                                'You moved without a word, right after a person spoke to you. Now answer what they '
                                'actually said in one or two short sentences. Mention the move only if they asked for it.'})
                        elif status != 'completed':
                            # September 27: after a refused plan Rex repeated "I'm on it".
                            messages = (*messages, {'role':'developer','content':
                                'The movement did not happen as planned (receipt status: '+str(status or output.get('outcome'))+
                                '). Say plainly what did or did not happen and why; do not repeat an earlier acknowledgment '
                                'such as "on it", and do not claim you moved or found anything the receipt does not show.'})
                        followup = Snapshot(self.session_id,turn_id,self.revision,messages,snapshot.speaker,
                                            followup_context,bool(images),continuation,images)
                        self.trace.emit('model_request',turn_id=turn_id,primary_calls=0,purpose='drive_result_continuation',
                                        image_count=len(images),reasoning_effort=getattr(self.brain,'reasoning_effort',None))
                        delivery.status, delivery.medium = 'requested', 'audio'
                        return followup, generate(followup)
                    def run_drive(call, drive_plan, result, depth):
                        # depth 1 is the single follow-up plan chosen after looking.
                        nonlocal drive_action, drive_noted, visual_selected, visual_proposed_text
                        args = call.arguments
                        receipt = None
                        if result.outcome=='ok':
                            if args['say_before']:
                                speak(args['say_before'])
                            drive_action = DriveAction(drive_plan)
                            drive_noted = False
                            # Once queued, re-identified faces from the moving camera no longer
                            # invalidate a self-initiated plan; explicit stop still halts it.
                            self.motion_committed = True
                            put_phrase(phrases,drive_action,cancel)
                            while not drive_action.done.wait(.05):
                                cancel.check()
                                if not worker.is_alive():
                                    raise ProviderFailure('OutputWorkerStopped')
                            receipt = asdict(drive_action.receipt)
                            drive_noted = True
                            self.trace.emit('drive_result',turn_id=turn_id,**receipt)
                            with self._lock:
                                self.state.actions.motion_observed({'status':receipt['status'],'stage':'complete','reason':receipt['reason'],
                                                             'medium':receipt['medium'],'motion_status':receipt['status']})
                            self.history.append({'role':'developer','content':
                                'Drive controller receipt (firmware/odometry/IMU, not visual confirmation): '+json.dumps(receipt)})
                            cancel.check()
                        if receipt and receipt['status'] in {'completed','partial'} and args['look_after']:
                            from .inspection import InspectionRequest
                            with self._lock:
                                self.state.attention.scene.invalidate(self.trace.clock())
                                look = InspectionRequest(drive_plan.request_id+':look','current',args['look_after'],
                                    self.state.attention.scene.view_epoch,self.trace.clock(),captured_after=(drive_action.receipt.finished_at or self.trace.clock())+.3)
                            data, images = self.output.execute_inspection(look,cancel,lambda: True)
                            data = {**data,'question':args['look_after'],'source_turn_id':context.get('source_turn_id'),
                                    'event_id':context.get('event_id'),'after_drive':receipt['status']}
                            self.trace.emit('inspection_result',turn_id=turn_id,**data,image_count=len(images))
                            self.history.append({'role':'developer','content':'Inspection after driving: '+json.dumps(data)})
                            followup, look_calls = drive_followup(call,{'drive':receipt,'inspection':data},images,data,replan=depth==0)
                            if look_calls:
                                chosen = look_calls[0]
                                if len(look_calls)!=1 or not images or chosen.name not in {'describe_view','drive_base'}:
                                    raise ProviderFailure('ToolContinuationLimit')
                                if chosen.name=='drive_base':
                                    if depth:
                                        raise ProviderFailure('ToolContinuationLimit')
                                    # Seen something after looking: one more plan from the fresh view.
                                    from .contracts import ToolResult
                                    try:
                                        with self._lock:
                                            next_plan = self.state.propose_drive(chosen,followup.context,cancel)
                                        next_result = ToolResult(next_plan.request_id,'ok','DriveProposed',self.state.store.revision)
                                    except ValueError as exc:
                                        next_plan, next_result = None, ToolResult(chosen.call_id,'rejected',str(exc),self.state.store.revision)
                                        self.trace.emit('drive_rejected',turn_id=turn_id,reason=str(exc))
                                    return run_drive_with(chosen, next_plan, next_result, 1, followup.context['drive'])
                                with self._lock:
                                    cancel.check()
                                    model = getattr(getattr(self.brain,'capability',None),'model',None) or 'scripted-or-unspecified'
                                    self.state.save_visual(chosen,followup.context,self.session_id,model,cancel)
                                visual_selected = data['request_id']
                                if chosen.arguments['text'] is not None:
                                    visual_proposed_text = chosen.arguments['text']
                                    speak(visual_proposed_text)
                        elif receipt and receipt['status']=='completed':
                            if args['say_after']:
                                speak(args['say_after'])
                            elif not opportunity and not spoke and not generated.strip():
                                if drive_followup(call,{'drive':receipt},wordless=True)[1]:
                                    raise ProviderFailure('ToolContinuationLimit')
                        elif not opportunity:
                            # Refused, rejected or incomplete: the model explains from the receipt.
                            output = {'drive':receipt} if receipt else {**asdict(result),'nothing_moved':True,
                                  'meaning':'The plan was rejected before any motion. Rex did not move.'}
                            if drive_followup(call,output)[1]:
                                raise ProviderFailure('ToolContinuationLimit')
                    def run_drive_with(call, plan, result, depth, fresh_context):
                        nonlocal drive_context
                        drive_context = fresh_context
                        return run_drive(call, plan, result, depth)
                    drive_context = context.get('drive')
                    run_drive(call, drive_plan, result, 0)
                    if not spoke and not generated.strip():
                        delivery.status, delivery.medium = 'suppressed', 'none'
                elif result.outcome=='ok' and call.name in {'set_robot_pose','set_robot_joints'}:
                    from dataclasses import asdict
                    cancel.check()
                    if opportunity and not any(call.arguments.get(key) for key in ('text','say_before','look_after')):
                        # Like silent directional attention, a finite body action may
                        # reframe its own audience. Speech/stop/state guards still apply.
                        self.motion_committed = True
                    expected_revision = result.revision
                    previous_guard = cancel.guard
                    cancel.guard = lambda: (previous_guard is None or previous_guard()) and not self.state.frozen and self.state.store.revision==expected_revision
                    # The model chains steps from the request's wording; each runs once
                    # the previous one's pulses arrive, and the first failure stops the rest.
                    from .leds import LIGHT_STEPS, LightPerformance, LightRequest
                    if call.arguments.get('say_before'):
                        # Live September 28: "On it." arrived after a long sequence. Speak the
                        # acknowledgment first, then move once it has played (talking motion
                        # and the requested steps share one servo owner).
                        heard = len(delivery.completed_text)
                        delivery.status, delivery.medium = 'requested', 'audio'
                        put_phrase(phrases,call.arguments['say_before'],cancel)
                        while len(delivery.completed_text) <= heard:
                            cancel.check()
                            if not worker.is_alive() or delivery.error:
                                raise ProviderFailure('OutputWorkerStopped')
                            time.sleep(.02)
                    moved, last_at = True, self.trace.clock()
                    changed_channels = set()
                    for index, step in enumerate(pose if isinstance(pose, tuple) else (pose,)):
                        suffix = f':{index}' if isinstance(pose, tuple) and len(pose) > 1 else ''
                        if isinstance(step, str) and step in LIGHT_STEPS:
                            # Lights run in sequence with the moves; the LED owner reports commands sent.
                            leds = self.output.leds
                            light = LightRequest(turn_id+':'+call.call_id+suffix,LIGHT_STEPS[step],2.5,
                                                 self.trace.clock()+30,leds.version)
                            self.output._light_demo(LightPerformance(light,None,'before_speech',lambda: True),cancel,delivery,turn_id)
                            moved = bool(delivery.lights) and delivery.lights[-1]['status']=='commands_sent'
                        else:
                            receipt = self.output.execute_pose(step,cancel,turn_id+':'+call.call_id+suffix,
                                hold_pose=not bool(opportunity))
                            self.trace.emit('robot_pose_result',turn_id=turn_id,pose=step,**asdict(receipt))
                            self.history.append({'role':'developer','content':'Robot pose controller receipt: '+json.dumps({'pose':step,**asdict(receipt)})})
                            moved, last_at = receipt.status == 'pulses_reached', receipt.at
                            changed_channels.update(receipt.changed_channels)
                        cancel.check()
                        if not moved:
                            break
                    look_question = call.arguments.get('look_after')
                    with self._lock:
                        self.state.actions.motion_observed({'status':'pulses_reached' if moved else 'failed',
                            'stage':'complete','medium':'controller','changed_channels':sorted(changed_channels)})
                    if not moved:
                        if opportunity:
                            delivery.status,delivery.medium='suppressed','none'
                        else:
                            put_phrase(phrases,"I couldn't complete that movement.",cancel)
                    elif call.arguments['text'] is not None:
                        put_phrase(phrases,call.arguments['text'],cancel)
                    elif not look_question and not call.arguments.get('say_before'):
                        delivery.status,delivery.medium='suppressed','none'
                    if moved and look_question:
                        # September 28: "look down and tell me what you see" was answered from
                        # the old view, then a head move was made with nothing said at all.
                        from .inspection import InspectionRequest
                        with self._lock:
                            self.state.attention.scene.invalidate(self.trace.clock())
                            look = InspectionRequest(turn_id+':'+call.call_id+':look','current',look_question,
                                self.state.attention.scene.view_epoch,self.trace.clock(),captured_after=last_at+.3)
                        data, images = self.output.execute_inspection(look,cancel,lambda: True)
                        data = {**data,'question':look_question,'source_turn_id':context.get('source_turn_id'),'after_poses':list(pose)}
                        self.trace.emit('inspection_result',turn_id=turn_id,**data,image_count=len(images))
                        with self._lock:self.state.actions.observed(data)
                        self.history.append({'role':'developer','content':'Inspection after moving: '+json.dumps(data)})
                        continuation = (*call.preamble,
                            {'type':'function_call','call_id':call.call_id,'name':call.name,'arguments':json.dumps(call.arguments)},
                            {'type':'function_call_output','call_id':call.call_id,'output':json.dumps({'poses':'pulses_reached','inspection':data})})
                        messages = (*self.history_messages(), {'role':'developer','content':
                            ('You looked out of your own curiosity, as a beat of your goal. React to what is actually '
                             'in the new frame like a friend would: a specific, funny opinion, or one personable question '
                             'to the humans about it ("what is the story with that?"). Never list or announce what is '
                             'visible. Keep it to one or two short sentences. '
                             if agency_step else
                             'Answer the look question from the new frame in one or two short sentences. ')+
                            'Do not repeat the movement acknowledgment.'})
                        followup = Snapshot(self.session_id,turn_id,self.revision,messages,snapshot.speaker,
                            {**{k:v for k,v in context.items() if k!='scene_description'},'inspection':data,
                             'inspection_description_allowed':bool(images)},bool(images),continuation,images)
                        self.trace.emit('model_request',turn_id=turn_id,primary_calls=0,purpose='pose_look_continuation',
                                        image_count=len(images),reasoning_effort=getattr(self.brain,'reasoning_effort',None))
                        delivery.status, delivery.medium = 'requested', 'audio'
                        look_calls = generate(followup)
                        if look_calls:
                            if len(look_calls)!=1 or not images or look_calls[0].name!='describe_view':
                                raise ProviderFailure('ToolContinuationLimit')
                            with self._lock:
                                cancel.check()
                                model = getattr(getattr(self.brain,'capability',None),'model',None) or 'scripted-or-unspecified'
                                # Saving changes the store revision this pose's guard checks; without
                                # this the comment about what he saw was cancelled unheard (probe, October 2).
                                expected_revision = self.state.save_visual(look_calls[0],followup.context,self.session_id,model,cancel)
                            visual_selected = data['request_id']
                            if look_calls[0].arguments['text'] is not None:
                                visual_proposed_text = look_calls[0].arguments['text']
                                put_phrase(phrases,visual_proposed_text,cancel)
                elif result.outcome=='ok' and call.name=='animate_lights':
                    from .leds import LightPerformance
                    expected_revision = result.revision
                    def valid_lights():
                        with self._lock:
                            return not self.state.frozen and self.state.store.revision==expected_revision
                    put_phrase(phrases,LightPerformance(light_request,call.arguments['text'],
                        call.arguments['timing'],valid_lights),cancel)
                elif result.outcome=='ok' and call.name=='respond_with_gesture':
                    from .performance import Performance
                    expected_revision = result.revision
                    expected_attention = self.state.attention.version
                    def valid_gesture():
                        with self._lock:
                            cancel.check()
                            return (self._resumed_speech<=self._capture_end and
                                    self.state.store.revision==expected_revision and
                                    self.state.attention.version==expected_attention and
                                    (anchor is None or self.state.attention.scene.valid(anchor)))
                    def delivered_gesture():
                        with self._lock:
                            cancel.check()
                            if not valid_gesture():
                                raise ProviderFailure('GestureEvidenceStale')
                            self.state.attention.clear()
                            self.state.attention.scene.invalidate(self.trace.clock())
                    put_phrase(phrases, Performance(call.arguments['text'],gesture,
                        call.arguments['timing'],valid_gesture,delivered_gesture),cancel)
                elif result.outcome=='ok' and call.name=='shift_attention':
                    # Output owns execution; never hold the state lock over an adapter call.
                    cancel.check()
                    if getattr(self.output.attention,'medium','simulation')=='controller':
                        self.motion_committed = True  # Silent directional motion may reframe its own arrival cue.
                    receipt = self.output.execute_attention(attention_intent, cancel)
                    with self._lock:
                        cancel.check()
                        if self.state.store.revision != result.revision:
                            raise ProviderFailure('StaleStateRevision')
                        self.state.attention.complete(attention_intent, receipt)
                    from dataclasses import asdict
                    self.state.actions.observed(asdict(receipt))
                    self.trace.emit('attention_result', turn_id=turn_id, **asdict(receipt),
                                    target=attention_intent.target, purpose=attention_intent.purpose)
                    delivery.status, delivery.medium = 'suppressed', 'none'
                    self.history.append({'role':'developer','content':
                        'Attention outcome: ' + str(asdict(receipt)) + '; target=' + attention_intent.target +
                        '. No speech or new observation. Controller receipts verify output pulses only; simulation never means physical motion.'})
                elif result.outcome=="ok" and (call.name=="remain_silent" or
                    call.name=="conversation_feedback" and result.data['text'] is None):
                    from dataclasses import asdict
                    import json
                    delivery.status, delivery.medium = "suppressed", "none"
                    if call.name=='remain_silent' and result.data['reason']!='no_response_needed':
                        self.proactive_hold_until = self.trace.clock()+120
                        self.state.initiative.cancel_schedule('quiet_or_human_floor')
                    self.history.extend((
                        {"type":"function_call","call_id":call.call_id,"name":call.name,"arguments":json.dumps({**call.arguments, **({"intent_update":None} if "intent_update" in call.arguments else {}), **({"next_opportunity":None} if "next_opportunity" in call.arguments else {})})},
                        {"type":"function_call_output","call_id":call.call_id,"output":json.dumps(asdict(result))}))
                    self.trace.emit("reply_suppressed",turn_id=turn_id,reason=result.data["reason"])
                elif result.outcome=='ok' and call.name=='use_visual_memory':
                    visual_selected = result.data['observation_id']
                    visual_proposed_text=result.data['text']
                    selected_revision=result.revision
                    previous_guard=cancel.guard
                    cancel.guard=lambda: (previous_guard is None or previous_guard()) and self.state.store.revision==selected_revision
                    put_phrase(phrases,result.data['text'],cancel)
                elif result.outcome=='ok' and call.name=='revise_visual':
                    import json
                    self.history.append({'role':'developer','content':'Visual interpretation revision executed: '+json.dumps(result.data)})
                    if result.data['text'] is not None:put_phrase(phrases,result.data['text'],cancel)
                    else:delivery.status,delivery.medium='suppressed','none'
                elif result.outcome=='ok' and call.name=='retract_visual':
                    import json
                    self.history.append({'role':'developer','content':
                        'Visual correction executed: '+json.dumps(result.data)+
                        '. Entire selected descriptions were withdrawn, including other details they contained; no replacement was saved. This is not a failure to see the original image. Do not treat withdrawn interpretations as available visual evidence.'})
                    if result.data['text'] is not None:
                        put_phrase(phrases,result.data['text'],cancel)
                    else:
                        delivery.status,delivery.medium='suppressed','none'
                elif result.outcome=='ok' and call.name=='use_observation':
                    observation_selected = result.data['cue']
                    previous_valid = cancel.valid
                    selected_revision = result.revision
                    cancel.valid = lambda: (previous_valid() and self.state.store.revision==selected_revision
                        and observation_ready())
                    put_phrase(phrases,result.data['text'],cancel)
                elif result.outcome=='ok' and call.name=='use_memory_opportunity':
                    memory_selected = result.data
                    previous_valid = cancel.valid
                    selected_revision = result.revision
                    selected_day = context['calendar_date']
                    cancel.valid = lambda: (previous_valid() and self.state.store.revision==selected_revision
                        and self.state.calendar_today().isoformat()==selected_day)
                    put_phrase(phrases,result.data['text'],cancel)
                elif result.outcome=="ok" and call.name in {"conversation_feedback","respond_with_intent"}:
                    put_phrase(phrases,result.data['text'],cancel)
                elif result.outcome=="ok" and call.name in {"ask_question","ask_face_question"}:
                    question = result.data
                    put_phrase(phrases,question["question"],cancel)
                else:
                    from dataclasses import asdict
                    import json
                    context = self.state.context(faces)
                    refresh_current_view(context)
                    from . import conversation_memory
                    context['personal_memory'] = conversation_memory.context(
                        self.state.store,context,self.session_id,self.state.wall_clock())
                    if not self.state.frozen:
                        with self._lock:self.state.intent_context(context)
                    if call.name=='recall_conversations':
                        # Keep the requested evidence window distinct from automatic
                        # recent memories; the result is already in continuation.
                        memory = context['personal_memory']
                        context['personal_memory'] = {k:memory[k] for k in ('person_id','now','local_now')}
                    quiet_set = call.name=='set_initiative' and result.outcome=='ok' and result.data['mode'] in ('quiet','silent')
                    went_silent[0] = quiet_set and result.data['mode']=='silent'
                    if quiet_set:
                        self.proactive_hold_until = self.trace.clock()+120
                        self.state.initiative.cancel_schedule('quiet_or_human_floor')
                    elif call.name=='set_initiative' and result.outcome=='ok':
                        self.proactive_hold_until = float('-inf')
                    continuation = (*call.preamble,
                        {"type":"function_call","call_id":call.call_id,"name":call.name,
                         "arguments":json.dumps({**call.arguments, **({"intent_update":None} if "intent_update" in call.arguments else {}), **({"next_opportunity":None} if "next_opportunity" in call.arguments else {})})},
                        {"type":"function_call_output","call_id":call.call_id,
                         "output":json.dumps(asdict(result))})
                    # Forgotten information is not sent back in the continuation history.
                    if self.state.forgotten:
                        self.history.clear()
                        self.history.append({"role":"developer","content":"A person was forgotten. Do not reuse previous personal context."})
                    followup = Snapshot(self.session_id,turn_id,self.revision,self.history_messages(),
                                        snapshot.speaker,context,False,continuation)
                    if quiet_set:
                        # October 3: "Well, you have eyes, don't you?" was taken as a request for
                        # quiet and executed without a word; the owner waited 69 seconds, then
                        # asked whether Rex was going to say something. Quiet is now always heard.
                        from dataclasses import replace
                        followup=replace(followup,messages=(*followup.messages,{'role':'developer','content':
                            ('You have just gone silent: you will say nothing at all, not even answers, until they '
                             'release you. Say so in one short sentence, in character, naming what will release you.'
                             if went_silent[0] else
                             'You have just switched yourself to quiet: you will not start conversations until '
                             'they invite you to. Tell them so in one short sentence, in character.') +
                            ' If their words were not really a request to stop talking, say you may have misread '
                            'them and that they only need to say you can talk again.'}))
                    if result.outcome!='ok' and call.name=='respond_with_intent':
                        from dataclasses import replace
                        followup=replace(followup,messages=(*followup.messages,{'role':'developer','content':
                            'The optional conversation note was rejected. Its draft text was NOT spoken. '
                            'Answer the human normally now in plain prose. Do not repeat a promise of future '
                            'work, claim a save or action succeeded, or use unsupported facts from the rejected note.'}))
                    self.trace.emit("model_request",turn_id=turn_id,primary_calls=0,purpose="tool_result_continuation",
                                    reasoning_effort=getattr(self.brain,"reasoning_effort",None))
                    if generate(followup):
                        raise ProviderFailure("ToolContinuationLimit")
                    if not generated.strip():
                        # Recover an empty tool-result answer once, without re-executing
                        # the tool or adding another call to ordinary streamed replies.
                        from dataclasses import replace
                        recovery = replace(followup, messages=(*followup.messages, {
                            'role':'developer', 'content':
                            'Your tool-result response contained no final speech. The operation has already '
                            'finished; do not repeat it. Give a brief final_answer to the human now using '
                            'the actual result. A rejected memory write does not prevent discussing what '
                            'they said. Never claim an unsuccessful save succeeded.'}))
                        self.trace.emit('model_request', turn_id=turn_id, primary_calls=0,
                            purpose='empty_tool_reply_recovery')
                        if generate(recovery):
                            raise ProviderFailure('ToolContinuationLimit')
                        if not generated.strip():
                            raise ProviderFailure('EmptyToolContinuation')
            self.trace.emit("model_completed", turn_id=turn_id, original_text=generated)
            tail = splitter.finish()
            if tail:
                put_phrase(phrases, tail, cancel)
            put_phrase(phrases, None, cancel)
            worker.join(timeout=5 * (40 + 30) + 5)  # Five queued/active phrases: audio plus transport bounds.
            if worker.is_alive():
                raise ProviderFailure("OutputWorkerTimeout")
        except Cancelled:
            if delivery.status != "failed":
                delivery.status = "interrupted"
            if not opportunity:
                self.trace.emit('reply_cancelled', turn_id=turn_id, reason=cancel_reason[0] or (
                    'newer_speech_arrived' if self._resumed_speech > self._capture_end else
                    'stopped' if self.revision != submitted_revision or self.closed else 'cancelled'),
                    audible=delivery.first_audio_at is not None)
        except Exception as exc:
            delivery.status = "failed"
            delivery.error = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
            model_error = delivery.error
            self.trace.emit("turn_failed", turn_id=turn_id, reason=delivery.error)
            cancel.event.set()
        finally:
            if drive_action is not None and not drive_noted:
                drive_action.done.wait(5.)
                if drive_action.receipt is not None:
                    import json
                    from dataclasses import asdict
                    self.history.append({'role':'developer','content':
                        'Drive controller receipt (turn interrupted): '+json.dumps(asdict(drive_action.receipt))})
            if attention_intent:
                with self._lock:
                    if self.state.attention.pending == attention_intent:
                        self.state.attention.clear()
                        self.trace.emit('attention_aborted', turn_id=turn_id, request_id=attention_intent.request_id)
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
            if self.state and state_started and not self.state.frozen and not self.state.forgotten:
                try:
                    from .conversation_memory import record
                    receipt = record(self.state.store, turn_id, self.session_id, archive_context, capture, transcript,
                                     delivery, self.state.wall_clock())
                    self.trace.emit('conversation_memory_record', turn_id=turn_id, **receipt)
                except Exception as exc:
                    self.trace.emit('conversation_memory_failed', turn_id=turn_id, error_type=type(exc).__name__)
            if not opportunity and delivery.status != 'rejected':
                if delivery.completed_text or delivery.status in {'completed','suppressed'}:
                    self._owed_reply = False
                elif transcript.trusted and delivery.status == "interrupted" and delivery.first_audio_at is None:
                    self._owed_reply = True
            if not opportunity and delivery.status == "interrupted" and delivery.first_audio_at is None and self._resumed_speech > source_end:
                self.history.append({"role":"developer","content":
                    "Rex yielded without speaking because microphone speech resumed after source turn " + turn_id +
                    ". The preceding user words remain part of the conversation; the next fragment may continue them. "
                    "This timing event does not establish that the voices are the same person."})
            if self.state and state_started and (opportunity or not self.state.forgotten):
                try:
                    with self._lock:
                        self.state.conversation_intent.delivered(
                            opportunity.event_id if opportunity else turn_id,turn_id,delivery)
                        from .output import plain_text
                        def fully_heard(text):
                            return (delivery.medium=='audio' and bool(delivery.completed_text) and
                                    plain_text(text).split()==' '.join(delivery.completed_text).split())
                        if visual_selected and delivery.medium=='audio' and delivery.completed_text:
                            from .visual_memory import delivered, audience as visual_audience
                            delivered(self.state.store,visual_selected,turn_id,' '.join(delivery.completed_text),self.state.wall_clock(),
                                      visual_audience(context),
                                      general=call.name=='describe_view' and call.arguments.get('description_scope')=='general')
                        if observation_selected and fully_heard(call.arguments['text']):
                            self.state.observation_delivered(observation_selected)
                            self.trace.emit('observation_delivered',turn_id=turn_id,cue_id=observation_selected['cue_id'],kind=observation_selected['kind'])
                        if memory_selected and fully_heard(memory_selected['text']):
                            self.state.memory_delivered(memory_selected)
                        if question and fully_heard(question['question']):
                            self.state.question_delivered(question["question"],question["addressee_id"],delivery.completed_at or self.trace.clock(),question.get("face_track_id"))
                except Exception as exc:
                    self.trace.emit("state_finalize_failed",turn_id=turn_id,error_type=type(exc).__name__)
            if performance_gesture and not any(r['request_id']==performance_gesture.request_id for r in delivery.gestures):
                row = {'request_id':performance_gesture.request_id,'status':'cancelled','at':self.trace.clock(),
                       'medium':getattr(self.output.gestures,'medium','simulation'),'reason':'OutputNotReached','primitive':performance_gesture.primitive,
                       'target':performance_gesture.target,'intensity':performance_gesture.intensity,
                       'timing':call.arguments['timing']}
                delivery.gestures.append(row)
                self.trace.emit('gesture_delivery',turn_id=turn_id,**row)
            if light_request and not delivery.lights:
                delivery.lights.append(dict(request_id=light_request.request_id,status='cancelled',
                    reason='OutputNotReached',medium='serial',evidence='commands_only_display_unverified'))
            if delivery.lights:
                import json
                self.history.append({'role':'developer','content':'LED command outcomes (visible display unverified): '+json.dumps(delivery.lights)})
            if self.state and delivery.gestures:
                self.state.performances.extend(delivery.gestures)
                import json
                self.history.append({'role':'developer','content':'Gesture outcomes (receipt medium applies; separate from heard speech; pulses do not verify mechanical movement): '+json.dumps(delivery.gestures)})
            if self.state and not self.state.forgotten:
                from .visual_memory import audience
                with self._lock:
                    if visual_selected:
                        self.state.visual_selections.append({'observation_id':visual_selected,'turn_id':turn_id,
                            'audience':audience(context),'interpretation_revision':next((r['source'].get('interpretation_revision',0) for r in context.get('visual_memory',[]) if r['id']==visual_selected),0),
                            'selected_text':visual_proposed_text or generated[:1200],
                            'heard_text':' '.join(delivery.completed_text) if delivery.medium=='audio' else '',
                            'delivery_status':delivery.status,'at':self.trace.clock()})
            if self.state and action_started:
                with self._lock:
                    outcome=self.state.actions.finish(delivery,self.trace.clock())
                    if outcome:
                        action_outcome = outcome
                        self.trace.emit('action_finished',turn_id=turn_id,**outcome)
            with self._lock:
                self._cancel = None
                self._delivery = None
                self._opportunity_guard = None
                self._proactive_delivery = None
                self._audience_bound_output = False
                self._memory_subject = None
                self._question_track = None
                self.motion_committed = False
                self.preparing_plan = False
                self.agency_scope = None
            audio_at = delivery.first_audio_at
            self.trace.emit("turn_done", turn_id=turn_id, status=delivery.status, medium=delivery.medium, timings={
                "endpoint": None if opportunity else source_end - speech_end,
                "context": model_at - began if model_at is not None else None,
                "model_first_text": first_token_at - model_at if first_token_at is not None else None,
                "end_of_speech_to_first_audio": audio_at - speech_end if audio_at is not None and not opportunity else None,
                "opportunity_to_first_audio": audio_at - source_end if audio_at is not None and opportunity else None,
                "total": self.trace.clock() - began,
            })
            if state_started and (opportunity or transcript.trusted or delivery.completed_text):
                self.last_interaction_at = self.trace.clock()
                if not opportunity and delivery.status=='completed': self.proactive_hold_until = float('-inf')
            if delivery.medium == 'audio' and delivery.first_audio_at is not None:
                self.last_spoken_reply = {'turn_id':turn_id,'started_at':delivery.first_audio_at,
                                          'finished_at':self.trace.clock()}
            if self.state and (schedule_proposal is not None or invitation_proposal is not None):
                with self._lock:
                    valid = (schedule_accepted and schedule_action_ok and not self.closed and not cancel.event.is_set()
                             and submitted_revision == self.revision and not self.state.forgotten)
                    if valid and schedule_guard:
                        valid = schedule_guard()
                    invitation_valid = valid and not self.state.initiative.quiet and not self.state.initiative.floor_held(self.trace.clock())
                    if invitation_proposal and invitation_proposal.action=='open':
                        invitation_valid = invitation_valid and schedule_proposal is not None and schedule_proposal.revision==self.state.initiative.schedule_revision
                    if invitation_valid and invitation_proposal and latest_faces and (invitation_proposal.scope=='audience' or
                        invitation_proposal.action!='open' and self.state.initiative.invitations.pending and
                        self.state.initiative.invitations.pending['scope']=='audience'):
                        from .conversation_intent import audience
                        invitation_valid = audience({'faces':latest_faces(self.trace.clock()),'speaker':context.get('speaker',{})})==invitation_proposal.audience
                    changed = self.state.initiative.invitations.finish(invitation_proposal,delivery,self.trace.clock(),valid=invitation_valid)
                    if changed and invitation_proposal.action!='open' and schedule_proposal is None:
                        self.state.initiative.cancel_schedule('invitation_closed')
            if agency_step and self.state:
                with self._lock:
                    self.state.agency.finish(agency_step,delivery,action_outcome,self.trace.clock(),agency_revision_reason)
            if opportunity_attempted:
                outcome = self.state.initiative.finished(opportunity,delivery,self.trace.clock())
                self.trace.emit('proactive_outcome',**outcome)
            self._processing.release()
        return delivery
