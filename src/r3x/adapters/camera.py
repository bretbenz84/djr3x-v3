"""One explicit macOS camera owner, shared latest frames, paced CPU face analysis."""
import shutil
import re
import threading
import time
from uuid import uuid4
from ..contracts import ProviderFailure
from ..faces import FaceTracker
from ..vision import Frame, FrameHub
from ..providers import Capability
from .face import FaceEncoder, NAMESPACE

def select_camera(device_listing: str, name: str) -> int:
    video,devices = False,[]
    for line in device_listing.splitlines():
        if "AVFoundation video devices:" in line:
            video = True
        elif "AVFoundation audio devices:" in line:
            video = False
        elif video and (match:=re.search(r"\[(\d+)\]\s+(.+)$",line)):
            devices.append((int(match[1]),match[2].strip()))
    exact = [i for i,n in devices if n.casefold()==name.casefold()]
    matches = exact or [i for i,n in devices if name.casefold() in n.casefold()]
    if not name or len(matches)!=1:
        raise ProviderFailure("CameraNameMissingOrAmbiguous")
    return matches[0]



class Camera:
    def __init__(self, device_name, model_path, trace=None, width=1280, height=720):
        self.device_name, self.trace = device_name, trace
        self.width,self.height = width,height
        self.encoder = FaceEncoder(model_path)
        self.process = self.worker = self.analysis_worker = None
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.tracker = FaceTracker()
        self.hub = FrameHub()
        self.hub.register("faces")
        self.failure = None
        self.profiles = lambda: {}
        self.session_id = uuid4().hex
        self.capability = Capability("camera","AVFoundation ffmpeg + SCRFD/EdgeFace CPU",
            "optional persistent face recognition; separate from acoustic identity",NAMESPACE,
            ("opencv-python","numpy","onnxruntime"),("named macOS camera", "ffmpeg", f"{width}x{height}; face analysis <=2fps"))

    def start(self):
        import subprocess
        if not self.device_name or any(ord(c)<32 for c in self.device_name):
            raise ProviderFailure("CameraNameMissingOrInvalid")
        executable = shutil.which("ffmpeg")
        if not executable: raise ProviderFailure("FFmpegMissing")
        self.encoder.start()
        try:
            listing = subprocess.run([executable,"-hide_banner","-f","avfoundation","-list_devices","true","-i",""],
                stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5)
        except subprocess.TimeoutExpired:
            raise ProviderFailure("CameraEnumerationTimedOut") from None
        index = select_camera(listing.stderr,self.device_name)
        self.process = subprocess.Popen([executable,"-nostdin","-loglevel","error","-f","avfoundation",
            "-pixel_format","uyvy422","-framerate","30","-video_size",f"{self.width}x{self.height}","-i",str(index)+":none","-an",
            "-pix_fmt","bgr24","-f","rawvideo","pipe:1"],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        self.worker = threading.Thread(target=self._read,name="r3x-camera",daemon=True)
        self.analysis_worker = threading.Thread(target=self._analyze,name="r3x-faces",daemon=True)
        self.worker.start()
        self.analysis_worker.start()
        if not self.ready.wait(10) or self.failure:
            raise ProviderFailure("CameraDidNotProduceAnalyzedFrame")

    def _failed(self,exc):
        if not self.stop.is_set():
            self.failure = type(exc).__name__
            self.ready.set()
            if self.trace: self.trace.emit("provider_failed",capability="camera",error_type=self.failure)
            self.cancel()

    def _read(self):
        try:
            size = self.width*self.height*3
            while not self.stop.is_set():
                pixels = bytearray()
                while len(pixels)<size and not self.stop.is_set():
                    chunk = self.process.stdout.read(size-len(pixels))
                    if not chunk: raise ProviderFailure("CameraStreamEnded")
                    pixels.extend(chunk)
                if not self.stop.is_set():
                    self.hub.publish(Frame(uuid4().hex,self.device_name,self.session_id,time.monotonic(),
                                           self.width,self.height,bytes(pixels)))
        except Exception as exc: self._failed(exc)

    def _analyze(self):
        last = None
        try:
            while not self.stop.is_set():
                frame = self.hub.take("faces",last)
                if frame is None: continue
                last = frame.frame_id
                began = time.monotonic()
                if began-frame.at>.5: continue
                detections = self.encoder.analyze(frame)
                with self.lock:
                    profiles = self.profiles()
                    rows = self.tracker.update([d[0] for d in detections],frame.at,[d[1] for d in detections],profiles)
                self.ready.set()
                if self.trace:
                    self.trace.emit("camera_frame",faces=len(rows),frame_id=frame.frame_id,
                        recognized=sum(r['identity'] is not None for r in rows),
                        processing_seconds=time.monotonic()-began,source_age_seconds=began-frame.at)
                del frame, detections
                self.stop.wait(max(0,.5-(time.monotonic()-began)))
        except Exception as exc: self._failed(exc)

    def latest(self, now):
        with self.lock:
            if self.failure or self.stop.is_set() or not self.tracker.history: return ()
            at, rows = self.tracker.history[-1]
            return rows if 0<=now-at<=1. else ()

    def during(self,start,end):
        with self.lock:
            return () if self.failure or self.stop.is_set() else self.tracker.during(start,end)

    def invalidate(self):
        with self.lock:
            self.tracker.clear()
            return self.tracker.epoch

    def cancel(self):
        self.stop.set()
        self.hub.close()
        if self.process and self.process.poll() is None: self.process.terminate()

    def close(self):
        self.cancel()
        if self.process:
            import subprocess
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for worker in (self.worker,self.analysis_worker):
            if worker:
                worker.join(3)
                if worker.is_alive(): raise ProviderFailure("CameraWorkerDidNotStop")
        if self.process: self.process.stdout.close()
        self.invalidate()
        self.encoder.close()
        self.process = None
