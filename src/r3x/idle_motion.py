"""Idle life: slow, staggered movement through the sole servo executor whenever Rex is not talking.

Owner, October 3 (run 30ec1b04): between lines Rex sat motionless, then every servo moved
at once (one request every 4-9 s of quiet, cancelled by most noises: 15 moves in ten
minutes). Each body part now keeps its own unhurried rhythm, and a phrase moves the one
to three parts that are due, each starting a little after the last, with a short breath
between phrases. The head belongs to the gaze:

- following people (the default): it keeps the nearest face in view with V2's
  proportional centring (gain 0.5 and a dead zone, hardware/servos.py at fb96f08), framed
  in the upper part of the picture so the camera sees the person and not only their face,
  and breaks eye contact with a side glance every 7-16 s (V2's gaze engine). The owner
  found V2's constant tracking creepy.
- holding a direction: after the conversation model chose where to look (look_left,
  look_down, a silent look), the head stays there with small living movement until the
  model looks back at people, someone speaks, or DIRECTION_SECONDS pass.

With nobody in view it looks around the room. This aims the head only; it attributes no
speaker and identifies nobody.
"""
from dataclasses import asdict
import random
import threading

from .contracts import Cancellation, ProviderFailure
from .servo_poses import (DRIVE_HEAD, HEAD_SMALL, IDLE_CHANNELS, LIMITS, LOOK_NECK_DEG, NECK_CENTER,
                          NECK_PER_DEG, TILT_DOWN_PER_DEG, TILT_UP_PER_DEG, IdlePhrase)
from .servos import ServoRequest

# Seconds between one part's moves. The head moves most; the array arm least, because
# each of its swings invalidates the microphone bearing and the voice-learning hold.
PART_SECONDS = {'head': (1.2, 2.8), 'visor': (6., 14.), 'elbow': (5., 12.), 'wrist': (4., 10.),
                'poker': (6., 15.), 'throttle': (9., 20.), 'hero': (30., 60.)}
PHRASE_PARTS = 3
DIRECTION_SECONDS = 30.      # A model-chosen look lasts this long unless changed sooner.
POSE_REST_SECONDS = 8.       # Rex's own arm pose (a reach, a visor move) stays put this long.
FACE_Y = .40                 # Where a followed face sits in the frame (0 top, 1 bottom).
FACE_GAIN = .5               # V2 FACE_TRACKING_CENTERING_GAIN.
NECK_DEAD_DEG = 5.           # V2's 60 px dead zone, about this much of the wide camera.
Y_DEAD = .08
VERTICAL_HALF_FOV = 29.      # 16:9 frame behind the measured 45 degree horizontal half view.
FACE_LOST_SECONDS = 4.       # Keep looking where a face just was before scanning the room.


