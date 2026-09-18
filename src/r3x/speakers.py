"""One acoustic tracker for enrolled people and unnamed session voices.

This is a bounded window-consistency baseline, not calibrated diarization or a
dedicated overlap detector. Cosines are similarities, never probabilities.
"""
from collections import deque
from dataclasses import dataclass, field
import math
import statistics
from uuid import uuid4


def unit(vector):
    if not vector or not all(math.isfinite(v) for v in vector):
        return None
    norm = math.sqrt(sum(v*v for v in vector))
    return tuple(v / norm for v in vector) if norm > 1e-10 else None


def cosine(a, b):
    if len(a) != len(b):
        return -1.
    return sum(x*y for x, y in zip(a, b))


def centroid(vectors):
    return unit(tuple(sum(row[i] for row in vectors)/len(vectors) for i in range(len(vectors[0]))))


def window_consistency(windows):
    """Compare each window with independent pooled evidence, preserving all windows.

    Also veto two internally coherent, separated temporal groups: pooling those
    would otherwise hide a speaker change behind a plausible average voice.
    """
    vectors = [w[2] for w in windows]
    if len(vectors)<2: return 1., False
    scores = []
    for i, vector in enumerate(vectors):
        other = centroid(vectors[:i]+vectors[i+1:])
        scores.append(cosine(vector,other) if other else -1.)
    for cut in range(2,len(vectors)-1):
        left,right = vectors[:cut],vectors[cut:]
        a,b = centroid(left),centroid(right)
        if (a and b and cosine(a,b)<.45 and
            min(cosine(v,a) for v in left)>=.75 and
            min(cosine(v,b) for v in right)>=.75):
            return min(scores), True
    return min(scores), False


@dataclass(frozen=True)
class VoiceObservation:
    namespace: str
    embedding: tuple[float, ...] | None = None
    windows: tuple[tuple[float, float, tuple[float, ...]], ...] = ()
    voiced_seconds: float = 0.
    consistency: float | None = None
    mixed: bool = False
    reason: str = "unmeasured"
    direction_conflict: bool = False
    direction: dict | None = None


@dataclass(frozen=True)
class Attribution:
    track_id: str | None = None
    person_id: str | None = None
    state: str = "unresolved"
    reason: str = "no_acoustic_evidence"
    candidates: tuple[tuple[str, float], ...] = ()
    learning_eligible: bool = False


@dataclass
class Track:
    track_id: str
    last_at: float
    references: deque = field(default_factory=lambda: deque(maxlen=5))
    person_id: str | None = None
    association: str = "unresolved"
    retained: bool = False


@dataclass(frozen=True)
class SpeakerPolicy:
    identity_threshold: float = .50
    margin: float = .07
    track_threshold: float = .55
    window_threshold: float = .45
    min_named_seconds: float = 1.
    min_learning_seconds: float = 1.
    track_expiry_seconds: float = 300.
    # Car-session calibration: weak continuity is observational only. It never
    # changes references, carries a name, or permits enrollment.
    continuity_threshold: float = .30
    continuity_margin: float = .12
    min_new_track_seconds: float = 1.


