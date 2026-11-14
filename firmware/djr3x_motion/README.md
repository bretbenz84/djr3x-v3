# DJ-R3X motion controller — ESP32 firmware (Phase 0)

> **Home: djr3x-v3.** This is the authoritative source, copied October 23, 2026 from the
> V2 working tree (V2 HEAD `95f2403`, firmware 0.2.10 as flashed); edit, test and flash
> from here. V2's copy is reference only. Host code, tools and docs named below
> (`intelligence/…`, `tools/…`, `firmware/tools/…`, `docs/…`) are V2 files unless
> noted. Native checks: `.venv/bin/python tools/run_firmware_checks.py` (sources in
> `firmware/tests`). Provenance: V3 `V2_REFERENCE.md`.

Real-time motion controller for DJ-R3X's drive base. It implements the full
Mac↔ESP32 wire protocol ([docs/motion_protocol.md](https://github.com/bretbenz84/djr3x-v2/blob/95f2403e78c9ef786054fa13a31bb2ac534cc29d/docs/motion_protocol.md)
v1) and has **two build modes** (see "Build modes" below):

- **Stub (`MOTION_HW_PRESENT=0`, the repo default)** — the original **Phase 0
  bring-up** build: a stubbed hardware layer, so it runs and is fully testable on a
  bare ESP32 with **nothing wired**. The plant model synthesizes odometry; ToF reads
  "clear".
- **Live (`-DMOTION_HW_PRESENT=1`)** — the **Phase 1 real drive base**: BTS7960 PWM,
  Hall quadrature encoders (PCNT/ESP32Encoder), per-wheel velocity PID, and odometry
  integrated from encoder deltas. **ToF defaults to a safe "clear" stub** here, so
  **obstacle avoidance is inactive** — add `-DMOTION_TOF_PRESENT=1` once the 8 ToF
  sensors (4× VL53L0X + 4× VL53L1X) are wired (driver in `tof.cpp`; TCA9548A mux
  addressing — see "Build modes"). Pins live in `pins.h`, measured/tuned constants in
  `calib.h` (the geometry values there are **placeholders — measure them on the real
  base**, docs §14).

- The plant model in `control.cpp` synthesizes odometry from commanded velocity,
  so `turn`/`move` actually run to completion and emit `done`.
- `hal.cpp` returns a "clear room" for the ToF sensors, so the reflex/zone logic
  stays green.
- Everything above the HAL (protocol, control, safety, state machine, watchdog,
  deadman) is the real thing and is what we validate here.

As hardware is wired, flip `MOTION_HW_PRESENT` to `1` in [`hal.h`](hal.h) and fill
the marked driver sections — nothing above the HAL changes.

## Board

Classic ESP32-WROOM-32 (Elegoo DevKit, CP2102 USB bridge). FQBN `esp32:esp32:esp32`.
The serial-port path varies per machine — find it with `arduino-cli board list` (it is
the same path you set as `MOTION_ESP32_PORT` in `.env`). The commands below use `$PORT`:

```bash
export PORT=/dev/cu.usbserial-XXXX   # YOUR board's port — see `arduino-cli board list`
```

## Toolchain (already installed on this machine)

```bash
arduino-cli core install esp32:esp32      # Espressif core (3.3.10 on this machine)
arduino-cli lib install ArduinoJson       # JSON (7.4.3)
arduino-cli lib install ESP32Encoder      # Hall quadrature decode (0.12.0) — live build only
arduino-cli lib install VL53L0X           # Pololu short-range ToF (1.3.1) — ToF build (-DMOTION_TOF_PRESENT=1)
arduino-cli lib install VL53L1X           # Pololu long-range ToF (1.3.1)  — ToF build (-DMOTION_TOF_PRESENT=1)
```

> **Core version (2.x and 3.x both supported).** `setup_macos.sh` installs
> `esp32:esp32` **unpinned**, so a machine gets whatever core is current — older installs
> may be on the legacy **2.x** core, newer ones on **3.x** (e.g. 3.3.10). The live HAL
> builds on **both**: the LEDC API moved from channel-based (2.x: `ledcSetup` /
> `ledcAttachPin`) to pin-based (3.x: `ledcAttach`), and `hal.cpp` picks the right one
> via an `ESP_ARDUINO_VERSION` guard. `ESP32Encoder` 0.12.0 covers the matching PCNT
> change. The `setup_macos.sh` toolchain step installs all three deps automatically.

## Build / upload / monitor

```bash
# Compile (from repo root)
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/djr3x_motion

# Flash the connected board
arduino-cli upload  --fqbn esp32:esp32:esp32 -p "$PORT" firmware/djr3x_motion

# Watch the raw NDJSON telemetry stream (Ctrl-A k to quit `screen`)
arduino-cli monitor -p "$PORT" -c baudrate=115200
```

## Build modes

`MOTION_HW_PRESENT` is `#ifndef`-guarded in [`hal.h`](hal.h) and defaults to `0`,
so a plain build (and the smoke test, and `tests/`) stays bare-board. Select the
live drivers with a build-property override — no source edit:

```bash
# Stub / bare-board (default)
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/djr3x_motion

# Live Phase-1 drive base
arduino-cli compile --fqbn esp32:esp32:esp32 \
  --build-property "compiler.cpp.extra_flags=-DMOTION_HW_PRESENT=1" firmware/djr3x_motion

# Flash live (115200 — 921600 is unreliable on this USB bridge)
arduino-cli upload --fqbn esp32:esp32:esp32:UploadSpeed=115200 \
  --build-property "compiler.cpp.extra_flags=-DMOTION_HW_PRESENT=1" \
  -p "$PORT" firmware/djr3x_motion
```

The live build boots to **idle with the motors disabled** — nothing moves until an
explicit command energizes them.

### ToF obstacle avoidance (sensors wired)

Off by default even in the live build (`hal_read_tof` reports a clear room). Enable it
once the 8 ToF sensors are on the I²C mux — combine the flags. The layout (rev 2) is
**8 radial sensors** at the 540 mm base-ring surface, every 45° starting 22.5° off the
forward axis: 4 short-range **VL53L0X** on mux ch 0-3 as the LEFT/RIGHT pairs
(`lf,lb,rf,rb` — lateral clearance for the hallway steering assist) + 4 long-range
**VL53L1X** on mux ch 4-7 as the FRONT/REAR pairs (`fl,fr,rl,rr`, ±22.5° off each axis —
stop reflex + room sense). All stay at 0x29; the TCA9548A mux selects one channel at a
time (zero XSHUT GPIOs). There is **no down/cliff sensor** in this layout, so cliff/
drop-off detection is unavailable. While driving FORWARD, the firmware auto-steers away
from walls / centers in a hallway (`assist_*` config params, docs §6.4). This applies to
gamepad drive and Mac `drive`/`move` commands; reverse movement and pure turns
are not auto-centered. The operator's stick adds on top in manual mode, and
B/e-stop plus the stop reflex always win. Front avoidance starts at the larger
of 900 mm and the configured assist range; its contribution is capped at 0.25 rad/s.

Since firmware 0.2.0, **the Mac owns come-here semantics**. There is no firmware
`come` command, social stopping distance, or caller-heading restoration. The Mac
sends ordinary `turn` and refreshed `drive` commands; the ESP32 handles motor
control, acceleration, obstacle avoidance, gamepad priority, and the watchdog.
A host update is required with this firmware. See `intelligence/approach.py` for
target tracking, estimated camera range, arrival confirmation, and loss limits.

Finite turns use the LSM6DS3 gyro yaw as the physical completion signal when the IMU is
healthy, so spinning encoders cannot falsely claim a completed turn when the wheels slip.
Encoder odometry remains the fallback if the IMU was unavailable when the turn began.
An IMU-verified turn that fails to make enough physical yaw before its bounded timeout is
aborted rather than grinding indefinitely. After motor current settles, the Mac can also
check the completed turn against its fused compass heading and issue one small correction;
that second check requires `COMPASS_ENABLED=1` and a valid in-situ calibration file.

```bash
arduino-cli compile --fqbn esp32:esp32:esp32 \
  --build-property "compiler.cpp.extra_flags=-DMOTION_HW_PRESENT=1 -DMOTION_TOF_PRESENT=1" \
  firmware/djr3x_motion
```

The mux is **required** for this layout (8 sensors exceed the ESP32's free XSHUT GPIOs;
the `-DMOTION_TOF_USE_MUX=0` path `#error`s). `tof.cpp` is still a **scaffold** — not yet
fully hardware-validated; bench-check the channel→field order and timing budgets (docs §6).
Bring-up emits one `[motion_fw] tof[…]` log per sensor (OK/FAIL) + an `N/8 up` tally.

### QMC5883L compass (GY-271) — raw axes in telemetry

A QMC5883L magnetometer (NOT the HMC — different register map; the GY-271 boards
ship either) joins the main I²C trunk at fixed address **0x0D** (no conflicts:
INA226 0x40, MPU 0x68, mux 0x70, BMP/BME 0x76). The firmware only probes,
configures ±8 G continuous mode, and publishes RAW counts at 10 Hz in the `mag`
telemetry block (`{ok,x,y,z[,ovl]}`); calibration, tilt compensation, and the
current-gated heading fusion live on the Mac (`hardware/compass.py`,
`tools/compass_calibrate.py` — run the figure-8 calibration IN-SITU on the
robot). Absent sensor = one boot log line + `{ok:false}`, like the IMU/env.

### Front 8x8 Matrix ToF (DFRobot SEN0628) — `-DMOTION_TOF_MATRIX_PRESENT=1`

A single VL53L7CX 8x8 matrix module (onboard RP2040, Gravity 4-pin I²C) mounted at the
**direct front of the base, pointed level**, gives the front stop/slow reflex a real
64-zone view — chair legs, seat edges, and low clutter that a single-point sensor
misses. Independent of the radial array (`MOTION_TOF_PRESENT`): the matrix's
floor-rejected left/right-half obstacle distances **override `fl`/`fr`** (or
min-combine when the radial array is also built in). Rear/side coverage still needs
the radial array — **reversing is unprotected** with only the matrix wired.

**Speckle rejection (2026-08-01):** each half publishes its *second*-nearest
qualifying zone (`TOF_MATRIX_MIN_OBSTACLE_ZONES`, calib.h). A lone phantom zone —
VL53L7CX speckle that pinned a parked base BLOCKED with >1 m genuinely clear —
reads as clear; any real obstacle close enough to matter lights multiple zones, so
second ≈ nearest. Trade: an object thin enough to hit exactly one zone is invisible.
Paired with `FINITE_BLOCK_GRACE_MS` (control.cpp): a finite move pauses through a
transient block and resumes, only giving up `done:blocked` after 900 ms of
continuous block.

```bash
# Live drive base + gamepad + front matrix — THE robot build with this sensor wired.
# ⚠ FQBN MUST be the Bluepad32 core. Field lesson 2026-07-16: flashing this firmware
# with plain esp32:esp32 (core 3.3.10) froze core 1 at boot — its rewritten IDF
# i2c_master driver hard-hangs on this trunk's transactions (and drops the gamepad).
# The Bluepad32 core's legacy I2C driver is the one this firmware is stable on.
# ⚠ Keep -DMOTION_TOF_PRESENT=1 in the list: without it the radial 8-sensor
# array is silently stubbed (rear/side ToF read "all clear" — reversing loses
# its protection). The matrix flag only covers the front.
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32:UploadSpeed=115200 \
  --build-property "compiler.cpp.extra_flags=-DMOTION_HW_PRESENT=1 -DMOTION_TOF_PRESENT=1 -DMOTION_GAMEPAD_PRESENT=1 -DMOTION_TOF_MATRIX_PRESENT=1" \
  --upload -p "$PORT" firmware/djr3x_motion
```

#### Safe full-robot flash runbook

The battery menu bar LaunchAgent normally owns the same ESP32 serial port while
`main.py` is stopped. **Boot it out before every flash.** If it is left running,
both processes read/write the port and `esptool` fails partway through with errors
such as `Invalid head of packet` or `The chip stopped responding`. A partial flash
must be retried before the ESP32 can boot normally.

The LSM6DS3 accelerometer/gyro and QMC5883L/P compass need **no compile flags**:
their drivers are always built, probed at boot, and reported as `imu` and `mag` in
telemetry. The four flags below enable the motors, Bluepad32 gamepad, eight radial
ToF sensors, and front matrix ToF respectively.

```bash
export PORT=/dev/cu.usbserial-110

# 1. Release the ESP32 serial port.
launchctl bootout gui/$(id -u)/com.djr3x.battery

# 2. Compile AND upload the exact full hardware variant. Keep UploadSpeed=115200.
arduino-cli compile \
  --fqbn esp32-bluepad32:esp32:esp32:UploadSpeed=115200 \
  --build-property "compiler.cpp.extra_flags=-DMOTION_HW_PRESENT=1 -DMOTION_GAMEPAD_PRESENT=1 -DMOTION_TOF_PRESENT=1 -DMOTION_TOF_MATRIX_PRESENT=1" \
  --upload -p "$PORT" firmware/djr3x_motion

# 3. Restore the battery menu bar meter after verification.
launchctl bootstrap gui/$(id -u) \
  "$HOME/Library/LaunchAgents/com.djr3x.battery.plist"
```

After flashing, briefly inspect telemetry before restoring the meter (or start
`main.py` and inspect its motion telemetry). A healthy full build should show
`charging:true` near the charger's ~14.2 V, `imu.ok:true`, `mag.ok:true`, and live
`tof_mm` readings. The unplugged full pack is about 13.4 V; firmware and host both
use 14.0 V as the charger-present safety threshold. If the battery agent was already
unloaded, `bootout` may report “Could not find specified service”; that is harmless.

**Wiring** (its OWN I²C bus — never the 21/22 trunk, pins.h): sensor `D/T` →
**GPIO4** (SDA), `C/R` → **GPIO5** (SCL), VCC → 3V3, GND → GND. The module's RP2040
clock-stretches (frame packaging; ~5 s mode reconfigure), and a stretch that outlasts
the Wire timeout corrupts the ESP32 core's I²C driver state — on a shared bus that
crashed the IMU's next transaction and froze the whole firmware (field bug
2026-07-16). On its private controller (I2C1) a wedge can only kill the matrix's own
poll task; `fl`/`fr` then go stale → an honest `-1` within `TOF_MATRIX_STALE_MS`
while the robot keeps running. DIP address **0x33**
(`TOF_MATRIX_ADDR`). No library install needed — the robot build speaks the wire
protocol directly (`tof_matrix.cpp`, a bounded vendored mini-driver; the DFRobot
library's private 8 s receive timeout and per-call mallocs make it bench-only:
`firmware/tof_matrix_test`).

