# Proactive core implementation plan

Current scope: [implementation checklist](IMPLEMENTATION_STATUS.md). Dated sections
below preserve the evidence and limitations of their respective checkpoints.

September 12, 2026. The user authorized planning and beginning implementation.
Existing documentation edits were already present in the working tree. This work
uses v3's own Session and providers; no v2 extraction or personal-data import.

## Operating goal

Rex normally runs when Bret chooses to demonstrate him to friends. He should act
like an engaged friend: an initial opportunity after roughly **7–10 seconds** of a
genuine lull, rather than waiting 15–60 seconds. Start at eight seconds, measured
after both recent human speech and completion of the preceding interaction. That
is opportunity timing, not a promise of audible output at eight seconds: model
and speech latency follow. The model still interprets whether this is a welcome
opening. See [design](PROACTIVE_CONVERSATION.md) for delight and emotional sensitivity.

## Startup greeting repair — September 14, 2026

The user requires a hello when Rex starts with a stable visible person before
anyone speaks. The latest live failure (`c151bb0def17478daf6d2ec3afba85e7`)
recognized a face, offered an arrival at eight seconds, then selected
`remain_silent(no_response_needed)` and imposed a 120-second hold.

Startup arrivals now explicitly request a brief model-written greeting. Only
ordinary speech or an audible face-name invitation is offered in that opening;
silent attention/description tools remain available for later opportunities.
Attached images no longer add conflicting instructions about a preceding exchange.
No identity is required for a simple hello, and no acoustic identity is inferred.
The existing eight-second startup gate, camera stability, quiet/floor checks,
new-speech cancellation and delivery receipts remain in force. Model/network or
playback failure is still a failure, not a guaranteed or fabricated greeting.

A `no_response_needed` decision on a later opportunity skips that contribution
without imposing the 120-second hold. Human-floor reasons and explicit quiet/stop
controls retain their existing protection. Ordinary pacing still limits attempts;
this change does not automatically retry every declined arrival.

Verification: **382 blocked-I/O checks passed**, including a synthetic hosted
transport through production Session with startup images and audible greeting,
unknown-face name invitation, later silence versus human-floor holds, cancellation
and non-repetition. No hosted model, microphone, camera or speaker was operated.
Live startup greeting quality and latency still need acceptance.

## Stages

| Stage | Deliverable | Verification and completion gate |
| --- | --- | --- |
| 1. Shared initiative foundation | Generalize the existing arrival event and Session path; add visible-group lulls, shared history/outcomes, explicit session quiet/resume, sensitivity guidance, bounded pacing and cancellation | Production Session/worker tests with blocked I/O, hosted synthetic semantic probes, documented live limits |
| 2. Conversational feedback | Source-linked interpretation of invitation responses, topic closure/repetition and per-participant preferences; preserve human-to-human floor context without a universal metadata call | Multi-person sequences where unrelated speech does not settle a question, quietness is not disengagement, and correction/forgetting remove stale context; compare model-call counts and latency |
| 3. Contextual memory opportunities | Relevant accepted plans, callbacks and celebrations with source/audience constraints; diverse bounded opportunity selection | Source/subject/discretion scenarios, dates and corrections across restart; no automatic biography from a proactive remark |
| 4. Live social acceptance | Deferred at user request until software is complete; then repeated dev-Mac demonstrations and combined-load M2 sessions | Human ratings of delight, warmth and timing alongside interruptions, repetition, missed openings, attribution, median/p95 latency and playback |
| 5. Additional observation sources | Implemented public news/weather and scene images for conversational object/animal/gesture reactions; actuator control is outside the clarified proactive-only scope | Verify each provider's facts/freshness and resource cost; preserve all core acceptance contracts before adding the next |

Stages 1–4 are core work. The user subsequently authorized finishing the proactive
software before testing, including stage 5 inputs, and excluded the wider robot
roadmap. This does not authorize starting hardware during implementation. Stages are
not parallel conversation loops. No mandatory classifier/planner call is added
before ordinary replies. Keep the semantic feedback design bounded; an optional
state tool must not turn every natural response into two model generations.

## First slice boundaries (historical scope)

Start with the camera already used for arrivals. Lulls require a fresh stable
visible audience; this avoids claiming that some other person's recent speech
proves a named target is still present. Names are optional and the model considers
the group without choosing the most talkative person in Python. Voice-only
presence and additional scene interpretation remain later measured work.

