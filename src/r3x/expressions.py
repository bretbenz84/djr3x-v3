"""What a face is showing, from the reader's blendshape scores. No model and no device here.

V2 (vision/face_expression.py, consciousness.py) learned two things this keeps. MediaPipe
over-reads mouthSmile on a resting face, most of all when the camera looks up at someone, so a
look fires only on a rise above that face's own resting level as well as an absolute floor. And
a smile is a moment: it must hold for two readings (one second at the 2 Hz face pass) before it
counts. V2's fourth look, the furrowed brow, is left out: on the robot's own footage (October 22
sentry clip, 159 readings) a resting browDown read a median .56 and .71 at the 90th percentile,
so it fired four times in 149 seconds on nothing anyone could act on.

A look is evidence for the conversation only: it goes in the picture of the present and the
session log. It never reaches speaker attribution, enrollment, identity or saved memory.
"""
from dataclasses import dataclass, field
from uuid import uuid4

from .faces import iou

# Absolute floor a look must reach, and how far above the resting face (V2's tuned defaults;
# frown and surprise had no baseline in V2 and use the same mechanism here).
FLOOR = {'smile': .50, 'surprise': .40, 'frown': .35}
RISE = {'smile': .22, 'surprise': .20, 'frown': .15}
# Peak a look must reach to be mentioned when no line of Rex's prompted it (V2's bar for
# unprompted reactions; a smile right after his line needs only FLOOR and RISE).
UNPROMPTED = {'smile': .70, 'surprise': .50, 'frown': .55}
HOLD_READS = 2       # Readings a look must hold to count; about one second.
END_READS = 2        # Readings without it before the look is over.
# Resting level, per face: V2's 4 Hz constants restated for the 2 Hz pass. Follows a relaxing
# face fast and a rising one slowly, so a real smile stays a spike above the floor; a smile
# held for half a minute becomes the new resting level and stops being news.
REST_DOWN, REST_UP = .36, .04
WARMUP_READS = 8     # Until the resting level is known, only the UNPROMPTED bar counts.
FORGET_SECONDS = 8.  # Out of view this long, the face's resting level is forgotten.
LOST_SECONDS = 1.5   # Unread this long, a look in progress is over.


def looks(scores):
    """The looks' scores from raw blendshapes; surprise is V2's mean of eyes, jaw and brow."""
    def mean(*keys):
        return sum(max(0., min(1., float(scores.get(key) or 0.))) for key in keys) / len(keys)
    return {'smile': mean('mouthSmileLeft', 'mouthSmileRight'),
            'surprise': (mean('eyeWideLeft', 'eyeWideRight') + mean('jawOpen') + mean('browInnerUp')) / 3,
            'frown': mean('mouthFrownLeft', 'mouthFrownRight')}


@dataclass
class Watch:
    box: tuple
    identity: str | None
    seen: float
    rest: dict = field(default_factory=dict)
    reads: int = 0
    read_at: float = float('-inf')
    pending: str | None = None
    pending_since: float = 0.
    pending_reads: int = 0
    look: str | None = None
    since: float = 0.
    moment: str | None = None
    peak: float = 0.
    missing_reads: int = 0


class ExpressionWatch:
    """Per-face resting levels and current looks. Boxes and names link readings because face
    tracks renumber far too often (about 32 new ones a minute) to hold a resting level."""

    def __init__(self):
        self.watches = []
        self.frame_at = None
        self.used = set()

    def clear(self):
        self.watches = []
        self.frame_at = None
        self.used = set()

    def _find(self, box, identity, at):
        if at != self.frame_at:
            self.frame_at, self.used = at, set()
            self.watches = [w for w in self.watches if at - w.seen <= FORGET_SECONDS]
        free = [w for w in self.watches if id(w) not in self.used]
        watch = next((w for w in free if identity and w.identity == identity), None)
        if watch is None:
            # A box never carries a look across to a different recognized person.
            near = [(iou(box, w.box), i) for i, w in enumerate(free)
                    if not (identity and w.identity and w.identity != identity)]
            score, index = max(near, default=(0., -1))
            if score >= .1:
                watch = free[index]
        if watch is None:
            watch = Watch(tuple(box), identity, at)
            self.watches.append(watch)
        self.used.add(id(watch))
        watch.box, watch.seen = tuple(box), at
        watch.identity = identity or watch.identity
        return watch

    def _above(self, watch, scores):
        """The look furthest above its bar, or None."""
        warmed = watch.reads >= WARMUP_READS
        best, margin = None, 0.
        for look, score in scores.items():
            bar = max(FLOOR[look], watch.rest[look] + RISE[look]) if warmed else UNPROMPTED[look]
            if score >= bar and (best is None or score - bar > margin):
                best, margin = look, score - bar
        return best

    def observe(self, box, identity, blendshapes, at):
        """One face in one frame; blendshapes None when it could not be read. Returns the face
        row's fields: nothing for a resting face, which is most of them."""
        watch = self._find(box, identity, at)
        scores = None
        if blendshapes is not None:
            scores = looks(blendshapes)
            self._read(watch, scores, at)
        elif watch.look and at - watch.read_at > LOST_SECONDS:
            watch.look = watch.moment = None
        row = {}
        if scores is not None:   # Logged for calibration; never sent to the model (field_guide.slim).
            row['expression_read'] = {k: round(v, 3) for k, v in scores.items()}
        if watch.look:
            row.update(expression=watch.look, expression_since=watch.since, expression_moment=watch.moment,
                       expression_unprompted=watch.peak >= UNPROMPTED[watch.look])
        return row

    def _read(self, watch, scores, at):
        for look, score in scores.items():
            rest = watch.rest.get(look, score)
            watch.rest[look] = rest + (REST_DOWN if score < rest else REST_UP) * (score - rest)
        top = self._above(watch, scores)
        watch.reads += 1
        watch.read_at = at
        if watch.look and top == watch.look:
            watch.missing_reads = 0
            watch.peak = max(watch.peak, scores[top])
        elif watch.look:
            watch.missing_reads += 1
            if watch.missing_reads >= END_READS:
                watch.look = watch.moment = None
        if watch.look is None:
            if top and top == watch.pending:
                watch.pending_reads += 1
            else:
                watch.pending, watch.pending_since, watch.pending_reads = top, at, 1 if top else 0
            if top and watch.pending_reads >= HOLD_READS:
                watch.look, watch.since, watch.moment = top, watch.pending_since, uuid4().hex
                watch.peak, watch.missing_reads, watch.pending = scores[top], 0, None
