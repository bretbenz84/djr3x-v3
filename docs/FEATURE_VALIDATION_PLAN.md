# Feature validation plan

For commands, exact dialogue and step-by-step actions, use the
[live test script](LIVE_TEST_SCRIPT.md). This document provides the broader coverage
and evidence criteria behind those hands-on runs.

Prepared September 20, 2026. This is a plan, not new acceptance evidence.
Start with the current checkout and freeze its revision/settings for each pass.
Keep feature expansion paused while validating the implemented behavior.

## What needs proving

The [implementation checklist](IMPLEMENTATION_STATUS.md) records 467 passing
blocked-I/O checks. Those checks establish software contracts, not room accuracy,
complete audible replies, or enjoyable conversation. The September 19 persona
revision has hosted development evidence (141/168), but no subsequent live run.
Its cases were used during tuning and are not a held-out benchmark.

| Feature | Evidence already documented | Validation still needed |
| --- | --- | --- |
| Listening, transcription, streamed speech and cancellation | Synthetic contracts; earlier live failures and subsequent repairs | Missed speech, continuation, complete playback and recovery on current code |
| Names, voice enrollment and speaker attribution | Contracts and small saved-audio studies; some difficult cohorts fully abstain | Bret/Jeff discrimination, unknowns, overlap, restart and useful coverage |
| Faces and startup greetings | Saved-media tests and camera-only freshness/distance checks | Recognition with full conversation load; reliable audible greetings; face/voice separation |
| Persistent personal memory | State, provenance and restart contracts; hosted semantic cases | Actual spoken save/recall/correct/forget and private audience handling |
| Persona, social feedback and conversational intentions | Hosted fictional trials plus pacing contracts | Natural timing, human floor, unanswered questions, quiet/resume, repetition and warmth |
| Current-view inspection and visual descriptions | Synthetic images, hosted inspection/correction trials | Real scene accuracy, fresh evidence and corrections without invented changes |
| Named pet/object photo memory | Storage safeguards and saved-photo trials | Reliable teaching selection and individual recognition; both dogs were misidentified in the final documented nine-turn trial |
| News/weather and memory opportunities | Provider probes and synthetic/hosted cases | Relevant, truthful, nonrepetitive live contributions with audience discretion |
| Prompt caching and ordinary reply path | Paid caching probe, later persona evaluation | Current live first-audio timing, cache use and unnecessary intent/tool calls |
| ReSpeaker and target M2 workload | Bounded direction/camera/load probes | Combined speech/camera/object workload, direction ambiguity, latency and memory trend |
| Attention, gestures and movement/inspection sequencing | Simulation contracts | Simulation regression only here; physical drivers and microphone hold remain unimplemented |

Use current status and later dated amendments over historical “next stage” notes.
Model-selected JSON timing is **not implemented**: test the current 8/15/30/60-second
opportunity backoff, not the proposed replacement. Physical turning, gestures,
motion-aware noticing, games and long-lull thought experiments are outside this plan.

## Test order and sessions

Durations are planning estimates. Run the first pilot before booking a long group
session. Each stage gets its own result; a blocker in one feature need not prevent
independent testing of another.

| Pass | People / time | Purpose and exit condition |
| --- | --- | --- |
| 0. Baseline | Developer, 10–15 minutes | Automated checks pass, readiness inspected, revision/settings and evidence paths recorded |
| 1. Solo pilot | One person, about 20 minutes | Listening, full playback, greetings, quiet/resume and cancellation are usable enough for group testing |
| 2. Identity and memory | Bret + Jeff, 30–40 minutes including restart | Review voice versus face evidence; verify source/subject and persistence; retain every attribution error |
| 3. Social conversation | Two people, 25–30 minutes | Structured boundaries followed by at least 15 minutes of natural conversation |
| 4. Vision and photo memory | One or two people, 20–30 minutes | Separate description/correction/storage results from individual-pet recognition results |
| 5. Repeat and load | Two further separate two-person sessions, 20–30 minutes each | Change seating/distance/day; test full intended configuration and target M2 load |
| 6. Frozen acoustic evaluation | Human labeling followed by saved-audio replay | At least 100 clean held-out utterances plus difficult cohorts; precision and coverage both reported |

Passes 2, 3 and 5 provide more than the existing minimum of three separate
two-person sessions. A restart inside one encounter is not an independent session.
An out-of-roster third speaker is needed for a real unknown-speaker cohort; if
unavailable, mark that portion untested.

## Before each run

- Record commit and dirty status, model/reasoning, settings differences, device,
  camera/object/direction enablement, participant labels and start time. Do not
  copy secrets into results. Keep private records under ignored `local/validation/`.
