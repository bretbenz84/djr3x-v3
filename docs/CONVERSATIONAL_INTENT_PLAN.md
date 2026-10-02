# Greeting-led conversational intentions

**October 1 replacement:** [persistent narrative and conversation plans](AGENCY_IMPLEMENTATION_PLAN.md)
now own proactive progression. Stronger private planning and cheaper step execution
replace optional silence voting and general self-scheduling timers. The dated design
below remains background for shared social state and evidence boundaries; it does not
describe the current scheduler. Live acceptance of the replacement is pending.

October 1: the [agency increment](AGENCY_IMPLEMENTATION_PLAN.md) extends this record
with bounded working interests and separate model-selected reconsideration. It
removes the later 20/60-second audible floors and visible-person prerequisite.
The historical implementation and evidence below describe their dated versions.

Status: software implemented September 18, 2026 following the user's request to
implement this plan. The design and staged acceptance protocol below distinguish
software contracts from hosted semantic evidence and live visitor acceptance.
No live hardware or personal database was operated for this implementation.

## Intended behavior

When greeting someone, the existing conversation model may retain a small,
revisable intention for the visit. A new visitor might welcome an introduction and
an opening to discuss an interest. A recognized visitor might welcome a relevant
check-in. These are model judgments, not fixed scripts or a required interview.
Recognition establishes neither friendship nor a previous successful conversation.

An intention gives later replies and proactive opportunities continuity. It is
subordinate to what people actually say and may be replaced, completed or abandoned.
Rex does not need to name every visitor, collect biography, or finish his intention.
No durable interest, relationship, identity or emotional state is inferred from it.

Example: greet an unfamiliar visitor, learn that they want to discuss Rex's camera,
and switch from getting acquainted to that discussion. For a familiar visitor with
an eligible trip memory, offer one follow-up; if they change the subject, follow
them instead of pursuing the trip.

## Foundations and gaps when proposed

- `runtime.py::proactive_tick` offers arrivals, conversation lulls and ambient cues.
  It currently requires a stable visible audience; a normal lull is offered once
  per human source turn. Eight-second quiet and existing pacing gates still apply.
- `proactive.py::Initiative` owns session pacing, feedback and delivery outcomes.
- `CoreState.context` and `opportunity_context` provide the shared conversational
  context, including eligible dated memories and participant boundaries.
- `Session` already separates proposed actions from actual speech delivery. It
  currently accepts one tool call per response and forbids mixed tool/prose output.
- Before this implementation, startup greeting checks restricted tool
  choices to `ask_face_question`; ordinary greeting prose is also supported.
- Before this implementation, there was no explicit retained conversational intention.
  Model-selected wakeups remain outside this implementation.

## Original first-version scope

Implement greeting-led, session-only intentions and their revision during existing
turns. Preserve ordinary streaming replies and the single primary generation.
No separate planner, classifier, resident model, background LLM polling, general
action sequencer, hardware behavior or durable memory schema is needed.

Keep the existing scheduler and call budgets. Intentions are context, not triggers
or permission to speak. This version will not solve long-silence re-engagement or
audio-only proactive availability. Those require separately evaluated scheduling
work; a retained intention must not quietly bypass today's gates.

## State and evidence contract

Add a small typed `conversation_intent.py` module owned by `CoreState`. Keep one
active intention for the shared visit, with at most two optional conversational
openings. This avoids competing per-person agendas in a group.

Proposed record:

| Field | Purpose |
| --- | --- |
| ID and revision | State-owned identity, stale-update rejection and correlated receipts |
| Objective | One short model-authored conversational intention, not biography |
| Audience references | Offered person/face/track IDs with their evidence type; group scope allowed |
| Source references | Originating greeting event or human turn; bounded references to offered supporting evidence |
| Optional openings | At most two short directions to consider, not queued questions or commands |
| Memory references | Eligible claim IDs, not copies of private facts in a free-text agenda |
| Status | Active, suspended, completed or abandoned |
| Latest change | Source-linked reason and time; model interpretation distinct from observations |
| Delivery links | Existing receipts for the associated greeting/contribution; generated is not heard |

