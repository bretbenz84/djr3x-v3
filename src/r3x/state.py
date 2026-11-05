"""One validator/executor for meaning proposals and all persistent transitions."""
from .field_guide import GUIDE
from dataclasses import asdict, replace
import hashlib
import json
import time
from collections import deque
from .speakers import Track, centroid, cosine, unit
from uuid import uuid4

from .contracts import ToolResult
from .enrollment import Enrollment
from .memory import CLAIM_INSERT, label, name_key, name_words
from .semantics import GAME_TOOLS, validate


# Saved-voice growth from recognized speech (owner, October 13: evidence builds over time).
VOICE_GROWTH_SECONDS = 2.     # V2's per-sample enrollment minimum.
VOICE_GROWTH_SCORE = .55      # The strong track-update level, above the .50 naming level.
VOICE_GROWTH_MARGIN = .10     # Lead over the next saved voice.
VOICE_GROWTH_PER_SESSION = 3
VOICE_GROWTH_DUPLICATE = .97  # Closer than this to a saved clip adds nothing new.
# Owner ruling, October 14: with one face in view, recognized, nobody else around and no other
# voice this visit, a voice this close to that person's profile is that person speaking. Live
# traces: Bret alone was attributed on 23 of 81 turns; misses scored a median .50.
INFER_VOICE_FLOOR = .40
# In-clip disagreement or another enrolled voice nearby is never settled by presence.
INFER_BLOCKING_REASONS = frozenset({'missing_or_wrong_namespace', 'echo_mixed_or_conflicting',
    'window_disagreement', 'short_or_untrusted', 'ambiguous_profiles', 'window_identity_conflict',
    'window_track_conflict', 'track_profile_conflict', 'motion_low_confidence'})
# Owner ruling, October 16: once Rex is sure it is Bret (a few clear voice matches), it stays Bret
# for the rest of the conversation, through motion noise and with his face too far to recognize;
# other voices score differently and are never tied to his. October 15 (2f85db5c): alone with
# Rex, 15 of his sentences went unattributed during Rex's own motion, scoring .15-.60.
# A near spelling of a name counts as that person only for someone named this recently, and only
# this close (October 18: Exodia .77 and Exotica .71 against the friend's saved spelling).
NAME_SPELLING_SECONDS = 1800.
# A fact about a pet ("he barks a lot") is about the pet named in this utterance or this long before.
PET_NAMED_SECONDS = 600.
NAME_SPELLING_RATIO = .7
# A position a person asked for is kept this long against Rex's own moves (October 18).
REQUESTED_POSITION_SECONDS = 120.
CARRY_CONFIRMATIONS = 2      # Clear acoustic matches in this conversation before the carry starts.
CARRY_GAP_SECONDS = 300.      # Nobody speaking this long ends the conversation (the track expiry).
# Clean clips of other voices and the television scored .19 or less against Bret's profile in
# every saved run; his own motion-masked clips scored .25 or more on 14 of 15 lost turns.
CARRY_VOICE_FLOOR = .25
# A person spoken about is linked by a name someone said this recently in the conversation, so
# "he plays the cello" right after "Sam Rivera is my partner" is about Sam (October 16).
MENTION_SECONDS = 600.
# Words that refer to someone without naming them; a person record is never made from them.
NOT_NAMES = frozenset({'he', 'she', 'him', 'her', 'his', 'hers', 'they', 'them', 'their', 'i', 'me', 'my', 'you',
                       'your', 'we', 'us', 'our', 'it', 'someone', 'somebody', 'everyone', 'nobody'})
# Owner ruling, October 20: someone introduced by another person ("This is Jeremy", "get to know
# Jeremy") whose face or voice Rex has not saved is learned from the one unknown face and the one
# unknown voice around, under that name. Live 515d71db: Jeremy was introduced and talked to for
# fifteen minutes, and nothing of his face or voice was gathered.
NEWCOMER_SECONDS = 1800.      # The visit; saying their name again extends it.
NEWCOMER_LOOKBACK = 120.      # A voice heard this long before the introduction can be theirs.
NEWCOMER_MIN_SECONDS = 1.5    # Real speech on the voice (as SpeakerTracker.other_voices).
NEWCOMER_MIN_CLIPS = 2        # One clip can be anyone's off moment: Bret's own tired line scored .30.
# A pooled voice this close to a saved one may be that person unrecognized. Live 1bfce8af
# (October 24): Jeremy's clips scored .16-.46 against Bret's profile (also on October 20) and his
# first clean clip .37, so a .30 ceiling kept his voice nameless all visit. Bret's own clips score
# .62-.76 against the rest of his voice and were named at .52-.68 live.
NEWCOMER_KNOWN_CEILING = .45
NEWCOMER_FACE_ANCHOR = .45    # Later views must look like the introduced face (FaceTracker.CONTINUITY).
NEWCOMER_FACE_VIEWS = 6       # The face_references cap per person.
# Unresolved for want of acoustic strength, not because the clip conflicts with itself or him.
# ambiguous_continuity is left out: the clip is close to two different voices of this visit.
CARRY_REASONS = frozenset({'motion_low_confidence', 'session_track', 'tentative_continuity', 'short_continuity',
                           'too_short_for_new_track', 'short_or_untrusted'})


# A fact told while the voice was unconfirmed is kept as a candidate (October 25); its interest news
# or follow-up waits until it is said again with the voice confirmed.
UNCONFIRMED_FACT = ('Saved as a candidate fact: told while the voice was not confirmed. It is part of what Rex '
                    'knows about them; nothing to tell them about saving.')


# October 17 (reply context diet): every reply carried each claim's full database row (source
# track, turn, revision, raw timestamps), about 570 characters each. The model needs the fact, who
# said it, its status and when it was told; the store keeps the rest for the tools.
CLAIM_FIELDS = ('id', 'subject_id', 'said_by', 'predicate', 'value', 'kind', 'status', 'mentions_id',
                'told', 'planned_time', 'followup')
PERSON_FIELDS = ('id', 'name', 'aliases', 'status')


def voice_onset(capture):
    """When the voice in a capture began (its pre-roll of quiet left out)."""
    return capture.started_at + (capture.voiced_intervals[0][0] if capture.voiced_intervals else 0.)


def public_claim(row, now):
    from .conversation_memory import told
    row = told(row, now)
    return {k: row[k] for k in CLAIM_FIELDS if row.get(k) is not None}


