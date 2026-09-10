"""Explicit paid integration check: synthetic speech -> real core -> silent text.

No microphone, video, or playback. The synthetic voice/claims stay in this run's
separate validation database; they never become live participant profiles.
"""
import json
from pathlib import Path
import resource
import time
from uuid import uuid4

from r3x.adapters.asr import QwenASR
from r3x.adapters.audio import SileroVAD
from r3x.adapters.hosted import ElevenLabsTTS,OpenAIBrain
from r3x.adapters.voice import CAMPlus,NAMESPACE
from r3x.contracts import Cancellation
from r3x.dataset import segment,write_wav
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.perception import Perception
from r3x.providers import ProviderOwner
from r3x.session import Session
from r3x.settings import ScratchPaths,Secrets
from r3x.speakers import SpeakerTracker
from r3x.state import CoreState
from r3x.trace import Trace,report


def main():
    root=ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    root.prepare()
    folder=root.root/"validation"/uuid4().hex
    folder.mkdir(parents=True)
    secrets=Secrets.load(root.root)
    asr=QwenASR(root.root/"models/qwen3-asr",-.35)
    vad=SileroVAD(.5)
    encoder=CAMPlus(root.root/"models/campplus.onnx")
    brain=OpenAIBrain(secrets.openai_api_key)
    tts=ElevenLabsTTS(secrets.elevenlabs_api_key,secrets.elevenlabs_voice_id)
    owner=ProviderOwner((asr,vad,encoder,brain,tts))
    trace=Trace("synthetic-conversation",folder/"session.jsonl")
    memory=Memory(folder/"synthetic.sqlite3")
    memory.start()
    session=None
    evidence={"passed":False,"evidence":"real ASR, VAD, CAM++, language model; synthetic spoken participant; silent replies",
              "audio_played":False,"video_captured":False,"microphone_opened":False,"group_accuracy":None,"turns":[]}
    try:
        if not owner.start():raise RuntimeError("ProviderStartupFailed")
        perception=Perception(asr,encoder,trace)
        def fresh():
            return Session(uuid4().hex,brain,TextOutput(trace),trace,CoreState(memory,SpeakerTracker(NAMESPACE),trace))
        session=fresh()
        def turn(text,index):
            import numpy as np
            from scipy.signal import resample_poly
            pcm=b"".join(tts.synthesize(text,Cancellation()))
            samples=resample_poly(np.frombuffer(pcm,dtype="<i2").astype(np.float32),320,441)
            pcm=np.clip(samples,-32768,32767).astype("<i2").tobytes()
            write_wav(folder/f"input-{index}.wav",pcm)
            captures=list(segment(pcm,session.session_id,vad,offset=time.monotonic()-len(pcm)/32000-1,device="synthetic ElevenLabs test input"))
            if not captures:raise RuntimeError("SyntheticSpeechNotCaptured")
            for capture in captures:
                heard=perception.analyze(capture,Cancellation())
                receipt=session.process_turn(capture,heard.transcript,session.revision,heard.voice)
                evidence["turns"].append({"reference":text,"transcript":heard.transcript.text,"trusted":heard.transcript.trusted,
                    "voiced_seconds":heard.voice.voiced_seconds,"window_consistency":heard.voice.consistency,
                    "attribution":session.state.current[4].state,"delivery":receipt.status,"reply":" ".join(receipt.completed_text)})
                if receipt.status!="completed":raise RuntimeError("SyntheticConversationTurnFailed")
        lines=["I'm Jordan Avery. I spend my weekends restoring old clocks.",
               "I love finding a dusty old clock at a flea market and bringing it back to life.",
               "My workshop is in the garage, and right now I am fixing a small wooden mantel clock."]
        for i,line in enumerate(lines):turn(line,i)
        people=memory.resolve("Jordan Avery")
        evidence["enrolled"]=len(people)==1 and people[0]["status"]=="supported"
        if not evidence["enrolled"]:raise RuntimeError("SyntheticNaturalEnrollmentIncomplete")
        pid=people[0]["id"]
        turn("Please remember that my favorite color is blue.",3)
        evidence["accepted_self_report"]=any("blue" in c["value"].lower() and c["said_by"]==pid for c in memory.claims(pid))
        session.close()
        memory.close()
        memory=Memory(folder/"synthetic.sqlite3")
        memory.start()
        session=fresh()
        turn("Can you tell me what my favorite color is?",4)
        evidence["recall_after_restart"]="blue" in evidence["turns"][-1]["reply"].lower() and session.state.current[4].person_id==pid
        turn("Actually, my favorite color is green, not blue. Please correct that.",5)
        evidence["corrected"]=any("green" in c["value"].lower() and c["said_by"]==pid for c in memory.claims(pid)) and not any("blue" in c["value"].lower() for c in memory.claims(pid))
        evidence["recordings"]=memory.rows("SELECT count(*) AS n FROM recordings")[0]["n"]
        evidence["passed"]=all(evidence[k] for k in ("enrolled","accepted_self_report","recall_after_restart","corrected"))
        evidence["peak_rss_bytes_macos"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception as exc:
        evidence["error_type"]=type(exc).__name__
        if isinstance(exc,RuntimeError):evidence["reason"]=str(exc)
    finally:
        if session:session.close()
        memory.close()
        owner.close()
        trace.close()
        evidence["runtime"]=report(folder/"session.jsonl")
        (folder/"evidence.json").write_text(json.dumps(evidence,indent=2))
    print(json.dumps({"passed":evidence["passed"],"evidence":str(folder/"evidence.json")}))
    return 0 if evidence["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
