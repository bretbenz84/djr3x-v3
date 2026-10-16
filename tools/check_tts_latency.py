"""Explicit paid comparison of ElevenLabs TTS models on Rex's production request path.

Run manually: .venv/bin/python tools/check_tts_latency.py [--rounds 3]
Uses the live ElevenLabsTTS adapter (same /stream endpoint, pcm_22050, voice settings and
seed) with only the model ID changed. No microphone, playback or hardware. Text is synthetic.
Measures request-to-first-PCM-chunk and full synthesis time on one warm connection, as in
production; the V2 leveler is not attached, so its pre-roll is excluded for both models.
Evidence and WAVs go to local/validation/<id>/ for listening.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import statistics
import time
from uuid import uuid4
import wave

from r3x.adapters.hosted import ElevenLabsTTS
from r3x.contracts import Cancellation, ProviderFailure
from r3x.settings import ScratchPaths, Secrets

MODELS = ("eleven_v3_conversational", "eleven_v4_turbo")
LINES = (
    "Oh, hey!",
    "Camping? Bring a flashlight. Trust me, the forest gets weird after dark.",
    "I used to pilot Star Tours, you know. Then they gave me a microphone instead. Best demotion ever.",
    "Sacramento's looking sunny today, around seventy-five. Perfect weather for a droid to overheat in style.",
    "Wait, wait. You're telling me you've never heard the Endor Express? Okay, sit down, this changes everything.",
)


def take(tts, text):
    cancel = Cancellation()
    began = time.monotonic()
    first, chunks = None, []
    for chunk in tts.synthesize(text, cancel):
        if first is None:
            first = time.monotonic() - began
        chunks.append(chunk)
    pcm = b"".join(chunks)
    return {"first_audio_s": first, "total_s": time.monotonic() - began,
            "audio_s": len(pcm) / (2 * tts.sample_rate), "chars": len(text)}, pcm


def summary(values):
    ordered = sorted(values)
    return {"median": statistics.median(ordered), "p90": ordered[min(len(ordered) - 1, int(.9 * len(ordered)))],
            "min": ordered[0], "max": ordered[-1], "n": len(ordered)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=3)
    rounds = parser.parse_args().rounds
    root = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    root.prepare()
    secrets = Secrets.load(root.root)
    if not (secrets.elevenlabs_api_key and secrets.elevenlabs_voice_id):
        raise SystemExit("ElevenLabs key and voice ID are required (local/secrets.toml or environment).")
    folder = root.root / "validation" / ("tts-latency-" + uuid4().hex[:12])
    folder.mkdir(parents=True)
    providers = {}
    for model in MODELS:
        tts = ElevenLabsTTS(secrets.elevenlabs_api_key, secrets.elevenlabs_voice_id)
        tts.capability = replace(tts.capability, model=model)
        tts.start()
        providers[model] = tts
    evidence = {"evidence": "hosted ElevenLabs on synthetic text, no playback", "live_hardware": False,
                "started": time.time(), "models": list(MODELS), "rounds": rounds, "takes": []}
    try:
        for model, tts in providers.items():   # Warm each connection; production keeps one open.
            evidence.setdefault("warmup", {})[model] = take(tts, "Hello.")[0]
        for index in range(rounds):
            for number, text in enumerate(LINES):
                order = MODELS if (index + number) % 2 == 0 else MODELS[::-1]
                for model in order:
                    try:
                        result, pcm = take(providers[model], text)
                    except ProviderFailure as exc:
                        evidence["takes"].append({"model": model, "line": number, "round": index, "error": str(exc)})
                        continue
                    evidence["takes"].append({"model": model, "line": number, "round": index, **result})
                    if index == 0:
                        with wave.open(str(folder / f"{model}-line{number}.wav"), "wb") as handle:
                            handle.setnchannels(1)
                            handle.setsampwidth(2)
                            handle.setframerate(providers[model].sample_rate)
                            handle.writeframes(pcm)
        evidence["summary"] = {}
        for model in MODELS:
            done = [t for t in evidence["takes"] if t["model"] == model and "error" not in t]
            evidence["summary"][model] = {
                "first_audio_s": summary([t["first_audio_s"] for t in done]) if done else None,
                "total_s": summary([t["total_s"] for t in done]) if done else None,
                "characters": sum(t["chars"] for t in done),
                "errors": sum(1 for t in evidence["takes"] if t["model"] == model and "error" in t)}
    finally:
        for tts in providers.values():
            tts.close()
        (folder / "evidence.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps({"summary": evidence.get("summary"), "evidence_path": str(folder / "evidence.json")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
