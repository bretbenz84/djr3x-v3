"""Fast head turn toward a voice in front of Rex, before any model call.

Owner request September 28: when someone starts talking from the front half, the neck
turns to face them as a reflex, taking over any earlier held look ("I'm over here").
The model can still look anywhere afterwards. The bearing is microphone-array evidence,
never an identity; behind Rex (for example a TV in the next room) nothing moves.

The array rides on the hero arm. In session 5e97b13a it read Bret 6 degrees right while
the camera saw his face 32-38 degrees left (the run before agreed within ~10). When a
visible face lies near the voice's bearing, the head aims at that face instead: the
camera is the precise sensor. This only aims the head; it attributes no speaker.
"""
from dataclasses import asdict
import threading

from .contracts import Cancellation, ProviderFailure
from .drive_space import NECK_CENTER, NECK_PULSES_PER_DEG, image_bearing
from .servo_poses import LIMITS
from .servos import ServoRequest

FORWARD_DEG = 90.     # Front half only; a voice behind is left to the model and base.
MIN_TURN_PULSES = 400  # About 5 degrees: already facing the voice closely enough.
FACE_MATCH_DEG = 60.  # A visible face this close to the voice bearing is where to look.


def voice_neck(bearing: float) -> int:
    """Neck pulse facing a +left array bearing, within the neck's calibrated travel."""
    return max(LIMITS[0][0], min(LIMITS[0][1], round(NECK_CENTER - bearing*NECK_PULSES_PER_DEG)))


class VoiceReflex:
    def __init__(self, owner):
        self.owner = owner
        self.trace = None
        self.on_head_move = None   # () -> None: the camera is about to re-frame the room
        self.faces = None          # () -> fresh face boxes in the current camera frame
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
            if self._thread is not None and self._thread.is_alive():
                return 'already_turning'
            aim = bearing
            boxes = ()
            if self.faces:
                try:
                    boxes = tuple(self.faces() or ())
                except Exception:
                    boxes = ()
            if boxes:
                neck_deg = self.owner.neck_degrees()
                seen = min((image_bearing(box, neck_deg) for box in boxes), key=lambda b: abs(b-bearing))
                if abs(seen-bearing) <= FACE_MATCH_DEG:
                    aim = seen
            target = voice_neck(aim)
            current = self.owner.commanded.get(0)
            if current is not None and abs(current - target) < MIN_TURN_PULSES:
                return 'already_facing'
            if self.on_head_move:
                self.on_head_move()
            self._index += 1
            request = ServoRequest(f'reflex:{self._index}', self.owner.version, 'reflex',
                                   self.owner.clock()+5., pose={0: target})
            token = self._cancel = Cancellation()
            # Someone new is talking: an earlier held look gives way to facing them.
            self.owner.held_channels.discard(0)
            def run():
                result = self.owner.execute(request, token)
                if self.trace:
                    self.trace.emit('voice_reflex', bearing_deg=round(bearing, 1), aim_deg=round(aim, 1),
                                    aimed_at='face' if aim != bearing else 'voice', neck=target, **asdict(result))
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
