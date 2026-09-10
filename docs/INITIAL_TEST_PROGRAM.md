# Initial test program and acceptance plan

Status: implementation specification only. None of the commands, modules, or
results described here exists yet. [DESIGN.md](../DESIGN.md) defines ownership;
[V2_REFERENCE.md](../V2_REFERENCE.md) locates reference implementations.

## The first question to answer

Can Rex conduct an ordinary conversation with Bret and Jeff, distinguish speakers
when the evidence supports it, handle uncertainty naturally, and preserve a few
correctly attributed memories across a restart?

The first program is a replacement-core experiment with real microphone input and
Rex's voice. A typed chat demo is useful for plumbing but cannot answer this question.
No motors, decorative servo control, games, or proactive feature expansion is needed.

## Implementation slices

| Slice | Deliverable | Gate before expanding |
| --- | --- | --- |
| 0. Independent boot | New environment, small settings schema, explicit provider lifecycle, scratch paths, startup report, blocked-I/O test fixtures | Importing core modules loads no models/devices; no v2 imports or writable v2 DB connections |
| 1. Audible loop | Capture, batch ASR, main model reply, ElevenLabs adapter, single output owner, cancellation, original/played-text trace | A real spoken exchange works; speech completion and failures are visible; only selected models load |
| 2. Speaker evidence | Labeled-audio replay, session tracks, enrolled profile copies, unknown option, change/overlap diagnostics | Distinguish encoder, segmentation, and decision-rule failures; establish a held-out Bret/Jeff baseline |
| 3. Meaning and memory | Basic camera tracks, identity/reference tools, pending questions as context, claim store, correction and recall | Full introduced-person/correction scenarios run through the real core; persistence survives restart correctly |
| 4. Group acceptance | Repeated dev-Mac sessions, saved-audio replay, load/failure tests, diagnostics review | Freeze and satisfy the declared evaluation gates, or identify a specific remaining limitation |
| 5. Controlled expansion | One additional useful capability through typed tool/results | Its addition preserves prior group, memory, delivery, and latency gates; retire any replaced implementation |

Begin speaker data collection as soon as slice 1 works; do not finish a GUI or rich
memory before investigating the weakest link. Initial design and integration research
should be a short bounded pass, followed by a running loop. Full v2 feature parity
is a later backlog, not the first release definition or a promised completion date.

## Suggested implementation shape

This is a responsibility sketch, not a requirement to scaffold every file at once:

```text
src/r3x/
  app.py                 Explicit boot/close and dependency construction
  contracts.py           Typed observations, proposals, results, and identifiers
  session.py             One conversation state owner and turn lifecycle
  speakers.py            One tracker for named/unnamed voices and associations
  brain.py               Main model contract and allowed semantic tools
  memory.py              Small attributed-claim and recording store
  output.py              One speech scheduler and delivery receipts
  adapters/              Capture, ASR, voice encoder, TTS, camera, optional DoA
tests/
  fixtures/              Synthetic and sanitized text/evidence scenarios
local/                   Ignored scratch state, imported copies, audio, reports
```

Keep a narrow interface between pure core logic and providers. Camera/ASR/model
workers return events to session ownership; they cannot change profiles or speak.
Core import tests must work with all heavyweight model modules unavailable.

## Proposed operating modes

These command shapes are design examples, not runnable instructions today:

| Proposed mode | Purpose | I/O expectations |
| --- | --- | --- |
| `python -m r3x doctor --profile dev-mac` | Show effective configuration, capabilities, model paths, and dependency readiness | No recording, playback, download, or model inference unless explicitly requested |
| `python -m r3x live --profile dev-mac` | Real conversation with camera presence, selected providers, and scratch storage | Microphone, camera, and selected hosted calls; no serial hardware |
| `python -m r3x replay <manifest>` | Run labeled recordings through production segmentation/ASR/tracking and optionally the real brain | Saved files; no live mic/servo; hosted inference is an explicit option |
| `python -m r3x scenario <fixture>` | Exercise full decision/state/output contracts with deterministic observations/providers | No network, devices, or personal databases |
| `python -m r3x report <session>` | Summarize identity decisions, writes, delivery, resources, and latency | Read local session evidence |

Use one shared turn-processing entry point in live, replay, and scenario modes.
Replace observations/providers, not the decision machinery being evaluated. Typed
tests must not automatically receive the identity confidence of real audio input.

## Scratch data and baseline preparation

1. Preserve v2 as the reference installation. Record its commit and working-tree
   differences; use the reference revision in V2_REFERENCE.md or a freshly verified
   successor that includes the recent repairs.
2. Before creating local artifacts, ignore `local/`, credentials, databases, private
   recordings, model weights, and exports. Keep sanitized fixtures separate.
