"""Interval-bound face presence only. No landmarks, mouth data, or voice voting."""
from collections import deque
from dataclasses import dataclass
from uuid import uuid4


def iou(a,b):
    x,y = max(a[0],b[0]),max(a[1],b[1])
    width = max(0.,min(a[0]+a[2],b[0]+b[2])-x)
    height = max(0.,min(a[1]+a[3],b[1]+b[3])-y)
    intersect = width*height
    return intersect/max(1e-12,a[2]*a[3]+b[2]*b[3]-intersect)


@dataclass
class FaceTrack:
    track_id: str
    box: tuple[float,float,float,float]
    last_seen: float


class FaceTracker:
    def __init__(self):
        self.tracks = {}
        self.history = deque(maxlen=64)

    def update(self, boxes, at):
        self.tracks = {k:t for k,t in self.tracks.items() if 0 <= at-t.last_seen < 1.5}
        available = set(self.tracks)
        seen = []
        for box in boxes[:12]:
            scores = sorted(((iou(box,self.tracks[k].box),k) for k in available),reverse=True)
            # Ambiguous crossings create a fresh visual track, never a confident merge.
            key = scores[0][1] if scores and scores[0][0]>.3 and (len(scores)==1 or scores[0][0]-scores[1][0]>.1) else "face-"+uuid4().hex
            available.discard(key)
            self.tracks[key] = FaceTrack(key,tuple(box),at)
            center = box[0]+box[2]/2
            seen.append({"track_id":key,"box":list(box),"at":at,
                         "position":"left" if center<.33 else "right" if center>.67 else "center",
                         "identity":None,"evidence":"face_presence_only"})
        self.history.append((at,tuple(seen)))
        return tuple(seen)

    def during(self, start, end):
        # No substitution of a previous frame when current interval evidence is missing.
        frames = [(at,rows) for at,rows in self.history if start<=at<=end]
        if not frames or end-frames[-1][0]>1.0:
            return ()
        result = {}
        for at,rows in frames:
            for row in rows:
                result[row["track_id"]] = {**row,"first_seen_in_capture":result.get(row["track_id"],{}).get("first_seen_in_capture",at)}
        return tuple(row for row in result.values() if end-row["at"]<=1.0)
