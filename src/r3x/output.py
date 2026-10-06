"""The sole speech owner. Generated text is never a delivery receipt."""
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

    def execute_inspection(self, request, cancel, valid):
        if self.inspection is None:
            raise ProviderFailure('InspectionUnavailable')
        if not self._motion_lock.acquire(blocking=False):
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

    def _drive(self, action, cancel, turn_id):
        from .drive import DriveReceipt
        try:
            if self.drive is None:
                action.receipt = DriveReceipt(action.plan.request_id, 'failed', 'DriveUnavailable')
            elif not self._motion_lock.acquire(timeout=5.):
                action.receipt = DriveReceipt(action.plan.request_id, 'refused', 'MotionResourceBusy')
            else:
                try:
                    action.receipt = self.drive.execute(action.plan, cancel)
                finally:
                    self._motion_lock.release()
        finally:
            action.done.set()
        cancel.check()

    def run(self, phrases: Queue, cancel: Cancellation, turn_id: str, delivery: Delivery) -> None:
        performance = None
        light = None
        try:
            while True:
                cancel.check()
                try:
                    text = phrases.get(timeout=.05)
                except Empty:
                    continue
                if text is None:
                    break
                from .drive import DriveAction
                if isinstance(text, DriveAction):
                    self._drive(text, cancel, turn_id)
                    continue
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
                    if performance.timing == 'before_speech':
                        self._gesture(performance, cancel, turn_id, delivery)
                cloned = text if isinstance(text, ClonedPhrase) else None
                if cloned:
                    text = cloned.text
                prerecorded = getattr(text, 'pcm', None)   # Cached canned line (startup ready line).
                if prerecorded is not None:
                    text = text.text
                text = speech_text(text)
                if not text:
                    continue
                self.trace.emit("speech_generated", turn_id=turn_id, text=text,
                                **({'voice': cloned.voice} if cloned else {}))
                synthesis_at = self.trace.clock()
                first_pcm_at = None
                talking_started = False
                light_token = None
                light_attempted = False
                if self.leds: self.leds.clear_pcm()
                def chunks():
                    nonlocal first_pcm_at
                    stream = (iter((prerecorded,)) if prerecorded is not None else
                              cloned.take.chunks(cancel) if cloned else self.tts.synthesize(text,cancel))
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
                    nonlocal talking_started, light_token, light_attempted
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
                    if self.speech_motion and self._motion_lock.acquire(blocking=False):
                        try:
                            cancel.check()
                            self.speech_motion.start(turn_id, cancel)
                            talking_started = True
                        except Exception as exc:
                            self._motion_lock.release()
                            self.trace.emit('talking_motion', turn_id=turn_id, status='failed',
                                            reason=type(exc).__name__)
                try:
                    receipt = self.sink.play(chunks(), cancel, started)
                finally:
                    if light_token is not None:
                        self._finish_lights(light_token,light,delivery,turn_id,
                            'cancelled' if cancel.event.is_set() else None)
                    elif light and not light_attempted:
                        self._light_result(delivery,turn_id,light.request,'cancelled','PlaybackNotStarted')
                    if talking_started:
                        try:
                            motion_receipt = self.speech_motion.stop()
                            if motion_receipt:
                                from dataclasses import asdict
                                self.trace.emit('talking_motion', turn_id=turn_id, **asdict(motion_receipt))
                        except Exception as exc:
                            # Keep the resource reserved if the worker did not stop.
                            self.trace.emit('talking_motion', turn_id=turn_id, status='failed',
                                            reason=type(exc).__name__)
                        else:
                            self._motion_lock.release()
                    if first_pcm_at is not None:
                        self.echo_until = self.trace.clock() + .3
                    self.active.clear()
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
                delivery.completed_text.append(plain_text(value))
                self.trace.emit("text_reply",turn_id=turn_id,text=plain_text(value),audio_played=False)
            if delivery.status != "suppressed":
                delivery.status = "completed" if delivery.completed_text else "failed"
        except Cancelled:
            delivery.status = "interrupted"
