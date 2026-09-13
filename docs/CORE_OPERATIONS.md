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
   turns, at least one voiced second each and eight total, four transcript words
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
`unreviewed.jsonl` inventory for endpointed utterances. It also retains the first
30 PCM seconds of fresh input separately as `input-diagnostic.wav` and
`input-diagnostic.json`, even if the endpoint detects nothing. This short diagnostic
is not in the utterance inventory and has no speaker labels. Longer capture-recall
studies still need separately saved and labeled continuous recordings.

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
required; each clip needs one voiced second, four transcript words, trusted ASR,
and compatible acoustic evidence. Samples must agree with the pooled group and retain a pairwise conflict floor; analyzed
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

## Proactive face greetings

This section describes implemented behavior. The September 12
[proactive conversation design](PROACTIVE_CONVERSATION.md) makes broader initiative
part of core development, with delight and sensitive mood improvement as goals.
The first shared arrival/lull slice is now implemented; see
[lull operation](#conversational-lulls-and-initiative-control). Broader semantic
feedback, per-person state and memory opportunities remain planned.

The normal `live --camera` runner now allows a stable face arrival to prompt the
existing conversation model. It can greet a recognized person using their name
and accepted memory, invite an unfamiliar person's name, or remain silent based
on recent conversation. Greeting wording comes from the LLM, not Python templates.
Recognition alone never identifies the acoustic speaker or saves a new identity.

Expect an initial opportunity after at least eight seconds of startup and three
seconds of stable face observations, with eight seconds free of detected speech
by default (the configured lull interval). After a conversation, allow that same
interval after completed work. Queued audio/transcripts, ASR
work, a pending name question, and an explicit stop prevent new greetings. Silence
choices also impose a two-minute pause. Greetings are limited to one attempt per
visit, with a 45-second global gap and five-minute per-person gap. A return needs
at least 30 seconds of absence. Anonymous invitations are limited to one per
occupied scene until the target is named or the scene empties for 30 seconds.

A face question becomes pending only after its audio finishes playing. A companion
can answer for the targeted person through the existing third-party introduction
path. Text-only output does not establish a delivered spoken question. If a target
leaves or changes identity while a greeting is being generated, it is cancelled.
Detected non-echo speech also cancels it; the existing playback echo window still
limits detection of human overlap during Rex's speech.

For a live check, use `.venv/bin/python -m r3x live --camera --record-evaluation`,
remain visible and quiet briefly, then try an unfamiliar companion and answer
for them. Verify no repeated greeting while staying in frame, no interruption
while speaking, and correct face targeting. Terminal traces mark arrival
opportunities separately from heard speech. Frozen evaluation disables proactive
speech. Camera/audio live validation remains pending; 151 blocked-I/O checks and
three actual hosted-model text-only cases passed on September 11, 2026.

## Conversational lulls and initiative control

September 12 first slice: `live --camera` now offers the same conversational model
a lull opportunity when a stable group is visible. Default `proactive_lull_seconds`
is **8.0**, adjustable from 3–120 seconds in local settings. It is measured since
both recent microphone speech and the last completed interaction. Model/TTS latency
comes afterward; this is not eight-second first-audio acceptance. Camera-disabled
runs retain ordinary replies but have no new automatic lull trigger.

The host normally runs Rex for social demonstrations. The prompt favors a brief
welcome contribution while preserving thinking pauses, questions between humans,
grief and explicit boundaries. Tiredness or a short answer does not mechanically
set an energy mode. History retains each source's speaker uncertainty; the lull
does not invent a new speaker or turn someone's face into acoustic identity.

Initial pacing is one lull attempt per processed human source turn, at least eight
seconds between proactive attempts and at most six attempts in a rolling five-minute
window across both arrivals and lulls. Human turns do not reset the rolling cap.
Arrivals retain their additional visit/cooldown rules. A model silence decision or
terminal stop retains the two-minute hold. Attempts cancelled before generation
also consume pacing, preventing a failed freshness check from spinning every tick.
This policy is provisional social calibration, not a verified ideal conversation rate.

An explicit "stop starting conversations until I ask" can call `set_initiative`
with `mode=quiet`, silently. It creates a session-wide quiet boundary that survives
ordinary replies and history eviction. Direct questions still receive answers.
An explicit invitation to initiate again uses `mode=available`; its acknowledgment
follows the executed result. Both modes require a trusted current source, never a
camera event. They reset on a new run. Narrow topic/per-person boundaries remain
in original history; richer structured feedback is the next planned stage.

New speech, queued captures/transcripts, ASR work, stale microphone frames, target
changes, and camera audience changes cancel/block opportunities. Frozen evaluation
does not enable them. Lulls expose only silence alongside ordinary prose; they
cannot enroll identity, save a personal fact or change the quiet policy. Arrival
questions retain the existing delivery-based pending-question path. Private claim
retrieval for named arrivals is withheld when multiple faces are visible; original
history still requires the model to exercise audience discretion.

Diagnostics use `proactive_opportunity`, `proactive_turn`, `proactive_outcome` and
`proactive_opportunity_done`. The report groups outcomes by kind/status/medium and
counts model requests by purpose. An audio receipt with complete phrases is distinct
from text-only, cancelled, failed or suppressed output. An invitation's response is
explicitly unassessed, not automatically "ignored" or "answered". Outcomes keep at
most 16 entries; forgetting clears retained source/outcome links without revoking
quiet or anonymous pacing. No new provider, model, dependency or background worker.

```sh
.venv/bin/python -m r3x scenario tests/fixtures/proactive_core.json
# Explicit hosted synthetic checks, with silent text output and no personal DB:
.venv/bin/python tools/check_proactive_conversation.py
.venv/bin/python tools/check_proactive_conversation.py --variants
```

Try a receptive quiet visitor, a lull after a completed topic, a thinking pause,
and a question directed to a companion. Then request quiet, ask Rex a direct
question, and invite him to initiate again. Review whether the contributions are
welcome and amusing, not merely whether he spoke. Live devices and combined-load
M2 acceptance remain open; the playback echo window still limits human barge-in.
See [implementation evidence](PROACTIVE_IMPLEMENTATION.md#evidence) for measured
synthetic/hosted results and retained semantic misses.

### Voice-window variation and conversational accumulation

CAM++ now compares each window against the pooled *other* windows, rather than
letting the single worst pair reject a whole sentence. No window is removed.
Two coherent but acoustically separated temporal groups still veto learning, as
do a strongly contradictory window and windows favoring a competing speaker.
This is a heuristic, not proof that no second person spoke.

Qualifying clips can contain one second of voiced speech; enrollment still needs
three distinct compatible clips, eight voiced seconds total and four words per
clip. An incoming clip must match the original anchor (.50), the existing sample
centroid (.55), and every retained sample at least .45. Track assignment still
requires .55 for the whole utterance; each window must choose the same group with
the existing rival margin and a .35 contradiction floor. Named-profile matching
retains its stricter window checks. The encoder, embeddings namespace and stored
original audio format are unchanged. Logs include both the new consistency score
and the old minimum-pair diagnostic, plus reasons for deferred learning.

September 11 saved-audio validation used the real CAM++ model, recorded transcripts
and a scripted introduction through production Session with text output and a
temporary database. Bret's five-turn run enrolled from three clips totaling 8.384
voiced seconds and recognized the final utterance; the short question abstained.
No live database was modified. A separate 21-clip, user-labeled car cohort with
leave-one-clip-out Bret/Jeff reference galleries produced zero named decisions
before and after: zero wrong names, but **100% abstention**, so it does not establish
useful car identification accuracy. Live multi-person, overlap and M2 validation
remain open. Reports and replay scripts are under `local/validation/`.

### Source-linked conversational feedback

The second proactive slice retains explicit topic closure/avoidance and invitation
responses for the current run. A note belongs to the observed speaker's supported
person ID or unresolved acoustic track; a mentioned name cannot take ownership.
Another participant cannot remove that person's topic boundary. An explicit
reopening uses the referenced note, and forgetting clears social source context.
Identity-association cancellation removes affected notes; restart starts fresh.

Human-floor silence decisions retain their source for up to two minutes. A direct
question to Rex still receives a reply, but does not itself release that earlier
floor. A semantic answer/completion can release it through `conversation_feedback`;
otherwise it expires. Active holds defer arrivals/lulls without a model call.
Expiry is not evidence of an answer. A source too uncertain for a note can still
receive a silent response and the existing temporary hold.

Feedback is optional during normal conversation and can include the brief reply
for the existing output owner in the same generation. It never saves biography,
identifies a speaker, or automatically settles a pending identity question.
A maximum of 32 notes is retained. Explicit avoid-topic boundaries are preserved
when routine feedback is evicted; at full boundary capacity a new note is rejected.
These bounded notes and delivery excerpts help the model avoid repetition, but
semantic selection and social quality remain imperfect. See
[implementation evidence](PROACTIVE_IMPLEMENTATION.md).

Explicit hosted synthetic probes, when desired (API usage, no devices):

```sh
.venv/bin/python tools/check_conversation_feedback.py
.venv/bin/python tools/check_conversation_feedback.py --variants
```

The probes use in-memory stores, synthetic acoustic tracks and one simulated audible
invitation; no real audio is played. Read the source, reply and feedback together.
A passing state check alone does not establish tact, humor or correct live attribution.

### Remembered plans, birthdays and callbacks

A supported speaker can naturally give Rex a meaningful plan, birthday, milestone
or callback to remember. `remember_followup` saves the accepted self-report and
its date/sharing metadata together. Sharing is private to the subject unless they
explicitly permit group use. An unknown visible bystander withholds private cues.

On a later arrival or lull, eligible follow-ups are options for the same conversation
model, not guaranteed reminders. Plans are considered within seven days either side
of their date; celebrations on their saved date; birthdays on their month/day each
year; callbacks can be considered daily. Rex must not assume a past plan happened.
Completed audio prevents repetition for that occurrence/day across restart.

Generic old claims are not automatically scheduled. Correcting/retracting the claim
retires the old cue; a fresh follow-up save is needed to establish a replacement's
date/sharing scope. Forgetting also removes its delivery receipts. No background
calendar task, news provider or new model runs for this feature.

Explicit hosted synthetic validation (API calls, no real devices/personal database):

```sh
.venv/bin/python tools/check_memory_opportunities.py
```

Read the generated wording as well as the storage/eligibility score. A passing
candidate check does not establish an appropriate greeting or truthful reminder
promise. See the [stage evidence](PROACTIVE_IMPLEMENTATION.md).

### Public information and scene-aware initiative

With `live --camera`, the completed proactive software also offers BBC top headlines,
Sacramento weather and small camera snapshots to the same conversation model.
Scene snapshots are sent to the hosted model only during an opportunity. Set
`scene_observations_enabled = false` to keep camera pixels out of hosted requests.
Set `news_feed_urls = []` or `weather_enabled = false` to disable those sources.
No new provider starts in doctor or frozen replay, and no extra resident model loads.

New cues can open an opportunity without another spoken turn, while existing quiet,
floor, audience and pacing gates remain in control. These are conversational
reactions; there is no motor/servo action. See [observation sources](OBSERVATION_SOURCES.md)
for lifecycle, freshness, retention, source defaults and explicit validation commands.
The user deferred live acceptance until this software work was complete.
### Incomplete voice learning

The terminal now shows pending voice learning with qualifying turns and usable
speech seconds. Three turns alone do not complete enrollment: eight usable seconds
and the final acoustic consistency checks are also required. Only `Voice samples
saved` confirms completion. Saved names and face references are separate from a
completed voice profile. Unfinished voice candidates resume for 24 hours since
their last update; they are not verified identity across launches.

For recognition or speaker-handoff investigations, launch with
`.venv/bin/python -m r3x live --camera --record-evaluation`. This retains evaluation
audio locally, including captures that were not accepted for enrollment. Without
it, a transcript of two people speaking cannot establish their acoustic boundary.

Proactive turns now print their invalidation reason when a runtime guard stops
work. Unheard arrival greetings can be reconsidered after the normal cooldown.
Name invitations are also available during lulls, chosen by the same conversation
model. A pending question is recorded only when it was actually spoken.

When diagnosing missing transcription, the terminal prints a microphone summary
about every five seconds. `digital silence` means the processed samples were all
zero; a numerical peak with zero detected-speech frames means audio arrived but
the speech detector did not accept it. Neither proves which person or sound was
captured. The JSON `audio_input_health` event also records RMS, echo frames and
whether an utterance is pending. With `--record-evaluation`, Rex also saves the
first 30 PCM seconds of fresh raw microphone input, independently of speech
detection, as `input-diagnostic.wav` with a timestamp/echo sidecar. Quitting earlier
saves the shorter sample. This diagnostic is separate from endpointed recordings
and is never automatically used for enrollment or labeled evaluation. Recording
failure is reported without stopping input processing.

### Intermittent OpenAI connectivity

Temporary OpenAI connection/read/write errors, timeouts, HTTP 408/429 and selected
server errors now retry before any reply text or tool is released. Retry delays
increase through 2, 4, 8, 16 and 30 seconds, capped at 30. The actual request tests
service availability; no separate ping service is required. The terminal reports
waiting and recovery. Explicit stop/shutdown cancels retries. Authentication and
other permanent errors fail normally, and partially released replies are not
regenerated automatically.

Transcribed turns wait instead of being discarded when the conversation queue is
full. New speech cancels the older pending answer; its original words remain in
session history and the newest turn can respond with that context. Existing bounded
history and raw-capture queue limits still apply. Retry work is session-only and
is not resumed automatically after quitting/restarting; this is not a durable
outbox. Local capture/ASR remain independent of OpenAI. Network retry events are
counted separately as model requests, without claiming another primary decision.
