"""Fast head turn toward a voice in front of Rex, before any model call.

Owner request September 28: when someone starts talking from the front half, the neck
turns to face them as a reflex, taking over any earlier held look ("I'm over here").
The model can still look anywhere afterwards. The bearing is microphone-array evidence,
never an identity; behind Rex (for example a TV in the next room) nothing moves.

A look a person asked for is the exception: while the servo owner holds it (October 23),
the reflex leaves the head where they asked it to look.

The array rides on the hero arm. In session 5e97b13a it read Bret 6 degrees right while
the camera saw his face 32-38 degrees left (the run before agreed within ~10). When a
visible face lies near the voice's bearing, the head aims at that face instead: the
camera is the precise sensor. This only aims the head; it attributes no speaker.

The same worker gives the listening cue (owner, October 11): once a trusted transcript
is back, the head cocks to one side while Rex thinks (servo_poses.listening_lanes).
Anything that moves him deliberately (talking, a pose, a gesture, a drive) stops it first.
A person speaking stops it where it is (settle), and it does not start while they are
mid-sentence: October 20 (515d71db), clips heard during the cue matched Bret's voice at a
median .39 against .57 still, and none of them could start a new person's voice.
"""
from dataclasses import asdict
import random
import threading

from .contracts import Cancellation, ProviderFailure
from .drive_space import image_bearing, neck_pulse
from .servo_poses import HEAD_PAIR, LIMITS, head_pose
from .servos import ServoRequest

FORWARD_DEG = 90.     # Front half only; a voice behind is left to the model and base.
MIN_TURN_PULSES = 400  # About 5 degrees: already facing the voice closely enough.
FACE_MATCH_DEG = 60.  # A visible face this close to the voice bearing is where to look.


def voice_neck(bearing: float) -> int:
    """Neck pulse facing a +left array bearing, within the neck's calibrated travel."""
    return neck_pulse(bearing)


class VoiceReflex:
    def __init__(self, owner, *, rng=None):
        self.owner = owner
        self.rng = rng or random.Random()
        self.trace = None
        self.faces = None          # () -> fresh face boxes in the current camera frame
        self.on_voice = lambda: None   # A voice in front returns the idle gaze to people.
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = None
        self._listening = None   # (cancellation, thread) of the latest listening cue.
        self._index = 0
        self.closed = False

    def face(self, bearing: float) -> str:
        """Start the turn in the background; the audio loop never waits on the servos."""
        with self._lock:
            if self.closed or self.owner.state != 'active':
                return 'unavailable'
            if abs(bearing) > FORWARD_DEG:
                return 'behind'
            if self.owner.holding_requested_look():
                # October 23 (1e626e07): "you're not finding it" swung the head back from the table
                # he had been asked to search. A look a person asked for holds until he has
                # answered from it (owner ruling: about 20 s after the answer).
                return 'holding_requested_look'
            self.on_voice()
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

    def heard(self) -> str:
        """Cock the head to show Rex heard what was said; started in the background."""
        with self._lock:
            if self.closed or self.owner.state != 'active':
                return 'unavailable'
            if set(HEAD_PAIR).intersection(self.owner.held_channels):
                return 'head_held'   # A person's held pose, or the drive stance.
            if self._thread is not None and self._thread.is_alive():
                return 'already_turning'
            _, tilt = head_pose(self.owner.commanded) if set(HEAD_PAIR).issubset(self.owner.commanded) else (0., 0.)
            # On the side of a lean the head already has (October 21: cocking away from it swung
            # the head across, left then right, as a person went on talking); straight, either side.
            side = (1 if tilt > 0 else -1) if abs(tilt) > .1 else self.rng.choice((-1, 1))
            self._index += 1
            request = ServoRequest(f'listening:{self._index}', self.owner.version, 'listening',
                                   self.owner.clock()+10., side=side)
            token = self._cancel = Cancellation()
            def run():
                result = self.owner.execute(request, token)
                if self.trace:
                    self.trace.emit('listening_cue', side=side, **asdict(result))
            self._thread = threading.Thread(target=run, name='r3x-listening-cue', daemon=True)
            self._listening = (token, self._thread)
            self._thread.start()
            return 'started'

    def settle(self) -> bool:
        """A person is talking: a running listening cue stops where it is and the head holds
        still, so the rest of what they say is heard cleanly. Only while it holds: the cock's
        own whir can sound like speech. Called from the audio loop, so it takes no lock and
        joins nothing; the servo owner sees the cancel within a step."""
        listening = self._listening
        if (listening is None or listening[0].event.is_set() or not listening[1].is_alive()
                or self.owner.travelling):
            return False
        listening[0].event.set()
        return True

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
