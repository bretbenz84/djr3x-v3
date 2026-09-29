"""Fast head turn toward a voice in front of Rex, before any model call.

Owner request September 28: when someone starts talking from the front half, the neck
turns to face them as a reflex. It is not a hold: the model's explicit looks win, and
talking motion re-anchors on the addressee. The bearing is microphone-array evidence,
never an identity; behind Rex (for example a TV in the next room) nothing moves.
"""
from dataclasses import asdict
import threading

from .contracts import Cancellation, ProviderFailure
from .drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
from .servo_poses import LIMITS
from .servos import ServoRequest

FORWARD_DEG = 90.     # Front half only; a voice behind is left to the model and base.
MIN_TURN_PULSES = 400  # About 5 degrees: already facing the voice closely enough.


def voice_neck(bearing: float) -> int:
    """Neck pulse facing a +left array bearing, within the neck's calibrated travel."""
    return max(LIMITS[0][0], min(LIMITS[0][1], round(NECK_CENTER - bearing*NECK_PULSES_PER_DEG)))


class VoiceReflex:
    def __init__(self, owner):
        self.owner = owner
        self.trace = None
        self.on_head_move = None   # () -> None: the camera is about to re-frame the room
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = None
        self._index = 0
        self.closed = False

    def face(self, bearing: float) -> str:
        """Start the turn in the background; the audio loop never waits on the servos."""
        with self._lock:
            if self.closed or self.owner.state != 'active':
                return 'unavailable'
            if abs(bearing) > FORWARD_DEG:
                return 'behind'
            if 0 in getattr(self.owner, 'held_channels', ()):
                return 'neck_held'   # An explicit look stays where it was asked to be.
            if self._thread is not None and self._thread.is_alive():
                return 'already_turning'
            target = voice_neck(bearing)
            current = self.owner.commanded.get(0)
            if current is not None and abs(current - target) < MIN_TURN_PULSES:
                return 'already_facing'
            if self.on_head_move:
                self.on_head_move()
            self._index += 1
            request = ServoRequest(f'reflex:{self._index}', self.owner.version, 'reflex',
                                   self.owner.clock()+5., pose={0: target})
            token = self._cancel = Cancellation()
            def run():
                result = self.owner.execute(request, token)
                if self.trace:
                    self.trace.emit('voice_reflex', bearing_deg=round(bearing, 1), neck=target, **asdict(result))
            self._thread = threading.Thread(target=run, name='r3x-voice-reflex', daemon=True)
            self._thread.start()
            return 'started'

    def _stop(self):
        if self._cancel:
            self._cancel.event.set()
        if self._thread:
            self._thread.join(timeout=2.)
            if self._thread.is_alive():
                raise ProviderFailure('VoiceReflexStillActive')
            self._thread = None

    def stop(self):
        with self._lock:
            self._stop()

    def close(self):
        with self._lock:
            self.closed = True
            self._stop()
