"""Isolated CPU ONNX recognizer benchmark on aligned saved crops; no devices/network."""
import argparse
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time

from r3x.visual_evaluation import classify, file_digest, load_manifest, summarize
from r3x.speakers import unit


def rss_bytes():
    return int(subprocess.check_output(['ps','-o','rss=','-p',str(__import__('os').getpid())]).strip())*1024


def distribution(values):
    values = sorted(values)
    return {'count':len(values),'median_seconds':statistics.median(values),
            'p95_seconds':values[max(0,math.ceil(.95*len(values))-1)]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--manifest',type=Path)
    parser.add_argument('--smoke',action='store_true',help='Generated pixels: resource check only, no accuracy claim')
    parser.add_argument('--threshold',type=float,required=True,help='Freeze using a separate tuning set before evaluating')
    parser.add_argument('--margin',type=float,default=.07)
    parser.add_argument('--mean',type=float,default=127.5)
    parser.add_argument('--std',type=float,default=127.5)
    parser.add_argument('--threads',type=int,default=1)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    if bool(args.manifest)==args.smoke or not -1<=args.threshold<=1 or not 0<=args.margin<=2 or args.std<=0 or args.threads<1:
        parser.error('Choose exactly one manifest or smoke, with valid thresholds/normalization/threads')
    if args.output.exists() or args.output.is_symlink():
        parser.error('Output already exists; choose a new report path')
    if args.model.resolve()!=args.model.absolute() or file_digest(args.model)!=args.sha256:
        parser.error('Model missing, linked or digest mismatch')
    rows = load_manifest(args.manifest) if args.manifest else []
    before_import = rss_bytes()
    import cv2
    import numpy as np
    import onnxruntime as ort
    cv2.setNumThreads(1)
    options = ort.SessionOptions()
    options.intra_op_num_threads = args.threads
    options.inter_op_num_threads = 1
    start = time.perf_counter()
    session = ort.InferenceSession(str(args.model),sess_options=options,providers=['CPUExecutionProvider'])
    load_seconds = time.perf_counter()-start
    inp = session.get_inputs()[0]
    if len(session.get_inputs())!=1 or inp.type!='tensor(float)' or list(inp.shape)[1:] != [3,112,112]:
        raise ValueError('ExpectedOneFloatNCHW112RecognizerInput')
    loaded_rss = rss_bytes()
    def embed(image):
        if image is None or image.shape != (112,112,3):
            raise ValueError('ExpectedAlreadyAligned112BGRImage')
        tensor = cv2.dnn.blobFromImage(image,1/args.std,(112,112),(args.mean,)*3,swapRB=True)
        vector = unit(session.run(None,{inp.name:tensor})[0].reshape(-1).tolist())
        if vector is None:
            raise ValueError('InvalidEmbedding')
        return vector
    generated = np.random.default_rng(17).integers(0,256,(112,112,3),dtype=np.uint8)
    first = time.perf_counter(); embed(generated); first_seconds=time.perf_counter()-first
    timings, gallery, decisions = [], {}, []
    if args.smoke:
        for _ in range(30):
            start=time.perf_counter(); embed(generated); timings.append(time.perf_counter()-start)
    else:
        # Gallery built before probes; labels never enter the inference function.
        for row in rows:
            if row['split']=='gallery':
                gallery.setdefault(row['person_id'],[]).append(embed(cv2.imread(row['_path'])))
        for row in rows:
            if row['split']!='probe': continue
            image=cv2.imread(row['_path'])
            start=time.perf_counter(); vector=embed(image); timings.append(time.perf_counter()-start)
            decisions.append({'expected':row['person_id'],'predicted':classify(vector,gallery,args.threshold,args.margin),
                              'camera':row['camera'],'session':row['session'],'face_width_px':row['face_width_px']})
    report={'version':1,'evidence':'generated_pixels_resource_smoke' if args.smoke else 'real_model_saved_aligned_crops',
            'model_sha256':args.sha256,'model_bytes':args.model.stat().st_size,
            'manifest_sha256':file_digest(args.manifest) if args.manifest else None,
            'host':{'platform':platform.platform(),'machine':platform.machine(),
                    'chip':subprocess.check_output(['sysctl','-n','machdep.cpu.brand_string'],text=True).strip() if sys.platform=='darwin' else None},
            'providers':session.get_providers(),'threads':args.threads,
            'dependencies':{n:importlib.metadata.version(n) for n in ('onnxruntime','opencv-python','numpy')},
            'threshold':args.threshold,'margin':args.margin,'normalization':{'mean':args.mean,'std':args.std,'order':'RGB'},
            'load_seconds':load_seconds,'first_embedding_seconds':first_seconds,'warm_embedding':distribution(timings),
            'rss_before_import_bytes':before_import,'rss_after_load_bytes':loaded_rss,'rss_after_run_bytes':rss_bytes(),
            'process_peak_rss_bytes':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*(1 if sys.platform=='darwin' else 1024),
            'accuracy':summarize(decisions) if decisions else None,
            'limitations':['No camera, detector, alignment, pose, object model, ASR contention or tracking evaluated',
                           'RSS is process memory, not total system pressure or a robot resource guarantee',
                           'Fresh process per candidate; runtime defaults are unchanged']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as stream: json.dump(report,stream,indent=2)
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    main()
