"""Scripted providers exercise the production Session, not a second mock brain."""
import json
from pathlib import Path
import time
import tempfile
from uuid import uuid4

from .contracts import Capture, Transcript, Playback,ToolCall
from .output import OutputOwner
from .session import Session
from .trace import Trace


class ScriptedBrain:
    def __init__(self):
        self.text = ""
        self.tool = None

    def reply(self, snapshot, cancel):
        if self.tool and not snapshot.continuation:
            arguments = {k:snapshot.turn_id if v=="$turn" else v for k,v in self.tool["arguments"].items()}
            yield ToolCall(uuid4().hex,self.tool["name"],arguments)
            return
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
    if fixture.get("core"):
        return run_core(fixture,path.name)
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


def run_core(fixture,name):
    from .memory import Memory
    from .speakers import SpeakerTracker,VoiceObservation
    from .state import CoreState
    namespace = "synthetic-scenario:3"
    brain = ScriptedBrain()
    trace = Trace(uuid4().hex)
    trace.emit("scenario_start",evidence="synthetic",fixture=name,
               note="Scripted model decisions; production Session/state/store; simulated audio delivery")
    passed = True
    with tempfile.TemporaryDirectory() as tmp:
        database=Path(tmp).resolve()/"core.sqlite3"
        memory=Memory(database)
        memory.start()
        def fresh():
            return Session(uuid4().hex,brain,OutputOwner(SyntheticTTS(),SyntheticSink(),trace),trace,
                           CoreState(memory,SpeakerTracker(namespace),trace))
        session=fresh()
        try:
            for turn in fixture["turns"]:
                if turn.get("restart"):
                    session.close()
                    memory.close()
                    memory=Memory(database)
                    memory.start()
                    session=fresh()
                brain.text=turn["reply"]
                brain.tool=None
                if turn.get("tool"):
                    args={}
                    for key,value in turn["tool"]["arguments"].items():
                        if isinstance(value,str) and value.startswith("$person:"):
                            people=memory.resolve(value.removeprefix("$person:"))
                            if len(people)!=1:raise ValueError("FixturePersonAmbiguous")
                            value=people[0]["id"]
                        if value=="$claim":
                            value=memory.claims()[0]["id"]
                        args[key]=value
                    brain.tool={"name":turn["tool"]["name"],"arguments":args}
                duration=float(turn.get("voiced_seconds",3.))
                now=time.monotonic()
                capture=Capture(uuid4().hex,session.session_id,uuid4().bytes*int(2000*duration),now-duration,now,now,
                                voiced_intervals=((0.,duration),),device="synthetic fixture")
                vector=tuple(turn.get("voice",(1.,0.,0.)))
                observation=VoiceObservation(namespace,vector,((0.,duration,vector),),duration,1.,False,"synthetic")
                receipt=session.process_turn(capture,Transcript(turn["text"],0.,True),session.revision,observation)
                actual={"people":len(memory.people()),"accepted_claims":len(memory.claims()),
                        "recordings":memory.rows("SELECT count(*) AS n FROM recordings")[0]["n"],
                        "supported_profiles":len(memory.profiles(namespace)),"enrollments":len(session.state.enrollments)}
                okay=receipt.status=="completed" and all(actual[k]==v for k,v in turn.get("expect",{}).items())
                if turn.get("expect_value"):
                    okay=okay and any(c["value"]==turn["expect_value"] for c in memory.claims())
                passed=passed and okay
                trace.emit("scenario_assertion",text=turn["text"],passed=okay,actual=actual)
        finally:
            session.close()
            memory.close()
    trace.emit("scenario_done",passed=passed,evidence="synthetic")
    return passed