The first ledger records attempts and delivery outcomes, not inferred emotions
or response success. Responses remain explicitly unassessed in that ledger;
the model reads the original conversation. At most one lull consideration follows
each processed source turn, while a rolling cap spans all source turns and arrivals.
This is an initial bound, not semantic engagement detection. A failed/declined
attempt consumes pacing but never becomes an ignored spoken question. Retained
source links/outcomes are bounded and cleared on forgetting.

An explicit request to stop unsolicited speech creates a session quiet boundary.
Ordinary direct questions still work; an explicit invitation to initiate again
clears it. The initial scope is the shared room for this run. Topic-specific and
per-person boundaries stay in original history until stage 2; do not silently
persist a preference or infer one from low energy. Terminal stop retains its
existing temporary cancellation hold.

Synthetic fixtures cannot establish mood improvement or actual humor. Hosted
text probes should be read for relevance and restraint; their output status alone
is not a quality score. No live microphone, camera or playback is needed to build
or run these first checks.

## Evidence

- Baseline: 157 blocked-I/O checks passed before source changes.
- Stage 1: implemented against v3 base `bdb41d1d31f265cbe9aced6e7943ce63af9822d4`;
  **174 blocked-I/O checks pass**, including the new documented scenario through
  production Session. Source preservation, shared one-call replies/lulls, eight-second
  scheduling, frozen evaluation, quiet/resume, audience/speech/queue cancellation,
  failed attempts, empty model output, rolling pacing, forgetting and restart are
  exercised with synthetic evidence and temporary databases. `git diff --check` and
  115 local Markdown links/anchors pass. No device or real acoustic model was used.
- Final hosted original wording: **8/9** expected behavior checks; alternate wording:
  **8/9**. Each used ten actual hosted requests for nine judged turns (one explicit
  resume tool continuation), plus six scripted seed generations. Both produced
  contributions for receptive tiredness and a completed topic, and respected grief,
  thinking pauses and explicit quiet/resume. Original wording still intruded on a
  question addressed to another human; alternate wording added a reassurance after
  an unanswered invitation. These are unresolved semantic misses, not passing
  silence decisions. Earlier failures and the initially blocked-network run remain
  in private validation; successful final runs do not erase them.
- Hosted judged-turn wall time, including applicable tool continuation and silent
  text output: original median/max **0.858/1.509 seconds**, alternate
  **1.085/2.317 seconds**, nine samples each. These exclude capture, ASR, TTS and
  playback; they do not establish live first-audio latency or M2 resource impact.
- Existing hosted continuation/introduction/addressee regressions: **13/14**;
  an explanation intended for a parent still received a reply. This intermediate
  run preceded the final lull-specific guidance. No claim of universal group
  participation or a matched improvement over the previous prompt is made.
- Private evidence: `local/validation/proactive-a3f6696b32aa441d9716151111fcfebe/`
  (final original), `local/validation/proactive-21404da1c5b1414d9c28a5105a18e577/`
  (final alternate), and `local/validation/135d837083384fc9a10b785695e10d0b/`
  (existing regression suite). Actual hosted calls used synthetic text, in-memory
  stores and TextOutput; no microphone, camera, TTS, personal DB or robot was used.
- Stage 1 social/live acceptance remains open. Read the sample replies and evaluate
  humor, relevance and timing with people. A status match is not a humor score;
  no mood improvement or real speaker discrimination was measured.
- Stages 2, 3 and 5 are also implemented; their dated evidence follows below.
  Stage 4 is live acceptance, not an unfinished software stage.

## Stage 2: conversational feedback

Implemented against v3 base `73b09a6313301a9b1147dd5af2ff8fc3c78c054e`.
No v2 extraction, data import, provider or resident model was added.

- `conversation_feedback` records explicit invitation answer/decline, topic closure
  or avoidance, corrections/reopening and resolution of a human-owned floor.
  It is optional on ordinary turns. Its validated reply goes directly to the sole
  output owner in the same generation; a null reply stays silent. Rejected tools
  retain the existing exceptional result-continuation path.
- Each note retains the current source ID, bounded original words and observed
  speaker person/track. Untrusted, mixed, tentative-continuity and absent-track
  sources cannot write feedback. A mentioned person is never substituted for the
  speaker. Notes remain session interpretations, not permanent personal facts.
