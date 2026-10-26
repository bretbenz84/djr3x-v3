"""Shared saved/live capture analysis. Providers return evidence, never identities."""
from dataclasses import dataclass, replace

from .contracts import Capture, Cancellation, Transcript, Cancelled
from .speakers import VoiceObservation, cosine


@dataclass(frozen=True)
class PerceivedTurn:
    capture: Capture
    transcript: Transcript
    voice: VoiceObservation
    faces: tuple[dict, ...] = ()


class Perception:
    def __init__(self, asr, encoder, trace, camera=None, direction=None):
        self.asr, self.encoder, self.trace, self.camera = asr, encoder, trace, camera
        self.direction = direction

    def analyze_many(self, capture, cancel: Cancellation):
        from .segmentation import change_boundaries, split_capture
        began = self.trace.clock()
        voice = self._voice(capture,cancel)
        seconds = self.trace.clock()-began
        cuts = change_boundaries(voice)
        if cuts:
            self.trace.emit("speaker_change_proposed",capture_id=capture.capture_id,
                            boundaries=cuts,verified=False,analysis_seconds=seconds)
            for part in split_capture(capture,cuts):
                cancel.check()
                yield self.analyze(part,cancel)
        else:
            yield self.analyze(capture,cancel,voice,seconds)

    def _voice(self,capture,cancel):
        try:
            voice = self.encoder.analyze(capture,cancel)
            # Motion lowers confidence in the tracker; the evidence is kept.
            return replace(voice, motion=True) if capture.motion_risk else voice
        except Cancelled:
            raise
        except Exception as exc:
            self.trace.emit("provider_failed",capability="voice_encoder",error_type=type(exc).__name__)
            return VoiceObservation(self.encoder.capability.model,reason="encoder_failed")

    def analyze(self, capture, cancel: Cancellation, voice=None, voice_seconds=None):
        began = self.trace.clock()
        transcript = self.asr.transcribe(capture, cancel)
        self.trace.emit("transcript", capture_id=capture.capture_id, original_text=transcript.heard_as or transcript.text,
            **({"corrected_text": transcript.text} if transcript.heard_as else {}),
            avg_logprob=transcript.avg_logprob, trusted=transcript.trusted,
            asr_seconds=self.trace.clock()-began, queue_seconds=max(0.,began-capture.ended_at))
        cancel.check()
        began = self.trace.clock()
        if voice is None:
            voice = self._voice(capture,cancel)
        cancel.check()
        self.trace.emit("speaker_analysis", capture_id=capture.capture_id,
            speaker_seconds=self.trace.clock()-began if voice_seconds is None else voice_seconds, namespace=voice.namespace,
            voiced_seconds=voice.voiced_seconds, windows=len(voice.windows),
            consistency=voice.consistency, reason=voice.reason,
            minimum_pair_similarity=min((cosine(a[2],b[2]) for i,a in enumerate(voice.windows)
                                         for b in voice.windows[i+1:]),default=None))
        faces = self.camera.during(capture.started_at,capture.speech_ended_at) if self.camera else ()
        if self.direction:
            evidence = self.direction.during(capture.started_at,capture.speech_ended_at)
            voice = replace(voice, direction=evidence,
                            direction_conflict=voice.direction_conflict or evidence['status']=='ambiguous')
            self.trace.emit('direction_evidence', capture_id=capture.capture_id, **evidence)
        return PerceivedTurn(capture,transcript,voice,tuple(faces))
