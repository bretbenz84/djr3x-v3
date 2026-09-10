"""Scripted providers exercise the production Session, not a second mock brain."""
import json
from pathlib import Path
import time
from uuid import uuid4

from .contracts import Capture, Transcript, Playback
from .output import OutputOwner
from .session import Session
from .trace import Trace


class ScriptedBrain:
    def __init__(self):
        self.text = ""

    def reply(self, snapshot, cancel):
        for offset in range(0, len(self.text), 9):
            cancel.check()
            yield self.text[offset:offset + 9]


class SyntheticTTS:
    def synthesize(self, text, cancel):
        cancel.check()
        yield b"\0\0" * 32


class SyntheticSink:
    def play(self, chunks, cancel, on_start):
        began = time.monotonic()
        on_start(began)
        frames = 0
        for chunk in chunks:
            cancel.check()
            frames += len(chunk) // 2
        return Playback(frames, began, time.monotonic(), 0)

    def cancel(self):
        pass


def run(path: Path):
    fixture = json.loads(path.read_text())
    brain = ScriptedBrain()
    session_id = uuid4().hex
    trace = Trace(session_id)
    trace.emit("scenario_start", evidence="synthetic", fixture=path.name,
               note="Scripted model output and simulated playback; no semantic or audio accuracy evidence")
    session = Session(session_id, brain, OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace)
    passed = True
    for turn in fixture["turns"]:
        now = time.monotonic()
        brain.text = turn["reply"]
        capture = Capture(uuid4().hex, session_id, b"\0\0" * 16, now, now, now)
        delivery = session.process_turn(capture, Transcript(turn["text"], None, False), session.revision)
        passed = passed and delivery.status == "completed"
    session.close()
    trace.emit("scenario_done", passed=passed, evidence="synthetic")
    return passed
