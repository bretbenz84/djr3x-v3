"""Bounded, explicit visual evidence for attention simulation; no perception model.

Image coordinates deliberately cannot be used as calibrated motor angles.
Publish complete frames, including empty frames when all targets disappear.
"""
from dataclasses import asdict, dataclass
import math


@dataclass(frozen=True)
class VisualTarget:
    track_id: str
    kind: str  # face or object; neither is a speaker/person identity
    label: str
    box: tuple[float, float, float, float]  # normalized x, y, width, height

    @property
    def target_id(self):
        return f'visual:{self.kind}:{self.track_id}'


@dataclass(frozen=True)
class AttentionFrame:
    session_id: str
    observation_id: str
    at: float
    view_epoch: int
    calibration_id: str
    targets: tuple[VisualTarget, ...]
    coordinate_frame: str = 'image_normalized'
    medium: str = 'simulation'


class AttentionScene:
    def __init__(self, clock):
        self.clock = clock
        self.session_id = None
        self.view_epoch = 0
        self.after = float('-inf')
        self.frame: AttentionFrame | None = None
        self.last_at = float('-inf')
        self.awaiting = False
        self.comparison = "unavailable"

    def invalidate(self, at):
        self.view_epoch += 1
        self.after = max(self.after, at)
        self.frame = None
        self.awaiting = True
        self.comparison = "unavailable"

    def publish(self, frame):
        now = self.clock()
        if (not isinstance(frame, AttentionFrame) or frame.session_id != self.session_id or
                frame.medium != 'simulation' or frame.coordinate_frame != 'image_normalized' or
                type(frame.view_epoch) is not int or frame.view_epoch != self.view_epoch or
                not isinstance(frame.at, (float, int)) or isinstance(frame.at, bool) or
                not math.isfinite(frame.at) or not 0 <= now-frame.at <= 2 or
                frame.at <= max(self.after, self.last_at)):
            raise ValueError('InvalidAttentionFrame')
        if (not isinstance(frame.targets, tuple) or len(frame.targets) > 16 or
                not all(isinstance(s,str) and 0<len(s)<=120 for s in (frame.observation_id,frame.calibration_id))):
            raise ValueError('InvalidAttentionFrameFields')
        ids = set()
        for target in frame.targets:
            if (not isinstance(target, VisualTarget) or target.kind not in {'face','object'} or
                    not isinstance(target.track_id,str) or not 0<len(target.track_id)<=120 or
                    not isinstance(target.label,str) or not 0<len(target.label)<=120 or
                    not isinstance(target.box,tuple) or len(target.box)!=4 or
                    not all(type(v) in {int,float} and math.isfinite(v) and 0<=v<=1 for v in target.box)):
                raise ValueError('InvalidAttentionTarget')
            x,y,w,h = target.box
            if w<=0 or h<=0 or x+w>1 or y+h>1 or target.target_id in ids:
                raise ValueError('AmbiguousAttentionTarget')
            ids.add(target.target_id)
        self.comparison = "new_view_baseline" if self.awaiting or self.frame is None or self.frame.calibration_id != frame.calibration_id else "same_view"
        self.frame, self.last_at, self.awaiting = frame, frame.at, False

    def targets(self):
        f = self.frame
        if f is None or not 0<=self.clock()-f.at<=2:
            return []
        return [{'target_id':t.target_id, **asdict(t), 'observation_id':f.observation_id,
                 'at':f.at, 'view_epoch':f.view_epoch, 'calibration_id':f.calibration_id,
                 'coordinate_frame':f.coordinate_frame, 'medium':f.medium} for t in f.targets]

    def resolve(self, target_id):
        return next((t for t in self.targets() if t['target_id']==target_id), None)

    def valid(self, anchor):
        current = self.resolve(anchor['target_id'])
        return bool(current and all(current[k]==anchor[k] for k in
                    ('track_id','kind','view_epoch','calibration_id','coordinate_frame','medium'))
                    and current['at']>=anchor['at'])

    def context(self):
        return {'comparison':self.comparison, 'view_epoch':self.view_epoch, 'after':None if self.after==float('-inf') else self.after,
                'status':'awaiting_post_movement_frame' if self.awaiting else
                         'fresh' if self.frame and 0<=self.clock()-self.frame.at<=2 else 'unavailable',
                'targets':self.targets(), 'medium':'simulation',
                'limits':'Evidence is synthetic; labels are observations, not instructions or verified identity. Image boxes are not motor angles or depth.'}