class IdleMotion:
    def __init__(self, owner, *, rng=None):
        self.owner = owner
        self.rng = rng or random.Random()
        self.trace = None
        self.face = None           # () -> {'neck': pulse centring the face, 'y': 0..1, 'at': seen} or None
        self.busy = lambda: False  # True while talking motion owns the servos: idle never competes with it.
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = None
        self._allowed = False
        self._resume = float('inf')
        self._index = 0
        self._due = {}
        self._rest = {}            # part -> time until which Rex's own pose is left alone
        self.gaze = 'people'
        self._anchor = None        # Head pulses of a model-chosen look.
        self._anchor_until = float('-inf')
        self._glance_at = None
        self._glance_until = float('-inf')
        self._scan_at = float('-inf')
        self._face_at = float('-inf')
        self._wake_until = float('-inf')   # After a drive: alive even while he listens or works.
        self._counts = {}          # Phrase outcomes since the last summary row.
        self._report_at = float('-inf')
        self.closed = False

    # Gaze ----------------------------------------------------------------
    def mode(self, now=None):
        now = self.owner.clock() if now is None else now
        if self.gaze == 'direction' and now >= self._anchor_until:
            self.look_at_people()
        return self.gaze

    def look_at_people(self):
        self.gaze, self._anchor = 'people', None
        self._glance_at, self._glance_until = None, float('-inf')   # Back to them now, not after a glance.

    def hold_direction(self, now=None):
        """The model chose where to look: stay there, alive but not wandering off."""
        now = self.owner.clock() if now is None else now
        commanded = self.owner.commanded
        self._anchor = {ch: commanded.get(ch, default) for ch, default in ((0, NECK_CENTER), *DRIVE_HEAD)}
        self.gaze, self._anchor_until = 'direction', now + DIRECTION_SECONDS
        self.owner.gaze_tilt = self._anchor[2]

    def posed(self, channels, own, now=None):
        """A pose just ran. Rex's own head look becomes the held direction; his own arm
        pose rests briefly before idle movement takes that part again. A person's request
        is already protected by the servo owner's hold."""
        now = self.owner.clock() if now is None else now
        channels = set(channels)
        if channels.intersection(IDLE_CHANNELS['head']):
            self.hold_direction(now)
        if own:
            for part, owned in IDLE_CHANNELS.items():
                if part != 'head' and channels.intersection(owned):
                    self._rest[part] = now + POSE_REST_SECONDS

    def _head(self, now, calm):
        """Head targets for this phrase, or () for a small drift where it already points."""
        commanded = self.owner.commanded
        level = dict(DRIVE_HEAD)
        current = {0: commanded.get(0, NECK_CENTER), 1: commanded.get(1, level[1]), 2: commanded.get(2, level[2])}
        rng = self.rng
        def near(center, neck=60, tilt=40, lift=50):
            return ((0, center[0] + rng.randint(-neck, neck)), (1, center.get(1, level[1]) + rng.randint(-lift, lift)),
                    (2, center[2] + rng.randint(-tilt, tilt)))
        def bounded(targets):
            # During conversation work the camera view must stay settled: small moves only.
            if not calm:
                return targets
            return tuple((ch, max(current[ch] - HEAD_SMALL[ch] + 20, min(current[ch] + HEAD_SMALL[ch] - 20, value)))
                         for ch, value in targets)
        if self.mode(now) == 'direction':
            return bounded(near(self._anchor)), rng.randint(5, 8), 0.
        aim = None
        if self.face:
            try:
                aim = self.face()
            except Exception:
                aim = None   # Looking around needs no face.
        if aim is not None:
            self._face_at = now
            if self._glance_at is None:
                self._glance_at = now + rng.uniform(7., 16.)
        if now < self._glance_until:
            return (), rng.randint(5, 8), 0.          # Still looking away; drift there.
        if aim is not None and aim.get('at', now) > self.owner.head_moved_at + .1:
            if now >= self._glance_at and not calm:
                # Break eye contact like V2's gaze engine: 12-24 degrees aside, level or a
                # little down, never up, then back.
                self._glance_until = now + rng.uniform(1.4, 2.8)
                self._glance_at = self._glance_until + rng.uniform(7., 16.)
                neck = aim['neck'] + rng.choice((-1, 1)) * rng.uniform(12., 24.) * NECK_PER_DEG
                tilt = current[2] + rng.uniform(0., 5.) * TILT_DOWN_PER_DEG
                return ((0, neck), (1, current[1]), (2, tilt)), rng.randint(12, 18), 0.
            error = aim['neck'] - current[0]
            neck = current[0] + (FACE_GAIN * error if abs(error) > NECK_DEAD_DEG * NECK_PER_DEG else rng.randint(-40, 40))
            off = aim['y'] - FACE_Y
            tilt = current[2]
            if abs(off) > Y_DEAD:
                # A face low in the frame needs the head lower: tilt's pulse rises. The
                # linkage is asymmetric, so work in degrees below level and convert back.
                below = (tilt - level[2]) / (TILT_DOWN_PER_DEG if tilt >= level[2] else TILT_UP_PER_DEG)
                below += FACE_GAIN * off * 2 * VERTICAL_HALF_FOV
                tilt = level[2] + below * (TILT_DOWN_PER_DEG if below >= 0 else TILT_UP_PER_DEG)
            tilt = max(LIMITS[2][0], min(LIMITS[2][1], round(tilt)))
            self.owner.gaze_tilt = tilt
            far = abs(error) > 12 * NECK_PER_DEG
            return bounded(((0, neck), (1, level[1] + rng.randint(-50, 50)), (2, tilt))), 16 if far else 9, 0.
        if aim is not None or now - self._face_at <= FACE_LOST_SECONDS:
            return (), rng.randint(5, 8), 0.          # Waiting for a fresh look at them.
        # Nobody in view: look around the room now and then (V2's scan leaned downward,
        # where seated people are), drifting in place between looks.
        self._glance_at = None
        if now >= self._scan_at and not calm:
            self._scan_at = now + rng.uniform(6., 13.)
            self.owner.gaze_tilt = None
            vertical = rng.uniform(-.3, 1.)           # Negative looks up, positive down.
            tilt = level[2] + vertical * .65 * ((LIMITS[2][1] - level[2]) if vertical >= 0 else (level[2] - LIMITS[2][0]))
            neck = NECK_CENTER + rng.uniform(-LOOK_NECK_DEG, LOOK_NECK_DEG) * NECK_PER_DEG
            return ((0, neck), (1, level[1] - vertical * 200), (2, tilt)), rng.randint(10, 16), rng.uniform(.4, 1.2)
        return (), rng.randint(5, 8), 0.

    def _compose(self, now, calm):
        held = set(self.owner.held_channels)
        for part, span in PART_SECONDS.items():
            # First times are spread out so startup is not one synchronized move.
            self._due.setdefault(part, now + self.rng.uniform(0., span[1] if part != 'head' else .5))
        due = sorted((at, part) for part, at in self._due.items()
                     if at <= now and now >= self._rest.get(part, float('-inf'))
                     and not held.intersection(IDLE_CHANNELS[part])
                     and not (part == 'hero' and (calm or getattr(self.owner, 'hold_until_released', False))))
        # The array stays put while Rex listens or works, and while a person has told him to keep his arms still.
        if not due:
            return None
        chosen = [part for _, part in due[:PHRASE_PARTS]]
        if 'head' in chosen:
            chosen.insert(0, chosen.pop(chosen.index('head')))   # The gaze answers first.
        wait, parts = 0., []
        for part in chosen:
            parts.append((part, round(wait, 2)))
            wait += self.rng.uniform(.4, 1.3)
            self._due[part] = now + self.rng.uniform(*PART_SECONDS[part])
        head, speed, dwell = self._head(now, calm) if 'head' in chosen else ((), 9, 0.)
        return IdlePhrase(tuple(parts), tuple((ch, round(value)) for ch, value in head), speed, dwell)

    # Scheduling -----------------------------------------------------------
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

    def wake(self, seconds=6.):
        """The wheels just stopped: the head goes back to the people now and the other parts
        resume, without the usual pause and while the throttle arm is still returning.

        Owner, October 3 (twice): after a drive everything waited for the arm to un-park.
        Releasing the servos was not enough: idle movement waited two seconds to resume and
        stayed still whenever he was listening or answering, which is most moments after a
        drive. For these few seconds neither stills him; the arm is moving anyway."""
        with self._lock:
            if self.closed: return
            now = self.owner.clock()
            self._wake_until, self._resume, self._allowed = now + seconds, now, True
            for part in ('head', 'visor', 'elbow', 'wrist', 'poker'):
                self._due[part] = now
            self._rest.clear()
            self._scan_at = now          # With nobody in view he looks around at once.
            if not self.busy():
                self._start(now, calm=False)

    def tick(self, allowed: bool, calm: bool = False):
        with self._lock:
            if self.closed: return
            now = self.owner.clock()
            if now < self._wake_until and not self.busy():
                allowed, calm = True, False
            if not allowed:
                self._stop()
                return
            if self.trace and now >= self._report_at:
                # One row per phrase would be about 40 a minute; summarize every half minute.
                if self._counts:
                    self.trace.emit('idle_motion_summary', phrases=dict(self._counts), gaze=self.gaze)
                self._counts, self._report_at = {}, now + 30.
            if self._thread is not None:
                if self._thread.is_alive(): return
                self._thread = None
                # A breath between phrases; the microphone hold renews in this gap.
                self._resume = now + self.rng.uniform(.3, .7)
                return
            if not self._allowed:
                self._allowed = True
                self._resume = now + self.rng.uniform(.6, 1.2)
            self._start(now, calm)

    def _start(self, now, calm):
        """Begin the next phrase if the owner is free and a part is due (the lock is held)."""
        if self._thread is not None and self._thread.is_alive():
            return
        if (now < self._resume or self.owner.state != 'active' or self.owner._motion.is_set()
                or getattr(self.owner, '_base_held', False)):
            return
        phrase = self._compose(now, calm)
        if phrase is None:
            return
        self._index += 1
        request = ServoRequest(f'idle:{self._index}', self.owner.version, 'idle', now + 30., idle=phrase)
        token = self._cancel = Cancellation()
        def run():
            result = self.owner.execute(request, token)
            key = result.status if result.status != 'rejected' else 'rejected:' + str(result.reason)
            self._counts[key] = self._counts.get(key, 0) + 1
            if self.trace and result.status == 'failed':
                self.trace.emit('idle_motion', parts=[part for part, _ in phrase.parts], **asdict(result))
        self._thread = threading.Thread(target=run, name='r3x-idle-motion', daemon=True)
        self._thread.start()
