"""The startup sound, the ready line, the power-down clip and Rex's saved canned lines.

Owner, November 20: V2's order (HEAD 777b38c) had been a startup whir while the servos wake, a
"please wait" line over the model loads, the long droid start-up sound as filler, then an "I'm
ready" line. The models now load during the servo wake, so there is nothing left to wait through:
the please-wait line and the filler are retired. A ready line is still said the moment he can
listen (with none, nothing told someone out of his view that he was up); the owner picked its
lines that day. It is chosen and fetched as startup begins, so it plays without a pause. The
spoken intro clips (Roger Control etc.) are copied but not played, as in V2.

November 21 (owner): the startup sound is the owner's own clip (startup_sequence.wav) in place of
V2's whir: a build of about twelve seconds, a pause, then a short finishing sound. The clip is cut
at its pause. The build plays while he starts; the finish is held until he is ready and plays
as the visor goes up, whenever that turns out to be. A clip with no pause is all build.

The mouth lights follow the build (owner, the same day): dark for its first second, a light or
two twinkling with its loudness until it comes back up, then lit from the centre outwards. This
module measures how loud the build is over time, as it is heard, and tells the LED owner when it
first sounds (on_build_heard).

Canned lines (the ready lines, the out-of-credit notices, sentry mode's and the depth sensor's)
are synthesized once in his voice and kept on disk, so they play at once and still play when the
voice service cannot be reached.

Shutdown (owner request, October 10): V2's power-down clip plays while the servos park,
started by the servo owner's park hook and finished before playback closes.

October 22 (owner): startup sound stuttered while models loaded. It goes through one
driver-buffered stream (AudioSink.buffered), opened as startup begins so its initial silence
plays out before the sound.
"""
from array import array
import hashlib
import math
import json
from pathlib import Path
import random
import sys
import threading
import time
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


STARTUP_CLIP = 'startup_sequence.wav'   # The owner's startup sound (November 21): build, pause, finish.
# The mouth lights and this clip, by the owner's ear (November 21): "It should start 2 second after the
# sound clip starts", then a light or two twinkling; the sound "starts going back up around 6.5
# seconds", and from there the mouth is lit from its centre outwards. Seconds into the build.
# After seeing two seconds in a live start: "a little sooner, maybe by a second".
MOUTH_WAIT_SECONDS = 1.
MOUTH_GROW_SECONDS = 6.5
LOUDNESS_STEP = .1        # Seconds of sound for each loudness reading.
LOUDNESS_RANGE_DB = 24.   # A reading of 0 is this far under the build's loudest moment; 255 is that moment.
QUIET_RMS = 104       # Of 32768: about -50 dBFS. Below this for a whole window is silence.
PAUSE_WINDOW = .05    # Seconds of sound judged at a time.
MIN_PAUSE = .4        # A quiet stretch this long inside the clip is its pause.
CUT_FADE = .03        # Each cut end fades out over this long, so a cut never clicks.


