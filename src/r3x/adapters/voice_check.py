"""Pinned CED-Tiny: whether a brief sound holds a human voice, as a second opinion beside Silero.

Owner, October 26: Rex's own motors, coughs, Max and the TV were transcribed as "Oh."/"Okay.".
On the 172 saved September captures with the owner's labels, CED-Tiny (Xiaomi, AudioSet 48.1 mAP,
5.5M parameters, Apache-2.0 weights) scored all 43 motor sounds below 0.5 and his real stops at
0.59-0.77; YAMNet put a motor sound at 0.94. Evidence and limits: docs/VOICE_CHECK.md.
"""
import hashlib
from pathlib import Path
import threading

from ..contracts import ProviderFailure
from ..providers import Capability


# k2-fsa/sherpa-onnx release audio-tagging-models, sherpa-onnx-ced-tiny-audio-tagging-2024-04-19,
# model.onnx (22,256,662 bytes), exported from RicherMans/CED.
MODEL_SHA256 = "afe72c1243ec84157d12231e4ea15152a7b438273f6eb06d260fac3e4dd2a4df"
MODEL_FILE = "models/ced-tiny/model.onnx"   # Under the scratch root (local/).
VOICE_CLASSES = (0, 4, 5)   # AudioSet "Speech", "Conversation", "Narration, monologue".
CLASSES = 527
MIN_SAMPLES = 16000         # Shorter audio is padded with silence to one second.
MAX_SAMPLES = 30 * 16000    # The export takes at most 3,000 frames (30 s).


class VoiceCheck:
    def __init__(self, path: Path):
        self.path, self.session, self.front_end = path, None, None
        self.lock = threading.Lock()
        self.capability = Capability("voice_check", "CED-Tiny ONNX",
            "whether a brief sound holds a human voice; household sounds in the room", "ced-tiny:" + MODEL_SHA256[:12],
            ("onnxruntime", "torch", "torchaudio", "numpy"), (str(path), "CPU:2 threads"), required=False)

    def start(self):
        if self.path.resolve() != self.path or not self.path.is_file():
            raise ProviderFailure("VoiceCheckModelMissingOrLinked")
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ProviderFailure("VoiceCheckModelDigestMismatch")
        import onnxruntime as ort
        import torch
        import torchaudio   # Already loaded by Silero and the voice encoder.
        options = ort.SessionOptions()
        # 8.5 ms median for a 1.6-4 s capture on the M2 with two threads (October 26).
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        session = ort.InferenceSession(str(self.path), sess_options=options, providers=["CPUExecutionProvider"])
        inputs, outputs = session.get_inputs(), session.get_outputs()
        if ([i.name for i in inputs], inputs[0].shape[1], [o.name for o in outputs], outputs[0].shape[1]) != (["feats"], 64, ["prob"], CLASSES):
            raise ProviderFailure("VoiceCheckModelContractMismatch")
        # CED's own front end (RicherMans/CED models/audiotransformer.py): 64 mel bands to 8 kHz, decibels.
        mel = torchaudio.transforms.MelSpectrogram(sample_rate=16000, n_fft=512, win_length=512, hop_length=160,
                                                   f_min=0, f_max=8000, n_mels=64, center=True)
        decibels = torchaudio.transforms.AmplitudeToDB(top_db=120)
        def front_end(audio):
            with torch.no_grad():
                return decibels(mel(torch.from_numpy(audio)[None])).numpy()
        self.front_end, self.session = front_end, session

    def cancel(self):
        pass   # One check takes milliseconds; nothing to interrupt.

    def close(self):
        with self.lock:
            self.session = self.front_end = None

    def tags(self, pcm: bytes):
        """All 527 AudioSet scores for the sound, each 0 to 1 (room_sounds.py reads household sounds)."""
        with self.lock:
            if self.session is None:
                raise ProviderFailure("VoiceCheckNotStarted")
            import numpy as np
            audio = np.frombuffer(pcm, dtype="<i2")[:MAX_SAMPLES].astype(np.float32) / 32768
            audio = np.pad(audio, (0, max(0, MIN_SAMPLES - len(audio))))
            result = self.session.run(None, {"feats": self.front_end(audio).astype(np.float32)})[0].reshape(-1)
        if len(result) != CLASSES or not np.all((result >= 0.) & (result <= 1.)):
            raise ProviderFailure("InvalidVoiceProbability")
        return result

    def voice(self, pcm: bytes) -> float:
        """How sure the model is that the sound holds a human voice, 0 to 1."""
        result = self.tags(pcm)
        return float(max(result[i] for i in VOICE_CLASSES))
