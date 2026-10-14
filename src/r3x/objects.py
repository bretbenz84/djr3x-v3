"""Paced object observations; track IDs are temporary locations, never identity."""
from threading import Event,RLock,Thread
from uuid import uuid4
import time
from .faces import iou
from .providers import Capability
from .contracts import ProviderFailure
from .scene import SceneImage


def crop_image(frame,box=None):
    import cv2
    import numpy as np
    import base64
    a=np.frombuffer(frame.pixels,dtype=np.uint8).reshape(frame.height,frame.width,3)
    if box:
        x,y,w,h=box
        a=a[max(0,int(y*frame.height)):min(frame.height,int((y+h)*frame.height)+1),max(0,int(x*frame.width)):min(frame.width,int((x+w)*frame.width)+1)]
    if a.size==0:raise ValueError('EmptyObjectCrop')
    ratio=min(1.,960/max(a.shape[:2]))
    if ratio<1:a=cv2.resize(a,(round(a.shape[1]*ratio),round(a.shape[0]*ratio)),interpolation=cv2.INTER_AREA)
    ok,data=cv2.imencode('.png',a)
    if not ok or len(data)>1_100_000:raise ValueError('ObjectImageTooLarge')
    return SceneImage(frame.frame_id,frame.at,'data:image/png;base64,'+base64.b64encode(data).decode())


def describe_positions(rows):
    """Camera-image coordinates and relative order, never identity or robot bearing."""
    for row in rows:
        x,y,w,h=row['box'];cx,cy=x+w/2,y+h/2
        peers=[r for r in rows if r['label']==row['label'] or {r['label'],row['label']}<={'dog','cat'}]
        horizontal=1+sum(r['box'][0]+r['box'][2]/2<cx for r in peers)
        vertical=1+sum(r['box'][1]+r['box'][3]/2<cy for r in peers)
        row['image_position']={'center_x':cx,'center_y':cy,'frame':'camera image; x rightward, y downward',
            'similar_objects':len(peers),'left_to_right_rank':horizontal,'top_to_bottom_rank':vertical,
            'description':('leftmost' if horizontal==1 else 'rightmost' if horizontal==len(peers) else 'middle')+
                ', '+('uppermost' if vertical==1 else 'lowermost' if vertical==len(peers) else 'middle')+' among '+str(len(peers))+' similar detected objects' if len(peers)>1 else 'only detected object of this category'}
    return rows


PET_LABELS=frozenset({'dog','cat'})   # The detector often calls one animal both, frame to frame.
# October 13: Max held in Bret's arms was detected about every other second; "This is Max" came
# between detections and the teaching look failed. A pet seen this recently is still there.
PET_RECALL_SECONDS=10.


