# Servo integration: software ready for supervised commissioning

September 25, 2026. The owner requested adapting V2 servos, including the current
throttle-arm working tree, preserving recognizable startup/shutdown choreography
and movement while talking. The follow-on adds optional live composition, confirmed
handoff, operator tools and microphone protection. **Not activated or validated on
hardware.** The owner asked to leave V2 running and prepare cutover for later.

## October 13: the poker arm sways like a cat's tail

Owner: the poker arm's idle should be steadier, almost like a cat's tail waving: not fast,
just more constant, with the wrist following the elbow in the opposite direction, trailing
it. It used to jump to a random spot every 3-7 s. Now (`servo_poses.poker_tail`):

- *Every phrase:* the arm is due in every idle phrase (it takes one of the three parts) and
  each move is half a sway, across to the other side of its awake pose (swing 6000, elbow
  6000, wrist 3768). Side to side every time, never back to the same side.
- *The shape:* the swing goes with the elbow (±300-600 and ±350-550); the wrist bends the
  other way (±500-800). All three stay inside the old idle range, under the elbow's
  clearance ceiling.
- *The trail:* the elbow and wrist still start in the same command, because the clearance
  check needs them together. The wrist eases in at a third of the elbow's acceleration and
  arrives 0.5-0.8 s after it, so the hand picks up speed late and follows through. Each half
  sway takes 1.7-2.3 s at board speed 8 or less (new per-joint `Beat.profiles`).
- *No stopping:* when another part in the same phrase takes longer (the hero arm's reach, up
  to about 8 s), the tail adds more half sways until it is about done. A held or tucked poker
  arm does not sway. An arm that starts where rising into the sway could meet the wrist takes
  the staged route back to the awake pose first.

Phrases now last as long as a half sway (about 2.2-3.1 s), so the head's face-following
re-aims about every 2.5-3 s rather than every 1.2-2.8 s; it keeps drifting in between. 1,134
blocked-I/O checks pass (simulated servo board, which arrives instantly). How it looks, the
real acceleration curve, the pause at each end of a sway, its noise and servo heat under
constant motion are unseen until a run on the robot.

## October 12 (later): livelier, more continuous idle

Owner: idle looked frozen with a small move now and then; talking is vivacious; idle should sit
in between. Idle now runs at board speeds up to 18 (was 5-12; talking uses 20-45):

- *Cadence:* hero arm every 2.5-6 s (was 5-12), poker arm 3-7 s (6-15), throttle 5-11 s (9-20),
  visor 4-9 s (6-14). Parts in one burst start 0.2-0.8 s apart (0.4-1.3), and the pause between
  bursts is 0.15-0.35 s (0.3-0.7; the microphone hold still renews in it).
- *Size and pace:* arm moves are about 40% larger and take 1.1-2.2 s (1.6-3.2); the throttle arm
  2-3.2 s (3-4.6). The hero shoulder swing (microphone array) keeps its size, pace and 30-60 s rhythm.
- *Head:* when it is not re-aiming, it wanders about ±3.5 degrees of neck, with a small nod and
  lean, at speed 10-14 (was ±2 degrees at 5-9). While following a face it stays in the dead zone
  with a little more life (±2 degrees of neck, a small nod and lean). When a burst also moves
  an arm or the visor, the head keeps wandering until they finish instead of stopping to wait.
  All of these stay inside the camera's "same view" limits, so they do not reset scene settling.

Synthetic only: 1,113 blocked-I/O checks pass, but the simulated board arrives instantly, so
how continuous it looks, how loud it is, and whether his own servo noise now stills him more
often must be judged on the robot. In the October 12 live session idle ran only 2-8 bursts per
30 s, many cut short by speech in the room; this change does not alter that gating.

## October 12: idle arms move as a whole

Owner: in idle life the arm joints moved one at a time, while a real arm moves several joints
at once; two arms moving together is fine, but an arm that moves should move all its joints a
little. Idle life (`idle_motion.py`, `servo_poses.idle_lanes`) now treats each arm as one part:

- *Hero arm* (every 5-12 s): elbow and wrist move together, every time. Its shoulder carries
  the microphone array and each swing pauses voice learning, so it still joins only every
  30-60 s (`SWING_SECONDS`, not while he listens or works, nor while a person holds his arms);
  then the whole arm reaches out, pauses and comes back with the array at centre.
- *Poker arm* (6-15 s): swing, elbow and wrist in one move. Inside its idle range the elbow
  never nears its clearance ceiling, so the staged one-joint-at-a-time route is used only
  when an arm starts outside that range.
- *Throttle arm*: already moved its three joints together; unchanged.

Within an arm all joints start at the same moment and are paced to arrive together (new
`Beat.seconds`). Board speeds are whole numbers, so each joint's travel is adjusted slightly to
a whole speed over the beat's time; over 400 random moves every joint moved at least 40 µs and
all finished within 0.01 s of each other. Moves take 1.6-3.2 s at board speed 7 or less. A
held elbow or wrist now keeps that whole arm still (no lone wrist wiggle). 1,110 blocked-I/O
checks pass (synthetic, simulated servo board); how it looks and sounds on the robot is unseen.

## October 11: the listening cue (head cock while he thinks)

Owner: show that Rex heard you during the wait before his answer, with a head cock at the end
of transcription. When a trusted transcript comes back (`VoiceRuntime._asr_loop`, once per
capture), `RobotServos.heard` steps calm idle movement aside and the voice-reflex worker runs
a `listening` request (`servo_poses.listening_lanes`): the two head servos cock to one side
(tilt 0.35, a touch down) at speed 35, hold for 3 s and return to where the head was. The side
is random, or away from a lean the head already has. Neck, lift and the array arm do not move.
Untrusted transcripts (his own motors heard as "Oh."), stop and shutdown commands give no cue.
It is skipped (`listening_cue_skipped`) while he is already talking or a person's held pose or
the drive stance holds the head. Talking, a pose, a gesture or a drive stops it first, like the
voice turn; talking sway then straightens the head as the reply plays. The cue arrives about
2 s before first audio (ASR about 0.5 s after the end of speech, model and voice about 2.5 s
more). No setting. 1,109 blocked-I/O checks pass (synthetic, simulated servo board); how it looks, how loud the
head servos are, and whether it still fires during a reply that streams quickly are unseen on
the robot.

