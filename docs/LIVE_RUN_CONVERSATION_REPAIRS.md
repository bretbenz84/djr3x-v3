# Latest-run conversation repairs

Current repair: [September 29 engagement](#september-29-engagement-repair).
Earlier sections retain the evidence and policy at their dates.

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

### September 28 (second run): replies to untrusted audio

Session `af692e206e1e4716a86936530266b9c8`, with a TV in the next room. The prompt already
said never to reply to untrusted transcripts, but Rex answered "Oh.", "Yeah.", "I don't
want to see you cry." and "Emma." ("Which one is Emma?"), often in two sentences each
about lag. An untrusted transcript now stays in history without a model call or reply,
unless a trusted human turn was yielded before Rex spoke; then the model is called once
to answer that owed question. Replies were still yielded to sustained audio: Bret
repeating himself during an 8 s photo-memory turn, and a 9 s TV stretch (transcribed as
"Wow." x256, untrusted). Sustained trusted-sounding TV speech ("Moving closer and
tracking.") is still treated as a person; separating it needs speaker/direction work.
The "Max" save used the stably detected dog, which Rex had just called Toby; the other
dog was detected in only a few frames. The saved record was not changed. Synthetic checks
only; no hardware or hosted model was run for this repair.

## September 29 engagement repair

Read-only review of the latest 811-second session found 13 proactive attempts:
two completed, five model-selected silences and six interruptions. Two direct
greetings were lost to intention/audience checks. A rejected memory write received
an empty continuation. Twenty rejected transcripts also occupied conversation
history and reset lull timing. The two completed proactive remarks reused a visual
premise. These are distinct failures; changing the speaker model was not indicated
by this evidence.

Implemented through the existing state, Session, scheduler and output owners:

- Untrusted input retains its original source archive and trace, but no longer
  evicts dialogue, replaces the last trusted lull source or resets the lull clock.
  The existing owed-answer recovery remains. Pending input and recognition still
  prevent overlapping a human turn; unresolved activity has bounded quiet pacing.
- Ongoing trusted conversation can reconsider lulls without an intention, or when
  camera changes suspended it. Current audience validation, explicit quiet/floor
  boundaries, pending questions, 8/15/30/60-second backoff and the shared six-call
  rolling budget remain. No old intention is automatically reactivated or exposed
  to a changed group. Model-selected JSON scheduling remains unimplemented.
- A general direct reply is not withheld by its optional intention. Audience change
  drops the note; targeted questions and memory-bearing updates retain validation.
  General proactive arrival greetings accept the existing supported, spatially
  continuous recognized-face track replacement. Actual group changes still reject.
- Tool-result requests explicitly require a final spoken answer even on rejection.
  An empty continuation gets one traced, tools-disabled recovery using the same
  result, without re-executing the operation. A second empty answer fails explicitly.
  Ordinary replies still use one primary generation.
- Lull context explicitly includes the last completed reply. Prompt guidance asks
  for substantive curiosity, distinct ideas rather than reworded jokes, an actual
  change of subject when requested, and engagement rather than explanations of quiet.
  The unanswered-question rule remains: one short aside, then wait for a change.

**Verification: 765 blocked-I/O checks pass**, including nine new regressions for
noise archival/history/timing, trusted short replies, unresolved capture, continuing
lulls and the call cap, rejected-write recovery, bounded/cancelled recovery, targeted
question rejection and recognized arrival continuity. Existing camera-suspension
and ordinary-intention tests now assert the revised contracts. Temporary databases,
fake clocks/providers and synthetic audio only; no devices were operated.

`tools/check_engagement_repair.py` runs fictional dialogue through production Session
and the idle scheduler, with a fixed simulation clock and synthetic playback.
The final run made seven hosted calls: it changed subjects, advanced a different
aspect of a discussion, continued after an invitation, did not repeat either
unanswered question, honored quiet and answered a rejected save without claiming
success. The human-floor case was withheld by the scheduler (not a hosted silence).
No empty-response recovery was needed in this hosted run; that branch has synthetic
contract coverage. Initial development probes exposed topic/question repetition and
led to the revised prompt/context; these are development cases, not held-out scores.

Final hosted usage: 84,701 input tokens (70,912 cached), 904 output tokens. Six timed
cases had 1.80 s median / 2.35 s maximum wall time; the additional follow-up was not
timed separately. These are hosted-plus-simulated-output times, not real first-audio
latency or a reliable tail estimate. No local inference models were loaded; RSS,
physical playback, acoustic accuracy and real-room engagement were not measured.
Private receipts: `local/validation/engagement-761a64a5e2c645a8a4169d8e67abf6f4/`.

Live acceptance remains: normal head movement during greetings; quiet intervals
with motor noise; a personal disclosure whose memory write is ineligible; a requested
topic change; and several minutes of voluntary conversation without repeated premises.
Recognition-to-unknown face churn, trusted background TV speech, the rolling call cap
and semantic variability can still affect a live run. No personal state was changed
and no robot process was restarted during this repair.

## September 29 startup and playback cutoff repair

The next 166-second live run exposed a separate timing failure: the
arrival greeting was cancelled with `audio_timestamp_ahead` when the latest
microphone timestamp was 37 ms ahead of the scheduler clock. First TTS PCM had
arrived, but no playback-start receipt was recorded. The operator heard its onset;
logs alone cannot establish the amount played. Initial rejected noise had also
removed the startup-greeting requirement by advancing the raw speech marker.

The runtime now bounds microphone liveness by frame receipt time, preserving
original ADC timestamps for capture/attribution and retaining stale-input guards.
The device callback latches the first speech PCM submission before the delayed
producer-thread start notification. Session and opportunity guards share that
commitment, so resumed activity or camera freshness cannot cut already-started
speech in the notification gap. Explicit cancellation still interrupts active
speech; interruption retains a start receipt, while already-drained audio retains
its completion receipt. Heavy output/motion callbacks remain outside the realtime
callback. Effect audio does not commit a spoken reply.
Startup greeting eligibility now uses trusted dialogue and delivered proactive
speech, rather than rejected microphone activity.

The one-word reply answered a trusted ASR transcript of “Okay” which the operator
said they had not spoken. It had only one 32 ms VAD-positive block, but retained
preroll and endpoint-tail audio; this is not a 32 ms recording. Motion risk was
false and the microphone-hold flag was true. These flags describe commanded motion
and posture, not measured acoustic silence: servo noise remains plausible. No
waveform was retained, so its source cannot be established. No new duration veto
or ASR confidence threshold was introduced. Short speech/stop-command recall and
false transcription under servo noise still need saved-audio/live acceptance.

The first follow-up answer was cancelled before PCM by the repeated question.
Later answers have completed software delivery receipts and zero reported
underruns; that does not verify physical audibility.

Verification: **772 blocked-I/O checks pass**, including seven new synthetic
regressions covering callback/receipt ordering,
explicit stop and receipt retention, effect isolation, resumed speech and camera
changes after PCM, future/stale microphone timing, and startup after rejected
noise. Existing later-arrival fixtures now include an actual trusted conversation.
No hardware, hosted providers, acoustic models or personal state were changed.
