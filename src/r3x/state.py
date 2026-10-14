"""One validator/executor for meaning proposals and all persistent transitions."""
from dataclasses import asdict, replace
import hashlib
import json
import time
from collections import deque
from .speakers import Track, cosine, unit
from uuid import uuid4

from .contracts import ToolResult
from .enrollment import Enrollment
from .memory import label
from .semantics import validate


# Saved-voice growth from recognized speech (owner, October 13: evidence builds over time).
VOICE_GROWTH_SECONDS = 2.     # V2's per-sample enrollment minimum.
VOICE_GROWTH_SCORE = .55      # The strong track-update level, above the .50 naming level.
VOICE_GROWTH_MARGIN = .10     # Lead over the next saved voice.
VOICE_GROWTH_PER_SESSION = 3
VOICE_GROWTH_DUPLICATE = .97  # Closer than this to a saved clip adds nothing new.


class CoreState:
    def __init__(self, store, tracker, trace, frozen=False, face_invalidator=None, require_microphone_hold=False):
        self.store, self.tracker, self.trace = store, tracker, trace
        self.enrollments = {}
        self.evidence = {}
        self._restored = False
        self.pending = None
        self.current = None
        self.conversation_source = None  # Last trusted input, separate from archived noise.
        self.forgotten = False
        self.audience_seen = None  # (person_id, at): last evidenced private audience this run.
        self.company_at = float('-inf')  # When more than one person was last in evidence.
        self.frozen = frozen
        self.require_microphone_hold = require_microphone_hold
        self.voice_grown = {}   # person_id -> clips added to their saved voice this run.
        from datetime import date
        self.calendar_today = date.today
        self.memory_cursor = 0
        self.last_inspection = None
        from .actions import Actions
        self.actions = Actions()
        self.visual_selections = deque(maxlen=16)
        self.performances = deque(maxlen=8)
        from .attention import AttentionState
        self.attention = AttentionState(trace.clock)
        self.observations = None
        self.scene_observer = None
        self.objects = None
        self.places = None
        self.photo_memory = None
        self.wall_clock = time.time
        self.faces = ()
        self.face_associations = {}
        self.face_learning = {}
        self.face_invalidator = face_invalidator
        self.min_face_epoch = 0
        from .proactive import Initiative
        self.initiative = Initiative(trace)
        from .agency import AgencyPlan
        self.agency = AgencyPlan(store, trace)
        self.initiative.agency = self.agency
        from .conversation_intent import ConversationIntent
        self.conversation_intent = ConversationIntent(trace, self.initiative.cancel_schedule)
        from .impersonation import Impressions
        self.impressions = Impressions()
        self.voice_references = None   # ReferenceStore when a voice cloner is composed.

    def apply_photo(self,call,context,cancel):
        cancel.check()
        try:
            if self.frozen or self.photo_memory is None or self.current is None or context.get('trigger'):
                raise ValueError('PhotoTeachingNeedsHumanTurn')
            if context['state_revision']!=self.store.revision or context['photo_memory']['revision']!=self.photo_memory.data['revision']:
                raise ValueError('StalePhotoRevision')
            if call.name=='remember_object' and call.arguments['evidence_id']!=context.get('photo_inspection',{}).get('evidence_id'):
                raise ValueError('PhotoEvidenceNotOffered')
            if call.name=='remember_object' and call.arguments.get('kind')=='pet':
                pets=[r for r in context['photo_memory'].get('objects',[]) if r.get('label') in {'dog','cat'}]
                if len(pets)>1 and not context.get('photo_inspection',{}).get('target',{}).get('target_id'):
                    raise ValueError('PhotoTeachingNeedsSinglePetCrop')
            turn,capture,transcript,voice,attribution=self.current
            if not transcript.trusted or capture.echo_risk:raise ValueError('UntrustedPhotoTeaching')
            source={'turn_id':turn,'session_id':capture.session_id,'text':transcript.text,
                    'speaker_id':attribution.person_id,'speaker_track':attribution.track_id,
                    'attribution_state':attribution.state,'wall_time':self.wall_clock()}
            result=self.photo_memory.apply(call,source,self.trace.clock())
            return {'outcome':'ok','data':result}
        except (ValueError,OSError) as exc:
            return {'outcome':'rejected','reason':str(exc)}

    def save_visual(self, call, context, session_id, model, cancel):
        from . import visual_memory
        cancel.check()
        validate(call.name,call.arguments)
        if call.name!='describe_view' or not (context.get('inspection_description_allowed') or context.get('scene_description')) or self.frozen:
            raise ValueError('VisualDescriptionUnavailable')
        if self.store.revision!=context['state_revision']:
            raise ValueError('StaleStateRevision')
        source = context['inspection'] if context.get('inspection_description_allowed') else context['scene_description']
        if context.get('scene_description') and not context.get('inspection_description_allowed'):
            cue = next((c for c in context.get('observations',[]) if c['kind']=='scene' and c['cue_id']==source['cue_id']),None)
            if cue is None or not self.observation_available(cue) or not 0<=self.trace.clock()-source['captured_at']<=15:
                raise ValueError('VisualEvidenceUnavailable')
        return visual_memory.save(self.store,session_id,visual_memory.audience(context),source,
                                  call.arguments,model,self.wall_clock())

    def propose_inspection(self, call, context, cancel):
        from .inspection import InspectionRequest
        cancel.check()
        validate(call.name,call.arguments)
        if self.frozen or not context.get('inspection_enabled'):
            raise ValueError('InspectionUnavailable')
        if call.arguments['direction'] not in context.get('inspection_directions',('current','left','right','behind')):
            raise ValueError('MotionUnavailable')
        if self.store.revision!=context['state_revision']:
            raise ValueError('StaleStateRevision')
        self.attention.clear()
        self.attention.scene.invalidate(self.trace.clock())
        source = context.get('event_id') or context['source_turn_id']
        return InspectionRequest(source+':'+call.call_id,call.arguments['direction'],
            call.arguments['question'],self.attention.scene.view_epoch,self.trace.clock())

    def propose_pose(self, call, context, cancel):
        from .servo_poses import POSE_COMMANDS
        cancel.check()
        validate(call.name,call.arguments)
        if self.frozen or not context.get('robot_pose_enabled'): raise ValueError('RobotPoseUnavailable')
        if self.store.revision != context['state_revision']: raise ValueError('StaleStateRevision')
        if call.name == 'set_robot_joints':
            from .servo_poses import joint_targets
            return joint_targets(call.arguments['positions'])
        from .leds import LIGHT_STEPS
        poses = call.arguments['poses']
        if not poses or any(p not in POSE_COMMANDS and p not in LIGHT_STEPS for p in poses): raise ValueError('UnknownRobotPose')
        if any(p in LIGHT_STEPS and not (context.get('led_enabled') and LIGHT_STEPS[p] in context.get('led_patterns',()))
               for p in poses): raise ValueError('LEDPatternUnavailable')
        if call.arguments['look_after'] is not None and not context.get('inspection_enabled'):
            raise ValueError('InspectionUnavailable')
        return tuple(poses)

    def propose_drive(self, call, context, cancel):
        """Validate a model-selected base plan against offered targets and limits.

        Rejections carry a reason the model can explain; nothing moves here.
        Self-initiated plans (proactive opportunities) get the smaller limits.
        """
        from .drive import DrivePlan, DriveStep, LIMITS
        cancel.check()
        validate(call.name, call.arguments)
        drive = context.get('drive') or {}
        if self.frozen or not drive:
            raise ValueError('DriveUnavailable')
        if not drive.get('available'):
            raise ValueError('DriveUnavailable:' + str(drive.get('unavailable_reason')))
        if self.store.revision != context['state_revision']:
            raise ValueError('StaleStateRevision')
        origin = drive['origin']
        limits = LIMITS[origin]
        args = call.arguments
        offered = {t['target_id']: t for t in drive['targets']}
        if len(args['steps']) > limits['max_steps']:
            raise ValueError('DriveTooManySteps')
        steps, meters, degrees = [], 0., 0.
        for raw in args['steps']:
            action = raw['action']
            named = raw.get('target')
            if action in {'face', 'approach', 'find_person'} and isinstance(named, str) and named not in offered and named != 'sought':
                # October 3: the model dropped characters from a face id ("come towards me");
                # the unrecognized id was read as the thing sought earlier and he turned back
                # to the microwave. A damaged id is repaired only to the one offered target
                # it clearly abbreviates; it never becomes a different goal.
                def shared(a, b):
                    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                close = [key for key in offered if shared(key, named) >= 12]
                if len(close) == 1:
                    named = close[0]
                    raw = {**raw, 'target': named}
            if (action == 'approach' and named not in offered and named != 'sought' and
                    (any(s.action == 'seek' for s in steps) or
                     (drive.get('last_sought') and isinstance(named, str) and 'sought' in named))):
                # An approach naming anything unrecognized right after a seek in the same plan
                # can only mean what was just sought; so does a spelling of the word itself
                # (September 27: the model wrote "drive.last_sought").
                raw = {**raw, 'target': 'sought'}
            if action == 'seek' and raw.get('target') is not None:
                # September 27: the model put "white door on the left" in target. A seek names
                # things only in words; keep them as the description rather than refuse.
                raw = {**raw, 'description': raw.get('description') or raw['target'], 'target': None}
            used = {k for k in ('direction', 'degrees', 'meters', 'target', 'stop_at_m', 'description') if raw.get(k) is not None}
            required = {'turn': {'direction', 'degrees'}, 'move': {'direction', 'meters'}, 'face': {'target'},
                        'approach': {'target'}, 'find_person': set(), 'seek': {'description'}}[action]
            allowed = required | ({'stop_at_m'} | ({'description'} if raw.get('target') == 'sought' else set())
                                  if action == 'approach' else {'target'} if action == 'find_person' else
                                  {'direction'} if action == 'seek' else set())
            if not required <= used or not used <= allowed:
                raise ValueError('DriveStepFieldsMismatch:' + action)
            if raw['direction'] is not None and raw['direction'] not in (
                    {'left', 'right'} if action in {'turn', 'seek'} else {'forward', 'backward'}):
                raise ValueError('DriveDirectionMismatch:' + action)
            if action == 'seek' and not drive.get('vision_seek'):
                raise ValueError('VisionSeekUnavailable')
            if action == 'approach' and raw['target'] == 'sought':
                # A seek earlier in this plan, an explicit description, or the last
                # thing sought ("keep going toward the door") names what to re-find.
                if not (any(s.action == 'seek' for s in steps) or raw.get('description') or drive.get('last_sought')):
                    raise ValueError('ApproachSoughtNeedsSeek')
                if raw['stop_at_m'] is not None and not .4 <= raw['stop_at_m'] <= 2.5:
                    raise ValueError('DriveStopDistanceOutOfRange')
                steps.append(DriveStep(action, target='sought', stop_at_m=raw['stop_at_m'],
                                       description=(raw.get('description') or '').strip() or None, pace=raw.get('pace')))
                continue
            if action == 'turn' and not 1 <= raw['degrees'] <= limits['turn_deg']:
                raise ValueError('DriveTurnOutOfRange')
            if action == 'move' and not .05 <= raw['meters'] <= limits['total_m']:
                raise ValueError('DriveMoveOutOfRange')
            if action == 'seek':
                steps.append(DriveStep('seek', description=raw['description'].strip(), prefer=raw['direction'],
                                       pace=raw.get('pace')))
                continue
            if (action in {'face', 'approach'} or raw['target'] is not None) and raw['target'] not in offered:
                raise ValueError('DriveTargetNotOffered')
            # nearest_person with nobody in view is valid: the drive owner looks for the person
            # first (October 3: "come over here" was refused for want of a turn or search step).
            if raw['stop_at_m'] is not None and not .4 <= raw['stop_at_m'] <= 2.5:
                raise ValueError('DriveStopDistanceOutOfRange')
            if action == 'find_person' and any(s.action == 'find_person' for s in steps):
                raise ValueError('DriveRepeatedSearch')
            meters += abs(raw['meters'] or 0.)
            degrees += abs(raw['degrees'] or 0.)
            # Robot-frame signs are applied here, never chosen by the model: +left, +forward.
            sign = -1. if raw['direction'] in {'right', 'backward'} else 1.
            steps.append(DriveStep(action, sign*raw['degrees'] if raw['degrees'] is not None else None,
                                   sign*raw['meters'] if raw['meters'] is not None else None,
                                   raw['target'], raw['stop_at_m'], pace=raw.get('pace')))
        if meters > limits['total_m'] or degrees > limits['total_turn_deg']:
            raise ValueError('DrivePlanExceedsLimits')
        if args['look_after'] is not None and not context.get('inspection_enabled'):
            raise ValueError('InspectionUnavailable')
        source = context.get('event_id') or context['source_turn_id']
        return DrivePlan(source + ':' + call.call_id, tuple(steps), origin, args['purpose'][:200],
                         {k: dict(v) for k, v in offered.items()}, self.trace.clock() + 20.,
                         args['look_after'], allow_new_arrivals=context.get('trigger') == 'unseen_presence')

    def propose_lights(self, call, context, cancel):
        from .leds import LightRequest
        cancel.check()
        validate(call.name,call.arguments)
        if self.frozen or not context.get('led_enabled'):
            raise ValueError('LEDUnavailable')
        if self.store.revision != context['state_revision']:
            raise ValueError('StaleStateRevision')
        args = call.arguments
        if args['pattern'] not in context['led_patterns']:
            raise ValueError('LEDPatternUnavailable')
        if args['timing']=='during_speech' and (args['text'] is None or args['pattern'] in {'compliment','blink'}):
            raise ValueError('InvalidSpeakingAnimation')
        source = context.get('event_id') or context['source_turn_id']
        return LightRequest(source+':'+call.call_id,args['pattern'],
            1. if args['duration']=='brief' else 2.5,self.trace.clock()+30,context['led_version'])

    def propose_performance(self, call, context, cancel):
        from .performance import Gesture
        cancel.check()
        validate(call.name, call.arguments)
        if self.frozen or not context.get('gesture_enabled'):
            raise ValueError('GestureUnavailable')
        if self.store.revision != context['state_revision']:
            raise ValueError('StaleStateRevision')
        self.attention.context()
        if self.attention.version != context['attention']['version']:
            raise ValueError('StaleAttentionVersion')
        target = call.arguments['target']
        if context.get('gesture_targets') is not None and target not in context['gesture_targets']:
            raise ValueError('GestureTargetUnavailable')
        anchor = None
        if target not in {'left','right','up','forward','unfocused'}:
            anchor = next((r for r in context['attention']['vision']['targets'] if r['target_id']==target),None)
            if anchor is None or not self.attention.scene.valid(anchor):
                raise ValueError('GestureTargetUnavailable')
            anchor = self.attention.scene.resolve(target)
        source = context.get('event_id') or context['source_turn_id']
        return Gesture(source+':'+call.call_id,call.arguments['primitive'],target,
                       call.arguments['intensity'],self.trace.clock()+30,anchor), anchor

    def propose_attention(self, call, context, cancel):
        cancel.check()
        validate(call.name, call.arguments)
        if self.frozen or not context.get('attention_enabled'):
            raise ValueError('AttentionUnavailable')
        if self.store.revision != context['state_revision']:
            raise ValueError('StaleStateRevision')
        self.attention.context()  # Expiry is a versioned transition, even during generation.
        if self.attention.version != context['attention']['version']:
            raise ValueError('StaleAttentionVersion')
        source = context.get('event_id') or context['source_turn_id']
        anchor = None
        if call.arguments['target'] not in {'left','right','up','forward','unfocused'} and not call.arguments['target'].startswith('visual:'):
            raise ValueError('InvalidAttentionTarget')
        if call.arguments['target'].startswith('visual:'):
            offered = next((t for t in context['attention']['vision']['targets']
                            if t['target_id']==call.arguments['target']), None)
            if offered is None or not self.attention.scene.valid(offered):
                raise ValueError('AttentionTargetUnavailable')
            anchor = self.attention.scene.resolve(call.arguments['target'])
        if context.get('attention_targets') is not None and call.arguments['target'] not in context['attention_targets']:
            raise ValueError('AttentionTargetUnavailable')
        return self.attention.propose(source + ':' + call.call_id, call.arguments, anchor)

    def cancel(self):
        self.initiative.invitations.invalidate('explicit_stop')
        self.attention.clear()
        self.attention.scene.invalidate(self.trace.clock())
        # Stopping speech cancels dialogue work, not previously observed evidence.
        # Identity corrections use cancel_association or forget_person explicitly.
        if self.pending:
            self.trace.emit("pending_cancelled", enrollments=0, question=True)
        self.pending = None

    def begin(self, turn_id, capture, transcript, observation, faces=()):
        if not self._restored and not self.frozen:
            with self.store.transaction() as conn:
                conn.execute("DELETE FROM voice_candidates WHERE updated_at<?", (time.time()-86400,))
            for row in self.store.rows("SELECT * FROM voice_candidates WHERE namespace=? ORDER BY updated_at DESC LIMIT 12", (self.tracker.namespace,)):
                if row["person_id"] and self.store.person(row["person_id"])["status"] != "provisional":
                    with self.store.transaction() as conn:
                        conn.execute("DELETE FROM voice_candidates WHERE track_id=?", (row["track_id"],))
                    continue
                proposal = Enrollment.decode(row)
                self.evidence[proposal.track_id] = proposal
                self.tracker.tracks[proposal.track_id] = Track(proposal.track_id, float('-inf'),
                    deque([s[2].embedding for s in proposal.samples] or [proposal.anchor], maxlen=5), proposal.person_id,
                    "hypothesis" if proposal.person_id else "unresolved", retained=True)
                if proposal.person_id:
                    self.enrollments[proposal.track_id] = proposal
            self._restored = True
        expired = [key for key, p in self.evidence.items() if time.time()-p.updated_at >= 86400]
        if expired and not self.frozen:
            with self.store.transaction() as conn:
                conn.executemany("DELETE FROM voice_candidates WHERE track_id=?", [(key,) for key in expired])
            for key in expired:
                self.evidence.pop(key, None)
                self.enrollments.pop(key, None)
                self.tracker.tracks.pop(key, None)
        # Retention keeps acoustic evidence matchable, not recently heard.
        for key in self.evidence:
            if key in self.tracker.tracks:
                self.tracker.tracks[key].retained = True
        if capture.motion_risk and not observation.motion:
            observation = replace(observation, motion=True)
        if capture.overlap is True:
            observation = replace(observation, mixed=True)
        attribution = self.tracker.observe(observation, capture.ended_at,
            self.store.profiles(self.tracker.namespace), transcript.trusted, capture.echo_risk, capture.truncated)
        self.enrollments = {key:p for key,p in self.enrollments.items()
                            if p.track_id in self.tracker.tracks}
        if self.pending and capture.ended_at-self.pending["delivered_at"] > 120:
            self.pending = None
        if (capture.motion_risk or capture.parent_capture_id is not None or capture.robot_microphone_hold is False or
                (self.require_microphone_hold and capture.robot_microphone_hold is not True)):
            attribution = replace(attribution,learning_eligible=False)
        self.current = (turn_id,capture,transcript,observation,attribution)
        if transcript.trusted and not capture.echo_risk:
            self.conversation_source = turn_id
        self.faces = tuple(f for f in faces if f.get("face_epoch",0)>=self.min_face_epoch)
        faces = self.faces
        self.face_associations = {k:v for k,v in self.face_associations.items() if any(f["track_id"]==k for f in faces)}
        track = self.tracker.tracks.get(attribution.track_id) if attribution.reason!="tentative_continuity" else None
        self.store.source(turn_id,capture,transcript,attribution,observation,track.person_id if track else None)
        self.forgotten = False
        self.learn_observation()
        self.learn_faces()
        return attribution

    def identity_targets(self, name):
        """Same acoustic authorization for offered actions and execution."""
        try:
            self._source_allowed({}, name)
        except ValueError:
            return []
        attribution = self.current[4]
        targets = [attribution.person_id] if attribution.person_id else []
        provisional = self.enrollments.get(attribution.track_id)
        if name == "rename_person" and provisional:
            targets.append(provisional.person_id)
        return list(dict.fromkeys(targets))

    def context(self, faces=()):
        turn_id,capture,transcript,observation,attribution = self.current
        track = self.tracker.tracks.get(attribution.track_id) if attribution.reason!="tentative_continuity" else None
        speaker_person = self.store.person(attribution.person_id) if attribution.person_id else None
        from .face_attribution import public_face
        return self.note_audience({"source_turn_id":turn_id,"state_revision":self.store.revision,
                "current_utterance":transcript.text,"calendar_date":self.calendar_today().isoformat(),
                "participation":self.initiative.context(self.trace.clock()),
                "identity_action_targets":{name:self.identity_targets(name) for name in ("rename_person","forget_person")},
                "speaker":{**asdict(attribution),"display_name":speaker_person["name"] if speaker_person else None,
                           "direction":observation.direction},
                "provisional_person_id":track.person_id if track else None,
                "people":self.store.people(),"claims":self.store.claims(include_candidates=True),
                "pending_question":self.pending,"faces":[{**public_face(f),"person_hypothesis":self.face_associations.get(f["track_id"])} for f in self.faces],
                "face_enrollment":[{"face_track_id":k,"person_id":v["person_id"]} for k,v in self.face_learning.items()],
                "frozen_evaluation":self.frozen,
                "transcript_trusted":transcript.trusted,
                "acoustic_limits":{"overlap_detector":"window consistency only", "overlap":capture.overlap,
                    "speaker_boundary_proposed":capture.parent_capture_id is not None},
                "enrollment":[e.progress() for e in self.enrollments.values()]})

    def note_audience(self, context):
        """Remember the private audience while evidence supports it, and offer it back to
        the memory gate for a short while after (conversation_memory.subject).

        A contradiction (several faces, somebody else recognized) ends the carry at once;
        a fresh recognition is then needed."""
        from . import conversation_memory as cm
        now = self.trace.clock()
        who = cm.evidenced_subject(context)
        faces = context.get('current_view', {}).get('faces', context.get('faces', []))
        if len(faces) > 1 or (self.audience_seen and cm.contradicted(context, self.audience_seen[0])):
            # Somebody else is here: nobody is carried until recognized directly, and an
            # unrecognized face cannot inherit the visit for a while (COMPANY_SECONDS).
            self.audience_seen, self.company_at = None, now
        if who:
            self.audience_seen = (who, now)
        context.pop('recent_audience', None)
        if self.audience_seen and now - self.company_at >= cm.COMPANY_SECONDS:
            context['recent_audience'] = {'person_id':self.audience_seen[0],
                'seconds_ago':round(now-self.audience_seen[1],1),
                'meaning':'Last person recognized as the private audience. Keeps their memories usable through brief camera gaps; not proof they are visible now or that they spoke.'}
        return context

    def opportunity_context(self, event, *, advance_memory_cursor=True):
        """No acoustic observation/source turn is fabricated for an opportunity."""
        from .face_attribution import public_face
        if self.frozen: raise ValueError("FrozenEvaluationDisallowsProactiveSpeech")
        self.forgotten=False  # A new opportunity is a new turn, not the prior forget operation.
        if self.pending and event.at-self.pending['delivered_at']>120: self.pending=None
        faces = [public_face(f) for f in event.faces if f.get('face_epoch',0)>=self.min_face_epoch]
        people = self.store.people()
        person_ids = {p['id'] for p in people}
        if event.kind not in {'visual_arrival','pet_arrival','conversation_lull','ambient','unseen_presence','reconsideration'}: raise ValueError('UnknownOpportunity')
        if event.person_id and event.person_id not in person_ids: raise ValueError('VisualPersonNoLongerAvailable')
        if event.kind=='visual_arrival' and not any(f['track_id']==event.target_track for f in faces):
            raise ValueError('VisualTargetStale')
        if event.kind=='conversation_lull':
            if event.source_turn_id != self.conversation_source:
                raise ValueError('StaleLullSource')
            if self.current is None:
                from .conversation_intent import audience
                intent = self.conversation_intent.record
                if intent is None or intent.status != 'active' or intent.audience != audience({'faces':faces}):
                    raise ValueError('LullRequiresConversationOrActiveIntent')
        cues=[cue.public() for cue in event.public_cues if self.observation_available(cue.public(),faces)]
        if event.scene_id and event.scene_images:
            scene={'cue_id':event.scene_id,'kind':'scene','source':'current camera snapshots',
                   'source_url':'','published_at':self.wall_clock(),'expires_at':self.wall_clock()+10,
                   'text':'Two ordered snapshots when available. Interpret only visible evidence; no verified identity, emotion or mouth-motion evidence.'}
            if self.observation_available(scene):cues.append(scene)
        from .memory_opportunities import candidates
        offered = candidates(self.store,faces,self.calendar_today(),self.memory_cursor)
        if advance_memory_cursor: self.memory_cursor += len(offered)
        return self.note_audience({'observations':cues,'calendar_date':self.calendar_today().isoformat(),'memory_opportunities':offered,'trigger':event.kind,'event_id':event.event_id,'state_revision':self.store.revision,
                'participation':self.initiative.context(self.trace.clock()),
                'opportunity':{'kind':event.kind,'source_turn_id':event.source_turn_id,
                               'quiet_seconds':event.quiet_seconds,
                               'audience':'arrival_target' if event.kind=='visual_arrival' else
                                          'nobody_visible' if not faces else 'visible_group'},
                **({'visual_arrival':{'face_track_id':event.target_track,'person_id':event.person_id,
                                     'returning_this_session':event.returning,
                                     'startup_greeting':self.conversation_source is None and not event.returning
                                         and not any(r['heard_complete_phrases'] for r in self.initiative.outcomes)}} if event.kind=='visual_arrival' else {}),
                **({'pet_arrival':{'target_id':event.target_track,
                                   'detector_label':(event.pet or {}).get('label'),
                                   'saved_pets':[{'entity_id':e.get('entity_id'),'name':e.get('name')}
                                                 for e in (self.photo_memory.catalog() if self.photo_memory else ())
                                                 if e.get('kind')=='pet'],
                                   'meaning':'An animal came into view. The detector confuses dogs and cats; '
                                             'only a look against saved photos can say which pet it is.'}}
                   if event.kind=='pet_arrival' else {}),
                'speaker':{'person_id':None,'state':'unresolved','reason':'no_speech_observed'},
                'faces':faces,'people':people,'claims':[],
                'pending_question':self.pending,'frozen_evaluation':self.frozen})

    def intent_context(self, context, *, human=False):
        from .conversation_intent import faces_for
        from .memory_opportunities import candidates
        memories = candidates(self.store, faces_for(context), self.calendar_today(), limit=128, include_delivered=True)
        self.conversation_intent.refresh(context, [r['claim_id'] for r in memories], human=human)
        # Ordinary turns may maintain existing references but don't gain a new memory scheduler.
        if not context.get('trigger'):
            keep = set(self.conversation_intent.record.memory_ids) if self.conversation_intent.record else set()
            context['memory_opportunities'] = [r for r in memories if r['claim_id'] in keep]
        context['conversation_intent'] = self.conversation_intent.context(context)
        context['invitations'] = self.initiative.invitations.context(context,self.trace.clock())
        context['reconsideration'] = self.initiative.scheduling_context(context.get('event_id') or context.get('source_turn_id'))
        return context

    def _with_intent(self, call, context, cancel, execute):
        from .conversation_intent import UPDATE_TOOLS, faces_for, audience
        from .memory_opportunities import candidates
        value = call.arguments.get('intent_update')
        if call.name not in UPDATE_TOOLS or (value is None and call.name != 'respond_with_intent'):
            return execute(call)
        source = context.get('event_id') or context.get('source_turn_id')
        request_id = str(source) + ':' + call.call_id
        fingerprint = hashlib.sha256(json.dumps({'name':call.name,'args':call.arguments},sort_keys=True).encode()).hexdigest()
        intents = self.conversation_intent
        try:
            cancel.check()
            if self.frozen:raise ValueError('FrozenEvaluationDisallowsMutation')
            if not context.get('trigger'):
                if self.current is None or source != self.current[0]:raise ValueError('IntentSourceMismatch')
                self._source_allowed({}, call.name)
            prior = intents.requests.get(request_id)
            if prior:
                if prior[0] != fingerprint:raise ValueError('RequestIdReusedWithDifferentArguments')
                if prior[2] != audience(context):raise ValueError('IntentAudienceChanged')
                if prior[1].revision != self.store.revision:raise ValueError('StaleStateRevision')
                return prior[1]
            if context.get('state_revision') != self.store.revision:raise ValueError('StaleStateRevision')
            memories = candidates(self.store, faces_for(context), self.calendar_today(), limit=128, include_delivered=True)
            try:
                row = intents.prepare(value, context, [r['claim_id'] for r in memories])
            except ValueError as exc:
                # Boundaries and silence may discard optional notes. A reply explicitly
                # tied to an intention must recover from rejection before speaking it.
                if str(exc) == 'FrozenEvaluationDisallowsMutation':raise
                self.trace.emit('conversation_intent_rejected',source_id=source,reason=str(exc),operation='reply_rejected' if call.name=='respond_with_intent' else 'continued_without_intent')
                if call.name == 'respond_with_intent':
                    raise  # Never speak a promise whose only action record was rejected.
                row = None
                call = replace(call, arguments={**call.arguments,'intent_update':None})
            validate(call.name,call.arguments)
            cancel.check()
            result = execute(call)
            if result.outcome == 'ok':
                if row is not None:intents.commit(row)
                if call.name == 'conversation_feedback' and result.data['action'] in {'topic_closed','topic_avoid','invitation_declined'}:
                    intents.suspend('participant_boundary')
                if call.name == 'remain_silent' and result.data['reason'] in {'other_addressee','background_conversation','unfinished_thought'}:
                    intents.suspend('human_floor')
                if self.initiative.quiet:intents.suspend('initiative_quiet')
                intents.requests[request_id] = (fingerprint, result, audience(context))
                if len(intents.requests)>32:del intents.requests[next(iter(intents.requests))]
            return result
        except ValueError as exc:
            self.trace.emit('conversation_intent_rejected',source_id=source,reason=str(exc))
            return ToolResult(request_id,'rejected',str(exc),self.store.revision)

    def execute_opportunity(self, call, context, cancel):
        return self._with_intent(call,context,cancel,
            lambda call:self._execute_opportunity(call,context,cancel))

    def _execute_opportunity(self, call, context, cancel):
        """Visual cues can ask or stay quiet, never create identity or personal facts."""
        request_id = context['event_id']+':'+call.call_id
        try:
            cancel.check()
            validate(call.name,call.arguments)
            if self.store.revision!=context['state_revision']: raise ValueError('StaleStateRevision')
            from .semantics import greeting_violation
            violation = greeting_violation(context, call)
            if violation: raise ValueError(violation)
            if call.name=='respond_with_intent':
                data={'text':call.arguments['text']}
            elif call.name=='use_visual_memory':
                from . import visual_memory
                offered={r['id'] for r in context.get('visual_memory',[])}
                current=visual_memory.context(self.store,self.attention.scene.session_id,visual_memory.audience(context))
                if call.arguments['observation_id'] not in offered & {r['id'] for r in current}:
                    raise ValueError('VisualObservationUnavailable')
                data={'text':call.arguments['text'],'observation_id':call.arguments['observation_id']}
            elif call.name=='use_observation':
                cue=next((r for r in context.get('observations',[]) if r['cue_id']==call.arguments['cue_id']),None)
                if cue is None or not self.observation_available(cue,context.get('faces',[])):raise ValueError('ObservationUnavailable')
                data={'text':call.arguments['text'],'cue':cue}
            elif call.name=='use_memory_opportunity':
                from .memory_opportunities import candidates
                offered={r['claim_id'] for r in context.get('memory_opportunities',[])}
                current=candidates(self.store,context['faces'],self.calendar_today(),limit=128)
                candidate=next((r for r in current if r['claim_id']==call.arguments['claim_id'] and r['claim_id'] in offered),None)
                if not candidate:raise ValueError('MemoryOpportunityUnavailable')
                data={'text':call.arguments['text'],'claim_id':candidate['claim_id'],'occasion':candidate['occasion']}
            elif call.name=='remain_silent': data={'reason':call.arguments['reason']}
            elif call.name=='ask_face_question':
                if call.arguments['face_track_id'] not in {f['track_id'] for f in context['faces']}:
                    raise ValueError('FaceEvidenceMissing')
                if context['trigger']=='visual_arrival' and call.arguments['face_track_id']!=context['visual_arrival']['face_track_id']:
                    raise ValueError('VisualQuestionTargetMismatch')
                data={'question':call.arguments['text'],'face_track_id':call.arguments['face_track_id'],'addressee_id':None}
            else: raise ValueError('VisualEventDoesNotAuthorizeMutation')
            return ToolResult(request_id,'ok','executed',self.store.revision,data)
        except ValueError as exc:
            return ToolResult(request_id,'rejected',str(exc),self.store.revision)

    def observation_available(self, cue, faces=None):
        if cue['expires_at']<=self.wall_clock():return False
        provider=self.scene_observer if cue['kind']=='scene' else self.observations
        if provider is not None and not provider.valid(cue['cue_id']):return False
        if cue['kind']=='interest_news':
            # Rechecked from the store each time: a forgotten person or retracted interest stops it.
            from .news import interest_topics
            if faces is None or cue.get('topic') not in interest_topics(self.store,faces):return False
        # A searched story is spoken once; headlines and weather may recur after a day.
        window=7*86400 if cue['kind'] in {'story','interest_news'} else 86400
        return not self.store.rows('SELECT 1 FROM observation_delivery WHERE cue_id=? AND delivered_at>?',
                                   (cue['cue_id'],self.wall_clock()-window))

    def observation_delivered(self, cue):
        with self.store.transaction() as conn:
            conn.execute('DELETE FROM observation_delivery WHERE delivered_at<?',(self.wall_clock()-7*86400,))
            conn.execute('INSERT OR REPLACE INTO observation_delivery VALUES(?,?,?)',
                         (cue['cue_id'],cue['kind'],self.wall_clock()))
            self.store.advance(conn)

    def memory_delivered(self, selected):
        with self.store.transaction() as conn:
            current=conn.execute("SELECT status FROM claims WHERE id=?",(selected['claim_id'],)).fetchone()
            if not current or current[0]!='accepted':return
            written=conn.execute('INSERT OR IGNORE INTO memory_followup_delivery VALUES(?,?,?)',
                         (selected['claim_id'],selected['occasion'],self.calendar_today().isoformat()))
            if written.rowcount:self.store.advance(conn)

    def execute(self, call, expected_revision, cancel, context=None):
        if call.arguments.get('intent_update') is not None or call.name == 'respond_with_intent':
            context = context if context is not None else self.intent_context(self.context())
            context = dict(context, state_revision=expected_revision)
            return self._with_intent(call,context,cancel,
                lambda call:self._execute(call,expected_revision,cancel))
        return self._execute(call,expected_revision,cancel,context)

    def _execute(self, call, expected_revision, cancel, context=None):
        turn_id,capture,transcript,observation,attribution = self.current
        request_id = capture.session_id + ":" + call.call_id
        fingerprint = hashlib.sha256(json.dumps({"name":call.name,"args":call.arguments},sort_keys=True).encode()).hexdigest()
        revision = self.store.revision
        try:
            validate(call.name,call.arguments)
            if self.frozen and call.name not in {"recall","recall_conversations","ask_question","ask_face_question","settle_question","remain_silent"}:
                raise ValueError("FrozenEvaluationDisallowsMutation")
            if call.name == 'respond_with_intent':
                cancel.check()
                if revision != expected_revision:raise ValueError('StaleStateRevision')
                return ToolResult(request_id,'ok','executed',revision,{'text':call.arguments['text']})
            with self.store.transaction() as conn:
                cancel.check()
                previous = conn.execute("SELECT * FROM operations WHERE request_id=?",(request_id,)).fetchone()
                if previous:
                    if previous["fingerprint"] != fingerprint:
                        raise ValueError("RequestIdReusedWithDifferentArguments")
                    data = json.loads(previous["result"])
                    return ToolResult(request_id,data["outcome"],data["reason"],revision,data.get("data",{}))
                revision = int(conn.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
                if revision != expected_revision:
                    raise ValueError("StaleStateRevision")
                data, mutation = self._apply(conn,call.name,call.arguments,context)
                cancel.check()
                if mutation:
                    revision = self.store.advance(conn)
                result = ToolResult(request_id,"ok","executed",revision,data)
                conn.execute("INSERT INTO operations VALUES(?,?,?)",(request_id,fingerprint,json.dumps(asdict(result))))
            # In-memory proposals are applied only after the transaction commits.
            self._after(call.name,data)
            self.trace.emit("tool_result", name=call.name, **asdict(result))
            return result
        except ValueError as exc:
            result = ToolResult(request_id,"rejected",str(exc),self.store.revision)
        except Exception as exc:
            from .contracts import Cancelled
            if isinstance(exc,Cancelled):
                raise
            result = ToolResult(request_id,"failed",type(exc).__name__,self.store.revision)
        self.trace.emit("tool_result",name=call.name,**asdict(result))
        return result

    def _source_allowed(self, args, name):
        turn_id,capture,transcript,observation,attribution = self.current
        if args.get("source_turn_id",turn_id) != turn_id:
            raise ValueError("SourceTurnMismatch")
        if (not transcript.trusted or capture.echo_risk or capture.truncated or observation.mixed or capture.overlap is True
                or capture.parent_capture_id is not None):
            raise ValueError("UntrustedSource")
        if attribution.reason=="tentative_continuity" and (
                name in {"rename_person","forget_person"} or
                (name=="record_claim" and args["kind"]=="self_report")):
            raise ValueError("TentativeTrackDoesNotAuthorizeSelfIdentity")

    def _person(self, person_id):
        person = self.store.person(person_id)
        if person is None or person["status"]=="retracted":
            raise ValueError("UnknownPersonId")
        return person

    def _feedback(self, args):
        turn_id,capture,transcript,observation,attribution = self.current
        self._source_allowed(args,"conversation_feedback")
        if not attribution.track_id or attribution.reason == "tentative_continuity":
            raise ValueError("FeedbackNeedsCurrentSpeakerEvidence")
        participant = {"person_id":attribution.person_id,"track_id":attribution.track_id}
        action, reference, topic = args["action"], args["reference_id"], args["topic"]
        needs_reference = action in {"invitation_answered","invitation_declined","retract","floor_resolved"}
        if bool(reference) != needs_reference or bool(topic) != (action in {"topic_closed","topic_avoid"}):
            raise ValueError("FeedbackFieldsMismatch")
        if action == "yield_floor" and args["text"] is not None:
            raise ValueError("FloorYieldMustBeSilent")
        now = self.trace.clock()
        if action in {"retract","floor_resolved"}:
            previous = next((r for r in self.initiative.context(now)["feedback"] if r["id"]==reference), None)
            if not previous: raise ValueError("FeedbackReferenceMissing")
            if previous['source_turn_id']==turn_id:
                raise ValueError('FeedbackNeedsNewSource')
            if action=='floor_resolved':
                if previous['action']!='yield_floor': raise ValueError('NotAHumanFloorReference')
            else:
                owner = previous["participant"]
                if not (owner["person_id"] == participant["person_id"] if owner["person_id"]
                        else owner["track_id"] == participant["track_id"]):
                    raise ValueError("FeedbackBelongsToAnotherParticipant")
        elif needs_reference:
            previous = next((r for r in self.initiative.outcomes if r["event_id"]==reference), None)
            if not previous or not previous["heard_complete_phrases"]:
                raise ValueError("FeedbackNeedsAudibleOpportunity")
            if previous["at"] > now or now-previous["at"] > 300:
                raise ValueError("FeedbackOpportunityExpired")
            if turn_id == previous['source_turn_id'] or capture.started_at < previous['at']:
                raise ValueError("FeedbackSourcePrecedesOpportunity")
            target = previous["target_person_id"]
            if target and target != attribution.person_id:
                raise ValueError("FeedbackTargetMismatch")
        if action not in {'retract','floor_resolved'} and len(self.initiative.feedback)==32 and all(
                r['action']=='topic_avoid' for r in self.initiative.feedback):
            raise ValueError('FeedbackCapacityReached')
        return {"id":uuid4().hex,"action":action,"reference_id":reference,"topic":topic,
                "source_turn_id":turn_id,"source_text":transcript.text[:500],
                "participant":participant,"at":now,"expires_at":now+120 if action=="yield_floor" else None,
                "text":args["text"],"reason":"conversation_feedback"},True

    def _impersonate(self, conn, args, context):
        """Resolve the chosen voice to a reference clip the cloner can use. Nothing here
        is a fact or an identity: the parody is bounded, the voice comes only from
        offered targets, and a stranger's voice is this session's own clean samples."""
        from . import impersonation
        from .output import plain_text
        turn_id,capture,transcript,observation,attribution = self.current
        self._source_allowed(args,'impersonate')
        offered = (context or {}).get('impersonation') or {}
        if self.frozen or self.voice_references is None or not offered.get('available'):
            raise ValueError('ImpersonationUnavailable')
        if not args['requested']:
            allowed, reason = self.impressions.spontaneous_allowed(self.trace.clock())
            if not allowed:
                raise ValueError('SpontaneousImpressionCooldown:' + str(reason))
        script = impersonation.cap_script(plain_text(args['script']),
            impersonation.MAX_SCRIPT_WORDS if args['requested'] else impersonation.CAMEO_MAX_SCRIPT_WORDS)
        if not script:
            raise ValueError('InvalidImpressionScript')
        target = args['target']
        reference = None
        if target == 'self':
            if not offered.get('speaker',{}).get('ready'):
                raise ValueError('SpeakerVoiceNotReady')
            if attribution.person_id and self.store.person(attribution.person_id)['status']=='supported':
                person = self.store.person(attribution.person_id)
                reference = self.voice_references.person_reference(self.store,conn,person['id'],person['name'])
            else:
                proposal = self.evidence.get(attribution.track_id) if attribution.track_id else None
                if proposal is None or attribution.reason=='tentative_continuity':
                    raise ValueError('SpeakerVoiceNotReady')
                reference = self.voice_references.track_reference(proposal.samples,attribution.track_id,
                                                                  capture.session_id,'you')
            kind = 'speaker'
        elif any(p['target_id']==target for p in offered.get('people',[])):
            person = self._person(target)
            if person['status']!='supported': raise ValueError('VoiceReferenceUnavailable')
            reference = self.voice_references.person_reference(self.store,conn,person['id'],person['name'])
            kind = 'person'
        elif target in offered.get('famous',[]):
            reference = impersonation.famous_voices().get(target)
            kind = 'famous'
        else:
            raise ValueError('ImpressionTargetNotOffered')
        if reference is None:
            raise ValueError('VoiceReferenceUnavailable')
        for key in ('intro','outro'):
            if args[key] is not None and not plain_text(args[key]):
                args[key] = None
        return {'subject':reference.name,'voice':reference.key,'target_kind':kind,'script':script,
                'script_capped':script!=' '.join(plain_text(args['script']).split()),
                'intro':args['intro'],'outro':args['outro'],'requested':args['requested'],
                'reference_wav':reference.wav_path,'reference_text':reference.text,
                'meaning':'Voice reference resolved; nothing has been performed yet.'}

    def _apply(self, conn, name, args, context=None):
        turn_id,capture,transcript,observation,attribution = self.current
        if name == 'use_visual_memory':
            from . import visual_memory
            allowed=visual_memory.context(self.store,capture.session_id,visual_memory.audience({'faces':self.faces}))
            if args['observation_id'] not in {r['id'] for r in allowed}:
                raise ValueError('VisualObservationUnavailable')
            return {'text':args['text'],'observation_id':args['observation_id']},False
        if name == 'revise_visual':
            self._source_allowed(args,name)
            from . import visual_memory
            allowed=visual_memory.correction_candidates(self.store,capture.session_id,visual_memory.audience({'faces':self.faces}))
            if not {r['observation_id'] for r in args['revisions']} <= {r['id'] for r in allowed}:
                raise ValueError('VisualObservationUnavailable')
            records=visual_memory.revise(conn,args['revisions'],turn_id,transcript.text,self.wall_clock())
            return {'text':args['text'],'source_turn_id':turn_id,'records':records,
                    'meaning':'Revised old model interpretations using this user correction; no new image or verified personal fact.'},True
        if name == 'retract_visual':
            self._source_allowed(args,name)
            from . import visual_memory
            allowed=visual_memory.correction_candidates(self.store,capture.session_id,visual_memory.audience({'faces':self.faces}))
            if not set(args['observation_ids']) <= {r['id'] for r in allowed}:
                raise ValueError('VisualObservationUnavailable')
            conn.executemany("UPDATE visual_observations SET status='retracted' WHERE id=?",[(key,) for key in args['observation_ids']])
            return {'text':args['text'],'source_turn_id':turn_id,
                    'records':[{'observation_id':key,'status':'retracted'} for key in args['observation_ids']],
                    'replacement_saved':False},True
        if name == 'impersonate':
            return self._impersonate(conn,args,context), False
        if name == 'shut_down':
            self._source_allowed(args,name)   # Only a clearly heard, non-echo request from this turn.
            return {'text':args['text']}, False
        if name == "remain_silent":
            data = {"reason":args["reason"]}
            if not self.frozen and args['reason'] in {'other_addressee','background_conversation','unfinished_thought'}:
                try:
                    data['feedback'], _ = self._feedback(dict(action='yield_floor',reference_id=None,
                        topic=None,source_turn_id=turn_id,text=None))
                except ValueError:
                    # Silence remains valid even when source/track evidence cannot support a note.
                    pass
            return data,'feedback' in data
        if name == "conversation_feedback":
            return self._feedback(args)
        if name == "set_initiative":
            self._source_allowed(args,name)
            return {"mode":args["mode"],"source_turn_id":turn_id,
                    "reason":"explicit_initiative_request"},False
        if name == "remember_followup":
            self._source_allowed(args,name)
            if attribution.state!='supported' or not attribution.person_id:
                raise ValueError('FollowupNeedsSupportedSelfIdentity')
            from .memory_opportunities import validate_date
            validate_date(args['kind'],args['on_date'])
            data, _ = self._apply(conn,'record_claim',dict(subject_id=attribution.person_id,
                subject_reference=None,predicate=args['predicate'],value=args['value'],
                kind='self_report',source_turn_id=turn_id))
            if data['status']!='accepted':raise ValueError('FollowupNeedsAcceptedClaim')
            conn.execute('INSERT INTO memory_followups VALUES(?,?,?,?)',
                         (data['claim_id'],args['kind'],args['on_date'],args['audience']))
            return {**data,'followup_kind':args['kind'],'on_date':args['on_date'],'audience':args['audience']},True
        if name in {'name_room','correct_room'}:
            return self._room(conn,name,args),True
        if name == "remember_interest":
            self._source_allowed(args,name)
            if attribution.state!='supported' or not attribution.person_id:
                raise ValueError('InterestNeedsSupportedSelfIdentity')
            from .news import normalize_topic
            topic=normalize_topic(args['topic'])
            if not 2<=len(topic)<=80:raise ValueError('InvalidInterestTopic')
            data, _ = self._apply(conn,'record_claim',dict(subject_id=attribution.person_id,
                subject_reference=None,predicate=args['predicate'],value=args['value'],
                kind='self_report',source_turn_id=turn_id))
            if data['status']!='accepted':raise ValueError('InterestNeedsAcceptedClaim')
            conn.execute('INSERT INTO interests VALUES(?,?)',(data['claim_id'],topic))
            return {**data,'interest_topic':topic,
                    'meaning':'Saved as their own stated interest; fresh news about it may be offered while they are here.'},True
        if name in {'recall_conversations','remember_episode','retract_episode'}:
            from . import conversation_memory as cm
            who = cm.subject(context or self.context())
            if not who: raise ValueError('PrivateRecallRequiresKnownAudience')
            if name == 'recall_conversations':
                return cm.recall(self.store,who,capture.session_id,query=args['query'],period=args['period'],
                                 after=args['after'],before=args['before'],now=self.wall_clock()),False
            self._source_allowed(args,name)
            if name == 'remember_episode':
                return cm.episode(self.store,conn,who,args,uuid4().hex,self.wall_clock()),True
            deleted = conn.execute('DELETE FROM conversation_episodes WHERE id=? AND person_id=?',
                                   (args['episode_id'],who)).rowcount
            if not deleted: raise ValueError('EpisodeUnavailable')
            return {'episode_id':args['episode_id'],'status':'retracted'},True
        if name == "recall":
            if args["subject_id"]:
                self._person(args["subject_id"])
            return {"claims":self.store.claims(args["subject_id"])},False
        if name == "ask_face_question":
            if not any(f["track_id"]==args["face_track_id"] for f in self.faces):
                raise ValueError("FaceEvidenceMissing")
            return {"question":args["text"],"addressee_id":None,"face_track_id":args["face_track_id"]},False
        if name == "ask_question":
            if args["addressee_id"]:
                self._person(args["addressee_id"])
            return {"question":args["text"],"addressee_id":args["addressee_id"]},False
        if name == "settle_question":
            if not self.pending:
                raise ValueError("NoDeliveredPendingQuestion")
            return {"status":args["status"]},False
        self._source_allowed(args,name)
        if name == "reset_face_identity":
            person = self._person(args["person_id"])
            face = next((f for f in self.faces if f["track_id"]==args["face_track_id"]),None)
            if face is None or face.get('identity')!=person['id']:
                raise ValueError("FaceCorrectionNeedsCurrentAssociation")
            conn.execute("DELETE FROM face_references WHERE person_id=?", (person['id'],))
            conn.execute("INSERT OR IGNORE INTO face_learning_blocks VALUES(?)", (person['id'],))
            return {"person_id":person['id'],"status":"face_references_removed"},True
        if name == "introduce_person":
            if args["face_track_id"] and not any(f["track_id"]==args["face_track_id"] for f in self.faces):
                raise ValueError("FaceEvidenceMissing")
            name_text = args["name"].strip()
            if len(name_text) > 120 or any(ord(c)<32 for c in name_text):
                raise ValueError("InvalidDisplayLabel")
            matches = self.store.resolve(name_text)
            person_id = args["person_id"]
            if (args["subject"] == "other" and not person_id and not matches and not args["face_track_id"]
                    and self.photo_memory is not None and any(
                        e['kind'] == 'pet' and e['name'].casefold() == name_text.casefold()
                        for e in self.photo_memory.catalog())):
                # October 3: "This is Max" about the dog on the couch created a person named Max
                # beside the saved pet. The catalog, not the wording, decides this.
                raise ValueError("NameBelongsToSavedPet")
            if person_id:
                self._person(person_id)
            elif len(matches)==1:
                person_id = matches[0]["id"]
            elif len(matches)>1:
                raise ValueError("AmbiguousName")
            fresh_track = False
            introduction_attribution = attribution
            voice_usable = (capture.robot_microphone_hold is not False and
                            (not self.require_microphone_hold or capture.robot_microphone_hold is True) and
                            observation.embedding is not None and
                            observation.namespace == self.tracker.namespace and
                            observation.consistency is not None and
                            observation.consistency >= self.tracker.policy.track_threshold and
                            observation.voiced_seconds >= .5 and not observation.direction_conflict)
            if args["subject"] == "self":
                # A name assertion is not an acoustic recognition result. Ambiguous
                # old groups must not veto a new, source-bound enrollment anchor.
                reliable_track = attribution.track_id if attribution.reason in {'session_track','acoustic_match'} else None
                track = self.tracker.tracks.get(reliable_track)
                if track and track.person_id and track.person_id != person_id:
                    raise ValueError("ConflictingVoiceIdentity")
                if attribution.person_id and attribution.person_id != person_id:
                    raise ValueError("NameCannotOverrideAcoustics")
                if person_id:
                    if self.store.person(person_id)["status"]=="suspect":
                        raise ValueError("SuspectProfileRequiresLocalReviewOrVoiceReset")
                    if person_id in self.store.profiles(self.tracker.namespace) and attribution.person_id!=person_id:
                        raise ValueError("ExistingProfileNeedsCompatibleVoice")
                    if any(e.person_id==person_id and e.track_id!=reliable_track for e in self.enrollments.values()):
                        # Reintroduction can replace a stalled provisional chain,
                        # but cannot replace a recently active competing voice.
                        competing_chains = [e for e in self.enrollments.values() if e.person_id==person_id and e.track_id!=reliable_track]
                        if reliable_track is not None or any(0 <= capture.ended_at-self.tracker.tracks[e.track_id].last_at <= 8
                               for e in competing_chains if e.track_id in self.tracker.tracks):
                            raise ValueError("CompetingVoiceAssociation")
                if voice_usable and reliable_track is None:
                    if sum(capture.ended_at-t.last_at<=self.tracker.policy.track_expiry_seconds
                           for t in self.tracker.tracks.values()) < 12:
                        reliable_track, fresh_track = uuid4().hex, True
                from .speakers import Attribution
                introduction_attribution = (replace(attribution,track_id=reliable_track,state='hypothesis',
                    reason='self_introduction',learning_eligible=voice_usable and observation.voiced_seconds>=1)
                    if reliable_track and voice_usable else Attribution(reason='name_only_introduction'))
            if not person_id:
                person_id = self.store.add_person(conn,name_text,args["aliases"],turn_id)
            from .face_attribution import target_allowed, learning_samples
            face = next((f for f in self.faces if f["track_id"]==args["face_track_id"]),None)
            # The semantic owner may omit the face ID for an ordinary self-introduction.
            if face is None and args['subject']=='self' and len(self.faces)==1:
                face = self.faces[0]
            competing = any(key!=attribution.track_id and 0<=capture.ended_at-track.last_at<=8
                            for key,track in self.tracker.tracks.items())
            consistent_intro = (not observation.direction_conflict and observation.consistency is not None and
                                observation.consistency>=self.tracker.policy.track_threshold)
            allowed = face is not None and target_allowed(face,self.faces,args['subject'],introduction_attribution,self.pending,
                                                          competing,introduction_source=consistent_intro)
            status, saved = 'no_face_target', 0
            if face is not None:
                status = 'ambiguous_face_target'
                if face.get('identity') not in (None,person_id):
                    allowed, status = False, 'conflicting_face_identity'
                if allowed:
                    conn.execute('DELETE FROM face_learning_blocks WHERE person_id=?', (person_id,))
                    samples,status = learning_samples(face,capture,person_id,self.store)
                    if samples:
                        saved = self.store.save_faces(conn,person_id,samples,turn_id,
                            {'subject':args['subject'],'said_by':attribution.person_id,'source_track':introduction_attribution.track_id,
                             'face_track_id':face['track_id']})
                        status = 'saved' if saved else 'already_saved'
            voice_status = 'awaiting_usable_audio'
            if args['subject']=='self' and introduction_attribution.track_id:
                voice_status = 'recognized' if attribution.person_id else 'collecting_evidence'
            return {"person_id":person_id,"subject":args["subject"],"track_id":introduction_attribution.track_id if args["subject"]=="self" else None,
                    "fresh_voice_track":fresh_track,
                    "identity_basis":"self_report" if args['subject']=='self' else 'third_party_introduction',
                    "voice_status":voice_status,
                    "status":"hypothesis","face_track_id":face['track_id'] if face else None,
                    "face_status":status,"face_samples_saved":saved,"face_target_allowed":allowed},True
        if name == "record_claim":
            self._person(args["subject_id"])
            if args["kind"]=="third_party":
                reference = args["subject_reference"]
                matches = self.store.resolve(reference) if reference else []
                if not reference or label(reference) not in label(transcript.text) or len(matches)!=1 or matches[0]["id"]!=args["subject_id"]:
                    raise ValueError("ThirdPartySubjectNeedsUniqueExplicitReference")
            elif args["subject_reference"] is not None:
                raise ValueError("SelfReportDoesNotTakeNameReference")
            if args["kind"]=="self_report" and attribution.person_id is None:
                track = self.tracker.tracks.get(attribution.track_id)
                if not track or track.person_id!=args["subject_id"]:
                    raise ValueError("UnresolvedSelfReportSubject")
            if args["kind"] == "self_report" and attribution.person_id not in (None,args["subject_id"]):
                raise ValueError("SelfReportSubjectMismatch")
            supported = attribution.state == "supported" and attribution.person_id is not None
            status = "accepted" if supported else "candidate"
            if conn.execute("SELECT 1 FROM claims WHERE subject_id=? AND predicate=? AND status='accepted' AND value<>?",
                            (args["subject_id"],args["predicate"],args["value"])).fetchone():
                status = "candidate"  # A conflicting new claim never silently replaces the current one.
            claim_id = uuid4().hex
            conn.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?)",(claim_id,args["subject_id"],args["predicate"],args["value"],
                         attribution.person_id,attribution.track_id,turn_id,args["kind"],status,None,self.store.revision+1))
            return {"claim_id":claim_id,"status":status,"subject_id":args["subject_id"],"said_by":attribution.person_id,
                    "subject_name":self.store.person(args["subject_id"])["name"],"predicate":args["predicate"],"value":args["value"]},True
        if name in {"correct_claim","retract_claim"}:
            claim = conn.execute("SELECT * FROM claims WHERE id=?",(args["claim_id"],)).fetchone()
            if not claim or claim["status"] not in {"accepted","candidate"}:
                raise ValueError("UnknownOrRetiredClaim")
            if not attribution.person_id or attribution.person_id not in (claim["said_by"],claim["subject_id"]):
                raise ValueError("CorrectionNeedsSupportedSourceOrSubject")
            conn.execute("UPDATE claims SET status=? WHERE id=?",("superseded" if name=="correct_claim" else "retracted",claim["id"]))
            if name == "retract_claim":
                return {"claim_id":claim["id"],"status":"retracted"},True
            claim_id = uuid4().hex
            kind = "self_report" if attribution.person_id==claim["subject_id"] else "third_party"
            conn.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?)",(claim_id,claim["subject_id"],claim["predicate"],args["value"],
                         attribution.person_id,attribution.track_id,turn_id,kind,"accepted",claim["id"],self.store.revision+1))
            return {"claim_id":claim_id,"status":"accepted","supersedes":claim["id"],"subject_id":claim["subject_id"],
                    "subject_name":self.store.person(claim["subject_id"])["name"],"predicate":claim["predicate"],"value":args["value"]},True
        if name == "cancel_association":
            if not args["person_id"] and not args["track_id"]:
                raise ValueError("AssociationTargetRequired")
            matches = [e for e in self.enrollments.values() if
                       (not args["person_id"] or e.person_id==args["person_id"]) and
                       (not args["track_id"] or e.track_id==args["track_id"])]
            if not matches:
                raise ValueError("NoMatchingProvisionalAssociation")
            person_id = matches[0].person_id
            retired = self.store.person(person_id)["status"]=="provisional"
            if retired:
                conn.execute("UPDATE people SET status='retracted' WHERE id=?",(person_id,))
                conn.execute("DELETE FROM face_references WHERE person_id=?", (person_id,))
                conn.execute("UPDATE claims SET status='retracted' WHERE subject_id=? AND status='candidate'",(person_id,))
            conn.execute("DELETE FROM voice_candidates WHERE person_id=?", (person_id,))
            return {"person_id":person_id,"track_id":args["track_id"],"status":"cancelled"},True
        if name in {"rename_person","forget_person"}:
            person = self._person(args["person_id"])
            if person["id"] not in self.identity_targets(name):
                raise ValueError("SupportedSelfIdentityRequired")
            if name=="forget_person":
                conn.execute("DELETE FROM visual_observations")
                self.store.forget(conn,person["id"])
                return {"person_id":person["id"],"status":"forgotten"},True
            if len(args["name"].strip())>120 or any(ord(c)<32 for c in args["name"]):
                raise ValueError("InvalidDisplayLabel")
            conn.execute("UPDATE people SET name=?,aliases=? WHERE id=?",(args["name"],json.dumps(args["aliases"]),person["id"]))
            return {"person_id":person["id"],"status":"renamed"},True
        raise ValueError("UnknownTool")

    def _room(self, conn, name, args):
        """Room names come from what people say; the recognizer only collects views for them."""
        turn_id = self.current[0]
        if self.places is None: raise ValueError('RoomRecognitionUnavailable')
        self._source_allowed(args,name)
        rooms = {r['id']:r['name'] for r in conn.execute('SELECT id,name FROM places').fetchall()}
        def clean(value):
            value = ' '.join(str(value or '').split())
            if not 2 <= len(value) <= 60: raise ValueError('InvalidRoomName')
            return value
        if name == 'name_room':
            room = clean(args['name'])
            existing = next((k for k,v in rooms.items() if label(v)==label(room)),None)
            if existing is None:
                existing = uuid4().hex
                conn.execute('INSERT INTO places VALUES(?,?,?,?,NULL)',(existing,room,self.wall_clock(),turn_id))
            return {'room_id':existing,'name':rooms.get(existing,room),'created':existing not in rooms,'source_turn_id':turn_id,
                    'meaning':'Named. Rex is now collecting views of the room for up to a minute (looking around helps); '
                              'it is not learned until place.last_learning says learned. Do not say it is memorized.'}
        if args['room_id'] not in rooms: raise ValueError('UnknownRoom')
        room_id, action = args['room_id'], args['action']
        if action == 'not_this_room':
            return {'room_id':room_id,'action':action,'name':rooms[room_id],'source_turn_id':turn_id}
        if action == 'rename':
            new = clean(args['new_name'])
            if any(label(v)==label(new) and k!=room_id for k,v in rooms.items()): raise ValueError('RoomNameInUse')
            conn.execute('UPDATE places SET name=? WHERE id=?',(new,room_id))
            return {'room_id':room_id,'action':action,'old_name':rooms[room_id],'name':new,'source_turn_id':turn_id}
        conn.execute('DELETE FROM places WHERE id=?',(room_id,))   # Views and connections cascade.
        return {'room_id':room_id,'action':action,'name':rooms[room_id],'source_turn_id':turn_id,
                'meaning':'The room, every saved view of it and its connections are deleted.'}

    def _after(self,name,data):
        if name == 'name_room' and self.places:
            self.places.learn(data['room_id'],data['name'],data['created'])
        if name == 'correct_room' and self.places:
            if data['action'] == 'not_this_room': data['belief_dropped'] = self.places.reject(data['room_id'])
            else: self.places.forgotten(data['room_id'])
        if name in {'forget_person','cancel_association','reset_face_identity','correct_claim','retract_claim'}:
            self.conversation_intent.clear(name)
        if name=='remain_silent' and 'feedback' in data:
            self._after('conversation_feedback',data['feedback'])
        if name=='conversation_feedback':
            if self.initiative.invitations.feedback(data['action'],data.get('reference_id'),data['source_turn_id'],self.context(),self.trace.clock()):
                self.initiative.cancel_schedule('invitation_resolved')
            if data['action'] in {'topic_closed','topic_avoid','invitation_declined','yield_floor'}:
                # Yielding the floor to people talking pauses Rex's goal (the floor gate holds
                # his initiative); only a real "drop it" or a declined invitation ends it.
                self.initiative.cancel_schedule('human_floor' if data['action']=='yield_floor' else 'participant_boundary')
                self.conversation_intent.suspend('participant_boundary')
            if data['action'] in {'retract','floor_resolved'}:
                self.initiative.feedback = deque((r for r in self.initiative.feedback
                    if r['id'] != data['reference_id']), maxlen=32)
            else:
                # Proposed reply text is not evidence of actual delivery.
                if len(self.initiative.feedback)==32:
                    # Active explicit boundaries cannot disappear behind routine feedback.
                    expendable = next(r for r in self.initiative.feedback if r['action']!='topic_avoid')
                    self.initiative.feedback.remove(expendable)
                self.initiative.feedback.append({k:v for k,v in data.items() if k not in {'text','reason'}})
        if name in {'retract_visual','revise_visual'}:
            self.agency.clear(name)
            self.initiative.invitations.invalidate(name,erase=True)
        if name=='set_initiative':
            self.initiative.quiet = data['mode'] in ('quiet','silent')
            self.initiative.silent = data['mode']=='silent'
            self.initiative.policy_source = data['source_turn_id']
            if self.initiative.quiet:
                self.initiative.cancel_schedule('initiative_quiet')
                self.conversation_intent.suspend('initiative_quiet')
        if name=="introduce_person" and data.get("face_track_id"):
            key = data['face_track_id']
            self.face_associations[key] = data["person_id"]
            if data.get('face_target_allowed') and data['face_status']=='need_clear_compatible_views':
                self.face_learning[key] = {'person_id':data['person_id'],'source_turn':self.current[0],
                    'subject':data['subject'],'said_by':self.current[4].person_id,
                    'source_track':data.get('track_id') or self.current[4].track_id,'expires':self.current[1].ended_at+30}
            else:
                self.face_learning.pop(key,None)
            if data.get('face_samples_saved'):
                self.trace.emit('face_learning_saved',person_id=data['person_id'],samples=data['face_samples_saved'],revision=self.store.revision)
        if name in {"forget_person","cancel_association","reset_face_identity"}:
            self.face_learning = {k:v for k,v in self.face_learning.items() if v["person_id"]!=data.get("person_id")}
            if self.face_invalidator:
                self.min_face_epoch = self.face_invalidator()
                self.face_learning.clear()
                self.face_associations.clear()
            self.faces = ()
            self.face_associations = {k:v for k,v in self.face_associations.items() if v!=data.get("person_id")}
        turn_id,capture,transcript,observation,attribution = self.current
        if name=="introduce_person" and data["subject"]=="self" and data.get('track_id'):
            key = data['track_id']
            if data.get('fresh_voice_track'):
                self.tracker.tracks[key] = Track(key,capture.ended_at,deque([observation.embedding],maxlen=5))
            # Replace only provisional chains for this explicitly reintroduced name.
            for old_key, proposal in list(self.enrollments.items()):
                if proposal.person_id==data['person_id'] and old_key!=key:
                    self.enrollments.pop(old_key,None);self.evidence.pop(old_key,None)
                    self.tracker.tracks.pop(old_key,None)
                    with self.store.transaction() as conn:
                        conn.execute('DELETE FROM voice_candidates WHERE track_id=?',(old_key,))
            attribution = replace(attribution,track_id=key,state='supported' if attribution.person_id else 'hypothesis',
                                  reason='acoustic_match' if attribution.person_id else 'self_introduction',
                                  learning_eligible=capture.robot_microphone_hold is not False and observation.voiced_seconds>=1)
            self.current = (turn_id,capture,transcript,observation,attribution)
            with self.store.transaction() as conn:
                evidence = json.loads(conn.execute('SELECT evidence FROM turns WHERE id=?',(turn_id,)).fetchone()[0])
                evidence['association_person_id'] = data['person_id']
                evidence['introduction_track_id'] = key
                conn.execute('UPDATE turns SET evidence=? WHERE id=?',(json.dumps(evidence),turn_id))
            self.tracker.associate(attribution.track_id,data["person_id"],attribution.person_id==data["person_id"])
            if attribution.person_id is None and attribution.track_id not in self.enrollments:
                proposal = self.evidence.get(attribution.track_id)
                if proposal is None:
                    proposal = Enrollment(None,attribution.track_id,observation.embedding)
                # The introduction must align with each retained sample, not just recency.
                proposal.samples = [s for s in proposal.samples if cosine(s[2].embedding, observation.embedding) >= .55]
                proposal.digests = {hashlib.sha256(s[0].pcm).hexdigest() for s in proposal.samples}
                proposal.person_id = data["person_id"]
                proposal.anchor = observation.embedding
                proposal.updated_at = time.time()
                self.evidence[attribution.track_id] = proposal
                self.enrollments[attribution.track_id] = proposal
                self.persist_evidence(proposal)
                self.learn_observation()
        if name=='cancel_association':
            self.initiative.discard_association(data.get('person_id'),data.get('track_id'))
            if self.voice_references is not None and data.get('track_id'):
                self.voice_references.forget_track(data['track_id'])
            for key, proposal in self.enrollments.items():
                if proposal.person_id==data.get('person_id'):
                    self.initiative.discard_association(None,key)
        if name in {"cancel_association","forget_person"}:
            for key,e in list(self.enrollments.items()):
                if data.get("person_id")==e.person_id or data.get("track_id")==key:
                    del self.enrollments[key]
                    self.evidence.pop(key, None)
                    with self.store.transaction() as conn:
                        conn.execute("DELETE FROM voice_candidates WHERE track_id=?", (key,))
                    self.tracker.forget(e.person_id)
        if name=="forget_person":
            self.initiative.forget()
            self.tracker.forget(data["person_id"])
            self.impressions = type(self.impressions)()   # Performed parody of a forgotten person is not kept.
            if self.voice_references is not None:
                self.voice_references.close()
            self.pending = None
            self.attention.clear()
            self.attention.recent.clear()
            self.performances.clear()
            self.actions.clear()
            self.visual_selections.clear()
            self.last_inspection = None
            self.attention.scene.invalidate(self.trace.clock())
            self.forgotten = True
            self.audience_seen = None
            from .speakers import VoiceObservation,Attribution
            self.current = (turn_id,replace(capture,pcm=b""),transcript,
                            VoiceObservation(observation.namespace),Attribution(reason="person_forgotten"))
        if name in {"introduce_person","cancel_association","rename_person","forget_person","reset_face_identity"}:
            self.trace.emit("association_result",source_turn_id=turn_id,revision=self.store.revision,operation=name,**data)
        if name=="settle_question":
            self.trace.emit("question_settled",source_turn_id=turn_id,**data)
        if name in {"settle_question","introduce_person","record_claim","correct_claim","cancel_association"}:
            self.pending = None

    def learn_faces(self):
        if self.frozen: return
        from .face_attribution import learning_samples
        turn,capture,transcript,observation,attribution = self.current
        visible = {f['track_id']:f for f in self.faces}
        self.face_learning = {k:v for k,v in self.face_learning.items() if k in visible and capture.ended_at<=v['expires']}
        if not transcript.trusted or capture.echo_risk or observation.mixed or capture.overlap is True or capture.truncated or capture.parent_capture_id:
            return
        # A supported voice and sole clear face can associate an already named person
        # without requiring another introduction. Competing recent voices veto this.
        if attribution.person_id and len(self.faces)==1:
            from .face_attribution import target_allowed
            face = self.faces[0]
            competing = any(k!=attribution.track_id and 0<=capture.ended_at-t.last_at<=8 for k,t in self.tracker.tracks.items())
            samples = face.get('_samples',())
            blocked = self.store.rows('SELECT person_id FROM face_learning_blocks WHERE person_id=?', (attribution.person_id,)) if samples else []
            if (samples and not blocked and face.get('identity') in (None,attribution.person_id) and
                target_allowed(face,self.faces,'self',attribution,None,competing) and
                not self.store.face_profiles(samples[0].namespace).get(attribution.person_id)):
                self.face_learning.setdefault(face['track_id'], {'person_id':attribution.person_id,
                    'source_turn':turn,'subject':'self','said_by':attribution.person_id,
                    'source_track':attribution.track_id,'expires':capture.ended_at+30})
        for key,proposal in list(self.face_learning.items()):
            if proposal['subject']=='self' and attribution.track_id!=proposal['source_track']: continue
            samples,reason = learning_samples(visible[key],capture,proposal['person_id'],self.store)
            if not samples:
                if reason!='need_clear_compatible_views': self.face_learning.pop(key,None)
                continue
            try:
                with self.store.transaction() as conn:
                    saved = self.store.save_faces(conn,proposal['person_id'],samples,proposal['source_turn'],proposal)
                    revision = self.store.advance(conn) if saved else self.store.revision
                self.face_learning.pop(key,None)
                self.trace.emit('face_learning_saved',person_id=proposal['person_id'],samples=saved,revision=revision)
            except Exception as exc:
                self.trace.emit('face_learning_failed',error_type=type(exc).__name__)

    def learn_observation(self):
        # A storage failure must not discard the user's words or kill listening.
        try:
            self.collect_evidence()
            self.advance_enrollment()
        except Exception as exc:
            self.trace.emit("voice_learning_failed", error_type=type(exc).__name__)

    def persist_evidence(self, proposal):
        with self.store.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO voice_candidates VALUES(?,?,?,?,?)",
                (proposal.track_id, proposal.person_id, self.tracker.namespace, proposal.updated_at, json.dumps(proposal.encode())))
            if proposal.person_id:
                conn.executemany("UPDATE artifacts SET person_id=? WHERE capture_id=? AND person_id IS NULL",
                    [(proposal.person_id, sample[0].capture_id) for sample in proposal.samples])
            conn.execute("DELETE FROM voice_candidates WHERE track_id NOT IN (SELECT track_id FROM voice_candidates ORDER BY updated_at DESC LIMIT 12)")
        retained={r['track_id'] for r in self.store.rows('SELECT track_id FROM voice_candidates')}
        for key in set(self.evidence)-retained:
            self.evidence.pop(key,None)
            self.enrollments.pop(key,None)
        for key,track in self.tracker.tracks.items():
            track.retained=key in retained

    def collect_evidence(self):
        if self.frozen:
            return
        turn, capture, transcript, observation, attribution = self.current
        self.evidence = {k:v for k,v in self.evidence.items() if k in self.tracker.tracks}
        if attribution.person_id:
            self.grow_voice()
            return
        if not attribution.learning_eligible:
            if not attribution.person_id:
                self.trace.emit('voice_learning_deferred',capture_id=capture.capture_id,
                    reason=attribution.reason,
                    voiced_seconds=observation.voiced_seconds,consistency=observation.consistency)
            return
        proposal = self.evidence.get(attribution.track_id)
        if proposal is None:
            proposal = Enrollment(None, attribution.track_id, observation.embedding)
        if proposal.add(capture, transcript, observation, attribution, turn):
            self.evidence[attribution.track_id] = proposal
            self.persist_evidence(proposal)
        else:
            self.trace.emit('voice_learning_deferred',capture_id=capture.capture_id,
                            reason=proposal.last_reason)

    def grow_voice(self):
        """Add a clearly recognized, clean clip to that person's saved voice.

        Owner, October 13: voice evidence should build over time. Profiles stopped at the
        enrollment chain (Bret's held three clips, about seven voiced seconds), so every later
        night was matched against the same short samples. Growth goes through the enrollment
        writer with the enrollment exclusions (no motion, echo, overlap, split or doubtful
        transcript), a clear lead over everyone else, at most VOICE_GROWTH_PER_SESSION clips per
        person per run and the store's 20-recording cap. Near-duplicates add nothing."""
        turn_id,capture,transcript,observation,attribution = self.current
        person_id = attribution.person_id
        why = None
        ranked = attribution.candidates
        if attribution.reason != 'acoustic_match' or not attribution.learning_eligible or observation.motion:
            why = 'not_a_clean_still_match'
        elif observation.voiced_seconds < VOICE_GROWTH_SECONDS:
            why = 'too_short_to_grow_profile'
        elif not ranked or ranked[0][0] != person_id or ranked[0][1] < VOICE_GROWTH_SCORE:
            why = 'match_not_strong_enough'
        elif len(ranked) > 1 and ranked[0][1] - ranked[1][1] < VOICE_GROWTH_MARGIN:
            why = 'another_voice_too_close'
        elif capture.robot_microphone_hold is False or (self.require_microphone_hold and capture.robot_microphone_hold is not True):
            why = 'microphone_not_settled'
        elif self.voice_grown.get(person_id, 0) >= VOICE_GROWTH_PER_SESSION:
            why = 'enough_new_clips_this_run'
        else:
            try:
                self._source_allowed({}, 'grow_voice')
            except ValueError:
                why = 'untrusted_source'
        vector = unit(observation.embedding) if why is None else None
        if why is None and vector is None:
            why = 'no_embedding'
        if why is None:
            known = self.store.profiles(self.tracker.namespace).get(person_id, [])
            if any(cosine(vector, unit(ref) or ()) >= VOICE_GROWTH_DUPLICATE for ref in known):
                why = 'already_represented'
        if why is not None:
            self.trace.emit('voice_profile_unchanged', person_id=person_id, reason=why)
            return
        with self.store.transaction() as conn:
            before = conn.execute('SELECT count(*) FROM recordings WHERE person_id=?', (person_id,)).fetchone()[0]
            self.store.save_voice(conn, person_id, [(capture, transcript, observation, turn_id)],
                                  observation.namespace, acceptance='recognized_clean_match')
            after = conn.execute('SELECT count(*) FROM recordings WHERE person_id=?', (person_id,)).fetchone()[0]
            revision = self.store.advance(conn) if after > before else self.store.revision
        if after > before:
            self.voice_grown[person_id] = self.voice_grown.get(person_id, 0) + 1
            self.trace.emit('voice_profile_grown', person_id=person_id, recordings=after,
                            score=round(ranked[0][1], 3), voiced_seconds=round(observation.voiced_seconds, 2),
                            revision=revision)
        else:
            self.trace.emit('voice_profile_unchanged', person_id=person_id, reason='profile_full_or_duplicate')

    def advance_enrollment(self):
        if self.frozen:
            return
        turn_id,capture,transcript,observation,attribution = self.current
        if capture.robot_microphone_hold is False or (self.require_microphone_hold and capture.robot_microphone_hold is not True):
            return
        proposal = self.enrollments.get(attribution.track_id)
        if proposal:
            self.trace.emit("voice_learning_progress", **proposal.progress(), reason=proposal.last_reason)
            if proposal.ready:
                if self.require_microphone_hold and any(s[0].robot_microphone_hold is not True for s in proposal.samples):
                    self.trace.emit('voice_learning_deferred',reason='prior_samples_lack_robot_microphone_hold')
                    return
                try:
                    self._source_allowed({}, "rename_person")
                except ValueError:
                    return
                # Recheck earlier samples against voices discovered later in the exchange.
                rivals = {key: track.references for key, track in self.tracker.tracks.items()
                          if key != proposal.track_id}
                for sample in proposal.samples:
                    vectors = [sample[2].embedding, *(w[2] for w in sample[2].windows)]
                    for vector in vectors:
                        own = cosine(vector, proposal.anchor)
                        ranked = self.tracker.rank(vector, rivals)
                        if ranked and own - ranked[0][1] < self.tracker.policy.margin:
                            self.trace.emit("voice_learning_deferred", person_id=proposal.person_id,
                                            reason="competing_group_evidence")
                            return
                with self.store.transaction() as conn:
                    self.store.save_voice(conn,proposal.person_id,proposal.samples,observation.namespace)
                    conn.execute("DELETE FROM voice_candidates WHERE track_id=?", (proposal.track_id,))
                    revision = self.store.advance(conn)
                self.tracker.associate(proposal.track_id,proposal.person_id,True)
                self.tracker.tracks[proposal.track_id].retained=False
                del self.enrollments[proposal.track_id]
                self.evidence.pop(proposal.track_id, None)
                self.trace.emit("voice_learning_saved",person_id=proposal.person_id,samples=len(proposal.samples),revision=revision)

    def question_delivered(self,text,addressee_id,completed_at,face_track_id=None):
        self.pending = {"text":text,"addressee_id":addressee_id,"delivered_at":completed_at}
        if face_track_id: self.pending["face_track_id"] = face_track_id
        self.trace.emit("question_delivered",**self.pending)
