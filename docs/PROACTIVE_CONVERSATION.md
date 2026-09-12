# Proactive conversation and situational awareness

September 12, 2026: user-approved design direction, not an implementation report.
Proactive participation belongs in the conversational core and its acceptance
work. It is designed alongside replies, identity and memory rather than postponed
as an independent entertainment layer. This documentation update authorizes no
runtime changes, new providers or hardware operation.

Today, face-arrival opportunities already use the shared Session, conversation
model and output owner. General lull opportunities, broader social awareness and
the outcome tracking proposed here are not yet implemented. See
[current face behavior](CORE_OPERATIONS.md#proactive-face-greetings).

## Purpose: delight with sensitivity

Rex's purpose is to delight, make people laugh, and help improve their mood.
Emotional awareness guides the manner and timing of that contribution. It must
not mechanically mirror someone's mood or make a subdued person get a subdued,
uninterested robot. A tired person may welcome a low-pressure joke or an amusing
observation that asks nothing of them. Short answers alone do not imply rejection.

Grief, distress, serious disclosures and explicit boundaries call for sensitivity:
acknowledge, listen, offer appropriate support, or leave space rather than inserting
a joke. Do not force cheerfulness, insist on improving someone's feelings, diagnose
an emotion from a face, or make them reassure Rex that he is not bothering them.
Use expressed meaning and conversational context; emotional interpretations remain
uncertain and revisable. Warmth and delight can include attentive companionship.

Both excessive chatter and missed welcome openings are failures. Silence is a valid
choice, not the universal optimization target. Visible presence permits considering
an invitation; it does not prove attention or oblige anyone to answer.

## What to preserve from v2

The read-only source review used v2 revision
`9806c74fd233a7118da88ca5ae55fb7450deec05`, with a clean working tree. It did not
verify live configuration or operate providers. Source pointers are in
[V2_REFERENCE.md](../V2_REFERENCE.md#proactive-behavior-review-september-12-2026).

| Source of an opportunity | Useful behavior to retain in the design |
| --- | --- |
| People arriving or returning | Natural individual/group greetings and contextual introductions |
| The ongoing exchange | Continue a thread, contribute a callback, occasionally join discussion about Rex |
| Accepted memories and plans | Relevant follow-ups, celebrations, birthdays and considerate check-ins |
| A meaningful scene change | Notice an animal arrival, a presented object, or a changed environment |
| Social signals | Acknowledge a wave or a reaction when evidence and timing support it |
| Outside information | Offer relevant, dated news or a notable weather change |
| Rex's character | A playful observation, a grounded personal aside, or a shared recollection |

These are sources of material for one conversational decision, not independent
speakers. Their inclusion here does not imply v3 has object, gesture, sound-event,
news, calendar or mood providers. Availability of material does not require using it.

V2 already lets Lean choose a cue, improvise, or pass in one generation. Preserve
that freedom, delivery-aware bookkeeping, topic deduplication and bounded pacing.
Retire the fixed-order cue ladder, separate reactive speech authorities, shared
room-wide user-energy classification, and canned pressure to explain silence.
Do not infer a target's presence from another person's recent speech or treat
facing Rex as an override of an expressed boundary.

## Shared conversational awareness

Build on the existing versioned snapshot and original exchange history. The
following are proposed context responsibilities, not a new parallel world model:

- **People and evidence:** current visible tracks, recent acoustic tracks, identity
  hypotheses, arrivals/departures, evidence age and conflicts. Preserve uncertain
  tracks without requiring names before group conversation can work.
- **The conversational floor:** who appears to be addressing whom, whether humans
  are talking to each other, unfinished thoughts, delivered unanswered questions,
  active tasks and whether Rex is participating or observing.
- **The thread:** what is being discussed, volunteered details, topic changes,
  corrections and useful unresolved points. A recent topic is not automatically
  forbidden; continuing it differs from repeating a question already answered.
- **Participant receptiveness:** explicit requests, responses to Rex's invitations,
  preferences and recent interaction evidence tied to the relevant participant or
  unresolved track. One person's short answer must not set everyone else's mood.
- **Audience and discretion:** who is present, who supplied a remembered claim,
  and whether surfacing it is appropriate for this audience. Private good news
  deserves discretion just as difficult news does.
- **Changes worth noticing:** fresh, supported observations and how they differ
  from the recent scene. Unchanged furniture is not a new event each frame.
- **Rex's contribution history:** what he actually said, which subject he raised,
  whether anyone engaged, and what was declined, interrupted or never delivered.

Code records observations, timestamps and outcomes. The conversational model owns
their semantic interpretation. Do not recreate a regex energy/addressee classifier,
infer private emotional states from appearance, or turn session judgments into
durable biography. Interpretations retain supporting source IDs and can expire or
be corrected; they never grant acoustic identity or enrollment permission.

## One opportunity and decision path

Fresh observations or an appropriate pause can create a bounded opportunity. A
timer may wake consideration, but elapsed silence is not itself a reason to speak.
Use the existing Session and sole conversational model for both replies and
proactive turns, with no fabricated human utterance.

1. Code checks that input/output is available, observations are fresh, and pacing
   and explicit boundaries permit consideration. Coalesce related changes rather
   than enqueueing a remark for every event. Ordinary human input takes priority.
2. Supply compact evidence and a small, diverse set of relevant opportunities.
   Bounded selection must not always hide later sources behind a fixed category
   order; log what was offered and omitted so starvation is observable.
3. In one generation, the model chooses a contribution and audience, an available
   typed operation, or silence. It considers whether to deepen the current thread,
   offer a playful aside, invite participation, introduce a topic, or simply wait.
   These are choices, not a mandatory plan or metadata preamble before prose.
4. Validate permissions and freshness again before execution/output. New speech,
   changed targets or a superseding conversation invalidates stale work. Preserve
   cancellation and actual delivery receipts through the existing output owner.
5. Record the outcome. Reconsider later on meaningful change or a bounded future
   opportunity; do not regenerate rejected material in a tight retry loop.

No additional mandatory classifier/planner generation is added to ordinary replies.
Any retained semantic state should come from the same conversational owner through
explicit validated proposals when needed, not a continuous second brain. Proposed
news refreshes or heavier perception run only as declared, budgeted capabilities;
they cannot block listening or speak independently. No new resident model is implied.

Keep attempt/cost pacing separate from delivery and topic history. Considering an
opportunity may consume an attempt budget; an unheard line cannot count as an
ignored question or completed greeting. Track considered/declined, spoken/answered,
spoken/unanswered, dismissed, cancelled and failed outcomes separately. A response
counts as engagement only when its meaning connects it to the invitation; nearby
human speech or a polite acknowledgment must not blindly reset all limits.

Repeated unanswered invitations reduce initiative without compulsory disengagement
questions. Renewed engagement or a meaningful new event can permit another opening
within explicit boundaries and rate limits. Avoid permanent muting from one terse
answer. Track repeated subjects as well as wording, and keep this ledger bounded
with correction/forgetting support. Long-term personal preferences require the
normal attributed-memory validation, not automatic optimization of a mood score.

## Behavior examples

| Situation | Intended judgment |
| --- | --- |
| A visitor arrives before speaking | Consider a warm greeting or invitation; do not require speech first |
| Bret is quiet after saying he had a tiring day | A short, low-pressure amusing aside may be welcome; do not mechanically match low energy |
| Someone describes a bereavement | Give the disclosure space and respond sensitively; no automatic joke or mood-lifting attempt |
| Bret pauses mid-story | Wait for the thought; elapsed time alone does not open a new topic |
| Bret asks Jeff a question | Leave Jeff the floor, even if Rex knows an answer |
| Bret explains Rex to Jeff | Usually listen; an occasional relevant quip can fit after the explanation ends |
| A camera troubleshooting topic reaches a natural lull | Continue that thread when useful; avoid a disconnected weekend interview |
| Jeff is quietly listening to the group | Invite him only if there is a natural topical opening; no equal-speaking quota or silence accusation |
| One person gives a short answer while another remains engaged | Preserve individual context; do not dampen the entire room |
| Rex's last two invitations receive no engagement | Back off without demanding reassurance; remain available for a new opening |
| Someone presents an unfamiliar object | Consider one grounded observation/question; uncertainty must survive into wording |
| A relevant news story is available | Offer one sourced, dated interesting point if it fits; expand when invited |
| A private remembered milestone concerns someone in a group | Withhold inappropriate disclosure; a celebration is not automatically public |
| A greeting was cancelled before audio began | Do not label the visitor unresponsive or claim Rex greeted them |

Examples illustrate judgment, not prescribed dialogue or deterministic mappings.

## Development and acceptance

Develop this policy together with core replies, attribution, memory and output.
First exercise arrivals, meaningful lulls, human-to-human conversation, emotional
sensitivity and initiative outcomes using existing evidence and providers. Then
integrate accepted-memory follow-ups through the same policy. News, richer vision,
sound events and physical reactions remain separate capability implementations;
their social contracts are designed here before adding their resource costs.

Use the production Session in synthetic scenarios and timestamped replay; do not
build a separate mock proactive brain. Frozen identity replay must remain free of
training/memory mutations. Add explicit proactive evaluation fixtures without
silently enabling unsolicited output in the existing acoustic evaluation mode.

The [acceptance scenarios](INITIAL_TEST_PROGRAM.md#proactive-conversation-acceptance)
must assess delight and useful initiative alongside restraint. Real people should
judge welcome contributions, humor, warmth and social timing; detector labels,
synthetic tests and an LLM's self-rating cannot establish improved mood.
