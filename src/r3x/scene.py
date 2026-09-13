"""Two small camera snapshots for the existing brain; no face/speaker interpretation."""
from collections import deque
from dataclasses import dataclass, field
from hashlib import sha256
import base64
import math
import struct
import threading
import time
import zlib
from .providers import Capability
from .contracts import ProviderFailure


@dataclass(frozen=True)
class SceneImage:
    frame_id: str
    at: float
    data_url: str = field(repr=False)


def encode_frame(frame):
    if not 0<frame.width*frame.height<=1920*1080 or len(frame.pixels)!=frame.width*frame.height*3:
        raise ValueError('InvalidBoundedSceneFrame')
    step=max(1,math.ceil(max(frame.width,frame.height)/480))
    width=(frame.width+step-1)//step;height=(frame.height+step-1)//step
    rgb=bytearray();signature=[]
    for y in range(0,frame.height,step):
        row=frame.pixels[y*frame.width*3:(y+1)*frame.width*3]
        for x in range(0,frame.width,step):
            i=x*3;rgb.extend((row[i+2],row[i+1],row[i]))
    for y in range(0,height,max(1,height//9)):
        for x in range(0,width,max(1,width//16)):
            i=(y*width+x)*3;signature.extend(rgb[i:i+3])
    def chunk(kind,body):return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body)&0xffffffff)
    raw=b''.join(b'\0'+rgb[y*width*3:(y+1)*width*3] for y in range(height))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw,1))+chunk(b'IEND',b'')
    return SceneImage(frame.frame_id,frame.at,'data:image/png;base64,'+base64.b64encode(png).decode()),bytes(signature)


class SceneObserver:
    def __init__(self,camera,trace=None,clock=time.monotonic):
        self.camera,self.trace,self.clock=camera,trace,clock
        self.capability=Capability('scene_observations','shared camera snapshots','objects, animals and visible gestures in the main conversation generation',
            None,(),('two cached + two in-flight <=480px images; <=2 samples/sec; hosted input during opportunities or tool-selected current-view questions',),required=False)
        self.stop=threading.Event();self.lock=threading.RLock();self.worker=None
        self.images=deque(maxlen=2);self.signature=None;self.scene_id=None;self.last_offer=float('-inf');self.offered_id=None

    def start(self):
        self.camera.hub.register('scene')
        self.worker=threading.Thread(target=self._run,name='r3x-scene',daemon=True);self.worker.start()

    def observe(self,frame):
        if frame.session_id!=self.camera.session_id or not 0<=self.clock()-frame.at<=1:return
        began=self.clock();image,signature=encode_frame(frame)
        with self.lock:
            deltas=[abs(a-b) for a,b in zip(signature,self.signature or b'')]
            changed=self.signature is None or len(signature)!=len(self.signature) or sum(deltas)/len(signature)>12 or sum(d>45 for d in deltas)/len(signature)>.02
            if changed:
                self.scene_id='scene:'+sha256(signature).hexdigest()[:24];self.signature=signature
            self.images.append(image)
        if self.trace:self.trace.emit('scene_sample',frame_id=frame.frame_id,changed=changed,bytes=len(image.data_url),seconds=self.clock()-began)

    def _run(self):
        last=None
        try:
            while not self.stop.is_set():
                frame=self.camera.hub.take('scene',last,timeout=.5)
                if frame:
                    last=frame.frame_id;self.observe(frame)
                self.stop.wait(.5)
        except Exception as exc:
            if self.trace:self.trace.emit('provider_failed',capability='scene_observations',error_type=type(exc).__name__)
            self.cancel()

    def snapshot(self,include_offered=False):
        with self.lock:
            now=self.clock()
            images=tuple(i for i in self.images if 0<=now-i.at<=2)
            if self.stop.is_set() or not images:return None
            if not include_offered and (self.offered_id==self.scene_id or now-self.last_offer<15):return None
            return self.scene_id,images

    def valid(self,cue_id):
        snap=self.snapshot(True)
        return snap is not None and snap[0]==cue_id

    def attempted(self,cue_id):
        with self.lock:self.offered_id=cue_id;self.last_offer=self.clock()

    def cancel(self):self.stop.set()
    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(2)
            if self.worker.is_alive():raise ProviderFailure('SceneWorkerDidNotStop')
        with self.lock:self.images.clear();self.signature=None
