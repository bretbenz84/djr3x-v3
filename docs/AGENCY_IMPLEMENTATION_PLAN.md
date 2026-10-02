# Making Rex feel alive

October 1, 2026. **All three increments implemented in software; semantic/live acceptance remains open.**
The user authorized the first increment and explicitly rejected a visible-person
requirement and fixed 20/60-second unsolicited-speech waits. Those gates are removed.
The user subsequently authorized increments 2 and 3 without further interaction. This document does not authorize paid evaluations,
deployment or hardware operation. See the implementation evidence below.

## Intended experience

Rex should have interests, attention and activities that continue when nobody is
giving him commands. He should sometimes contribute a thought, pursue a curiosity,
make a small social bid, or participate silently. He should also comfortably let
an idea go. More frequent speech or random movement alone is not success.

The camera is attached to the moving head. Turning toward an object may provide a
welcome opening, but turning away and back must not restart the same question,
joke or description. Newly visible is not necessarily newly present or interesting.
Agency must also work in a familiar, unchanged room.

An "internal monologue" here means short, revisable working notes about interests
and possible next actions. It does not require continuous prose narration, a second
conversation model, or claims of subjective experience. The same conversational
owner maintains these notes and chooses replies, initiative, actions and silence.

Examples are behavioral targets, not fixed scripts:

- After a conversation about a failed repair, Rex later offers a specific playful
  theory about it, without repeating his answer or demanding a response.
- An unfamiliar object catches his attention. He looks, leaves the human time to
  finish speaking, and asks once if the curiosity still matters.
- A quiet visitor watches Rex. He offers a characterful opinion or brief
  demonstration, rather than repeatedly asking the visitor questions.
- Nobody answers an invitation. Rex leaves room, eventually lets it go without
  interpreting rejection, and can attend to something else.
- Rex turns back toward an already discussed object and says nothing about it
  unless something meaningful changed or the conversation supplies a new reason.

## Foundation and gaps at proposal time

This assessment comes from documentation and source inspection, not a new live
measurement. Historical documents contain superseded milestones; current code and
dated evidence take precedence over old completion claims.

| Existing foundation | Gap addressed by this proposal |
| --- | --- |
| One conversational owner, shared Session, validated tools and actual output receipts | Preserve these owners while enabling sustained self-directed behavior. |
| One session conversation intention with optional openings | Extend its continuity beyond a visit objective to bounded unresolved interests and activities. |
| Arrival/lull/ambient opportunities and attempt pacing | Permit model-selected reconsideration and separate consideration, silent action and speech eligibility. |
| 20-second post-reply/greeting and 60-second post-proactive audible floors | These currently gate proactive consideration as well as speech. Removed by the October 1 first increment at the user’s request. |
| Six proactive attempts per five minutes and automatic backoff | Measure actual cost and replace automatic scheduling coherently; silent attempts must not create busy polling. |
| Scene images, object detection, visual descriptions and photo memory | Preserve what was explored across changing views; a changed image is not a semantic novelty decision. |
| Idle motion, explicit poses, bounded self-initiated base motion and simulated attention contracts | Wire a small set of purposeful physical attention and expression choices through existing executors. |
| Strong unanswered-question restraint | Add a graceful invitation lifecycle so waiting need not become indefinite inactivity. |

Relevant foundations: [proactive policy](PROACTIVE_CONVERSATION.md),
[conversation intentions](CONVERSATIONAL_INTENT_PLAN.md),
[scheduling proposal](NEXT_CONVERSATION_SCHEDULING.md),
[attention contracts](ATTENTION_IMPLEMENTATION.md),
[servo integration](SERVO_INTEGRATION.md), and
[current implementation status](IMPLEMENTATION_STATUS.md).

## Architectural boundaries

- Extend the existing conversation owner and state; no separate proactive brain,
  periodic chatter service, new resident model or mandatory planner call.
