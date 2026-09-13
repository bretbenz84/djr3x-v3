"""Real ASR/VAD/CAM++ and blank-image CPU benchmark; no live device calls."""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import time
from uuid import uuid4
import wave

from r3x.adapters.asr import QwenASR
from r3x.adapters.audio import SileroVAD
from r3x.adapters.voice import CAMPlus
from r3x.contracts import Cancellation
from r3x.dataset import write_wav,segment
from r3x.perception import Perception
from r3x.providers import ProviderOwner
from r3x.settings import ScratchPaths
from r3x.trace import Trace


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("input",type=Path,help="Explicit synthetic saved input WAV")
    args=parser.parse_args()
    paths=ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    folder=paths.root/"validation"/uuid4().hex
    folder.mkdir(parents=True)
    trace=Trace("saved-core",folder/"session.jsonl")
    asr=QwenASR(paths.root/"models/qwen3-asr",-.35)
    encoder=CAMPlus(paths.root/"models/campplus.onnx")
    vad=SileroVAD(.5)
    owner=ProviderOwner((asr,vad,encoder))
    evidence={"source":"synthetic saved speech","live_devices":False,"video_captured":False,"audio_played":False,"passed":False}
    try:
        import numpy as np
        from scipy.signal import resample_poly
        with wave.open(str(args.input),"rb") as handle:
            assert handle.getnchannels()==1 and handle.getsampwidth()==2
            rate=handle.getframerate()
            pcm=handle.readframes(handle.getnframes())
        import math
        divisor=math.gcd(rate,16000)
        samples=resample_poly(np.frombuffer(pcm,dtype="<i2").astype(np.float32),16000//divisor,rate//divisor)
        pcm=np.clip(samples,-32768,32767).astype("<i2").tobytes()
        write_wav(folder/"input16k.wav",pcm)
        began=time.monotonic()
        if not owner.start(): raise RuntimeError("ProviderStartupFailed")
        evidence["startup_seconds"]=time.monotonic()-began
        captures=list(segment(pcm,"saved-core",vad,offset=time.monotonic()-len(pcm)/32000-1))
        perception=Perception(asr,encoder,trace)
        results=[]
        for capture in captures:
            perceived=perception.analyze(capture,Cancellation())
            results.append({"text":perceived.transcript.text,"trusted":perceived.transcript.trusted,
                "dimensions":len(perceived.voice.embedding or ()),"windows":len(perceived.voice.windows),
                "consistency":perceived.voice.consistency,"voiced_seconds":perceived.voice.voiced_seconds,
                "namespace":perceived.voice.namespace})
        evidence["captures"]=results
        began=time.monotonic()
        from r3x.adapters.face import Detector
        detector=Detector(paths.root/"models/scrfd_10g.onnx",640)
        evidence["face_detector_startup_seconds"]=time.monotonic()-began
        gray=np.zeros((480,640,3),dtype=np.uint8)
        timings=[]
        for _ in range(20):
            began=time.monotonic()
            faces=detector.detect(gray)
            timings.append(time.monotonic()-began)
            assert not len(faces)
        evidence["blank_frame_processing_seconds"]={"n":20,"median":statistics.median(timings),"p95":sorted(timings)[18]}
        evidence["peak_rss_bytes_macos"]=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        evidence["passed"]=bool(results) and all(r["trusted"] and r["dimensions"]==192 for r in results)
        manifest={"version":1,"roster":{"synthetic-rex":"Synthetic Rex voice, not a human enrollment"},"clips":[{
            "id":"synthetic-camping","file":"input16k.wav","sha256":hashlib.sha256((folder/"input16k.wav").read_bytes()).hexdigest(),
            "session":"synthetic-provider-session","device":"ElevenLabs synthetic file","condition":"synthetic speech",
            "split":"evaluation","resolvable":True,"overlap":False,
            "segments":[{"start":0.,"end":len(pcm)/32000,"speakers":["synthetic-rex"]}]}]}
        (folder/"manifest.json").write_text(json.dumps(manifest,indent=2))
    except Exception as exc:
        evidence["error_type"]=type(exc).__name__
        from r3x.contracts import ProviderFailure
        if isinstance(exc,ProviderFailure): evidence["reason"]=str(exc)
    finally:
        owner.close()
        trace.close()
        (folder/"evidence.json").write_text(json.dumps(evidence,indent=2))
    print(json.dumps({"passed":evidence["passed"],"evidence":str(folder/"evidence.json")}))
    return 0 if evidence["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
