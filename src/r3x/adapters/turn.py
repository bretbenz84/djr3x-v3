"""Pinned Smart Turn v3.2: whether a pause in speech ends the speaker's turn, judged from the audio.

Owner request, October 26. Pipecat's open model (BSD-2-Clause): a Whisper Tiny encoder with a
linear head, about 8M parameters, int8 for CPU. It hears pitch and pace, not words, so "So, ..."
sounds unfinished and "Yes." finished. It runs after Silero once a pause reaches 0.2 s
(capture.TURN_CHECK_SECONDS). Evidence and limits: docs/TURN_DETECTION.md.
"""
import hashlib
from pathlib import Path
import threading

from ..contracts import ProviderFailure
from ..providers import Capability


# pipecat-ai/smart-turn-v3 revision f766f81d3cfd, smart-turn-v3.2-cpu.onnx (8,679,182 bytes).
MODEL_SHA256 = "2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f"
MODEL_FILE = "models/smart-turn/smart-turn-v3.2-cpu.onnx"   # Under the scratch root (local/).
SAMPLES = 8 * 16000   # The model hears the last 8 s; shorter speech is padded with silence in front.


def model_input(pcm: bytes):
    """The last 8 s of 16-bit PCM as floats, zeros in front (as the model was trained)."""
    import numpy as np
    audio = np.frombuffer(pcm, dtype="<i2")[-SAMPLES:].astype(np.float32) / 32768
    return np.pad(audio, (SAMPLES - len(audio), 0))


class SmartTurn:
    def __init__(self, path: Path):
        self.path, self.session, self.features = path, None, None
        self.lock = threading.Lock()
        self.capability = Capability("turn_detection", "Smart Turn v3.2 ONNX",
            "whether a pause ends the speaker's turn", "smart-turn-v3.2-cpu:" + MODEL_SHA256[:12],
            ("onnxruntime", "transformers", "numpy"), (str(path), "CPU:2 threads"))

    def start(self):
        if self.path.resolve() != self.path or not self.path.is_file():
            raise ProviderFailure("TurnModelMissingOrLinked")
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ProviderFailure("TurnModelDigestMismatch")
        import onnxruntime as ort
        from transformers import WhisperFeatureExtractor   # Already loaded by speech recognition.
        options = ort.SessionOptions()
        # 20 ms a check on the M2 with two threads, 36 ms with one (October 26).
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(str(self.path), sess_options=options, providers=["CPUExecutionProvider"])
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if ([i.name for i in inputs], inputs[0].shape[1:], [o.name for o in outputs]) != (["input_features"], [80, 800], ["logits"]):
            raise ProviderFailure("TurnModelContractMismatch")
        self.features = WhisperFeatureExtractor(chunk_length=8)
        self.session = session

    def cancel(self):
        pass   # One check takes tens of milliseconds; nothing to interrupt.

    def close(self):
        with self.lock:
            self.session = self.features = None

    def finished(self, pcm: bytes) -> float:
        """The probability that the speaker has finished, from the turn's audio so far."""
        with self.lock:
            if self.session is None:
                raise ProviderFailure("TurnDetectionNotStarted")
            import numpy as np
            features = self.features(model_input(pcm), sampling_rate=16000, return_tensors="np", padding="max_length",
                                     max_length=SAMPLES, truncation=True, do_normalize=True).input_features
            # The model's last layer is a sigmoid: its "logits" output is already a probability.
            result = self.session.run(None, {"input_features": features.astype(np.float32)})[0].reshape(-1)
        if len(result) != 1 or not 0. <= float(result[0]) <= 1.:
            raise ProviderFailure("InvalidTurnProbability")
        return float(result[0])