- Stop v2 before using the same devices. Use the v3 environment. Keep v2 unchanged.
- Identify which state the run uses. Normal `live` uses the persistent local store
  and may learn names/faces/voices. Use only information participants want retained.
  Fictional/destructive state tests belong in temporary-store harnesses; do not
  erase existing profiles to make a test easier. Isolated live state requires a
  verified setup first; `--config` alone does not imply an isolated database.
- Establish the actual speakers, intended addressees and scene contents independently
  of Rex. Record timestamps and verbatim wording for failures, including what was
  actually heard. For visual accuracy, retain an explicitly selected reference
  image when possible; a prose log alone cannot prove what the camera saw.
- Use `--record-evaluation` for acoustic investigations. It retains endpointed WAVs
  and only the first 30 seconds of raw-input diagnostics. Put a suspected VAD failure
  near startup; a late missed utterance may have no raw waveform. A missing waveform
  leaves the acoustic cause unresolved.

Existing commands, to execute when the corresponding test is scheduled:

```sh
.venv/bin/python tools/run_checks.py
.venv/bin/python -m r3x doctor
.venv/bin/python -m r3x live --camera --record-evaluation
.venv/bin/python -m r3x report local/sessions/SESSION_ID.jsonl
.venv/bin/python -m r3x profiles list
```

Replace `SESSION_ID` with the printed path. Readiness is not inference/device
acceptance. For a camera-off comparison, verify `camera_enabled = false` in the
chosen settings; omitting `--camera` does not override an enabled setting.

## Pass 1: solo pilot — recommended first test

| ID | Action | Required observation |
| --- | --- | --- |
| A1 | Start camera-enabled, remain visible and silent; repeat across three launches | A greeting can complete before the visitor speaks; no unexplained freshness cancellation. Measure startup-to-opportunity and opportunity-to-audio separately |
| A2 | Speak five natural sentences at normal distance, then five farther away | Every missed/truncated utterance is counted against the human record; inspect raw input/VAD/ASR stages before blaming meaning |
| A3 | Pause mid-sentence, then continue; separately finish a thought and wait | Rex preserves unfinished words and avoids an early reply; a completed thought can receive an answer |
| A4 | Ask for a longer answer with a distinctive final phrase | Listen through the end; generated text, complete audible content and playback receipt agree |
| A5 | Cancel once during generation, once during playback using Enter | Prompt cancellation, no queued tail/revival; the next request works. Measure actual cancellation delay |
| A6 | Request no initiative, ask a direct question, wait, then explicitly resume initiative | Quiet is recorded and survives the direct answer; resumed initiative is possible |
| A7 | Let a name/invitation question go unanswered for a few minutes | At most one short no-answer-needed joke, then no repeated/new question until meaningful change; a declined name is not requested again |
| A8 | Briefly leave/re-enter or obscure the camera during an ordinary spoken line | General speech already playing finishes through a visual gap; later context reflects current presence |

Repeat a failed case once without changing code to distinguish intermittent behavior.
Preserve both outcomes. Stop the affected pass for corrupted writes or repeated
unusable capture/playback, and investigate before exposing more participants.
Spoken barge-in during speaker playback is an existing echo-gating limitation;
terminal stop is the cancellation acceptance path. Do not claim AEC is present.

## Pass 2: identity, faces and memory

Use independently labeled Bret/Jeff turns, not names inferred from transcripts.

| ID | Action | Required observation |
| --- | --- | --- |
| B1 | Each introduces himself, then alternate sentence-length turns | Name save, face association, pending voice evidence and completed voice enrollment are reported separately; only compatible samples enter each profile |
| B2 | Continue naturally until enrollment completes or remains deferred | Record qualifying samples/seconds and rejection reasons. Three compatible clips/eight voiced seconds are eligibility requirements, not a guarantee of completion |
| B3 | A–B–A turns, short yes/no replies, laughter, near/far seating | Score correct names, wrong names and abstentions separately. No person inherits the previous speaker's identity |
| B4 | Bret visibly silent while Jeff speaks off camera; then swap | Visible face does not substitute for acoustic identity or authorize a wrong-person write |
| B5 | Introduce the other person; answer a name question with an unrelated activity; correct a mistaken name | Mentioned person, introducer and speaker stay distinct; no activity becomes a person's name; cancelled association stops collecting |
| B6 | After supported identity, each saves a different non-sensitive fact | Correct subject, source turn, source speaker and status; acknowledgment follows a successful write |
| B7 | Quit/relaunch, recognize new utterances, recall both facts, correct one and recall again | Persistence works beyond chat history; correction replaces current value while preserving provenance |
| B8 | State a fact about the other person; offer a fact during ambiguous/overlapping speech | Third-party attribution is explicit; uncertainty creates no guessed accepted self-report or biometric write |
| B9 | Reset/forget a disposable identity in an isolated test store, including pending evidence | No active recall, audio/reference or pending association survives deletion; diagnostics have their separate documented retention |

