# Persistent narrative and conversation plans

## Owner direction after the fourth live run — proposed, not implemented

Status, October 1, 2026 (late): the implemented replacement described below **failed
live acceptance** in session `f4e2bf9716d3485e96a7ca6837935c43` (9.6 minutes, real
hardware). Evidence from that trace: 40 planner calls, 26 saved plans, 11 steps
started, 4 completed, 2 self-initiated spoken lines. 17 opportunities were cancelled
by human speech and 8 as `audience_changed_or_stale` (face-track renumbering while the
owner stayed present). Two trusted human requests lost their ordinary reply without a
logged reason ("How do you know that you got caught in a loop?", the first "Back up
five feet."); the planner then drafted its own answer. Most plans were silent head
turns. Every completed reply exceeded four seconds to first audio (median 4.6, max
7.3, n=10). Cause of the two dropped replies is unconfirmed; the unlogged cancel in
`session.py` (`_resumed_speech > _capture_end`) is the candidate.

The owner rejected the mechanism, not only its tuning. A plan was meant to be a goal
Rex adopts because he noticed something and then pursues across many exchanges. It was
built as a short script that is regenerated whenever anything changes, which is still
input-then-react. Nothing in this section is implemented.

### Priority order (owner requirement)

1. **Being spoken to comes first.** Rex answers people through the ordinary reply
   path. A plan never takes over, delays or re-drafts a reply to a human request.
2. **People he notices get greeted.** He greets and asks how they are doing.
3. **If nothing happens, he starts doing things.** He picks something and pursues it
   without waiting for input.

### What a plan is

- **Started by noticing, not by a quiet timer.** Triggers: a known person appears, an
  unknown person appears, something in the room catches his interest, or nothing has
  happened for a while.
- **A goal plus a few ideas toward it**, written once by the stronger model. Owner
  examples:
  - Known person with saved facts: plan a conversation, begin speaking, and keep it
    going when it dies (ask how they are, whether they have plans).
  - Unknown person: get to know them; notice what they are wearing and ask about it.
  - Something in the room: decide to investigate, move toward it, study it, ask or
    comment about it.
  - Nobody engaging: go toward something and reach out his hand; cross the room, look
    down and comment on what is there.
- **Steered by conversation, not cancelled by it.** The reply model receives the
  current goal as context on each human turn, so the one ordinary reply generation can
  also advance the goal. A human turn does not mark the plan stale.
- **Resumes after interruptions.** An explicit request ("back up five feet") is done
  immediately by the reply path; the plan then continues or is dropped by a real
  ending below.
- **Ended only for real reasons:** goal achieved, the person has actually left, a human
  says to drop it or asks for quiet, or a more interesting goal replaces it. Face-track
  renumbering, untrusted transcripts and his own motor noise end nothing.
- **Planner calls are rare.** One when a goal is adopted and one when it ends. The
  expected rate is a handful per ten minutes; the measured rate to beat is 40.

### What catches his interest (owner requirement)

Anything can. No special detector or new model is needed; the planner chooses from the
existing camera and object evidence, and almost any choice is an improvement on the
current behavior. The requirement is about how he engages with it:

- **Personable, not inventory.** He asks about the thing or the person's connection to
  it: "What's that wall painting about?", "Tell me more about this photo on the wall."
- **Never announce the obvious.** Stating that an object exists ("I see a clock") is the
  failure the owner called out. Naming what is visible is not a contribution.
- **Curiosity aimed at people.** A noticed thing is a way into a conversation about
  them, their home and their stories, not a prompt for a riff about the object.

### Motion (owner requirement)

Self-initiated driving needs no permission and is not limited to small moves. The only
limits are physical: he must not drive into things and must respect the time-of-flight
sensor boundaries. Existing obstacle, clearance, charging, manual-control and stop-word
guards remain. The 1.5 m single-request travel limit seen in the trace conflicts with
the owner's no-caps ruling and is to be removed. Mid-drive human speech that is not a
stop word should not abandon a physical plan step.

### What this retires and keeps

Retire: the two-second quiet preparation trigger, marking the plan dirty on every
human turn, replanning after every interrupted step, and audience invalidation of
steps that disclose nothing private. Keep: the single `agency_plan` store, the planner
call, the shared speech/head/arm/wheel executors and their receipts, quiet and
stop-word handling, and audience checks on personal disclosures.

### Open before coding

- Find and log the cause of the two silently dropped human replies.
- Acceptance must be a live run. The simulated room has no interruptions or track
  churn, which is why earlier synthetic passes did not predict these failures.

The sections below describe the implemented software that this direction replaces.

## October 1 replacement

The owner reported that the previous three agency increments still left Rex silent.
The initially inspected seven-minute session had 16 proactive opportunities and **zero
completed audible proactive contributions**: 12 silence decisions, two silent visual
bookkeeping attempts, and two interruptions. Two spoken promises also lost their
working-interest updates during validation. Synthetic tests had established the
old mechanism's contracts; they had not established engaging live behavior.

The owner authorized replacing the optional “should I participate?” loop with a
stronger model that writes a private character narrative and a concrete plan,
saved in memory, followed by periodic prompts to the cheaper conversation model.
This document supersedes the earlier increment plan and self-scheduling design.
Existing physical attention, visual continuity, invitation, memory and output
executors remain shared with ordinary conversation.

The next live run (`c0ecb89dbc2a40dfb1fc805fcdc1eec2`) also failed: five planning
attempts, two `GeneralAgencyPlanHasPrivateEvidence` rejections, three speech
interruptions, zero saved plans and zero executed steps. Valid camera/target refs
were classified as private and rejected rather than scoped. No correction feedback
reached the planner. Retry delays then postponed all activity. An unrelated optional
working-interest validation error also erased a human reply. The repair below
addresses these observed failures; earlier synthetic success was insufficient.

## Implemented architecture

One Session remains the conversational owner. At an eligible lull it:

1. Loads the saved plan and constructs evidence from recent trusted conversation,
   offered observations, visual memory and current targets. It includes previous
   steps, actual heard words, action receipts, unresolved questions and boundaries.
2. Calls the configured stronger planner when there is no usable plan, after a human
   turn, after exhaustion/failure, or when audience scope changes. The planner writes
   a private first-person character perspective, an objective and one to four
   concrete steps; normally one to three keep the next action close to the exchange. Imagined
   attitudes and hypotheticals are allowed; invented observations or human facts
   are not. Factual references must be among the offered IDs.
3. Validates capabilities and references and saves the plan in the existing SQLite
   memory store's `agency_plan` table. These are fictional character/activity notes,
   never personal claims or hidden reasoning. Offered audience references narrow a
   general proposal to the current audience instead of rejecting it. Invented refs
   still fail. One bounded correction call receives the rejected proposal and error.
   A provider failure or unsuccessful correction installs an explicit
   `recovery` step: a silent attention shift or temporary pose when available, otherwise
   a brief context-appropriate contribution. Failed premises do not force jokes.
   There is only one active plan and no silent provider substitution.
4. Sends the current step and fresh context to GPT-6 Luna through the normal reply
   path. Tools implement a contribution, inspection, directional look, speech with a
   gesture, temporary named/joint pose, or bounded drive. Physical-only plans are valid;
   movement can be the first step. A changed premise or boundary permits an explicit
   `revise_agency_plan` result. The failed step and reason are retained; the same
   opportunity executes a recovery step with one bounded Luna call. The replacement
   offers no further revision. Human input,
   quiet, privacy and cancellation still govern the output owner.
5. Records actual delivery and executor results. Completed audible text advances a
   contribution. Inspection also needs a completed acquisition with an image; looks
   need an executor receipt; gestures need completed speech and a gesture receipt.
   Drive steps need completed drive receipts. Poses need successful pulses on changed
   channels; commanding an already-reached position does not count as physical agency.
   Failure or interruption requests revision. A spoken promise cannot count as a
   physical action. Controller pulses remain distinct from measured physical arrival.

Ordinary human replies still use one primary Luna generation, with existing bounded
tool-result continuations when needed. They never wait for a new planning call.
The performer no longer receives instructions to author parallel working-interest
plans. Rejected optional reply notes recover via an ordinary result continuation;
the rejected draft/promise is not spoken and no rejected write is acknowledged.
An in-flight private planning call yields to human speech through the existing
cancellation path. No separate speaking loop, resident model or service was added.

## Timing and perception

Private preparation starts after two quiet seconds through the same Session. It
does not consume an arrival, advance memory offers, change speech history or reset
the interaction clock. Speech still uses the configured eight-second default lull;
planning latency overlaps this wait instead of starting after it. Step delays are
3–30 seconds, normally 5–10, with a five-second execution pacing minimum and the
configured lull floor still applying. These are eligibility times; model generation,
inspection and TTS can add latency. These are not first-audio guarantees.

No visible person or new scene is required. News, weather and camera evidence are
coalesced into the next step context rather than spending one attempt per update.
Conversation, ordinary curiosity and physical presence sustain plans; music is flavor
when relevant, not the default topic. Human feedback changes the next activity.
The one-seed policy, model-created general timers, silent-decision backoff and
12-attempts-per-five-minutes cutoff are retired. Planner provider/validation failures
use bounded 10–60-second planner retries; these no longer block character-step
execution. A generated step can continue while the stronger planner is unavailable.

Head movement does not itself establish novelty. Existing settling, returning-view
history and delivered-description evidence remain in use. Plans retain their actual
outcomes, and the planner sees recent prior-plan outcomes for continuity. A visible
person is not an identity assertion. Explicitly general plans survive changed visibility without restarting their steps.
Personal/face-target evidence narrows a proposed general plan to its audience;
public observations, camera acquisition metadata and unnamed object targets can remain
general. Private planning retains recent dialogue despite face-track instability;
camera receipts cannot crowd out feedback. This context does not authorize disclosing
personal facts to a changed audience. Audience-scoped notes are withheld when scope
differs. Preparation/execution use the same audience snapshot; general activity survives
camera churn, while personal output revalidates before delivery. Room-wide object
identity and semantic non-repetition remain live evaluation questions.

Explicit quiet, human-floor state, queued input, fresh microphone requirements and
shutdown still gate activity. Pending identity questions and unanswered social
invitations do not freeze unrelated plan steps. Invitations retain their own
explicit heard/open/answered/declined/lapsed lifecycle; time never invents an answer.
`next_opportunity` is retained only as validated invitation review metadata and
cannot schedule general work.

## Companion and body repair after the next live run

Session `299c989c202a4f9ca815b6bf9dfed331` spoke proactively but failed socially:
repeated musical furniture jokes, discarded feedback, zero proactive gestures/base
moves and one late head look. The owner remained in view. The camera detected a
face in 768/793 analyzed frames but issued 175 track IDs; 25 face boxes passed the
48-pixel width threshold but failed the old redundant area threshold. Social audience
eligibility now uses settled presence and width, without changing identity or learning
thresholds. The same audience survives a planning-time polling gap; private output
still cancels for genuinely changed tracks/membership before delivery.

Terra plans from dialogue before inventing a topic, ends rejected bits, and can lead
with physical action. It sees current named poses, joint positions, held parts and
base clearance/capabilities. Existing self-initiated drive limits and charging,
manual-control, obstacle and stop guards remain. Autonomous poses neither acquire
persistent holds nor release/override human holds. Autonomous drive refuses while a
human pose hold is active, before preparing the head/arms. Ordinary public questions
do not require a visible listener; disclosing personal facts remains audience-bound.
Private planning permits existing
idle motion; capture and output retain their motion exclusions. A no-change head
command does not invalidate camera continuity. Self-requested inspection can retain
useful observations silently instead of announcing inventory. Unanswered name
questions are not re-asked on the next human turn.

## Models and persistence

`agency_planner_model` defaults to **gpt-5.6-terra**, with medium reasoning. The owner
selected this model after the account's read-only lookup exposed GPT-6 Sol, GPT-6 Astra and GPT-5.6 Terra; the requested
`gpt-6-terra` returned model_not_found. No paid generation was used for that check.
The model setting is explicit and there is no automatic fallback. Replies and step
execution retain **gpt-6-luna**.

The plan survives normal shutdown. A restart must revalidate it with current evidence
before execution; saved motor commands are never replayed. Forgetting, source
corrections, explicit stop and applicable participant boundaries clear derived plan
notes. Human turns mark the plan for revision after the immediate reply. Plan storage
uses the existing ignored scratch database, with no copy or connection to V2 data.

## Evidence and remaining acceptance

`tests/test_agency_plan.py` exercises the production Session, runtime, temporary
SQLite store and output owner with fake providers, clocks, images and audio. It covers
continued steps without human input, cheaper immediate replies, restart, interruption,
quiet/floor/freshness, invalid evidence, provider failure, revision reasons, physical
receipts, failed acquisitions and reports. Older scheduler-specific tests have been
retired or rewritten around the new behavior. The final suite result is recorded in
[implementation status](IMPLEMENTATION_STATUS.md).

Trace reports separate planning calls, execution calls, planner duration, actual step
outcomes and available token usage. Missing costs, latency, memory or physical evidence
remain unknown. `tools/check_agency.py --live` now exercises the production scheduler,
Session, actual Terra/Luna providers, temporary memory and synthetic output in an
unchanged empty room, ordinary companionship, rejected humor with camera input,
and embodied curiosity. Servo and base owners use injected fake transports. It uses
no hardware. Results and limitations are recorded in [implementation status](IMPLEMENTATION_STATUS.md).

The owner will run the next live test. Check that Rex develops an idea across several
lulls, changes course after speech, varies thoughts without repeatedly questioning
people, uses occasional bounded actions and respects quiet. Compare delivered output
against the zero-contribution baseline. Planner quality, live latency/cost, physical
expression and whether Rex feels alive remain **unaccepted until that test**.

## Increment 2 implementation — October 1, 2026

Retained physical foundation: bounded directional attention and gestures share the
existing servo/output owner. Settling and returning-view evidence prevent a head turn
from being treated automatically as novel sight. Controller receipts do not measure
shaft position. The new planner delegates steps to these same executors.

## Increment 3 implementation — October 1, 2026

Retained invitation foundation: only complete audible invitations become pending;
explicit transitions record answered, declined, superseded or lapsed states. The
stronger planner and step owner replace the former optional self-scheduling behavior.