Initial bounds: 240 characters for objective, 160 per opening/change reason,
eight evidence references and eight prior transition receipts. Include at most
2,000 serialized characters of intent context, evicting old receipts first and
preserving complete current fields. Reject oversized proposals rather than truncate
their meaning. These are initial engineering limits to measure, not calibrated
social thresholds or new user configuration switches.

Use a separate session intent revision alongside the existing store revision;
do not increment the persistent database merely to update conversational guidance.
Validate request/session/source IDs, target scope, offered evidence, both revisions
and cancellation before committing. Requests are idempotent within the bounded
turn/result lifecycle. An evicted or stale request cannot recreate old state.

The LLM selects relevance, objective and semantic changes. Code validates authority
and evidence; it must not classify free speech through keywords or name regexes.
Unknown visitors may have face-scoped intentions without being named speakers.
An introduction does not transfer that face's intention to an uncertain voice.
Any rebinding must use the existing validated association, never name equality.

## One-call model integration

Add `respond_with_intent`, a typed operation containing natural spoken text and an
intent proposal. It supports a generic greeting or a normal reply that creates,
replaces, completes or abandons the intention. It returns text directly to the sole
output owner after validation, with no result-generation call. Plain streaming
prose remains the default when there is no useful state update.

Add a nullable `intent_update` to the existing `ask_face_question`,
`use_memory_opportunity`, `conversation_feedback` and `remain_silent` operations.
This lets the model greet/ask, select a validated memory, update feedback, or remain
silent while updating its intention in the same tool call. In strict schemas the
field is present but may be null; planning itself is optional. Supply one shared
nested schema and explicit nested validation rather than duplicate validators.

Apply combined proposals atomically under state ownership: invalid planning data
cannot partially apply feedback or speak a success acknowledgment. Keep quiet/stop
handling independent of optional planning so a planning error cannot block them.
Do not add fields to unrelated identity/photo tools or allow multiple simultaneous
tool calls. When another operation is required, perform it normally; refresh the
intention on a later eligible turn if useful, with no extra planning continuation.

Update all three startup greeting restrictions coherently. Permit a spoken
`respond_with_intent`, a targeted question, or an eligible memory contribution with
a greeting. Preserve the startup greeting requirement; a silent plan is not a
successful greeting. Memory-bearing speech must still use `use_memory_opportunity`
and its executor checks, never bypass them through free-text intention output.

Model guidance should explain:

- Use recognition, accepted evidence and current context, not a binary friend/new
  person script. Generic warmth is possible without personal memory.
- Prefer one natural opening; do not announce the plan, enumerate questions or
  turn the conversation into onboarding.
- Update only when useful. Follow a human-led topic even if no update tool is used.
- Use original source words and actual delivery to assess progress. A short reply
  or unanswered question does not establish disinterest or completed rapport.
- A plan is revisable interpretation, not an instruction that overrides current
  input, quiet mode, participant boundaries or the conversational floor.

## Lifecycle, privacy and cancellation

Provide eligible intent context to ordinary replies and proactive opportunities.
Revalidate referenced memories against current claims, dates and audience on every
use. A formerly private opening must not become visible to a newly arrived group.
Do not retain private claim text in the intention; withhold affected openings when
their source is unavailable, retracted or no longer audience-eligible.

Audience change suspends the old intention before another contribution. A fresh
model decision may replace it with a group-appropriate intention. Temporary stale
camera evidence suspends use without treating a missed frame as a departure. Reuse
the existing 30-second absence evidence to abandon a departed target's intention.

Explicit quiet suspends initiative without disabling direct replies. Resuming
initiative permits reconsideration, not automatic playback of a saved opening.
Existing topic/floor boundaries always win. Completed/abandoned intentions do not
reopen automatically. Forgetting and affected identity corrections clear dependent
intent state and receipts; restart clears all intent state.

