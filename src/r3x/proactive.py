"""Bounded arrival opportunities. Code paces; the conversation model chooses speech."""
from dataclasses import dataclass
import threading
from uuid import uuid4
from .contracts import Cancellation


@dataclass(frozen=True)
class VisualArrival:
    event_id: str
    session_id: str
    at: float
    target_track: str
    person_id: str | None
    faces: tuple[dict, ...]
    speech_marker: float
    returning: bool


class VisualCancellation(Cancellation):
    def __init__(self, valid):
        super().__init__()
        self.valid = valid

    def check(self):
        if not self.valid(): self.event.set()
        super().check()


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
            return VisualArrival(uuid4().hex,session_id,now,target['track_id'],target.get('identity'),
                                 self.faces,speech_marker,entry['returning'])

    def valid(self, event, now):
        with self.lock:
            return any(f['track_id']==event.target_track and f.get('identity')==event.person_id and
                       f.get('face_epoch')==next(t.get('face_epoch') for t in event.faces if t['track_id']==event.target_track) and
                       0<=now-f['at']<=1. for f in self.faces)
