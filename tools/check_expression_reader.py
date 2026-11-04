"""Real model on saved pictures: how well the expression reader reads, how fast, and what fires.

Offline and local: nothing is sent anywhere or written. Not part of the blocked-I/O checks. Needs
mediapipe and the model (requirements-expressions.txt, tools/prepare_face_models.py); until they are in
.venv it also runs with V2's venv, read only, given --model:

    /Users/bbenziger/djr3x-v2/venv/bin/python -I tools/check_expression_reader.py --model PATH --image A.png --video B.mov

--image: one reading per detected face, then the same face shrunk to the robot's usual face widths.
--video: two readings a second, as the live face pass does, through the same rule that decides a look.
Scores show the model's answer. They are not accuracy against labelled faces; this tool has none.
"""
import argparse
from pathlib import Path
import resource
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from r3x.adapters.expression import MIN_WIDTH, ExpressionReader
from r3x.adapters.face import Detector
from r3x.expressions import UNPROMPTED, ExpressionWatch, looks


def peak_mb():
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e6)


def percentile(values, share):
    return round(sorted(values)[int(len(values) * share)], 3) if values else None


def image_check(path, reader, detector, cv2, timings):
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError('UnreadableImage')
    faces = detector.detect(image)
    print(f'{path.name}: {len(faces)} faces')
    for _, box, _ in faces[:3]:
        width = float(box[2] - box[0])
        for target in (None, 130, 83, 66, 50):
            scale = 1. if target is None else target / width
            small = image if target is None else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            began = time.perf_counter()
            result = reader.read(small, [float(v) * scale for v in box])
            timings.append(time.perf_counter() - began)
            shown = {k: round(v, 2) for k, v in looks(result).items()} if result else 'MISSED'
            print(f'  face {round(width * scale)} px wide: {shown}')


def video_check(path, reader, detector, cv2, timings):
    video = cv2.VideoCapture(str(path))
    fps = video.get(cv2.CAP_PROP_FPS) or 15.
    total = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    watch = ExpressionWatch()
    scores, moments, read, missed = {k: [] for k in UNPROMPTED}, {}, 0, 0
    for index in range(0, total, max(1, round(fps / 2))):
        video.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = video.read()
        if not ok:
            break
        at = index / fps
        height, width = frame.shape[:2]
        for _, box, _ in detector.detect(frame)[:3]:
            if box[2] - box[0] < MIN_WIDTH:
                continue
            began = time.perf_counter()
            result = reader.read(frame, box)
            timings.append(time.perf_counter() - began)
            read, missed = read + (result is not None), missed + (result is None)
            shape = (box[0] / width, box[1] / height, (box[2] - box[0]) / width, (box[3] - box[1]) / height)
            row = watch.observe(tuple(float(v) for v in shape), None, result, at)
            for look, score in (row.get('expression_read') or {}).items():
                scores[look].append(score)
            if row.get('expression'):
                moment = moments.setdefault(row['expression_moment'], {'look': row['expression'],
                    'from_second': round(row['expression_since'], 1), 'unprompted': False})
                moment.update(to_second=round(at, 1), unprompted=moment['unprompted'] or row['expression_unprompted'])
    print(f'{path.name}: {total / fps:.0f} s, {read} faces read, {missed} missed')
    for look, values in scores.items():
        print(f'  {look}: median {percentile(values, .5)}, 90th {percentile(values, .9)}, 99th {percentile(values, .99)}, '
              f'max {round(max(values), 3) if values else None}')
    print(f'  looks that fired: {list(moments.values())}')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=Path, default=ROOT / 'local/models/faces/face_landmarker.task')
    parser.add_argument('--detector', type=Path, default=ROOT / 'local/models/faces/scrfd_10g.onnx')
    parser.add_argument('--image', type=Path, action='append', default=[])
    parser.add_argument('--video', type=Path, action='append', default=[])
    args = parser.parse_args()
    import cv2
    before = peak_mb()
    began = time.perf_counter()
    reader = ExpressionReader(args.model)
    reader.start()
    print(f'model load {time.perf_counter() - began:.2f} s; peak memory {before} -> {peak_mb()} MB')
    detector = Detector(args.detector, 960)
    timings = []
    try:
        for path in args.image:
            image_check(path, reader, detector, cv2, timings)
        for path in args.video:
            video_check(path, reader, detector, cv2, timings)
    finally:
        reader.close()
    print(f'{len(timings)} reads: median {percentile(timings, .5) * 1000:.1f} ms, 99th {percentile(timings, .99) * 1000:.1f} ms; peak memory {peak_mb()} MB')


if __name__ == '__main__':
    main()
