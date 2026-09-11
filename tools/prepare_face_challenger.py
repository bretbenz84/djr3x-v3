"""Explicit pinned EdgeFace-S download, separate from the live runtime."""
import hashlib
import json
from pathlib import Path
import urllib.request
from uuid import uuid4

URL = 'https://github.com/yakhyo/edgeface-onnx/releases/download/weights/edgeface_s_gamma_05.onnx'
SHA256 = 'b850767cf791bda585600b5c4c7d7432b2f998ccd862caae34ef1afa967d2e54'
SIZE = 14805514


def main():
    root=Path(__file__).resolve().parents[1]/'local/models/visual-bench'
    root.mkdir(parents=True,exist_ok=True)
    path=root/'edgeface_s_gamma_05.onnx'
    if root.resolve()!=root or path.resolve()!=path:
        raise ValueError('LinkedModelPath')
    downloaded=0
    if not path.exists():
        temporary=root/(uuid4().hex+'.download')
        try:
            digest=hashlib.sha256()
            with urllib.request.urlopen(URL,timeout=30) as response, temporary.open('xb') as stream:
                while chunk:=response.read(1048576):
                    downloaded+=len(chunk)
                    if downloaded>SIZE: raise ValueError('UnexpectedModelSize')
                    digest.update(chunk); stream.write(chunk)
            if downloaded!=SIZE or digest.hexdigest()!=SHA256:
                raise ValueError('ModelDigestMismatch')
            temporary.chmod(0o600); temporary.rename(path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.stat().st_nlink!=1 or path.stat().st_size!=SIZE or hashlib.sha256(path.read_bytes()).hexdigest()!=SHA256:
        raise ValueError('ExistingModelMismatch')
    record={'model':'EdgeFace-S gamma .5','path':path.name,'source':URL,'sha256':SHA256,
            'bytes':SIZE,'verification':'GitHub release API asset digest and exact size',
            'runtime_selected':False}
    manifest=root/'edgeface_s.json'
    if manifest.is_symlink(): raise ValueError('LinkedManifest')
    manifest.write_text(json.dumps(record,indent=2)+'\n')
    print(json.dumps({**record,'downloaded_bytes':downloaded},indent=2))


if __name__=='__main__': main()
