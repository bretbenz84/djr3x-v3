# Dashboard (V2 GUI port)

Status, November 16, 2026: **the joystick console works on the robot (owner's report).** No report yet
for the manual servo override or the render cost on the M2.

Status, October 14, 2026: implemented in software, not yet run with Rex live. The owner
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
| Visual feed | Newest camera frame (`FrameHub.peek`, no consumer slot), face tracker rows labelled with the recognized name, tracked objects. A face name is visibility, never the speaker. Under each face read by the [expression reader](FACE_EXPRESSIONS.md), what it shows (November 10): the held look (smiling, surprised, frowning) or neutral, with the smile reading; the FPS chip says "expressions off" when the reader is not running. Measured FPS and a STALE/NO SIGNAL chip as in V2. |
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
  - Setpoints are sent at 10 Hz. They are capped at 0.25 m/s and 60°/s (the September planned-move limits),
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

## ToF diagnostics window — November 20, 2026

Owner: "a GUI diagnostics tool that shows all the ToF data live, launchable from the menu bar helper
app." Asked for while checking sensor cabling: the two rear corner sensors had not answered for a day,
the firmware was discarding a healthy front-right one, and nothing on screen said so.

- **Open it:** battery menu, **ToF Diagnostics…**; a second click brings it to the front. A saved
  capture plays with `.venv/bin/python -m r3x.gui.tof_diagnostics --replay FILE.jsonl`.
- **What it shows.**
  - Rex from above: where each of the eight ring sensors sits, whether it answers and how far it sees,
    with both 8×8 sensors as the fans at the front and back.
  - One row per ring sensor: name, multiplexer channel, reading, the range over the last minute, a
    one-minute trace, and a count of dropouts. A stretch with no answer is a red band, so a reading
    lost while a cable is moved stays on screen.
  - Both 8×8 grids (the rear one in its own frame: its left is his right), frames a second and stalls.
  - The two front corner sensors' raw answers: what the sensor says and how sure it is, against what
    the controller passes on, and how often the ring is being read (about 110 ms with this stream
    running; 227 ms on November 20 with two silent sensors waited on every round).
  - What stands out, in words: which sensors do not answer (with channels), one that answers but is
    discarded, a slow ring, doubtful readings, depth sensor stalls, dropouts while watching.
  - The controller's own messages.
- **Where the data comes from.** The battery menu keeps the drive controller's port and passes each
  line it reads to the window, which opens nothing and sends nothing. The menu never waits on the
  window (`operator_tools/utils/line_relay.py`): a window that stops reading loses lines, and the
  meter, the joystick and the handover of the port to Rex carry on.
- **The one thing sent to the controller:** while the window is open and the meter is live, the menu
  asks for the firmware's diagnostic stream of the two front corner sensors (`tof_debug`, 12 s,
  renewed every 8 s, switched off when the window closes). It moves nothing and the firmware stops it
  by itself while the wheels turn.
- **While Rex runs** he has the port: the window says so and resumes when he shuts down. The
  Motivator console above is the view of a running Rex.
- **Structure:** `gui/tof_model.py` (Qt-free: readings, the minute kept, the wording),
  `gui/tof_diagnostics.py` (the window; the 8×8 grid is the Motivator's widget), and the battery
  menu's `_DiagnosticsWindow` (one per window; `_diagnostics_feed` and `_diagnostics_tick` serve both).
  Both windows share `gui/controller_feed.py` (decoding a line, the link line, the controller's
  messages) and `gui/diagnostics_window.py` (the feed from stdin, replay, snapshot, the look).

**Evidence.** 20 synthetic checks in `tests/test_tof_diagnostics.py`: the model on made-up controller
lines, the relay against a reader that stops and one that goes away, and the menu's wiring with the
window replaced by a pipe. The 1,952 checks chosen by the change pass; the full suite was not run.
Live and read-only on November 20 (firmware 0.2.20, nothing moved): the battery menu's own functions
fed the window from the controller for 20 s, 577 lines; the raw stream was asked for twice and switched
off after the window closed. The window, drawn off screen, showed rear-left and rear-right not
answering, front-right discarded, the ring read every 227 ms and the front-left's doubtful readings.
The battery menu was then restarted with the new item and reads the meter.

**Not yet done:** the menu click itself and the window on the robot's own screen (every check drew it
off screen). The raw stream covers only the two front corner sensors (that is all the firmware
offers), and nothing is recorded to disk.

