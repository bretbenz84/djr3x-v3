"""The startup whir, the ready line, the power-down clip and Rex's saved canned lines.

Owner, November 20: V2's order (HEAD 777b38c) had been a startup whir while the servos wake, a
"please wait" line over the model loads, the long droid start-up sound as filler, then an "I'm
ready" line. The models now load during the servo wake, so there is nothing left to wait through:
the please-wait line and the filler are retired. A ready line is still said the moment he can
listen (with none, nothing told someone out of his view that he was up); the owner picked its
lines that day. It is chosen and fetched as startup begins, so it plays without a pause. The
spoken intro clips (Roger Control etc.) are copied but not played, as in V2.

Canned lines (the ready lines, the out-of-credit notices, sentry mode's and the depth sensor's)
are synthesized once in his voice and kept on disk, so they play at once and still play when the
voice service cannot be reached.

Shutdown (owner request, October 10): V2's power-down clip plays while the servos park,
started by the servo owner's park hook and finished before playback closes.

October 22 (owner): startup sound stuttered while models loaded. It goes through one
driver-buffered stream (AudioSink.buffered), opened as startup begins so its initial silence
plays out before the whir.
"""
import hashlib
import json
from pathlib import Path
import random
import threading
import wave

from .contracts import Cancellation, Cancelled
from .providers import Capability

ASSETS = Path(__file__).resolve().parents[2] / 'assets' / 'audio' / 'startup'


# The owner's picks, November 20: his own (the first is a certain holographic doctor's) and
# call-backs to the Star Tours ride he flew, "Brakes!" being his line in it. Short, because he
# cannot listen while he says one. He greets whoever he sees right after it.
READY_LINES = (
    'Please state the nature of the medical emergency.',
    'Rex online. Lower your expectations.',
    "Good news: nothing's on fire.",
    "Motivator's warm. Let's go.",
    'Back from the scrap heap.',
    'Ready. Mostly.',
    'Who woke the droid?',
    "Online. Don't make it weird.",
    'First flight for me too, folks.',
    'Next stop: Endor. Probably.',
    'Brakes! Brakes! Where are the brakes?',
    'No comets this time. Promise.',
    'Star Tours trained. Barely.',
    'This flight goes better. Promise.',
    'Light speed to Endor. Buckle up.',
)


class Prerecorded:
    """A line whose PCM already exists; the output owner plays it with the usual receipts."""
    def __init__(self, text: str, pcm: bytes):
        self.text, self.pcm = text, pcm


class LineCycle:
    """Random order without repeats until every line has played (V2 phrase cycler). The lines
    already said are kept by their words, so changing the list never marks a new line as said."""
    def __init__(self, path: Path, rng=None):
        self.path, self.rng = path, rng or random.Random()

    def pick(self, key: str, lines) -> str:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = {}
        if not isinstance(state, dict):
            state = {}
        said = [line for line in state.get(key, []) if line in lines] if isinstance(state.get(key), list) else []
        remaining = [line for line in lines if line not in said]
        if not remaining:
            said, remaining = [], list(lines)
        choice = self.rng.choice(remaining)
        state[key] = said + [choice]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state))
            temporary.replace(self.path)
        except OSError:
            pass  # Cycling is a nicety; a read-only disk must not block startup.
        return choice


def read_clip(name: str, sample_rate: int = 22050) -> bytes:
    with wave.open(str(ASSETS / name), 'rb') as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != sample_rate:
            raise ValueError('StartupClipFormat:' + name)
        return clip.readframes(clip.getnframes())


