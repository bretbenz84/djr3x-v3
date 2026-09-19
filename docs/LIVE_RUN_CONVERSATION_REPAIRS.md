# Latest-run conversation repairs

Inspected the latest live session, `local/sessions/16ef81c7b1e04255b0d10fcd7251d7cb.jsonl`.
This run used real devices, but this repair work did not start any device. Personal
logs and probe artifacts remain ignored under `local/`.

## Findings and changes

All six proactive turns were interrupted by audience validation; none finished as a
model decision to stay silent. Five invalidations occurred with the most recently
published face observation 1.001–1.035 seconds old. Face analysis was running at
roughly 2 Hz with about half a second of capture-to-publication delay, so the
one-second presence cutoff periodically discarded an unchanged visible visitor.
One other invalidation involved fresher evidence and is not attributed to that
threshold. Actual audience/identity changes still invalidate targeted output.

Camera latest-presence, arrival validation and ordinary current-view context now
share a **1.5-second** limit. This covers the measured analysis/publication interval
without changing capture timestamps, acoustic attribution, enrollment or face
matching thresholds. An explicit empty frame remains empty; old faces are not
resurrected. Truly stale camera evidence still blocks speech startup.

The first two ordinary intent-bearing greetings started audio but were interrupted
with no complete phrase and zero recorded underruns. The intent guard could cut
a general line mid-playback on the same camera expiry. General intent-bearing
speech now follows the existing ordinary-output rule: validate before playback,
then allow already-started words to finish through visual changes. Human input,
stop and shutdown still cancel. Targeted name questions, private memory references
and other audience-bound outputs retain their guard throughout playback. New
`intent_output_rejected` events make future audience cancellations explicit.

The transcript/model sequence also showed a dog/toy referent correction interpreted
as a toy noun and a later sound-alike treated as a new unrelated object. The shared
model instructions now distinguish transcript trust from guaranteed word accuracy,
use recent human corrections to resolve plausible sound-alikes, distinguish a pet
from its toy/name, and clarify material ambiguity. Missing image input is not proof
an object is absent: current visual claims require fresh inspection when available.
No lexical replacement list, new classifier, automatic biography write, or ASR
context echo prompt was added. Original transcripts remain unchanged.

## Verification and limits

**463 blocked-I/O tests pass**, including four new contracts: delayed camera
publication does not cancel a greeting; cache evidence still expires and explicit
empty frames stay empty; general spoken intent text survives a camera gap; targeted
questions still cancel when their audience leaves. Existing pre-playback audience,
human interruption, failure and privacy checks also pass.

Four hosted fictional text probes correctly handled animal-versus-toy correction,
a contextual sound-alike, an unrelated bird-name sound-alike, and an explicit real
topic change. These exercised the production hosted adapter with synthetic histories,
without camera/audio or identity tools; they are semantic development evidence,
not acoustic/visual accuracy or full live acceptance. The later explicit missing-image
instruction has contract-suite coverage but no additional hosted measurement.

The live run had `record_evaluation=false`. No saved evaluation waveforms are
available to establish the intended phonemes or measure an ASR improvement. The
ASR model is unchanged; these fixes improve downstream interpretation. Real playback,
startup greeting reliability and room transcription still need a subsequent live run.

Ignored evidence:
- `local/validation/live-run-repair-16ef81c7/review.json` and `contract-checks.log`
- `local/validation/conversation-repairs-86cb4bccdf30464395f10584f007d6d9.json`

Reproducible fictional semantic probe: `.venv/bin/python tools/check_transcript_context.py`.
The [next JSON scheduling implementation](NEXT_CONVERSATION_SCHEDULING.md) is documented
separately and remains unimplemented.
