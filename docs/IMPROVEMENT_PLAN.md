# October 14 improvement plan

Owner-approved plan from the October 14, 2026 architecture review. The working copy lives
in the owner's plan document; this file records what landed in the repository and its
evidence. Diagnosis: Rex lives in the instant. The language model has no feeling of time,
vision has no object permanence, and driving has no memory of space. One-on-one
conversation works because the chat history is a memory carried forward.

Owner rulings (October 14): improve V3; no new features until steps 1–3 land (the pet
noticing work is the one kept exception); Rex teases and roasts everyone, guests included,
but not always: the conversation decides; a smarter model may write joke material on the
side, never delaying a reply; the camera stays; no lidar; the front-right ToF sensor will be
repaired. The owner asked for the whole plan to be implemented without robot runs; every
result below is synthetic, saved-log or hosted-model evidence until he tests it live.

| Step | What | Status |
| --- | --- | --- |
| 0 | Baseline numbers from session logs | Done October 14 |
| 1 | One shared picture of the present | Done October 14 (synthetic) |
| 2 | Rex sets his own wake-ups | Done October 14 (synthetic, paid probes) |
| 3 | Short-term memory of space for driving | Done October 14 (simulated base) |
| 4 | Make him funny again | Done October 14 (synthetic, paid probe) |
| 5 | Faster replies | Done October 14 (synthetic, paid probes) |

## Step 0: baseline (October 14)

`tools/baseline_report.py` reads the private session traces (read-only) and prints the
numbers each later step is judged by; `--since`/`--until` pick sessions by file date.
Replies now also log `model_first_output` (first prose or finished tool call), because 170
of 205 recent replies went through a tool and had no model timing at all.

Baseline: the 18 robot sessions of October 5–13 (2.2 hours, real hardware, owner present).
The private JSON is kept in `local/validation/plan-baseline/`.

| Measure | Baseline |
| --- | --- |
| Reply wait, end of speech to first audio | median 4.99 s, p90 7.45 s; 189 of 205 over 4 s |
| Model share (prose replies only) | median 3.2 s; ASR 0.37 s; first voice audio 0.38 s; endpoint 1.22 s (now 0.7 s) |
| Planned beats | 148 started: 46 heard, 27 silent moves done, 47 talked over, 10 let go, 43 skipped |
| Face tracks | 32 new track IDs per minute with a face in view |
| Re-greeted someone recognized 31–120 s earlier | 6 of 8 arrival greetings |
| Drive trips | 68: 51 completed, 10 partial; median 9.5 s, p90 40 s; 15 blocked or refused steps; 57 hosted looks |
| Near-repeated lines | 0 of 262 (word overlap ≥ 0.7); no joke record existed |
| Model cost | 262 calls per hour, about $1.36 per hour (Terra priced at list input rate: an upper bound) |

