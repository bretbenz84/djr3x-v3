# Current implementation status

Updated October 25, 2026. This is the consolidated
checklist for current scope.
The linked plans retain dated implementation history and test results. An earlier
slice's limitations or test count do not describe the final implementation.
Documentation does not authorize new hardware operation or feature expansion.

## October 14 improvement plan

The owner approved a six-step plan after an architecture review: one shared picture of the
present, model-set wake-ups, short-term memory of space, humor, and faster replies, with a
feature freeze until steps 1–3 land. Progress and evidence: [improvement plan](IMPROVEMENT_PLAN.md).
Step 0 adds `tools/baseline_report.py` and the October 5–13 baseline (median reply wait
4.99 s; 32 new face track IDs per minute; 6 of 8 arrival greetings re-greeted someone seen
under two minutes earlier). Steps 1–5 landed the same day, all **not yet run on the robot**:
one picture of the present with time in words (`present.py`), beats that wake on conditions
Rex chose (`wait_for`, no easing ladder), a minute's memory of the floor for driving
(`local_map.py`: doubtful readings rechecked, remembered ways round), teasing everyone when it
fits with a joke record and a writer's room (`jokes.py`, `note_bit`), and reply-tool words spoken
as they stream (`tool_text.py`, about 0.9 s sooner to the first phrase in a paid probe).
1178 blocked-I/O checks pass.

## October 25 run: frozen servos and the first impression's stall — October 26