def split_at_pause(pcm: bytes, sample_rate: int = 22050) -> tuple[bytes, bytes]:
    """(build, finish) of a startup sound: its silent ends dropped, then cut at its longest pause.
    The build is what comes before the pause and the finish what comes after; a clip with no
    pause of MIN_PAUSE is all build. 16-bit mono PCM in and out."""
    samples = array('h', pcm[:len(pcm) // 2 * 2])
    if sys.byteorder != 'little':
        samples.byteswap()
    step = max(1, int(sample_rate * PAUSE_WINDOW))
    floor = QUIET_RMS * QUIET_RMS
    loud = []
    for start in range(0, len(samples), step):
        window = samples[start:start + step]
        loud.append(sum(v * v for v in window) >= floor * len(window))
    if True not in loud:
        return b'', b''
    first, last = loud.index(True), len(loud) - 1 - loud[::-1].index(True)
    longest, begins, run = 0, None, None
    for index in range(first, last + 1):
        if not loud[index]:
            run = index if run is None else run
        elif run is not None:
            if index - run > longest:
                longest, begins = index - run, run
            run = None

    def cut(start, end):
        """Windows start to end, with one more to let the sound die away, faded at its end."""
        part = samples[start * step:min(len(samples), (end + 1) * step)]
        fade = min(len(part), int(sample_rate * CUT_FADE))
        for offset in range(fade):
            part[len(part) - fade + offset] = part[len(part) - fade + offset] * (fade - offset) // fade
        if sys.byteorder != 'little':
            part.byteswap()
        return part.tobytes()

    if begins is None or longest * step < MIN_PAUSE * sample_rate:
        return cut(first, last + 1), b''
    return cut(first, begins), cut(begins + longest, last + 1)


def loudness(pcm: bytes, sample_rate: int = 22050) -> list[int]:
    """How loud a sound is as it is heard, one reading (0-255) for every LOUDNESS_STEP of it. The ear
    takes little notice of low rumble, so the lows are turned down first (a gentle high-pass at 1 kHz;
    on the owner's clip its rise and fall track the standard A-weighted level, correlation 0.98, where
    the plain level does not: 0.74). Readings are decibels below the loudest one, over LOUDNESS_RANGE_DB.
    16-bit mono PCM."""
    samples = array('h', pcm[:len(pcm) // 2 * 2])
    if sys.byteorder != 'little':
        samples.byteswap()
    keep = 1 / (1 + 2 * math.pi * 1000 / sample_rate)
    per = max(1, int(sample_rate * LOUDNESS_STEP))
    powers, total, count, before, passed = [], 0., 0, 0., 0.
    for sample in samples:
        passed = keep * (passed + sample - before)
        before = sample
        total += passed * passed
        count += 1
        if count == per:
            powers.append(total / count)
            total, count = 0., 0
    if count > per // 2:
        powers.append(total / count)
    top = max(powers, default=0.)
    if top <= 0:
        return [0] * len(powers)
    return [max(0, min(255, round((10 * math.log10(max(p, 1e-12) / top) + LOUDNESS_RANGE_DB) / LOUDNESS_RANGE_DB * 255)))
            for p in powers]


def read_clip(name: str, sample_rate: int = 22050) -> bytes:
    with wave.open(str(ASSETS / name), 'rb') as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != sample_rate:
            raise ValueError('StartupClipFormat:' + name)
        return clip.readframes(clip.getnframes())


class StartupAudio:
    """Plays the startup sound on its own thread: its build over the servo wake and model loading,
    its finish when he is ready (land); done before live output."""
    capability = Capability('startup_audio', 'bundled startup clips and cached canned TTS',
        "the startup sound's build while he starts and its finish when he is ready; the ready line; "
        'saved canned lines; power-down clip while the servos park',
        required=False, shutdown='end the startup sound; let the power-down clip finish; join threads')

    def __init__(self, tts, sink, state_dir: Path, *, rng=None, trace=None, wake_motion=False):
        """wake_motion: he has servos, so the sound waits for their wake to begin (motion_started)."""
        self.tts, self.sink, self.trace, self.wake_motion = tts, sink, trace, wake_motion
        self._output = None   # The buffered startup stream, open while _run plays.
        self._motion_started = threading.Event()
        self._landing = threading.Event()    # He is ready: the build ends and the finish plays.
        self._quit = threading.Event()       # Startup sound is over now: no more build, no finish.
        self._finish_heard = threading.Event()   # The finish has begun to sound (or there is none to play).
        # (heard_at, level_at, wait, grow, end) -> None, called once as the build is first due at the
        # speaker: when (the sink's clock), a function giving its loudness (0-255, or None before it
        # begins) so many seconds into it, and three moments in it, in seconds: the mouth's wait,
        # where it starts to grow, and the build's end. The LED owner's (LEDs.startup_sound).
        self.on_build_heard = None
        self._loudness = None                    # The build's loudness readings, once measured.
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

    def _play(self, pcm: bytes, label: str, cancel: Cancellation | None = None, visualize: bool = True,
              until=None, heard=None):
        """Feed ~0.1 s pieces like streamed speech (a whole clip at once exceeded the
        sink's drain bound and forced an abort). During startup the pieces go to the
        buffered stream; the power-down clip uses ordinary playback. until: once it answers
        true no more is fed, and what is already queued plays out. heard: called, with the time
        it is due at the speaker, as the clip's first sound is written."""
        cancel = cancel or self._cancel
        cancel.check()
        def pieces():
            for i in range(0, len(pcm), 4410):
                if until is not None and until():
                    return
                yield pcm[i:i+4410]
        output = self._output if cancel is self._cancel and self._output is not None else self.sink
        playback = output.play(pieces(), cancel, heard or (lambda at: None), visualize=visualize)
        self._emit(status='played', clip=label, underruns=getattr(playback, 'underruns', None))

    def motion_started(self):
        """Servo wake choreography is beginning: start the startup sound with it (owner request)."""
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
            build, finish = split_at_pause(read_clip(STARTUP_CLIP))
            self._loudness = loudness(build)   # About 20 ms; the mouth lights follow it.
            if self.wake_motion:
                # Start with the servo wake-up, not before it (LED startup comes first).
                self._wait(lambda: self._motion_started.is_set() or self._quit.is_set(), 15.)
            ending = lambda: self._landing.is_set() or self._quit.is_set()
            if not ending():
                # Ready before the build is over: no more of it is fed, and the second or so
                # already queued plays out. Still starting when it is over: quiet until he is ready.
                self._play(build, 'startup_build', until=ending, heard=lambda at: self._build_heard(at, len(build) / 44100))
            self._wait(ending, 120.)
            if finish and self._landing.is_set() and not self._quit.is_set():
                self._play(finish, 'startup_finish', heard=lambda at: self._finish_heard.set())
        except Cancelled:
            self._emit(status='stopped')
        except Exception as exc:
            self._emit(status='failed', error_type=type(exc).__name__, reason=str(exc)[:120])
        finally:
            self._finish_heard.set()   # Nobody waits on a finish that will not come.
            output, self._output = self._output, None
            if output is not None:
                try:
                    output.close()   # Drained already; live speech gets the device alone.
                except Exception as exc:
                    self._emit(status='buffered_output_close_failed', error_type=type(exc).__name__)

    def _build_heard(self, at, seconds):
        """The build's first sound is due at the speaker at `at`: tell whoever follows it (the lights)."""
        def level_at(into):
            readings = self._loudness
            if readings is None or into < 0:
                return None
            index = int(into / LOUDNESS_STEP)
            return readings[index] if index < len(readings) else 0
        self._emit(status='build_heard', seconds=round(seconds, 2))
        if self.on_build_heard is not None:
            try:
                self.on_build_heard(at, level_at, MOUTH_WAIT_SECONDS, MOUTH_GROW_SECONDS, seconds)
            except Exception as exc:
                self._emit(status='build_heard_hook_failed', error_type=type(exc).__name__)

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

    def land(self, timeout: float = 4.):
        """He is ready: the build ends (what is queued, at most about a second and a half, plays
        out) and the finish follows. Returns as the finish starts to sound, so the lights can lock
        and the visor can go up with it; stop() then waits for it to end."""
        began = time.monotonic()
        self._landing.set()
        if self._thread is not None:
            self._finish_heard.wait(timeout)
        self._emit(status='landed', waited_seconds=round(time.monotonic() - began, 2))

    def stop(self):
        """The startup sound is over: a finish that has begun plays to its end; otherwise no more of
        the build is fed and what is queued plays out. Live speech then has the speaker alone. Only
        a stuck device is cut."""
        if not self._landing.is_set():
            self._quit.set()
        if self._thread is not None:
            self._thread.join(8.)
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
