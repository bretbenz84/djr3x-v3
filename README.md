# DJ-R3X v3

The conversation core for Rex, the DJ-R3X droid. One conversational model decides what
to say and do. Code validates and executes those decisions, keeps uncertainty honest,
and enforces physical limits.

**Status, September 27, 2026:** the core conversation software and the robot's
hardware integrations are implemented and in daily live use on the robot: voice,
camera, servos, LEDs, wheels and radar. Live acceptance is still open for group
identity (two people alternating), social behaviour and the newest drive changes.
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) is the checklist of
what is finished, what is only simulated and what remains.

V2 (`/Users/bbenziger/djr3x-v2`) is a separate, unchanged reference. V3 never imports
it or reads its data.

## What Rex does today

- **Conversation.** Listens through the ReSpeaker array, transcribes locally (Qwen3-ASR),
  replies with one `gpt-5.4-mini` call per turn and speaks through ElevenLabs. Speech is
  leveled to V2's −20 LUFS target.
  - Audible speech is never cut off except by an explicit stop phrase ("stop",
    "shut up", "be quiet", "whoa", repeated or wrapped as in "Oh, stop!").
  - Transcripts the recognizer marks unreliable are kept but never answered. These are
    usually his own motors heard as "Oh.".
- **Who is speaking.** CAM++ voice tracks, natural introductions and enrollment, and
  face recognition (SCRFD-10G + EdgeFace-S). Speaker, visible face, microphone direction
  and addressee stay separate.
  - Motion lowers confidence rather than discarding evidence. Motion-flagged speech is
    matched with stricter thresholds and never enrolls.
- **Memory.** Source-linked personal facts, corrections and forgetting in a local SQLite
  store. Named pet/object photo memory (RF-DETR Nano detection).
- **Initiative.** Greets arrivals, contributes during lulls, uses news, weather and scene
  observations, and can choose silence. It is one owner with replies, not a separate loop.
- **Seeing.** Deliberate camera looks use a sharp 1280 px frame. The robot camera is a
  wide ~90° lens at 1920×1080.
- **Body (Pololu Maestro, 11 servos).**
  - Startup and park choreography, and talking motion driven by actual playback.
  - The head turns toward whoever he is answering.
  - Idle movement: arm wander, and head looks at the face in front of him or around
    the room.
  - Model-selected poses and a microphone hold for enrollment.
- **Lights.** Head and chest startup/shutdown, speaking lights and model-selected
  animations.
- **Wheels and radar** (V2 ESP32 firmware, unchanged):
  - The model plans turns, moves, facing and approaching people or objects, finding a
    person, and seeking things the detector cannot name ("find the door and go to it").
  - Approaches to a sought thing roll continuously while hosted vision looks run in the
    background. He goes around obstacles and never reverses to make arm room on the way.
  - Safety: throttle-arm park, hero elbow and poker arm raised and head levelled while
    driving, arm-swing and path-clearance checks, and an immediate halt on stop.
  - V2's motion sounds play at 50% while the wheels move.
  - He can answer questions about battery, charging, obstructions, radar bodies, tilt
    and room climate from live readings.
- **Startup.** Servo wake-up with V2's startup sounds, a short "please wait" line, and a
  spoken hardware notice when something failed to connect. He ends with a short ready
  line (a statement, never an invitation).

## Run it

Say "wake up rex", or start Rex by hand (the supervisor and menus step aside
automatically):

```sh
cd /Users/bbenziger/djr3x-v3
.venv/bin/python -m r3x live
```

- Hardware is enabled by `local/settings.toml`: `camera_enabled`, `servo_port`,
  `head_led_port`, `chest_led_port`, `drive_port` and `radar_port` (all set on the
  robot). `--camera` turns the camera on for a single run. Every setting is documented
  in [settings.example.toml](settings.example.toml).
- Say "shut down" to exit; Enter or `stop` + Enter in the terminal cancels the current
  reply; `quit` or Ctrl-C exits.
- The terminal shows the conversation. Detailed JSON diagnostics go to the session file
  printed at startup, under `local/sessions/`.
- `--record-evaluation` also keeps endpointed WAVs for human labeling.
- `python -m r3x doctor` checks dependencies, model files and credentials without
  opening devices.

**Always-on services.** The robot's LaunchAgents run V3's copies of V2's wake supervisor
and servo/LED/battery menu-bar apps (`operator_tools/`). Say **"wake up rex"** to start
Rex; the supervisor also keeps the V3 checkout updated from GitHub (fast-forward only,
never over uncommitted changes). See [operator services](docs/OPERATOR_CUTOVER.md).

