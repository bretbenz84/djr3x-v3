"""Bounded visual evidence. Face recognition never votes on acoustic identity."""
from collections import deque
from dataclasses import dataclass, field, replace
from uuid import uuid4
from .speakers import cosine


@dataclass(frozen=True)
class FaceSample:
    vector: tuple[float, ...] | None
    aligned: bytes
    original: bytes
    frame_id: str
    camera_id: str
    session_id: str
    at: float
    width: float
    detector_score: float
    sharpness: float
    brightness: float
    transform: tuple[float, ...]
    namespace: str
    eligible: bool
    native_box: tuple[float,...] = ()
    source_dimensions: tuple[int,int] = (0,0)
    source_frame_sha256: str = ""


def iou(a,b):
    x,y = max(a[0],b[0]),max(a[1],b[1])
    width = max(0.,min(a[0]+a[2],b[0]+b[2])-x)
    height = max(0.,min(a[1]+a[3],b[1]+b[3])-y)
    intersect = width*height
    return intersect/max(1e-12,a[2]*a[3]+b[2]*b[3]-intersect)


def rank_face(vector, profiles):
    if vector is None: return []
    return sorted(((person, sum(cosine(vector,v) for v in refs)/len(refs))
                   for person,refs in profiles.items() if refs), key=lambda p:p[1], reverse=True)


@dataclass
class FaceTrack:
    track_id: str
    box: tuple[float,float,float,float]
    last_seen: float
    vector: tuple[float,...] | None = None
    samples: deque = field(default_factory=lambda:deque(maxlen=3))
    identity: str | None = None
    strong_at: float = float('-inf')
    candidate: str | None = None
    candidate_since: float = 0.
    first_seen: float | None = None


class FaceTracker:
    # Provisional EdgeFace operating points; requires robot-camera calibration.
    ACCEPT = .55
    MARGIN = .10
    CONTINUITY = .45

    def __init__(self):
        self.tracks = {}
        self.history = deque(maxlen=64)
        self.sample_history = deque()
        self.sample_bytes = 0
        self.epoch = 0
        self.last_at = float('-inf')

    def clear(self):
        self.epoch += 1
        self.tracks.clear()
        self.history.clear()
        self.sample_history.clear()
        self.sample_bytes = 0

    def update(self, boxes, at, samples=None, profiles=None):
        if at <= self.last_at: return ()
        self.last_at = at
        while self.sample_history and at-self.sample_history[0][1].at>32:
            self.sample_bytes -= self.sample_history.popleft()[2]
        profiles = profiles or {}
        self.tracks = {k:t for k,t in self.tracks.items() if 0 <= at-t.last_seen < 1.5}
        available = set(self.tracks)
        seen = []
        for index,box in enumerate(boxes[:12]):
            sample = samples[index] if samples is not None else None
            vector = sample.vector if sample else None
            scores = []
            for key in available:
                track = self.tracks[key]
                spatial = iou(box,track.box)
                compatible = vector is not None and track.vector is not None and cosine(vector,track.vector)>=self.CONTINUITY
                # With embeddings, position alone must never transfer identity.
                if spatial>.15 and (compatible or samples is None):
                    scores.append((spatial,key))
            scores.sort(reverse=True)
            key = scores[0][1] if scores and (len(scores)==1 or scores[0][0]-scores[1][0]>.1) else 'face-'+uuid4().hex
            available.discard(key)
            track = self.tracks.get(key) or FaceTrack(key,tuple(box),at)
            if track.first_seen is None:track.first_seen=at
            track.box,track.last_seen = tuple(box),at
            if vector is not None and (sample.eligible or track.vector is None): track.vector = vector
            if sample and sample.eligible and (not track.samples or at-track.samples[-1].at>=.8):
                if all(cosine(vector,s.vector)>=.6 for s in track.samples):
                    track.samples.append(replace(sample,aligned=b'',original=b''))
                    size = len(sample.aligned)+len(sample.original)+20000
                    self.sample_history.append((key,sample,size))
                    self.sample_bytes += size
                    while self.sample_history and (self.sample_bytes>16*1024*1024 or len(self.sample_history)>192 or at-self.sample_history[0][1].at>32):
                        self.sample_bytes -= self.sample_history.popleft()[2]
            ranked = rank_face(vector,profiles)
            score = ranked[0][1] if ranked else None
            margin = score-ranked[1][1] if len(ranked)>1 else (score+1 if ranked else None)
            candidate = ranked[0][0] if ranked and score>=self.ACCEPT and margin>=self.MARGIN else None
            if track.identity not in profiles: track.identity = None
            if candidate:
                if track.identity and candidate!=track.identity:
                    track.identity = None  # Conflict removes a name immediately; no sticky wrong name.
                if track.candidate!=candidate:
                    track.candidate,track.candidate_since = candidate,at
                elif at-track.candidate_since>=.8:
                    track.identity,track.strong_at = candidate,at
            else:
                track.candidate = None
                # Recency is bounded and only survives compatible evidence on this track.
                if at-track.strong_at>2. or (ranked and track.identity and
                    dict(ranked).get(track.identity,-1)<.4): track.identity = None
            self.tracks[key] = track
            center = box[0]+box[2]/2
            seen.append({'track_id':key,'face_epoch':self.epoch,'box':list(box),'at':at,'track_first_seen':track.first_seen,
                'position':'left' if center<.33 else 'right' if center>.67 else 'center',
                'identity':track.identity,'evidence':'face_match' if track.identity else 'face_unresolved',
                'similarity':score,'margin':margin,'face_width_px':sample.width if sample else None,
                'learning_quality':bool(sample and sample.eligible),
                'needs_closer_look':bool(sample and sample.width<96)})
        # Keep only tracks observed now or briefly absent. A finite crowd cannot accumulate hidden crops.
        self.tracks = dict(sorted(self.tracks.items(),key=lambda item:item[1].last_seen,reverse=True)[:12])
        self.history.append((at,tuple(seen)))
        return tuple(seen)

    def during(self, start, end):
        frames = [(at,rows) for at,rows in self.history if start<=at<=end]
        if not frames or end-frames[-1][0]>1.0: return ()
        result = {}
        all_tracks = set()
        for at,rows in frames:
            all_tracks.update(r['track_id'] for r in rows)
            for row in rows:
                result[row['track_id']] = {**row,'first_seen_in_capture':result.get(row['track_id'],{}).get('first_seen_in_capture',at)}
        answer = []
        latest_tracks={r['track_id'] for r in frames[-1][1]}
        crop_targets = {r['track_id'] for r in sorted(result.values(),key=lambda r:r['box'][2]*r['box'][3],reverse=True)[:3]}
        for row in result.values():
            if row['track_id'] not in latest_tracks:continue
            if end-row['at']>1.: continue
            evidence = tuple(s for key,s,_ in self.sample_history if key==row['track_id'] and start<=s.at<=end)[-3:] if row['track_id'] in crop_targets else ()
            answer.append({**row,'sole_face_throughout':len(all_tracks)==1,
                'newly_presented':row.get('track_first_seen',float('-inf'))>start+.5,
                '_samples':evidence})
        return tuple(answer)
