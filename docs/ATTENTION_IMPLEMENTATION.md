# Intentional attention and coordinated action

September 12, 2026. The user authorized this implementation document and the first
slice, then authorized proceeding with slice 2. Physical operation and the wider robot roadmap are not part of this slice.

## Goal

Rex should attend to people and his surroundings for conversational reasons. He
can look away to consider something, shift attention toward an interesting object,
share a reaction across a group, or stay still. Face tracking follows a selected
target; it does not independently choose what deserves attention. Looking away
must not discard the conversation, change speaker identity, or label someone bored
or disengaged. Delight and warmth remain goals, with restraint around serious topics.

A timer may expire an intent or make consideration eligible. It must not prescribe
a recurring performance. The existing conversation model chooses optional actions;
ordinary replies keep one primary generation and no mandatory planning preamble.
Continuous tracking and smooth trajectories belong to local controllers, not a
continuous stream of LLM calls.

## Ownership and target contract

One conversational owner proposes intent. CoreState validates and owns session
attention, using explicit versions independently of the persistent store revision.
The output owner dispatches execution and coordinates its eventual speech and
motion channels. Providers return receipts; they never decide their own social acts.

The target contract distinguishes participant/face tracks, observed
object tracks, robot-relative directions and unfocused attention. A person ID is
not a spatial target. Located targets need an observation ID/time, coordinate frame,
calibration version and expiry; the executor must reject lost or ambiguous targets.
An object name generated from an image does not supply a verified bearing or depth.

An attention hold is bounded and can be superseded. Expiry releases the intent;
it does not command an automatic return to center. Cancellation/manual control can
invalidate a pending or active intent. Acoustic attribution, participant presence
and attention remain separate: turning away is not evidence that a person left.

## Implementation stages

| Slice | Work | Acceptance |
| --- | --- | --- |
| 1 — implemented | Versioned attention intent, optional silent directional choice through production Session, output-owned simulation, bounded recent receipts | Synthetic one-call reply/proactive sequences, no fake speech or facts, expiry/cancellation/stale receipts, disabled capability |
| 2 — implemented in simulation | Grounded face/object targets and attention transitions; coordinate/evidence contracts; perception after simulated movement | Lost/replaced targets, ambiguous people, calibration changes, camera movement versus independent scene change; no identity promotion |
| 2b — description-memory slice implemented; live acquisition/semantic evaluation pending | Rich hosted image descriptions for visual questions and selective attention; source-linked visual observation memory and semantic repetition context | Stable appearance versus genuine change, uncertain person binding, corrected descriptions, direct questions, cost and latency |
| 3 — implemented in bounded simulation | One spoken phrase with one target-aware primitive before/after playback, exclusive motion resource and separate receipts | Interruption, partial completion, stale targets, resource conflicts and failure independence; continuous blending remains hardware-integration work |
| 4 — planned | Calibrated hardware adapters, constrained trajectories, sensor feedback and movement/audio exclusion | Explicit bench and room acceptance, manual takeover, limits/settling, obstruction, camera occlusion, microphone geometry, combined M2 load |

The user selected slice 3 next; its bounded simulation implementation is described below.
Rich descriptive memory now has an implemented inspection-based slice. Live acquisition,
semantic evaluation and hardware integration (4) remain pending.
Existing conversation/identity/social acceptance remains unresolved and continues
alongside this work. Simulation cannot certify physical expressiveness or mood lift.

## Slice 1: actual behavior

`AttentionIntent` contains a correlated request ID, attention version, target,
purpose, brief rationale and monotonic proposal/expiry times. `AttentionState` owns
one pending/active intent and the last eight simulation receipts. It has no database,
background worker or timer loop. Expiry is evaluated when context is read or a
receipt is applied. A restart starts without an attention target. Forgetting clears
attention and its recent receipts; stop clears the active/pending intent.

The optional `shift_attention` tool offers **left, right, up, forward or unfocused**
as robot-relative intentions. Purposes are consider, orient, disengage_gaze and rest.
It deliberately does not offer a named person, object inspection, arbitrary servo
values, an emotion classifier, or a claim to have observed something new. Holds
are brief (3 seconds) or normal (8 seconds), bounded simulation choices rather than
hardware timing calibration. Future calibration must be measured before physical use.

The tool is exposed only when an attention adapter is explicitly injected into the
production `OutputOwner`. No live setting, automatic startup, model load, network
request or hardware driver was added. Normal `r3x boot` does not expose this tool.
The supplied `SimulatedAttention` must be explicitly started by the harness; it
executes synchronously, checks cancellation/expiry, and closes with Session.

