# Core operations and morning test

The core is implemented. Real room acceptance is still open. Overnight work opened
no microphone, played no audio, captured no video, and imported no personal v2 data.
The synthetic participant used for integration checks lives only in separate
`local/validation/` databases, never in the live store.

## First live session

From `/Users/bbenziger/Local/djr3x-v3`, after stopping v2:

```sh
.venv/bin/python -m r3x live --camera --record-evaluation
```

Omit `--camera` for an audio-only test. Omit `--record-evaluation` to retain only
accepted enrollment audio and bounded unverified candidate audio, rather than all endpointed test captures. The copied
camera selection remains disabled in settings; the flag enables it for this run.
macOS permission prompts and actual device formats have not been tested in v3.
The camera requires ffmpeg, a unique AVFoundation video-device name, and the pinned
SCRFD-10G and EdgeFace-S models in `local/models/faces/`. It requests 1280×720
UYVY at 30 fps; a separate CPU worker analyzes the latest frame at most twice per
second. Models are already prepared on this Mac. It reports failure rather than opening an unrelated device.

`doctor` is an optional diagnostic; live startup checks readiness itself.
The terminal shows listening, your transcript, Rex's proposed speech, actual playback,
actions, and failures. Camera frames and resource details stay in the JSON session
log whose path is printed at startup. “Preparing speech” does not mean it was played.
An `ElevenLabsConnectTimeout` means the speech service connection timed out before
audio arrived; it does not establish a speaker-device failure.

Wait for `Listening`. Enter or `stop` + Enter cancels; `quit` or Ctrl-C exits.
The default echo gate excludes the playback queue/window and its 300 ms tail.
Capture splits when that gate changes, preserving human speech before and after
playback. Waiting for TTS audio no longer gates the microphone. Simultaneous human
speech within playback is still excluded; no source separation or AEC is claimed.
Use terminal stop during playback. Spoken stop is available on trusted non-echo
captures. Headset isolation can be explicitly configured; no AEC is implemented.

The terminal reports `Rex stayed quiet` when the conversation model chooses not to
answer another device or a background fragment. That decision applies to one turn.
Tentative session-track matches use cosine .30 and a .12 competing-track margin;
they carry no name, do not update reference vectors, and cannot authorize self
identity or enrollment. Strong track updates still use .55, named profiles .50,
and new tracks after the first require at least one voiced second. These are car
session tuning values, not a held-out accuracy guarantee. Two-sided acoustic
window changes can propose approximate splits, with source IDs/offsets retained;
their derived audio cannot be used for enrollment or personal writes.

For numbered, user-labeled sessions, a silent local diagnostic is available:

```sh
.venv/bin/python tools/check_labeled_session.py local/recordings/SESSION_ID
```

It expects the `human-labels.json` review sidecar and the matching session trace.
Labels score predictions afterwards and never seed the tracker. It reuses recorded
VAD evidence and reconstructs playback windows from receipts through the production
endpoint/perception/session. Old recordings lack per-frame echo flags, so this
reconstruction is approximate. Mixed-clip speaker order alone cannot measure exact
boundary accuracy. Reports and derived embeddings are private diagnostics under
`local/validation/`; no live profile is read, enrolled, or changed.

Suggested first pass:

1. Bret and Jeff each give an ordinary self-introduction, then alternate several
   sentence-length turns. Include a hobby or recent activity; no recitation routine.
2. Inspect `speaker_evidence`, `voice_learning_progress`, and `voice_learning_saved`.
   A name starts a hypothesis. Initial defaults require three distinct compatible
   turns, at least two voiced seconds each and eight total, four transcript words
   per qualifying sample, and anchor/cluster agreement. No DoA or mouth evidence is
   used. A competing voice cannot open a second chain for the same person.
3. Once acoustic support is established, volunteer a clear fact and ask Rex to
   remember it. Inspect `tool_proposed` and `tool_result`, including subject,
   source speaker, status, source turn, and actual output delivery.
4. Exit and start again. Ask for that fact, then correct it. Current recall should
   use the corrected value; the original quote remains in correction history.
5. Try the name-question/activity, “you're creepy,” mentioned Jeff, ambiguous
   nickname, and onboarding-correction cases from the acceptance plan. Include
   short replies, one person off camera, rapid switches, and overlap.
6. Cancel a reply during generation and playback. Check the final spoken words,
   interruption receipt, queued tails, microphone shutdown, and playback underruns.