No robot run was made for this step (owner's instruction): the recent untested changes
were not run first, so the baseline predates the October 11–13 changes for many sessions.

## Step 1: one picture of the present (October 14)

`src/r3x/present.py` is the single owner of presence and the conversation clock. It runs no
model and touches no device; the runtime, the reply path and the planner feed it faces,
detected things, trusted speech and Rex's heard lines, and read it back.

- **People.** Recognition by face or supported voice. A person is *here* for 45 s after
  their last recognition, and stays for up to 180 s while unrecognized faces or speech
  continue and nobody else is recognized. A visit only breaks after 180 s without
  recognition, so a renumbered face, looking away or Rex's own turning is not leaving.
  A known face is greeted as arriving only when their visit began in the last 60 s:
  someone heard or seen two minutes ago is not greeted again (the October 5–13 baseline had
  6 of 8 such re-greetings). This replaces the goal's own presence tracking, the 60-second
  "spoke recently" arrival check and the goal's separate 45/180-second constants.
- **Private audience.** The memory gate's 90-second carry and ten-minute company rule moved
  here unchanged (`PRIVATE_CARRY_SECONDS`, `COMPANY_SECONDS`); privacy stays stricter than
  presence on purpose. Forgetting a person removes them from the picture.
- **Things.** Pet sightings (the cat/dog flicker, short dropouts, settle, return and
  cooldown rules of the October 13 pet arrival) and the last few object labels with when and
  where they were seen. The 10-second pet recall rule is now a constant of this module.
- **Conversation clock.** Who spoke last and when, Rex's last heard line and whether anyone
  answered, how long the quiet has lasted, and his unanswered own lines (moved from the goal).
- **Time in words.** `right_now` goes into every reply, beat and planner request: people
  here and since when, any pet or thing seen lately, and the clock in words ("his question
  went unanswered and that moment has passed: do not repeat it or ask again", "a lull",
  "a long quiet stretch: the last topic has gone cold"). The persona and planner are told to
  trust these timing words over raw timestamps.

Retired: `AgencyPlan` presence tracking and `PRESENCE_GRACE_SECONDS`/`IDENTITY_INHERIT_SECONDS`/
`ALONE_PAUSE_SECONDS`, `spoke_recently`, `CoreState.audience_seen`/`company_at`,
`conversation_memory.VISIT_GRACE_SECONDS`/`COMPANY_SECONDS`, `proactive.PetArrivals`.
Still separate on purpose: the camera's own track bookkeeping (`faces.py`), the arrival
greeter's settle and cooldown rules, and the drive's remembered voice heading (step 3).

Evidence: synthetic only. All existing presence, privacy and pet checks pass unchanged in
substance; nine new checks in `tests/test_present.py` cover the same-visit rule, a real
return after an absence, the question-left-hanging wording, unanswered-line reset, pets,
forgetting, and `right_now` reaching beats and the planner.

## Step 2: Rex sets his own wake-ups (October 14)

Each planned beat now carries a wake-up instead of a fixed delay: `quiet` (after some seconds
of nobody talking), `answered` (once someone replies to the previous beat), `unanswered` (only
if nobody has spoken for some seconds after it; let go if they did), or `pet_in_view`. The
planner chooses them; the picture of the present watches them. A beat whose moment never comes
(`answered` or `pet_in_view` after 240 s, `unanswered` once answered) is skipped as
`moment_passed` before any model call is spent on it. When people have spoken since a beat was
planned, the performer may also hold it once with the new `wait_for` tool, naming the moment.

Kept fixed in code: people first, quiet and floor rules, the five-second lull floor, the
8-second minimum between planner calls, and one rule for being ignored: with two or three of
his lines unanswered a spoken beat needs 30 s of quiet, with four or more 60 s (silent moves are
never held). Retired: `delay_seconds`, the 0/10/20/40/80/120-second easing ladder
(`EASE_STEP_SECONDS`, `EASE_MAX_SECONDS`, `ease_seconds`) and the 20-second plan lead
(`PLAN_LEAD_SECONDS`), which only existed to rest during that ladder.

Evidence:

- Synthetic: 1157 blocked-I/O checks pass, five new in `tests/test_wake.py` (a follow-up waits
  for an answer and then runs at once; an answer never given lets the follow-up go with no model
  call; a rescue line runs only when the question fell flat; a dog beat waits for the dog; the
  performer holds a beat once and it runs when the chosen moment comes).
- Paid, fictional Sam (`tools/check_agency_beats.py`, labels `step2-wake` and
  `step2-wake-quiet`, private receipts under `local/validation/`): the planner used all three
  conversational wake kinds (13 beats: 7 quiet, 4 unanswered, 2 answered); 8 of 8 attempted beats
  were spoken, one mostly recited, no repeated phrases; three beats were let go as
  `moment_passed` without a call. The quiet-room-then-dog run spoke 6 lines in five quiet
  minutes, used interest news and general news, and greeted the dog as Max.
- Not measured: wall-clock pacing with real people, and how often the live performer uses
  `wait_for`.

## Step 3: short-term memory of space (October 14)

`src/r3x/local_map.py` keeps, for about a minute, where the ring and depth sensors saw returns
and where they saw open floor (10 cm cells within 2 m, in the frame of his tracked position and
IMU heading). Every sensor read the drive makes feeds it, at most ten times a second, moving or
still. It is not a floor plan: wheel travel drifts and the sensors are sparse rays, so old
cells are forgotten.

- **Rechecking a doubtful reading.** A return on his straight path where open floor was seen
  by at least two frames in the last 3 s, with nothing returned there in the 10 s before, is
  rechecked: he holds still for 0.6 s and reads again. A forward move whose path stays blocked
  is refused as before; one that clears goes ahead. A continuous roll toward a sought thing
  pauses the same way instead of ending in a stop, a hosted floor look and a detour. The
  firmware's own stop zone and every host clearance margin are unchanged; something already
  there before is never second-guessed.
- **The way round, planned once.** After the camera's floor look has chosen a side around
  an obstacle, the next legs around the same thing come from floor the sensors already saw
  open on that side (most of the centreline seen open, no remembered return across the body),
  up to four 0.8 m legs, before another hosted look is paid for. Each still passes the usual
  turn swing check, move clearance and firmware guards.
- **The model is told** that Rex remembers what his range sensors saw for about a minute,
  with no floor plan.

Not changed: the October 3 face-approach hold (front within 0.2 m holds up to 5 s), voice
heading memory (120 s room heading), the sought thing's remembered heading and fixed position,
the firmware and its configuration.

Evidence: simulated base only (`tests/test_local_map.py`, plus a counter-route and a cruise
check in `tests/test_floor_feedback.py`). A brief phantom return is rechecked and the move goes
ahead; an obstacle that stays stops him; an obstacle present from the start is not rechecked;
memory fades; a 0.5 m first camera leg round a counter is continued by remembered floor on the
same side to the door with one hosted look instead of two; a 0.3 s phantom mid-roll pauses the
run with no detour. Real sensor noise, wheel drift over a minute and how often live false
blocks are actually doubtful are unmeasured until a floor run.

## Step 4: make him funny again (October 14)

- **Persona.** Teasing and roasting are how Rex shows affection, with everyone here, guests
  included (lighter at first with someone new), when the conversation offers it, never in
  serious moments. Seven short V2 lines are style examples (see V2_REFERENCE.md); a "roast me"
  gets real bite about what they said, did, chose or own. Bodies, intelligence, identity and
  family remain off limits; dropping a bit after a request is unchanged.
- **Joke record.** `jokes.joke_record` keeps bits Rex told from writer material and, through the
  new `note_bit` tool (spoken reply in its text, used only when a reaction is clear), how they
  landed: landed, flopped or asked to stop. `humor` in the context offers running gags (landed
  bits with this person, in their private audience only, 60 days), bits that flopped (30 days)
  and bits told lately (two days). Forgetting a person deletes their records.
- **Writer's room.** After a reply to a person, `jokes.JokeRoom` may send a small summary (the
  last eight exchanges, who is here, the moment from step 1, his own material) to the planner
  model, which returns up to three joke angles. A later reply sees them as optional
  `joke_material` for two turns or two minutes; the reply never waits for the writer. At most
  one call every 45 s and 30 an hour, only after human turns; `joke_writer_enabled` turns it
  off. Estimated at under half a cent per call (Terra list rates; not measured live).

