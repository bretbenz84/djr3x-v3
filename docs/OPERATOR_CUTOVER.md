# Prepared operator cutover

**Not executed.** September 25: the owner chose to keep V2 running and commission
later. V3's default settings were not changed. The prepared bundle is under
`local/operator-migration/20260925T191225654301Z/`; it contains robot settings,
two new LaunchAgent definitions, four original plist backups and digest inventory.
Wake models are independent copies under `local/models/wake/`. Optional packages
are installed in V3's `.venv` and pinned in `requirements-robot.txt`.

## Before changing services

Be at the robot with its motion envelope clear. Verify its actual joint positions,
source calibration and serial identity; controller pulse readback is not shaft
feedback. Current preparation uses the inspected Maestro port and source limits;
the V2 servo limit overrides were empty at inspection. Check again if the mechanism,
board configuration or port changed. Close any manually launched servo/control
programs and shut down the V2 controller normally before replacing its companions.

Inspect the bundle's `inventory.json` and compare the original plist hashes again.
If an installed definition changed, prepare a fresh bundle rather than overwriting
it using stale rollback assumptions. Re-preparation is explicit and does not start
anything:

```sh
.venv/bin/python tools/prepare_operator_migration.py \
  --wake-source /path/to/v2/assets/models/wake_word \
  --servo-port /dev/cu.YOUR_MAESTRO
```

All commands below are for that future supervised session. Set `BUNDLE` to the
reviewed absolute bundle path. Do not batch commissioning and LaunchAgent activation
without checking the result of each stage.

## Disable the old competing companions

Disable login/KeepAlive restart as well as unloading the jobs. Keep the original
plist files and the prepared backups. LED and battery jobs stay installed for this
servo-only migration and yield to the same controller lock.

```sh
launchctl disable "gui/$(id -u)/com.djr3x.supervisor"
launchctl disable "gui/$(id -u)/com.djr3x.servo"
launchctl bootout "gui/$(id -u)/com.djr3x.supervisor"
launchctl bootout "gui/$(id -u)/com.djr3x.servo"
```

Verify both jobs and their child processes have exited. A manually started V2
supervisor also blocks V3. V3's global controller intent makes legacy console
writers yield; resource leases and `lsof` must then confirm release. A timeout or
unknown probe result is a failure, never permission to force the port open.

## Commission and test in the foreground

Run the read-only dependency/configuration check first:

```sh
.venv/bin/python -m r3x doctor --config "$BUNDLE/robot-settings.toml"
```

The live runner rejects zero or partial cold output state. Do not treat a successful
dependency check as physical readiness. If all eleven channels are off, the operator
must independently establish the intended rest positions before running the one-time
initializer. These are head centered/lowered at its calibrated minimum, head tilted
down, visor closed, elbow at gravity rest, hand/poker arm at 6000 pulses, hero arm at
5984 and throttle parked at 9088/9984/2048 quarter-microseconds. If physical reference
positions are not independently known, commission those first; the flag cannot
measure them or make an arbitrary unpowered pose safe.

Only after that physical verification, `operator servo-commission --confirm-rest`
with the explicit `--config` writes the initial pulses. It refuses mixed/nonzero
output state and creates no durable park marker. Review the result and inspect the
mechanism before continuing. To exercise an already energized, valid park route:

```sh
.venv/bin/python -m r3x operator servo-park --config "$BUNDLE/robot-settings.toml"
.venv/bin/python -m r3x live --config "$BUNDLE/robot-settings.toml"
```

Verify wake scan/throttle unfold, actual-playback talking motion, stop during speech,
and quit → park → port closure. Inspect motion receipts and microphone-hold coverage
in the session trace. Check normal quit and a deliberate SIGTERM to that verified V3
PID; SIGKILL does not park. No voice shutdown command is implemented in this slice.
Test failures and resource release before leaving a supervised run unattended.

## Install the prepared V3 jobs

After foreground acceptance, copy the two **new** plist names to
`~/Library/LaunchAgents/` and bootstrap them. Load the servo console first and test
manual V3 launch/release/reacquisition, then enable the supervisor and test voice
launch. Its `wake up rex` model needs a live positive/negative wake check; synthetic
silence proves neither detection nor false-trigger rates.

```sh
cp "$BUNDLE/com.djr3x.v3.servo.plist" "$HOME/Library/LaunchAgents/"
cp "$BUNDLE/com.djr3x.v3.supervisor.plist" "$HOME/Library/LaunchAgents/"
launchctl enable "gui/$(id -u)/com.djr3x.v3.servo"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.djr3x.v3.servo.plist"
launchctl enable "gui/$(id -u)/com.djr3x.v3.supervisor"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.djr3x.v3.supervisor.plist"
```

Confirm one supervisor/controller set, login behavior, no stale slider commands,
and the return of off-program controls after normal exit, failed startup and process
exit. Killing the supervisor does not stop its independent V3 child: identify and
gracefully stop the controller separately when required. Logs are under V3
`local/sessions/`; supervisor-launched controller stdout is `controller-console.log`.

## Rollback

Disable/unload both new V3 jobs. Gracefully stop any V3 controller and verify its
microphone/serial handles and locks are released before restoring the old owners.
If originals were altered, restore their reviewed backup definitions. Then enable
and bootstrap `com.djr3x.servo` and `com.djr3x.supervisor` from their original plist
paths. Leave the new V3 jobs disabled so login cannot start both generations.
Verify V2 off-program controls and wake launch before declaring rollback complete.

Physical clearances, cold-start safety, interruption/settling, live handoff, enrollment
quality, wake accuracy and combined robot load remain acceptance items. Current
software evidence is in [servo integration](SERVO_INTEGRATION.md#verification).
