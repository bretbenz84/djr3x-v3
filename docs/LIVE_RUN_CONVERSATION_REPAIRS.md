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

## September 20: audience continuity and unheard-lull recovery

The solo script's user-run session completed its startup greeting but no later
proactive speech. Three ambient decisions chose silence, one lull retained a visual
description silently, and two lulls were cancelled by audience validation. The
operator confirmed that the audience did not change. Camera metadata showed a
recognized face replaced by an unresolved track, and later a brief additional
low-score face detection. These are false audience changes in that run, not evidence
that the user departed. No prompt rule can repair an opportunity cancelled before
the model is called.

Implemented in `proactive.py`, `runtime.py` and `session.py`:

- Select opportunities from the latest camera cache. Wait for the existing
  three-second stability interval to cover the full observed group before generating.
  A brief extra detection postpones dispatch without spending an attempt. Do not
  remove uncertain faces from privacy context or claim they are definitely false.
- Maintain bounded presence continuity separately from face identity evidence.
  For general lull/ambient speech, a new track can retain presence only with a unique
  matching recognized identity, spatial overlap above the existing tracker IoU gate,
  adjacent observations within the presence freshness window and unchanged epoch.
  Unknown/conflicting identities, ambiguous duplicates, gaps and spatial jumps do not
  inherit continuity. Targeted questions and tool operations still validate raw IDs.
- After a camera-only cancellation with no audio started, allow one fresh lull
  reconsideration per source turn: at least eight seconds after completion, expiring
  after 30 seconds. Keep attempts spent and the existing rolling cap. Another camera
  cancellation cannot rearm that recovery. Human speech/stop clears it; quiet, floor,
  pending questions, source validation and shutdown still take precedence.

No persona/schema instructions, acoustic/face recognition thresholds, models,
providers or settings were added or changed. Existing strict checks for new audiences
remain, including private speech during playback. Transient detections arriving
mid-generation can still cancel unheard work; recovery is deliberately bounded.
The unrelated missing/abandoned conversational intention was not automatically
recreated. The model still chooses whether to speak at an eligible opportunity.

**Verification:** 477 blocked-I/O checks pass (ten new tests plus updated scheduling
expectations). Synthetic production-runtime/Session/output cases cover stale polling,
brief extra detection and settled recovery, known-track continuity, unknown/conflict/
epoch/gap/departure rejection, one retry with fresh context, second cancellation,
quiet/floor/stop/input/call-budget/expiry gates, and private/targeted speech rejection
before and during playback. Temporary stores, scripted decisions and synthetic audio
only; no hardware or paid providers were started.

Read-only replay of the supplied log's camera metadata through the new audience
tracker found settled membership 4.02 and 4.51 seconds after the two original
cancellations. This checks the settling policy on recorded observations, not image
recognition, actual model decisions, playback or a complete live replay. New live
acceptance remains open; no personal state or original logs were changed.

### Short solo retest

No second person is needed. From the v3 checkout run:

```sh
.venv/bin/python -m r3x live --camera --record-evaluation
```

1. Wait for `Listening` and the greeting. Say: **“Rex, let's talk about ridiculous
   spaceships. Invent one that runs on toast.”**
2. After the reply ends, remain in view for 45 seconds. Move your head/look toward
   the laptop normally; no need to hold still. Note any spontaneous speech or silence.
3. Say: **“Rex, stop starting conversations until I tell you to start again.”**
   Wait 20 seconds. Say: **“Rex, what is seven plus five?”** Wait for the answer.
4. Say: **“Rex, you can start conversations again. Let's keep talking about that
   ridiculous spaceship.”** After his reply, remain in view for 45 seconds.
5. Type `quit` and share the printed session path plus what you heard.

Silence alone is not a failed scheduler test: inspect whether the model deliberately
chose it, the audience was still unsettled, or a cancelled opportunity failed to
recover. This retest also checks that quiet/direct replies still work. It does not
promise a spoken follow-up on every lull or establish two-person acceptance.

### Solo retest evidence

The subsequent user-run camera/audio session completed its greeting and two further
proactive audio contributions (one lull, one ambient). Five opportunities all reached
the model; none were audience-cancelled. Two later opportunities, after successful
quiet/resume, explicitly selected `remain_silent`. Camera metadata changed track/
identity membership five times during the run. No retry was needed, so live retry
recovery itself remains unexercised. This is a successful solo dispatch/playback
check, not complete social or group acceptance.

Quiet mode held between its successful application and explicit resume. No direct
question was transcribed during that quiet interval, so direct-answer-while-quiet
was not retested. All five speech deliveries completed, with zero reported underruns.
The two human-triggered audible replies took about 5.85 seconds median from end of
speech to first audio; this small sample remains slower than the desired roughly
four-second response. Proactive first audio followed opportunity creation by a
median 2.82 seconds across three spoken opportunities.

The two unsolicited contributions repeated substantially the same room/captain/
spaceship premise. No conversational intention was created. Repetition quality and
intentional follow-through remain separate open issues. `CameraStreamEnded` was
logged about 0.28 seconds after shutdown cancellation, not during an active exchange;
all lifecycle close operations reported success. Its shutdown classification has
not been repaired by this work. The original private trace remains under ignored
`local/sessions/`; no new providers or devices were operated during log review.

### September 28: noise yielded unheard replies

Session `7db71dda77e2445ea7daff18f4e8fb49`: Rex generated a reply to "Well, I turned you
on, so I would be the one in front of you" and yielded it before playback because a
single VAD frame followed. That noise, and three more, transcribed as untrusted "Oh.",
each yielding the next reply; "Did you hear me?" was lost the same way. Across the eight
most recent sessions every phantom "Oh."/"Okay." capture had at most 0.48 s voiced,
while real speech had at least 0.64 s. An unheard reply now yields only after 0.5 s of
voiced, non-echo speech in the resuming utterance. Brief sounds still become their own
turns and still mark activity for lull timing. Two new synthetic checks cover the
session contract and the runtime frame path; live behaviour with a real interruption
remains to be confirmed.
