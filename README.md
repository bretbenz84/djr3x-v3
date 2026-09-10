# DJ-R3X v3

A smaller conversation core for Rex, built around reliable group conversation,
explicit uncertainty, and memories that retain who said what about whom.

**Status: slice 1 (audible-loop implementation) landed; live acceptance pending.**
The program connects a selected microphone, Silero endpointing, batch Qwen3-ASR,
one OpenAI reply generation, and streamed ElevenLabs speech through one output owner.
It has cancellation, bounded input queues, and delivery diagnostics. Speaker identity,
camera, and persistent memory are not implemented. V2 remains separate and unchanged.

## Run the voice core

Use Python 3.11 and a dedicated v3 environment, from this checkout:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip install -r requirements-live.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m r3x doctor --profile dev-mac
.venv/bin/python -m r3x live --profile dev-mac
.venv/bin/python tools/run_checks.py
```

The development machine's environment was created using
`/Users/bbenziger/.pyenv/versions/3.11.9/bin/python`. It is already installed here.
`requirements-live.txt` locks the tested macOS/Apple Silicon dependencies; the
short `requirements-live.in` records the selected adapters. Core imports and synthetic
checks still need only the standard library. Editable checkout installation is required.

`doctor` inspects declared live capabilities, dependency versions, local model files,
and credential presence without starting providers or creating scratch state. Missing
prerequisites produce exit code 1. Device enumeration and hosted availability are not
part of this read-only check; `conversation_ready: false` is expected before startup.

`live` loads only the selected ASR/VAD models and opens the configured microphone and
speech output. Stop v2 first so they do not compete for audio devices. Grant macOS
microphone permission to the terminal when prompted. Speak naturally after the
`listening` event. **Enter or `stop` + Enter cancels; `quit` or Ctrl-C exits.**

The default speaker setup rejects captures overlapping Rex's output, including a
300 ms tail, because there is no Mac acoustic echo canceller yet. This also rejects
human speech during playback. Terminal stop remains available then. Spoken `stop`
works on trusted, non-echo captures; using genuinely isolated headphones allows
`echo_isolated = true` for capture during playback. This setting is a user assertion,
not measured echo isolation. Speech during model generation is queued in capture order.

Session JSONL traces go under ignored `local/sessions/`. They retain original text,
ASR quality, model calls, completed speech phrases, failures, and available timing.
Partial phrase text is unknown, not inferred from a fraction of audio frames. Raw
microphone audio is not saved in this slice. A capture/replay dataset comes in slice 2.

The default configuration is `local/settings.toml`; `--config path/to/file.toml`
overrides it. See [settings.example.toml](settings.example.toml) for the strict schema,
consumers, and calibration defaults. Names take precedence over device indices and
must match one microphone; missing/ambiguous names fail instead of silently selecting
another device. Unspecified output uses the system playback device.

The authorized setup import copied the dev-Mac microphone selection, OpenAI and
ElevenLabs credentials, Rex voice ID, and independent Qwen model files into ignored
`local/`. `local/v2-setup-import.json` records model digests and the camera selection
reserved for the next slice. The runtime never reads v2. Secrets are in
`local/secrets.toml`; `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, and
`ELEVENLABS_VOICE_ID` environment variables override them. Values are not logged.
For another checkout, the explicit one-time helper is `tools/import_v2_setup.py`;
it refuses to replace an existing setup and never executes v2 configuration code.

Additional implemented checks:

```sh
.venv/bin/python -m r3x boot
.venv/bin/python -m r3x scenario tests/fixtures/audible_loop.json
.venv/bin/python -m r3x report local/sessions/SESSION_ID.jsonl
# Explicit paid-provider check on synthetic saved audio, with no live devices:
.venv/bin/python tools/check_providers.py
```

`boot` remains a provider-free lifecycle smoke check. `scenario` replaces providers
but uses the same `Session.process_turn` as live; scripted answers cannot validate
language understanding. `report` summarizes observed timings and includes failures.
See [slice-1 evidence](docs/INITIAL_TEST_PROGRAM.md#slice-1-implementation-evidence)
for measured provider checks and the remaining live microphone/speaker gate.

## Start here

| Document | What it answers |
| --- | --- |
| [DESIGN.md](DESIGN.md) | Who makes each decision, how turns flow, how identity and memory work, and which choices remain proposals. |
| [Initial test program](docs/INITIAL_TEST_PROGRAM.md) | What to build first, how to exercise it with Bret and Jeff, and what evidence is needed before expansion. |
| [V2_REFERENCE.md](V2_REFERENCE.md) | Where to recover working integrations, field failures, model constraints, and useful tests without importing the old architecture. |
| [AGENTS.md](AGENTS.md) | Development rules for people and coding agents working in this repository. |

## Why this project exists

V2 accumulated several generations of models, conversation handlers, configuration,
and fallbacks. It contains valuable integrations and useful robot behavior, but
some handlers interpret speech and write memory before the conversational model
can judge its meaning. Weak speaker attribution then compounds those mistakes.

V3 will give one conversational model authority over language interpretation, one
speaker tracker authority over acoustic evidence, one state owner authority over
session changes, and one output owner authority over speech. Code will validate
and execute requests, preserve uncertainty, and enforce physical limits.

This is a replacement core, not a promise to reproduce every v2 feature before
the first usable release. Progress is measured in actual conversations and sound
memory writes, not a feature count or a smaller file count alone.

## First usable program

- Real microphone capture, batch transcription, and Rex's actual spoken replies.
- One speaker tracker covering both named and unnamed voices; one-to-three-person
  sessions, with two-person dev-Mac testing first.
- Basic camera face presence and stable tracks for group context; no mouth-motion
  input to identity, enrollment, or learning permission.
- Natural introductions and corrections interpreted by the conversational model.
- A small, separate SQLite store for participants, source turns, claims, and
  accepted voice recordings; recall and correction must work after a restart.
- A minimal diagnostic view or terminal trace explaining speaker evidence, model
  requests, writes, actual playback, and latency.

The first end-to-end smoke test can omit camera integration, but it is not group
acceptance. Group acceptance uses the real microphone, two people, and camera
presence. Directional audio is optional; the dev Mac must work without it.

## Deferred capabilities

Wheel motion, servo expression, LEDs, games, autonomous greetings, visual riffs,
impersonations, rich biographies, relationship scoring, semantic memory retrieval,
and a polished GUI follow the core's acceptance gates. Offline mode and additional
providers are later explicit profiles, not a default collection of loaded models.
Preserve their v2 reference material so adding them later does not repeat research.

On the physical robot, the enrollment microphone hold is required before voice
collection is enabled there. Deferring decorative servo behavior does not waive
that requirement. See [microphone geometry](DESIGN.md#microphone-geometry).

## Working implementation defaults

These are recommendations for the first build, not settled provider requirements:

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
validation audio, traces, and future stores beneath it. It is gitignored; keep private
artifacts there and add ignore rules before using other private paths. The slice-1
setup import copied only the authorized configuration/credentials and ASR weights;
no personal memory, recordings, or voiceprints were imported.

Use independent copies of selected voice profiles and recordings when importing
is implemented. Do not point a v3 database connection at the live v2 database.
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