**Floor rejection**: the sensor sits above the floor, so the lower rows of the 45°
FOV permanently see floor at short range (h=0.15 m ⇒ bottom row ≈ 0.45 m) — raw,
that would pin the front zone in SLOW/STOP forever. Each below-horizon row gets a
geometric expected-floor distance (`h/sin(row angle)`); readings at/beyond
`TOF_MATRIX_FLOOR_TOLERANCE` (80%) of it are floor → clear, meaningfully shorter
readings are real obstacles. Obstacles standing proud of the floor (chair legs) are
still caught — mostly by the near-horizontal rows, which have no floor in view.

**Calibration (calib.h, before trusting it on the floor):**
1. **Measure `TOF_MATRIX_HEIGHT_M`** — lens centre to floor, metres. Err HIGH if
   unsure: too-high makes empty floor read as an obstacle (obvious nuisance block);
   too-low classifies real low obstacles as floor (silent miss).
2. **Verify orientation** with the bench viewer (`tools/tof_matrix_gui.py`): tilt the
   module down — near readings must appear in the BOTTOM rows, else set
   `TOF_MATRIX_FLIP_V 1`; hold a hand at the robot's front-LEFT — `fl` (not `fr`)
   must drop in telemetry, else set `TOF_MATRIX_FLIP_H 1`.