A silent choice travels through the same primary model/tool route as a reply or
arrival/lull opportunity. CoreState checks capability, frozen-mode restrictions,
store revision and attention version before proposing. The output owner calls the
adapter outside the state lock. Session rechecks cancellation/store revision and
CoreState's attention record validates the correlated receipt before committing.
Changed proactive evidence uses the existing cancellation guard. No extra model
generation or TTS request follows a successful attention choice.

Only a receipt explicitly labeled `simulation` / `simulated` is accepted in this
slice. The next conversation snapshot includes active/recent attention and explicit
simulation limits. History preserves the user's words and records that no physical
movement, speech or new visual observation occurred. A silent attention choice does
not impose the 120-second human-floor hold used by semantic silence. Existing quiet,
floor and proactive pacing gates still govern unsolicited opportunities.

`attention_result` traces identify simulation, request, target and purpose; aborted
pending proposals produce `attention_aborted`. Speech remains independently marked
suppressed/none. These are not physical completion or successful visual inspection.
A separate general action-delivery ledger and simultaneous speech/motion are planned
in slice 3, not implied by this first adapter.

## Movement composition direction

Preserve V2's smooth procedural listening motion, gaze baselines and post-audio
comedic timing. Replace default event-to-animation mappings with contextual intent
and small composable primitives: gaze, tilt, lift, visor, arm emphasis and stillness.
The model chooses purpose, supported target and expressive parameters; local code
bounds and realizes trajectories. Recent performances help avoid repetitive
choreography. A few deliberate signature routines may remain available.

Execution must eventually arbitrate resources: gaze tracking, gestures, base motion
and microphone stabilization cannot independently write the same joints. Neck/base
movement invalidates old direction references; visor movement can obstruct vision.
Movement-contaminated audio cannot enroll a voice. Mouth motion stays excluded.

An inspect action must wait for completion/settling and a fresh post-movement frame
before claiming to have looked at something. The current scene-difference heuristic
has no egomotion compensation, so physical movement cannot simply be attached to it.
Fresh evidence may justify a later model decision; that is not a mandatory second
call for ordinary replies. Phrase timing must anchor to actual playback, with motion
and speech failures reported separately.

## Verification and remaining limits

Run `.venv/bin/python tools/run_checks.py`. The first slice adds 12 synthetic
contract tests through the production Session and output owner, bringing the suite
to **233 blocked-I/O checks**. Tests cover ordinary and proactive silent choices,
continuity into the next reply, unavailable/closed adapters, invalid targets,
expiry, invalid physical receipts, new speech, explicit cancellation, changing
opportunity evidence and held human floors. Temporary databases only.

These tests inject scripted model decisions; no hosted-model judgment, physical
movement, tracking accuracy, gesture quality, latency under load or human preference
was evaluated. No provider was started beyond the in-memory simulator. Real model
selection quality and social acceptance remain future evidence, not passed gates.

## Slice 2: grounded targets and post-movement evidence

Implemented September 12, 2026 after the user requested proceeding. This extends
the same simulator and tool; it does not add a second attention loop or expose
motion during normal live startup.

`AttentionFrame` is an immutable, explicitly simulated provider observation with
session/observation IDs, capture time, view epoch, calibration ID, coordinate frame
and at most 16 `VisualTarget` records. Each target has a stable track ID, face/object
kind, bounded descriptive label and normalized image box. Track IDs must remain
stable for one tracked entity and must not be reused for a different entity.
Neither a label nor a face track confirms a person or acoustic speaker. Coordinates
are image locations only: no depth, physical angle or calibrated trajectory is inferred.

An explicit harness publishes complete frames through `Session.observe_attention`;
empty frames represent no currently visible targets. No model tool can publish a
frame or invent an object track. Invalid sessions, stale/future/nonfinite times,
wrong view epochs, duplicate targets, unsupported coordinate frames and malformed
boxes are rejected before replacing evidence. Frames expire after two seconds.
Only one current frame is retained; no frame history or image storage is added.

`shift_attention` now offers current visual IDs alongside directional choices.
The state owner checks the ID was offered and still refers to a fresh track in the
same view/calibration. If the tracker refreshed the same target during generation,
the proposal uses its latest location. Execution receives the source anchor and
rechecks target availability before committing its receipt. Losing/replacing the
target or changing calibration rejects the pending result. This is synthetic
tracking evidence, not a detector or real-world recognition accuracy measurement.

