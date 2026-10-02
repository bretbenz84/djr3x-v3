"""Low-priority, bounded idle requests through the sole servo executor."""
from dataclasses import asdict
import random
import threading

from .contracts import Cancellation, ProviderFailure
from .servos import ServoRequest


class IdleMotion:
    def __init__(self, owner, *, rng=None):
        self.owner = owner
        self.rng = rng or random.Random()
        self.trace = None
        self.face = None           # () -> neck pulse centring the face in front, or None
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = None
        self._allowed = False
        self._next = float('inf')
        self._index = 0
        self.closed = False

    def _stop(self):
        if self._cancel:
            self._cancel.event.set()
        if self._thread:
            self._thread.join(timeout=2.)
            if self._thread.is_alive():
                raise ProviderFailure('IdleMotionStillActive')
            self._thread = None
        self._allowed = False

    def stop(self):
        with self._lock:
            self._stop()

    def close(self):
        with self._lock:
            self.closed = True
            self._stop()

    def tick(self, allowed: bool):
        with self._lock:
            if self.closed: return
            if not allowed:
                self._stop()
                return
            now = self.owner.clock()
            if self._thread is not None:
                if self._thread.is_alive(): return
                self._thread = None
                self._allowed = False
            if not self._allowed:
                self._allowed = True
                self._next = now+self.rng.uniform(4.,9.)
            if now<self._next or self.owner.state!='active' or self.owner.motion_risk():
                return
            self._index += 1
            face_neck = None
            if self.face:
                try:
                    face_neck = self.face()
                except Exception:
                    face_neck = None   # Looking around needs no face.
            request = ServoRequest(f'idle:{self._index}',self.owner.version,'idle',now+20.,face_neck=face_neck)
            token = self._cancel = Cancellation()
            def run():
                result = self.owner.execute(request,token)
                if self.trace: self.trace.emit('idle_motion',**asdict(result))
            self._thread = threading.Thread(target=run,name='r3x-idle-motion',daemon=True)
            self._thread.start()
