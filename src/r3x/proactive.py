"""Bounded social opportunities and receipts; no language or mood classification."""
from collections import deque
from dataclasses import dataclass
import threading
from uuid import uuid4
from .contracts import Cancellation


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
        self.feedback = deque(maxlen=32)

    def floor_held(self, now: float) -> bool:
        return any(r['action']=='yield_floor' and now < r['expires_at'] for r in self.feedback)

    def allowed(self, now: float) -> bool:
        recent = [at for at in self.attempts if 0 <= now-at < 300]
        return not self.quiet and not self.floor_held(now) and len(recent) < 6 and (
            not self.attempts or now-self.attempts[-1] >= 8)

    def attempted(self, event: Opportunity) -> None:
        self.attempts.append(event.at)
        if event.kind in {"conversation_lull","ambient"}:
            self.lull_source = event.source_turn_id

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

    def observe(self, faces, now):
        with self.lock:
            self.faces = tuple({k:v for k,v in f.items() if not k.startswith('_')} for f in faces
                               if 0<=now-f['at']<=1.)
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
                    entry = {'first':face['at'],'last':face['at'],'attempt':float('-inf'),
                             'used':False,'returning':False,'track':face['track_id']}
                    self.seen[key] = entry
                elif face['at']-entry['last']>=self.absent_seconds:
                    entry.update(first=face['at'],used=False,returning=True,track=face['track_id'])
                elif entry['track']!=face['track_id'] or face['at']-entry['last']>1.5:
                    # A replacement track must restabilize, but does not reopen a used visit.
                    entry.update(first=face['at'],track=face['track_id'])
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
            candidates = []
            for face in self.faces:
                if not 0<=now-face['at']<=1.: continue
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
                                 self.faces,speech_marker,entry['returning'])

    def valid(self, event, now):
        with self.lock:
            if event.kind in {"conversation_lull","ambient"}:
                keys = lambda faces: {(f['track_id'],f.get('identity'),f.get('face_epoch',0)) for f in faces}
                fresh = [f for f in self.faces if 0<=now-f['at']<=1]
                return bool(event.faces) and keys(fresh) == keys(event.faces)
            return any(f['track_id']==event.target_track and f.get('identity')==event.person_id and
                       f.get('face_epoch')==next(t.get('face_epoch') for t in event.faces if t['track_id']==event.target_track) and
                       0<=now-f['at']<=1. for f in self.faces)

    def audience(self, now):
        """Fresh stable audience, including unnamed people; never an acoustic ID."""
        with self.lock:
            result = []
            for face in self.faces:
                entry = self.seen.get(face.get('identity') or face['track_id'])
                box = face.get('box',(0,0,0,0))
                if (entry and entry['track']==face['track_id'] and
                    0<=now-face['at']<=1 and entry['last']-entry['first']>=self.stable_seconds and
                    (face.get('face_width_px') or 0)>=48 and box[2]*box[3]>=.006):
                    result.append(face)
            return tuple(result)
