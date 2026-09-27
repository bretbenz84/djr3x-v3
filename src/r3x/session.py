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
        self._lock = threading.RLock()
        self._processing = threading.Lock()
        self._cancel: Cancellation | None = None
        self._opportunity_guard = None
        self._proactive_delivery = None
        self._audience_bound_output = False
        self.closed = False
        self.output_worker = None
        self.state = state
        if state is not None:
            state.attention.scene.session_id = session_id
            state.conversation_intent.clear("new_session")
        self._latest_speech = float("-inf")
        self._capture_end = float("inf")
        self.proactive_hold_until = float("-inf")
        self.last_interaction_at = float("-inf")

    def cancel(self) -> None:
        with self._lock:
            self.proactive_hold_until = self.trace.clock()+120
            self.revision += 1
            if self._cancel:
                self._cancel.event.set()
            if self.state:
                self.state.initiative.lull_retry_until = float('-inf')
                self.state.conversation_intent.suspend("explicit_stop")
                self.state.cancel()
        self.output.cancel()
        self.trace.emit("cancel", revision=self.revision)

    def note_speech(self, ended_at: float) -> None:
        """Yield an unheard reply to resumed, non-echo microphone speech.

        This is not an explicit stop: keep revision, pending questions, and the
        original user history so the continuation can be understood normally.
        """
        with self._lock:
            if self.state and ended_at > self._latest_speech:
                self.state.initiative.lull_retry_until = float('-inf')
            self._latest_speech = max(self._latest_speech, ended_at)
            if self._cancel and ended_at > self._capture_end and not self._cancel.event.is_set():
                self._cancel.event.set()
                self.trace.emit("reply_yielded", reason="user_resumed_speaking")

    def check_opportunity(self):
        cancel_output = False
        with self._lock:
            if self._opportunity_guard and self._cancel and not self._cancel.event.is_set() and not self._opportunity_guard():
                self._cancel.event.set()
                cancel_output = True
        if cancel_output: self.output.cancel()

    def close(self) -> None:
        self.closed = True
        self.cancel()
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

    def process_opportunity(self, event, submitted_revision, still_valid):
        return self._process(None,None,submitted_revision,faces=event.faces,opportunity=event,still_valid=still_valid)

    def _process(self, capture, transcript, submitted_revision, observation=None, faces=(), opportunity=None, still_valid=None, latest_faces=None):
        if not self._processing.acquire(blocking=False):
            raise RuntimeError("Only one conversation owner may process turns")
        worker = None
        delivery = Delivery()
        turn_id = uuid4().hex
        began = self.trace.clock()
        from .proactive import OpportunityCancellation
        cancel = OpportunityCancellation(still_valid) if opportunity else Cancellation()
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
        try:
            with self._lock:
                if self.closed or source_session != self.session_id or submitted_revision != self.revision:
                    self.trace.emit("turn_rejected", capture_id=source_id, reason="stale")
                    delivery.status = "rejected"
                    return delivery
                self._cancel = cancel
                self._opportunity_guard = still_valid if opportunity else None
                self._proactive_delivery = delivery if opportunity else None
                self._audience_bound_output = False
                self._capture_end = opportunity.speech_marker if opportunity else float("inf")
                if opportunity and self.state and self.state.initiative.floor_held(self.trace.clock()):
                    cancel.check()
                    delivery.status,delivery.medium = 'suppressed','none'
                    self.trace.emit('proactive_deferred',event_id=opportunity.event_id,reason='human_floor_held')
                    return delivery
                if opportunity and self.state:
                    self.state.initiative.attempted(opportunity)
                    opportunity_attempted = True
                if opportunity and self._latest_speech!=opportunity.speech_marker: cancel.event.set()
                cancel.check()
            if opportunity:
                self.trace.emit('proactive_turn',turn_id=turn_id,event_id=opportunity.event_id,kind=opportunity.kind,
                    face_track_id=opportunity.target_track,person_id=opportunity.person_id,revision=self.revision)
            else:
                self.trace.emit("turn", turn_id=turn_id, capture_id=capture.capture_id, parent_capture_id=capture.parent_capture_id,
                            original_text=transcript.text, avg_logprob=transcript.avg_logprob,
                            trusted=transcript.trusted, speaker="unresolved", revision=self.revision)
            if not opportunity and (capture.echo_risk or not transcript.text.strip()):
                delivery.status = "rejected"
                self.trace.emit("turn_rejected", turn_id=turn_id,
                                reason="echo_risk" if capture.echo_risk else "empty_transcript")
                return delivery
            # Syntax-only emergency control. No names/facts are parsed in Python.
            if not opportunity and transcript.trusted and transcript.text.strip().lower().rstrip(".!?") in {"stop", "cancel", "stop rex", "rex stop"}:
                self.cancel()
                delivery.status = "interrupted"
                return delivery
            self.history.begin_turn()
            context = {}
            if self.state:
                with self._lock:
                    cancel.check()
                    if opportunity:
                        if self.state.initiative.quiet:
                            raise ProviderFailure('InitiativeQuietRequested')
                        context = self.state.opportunity_context(opportunity)
                    else:
                        observation = observation or VoiceObservation(self.state.tracker.namespace)
                        attribution = self.state.begin(turn_id,capture,transcript,observation,faces)
                        context = self.state.context(faces)
                    state_started = True
                if not opportunity:
                    self.trace.emit("speaker_evidence",turn_id=turn_id,**context["speaker"],
                                    namespace=observation.namespace,window_consistency=observation.consistency)
            if self.brain is None:
                delivery.status = "observed"  # Frozen acoustic replay through the same state path.
                delivery.medium = "none"
                return delivery
            if opportunity:
                import json
                evidence = {"kind":opportunity.kind,"event_id":opportunity.event_id,
                            "quiet_seconds":opportunity.quiet_seconds,
                            "source_turn_id":opportunity.source_turn_id,
                            "faces":context['faces']}
                if opportunity.kind=='visual_arrival': evidence['arrival']=context['visual_arrival']
                self.history.append({'role':'developer','content':
                    ('Startup greeting: a stable person is visible and no human has spoken in this run. '
                     'Say a brief hello now; choose natural wording and optionally invite an unknown person to share their name. '
                     if context.get('visual_arrival',{}).get('startup_greeting') else
                    'Proactive opportunity; nobody spoke. The previous exchange was already processed. '
                    'There is no new human reply; do not answer the earlier utterance again. '
                    'Choose whether a new contribution is welcome or use remain_silent. ')+'Observation: '+json.dumps(evidence)})
            elif self.state:
                import json
                self.history.append({"role":"developer","content":"Evidence for the immediately following utterance: "+json.dumps({
                    "speaker":context["speaker"],"source_turn_id":turn_id,
                    "speech_ended_at":capture.speech_ended_at,"transcript_trusted":transcript.trusted,
                    "faces":context["faces"]},ensure_ascii=False)})
            if not opportunity: self.history.append({"role": "user", "content": transcript.text})
            # A newer capture can arrive while the previous ASR result waits in
            # the queue. Retain its source/history, but do not answer it belatedly.
            with self._lock:
                self._capture_end = opportunity.speech_marker if opportunity else source_end
                if self._latest_speech > self._capture_end:
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
                    context['attention'] = self.state.attention.context()
            if self.state and not self.state.frozen and getattr(self.output,'leds',None):
                from .leds import PATTERNS
                led = self.output.leds
                context.update(led_enabled=led.state=='ready',led_version=led.version,
                    led_patterns=[p for p in PATTERNS if (p!='compliment' or 'chest' in led.ports) and (p!='blink' or 'head' in led.ports)])
            context['robot_pose_enabled'] = bool(self.state and not self.state.frozen and not opportunity and getattr(self.output,'robot',None) and self.output.robot.owner.state=='active')
            if context['robot_pose_enabled']:
                held = set(getattr(self.output.robot.owner,'held_channels',()))
                context['robot_pose_state'] = {'held_parts':
                    [name for name,channels in (('neck',{0}),('head_lift',{1}),('head_tilt',{2}),('visor',{3}),('hero_elbow',{4}),('hero_wrist',{5}),('poker_arm',{6}),('hero_arm',{7}),('throttle_arm',{8,9,10})) if held.intersection(channels)]}
            scene_images = opportunity.scene_images if opportunity and any(c['kind']=='scene' for c in context.get('observations',[])) else ()
            if self.state and not self.state.frozen:
                from . import visual_memory
                context['visual_memory'] = visual_memory.context(self.state.store,self.session_id,visual_memory.audience(context))
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
                        'cue_id':opportunity.scene_id, 'question':None}
                else:
                    scene_images = ()
            if self.state and not self.state.frozen:
                context['visual_novelty'] = visual_memory.novelty(context['visual_memory'],scene_images)
                context['visual_novelty']['recent_selections'] = [r for r in self.state.visual_selections if r['audience']==visual_memory.audience(context)]
                context['action_continuity'] = self.state.actions.context(visual_memory.audience(context))
            refresh_current_view(context)
            if self.state and not self.state.frozen:
                with self._lock:
                    self.state.intent_context(context, human=not opportunity)
                from .photo_flow import context as photo_context
                context.update(photo_context(self.state))
            from .conversation_intent import audience as intent_audience
            request_audience = intent_audience(context)
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
                            self._audience_bound_output = (delta.name != 'respond_with_intent'
                                or bool(update.get('memory_ids')))
                            calls.append(delta)
                            continue
                        if first_token_at is None:
                            first_token_at = self.trace.clock()
                            self.trace.emit("model_first_text", turn_id=turn_id)
                        if request.context.get('photo_inspection',{}).get('intent')=='teach':
                            raise ProviderFailure('PhotoTeachingRequiresDecision')
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
            if not calls and not generated.strip():
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
                if context.get('visual_arrival',{}).get('startup_greeting') and call.name not in {'ask_face_question','respond_with_intent','use_memory_opportunity'}:
                    raise ProviderFailure('StartupGreetingRequiresSpeech')
                self.trace.emit("tool_proposed",turn_id=turn_id,call_id=call.call_id,name=call.name,arguments=dict(call.arguments))
                with self._lock:
                    cancel.check()
                    if call.arguments.get('intent_update') is not None or call.name == 'respond_with_intent':
                        refresh_current_view(context)
                        if intent_audience(context) != request_audience:
                            raise ProviderFailure('IntentAudienceChanged')
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
                    if result.outcome!='ok' and (opportunity or call.arguments.get('intent_update') is not None or call.name=='respond_with_intent'):
                        raise ProviderFailure(result.reason)
                    if result.outcome=='ok' and call.arguments.get('intent_update') is not None and latest_faces:
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
                    if result.outcome=='ok' and call.name in {'inspect_view','shift_attention','respond_with_gesture'}:
                        from .visual_memory import audience
                        self.state.actions.begin(result.request_id,call.name,call.arguments.get('question') or call.arguments.get('purpose') or call.arguments.get('text'),turn_id,audience(context),self.trace.clock())
                        action_started=True
                        self.trace.emit('action_started',turn_id=turn_id,request_id=result.request_id,kind=call.name)
                self.trace.emit("tool_validated" if call.name in {"shift_attention","respond_with_gesture","inspect_view","animate_lights","set_robot_pose","set_robot_joints"} else "tool_executed",
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
                    self.trace.emit('inspection_result',turn_id=turn_id,**data,image_count=len(images))
                    with self._lock:self.state.actions.observed(data)
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
                elif result.outcome=='ok' and call.name in {'set_robot_pose','set_robot_joints'}:
                    from dataclasses import asdict
                    expected_revision = result.revision
                    previous_guard = cancel.guard
                    cancel.guard = lambda: (previous_guard is None or previous_guard()) and not self.state.frozen and self.state.store.revision==expected_revision
                    receipt = self.output.execute_pose(pose,cancel,turn_id+':'+call.call_id)
                    self.trace.emit('robot_pose_result',turn_id=turn_id,pose=pose,**asdict(receipt))
                    self.history.append({'role':'developer','content':'Robot pose controller receipt: '+json.dumps({'pose':pose,**asdict(receipt)})})
                    cancel.check()
                    if receipt.status != 'pulses_reached':
                        put_phrase(phrases,"I couldn't complete that movement.",cancel)
                    elif call.arguments['text'] is not None:
                        put_phrase(phrases,call.arguments['text'],cancel)
                    else:
                        delivery.status,delivery.medium='suppressed','none'
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
                            return (self.state.store.revision==expected_revision and
                                    self.state.attention.version==expected_attention and
                                    (anchor is None or self.state.attention.scene.valid(anchor)))
                    def delivered_gesture():
                        with self._lock:
                            cancel.check()
                            if (self.state.store.revision!=expected_revision or self.state.attention.version!=expected_attention or
                                (anchor is not None and not self.state.attention.scene.valid(anchor))):
                                raise ProviderFailure('GestureEvidenceStale')
                            self.state.attention.clear()
                            self.state.attention.scene.invalidate(self.trace.clock())
                    put_phrase(phrases, Performance(call.arguments['text'],gesture,
                        call.arguments['timing'],valid_gesture,delivered_gesture),cancel)
                elif result.outcome=='ok' and call.name=='shift_attention':
                    # Output owns execution; never hold the state lock over an adapter call.
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
                        'Attention shift simulated only: ' + attention_intent.target +
                        '. No physical movement, speech, or new observation occurred.'})
                elif result.outcome=="ok" and (call.name=="remain_silent" or
                    call.name=="set_initiative" and result.data['mode']=='quiet' or
                    call.name=="conversation_feedback" and result.data['text'] is None):
                    from dataclasses import asdict
                    import json
                    delivery.status, delivery.medium = "suppressed", "none"
                    if (call.name=='set_initiative' or
                        call.name=='remain_silent' and result.data['reason']!='no_response_needed'):
                        self.proactive_hold_until = self.trace.clock()+120
                    self.history.extend((
                        {"type":"function_call","call_id":call.call_id,"name":call.name,"arguments":json.dumps({**call.arguments, **({"intent_update":None} if "intent_update" in call.arguments else {})})},
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
                    if not self.state.frozen:
                        with self._lock:self.state.intent_context(context)
                    if call.name=='set_initiative' and result.outcome=='ok':
                        self.proactive_hold_until = float('-inf')
                    continuation = (*call.preamble,
                        {"type":"function_call","call_id":call.call_id,"name":call.name,
                         "arguments":json.dumps({**call.arguments, **({"intent_update":None} if "intent_update" in call.arguments else {})})},
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
            worker.join(timeout=5 * (40 + 30) + 5)  # Five queued/active phrases: audio plus transport bounds.
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
            if not opportunity and delivery.status == "interrupted" and delivery.first_audio_at is None and self._latest_speech > source_end:
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
                            from .visual_memory import delivered
                            delivered(self.state.store,visual_selected,turn_id,' '.join(delivery.completed_text),self.state.wall_clock())
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
                       'medium':'simulation','reason':'OutputNotReached','primitive':performance_gesture.primitive,
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
                self.history.append({'role':'developer','content':'Gesture outcomes (simulation only, separate from heard speech): '+json.dumps(delivery.gestures)})
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
                        self.trace.emit('action_finished',turn_id=turn_id,**outcome)
            with self._lock:
                self._cancel = None
                self._opportunity_guard = None
                self._proactive_delivery = None
                self._audience_bound_output = False
            audio_at = delivery.first_audio_at
            self.trace.emit("turn_done", turn_id=turn_id, status=delivery.status, medium=delivery.medium, timings={
                "endpoint": None if opportunity else source_end - speech_end,
                "context": model_at - began if model_at is not None else None,
                "model_first_text": first_token_at - model_at if first_token_at is not None else None,
                "end_of_speech_to_first_audio": audio_at - speech_end if audio_at is not None and not opportunity else None,
                "opportunity_to_first_audio": audio_at - source_end if audio_at is not None and opportunity else None,
                "total": self.trace.clock() - began,
            })
            if state_started:
                self.last_interaction_at = self.trace.clock()
                if not opportunity and delivery.status=='completed': self.proactive_hold_until = float('-inf')
            if opportunity_attempted:
                outcome = self.state.initiative.finished(opportunity,delivery,self.trace.clock())
                self.trace.emit('proactive_outcome',**outcome)
            self._processing.release()
        return delivery
