# Telepresence mode: implementation plan

Status, November 16, 2026: **plan with the owner's decisions recorded ([below](#owner-decisions-november-16));
nothing here is built, and this document authorizes no code, firmware or hardware operation.** Owner's idea: a Mac app that wakes the robot into a mode with
no personality and no models, where Rex is a conduit for the owner's voice. The owner hears the room,
sees through the camera, drives the base and moves the head and head lift. The supervisor launches a
separate program for it. Access is over the owner's home WireGuard VPN only.

## What the session is like

- **At the owner's end:** open the app, press Connect. Within a few seconds the room is audible and
  the camera is on screen. Unmute to talk; the voice comes out of Rex's speaker. Keys, mouse or a game
  controller drive the base and aim the head. Hang Up parks him.
- **At home:** Rex's servos wake, the visor opens, and the lights show a look used for nothing else;
  the lights are the only cue that the owner has connected. His mouth lights move with the owner's
  voice. He does not listen for meaning, reply, greet, remember or move on his own.
- **When it ends** (hang up or a lost link): wheels stop, servos park, the visor closes, lights fade,
  and the supervisor goes back to listening for "wake up rex".

## Three pieces

| Piece | Where | Job |
| --- | --- | --- |
| Telepresence program | `python -m r3x telepresence` in this package | Owns the hardware for the session and carries audio, video, commands and telemetry. No conversation owner, no store. |
| Supervisor trigger | `operator_tools/rex_supervisor.py` | Answers status, starts and stops the program on request. Media never passes through it. |
| Mac app | New SwiftUI app in a folder of this repository (proposal: `apps/telepresence/`) | Connect, see, hear, talk, steer. |

The program belongs inside `r3x` rather than beside it because the limits live in the existing owners.
A second driver for the servos or wheels would break "one active implementation per responsibility".
Nothing loads at import, so a command that starts no models is possible today.

## What already exists

