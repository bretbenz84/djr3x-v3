# Persistent narrative and conversation plans

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
   concrete steps; normally three or four amortize the planning call. Imagined
   attitudes and hypotheticals are allowed; invented observations or human facts
   are not. Factual references must be among the offered IDs.
3. Validates capabilities and references and saves the plan in the existing SQLite
   memory store's `agency_plan` table. These are fictional character/activity notes,
   never personal claims or hidden reasoning. Offered audience references narrow a
   general proposal to the current audience instead of rejecting it. Invented refs
   still fail. One bounded correction call receives the rejected proposal and error.
   A provider failure or unsuccessful correction installs an explicit
   `degraded_character` step: Luna authors a fresh impersonal thought with no factual
   premise. There is only one active plan and no silent provider substitution.
4. Sends the current step and fresh context to GPT-6 Luna through the normal reply
   path. Its offered tools implement a contribution, inspection, directional look
   or speech with a gesture. Generic silence voting and wheel movement are absent
   from this step surface. A changed premise or boundary permits an explicit
   `revise_agency_plan` result. The failed step and reason are retained; the same
   opportunity executes a replacement character contribution with one bounded Luna
   call. The replacement offers neither silence nor another revision. Human input,
   quiet, privacy and cancellation still govern the output owner.
5. Records actual delivery and executor results. Completed audible text advances a
   contribution. Inspection also needs a completed acquisition with an image; looks
   need an executor receipt; gestures need completed speech and a gesture receipt.
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
Familiar surroundings and Rex's musical/droid perspective can sustain a plan.
The one-seed policy, model-created general timers, silent-decision backoff and
12-attempts-per-five-minutes cutoff are retired. Planner provider/validation failures
use bounded 10–60-second planner retries; these no longer block character-step
execution. A generated step can continue while the stronger planner is unavailable.

Head movement does not itself establish novelty. Existing settling, returning-view
history and delivered-description evidence remain in use. Plans retain their actual
outcomes, and the planner sees recent prior-plan outcomes for continuity. A visible
person is not an identity assertion. Explicitly general plans survive changed visibility without restarting their steps.
Personal/camera/target evidence narrows a proposed general plan to its audience;
public observations and impersonal character ideas can remain general. Audience-scoped notes are withheld when the
scope differs; camera loss still permits a fresh generic plan. Room-wide object
identity and semantic non-repetition remain live evaluation questions.

Explicit quiet, human-floor state, queued input, fresh microphone requirements and
shutdown still gate activity. Pending identity questions and unanswered social
invitations do not freeze unrelated plan steps. Invitations retain their own
explicit heard/open/answered/declined/lapsed lifecycle; time never invents an answer.
`next_opportunity` is retained only as validated invitation review metadata and
cannot schedule general work.

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
unchanged empty room and after a fictional conversation with camera input. It uses
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