class SpeakerTracker:
    def __init__(self, namespace: str, policy: SpeakerPolicy | None = None):
        self.namespace, self.policy = namespace, policy or SpeakerPolicy()
        self.tracks: dict[str, Track] = {}

    def reset(self):
        self.tracks.clear()

    def forget(self, person_id: str):
        for track_id in list(self.tracks):
            if self.tracks[track_id].person_id == person_id:
                del self.tracks[track_id]

    def associate(self, track_id: str, person_id: str, supported: bool = False):
        track = self.tracks[track_id]
        if track.person_id not in (None, person_id):
            raise ValueError("ConflictingTrackAssociation")
        track.person_id = person_id
        track.association = "supported" if supported else "hypothesis"

    def rank(self, vector, profiles):
        scores = []
        for person_id, references in profiles.items():
            values = sorted((cosine(vector, v) for v in references), reverse=True)
            if values:
                scores.append((person_id, statistics.mean(values[:3])))
        return sorted(scores, key=lambda row: row[1], reverse=True)

    def observe(self, observation: VoiceObservation, at: float, profiles: dict,
                trusted: bool = True, echo: bool = False, truncated: bool = False) -> Attribution:
        p = self.policy
        self.tracks = {key: t for key, t in self.tracks.items() if t.retained or at - t.last_at <= p.track_expiry_seconds}
        vector = unit(observation.embedding)
        if observation.namespace != self.namespace or vector is None:
            return Attribution(reason="missing_or_wrong_namespace")
        if echo or observation.mixed or observation.direction_conflict:
            return Attribution(reason="echo_mixed_or_conflicting")
        if observation.consistency is not None and observation.consistency < p.window_threshold:
            return Attribution(reason="window_disagreement")
        ranked = self.rank(vector, profiles)
        alternatives = tuple(ranked[:3])
        if not trusted or truncated or observation.voiced_seconds < .5:
            return Attribution(reason="short_or_untrusted", candidates=alternatives)
        person_id = None
        if ranked and ranked[0][1] >= p.identity_threshold:
            if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < p.margin:
                return Attribution(reason="ambiguous_profiles", candidates=alternatives)
            # Each window must agree with the selected profile, not just the pooled voice.
            if any(not (r := self.rank(window[2], profiles)) or r[0][0] != ranked[0][0] or
                   r[0][1] < p.identity_threshold or (len(r) > 1 and r[0][1]-r[1][1] < p.margin)
                   for window in observation.windows):
                return Attribution(reason="window_identity_conflict", candidates=alternatives)
            if observation.voiced_seconds >= p.min_named_seconds:
                person_id = ranked[0][0]
        tracks = sorted(((key, statistics.mean(cosine(vector, ref) for ref in track.references))
                         for key, track in self.tracks.items()), key=lambda row: row[1], reverse=True)
        if (person_id is None and tracks and
                p.continuity_threshold <= tracks[0][1] < p.track_threshold):
            if len(tracks)>1 and tracks[0][1]-tracks[1][1] < p.continuity_margin:
                return Attribution(reason="ambiguous_continuity", candidates=alternatives)
            if self.tracks and observation.voiced_seconds < p.min_new_track_seconds:
                return Attribution(reason="short_continuity", candidates=alternatives)
            return Attribution(track_id=tracks[0][0],reason="tentative_continuity",
                               candidates=alternatives)
        if tracks and tracks[0][1] >= p.track_threshold:
            if len(tracks) > 1 and tracks[0][1] - tracks[1][1] < p.margin:
                return Attribution(reason="ambiguous_tracks", candidates=alternatives)
            track = self.tracks[tracks[0][0]]
            if person_id and track.person_id not in (None, person_id):
                return Attribution(reason="track_profile_conflict", candidates=alternatives)
            # The complete clip must meet .55. Individual phonetic windows may
            # be weaker, but none may contradict it or prefer a competing group.
            references = {key: t.references for key, t in self.tracks.items()}
            if any(not (r := self.rank(window[2], references)) or r[0][0] != track.track_id or
                   r[0][1] < .35 or (len(r) > 1 and r[0][1]-r[1][1] < p.margin)
                   for window in observation.windows):
                return Attribution(reason="window_track_conflict", candidates=alternatives)
            # An old named track does not manufacture enrolled acoustic support.
        else:
            if self.tracks and observation.voiced_seconds < p.min_new_track_seconds:
                return Attribution(reason="too_short_for_new_track", candidates=alternatives)
            if sum(at-t.last_at<=p.track_expiry_seconds for t in self.tracks.values()) >= 12:
                return Attribution(reason="track_capacity", candidates=alternatives)
            track = Track(uuid4().hex, at)
            self.tracks[track.track_id] = track
        track.last_at = at
        track.references.append(vector)
        if person_id:
            track.person_id, track.association = person_id, "supported"
        state = "supported" if person_id else "hypothesis" if track.person_id else "unresolved"
        eligible = (observation.voiced_seconds >= p.min_learning_seconds and
                    observation.consistency is not None and observation.consistency >= p.track_threshold)
        return Attribution(track.track_id, person_id, state, "acoustic_match" if person_id else "session_track",
                           alternatives, eligible)
