"""Explicit local recording and independently labeled, digest-frozen replay data."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import math
import wave

from .capture import Endpoint, Frame
from .contracts import Capture


def read_wav(path):
    if path.stat().st_size>64*1024*1024:
        raise ValueError("WaveExceedsReplayLimit")
    with wave.open(str(path),"rb") as handle:
        if (handle.getnchannels(),handle.getsampwidth(),handle.getframerate(),handle.getcomptype()) != (1,2,16000,"NONE"):
            raise ValueError("ReplayRequiresPCM16Mono16k")
        pcm = handle.readframes(handle.getnframes())
        if len(pcm)!=handle.getnframes()*2:
            raise ValueError("TruncatedWave")
        return pcm


def write_wav(path,pcm):
    with path.open("xb") as stream:
        with wave.open(stream,"wb") as handle:
            handle.setparams((1,2,16000,0,"NONE","not compressed"))
            handle.writeframes(pcm)


class CaptureRecorder:
    def __init__(self,root,session_id):
        self.root = root/session_id
        if self.root.resolve()!=self.root:
            raise ValueError("RecordingDirectoryLinked")
        self.root.mkdir(mode=0o700)
        self.input_pcm = bytearray()
        self.input_frames = []
        self.input_finished = False

    def record_input(self, frame):
        """First 30 PCM seconds, before VAD; diagnostic only, never enrollment."""
        if self.input_finished:return None
        offset=len(self.input_pcm)
        pcm=frame.pcm[:max(0,30*32000-offset)]
        if pcm:
            self.input_pcm.extend(pcm)
            self.input_frames.append({'offset_seconds':offset/32000,'started_at':frame.started_at,
                'ended_at':frame.ended_at,'pcm_bytes':len(pcm),'echo_risk':frame.echo_risk,'motion_risk':frame.motion_risk})
        if len(self.input_pcm)>=30*32000:return self.close()
        return None

    def close(self):
        if self.input_finished:return None
        self.input_finished=True
        if not self.input_pcm:return None
        path=self.root/'input-diagnostic.wav'
        try:
            write_wav(path,self.input_pcm)
            with (self.root/'input-diagnostic.json').open('x') as handle:
                json.dump({'purpose':'raw input before speech detection; diagnostic only, not enrollment or labeled evaluation',
                    'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'frames':self.input_frames},handle)
            return path
        finally:
            self.input_pcm.clear()
            self.input_frames.clear()

    def save(self,capture):
        path = self.root/(capture.capture_id+".wav")
        write_wav(path,capture.pcm)
        item = {"id":capture.capture_id,"file":path.name,"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
                "session":capture.session_id,"device":capture.device,"condition":"unlabeled",
                "split":"unreviewed","segments":[],"resolvable":None,"overlap":None,
                "capture_evidence":{"motion_risk":capture.motion_risk,"echo_risk":capture.echo_risk,"truncated":capture.truncated,"ended_by":capture.ended_by,
                    "voiced_intervals":capture.voiced_intervals,"started_at":capture.started_at}}
        with (self.root/"unreviewed.jsonl").open("a") as handle:
            handle.write(json.dumps(item)+"\n")


def load_manifest(path):
    raw = path.read_bytes()
    if len(raw)>16*1024*1024:
        raise ValueError("ManifestTooLarge")
    data = json.loads(raw)
    if data.get("version")!=1 or not isinstance(data.get("clips"),list) or not data["clips"]:
        raise ValueError("InvalidManifest")
    if not isinstance(data.get("roster"),dict):
        raise ValueError("ExplicitPersonIdRosterRequired")
    ids, digests = set(),set()
    for item in data["clips"]:
        for key in ("id","file","sha256","session","device","condition","split"):
            if not isinstance(item.get(key),str) or not item[key]:
                raise ValueError("MissingClipField:"+key)
        if item["id"] in ids or item["sha256"] in digests:
            raise ValueError("DuplicateClipOrCrossSplitLeakage")
        ids.add(item["id"])
        digests.add(item["sha256"])
        if item["split"] not in {"enrollment","tuning","evaluation"}:
            raise ValueError("UnreviewedSplit")
        if type(item.get("resolvable")) is not bool or type(item.get("overlap")) is not bool:
            raise ValueError("IndependentResolvabilityAndOverlapLabelsRequired")
        source = path.parent/item["file"]
        if Path(item["file"]).is_absolute() or ".." in Path(item["file"]).parts or source.resolve()!=source.absolute():
            raise ValueError("ReplayFilesMustBeIndependentRelativeCopies")
        if hashlib.sha256(source.read_bytes()).hexdigest()!=item["sha256"]:
            raise ValueError("ReplayDigestMismatch")
        duration = len(read_wav(source))/32000
        if not isinstance(item.get("segments"),list):
            raise ValueError("IndependentSpeechIntervalsRequired")
        for seg in item["segments"]:
            a,b = seg.get("start"),seg.get("end")
            if any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)) or not 0<=a<b<=duration+.001:
                raise ValueError("InvalidLabeledInterval")
            if not isinstance(seg.get("speakers"),list) or not seg["speakers"] or any(p not in data["roster"] for p in seg["speakers"]):
                raise ValueError("UnknownExpectedPersonId")
    return data,hashlib.sha256(raw).hexdigest()


def segment(pcm,session_id,vad,silence=1.2,offset=0.,device="saved_audio",turn=None):
    """The same Endpoint, VAD and turn judge (Smart Turn, when on) as the live microphone, including final silence."""
    endpoint = Endpoint(session_id,silence,turn)
    vad.reset()
    padded = pcm + b"\0\0"*int((silence+.064)*16000)
    for n in range(0,len(padded),1024):
        block = padded[n:n+1024].ljust(1024,b"\0")
        at = offset+n/32000
        capture = endpoint.push(Frame(block,at,at+.032),vad.is_speech(block))
        if capture:
            yield replace(capture,device=device)


def oracle_capture(pcm,clip,session_id,offset):
    """Labeled timing isolates encoder errors; never called by production replay."""
    end = len(pcm)/32000
    intervals = tuple((s["start"],s["end"]) for s in clip["segments"])
    # Labels do NOT provide overlap or person identity to the inference path.
    return Capture(clip["id"],session_id,pcm,offset,offset+end,offset+end,
                   voiced_intervals=intervals,device=clip["device"])
