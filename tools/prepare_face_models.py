"""Prepare independently copied pinned runtime face assets from the offline comparison cache."""
import json
from pathlib import Path
import shutil
from uuid import uuid4
from r3x.adapters.face import DETECTOR_SHA, RECOGNIZER_SHA
from r3x.visual_evaluation import file_digest


def prepare(root):
    source = root / 'visual-bench'
    destination = root / 'faces'
    if destination.resolve()!=destination.absolute(): raise ValueError('LinkedFaceModelDirectory')
    destination.mkdir(parents=True,exist_ok=True)
    evidence = []
    for name,expected in (('scrfd_10g.onnx',DETECTOR_SHA),('edgeface_s_gamma_05.onnx',RECOGNIZER_SHA)):
        target = destination/name
        if target.resolve()!=target.absolute(): raise ValueError('LinkedFaceModelFile')
        if not target.exists():
            original = source/name
            if original.resolve()!=original.absolute() or not original.is_file() or file_digest(original)!=expected:
                raise ValueError('PrepareVisualAssetsAndFaceChallengerFirst: '+name)
            temporary = destination/(uuid4().hex+'.copy')
            try:
                shutil.copyfile(original,temporary)
                if file_digest(temporary)!=expected: raise ValueError('FaceModelCopyDigestMismatch')
                temporary.rename(target)
            finally: temporary.unlink(missing_ok=True)
        if target.stat().st_nlink!=1 or file_digest(target)!=expected: raise ValueError('FaceModelDigestOrIndependenceMismatch')
        evidence.append({'file':name,'sha256':expected,'copied_from':str(source/name),'bytes':target.stat().st_size})
    (destination/'provenance.json').write_text(json.dumps(evidence,indent=2))
    return evidence


if __name__=='__main__':
    print(json.dumps(prepare(Path(__file__).resolve().parents[1]/'local/models'),indent=2))
