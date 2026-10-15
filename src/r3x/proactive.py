"""Bounded social opportunities and receipts; no language or mood classification."""
from collections import deque
from dataclasses import dataclass
import threading
import math
from uuid import uuid4
from .contracts import Cancellation
from .faces import VISIBLE_FACE_MAX_AGE, iou


@dataclass(frozen=True)
class Opportunity:
    event_id: str
    session_id: str
    at: float
    target_track: str | None
    person_id: str | None
    faces: tuple[dict, ...]
    speech_marker: float
    returning: bool
    kind: str = "visual_arrival"
    source_turn_id: str | None = None
    quiet_seconds: float | None = None
    public_cues: tuple = ()
    scene_id: str | None = None
    scene_images: tuple = ()
    audience_key: tuple = ()
    pet: dict | None = None   # pet_arrival: the detection that arrived.


# Nullable additions keep ordinary streamed prose unchanged. These are resource
# bounds, not speech deadlines: a due request always reconsiders fresh context.
def next_opportunity_schema():
    fields = {
        'source_id': {'type': 'string', 'minLength': 1, 'maxLength': 120},
        'revision': {'type': 'integer', 'minimum': 0},
        'trigger': {'type': 'string', 'enum': ['after_speech', 'after_decision', 'wait_for_input']},
        'delay_seconds': {'type': ['number', 'null'], 'minimum': 3, 'maximum': 300},
    }
    return {'type': ['object', 'null'], 'properties': fields,
            'required': list(fields), 'additionalProperties': False}


def validate_next(value):
    if value is None:
        return
    if not isinstance(value, dict) or value.keys() != next_opportunity_schema()['properties'].keys():
        raise ValueError('InvalidNextOpportunity')
    if (not isinstance(value['source_id'], str) or not 0 < len(value['source_id']) <= 120 or
            type(value['revision']) is not int or value['revision'] < 0):
        raise ValueError('InvalidNextOpportunitySource')
    if value['trigger'] not in {'after_speech', 'after_decision', 'wait_for_input'}:
        raise ValueError('InvalidNextOpportunityTrigger')
    delay = value['delay_seconds']
    if value['trigger'] == 'wait_for_input':
        if delay is not None:
            raise ValueError('WaitHasDelay')
    elif type(delay) not in (int, float) or not math.isfinite(delay) or not 3 <= delay <= 300:
        raise ValueError('InvalidNextOpportunityDelay')


@dataclass(frozen=True)
class Reconsideration:
    source_id: str
    revision: int
    trigger: str
    delay_seconds: float | None


class OpportunityCancellation(Cancellation):
    def __init__(self, valid):
        super().__init__()
        self.valid = valid

    def check(self):
        if not self.is_committed() and not self.valid(): self.event.set()
        super().check()


