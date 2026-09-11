"""Pinned CPU SCRFD + EdgeFace. Explicit loading; static alignment only."""
from ..visual_evaluation import file_digest
from ..contracts import ProviderFailure

DETECTOR_SHA='5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91'


def align_face(image, points):
    import numpy as np
    import cv2
    target=np.array([[38.2946,51.6963],[73.5318,51.5014],[56.0252,71.7366],
                     [41.5493,92.3655],[70.7299,92.2041]],dtype=np.float64)
    rows=[];values=[]
    for (x,y),(u,v) in zip(points,target):
        rows.extend([[x,-y,1,0],[y,x,0,1]]);values.extend([u,v])
    a,b,tx,ty=np.linalg.lstsq(np.asarray(rows),np.asarray(values),rcond=None)[0]
    matrix=np.array([[a,-b,tx],[b,a,ty]])
    return cv2.warpAffine(image,matrix,(112,112)),matrix


class Detector:
    def __init__(self,path,size):
        import onnxruntime as ort
        if file_digest(path)!=DETECTOR_SHA: raise ValueError('DetectorDigestMismatch')
        options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
        # M5 changing-frame regression: ORT #28231. Keep bounded standard CPU kernels.
        options.add_session_config_entry("mlas.disable_kleidiai", "1")
        options.enable_cpu_mem_arena = False  # Release large detector temporaries on the 16 GB target.
        self.session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider'])
        self.name=self.session.get_inputs()[0].name;self.size=size
        if len(self.session.get_outputs())!=9:raise ValueError('ExpectedSCRFDThreeLevelKeypointModel')
    def detect(self,frame):
        import cv2
        import numpy as np
        h,w=frame.shape[:2];scale=self.size/max(h,w)
        nw,nh=int(w*scale),int(h*scale)
        resized=np.zeros((self.size,self.size,3),np.uint8)
        resized[:nh,:nw]=cv2.resize(frame,(nw,nh))
        blob=cv2.dnn.blobFromImage(resized,1/128.,(self.size,self.size),(127.5,)*3,swapRB=True)
        outputs=self.session.run(None,{self.name:blob});candidates=[]
        for i,stride in enumerate((8,16,32)):
            scores=outputs[i].reshape(-1)
            distances=outputs[i+3].reshape(-1,4)*stride
            landmarks=outputs[i+6].reshape(-1,5,2)*stride
            grid=np.stack(np.mgrid[:self.size//stride,:self.size//stride][::-1],axis=-1).reshape(-1,2)*stride
            anchors=np.repeat(grid,2,axis=0)
            for j in np.where(scores>=.5)[0]:
                x,y=anchors[j];l,t,r,b=distances[j]
                box=np.array([x-l,y-t,x+r,y+b])/scale
                points=(landmarks[j]+anchors[j])/scale
                candidates.append((float(scores[j]),box,points))
        candidates.sort(key=lambda r:r[0],reverse=True);kept=[]
        for candidate in candidates:
            box=candidate[1]
            def overlap(other):
                a=np.maximum(box[:2],other[:2]);b=np.minimum(box[2:],other[2:])
                inter=np.prod(np.maximum(0,b-a+1))
                return inter/(np.prod(box[2:]-box[:2]+1)+np.prod(other[2:]-other[:2]+1)-inter)
            if not any(overlap(row[1])>.4 for row in kept):kept.append(candidate)
        return kept


RECOGNIZER_SHA = "b850767cf791bda585600b5c4c7d7432b2f998ccd862caae34ef1afa967d2e54"
NAMESPACE = "edgeface-s:" + RECOGNIZER_SHA + ":rgb127.5:arcface-112-v1"


class FaceEncoder:
    def __init__(self, root):
        self.root = root
        self.detector = self.session = None

    def start(self):
        import cv2
        import onnxruntime as ort
        cv2.setNumThreads(1)
        for name, digest in (("scrfd_10g.onnx", DETECTOR_SHA),
                             ("edgeface_s_gamma_05.onnx", RECOGNIZER_SHA)):
            path = self.root / name
            if path.resolve() != path.absolute() or not path.is_file() or file_digest(path) != digest:
                raise ProviderFailure("FaceModelMissingOrDigestMismatch")
        self.detector = Detector(self.root / "scrfd_10g.onnx", 640)
        options = ort.SessionOptions()
        # M5 changing-frame regression: ORT #28231. Keep bounded standard CPU kernels.
        options.add_session_config_entry("mlas.disable_kleidiai", "1")
        options.enable_cpu_mem_arena = False
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(self.root / "edgeface_s_gamma_05.onnx"),
            sess_options=options, providers=["CPUExecutionProvider"])
        inp = self.session.get_inputs()[0]
        if inp.type != "tensor(float)" or list(inp.shape)[1:] != [3,112,112]:
            raise ProviderFailure("FaceRecognizerContractMismatch")
        self.input_name = inp.name

    def embed(self, aligned):
        import cv2
        from ..speakers import unit
        tensor = cv2.dnn.blobFromImage(aligned, 1/127.5, (112,112), (127.5,)*3, swapRB=True)
        result = unit(self.session.run(None, {self.input_name:tensor})[0].reshape(-1).tolist())
        if result is None or len(result) != 512:
            raise ProviderFailure("InvalidFaceEmbedding")
        return result

    def analyze(self, frame):
        import cv2
        import numpy as np
        from ..faces import FaceSample
        image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height,frame.width,3)
        import hashlib
        frame_digest = hashlib.sha256(frame.pixels).hexdigest()
        result = []
        for score, box, points in self.detector.detect(image)[:12]:
            x1,y1,x2,y2 = box
            width = float(x2-x1)
            if width < 20: continue
            crop, matrix = align_face(image, points)
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            blur, light = float(cv2.Laplacian(gray,cv2.CV_64F).var()), float(gray.mean())
            eye_distance = float(np.linalg.norm(points[1]-points[0]))
            nose_ratio = float(np.linalg.norm(points[2]-points[0])) / max(1.,eye_distance)
            inside = bool(x1>=0 and y1>=0 and x2<=frame.width and y2<=frame.height)
            quality = width>=96 and score>=.75 and blur>=45 and 30<=light<=225 and .35<=nose_ratio<=1.25 and inside
            vector = self.embed(crop) if width>=32 and score>=.6 else None
            original = image[max(0,int(y1)):min(frame.height,int(y2)+1),max(0,int(x1)):min(frame.width,int(x2)+1)]
            png = source = b""
            if quality and original.size:
                ok, encoded = cv2.imencode(".png",crop)
                ok2, raw = cv2.imencode(".jpg",original,[cv2.IMWRITE_JPEG_QUALITY,95])
                if ok and ok2 and len(raw)<=160000 and len(encoded)<=50000:
                    png, source = encoded.tobytes(), raw.tobytes()
                else: quality = False
            sample = FaceSample(vector, png, source, frame.frame_id, frame.camera_id, frame.session_id,
                frame.at, width, score, blur, light, tuple(matrix.reshape(-1).tolist()), NAMESPACE, quality,tuple(float(v) for v in box),(frame.width,frame.height),frame_digest)
            result.append(((float(x1/frame.width),float(y1/frame.height),float((x2-x1)/frame.width),float((y2-y1)/frame.height)),sample))
        return result

    def close(self):
        self.detector = self.session = None
