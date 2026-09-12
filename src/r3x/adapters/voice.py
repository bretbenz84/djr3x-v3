"""V3-owned pinned CAM++ encoder; no device access or model downloads."""
import hashlib
from pathlib import Path
import threading

from ..contracts import Capture, Cancellation, ProviderFailure
from ..providers import Capability
from ..speakers import VoiceObservation, centroid, window_consistency


MODEL_SHA256 = "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2"
NAMESPACE = "campplus:aa3cfc16963a:16k-fbank80-globalmean:192:v1"


class CAMPlus:
    def __init__(self, path: Path):
        self.path, self.session = path, None
        self.lock = threading.Lock()
        self.capability = Capability("voice_encoder", "CAM++ ONNX", "acoustic tracks and enrollment",
            NAMESPACE, ("onnxruntime", "torch", "torchaudio", "numpy"), (str(path), "CPU:2 threads"))

    def start(self):
        import onnxruntime as ort
        if self.path.resolve() != self.path or not self.path.is_file():
            raise ProviderFailure("VoiceModelMissingOrLinked")
        if hashlib.sha256(self.path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ProviderFailure("VoiceModelDigestMismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
        self.session = ort.InferenceSession(str(self.path), sess_options=options, providers=["CPUExecutionProvider"])
        meta = self.session.get_modelmeta().custom_metadata_map
        if any(meta.get(k) != v for k, v in {"output_dim":"192", "normalize_samples":"1",
               "sample_rate":"16000", "feature_normalize_type":"global-mean"}.items()):
            raise ProviderFailure("VoiceFeatureContractMismatch")

    def cancel(self):
        pass

    def close(self):
        with self.lock:
            self.session = None

    def embed(self, pcm: bytes, cancel: Cancellation):
        import numpy as np
        import torch
        from torchaudio.compliance.kaldi import fbank
        cancel.check()
        wav = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        n = len(wav)//160
        if n < 20:
            return None
        rms = np.sqrt(np.mean(wav[:n*160].reshape(n,160)**2, axis=1))
        active = np.flatnonzero(rms > max(.001, float(rms.max())*.05))
        if not len(active):
            return None
        wav = wav[max(0,int(active[0]*160)-960):min(len(wav),int((active[-1]+1)*160)+960)]
        if len(wav) < 3200:
            return None
        with torch.inference_mode():
            features = fbank(torch.from_numpy(wav.copy()).unsqueeze(0), num_mel_bins=80,
                             sample_frequency=16000, dither=0.)
            features -= features.mean(dim=0, keepdim=True)
            array = features.unsqueeze(0).numpy()
        with self.lock:
            cancel.check()
            if self.session is None:
                raise ProviderFailure("VoiceEncoderNotStarted")
            result = self.session.run(["embedding"], {"x":array})[0].reshape(-1)
        norm = float(np.linalg.norm(result))
        if len(result) != 192 or not np.isfinite(result).all() or norm <= 1e-10:
            raise ProviderFailure("InvalidVoiceEmbedding")
        return tuple(float(v)/norm for v in result)

    def analyze(self, capture: Capture, cancel: Cancellation) -> VoiceObservation:
        if capture.sample_rate != 16000 or len(capture.pcm) % 2:
            raise ProviderFailure("VoicePCMContract")
        if capture.echo_risk or capture.overlap is True:
            return VoiceObservation(NAMESPACE, mixed=capture.overlap is True, reason="echo_or_labeled_overlap")
        intervals = capture.voiced_intervals
        voiced = sum(end-start for start, end in intervals)
        if not intervals:
            return VoiceObservation(NAMESPACE, reason="voiced_intervals_missing")
        begin, end = intervals[0][0], intervals[-1][1]
        length = end-begin
        starts = [begin] if length <= 2 else [begin + i*(length-2)/min(7, max(1, int(length-1)))
                                            for i in range(min(7, max(1, int(length-1)))+1)]
        windows = []
        for start in starts:
            finish = min(start+2., end)
            # Never use an almost-silent sliding window as change evidence.
            window_voiced = sum(max(0., min(finish,b)-max(start,a)) for a,b in intervals)
            if window_voiced < .3:
                continue
            embedding = self.embed(capture.pcm[int(start*16000)*2:int(finish*16000)*2], cancel)
            if embedding:
                windows.append((start, finish, embedding))
        if not windows:
            return VoiceObservation(NAMESPACE, voiced_seconds=voiced, reason="no_usable_windows")
        similarity, change = window_consistency(windows)
        return VoiceObservation(NAMESPACE, centroid([w[2] for w in windows]), tuple(windows), voiced,
                                similarity, mixed=change,
                                reason="coherent_window_groups" if change else "pooled_window_consistency")