class Initiative:
    """Session-state-owned pacing. Attempts never masquerade as heard speech.

    Semantic feedback is optional, source-linked and scoped to its observed speaker.
    A new source turn permits consideration, not an inference of engagement.
    """
    def __init__(self, trace=None):
        self.trace = trace
        self.agency = None
        from .invitations import Invitations
        self.invitations = Invitations(trace)
        self.schedule_revision = 0
        self.quiet = False
        self.silent = False   # Not a word, replies included, until the person releases him.
        self.policy_source = None
        self.attempts = deque(maxlen=16)
        self.outcomes = deque(maxlen=16)
        self.lull_source = None
        self.lull_attempts = 0
        self.feedback = deque(maxlen=32)

    def floor_held(self, now: float) -> bool:
        return any(r['action']=='yield_floor' and now < r['expires_at'] for r in self.feedback)

    def scheduling_context(self, source):
        return {'revision': self.schedule_revision, 'source_id': source,
                'delay_bounds_seconds': [3, 300], 'meaning': 'Invitation review metadata only; agency plans own periodic advancement.'}

    def cancel_schedule(self, reason):
        if self.agency:
            # October 2: a human turn steers the goal; it no longer discards it (live
            # October 1, every "Okay." threw away the plan: 40 planner calls in ten minutes).
            if reason in {'forgotten','forget_person','correct_claim','retract_claim',
                          'cancel_association','reset_face_identity','explicit_stop','initiative_quiet'}:
                self.agency.clear(reason)
            elif reason == 'participant_boundary':
                # "Drop it" ends the goal; it stays as context so its premise is not revived.
                self.agency.replace(reason)
        if reason in {'new_session','forgotten','forget_person','correct_claim','retract_claim','cancel_association','reset_face_identity'}:
            self.invitations.invalidate(reason,erase=True)
        elif reason in {'explicit_stop','initiative_quiet','participant_boundary','human_floor'}:
            self.invitations.invalidate(reason)
        self.schedule_revision += 1  # Also invalidates in-flight proposals.

    def prepare_schedule(self, value, context):
        validate_next(value)
        if value is None:
            return None
        if context.get('frozen_evaluation'):
            raise ValueError('FrozenEvaluationDisallowsMutation')
        offered = context.get('reconsideration', {})
        source = context.get('event_id') or context.get('source_turn_id')
        if value['source_id'] != source or offered.get('source_id') != source:
            raise ValueError('ReconsiderationSourceMismatch')
        if value['revision'] != self.schedule_revision or offered.get('revision') != self.schedule_revision:
            raise ValueError('StaleReconsideration')
        return Reconsideration(**value)

    def attempted(self, event: Opportunity) -> None:
        self.attempts.append(event.at)
        self.cancel_schedule("opportunity_consumed")
        if event.kind in {"conversation_lull","ambient","reconsideration"}:
            if self.lull_source != event.source_turn_id:
                self.lull_attempts = 0
            self.lull_source = event.source_turn_id
            self.lull_attempts = min(16, self.lull_attempts + 1)

    def finished(self, event: Opportunity, delivery, now: float) -> dict:
        heard = bool(delivery.completed_text) and delivery.medium == "audio"
        row = {"event_id": event.event_id, "kind": event.kind, "at": now,
               "status": delivery.status, "medium": delivery.medium,
               "heard_complete_phrases": heard,
               "source_turn_id": event.source_turn_id, "target_person_id": event.person_id,
               "delivered_text": " ".join(delivery.completed_text)[:1200] if heard else None,
               "response": "unknown" if heard else "not_applicable"}
        self.outcomes.append(row)
        return row

    def context(self, now: float | None = None) -> dict:
        return {"initiative_quiet": self.quiet, "silent_until_released": self.silent, "policy_source_turn_id": self.policy_source,
                "recent_opportunities": list(self.outcomes),
                "lull_attempts_since_source": self.lull_attempts,
                "feedback": [dict(row) for row in self.feedback
                             if row["expires_at"] is None or now is None or now < row["expires_at"]]}

    def discard_association(self, person_id, track_id) -> None:
        self.feedback = deque((r for r in self.feedback if not (
            person_id and r['participant']['person_id']==person_id or
            track_id and r['participant']['track_id']==track_id)), maxlen=32)

    def forget(self) -> None:
        self.cancel_schedule("forgotten")
        # Keep anonymous pacing and a quiet request, remove personal source links.
        self.policy_source = None
        self.outcomes.clear()
        self.feedback.clear()
        self.lull_source = None
        self.lull_attempts = 0