After simulated completion (or explicit cancellation), the old view is invalidated
and its epoch advances. Context says `awaiting_post_movement_frame`; prior targets
cannot be used as a successful inspection. A new frame must have the new view epoch
and a capture time strictly after completion/cancellation. The first frame becomes
`new_view_baseline`, not evidence of an independent environmental change. Subsequent
same-calibration frames can be compared within that view. A missing active target
or changed calibration releases its attention hold without asserting that a person
left or disengaged. Expired holds do not command recentering.

Fresh synthetic evidence enters the next existing conversational snapshot. No
automatic second model call or new proactive wakeup is created by publishing a
frame. Selecting an action and receiving fresh evidence remain distinct transitions.
The existing live scene-difference provider has NOT been connected to simulated
view changes; egomotion compensation and a real face/object tracking adapter remain
requirements for hardware/perception integration. A simulator must not invalidate
or relabel images from a camera that did not actually move.

Validation: **243 blocked-I/O checks pass**, including 10 new slice-2 scenarios.
These exercise grounded object selection, two separate face tracks without identity
writes, updated target locations, target disappearance, calibration changes,
unoffered/stale targets, malformed frames, post-movement epochs/timing, and empty
post-movement views. Previous slice-1 cancellation and conversation tests still pass.
No hosted model, detector, camera, actuator, private data or physical inspection was
used. Simulated frame publication does not prove live object/face continuity,
coordinate calibration, motor settling, natural gaze choices or resource performance.

## Rich visual understanding: requested design addition

The user explicitly wants hosted OpenAI image descriptions retained as an avenue,
beyond object detector labels. Useful descriptions include appearance (hair color,
style, clothing/accessories and a rough apparent age range), room details, objects
and their relationships. Object tracks locate candidates; image understanding
provides descriptive meaning. Neither substitutes for the other. Current live
proactive snapshots already reach the hosted conversation model, but durable visual
observation memory and a general direct visual-query/inspection flow are not yet
implemented. This section is a design addition, not a claim that those paths exist.

Use three access routes: an explicit visual question, a conversation-owner request
to inspect a selected target, and a budgeted refresh when existing visual context
is missing/stale or potentially changed. Ordinary replies need no separate vision
call. Supply suitable fresh images to the main response call when possible; a
requested inspection can collect evidence after movement and continue only when
that evidence is available. Heavier descriptive extraction is bounded optional
perception work, never a second authority deciding what Rex says.

Cache bounded descriptions with observation/frame IDs, capture time, visible track,
view/calibration context, model provenance and uncertainty. Retain descriptive
observations for useful later recall under an explicit v3 visual-memory schema,
separate from accepted self-reported biography. A rough apparent age is an estimate,
not a birth date, verified age or identity. Uncertain visual-to-person association
stays track-scoped; no automatic promotion of acoustic identity or person facts.
Corrections, forgetting, freshness and audience discretion must cover these records.
Storage limits and persistence defaults are implementation decisions still to specify;
this proposal does not authorize importing V2 profiles or storing raw image archives.

Separate **observed**, **apparently changed**, **selected for comment** and **actually
said**. Give the conversational model the current description, relevant earlier
observations, and source-linked completed remarks. New wording or another capture
of the same brown hair is not itself novelty. A different hairstyle under comparable
views may be a change; lighting, occlusion and inconsistent model descriptions can
leave change uncertain. Description retention must not automatically create a
proactive invitation or a remark for every property.

The LLM decides whether a detail is worth mentioning, relates to the current
question, supports a fresh conversational angle, or is best left as background.
Stable details remain available for a direct question, reference resolution or
contextually relevant humor. Do not replace V2's hair-specific workaround with an
ever-growing blacklist of appearance traits. Python owns general contracts:
freshness, attribution, bounded acquisition/caching, cancellation, correction,
source validity and truthful delivery receipts. Relevance, repetition and social
appropriateness remain judgments of the conversational owner, evaluated with
multi-turn examples rather than assumed solved by a prompt.

Refreshes should reuse a suitable current frame/description, coalesce concurrent
requests for the same view, and prioritize explicit visual questions over background
work. Compare image size/detail and model choices with measured token/latency cost
before setting budgets. Keep the existing model initially; do not resurrect V2's
model defaults or add a resident detector solely for this design.

