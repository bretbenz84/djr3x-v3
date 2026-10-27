"""Pure endpointing over timestamped, VAD-labeled PCM blocks."""
from collections import deque
from dataclasses import dataclass
from uuid import uuid4

from .contracts import Capture


# Smart Turn (owner request, October 26): at this much silence the turn judge hears the capture so
# far, and a finished-sounding turn ends there instead of after the full end-of-speech silence.
TURN_CHECK_SECONDS = .2
# Saved robot audio (September): at .7, 158 of 163 turns ended at the check, and "Rex, stop | starting
# conversations" (.61) stayed one sentence. docs/TURN_DETECTION.md.
TURN_FINISHED = .7


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
    def __init__(self, session_id: str, silence: float = 0.7, turn=None):
        self.session_id, self.silence = session_id, silence
        # turn(pcm) -> probability the speaker finished, or None when it could not judge (adapters.turn).
        # Without one, or with early False (a Jeopardy answer's thinking pause), only silence ends speech.
        self.turn, self.early = turn, True
        self.judged = None   # The pause (its last speech time) already judged.
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
        self.judged = None

    def push(self, frame: Frame, speech: bool) -> Capture | None:
        boundary = None
        previous = self.frames[-1] if self.frames else self.pre[-1] if self.pre else None
        if previous and previous.echo_risk != frame.echo_risk:
            # Do not discard a human prefix just because playback begins before
            # endpoint silence. Nor carry playback preroll into the next person.
            boundary = self.finish(False, ended_by="playback_boundary")
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
        silent = frame.ended_at - self.last_speech
        if (not truncated and self.turn and self.early and not frame.echo_risk
                and silent >= TURN_CHECK_SECONDS and self.judged != self.last_speech):
            # Once per pause, on everything heard so far (the model wants the whole turn).
            self.judged = self.last_speech
            probability = self.turn(b"".join(f.pcm for f in self.frames))
            if probability is not None and probability >= TURN_FINISHED:
                return self.finish(ended_by="finished")
        if not truncated and silent < self.silence:
            return boundary
        return self.finish(truncated)

    def finish(self, truncated=False, ended_by=None):
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
                             voiced_intervals=tuple((a-self.frames[0].started_at,b-self.frames[0].started_at) for a,b in self.intervals),
                             ended_by=ended_by or ("truncated" if truncated else "silence"))
        self.reset()
        return result