Expire after 15 minutes without a processed human turn or completed audible
contribution for the relevant audience. Silent model edits cannot keep it alive.
This proposed fixed expiry bounds stale session context; evaluate it before exposing
any configuration. Prune on existing runtime/context activity, with no new worker.

A validated intention may exist before its greeting plays, but mark its introduction
as undelivered. Failed/cancelled output cannot count as a question asked or a topic
explored. Partial output retains the existing uncertainty. Cancellation after a
state transition does not erase history or silently schedule a retry. The next
turn receives the actual outcome and may discard the intention.

## Implementation sequence

1. **State contract:** add bounded typed state, revisions, transitions, provenance,
   lifecycle and context filtering. Add focused unit checks with temporary state.
2. **Greeting integration:** add schemas, validation and direct output handling;
   update startup tool restrictions together. Exercise generic, unfamiliar and
   memory-led greetings through production Session with scripted providers.
3. **Conversation integration:** add optional updates to the four existing tools,
   ordinary/proactive context, boundary/identity/forget invalidation, delivery links
   and bounded trace events. Keep the scheduler unchanged.
4. **Semantic evaluation:** once separately authorized, compare the existing model
   with/without intention context on identical fictional multi-turn scenarios.
   Revise prompt/schema based on observed failures, not a different conversation loop.
5. **Live acceptance:** once explicitly authorized, test unfamiliar/familiar/mixed
   visitors, interruption and natural topic changes. Record participant observations
   before declaring continuity or social quality improved.

Likely touch points: `conversation_intent.py`, `state.py`, `semantics.py`,
`session.py`, `adapters/hosted.py`, lifecycle hooks in `runtime.py`, trace/report
support and production-Session tests. Reuse Initiative's existing feedback and
outcome records instead of creating duplicate engagement or question tracking.

## Acceptance and measurement

Synthetic blocked-I/O contracts must cover:

- New visitor → greeting → human-chosen topic → revised intention, with one primary
  call per ordinary turn and no planning-only continuation.
- Recognized visitor with/without eligible memory; unknown face and ambiguous voice
  never gain identity, biography or friendship from planning.
- Name question and memory check-in carry intentions in a single validated action;
  pending questions and memory delivery remain dependent on actual audio receipts.
- Changed audience, private memory, corrected/retracted source, topic avoidance,
  quiet/resume, thinking pause and human-to-human exchange.
- Missed camera frames, actual departure, restart, forgetting, identity correction,
  stale revision, malformed/duplicate request, capacity and expiry.
- Cancellation before validation, after commit and during playback; failed output
  and unheard greetings never count as engagement or automatically replay.
- Required unrelated tools still work without planning, and ordinary prose keeps
  its streaming path. Frozen replay disallows intention mutation.

Run the v3 blocked-I/O runner and focused production-Session scenarios. No devices,
network or personal databases belong in ordinary tests.

Hosted evaluation should use fictional people and held-out wording across at least
12 scenario families, repeated three times for baseline and candidate. Review
the actual dialogue as well as tool state: appropriate continuity, responsiveness
to redirection, repeated questions, intrusive check-ins, invented familiarity,
privacy mistakes, missed welcome openings and unnecessary tool use. Compare the
same inputs/settings; retain failures and variation. This is a proposed protocol,
not a current test result or general accuracy claim.

Measure primary/continuation calls, input/output tokens, actual returned usage,
first output and completion median/tail, planning tool frequency, context size and
rejected transitions. A combined planning tool buffers speech until validation;
one call does not mean zero added first-audio latency. Compare against ordinary
greeting streaming and report that cost explicitly. Live evaluation adds actual
first audio, interruption/playback and target-M2 resource measurements.

Delivery criteria: all structural contracts pass; hosted trials demonstrate useful
continuity without observed boundary/privacy failures or systematic extra calls;
live participants find it natural rather than an interview. Retain unresolved
semantic failures explicitly. Synthetic success alone completes only the software
contract, not social acceptance.