- Ordinary replies retain one primary generation and their streaming path. Optional
  state updates occur in that generation. Inspection-result continuations remain
  explicit, bounded and measured.
- Semantic choices belong to the model. Code validates sources, revisions,
  permissions, budgets, lifecycle and executor constraints.
- Retire replaced scheduling/state behavior rather than maintaining parallel modes.
- Personal memory, speaker identity and visible presence remain separate. Working
  interests do not grant learning permission or create accepted personal facts.
- Preserve source-linked correction, forgetting, audience discretion and actual
  delivery receipts. No mouth-motion evidence enters attribution or enrollment.
- Begin with existing perception and motion capabilities. Broader roaming, mapping,
  new feeds, games and additional perception models are outside this delivery.

## Increment 1: working interests and reconsideration

### 1. Explain inactivity before changing it

Add bounded diagnostics for why Rex is inactive: human floor, pending invitation,
speech cooldown, audience unavailable, attempt budget, no scheduled consideration,
model-selected silence, unavailable motion, or executor contention. Record changes
in gates rather than emitting a trace every idle tick.

Connect evidence, consideration, selection and actual output through existing IDs.
Use repeatable scenarios for a quiet visitor, unchanged room, post-conversation
pause, unanswered question and head turn away/back. Establish model-call, latency
and movement baselines. Diagnose literal prolonged motionlessness separately from
whether movement communicates intention.

**Exit criterion:** a trace explains whether Rex chose to wait, was prevented from
considering anything, or attempted something that never completed.

### 2. Extend the existing intention record

Evolve `conversation_intent.py` under CoreState ownership. Keep one current focus
and initially at most three unresolved interests; these are proposed engineering
bounds to evaluate, not new user settings or calibrated social limits.

Each interest needs a stable ID/revision, brief subject, evidence references,
remaining curiosity or possible contribution, audience scope, lifecycle status,
and links to attempts/results. Distinguish an observation, a human statement, an
eligible memory and a character-authored hypothetical. An invented idea must never
masquerade as something observed or remembered.

The model may create, revise, resolve or abandon an interest during an existing
decision, including a silent decision. Most replies need no update. Keep bounded
closed-interest context so abandoning something does not immediately regenerate it.
Reuse existing delivery and feedback records rather than duplicating them.

Working notes remain session-only. Restart clears them; durable follow-ups continue
through existing memory tools. Source correction/forgetting invalidates dependent
notes. Audience changes withhold inappropriate content; stale proposals cannot
restore an old interest. Silent edits cannot indefinitely extend stale activity.

**Exit criterion:** an interest survives a pause or interruption, can develop from
new evidence, and yields to redirection without becoming an interview checklist.

### 3. Let the owner request its next consideration

Extend the existing `next_opportunity` proposal to cover silent decisions and
supported event conditions. Keep one bounded versioned pending reconsideration;
multiple interests do not create independent timers or competing queues.

Separate eligibility to **consider**, **act silently**, and **speak**. A speech
cooldown should not automatically prohibit reviewing evidence or an appropriate
silent attention shift. Explicit quiet, human-floor boundaries and requests to
stop bothering someone still constrain activity; silent motion is not a loophole.

Proposed scheduling semantics:

- After-speech delays start only after successful completed delivery. Failed,
  cancelled or partial speech does not arm the proposed follow-up.
- A silent decision may explicitly request a bounded later consideration from its
  committed completion time. Silence without such a request does not imply a timer.
- An action-dependent request wakes on its correlated result. Fresh inspection
  evidence requires successful acquisition, not merely a command being issued.
  Failures are outcomes to handle, never fabricated observations or automatic retries.
- Waiting for input cancels the pending timer. New human input supersedes older
  autonomous work. Due requests always reconsider with fresh context.
- Shutdown, expiry, invalid audience/source, abandoned interests and relevant quiet
  boundaries cancel or invalidate pending work. Reject duplicate/stale rearming.

