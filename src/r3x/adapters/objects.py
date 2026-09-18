"""Pinned RF-DETR Nano ONNX; one CPU thread, no torch/runtime downloads."""
from hashlib import sha256
from ..providers import Capability
from ..contracts import ProviderFailure

DIGEST = 'c5bf86667ae59f6d10d508fcb0af90da0478a4eb22d0d9f0e051a217896509b9'
# COCO category IDs, retaining the sparse IDs used by the pretrained checkpoint.
LABELS = dict(zip((1,2,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19,20,21,22,23,24,25,27,28,31,32,33,34,35,36,37,38,39,40,41,42,43,44,46,47,48,49,50,51,52,53,54,55,56,57,58,59,60,61,62,63,64,65,67,70,72,73,74,75,76,77,78,79,80,81,82,84,85,86,87,88,89,90),
    'person|bicycle|car|motorcycle|airplane|bus|train|truck|boat|traffic light|fire hydrant|stop sign|parking meter|bench|bird|cat|dog|horse|sheep|cow|elephant|bear|zebra|giraffe|backpack|umbrella|handbag|tie|suitcase|frisbee|skis|snowboard|sports ball|kite|baseball bat|baseball glove|skateboard|surfboard|tennis racket|bottle|wine glass|cup|fork|knife|spoon|bowl|banana|apple|sandwich|orange|broccoli|carrot|hot dog|pizza|donut|cake|chair|couch|potted plant|bed|dining table|toilet|tv|laptop|mouse|remote|keyboard|cell phone|microwave|oven|toaster|sink|refrigerator|book|clock|vase|scissors|teddy bear|hair drier|toothbrush'.split('|')))

class ObjectDetector:
    def __init__(self,path):
        self.path,self.session=path,None
        self.capability=Capability('object_detector','RF-DETR Nano ONNX CPU','shared animal/object detection',
            'rfdetr-nano:'+DIGEST[:12],('numpy','opencv-python','onnxruntime'),('384x384; CPU threads=1; no fallback',))

    def start(self):
        if self.path.resolve()!=self.path or sha256(self.path.read_bytes()).hexdigest()!=DIGEST:
            raise ProviderFailure('ObjectModelDigestMismatch')
        import onnxruntime as ort
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        self.session=ort.InferenceSession(str(self.path),sess_options=options,providers=['CPUExecutionProvider'])
        if self.session.get_inputs()[0].shape!=[1,3,384,384]:raise ProviderFailure('ObjectModelShapeMismatch')

    def detect(self,frame):
        if self.session is None:raise ProviderFailure('ObjectDetectorNotStarted')
        import cv2
        import numpy as np
        image=np.frombuffer(frame.pixels,dtype=np.uint8).reshape(frame.height,frame.width,3)
        rgb=cv2.cvtColor(image,cv2.COLOR_BGR2RGB)
        x=cv2.resize(rgb,(384,384)).astype(np.float32)/255.
        x=(x-np.array([.485,.456,.406],dtype=np.float32))/np.array([.229,.224,.225],dtype=np.float32)
        boxes,logits=self.session.run(['dets','labels'],{'input':x.transpose(2,0,1)[None]})
        if boxes.shape!=(1,300,4) or logits.shape!=(1,300,91):raise ProviderFailure('ObjectOutputShapeMismatch')
        scores=1/(1+np.exp(-np.clip(logits[0],-80,80)))
        rows=[]
        from ..faces import iou
        for query in np.argsort(scores.max(axis=1))[::-1]:
            cls=int(scores[query].argmax());score=float(scores[query,cls])
            if score<.4:break
            if cls not in LABELS or cls==1:continue # Faces own people; no person learning here.
            cx,cy,w,h=map(float,boxes[0,query]);x1=max(0.,cx-w/2);y1=max(0.,cy-h/2)
            box=(x1,y1,min(1.,cx+w/2)-x1,min(1.,cy+h/2)-y1)
            if not all(np.isfinite(box)) or min(box[2:])<=0:continue
            if any(iou(box,r['box'])>.7 for r in rows):continue
            rows.append({'label':LABELS[cls],'score':score,'box':list(box)})
            if len(rows)>=12:break
        return rows

    def cancel(self):pass # In-flight bounded CPU inference is joined by its owning worker.
    def close(self):self.session=None
