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
    motion: bool = False   # Servo/base motion overlapped the speech: lower confidence, never discard.


# Owner ruling, September 27: motion lowers confidence rather than discarding evidence.
MOTION_THRESHOLD_BONUS = .05
MOTION_MARGIN_BONUS = .03


@dataclass(frozen=True)
class Attribution:
    track_id: str | None = None
    person_id: str | None = None
    state: str = "unresolved"
    reason: str = "no_acoustic_evidence"
    candidates: tuple[tuple[str, float], ...] = ()
    learning_eligible: bool = False
    # The state owner's presence deduction (state "inferred"): never acoustic identity, never
    # learning or identity administration. person_id stays the acoustic match alone.
    inferred_person_id: str | None = None

    @property
    def speaker_id(self):
        """Who is talking for conversation, claims and memory: acoustic, else inferred."""
        return self.person_id or self.inferred_person_id


@dataclass
class Track:
    track_id: str
    last_at: float
    references: deque = field(default_factory=lambda: deque(maxlen=5))
    person_id: str | None = None
    association: str = "unresolved"
    retained: bool = False
    voiced_seconds: float = 0.


@dataclass(frozen=True)
class SpeakerPolicy:
    identity_threshold: float = .50
    margin: float = .07
    track_threshold: float = .55
    window_threshold: float = .45
    # October 14 live traces: 21 of Bret's turns (2-15 s, pooled .54-.68) were rejected because
    # one phonetic window fell under .50. Windows may be weaker than the clip, as on the track
    # path, but none may fall below this or prefer someone else. Growth still needs every window.
    window_identity_floor: float = .35
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
        # The last observed clip against each session voice: diagnostics for the log only
        # (November 10, 968a2b65: four "tentative_continuity" clips left no number to calibrate by).
        self.last_track_scores: tuple[dict, ...] = ()

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

    def voice_of(self, track, profiles):
        """The enrolled person a session track sounds like: its label, else the clear best
        profile for its pooled references. Used to tell one voice from two, never to name."""
        if track.person_id:
            return track.person_id
        pooled = centroid(list(track.references)) if track.references else None
        ranked = self.rank(pooled, profiles) if pooled else []
        if ranked and ranked[0][1] >= self.policy.continuity_threshold and (
                len(ranked) < 2 or ranked[0][1] - ranked[1][1] >= self.policy.margin):
            return ranked[0][0]
        return None

    def _one_voice(self, tracks, margin, profiles):
        """The best track, unless a close runner-up is a different voice. October 14 (2859a4e6):
        an unlabeled first clip and Bret's recognized track split one voice, and five of his
        turns were "ambiguous" between them. Of two tracks of one voice, the named one wins."""
        if len(tracks) < 2 or tracks[0][1] - tracks[1][1] >= margin:
            return tracks[0][0]
        a, b = self.tracks[tracks[0][0]], self.tracks[tracks[1][0]]
        voice = self.voice_of(a, profiles)
        if voice is None or voice != self.voice_of(b, profiles):
            return None
        return b.track_id if b.person_id and not a.person_id else a.track_id

    def other_voices(self, person_id, profiles, since, min_seconds=1.5):
        """Session voices heard since `since`, with real speech, that do not sound like this person."""
        return [key for key, track in self.tracks.items()
                if track.last_at >= since and track.voiced_seconds >= min_seconds
                and self.voice_of(track, profiles) != person_id]

    def rank(self, vector, profiles):
        scores = []
        for person_id, references in profiles.items():
            values = sorted((cosine(vector, v) for v in references), reverse=True)
            if values:
                scores.append((person_id, statistics.mean(values[:3])))
        return sorted(scores, key=lambda row: row[1], reverse=True)

    def _profile_match(self, observation, profiles, trusted, echo, truncated):
        """The enrolled person this clip clearly is: (vector, person_id or None, alternatives,
        windows_clear), or an Attribution saying why no profile can be named from it."""
        p = self.policy
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
        threshold = p.identity_threshold + (MOTION_THRESHOLD_BONUS if observation.motion else 0.)
        margin = p.margin + (MOTION_MARGIN_BONUS if observation.motion else 0.)
        person_id = None
        windows_clear = True
        if ranked and ranked[0][1] >= threshold:
            if len(ranked) > 1 and ranked[0][1] - ranked[1][1] < margin:
                return Attribution(reason="ambiguous_profiles", candidates=alternatives)
            # Each window must agree with the selected profile, not just the pooled voice.
            window_ranks = [self.rank(window[2], profiles) for window in observation.windows]
            if any(not r or r[0][0] != ranked[0][0] or r[0][1] < p.window_identity_floor or
                   (len(r) > 1 and r[0][1]-r[1][1] < margin) for r in window_ranks):
                return Attribution(reason="window_identity_conflict", candidates=alternatives)
            windows_clear = all(r[0][1] >= threshold for r in window_ranks)
            if observation.voiced_seconds >= p.min_named_seconds:
                person_id = ranked[0][0]
        return vector, person_id, alternatives, windows_clear

    def match(self, observation: VoiceObservation, profiles: dict, trusted: bool = True,
              echo: bool = False, truncated: bool = False) -> tuple[str | None, str]:
        """Who this clip clearly is among saved voices, by the same tests that name a speaker in
        observe, without touching session tracks: (person_id, reason) or (None, reason). Sentry mode
        (October 22) stands down for a clear match only; it never learns from what it hears."""
        matched = self._profile_match(observation, profiles, trusted, echo, truncated)
        if isinstance(matched, Attribution):
            return None, matched.reason
        return matched[1], "acoustic_match" if matched[1] else "no_clear_profile_match"

    def observe(self, observation: VoiceObservation, at: float, profiles: dict,
                trusted: bool = True, echo: bool = False, truncated: bool = False) -> Attribution:
        p = self.policy
        self.last_track_scores = ()
        self.tracks = {key: t for key, t in self.tracks.items() if t.retained or at - t.last_at <= p.track_expiry_seconds}
        matched = self._profile_match(observation, profiles, trusted, echo, truncated)
        if isinstance(matched, Attribution):
            return matched
        vector, person_id, alternatives, windows_clear = matched
        if observation.motion:
            # Match against what is known, but servo noise never becomes a track
            # reference or a new voice group.
            track = next((t for t in self.tracks.values() if person_id and t.person_id == person_id), None)
            if track is not None:
                track.last_at = at
            return Attribution(track.track_id if track else None, person_id,
                               "supported" if person_id else "unresolved",
                               "acoustic_match_during_motion" if person_id else "motion_low_confidence",
                               alternatives, False)
        tracks = sorted(((key, statistics.mean(cosine(vector, ref) for ref in track.references))
                         for key, track in self.tracks.items()), key=lambda row: row[1], reverse=True)
        self.last_track_scores = tuple(
            {'track_id': key, 'similarity': round(score, 3), 'references': len(self.tracks[key].references),
             'voiced_seconds': round(self.tracks[key].voiced_seconds, 2), 'person_id': self.tracks[key].person_id}
            for key, score in tracks[:3])
        if (person_id is None and tracks and
                p.continuity_threshold <= tracks[0][1] < p.track_threshold):
            chosen = self._one_voice(tracks, p.continuity_margin, profiles)
            if chosen is None:
                return Attribution(reason="ambiguous_continuity", candidates=alternatives)
            if self.tracks and observation.voiced_seconds < p.min_new_track_seconds:
                return Attribution(reason="short_continuity", candidates=alternatives)
            return Attribution(track_id=chosen,reason="tentative_continuity",
                               candidates=alternatives)
        if tracks and tracks[0][1] >= p.track_threshold:
            chosen = self._one_voice(tracks, p.margin, profiles)
            if chosen is None:
                return Attribution(reason="ambiguous_tracks", candidates=alternatives)
            track = self.tracks[chosen]
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
        elif person_id and (own := next((key for key, _ in tracks if self.tracks[key].person_id == person_id), None)):
            # A recognized voice continues that person's track even when this clip sounds
            # unlike it; a second track for the same person made later clips ambiguous.
            track = self.tracks[own]
        else:
            if self.tracks and observation.voiced_seconds < p.min_new_track_seconds:
                return Attribution(reason="too_short_for_new_track", candidates=alternatives)
            if sum(at-t.last_at<=p.track_expiry_seconds for t in self.tracks.values()) >= 12:
                return Attribution(reason="track_capacity", candidates=alternatives)
            track = Track(uuid4().hex, at)
            self.tracks[track.track_id] = track
        track.last_at = at
        track.references.append(vector)
        track.voiced_seconds += observation.voiced_seconds
        if person_id:
            track.person_id, track.association = person_id, "supported"
        state = "supported" if person_id else "hypothesis" if track.person_id else "unresolved"
        eligible = (observation.voiced_seconds >= p.min_learning_seconds and windows_clear and
                    observation.consistency is not None and observation.consistency >= p.track_threshold)
        return Attribution(track.track_id, person_id, state, "acoustic_match" if person_id else "session_track",
                           alternatives, eligible)