Reuse the idle worker. Coalesce events, retain a measured call budget, and increase
waits after repeated empty considerations. No fixed-frequency background generation.
Choose delay bounds, event-expiry behavior and spending limits during this slice;
measure them before claiming improved cadence. Replace the old automatic backoff
when the new scheduler lands, and remove the rejected fixed 20/60-second audible floors.

**Exit criterion:** Rex can consider and act silently while leaving conversational
space, without excess calls or delaying direct replies.

## Increment 2: purposeful physical attention without repetition

### 4. Connect attention to existing hardware ownership

Add a physical adapter for the existing attention contract using the servo/output
owners. Start with calibrated directional looks and a small set of brief gestures.
Offer object-targeted gaze only when available geometry supports it; screen
position alone must not imply a calibrated head/base bearing.

Chosen attention temporarily preempts random idle motion, then releases control.
Coordinate with talking motion, voice reflexes, explicit held poses, microphone
holds, drive stance, stop and shutdown. An autonomous gesture must not silently
undo a human-requested pose. No competing servo writer is introduced.

Distinguish command/readback completion, settling and fresh perception. A controller
receipt is not proof of mechanically measured arrival or successful sight. Every
activity needs a finite end, cancellation, and an outcome returned to its interest.

### 5. Preserve context through a moving camera

Maintain distinct information about what is currently visible and what Rex has
already noticed, investigated or discussed. Extend existing visual-memory and
spoken-remark context rather than adding an independent room model.

- Attach head/base motion and settling evidence to observation timing. Unknown
  motion cause stays unknown; manual movement need not have a command receipt.
- Mark reframed views as newly visible, without asserting a new arrival.
- Preserve discussion and interests when a subject leaves the frame. A new frame,
  scene hash or object-track ID does not reset them.
- Supply recent descriptions, uncertainty, actual heard remarks and relevant photo
  references for model-owned continuity judgment. Do not merge objects solely by
  category/name; two similar objects may remain unresolved.
- Let meaningful change, an unresolved question or a human reference justify a
  revisit. Reappearance alone normally supplies no reason to speak.
- Keep purely visual exploration possible without requiring a visible face, but
  use explicit audience evidence for addressing people or surfacing personal memory.
  This changes current opportunity eligibility and requires dedicated tests.

**Exit criteria:** turning away and back does not repeat a question or restart a
joke; an actual change can still be noticed. Rex can look, inspect, decide nothing
more is needed, and return attention without forced commentary.

## Increment 3: varied initiative and acceptance

### 6. Develop behavior choices and invitation lifecycle

Revise the shared prompt and compatible tool schemas around these choices:

| Choice | Desired behavior |
| --- | --- |
| Continue an interest | Advance an unresolved aspect rather than repeat the previous answer. |
| Offer a characterful thought | Give a grounded opinion, playful hypothesis or specific joke that need not demand an answer. |
| Make a social bid | Offer one question, challenge or brief demonstration with room to decline or ignore it. |
| Investigate | Look or inspect for a particular reason, then resolve or revise that curiosity. |
| Participate silently | Make an appropriate glance/gesture or remain attentive without taking the floor. |
| Let something go | Close an unanswered invitation without announcing rejection or interrogating the silence. |

An invitation is initially waiting, then can be answered, declined, superseded or
allowed to lapse. Elapsed time permits reconsideration; it does not infer rejection,
disinterest or an answer. Use the model's explicit validated transition to relinquish
an invitation, retaining that it was asked. Do not use invitation expiry to override
an unfinished human thought, a human-to-human question or an explicit boundary.

Keep character tastes consistent in the persona. Personalization uses supported
memories, not inferred preferences from passing mentions. Small activities initially
use existing head/arm/light/inspection capabilities and end naturally. No requirement
to speak, move or be funny on every opportunity.

**Exit criterion:** in an unchanged room Rex varies his contributions, shows
continuity, and can move on without repeated questions or generic filler.

### 7. Verify contracts, semantic behavior and live experience separately

