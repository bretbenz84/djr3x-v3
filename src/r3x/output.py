"""The sole speech owner. Generated text is never a delivery receipt."""
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from queue import Empty, Full, Queue
import re
import threading
import time

from .contracts import Cancellation, Cancelled, ProviderFailure, Sink, TTS
from .performance import Performance
from .leds import LightPerformance


TAGS = frozenset({"sarcastic", "curious", "excited", "mischievously", "laughs",
                  "sighs", "whispers", "snorts", "exhales"})


def speech_text(text: str) -> str:
    """Tag filtering is output syntax, never language interpretation."""
    count = 0
    def tag(match):
        nonlocal count
        if match.group(1).lower() in TAGS and count < 2:
            count += 1
            return match.group(0)
        return ""
    return re.sub(r"\[([^\]]*)\]", tag, text).strip()


def plain_text(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", "", text).strip()


class Phrases:
    """Bounded speech chunks while the one model generation is still streaming."""
    def __init__(self):
        self.pending = ""

    def add(self, delta: str) -> list[str]:
        self.pending += delta
        chunks = []
        while self.pending:
            # Only a mark followed by whitespace ends a phrase: mid-stream, "1." may be the
            # start of "1.42" (October 3: "rolled 1. 42 meters" was heard as forty-two meters).
            cut = next((m.end() for m in re.finditer(r"[.!?]\s", self.pending)
                        if m.end() >= 25), 0)
            if not cut and len(self.pending) >= 320:
                cut = self.pending.rfind(" ", 0, 320)
                if cut < 1:
                    cut = 320
            if not cut:
                break
            chunks.append(self.pending[:cut].strip())
            self.pending = self.pending[cut:]
        return chunks

    def finish(self) -> str:
        result, self.pending = self.pending.strip(), ""
        return result


@dataclass
class Delivery:
    status: str = "requested"
    completed_text: list[str] = field(default_factory=list)
    first_audio_at: float | None = None
    error: str | None = None
    underruns: int = 0
    medium: str = "audio"
    gestures: list[dict] = field(default_factory=list)
    completed_at: float | None = None
    lights: list[dict] = field(default_factory=list)


@dataclass
class ClonedPhrase:
    """Words performed in another voice: a prepared Take from the voice cloner."""
    text: str
    take: object
    voice: str


@dataclass(frozen=True)
class Sound:
    """A sound effect played between lines (Jeopardy's stings and Final music, October 26). Not words: it is
    never heard text. While it plays the microphone is gated as for his speech, so it is not taken for a person."""
    name: str
    pcm: bytes


class MusicBed:
    """A looping clip under the room: Jeopardy's think music while players answer (October 26). It plays on
    the speech sink without gating the microphone, so answers are still heard, and the output owner stops it
    before anything else plays (one stream at a time). stop(wait=False) is safe from the listening loop."""
    PIECE = 1024

    def __init__(self, sink, trace=None):
        self.sink, self.trace = sink, trace
        self._thread = None
        self._stop = threading.Event()
        self._cancel = Cancellation()

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def start(self, pcm: bytes) -> bool:
        if self.playing or not pcm:
            return False
        self.stop()
        self._stop, self._cancel = threading.Event(), Cancellation()
        stop, cancel = self._stop, self._cancel
        def pieces():
            while not stop.is_set():
                for i in range(0, len(pcm), self.PIECE):
                    if stop.is_set():
                        return
                    yield pcm[i:i + self.PIECE]
        def run():
            try:
                self.sink.play(pieces(), cancel, lambda at: None, visualize=False)
            except Cancelled:
                pass
            except Exception as exc:   # Music never fails the game.
                if self.trace:
                    self.trace.emit('music_bed_failed', error_type=type(exc).__name__)
        self._thread = threading.Thread(target=run, name='r3x-music-bed', daemon=True)
        self._thread.start()
        return True

    def stop(self, wait=True) -> None:
        thread = self._thread
        self._stop.set()
        self._cancel.event.set()
        if thread is not None and wait:
            thread.join(1.)
            if not thread.is_alive():
                self._thread = None


@dataclass(frozen=True)
class AccentLine:
    """One line in a speaking mode's voice (voice_mode line, October 25); the next is his own again."""
    text: str
    mode: str


class FillerLoop:
    """V2's "thinking" chirps while an impression renders: cycled short effects on the
    speech sink, started only when nothing else is playing and stopped the moment the
    take is ready. Never marks echo risk; the microphone keeps listening."""
    ASSETS = Path(__file__).resolve().parents[2] / 'assets' / 'audio' / 'effects'
    GAIN = .5
    PIECE = 1024

    def __init__(self, sink, trace=None):
        self.sink, self.trace = sink, trace
        self._thread = None
        self._stop = threading.Event()
        self._cancel = Cancellation()

    @classmethod
    def clips(cls):
        import wave
        from array import array
        import random
        clips = []
        for path in sorted(cls.ASSETS.glob('thinking-*.wav')):
            with wave.open(str(path), 'rb') as clip:
                if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != 22050:
                    continue
                samples = array('h', clip.readframes(clip.getnframes()))
                for i in range(len(samples)):
                    samples[i] = int(samples[i] * cls.GAIN)
                clips.append(samples.tobytes())
        random.shuffle(clips)
        return clips

    def start(self):
        try:
            clips = self.clips()
        except (OSError, ValueError):
            clips = []
        if not clips:
            return False
        gap = b'\0' * (2 * 22050 // 2)   # Half a second between chirps.
        def pieces():
            while not self._stop.is_set():
                for clip in clips:
                    for pcm in (clip, gap):
                        for i in range(0, len(pcm), self.PIECE):
                            if self._stop.is_set():
                                return
                            yield pcm[i:i + self.PIECE]
        def run():
            try:
                self.sink.play(pieces(), self._cancel, lambda at: None, visualize=False)
            except Cancelled:
                pass
            except Exception as exc:   # A filler never fails the impression.
                if self.trace:
                    self.trace.emit('filler_sound_failed', error_type=type(exc).__name__)
        self._thread = threading.Thread(target=run, name='r3x-filler', daemon=True)
        self._thread.start()
        return True

    def stop(self, now=False):
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._stop.set()
        if now:
            self._cancel.event.set()
        thread.join(1.)
        if thread.is_alive():
            self._cancel.event.set()
            thread.join(1.)


# A look waits this long for words already playing (a lead-in is one short line).
INSPECTION_WAIT_SECONDS = 8.
# Owner, October 24: he talks while he rolls, in short lines only. A stop said over his voice is
# harder to catch, so more words than this still to say when a drive starts are said first, and the
# wheels wait for them (one short sentence: about 4 s, a metre at top speed).
ROLLING_WORDS = 14
# His own lines this close to a stretch of captured sound may be in it (the stop check sets them aside).
OWN_LINE_MARGIN_S = 1.


class SpeechMark:
    """Queued behind speech that must be heard before a drive starts; set when reached."""
    def __init__(self):
        self.reached = threading.Event()


def spoken_text(item) -> str:
    """The words of a queued output item (a phrase, a performance, a cloned or canned line)."""
    text = item if isinstance(item, str) else getattr(item, 'text', None)
    return plain_text(text) if isinstance(text, str) else ''


class OutputOwner:
    def __init__(self, tts: TTS, sink: Sink, trace, *, attention=None, gestures=None, inspection=None,
                 speech_motion=None, leds=None, robot=None, drive=None, cloner=None):
        self.tts, self.sink, self.trace = tts, sink, trace
        self.cloner = cloner     # Optional voice cloner for model-chosen impressions.
        self.attention = attention
        self.gestures = gestures
        self.inspection = inspection
        self.robot = robot
        self.speech_motion = speech_motion
        self.leds = leds
        self.drive = drive
        self.drive_unavailable = None  # Reason when configured wheels failed to start.
        self._motion_lock = threading.Lock()
        self.active = threading.Event()
        self.echo_until = 0.
        # Talking motion and the drive (owner, October 24: he talks while he rolls). A line's
        # full talking motion holds the motion resource; beside a drive only the visor moves and
        # the drive keeps the resource.
        self._talk_guard = threading.Lock()
        self._talk = None          # 'full' or 'visor' while a line's talking motion runs.
        self._driving = False
        self._saying = None        # The output item being said now.
        self.lines = deque(maxlen=8)   # [started, ended or None, words] of his recent lines.
        self.bed = None            # MusicBed under the room (Jeopardy), stopped before anything plays.

    def execute_inspection(self, request, cancel, valid):
        if self.inspection is None:
            raise ProviderFailure('InspectionUnavailable')
        # October 16 (511ce677): both looks failed at once because the lead-in ("Let me look.")
        # was still playing with its talking motion. Wait for it, briefly, instead.
        deadline = time.monotonic() + INSPECTION_WAIT_SECONDS
        while not self._motion_lock.acquire(timeout=.05):
            cancel.check()
            if time.monotonic() > deadline:
                return {'request_id':request.request_id,'status':'failed','medium':'simulation',
                        'stage':'movement','motion_status':'not_completed','reason':'MotionResourceBusy'}, ()
        try:
            return self.inspection.execute(request,cancel,valid)
        finally:
            self._motion_lock.release()

    def execute_pose(self, name, cancel, request_id, *, hold_pose=True):
        if self.robot is None: raise ProviderFailure('RobotPoseUnavailable')
        with self._motion_lock:
            cancel.check()
            return self.robot.execute_pose(name,cancel,request_id,**({} if hold_pose else {'hold_pose':False}))

    def sentry_motion(self, move, cancel, wait=0.):
        """A sentry body move (robot_servos.sentry_*), never while talking motion or another move owns
        the servos. Returns its receipt, or None when the motion resource stayed busy."""
        if self.robot is None:
            raise ProviderFailure('RobotPoseUnavailable')
        acquired = self._motion_lock.acquire(timeout=wait) if wait else self._motion_lock.acquire(blocking=False)
        if not acquired:
            return None
        try:
            cancel.check()
            return move(self.robot, cancel)
        finally:
            self._motion_lock.release()

    def execute_attention(self, intent, cancel):
        if self.attention is None:
            raise ProviderFailure('AttentionUnavailable')
        cancel.check()
        if not self._motion_lock.acquire(blocking=False):
            raise ProviderFailure('MotionResourceBusy')
        try:
            receipt = self.attention.execute(intent, cancel)
            cancel.check()
            return receipt
        finally:
            self._motion_lock.release()

    def _gesture(self, performance, cancel, turn_id, delivery):
        from dataclasses import asdict
        from .performance import GestureReceipt
        gesture = performance.gesture
        started = self.trace.clock()
        acquired = False
        try:
            cancel.check()
            if started >= gesture.expires_at or not performance.valid():
                raise ProviderFailure('GestureEvidenceStale')
            if self.gestures is None:
                raise ProviderFailure('GestureUnavailable')
            acquired = self._motion_lock.acquire(blocking=False)
            if not acquired:
                raise ProviderFailure('MotionResourceBusy')
            # Speech commitment must not exempt a later physical gesture from
            # human priority or target freshness. Its token does not erase heard audio.
            def gesture_allowed():
                cancel.check()
                return performance.valid()
            motion_cancel = Cancellation(guard=gesture_allowed)
            receipt = self.gestures.execute(gesture, motion_cancel)
            motion_cancel.check()
            cancel.check()
            if not performance.valid():
                raise ProviderFailure('GestureEvidenceStale')
            if (not isinstance(receipt, GestureReceipt) or receipt.request_id != gesture.request_id or
                receipt.medium != getattr(self.gestures,'medium','simulation') or
                receipt.status != ('pulses_reached' if receipt.medium=='controller' else 'simulated') or
                not started <= receipt.at <= self.trace.clock() or receipt.at >= gesture.expires_at):
                raise ProviderFailure('InvalidGestureReceipt')
            performance.delivered()
        except Cancelled:
            receipt = GestureReceipt(gesture.request_id, 'cancelled', self.trace.clock(), medium=getattr(self.gestures,'medium','simulation'), reason='TurnCancelled')
        except Exception as exc:
            receipt = GestureReceipt(gesture.request_id, 'failed', self.trace.clock(),
                medium=getattr(self.gestures,'medium','simulation'), reason=str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__)
        finally:
            if acquired:
                self._motion_lock.release()
        row = {**asdict(receipt), 'primitive':gesture.primitive, 'target':gesture.target,
               'intensity':gesture.intensity, 'timing':performance.timing}
        delivery.gestures.append(row)
        self.trace.emit('gesture_delivery', turn_id=turn_id, **row)
        cancel.check()

    def _light_result(self, delivery, turn_id, request, status, reason=None):
        row = dict(request_id=request.request_id if request else turn_id+':speaking',
            pattern=request.pattern if request else 'neutral',status=status,reason=reason,
            at=self.trace.clock(),medium='serial',evidence='commands_only_display_unverified')
        delivery.lights.append(row)
        self.trace.emit('led_delivery',turn_id=turn_id,**row)

    def _begin_lights(self, light, cancel):
        cancel.check()
        if not self.leds: raise ProviderFailure('LEDUnavailable')
        if light and (not light.valid() or self.trace.clock()>=light.request.expires_at):
            raise ProviderFailure('StaleLEDRequest')
        return self.leds.begin(light.request.pattern if light else 'neutral',
            speaking=light is None or light.timing=='during_speech',
            request=light.request if light else None)

    def _finish_lights(self, token, light, delivery, turn_id, status=None):
        try:
            result = self.leds.finish(token)
            self._light_result(delivery,turn_id,light.request if light else None,
                status if result=='commands_sent' and status else result)
        except Exception as exc:
            self._light_result(delivery,turn_id,light.request if light else None,'failed',str(exc) if isinstance(exc,ProviderFailure) else type(exc).__name__)

    def _light_demo(self, light, cancel, delivery, turn_id):
        token = None
        status = None
        try:
            if light.request.pattern=='blink':
                result = self.leds.blink(light.request,cancel,light.valid)
                self._light_result(delivery,turn_id,light.request,result)
                return
            token = self._begin_lights(light,cancel)
            deadline = self.leds.clock()+light.request.duration
            while self.leds.clock()<deadline:
                cancel.check()
                if not light.valid(): raise ProviderFailure('StaleLEDRequest')
                self.leds.wait(min(.05,deadline-self.leds.clock()))
        except Cancelled:
            status = 'cancelled'
            if token is None:
                self._light_result(delivery,turn_id,light.request,status)
        except Exception as exc:
            status = 'failed'
            if token is None:
                self._light_result(delivery,turn_id,light.request,status,type(exc).__name__)
        finally:
            if token is not None: self._finish_lights(token,light,delivery,turn_id,status)
        cancel.check()

    def await_take(self, take, cancel: Cancellation, *, filler: bool, timeout: float = 60.) -> bool:
        """Wait for a prepared cloned take; play the thinking filler meanwhile when asked.

        The caller has let any preceding phrase finish, so the sink is free for the
        filler. Returns True when the take rendered; False carries take.error.
        """
        loop = None
        deadline = time.monotonic() + timeout
        try:
            while not take.ready.wait(.05):
                cancel.check()
                if time.monotonic() > deadline:
                    take.close()
                    take.error = take.error or 'ImpersonationPreparationTimeout'
                    return False
                if filler and loop is None and not self.active.is_set():
                    loop = FillerLoop(self.sink, self.trace)
                    if not loop.start():
                        loop = None
                        filler = False
            cancel.check()
            return take.error is None
        finally:
            if loop is not None:
                loop.stop(now=cancel.event.is_set())

    def echo_risk(self) -> bool:
        return self.active.is_set() or self.trace.clock() < self.echo_until

    def cancel(self) -> None:
        if self.drive:
            self.drive.cancel()  # Stop the wheels first; speech cancellation follows.
        if self.speech_motion:
            self.speech_motion.cancel()
        self.sink.cancel()
        interrupt = getattr(self.tts, 'interrupt', None)
        if interrupt:
            interrupt()   # A stalled network read must not outlive a stop or shutdown.

    def words_ahead(self, phrases: Queue) -> int:
        """Words still to be said before anything queued now: the line being said (all of it) and the queue."""
        with phrases.mutex:
            queued = list(phrases.queue)
        return sum(len(spoken_text(item).split()) for item in (self._saying, *queued) if item is not None)

    def said_between(self, start: float, end: float) -> str:
        """Rex's own words in the lines playing (or about to) between these times."""
        return ' '.join(words for began, ended, words in list(self.lines)
                        if began <= end + OWN_LINE_MARGIN_S and (ended is None or ended >= start - OWN_LINE_MARGIN_S))

    def start_drive(self, action, phrases: Queue, cancel: Cancellation, turn_id: str) -> None:
        """Run a drive plan beside speech (owner, October 24: he talks while he rolls).

        The arms tuck and the wheels start while the line already playing goes on, and later
        lines play while he rolls. When more than ROLLING_WORDS are still to be said, they are
        said first and the wheels wait, as before. action.done is set once the drive is over."""
        mark = None
        if self.words_ahead(phrases) > ROLLING_WORDS:
            mark = SpeechMark()
            put_phrase(phrases, mark, cancel)
        threading.Thread(target=self._drive, args=(action, cancel, turn_id, mark),
                         name='r3x-drive', daemon=True).start()

    def _drive(self, action, cancel, turn_id, mark=None):
        from .drive import DriveReceipt
        owned = False
        try:
            if mark is not None:
                while not mark.reached.wait(.05):
                    cancel.check()
            if self.drive is None:
                action.receipt = DriveReceipt(action.plan.request_id, 'failed', 'DriveUnavailable')
                return
            with self._talk_guard:
                if self._talk == 'full':
                    # A line is playing with its full talking motion: the head and arms are the
                    # drive's now, the visor carries the rest of the line.
                    try:
                        self.speech_motion.stop()
                    except Exception as exc:
                        self.trace.emit('talking_motion', turn_id=turn_id, status='failed', reason=type(exc).__name__)
                    else:
                        self._talk, owned, self._driving = None, True, True   # Its motion resource passes on.
                        self._talk_beside_drive(turn_id, cancel)
            if not owned:
                owned = self._motion_lock.acquire(timeout=5.)
                if not owned:
                    action.receipt = DriveReceipt(action.plan.request_id, 'refused', 'MotionResourceBusy')
                    return
                with self._talk_guard:
                    self._driving = True
            action.receipt = self.drive.execute(action.plan, cancel)
        except Cancelled:
            action.receipt = DriveReceipt(action.plan.request_id, 'cancelled', 'stop_requested')
        except Exception as exc:
            action.receipt = DriveReceipt(action.plan.request_id, 'failed',
                                          str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__)
        finally:
            with self._talk_guard:
                self._driving = False
                if owned:
                    self._motion_lock.release()
            action.done.set()

    def _talk_beside_drive(self, turn_id, cancel) -> bool:
        """Visor-only talking motion for a line beside a drive (the caller holds _talk_guard)."""
        try:
            cancel.check()
            self.speech_motion.start(turn_id, cancel, visor_only=True)
        except Exception as exc:
            self.trace.emit('talking_motion', turn_id=turn_id, status='failed', reason=type(exc).__name__)
            return False
        self._talk = 'visor'
        return True

    def _start_talking(self, turn_id, cancel) -> bool:
        """Talking motion for a line that began playing; True when this line now owns it."""
        if not self.speech_motion:
            return False
        with self._talk_guard:
            if self._talk:
                return False   # A worker that would not stop keeps the resource reserved.
            if self._driving:
                return self._talk_beside_drive(turn_id, cancel)
            if not self._motion_lock.acquire(blocking=False):
                return False
            try:
                cancel.check()
                self.speech_motion.start(turn_id, cancel)
            except Exception as exc:
                self._motion_lock.release()
                self.trace.emit('talking_motion', turn_id=turn_id, status='failed', reason=type(exc).__name__)
                return False
            self._talk = 'full'
            return True

    def _stop_talking(self, turn_id, finished) -> None:
        """The line ended: its talking motion stops (finished: it played to its end)."""
        from dataclasses import asdict
        with self._talk_guard:
            talk = self._talk
            if talk is None:
                return
            try:
                motion_receipt = self.speech_motion.stop(finished=finished)
            except Exception as exc:
                # Keep the resource reserved if the worker did not stop.
                self.trace.emit('talking_motion', turn_id=turn_id, status='failed', reason=type(exc).__name__)
                return
            self._talk = None
            if talk == 'full':
                self._motion_lock.release()
        if motion_receipt:
            self.trace.emit('talking_motion', turn_id=turn_id, **asdict(motion_receipt))

    def _play_sound(self, sound: Sound, cancel: Cancellation, turn_id: str) -> None:
        """A sound effect: the room's microphone is gated while it plays, as for his speech."""
        if self.bed is not None:
            self.bed.stop()
        def pieces():
            for i in range(0, len(sound.pcm), 4096):
                cancel.check()
                if i == 0:
                    self.active.set()
                yield sound.pcm[i:i + 4096]
        played = False
        try:
            self.sink.play(pieces(), cancel, lambda at: None, visualize=False)
            played = not cancel.event.is_set()
        finally:
            self.echo_until = self.trace.clock() + .3
            self.active.clear()
            self.trace.emit('sound_played', turn_id=turn_id, sound=sound.name, completed=played,
                            seconds=round(len(sound.pcm) / 2 / 22050, 2))

    def run(self, phrases: Queue, cancel: Cancellation, turn_id: str, delivery: Delivery) -> None:
        performance = None
        light = None
        sounds = getattr(self.drive, 'sounds', None)   # The wheels' whir shares this sink.
        hushed = False
        try:
            while True:
                self._saying = None
                cancel.check()
                try:
                    text = phrases.get(timeout=.05)
                except Empty:
                    if hushed:
                        sounds.resume()   # Nothing more to say for now: the whir comes back.
                        hushed = False
                    continue
                if text is None:
                    break
                if isinstance(text, SpeechMark):
                    text.reached.set()   # Everything ahead of it was said: a waiting drive may go.
                    continue
                if isinstance(text, Sound):
                    if sounds and not hushed:
                        sounds.hush()
                        hushed = True
                    self._play_sound(text, cancel, turn_id)
                    continue
                self._saying = text
                light = text if isinstance(text,LightPerformance) else None
                if light:
                    text = light.text or ''
                    if light.timing=='before_speech':
                        self._light_demo(light,cancel,delivery,turn_id)
                        light = None
                    if not text:
                        delivery.status,delivery.medium = 'suppressed','none'
                        continue
                performance = text if isinstance(text, Performance) else None
                if performance:
                    text = performance.text
                cloned = text if isinstance(text, ClonedPhrase) else None
                if cloned:
                    text = cloned.text
                accent = text if isinstance(text, AccentLine) else None
                if accent:
                    text = accent.text
                prerecorded = getattr(text, 'pcm', None)   # Cached canned line (startup ready line).
                if prerecorded is not None:
                    text = text.text
                text = speech_text(text)
                if not text:
                    if performance:
                        # Its words were already streamed ahead as plain phrases (October 14,
                        # step 5): the gesture follows them on its own.
                        try:
                            self._gesture(performance, cancel, turn_id, delivery)
                        except ProviderFailure as exc:
                            self.trace.emit('gesture_failed', turn_id=turn_id, reason=str(exc))
                    continue
                self.trace.emit("speech_generated", turn_id=turn_id, text=text,
                                **({'voice': cloned.voice} if cloned else {}),
                                **({'voice_mode': accent.mode} if accent else {}))
                synthesis_at = self.trace.clock()
                first_pcm_at = None
                talking_started = False
                light_token = None
                light_attempted = False
                playing, talking_lock = False, threading.Lock()
                def start_talking():
                    nonlocal talking_started
                    if not talking_started:
                        talking_started = self._start_talking(turn_id, cancel)
                gesture_worker = None
                if performance and performance.timing == 'with_speech':
                    # October 11: finishing the gesture before asking for audio delayed
                    # first audio by about 2.6 s. The gesture now runs while the line is
                    # synthesized and played; talking motion takes over once it ends.
                    def gesture_alongside(performance=performance):
                        try:
                            self._gesture(performance, cancel, turn_id, delivery)
                        except Cancelled:
                            return
                        with talking_lock:
                            if playing and not cancel.event.is_set():
                                start_talking()
                    gesture_worker = threading.Thread(target=gesture_alongside, name='r3x-gesture', daemon=True)
                    gesture_worker.start()
                if self.leds: self.leds.clear_pcm()
                def chunks():
                    nonlocal first_pcm_at
                    stream = (iter((prerecorded,)) if prerecorded is not None else
                              cloned.take.chunks(cancel) if cloned else
                              self.tts.synthesize(text,cancel,mode=accent.mode) if accent else self.tts.synthesize(text,cancel))
                    try:
                        for chunk in stream:
                            cancel.check()  # Speech may have resumed during the TTS request.
                            if first_pcm_at is None:
                                first_pcm_at = self.trace.clock()
                                # Network generation is silent. Gate capture only
                                # once PCM can enter the playback queue.
                                self.active.set()
                                self.trace.emit("tts_first_pcm",turn_id=turn_id,tts_first_pcm_seconds=first_pcm_at-synthesis_at)
                            yield chunk
                    finally:
                        close = getattr(stream,"close",None)
                        if close:
                            close()
                def started(at):
                    nonlocal light_token, light_attempted, playing
                    delivery.first_audio_at = delivery.first_audio_at or at
                    delivery.status = "started"
                    self.trace.emit("playback_started", turn_id=turn_id, at_device=at,
                                    playback_queue_seconds=at-first_pcm_at if first_pcm_at is not None else None)
                    if self.leds:
                        light_attempted = True
                        try:
                            light_token = self._begin_lights(light,cancel)
                        except Exception as exc:
                            self._light_result(delivery,turn_id,light.request if light else None,'failed',str(exc) if isinstance(exc,ProviderFailure) else type(exc).__name__)
                    with talking_lock:
                        playing = True
                        start_talking()
                played_out = False   # The whole line played: talking motion winds down, not freezes.
                if self.bed is not None:
                    self.bed.stop()   # One stream at a time: the think music gives way to him.
                if sounds and not hushed:
                    sounds.hush()   # Owner, October 24: the whir goes quiet while he talks.
                    hushed = True
                line = [synthesis_at, None, plain_text(text)]
                self.lines.append(line)   # His own words, set aside when stop is listened for over them.
                try:
                    receipt = self.sink.play(chunks(), cancel, started)
                    played_out = not cancel.event.is_set()
                finally:
                    line[1] = self.trace.clock()
                    with talking_lock:
                        playing = False
                    if light_token is not None:
                        self._finish_lights(light_token,light,delivery,turn_id,
                            'cancelled' if cancel.event.is_set() else None)
                    elif light and not light_attempted:
                        self._light_result(delivery,turn_id,light.request,'cancelled','PlaybackNotStarted')
                    if talking_started:
                        self._stop_talking(turn_id, played_out)
                    if first_pcm_at is not None:
                        self.echo_until = self.trace.clock() + .3
                    self.active.clear()
                    if gesture_worker is not None:
                        gesture_worker.join()
                    if hushed and phrases.empty():
                        sounds.resume()
                        hushed = False
                delivery.completed_text.append(plain_text(text))
                delivery.completed_at = receipt.completed_at
                delivery.underruns += receipt.underruns
                self.trace.emit("phrase_completed", turn_id=turn_id, text=plain_text(text),
                                frames=receipt.frames, underruns=receipt.underruns,
                                completed_at=receipt.completed_at, **({'voice': cloned.voice} if cloned else {}))
                cancel.check()
                if performance and performance.timing == 'after_speech':
                    self._gesture(performance, cancel, turn_id, delivery)
                performance = None
            cancel.check()
            if delivery.status != "suppressed":
                delivery.status = "completed" if delivery.completed_text else "failed"
            if not delivery.completed_text and delivery.status!="suppressed":
                delivery.error = "EmptySpeech"
        except Cancelled:
            delivery.status = "interrupted"
        except Exception as exc:
            delivery.status = "failed"
            delivery.error = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
            cancel.event.set()  # Stop further model text on output failure.
        finally:
            if performance and not any(r['request_id']==performance.gesture.request_id for r in delivery.gestures):
                from dataclasses import asdict
                from .performance import GestureReceipt
                row = {**asdict(GestureReceipt(performance.gesture.request_id, 'cancelled', self.trace.clock(),
                        medium=getattr(self.gestures,'medium','simulation'), reason='SpeechNotCompleted')), 'primitive':performance.gesture.primitive,
                        'target':performance.gesture.target,'intensity':performance.gesture.intensity,
                        'timing':performance.timing}
                delivery.gestures.append(row)
                self.trace.emit('gesture_delivery',turn_id=turn_id,**row)
            self.active.clear()
            self._saying = None
            if hushed:
                sounds.resume()
            self.trace.emit("delivery", turn_id=turn_id, status=delivery.status,
                            medium=delivery.medium,
                            completed_text=" ".join(delivery.completed_text), error=delivery.error,
                            partial_phrase_text=None, underruns=delivery.underruns)


def put_phrase(queue: Queue, value: str | Performance | None, cancel: Cancellation) -> None:
    while True:
        cancel.check()
        try:
            queue.put(value, timeout=.05)
            return
        except Full:
            continue


class TextOutput:
    """Explicit silent replay output. Never creates an audio delivery receipt."""
    # No hardware adapters: the session reads these like OutputOwner's (October 10: the
    # hosted drive-planning check failed every turn on a missing 'attention').
    attention = gestures = inspection = drive = None

    def __init__(self,trace):
        self.trace = trace

    def cancel(self):
        pass

    def run(self,phrases,cancel,turn_id,delivery):
        if delivery.status != "suppressed":
            delivery.medium = "text"
        try:
            while True:
                cancel.check()
                try:
                    value = phrases.get(timeout=.05)
                except Empty:
                    continue
                if value is None:
                    break
                if isinstance(value, Sound):
                    self.trace.emit("sound_played", turn_id=turn_id, sound=value.name, completed=False, medium="text")
                    continue
                delivery.completed_text.append(plain_text(value))
                self.trace.emit("text_reply",turn_id=turn_id,text=plain_text(value),audio_played=False)
            if delivery.status != "suppressed":
                delivery.status = "completed" if delivery.completed_text else "failed"
        except Cancelled:
            delivery.status = "interrupted"
