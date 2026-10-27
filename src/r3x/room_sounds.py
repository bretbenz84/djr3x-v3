"""Household sounds heard in the room, logged and never acted on (owner, October 26).

The owner asked Rex to listen to the room all the time with CED-Tiny and log any doorbell, knock, dog
or alarm, with no reaction yet, so false alarms can be counted before he ever responds to one. Each
event keeps a short clip to listen to. Nothing here reaches the conversation, the speech detector or
his movement; a look runs on its own thread and the listening loop only hands it frames.
docs/ROOM_SOUNDS.md.
"""
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import statistics
import threading
import time
from uuid import uuid4

from .contracts import ProviderFailure
from .dataset import write_wav
from .providers import Capability


# AudioSet classes (index: name) grouped as the owner named them.
GROUPS = {
    'doorbell': {355: 'Doorbell', 356: 'Ding-dong'},
    'knock': {359: 'Knock'},
    'dog': {74: 'Dog', 75: 'Bark', 76: 'Yip', 77: 'Howl', 78: 'Bow-wow', 79: 'Growling', 80: 'Whimper (dog)'},
    'alarm': {388: 'Alarm', 395: 'Alarm clock', 399: 'Smoke detector, smoke alarm', 400: 'Fire alarm'},
}
VOICE_CLASSES = (0, 4, 5)   # "Speech", "Conversation", "Narration, monologue": a false alarm on talk shows here.
MUSIC_CLASS = 137
WINDOW_SECONDS = 2.     # Each look hears this much of the room...
HOP_SECONDS = 1.        # ...once a second, so every moment is heard twice.
# A group scoring this much logs an event. On the saved September clips CED-Tiny's dog score at .35
# caught one of Max's two barks and fired on a cough, the TV and the owner (docs/VOICE_CHECK.md);
# logging from .3 keeps near misses, so an alarm level can be chosen from this house's own sounds.
LOG_SCORE = .3
EVENT_GAP_SECONDS = 2.  # Looks below the log score for this long end an event.
EVENT_MAX_SECONDS = 30. # A longer one (an alarm left sounding) is logged in parts.
CLIP_PAD_SECONDS = 1.   # Room sound kept either side of an event.
CLIP_MAX_SECONDS = 12.  # Around the highest-scoring look when an event runs longer.
CLIPS_PER_RUN = 200     # At most about 77 MB a run; later events are still logged, without a clip.
FAILURES_TO_STOP = 3    # Model failures in a row before the listener gives up for the run.
SUMMARY_SECONDS = 600.  # How often the listening totals are logged.
BUFFER_SECONDS = EVENT_MAX_SECONDS + EVENT_GAP_SECONDS + WINDOW_SECONDS + 2 * CLIP_PAD_SECONDS


@dataclass
class Event:
    event_id: str
    group: str
    started_at: float
    ended_at: float
    peak: float
    peak_class: str
    peak_window: tuple[float, float]
    voice: float
    music: float
    looks: int = 1
    rex_sound: bool = False
    servos: bool = False
    wheels: bool = False