Voice matching starts at cosine .50 with .07 margin. Session-track matching uses
.55; disagreement below .45 across windows abstains. Learning requires at least
.55 window consistency and agreement with the original anchor and sample cluster.
Ordinary tracks expire after five minutes; groups with retained candidate evidence
remain matchable for up to 24 hours after their last added sample/name anchor.
Pending questions expire after two minutes. These are
initial heuristics, not calibrated probabilities. Window consistency is **not a
validated overlap detector**. Nearby similar voices and short/mixed speech need
the held-out dataset before any accuracy claim.

Camera tracks report presence, coarse position and recognized or provisional person
IDs. Clear compatible crops can persist through an introduction or an already
supported voice with a sole prominent face. An introducer can name a friend without
assigning their own voice to that friend. Ambiguous faces may need a targeted name
question or closer look. Saved face profiles survive restart; temporary hypotheses
do not. Supplied sample photos are not automatically imported.

Use `profiles list` for voice/face reference counts, `profiles faces PERSON_ID` for
face provenance, or `profiles reset-face PERSON_ID` to remove a wrong face profile
without changing the voice/name. Reset blocks automatic relearning until an explicit
introduction. `forget` also removes stored face crops and vectors. Face-only
corrections can be interpreted conversationally through `reset_face_identity`.
See [face implementation](FACE_ATTRIBUTION_PLAN.md#implemented-dev-mac-face-path)
for provisional recognition/quality thresholds and outstanding hardware validation.
No visual observation certifies a voice. False self-identification also remains
a limitation: this learns voice-to-claimed-person associations, not legal identity.

## Inspect sessions

```sh
.venv/bin/python -m r3x report local/sessions/SESSION_ID.jsonl
.venv/bin/python -m r3x profiles list
.venv/bin/python -m r3x profiles recordings PERSON_ID
```

Profiles use stable IDs; labels and explicit aliases are lookup data. The core
keeps at most 100 participant records, 12 current acoustic tracks with five recent
references each, 16 conversation messages, and 32 claims in a context/recall result.
Claims beyond the retrieval window remain stored. No semantic embedding model or
automatic summarizer runs. Accepted profiles stop growing after the initial chain;
there is no passive second writer refreshing voiceprints.

Reports distinguish ASR, speaker analysis, context, model requests, first generated
PCM, playback queue, actual first audio, and total turn time when those measurements
exist. Resource events every 30 seconds contain process peak RSS, CPU time, queue
occupancy, and thread count. Peak RSS is not a current-RSS measurement. Text-only
checks have no actual playback latency or underrun result.

Ordinary replies use one primary model generation. An explicit tool may use one
result continuation; a targeted question can be delivered directly from its tool
text. Intermediate provider commentary is unspoken. Final-answer text streams;
unphased text on a tool-enabled request is held until completion determines whether
it accompanies a tool. There is no preliminary classifier, automatic retry, or
substitute provider. A failed write never authorizes a save acknowledgment.

The active store is `local/core.sqlite3`. Its claims retain subject, said-by person
or unresolved track, original source text/turn, status, and correction links. An
accepted claim means that source said it, not that its factual content was verified.
An unresolved self-report cannot guess a roster person even for a candidate claim.
Third-party writes require a unique explicit name/alias from the current utterance;
an ambiguous nickname or unresolved pronoun needs clarification. A cancelled new
name is retired from active lookup. Provisional acoustic proposals never survive
restart as established identity.

## Saved-audio replay

`--record-evaluation` creates `local/recordings/SESSION_ID/*.wav` and an
`unreviewed.jsonl` inventory. It captures endpointed utterances, not continuous room
audio; it cannot by itself measure speech the live endpoint missed. Use separately
saved continuous recordings when investigating capture recall.

Create independent labels before evaluating. Keep enrollment/tuning/evaluation
files separate, and use the actual v3 person IDs for enrolled people. A real unknown
person gets a separate dataset ID. Minimal manifest example (replace the digest
and timing with reviewed values):

```json
{
  "version": 1,
  "roster": {"V3_PERSON_ID": "Participant A", "UNKNOWN_DATASET_ID": "Unknown participant"},
  "clips": [{
    "id": "session-2-turn-1",
    "file": "turn-1.wav",
    "sha256": "SHA256_OF_THE_WAV_FILE",
    "session": "session-2",
    "device": "selected Mac microphone",
    "condition": "near, seated",
    "split": "evaluation",
    "resolvable": true,
    "overlap": false,
    "segments": [{"start": 0.3, "end": 3.6, "speakers": ["V3_PERSON_ID"]}]
  }]
}
```

WAVs must be independent relative files, mono 16 kHz PCM16. File digests are checked.
Duplicate files across splits and exact enrolled PCM reused as evaluation are
rejected. Human speaker/overlap labels are applied after inference; they do not
steer the tracker. The global overlap label conservatively marks that clip's
captures mixed; use narrowly labeled clips when scoring overlap separately.

```sh
.venv/bin/python -m r3x replay local/dataset/manifest.json
# Explicit paid language inference, with text-only output and frozen writes:
.venv/bin/python -m r3x replay local/dataset/manifest.json --brain
# Separate encoder diagnostic; human timing is supplied, not production VAD:
.venv/bin/python -m r3x replay local/dataset/manifest.json --oracle-segments
```

Replay uses the same endpoint, perception, Session, tracker and state validator as
live. It uses a consistent in-memory snapshot of the live core database, resets
tracks across dataset sessions, and never changes enrollment or personal facts.
Without `--brain`, no conversational model is called. Reports and traces are saved
under a new `local/validation/RUN_ID/` directory. They include wrong names,
abstentions, named precision/coverage, per-person/device/condition breakdowns,
unknown/mixed/short cohorts, track fragmentation/merges, and missing/failed clips.
Capture-duration counts are not frame-level diarization error rates.

The proposed clean gate requires at least 100 held-out captures, ≥95% precision
among named decisions and ≥85% named coverage. It cannot pass through abstention.
It is only one diagnostic gate: real unknown/mixed cohorts, three group sessions,
including a roughly 15-minute exchange, and matched v2 latency remain required.

## Independent profile import and repair

No personal import was run overnight. These commands are explicit offline actions;
they hold the same v3 lease as `live`, excluding stale in-memory state.

```sh
.venv/bin/python -m r3x profiles preview-v2 --source /ABSOLUTE/PATH/TO/people.db --source-person-id SOURCE_ID --out local/imports/preview.json
.venv/bin/python -m r3x profiles apply-import local/imports/preview.json
.venv/bin/python -m r3x profiles reembed PERSON_ID --out local/imports/reembed.json
.venv/bin/python -m r3x profiles apply-reembed local/imports/reembed.json
.venv/bin/python -m r3x profiles review PERSON_ID --status supported
```

Supply the actual source database path and selected IDs. Repeat `--source-person-id`
for another participant. The importer uses a read-only SQLite connection and
consistent backup, selects only requested labels/aliases/original audio/CAM++
references, and records a snapshot digest and new ID mapping. It imports no facts.
Original float32 WAV bytes are preserved alongside derived PCM16. Unsupported
rates/formats or inconsistent source digests are rejected, never silently converted.

Imported profiles begin `suspect`. Legacy vectors retain a separate unverified
v2 namespace and are not used for current identification. Re-embedding previews
run the current VAD/CAM++ path on retained originals; applying verifies namespace,
source digests, person IDs, and revision. Old namespaces remain preserved. Human
review can enable a profile only with three current-namespace recordings; copied
profiles are not ground truth. Prints without original audio cannot be re-embedded.

```sh
.venv/bin/python -m r3x profiles review PERSON_ID --status suspect
.venv/bin/python -m r3x profiles export RECORDING_ID --out local/exports/sample.wav
.venv/bin/python -m r3x profiles delete-recording RECORDING_ID
.venv/bin/python -m r3x profiles reset-voice PERSON_ID
.venv/bin/python -m r3x profiles forget PERSON_ID
```

Removing a bad recording disables that profile pending review. `reset-voice`
removes its voice data while preserving names and claims, allowing a fresh natural
enrollment. Suspect samples cannot be silently reactivated by a new introduction.
`forget` removes the person, claims from/about them, accepted audio, derived
references, active associations, and registered recording/export/preview files.
Changed files fail cleanup rather than being deleted blindly; a failure is not
acknowledged as completed. SQLite changes roll back on failure; already removed
artifact files stay removed and a retry can finish the operation.

Private session traces and manually copied/unregistered datasets are separate
diagnostic artifacts, not active retrieval. They may still contain historical
text or user-made copies and need explicit deletion when discarding that evidence.
The program cannot locate arbitrary copies outside its managed scratch data.

## Developer checks

```sh
.venv/bin/python tools/run_checks.py
.venv/bin/python -m r3x scenario tests/fixtures/core_memory.json
# Explicit paid synthetic checks; none open live devices or play sound:
.venv/bin/python tools/check_meaning.py --variants
.venv/bin/python tools/check_conversation_audio.py
```

The ordinary runner blocks network, model/device imports, subprocesses, native
loads, v2 files, and real v3 state; fixtures use temporary databases. The full
synthetic-audio integration uses real Qwen/Silero/CAM++/OpenAI/ElevenLabs with a
separate validation database and silent replies. Its success does not establish
human voice discrimination, camera accuracy, physical cancellation, or speech quality.

## Patient listening check

The September 10 listening repair increases default endpoint silence from 0.6 to
1.2 seconds. The dev Mac settings inherit this default. Non-echo speech resuming
before playback cancels the pending reply while retaining the prior user words
for context. The console reports “Still listening”. This does not reset pending
identity questions or perform an explicit stop. The same conversational generation
can choose `remain_silent(unfinished_thought)` for thinking pauses and incomplete
clauses; it does not add a separate classifier. Raw ASR text remains unchanged.

After restarting live, tell Rex a story with natural pauses and trailing clauses,
then finish the thought. He should leave room rather than ask for the missing
ending. Also try a complete short answer after his question. Live acceptance is
still required: a 1.2-second pause is not proof of sentence completion, Qwen can
mishear homophones, and human speech during the playback echo gate remains
unavailable. This change does not provide echo cancellation or full barge-in.


For the continuation/introduction repair, use the same live command after restart.
Try a travel statement across a pause, a place correction, your self-introduction,
and ordinary follow-up conversation. Rex should start a provisional association
rather than confuse an introduction with a rename. Introduce your dad, explain Rex
to him, then address Rex directly again. The conversation model now uses low
reasoning within its existing primary call; the endpoint and Qwen model are unchanged.
The hosted checks passed the reproduced exchanges, but ambiguous homophones and
standalone search fragments still sometimes get unwanted replies.


## Session context and grounded character

A run now retains up to 32 complete exchanges within a 24,000-character serialized
history budget instead of 16 individual message items. Metadata no longer displaces
conversation after roughly five replies. Old exchanges leave as units so a user
utterance is not separated from its speaker evidence or a tool result from its call.
The current exchange is never silently clipped; if it alone exceeds the budget,
the program reports `ConversationTurnExceedsHistoryBudget`. This budget covers
history, not the separately bounded state snapshot or system instructions.

There is no session-summary model or extra classifier. Context is the original
conversation, including only Rex's known delivered replies. It can carry expressed
relationships and style corrections without saving them as personal biography or
confirming a voice identity. Older information still falls out at the limits.
Chat history starts fresh on restart. Names, accepted facts, completed voice
profiles, and retained diagnostic transcripts keep their existing persistence.
Forgetting a person clears active conversational history before the result follow-up.

For a live check, mention a companion and a wording preference early, discuss other
topics, and ask about both later. Criticize an actual mistake and also challenge a
claim that was not a factual error: Rex should correct the former and respond to the
latter briefly without reflexive agreement or an extended defense of his personality.
Listening timing remains unchanged. Hosted tone tests are evidence, not guarantees.

### Conversational enrollment evidence

Clean unnamed groups now collect up to six original clips each before an
introduction. At least three qualifying clips and eight voiced seconds are still
required; each clip needs two voiced seconds, four transcript words, trusted ASR,
and compatible acoustic evidence. Samples must agree pairwise, and analyzed
windows must agree on the selected group. Before saving, earlier samples are
checked against competing groups discovered later. Similarity thresholds are
heuristics, not certainty or a calibrated probability.

A self-introduction attaches a name to the existing acoustic group and can finish
enrollment immediately. Mentioning someone else does not attach their name to
the current voice. Original source attribution stays intact; this does not rewrite
all earlier transcripts as verified speech. Correcting a display name preserves
the same person's acoustic evidence.

The additive `voice_candidates` SQLite table holds unverified original PCM,
transcripts, embeddings, source turns, and model namespace separately from accepted
profiles. At most 12 groups persist, with six clips each; candidates expire after
24 hours without added evidence, checked on startup/input. Expiration is performed
when the program next runs if it was closed. Restart restores hypotheses, not
verified identities, and fresh speech must match acoustically. Accepted profiles
remain durable. Existing runs predating this change have no candidate archive to
recover automatically.

Learning runs on input before response generation. Silent, failed, yielded, and
stopped replies do not reset the evidence. A deliberate association correction,
forget, or voice reset removes the relevant pending evidence. No extra model call,
new provider, microphone operation, or camera operation was added.
