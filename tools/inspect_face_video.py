"""Offline SCRFD extraction and same-recording face diagnostic; never enrolls profiles."""
import argparse
import json
from pathlib import Path
import time
from r3x.visual_evaluation import file_digest

from r3x.adapters.face import DETECTOR_SHA, Detector, align_face


def extract(args):
    import cv2
    import numpy as np
    cv2.setNumThreads(1)
    source=args.video.resolve()
    if not source.is_file() or source.suffix.lower() not in {'.mov','.mp4'}:raise ValueError('SavedVideoRequired')
    args.output.mkdir(parents=True,exist_ok=False)
    detector=Detector(args.detector,args.size)
    video=cv2.VideoCapture(str(source),cv2.CAP_FFMPEG,[cv2.CAP_PROP_N_THREADS,1])
    if not video.isOpened():raise ValueError('VideoOpenFailed')
    duration=video.get(cv2.CAP_PROP_FRAME_COUNT)/video.get(cv2.CAP_PROP_FPS)
    if not 0<duration<=600:raise ValueError('ExpectedBoundedVideoUnderTenMinutes')
    frames=[]
    try:
        for second in range(int(duration)+1):
            video.set(cv2.CAP_PROP_POS_MSEC,second*1000);ok,frame=video.read()
            if not ok:break
            began=time.perf_counter();faces=detector.detect(frame);elapsed=time.perf_counter()-began
            row={'second':second,'detection_seconds':elapsed,'detections':len(faces),
                 'source_dimensions':[frame.shape[1],frame.shape[0]],'crop':None}
            preview=cv2.resize(frame,(320,180))
            for confidence,box,points in faces:
                x1,y1,x2,y2=box;cv2.rectangle(preview,(int(x1*320/frame.shape[1]),int(y1*180/frame.shape[0])),
                    (int(x2*320/frame.shape[1]),int(y2*180/frame.shape[0])),(0,255,0),1)
            cv2.imwrite(str(args.output/f'frame-{second:03d}.jpg'),preview)
            if len(faces)==1:
                confidence,box,points=faces[0];crop,matrix=align_face(frame,points)
                name=f'face-{second:03d}.png';cv2.imwrite(str(args.output/name),crop)
                row.update(crop=name,sha256=file_digest(args.output/name),face_width_px=float(box[2]-box[0]),
                           box=box.tolist(),landmarks=points.tolist(),alignment=matrix.tolist(),confidence=confidence)
            frames.append(row)
    finally:video.release()
    data={'version':1,'evidence':'same_recording_diagnostic','video':str(source),'video_sha256':file_digest(source),
          'detector_sha256':DETECTOR_SHA,'detector_input':args.size,'detector_threshold':.5,'nms':.4,
          'gallery_seconds':[0],'excluded_neighbor_seconds':[1,2,3],'frames':frames,
          'limitations':['One person, one recording, no impostors, no calibrated thresholds',
                         'Exactly one detection required; missing/multiple detections are retained',
                         'Static alignment landmarks only; no mouth motion or audio analyzed']}
    (args.output/'extraction.json').write_text(json.dumps(data,indent=2))
    print(json.dumps({'frames':len(frames),'single_face':sum(r['crop'] is not None for r in frames),'output':str(args.output)}))


def compare(args):
    import cv2
    import numpy as np
    import onnxruntime as ort
    from r3x.speakers import unit,cosine
    import statistics
    data=json.loads(args.extraction.read_text());root=args.extraction.parent
    if file_digest(args.model)!=args.sha256:raise ValueError('RecognizerDigestMismatch')
    options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
    session=ort.InferenceSession(str(args.model),sess_options=options,providers=['CPUExecutionProvider'])
    name=session.get_inputs()[0].name
    def embed(row):
        path=root/row['crop']
        if file_digest(path)!=row['sha256']:raise ValueError('CropDigestMismatch')
        image=cv2.imread(str(path));blob=cv2.dnn.blobFromImage(image,1/127.5,(112,112),(127.5,)*3,swapRB=True)
        result=unit(session.run(None,{name:blob})[0].reshape(-1).tolist())
        if result is None:raise ValueError('InvalidEmbedding')
        return result
    gallery=[embed(r) for r in data['frames'] if r['second'] in data['gallery_seconds'] and r['crop']]
    if len(gallery)!=len(data['gallery_seconds']):raise ValueError('FixedReferenceNotDetected')
    results=[]
    for row in data['frames']:
        if row['second'] in data['gallery_seconds']+data['excluded_neighbor_seconds']:continue
        result={'second':row['second'],'detections':row['detections'],'similarity':None,'face_width_px':row.get('face_width_px')}
        if row['crop']:
            began=time.perf_counter();vector=embed(row);result['seconds']=time.perf_counter()-began
            result['similarity']=sum(cosine(vector,g) for g in gallery)/len(gallery)
        results.append(result)
    groups={}
    for row in results:
        if row['similarity'] is None:continue
        w=row['face_width_px'];key='<64' if w<64 else '64-111' if w<112 else '112-223' if w<224 else '224+'
        groups.setdefault(key,[]).append(row['similarity'])
    report={'evidence':'same_recording_same_person_diagnostic','model_sha256':args.sha256,
            'extraction_sha256':file_digest(args.extraction),'gallery_seconds':data['gallery_seconds'],
            'probe_frames':len(results),'usable_crops':sum(r['similarity'] is not None for r in results),
            'size_groups':{k:{'n':len(v),'min':min(v),'median':statistics.median(v),'max':max(v)} for k,v in groups.items()},
            'results':results,'limitations':data['limitations']+['Similarity scales differ by model; not comparable accuracy percentages',
                'No live profile updated; no cross-session test; no wrong-person rate can be measured']}
    with args.output.open('x') as stream:json.dump(report,stream,indent=2)
    print(json.dumps({k:v for k,v in report.items() if k!='results'},indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    e=sub.add_parser('extract');e.add_argument('--video',type=Path,required=True)
    e.add_argument('--detector',type=Path,required=True);e.add_argument('--size',type=int,choices=[640,960],default=640)
    e.add_argument('--output',type=Path,required=True)
    c=sub.add_parser('compare');c.add_argument('--extraction',type=Path,required=True)
    c.add_argument('--model',type=Path,required=True);c.add_argument('--sha256',required=True);c.add_argument('--output',type=Path,required=True)
    args=p.parse_args();extract(args) if args.command=='extract' else compare(args)


if __name__=='__main__':main()