Evidence: synthetic checks in `tests/test_jokes.py` (pacing, freshness, audience, use detection,
a failed writer staying silent, a used bit recorded and a landed one returning as a running gag
the next day, a reply not waiting for a busy writer, forgetting). Paid probe
`tools/check_humor.py` (fictional Sam and guest Jordan, one run, labels by Terra without the arm):

| Scenario | Persona before step 4 | After (persona and writer's room) |
| --- | --- | --- |
| Dubious kitchen-painting plan | 2 of 6 lines tease; funny 1.6 of 5 | 4 of 6; funny 2.0 |
| Guest's karaoke disaster | 3 of 6; funny 2.0 | 5 of 6; funny 2.8 |
| Grief | no jokes | no jokes |

A typical new line: "That's not karaoke, that's a six-minute hostile takeover of the microphone."
Both arms were judged to repeat a premise once or twice per scenario. One run per arm is a
small sample; the owner's ear is the real test.

## Step 5: faster replies (October 14)

Most replies to people (170 of 205 in the baseline) come inside a reply tool, and a tool call is
handed over only when the whole response is finished. `tool_text.TextField` now decodes the
spoken `text` argument of `respond_with_intent` and `respond_with_gesture` as it streams (strict
arguments arrive in schema order and `text` is first), and the session phrases and queues it
exactly like prose; when the text closes, its last phrase is spoken at once. Only replies to
people stream: Rex's own beats are checked for repeats and stacked questions before speaking, and
nothing streams while he is told to stay silent or while a photo is being taught. The finished
call still carries the same text for validation and history; a gesture follows its streamed words
on its own; a streamed reply whose tool is then rejected is not answered a second time.

Tool definitions were measured rather than trimmed: sending only the allowed tools was not faster
than the cache-friendly static list (median first output 2.75 s against 2.12 s, 6 paced requests
each), so the static list stays. Removing the dead `next_opportunity` field from five tools would
save about 2,000 of 74,000 definition characters and was left alone.

Evidence: 1178 blocked-I/O checks pass, seven new in `tests/test_tool_text.py` (decoding across
chunk and escape boundaries, no streaming when text is not first, the adapter streaming ahead of the
finished call, speech audible before the call completes and said once, a one-sentence reply spoken
when its text closes, a rejected streamed reply not answered twice). Paid probe
`tools/check_reply_latency.py` (synthetic production-sized request, 23,300 input tokens, 22,100
cached, gpt-6-luna/low, 6 paced requests with a reply tool required): first speakable phrase at a
median of 2.09 s with streaming against 3.00 s when the response finished, about 0.9 s sooner.
That is model time only; ASR, endpointing, TTS and playback are unchanged, so the expected live
median moves from about 4.6 s toward 3.7-4 s for tool replies. Live reply timing is unmeasured.

October 16 follow-up (owner request, after the October 13 log analysis): move acknowledgments
stream like reply words, every look or lookup starts with a lead-in, saves and quiet requests
answer in one call, and the endpoint is 0.35 s. Details and evidence:
[implementation status](IMPLEMENTATION_STATUS.md), October 16 reply speed.

## Where the plan stands

All six steps are implemented in software. None of it has run on the robot: the owner chose to
test the whole plan himself. `tools/baseline_report.py --since 2026-10-15` compares his next runs
with the October 5-13 baseline above.

## First live run and repairs (October 14, evening)

Session `e1f8b2c6` (11 minutes, real hardware, the owner with Max and Toby). Owner: mostly a
successful run, not funny enough; Max was recognized; when he moved chairs Rex reoriented to him.
From the log, against the baseline: median reply wait 4.89 s (4.99 before), single-call replies
3.4–5 s, replies needing a second model call (a look, a lookup, a photo save) 6–10 s, one 15 s
provider stall; 19 new face tracks a minute; three planned lines dropped once stale, 16 waits
for their moment, no stacked or repeated questions; voice recognized the owner on 8 of 44 turns
and the face in 56% of frames with a face, yet he stayed present throughout; estimated model cost
about $2 an hour (planner about $1.15, joke writer about $0.30; Terra priced at list input rate).

Repaired the same evening (synthetic checks and paid probes; **not yet run on the robot**):

1. **Arriving in silence.** A silent approach stays allowed (owner), but when his own approach
   ends beside someone and nothing was said, a follow-up line is spoken on arrival.
2. **"Back up five feet" cancelled halfway.** Backing up brought another face into view and the
   private-memory guard cancelled the whole turn, wheels included. The guard now waits while a
   move runs and applies again before anything is said after it.
3. **A greeting prepared for someone already talked with** (74 s in). Someone recognized in view
   when Rex spoke, or heard by voice, is not "arriving" even in the first minute of their visit.
4. **The clock.** The picture of the present now carries the local time in words ("Wednesday
   evening, 6:24 PM, October 14") and his earlier heard lines as things already said; he owned
   one clock slip four times. The persona keeps a concession to a few words, then a comeback, and
   never revisits an owned mistake; the planner may not plan beats about his own slips. The
   picture is written to the session log (`right_now`) so runs can be read the way he saw them.
5. **Humor.** The writer's material was used none of the eleven times: it reacted to the line
   just said, which is stale by the reply that sees it. The writer now aims at what will still be
   true next turn (the ongoing plan, what they hold or have around them, the hour, known
   interests), writes roasts rather than observations, and never builds on Rex's own mistakes;
   the reply model is told to prefer a fitting piece over a safer line; an admission ("I haven't
   actually measured anything") is a gift to answer, not an acknowledgment. Paid probe
   (`check_humor.py`, one run): 11 of 12 lines teased against 6 of 12 with the pre-step-4 persona,
   judged funnier (2.2 and 3.2 against 2.0 and 2.3 of 5), still no jokes in the grief scenario.
   Whether material is used cannot be measured from wording; the probe found no verbatim use.
6. **Silence while looking or checking.** `inspect_view`, `inspect_visual_memory` and
   `recall_conversations` take an optional `say_first` lead-in ("Let me look."), streamed and
   spoken at once; the answer that follows is told not to repeat it.
7. **Cost.** The planner is asked for 4–6 beats instead of 2–5 (a beat whose moment never comes is
   let go). Paid probe: two planner calls for the conversation that took three, 6 of 7 beats
   spoken, none recited. With the false arrival gone, live runs should replan about half as often.

1185 blocked-I/O checks pass, including new checks for the arrival line, the uncancelled
back-up (both fail without the repair), the engaged-person rule, the clock and earlier lines,
the lead-in, and the writer's summary.

## Second live run: what he was looking at, and the dog (October 14, evening)

Session `1662b007` (5 minutes). Owner: he got the dog wrong; he looked up and to the right, and asked
what he was looking at he read a description "of what was in the frame"; he called the owner "a man
sitting by the couch" though face recognition knew him. From the log: a planned silent look beat
tipped his head up ("look upward while letting the astronomy detour go"); asked, the reply model had
no sense of where its head pointed or why, took a fresh whole-view photo look (14.3 s to first audio)
and recited it, ending "I don't see a pet in this frame". The owner sat at the left edge of the wide view
throughout. The pet arrival's look used a cut-off crop of Toby at the bottom edge of the view and
greeted him as Max; after "No, this is Toby" the view was not saved to Toby.

Repaired (synthetic checks and a paid probe; **not yet run on the robot**):

- **Where he is looking, and why.** The picture of the present now carries `rex.looking`: which way the
  head points (from the commanded neck and head pulses), since when, why (the reason of the look he
  chose, recorded when a look or a look pose completes, or what the idle head is doing: following a
  face, an idle glance, looking around an empty room), who is where in his view, and what the object
  detector sees in the middle of it. After an idle glance he is told to answer as a person would (what
  caught his eye, or stretching his neck), never with internal words. The idle head motion now reports
  its activity.
- **His eyes, not a camera.** The persona says the camera is his eyes: "I see", never frame, image,
  picture, shot or camera; asked what he is looking at, he answers from `rex.looking` without a new look
  or an inventory, and does not narrate where people sit in his sight unless asked.
- **Recognized faces are people.** A person he sees whose face is recognized is that person: "you" for
  the one he is talking with, names for others, never "a man". Naming from appearance alone stays
  forbidden.
- **Pets.** A saved pet is named only when the look clearly matches; a cut-off or edge view, or two pets
  that could fit, gets a question ("Is that Max or Toby?"). A correction is teaching: the view is saved
  to the right pet.

Evidence: 1189 blocked-I/O checks pass (new: gaze awareness for an idle glance and a chosen look, the
persona's vision and recognized-face wording). Paid probe `tools/check_looking.py` (fictional Sam,
stand-in head, real reply model), answers to "What are you looking at?": idle glance: "Oh, that clock
caught my eye. It's been keeping better time than my flight record ever did."; following Sam: "I'm
looking straight ahead at you, Sam"; chosen look: "The clock, mostly" (the planner's reason was not
speakable). No camera words in any answer. Dog recognition from saved photos remains weak.
