# Persona prompt consolidation — September 18–19, 2026

(November 10: the character section was replaced by a much shorter one; see
[replies from Claude](CLAUDE_REPLIES.md). The operating rules described here remain.)

Implemented at the owner's request. The previous persona had grown to 423 lines through
successive incident patches, repeated the same rule in several places, and contained
instructions that disagreed. This replaces it with one shorter, ordered prompt. Hosted
evidence below is synthetic text only; live conversation has not been rerun.

## Owner decisions applied

- Rex should be **eager to learn names**, but never by interrupting people.
- **Appearance remarks are allowed**, kindly and with honest uncertainty. Inferring
  identity, emotion, health or private facts from an image is still excluded.
  (November 3: the code's own reading of a face, `right_now.expressions`, is allowed;
  [what faces show](FACE_EXPRESSIONS.md). Images are still not read for emotion.)
- **Keep conversational intentions.** Ordinary replies stay plain prose, which streams;
  the intention tool is for the greeting and for real changes of direction.
- After Rex's own **unanswered question**: do not repeat it or ask another; **crack one
  short joke** that needs no reply, then stay silent until something changes. Ask a name
  again the next time that person speaks to him; never after a decline.
- Under about four seconds to first audio is acceptable; keep the streaming path default.

## What changed

- `PERSONA` in `src/r3x/adapters/hosted.py` is 242 lines (21,265 characters; previously
  423 lines, 33,593 characters). Sections: voice, one priority ladder, what Rex is given,
  who he is talking to, his initiative (an ordered three-step decision), names and
  identity, memory, social state, what he sees, conversation intent, tool protocol.
- One precedence order replaces three separate "takes precedence" statements:
  boundaries and sensitivity, then the human floor, then required state changes, then
  answering, then Rex's own initiative.
- Single-incident patches were replaced by the principle behind them. Tool field rules
  live in the tool descriptions and are no longer restated in the persona.
- The per-turn "Conversation continuity" note that pushed `respond_with_intent` on every
  human turn with an active intention was removed; the persona states the rule once.
- Mode-specific notes (photo memory, inspection results, visual novelty, action
  continuity) are unchanged in wording.
- **Request layout:** `instructions` is now the static persona only. The mode notes and
  `CURRENT CORE CONTEXT` travel in one developer message placed after the conversation
  history, so tools, persona and history form one cacheable prefix. See
  [prompt caching](PROMPT_CACHING.md).
- **An invalid intention note no longer costs the reply.** Baseline runs showed whole
  turns failing with `NoIntentToFinish` or `IntentObjectiveRequired`, including a topic
  boundary that was therefore never recorded. A malformed, stale or unoffered
  `intent_update` is now dropped and traced (`conversation_intent_rejected`,
  `operation=continued_without_intent`); the reply, boundary or silence decision
  proceeds. State staleness, source trust and frozen evaluation still reject the whole
  operation. This reverses the earlier all-or-nothing rule in the
  [intention plan](CONVERSATIONAL_INTENT_PLAN.md).
- **Unknown identifiers reach the state owner.** The first caching repair failed the
  whole turn in the adapter (`UngroundedToolValue`) when the model supplied an
  identifier that was not offered; an unknown speaker asking to be remembered got
  silence. The adapter now supplies only single-valued fields; any other identifier is
  validated by the state owner, whose structured rejection the model can explain.
- `model_stream_error` records the provider's error code; `report` lists
  `tools_proposed` by name, so the share of replies using `respond_with_intent` is
  visible after a live session.
- `tools/check_proactive_conversation.py` accepts a no-question joke, as well as
  silence, for the unanswered-invitation case. `tools/check_conversation_intentions.py`
  reads the context from its new position.

## Evidence

**467 blocked-I/O checks pass.** New or changed contracts: static instructions with the
request context after history; a fabricated subject ID rejected by state and explained;
an invalid plan dropped without losing the boundary or reply; stale or unoffered plan
fields leave the intention unchanged.

