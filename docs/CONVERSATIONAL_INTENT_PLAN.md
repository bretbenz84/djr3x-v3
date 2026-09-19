# Greeting-led conversational intentions

Status: proposed implementation plan, September 18, 2026. No runtime changes or
validation have been performed. This document plans the user's brainstorming
request; it does not authorize implementation, hosted trials or hardware operation.

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

## Current foundations and gaps

- `runtime.py::proactive_tick` offers arrivals, conversation lulls and ambient cues.
  It currently requires a stable visible audience; a normal lull is offered once
  per human source turn. Eight-second quiet and existing pacing gates still apply.
- `proactive.py::Initiative` owns session pacing, feedback and delivery outcomes.
- `CoreState.context` and `opportunity_context` provide the shared conversational
  context, including eligible dated memories and participant boundaries.
- `Session` already separates proposed actions from actual speech delivery. It
  currently accepts one tool call per response and forbids mixed tool/prose output.
- Startup greeting checks in schemas, Session and state currently restrict tool
  choices to `ask_face_question`; ordinary greeting prose is also supported.
- There is no explicit retained conversational intention or model-selected wakeup.

## First-version scope

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

## Later decision: model-selected reconsideration

After this version is evaluated, consider a bounded `reconsider_after` request or
event condition. It would schedule an opportunity through the same owner and
existing budgets, never scheduled speech. It needs separate expiry, deduplication,
quiet/floor handling and cost tests. Long-lull thought experiments, richer
motion-aware noticing and audio-only initiative remain separately scoped work.
