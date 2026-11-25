"""Near walls and corners from the head camera, while the wheels roll (owner, November 24).

Run d085dc5f: rolling toward the hallway to the workshop, steering left toward its doorway, Rex ran his left
side into the hall's corner. It sat between what the front depth grid sees (about 22 degrees either side of
straight ahead) and the side beams (67 degrees): the range sensors saw it only when it was 3 cm from him. The
owner: "It was just outside the ToF sensors view but would have been within the camera view."

The camera sees about 45 degrees either side. Depth Anything V2 Small (Apache-2.0; onnx-community's fp16
export, 49.6 MB, under local/models/depth, never committed) turns a frame into relative depth: which parts are
nearer than which, at an unknown scale that changes from frame to frame. The drive owner puts real distances
on it each time from the front depth grid's own readings in the middle of the same view
(drive_space.camera_points) and uses only the parts of the view the grid cannot see.

Owned by this provider: one model session, one worker. It works only while the drive owner says the wheels are
rolling, at most once every EVERY_S, reading the newest camera frame without taking a consumer slot. It never
speaks, steers or stops anything itself.
"""
from hashlib import sha256
import threading
import time

from .contracts import ProviderFailure
from .providers import Capability

MODEL_FILE = 'models/depth/depth-anything-v2-small-fp16.onnx'   # Under local/ (the scratch root).
DIGEST = '2df6223f206b5164e21f664ace61dabeb9bb6a49b8b5a3e00510b4807d0f5b04'
WIDTH, HEIGHT = 252, 140      # Multiples of 14 (the model's patch); 92 ms a frame on the M2 with two threads.
BINS = 36                     # Columns across the view: 2.5 degrees each.
BAND = (.40, .95)             # Rows read: below the ceiling (the camera looks level or a little up), to the bottom.
PERCENTILE = 75.              # Within a column, the near end of what fills it (a jamb fills it top to bottom).
EVERY_S = .25
FRESH_S = .6                  # An older view says nothing about where he is now.
MEAN, STD = (.485, .456, .406), (.229, .224, .225)


class CameraDepth:
    def __init__(self, camera, path, *, clock=time.monotonic):
        self.camera, self.path, self.clock = camera, path, clock
        self.active = lambda: False   # The drive owner: the wheels are rolling.
        self.neck = lambda: 0.        # Degrees the head is turned from the base (+left), read with the frame.
        self.trace = None
        self.session = None
        self.view = None              # {'at', 'frame_id', 'neck', 'columns': [(camera bearing deg, disparity)]}
        self.frames = 0
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.worker = None
        self.capability = Capability(
            'camera_depth', 'Depth Anything V2 Small ONNX CPU',
            'near walls and corners beside his path that the range sensors cannot see, from the head camera',
            'depth-anything-v2-small-fp16:' + DIGEST[:12], ('numpy', 'onnxruntime', 'opencv-python'),
            (f'{WIDTH}x{HEIGHT}; CPU threads=2; about 0.1 s a frame, at most one every {EVERY_S} s, only while rolling',),
            required=False)

    def start(self):
        if self.session is None:
            if self.path.resolve() != self.path or sha256(self.path.read_bytes()).hexdigest() != DIGEST:
                raise ProviderFailure('CameraDepthModelDigestMismatch')
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads, options.inter_op_num_threads = 2, 1
            options.enable_cpu_mem_arena = False; options.enable_mem_pattern = False
            # Full graph optimization fails on this fp16 export (a layer-norm fusion); the basic level runs it.
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
            session = ort.InferenceSession(str(self.path), sess_options=options, providers=['CPUExecutionProvider'])
            if session.get_inputs()[0].name != 'pixel_values':
                raise ProviderFailure('CameraDepthModelShapeMismatch')
            self.session = session
        self.stop.clear()
        self.worker = threading.Thread(target=self._run, name='r3x-camera-depth', daemon=True)
        self.worker.start()

    def cancel(self):
        self.stop.set()

    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(3)
            if self.worker.is_alive():
                raise ProviderFailure('CameraDepthWorkerDidNotStop')
        self.session = None

    def latest(self, now=None):
        """The newest view, while fresh; None otherwise."""
        now = self.clock() if now is None else now
        with self.lock:
            view = self.view
        return view if view is not None and 0 <= now - view['at'] <= FRESH_S else None

    def _run(self):
        last = None
        try:
            while not self.stop.is_set():
                began = self.clock()
                if not self.active():
                    self.stop.wait(.1)
                    continue
                frame = self.camera.hub.peek() if getattr(self.camera, 'hub', None) else None
                if frame is None or frame.frame_id == last or not 0 <= began - frame.at <= FRESH_S:
                    self.stop.wait(.05)
                    continue
                last = frame.frame_id
                neck = self._neck()
                columns = self.columns(frame)
                with self.lock:
                    self.view = {'at': frame.at, 'frame_id': frame.frame_id, 'neck': neck, 'columns': columns}
                self.frames += 1
                self.stop.wait(max(.0, EVERY_S - (self.clock() - began)))
        except Exception as exc:
            if self.trace: self.trace.emit('provider_failed', capability='camera_depth', error_type=type(exc).__name__)

    def _neck(self):
        try:
            return float(self.neck() or 0.)
        except Exception:
            return 0.

    def columns(self, frame):
        """[(camera-relative bearing in degrees, +left; disparity)] across the view, near = larger."""
        import numpy as np
        import cv2
        from .drive_space import HALF_FOV
        image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
        small = cv2.resize(image, (WIDTH, HEIGHT), interpolation=cv2.INTER_AREA)[:, :, ::-1].astype(np.float32) / 255.
        x = ((small - np.array(MEAN, dtype=np.float32)) / np.array(STD, dtype=np.float32)).transpose(2, 0, 1)[None]
        depth = self.session.run(None, {'pixel_values': np.ascontiguousarray(x, dtype=np.float32)})[0][0]
        return profile(depth, HALF_FOV)


def profile(depth, half_fov):
    """A disparity map (rows x columns) as BINS (bearing, near end of each column band) pairs."""
    import numpy as np
    height, width = depth.shape
    band = depth[int(BAND[0] * height):max(int(BAND[0] * height) + 1, int(BAND[1] * height))]
    out = []
    for index, cols in enumerate(np.array_split(np.arange(width), BINS)):
        centre = (cols[0] + cols[-1] + 1) / 2. / width
        out.append((round((.5 - centre) * 2 * half_fov, 2), round(float(np.percentile(band[:, cols], PERCENTILE)), 4)))
    return out
