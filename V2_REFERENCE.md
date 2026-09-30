# V2 reference and extraction map

## Authorized V2 battery menu fix — September 27, 2026

Owner-approved V2 change (not an extraction): after V3 quit, V2's battery menu app
logged `CHARGE:52:0` sent to the chest Nano, but the gauge stayed dark. Opening the
port resets the Nano and the app waited only 2 s, so a slow bootloader can swallow the
line, and its level de-duplication then waits up to 30 minutes. `tools/rex_battery_menubar.py`
now repeats the command 2.5 s later on the same open (no second reset). Committed
locally in V2 on top of `777b38c`; only `com.djr3x.battery` was restarted, and its
first paint logged the repeated send. V3 code is unchanged.

## Speech leveling and startup audio — September 27, 2026

Owner request. Source HEAD `777b38c803782cd4657e29ff544dfe7c1ca958ad`, clean tree; no V2
file moved, changed or deleted. `audio/speech_level.py` (SHA-256 `79544c13…77f28f8`) is
copied into `src/r3x/speech_level.py` with numpy/scipy loaded on first use (the contract
runner blocks native libraries) and V2's `TTS_LOUDNESS_*` defaults as fixed policy
(−20 LUFS, 26 dB max gain, −3 dBFS peak, 300 ms pre-roll, −4 dB for `[whispers]`).
Live composition plugs it into `ElevenLabsTTS`, so every phrase, including canned
startup lines, is leveled; it adds about 0.3 s before first audio. V2's leveler tests
run as `tools/check_speech_level.py`.

Startup: the nine `assets/audio/startup/*.mp3` clips are copied as 22,050 Hz mono
16-bit WAVs under `assets/audio/startup/`. `src/r3x/startup_audio.py` ports V2's order
(`main.py` startup audio, `_start_startup_boot_tts_thread`, ready line): startup whir,
a cycled `STARTUP_BOOT_TTS_LINES` "please wait" line and `droid_startup` filler over
servo wake and model loads, then a cycled `STARTUP_READY_TTS_LINES` line after the
hardware notice. The 48 boot and 34 ready lines are copied verbatim; cycling state and
the canned-line PCM cache live in ignored `local/cache/`. Spoken intro clips are copied
but unplayed (V2 default `PLAY_STARTUP_SPEECH_CLIP = False`); the shutdown clip is not
yet used. No live playback was run for this change.

## Operator services — September 27, 2026

Owner request. Source HEAD `777b38c` plus local commit `fb96f08` (battery menu chest
repaint), otherwise clean. Copied unchanged except for V3 wiring into `operator_tools/`
(SHA-256 prefixes of sources):
- `rex_supervisor.py` (`1c5a9a09`) and `utils/repo_updater.py` (`886b5bcb`),
  `utils/single_instance.py` (`c57c4a6b`) and `utils/supervisor_handoff.py`
  (`8e7fa293`);
- `tools/rex_battery_menubar.py` (`c50068a3`), `tools/rex_led_menubar.py` (`5c2592dd`),
  `tools/rex_servo_menubar.py` (`59d5ce7a`) and `tools/throttle_sequences.py`
  (`d9023e06`);
- `startup_chime.mp3` (`6cccd730`), `droid_gaining_electric.mp3` (`50a74597`) and
  `droid_losing_electric.mp3` (`085d80ee`), as 44.1 kHz mono WAVs in
  `assets/audio/operator/`.

Preserved: wake threshold and debounce, chimes, charger cues, mic-wedge relaunch (V3
now exits 86 on `MicrophoneStalled`), fast-forward-only auto-update (now of the V3
checkout), lock-based yielding, and every menu feature. Adapted: settings from
`local/settings.toml` via `operator_tools/v3_settings.py`, state and logs under
`local/`, V3's digest-checked wake-model copies, WAV playback without `soundfile`, and
no durable park marker. Checks: `tests/test_operator_tools.py` (synthetic), a live
no-motion handover (all four yielded in 0.7 s and resumed), and a live update check
("already current"). Voice wake launch and charger cues remain to be heard.

## Motion sound effects — September 27, 2026

