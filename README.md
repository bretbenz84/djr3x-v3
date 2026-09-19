# DJ-R3X v3

A smaller conversation core for Rex, with one language owner, one acoustic speaker
tracker, one state executor, and one speech output owner.

**Status: the agreed core software is implemented; live identity and social acceptance remain incomplete.**
See the [current implementation checklist](docs/IMPLEMENTATION_STATUS.md) for completed
software, remaining validation and deferred hardware work.
Microphone input, Qwen ASR, CAM++ tracks and natural enrollment, semantic identity
and memory tools, persistent attributed claims, optional persistent face recognition, frozen
saved-audio replay, and profile maintenance are implemented. The full robot roadmap remains outside this build; the explicitly authorized
proactive news/weather and scene-input extensions are implemented. V2 remains separate and unchanged.

The overnight checks used synthetic data and saved audio. No microphone was opened,
no audio was played, and no video was captured. Read the [morning test guide](docs/CORE_OPERATIONS.md)
and [acceptance evidence](docs/INITIAL_TEST_PROGRAM.md#core-implementation-evidence-slices-2-4).

The [attention and coordinated-action slices](docs/ATTENTION_IMPLEMENTATION.md) are also
implemented for explicit simulation harnesses. They add silent attention choices,
grounded synthetic visual targets, post-movement evidence checks, and gestures
coordinated before/after speech through the same
conversation owner. An LLM-selected inspection can also sequence simulated movement,
settling and image capture before a model continuation. Camera-enabled startup now offers [current-view inspection](docs/CURRENT_VIEW_VALIDATION.md)
through the same LLM tool. Physical turning and composite motion remain unavailable. Inspection descriptions can now be saved
as bounded, source-linked visual observations and reused through LLM tools. Fresh
proactive scene images can also be described and retained silently or with a remark
in the primary call, with
actual spoken remarks kept separately. [Hosted proactive trials](docs/PROACTIVE_VISUAL_EVALUATION.md)
now document improved restraint plus remaining repetition issues.
[Related visual corrections](docs/VISUAL_CORRECTION.md) can withdraw multiple
LLM-selected descriptions together. The [core integration pass](docs/CORE_INTEGRATION_PASS.md)
adds detail-preserving revisions, visual novelty context, action continuity across
interruption and combined contract tests. Live descriptive quality remains unvalidated.

Greeting-led [conversational intentions](docs/CONVERSATIONAL_INTENT_PLAN.md) now
retain an optional, revisable direction for the visit through the same model call.
Later replies can follow the visitor's agenda without a separate planner. These are
session context, not personal facts or scheduled speech. Active intentions now
permit spaced follow-up opportunities during continuing silence; the same model
chooses whether and how to speak, within existing quiet/floor and call-budget gates. Live social acceptance remains open.

## Run the core

The dev Mac already has its independent `.venv`, selected devices, credentials,
and model files. From this checkout, when ready to test:

```sh
.venv/bin/python -m r3x live
# Face recognition and optional WAV retention for human labeling.
.venv/bin/python -m r3x live --camera --record-evaluation
```

Stop v2 before starting a live test. Grant microphone/camera permission if macOS
requests it. Speak after `Listening`. **Enter or `stop` + Enter cancels;
`quit` or Ctrl-C exits.** No startup command runs automatically in the background.

The live terminal shows conversation and errors; detailed JSON diagnostics remain
in the session file printed at startup. Generated speech is labeled separately from
actual playback. `doctor` is optional: live startup already checks readiness.

`doctor` checks dependency/model-file/credential presence without starting providers,
opening devices, or creating scratch state. Missing prerequisites return exit code 1.
`conversation_ready: false` is expected before startup. Camera defaults to disabled.

Default echo handling separates captures when the playback gate changes, preserving
human prefixes/suffixes rather than rejecting a whole mixed recording. The gate
starts when speech PCM enters the output queue, not while waiting on the network,
and ends 300 ms after playback. Human speech overlapping that window is still
excluded; this is not echo cancellation. Terminal stop works then. Trusted
spoken stop works outside the echo window. `echo_isolated = true` is available for
an actually isolated headset setup; it does not enable acoustic echo cancellation.
The default pause allowance is 1.2 seconds. Non-echo speech resuming during
recognition/generation cancels an unheard reply; earlier words remain in history
for the next turn. Unfinished thoughts can receive a silent decision from the same
conversation model. This does not detect human interruptions during speaker echo.

Session conversation now retains up to 32 complete exchanges, within a 24,000-
character serialized history budget. Speaker evidence, original user words, and
heard replies stay together; older exchanges leave as units. This is session-only
context, not a summary or permanent biography. A new run starts with fresh chat
history; saved names, facts, and completed voice profiles remain in the local store.

The conversation model remains `gpt-5.4-mini`, now using low reasoning within the
same primary call. Yielded user words retain an explicit delivery/context note;
continuations can complete the previous thought. Identity actions are offered only
for targets authorized by the state owner. An existing-name self-introduction
starts a provisional association rather than renaming someone.

The conversation model can choose a silent turn for another device's commands or
background speech. A later direct request re-engages normally. Within-capture speaker
changes are conservative proposals; derived turns retain their source offsets and
cannot enroll voices or write personal facts. Tentative track continuity is distinct
from named identity and never refreshes voice references. The labeled car-session
checks improved continuity but still abstain on short/difficult clips; see acceptance
evidence for the coverage tradeoff and unresolved overlap/boundary limitations.

## Local setup and data

`local/settings.toml` contains the selected Mac microphone and camera names.
[settings.example.toml](settings.example.toml) documents every setting and default.
Names must resolve uniquely; the camera uses a fresh AVFoundation enumeration at
explicit startup. Capture defaults to 1280×720 at 30 fps; the robot uses its verified
[1920×1080 mode](docs/CORE_OPERATIONS.md#robot-camera-startup-september-17).
A separate worker analyzes the latest frame
at most twice per second using pinned SCRFD-10G and EdgeFace-S on one CPU thread.
The [September 17 camera fix](docs/FACE_DISTANCE_FIX.md) prevents FFmpeg from
repeating old frames and raises detector detail to a 960-pixel long edge with
rectangular padding (960×544 for the robot's full-resolution 1080p capture).
Clear compatible views can become persistent face references through introductions
or a supported voice with a sole prominent face. Third-party introductions keep the
introducer's voice separate. Face identity never overrides acoustic identity.
See the [face implementation and limits](docs/FACE_ATTRIBUTION_PLAN.md#implemented-dev-mac-face-path).
Live device compatibility, robot-camera calibration and combined-load M2 performance
remain to be checked. No mouth-motion processing is used.

Credentials and the selected Rex voice are in ignored `local/secrets.toml`.
`OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, and `ELEVENLABS_VOICE_ID` override those
values. Runtime does not read v2. The authorized one-time setup helper,
`tools/import_v2_setup.py`, copies only selected configuration, credentials, and
public ASR files; it refuses to overwrite an existing setup. No v2 personal data
has been imported into the live core.

The active store is `local/core.sqlite3`. Accepted original voice audio and its
embeddings commit together. `--record-evaluation` additionally saves endpointed
WAVs under `local/recordings/`; they are explicitly unreviewed until a human labels
them. The same flag saves a separate, bounded 30-second raw-input diagnostic before
speech detection, allowing investigation of missed speech. Session traces under
`local/sessions/` contain private transcripts and results.
Never commit or publish `local/`. See the operations guide for independent imports,
review, re-embedding, deletion, and the scope of diagnostic-file retention.

For a fresh environment, use Python 3.11; this Mac uses
`/Users/bbenziger/.pyenv/versions/3.11.9/bin/python`:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip install -r requirements-live.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
# After supplying local credentials/devices and the Qwen ASR model:
.venv/bin/python tools/prepare_core_models.py
```

`prepare_core_models.py` verifies/downloads the pinned public CAM++ assets; it does not run inference or open devices. The resolved
Apple Silicon environment is locked in `requirements-live.txt`; selected direct
dependencies are in `requirements-live.in`. Core imports and ordinary checks use
only the standard library. An editable checkout is required.

Face assets on this Mac are already prepared. A fresh setup reuses the offline
comparison cache with `.venv/bin/python tools/prepare_face_models.py`; see the face
plan for cache preparation. Startup verifies digests and never silently substitutes
a detector or downloads models. `samples/` stays ignored and is not automatically
enrolled into the live database.

## Check and inspect

```sh
.venv/bin/python tools/run_checks.py
.venv/bin/python -m r3x scenario tests/fixtures/core_memory.json
.venv/bin/python -m r3x replay local/dataset/manifest.json
.venv/bin/python -m r3x report local/sessions/SESSION_ID.jsonl
.venv/bin/python -m r3x profiles --help
```

`scenario` uses scripted providers with the production Session/state/store; it
cannot prove language or acoustic accuracy. `replay` uses real VAD, ASR and CAM++
on saved files, never live audio/video; `--brain` explicitly adds hosted inference
with silent text output. Evaluation runs freeze identity profiles and disallow
training or memory mutations. `--oracle-segments` isolates encoder behavior using
human timing labels and cannot pass a production accuracy gate.

`boot` remains a provider-free lifecycle check. Reports retain failures, missing
measurements, model calls, speaker-analysis cost, playback receipts, and available
latency/resource observations. A clean synthetic suite is not Bret/Jeff acceptance.

## Start here

| Document | What it answers |
| --- | --- |
| [Current implementation checklist](docs/IMPLEMENTATION_STATUS.md) | Current software completion, validation gaps and future coding scope. |
| [DESIGN.md](DESIGN.md) | Who makes each decision, how turns flow, how identity and memory work, and which choices remain proposals. |
| [Proactive conversation](docs/PROACTIVE_CONVERSATION.md) | How Rex should initiate, delight and participate sensitively as part of the core; software implemented; social acceptance remains open. |
| [Proactive implementation plan](docs/PROACTIVE_IMPLEMENTATION.md) | Implemented proactive stages, dated evidence and remaining social acceptance. |
| [Initial test program](docs/INITIAL_TEST_PROGRAM.md) | What to build first, how to exercise it with Bret and Jeff, and what evidence is needed before expansion. |
| [V2_REFERENCE.md](V2_REFERENCE.md) | Where to recover working integrations, field failures, model constraints, and useful tests without importing the old architecture. |
| [AGENTS.md](AGENTS.md) | Development rules for people and coding agents working in this repository. |

## Why this project exists

V2 accumulated several generations of models, conversation handlers, configuration,
and fallbacks. It contains valuable integrations and useful robot behavior, but
some handlers interpret speech and write memory before the conversational model
can judge its meaning. Weak speaker attribution then compounds those mistakes.

V3 gives one conversational model authority over language interpretation, one
speaker tracker authority over acoustic evidence, one state owner authority over
session changes, and one output owner authority over speech. Code validates
and executes requests, preserve uncertainty, and enforce physical limits.

This is a replacement core, not a promise to reproduce every v2 feature before
the first usable release. Progress is measured in actual conversations and sound
memory writes, not a feature count or a smaller file count alone.

Rex should delight, make people laugh and help improve their mood. Emotional
awareness guides sensitivity rather than mechanically matching low energy; grief,
distress and explicit boundaries call for a different response. Proactive and
reactive conversation share one owner and are developed and evaluated together.
The [proactive design](docs/PROACTIVE_CONVERSATION.md) extends the implemented
face-arrival path. The first slice adds visible-group lulls after about eight
seconds, explicit session quiet/resume and attempt/delivery history. The second slice
adds source-linked invitation feedback, participant topic boundaries and human-floor
holds with semantic release, without a required extra generation. Dated, source-linked memory opportunities are also implemented, with private-by-default
audiences and delivery receipts. [News/weather and scene inputs](docs/OBSERVATION_SOURCES.md)
now join the same proactive path. Hosted semantic reliability and live social acceptance remain open.

## First usable program

- Real microphone capture, batch transcription, and Rex's actual spoken replies.
- One speaker tracker covering both named and unnamed voices; one-to-three-person
  sessions, with two-person dev-Mac testing first.
- Camera face recognition, stable visual tracks and persistent name associations;
  no mouth-motion input to identity, enrollment, or learning permission.
- Natural introductions and corrections interpreted by the conversational model.
- Context-sensitive initiative and group participation, designed alongside replies;
  face arrivals and visible-group lulls are implemented, broader acceptance is pending.
- A small, separate SQLite store for participants, source turns, claims, and
  accepted voice recordings; recall and correction must work after a restart.
- A minimal diagnostic view or terminal trace explaining speaker evidence, model
  requests, writes, actual playback, and latency.

The first end-to-end smoke test can omit camera integration, but it is not group
acceptance. Group acceptance uses the real microphone, two people, and camera
presence. Directional audio is optional; the dev Mac must work without it.
The [ReSpeaker integration](docs/RESPEAKER_INTEGRATION.md) supplies explicit
channel-0 capture and timestamped USB direction evidence to conversation context.
Robot settings use V2's validated processed channel and 1.5× gain. Combined live
direction and group acceptance remain pending.

## Deferred capabilities

Physical wheel/servo/LED control, games, impersonations, rich biographies,
relationship scoring, semantic memory retrieval,
and a polished GUI follow the core's acceptance gates. Offline mode and additional
providers are later explicit profiles, not a default collection of loaded models.
Preserve their v2 reference material so adding them later does not repeat research.

On the physical robot, the enrollment microphone hold is required before voice
collection is enabled there. Deferring decorative servo behavior does not waive
that requirement. See [microphone geometry](DESIGN.md#microphone-geometry).

## Working implementation defaults

These are the current core defaults; future provider changes need measured justification:

- Python, initially the proven Python 3.11 ecosystem, with a new project environment.
- One process, explicit lifecycle, bounded queues, typed records, and no import-time
  model loading or device access.
- Reuse Qwen3-ASR, CAM++, the existing hosted reply model, and ElevenLabs as initial
  candidates through small adapters. Benchmark before replacing a model.
- One primary model generation for ordinary conversation. Tool-result follow-ups
  are explicit exceptional work, never a hidden universal classifier stage.
- Fresh project configuration containing only settings used by the new program.

See [decision status](DESIGN.md#decision-status) for firm requirements, proposed
defaults, and questions to resolve through measurements.

## Data and reference separation

V2 reference checkout: `/Users/bbenziger/djr3x-v2`.
V3 development checkout: `/Users/bbenziger/Local/djr3x-v3`.

Do not import v2's startup, global configuration, interaction, or consciousness
modules. Reuse verified details through extraction and focused adapter tests.
The inspected reference revision and working-tree status are in V2_REFERENCE.md;
check them again before extracting code.

The runtime directory is `local/`, with private settings, credentials, model files,
validation audio, traces, and stores beneath it. It is gitignored; keep private
artifacts there and add ignore rules before using other private paths. The slice-1
setup import copied only the authorized configuration/credentials and ASR weights;
no personal memory, recordings, or voiceprints were imported.

The explicit profile importer makes independent copies of selected labels, originals,
and references, with a source digest and ID mapping. Do not point a v3 database connection at the live v2 database.
Start personal claims empty or with a small explicitly verified test fixture.

## Development sequence

1. Implement independent boot, configuration, adapters, and delivery diagnostics.
2. Establish a runnable voice loop and the labeled speaker-evaluation harness.
3. Add the unified participant tracker, semantic identity tools, and minimal memory.
4. Evaluate complete conversations, restart/recall, corrections, failures, and load.
5. Restore one additional capability at a time through tools returning results.

The detailed gates are in the [initial test program](docs/INITIAL_TEST_PROGRAM.md).
Do not postpone speaker evaluation until the surrounding product is complete.

## Keeping the design small

Each capability has one owner, one active implementation, explicit dependencies,
and a removal path. A replacement includes retiring its predecessor. Configuration
switches do not justify keeping competing conversational authorities indefinitely.
Use Git history to preserve retired code rather than inactive runtime branches.

Voice enrollment now accumulates clean unnamed speaker groups before introductions.
An introduction can use earlier matching audio, and unfinished groups survive
restart for up to 24 hours. See [enrollment evidence](docs/CORE_OPERATIONS.md#conversational-enrollment-evidence)
for thresholds, retention, correction behavior, and remaining calibration limits.

Face recognition now uses SCRFD-10G and EdgeFace-S when `--camera` is enabled,
with persistent face/name associations and a shared pipeline for future pose/object
consumers. The [face attribution plan](docs/FACE_ATTRIBUTION_PLAN.md) records the
implementation, provisional thresholds, evidence and remaining M2/16 GB acceptance.

With `--camera`, stable arrivals can also prompt an LLM-written greeting or a
targeted name question without anyone speaking first. The existing conversation
owner uses recent history and accepted memory, and can choose silence. Arrival
pacing prevents repeated greetings from face flicker; microphone activity, queued
speech, and pending questions take priority. See
[proactive greetings](docs/CORE_OPERATIONS.md#proactive-face-greetings) for behavior
and the remaining live check.

With `--camera`, a stable visible group can also prompt one brief contribution
after roughly eight seconds of a conversational lull. Rex uses the same model and
history to choose speech or silence. Explicit requests to stop initiating persist
for the run while direct replies remain available; invite him to resume when ready.
See [lull operation](docs/CORE_OPERATIONS.md#conversational-lulls-and-initiative-control)
for pacing, current limits and the repeatable synthetic scenario.

## Named pet/object photo memory (September 17)

The robot now enables [object detection and photographic memory](docs/OBJECT_PHOTO_MEMORY.md)
with `--camera`. A single paced RF-DETR Nano ONNX pass supplies object cues to the
existing conversation owner. Teach a clearly indicated pet/object by name; the
model can inspect it, save a PNG reference, compare later views, correct the name
or forget it. Photos and the readable JSON catalog live under ignored
`local/visual-memory/`, separate from SQLite. Ordinary replies add no mandatory
model call; selected comparisons do. Two-dog spatial-reference reliability and
full conversational latency remain acceptance work.