- Invitation feedback references actual completed audible phrases, within five
  minutes, after the source invitation, with any named target checked against the
  current speaker. Unrelated speech never automatically settles an invitation or
  pending identity question. Group outcomes stay unknown; feedback belongs to the
  responding participant. Delivered excerpts support semantic repetition checks.
- Topic boundaries are participant-scoped. Only their owner can retract/reopen
  them; source-linked floor resolution may come from the other participant who
  answers that exchange. Association corrections remove affected notes; forgetting
  removes all social links; restart restores none of this session context.
- Existing `remain_silent` human-floor reasons preserve their semantic decision
  with a source-linked note. The scheduler and Session defer initiative while that
  note is active, even after an unrelated direct reply or history eviction. A
  semantic completion releases it; expiry is at two minutes, not an inferred answer
  (since October 24: 20 seconds after the last words heard; see IMPLEMENTATION_STATUS.md).
  This uses the existing model decision rather than a second silence classifier.
- There are at most 32 notes and 16 delivery receipts, with excerpts bounded to
  500 source / 1,200 delivered characters. Routine feedback cannot evict an active
  avoid-topic boundary; a full set of boundaries rejects additional notes rather
  than silently forgetting one. No extra settings were added.

### Stage 2 verification

- **192 blocked-I/O checks pass** (174 before this slice). Synthetic voices and
  production Session/state/output paths exercise participant scope, source validation,
  revisions, idempotence, unrelated speech, actual delivery, failure, forgetting,
  correction, restart, bounded retention, human-floor holds/releases and generation
  counts. No network, audio, serial, camera or real acoustic model is used by them.
- Hosted evidence below is synthetic text with synthetic acoustic tracks, in-memory
  stores, TextOutput and one simulated audible invitation. No microphone, camera,
  TTS service, personal database or real playback is used. The same configured
  hosted model remains `gpt-5.4-mini`, low reasoning.
- Earlier hosted iterations exposed conflicting silence instructions and weak tool
  selection. One intermediate 9/10 run still intruded after an unrelated direct
  question. This led to applying the LLM's existing floor decision in the state
  and scheduler, rather than asking the model to rediscover it at every lull.
  Earlier runs remain in private validation; they are not erased by later results.

- Final original feedback probes: **9/11**, ten hosted requests for eleven judged
  turns (one held lull uses zero). Alternate wording: **9/11**, eleven hosted
  requests (one held lull uses zero; one rejected foreign-boundary retraction uses
  a result continuation). Each run also has two scripted seeds. In the original,
  an invitation decline and completed topic were recorded as avoid-topic notes.
  In the alternate, the state owner correctly rejected another participant's
  attempted retraction, but the extra call fails the one-call expectation; a human
  answer did not release its floor note. That hold expires after two minutes.
- Hosted judged-turn wall time, excluding the zero-call hold: original median/max
  **1.409/1.845 seconds**, alternate **1.471/4.302 seconds**, ten samples each.
  These include applicable result continuation and silent TextOutput, but exclude
  capture, ASR, TTS and real playback. This is not a matched latency benchmark or
  evidence of improved end-to-end performance over stage 1.
- Private final feedback evidence: `local/validation/feedback-60d9d2c8a00d4e5fb5af74ad5a72cab4/`
  and `local/validation/feedback-431d23877522482bbb85e084ce561b6f/`. The initial
  blocked-network run is retained; its early absence-only checks were corrected
  to require successful delivery/suppression and are not semantic evidence.

- Final earlier proactive regression probes: **7/9**, ten actual hosted requests
  for nine judged turns, plus six scripted seeds. Welcome tiredness, completed-topic
  initiative, thinking/grief and quiet/resume passed. A human-directed question and
  an unanswered invitation still received unwanted follow-ups. These historical
  seed turns provide original words but no semantic human-floor note, so this
  exposes the remaining dependence on interpreting context when a hold is absent.
  Private evidence: `local/validation/proactive-a9389502283c492eb42479f92a0caa8b/`.
  This small stochastic sample does not establish a measured regression rate or
  universal improvement over the earlier 8/9 runs.
- Final whitespace validation and **44 local Markdown links/anchors** in changed
  documentation pass. Implementation acceptance remains separate from hosted and
  live social acceptance.

### Remaining acceptance

