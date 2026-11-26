"""Dancing (owner, November 25): asked to dance with no music playing, Rex dances for DANCE_SECONDS;
while a song from the Music app plays he dances to it for as long as it is heard.

A dance is a run of short phrases of DANCE_PHRASE_BEATS beats, each one servo request through the sole
servo owner, so a stop, a line of his, a person speaking or a person's pose takes the servos back between
or in the middle of phrases exactly as it does from idle movement. Each phrase bobs and sways the head and
swings all three arms through poses talking, idle and the short arm dance already use (the throttle hand
never lower than a talking pose, the microphone arm still); every bar the eyes, mouth glow and chest take
the next party colour. The tempo is the song's BPM from the Music app when the library has one, else a
typical tempo for its genre; nothing listens for the beat, so the moves keep the song's pace, not its
downbeat. A dance asked for without music keeps DEFAULT_BPM.

Held channels (a person's pose, an arm kept clear of an obstacle, a hand holding something) are left
out of every phrase. Dancing never moves the wheels.
"""
from dataclasses import dataclass
import math
import random
import threading

from .contracts import Cancellation
from .servo_poses import (DANCE_POKER, DANCE_THROTTLE, FLAIR_ARM, HEAD, HEAD_PAIR, HEAD_LIFT_NORMAL, LIMITS,
                          NECK_CENTER, NECK_PER_DEG, POKER, THROTTLE, Beat, _poker_stages, eased_beat,
                          head_pose, head_pulses, throttle_clear, throttle_route, validate_pose)

DANCE_SECONDS = 30.          # Asked to dance with no music (owner, November 25).
DEFAULT_BPM = 112.
DANCE_PHRASE_BEATS = 8       # Two bars per servo request.
# The folded tempo: a song's BPM is halved or doubled into this range, since the servos cannot swing at
# 170 and 60 looks asleep.
BPM_RANGE = (84., 140.)
# Typical tempos when the library has no BPM for the song (most streamed songs read 0).
GENRE_BPM = {'hip-hop': 92, 'hip hop': 92, 'rap': 92, 'r&b': 96, 'soul': 100, 'reggae': 88, 'blues': 92,
             'country': 110, 'rock': 120, 'alternative': 120, 'pop': 116, 'dance': 124, 'electronic': 124,
             'house': 124, 'disco': 120, 'funk': 108, 'metal': 130, 'punk': 140, 'latin': 104, 'jazz': 112,
             'classical': 96, 'soundtrack': 104, 'christmas': 112, 'holiday': 112}
PARTY_COLOURS = ('excited', 'happy', 'curious', 'proud', 'surprised', 'sympathetic')
# Shortest swing each part keeps up with (servo_poses' short arm dance): faster beats take half time.
LEAST_SECONDS = {'head': .3, 'hero': .45, 'poker': .7, 'throttle': .8}
NECK_SWAY_DEG = 9.
HEAD_BOB = .12               # Pitch fraction down and up on each beat.
HEAD_SWAY_TILT = .22
HEAD_LIFT_BOB = 160


def beat_seconds(bpm=None, genre=None) -> float:
    """Seconds per beat for a song's BPM (0 or None: its genre's typical tempo), folded into BPM_RANGE."""
    tempo = float(bpm or 0)
    if not tempo:
        genre = (genre or '').lower()
        tempo = next((float(v) for k, v in GENRE_BPM.items() if k in genre), DEFAULT_BPM)
    while tempo > BPM_RANGE[1]:
        tempo /= 2
    while tempo < BPM_RANGE[0]:
        tempo *= 2
    return 60. / tempo


@dataclass(frozen=True)
class DancePhrase:
    beat_seconds: float
    beats: int = DANCE_PHRASE_BEATS
    seed: int = 0
    home: tuple[tuple[int, int], ...] = ()   # The last phrase ends each arm here (where the dance began).


def _every(part, beat):
    """How many beats one swing of this part spans at this tempo."""
    return max(1, math.ceil(LEAST_SECONDS[part] / beat - 1e-9))