class Arrivals:
    stable_seconds = 3.
    absent_seconds = 30.
    global_cooldown = 45.
    person_cooldown = 300.
    retry_seconds = 30.   # How long a hello cut off by thrown-away noise waits for its quick retry.

    def __init__(self):
        self.lock = threading.RLock()
        self.seen = {}
        self.faces = ()
        self.empty_since = None
        self.unknown_attempted = False
        self.unknown_target = None
        self.epoch = None
        self.last_attempt = float('-inf')
        self._members = ()
        self._audience_key = ()
        self._audience_since = float('inf')
        self.moved_at = float('-inf')
        self.moved_allows_new = False

    def note_base_motion(self, now, allow_new=False):
        """Rotating/driving re-frames the room; faces newly tracked right after it
        are the same visit, not arrivals, unless the move went to investigate."""
        with self.lock:
            self.moved_at, self.moved_allows_new = now, allow_new

    def _observe_members(self, faces):
        # Presence continuity only. Never replace face IDs or identity evidence
        # in the model context, attribution, enrollment or targeted tools.
        members = []
        used = set()
        for face in faces:
            matches = []
            for old, token in self._members:
                if token in used or old.get('face_epoch',0) != face.get('face_epoch',0):
                    continue
                if old.get('identity') != face.get('identity'):
                    continue
                if not 0 <= face['at']-old['at'] <= VISIBLE_FACE_MAX_AGE:
                    continue
                same_track = old['track_id'] == face['track_id']
                named = face.get('identity')
                unique = named and sum(f.get('identity') == named for f in faces) == 1
                unique = unique and sum(f.get('identity') == named for f,_ in self._members) == 1
                if same_track or (unique and iou(old['box'],face['box']) > .15):
                    matches.append(token)
            token = matches[0] if len(matches) == 1 else uuid4().hex
            used.add(token)
            members.append((face,token))
        key = tuple(sorted((token,f.get('identity'),f.get('face_epoch',0)) for f,token in members))
        if key != self._audience_key:
            self._audience_since = max((f['at'] for f in faces), default=float('inf'))
        self._members, self._audience_key = tuple(members), key

    def observe(self, faces, now):
        with self.lock:
            previous = self._members
            self.faces = tuple({k:v for k,v in f.items() if not k.startswith('_')} for f in faces
                               if 0<=now-f['at']<=VISIBLE_FACE_MAX_AGE)
            self._observe_members(self.faces)
            continuous = {f['track_id'] for f,token in self._members
                          if any(old_token == token for _,old_token in previous)}
            epochs = {f.get('face_epoch',0) for f in self.faces}
            if epochs and self.epoch not in epochs:
                self.seen.clear()
                self.epoch = max(epochs)
            if not self.faces:
                if self.empty_since is None: self.empty_since = now
                if now-self.empty_since>=self.absent_seconds: self.unknown_attempted = False
            else: self.empty_since = None
            for face in self.faces:
                key = face.get('identity') or face['track_id']
                entry = self.seen.get(key)
                if entry is None:
                    reframed = 0 <= face['at']-self.moved_at <= 6 and not self.moved_allows_new
                    entry = {'first':face['at'],'last':face['at'],'attempt':float('-inf'),
                             'used':reframed,'returning':False,'track':face['track_id']}
                    self.seen[key] = entry
                elif face['at']-entry['last']>=self.absent_seconds:
                    entry.update(first=face['at'],used=False,returning=True,retried=False,track=face['track_id'])
                elif entry['track']!=face['track_id'] or face['at']-entry['last']>1.5:
                    # Only supported, spatially continuous replacements retain
                    # presence stability. Neither case reopens a used visit.
                    entry.update(first=entry['first'] if face['track_id'] in continuous else face['at'],
                                 track=face['track_id'])
                entry['last'] = face['at']
                if face['track_id']==self.unknown_target and face.get('identity'):
                    # Naming a previously invited visitor is not a second arrival.
                    entry.update(used=True,attempt=self.last_attempt)
                    self.unknown_attempted = False
                    self.unknown_target = None
            self.seen = dict(sorted(self.seen.items(),key=lambda p:p[1]['last'],reverse=True)[:128])

    def take(self, session_id, now, speech_marker, human_turns=None):
        """human_turns counts trusted human turns so far; None makes no quick retry."""
        with self.lock:
            # A hello cut off by a sound later thrown away (October 14: Rex's own motor noise,
            # heard as "Oh.") is retried at once, ahead of the cooldown and anything else, while
            # nobody has really spoken since. Once per visit.
            for entry in self.seen.values():
                cut_off = entry.get('cut_off')
                # Someone really spoke (the conversation took over), or they did not come back.
                if cut_off and (cut_off[0] != human_turns or now-cut_off[1] > self.retry_seconds):
                    entry['cut_off'] = None
            retry = any(e.get('cut_off') for e in self.seen.values())
            if not retry and now-self.last_attempt<self.global_cooldown: return None
            if not self.audience(now): return None
            candidates = []
            for face in self.faces:
                if not 0<=now-face['at']<=VISIBLE_FACE_MAX_AGE: continue
                person = face.get('identity')
                if person and sum(f.get('identity')==person for f in self.faces)!=1: continue
                if not person and self.unknown_attempted: continue
                box=face.get('box',(0,0,0,0))
                if (face.get('face_width_px') or 0)<48 or box[2]*box[3]<.006: continue
                key = person or face['track_id'];entry=self.seen[key]
                if entry['used'] or entry['last']-entry['first']<self.stable_seconds: continue
                if now-entry['attempt']<self.person_cooldown: continue
                if retry and not entry.get('cut_off'): continue
                candidates.append((bool(person),box[2]*box[3],face,key,entry))
            if not candidates: return None
            _,_,target,key,entry = max(candidates,key=lambda r:r[:2])
            entry['used'],entry['attempt'],entry['cut_off'] = True,now,None
            self.last_attempt = now
            if not target.get('identity'):
                self.unknown_attempted = True
                self.unknown_target = target['track_id']
            return Opportunity(uuid4().hex,session_id,now,target['track_id'],target.get('identity'),
                                 self.faces,speech_marker,entry['returning'],audience_key=self._audience_key)

    def still_present(self, event, now, grace=10.):
        """The arrival's person is still around, judged by identity, not track ID.

        Live October 1: one continuously visible owner received 175 track IDs, and
        greetings and plan steps were cancelled as "audience changed". A known person
        counts while any face carries their identity; an unknown visitor while any
        face is seen.
        """
        with self.lock:
            if event.person_id:
                entry = self.seen.get(event.person_id)
                if not entry or not 0 <= now-entry['last'] <= grace:
                    return False
                # Someone else, recognized, standing there alone is not the person greeted.
                known = {f.get('identity') for f in self.faces
                         if 0 <= now-f['at'] <= VISIBLE_FACE_MAX_AGE and f.get('identity')}
                return not known or event.person_id in known
            return any(0 <= now-entry['last'] <= grace for entry in self.seen.values())

    def finished(self, event, delivery, cut_off_turns=None):
        """An interrupted unheard arrival is eligible again after normal pacing.

        Keep the global cooldown and attempt budget. Silence, failures, and
        even partially spoken phrases do not trigger automatic re-offering.
        cut_off_turns: a queued sound stopped it before any speech, when this many
        trusted human turns had been heard. While that count stands (the sound was
        thrown away), the hello is retried at once.
        """
        if (event.kind!='visual_arrival' or delivery.status!='interrupted' or
                delivery.completed_text or delivery.first_audio_at is not None):
            return
        with self.lock:
            entry = self.seen.get(event.person_id or event.target_track)
            # A known person is keyed by identity; their renumbered track is the same visit.
            if (not entry or (entry['track']!=event.target_track and not event.person_id)
                    or entry['attempt']!=event.at):
                return
            entry['used'] = False
            if cut_off_turns is not None and not entry.get('retried'):
                entry['cut_off'], entry['retried'] = (cut_off_turns, event.at), True
            # The global cooldown still limits a retry; the per-person cooldown
            # applies to greetings that had a chance to reach the person.
            entry['attempt'] = float('-inf')
            if self.unknown_target==event.target_track:
                self.unknown_attempted = False
                self.unknown_target = None

    def audience(self, now):
        """Full fresh audience after three seconds of settled membership.

        Stability gates initiative, not membership. The event and its validity
        check must describe the same visible group, including background faces.
        """
        with self.lock:
            if (not self.faces or min(f['at'] for f in self.faces)-self._audience_since < self.stable_seconds):
                return ()
            result = []
            for face in self.faces:
                entry = self.seen.get(face.get('identity') or face['track_id'])
                if (entry and entry['track']==face['track_id'] and
                    0<=now-face['at']<=VISIBLE_FACE_MAX_AGE and entry['last']-entry['first']>=self.stable_seconds and
                    (face.get('face_width_px') or 0)>=48):
                    result.append(face)
            return tuple(f for f in self.faces if 0<=now-f['at']<=VISIBLE_FACE_MAX_AGE) if result else ()

    def audience_snapshot(self, now):
        with self.lock:
            return self.audience(now), self._audience_key