| Need | Existing piece | Gap |
| --- | --- | --- |
| Services step aside, ports and microphone released | `/tmp/djr3x-main.lock`, `ControllerHandoff` (0.7 s measured) | None: telepresence takes the same lock as `live`. |
| Drive by stick with a deadman | `DriveBase.begin_manual`/`manual`/`end_manual` (dashboard Motivator): 10 Hz setpoints, speed and turn caps, front path check, arm-swing check, firmware rear block, zero when stale over 0.3 s, firmware 300 ms deadman | Works on the robot (owner, November 16). A network link needs its own staleness rules (below). |
| Head, lift and visor by hand | `RobotServos.begin_operator`/`operator_pose`/`end_operator`: calibrated limits, paired-joint routes, clearance checks, Rex's own motion refused | Today the override refuses drives that need the arm park. Telepresence needs both at once (below). |
| Mouth lights from audio | `LEDs.pcm` follows playback PCM | A telepresence look for eyes and chest (sentry's `guard` is the pattern to copy). |
| Room audio with echo removed | ReSpeaker Flex (XVF3800) channel 0: hardware echo cancel, beamforming, noise suppression, gain. Playback through the same device gives it the echo reference | Talk-over has never been validated in V3; `live` gates the microphone while Rex plays instead. |
| Playback | `AudioSink` / `BufferedOutput` | A small jitter buffer for a live stream; a fixed gain and limiter (the −20 LUFS leveler works per phrase). |
| Camera frames | `adapters/camera.py` (ffmpeg AVFoundation capture) | `Camera` also loads the face models. The capture half must be usable alone. |
| Base readings | Drive telemetry, radar, depth matrices, battery | Sent to the app as a small status message. |
| Launch, logs, update deferral | Supervisor's `_launch_controller` and child handling | A second launch reason and command; no relaunch-on-wedge for this mode. |

Not started in this mode: ASR, VAD, voice and face recognition, object and room models, the reply
model, the planner, ElevenLabs, Breeze, the wake word, the stop keyword, news, memory and the SQLite
store. With no stop keyword, nobody at home can halt him by voice; the owner's Stop, the obstacle
checks, the deadman and the lost-link park are what stop the wheels.

## Robot program

**Start.** Take the controller lock and wait for the services to release. Open the servo board, LED
board, drive link, radar, microphone, speaker and camera that settings enable. Wake the servos to their
awake poses and open the visor, skipping the startup sounds, the please-wait and ready lines and the
hardware notice. Show the telepresence lights, with no sound. Then accept one client.

**Servo mode.** A new owner mode, "telepresence stance": arms held in the drive stance for the whole
session, head yaw, pitch, side tilt, lift and visor under operator control, everything else refused
(idle life, talking motion, listening cue, reflexes, gestures, expressions). This removes the conflict
between the operator override and the arm park. Open point: the drive stance levels the head while the
wheels run. Find out why before allowing looks while rolling; start with free looks at a standstill.

**Driving.** `begin_manual` at the first stick movement, `manual` for each setpoint, `end_manual` at
hang up. All existing gates stay. Added for a network link:

- The app sends setpoints at 10 Hz with a sequence number. The robot applies only the newest and drops
  any that arrive out of order or in a burst after a stall.
- No fresh setpoint for 0.3 s sends zero (as the dashboard does); the firmware deadman is behind that.
- The robot measures round-trip time with its own pings. Above a limit (proposal: 400 ms) it refuses
  forward and turn setpoints and tells the app why.
- The app zeroes the stick when the newest video frame is older than a limit (proposal: 0.5 s), when
  the window loses focus, and on Stop (space bar).
- Link silent for 30 s: park and exit, as for hang up.

**Audio.** Room to owner: channel 0 at 16 kHz, straight to the link, with no gate. Owner to room: the
received stream into the same XVF3800 output. If the measured echo is poor, the fallback is half duplex:
the room channel is ducked while the owner's voice is playing.

**Video.** One camera stream, downscaled (proposal: 960×540, 15 frames a second), each frame stamped
with the robot's clock so the app can show its age.

**Record.** A session trace under `local/sessions/` with connect, disconnect, commands, drive receipts
and link statistics. No audio, no pictures, no store writes.

## Link between app and robot

Carried over WireGuard, so NAT traversal and outside exposure are not problems to solve.

| Option | For | Against |
| --- | --- | --- |
| **A. Plain streams (owner's choice to start):** one authenticated connection carrying audio frames, JPEG frames, and small JSON messages for commands and status | Almost no new dependency on either end; Apple's own frameworks cover the app; easy to fake in blocked-I/O checks | No built-in loss concealment or adaptive video; a stall delays audio; JPEG at this size is roughly 2–5 Mbit/s of home upload |
| B. WebRTC (`aiortc` on the robot, a WebRTC library or web view in the app) | Jitter buffers, loss concealment, adaptive video, unreliable data channel for setpoints; a browser or phone could be a client | A new framework on the robot with software video encoding, and a large binary or a web view in the app; AGENTS.md asks for a demonstrated need and measured cost first |

Decided: build A, measure it from off-site, and move to B only if the numbers demand it
(proposal: mouth-to-ear delay over 400 ms, or audible dropouts more than a few times a minute). The
message layer should not care which carries it. Check the current state of `aiortc` on Python 3.11 and
Apple silicon before weighing B; this plan did not.

## Supervisor trigger

A small listener in the supervisor with three requests: status, start, stop.

- **Status:** asleep, Rex awake, sentry mode, telepresence running, plus battery and charging.
- **Start while asleep:** play nothing, launch `python -m r3x telepresence` as the child, exactly as it
  launches `live` (same lock, same console log, updates fetched but not applied until exit).
- **Start while Rex is awake:** refused, with the reason. A request never ends a running
  conversation, a Jeopardy game or sentry mode.
- **Exit:** any exit returns to wake-word listening. A failed start is reported to the app with the
  readiness report's reason; a failed start under launchd is otherwise silent.
- **While it runs:** the supervisor is dormant, so "wake up rex" does nothing until the session ends.

## Access and privacy

- Off by default: `telepresence_enabled = false`. Proposed settings, each with one consumer:
  `telepresence_bind` (the address and port to listen on; never all interfaces),
  `telepresence_allowed_sources` (the WireGuard subnet), and `TELEPRESENCE_TOKEN` in `local/secrets.toml`.
- One client at a time; a second is refused. The app keeps the token in the Keychain.
- People at home get one sign: the lights, for the whole session. The owner's microphone starts muted.
- Camera pixels and room audio go only to the authenticated client and are not saved on either end.

## Mac app

- **Connect screen:** robot address, state from the supervisor, battery.
- **Session:** video with frame age and round-trip time, mute and push-to-talk, Stop, Hang Up.
  Voice processing on the app's own microphone, so the owner can use speakers.
- **Steering:** W/A/S/D or arrows held to move; drag on the video to aim the head; keys for lift and
  visor; a game controller as well (left stick wheels, right stick head).
- **Status strip:** why a move was refused (path blocked, arm swing, link too slow), obstacle and depth
  readings, charge.
- A dev Mac with no ports can run the program against the simulated body and base, so the app can be
  built without the robot.

## Stages

Each stage ends with its own checks; live checks are labeled separately from blocked-I/O ones.

0. **Measurements, no motion.** Talk-over echo through the XVF3800 with a file playing; link delay and
   loss from off-site over WireGuard; camera capture without the face models; CPU on the M2.
1. **Conduit only.** The program with audio both ways, video, lights and the visor; started by hand;
   a throwaway test client. No wheels, no head control.
2. **Mac app, first version.** Connect, see, hear, talk, hang up.
3. **Head, lift and visor,** with the telepresence stance.
4. **Supervisor trigger,** status and the refusal rules.
5. **Driving,** at the dashboard joystick's caps. No supervised stage first (owner).
6. **Later:** game controller polish, a link from a sentry alert to a telepresence session.

**Contract checks to write** (fake transport and clocks, simulated ESP32, software Maestro):
- no model or store opens in this mode;
- a lost link zeroes the wheels within the deadman and parks after the timeout;
- late, reordered and burst setpoints are dropped;
- a wrong token, a second client and a disallowed source are refused;
- an obstacle holds forward and the reason reaches the client;
- operator limits hold and Rex's own motion is refused;
- supervisor states: asleep, awake, hand-started Rex, failed start, exit back to listening.

## Risks

- **Drop-offs: not a risk here.** The base cannot sense stairs or ledges, but the owner lives on the
  first floor with none to reach (owner, November 16).
- **Echo.** If the hardware echo cancel does not hold up under talk-over, the mode is half duplex.
- **Motor and servo noise** in the room audio while the owner moves him.
- **Head response.** The Maestro's slow ramps suit Rex's character; aiming a camera by hand may need
  quicker operator speeds, within the same limits.
- **Charging.** What the base allows while on the charger needs checking before a remote drive off it.
- **Manual override.** The dashboard's joystick works on the robot (owner, November 16); its manual
  servo override, which head control builds on, has no robot report yet.

## Owner decisions (November 16)

| Question | Decision |
| --- | --- |
| Link | Plain streams to start. |
| Cue at home | The lights; no sound or spoken line. |
| Stop keyword | None. No models at all in this mode. |
| Takeover | None. A request starts telepresence only when Rex is asleep. |
| Driving with nobody home | Allowed from the start, with no supervised runs first. |
| App | A folder in this repository. |
| Trigger | A listener in the supervisor. |
