"""Independent Qwen adapter retaining per-token log-probability evidence."""
from pathlib import Path
import math
import threading
import time

from ..contracts import Capture, Cancellation, ProviderFailure, Transcript
from ..providers import Capability
from ..mlx_schedule import MLX_LOCK
from ..spelling import compile_table, correct


class QwenASR:
    def __init__(self, model_path: Path, trust_floor: float, corrections=None):
        self.model_path, self.trust_floor = model_path, trust_floor
        self.corrections = compile_table(corrections or {})
        self.model = None
        self.lock = threading.Lock()
        self.capability = Capability("asr", "mlx-audio", "batch transcription with quality evidence",
                                     "Qwen3-ASR-1.7B-8bit", ("mlx-audio", "mlx", "mlx-lm", "numpy"),
                                     (str(model_path), "Metal"))

    def start(self):
        if not (self.model_path / "config.json").is_file():
            raise ProviderFailure("ASRModelMissing")
        from mlx_audio.stt.utils import load_model
        with MLX_LOCK:   # Metal work shares one lock with the voice cloner.
            self.model = load_model(str(self.model_path))

    def cancel(self):
        pass  # The in-flight operation owns its token and checks each decoded token.

    def close(self):
        with self.lock:
            self.model = None

    def transcribe(self, capture: Capture, cancel: Cancellation) -> Transcript:
        import numpy as np
        import mlx.core as mx
        if capture.sample_rate != 16000 or len(capture.pcm) % 2:
            raise ProviderFailure("InvalidASRPCM")
        with self.lock, MLX_LOCK:
            cancel.check()
            if self.model is None:
                raise ProviderFailure("ASRNotStarted")
            audio = np.frombuffer(capture.pcm, dtype="<i2").astype(np.float32) / 32768
            tokens, probabilities = [], []
            deadline = time.monotonic() + 30
            # No lexical/past-Rex prompt: v2 showed context echo hallucinations.
            stream = self.model.stream_generate(audio, language="English", max_tokens=256,
                                                system_prompt=None)
            try:
                for token, logprobs in stream:
                    cancel.check()
                    if time.monotonic() > deadline:
                        raise ProviderFailure("ASRDeadline")
                    value = int(token)
                    tokens.append(value)
                    probability = float(logprobs[value])
                    if math.isfinite(probability):
                        probabilities.append(probability)
                mx.synchronize()
                text = self.model._tokenizer.decode(tokens).strip()
            finally:
                stream.close()
            mean = sum(probabilities) / len(probabilities) if probabilities else None
            trusted = (mean is not None and len(probabilities) == len(tokens) and
                       mean >= self.trust_floor and len(tokens) < 256 and not capture.truncated)
            fixed = correct(text, self.corrections)   # The owner's name spellings (October 26).
            return Transcript(fixed, mean, trusted, text if fixed != text else None)