## Sensor diagnostics window — November 22, 2026

Owner: "this sensor needs to be addressed. The menu bar app has a ToF utility menu. Can you add
something similar for the other sensors?" That day's run had failed on a gyroscope whose zero was wrong
by 19.7° a second, and nothing on screen showed it
([room navigation plan](ROOM_NAVIGATION_PLAN.md), run `e54062b9`).

- **Open it:** battery menu, **Sensor Diagnostics…**; a second click brings it to the front. A saved
  capture plays with `.venv/bin/python -m r3x.gui.sensor_diagnostics --replay FILE.jsonl`. It can be
  open beside the ToF window.
- **What it shows.**
  - A dial: which way he faces by the gyroscope (orange) and by the wheels (blue). In the middle, how
    fast the gyroscope's heading moves with the wheels still. That drift is the number that says
    whether its zero is right: green under 0.3° a second (November 20, standing: 0.05 to 0.19), amber
    to 1, red above.
  - A minute of each reading as a trace: heading by the gyroscope and by the wheels, tilt both ways,
    the compass's raw direction and field strength, each wheel's speed. A stretch where the sensor
    did not answer is a red band.
  - The sensor bus: which chips answer now (gyroscope and tilt, compass, climate, battery gauge, the
    eight ring sensors, both 8×8 depth sensors), for how long one has been silent, and how often one
    dropped out while watched.
  - The wheels: still or moving, position by the wheels, drive level, and the last move as the
    gyroscope and the wheels each measured it. Turning him with the battery menu's Drive Controls is the
    test: the two should agree.
  - The controller: its state, how long it has been running, gamepad, who is in charge.
  - What stands out, in words, and the controller's own messages.
- **What it is for.** The gyroscope's zero is taken once, when the controller starts (one second,
  standing still). If the sensor bus was down then and came back later, the firmware takes the zero
  from a single reading; on November 22 that reading came while his wake animation shook him. The
  battery gauge is also set up only at the start, so a bus that was down then leaves the battery menu
  with no charge or volts, which looks like the menu having handed the port to Rex when it has not.
  **Restart ESP32** in the battery menu, with Rex standing still, sets both up again; the window then
  shows the new drift within a few seconds. From firmware 0.2.24 (flashed that afternoon,
  [firmware notes](../firmware/djr3x_motion/README.md)) the gyroscope measures its zero again in every
  second he stands still, and the Controller panel shows that zero and how long ago it was measured.
  On it, standing still for 29 s after the flash, the heading did not move at all.
- **The compass** is shown raw and uncalibrated: whether it answers and how steady it reads, not north.
- **Where the data comes from:** exactly as the ToF window. This one asks the controller for nothing.
- **Structure:** `gui/sensor_model.py` (Qt-free: stillness, drift, the last move, the bus roll call,
  the wording) and `gui/sensor_diagnostics.py` (the window).

**Evidence.** 14 synthetic checks in `tests/test_sensor_diagnostics.py` (the model on made-up
controller lines: a steady gyroscope, a wrong zero, drift read only at rest, the last move, silent
and returning chips, a heading that starts over, a controller restart, a wandering compass, a wheel
driven without turning) and 2 more in `tests/test_tof_diagnostics.py` for the menu feeding both
windows. Drawn off screen from a November 20 recording (0.20° a second, front 8×8 silent) and from a
made-up copy of November 22's fault. Not yet opened from the menu on the robot's own screen, and not
yet seen with the real fault live.

## Compass calibration window — November 25, 2026

Owner: "add the calibration routine to the menu bar app as a clickable option? it would launch a small GUI
that shows progress of the calibration. I would ensure the motor controllers were off while I push it."

- **Open it:** battery menu, **Compass Calibration…**. Fed exactly as the diagnostics windows; it asks the
  controller for nothing and never moves the wheels. With Rex running it says he has the controller.
- **Use it:** motor controllers off, press **Start**, push him slowly round a full circle (either way). The
  ring, front up, lights each tenth of the circle (by the gyroscope) once a quiet compass reading is taken
  there; the dots in the middle are the raw readings, which should trace a ring. At a full circle (300° by
  the gyroscope) **Finish & Save** lights up. If someone knows which way he faces at the end, type it
  first (true degrees, 345 = 15° left of north): north is then pinned to it; otherwise the 13° declination
  is used. Readings taken while the pack draws over 1.6 A are left out and counted, with a reminder to
  switch the motor controllers off.
