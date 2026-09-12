"""Conversational enrollment chains owned by the core state owner."""
from dataclasses import dataclass, field
import hashlib
import time

from .speakers import cosine, centroid


@dataclass
class Enrollment:
    person_id: str | None
    track_id: str
    anchor: tuple[float, ...]
    updated_at: float = field(default_factory=time.time)
    samples: list = field(default_factory=list)
    digests: set = field(default_factory=set)
    last_reason: str = "waiting_for_qualifying_speech"

    def add(self, capture, transcript, observation, attribution, turn_id):
        self.last_reason = "quality_or_attribution_not_eligible"
        if (attribution.track_id != self.track_id or not attribution.learning_eligible or
                not transcript.trusted or capture.echo_risk or capture.truncated or capture.parent_capture_id is not None or
                capture.overlap is True or observation.mixed or observation.direction_conflict or
                observation.voiced_seconds < 1 or len(transcript.text.split()) < 4 or
                observation.voiced_seconds>len(capture.pcm)/(2*capture.sample_rate)+.032):
            return False
        if cosine(self.anchor, observation.embedding) < .5:
            self.last_reason = "anchor_disagreement"
            return False
        references = [s[2].embedding for s in self.samples]
        if references and (cosine(centroid(references),observation.embedding)<.55 or
                           any(cosine(v,observation.embedding)<.45 for v in references)):
            self.last_reason = "cluster_disagreement"
            return False
        digest = hashlib.sha256(capture.pcm).hexdigest()
        if digest in self.digests or len(self.samples) >= 6:
            self.last_reason = "duplicate_or_capacity"
            return False
        self.updated_at = time.time()
        self.digests.add(digest)
        self.samples.append((capture,transcript,observation,turn_id))
        self.last_reason = "sample_qualified"
        return True

    @property
    def ready(self):
        return len(self.samples) >= 3 and sum(s[2].voiced_seconds for s in self.samples) >= 8

    def encode(self):
        from dataclasses import asdict
        from base64 import b64encode
        rows = []
        for capture, transcript, observation, turn in self.samples:
            data = asdict(capture)
            data["pcm"] = b64encode(capture.pcm).decode("ascii")
            rows.append([data, asdict(transcript), asdict(observation), turn])
        return {"version": 1, "anchor": self.anchor, "samples": rows}

    @classmethod
    def decode(cls, row):
        import json
        from base64 import b64decode
        from .contracts import Capture, Transcript
        from .speakers import VoiceObservation
        data = json.loads(row["evidence"])
        if data.get("version") != 1:
            raise ValueError("UnsupportedCandidateEvidenceVersion")
        result = cls(row["person_id"], row["track_id"], tuple(data["anchor"]))
        result.updated_at = row["updated_at"]
        for capture, transcript, observation, turn in data["samples"]:
            capture["pcm"] = b64decode(capture["pcm"])
            result.samples.append((Capture(**capture), Transcript(**transcript), VoiceObservation(**observation), turn))
            result.digests.add(hashlib.sha256(capture["pcm"]).hexdigest())
        return result