Owner request. Source HEAD `77b2008`, V2 tree modified only in unrelated tool files; no V2
file moved, changed or deleted. `assets/audio/sound_effects/{motion_whir,motion_turning,
slow_down,arrived}.mp3` (SHA-256 prefixes `4eaf02ed`, `08740318`, `a7100c9c`, `d44db7ea`)
are copied as 22,050 Hz mono 16-bit WAVs under `assets/audio/motion/`. Behavior preserved
from `intelligence/motion_controller.py` `_fx_drive_loop_*` and `audio/sound_effects.py`:
the drive or turn clip loops for each wheel command and stops when the base reports done
or on stop; slow-down plays on a blocked stop and arrived after an approach; a quarter
volume for self-initiated motion relative to commanded
(`MOTION_AUTONOMOUS_FX_GAIN`); a 30 s loop cap; motor sounds never mute the microphone.
Adapted: `src/r3x/motion_sounds.py` plays through the one speech sink while the output
owner runs the drive (no second stream on the microphone's device), without LED lip sync,
trims the 4 s accents to 2 s with a fade, and plays commanded motion at 50% (owner;
V2 used 0.8). Synthetic checks only; live volume and
ASR under the whir remain to be heard.

## Drive base and radar extraction — September 27, 2026

User-authorized scope: LLM-directed base motion with radar evidence and no firmware
change. Source HEAD `777b38c803782cd4657e29ff544dfe7c1ca958ad`, clean working tree
(`git status --short` empty). SHA-256: `hardware/motion.py` `afe325f9…6d8da4`,
`hardware/radar.py` `98c0621a…fabe3`, `intelligence/approach.py` `f39e0216…610e97d`,
`intelligence/motion_swing.py` `ab57d0af…88cc95`, `intelligence/motion_agency.py`
`bde9bffe…67d73`, `docs/motion_protocol.md` `93a37741…1cac`, `config.py` `e1a061a0…aa0258`.

Preserved: protocol v1 framing, default port open (no DTR drop), hello/caps, 150 ms
heartbeat, config push, seq-correlated ack/done, tofmx decoding, V2's swing check
geometry and arm extents, throttle park before non-reverse motion, the approach
controller's arrival/blocked/lost rules, radar persistence and seam merge, the flinch
baselines and corroboration, charger voltage lockout and matrix/ring presence gates.
Adapted: one V3 owner and tool instead of timers, regex routes and an LLM route rescue;
V2's fixed 0.55 m fused-front subtraction replaced by a 0.35 m stand-off over per-band
matrix minima; explicit ports (radar by path, V2 resolved it by USB serial); no
reconnect, compass, place gating, games/DJ holds or motion sounds. Dependencies:
pyserial (already locked). No V2 file, firmware or data changed. Checks: 45 synthetic
tests and paced hosted planning probes; live floor, clearance and overshoot validation
remain. See [drive base integration](docs/DRIVE_BASE_INTEGRATION.md).

## Idle arms and elbow wake/rest — September 25, 2026

User-authorized expansion: restore random awake hero/poker arm motion and add elbow
wake/shutdown movement. Re-inspected `_idle_arm_wander_targets`, `arm_wander_thread`,
arm ranges, elbow neutral/rest and the current enabled hero-wander setting. V2 HEAD
remains `663fee5ef4dd050b56b5ffda1e8e529284171c37`, with the previously recorded dirty
working tree and authorized supervisor changes. No V2 file changed in this slice.
`sequences/animations.py` SHA-256 remains
`e5451f2883fbe9be329c10073b4e16f82698eb94d2ffb54989fe4692596432de`; `config.py` remains
`38853dc4b15a9aa4f1cf1f874ca54c97bde85057ce0c71acc49c8ba95e53a9c8`.

Preserve 4–9 second idle scheduling, broad hero and quieter poker excursions, opposite
side bias from offset poses, and elbow neutral 6720/rest 6300. V3 submits one bounded
idle request to the sole servo executor and stops/joins it before speech or shutdown.
It does not copy V2's forever-running independent servo writers or global flags.
V2's current config explicitly enables hero wander despite an older microphone caveat;
V3 permits the requested motion but keeps its microphone/direction evidence guards,
returns channel 7 to center, and reacquires a settled hold before learning. No extra
dependencies or settings. Six new fake-pulse/clock tests bring V3 to 561 passing
blocked-I/O checks. Live clearance, movement feel, listening gaps, enrollment coverage
and added runtime cost remain unmeasured; no hardware was operated for this change.

## Speaking visor addition — September 25, 2026

The user requested visor/headtilt expression while retaining the new neck turns.
Reused the existing recorded `hardware/servos.py` speech visor behavior and
`config.py` `VISOR_SPEECH_FLOOR_QUS=6000` (same revision/digests below). V3 adds a
bounded, independently repeating channel-3 lane from 6000 to 6640; existing slow
channel-2 sway and neck turns remain. No V2 changes, dependencies or configuration
switches. Strengthened existing tests require all channels 0–5 during production
playback, visor range/repetition, headtilt range, cancellation and pulse drain.
555 blocked-I/O checks pass; physical timing/visibility and visor camera occlusion
remain live validation, with no hardware operated for this change.

## Lifecycle pace adjustment — September 25, 2026

Rechecked V2 `sequences/animations.py` startup/shutdown at the recorded revision and
dirty-tree status below (source digest remains
`e5451f2883fbe9be329c10073b4e16f82698eb94d2ffb54989fe4692596432de`). V2's stepped
`move_to` target stream adds pacing beyond its Maestro caps. Following the owner's
live report that V3 moved too fast, the V3 head lifecycle caps are reduced to speed
12 / acceleration 3 for startup lift/visor/neck and shutdown/park droop. No additional
dependencies, V2 edits, new workers or settings. Existing synthetic lifecycle checks
cover profiles/arrival/cancellation; physical speed and V2 timing parity remain for
live retest. See [pace adjustment](docs/SERVO_INTEGRATION.md#lifecycle-pace-adjustment-after-live-feedback).

## Talking-motion repair from live feedback — September 25, 2026

Re-inspected active `hardware/servos.py` (`begin_speech_motion` and
`speech_reactive_move`) plus relevant `config.py` ranges/profiles at the same V2
HEAD `663fee5ef4dd050b56b5ffda1e8e529284171c37`. Source SHA-256 values remain
`20437af716ed2cc22a6787dcb0e985188c52a7625245a788e453afebdb1c5bd9` and
`38853dc4b15a9aa4f1cf1f874ca54c97bde85057ce0c71acc49c8ba95e53a9c8`, respectively.
The tree remains dirty as recorded below, additionally retaining the authorized
supervisor handoff changes and two new files; no V2 file changed in this repair.

The original V3 extraction omitted the hero-arm elbow/wrist and neck motion, and
its small headlift beat stalled behind the throttle cycle. Preserve V2's normal
head/arm movement ranges and profiles through explicit independent lanes in the
single V3 servo executor. Head anchors stay stable across cancelled phrases; the
microphone-bearing channel 7 remains fixed. No extra dependencies, V2 mood/global
state, random per-frame writes or separate motion workers were imported. Visor,
poker-arm and amplitude-driven styling remain outside this specific correction.

555 blocked-I/O V3 checks pass, with three new delayed-pulse/repetition/bounds
contracts and strengthened production playback/channel coverage. The user's live
trace shows a running talking worker, not visible joint arrival. Live retesting of
the repaired movement, microphone contamination, clearances and latency remains
pending; see [servo evidence](docs/SERVO_INTEGRATION.md).

## Cooperative supervisor handoff — September 25, 2026

The user explicitly requested that manual V3 launches make the V2 supervisor yield,
and approved one restart to load the update. This is a scoped exception to the
read-only V2 baseline: only `rex_supervisor.py`, a new independent stdlib
`utils/supervisor_handoff.py`, and its new tests were changed for this request.
Previously dirty V2 files were preserved. V3 has no import dependency on V2.

The existing V2 controller-lock behavior remains. The new protocol adds a per-PID
OS-held registration and a shared microphone lease held through successful stream
close. Registration is verified for every live legacy listener; stale files are
not proof. Recheck after handoff rejects a supervisor restart race. Failed microphone
close prevents launch/reopen and retains its lease. Process exit releases the locks.
An updated supervisor resumes listening when manual V3 releases controller ownership;
an old unregistered supervisor still fails closed. A running V2 controller remains
exclusive and is never stopped by V3. Installing a replacement V3 wake supervisor
still requires unloading the V2 supervisor to avoid competing wake listeners.

Post-change SHA-256 at V2 HEAD `663fee5ef4dd050b56b5ffda1e8e529284171c37` plus the
recorded dirty tree:

| Source | SHA-256 |
| --- | --- |
| `rex_supervisor.py` | `1c5a9a09cee51ffe320c180c396c8739adcce5338d3c62ea96408802049c1c96` |
| `utils/supervisor_handoff.py` | `8e7fa293897e5ef9177cb6ff4e0cf7b21a081c23411186f75b0a27eba991d24b` |

Evidence: 552 V3 blocked-I/O tests pass. V2's venv/isolated lean runner passes five
new fake-stream/lease tests and ten existing wedge-policy tests. Its broader
supervisor module passes 26/28, with two old default assertions (0.7/three frames)
conflicting with unchanged HEAD behavior (0.92/four); those functions were compared
structurally against HEAD. That module includes the real wake model on synthetic
silence, not a live accuracy test. No thresholds or unrelated tests were changed.

The approved graceful SIGTERM/KeepAlive restart completed. Read-only process and
OS-lock probes confirmed the replacement V2 supervisor is active, holds protocol-v1
registration and has reacquired its microphone lease. No V3 controller, microphone,
servo or LED was started for validation. Full live takeover/recovery and visual/
mechanical acceptance remain pending; the restart resumes V2's existing listener.

## LED protocol and playback extraction — September 25, 2026

Read-only V2 HEAD `663fee5ef4dd050b56b5ffda1e8e529284171c37`; the same dirty
working tree listed in the servo extraction below was present, including `config.py`.
Active driver, audio, sequence and Arduino sources were used instead of historical
LED notes. The installed firmware was not queried or flashed.

| Source | SHA-256 |
| --- | --- |
| `hardware/leds_head.py` | `e5d4d772e860e7268d0a05bca30846bf96eaf8acd4f8be5cf8d6e8fe06bbc5a2` |
| `hardware/leds_chest.py` | `26f77c47897e744a43cfdf988e604eb390c09af17c9a1f758397a7a0855e959e` |
| `arduino/head_nano/head_nano.ino` | `72a946e432a475c1ffbd15f28d7036f555fe42367068001b7da394972cae3aa6` |
| `arduino/chest_nano/chest_nano.ino` | `1619abfe5619fb04eed7f8b9155799266f863443b67da16bec38800d3155c7aa` |
| `audio/tts.py` | `6d91a6554eab59a41b07c13f14362c1dea9f69e7370406756ffe118b54e8cd4a` |
| `tools/rex_led_menubar.py` | `5c2592dd963eec36feb5367e90de42d4ee11a49ece1d723f39b60bb9ade725d4` |
| `config.py` | `38853dc4b15a9aa4f1cf1f874ca54c97bde85057ce0c71acc49c8ba95e53a9c8` |
| `sequences/animations.py` | `e5451f2883fbe9be329c10073b4e16f82698eb94d2ffb54989fe4692596432de` |

Preserved behavior: chest startup, warm startup eyes, six speaking color styles,
RMS scale 800 and delta eight, repeated mouth stop plus eye-blink rearm, compliment
sparkles and four-second shutdown fade. Firmware handles mouth GRB conversion;
Python sends ordinary RGB. Head firmware needs level keepalives every <1.5 seconds,
not just an initial SPEAK command. Chest curious falls back to neutral by design.

Adaptation: V3-owned explicit ports/pyserial dependency, a single lifecycle/serial
owner and bounded PCM mailbox driven by the production audio sink; no V2 imports,
global config/state, independent conversational handlers or reconnect loops. The
existing state owner validates the LLM's bounded `animate_lights` proposal and the
existing output owner executes it with command-only receipts. No new model,
framework, firmware fork or always-running service was added. Off-program V2 LED
and battery tools remain installed; confirmed descriptor release supplements their
shared controller-lock handoff before V3 may open the boards. The adapter does not
import, spawn or depend on those scripts. Independent V3 menus are future work.

Focused evidence: **548 blocked-I/O tests**, including 20 new synthetic LED
contracts and the production sink PCM-observer check. No real model, personal data,
audio device or serial port was used. A new ignored cutover bundle was prepared;
default settings, installed services and V2 files were not modified. See
[LED integration](docs/LED_INTEGRATION.md) for behavior, resource limits and pending
live firmware/color/timing/audio-load/handoff acceptance. No visible LED success or
measured hardware latency is inferred from fake serial writes.

## Servo lifecycle and talking-motion extraction — September 25, 2026

Follow-on implementation: explicit serial opening, cross-version controller/resource
leases, V3 supervisor and servo console, robot lifecycle/microphone holds and a
prepared LaunchAgent cutover. V2 services remain running at the owner's request.
Additional active-source digests at the same revision/dirty state:

| Source | SHA-256 |
| --- | --- |
| `rex_supervisor.py` | `6c547312e8eaf051bf341feb5b9ba37dfbce384938dde88b859af26c01683928` |
| `tools/rex_servo_menubar.py` | `59d5ce7aeb934f35fa0a0ee4f1cb766958b383a4d1e40e06af3351120c46bb16` |
| `utils/single_instance.py` | `c57c4a6b406e0182ffc2cf991c394c608a1e47a486d4b531a8a48472098df341` |
| `utils/port_handoff.py` | `2ca72b334426cf0eaed78be644ee2bde4a6ac01a76d18a1651a4e676d78f3421` |

Preserve selected-channel wake detection (0.92, four 80-ms frames), off-program
sliders/readback and explicit board home, lock-driven dormancy and reverse resource
release. Replace blind timeout continuation with failed handoff; queued commands
carry connection generations and cannot survive handoff. Do not copy automatic Git
updates, independent servo writers or V2's environment/config loader. The newer V2
sequence-recording UI was inventoried but is not included in this initial console.

Dependencies: optional `pyserial==3.5`, `rumps==0.4.0`, `openwakeword==0.6.0` and
resolved additions are pinned in `requirements-robot.txt`, installed only in V3.
The wake classifier and two feature ONNX models were independently copied by
`tools/prepare_operator_migration.py`; source paths/digests are recorded in ignored
`local/models/wake/manifest.json`. Effective V2 servo limit overrides were inspected
and empty; the Maestro device path went only into the independent prepared local
configuration. No personal database, recording or voiceprint was imported.

Validation: 528 blocked-I/O tests pass (48 new across both servo slices); one real
wake-model run used synthetic silence with no audio device. Measurements and pending
live gates are in [servo evidence](docs/SERVO_INTEGRATION.md#verification). The prepared
bundle records and backs up all four installed V2 agents. No job was changed or
hardware operated. The source-only first-slice description below is historical;
its no-serial-opener limitation is superseded by this follow-on.

Read-only V2 HEAD `663fee5ef4dd050b56b5ffda1e8e529284171c37`. The working tree
was **dirty**: `CONTEXT.md`, `README.md`, `config.py`, `hardware/throttle_motion.py`,
`sequences/throttle_arm.py`, and three throttle test files. The user explicitly
selected the current throttle working tree; the commit alone does not contain it.
No V2 files, local data or running services were changed.

| Inspected source | SHA-256 at extraction |
| --- | --- |
| `hardware/servos.py` | `20437af716ed2cc22a6787dcb0e985188c52a7625245a788e453afebdb1c5bd9` |
| `hardware/throttle_motion.py` | `dac2c35a31bc2589cbc87d5e76e270b6f410a1c1aaf890a60273116be8a6bfa0` |
| `sequences/animations.py` | `e5451f2883fbe9be329c10073b4e16f82698eb94d2ffb54989fe4692596432de` |
| `sequences/throttle_arm.py` | `5de60a4fffae3e097d0b60547ab1ea33590bff93a4a82548a898bbc4db3e075b` |
| `config.py` (servo sections only; not copied/imported) | `38853dc4b15a9aa4f1cf1f874ca54c97bde85057ce0c71acc49c8ba95e53a9c8` |

Preserved: overlapping head raise/neck scan/throttle unfold, coordinated head droop
and elbow rest, tuck/park routes including full-down recovery, atomic throttle
targets, bounded profiles, cancellation and actual-output evidence. Preserve talking
movement: an initial pair of V2 throttle speech poses plus bounded headlift motion
now follows actual playback through V3's output owner. Richer V2 expression remains
an explicit follow-on, not a second mood/idle controller.

Adaptation: `servo_poses.py`, `servos.py`, and `adapters/maestro.py` own independent
typed requests, calibrated source constants, one executor, structured receipts and
an injected transport. `output.py` supplies optional audio-start/stop coordination.
V2 dependencies on pyserial, global config/state, world state, LED modules and
multiple workers are not imported. This slice adds only standard-library code;
there is no serial opener or new live dependency. V2's obsolete 1984 headlift floor
is replaced with its effective configured clamp at 2600. Pulse readback is never
called measured shaft feedback. Cold zero/partial state is rejected rather than
trusting V2's park marker or stale pose cache.

Focused checks use synthetic ports/clocks and the production executor/output path;
no V2 tests were run (the historical park test can operate real hardware).
[Slice details, verification and outstanding live gates](docs/SERVO_INTEGRATION.md)
cover handoff, commissioning, mechanical clearance, talking motion, microphone
holds/audio exclusion, physical timing and combined-load measurements. Existing
operator-tools migration remains required before live activation.

## ReSpeaker microphone/direction extraction — September 17, 2026

Source: clean V2 `488780557ad70925e3b3c23fb542f544ed51a5b7`, verified before
extraction. Relevant files: `audio/stream.py`, `audio/hardware_aec.py`,
`hardware/flex_doa.py`, `tools/flex_ctl.py`, and
`docs/respeaker_flex_xvf3800.md` including its September 7 field comparison.
Older ReSpeaker Lite/channel-1 notes are superseded by the current Flex channel-0
configuration. The owner reaffirmed channel-0 hardware AEC validation.

Preserved: processed channel 0 only, 1.5 host gain, playback through the Flex;
USB read protocol; selected beam over lagging DoA; 50,000 energy floor; circular,
energy/recency-weighted direction evidence and playback exclusion. Dependencies:
existing sounddevice plus pyusb 1.3.1 and libusb-package 1.0.30.0 (bundled libusb).
V3 adaptation uses owned instances and injected clocks/output state, bounded
read-only polling, exact speech windows, explicit unavailable/ambiguous results,
source-linked model context and existing enrollment guards. No V2 global state,
servo imports, writable USB commands, data copies or startup chain were reused.

[Checks and remaining live validation](docs/RESPEAKER_INTEGRATION.md): 398 synthetic
contract checks, live read-only USB routing/poll/lifecycle probe, dependency checks.
The new worker's standalone measured cost is 0.042 CPU seconds over 5.09 seconds;
combined-load latency and two-person direction accuracy remain unmeasured.
No robot motion, audio playback or audio/video capture was performed. Physical
enrollment hold, exact arm/camera transforms and talk-over are not implemented.

This is a map to preserve implementation knowledge, not a dependency manifest or
an instruction to copy all of v2. Start with [DESIGN.md](DESIGN.md) and the
[initial test program](docs/INITIAL_TEST_PROGRAM.md). Extract a small integration
only when its implementation slice needs it.

## Reference snapshot

Inspected during the September 9–10, 2026 design session:

- Local source: `/Users/bbenziger/djr3x-v2`.
- V2 HEAD at final verification: `9806c74fd233a7118da88ca5ae55fb7450deec05`
  (`Update identity references and local TTS behavior`).
- Working tree: **clean** at final verification. Recent identity, onboarding,
  nickname, impersonation, and speech-cutoff fixes became committed during this
  documentation session. Use this revision, not its predecessor `53d3c86`, as the
  reference for those fixes. Remote synchronization was not checked.
- V3 started at `3779af19277c35c7dfc66773ef39e0d63bf3b436`, with a placeholder README
  and Python `.gitignore`. That initial session added design documents only; implementation entries below record the later core build.

The links below point to local files for convenient inspection. They can change
after this snapshot; check the source and diff again before extraction. Record the
source file digest/revision when code is actually moved. No v2 tests or live devices
were run to validate v3 as part of this documentation task.

Useful orientation: [README](/Users/bbenziger/djr3x-v2/README.md),
[CONTEXT](/Users/bbenziger/djr3x-v2/CONTEXT.md),
[CLAUDE](/Users/bbenziger/djr3x-v2/CLAUDE.md), and
[Lean restructuring plan](/Users/bbenziger/djr3x-v2/docs/lean_brain_restructuring_plan.md).
These contain historical sections. Distinguish a previous plan from current code
and observed behavior; neither an old note nor a passing unit test proves room performance.

## First-program sources

| Capability | Start with | Preserve or investigate | Boundary for v3 |
| --- | --- | --- | --- |
| Microphone and VAD | [audio/stream.py](/Users/bbenziger/djr3x-v2/audio/stream.py), [audio/vad.py](/Users/bbenziger/djr3x-v2/audio/vad.py) | PCM capture, voiced intervals, device handling, endpoint timing | Explicit lifecycle and timestamps; no v2 conversation globals |
| Batch ASR | [audio/transcription.py](/Users/bbenziger/djr3x-v2/audio/transcription.py), especially `_qwen_transcribe` | Qwen3-ASR loading/preprocessing, transcript quality, original audio correlation | One selected backend; report failures instead of silently changing provider or quality semantics |
| CAM++ encoder | [audio/campplus.py](/Users/bbenziger/djr3x-v2/audio/campplus.py), `Encoder`; [CAM++ notes](/Users/bbenziger/djr3x-v2/docs/campplus_voice_id.md) | Exact model, preprocessing, normalized embedding, model namespace | Extract the encoder adapter; do not import all legacy recognition backends |
| Recognition evidence | [audio/speaker_id.py](/Users/bbenziger/djr3x-v2/audio/speaker_id.py), `rank_embedding`, `window_evidence` | Scores against distinct people, margins, sample quality, bounded window analysis | Identification and speaker-change detection require separate calibration |
| Conversational learning | [intelligence/voice_learning.py](/Users/bbenziger/djr3x-v2/intelligence/voice_learning.py), `Learner`, `Policy`, `Sample`; [learning notes](/Users/bbenziger/djr3x-v2/docs/conversational_voice_learning.md) | Provisional anchor, repeated compatible samples, expiry/correction, original recordings | Reuse the evidence contract; integrate into v3's single tracker/state owner |
| Voice archive | [memory/voice_recordings.py](/Users/bbenziger/djr3x-v2/memory/voice_recordings.py), `save_batch`; [recording utility](/Users/bbenziger/djr3x-v2/tools/voice_recordings.py) | Atomic WAV/embedding writes, deduplication, metadata, limits, previewed re-embedding | V3-owned store and independent imports; preserve namespace and deletion behavior |
| Face presence | [vision/face.py](/Users/bbenziger/djr3x-v2/vision/face.py), [vision/face_presence.py](/Users/bbenziger/djr3x-v2/vision/face_presence.py) | Face track continuity, identity alternatives, interval-bound presence | Small faces remain uncertain; no mouth-derived active-speaker evidence |
| Main reply and tools | [intelligence/lean_brain.py](/Users/bbenziger/djr3x-v2/intelligence/lean_brain.py), `stream_reply`; [tool_router.py](/Users/bbenziger/djr3x-v2/intelligence/tool_router.py) | Prompt size, streaming reply path, useful tool schemas | Tools return correlated results; no independent spoken responses or old handler ladder |
| Rex's character | [intelligence/personality.py](/Users/bbenziger/djr3x-v2/intelligence/personality.py) | Review persona guidance and representative dialogue | A compact prompt; do not transplant serial mood/comedy/governor machinery |
| Online TTS | [audio/tts.py](/Users/bbenziger/djr3x-v2/audio/tts.py) | ElevenLabs request shape, settings, cache identity, streaming, drain/cancel behavior | A provider adapter plus the single output owner; no imported servo/LED orchestration |
| Delivery and self-audio | [audio/speech_queue.py](/Users/bbenziger/djr3x-v2/audio/speech_queue.py), [audio/echo_cancel.py](/Users/bbenziger/djr3x-v2/audio/echo_cancel.py) | Queue ordering, cancellation, output timing, echo rejection | Actual delivery events must reach session state; generated text is not proof of playback |
| Names and minimal memory | [memory/people.py](/Users/bbenziger/djr3x-v2/memory/people.py), [memory/facts.py](/Users/bbenziger/djr3x-v2/memory/facts.py), [memory/conversations.py](/Users/bbenziger/djr3x-v2/memory/conversations.py) | Stable IDs, unique canonical/alias/nickname resolution, persistence examples | New attributed-claim schema; language meaning stays with the model |
| Isolated checks and replay | [tools/production_replay.py](/Users/bbenziger/djr3x-v2/tools/production_replay.py), [tools/run_lean_checks.py](/Users/bbenziger/djr3x-v2/tools/run_lean_checks.py) | Temporary stores, blocked external I/O, production-path replay | Build against the new core; do not import v2's monolith to reuse its harness |

### Model and protocol details to retain

The following are **v2 source defaults or implementation details**, not a report
of the effective live configuration and not a mandatory v3 model list. Local
overrides, environment, credentials, availability, and future API changes must be
checked during implementation.

- ASR defaults to Qwen3-ASR. The existing Qwen path preserves token log-probability
  information through its decoding integration; replacing it with a convenient
  wrapper must not silently discard the quality signal used by the turn pipeline.
- CAM++ uses the `speech_campplus_sv_zh_en_16k-common_advanced` ONNX export, 192-dimensional
  unit embeddings, 16 kHz input, and 80-bin Kaldi filterbanks with mean normalization.
  The adapter uses CPU ONNX inference and a Torch/torchaudio feature frontend; this
  dependency and CPU cost still need to be accounted for. The model's minimum input
  length is not a promise of accurate identity on a brief "yes."
- CAM++ recognition defaults include cosine `0.50` and margin `0.07`. Enrollment
  starts with three qualifying turns, at least two voiced seconds each and eight
  total, plus anchor/cluster agreement. These are baselines to evaluate, not calibrated
  probabilities or universally appropriate diarization thresholds.
- V2's reply defaults distinguish `LLM_MODEL` from `LLM_CONVERSATION_MODEL`; the latter
  is `gpt-5.4-mini` in the inspected source. Do not infer the actual conversation
  backend solely from the generic setting, or copy every model switch into v3.
- ElevenLabs defaults to `eleven_v3_conversational` in the inspected source. Review
  `_pin_v3_stability`, `_v3_seed`, `_stitch_previous_text`, `_cache_path`, and
  `_fetch_from_api`. Preserve the chosen stability/tag policy and cache identity
  across model, voice, text, seed, and effective settings. A requested seed does not
  establish bit-for-bit provider reproducibility.
- The current TTS code omits `previous_text` for both v3 model variants. Its recorded
  live `unsupported_model` rejection concerns `eleven_v3`; compatibility with the
  conversational variant was not independently validated. Do not turn the broader
  omission policy into a claim that both APIs were tested. Older nearby comments
  about stitching and whole-reply behavior do not supersede current request code.

Model provenance helper: [tools/download_campplus.py](/Users/bbenziger/djr3x-v2/tools/download_campplus.py).
Keep the checkpoint digest and preprocessing namespace with any imported embeddings.
Do not copy credentials or assume a machine-specific voice ID belongs in tracked docs.

### Existing seams that must not become v3 architecture

- [intelligence/interaction.py](/Users/bbenziger/djr3x-v2/intelligence/interaction.py)
  contains the live orchestration and useful failure evidence, but also many competing
  paths. Its anonymous-slot helper handles unnamed voices separately from recognized
  people. That is not the proposed unified named/unnamed tracker.
- [intelligence/voice_learning_runtime.py](/Users/bbenziger/djr3x-v2/intelligence/voice_learning_runtime.py)
  shows where the learner is invoked and how original capture evidence is carried.
  It depends on the old interaction state; copy the contract, not the global bridge.
- [intelligence/attribution.py](/Users/bbenziger/djr3x-v2/intelligence/attribution.py)
  still contains mouth-derived attribution decisions despite conversational learning's
  mouth-free intent. V3 excludes those inputs from every identity and learning path.
  This observation does not establish mouth tracking as the cause of every v2 incident.
- [intelligence/onboarding.py](/Users/bbenziger/djr3x-v2/intelligence/onboarding.py)
  and [memory/name_validation.py](/Users/bbenziger/djr3x-v2/memory/name_validation.py)
  contain recent defensive fixes for literal names and misfiled answers. Preserve
  their regression scenarios; do not rebuild a growing regex-based semantic interpreter.
- [memory/retrieval.py](/Users/bbenziger/djr3x-v2/memory/retrieval.py) is a reference
  for later retrieval, not an initial dependency. Measure synchronous embedding work
  before putting it on the response path.

## Later integrations worth preserving

| Capability | Sources | Constraints or lessons |
| --- | --- | --- |
| ReSpeaker direction | [hardware/flex_doa.py](/Users/bbenziger/djr3x-v2/hardware/flex_doa.py), [tools/flex_doa.py](/Users/bbenziger/djr3x-v2/tools/flex_doa.py), [tools/flex_ctl.py](/Users/bbenziger/djr3x-v2/tools/flex_ctl.py) | Timestamped capture evidence; direction is supporting evidence, not identity. Missing data stays missing. Camera left/center/right is coarse context |
| Enrollment mic hold | [hardware/servos.py](/Users/bbenziger/djr3x-v2/hardware/servos.py), `hold_voice_enrollment_mic`, `release_voice_enrollment_hold` | Midpoint of configured safe range, settling, bounded lease, release/invalidate on movement and cancellation; required before enrollment on the physical robot |
| Local TTS | [audio/local_tts.py](/Users/bbenziger/djr3x-v2/audio/local_tts.py), [local TTS notes](/Users/bbenziger/djr3x-v2/docs/local_tts_backends.md) | Breeze 8-bit is the chosen voice; preserve its verified reference pair and API contract. Offline mode should be a deliberate capability profile |
| Local scheduling | [utils/mlx_lock.py](/Users/bbenziger/djr3x-v2/utils/mlx_lock.py), [utils/local_work.py](/Users/bbenziger/djr3x-v2/utils/local_work.py), [audio/breeze_fast_depth.py](/Users/bbenziger/djr3x-v2/audio/breeze_fast_depth.py), [audio/breeze_ref_cache.py](/Users/bbenziger/djr3x-v2/audio/breeze_ref_cache.py) | Coordinate ASR/TTS and optional local work; do not assume Python threads make MLX workloads independent |
| Impersonations | [features/impersonation.py](/Users/bbenziger/djr3x-v2/features/impersonation.py), [impersonation plan](/Users/bbenziger/djr3x-v2/docs/local_tts_impersonation_plan.md) | Reuse eligible natural recordings, match transcript to reference audio, keep useful partial captures, resolve nicknames, and use supported biographical material |
| Single-instance handling | [utils/single_instance.py](/Users/bbenziger/djr3x-v2/utils/single_instance.py) | Useful lifecycle example; avoid two processes competing for the same microphone or database |

Breeze currently uses `mlx-community/Breeze-TTS-2-mlx-8bit` with the v2 integration
pinned to `mlx-audio[tts]==0.5.1`. That version records a known integration, not a
claim that it is the latest library. The user's 8-bit choice should not be replaced
by 4-bit or mixed precision without a new decision.

Current ordinary Breeze speech uses 1.5 seconds of preroll; the impersonation path
prepares a complete take. A previous rule deliberately aborted impressions after
three playback underruns and was removed in the latest working tree. **Do not restore
that rule from an older note.** An underrun reports starvation; it does not mean all
remaining generated audio should be thrown away. Validate complete content and live
playback separately, with the camera and local model workload present.

[tools/check_breeze_speech.py](/Users/bbenziger/djr3x-v2/tools/check_breeze_speech.py)
helps check generated speech content. Passing that check is not proof of uninterrupted
speaker playback. Original user recordings and voice-reference assets remain private;
this source map does not import them.

## Regression cases to bring forward

The [initial test plan](docs/INITIAL_TEST_PROGRAM.md#conversation-and-memory-scenarios) translates
these into v3 acceptance scenarios. Logs describe what text the system displayed or
heard; without the corresponding waveforms they cannot prove segmentation or playback.

| Field evidence | Lesson to preserve |
| --- | --- |
| [16:45 conversation](/Users/bbenziger/djr3x-v2/logs/conversation-2026-09-09-16-45-24.log) | Rex greeted Bret by name, then reflexively asked who was speaking. Carry the recent exchange as contextual evidence without treating it as confirmed voice enrollment |
| [21:11 conversation](/Users/bbenziger/djr3x-v2/logs/conversation-2026-09-09-21-11-40.log) | Jeff's introduction and partial impersonation samples exposed competing enrollment/capture paths. Preserve eligible natural data and handle a decline without discarding useful accepted material |
| User's report of truncated impressions and current local-TTS notes | Generated text can outlive the audio that played. Track delivery and verify the distinctive final phrase; never report a take complete solely from the GUI transcript |
| User's Jeff nickname and "Boogers/creepy" reports; current impersonation fixes | Resolve explicit nicknames. A question, joke, or comment aimed at Rex is not the speaker's biography; generated parody must not become later factual evidence |
| [22:31 conversation](/Users/bbenziger/djr3x-v2/logs/conversation-2026-09-09-22-31-23.log) | "That's Jeff Benziger" became a literal name. The speaker, referenced person, and uncertain visible face need separate representation before any write |

Useful existing tests to read for contracts, not transplant wholesale:

- Capture/encoder: [test_transcription_backend](/Users/bbenziger/djr3x-v2/tests/test_transcription_backend.py),
  [test_campplus](/Users/bbenziger/djr3x-v2/tests/test_campplus.py),
  [test_speaker_segments](/Users/bbenziger/djr3x-v2/tests/test_speaker_segments.py).
- Attribution/learning: [test_voice_learning](/Users/bbenziger/djr3x-v2/tests/test_voice_learning.py),
  [test_voice_primary_identity](/Users/bbenziger/djr3x-v2/tests/test_voice_primary_identity.py),
  [test_person_reference_identity](/Users/bbenziger/djr3x-v2/tests/test_person_reference_identity.py),
  [test_face_tracking](/Users/bbenziger/djr3x-v2/tests/test_face_tracking.py).
- Meaning/output: [test_onboarding](/Users/bbenziger/djr3x-v2/tests/test_onboarding.py),
  [test_tool_router](/Users/bbenziger/djr3x-v2/tests/test_tool_router.py),
  [test_impersonation_recordings](/Users/bbenziger/djr3x-v2/tests/test_impersonation_recordings.py),
  [test_impersonation_take](/Users/bbenziger/djr3x-v2/tests/test_impersonation_take.py),
  [test_breeze_tts](/Users/bbenziger/djr3x-v2/tests/test_breeze_tts.py).

The latest reference commit includes `test_person_reference_identity.py` and the
recent changes to these tests. No test counts or green-suite claims are implied by
this catalog. V2 tests require its own venv and isolated runner; v3 needs its own environment.

## Research pointers, not selected new dependencies

- [3D-Speaker](https://github.com/modelscope/3D-Speaker): primary toolkit reference
  for speaker models and diarization. Evaluate any added model on the actual workload.
- [Overlap-aware low-latency online speaker diarization](https://arxiv.org/abs/2109.06483):
  an incremental clustering/segmentation approach worth comparing with the simple
  baseline. This paper is not a benchmark of Rex, CAM++, or the M2 robot.
- [MLX](https://github.com/ml-explore/mlx) and
  [MLX Swift](https://github.com/ml-explore/mlx-swift): native computation and alternate
  language interfaces. They do not establish a whole-program Swift speedup.
- [Qwen3-ASR Swift adapter](https://github.com/Blaizzy/mlx-audio-swift/blob/main/Sources/MLXAudioSTT/Models/Qwen3ASR/README.md)
  and [AVAudioEngine](https://developer.apple.com/documentation/avfaudio/avaudioengine):
  examples for a future measured native port, not prerequisites for this prototype.

Verify current primary documentation when implementing a changed external API. Preserve
the v2 lessons while measuring whether its particular adapter still meets v3's contract.

## Record each extraction

Append a brief entry when code is actually reused:

1. Capability and v3 owner; source paths, commit, dirty-file digest if applicable.
2. Exact behavior retained, dependencies/settings required, and code deliberately retired.
3. Adaptation to v3 IDs, lifecycle, cancellation, outcomes, storage, and error reporting.
4. Focused automated evidence, saved-audio evidence, live validation still required.
5. Any model/API behavior still assumed rather than verified.

Slice 0 (September 10, 2026) was implemented independently with Python standard-library
boot, configuration, and lifecycle code. It required no v2 source extraction or
baseline-data access.

### Slice 1: audible-loop adapters (September 10, 2026)

Source reverified: v2 `9806c74fd233a7118da88ca5ae55fb7450deec05`, clean working tree.
V3 does not import v2 modules or connect to its databases. Dependencies were installed
in v3, with the resolved environment in `requirements-live.txt`.

| Source / owner | Retained behavior and adaptation | Evidence / remaining validation |
| --- | --- | --- |
| `audio/transcription.py::_qwen_transcribe` → `adapters/asr.py` | Qwen3-ASR-1.7B-8bit, mono 16 kHz, batch decode through `stream_generate`, 256-token cap, token log probabilities, -0.35 initial trust floor. One instance-owned lock; explicit lifecycle/cancel token. No v2 globals, lexical bias, recent-Rex prompt, text correction ladder, or Whisper fallback. | Real synthetic saved-audio decode passed with the words preserved; room-quality thresholds and native cancellation still need live evaluation. |
| `audio/stream.py`, `audio/vad.py` → `adapters/audio.py`, `capture.py` | 512-frame callbacks, timestamped PCM, Silero 6.2.1, explicit selected mic, bounded buffers, reset on gaps. VAD loads only at start. Name resolution refuses missing/ambiguous matches rather than falling back to an unrelated index. No servo/AEC/watchdog startup chain copied. | Synthetic endpoint/gap/echo/duration tests passed; selected Mac microphone metadata validated without recording. Real endpointing, mic stalls, and output overlap remain live checks. |
| `intelligence/lean_brain.py::stream_reply`, `config.py::REX_CORE_PROMPT` → `adapters/hosted.py` | One main streaming generation with the existing `gpt-5.4-mini` model; a compact adapted Rex persona. Responses API with `store=false` and no retries replaces the old completion wrapper. No mood/governor/handler stack, planner, identity tools, or memory writer was copied. | One real reply request passed; production Session one-call and stale/cancel contracts passed with fixtures. Natural conversation and meaning remain unvalidated. |
| `audio/tts.py`, selected `TTS_V3_*` constants → `adapters/hosted.py`, `output.py` | `eleven_v3_conversational`, selected Rex voice, `pcm_22050`, natural stability .5, seed 1440639067, approved tag palette, and no `previous_text`. No silent buffered/local fallback, motors, LEDs, or cache. The sole output owner emits drain-based completion; incomplete phrases never enter history as fully delivered. | Real synthesis to WAV and synthetic PCM-final-tail checks passed. Actual playback, underruns, cancellation, and consistent voice across phrase requests remain live checks. |

Source SHA-256 for the two directly adapted protocol implementations:

- `audio/transcription.py`: `873e5cba07de8a4f5b6f3f0d29453d90c85ccab52ae72c548e01ef28e474be85`
- `audio/tts.py`: `7719de3988515d0decb451355bd7317db3c7eb155c878e19401c7e66cfc81356`

Deliberate speech scheduling choice: the v3 design streams ordinary prose. This
slice therefore synthesizes bounded phrases while the one reply generation continues,
unlike v2's whole-reply v3-voice path. It retains the v3 request settings but does not
claim equivalent timbre between phrases. Evaluate that tradeoff in the live gate;
there is no unmeasured second TTS mode to silently fall back to.

The user explicitly authorized the v2 device configuration and API credentials.
`tools/import_v2_setup.py` read selected `.env` entries and literal values from
`apikeys.py` / `config.py` without executing them. It wrote independent ignored
settings and secrets, retained camera selection as deferred metadata, and copied
only the nonpersonal ASR model files with matching SHA-256 digests. The private
manifest is `local/v2-setup-import.json`. No v2 data was modified, and no people,
voiceprint, original-person-audio, or claim import occurred.

Current protocol references checked during implementation:
[OpenAI streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses),
[ElevenLabs streamed speech](https://elevenlabs.io/docs/api-reference/text-to-speech/stream),
and [sounddevice raw streams](https://python-sounddevice.readthedocs.io/en/0.5.5/api/raw-streams.html).
Package-specific Qwen stream details were checked in the installed v2 mlx-audio
0.5.1 implementation, then exercised independently in v3. The
[slice-1 evidence](docs/INITIAL_TEST_PROGRAM.md#slice-1-implementation-evidence)
distinguishes synthetic contracts, real providers on saved audio, and pending live work.

### Slices 2–4: acoustic evidence, camera presence and storage (September 10, 2026)

V2 source was reverified at `9806c74fd233a7118da88ca5ae55fb7450deec05`, clean.
V3 changes are based on `47a1507`. No v2 module/environment/database is a runtime
dependency, and no v2 personal data or files were changed.

| Source → v3 owner | Retained behavior and adaptation | Evidence / remaining live work |
| --- | --- | --- |
| `audio/campplus.py` → `adapters/voice.py`, `speakers.py` | Exact public ONNX checkpoint, 16 kHz/80-bin globally centered Kaldi features, unit 192-D embeddings, CPU two-thread inference. New explicit lifecycle and bounded windows; one named/unnamed tracker, margin/unknown option, namespace and disagreement guards. No legacy score offsets or alternate model fallback. | Real synthetic saved audio and six-turn natural-learning integration passed. Human profile discrimination, short speech and overlap remain unmeasured. |
| `intelligence/voice_learning.py` → `enrollment.py`, `state.py` | Original acoustic anchor, repeated compatible natural turns, 3/2s/8s starting thresholds, correction/expiry and archived originals. All writes belong to v3 state; no old interaction handlers or mouth/servo paths. Competing/known/suspect profiles cannot be silently overwritten. | Deterministic contamination/cancellation/failure checks and real-model synthetic enrollment passed. Live Bret/Jeff acceptance remains open. |
| `memory/voice_recordings.py`, `setup_assets.py` schema → `memory.py`, `profiles.py` | Original WAV/PCM provenance, atomic embedding/archive transactions, independent selected copies, model namespaces, preview-first re-embedding and deletion. Source SQLite is read-only and consistently snapshotted; facts are excluded. | Synthetic WAL import, source independence, rollback, namespace, correction, reset and managed-file deletion checks passed. No personal import or review was run. |
| `vision/camera.py::_FFmpegCapture` → `adapters/camera.py`, `faces.py` | AVFoundation unique device-name resolution, complete raw-frame reads, explicit bounded termination and no silent alternate device. V3 owns the process/worker and uses only face-presence boxes, coarse position and expiring tracks. No global frame singleton, automatic reconnection, appearance model or facial recognition was copied. | Selection/expiry tests and generated-frame detector timings passed. Camera was never opened; format/permission/room/load checks remain live work. |

SHA-256 of inspected source files:

- `audio/campplus.py`: `95c1a049b47ab66013d6b631e1fbec32d6716d9a0172d8fb6d54ec1ba40cc26d`
- `intelligence/voice_learning.py`: `903f5ab14ddc6438f3df2ab41460eb6e0eb133a5301b45af326aa57f2e3e6367`
- `memory/voice_recordings.py`: `dd4fed95a3d0f29e607a7b4c98e36a81c84589d6ef362419dbfa339de139ae62`
- `vision/camera.py`: `340c4acd5ec25764b01adb9d301a93a91d81feb1f90752bad12f6bf1ac435a8f`

The nonpersonal CAM++ file was independently copied and verified with SHA-256
`aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2`.
The new minimal camera detector uses the [official OpenCV 4.13 Haar asset](https://raw.githubusercontent.com/opencv/opencv/4.13.0/data/haarcascades/haarcascade_frontalface_default.xml),
SHA-256 `0f7d4527844eb514d4a4948e822da90fbb16a34a0bbbbc6adc6498747a5aafb0`,
with its embedded license retained. This avoids adding a resident facial-identity
model for a presence-only requirement. Blank-frame CPU cost was measured; actual
face quality and simultaneous camera/inference/playback performance were not.
OpenCV 5 removed the selected classifier API, so the tested adapter pins
[opencv-python 4.13.0.92](https://pypi.org/project/opencv-python/4.13.0.92/).
The available ffmpeg reports version 8.1.1; no capture validation is implied.

The subsequent car-session repairs use v3's own user-labeled recordings and active
adapters; no additional v2 code or personal data was extracted. The checkpoint,
feature namespace, providers, and named-enrollment thresholds remain unchanged.
Tentative track linking, playback-boundary endpointing, and within-capture change
proposals are v3 adaptations with results/limits in INITIAL_TEST_PROGRAM.md.
The per-turn silence operation uses the existing strict Responses function-call
contract and paired call/result history, following the [official function-calling
guide](https://developers.openai.com/api/docs/guides/function-calling).

The small semantic tool/state/claim core is new v3 code. Its Responses integration
was checked against [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
and [assistant phase handling](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4).
Intermediate commentary is not spoken, returned phases are preserved for tool
continuations, and final answers retain the streaming path. Real hosted-model
checks cover introductions, corrections, reference ambiguity and failed writes;
no old semantic handler ladder or secondary classifier was extracted.

### Face comparison preparation (September 10, 2026)

Source revision `9806c74fd233a7118da88ca5ae55fb7450deec05`, clean at inspection.
Inspected `config.py`, `vision/face.py`, `vision/pose.py`,
`vision/animal_detector.py` and the installed InsightFace `arcface_onnx.py` input
normalization. Reused only the five public model files enumerated by
`tools/prepare_visual_assets.py`; copied independently to
`local/models/visual-bench/`, with exact bytes, source paths and SHA-256 in its
`assets.json`. No personal profiles, images, v2 globals or fallback chains copied.

The offline recognizer harness preserves explicit RGB/NCHW 112 input normalization,
CPU ONNX execution and unit embeddings, using v3's existing OpenCV/NumPy/ONNX Runtime.
Generated-pixel inference checks lifecycle/resource reporting, not accuracy or
preprocessing parity on faces. Contract tests verify copy independence, integrity,
dataset split separation, wrong-name and abstention accounting. Face-model parity,
real-image discrimination, detector/alignment, M2 memory and combined visual load
remain outstanding. RF-DETR is a future comparison reference, not a v3 selection;
the user reports frequent errors. See [face plan](docs/FACE_ATTRIBUTION_PLAN.md).

The saved-video diagnostic inspects the installed v2 InsightFace SCRFD decoder
(strides 8/16/32, two anchors, box/keypoint distances, RGB normalization and NMS)
as an algorithm reference. V2 remained clean at
`9806c74fd233a7118da88ca5ae55fb7450deec05`. The new file-only helper owns its own
ONNX session and bounded frame sampling, loads only the independently copied v3
model, and does not import v2. Static least-squares similarity alignment is checked
with synthetic identity/scale/translation inputs. Real extracted crops were visually
reviewed. Exact native-reference parity and robot validation remain outstanding;
no camera, audio playback, microphone, or personal database was operated.


The authorized live-face implementation promotes this v3 SCRFD decoder/alignment
into `src/r3x/adapters/face.py`; the offline tool imports that same implementation.
EdgeFace-S uses the previously downloaded pinned challenger, not v2 runtime code.
The presence-only Haar path is retired. Public SCRFD/EdgeFace files were copied
independently from v3's comparison cache to `local/models/faces/`, with provenance
and digest verification; no additional v2 extraction or personal-data import occurred.
Focused state/tracking/storage checks and real saved-media results are recorded in
[face implementation evidence](docs/FACE_ATTRIBUTION_PLAN.md#implementation-evidence-and-remaining-acceptance).
Camera/robot hardware and M2 combined-load validation remain outstanding.

### Proactive greeting behavior preserved in v3 (September 11, 2026)

At the user's request, stable visible arrivals can again initiate greetings and
name invitations. V3 uses its existing LLM conversation owner, history, face state
and output receipts instead of copying static v2 greeting handlers. No v2 source,
profile or model asset was extracted for this change. The face plan records the
151 blocked-I/O checks, three hosted text-only cases and outstanding live validation.

### Proactive behavior review (September 12, 2026)

Read-only review at `9806c74fd233a7118da88ca5ae55fb7450deec05`, clean working tree.
No source was extracted, no personal data imported, and no tests, providers or
hardware were run. This records design lessons, not a new runtime integration.
Effective live overrides were not inspected. The
[proactive core design](docs/PROACTIVE_CONVERSATION.md) incorporates the user's
goal of delight and mood improvement with sensitivity rather than energy mirroring.

| Current v2 source | Lesson for v3 |
| --- | --- |
| `intelligence/lean_brain.py::consider_initiating`, `_render_option` | One LLM can choose a cue, freeform contribution or PASS; retain that choice and concise grounded material |
| `intelligence/interaction.py::_collect_lean_cue_candidates` | Fixed ordering and a three-candidate default cap can hide relevant lower entries; make bounded opportunity selection and omissions inspectable |
| `intelligence/interaction.py::_maybe_lean_impulse` | Preserve bounded consults, stale-revision rejection and delivery outcomes; avoid rebuilding its timer ladder and compulsory engagement probes |
| `intelligence/interaction.py::_lean_impulse_addressee`, `_lean_impulse_person_present` | Recent/most-frequent speaker is not always the audience; any recent human speech must not establish a particular target's presence |
| `intelligence/user_energy.py::note_user_turn`, `_classify` | The current singleton discards person ID and interprets short replies through heuristics; do not transplant room-wide energy matching into group conversation |
| `intelligence/consciousness.py::_step_group_turn_taking`, `_step_overheard_chime_in` | Preserve optional topical inclusion and awareness that people may discuss Rex; let the shared model decide participation without quotas or random sentiment-based triggers |
| `intelligence/consciousness.py` presence, wave, room-change, held-object and reaction steps; `intelligence/idle_behaviors.py` | Preserve useful observations and character ideas, not independent speech authorities; some legacy purposes are suppressed by Lean and others use separate paths |
| `intelligence/interaction.py::_lean_celebration_cue`, `_lean_news_cue`; `awareness/current_events.py` | Current code includes celebration cues and cached general/interest news; retain discretion, provenance, dates and delivery-based deduplication when these capabilities are implemented |
| `awareness/situation.py`, `intelligence/action_governor.py` | Separate hard availability/evidence gates from social interpretation; one shared conversation/output path replaces competing reactive arbitration |

V3 behavior and source-preserving acceptance are specified in
[proactive acceptance](docs/INITIAL_TEST_PROGRAM.md#proactive-conversation-acceptance).
No v2 runtime dependency, new model, configuration or API contract was added.

The subsequent first proactive core slice uses v3's existing Session and face
arrival scheduler, generalized to typed opportunities. It adds no v2 extraction,
provider or model. V3-owned lull pacing, quiet/resume and delivery bookkeeping are
recorded in [the implementation plan](docs/PROACTIVE_IMPLEMENTATION.md); the only
new setting is the user's requested conversational lull timing. Live validation
and richer feedback/memory integrations remain pending.

## Completing proactive input sources (September 12, 2026)

Rechecked v2 `9806c74fd233a7118da88ca5ae55fb7450deec05`, working tree clean,
without changing files or reading personal databases. Relevant reference:
`awareness/current_events.py` (`stories`, `refresh_if_stale`, `pick_story`,
`mark_mentioned`). Preserve dated/fresh cached material, off-turn refresh and
marking only after actual delivery. Do not preserve the global startup/cache chain,
fixed ordered selection or the historical date-parsing heuristics.

V3's implementation is independent: bounded RSS/Open-Meteo adapters, explicit
lifecycle workers, a typed observation and the existing Session/model/output path.
Scene inputs reuse v3's own FrameHub; no v2 vision or hardware adapter was copied.
Dependencies: existing httpx for public retrieval, Python standard-library XML/PNG
handling, and the existing hosted conversation model for images. No new resident
model or v2 runtime dependency was added. The v3 user selected Sacramento weather
and top headlines; actuator control stays outside their proactive-only scope.

221 blocked-I/O checks and explicit public-source/synthetic hosted probes are
recorded in [observation-source evidence](docs/OBSERVATION_SOURCES.md). Real scene,
animal/gesture, group and M2 validation remain pending; no hardware was operated.

## Attention and motion design review (September 12, 2026)

Read-only V2 HEAD `9806c74fd233a7118da88ca5ae55fb7450deec05`; working tree clean on
recheck. No V2 code, settings, calibrations or personal data were extracted.

- `sequences/animations.py`: fixed named beats, gaze and wave-back helpers. Preserve
  expressive range; replace default fixed event choreography with contextual,
  target-aware composition. Random side selection alone does not remove repetition.
- `hardware/servos.py`: smooth listening trajectories and procedural speech motion.
  Preserve bounded/smooth movement and tracking baselines, not independent writers.
- `intelligence/performance_plan.py`, `performance_output.py` and the interaction
  post-line callback: preserve actual-audio-end timing and interruption checks.
- `intelligence/motion_controller.py`, `motion_sequence.py`, `motion_agency.py`:
  preserve correlated completion, cancellation, target loss and manual ownership
  when physical integration is separately implemented.

V2 depends on Maestro/servo calibration, ESP32 serial/firmware for the base,
perception, shared state and speech callbacks. None becomes a v3 runtime dependency.
The first v3 adaptation is an independent in-memory attention contract and simulated
output adapter; see [implementation and evidence](docs/ATTENTION_IMPLEMENTATION.md).
Its 12 new synthetic checks do not validate V2 hardware or live motion. Physical
calibration, perception after movement, combined load and social quality remain open.

## Rich visual descriptions and repeated hair remarks (September 12, 2026)

V2 HEAD rechecked at `9806c74fd233a7118da88ca5ae55fb7450deec05`, clean working tree.
Read-only design review; no runtime extraction, provider calls or private data reads.

- `vision/face.py:update_appearance` contains hosted structured appearance extraction
  including hair/style/features and apparent age, with persistence into V2 facts.
  The function exists; this review did not establish its current live invocation
  frequency. Preserve descriptive richness, not direct promotion into v3 biography.
- `vision/scene.py:analyze_directed_attention` supplies the user's question/direction
  after a look and defers competing periodic work. Preserve question-aware visual
  interpretation and avoiding duplicate uploads.
- `intelligence/interaction.py:_lean_visual_riff_cue` documents the September 3
  "Brown hair again, Bret" failure and calls `_pick_appearance_hint(...,
  include_hair=False)`. Preserve the regression case; replace trait-specific
  exclusion with source-linked observation/actual-remark context and evaluated
  conversational judgment.

Dependencies include V2 OpenAI image requests, JPEG encoding, shared perception,
identity and facts storage. No dependencies or data were copied. V3's planned
adaptation and acceptance cases are in the
[rich-vision design](docs/ATTENTION_IMPLEMENTATION.md#rich-visual-understanding-requested-design-addition).
No new runtime capability or validation score is claimed by this documentation change.

## Robot local setup (September 17, 2026)

At the user's request, prepared the fresh v3 checkout on the base M2 / 16 GB
Mac mini. V2 source was clean at
`488780557ad70925e3b3c23fb542f544ed51a5b7`. The existing setup importer read selected
device settings and literal credentials/voice selection without executing v2 code,
and independently copied Qwen ASR files with digest verification. No personal
profiles, memory or recordings were imported; v2 remains unchanged.

V2's ReSpeaker input/output selection was resolved against OS device enumeration;
its blank camera name was replaced with the actual enumerated camera name. The
private `.env` records those hardware names. Runtime consumes `local/settings.toml`
and `local/secrets.toml`; `.env` is not automatically loaded. The settings use v3's
existing `dev-mac` profile, also on this Mac mini; no robot motion profile was added.

Installed the repository's pinned build/live requirements in a new v3 `.venv`.
Independently copied only the pinned CAM++ and SCRFD public assets from v2 and
downloaded the pinned EdgeFace-S checkpoint with checksum verification. Private
source/file digests are recorded in `local/v2-setup-import.json` and
`local/models/robot-asset-copies.json`, with face preparation provenance alongside
the model files. No v2 environment or startup machinery is a runtime dependency.

Verification: 382 blocked-I/O synthetic checks passed; dependency consistency and
camera-enabled non-starting readiness passed. OS capability queries confirmed
16 kHz mono PCM16 input and 22.05 kHz mono PCM16 output on ReSpeaker. Enumeration
and format checks do not establish capture, playback or camera-frame quality.
The real local Silero, CAM++, Qwen and face models also loaded and closed together;
Silero rejected synthetic silence. That single startup probe peaked at 2,912 MiB
process RSS, without devices, hosted requests or sustained inference. It is not
combined-load acceptance or recognition evidence.
Live conversation, combined-load performance and the required physical-robot
enrollment microphone hold remain outstanding; no actuator adapter was added.

## September 17: object detector and photographic memory

Reference checkout clean at `488780557ad70925e3b3c23fb542f544ed51a5b7` before and
after inspection. Relevant paths: `vision/animal_detector.py`,
`perception/place_recognition.py`, `memory/room_model.py`. V2's active detector
selects RF-DETR Nano with an older MediaPipe fallback; its room-object store is
category keyed and its place recognizer compares enrolled image embeddings.

Preserved: pretrained RF-DETR Nano category/box behavior, RGB conversion and one
model serving animals and household objects. Used the public checkpoint only;
no V2 personal database or profile was copied. One-time export used V2's installed
RF-DETR 1.8.3/torch 2.12.0/ONNX 1.22.0 tooling and wrote exclusively into V3 local
storage. V2's code, environment and local data were not modified.

Adaptation: V3-owned ONNX CPU adapter, one thread, one shared detection pass every
1.5 s, third bounded FrameHub consumer, temporary spatial tracks, one conversation
owner, and a new file-backed named PNG reference gallery. No detector-framework
runtime dependency, fallback, independent speaker, room model, global V2 config or
category-keyed individual identity was transplanted. Live dependencies are already
present in the V3 lock. Export/digest, focused checks, camera samples, hosted
comparison failures/improvements and outstanding live acceptance are recorded in
[object/photo memory](docs/OBJECT_PHOTO_MEMORY.md).

## Supervisor and operator-console migration requirement — September 17

Read-only review of clean V2 `488780557ad70925e3b3c23fb542f544ed51a5b7`:
`rex_supervisor.py`, `tools/rex_servo_menubar.py`, `utils/single_instance.py`,
`utils/port_handoff.py`, `scripts/install_supervisor.sh`, and `docs/supervisor.md`.
Preserve useful off-program controls and clean handoff for both supervised and
manual launches. Adapt independently to the V3 runner/environment/configuration;
inventory battery/LED companion agents and plan an explicit LaunchAgent cutover.
The active supervisor launches headless despite older GUI wording in its document.
No code/dependencies were extracted and no installed services or hardware were
changed. [Requirements and future acceptance](docs/OPERATOR_TOOLS_MIGRATION.md)
are a prerequisite for physical hardware integration, not an implemented feature.

## September 25: named throttle poses and explicit eye blink extraction

Source HEAD `663fee5ef4dd050b56b5ffda1e8e529284171c37`; inspected working tree,
not a clean-HEAD assumption. `sequences/throttle_arm.py` and
`hardware/throttle_motion.py` are modified. SHA-256 respectively:
`5de60a4fffae3e097d0b60547ab1ea33590bff93a4a82548a898bbc4db3e075b`,
`dac2c35a31bc2589cbc87d5e76e270b6f410a1c1aaf890a60273116be8a6bfa0`.
`arduino/head_nano/head_nano.ino` is unmodified, SHA-256
`72a946e432a475c1ffbd15f28d7036f555fe42367068001b7da394972cae3aa6`.
Other existing dirty V2 files include config, supervisor/handoff, throttle tests
and documentation; none changed for this extraction.

Preserve offer (2176/2601/5938), high-five (2176/9361/6302), rest and full-down
calibration, plus existing eye-color commands. Adapt to V3's semantic proposal,
output reservation and sole servo/LED owners. Named holds suppress only affected
speaking lanes; release restores ordinary motion. Clearance uses validated bridges
rather than the V2 coordinated direct high-five exception. Blink is three bounded
host eye-color cycles with cancellation/shutdown precedence. No V2 runtime import,
new dependency, firmware flash, copied interaction router or global state.

Focused contracts and full 573-check blocked-I/O suite cover route combinations,
held poses, release, rejected/cancelled/failed movement, truthful acknowledgment,
three blinks and shutdown, and the related motion-vs-echo capture fix. All evidence
is synthetic; live mechanical clearance, blink rendering, model tool choice and
microphone/ASR accuracy remain to be commissioned.

## September 25: throttle idle follow-up

Source remains HEAD `663fee5ef4dd050b56b5ffda1e8e529284171c37` with modified
`sequences/throttle_arm.py` and `config.py`. Inspected working-tree SHA-256:
`5de60a4fffae3e097d0b60547ab1ea33590bff93a4a82548a898bbc4db3e075b`
and `38853dc4b15a9aa4f1cf1f874ca54c97bde85057ce0c71acc49c8ba95e53a9c8`.
No V2 files were changed. Preserve five idle poses (including rest), non-repeating
selection, 2.3–3.3-second target duration and idle speed/acceleration caps
14/32/36 and 3/6/7, scaled by the already extracted throttle pace.

V3 adds a throttle lane to its existing idle scheduler rather than a separate
controller thread: completed motion is followed by its shared 4–9-second quiet
delay. Full three-joint batches and clearance checks remain in the sole owner;
held throttle poses exclude this lane until release. No new dependency/settings.
Synthetic contracts cover varying poses, pulse limits, held-arm exclusion, release,
speech preemption and park. Live motion feel/clearance and idle cadence remain
unverified for this change; 588 blocked-I/O checks pass including spoken exit.

## September 28: requested wave

Source HEAD `fb96f086925841479952c649b0a39a8f6c77f70c`; `sequences/animations.py` and `config.py` are clean.
Inspected `sequences/animations.py` SHA-256: `35bd2751ca9ec4a5f6ecb0d1b0a1a32445aca87a57d985c2bca72e4c1b3f8653`. No V2 files were changed.
Preserve `wave_back_gesture`: hero arm to 4800 with elbow 6300 and wrist 6000 at raise
speed/acceleration 55/14, then four full-travel wrist sweeps at the auto speed for a
0.32-second swing (`WAVE_BACK_WRIST_HALF_PERIOD_SECS`), then return to the prior pose.

V3 runs it as a one-shot `wave` step of `set_robot_pose` inside the sole servo owner,
using v3's calibrated wrist limits and V2's unlimited (0) sweep acceleration; nothing is
held afterwards. Adaptation: the elbow bob is omitted (one speed per beat). No camera wave detection, sound effect, cooldowns or settings
were copied. Synthetic contracts cover the sweep, return and no hold; live speed,
amplitude and clearance remain unverified. 753 blocked-I/O checks pass.

## Cross-session conversation memory — September 30, 2026

Reviewed V2 HEAD `fb96f086925841479952c649b0a39a8f6c77f70c`, clean working tree.
Design adaptation, no copied code or imported personal data. Sources:
`memory/conversations.py`, `memory/recall.py`, `memory/retrieval.py`,
`memory/episodes.py`, `memory/episodic_recall.py`, and their active wiring in
`intelligence/lean_brain.py`, `intelligence/llm.py`, `intelligence/interaction.py`.

Preserved behavior: persistent original conversation evidence, prior-session
recall, bounded personal context, optional shared-experience summaries and source
retrieval. Adapted to V3's one conversation/state/output owner, stable IDs,
explicit-offset time bounds, completed-phrase receipts and conservative audience
filtering. Rejected V2's regex semantic routes, global session caches, unspecific
day-wide participant lookup, last-day-as-last-session assumption, pre-delivery
callback consumption and old blanket prohibition on interest-based greetings.

Dependencies: existing SQLite, datetime and V3 providers only. No Ollama embedding
service or new resident model. V2's documented isolated runner passed 87 focused
checks (memory_recall, memory_unified_retrieval, episodic_recall,
lean_episodic_callback, memory_hardening); synthetic/temp stores only. V3 coverage
and remaining live validation are in `docs/CONVERSATION_MEMORY.md`. No V2 file or
personal database was modified.
