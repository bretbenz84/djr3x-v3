"""Install the verified RF-DETR Nano export into V3; never load models at startup implicitly.

Usage: .venv/bin/python tools/prepare_object_models.py --onnx /path/to/rfdetr-nano.onnx
For export provenance and a reproducible offline recipe see docs/OBJECT_PHOTO_MEMORY.md.
"""
import argparse
from hashlib import sha256
from pathlib import Path
import shutil
from r3x.adapters.objects import DIGEST


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--onnx',type=Path,required=True)
    args=parser.parse_args()
    source=args.onnx.resolve(strict=True)
    if sha256(source.read_bytes()).hexdigest()!=DIGEST:raise SystemExit('Object export digest mismatch')
    target=Path(__file__).resolve().parents[1]/'local/models/objects/rfdetr-nano.onnx'
    if target.resolve()!=target:raise SystemExit('Independent V3 model path required')
    target.parent.mkdir(parents=True,exist_ok=True)
    if source!=target:shutil.copyfile(source,target)
    print(target)

if __name__=='__main__':main()
