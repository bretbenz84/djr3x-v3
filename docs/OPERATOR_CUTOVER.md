# Operator services (installed)

**Status, September 27, 2026: V3 owns the robot's always-on services.** The four
LaunchAgents keep their labels but now run V3's copies of V2's programs from
`operator_tools/`, under V3's `.venv`. V2's checkout is unchanged and none of its
programs run.

| LaunchAgent | Program | What it does while Rex is off |
| --- | --- | --- |
| `com.djr3x.supervisor` | `operator_tools/rex_supervisor.py` | Listens for "wake up rex" (openWakeWord `wakeuprex.onnx`, score ≥ 0.92 for 4 frames of 80 ms). Plays the chime and launches `python -m r3x live`. Plays the ready chime once at login, and charger connected/disconnected sounds. Auto-updates the V3 checkout. |
| `com.djr3x.battery` | `operator_tools/tools/rex_battery_menubar.py` | Menu-bar battery readout from the drive ESP32 (percent, volts, amps, watts, runtime), Set-Battery-%, ESP32 restart and gamepad. Paints the chest meter and the mouth charge glow. |
| `com.djr3x.led` | `operator_tools/tools/rex_led_menubar.py` | Head/chest LED animation buttons. Battery-meter mode (default) leaves both boards to the battery app. |
| `com.djr3x.servo` | `operator_tools/tools/rex_servo_menubar.py` | Sliders for all fourteen servos (two-servo head, poker elbow/wrist since October 10), throttle nudges, measurement mode, throttle sequence recording/playback and Pololu restart. |

Each plist sets `PATH` to include `/opt/homebrew/bin`. launchd's default path lacks
it, so the first voice launch on September 27 exited at once: V3's camera readiness
check could not find Homebrew's `ffmpeg`, although the same command worked from
Terminal. A failed start is silent. The reason is in the readiness report's `missing`
list in `controller.console.log`.

Logs are in `local/logs/` (`supervisor`, `battery_menubar`, `led_menubar`,
`servo_menubar`, and `controller.console.log` for a supervisor-launched Rex). Console
state, throttle sequences and measurements are in `local/operator/`.

## How they share the robot

- A running controller holds `/tmp/djr3x-main.lock`, V2's lock path, which V3 kept.
  - Every service polls it and goes dormant: the supervisor closes the microphone, and
    the menus close their serial ports.
  - V3's startup waits for the microphone lease and for `lsof` to show every port free.
    It never forces a port.
- **Measured September 27** with V3's real startup handover code, opening no devices and
  moving nothing:
  - All four services released the microphone and ports within 0.7 s.
  - After release, each resumed: the supervisor listening, the battery app repainting the
    chest meter, the servo menu reconnected.
- A wedged microphone makes V3 exit with code 86. The supervisor then relaunches Rex
  after 2 s, at most 3 times in 10 minutes. "Shut down" and other exits return to
  wake-word listening.

## Automatic updates

- **When the supervisor checks:**
  - at startup, at each wake-up (Rex then starts on the updated code) and every 4 hours;
  - while Rex runs it only fetches, and applies the update after he exits.
- **What it does:** fast-forwards the V3 checkout's `main` from `origin/main`.
- **When it skips:**
  - the checkout has uncommitted changes;
  - it is on another branch;
  - local and remote histories have diverged.

  It never stashes, resets or merges.
- **Credentials:** the private GitHub repo needs the saved GitHub login from the
  keychain. The owner granted keychain access on September 27; before that the check
  timed out under launchd.
- **Not automatic:** new Python dependencies still need a manual install. A changed
  supervisor restarts itself.

## Where the copies come from

`operator_tools/` holds independent copies of V2 `777b38c` + `fb96f08`: `rex_supervisor.py`,
`tools/rex_{battery,led,servo}_menubar.py`, `tools/throttle_sequences.py` and
`utils/{single_instance,supervisor_handoff,repo_updater}.py`. The adaptations:
- `v3_settings.py` supplies the V2 setting names from V3's `local/settings.toml`.
- The supervisor launches V3, verifies V3's wake-model digests and plays WAV copies of
  the chime and charger sounds (`assets/audio/operator/`).
- State paths moved under `local/`.
- V2's durable "clean park" marker is not used.

`tests/test_operator_tools.py` checks the wiring without devices. V3's earlier minimal
supervisor and servo console were retired. `python -m r3x operator servo-park` remains;
startup itself handles servos left off by a power-down.

## Rollback to V2

The original V2 plist files and their digests are in
`local/operator-migration/v2-agents-20260928T053148Z/`. With Rex off:

```sh
for j in supervisor servo led battery; do launchctl bootout gui/$(id -u)/com.djr3x.$j; done
cp local/operator-migration/v2-agents-20260928T053148Z/com.djr3x.*.plist ~/Library/LaunchAgents/
for j in servo led battery supervisor; do launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.djr3x.$j.plist; done
```

## Still to check live

- Saying "wake up rex" launches V3 through the supervisor.
- Voice "shut down" returns to listening.
- Charger plug/unplug sounds.
- A real mic-wedge relaunch.
- LED console animations.
- Throttle sequence playback under V3's servo limits.

Physical servo commissioning notes remain in [servo integration](SERVO_INTEGRATION.md).
