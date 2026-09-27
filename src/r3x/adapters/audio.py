"""Explicit selected-channel input/VAD and one PCM output stream."""
from array import array
from collections import deque
from queue import Empty, Full, Queue
import threading
import time

from ..capture import Frame
from ..contracts import Cancellation, Cancelled, Playback, ProviderFailure
from ..providers import Capability


def selected_pcm(data, channels: int, gain: float) -> bytes:
    """Select channel zero, never average raw capsules into processed audio."""
    if channels == 1 and gain == 1:
        return bytes(data)
    import sys
    samples = array('h')
    samples.frombytes(bytes(data))
    if sys.byteorder != 'little':
        samples.byteswap()
    if len(samples) % channels:
        raise ValueError('IncompleteInterleavedFrame')
    mono = array('h', (max(-32768, min(32767, round(v * gain))) for v in samples[::channels]))
    if sys.byteorder != 'little':
        mono.byteswap()
    return mono.tobytes()


def resolve_device(devices, name: str, index: int | None, kind: str):
    candidates = [(i, d) for i, d in enumerate(devices) if d[f"max_{kind}_channels"] >= 1]
    if name:
        exact = [i for i, d in candidates if d["name"].casefold() == name.casefold()]
        matches = exact or [i for i, d in candidates if name.casefold() in d["name"].casefold()]
        if len(matches) != 1:
            raise ProviderFailure("DeviceNameMissingOrAmbiguous")
        return matches[0]  # Stable name wins over an old enumeration index.
    if index is not None:
        if index not in [i for i, _ in candidates]:
            raise ProviderFailure("DeviceIndexUnavailable")
        return index
    if kind == "input":
        raise ProviderFailure("MicrophoneSelectionRequired")
    return None  # Explicit documented default: system playback device.


class Microphone:
    def __init__(self, settings, echo_risk, clock=time.monotonic):
        self.settings, self.echo_risk, self.clock = settings, echo_risk, clock
        self.capability = Capability("capture", "sounddevice", "explicit selected-channel microphone",
                                     dependencies=("sounddevice",), resources=(
                                         f'{settings.microphone_mode}; channel 0; gain {settings.input_gain:g}',))
        self.frames = Queue(maxsize=128)
        self.stream = None
        self.dropped = 0
        self.status_errors = 0
        self.last_callback = 0.
        self.stopped = threading.Event()
        self.device_name = settings.input_device_name or "configured_index"
        self.trace = None
        self.motion_risk = lambda: False
        self.channels = 2 if settings.microphone_mode == 'respeaker-flex' else 1
        self.clock_offset = None

    @property
    def source_device(self):
        return (f'{self.device_name}; channel=0; gain={self.settings.input_gain:g}; '
                f'mode={self.settings.microphone_mode}')

    def start(self):
        import sounddevice as sd
        devices = sd.query_devices()
        index = resolve_device(devices, self.settings.input_device_name,
                               self.settings.input_device_index, "input")
        self.device_name = devices[index]["name"]
        if self.settings.microphone_mode == 'respeaker-flex':
            output = resolve_device(devices, self.settings.output_device_name, None, 'output')
            if ('xvf3800' not in self.device_name.lower() or output != index or
                    devices[index]['max_input_channels'] < 2):
                raise ProviderFailure('FlexRequiresSameSelectedInputAndOutputDevice')
        def callback(data, count, timing, status):
            now = self.clock()
            self.last_callback = now
            if status:
                self.status_errors += 1
            # Preserve the stream clock mapping established before callbacks;
            # callback/GIL delay must not become a false gap in captured PCM.
            began = timing.inputBufferAdcTime + self.clock_offset
            frame = Frame(selected_pcm(data, self.channels, self.settings.input_gain), began, began + count / 16000,
                          not self.settings.echo_isolated and self.echo_risk(), self.motion_risk())
            try:
                self.frames.put_nowait(frame)
            except Full:
                self.dropped += 1
        self.stream = sd.RawInputStream(device=index, samplerate=16000, channels=self.channels,
                                        dtype="int16", blocksize=512, callback=callback)
        before=self.clock()
        stream_time=self.stream.time
        after=self.clock()
        self.clock_offset=(before+after)/2-stream_time
        self.last_callback = self.clock()
        self.stream.start()
        if self.trace:
            self.trace.emit('microphone_configuration', device=self.device_name,
                mode=self.settings.microphone_mode, channels_opened=self.channels,
                selected_channel=0, gain=self.settings.input_gain,
                hardware_aec=self.settings.microphone_mode == 'respeaker-flex',
                playback_suppression=not self.settings.echo_isolated)

    def discard_pending(self):
        """Listening begins after startup speech; never replay its queued PCM."""
        while True:
            try: self.frames.get_nowait()
            except Empty: break

    def read(self):
        if self.stopped.is_set():
            raise Cancelled()
        try:
            return self.frames.get(timeout=.1)
        except Empty:
            if self.clock() - self.last_callback > 3:
                raise ProviderFailure("MicrophoneStalled")
            return None

    def cancel(self):
        self.stopped.set()

    def close(self):
        if self.stream:
            self.stream.abort()
            self.stream.close()
            self.stream = None