class Objects:
    def __init__(self,camera,detector,trace=None,scene=None,clock=time.monotonic):
        self.camera,self.detector,self.trace,self.scene,self.clock=camera,detector,trace,scene,clock
        self.stop=Event();self.lock=RLock();self.worker=None;self.frame=None;self.rows=[];self.failure=None
        self.labels={}   # Recent target id -> label, to re-find a thing whose track restarted.
        self.last_pet=None   # (frame, row) of the latest pet detection, for flickering detections.
        self.capability=Capability('objects',detector.capability.provider,'stable object cues and crops',detector.capability.model,
            detector.capability.dependencies,('one shared frame; one inference pass/1.5 seconds; latest work only',))

    def start(self):
        self.detector.start();self.camera.hub.register('objects')
        self.worker=Thread(target=self._run,name='r3x-objects',daemon=True);self.worker.start()

    def observe(self,frame):
        if frame.session_id!=self.camera.session_id or not 0<=self.clock()-frame.at<=1:return
        began=self.clock();detections=self.detector.detect(frame)
        with self.lock:
            old=list(self.rows);rows=[]
            for d in detections:
                matches=sorted(((iou(d['box'],r['box']),r) for r in old if frame.at-r['at']<4),key=lambda p:p[0],reverse=True)
                match=matches[0][1] if matches and matches[0][0]>.3 and (len(matches)==1 or matches[0][0]-matches[1][0]>.1) else None
                if match:old.remove(match)
                row={**d,'target_id':match['target_id'] if match else 'object-'+uuid4().hex,'at':frame.at,
                     'samples':match['samples']+1 if match else 1}
                rows.append(row)
            appeared=any(r['samples']==2 for r in rows)
            self.rows,self.frame=describe_positions(rows),frame
            for r in rows:self.labels[r['target_id']]=r['label']
            pets=[r for r in rows if r['label'] in PET_LABELS]
            if pets:self.last_pet=(frame,dict(max(pets,key=lambda r:r['box'][2]*r['box'][3])))
            while len(self.labels)>64:self.labels.pop(next(iter(self.labels)))
        if appeared and self.scene:
            with self.scene.lock:self.scene.scene_id='objects:'+uuid4().hex
        if self.trace:self.trace.emit('object_sample',frame_id=frame.frame_id,objects=rows,seconds=self.clock()-began)

    def snapshot(self, min_samples=2):
        with self.lock:
            if self.stop.is_set() or self.failure or not self.frame or not 0<=self.clock()-self.frame.at<=4:return ()
            rows=[dict(r) for r in self.rows if r['samples']>=min_samples]
            recalled=self._recalled_pet()
            if recalled and not any(r['label'] in PET_LABELS for r in self.rows):
                rows.append({**recalled[1],'recently_seen':True,
                             'seen_seconds_ago':round(self.clock()-recalled[1]['at'],1)})
            return tuple(rows)

    def _recalled_pet(self):
        if not self.last_pet:return None
        frame,row=self.last_pet
        return self.last_pet if 0<=self.clock()-row['at']<=PET_RECALL_SECONDS else None

    def inspect(self,target_id=None):
        with self.lock:
            if self.stop.is_set() or self.failure or not self.frame or not 0<=self.clock()-self.frame.at<=4:
                raise ValueError('FreshObjectViewUnavailable')
            frame=self.frame
            target=next((r for r in self.rows if r['target_id']==target_id and r['samples']>=2),None) if target_id else None
            recalled=self._recalled_pet()
            if (target_id and target is None and recalled and
                    (recalled[1]['target_id']==target_id or self.labels.get(target_id) in PET_LABELS)
                    and not any(r['label'] in PET_LABELS and r['samples']>=2 for r in self.rows)):
                # The pet between flickering detections: its own last frame, not today's box on
                # a frame the head has since moved.
                frame,target=recalled
            elif target_id and target is None:
                # Track ids restart whenever the head or base moves. October 3: a look at the
                # dog failed a few seconds after it was offered and was voiced as his "saved
                # photo" having expired. The one thing of the same kind still in view is it.
                dog_or_cat={'dog','cat'}
                wanted=self.labels.get(target_id)
                same=[r for r in self.rows if r['samples']>=2 and wanted and
                      (r['label']==wanted or {r['label'],wanted}<=dog_or_cat)]
                if len(same)!=1:raise ValueError('ObjectTargetExpired')
                target=same[0]
        return (crop_image(frame,target['box'] if target else None),
                dict(target) if target else {'label':'whole view','box':[0,0,1,1]},crop_image(frame))

    def _run(self):
        last=None
        try:
            while not self.stop.is_set():
                began=self.clock();frame=self.camera.hub.take('objects',last)
                if frame:last=frame.frame_id;self.observe(frame)
                self.stop.wait(max(.05,1.5-(self.clock()-began)))
        except Exception as exc:
            self.failure=type(exc).__name__
            if self.trace:self.trace.emit('provider_failed',capability='objects',error_type=self.failure)
            self.cancel()

    def cancel(self):self.stop.set()
    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(5)
            if self.worker.is_alive():raise ProviderFailure('ObjectWorkerDidNotStop')
        with self.lock:self.frame=None;self.rows=[]
        self.detector.close()
