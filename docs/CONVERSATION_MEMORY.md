# Cross-session conversation memory — September 30, 2026

The conversation owner now receives bounded prior exchanges, accepted personal
facts and optional source-linked episodes. The same model chooses whether to use
those memories, retrieve more, retain a summary, or stay with the current topic.
No extra classifier, embedding model, mandatory summary call or shutdown worker
was introduced. V2 supplied the design reference; V3 imports no V2 code or data.

## Storage and retrieval

An additive `conversation_memory_schema=2` schema creates indexed exchanges,
episodes and source attribution evidence in the existing V3 database on normal
startup. Version 1 records remain readable with their original source attribution.
Each eligible exchange records its session, audience scope, source turn, wall-clock time, original
utterance, delivery status and completed audio phrases. Generated tails and
text-only output do not become heard replies. Interrupted exchanges retain the
human words and any completed phrases. Source-only turns remain in their original
archive. Each attempt emits `conversation_memory_record` with its recorded/skipped
status and reason; write errors emit `conversation_memory_failed`.

Private memory requires a supported speaker or a sole recognized, supported
visible person; unknown/additional visible people withhold that context. Visibility
may authorize discussing a person's memories in their presence, but never assigns
an uncertain utterance to them. Trusted speech during that person's visit is now
retained even when voice identification abstains. The exchange's `person_id` is its
audience scope, not a claim about who spoke. Separate attribution records retain
the source speaker state, candidates, reason and visible people. Capture-time
audience evidence is kept even if the camera changes during the reply. No-camera
use can use a supported speaker. This is conservative audience
filtering, not proof of privacy or an implementation of group memory sharing.

Automatic context contains up to four prior exchanges, six accepted facts, three
current-session exchanges and four episode interpretations with source excerpts.
The prompt allows a relevant hobby or unfinished topic as a greeting, without
requiring a special follow-up registration or inventing a past event. Dated
follow-ups keep their existing dedicated delivery tracking. Completed exchanges
provide cross-session repetition evidence, not an automatic topic prohibition.

The model can call:

- `recall_conversations`: query words plus optional explicit-offset ISO8601 bounds or numeric
  seconds-before-now bounds. Two numeric endpoints form an age window in either
  order; code performs the clock arithmetic.
  The model interprets language and dates; code applies person/session/time filters
  and lexical ranking. Search considers the latest 200 matching exchanges and
  returns at most 12, with interval count and truncation metadata. Human and heard
  excerpts are capped at 600 characters and marked when truncated. Local and UTC
  clocks are supplied; prior sessions are excluded from the current transcript.
- `remember_episode`: optionally save a short interpretation of one existing
  exchange. Original human words and heard speech remain attached. This is a
  fallible summary, not a verified personal fact or a claim that plans happened.
- `retract_episode`: withdraw a mistaken interpretation. Fact corrections continue
  through the existing claim tools; current accepted facts and later corrections
  override historical descriptions. Forgetting cascades through personal exchanges
  and episodes, including known affected source turns and cached operation results.

Retrieval/summary tools use the existing validated tool-result continuation. An
ordinary reply or greeting still needs only one model generation. The audience is
rechecked before private-memory output begins when fresh camera evidence exists.
Already-started audio follows the existing explicit-stop policy.

## Historical coverage and limits

Pre-upgrade source turns used a monotonic clock and did not persist assistant
speech in the memory database. An unbounded recall request can therefore return
up to eight trusted, supported-person original utterances, explicitly labeled
undated and without delivery/audience evidence. Time-filtered recall does not
invent dates for these sources. No old logs or V2 personal databases are imported.
New time-aware exchange history accumulates from the next normal launch.

Exact historical greeting counts, automatic whole-session summaries, shared group
recall, arbitrary semantic search and corrected-speaker reassignment are not added.
The model can save meaningful source-linked episodes during a later turn; summaries
are not guaranteed for every session. Presence across head/body movement remains a
separate outstanding repair. Unknown/group audiences still prevent private exchange
archiving. Transcript-quality, echo, truncation and overlap gates still apply.
Previously skipped exchanges are not reconstructed automatically from logs; the
new behavior starts on the next launch of the updated program.

## Verification

Synthetic regressions exercise restart, original words and completed audio,
interruption, production tool continuation, greeting interests, audience isolation,
uncertain voices, date bounds/truncation, summary source validation, retraction and
forgetting. Full-suite results and hosted evidence are recorded in
[implementation status](IMPLEMENTATION_STATUS.md).

`.venv/bin/python -m tools.check_conversation_recall` is an explicit hosted probe
using fictional identity and conversation, temporary databases and synthetic
playback. It does not operate devices or read personal memory. Real-room memory
usefulness, group privacy, speaker accuracy, playback and long-term storage/query
performance remain unmeasured.

Final hosted development probe: four fictional cases, seven model calls. Retrieved
and correctly described the two-hours-old telescope exchange; returned no exchange
for the year-old window without substituting a recent topic; used the telescope
in a greeting; saved an accurate source-linked episode before acknowledging it.
Wording still embellished the untested motor as "stubborn"; this does not establish
flawless grounding or social acceptance. Earlier probes exposed out-of-period
fallback, a rejected empty query and reversed relative offsets; the final code
uses nullable queries, scoped continuation context and normalized numeric windows.
These are development cases, not held-out accuracy measurements.

Final usage: 78,359 input tokens (50,688 cached), 925 output tokens. Four request
wall times had 2.52 s median and 4.68 s maximum, including hosted generation and
synthetic output. This is not physical first-audio latency or a tail benchmark.
No resident model was added; local model load/RSS and large-archive query latency
were not measured. Private receipts:
`local/validation/conversation-memory-c46dd851c3f44eaab2261123873b120d/`.

## September 30 audience-memory repair

Two dev-Mac sessions exposed an incorrect archive gate: trusted multi-sentence
story turns received complete replies but were discarded when an individual
voice window failed identity checks. The next run retrieved only the supported
short reply and greeting. This was missing exchange storage, not missing LLM
understanding. The replacement records the conversation under its recognized
audience, keeps acoustic attribution separate, and supplies that evidence on
automatic context, explicit recall and episode summaries. The LLM can naturally
recall what was discussed without claiming every nearby speaker was the visitor.
Explicit speaker corrections remain original exchanges; this change does not
certify a voice, enroll samples or turn uncertain speech into accepted biography.
Forgetting also redacts source turns of audience-scoped uncertain exchanges.

Verification covers the long-story/window-abstention pattern through the production
Session, SQLite close/reopen and tool continuation, retained speaker corrections,
no accepted-fact promotion, differing known speakers, changed/unknown/group
audiences, unusable transcripts, version-1 upgrade and cascading forgetting.
The hosted fictional probe now starts with a window-conflicted telescope story
and actually reopens the database before recall. See implementation status for
results; acoustic accuracy and live dev-Mac acceptance require a fresh run.

Final repair evidence: **792 synthetic blocked-I/O tests passed**. Four hosted
fictional cases completed in 2.43 s median / 2.60 s maximum total
request time with synthetic playback, using 7 model calls,
82,007 input tokens (58,624 cached), and
650 output tokens. The model recalled the unresolved-speaker telescope
exchange after restart, respected an empty historical window, greeted from memory
and saved an accurate source-linked episode. Its greeting still embellished the
motor as “dramatic”; these development probes do not establish perfect grounding
or live acceptance. No new resident models or physical devices; RSS, large-archive
query cost and acoustic accuracy remain unmeasured. Private receipts:
`local/validation/conversation-memory-1a771a221c3d494b978ae9b4f1d24a64/`.
