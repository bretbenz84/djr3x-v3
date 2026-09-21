# Live test script

Run this in order. Quoted lines are things to **say aloud**; commands go in the
terminal. Checkboxes are for the human tester. A pass means the observed behavior
matches the check, not that Rex used particular words.

Start with Run 1 alone. Runs 2–3 need Bret and Jeff. Run 4 uses two visually
different household objects; dogs are an optional follow-up. Allow roughly an
hour for the scripted runs, or split them over several days.

These commands use the existing v3 configuration and saved memory, real microphones,
camera, paid conversation/TTS providers and local evaluation recordings. Use the
dev Mac for new voice enrollment; robot enrollment still requires the unimplemented
microphone servo hold. Stop v2 before starting. Do not clear existing profiles.

## 1. Start Rex and test basic conversation — about 10 minutes

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

After his reply, say **“I was thinking we could…”**, pause for two seconds, then
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

## 2. Bret and Jeff: identity and memory — about 15 minutes

**Before starting, run:**

```sh
.venv/bin/python -m r3x profiles list
.venv/bin/python -m r3x live --camera --record-evaluation
```

Keep the profile output so we can compare afterward. Wait for `Listening` and the
startup greeting. Both people should be visible. Use normal voices; each person
waits for Rex's reply to finish before the next line.

**2A — Introductions and alternating speech**

| Who | Say exactly |
| --- | --- |
| Bret | “Hi Rex, I'm Bret. We're going to take turns talking with you for a few minutes.” |
| Jeff | “Hi Rex, I'm Jeff. I'm the other person here, and I will be talking with you too.” |
| Bret | “Rex, if you ran a restaurant on the moon, what would be the strangest thing on the menu?” |
| Jeff | “Rex, I think the restaurant needs a terrible slogan. Can you come up with one?” |
| Bret | “Rex, now imagine the customers are all robots. What would they complain about?” |
| Jeff | “Rex, give the restaurant a new name that sounds much fancier than it deserves.” |

- [ ] No wrong-person greeting or reply. Record abstention/unnamed replies separately from wrong names.
- [ ] For anyone not already enrolled, note whether the terminal says learning is pending or voice samples were saved.

A saved name or face is not a completed voice profile. If still pending, each person
adds two ordinary sentence-length turns about the restaurant. If still pending after
that, mark enrollment **incomplete**; do not keep repeating names to force a pass.

**2B — Separate a visible face from a speaker**

Jeff moves out of camera view but stays close enough to the microphone. Bret stays
visible and silent. Jeff says:

**“Rex, I'm speaking from outside your camera view. Can you tell who is talking?”**

Swap positions. Bret says the same line while Jeff remains visible and silent.

- [ ] Correct named answers or honest uncertainty; neither answer simply names the silent visible person.

Return both people to view.

**2C — Save two distinct test memories**

| Who | Say exactly |
| --- | --- |
| Bret | “Rex, please remember that my test phrase is copper comet.” |
| Jeff | “Rex, please remember that my test phrase is velvet pancake.” |
| Bret | “Rex, what is my test phrase?” |
| Jeff | “Rex, what is my test phrase?” |

- [ ] Each person receives their own phrase, not the other person's.
- [ ] A claimed save has a successful action result, not only spoken reassurance.

If identity is uncertain, clarification or a deferred save is appropriate. Record
that as **memory test blocked by attribution**, not a successful persistence test.

Type `quit` and press Enter. Then run:

```sh
.venv/bin/python -m r3x profiles list
```

Compare voice/face counts with the output from before the run. Do not delete anything.

## 3. Restart, correction and group manners — about 15 minutes

**Run:**

```sh
.venv/bin/python -m r3x live --camera --record-evaluation
```

Wait for startup to finish. Do not repeat the test phrases before the recall questions.

**3A — Persistent recall and correction**

| Who | Say exactly |
| --- | --- |
| Bret | “Rex, what test phrase did I ask you to remember last time?” |
| Jeff | “Rex, what test phrase did I ask you to remember last time?” |
| Bret | “Rex, change my test phrase from copper comet to silver comet.” |
| Bret | “Rex, what is my test phrase now?” |
| Jeff | “Rex, what is my test phrase?” |

- [ ] Recall survives restart: Bret initially gets copper comet; Jeff gets velvet pancake.
- [ ] After correction, Bret gets silver comet and Jeff's phrase stays unchanged.

If Rex cannot identify a speaker, let him clarify; record how many identity questions
were needed. Do not supply the expected phrase to rescue a failed recall.

**3B — Humans talking to each other**

Bret turns toward Jeff and says: **“Jeff, if you could visit any planet, which one would you choose?”**

Jeff waits ten seconds before answering: **“I'd choose Saturn because I want to see the rings.”**

- [ ] Rex does not answer the question for Jeff or fill the waiting interval with a new question.

Jeff then says: **“Rex, which planet would you choose?”**

- [ ] Rex re-engages when directly addressed.

**3C — Topic boundaries**

Bret says: **“Rex, I don't want any more jokes about the moon restaurant. Let's drop that topic.”**

Jeff says: **“I liked the moon restaurant jokes.”**

Wait 20 seconds.

- [ ] Jeff's remark does not erase Bret's boundary or restart restaurant jokes.

Bret says: **“Rex, I'm happy to talk about the moon restaurant again now. Give us one more joke.”**

- [ ] Bret can explicitly reopen the topic.

**3D — An unanswered question**

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

Jeff: **“Rex, forget my test phrase. Keep my name and everything else.”**

- [ ] Only those facts are removed. If he proposes forgetting the entire person, say no and record the failure.

Type `quit` and press Enter.

## 4. Camera inspection and photographic memory — about 15 minutes

Get two visually different objects, such as a mug and a plush toy. The names below
are temporary test labels. Object photo memory must already be enabled/prepared in
the selected settings; if unavailable, record that and skip teaching rather than
changing settings mid-test. Generic current-view inspection can still be tested.

**Run:**

```sh
.venv/bin/python -m r3x live --camera --record-evaluation
```

**4A — Current view**

Show only the first object clearly. Say: **“Rex, look at what I'm holding and describe its color and shape.”**

- [ ] Description matches what is actually visible.

Move it to the other side without changing the object. Say:

**“Rex, look again. What changed, and what stayed the same?”**

- [ ] He does not invent a new color, shape or identity.

**4B — Teach two objects separately**

Show only the first object. Say:

**“Rex, this object is named Test Comet. Remember what Test Comet looks like.”**

Wait for the result. Replace it with the second object. Say:

**“Rex, this different object is named Test Pebble. Remember what Test Pebble looks like.”**

- [ ] Each teaching attempt saves the correct object or explicitly explains it could not save.
- [ ] No confident “remembered” acknowledgment without a successful action.

Say: **“Rex, add this note to Test Comet: it is an object we use for testing.”**

Then say: **“Rex, what do you remember about Test Comet?”**

- [ ] The note is added without losing appearance details or changing Test Pebble.

**4C — Restart and recognize**

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

## 5. Collect results after each run

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