## Later decision: model-selected reconsideration (still deferred)

After this version is evaluated, consider a bounded `reconsider_after` request or
event condition. It would schedule an opportunity through the same owner and
existing budgets, never scheduled speech. It needs separate expiry, deduplication,
quiet/floor handling and cost tests. Long-lull thought experiments, richer
motion-aware noticing and audio-only initiative remain separately scoped work.


## Implementation details

`conversation_intent.py` now owns the typed bounded record under CoreState. The
shared Session supplies context to ordinary/proactive calls; no new opportunity,
scheduler, provider, model or setting was added. `respond_with_intent` and optional
updates on name questions, memory contributions, feedback and silence use the same
primary generation. Unchanged replies continue streaming normally.

Hosted strict schemas include nullable `intent_update`; programmatic core callers
may omit it. A supplied update is fully validated. There are no fallback planning
handlers or model-prose parsers. Rejected combined updates produce an explicit
failure without applying their associated feedback or adding a recovery generation.

Implementation bounds: one intention, two openings, three selected targets, eight
memory references, eight transition receipts, 32 idempotence receipts and 2,000
serialized characters of model-facing intention context. At most six typed target
choices are offered. Oversized plans reject; history receipts are omitted first.
Expired or audience-ineligible text is withheld rather than truncated into a new
meaning. Completed/abandoned records also expire. Explicit stop suspends an intent.

New sessions clear intentions even when a harness reuses CoreState. Forgetting, association cancellation, face-identity reset, or claim correction/retraction
conservatively clears the whole intention. Audience changes
suspend and hide its text; a replacement for another audience drops old transition
text. A brief camera gap suspends; 30 seconds without a selected target (or any original group member for a
group-scoped intention) clears it. This is presence bookkeeping, not proof that someone physically left the room.

Already-delivered follow-ups remain eligible as source references while the claim,
date and audience permit them. They remain excluded from proactive delivery
candidates, preventing the intention from rearming a check-in. Existing text history
still requires model discretion; this feature does not retroactively redact all
previous dialogue when someone joins.

Speech and plan outcomes remain separate. Updated intentions initially have no
associated delivery; completed phrases, text-only results, interruption and failure
are recorded explicitly. Planning cannot confirm a name, save a personal interest,
or infer that a question was answered. Traces/report summaries include intention
transitions and rejected proposals; full private diagnostics remain under `local/`.

## Verification evidence

The final blocked-I/O suite passed **451 checks**, including 34 new intention
contracts. These use synthetic voices/output and temporary databases; no real model
or device runs inside this suite. Two last lifecycle regressions first failed,
then passed after fixing selected-target departure with a remaining bystander and
cached human results after a newer source turn.

Hosted development comparison: **12 families × 3 repeats × 2 arms**, 36 conversations
and 105 scheduled turns per arm, using `gpt-5.4-mini` with low reasoning.

| Observed metric | Baseline | With intentions |
| --- | ---: | ---: |
| Primary model calls | 99 | 99 |
| Existing tool-result continuations | 1 | 0 |
| First usable text, median / p95 | 0.837 / 2.423 s | 1.591 / 2.617 s |
| First usable text sample count | 86 | 88 |
| Turn completion, median / p95 | 0.978 / 2.044 s | 1.549 / 2.611 s |
| Reported input tokens | 792,932 | 938,129 |
| Reported output tokens | 6,118 | 16,746 |
| Final retained conversation failures | 0 | 0 |

First text measures model-request to the first `text_reply`, not audible speech.
Completion includes suppressed turns, so its distribution has a different sample
mix. Request pacing is excluded. First model delta median/p95 was 0.693/1.309 s
for 74 instrumented baseline requests and 0.801/1.558 s for 99 candidate requests;
older retained baseline traces lack this extra instrumentation. Function-argument
deltas are not usable speech. Structured replies increased first-text median by
about 0.75 s in this sample; 65 of 99 candidate primary calls used
`respond_with_intent`. No extra planning generation was introduced.

