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
accepted enrollment audio, rather than all endpointed test captures. The copied
camera selection remains disabled in settings; the flag enables it for this run.
macOS permission prompts and actual device formats have not been tested in v3.
The camera requires ffmpeg, a unique AVFoundation video-device name, and the pinned
face classifier. It requests 640×480 UYVY at 30 fps and detects faces at most twice
per second. It reports failure rather than opening an unrelated device.

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
Tracks expire after five minutes; pending questions expire after two. These are
initial heuristics, not calibrated probabilities. Window consistency is **not a
validated overlap detector**. Nearby similar voices and short/mixed speech need
the held-out dataset before any accuracy claim.

Camera tracks give presence, coarse position, and explicit provisional name
associations. There is no face-recognition model. Known-face scenarios can exercise
supplied face hypotheses, but the camera does not identify people by itself.
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
