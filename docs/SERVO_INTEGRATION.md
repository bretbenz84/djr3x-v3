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
  is not needed for this slice and is not exposed as a gesture.
- The production `OutputOwner` accepts optional talking motion. Actual playback
  start launches its worker; normal completion, failure and cancellation stop it.
  TTS generation and queued PCM do not start movement. The worker shares the servo
  executor and output's motion reservation; callbacks never write serial. A stopped
  phrase holds current output pulses rather than issuing a late neutral/visor reset.
  Motion receipts and errors are recorded separately from speech delivery.
- The initial talking repertoire uses small headlift emphasis and two V2 throttle
  speech poses. It repeats only during playback. Heavy headtilt, gaze and the
  microphone-bearing hero arm are left alone. This is a bounded initial adaptation,
  not all of V2's emotion-dependent body/visor motion or amplitude following.
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
  are unchanged. Motion and settling gate microphone input even with isolated audio;
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
Unknown/zero or partial cold-start pulse state is rejected without a target write.
No V2 park marker is copied or trusted. `operator servo-commission --confirm-rest`
is an explicit one-time initializer for **all-zero outputs only**, after the operator
physically verifies every joint at the documented rest pose. It applies bounded
profiles before first pulses and saves no persistent pose-truth marker. It cannot
infer where an unpowered mechanism is. Live commissioning remains required.

The serial opener uses exclusive access and 100-ms read/write timeouts, with no
discovery or reconnect. Optional operator dependencies are in `requirements-robot.txt`
and installed in V3's independent environment. The default servo port remains empty;
the prepared robot configuration is a separate ignored file. The existing
attention/gesture simulations remain simulations; they do not accept these receipts
as physical attention evidence.

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
4. Extend model-selected attention/gestures through the same owner, with truthful
   physical evidence and fresh post-motion vision. Adapt richer speech expression
   from actual audio timing; avoid independent mood/idle motion authorities.

## Verification

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
