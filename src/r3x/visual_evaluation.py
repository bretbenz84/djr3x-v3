"""Offline aligned-face evaluation contracts. No camera or profile writes."""
import hashlib
import json
import math
from pathlib import Path
from .speakers import cosine, unit


def file_digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(path):
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    if data.get('version') != 1 or data.get('alignment') != 'arcface-112-v1':
        raise ValueError('UnsupportedFaceManifest')
    rows = data.get('images', [])
    if not rows:
        raise ValueError('EmptyFaceDataset')
    seen, sessions, gallery = set(), {}, set()
    for row in rows:
        if row['split'] not in {'gallery','probe'} or not row['session'] or not row['camera']:
            raise ValueError('MissingSplitSessionCamera')
        if row['person_id'] is not None and not isinstance(row['person_id'], str):
            raise ValueError('InvalidPersonLabel')
        if not math.isfinite(row['face_width_px']) or row['face_width_px'] <= 0:
            raise ValueError('InvalidOriginalFaceWidth')
        image = path.parent / row['path']
        if image.is_absolute() and not image.resolve().is_relative_to(path.parent):
            raise ValueError('ImageOutsideDataset')
        if image.resolve() != image.absolute() or not image.is_file():
            raise ValueError('MissingOrLinkedImage')
        actual = file_digest(image)
        if actual != row['sha256'] or actual in seen:
            raise ValueError('DigestMismatchOrDuplicateImage')
        seen.add(actual)
        previous = sessions.setdefault(row['session'], row['split'])
        if previous != row['split']:
            raise ValueError('SessionLeakage')
        if row['split'] == 'gallery':
            if not row['person_id']:
                raise ValueError('UnnamedGalleryFace')
            gallery.add(row['person_id'])
        row['_path'] = str(image)
    if not gallery or not any(r['split']=='probe' for r in rows):
        raise ValueError('GalleryAndProbesRequired')
    if any(r['person_id'] is not None and r['person_id'] not in gallery for r in rows):
        raise ValueError('UnknownProbeMustUseNullLabel')
    return rows


def classify(vector, gallery, threshold, margin):
    vector = unit(vector)
    if vector is None:
        return None
    scores = sorted(((sum(cosine(vector,v) for v in refs)/len(refs), person)
                     for person,refs in gallery.items() if refs), reverse=True)
    if not scores or scores[0][0] < threshold or (len(scores)>1 and scores[0][0]-scores[1][0] < margin):
        return None
    return scores[0][1]


def summarize(decisions):
    def counts(rows):
        known = [r for r in rows if r['expected'] is not None]
        unknown = [r for r in rows if r['expected'] is None]
        correct = sum(r['predicted']==r['expected'] for r in known)
        wrong = sum(r['predicted'] is not None and r['predicted']!=r['expected'] for r in rows)
        named = sum(r['predicted'] is not None for r in rows)
        return {'probes':len(rows), 'known':len(known), 'unknown':len(unknown),
                'correct_known':correct, 'wrong_names':wrong,
                'unknown_false_accepts':sum(r['predicted'] is not None for r in unknown),
                'abstentions':len(rows)-named,
                'known_accuracy':correct/len(known) if known else None,
                'named_precision':correct/named if named else None,
                'named_coverage':named/len(rows) if rows else None}
    groups = {}
    for row in decisions:
        width = row['face_width_px']
        band = '<32' if width<32 else '32-63' if width<64 else '64-111' if width<112 else '112+'
        key = row['camera'] + ':' + band
        groups.setdefault(key, []).append(row)
    return {'overall':counts(decisions), 'camera_and_native_face_width':{k:counts(v) for k,v in groups.items()},
            'scope':'aligned crops only; no detection, tracking, temporal flicker, or production acceptance'}
