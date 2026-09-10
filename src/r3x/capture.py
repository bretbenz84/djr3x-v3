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

    def reset(self):
        self.pre.clear()
        self.frames.clear()
        self.last_speech = None
        self.voiced = 0.

    def push(self, frame: Frame, speech: bool) -> Capture | None:
        if not self.frames:
            if not speech:
                self.pre.append(frame)
                return None
            self.frames.extend(self.pre)
            self.pre.clear()
        self.frames.append(frame)
        if speech:
            self.last_speech = frame.ended_at
            self.voiced += frame.ended_at - frame.started_at
        truncated = frame.ended_at - self.frames[0].started_at >= 20
        if not truncated and frame.ended_at - self.last_speech < self.silence:
            return None
        result = None
        if self.voiced >= .16:
            result = Capture(uuid4().hex, self.session_id, b"".join(f.pcm for f in self.frames),
                             self.frames[0].started_at, self.last_speech, frame.ended_at,
                             echo_risk=any(f.echo_risk for f in self.frames), truncated=truncated)
        self.reset()
        return result