Use the production Session/runtime with fake clocks, temporary stores, simulated
motion and blocked network/audio/serial for structural checks. Cover stale/duplicate
updates, restart, correction/forgetting, audience changes, quiet, human priority,
interrupted activities, failed output and cancellation before/after execution.

Include these behavioral sequences:

| Sequence | Evidence sought |
| --- | --- |
| Greeting, then five minutes with a quiet receptive visitor | Welcome initiative, purposeful quiet behavior, no repeated demand for answers. |
| Familiar unchanged room | Conversation/character can supply an interest without manufactured visual novelty. |
| Head turns away and back; object tracks change | Prior discussion survives; no repeated question or remark. |
| New object, two similar objects, object actually changes | Useful noticing without false identity or blanket suppression. |
| Invitation unanswered, then a new opening | Room to answer followed by graceful release, without inferring rejection. |
| Human interrupts a chosen activity or changes topic | Human priority; abandoned work does not automatically resume. |
| Serious disclosure, quiet request, or humans talking together | Sensitive restraint without converting all later participation into permanent silence. |
| Inspection/motion fails or audience changes | Honest outcomes and no stale action, private disclosure or false visual claim. |

Then run separately authorized, paced hosted comparisons with fictional conversations
and controlled evidence. Repeat baseline/candidate sequences and review complete
dialogues, silent choices and actions. An LLM's rating alone cannot establish that
Rex feels alive.

Finally arrange explicit live robot sessions. Measure both missed welcome openings
and unwanted interruptions; repetition; question load; purposeful silent actions;
interest progression; and observer ratings of attentiveness, curiosity, warmth and
comfort. Report denominators and unresolved failures. Record model calls/tokens,
cost, median/tail latency, actual audio timing, combined resource use and playback/
capture issues. Synthetic motion does not establish physical performance, and
synthetic voices do not establish attribution accuracy.

Set quantitative release thresholds from the baseline and user-reviewed examples
before the final comparison. Success requires useful initiative as well as restraint;
neither permanent silence nor greater speech/movement volume passes by itself.

## Implementation map and delivery evidence

| Area | Likely touch points |
| --- | --- |
| Interests, lifecycle and authority | `src/r3x/conversation_intent.py`, `state.py`, `session.py` |
| Consideration, budgets and diagnostics | `runtime.py`, `proactive.py`, existing trace/report paths |
| Model proposals and character | `semantics.py`, `adapters/hosted.py` |
| Physical attention and arbitration | `attention.py`, `output.py`, `robot_servos.py`, `idle_motion.py`, existing servo executors |
| Moving-view context and repetition | `scene.py`, `visual_memory.py`, existing object/photo evidence |
| Verification | Production-path tests and the existing `tools/run_checks.py` runner; separately scoped hosted/live evaluation |

For each increment, record implemented contracts, retired behavior, measured results
and remaining limitations in this document and `IMPLEMENTATION_STATUS.md`. Update
the proactive, intention and scheduling documents where behavior changes; preserve
their dated historical evidence. The first-increment evidence below supersedes the historical timing and
visible-audience requirements; increment 2 and 3 evidence follows it. Semantic/live acceptance remains separate.

No V2 extraction, new dependency, personal-data import or hardware operation is
needed to write or review this plan. The original documentation request performed no implementation. The subsequent
authorization and first-increment delivery are recorded below.


## Increment 1 implementation — October 1, 2026

Implemented through the existing Session, CoreState, Initiative and output owners:

- A startup opportunity and a first lull after trusted human input can occur without
  a camera or visible person. Scene/feed opportunities also need no face. No face
  means missing audience evidence, not proof the room is empty or someone is listening.
  Targeted questions and private memory retain their evidence/access checks.
- Configured public news/weather sources no longer depend on enabling the camera.
  Configuration/imports remain inert; providers start and close with the live runner.
- Removed the fixed 20-second and 60-second post-speech floors and the automatic
  8/15/30/60 continuing-lull backoff. Plain replies still stream. The existing
  `proactive_lull_seconds` controls one seed after a human exchange (default eight);
  it does not override an explicit model-selected reconsideration.
