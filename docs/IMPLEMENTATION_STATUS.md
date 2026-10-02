# Current implementation status

Updated October 1, 2026. This is the consolidated
checklist for current scope.
The linked plans retain dated implementation history and test results. An earlier
slice's limitations or test count do not describe the final implementation.
Documentation does not authorize new hardware operation or feature expansion.

October 1 [agency increment 1](AGENCY_IMPLEMENTATION_PLAN.md): working interests,
model-selected reconsideration, and inactivity diagnostics are implemented. Generic
initiative no longer requires a visible person or camera. The fixed 20/60-second
audible floors and automatic continuing-lull backoff are removed. General interests
can survive head turns; private notes and memory remain audience-filtered. Losing
visual audience evidence hides scoped notes without blocking a fresh generic
consideration. Configured public feeds also work without a camera. The scheduler
retains one versioned request, 3–300-second requested delays, a twelve-attempts per
five-minute cap, and backoff for unchanged silent decisions. Plain replies retain
one primary generation; inspection retains its existing bounded continuation.

Verification: **817 blocked-I/O contract checks pass** (66.1 seconds). New production-path
contracts use synthetic audio/camera, fake clocks, simulated inspection and temporary
stores. No hosted model, acoustic model, feed fetch or hardware was operated.
Model selection quality, actual costs, M2 load and live cadence remain unmeasured.
That first increment left physical attention and moving-view continuity pending;
the second-increment entry below supersedes those limitations. Invitation-lapse
behavior is implemented in increment 3 below.

