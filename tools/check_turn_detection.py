"""Smart Turn v3.2 on saved robot captures: how much sooner finished speech ends, and how often speech splits.

  prepare            download the pinned model into local/models/smart-turn (8.7 MB, checksum verified)
  saved [DIR ...]    re-cut saved captures (default: every local/recordings session) through Rex's own
                     Silero endpoint twice, without and with Smart Turn, at --silence (default: settings)
                     --words also transcribes each piece of a capture that splits differently
                     (pieces the recognizer marks unreliable, which Rex never answers, in brackets)

Real models on saved audio: nothing is played or recorded, and results stay in local/validation.
Each saved capture was one turn under the endpoint of its day (1.2 s in September), so its end is a
real end of speech and any pause inside it was bridged then. Echo and truncated captures are skipped.
"""
import argparse
import hashlib
import json
from pathlib import Path
import resource
import statistics
import sys
import time
import urllib.request
from uuid import uuid4

from r3x.adapters.audio import SileroVAD
from r3x.adapters.turn import MODEL_FILE, MODEL_SHA256, SmartTurn
from r3x.dataset import read_wav, segment
from r3x.settings import ScratchPaths, Settings


URL = ("https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/"
       "f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx")
BYTES = 8679182


def prepare(paths):
    path = paths.root/MODEL_FILE
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        temporary = path.with_name(uuid4().hex + ".download")
        try:
            with urllib.request.urlopen(URL, timeout=30) as response, temporary.open("xb") as handle:
                total = 0
                while chunk := response.read(1024*1024):
                    total += len(chunk)
                    if total > BYTES:
                        raise ValueError("ModelDownloadTooLarge")
                    handle.write(chunk)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != MODEL_SHA256:
                raise ValueError("ModelDigestMismatch")
            temporary.rename(path)
        finally:
            temporary.unlink(missing_ok=True)
    if path.resolve() != path or hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
        raise ValueError("ExistingModelDigestMismatch")
    print(json.dumps({"file": str(path), "source": URL, "sha256": MODEL_SHA256, "bytes": path.stat().st_size}))


def clips(folders):
    """(path, evidence) for each saved capture that ended on silence with no playback in it."""
    for folder in folders:
        manifest = folder/"unreviewed.jsonl"
        if not manifest.is_file():
            yield from ((p, {}) for p in sorted(folder.glob("*.wav")) if p.name != "input-diagnostic.wav")
            continue
        for line in manifest.read_text().splitlines():
            item = json.loads(line)
            evidence = item["capture_evidence"]
            if not (evidence["echo_risk"] or evidence["truncated"]):
                yield folder/item["file"], evidence


def saved(args, paths):
    settings = Settings.load(paths.root/"settings.toml")
    silence = args.silence or settings.end_silence_seconds
    folders = args.folders or sorted(p for p in (paths.root/"recordings").iterdir() if p.is_dir())
    vad, turn = SileroVAD(settings.vad_threshold), SmartTurn(paths.root/MODEL_FILE)
    asr = None
    vad.start()
    if args.words:   # Started first, as in Rex, so the turn model is not charged for shared libraries.
        from r3x.adapters.asr import QwenASR
        from r3x.contracts import Cancellation
        asr = QwenASR(paths.root/"models/qwen3-asr", settings.asr_trust_logprob, settings.transcript_corrections)
        asr.start()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    turn.start()
    memory_mb = (resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before) / 2**20
    checks = []
    def timed(pcm):
        began = time.perf_counter()
        probability = turn.finished(pcm)
        checks.append(time.perf_counter() - began)
        return probability
    rows = []
    for path, evidence in clips(folders):
        pcm = read_wav(path)
        plain = list(segment(pcm, "check", vad, silence))
        smart = list(segment(pcm, "check", vad, silence, turn=timed))
        if not plain or not smart:
            continue
        row = {"file": str(path.relative_to(paths.root)), "moving": bool(evidence.get("motion_risk")),
               "voiced": round(sum(b-a for a, b in evidence.get("voiced_intervals", ())), 2) or None,
               "pieces": [len(plain), len(smart)],
               "end_wait": [round(c.ended_at - c.speech_ended_at, 3) for c in (plain[-1], smart[-1])],
               "ended_by": smart[-1].ended_by}
        if asr and len(plain) != len(smart):
            # Rex never answers a piece the recognizer marks unreliable: shown in brackets.
            row["words"] = [[(lambda t: t.text if t.trusted else f"[{t.text}]")(asr.transcribe(c, Cancellation()))
                             for c in cut] for cut in (plain, smart)]
        rows.append(row)
    vad.close()
    turn.close()
    if asr:
        asr.close()
    finished = [r for r in rows if r["ended_by"] == "finished"]
    summary = {
        "evidence": "real Silero and Smart Turn models on saved robot captures", "silence": silence,
        "model_sha256": MODEL_SHA256, "captures": len(rows), "moving": sum(r["moving"] for r in rows),
        "ended_at_check": len(finished), "ended_at_check_moving": sum(r["moving"] for r in finished),
        "end_wait_mean": [round(statistics.mean(r["end_wait"][i] for r in rows), 3) for i in (0, 1)],
        "pieces": [sum(r["pieces"][i] for r in rows) for i in (0, 1)],
        "captures_split_more": sum(r["pieces"][1] > r["pieces"][0] for r in rows),
        "captures_split_less": sum(r["pieces"][1] < r["pieces"][0] for r in rows),
        "check_seconds": {"count": len(checks), "median": round(statistics.median(checks), 4),
                          "p90": round(sorted(checks)[int(.9*len(checks))], 4), "max": round(max(checks), 4)} if checks else None,
        "model_memory_mb": round(memory_mb, 1)}
    folder = paths.root/"validation"/f"turn-detection-{uuid4().hex[:8]}"
    folder.mkdir(mode=0o700)
    (folder/"captures.json").write_text(json.dumps(rows, indent=1))
    (folder/"summary.json").write_text(json.dumps(summary, indent=1))
    print(f"{summary['captures']} saved turns ({summary['moving']} while Rex moved), longest wait {silence} s")
    print(f"  ended at the 0.2 s check: {summary['ended_at_check']} ({summary['ended_at_check_moving']} while moving)")
    print(f"  mean wait after the last word: {summary['end_wait_mean'][0]} s without, {summary['end_wait_mean'][1]} s with Smart Turn")
    print(f"  pieces: {summary['pieces'][0]} without, {summary['pieces'][1]} with; "
          f"{summary['captures_split_more']} turns split more, {summary['captures_split_less']} less")
    if checks:
        c = summary["check_seconds"]
        print(f"  {c['count']} checks: median {c['median']*1000:.0f} ms, 1 in 10 over {c['p90']*1000:.0f} ms, "
              f"slowest {c['max']*1000:.0f} ms; model memory +{summary['model_memory_mb']} MB")
    for r in rows:
        if "words" in r:
            print(f"  {' | '.join(r['words'][0])}\n    -> {' | '.join(r['words'][1])}")
    print(f"Saved in {folder}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    again = commands.add_parser("saved")
    again.add_argument("folders", type=Path, nargs="*")
    again.add_argument("--silence", type=float, help="the longest wait (default: end_silence_seconds)")
    again.add_argument("--words", action="store_true", help="transcribe pieces that split differently")
    args = parser.parse_args()
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    if args.command == "prepare":
        return prepare(paths)
    if not (paths.root/MODEL_FILE).is_file():
        print("The Smart Turn model is missing: run  .venv/bin/python tools/check_turn_detection.py prepare")
        return 1
    return saved(args, paths)


if __name__ == "__main__":
    sys.exit(main())
