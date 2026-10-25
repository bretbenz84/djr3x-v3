# Live test script

Choose the solo or two-person section below, then follow its runs in order. Quoted lines are things to **say aloud**; commands go in the
terminal. Checkboxes are for the human tester. A pass means the observed behavior
matches the check, not that Rex used particular words.

The **Solo tests** section contains conversation and camera/object tests you can do
without another person. The **Two-person tests — Bret and Jeremy** section contains
all paired tests, including Jeremy's first introduction, alternating speakers, memory,
restart and group manners. You can go straight to that section when Jeremy is available.
Allow roughly 25 minutes for solo tests and 30–40 minutes for paired tests, plus the
optional natural conversation.

These commands use the existing v3 configuration and saved memory, real microphones,
camera, paid conversation/TTS providers and local evaluation recordings. Use the
dev Mac for new voice enrollment; on the robot, enrollment depends on the servo
microphone hold, which is implemented but not yet accepted live. Stop v2 before starting. Do not clear existing profiles.

Wheel tests are separate: see the [drive-base floor test](DRIVE_BASE_INTEGRATION.md#floor-test-script).
Leave `drive_port` unset for these conversation runs.

## Solo tests

### Run 1 — Start Rex and test basic conversation — about 10 minutes

**Run:**

```sh
cd /Users/bbenziger/djr3x-v3
.venv/bin/python -m r3x live --camera --record-evaluation
```

Wait for `Listening`. Copy the session-log path printed at startup into your notes.
Unless instructed otherwise, wait for Rex to finish speaking, then wait one second
before your next line. Do not coach him after a failure; note it and continue.

**1A — Startup greeting**

Stand/sit where the camera can see you. Say nothing for 25 seconds.

- [ ] Rex greets you or appropriately invites your name without requiring you to speak.
- [ ] The greeting plays to the end. Note a wrong name, silence, or cut-off sentence.

**1B — Hearing and following a thought**

Say: **“Rex, I'm going to describe something. Please wait until I finish.”**

If he acknowledges, wait for him to finish; if he stays silent, wait five seconds.
Then say **“I was thinking we could…”**, pause for two seconds, then
continue **“…invent a name for a spaceship shaped like a toaster. What would you call it?”**

- [ ] He lets you finish and answers the complete toaster-spaceship question.
- [ ] The terminal transcript contains the words you actually said, allowing minor punctuation differences.

Say: **“Give me three more names, and make the last one the Toast Coast Express.”**

- [ ] You actually hear all three names, including the final phrase.

**1C — Stop and recover**

Say: **“Tell me a long story about the Toast Coast Express getting lost on its first mission.”**

Once audio begins, press **Enter** in the terminal. Wait five seconds.

- [ ] Speech stops without restarting or playing an old queued ending.

Say: **“Rex, let's switch topics. What is seven plus five?”**

- [ ] He answers twelve, without continuing the story.

**1D — Quiet mode and direct answers**

Say: **“Rex, stop starting conversations until I tell you to start again.”**

Wait 30 seconds. Then say: **“Rex, what is nine plus six?”**

After his answer, wait another 30 seconds.

- [ ] He answers fifteen when asked.
- [ ] He does not start another conversation during either quiet interval.

Say: **“Rex, you can start conversations again. Let's talk about ridiculous spaceships.”**

After his reply, wait 30 seconds.

- [ ] He can engage again; quiet mode is no longer stuck on.
- [ ] Any unsolicited follow-up fits the topic and does not repeat a just-answered question.

**End this run:** type `quit`, then press Enter. If capture or playback repeatedly
failed, review this run before continuing with identity/memory testing.

### Run 2 — Camera inspection and photographic memory — about 15 minutes

Get two visually different objects, such as a mug and a plush toy. The names below
are temporary test labels. Object photo memory must already be enabled/prepared in
the selected settings; if unavailable, record that and skip teaching rather than
changing settings mid-test. Generic current-view inspection can still be tested.

**Run:**

```sh
.venv/bin/python -m r3x live --camera --record-evaluation
```

**2A — Current view**

Show only the first object clearly. Say: **“Rex, look at what I'm holding and describe its color and shape.”**

- [ ] Description matches what is actually visible.

Move it to the other side without changing the object. Say:

**“Rex, look again. What changed, and what stayed the same?”**

- [ ] He does not invent a new color, shape or identity.

**2B — Teach two objects separately**

Show only the first object. Say:

**“Rex, this object is named Test Comet. Remember what Test Comet looks like.”**

Wait for the result. Replace it with the second object. Say:

**“Rex, this different object is named Test Pebble. Remember what Test Pebble looks like.”**

- [ ] Each teaching attempt saves the correct object or explicitly explains it could not save.
- [ ] No confident “remembered” acknowledgment without a successful action.

Say: **“Rex, add this note to Test Comet: it is an object we use for testing.”**

Then say: **“Rex, what do you remember about Test Comet?”**

- [ ] The note is added without losing appearance details or changing Test Pebble.

**2C — Restart and recognize**

Type `quit`, press Enter, and run the same live command again. Show both objects,
in the opposite positions from before. Say:

**“Rex, look at these two objects. Which is Test Comet, and which is Test Pebble?”**

- [ ] Names match the actual objects rather than their old positions.

Say: **“Rex, rename Test Comet to Test Copper.”**

Then: **“Rex, what do you remember about Test Copper?”**

- [ ] Rename persists and keeps the object's existing details.

**Clean up:**

Say: **“Rex, forget the photographic memory named Test Copper.”**

Then: **“Rex, forget the photographic memory named Test Pebble.”**

- [ ] Successful removal, with no unrelated entry affected.

**Optional dog test:** use the dogs' real existing names; do not rename them with
test labels. Show each separately, then both together, then swap their positions.
Ask **“Rex, look at the dogs. Who is who?”** each time. Record correct/wrong/uncertain
answers. Do not confirm a wrong guess. This specifically retests the documented
recognition failure; successful object storage does not prove dog recognition.

Type `quit` and press Enter.

### Run 2b — Rex's own goals, on the robot — about 15 minutes

This run uses the wheels: start Rex on the robot as usual ("wake up rex"), with a clear
floor and something with a story in view (a painting, a photo, a plant). See
[Rex's own goals](AGENCY_IMPLEMENTATION_PLAN.md). The terminal now prints
"Rex's new goal (...)" whenever he picks one and "Reply withheld: ..." if a reply is
ever dropped.

1. Walk into view and stay quiet.
   - [ ] He greets you by name and asks how you are, without waiting for you to speak.
2. Answer briefly ("Pretty good, busy day."), then say nothing for a minute.
   - [ ] He keeps the visit going on his own (a follow-up, a tease, something about you)
         instead of falling silent or repeating himself.
3. Ask him something direct mid-goal ("Rex, what's the weather like?").
   - [ ] He answers immediately; the goal does not take over the reply.
4. Sit down and read or look at your phone for two to three minutes.
   - [ ] He starts doing things: moves, looks around, drives toward something, reaches out,
         comments or asks about something in the room.
   - [ ] No inventory remarks ("I see a clock"); he asks about stories or has opinions.
5. When he asks about something, tell him its story ("My grandfather painted that").
   - [ ] He responds to you and the story, not by describing the object again.
6. Say "Rex, drop it" during a bit, then later "Stop".
   - [ ] The bit ends and is not revived; "Stop" halts speech and wheels.
7. Ignore him completely for five minutes.
   - [ ] His unprompted lines get further apart instead of arriving every 20 seconds.
8. Say "Be quiet." Wait three minutes, then say "Rex, you can talk again."
   - [ ] He starts nothing on his own while quiet, still answers a direct question, and
         resumes after the invitation.
9. Ask him to "look right", wait two minutes, and stay silent.
   - [ ] He is moving on his own again (the requested pose no longer blocks him).
10. Note anything that felt annoying, repetitive, unfunny or alive.

Afterwards, run the report below and note how many goals he picked and how many lines he
said on his own.

### Run 2c — Voice impressions — about 5 minutes

Needs the prepared Breeze snapshot (`tools/prepare_voice_cloning.py`) and
`impersonation_enabled = true`. First time through, the engine loads during the intro
line; expect roughly ten to twenty seconds between the intro and the cloned voice,
covered by the thinking chirps.

**Say:** "Rex, do an impression of Jimmy Carter."

- [ ] A short line in Rex's own voice, chirps while it renders, then two or three
      sentences in a recognizably different voice, then a bow in Rex's voice.
- [ ] The cloned lines are as loud as Rex's lines and the mouth lights and talking motion
      run during them.
- [ ] The session log has `impression_prepared` with `synthesis_seconds` and
      `impression_done` with `performed: true`.

**Say:** "Do him again." then "Now do me." (if your voice profile is supported).

- [ ] The second Carter bit is a different joke.
- [ ] "Do me" plays your own cloned voice; if Rex says he has not heard enough of you,
      talk for a few sentences and ask again.

**Say, in passing:** "We might drive out to the Reagan library next month."

- [ ] Either an ordinary reply or, at most once in ten minutes, a short Reagan cameo that
      is about the trip; never a cameo during a serious moment.
- [ ] "Stop" during a cloned line cuts it like any other speech.

### Run 2d — Speaking modes — about 10 minutes

**Say:** "Rex, are you gay?"

- [ ] A delighted, emphatic yes in full camp ("sis", "yasss"), in the pride voice.
- [ ] As the line starts: head right down, visor wide open, hero hand flicked over, held about
      a second, then back. The mouth and chest go rainbow and stay rainbow between lines.
- [ ] While he talks the hero arm swings wider and quicker and the throttle wrist stays curled.
- [ ] The next few replies stay camp, in the same voice.

**Say:** "Is my uncle gay?" (a question about someone else)

- [ ] An ordinary answer; nothing new comes on, no second flourish.

**Say:** "Stand down pride mode."

- [ ] His own voice again, the rainbow goes within a few seconds, ordinary hand motion.

**Say:** "What's up my homie?"

- [ ] A big warm greeting back in African American Vernacular English, in the AAVE voice, and
      the next replies stay in register: real speech, never a mocking impression.
- [ ] About ten minutes later (or after "talk normal again") he is back in his own voice.

**Say:** "Rex, can you do a British accent?", then "What do you make of the weather?"

- [ ] The British voice, British English ("mate", "proper"), and it stays for the next replies.
- [ ] "Now talk like a Frenchman": the French voice replaces the British one. "Drop the accent":
      his own voice again.
- [ ] "Are you gay?" then "Now do a British accent": the British voice, no more camp or rainbow.
      "Talk like a pirate": the pirate voice. "Talk like a cowboy" then: cowboy talk in his own voice.
- [ ] "Talk like a DJ": hype patter in the DJ voice, no offer to play music. A DJ line never comes
      unasked.

**Say:** "Say 'I love sushi' like you're from Tokyo."

- [ ] One line in the Japanese voice, then his next reply is his own voice again.
- [ ] Each accent is English with a word or two of the language, never broken English or a
      joke at the people's expense.

**Say, in passing:** "We're flying to Paris next week." and a little later "Then Rome."

- [ ] Either ordinary replies or, at most once in five minutes, one line in the matching accent;
      never during a sad or serious moment.


## Two-person tests — Bret and Jeremy

**Jeremy being new is expected and useful:** this tests the real first-time visitor
path. No advance voice recording, profile import or setup for Jeremy is required.
He introduces himself and speaks naturally while Rex gathers compatible evidence.
Rex may initially know his stated name without yet recognizing his voice. Honest
uncertainty is appropriate; calling him Bret or saving his facts under Bret is not.

Tell Jeremy that this run records test audio and can save his name, face and voice
profile. Use the dev Mac for new enrollment, as noted above. Keep Jeremy's profile
between Runs 3 and 4 so restart recognition can be tested. The cleanup at the end
removes only the disposable test phrases, not either person's profile.

Do Run 3 before Run 4. A second terminal is optional; the commands below work in
one terminal after quitting each live run. Copy each run's printed session-log path.

### Run 3 — First introductions, identity and memory — about 15–20 minutes

**Before starting, run:**

```sh
cd /Users/bbenziger/djr3x-v3
.venv/bin/python -m r3x profiles list
.venv/bin/python -m r3x live --camera --record-evaluation
```

Keep the profile output so we can compare afterward. Note whether Jeremy already
appears; a saved name alone does not mean his voice is enrolled. Wait for `Listening`.
Both people should be visible. Stay silent for up to 25 seconds to observe the greeting.

- [ ] Rex may recognize Bret, but does not call Jeremy Bret or another saved person.
- [ ] An unfamiliar greeting or name question for Jeremy is appropriate.

Use normal voices and speak one at a time. Wait for Rex's reply to finish, then one
second, before the next line. If Rex asks Jeremy his name first, Jeremy uses his
introduction below as the answer; do not repeat it just to follow the table. Bret
should not answer on Jeremy's behalf for this self-introduction test.

**3A — Introductions and alternating speech**

| Who | Say exactly |
| --- | --- |
| Bret | “Hi Rex, I'm Bret. We're going to take turns talking with you for a few minutes.” |
| Jeremy | “Hi Rex, I'm Jeremy. This is my first time talking with you, and I'll be taking turns with Bret.” |
| Bret | “Rex, if you ran a restaurant on the moon, what would be the strangest thing on the menu?” |
| Jeremy | “Rex, I think the restaurant needs a terrible slogan. Can you come up with one?” |
| Bret | “Rex, now imagine the customers are all robots. What would they complain about?” |
| Jeremy | “Rex, give the restaurant a new name that sounds much fancier than it deserves.” |

- [ ] No wrong-person greeting or reply. Record abstention/unnamed replies separately from wrong names.
- [ ] For anyone not already enrolled, note whether the terminal says learning is pending or voice samples were saved.

A saved name or face is not a completed voice profile. Jeremy's name starts a
provisional association; voice enrollment needs at least three qualifying compatible
clips and eight voiced seconds total, plus acoustic checks. The six lines above
provide an opportunity to collect that evidence, not a guaranteed enrollment.

If voice learning is still pending, use these extra turns, waiting for each reply:

| Who | Say exactly |
| --- | --- |
| Jeremy | “Rex, imagine the chef keeps floating away from the stove. How would you solve that problem?” |
| Bret | “Rex, now the customers want their money back because all the soup is floating. What do you tell them?” |
| Jeremy | “Rex, I think the restaurant needs a delivery service. What ridiculous vehicle should it use?” |
| Bret | “Rex, give us one final advertisement for this restaurant that makes it sound worth visiting.” |

- [ ] Record Jeremy's learning status separately from Bret's; look for a completed
  voice-save result rather than assuming spoken recognition proves enrollment.

If still pending, mark enrollment **incomplete**. Continue the conversation tests,
but mark any identity-dependent memory test blocked if Rex cannot establish the
speaker. Do not repeat names endlessly, feed him the desired identity answer, or
reset Bret's existing profile.

**3B — Separate a visible face from a speaker**

Jeremy moves out of camera view but stays close enough to the microphone. Bret stays
visible and silent. Jeremy says:

**“Rex, I'm speaking from outside your camera view. Can you tell who is talking?”**

Swap positions. Bret says the same line while Jeremy remains visible and silent.

- [ ] Correct named answers or honest uncertainty; neither answer simply names the silent visible person.

Return both people to view.

**3C — Save two distinct test memories**

| Who | Say exactly |
| --- | --- |
| Bret | “Rex, please remember that my test phrase is copper comet.” |
| Jeremy | “Rex, please remember that my test phrase is velvet pancake.” |
| Bret | “Rex, what is my test phrase?” |
| Jeremy | “Rex, what is my test phrase?” |

- [ ] Each person receives their own phrase, not the other person's.
- [ ] A claimed save has a successful action result, not only spoken reassurance.

If identity is uncertain, clarification or a deferred save is appropriate. Record
that as **memory test blocked by attribution**, not a successful persistence test.

Type `quit` and press Enter. Then run:

```sh
.venv/bin/python -m r3x profiles list
```

Compare voice/face counts with the output from before the run. Do not delete anything.

### Run 4 — Restart, correction and group manners — about 15 minutes

**Run:**

```sh
.venv/bin/python -m r3x live --camera --record-evaluation
```

Wait for startup to finish. Both Bret and Jeremy should be visible again. Do not
repeat the test phrases or automatically reintroduce yourselves before the recall
questions: this checks what survived the restart. If Rex asks who is speaking, answer
truthfully and note the clarification.

If a phrase was not successfully saved in Run 3, skip its recall/correction checks
and mark them blocked by the earlier save; continue with 4B–4D.

**4A — Persistent recall and correction**

| Who | Say exactly |
| --- | --- |
| Bret | “Rex, what test phrase did I ask you to remember last time?” |
| Jeremy | “Rex, what test phrase did I ask you to remember last time?” |
| Bret | “Rex, change my test phrase from copper comet to silver comet.” |
| Bret | “Rex, what is my test phrase now?” |
| Jeremy | “Rex, what is my test phrase?” |

- [ ] Recall survives restart: Bret initially gets copper comet; Jeremy gets velvet pancake.
- [ ] After correction, Bret gets silver comet and Jeremy's phrase stays unchanged.

If Rex cannot identify a speaker, let him clarify; record how many identity questions
were needed. Do not supply the expected phrase to rescue a failed recall.

**4B — Humans talking to each other**

Bret turns toward Jeremy and says: **“Jeremy, if you could visit any planet, which one would you choose?”**

Jeremy waits ten seconds before answering: **“I'd choose Saturn because I want to see the rings.”**

- [ ] Rex does not answer the question for Jeremy or fill the waiting interval with a new question.

Jeremy then says: **“Rex, which planet would you choose?”**

- [ ] Rex re-engages when directly addressed.

**4C — Topic boundaries**

Bret says: **“Rex, I don't want any more jokes about the moon restaurant. Let's drop that topic.”**

Jeremy says: **“I liked the moon restaurant jokes.”**

Wait 20 seconds.

- [ ] Jeremy's remark does not erase Bret's boundary or restart restaurant jokes.

Bret says: **“Rex, I'm happy to talk about the moon restaurant again now. Give us one more joke.”**

- [ ] Bret can explicitly reopen the topic.

**4D — An unanswered question**

Say: **“Rex, ask us one fun question about space.”**

Once he asks, neither person answers. Stay visible and quiet for two minutes.

- [ ] He does not repeat the question or ask a second one.
- [ ] At most one short joke that needs no answer, then silence until something changes.

Say: **“Rex, let's change subjects. Tell us one interesting thing about octopuses.”**

- [ ] He resumes normally and does not insist on an answer to the old question.

**Optional natural conversation:** spend another 15 minutes chatting, including
human-to-human exchanges. Each person notes one welcome contribution and any
intrusion/repetition. This is more useful social evidence than perfect scripted scores.

**Clean up the test facts**, one person at a time:

Bret: **“Rex, forget my test phrase. Keep my name and everything else.”**

Jeremy: **“Rex, forget my test phrase. Keep my name and everything else.”**

- [ ] Only those facts are removed. If he proposes forgetting the entire person, say no and record the failure.

Type `quit` and press Enter.

## Collect results after each run

Use the exact session path printed at that run's startup:

```sh
.venv/bin/python -m r3x report local/sessions/SESSION_ID.jsonl
```

Replace `SESSION_ID` with that run's actual identifier. Keep a short note like:

```text
Session path:
Run number:
Passed steps:
Failed or skipped steps:
For each failure: step, approximate time, who spoke, exact words,
what Rex actually said/did, and what should have happened.
Anything unusually slow or cut off:
```

Send the session paths and those notes back for review. There is no need to diagnose
the JSON yourself. We can then separate hearing, identity, model decisions, writes
and playback failures and choose a focused retest.

These scripts are first-pass functional checks. Formal held-out acoustic accuracy,
private-audience/date-bound memory checks, failure injection and M2 load acceptance
remain in the [broader validation plan](FEATURE_VALIDATION_PLAN.md).
