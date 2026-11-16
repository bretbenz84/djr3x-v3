"""A live voice played as it arrives: one output stream for the whole call behind a small jitter buffer.

The robot plays the owner's voice with it, and the test client plays the room with it. Playback
starts once START_SECONDS are buffered and starts again from silence after running dry, so network
jitter costs a short gap rather than crackle; more than MAX_SECONDS waiting drops the oldest down to
START_SECONDS, so delay never builds up. The stream is stopped, never aborted while it runs (aborting
a running output silenced the reSpeaker's microphone, September 27 and October 26-27).
"""
import threading
import time

from ..contracts import ProviderFailure
from ..providers import Capability
from .protocol import AUDIO_RATE

START_SECONDS = .12
MAX_SECONDS = .4
BLOCK = 320   # Samples: 20 ms.


def open_output(index, callback, finished):
    import sounddevice as sd
    stream = sd.RawOutputStream(device=index, samplerate=AUDIO_RATE, channels=1, dtype='int16',
                                blocksize=BLOCK, latency='low', callback=callback, finished_callback=finished)
    return stream, sd.CallbackStop


def resolve_output(name):
    import sounddevice as sd
    from ..adapters.audio import resolve_device
    return resolve_device(sd.query_devices(), name, None, 'output')


class LiveSpeaker:
    def __init__(self, output_name='', *, clock=time.monotonic, open_stream=open_output, resolve=resolve_output,
                 name='telepresence_speaker'):
        self.output_name, self.clock, self.open_stream, self.resolve = output_name, clock, open_stream, resolve
        self.capability = Capability(name, 'sounddevice', 'a live voice behind a small jitter buffer',
            dependencies=('sounddevice',), resources=('speaker', f'{AUDIO_RATE} Hz mono, {BLOCK}-sample blocks'))
        self.lock = threading.Lock()
        self.buffer = bytearray()
        self.priming = True
        self.ending = threading.Event()
        self.finished = threading.Event()
        self.stream = None
        self.stop_type = None
        self.on_pcm = None   # (pcm, when it is heard): the mouth lights follow the voice.
        self.stats = {'underruns': 0, 'dropped_seconds': 0., 'played_seconds': 0., 'callback_errors': 0}
        self.start_bytes = int(START_SECONDS * AUDIO_RATE) * 2
        self.max_bytes = int(MAX_SECONDS * AUDIO_RATE) * 2

    def start(self):
        index = self.resolve(self.output_name)
        self.stream, self.stop_type = self.open_stream(index, self._callback, self.finished.set)
        self.stream.start()

    def write(self, pcm: bytes):
        if len(pcm) % 2:
            raise ProviderFailure('OddPCMByteCount')
        with self.lock:
            self.buffer += pcm
            if len(self.buffer) > self.max_bytes:
                drop = len(self.buffer) - self.start_bytes
                del self.buffer[:drop]
                self.stats['dropped_seconds'] += drop / 2 / AUDIO_RATE

    def clear(self):
        with self.lock:
            self.buffer.clear()
            self.priming = True

    def play_clip(self, pcm: bytes, *, wait=time.sleep) -> bool:
        """A whole recorded line through the call's stream, fed at the pace it plays (written at once,
        the jitter buffer would cut it back to START_SECONDS). Returns once it has played: True, or
        False when it was cut short by close or a stream that stopped playing. Its own end is not
        counted as a gap in the call's underruns."""
        if len(pcm) % 2:
            raise ProviderFailure('OddPCMByteCount')
        step = int(.1 * AUDIO_RATE) * 2
        before = self.stats['underruns']
        deadline = self.clock() + len(pcm) / 2 / AUDIO_RATE + 3.
        for start in range(0, len(pcm), step):
            while self.buffered_seconds() * AUDIO_RATE * 2 > self.max_bytes - step:   # Never trimmed.
                if self.ending.is_set() or self.clock() > deadline:
                    return False
                wait(.02)
            self.write(pcm[start:start + step])
        while self.buffered_seconds() > 0:
            if self.ending.is_set() or self.clock() > deadline:
                return False
            wait(.02)
        wait(.15)   # The device's own buffer plays out.
        with self.lock:
            if self.stats['underruns'] > before:
                self.stats['underruns'] -= 1
        return True

    def buffered_seconds(self) -> float:
        with self.lock:
            return len(self.buffer) / 2 / AUDIO_RATE

    def _callback(self, outdata, frames, timing, status):
        if self.ending.is_set():
            outdata[:] = b'\0' * len(outdata)
            raise self.stop_type   # Ends on this silent buffer, like a finished line.
        wanted = frames * 2
        with self.lock:
            if self.priming and len(self.buffer) >= self.start_bytes:
                self.priming = False
            if self.priming:
                piece = b''
            else:
                piece = bytes(self.buffer[:wanted])
                del self.buffer[:wanted]
                if len(piece) < wanted:
                    self.priming = True
                    self.stats['underruns'] += 1
        self.stats['played_seconds'] += len(piece) / 2 / AUDIO_RATE
        data = piece + b'\0' * (wanted - len(piece))
        outdata[:] = data
        if self.on_pcm is not None:
            try:
                at = self.clock() + (timing.outputBufferDacTime - timing.currentTime if timing is not None else 0.)
                self.on_pcm(data, at)
            except Exception:
                self.stats['callback_errors'] += 1   # The lights never stop the voice.

    def cancel(self):
        self.ending.set()

    def close(self):
        self.ending.set()
        stream, self.stream = self.stream, None
        if stream is None:
            return
        if not self.finished.wait(.5):
            stream.abort()   # It never reached its next callback: only a stalled stream is aborted.
        stream.close()
