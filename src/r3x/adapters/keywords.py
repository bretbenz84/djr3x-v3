"""Spoken keywords heard straight from the sound: "stop" while the wheels run, "Hey Rex" or "Hey
DJ Rex" over his own voice.

Owner, October 25. After run a0afcf85 ("I told you to stop many times... an ongoing problem") he
downloaded `stop.onnx` from the openwakeword.com library (model 1111); the speech detector rarely
passed .5 while the wheels ran and the recognizer heard a faint "stop" as "Oh." or "Ah.". Then:
"Hey DJ Rex" or "Hey Rex" should interrupt his speech so the owner can talk over him, with V2's
`Hey_DJ_Rex.onnx` and `Hey_rex.onnx` (V2 assets, HEAD 95f2403). All run on openWakeWord's own
feature models in one model, so one pass over the sound scores every word; each file present is
checked against `local/models/wake/manifest.json`. Nothing loads at import; `start` loads, `close`
lets go. A detection is evidence for a fast path only, never a turn or a transcript.
"""
import hashlib
import json
from pathlib import Path

from ..contracts import ProviderFailure
from ..providers import Capability

CHUNK = 1280          # openWakeWord scores 80 ms of 16 kHz audio at a time.
FEATURES = ('melspectrogram.onnx', 'embedding_model.onnx')
# Word -> (score, running 80 ms frames). tools/check_stop_hearing.py on the September 27 robot
# audio: stop at .7 twice fired on no other utterance or noise, and on 0-6 of 60 stops mixed into
# drive noise (his plain "Stop." reached only .04-.40). "Hey Rex" scored .89-1.00 on all seven of
# the owner's saved calls, 21 of 28 at .5 when mixed over his own voice as the board hears it, and
# never on his voice or cached lines alone.
WORDS = {'stop': (.7, 2), 'Hey_rex': (.5, 2), 'Hey_DJ_Rex': (.5, 2)}
WAKE_WORDS = frozenset({'Hey_rex', 'Hey_DJ_Rex'})


class KeywordSpotter:
    def __init__(self, folder: Path, words=None):
        self.folder = Path(folder)
        self.words = dict(WORDS if words is None else words)
        self.model = None
        self.loaded = ()
        self._pending = b''
        self._runs = {}
        self.last_scores = {}
        self.capability = Capability('spoken_keywords', 'openwakeword', 'stop while driving; wake words over his voice',
                                     ', '.join(name + '.onnx' for name in self.words),
                                     ('openwakeword', 'onnxruntime', 'numpy'), (str(self.folder), 'CPU'),
                                     required=False)

    def start(self):
        try:
            records = {r['name']: r['sha256'] for r in json.loads((self.folder / 'manifest.json').read_text())}
        except (OSError, ValueError, KeyError, TypeError):
            raise ProviderFailure('KeywordManifestUnreadable')
        present = tuple(w for w in self.words if (self.folder / (w + '.onnx')).is_file())
        if not present:
            raise ProviderFailure('KeywordModelMissing')
        for name in (*FEATURES, *(w + '.onnx' for w in present)):
            path = self.folder / name
            if name not in records or not path.is_file():
                raise ProviderFailure('KeywordModelMissing')
            if hashlib.sha256(path.read_bytes()).hexdigest() != records[name]:
                raise ProviderFailure('KeywordModelDigestMismatch')
        from openwakeword.model import Model
        self.model = Model(wakeword_models=[str(self.folder / (w + '.onnx')) for w in present],
                           inference_framework='onnx',
                           melspec_model_path=str(self.folder / FEATURES[0]),
                           embedding_model_path=str(self.folder / FEATURES[1]))
        self.loaded = present

    def feed(self, pcm: bytes) -> dict:
        """16-bit mono PCM at 16 kHz. Returns {word: score} for each word whose score has just held
        for its running frames; a word fires again only after its score falls."""
        if self.model is None:
            raise ProviderFailure('KeywordsNotStarted')
        import numpy as np
        self._pending += pcm
        fired = {}
        while len(self._pending) >= 2 * CHUNK:
            chunk, self._pending = self._pending[:2 * CHUNK], self._pending[2 * CHUNK:]
            self.last_scores = {w: float(s) for w, s in self.model.predict(np.frombuffer(chunk, dtype='<i2')).items()}
            for word, score in self.last_scores.items():
                threshold, frames = self.words[word]
                self._runs[word] = self._runs.get(word, 0) + 1 if score >= threshold else 0
                if self._runs[word] == frames:
                    fired[word] = score
        return fired

    def reset(self):
        if self.model is not None:
            self.model.reset()
        self._pending, self._runs = b'', {}

    def cancel(self):
        pass

    def close(self):
        self.model = None