class RoomSounds:
    def __init__(self, model, root: Path, *, threaded=True, wall=time.time):
        """model: adapters.voice_check.VoiceCheck (tags). root: clips go in root/<session id>/.
        threaded=False looks inline from feed (tests)."""
        self.model, self.root, self.threaded, self.wall = model, root, threaded, wall
        self.trace = None
        self.capability = Capability("room_sounds", "CED-Tiny ONNX",
            "logs doorbells, knocks, dogs and alarms heard in the room; no reactions", None, (),
            (str(root), f"a {WINDOW_SECONDS:g} s look every {HOP_SECONDS:g} s"), required=False,
            requires=("voice_check",))
        self.frames = deque()   # (started_at, ended_at, pcm, rex_sound, servos, wheels)
        self.lock = threading.Lock()
        self.due = threading.Event()
        self.stopping = threading.Event()
        self.thread = None
        self.started = self.listening = False
        self.open: dict[str, Event] = {}
        self.looked_to = None   # Where the last look's sound ended.
        self.heard = self.missed = 0.
        self.look_ms = []       # Since the last summary.
        self.counts = {group: 0 for group in GROUPS}
        self.saved = self.failures = 0
        self.summary_at = None

    def start(self):
        if self.trace is None:
            raise ProviderFailure("RoomSoundsTraceMissing")
        if self.root.resolve() != self.root.absolute():
            raise ProviderFailure("RoomSoundsFolderLinked")
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.started = self.listening = True
        self.summary_at = self.trace.clock() + SUMMARY_SECONDS
        self.trace.emit('room_sounds', status='listening', clips=str(self.root / self.trace.session_id),
                        log_score=LOG_SCORE, groups=sorted(GROUPS))
        if self.threaded:
            self.thread = threading.Thread(target=self._run, name='r3x-room-sounds', daemon=True)
            self.thread.start()

    def cancel(self):
        self.stopping.set()
        self.due.set()

    def close(self):
        self.cancel()
        if self.thread is not None:
            self.thread.join(2.)
            if self.thread.is_alive():
                raise ProviderFailure("RoomSoundsWorkerDidNotStop")
            self.thread = None
        if self.started:
            self.started = self.listening = False
            for group in list(self.open):
                self._finish(group)   # What was still sounding at shutdown.
            self._summary(final=True)
        with self.lock:
            self.frames.clear()

    def feed(self, frame, *, rex_sound=False, wheels=False):
        """Every frame from the listening loop; kept for the next look, never waited on.
        rex_sound: his think music under the room (his speech and effects are the frame's echo_risk)."""
        if not self.listening or self.stopping.is_set():
            return
        with self.lock:
            self.frames.append((frame.started_at, frame.ended_at, frame.pcm,
                                bool(rex_sound or frame.echo_risk), bool(frame.motion_risk), bool(wheels)))
            while self.frames and frame.ended_at - self.frames[0][1] > BUFFER_SECONDS:
                self.frames.popleft()
            due = (frame.ended_at - self.frames[0][0] >= WINDOW_SECONDS and
                   (self.looked_to is None or frame.ended_at - self.looked_to >= HOP_SECONDS))
        if due:
            if self.threaded:
                self.due.set()
            else:
                self.step()

    def _run(self):
        while not self.stopping.is_set():
            self.due.wait(.5)
            self.due.clear()
            if self.stopping.is_set():
                return
            try:
                self.step()
            except Exception as exc:
                self.listening = False
                self.trace.emit('room_sounds', status='stopped', reason='failed:' + type(exc).__name__)
                return

    def step(self):
        """One look at the latest sound, when one is due."""
        with self.lock:
            if not self.frames:
                return
            end = self.frames[-1][1]
            if self.looked_to is not None and end - self.looked_to < HOP_SECONDS:
                return
            window = []
            for frame in reversed(self.frames):
                if frame[1] <= end - WINDOW_SECONDS:
                    break
                window.append(frame)
            window.reverse()
            start = window[0][0]
            new = end - (self.looked_to if self.looked_to is not None else start)
            self.heard += min(new, WINDOW_SECONDS)
            self.missed += max(0., new - WINDOW_SECONDS)   # The worker fell behind.
            self.looked_to = end
        began = time.perf_counter()
        try:
            scores = self.model.tags(b''.join(f[2] for f in window))
        except Exception as exc:
            self.failures += 1
            self.trace.emit('provider_failed', capability='room_sounds', error_type=type(exc).__name__)
            if self.failures >= FAILURES_TO_STOP:
                self.listening = False
                self.trace.emit('room_sounds', status='stopped', reason='model_failed')
            return
        self.failures = 0
        self.look_ms.append((time.perf_counter() - began) * 1000)
        rex_sound, servos, wheels = (any(f[i] for f in window) for i in (3, 4, 5))
        voice = float(max(scores[i] for i in VOICE_CLASSES))
        for group, classes in GROUPS.items():
            index = max(classes, key=lambda i: scores[i])
            score = float(scores[index])
            hit = score >= LOG_SCORE
            event = self.open.get(group)
            if event is not None and ((not hit and end - event.ended_at >= EVENT_GAP_SECONDS) or
                                      (hit and end - event.started_at > EVENT_MAX_SECONDS)):
                self._finish(group)
                event = None
            if not hit:
                continue
            if event is None:
                event = self.open[group] = Event(uuid4().hex, group, start, end, score, classes[index],
                                                 (start, end), voice, float(scores[MUSIC_CLASS]), 0)
            event.ended_at = end
            event.looks += 1
            event.rex_sound |= rex_sound
            event.servos |= servos
            event.wheels |= wheels
            if score > event.peak:
                event.peak, event.peak_class, event.peak_window = score, classes[index], (start, end)
                event.voice, event.music = voice, float(scores[MUSIC_CLASS])
        if self.trace.clock() >= self.summary_at:
            self._summary()
            self.summary_at += SUMMARY_SECONDS

    def _finish(self, group):
        event = self.open.pop(group)
        self.counts[group] += 1
        clip, skipped = None, None
        if self.saved >= CLIPS_PER_RUN:
            skipped = 'clip_limit'
        else:
            middle = sum(event.peak_window) / 2
            began = max(event.started_at - CLIP_PAD_SECONDS, middle - CLIP_MAX_SECONDS / 2)
            ended = min(event.ended_at + CLIP_PAD_SECONDS, began + CLIP_MAX_SECONDS)
            with self.lock:
                pcm = b''.join(f[2] for f in self.frames if f[1] > began and f[0] < ended)
            try:
                folder = self.root / self.trace.session_id
                folder.mkdir(mode=0o700, exist_ok=True)
                path = folder / f'{group}-{event.event_id}.wav'
                write_wav(path, pcm)
                clip = path.name
                self.saved += 1
            except OSError as exc:
                skipped = 'write_failed:' + type(exc).__name__
        local = datetime.fromtimestamp(self.wall() - (self.trace.clock() - event.started_at))
        self.trace.emit('room_sound', event_id=event.event_id, group=group,
                        local_time=local.isoformat(timespec='seconds'), started_at=round(event.started_at, 3),
                        seconds=round(event.ended_at - event.started_at, 1), peak_score=round(event.peak, 3),
                        peak_class=event.peak_class, looks=event.looks, voice_score=round(event.voice, 3),
                        music_score=round(event.music, 3), rex_sound=event.rex_sound, servos=event.servos,
                        wheels=event.wheels, clip=clip, **({'clip_skipped': skipped} if skipped else {}))

    def _summary(self, final=False):
        ms = self.look_ms
        self.trace.emit('room_sounds_listened', final=final, heard_seconds=round(self.heard),
                        missed_seconds=round(self.missed), looks=len(ms),
                        look_ms_median=round(statistics.median(ms), 1) if ms else None,
                        look_ms_max=round(max(ms), 1) if ms else None, events=dict(self.counts), clips=self.saved)
        self.look_ms = []
