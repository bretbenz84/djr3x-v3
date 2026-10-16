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
follow-ups keep their existing dedicated delivery tracking. Saved facts carry when they were told
(`told`, wall clock) and, for a plan with a time, `planned_time` (October 15). Completed exchanges
provide cross-session repetition evidence, not an automatic topic prohibition.

`earlier_greetings` (October 14) lists the last three heard arrival greetings to the person,
across runs, with how long ago in words. Two restarts that evening both opened with "Hey,
Brett! Good to see you. How's your night going?"; the prompt had prescribed name plus
how-are-you, and the startup path lacked the visible-detail guidance. V2 kept "oh hey,
you're back!" alive by reading a persisted last-greeted time after a reboot; here code
supplies the same fact and the model chooses the words: a return is greeted as one, and
an earlier greeting's wording is not reused. Synthetic checks only; variety is **not yet
observed live**.

The model can call:

- `recall_conversations`: topic words plus an optional named period
  (`earlier_today`, `this_morning`, `last_night` = 5 PM yesterday to 5 AM today,
  `yesterday`), or explicit-offset ISO8601 bounds, or numeric seconds-before-now
  bounds. Two numeric endpoints form an age window in either order; a period with
  bounds is rejected. Code performs the local-clock arithmetic.
  The model interprets language; code applies person/session/time filters and
  whole-word ranking (the person's words count double). Search considers the latest
  200 matching exchanges. A period or bounds returns up to 60 exchanges oldest first,
  the person's words before Rex-only lines when trimming; otherwise at most 12.
  Each row carries a local time label ("Sat Oct 10, 7:03 PM (yesterday, evening)")
  and compact attribution (speaker person, state, reason; visible identities)
  without microphone geometry. Human and heard excerpts are capped at 600
  characters and marked when truncated; prior sessions only.
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
speech in the memory database. An unbounded recall whose dated results are empty
or match none of the query words can return up to eight trusted, supported-person
original utterances, labeled supplementary and undated, without delivery/audience
evidence. October 11 showed why they are withheld otherwise: offered beside dated
results, the undated caveat became Rex's reason to say he could not remember last
night. Time-filtered recall does not invent dates for these sources. No old logs or V2 personal databases are imported.
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

## October 3: the same visit survives a restarted face track

Run `d4634610d3c34bcda70ccfa1a1a893b3`: at 06:03 the owner said he would take his car's
bumper off; the exchange was stored. At 11:35 Rex greeted him by name, then answered "Do
you remember what I told you I was planning on doing today?" with silence and "I told you
I was going to work on my car" with "I don't have a record of you mentioning the car".
The storage and the model were not at fault. Private memory was gated on recognizing his
face at that instant, and Rex's own head and wheel movement restarts face tracks, which
stay unrecognized for about a second. On the first question the new track left the reply
with no memory context and no recall tool; on the second the tool was offered, then
refused two seconds later (`PrivateRecallRequiresKnownAudience`) when the track restarted
again.

The state owner now remembers the last private audience that evidence supported
(`note_audience`), and `conversation_memory.subject` carries that person for up to 90
seconds (`PRIVATE_CARRY_SECONDS` in `present.py`) while nothing contradicts the same visit: the view is
empty or holds one not-yet-recognized face, and no supported voice belongs to somebody
else. Two faces at once, or a face or voice recognized as another person, end the carry
at once; a fresh recognition is then required. Exchanges archived this way record
`audience_basis: recently_recognized_person`. Carrying authorizes memory use only: it
never attributes an utterance, enrolls a voice or face, or changes the reply guard, which
already treated a renumbered track as the same visit.

Limit, accepted for this repair: someone who replaces the recognized person within 90
seconds, alone and never seen beside them, would be treated as the same visit until the
window lapses. Face evidence cannot tell that case from the owner's own face at a distance
(his track read "unrecognized" at similarity 0.33 and 0.61 in this run).

Verification: two new synthetic checks through the production Session (recall after a
restarted track, with the stored basis; the carry ending by time, company and another
recognized person). No hosted model or camera was used; real-room behaviour is untested.

Later the same day (run `be8c8d55`) the accepted limit showed up: with a guest beside the
owner, the camera sometimes held only the guest, who then inherited the owner's
recognition and was called by his name. After two faces are seen together, or somebody
else is recognized, nobody is carried for ten minutes (`COMPANY_SECONDS` in `present.py`): only direct
recognition opens private memory. Alone with one person, the 90 second carry is unchanged.

## October 11: "last night" found on the first lookup

Live: "Do you remember what I told you last night?" made the model search the words
"last night" with no time bounds. Substring matching ranked Rex's "tonight" questions
and that afternoon's chat first; the 12-row cut omitted the 7 PM remark asked about,
and Rex said the archive was undated. A topic-word follow-up ("drugs") found it.

Repair: named periods computed on the local clock, whole-period results oldest first
(up to 60), local time labels, whole-word ranking, compact attribution, and the undated
archive offered only when dated results are empty or unmatched. The compaction also
shrinks the recent-exchange context sent with ordinary replies.

Verification: `tests/test_conversation_memory.py` replays the October 11 layout with
synthetic rows and checks local-clock periods (full suite 1,103 tests, synthetic).
A paced hosted check, real model on a synthetic temporary database, asked five
"last night"/"yesterday" phrasings: the model chose the period each time and all five
replies quoted the 7 PM remark (about 31,500 input tokens per turn, 3.3-5.7 s wall
time including synthetic output). Not a live microphone or held-out accuracy test.
Private receipts: `local/validation/recall-last-night/`.