Semantic selection is probabilistic. A completed subject can be recorded as a
stronger avoid-topic preference, and an invitation decline can become a topic
boundary without an invitation receipt link. Phrasing sometimes overstates group
scope even when stored state remains correctly participant-scoped. No synthetic
status score proves humor, sensitivity, actual mood improvement or live speaker
accuracy. Proactive holds depend on the initial semantic silence decision; a missed
human addressee can still receive an ordinary reply. The two-minute expiry and
per-participant feedback need live demonstration acceptance. M2 resource usage and
capture-to-audio latency have not been measured for this slice.

## Stage 3: contextual memory opportunities

Implemented against v3 base `67597216b39fe8d376fcc275bf4ba6f0dba920b7`.
No v2 extraction, personal-data import or new provider/model was added.

`remember_followup` uses the existing attributed-claim executor to atomically save
an accepted supported-speaker self-report and its follow-up metadata. A plan or
celebration uses an exact ISO calendar date; a birthday uses month/day; a callback
has no date. The local calendar date is supplied to the same conversation model.
Unknown/provisional speakers cannot schedule somebody else's memory. Ambiguous
relative dates need clarification, not Python language parsing.

Sharing defaults to `subject_only`; `group` requires the subject's explicit semantic
permission. A private candidate requires the subject as the sole visible face;
an unknown extra face also withholds it. A group candidate still requires the
subject present. Third-party/candidate claims are excluded. These are candidate
access rules, not proof that all speech or retained conversation history can never
leak a private detail. The model still interprets the source and discretion.

At arrivals and lulls, up to six eligible candidates rotate through a bounded pool
of 128 recent accepted follow-ups. The model can select one, use another fresh
contribution, or stay silent. This preserves one proactive generation and the
existing quiet/floor/pacing gates. `use_memory_opportunity` revalidates the offered
claim, current status, date and audience, then uses the sole output owner. Speech,
audience changes, changed state revision or midnight can cancel stale output.
Generated speech never creates a new personal fact.

Only completed audible output records a persistent delivery receipt. Text-only,
failed and interrupted deliveries do not count. Birthdays are eligible on their
calendar day each year (February 29 stays February 29); celebrations on the exact
saved date; plans within seven days before/after their date, without assuming they
happened; callbacks may be considered daily. A delivered plan/celebration is not
reoffered for that occurrence, a birthday not again that year, and a callback not
again that day, including after restart. These are opportunity eligibility windows,
not an unattended reminder service or guaranteed speech schedule.

Correction/retraction makes the old linked claim ineligible without carrying its
date or sharing permission to a replacement. Forgetting cascades to metadata and
receipts. Existing databases gain additive tables on ordinary Memory.start; original
claims remain intact. Old generic claims are not automatically converted into dated
or group-shareable follow-ups. A corrected follow-up currently needs a fresh
`remember_followup` save to establish new scheduling/sharing metadata.

### Stage 3 evidence and remaining acceptance

- **205 blocked-I/O checks pass**, versus 192 before this slice. Synthetic contracts
  cover private/unknown/group audiences, supported subject attribution, date windows,
  leap dates, rotation, atomic rejection, corrections, forgetting, restart, additive
  schema compatibility, sole-generation selection, text vs audio receipts and
  midnight cancellation. No devices or real acoustic models were used.
- Hosted synthetic storage/eligibility probes: **6/6** in each of two runs. The final
  run includes an explicitly shared birthday with an additional unknown face. Plans
  and birthdays used `use_memory_opportunity`; a private promotion was withheld in
  a group after clearing session history. Three saves and three arrival turns use
  the configured hosted model with synthetic supported acoustic enrollment,
  in-memory stores and TextOutput. No audio was played and no personal DB was read.
- These scores check saves and candidate availability, not unrestricted semantic
  quality. The sailing-save acknowledgment still promised a future nudge despite
  opportunity-only guidance. One greeting narrated camera framing, and another
  implied a return visit without evidence. These wording failures remain open;
  neither six passing state checks nor synthetic identity establishes social acceptance.
- Private evidence: `local/validation/memory-opportunities-7d407edb00d14204a8120f188b139b0c/`
  and `local/validation/memory-opportunities-76b6b3e264a04ad889b3ad7d8ad245d8/` (final
  group case). Stage 1/2 semantic limitations remain; this slice does not claim to
  resolve unwanted replies, missed floor releases or humor quality.
