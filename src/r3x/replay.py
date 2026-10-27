"""Saved audio through the production endpoint, perception, Session and tracker."""
from dataclasses import asdict
import json
from pathlib import Path
import resource
import sqlite3
import time
from uuid import uuid4

from .contracts import Cancellation
from .dataset import load_manifest, read_wav, segment, oracle_capture
from .evaluation import Evaluation
from .memory import Memory
from .output import TextOutput
from .perception import Perception
from .providers import ProviderOwner
from .session import Session
from .speakers import SpeakerTracker
from .state import CoreState
from .trace import Trace, report


def snapshot_store(path):
    memory = Memory(":memory:")
    memory.start()
    if path.exists():
        if path.resolve()!=path:
            raise ValueError("ReplayStoreMustBeIndependent")
        try:
            with sqlite3.connect(path.as_uri()+"?mode=ro",uri=True) as source:
                source.backup(memory.connection)
            memory.validate_schema()
        except BaseException:
            memory.close()
            raise
    return memory


def run(manifest,root,settings,with_brain=False,oracle=False):
    from .adapters.asr import QwenASR
    from .adapters.audio import SileroVAD
    from .adapters.voice import CAMPlus, NAMESPACE
    from .adapters.hosted import OpenAIBrain
    from .settings import Secrets
    data,digest = load_manifest(manifest.resolve())
    clips = [c for c in data["clips"] if c["split"]=="evaluation"]
    if not clips:
        raise ValueError("NoEvaluationClips")
    memory = snapshot_store(root/"core.sqlite3")
    # Refuse to score enrollment audio or a lossless rewrap as held-out evidence.
    import hashlib
    enrollment_pcm = {r["digest"] for r in memory.rows("SELECT digest FROM recordings")}
    for clip in clips:
        if hashlib.sha256(read_wav(manifest.parent/clip["file"])).hexdigest() in enrollment_pcm:
            memory.close()
            raise ValueError("EvaluationContainsEnrolledAudio")
    run_id = uuid4().hex
    from .settings import ScratchPaths
    ScratchPaths(root).prepare()
    directory = root/"validation"/run_id
    directory.mkdir(parents=True,mode=0o700)
    trace = Trace(run_id,directory/"session.jsonl")
    asr = QwenASR(root/"models/qwen3-asr",settings.asr_trust_logprob,settings.transcript_corrections)
    encoder = CAMPlus(root/"models/campplus.onnx")
    vad = SileroVAD(settings.vad_threshold)
    turn = None
    if settings.smart_turn_enabled:
        from .adapters.turn import MODEL_FILE, SmartTurn
        turn = SmartTurn(root/MODEL_FILE)
    brain = OpenAIBrain(Secrets.load(root).openai_api_key) if with_brain else None
    owner = ProviderOwner((asr,encoder,vad)+((turn,) if turn else ())+((brain,) if with_brain else ()))
    evaluation = Evaluation(memory.profiles(NAMESPACE))
    sessions = {}
    session = None
    try:
        if not owner.start():
            raise ValueError("ReplayProviderStartupFailed:"+json.dumps([asdict(r) for r in owner.results]))
        trace.emit("replay_start",evidence="real_models_saved_audio",dataset_sha256=digest,
            inference="oracle_timing_encoder_diagnostic" if oracle else "production_segmentation",
            frozen=True,hosted_brain=with_brain,audio_played=False,video_captured=False,
            models=[p.capability.model for p in owner.providers],settings=asdict(settings))
        perception = Perception(asr,encoder,trace)
        for clip in clips:
            # Session boundaries clear acoustic continuity; no labels enter inference.
            if not session or sessions.get("label")!=clip["session"]:
                if session:
                    session.close()
                session = Session(uuid4().hex,brain,TextOutput(trace),trace,
                    CoreState(memory,SpeakerTracker(NAMESPACE),trace,frozen=True))
                sessions = {"label":clip["session"],"offset":time.monotonic()}
            offset = sessions["offset"]
            pcm = read_wav(manifest.parent/clip["file"])
            sessions["offset"] += len(pcm)/32000+1.
            captures = [oracle_capture(pcm,clip,session.session_id,offset)] if oracle else segment(
                pcm,session.session_id,vad,settings.end_silence_seconds,offset,clip["device"],turn and turn.finished)
            count = 0
            try:
                for capture in captures:
                    perceived_turns = (perception.analyze(capture,Cancellation()),) if oracle else perception.analyze_many(capture,Cancellation())
                    for perceived in perceived_turns:
                        part = perceived.capture
                        delivery = session.process_turn(part,perceived.transcript,session.revision,perceived.voice)
                        if session.state.current and session.state.current[1].capture_id==part.capture_id:
                            evaluation.add(clip,part,session.state.current[4],perceived.voice.voiced_seconds,offset)
                        else:
                            evaluation.missing.append({"clip":clip["id"],"reason":"no_attribution","status":delivery.status})
                        count += 1
                if not count:
                    evaluation.missing.append({"clip":clip["id"],"reason":"no_capture"})
            except Exception as exc:
                evaluation.missing.append({"clip":clip["id"],"reason":type(exc).__name__})
                trace.emit("replay_clip_failed",clip=clip["id"],error_type=type(exc).__name__)
        result = {**evaluation.report(),"dataset_sha256":digest,"namespace":NAMESPACE,
            "evaluation_clips":len(clips),"mode":"oracle_timing" if oracle else "production",
            "frozen":True,"audio_played":False,"camera_captured":False,
            "peak_rss_bytes":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "trace":str(directory/"session.jsonl")}
        if oracle:
            result["proposed_clean_gate_passed"] = False
        result["runtime"] = report(directory/"session.jsonl")
        (directory/"report.json").write_text(json.dumps(result,indent=2))
        print(json.dumps({"report":str(directory/"report.json"),"clean_enrolled":result["clean_enrolled"],
                          "failures":evaluation.missing,"group_acceptance":False},indent=2))
        return not evaluation.missing
    finally:
        if session:
            session.close()
        owner.close()
        memory.close()
        trace.close()