3. Implement an explicit import that reads a consistent v2 snapshot and creates
   independent v3 copies of selected participant labels, voiceprints, and original
   audio. Record source IDs, digests, model namespace, and new ID mapping.
4. Start personal claims empty or with a few verified fixture claims. Do not bulk
   import v2's accumulated facts to prove a clean core works.
5. Allow a dataset reviewer to flag a copied profile as suspect. Existing enrollment
   is a starting reference, not ground truth about every stored clip.
6. Keep labeled evaluation recordings separate from enrollment and tuning. No
   automatic enrollment or adaptive updates on the frozen accuracy-evaluation set.
   Run separate adaptation experiments if evaluating safe profile growth.

No import or recording has been performed by the documentation task. A later live
test must not run both v2 and v3 on the same microphone/speaker devices concurrently.

## Speaker evaluation dataset

Use human labels established independently of Rex's predictions. Include Bret and
Jeff, and an out-of-roster speaker when available. Synthetic unknown embeddings
are useful contract tests but do not substitute for a real unknown voice.

Cover multiple sessions and conditions:

- Near and farther away, facing the microphone and turning away, seated and moving.
- Longer natural sentences, name answers, short acknowledgments, questions, and laughter.
- A-B-A changes, rapid interjections, pauses, and deliberate overlap.
- One-to-three visible people, small distant faces, incorrect face-name candidates,
  a silent visible person with someone speaking off camera, and absent camera evidence.
- Rex speech/echo and, later, impersonations that must not refresh real identities.
- ReSpeaker direction only in a separate robot cohort after the servo hold exists.

For each capture, record expected speaker(s), speech intervals, transcript reference
where available, overlap, device, session, condition, and whether identity is truly
resolvable from the recording. Unresolvable examples must not silently disappear
from coverage reporting. An acoustic track label is distinct from its person name.

First compare clean same-person and different-person scores. Then evaluate the
complete tracker. High ASR accuracy is not speaker accuracy; low cosine between
two short windows is not proof of a speaker change. Compare alternate models or
segmentation only after locating which stage failed, using the same held-out data.

## Conversation and memory scenarios

Expected behavior below is a contract, not an exact response wording. Run paraphrases,
straight/curly apostrophes, unknown speakers, and variations that were not used while
implementing. Use actual speech for the final acceptance pass.

| Scenario | Required outcome |
| --- | --- |
| New person says a full name, then speaks naturally | A provisional association; accepted voice samples accumulate only with compatible evidence; no repeat-after-me routine |
| Known face lacks CAM++ prints | Model asks a natural speaking confirmation; the confirmed voice anchors the chain; a different person's answer cannot enroll it |
| Bret says "That's Jeff Benziger" | Resolve the mentioned person through an explicit unique nickname/alias match; preserve Bret/unresolved as the speaker; no literal-sentence name, new face, or bystander voice enrollment |
| Rex asks a name; response is "I'm going camping tomorrow" | Interpret the statement or clarify; create no person named after the activity |
| "That's not his name" during onboarding or collection | Correction reaches the conversational owner; affected provisional work stops; no next-question handler consumes it as a profile answer |
| Two people share a nickname | Request useful disambiguation when needed; no arbitrary match or duplicate enrollment |
| Rex just greeted Bret; a plausible reply follows | Continue coherently without reflexively demanding a name; continuity remains evidence-limited and cannot authorize profile growth |
| Jeff says "You're creepy" after a job question | A comment about Rex; no occupation fact or later self-description of Jeff as creepy |
| Jeff asks about Jimmy Carter or makes a passing restaurant joke | A question/remark; no automatic lasting interest, worldview, occupation, or catchphrase |
| Bret reports a fact about Jeff | Save only if eligible, with Jeff as subject and Bret as source; never relabel it as Jeff's own statement |
| Speaker uncertain while volunteering a personal fact | Preserve the utterance/pending claim; no guessed personal profile write |
| Clear self-report, restart, recall | Retrieve the same attributed claim with its source/status; session voice tracks do not persist as unquestionable identities |
| "I meant clocks, not radios" | Correct the relevant claim through context; recall uses the correction and retains provenance |
| Person/voice data is forgotten | Active retrieval, accepted audio, derived references, and pending associations stop using deleted data |
| Several people speak within one capture | Split only with supported timing or abstain; no whole-buffer transcript assigned confidently to one person |
| Ask "Who is speaking?" under weak evidence | Admit uncertainty or express a supported tentative association; a name mentioned in the utterance cannot answer the question by itself |

Deliberate false self-identification is a limit to document, not something a cosine
threshold or text model can solve universally. The prototype learns a voice-to-claimed-
identity association; it is not legal identity verification.