Acceptance scenarios must include unchanged hair across fresh frames/restarts,
changed hairstyle versus changed lighting, a direct question about a familiar
feature, two similar people, uncertain apparent age, a correction, and an interrupted
remark that was never heard. Include a welcome spontaneous visual contribution as
well as restraint; universal silence must not count as success. Report model calls,
image tokens, cache reuse, description quality and repetition separately.

## Slice 3: coordinated speech and gestures

Implemented after the user explicitly selected coordinated speech/gestures as the
next slice. The optional `respond_with_gesture` tool uses the same primary model
call to supply a spoken phrase, one expressive primitive (`nod`, `tilt`, `lift`, or
`arm_emphasis`), a supported target, subtle/normal intensity and before/after timing.
These are intent parameters, not prerecorded animation clips or random triggers.
Ordinary prose streams normally and requires no gesture tool or planner. The
coordinated phrase is bounded by the existing 1,200-character tool-text limit.

CoreState validates capability, source/store revision and any offered visual target.
The gesture includes its source anchor with image coordinates and calibration/view
provenance; the latest compatible target observation supplies the location. A
`Performance` envelope enters the existing bounded output queue. OutputOwner owns
speech and gesture execution, with one exclusive motion lock shared with silent
attention shifts. An occupied resource skips the optional gesture rather than
starting a competing writer. A completed expressive gesture releases the earlier
attention hold and invalidates the simulated view; fresh evidence is required again.

Timing is anchored to output, not estimated TTS duration or generated punctuation:

- `before_speech`: attempt the gesture before synthesizing/playing this phrase.
- `after_speech`: attempt it only after the sink reports the whole phrase completed.
  A failed or interrupted phrase cancels its dependent gesture.

At dispatch and completion, cancellation, store/attention version, target freshness
and a 30-second plan deadline are checked. A changed target or busy/unavailable
adapter produces a failed gesture receipt while the independent line may still be
spoken. Model instructions require the line not to assert motion success. Human
speech, stop and proactive audience invalidation use the existing turn cancellation
token. No gesture is allowed to survive as detached background animation work.

Speech delivery and gesture delivery are independent: completed text remains heard
if a later gesture fails/cancels; a successfully simulated pre-speech gesture remains
recorded if speech later fails. A sink's successful completion receipt is retained
even if cancellation races its return. Unreached queued gestures are recorded as
cancelled. `gesture_delivery` records correlated request ID, primitive, target,
intensity, timing, simulation status and failure reason. No physical completion is
accepted from the simulator. The last eight gesture outcomes enter later model
context alongside heard text, to support continuity and repetition judgment;
forgetting clears that context. This does not create durable personal facts.

`SimulatedGesture` is explicitly injected into OutputOwner and started by a harness;
Session closes it. No live setting/default, background model, actuator driver,
network call or speech-only fallback pretending motion succeeded was added.
Normal live startup still exposes no motion tools. The simulator has no trajectories
or timing sleeps and must not be mistaken for measured physical performance.

**257 blocked-I/O checks pass**, including 14 new production Session/output scenarios.
They cover before/after ordering, one primary generation, gesture failure versus
speech success, speech failure versus pre-gesture success, cancelled dependent
post-gestures, audio-completion cancellation races, resource contention, invalid
physical receipts, unavailable capability, proactive execution, expiry, visual target
loss and changed proactive evidence. All use scripted model choices, synthetic audio,
fake clocks and temporary databases. No hosted-model judgment or hardware was tested.

Current limits are explicit: one primitive per coordinated phrase; no arbitrary
choreography, overlapping gesture trajectories, word-level alignment, continuous
gaze blending or physical settling feedback. Serial execution is the conservative
resource policy until real joint controllers and measured timing exist. The next
hardware design must preserve these contracts while implementing interruption-safe
trajectories and measuring latency/combined load. Rich image descriptions remain the
separate planned avenue; this slice does not replace or implement that work.

## Composite visual inspection: motion, settling and capture

The user authorized an LLM-selected inspection action, including requests such as
"look behind you and tell me what you see." The simulation contract is implemented;
no regex interprets the request. The optional `inspect_view` tool supplies a direction
(current/left/right/behind) and the question to answer. Behind is an inspection
intent for a base rotation, not a fixed servo angle chosen by the model.

CoreState validates capability and store revision, releases old attention, and
invalidates the prior view before dispatch. The output owner acquires the same
exclusive motion resource used by gestures and attention. An explicitly started
`Inspection` coordinator uses injected simulation providers in this order:

1. Request movement (or a stationary view for current). Require a matching request,
   direction and view-epoch completion receipt; a request acknowledgment is insufficient.
