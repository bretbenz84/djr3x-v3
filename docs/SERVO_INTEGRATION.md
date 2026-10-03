# Servo integration: software ready for supervised commissioning

September 25, 2026. The owner requested adapting V2 servos, including the current
throttle-arm working tree, preserving recognizable startup/shutdown choreography
and movement while talking. The follow-on adds optional live composition, confirmed
handoff, operator tools and microphone protection. **Not activated or validated on
hardware.** The owner asked to leave V2 running and prepare cutover for later.

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
