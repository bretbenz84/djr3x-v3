"""Pure endpointing over timestamped, VAD-labeled PCM blocks."""
from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from .contracts import Capture


@dataclass(frozen=True)
class Frame:
    pcm: bytes
    started_at: float
    ended_at: float
    echo_risk: bool = False
    motion_risk: bool = False
    # The reSpeaker Flex's channel 1 (its ASR output: echo-cancelled and beamformed, no AGC and no
    # ducking while Rex plays), for the keyword spotter only; channel 0 stays the listening channel.
    beam_pcm: bytes | None = None


class Endpoint:
    def __init__(self, session_id: str, silence: float = 0.7):
        self.session_id, self.silence = session_id, silence
        # 1 s at 512 frames / 16 kHz. Silero can lag a soft onset ("You can come
        # here" lost "You can"); 8/41 September 27 captures began mid-speech at 256 ms.
        self.pre = deque(maxlen=32)
        self.frames: list[Frame] = []
        self.last_speech = None
        self.voiced = 0.
        self.intervals = []
        self.voiced_motion = 0.

    def reset(self):
        self.pre.clear()
        self.frames.clear()
        self.last_speech = None
        self.voiced = 0.
        self.intervals.clear()
        self.voiced_motion = 0.

    def push(self, frame: Frame, speech: bool) -> Capture | None:
        boundary = None
        previous = self.frames[-1] if self.frames else self.pre[-1] if self.pre else None
        if previous and previous.echo_risk != frame.echo_risk:
            # Do not discard a human prefix just because playback begins before
            # endpoint silence. Nor carry playback preroll into the next person.
            boundary = self.finish(False)
        if not self.frames:
            if not speech:
                self.pre.append(frame)
                return boundary
            self.frames.extend(self.pre)
            self.pre.clear()
        self.frames.append(frame)
        if speech:
            self.last_speech = frame.ended_at
            self.voiced += frame.ended_at - frame.started_at
            if frame.motion_risk:
                self.voiced_motion += frame.ended_at - frame.started_at
            if self.intervals and abs(self.intervals[-1][1]-frame.started_at) < .002:
                self.intervals[-1] = (self.intervals[-1][0],frame.ended_at)
            else:
                self.intervals.append((frame.started_at,frame.ended_at))
        truncated = frame.ended_at - self.frames[0].started_at >= 20
        if not truncated and frame.ended_at - self.last_speech < self.silence:
            return boundary
        return self.finish(truncated)

    def finish(self, truncated=False):
        result = None
        # VAD has already admitted speech. Short commands may have only one or
        # two confident frames among unvoiced consonants; a second duration gate
        # silently loses them before ASR can examine the complete buffered PCM.
        if self.frames and self.voiced > 0:
            result = Capture(uuid4().hex, self.session_id, b"".join(f.pcm for f in self.frames),
                             self.frames[0].started_at, self.last_speech, self.frames[-1].ended_at,
                             # Motion lowers voice confidence when it overlaps a real share of
                             # the speech, not the 1 s pre-roll or a last idle beat.
                             motion_risk=self.voiced_motion >= .3*self.voiced,
                             echo_risk=any(f.echo_risk for f in self.frames), truncated=truncated,
                             voiced_intervals=tuple((a-self.frames[0].started_at,b-self.frames[0].started_at) for a,b in self.intervals))
        self.reset()
        return result