3. **Floor check**: on open floor the front zone must read CLEAR (`fl`/`fr` ≈ 3500);
   a hand at ~0.4 m must drop it into SLOW, at ~0.15 m into STOP/BLOCKED.

Init is deferred (~6 s after boot: probe → 8x8 mode-set → 5 s sensor settle) so
`setup()`/the Mac handshake never stall; until ready `fl`/`fr` read **-1** in the
matrix-only build (visible in the GUI radar; safety fails open on -1 by the
documented policy, same as the radial array). Frames poll at ~13 Hz
(`TOF_MATRIX_FRAME_INTERVAL_MS`), each read hard-bounded at
`TOF_MATRIX_READ_TIMEOUT_MS` (30 ms) so a wedged sensor can never stall the sensor
task. A drop-off/cliff mode (floor reading *farther* than expected) is a possible
future extension — not implemented; the `cliff` zone stays unused.

### Manual gamepad override (Bluepad32) — Phase 1.5

A Bluetooth gamepad paired **directly to the ESP32** (not the Mac) can grab the wheel
and override autonomous/voice motion — it works even with the USB link down (docs §11).
Off by default; the feature is `gamepad.cpp` behind `MOTION_GAMEPAD_PRESENT`.

**This build needs a different toolchain.** Bluepad32 replaces the Bluetooth stack, so
it ships as its own ESP32 board package, *not* a library on `esp32:esp32`. Install it
once, then build with that FQBN:

```bash
arduino-cli config add board_manager.additional_urls \
  https://raw.githubusercontent.com/ricardoquesada/esp32-arduino-lib-builder/master/bluepad32_files/package_esp32_bluepad32_index.json
arduino-cli core update-index
arduino-cli core install esp32-bluepad32:esp32   # platform ID is HYPHENATED (FQBN below too)

# Use `compile --upload` (NOT a bare `upload` — that has no --build-property, so it would
# flash whatever variant was last cached). UploadSpeed=115200 is REQUIRED: this USB bridge
# fails the default 921600 ("Invalid head of packet" right after the baud change).

# CONNECTIVITY TEST — gamepad ON, motors STUBBED (the base will NOT move; safest for a
# first pairing test). The live `gp` telemetry + GUI mirror run identically to live:
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32:UploadSpeed=115200 \
  --build-property "compiler.cpp.extra_flags=-DMOTION_GAMEPAD_PRESENT=1" \
  --upload -p "$PORT" firmware/djr3x_motion

# LIVE drive base + gamepad (only on a stand; add -DMOTION_TOF_PRESENT=1 if ToF is wired):
arduino-cli compile --fqbn esp32-bluepad32:esp32:esp32:UploadSpeed=115200 \
  --build-property "compiler.cpp.extra_flags=-DMOTION_HW_PRESENT=1 -DMOTION_GAMEPAD_PRESENT=1" \
  --upload -p "$PORT" firmware/djr3x_motion
```

The dual-version LEDC guard in `hal.cpp` means the motor code builds on the Bluepad32
core whether it tracks ESP32 2.x or 3.x.

**8BitDo Pro 2 — pairing.** Power it in a Bluepad32-friendly mode (hold **START + A**
for the Android/"D" profile; if pairing is flaky try **START + X** for X-input), then
hold the top pair button until the LEDs sweep. Bluepad32 accepts the new connection and
remembers the bond for next time.

**Controls** (`gamepad.cpp`, tunables in `calib.h`):

| Input | Action |
| --- | --- |
| Left stick | arcade drive — Y forward/back, X turn, both on an expo curve; ceilings 0.60 m/s (96 mm wheels, 0.2.18) and 0.96 rad/s (one profile since 0.2.13). A lean while driving banks: forward speed holds and the inside wheel slows. Pure X spins in place. A wheel runs backward for a turn only when the base is nearly still (under 0.12 m/s, fully under 0.03), whatever the stick does |
| L3 (click left stick) | nothing since 0.2.13 (was the hardwood/carpet toggle) |
| **D-pad** | **relative nudges**: Up/Down = short forward/back move (`GAMEPAD_NUDGE_DIST_M`, ToF-gated), Left/Right = relative 90° turn (`default_turn_deg`) |
| **L1 (hold) + D-pad** | **spin to an absolute heading** (encoder test): Up=0°, Left=+90° (CCW), Down=180°, Right=−90° (CW) |
| **B** | **E-STOP** (always honored) |
| Start | clear e-stop + return control to AUTO |
| **L1 + R1 (hold 2 seconds)** | After physically removing the charger cable, declare **unplugged** with both sticks centered and D-pad/Start/B released. The next battery tick (up to 1 second later) releases the charger lock if fresh readings permit it. Release the shoulders, then drive. Works with the Mac app asleep. |
| **R3 (click right stick)** | Toggle SENSOR-BYPASS: right stick drives with ToF gating off; left stick is ignored until toggled off. Does not bypass the charger lock. |

The unplug shortcut is an explicit operator declaration, like saying “you're
unplugged”; only use it after removing the cable. It cancels queued movement,
keeps MANUAL ownership, and preserves e-stop/fault. Fresh sensor readings showing
charging current, charger voltage, or a failed read refuse release; release and
repeat the hold to retry. Firmware `0.2.4-current-unplug` automatically releases after three consecutive
fresh 1 Hz samples of at least 0.5 A net battery discharge, with valid voltage
below 14.0 V. Near-zero/charging current or missing readings reset the count.
This replaces the 90-second guard using the owner's October 1 measurements:
about 0.01 A plugged in versus 1-3 A discharge unplugged/asleep. Display smoothing
does not delay the decision. A connected charger that is outdrawn by the robot
can still mimic an unplug; this is current-based inference, not cable sensing.
Validation: the native fake-INA226/NVS regression and full Bluepad32 build with
motion, gamepad, radial ToF and matrix ToF enabled pass (811,977 bytes flash;
90,604 bytes globals). Flashed October 1 at the owner's request; upload hashes verified and the board
reported `0.2.4-current-unplug`. Stationary telemetry retained the charging lock
at 14.367 V / 6 mA. Physical unplug latency and loaded operation remain untested.