Offered intention context measured 881 characters median, 1,164 p95. A separate
1,000-call synthetic three-face context microbenchmark measured 0.067 ms median,
0.081 ms p95 and 24.7 MB process lifetime peak RSS. That small empty-store process
is not a combined-load robot benchmark. Raw tokens are provider-reported usage,
not a billed-dollar estimate.

Ignored evidence directory:
`local/validation/conversation-intentions-0236719d2ac64a4db96bb8689fd51cc1/`
contains `evidence.json`, `summary.json`, `review.json`, individual traces, preserved
prior attempts and `contract-checks.log`. The separate context measurement is
`local/validation/intent-context-microbenchmark.json`. Final hosted candidate runs
used one consistent prompt/schema/module revision. The two subsequent lifecycle
fixes are covered by contract checks; these fixed-audience hosted scenarios do not
exercise those branches.

Hosted trials use fictional visitors, synthetic speaker evidence, temporary databases
and silent TextOutput through the production Session. They do not establish actual
greeting delivery, acoustic accuracy or social acceptance. The baseline adapter and
schemas come from `a510502`; both arms use the shared current core. Several prompt
variants were tried on these families, so these are development probes, not a fully
held-out or randomized benchmark. Early token-rate failures and prior attempts are
preserved separately; repaired final conversations must not be interpreted as zero
failures across all attempts.

Manual review found useful explicit continuity from welcome to bird photography,
sailing/navigation, music and movies. Quiet requests, thinking pauses and human-floor
holds kept intentions suspended while direct questions could still receive answers.
No private sailing detail appeared in the bystander scenarios reviewed. This is a
small observed result, not a guarantee against all privacy failures.

Semantic limitations remain: some generic welcome objectives linger, a recognized
visitor was sometimes asked for their name, and one unanswered-greeting probe
produced unnecessary reassurance. Both arms already followed many topic changes;
these probes demonstrate retained state, not proven superiority in naturalness.
TextOutput is explicitly marked unheard, so the unanswered-greeting result cannot
establish how Rex behaves after actual audible delivery. Name-seeking prose in
`respond_with_intent` does not create the pending-question state owned by
`ask_face_question`; model tool selection still matters. Familiar-visitor check-ins
remain optional, and these probes do not prove reliable spontaneous recall.

Live visitor acceptance, actual first audio, interruption/playback quality and
combined-load M2 performance remain unmeasured. No local model, camera, microphone,
speaker, motor or personal database was opened. No new resident model was added.


## Follow-up implementation: continuing lulls