Hosted comparison, `gpt-5.4-mini`/low, production Session with synthetic identities,
temporary stores and text output. Requests were paced five seconds apart: an unpaced
first attempt was rejected in-stream by the provider on most calls and was discarded.
Baseline is commit `03d89db` (old persona, first caching repair). Each suite ran once per
arm, so single-case differences are within run-to-run variation.

| Suite | Baseline | New prompt |
| --- | ---: | ---: |
| `check_meaning.py` | 12/14 | 13/14 |
| `check_meaning.py --variants` | 12/14 | 13/14 |
| `check_addressee.py` | 20/24 | 22/24 |
| `check_addressee.py --variants` | 23/24 | 21/24 |
| `check_addressee.py --listening` | 16/18 | 17/18 |
| `check_conversation_repairs.py` | 13/14 | 13/14 |
| `check_proactive_conversation.py` | 9/9 | 8/9 |
| `check_proactive_conversation.py --variants` | 9/9 | 8/9 |
| `check_conversation_feedback.py` | 4/11 | 9/11 |
| `check_conversation_feedback.py --variants` | 11/11 | 11/11 |
| `check_memory_opportunities.py` | 6/6 | 6/6 |
| Total | 135/168 | 141/168 |

The new-prompt column is the third revision, except the two proactive rows, which were
rerun after a fourth, targeted revision. The first draft scored lower (for example 7/9
proactive and 19/24 addressee): it asked for names during a thinking pause and after a
question one person asked another, answered navigation announcements, and replied in
prose where a state tool was required. Those findings shaped the revisions. Because the
prompt was revised against these same suites, they are development evidence, not a
held-out benchmark. `check_session_context.py` and `check_transcript_context.py` have no
pass count; their replies were read and were comparable in both arms.

The baseline's 4/11 feedback run is one early failure cascading through a sequential
dialogue: a topic boundary was lost to `IntentObjectiveRequired`, fixed above. The
baseline's two extra meaning failures were the adapter's `UngroundedToolValue`, also
fixed above.

Remaining failures with the new prompt:

- `natural_introduction` fails in both arms: the name is saved but the check expects
  voice enrollment to have started, which the synthetic capture does not trigger. In
  the first draft's run Rex also offered Bret's saved hobby as the newly introduced
  Jordan's.
  After it, "stop learning that name" has no `cancel_association` on offer and the model
  chose `set_initiative` instead; the check passes vacuously. Unresolved, both arms.
- Empty model responses for speech that deserves silence (navigation, a bystander, another
  assistant): three in the final addressee runs, two in the baseline. Rex is
  silent either way, but the turn is logged as `EmptyModelResponse` and no floor note is
  kept. Not changed in code.
- Both arms answer bare search fragments and a navigation instruction, and treat
  "heading east two" as complete.
- New-prompt misses seen once each: a reply to the unfinished "We're going to."; a quip
  at a lull after a human-to-human question; a gentle spoken line after a request to sit
  quietly in grief; a floor note not released. A declined invitation answered in prose
  failed in both arms.
- The share of replies using `respond_with_intent`, and therefore the latency effect of
  removing the per-turn note, was not measured; the intention evaluator was not rerun.
  Generic welcome objectives may linger longer without that note.

Estimated evaluation spend is about $3 of API usage (roughly 1,200 requests including
discarded and probe runs), estimated from request counts at published prices because
these tools do not record token usage. This exceeded the $1–2 quoted beforehand.

Private receipts: `local/validation/persona-rewrite/` (`baseline.json`,
`candidate-draft1.json`, `candidate2.json`, `candidate3.json`, `candidate4.json`, the old
and new persona text, the paced launcher and the arm runner).

## Not done

- No live conversation, audio, camera or robot hardware was used. Humor, warmth, name
  eagerness in a real room and first-audio latency are unassessed.
- The photo-memory and inspection notes still contain incident-specific wording.
- History eviction still shifts the cached prefix once the history budget is full.
