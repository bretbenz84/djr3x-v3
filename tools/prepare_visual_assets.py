"""Copy an allowlist of public v2 model assets; never import v2 or copy profiles."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from uuid import uuid4

ASSETS = {
    "rfdetr_nano": ("rfdetr/rf-detr-nano.pth", "future_object_load"),
    "scrfd_10g": ("insightface/models/buffalo_l/det_10g.onnx", "detector"),
    "arcface_r50": ("insightface/models/buffalo_l/w600k_r50.onnx", "recognizer"),
    "pose_lite": ("pose/pose_landmarker_lite.task", "future_pose_load"),
    "efficientdet_lite0": ("object_detection/efficientdet_lite0.tflite", "legacy_object_fallback"),
}


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()


def prepare(source, destination):
    source, destination = source.absolute(), destination.absolute()
    if source.resolve() != source or destination.resolve() != destination:
        raise ValueError("LinkedAssetDirectory")
    if destination == source or destination.is_relative_to(source):
        raise ValueError("DestinationMustBeIndependent")
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    records = []
    for name, (relative, role) in ASSETS.items():
        original = source / relative
        if original.resolve() != original or not original.is_file():
            raise ValueError("MissingOrLinkedAsset:" + name)
        expected = digest(original)
        target = destination / (name + original.suffix)
        if target.resolve() != target or (target.exists() and target.stat().st_nlink != 1):
            raise ValueError("SharedDestinationAsset")
        if target.exists():
            if digest(target) != expected:
                raise ValueError("ExistingAssetDiffers:" + name)
        else:
            temporary = destination / (uuid4().hex + ".copy")
            try:
                shutil.copyfile(original, temporary)
                if digest(temporary) != expected or digest(original) != expected:
                    raise ValueError("CopyDigestMismatch")
                temporary.chmod(0o600)
                temporary.rename(target)
            finally:
                temporary.unlink(missing_ok=True)
        records.append({"id":name,"role":role,"path":target.name,"source":str(original),
                        "sha256":expected,"bytes":target.stat().st_size})
    catalog = destination / "assets.json"
    if catalog.is_symlink():
        raise ValueError("LinkedCatalog")
    catalog.write_text(json.dumps({"version":1,"downloaded_bytes":0,"assets":records},indent=2)+"\n")
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="v2 assets/models directory")
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parents[1]/"local/models/visual-bench")
    args = parser.parse_args()
    print(json.dumps({"copied_or_verified":prepare(args.source,args.destination),"downloaded_bytes":0},indent=2))


if __name__ == "__main__":
    main()