## Delivery, lifecycle, and failure scenarios

- A write fails: no "I've saved that" acknowledgment and no half-written record.
- A duplicate tool request arrives: one mutation and a repeatable result.
- A result arrives for an old session/revision: no new write or speech from stale work.
- Stop/cancel occurs during generation or playback: bounded shutdown, no trailing
  queued speech, and an interrupted delivery receipt.
- A question fails before being heard: it cannot claim the next utterance as its answer.
- Speech is captured during a reply: retain bounded input in order; do not launch
  a second conversational owner. Report overflow explicitly.
- ASR/encoder/TTS fails or a model is absent: show the actual capability failure;
  no silent namespace/provider switch, endless retry, or false successful playback.
- Transcript has low trust, or audio is echo/mixed: it cannot produce named biometric
  writes or established personal self-reports.
- Camera work and local inference compete: measure scheduling and actual playback;
  an isolated successful WAV generation does not establish live reliability.
- Disabled capabilities: no import-time model load, device access, or hidden warmup.

When local Breeze/impersonations are introduced, add complete-content and playback
tests: original transcript matches its reference WAV; the distinctive final phrase
survives; underruns do not deliberately discard remaining audio; failures produce no
successful outro. Preserve 8-bit and the chosen speech buffering policy.

## Metrics and proposed acceptance gates

Freeze the dataset, definitions, and gates before the evaluation run. The numeric
values below are initial proposals, not measured v3 results or user-approved service
guarantees. Revise them with an explicit rationale before a new evaluation, not
after a failure just to declare success.

| Area | Initial target/evidence |
| --- | --- |
| Meaning/state integrity | All listed deterministic scenario contracts pass, including unseen wording variants and failed/stale operations |
| Persistent data integrity | Zero wrong-person claims or named biometric writes observed in the declared acceptance sessions; report cohort size, not a universal zero-error claim |
| Clean enrolled speech | On held-out single-speaker captures with at least two voiced seconds: at least 95% correct among named decisions AND at least 85% named coverage; report per person, distance, and device |
| Unknown and mixed speech | Dedicated real cohorts; report false-known assignments and missed switches/overlap separately; zero named training writes on labeled mixed/Rex-echo samples |
| Short/ambiguous speech | Report assignment precision, abstention, and fragmentation separately; do not inflate clean-speech metrics by excluding difficult cases from the overall report |
| Live conversation | At least three separate two-person sessions, including one roughly 15-minute natural exchange; record needless identity questions, wrong names, missed corrections, and participant feedback |
| Latency | Same-device median and p95 end-of-speech-to-first-audio no worse than the matched v2 baseline; seek a material improvement. Record concrete release thresholds after the baseline, before evaluation |
| Perceived responsiveness | Existing 4–6-second delays are a known dissatisfaction, not an acceptable target merely because v2 also has them; do not declare the product ready from non-regression alone |
| Resources | Only declared models load; no growing queue/cache or unbounded memory trend during a session; report resident models, memory observations, and playback underruns |
| Speech truth | No false completion/save acknowledgment in the acceptance scenarios; generated, queued, and played states remain distinguishable |

Start with at least 100 held-out clear utterances across speakers/sessions as a
practical diagnostic sample, plus separate short, unknown, and overlap cohorts.
Report numerators and denominators. This size does not prove general reliability;
additional real sessions and device conditions remain necessary.

Report both per-turn and per-time tracking metrics where possible: wrong named
assignments, unknown-person false accepts, abstention, track fragmentation, merges,
switch detection, and overlap handling. A system assigning no names cannot pass.
Do not train or tune using evaluation labels, or test enrollment clips against
their own saved embeddings as recognition evidence.

Latency reports include capture endpoint delay, ASR, speaker analysis, context
preparation, model calls, first audio production, playback queue, and actual playback.
Separate cold/warm, online/offline, Mac/robot, and optional-camera-load results.
Include failures and timeouts in the overall report rather than silently removing
them; successful-request timing may be reported as an additional distribution.

## Evidence to retain

A report names the source revision, dirty-tree status, effective profile, model
versions, dataset digest, enrollment/tuning/evaluation split, device, scenario
results, metrics, observed failures, and unresolved limits. Private artifacts stay
local; a sanitized summary and fixture contracts can be tracked.

Use three distinct validation levels: deterministic contract checks; real models
on saved recordings; and live room/provider/hardware sessions. State which passed.
Do not let a large synthetic test count stand in for group recognition evidence.

When a gate fails, first identify whether the error is capture, segmentation,
acoustic matching, language interpretation, state validation, storage, or delivery.
Fix the owning component. Do not add a new interception rule in a different layer
or rewrite all providers together. Expand only after the remaining limitations
are explicit and acceptable for the next intended use.
