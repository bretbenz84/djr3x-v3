"""Conversational enrollment chains owned by the core state owner."""
from dataclasses import dataclass, field
import hashlib

from .speakers import centroid, cosine


@dataclass
class Enrollment:
    person_id: str
    track_id: str
    anchor: tuple[float, ...]
    created_at: float
    samples: list = field(default_factory=list)
    digests: set = field(default_factory=set)
    last_reason: str = "waiting_for_qualifying_speech"

    def add(self, capture, transcript, observation, attribution, turn_id):
        self.last_reason = "quality_or_attribution_not_eligible"
        if (attribution.track_id != self.track_id or not attribution.learning_eligible or
                not transcript.trusted or capture.echo_risk or capture.truncated or capture.parent_capture_id is not None or
                observation.mixed or observation.direction_conflict or
                observation.voiced_seconds < 2 or len(transcript.text.split()) < 4 or
                observation.voiced_seconds>len(capture.pcm)/(2*capture.sample_rate)+.032):
            return False
        if cosine(self.anchor, observation.embedding) < .5:
            self.last_reason = "anchor_disagreement"
            return False
        if self.samples and cosine(centroid([s[2].embedding for s in self.samples]), observation.embedding) < .55:
            self.last_reason = "cluster_disagreement"
            return False
        digest = hashlib.sha256(capture.pcm).hexdigest()
        if digest in self.digests or len(self.samples) >= 6:
            self.last_reason = "duplicate_or_capacity"
            return False
        self.digests.add(digest)
        self.samples.append((capture,transcript,observation,turn_id))
        self.last_reason = "sample_qualified"
        return True

    @property
    def ready(self):
        return len(self.samples) >= 3 and sum(s[2].voiced_seconds for s in self.samples) >= 8
