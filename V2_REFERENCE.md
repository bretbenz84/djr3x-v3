# V2 reference and extraction map

## Voice impressions extraction — October 6, 2026

Owner request (port the voice-cloning impressions, including "impersonate me", as an
LLM tool). Source V2 HEAD `fb96f086925841479952c649b0a39a8f6c77f70c`; working tree had
uncommitted firmware/battery edits unrelated to these files. Read, not copied:
`features/impersonation.py`, `features/organic_impersonation.py`, `audio/local_tts.py`,
`audio/breeze_ref_cache.py`, `docs/local_tts_impersonation_plan.md`,
`docs/local_tts_backends.md`, `docs/presidential_voice_refs.md` and the impersonation tests.

Preserved behavior: Breeze TTS 2 8-bit (`mlx-community/Breeze-TTS-2-mlx-8bit` at
`c6e4a2ff`, mlx-audio 0.5.1), reference audio passed by file path (an array is not
resampled), voice `S0`, `cfg_scale` 1.0, temperature 0.7, repetition penalty 1.2, the
750-token cap and the word-count duration bound, one complete take prepared before
playback, no abort on underruns, no caching of generated speech, the 45-word script cap
at sentence boundaries, intro/outro framing in Rex's own voice, the thinking filler for
requested bits but not cameos, the cameo budget (ten minutes between cameos, an hour per
voice, four per run), reference building from accepted recordings (two voiced seconds
per clip, six total, twenty seconds at most, 0.3 s gaps, 0.5 s tail), silence trimming,
and the per-chunk MLX lock discipline. Copied with adaptation: `audio/breeze_fast_depth.py`
→ `src/r3x/adapters/breeze_fast_depth.py` (class patch guarded by a V3 marker). The
reference-prefix cache was not ported (little benefit for occasional, varied voices).

V3 adaptation: the conversation model writes the parody inside the `impersonate` call
(no separate script LLM call, no regex target parsing, no roster scan of utterances for
cameos; the model decides from `impersonation` context); the state owner validates
offered targets, budget and source trust and resolves references; the one output owner
plays intro, cloned take and outro with the usual lights, talking motion and echo gate;
references for people come from V3's own store (artifacts removed on forget) and for
strangers from their current track's clean samples (session-scoped); the engine loads on
first use and unloads after ten idle minutes; a shared MLX lock now also covers the
Qwen3-ASR adapter. Dropped: V2's capture flow and pending slots, `--local-tts` mode,
ElevenLabs fallback, episode/decision-ledger writes, phrase-cycler state files, and all
`IMPERSONATION_*`/`BREEZE_*` configuration (one `impersonation_enabled` setting remains).