- The final six judged turns used **nine hosted requests**: one primary generation
  plus the existing save-result continuation for each of three saves, and one
  primary generation for each of three arrival decisions. Judged-turn wall time
  median/max was **2.018/5.836 seconds** (six samples); this includes hosted work
  and TextOutput, not capture/ASR/TTS/playback. It is not a matched latency comparison.
  Whitespace validation and **46 local Markdown links/anchors** pass.
- Live M2 load, recognition accuracy, source-to-audio timing, delight and discretion
  with real groups remain unmeasured. The next stage is explicitly arranged live
  acceptance, not automatically starting microphones/cameras or robot hardware.

## Stage 4: social acceptance preparation

The user authorized proceeding with social acceptance. The dev-Mac non-starting
readiness check reports no missing dependencies, including camera prerequisites.
No live conversation has yet been observed in this stage. The
[live social acceptance guide](SOCIAL_ACCEPTANCE.md) defines a first guided pilot,
a natural conversation segment, observation records and the distinction between
a solo check, a two-person pilot and the existing multi-session acceptance gates.
The user subsequently deferred startup until the software plan was finished; full
audio-capture retention is not enabled by default. No old data is imported. Device startup, social ratings and live
acceptance remain pending until the session actually occurs.

## Stage 5: observation inputs and software completion

The user explicitly requested finishing the proactive-conversation plan before
live testing, excluding the wider robot roadmap. They selected Sacramento, CA
weather and top headline news. Public RSS/Open-Meteo observations and shared-camera
snapshots now feed the existing conversational owner with bounded rotation,
freshness, cancellation, source attribution and persistent completed-audio receipts.
New material can create an ambient opportunity without a human speaking first.
No additional resident model, independent speaker or mandatory reply classifier
was introduced. Actuator control and sound-event classifiers remain wider-roadmap
work; conversational reactions to scene/gesture evidence are implemented here.

**221 blocked-I/O checks pass.** The final explicit public-source validation fetched
BBC headlines and Sacramento weather; **4/4** synthetic hosted status cases passed
using four model requests, including one scene image and a serious-context silence
case. See [observation-source documentation](OBSERVATION_SOURCES.md) for exact
limits, configuration, token/latency evidence and remaining semantic issues.

The agreed proactive software stages are implemented. Real social acceptance,
gesture/object accuracy, timing and M2/device validation remain unperformed; the
source probes are not substitutes for those measurements. No hardware was started,
no old people data was imported, and no additional live test is requested as a
prerequisite to this software delivery.

### September 13: introductions and interrupted opportunities

Two user-run sessions contained eleven proactive outcomes: nine interrupted without
complete spoken phrases and two deliberately suppressed. Historical traces do not
identify the invalidating guard, so these counts do not establish the cancellation
cause. No proactive phrase was recorded as completely heard in those runs.

Implemented repairs:
- Targeted face-name questions are available at lulls and ambient opportunities as
  well as arrivals. The state owner validates the target against the offered faces;
  arrival questions still target the arrival. Only actual audio delivery creates
  a pending question. No proactive identity or personal-data write is authorized.
- Shared model guidance favors natural introductions for unnamed speakers and
  unfamiliar faces, while respecting prior introductions, pending/declined
  invitations, provisional enrollment and quiet requests. This adds no classifier
  or extra mandatory model generation.
- An arrival interrupted before first audio can be offered again after the existing
  45-second global cooldown, within the existing six-attempt/five-minute budget.
  Partial/heard speech, deliberate silence and provider failures do not reopen it.
- Cancellation checks sample the microphone timestamp before reading the clock,
  preventing a concurrent update from looking like future audio. Structured trace
  and terminal reasons distinguish speech, queued work, unavailable audio and an
  audience change/staleness. This race is reproducible synthetically; its occurrence
  in the historical sessions is not established.

Validation: 336 blocked-I/O checks passed. Added production-path checks cover an
LLM-tool-shaped name question during a lull, waiting after audible delivery,
unheard-arrival recovery versus partial playback, the timestamp race, and stale
microphone cancellation. No hardware or hosted model was run. Live greeting rate,
model choice of introductions, cancellation causes and social quality remain open.

### September 13: live cancellation evidence and microphone diagnostics

The next user-run session recorded four audience-change/staleness cancellations
and one human-speech cancellation. It contained no completed captures or ASR
requests; microphone frames continued arriving. The user reports normal/loud
speech and manual Ctrl-C. No waveform was retained because evaluation recording
runs after endpoint completion. The trace cannot establish input level or VAD
accuracy for this run.

