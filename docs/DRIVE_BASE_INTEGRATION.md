# Drive base: LLM-directed motion

Status, September 27, 2026: **implemented in software with a simulated ESP32; not yet
run on the robot.** The owner asked for purposeful, more frequent movement decided by the
conversation model, V2's radar as turning evidence, no firmware changes, the full scope
in one day, and a measured decision on a stronger model. The wheels have not moved: live
floor acceptance waits for the owner's explicit go-ahead.

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
  reverse, per V2). The firmware keeps its reflex stop, 500 ms watchdog and 300 ms deadman.

## Limits (owner-approved September 27)

| | Requested | Self-initiated |
| --- | --- | --- |
| Top speed (also pushed to firmware `max_lin`) | 0.25 m/s | 0.18 m/s |
| Firmware leg | 1.5 m | 0.75 m |
| Plan distance total (moves and approaches) | 3 m | 1.5 m |
| Turn per step / total | 360° / 720° | 180° / 360° |
| Steps | 6 | 4 |

A move longer than one leg runs as several legs with clearance rechecked between them.
Turns run at 45°/s (V2 overshoot grows with rate), with one IMU-verified correction
of at most 45° at 20°/s. Host stand-off is 0.35 m in front and 0.30 m behind.

## Safety gates

Refused, with a spoken reason, when telemetry is older than 0.5 s, the gamepad owns the
base, the firmware reports estop/fault/comms loss, the base is charging (flag or at least
13.6 V), the 8x8 depth matrix is stale, or every ring sensor errors. Spins use V2's swing
check about the rear axle including the left arms; unknown sensing refuses. Moves are
clamped to measured clearance; unknown rear clearance refuses reversing. The front uses
per-band matrix minima (left/centre/right, second-nearest floor-rejected zone) plus valid
radial sensors, and flags the faulty front-right radial rather than trusting it.

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
`gpt-5.6-terra` (low) 3/3 (left 45°, forward 1 m, face the dog). So a route planner runs
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

## Floor test — only after the owner says to run it

Hardwood room, no drop-offs. Clear 1.5 m around Rex. Keep the gamepad in hand: any stick
push takes over, **B** is emergency stop. Stop V2 first. Add to `local/settings.toml`:

```toml
drive_port = "/dev/cu.usbserial-110"
radar_port = "/dev/cu.usbmodem2301"
```

Start with `.venv/bin/python -m r3x live --camera --record-evaluation`. Expect no wheel
motion at startup. Then, one at a time:

1. Say **"Turn left."** Rex turns about 90° left. Check the direction and that it stops.
2. Say **"Back up a little."** A short reverse.
3. Say **"Spin all the way around, back up a bit, then come to me."** Steps in order;
   ends facing you about a metre away.
4. Stand about 3 m away and say **"Come here."** It should get to about 1 m, not stall
   after a few centimetres (the V2 failure).
5. Walk behind Rex and say **"Rex, over here!"** It should turn toward your voice.
6. Mid-drive, say **"Stop."** Wheels stop at once.
7. Put a box between Rex and an object it can see and ask it to take a closer look.
   It should go around (route planner) or explain the block.
8. Say **"Stay right there,"** then chat. It should not drive itself.
9. Slowly put a hand about 20 cm in front of it. It should back up once.

Report which failed and the session-log path. The owner-visible success is purposeful
motion; logs alone cannot establish clearance, overshoot or social welcome.

## Remaining acceptance and limits

Physical turn overshoot and correction, stand-off distances, the matrix phantom returns,
the back-off threshold, arm clearance during turns, camera bearing (25° half-FOV) and
face-range accuracy, radar persistence, microphone contamination while driving, combined
M2 load and social welcome of self-initiated motion are unmeasured. There is no map,
localization, cliff sensing or obstacle memory; the geometry constants are V2 estimates.
The V2 battery menu app keeps its port only while V3 is off; V3 battery/base menus remain
future work. Room exploration and no-drive rooms are not implemented.

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
  elbow fully (7424, higher is up) and the poker arm to 8000, held with the throttle and
  head until the drive ends.
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
  absent sensors as null. It is in every conversation context, including a
  disconnected-controller notice.
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

