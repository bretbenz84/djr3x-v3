# DJ-R3X v3

The conversation core for Rex, the DJ-R3X droid. One conversational owner uses a stronger model for saved activity plans and
a cheaper model for replies and plan-step execution. Code validates and executes those decisions, keeps uncertainty honest,
and enforces physical limits.

**Status, October 1, 2026:** the core conversation software and the robot's
hardware integrations are implemented and in daily live use on the robot: voice,
camera, servos, LEDs, wheels and radar. Live acceptance is still open for group
identity (two people alternating), the new persistent agency plans and the newest drive changes.
[docs/IMPLEMENTATION_STATUS.md](docs/IMPLEMENTATION_STATUS.md) is the checklist of
what is finished, what is only simulated and what remains. The October 14
[improvement plan](docs/IMPROVEMENT_PLAN.md) is implemented in software and awaits its
first robot run.

V2 (`/Users/bbenziger/djr3x-v2`) is a separately maintained reference. V3 never imports
it or reads its data.

## What Rex does today

- **Conversation.** Listens through the ReSpeaker array, transcribes locally (Qwen3-ASR),
  replies with one `gpt-6-luna` call per turn and speaks through ElevenLabs. Speech is
  leveled to V2's −20 LUFS target.
  - Audible speech is never cut off except by an explicit stop phrase ("stop",
    "shut up", "be quiet", "whoa", repeated or wrapped as in "Oh, stop!").
  - "Be quiet", "quiet", "shut up" and "hush" also keep him from starting talk or
    movement on his own until someone invites him to talk again; "stop" pauses that for
    two minutes.
  - Transcripts the recognizer marks unreliable are kept but never answered. These are
    usually his own motors heard as "Oh.", and since October 18 such a sound no longer cuts
    off a line he is preparing; only words do.
- **Who is speaking.** CAM++ voice tracks, natural introductions and enrollment, and
  face recognition (SCRFD-10G + EdgeFace-S). Speaker, visible face, microphone direction
  and addressee stay separate.
  - Motion lowers confidence rather than discarding evidence. Motion-flagged speech is
    matched with stricter thresholds and never enrolls.
- **Memory.** Source-linked personal facts, corrections and forgetting in a local SQLite
  store, cross-session exchange recall and optional source-linked shared memories
  ([details](docs/CONVERSATION_MEMORY.md)). Named pet/object photo memory (RF-DETR Nano detection).
  Since October 25 a fact said while his own motor noise masks the voice is still kept, as a candidate
  fact about the person he is talking with, and he never tells anyone a save failed.