Assets: `assets/voices/famous` (26 clips plus transcripts, regular files only) and the
14 `assets/audio/sound_effects/thinking/*.mp3` chirps (V2 `77b2008`) converted to
22,050 Hz mono WAV under `assets/audio/effects/`. The model snapshot was copied from V2's
`assets/models/breeze_tts/8bit` into ignored `local/models/breeze-tts-8bit` by
`tools/prepare_voice_cloning.py`, which hashed the weights and wrote provenance. V2's
Rex clone reference (`assets/voices/rex`) and the one captured person clip under
`assets/voices/people` were not copied. Checks and remaining live validation:
[voice impressions](docs/IMPERSONATION.md#verification).

October 23 amendment (owner request: impressions of his own; no further V2 code read). Two
preserved behaviours changed: a cameo now waits for its take with the thinking filler like a
request (live takes rendered in 13-31 s, a long silence after his hand-off line), and the
same-voice hour, which the reply path never checked, is enforced. Impression beats are new V3
behaviour: the planner plans them, the take is rendered before Rex speaks, and they share the
cameo budget.

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
the canned-line PCM cache live in ignored `local/cache/`. October 10: like V2's
`tts.ensure_cached` before the clips, the chosen line is fetched while the whir and
wake play; unlike V2, every boot and ready line not yet cached is synthesized once in
the background after startup (paced, paused while Rex speaks), so later launches read
all of them from disk. Spoken intro clips are copied
but unplayed (V2 default `PLAY_STARTUP_SPEECH_CLIP = False`). October 10: the shutdown
clip (`SHUTDOWN_AUDIO_FILE`, started by V2 `_shutdown` alongside the LED fade and servo
droop) plays from the servo owner's park hook, after the LED fade command and before
the park sequence, through the one speech sink; playback closes only after it finishes.
Checked with synthetic sinks and pulse ports; no live playback was run for either change.

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

## Charger release correction — October 1, 2026

Inspected V2 HEAD `fb96f086925841479952c649b0a39a8f6c77f70c`, clean working tree:
`firmware/djr3x_motion/{battery.cpp,calib.h,gamepad.cpp,gamepad_unplug.h,protocol.h}`,
firmware README, `intelligence/motion_controller.py`, and `tests/test_gamepad_unplug.py`.
History: `bcc978d` removed the surface-charge hold floor; `5f8d361` restored a
90-second automatic discharge guard after false unplug under startup load;
`34ae195` removed extra host delay following firmware release; `53cfbab` added the
two-second gamepad unplug declaration and fresh-sample assertion checks.

Adaptation: correct V3's voltage backstop from 13.6 to 14.0 V, preserving the firmware
latch, immediate host release on fresh clear telemetry, and high-voltage blocking.
No new dependency, V2 edit, firmware change or V2 private-data access. Existing V3
battery diagnostics were read narrowly for charger/gamepad/controller evidence. Host regression
checks simulate surface charge through refusal/release/execution and charger-voltage
refusal. V2's `venv/bin/python tools/run_lean_checks.py gamepad_unplug` passes its
compiled fake-sensor/NVS test. Installed firmware version and real unplug latency
were not measured. See [charger investigation](docs/DRIVE_BASE_INTEGRATION.md#charger-release-investigation--october-1-2026).

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
| Impersonations | [features/impersonation.py](/Users/bbenziger/djr3x-v2/features/impersonation.py), [impersonation plan](/Users/bbenziger/djr3x-v2/docs/local_tts_impersonation_plan.md) | Extracted October 6, 2026 (see the entry at the top of this file). Reuse eligible natural recordings, match transcript to reference audio, and keep generated parody apart from biography |
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

### Return greetings across restarts (October 14, 2026)

Behavior preserved: V2 `intelligence/greeting_cadence.py` (revision 4887805, clean apart from
untracked `samples/`) read the persisted `people.last_greeted_at` so a reboot did not re-run
the full hello ("you're back", no second how-are-you). No code copied: its buckets and
wellbeing-ask regex stay in V2. V3 gives the model its last heard arrival greetings with the
gap in words (`conversation_memory.greetings`) and the model chooses the wording. One
synthetic check; live variety not yet observed.

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

## Owner-directed firmware change — October 1, 2026

The user explicitly authorized modifying the drive firmware's automatic charging
release after supplying current measurements (plugged ~0.01 A; unplugged/asleep
1-3 A). V2 source HEAD remains `fb96f086925841479952c649b0a39a8f6c77f70c`, clean
before this scoped change. This is a direct firmware edit, not a runtime extraction:
`battery.cpp`, `calib.h`, `protocol.h`, firmware README and its existing native test.
No V2 application or personal data changed. Release now uses three fresh samples
of >=500 mA discharge, with valid sub-14 V voltage; fresh entry/reset of EMA prevents
stale history. Version 0.2.4; no added dependencies. V2 isolated lean runner's
`gamepad_unplug` test passes with compiled real battery code and fake INA226/NVS.
Physical cable detection, live loaded-current validation and flashing remain absent.

Deployment follow-up: the owner explicitly requested flashing this build. Upload
hashes and live `0.2.4-current-unplug` hello verified on October 1; the battery
LaunchAgent was restored and its serial ownership confirmed. Stationary charging
telemetry remained locked. No movement/unlock command or physical unplug test.

## Companion behavior review — October 1, 2026

Read-only comparison at `fb96f086925841479952c649b0a39a8f6c77f70c`. The V2 tree has the
owner-authorized firmware README/battery/calibration/protocol and gamepad-unplug test
changes above; no conversation/motion source edits. Reviewed active
`intelligence/lean_brain.py::_IMPULSE_INSTRUCTION` and orientation/idle movement in
`intelligence/motion_agency.py`. Preserve following the actual conversational loose
end, avoiding default music topics, and bounded physical presence with current
clearance. Adapted these design lessons to V3's existing planner and conversation,
state, output, servo and drive owners; no V2 code, globals or data copied. No new
dependencies or resident models. Focused `test_agency_companion.py` checks use temporary
stores, fake pulse/ESP32 transports and blocked external I/O. Hosted fictional-context
evaluation and outstanding physical/social acceptance are recorded in implementation
status. V2 was neither changed nor run during this review.

## Face following and gaze breaks — October 3, 2026

Read-only reference at `fb96f086925841479952c649b0a39a8f6c77f70c`. The V2 tree still
carries only the owner-authorized firmware and gamepad-test edits listed above; the files
read here (`config.py` face-tracking settings, `intelligence/gaze_engine.py`,
`hardware/leds_head.py`, `arduino/head_nano/head_nano.ino`) are unmodified. Nothing was
imported, run or changed in V2.

- Behavior preserved: proportional face centring with a dead zone (centring gain 0.5,
  60 px dead zone), on yaw and pitch; breaks in eye contact to the side and slightly down,
  never up (side 15-25 degrees, down at most 5); a short hold when the face is lost before
  scanning; the asymmetric tilt scale (about 20 degrees down, 25 up from neutral).
- Adaptation: V2 ran a 12 Hz closed loop with its own servo writer and a duty-cycle state
  machine tied to conversation phases. V3 takes one partial step per idle phrase (every
  1-3 s) through the single servo owner, glances away every 7-16 s, frames the face 40%
  down the picture, and lets the conversation model switch between following people and
  holding a chosen direction. No V2 settings, calibration files or globals were copied;
  the constants are in `idle_motion.py` and `servo_poses.py`.
- Mouth start: V2's firmware comments record that the head board drops serial bytes while
  repainting; V3 now repeats `SPEAK:` (see docs/LED_INTEGRATION.md).
- Dependencies: none added. Checks: `tests/test_idle_motion.py`, `tests/test_leds.py`
  with fake pulses, clocks and serial. Remaining live validation: tracking feel and
  vertical aim on the robot, and the mouth animation on every line.


## Owner-directed front-right firmware disable — October 10, 2026

Direct edit, not an extraction: V2 HEAD `fb96f086925841479952c649b0a39a8f6c77f70c`.
The source tree already contained unrelated application edits and uncommitted
firmware battery/calibration, raw diagnostic and crosstalk changes (version 0.2.6).
These were preserved; the build used the current working tree, not HEAD alone.
Reviewed persistence commit `7cd43a7`, quarantine `639eb66`, radial/matrix split
`17de7a6` and current firmware/runbook. New scoped edits: `tof.cpp` skips mux 5
initialization and polling with an explicit DISABLED log; `protocol.h` identifies
0.2.7; firmware README documents missing coverage. Other sensors and dependencies
are preserved. No V2 application or personal database was changed by this task.

Full Bluepad32 build and owner-requested flash succeeded (retry after an initial
serial transfer failure). All four hardware flags enabled; 26 isolated sensor and
battery regressions passed. Live boot confirms gamepad support, seven active radial
sensors and ready matrix; 139 stationary frames keep `fr_radial=-1`. Motor duty
stayed zero. Battery menu restored; physical gamepad/floor-drive acceptance remains
open. See [deployment evidence](docs/DRIVE_BASE_INTEGRATION.md#front-right-radial-firmware-disable--october-10-2026).
Binary SHA-256: `406a0809ecac5218ffc8d0a6e8295b49da2819e35a596eb9658a653bebc482dc`.


## Owner-directed matrix floor calibration — October 10, 2026

Direct firmware edit from V2 HEAD `fb96f086925841479952c649b0a39a8f6c77f70c`
plus its existing working-tree edits and the earlier 0.2.7 radial disable. The owner
identified the saved bottom-row returns as floor. Added measured row thresholds
in `calib.h`, a pure `tof_matrix_floor.h` helper used by `tof_matrix.cpp`, version
0.2.8, native production geometry/aggregation checks and firmware documentation.
Existing motor/gamepad/battery/radial behavior and upper matrix rows preserved;
no new dependency. 26 isolated regressions passed; 53-frame saved host replay
removed 42 near-floor blocks. Full four-flag Bluepad32 build/upload hashes verified.
Live capture: all 57 matrix frames show clear host path at 3.5 m and calibrated
bottom thresholds 70/70; final front pair 3500/3500, zone CLEAR, radial still -1,
Bluepad32 ready. Battery menu restored; no drive/gamepad test. Floor-range objects
in those bottom rows are indistinguishable from floor, and live driving acceptance
remains open. [Evidence](docs/DRIVE_BASE_INTEGRATION.md#matrix-floor-calibration--october-10-2026).

## Searched daily news and interest news — October 11, 2026

Owner request: model V3's news on V2 while keeping the BBC feed. Source HEAD
`95f2403e78c9ef786054fa13a31bb2ac534cc29d`; its uncommitted changes are firmware only, and
`awareness/`, `memory/interests.py` and `intelligence/web_search.py` are clean. Read, not
copied: `awareness/current_events.py` (SHA-256 `dad071c9…`) and `memory/interests.py`
(`a8e629a7…`). No V2 data or cache was read.

Preserved: one hosted web search a day for about five concrete, varied stories (no
homepages or roundups, light items mixed in, at most one AI/tech story, no gossip about
minors or graphic tragedy); same-day restarts reuse the cache; a failed fetch keeps the
older stories, which still count as news for about a day; per-topic interest news, at most four topic searches a day,
shared between people with the same topic; each story offered once; the event's day stated
relative to today (V2 commit `2af39e3`).

Adapted: `src/r3x/news.py` plus the existing `PublicObservations` worker, with no new
thread or service. Stories become ordinary cues for `use_observation`, so the one
conversation owner chooses whether to speak and only completed audio records delivery.
V2 found event dates in the text with month-name regexes; V3 asks the search for a
structured `event_date` and computes the relative day in code. V2's AI rule was only a
prompt instruction (added July 18 after OpenAI launches dominated); V3 also asks for a
fixed `category` and keeps at most one `ai_tech` story in code. Stories whose link the
search never returned are dropped. Interests come from a new `remember_interest` tool:
a supported speaker's own self-report claim plus a search topic, so correction, retraction
and forgetting reuse the claim paths. Interest news is offered and revalidated only while
that person is recognized in view, and it is private evidence for the goal planner.

Checks: `tests/test_news.py` (14 synthetic checks) and a paid probe,
`tools/check_observation_sources.py --search`. Live conversation use remains unaccepted.

## Room recognition — October 11, 2026

Owner request: room exploration and mapping using V2's room models. Source HEAD
`95f2403e78c9ef786054fa13a31bb2ac534cc29d`; `perception/`, `memory/` and `data/places.db` were
not modified (only firmware files are dirty). Read, not copied: `perception/place_recognition.py`
(SHA-256 `38e5de0f…`), `perception/place_service.py` (`ef9bc867…`), `perception/place_embedder.py`
(`58933f4a…`); `memory/room_model.py` (`483ab1b0…`) and `intelligence/exploration.py` informed the scope only.

Preserved: MobileCLIP-S2 (`datacompdr`) image embeddings and V2's `mobileclip_s2_v1`
namespace; top-3 room scoring; 0.80 confident with a 0.04 margin; 0.86 for a lone room; 0.68
floor; 5-frame majority; motion freeze with the carried-robot flip (10) and lost belief (16);
unfamiliar after 8; 8-view enrollment with 35° or 3 s separation and a 60 s timeout (≥3 views
to keep); 0.88 look-alike check; refresh only for the clear top room within 0.70–0.78 when two
or more rooms exist; 15 views per room; a human denial outranks the score.

Adapted: ONNX export (`tools/export_place_model.py`, digest `bf9d116d…`) with numpy
preprocessing matching open_clip at cosine 1.0000 on 14 saved frames; plain-Python matching;
rooms, views and connections in the core store; naming and corrections through the state
owner's `name_room`/`correct_room` tools, interpreted by the conversation model instead of V2's
regex answer capture; an `explore` goal in the existing planner instead of V2's separate
exploration mode; face width instead of body pose for occlusion; slower sampling while settled.
Not ported: no-drive rooms and V2's per-object room novelty (not requested).

Data: `tools/import_v2_places.py` copied V2's three rooms (dining room, workshop, living room;
15 views each) from a read-only SQLite backup snapshot, SHA-256 `1808063d71eda96576103f12e6bff91417e7046ca99beef9c52bdde061d8126c`,
with new IDs, `import_ref` and the mapping in `imports`. Details and limits:
[room recognition](docs/ROOM_RECOGNITION.md).

## Humor style examples — October 14, 2026

Step 4 of the [improvement plan](docs/IMPROVEMENT_PLAN.md) (owner: V2 "joked more, roasted
people more", from hard-coded lines that repeated). Source HEAD
`95f2403e78c9ef786054fa13a31bb2ac534cc29d`; only firmware files and one firmware test are dirty,
no humor file. Read, not copied as code: `config.py` (`COMEDY_LINE_BANKS`, quip pools, the
September 6 persona and the older roast-first persona at `665e8dd^:config.py`),
`features/games.py` trivia quips, `intelligence/comedy_modes.py`, `intelligence/social_frame.py`.

Preserved: seven short lines as style examples in the persona's TEASING paragraph (unanswered
question, trivia right and wrong, a dubious idea, being laughed at, the dinner tease, a pilot
self-own), and V2's lesson that roasting was the default lens of its most fun period. Adapted:
no comedy-mode chooser, roast-level governor or line banks; the conversation model decides when
to tease, a joke record replaces V2's bit ledger and callback cooldowns, and a writer's room
supplies fresh material instead of pools. Not ported: the vision "roast me" prompt that roasted
appearance and build, and greeting-based speech styles (identity-based). No V2 personal data or
eval corpora (they name the owner and his health) were read into V3.

## Dashboard (PySide6 GUI) — October 14, 2026

Owner request: port V2's Python GUI (not a web GUI), with manual servo and drive control;
Memory Banks and typed input left for later. Source: V2 `488780557ad70925e3b3c23fb542f544ed51a5b7`.
`git status --short gui` was empty, so the working tree matched HEAD for `gui/`. SHA-256 of
the files adapted:

| File | SHA-256 |
| --- | --- |
| `gui/dashboard.py` | `01eac032d11754b30a426f75d5d3ee34563b6ac2a87bbb97e8345c31d5d82caf` |
| `gui/vision_panel.py` | `10533e02f99c55b12e3a64e3ba68cb70b000a87e3287f01198ddebe7720d4b2a` |
| `gui/conversation_panel.py` | `ee9b0a4d5252994144568fc752cb771f5b926aa4fb5744cfcb895ee7dbad1b0b` |
| `gui/log_panel.py` | `ea6171291c07b20e49dc764b938e172e5eb3aace7daa7067688e9bf084d78c17` |
| `gui/rex_avatar.py` | `1b48105699b1ea66d7dcff47052757a1fde16c7561561798c9ad3b594e332ee6` |
| `gui/rex_avatar_3d.py` | `60aa19e8c130da7138cbfe33d6330bd355f7fe3f9659f36eb38daad156326f1f` |
| `gui/avatar_rig.py` | `47a426a9f1c01cb75d436556411c0e730d27300b68d738de4893adc7116cda05` |
| `gui/mouth_leds.py` | `02e2c1c775bd6344e16147f69b23850b375602078f0d48f9d80e8424b0974ec0` |
| `gui/theme.py` | `f0c6408a355e250357c8cae2119222a9eb0d25868af9c53f622ff19bde49cbb3` (copied unchanged) |
| `gui/assets/rex/geometry.npz` | `8f551a982fa15b652a765906bc37251f45bf8e01d35694bdd9a681da38ea6cbc` (with rig.json, materials.json, Avatar.qml and both SVGs, unchanged) |

What was preserved:

- The layout, theme, camera panel and status chips.
- The read-along transcript and the colour-coded log console.
- The Qt Quick 3D rig, lighting, mouth diffuser, eye blink and chest palettes.
- The Motivator widgets, with arcade mixing and the ramp.
- The pattern of polling snapshots at 20 Hz on the Qt thread with Rex on a worker thread.

Adaptation:

- No `config`, singleton bridge or global pause flags. A Qt-free `DashboardFeed` is the
  trace display, reads owner snapshots, and sends buttons through the owners.
- The rig is driven by V3 calibrated pulses: the two-servo head pitch and tilt, and the
  inverted visor. V2's rig pitch sign was found reversed on rendered poses and corrected.
- Manual servo writes became the servo owner's `operator` mode. The joystick became
  `DriveBase` teleop under the plan lock, with arm park, path and swing checks.

Not ported:

- `state_bridge.py`, `memory_banks.py`, `jeopardy_panel.py` and `live_face_tracker.py`.
- The 2D painter avatar.
- `studio.hdr`, which V2's code no longer reads.
- Pause/Sleep, the pose skeleton and occlusion zones.

Checks and remaining live validation are in [dashboard](docs/GUI.md#evidence).

## Owner-directed rear matrix and front-right re-enable — October 19, 2026

Direct firmware edit, not an extraction: V2 HEAD `95f2403e78c9ef786054fa13a31bb2ac534cc29d`
plus its existing uncommitted firmware edits (0.2.6-0.2.8), preserved. The owner asked
for rear 8x8 support and, replacing the faulty module, the front-right radial back on,
then to compile and flash. Changed: `tof_matrix.cpp` (two modules, per-module state
machines on one task), `tof_matrix_floor.h` (pure geometry/aggregation per module),
`tof_matrix.h`, `tof.cpp` (rear overlay, channel 5 re-enabled), `calib.h` (rear
constants, crosstalk 0), `context.h`/`proto_io.*` (`rl_radial`/`rr_radial`,
`tofmx_rear`), `protocol.h` 0.2.9, README, and V2 tests `test_tof_matrix_floor.py`,
`test_tof_fr_xtalk.py`. No dependency added; no V2 application or data changed.
Evidence and outstanding live checks:
[drive integration](docs/DRIVE_BASE_INTEGRATION.md#rear-depth-matrix-and-front-right-replacement--october-19-2026).

## Owner-directed smooth starts and stops — October 23, 2026

Direct firmware edit, not an extraction: V2 HEAD `95f2403e78c9ef786054fa13a31bb2ac534cc29d`
plus its existing uncommitted firmware edits (0.2.4-0.2.9), preserved. The owner reported
jarring turns, harsh stops and false obstacle slams, and asked for a fix and a flash.
Changed: new pure `drive_shaping.h` (ramped breakaway boost, stop fade, wheel PID, reflex
deceleration); `hal.cpp` uses it and `hal_drive_velocity` takes a `stopping` flag; `control.cpp`
replaces the one-tick reflex cut with `reflex_ramp_step` and passes `stopping`; `calib.h` adds
boost and reflex constants and retires `WHEEL_LAUNCH_CONFIRM_S`/`WHEEL_RESTALL_CONFIRM_S`;
`hal.h`; `protocol.h` 0.2.10; README; new V2 test `test_drive_shaping.py`. No dependency
or runtime config key added; no V2 application or data changed. The source then moved
into v3 (next section). Evidence and outstanding live checks:
[drive integration](docs/DRIVE_BASE_INTEGRATION.md#smooth-starts-and-stops--october-23-2026-firmware-0210).

## Drive firmware moved into v3 — October 23, 2026

Owner request: the drive firmware and its native tests live in this repository, V2 stays
reference. Until now every firmware change since V2 `53cfbab` (September 25) existed only
as uncommitted edits in the V2 working tree.

Source: V2 HEAD `95f2403e78c9ef786054fa13a31bb2ac534cc29d` with uncommitted edits to
`README.md`, `battery.cpp`, `calib.h`, `context.h`, `control.cpp`, `hal.cpp`, `hal.h`,
`proto_io.*`, `protocol.h`, `tof.cpp`, `tof_fr_filter.h`, `tof_matrix.*` and untracked
`drive_shaping.h`, `tof_matrix_floor.h`, i.e. firmware 0.2.10 as flashed. All 36 files of
`firmware/djr3x_motion` copied unchanged to `firmware/djr3x_motion`; digest (SHA-256 of
the sorted per-file SHA-256 list) `82d4815400dba0ce710e5d27e3135fe4a06e6ef641540c37726a8fdbdca53adb`
on both sides. Afterwards only the README changed: a provenance note, V2 doc links pinned
to that revision on GitHub, and test paths.

Tests: `test_drive_shaping`, `test_gamepad_unplug` (V2 working-tree version),
`test_tof_fr_filter`, `test_tof_fr_health`, `test_tof_fr_xtalk`, `test_tof_matrix_floor`
and their two tracked sensor CSV fixtures moved to `firmware/tests` with paths rooted at
`firmware/`. They compile firmware with clang++, which `run_checks.py` forbids, so
`tools/run_firmware_checks.py` runs them: only clang++ and programs built under the temp
directory may start; network, devices, V2 and `local/` stay blocked.

Not copied: the radar ESP32 firmware (`firmware/djr3x_radar`), the bench sketch
`firmware/tof_matrix_test`, `firmware/tools/*` and V2's protocol/system docs (the
README links to them). V2's working tree keeps its uncommitted copy untouched.

Checks: 39 native checks pass from v3. The full four-flag Bluepad32 build from v3 is
814,457 bytes, 90,956 globals; against the flashed binary only the embedded ELF hash
(32 bytes at 0xb0) and the trailing image checksum and digest differ, both from the
sketch path recorded in the ELF, so the code on the board is this source. Remaining live
validation is unchanged (previous section).

## Pride mode and AAVE mode — October 25, 2026

Owner request: port V2's pride mode ("queeny mode") and its African American Vernacular English
mode ("homie mode"), each in an ElevenLabs voice of its own. Source HEAD
`95f2403e78c9ef786054fa13a31bb2ac534cc29d`; `intelligence/`, `sequences/`, `hardware/`, `config.py`
and `arduino/` clean (only firmware and one firmware test are dirty). Read, not copied as code:
`intelligence/pride.py` (SHA-256 `e81d18c4…`), `intelligence/homie.py` (`f2c482f8…`), the
`pride_flourish` in `sequences/animations.py` (`64c7dec8…`), `PRIDE_WRIST` in `sequences/throttle_arm.py`
(`c86d9c0b…`), the `PRIDE_SPEECH_*` talking motion in `hardware/servos.py` (`5266d445…`), the PRIDE
lease in `hardware/leds_head.py`/`leds_chest.py` and the `PRIDE:{0|1}` firmware commands in
`arduino/head_nano` (`72a946e4…`) and `arduino/chest_nano` (`1619abfe…`), already on the boards.

Preserved: the triggers (asked about his own sexuality; greeted "what's up my homie"), a proud yes
and a warm greeting back on the same turn, ten minutes renewed by asking or greeting again, V2's
full-volume directives (minus "the real question still gets asked", which V3's
reply voice forbids) with AAVE's one guard against mocking parody; for pride the rainbow mouth and
chest renewed inside the firmware's 10 s lease, the one-shot flourish (head fully down, visor wide,
hero hand at the end of its travel, held 1 s, back), hero elbow/hand talking swings about twice as
wide and often, and the throttle wrist curl (9016, inside the raised clearance box).

Adapted: the reply model calls a `voice_mode` tool (on/off, with its line spoken after the switch,
one model call) instead of V2's regex arming; the state owner keeps the modes and the reply request
carries a mode's directive last; "stand down pride mode" is the same tool, not a command parser
entry. The flourish is a with-speech gesture through the output owner, eased on the talking pace;
the lights, talking motion and voice read the modes through callables. New: the owner's voices
(`VOICES` in `src/r3x/voice_modes.py`, with the accents added the same day), and
sentry mode clears both modes and refuses them while it guards. Changed: V2 let both be on at
once; the owner asked the same evening for one mode at a time. Not ported: V2's enable switches
and timing settings (no current user choice), the emoji filter (not seen in V3 output yet).

Checks: `tests/test_voice_modes.py`, synthetic providers, pulse and serial ports. Remaining live
validation: how the lines sound in both voices, the flourish and hand swings on the robot, the
rainbow on both boards, and whether the model calls the tool for real questions and greetings
and not for questions about other people.