## October 11: arms stay tucked beside obstacles

Owner: the poker arm reaches about 2 ft out from the left side sensors and the throttle arm
about 2 ft ahead of the front sensors; after a drive, neither should come out into something.
When the wheels stop, the drive owner reads the ring (`drive_space.arms_blocked`): the poker
arm (swing 6, elbow/wrist 12/13) stays at its drive stance (8000, `POKER_PARK`) while the
nearer left side sensor reads under 1.07 m (3.5 ft), and the throttle stays at `PARK` while
the nearest front reading (ring radials and depth matrix) is under 0.61 m (2 ft). No reading
counts as blocked. `return_arm(keep=...)` walks only the other arms back; the kept arms'
channels stay held (`kept_arms`), so idle and talking skip them, an aged hold or
`release_poses` does not free them, and any pose for them is refused as
`ArmKeptClearOfObstacle`. Each second the drive owner rechecks; once an arm's space has read
clear for 2 s it walks to its awake pose (not while driving, and not while a person's
`hold_arms` stands). The next drive's park takes kept arms as usual. The reply model sees
`robot_pose_state.arms_tucked_for_obstacle`. Not covered: an arm already out when someone
steps up beside him is not pulled in, and startup or requested poses with no drive before
them are not gated. Synthetic checks only (simulated base and servo board).

## October 10: two-servo head, poker elbow and wrist, inverted visor

The owner reworked the head and the poker arm and measured the new positions in the
Pololu Maestro Control Center (microseconds; the code uses quarter-microseconds, x4).
The measured extremes are the calibrated limits. **Not yet run on the robot.**

**Head.** The single head-tilt servo is gone. Two servos in the head face each other,
each with a linkage onto one centre post: `head_left` (channel 2, the old channel) and
`head_right` (channel 11). Driven against each other they pitch the head; driven the same
way they shift the post sideways and cock the head to one side (called *tilt* below; the
owner also called it yaw). Readings (left / right, µs):

| pose | left | right |
|---|---|---|
| level | 1458 | 1620 |
| level, fully down | 1013 | 1904 |
| level, fully up | 2032 | 1312 |
| down, tilted right | 1008 | 1702 |
| down, tilted left | 1350 | 1849 |
| up, tilted left | 2032 | 1587 |
| up, tilted right | 1659 | 1312 |