2. Wait until the movement completion time plus the provider's settling interval.
   An early return is rejected. The default 0.5 seconds is a simulation value, not
   a measured robot calibration; real integration needs measured settling and
   motion feedback. Settling is executor-owned, not LLM-selected.
3. Capture an image strictly after settling. Require the requested direction/view
   epoch, matching source timestamp and an image no more than two seconds old at
   return. Old, mismatched or absent imagery cannot become the requested view.
4. Return structured stage/outcome metadata and, only on success, the fresh image
   to one tool-result model continuation. That continuation may optionally use only `describe_view` to save a source-linked
   interpretation and provide its reply directly; no further model call follows.

The image uses the existing hosted image-input payload path, now also available on
this inspection continuation. The model receives the original question/conversation
and explicit simulation metadata. No inspection image enters durable memory, trace
payloads or chat history. Stage metadata does enter history. Ordinary replies still
use one primary generation; an inspection uses that selection plus one continuation.
Failure may get a verbal explanation through the continuation, but supplies no image.

Cancellation is checked across movement, settling, capture, generation and output.
A version/view guard cancels stale inspection responses even after image acquisition.
A blocked turn never proceeds to capture. Movement success followed by camera failure
or cancellation stays visible as partial completion in `inspection_result`; it does
not become successful inspection. Providers receive a 15-second absolute deadline
and must honor it and the cancellation token. The coordinator rejects late returns;
it cannot forcibly interrupt a misbehaving blocking provider. No such hardware
provider is enabled by this slice.

Normal live startup exposes no inspection tool: the coordinator and its move/wait/
capture callables are explicitly injected into a simulation harness. Physical base
rotation, calibrated heading/coordinate transforms, motor settling feedback and live
camera capture after movement remain unimplemented. Synthetic image evidence is not
a live tracked-object frame; a later tracking adapter must publish its own valid
post-movement observations. Rich descriptive-memory/caching work remains planned.

**269 blocked-I/O checks pass**, including 12 new inspection tests. They exercise
ordered motion/settling/capture, blocked movement, early settling, stale/wrong-view
images, cancellation and partial receipts, deadlines, resource contention, one
continuation, image transport in the hosted adapter and view invalidation during
generation. These use synthetic providers/images and scripted model choices; the
hosted transport test uses fake HTTP. No real OpenAI request, camera, speaker or
robot was operated, and descriptive quality/physical inspection remains unverified.

## Rich visual memory: inspection description and reuse slice

Implemented after the user requested the next rich-vision slice. No regex extracts
appearance, selects an inspection or decides whether an observation is interesting.
The existing inspection result continuation can answer directly, or optionally call
`describe_view` with an observation ID, description, uncertainty and final reply
(null means retain the observation silently). The hosted adapter offers only that
narrow completion tool on a successful image-bearing continuation. A failed capture
cannot save a description; a mismatched evidence ID or another requested action is
rejected. There is no third generation or additional visual classifier call.

A v3-owned `visual_observations` table retains at most 32 descriptions, their model,
creation time, source inspection/frame/view/direction/capture metadata, original
question and source turn/event IDs, uncertainty, status and source-linked actual
spoken remarks. It stores no raw image and writes no people/claims/identity records.
These are model interpretations; rough apparent age, hair and clothing are not
confirmed personal facts. Source metadata keeps simulation explicit.

The last eight eligible descriptions enter model context as historical evidence.
Eligibility requires the same session and exact visible-face-track audience set;
there is no assumed person match across views, restart or a changed group. Empty
face sets are unresolved, not evidence of a sole participant. The contextual records
survive database restart for provenance but are not automatically surfaced in a new
session. Cross-session reuse needs measured person/place association and privacy
policy; it is deliberately not inferred from matching hair or description wording.

The optional `use_visual_memory` tool supplies a cached-reference reply directly
without another capture or generation. It records the selected observation's reply
only after actual audio completion. `retract_visual` withdraws an eligible description
based on a trusted current source utterance, keeping the correction in the normal
conversation and excluding the record from future cache context. All visual records
are conservatively removed when a person is forgotten because scene descriptions
currently have no reliable per-person partition. Ordinary replies remain available;
only explicitly selected descriptions receive structured visual delivery receipts.
Other heard replies remain in the shared conversation history.

The LLM sees description, uncertainty, source dates and previous actual remarks
together. Stable appearance stays available for relevant questions, but a repeated
frame or new phrasing does not establish a change or require a comment. There is no
hair blacklist, emotion/energy mirror or timer that promotes every stored attribute
into proactive speech. The rules bound evidence and execution; the model chooses
relevance, repetition, comparisons and whether to speak.