class SileroVAD:
    def __init__(self, threshold: float):
        self.threshold = threshold
        self.last_probability = None
        self.model = None
        self.capability = Capability("vad", "silero-vad", "speech endpoint detection",
                                     "Silero 6.2.1", ("silero-vad", "torch", "torchaudio", "numpy"), ("CPU",))

    def start(self):
        import torch
        from silero_vad import load_silero_vad
        torch.set_num_threads(1)
        # Silero currently ships a TorchScript artifact. Keep that supported
        # loader; silence only its upstream migration notice, not runtime errors.
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore', message=r'`torch\.jit\.load` is deprecated\. Please switch to `torch\.export`\.',
                                    category=FutureWarning, module=r'torch\.jit\._serialization')
            self.model = load_silero_vad()

    def is_speech(self, pcm: bytes):
        import numpy as np
        import torch
        if self.model is None:
            raise ProviderFailure("VADNotStarted")
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        with torch.inference_mode():
            self.last_probability = self.model(torch.from_numpy(audio), 16000).item()
            return self.last_probability >= self.threshold

    def reset(self):
        if self.model:
            self.model.reset_states()

    def cancel(self):
        pass

    def close(self):
        self.model = None


class AudioSink:
    """Callback receipts use the device DAC clock; completion waits for drain."""
    def __init__(self, output_name="", clock=time.monotonic):
        self.output_name, self.clock = output_name, clock
        self.capability = Capability("playback", "sounddevice", "single speech output",
                                     dependencies=("sounddevice",), resources=("speaker",))
        self.sd = None
        self.stream = None
        self.index = None
        self.on_pcm = None

    def start(self):
        import sounddevice as sd
        self.sd = sd
        self.index = resolve_device(sd.query_devices(), self.output_name, None, "output")

    def cancel(self):
        # The active turn token makes the next callback raise CallbackAbort.
        # No blocking PortAudio API call from a signal handler/control thread.
        pass

    def close(self):
        if self.stream:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        self.sd = None

    def play(self, chunks, cancel: Cancellation, on_start) -> Playback:
        if self.sd is None:
            raise ProviderFailure("PlaybackNotStarted")
        queue = Queue(maxsize=16)  # <1.5 sec at 4096 bytes/block.
        eof = threading.Event()
        finished = threading.Event()
        drained = threading.Event()
        pending = b""
        frames = 0
        underruns = 0
        started_at = None
        last_callback = self.clock()
        announced = False

        def callback(outdata, count, timing, status):
            nonlocal pending, frames, underruns, started_at, last_callback
            last_callback = self.clock()
            outdata[:] = b"\0" * len(outdata)
            if cancel.event.is_set():
                raise self.sd.CallbackAbort
            if status.output_underflow:
                underruns += 1
            wanted = count * 2
            while len(pending) < wanted:
                try:
                    pending += queue.get_nowait()
                except Empty:
                    break
            actual = min(wanted, len(pending))
            if actual:
                outdata[:actual] = pending[:actual]
                pending = pending[actual:]
                frames += actual // 2
                if started_at is None:
                    started_at = self.clock() + timing.outputBufferDacTime - timing.currentTime
            if self.on_pcm:
                self.on_pcm(bytes(outdata),self.clock()+timing.outputBufferDacTime-timing.currentTime)
            if actual < wanted:
                if eof.is_set() and queue.empty() and not pending:
                    drained.set()
                    raise self.sd.CallbackStop  # Drain final padded callback before finished.
                underruns += 1  # Starvation is reported; no deliberate audio discard.

        def announce():
            nonlocal announced
            if started_at is not None and not announced:
                on_start(started_at)
                announced = True

        def open_stream():
            self.stream = self.sd.RawOutputStream(device=self.index, samplerate=22050,
                channels=1, dtype="int16", blocksize=1024, callback=callback,
                finished_callback=finished.set)
            self.stream.start()

        remainder = b""
        try:
            for chunk in chunks:
                cancel.check()
                remainder += chunk
                aligned = len(remainder) - len(remainder) % 2
                piece, remainder = remainder[:aligned], remainder[aligned:]
                if not piece:
                    continue
                while True:
                    cancel.check()
                    announce()
                    if self.stream and (finished.is_set() or self.clock() - last_callback > 3):
                        raise ProviderFailure("PlaybackStalled")
                    try:
                        queue.put(piece, timeout=.05)
                        break
                    except Full:
                        continue
                if self.stream is None and queue.qsize() >= 3:
                    open_stream()  # About 280 ms of PCM before initial playback.
                announce()
            if remainder:
                raise ProviderFailure("OddPCMByteCount")
            eof.set()
            if self.stream is None:
                if queue.empty():
                    raise ProviderFailure("EmptyAudio")
                open_stream()
            deadline = self.clock() + 5
            while not finished.wait(.05):
                cancel.check()
                announce()
                if self.clock() > deadline:
                    raise ProviderFailure("PlaybackDrainTimeout")
            announce()
            if not drained.is_set():
                cancel.check()
                raise ProviderFailure('PlaybackNotDrained')
            return Playback(frames, started_at, self.clock(), underruns)
        finally:
            if self.stream:
                self.stream.abort()  # Already drained on successful completion.
                self.stream.close()
                self.stream = None
            close = getattr(chunks, "close", None)
            if close:
                close()
