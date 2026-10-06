"""Prepare the pinned Breeze TTS 2 8-bit snapshot for cloned-voice impressions.

Copies an existing local snapshot (`--source DIR`, an independent copy with symlinks
resolved) or downloads the pinned revision from Hugging Face into
local/models/breeze-tts-8bit, verifies the files the engine needs, hashes the weights
and writes provenance.json. No model loads and no device opens. The weights stay
ignored; the research/non-commercial license and notice files are copied with them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
from uuid import uuid4

from r3x.adapters.breeze import MODEL_ID, MODEL_REVISION, REQUIRED_FILES, WEIGHT_BYTES, WEIGHT_SHA256
from r3x.settings import ScratchPaths

COPIED_FILES = REQUIRED_FILES + ('model.safetensors.index.json', 'generation_config.json', 'special_tokens_map.json',
                                 'audio_tokenizer/configuration.json', 'audio_tokenizer/preprocessor_config.json',
                                 'LICENSE', 'NOTICE', 'README.md')


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as handle:
        while chunk := handle.read(16 * 1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def copy_snapshot(source: Path, destination: Path) -> None:
    for name in COPIED_FILES:
        original = (source / name).resolve()
        if not original.is_file():
            if name in REQUIRED_FILES:
                raise ValueError('SourceSnapshotIncomplete:' + name)
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)   # Resolves links: an independent copy, never a shared file.


def download_snapshot(destination: Path) -> str:
    from huggingface_hub import snapshot_download
    snapshot_download(MODEL_ID, revision=MODEL_REVISION, local_dir=str(destination),
                      allow_patterns=list(COPIED_FILES))
    return 'huggingface:' + MODEL_ID + '@' + MODEL_REVISION


def verify(destination: Path) -> dict:
    digests = {}
    for name in REQUIRED_FILES:
        path = destination / name
        if path.resolve() != path or not path.is_file():
            raise ValueError('PreparedSnapshotIncomplete:' + name)
        if name in WEIGHT_BYTES and path.stat().st_size != WEIGHT_BYTES[name]:
            raise ValueError('WeightSizeMismatch:' + name)
    for name, expected in WEIGHT_SHA256.items():
        digests[name] = digest(destination / name)
        if digests[name] != expected:
            raise ValueError('WeightDigestMismatch:' + name + ':' + digests[name])
    digests['config.json'] = digest(destination / 'config.json')
    return digests


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, help='Existing local snapshot directory to copy instead of downloading')
    args = parser.parse_args(argv)
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    root = paths.root / 'models'
    root.mkdir(exist_ok=True, mode=0o700)
    destination = root / 'breeze-tts-8bit'
    if destination.resolve() != destination:
        raise ValueError('ModelDirectoryLinked')
    staged = None
    source_note = None
    if not destination.is_dir():
        staged = root / ('breeze-tts-8bit.' + uuid4().hex + '.staging')
        staged.mkdir(mode=0o700)
        try:
            if args.source:
                copy_snapshot(args.source.resolve(), staged)
                source_note = 'copied_from:' + str(args.source.resolve())
            else:
                source_note = download_snapshot(staged)
            staged.rename(destination)
        except BaseException:
            shutil.rmtree(staged, ignore_errors=True)
            raise
    digests = verify(destination)
    provenance = {'model': MODEL_ID, 'revision': MODEL_REVISION, 'source': source_note or 'existing_directory',
                  'sha256': digests, 'bytes': {name: (destination / name).stat().st_size for name in REQUIRED_FILES},
                  'license': 'BreezeBlue research and non-commercial license (see LICENSE/NOTICE in the snapshot)'}
    (destination / 'provenance.json').write_text(json.dumps(provenance, indent=2))
    print(json.dumps({'prepared': str(destination), 'source': provenance['source'], 'verified': sorted(digests),
                      'devices_opened': False, 'model_loaded': False}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(json.dumps({'outcome': 'failed', 'reason': str(exc)}), file=sys.stderr)
        raise SystemExit(1)
