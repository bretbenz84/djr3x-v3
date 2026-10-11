"""Pinned MobileCLIP-S2 image encoder (ONNX) for room recognition; no torch, no downloads.

Exported from V2's open_clip checkpoint by tools/export_place_model.py. V2 preprocessed with
PIL (shorter side to 256 with antialiased bilinear, center crop, RGB in [0,1], no mean/std);
this reproduces that resampling in numpy (no PIL dependency); parity is in docs/ROOM_RECOGNITION.md.
"""
from functools import lru_cache
from hashlib import sha256
from ..contracts import ProviderFailure
from ..providers import Capability

DIGEST = 'bf9d116d3192552bbd325410d155610d061b99bad86237f29114a12f4c935392'
NAMESPACE = 'mobileclip_s2_v1'   # V2's model tag: imported vectors share it only after the parity check.
SIZE = 256


@lru_cache(maxsize=8)
def _weights(source: int, target: int):
    """PIL's antialiased bilinear (triangle) resampling weights as a (target, source) matrix."""
    import numpy as np
    scale = source / target
    support = max(scale, 1.0)
    rows = np.zeros((target, source), dtype=np.float64)
    for i in range(target):
        center = (i + .5) * scale
        lo, hi = max(0, int(center - support + .5)), min(source, int(center + support + .5))
        x = (np.arange(lo, hi) - center + .5) / support
        w = np.clip(1 - np.abs(x), 0, None)
        rows[i, lo:hi] = w / w.sum()
    rows = rows.astype(np.float32)
    rows.setflags(write=False)
    return rows


def preprocess(frame):
    """BGR frame bytes -> float32 1x3x256x256 RGB in [0,1], reproducing open_clip's transform:
    torchvision Resize(256) on a PIL image (horizontal then vertical triangle pass, each rounded
    to 8 bits), CenterCrop(256), ToTensor. OpenCV's area filter differed by up to 0.02 in
    room-to-room similarity, half of V2's 0.04 decision margin."""
    import numpy as np
    image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    planes = image[:, :, ::-1].transpose(2, 0, 1).astype(np.float32)          # RGB, (3, h, w)
    short, long = sorted((frame.width, frame.height))
    target = SIZE, int(SIZE * long / short)
    width, height = (target[1], target[0]) if frame.width >= frame.height else target
    horizontal = np.clip(np.rint(planes @ _weights(frame.width, width).T), 0, 255)       # (3, h, width)
    resized = np.clip(np.rint(_weights(frame.height, height) @ horizontal), 0, 255)      # (3, height, width)
    top, left = int(round((height - SIZE) / 2.)), int(round((width - SIZE) / 2.))
    crop = resized[:, top:top + SIZE, left:left + SIZE] / np.float32(255.)
    return np.ascontiguousarray(crop[None], dtype=np.float32)


class PlaceEncoder:
    def __init__(self, path):
        self.path, self.session = path, None
        self.capability = Capability('place_encoder', 'MobileCLIP-S2 ONNX CPU', 'which known room the camera sees',
                                     'mobileclip-s2:' + DIGEST[:12], ('numpy', 'onnxruntime'),
                                     ('256x256; CPU threads=1; one frame every 1.5 s at most; no fallback',),
                                     required=False)

    def start(self):
        if self.path.resolve() != self.path or sha256(self.path.read_bytes()).hexdigest() != DIGEST:
            raise ProviderFailure('PlaceModelDigestMismatch')
        import onnxruntime as ort
        options = ort.SessionOptions(); options.intra_op_num_threads = 1; options.inter_op_num_threads = 1
        # The default memory arena kept ~525 MB resident on the M2; without it ~300 MB, same speed.
        options.enable_cpu_mem_arena = False; options.enable_mem_pattern = False
        self.session = ort.InferenceSession(str(self.path), sess_options=options, providers=['CPUExecutionProvider'])
        if self.session.get_inputs()[0].shape != [1, 3, SIZE, SIZE]:
            raise ProviderFailure('PlaceModelShapeMismatch')

    def embed(self, frame):
        """L2-normalized float32 vector."""
        if self.session is None:
            raise ProviderFailure('PlaceEncoderNotStarted')
        import numpy as np
        vector = self.session.run(['embedding'], {'pixels': preprocess(frame)})[0].reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(vector))
        if vector.shape != (512,) or not np.isfinite(norm) or norm < 1e-9:
            raise ProviderFailure('PlaceEmbeddingInvalid')
        return vector / norm

    def cancel(self): pass   # Bounded CPU inference is joined by its owning worker.
    def close(self): self.session = None
