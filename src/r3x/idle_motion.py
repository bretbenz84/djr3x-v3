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
from .servo_poses import (DRIVE_HEAD, HEAD, HEAD_SMALL, IDLE_CHANNELS, LOOK_NECK_DEG, NECK_CENTER,
                          NECK_PER_DEG, PITCH_SMALL, TILT_SMALL, IdlePhrase, degrees_from_pitch, head_pose,
                          head_pulses, pitch_from_degrees)
from .servos import ServoRequest

# Seconds between one part's moves. An arm moves all of its joints together (owner,
# October 12), so each arm is one part.
# Owner, October 12: idle read as frozen with a small move now and then; it should sit
# between that and the lively talking motion, so the arms come round about twice as often.
# Owner, October 13: the poker arm sways like a cat's tail. During a bout it is due in every
# phrase (half a sway each, servo_poses.poker_tail) and takes one of its three parts.
PART_SECONDS = {'head': (1.2, 2.8), 'visor': (4., 9.), 'hero': (2.5, 6.), 'poker': (0., 0.),
                'throttle': (5., 11.)}
# The hero arm's shoulder joins its move at most this often: it carries the microphone
# array, and each swing invalidates the bearing and the voice-learning hold.
SWING_SECONDS = (30., 60.)
# Owner, October 13 (later): constant swaying was too much. The tail sways back and forth
# for a bout, settles back to its awake pose, rests still, then sways again.
TAIL_SWAY_SECONDS = (10., 25.)
TAIL_REST_SECONDS = (15., 40.)
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
        self._swing_at = None      # When the hero arm's next move may include the array shoulder.
        self._tail_until = None    # When the poker arm's bout of swaying ends; None while it rests.
        self._rest = {}            # part -> time until which Rex's own pose is left alone
        self.gaze = 'people'
        self._anchor = None        # Head pulses of a model-chosen look.
        self._anchor_until = float('-inf')
        self._glance_at = None
        self._glance_until = float('-inf')
        self._scan_at = float('-inf')
        self._face_at = float('-inf')
        self._cock = 0.                    # A gentle attentive head cock while following a face...
        self._cock_until = float('-inf')   # ...held until then...
        self._cock_at = float('-inf')      # ...and next begun then.
        self._wake_until = float('-inf')   # After a drive: alive even while he listens or works.
        self._wake_at = float('-inf')
        # What the head is doing and since when, for Rex's own sense of where he is looking
        # (October 14, 1662b007: asked "what are you looking at?" he could only describe the view).
        self.activity = ('following_people', float('-inf'))
        self._counts = {}          # Phrase outcomes since the last summary row.
        self._report_at = float('-inf')
        self.closed = False

    # Gaze ----------------------------------------------------------------
    def mode(self, now=None):
        now = self.owner.clock() if now is None else now
        if self.gaze == 'direction' and now >= self._anchor_until:
            self.look_at_people()
        return self.gaze

    def _doing(self, kind, now):
        if self.activity[0] != kind:
            self.activity = (kind, now)

    def look_at_people(self):
        self.gaze, self._anchor = 'people', None
        self._glance_at, self._glance_until = None, float('-inf')   # Back to them now, not after a glance.

    def hold_direction(self, now=None):
        """The model chose where to look: stay there, alive but not wandering off."""
        now = self.owner.clock() if now is None else now
        commanded = self.owner.commanded
        self._anchor = {ch: commanded.get(ch, default) for ch, default in ((0, NECK_CENTER), *DRIVE_HEAD)}
        self.gaze, self._anchor_until = 'direction', now + DIRECTION_SECONDS
        self.owner.gaze_pitch = head_pose(self._anchor)[0]

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
        """Head targets for this phrase, or () for a small drift where it already points.

        The head is a neck pulse, a lift pulse, and a pitch and side tilt for the two head
        servos (fractions of their measured travel, servo_poses.head_pulses)."""
        commanded = self.owner.commanded
        level = dict(DRIVE_HEAD)
        current = {ch: commanded.get(ch, level.get(ch, NECK_CENTER)) for ch in HEAD}
        pitch, tilt = head_pose(current)
        rng = self.rng
        def look(neck, lift, pitch, tilt):
            if calm:
                # During conversation work the camera view must stay settled: small moves only.
                neck = max(current[0] - HEAD_SMALL[0] + 20, min(current[0] + HEAD_SMALL[0] - 20, neck))
                lift = max(current[1] - HEAD_SMALL[1] + 20, min(current[1] + HEAD_SMALL[1] - 20, lift))
                pitch = max(pitch0 - PITCH_SMALL, min(pitch0 + PITCH_SMALL, pitch))
                tilt = max(tilt0 - TILT_SMALL, min(tilt0 + TILT_SMALL, tilt))
            return ((0, neck), (1, lift), *head_pulses(pitch, tilt).items())
        pitch0, tilt0 = pitch, tilt
        def near(anchor, neck=60, lift=50, pitch_by=.03, tilt_by=.06):
            anchor_pitch, anchor_tilt = head_pose(anchor)
            return look(anchor[0] + rng.randint(-neck, neck), anchor.get(1, level[1]) + rng.randint(-lift, lift),
                        anchor_pitch + rng.uniform(-pitch_by, pitch_by), anchor_tilt + rng.uniform(-tilt_by, tilt_by))
        if self.mode(now) == 'direction':
            self._doing('holding_chosen_direction', now)
            return near(self._anchor), rng.randint(10, 14), 0.
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
            return (), rng.randint(10, 14), 0.          # Still looking away; drift there.
        if aim is not None and aim.get('at', now) > self.owner.head_moved_at + .1:
            if now >= self._glance_at and not calm:
                # Break eye contact like V2's gaze engine: 12-24 degrees aside, level or a
                # little down, never up, with a slight cock of the head, then back.
                self._glance_until = now + rng.uniform(1.4, 2.8)
                self._glance_at = self._glance_until + rng.uniform(7., 16.)
                self._doing('glancing_away', now)
                neck = aim['neck'] + rng.choice((-1, 1)) * rng.uniform(12., 24.) * NECK_PER_DEG
                glance_pitch = pitch_from_degrees(degrees_from_pitch(pitch) - rng.uniform(0., 5.))
                return look(neck, current[1], glance_pitch, tilt + rng.choice((-1, 1)) * rng.uniform(.1, .25)), rng.randint(12, 18), 0.
            self._doing('following_people', now)
            error = aim['neck'] - current[0]
            neck = current[0] + (FACE_GAIN * error if abs(error) > NECK_DEAD_DEG * NECK_PER_DEG else rng.randint(-160, 160))
            off = aim['y'] - FACE_Y
            if abs(off) > Y_DEAD:
                # A face low in the frame needs the head pitched lower. The travel is
                # asymmetric, so work in degrees above level and convert back.
                degrees = degrees_from_pitch(pitch) - FACE_GAIN * off * 2 * VERTICAL_HALF_FOV
                pitch = pitch_from_degrees(degrees)
            self.owner.gaze_pitch = pitch
            far = abs(error) > 12 * NECK_PER_DEG
            # Attentive listening, as a dog does: every 10-25 s the head settles into a gentle
            # cock to one side for 3-8 s, then straightens (owner, October 10: a little more tilt).
            if now >= self._cock_at:
                self._cock = rng.choice((-1, 1)) * rng.uniform(.15, .3)
                self._cock_until = now + rng.uniform(3., 8.)
                self._cock_at = self._cock_until + rng.uniform(10., 25.)
            tilt = (self._cock if now < self._cock_until else 0.) + rng.uniform(-.05, .05)
            pitch += rng.uniform(-.04, .04)   # A living nod and lean around the framing, never a fixed stare.
            return look(neck, level[1] + rng.randint(-110, 110), pitch, tilt), 18 if far else 13, 0.
        if aim is not None or now - self._face_at <= FACE_LOST_SECONDS:
            return (), rng.randint(10, 14), 0.          # Waiting for a fresh look at them.
        # Nobody in view: look around the room now and then (V2's scan leaned downward,
        # where seated people are), drifting in place between looks.
        self._glance_at = None
        if now >= self._scan_at and not calm:
            self._scan_at = now + rng.uniform(6., 13.)
            self.owner.gaze_pitch = None
            self._doing('looking_around_the_room', now)
            vertical = rng.uniform(-.3, 1.)           # Negative looks up, positive down.
            neck = NECK_CENTER + rng.uniform(-LOOK_NECK_DEG, LOOK_NECK_DEG) * NECK_PER_DEG
            return (look(neck, level[1] - vertical * 200, -vertical * .65, rng.uniform(-.3, .3)),
                    rng.randint(12, 18), rng.uniform(.4, 1.2))
        return (), rng.randint(10, 14), 0.

    def _compose(self, now, calm):
        held = set(self.owner.held_channels)
        for part, span in PART_SECONDS.items():
            # First times are spread out so startup is not one synchronized move.
            self._due.setdefault(part, now + self.rng.uniform(0., span[1] if part != 'head' else .5))
        if self._swing_at is None:
            self._swing_at = now + self.rng.uniform(0., SWING_SECONDS[1])
        due = sorted((at, part) for part, at in self._due.items()
                     if at <= now and now >= self._rest.get(part, float('-inf'))
                     and not held.intersection(IDLE_CHANNELS[part]))
        if not due:
            return None
        chosen = [part for _, part in due[:PHRASE_PARTS]]
        # The array stays put while Rex listens or works, and while a person has told him to keep his arms still.
        swing = ('hero' in chosen and now >= self._swing_at and not calm
                 and not getattr(self.owner, 'hold_until_released', False))
        if swing:
            self._swing_at = now + self.rng.uniform(*SWING_SECONDS)
        if 'head' in chosen:
            chosen.insert(0, chosen.pop(chosen.index('head')))   # The gaze answers first.
        wait, parts = 0., []
        for part in chosen:
            parts.append((part, round(wait, 2)))
            wait += self.rng.uniform(.2, .8)
            self._due[part] = now + self.rng.uniform(*PART_SECONDS[part])
        settle = False
        if 'poker' in chosen:
            if self._tail_until is None:
                self._tail_until = now + self.rng.uniform(*TAIL_SWAY_SECONDS)   # A bout begins.
            elif now >= self._tail_until:
                settle, self._tail_until = True, None
                self._due['poker'] = now + self.rng.uniform(*TAIL_REST_SECONDS)
        head, speed, dwell = self._head(now, calm) if 'head' in chosen else ((), 9, 0.)
        return IdlePhrase(tuple(parts), tuple((ch, round(value)) for ch, value in head), speed, dwell, swing, settle)

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

    def wake(self, seconds=6., delay=0.):
        """The wheels just stopped: the head goes back to the people now and the other parts
        resume, without the usual pause and while the throttle arm is still returning.

        Owner, October 3 (twice): after a drive everything waited for the arm to un-park.
        Releasing the servos was not enough: idle movement waited two seconds to resume and
        stayed still whenever he was listening or answering, which is most moments after a
        drive. For these few seconds neither stills him; the arm is moving anyway.

        delay: seconds of stillness first. October 5: a picture asked for after a drive
        failed because the head set off wandering 10 ms after the wheels stopped and the
        camera counted as moving for the whole capture window."""
        with self._lock:
            if self.closed: return
            now = self.owner.clock()
            self._wake_at, self._wake_until, self._resume, self._allowed = now + delay, now + delay + seconds, now + delay, True
            for part in ('head', 'visor', 'hero', 'poker'):
                self._due[part] = now + delay
            self._rest.clear()
            self._scan_at = now + delay  # With nobody in view he looks around at once.
            if not delay and not self.busy():
                self._start(now, calm=False)

    def tick(self, allowed: bool, calm: bool = False):
        with self._lock:
            if self.closed: return
            now = self.owner.clock()
            if self._wake_at <= now < self._wake_until and not self.busy():
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
                self._resume = now + self.rng.uniform(.15, .35)
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
