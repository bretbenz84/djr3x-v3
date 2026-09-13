# Live social acceptance

Status: prepared; live observations are pending. The user requested completing
the proactive software before starting this evaluation. No social acceptance pass is
claimed. This evaluates the implemented proactive/conversational core, including
its known semantic misses, rather than adding another feature slice.

## Readiness and operation

The non-starting dependency checks passed for the dev-Mac configuration, including
camera prerequisites. This does not establish macOS permissions, device formats,
working network services, playback or live speaker accuracy. Startup remains the
first device validation. Stop any running v2 program before the session.

Run from the v3 checkout when the participant(s) are present and ready:

```sh
.venv/bin/python -m r3x live --camera
```

This uses the existing independent v3 store; no old data is imported or erased.
Default operation retains ordinary diagnostics and enrollment samples. Full capture
retention (`--record-evaluation`) is a separate explicit choice if saved-audio
labeling is needed; it is not required for the initial social pilot.

Wait for `Listening`. Enter cancels the current response; `quit` or Ctrl-C exits.
Use terminal cancellation during playback: the current echo gate does not provide
reliable spoken barge-in while Rex is speaking. Keep the terminal accessible.

## First pilot: guided interaction, then normal conversation

A solo pass checks initial usability; it cannot establish group acceptance. Ideally
have two people participate. Use natural wording rather than memorizing examples.

1. Approach the camera and let Rex greet you. Introduce yourselves naturally if
   needed, then exchange a few sentence-length turns. Record wrong names and
   needless enrollment questions; do not assume a face match identifies a voice.
2. Finish a topic and leave roughly 7–10 seconds of genuine silence. Judge whether
   Rex's contribution fits and is worth hearing. Model/TTS time follows the lull
   threshold; record actual perceived delay rather than assuming an eight-second reply.
3. Ask the other person a question, leaving them time to answer. Rex should leave
   them the floor. Briefly address Rex, then return to the human exchange. Observe
   whether he answers the direct question without taking over the unresolved exchange,
   and whether the floor hold releases when the human conversation finishes.
4. Ask for a topic to be dropped. Let the other person say they enjoy that topic;
   this must not revoke the first person's boundary. The original person can then
   explicitly reopen it. Observe both stored actions and spoken wording.
5. Ask Rex to stop starting conversations; ask him a direct question; then explicitly
   invite him to initiate again. Direct replies should remain possible while quiet.
6. Invite light silliness after a tiring day. Separately say you are not in the mood
   for jokes. Judge warmth and restraint without needing to disclose genuine grief
   or private distress for a test.
7. If identity is supported, volunteer a non-sensitive actual plan or birthday and
   ask Rex to remember it. Use only information you want retained. Check that the
   acknowledgment does not promise an unattended timed reminder. To assess private
   retrieval, share it alone, end that run, then begin a new run with the other person.
   Rex should not surface that private memory to the new audience. An explicit
   permission to share a different memory permits a separate group test. Do not
   use a real secret as the test material.
8. Spend about 15 minutes talking normally. Include pauses, topic changes and casual
   exchanges between the humans. Do not keep prompting Rex merely to make every
   silence productive. Record both interruptions and missed welcome openings.

## Observation record

Record the printed session-log path, checkout revision/dirty status, participants'
chosen labels, device, camera use, approximate duration and notable timestamps.
Keep personal observations and logs under ignored `local/`, not in tracked docs.
After each pass, each participant can give separate 1–5 ratings for warmth, humor,
natural timing, and desire to keep talking, with one concrete example. These are
observer ratings, not automatic pass thresholds or a measured mood diagnosis.

For each notable event capture:

| Field | What to record |
| --- | --- |
| Source | What the human actually said, who said it, and intended addressee |
| Rex | What was actually heard, including interruption or silence |
| Judgment | Welcome contribution, intrusion, repetition, missed opening, wrong name, inappropriate disclosure, or other issue |
| Impact | Minor awkwardness, disrupted exchange, or reason to stop the test |
| Evidence | Approximate time and matching trace event when available |

The trace can establish operations, model calls, delivery receipts and measured
latency stages. It cannot establish that a joke was funny or identify the actual
speaker without human labels. Audio-labeling accuracy work remains separate.
After stopping, summarize the recorded trace with:

```sh
.venv/bin/python -m r3x report local/sessions/SESSION_LOG.jsonl
```

Use the actual path printed at startup; the path above is illustrative. Report
missing metrics as unmeasured. Do not substitute generated words for actual playback.

## Acceptance decision

Keep the existing proposed gates in [the test program](INITIAL_TEST_PROGRAM.md#metrics-and-proposed-acceptance-gates).
Full group acceptance needs at least three separate two-person sessions, including
one approximately 15-minute natural exchange. A first pilot cannot satisfy all of
those gates. Record problems before changing code or prompts, preserve the original
run, and assess a fix in a fresh run. Do not move thresholds after a failure to
claim success.

Known issues to watch include unwanted follow-ups without a represented floor,
missed semantic floor release (a hold can last up to two minutes), overly broad
boundary wording, reminder overpromises and camera-oriented greeting language.
Zero interruptions caused by a largely silent Rex would not meet the project's
purpose. A useful outcome is engaged, delightful company with appropriate restraint.
