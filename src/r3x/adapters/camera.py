"""Explicit macOS named-camera capture, bounded CPU face presence, no recognition."""
import shutil
import hashlib
import re
import threading
import time

from ..contracts import ProviderFailure
from ..faces import FaceTracker
from ..providers import Capability

FACE_MODEL_SHA256 = "0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0"


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


def load_detector(path):
    import cv2
    if path.resolve()!=path or not path.is_file():
        raise ProviderFailure("FaceDetectorMissing")
    if hashlib.sha256(path.read_bytes()).hexdigest()!=FACE_MODEL_SHA256:
        raise ProviderFailure("FaceDetectorDigestMismatch")
    cv2.setNumThreads(1)
    detector = cv2.CascadeClassifier(str(path))
    if detector.empty():
        raise ProviderFailure("FaceDetectorInvalid")
    return detector


class Camera:
    def __init__(self, device_name, model_path, trace=None):
        self.device_name, self.trace = device_name, trace
        self.model_path = model_path
        self.process = self.worker = self.detector = None
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.tracker = FaceTracker()
        self.failure = None
        self.capability = Capability("camera","AVFoundation ffmpeg + OpenCV Haar",
            "optional basic face presence; no identity or speaker inference","haarcascade_frontalface_default",
            ("opencv-python","numpy"),("named macOS camera", "ffmpeg", "640x480; detection <=2fps"))

    def start(self):
        import cv2
        import subprocess
        if not self.device_name or any(ord(c)<32 for c in self.device_name):
            raise ProviderFailure("CameraNameMissingOrInvalid")
        executable = shutil.which("ffmpeg")
        if not executable:
            raise ProviderFailure("FFmpegMissing")
        self.detector = load_detector(self.model_path)
        try:
            listing = subprocess.run([executable,"-hide_banner","-f","avfoundation","-list_devices","true","-i",""],
                stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=5)
        except subprocess.TimeoutExpired:
            raise ProviderFailure("CameraEnumerationTimedOut") from None
        index = select_camera(listing.stderr,self.device_name)
        self.process = subprocess.Popen([executable,"-nostdin","-loglevel","error","-f","avfoundation",
            "-pixel_format","uyvy422","-framerate","30","-video_size","640x480","-i",str(index)+":none","-an",
            "-pix_fmt","bgr24","-f","rawvideo","pipe:1"],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,bufsize=0)
        self.worker = threading.Thread(target=self._read,name="r3x-camera",daemon=True)
        self.worker.start()
        if not self.ready.wait(5) or self.failure:
            raise ProviderFailure("CameraDidNotProduceAFrame")

    def _read(self):
        import cv2
        import numpy as np
        next_detection = 0.
        try:
            while not self.stop.is_set():
                frame = bytearray()
                while len(frame)<640*480*3 and not self.stop.is_set():
                    chunk = self.process.stdout.read(640*480*3-len(frame))
                    if not chunk:
                        raise ProviderFailure("CameraStreamEnded")
                    frame.extend(chunk)
                at = time.monotonic()
                self.ready.set()
                if self.stop.is_set() or at<next_detection:
                    continue
                next_detection = at+.5
                gray = cv2.cvtColor(np.frombuffer(frame,dtype=np.uint8).reshape(480,640,3),cv2.COLOR_BGR2GRAY)
                boxes = self.detector.detectMultiScale(gray,scaleFactor=1.15,minNeighbors=5,minSize=(36,36))
                normalized = [(float(x)/640,float(y)/480,float(w)/640,float(h)/480) for x,y,w,h in boxes]
                with self.lock:
                    self.tracker.update(normalized,at)
                if self.trace:
                    self.trace.emit("camera_frame",faces=len(normalized),processing_seconds=time.monotonic()-at)
        except Exception as exc:
            if not self.stop.is_set():
                self.failure = type(exc).__name__
                self.ready.set()
                if self.trace:
                    self.trace.emit("provider_failed",capability="camera",error_type=self.failure)

    def during(self,start,end):
        with self.lock:
            return () if self.failure or self.stop.is_set() else self.tracker.during(start,end)

    def cancel(self):
        self.stop.set()
        if self.process and self.process.poll() is None:
            self.process.terminate()

    def close(self):
        self.cancel()
        if self.process:
            import subprocess
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
            if self.worker:
                self.worker.join(2)
                if self.worker.is_alive():
                    raise ProviderFailure("CameraWorkerDidNotStop")
            self.process.stdout.close()
        self.detector = self.process = None
