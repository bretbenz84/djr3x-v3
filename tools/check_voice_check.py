"""CED-Tiny voice check on saved robot captures: which transcripts Rex would keep but not answer.

  prepare            download the pinned model into local/models/ced-tiny (28.5 MB archive, checksums verified)
  saved [DIR ...]    score every saved capture (default: every local/recordings session) with Rex's own
                     VoiceCheck and apply his rule: a trusted transcript of a brief sound (under
                     VOICE_CHECK_MAX_VOICED of detected voice), not a stop or shutdown, scoring under
                     NOT_SPEECH_SCORE is held back. --labels FILE (JSON, capture id -> label) counts by label.

Real model on saved audio: nothing is played or recorded. Evidence and limits: docs/VOICE_CHECK.md.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tarfile
import time
import urllib.request
from uuid import uuid4

from r3x.adapters.voice_check import MODEL_FILE, MODEL_SHA256, VoiceCheck
from r3x.dataset import read_wav
from r3x.runtime import NOT_SPEECH_SCORE, SHUTDOWN_COMMANDS, VOICE_CHECK_MAX_VOICED
from r3x.session import is_stop_command
from r3x.settings import ScratchPaths


URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/audio-tagging-models/"
       "sherpa-onnx-ced-tiny-audio-tagging-2024-04-19.tar.bz2")
ARCHIVE_SHA256 = "84baf315b57d61aa69480c4fee878dab54cbc7be3e877db334e65d8b087e23c3"
ARCHIVE_BYTES = 28531989
MEMBER = "sherpa-onnx-ced-tiny-audio-tagging-2024-04-19/model.onnx"


def prepare(paths):
    path = paths.root/MODEL_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        archive = path.with_name(uuid4().hex + ".download")
        temporary = path.with_name(uuid4().hex + ".onnx")
        try:
            with urllib.request.urlopen(URL, timeout=60) as response, archive.open("xb") as handle:
                total = 0
                while chunk := response.read(1024*1024):
                    total += len(chunk)
                    if total > ARCHIVE_BYTES:
                        raise ValueError("ModelDownloadTooLarge")
                    handle.write(chunk)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != ARCHIVE_SHA256:
                raise ValueError("ArchiveDigestMismatch")
            with tarfile.open(archive, "r:bz2") as tar, temporary.open("xb") as handle:
                handle.write(tar.extractfile(tar.getmember(MEMBER)).read())   # The one file, by name.
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA256:
                raise ValueError("ModelDigestMismatch")
            temporary.rename(path)
        finally:
            archive.unlink(missing_ok=True)
            temporary.unlink(missing_ok=True)
    if path.resolve() != path or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("ExistingModelDigestMismatch")
    print(json.dumps({"file": str(path), "source": URL, "sha256": MODEL_SHA256, "bytes": path.stat().st_size}))


def captures(paths, folders):
    """(wav, transcript event, capture event) for each saved capture with a transcript, echo excluded."""
    for folder in folders:
        log = paths.root/"sessions"/(folder.name + ".jsonl")
        if not log.is_file():
            continue
        events = [json.loads(line) for line in log.read_text().splitlines() if '"capture' in line or '"transcript"' in line]
        found = {e["capture_id"]: e for e in events if e["event"] == "capture"}
        for e in events:
            wav = folder/(e.get("capture_id", "") + ".wav")
            if e["event"] == "transcript" and wav.is_file() and not found.get(e["capture_id"], {}).get("echo_risk"):
                yield wav, e, found.get(e["capture_id"], {})


def saved(args, paths):
    folders = args.folders or sorted(p for p in (paths.root/"recordings").iterdir() if p.is_dir())
    labels = json.loads(args.labels.read_text()) if args.labels else {}
    check = VoiceCheck(paths.root/MODEL_FILE)
    check.start()
    held, kept, seconds = defaultdict(list), Counter(), []
    try:
        for wav, transcript, capture in captures(paths, folders):
            text = transcript["original_text"]
            began = time.perf_counter()
            score = check.voice(read_wav(wav))
            seconds.append(time.perf_counter() - began)
            label = labels.get(transcript["capture_id"], "unlabelled")
            brief = (capture.get("vad_voiced_seconds") or 0.) < VOICE_CHECK_MAX_VOICED
            exempt = is_stop_command(text) or text.strip().lower().rstrip(".!?") in SHUTDOWN_COMMANDS
            if transcript["trusted"] and brief and not exempt and score < NOT_SPEECH_SCORE:
                held[label].append((text, round(score, 3)))
            elif transcript["trusted"]:
                kept[label] += 1
    finally:
        check.close()
    print(json.dumps({"evidence": "real_model_saved_audio", "captures": len(seconds),
                      "median_ms": round(1000*statistics.median(seconds), 1), "slowest_ms": round(1000*max(seconds), 1),
                      "held_back_by_label": {k: len(v) for k, v in held.items()},
                      "answered_by_label": dict(kept), "held_back": held}, indent=1))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    again = commands.add_parser("saved")
    again.add_argument("folders", type=Path, nargs="*")
    again.add_argument("--labels", type=Path, help="JSON object: capture id -> label (e.g. the owner's listening)")
    args = parser.parse_args()
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    if args.command == "prepare":
        return prepare(paths)
    if not (paths.root/MODEL_FILE).is_file():
        print("The voice check model is missing: run  .venv/bin/python tools/check_voice_check.py prepare")
        return 1
    return saved(args, paths)


if __name__ == "__main__":
    sys.exit(main())
