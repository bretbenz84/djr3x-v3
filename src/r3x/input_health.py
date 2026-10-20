"""Bounded microphone/VAD diagnostics, without retaining audio or interpreting it."""
from array import array
import math
import sys

# October 19 (b4ce6464): three or four shouted stops during a 36 s approach left one
# capture; 5 s summaries could not show whether the processed channel carried any voice
# while the wheels ran. While they run, windows are one second and marked.
INTERVAL_S = 5.
MOVING_INTERVAL_S = 1.


class InputHealth:
    def __init__(self, trace, device):
        self.trace, self.device = trace, device
        self.began = trace.clock()
        self.frames = self.samples = self.energy = self.peak = 0
        self.speech_frames = self.echo_frames = 0
        self.peak_vad_probability = None
        self.moving = False

    def observe(self, frame, speech, pending, *, vad_probability=None, wheels_moving=False):
        now = self.trace.clock()
        if wheels_moving != self.moving:
            if self.frames:
                self._emit(now, pending)   # A window never mixes rolling with standing still.
            self.moving = wheels_moving
        samples = array('h', frame.pcm)
        if sys.byteorder != 'little': samples.byteswap()
        self.frames += 1
        self.samples += len(samples)
        self.energy += sum(v*v for v in samples)
        self.peak = max(self.peak, max((abs(v) for v in samples), default=0))
        if vad_probability is not None:
            self.peak_vad_probability = max(self.peak_vad_probability or 0.,vad_probability)
        self.speech_frames += bool(speech)
        self.echo_frames += bool(frame.echo_risk)
        if now-self.began < (MOVING_INTERVAL_S if self.moving else INTERVAL_S): return
        self._emit(now, pending)

    def _emit(self, now, pending):
        def db(value):
            return 20*math.log10(value/32768) if value else None
        self.trace.emit('audio_input_health', device=self.device,
            interval_seconds=now-self.began, frames=self.frames,
            peak_dbfs=db(self.peak), rms_dbfs=db(math.sqrt(self.energy/self.samples)) if self.samples else None,
            speech_frames=self.speech_frames, echo_frames=self.echo_frames,
            peak_vad_probability=self.peak_vad_probability,
            utterance_pending=bool(pending), wheels_moving=self.moving)
        self.began = now
        self.frames = self.samples = self.energy = self.peak = 0
        self.speech_frames = self.echo_frames = 0
        self.peak_vad_probability = None