def dance_lanes(current, phrase: DancePhrase, held=frozenset()):
    """One phrase of dancing: a lane per part not held, each ending where the next phrase picks up (or,
    for the last phrase, at its home pose)."""
    rng = random.Random(phrase.seed)
    beat, beats, held = phrase.beat_seconds, phrase.beats, set(held)
    home = dict(phrase.home)
    style = rng.choice(('bob', 'bob_sway', 'sway_lift'))
    lanes = []
    if not held.intersection(HEAD) and set(HEAD).issubset(current):
        at, lane = dict(current), []
        pitch0, _ = head_pose(current)
        pitch0 = max(-.45, min(.3, pitch0))
        room = round(NECK_SWAY_DEG * NECK_PER_DEG) + 20
        neck0 = max(LIMITS[0][0] + room, min(LIMITS[0][1] - room, current[0]))
        side = rng.choice((-1, 1))
        for i in range(beats):
            down = i % 2 == 0
            swing = (i // 2) % 2 == 0
            pitch = pitch0 - HEAD_BOB if down else pitch0 + HEAD_BOB * .4
            tilt = side * HEAD_SWAY_TILT * (1 if swing else -1) if style != 'bob' else 0.
            if i == beats - 1:
                pitch, tilt = pitch0, 0.   # Level again where it began, ready for the next phrase.
            # The sway ends each phrase back in the middle, so phrase after phrase it never drifts off.
            neck = (neck0 + side * NECK_SWAY_DEG * NECK_PER_DEG * (1 if swing else -1)
                    if style != 'bob' and i < beats - 1 else neck0)
            lift = HEAD_LIFT_NORMAL + (HEAD_LIFT_BOB if not down else -HEAD_LIFT_BOB) if style == 'sway_lift' else current[1]
            targets = {0: round(neck), 1: round(max(LIMITS[1][0], min(LIMITS[1][1], lift)))}
            for lean in (tilt, tilt / 2, 0.):
                targets.update(head_pulses(pitch, lean))
                try:
                    validate_pose(targets)
                    break
                except ValueError:
                    continue   # Too far for the head pair's envelope at this pitch: lean less.
            seconds = max(LEAST_SECONDS['head'], beat)
            lane.append(eased_beat(at, tuple(targets.items()), seconds * .9, dwell=seconds * .1))
            at.update(targets)
        if home and set(HEAD).issubset(home):
            back = {ch: home[ch] for ch in HEAD}
            lane.append(eased_beat(at, tuple(back.items()), .6))
        lanes.append(tuple(lane))
    if not held.intersection((4, 5)):
        every = _every('hero', beat)
        at, lane, flip = dict(current), [], rng.randint(0, 1)
        for i in range(max(1, beats // every)):
            aim = FLAIR_ARM[(i + flip) % 2]
            lane.append(eased_beat(at, aim, every * beat * .9, dwell=every * beat * .1))
            at.update(aim)
        if home:
            lane.append(eased_beat(at, tuple((ch, home[ch]) for ch in (4, 5)), .5))
        lanes.append(tuple(lane))
    if not held.intersection((6, *POKER)):
        every = _every('poker', beat)
        start = {ch: current[ch] for ch in (6, *POKER)}
        stages = _poker_stages(start, DANCE_POKER[0])
        stages += [dict(DANCE_POKER[i % 2]) for i in range(1, max(2, beats // every))]
        if home:
            stages += _poker_stages(stages[-1], {ch: home[ch] for ch in (6, *POKER)})
        at, lane = dict(current), []
        for stage in stages:
            lane.append(eased_beat(at, tuple(stage.items()), every * beat))
            at.update(stage)
        lanes.append(tuple(lane))
    if not held.intersection(THROTTLE):
        every = _every('throttle', beat)
        start = {ch: current[ch] for ch in THROTTLE}
        stages = throttle_route(current, dict(DANCE_THROTTLE[0]))
        stages += [dict(DANCE_THROTTLE[i % 2]) for i in range(1, max(2, beats // every))]
        if home:
            stages += throttle_route(stages[-1], {ch: home[ch] for ch in THROTTLE})
        previous = start
        for stage in stages:
            if not throttle_clear(previous, stage):
                raise ValueError('ThrottleRouteOutsideClearance')
            previous = stage
        lanes.append(tuple(Beat(tuple(stage.items()), throttle_duration=max(.8, every * beat), dwell=.05)
                           for stage in stages))
    for lane in lanes:
        for one in lane:
            validate_pose(dict(one.targets))
    return tuple(lane for lane in lanes if lane)


class Dance:
    """The dance scheduler beside idle movement. RobotServos ticks it every frame; while it wants the
    servos idle movement stays stopped."""

    def __init__(self, owner, *, rng=None):
        self.owner = owner
        self.rng = rng or random.Random()
        self.trace = None
        self.lights = None         # The LED owner, when he has lights (react/finish/settle).
        self._lock = threading.Lock()
        self._thread = None
        self._cancel = None
        self._index = 0
        self._until = None         # A requested dance ends then; None: no requested dance.
        self._music = None         # Seconds per beat while a song is heard; None: no song.
        self._song = None          # The song heard (or paused for talk).
        self._off_song = None      # "Stop dancing" during this song: not danced to again.
        self._home = None          # Where the arms and head were when the dance began.
        self._colour = 0
        self._light = None         # Token of the bar's colours.
        self.closed = False

    # What people asked ------------------------------------------------
    def start(self, seconds=DANCE_SECONDS, reason='asked') -> str:
        with self._lock:
            if self.closed or self.owner.state != 'active':
                return 'unavailable'
            now = self.owner.clock()
            was = self.wanted(now)
            self._until = now + seconds
            self._off_song = None
            if not was:
                self._began(now)
            self._emit('dance', status='started', reason=reason, seconds=seconds,
                       to_music=self._music is not None)
            return 'started'

    def stop(self, reason='asked') -> bool:
        """Stop dancing now (a stop phrase, "stop dancing"). A song still playing is not danced to again."""
        with self._lock:
            was = self.wanted(self.owner.clock()) or self._thread is not None
            self._until = None
            self._off_song = self._song
            self._halt()
            if was:
                self._ended(reason)
            return was

    def music(self, playing: bool, seconds_per_beat: float | None = None, song=None):
        """The runtime's tick: whether a song is coming out of his speaker now, which, and its beat."""
        with self._lock:
            now = self.owner.clock()
            was = self.wanted(now)
            self._music = seconds_per_beat if playing and seconds_per_beat else None
            if playing:
                self._song = song
            if self.wanted(now) and not was:
                self._began(now)
                self._emit('dance', status='started', reason='music', beat_seconds=round(self._music, 3))
            elif was and not self.wanted(now):
                self._halt()
                self._ended('music_stopped')

    def state(self, now=None):
        """For the reply context: what the dance is doing."""
        now = self.owner.clock() if now is None else now
        if self._until is not None and now < self._until:
            return {'dancing': True, 'seconds_left': round(self._until - now), 'to_music': self._music is not None}
        if self._music is not None and not self._dance_off():
            return {'dancing': True, 'to_music': True}
        return {'dancing': False, **({'stopped_for_this_song': True} if self._music and self._dance_off() else {})}

    def _dance_off(self):
        return self._off_song is not None and self._off_song == self._song

    # Scheduling --------------------------------------------------------
    def wanted(self, now=None) -> bool:
        now = self.owner.clock() if now is None else now
        if self._until is not None and now >= self._until:
            self._until = None
        return self._until is not None or (self._music is not None and not self._dance_off())

    def engaged(self) -> bool:
        """Wanted, or still finishing (a phrase under way, the lights not yet plain)."""
        return self.wanted() or self._thread is not None or self._light is not None or self._home is not None

    def pause(self):
        """Something else needs the servos now (a line, a person speaking, a person's pose): the phrase
        under way stops where it is; the dance carries on later if it still has time."""
        with self._lock:
            self._halt()

    def close(self):
        with self._lock:
            self.closed = True
            self._until, self._music = None, None
            self._halt()
            self._lights_off()

    def tick(self, allowed: bool) -> bool:
        """True while the dance wants the servos (idle movement then stays still)."""
        with self._lock:
            if self.closed:
                return False
            now = self.owner.clock()
            if self._thread is not None:
                if not allowed:
                    self._halt()
                    return self.wanted(now)
                if self._thread.is_alive():
                    return True   # The phrase under way finishes, the last one's way home included.
                self._thread = None
            if not self.wanted(now):
                if self._home is not None or self._light is not None:
                    self._ended('time_up')
                return False
            if not allowed or self.owner.busy or self.owner.state != 'active' or getattr(self.owner, '_base_held', False):
                return True
            self._phrase(now)
            return True

    def _phrase(self, now):
        from .servos import ServoRequest
        beat = self._music if self._music is not None and not self._dance_off() else 60. / DEFAULT_BPM
        beats = DANCE_PHRASE_BEATS
        last = self._until is not None and self._music is None and now + beats * beat >= self._until
        if self._until is not None and self._music is None:
            beats = max(2, min(beats, round((self._until - now) / beat)))
        home = tuple(sorted(self._home.items())) if last and self._home else ()
        self._index += 1
        phrase = DancePhrase(beat, beats, self.rng.randrange(1 << 30), home)
        request = ServoRequest(f'dance:{self._index}', self.owner.version, 'dance', now + 60., dance=phrase)
        token = self._cancel = Cancellation()
        self._bar_lights()
        def run():
            result = self.owner.execute(request, token)
            if result.status in ('failed', 'rejected') and self.trace:
                self.trace.emit('dance_phrase', status=result.status, reason=result.reason)
            if last and result.status == 'pulses_reached':
                with self._lock:
                    self._until = None
        self._thread = threading.Thread(target=run, name='r3x-dance', daemon=True)
        self._thread.start()

    def _began(self, now):
        commanded = self.owner.commanded
        keep = (*HEAD, 4, 5, 6, *POKER, *THROTTLE)
        self._home = {ch: commanded[ch] for ch in keep if ch in commanded} if all(
            ch in commanded for ch in keep) else None

    def _halt(self):
        if self._cancel:
            self._cancel.event.set()
        if self._thread is not None:
            self._thread.join(timeout=2.)
            self._thread = None

    def _ended(self, reason):
        self._home = None
        self._lights_off()
        self._emit('dance', status='ended', reason=reason)

    def _bar_lights(self):
        if self.lights is None:
            return
        try:
            self._light = self.lights.react(PARTY_COLOURS[self._colour % len(PARTY_COLOURS)]) or self._light
            self._colour += 1
        except Exception:
            pass   # The lights are optional; a missing board never stops the dance.

    def _lights_off(self):
        if self.lights is None or self._light is None:
            return
        token, self._light = self._light, None
        try:
            self.lights.finish(token)
            self.lights.settle()
        except Exception:
            pass

    def _emit(self, event, **fields):
        if self.trace:
            self.trace.emit(event, **fields)