October 1 [agency increment 2](AGENCY_IMPLEMENTATION_PLAN.md#increment-2-implementation--october-1-2026):
physical directional looks and finite before/after-speech gestures now use the
existing output/servo owners after successful configured robot startup. Explicit
held poses/drive stance take priority; receipts distinguish controller pulses from
simulation and actual speech. Camera motion/settling gates both scene snapshots and
deliberate current-view captures. A bounded view history suppresses returning-view
ambient cues; explicitly general visual descriptions and impersonal heard remarks
survive changing visibility, with private context still audience-filtered. No new
model, service, transport or automatic planner is added. Object-targeted physical
gaze, room-wide object identity and reliable semantic non-repetition remain unproven.

Increment 2 verification: **833 blocked-I/O checks pass** (66.1 seconds), including 16 new
agency-2 contracts. Checks use fake pulse transport,
synthetic images/audio, fake clocks and temporary stores; no devices or hosted
providers ran. Live motion/clearance, camera settling, acoustic effects, combined
M2 load/cost/latency and conversational quality remain pending.

October 1 [agency increment 3](AGENCY_IMPLEMENTATION_PLAN.md#increment-3-implementation--october-1-2026):
nullable invitation proposals on existing tools now track audible social invitations,
model-selected review and explicit answered/declined/superseded/lapsed outcomes. One
pending record and six recent closed records preserve heard words, source, revision
and audience scope. Review uses the existing scheduler, with no new timer or model.
The shared prompt adds varied character/interest/silent activity choices; human floor,
quiet, private memory and identity clarification boundaries remain separate.

Verification: **851 blocked-I/O checks pass** (70.4 seconds), including 18 new invitation/agency checks. Synthetic production-path checks include an
unchanged-room five-minute sequence with four scripted primary decisions and no
repeating timer. This is contract evidence, not semantic or live social acceptance.
[Prepared acceptance scenarios](AGENCY_ACCEPTANCE.md) cover the remaining hosted/live
comparisons and measurements. No paid provider or hardware ran; no social threshold,
actual cost, combined M2 performance or semantic accuracy is claimed.

September 29 [engagement repair](LIVE_RUN_CONVERSATION_REPAIRS.md#september-29-engagement-repair):
rejected audio no longer resets lull timing or crowds out conversation history;
ordinary replies survive optional intention/audience failures; conversation can
continue without a greeting plan; empty tool-result speech has bounded recovery.
Explicit last-reply context and revised guidance address repeated premises/questions.
765 blocked-I/O checks pass, plus seven hosted fictional decisions with synthetic
output. Real-room timing, acoustic capture and social acceptance remain open.

September 29 [startup/playback cutoff repair](LIVE_RUN_CONVERSATION_REPAIRS.md#september-29-startup-and-playback-cutoff-repair):
microphone liveness tolerates future ADC timestamps; speech commits at PCM
submission before its delayed start notification; rejected noise no longer removes
the startup greeting requirement. 772 blocked-I/O checks pass. Servo-noise ASR
false positives and physical audibility remain unverified; no hardware was run.

September 30 [cross-session conversation memory](CONVERSATION_MEMORY.md):
completed exchanges survive restart; relevant accepted facts and prior exchanges
reach greetings/replies; the LLM can retrieve dated conversations and save/retract
source-linked episode interpretations. Private audience and speaker uncertainty
are preserved. **785 blocked-I/O checks pass**, including 13 new regressions.
Four hosted fictional cases made seven model calls; no hardware was operated.
Old source-only records remain undated; natural-language embellishment, acoustic
attribution and real-room acceptance remain open.

September 30 audience-memory repair: recognized-camera conversations now retain
trusted exchanges despite acoustic identity abstention. Audience and speaker
provenance stay separate across restart, automatic context, tool recall and
source-linked summaries. Each archive attempt reports a saved/skipped reason.
**792 blocked-I/O checks pass**; seven additional contracts cover the reported
failure, upgrade, source forgetting, quality gates and audience changes. Four
hosted fictional cases verify recall after reopening SQLite, empty-period handling,
a memory-informed greeting and episode save. No hardware operated. The greeting
still embellished an untested motor as “dramatic”; social grounding and live
acoustic/dev-Mac acceptance remain open. Previously skipped exchanges are not
reconstructed automatically. See [memory evidence](CONVERSATION_MEMORY.md).

September 30 [turn-taking repair](LIVE_RUN_CONVERSATION_REPAIRS.md#september-30-turn-taking-repair):
queued speech carries capture/playback chronology; the LLM favors brief replies
and listening to acknowledgments/unfinished answers. Unsolicited speech waits at
least 20 s after replies/greetings and 60 s after other proactive speech. Direct
human requests remain immediate. **796 synthetic blocked-I/O checks pass**;
eight hosted fictional cases pass participation outcomes. No hardware operated;
real conversational comfort and overlap capture remain unverified.

October 1 [character and model evaluation](CHARACTER_EVALUATION.md):
ordinary conversation now uses GPT-6 Luna at low reasoning with a more opinionated,
contextual teasing persona. Unblinded review of 22 paired conversations favored
the revision 9/22, baseline 5/22, with 8 ties; final model cross-check cost was 73%
lower on Luna, with slower completion. **797 blocked-I/O checks and 8/8 hosted
participation cases pass.** Final hosted memory runs confirm recall and saves.
Cache-write/reasoning telemetry and tool-result wording are updated. No hardware
operated; generic jokes and live character acceptance remain open.

## Completed software

[Short-speech capture repair](SHORT_SPEECH_CAPTURE.md) removes a silent pre-ASR
duration veto; 590 synthetic checks pass, with live acoustic acceptance pending.

Startup hardware notices and camera-unavailable voice operation are implemented;
584 synthetic checks pass, with live acceptance pending. See [startup health](STARTUP_HEALTH.md).

September 17: [camera freshness and face-distance fix](FACE_DISTANCE_FIX.md)
removes repeated startup frames and raises detector detail to 960-pixel long edge.
Camera-only recognition passed; combined conversation/group acceptance remains open.

| Status | Capability | Implementation and evidence |
| --- | --- | --- |
| Implemented; live acceptance pending | Session intentions and bounded working interests, one-call revisions, model-selected reconsideration, audience/memory filtering and delivery-aware lifecycle | [Plan and implementation](CONVERSATIONAL_INTENT_PLAN.md) |
| Implemented | Microphone capture, transcription, one conversation owner, streamed speech, cancellation, delivery receipts, bounded history and network retry | [Operations](CORE_OPERATIONS.md), [initial slices and evidence](INITIAL_TEST_PROGRAM.md) |
| Implemented; combined live acceptance pending | ReSpeaker channel-0 capture, V2 host gain, read-only direction provider, source-linked voice/face/direction context and competing-direction learning guard | [ReSpeaker evidence and limits](RESPEAKER_INTEGRATION.md) |
| Implemented; live reliability incomplete | Persistent face/voice profiles, self-introduced names, compatible conversational enrollment, restart, correction and forgetting | [Face path](FACE_ATTRIBUTION_PLAN.md), [introduction-first recovery](INTRODUCTION_RECOVERY.md) |
| Implemented | Proactive arrivals/lulls, shared reply/initiative context, quiet/resume, conversational feedback, dated memory opportunities, news/weather and scene inputs | [Proactive stages 1, 2, 3 and 5](PROACTIVE_IMPLEMENTATION.md), [sources](OBSERVATION_SOURCES.md) |
| Implemented | Tool-selected current-camera inspection, rich visual descriptions, bounded visual memory and exact-image description reuse | [Current-view inspection](CURRENT_VIEW_VALIDATION.md), [attention slices](ATTENTION_IMPLEMENTATION.md) |
| Implemented | Related-record withdrawal, detail-preserving revisions, visual novelty context and actual spoken-remark records | [Visual correction](VISUAL_CORRECTION.md), [integration pass](CORE_INTEGRATION_PASS.md) |
| Implemented; physical acceptance pending | Physical directional attention and finite gestures; simulated visual-target attention; movement/settle/capture sequencing, exclusive motion ownership and separate executor/speech outcomes | [Attention slices 1–3](ATTENTION_IMPLEMENTATION.md) |
| Implemented; in live use, acceptance pending | Head/chest startup/fade, playback-driven speaking LEDs and validated LLM animation tool including requested eye blinks | [LED integration and gates](LED_INTEGRATION.md) |
| Implemented; in live use, acceptance pending | Servo lifecycle including throttle/elbow, playback-driven talking motion with addressee gaze, quiet-time idle hero/poker/throttle arms and head looks (face in front or around the room), spoken normal exit, LLM-selected held poses and bounded control of all eleven joints, bounded microphone holds, cross-version resource handoff | [Servo integration and gates](SERVO_INTEGRATION.md), [operator services](OPERATOR_CUTOVER.md) |
| Implemented; floor-tested, newest changes simulated only | LLM-selected drive-base plans (turn, move, face, approach, find person, vision seek of described things), continuous approach while hosted looks run, path-aware clearance and detours, radar bodies as bearing priors, arm/head drive stance, swing/clearance gates, truthful receipts, self-initiated moves including unseen radar presence, back-off reflex, blocked-path route planner, V2 motion sounds, and live battery/obstruction/radar/climate readings for the model | [Drive base integration](DRIVE_BASE_INTEGRATION.md) |
| Implemented; in live use | V2 speech leveling (−20 LUFS) on every phrase; V2 startup sounds with servo wake-up, short cached please-wait/ready lines and spoken hardware notice; stop phrases wrapped or repeated ("Oh, stop!"); unreliable noise transcripts kept but never answered; one-time microphone recovery after a silent input | [V2 reference](../V2_REFERENCE.md#speech-leveling-and-startup-audio--september-27-2026), [drive repairs](DRIVE_BASE_INTEGRATION.md#talkative-arrivals-noise-replies-surroundings--september-27) |
| Installed and running | Wake supervisor ("wake up rex", chimes, charger cues, mic-wedge relaunch, V3 auto-update) and servo/LED/battery menu-bar apps copied from V2 and run by the four LaunchAgents; no-motion handover measured at 0.7 s | [Operator services](OPERATOR_CUTOVER.md) |
| Implemented | Action continuity after interruption, bounded outcome history, no automatic resumption of abandoned work | [Integration pass](CORE_INTEGRATION_PASS.md) |
| Implemented | External-review repairs, scene-change cutoff repair and introduction recovery | [Review follow-up](REVIEW_2026_09_13.md), [introduction recovery](INTRODUCTION_RECOVERY.md) |

The agreed core software slices have no outstanding feature implementation stage.
Implemented does not mean acoustically reliable, socially accepted or free of bugs.
The latest blocked-I/O result is recorded with the October 1 agency increment above.
The preceding **756 checks** covered the September 28 voice
head-turn reflex (camera-aimed, taking over held looks), say-before acknowledgments, uncapped mixed move/light sequences, a wave, look-then-describe, and no
replies to untrusted transcripts; the earlier 645 included 45 [drive-base contracts](DRIVE_BASE_INTEGRATION.md#verification), including the
[greeting-led intention contracts](CONVERSATIONAL_INTENT_PLAN.md) and the
[prompt-caching repair](PROMPT_CACHING.md), plus 48 new
[servo/operator/robot contracts](SERVO_INTEGRATION.md#verification) and 20
[LED contracts](LED_INTEGRATION.md#verification-and-remaining-acceptance).
Four additional V3 tests cover cooperative legacy-supervisor registration,
acknowledged release, timeout and restart rejection.
Three additional talking-motion contracts cover the live-feedback repair: missing
head/elbow/wrist channels, independently repeating lanes and pulse-arrival drain.
Six additional idle/elbow tests cover bounded random motion, quiet scheduling,
preemption, microphone holds and lifecycle shutdown.
Prior counts in linked documents describe their dated checkpoints.

September 20: [audience continuity and unheard-lull recovery](LIVE_RUN_CONVERSATION_REPAIRS.md#september-20-audience-continuity-and-unheard-lull-recovery)
now settle the full camera audience before new proactive work, preserve supported
named presence across spatially continuous track replacements for general speech,
and allow one paced retry of an unheard camera-cancelled lull. Prompt text and
identity/enrollment policies are unchanged. Synthetic contracts and recorded-camera
metadata were checked before the solo retest below.

Subsequent [solo retest](LIVE_RUN_CONVERSATION_REPAIRS.md#solo-retest-evidence): greeting
plus two proactive contributions completed, with no audience cancellations and no
playback underruns. Quiet/resume executed successfully; later silence was explicitly
model-selected. Live retry recovery was not exercised. Repeated visual remarks,
roughly 5.85-second first-audio latency on two direct replies, and missing conversational
intentions remain open; group acceptance is unchanged.

## Acceptance and defect investigation still open

- Live enrollment, alternating speakers, wrong-person assignments and abstention,
  including recognition across launches and room/device changes. Successful
  recognition in individual runs is progress, not broad group acceptance.
- Missed speech under difficult acoustic conditions and combined model/camera load.
  The historical raw-input/VAD failure is not declared fixed by later successful runs.
- Interruption and playback behavior after the latest repairs, including long replies
  and unreliable network conditions.
- Proactive timing, welcome participation, restraint, repetition, humor and visual
  description/correction quality with varied real scenes.
- End-to-end latency, tail latency, memory use and playback under the robot's base
  Apple M2 / 16 GB workload. Measurements on other Macs do not certify that target.

[Proactive stage 4](SOCIAL_ACCEPTANCE.md) is this live acceptance work, not another
coding slice. Failures found during acceptance can require scoped code fixes.
Synthetic tests, hosted synthetic-image probes and live hardware evidence remain
separate categories.

## Future coding requiring separate scope

| Future capability | Boundary of current implementation |
| --- | --- |
| Public installer and optional hardware configuration | Future installer built around existing `local/settings.toml`, independent optional capabilities, clear missing-device behavior, selected dependency/model downloads and optional service setup. No new settings or installer implemented. [Public-install plan](PUBLIC_INSTALL_PLAN.md) |
| Physical motion and expression | [Servo integration](SERVO_INTEGRATION.md) includes explicit live composition and motion/enrollment guards. Physical commissioning, obstruction feedback, live manual-takeover acceptance remain. Finite model-selected physical directional looks and gestures are implemented. Explicit requested head turns, all-joint poses, addressee gaze while talking and idle face/room looks are implemented; object-grounded autonomous gaze remains pending. Base motion followed by a fresh camera question is implemented through [drive_base](DRIVE_BASE_INTEGRATION.md). |
| Continuous speech/motion coordination | Playback-driven talking motion shares the servo owner; synthetic checks cover actual audio start/stop and failures. Live validation, richer V2 expression, word-level alignment and continuous gaze blending remain. |
| Live tracked-object attention after movement | Target/version contracts exist in simulation. Physical directional looks and settled captures are implemented; tracked room-object gaze and mechanically verified arrival remain pending. Current-camera descriptions do not supply those controllers. |
| Optional vision extensions | Adaptive image detail, general inspection-request coalescing and supported cross-session visual association are future options, not missing requirements of the completed core. Exact-image description reuse already exists. |
| Motion-aware proactive noticing (partial implementation) | Commanded head/base motion, settling and bounded returning-view suppression are implemented. Independent subject motion, robust object continuity and unknown/manual camera movement remain future work. [September 17 direction](PROACTIVE_CONVERSATION.md#future-idea-motion-and-new-arrivals-as-attention-cues) |
| Compass heading | The drive base publishes raw QMC5883L magnetometer counts; V2's calibrated, current-gated compass fusion (`hardware/compass.py`, calibration file) is not ported. V3 uses gyro yaw only. |
| Owner-requested companion features | Jeopardy, optional local voice-cloning/impersonation bits, room exploration, progressively interest-tailored idle banter/news, and the September 18 “Do you ever think” long-lull thought experiments. Purposeful motion is implemented in software ([drive base](DRIVE_BASE_INTEGRATION.md)); maps, localization and exploration are not. General news/proactive inputs already exist; these extensions remain future work. [Feature roadmap](FUTURE_FEATURES.md) |
| Wider roadmap | Games, physical LEDs, richer relationship/biography systems, semantic memory search, offline conversational providers and a polished GUI remain separate future work. |

Mouth-motion/active-speaker research is not an active implementation task. The user
declined that experiment; no mouth-motion model or enrollment dependency was added.

## Reading the historical plans

Use this checklist for current completion status; use each feature document for
contracts, design rationale and dated evidence. The initial program's numbered
slices and the proactive plan's numbered stages are separate sequences. In
particular, the initial program's deferred expansion must not be confused with
proactive stage 5, whose news/weather/scene inputs are implemented.

September 17 reply-time face context: fresh cached `current_view` is separate from
speech-time attribution/enrollment evidence; 405 blocked-I/O tests pass. Live
acceptance pending. See [face timing evidence](FACE_DISTANCE_FIX.md#reply-time-face-evidence-september-17).

September 17 scoped object/photo addition: paced RF-DETR Nano ONNX detection and
file-backed named photographic memory are implemented and enabled locally. The
shared model can inspect/compare, teach, correct and forget pet/object references.
413 blocked-I/O checks and bounded camera/hosted/load probes pass their documented
contracts. Individual-animal accuracy, ambiguous spatial references and full live
conversation acceptance remain limited; no dedicated room model was added. See
[implementation and evidence](OBJECT_PHOTO_MEMORY.md).

Photo-memory follow-up: 417 blocked-I/O checks pass with explicit save/defer teaching,
source-name checks, additive notes, and multi-pet overview rejection. Saved-photo
hosted checks still misidentified the dogs despite fresh inspection; reliable
individual-animal recognition remains open. See the
[handling follow-up](OBJECT_PHOTO_MEMORY.md#teaching-and-correction-safeguards-september-17-follow-up).

User-requested [Luna comparison](LUNA_COMPARISON.md): saved-photo trials completed
with isolated galleries and silent output. Luna was cheaper but slower in the small
paired test and failed a breed-update acknowledgment; production remains Mini/low.

The subsequent [Terra trial](LUNA_COMPARISON.md#terra-follow-up--september-17)
completed the same five saved-photo turns with successful breed persistence and
fresh inspections for both correct recognition answers. Median text completion was
3.27 s; estimated cost was $0.18589, about 2.9 times the Mini trial. This remains a
small provider comparison, not live acceptance; the production model is unchanged.

Latest [live-run conversation repairs](LIVE_RUN_CONVERSATION_REPAIRS.md) address
presence-driven greeting cancellation, interrupted intent speech and transcript
interpretation. [Model-selected JSON timing](NEXT_CONVERSATION_SCHEDULING.md) is now implemented
through all three agency software increments. Acoustic and live playback acceptance remain pending.

September 18 [prompt-caching repair](PROMPT_CACHING.md): per-turn identifier enums in
transmitted tool schemas had left every tool-enabled request uncached. The adapter now
sends a static tool list with `allowed_tools` and grounds returned identifiers locally.
A paid synthetic probe measured 11,008 of about 12,400 input tokens cached per primary
call. Hosted semantic suites and live conversation have not been rerun on this change.

September 19 [persona consolidation](PERSONA_REWRITE.md): the conversation prompt was
rewritten from 423 to 242 lines with one priority order, the owner's rulings on names,
appearance, intentions and unanswered questions, and the request context moved after the
history. An invalid intention note no longer fails a turn, and unknown identifiers are
rejected by the state owner rather than the adapter. Paced hosted suites scored 141/168
against 135/168 for the previous prompt, as development evidence only. 467 blocked-I/O
checks pass. Live conversation has not been rerun.

September 27: all-joint model tools and animation argument repair pass 597 synthetic blocked-I/O checks. No new live hardware or hosted-model validation; clipped transcription remains unresolved. See [servo evidence](SERVO_INTEGRATION.md#september-27-all-eleven-model-selected-joints).

September 27 [first floor run repairs](DRIVE_BASE_INTEGRATION.md#first-floor-run-and-repairs--september-27): wide-angle camera calibration, seated-person approach distance, 1 s pre-speech buffer, audible speech finishes unless explicitly stopped, and head gaze toward the addressee. Synthetic checks only since the run.

September 27 [drive base](DRIVE_BASE_INTEGRATION.md): 645 blocked-I/O checks pass with a simulated ESP32. Paced hosted probes: gpt-5.4-mini planned 16/20 synthetic cases; with the geometry-triggered gpt-5.6-terra route planner, 19/20. No wheels moved; the floor test awaits the owner.

September 27 greeting-cutoff repair: 600 blocked-I/O checks pass. General proactive intent replies now finish across face-track changes after audio starts; pre-playback audience validation and targeted-question cancellation remain. Tests use synthetic audio/camera providers through the production runtime validator. No live hardware or hosted calls; audible acceptance remains pending. See [cutoff evidence](PROACTIVE_IMPLEMENTATION.md#september-27-greeting-interrupted-by-face-track-churn).

September 27, floor-test day: repeated live runs on the robot with wheels, radar,
servos, LEDs and camera. The findings and repairs are recorded in [drive base](DRIVE_BASE_INTEGRATION.md) sections from
"First floor run" to "Talkative arrivals":
- vision seek and door approach, path-aware clearance, detours and the hallway
  no-reverse rule;
- the continuous approach, motion sounds and arm/head drive stance;
- wrapped stop phrases and never answering noise transcripts;
- brief arrival reports, live base status and idle head looks.
Also a startup crash, where startup clips were fed whole and hit the playback drain
timeout, is fixed by streaming them in pieces with one-time microphone recovery.
729 blocked-I/O checks pass. Changes after the last floor run are synthetic only.
