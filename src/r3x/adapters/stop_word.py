"""A spoken "stop" heard straight from the sound while the wheels run (owner, October 25).

Owner after run a0afcf85: "I told you to stop many times, and you did not stop", "this has been an
ongoing problem" (also October 16 and 19). While the wheels ran, the speech detector rarely passed
.5 and the recognizer heard a quiet "stop" as "Oh." or "Ah."; this listens to the sound itself,
beside both. The owner downloaded `stop.onnx` from the openwakeword.com library (model 1111); it runs
on openWakeWord's own feature models, the same local copies the supervisor's wake word uses, each
checked against `local/models/wake/manifest.json`. Nothing loads at import; `start` loads, `close`
lets go. A detection is evidence for the stop fast path only, never a turn or a transcript.
"""
import hashlib
import json
from pathlib import Path

from ..contracts import ProviderFailure
from ..providers import Capability

CHUNK = 1280          # openWakeWord scores 80 ms of 16 kHz audio at a time.
FILES = ('stop.onnx', 'melspectrogram.onnx', 'embedding_model.onnx')
# tools/check_stop_hearing.py on the September 27 robot audio: at .7 for two running frames it fired
# on none of 124 other utterances and 313 two-second stretches of drive and room noise, and on 6 of
# 60 real stops mixed into drive noise ("Oh, stop!" at .95; the owner's plain "Stop." only reached
# .04-.40). About 1.6 ms of CPU per 80 ms frame.
THRESHOLD = .7
FRAMES = 2            # Running 80 ms frames at or above it.


class StopSpotter:
    def __init__(self, folder: Path, threshold: float = THRESHOLD, frames: int = FRAMES):
        self.folder, self.threshold, self.frames = Path(folder), threshold, frames
        self.model = None
        self.key = None
        self._pending = b''
        self._run = 0
        self.last_score = None
        self.capability = Capability('stop_word', 'openwakeword', 'spoken stop while the wheels run',
                                     'stop.onnx (openwakeword.com library, model 1111)',
                                     ('openwakeword', 'onnxruntime', 'numpy'), (str(self.folder), 'CPU'),
                                     required=False)

    def start(self):
        try:
            records = {r['name']: r['sha256'] for r in json.loads((self.folder / 'manifest.json').read_text())}
        except (OSError, ValueError, KeyError, TypeError):
            raise ProviderFailure('StopWordManifestUnreadable')
        for name in FILES:
            path = self.folder / name
            if name not in records or not path.is_file():
                raise ProviderFailure('StopWordModelMissing')
            if hashlib.sha256(path.read_bytes()).hexdigest() != records[name]:
                raise ProviderFailure('StopWordModelDigestMismatch')
        from openwakeword.model import Model
        self.model = Model(wakeword_models=[str(self.folder / 'stop.onnx')], inference_framework='onnx',
                           melspec_model_path=str(self.folder / 'melspectrogram.onnx'),
                           embedding_model_path=str(self.folder / 'embedding_model.onnx'))
        self.key = next(iter(self.model.models))

    def feed(self, pcm: bytes):
        """16-bit mono PCM at 16 kHz. Returns the score when this sound first holds a stop for
        `frames` running 80 ms frames, else None; once it fires it waits for the score to fall."""
        if self.model is None:
            raise ProviderFailure('StopWordNotStarted')
        import numpy as np
        self._pending += pcm
        fired = None
        while len(self._pending) >= 2 * CHUNK:
            chunk, self._pending = self._pending[:2 * CHUNK], self._pending[2 * CHUNK:]
            self.last_score = float(self.model.predict(np.frombuffer(chunk, dtype='<i2'))[self.key])
            if self.last_score >= self.threshold:
                self._run += 1
                if self._run == self.frames:
                    fired = self.last_score
            else:
                self._run = 0
        return fired

    def reset(self):
        if self.model is not None:
            self.model.reset()
        self._pending, self._run = b'', 0

    def cancel(self):
        pass

    def close(self):
        self.model = None