**Rumble feedback** (`GAMEPAD_RUMBLE_*` in `calib.h`, master `GAMEPAD_RUMBLE_ENABLED`):
the pad is a tactile echo of the collision avoidance — a **light buzz** entering the
braking band (the taper just grabbed the throttle), a **strong thump** on a hard stop
(BLOCKED), re-thumping on a slow cadence while you keep pushing into the wall; plus a
friendly **double pulse** whenever a host completes the `hello` handshake (Rex's
`main.py` starting up — the bench tools trigger it too). All Bluepad32 rumble calls run
on the loopTask; the serial task only sets a flag.

**D-pad → relative nudges (driving).** Plain D-pad presses are one-shot positioning moves,
relative to wherever the base is NOW: **Up/Down** run a short encoder-closed forward/back
`move` of `GAMEPAD_NUDGE_DIST_M` (0.30 m) at `GAMEPAD_NUDGE_SPEED_FRAC` of `max_lin`;
**Left/Right** run a relative spin of `default_turn_deg` (90°) at `default_turn_rate`. All
are issued as **MANUAL** finite commands (`ctl_manual_move` / `ctl_manual_turn`): they run
locally on the base, survive a USB drop, and the Mac can't fight them. A left-stick push
cancels an in-flight nudge; **B** e-stops it; **Start** clears + returns to AUTO. The
forward/back nudges have a travel direction, so the **ToF stop reflex gates them** like any
finite move (`done:blocked` at a `Z_STOP` obstacle); the turns are pure spins, so ToF does
NOT gate them — mind the ring swing near obstacles.

**L1 (hold) + D-pad → absolute-heading turn (encoder validation).** Each arrow press spins
the base in place to the absolute heading above (odometry frame, set at boot), via the same
encoder-closed-loop finite `turn` the Mac uses — so on a correctly wired + calibrated base it
lands square at 90° steps. Use it to sanity-check the encoders: a **flipped encoder sign**
makes the spin run away (never converges) instead of stopping; a wrong `counts_per_meter` /
`track_width_m` scale makes it over- or under-rotate (e.g. command 90°, get 75°). Computed
as a shortest-path delta from the live heading (`g_ctx.odom.theta`). (Needs the
`-DMOTION_HW_PRESENT=1` build for real encoders; on the stub build the turn "works" but only
against synthesized odometry, testing nothing.)

Any meaningful stick push switches `owner` to **MANUAL** — the Mac's drive/turn/move
are then refused (`stop`/`estop`/`config`/`ping` still work) and the GUI shows
`owner: manual`. Default is **MANUAL-ASSISTED** (ToF still protects you); FULL-OVERRIDE
is the only way past it and only while held. If the pad drops, the base stops immediately
and stays MANUAL until the idle timeout hands back to AUTO (`manual_autoreturn`, on by
default since 0.2.11, 4 s) or you reconnect and press Start.

**Action buttons → R3X soundboard / animations.** The buttons motion does NOT use —
**A, X, Y, Select (−), Home (★), R3 (right stick click)** — are forwarded to the
Mac as `event:"button"` (rising edge, one per press) by `poll_action_buttons()`.
(L3 and R3 are NOT forwarded.) They fire
**whenever the pad is connected**, independent of the drive `owner` (so the soundboard works
in AUTO and pressing them does NOT grab the wheel). On the Mac,
`intelligence/motion_controller._on_motion_event` looks the button up in
`config.MOTION_GAMEPAD_BUTTON_ACTIONS` and triggers a **sound clip** (`audio/soundboard.py`
plays an MP3 from `assets/audio/clips/`) and/or a **servo animation**
(`sequences.animations.play_body_beat`). The map is data-driven — edit it to remap, no
firmware change. Button names: `a x y select home l3 r3`. (The **D-pad is NOT** in this list
— it drives the encoder-test heading turns above, not the soundboard.) (Needs the
`-DMOTION_GAMEPAD_PRESENT=1` build above.)

**Live state in the GUI ("Motivator Control").** Every telemetry frame carries a `gp`
object — `{connected, lx, ly, btn}` — mirroring the pad's left stick (normalized, right=+
/ stick-up=+) and a pressed-button bitmask (`emit_telemetry` in `proto_io.cpp`, captured
each tick in `gamepad_tick`). The GUI's **PHYSICAL CONTROLLER** panel
(`GamepadMirrorWidget` in `gui/dashboard.py`) renders it read-only: a dot tracks the stick
and held buttons light up, refreshed at the dialog's 150 ms telemetry tick. The bitmask
bit order (`GP_BTN_*` in `gamepad.cpp`) MUST stay in sync with `_GP_BTN_LABELS` in the GUI:
`A B X Y L1 R1 L2 R2 ↑ ↓ ← → Sel Start Home L3 R3`. When no pad is paired (or in a
non-gamepad build) `gp.connected` is false and the panel shows "no pad connected". This is
the recommended way to verify controller connectivity — pair, open Motivator Control, and
watch the dot move.

> **Scaffold:** the arbitration (owner switching, full-override, disconnect failsafe,
> watchdog-bypass-while-manual) is compiled and verified in the stock build, but the
> Bluepad32 I/O itself is **not yet hardware-validated** — verify the button map and
> pairing on the real pad. Builds only with the `esp32-bluepad32` core above.

## Protocol smoke test (the bring-up acceptance test)

With the board flashed and connected, run the host-side test. It opens the serial
port, drives the protocol, and prints PASS/FAIL for the handshake, telemetry
schema, `turn`/`move` completion, the drive deadman, the heartbeat watchdog +
recovery, estop/clear precedence, error handling, and clamping:

```bash
venv/bin/python firmware/tools/motion_serial_smoketest.py --port "$PORT"
```

Exit code 0 = every check passed. This is the evidence that the firmware speaks
the contract correctly.

> **Run the smoke test against the STUB build (`MOTION_HW_PRESENT=0`).** Its
> `turn`/`move` checks expect a `done` reply, which the stub plant produces from
> synthesized odometry. In a **live build on a bare board** those commands never
> complete (no encoder counts → no progress), so the test would hang/fail on them —
> by design. Only run it live once real wheels + encoders are wired and the base is
> on a stand.

## File map

