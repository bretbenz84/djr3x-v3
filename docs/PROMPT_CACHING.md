# Prompt caching repair — September 18, 2026

Implemented in the hosted adapter. Owner-reported OpenAI spending had risen from at
most about $0.80 per day under V2 to about $2.00 per day under V3.

## Finding

Every recorded tool-enabled V3 request reported `cached_tokens = 0` on roughly
9,000–11,000 input tokens, across gpt-5.4-mini, Luna and Terra trials. The 5.6 models
also reported a cache *write* of nearly the whole prompt on every call and never a
read. The only earlier cache hits (4,864 tokens, the persona) were tool-less
introduction continuations in the September 17 network benchmark.

OpenAI caching matches the rendered request prefix, and tool definitions render before
`instructions`. `grounded_tools` placed per-snapshot identifier enums inside the
transmitted schemas; `source_turn_id` changes every turn, so the prefix diverged within
the first few hundred tokens and neither the tools nor the 4,900-token persona after
them could be reused.

## Change

- The request now transmits the byte-identical static `TOOLS` list. The offered subset
  is expressed with `tool_choice: {type: allowed_tools, mode: auto|required}`
  (`none` when nothing is offered). `grounded_tools` remains the single definition of
  what is offered and which identifiers are valid.
- `semantics.ground` applies that snapshot grounding to each returned call before it
  leaves the adapter. A field with exactly one permitted value (for example
  `source_turn_id`) is supplied, exactly as the strict enum previously forced it. A
  tool that was not offered fails the turn with `ToolNotOffered`. Other identifiers
  pass to the state owner, whose existing revalidation rejects unknown or unoffered
  targets with a structured result. (The first version of this repair failed the whole
  turn with `UngroundedToolValue` instead; hosted checks showed that silenced an unknown
  speaker's request, so it was changed on September 19. See
  [persona consolidation](PERSONA_REWRITE.md).)
- `model_usage` trace events and `report` now include `cached_tokens`.

No model, prompt text, tool contract, setting or extra request was added. The model now
sees descriptions of tools it is not currently allowed to call, and identifiers are no
longer constrained during decoding; both are behavior risks that the synthetic suite
cannot assess.

## Evidence

**465 blocked-I/O checks pass**, including two new adapter contracts: transmitted tools
stay byte-identical while offers and identifiers change, and returned identifiers are
grounded locally (supplied singleton, fabricated ID rejected, unoffered tool rejected).

Paid hosted probe, gpt-5.4-mini/low, synthetic typed turns through the production
Session/CoreState/OpenAIBrain with in-memory state and TextOutput. No devices or
personal data. Private script and receipts: `local/validation/prompt-cache-probe/`.

| Request shape | Turns | Input tokens per call | Cached per call after the first |
| --- | ---: | ---: | ---: |
| Previous production payload | 4 + 4 | 8,932–9,513 | 0 |
| Static tools (wrapper experiment) | 4 + 10 | 12,103–14,968 | 11,008 |
| Static tools + context moved after history (experiment only) | 4 + 10 | 12,125–15,424 | 11,008 rising to 14,080 |
| Patched production adapter | 5 | 12,108–13,017 | 11,008 |

In the patched run a self-introduction and a `record_claim` both executed with valid
free-form identifiers. Tool-less result continuations cached 0 and 5,888 tokens; they
use a different prefix and were not changed. First-content latency showed no clear
difference in these small samples (0.5–1.5 s in every arm, one 4.1 s outlier).

The full static list adds about 3,100 input tokens per call, all within the cached
prefix. At the published $0.75 / $0.075 / $4.50 per million input / cached / output
tokens, a typical primary call falls from about $0.0069 to about $0.0019 of input.
This is an estimate from returned usage, not an invoice, and it applies only while
calls arrive within the cache lifetime (minutes for this model). A long idle gap
produces one uncached call.

## Not done

- **Context after history** was adopted on September 19 together with the
  [persona consolidation](PERSONA_REWRITE.md): `instructions` is the static persona and
  the request-specific notes and context follow the history. The experiment above
  cached 91% of a ten-turn request in that layout. Its hosted comparison was run jointly
  with the new persona, not separately.
- History eviction shifted the prefix every turn once the 32-exchange / 24,000-character
  budget was full. Robot sessions on October 10–11 showed the result: about eight
  exchanges in, every later reply reported `cached_tokens = 0` and rewrote about 26,000
  tokens of cache. Since October 11 a full budget evicts down to two thirds in one block,
  so the prefix holds until the budget fills again. Cache rate after the change is not
  yet measured live.
- Extended cache retention and `prompt_cache_key` were not adopted or evaluated.
- `tools/check_conversation_intentions.py` swaps `grounded_tools` for its baseline arm;
  the transmitted static list now always comes from the current `TOOLS`, so that
  historical baseline comparison is no longer exact.
- Live conversation has not been rerun on this change. The hosted semantic suites were
  rerun on September 19; results are in [persona consolidation](PERSONA_REWRITE.md).