class StartupAudio:
    """Plays the whir over the servo wake on its own thread; finished before live output."""
    capability = Capability('startup_audio', 'bundled startup clips and cached canned TTS',
        'startup whir while the servos wake; the ready line; saved canned lines; power-down clip while the servos park',
        required=False, shutdown='let the whir and the power-down clip finish; join threads')

    def __init__(self, tts, sink, state_dir: Path, *, rng=None, trace=None, wake_motion=False):
        """wake_motion: he has servos, so the whir waits for their wake to begin (motion_started)."""
        self.tts, self.sink, self.trace, self.wake_motion = tts, sink, trace, wake_motion
        self._output = None   # The buffered startup stream, open while _run plays.
        self._motion_started = threading.Event()
        self.cycle = LineCycle(state_dir / 'startup-lines.json', rng)
        self._ready_line = None   # (text, fetching thread, its result) from start until it is said.
        self.cache = state_dir / 'startup-tts'
        self._cancel = Cancellation()
        self._thread = None
        self._shutdown_thread = None
        self._shutdown_cancel = Cancellation()   # Separate: stopping startup sound never cuts the power-down clip.
        self._warm_thread = None
        self._warm_stop = threading.Event()

    def _emit(self, **data):
        if self.trace:
            self.trace.emit('startup_audio', **data)

    def _cache_path(self, text: str) -> Path:
        model = getattr(getattr(self.tts, 'capability', None), 'model', None)
        key = hashlib.sha256(json.dumps([model, getattr(self.tts, 'voice_id', None), text]).encode()).hexdigest()
        return self.cache / (key + '.pcm')

    def cached(self, text: str) -> bytes | None:
        try:
            data = self._cache_path(text).read_bytes()
        except OSError:
            return None
        return data if data and not len(data) % 2 else None

    def speech(self, text: str, cancel: Cancellation) -> bytes:
        """Canned line PCM, synthesized (and leveled) once, then read from the cache."""
        data = self.cached(text)
        if data is not None:
            return data
        path = self._cache_path(text)
        # Cached under his own voice, so made in it even while a speaking mode is on (October 25).
        own = getattr(self.tts, 'synthesize_own', None)
        data = b''.join(own(text, cancel) if own else self.tts.synthesize(text, cancel))
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            pass
        return data

    def _play(self, pcm: bytes, label: str, cancel: Cancellation | None = None, visualize: bool = True):
        """Feed ~0.1 s pieces like streamed speech (a whole clip at once exceeded the
        sink's drain bound and forced an abort). During startup the pieces go to the
        buffered stream; the power-down clip uses ordinary playback."""
        cancel = cancel or self._cancel
        cancel.check()
        pieces = (pcm[i:i+4410] for i in range(0, len(pcm), 4410))
        output = self._output if cancel is self._cancel and self._output is not None else self.sink
        playback = output.play(pieces, cancel, lambda at: None, visualize=visualize)
        self._emit(status='played', clip=label, underruns=getattr(playback, 'underruns', None))

    def motion_started(self):
        """Servo wake choreography is beginning: play the whir with it (owner request)."""
        self._motion_started.set()

    def _wait(self, ready, timeout):
        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.daemon = True
        timer.start()
        try:
            while not ready() and not deadline.is_set():
                self._cancel.check()
                self._cancel.event.wait(.05)
        finally:
            timer.cancel()

    def _run(self):
        try:
            try:
                self._output = self.sink.buffered()
                self._emit(status='buffered_output', ahead_seconds=round(self._output.latency, 2))
            except Exception as exc:
                self._emit(status='buffered_output_failed', error_type=type(exc).__name__)
            if self.wake_motion:
                # Start with the servo wake-up, not before it (LED startup comes first).
                self._wait(self._motion_started.is_set, 15.)
            self._play(read_clip('startup_whir.wav'), 'startup_whir')
        except Cancelled:
            self._emit(status='stopped')
        except Exception as exc:
            self._emit(status='failed', error_type=type(exc).__name__, reason=str(exc)[:120])
        finally:
            output, self._output = self._output, None
            if output is not None:
                try:
                    output.close()   # Drained already; live speech gets the device alone.
                except Exception as exc:
                    self._emit(status='buffered_output_close_failed', error_type=type(exc).__name__)

    def _fetch_ready_line(self):
        """Choose this launch's ready line and get its sound while everything else starts: read from
        disk, or made by the voice service the first time a line comes up."""
        line, result = self.cycle.pick('ready', READY_LINES), {}
        def run():
            try:
                result['pcm'] = self.speech(line, self._cancel)
            except Exception as exc:
                result['error'] = exc
        thread = threading.Thread(target=run, name='r3x-ready-line', daemon=True)
        self._ready_line = (line, thread, result)
        thread.start()

    def ready(self) -> Prerecorded:
        """The line he says once everything has loaded. Raises if its sound could not be had."""
        if self._ready_line is None:
            self._fetch_ready_line()
        line, thread, result = self._ready_line
        thread.join(8.)   # In hand long before this unless the voice service is slow.
        if 'pcm' not in result:
            raise result.get('error') or RuntimeError('ReadyLineNotReady')
        return Prerecorded(line, result['pcm'])

    def start(self):
        self._fetch_ready_line()
        self._thread = threading.Thread(target=self._run, name='r3x-startup-audio', daemon=True)
        self._thread.start()

    def stop(self):
        """Wait out the whir (about five seconds, over well before startup is) so live speech has
        the speaker alone; only a stuck device is cut."""
        if self._thread is not None:
            self._thread.join(12.)
        if self._thread is not None and self._thread.is_alive():
            self._cancel.event.set()    # Last resort only (a stuck device).
            self._thread.join(5.)
        return self._thread is None or not self._thread.is_alive()

    def warm(self, lines, busy=None, interval: float = 3.):
        """Synthesize every one of these canned lines that is not cached yet, one at a time in
        the background after startup, pausing while Rex speaks. Once done, every later launch
        reads them from disk. Returns the thread, or None when nothing is missing."""
        missing = [line for line in dict.fromkeys(lines) if self.cached(line) is None]
        if not missing:
            return None
        def run():
            done = 0
            for line in missing:
                while busy is not None and busy():
                    if self._warm_stop.wait(.2):
                        break
                if self._warm_stop.is_set() or self._cancel.event.is_set():
                    break
                try:
                    self.speech(line, self._cancel)
                    done += 1
                except Cancelled:
                    break
                except Exception as exc:
                    self._emit(status='warm_failed', error_type=type(exc).__name__, text=line)
                    break   # A failing provider is not retried line after line.
                if self._warm_stop.wait(interval):
                    break
            self._emit(status='warmed', synthesized=done, missing=len(missing) - done)
        self._warm_thread = threading.Thread(target=run, name='r3x-startup-line-cache', daemon=True)
        self._warm_thread.start()
        return self._warm_thread

    def shutdown_started(self):
        """The servos are parking: play the power-down clip with them (owner request).
        Startup playback is stopped first, so only one clip uses the sink; the park
        never waits for the clip, and close() lets it finish before playback closes."""
        self.stop()
        if self._shutdown_thread is not None:
            return
        def run():
            try:
                self._play(read_clip('robot_power_down.wav'), 'robot_power_down',
                           cancel=self._shutdown_cancel, visualize=False)
            except Cancelled:
                self._emit(status='stopped', clip='robot_power_down')
            except Exception as exc:
                self._emit(status='failed', clip='robot_power_down', error_type=type(exc).__name__, reason=str(exc)[:120])
        self._shutdown_thread = threading.Thread(target=run, name='r3x-shutdown-audio', daemon=True)
        self._shutdown_thread.start()

    def cancel(self):
        self._warm_stop.set()
        self._cancel.event.set()

    def close(self):
        self._warm_stop.set()
        if self._warm_thread is not None:
            self._warm_thread.join(3.)
        self.stop()
        thread = self._shutdown_thread
        if thread is not None:
            thread.join(6.)   # The power-down clip is about two seconds; the sink closes after this.
            if thread.is_alive():
                self._shutdown_cancel.event.set()   # Last resort only (a stuck device).
                thread.join(2.)