- **Initiative (goals, October 2).** People come first: anyone talking to Rex gets an
  ordinary reply, and someone who arrives is greeted (by name, with "how are you", when
  known). When nothing is happening, Rex pursues a goal of his own: catching up with a
  known person from saved facts, getting to know a stranger, investigating something in
  the room (drive over, reach out, look closely, ask about it) or livening up a dead
  moment. GPT-5.6 Terra plans each goal once, in the background, from the conversation,
  saved facts and one camera frame; GPT-6 Luna performs one beat per lull. Since October 24 he is
  curious on his own turns: he is offered what he does not know yet about the person he is with and
  each saved pet (their work, where they grew up, how Max came home), asks about one when the moment
  fits, saves the answer and does not ask it again for a few days. A pet walking in gets a hello, then
  his question about it; someone far away gets a goal that rolls over to them. His replies to what
  people say end without a tacked-on follow-up question ([details](docs/IMPLEMENTATION_STATUS.md#curious-about-people-and-pets--october-24)). Conversation
  steers a goal instead of discarding it; it ends when its beats are used, its person
  leaves, someone redirects it or a new arrival replaces it. He eases off when nobody
  answers and, with nobody seen or heard for five minutes, starts no new goals. After 45 seconds with
  nobody talking (him included) and someone in view, he asks one "Do you ever think..." question, once
  per quiet stretch (October 23). Talk he leaves to others (to each other, a pet, a device) holds
  his goal only until nobody has spoken for 20 seconds (October 24). Live acceptance is pending
  ([design](docs/AGENCY_IMPLEMENTATION_PLAN.md)).
- **News (October 11).** BBC headlines, Sacramento weather and, once a day, about five
  web-searched stories (at most one about AI or tech) that Rex may bring up in a lull,
  stating when each event happens. Interests people tell him about themselves are saved
  and, while they are here, get their own daily news search. Live use is pending
  ([details](docs/OBSERVATION_SOURCES.md#searched-daily-stories-and-interest-news-october-11-2026)).
- **Voice impressions (October 6).** Ask for an impression of Jimmy Carter, of a known
  person or of yourself, and Rex writes a short parody and performs it in that cloned
  voice through the local Breeze engine, framed by lines in his own voice. Now and then,
  when someone brings up a person whose voice he has (a famous one, someone he knows, or
  the speaker's own), he slips in a short surprise cameo of that person butting into the
  conversation. Since October 23 he also does impressions on his own, as a beat of one of
  his goals ("off to the Reagan library?" gets Reagan's opinion), with the voice rendered
  before he speaks; both share one budget, about one every ten minutes at most. "Do me"
  works once Rex has heard about six seconds of your clean speech; a stranger's voice lasts
  for the session only. Parody is never stored as a fact. Live acceptance pending
  ([details](docs/IMPERSONATION.md)).
- **Rooms (October 11).** With `place_recognition_enabled`, V2's room model (run locally)
  tells Rex which room he is in. In an unfamiliar room he asks what it is and learns it
  when told, remembers which rooms he has moved between, and may set off to explore. V2's
  three taught rooms are imported. Not yet run on the robot ([details](docs/ROOM_RECOGNITION.md)).
- **Sentry mode (October 22).** "Activate sentry mode until 7pm" (or for an hour, silent, with a 60
  second activation): a spoken countdown, then red lights, still arms, a slowly sweeping neck and
  locked wheels, with no paid calls while nothing happens.
  - A stranger's face held a few seconds, or a full sentence from an unknown voice, sets off the
    alarm: he roasts the intruders for about two minutes, records video and sends a phone alert with
    a photo (ntfy).
  - Someone he knows by face or voice stands him down. When the time runs out he shuts down.
  - Not yet run on the robot ([details and phone setup](docs/SENTRY_MODE.md)).
- **Seeing.** Deliberate camera looks use a sharp 1280 px frame. The robot camera is a
  wide ~90° lens at 1920×1080. A look someone asks for ("look left and see if you see it") turns
  the head there first and holds it while he answers, even when they speak (October 23; not yet
  run on the robot).
- **Body (Pololu Maestro, 14 servos).**
  - Startup and park choreography, and talking motion driven by actual playback.
  - The moment he has understood what was said, his head cocks to one side while he
    thinks (October 11; not yet run on the robot).
  - The head turns toward whoever he is answering, and since October 10 can cock to a
    side (two facing servos pitch and tilt it) for curious or playful moments.
  - Idle life: slow, staggered movement of one to three parts at a time whenever he
    is not talking; the head follows the nearest face with glances away, or holds a
    direction the model chose (October 3; not yet run on the robot).
  - Model-selected poses and a microphone hold for enrollment.
- **Lights.** Head and chest startup/shutdown, speaking lights and model-selected
  animations.
- **Wheels and radar** (drive firmware in `firmware/djr3x_motion`, moved from V2 October 23; radar firmware still V2's):
  - The model plans turns, moves, facing and approaching people or objects, finding a
    person, and seeking things the detector cannot name ("find the door and go to it").
  - Approaches to a sought thing roll continuously while hosted vision looks run in the
    background. He goes around obstacles and never reverses to make arm room on the way.
  - Safety: throttle-arm park, hero elbow and poker arm raised and head levelled while
    driving (the arms walk back to their awake poses when the wheels stop), arm-swing and
    path-clearance checks, and an immediate halt on stop (since October 25 also heard in
    fainter sound, and by the owner's `stop.onnx` stop word, while the wheels run).
  - V2's motion sounds play at 50% while the wheels move, and pause while he talks.
  - He talks while he rolls (October 24, not yet run on the robot): the wheels start while his
    opening line plays, and a short line can be timed to the moment they do. Only the visor and
    mouth lights move with it; a stop said over his voice still halts him.
  - He can answer questions about battery, charging, obstructions, radar bodies, tilt
    and room climate from live readings.
- **Startup.** The startup whir plays as the servos wake, a short "please wait" line is
  spoken the moment the whir and wake finish, and the droid start-up sound fills the rest
  until loading finishes and cuts it. The please-wait and ready lines are synthesized once
  and kept on disk; any still missing are filled in quietly after startup, so later
  launches never wait on the voice service. A spoken hardware notice follows when something failed to
  connect, then a short ready line (a statement, never an invitation).
- **Shutdown.** V2's power-down sound plays while the servos move to their parked
  positions and the lights fade.

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
- `--gui` opens V2's dashboard: camera, actuators with manual override, scene, logs, the
  3D droid, the transcript and the Motivator joystick console. It needs `requirements-gui.txt`;
  the supervisor's wake launch stays headless ([dashboard](docs/GUI.md)).
- The terminal shows the conversation. Detailed JSON diagnostics go to the session file
  printed at startup, under `local/sessions/`.
- `--record-evaluation` also keeps endpointed WAVs for human labeling.
- `python -m r3x doctor` checks dependencies, model files and credentials without
  opening devices.

**Always-on services.** The robot's LaunchAgents run V3's copies of V2's wake supervisor
and servo/LED/battery menu-bar apps (`operator_tools/`). Say **"wake up rex"** to start
Rex; the supervisor also keeps the V3 checkout updated from GitHub (fast-forward only,
never over uncommitted changes). See [operator services](docs/OPERATOR_CUTOVER.md).

**The wheels move when asked, or when Rex decides to go somewhere as part of his own goal**
(no smaller limits for his own moves; obstacle and time-of-flight checks apply to every move).
The base cannot sense drop-offs; the owner keeps him away from stairs and ledges. Leave `drive_port` empty for conversation-only tests. Floor tests are
described in [DRIVE_BASE_INTEGRATION.md](docs/DRIVE_BASE_INTEGRATION.md).

## Check

```sh
.venv/bin/python tools/run_checks.py                      # blocked-I/O contract checks
.venv/bin/python tools/run_firmware_checks.py             # drive firmware, compiled natively
.venv/bin/python -m r3x scenario tests/fixtures/core_memory.json
.venv/bin/python -m r3x replay local/dataset/manifest.json
.venv/bin/python -m r3x report local/sessions/SESSION_ID.jsonl
```

- `run_checks.py` blocks network, audio, serial and native libraries, and uses fake
  clocks, a simulated ESP32 and synthetic providers.
- `run_firmware_checks.py` compiles drive-firmware headers with clang++ and runs them;
  only the compiler and the programs it builds may start.
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
.venv/bin/python tools/prepare_voice_cloning.py    # Breeze TTS 2 8-bit for impressions (about 4.6 GB; --source DIR copies a local snapshot)
# Room recognition: export V2's MobileCLIP-S2 with any Python that has torch, open_clip and onnx
/Users/bbenziger/djr3x-v2/venv/bin/python -I tools/export_place_model.py --cache /Users/bbenziger/djr3x-v2/assets/models/mobileclip --out local/models/places/mobileclip-s2.onnx
```

Credentials and the Rex voice live in ignored `local/secrets.toml`
(`OPENAI_API_KEY`, `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` environment variables
override them). Startup verifies model digests and never substitutes or downloads
models on its own.

## Data rules

- Everything private is under ignored `local/`: settings, credentials, the store
  (`local/core.sqlite3`), recordings, voiceprints, visual memory, caches and session
  traces. Never commit or publish it.
- Bundled audio (`assets/audio/startup`, `assets/audio/motion`, `assets/audio/effects`) is
  V2's sound effects converted to WAV. It contains no personal recordings. The famous
  reference voices in `assets/voices/famous` are public clips ([provenance](assets/voices/README.md));
  references to people Rex knows are derived under `local/` and never tracked.
- No V2 personal data has been imported. Any import makes independent copies with a
  source digest and explicit ID mapping. Nothing opens a writable connection to V2's
  databases.

## Documents

| Document | What it answers |
| --- | --- |
| [Implementation status](docs/IMPLEMENTATION_STATUS.md) | What is finished, simulated only, or still open. |
| [Improvement plan](docs/IMPROVEMENT_PLAN.md) | The October 14 plan: steps, owner rulings, baseline and per-step evidence. |
| [Drive base](docs/DRIVE_BASE_INTEGRATION.md) | Wheels, radar, safety gates, vision seek and floor-run repairs. |
| [Servo integration](docs/SERVO_INTEGRATION.md) | Choreography, talking and idle motion, poses, microphone hold. |
| [LED integration](docs/LED_INTEGRATION.md) | Head/chest lights and animations. |
| [Sentry mode](docs/SENTRY_MODE.md) | Guarding a room alone: what trips and stands him down, recordings, phone alerts and their setup. |
| [Room recognition](docs/ROOM_RECOGNITION.md) | Which room, asking and learning rooms, connections, exploring, the V2 import. |
| [Voice impressions](docs/IMPERSONATION.md) | The `impersonate` tool, reference voices, the local cloning engine, checks and open acceptance. |
| [Dashboard](docs/GUI.md) | `live --gui`: V2's PySide6 window in V3, manual servo override and joystick driving through the owners. |
| [Operator services](docs/OPERATOR_CUTOVER.md) | Wake supervisor, menu-bar apps, auto-update, rollback. |
| [Live test script](docs/LIVE_TEST_SCRIPT.md) | Lines to say and results to check in hands-on conversation tests. |
| [DESIGN.md](DESIGN.md) | Who decides what, turn flow, identity and memory design. |
| [Proactive conversation](docs/PROACTIVE_CONVERSATION.md) | How Rex initiates and participates. |
| [Agency implementation plan](docs/AGENCY_IMPLEMENTATION_PLAN.md) | Goal-based initiative (October 2): what starts, steers and ends a goal, and the failed live runs that led to it; live acceptance remains open. |
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
  for attribution. A close voice while one recognized person is alone in view is
  `inferred` as them, never used for learning.
- **Honest receipts.** Rex never claims a save, a move, an arrival or speech before its
  executor reports success.
- **Small and replaceable.** No model or device loads at import. Every capability
  declares its dependencies and start/cancel/close. A replacement retires its
  predecessor.
- **Cost.** Ordinary replies use one `gpt-6-luna` call with low reasoning.
  See the [character/model comparison](docs/CHARACTER_EVALUATION.md) for measured
  costs, latency and remaining humor limitations. Stronger models run only
  narrowly: `gpt-5.6-terra` plans one goal at a time (at least eight seconds apart, never
  per reply), within the owner's roughly
  $45/month budget. Live goal-planning cost is not yet measured.