`servo_poses.head_pulses(pitch, tilt)` turns a pitch (-1 fully down .. 1 fully up) and a
tilt (-1 cocked to Rex's left .. 1 to his right) into the pair of pulses: pitch moves the
servos against each other along the measured level/up/down line (the travel is asymmetric),
and a full tilt shifts both the same way by about 275 µs in total, each within its limits,
the other taking what one cannot. Tilting right lowers both pulses. `head_pose` is the
inverse (a least-error fit); it reproduces all seven readings exactly. Every head command
sets both channels (`IncompleteHeadPose` otherwise), and a pair that no pitch/tilt
explains within 150 µs is refused as `HeadPairOutsideEnvelope`, so the two servos cannot
be driven into the corners the linkage never saw (for example both at their highest).
Where one servo sits at its limit the decomposition is ambiguous (fully down tilted right
also reads as a lesser pitch with more tilt); poses are still exact, only the reading of
them is not. Pitch in degrees still uses V2's 20 down / 25 up estimate, unverified here.

What uses it: the level drive stance and startup head are the owner's level reading; park
is fully down. `look_down`/`look_up` pitch by 0.8 and keep the tilt; new `tilt_left`,
`tilt_right` and `tilt_straight` steps cock the head by 0.8 and keep the pitch and neck;
`look_level` levels and straightens. `set_robot_joints` offers `head_pitch` and
`head_tilt` (0-100, 50 level/straight) instead of a raw servo. The talking sway nods
slightly with a slight cock to each side; idle glances away carry a small cock; room scans
a slight random one; face following moves the pitch and keeps the tilt. The `tilt`
gesture is now a real side cock (0.6, or 0.3 subtle) that straightens again, and `nod`
pitches both servos. The camera reframe rule counts both head servos.

*Expressions (October 10 evening; owner: "dogs cock their heads left and right when they're
perplexed", use the freedom for more expression).* `HEAD_EXPRESSIONS` adds `perplexed`
(cock 0.6 to one side, swing to the other, slightly down, 0.7 s each), `wiggle` (three
quick 0.35 cocks side to side) and `sympathetic` (a slow 0.45 cock, slightly down, held
1.2 s). All return the head to where it was and hold nothing. They are offered to the
reply model as `respond_with_gesture` primitives (with guidance on when each fits and to
keep most lines plain) and, apart from the plain `tilt`, as `set_robot_pose` steps for
requests and Rex's own plans. The choice stays with the conversation model: no event is
mapped to an animation. Idle life carries a little more tilt: drift ±0.06, glances away
cock 0.1-0.25, room scans ±0.3, and while following a face the head settles into a gentle
0.15-0.3 cock for 3-8 s every 10-25 s, then straightens.

**Poker arm.** Channel 6 still swings the arm. New `poker_elbow` (12: 864 µs down, 1920
up) and `poker_wrist` (13: 496 down, 1440 straight, 2486 curled up and inward). The elbow
rises fully only with the wrist at or below 1564 µs; curled further in, the measured ceiling
falls (wrist 1894 → elbow 1807, 2136 → 1709, 2496 → 1597; linear between). A pair above
the ceiling is refused (`PokerElbowWristCollision`) before anything moves, and every
elbow/wrist move is staged one joint at a time (elbow down to the lower of the two, wrist,
elbow up) so the ceiling is never crossed on the way. Named poses from the readings:
`poker_out` (1057/1389), `poker_up` (1500/942, 45 degrees up with the hand level),
`poker_down` (elbow down, wrist straight). The drive stance adds elbow 1920 / wrist 1564
beside the raised arm. Elbow 1920 / wrist 1564 is also the arm's park (`POKER_PARK`): the
cold-start hold for its two channels, and where shutdown returns it in clearance stages.
Awake, the arm sits at `poker_up` (45 degrees up, hand level): startup takes it there from
the park in clearance stages beside the head and throttle lanes, and idle life wanders the
elbow and wrist slowly around that pose (owner, October 10 evening: after the wake the arm
stayed at its park and looked wrong). First live start,
October 10 evening: the code had assumed an elbow-down rest, so the arm, which stands at
its park when switched on, dropped at once and shutdown left it down; corrected the same
day.

**Visor.** The servo now runs inverted: low pulses raise it, high pulses lower it, within
the same 4544-6976 travel. Owner's readings, October 11: the neutral open point is 1433 µs
(5732; startup and `visor_up`) and the surprise position is 1136 µs (4544, the channel's
low limit, the widest it opens). Closed and park stay 1744 µs (6976), reached on the robot.
Speaking (5492-6132) and idle (5382-6082, half-blink to 6132) keep the September offsets
around the open neutral, mirrored; the old 400-pulse closing excursion partly hid the camera
then, so check the camera view at 6132. The new `surprised` expression snaps the visor to
the surprise position with unlimited acceleration while the head lifts 0.15, holds 0.8 s,
and eases both back; it is a reply-model gesture and a `set_robot_pose` step. The first
mirrored guess (open 5120) was replaced by these readings.

**First live run (session `f07e53af`, October 10 evening).** The wake choreography,
`tilt_left`, `tilt_right`, `look_level`, `look_up`, `look_down` and `poker_up` all reached
their pulses on the robot. Three findings:

- The poker arm dropped at startup and stayed down at shutdown: the cold-start hold had
  assumed an elbow-down rest while the arm stands at its park when switched on. Fixed above.
- "Can you look left?" moved the head to the right. Rex was already following Bret at the
  neck's left limit (the voice reflex aimed at 1984 twice; he stood 55-65 degrees left), and
  `look_left` was a fixed 33-degree pose, so it swung back toward centre; "turn your head
  left" then found him already there and moved nothing. Side looks are now relative: from
  nearer the centre they go to the 33-degree pose; already there or beyond, a person's
  request goes on to the neck's limit on that side, and Rex's own repeated step moves
  nothing (`neck_target`; `already_at` reports a side look at or beyond its pose).
- Shutdown failed with `ServoArrivalTimeout` on the head/visor lane after 25 s; the throttle
  parked. The trace did not say which channel never arrived. A timeout now names each
  channel still short of its target with its pulse (for example `ch3@6400->6976`). The
  likely cause is the Maestro's own stored channel range clamping a target it has never been
  sent before: the visor park 6976 (1744 µs) is the only new lifecycle value outside anything
  the September code commanded; the head pair's park (1013/1904 µs) is the owner's reading.
  Confirm from the next shutdown's reason, then either widen the board's range in the
  Maestro Control Center or lower the park pulse to the board's limit.

**Second live run (sessions `6b8fa000` and `b954c14c`, October 10 afternoon).** Two more
findings, both fixed the same evening (pulse simulator only):

- **The poker arm did not come back after driving.** The drive stance raised the arm (8000),
  put its elbow up and wrist at 1564 µs, and when the wheels stopped only the throttle arm
  walked back; the hero elbow and the poker channels were merely let go, and idle life
  never carried them back (the next drive's park changed none of them). `return_arm` now
  walks every arm the stance moved: the throttle to rest, the hero elbow to its awake
  6720, the poker arm to mid swing (6000) and its elbow/wrist to `poker_up` by the staged
  clearance route, all beside one another and beside head motion, which is free at once.
  Those channels stay held until they are back; a request for them meanwhile is
  `ServoBusy`, as the throttle already was.
- **Shutdown timed out again, now named: `ch1@2624->2600`.** The head-lift park of 2600
  never arrives because the board's own range stops at 2624 (656 µs); the lift's minimum
  and park are 2624 now. The visor park reached 6976 this time.

**Also.** `IncompletePokerPose` mirrors the throttle's batch rule; held parts report
`head_pitch_and_tilt` and `poker_elbow_and_wrist`; `robot_pose_state.joint_percent` is
now by joint name with the head as pitch/tilt; the servo menu-bar console lists all 14
channels with the new limits and direction hints. Confirmed on the robot (owner, October
10 evening): `tilt_left` cocks the head to Rex's own left. Unverified: how far a 0.8 tilt
visibly cocks the head and whether the linkage binds anywhere inside the envelope, the poker arm's
clearance of the body and other arms, the visor's real endpoints, and whether the
motion reads as expressive. Synthetic checks only.

## Implemented

- `servo_poses.py` owns pulse limits and small lifecycle/talking sequences.
  `servos.py` owns execution and versioned, expiring requests; `adapters/maestro.py`
  encodes the compact protocol. No V2 runtime import, configuration load, global
  state, automatic reconnect, semantic router or additional model is used.
- Startup raises the head and opens the visor while the neck scans left/right
  (either order) and the throttle arm unfolds through its established waypoints.
  One executor advances independent lanes, rather than spawning competing writers.
- Shutdown/park concurrently droops the head, recenters the neck, closes the visor,
  lowers the elbow to 6300, and routes the throttle through tuck to park. It waits
  for output-pulse readback and settling. A completed park rejects subsequent
  animation requests; close itself sends no motion. Channels 5–7 stay where they
  are, matching V2 shutdown rather than the broader V2 test-only reset helper.
  V2's power-down sound starts with the park (October 10): the servo owner's park
  hook plays it through the speech sink after the LED fade command, the park never
  waits for it, and playback closes only after the clip finishes.
- The throttle always moves as a three-channel batch. Every route is checked
  before any lane moves and rechecked against fresh pulses before each target.
  The latest full-down retraction corridor is included. Lifecycle profiles retain
  the current V2 throttle pace. The directional full-down-to-high-five exception
  is not used: named high-five requests use the validated retraction bridge.
- The production `OutputOwner` accepts optional talking motion. Actual playback
  start launches its worker; normal completion, failure and cancellation stop it.
  TTS generation and queued PCM do not start movement. The worker shares the servo
  executor and output's motion reservation; callbacks never write serial. A stopped
  phrase holds current output pulses rather than issuing a late neutral/visor reset.
  Motion receipts and errors are recorded separately from speech delivery.
- Talking now includes neck/lift emphasis, a slow modest headtilt sway, a speaking
  visor lane, hero-arm
  elbow (channel 4), wrist (channel 5) and two V2 throttle poses. Independent lanes
  repeat while slower lanes finish; sent targets drain before a successful cycle
  receipt. Head anchors are captured after startup, preventing interrupted phrases
  from ratcheting the pose. Channel 7 stays fixed for the microphone hold. This
  preserves visible speaking movement with bounded beats, not V2's random per-frame,
  emotion-dependent or amplitude-driven body policy.
- Cancellation best-effort holds touched channels at fresh controller pulses;
  partial transport failure faults the owner without retry/replay. Requests have
  no deferred queue. Stop/join talking motion before submitting park. A busy owner
  rejects concurrent requests; callers must not treat rejection as a completed park.
- `servo_port` explicitly opts live startup into the robot provider. Wake finishes
  before capture/camera startup. Reverse cleanup stops core workers and capture,
  joins talking motion, parks, closes serial, then acknowledges handoff. SIGTERM
  uses graceful runtime shutdown. A failed closer retains ownership until process
  exit rather than telling companions it is safe to reopen.
- Every live run now takes the cross-version controller lock and the V3 microphone
  resource lease. Servo-enabled runs also acquire the Maestro lease and check both
  serial aliases with read-only `lsof`. The old V2 supervisor must be unloaded;
  its lock polling does not acknowledge actual microphone release. `doctor` remains
  inert and does not inspect jobs or open devices.
- Robot capture obtains bounded 30-second hero-arm holds at its calibrated midpoint
  (5984), with 1.5-second settling. Capture completion, cancellation and any servo
  sequence release the hold. Samples must be wholly covered to qualify for voice
  enrollment. Renewing never retroactively qualifies audio. A robot chain discards
  unheld pending sample references before collecting held ones; accepted profiles
  are unchanged. Motion and settling exclude speaker identification/enrollment even with isolated audio;
  transcription retains human speech through movement, with separate motion-risk evidence;
  servo movement also invalidates old direction samples.

## Calibration and evidence boundaries

All positions use Maestro quarter-microseconds. Source constants are an initial
robot-specific calibration, not an import of effective local/environment overrides.
The V2 animation still declares headlift floor 1984; its current configuration
raises the minimum to 2600 and clamps writes. V3 explicitly parks at **2600**.
Throttle bounds intersect source configuration with V2's measured board endpoints:
shoulder 2176–9088, elbow/wrist 2048–9984. Park is 9088/9984/2048.

Maestro readback describes generated pulses, **not shaft feedback or obstruction
detection**. Receipts say `pulses_reached`, with the simulation/controller medium
and evidence type explicit. Timeouts cannot prove where the physical mechanism is.
A channel reading 0 is off since power-up (the robot was switched off). As in V2,
startup first holds each such channel at its unpowered rest pose (`UNPOWERED_REST`:
head park, arms at rest, throttle park) with bounded profiles, then runs the normal
wake choreography; the trace records `servo_unpowered_rest`. The board applies the
first pulses on its next 20 ms period, so startup polls (up to 1 s) until those
channels stop reading 0; V2 never re-read, V3 validates every move from readback. Live channels keep
their readings (a partly-off throttle arm is sent as one batch with its live joints
unchanged). A live reading outside the calibrated limits still rejects startup
before any write. No V2 park marker is copied or trusted. The Maestro jumps to the
first pulse after power-up, so a joint not hanging at rest snaps there, as in V2.
This replaced the manual `servo-commission` command (September 28), which blocked
every start after a power-off.

The serial opener uses exclusive access and 100-ms read/write timeouts, with no
discovery or reconnect. Optional operator dependencies are in `requirements-robot.txt`
and installed in V3's independent environment. The default servo port remains empty;
the prepared robot configuration is a separate ignored file. The existing
attention/gesture simulations remain simulations. October 1 adds separately started
physical adapters after successful robot startup, accepting only controller pulse
receipts. See [agency increment 2](AGENCY_IMPLEMENTATION_PLAN.md#increment-2-implementation--october-1-2026)
for finite trajectories, held-pose priority, cancellation and camera settling limits.

## October 3: continuous idle life and gaze

Supersedes the scheduling and head behaviour in the two September sections below (their
ranges and the single servo owner are kept). **Not yet run on the robot.**

Live run `30ec1b04` (10.4 minutes): the idle scheduler waited 4-9 quiet seconds, then one
request moved eight channels together. It ran 15 times; 5 were cut short by his own beats
and 6 by a phantom transcript of their own noise. The owner: "He sits motionless for a
while, then suddenly all the servos move at the same time", and asked for slow, measured
movement that is always going on and is not just back and forth.

**Idle life** (`idle_motion.py`, `servo_poses.idle_lanes`). Each part keeps its own rhythm:
head every 1-3 s, wrist 4-10 s, elbow 5-12 s, visor 6-14 s, poker arm 6-15 s, throttle arm
9-20 s, the microphone-carrying hero arm 30-60 s. A phrase moves the one to three parts
that are due, each starting 0.4-1.3 s after the last, then pauses 0.3-0.7 s. Every move
goes somewhere new (a bounded step from where the joint is, not a swing between two ends)
at Maestro speeds 5-18, a third to a half of the September idle; the visor sometimes does a
slow half-blink and the wrist sometimes rolls past and eases back. The visor stays in its
speaking range (6000-6750), above the camera. Hero-arm excursions are smaller (700-1400
from centre, was 1300-2000) and slower. No new servo writer: one request, one executor;
lanes may now start after a wait.

**Gaze.** Two modes, chosen by the conversation model:

- *Following people* (default). With a face in view the head moves half of the way toward
  centring it on each step (V2's centring gain 0.5 and dead zone), on the neck and the
  tilt, aiming to keep the face 40% of the way down the picture so the body is in view (in
  the live run the owner's face sat about 75% down, his body below the frame). Every 7-16 s
  it glances 12-24 degrees aside for 1.4-2.8 s and comes back, as V2's gaze engine did;
  the owner found V2's constant tracking creepy. With nobody in view it looks around the
  room every 6-13 s and drifts in between.
- *Holding a chosen direction.* After Rex's own `look_*` pose or silent look, the head
  stays there with small living movement for up to 30 s, or until the model uses the new
  `look_at_people` step, someone speaks (the voice reflex), or a drive ends. A look a
  person asked for is still a held pose (90 s against his own moves, until released).

`robot_pose_state.gaze` tells the model which mode is active. Talking sways around the
pitch the gaze chose instead of snapping back to level for each line.

**Camera and listening.** Head moves under about 5 degrees no longer count as a new camera
view, so scene settling, planner frames and arrival greetings are not reset by small
movement; larger looks still do. While Rex is transcribing or working on a reply, idle life
continues but stays calm: no view-changing head moves and no hero-arm swings. Confirmed
speech still stops idle movement at once.

**Microphone hold.** The hold that qualifies a capture for voice learning now tracks the
array arm (channel 7) only: other joints moving no longer release it, since with continuous
idle movement it would never have been valid. Their noise still marks the capture
(`motion_risk`) and keeps it out of learning. A hero-arm move ends the hold and a new one
waits 1.5 s after the arm stops.

**His own noise.** See [core operations](CORE_OPERATIONS.md) and `runtime.py`: a sound that
starts while his servos move is suspected self-noise until it has 0.25 s of continuous
voiced audio or 0.4 s within a second. Until then it does not stop idle movement, yield a
beat or hold up his initiative. The capture is still transcribed; a trusted transcript of
an unconfirmed capture is marked untrusted unless recognized clearly (average log
probability at least -0.1) or it is a stop or shutdown command. A suspected capture open
3.5 s gets a still moment so it can end.

Evidence: 935 blocked-I/O checks, with fake pulses and clocks covering staggered starts,
speed and range bounds, small versus view-changing head moves, face following with dead
zone, glances and stale-frame rejection, room scanning, held directions, calm mode, the
array-arm hold and the self-noise rules. In a simulated ten minutes the scheduler started
464 phrases with at most 3.7 s between starts; real moves take longer, so the robot will
run fewer. **Unverified on the robot:** how the motion looks, whether following is
comfortable, whether slow moves are quiet enough for the microphone, vertical aim (the
tilt scale is V2's estimate), clearance of the hero wrist (4600-7400, just beyond its talking
range) and poker arm while the throttle arm moves, servo temperature under continuous use, and voice-learning coverage.

## October 3: the head is free the moment a drive ends

Owner: after driving, "the head and other servos wait for the arm to un-park before he
turns his head or does any other motion". The un-park was one servo request of about five
seconds, and the owner runs one request at a time, so idle gaze, talking sway and gestures
were refused (`ServoBusy`) and the drive stance stayed held until it finished.

Now `begin_arm_return` releases the neck and head the instant the wheels stop, and
`return_arm` walks the throttle arm back to rest outside the single-request lock, using
the same staged route and clearance checks (since October 10 the hero elbow and poker
arm, elbow and wrist walk back beside it). Only those arm channels stay held; a request
that would command them, and park or shutdown, are refused until they are back (the next
drive and shutdown already wait for it). The Maestro
adapter takes each wire exchange whole so the two cannot interleave. A stop holds the arm
where it is. Checked with the pulse simulator only (four new checks, one with head moves
running in another thread during the return); on the robot, confirm the head resumes
following at once and the arm return is unchanged.

Second run the same day (`aa6782c0`): the owner still saw everything wait for the arm. The
log shows the servos were free, but idle movement waited about two seconds to resume (a
one-second after-drive hold plus its usual pause) and stayed still whenever he was
listening or answering, which was most moments after a drive: once nothing moved for the
whole five-second return because the owner was speaking. `IdleMotion.wake` now starts a
head-led phrase the instant the wheels stop and keeps idle movement going for six seconds
regardless of listening or working (the arm is moving then anyway); it never competes with
talking motion, which already moves the head. Two new pulse-simulator checks.

## Idle arms and elbow lifecycle

The user requested random hero/poker arm movement while awake. A low-priority
`IdleMotion` scheduler now waits a random 4–9 seconds of eligible quiet, then submits
one finite request through the existing ServoOwner. V2's broad hero excursion range
(1300–2000 quarter-microseconds from center) and quieter poker range (800–1500) are
retained, clamped to calibrated bounds. Positions cross center when an arm is already
offset. No new service, device owner, model or configuration switch is introduced.

Idle moves yield to VAD activity, an active capture, queued recognition/conversation,
model/output work and shutdown. Talking joins idle before its first target; shutdown
joins both workers before park. An idle request cannot queue behind speech or restart
after lifecycle cancellation. The normal hero excursion returns channel 7 to 5984;
cancellation holds current pulses, and the ordinary microphone hold recenters later.
Recentring does not begin mid-capture or while a reply is being generated/played.

September 27 (owner): each idle request also moves the head. With a face in view it
looks at the largest face three times in four (small jitter) and glances elsewhere
otherwise; with none it looks within ±35° of centre and up/down around the level drive
pose, biased downward as in V2's room scan. A head look marks the camera as re-framed
so the same people are not treated as arrivals. Head channels are held level and
centred while the base drives. Synthetic only; live cadence and feel are unverified.
Idle motion invalidates direction/hold evidence and retains existing motion-noise
exclusion. Input overlapping motion/settling may be rejected or lack enrollment
permission; validate the resulting listening gaps and movement feel on the robot.

Startup now raises the hero-arm elbow (channel 4) to V2's 6720 neutral alongside the
slow head wake. Shutdown already targets its 6300 gravity rest; the new startup rise
makes that droop visible even if Rex never spoke. The slower lifecycle profiles are
retained.

**561 blocked-I/O checks pass**, including six new idle/elbow contracts using fake
pulses/clocks: random bounds/return, startup-to-shutdown elbow, quiet scheduling,
speech preemption, hold invalidation/settling and shutdown latching. No additional
hardware run was performed. Live range/clearance, servo noise, interruption response,
voice-learning coverage, latency and CPU cost remain acceptance checks.

## Next integration gates

1. Perform the prepared [operator-tools cutover](OPERATOR_TOOLS_MIGRATION.md) with
   the owner present. V3's supervisor and console are implemented; installed jobs
   remain V2. Verify manual/supervised startup and actual microphone/serial handoff.
2. Commission the source calibration and cold-start pose evidence. Verify
   finite serial timeouts, manual takeover, interrupted park and transport faults.
3. Validate live talking motion, motion-audio exclusion, direction invalidation and
   held-microphone enrollment. These are implemented contracts, not yet measured
   mechanical/acoustic guarantees. Quiet interruptions can extend exclusion while
   the hold reacquires and settles; measure this conversational cost.
4. Validate the implemented finite model-selected attention/gestures through the
   same owner, with truthful pulse receipts and settled post-motion vision. Adapt richer speech expression
   from actual audio timing; avoid independent mood/idle motion authorities.

## September 25 live-feedback repair

The owner reported that the hero-arm elbow/wrist and head did not visibly move
while talking. The live trace showed the talking worker executing and being
cancelled at playback completion; this is the normal stop mechanism, not evidence
of a rejected start. Inspection found that the first adaptation never targeted
channels 4/5 or the neck, and its tiny headlift cycle waited for the slow throttle
lane before repeating. The repaired sequence restores those channels and lets
shorter lanes repeat independently. No V2 source or running service changed for
this repair, and no additional hardware test was operated by the agent.

**555 blocked-I/O checks pass** after this fix. Three added contracts cover repeated
head/elbow/wrist beats, drain completion with delayed fake pulse arrival, and bounds;
existing tests now require all five expressive head/arm channels through the actual
OutputOwner/TalkingMotion path and check interrupted head anchors. Live confirmation
of visibility, comfortable pace and acoustic impact remains pending.

### Speaking visor addition

At the owner's request, speaking now includes a separate visor lane between 6000
and 6640 quarter-microseconds, based on V2's 6000 speech floor. It repeats alongside
the existing gentle headtilt sway (startup anchor ±120), neck turns, elbow/wrist and
throttle. Neck turns retain their accepted pattern. Production playback coverage
now requires channels 0–5 plus throttle, with cancellation and delayed arrival
checks covering the additional lane. The 555-check blocked-I/O suite passes; live
visor visibility/camera occlusion and motion feel remain to be judged on the robot.

### Lifecycle pace adjustment after live feedback

The owner found startup/shutdown head movement much faster than V2. V2 combines
Maestro caps with software-stepped targets; V3 sends final targets directly. The
startup head/visor and neck sweep, and shutdown/park head/visor/elbow lane, now use
speed 12 and acceleration 3 instead of startup 30/28 with acceleration 8 and
shutdown 70/14. Positions and dwell times are retained. These lower controller
caps produce a slower glide without adding a second interpolation writer. This is
a first live-feedback tuning pass, not measured timing parity with V2; the owner
still needs to judge the physical pace.

## Verification (initial servo/operator checkpoint)

`.venv/bin/python tools/run_checks.py`: **528 checks passed**, including 48 new
servo/output, handoff, operator and robot-enrollment contracts across both slices.
`git diff --check` and dependency consistency checks passed.

The blocked-I/O suite exercises synthetic pulse readback, concurrent lifecycle lanes,
atomic throttle targets, profile application, stale requests, timeout, cancellation,
transport failures, full-down retraction, park latching and actual-playback hooks.
It cannot establish mechanical clearance, motion feel, audio contamination, physical
settling, obstruction handling or live console handoff. No hardware,
hosted provider or personal database was used. Live latency, median/tail movement
timing, combined-load memory and playback impact remain unmeasured.

Separate real-model check: the independently copied V2 wake classifier and its two
feature models processed 150 synthetic-silence frames (12 seconds) without opening
audio. One dev-Mac run measured 17.54 s model/import startup, 1.85 ms median and
2.19 ms p95 inference per 80-ms frame, and 190,627,840 bytes (~182 MiB) process peak
RSS. Maximum silence score was 0.000763, below the retained 0.92/four-frame trigger.
This is overhead/silence evidence, not positive wake accuracy or a live-M2 benchmark.

## September 25: model-selected held poses and capture repair

October 3: a held pose blocks Rex's own (self-initiated) poses and drives for 90 seconds
(`HOLD_YIELD_SECONDS`), then yields to his initiative. Before that, one "look right" in
conversation refused all of his own movement until someone asked for a release. Requests
from people, talking/idle lanes and `release_poses` are unchanged.

`set_robot_pose` is available for human turns when the live robot owner is active.
CoreState validates the named command and revision; OutputOwner reserves movement,
stops idle/talking, and delegates to the same versioned ServoOwner. There are no
raw model pulse targets or additional model calls. Commands are `visor_down`,
`visor_up`, `throttle_offer` (hold out the hand), `throttle_high_five`,
`throttle_down`, `throttle_rest`, and `release_poses` (resume normal movement).
Requested joints remain held through subsequent speech; other speaking and idle
lanes continue. Release routes the throttle to rest before re-enabling its speech
lane. Shutdown overrides held poses and parks normally. Cancellation holds current
pulses; a failed action cannot speak its proposed success acknowledgment.

V2's offer/high-five/rest/down targets are retained. Full-down transitions use
shoulder-only raising and established retraction waypoints, with all stages checked
before execution; V2's special coordinated direct high-five exception is not used.
Readback establishes controller pulse arrival, never observed shaft position.

The reported clipped requests coincided with `playback_echo_risk` rejection after
idle motion. Motion had been folded into echo, causing endpoint splits when motion
stopped. Frames/captures now carry separate `motion_risk`; endpointing preserves
speech across that transition and ASR can process the whole utterance. Perception
skips voice embedding, CoreState abstains attribution/learning, and microphone-hold
qualification remains false for motion-contaminated audio. Actual playback echo
still follows its existing rejection path. This fixes an identified clipping path;
logs without retained waveforms cannot explain every missed word.

Blocked-I/O verification: **573 checks passed** with synthetic devices, speech and
embeddings, including pose transitions/holding/release, cancellation/rejection,
execution-before-acknowledgment, blink shutdown and complete capture across motion.
Live ASR accuracy, natural-language model selection, physical clearance and animation
feel remain pending. No hardware was operated for this update; no added dependency
or resident model, and no measured live latency/resource figures.

### Resume-motion follow-up

Live user feedback confirmed eye blinks and offering the hand. The next request,
“You can resume normal motion now,” was correctly transcribed, but the model
answered in prose without executing `release_poses`; no release receipt exists
for that turn. The controller correctly retained its previous hold.

Each human-turn snapshot now includes current held body parts, and pose history
names the executed pose. The semantic tool description and hosted request guidance
explicitly require `release_poses` for requests to resume/relax after a hold,
including permission phrasing. No transcript regex or extra classifier was added.
A production-session regression covers offer, intervening conversation, release
before acknowledgment, renewed throttle speech movement, and cleared hold context.
574 blocked-I/O checks pass; scripted choices verify execution/context, not live
LLM selection. A fresh live language test after restarting V3 remains pending.

### Throttle idle and spoken shutdown

Throttle channels 8–10 now join idle hero/poker movement, using V2's five idle
poses, excluding the current pose, with 2.3–3.3-second motion and lower idle
profiles. The existing single idle scheduler owns cadence and cancellation;
there is no new writer. Held throttle poses remain fixed. Releasing poses restores
both idle and speech motion. Speech/capture still preempts idle, and shutdown parks.

A trusted standalone “shut down” or “shutdown” (also “Rex, shut down”) requests
normal runtime exit without an LLM call. It cancels ongoing work, lets the main
loop drain workers, then uses existing application cleanup for LED fade, servo
park, port closure and V2 handoff. It does not power off macOS. “Stop” remains
cancellation; longer discussion or untrusted transcripts do not trigger shutdown.
Existing playback-echo rejection still applies; reliable voice barge-in is not
newly provided. Terminal `shut down`/`shutdown` also exit normally.

588 blocked-I/O checks pass, including the shared runtime exit followed by park
and closed fake transport. All new evidence is synthetic; no robot or hosted
provider was operated. User confirmed the preceding resume/camera repairs worked.

### September 27: quieter throttle wrist

User reported throttle-wrist noise. Channel 10's pace multiplier is reduced from
2.2 to 1.1, scaling both speed and acceleration caps and proportional travel to
roughly half their prior values. This applies to idle, speech, requested poses,
startup and parking; shoulder/elbow multipliers and all position/clearance limits
are retained. Existing pulse-arrival waits still gate sequence progression.
This is a physical tuning change based on feedback, not measured acoustic evidence.
590 synthetic blocked-I/O checks pass; actual noise reduction and live motion pace
remain to be evaluated. No hardware was operated for this adjustment.

### September 27: all eleven model-selected joints

Session `6e3cf34ffd9b48ebbc26f83a79806aaa` started the servo provider,
but the correctly transcribed head-turn request received a refusal: the tool only
exposed visor/throttle poses. `set_robot_pose` now adds head left/right/center using
the existing startup neck targets (4000/8000/5472). `set_robot_joints` exposes neck,
head lift/tilt, visor, hero elbow/wrist, poker arm, microphone-bearing hero arm,
and throttle shoulder/elbow/wrist. Each field is null (unchanged) or a finite
0–100 percentage of the existing calibrated pulse range, not an angle. No raw
serial command, speed override, extra model or V2 extraction was added.

Partial throttle requests fill unspecified throttle axes from fresh controller
readback and retain the three-channel batch, existing bridge routes and clearance
validation. The whole route is validated before any joint writes. Arbitrary
combinations can be rejected; calibrated endpoints do not guarantee every combined
pose is mechanically clear. Acknowledgment follows pulse arrival, not visual proof.

Every selected joint holds through speech and idle motion. Holding channel 7 also
prevents microphone recentering; identity enrollment remains unavailable until
release and a newly settled microphone hold. `release_poses` clears all holds;
shutdown overrides them. Context names all held body parts. The model uses these
tools for explicit human requests; autonomous physical gaze remains separate.

597 blocked-I/O checks pass with synthetic serial/PCM, fake clocks and scripted
model choices. New coverage exercises every joint through Session, head motion
before speech, hold persistence, partial throttle batches, unsafe-route rejection
without writes, cancellation, release/reacquisition and park. No hardware or hosted
model calls were made. Live natural-language selection, directions, clearance and
motion feel still require validation. The reported clipped transcription remains
unresolved without recorded audio; this servo change does not claim an ASR fix.

### September 28: look directions

Session `7db71dda77e2445ea7daff18f4e8fb49`: "look down and to your left" became
`set_robot_joints` neck 20%, head_lift 30%. Lift 30% (4143) is above the level stance
(3600), so the head rose instead of lowering; tilt, the main downward axis, was untouched.
"Look to your left" then chose `head_left` (4000, about 19 degrees) and was spoken as
"Turning left." Because 4000 is right of the earlier 3379, the head swung back right.

Look poses (renamed `look_*` below) now include down, up and level. Down/up use
idle_head's conventions (lower lift, higher tilt looks down) at 3200/5300 and 4200/4000;
level is the drive stance. `look_left` mirrors `look_right` about NECK_CENTER (2944,
about 33 degrees); the startup scan keeps 4000. The new down/up values and the wider left
are unverified on the robot: confirm the look is visible and clear of the body.

### September 28 (second run): chained steps, wave, look then describe

Session `af692e206e1e4716a86936530266b9c8`: "look to your right and then down" ran only
`head_right`, spoken "Turning right."; "can you wave your hand?" had no wave, so Rex held
out the throttle hand; "look down and tell me what you see" was first answered from the
old view, then repeated, moving the head with nothing said.

`set_robot_pose` now takes `poses`, an ordered list of steps the conversation model
composes from the request ("look right, then left, then up, then down" is four steps).
Fixed combined poses were removed: side steps keep up/down and vice versa, so chaining
covers them. Steps run in order after each arrives; the first failure stops the rest and
Rex says the movement did not complete. Poses are named `look_*` so the model describes
them as looking; the wheels remain drive_base. `look_after` takes a fresh camera frame
0.3 s after the last step and makes one bounded continuation call to answer it.

`wave` extracts V2's wave-back (`sequences/animations.py` at fb96f08, clean tree): hero
arm to 4800 and elbow to 6300, the wrist sweeps its full calibrated travel four times at
V2's auto speed for a 0.32 s swing, then channels 4/5/7 return to where they started.
Nothing is held. V2's in-sync elbow bob is omitted (one speed per beat). Moving
channel 7 moves the microphone array, so bearings are invalidated as for any arm move.
Synthetic serial and a fake camera cover these contracts. Live wave speed,
visible sweep amplitude, arm clearance and the model's step choices remain unverified.

### September 28 (owner requests): no caps, mixed sequences, voice reflex

The owner does not want caps on motion commands. A step list has no maximum length, and
the Maestro adapter now accepts speed/acceleration 0 (the board's own "no limit") when
choreography chooses it; the wave's wrist sweeps use it, as V2 did. Existing profiles are
unchanged. Calibrated pulse limits and throttle clearance checks remain.

Steps may mix moves and lights in order: `blink` (three eye blinks) and `lights_<pattern>`
(about 2.5 s of an LED animation) run through the LED owner between servo steps, only
when that pattern is available. "Look left then right then up then down then hold your
hand out and blink your eyes" is one call of six steps and one model generation.

Voice reflex (`voice_reflex.py`): once an utterance has 0.3 s of voiced, non-echo speech
and the array reports one dominant bearing (3+ strong samples), the neck turns to face it
at speed 60/acceleration 15 before recognition or any model call. Only the front half
(within 90 degrees) moves the head; bearings beyond the neck's travel turn it fully to
that side. It fires once per utterance, holds nothing, re-frames the camera like an idle
glance, and yields to an explicit held neck pose. A model move, base motion or talking
stops an unfinished reflex instead of failing busy. It assumes array forward is body
forward, as drive already does. In session af692e20 Bret's clear bearings were 13-39
degrees left and the TV's -142, so the TV would not have turned the head. Neck noise
during the utterance is flagged as motion (lower voice confidence, not discarded).
Synthetic checks only; live latency, aim and feel are unverified.

### September 28 (third run): acknowledgment order and reflex aim

Session `5e97b13a661b4a6b88a45faecebed371`: a five-step sequence (hand, look left, visor
down/up, blink) ran correctly, but "On it." played only after it. `set_robot_pose` now has
`say_before`, spoken and heard before the first step (talking motion and the steps share
the servo owner); `text` still waits for every step.

The reflex fired once and moved about 6 degrees. The array put Bret 6-9 degrees right in
every clear utterance while the camera saw his face 32-38 degrees left, at the frame edge;
the previous session's array and camera agreed within about 10 degrees. The cause (array
or arm mounting, or beam selection) is not established from the logs. When a fresh face
is within 60 degrees of the voice bearing, the reflex now aims at the face; otherwise at
the voice. "I'm over here" after a held look left was skipped as `neck_held`; a new voice
now takes the neck back from an earlier held look. Synthetic checks only.