- Nullable `next_opportunity` proposals on compatible tools select `after_speech`,
  `after_decision`, or `wait_for_input`, with source/revision validation. There is
  one pending session request. Delays are bounded to 3–300 seconds; speech requests
  arm only after complete audible delivery. Silent decisions and successful
  inspection/attention tools can request reconsideration after their turn completes.
  Failed acquisition, failed/partial output, cancellation and stale requests do not
  arm a timer. Inspection uses its existing result continuation, not a new planner.
- New human speech supersedes pending work. Quiet, human-floor state, stop, frozen
  evaluation and input health retain their guards. Pending targeted questions still
  leave room for an answer; the broader invitation lifecycle belongs to increment 3.
- One startup seed and one per-human-source lull are available without an explicit
  request; further timer-based considerations require the model to schedule them.
  `wait_for_input` suppresses seed lulls and ambient cues until input; a fresh arrival
  or radar event may still offer genuinely new evidence. A request expires five
  minutes after becoming due. The bounded existing unheard camera-cancellation retry
  remains a single recovery attempt, not an automatic ongoing conversation loop.
- Resource bounds: at least three seconds between attempts, at most twelve attempts
  per rolling five minutes (including unheard attempts), unchanged by human replies.
  Consecutive silent decisions without a changed working state back off their
  requested delay through 3/6/12/24/48/96/180 seconds. These are resource bounds,
  not a promise of speech or measured socially optimal timing; no new setting added.
- The existing intention record now includes up to three interests and three recent
  closed interests, with state-owned IDs, source evidence, status and delivery links.
  The model updates them in the same generation. `interests=null` retains the set;
  an array replaces it and closes omitted items. Closed context is pruned first to
  keep the model-facing record bounded to 6,000 serialized characters.
- Explicit general scope is limited to impersonal ideas/observations, without person
  targets or memory references. Audience-scoped notes are withheld on audience
  changes. A missing visual audience hides private notes without cancelling a
  fresh generic reconsideration. Correcting/forgetting their source clears dependent state and scheduling;
  restart clears working notes and the timer. Existing fifteen-minute activity expiry
  remains; silent edits do not extend it. Semantic discretion is still required for
  free text and historical conversation, not guaranteed by structural validation.
- `initiative_gate` records changes in inactivity reason. Scheduling traces identify
  arming, waiting, cancellation, rejection and failed arming; the existing `report`
  command includes gate transitions and scheduling outcomes. Existing call, token,
  latency, resource and delivery traces remain the measurement path.

### Evidence and remaining limits

First-increment tests use production Session/runtime paths with fake clocks,
synthetic speech/output, temporary stores, fake camera evidence and simulated
inspection. They cover camera-free startup/lulls, short delivery-based scheduling,
silent backoff, wait/input cancellation, stale/duplicate/expired proposals, failed
output/inspection, general versus private scope, interest revision/closure, restart,
source invalidation and report diagnostics. Tests specific to retired automatic
backoff and fixed audible floors were replaced by the new scheduling contracts.

**817 blocked-I/O checks pass** in 66.1 seconds using the v3 environment and
`tools/run_checks.py`; the consolidated result is in IMPLEMENTATION_STATUS.md.
No hosted model, real acoustic model, live feed, microphone, camera, servo or wheels
were operated for this change. Existing source inspection establishes the removed
policy limits; no new live before/after cadence measurement is claimed. Actual model
selection frequency, social quality, billed cost and combined M2 load remain unmeasured.
No stronger claim follows from synthetic success.

At this first checkpoint physical attention and moving-view continuity were still
pending. The second-increment implementation below supersedes that limitation;
the third-increment evidence below covers invitation/character work.


## Increment 2 implementation — October 1, 2026