Session `c5ad0deb`: five minutes in every servo froze until shutdown and the park was refused.
Not the board (it answered at once, no error flags, still holding the run's last pulses); loading
the impression voice held the interpreter 0.43 s, a servo send reported a timeout it had not
had, and that locked every servo out. A send timeout now asks the board, and if it answers Rex
holds the moving joints and carries on ([servo integration](SERVO_INTEGRATION.md)). The voice's
text tokenizer, the 0.4 s hold, is built at start before the microphone opens; the first
impression's load now holds at most 29 ms ([impressions](IMPERSONATION.md)). Synthetic servo
checks; real-model timing on this Mac; not yet run on the robot. 1640 of 1641 blocked-I/O checks
pass; `test_sentry` fails the same way without this change.

## October 25 run: facts refused, and the refusals spoken

Session `a0afcf85` (16 minutes, real hardware, the owner alone). Owner: "He often refuses to save memories
and tells me so... Rex should be remembering story's and facts he's told about people and never talk about
failing to save it." Two things he said about himself were refused while Rex's motors masked his voice and
became "I couldn't save that detail"; both tellings of how Toby came home were never saved; "My sister's name
is Jennifer" greeted the absent sister. Root cause: a faint one-frame "face" at the edge of the view counted
as ten minutes of company, so his voice was never carried through motion noise that evening. Repairs
([conversation memory](CONVERSATION_MEMORY.md#october-25-facts-are-kept-and-a-failed-save-is-never-talked-about)):
one-frame faces are not company; a fact said while the voice is unconfirmed is kept as a candidate fact about
the person he is talking with and used with them; a refused save is never mentioned; stories and a pet's
history are saved; someone named in a story is a relationship fact, not an introduction. A paid probe on
fictional input saved 13 of 15 expected lines (7 of 15 with the old persona) and never mentioned a refused
save. Synthetic voices and faces for the state rules; **not yet run on the robot**. 1573 blocked-I/O checks
pass (5 skipped).

Stops missed during the drive to the door ("an ongoing problem"): the board turns everything down
under motor noise, and a faint "stop" is heard as "Oh." or "Ah.". While the wheels run a quieter sound
now counts (.3), an unconfident lone "Whoa" no longer halts, and the owner's `stop.onnx` listens in the
sound itself. On saved September robot audio stops heard rose only from 28 to 29 of 60 and false halts
fell from 3 to 1; whether the board suppresses the voice needs a live recorded test
([drive base](DRIVE_BASE_INTEGRATION.md#october-25-stops-still-missed-while-rolling)); 1584 blocked-I/O
checks pass (5 skipped), 11 new in `tests/test_keywords.py`. Not repaired: three
of Rex's own beats changed the subject to houseplants, pizza and mugs right after a painful story ("Why
did you just talk about mugs?"). A name-only "Jennifer" record from the mistaken introduction remains in
the database for the owner to remove.

## Celebrity callouts and body jokes — October 25

Owner request: V2's celebrity callouts (Jeff Benziger of History Hunters fame, T'Joy the world's greatest
hair stylist, PJ the major Kings fan, JT the volleyball celebrity) "made people in the room feel special",
but V2 picked from fixed lines keyed to name spellings. Now they are added by voice through one tool,
`callout` (`callouts.py`), and the reply model writes the words each time.

- **Adding one.** Anyone can ask, about themself, someone else or a saved pet ("PJ is a huge Kings fan,
  treat him like Kings royalty whenever he shows up"). It is filed on the person's record by id, so any of
  their names works; someone not met yet gets a name-only record, and their first introduction is the
  entrance. It keeps what they are famous for, up to six angles and what is off limits, and who asked. It
  is Rex's material, never a fact about them (V2 had stored "JT, major volleyball celebrity" as JT's
  interest).
- **Changing and ending one.** "Also work in the cowbells" adds an angle; "no more cowbell jokes" makes it
  off limits and drops the angle. Asked to stop, he switches it off and keeps it ("bring it back" turns it on
  again); asked never to call them that again, he deletes it. A groan is the bit working, not a stop.
  Forgetting a person removes callouts about them and the ones they added; forgetting a pet removes its own.
- **Using one.** The reply context lists the callouts of people in view, speaking or talked about in the
  last ten minutes, and of pets named lately or walking in. An arriving person (by face) or a pet walking in
  gets an entrance in place of the usual hello, at most every three hours; a callback is offered at most every
  fifteen minutes, counted from any heard callout line or change. Heard lines are matched to callouts by their
  distinctive words (Rex's own output only) and the last three are shown so the wording changes. The writer's
  room gets callouts that are due a callback. None during sentry mode. A callout's angles may go past his usual
  joke limits; its off limits always hold.
- **Bodies are fair game** (owner, same day: "I don't know why bodies are off limits"). The persona, the joke
  writer, the goal planner and impressions no longer forbid jokes about people's bodies; health, intelligence,
  identity, religion, politics, money and family keep their existing limits.

Evidence: 16 checks in `tests/test_callouts.py` (made-up people, temporary stores, fake clocks, the production
Session for an arrival). Paid probe `tools/check_callouts.py --live` (fictional input, real reply model, an unknown
voice, synthetic speech): the first run proposed the right action on 14 of 14 turns, but "also work in the cowbells"
and "PJ says never call him that again" were refused because PJ was out of view; after showing callouts of people
talked about lately, "bring the Kings bit back" was refused because a stopped callout was not listed; with stopped
ones listed, the third run applied 14 of 14 (the speaker's own callout cannot be filed for a voice Rex does not
know, as expected). PJ's arrival and Jeff's introduction were heard as entrances. About 50 paid requests over the
three runs; cost not measured. The "never recite off limits" wording was added after the last run and is untested.
1635 blocked-I/O checks pass (5 skipped). **Not yet run on the robot**; whether the entrances are funny is for the
owner's ear.

## Speaking modes — October 25

Owner requests: port V2's pride mode ("queeny mode") and its African American Vernacular English mode
("homie mode"), each in its own ElevenLabs voice; then eleven accents (British, French, Italian, German,
Spanish, Russian, Scandinavian, Japanese, Chinese, Nigerian, Indian) and a pirate, each in its own voice,
in English and culturally fitting, and now and then a single line in one when the talk suggests it;
then Arabic, Filipino, Vietnamese, Brazilian Portuguese and Dutch accents and a DJ; then Irish and Scottish.

- Asked about his own sexuality ("are you gay?", "do you like men?"), Rex says a proud yes and goes full
  camp ("sis", "yasss", tea and drama) for ten minutes. Greeted "what's up my homie", he answers in AAVE
  for ten minutes, spoken for real, never as a mocking impression.
- Asked for an accent ("do a British accent", "talk like you're from Lagos"), he speaks English as
  someone from there does (idiom, rhythm, a native word or two; never a caricature, broken English or a
  joke at the people's expense) for ten minutes. "Talk like a pirate" is a mode of its own (arr, matey),
  and so is "talk like a DJ" (hype patter), which only ever answers a request: he never chooses a DJ
  line himself, since the owner wants no DJ or music talk unprompted, and he still cannot play music.
- One mode at a time (owner, the same evening): asked to be someone else, he switches to it and the
  mode before ends, pride and AAVE included, and the switching line already drops the old mode's
  words; asked to talk some way he has no mode for ("like a cowboy"), he switches the mode off and
  does it in his own voice.
- Asking or greeting again starts the ten minutes over; "stand down pride mode", "talk normal again" or
  "drop the accent" ends one. Sentry mode clears them all and refuses them while it guards.
- One line: asked ("say that like a German") he says just that line in the accent's voice, in English
  ("Ja, the bus is late again. Naturally.", never translated), and is himself on the next turn. On his own,
  when the talk brings up a place or people (a Paris trip, sushi, a German car), he may say one reply or
  one lull line in that place's accent: never while a mode is on, never two within five minutes, never in
  a serious moment (the last two are the model's judgment; the five minutes are code).
- How: the reply model calls `voice_mode` (on, off or line; V2 used a regex), and its line is spoken
  once the switch succeeds, in one model call. While a mode is on its directive rides last on every
  reply request, his own beats included. Each mode's ElevenLabs voice is in `VOICES` in
  `src/r3x/voice_modes.py` (the owner's choice: in the repository, not in local secrets).
- Pride also brings V2's body: a rainbow mouth and chest (the boards' `PRIDE:1`, renewed every 3 s), a
  one-shot flourish as it comes on (head fully down, visor wide open, hero hand at the end of its
  travel, held a second, then back), hero elbow and hand swinging about twice as wide and often while
  he talks, and the throttle wrist curled ([V2 extraction](../V2_REFERENCE.md#pride-mode-and-aave-mode--october-25-2026)).

Evidence:
- 24 synthetic checks in `tests/test_voice_modes.py` (1612 blocked-I/O checks pass, 5 skipped): state, request, voice choice, one-line lines on
  replies and lulls, the five-minute limit, the flourish through the output owner on a pulse port,
  talking lanes and clearance, the light lease.
- One short line was synthesized in each of the twenty-two voices; all work with `eleven_v4_turbo`.
- The first Russian and Italian voices (remixes designed in ElevenLabs) sounded British on Rex's
  `eleven_v4_turbo`, though the log showed both modes switched on and their voices used. The owner
  replaced the two voices; the new ones still sounded British on v4 and right on Eleven v3 in
  ElevenLabs' web interface, so those two modes moved to `eleven_v3`. The owner then chose by ear
  (`MODELS` in `voice_modes.py`): Italian, Russian, Japanese, Chinese, Nigerian, Arabic, Filipino,
  Vietnamese, Brazilian, Scottish and the pirate on v3 (first audio median 0.76 s, at most 0.94 s, over
  all 22 voices), the rest on v4 Turbo with Rex's own voice (0.20 s). Rex's own voice (now `REX_VOICE` in the repository, no longer in local secrets) stays on
  v4 Turbo.
- `tools/check_voice_modes.py --live` (paid, fictional input, seven full runs and five short ones).
  Wording repairs on the way:
  - The first wording made him repeat its example lines ("Yasss, sis! Gay and fabulous, obviously."
    three times).
  - "My homie Marcus is coming over" and "I was out with my homeboy all day" switched AAVE on.
  - With British on, he asked for a needless British line.
  - After a one-line Russian request he did Russian again on the next turn unasked, and asked lines
    came out translated ("Der Zug ist verspätet", "J'adore cette chanson"). Afterwards 6 of 6
    follow-ups were his own voice and 4 of 4 lines English with a native touch.
  - Unaddressed statements ("We're flying to Paris next week") got no reply at all, an existing
    behaviour this does not change; the probe now addresses Rex. Two statements addressed to
    him ("Rex, we're flying to Paris next week") were still left unanswered as needing no reply.
  - Switching from pride, the switching line kept camp words ("Oh, absolutely, sis... darling" in the
    British voice; "Arrr, sis... Yasss, queen" as a pirate). With the rule in the mode's own
    directive, 3 of 4 switches were clean and the fourth only joked about the old mode.
- Last full run: 36 of 36 turns right. Pride then British left only British on, AAVE then Paris only
  French, German then pirate only the pirate, and "talk like a cowboy" switched the pirate off with
  cowboy talk in his own voice; nothing for "is my uncle gay" or a homeboy in passing. Lines were
  fresh, and his grandmother's death got a kind answer in his own voice. He chose an accent line
  himself once in the 33-turn run before (French about Paris and Rome) and not at all in this one.
- The added modes: 12 of 12 turns right (Dutch on and in register, an Arabic line asked for and then
  his own voice, the DJ on, in patter and off, Filipino to Vietnamese to Brazilian with only the newest
  on, nothing for "my cousin is a DJ in Las Vegas").

**Not yet run on the robot**: the voices in conversation, the flourish, hand swings and throttle curl,
and the rainbow on both boards are unseen; the dashboard does not mirror the rainbow. How often he
chooses an accent line in real conversation, and on his own goal beats, is unmeasured.

## Out of credit — October 25

Owner: "Can the rex program tell me when credits have run out. With a pre-written TTS." The OpenAI account
ran dry that evening and every reply failed in silence (`credit_balance_exhausted` inside the response
stream). Now that error, or a refused request with `insufficient_quota` (an HTTP 429 Rex used to retry
forever), ends the turn as out of credit, and so does ElevenLabs' `quota_exceeded`. When it happens on
a turn where someone spoke to him, he plays a saved line in his own voice saying which account to top
up: made once and kept with the startup lines (made in his own voice whatever speaking mode is on, a
fix for those lines too), so it needs no paid call and still plays when the voice service is the one
out. At most every ten minutes per service; his own turns never announce it. Both lines were saved to
the robot's line cache the same evening. Evidence: 6 synthetic checks in `tests/test_credit_notice.py`
(fake responses shaped like the October 25 trace); not yet heard on the robot during a real outage.

## "Hey Rex" over his voice — October 25

Owner: "Hey DJ Rex" or "Hey Rex" should interrupt his speech so he can talk over him. V2's two wake-word
models run beside `stop.onnx` on every frame; while Rex speaks and the wheels are still, either one cuts his
line within one playback callback, holds his own beats 10 s and leaves goals, questions and the wheels
alone; what follows is heard as the person's words, not echo. While driving it waits (only stop or a
warning halts a drive). Real models on saved audio: "Hey Rex" .89-1.00 on all seven saved calls, never on
his own voice; "Hey DJ Rex" was never recorded. 1587 blocked-I/O checks pass (5 skipped). **Not yet run on
the robot** ([drive base](DRIVE_BASE_INTEGRATION.md#october-25-hey-rex-over-his-voice)).

## October 24 run: the introduced guest still went unlearned

Session `1bfce8af` (11 minutes, real hardware; the owner, Jeremy, later Joy and the dog) was the first
live run of the October 20 repair. "This is Jeremy Thomas" opened the window for his face and voice, and
nothing was saved. Repairs ([introduction recovery](INTRODUCTION_RECOVERY.md#october-24-the-first-live-run)):
- **His voice sounded too much like the owner's saved voice.** Jeremy's voice formed its own track (17
  clips, 49 voiced seconds); his clips scored .16–.46 against the owner's profile (also on October 20) and
  the first clean one .37, and the link refused anything at .30 or more. The ceiling is now .45 on at least
  two pooled clips (`NEWCOMER_MIN_CLIPS`): the owner's clips score .62–.76 against the rest of his voice and
  were named at .52–.68 live. His off moments come lower (a tired line made its own track at .30 on October
  19, taken for him by presence), so a voice presence or the carry took for someone is never the guest's.
- **A blurry glimpse became the face reference.** No face was pointed at, so the first unknown face after
  the introduction, from any view, set the reference: often the owner's face in a blurry frame on a fresh
  camera track, or Jeremy turned aside. Four minutes of clear views of Jeremy (up to five a turn) never
  matched it, silently. Only clear views count now and only a face the introduction pointed at is a
  reference; otherwise the first clear views start the pool, and a lone earlier view that nothing since
  matches gives way.
- **Nothing said why.** `newcomer_voice_waiting` and `newcomer_face_waiting` log once why a voice or face
  waits (close to a saved voice, taken for someone, one clip so far, two unknown voices; unlike the
  introduced face, more than one unknown face, an earlier view replaced).

Still open: until a guest's voice is saved, presence inference and the carry treat a voice .30 or more like
the owner's as his (Jeremy reached .46), relying on their face and company guards. Synthetic voices and
faces, thresholds from the run's logged real-model scores; **not yet run on the robot**. 1561
blocked-I/O checks pass (5 skipped).

## Curious about people and pets — October 24

Owner, after a review of the ten runs from October 22 to 24: "Rex appears to have no curiosity. Most things are
statements or jokes about what he sees... He almost never asks me personal questions... I've never heard Rex
'tell me about when you first got your pet Max'. True friends ask to know more, then remember it and use that
info for future conversations." And, ruling on which questions: "when an LLM writes a response it often includes
a follow up question. Those were what were annoying. I'm talking about proactive curiosity about people and
pets." In those runs 4 of 143 lines asked about the owner's life, all four dog arrivals got "is he behaving?",
nothing new about the owner was saved, the furniture or decor came up in 25 lines, 23 lines turned his day into
an office ("chief compliance officer", "logistics department") and 16 reached for the piloting record. The
causes were in the instructions: "no interview question" on every reply, at most one question per plan, and
plans written "without interrogating him". Owner defaults: work, partner, family, childhood, friends, pets, trips
and hobbies are fine to ask about; health, money and politics only when they bring them up; guests get the same
curiosity, lighter at first.
- **What he does not know yet** (`curiosity.py`). With a known person to talk with privately, replies and the
  planner get up to eight topics: ten about the person (their work, where they grew up, how they met their
  partner...), the story behind each saved interest, and six about each saved pet (how Max came home, his worst
  crime...), never-asked ones first, the pets first when one is in view. The model judges whether saved facts or
  pet notes already answer one.
- **Asked on his own turns.** The persona, the per-reply reminder (now `VOICE_REMINDER`) and the beat note put
  his questions on his own initiative: a lull, being handed the floor, a pet walking in, something in front of
  him with a story. A reply to what someone said ends without a tacked-on follow-up. Plans hold two or three
  question beats, each followed by a beat that reacts to the answer without another question.
- **Spent, then open.** A line that asks a topic names it (`asked_about` on the reply tools, `curious_about` on a
  plan beat); only a question actually heard is recorded (`curiosity_asked`: a topic and the person or pet, no
  words), and it is not offered again for three days. For three minutes the reply sees `your_question`: react and
  save the answer (a fact about them, or a note on the pet). Forgetting a person cascades; forgetting a pet
  deletes its rows. A topic that was not offered is traced (`curiosity_topic_not_offered`) and not recorded.
- **A pet walking in** gets a warm hello by name, then his question about it as the next beat
  (`PET_QUESTION_BEAT`): the hello comes from a photo look whose follow-up has no tools to name a topic.
- **People first, then closer.** With anyone here, plain furniture, decor and the room are never a goal's
  subject; an investigate goal with people present asks them the thing's story first. A person farther than
  2.5 m (`FAR_PERSON_M`, from the face's rough range) is marked far for the planner, whose goal then rolls over to
  them first and stops about 1.2 m (4 ft) away.
- **Spent joke shapes.** The office joke is spent once his own lines use it, the piloting record after one a
  visit (`jokes.spent_shapes`, his own heard lines only); replies, the planner and the joke writer see them.

- **No question twice.** A planned question whose topic is already spent is let go before any model call
  (`curiosity_already_asked`); while an answer is open, the per-reply reminder adds "save what they told you and
  react without a new question".

Checks: 12 new blocked-I/O checks in `tests/test_curiosity.py` (offered topics per person and pet, a heard
question spent and open for its answer, a topic not offered, forgetting a person and a pet, a planned question
beat and the far person, a spent question let go, a pet walking in, the joke shapes); the full suite passes.
Paid probe `tools/check_curiosity.py --live` (fictional Sam, known by voice, into astronomy, with a saved dog
Pepper; four lines of ordinary chat, then about four minutes in view while Sam answers whatever Rex asks from a
fixed pool; real reply model and planner; `before` is the persona, planner and reminder of commit 5d2b918 with no
topics offered), two runs per arm:

| | before | after | after, re-run with the last two repairs |
|---|---|---|---|
| replies ending in a question | 3 of 16 | 3 of 17 | 3 of 23 (one rhetorical) |
| his own lines asking about Sam or Pepper | 4 | 7 | 14 |
| topics asked and recorded | none | work; work, where he grew up | work, Pepper's name, where he grew up, as a kid, Pepper's worst crime; work, where he grew up, as a kid |
| facts saved from answers | 0, 0 | 1 (+2 Pepper notes), 0 | 9 (+1 Pepper note), 3 |
| office / piloting / furniture lines | 1 / 1 / 1 | 0 / 1 / 0 | 1 / 0 / 0 |

In the first after-run "where did you grow up?" got "Do you miss the rain, or mostly the forests?" and nothing
saved, and in the re-run a planned "what do you do for work?" came after Sam had answered; both repairs above
came from those. Still seen: replies to an answer sometimes add a question ("What font do you always end up
defending?"); a repeated answer is saved twice; "Pepper chased a squirrel" was once saved as a fact about Sam;
the "what if" questions after 45 quiet seconds take many of his own lines. 139 reply-model and 19 planner calls in
all (`local/validation/curiosity-f17d4802`, `curiosity-5b2e9e41`); dollars are not recorded.
**Not yet run on the robot**: whether the questions come at good moments, whether answers are saved in a live room,
the far-person drive, and the pet question after a real arrival.

## A slower, eased wiggle — October 24

Live run `aedd9ce6` (real hardware): talking while rolling worked. In the 0.4 m roll the
line played over about 1.8 s of the 2.3 s the wheels turned, and on the approach about 3.4 s;
the visor alone talked. The owner then found the side-to-side `wiggle`, done right before
shutdown, too rapid and creaky. It was three reversals in about 1.5 s on the steepest head ramp of
any expression. Each swing now eases in and out on one smooth curve, like idle head moves:
3.1 s in all, a fifth of the acceleration and under half the peak speed. At the owner's request
the `perplexed` double head-cock eases the same way (3.9 s, holds kept)
([servos](SERVO_INTEGRATION.md)). Synthetic pulses only; **not yet seen on the robot**.

## He talks while he rolls — October 24

Owner: "He either talks, or rolls, but never both at the same time." In 309 saved drives (56
robot sessions) not one second of his speech overlapped the wheels. A drive ran inside the one
speech queue, shared the motion lock with talking motion, and its whir used the one speech sink.
Repairs (owner's choices: speak as he sets off, visor and mouth lights only, head and arms still,
whir quiet while he talks, short lines only, stop still heard over his voice), synthetic checks
only, **not yet run on the robot**:
- The drive runs beside speech: the arms tuck and the wheels start while his opening line plays.
  More than 14 words still to say are said first, and the wheels wait (`output.ROLLING_WORDS`).
- A new `drive_base` line, `say_while`, starts once the wheels do (the opening line was still
  playing at plan acceptance in 238 of 336 drives, but for a median 0.9 s against a 2.8 s arm
  tuck, so it rarely reached the rolling). A longer one is said before he moves.
- Beside a drive only the visor talks (`ServoOwner.talk_visor`); mouth lights follow playback.
- The whir pauses for each line and comes back while the wheels still run.
- While the wheels run, sound captured over his own voice is checked for a stop or collision
  warning with his own line's words set aside (`stop_heard_over`); the conversation still ignores it.
Details and evidence: [drive base](DRIVE_BASE_INTEGRATION.md#talking-while-rolling--october-24-2026).
1544 blocked-I/O checks pass (5 skipped), 21 new in `tests/test_talk_while_rolling.py`. Open: a live
test of "stop" said over his voice while he rolls, how the visor-only talk looks, and how often the
model writes a rolling line (no hosted probe was run).

## A quiet room is not other people talking — October 24

Live run `b5ef657c` (owner alone, 7 minutes): after a good start (a drive toward him, five lines),
Rex sat silent and still for about two minutes, then two more, until the owner spoke. One reply
("He's at the truth.", misheard) was taken as said to someone else, and that choice held all his
own initiative for a fixed **two minutes**: no beats, no drives, no "what if". When the hold ran
out the "what if" came, was let go as "talking to each other" in an empty room, and started another
two minutes; the planned head tilt after it was dropped as its follow-up. Repairs:
- **The floor goes back after 20 s of quiet.** Talk he leaves to others (to someone else, a pet, a
  device) or an unfinished thought holds his own beats until nobody has spoken for 20 seconds,
  counted from the last words heard (`proactive.HUMAN_FLOOR_SECONDS`, which also replaces the 20 s
  pet-aside hold). Words heard while the floor note is held keep it with the people talking. On his
  own beat long after anyone spoke, letting it go holds nothing. "Stop" and quiet requests are unchanged.
- **One more ask in a quiet room.** If his own beat is let go as people talking to each other when
  nobody has said a word for 20 s, or a "what if" is let go with no reason, the same generation is
  asked once more (`quiet_room_silence_retry`), stating how long the room has been quiet; a second
  silence stands. A beat at a lull soon after people's words may still stay out without a retry.
- **An inserted beat has no follow-ups.** A "what if" or visor beat that is let go no longer drops
  the plan's next step that followed the beat before it.

Checks: 6 new in `tests/test_october24_repairs.py`; 3 old checks that asserted the two-minute hold
now assert the 20 s rule; 1,523 blocked-I/O checks pass (5 skipped). Paid probe
`tools/check_hypotheticals.py --scenario misheard` (fictional Sam, the misheard line's silence
replayed, then three quiet minutes; fixed spoken plan, 15 s ticks), 3 runs, 27 Luna calls, about
$0.03: Rex spoke again 35 s after the misheard line every time (one tick after the 20 s hold;
live: four minutes), asked one "what if" in each run on the first try, all ending at the question
mark ("What if every telescope could see through clouds, but only to show you what the sky looked
like a thousand years ago?"), and said 4 lines in the three minutes. In 1 of 3 runs the beat after
the misheard line was first let go as talk between others, as live; the quiet-room retry spoke.
Later beats joked about being ignored, twice in one run (the ruling allows one jab). The probe has
no body, so every beat is spoken. **Not yet run on the robot**: whether a quiet stretch now brings
the "what if" and the rest of the plan's moves.

## "Do you ever think..." after a long quiet — October 23

Owner request (the September 18 roadmap item): when the room has gone quiet, Rex sparks it with a short
"what if", like "what would happen if everyone won the lottery on the same day?". Owner rulings: 45 seconds
with nobody talking, Rex included, and one question per quiet stretch (the next only after someone speaks).
- **When.** The idle tick inserts one beat ahead of his current goal's next, marked `wonder`, once 45 s have
  passed with no human speech and no heard line of his, and only with someone in view. His silent moves and
  glances do not count as talking. Quiet mode, the human floor and queued speech still stop it first.
- **What.** The beat's single generation writes it (`hypotheticals.INSTRUCTION`): one light hypothetical,
  sometimes a twist on an interest of theirs, ending at the question mark with no answer of his own. It may
  be let go when they are busy, upset or talking to each other. It is exempt from the rule against asking a
  new question over his own unanswered one; it is not offered a cloned voice.
- **Remembered.** Only a question actually heard is saved (`hypotheticals_asked`; a private one with its
  person, removed when they are forgotten) and offered as spent next time. A reply within 3 minutes sees
  `your_hypothetical` and may play along. Make-believe is never a fact.

Checks: 6 new blocked-I/O checks in `tests/test_hypotheticals.py`; 1,517 pass (5 skipped). With the trigger
disabled 4 fail; without the question-rule exemption 3 fail. Paid probe `tools/check_hypotheticals.py`
(fictional Sam sits quietly in view after a short chat, answers, then sits quietly again), about $0.20:
- With the real planner (2 runs) Rex's own beats kept talking every 15-30 s into the silence, so the first
  stretch never reached 45 s. One question came in each second stretch, and one ran a punchline after its
  question; the instruction now says to stop at the question mark. That probe has no body, so every planned
  beat is spoken; on the robot many are silent moves.
- With a fixed plan that leaves the room quiet (3 runs), 4 questions, all ending at the question mark, at
  most one per stretch: "What if household appliances held elections—would the kettle win on turnout, or the
  vacuum on sheer intimidation?", "What if every door in the house opened onto a different planet—where would
  you sneak off first?". One later beat still remarked that he had not been answered.
**Not yet run on the robot**: how often a real visit goes 45 s quiet, and whether the questions are welcome.

## Impressions of his own — October 23

Owner: "If I mentioned I was going to a particular president's library, he would riff about it in
that president's voice", in V2, and he wants it "with other people's voices too, and my own". In
132 V3 runs there had been five requested impressions and no cameo at all.
- **On his own beats.** The goal planner can plan an `impression` beat (a voice and the thing it
  butts in about), and an ordinary spoken beat may become one when the talk since it was planned
  handed him a voice. The voice is rendered before he says a word, then his setup line, the voice
  and his reaction play straight through; a failed render says nothing. Not while nobody is
  answering his lines.
- **Any voice he has.** Famous voices, people he knows, and the person here in their own voice.
  People's voices only on a private beat (someone he knows is here); a known person's voice never
  reaches a stranger.
- **Cameos in replies** may use the same voices, now wait with the thinking chirps after his line
  (live takes rendered in 13-31 s), and the reply reminder names a voice just brought up as a chance
  for one. The same-voice hour, never checked on replies before, is enforced.
- **One budget** for cameos and beats: ten minutes apart, the same voice once an hour, four per run.

Checks: 16 new blocked-I/O checks in `tests/test_impression_beats.py` (fake cloner); 1,511 pass (5
skipped); four of them fail with the render-first path removed. Paid probe
`tools/check_impression_beats.py` (real reply model and planner, fictional Sam known by voice, fake
cloner and clock), four rounds while tuning the wording: the Reagan library brought a Reagan bit in 3
of the last 6 library runs (one reply cameo, two on his own beats), e.g. "Well, Sam, enjoy Air Force
One. I always believed a president should travel comfortably, and preferably with a pilot who knows
where he's going." Sam's own voice was used once (round 1, a generic bit about his entrance) and
planned once more but held because nobody was answering. One reply said "a Reagan cameo is still
available if you want one"; the reminder now forbids that, and the next two runs had no such line.
About $0.70 of hosted calls in all, estimated from returned usage. Also seen, not changed: replies to
"next month I'm driving to the Reagan library" twice narrated a failed save ("I couldn't pin down
next month"). **Not yet run on the robot**: live render time on a beat, how often the planner picks
one with real people, and whether the chirps before a cameo feel right.

## Looking for things, and the run's other problems — October 23 run 1e626e07

Owner: "Rex struggles to turn his head to look for things when instructed." Asked six times in
2¼ minutes to look left and find the duckling droid on the table, he found it on the sixth.
- **One way to look.** Two of the three look tools could only photograph where the head already
  pointed; the one that moved it looked only when its question was filled in. Every look now
  takes a direction: `inspect_view` and `inspect_visual_memory` gain left, right, up, down and
  `last_look` (where a person last had him look). On the robot the head turns there first and the
  picture is taken after it arrives; already looking that way, it does not swing further.
  `set_robot_pose` on a person's turn always ends a `look_*` step in a picture, taken right after
  the look and before any look back ("look left first and then find him" was look_left,
  look_at_people with no picture between).
- **A requested look holds.** Owner ruling: until he has answered from it and about 20 s after,
  even when they speak; the voice reflex ("You're not finding it" swung the head back) and the
  idle gaze wait, and his own goal beats cannot move it. "Look at me" or a drive ends it. Owner
  ruling: such looks are brisk (neck speed 40, was 20; the swing took about 3 s).
- **He knows where his head points and why.** Held left at Bret's request he had been told
  "looking at the person he is with, since a couple of minutes ago". The reason now comes from the
  servo owner: the request it still points for, or the voice it turned to, with `earlier_look`
  when the head has left a requested look.
- **A look about a thing is not a dog check.** "He's white" (the droid) got both dogs' crops and
  photos and "that's Toby". `inspect_visual_memory` has `subject` animal or thing; a thing look
  gets no pet crops or pet photos.
- **Split sentences are one.** "Look to your left and notice there is a." then "Duckling droid on
  the counter." is answered as one utterance when the first piece was yielded unanswered and the
  second starts within 5 s (the separate source turns stay in the trace and store).
- **Neck calibration.** In four runs a seated face moved across the picture 0.48–0.65 (pooled
  0.57) as far as a neck change should move it with the IMU-checked 90° lens: the neck turns about
  0.57 of what V2's never-measured figure assumed (whole travel about 51°, "look left" about 19°).
  The reflex aimed at Bret's face about 25° short and crept toward him. `NECK_PULSES_PER_DEG` is
  corrected and every degree-to-pulse conversion clamps to the neck's travel; idle and talking
  motion keep their tuned sizes. Drive re-finding looks go 25° (was a believed 40°), and a missed
  head look at something far to the side turns the body for one more look.
- **The lost reply.** One blurry, low-confidence frame (detector .51) counted as a newcomer and
  dropped the reply to "There's also a marionette puppet next to him"; the owed answer then went
  to his own telescope beat. A newcomer must be seen 1 s at .6 or better; a reply dropped for a
  newcomer is answered at the next lull before any goal beat.
- **The eyes.** They went dark for several seconds with no command sent and no USB reset in the
  Mac's log, until his next line set them. Between lines the eye colour is now resent every 3 s
  (not over a requested blink). He invented "my head reflex swung back toward your voice": he is
  told he cannot see his own lights and to say he does not know.
- **Pet facts.** "He's very feisty, and he likes to bark a lot" about Max was refused as a fact
  about a person. A third-party fact naming a saved pet said in the last 10 minutes is added to
  that pet's notes (not when the words name another pet).
- **Content.** Saved-fact topics open as his own memory with a way in ("Voyager, huh?" got "Why do
  you bring that up randomly?"); an opinion he just gave is defended, not reversed in the same
  reply; words he has said in two or more recent lines (`mentioned_lately`, his own output only)
  are spent for the planner; look answers stop listing the telescope.
- **Cost.** Reply requests averaged 49k input tokens and 19 of 45 cached nothing. Continuations
  now send the same static tools (tool choice none), and every reply request carries one
  `prompt_cache_key`; one paid request confirmed the key is accepted. The cache-hit change on
  the robot is not yet measured.
- Speed was within the 4 s ruling: median 3.4 s to the first word, 5 of 23 over 4 s (max 4.8 s).

Checks: the 1,492 blocked-I/O checks pass (18 new, mostly in `test_october23_looking.py` and
`test_october23_repairs.py`; drive, gaze and reflex expectations moved to the measured neck).
Paid probe `tools/check_action_requests.py` (real reply model, stand-in head): the five duckling
cases 19 of 20 over four rounds (the miss looked left with the pose tool and a picture, now
counted), all 27 cases 27 of 27. A first wording of the looking note dropped "You can talk again,
but you can raise your visor too" to 1 of 8 (old code 7 of 8); with the lighter note and
resume-first wording it is 8 of 8. Paid humor probe (`tools/check_humor.py --before HEAD`, one run of
seven scenarios plus three more of pushback): no clear change (mean funniness 2.12 against 2.04;
in the pushback scenario 3 retreats against 1 over four runs each, with equal funniness). **Not yet
run on the robot**, the neck figure included; it is fitted from logged faces, not measured by eye.

**Live, run 8e11d468 (same evening):** owner: "he looked correctly". "I would like you to look to your
left. And locate the desk." was one look left with a picture ("The desk is just left of the couch,
beneath the monitor"); "What do you see on the table to the left?" after his head had turned away looked
again with `last_look`. The voice reflex landed on the owner's face and later turns were already facing
(no creeping). A split sentence was joined. Toby and Max were mixed up again (more close photos of
each will help). Adding a photo or a detail to Max was refused twice because the words did not repeat
his name. Owner ruling: a pet he just named takes the photo or detail without the name again, unless the
words name a different pet. A pet a person named in the last 10 minutes now counts for another photo of
it, a new note and a third-party fact, when the words name no other saved pet or object; a rename, a
removal or forgetting still needs the name. The teaching look is told which pet was just named, so it
no longer asks for it. The 1,495 blocked-I/O checks pass. Not yet run on the robot.

## Sentry mode ignored the person who armed him — October 22 run dc9f7480

Owner: "I activated sentry mode and triggered it, then tried to say it was me with my face in camera
and asked him to stand down. He refused, saying I was a rouse... I can't shut him down." Bret armed
him and stayed in the room. Whoever armed him is exempt as "leaving" until the room has been empty
for 30 s, and the room never emptied. So after the trip his recognized face (almost every frame for
two minutes) and four clear voice matches ("It's okay, Rex. It's me.") were ignored. "Shut down."
in his less clearly matched voice was ignored as an intruder's. Rex stopped only when his 5 minutes
ran out.
- The "leaving" exemption holds only until the alarm goes off. After a trip, anyone he knows by face
  or clear voice stands him down, the person who armed him included.
- An exact "shut down" works with a clear voice match of someone he knows, or with someone he knows
  in view (the person who armed him included), in any sentry phase.
Checks: three replays of the run fail on the code that ran and pass now; 40 sentry checks and the
full suite pass. **Not yet run on the robot.**

## Sentry mode's alarm talk spoke as ordinary Rex — October 22 run a45983c1

Owner: "It in practice works exactly as designed. The problem is when he's triggered. He started
commenting on the telescope in the room." The alarm lines were sent with ordinary Rex's whole request:
36 messages of earlier conversation, saved memories, room and photo memory, joke material, and a
picture of the whole room. A voice-only trip (a device saying "you don't have enough credits") got
"A telescope in the room and not a face in sight" and a jab at a closet. In the second test Bret was
recognized 0.6 s before the roast, but the guard checked faces only after speaking: "Oh, Brett—it's
you. Stand-down; I was about to accuse that telescope…" in alarm. The phone alert waited behind the
first line (22 s).
- Guard lines are written from a guard-duty brief and this intrusion's own exchange only, with the
  sentry facts as their whole context. The picture is cropped to the stranger (none when nobody is in
  view: he calls out to the hidden voice instead).
- A known face in view is checked before the cry and before every line: he stands down instead.
- The alert has its own 10 s timer.
Checks: 37 sentry checks (3 new) and the full suite pass. Paid probe: three voice-only alarm lines
with a telescope conversation in history, all on guard duty. **The repair is not yet run on the robot.**

## Sentry mode — October 22

Owner request: "activate sentry mode", read by the reply model ("until 7pm", "for 1 hour", "with no
audio alerts", "silent with 60 second activation good for 9 hours") through the new `sentry_mode`
tool. Code checks the request and keeps the clock.
- **Countdown.** He states the settings and counts down in cached clips of his own voice. Saying
  "stop" or "cancel" during the count calls it off.
- **On guard.** Red lights, every arm held at rest, the head level, the neck sweeping five stops,
  the wheels and the back-off reflex refused. No idle motion, goals, planner, joke writer or paid
  news searches, and no hosted call at all while nothing happens.
- **Tripping.** A face held about 3 s, unrecognized 3 s later, or a sentence of four or more trusted
  words, trips the alarm. Faces that never move during the countdown's survey are pictures.
- **The alarm.** A cached "Intruder alert!", the arm out, and about two minutes of roasting through
  the one conversation owner with a sharp picture and no tools, then silent watching.
- **Recording and alerts.** An arriving face starts a recording with 10 s of pre-roll, kept in
  `local/sentry/recordings/`. An ntfy phone alert with a photo follows 10 s after the trip, unless a
  known face stood him down first.
- **Standing down.** A recognized face, or a clear saved-voice match (`SpeakerTracker.match`, read
  only), stands him down. An intruder's words never do. When the time is up he shuts down.

Checks: 34 new blocked-I/O checks (synthetic camera rows, voices, clocks, ports, HTTP); the full
suite passes. ffmpeg joined synthetic frames and audio into a playable `video.mov` on this Mac.
Paid probe (`tools/check_sentry.py --live`, real reply model): 7 of 7 requests read correctly, and
the alarm and stand-down lines came out in words with no tool. **Not yet run on the robot**: lights,
servo moves, real faces and noise, hearing a call-off over the countdown, recording load and phone
delivery. Details and phone setup: [sentry mode](SENTRY_MODE.md).

## The visor fumbles were a missing tool — October 22 run e72ab522

Owner: "the visor stuff still sucks badly". Lowering, raising and "back to normal" failed again
(prose saying it was done, two light sparkles for "raise it", "the visor tool isn't available"). The
cause, across all three runs of the day: the servo owner is `moving` whenever any motion runs, above
all the listening cue as a person finishes speaking, and the pose tool was offered only while it was
`active`. Every visor request that arrived during the cue had no pose tool (15 of 20 in runs 1072c10a,
0421a222 and e72ab522); every one that arrived while the cue was refused worked. The same gate hid the
shut visor, so the "it's dark" beat was let go as `visor_open`. The earlier wording and quiet-tool
repairs treated symptoms; the paid probe missed it because its stand-in robot was always `active`.
Poses are now offered while the robot is awake (`active` or `moving`; a pose stops other motion first),
and the probe's stand-in is `moving`. Paid probe, visor cases: 7 of 7. **Not yet run on the robot.**

## Seventeen repairs from run 0421a222 — October 22

Owner review of the 2:37 PM run (18 min). Fast mode held (all 76 reply calls served Fast; 6 of 31
replies over 4 s, from 19 of 25) and plans written ahead were adopted at once. Fixed:
- **Visor.** Worded as blinding him, lowering took four asks (silence, a nod saying "Down it goes",
  words, then the move); the pose tool is neutral again and the dark is noticed once it is down:
  a one-line reminder rides each request while it is shut, and his next beat after it closes is
  about the dark (let go if it was raised first). `visor_normal` puts it back to its usual open
  position and lets it move again; his own raise uses it; `release_poses` says the visor goes back.
- **Looking.** "Look left and tell me what's on the desk" is one `set_robot_pose` with `look_after`;
  the silent glance is no longer offered on a person's turn when poses are, and a follow-up "do you
  see it?" looks again in that call (the voice reflex turns the head back to whoever speaks, kept
  for "I'm over here", September 28). Glances can look down.
- **Showing.** Being shown something is a look, not a save; he offers to remember it.
- **Repeated looks.** An own closer look within 2 minutes of the last look is let go, no call; the
  voice reminder forbids announcing a bit or subject switch.
- **Shutdown.** "Red shut down" and similar mishearings of his name are the exact shutdown.
- **Pacing.** The "if nobody answers in N s" wait counts from his last heard line, not a silent glance
  (35 and 36 s silences); the planner plans a look back only after a look away (seven empty ones);
  a moved-on beat that picks silence without a reason is asked once more.
- **Lights.** An effect he does not have is said, not substituted; the sparkle with words runs while
  he speaks (it added 2.5 s before); a beat opening like his last line counts as a repeat.
- **Speed.** Pose replies always lead with a short line; at startup, planning waits up to 20 s for the
  greeting of a face in view (one wasted planner call per startup).
- **Content.** Subjects of recent goals, this run's or the last, are spent for the planner (the
  telescope in 9 of 47 lines); `calendar` gains `born` (age and birthday give the year; the
  answer was right for the wrong reason); praise gets "Naturally.", not "You're welcome".
Blocked-I/O checks: the 1,028 tests in the affected files pass (not the full suite). Paid probe,
visor cases only, two rounds: 10 of 10 (lowering on the first ask, "back to normal" with
`visor_normal`) in short fresh conversations; the live miss came in a long one. **Not yet run on the
robot.**

## Reply model in Fast mode — October 22

Owner choice: Rex's reply model (GPT-6 Luna, reasoning effort low) runs in OpenAI Fast mode
(formerly priority processing), `reply_service_tier = "fast"`, at twice the Standard token price;
the planner and joke writer stay Standard. `model_usage` records the tier actually served (Fast
can fall back to Standard when traffic ramps). Run 1072c10a had 19 of 25 replies over 4 s.
Paid probe `tools/check_reply_latency.py` (production-sized synthetic request, 34k input tokens,
10 alternating rounds per arm, all 20 Fast requests served Fast), median time to the first
speakable phrase:

| route | Standard | Fast |
|---|---|---|
| reply tool (most live replies) | 2.18 s (p90 2.30) | 1.42 s (p90 1.49) |
| prose | 3.43 s (p90 3.97, max 5.55) | 2.26 s (p90 2.44, max 2.85) |

Logged Luna spend was about $0.61 per running hour at Standard (1,682 calls, 11.5 h, $4.71 in the
last 30 days); Fast doubles it, about $4.70 more a month at that use. Live requests carry about
50k input tokens, so the live gain may differ; **not yet measured on the robot**. Cache writes were
54% of Luna spend (about 40% of calls miss the prompt cache), a separate saving not yet pursued.

## Speaking less, long pauses, light and visor fumbles — October 22 run 1072c10a

Owner: since the stale-goal change he speaks less, the pauses between his own lines are too long,
"sparkle your chest" got "I can't", and the visor would lower but not rise. Run 1072c10a (14 min):
15 spoken beats, 6 said; silences of 39 s and 52 s; 19 of 25 replies over 4 s to first audio.
- **Moved-on beats speak.** Told "if no natural bridge exists, remain_silent", the performer let 7
  of 11 moved-on beats go. A moved-on spoken beat now says one short thing that follows the
  current talk, bridging to the goal where natural; silence only for a real reason. Letting a
  beat go resets the two-turn count (it never reset, so every later beat was stale too), and the
  count starts when the plan is ready, not when planning began (the telescope plan was stale
  before its first beat). This revises the stale-goal rule below.
- **Pauses.** A beat nobody heard (a silent glance, one let go) no longer restarts the five-second
  quiet clock; the next follows 3 s later. The next plan is written in the background once a goal
  is two beats in with two left, and follows its last beat at once (8-13 s of planning silence
  before); same planner calls, one wasted only if the goal ends another way first.
- **Quiet switch.** `set_initiative` drew "Can you..." requests, 15 times in 7 runs since
  October 16, twice today switching him quiet. It is now `stop_chiming_in` (quiet or silent) and
  `resume_chiming_in`, each offered only when it changes something, with no motion examples. The
  switch runs first; after resuming, the same turn does the rest with any tool ("you can talk again,
  but raise your visor too" lifted quiet and promised a visor that never moved); after going
  quiet or silent the next words say so, with no tool. Quiet pauses the goal instead of erasing it.
  The October 17 no-op retry is retired.
- **Visor.** `visor_up` sent it to the open neutral, inside the range it wanders while talking, so
  nothing rose; it now holds 1150 µs, just short of the surprise limit. A nod line may not claim
  the visor, arms, lights or wheels moved ("Visor up." came with a nod). Shut, the visor covers his
  eyes (owner: it "effectively blinds him"): while it is (nearly) fully down the context says he
  sees nothing, and lowering it, and now and then while it stays shut, he makes light of his own
  dark ("Oh wow, it's dark in here"), never of blind people. A shut visor no longer drifts open
  when a person's hold ages out (idle motion took it back after 90 s, without a word). Fifteen
  seconds after a person shuts it, with nothing newer asked of him, it is his to raise: any lull
  beat may raise it (and only it) with a line that leans in ("I kept it down for you, but I really
  wanted to see again. It was dark in here!"), unless they asked him to keep it down.
- **Lights.** `compliment` is now `sparkle` and lights the chest only (it also lit the eyes and ran
  the mouth). A wordless light request while answering someone is asked once for its line.
- **Looks back.** Look beats can look back at the people (`shift_attention` target `people`,
  following faces again); a look at an invented person id is let go with its reason instead of
  failing the turn (twice today).
- **Facts about other people** (owner: "I should be able to talk about other people and have those
  facts be about those people"). "He turns thirty-six on November thirtieth" was saved as the
  owner's birthday. `remember_followup` and `remember_interest` take the other person's name; the
  record is theirs, said by the teller, and a follow-up about them comes back with the teller.
  Facts someone told about others are now part of what Rex knows with that person.
Paid probe `tools/check_action_requests.py` (real reply model, synthetic voice, stand-in head and
in-memory lights; 19 cases: the logged misfires and real quiet requests): before, 18 of 19 in each
of two rounds, failing "you can talk again, but raise your visor too" both times; after, 38 of 38
over two rounds, every quiet request heard. In these short fresh conversations the old switch was
not called for action requests either, so the live misfires (long context) are not reproduced and
their fix is not proven by it. `tools/check_stale_beats.py` was written to measure moved-on beats
and not yet run. Blocked-I/O checks: the last full run (1418) passed except the PySide6 import test;
with the GUI packages now installed that test and the dashboard tests pass. After the last changes
(quiet words-only, the shut visor, told-by facts kept to the teller) only the affected test files
were run, not the full suite. The saved birthday of the owner's partner was moved from the owner's
record to the partner's (backup `local/core.before-jeremy-birthday-20261022.sqlite3`).
**Not yet run on the robot**: visor height, the chest-only sparkle, pacing in a live room.

## Conversation switches that felt random — October 22

Owner review of runs 55db1330 and 193c8df6. Three causes found and fixed (the greeting about a
past event stays, by the owner's choice):
- **Stale goal beats.** Plans of about eight beats on one subject kept returning to it after the
  talk moved on (five telescope remarks in five minutes, one right after a talk about the dogs;
  a second cloud insult). A goal is now *overtaken* once people have spoken twice since its last
  heard beat, or since planning began (counted from the ready plan since run 1072c10a, above). Its
  next beat is told not to jump back to the subject cold but to reach the goal through what they
  are talking about now, or let the beat go (now: say something that fits instead); a look-closer
  beat may be let go too. The goal itself goes on (conversation steers a goal and never ends it,
  owner ruling of October 1) and no planner call is added (October 17). A first version that
  ended overtaken goals was softened the same day: replayed on these runs it would have ended
  goals at 4 of 9 beats and added planner calls. A heard beat resets the count.
- **A head turn taken for a newcomer.** The owner, alone and recognized until 119.0 s, turned his
  head; the unrecognized view was asked "what should I call you?" and a "newly arrived guest" goal
  followed. An unrecognized face is no longer an arrival when it is the only face in view and
  someone known was recognized by face or voice within 60 s; a second face beside a known one is
  still greeted.
- **An avoided topic joked about.** "I was going to ask about the wine run, but apparently I enjoy
  surviving" broke the owner's October 16 "Stop asking me about it". The reply and planner
  instructions now forbid any mention of an avoided topic, including jokes about not asking and
  plan text such as "without revisiting" it. The old wine plan was no longer offered as a
  follow-up; the saved fact remains, and the rule governs it.
9 new blocked-I/O checks with scripted providers. **Not yet run with the hosted model or on the
robot**: whether the performer follows the stale-subject note and the never-mention rule.

## Startup sound buffered; boot line lights the mouth — October 22

Owner: the please-wait line left the mouth LEDs dark, and the droid filler stuttered while models
loaded. Logs (sessions 7f6ce043, b9b8237f, 55db1330) show `led_delivery` for the ready line only;
the boot line was played straight to the sink, with talking motion but no speaking lights.
- **Mouth.** The boot line now begins the speaking LEDs and feeds them playback levels, then
  finishes them, like any line; a dark LED board is logged and the line still plays.
- **Stutter.** Loading the ASR model held the interpreter up to 97 ms at a time (silent probe);
  the playback callback needs it every 46 ms and had at most 0.18 s queued. Under quarter-second
  holds it ran dry 10 times in 3 s, and the device reported none as an underflow, so callback-mode
  `underruns: 0` is not evidence of clean audio on the reSpeaker. Startup sound (whir, boot line,
  filler) now shares one PortAudio blocking-mode stream (`AudioSink.buffered`, latency 0.5:
  about 1.5 s of ring on the reSpeaker) that PortAudio drains without Python. It opens as
  startup begins, so the ring's initial silence plays out before the servo wake, and closes
  before the ready line. The power-down clip and live speech are unchanged.
- Silent real-device probe: the buffered stream playing 9 s of silence while the ASR model
  loaded plus six 0.25 s holds never fell below 1.02 s queued (median 1.39 s), zero underruns.
- Readiness still lets the filler drain (up to about 1.5 s, as the old 1.6 s queue did).
Synthetic checks for the stream, startup order and lights; the one `test_boot` PySide6 error is
unchanged. **Not yet heard on the robot**: audible clip quality and mouth timing on the boot line.

## Calendar, weather and systems tools — October 21

Owner requests. Live session 7f6ce043: asked for the weather and then "Sacramento, California", Rex
said he had no report; `news_today` returned 14 items and the fetched weather, sorted last, fell off.
- **Clock.** `right_now.local_time` now includes the year.
- **`calendar`.** The model names an operation and the dates it means; `dates.py` computes
  weekdays, day counts, date shifts, nth weekdays (Thanksgiving), next weekday or anniversary,
  Easter and the time in another IANA time zone. Missing or invalid fields are rejected.
- **`weather`.** `weather.py` asks Open-Meteo (free, no key) for the named city (region and
  country narrow it) or, with no city, Rex's home location: current conditions, the next
  twelve hours in two-hour steps and a seven-day forecast, reused for ten minutes per place.
  Rex says he looked it up. `news_today` no longer carries weather; lull weather remarks are unchanged.
- **`systems_status`.** Battery percent, volts, amps, watts, charger flag, controller state,
  tilt and the room's temperature, humidity and pressure, read live from the drive controller
  on request (`systems.py`). These readings left the per-turn drive context. The climate
  sensor sits inside Rex (87 F in a 72 F house), so `room_temperature_offset_f` (default
  −15) corrects the temperature and humidity is recomputed for the corrected temperature
  (same moisture, Magnus formula).
- The three lookups run outside the store transaction; a failure is a structured result.
Checks: 18 new or updated blocked-I/O checks with synthetic data and fake HTTP; the full
suite's one error (`test_boot` cannot import PySide6 in `.venv`) fails identically on the prior
commit. One live Open-Meteo lookup (Sacramento, home, Tokyo, Portland) matched the parser.
**Not yet run on the robot or with the hosted model**: tool choice, the corrected room
reading against a thermometer, and battery answers from the real controller are unverified.

## Idle head moves on one smooth curve — October 21

Owner: the random head motion is not fluid (compared with Microduck's look-around). Each idle head
move now times itself by its size and gives every head servo the board speed and acceleration that
keep it closest to one shared minimum-jerk curve, so they arrive together; room looks go somewhere
new and sometimes straight ahead. Details and the simulated-ramp numbers are in
[servo integration](SERVO_INTEGRATION.md#october-21-idle-head-moves-on-one-smooth-curve).
Later the same evening, after a robot run (fluid but slow, little looking around, talking jarring):
idle moves are quicker with more frequent, wider glances and room looks and drift through each
phrase; talking moves the head, and the hero elbow and wrist, on the same eased curve, varied
each time, with the arm and visor starting just after the head. Synthetic checks only.
After a second robot run: a line that plays out lets the arms run on and idle carries the
liveliness of talking for about 20 s; the throttle arm moves firmly in bouts and rests lower
between them; the poker arm sways while he speaks. Synthetic checks only.
Then: the throttle finishes its talking move at its own pace and goes straight on, its wrist
is gentler, and at rest the shoulder relaxes fully (through the tuck). Synthetic checks only.
The listening cue (head cock as he hears you) now eases in and out, varies, and stays on one
side across a run of sentences instead of swinging across. Synthetic checks only.
The poker arm's sway while he talks is about a third of its idle size, slower, with pauses.

## October 20 run: an introduced guest was never learned

Session `515d71db` (24 minutes, real hardware, the owner and his partner Jeremy). The owner
introduced Jeremy ("This is Jeremy Thomas", "get to know Jeremy", "talk to Jeremy") and Rex
gathered none of his face or voice: the owner called it the biggest issue. Four causes, and the
repairs (synthetic checks; **not yet run on the robot**):

- **A third-party introduction linked no voice by design.** It now opens a window for the visit in
  which the one unknown voice and the one stranger's face around are gathered under the
  introduced name (owner ruling, AGENTS.md; details in
  [introduction recovery](INTRODUCTION_RECOVERY.md#october-20-someone-introduced-by-another-person)).
  A second unknown voice undoes the voice link; the voice still completes only through the
  enrollment chain. A guest's objection does not stop it (owner); forgetting does.
- **The listening head cock made new voices unlearnable.** 81 of 87 clips with a second or more of
  speech overlapped Rex's motion, 41 of them the listening cue: people answered during its hold.
  A clip heard during motion never starts a new voice. The still hold no longer counts as motion,
  speech during it stops the cue where it is, and no cue starts while the person is mid-sentence.
- **Face learning followed one camera track for 30 s;** tracks lasted a median 0.5 s (1,169 in 24
  minutes). Views are now pooled across tracks, anchored on the introduced face.
- **The model pointed at the owner's blurry face** (.77 against his saved face, unnamed) instead of
  Jeremy's. Face rows now carry `resembles`, and a target that resembles someone else is replaced
  by the one stranger's face in view.

Also seen, not repaired here: speech came in pieces at 0.5 s pauses and about 95 prepared replies
were dropped for newer speech; the microphone array placed only 44 of 268 utterances, so a
two-minute-old voice direction from Jeremy sent Rex the wrong way when the owner said "We're over
here"; approaches stopped about a metre from Jeremy, too close for him. 1358 of 1365 blocked-I/O checks pass (5 skipped); the GUI import check needs
PySide6, which this environment lacks, and one dashboard operator check lost an existing race with
idle motion once under full-suite load (it passes alone).

## October 18 afternoon run: "ignored" fired too soon, and a cake that was not there

Session `6aaf22aa` (4:52–5:09 PM, real hardware, the owner alone; the first run with all of the
October 17–18 repairs). What worked: 17 replies to the owner, median 3.5 s from the end of his words
to Rex's first sound, 10 of them under 4 s; every one over 4 s except two was a reply queued behind
Rex's own drive or line (22 s and 7.8 s), not a slow model. Twelve drive trips: both approaches to
the owner and "back up three feet" (0.91 m) completed, turns landed within 2–3° of what was asked,
a self-chosen reverse was refused as designed, and "shut down" acted at once. No lead-in opened
"Let me"; no "You're right" openers; news read the fetched headlines; Jeremy was recalled as the
decorator; nobody else was credited with the owner's voice (recognized on 9 of 28 trusted turns,
all the rest unresolved, never wrong). The pose, lights and idle motion ran without a freeze; no
playback underruns; peak memory 3.65 GB.

What did not, and the repairs (synthetic checks; **not yet run on the robot**):

- **All four "ignored" goal endings were wrong.** The count rose the moment Rex's second line
  finished playing and the goal ended right there: "Brett, is tonight anything beyond the
  laundry?" was ended as ignored as it finished, and the owner answered six seconds later. Twice
  more the owner's words ("I'm scratching my head", "Back up.") were already captured and waiting
  behind Rex's own approach when the goal ended. Each cost a planner call and a self-deprecating
  jab the owner had not earned. Now the decision is taken at the lull check: after the second
  unanswered line the next beat holds for `IGNORED_ANSWER_SECONDS` (8 s, from the end of his line)
  while the person may answer; speech captured but not yet understood keeps the check from running
  (the existing human_work_pending gate) and then resets the count; a plan made while already
  ignored is the ignored response and is not itself ended as ignored (the probe showed a second
  planner call otherwise).
- **A phantom cake.** The object detector read a "cake" in 14 of 671 looks, never more than three in
  a row (also pizza, a kite, a frisbee, a cat, a toothbrush); it entered the picture of the present
  as a thing "seen just now", the writer's room pitched "cake sitting in the room" and Rex teased the
  owner about a cake until he said there was none. Detector scores do not separate it (cake .46
  median; the real backpack, cup and remote .46–.48), persistence does: real things are seen for
  minutes. A thing now joins the picture only after `STEADY_SAMPLES` (5) looks in a row, about seven
  seconds; once known it stays through a dropped look. Pets keep their separate path. The planner's
  own evidence list and drive targets still come from the two-look snapshot.
- **His own motors cut off his beats** (owner: "that's probably why he sits there doing nothing").
  53 of 81 captures were brief sounds during his own motion, recognized as "Okay." or "Oh.",
  found unreliable and never answered; but 11 of the 13 beats cut off while being prepared were
  cut off by exactly these (`capture_queued`, `recognition_busy`), each then waiting its 4-second
  retry and a fresh 5-second lull, and a second cut-off skips the beat. Queued audio now
  interrupts a beat in flight only while it may be words (`words_pending`): a capture confirmed as
  speech, or a brief sound whose transcript recognition then trusted (it joins as a queued turn).
  An unconfirmed sound found unreliable lets the beat go on. Audible speech, stop words, "shut
  down" and the lull gate itself are unchanged.

Also seen, not repaired here: the first plan was written before the owner's face was recognized
and its beat still asked a recognized owner his name ("You already know me"); the planner later
called the recognized owner "the unfamiliar person seated with a phone" while
`unrecognized_face_in_view` was true with him alone in view; "Back up." waited 11 s behind Rex's own
beat and was then dropped when the owner spoke again, so he backed up only when asked a second time;
the second approach ended 0.32 m from the owner; a look's follow-up said Halloween was "months ahead"
on October 18; a lead-in was spoken twice ("Oh, that one I know. Oh, that one I know."); 53 of 81
captures were motor noise heard as "Okay." or "Oh." (never answered; the beats they cut off are
repaired above; once they filled the conversation queue); a bearing came with 6 of 81 utterances; the writer's material
was never reported as used (one joke recorded: the cake). Reply requests still carried about 45,000
input tokens each (47% cached), the same as before the October 17 diet; the two October 18 runs
together cost about $1.86 an hour at 366 model calls an hour (`tools/baseline_report.py --since
2026-10-18`). The weather fetch failed twice with an HTTP error.

1339 of 1341 blocked-I/O checks pass apart from the GUI import (PySide6 not installed) and the
intermittent dashboard servo timing check (passes alone); new checks in
`tests/test_present.py` (a flicker is not a thing), `tests/test_agency_plan.py` (an answer in the
window keeps the goal; queued speech blocks the check) and `tests/test_proactive_core.py` (a brief
sound during his own motion does not cut off a greeting; a trusted turn still does).

## Pets from close crops only; near spellings of a name — October 18

Same run, traced further. Of 24 photo looks and teachings, 21 used the whole camera view: the
detector found any dog in 39% of frames and flickered on the dog held in the owner's arms, and
the reply model often chose the whole view. Whole views are sent at 960 px wide, so the white
chihuahua was about 40 pixels across in dim light and was saved as "light-colored with darker
ears"; four of six saved pet photos were the whole room, and one saved as Max showed only Toby.
The friend's name was transcribed Exudica, Exodia, Exotica, Exhilar and "exude a cup".
Repairs (synthetic checks; **not yet run on the robot**):

- **Every animal seen in the last 10 s gets its own close crop** (`Objects.pet_views`: each
  sighting keeps its own frame, newest per place, up to three, box widened 25%), offered beside
  the overview in looks and teachings, each with its own evidence id.
- **A pet is saved only from a close crop** (`PetPhotoNeedsCloseCrop`; "hold them up toward me"),
  replacing `PhotoTeachingNeedsSinglePetCrop`; whole-room pet photos are never used in
  comparisons; when pet crops are present, only pet photos are compared, within the eight-image
  budget. Objects and rooms may still use the whole view.
- **Near spellings.** For someone named or introduced in the last 30 minutes, a name scoring .7
  or more (same first letter) is that person in introductions, third-party mentions and pet
  naming, and each request carries `name_spellings` (heard word, saved name) so Rex uses the
  saved name without remarking on spelling. Only when exactly one recent person fits. Known
  limit: a different person with a near name introduced within half an hour is taken as the
  first (Michael/Michelle score .80, Daniel/Danielle .86, as close as Exodia .77).

Photo memory cleanup at the owner's request (backup `local/visual-memory.before-pet-crops-*`):
the four whole-room pet photos removed; the owner confirmed the remaining crop saved as Max is
Toby, so it moved to Toby (two photos, both confirmed); Max and Nova keep their names with no
photo until re-taught up close, Nova described as the owner did ("small, completely white
chihuahua").

1337 of 1338 blocked-I/O checks pass apart from the intermittent dashboard servo timing check
(GUI import: PySide6 not installed); seven new in `tests/test_pet_crops_and_names.py`.

## October 18 run: the friend saved as a dog, and other repairs

Session `50ab4eda` (31 minutes after midnight, real hardware): the owner, a friend on the couch
and three dogs. Replies that started at once had a median of 3.37 s and p90 of 4.17 s (5 more
waited behind Rex's own speech, median 9.7 s); both spoken stops halted the wheels with
"Stopped."; news, "turn around", "move forward five feet" and "back up six feet" worked; the
owner was recognized on 31 of about 100 trusted turns. Not great, repaired (synthetic checks;
**not yet run on the robot**):

- **The friend was saved as a pet** ("This is ..." with dogs in view) and a dog was saved under
  a misheard name. A pet may not take a known person's name (`NameBelongsToPerson`), and the
  persona says "This is <name>" names a person unless they say it is the animal; ask when unsure.
- **"She also goes by ..." became a reminder about the owner.** An introduction of a known
  person now adds new names as aliases; remember_followup is never for names.
- **He told the guest he could not "save your face"**: never talk about faces or voices being
  saved, scanned or learned.
- **Asked to face the owner, he turned away 30 s later** on his own doorway beat. For two
  minutes after a person's requested move, his own moves may only face or approach a person
  (`KeepingRequestedPosition`).
- **Still conceding** (8 of 73 lines) and **22 of 73 lines opened "Let me check/compare/get"**.
  Concessions are now counted in any sentence, a reminder goes last in the request once one was
  made, and lead-ins are told to vary (often a sound or a reaction, not "Let me" again).

Memory cleanup at the owner's request (backups `local/core.before-oct18-cleanup-*.sqlite3` and
`local/visual-memory.before-oct18-cleanup-*`): two wrongly taught photo entries forgotten with
their photos, the misfiled other name moved to the friend's aliases (its claim retracted and
reminder removed), and the provisional "Dad" forgotten through the store's forget.

Microphone direction with all four beams logged: the beam Rex reads (index 3) is the right one,
as no other beam carried speech when it read zero; slow reads are gone (2 of 14,248 over 0.2 s).
Sentences of 1.5 s or more got a direction 12 of 30 times; one-word replies 17 of 93. The
remaining limit is the 50,000 energy floor and three-sample minimum on short speech; lowering
them should be checked against the recognized face's bearing first.

1330 of 1331 blocked-I/O checks pass apart from the intermittent dashboard servo timing check
(GUI import: PySide6 not installed); four new in `tests/test_company_repairs.py`.

## Three on the couch: repairs from the October 17 night runs

Sessions `2b044e51` and `76184646` (10 and 12 minutes, real hardware): the owner, Jeremy and a
third person who did not speak. Nobody was wrongly credited with the owner's voice and Rex
recalled what the owner had said about Jeremy; but 95 replies were started and thrown away to
cross-talk, Rex answered talk that was not for him ("You got in a conversation that didn't
involve you"), Jeremy felt stared at, "turn to face me" was refused twice, simple requests went
through the quiet-mode tool with false "I can't" answers, he said he could not get news, and
nine lines opened with a concession. Repairs (synthetic checks; **not yet run on the robot**):

- **Endpoint 0.5 s** (owner; was 0.35 s), in the default, the example and `local/settings.toml`.
- **Company.** With two or more people around, each request carries `company` (how many, and
  whether "Rex" is in the words: spelling, not meaning) and the persona's `in_company` rule:
  answer only words that are for him, otherwise `remain_silent(other_addressee)`.
- **Gaze shared among people** (owner): with two or more faces the idle head stays with one
  person 4-9 s, then goes to whoever it looked at least recently, and turns to someone
  identified as speaking within 6 s; the existing glances away every 7-16 s are unchanged
  (`gaze.FaceTurns`, replacing "always the largest face").
- **"Face me" without a voice bearing** uses the identified speaker's own recognized face as the
  `speaker` drive target.
- **No more "You're right."** The persona forbids opening with a concession or apology and
  treats a disputed word as a mishearing; `right_now.conversation.concessions` counts his own
  recent concessions (his output only).
- **Wrong tools.** A no-op `set_initiative(available)` is retried once without that tool, so the
  request gets its real tool; `news_today` reads the already-fetched headlines, stories and
  weather when asked; `inspect_view` says it cannot look anything up; `recall` points time
  questions to `recall_conversations`; the hero arm has no hold-out pose and he says so.
- **A relationship is not a name** ("My dad." saved a person named Dad): introduce_person asks
  for a name first. Live, this may have been a real introduction of the third person.
- **Context.** Saved facts in `personal_memory` carry only what the model uses (the block was
  15,600 characters), the `claims` list no longer repeats them, and camera diagnostics on each
  face are left out of requests.
- **Measurement.** `turn_done.timings.before_turn` and the report's `reply_wait_prompt` /
  `reply_wait_queued_behind_rex`: the 8-15 s replies were words that waited while Rex was still
  talking or driving, not slow models.

**Microphone direction** (no bearing on 102 of 119 and 85 of 97 utterances): not mainly
loudness. The array's selected-beam speech energy read zero on 61% of polls its own detector
marked as speech; a quarter of the rest were under the 50,000 floor; a 1-2 s utterance yields
about two usable polls and three are needed; slow USB reads (16%) and Rex's own playback
(13%) removed more. The read-time limit is now 0.2 s (was 0.15) and all four beam energies are
logged; which register or floor is right needs a short read-only probe with someone speaking
from known places. **Voice recognition in company** stays strict: 90% of recordings are marked
"during motion" because idle movement is continuous, which raises the match bar by .05 (owner
ruling: lower confidence, never discard); four of the owner's lines at .52-.53 missed it.
Lowering that needs a labelled recording with a second voice.

1326 of 1327 blocked-I/O checks pass (GUI import: PySide6 not installed); new checks in
`tests/test_company_repairs.py`, `tests/test_face_turns.py` and `tests/test_floor_feedback.py`.

## Reply context diet and longer plans — October 17

Where the money went in the five runs since October 15 (token records, 52 minutes, about
$1.79 an hour, Terra's cached tokens priced at the full rate): the goal planner $0.85 (20
calls), the joke writer $0.35 (44 calls), replies to the owner $0.37 (122 calls at about 44,000
input tokens, half of it cached), Rex's own beats $0.11, result continuations $0.09. The
beats that are talked over or skipped are not the cost. Owner: keep the writer's room; put
the reply context on a diet; make plans last longer.

**Diet** (synthetic checks; token effect **unmeasured live**):

- About 6,200 characters of fixed field explanations ("meaning" strings) and about 3,000
  characters of situational rules rode in every request after the history, where nothing is
  cached. They now travel once in the persona instructions (`field_guide.py`, CONTEXT FIELD
  GUIDE and SITUATIONS); the request names the situations that apply and the adapter strips
  any registered meaning from the JSON. The builders still attach them, so replay reports and
  fixtures are unchanged, and an unregistered text still travels.
- The 53 famous voice ids sit in a static block before the history, where the cache prefix
  holds, instead of in the changing context.
- Claim rows carry the fact, who said it, status and when it was told (about 250 characters
  each instead of 570 with source track, turn, revision and raw times); people rows carry
  id, name, aliases and status; prior-session exchanges carry the words (400 characters at
  most), when and who spoke instead of the whole database row; the current session's
  exchanges are ids only (their words are already in the history); past opportunities are
  the last ten with 240 characters of spoken text instead of sixteen with 1,200.
- Every `model_request` now logs `context_chars` (characters per context part, largest
  first), so the next run shows what the context costs and what is still large.

**Plans last longer.** A plan may carry up to ten beats and the planner is asked for six to
ten (`MAX_STEPS`); four-to-six-beat plans were used up in about two and a half minutes each,
so the planner ran every 2.6 minutes. More beats cost output tokens only. Not changed: the
writer's room, its caps, the restart revalidation call.

1315 of 1316 blocked-I/O checks pass (GUI import: PySide6 not installed); seven new in
`tests/test_context_diet.py`.

## Ignored means move on — October 17

Owner ruling, replacing the October 3 easing: when two of Rex's spoken lines in a row go
unanswered, the current goal ends (`ignored`) and a fresh one is planned at once, so he is doing
something new within the planner's 8-second floor; the planner is told `engagement.ignored` and
may open the new goal with one short self-deprecating, roast-style jab about being ignored, then
something new and mostly silent. The 30-second and 60-second quiet a spoken beat used to need
after two and four unanswered lines is gone. Bounds kept in code: one such replan per silence
(`ignored_jab_spent` tells the planner the jab is used; a second ignored goal just carries on until
somebody speaks), the five-second lull floor, and the planner's "mostly silent doing" guidance at
two or more unanswered lines. Synthetic checks only (`tests/test_agency_plan.py`); the cost of an
extra planner call per ignored stretch is unmeasured live.

## Plan review repairs — October 17

A review of the October 14 plan's commits against the five robot runs since October 15
(`tools/baseline_report.py --since 2026-10-15`: 52 minutes, owner alone). Reply wait median
3.56 s (3.38 s on October 16) against 4.99 s before, 30 of 108 over 4 s; 0 re-greetings; but
343 model calls an hour against 262 (about $1.89 an hour against $1.36; 44 writer's-room
batches with one detected use), 14 of 59 planned lines heard, the floor-memory recheck never
fired in 34 trips, and no "wait for a better moment" by the performer. Seven code repairs
(synthetic checks; **not yet run on the robot**):

1. **Company is seen by the picture itself.** Two faces in one view now mark company in
   `present.py` as they are seen, and the state owner feeds each turn's faces to the picture
   before any speaker deduction. Before, company was recorded only when a reply or beat
   context was built, so a second person seen between turns left "alone with the owner" true.
2. **The voice carry rule is tighter.** The clip's own session track no longer escapes the
   other-voice check unless it holds only this clip or was already credited to him, so a
   newcomer's second sentence joining a track that was never his is not credited to him; two
   faces in view or company within ten minutes end the carry. One far unrecognized face still
   does not (owner, October 16). A newcomer's very first clip still rests on the .25 floor.
3. **Streamed words finish when the turn then fails.** Reply words spoken as they stream
   (step 5) used to be cut mid-word when the response then broke the protocol (two tool
   calls, a bad drive target). They now play out, the turn is still recorded as failed, and
   nothing is answered twice (`speech_finishing_after_failure`).
4. **"Already spoken with" is the person he answered**, not everyone recognized within ten
   seconds of his line: a known guest who walks in while Rex answers the owner is still greeted.
5. **Writer's room.** The reply names the piece it used (`used_material` on the reply tools), so
   a reworded bit is recorded; wording overlap stays as the fallback. After a turn that went
   wrong (a refused or incomplete move, a failed look, a rejected tool) no material is
   written, since the October 16 writer pitched Rex's own navigation failures.
6. **Things in the picture** are placed on his left, middle or right from their box instead
   of the ranking phrase "only detected object of this category"; a pet is not listed twice.
7. **A block taken at face value is logged** (`drive_doubtful_checked`: remembered free
   frames and returns at that spot) so the next floor run shows whether the recheck rule is
   too strict or simply never met.

1308 of 1309 blocked-I/O checks pass (GUI import: PySide6 not installed); nine new checks in
`tests/test_present.py`, `tests/test_speaker_presence.py`, `tests/test_tool_text.py`,
`tests/test_jokes.py` and `tests/test_local_map.py`. Not changed: the writer's 45 s and
30-an-hour caps, planner cadence, the 0.35 s endpoint. Still open from the review: cost per
hour against the budget, and whether the writer's material is used at all, which the new
`used_material` field now makes countable from the trace.

## Memory repairs — October 16

The October 14 plan "in five minutes I'm going to the store to get wine" was asked about in three
runs (October 15, twice on October 16) although the owner answered and finally said "Stop asking me
about it"; and in session `3af18a12` Rex said he had never been told the owner's favorite show
(Voyager, said September 30 and October 11) and muddled a name. Causes, from the logs and the
database (8 saved facts, 660 conversation exchanges with the owner):

- A follow-up was recorded as asked only through `use_memory_opportunity`; planner beats asked
  through ordinary replies, so it stayed due for a week. Answers changed nothing stored.
- A "stop asking" boundary lived in memory and was gone after a restart.
- Conversation search read only the newest 200 exchanges (Voyager was 329 back), matched whole
  words only, and with no match returned the newest unrelated chats.
- "What do you know about me" read saved facts only; replies saw only the newest 6 facts.

Repairs (synthetic checks, temporary databases; **not yet run on the robot**):

1. A heard planner beat whose evidence is a follow-up records it as asked (`followups_asked`,
   trace `followup_asked`). A callback with no time is brought up once, not once a day.
2. A fact whose follow-up was asked says so (`followup`), and a passed, asked plan is marked not
   current; the planner is told never to ask about it again.
3. `topic_avoid` feedback is saved per person (`topic_boundaries`) and carried into later runs
   until retracted; forgetting the person removes it.
4. Search with words reads every earlier conversation with the person, by word forms ("stars",
   "star") and near spellings (a name misheard by a letter or two, half weight), and says plainly when nothing
   matched. A time window ("last night") is still returned whole.
5. `recall` takes an optional `query`: earlier conversations about that subject come back with the
   saved facts (private memory, same audience rules as `recall_conversations`). Every saved fact
   reaches each reply.

The existing wine follow-up's one "after its time" occasion is still unrecorded in the live
database, so it would be offered once more. 1289 of 1290 checks pass (GUI import: PySide6 not
installed), nine new in `tests/test_memory_repairs.py`; one older search check now expects only
matching conversations. Backfilling facts from old conversations and memory for other people are
left for later (owner).

## Memory for other people — October 17

On October 16 the owner told Rex several things about his partner. They were saved only as the
owner's own facts and one was lost; asked whether he knew the partner, Rex searched only the owner's
chats. The partner had a name-only record from an earlier visit, but names matched only exactly and a
fact about someone else needed their name in that same sentence. Follow-up generations have no tools,
so the link has to be made in the saving call itself. (Examples below use a made-up "Sam Rivera".)

- A fact may name one other person (`other_person`, stored as `claims.mentions_id`): "Sam is my
  partner" stays the speaker's fact and is linked to Sam. Something about only Sam is `third_party`
  about him, said by the speaker, never Sam's own words.
- The state owner links a name someone said, capitalized, in this utterance or this conversation's
  last ten minutes ("he" right after "Sam"), to the one known person it fits ("Sam Rivera" fits "Sam"
  and is kept as another name for him). A name fitting two people, or sharing a word without fitting
  ("Sammy Rivera"), is refused so Rex asks. A name fitting nobody makes a name-only record: no voice,
  face or enrollment; only that person's own introduction starts learning them.
- `recall` with a name returns each person it may mean: the sentence they were first named in,
  whether voice and face are learned, and facts about or linked to them with who said each.
- Forgetting a person removes facts linked to them and blanks the sentences those came from. A
  recognized speaker may forget someone known only from what that speaker told Rex (no voice, face,
  voice collection, own conversations, or facts from anyone else); otherwise as before.
- Unchanged: ordinary chat lines that merely mention a forgotten name stay in the teller's own
  conversation history; facts about others are not audience-restricted (same as every saved fact).

Checks: eight new in `tests/test_other_people_memory.py`, synthetic voices, made-up names and
temporary databases. **Not yet run on the robot or against the hosted model.** The live database was
repaired once (backup first): the partner's record gained his full name, the partner facts were
linked to him, and one lost remark was saved as the owner's statement about him.

## Owner facts backfilled from old conversations — October 17

A one-time review of every stored line (660 dated exchanges plus about 1,570 older undated turns,
read from a SQLite backup copy; no hosted model calls) proposed 16 lasting facts the owner had told
Rex but that were never saved. The owner approved all 16 from a preview showing each fact, the exact
words, the date and the speaker evidence; they were written with `record_claim` /
`remember_interest` / `remember_followup` rules (self-reports by the owner, each tied to its original
source turn and time; backup taken first). Live facts went from 8 to 24: favorites, hobbies and
projects, a pet, home, and two upcoming plans as follow-ups. Four came from lines where Rex had not
settled who spoke; the owner confirmed them in review, and their original speaker evidence is
unchanged. Not saved by choice: sensitive topics and facts about other people. The review files and
ledger stay in ignored `local/memory-backfill-20261017/`.

Found in the backfill and repaired October 17: interests were saved under the model's label
("interest", "likes"), and a second interest with the same label read as contradicting the first, so
it was demoted and refused. Each interest now gets a label of its own (`interest: <topic>`), and an
interest said again is reported as already saved instead of an error; single-valued facts ("lives
in") still conflict. Existing saved interests keep their old labels. Three new checks in
`tests/test_memory_repairs.py`; 1299 of 1301 pass (GUI import: PySide6 not installed; the intermittent
operator-servo dashboard check).

## Driving repairs from the October 16, 7:33 PM run

Session `03bc4e1f` (real hardware, 8 minutes): the spoken stop halted a turn at once and was
confirmed; replies median 3.29 s, 1 of 19 over 4 s. Repaired (synthetic checks, simulated base;
**not yet run on the robot**):

- **Arrival lines are written on arrival.** He said "I'm heading toward the doorway." after
  driving 3 m to it: the plan's `say_after` was written before setting off. After a completed
  approach that travelled, the arrival line now comes from one short follow-up call that sees the
  receipt, with the planned line as intent. Plain moves and turns still say `say_after` as written.
- **An empty spoken line means nothing to say.** "Turn 60 degrees to your left" got 11 s of
  silence: with `say_before` first the model wrote `""`, the plan was rejected as invalid text, and
  the explanation call produced nothing for 9 s. Blank optional lines of any tool become null
  before validation.
- **His own plans never back up** until the rear depth sensor (bought October 16) is fitted: a
  beat "to give Brett room" reversed 0.4 m into a chair the two narrow rear sensors missed. A
  person can still ask him to back up.
- **A stopped step is logged** like a finished one (heading change, seconds, odometry, clearance):
  a turn stopped after 9 s at 0 degrees had left no trace.
- **A move's words never claim a save.** "Ah, the entryway, got it" came with a turn toward the
  owner and nothing was saved; the room tool now says to use it even when he also wants to turn or
  reply, and drive lines may not say he saved or will remember anything (instruction only).

Not changed: an own beat that approached an object no longer offered failed silently (0:36).
1280 of 1281 checks pass (GUI import: PySide6 not installed), five new in
`tests/test_drive_repairs.py`; one older approach check now expects the arrival follow-up.

## Stop while driving — October 16

Session `3af18a12` (real hardware): on the way to the microwave, about to hit the kitchen island,
the owner said "Rex, stop moving." and then "stop" several times; Rex kept driving and nothing was
acknowledged. The first phrase was transcribed correctly 41 s into the drive but missed the strict
stop list ("moving" was not an allowed word), so it queued as conversation behind the two-minute
drive. The conversation queue was already full of unclear sounds, so the speech-recognition worker
blocked on it, and every later capture (five in the next 100 s) was dropped untranscribed.

Repairs (synthetic checks; **not yet run on the robot**):

- The strict stop phrases also accept "stop moving/driving/turning", "stop it/that", "stop right
  there", "hold it" and "don't move", still only as a whole utterance ("Keep moving", "Wait." and
  "Stop by the store later" stay conversation).
- Speech recognition never waits for the conversation: transcripts that do not fit wait beside the
  queue (`waiting_turns`, eight at most, unclear sounds dropped first) and move in, in order, as
  room appears. A stop said while he drives is transcribed and halts the wheels at once.
- When a spoken stop halts a drive he says "Stopped." (not after "be quiet" or shutdown); a further
  stop cuts that line too.

- Owner ruling: while he is driving, a warning that he is about to hit something ("you're going
  to hit", "gonna hit", "about to hit", anywhere in the sentence) stops him like "stop", and he
  says "Stopped." Standing still, the same words are ordinary conversation.

Other speech during a drive is still answered only after the drive ends (owner: not anything, only
stops and warnings). 1275 of 1276 blocked-I/O checks pass (GUI import: PySide6 not installed),
nine new in `tests/test_stop_while_driving.py`; one older check now expects the waiting list
instead of a blocked worker.

## Reply speed: where the wait goes, and four repairs — October 16

Saved-log analysis (17 robot sessions, October 5–15; real hardware, no waveforms). A typical
reply on October 13 (session `332b0133`, 36 replies, median 4.93 s, p90 8.65 s, 32 over 4 s):
endpoint silence 0.77 s, speech recognition 0.57 s, context 0.01 s, model to first words
2.3–3.2 s, first voice audio 0.39 s, playback start about 0.15 s. Prompt size and cache hits did
not change model time (167 single-call replies: 2.28 s uncached against 2.58 s cached); output
length did. The run was not a regression: medians fell from 5.0–5.7 s (October 5–11, 1.2 s
endpoint) to 4.4–5.0 s (0.7 s endpoint) to 4.36 s on October 15 (streamed replies, 16 of 28 over
4 s). October 13 was slow because 18 replies were moves (median 4.3 s, words waited for the whole
call), 9 needed a second model call (median 6.2 s; looks with photos 8.5–10.6 s), and one call
stalled for 10.5 s (3 of 167 calls over 8 s).

Repairs (synthetic checks; **not yet run on the robot**):

1. **Endpoint 0.35 s** (owner choice): about 0.35–0.4 s off every reply. Pauses longer than
   0.35 s now split speech into separate captures; a split before Rex is audible cancels the
   first reply, at the cost of an extra model call and a shorter clip for voice evidence.
2. **Moves speak while the plan is written.** `drive_base` and `set_robot_pose` put
   `say_before` first and stream it like reply words. It is heard before the plan is checked; a
   refused plan is then explained without repeating it. A pose still waits for it to finish.
3. **Every look or lookup starts with a few words.** `say_first` is no longer optional: the
   model left it out on all 4 looks and the 1 recall of October 14–15 (6–10 s of silence).
4. **Saves answer in one call.** `remember_followup`, `remember_interest`, `record_claim`,
   `correct_claim`, `retract_claim`, `settle_question`, `name_room`, `correct_room` and
   `note_bit` carry the spoken reply, said only after the executor succeeds
   (a candidate claim, a refusal or a null line still goes back to the model). This saves about
   1–1.5 s and one model call each. While told to stay silent, the save stands and the words are
   withheld. Identity tools (introductions, renames, corrections of who is who) are unchanged.

Estimated from the logs, not measured: the shorter endpoint alone takes October 13 from 32 to
23 of 36 replies over 4 s and October 15 from 16 to 14 of 28. 1260 of 1262 blocked-I/O checks
pass, ten new in `tests/test_reply_speed.py`; the two others are the GUI import (PySide6 not
installed) and an intermittent dashboard servo timing check that also fails without these changes.
Live reply timing, whether a mandatory lead-in sounds repetitive, and how often 0.35 s splits
a sentence need a robot run.

First live run with these repairs (session `511ce677`, October 16 evening, real hardware): median
reply 3.38 s, p90 4.48 s, 5 of 34 over 4 s (32 of 36 on October 13). Problems found and repaired
the same night (synthetic checks; **not yet run on the robot**):

- **Forward moves refused at "3 cm" with nothing there.** The front depth matrix's bottom row read
  12-31 mm, under the 70 mm floor threshold, so host and firmware both stopped him (firmware zone
  `stop` in 36 of 56 live frames). Cause: a peeling sticky cover on the sensor, removed by the
  owner; the live re-read showed the floor at 270-430 mm, firmware zone clear in 56 of 56 frames.
  Phantoms like this appear in logs from October 13. No code change.
- **Every look failed (`MotionResourceBusy`).** The mandatory lead-in was still playing with its
  talking motion, and a look gave up at once. A look now waits up to 8 s for words already playing.
- **`set_initiative` answered "raise your head" three times** once it carried a spoken line,
  switching quiet mode on while promising a move. It is back to a result-then-reply tool.
- **A refused move ended the attempt** ("go look around": one refused left turn, right side open).
  A refused or incomplete requested move may now be replanned once with `drive_base` only, never
  opposite to a direction the person named; a second refusal is explained.
- **After a look that took no picture he described things** (a clear path, a telescope). Failed
  looks now carry an instruction to say so and describe nothing.

During a self-initiated 45° turn at 1:54 the wheels turned about 133° while the body turned 43°.
The owner reports no contact with the bookshelf behind him and a wheel that has been slipping
lately, which matches; he will test the wheel. No code change. 1266 of 1267 checks pass (GUI
import: PySide6 not installed).

## Rex's voice model: ElevenLabs v4 Turbo — October 16

Owner decision: `eleven_v4_turbo` replaces `eleven_v3_conversational` at the same API price
($0.04 per 1,000 characters). Request, voice, stability, seed, tags and leveling are unchanged;
cached startup lines are keyed by model and re-synthesize on their own. `tools/check_tts_latency.py`
(hosted, synthetic text, this Mac, no playback, 15 takes each) measured first audio at median
0.19 s versus 0.235 s (p90 0.22 versus 0.26 s): about 44 ms, roughly 1% of the 5 s reply wait.
Voice character, tag delivery and loudness on the robot are **not yet checked by ear or live**.

## Who is talking: a sure voice carries through the conversation — October 16

Session `2f85db5c`: alone with Rex, 15 of Bret's 43 trusted turns were attributed to him; 15
more went unresolved because Rex was moving while he spoke (scores .15–.60), and the presence
rule needs his face recognized and no motion. Owner ruling: once Rex is sure it is Bret, it is
Bret for the rest of the conversation, even too far for a face match; other voices score
differently and must not be tied to him. After two acoustic matches in a conversation (five
quiet minutes end it), a later trusted clip is `inferred` as him when he is its best match at .25
or more with a clear lead, its own trouble is weakness rather than conflict (motion, a short or
unlabeled clip; never echo, overlap, in-clip disagreement or closeness to two voices), nobody
else is recognized in view or within ten minutes, and no other voice was heard in ten minutes.
Same exclusions as presence: no learning, enrollment, rename or forget. The .25 floor: clean
clips of other voices and the TV scored .19 or less against his profile in every saved run.
Replaying logged scores (no audio was saved): `2f85db5c` 15 → 30 of 43; the last 14 runs 67 →
117 of 300, with the other-voice check approximated from the logs; 3 clips of a run where
another person was talking would have been credited to Bret without that check. Synthetic
contract checks; wrong-person rate with a second person talking off camera still unmeasured.

The October 14 wine fact, rewritten word for word during the room question, was restored to
its original claim and time (told October 14 at 11:45 p.m., due 11:50 p.m.); the copy is
retracted. Backup: `local/core.before-wine-repair-20261016.sqlite3`.

## October 15 runs: servo freeze, rooms, the dishes bit, stale plans

Two live runs that night. In `24279950` every servo stopped two minutes in and stayed stopped
until restart; `2f85db5c` (16 minutes) went well. Four repairs:

- **Servo freeze.** A kept throttle arm coming out after a drive also sent the hero elbow home
  while idle motion was moving it; the walk-out timed out after 25 s and one timeout faulted the
  whole servo owner. The walk-out now moves only the kept arm, and a joint that does not arrive
  during awake motion is held where it stopped while everything else carries on
  ([details](SERVO_INTEGRATION.md#october-15-one-stuck-joint-no-longer-freezes-every-servo)).
- **Room recognition never started.** The camera hub took three consumers and places was the
  fourth, refused at every start since October 11; Rex could not say which room he was in. The
  hub takes four. About 470 MB and 17% of one core at the 1.5 s pace, measured in a fresh
  process; live contention unmeasured ([details](ROOM_RECOGNITION.md)).
- **The dishes bit came back.** One plan held two bits about Bret's counter work. He said "I'm
  just doing the dishes", Rex said "Then I'll let you get on with it", and the second bit ran
  40 s later. A beat planned before the person last spoke now sees Rex's own answer beside
  theirs, with: keep your word if you said you'd leave them to it, and a second bit on something
  already joked about is spent. Paid probe with fictional Sam (`check_agency_beats.py
  --scenario busy_with_chore`): the second bit was spoken in 4 of 4 runs before, 0 of 6 after;
  the ordinary conversation probe still spoke 5 of 6 beats in each of 2 runs (6 of 7 on October 14).

- **Last night's wine run treated as current.** "In five minutes, I'm going to go to the store to
  get wine" (October 14) was still a current fact on October 15: the joke writer pitched a
  "late-night wine run" in 6 of 13 batches. Saved facts had no wall-clock time, and a same-words
  "correction" during the room question had moved its source to that turn and dropped its
  follow-up. Facts now keep when they were told (`claims.said_at`; older ones fall back to their
  recorded exchange, else "unknown"), and the reply model, planner and joke writer see `told` and,
  for a plan with a time, `planned_time`, with one rule: a plan tied to a moment is over once that
  moment has gone by. A correction that changes nothing is refused (`CorrectionUnchanged`). The
  existing wine fact was restored to its original time on October 16 (see above).

Synthetic checks and the paid text probe only; none of these has run on the robot.
1244 blocked-I/O checks: all pass except `test_boot`'s import sweep, which also fails before
these changes because PySide6 (the dashboard) is not installed in `.venv`.

## Rex's personal history — October 15

Session `34d84a03`: asked about his old trips to Endor, Rex called his past "fictional backstory,
not a real travel log" and did not know R2-D2 was his navigator. His persona had one line of history
and called his past mere flavor. Now a researched, owner-reviewed history is in code
(`personal_history.py`) and Rex looks it up by section, in his own voice, both in character and as a
park goer would know the ride; the persona says his past is his own. Synthetic checks only; not yet
tried with the hosted model or on the robot.

## Wine-run follow-up repair — October 15

Sessions `34d84a03` and `ddb68ec9`: told "in five minutes I'm going to the store to get wine",
then restarted five minutes later, Rex never asked. His startup hello, the one place the
follow-up was offered, was cut off 0.1 s in by his own motor noise heard as "Oh.". The 45 s
pause before another hello let a pet remark take the moment. His plan then called the trip
"imminent", because follow-ups carried no time. Repaired, synthetic evidence only: saving a
follow-up takes a due time (minutes from now or a local date/time, resolved by code) and keeps
when it was said; each turn the clock marks it `ahead` or `passed`, and the prompts say passed
means ask how it went, never assume it happened. Passed follow-ups lead the offered list and the
planner's first spoken beat. A hello cut off by a sound that is then thrown away (no trusted
turn) is retried at once, once per visit; fuzzy times are due at the end of their window
("tomorrow morning" noon). 1201 blocked-I/O checks pass. Not yet run on the robot or with the hosted model.

## Who is talking: presence layered over voice — October 14

With only Bret in view, live runs tagged 23 of 81 of his turns as him. Two acoustic repairs:
phonetic windows need .35 rather than the full .50 (profile growth still needs every window),
and two tracks of one voice no longer make each other ambiguous. Then, by owner ruling, the
state owner infers the speaker (`inferred`) when one recognized face is alone in view, nobody
else has been around for ten minutes, no other voice was heard this visit, and the voice's
clear best match is that person at .40 or more. Inferred speech can save claims, plans and
interests; it never feeds voice or face learning, enrollment, rename or forget.
Evidence: real CAM++ on saved audio from all runs (speech intervals rebuilt by an energy
detector, current 3-clip profile): with one recognized face, 17 of 72 turns before, 39 after
the acoustic repairs, 45 with inference; 0 inferences on the 140 other turns. Synthetic
contract checks for each gate; 1224 blocked-I/O checks pass. **Not yet run live**, and the wrong-person rate is unmeasured:
no recording has another person speaking off camera while Bret is in view.

## Greeting variety — October 14

Every launch greeted the owner with the same formula. The model now sees its last heard
arrival greetings across runs (`personal_memory.earlier_greetings`) and greets a return
like a return, as V2's "you're back" did; the startup path shares the arrival guidance.
1215 blocked-I/O checks pass (synthetic only); **not yet run live**. Details: [conversation memory](CONVERSATION_MEMORY.md).

## Dashboard — October 14

`live --gui` opens V2's PySide6 dashboard, adapted to V3. It observes the trace and the
owners' snapshots, and its manual servo override and joystick act through the servo and
drive owners (operator mode, teleop holding the plan lock). Synthetic contract checks and a
native render with synthetic data only; **not yet run with Rex live or on the robot's M2**.
Details: [dashboard](GUI.md).

## Second live run — October 14 (evening)

Session `1662b007`: Toby greeted as Max from a cut-off look; asked what he was looking at after his own
planned look up, he recited a fresh view ("a man sitting by the couch ... in this frame") though face
recognition knew the owner. Repaired, synthetic and paid-probe evidence only: the picture of the present
now says where his head points, why, who is where in his wide view and what is in the middle of it; the
persona treats the camera as his eyes and recognized faces as people; uncertain pet looks ask, and
corrections are saved. Details: [improvement plan](IMPROVEMENT_PLAN.md#second-live-run-what-he-was-looking-at-and-the-dog-october-14-evening).

## First live run of the improvement plan — October 14 (evening)

Session `e1f8b2c6`, 11 minutes on the robot: mostly successful by the owner's account (Max
recognized, reoriented to him after he moved), not funny enough. Seven repairs the same evening,
synthetic and paid-probe evidence only: a line on arrival after his own silent approach; a
requested back-up no longer cancelled by the private-memory guard while moving; someone already
talked with is not greeted as arriving; local time and his earlier lines in the picture of the
present (one clock slip had been owned four times); a writer's room aimed at the next turn and a
persona that answers admissions; a spoken lead-in while looking or checking; 4–6-beat plans.
Details: [improvement plan](IMPROVEMENT_PLAN.md#first-live-run-and-repairs-october-14-evening).

## Poker arm tail sway — October 13

Owner request: the poker arm's idle moves like a cat's tail. It sways back and forth across its
awake pose for 10-25 s (1.7-2.3 s per half sway), settles back to that pose, then rests still for
15-40 s before the next bout (constant swaying was too much). The wrist bends against the elbow
and trails it, arriving 0.5-0.8 s later. While it sways, the head's face-following re-aims about
every 2.5-3 s. 1,135 blocked-I/O checks pass (simulated servo board only). **Not yet run on the robot**
([details](SERVO_INTEGRATION.md#october-13-the-poker-arm-sways-like-a-cats-tail)).

## Mid-conversation freeze — October 12

Session 566c9154 froze after "Talking.": Rex chose silence, and saving that choice checked
the faces in view (the private-memory audience guard) inside the memory transaction while
the face worker held the camera lock waiting to read face profiles from memory. Each waited
for the other; the microphone loop and then shutdown waited on the conversation lock, so
SIGTERM's stop reached shutdown but shutdown itself hung until SIGINT. The face worker now
reads profiles outside the camera lock and discards that frame if a forget cleared faces
meanwhile. The idle-arm change of that evening (8fcb11e) takes none of these locks and is
not involved. A check through the production reply path reproduces the hang without the
fix (synthetic; not yet run live). Shutdown no longer waits on stuck work: it signals every
worker to stop, waits at most 0.5 s for the conversation and control locks and 2 s for workers,
parks the servos, stops the wheels and lights, then ends the process. A worker left behind is
named in `worker_stuck`. Synthetic check only.

## Listening cue — October 11

When a trusted transcript is back, Rex's head cocks to one side and holds about 3 s while the
reply is prepared; the reply's talking motion straightens it. It shows he heard during the
roughly 2.5 s before first audio; it does not shorten the wait. Synthetic checks only; not yet
run on the robot ([servo integration](SERVO_INTEGRATION.md#october-11-the-listening-cue-head-cock-while-he-thinks)).

## Reply latency — October 11

Owner report: replies feel slow. In the last 12 robot sessions (October 5–11, 129 replies to
human speech) the median wait from end of speech to first audio was **5.1 s** (p90 7.2 s) and
97% exceeded the owner's 4 s bar. Typical stages: endpoint silence 1.27 s, ASR 0.54 s,
hosted model 2.5 s to a usable reply (about 1.8 s for plain prose, 2.5–2.9 s when the words
arrive inside a tool call), then 0.6 s of TTS and playback.

Changed (simulated checks only; **not yet run on the robot**):

- A gesture chosen to start with a line (`with_speech`, formerly `before_speech`) now runs
  while the line is synthesized and played. Finishing it first had held speech back by about
  2.6 s in 3 of 11 gesture replies (up to 8.3 s total). Talking motion takes over when it ends.
- Full conversation history now evicts to two thirds in one block. Per-turn eviction had left
  every reply after about the eighth exchange uncached ([prompt caching](PROMPT_CACHING.md)).
  Cached and uncached replies took the same model time in the recorded sessions, so this is a
  cost repair, not a measured speed-up.

- Endpoint silence is now 0.35 s (owner choice, October 16), after 0.7 s (October 11, the
  owner's measured average thinking pause) and an untuned 1.2 s September 10 start. It takes
  about 0.35 s off every reply wait (see the October 16 reply-speed section). Speech that resumes before a reply is
  audible still cancels it, and the model can still wait on an unfinished thought. Longer
  pauses now split into separate captures (shorter clips for voice evidence); how often that
  happens and whether Rex starts talking over resumed speech need a live run.

Removing the gesture stall lowers the recorded p90 to about 6.8 s; the shorter endpoint
should take about 0.5 s off every reply, putting the expected median near 4.6 s. The rest is
the model's first words (about 1.8–2.5 s), ASR and 0.6 s of voice output. 1,105 blocked-I/O checks pass.

**Hosted probe (October 11, paid, synthetic content, 6 paced requests per arm, gpt-6-luna/low,
plain-text answers; private receipts in `local/validation/latency-probe/`).** Median time to
first streamed output: 0.83 s for a 56-token request, 0.95 s for persona plus synthetic
history and context without tools (13,400 tokens), 1.81 s with the full static tool list
added (26,250 tokens, uncached) and 1.35 s for the same request with 22,500 tokens cached.
Sending the 42 tool definitions costs about 0.9 s uncached or 0.5 s cached on every reply;
prompt size without tools costs little. One round had 4.9 s and 10.8 s outliers on the two
small arms, so provider stalls contribute to the tail. Production requests are larger
(30,000–44,000 tokens) than this synthetic one.

## Arms stay tucked beside obstacles — October 11

Owner request. After a drive, the poker arm stays raised at its drive stance while either
left side sensor reads anything within 3.5 ft (1.07 m; the arm reaches about 2 ft), and the
throttle arm stays parked while anything is within 2 ft (0.61 m) in front (front ring sensors
and depth matrix). A missing reading keeps the arm in too. Once the space has read clear for
2 s he puts the arm out; meanwhile idle and talking motion leave it alone and a pose for it
is refused. The hero elbow always returns. 1,100 blocked-I/O checks pass (simulated base and
servo board only). **Not yet run on the robot**
([details](SERVO_INTEGRATION.md#october-11-arms-stay-tucked-beside-obstacles)).

## Room recognition and exploring — October 11

Owner request, built on V2's room model. Rex recognizes which room he is in from the camera
(V2's MobileCLIP-S2, run locally), asks what an unfamiliar room is called, learns it when told,
and remembers which rooms he has moved between. A new `explore` goal has him drive around,
look and comment in a new room. V2's three taught rooms (dining room, workshop, living room)
were imported. On saved September frames they score just under the confident bar (likely the
newer wide lens), so Rex will at first be unsure and ask. About 300 MB and 0.25 s per frame on
the M2; no cost. 1,095 blocked-I/O checks pass. **Not yet run on the robot**; off until
`place_recognition_enabled` is set ([details](ROOM_RECOGNITION.md)). October 15: with it set,
it had still never started (the camera hub took only three consumers); fixed, see below.

## Searched news and interest news — October 11

Owner request, modeled on V2 with the BBC feed kept. Once a day a web search picks about
five varied, dated stories (at most one about AI or tech, enforced in code); Rex saves
interests people state about themselves (`remember_interest`) and, while they are in view,
searches up to four of those topics a day for fresh news to bring up with them. Code states each event's day
relative to today. 1,082 blocked-I/O checks pass; one paid probe kept 5 of 5 daily stories and the
interest story (about 7.6 cents together). **Not yet used in conversation on the robot**
([details](OBSERVATION_SOURCES.md#searched-daily-stories-and-interest-news-october-11-2026)).

## October 10 afternoon runs: the new head and poker arm on the floor

Runs `f07e53af`, `6ae27b19`, `6b8fa000` and `b954c14c` (real hardware). The two-servo
head, its looks and tilts, and `poker_up` reached their pulses. Repaired the same evening,
all with synthetic checks only: the poker arm and hero elbow now walk back to their awake
poses after a drive (only the throttle did); the head-lift park is 2624, the board's
floor, so shutdown no longer times out on it; lone depth-matrix zones no longer block the
path (a "come here" stalled at 6 cm with every column reading open); blocked moves log the
clearance that stopped them; and the tool text makes "turn left" a wheel turn, never a
head look (two corrections in a row were answered with `look_left`). The 15:22 refusals
were the front-right ring sensor's phantom, not software. Hosted planning check results
for the turn wording are recorded in the drive doc. **Not yet run on the robot**
([drive base](DRIVE_BASE_INTEGRATION.md#october-10-evening-stalled-beside-the-couch-turn-left-as-a-head-look-arms-after-a-drive),
[servos](SERVO_INTEGRATION.md#october-10-two-servo-head-poker-elbow-and-wrist-inverted-visor)).

## Voice impressions — October 6

Owner request: port V2's cloned-voice impressions as a model tool. `impersonate` lets the
conversation model perform a short parody in a famous voice, a known person's voice or the
current speaker's own voice (once six clean voiced seconds are on file), framed by lines in
Rex's voice, and slip in rare budgeted cameos when someone mentions a person he has a voice
for. The local Breeze TTS 2 engine renders each take completely before playback and shares
one MLX lock with transcription. 1052 synthetic checks pass; the real engine synthesized
takes to files on the robot (about 1.5 times real time, 2.9–3.7 s load, 6.4 GB peak MLX
memory); a five-call hosted probe chose the tool with a good script. **No live playback
yet** ([voice impressions](IMPERSONATION.md)).

## Hallway crawl — October 5 (evening)

Run `4153bcbc1be24457ae3ce29cf29e2b09` left Rex stopped in a 1.2 m hallway; a live sensor
read (no motion) showed him angled into the right wall. The host had been stopping every
leg within 0.3 m while the firmware's own wall steering was straightening him. Tight space
is now one slow continuous crawl: the firmware keeps steering, the host adds the slant of
the wall beside him and what the depth matrix sees ahead, sizes the turn before moving by
that slant, and stops only for something in the path, a wall about to touch, or no headway.
Refused turns are logged. Simulated in a geometric hallway and a 0.8 m doorway with the
firmware's steering modelled; **not yet run on the robot**
([drive base](DRIVE_BASE_INTEGRATION.md#october-5-evening-stuck-in-a-hallway-with-the-door-still-to-come)).

Same evening: the floor-look picture now waits 1.5 s for the head to be still and keeps
the least shaken of a short run of frames, and a picture asked for after a drive holds
idle head motion until it is taken (the logged "camera view failed" was real: idle motion
had started 10 ms after the stop)
([drive base](DRIVE_BASE_INTEGRATION.md#october-5-evening-second-repair-blurred-floor-looks-and-the-picture-that-failed)).

## Kitchen and hallway run repairs — October 3 (midday)

The owner's run `d4634610d3c34bcda70ccfa1a1a893b3` (about 18 minutes, real hardware)
exposed four faults. All repairs are software, checked with simulated hardware; **none has
run on the robot**. One read-only live sensor reading was taken at the owner's invitation
(no motion; see the drive document).

- **Forgot the morning's plan and denied hearing it.** Memory access required recognizing
  the owner's face at that instant, and Rex's own movement keeps restarting face tracks.
  A recognized person now remains the private audience for up to 90 seconds unless someone
  else appears ([conversation memory](CONVERSATION_MEMORY.md#october-3-the-same-visit-survives-a-restarted-face-track)).
- **Hallways blocked every forward move.** Walls beside the path no longer count; pointed
  at a wall he turns away first, goes slower in tight spaces and is not refused while the
  way straight ahead is open ([drive base](DRIVE_BASE_INTEGRATION.md#october-3-hallways-the-lost-search-and-turn-sizes)).
- **Returned to the microwave when told "come towards me"; turns the wrong size.** A
  mistyped target id no longer becomes the old search, arriving ends a search, a dictated
  route is not replaced by the route planner, target bookkeeping follows the IMU instead of
  slipping wheel odometry, and "slight" means about 45 degrees.
- **Head frozen until the arm un-parked.** The head and other servos are released when the
  wheels stop; the throttle arm returns in the background
  ([servo integration](SERVO_INTEGRATION.md#october-3-the-head-is-free-the-moment-a-drive-ends)).

Second run `aa6782c09e1c42bb98390c4ba44c27be` (afternoon, with those repairs loaded):

- **"Come over here" refused, then a second turn left him facing away.** Approaching the
  nearest person now looks for them (last voice, radar, around) instead of being refused or
  giving up; the last voice is kept as a room heading so it stays right after he turns.
- **Firmware steered him aside near the person.** Its wall-steering is switched off for the
  last 1.2 m of an approach and restored after; it still slows and stops him.
- **Servos still seemed to wait for the arm.** They were free but idle movement resumed two
  seconds late and not at all while he listened; it now starts the instant the wheels stop.

Third run `30cbd0250b6648d48c663fc66ec7901e` (11 minutes, with both repairs loaded): the
memory recall and the "face me" search worked on the robot. The owner's verdict: "still
struggling with logical things". From the log:

- **Waited 69 seconds for a reply.** "Well, you have eyes, don't you?" was taken by the
  model as a request for quiet (`set_initiative`), which executed without a word. Quiet is
  now always followed by one spoken line, and its description excludes teasing, rhetorical
  questions and criticism.
- **"You're a little too close" produced a turn**, and he was 21 cm from the owner's knees.
  See the [drive document](DRIVE_BASE_INTEGRATION.md#october-3-third-run-distance-from-people-and-the-voice-target),
  which also covers the phantom voice bearing that turned him away from the owner.
- **A question answered by a silent move.** "Do you have access to your compass direction?"
  got a wordless 17 degree turn. A requested drive plan with no line of its own now gets a
  follow-up reply.
- **A remark got no reply at all.** The model aimed a look at a face id the tool does not
  take; the error failed the turn. A refused look, pose, gesture, light or inspection now
  goes back to the model, which still answers.
- **Rex's own next beat leaked into replies.** "How you doing over there?" was answered by
  inspecting a clock, because the reply model was shown the next scripted beat of Rex's
  goal. Replies now see the goal's subject (and its objective only when it is about that
  person), never the next beat. The planner is also told not to state a guessed object as
  fact (it invented a telescope).

Not fixed, measured: the 15 spoken replies took 3.9-7.3 s from end of speech to first
audio (median 5.4 s) against the owner's 4 s limit, and once the conversation history
filled, 17 of 22 full-size reply calls reported zero cached input tokens on about 38,000.
The cause is not established.

Fourth run `be8c8d5584904f49b833fb6c03d33b9d` (9.6 minutes, all of the above loaded; the
owner: "I think things are improving"). **Seen working on the robot:**

- "Can you come over here?" with nobody in view: accepted, he turned 107 degrees looking
  for the owner, found him and drove 2.67 m.
- "You're a little too close to the table. Can you back up a little?": backed up 0.6 m,
  no turn. "Back up three feet": 0.85 m (rear clearance), then said so.
- After all nine drives the head moved within 0.01 s of the wheels stopping; the arm
  finished returning about 5 s later.
- No failed turns, no refused tools, no mistaken quiet.

**Found in that run and changed (simulated checks only):**

- A guest sitting beside the owner was answered "Nice to meet you too, Brett": alone in
  view for a moment, the guest inherited the owner's recent recognition. Once two faces
  have been seen together, the same-visit carry is off for ten minutes and only direct
  recognition counts ([conversation memory](CONVERSATION_MEMORY.md)).
- "This is Max" about the dog created a person named Max (provisional, still in the local
  database). A name that belongs to a saved pet is now refused as a new person.
- "I tried to check Max's saved photo, but that image reference had expired" was spoken: a
  failed close look is no longer narrated.

**Open:** he stopped 0.30 m from a coffee table between him and the couch and was asked to
back away; nine replies took 4.6-8.7 s to first audio (median 5.5 s; a tenth spoke only
after its move, at 14.2 s) and 12 of 19 full-size reply calls reported no cached input; the guest was never greeted or asked for a
name; a distance was still spoken as "0.85 metres".

Verification: **975 blocked-I/O checks pass** (90.5 seconds; fake clocks, pulses, drive and
providers). No hosted model was called for these changes.

## Looking for a way around obstacles — October 3 (evening)

Owner request after the counter and table stops. An approach that is blocked with its
target still far now photographs the floor with the head tipped down, combines the picture
with the range sensors by direction in one hosted look, takes the leg it proposes, then
turns back to where the target should be and checks. He keeps his own position while
moving, and two sightings fix where a target is. The text-only `gpt-5.6-terra` route
planner is retired. Details and limits:
[drive base](DRIVE_BASE_INTEGRATION.md#october-3-looking-for-a-way-around).

**Simulated only**, with a scripted stand-in for the hosted look: 994 blocked-I/O checks
pass (108 seconds). The hosted look has not been tried on a real picture; the first floor
run is its first test.

First floor run of it, `eef0a6efaf95497fa4a1524a9de03f9f`: the hosted look worked and he
rolled five legs round the kitchen island, but lost the microwave after 657 degrees of
turning; see the [drive document](DRIVE_BASE_INTEGRATION.md#october-3-first-run-of-the-camera-route-what-the-log-showed)
for the causes and repairs (head-first look back, three-sighting positions, turn coast,
eight legs). The same run showed two conversational faults, now changed:

- **"Leave that hand there until I say you can move it"** was answered by moving the hand
  to another pose. A new `hold_arms` step moves nothing and holds the elbow, wrist, poker
  arm and throttle arm where they are with no time limit, until `release_poses`; idle
  movement also stops swinging the microphone arm. Ordinary requested poses still give way
  to his own movement after 90 seconds. A requested drive still parks the arm first.
- **"Stop talking until I greet you again"** was acknowledged, then an "Okay." ninety
  seconds later got a spoken reply and switched him back on. A new `silent` setting allows
  only two choices per utterance, staying silent or being released; any words the model
  produces otherwise are withheld.

**1,009 blocked-I/O checks pass** (115 seconds). No hosted model was called, so whether
the model now chooses `hold_arms` and `silent` for those requests is unmeasured.

Evening runs `b0d3ef1d` and `7af54a94` (41 minutes together): the owner called them
"really good". On the robot, two of three microwave trips reported success (the third had
in fact arrived), the target was found in 53 of 53 looks, turns landed within 3 degrees 92
times in 111, and "keep your hand down and don't move it" chose `hold_arms`, which was then
cancelled 0.2 s later by someone else speaking. Changes from those logs, simulated only:
arrival is recognised when the target has grown large in view, an arrival within reach of
a person is no longer told as a failure, a failed route direction is not repeated, and
`hold_arms` cannot be interrupted. Details:
[drive base](DRIVE_BASE_INTEGRATION.md#october-3-evening-the-camera-route-works-on-the-floor).

Unchanged and measured in those runs: replies took a median of 5.0 s and 6.0 s to first
audio (4 of 46 under the owner's 4 s limit), and 56 of 89 full-size reply calls reported no
cached input. `silent` was not exercised.

**1,019 blocked-I/O checks pass** (115 seconds).

## Feeling alive: live-run repairs — October 3 (evening)

The owner's run `30ec1b046fb749558934ca55b50ffc06` (10.4 minutes, real hardware) was the
first he called a big improvement: 8 goals planned, 19 beats started, 12 self-initiated
lines heard, one self-initiated drive. His criticisms and what changed, all in software and
**none of it yet run on the robot** (details in [Rex's own goals](AGENCY_IMPLEMENTATION_PLAN.md#live-run-october-3-feeling-alive)
and [servo integration](SERVO_INTEGRATION.md#october-3-continuous-idle-life-and-gaze)):

- **Motionless, then every servo at once.** Idle movement ran 15 times in ten minutes and
  each run moved eight channels together. It is now continuous: each body part has its own
  slow rhythm and one to three parts move at a time, starting at different moments.
- **His own servo noise was heard as speech.** 6 of 10 idle moves produced a phantom
  "Okay."/"Oh.", which froze the movement, cancelled his own beats and twice cost a reply
  call. Brief sounds during his own motion no longer stop him or his initiative and need
  clearer recognition to count as a turn. Nothing is discarded.
- **Gaze.** The head follows the nearest face (V2's proportional centring), framed so the
  camera sees the person, with glances away; the model can point it somewhere instead and
  return it with `look_at_people`.
- **Slow pacing when nobody answers.** The lull is 5 s (was 8), beat pauses are shorter, an
  interrupted beat retries in 4 s (was up to 15), easing off caps at two minutes (was five)
  and delays talking only: silent moves continue.
- **Moved on his own only once; furniture over people.** The planner now looks at the
  people first (anything out of the ordinary, such as what someone is or is not wearing),
  treats decor as a last resort, includes a drive in most goals, may move without a word,
  and chooses a pace for each drive step.
- **Did not turn to a voice from the side.** A voice beyond the head's reach now prompts a
  body turn as part of the reply, unless something else has his interest.
- **Mouth animation missed some lines.** The start command was sent once per phrase over
  a link the head board is known to drop bytes on; it is now repeated.

Verification: **935 blocked-I/O checks pass** (75.4 seconds; fake clocks, pulses, LEDs,
drive and providers). No hardware ran and no hosted model was called for this change, so
the planner's new choices, the performer's use of pace, gaze and silence, the real servo
noise level and the mouth fix are all unmeasured until the next robot run.

## Goal initiative review repairs — October 3

A code review of the October 2 goal implementation found twelve problems; all but one are
fixed (details in [Rex's own goals](AGENCY_IMPLEMENTATION_PLAN.md#review-repairs-october-3)):

- **Greetings are required.** Arrivals no longer offer optional lull activities or "nothing
  to add" silence; contradictory lull instructions are removed from the greeting call.
- **No roaming limit.** A home range for self-initiated moves was added and then removed
  at the owner's direction; his own moves are bounded only by obstacles and ToF limits.
- **Easing off.** Unanswered spoken beats lengthen the wait (up to five minutes); speech
  or an arrival restores the pace. Goals are planned at most 20 s before they are due.
- **Lasting quiet.** "Be quiet", "quiet", "shut up", "hush", "stop talking" keep Rex from
  starting anything until invited back; "stop" remains a two-minute pause.
- **Pose holds age out.** A requested pose blocks his own movement for 90 s, not forever.
- **Privacy.** A person's goal is shown only on that person's turns; an unrecognized
  person inherits a known person's visit for at most three minutes.
- **Planner vision.** The planner receives the current frame even for an unchanged view.
- **Also:** floor yields pause rather than discard goals; name questions follow the person
  through track renumbering; a blip of servo noise no longer cancels a move under way;
  planner crashes back off; self-initiated seeks get 4 paid looks; tiny or weak face
  detections no longer count as newcomers.
- **Still open:** a private-memory reply is withheld, not regenerated, when another
  substantial face appears before speech starts (about 1 frame in 3,000 in recent logs).

Verification: **914 blocked-I/O checks pass** (69.9 seconds, after the roaming limit and its two checks were removed); no hardware ran.

Hosted probe after the repairs (real GPT-5.6 Terra and GPT-6 Luna, fictional inputs, fake
transports, accelerated clock), ignored folder
`local/validation/agency-e18eee25f34842b3bd33b4f380dc67e2/`:

| Case | Greeting | Goals (planner calls) | Completed beats |
| --- | --- | --- | --- |
| Known friend | "Hey, Sam! Good to see you—how's your day going?" | catch up, twice (2) | 5 |
| New visitor | "Hey, glad you're here! What's your name?" | get to know, entertain (2) | 5 |
| Room with a painting | not applicable | investigate, entertain (2) | 5 |

Unanswered lines spread out as designed (known friend: 24, 24, 43 and 80 simulated seconds
apart). 6 Terra and 24 Luna calls; about 40,000 and 550,000 input tokens. One inspect beat
recited a description of the painting after hearing its story; its instruction was
tightened afterwards and not re-measured. The rejected-humor case was not re-run. The
probe still cannot judge real perception, movement, pacing in wall-clock time or cost.

## Goal-based initiative — October 2

The October 1 plan design failed live (`f4e2bf97`: 40 planner calls, 26 plans, 2 spoken
self-initiated lines and 2 silently dropped human replies in 9.6 minutes). At the owner's
direction it is replaced by goals; see [Rex's own goals](AGENCY_IMPLEMENTATION_PLAN.md).

- **People first.** Replies never wait for planning. Arrivals are greeted directly and become
  the next goal's subject; someone who spoke within the last minute is not re-greeted.
- **Goals, not scripts.** Terra plans one goal (catch up, get to know, investigate,
  entertain) in a background thread from dialogue, saved facts and one camera frame. Human
  turns steer it; real endings only. Interrupted beats retry once; failed beats are skipped.
- **Dropped replies fixed.** Private-memory replies are withheld only for a real audience
  change, and every withheld reply is traced (`reply_cancelled`). Face-track renumbering
  no longer cancels greetings, beats or replies.
- **Motion.** Self-initiated drive limits now equal requested ones; the model sees the
  10 m plan total instead of the 1.5 m leg it misread. Committed silent moves survive their
  own motor noise.
- **Fixes found by the hosted probe.** The improvised fallback beat could crash after Rex
  drove out of sight of everyone; a look-and-comment beat that saved its description was
  cancelled unheard (also affected human "look down and tell me" requests); beats treated
  Rex's own camera frame as a picture someone sent. All three are fixed with regression tests.
- **Cost controls.** Eight seconds minimum between planner calls, one repair, 10-60 s
  failure backoff, and no new goals after five minutes with nobody seen or heard.

Verification: **903 blocked-I/O checks pass** (73.1 seconds). They use temporary
SQLite, fake clocks/providers/pulse/ESP32 transports, synthetic faces and blocked
network/audio/serial; no hardware ran.

Hosted probe on the final code (`tools/check_agency.py --live`, real GPT-5.6 Terra and
GPT-6 Luna, fictional inputs, synthetic playback, fake servo/base transports, accelerated
clock), ignored folder `local/validation/agency-7d207c1a30f24684a156baa8d704959d/`:

| Case | Goals (planner calls) | Completed beats | What Rex did |
| --- | --- | --- | --- |
| Known friend with two saved facts | catch up, twice (2) | 5 | "Sam, how's your day going so far?"; joked about the sock-stealing beagle; rolled closer on his own and asked about half-marathon training |
| New visitor | get to know, twice (2) | 5 | Waved, "Hey there! I'm Rex. What's your name?"; learned "Jordan"; rolled closer; wifi jokes |
| Room with a sailboat painting and a plant | investigate, twice (2) | 5 | Turned and looked at the painting ("absolutely no intention of taking the scenic route"); answered "my grandfather painted it" with "Did he paint it from memory, or out on the lake?"; drove to the plant |
| Rejected humor | get to know, entertain (2) | 5 | Recorded "avoid music jokes"; no music jokes followed; door-repair humor |

Totals: 8 Terra calls (about 51,000 input and 3,200 output tokens; planning median
5.8 s, maximum 6.5 s, in the background), 30 Luna calls (about 680,000 input tokens,
406,000 cached), four completed self-initiated drives. Billed cost was not measured.
Three earlier runs that day (`agency-c0916505…`, `agency-cbb10d74…`, `agency-c04fe758…`)
found the fixes listed above; they are superseded by this run.

Known limits from the probe: the cheaper model still reuses one joke across beats (the
beagle and the socks, four times; a beat instruction against reusing a landed premise was
added after this run and is not yet measured) and occasionally asks a pre-planned question that
ignores what was just said. The synthetic camera always shows the same frame, the arrival
greeting was not exercised (probe faces are below the greeting size threshold), and the
probe cannot judge pacing, real perception, movement or whether Rex feels alive. Live
planner cost per hour of company is unmeasured and is the first number to check.

## Companion and physical agency repair — October 1

The latest live baseline (`299c989c202a4f9ca815b6bf9dfed331`) produced six proactive
spoken turns but repeatedly returned to musical furniture jokes after negative
feedback. It executed no proactive gesture or base movement, and one late head look.
The owner stayed in camera view: 768/793 analyzed frames contained a face, but 175
track IDs were issued. These tracking changes do not establish physical departures.
The planner could lose the dialogue when no eligible audience remained, explicitly
forbade wheels, and usually placed inspections before social/physical engagement.

Implemented through the existing Session/state/output owners:

- Terra and Luna share Rex's character. Plans prioritize actual dialogue, sincere
  curiosity and relevant humor, retire rejected bits, and describe an intention
  rather than script a monologue. Unanswered name questions are not re-asked on the
  next human turn. Self-requested inspections may save observations silently.
- Plans support contribute, inspect, look, gesture, temporary pose and drive; a silent
  physical action can come first. Base charging/manual/clearance/stop limits remain.
  Agency poses cannot override/release human holds or create permanent new holds.
  Autonomous drive also refuses while a human pose hold is active, before its
  preparation routine can recenter the head/arms; explicit requested drive is unchanged.
- The planner sees current poses/joint positions and recent action purposes.
  No-change poses request revision, not successful physical-agency credit. Their
  unchanged head targets no longer invalidate the camera. Silent committed poses,
  like directional attention, can finish through their own camera reframe.
- Private planning retains recent dialogue despite face/track churn and permits
  existing idle motion. Preparation and execution use the same audience snapshot.
  A long planning/polling gap cannot cancel an unchanged audience. Generic activity
  survives view changes; private output retains audience checks. Social presence uses
  settled membership and face width, without the redundant area gate; identity and
  enrollment thresholds are unchanged.
- Invalid steps recover once, normally with quiet body activity rather than a forced
  unrelated joke. Human input, quiet/floor, input health, cancellation, corrections,
  forgetting and restart revalidation remain authoritative.

Verification: **889 blocked-I/O checks passed** (69.2 seconds), including silent
poses/public gesture questions surviving their own camera reframe, audience privacy,
long planning gaps, no-op poses and human cancellation. After the last autonomous-drive
hold guard, **42 focused companion/drive-session checks passed** (13.5 seconds).
Tests use temporary SQLite, fake clocks/providers/pulse/ESP32 transports and blocked
network/audio/serial. They do not operate hardware or establish acoustic/person
discrimination. Public-scope guidance explicitly allows ordinary friendly questions
without requiring a visible listener; private facts remain audience-bound.

Hosted checks use real **GPT-5.6 Terra / GPT-6 Luna**, fictional dialogue, synthetic
playback and production servo/base owners on injected fake transports:

- Shared-character cases, ignored
  `local/validation/agency-470a501ab1214529ac413353de77b7bd/`: **10 completed proactive
  steps**, no audience invalidations or failed turns. Companionship produced three
  spoken gestures and two silent poses, including “What part of work made today feel
  so long?” and a follow-up about the person's improvised presentation. Rejected-humor
  repair produced five silent looks/poses, did not revive the music bit, and followed
  the later human topic. Two proposed steps were revised and recovered; this is not
  a claim that every first proposal was good. Explicit quiet stopped model work.
- Those two cases used **10 Terra and 18 Luna calls**, including five human replies,
  ten scheduled executions, two step recoveries and one tool continuation. Planner
  duration: median **4.83 s**, maximum **9.41 s**, n=10. Terra reported 55,880 input /
  3,575 output tokens (18,918 cached input; 1,858 reasoning); Luna 397,924 / 819
  (265,814 cached; 37 reasoning). Billed cost is unmeasured.
- The preceding continuity-check run,
  `local/validation/agency-8291270b78ab4943949ac75cdd032658/`, completed **15 steps**
  across three cases with no audience invalidations, including an unsolicited base
  move, head/arm actions and relevant conversational follow-ups. The empty-room case
  in `agency-4d9a6fcc37614027b4515c5a8e94e648/` completed five steps including two
  base moves without any person/input. These precede the final shared-character
  prompt and are mechanism evidence, not equivalent final social-quality results.

Earlier passes exposed repeated neutral poses and polling-gap cancellations; their
traces are retained, not counted as acceptance. The former two-case musical probe
only established delivery and missed the user's actual complaint. Current acceptance
cases include ordinary companionship, rejected humor, continuous visibility, body
initiative and unchanged scenes. Prompts/evals follow the official
[OpenAI guidance](https://developers.openai.com/api/docs/guides/prompt-engineering).

No robot, real camera/microphone, TTS or personal-data upload was used. Inter-turn
probe time is accelerated; wall-clock first-audio, real playback/tail latency, resident
memory/M2 load and long-run social quality remain unmeasured. The owner's next robot
run remains the physical and social acceptance test. See [design](AGENCY_IMPLEMENTATION_PLAN.md)
and [acceptance cases](AGENCY_ACCEPTANCE.md).

The dated checkpoints below describe prior software. Their scheduler rules and counts
are historical; this replacement supersedes them.

October 1 [charger release correction](DRIVE_BASE_INTEGRATION.md#charger-release-investigation--october-1-2026):
V3's stale 13.6 V backstop now matches the firmware/battery menu's 14.0 V threshold,
so surface charge does not prolong the host block after firmware release. All 51
focused drive/session checks pass with simulated telemetry; V2's existing compiled
fake-sensor gamepad-unplug test also passes. Firmware's automatic 90-second discharge
guard is unchanged. Installed firmware and live unplug timing were not checked;
no hardware was operated or flashed. Earlier full-suite results below predate this fix.

Follow-up live serial inspection confirmed installed firmware `0.2.3-gamepad-unplug`
and `chg_assert` support. It reported charging at about 14.36 V with the gamepad
disconnected. Only a hello/status query was sent; no motion, reset, flash or unlock.
The operator was away, so the failed L1+R1 attempt remains unreproduced. Serial access
was returned to the battery menu after inspection.

Later October 1 owner-directed firmware source update supersedes the 90-second rule:
`0.2.4-current-unplug` uses three fresh >=0.5 A discharge samples with valid voltage
below 14 V, based on the owner's measured 1-3 A unplugged draw. The compiled
fake-sensor battery/gamepad regression and full hardware-variant Arduino build pass.
This source change is in V2's firmware
tree; the board has not been flashed and live acceptance remains pending. See the
[updated current-release scope](DRIVE_BASE_INTEGRATION.md#owner-directed-current-release--october-1-2026).

October 1 [agency increment 1](AGENCY_IMPLEMENTATION_PLAN.md): working interests,
model-selected reconsideration, and inactivity diagnostics are implemented. Generic
initiative no longer requires a visible person or camera. The fixed 20/60-second
audible floors and automatic continuing-lull backoff are removed. General interests
can survive head turns; private notes and memory remain audience-filtered. Losing
visual audience evidence hides scoped notes without blocking a fresh generic
consideration. Configured public feeds also work without a camera. The scheduler
retains one versioned request, 3–300-second requested delays, a twelve-attempts per
five-minute cap, and backoff for unchanged silent decisions. Plain replies retain
one primary generation; inspection retains its existing bounded continuation.

Verification: **817 blocked-I/O contract checks pass** (66.1 seconds). New production-path
contracts use synthetic audio/camera, fake clocks, simulated inspection and temporary
stores. No hosted model, acoustic model, feed fetch or hardware was operated.
Model selection quality, actual costs, M2 load and live cadence remain unmeasured.
That first increment left physical attention and moving-view continuity pending;
the second-increment entry below supersedes those limitations. Invitation-lapse
behavior is implemented in increment 3 below.

October 1 [agency increment 2](AGENCY_IMPLEMENTATION_PLAN.md#increment-2-implementation--october-1-2026):
physical directional looks and finite before/after-speech gestures now use the
existing output/servo owners after successful configured robot startup. Explicit
held poses/drive stance take priority; receipts distinguish controller pulses from
simulation and actual speech. Camera motion/settling gates both scene snapshots and
deliberate current-view captures. A bounded view history suppresses returning-view
ambient cues; explicitly general visual descriptions and impersonal heard remarks
survive changing visibility, with private context still audience-filtered. No new
model, service, transport or automatic planner is added. Object-targeted physical
gaze, room-wide object identity and reliable semantic non-repetition remain unproven.

Increment 2 verification: **833 blocked-I/O checks pass** (66.1 seconds), including 16 new
agency-2 contracts. Checks use fake pulse transport,
synthetic images/audio, fake clocks and temporary stores; no devices or hosted
providers ran. Live motion/clearance, camera settling, acoustic effects, combined
M2 load/cost/latency and conversational quality remain pending.

October 1 [agency increment 3](AGENCY_IMPLEMENTATION_PLAN.md#increment-3-implementation--october-1-2026):
nullable invitation proposals on existing tools now track audible social invitations,
model-selected review and explicit answered/declined/superseded/lapsed outcomes. One
pending record and six recent closed records preserve heard words, source, revision
and audience scope. Review uses the existing scheduler, with no new timer or model.
The shared prompt adds varied character/interest/silent activity choices; human floor,
quiet, private memory and identity clarification boundaries remain separate.

Verification: **851 blocked-I/O checks pass** (70.4 seconds), including 18 new invitation/agency checks. Synthetic production-path checks include an
unchanged-room five-minute sequence with four scripted primary decisions and no
repeating timer. This is contract evidence, not semantic or live social acceptance.
[Prepared acceptance scenarios](AGENCY_ACCEPTANCE.md) cover the remaining hosted/live
comparisons and measurements. No paid provider or hardware ran; no social threshold,
actual cost, combined M2 performance or semantic accuracy is claimed.

September 29 [engagement repair](LIVE_RUN_CONVERSATION_REPAIRS.md#september-29-engagement-repair):
rejected audio no longer resets lull timing or crowds out conversation history;
ordinary replies survive optional intention/audience failures; conversation can
continue without a greeting plan; empty tool-result speech has bounded recovery.
Explicit last-reply context and revised guidance address repeated premises/questions.
765 blocked-I/O checks pass, plus seven hosted fictional decisions with synthetic
output. Real-room timing, acoustic capture and social acceptance remain open.

September 29 [startup/playback cutoff repair](LIVE_RUN_CONVERSATION_REPAIRS.md#september-29-startup-and-playback-cutoff-repair):
microphone liveness tolerates future ADC timestamps; speech commits at PCM
submission before its delayed start notification; rejected noise no longer removes
the startup greeting requirement. 772 blocked-I/O checks pass. Servo-noise ASR
false positives and physical audibility remain unverified; no hardware was run.

September 30 [cross-session conversation memory](CONVERSATION_MEMORY.md):
completed exchanges survive restart; relevant accepted facts and prior exchanges
reach greetings/replies; the LLM can retrieve dated conversations and save/retract
source-linked episode interpretations. Private audience and speaker uncertainty
are preserved. **785 blocked-I/O checks pass**, including 13 new regressions.
Four hosted fictional cases made seven model calls; no hardware was operated.
Old source-only records remain undated; natural-language embellishment, acoustic
attribution and real-room acceptance remain open.

September 30 audience-memory repair: recognized-camera conversations now retain
trusted exchanges despite acoustic identity abstention. Audience and speaker
provenance stay separate across restart, automatic context, tool recall and
source-linked summaries. Each archive attempt reports a saved/skipped reason.
**792 blocked-I/O checks pass**; seven additional contracts cover the reported
failure, upgrade, source forgetting, quality gates and audience changes. Four
hosted fictional cases verify recall after reopening SQLite, empty-period handling,
a memory-informed greeting and episode save. No hardware operated. The greeting
still embellished an untested motor as “dramatic”; social grounding and live
acoustic/dev-Mac acceptance remain open. Previously skipped exchanges are not
reconstructed automatically. See [memory evidence](CONVERSATION_MEMORY.md).

September 30 [turn-taking repair](LIVE_RUN_CONVERSATION_REPAIRS.md#september-30-turn-taking-repair):
queued speech carries capture/playback chronology; the LLM favors brief replies
and listening to acknowledgments/unfinished answers. Unsolicited speech waits at
least 20 s after replies/greetings and 60 s after other proactive speech. Direct
human requests remain immediate. **796 synthetic blocked-I/O checks pass**;
eight hosted fictional cases pass participation outcomes. No hardware operated;
real conversational comfort and overlap capture remain unverified.

October 1 [character and model evaluation](CHARACTER_EVALUATION.md):
ordinary conversation now uses GPT-6 Luna at low reasoning with a more opinionated,
contextual teasing persona. Unblinded review of 22 paired conversations favored
the revision 9/22, baseline 5/22, with 8 ties; final model cross-check cost was 73%
lower on Luna, with slower completion. **797 blocked-I/O checks and 8/8 hosted
participation cases pass.** Final hosted memory runs confirm recall and saves.
Cache-write/reasoning telemetry and tool-result wording are updated. No hardware
operated; generic jokes and live character acceptance remain open.

## Completed software

[Short-speech capture repair](SHORT_SPEECH_CAPTURE.md) removes a silent pre-ASR
duration veto; 590 synthetic checks pass, with live acoustic acceptance pending.

Startup hardware notices and camera-unavailable voice operation are implemented;
584 synthetic checks pass, with live acceptance pending. See [startup health](STARTUP_HEALTH.md).

September 17: [camera freshness and face-distance fix](FACE_DISTANCE_FIX.md)
removes repeated startup frames and raises detector detail to 960-pixel long edge.
Camera-only recognition passed; combined conversation/group acceptance remains open.

| Status | Capability | Implementation and evidence |
| --- | --- | --- |
| Implemented; live acceptance pending | Persistent character plans, stronger planning/cheaper execution, immediate one-call replies, audience filtering and actual-delivery progress | [Plan and implementation](CONVERSATIONAL_INTENT_PLAN.md) |
| Implemented | Microphone capture, transcription, one conversation owner, streamed speech, cancellation, delivery receipts, bounded history and network retry | [Operations](CORE_OPERATIONS.md), [initial slices and evidence](INITIAL_TEST_PROGRAM.md) |
| Implemented; combined live acceptance pending | ReSpeaker channel-0 capture, V2 host gain, read-only direction provider, source-linked voice/face/direction context and competing-direction learning guard | [ReSpeaker evidence and limits](RESPEAKER_INTEGRATION.md) |
| Implemented; live reliability incomplete | Persistent face/voice profiles, self-introduced names, compatible conversational enrollment, restart, correction and forgetting | [Face path](FACE_ATTRIBUTION_PLAN.md), [introduction-first recovery](INTRODUCTION_RECOVERY.md) |
| Implemented | Proactive arrivals/lulls, shared reply/initiative context, quiet/resume, conversational feedback, dated memory opportunities, news/weather and scene inputs, searched daily stories and interest news (October 11; live use pending) | [Proactive stages 1, 2, 3 and 5](PROACTIVE_IMPLEMENTATION.md), [sources](OBSERVATION_SOURCES.md) |
| Implemented | Tool-selected current-camera inspection, rich visual descriptions, bounded visual memory and exact-image description reuse | [Current-view inspection](CURRENT_VIEW_VALIDATION.md), [attention slices](ATTENTION_IMPLEMENTATION.md) |
| Implemented | Related-record withdrawal, detail-preserving revisions, visual novelty context and actual spoken-remark records | [Visual correction](VISUAL_CORRECTION.md), [integration pass](CORE_INTEGRATION_PASS.md) |
| Implemented; physical acceptance pending | Physical directional attention and finite gestures; simulated visual-target attention; movement/settle/capture sequencing, exclusive motion ownership and separate executor/speech outcomes | [Attention slices 1–3](ATTENTION_IMPLEMENTATION.md) |
| Implemented; in live use, acceptance pending | Head/chest startup/fade, playback-driven speaking LEDs and validated LLM animation tool including requested eye blinks | [LED integration and gates](LED_INTEGRATION.md) |
| Implemented; in live use, acceptance pending | Servo lifecycle including throttle/elbow, playback-driven talking motion with addressee gaze, quiet-time idle hero/poker/throttle arms and head looks (face in front or around the room), spoken normal exit, LLM-selected held poses and bounded control of all fourteen servo channels (two-servo head pitch/side tilt, poker elbow/wrist with clearance since October 10), bounded microphone holds, cross-version resource handoff | [Servo integration and gates](SERVO_INTEGRATION.md), [operator services](OPERATOR_CUTOVER.md) |
| Implemented; floor-tested, newest changes simulated only | LLM-selected drive-base plans (turn, move, face, approach, find person, vision seek of described things), continuous approach while hosted looks run, path-aware clearance and detours, radar bodies as bearing priors, arm/head drive stance, swing/clearance gates, truthful receipts, self-initiated moves including unseen radar presence, back-off reflex, blocked-path route planner, V2 motion sounds, and live battery/obstruction/radar/climate readings for the model | [Drive base integration](DRIVE_BASE_INTEGRATION.md) |
| Implemented; in live use | V2 speech leveling (−20 LUFS) on every phrase; V2 startup sounds with servo wake-up, short cached please-wait/ready lines and spoken hardware notice; V2 power-down sound while the servos park; stop phrases wrapped or repeated ("Oh, stop!"); unreliable noise transcripts kept but never answered; one-time microphone recovery after a silent input | [V2 reference](../V2_REFERENCE.md#speech-leveling-and-startup-audio--september-27-2026), [drive repairs](DRIVE_BASE_INTEGRATION.md#talkative-arrivals-noise-replies-surroundings--september-27) |
| Installed and running | Wake supervisor ("wake up rex", chimes, charger cues, mic-wedge relaunch, V3 auto-update) and servo/LED/battery menu-bar apps copied from V2 and run by the four LaunchAgents; no-motion handover measured at 0.7 s | [Operator services](OPERATOR_CUTOVER.md) |
| Implemented | Action continuity after interruption, bounded outcome history, no automatic resumption of abandoned work | [Integration pass](CORE_INTEGRATION_PASS.md) |
| Implemented; live playback pending | Model-chosen voice impressions (famous clips, known people, the current speaker) and budgeted cameos through the local Breeze engine, the one output owner and the shared MLX lock | [Voice impressions](IMPERSONATION.md) |
| Implemented | External-review repairs, scene-change cutoff repair and introduction recovery | [Review follow-up](REVIEW_2026_09_13.md), [introduction recovery](INTRODUCTION_RECOVERY.md) |

The agreed core software slices have no outstanding feature implementation stage.
Implemented does not mean acoustically reliable, socially accepted or free of bugs.
The latest blocked-I/O result is recorded in the agency replacement entry above.
The preceding **756 checks** covered the September 28 voice
head-turn reflex (camera-aimed, taking over held looks), say-before acknowledgments, uncapped mixed move/light sequences, a wave, look-then-describe, and no
replies to untrusted transcripts; the earlier 645 included 45 [drive-base contracts](DRIVE_BASE_INTEGRATION.md#verification), including the
[greeting-led intention contracts](CONVERSATIONAL_INTENT_PLAN.md) and the
[prompt-caching repair](PROMPT_CACHING.md), plus 48 new
[servo/operator/robot contracts](SERVO_INTEGRATION.md#verification) and 20
[LED contracts](LED_INTEGRATION.md#verification-and-remaining-acceptance).
Four additional V3 tests cover cooperative legacy-supervisor registration,
acknowledged release, timeout and restart rejection.
Three additional talking-motion contracts cover the live-feedback repair: missing
head/elbow/wrist channels, independently repeating lanes and pulse-arrival drain.
Six additional idle/elbow tests cover bounded random motion, quiet scheduling,
preemption, microphone holds and lifecycle shutdown.
Prior counts in linked documents describe their dated checkpoints.

September 20: [audience continuity and unheard-lull recovery](LIVE_RUN_CONVERSATION_REPAIRS.md#september-20-audience-continuity-and-unheard-lull-recovery)
now settle the full camera audience before new proactive work, preserve supported
named presence across spatially continuous track replacements for general speech,
and allow one paced retry of an unheard camera-cancelled lull. Prompt text and
identity/enrollment policies are unchanged. Synthetic contracts and recorded-camera
metadata were checked before the solo retest below.

Subsequent [solo retest](LIVE_RUN_CONVERSATION_REPAIRS.md#solo-retest-evidence): greeting
plus two proactive contributions completed, with no audience cancellations and no
playback underruns. Quiet/resume executed successfully; later silence was explicitly
model-selected. Live retry recovery was not exercised. Repeated visual remarks,
roughly 5.85-second first-audio latency on two direct replies, and missing conversational
intentions remain open; group acceptance is unchanged.

## Acceptance and defect investigation still open

- Live enrollment, alternating speakers, wrong-person assignments and abstention,
  including recognition across launches and room/device changes. Successful
  recognition in individual runs is progress, not broad group acceptance.
- Missed speech under difficult acoustic conditions and combined model/camera load.
  The historical raw-input/VAD failure is not declared fixed by later successful runs.
- Interruption and playback behavior after the latest repairs, including long replies
  and unreliable network conditions.
- Proactive timing, welcome participation, restraint, repetition, humor and visual
  description/correction quality with varied real scenes.
- End-to-end latency, tail latency, memory use and playback under the robot's base
  Apple M2 / 16 GB workload. Measurements on other Macs do not certify that target.

[Proactive stage 4](SOCIAL_ACCEPTANCE.md) is this live acceptance work, not another
coding slice. Failures found during acceptance can require scoped code fixes.
Synthetic tests, hosted synthetic-image probes and live hardware evidence remain
separate categories.

## Future coding requiring separate scope

| Future capability | Boundary of current implementation |
| --- | --- |
| Public installer and optional hardware configuration | Future installer built around existing `local/settings.toml`, independent optional capabilities, clear missing-device behavior, selected dependency/model downloads and optional service setup. No new settings or installer implemented. [Public-install plan](PUBLIC_INSTALL_PLAN.md) |
| Physical motion and expression | [Servo integration](SERVO_INTEGRATION.md) includes explicit live composition and motion/enrollment guards. Physical commissioning, obstruction feedback, live manual-takeover acceptance remain. Finite model-selected physical directional looks and gestures are implemented. Explicit requested head turns, all-joint poses, addressee gaze while talking and idle face/room looks are implemented; object-grounded autonomous gaze remains pending. Base motion followed by a fresh camera question is implemented through [drive_base](DRIVE_BASE_INTEGRATION.md); since October 24 he talks while he rolls (visor only; simulated, not yet run on the robot). |
| Continuous speech/motion coordination | Playback-driven talking motion shares the servo owner; synthetic checks cover actual audio start/stop and failures. Live validation, richer V2 expression, word-level alignment and continuous gaze blending remain. |
| Live tracked-object attention after movement | Target/version contracts exist in simulation. Physical directional looks and settled captures are implemented; tracked room-object gaze and mechanically verified arrival remain pending. Current-camera descriptions do not supply those controllers. |
| Optional vision extensions | Adaptive image detail, general inspection-request coalescing and supported cross-session visual association are future options, not missing requirements of the completed core. Exact-image description reuse already exists. |
| Motion-aware proactive noticing (partial implementation) | Commanded head/base motion, settling and bounded returning-view suppression are implemented. Independent subject motion, robust object continuity and unknown/manual camera movement remain future work. [September 17 direction](PROACTIVE_CONVERSATION.md#future-idea-motion-and-new-arrivals-as-attention-cues) |
| Room navigation | Rooms can be recognized, named and connected in software ([room recognition](ROOM_RECOGNITION.md)); it first starts live after the October 15 camera-hub fix and is unproven on the robot. Positions, a floor plan and "go to the kitchen" do not exist. |
| Compass heading | The drive base publishes raw QMC5883L magnetometer counts; V2's calibrated, current-gated compass fusion (`hardware/compass.py`, calibration file) is not ported. V3 uses gyro yaw only. |
| Owner-requested companion features | Jeopardy and room exploration; the September 18 “Do you ever think” long-lull thought experiments landed October 23. Voice impressions landed October 6 ([details](IMPERSONATION.md)), impressions of his own as goal beats October 23, and interest news October 11 ([details](OBSERVATION_SOURCES.md)). Purposeful motion is implemented in software ([drive base](DRIVE_BASE_INTEGRATION.md)); room recognition and an explore goal landed October 11 ([rooms](ROOM_RECOGNITION.md)); maps and localization are not. General news/proactive inputs already exist; these extensions remain future work. [Feature roadmap](FUTURE_FEATURES.md) |
| Wider roadmap | Games, physical LEDs, richer relationship/biography systems, semantic memory search and offline conversational providers remain separate future work. The dashboard's Memory Banks editor and typed input were not ported ([dashboard](GUI.md)). |

Mouth-motion/active-speaker research is not an active implementation task. The user
declined that experiment; no mouth-motion model or enrollment dependency was added.

## Reading the historical plans

Use this checklist for current completion status; use each feature document for
contracts, design rationale and dated evidence. The initial program's numbered
slices and the proactive plan's numbered stages are separate sequences. In
particular, the initial program's deferred expansion must not be confused with
proactive stage 5, whose news/weather/scene inputs are implemented.

September 17 reply-time face context: fresh cached `current_view` is separate from
speech-time attribution/enrollment evidence; 405 blocked-I/O tests pass. Live
acceptance pending. See [face timing evidence](FACE_DISTANCE_FIX.md#reply-time-face-evidence-september-17).

September 17 scoped object/photo addition: paced RF-DETR Nano ONNX detection and
file-backed named photographic memory are implemented and enabled locally. The
shared model can inspect/compare, teach, correct and forget pet/object references.
413 blocked-I/O checks and bounded camera/hosted/load probes pass their documented
contracts. Individual-animal accuracy, ambiguous spatial references and full live
conversation acceptance remain limited; no dedicated room model was added. See
[implementation and evidence](OBJECT_PHOTO_MEMORY.md).

Photo-memory follow-up: 417 blocked-I/O checks pass with explicit save/defer teaching,
source-name checks, additive notes, and multi-pet overview rejection. Saved-photo
hosted checks still misidentified the dogs despite fresh inspection; reliable
individual-animal recognition remains open. See the
[handling follow-up](OBJECT_PHOTO_MEMORY.md#teaching-and-correction-safeguards-september-17-follow-up).

User-requested [Luna comparison](LUNA_COMPARISON.md): saved-photo trials completed
with isolated galleries and silent output. Luna was cheaper but slower in the small
paired test and failed a breed-update acknowledgment; production remains Mini/low.

The subsequent [Terra trial](LUNA_COMPARISON.md#terra-follow-up--september-17)
completed the same five saved-photo turns with successful breed persistence and
fresh inspections for both correct recognition answers. Median text completion was
3.27 s; estimated cost was $0.18589, about 2.9 times the Mini trial. This remains a
small provider comparison, not live acceptance; the production model is unchanged.

Latest [live-run conversation repairs](LIVE_RUN_CONVERSATION_REPAIRS.md) address
presence-driven greeting cancellation, interrupted intent speech and transcript
interpretation. [Model-selected JSON timing](NEXT_CONVERSATION_SCHEDULING.md) is now implemented
through all three agency software increments. Acoustic and live playback acceptance remain pending.

September 18 [prompt-caching repair](PROMPT_CACHING.md): per-turn identifier enums in
transmitted tool schemas had left every tool-enabled request uncached. The adapter now
sends a static tool list with `allowed_tools` and grounds returned identifiers locally.
A paid synthetic probe measured 11,008 of about 12,400 input tokens cached per primary
call. Hosted semantic suites and live conversation have not been rerun on this change.

September 19 [persona consolidation](PERSONA_REWRITE.md): the conversation prompt was
rewritten from 423 to 242 lines with one priority order, the owner's rulings on names,
appearance, intentions and unanswered questions, and the request context moved after the
history. An invalid intention note no longer fails a turn, and unknown identifiers are
rejected by the state owner rather than the adapter. Paced hosted suites scored 141/168
against 135/168 for the previous prompt, as development evidence only. 467 blocked-I/O
checks pass. Live conversation has not been rerun.

September 27: all-joint model tools and animation argument repair pass 597 synthetic blocked-I/O checks. No new live hardware or hosted-model validation; clipped transcription remains unresolved. See [servo evidence](SERVO_INTEGRATION.md#september-27-all-eleven-model-selected-joints).

September 27 [first floor run repairs](DRIVE_BASE_INTEGRATION.md#first-floor-run-and-repairs--september-27): wide-angle camera calibration, seated-person approach distance, 1 s pre-speech buffer, audible speech finishes unless explicitly stopped, and head gaze toward the addressee. Synthetic checks only since the run.

September 27 [drive base](DRIVE_BASE_INTEGRATION.md): 645 blocked-I/O checks pass with a simulated ESP32. Paced hosted probes: gpt-5.4-mini planned 16/20 synthetic cases; with the geometry-triggered gpt-5.6-terra route planner, 19/20. No wheels moved; the floor test awaits the owner.

September 27 greeting-cutoff repair: 600 blocked-I/O checks pass. General proactive intent replies now finish across face-track changes after audio starts; pre-playback audience validation and targeted-question cancellation remain. Tests use synthetic audio/camera providers through the production runtime validator. No live hardware or hosted calls; audible acceptance remains pending. See [cutoff evidence](PROACTIVE_IMPLEMENTATION.md#september-27-greeting-interrupted-by-face-track-churn).

September 27, floor-test day: repeated live runs on the robot with wheels, radar,
servos, LEDs and camera. The findings and repairs are recorded in [drive base](DRIVE_BASE_INTEGRATION.md) sections from
"First floor run" to "Talkative arrivals":
- vision seek and door approach, path-aware clearance, detours and the hallway
  no-reverse rule;
- the continuous approach, motion sounds and arm/head drive stance;
- wrapped stop phrases and never answering noise transcripts;
- brief arrival reports, live base status and idle head looks.
Also a startup crash, where startup clips were fed whole and hit the playback drain
timeout, is fixed by streaming them in pieces with one-time microphone recovery.
729 blocked-I/O checks pass. Changes after the last floor run are synthetic only.

October 1 firmware deployment follow-up: owner-authorized upload of
`0.2.4-current-unplug` completed with verified hashes and matching live hello.
Stationary telemetry kept charging locked at 14.367 V / 6 mA. Battery menu service
restored and serial ownership confirmed. This supersedes the unflashed status
above; physical unplug latency/gamepad acceptance remains pending. No movement
or charge assertion was commanded.


October 10 owner-directed firmware deployment: `0.2.7-fr-disabled` disables the
faulty front-right radial on mux 5 (unavailable, not a fabricated clear reading).
Full Bluepad32/gamepad/motors/radial/matrix build and upload verified; 26 isolated
sensor/battery regressions pass. Stationary hardware: 139 frames with `fr_radial=-1`,
seven other radials online, matrix ready, Bluepad32 ready, zero motor duty. Battery
menu restored. Floor driving/gamepad interaction and hardware repair remain open.
See [firmware evidence](DRIVE_BASE_INTEGRATION.md#front-right-radial-firmware-disable--october-10-2026).


October 10 matrix floor follow-up: owner-confirmed close floor returns now use
measured rejection thresholds in the bottom two rows, superseding the >=250 mm
geometric lower bound there. Firmware `0.2.8-matrix-floor` built/flashed with all
four flags and hashes/version verified. 26 isolated native regressions pass;
53-frame saved replay removes 42 close-floor blocks. Stationary live capture:
57/57 matrix frames give clear host path at 3.5 m; firmware front pair 3500/3500,
zone CLEAR, radial remains disabled, Bluepad32 ready. Battery menu restored.
No drive/gamepad interaction test; floor-range low objects remain indistinguishable
in those two rows. [Evidence](DRIVE_BASE_INTEGRATION.md#matrix-floor-calibration--october-10-2026).

October 12 bookshelf turn repair: an approach turn with something close behind now banks
forward instead of spinning. A spin making no headway for 2 s is stopped, and a refused or
stalled spin banks before it backs up or gives up. Turn traces now carry clearance.
1117 blocked-I/O checks pass, on a simulated base only; the live retry from the bookshelf
spot is pending. [Evidence](DRIVE_BASE_INTEGRATION.md#october-12-evening-a-spin-into-the-bookshelf-behind-him).

October 13 live-run repairs (session `332b0133`, Bret alone, about 17 minutes). Navigation:
camera-chosen route turns and sensor detours now bank or back up to earn room when a spin is
refused (the "go right around the couch" turn was refused at 0.55 m and the approach gave up
1.7 m short); a back-up's receipt says why; refused moves and turns are logged with clearance.
"Face me" is a wheel turn, never a head pose. Conversation: a planned beat sharing a five-word
phrase with one of Rex's last six lines gets one rewrite (exact repeats were the only check);
beats must not remake a point a reply already made, narrate the plan, or revive Rex's own
confused remarks (the "command-post" line came from the 18:35 session's memory). A new
`shut_down` action takes the normal exit for any wording of the request, on human turns only.
Impression scripts may not be empty; a line Rex asked for is performed, not taken as an
introduction; loose plan dates save as callbacks; rejections are explained without reason
codes. `neck_up`/`neck_down` drive the head-lift pole to its measured limits. Recognized
clean speech now grows a saved voice ([rules](CORE_OPERATIONS.md)); Bret's had stayed at
three enrollment clips. 1128 blocked-I/O checks pass, synthetic only; no hosted model,
saved audio or hardware was run. Prompt-rule effects and live navigation remain unverified.

October 13 planned-beat repair (owner: "probably the worst feature so far"). Live, beats were recited from
the plan almost word for word and often ran after the conversation had moved on. The planner now writes
short directions (under 25 words, no ready-made lines) with at most one question beat per plan and marks
`follows_previous` follow-ups. Code: a follow-up is dropped when its setup never landed; a beat talked over by
a real turn is dropped rather than retried; a beat may not stack a question on Rex's own unanswered one (one
rewrite, else the beat is let go); a beat may be let go with `remain_silent`. When people spoke after a beat
was planned, the beat model sees their latest words beside the beat and decides whether it still fits.
Paid probe `tools/check_agency_beats.py` (fictional Sam, real reply model and planner, synthetic audio, fake
clock) replays the October 13 conversation shape. Before (2 runs, 14 beats): 5 beats mostly recited, about 5
off-topic. After (final arm, 3 runs, 21 beats): 11 spoken, none mostly recited, 0–1 off-topic, 10 let go, no
repeated phrases. About $2 of hosted calls across all arms, estimated from returned usage. Text only; live
timing, interruptions and the owner's judgment are untested.

October 13 (late) live-reaction repair, from session `8f94717a` (Max held for minutes, never noticed;
long silences; no interests or news). Animals: a dog or cat settled in view for 2.5 s (cat/dog label
flicker and short dropouts count as one sighting) starts a `pet_arrival` moment after people and before
goals, at most every 5 minutes: one look against saved pets, then a greeting by name or a playful "is that
Max?". A pet stays targetable for 10 s between flickering detections, so teaching "This is Max" no longer
fails as ObjectTargetExpired. A look that tries to save without a name now answers in words. The planner
sees saved pets and the detector caveat. Silences: silent moves always run and a spoken beat may be let go
only after people spoke since it was planned (13 of 22 beats had been dropped); talking to a pet holds Rex
quiet 20 s, not 120 s; after 45 s without speech the planner opens something new, most personal first
(news about their interests, their interests, general news). Bret's astronomy and camping facts had never
been marked as interests, so no interest news was ever searched; the new `r3x profiles mark-interest`
command marked both (revisions 64–65), and stated likes now prefer `remember_interest`. Paid probe
`tools/check_agency_beats.py --scenario quiet_then_dog` (fictional Sam, drawn dog picture, fake news):
2 final runs, 5–6 lines in five quiet minutes each, interest news and general news both used, the dog
greeted with Max's name both times, never called a cat. Conversation scenario: 0 recited, 0 off-topic.
Synthetic pictures cannot show whether Max is recognized from a real camera frame.