def public_person(row):
    return {k: row[k] for k in PERSON_FIELDS if k in row}


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
        self.frozen = frozen
        self.require_microphone_hold = require_microphone_hold
        self.voice_grown = {}   # person_id -> clips added to their saved voice this run.
        self.history_told = {}  # session_id -> personal_history sections looked up this conversation.
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
        self.weather = None          # weather.Weather: on-request lookups.
        self.systems_status = None   # Callable: battery and room readings (systems.report).
        self.scene_observer = None
        self.objects = None
        self.places = None
        self.photo_memory = None
        self.wall_clock = time.time
        from .sentry import Sentry
        self.sentry = Sentry(trace.clock, lambda: self.wall_clock())   # Sentry mode's guard (October 22).
        self.faces = ()
        self.face_associations = {}
        self.face_learning = {}
        self.face_invalidator = face_invalidator
        self.min_face_epoch = 0
        from .proactive import Initiative
        self.initiative = Initiative(trace)
        from .voice_modes import VoiceModes
        self.voice_modes = VoiceModes()   # Pride and AAVE modes (ported from V2, October 25).
        from .jeopardy import Jeopardy
        self.jeopardy = Jeopardy(trace.clock)   # V2's Jeopardy, hosted by Rex (owner, October 26).
        self._load_topic_boundaries()
        from .present import Present
        self.present = Present()   # Who is here, things around, the conversation clock.
        self._visits_noted = float('-inf')
        self._visit_ids = {}       # person_id -> (visit began, monotonic; its visits row id)
        # Whose voice this conversation has clearly matched: {'person', 'confirmed', 'last_at'}.
        self.voice_conversation = None
        self.named_people = {}   # person_id -> when their exact name was last said or they were introduced.
        self.named_pets = {}     # photo-memory pet id -> when a person last said its name.
        self.newcomers = {}      # person_id -> an introduced person's face and voice still being gathered.
        self.voices_taken = {}   # track_id -> whom presence or the carry took this voice for.
        from .agency import AgencyPlan
        self.agency = AgencyPlan(store, trace, self.present)
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
            offered_ids={context.get('photo_inspection',{}).get('evidence_id'),
                         *context.get('photo_inspection',{}).get('evidence_ids',())}
            if call.name=='remember_object' and call.arguments['evidence_id'] not in offered_ids-{None}:
                raise ValueError('PhotoEvidenceNotOffered')
            if call.name=='remember_object' and call.arguments.get('kind')=='pet' and (
                    self.store.resolve(call.arguments['name']) or self.near_named(call.arguments['name'])):
                # October 18: "This is Exodia" (a friend on the couch) was saved as a dog. The
                # roster decides this, mirroring NameBelongsToSavedPet for introductions.
                raise ValueError('NameBelongsToPerson')
            turn,capture,transcript,voice,attribution=self.current
            if not transcript.trusted or capture.echo_risk:raise ValueError('UntrustedPhotoTeaching')
            source={'turn_id':turn,'session_id':capture.session_id,'text':transcript.text,
                    'speaker_id':attribution.person_id,'speaker_track':attribution.track_id,
                    'attribution_state':attribution.state,'wall_time':self.wall_clock(),
                    'named_recently':self.recently_named_pets()}
            result=self.photo_memory.apply(call,source,self.trace.clock())
            if result.get('status')=='forgotten':
                # What he asked about a forgotten pet goes with it (curiosity.py).
                from .curiosity import forget_pet
                from .callouts import forget_pet as forget_pet_callout
                with self.store.transaction() as conn:
                    forget_pet(conn,result['entity_id'])
                    forget_pet_callout(conn,result['entity_id'])
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
        if (context.get('agency_step') or {}).get('kind', 'pose') != 'pose' and list(poses) not in (['visor_normal'], ['visor_up']):
            raise ValueError('OnlyHisOwnVisorThisBeat')   # A pose on another kind of beat raises the visor, nothing else.
        if any(p in LIGHT_STEPS and not (context.get('led_enabled') and LIGHT_STEPS[p] in context.get('led_patterns',()))
               for p in poses): raise ValueError('LEDPatternUnavailable')
        if call.arguments['look_after'] is not None and not context.get('inspection_enabled'):
            raise ValueError('InspectionUnavailable')
        return tuple(poses)

    def recently_named_pets(self):
        """Saved pets a person named in the last PET_NAMED_SECONDS, unless these words name another saved
        pet or object. Owner ruling October 23 (run 8e11d468): "Take another picture of him" and "He also
        has brown paws" right after "This is Max" were refused until he said the name again."""
        if self.photo_memory is None or self.current is None:
            return ()
        from .photo_memory import names_subject
        now, text = self.trace.clock(), self.current[2].text
        entities = self.photo_memory.data['entities']
        named_now = {k for k, e in entities.items() if names_subject(text, e['name'])}
        return tuple(k for k, t in self.named_pets.items()
                     if k in entities and now - t <= PET_NAMED_SECONDS and not named_now - {k})

    def _named_pet(self, reference):
        """The recently named saved pet a claim's subject names (recently_named_pets)."""
        if not isinstance(reference, str) or not reference.strip():
            return None
        from .photo_memory import names_subject
        entities = self.photo_memory.data['entities'] if self.photo_memory is not None else {}
        return next((k for k in self.recently_named_pets() if names_subject(reference, entities[k]['name'])), None)

    def _pet_fact(self, call, entity_id):
        """Append a person's fact about a saved pet to its photographic-memory notes."""
        from .contracts import ToolCall
        turn_id, capture, transcript, _, attribution = self.current
        entity = self.photo_memory.data['entities'][entity_id]
        args = call.arguments
        note = (args['value'] if args['predicate'].strip().casefold() in args['value'].casefold()
                else f"{args['predicate']}: {args['value']}")
        edit = ToolCall(call.call_id, 'edit_object_memory', {'entity_id': entity_id, 'action': 'correct',
                        'name': entity['name'], 'notes': note[:300], 'remove_notes': None,
                        'source_turn_id': turn_id, 'text': 'Saved on the pet record.'})   # Never spoken.
        source = {'turn_id': turn_id, 'session_id': capture.session_id, 'text': transcript.text,
                  'speaker_id': attribution.person_id, 'speaker_track': attribution.track_id,
                  'attribution_state': attribution.state, 'wall_time': self.wall_clock(),
                  'named_recently': (entity_id,)}
        request_id = capture.session_id + ":" + call.call_id
        if args['source_turn_id'] != turn_id:
            return ToolResult(request_id, 'rejected', 'SourceTurnMismatch', self.store.revision, {})
        try:
            data = self.photo_memory.apply(edit, source, self.trace.clock())
        except (ValueError, OSError) as exc:
            return ToolResult(request_id, 'rejected', str(exc), self.store.revision, {})
        return ToolResult(request_id, 'ok', 'saved_on_pet_record', self.store.revision,
                          {'pet': entity['name'], 'note': note[:300], 'status': data.get('status')})

    def _note_names_said(self, text):
        """Known people whose exact name or alias was said in this trusted utterance."""
        said = [w.casefold() for w in name_words(text)]
        now = self.trace.clock()
        named = []
        for person in self.store.people():
            for name in (person["name"], *person["aliases"]):
                wanted = [w.casefold() for w in name_words(name)]
                if wanted and any(said[i:i+len(wanted)] == wanted for i in range(len(said)-len(wanted)+1)):
                    self.named_people[person["id"]] = now
                    named.append(person["id"])
                    break
        return named

    def near_named(self, reference):
        """The one known person this conversation already named whose name this is a near spelling of.

        October 18: speech recognition wrote one friend's name as Exudica, Exodia, Exotica and
        "exude a cup" within minutes. Spelling alone cannot be trusted across conversations
        (Michael and Michelle score .80, Daniel and Danielle .86), so a near spelling counts only
        for someone named or introduced in the last NAME_SPELLING_SECONDS, and only when exactly
        one such person fits. Exact names never come here."""
        from difflib import SequenceMatcher
        heard = "".join(w.casefold() for w in name_words(reference or ""))
        if len(heard) < 4 or heard in NOT_NAMES:
            return None
        now = self.trace.clock()
        fits = []
        for person_id, at in list(self.named_people.items()):
            if now - at > NAME_SPELLING_SECONDS:
                continue
            person = self.store.person(person_id)
            if person is None:
                self.named_people.pop(person_id, None)
                continue
            for name in (person["name"], *person["aliases"]):
                known = "".join(w.casefold() for w in name_words(name))
                if (known and known != heard and known[0] == heard[0] and len(known) >= 4
                        and SequenceMatcher(None, known, heard).ratio() >= NAME_SPELLING_RATIO):
                    fits.append(person)
                    break
        return fits[0] if len(fits) == 1 else None

    def name_spellings(self, text):
        """Words of this utterance that are near spellings of someone named this conversation."""
        rows = []
        for word in dict.fromkeys(name_words(text or "")):
            if len(word) < 5 or self.store.resolve(word):
                continue
            person = self.near_named(word)
            if person:
                rows.append({"heard": word, "known_as": person["name"], "person_id": person["id"]})
        return rows

    def _keeping_requested_position(self, drive, steps, offered):
        """October 18 (50ab4eda): asked to face the owner, Rex lined up after two corrections; 30 s
        later his own "roll toward the doorway" beat turned him away ("Why did you turn to face
        away?"). For REQUESTED_POSITION_SECONDS after a person's requested move completes, his own
        moves may only face or approach a person."""
        now = self.trace.clock()
        recent = any(row.get('origin') == 'requested' and row.get('status') == 'completed'
                     and row.get('finished_at') is not None and 0 <= now - row['finished_at'] <= REQUESTED_POSITION_SECONDS
                     for row in drive.get('recent_motion') or ())
        if not recent:
            return False
        def toward_person(step):
            target = step.get('target')
            kind = (offered.get(target) or {}).get('kind')
            return step.get('action') in {'face', 'approach', 'find_person'} and (
                target in {'nearest_person', 'speaker'} or kind in {'face', 'nearest_person'})
        return not all(toward_person(step) for step in steps)

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
        if origin == 'self_initiated' and self._keeping_requested_position(drive, args['steps'], offered):
            raise ValueError('KeepingRequestedPosition')
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
            if (action == 'approach' and named == 'speaker' and 'speaker' not in offered
                    and 'nearest_person' in offered):
                # October 19 (b4ce6464): "come a little closer" with the owner in view 2.8 m
                # ahead became an approach to "speaker" while the microphone gave no direction,
                # and was refused. Coming to whoever asked is an approach to the nearest person,
                # which looks for them first; it names nobody.
                raw = {**raw, 'target': 'nearest_person'}
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
            if origin == 'self_initiated' and action == 'move' and raw['direction'] == 'backward':
                # October 16 (03bc4e1f): "give Brett some room" reversed him into a chair the two
                # narrow rear sensors missed. Until the rear depth sensor is fitted, only a person
                # can ask him to back up.
                raise ValueError('SelfInitiatedReverse')
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
        if args['timing']=='during_speech' and (args['text'] is None or args['pattern']=='blink'):
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
        if call.arguments['target'] not in {'left','right','up','down','forward','people','unfocused'} and not call.arguments['target'].startswith('visual:'):
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
        self.jeopardy.hold(self.trace.clock())   # A stop pauses the answer clock until someone carries on.

    def resume(self, current):
        """Make an earlier trusted turn current again, so the reply it is owed is that turn's: its words, its
        speaker evidence and its source for any save. Used only when the turn that yielded it was an
        untrusted sound (Session, October 26); that sound's own source stays stored and is never answered."""
        self.current = current

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
                capture.channel != 0 or
                (self.require_microphone_hold and capture.robot_microphone_hold is not True)):
            attribution = replace(attribution,learning_eligible=False)
        self.current = (turn_id,capture,transcript,observation,attribution)
        if transcript.trusted and not capture.echo_risk:
            self.conversation_source = turn_id
            for person_id in self._note_names_said(transcript.text):
                if person_id in self.newcomers:   # "Talk to Jeremy": still getting to know him.
                    self.newcomers[person_id]['until'] = capture.ended_at + NEWCOMER_SECONDS
            if self.photo_memory is not None:
                from .photo_memory import names_subject
                for key, entity in self.photo_memory.data['entities'].items():
                    if entity['kind'] == 'pet' and names_subject(transcript.text, entity['name']):
                        self.named_pets[key] = self.trace.clock()
        self.newcomers = {k: n for k, n in self.newcomers.items() if capture.ended_at <= n['until']}
        self.faces = tuple(f for f in faces if f.get("face_epoch",0)>=self.min_face_epoch)
        faces = self.faces
        # The picture of the present sees this turn's faces before any deduction reads it
        # (October 17: company seen at a turn must count against the next one's carry).
        self.present.saw_faces(faces, self.trace.clock())
        attribution = self.infer_speaker(attribution, capture, transcript, observation, faces)
        attribution = self.carry_speaker(attribution, capture, transcript, observation, faces)
        if attribution.inferred_person_id and attribution.track_id:
            self.voices_taken[attribution.track_id] = attribution.inferred_person_id   # Never a newcomer's.
        self.current = (turn_id,capture,transcript,observation,attribution)
        self.face_associations = {k:v for k,v in self.face_associations.items() if any(f["track_id"]==k for f in faces)}
        track = self.tracker.tracks.get(attribution.track_id) if attribution.reason!="tentative_continuity" else None
        self.store.source(turn_id,capture,transcript,attribution,observation,track.person_id if track else None)
        self.forgotten = False
        self.link_newcomer_voice()
        self.learn_observation()
        self.learn_faces()
        return attribution

    def infer_speaker(self, attribution, capture, transcript, observation, faces):
        """Layer presence over a near-threshold voice (owner, October 14). Mouth motion is not
        evidence. The result is state "inferred" with inferred_person_id; person_id stays the
        acoustic match, so voice and face learning, enrollment and identity administration,
        which all key on person_id or learning_eligible, never see it."""
        if attribution.person_id or attribution.reason in INFER_BLOCKING_REASONS:
            return attribution
        if (not transcript.trusted or capture.echo_risk or capture.truncated or capture.overlap is True
                or observation.motion or observation.mixed or observation.direction_conflict):
            return attribution
        from .present import followed
        faces = followed(faces)                      # A face caught in one frame is nobody.
        if len(faces) != 1 or (faces[0].get('face_width_px') or 48) < 48 or not faces[0].get('identity'):
            return attribution
        who = faces[0]['identity']
        person = self.store.person(who)
        ranked = attribution.candidates
        if not person or person['status'] != 'supported' or not ranked or ranked[0][0] != who:
            return attribution
        if ranked[0][1] < INFER_VOICE_FLOOR or (len(ranked) > 1 and ranked[0][1]-ranked[1][1] < self.tracker.policy.margin):
            return attribution
        from .present import COMPANY_SECONDS
        now = self.trace.clock()
        if not self.present.alone_with(who, now):
            return attribution
        if self.tracker.other_voices(who, self.store.profiles(self.tracker.namespace), now - COMPANY_SECONDS):
            return attribution
        return replace(attribution, state='inferred', reason='alone_with_recognized_face',
                       inferred_person_id=who, learning_eligible=False)

    def carry_speaker(self, attribution, capture, transcript, observation, faces):
        """Keep a clearly matched voice through the conversation (owner, October 16). After
        CARRY_CONFIRMATIONS acoustic matches, a later clip that still sounds most like that person
        (CARRY_VOICE_FLOOR) is theirs, through motion noise and with no face, unless someone else
        was heard or recognized lately. Like presence, the result is state "inferred": never
        acoustic identity, voice or face learning, enrollment or identity administration."""
        now = self.trace.clock()
        conversation = self.voice_conversation
        if conversation and now - conversation['last_at'] > CARRY_GAP_SECONDS:
            conversation = self.voice_conversation = None
        if attribution.person_id and attribution.state == 'supported':
            if conversation and conversation['person'] == attribution.person_id:
                conversation.update(confirmed=conversation['confirmed'] + 1, last_at=now)
                conversation['tracks'].add(attribution.track_id)
            else:
                self.voice_conversation = {'person': attribution.person_id, 'confirmed': 1, 'last_at': now,
                                           'tracks': {attribution.track_id}}   # Session tracks credited to him.
            return attribution
        if conversation and attribution.speaker_id == conversation['person']:
            conversation['last_at'] = now          # Presence named him: the conversation goes on.
            return attribution
        if (not conversation or conversation['confirmed'] < CARRY_CONFIRMATIONS or attribution.speaker_id
                or attribution.reason not in CARRY_REASONS):
            return attribution
        if (not transcript.trusted or capture.echo_risk or capture.truncated or capture.overlap is True
                or observation.mixed or observation.direction_conflict):
            return attribution
        who, ranked = conversation['person'], attribution.candidates
        if not ranked or ranked[0][0] != who or ranked[0][1] < CARRY_VOICE_FLOOR or (
                len(ranked) > 1 and ranked[0][1] - ranked[1][1] < self.tracker.policy.margin):
            return attribution
        from .present import COMPANY_SECONDS, followed
        if any(f.get('identity') not in (None, who) for f in faces) or len(followed(faces)) > 1:
            return attribution                       # Somebody else is in view (one far face may still be him).
        if self.present.recognized_other(who, now, COMPANY_SECONDS) or now - self.present.company_at < COMPANY_SECONDS:
            return attribution
        profiles = self.store.profiles(self.tracker.namespace)
        # October 17 review: the clip's own session track used to be left out of the other-voice
        # check, so a newcomer's second sentence, joining a track whose first was never his, was
        # credited to him. The own track is left out only while it holds nothing but this clip
        # (the floor above is then the whole test) or was itself credited to him earlier.
        own = self.tracker.tracks.get(attribution.track_id)
        own_is_his = own is not None and (len(own.references) <= 1 or attribution.track_id in conversation['tracks'])
        if [t for t in self.tracker.other_voices(who, profiles, now - COMPANY_SECONDS)
                if t != attribution.track_id or not own_is_his]:
            return attribution                       # Another voice this conversation.
        conversation['last_at'] = now
        conversation['tracks'].add(attribution.track_id)
        return replace(attribution, state='inferred', reason='same_voice_this_conversation',
                       inferred_person_id=who, learning_eligible=False)

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
        if name == "forget_person" and attribution.person_id:
            # Someone Rex knows only from what this speaker told him can be forgotten at their word.
            enrolling = {e.person_id for e in self.enrollments.values()}
            targets += [p for p in self.store.mention_only(attribution.person_id) if p not in enrolling]
        return list(dict.fromkeys(targets))

    def context(self, faces=()):
        turn_id,capture,transcript,observation,attribution = self.current
        track = self.tracker.tracks.get(attribution.track_id) if attribution.reason!="tentative_continuity" else None
        speaker_person = self.store.person(attribution.speaker_id) if attribution.speaker_id else None
        from .face_attribution import public_face
        modes = self.voice_modes.context(self.trace.clock())
        accent_line = self.voice_modes.line_allowed(self.trace.clock()) and not self.sentry.active
        game = self.jeopardy.context(self.trace.clock(), voice_onset(capture)) if self.jeopardy.active else None
        return self.note_audience({"source_turn_id":turn_id,"state_revision":self.store.revision,
                **({"jeopardy":game} if game else {}),
                "current_utterance":transcript.text,"calendar_date":self.calendar_today().isoformat(),
                "participation":self.initiative.context(self.trace.clock()),
                **({"voice_mode":modes} if modes else {}),
                **({"accent_line_allowed":True} if accent_line else {}),
                "identity_action_targets":{name:self.identity_targets(name) for name in ("rename_person","forget_person")},
                "speaker":{**asdict(attribution),"speaker_id":attribution.speaker_id,
                           "display_name":speaker_person["name"] if speaker_person else None,
                           "direction":observation.direction},
                "provisional_person_id":track.person_id if track else None,
                "name_spellings":self.name_spellings(transcript.text) if transcript.trusted else [],
                "people":[public_person(p) for p in self.store.people()],
                "claims":[public_claim(row, self.wall_clock()) for row in self.store.claims(include_candidates=True)],
                "pending_question":self.pending,"faces":[{**public_face(f),"person_hypothesis":self.face_associations.get(f["track_id"])} for f in self.faces],
                "face_enrollment":[{"face_track_id":k,"person_id":v["person_id"]} for k,v in self.face_learning.items()],
                "frozen_evaluation":self.frozen,
                "transcript_trusted":transcript.trusted,
                "acoustic_limits":{"overlap_detector":"window consistency only", "overlap":capture.overlap,
                    "speaker_boundary_proposed":capture.parent_capture_id is not None},
                "enrollment":[e.progress() for e in self.enrollments.values()]})

    def bit_told(self, session_id, person_id, premise, line):
        """A writer's-room bit Rex actually said, kept so it is not told again."""
        from . import jokes
        if self.frozen or self.store.connection is None:
            return
        with self.store.transaction() as conn:
            jokes.record(conn, session_id, person_id, jokes.audience_key(person_id), premise, line, 'told',
                         self.wall_clock())

    def note_audience(self, context):
        """The private audience for the memory gate, kept by the picture of the present."""
        return self.present.note_audience(context, self.trace.clock())

    def note_visits(self, now=None):
        """Keep, across runs, when each recognized person's visit began and when they were last in evidence
        (owner, October 31: someone seen 20 minutes apart and 20 days apart got the same hello)."""
        from . import familiarity
        now = self.trace.clock() if now is None else now
        if self.frozen or now - self._visits_noted < familiarity.VISIT_NOTE_SECONDS: return
        self._visits_noted = now
        wall = self.wall_clock()
        with self.present.lock:
            seen = {p: (self.present.visit_since.get(p, at), at) for p, at in self.present.recognized.items()
                    if now - at <= familiarity.VISIT_NOTE_SECONDS}
        rows = []
        for person, (since, at) in seen.items():
            visit = self._visit_ids.get(person)
            if visit is None or visit[0] != since:
                visit = self._visit_ids[person] = (since, uuid4().hex)
            rows.append((visit[1], person, wall - (now - since), wall - (now - at)))
        if not rows: return
        try:
            with self.store.transaction() as conn:
                familiarity.note_visits(conn, rows, wall)
        except Exception as exc:   # Someone forgotten meanwhile; the next note retries the rest.
            self.trace.emit('visits_note_failed', error_type=type(exc).__name__)

    def last_visit(self, person_id):
        """For a greeting: when Rex last saw them before this visit, and how often they come by."""
        from . import familiarity
        now, wall = self.trace.clock(), self.wall_clock()
        with self.present.lock:
            since = self.present.visit_since.get(person_id, now)
        return familiarity.last_visit(self.store, person_id, wall - (now - since), wall)

    def opportunity_context(self, event, *, advance_memory_cursor=True):
        """No acoustic observation/source turn is fabricated for an opportunity."""
        from .face_attribution import public_face
        if self.frozen: raise ValueError("FrozenEvaluationDisallowsProactiveSpeech")
        self.forgotten=False  # A new opportunity is a new turn, not the prior forget operation.
        if self.pending and event.at-self.pending['delivered_at']>120: self.pending=None
        faces = [public_face(f) for f in event.faces if f.get('face_epoch',0)>=self.min_face_epoch]
        people = self.store.people()
        person_ids = {p['id'] for p in people}
        from .sentry import SENTRY_KINDS
        if event.kind not in {'visual_arrival','pet_arrival','conversation_lull','ambient','unseen_presence','reconsideration',*SENTRY_KINDS}: raise ValueError('UnknownOpportunity')
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
        offered = candidates(self.store,faces,self.calendar_today(),self.memory_cursor,now=self.wall_clock())
        if advance_memory_cursor: self.memory_cursor += len(offered)
        modes = self.voice_modes.context(self.trace.clock())
        accent_line = (self.voice_modes.line_allowed(self.trace.clock()) and not self.sentry.active
                       and event.kind in {'conversation_lull','ambient','reconsideration'})
        return self.note_audience({'observations':cues,'calendar_date':self.calendar_today().isoformat(),'memory_opportunities':offered,'trigger':event.kind,'event_id':event.event_id,'state_revision':self.store.revision,
                'participation':self.initiative.context(self.trace.clock()),
                **({'voice_mode':modes} if modes else {}),   # His own lines keep a mode's delivery too.
                **({'accent_line_allowed':True} if accent_line else {}),
                'opportunity':{'kind':event.kind,'source_turn_id':event.source_turn_id,
                               'quiet_seconds':event.quiet_seconds,
                               'audience':'arrival_target' if event.kind=='visual_arrival' else
                                          'nobody_visible' if not faces else 'visible_group'},
                **({'visual_arrival':{'face_track_id':event.target_track,'person_id':event.person_id,
                                     'returning_this_session':event.returning,
                                     **({'last_visit':last} if (last := event.person_id and self.last_visit(event.person_id)) else {}),
                                     'startup_greeting':self.conversation_source is None and not event.returning
                                         and not any(r['heard_complete_phrases'] for r in self.initiative.outcomes)}} if event.kind=='visual_arrival' else {}),
                **({'pet_arrival':{'target_id':event.target_track,
                                   'detector_label':(event.pet or {}).get('label'),
                                   'saved_pets':[{'entity_id':e.get('entity_id'),'name':e.get('name')}
                                                 for e in (self.photo_memory.catalog() if self.photo_memory else ())
                                                 if e.get('kind')=='pet'],
                                   'meaning':GUIDE['pet_arrival']}}
                   if event.kind=='pet_arrival' else {}),
                'speaker':{'person_id':None,'state':'unresolved','reason':'no_speech_observed'},
                'faces':faces,'people':people,'claims':[],
                'pending_question':self.pending,'frozen_evaluation':self.frozen})

    def intent_context(self, context, *, human=False):
        from .conversation_intent import faces_for
        from .memory_opportunities import candidates
        memories = candidates(self.store, faces_for(context), self.calendar_today(), limit=128, include_delivered=True,
                              now=self.wall_clock())
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
            memories = candidates(self.store, faces_for(context), self.calendar_today(), limit=128, include_delivered=True,
                                  now=self.wall_clock())
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
                current=candidates(self.store,context['faces'],self.calendar_today(),limit=128,now=self.wall_clock())
                candidate=next((r for r in current if r['claim_id']==call.arguments['claim_id'] and r['claim_id'] in offered),None)
                if not candidate:raise ValueError('MemoryOpportunityUnavailable')
                data={'text':call.arguments['text'],'claim_id':candidate['claim_id'],'occasion':candidate['occasion']}
            elif call.name=='impersonate':
                data=self._beat_impression(dict(call.arguments),context)
            elif call.name=='voice_mode':
                # His own turn: only a line of his own in an accent; switching a mode answers a person.
                if call.arguments['switch']!='line' or call.arguments.get('requested', True) is not False:
                    raise ValueError('VoiceModeNeedsAPersonsWords')
                data=self._accent_line(call.arguments)
                self.voice_modes.line_at=self.trace.clock()
            elif call.name=='remain_silent': data={'reason':call.arguments['reason']}
            elif call.name=='jeopardy_setup':
                # A person asked and his reply was dropped unheard (owed_answer); his own goals never start a game.
                if not context.get('owed_answer'):raise ValueError('JeopardyNeedsAPersonsWords')
                data=self._plan_game(call.name,call.arguments,None,True)
                self.jeopardy.commit()
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

    def _load_topic_boundaries(self):
        """October 16 (03bc4e1f): "Stop asking me about it" held only until shutdown; the next
        run asked again. A person's "don't bring that up" now carries across runs."""
        for row in self.store.rows('SELECT * FROM topic_boundaries ORDER BY created_at DESC LIMIT 16'):
            self.initiative.feedback.appendleft({
                'id': row['id'], 'action': 'topic_avoid', 'reference_id': None, 'topic': row['topic'],
                'source_turn_id': row['source_turn'], 'source_text': row['source_text'],
                'participant': {'person_id': row['person_id'], 'track_id': None},
                'at': self.trace.clock(), 'expires_at': None, 'told_on': row['told_on'],
                'carried_from_earlier_run': True})

    def followups_asked(self, claim_ids):
        """Record follow-ups raised in a heard line, whatever tool carried it. Returns the
        claim ids newly recorded."""
        from .memory_opportunities import occasion
        asked = []
        for claim_id in dict.fromkeys(i for i in claim_ids if isinstance(i, str)):
            rows = self.store.rows('SELECT f.kind,f.on_date,f.due_at FROM memory_followups f JOIN claims c '
                                   "ON c.id=f.claim_id WHERE f.claim_id=? AND c.status='accepted'", (claim_id,))
            if not rows:
                continue
            key = occasion(rows[0]['kind'], rows[0]['on_date'], self.calendar_today(), rows[0]['due_at'], self.wall_clock())
            if key and not self.store.rows('SELECT 1 FROM memory_followup_delivery WHERE claim_id=? AND occasion=?',
                                           (claim_id, key)):
                self.memory_delivered({'claim_id': claim_id, 'occasion': key})
                asked.append(claim_id)
        return asked

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
            if self.frozen and call.name not in {"recall","recall_conversations","personal_history","news_today","weather","calendar","systems_status","ask_question","ask_face_question","settle_question","remain_silent"}:
                raise ValueError("FrozenEvaluationDisallowsMutation")
            if call.name == 'respond_with_intent':
                cancel.check()
                if revision != expected_revision:raise ValueError('StaleStateRevision')
                return ToolResult(request_id,'ok','executed',revision,{'text':call.arguments['text']})
            if call.name == 'record_claim' and call.arguments['kind'] == 'third_party':
                pet = self._named_pet(call.arguments.get('subject_reference'))
                if pet is not None:
                    # October 23 (1e626e07): "He's very feisty, and he likes to bark a lot" about Max was refused
                    # (NameDoesNotFitThatPerson) and the owner called it a programming limitation. A fact about a
                    # saved pet goes on that pet's record, as notes, with the words of the person who said it.
                    cancel.check()
                    if revision != expected_revision:raise ValueError('StaleStateRevision')
                    result = self._pet_fact(call, pet)
                    self.trace.emit("tool_result", name=call.name, **asdict(result))
                    return result
            if call.name in {'weather','calendar','systems_status'}:
                # Read-only lookups: nothing is written, and a network wait never holds the store.
                cancel.check()
                result = ToolResult(request_id,'ok','executed',revision,self._lookup(call.name,call.arguments,cancel))
                self.trace.emit("tool_result", name=call.name, **asdict(result))
                return result
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
                if call.name == 'introduce_person' and data.get('person_id') and not self.sentry.active:
                    # Someone with a callout just introduced (often added before they ever came over):
                    # this hello is their entrance (owner, October 25).
                    from . import callouts
                    row = conn.execute("SELECT * FROM callouts WHERE person_id=? AND status='on'",
                                       (data['person_id'],)).fetchone()
                    if row is not None:
                        data['callout'] = {**callouts.entry(conn,row,self._label(conn,data['person_id']),
                                                            self.wall_clock(),arrival=True),
                                           'meaning':'This person has a callout: when entrance_due is true, make your '
                                                     'hello their celebrity entrance (SITUATIONS callout_entrance).'}
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

    def _lookup(self, name, args, cancel):
        if name == 'calendar':
            from datetime import datetime
            from .dates import answer
            return answer(args, datetime.fromtimestamp(self.wall_clock()).astimezone())
        if name == 'weather':
            if self.weather is None:
                raise ValueError('WeatherUnavailable')
            return self.weather.lookup(args['city'], args['region'], args['country'], cancel)
        if self.systems_status is None:
            raise ValueError('SystemsStatusUnavailable')
        return self.systems_status()

    def _accent_line(self, args):
        """One line in an accent's (or the pirate's) voice (owner, October 25): asked for, or now and then his own."""
        from .voice_modes import LINE_MODES
        if args['mode'] not in LINE_MODES:raise ValueError('LineIsForAnAccent')
        if not (args.get('text') or '').strip():raise ValueError('AccentLineNeedsItsWords')
        requested = args.get('requested', True) is not False   # Left out: asked for.
        from .voice_modes import OWN_LINE_MODES
        if not requested and args['mode'] not in OWN_LINE_MODES:
            raise ValueError('OnlyWhenAskedFor')   # The DJ (owner, October 20: no DJ talk unprompted).
        if not requested and not self.voice_modes.line_allowed(self.trace.clock()):
            raise ValueError('AccentLineNotNow')
        return {'mode':args['mode'],'switch':'line','requested':requested,'text':args['text']}

    def _source_allowed(self, args, name):
        turn_id,capture,transcript,observation,attribution = self.current
        if args.get("source_turn_id",turn_id) != turn_id:
            raise ValueError("SourceTurnMismatch")
        if (not transcript.trusted or capture.echo_risk or capture.truncated or observation.mixed or capture.overlap is True
                or capture.parent_capture_id is not None):
            raise ValueError("UntrustedSource")
        # A self-report on a tentative track is settled by _unconfirmed_self, as a candidate (October 25).
        if attribution.reason=="tentative_continuity" and name in {"rename_person","forget_person"}:
            raise ValueError("TentativeTrackDoesNotAuthorizeSelfIdentity")

    def _unconfirmed_self(self, proposed):
        """Whose own fact this is when the voice was not confirmed: the person Rex is talking with,
        when the clip still sounds most like them and nobody else was heard or recognized lately.

        Owner, October 25 (a0afcf85): "I hate paperwork" and "I used to get breakfast at gas
        stations every morning before high school" were refused while Rex's own motion masked his
        voice (.36, .54), and Rex said he couldn't save them. The fact is kept as a candidate:
        said_by stays empty and the turn keeps the acoustic evidence, so it never confirms who
        spoke, enrolls a voice or replaces an accepted fact. A clip that sounds like nobody here,
        or another voice this visit, still keeps it off anyone's record."""
        from .present import COMPANY_SECONDS, followed
        turn_id,capture,transcript,observation,attribution = self.current
        now = self.trace.clock()
        track = self.tracker.tracks.get(attribution.track_id)
        if track and track.person_id and attribution.reason != 'tentative_continuity':
            # A voice still being learned under their name.
            if proposed not in (None, track.person_id):
                raise ValueError("UnresolvedSelfReportSubject")
            return self._person(track.person_id)["id"]
        conversation = self.voice_conversation
        if conversation and now - conversation['last_at'] <= CARRY_GAP_SECONDS:
            who = conversation['person']
        else:
            seen = [f.get('identity') for f in followed(self.faces)]
            who = seen[0] if len(seen) == 1 else None
        ranked = attribution.candidates
        if (not who or proposed not in (None, who) or not ranked or ranked[0][0] != who
                or ranked[0][1] < CARRY_VOICE_FLOOR
                or (len(ranked) > 1 and ranked[0][1] - ranked[1][1] < self.tracker.policy.margin)
                or self.present.recognized_other(who, now, COMPANY_SECONDS)):
            raise ValueError("UnresolvedSelfReportSubject")
        # The clip's own track counts against them unless it holds only this clip or was theirs
        # (as in carry_speaker).
        own_is_theirs = track is not None and (len(track.references) <= 1 or
                                               bool(conversation and attribution.track_id in conversation['tracks']))
        if [t for t in self.tracker.other_voices(who, self.store.profiles(self.tracker.namespace), now - COMPANY_SECONDS)
                if t != attribution.track_id or not own_is_theirs]:
            raise ValueError("UnresolvedSelfReportSubject")   # Another voice this visit.
        return self._person(who)["id"]

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
        from .proactive import HUMAN_FLOOR_SECONDS
        return {"id":uuid4().hex,"action":action,"reference_id":reference,"topic":topic,
                "source_turn_id":turn_id,"source_text":transcript.text[:500],
                "participant":participant,"at":now,"expires_at":now+HUMAN_FLOOR_SECONDS if action=="yield_floor" else None,
                "text":args["text"],"reason":"conversation_feedback"},True

    def _impersonate(self, conn, args, context):
        """Resolve the chosen voice to a reference clip the cloner can use. Nothing here
        is a fact or an identity: the parody is bounded, the voice comes only from
        offered targets, and a stranger's voice is this session's own clean samples."""
        from . import impersonation
        turn_id,capture,transcript,observation,attribution = self.current
        self._source_allowed(args,'impersonate')
        offered = (context or {}).get('impersonation') or {}
        if self.frozen or self.voice_references is None or not offered.get('available') or offered.get('beat'):
            raise ValueError('ImpersonationUnavailable')
        if not args['requested']:
            allowed, reason = self.impressions.spontaneous_allowed(self.trace.clock())
            if not allowed:
                raise ValueError('SpontaneousImpressionCooldown:' + str(reason))
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
        return self._impression(args, reference, kind, 'requested' if args['requested'] else 'cameo')

    def _beat_impression(self, args, context):
        """An impression beat of Rex's own (October 23): nobody asked, so the cameo budget
        applies; the voice comes only from those offered for this beat, and there is no
        current speaker to be 'self'."""
        from . import impersonation
        offered = context.get('impersonation') or {}
        if (self.frozen or self.voice_references is None or not offered.get('beat')
                or (context.get('agency_step') or {}).get('kind') not in {'impression', 'contribute'}):
            raise ValueError('ImpersonationUnavailable')
        if args['source_turn_id'] != context['event_id']:
            raise ValueError('SourceTurnMismatch')
        target = args['target']
        with self.store.transaction() as conn:
            if any(p['target_id']==target for p in offered.get('people',[])):
                person = self._person(target)
                if person['status']!='supported': raise ValueError('VoiceReferenceUnavailable')
                reference = self.voice_references.person_reference(self.store,conn,person['id'],person['name'])
                kind = 'person'
            elif target in offered.get('famous',[]):
                reference = impersonation.famous_voices().get(target)
                kind = 'famous'
            else:
                raise ValueError('ImpressionTargetNotOffered')
        return self._impression({**args, 'requested': False}, reference, kind, 'beat')

    def _impression(self, args, reference, kind, occasion):
        """The bounded performance for a resolved voice; nothing is performed yet."""
        from . import impersonation
        from .output import plain_text
        if reference is None:
            raise ValueError('VoiceReferenceUnavailable')
        if not args['requested']:
            # Voice by voice too: the same voice once an hour, whether in a reply or a beat.
            allowed, reason = self.impressions.spontaneous_allowed(self.trace.clock(), reference.key)
            if not allowed:
                raise ValueError('SpontaneousImpressionCooldown:' + str(reason))
        script = impersonation.cap_script(plain_text(args['script']),
            impersonation.MAX_SCRIPT_WORDS if args['requested'] else impersonation.CAMEO_MAX_SCRIPT_WORDS)
        if not script:
            raise ValueError('InvalidImpressionScript')
        intro, outro = ((args[key] if args[key] is not None and plain_text(args[key]) else None) for key in ('intro','outro'))
        return {'subject':reference.name,'voice':reference.key,'target_kind':kind,'script':script,
                'script_capped':script!=' '.join(plain_text(args['script']).split()),
                'intro':intro,'outro':outro,'requested':args['requested'],'occasion':occasion,
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
        if name == 'note_bit':
            # How Rex's own joke landed: a judgement about his material, never a fact about anyone.
            from . import jokes
            person = ((context or {}).get('personal_memory') or {}).get('person_id')
            line = (self.present.last_rex or {}).get('text', '')   # The line they reacted to.
            jokes.record(conn, capture.session_id, person, jokes.audience_key(person), args['premise'],
                         line, args['reaction'], self.wall_clock())
            return {'text':args['text'],'premise':args['premise'],'reaction':args['reaction']},True
        if name == 'callout':
            # Owner, October 25: anyone may add one, about themself, someone else or a saved pet.
            self._source_allowed(args,name)
            from . import callouts
            kind, subject = self._callout_subject(conn,args)
            about = (self.photo_memory.data['entities'][subject]['name'] if kind == 'pet'
                     else self._label(conn,subject))
            return callouts.apply(conn,kind,subject,about,args,said_by=attribution.speaker_id,turn_id=turn_id,
                                  source_text=transcript.text,now=self.wall_clock()),True
        if name == 'impersonate':
            return self._impersonate(conn,args,context), False
        if name == 'shut_down':
            self._source_allowed(args,name)   # Only a clearly heard, non-echo request from this turn.
            return {'text':args['text']}, False
        if name == 'sentry_mode':
            # Owner, October 22: a clearly heard request from this turn; code works out the countdown and end.
            self._source_allowed(args,name)
            if not (context or {}).get('sentry_available'):raise ValueError('SentryUnavailable')
            if self.sentry.active:raise ValueError('SentryAlreadyActive')
            from .sentry import plan
            chosen = plan(args, self.wall_clock())
            return {'countdown_seconds':chosen.countdown_seconds,'ends_at':chosen.ends_at,
                    'audio_alerts':chosen.audio_alerts,'requested_at':chosen.requested_at,
                    'until_text':chosen.until_text,**chosen.public()}, False
        if name in GAME_TOOLS:
            # Owner, October 26: the move is checked against the game here and takes effect once committed.
            return self._plan_game(name, args, voice_onset(capture), transcript.trusted), False
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
            data, mutation = self._feedback(args)
            person = data['participant']['person_id']
            if data['action'] == 'topic_avoid' and person:
                conn.execute('INSERT OR REPLACE INTO topic_boundaries VALUES(?,?,?,?,?,?,?)',
                             (data['id'], person, data['topic'], data['source_turn_id'], data['source_text'],
                              self.calendar_today().isoformat(), self.wall_clock()))
            elif data['action'] == 'retract':
                conn.execute('DELETE FROM topic_boundaries WHERE id=?', (data['reference_id'],))
            return data, mutation
        if name in {"stop_chiming_in","resume_chiming_in"}:
            self._source_allowed(args,name)
            return {"mode":args["mode"] if name=="stop_chiming_in" else "available","source_turn_id":turn_id,
                    "reason":"explicit_initiative_request"},False
        if name == 'voice_mode':
            # A clearly heard person's question or greeting this turn; no sentry guard in a camp voice.
            self._source_allowed(args,name)
            if self.sentry.active:raise ValueError('SentryModeActive')
            if args['switch'] == 'line':
                return self._accent_line(args), False
            from .voice_modes import MODE_SECONDS
            on = args['switch'] == 'on'
            return {'mode':args['mode'],'switch':args['switch'],'source_turn_id':turn_id,
                    'minutes':round(MODE_SECONDS/60) if on else 0,
                    # Pride's one-shot pose as it first comes on, as V2 threw it when the mode armed.
                    'flourish':on and args['mode']=='pride' and not self.voice_modes.on('pride',self.trace.clock())},False
        if name == "remember_followup":
            self._source_allowed(args,name)
            from .memory_opportunities import validate_date, resolve_due
            validate_date(args['kind'],args['on_date'])
            said_at=self.wall_clock()
            due_at=resolve_due(args['kind'],args['on_date'],args['due_in_minutes'],args['due_by'],said_at,self.calendar_today())
            # October 22 (1072c10a): "he turns thirty-six on November thirtieth", about the owner's
            # partner, was saved as the owner's birthday. Someone else's occasion is theirs, said by
            # the speaker, and comes back up with the speaker (memory_opportunities).
            about = args.get('subject_reference')
            data, _ = self._apply(conn,'record_claim',dict(subject_id=None if about else attribution.speaker_id,
                subject_reference=about,predicate=args['predicate'],value=args['value'],
                kind='third_party' if about else 'self_report',source_turn_id=turn_id,other_person=args.get('other_person')))
            if data['status']!='accepted' and data['said_by'] is None:
                return {**data,'meaning':UNCONFIRMED_FACT},True   # Kept, not followed up.
            if data['status']!='accepted':raise ValueError('FollowupNeedsAcceptedClaim')
            conn.execute('INSERT INTO memory_followups(claim_id,kind,on_date,audience,said_at,due_at) VALUES(?,?,?,?,?,?)',
                         (data['claim_id'],args['kind'],args['on_date'],args['audience'],said_at,due_at))
            from .memory_opportunities import clock
            return {**data,'followup_kind':args['kind'],'on_date':args['on_date'],'audience':args['audience'],
                    'due':clock(due_at) if due_at is not None else None},True
        if name in {'name_room','correct_room'}:
            return self._room(conn,name,args),True
        if name == "remember_interest":
            self._source_allowed(args,name)
            from .news import normalize_topic
            topic=normalize_topic(args['topic'])
            if not 2<=len(topic)<=80:raise ValueError('InvalidInterestTopic')
            # October 22: someone else's interest ("Jeremy loves to play volleyball") is theirs.
            about = args.get('subject_reference')
            subject = (self._mentioned(conn,about,None)[0] if about else
                       attribution.speaker_id or self._unconfirmed_self(None))
            saved=conn.execute("SELECT c.id,c.value FROM interests i JOIN claims c ON c.id=i.claim_id "
                               "WHERE c.subject_id=? AND c.status='accepted' AND i.topic=?",
                               (subject,topic)).fetchone()
            if saved:
                # Said again: already one of their interests, not a contradiction.
                return {'claim_id':saved[0],'status':'accepted','subject_id':subject,'value':saved[1],
                        'interest_topic':topic,'already_saved':True,
                        'meaning':'Already saved as their interest; nothing new was written.'},False
            # October 17: interests were saved under the model's label ("interest", "likes"), and a
            # second one with the same label read as contradicting the first, so it was refused. A
            # person has many interests: each gets a label of its own.
            data, _ = self._apply(conn,'record_claim',dict(subject_id=subject,
                subject_reference=about,predicate='interest: '+topic,value=args['value'],
                kind='third_party' if about else 'self_report',source_turn_id=turn_id))
            if data['status']!='accepted' and data['said_by'] is None:
                return {**data,'interest_topic':topic,'meaning':UNCONFIRMED_FACT},True   # No news search yet.
            if data['status']!='accepted':raise ValueError('InterestNeedsAcceptedClaim')
            conn.execute('INSERT INTO interests VALUES(?,?)',(data['claim_id'],topic))
            return {**data,'interest_topic':topic,
                    'meaning':('Saved as '+data['subject_name']+"'s interest, told by the speaker; news about it may be "
                               'offered while '+data['subject_name']+' is here.') if about else
                              'Saved as their own stated interest; fresh news about it may be offered while they are here.'},True
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
        if name == "news_today":
            if self.observations is None:
                raise ValueError("NewsUnavailable")
            from .news import interest_topics
            topics = list(interest_topics(self.store, self.faces))
            items = self.observations.listing(topics)
            return {'items': items, 'count': len(items),
                    'meaning': 'What Rex has read today; not live and not complete. A story absent here is one he has not seen.'}, False
        if name == "personal_history":
            from .personal_history import lookup
            told = self.history_told.setdefault(capture.session_id, set())
            data = lookup(args["section"])
            if args["section"] in told:
                # October 20 probes: told once per conversation in the persona, the Endor story was
                # still looked up and retold in three replies running.
                data = {**data, 'already_told': 'You looked this section up earlier in this conversation and told it. '
                        'Do not retell it or a variation of it; say something else entirely.'}
            told.add(args["section"])
            return data,False
        if name == "recall":
            if args["subject_id"]:
                self._person(args["subject_id"])
            data = {"claims":self.store.claims(args["subject_id"],include_candidates=bool(args["subject_id"]))}
            if args.get("query"):
                # "Do you know Sam?": everyone the name could mean, with how Rex knows them.
                _, compatible, _ = self.store.name_matches(args["query"])
                data["people"] = [self.store.acquaintance(p["id"]) for p in compatible[:3]]
                from . import callouts
                for person in data["people"]:
                    callout = callouts.describe(conn, person["person_id"])   # "What's PJ's bit?"
                    if callout: person["callout"] = callout
                # Facts are only what was saved; the conversations hold the rest (October 16).
                from . import conversation_memory as cm
                who = cm.subject(context or self.context())
                if who:
                    found = cm.recall(self.store, who, capture.session_id, query=args["query"], limit=6, now=self.wall_clock())
                    data["conversations"] = {k: found[k] for k in ('exchanges', 'searched', 'no_match', 'coverage',
                                                                    'legacy_source_utterances') if k in found}
                else:
                    data["conversations"] = {"unavailable": "No known private audience: earlier conversations stay private."}
            return data,False
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
            if not person_id and not matches:
                near = self.near_named(name_text)
                if near:
                    # October 18: the transcriber wrote one friend's name five ways in ten minutes.
                    # Someone named this conversation wins over a saved pet's near name.
                    person_id = near["id"]
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
            voice_usable = (capture.robot_microphone_hold is not False and capture.channel == 0 and
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
            self.named_people[person_id] = self.trace.clock()
            if args["aliases"]:
                # October 18: "She's also known as Tjoy" was filed as a reminder about the owner.
                # Another name for someone known joins their lookup names (display data only).
                known = self.store.person(person_id)
                names = {label(known["name"]), *(label(a) for a in known["aliases"])}
                fresh = [" ".join(a.split()) for a in args["aliases"]
                         if isinstance(a, str) and 0 < len(a.strip()) <= 120 and not any(ord(c) < 32 for c in a)
                         and label(a) not in names]
                if fresh:
                    conn.execute("UPDATE people SET aliases=? WHERE id=?",
                                 (json.dumps([*known["aliases"], *dict.fromkeys(fresh)][:8]), person_id))
            from .face_attribution import target_allowed, learning_samples
            face = next((f for f in self.faces if f["track_id"]==args["face_track_id"]),None)
            # The semantic owner may omit the face ID for an ordinary self-introduction.
            if face is None and args['subject']=='self' and len(self.faces)==1:
                face = self.faces[0]
            from .face_attribution import stranger
            if (args['subject']=='other' and face is not None and
                    (face.get('identity') not in (None,person_id) or face.get('resembles') not in (None,person_id))):
                # October 20 (515d71db): "This is Jeremy Thomas" was pinned on a blurry face that
                # scored .77 against Bret's saved face, beside the one face that matched nobody.
                strangers = [f for f in self.faces if stranger(f)]
                if len(strangers)==1:
                    face = strangers[0]
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
            elif args['subject']=='other':
                # Their own speech is gathered after the introduction (_welcome), never the introducer's.
                voice_status = ('recognized' if person_id in self.store.profiles(self.tracker.namespace) else
                                'collecting_evidence' if any(e.person_id==person_id for e in self.enrollments.values())
                                else 'listening_for_their_voice')
            return {"person_id":person_id,"subject":args["subject"],"track_id":introduction_attribution.track_id if args["subject"]=="self" else None,
                    "fresh_voice_track":fresh_track,
                    "identity_basis":"self_report" if args['subject']=='self' else 'third_party_introduction',
                    "voice_status":voice_status,
                    "status":"hypothesis","face_track_id":face['track_id'] if face else None,
                    "face_status":status,"face_samples_saved":saved,"face_target_allowed":allowed},True
        if name == "record_claim":
            other = args.get("other_person")
            if args["kind"]=="third_party":
                subject, subject_created = self._mentioned(conn,args["subject_reference"],args["subject_id"])
            elif args["subject_reference"] is not None:
                raise ValueError("SelfReportDoesNotTakeNameReference")
            elif attribution.speaker_id is None:
                subject, subject_created = self._unconfirmed_self(args["subject_id"]), False
            else:
                # A speaker's own fact is theirs even when the subject was left out (October 25).
                subject, subject_created = self._person(args["subject_id"] or attribution.speaker_id)["id"], False
            mentioned, mentioned_created = self._mentioned(conn,other,None) if other else (None, False)
            if mentioned == subject:
                raise ValueError("OtherPersonIsTheSubject")
            speaker = attribution.speaker_id
            if args["kind"] == "self_report" and speaker not in (None,subject):
                raise ValueError("SelfReportSubjectMismatch")
            # The turn's stored evidence keeps whether this was acoustic or inferred.
            supported = attribution.state in ("supported","inferred") and speaker is not None
            status = "accepted" if supported else "candidate"
            if conn.execute("SELECT 1 FROM claims WHERE subject_id=? AND predicate=? AND status='accepted' AND value<>?",
                            (subject,args["predicate"],args["value"])).fetchone():
                status = "candidate"  # A conflicting new claim never silently replaces the current one.
            claim_id = uuid4().hex
            conn.execute(CLAIM_INSERT,(claim_id,subject,args["predicate"],args["value"],
                         speaker,attribution.track_id,turn_id,args["kind"],status,None,self.store.revision+1,self.wall_clock(),
                         mentioned))
            data = {"claim_id":claim_id,"status":status,"subject_id":subject,"said_by":speaker,
                    "subject_name":self._label(conn,subject),"predicate":args["predicate"],"value":args["value"]}
            if subject_created:
                data["subject_record"] = "new name-only record: no voice or face learned"
            if mentioned:
                data["other_person"] = {"person_id":mentioned,"name":self._label(conn,mentioned),
                    "record":"new name-only record: no voice or face learned" if mentioned_created else "existing"}
            return data,True
        if name in {"correct_claim","retract_claim"}:
            claim = conn.execute("SELECT * FROM claims WHERE id=?",(args["claim_id"],)).fetchone()
            if not claim or claim["status"] not in {"accepted","candidate"}:
                raise ValueError("UnknownOrRetiredClaim")
            if not attribution.speaker_id or attribution.speaker_id not in (claim["said_by"],claim["subject_id"]):
                raise ValueError("CorrectionNeedsSupportedSourceOrSubject")
            if name == "correct_claim" and args["value"] == claim["value"]:
                # October 15 (2f85db5c): asked which room this was, the reply "corrected" the wine
                # plan to its own words; the fact then claimed the room question as its source.
                raise ValueError("CorrectionUnchanged")
            conn.execute("UPDATE claims SET status=? WHERE id=?",("superseded" if name=="correct_claim" else "retracted",claim["id"]))
            if name == "retract_claim":
                return {"claim_id":claim["id"],"status":"retracted"},True
            claim_id = uuid4().hex
            kind = "self_report" if attribution.speaker_id==claim["subject_id"] else "third_party"
            conn.execute(CLAIM_INSERT,(claim_id,claim["subject_id"],claim["predicate"],args["value"],
                         attribution.speaker_id,attribution.track_id,turn_id,kind,"accepted",claim["id"],self.store.revision+1,
                         self.wall_clock(),claim["mentions_id"]))
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

    @staticmethod
    def _label(conn, person_id):
        return conn.execute("SELECT name FROM people WHERE id=?",(person_id,)).fetchone()[0]

    def _named_recently(self, reference):
        """Whether someone said this name, as a name, in this conversation within MENTION_SECONDS."""
        words = name_words(reference)
        if not words or len(words)>4 or {w.casefold() for w in words} & NOT_NAMES:
            return False
        turn_id,capture,transcript,observation,attribution = self.current
        heard = [transcript.text] + [r["text"] for r in self.store.rows(
            "SELECT text,evidence FROM turns WHERE session_id=? AND id<>? AND created_at BETWEEN ? AND ? ORDER BY created_at DESC LIMIT 40",
            (capture.session_id,turn_id,capture.ended_at-MENTION_SECONDS,capture.ended_at))
            if json.loads(r["evidence"]).get("trusted") and not json.loads(r["evidence"]).get("echo_risk")]
        wanted = [name_key(w) for w in words]
        # The saved spelling of a name said a little differently counts as said (October 26: "Exodia" was
        # heard, the model used her saved "Exudica", and her callout was refused as a name nobody said).
        saved = "".join(wanted) if self.store.resolve(reference) else None
        from difflib import SequenceMatcher
        for text in heard:
            said = name_words(text)
            for i in range(len(said)-len(wanted)+1):
                window = said[i:i+len(wanted)]
                # A name is written as one: "my partner" or "the boss" are not names.
                if not all(w[:1].isupper() for w in window):
                    continue
                keys = [name_key(w) for w in window]
                if keys == wanted:
                    return True
                heard_as = "".join(keys)
                if (saved and len(saved) >= 4 and heard_as[:1] == saved[:1]
                        and SequenceMatcher(None, heard_as, saved).ratio() >= NAME_SPELLING_RATIO):
                    return True
        return False

    def _mentioned(self, conn, reference, proposed_id):
        """The person a spoken name refers to: (person_id, created).

        A name said in the conversation links to the one known person it fits, or makes a
        name-only record when it fits nobody. That record has no voice or face; only that
        person's own introduction can start learning them. Never the current speaker.
        """
        if not isinstance(reference,str) or not reference.strip() or not self._named_recently(reference):
            raise ValueError("MentionNeedsANameSaidInThisConversation")
        name_text = " ".join(reference.split())
        # "Sam" with both a Sam and a Sam Rivera known is a question, even though one fits exactly.
        exact, candidates, overlapping = self.store.name_matches(name_text)
        if proposed_id is not None:
            self._person(proposed_id)
            if [p["id"] for p in candidates]!=[proposed_id]:
                raise ValueError("NameDoesNotFitThatPerson")
        if len(candidates)>1:
            raise ValueError("AmbiguousName")
        created = False
        if candidates:
            person_id = candidates[0]["id"]
            person = candidates[0]
            longer = len(name_words(name_text)) > max(len(name_words(n)) for n in (person["name"],*person["aliases"]))
            if not exact and longer and len(person["aliases"])<8:
                # "Sam Rivera" for a record named "Sam": keep the fuller name too.
                conn.execute("UPDATE people SET aliases=? WHERE id=?",(json.dumps([*person["aliases"],name_text]),person_id))
        elif overlapping:
            raise ValueError("PossiblySameAsAKnownPerson")
        elif (near := self.near_named(name_text)) is not None:
            person_id = near["id"]
        elif (spelled := self._spelled_like(name_text)) is not None:
            # October 26 probe: "Exodia" (as heard) made a second person beside the saved Exudica. A near
            # spelling of someone not named lately is asked about, never assumed and never a new record.
            raise ValueError("PossiblySameAsAKnownPerson:" + spelled["name"])
        else:
            if self.photo_memory is not None and any(e['kind']=='pet' and label(e['name'])==label(name_text)
                                                     for e in self.photo_memory.catalog()):
                raise ValueError("NameBelongsToSavedPet")
            person_id, created = self.store.add_person(conn,name_text,[],self.current[0]), True
        if person_id in (self.current[4].speaker_id, self.current[4].person_id):
            raise ValueError("MentionedPersonIsTheSpeaker")
        return person_id, created

    def _spelled_like(self, reference):
        """The one saved person whose name this is a near spelling of, by the near_named test but whenever
        they were last named; None when nobody or several fit."""
        from difflib import SequenceMatcher
        heard = "".join(name_key(w) for w in name_words(reference or ""))
        if len(heard) < 4 or heard in NOT_NAMES:
            return None
        fits = [p for p in self.store.people() if any(
            (known := "".join(name_key(w) for w in name_words(n))) and known[0] == heard[0] and len(known) >= 4
            and SequenceMatcher(None, known, heard).ratio() >= NAME_SPELLING_RATIO for n in (p["name"], *p["aliases"]))]
        return fits[0] if len(fits) == 1 else None

    def _callout_subject(self, conn, args):
        """Whose callout a call means: ('person' or 'pet', id). Adding one to someone not known yet makes a
        name-only record, like a fact about them; the other actions only find a callout that exists."""
        from . import callouts
        attribution = self.current[4]
        if args['callout_id']:
            return callouts.subject_of(conn, args['callout_id'])
        if args['subject'] == 'speaker':
            return 'person', attribution.speaker_id or self._unconfirmed_self(None)
        name = args['name']
        if not isinstance(name, str) or not name.strip():
            raise ValueError('CalloutNeedsAName')
        if args['subject'] == 'pet':
            entities = self.photo_memory.data['entities'] if self.photo_memory is not None else {}
            pet = self._named_pet(name) or next((k for k, e in entities.items()
                                                 if e['kind'] == 'pet' and label(e['name']) == label(name)), None)
            if pet is None:
                raise ValueError('NoSavedPetByThatName')
            return 'pet', pet
        if args['action'] == 'add':
            try:
                return 'person', self._mentioned(conn, name, None)[0]
            except ValueError as exc:
                if str(exc) != 'MentionedPersonIsTheSpeaker':
                    raise
                return 'person', attribution.speaker_id or attribution.person_id
        _, compatible, _ = self.store.name_matches(name)
        near = self.near_named(name)
        found = callouts.with_callout(conn, list(dict.fromkeys([p['id'] for p in compatible] + ([near['id']] if near else []))))
        if len(found) > 1:
            raise ValueError('AmbiguousName')
        if not found:
            raise ValueError('NoCalloutForThem')
        return 'person', found[0]

    def callout_context(self, context):
        """The callouts switched on for the people and pets around now, for the reply model."""
        if self.frozen or self.sentry.active or self.store.connection is None:
            return None
        from . import callouts
        people = {f['identity'] for f in context.get('faces') or () if f.get('identity')}
        people.add((context.get('speaker') or {}).get('speaker_id'))
        people.add((context.get('personal_memory') or {}).get('person_id'))
        arrival = (context.get('visual_arrival') or {}).get('person_id')
        people.add(arrival)
        # Someone talked about lately is shown too, so "also work in the cowbells" or "never call him that
        # again" can find their callout (hosted probe, October 25: PJ was out of view and both were lost).
        now = self.trace.clock()
        mentioned = {p for p, at in self.named_people.items() if now - at <= MENTION_SECONDS} - people
        entities = self.photo_memory.data['entities'] if self.photo_memory is not None else {}
        pets = set(self.recently_named_pets())
        if context.get('trigger') == 'pet_arrival':
            pets |= {k for k, e in entities.items() if e['kind'] == 'pet'}   # A look says which one it is.
        arriving = ({arrival} if context.get('trigger') == 'visual_arrival' and arrival else
                    pets if context.get('trigger') == 'pet_arrival' else set())
        with self.store.lock:
            conn = self.store.connection
            names = {p: row[0] for p in sorted((people | mentioned) - {None})
                     if (row := conn.execute('SELECT name FROM people WHERE id=?', (p,)).fetchone())}
            return callouts.context(conn, names, {k: entities[k]['name'] for k in sorted(pets) if k in entities},
                                    arriving, self.wall_clock(), away=mentioned)

    def callout_heard(self, offered, line):
        """A line of his that was heard: the offered callouts it used, kept for pacing and variety."""
        if self.frozen or self.store.connection is None or not offered or not line:
            return []
        from . import callouts
        with self.store.transaction() as conn:
            return callouts.heard(conn, offered.get('here'), line, self.wall_clock())

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

    def _plan_game(self, name, args, speaker_at, trusted):
        if self.sentry.active:raise ValueError('SentryModeActive')
        people = {}
        for spoken in args.get('players') or ():
            # Display and gaze only, never an identity claim: one exact or one compatible known name.
            exact, compatible, _ = self.store.name_matches(spoken)
            match = exact if len(exact) == 1 else compatible if not exact and len(compatible) == 1 else []
            people[' '.join(str(spoken).split())] = match[0]['id'] if match else None
        parts = self.jeopardy.plan(name, args, speaker_at=speaker_at, people=people, trusted=trusted)
        return {'parts':parts,'text':args.get('text')}

    def _after(self,name,data):
        if name in GAME_TOOLS:
            self.jeopardy.commit()
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
        if name == 'voice_mode' and data['switch'] == 'line':
            if not data['requested']:
                self.voice_modes.line_at = self.trace.clock()
        elif name == 'voice_mode':
            self.voice_modes.switch(data['mode'], data['switch']=='on', self.trace.clock())
        if name == 'sentry_mode':
            self.voice_modes.clear()   # He guards in his own voice and red lights.
        if name == 'callout' and data['subject_kind'] == 'person' and data['status'] != 'removed':
            self.named_people[self.store.rows('SELECT person_id FROM callouts WHERE id=?', (data['callout_id'],))[0]
                              ['person_id']] = self.trace.clock()   # Just talked about: follow-ups find it.
        if name in {'stop_chiming_in','resume_chiming_in'}:
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
        if name=="introduce_person" and data["subject"]=="other":
            self._welcome(data)
        if name in {"cancel_association","forget_person"}:
            self.newcomers.pop(data.get("person_id"), None)
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
            self.voices_taken = {k: p for k, p in self.voices_taken.items() if p != data["person_id"]}
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
            self.present.forget(data["person_id"])
            self._visit_ids.pop(data["person_id"], None)   # Their visits rows went with them (cascade).
            from .speakers import VoiceObservation,Attribution
            self.current = (turn_id,replace(capture,pcm=b""),transcript,
                            VoiceObservation(observation.namespace),Attribution(reason="person_forgotten"))
        if name in {"introduce_person","cancel_association","rename_person","forget_person","reset_face_identity"}:
            self.trace.emit("association_result",source_turn_id=turn_id,revision=self.store.revision,operation=name,**data)
        if name=="settle_question":
            self.trace.emit("question_settled",source_turn_id=turn_id,**data)
        if name in {"settle_question","introduce_person","record_claim","correct_claim","cancel_association"}:
            self.pending = None

    def _welcome(self, data):
        """Open, or renew, the window in which an introduced person's face and voice are
        gathered (owner ruling, October 20). Nothing is opened for what is already saved."""
        if self.frozen:
            return
        from .face_attribution import appearance, stranger
        turn_id, capture, transcript, observation, attribution = self.current
        person_id = data['person_id']
        voice = person_id not in self.store.profiles(self.tracker.namespace)
        face = not data.get('face_samples_saved') and not self.store.rows(
            'SELECT 1 FROM face_references WHERE person_id=? LIMIT 1', (person_id,))
        if not voice and not face:
            self.newcomers.pop(person_id, None)
            return
        newcomer = self.newcomers.setdefault(person_id, {
            'person_id': person_id, 'since': capture.ended_at, 'introducers': set(), 'anchor': None, 'pool': [],
            'voice_track': None})
        newcomer.update(voice=voice, face=face, source_turn=turn_id, said_by=attribution.person_id,
                        until=capture.ended_at + NEWCOMER_SECONDS)
        if attribution.track_id:
            newcomer['introducers'].add(attribution.track_id)   # The introducer's voice is never theirs.
        target = next((f for f in self.faces if f['track_id'] == data.get('face_track_id')), None)
        if newcomer['anchor'] is None and target is not None and stranger(target):
            # The face the introduction pointed at, even blurry, keeps a different face out later.
            newcomer['anchor'] = appearance(target)
        self.trace.emit('newcomer_welcomed', person_id=person_id, gathering_voice=voice, gathering_face=face,
                        face_anchor=newcomer['anchor'] is not None)
        self.link_newcomer_voice()

    def link_newcomer_voice(self):
        """Give the one unknown voice around the name of the one person introduced and still
        without a voice (owner ruling, October 20).

        Only an unnamed session voice with real speech on two clips, heard since shortly before
        the introduction, that is not close to a saved voice (NEWCOMER_KNOWN_CEILING), was not the
        introducer's and was not already taken for someone by presence or the carry. Another
        such voice heard twice, or two people waiting, links nothing and undoes a link until
        that voice has gone. The link is a hypothesis: the existing enrollment chain still needs
        three compatible turns and eight voiced seconds, rechecked against every other voice
        of the visit, before a voice is saved. Why nothing is linked yet goes to the log once."""
        if self.frozen or not self.newcomers:
            return
        profiles = self.store.profiles(self.tracker.namespace)
        for newcomer in self.newcomers.values():
            if newcomer['voice'] and newcomer['person_id'] in profiles:
                newcomer['voice'], newcomer['voice_track'] = False, None   # Enrollment saved it.
        self.newcomers = {k: n for k, n in self.newcomers.items() if n['voice'] or n['face']}
        waiting = [n for n in self.newcomers.values() if n['voice']]
        if len(waiting) != 1:
            return
        newcomer = waiting[0]
        if newcomer['voice_track'] not in self.tracker.tracks:
            newcomer['voice_track'] = None   # The linked voice expired with its evidence.
        chain = next((k for k, e in self.enrollments.items() if e.person_id == newcomer['person_id']), None)
        if chain is not None and chain != newcomer['voice_track']:
            newcomer['voice'] = False   # Their own introduction started this chain; it is not ours.
            return
        candidates, passed = [], []
        for key, track in self.tracker.tracks.items():
            if (track.person_id is not None or key in newcomer['introducers'] or not track.references
                    or track.last_at < newcomer['since'] - NEWCOMER_LOOKBACK
                    or track.voiced_seconds < NEWCOMER_MIN_SECONDS):
                continue
            ranked = self.tracker.rank(centroid(list(track.references)), profiles)
            if key in self.voices_taken:
                passed.append((track.voiced_seconds, key, 'taken_for_someone_saved', None))
            elif ranked and ranked[0][1] >= NEWCOMER_KNOWN_CEILING:
                passed.append((track.voiced_seconds, key, 'close_to_a_saved_voice', round(ranked[0][1], 2)))
            else:
                candidates.append(key)
        def rivals(key):
            # Another voice heard on two clips or more; one odd clip (a laugh) can split a voice.
            return [k for k in candidates if k != key and len(self.tracker.tracks[k].references) >= 2]
        if newcomer['voice_track'] is not None:
            if rivals(newcomer['voice_track']):
                self._unlink_newcomer_voice(newcomer, 'another_unknown_voice')
            return
        if not candidates:
            if passed:
                seconds, key, reason, score = max(passed)
                self._newcomer_waiting(newcomer, 'voice', reason, track_id=key, score=score)
            return
        key = max(candidates, key=lambda k: self.tracker.tracks[k].voiced_seconds)
        track = self.tracker.tracks[key]
        if rivals(key):
            self._newcomer_waiting(newcomer, 'voice', 'two_unknown_voices', track_id=key)
            return
        if len(track.references) < NEWCOMER_MIN_CLIPS:
            self._newcomer_waiting(newcomer, 'voice', 'one_clip_so_far', track_id=key)
            return
        newcomer['voice_waiting'] = None
        self.tracker.associate(key, newcomer['person_id'])
        proposal = self.evidence.get(key) or Enrollment(None, key, centroid(list(track.references)))
        proposal.person_id = newcomer['person_id']
        proposal.updated_at = time.time()
        self.evidence[key] = proposal
        self.enrollments[key] = proposal
        self.persist_evidence(proposal)
        newcomer['voice_track'] = key
        self.trace.emit('newcomer_voice_linked', person_id=newcomer['person_id'], track_id=key,
                        samples=len(proposal.samples), voiced_seconds=round(track.voiced_seconds, 2))

    def _unlink_newcomer_voice(self, newcomer, reason):
        """Take the name back off a linked voice; its clips stay as unnamed evidence."""
        key = newcomer['voice_track']
        track = self.tracker.tracks.get(key)
        if track is not None and track.person_id == newcomer['person_id']:
            track.person_id, track.association = None, 'unresolved'
        proposal = self.enrollments.pop(key, None)
        if proposal is not None:
            with self.store.transaction() as conn:
                conn.executemany('UPDATE artifacts SET person_id=NULL WHERE capture_id=? AND person_id=?',
                                 [(s[0].capture_id, newcomer['person_id']) for s in proposal.samples])
            proposal.person_id = None
            self.persist_evidence(proposal)
        newcomer['voice_track'] = None
        self.trace.emit('newcomer_voice_unlinked', person_id=newcomer['person_id'], track_id=key, reason=reason)

    def _newcomer_waiting(self, newcomer, kind, reason, **data):
        """Log once, not every turn, why an introduced person's voice or face is not gathered yet."""
        if newcomer.get(kind + '_waiting') != reason:
            newcomer[kind + '_waiting'] = reason
            self.trace.emit(f'newcomer_{kind}_waiting', person_id=newcomer['person_id'], reason=reason, **data)

    def gather_newcomer_face(self):
        """Pool clear views of the one stranger's face in view for the one introduced person
        still without a face, across the camera's track changes (owner ruling, October 20;
        that night face tracks lasted a median half second). Views must look like the face
        the introduction pointed at, when it pointed at one, and like each other, and no saved
        person may rival them; then they are saved under the introduced name.

        October 24 (1bfce8af): with no face pointed at, the first unknown face after the
        introduction, often a blurry glimpse, became the reference, and four minutes of clear
        views of the guest were turned away without a word in the log. Now only clear views
        count and none becomes a reference by itself: the first clear views start the pool, a
        lone earlier view that nothing since matches (an odd angle) gives way to newer ones,
        and why views wait is logged once."""
        from .face_attribution import clear_views, stranger, vetted_samples
        turn, capture, transcript, observation, attribution = self.current
        waiting = [n for n in self.newcomers.values() if n['face']]
        if len(waiting) != 1:
            return
        newcomer = waiting[0]
        strangers = [f for f in self.faces if stranger(f)]
        views = [[s for s in clear_views(f) if s.session_id == capture.session_id] for f in strangers]
        if len(strangers) > 1 and any(views):
            self._newcomer_waiting(newcomer, 'face', 'more_than_one_unknown_face', faces=len(strangers))
        if len(strangers) != 1:
            return
        anchor, pool = newcomer['anchor'], newcomer['pool']
        frames = {s.frame_id for s in pool}
        fresh = [s for s in views[0] if s.frame_id not in frames]
        if not fresh:
            return
        if anchor is not None:
            like = [s for s in fresh if cosine(s.vector, anchor) >= NEWCOMER_FACE_ANCHOR]
            if not like:
                self._newcomer_waiting(newcomer, 'face', 'unlike_the_introduced_face',
                                       similarity=round(max(cosine(s.vector, anchor) for s in fresh), 2))
                return
            fresh = like
        if pool and not any(all(cosine(s.vector, kept.vector) >= .6 for kept in pool) for s in fresh):
            self._newcomer_waiting(newcomer, 'face', 'earlier_view_replaced', views=len(pool))
            pool.clear()
        for sample in fresh:
            if len(pool) < NEWCOMER_FACE_VIEWS and all(cosine(sample.vector, kept.vector) >= .6 for kept in pool):
                pool.append(sample)
        if len(pool) < 2 or max(s.at for s in pool) - min(s.at for s in pool) < .8:
            return
        samples, reason = vetted_samples(sorted(pool, key=lambda s: s.at), newcomer['person_id'], self.store)
        if not samples:
            pool.clear()   # A rival or conflict: start again from fresh views.
            self.trace.emit('face_learning_deferred', person_id=newcomer['person_id'], reason=reason)
            return
        try:
            with self.store.transaction() as conn:
                saved = self.store.save_faces(conn, newcomer['person_id'], samples, newcomer['source_turn'],
                    {'subject': 'other', 'said_by': newcomer['said_by'], 'basis': 'introduced_newcomer_sole_stranger_face'})
                revision = self.store.advance(conn) if saved else self.store.revision
        except Exception as exc:
            pool.clear()
            self.trace.emit('face_learning_failed', error_type=type(exc).__name__)
            return
        newcomer['face'] = False
        pool.clear()
        self.trace.emit('face_learning_saved', person_id=newcomer['person_id'], samples=saved, revision=revision,
                        basis='introduced_newcomer')
        if not newcomer['voice']:
            self.newcomers.pop(newcomer['person_id'], None)

    def learn_faces(self):
        if self.frozen: return
        from .face_attribution import learning_samples
        turn,capture,transcript,observation,attribution = self.current
        visible = {f['track_id']:f for f in self.faces}
        self.face_learning = {k:v for k,v in self.face_learning.items() if k in visible and capture.ended_at<=v['expires']}
        self.gather_newcomer_face()   # Faces, not words: an untrusted or echoed turn still shows them.
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
        elif capture.channel != 0:
            why = 'heard_on_answer_channel'   # Channel 1, a Jeopardy answer under the music.
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
        if (capture.robot_microphone_hold is False or capture.channel != 0 or
                (self.require_microphone_hold and capture.robot_microphone_hold is not True)):
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