- Successful configured robot startup now explicitly starts physical attention and
  gesture adapters on the existing output/servo owners. No new setting, provider,
  model, thread or transport is introduced. Simulation adapters remain explicitly
  injected test capabilities and retain simulation receipts.
- Silent attention offers robot-relative left/right/up/forward/unfocused looks.
  Finite nod, tilt, lift and elbow emphasis run before or after completed speech,
  return their joint to its starting position, and target only `unfocused` (no
  promise of directed gaze). Directions use existing calibrated ranges; object/face
  targets remain simulation-only because this adapter has no verified geometry.
- Chosen motion stops idle/reflex/talking workers before requesting the same servo
  owner. An intersecting explicit held pose or drive stance rejects the whole
  expression. No microphone-arm or throttle channels move. Attention has a
  15-second execution deadline and 0.5/1.5-second dwell; these are finite executor
  bounds, not proactive speech delays. After dwell the owner releases without an
  automatic recenter; normal idle scheduling can resume. Gestures retain their
  existing 30-second request expiry. Cancellation holds reached pulses and does
  not automatically resume abandoned motion.
- Attention and gesture history distinguish `controller/pulses_reached` from
  `simulation/simulated`, separately from heard speech. Pulse readback proves
  controller output only, not shaft position, obstruction clearance or sight.
  Silent attention can request later reconsideration/inspection without speaking.
  Its own camera reframing does not cancel a committed silent look; human input,
  queued capture, shutdown and explicit stop still do.
  A separate gesture token preserves human priority even after speech has become
  audible: resumed human input cancels physical expression without erasing heard audio.
- Every servo sequence commanding head channels now signals camera motion, including
  startup, idle, reflex, talking, explicit poses and chosen expressions. The old
  idle/reflex pre-request callbacks are retired. Base motion joins
  this evidence. Scene images are withheld during motion and for 0.5 seconds after
  head commands / 1 second after base commands. Deliberate high-detail inspection
  also waits for an eligible frame and rechecks it. Settling is a timing guard,
  not measured mechanical feedback. Uncommanded movement remains unknown.
- The scene observer retains at most 32 compact view signatures and 32 considered
  view IDs within the session. Similar returning views reuse their ID and do not
  supply another ambient scene cue; differing pixels can still offer one under
  existing pacing. Deliberate inspection can revisit a considered view. Images
  from distinct views are not paired as evidence of a gesture. Similarity is the
  existing coarse image threshold, not object tracking or semantic identity; small
  changes can be missed, and changed lighting/viewpoint can evade matching.
- `describe_view.description_scope=general` explicitly retains impersonal room/object
  interpretations across changing face visibility. Audience/null remains the
  default. Source question text is omitted from general records. Original general
  descriptions' actually delivered remarks can accompany them; later contextual
  replies stay audience-filtered while a count preserves that a remark happened.
  Corrections retain source IDs without exposing the original correction transcript
  across audiences. Existing correction, forgetting, 32-record storage and eight-
  record recall bounds remain. Restart does not recall prior-session visual notes.
  Choosing safe general free text remains a semantic obligation, not a classifier
  guarantee; person details, ownership, quotations and private context must stay scoped.
- The primary prompt compares camera continuity, descriptions, heard remarks and
  working interests. A new frame, track ID or head turn alone does not reset a
  question or joke. Actual change, a human reference or unresolved curiosity can
  justify inspection; an inspection may finish with a silent description.

**833 blocked-I/O checks pass in 66.1 seconds**, including 16 new increment-2 checks.
Verification uses synthetic pulse transport, frames/audio, fake clocks and temporary
stores through production Session/output/servo paths. It covers finite completion,
held-pose rejection, before/after speech receipts, stalls, expired/uncalibrated targets,
cancellation, turn-away/back suppression, distinct-view eligibility, deliberate
settled capture, general versus private history, and bounded storage. See the
[consolidated test result](IMPLEMENTATION_STATUS.md). No hardware, live camera,
hosted model, new dependency, V2 extraction or personal-data access was used.

