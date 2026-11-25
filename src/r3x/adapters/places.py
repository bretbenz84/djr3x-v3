"""Pinned MobileCLIP-S2 image encoder (ONNX) for room recognition; no torch, no downloads.

Exported from V2's open_clip checkpoint by tools/export_place_model.py. V2 preprocessed with
PIL (shorter side to 256 with antialiased bilinear, center crop, RGB in [0,1], no mean/std);
this reproduces that resampling in numpy (no PIL dependency); parity is in docs/ROOM_RECOGNITION.md.
"""
from functools import lru_cache
import threading
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
        self._loading = threading.Lock()
        self.capability = Capability('place_encoder', 'MobileCLIP-S2 ONNX CPU', 'which known room the camera sees',
                                     'mobileclip-s2:' + DIGEST[:12], ('numpy', 'onnxruntime'),
                                     ('256x256; CPU threads=1; one frame every 1.5 s at most; no fallback',),
                                     required=False)

    def start(self):
        """Loads once: a second start (its owner's, after an early load) finds the model there."""
        with self._loading:
            if self.session is not None:
                return
            if self.path.resolve() != self.path or sha256(self.path.read_bytes()).hexdigest() != DIGEST:
                raise ProviderFailure('PlaceModelDigestMismatch')
            import onnxruntime as ort
            options = ort.SessionOptions(); options.intra_op_num_threads = 1; options.inter_op_num_threads = 1
            # The default memory arena kept ~525 MB resident on the M2; without it ~300 MB, same speed.
            options.enable_cpu_mem_arena = False; options.enable_mem_pattern = False
            session = ort.InferenceSession(str(self.path), sess_options=options, providers=['CPUExecutionProvider'])
            if session.get_inputs()[0].shape != [1, 3, SIZE, SIZE]:
                raise ProviderFailure('PlaceModelShapeMismatch')
            self.session = session   # Only a checked model counts as loaded.

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

    @staticmethod
    def thumbnail(frame):
        """A tiny grey copy (room_map.THUMB_W x THUMB_H bytes) for measuring small turns between views."""
        return thumbnail(frame)

    @staticmethod
    def sharpness(frame):
        """How sharp the frame is (looking.sharpness): a smeared one is not kept as a room photo."""
        from ..looking import sharpness
        return sharpness(frame)

    @staticmethod
    def jpeg(frame):
        """The photo kept on his own disk: 640 px wide, about 40 KB."""
        return jpeg(frame)

    def cancel(self): pass   # Bounded CPU inference is joined by its owning worker.
    def close(self): self.session = None


def thumbnail(frame):
    import cv2
    import numpy as np
    from ..room_map import THUMB_H, THUMB_W
    image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(grey, (THUMB_W, THUMB_H), interpolation=cv2.INTER_AREA)
    return bytes(small.tobytes())


def jpeg(frame, width=640, quality=80):
    import cv2
    import numpy as np
    image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    if frame.width > width:
        image = cv2.resize(image, (width, round(frame.height * width / frame.width)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode('.jpg', image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ProviderFailure('RoomPhotoEncodeFailed')
    return data.tobytes()


def router_identity(run=None, timeout=2.):
    """A digest of the default router's hardware address, or None (owner, November 22: buildings by router
    hardware, never the network name, which a campus shares across buildings). macOS reads it with no extra
    permission; the access point's own ID would need Location permission and is not used."""
    import hashlib
    import re
    import subprocess
    run = run or (lambda args: subprocess.run(args, capture_output=True, text=True, timeout=timeout).stdout)
    route = run(['/sbin/route', '-n', 'get', 'default'])
    gateway = next((line.split(':', 1)[1].strip() for line in route.splitlines() if line.strip().startswith('gateway:')), None)
    if not gateway or not re.fullmatch(r'[0-9.]+', gateway):
        return None
    arp = run(['/usr/sbin/arp', '-n', gateway])
    found = re.search(r'\bat ((?:[0-9a-f]{1,2}:){5}[0-9a-f]{1,2})\b', arp, re.IGNORECASE)
    if not found:
        return None
    mac = ':'.join(part.zfill(2) for part in found.group(1).lower().split(':'))
    return hashlib.sha256(('router:' + mac).encode()).hexdigest()[:32]
