# Proactive conversation and situational awareness

**October 1 replacement:** [persistent narrative and conversation plans](AGENCY_IMPLEMENTATION_PLAN.md)
now own proactive progression. Stronger private planning and cheaper step execution
replace optional silence voting and general self-scheduling timers. The dated design
below remains background for shared social state and evidence boundaries; it does not
describe the current scheduler. Live acceptance of the replacement is pending.

September 12, 2026: user-approved design direction with the agreed proactive software stages implemented.
Proactive participation belongs in the conversational core and its acceptance
work. It is designed alongside replies, identity and memory rather than postponed
as an independent entertainment layer. The user subsequently authorized finishing
the proactive software, including observation providers, before live testing.
Hardware operation remains a separate acceptance step.

Face arrivals and visible-group lull opportunities now use the shared Session,
conversation model and output owner. The first slice includes attempt/delivery
history and explicit session quiet/resume. The second slice adds bounded, source-linked
invitation feedback, topic boundaries and human-floor state. These are session
interpretations with speaker uncertainty, not verified emotions or durable preferences.
The third slice adds dated accepted-memory candidates and actual-delivery receipts.
News/weather and shared-camera scene snapshots are also implemented through the
same opportunity path. Broader outcome interpretation and live social acceptance
remain design targets. See
[implementation stages](PROACTIVE_IMPLEMENTATION.md) and
[current operation](CORE_OPERATIONS.md#conversational-lulls-and-initiative-control).

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

Owner ruling, November 3: Rex may see what a face shows (a smile at his joke, surprise, a frown) and play off
it; see [what faces show](FACE_EXPRESSIONS.md). A look is what a face shows, not a diagnosis, and words outrank it.

Both excessive chatter and missed welcome openings are failures. Silence is a valid
choice, not the universal optimization target. Visible presence permits considering
an invitation; it does not prove attention or oblige anyone to answer.

Rex is normally started deliberately for social demonstrations with friends.
Consider a welcome opening after a genuine lull. The October 1 user direction
rejects both a visible-person prerequisite and the fixed 20/60-second post-speech
floors. The [first agency increment](AGENCY_IMPLEMENTATION_PLAN.md) replaces them
with model-selected reconsideration, bounded working interests and one shared call
budget. A default eight-second seed lull remains; subsequent timer-based decisions
are explicitly requested by the model. Direct human input retains priority, as do
unfinished thoughts, human-to-human questions and explicit boundaries.

### September 30: contextual teasing

The master `PERSONA` in `src/r3x/adapters/hosted.py` now explicitly guides friendly
teasing: react to a specific shared moment, make one affectionate jab, and leave
room for a comeback. It discourages generic roast routines, invented personal
flaws, escalating insults and stale callbacks. Useful answers and sincere interest
remain welcome without a punchline; topic changes, discomfort and serious moments
end the bit. This is prompt guidance in the existing conversation owner.

Live humor acceptance remains pending. Review a harmless mishap, a playful comeback,
a new topic, a request to stop teasing and a serious disclosure in sequence. Judge
whether the joke fits the actual words, feels affectionate, varies naturally and
stops appropriately. Validation: all 796 synthetic blocked-I/O contract checks
pass; no hosted model or hardware was used. These checks cannot establish humor
quality or live conversational acceptance.

Hosted model comparison, September 30: `tools/compare_character_models.py` ran
seven fictional multi-turn scenarios twice per model through production Session,
with the unchanged persona, low reasoning, temporary stores and synthetic output.
Both GPT-5.4-mini and GPT-6 Luna completed all 38 evaluated turns (39 API calls each).
Estimated token charges including cache writes were $0.1512 and $0.0356;
median synthetic-output turn completion was 1.68 and 1.71 seconds respectively.
These timings exclude real ASR/TTS. Both recovered the seeded instrument correction;
mini reintroduced the dropped droid topic in 2/2 runs and Luna in 1/2. Qualitative
review found more direct requested teasing and steadier home-character framing from
Luna, but generic praise and weak jokes remain in both. This small text comparison
does not establish a humor winner or live acceptance; the live model remained mini
at that stage.
Full dialogues, prompt snapshot, usage and measurements are in ignored
`local/validation/character-models-d573545385ff4affa7f1624a2870b432/`.

October 1: the [character/model evaluation](CHARACTER_EVALUATION.md) supersedes that
initial choice. Rex now uses Luna with a stronger character prompt, measured across
22 paired conversations and a further mini/Luna cross-check. The assistant review
favored the revision 9 times, the baseline 5, with 8 ties. This is a modest text
improvement; live humor acceptance remains open.

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
speakers. The implemented inputs and their limits are documented in
[observation sources](OBSERVATION_SOURCES.md). Object/animal/gesture interpretation
uses small scene images in the existing model; no sound-event or mood classifier
is implemented. Availability of material does not require using it.

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
infer private emotional states from appearance (a visible look is evidence of the look only), or turn session
judgments into durable biography. Interpretations retain supporting source IDs and can expire or
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
integrate accepted-memory follow-ups through the same policy. News/weather and small shared-camera snapshots are now implemented; their resource
bounds and remaining recognition limitations are documented. Sound-event and
actuator implementations remain in the wider robot roadmap.

Use the production Session in synthetic scenarios and timestamped replay; do not
build a separate mock proactive brain. Frozen identity replay must remain free of
training/memory mutations. Add explicit proactive evaluation fixtures without
silently enabling unsolicited output in the existing acoustic evaluation mode.

The [acceptance scenarios](INITIAL_TEST_PROGRAM.md#proactive-conversation-acceptance)
must assess delight and useful initiative alongside restraint. Real people should
judge welcome contributions, humor, warmth and social timing; detector labels,
synthetic tests and an LLM's self-rating cannot establish improved mood.

## Intentional attention

The user also wants Rex to choose when to look away or attend to his surroundings,
rather than always fixating on faces. See the [attention implementation plan](ATTENTION_IMPLEMENTATION.md).
Its first two simulation-only slices implement attention choices, grounded synthetic
visual targets and post-movement evidence through the same conversational owner.
Coordinated before/after-speech gestures are also implemented in simulation, with
separate motion and audio receipts. Physical target-aware motion remains planned; no physical
capability is enabled. Attention shifts must not imply boredom, lost identity or
rejection of a quiet participant.

Rich hosted image descriptions remain a requested avenue alongside spatial object
tracks. The [attention plan](ATTENTION_IMPLEMENTATION.md#rich-visual-understanding-requested-design-addition)
now covers visual queries, selective descriptive refresh, observation memory and
model-owned repetition judgment. Observing a stable appearance detail does not
require commenting on it; prior observations and actual remarks provide context.
Inspection-based descriptive memory and cache-use/correction tools are now
implemented; live acquisition and real-model repetition evaluation remain pending.

## Future idea: motion and new arrivals as attention cues

**September 17 user direction — document only; do not implement yet.**

Rex should feel alive: attentive, curious, proactive and reactive in ways that feel
natural for a person or animal. Motion and newly appearing subjects deserve more
attention than an unchanged scene. This is a design inspiration, not a requirement
to comment on every movement or mechanically imitate animal behavior.

Example: a dog enters the view after being absent. Supply that possible arrival to
the conversational LLM, together with recent visibility, detection uncertainty,
current conversation and relevant photographic memories. The LLM could choose to
compare reference photos, recognize a familiar dog, ask about an unfamiliar dog,
notice silently, or keep listening. The observation offers an opportunity; it does
not directly trigger speech, a cloud call, enrollment or movement.

Future work should distinguish:

- **Subject motion or arrival:** something moves independently or becomes newly
  visible, versus an already familiar stationary subject.
- **Rex's own head/base motion:** a camera turn can move the entire image or reveal
  a previously unseen area. Use available motion telemetry and/or estimated camera
  motion, plus settling and continuity evidence, to avoid treating every pan as a
  room full of new arrivals. Preserve uncertainty when the cause is unknown.
- **Detection flicker and reappearance:** brief missed detections, occlusion,
  lighting changes and track resets do not prove a new animal arrived. Distinguish
  newly visible from newly present in the room, and both from a new individual.

Candidate evidence could include source frames/timestamps, prior absence duration,
track continuity, independent motion, camera-motion/settling status, possible
identity matches and whether Rex already noticed or reacted to this subject.
Photographic memory supplies comparison evidence rather than a guaranteed identity.
Motion must not become mouth-motion evidence for speaker attribution or enrollment.

Preserve the single conversation owner and existing human-floor, quiet, repetition
and resource budgets. A quick silent noticing event and a later conversational
reaction are different choices. Evaluate whether Rex feels attentive without being
jumpy, repetitive or interruptive. Test stationary-camera arrivals, head pans over
unchanged subjects, brief occlusion, two moving dogs and ongoing human conversation;
measure false arrivals, missed arrivals, reaction timing and processing cost.

**Existing foundation versus future work:** paced object detection, stable-detection
scene opportunities and a named photo gallery now exist. This proposal adds richer
motion/novelty evidence and camera-motion-aware interpretation; it is not implemented
or validated by those earlier features. Physical reactive gaze remains separate
future hardware work. This note authorizes no code or hardware operation.


## Future direction: interest-aware banter and news

September 17 owner request: progressively tailor idle conversation and news to
people's learned interests, alongside games, impressions and exploration. General
news/weather and the shared proactive decision path already exist; dedicated
interest-aware retrieval/ranking is future work. Preserve source-linked interests,
audience awareness, correction, topic variety, heard-remark deduplication and bounded
API spending. See the [companion-feature roadmap](FUTURE_FEATURES.md) for scope.
This is documentation, not an instruction to implement or start new background work.

## “Do you ever think” (implemented October 23)

**Implemented October 23, 2026** (`src/r3x/hypotheticals.py`). Owner rulings: the long lull is
45 seconds with nobody talking, Rex included (his silent moves and glances do not count), with
someone in view; one question per quiet stretch, the next only after someone has spoken. The
question is a beat inserted ahead of his current goal's next one, written by the beat's single
generation; only questions actually heard are recorded and offered back as spent; a reply soon
after sees `your_hypothetical` and may play along. Evidence: [implementation status](IMPLEMENTATION_STATUS.md).
The September 18 request below remains the design.

**September 18, 2026 user request.**

After a long, genuine lull, Rex may spark conversation with a short, LLM-generated
thought experiment or imaginative “what if” question. The aim is shared curiosity,
humor and an interesting exchange, rather than a quiz or a monologue.

Owner example:

> “Do you ever think, what would happen if everyone won the lottery on the same day?”

Other illustrative possibilities (not a canned runtime phrase bank):

- “Do you ever think about what the world would be like if nobody needed sleep?”
- “What if dogs could talk, but only for five minutes a day?”

### Intended behavior

- Treat a long lull as an opportunity for the existing conversational owner to
  consider this contribution, not an automatic timer-to-speech rule. The threshold
  remains to be chosen and evaluated; do not equate it with the current eight-second
  general-lull setting or add an unused configuration option now.
- Generate the question in the shared proactive decision call. No separate idea
  generator, mandatory classifier, continuous polling of the LLM or extra routine
  reply call. Apply the existing participation and API-spending budgets.
- Consider the audience, recent conversation, accepted interests and the mood of
  the exchange. Topics may be personalized when supported, but general curiosity
  must remain possible without a recognized person or a known interest profile.
- Respect quiet requests, unfinished thoughts, human-to-human conversation and
  sensitive moments. A long silence alone does not establish boredom or permission
  to demand engagement. The model can choose another contribution or remain silent.
- Offer one concise question and leave space for an answer. If someone engages,
  explore their response through normal conversation. Do not immediately answer
  Rex's own question, turn it into a lecture, or repeatedly prod after no response.
- Vary subjects and phrasing; “Do you ever think” names the feature, not a required
  opening for every prompt. Track topics actually spoken, not just generated text,
  so paraphrases of the same hypothetical do not masquerade as new material.
- Keep imaginary premises clearly hypothetical. Neither a fabricated scenario nor
  a playful answer becomes a real-world event or an accepted personal fact.

### Future acceptance

Exercise long versus brief lulls, welcomed responses, no response, quiet mode,
resumed speech before playback, sensitive conversations and repeat sessions.
Evaluate whether questions are enjoyable, varied and appropriately timed; verify
that silence/declines back off, canceled speech is not counted as heard, and
question generation stays within the shared proactive call/cost limits.


## Greeting-led conversational intentions (September 18)

An optional session intention can now accompany a greeting, name invitation or
eligible remembered check-in. The same owner may revise or abandon it on a later
turn; ordinary replies need no planning annotation. There is one bounded intention
for the visit, not competing per-person agendas or a fixed onboarding interview.
Audience changes, quiet/floor boundaries, correction, forgetting and actual delivery
constrain its use. See [implementation and evaluation](CONVERSATIONAL_INTENT_PLAN.md).
The subsequently requested continuation behavior now reconsiders an active plan
across continuing lulls, with attempt backoff of 8/15/30/60 seconds after attempts
finish, additionally bounded by the audible-speech floors above. The existing
idle worker offers these opportunities; there is no separate planner or worker.
Quiet, pending audible questions, human-floor holds, audience freshness, expiry and
the shared six-attempt/five-minute budget still constrain it. The model sees prior
deliveries and should advance an interest with contributions that need no response,
rather than repeat greetings or interview questions. Social timing remains unvalidated.


## October 1: first agency increment

The [agency implementation plan](AGENCY_IMPLEMENTATION_PLAN.md) records the user’s
expanded direction and first-increment implementation. Generic initiative no longer
requires camera presence. Working interests and explicit general/audience scope
extend the existing intention record; one model-selected reconsideration request
replaces automatic continuing-lull backoff and fixed audible floors. Physical
attention, motion-aware repetition handling and invitation-lapse behavior remain
later increments. Dated timing descriptions above retain historical evidence;
[NEXT_CONVERSATION_SCHEDULING.md](NEXT_CONVERSATION_SCHEDULING.md) is the current
scheduling contract.


## October 1 purposeful physical attention

[Agency increment 2](AGENCY_IMPLEMENTATION_PLAN.md#increment-2-implementation--october-1-2026)
connects silent robot-relative looks and finite before/after-speech gestures to the
existing servo owner. No visible person is required; human-floor/quiet constraints
remain. Turning away/back preserves bounded view and general observation history;
a returning view alone does not generate another ambient scene cue. The model can
request reconsideration, inspect fresh settled evidence, and finish silently.
Physical/social acceptance and calibrated object-targeted gaze remain pending.


### October 1 invitation lifecycle and agency completion

The [third agency increment](AGENCY_IMPLEMENTATION_PLAN.md#increment-3-implementation--october-1-2026)
adds optional source/revision-validated social invitations on existing tools, audible
commit, model-selected review through the single scheduler, and explicit closure with
bounded heard history. Elapsed time is never an answer or rejection. Human floor,
quiet, identity clarification and private audience boundaries remain in force.
The shared prompt supports specific character thoughts, continued interests, finite
activities and silence without a forced question or extra planner call.
Synthetic production-path checks and the [current suite result](IMPLEMENTATION_STATUS.md)
cover the software; [semantic/live acceptance](AGENCY_ACCEPTANCE.md) remains unmeasured.
