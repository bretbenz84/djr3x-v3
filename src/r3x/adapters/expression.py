"""MediaPipe Face Landmarker, one face crop at a time: blendshape scores and nothing else.

V2 ran the landmarker on the whole frame, whose own face finder is short-range, and its
landmarks "often disappear at conversational distances". The robot's faces are small (median
83 px wide across a 1080p view, October logs), so this reads the crop of each face SCRFD
already found, enlarged. Scores only: no landmarks or head pose leave here, and the camera
passes on only the finished looks, so nothing can feed speaker attribution by accident.
"""
import os
import threading

from ..contracts import ProviderFailure
from ..providers import Capability
from ..visual_evaluation import file_digest

LANDMARKER_SHA = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff'
NAMESPACE = 'mediapipe-face-landmarker:' + LANDMARKER_SHA
KEYS = ('mouthSmileLeft', 'mouthSmileRight', 'mouthFrownLeft', 'mouthFrownRight', 'browInnerUp',
        'eyeWideLeft', 'eyeWideRight', 'jawOpen')
MIN_WIDTH = 48.      # Narrower is a far face (the picture of the present does not count it either).
MAX_FACES = 3        # The largest faces in a frame; a crowd must not slow face recognition.
MARGIN = .45         # Of the face width on every side: the landmarker's finder expects context.
CROP = 256           # Side of the square it reads, enlarged or reduced to this.


def face_crop(image, box, cv2):
    """A square crop around one SCRFD box, kept inside the picture, at CROP pixels a side."""
    x1, y1, x2, y2 = box
    side = max(x2 - x1, y2 - y1) * (1 + 2 * MARGIN)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    height, width = image.shape[:2]
    left, top = max(0, int(cx - side / 2)), max(0, int(cy - side / 2))
    right, bottom = min(width, int(cx + side / 2)), min(height, int(cy + side / 2))
    if right - left < 8 or bottom - top < 8:
        return None
    crop = image[top:bottom, left:right]
    scale = CROP / max(crop.shape[:2])
    method = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    crop = cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), max(1, round(crop.shape[0] * scale))),
                      interpolation=method)
    pad_x, pad_y = CROP - crop.shape[1], CROP - crop.shape[0]
    if pad_x or pad_y:   # Against an edge of the picture: pad rather than stretch the face.
        crop = cv2.copyMakeBorder(crop, pad_y // 2, pad_y - pad_y // 2, pad_x // 2, pad_x - pad_x // 2,
                                  cv2.BORDER_REPLICATE)
    return crop


class ExpressionReader:
    def __init__(self, path):
        self.path = path
        self.landmarker = self.mediapipe = None
        self.lock = threading.Lock()   # close() must not free the model under a read on the faces thread.
        self.capability = Capability('expressions', 'MediaPipe Face Landmarker CPU',
            "what faces in view are showing, for the conversation; never identity or attribution",
            NAMESPACE, ('mediapipe', 'absl-py', 'matplotlib'), ('face_landmarker.task', 'camera face pass'),
            required=False, requires=('camera',))

    def start(self):
        if self.path.resolve() != self.path.absolute() or not self.path.is_file() or file_digest(self.path) != LANDMARKER_SHA:
            raise ProviderFailure('ExpressionModelMissingOrDigestMismatch')
        os.environ.setdefault('MPLBACKEND', 'Agg')   # mediapipe imports pyplot; Rex draws nothing with it.
        import mediapipe
        from mediapipe.tasks.python.core.base_options import BaseOptions
        from mediapipe.tasks.python.vision import face_landmarker
        from mediapipe.tasks.python.vision.core.vision_task_running_mode import VisionTaskRunningMode
        options = face_landmarker.FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(self.path)),
            running_mode=VisionTaskRunningMode.IMAGE, num_faces=1,
            min_face_detection_confidence=.3, min_face_presence_confidence=.3,
            output_face_blendshapes=True)
        self.landmarker = face_landmarker.FaceLandmarker.create_from_options(options)
        self.mediapipe = mediapipe

    def read(self, image, box):
        """Blendshape scores for the face in this box, or None if it could not be read."""
        import cv2
        crop = face_crop(image, box, cv2)
        if crop is None:
            return None
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        with self.lock:
            if self.landmarker is None:
                return None
            result = self.landmarker.detect(self.mediapipe.Image(image_format=self.mediapipe.ImageFormat.SRGB, data=rgb))
        if not result.face_blendshapes:
            return None
        return {c.category_name: float(c.score) for c in result.face_blendshapes[0] if c.category_name in KEYS}

    def read_faces(self, frame, boxes):
        """One entry per box in order; only the largest MAX_FACES wide enough are read."""
        import numpy as np
        image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
        order = sorted(range(len(boxes)), key=lambda i: boxes[i][2] - boxes[i][0], reverse=True)
        chosen = [i for i in order if boxes[i][2] - boxes[i][0] >= MIN_WIDTH][:MAX_FACES]
        readings = [None] * len(boxes)
        for index in chosen:
            readings[index] = self.read(image, boxes[index])
        return readings

    def cancel(self):
        pass

    def close(self):
        with self.lock:
            landmarker, self.landmarker, self.mediapipe = self.landmarker, None, None
            if landmarker is not None:
                landmarker.close()
