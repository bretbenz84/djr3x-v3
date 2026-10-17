# Current implementation status

Updated October 17, 2026. This is the consolidated
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
| Physical motion and expression | [Servo integration](SERVO_INTEGRATION.md) includes explicit live composition and motion/enrollment guards. Physical commissioning, obstruction feedback, live manual-takeover acceptance remain. Finite model-selected physical directional looks and gestures are implemented. Explicit requested head turns, all-joint poses, addressee gaze while talking and idle face/room looks are implemented; object-grounded autonomous gaze remains pending. Base motion followed by a fresh camera question is implemented through [drive_base](DRIVE_BASE_INTEGRATION.md). |
| Continuous speech/motion coordination | Playback-driven talking motion shares the servo owner; synthetic checks cover actual audio start/stop and failures. Live validation, richer V2 expression, word-level alignment and continuous gaze blending remain. |
| Live tracked-object attention after movement | Target/version contracts exist in simulation. Physical directional looks and settled captures are implemented; tracked room-object gaze and mechanically verified arrival remain pending. Current-camera descriptions do not supply those controllers. |
| Optional vision extensions | Adaptive image detail, general inspection-request coalescing and supported cross-session visual association are future options, not missing requirements of the completed core. Exact-image description reuse already exists. |
| Motion-aware proactive noticing (partial implementation) | Commanded head/base motion, settling and bounded returning-view suppression are implemented. Independent subject motion, robust object continuity and unknown/manual camera movement remain future work. [September 17 direction](PROACTIVE_CONVERSATION.md#future-idea-motion-and-new-arrivals-as-attention-cues) |
| Room navigation | Rooms can be recognized, named and connected in software ([room recognition](ROOM_RECOGNITION.md)); it first starts live after the October 15 camera-hub fix and is unproven on the robot. Positions, a floor plan and "go to the kitchen" do not exist. |
| Compass heading | The drive base publishes raw QMC5883L magnetometer counts; V2's calibrated, current-gated compass fusion (`hardware/compass.py`, calibration file) is not ported. V3 uses gyro yaw only. |
| Owner-requested companion features | Jeopardy, room exploration, and the September 18 “Do you ever think” long-lull thought experiments. Voice impressions landed October 6 ([details](IMPERSONATION.md)) and interest news October 11 ([details](OBSERVATION_SOURCES.md)); lull-time impression beats are not offered. Purposeful motion is implemented in software ([drive base](DRIVE_BASE_INTEGRATION.md)); room recognition and an explore goal landed October 11 ([rooms](ROOM_RECOGNITION.md)); maps and localization are not. General news/proactive inputs already exist; these extensions remain future work. [Feature roadmap](FUTURE_FEATURES.md) |
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