Live expressiveness, clearance, settling duration, microphone effects, semantic
non-repetition and combined resource/latency measurements remain unverified.
This implements bounded view continuity, not reliable room-wide object identity.
The third increment below implements invitation lifecycle and broader character guidance.


## Increment 3 implementation — October 1, 2026

Implemented under the user's authorization to finish the remaining software without
further interaction:

- The shared prompt offers continuing an interest, a specific characterful thought,
  one social invitation, purposeful inspection/expression, silence and relinquishing
  an idea. It favors contributions that need no answer after a recent question,
  preserves persona tastes, and forbids fabricated observation/memory or interpreting
  silence as rejection. These are model choices, not automatic rotating routines.
- Optional nullable `invitation_update` metadata on compatible existing reply/silence/
  expression tools carries source, revision, invitation ID, action, topic and scope.
  Ordinary streamed replies remain unchanged and need no annotation or planner call.
- One session invitation may be waiting, with six recent closed records. An explicit
  open supersedes the previous invitation only after full audible delivery. It requires
  an `after_speech` next-opportunity proposal using the existing single scheduler and
  its 3–300-second model-selected delay. No new timer queue, service or setting exists.
  Failed, cancelled, partial or text-only output cannot create a waiting invitation.
- Before its selected review time, an applicable invitation defers unsolicited
  opportunities. At review it remains waiting until a validated semantic transition:
  answered, declined or superseded requires a trusted human source; lapsed means Rex
  lets it go, only after review becomes eligible. Time itself changes no status.
  Closing can share a useful reply, a silent decision or a new scheduled consideration.
  A silent decision without scheduling produces no repeating review loop.
  A validated closure commits before any optional acknowledgment: playback failure
  cannot erase an explicit human decline. Only opening depends on heard delivery.
- The exact heard invitation and its terminal status remain bounded context so a new
  view or later opening does not erase that it was already asked. General scope is
  impersonal only; audience scope is withheld when audience evidence changes. Known
  private-memory/target references cannot be relabeled general. Safe free-text scope
  and non-repetition still depend on semantic judgment.
- New human speech cancels the pending timer without fabricating an answer. Explicit
  stop, quiet or topic/floor boundaries interrupt pending invitation work. Source
  corrections/forgetting and restart erase dependent invitation state. Existing
  validated invitation feedback resolves a matching tracked invitation through the
  same owner. Human-to-human floor state and identity/name clarifications retain
  their separate evidence rules and are never released by lapsing a social invitation.
  An explicit scheduled review may now reconsider general participation during the
  otherwise 120-second pending-clarification guard, without consuming the pending
  question or creating any identity evidence. No timer is added automatically.
- `invitation_transition`, rejection, invalidation and failed-commit diagnostics join
  existing gate/action/speech traces. Session reports summarize invitation outcomes.
  They describe execution, not measured warmth, humor or emotional understanding.

Verification: **851 blocked-I/O checks pass in 70.4 seconds**, including 18 new checks;
see [the consolidated result](IMPLEMENTATION_STATUS.md). New checks
exercise production Session/runtime with synthetic decisions/audio, fake clocks and
scratch stores: heard versus failed delivery, explicit versus inferred outcomes,
short review/lapse, source/revision rejection, interruption, audience isolation,
quiet/floor priority, bounded records, source invalidation and restart. A scripted
five-minute unchanged-room sequence progresses through invitation, lapse, a working
interest and silence in four primary calls. This proves orchestration, not that a
real model will make those choices or that four calls is a target cadence.

[Acceptance scenarios and scorecard](AGENCY_ACCEPTANCE.md) provide controlled cases,
denominators and required measurements for separately authorized hosted/live comparison.
No paid provider, real acoustic model, hardware or private dataset was used. Live
expressiveness, semantic non-repetition, useful-opening/interrupt tradeoffs, costs,
combined M2 load and social release thresholds remain unmeasured; they are not marked
complete merely because the software checks pass.
