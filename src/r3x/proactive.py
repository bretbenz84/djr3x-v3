"""Bounded social opportunities and receipts; no language or mood classification."""
from collections import deque
from dataclasses import dataclass
import threading
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


class OpportunityCancellation(Cancellation):
    def __init__(self, valid):
        super().__init__()
        self.valid = valid

    def check(self):
        if not self.valid(): self.event.set()
        super().check()


class Initiative:
    """Session-state-owned pacing. Attempts never masquerade as heard speech.

    Semantic feedback is optional, source-linked and scoped to its observed speaker.
    A new source turn permits consideration, not an inference of engagement.
    """
    def __init__(self):
        self.quiet = False
        self.policy_source = None
        self.attempts = deque(maxlen=16)
        self.outcomes = deque(maxlen=16)
        self.lull_source = None
        self.lull_attempts = 0
        self.feedback = deque(maxlen=32)
        self.lull_retry_until = float('-inf')
        self.lull_retry_used = False

    def floor_held(self, now: float) -> bool:
        return any(r['action']=='yield_floor' and now < r['expires_at'] for r in self.feedback)

    def allowed(self, now: float) -> bool:
        recent = [at for at in self.attempts if 0 <= now-at < 300]
        return not self.quiet and not self.floor_held(now) and len(recent) < 6 and (
            not self.attempts or now-self.attempts[-1] >= 8)

    def lull_due(self, now: float, source: str | None, active_intent: bool) -> bool:
        """Reconsider an active visit without treating silence as a human response."""
        if source is not None and source != self.lull_source:
            return True
        if (source == self.lull_source and now < self.lull_retry_until and
                self.outcomes and now-self.outcomes[-1]['at'] >= 8):
            return True
        if not active_intent:
            return False
        delay = (8., 15., 30., 60.)[min(max(self.lull_attempts - 1, 0), 3)]
        last_finished = self.outcomes[-1]['at'] if self.outcomes else float('-inf')
        last_attempt = self.attempts[-1] if self.attempts else float('-inf')
        return now - max(last_attempt, last_finished) >= delay

    def attempted(self, event: Opportunity) -> None:
        self.attempts.append(event.at)
        if event.kind in {"conversation_lull","ambient"}:
            if self.lull_source != event.source_turn_id:
                self.lull_attempts = 0
                self.lull_retry_used = False
                self.lull_retry_until = float('-inf')
            if event.kind == 'conversation_lull':
                self.lull_retry_until = float('-inf')
            self.lull_source = event.source_turn_id
            self.lull_attempts = min(4, self.lull_attempts + 1)

    def retry_unheard(self, event, delivery, reason, now):
        """One fresh reconsideration, not replay, after a camera-only cancellation."""
        if (event.kind != 'conversation_lull' or self.lull_retry_used or
                event.source_turn_id != self.lull_source or
                reason not in {'audience_changed_or_stale', 'intent_audience_changed'} or
                delivery.status != 'interrupted' or delivery.first_audio_at is not None or
                delivery.completed_text):
            return False
        self.lull_retry_used = True
        self.lull_retry_until = now + 30
        return True

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
        return {"initiative_quiet": self.quiet, "policy_source_turn_id": self.policy_source,
                "recent_opportunities": list(self.outcomes),
                "lull_attempts_since_source": self.lull_attempts,
                "feedback": [dict(row) for row in self.feedback
                             if row["expires_at"] is None or now is None or now < row["expires_at"]]}

    def discard_association(self, person_id, track_id) -> None:
        self.feedback = deque((r for r in self.feedback if not (
            person_id and r['participant']['person_id']==person_id or
            track_id and r['participant']['track_id']==track_id)), maxlen=32)

    def forget(self) -> None:
        # Keep anonymous pacing and a quiet request, remove personal source links.
        self.policy_source = None
        self.outcomes.clear()
        self.feedback.clear()
        self.lull_source = None
        self.lull_attempts = 0
        self.lull_retry_until = float('-inf')
        self.lull_retry_used = False


class Arrivals:
    stable_seconds = 3.
    absent_seconds = 30.
    global_cooldown = 45.
    person_cooldown = 300.

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
                    entry.update(first=face['at'],used=False,returning=True,track=face['track_id'])
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

    def take(self, session_id, now, speech_marker):
        with self.lock:
            if now-self.last_attempt<self.global_cooldown: return None
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
                candidates.append((bool(person),box[2]*box[3],face,key,entry))
            if not candidates: return None
            _,_,target,key,entry = max(candidates,key=lambda r:r[:2])
            entry['used'],entry['attempt'] = True,now
            self.last_attempt = now
            if not target.get('identity'):
                self.unknown_attempted = True
                self.unknown_target = target['track_id']
            return Opportunity(uuid4().hex,session_id,now,target['track_id'],target.get('identity'),
                                 self.faces,speech_marker,entry['returning'],audience_key=self._audience_key)

    def valid(self, event, now, *, audience_bound=True):
        with self.lock:
            if event.kind == 'unseen_presence':
                return True  # Radar prior; speech/queue/shutdown guards still apply.
            if event.kind in {"conversation_lull","ambient"}:
                keys = lambda faces: {(f['track_id'],f.get('identity'),f.get('face_epoch',0)) for f in faces}
                fresh = [f for f in self.faces if 0<=now-f['at']<=VISIBLE_FACE_MAX_AGE]
                if not audience_bound and event.audience_key:
                    return bool(fresh) and len(fresh)==len(self.faces) and event.audience_key==self._audience_key
                return bool(event.faces) and keys(fresh) == keys(event.faces)
            return any(f['track_id']==event.target_track and f.get('identity')==event.person_id and
                       f.get('face_epoch')==next(t.get('face_epoch') for t in event.faces if t['track_id']==event.target_track) and
                       0<=now-f['at']<=VISIBLE_FACE_MAX_AGE for f in self.faces)

    def finished(self, event, delivery):
        """An interrupted unheard arrival is eligible again after normal pacing.

        Keep the global cooldown and attempt budget. Silence, failures, and
        even partially spoken phrases do not trigger automatic re-offering.
        """
        if (event.kind!='visual_arrival' or delivery.status!='interrupted' or
                delivery.completed_text or delivery.first_audio_at is not None):
            return
        with self.lock:
            entry = self.seen.get(event.person_id or event.target_track)
            if not entry or entry['track']!=event.target_track or entry['attempt']!=event.at:
                return
            entry['used'] = False
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
                box = face.get('box',(0,0,0,0))
                if (entry and entry['track']==face['track_id'] and
                    0<=now-face['at']<=VISIBLE_FACE_MAX_AGE and entry['last']-entry['first']>=self.stable_seconds and
                    (face.get('face_width_px') or 0)>=48 and box[2]*box[3]>=.006):
                    result.append(face)
            return tuple(f for f in self.faces if 0<=now-f['at']<=VISIBLE_FACE_MAX_AGE) if result else ()

    def audience_snapshot(self, now):
        with self.lock:
            return self.audience(now), self._audience_key
