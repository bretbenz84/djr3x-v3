"""Explicit paid-provider/saved-audio check. No microphone, camera, or playback.

Run manually: .venv/bin/python tools/check_providers.py
All utterances are synthetic. This is not room or speaker-identity acceptance.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import time
from uuid import uuid4
import wave

from r3x.adapters.asr import QwenASR
from r3x.adapters.hosted import ElevenLabsTTS, OpenAIBrain
from r3x.contracts import Cancellation, Capture, Snapshot
from r3x.providers import ProviderOwner
from r3x.settings import ScratchPaths, Secrets


def write_wav(path, pcm, rate):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(pcm)


def main():
    root = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    root.prepare()
    folder = root.root / "validation" / uuid4().hex
    folder.mkdir(parents=True)
    secrets = Secrets.load(root.root)
    asr = QwenASR(root.root / "models/qwen3-asr", -.35)
    brain = OpenAIBrain(secrets.openai_api_key)
    tts = ElevenLabsTTS(secrets.elevenlabs_api_key, secrets.elevenlabs_voice_id)
    owner = ProviderOwner((asr, brain, tts))
    evidence = {"evidence": "real providers on synthetic saved audio", "live_hardware": False,
                "started": time.time(), "models": [p.capability.model for p in owner.providers]}
    cancel = Cancellation()
    try:
        began = time.monotonic()
        if not owner.start():
            evidence["startup"] = [asdict(r) for r in owner.results]
            raise RuntimeError("ProviderStartupFailed")
        evidence["startup_seconds"] = time.monotonic() - began
        reference = "Hello Rex. I am going camping tomorrow. What should I bring?"
        began = time.monotonic()
        pcm = b"".join(tts.synthesize(reference, cancel))
        evidence["input_tts_seconds"] = time.monotonic() - began
        write_wav(folder / "synthetic-input.wav", pcm, 22050)
        import numpy as np
        from scipy.signal import resample_poly
        resampled = resample_poly(np.frombuffer(pcm, dtype="<i2").astype(np.float32), 320, 441)
        mono = np.clip(resampled, -32768, 32767).astype("<i2").tobytes()
        duration = len(mono) / 32000
        capture = Capture("synthetic", "provider-check", mono, 0, duration, duration)
        began = time.monotonic()
        transcript = asr.transcribe(capture, cancel)
        evidence["asr_seconds"] = time.monotonic() - began
        evidence["reference"] = reference
        evidence["transcript"] = asdict(transcript)
        evidence["input_sha256"] = hashlib.sha256(pcm).hexdigest()
        began = time.monotonic()
        reply = "".join(brain.reply(Snapshot("provider-check", "turn", 0,
            ({"role":"user", "content":transcript.text},)), cancel))
        evidence["model_seconds"] = time.monotonic() - began
        evidence["model_calls"] = 1
        evidence["reply"] = reply
        began = time.monotonic()
        speech = b"".join(tts.synthesize(reply, cancel))
        evidence["reply_tts_seconds"] = time.monotonic() - began
        write_wav(folder / "reply.wav", speech, 22050)
        evidence["rendered_seconds"] = len(speech) / 44100
        evidence["playback_completed"] = False
        evidence["passed"] = bool(transcript.text and transcript.trusted and reply and speech)
        evidence["peak_rss_bytes_macos"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception as exc:
        evidence["passed"] = False
        evidence["error_type"] = type(exc).__name__
        # Only our safe ProviderFailure codes are persisted, never raw provider text.
        from r3x.contracts import ProviderFailure
        if isinstance(exc, ProviderFailure):
            evidence["reason"] = str(exc)
    finally:
        owner.close()
        (folder / "evidence.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps({"passed": evidence["passed"], "evidence_path": str(folder / "evidence.json")}))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