September 29 repair supersedes the plan-required restriction below: ordinary
trusted conversation can receive spaced follow-ups when no intention exists or
camera changes suspended it. The old intention is not automatically reactivated.
Current behavior and verification: [engagement repair](LIVE_RUN_CONVERSATION_REPAIRS.md#september-29-engagement-repair).

The user subsequently requested that proactive conversation actually use the plan
when a visitor stops talking, rather than stop after one lull contribution. The
runtime now reoffers the same conversation owner an opportunity while a plan is
active and its original audience remains eligible. It also works after a greeting
with no human source; state validates that case against the active audience-bound
intention rather than inventing a human turn.

After the initial human-turn lull, same-source reconsideration backs off through
8, 15, 30 and 60 seconds after attempts finish. With no human turn after a greeting,
the first reconsideration waits eight seconds after greeting delivery. A new processed human turn restores
normal first-lull eligibility without resetting the global call budget. All attempts,
including failures and silence, count; changes to plan wording cannot reset pacing.
A model-selected delay/tool remains deferred: this implementation uses the existing
idle worker and a bounded pacing policy, not another scheduler or model.

An active plan is permission to reconsider, not a promise to talk. The prompt asks
for a new contribution relevant to the plan, makes listening without responding a
valid interaction, and favors remarks over repeated questions. Existing receipts
show what actually played. Silence can preserve a plan for later; completion,
abandonment, suspension, expiry or changed audience prevents repeated plan lulls.
Quiet, pending questions, speech/capture priority, floor holds and the rolling
six-attempt/five-minute limit remain effective. No model, device or configuration
switch was added.

Verification: **458 blocked-I/O checks pass**, including seven new production-runtime
contracts for repeated lulls, greeting-only starts, silence/completion, changed
audience, quiet/pending questions, failure backoff and new-human-turn pacing. Fake
TTS/sink delivery verifies that the next opportunity sees prior heard contributions;
no real audio played. Hosted follow-up evidence is recorded below separately.


Hosted development probe: one five-turn dialogue per arm, using fictional bird
photography interest followed by three opportunities without further human input.
The candidate retained the objective and contributed distinct framing, eye-sharpness
and movement-prediction ideas. Both arms used five primary calls, zero continuations
and reported no provider failures. The baseline also offered ideas but announced a
lack of fresh angles before contributing again. No comparative quality claim follows
from this single example.

Evidence: `local/validation/conversation-intentions-417b9edd9c804631ba5450e468dcde77/`
contains dialogue, summary, review and contract-check results. This probe explicitly
uses fake TTS/sink **simulated delivery receipts**, not real playback, and directly
offers ambient events through Session; runtime backoff is covered separately by
fake-clock tests. Thus it demonstrates semantic continuation, not measured real-world
spacing, audio latency or sustained novelty. Reproduce with:

```sh
.venv/bin/python tools/check_conversation_intentions.py --repetitions 1 --families listening_lulls --synthetic-delivery
```


The user rejected the initial 30-second follow-up wait as socially too slow. Current
pacing is **8, 15, 30, then 60 seconds after the preceding attempt finishes**, not
from generation start. The normal configured lull minimum and shared six-attempt
rolling budget still apply, so these are earliest reconsideration times, not speech
deadlines. Model and TTS latency follow reconsideration. **459 blocked-I/O checks
pass**, including an additional slow-output test proving the delay starts after
completion. The previous hosted dialogue probe used the earlier spacing; the revised
spacing has synthetic runtime evidence only and has not been tested on hardware.


October 1 update: [model-selected JSON scheduling](NEXT_CONVERSATION_SCHEDULING.md)
is now implemented through the first agency increment.


## September 19 revision: an invalid plan no longer fails the turn

Hosted checks showed whole turns failing on planning errors (`NoIntentToFinish`,
`IntentObjectiveRequired`), including a topic boundary that was never recorded. The
all-or-nothing rule above is superseded: a malformed, stale or unoffered `intent_update`
is dropped and traced, and the reply, feedback or silence decision proceeds without it.
State staleness, source trust and frozen evaluation still reject the whole operation.
The per-turn "Conversation continuity" instruction was also removed; the persona states
once that the intention tool is for the greeting and for real changes of direction.
See [persona consolidation](PERSONA_REWRITE.md).

### September 30 audible turn-taking floor

The newer user feedback supersedes the eight-second audible follow-up policy:
ordinary speech/greetings reserve 20 seconds before unsolicited opportunities;
non-arrival proactive speech reserves 60 seconds, measured from completion.
Silent/unheard attempts keep their existing backoff. Direct human requests still
run immediately. Active intentions do not bypass these waits or require speech
while a question is unanswered. See [repair and evidence](LIVE_RUN_CONVERSATION_REPAIRS.md#september-30-turn-taking-repair).


October 1 increment 3 adds a bounded social-invitation lifecycle alongside working
interests, owned by Initiative under CoreState. Existing reply/silence tools can
optionally update both in one generation. Lapsing an invitation does not automatically
abandon its underlying interest or infer a human feeling. See
[implemented behavior](AGENCY_IMPLEMENTATION_PLAN.md#increment-3-implementation--october-1-2026).