Do not count enrollment samples as recognition tests. Add simultaneous speech and
rapid changes as a separate difficult cohort; approximate split proposals must not
authorize personal writes. Face-only correction should preserve valid voice/name
state; exercise it first with disposable state, not a deliberately damaged real profile.

## Pass 3: social behavior, intentions and memory opportunities

Use natural paraphrases, including wording not used in development probes.

1. Greet and introduce a topic, change it, then leave a genuine lull. Judge whether
   Rex follows the new topic rather than a stale welcome intention. Record completed
   speech to next opportunity and actual first audio; backoff is not forced speech.
2. Ask the other human a question and wait. Address Rex briefly, then return to the
   human exchange. He should answer the direct request without taking over the floor,
   then become eligible again when the human question is resolved.
3. One participant closes a topic; the other expresses interest in it. The first
   person's boundary must remain. Have the original participant explicitly reopen it.
4. Try receptive tiredness, a request for light silliness, and an explicit request
   to sit quietly during a fictional serious situation. Score welcome initiative
   as well as unwanted comments. No actual private distress is needed.
5. Share an innocuous private follow-up alone. Restart with another participant
   present: no private disclosure. Separately allow a different follow-up to be shared.
   Check appropriate use, no assertion that a plan occurred, no promised unattended
   reminder, and no repeated completed callback after restart. Date-bound eligibility
   can first be tested with fake clocks; do not alter the machine clock.
6. Talk naturally for at least 15 minutes. Include silence, human-to-human conversation,
   topic changes and indirect references to Rex. Do not keep prompting him to fill time.

Each participant independently rates warmth, humor, timing and desire to keep talking
from 1–5, with an example. Count welcome contributions, intrusions, repeated topics,
missed welcome openings, unnecessary identity questions and unanswered-question load.
Ratings are human evidence, not an automatic mood-improvement score. Keep participant
disagreements visible. No numerical social release threshold is agreed yet.

## Pass 4: visual inspection, correction and photo memory

| ID | Action | Required observation |
| --- | --- | --- |
| V1 | Ask about a visible object, repeat unchanged, then change only its color/location | Fresh inspection when needed, useful reuse when unchanged, no invented shape or identity change |
| V2 | Correct one detail in a multi-detail description; then withdraw several related descriptions | Correct scope, useful unrelated details retained, superseded observations not reused as current facts |
| V3 | Leave a scene unchanged across several lulls, then introduce one new object | No repeated visual riff presented as novelty; observation retention and actual spoken remarks stay distinct |
| V4 | Teach a uniquely named pet/object, append a detail, restart and recall | Correct photo/name/source; save or explicit deferral after teaching inspection; added notes preserve other details |
| V5 | Give an unnamed fragment, then explicitly name the intended subject | Clarification before a subject-specific change; no accidental overwrite of another entry |
| V6 | Present two pets together; ask ambiguous “the smaller one” questions | Individual target evidence or clarification; no whole multi-pet overview saved as one pet |
| V7 | Recognize each pet using different held-out images/views and swapped positions | Score correct identity, wrong identity and abstention; do not treat a correct save or fresh inspection as recognition success |
| V8 | Correct and forget a disposable photo entry | Persisted catalog and referenced files reflect the operation; acknowledgment matches execution |

Proposed diagnostic cohort for V7: ten new views per pet plus ten unfamiliar/ambiguous
views, with labels fixed before evaluation. This is a small screening set, not a
general accuracy claim. Record identity and visual-description accuracy separately.
Keep individual-animal recognition marked limited until evidence supports a release
criterion chosen before evaluation; the current record already contains failures.

For news/weather, first check source, publication/retrieval time and current validity
against the returned provider data. Then observe relevance and repeated-delivery
handling during a lull. Quiet/floor boundaries still apply. Exercise expired sources,
provider outages and interruption receipts in fake-provider tests before live checks.

## Passes 5–6: repeatability, accuracy and resources

Repeat on different days/seating with unchanged settings. Run matched camera-off and
full-camera/object configurations with similar utterances, followed by at least a
30-minute full-load run. Measure the actual M2/16 GB target separately from the dev
Mac. Inventory loaded models, RSS trend, queue pressure, frame age, failures and
playback underruns. Do not substitute isolated face inference for combined-load data.