| File | Role |
| --- | --- |
| `djr3x_motion.ino` | Globals, FreeRTOS task setup (control/serial/sensor/telemetry), boot |
| `protocol.h` | Wire version, capabilities, enum↔string maps (the vocabulary) |
| `context.h` | Shared `MotionContext` state, params, hard caps, lock macros |
| `config_params.{h,cpp}` | `config` command application + clamping |
| `proto_io.{h,cpp}` | NDJSON framing, parse + dispatch, all emitters |
| `control.{h,cpp}` | Stub plant + odometry, finite-command lifecycle |
| `safety.{h,cpp}` | Watchdog, ToF zones + reflex stop, comms-loss handling |
| `hal.{h,cpp}` | Hardware abstraction — motor/encoder stubs vs real drivers behind `MOTION_HW_PRESENT` |
| `zone_envelope.h` | Pure speed-adaptive obstacle envelope (stop line and braking distance by speed; the gamepad's own minimum); compiled natively by `firmware/tests/test_zone_envelope.py` |
| `stick_shaping.h` | Pure gamepad stick mapping (round travel to square, deadzones, expo, turn authority); compiled natively by `firmware/tests/test_stick_shaping.py` |
| `drive_shaping.h` | Pure per-wheel effort shaping (breakaway boost, stop fade, PID) and reflex braking; compiled natively by `firmware/tests/test_drive_shaping.py` |
| `tof.cpp` | ToF (VL53L0X ×5) driver — clear-room stub vs XSHUT/mux real driver behind `MOTION_TOF_PRESENT` |
| `gamepad.cpp` | Bluetooth gamepad manual override (Bluepad32) behind `MOTION_GAMEPAD_PRESENT` |

## Concurrency model

Four FreeRTOS tasks share `g_ctx` behind a recursive mutex (`g_state_mux`); all
serial writes are serialized behind `g_tx_mux` so NDJSON lines never interleave.
Lock-order rule: **never hold `g_state_mux` while emitting** (emit takes
`g_tx_mux`) — snapshot under the state lock, release, then emit.

## Phase roadmap

See [docs/motion_system.md](https://github.com/bretbenz84/djr3x-v2/blob/95f2403e78c9ef786054fa13a31bb2ac534cc29d/docs/motion_system.md) §17. This is Phase 0;
Phase 1 wires motors + encoders + ToF and replaces the HAL stubs with real
drivers + per-wheel PID.

## Battery sense (INA226, optional)

Pack voltage (12.8V 4S LiFePO4) is read by an INA226 breakout on the existing
I2C bus — no voltage divider needed (the INA measures up to 36V at its VBUS pin,
covering the charger's 14.6V peak). Without the sensor, telemetry reports
`batt_mv: -1` and the Mac-side battery feature stays dormant.

Wiring (voltage-only, four wires):

    INA226 VCC  -> 3V3
    INA226 GND  -> GND (common with the pack)
    INA226 SDA  -> GPIO 21   (piggyback the ToF bus; INA addr 0x40, mux is 0x70)
    INA226 SCL  -> GPIO 22
    INA226 VBUS -> BATT+ (the pack's positive terminal)

Current sensing (optional, later): the stock module shunt (R100 = 100 mΩ) only
ranges ±0.8 A — useless for drive motors. Fit a 2 mΩ shunt inline in the main
battery lead, then build with `-DBATT_SHUNT_MICROOHM=2000`; telemetry gains a
real `batt_ma` and the Mac side can coulomb-count true state of charge (LiFePO4
voltage is too flat mid-pack for voltage-only percentages).

## Front-right radial persistence (firmware 0.2.1)

The front-right VL53L1X (mux channel 5, `fr_radial`) has its own filter in
`tof_fr_filter.h`. The original shared guard accepted a sudden drop after two
similar readings. The new guard requires a consistent run for 300 ms, at least
three distinct sample timestamps, no inter-sample gap over 200 ms, and readings
within 200 mm of the candidate. At the measured 80 ms sensor cadence, that means
five readings over about 320 ms. Returning to the longer range or an invalid
sample cancels the candidate. This applies to drops of at least 400 mm, and to
drops of at least 100 mm into the below-300-mm band. Gradual approaches still
update immediately. Cold start honors the first range conservatively.

A large clearance increase needs 160 ms of confirmation, then releases at the
existing 300 mm per-sample limit. Invalid phase/quality readings hold the previous
range instead of becoming "4 metres clear"; `OutOfBoundsFail` still means nothing
in range. Eight consecutive errors still publish -1 and reset the filter. The
other seven radial channels, the 8x8 filter/floor mask, and all motion semantics
are unchanged. The Mac continues to own person approach; firmware owns sensing,
motor execution and danger avoidance.

Raw diagnostic command (stationary, opt-in, no motor command):

```json
{"v":1,"cmd":"tof_debug","seq":51,"seconds":45}
```

`seconds` must be 0–120; zero disables the stream. Firmware emits one `tofraw`
record per fresh front-right read, with `t` (board milliseconds), `raw_mm`, sensor
`status`, status-qualified `input_mm`, and `filtered_mm`. A negative `input_mm`
means an invalid observation. Streaming expires automatically and disables itself
if motion begins. It is off by default and does not change filtering. The serial
port must be exclusively owned by the recorder; quit/pause the battery menu app
before connecting, then restore it afterward. Never use the moving smoke test as
an alternative to a stationary capture.

The 2026-09-07 pre-fix capture contained 543 raw readings (150–168 mm). Ten
`WrapTargetFail` readings had incorrectly produced far/clear output. The sensor
really reported a sustained near range in that capture, so this filter must keep
that range; it cannot certify what physical object produced it. Raw fixture and
compiled-firmware regressions: `firmware/tests/test_tof_fr_filter.py`.

## Front-right quality quarantine (firmware 0.2.2)

The September 15 stationary capture shows a different fault from short range
spikes: most front-right observations are `WrapTargetFail`, interleaved with
valid-looking ~60 mm results. Distance persistence cannot reject this fault.
`TofFrHealth` quarantines this channel after eight invalid observations in the
last 16 fresh samples. It reports `fr_radial=-1` (unavailable), resets the distance
filter, and requires 16 consecutive valid observations (~1.2 seconds) to recover.
Duplicate timestamps cannot establish recovery; a >200 ms sampling gap breaks
recovery. An isolated error leaves the existing distance filter in control.

The matrix continues to supply the combined front-right distance when healthy.
This is degraded sensor coverage, **not proof that the radial direction is clear**:
the radial channel's hardware/optics remain faulty and should be repaired. Other
radial channels and the matrix filter are unchanged. State transitions are logged;
`tof_debug` exposes raw/status/input and the quarantined `filtered_mm=-1` result.
The recorded regression is `tests/fixtures/tof_fr_quality_2026_09_15.csv`.

## Front-right radial disabled (firmware 0.2.7, October 10, 2026)

At the owner's request, the faulty front-right VL53L1X on mux channel 5 is
disabled in `hal_tof_init`: it is not initialized, started or polled. Its radial
reading stays `fr_radial=-1`, so it cannot veto motion or steer the base with a
phantom obstacle. This supersedes automatic quality recovery and the channel's
October 10 crosstalk compensation experiment. The boot log explicitly reports
`DISABLED`; the radial tally is now normally 7/8.

The front matrix still supplies `fr` and its obstacle reflex; the other seven
radial channels, motors, gamepad, watchdog and charging interlock remain active.
This removes coverage from that one radial beam until hardware repair and an
explicit source change re-enable it. Use the full-robot Bluepad32 runbook above
with all four hardware flags. Full compilation passes (812,817 bytes program,
90,636 bytes globals); 26 isolated sensor/battery regression tests pass.

Upload completed with hashes verified after one failed serial transfer. Stationary
hello confirmed `0.2.7-fr-disabled`; 139 frames reported `fr_radial=-1`. Boot logs
confirmed seven other radial sensors online, Bluepad32 ready and matrix ready.
No drive command was sent; physical gamepad/floor-driving acceptance is pending.
The battery menu LaunchAgent was restored.

## Matrix floor calibration (firmware 0.2.8, October 10, 2026)

The owner identified the close bottom-row returns as empty floor. The prior
250 mm floor lower bound could never reject these 80-250 mm readings. Normalized
rows 6 and 7 (the bottom two, zero-based) now use a measured 70 mm floor-rejection
threshold, 10 mm below the minimum in the 53-frame stationary capture. Their
returns at/above 70 mm are floor; nearer valid returns still count as obstacles.
The other six rows retain their geometric thresholds. `tofmx.rej` publishes the
same thresholds used by the firmware reflex, so host path checks reject the same
floor without a Python workaround. This replaces the geometric-only rule for
these two rows; the front-right radial remains disabled.

This is a calibration for the owner's current floor/mount: low objects producing
the same ranges in these downward rows are indistinguishable from the floor.
Upper-row obstacle detection is retained. Four native production geometry/
aggregation checks cover observed floor ranges, upper-row close obstacles,
returns nearer than floor and missing/single-zone returns. Together with the
sensor/battery regressions, 26 isolated checks pass. Host replay of the 53 recorded
frames removes all 42 near-floor path blocks; remaining path minima start at
1.297 m. Full four-flag Bluepad32 build passes (812,849 bytes program, 90,636
bytes globals). Live deployment evidence is in the V3 drive integration document.

Deployment verified: hello `0.2.8-matrix-floor`, upload hashes verified, 57/57
stationary matrix frames give clear host path at 3.5 m using bottom thresholds
70/70. Final firmware front pair 3500/3500, zone CLEAR, radial still disabled,
Bluepad32 ready. Battery menu restored. No drive command was sent.

## Rear 8x8 matrix and front-right re-enable (firmware 0.2.9, October 19, 2026)

**Rear matrix.** An identical SEN0628 at the front module's height, facing straight
back. It shares the front's private bus: `D/T` and `C/R` spliced to the front
module's (GPIO4/GPIO5), GND common, DIP address **0x32** (front stays 0x33; two
modules at one address corrupt both). Power it from 3V3 like the front, or from the
5 V buck after checking its idle `D/T`/`C/R` lines read ~3.3 V, not 5 V, with the
module alone at 5 V. Keep that VCC off the front module's 3V3 lead when on 5 V.

One poll task (`tof_matrix.cpp`) owns both modules, each with its own
probe → mode-set → blind 5 s settle → ack state machine, so the front keeps polling
through the rear's mode switch. Nothing addresses a module while it settles. The
rear is optional: absent, it is probed once a second (logged every 60th try) and
`rl`/`rr` stay radial-only. Fitted, its sensor-frame halves map to the robot frame
(its left half is the robot's rear-RIGHT) and min-combine into `rl`/`rr` for the
reverse reflex; telemetry adds `rl_radial`/`rr_radial` (the radial pair alone)
and streams the raw grid as `tofmx_rear` every third frame (~4.4 Hz). A wedge on
the shared bus now silences both matrices (each reports -1 after
`TOF_MATRIX_STALE_MS`). Height and floor rows are **copies of the front's**
until this module's own empty-floor capture (calib.h `TOF_MATRIX_REAR_*`).

**Front-right radial.** Channel 5 is initialized and polled again for the owner's
replacement module. Its crosstalk compensation is back to 0 (that 32 kcps/SPAD
belonged to the faulty module's lens); persistence filter and quality quarantine
still apply. Geometry/aggregation now live in the pure `tof_matrix_floor.h` the
native test compiles directly.

## Smooth starts and stops (firmware 0.2.10, October 23, 2026)

Owner: on the 52 lb robot turns were jarring and almost too fast, they did not ease in,
stops were worse, and obstacle stops were mostly false slams. The setpoint ramp was
already gentle (the Mac pushes `accel_lin` 0.35 m/s², `accel_ang` 2 rad/s²); the duty
underneath it stepped. Three changes, all in `drive_shaping.h` with thin call sites in
`hal.cpp` and `control.cpp`:

- **Breakaway boost instead of a kick.** A wheel used to jump to its breakaway duty
  (hardwood pivot 750 ≈ 75%, carpet 1023, straight 500) on the first commanded tick
  and hold it until 0.12 s after it rolled. Now a wheel stalled for 30 ms gains duty
  above its running floor at 2500 duty/s up to that same ceiling, and gives it back at
  2500 duty/s once it moves. A hardwood pivot's first tick is about 446 duty (was
  ~817) and it breaks loose at about what it needs. A wheel that never moves still
  reaches the full ceiling within 0.15 s, so carpet keeps its authority. A wheel
  crawling at a target under the stall threshold is not treated as stalled.
- **Stops taper.** While the body ramps to rest (nothing commanded this tick), the
  running floor (230 straight, 380 hardwood / 650 carpet pivot), boost and integrator
  fade to zero with the target, the integrator stops winding, and the duty never
  reverses. It used to hold the floor until the target fell under 1 cm/s, then go to
  zero duty with the bridge enabled: a short-circuit brake.
- **Reflex braking.** A block toward the direction of travel used to cut the speed
  ramp to zero in one tick. It now sheds speed at v²/2(room − 4 cm), at least
  0.8 m/s² and at most 6 m/s², never past zero. At 0.25 m/s from the speed-adapted
  stop line (~0.12 m) it stops in about 4 cm; one false ~77 ms matrix frame costs
  about 0.06 m/s. The zones, latch, slow-zone taper and `FINITE_BLOCK_GRACE_MS` are
  unchanged.

Unchanged: speeds, ramps, turn deceleration (`TURN_DECEL_MIN_RAD_S`), gains, surface
modes, estop/fault/comms-loss motor cut (the bridge disables and the wheels coast),
gamepad mixing and the protocol. No new runtime config key.

Checks: `firmware/tests/test_drive_shaping.py` compiles the header with clang and runs
turn starts, stops and reflex cases against a synthetic toy wheel (7 tests); all 39
native firmware checks pass (`tools/run_firmware_checks.py`). Full four-flag Bluepad32 build: 814,457 bytes
program, 90,956 globals. Flashed at the owner's request; hashes verified, hello
`0.2.10-smooth-drive`. Not yet driven: the feel on the floor, whether a softer launch
stalls a carpet pivot, and how the change moves the Mac's turn-coast compensation.

## Yellow Jacket motors and 72 mm wheels (firmware 0.2.11, November 12, 2026)

The owner rebuilt the base with two goBILDA 5203 Yellow Jacket motors (50.9:1, 117 rpm,
1425.1 encoder counts per wheel revolution) and 72 mm wheels, and reported from the
gamepad: far too fast, an on/off stick, turns that jerk, no steering away from walls,
and a menu bar joystick that did nothing. A stationary telemetry read (hello only) found
firmware 0.2.10 still calibrated for the 60 rpm motors and 100 mm wheels: 17000 counts
per metre against a true 6300, so every measured speed read 0.37x true and any command
saturated both wheels at the drivetrain's 0.44 m/s. With both wheels saturated there is
no speed difference left for a turn or the wall assist. The pad also still owned the
wheels (`owner: manual`), which refuses every host `drive`.

Changed (all in `calib.h` unless noted):

- **Geometry**: 6300 counts per metre per wheel (computed, not taped), wheel 0.072 m,
  wheel-speed ceiling 0.42 m/s (was 0.90), gain scale 3.4 (was 5.2). Hard caps
  0.45 m/s and 2.0 rad/s (`context.h`, were 1.10 and 6.10). Track width unchanged.
- **Effort floors**, estimated at a third of the old motors' (about three times the push
  per unit of duty): running floor 80, straight breakaway 200, pivot floor/kick 150/350
  hardwood and 260/650 carpet.
- **Pad feel**: boot acceleration 0.40 m/s² and 2.5 rad/s² (were 1.2 and 8.0; the V3
  program still pushes 0.35 and 2.0). Expo 1.6 on both stick axes (forward was 0.60
  anti-expo, turn linear). Turn ceilings 1.2 / 1.6 rad/s instead of the whole wheel
  range (4 rad/s), so at full forward the turn stick slows the inside wheel in
  proportion and stops it only at full deflection. Forward 0.25 / 0.38 m/s.
- **Encoder watch** (`drive_shaping.h`, `hal.cpp`, `control.cpp`): a wheel driven at 45%
  duty or more with no encoder count for 0.5 s cuts the motors and raises the existing
  fault `encoder_stall` (Start or `clear` releases). Without it a dead encoder winds the
  speed loop to full duty. A truly jammed wheel trips it too.
- **Idle hand-back** (`context.h`): `manual_autoreturn` defaults to true, so a pad left
  alone for 4 s returns the wheels and the menu bar joystick works.

Not verified: encoder direction and scale on the new motors (the odometry after the
owner's pad drive read 6 cm and 15°, which suggests the encoders counted little or
nothing), the estimated floors, track width, any driving at all, carpet, and the Mac's
turn-coast compensation. The front-right radial sensor quarantined itself again at boot.

Checks: 40 native firmware checks pass, one new (the encoder watch). Full four-flag
Bluepad32 build 814,977 bytes program, 90,972 globals. Flashed at the owner's request,
hashes verified, hello `0.2.11-yellow-jacket`; boot showed 8/8 radial sensors, both
matrices, IMU, compass and battery sense up. No motion command was sent.

## Banking turns from the stick (firmware 0.2.12, November 12, 2026)

Owner, after driving 0.2.11: better, but at full forward a slight lean left or right did
nothing, then past a point the base nearly stopped and turned, with the two sides
counter-rotating; no banking. The per-axis stick mapping caused it. A thumbstick travels
in a circle, so leaning it at full push shortened the forward reading (0.71 at 45°), and
the deadzone and expo curve turned that into half speed or less, soon inside the band
where the mixing lets the inside wheel reverse. The turn axis also had a 12% deadzone of
its own, and full turn stick asked for more wheel-speed difference than the forward
ceiling.

`stick_shaping.h` (pure, called from `gamepad.cpp`) now stretches the round travel to a
square, so a full push anywhere within 45° of forward is still full forward; uses one
radial deadzone, keeps the forward axis's own, and gives the turn axis only 5%; eases
the turn curve from 1.6 to 1.3; and while translating limits full turn stick to a
wheel-speed difference of 80% of the forward ceiling each side
(`GAMEPAD_ARC_STEER_FRAC`). At full push on hardwood a 10° lean commands 0.235 / 0.265
m/s, 30° 0.157 / 0.343, 45° 0.05 / 0.42 (about a 0.28 m radius). The inside wheel stops
only past 45°, and the sides counter-rotate only within about 20° of pure sideways.
Standing still, sideways is the same spin as before (1.2 rad/s hardwood).

Checks: 45 native firmware checks pass, five new (`test_stick_shaping.py`: speed held
through a lean, progressive difference, no step across a 1° sweep, spins, centre,
reverse). Full four-flag build 815,517 bytes program, 90,972 globals. Flashed at the
owner's request, hashes verified, hello `0.2.12-bank-turns`. Not yet driven. The pad's
real stick range was assumed round and was not measured.

## Reversal by speed, one gamepad profile (firmware 0.2.13, November 12, 2026)

Owner, after driving 0.2.12: at full forward a slight right worked (with a jerk) and a
slight left did not; it did not brake before obstacles; and tying wheel reversal to the
stick's diagonal is wrong for a 55 lb robot carrying speed: "it needs to ramp down that
side's speed and reverse only if it's moving in place ... otherwise this thing will get
whiplash". He also expected no hardwood/carpet difference, and found the front 8x8
module's reset button held down (released; the log shows that module going stale twice
that evening).

- **Reversal follows speed** (`stick_shaping.h` `drive_speed_blend`, `control.cpp`). The
  spin↔arcade blend is now the more arcade of the stick's and the speed's: at 0.12 m/s or
  more (the larger of the ramped command and the measured speed) a turn can only slow the
  inside wheel to a stop; the allowance to reverse it eases in below that and is complete
  under 0.03 m/s. The mixing moved to the pure `mix_wheels` (`drive_shaping.h`).
- **Wheel-target slew** (`wheel_slew_step`, gamepad only): a wheel's target may gain speed
  or cross zero at most 1.0 m/s²; slowing toward zero is never limited. Without it the
  inside wheel swung backward at 1.9 m/s² as the allowance opened. Boot angular
  acceleration 2.5 → 2.0 rad/s² (the V3 program's own value).
- **Wall steering needs both front readings** (`control.cpp`). A missing front side
  counted as clear, so with the matrix in reset and the front-right radial dropping in and
  out, anything within 0.9 m front-left pushed every forward drive right by up to
  0.25 rad/s: enough to cancel a slight left and exaggerate a slight right. This is the
  likely cause of the left/right difference; it was not observed directly.
- **One gamepad profile**: the hardwood/carpet toggle on L3 is gone (0.25 m/s, 1.2 rad/s,
  pivot floor/kick 150/350). R3 is unchanged: it still turns obstacle protection off and
  moves driving to the right stick while off.

Simulated with the firmware's own mapping, ramps and mixing: stick thrown from full
forward to full left, the inside wheel slows 0.25 → 0 in 0.29 s at 0.85 m/s², reverses
only once the base is under 0.12 m/s, and reaches the full spin at 0.68 s with no wheel
target changing faster than 1.0 m/s². Thrown to a hard forward-left, no wheel reverses.

Checks: 49 native firmware checks pass (four new). Full four-flag build 815,629 bytes
program, 90,972 globals. Flashed at the owner's request, hashes verified, hello
`0.2.13-speed-reversal`; after boot the front matrix reported its own distances
(488 / 433 mm against the radial pair's 923 / 571). Not yet driven: the feel, braking
before obstacles with the matrix back, and whether the left/right difference is gone.

## Encoder directions measured, stronger encoder watch (firmware 0.2.14, November 12, 2026)

The owner turned each wheel by hand while telemetry was recorded (hello only, motors
unpowered). First pass: the right encoder only flickered one count up and down with no
net travel, the signature of one of its two signal wires missing; he found a loose wire.
Second pass, each wheel forward then back, twice: both encoders counted, and both read
forward as reverse (about -180 to -235 mm on the forward pushes, +185 to +210 mm on the
backward ones; each wheel registered only while it was the one being turned).

With a reversed encoder the speed loop is positive feedback, and with the dead channel
the right wheel read as stopped: on 0.2.11 to 0.2.13 both wheels ran to full duty for
any command. The tuning in those versions was therefore never exercised as intended,
and the left/right difference and jerks reported under 0.2.12 are more likely this than
the wall steering blamed in the 0.2.13 section.

- `ENC_SIGN_L` +1 and `ENC_SIGN_R` -1 (were -1 / +1). `MOTOR_SIGN_*` unchanged.
- The encoder watch now requires a wheel driven at 45% duty or more to be measured
  moving the way it is driven, 20 counts (3 mm) within 0.5 s (`encoder_watch_step`). It
  catches a dead encoder, the one-wire flicker the first version missed (any count reset
  it), and a reversed sign. Fault `encoder_stall` as before.

Checks: 49 native firmware checks pass (the watch test rewritten for the three cases).
Full four-flag build 815,741 bytes program, 90,996 globals. Flashed at the owner's
request, hashes verified, hello `0.2.14-encoder-signs`. Not yet driven: this is the
first build whose speed loops are closed correctly on these motors, so the estimated
floors, gains and counts per metre get their first real test.

## Gamepad top speed (firmware 0.2.15, November 12, 2026)

Owner, first drive with working speed control (0.2.14): "the motion is much better but
it's super slow now. Full speed ahead feels like 1/3 speed." Until then he had only felt
these motors flat out (about 0.47 m/s); 0.2.14 held the commanded 0.25 m/s.
`GAMEPAD_LIN_MS` is now 0.38 m/s, about 80% of flat out and under the 0.42 m/s wheel
ceiling. A hard bank at full push would ask the outside wheel for more than that
ceiling, so both wheels scale together: 15° lean 0.34 / 0.42 m/s, 30° 0.21 / 0.42 (the
base slows to 0.32), 45° 0.07 / 0.42 (0.25). Spin rate, ramps and autonomous speeds are
unchanged. At 0.38 m/s the boot obstacle envelope is a 0.20 m stop line and braking from
0.45 m.

Checks: 49 native firmware checks pass. Full four-flag build 815,733 bytes program,
90,996 globals. Flashed at the owner's request, hashes verified, hello
`0.2.15-pad-speed`. Not yet driven at this speed; braking before obstacles at 0.38 m/s
is unverified.

## Faster forward, gentler spin (firmware 0.2.16, November 12, 2026)

Owner after driving 0.2.15: "It's so much better ... The acceleration profile seems
really good. Nothing lurches." He asked for more forward/back speed if the motors have
it, and the stationary spin down by 20%.

- `GAMEPAD_LIN_MS` 0.38 → 0.45 m/s, the drivetrain's limit (0.44 m/s unloaded at 12 V,
  about 0.48 on a full pack). `WHEEL_TARGET_MAX_MS` 0.42 → 0.46, hard cap 0.45 → 0.50.
  At full stick the speed loop will sit at or near full duty, so the top few percent
  depend on charge and on the two motors matching.
- `GAMEPAD_ANG_RADS` 1.2 → 0.96 rad/s (about 55°/s in place).
- Banking no longer takes the spin ceiling as its limit: `GAMEPAD_ARC_STEER_FRAC` 0.70 of
  the forward ceiling keeps the bank he approved at 0.38 m/s. Full push: 15° lean
  0.37 / 0.46 m/s, 30° 0.23 / 0.46, 45° 0.08 / 0.46.
- Ramps, the reversal-by-speed rule, the wheel-target slew and autonomous speeds are
  unchanged. At 0.45 m/s the boot obstacle envelope is a 0.21 m stop line with braking
  from 0.50 m.

Checks: 49 native firmware checks pass. Full four-flag build 815,733 bytes program,
90,996 globals. Flashed at the owner's request, hashes verified, hello
`0.2.16-pad-faster`. Not yet driven at this speed: straight tracking at full duty and
braking before obstacles at 0.45 m/s are unverified.

## Braking envelope at the new top speed (firmware 0.2.17, November 12, 2026)

Owner after driving 0.2.16 at 0.45 m/s: "At full speed, it still runs into walls. If
it's near a wall and stationary it won't let me go forward ... It needs to decelerate a
little faster if it's quickly approaching a wall."

The obstacle zones scale with speed, and the scale's top (`ZONE_SPEED_REF_MS`) was still
0.80 m/s from the original 176 rpm motors. A full-speed run on these motors got 56% of
the configured envelope: stop line 0.21 m, braking from 0.50 m. In simulation an ideal
base still stops; one whose real speed lags the commanded slowdown by 0.3 s with 0.3 s of
sensor delay reaches the wall at 0.08 m/s, and with the host's tighter zones (0.15 /
0.60 m, pushed whenever Rex runs) at 0.3 m/s. At 0.25 m/s the same base stops.

- `ZONE_SPEED_REF_MS` 0.80 → 0.46: the whole configured envelope applies at top speed
  (boot values: stop line 0.30 m, braking from 0.75 m). The at-rest floors (0.10 / 0.18 m)
  are unchanged, so parking close still works.
- While the pad drives, the envelope is never tighter than the gamepad's own
  (`GAMEPAD_STOP_ZONE_M` 0.30, `GAMEPAD_SLOW_ZONE_M` 0.75), whatever the host configured
  for Rex. The envelope math moved to the pure `zone_envelope.h`.
- **Rex's own driving changes slightly**: at 0.25 m/s with the host's zones his stop line
  is 0.127 m (was 0.116) and braking starts 0.41 m out (was 0.31).

Checks: 54 native firmware checks pass, five new (`test_zone_envelope.py`: the envelope
at top speed and at rest, the pad's minimum, and a lagging base stopping at least 5 cm
short from full pad speed with either set of zones). Full four-flag build 815,833 bytes
program, 90,996 globals. Flashed at the owner's request, hashes verified, hello
`0.2.17-brake-envelope`. Not yet driven. The lag and delay figures are assumptions, not
measurements; no approach has been recorded. Oblique approaches, where the front corner
reaches a wall before the forward-facing sensors see it close, are not addressed.

## 96 mm wheels (firmware 0.2.18, November 13, 2026)

The owner replaced the 72 mm drive wheels with 96 mm ones and asked for the firmware to
match. Everything below is computed from the wheel size and the motor's published
figures; nothing was taped or driven.

- **Scale**: 4725 counts per metre per wheel (1425.1 / (π × 0.096 m); was 6300), wheel
  0.096 m, gain scale 2.55 (was 3.4: a third more speed per unit of duty).
- **Effort floors** scaled by 96/72 from the 72 mm values the owner drove (a quarter less
  push per unit of duty): running floor 105, straight breakaway 265, pivot floor / kick
  200 / 465.
- **Top speed**: the drivetrain now reaches 0.59 m/s unloaded at 12 V. Wheel ceiling
  0.61 m/s (was 0.46), hard caps 0.66 m/s and 2.7 rad/s, and the gamepad's forward
  ceiling 0.60 m/s (was 0.45), keeping full stick at the motors' limit as the owner
  asked on the 72 mm wheels. Spin rate, banking fraction and ramps are unchanged, so
  full speed now takes 1.5 s to reach.
- **Braking envelope**: `ZONE_SPEED_REF_MS` 0.61 (top speed). The gamepad's own envelope
  grows to a 0.40 m stop line with braking from 1.00 m at full speed; with the previous
  0.30 / 0.75 m the simulated lagging base reached the wall from 0.60 m/s, and with
  these it stops at least 7 cm short. Rex at 0.25 m/s with the host's zones: stop line
  0.12 m, braking from 0.35 m.

A stationary read after the wheel change showed no false obstacle from either depth
matrix (bottom two rows of the front one read floor at 299-586 mm, zone clear). The
matrices sit about 12 mm higher now; their floor calibration was not redone.

Checks: 54 native firmware checks pass (the stick-sweep step bound is now relative to
top speed). Full four-flag build 815,833 bytes program, 90,996 globals. Flashed at the
owner's request, hashes verified, hello `0.2.18-wheels-96mm`. Not yet driven: speed,
starting push, braking from 0.60 m/s, track width with the new wheels, and Rex's own
turns and approaches.

Owner, after driving 0.2.18 from the gamepad the same day: "its good, doesn't hit things." That
covers gamepad driving and braking at the new top speed. Still open: taped distance scale and track
width, the depth matrices' floor calibration at the new height, and Rex's own turns and approaches.
