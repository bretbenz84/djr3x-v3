# Drive base: LLM-directed motion

Status, September 27, 2026: **implemented and in live floor testing on the robot.** The
owner has run several floor sessions (turns, "come here", find-and-drive-to-a-door,
back up/turn around/come back). Each run's findings and repairs are recorded in the
dated sections below. The newest changes are checked in the simulated ESP32 only:
continuous sought approach, base status, the obstruction/radar context and the
hallway no-reverse rule. No firmware was changed.

## Current behaviour at a glance

- **Planning.** One `drive_base` tool call in the ordinary reply: up to six ordered steps
  (`turn`, `move`, `face`, `approach`, `find_person`, `seek`). It carries optional
  `say_before`/`say_while`/`say_after` lines, and `look_after` only when the person asked what is
  there or the next move depends on the view.
- **Talking while rolling (October 24; simulated only).** The drive runs beside speech: the
  arms tuck and the wheels start while his opening line plays, and `say_while` starts once the
  wheels do. The visor and mouth lights move with it; the head and arms keep the drive stance.
  See [Talking while rolling](#talking-while-rolling--october-24-2026).
  - `nearest_person` is always offered and always valid: with nobody in view he looks for
    the person first (toward the last voice, radar bodies, then around). The last located
    voice is remembered as a room heading, so `speaker` stays correct as he turns.
  - Nothing rewrites a plan. Getting around obstacles happens inside an approach (below);
    the text-only `gpt-5.6-terra` route planner was retired on October 3.
- **Seeking described things.** `seek` uses hosted vision looks (gpt-5.4-mini) to find
  things the detector cannot name. `approach sought` then rolls continuously toward it,
  steering by the remembered IMU heading while the next look runs in the background.
  - Each look is told which match it chose before; matches far from the remembered
    heading are ignored.
  - Arrival needs a near straight-ahead return and a reasonably large target, not a box
    size alone.
- **Obstacles.** Straight moves judge room along the body's path (`path_m`). Walls and
  corners beside the path do not block.
  - Pointed at a wall that crosses one side of his path, he first turns away from it in
    place (12 degrees at a time, at most three), then rolls. He rolls slower where a wall is
    close. If he cannot turn away he still creeps forward in short legs, and stops himself
    if the wall comes inside 22 cm (October 3; checked in the simulated base only).
  - When blocked short of the target he re-aims, then detours on an open side (at most
    twice), then reports blocked.
  - While seeking he never reverses to make arm room. Explicit turns still may.
- **Getting around things (October 3; simulated only).** When an approach is blocked with
  the target still far, he tips his head down, photographs the floor, and a hosted look at
  that picture plus the range sensors by direction picks the next leg (up to five per
  approach). Then he turns back to where the target should be and looks for it again. He
  keeps his own position as he goes, and two sightings from different places fix where
  the target is. Without a camera answer he falls back to the range sensors, which now
  include the side sensors.
- **Closing on a target.** For the last 1.2 m of an approach the host switches the
  firmware's wall-steering off (gain 0, restored afterwards), so the firmware slows and
  stops him instead of steering around what he is driving to (October 3; simulated only).
- **Safety and stance.**
  - The throttle arm parks, the hero elbow (7424) and poker arm (8000) are raised, the
    poker elbow goes up with its wrist at 1564 µs (October 10), and the head is centred,
    level and straight, right before the first wheel command. All are held until the
    drive ends; then the head is free at once and the arms walk back to their awake
    poses in the background (throttle to rest, hero elbow and poker arm to their mid
    points, poker elbow/wrist to the 45-degree pose; October 10 evening). Since October 11
    the poker arm stays raised while anything is within 3.5 ft of the left side sensors
    and the throttle stays parked while anything is within 2 ft in front, until the space
    reads clear for 2 s ([servos](SERVO_INTEGRATION.md#october-11-arms-stay-tucked-beside-obstacles)).
  - A return from the front depth matrix counts only when a neighbouring zone agrees with
    it (October 10 evening); lone zones no longer block the path.
  - Arm-swing checks gate turns. Firmware stop zones stay active.
  - Stop phrases ("stop", "whoa", "Oh, stop!") halt the wheels and silence the motion
    sounds at once.
  - Limits: 0.25 m/s, 10 m per plan (3 m until the owner found it cut trips short,
    September 27). Since October 2, Rex's own moves share these limits: the owner ruled
    that self-initiated motion needs no permission or smaller caps, only obstacle and
    time-of-flight respect, which the clearance checks enforce for every origin.
- **Honesty.** Receipts report firmware results, odometry and IMU heading. A stopped or
  blocked trip is reported as such, with the distance actually travelled.
- **Sound.** V2's drive and turn whirs loop while the wheels move (50% volume; a quarter
  of that for self-initiated moves) and pause while he talks. Blocked stops play the
  slow-down accent and arrivals the arrival chirp (not over his voice).
- **What the model can report.** The `systems_status` tool (October 21) reads
  `DriveBase.base_status` on request: battery percent, volts, current, charger,
  controller state, tilt and room climate (offset-corrected). `drive.clearance_m` with
  `clearance_meaning` covers obstructions, and `drive.radar_view` the radar ring's
  bodies.
- **Not used yet.** The QMC5883L compass and V2's compass fusion are not ported. The
  accelerometer is used only inside the firmware.

The sections below are the original design and then dated floor-run findings; later
sections supersede earlier details where they differ.

## Why V2 moved little and without intention

Reading V2 `777b38c` found separate reflex timers (wander, edge-in, welcome roll) rather
than conversational decisions; approaches that stopped after 26–40 cm because a fixed
0.55 m margin was subtracted from a front reading inflated by the faulty front-right
sensor; about a dozen mostly silent stand-downs; and 5–25 cm moves at a crawl.

## What V3 does

- **One tool, `drive_base`.** The conversation model returns up to six ordered steps:
  `turn` (left/right, degrees), `move` (forward/backward, metres), `face` a target,
  `approach` a target with a stop distance, `find_person`. Optional speech plays before
  motion, or after motion only if every step completed. `look_after` asks a camera
  question answered from a frame captured after the base stopped. Simple moves ride in
  the ordinary reply call: no extra model call. Direction words replace signed numbers
  after the model reversed "turn left" once in measurement.
- **What the model sees (`drive` context).** Clearance by direction, targets with
  robot-relative bearings (faces and objects from the head camera, moving radar bodies,
  the microphone-array direction of the last utterance, `nearest_person`), whether a
  straight approach to each camera target is blocked early, limits, displacement since
  startup and recent motion receipts, or an explicit unavailable reason.
- **Validation (CoreState).** Offered targets only; required fields per action; limits.
  Rejections return a reason the model explains in a bounded continuation; nothing moves.
- **Execution (DriveBase, via the output owner).** Parks the throttle arm and centres the
  neck (V2's interlock), runs steps one at a time against firmware `done` results, stops
  at the first incomplete step, sends `stop` on cancel, and returns the arm to rest in the
  background so speech need not wait. Receipts report firmware results, odometry and IMU
  heading, never visual confirmation. A refused or incomplete plan yields one
  continuation in human turns; self-initiated plans fail silently.
- **Moving on its own.** `drive_base` is offered in arrival, lull and ambient opportunities
  with smaller limits, and a new `unseen_presence` opportunity offers only `drive_base` or
  silence when the radar reports a moving body and no face has been visible for 5 s
  (90 s spacing). Opportunities share the existing six-per-five-minutes budget. After an
  explicit stop, the existing 120 s proactive hold applies. Faces first tracked within 6 s
  after base motion are not treated as new arrivals, except after investigating.
- **While the wheels run.** Speech detected during motion (including motor noise) does not
  yield the plan; the capture is still answered afterwards and a spoken "stop" halts at
  once. A queued self-initiated plan is not cancelled by re-identified faces from its own
  moving camera. V2's gamepad hand-back (4 s idle) is pushed at connect.
- **Local reflex only.** V2's back-off: a radial-corroborated intrusion inside 25 cm
  held three 1 s ticks backs off up to 30 cm within rear room (no arm park for straight
  reverse, per V2). The firmware keeps its reflex braking, 500 ms watchdog and 300 ms deadman.

## Limits (owner-approved September 27; self-initiated equalized October 2)

| | Requested and self-initiated |
| --- | --- |
| Top speed (also pushed to firmware `max_lin`) | 0.25 m/s |
| Firmware leg | 1.5 m |
| Plan distance total (moves and approaches) | 10 m (was 3 m) |
| Turn per step / total | 360° / 720° |
| Steps | 6 |

Until October 2, self-initiated plans were held to 0.18 m/s, 0.75 m legs, 1.5 m in total,
180°/360° and four steps. The owner rejected those caps for Rex's own goals ("go over to
something and reach out"). The model now sees `max_move_m` (the plan total) instead of the
leg length: live October 1 it read the 1.5 m leg as the most it could move and answered
"back up five feet" with 1.5 m.

October 3: his own vision seeks get 4 hosted looks per plan instead of 12. A home-range
limit on self-initiated moves was added and removed the same day (owner ruling: he never
places Rex near a drop, and his own moves are bounded only by obstacles and ToF limits).

A move longer than one leg runs as several legs with clearance rechecked between them.
Turns run at 45°/s (V2 overshoot grows with rate), with one IMU-verified correction
of at most 45° at 20°/s. Host stand-off is 0.35 m in front and 0.30 m behind.

## Safety gates

Refused, with a spoken reason, when telemetry is older than 0.5 s, the gamepad owns the
base, the firmware reports estop/fault/comms loss, the base is charging (flag or at least
14.0 V), the 8x8 depth matrix is stale, or every ring sensor errors. Spins use V2's swing
check about the rear axle including the left arms; unknown sensing refuses. Moves are
clamped to measured clearance; unknown rear clearance refuses reversing. The front uses
per-band matrix minima (left/centre/right, second-nearest floor-rejected zone) plus valid
radial sensors, and flags the faulty front-right radial rather than trusting it.

### Charger release investigation — October 1, 2026

V3's 13.6 V voltage backstop repeated an old surface-charge failure: an unplugged,
freshly charged pack can remain around 13.61 V for minutes after the firmware clears
`charging`. The host now uses 14.0 V, matching current V2 firmware and the battery
menu. The next fresh telemetry frame can release the host gate; no host grace timer
is added. This correction affects host-directed driving, not standalone gamepad driving.

Firmware still requires 90 consecutive 1 Hz discharge samples after its smoothed
current reaches 100 mA. Smoothing or interrupted discharge can extend that delay.
The former eight-second guard was lengthened after startup load mimicked an unplug
and the base moved with the cable attached (August 7). Simply shortening that guard
would repeat the known failure; net battery current does not directly sense the cable.

Source firmware `0.2.3-gamepad-unplug` provides a faster explicit declaration:
**after physically unplugging**, hold L1+R1 for two seconds with sticks centered,
D-pad/Start/B released; release the shoulders before driving. The next battery tick
(up to one additional second) accepts it only with fresh voltage/current readings
and no definite charger evidence. This cancels queued motion, keeps manual ownership,
and preserves estop/fault. The source exists; the installed board version was not
queried or flashed during this investigation. V3 has no spoken unplug-declaration tool.

Checks use the simulated ESP32 host path and V2's existing compiled battery/gamepad
test with fake INA226/NVS. Real unplug latency and loaded firmware remain unverified.
Reliable fast *automatic* cable detection needs an independent charger-present signal
or further measured evidence that distinguishes a connected supply from battery load.

The operator also reports L1+R1 did not release standalone gamepad driving; the host
threshold correction cannot fix that path. Existing battery diagnostics on September
30 record latch restoration at 21:25:31 and release at 21:27:03 (about 91 seconds),
with `estop` reported at 21:26:58. That does not establish the state during the reported
shortcut attempt. No shortcut request/refusal was found in the inspected battery log;
the menu releases the port while Rex runs, so absence is not proof of old firmware.
Confirm the installed firmware version and capture the shortcut's request/result
before claiming that failure resolved. The hold fires once; a rejected attempt needs
shoulder release and a new hold. Successful charger release does not clear estop/fault.

Live diagnostic follow-up, October 1: the installed board's `hello` reports
`0.2.3-gamepad-unplug` with `chg_assert` capability, ruling out an uninstalled shortcut.
At inspection it reported `charging:true`, about 14.36 V, and `gp.connected:false`.
Only `hello` was sent (no reset, flash, motion or charge assertion); the diagnostic
used the shared owner lock and returned serial access to the battery menu afterward.
The operator was away, so no physical unplug/button reproduction occurred. These
current readings do not explain the earlier failure. Capture connection, hold request,
battery acceptance/refusal and estop state during a reproduction before changing
the firmware guard.

## Radar

The ESP32-S3 LD2450 ring is optional (`radar_port`). Bodies must persist in at least three
of the last 1.5 s of frames; V2's cross-seam merge is kept. They appear as targets with
bearing, range and movement; `find_person` looks at an optional chosen target first, then
radar bodies, then the voice direction, then ±90° and 180° sweeps, stopping when a face
appears. Approaching a radar body turns first, then approaches a face the camera actually
sees, or moves straight toward the radar range if none. Radar is never identity.

## Stronger model: measured, then used narrowly

`tools/check_drive_planning.py` sends synthetic rooms through the production Session and
tool schema with simulated receipts (nothing moves), paced 5 s apart. Heuristics flag plans
for reading; they do not prove judgment. Final production-model results (2 repeats):

| Case | gpt-5.4-mini low | + Terra route planner |
| --- | --- | --- |
| Spin around, back up, come here | 2/2 | 2/2 |
| Turn left | 2/2 (earlier wording: one reversed) | 2/2 |
| Come here, caller behind | 2/2 | 2/2 |
| Closer look, path blocked by a box | 0/2 | 2/2 |
| Closer look, clear path | 2/2 | 2/2 |
| Question about driving (no motion) | 2/2 | 2/2 |
| Told to stay put, cat appears | 2/2 | 2/2 |
| "Drive five metres" | 0/2 (rejected, explained) | 1/2 |
| Voice from the side | 2/2 | 2/2 |
| Half-metre square | 2/2 | 2/2 |

For the blocked path alone, mini at medium reasoning routed 0/3 (turns of 3–18°) and
`gpt-5.6-terra` (low) 3/3 (left 45°, forward 1 m, face the dog). (Retired October 3: see
"Looking for a way around" below.) So a route planner ran
only when a validated plan would drive toward a target whose straight path the base
sensors show blocked; geometry triggers it, not wording. It returns steps only, validated
by the same state owner, and runs while the before-motion line plays. Measured cost:
about 1,040 input and 225 output tokens per call; at most 20 calls per run; failure keeps
the original plan, whose block is then reported. Self-initiated moves across all measurement runs: an
arrival 3.2 m away always got a spoken greeting, not a roll (0/8); a moving radar body
behind always prompted a turn (9/9). Evidence folders: `local/validation/drive-planning-*`.

## V2 extraction

Source HEAD `777b38c803782cd4657e29ff544dfe7c1ca958ad`, clean tree; firmware untouched.
See [V2_REFERENCE.md](../V2_REFERENCE.md#drive-base-and-radar-extraction--september-27-2026).

## Verification

45 new blocked-I/O tests (645 total) cover the handshake/config/heartbeat, missing
capabilities, IMU-verified turns and correction, clearance clamping, blocked moves,
swing refusal, gates, stop, face/person/object/radar approaches, radar-first search, arm
park and return, the back-off reflex, session speech ordering, rejections and receipt
continuations, direction words, legs, look-after frames after motion, route-planner
escalation and fallback, grounding, settings, leases, arrival reframing, startup notice,
the unseen-presence opportunity, and a self-initiated move that survives the face-track
churn its own motion causes (verified to fail without that rule).

## Floor test script

Run only when the owner chooses to. Hardwood room or hallway, no drop-offs. Keep the
gamepad in hand: any stick push takes over, **B** is emergency stop. Stop V2 first.
`drive_port` and `radar_port` are set in `local/settings.toml` on the robot.

Start with `.venv/bin/python -m r3x live --record-evaluation`. Expect no wheel motion at
startup. Then, one at a time:

1. **"Turn left."** About 90° left, with the turning sound; the head stays level.
2. **"Back up a little."** A short reverse.
3. **"Back up a bit, turn around, and come towards me."** All three parts run in order;
   ends facing you about a metre away.
4. From about 3 m away: **"Come here."** Reaches about 1 m without stalling.
5. **"Turn left, find a door and drive to it."** He finds a door and rolls toward it
   without stopping for each look, goes around things beside the path, and says briefly
   that he arrived or why he stopped. Trips over 10 m may need **"Keep going."**
6. Mid-drive: **"Oh, stop!"** Wheels and whir stop at once.
7. **"Do you sense any obstructions?"**, **"What do you see on your radar?"**, **"What's
   your battery level?"** Answers come from live readings, or say plainly what is
   unknown.
8. **"Stay right there,"** then chat. He should not drive himself.
9. Slowly put a hand about 20 cm in front of him. He should back up once.

Report which failed and the session-log path. The owner-visible success is purposeful
motion; logs alone cannot establish clearance, overshoot or social welcome.

## Remaining acceptance and limits

Updated September 27 after the floor runs.

**Seen working on the floor:** turns with IMU-measured angles, approaching a person to
about 1 m, finding a door and reaching it (in stop-and-go legs before the continuous
approach), going around a hallway corner, and a plain "Stop." halting within 0.2 s.

**Still unmeasured or unverified:**
- The continuous sought approach, including motion blur on looks while rolling.
- The hallway no-reverse rule and the arm-swing geometry now that the arms are raised
  while driving. V2 estimates of arm reach sometimes shortened turns (a 180° turn became
  37°).
- The matrix phantom returns, the back-off threshold and radar persistence.
- Microphone contamination while driving. Motor noise still produces unreliable "Oh."
  transcripts.
- The accuracy of the controller's battery percent, combined M2 load and the social
  welcome of self-initiated motion.

**Not implemented:** a map, localization, cliff sensing, obstacle memory, compass
heading and no-drive rooms. Room recognition and an `explore` goal landed October 11
([room recognition](ROOM_RECOGNITION.md)); they know which room, not where in it. The V2 battery menu app keeps its port
only while V3 is off; V3 battery/base menus remain future work.

## First floor run and repairs — September 27

The owner ran the robot: moves, turns and approaches executed. Findings and changes:

- **Wide-angle camera.** An IMU-verified 20° turn moved a face 22.5% of the frame, so
  the head camera sees about ±45°, not V2's ±25°. Distances had been read as 3 m instead
  of about 1.25 m and bearings roughly halved. `HALF_FOV` is now 45°; camera bearings
  add the current neck angle.
- **Stopped 4–5 ft away.** The approach also enforced personal distance from the front
  ToF sensors, which saw a seated person's knees or chair, not the face. Person
  approaches now use the calibrated face range, a 0.22 m obstacle gap and a 0.8 m
  default stop; each approach receipt records clearance where it stopped.
- **Clipped "You can come here".** The 256 ms pre-speech buffer missed onsets the
  detector recognized late: 8 of 41 recent captures began at speech level. It is 1 s.
- **Cut-off speech.** Owner rule: audible speech finishes unless explicitly stopped.
  Freshness guards (audience, observation, store revision) and resumed speech no longer
  cancel once audio starts; "stop", "shut up", "stop talking", "be quiet" and similar are
  the explicit stops. Echo gating still hides speech during playback, so a mid-sentence
  spoken stop works only with isolated audio.
- **Little head motion.** Talking now anchors the neck toward the addressee (attributed
  speaker's face, else the microphone-array bearing, else the largest face, at most 40°)
  and sways about ±7° (V2's ±3.4° barely read). V2's idle head wander is not restored.
- **Microphone direction** gave a bearing on 3 of 8 turns; short commands often have too
  few loud samples, and it then reports unavailable rather than guessing.

## Second floor run and repairs — September 27

- **Ignored speech for about 30 s.** Live Silero flagged 2 speech frames in a capture
  where a freshly loaded model flags 30; its recurrent state had gone numb after long
  echo and servo noise. The runtime now resets it when playback ends and every 4 s of
  quiet with no utterance pending (the 1 s pre-roll covers onsets).
- **Wrong clock, twice.** The detector never saw the framed clock photo; the model
  substituted the wall clock on the other side. Guidance now forbids substitution and
  describes the ~90° image. A plan may turn with `look_after`; the fresh image comes
  back and one follow-up `drive_base` plan may target what is now visible.
- **"Target not detected" right after parking.** Every head/base motion restarts object
  tracks. Re-finding now waits up to 2.5 s for post-motion detections, matches by label
  near the odometry-predicted bearing (never a same-label object elsewhere), and turns to
  where the target was seen before giving up.
- **"I'm on it" after nothing moved.** Refusal and incomplete-motion continuations carry
  an explicit note to say plainly what happened.
- **Turn-around refused twice.** A swing-blocked turn now backs up (or pulls forward) up
  to 0.5 m and retries once, as the owner had to direct manually.
- **Turns that backtracked.** Host IMU corrections are removed: telemetry heading drifted
  (right turns read half-size) while the firmware's own loop turned correctly. IMU and
  odometry angles are both recorded. "A little" means about 45° (owner).
- **Neck off-centre and head cocked up while driving.** `NECK_CENTER` and `DRIVE_HEAD`
  (lift/tilt) are single constants applied at every drive, awaiting console values.

## Third run repairs — September 27

- **"I need a direction" with a −132° bearing measured.** Motion-flagged captures had
  their whole voice observation replaced, discarding the microphone bearing; it is now
  kept (identity stays void). Bearings are invalidated only when the microphone arm
  (channel 7) or the wheels move, not by talking sway or other arms, which had left
  short phrases with zero samples. Radar only tracks moving bodies; a seated owner gave none.
- **Asked Bret's name, then refused his existing profile.** 15 of 19 captures were
  motion-flagged, voiding voice identity; the 1 s pre-roll made it worse. Motion now
  voids identity only when it overlaps at least 30% of voiced speech.
- **No park at shutdown.** Quitting mid-sentence left the speech worker blocked in an
  ElevenLabs read, so shutdown skipped all hardware release. A stop now closes the
  in-flight TTS stream, and hardware (wheels, servos, LEDs) is released even when a
  worker is stuck; only models stay loaded until the process exits.
- **Owner ruling: motion lowers confidence, never discards evidence.** Motion-flagged
  speech is always embedded and matched, with the identity bar raised from 0.50/0.07
  to 0.55/0.10 (score/margin). It never creates or extends a voice track and never
  enrolls; transcripts and bearings are kept. Playback-echo captures are still rejected.

## Vision seek and sharp looks — September 27

- **Sharp deliberate looks.** Inspections and looks after driving now send a fresh frame
  resized to 1280 px as JPEG at high detail. They were 480 px PNG thumbnails at low detail,
  where a framed photo on a wide-angle view is a few pixels. Background scene snapshots
  stay 480 px/low detail.
- **`seek` and `approach sought`.** For things the object detector cannot name (doors,
  photos, particular objects) the model adds a `seek` step with a short description.
  A separate bounded locator call (gpt-5.4-mini, structured output) returns the thing's
  image box or "not found"; the drive owner turns to its bearing, or sweeps 80° (preferred
  side first) for up to four looks. `approach sought` re-locates between legs of at most
  0.8 m and stops when the target fills 45% of the view, near it at the front stand-off, or
  at the plan budget. At most 12 locator calls per plan and 60 per run.
- **Measured on a saved robot frame:** doorway and laptop located at the correct image
  positions; an absent clock photo returned not found. About 2 s and roughly 820 input /
  150 output tokens per look. Live accuracy while driving is untested.

## Door run repairs — September 27

- **Stopped 15 ft short of a door and said "Here I am."** A front obstacle slightly right
  ended `approach sought` as arrival. See the hallway repairs for the arrival rule.
  Otherwise he re-aims precisely (a wall beside
  the door with the heading a few degrees off), then goes around on an open front band:
  turn 30° (55° if the obstacle is still in the path), move up to 0.6 m, face the
  remembered heading and re-locate. At most two detours; then `blocked` with
  `target_still_far`, which the model must explain.
- **Zig-zag between doors.** Re-locates for "door" chose three different doors. The
  locator now receives its earlier match note and expected image position, and a match
  more than 25° from the remembered IMU heading is treated as not seen.
- **Arm pulled back, then nothing.** The throttle-arm park and neck centring now happen
  right before the first wheel command, not at plan start; a plan that does not move
  makes no gesture.
- **Simulated only.** Checked in the fake base with shaped front sensors (box slightly
  right, wall ahead-right, wide wall, alternating locator). Real sensor cones, band
  geometry and the detour clearance rule are unmeasured on the floor.

## Hallway repairs — September 27

- **Refused an open hallway; "got there" twice, 13 ft short.** At the stop the centre
  matrix band read 3.3 m and the right 3.5 m, but the front-left radial read 0.67 m off a
  protruding hallway corner. Straight moves took the nearest of all front returns. They now
  use `path_m`: the matrix counts in full; a 22.5° radial counts in full unless the matrix
  band on its side is clear beyond its return, which places the return outside the matrix
  view and then counts only if inside the body's path (half-width 0.27 m + 0.05 m).
  `front_m` is still reported and still bounds the person-approach controller.
- **Door box 40% wide from 4 m.** Locator boxes are not distances. Arrival now needs the
  centre band within 0.4 m of the stand-off with the target aimed and at least 30% wide;
  a box alone ends the approach only at 60%. A corner beside the path never makes arrival.
- **Simulated only.** Replay of the recorded clearance readings plus shaped-room runs.

## Turn-around and stop repairs — September 27

- **"Back up, turn around and come towards me" lost its last part.** With nobody in view,
  `nearest_person` was not offered, so the model dropped the approach and later claimed it.
  It is now always offered (`visible_now` false when nobody is in view) and is valid
  after a turn, `find_person` or `seek` step in the same plan. Guidance requires every part
  of a stacked request, and `recent_motion.steps_run` lists exactly what ran.
- **"Oh, stop!" ignored while driving.** The stop fast path matched whole utterances
  exactly. It now accepts stop phrases (stop, halt, whoa, freeze, cancel, shut up, be quiet,
  hush) repeated and wrapped only in a few exclamations or "Rex"; anything else is ordinary
  speech. Two shouts transcribed as an untrusted "Oh." over motor noise remain missed.
- **Near-collision on a person approach.** The 0.22 m knee margin applied to any front
  return. It now applies only when the return is within 0.8 m of the face range;
  furniture short of the person keeps the 0.35 m margin.
- **Cancelled plans reported 0 m.** The interrupted step is recorded with its odometry.
- **Simulated only**, except the stop timing: the plain "Stop." halted the base 0.13 s
  after its transcript on the floor.

## Motion sounds and drive arm stance — September 27

- **V2 motion sounds.** Turn and drive whirs loop for each wheel command, the slow-down
  accent marks a blocked stop and the arrival chirp a finished approach (V2_REFERENCE).
  A stop command silences them at once. They play through the speech sink without LED
  lip sync and never raise echo risk, so the microphone still hears "stop".
- **Arm stance while driving (owner).** The pre-drive park also raises the hero-arm
  elbow fully (7424, higher is up) and the poker arm to 8000, plus (October 10) the poker
  elbow up with the wrist at 1564 µs, held with the throttle and head until the drive ends.
- **IMU.** Gyro yaw measures turns and remembers the sought thing's heading. The
  firmware's accelerometer pitch/roll and the QMC5883L compass are not used; V2's
  compass fusion and calibration are not ported.

## Hallway loop repair — September 27

- **Backed up instead of going around; looped.** The door stayed 5–16° left while a
  hallway corner sat by the left arms. Every re-aim toward the door hit the arm swing
  check, and the swing escape reversed up to 0.5 m before retrying. During `approach
  sought` turns never escape by reversing. Re-aims happen only past 15° (or when blocked
  straight ahead); a refused aim keeps driving forward while the path is open and aims
  again once past; a refused detour turn reports blocked. Explicit turns keep the escape.
  Owner: hallways are tight left and right but open forward, and the firmware steers.
- **Simulated only.** Tight-walls runs command no reverse; the previous code reversed.

## Continuous approach and base status — September 27

- **One continuous motion toward a sought thing.** `approach sought` no longer drives
  0.8 m legs with a stop for every hosted look. It streams velocity commands while the
  next locate runs in the background (at most one every 2 s), steering toward the
  remembered IMU heading that each locate refreshes (±0.2 rad/s). It stops for the front
  stand-off (braking from `path_m`), a firmware block, the travel budget, a target more
  than 40° off, two failed looks, or a box filling 60% of the view; then the existing
  arrival, re-aim and detour logic decides. A look in flight when the wheels stop is
  reused. Receipts report `continuous_runs`. Firmware hallway assist still applies.
- **Base status for honest answers.** `drive.base_status` carries the controller's
  coulomb-counted battery percent, volts, current, charger state, controller state/fault/
  error count, gamepad, IMU pitch/roll and room temperature/pressure/humidity, with -1 or
  absent sensors as null. Superseded October 21: no longer in every context; the
  `systems_status` tool reads it on request.
- **Simulated only.** Motion blur on looks while rolling, steering feel and the reported
  battery percent's accuracy are unverified.

## Talkative arrivals, noise replies, surroundings — September 27

- **Paragraphs on arrival.** The model attached `look_after` to every approach, so each
  arrival became a scene description. `look_after` is now only for a person's question
  about what is there or a next move that depends on the view, and a look after driving
  is answered in one short sentence unless the person asked what he sees.
- **Replies to noise.** Motor noise transcribed as an untrusted "Oh."/"Okay." yielded a
  real question (the battery question) and got "Mm-hm." Untrusted transcripts are kept
  but never answered; the model answers a yielded question instead or stays silent.
- **Obstructions and radar.** `drive.clearance_meaning` explains the time-of-flight
  readings and `drive.radar_view` lists radar ring status and bodies, so "any
  obstructions?" and "what's on your radar?" are answered from live data.

## Distance limit and plan validation — September 27 (Jeremy run)

- **3 m cap raised to 10 m for requested plans.** It stopped three door trips at 3 m and
  refused "a foot, then turn right and go ten feet". Trips normally end on arrival, an
  obstacle or a stop phrase. (Self-initiated motion kept 1.5 m until October 2.) Receipts now say
  `distance_limit`, and guidance asks for plain words (no "budget" or "stand-off").
- **Fixable refusals no longer refuse.** A seek whose description came in `target` keeps
  it as the description; `find_person` may aim at `nearest_person` with nobody in view.
- **Also observed:** the voice-launched V3 run started and "shut down" returned to the
  supervisor; two short unrequested reverses were the close-approach back-off reflex.

## Left turns with tucked arms — September 27

- **Left turns refused or shortened beside walls.** The host swing check still used V2's
  arms-out reach (0.73 m at 45° and 135° left), so a 180° turn stopped at 44° and door
  re-aims failed with a wall about 0.4–0.9 m away. The drive stance now tucks those arms
  (owner). Plans whose servo owner parks the arms use a tucked reach of 0.33 m (6 cm past
  the ring, an estimate); unknown arm positions keep V2's reach. A replay of the logged
  readings goes from refused to the full 180°. Firmware does not gate spins (V2
  `control.cpp`), so no firmware change or flash was needed; gamepad support is untouched.

## Voice-only approach and going around to a person — September 27

- **"Come towards my voice" needed a face.** The microphone bearing only turned him; with
  no face in view the approach gave up. Now, after facing the voice, he rolls up to 2 m
  on that heading (IMU hold, path clearance, stand-off braking). As soon as a face comes
  into view within ±20°, the normal face approach takes over and stops at about 1 m.
  Without a face, the receipt says he moved toward the voice without seeing anyone.
- **A trash can stopped "come to me".** Person approaches had no way around; the can was
  0.31 m ahead-left with the right side clear. When blocked with the person still more
  than 0.5 m beyond the stand-off, he now uses the shared detour: open side, 30°/55°,
  0.6 m. Then he turns back to the person's remembered heading, re-finds them and
  continues, at most twice. The sought approach uses the same detour step.
- **Simulated only.** Voice direction on the robot is noisy (the log showed 129° then
  31° for the same speaker), so a voice-only leg can head somewhat off; obstacles and the
  face handover bound it.

## Re-finding a person lost mid-approach — September 27

- **Gave up when the camera lost him.** After a detour, firmware steering turned the base
  (123° of turning in one approach) and the person left the frame; after 6 s unseen the
  approach ended "lost". The approach now remembers the person's absolute IMU heading at
  each sighting. After 2.5 s unseen it stops and looks with the head first (owner: less
  effort): toward that heading, then 30° either side, within ±40° of neck travel. If
  that fails it turns the body toward the heading. On success it faces the person with
  the body, recentres the head and continues, up to three times per approach. Receipts
  list `refound_by` (`head_look` or `body_turn`).
- **Simulated only.** The sim now turns the camera with the head; tests nudge the base
  50° (head look) and 100° (body turn) mid-approach and still arrive.

### Owner-directed current release — October 1, 2026

Superseding the unchanged-firmware investigation above, the owner reports a ~7 A
charger, ~0.01 A at plugged-in taper, and 1-3 A discharge while unplugged/asleep
(more awake), and requests a shorter automatic release. V2 firmware source
`0.2.4-current-unplug` now requires three consecutive fresh 1 Hz samples at or
above 500 mA discharge, plus valid voltage below 14.0 V. This replaces 90 samples
at 100 mA. Near-zero/inflow, high voltage or missing readings reset the exit count.
Entry also uses fresh samples; accepted release resets the display EMA, avoiding
old charging history delaying host release or re-latching. L1+R1 remains available.

The actual battery.cpp native test passes with fake INA226/NVS: 1 A and 3 A release,
near-zero/subthreshold hold, missing-sample and taper reset, surface-charge retention,
replugging and voltage veto, plus the existing explicit-assertion contracts. These
are synthetic checks. The current criterion cannot distinguish a physically absent
charger from a connected supply outdrawn by the load. No flash or live acceptance
was performed for this change; the installed version remains 0.2.3.

Full Arduino/Bluepad32 compilation passes with motion, gamepad, radial ToF and
matrix ToF enabled: 811,977 bytes program storage, 90,604 bytes globals. No upload
was requested or performed. Build output is `/tmp/rex-current-unplug-build`.

### Deployment — October 1, 2026

At the owner's explicit request, uploaded the tested full hardware build at 115200
baud to the configured drive ESP32. Upload hashes verified; live hello reported
`0.2.4-current-unplug`. Stationary telemetry: charging true, 14.367 V, +6 mA in
firmware convention, IMU/magnetometer OK, matrix ready, existing front-right radial
quarantine active. `comms_lost` is expected with no driving host heartbeat. No
motion or charge-assert command was sent. The battery LaunchAgent was stopped for
upload, then restored successfully and confirmed holding the serial port again.
This supersedes the pre-upload status above. Physical unplug timing and L1+R1
reproduction remain untested because the operator is away.

## October 3: pace

Owner request: his wheel moves "could be slower if desired... some variety in speed would
also help, even while turning". Each drive step takes an optional `pace`: `slow` (60% of
the approved 0.25 m/s and 45 degrees per second), `easy` (80%) or `normal`. It only scales
downward; clearance, limits and firmware caps are unchanged, and turn timeouts lengthen
with the slower rate. The model chooses it per step and is told to vary it. Checked with
the simulated base only: confirm on the robot that a 27 degree-per-second turn does not
stall on carpet.

## October 3: hallways, the lost search and turn sizes

Owner's run `d4634610d3c34bcda70ccfa1a1a893b3` (about 18 minutes, real hardware): finding
and driving to the microwave through a tight kitchen. Findings, then what changed. **All of
it is checked in the simulated base only; none has run on the robot.**

**Live reading.** The owner left Rex where he refused "move forward five feet" and offered
the sensors. Read-only: the controller lock was taken so the battery menu released the
port, only `hello` was sent (no heartbeat, configuration or motion), 41 telemetry frames
were read and the port was returned. Front-left radial 4.0 m and the centre and left of the
8x8 matrix open beyond 3.5 m; front-right radial 0.40 m; the two rightmost matrix columns
0.54-0.66 m; sides 0.26 m (left-back) and 0.43 m (right-front). So straight ahead was open
and a wall crossed the right edge of his path about 20 degrees off his heading. The host
computed 0.40 - 0.35 = 0.05 m of room and refused. With those readings the firmware's own
steering assist would have been nearly neutral (about -0.08 rad/s: the close left-back
return cancels the push away from the front-right wall), so the firmware alone would not
have freed him.

- **Hallways blocked motion.** Two causes. The matrix side bands counted every wall they
  saw, so even pointed straight down a hallway a move was shortened to the range at which
  the outer columns met the walls. And a wall crossing one side of the path refused the
  move outright. Now:
  - `matrix_path` counts a matrix zone only when it lies inside the corridor the body
    sweeps (half-width 0.27 m plus 0.05 m), using each column's angle across the 45 degree
    view. `path_left_m`, `path_center_m` and `path_right_m` say which side limits the path.
  - Before a forward move, when one side is short within 0.8 m while straight ahead and
    the other side are at least 0.3 m more open, he turns away in place: 12 degrees, re-read,
    at most three times, never a reverse (owner: "it would be good if he oriented himself
    to avoid the walls before he moved").
  - Where a side return is within 0.35 m or a front corner within 0.5 m he rolls at 60% of
    the step's pace ("in tight clearance hallways he should move slower").
  - If the turn is refused (arms beside something) and only a side return limits the path,
    he is no longer refused: legs of at most 0.6 m at the slow pace, the firmware assist
    steering, and the host stopping the leg when the wall comes inside 0.22 m
    (`wall_beside_path_too_close`). An obstacle straight ahead still shortens or refuses.
  - Person approaches now judge room by the same path reading, not the nearest front
    return of any kind.
- **Went back to the microwave when told "come towards me".** The model named the visible
  face with characters missing from its id. Validation treated any unrecognized approach
  target as "the thing sought earlier". A damaged id is now repaired only to the single
  offered target it abbreviates; an unknown target is refused; "sought" resumes a search
  only when named or right after a seek in the same plan. Reaching the sought thing ends
  the search, so `last_sought` is empty afterwards.
- **A dictated route was replaced.** "Slight left, forward a few feet, slight right, then
  on" ran as the route planner's own 30 degrees and one metre because the direct path to
  the microwave was blocked. The planner no longer runs when the plan already contains
  turn or move steps.
- **Turned the wrong amount looking for a target.** Twenty logged turns: the IMU read the
  commanded angle plus 3-10 degrees; wheel odometry read up to twice that on left turns
  (30 commanded: IMU 33, odometry 58; 90: 94 and 148). The owner's remarks matched the
  IMU. Predicting where an offered target should be used odometry, so after a left turn
  he looked tens of degrees off (an 86 degree turn for a target about 30 away). It now
  uses the IMU heading, odometry only without one.
- **Turns too small.** "Slight left" became 12-15 degrees and "turn left" 30. The tool
  text now says a little/slight/a bit is about 45 and never under 30 without a number, a
  plain turn is 90, and a correction changes the next turn by a visible amount.

Not changed: the consistent 3-10 degree turn overshoot (firmware coast), and two stops
where the front-right radial read 0.07-0.08 m; whether those were real edges is unknown.

V2 firmware `control.cpp` was read at `fb96f08` for the steering assist and stop zones
(the V2 working tree has unrelated uncommitted battery changes); nothing was copied or
flashed.

Verification: 13 new simulated checks (hallway straight and angled, the logged readings
with and without room to turn, a wall closing during a guarded leg, an obstacle ahead,
wheel slip, arrival ending the search, damaged and unknown target ids, a dictated route).
Unmeasured on the floor: the column geometry against real walls, whether 12 degree turns
and the slow pace stall on carpet, and how the firmware assist behaves on a guarded leg.

## October 3, second run: "come here"

Owner's run `aa6782c09e1c42bb98390c4ba44c27be` (2.5 minutes, real hardware, with the
midday repairs loaded): "this needs to work, but currently still feels broken". From the
log, then the changes. **Simulated base only; not yet run on the robot.**

- **"Can you come over here to me?" was refused.** The model asked to approach
  `nearest_person`; nobody was in view, and validation rejected the plan
  (`NearestPersonNeedsTurnOrSearchFirst`) although the voice had been located 88 degrees
  to his left. The rule is removed. Facing or approaching `nearest_person` with nobody in
  view now runs the person search itself: the last voice first, then radar bodies, then
  90 degrees either side and behind; only after that does it report
  `no_person_found_after_looking_around`.
- **Turned left a second time and faced away.** He turned 88 degrees to the voice and a
  face was straight ahead. "Turn that way and come towards" was spoken while that turn
  was still running, so it carried no voice direction, no `speaker` target was offered,
  and the model repeated the "88 degrees left" it had just said. The approach then gave
  up at once (`target_not_currently_detected`). Now the drive owner keeps the last
  located voice as a room heading for two minutes and offers `speaker` relative to where
  he faces now (it reads "straight ahead" in the simulated repeat of this case), each utterance's
  bearing is applied once, the instructions say not to add a turn from an angle mentioned
  earlier, and an approach that finds nobody looks toward that voice before giving up.
- **The firmware steered him away near the person.** Its assist steers toward the open
  side whenever a front return is within 0.9 m, which near a target is the target. The
  host now sends `config assist_gain 0` when the target is within 20 degrees of straight
  ahead and either its range is within 1.2 m of the stop distance or something is within
  1.2 m straight ahead, and restores the gain when the approach ends, the plan ends or the
  link closes. The firmware's slow zone, stop zone and its push away from a wall inside
  13 cm are untouched. The default gain (2.0, V2 `calib.h`) is also pushed at every
  connect, so a run that died mid-approach cannot leave steering off. No firmware change;
  V2 `config_params.cpp` at `fb96f08` shows a partial config applies at once without
  interrupting motion.

Verification: 5 new simulated checks (come here from out of view, the voice heading after
turning, the logged extra turn, nobody found, the steering switch and its restore).
Unmeasured: how the base tracks straight with steering off on the real floor, and the
voice heading's accuracy (the array's bearing varied by tens of degrees in earlier runs).

## October 3, third run: distance from people and the voice target

Owner's run `30cbd0250b6648d48c663fc66ec7901e` (11 minutes, real hardware, with both
earlier repairs loaded). Drive-related findings; the conversational ones are in
[implementation status](IMPLEMENTATION_STATUS.md). **Simulated base only.**

- **Stopped at the owner's knees** ("you're a little too close to me"). The model asked for
  0.8 m; the approach ended blocked at 0.21 m from the nearest front return with the face
  about 0.7 m away. The default stop for people is 1.0 m by face (was 0.8) and the nearest
  a front return may be on a person approach is 0.32 m (was 0.22). The 0.22 m hold for a
  wall beside the path is unchanged.
- **"Too close" produced a 45 degree turn.** The tool text said the owner means about 45
  degrees by "a little"; the phrase matched "a little too close". It now applies only to
  requested turns, and "too close / back off / give me room" is a move backward of about
  0.6 m with no turn.
- **Turned 115 degrees away from the owner while he was in view.** "You tell me." produced
  six direction samples split between -5 and 115 degrees; 115 won on energy and became the
  `speaker` target. The same bearing (100-115 degrees) appeared for "Hmm." and for a noise.
  With the owner 0.7 m away, the array also read a centred face as 22-38 degrees to the
  right (it sits beside the camera). Now a bearing within 50 degrees of a visible face is
  aimed at that face, and one that points away from everyone in view replaces the
  remembered voice only with at least 8 samples in one direction (80% of the weight). The
  unadopted bearing is still logged (`voice_direction_not_adopted`). This places a turn
  target only; it attributes no speech to the visible person.
- **"1. 42 meters" was heard as forty-two meters.** The phrase splitter cut at a period that
  was the end of the text received so far. It now cuts only at a mark followed by a space,
  and the model is told to give distances roughly and in feet.

Unmeasured: whether 1.0 m / 0.32 m is the distance the owner wants (0.8 m was his earlier
request, 4-5 ft too far), and the direction thresholds, which come from one run.

## October 3: looking for a way around

Owner: he could see the microwave above a counter that juts out, "however, he could not
make a plan to go around the counter"; he "stopped at the table, but he could've seen
visually that the space to the right of the table was open". He asked for time-of-flight
data mapped by direction against the camera view, photos along the way, pauses for hosted
calls, a slightly downward look at the floor, and a record of how far he has turned so he
can turn back and check the object is still ahead. **Everything below is checked in the
simulated base with a scripted stand-in for the hosted look. The hosted look itself has not
been tried on a real picture, and nothing has run on the robot.**

What happens when an approach (to a person, a detected object or a described thing) ends
blocked while the target is still far:

1. **A look at the floor.** The head tips down 12 degrees for one sharp picture, then
   returns to its level drive stance.
2. **The sensors by direction.** `open_directions` lists how far each range sensor sees:
   the eight columns of the front matrix (which cross the middle half of the picture), the
   two front corners, both sides and behind, each with its bearing and where it falls in
   the picture.
3. **A hosted choice of leg.** `OpenAIVisualLocator.route` (the same `gpt-5.4-mini` vision
   provider as the locator, about 1,500 input tokens a look) is given the picture, the
   goal and its bearing, the sensor list and what earlier legs did. It returns a point on
   visible open floor and a rough distance, or says the way is beyond the picture's edge
   on one side, or that there is none. It only proposes: the turn still passes the
   arm-swing check and the move the same clearance, wall and firmware guards as any other.
4. **Turn back and check.** After the leg he turns to where the target should be and
   looks for it again. If it is out of sight (a corner, a cabinet) he takes one more leg
   toward where it should be and looks again.

Up to five camera legs per approach; each look counts against the plan's allowance, raised
to 24 hosted looks for a request and 6 for his own moves (150 per run). When no look is
possible or it shows no way, the range sensors choose: an open front band as before, or
now a side sensor showing at least 0.8 m of floor, with the turn growing 30, 55, then 85
degrees. Only then does he report blocked.

**His own position.** Wheel travel is folded into a running position along the IMU
heading (wheel odometry's own heading over-reads turns). Each sighting of a described thing
is kept with where he stood and the room heading he saw it at; two sightings at least 0.4 m
apart whose lines cross fix its position, and from then on "where it should be" is the
bearing to that position from wherever he is. Before that he has only a direction, so for
the first look after a leg a match up to 60 degrees off is accepted instead of 25. A
person's position comes from the face bearing and range. This is dead reckoning over a few
metres, not a map: nothing is stored about the room.

**Retired.** The `gpt-5.6-terra` route planner rewrote a plan before it ran, from sensor
numbers alone and without a picture; it overrode a dictated route on October 3 and could
not see the counter. Its class, prompt, session hook, startup load, tests and check-tool
arm are removed. The model is now told to approach directly and leave detours to the
approach.

Verification: 11 new simulated checks (a counter passed on a camera leg, no wandering when
the picture shows no way, the head tip and return, a failed leg reported to the next look,
a table passed with the turn back to the person, looks counted, position under wheel slip,
crossing sight lines, the bearing to a fixed position, the sensor list, the table passed on
side sensors alone). Unmeasured and needing the robot: whether the hosted look picks usable
floor in real pictures, whether 12 degrees shows enough floor, the accuracy of the kept
position over a few metres, and the added pauses (a head tip, about two seconds per look).


## October 3, first run of the camera route: what the log showed

Owner's run `eef0a6efaf95497fa4a1524a9de03f9f` (9 minutes, real hardware): "it fell
short... sometimes the turning goes a little too far and then he loses sight of what he's
looking at, and at one point he gave up and turned around."

**What worked on the robot.** The hosted floor look answered every time (five looks, each
with a floor point and a reason such as "use the open left floor... skirting the island"),
he rolled five camera-chosen legs (0.6-1.5 m), the microwave grew in the picture from
about 10% to 15% of its width, and "come here" afterwards reached the owner (3.5 m).

**What failed.** The trip ended `lost` after 5.5 m and **657 degrees of turning**.

- After each leg he turned his whole body back to where he calculated the microwave should
  be, then looked twice at that one spot. The calculated position came from only two
  sightings about a metre apart, which is not enough: one look found the microwave at the
  far left edge of the picture, 36 degrees from where he was pointing.
- Five legs ran out part-way round the kitchen island.
- Each head look made by the drive owner started the 90-second "a person posed me" hold,
  which then refused his own moves. Ending 11 cm from the island also read to the back-off
  reflex as someone stepping close; he backed away on his own in the middle of his spoken
  report, and the report failed to finish (`PlaybackStalled`).
- The log held only the total turning, not each turn.

**Changes (simulated only).**

- **He looks back with his head first.** The neck covers about 25 degrees either side
  (October 23: measured from logged faces; 40 was believed before), so the
  body no longer swings back after a leg. He looks where the thing should be, then to the
  side it will have slid toward, then the other side; the body turns only when the thing
  lies beyond the head's reach, and once more, directly, when it is found.
- **A position needs three agreeing sightings.** Three or more sight lines from spread-out
  places that pass within 8 degrees of one point; otherwise he has a direction only.
- **Turn coast.** Thirty-odd logged turns ended 2-10 degrees past the command, more at the
  faster rate (about 0.11 s of coasting). Turns of 12 degrees or more are now commanded
  that much short. The simulated base coasts the same way.
- **More room to finish.** Eight camera legs per approach (was five), 40 hosted looks per
  request (was 24), and the route look is told to keep going round the same side.
- **Drive looks are not a person's pose**, and the reflex's baseline is forgotten after
  his own motion, so arriving near something is not "someone came close".
- **Every turn and move is logged** (`drive_step`) with the heading and position it left
  him at.

Unmeasured: all of it on the floor. The coast figure comes from turns at 27 and 45
degrees per second on this floor; the head-look order is a guess about which way a target
slides.

## October 3 evening: the camera route works on the floor

Owner's runs `b0d3ef1d42c0486fa58d806d5dc07e33` (33 minutes) and
`7af54a9421214133b83735e42ad15335` (8 minutes), real hardware, all earlier repairs loaded:
"they were really good. Rex found his targets and drove to them."

**Measured on the robot, from the logs.**

| | Result |
| --- | --- |
| Microwave trips | 3 attempted; 2 reported reached (188 s over 6.6 m, 183 s over 6.4 m, seven legs each); 1 reported blocked after 82 s, though the microwave filled 33% of the view when he stopped |
| Finding the target again | 53 of 53 hosted looks found it; it was never lost |
| Floor-route looks | 22 looks, 21 proposed a way; several proposals repeated a direction that had just failed |
| Turns | 111 logged; mean error +0.3 degrees, 92 within 3 degrees (before the coast correction: 2-10 degrees over) |
| "Come here" | Reached the owner each time, the longest over 7.4 m; three arrivals 0.9-1.2 m from him were reported as failures (`blocked`, `approach time limit`) |
| Back-off reflex | Did not fire after his own drives |
| Cost of looks | about 133,000 tokens across both runs |

**What the step log showed, and the changes (simulated only).**

- **Arrived, then hunted for a way round the counter.** In all three microwave trips the
  box reached 30-40% of the view (from 9%) while a counter beside or under it stopped him.
  Arrival required something straight ahead at the stand-off, so he kept asking for routes:
  up to 75 s and about 300 degrees of turning after he was already there. A thing at least
  30% wide that has at least doubled in the picture since first seen now counts as reached
  whatever stops him (`grew_in_view` in the receipt). A thing that only looks big from far
  away still does not.
- **A route leg undone by straightening.** Sent right, he turned right, then the
  wall-straightening turned him back in three 12 degree steps and the move was refused,
  three times from one spot. Route legs no longer straighten, and the same direction from
  the same spot is not tried again after it failed.
- **Arrivals told as failures.** A person approach that stops on a table, knees or the
  clock within 0.3 m of the stop distance is now completed ("stopped within reach"). The
  flat 20 s limit on one approach run, which is four metres at the easy pace, now scales
  with the distance allowed.

**Still open, from these runs.** The arm-swing check shortened six turns and refused three
"come here" requests outright near furniture (returns 0.50-0.61 m from the pivot); the tucked
arm reach it uses is an estimate that has not been measured. A microwave trip still takes
about three minutes. After a leg the body still turns 65-85 degrees back toward a person
who is close by.

## October 5 evening: stuck in a hallway, with the door still to come

Owner's run `4153bcbc1be24457ae3ce29cf29e2b09` (real hardware) ended with Rex stopped in
a hallway about 1.2 m wide; the owner left him there and invited a live read of the range
sensors (no motion, `hello` only, the controller lock taken so the battery menu released
the port). Readings: right side 0.25 m (front) and 0.34 m (back), left side 0.40 and 0.34 m,
front-right radial 0.40 m, everything else open. So he stood 7 cm right of centre, angled
about 11 degrees into the right wall, with the wall crossing his path 0.6 m ahead. The
owner then moved him to the workshop and asked for hallways to work, adding that the door
is tighter still (a 0.8 m opening for a 0.54 m body leaves 13 cm a side when centred).

**What the step log showed.** The firmware's own wall steering was doing its job: over the
last two legs his heading drifted 10 degrees away from the wall. The host kept interrupting
it. Each "guarded leg" stopped within 0.25-0.3 m because the 22.5 degree radial came
inside the 0.22 m hold (`wall_beside_path_too_close`), then the next leg started over from a
standstill. The straightening turn did not fire for those legs because the matrix saw the
wall ahead as well as beside, and refused turns left no trace at all, so the earlier part of
the run (`aim_turns_refused_for_arm_clearance: 4`, `detour_turn_refused`) could not be
read. A detour sequence had already brought him against the door jamb (all fronts 0.08-0.10 m).

**Changes (simulated only; nothing has run on the robot).**

- **Tight space is driven as one slow continuous crawl, not firmware legs.** A wall within
  0.5 m of either side, a front corner within 0.5 m, or one side of the path limited while
  straight ahead is open, makes the move a `drive` setpoint refreshed every 0.15 s at up to
  0.12 m/s (the firmware's stop zone at that speed is about 0.10 m). The firmware's wall
  steering stays on and finishes its work; the host no longer stops him for a wall beside
  the path until a return on the path is inside 0.12 m, something is in the path straight
  ahead (centre of the corridor under 0.40 m), a side is under 7 cm and closing, the
  firmware reports blocked, or the wheels make no headway for 2 s. A crawl that stops for
  something ahead after making headway reports `shortened_by_clearance` like any move;
  `crawled` and `steered_deg` are in the receipt.
- **The host adds the two things the firmware cannot read.** The two side sensors of a
  side give the slant of the wall there (`wall_angle`); a wall converging on his path adds
  a turn away proportional to the angle. The depth matrix's corridor reading adds a turn
  toward the more open side when a door jamb or wall crosses one side of the path ahead.
  Both are capped (0.4 rad/s together) and sit on top of the firmware's centring, front
  lean and 13 cm push, which are the same sign.
- **The turn before moving is sized by the wall.** Pointed into a wall beside him, the
  straightening turn is the convergence angle plus 3 degrees (8 to 30), 12 only when the
  side sensors cannot see a wall (a jamb ahead). It also fires when a wall within 0.5 m
  beside him converges, whether or not the matrix sees it ahead. A refused turn no longer
  ends the matter: the crawl steers instead.
- **Refused and shortened turns are logged** as `drive_step` rows with their reason.

**Simulated checks** (`tests/test_floor_feedback.py`, a geometric hallway whose readings
answer his motion, with the firmware's wall steering modelled in the fake base from V2
`control.cpp`): the logged pose driven twelve feet down a 1.2 m hallway without the walls
coming nearer than 0.12 m; pointed 20 degrees into a wall he turns 23 degrees away then
crawls; pressed 8 cm from a wall he eases off it; a 0.8 m doorway 15 cm off his line is
passed with both sides clear; a wall across the hallway stops the crawl short; a refused
turn leaves a trace. The firmware model is a transcription of its steering terms, not the
firmware: the real base's coast, sensor cones and 50 Hz loop are not modelled.

**Unmeasured on the floor.** Whether host and firmware steering together weave at crawl
speed (both are proportional terms; the host's angle term damps the firmware's centring in
simulation), whether 0.06-0.12 m/s stalls on carpet, how the 13 cm push behaves inside the
door, and the tucked-arm reach the swing check uses, which refused the aim and detour
turns near the jamb and is still an estimate.

## October 5 evening, second repair: blurred floor looks and the picture that "failed"

Owner: the head is not still enough for a clear picture until about 1.5 s after it tips
down, so the route looks were probably blurred; and Rex said he could not get a camera
view, which he wanted checked against the log.

**The failed picture was real, not invented.** After the blocked hallway approach the plan
asked for a look ("What do you see down the hallway and in the room?"). The log has
`inspection_result ... status: failed, reason: FreshCurrentViewUnavailable`. Ten
milliseconds after the wheels stopped, idle head motion began (`camera_motion moving:
true, source head:idle`) and the camera counted as moving for six seconds, the whole
three-second capture window. That was a consequence of the October 3 change that wakes
idle movement the instant a drive ends.

**Changes (simulated only).**

- **1.5 s settle before a route look.** After the head tips down for the floor picture the
  look waits `HEAD_SETTLE_S` (1.5 s) instead of 0.3-0.5 s; the same wait applies after a
  neck turn before a hosted look.
- **The least shaken of several frames.** A deliberate look no longer takes the first
  frame after the settle time: frames keep coming for 0.3 s and the sharpest (variance of
  the Laplacian on a small grey copy, compared only between frames of the same view) is
  the one sent (`looking.fresh_look`).
- **A picture asked for after a drive keeps the head still.** When the plan has
  `look_after`, the drive end tells the servo owner so (`finish_base_motion(look_pending)`)
  and idle motion wakes with a 4 s delay; the session wakes it as soon as the picture is
  taken, so the pause is only as long as the capture.

Checks: `tests/test_looking.py` (sharpest frame chosen, frames before the settle ignored,
timeout honest), the delayed idle wake, the route look's timing and the drive-end flag.
Not measured: whether 1.5 s is enough on the robot (the owner's estimate) and the
sharpness score on real head-camera frames.

## October 10 evening: stalled beside the couch, "turn left" as a head look, arms after a drive

Owner's runs `6b8fa000` (15:22) and `b954c14c` (16:17), real hardware, after the new head
and poker arm went in. "Come here" was refused in the first and barely moved in the
second; "turn left" did not turn; the arms did not come back after driving. From the logs:

- **15:22, "find the sliding glass door and drive to it": refused twice.** Every aim and
  detour turn was `turn_path_blocked: ring@315 near fr return at 0.5x m` with the
  front-right radial reading 0.07 m and the depth matrix open: the known phantom on that
  sensor (see [the front-right sensor note](#safety-gates)). Nothing in V3 changed for it;
  by the 16:17 run that sensor read 4.0 m, so the refusal did not recur.
- **16:17, "if you come over here": 0.18 m in 49 s, then "Stop."** The person was in view
  nine degrees off. The matrix's left column read 0.17 m (a coffee table corner, plausibly)
  and the centre 0.43 m, so the approach went straight to a route look, turned 33 degrees
  right, crawled 6 cm and stopped (`shortened_by_clearance`: the centre of the path read
  under 0.40 m), turned back, and the second look showed every matrix column and ring
  sensor open while `path_m` was still 0.16 m. That number came from single zones scattered
  across one band: each column takes its second-nearest zone, but a band pooled 24 zones and
  took the second nearest of those, so two lone speckles in different columns blocked the
  path. **Changed:** a matrix return now counts only when one of its eight neighbouring
  zones returns about the same range (within 10 cm or 20%); the nearest such zone is the
  band's reading, in `path_m`, `front_m` and the route look's columns alike. A real surface
  fills neighbouring zones at any range the host acts on. The firmware's own stop zones
  are untouched. **Logged:** a move that is refused, blocked or shortened now carries
  `clearance_m` (the path by side, the front radials, sides and rear) and a crawl says what
  stopped it (`stopped_by`); the route look's facts list `path_by_side_m`. What the three
  zones at 0.16 m were is not known from the log.
- **"Can you turn to your left?" became `look_left`, twice.** The model posed the head, and
  on "I asked you to turn left" said "Turning the base now" while posing the head again.
  Nothing refused a drive: `drive_base` was offered and the wheels were free (the
  `human_pose_held` refusal in the log was his own planned swivel, correctly held off by
  the look he had just been asked for). The tool text now says that "turn left", "turn to
  your left", "turn around", "rotate" and "face the door" are wheel turns and never a head
  look unless they say head, neck, eyes or look, and that "I asked you to turn" after a
  head look means a wheel turn now. The hosted planning check gained these two cases
  (`turn_to_your_left`, and `turn_left_after_head_look`, where the prior exchange was that
  question answered "On it."). With the new text, `gpt-6-luna` at low reasoning chose a
  `drive_base` turn left of 90 degrees in 6 of 6 (two repeats of each, with `turn_left`);
  evidence `local/validation/drive-planning-d6ec773c…`. The check tool itself had been
  failing every turn on a missing output attribute since the text-only output lost its
  adapters; `TextOutput` now carries them as `None`.
- **Arms after a drive.** See the [servo notes](SERVO_INTEGRATION.md#october-10-two-servo-head-poker-elbow-and-wrist-inverted-visor):
  only the throttle walked back; the poker arm stayed raised at its park.

Unmeasured on the robot: all of it. The corroboration rule is checked against the
recorded hallway grid and a synthetic scatter; whether the robot's matrix produces such
scatter routinely, or whether those zones were a real edge, needs the next run's
`clearance_m` rows.


## October 12 evening: a spin into the bookshelf behind him

Run `44a125be599946d2b7cd31b246292b57` (real hardware). Rex sat with his back to a
pass-through bookshelf on short legs, about 25 cm in front of a wall. "Can you come here,
please?" asked for a 72 degree left turn toward the owner. The swing check passed (the rear
sensors most likely read past the shelf to the wall), the body caught the shelf, and the
wheels pushed for about nine seconds until the firmware returned `aborted`. Nothing
recovered from a stalled spin, so he reported "aborted before I turned or moved" and stopped.
His own approach 30 s earlier had failed the same way. "Move forward three feet" then
completed 0.91 m: the floor ahead had been open throughout. The turn trace held no
clearance, so the rear readings at the time are not known.

Owner direction: driving forward and banking would have been fine. Changes:

- **Banked turns.** A turn toward someone he is approaching (30 degrees or more) drives
  forward round a curve instead of spinning when anything is within 0.8 m behind, or there
  is no rear reading, and the floor ahead is open (0.3 m past the front stand-off). He rolls
  10 cm nearly straight, then follows a 0.3 m radius (wider than half the body, so no corner
  swings backward) steered by the IMU heading, easing off near the end. The front sensors and
  the turning side's front corner and side sensor can stop it. After 0.6 m, any remaining
  turn is a spin with the usual swing check. A 72 degree bank covers about 0.45 m in
  simulation. Plain "turn" requests and "face me" still spin unless the spin fails.
- **Stall detection.** A spin whose IMU heading moves less than 3 degrees in 2 s is
  stopped as `turn_stalled` (owner chose 2 s over 1.5 s). The firmware's own abort
  took about nine seconds.
- **Recovery.** A spin that is refused by the swing check, stalls or is aborted banks the
  rest of the turn when the floor ahead is open. Otherwise he backs up within rear room and
  spins once more, then reports what stopped him. Before this change a refused spin backed
  up first, and a stalled one gave up.
- **Logging.** Every spin's `drive_step` trace carries the clearance it started from, and
  failed turns keep it in the receipt. Distance covered by banks and back-ups inside a step
  counts toward that step's `travelled_m`.

Checks: simulated base only. Covered: an approach with something close behind banks and
never spins or reverses; one with open space behind still spins; a caught spin is stopped
inside 2.6 s and banked to about 90 degrees; a caught spin with a wall ahead backs up,
retries once and reports `turn_stalled`. The simulated base now turns gradually so the IMU
sees a spin in progress. Not yet verified on the robot: the bank from the same bookshelf
spot, whether a 0.15 m/s, 0.5 rad/s arc moves smoothly on that floor, whether the firmware's
wall steering fights the bank, and whether 2 s ever cuts short a slow turn on carpet.

## Front-right radial firmware disable — October 10, 2026

Owner-authorized firmware `0.2.7-fr-disabled` disables the faulty VL53L1X on
mux channel 5 at initialization: no ranging or polling, `fr_radial=-1` throughout.
It cannot contribute phantom ranges to drive vetoes or steering. The independent
front matrix still owns combined `fr`; the other seven radial sensors remain active.
The matrix and other sensors can still slow/stop the base for obstacles.

Built and flashed the current V2 working-tree firmware with Bluepad32 4.1.0,
FQBN `esp32-bluepad32:esp32:esp32:UploadSpeed=115200` and all four flags set to 1:
`MOTION_HW_PRESENT`, `MOTION_GAMEPAD_PRESENT`, `MOTION_TOF_PRESENT`,
`MOTION_TOF_MATRIX_PRESENT`. Build: 812,817 bytes program, 90,636 bytes globals.
26 isolated V2 lean-runner sensor/battery regressions passed (native compiled
filters with recorded fixtures and synthetic battery inputs; blocked hardware I/O).

Initial USB transfer failed; retry completed with every upload hash verified.
Stationary live verification: hello version matched, all 139 telemetry frames
reported `fr_radial=-1`, boot logged channel 5 DISABLED, the other seven sensors OK,
Bluepad32 ready, and matrix ready. Last combined `fr` was 128 mm from the matrix,
with zero motor duty; IMU, compass and environment were online. `comms_lost` after
hello is expected without a driving host heartbeat. The battery LaunchAgent was
restored after verification. No drive command or physical gamepad test was sent;
floor driving and repaired radial coverage remain unvalidated. Raw stationary
telemetry and exact build options are retained under ignored `local/firmware/`.

## Matrix floor calibration — October 10, 2026

Owner confirms the close bottom-row returns are floor with no obstruction. The
firmware's previous geometric thresholds were clamped to >=250 mm, so it could
never reject the observed 80-250 mm floor returns. `0.2.8-matrix-floor` uses a
measured 70 mm threshold for normalized rows 6/7 (bottom two, zero-based), 10 mm
below the saved floor minimum. Both firmware aggregation and host clearance use
this calibration through the existing `tofmx.rej` fields; no host code changed.
The other six rows retain their geometric thresholds; nearer-than-70 mm bottom
returns remain obstacles. Objects returning the same ranges as floor in these
rows cannot be distinguished; upper rows retain obstacle detection. Front-right
radial remains disabled.

Checks: 26 isolated V2 native sensor/battery regressions, including four compiled
production geometry/aggregation cases for floor, close upper-row obstacles,
closer-than-floor returns and missing/single-zone returns. Host production
clearance replay on 53 recorded hardware frames removed 42 near-floor blocks;
remaining forward minima were 1.297-3.5 m. This is saved-frame evidence, not a drive
acceptance test. Full Bluepad32 build: 812,849 bytes program, 90,636 bytes globals;
all four existing hardware flags enabled. Source changes are confined to V2
matrix geometry/calibration, a pure floor helper, version 0.2.8, focused native
tests and firmware README. Existing uncommitted firmware/application edits were
preserved. V2 HEAD remains fb96f086925841479952c649b0a39a8f6c77f70c.

Deployment: upload hashes verified; live hello confirmed `0.2.8-matrix-floor`.
138 telemetry frames and 57 matrix frames were captured stationary. Every matrix
frame published `rej=[4095,4095,4095,4095,1548,517,70,70]`; host production path
analysis was clear at 3.5 m in all 57 frames. Final firmware `fl/fr=3500`, zone CLEAR,
front-right radial -1, Bluepad32 ready, seven radials online, zero motor duty.
No driving/gamepad interaction test was performed. Battery menu LaunchAgent
restored and serial ownership checked. Capture/build options remain ignored under
`local/firmware/`. Binary SHA-256:
`7c1e485b8b2c6e0ea0fbf5f2fa804d34d6710e2cf1065a7b98899c9ca02252ca`.

## October 14: short-term memory of the floor around him

Step 3 of the [improvement plan](IMPROVEMENT_PLAN.md#step-3-short-term-memory-of-space-october-14).
`local_map.LocalMap` remembers for about a minute where the range sensors saw returns and open
floor. A return on the path where open floor was just seen, with nothing there before, is
rechecked for 0.6 s (`RECHECK_S`) before a move is refused or a continuous roll is ended
(`drive_reading_rechecked` traces: holding, cleared or persisted). After one camera-chosen
leg around an obstacle, up to four further 0.8 m legs on the same side come from remembered
open floor (`drive_memory_leg`) before another hosted look. Firmware unchanged. Simulated base
only; no floor run.

## Rear depth matrix and front-right replacement — October 19, 2026

Owner: a second SEN0628 8x8 matrix at the front module's height, facing straight back,
so he cannot reverse into chair or bookshelf legs the two narrow rear beams miss (the
October 16 chair). Wired to the front module's private bus at DIP address 0x32. The
owner is also replacing the faulty front-right radial, so firmware re-enables it.

Firmware `0.2.9-rear-matrix` (V2 working tree; see V2_REFERENCE.md): both matrices on
one poll task, rear optional and folded into `rl`/`rr` for the reverse reflex, new
`rl_radial`/`rr_radial` telemetry and a `tofmx_rear` grid stream; channel 5 polled
again with crosstalk compensation cleared.

Host: `DriveLink` keeps the rear grid; `clearance()` counts both rear radials in full
plus rear matrix zones inside the straight reversing corridor (the same corridor rule
as the front), so a wall beside him does not shorten a reverse. Never seen means not
fitted: the rear stays radial-only. Once seen, a stale rear frame makes `rear_m`
unknown (`rear_depth_matrix_unavailable`) and reversing is refused. The local floor
memory records rear matrix columns at their bearings (`open_directions`); the camera
route's planner facts do not include them. His own plans still never reverse
(`SelfInitiatedReverse`) until the rear module is calibrated and seen working.

Checks (synthetic): 4 new host tests through the production link and clearance code
(a leg behind shortens a 1 m reverse to 0.4 m via the fake firmware's `tofmx_rear`
stream; a wall outside the corridor does not; a fitted matrix going quiet refuses;
unfitted keeps the radial pair). Full blocked-I/O suite: 1,345 tests, one unrelated
error (PySide6 not installed in `.venv`). V2 lean runner: 61 native firmware checks
including the rear module's floor/obstacle rules. Full four-flag Bluepad32 build
814,081 bytes program, 90,956 globals; bare and matrix-only variants also compile.

Deployment (owner-requested, both modules wired): upload hashes verified, binary SHA-256
`671151a3cc137a25d2dc5cbb8076d8a19781c9d50a2b2870ee6941437b2182ab`. Read-only stationary
captures under ignored `local/firmware/2026-10-19-0.2.9-*.ndjson` (no commands sent; one
controller reset to read the boot log). Boot: `0.2.9-rear-matrix`, all eight radials OK
(8/8, front-right included), front matrix ready at 0x33 and rear at 0x32 about 6.7 s after
boot. First capture: 237 telemetry, 159 front and 108 rear grids, zone clear, no -1. Rear
grid bottom rows read 271-293 / 381-428 mm, matching the front module's floor (268-288 /
390-422), so the copied calibration fits; rows 0-5 saw something 0.7-1.2 m behind. Rear
radials 0.87-0.90 m; rear matrix halves 0.63-0.75 m; host `rear_m` 0.69 m, no health flags.
New front-right radial steady at 838-853 mm, agreeing with the front matrix's right column
(791-855). Front-left radial alternated between open (up to 4 m) and 0.30-0.46 m returns
that the front matrix's left column (~0.9 m) did not see; cause not yet known.

Outstanding: left/right check with a hand behind each corner (behind on his left must
lower `rl`), the front-left radial's intermittent near returns, a supervised reverse toward
a leg, and lifting `SelfInitiatedReverse` once the owner accepts the rear module.

## October 19 evening: turned away from "come closer", stops missed

Run b4ce6464 (living room, owner about 2.8 m ahead, recognized in 25 of 58 frames).

- **Backwards voice.** Mid-sentence the microphone's selected beam flipped from about -2 to
  179 degrees (its front/back mirror) while the array's own DoA register stayed at 2; the
  flipped half was the loudest, so energy weighting read the voice as behind and "face the
  speaker" turned him 180. `direction.summarize` now calls a dominant bearing behind him
  `ambiguous` (`front_back_mirror`) when its front mirror image is also heard in the same
  utterance. Replayed over 25 recent sessions it changes only this reading.
- **Stale voice.** "Face me" later turned 166 degrees on that voice, heard 90 s and 2.6 m of
  driving earlier. A remembered voice now lapses once he has rolled 0.5 m (`VOICE_MOVED_M`).
- **Words against moves.** Answering "I told you to stop" and "why did you do that?", the reply
  model said "I'll stay put now" while sending a turn and then a 0.5 m move that resumed the
  stopped approach. When his last drive was stopped by a person, the reply prompt now says
  so and that only a new request moves him; complaints get words. A face step on a voice
  with nobody in view reports `already_facing_voice_direction_no_face_in_view` (or
  `turned_toward_...`) instead of a bare completion that became "I've turned to face you".
- **"Come closer" refused.** An approach to "speaker" with no speaker direction or identity
  becomes an approach to the nearest person (state validation); facing the speaker still
  needs a direction.
- **Stops while rolling.** Only one of three or four shouted stops reached the recognizer.
  For the first 25 s of the approach the processed channel stayed below the room's quiet
  level (-43 vs -40 dBFS RMS) and the detector never rose above 0.28; one "Okay." was noise
  (the owner said no such thing). The recognized "Stop." halted him about 1 s after the word
  ended. Effects play through the XVF3800, so AEC has their reference, and the drive never
  marks echo. While the wheels run, mic health is now logged each second (`wheels_moving`),
  and a short burst (up to 1.5 s voiced) is transcribed after a 0.2 s pause beside the normal
  capture: a stop word or collision warning halts the wheels at once (`stop_peek`).

Checks (synthetic): new direction, voice-memory, fast-stop, input-health and validation tests;
full blocked-I/O suite 1,351 tests with the known PySide6 import error and one servo timing
test that passed 3 of 3 when rerun alone. Hosted `check_drive_planning.py` (production
model, paced; the harness's stand-in drive had broken on the newer `speaker_person`
argument and is fixed): "I told you to stop" got words only 3/3, a fresh "okay, now come
here" after a stop still approached 3/3, "why did you do that?" still resumed the approach
in 2 of 3; told-to-stay-put and voice-from-the-side unchanged at 3/3.
Whether the board suppresses a voice under motor noise is not yet known: it needs a live
test with the wheels running and someone saying "stop" at measured distances.

## October 23: the neck's real reach

Logged faces in four runs show the neck turns about 0.57 of the degrees the code assumed
(`drive_space.NECK_PULSES_PER_DEG`; details in [implementation status](IMPLEMENTATION_STATUS.md)).
Bearings taken with the head turned were overstated by that much, so turns toward something
seen with a turned head overshot. Re-finding looks now go to 25 degrees (`LOOK_NECK_MAX`), the
body turns when the thing lies beyond what the turned head sees (65 degrees, as before), and a
missed head look at something more than 45 degrees aside turns the body toward it for one more
look. Simulated only; not yet driven.

## Smooth starts and stops — October 23, 2026 (firmware 0.2.10)

Owner: at 52 lb his turns were jarring and almost too fast, did not ease in, and stops were
worse; the obstacle stops were mostly false slams. He asked for a firmware fix and a flash.

The ramp the host pushes was already gentle (0.35 m/s², 2 rad/s², below the firmware's own
1.2 and 8), but the wheel power underneath it stepped:

- **Turn start.** On the first tick of a spin each wheel jumped to its breakaway duty
  (750 of 1023 on hardwood, all 1023 on the gamepad's carpet setting) and held it until
  0.12 s after the wheels rolled, so the spin lurched past the requested speed.
- **Every stop.** The running floor (37% in a spin, 22% straight) stayed on until the
  requested wheel speed fell below 1 cm/s, then the duty went to zero with the motor
  driver enabled: a short-circuit brake. A spin's sideways tire scrub stops the base
  almost at once when drive vanishes, so the upper body lurched.
- **Obstacle stops.** A near reading in the stop zone cut the speed ramp to zero in one
  tick and braked, then the base relaunched with the kick when the reading cleared.

Firmware 0.2.10 (`firmware/djr3x_motion/drive_shaping.h`, moved from V2 the same day;
see V2_REFERENCE.md):

- The breakaway push climbs from the running floor only while a wheel is actually
  stalled, and falls away once it moves, so he breaks loose with about the push he needs.
  The first tick of a hardwood spin is about 44% duty instead of 80%. A wheel that will
  not move still reaches the full ceiling within 0.15 s.
- While nothing commands motion, the floor, breakaway push and integral term fade to zero
  with the ramp, and the duty never reverses. Stops taper instead of ending in a brake.
- A block toward the direction of travel slows him at the deceleration the remaining room
  needs, between 0.8 and 6 m/s². From 0.25 m/s at the stop line (about 12 cm) he stops in
  about 4 cm over 0.3 s. One false frame costs a dip to about 0.19 m/s, not a stop.
  Zones, latch, slow-zone taper and the 900 ms block grace are unchanged.

Not changed: speeds, ramps, turn rate and its slow finish, gains, the gamepad, the protocol,
and the host. Estop, fault and comms loss still cut the motors (they coast).

Checks (synthetic): a native test compiles the header and runs spin starts, stops (tracking
and friction-stopped), a slow inside wheel, reflex stops at four speed/room pairs and one
false frame against a toy wheel; all 39 native firmware tests pass (V2 runner, then
`tools/run_firmware_checks.py` after the move). Full
four-flag Bluepad32 build 814,457 bytes, 90,956 globals.

Deployment (owner-requested): battery meter booted out and the controller lock held while
flashing; upload hashes verified; binary SHA-256
`7e3d4971f34ec1aae72a4c3bc8a2611640e6f714d2c8ce55bad7a6513e31b24c`, identical to the
tested build. A hello-only read reported `0.2.10-smooth-drive`, on the charger at 14.2 V,
IMU and compass OK, all radials and both matrices live, zone clear, wheels idle. The binary,
flash log, that read, and a rebuilt 0.2.9 rollback (binary and source) are under ignored
`local/firmware/`. Rollback: unpack `0.2.9-rear-matrix-rollback-source.tgz` and flash it
with the firmware README's runbook command.

Not yet driven. Open: the feel on the floor; whether the softer launch stalls a spin on
carpet (the host reports `turn_stalled` after 2 s without progress); and the turn-coast
compensation (`TURN_COAST_S`, fitted to the old push-then-brake stop), which the next run's
turn receipts can refit.

## Talking while rolling — October 24, 2026

Owner: "He either talks, or rolls, but never both at the same time." Saved logs agreed. In 309
drives across 56 robot sessions (September and October; real hardware, no waveforms) not one
second of his speech overlapped the wheels: about 71 minutes of driving in silence. Median
drive 6.5 s, p90 26.5 s, 49 over 20 s. Three things forced it: the output owner ran a drive inside
its one speech queue, so the opening line finished first and nothing played until the drive
returned; talking motion and the drive shared one motion lock; and the whir plays through the one
speech sink, which plays one sound at a time. The opening line alone would not have filled the
roll: in 238 of 336 drives it was still playing when the plan was accepted, but only for a median
0.9 s, and the arm tuck before the first wheel command takes a median 2.8 s.

Owner's choices: he speaks as he sets off, visor and mouth lights only, head and arms still, the
whir quiet while he talks, short lines only, and a stop still heard over his voice.

- **The drive runs beside speech.** `OutputOwner.start_drive` runs the plan on its own thread.
  The arms tuck and the wheels start while the line already playing goes on. When more than
  `ROLLING_WORDS` (14) are still to be said, they are said first and the wheels wait, as before.
- **`say_while`.** A new optional `drive_base` line, spoken once the wheels are first commanded
  (`DriveBase.rolled` names that plan), never for a plan that does not roll. One longer than 14
  words is said before the wheels start. A move too short for the line to start in time hears it
  just after. The tool tells the model to keep it to one sentence, claim no arrival, and use no
  stop words; agency beats may ask for a line on the move.
- **Visor only.** A line beside a drive runs `ServoOwner.talk_visor`: the visor alone, outside
  the single-request lock like the arm return, so the tuck, drive looks and the arm return run
  beside it. A line already playing when the drive starts gives its head and arms to the drive
  and carries on with the visor. A visor a person posed or shut is never moved. Mouth lights
  follow playback as before.
- **Whir.** `MotionSounds.hush` pauses the whir for each line; it comes back afterwards if the
  wheels still run. Accents are skipped over his voice.
- **Stop over his voice.** Captures made while he speaks are echo and the conversation rejects
  them. While the wheels run, the stop check now also hears them (`stop_peek` on an echo capture)
  and short bursts during his lines. His own line's words are set aside first
  (`stop_heard_over`), so his own "whoa" never stops him. The board's echo cancelling makes this
  plausible: across 140 one-second windows of his own playback in the last 12 sessions, the
  speech detector marked 1% of frames as speech, against 12.6% in windows without playback
  (logged VAD counts, no waveforms).

Checks (synthetic speech, simulated base and servo pulses): 21 new in
`tests/test_talk_while_rolling.py`. **Not yet run on the robot or against the hosted models.**
Open: how the visor-only talking looks while rolling; whether the model writes good rolling
lines and how often; whether a person's "stop" over his voice is transcribed in time (a live
test: someone says "stop" while he talks and rolls, at measured distances); and whether the
brief whir pauses between lines sound odd.