Robot enrollment is blocked until the required microphone servo hold exists and is
verified. Existing ReSpeaker direction support does not satisfy it. Read-only
direction/load testing is separate and must not silently collect robot voice profiles.

Build a frozen human-labeled manifest with at least 100 clean held-out utterances
(proposed balanced minimum: 50 each for Bret/Jeff, across sessions), plus separate
short, unknown, off-camera, rapid-switch, overlap and echo cohorts. Identify utterances
from the human record, so missed captures are counted instead of disappearing from
the denominator. Keep enrollment, tuning and held-out evaluation separate.

```sh
.venv/bin/python -m r3x replay local/dataset/manifest.json
```

Use production segmentation; `--oracle-segments` is diagnostic only. Replay freezes
learning and personal-memory mutation. `--brain` adds explicit paid hosted inference;
it still does not test live microphones, playback, proactive scheduling or hardware.

Retain the existing proposed acoustic gates: at least 95% correct among named clean
decisions **and** at least 85% named coverage, for single-speaker captures with at
least two voiced seconds. Report per person/device/distance, plus aggregate counts.
Report false-known unknowns, missed switches, overlap errors and abstention separately.
Zero named learning writes on labeled mixed/echo audio; zero observed wrong-person
accepted claims or biometric writes in the declared acceptance cohort.

Measure end-of-human-speech to actual first audio, including endpoint delay, ASR,
speaker analysis, model/tool calls, TTS and queueing. Separate ordinary/tool/proactive
turns and cold/warm runs. Report n, median, p95, failures and timeouts; small-cohort
p95 is descriptive. The owner finds roughly under four seconds acceptable; report
the fraction exceeding four seconds as well as tails rather than inventing a p95
guarantee. Any matched v2 comparison is a separately scheduled baseline, not permission
to operate or modify v2. Non-regression alone is not responsiveness acceptance.

Inspect `respond_with_intent` share, primary versus continuation requests, retries,
cache/token usage where available, and cost. Missing usage remains unmeasured. Test
network/write/provider failure with existing fakes first; any later real fault test
must be scoped and reversible, rather than broadly disrupting the user's network.

## Hosted regression and evidence review

Before paid runs, select the affected existing tools and a request/spend bound.
Useful suites include `check_meaning.py`, `check_addressee.py`,
`check_conversation_repairs.py`, `check_proactive_conversation.py`,
`check_conversation_feedback.py`, `check_memory_opportunities.py`,
`check_conversation_intentions.py`, `check_transcript_context.py`,
`check_visual_dialogue.py` and `check_visual_corrections.py` under `tools/`.
Inspect each tool's current options and fixture scope. The intention evaluator
defaults to a historical A/B baseline and three repetitions, not a single cheap
current-prompt request. Pace requests and retain rate-limit failures.

Audit vacuous/mismatched assertions before treating suite totals as acceptance:
[persona evidence](PERSONA_REWRITE.md) documents a name-save/enrollment mismatch and
a passing “stop learning” case without the relevant cancellation tool. Judge the
original words, selected operation, resulting state and actual outcome together.
After fixes, add fresh paraphrases/sequences instead of reporting tuned cases as
held-out results. Simulation-only action continuity remains covered by the ordinary
contracts; do not add physical motion to these sessions.

For each case, retain this record in ignored validation storage:

```text
Run / revision / settings / device / evidence category:
Case ID / timestamp / source turn / human-labeled speaker and addressee:
What the human said or presented:
Expected behavior:
What Rex actually played (or silence):
Proposed tool / executor result / persisted state / delivery receipt:
Result: PASS | FAIL | BLOCKED | NOT RUN
Evidence paths / missing evidence / severity / next retest:
```

Use separate categories: synthetic contracts, real models on saved media, hosted
fictional/saved-media inference, live devices, and human social judgment. A failed
capture blocks that case's downstream semantic conclusion; retain the capture
failure rather than scoring an absent model response as a semantic pass.

After every session, review before editing prompts or thresholds. Trace failures in
order: capture → VAD/ASR → attribution → context/model decision → state execution →
output/audience cancellation. Fix one demonstrated cause, run its focused regression,
then repeat the failed live case and a fresh variant. Retain the original failed run.

Close a feature only with linked evidence, denominators and remaining limitations.
Update IMPLEMENTATION_STATUS.md and the relevant feature document after validation,
not merely because code exists or a synthetic suite is green. Report partial
acceptance explicitly: for example, “photo persistence passed; pet identity failed.”
