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


class Endpoint:
    def __init__(self, session_id: str, silence: float = .6):
        self.session_id, self.silence = session_id, silence
        self.pre = deque(maxlen=8)  # 256 ms at 512 frames / 16 kHz.
        self.frames: list[Frame] = []
        self.last_speech = None
        self.voiced = 0.
        self.intervals = []

    def reset(self):
        self.pre.clear()
        self.frames.clear()
        self.last_speech = None
        self.voiced = 0.
        self.intervals.clear()

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
        if self.frames and self.voiced >= .16:
            result = Capture(uuid4().hex, self.session_id, b"".join(f.pcm for f in self.frames),
                             self.frames[0].started_at, self.last_speech, self.frames[-1].ended_at,
                             echo_risk=any(f.echo_risk for f in self.frames), truncated=truncated,
                             voiced_intervals=tuple((a-self.frames[0].started_at,b-self.frames[0].started_at) for a,b in self.intervals))
        self.reset()
        return result