**The wheels move only when asked or when the model chooses a small self-initiated move
in conversation.** Leave `drive_port` empty for conversation-only tests. Floor tests are
described in [DRIVE_BASE_INTEGRATION.md](docs/DRIVE_BASE_INTEGRATION.md).

## Check

```sh
.venv/bin/python tools/run_checks.py                      # 747 blocked-I/O contract checks
.venv/bin/python -m r3x scenario tests/fixtures/core_memory.json
.venv/bin/python -m r3x replay local/dataset/manifest.json
.venv/bin/python -m r3x report local/sessions/SESSION_ID.jsonl
```

- `run_checks.py` blocks network, audio, serial and native libraries, and uses fake
  clocks, a simulated ESP32 and synthetic providers.
- `scenario` drives the production session with scripted providers. `replay` runs real
  VAD/ASR/CAM++ on saved audio.
- Hosted-model evaluations (for example `tools/check_drive_planning.py`) are paid, paced
  runs kept separate from the contract suite.
- A clean synthetic suite is not live acceptance. Reports say which evidence was
  synthetic, saved audio, hosted or real hardware.

## Setup (fresh environment)

Python 3.11 (`/Users/bbenziger/.pyenv/versions/3.11.9/bin/python` on this Mac):

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip install -r requirements-live.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python tools/prepare_core_models.py      # pinned public CAM++ assets
.venv/bin/python tools/prepare_face_models.py      # face models from the offline cache
```

Credentials and the Rex voice live in ignored `local/secrets.toml`
(`OPENAI_API_KEY`, `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` environment variables
override them). Startup verifies model digests and never substitutes or downloads
models on its own.

## Data rules

- Everything private is under ignored `local/`: settings, credentials, the store
  (`local/core.sqlite3`), recordings, voiceprints, visual memory, caches and session
  traces. Never commit or publish it.
- Bundled audio (`assets/audio/startup`, `assets/audio/motion`) is V2's sound effects
  converted to WAV. It contains no personal recordings.
- No V2 personal data has been imported. Any import makes independent copies with a
  source digest and explicit ID mapping. Nothing opens a writable connection to V2's
  databases.

## Documents

| Document | What it answers |
| --- | --- |
| [Implementation status](docs/IMPLEMENTATION_STATUS.md) | What is finished, simulated only, or still open. |
| [Drive base](docs/DRIVE_BASE_INTEGRATION.md) | Wheels, radar, safety gates, vision seek and floor-run repairs. |
| [Servo integration](docs/SERVO_INTEGRATION.md) | Choreography, talking and idle motion, poses, microphone hold. |
| [LED integration](docs/LED_INTEGRATION.md) | Head/chest lights and animations. |
| [Operator services](docs/OPERATOR_CUTOVER.md) | Wake supervisor, menu-bar apps, auto-update, rollback. |
| [Live test script](docs/LIVE_TEST_SCRIPT.md) | Lines to say and results to check in hands-on conversation tests. |
| [DESIGN.md](DESIGN.md) | Who decides what, turn flow, identity and memory design. |
| [Proactive conversation](docs/PROACTIVE_CONVERSATION.md) | How Rex initiates and participates. |
| [Core operations](docs/CORE_OPERATIONS.md) | Enrollment, profiles, review, deletion and diagnostics. |
| [Future features](docs/FUTURE_FEATURES.md) | Owner-requested work not yet scoped. |
| [V2_REFERENCE.md](V2_REFERENCE.md) | Where each V2 extraction came from and what it preserved. |
| [AGENTS.md](AGENTS.md) | Rules for people and coding agents working here. |

## Principles

- **One owner per responsibility.**
  - One conversational owner interprets language and chooses replies or tools.
  - One speaker tracker owns acoustic evidence.
  - One state owner validates writes.
  - One output owner schedules speech, lights, servos and wheels, and records what
    actually happened.
- **Evidence, not guesses.** No regex turns speech into names, facts or answers. The
  model cannot promote weak acoustic evidence to identity. Mouth motion is never used
  for attribution.
- **Honest receipts.** Rex never claims a save, a move, an arrival or speech before its
  executor reports success.
- **Small and replaceable.** No model or device loads at import. Every capability
  declares its dependencies and start/cancel/close. A replacement retires its
  predecessor.
- **Cost.** Ordinary replies use one `gpt-5.4-mini` call. Stronger models run only
  narrowly: the `gpt-5.6-terra` route planner runs only when a direct path is blocked,
  within the owner's roughly $45/month budget.
