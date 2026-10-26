# Dashboard (V2 GUI port)

Status, October 14, 2026: **implemented in software, not yet run with Rex live.** The owner
asked for V2's PySide6 dashboard in V3 (no web GUI), with manual servo and drive control,
and without Memory Banks or typed input for now. Synthetic checks and offscreen/native
renders only; see [Evidence](#evidence).

## Run it

```sh
.venv/bin/python -m pip install -r requirements-gui.txt   # once: PySide6 6.11.1, as V2
.venv/bin/python -m r3x live --gui
.venv/bin/python tools/preview_dashboard.py                 # synthetic data, no Rex
```

The wake supervisor still starts Rex headless (V2 owner ruling, August 8: no window on every
wake). `--gui` is for hand-started runs. The terminal console keeps working alongside it.

## What it shows

V2's Rebel-style layout: a 16:9 camera panel over the Systems tabs, the rigged 3D droid in
the centre, the transcript on the right.

| Panel | Source in V3 |
| --- | --- |
| Top bar | Activity badge (listening, thinking, speaking, quiet, shutting down) from the trace and the quiet flag; device lights from settings and provider start results; pack charge/current/voltage from wheel telemetry. |
| Visual feed | Newest camera frame (`FrameHub.peek`, no consumer slot), face tracker rows labelled with the recognized name, tracked objects. A face name is visibility, never the speaker. Measured FPS and a STALE/NO SIGNAL chip as in V2. |
| Actuators | Every joint of `servo_poses.JOINT_RANGES` as a percentage of its calibrated range, where the joint's output pulse is now (see [Motion timing](#motion-timing)). |
| Scene | Current goal, recognized room, faces and objects in view, indoor climate. |
| Logs | One line per trace event (periodic samples left out); the JSON session file stays the complete record. |
| Droid bay | V2's Qt Quick 3D rig, posed from the ramped output pulses: neck, lift, two-servo head pitch and side tilt, inverted visor, hero arm/elbow/hand, poker arm. Eyes, mouth equalizer and chest lamps mirror the LED commands written. The throttle arm and poker elbow/wrist are not separate meshes in V2's export and stay fixed. Without a servo controller, see [Simulated body](#simulated-body). |
| Transcript | Turns the conversation took, with the speaker attribution (`Bret?` is a hypothesis); Rex's reply grows phrase by phrase and is marked when interrupted, with what was heard. |
| Motivator | V2's joystick console with radar ring, photoreceptors, 8×8 ToF matrix, attitude, gamepad mirror and ESP32 feedback. |

## Motion timing

The Maestro ramps every move at its commanded speed and acceleration, which is why the robot
moves slowly and deliberately. The dashboard shows that ramp, not the target. Every packet
the servo owner writes (`Maestro.observers`, via `ServoOwner.on_packet`) is replayed through
a software Maestro, so each joint's displayed position follows the board's speed and
acceleration with no extra serial reads.

October 14 owner report: the first port moved too fast. It eased toward each commanded
target in about 0.3 s, so a 4000-unit wake look that takes 2.8 s on the board finished almost
at once. Physical servo lag beyond the board's ramp is not modelled. Speed 0 ("no limit") is
capped at an estimated 15000 quarter-microseconds a second.

## Simulated body

With no `servo_port` (a dev Mac), the droid still moves as the robot would, like V2's
`GUI_SERVO_SIM`. `gui/sim.py` runs V3's own `RobotServos` against a software Maestro. The
software Maestro honours the commanded speed and acceleration, so motion takes as long as on
the board.

- **What it shows:** the wake choreography, idle life (aimed at faces when the camera is on),
  the head cock on a heard turn, talking sway during playback, and the park at shutdown.
- **Display only:** Rex is not given this body. He has no pose tools, no motion flags on his
  speech and no trace rows from it, so he never believes he moved.
- **Labelling:** the Actuators tab marks the readings as simulated. Manual override works on
  the simulated joints.

## Controls

- **Stop** does what Enter or "stop" does: cancels the current reply and halts the wheels.
- **Shut Down** (confirmed), closing the window, Ctrl-C and SIGTERM request Rex's normal
  shutdown (`VoiceRuntime.request_shutdown`). During model loading the request waits until
  the conversation is up. A failed startup keeps the window open for the Logs tab.
- **Manual override** puts the servo owner in operator mode. Rex's idle motion, talking
  sway, reflexes, gestures, poses, microphone recentring and arm returns are refused
  (`OperatorOverride`). Slider values go through the same owner as `operator` requests, so
  calibrated limits, paired-joint and throttle/poker routes and clearance checks still apply.
  The newest slider values win; turning the override off discards anything pending. While it
  is on, drives that need the arm park are refused.
- **Take the wheels** (Motivator) holds the drive owner's plan lock. Rex's own, requested and
  reflex drives are refused (`DriveBusy`). The arms park before the first non-reverse move.
  - Setpoints are sent at 10 Hz. They are capped at 0.25 m/s and 60°/s (the firmware limits),
    with V2's 1.2 s/0.5 s ramps.
  - Forward stops at the front path check. Spins stop at the arm-swing check. Reverse stops
    at the firmware's rear block.
  - A spoken "stop" or the Stop button holds the wheels until the stick is released.
  - If the window stops sending (stale over 0.3 s), the worker sends zero. The firmware's
    300 ms deadman stops the base if nothing arrives.
  - Release, STOP, closing the console and shutdown end manual mode and return the arms.

While a Jeopardy game is on, the window shows V2's game page instead (October 26; see
[Jeopardy](JEOPARDY.md#the-dashboard)), without V2's second 3D droid.

Not ported: Pause and Sleep (V3 has spoken quiet mode and no sleep state), Memory Banks,
typed input, V2's pose skeleton and occlusion-zone overlay, and the
optical-flow face-box smoother.

## Structure

`src/r3x/gui/`. Importing the package loads no Qt; only `--gui` does.

- `feed.py` (Qt-free) is the trace display (it wraps the terminal console) and polls owner
  snapshots. It runs the override and joystick workers, so blocking owner calls never run
  on Qt's thread. `App.live(feed)` hands it the composed owners.
- `sim.py` (Qt-free) is the display-only simulated body and software Maestro.
- `rig.py` and `lights.py` (Qt-free) hold the pulse-to-rig mapping, the LED command mirror,
  V2's mouth equalizer, chest palettes and eye blink.
- `app.py` runs Qt on the main thread (macOS) and Rex's unchanged live loop on a worker thread.
  `dashboard.py`, `vision.py`, `transcript.py`, `logs.py`, `motivator.py`, `avatar.py` and
  `theme.py` are V2's widgets adapted. `assets/rex/` holds V2's rig, meshes and QML.
- Owner hooks added for the dashboard:
  - `LEDs.on_command` observers;
  - `ServoOwner.operator` and `RobotServos.begin_operator`/`operator_pose`/`end_operator`;
  - `DriveBase.begin_manual`/`manual`/`end_manual`;
  - `RadarLink.telemetry`;
  - `VoiceRuntime.request_shutdown`.

## Evidence

- `tools/run_checks.py`: 1214 blocked-I/O checks pass (5 skipped: numpy-only cases, run and passing
  under plain `unittest`). `tests/test_dashboard.py` (synthetic) covers:
  - transcript attribution labels, phrase growth and interruption notes;
  - the shutdown-before-ready wait;
  - LED mirror parsing and mouth fade;
  - rig rigidity and pitch/visor direction;
  - operator mode on the simulated Maestro: Rex's requests refused, limits kept, stale
    sliders dropped;
  - teleop on the simulated ESP32: plans refused, clamping, an obstacle holding forward,
    stop holding the stick, close ending manual mode;
  - LED observers seeing only written commands;
  - the software Maestro's speed pacing, and that the simulated body starts only without a
    controller and is never handed to Rex;
  - the droid trailing a real owner's commanded target until the ramp arrives, and a
    failing display observer leaving the servos unaffected.
- Simulated-body timing over 25 s: the 4000-unit wake look arrived after 2.8 s and the
  2528-unit return after 2.3 s; talking-sway steps took 0.2–0.45 s.
- Rendered frames of the simulated body over 18 s showed the wake, idle, a heard turn,
  talking sway and the park (visor closed).
- Native render of `tools/preview_dashboard.py` (synthetic data) on an M5 Pro / 24 GB dev
  Mac. Head pitch direction was checked on rendered poses and the sign of V2's rig angle
  corrected. Side-tilt magnitude (12°) is a display estimate.
- Preview cost on that Mac, 20 s with the 3D droid animating: about 0.45 of one core and
  641 MB peak RSS for the whole preview process. Most of it is Qt Quick 3D rendering with
  V2's lighting (SSAO, shadow maps, MSAA). The robot's M2 / 16 GB cost alongside Rex's models
  is **unmeasured**.

Still open: a live `--gui` run with Rex, manual override and teleop on the robot (with the
arm park, obstacle hold and stop), and whether the render cost needs a lower avatar rate
on the M2.
