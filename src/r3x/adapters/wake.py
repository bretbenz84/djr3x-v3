"""Explicit off-program wake listener; V3-owned assets, no download/fallback."""
from pathlib import Path
import hashlib
import json
from .audio import resolve_device, selected_pcm
from ..contracts import ProviderFailure
from ..providers import Capability


class WakeListener:
    capability = Capability('wake_listener','openwakeword','off-program wake only',
        'wakeuprex',('openwakeword','onnxruntime','sounddevice','numpy'),
        ('selected microphone; 80ms frames',))

    def __init__(self, settings, root: Path):
        self.settings,self.root = settings,root
        self.stream = self.model = None
        self.consecutive = 0

    def start(self):
        import sounddevice as sd
        try:
            self.load_model()
            index = resolve_device(sd.query_devices(),self.settings.input_device_name,
                                   self.settings.input_device_index,'input')
            channels = 2 if self.settings.microphone_mode=='respeaker-flex' else 1
            self.stream = sd.RawInputStream(device=index,samplerate=16000,channels=channels,
                                            dtype='int16',blocksize=1280)
            self.stream.start()
            return self
        except BaseException:
            self.close()
            raise

    def load_model(self):
        """Explicit model-only entry point for saved/synthetic benchmarking."""
        from openwakeword.model import Model
        required = ('wakeuprex.onnx','melspectrogram.onnx','embedding_model.onnx')
        if any(not (self.root/name).is_file() or (self.root/name).resolve()!=self.root/name for name in required):
            raise ProviderFailure('WakeAssetsMissingOrRedirected')
        records = json.loads((self.root/'manifest.json').read_text())
        digests = {r['name']:r['sha256'] for r in records}
        if set(digests)!=set(required) or any(hashlib.sha256((self.root/name).read_bytes()).hexdigest()!=digests[name] for name in required):
            raise ProviderFailure('WakeAssetDigestMismatch')
        self.model = Model(wakeword_models=[str(self.root/'wakeuprex.onnx')],
            inference_framework='onnx',melspec_model_path=str(self.root/'melspectrogram.onnx'),
            embedding_model_path=str(self.root/'embedding_model.onnx'))

    def heard(self):
        import numpy as np
        if self.stream.read_available < 1280:
            return False
        raw,overflow = self.stream.read(1280)
        if overflow:
            self.consecutive = 0
            self.model.reset()
            return False
        channels = 2 if self.settings.microphone_mode=='respeaker-flex' else 1
        pcm = selected_pcm(raw,channels,self.settings.input_gain)
        scores = self.model.predict(np.frombuffer(pcm,dtype='<i2'))
        score = scores.get('wakeuprex')
        if score is None:
            raise ProviderFailure('WakeModelResultMissing')
        self.consecutive = self.consecutive + 1 if score >= .92 else 0
        return self.consecutive >= 4

    def close(self):
        if self.stream:
            self.stream.abort()
            self.stream.close()
            self.stream = None
        self.model = None
        self.consecutive = 0
