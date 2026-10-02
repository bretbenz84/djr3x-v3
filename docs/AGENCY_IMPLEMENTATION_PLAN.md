# Making Rex feel alive

October 1, 2026. **Implementation proposal; not implemented.** The user requested
analysis, an implementation plan, and this document. This document does not
authorize coding, paid evaluations, deployment, or hardware operation. Existing
runtime behavior remains unchanged.

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

## Existing foundation and gaps

This assessment comes from documentation and source inspection, not a new live
measurement. Historical documents contain superseded milestones; current code and
dated evidence take precedence over old completion claims.

| Existing foundation | Gap addressed by this proposal |
| --- | --- |
| One conversational owner, shared Session, validated tools and actual output receipts | Preserve these owners while enabling sustained self-directed behavior. |
| One session conversation intention with optional openings | Extend its continuity beyond a visit objective to bounded unresolved interests and activities. |
| Arrival/lull/ambient opportunities and attempt pacing | Permit model-selected reconsideration and separate consideration, silent action and speech eligibility. |
| 20-second post-reply/greeting and 60-second post-proactive audible floors | These currently gate proactive consideration as well as speech. Revisit their scope and cadence with evidence. |
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
when the new scheduler lands, and document the remaining audible floors explicitly.

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
their dated historical evidence. Until then, this proposal does not supersede their
description of current runtime behavior.

No V2 extraction, new dependency, personal-data import or hardware operation is
needed to write or review this plan. No implementation or evaluation of the proposed
changes has been performed as part of this documentation request.
