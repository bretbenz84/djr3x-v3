# DJ-R3X v3

A smaller conversation core for Rex, built around reliable group conversation,
explicit uncertainty, and memories that retain who said what about whom.

**Status: design and implementation brief only.** There is no runnable v3 program
yet. Proposed interfaces, commands, models, and acceptance targets below are not
implemented or validated. This repository begins with documentation, not a copy of
v2. The working v2 robot remains available separately.

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

The proposed runtime directory is `local/`, with scratch databases, voice-profile
copies, recordings, evaluation manifests, logs, and caches beneath it. This directory
is gitignored; keep private artifacts there and add ignore rules before using other
private paths. No personal data or credentials were copied as part of this task.

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
