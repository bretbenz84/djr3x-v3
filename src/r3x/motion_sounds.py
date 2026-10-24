"""Wheel-motion sound effects, ported from V2 intelligence/motion_controller.py (HEAD 77b2008).

While the wheels move, the drive or turn whir loops and stops the moment the base
reports done (V2 owner, 2026-07-24: a one-shot 4 s whir fell silent mid-move). A stop
blocked by an obstacle plays the slow-down accent; a finished approach plays the arrival
chirp. Commanded motion plays at 50% (owner, September 27); self-initiated motion at a
quarter of that (V2 owner, 2026-08-19). The effects never mark echo risk, so the
microphone keeps listening: V2's own motor sounds once deafened "stop" (2026-07-25).
They play through the one speech sink, so no second output stream opens on the device
the microphone shares. Since October 24 he may talk while the wheels run: the whir then
pauses for each line and comes back after it (owner: whir quiet while he talks).
"""
from array import array
from pathlib import Path
import threading
import wave

from .contracts import Cancellation, Cancelled, ProviderFailure

ASSETS = Path(__file__).resolve().parents[2] / 'assets' / 'audio' / 'motion'
CLIPS = {'move': 'motion_whir.wav', 'turn': 'motion_turning.wav',
         'blocked': 'slow_down.wav', 'arrived': 'arrived.wav'}
GAIN = {'requested': .5, 'self_initiated': .125}   # Owner September 27: 50%; self-initiated a quarter of that.
LOOP_CAP_SECONDS = 30.   # A lost completion must never leave the whir droning.
ACCENT_SECONDS = 2.      # The 4 s accents fade here so the spoken result follows quickly.
PIECE = 1024             # ~23 ms of PCM: a stopped loop drains in well under half a second.
RATE = 22050


def read_motion_clip(name: str) -> bytes:
    with wave.open(str(ASSETS / name), 'rb') as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != RATE:
            raise ValueError('MotionClipFormat:' + name)
        return clip.readframes(clip.getnframes())


def shaped(pcm: bytes, gain: float, seconds: float | None = None, fade: float = .3) -> bytes:
    """Scale 16-bit PCM; optionally cut to `seconds` with a linear fade-out."""
    samples = array('h', pcm)
    if seconds is not None:
        samples = samples[:int(seconds * RATE)]
    ramp = int(fade * RATE) if seconds is not None else 0
    total = len(samples)
    for i in range(total):
        factor = gain * (min(1., (total - i) / ramp) if ramp else 1.)
        samples[i] = max(-32768, min(32767, int(samples[i] * factor)))
    return samples.tobytes()


class MotionSounds:
    """One effect at a time on the speech sink; the drive owner starts and stops it."""
    def __init__(self, sink, trace=None):
        self.sink, self.trace = sink, trace
        self._clips = {}
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._cancel = Cancellation()
        self._wanted = None      # (kind, origin) of the loop the drive owner asked for, or None.
        self._hushed = False     # A line is playing on the sink: no effect starts.

    def _pcm(self, kind, origin, accent):
        key = (kind, origin)
        if key not in self._clips:
            self._clips[key] = shaped(read_motion_clip(CLIPS[kind]), GAIN.get(origin, GAIN['self_initiated']),
                                      ACCENT_SECONDS if accent else None)
        return self._clips[key]

    def _run(self, kind, pcm, loop, stop, cancel):
        def pieces():
            played = 0.
            while not stop.is_set() and played < LOOP_CAP_SECONDS:
                for i in range(0, len(pcm), PIECE):
                    if stop.is_set():
                        return
                    yield pcm[i:i+PIECE]
                played += len(pcm) / (2 * RATE)
                if not loop:
                    return
        try:
            self.sink.play(pieces(), cancel, lambda at: None, visualize=False)
        except Cancelled:
            pass
        except Exception as exc:   # An effect never fails the drive.
            if self.trace:
                self.trace.emit('motion_sound_failed', sound=kind, error_type=type(exc).__name__)

    def _begin(self, kind, origin, loop):
        self._halt()
        try:
            pcm = self._pcm(kind, origin, accent=not loop)
        except (OSError, ValueError) as exc:
            if self.trace:
                self.trace.emit('motion_sound_failed', sound=kind, error_type=type(exc).__name__)
            return None
        with self._lock:
            if self._hushed or (loop and self._wanted != (kind, origin)):
                # He is talking: the sink is his voice's (a loop comes back with resume).
                # Or the wheels stopped while a resume was on its way.
                return None
            self._stop, self._cancel = threading.Event(), Cancellation()
            self._thread = threading.Thread(target=self._run, args=(kind, pcm, loop, self._stop, self._cancel),
                                            name='r3x-motion-sound', daemon=True)
            self._thread.start()
            return self._thread

    def start(self, kind: str, origin: str):
        """Loop the turn or drive whir until stop()."""
        with self._lock:
            self._wanted = (kind, origin)
        self._begin(kind, origin, loop=True)

    def accent(self, kind: str, origin: str):
        """Play the arrival or blocked accent to its short end (stop() cuts it); none while he talks."""
        thread = self._begin(kind, origin, loop=False)
        if thread is not None:
            thread.join(ACCENT_SECONDS + 1.5)

    def stop(self, now: bool = False):
        """Starve the loop so it drains; `now` (a stop command, shutdown) cuts it."""
        with self._lock:
            self._wanted = None
        self._halt(now)

    def hush(self):
        """A line is about to play on the one sink: the effect stops at once and the
        loop the drive asked for waits for resume. Returns once the sink is free."""
        with self._lock:
            self._hushed = True
        self._halt(now=True)

    def resume(self):
        """The line is over: the whir comes back if the wheels still want it."""
        with self._lock:
            self._hushed, wanted = False, self._wanted
        if wanted:
            self._begin(*wanted, loop=True)

    def _halt(self, now: bool = False):
        with self._lock:
            thread, stop, cancel = self._thread, self._stop, self._cancel
            self._thread = None
        if thread is None:
            return
        stop.set()
        if now:
            cancel.event.set()
        thread.join(1.)
        if thread.is_alive():
            cancel.event.set()
            thread.join(1.)