- **After saving** the dial turns into a compass rose, north up, with the direction he faces and a warning
  when the field no longer matches the calibration (steel nearby) or the motors draw current. A refused
  calibration (he barely turned, or under a full circle) says why and saves nothing.
- **Stuck sensors:** the first try (November 25) showed nothing: after a drive the drive board's sensor bus
  had hung, so the gyroscope said "not ok" and the compass and battery current repeated their last values
  (1695 mA, read as motors working). Before and during a recording the window now says when the gyroscope
  or compass is not answering, or the compass has not changed at all for 5 s, and to use **Restart ESP32** or
  switch the drive board off and on. `tools/compass.py` refuses the same cases (and no longer loses its
  recording to the drive link's own `telemetry` field).
- **Saved to** the ignored `local/calibration/compass.json`, the same file `tools/compass.py read` uses.
  The fitting and its checks are `r3x.compass` ([V2 reference](../V2_REFERENCE.md#compass-calibration-and-heading--november-25-2026)).
- **Structure:** `gui/compass_model.py` (Qt-free: recording, the circle, finishing) and
  `gui/compass_calibration.py` (the window).

**Evidence.** 4 synthetic checks of the window's model in `tests/test_compass.py` (nothing kept before
Start, the circle filling, a half turn refused, a pinned save then the heading, motors drawing current,
stuck sensors said)
and 1 in `tests/test_tof_diagnostics.py` (fed the menu's lines, asks for nothing). Drawn off screen from
synthetic readings at each stage. Opened from the menu once (stuck sensors, above); `tools/compass.py read`
against the hung board printed the gyroscope message. The robot has not been calibrated.

## Drive controls window — November 25, 2026

Owner: "remove the directional control pad from the menu bar helper app, but instead make a line item to
activate a gui that pops up to control the movement. The GUI should have the directional clickable control
pad, but also have awsd keyboard movement, show that on the GUI so it's obvious to a new person." Later that
day: "your direction pad can't do angled drives. Can you make it a circular pad so I can drag the center
anywhere and it goes that direction" — the four arrows became a round stick.

- **Open it:** battery menu, **Drive Controls…** (the pad that sat in the dropdown is gone; **■ STOP Motors**
  stays in the menu). With Rex running, or no live controller, the stick and keys do nothing and it says so.
- **Driving:** press anywhere in the round stick and drag: up is forward, down back, the sides turn on the
  spot, and any angle between mixes the two in proportion (how far out sets how fast; past the rim stays at
  the rim; the middle 8% does nothing). Letting go springs the knob back and stops. Or hold **W** forward,
  **S** back, **A** turn left, **D** turn right (arrow keys too); two at once curves, and the knob shows the
  keys. **Space** or the STOP button stops. The keys are drawn
  on the window and light while held. A speed slider sets 20-100% of the old pad's caps (0.25 m/s, 60°/s;
  60% to start). Below, what he is doing and the speed the wheels report.
- **Safety:** the window says which way it is held at once on every change and again every 0.1 s while
  held; the menu stops the wheels when it lets go, closes, loses the keyboard to another window, or says
  nothing for 0.5 s. The firmware still stops 300 ms after the last command, and its range reflexes still
  slow and stop him near things.
- **How it talks:** fed the controller's lines on stdin like the other windows; it answers on stdout with
  `{"type": "drive", "x", "y", "engaged"}`, which the menu turns into the same `drive` and `stop` commands
  the dropdown pad sent. Structure: `gui/drive_controls.py`.

**Evidence.** 8 checks of the window drawn off screen (`tests/test_drive_controls.py`: a key held and let go,
two keys and the arrows, the repeat while held, the stick at three angles and let go, a press in the middle
or outside the circle, Space and losing focus, the speed slider and lit keys, nothing without a live
controller) and 3 of the menu (`tests/test_tof_diagnostics.py`: held and let go
at the old caps, quiet for 0.6 s or closed stops, nonsense never drives). Not yet used on the robot.

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

Owner, November 16: the joystick console works on the robot. Still open: manual override on the
robot, and whether the render cost needs a lower avatar rate on the M2.