**279 blocked-I/O checks pass**, including 10 new visual-memory scenarios. They cover
source-linked saving, silent observations versus audible replies, no biography writes,
reuse without capture, audience/session isolation, persistence, invalid source rejection,
correction, failed audio, cached delivery receipts, proactive continuation tools,
bounded retention and forgetting. Prior inspection tests now verify that an image
continuation exposes only `describe_view`. All choices are scripted, with fake hosted
HTTP and synthetic images; no real visual model or hardware was exercised.

Remaining work: real-model multi-turn repetition/description evaluation; a measured
live current-view acquisition adapter; optional resolution/detail selection; coalescing
and caching image acquisition requests; and supported cross-session visual association.
This slice reuses cached descriptions to avoid a new inspection when the LLM chooses
historical evidence. It does not yet deduplicate separate requested inspections or
claim that descriptive quality and natural novelty judgment are validated.

## Current-camera acquisition and hosted evaluation

The current-view adapter is now connected to camera-enabled startup when scene
uploads are enabled. It shares encoded frames, permits only current-view inspection,
and avoids another image upload when identical pixels match an eligible cached
description. **289 blocked-I/O checks pass**. Real hosted multi-turn trials on saved
synthetic images now exist; they also expose a residual invented shape change.
See [current evidence, configuration and limits](CURRENT_VIEW_VALIDATION.md).
Earlier simulation-only acquisition notes above describe the preceding slices;
physical turning/post-motion capture remains simulation-only. No device was operated.

## Proactive visual description and silent retention

The same `describe_view` tool is now available in the primary proactive call when
fresh scene images are actually supplied. The conversation owner may retain a
useful uncertain description silently (`text=null`), retain it with a relevant
remark, answer normally, choose another offered tool, or stay silent. No additional
inspection, image upload, model generation, background classifier or timer is added.
This connects rich descriptive memory to arrival/lull/ambient opportunities rather
than requiring somebody to ask a visual question first.

The source retains event and prior-turn provenance, every supplied frame ID/capture
time, camera medium and no-motion status. There is no invented human utterance or
identity/biography write. A description based on two images has no single-image
reuse digest: matching only its last frame cannot validate the whole interpretation.
Single-image observations can participate in the existing exact-image inspection
reuse. Historical descriptions now enter eligible conversation context even when
no motion/inspection adapter is installed, with the existing session/audience scope.

At dispatch, at most two images must each be at most two seconds old. Before saving,
the state owner checks the offered source ID, revision, cancellation, current scene
cue validity and a 15-second maximum acquisition age. The cue's existing ten-second
expiry may reject sooner. These are conservative software deadlines, not measured
camera/model calibration. Changed scene evidence during synthesis cancels the
pending remark. A saved observation remains historical evidence; only completed
audio earns a visual remark receipt and a scene-delivery receipt. Silent retention
neither claims speech nor imposes the two-minute `remain_silent` hold. Existing
opportunity pacing still applies. No trait-specific repetition rules were added.

**301 blocked-I/O checks pass**, including 12 new production Session scenarios for
one-call save/speech, silent retention, stale/missing source rejection, changed
scene/state, cancelled generation, expired acquisition, failed/interrupted audio,
source IDs, multi-frame provenance and cache availability without motion adapters.
These use scripted choices, synthetic images/audio and temporary databases. They do
not establish spontaneous remark quality, social judgment or camera performance.
No hosted call or hardware was used for this slice. The earlier hosted false shape
change remains unresolved; see [visual validation](CURRENT_VIEW_VALIDATION.md).

Next evaluation: saved-media multi-turn proactive trials covering both welcome
contributions and restraint, corrections and repeated unchanged appearance. Live
camera/group acceptance and physical motion controllers remain separate open work.

The September 13 [hosted proactive evaluation](PROACTIVE_VISUAL_EVALUATION.md) is now
complete for six small synthetic-image trials. A scoped input-context correction
improved final restraint cases. Repetition, related-record correction, real-photo
quality and live acceptance remain open; 302 blocked-I/O tests pass.

Related-record correction is now [implemented and evaluated](VISUAL_CORRECTION.md):
the LLM can withdraw multiple explicitly selected descriptions, including older
eligible records, in one atomic operation. 312 blocked-I/O checks pass. This is
whole-record withdrawal; partial edits, repetition quality and live acceptance remain open.
