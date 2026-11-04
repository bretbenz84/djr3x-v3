"""Prepare independently copied pinned runtime face assets from the offline comparison cache.

--landmarker PATH also copies the face landmarker that reads what faces show (the V2 asset:
assets/models/face/face_landmarker.task). Local copy, digest pinned; nothing is downloaded.
"""
import argparse
import json
from pathlib import Path
import shutil
from uuid import uuid4
from r3x.adapters.expression import LANDMARKER_SHA
from r3x.adapters.face import DETECTOR_SHA, RECOGNIZER_SHA
from r3x.visual_evaluation import file_digest


def prepare(root, landmarker=None):
    source = root / 'visual-bench'
    destination = root / 'faces'
    if destination.resolve()!=destination.absolute(): raise ValueError('LinkedFaceModelDirectory')
    destination.mkdir(parents=True,exist_ok=True)
    evidence = []
    files = [('scrfd_10g.onnx',DETECTOR_SHA,source/'scrfd_10g.onnx'),
             ('edgeface_s_gamma_05.onnx',RECOGNIZER_SHA,source/'edgeface_s_gamma_05.onnx')]
    if landmarker or (destination/'face_landmarker.task').exists():
        files.append(('face_landmarker.task',LANDMARKER_SHA,landmarker or destination/'face_landmarker.task'))
    for name,expected,original in files:
        target = destination/name
        if target.resolve()!=target.absolute(): raise ValueError('LinkedFaceModelFile')
        if not target.exists():
            if original.resolve()!=original.absolute() or not original.is_file() or file_digest(original)!=expected:
                raise ValueError('PrepareVisualAssetsAndFaceChallengerFirst: '+name)
            temporary = destination/(uuid4().hex+'.copy')
            try:
                shutil.copyfile(original,temporary)
                if file_digest(temporary)!=expected: raise ValueError('FaceModelCopyDigestMismatch')
                temporary.rename(target)
            finally: temporary.unlink(missing_ok=True)
        if target.stat().st_nlink!=1 or file_digest(target)!=expected: raise ValueError('FaceModelDigestOrIndependenceMismatch')
        evidence.append({'file':name,'sha256':expected,'copied_from':str(original),'bytes':target.stat().st_size})
    (destination/'provenance.json').write_text(json.dumps(evidence,indent=2))
    return evidence


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--landmarker', type=Path, help='Existing face_landmarker.task to copy in')
    print(json.dumps(prepare(Path(__file__).resolve().parents[1]/'local/models',parser.parse_args().landmarker),indent=2))
