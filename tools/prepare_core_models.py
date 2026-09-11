"""Explicit checksum-verified public model preparation; no inference or devices."""
import hashlib
import json
from pathlib import Path
import urllib.request
from uuid import uuid4

from r3x.adapters.voice import MODEL_SHA256
from r3x.settings import ScratchPaths


ASSETS=(
    ("campplus.onnx","https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx",MODEL_SHA256),
)


def main():
    paths=ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    root=paths.root/"models"
    if root.resolve()!=root:raise ValueError("ModelDirectoryLinked")
    root.mkdir(exist_ok=True,mode=0o700)
    evidence=[]
    for name,url,expected in ASSETS:
        path=root/name
        if path.resolve()!=path:raise ValueError("ModelFileLinked")
        if not path.exists():
            temporary=root/(uuid4().hex+".download")
            try:
                with urllib.request.urlopen(url,timeout=30) as response,temporary.open("xb") as handle:
                    total=0
                    while chunk:=response.read(1024*1024):
                        total+=len(chunk)
                        if total>50*1024*1024:raise ValueError("ModelDownloadTooLarge")
                        handle.write(chunk)
                if hashlib.sha256(temporary.read_bytes()).hexdigest()!=expected:raise ValueError("ModelDigestMismatch")
                temporary.rename(path)
            finally:
                temporary.unlink(missing_ok=True)
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError("ExistingModelDigestMismatch")
        evidence.append({"file":name,"source":url,"sha256":expected,"bytes":path.stat().st_size})
    (paths.root/"core-models.json").write_text(json.dumps(evidence,indent=2))
    print(json.dumps({"verified_models":[a["file"] for a in evidence],"devices_opened":False}))


if __name__=="__main__":
    main()
