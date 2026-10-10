"""Startup sounds and canned lines, ported from V2 main.py/config.py.

Order from V2 (HEAD 777b38c): a startup whir while the servos wake, a cycled "please wait" line
spoken over the model loads, the long droid start-up sound as filler, then a cycled
"I'm ready" line once everything is loaded. Lines cycle without repeats across
launches; the owner had the lines shortened. Canned lines are cached after their first synthesis so later launches
start instantly and cost nothing; a line's first synthesis runs while the whir and servo wake play, so the
line follows them without a pause (owner, October 10). The spoken intro clips (Roger Control etc.) are
copied but not played: V2 ships with PLAY_STARTUP_SPEECH_CLIP disabled.

Shutdown (owner request, October 10): V2's power-down clip plays while the servos park,
started by the servo owner's park hook and finished before playback closes.
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

# Owner rule carried from V2 (2026-09-03): ready lines end as a statement, never an
# invitation to speak, because Rex greets whoever is present unprompted next.
# Owner request, September 27: short lines (the long V2 ones ran past readiness).
BOOT_LINES = (
    'Still booting. Hang tight.',
    'Warming up my circuits.',
    'Not ready yet, folks.',
    'Loading. Please hold.',
    'Pre-flight checks. Almost there.',
    "Booting up. Don't talk yet.",
    'Hold on, still waking up.',
    'Give the old motivator a second.',
    'Still loading my personality.',
    'Standby. Captain Rex is loading.',
    'Calibrating photoreceptors.',
    'Booting. First flight for me too.',
    'Spooling the hyperdrive. Hold please.',
    'Memory banks loading.',
    'Almost awake. Not quite.',
    'Loading. Still loading.',
    'Running diagnostics. Stand by.',
    'Untangling my wiring. One moment.',
    'Still booting. Buckle up.',
    'One moment, folks. Warming up.',
)

READY_LINES = (
    "OK, I'm ready.",
    'Systems nominal.',
    'All systems go.',
    'Boot complete.',
    "I'm awake. Unfortunately.",
    'Online and listening.',
    'Ready. Expectations lowered.',
    'Fully operational.',
    "I'm up. Sarcasm calibrated.",
    'Boot successful. Barely.',
    'Ready for departure.',
    'Awake, and only mildly resentful.',
    'Online. No sparks, no smoke.',
    'All warmed up.',
    "Ready. That's a personal record.",
    "Systems green. I'm surprised too.",
    'Captain Rex, online.',
    'Operational. Mostly.',
)


class Prerecorded:
    """A line whose PCM already exists; the output owner plays it with the usual receipts."""
    def __init__(self, text: str, pcm: bytes):
        self.text, self.pcm = text, pcm


class LineCycle:
    """Random order without repeats until every line has played (V2 phrase cycler)."""
    def __init__(self, path: Path, rng=None):
        self.path, self.rng = path, rng or random.Random()

    def pick(self, key: str, lines) -> str:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = {}
        used = [i for i in state.get(key, []) if isinstance(i, int) and 0 <= i < len(lines)]
        remaining = [i for i in range(len(lines)) if i not in used] or list(range(len(lines)))
        if len(remaining) == len(lines):
            used = []
        choice = self.rng.choice(remaining)
        state[key] = used + [choice]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state))
            temporary.replace(self.path)
        except OSError:
            pass  # Cycling is a nicety; a read-only disk must not block startup.
        return lines[choice]


def read_clip(name: str, sample_rate: int = 22050) -> bytes:
    with wave.open(str(ASSETS / name), 'rb') as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != sample_rate:
            raise ValueError('StartupClipFormat:' + name)
        return clip.readframes(clip.getnframes())


class StartupAudio:
    """Plays over provider startup on its own thread; stopped before live output."""
    capability = Capability('startup_audio', 'bundled startup clips and cached canned TTS',
        'startup whir, please-wait line and filler while models load; ready line after; power-down clip while the servos park',
        required=False, shutdown='stop startup playback; let the power-down clip finish; join threads')

    def __init__(self, tts, sink, state_dir: Path, *, rng=None, trace=None, robot=None):
        self.tts, self.sink, self.trace, self.robot = tts, sink, trace, robot
        self._motion_started = threading.Event()
        self.cycle = LineCycle(state_dir / 'startup-lines.json', rng)
        self.cache = state_dir / 'startup-tts'
        self._cancel = Cancellation()
        self._thread = None
        self._line = threading.Event()   # Set while the please-wait line is audible.
        self._ready = threading.Event()  # Startup finished: no filler starts after this.
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
        data = b''.join(self.tts.synthesize(text, cancel))
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            pass
        return data

    def _play(self, pcm: bytes, label: str, gentle: bool = False, cancel: Cancellation | None = None,
              visualize: bool = True):
        """Feed ~0.1 s pieces like streamed speech (a whole clip at once exceeded the
        sink's drain bound and forced an abort). A gentle clip stops by starving the
        stream at readiness, so it drains instead of being force-stopped."""
        cancel = cancel or self._cancel
        cancel.check()
        def pieces():
            for i in range(0, len(pcm), 4410):
                if gentle and self._ready.is_set():
                    return
                yield pcm[i:i+4410]
        self.sink.play(pieces(), cancel, lambda at: None, visualize=visualize)
        self._emit(status='played', clip=label)

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

    def _fetch(self, line):
        """Synthesize (or read from cache) on a helper thread while the whir and the servo
        wake play out, so the line follows them without a pause (V2 pre-cached it too)."""
        result = {}
        def run():
            try:
                result['pcm'] = self.speech(line, self._cancel)
            except Exception as exc:
                result['error'] = exc
        thread = threading.Thread(target=run, name='r3x-startup-line', daemon=True)
        thread.start()
        def ready():
            self._wait(lambda: not thread.is_alive(), 30.)
            if 'error' in result:
                raise result['error']
            if 'pcm' not in result:
                raise RuntimeError('StartupLineNotReady')
            return result['pcm']
        return ready

    def _speak(self, line, fetched):
        """The please-wait line with the ordinary talking motion, once the servos are awake."""
        talking = None
        if self.robot is not None:
            self._wait(lambda: self.robot.owner.state == 'active', 25.)
        pcm = fetched()
        if self.robot is not None:
            if self.robot.owner.state == 'active':
                talking = self.robot.talking
                try:
                    talking.start('startup-boot-line', self._cancel)
                except Exception as exc:
                    talking = None
                    self._emit(status='talking_motion_failed', error_type=type(exc).__name__)
        self._line.set()
        try:
            self._play(pcm, 'boot_line')
        finally:
            self._line.clear()
            if talking is not None:
                try:
                    talking.stop()
                except Exception as exc:
                    self._emit(status='talking_motion_failed', error_type=type(exc).__name__)

    def _run(self):
        try:
            line = self.cycle.pick('boot', BOOT_LINES)
            fetched = self._fetch(line)   # Overlaps the LED boot, the whir and the servo wake.
            if self.robot is not None:
                # Start with the servo wake-up, not before it (LED startup comes first).
                self._wait(self._motion_started.is_set, 15.)
            self._play(read_clip('startup_whir.wav'), 'startup_whir')
            self._speak(line, fetched)
            self._emit(status='spoken', text=line)
            if not self._ready.is_set():
                self._play(read_clip('droid_startup.wav'), 'droid_startup', gentle=True)
        except Cancelled:
            self._emit(status='stopped')
        except Exception as exc:
            self._emit(status='failed', error_type=type(exc).__name__, reason=str(exc)[:120])

    def start(self):
        self._thread = threading.Thread(target=self._run, name='r3x-startup-audio', daemon=True)
        self._thread.start()

    def stop(self):
        """Starve the filler so it drains; let an audible line finish (owner rule)."""
        self._ready.set()   # The filler stops being fed and drains on its own.
        if self._thread is not None:
            self._thread.join(12.)   # An audible line finishes; the filler tail is ~1.5 s.
        if self._thread is not None and self._thread.is_alive():
            self._cancel.event.set()    # Last resort only (a stuck device).
            interrupt = getattr(self.tts, 'interrupt', None)
            if interrupt:
                interrupt()
            self._thread.join(5.)
        return self._thread is None or not self._thread.is_alive()

    def warm(self, busy=None, interval: float = 3.):
        """Synthesize every boot and ready line that is not cached yet, one at a time in
        the background after startup, pausing while Rex speaks (owner, October 10: the
        cycle kept reaching uncached lines, so most launches paid a hosted round trip).
        Once done, every later launch reads its lines from disk. Returns the thread."""
        missing = [line for line in BOOT_LINES + READY_LINES if self.cached(line) is None]
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

    def ready_line(self) -> str:
        return self.cycle.pick('ready', READY_LINES)

    def ready(self, cancel: Cancellation) -> Prerecorded:
        line = self.ready_line()
        return Prerecorded(line, self.speech(line, cancel))

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