Fixed a second freshness problem: ongoing opportunity guards now read the camera's
latest analyzed cache before evaluating the audience, rather than relying on the
main loop's older polling snapshot. The one-second freshness limit and cancellation
on actual audience changes remain intact. No frame is reanalyzed by this check.

Added five-second microphone diagnostics with selected device, peak/RMS level,
frame counts, speech-detector/echo counts and pending endpoint state. Terminal
output shows level and detected-speech counts. Diagnostics retain no PCM and run
without a new provider. This distinguishes digital silence from audible signal
that the detector rejects; it does not by itself fix the user's missing speech.

Validation: 338 blocked-I/O checks passed, including a camera result refreshed
between main-loop polls, actual audience-change cancellation, and separate signal
versus detector reporting. No live device was operated. Live input/ASR recovery
and confirmation of the historical camera cancellation mechanism remain pending.

### September 13: camera changes during audible replies

The subsequent live run delivered conversation and an arrival greeting. The
user-reported cut-off proactive sentence coincides with an audience invalidation
mid-playback; the trace reports interruption, not a TTS provider failure. A separate
ValueError followed a valid describe_view proposal while the scene changed during
model generation. The original trace omitted the exception reason; source freshness
validation is consistent with the observed scene changes and valid tool arguments.

General prose now requires a valid audience before first audio but can finish after
camera evidence changes. Human speech, explicit cancellation, unavailable input and
shutdown still interrupt. Tool-selected output retains audience checks, including
face questions, visual remarks, memory and actions. This does not enable acoustic
barge-in during the existing echo gate.

A VisualEvidenceUnavailable rejection during description saving becomes a structured
rejected tool result and silent outcome, without a save or generic turn failure.
Other validation/programming errors remain failures. The current TorchScript VAD
backend is unchanged; only its specific torch.jit.load FutureWarning is filtered
inside startup. Capture-gap traces now include gap duration and whether pending
input was discarded; the terminal describes reset/recovery without a generic error.

Validation: 340 blocked-I/O checks passed, including camera changes after first
speech, continued interruption for human speech, and structured stale-description
rejection. No hardware, hosted calls or TTS regeneration were used. Actual audible
completion, startup timing-gap cause and the separately observed six playback
underruns on another reply remain live validation work.

### September 13: intermittent network recovery

Two subsequent user-run sessions recorded OpenAIWriteTimeout on proactive requests.
Neither produced a completed capture/transcript: input diagnostics showed substantial
signal but zero speech-detector frames. That separate detection problem remains
unresolved; network retries do not repair it.

Implemented cancellable pre-output OpenAI retries with bounded backoff, visible
waiting/recovery, request-attempt accounting, and backpressure instead of dropping
already transcribed turns from a full conversation queue. No automatic replay of
partially released speech/tools or durable cross-launch retry was introduced.

Validation: 346 blocked-I/O checks passed. Synthetic transport tests cover repeated
write failures then success, identical retained request content, cancellation,
nonretryable authentication failure, and no replay after released text. Production
Session coverage confirms that new speech during an outage preserves earlier user
words and answers the latest turn. Queue tests preserve transcribed order. No live
network, model, microphone or speaker was operated for this change.

## September 27: greeting interrupted by face-track churn

In session `89c11091d03241de84f9485eca3236e5`, the complete “Well, hello
there. I’ve got company again…” greeting reached TTS. Playback began at 2434.030
and proactive validation cancelled it at 2435.998 with `intent_audience_changed`,
after new face tracks appeared. The explicit user stop came later, at 2439.947.
This is a logged program cancellation, not evidence of truncated TTS synthesis.

Session incorrectly marked every tool response as audience-bound, including a
general `respond_with_intent` greeting with no memory references. Such replies now
use the existing ordinary-prose policy: check the audience before audio starts,
then allow the phrase to finish across camera/track changes. Targeted questions,
memory-bearing replies, explicit stop, human-floor and runtime health cancellation
retain their existing guards. No provider, model, audio buffering or hardware
configuration changed. Future intention eligibility still uses current audience.

Regression checks exercise the actual VoiceRuntime opportunity validator and
Session output path with fake camera tracks and synthetic audio: a general intent
reply completes across track replacement after playback starts, the same change
before playback cancels, and a targeted question still cancels during playback.
Live camera/motion behavior and audible completion remain to be retested.
