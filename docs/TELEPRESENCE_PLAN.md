# Telepresence mode: implementation plan

Status, November 16, 2026: **stages 0-5 are implemented in software. Two calls have run on the robot over
WireGuard: audio both ways and video, started by hand (stage 1 and the app). Head control, driving and
the supervisor trigger ([below](#stages-3-5-implemented--november-16)): a call started through the
trigger with head aiming works on the robot (owner, November 16); driving over the link has not been
used yet. Stage 6 is not built.** The owner's decisions are recorded [below](#owner-decisions-november-16);
this document authorizes no further code, firmware or hardware operation. Owner's idea: a Mac app that wakes the robot into a mode with
no personality and no models, where Rex is a conduit for the owner's voice. The owner hears the room,
sees through the camera, drives the base and moves the head and head lift. The supervisor launches a
separate program for it. Access is over the owner's home WireGuard VPN only.

## What the session is like

- **At the owner's end:** open the app, press Connect. Within a few seconds the room is audible and
  the camera is on screen. Unmute to talk; the voice comes out of Rex's speaker. Keys, mouse or a game
  controller drive the base and aim the head. Hang Up parks him.
- **At home:** Rex's servos wake, the visor opens, and the lights show a look used for nothing else.
  Before the call is taken he says, in his own voice, that the owner is calling in through him
  (owner, November 16; it had been the lights alone). His mouth lights move with the owner's voice. He does not listen for meaning, reply, greet, remember or move on his own.
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
| Base readings | Drive telemetry, depth matrices, battery (not the radar: owner, November 16) | Sent to the app as a small status message. |
| Launch, logs, update deferral | Supervisor's `_launch_controller` and child handling | A second launch reason and command; no relaunch-on-wedge for this mode. |

Not started in this mode: ASR, VAD, voice and face recognition, object and room models, the reply
model, the planner, ElevenLabs, Breeze, the wake word, the stop keyword, news, memory and the SQLite
store. With no stop keyword, nobody at home can halt him by voice; the owner's Stop, the obstacle
checks, the deadman and the lost-link park are what stop the wheels.

## Robot program

**Start.** Take the controller lock and wait for the services to release. Open the servo board, LED
board, drive link, microphone, speaker and camera that settings enable (never the radar). Wake the servos to their
awake poses and open the visor, skipping the startup sounds, the please-wait and ready lines and the
hardware notice. Show the telepresence lights, play the call's spoken line, then accept one client.

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
- People at home get two signs: the spoken line as the call starts, and the lights for the whole
  session. The owner's microphone starts muted.
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

0. **Measurements, no motion** (tools written November 16; not yet run on the robot). Talk-over echo through the XVF3800 with a file playing; link delay and
   loss from off-site over WireGuard; camera capture without the face models; CPU on the M2.
1. **Conduit only** (software November 16; not yet run on the robot). The program with audio both ways, video, lights and the visor; started by hand;
   a throwaway test client. No wheels, no head control.
2. **Mac app, first version** (software November 16, [below](#stage-2-the-mac-app--november-16); not yet used on a real call). Connect, see, hear, talk, hang up.
3. **Head, lift and visor,** with the telepresence stance (software November 16, [below](#stages-3-5-implemented--november-16)).
4. **Supervisor trigger,** status and the refusal rules (software November 16; the listener runs on the robot).
5. **Driving,** at the dashboard joystick's caps. No supervised stage first (owner). (Software November 16.)
6. **Later:** game controller polish, a link from a sentry alert to a telepresence session.

**Contract checks to write** (fake transport and clocks, simulated ESP32, software Maestro):
- no model or store opens in this mode;
- a lost link zeroes the wheels within the deadman and parks after the timeout;
- late, reordered and burst setpoints are dropped;
- a wrong token, a second client and a disallowed source are refused;
- an obstacle holds forward and the reason reaches the client;
- operator limits hold and Rex's own motion is refused;
- supervisor states: asleep, awake, hand-started Rex, failed start, exit back to listening.

## Stages 0 and 1, implemented — November 16

Owner request, on the dev Mac (no supervisor there, and none installed). **Nothing below has run on the
robot.** Evidence so far: 41 blocked-I/O contract checks (fake sockets, devices and clocks) and one
5-second loopback run of the link probe on the dev Mac (127.0.0.1, no devices: 250 audio frames echoed,
median round trip 0.6 ms, nothing dropped), which shows the wire format and sender work, not any real
network.

**Stage 1, the program** (`src/r3x/telepresence/`, started by hand):

```sh
.venv/bin/python -m r3x telepresence-setup          # once per robot: the token, in local/secrets.toml
.venv/bin/python -m r3x telepresence [--camera]      # on the robot, with Rex off
.venv/bin/python tools/telepresence_client.py ROBOT_IP:PORT   # on the calling Mac, with headphones
```

- **Settings:** `telepresence_listen` (one `IP:port`; never every interface, port from 1024) and
  `telepresence_allowed_networks` (at least one, at most eight). The calling Mac's `local/secrets.toml`
  needs the same `telepresence_token` line as the robot's; `TELEPRESENCE_TOKEN` overrides it.
- **Start:** the program checks readiness before opening anything. It then takes the same live
  lease and controller handoff as `live`, so it never takes over a running Rex. Start order:
  - the speaker, the LED board and the servo wake (no startup sounds);
  - the microphone and the camera (frames only, no face models, scaled to 960 px by ffmpeg);
  - the JPEG video sender and the listener.

  The visor then opens and the lights show the telepresence look.
- **Lights:** magenta eyes (a colour no feeling uses), refreshed every 3 s. The mouth moves with
  whatever plays, which is the owner's voice. The chest runs its plain speaking pattern. This uses
  existing LED firmware commands; no firmware changed.
- **Link:** one TCP connection, the format in `protocol.py`.
  - **Sign-in:** the robot sends a challenge and the client answers with an HMAC of it under the
    token, so the token never crosses the wire. A wrong token or a second client is refused with
    the reason. A source outside the allowed networks is closed with nothing said.
  - **Sending order:** JSON first, then audio, then only the newest video frame. Room audio waiting
    over 0.5 s drops its oldest frames rather than adding delay.
- **Audio:**
  - Room to owner: microphone channel 0 at 16 kHz with no gate, as the plan says.
  - Owner to room: one output stream for the whole call, behind a 0.12 s jitter buffer. It
    restarts from silence after running dry, and anything beyond 0.4 s buffered is cut back. The
    stream is stopped, never aborted while running (aborting silenced the reSpeaker's microphone
    before).
- **Video:** 15 frames a second at quality 70, each stamped with the robot's clock. The client
  shows the frame's age using a clock offset measured from its pings.
- **Call endings:**
  - the client hangs up;
  - nobody connects within 120 s;
  - the link stays gone 30 s (a dropped link may come back meanwhile);
  - SIGTERM or Ctrl-C.

  A client silent for 5 s, or a send stuck for 5 s, is disconnected. Shutdown parks the servos with
  the light fade, as Rex's own shutdown does.
- **Records:** connects, refusals, link ends and a stats row every 10 s go to `local/sessions/`. A
  stats row has round trips, dropped and late audio, video encode time, and the CPU and peak memory
  of this process and ffmpeg. No audio or pictures are kept.
- **Test client** (`tools/telepresence_client.py`): plays the room, shows the camera (m mutes, q
  hangs up) and starts muted. It has no echo cancelling, so use headphones. It prints a summary of
  round trips, frame age and underruns.

**Stage 0, the measurements:** run each on the robot with Rex off. Each saves a JSON report under
`local/telepresence/`.

| Tool | Measures |
| --- | --- |
| `tools/measure_talkover_echo.py` | Plays a speech-like test voice (or `--wav`) through the configured output while recording every input channel. Per channel: the quiet floor, the level during the voice, its rise above the floor, and its correlation with the voice at the echo delay. On the Flex it also compares channels 0 and 1 against a raw capsule. Its full-duplex hint (within about 6 dB, correlation under 0.1) is a proposal, not a calibrated gate. |
| `tools/measure_telepresence_link.py serve` / `call ROBOT_IP:PORT` | The real wire format with no devices: 50 audio frames a second echoed back and a video-sized frame 15 times a second acknowledged. Reports round trips (median, p95, p99, max, share over 250 ms), the longest gap and the sender's dropped audio. The echo delay leaves out the microphone, speaker and jitter buffer. |
| `tools/measure_telepresence_camera.py` | The capture-only camera at the telepresence size: frames delivered, JPEG encode time and size, the upload that implies, and the CPU and memory of Python and ffmpeg each second. |

**Open:**
- None of the three measurements has been run on the robot or from off-site.
- The program has not run on the robot. Not yet checked:
  - the servo wake and visor;
  - the magenta eyes on the strip;
  - the mouth following the owner's voice;
  - echo during talk-over;
  - playback of a 16 kHz stream on the XVF3800.
- Whether TCP's stalls on a lossy link are acceptable is for the off-site link measurement to say.

## Stage 2, the Mac app — November 16

Owner request. Rex Call (`apps/telepresence/`, [how to build and use it](../apps/telepresence/README.md)) is a
SwiftUI app with no outside dependencies. It is built by `build.sh` into an ad-hoc-signed `.app`.

- **Calling:** enter the address and the token once; the token goes in the keychain. The app keeps
  calling for up to 60 s while he wakes. A refusal is explained in words: wrong token, someone else on
  a call, telepresence not running, the VPN down.
- **On a call:**
  - You see the camera, with the picture's age, the frame rate and the round trip.
  - You hear the room through a jitter buffer with the robot's own sizes (0.12 s start, 0.4 s most).
  - You start muted. Use the button or M to mute and unmute, and hold Space to talk. A meter shows
    your level.
- **Echo:** the microphone goes through macOS voice processing (echo cancelling). The room plays
  through the same audio engine, which makes it the canceller's reference, so speakers should work.
  If voice processing cannot start, the call view says to use headphones.
- **Ending:** Hang Up, Esc or quitting tells Rex to park.
- **Voice backlog:** voice still waiting to go out past 0.5 s is dropped rather than delayed. Unlike
  the robot, the app drops the newest frame rather than the oldest.
- **Evidence, all on the dev Mac:**
  - 6 Swift checks of the core against reference bytes from the Python robot: the sign-in proof, an
    audio frame and a JSON frame, plus the jitter buffer.
  - A headless run of the app's own connection code (`rexcall-probe`) against
    `tools/telepresence_fake_robot.py`, the robot's real call code with a synthetic room and camera,
    over localhost for 6 s:
    - signed in;
    - 187 room-audio frames (6.0 s) and 81 video frames arrived;
    - 251 voice frames went out, none dropped, and the fake robot measured them at the level sent;
    - the hang-up ended the robot's call.
  - A wrong token was refused with its reason.
  - The built app launched and stayed up.
- **Not yet checked:**
  - a real call in the app (microphone permission, voice processing, the room through the speakers,
    the picture on screen);
  - a call with the robot, and from off-site;
  - echo with speakers.

### First call from the owner, and the sound repair — November 16

The owner's first call from the dev Mac to the robot ended with "This Mac's sound could not start",
and the app had no way to choose a microphone or speaker.

**Cause.** The system log showed macOS voice processing refusing to start:
`client-side input and output formats do not match` (-10875). With voice processing on, its
microphone side runs at 16 kHz (4 channels) and its speaker side at 16 kHz (2 channels). The app's
mixer had wired itself to the speaker side at 44.1 kHz the moment it was first used. That Mac's
default devices are a 16 kHz C922 webcam microphone and 48 kHz headphones.

**Repair** (`Sources/RexCallAudio`):
- The speaker-side format is read before the mixer is touched, and the mixer is connected in it.
- If voice processing still fails, the call starts without it and says so.
- The microphone and speaker can be picked before or during a call.
- A failed start shows the macOS error.

**Learned on that Mac:**
- Voice processing builds its own device pairing from the system defaults and ignores a device set
  on the engine. The log showed it keep the C922 and headphones after the app chose others.
- On this macOS the engine's input and output are one unit, so setting one device moves both.

So a picked device uses a plain path instead: a speaker-only engine on the chosen output, and a
capture session on the chosen microphone, which delivers 16 kHz mono itself. That path has no echo
cancelling, so echo cancelling is offered only with both pickers on System default.

**Evidence** (`rexcall-audio-check` on the dev Mac, with real devices and silence played):

| Devices | Started | Echo cancelling |
| --- | --- | --- |
| C922 + headphones (system defaults; the pair that failed) | yes | on |
| Defaults, echo cancelling off | yes | off |
| C922 + Mac mini speakers | yes | off |
| CalDigit + Dell display | yes | off |
| C922 + headphones, picked by name | yes | off |

- The speaker kept up with the stream; nothing was dropped.
- The rebuilt app launched.
- **Not yet checked:** a call from the app after the repair, and how well echo cancelling works with
  speakers.

## First calls on the robot — November 16

Set up on the robot (`r3x-m2`, 192.168.1.100):
- `telepresence_listen = "192.168.1.100:47800"` and `telepresence_allowed_networks = ["192.168.1.0/24"]`;
- the token made by `telepresence-setup`.

WireGuard clients (10.9.0.0/24) arrive from the VPN box's home address, 192.168.1.253, so the home
network rule covers them.

**Live evidence:**
- **Before any call:** the full blocked-I/O suite passed on the robot (1,916). The link probe ran
  against the real address from the robot itself: 8 s, nothing dropped, round trip 0.4 ms median, so
  this says nothing about the network.
- **The camera at the telepresence size:**
  - started from a coding session, it produced no frame, because macOS's camera permission follows
    whatever starts the program;
  - started by launchd, as the supervisor starts Rex, it came up in 1.4 s at 960×540;
  - it delivered **4.3 new frames a second**, against 15 asked for. The cause is not found; the small
    JPEGs (13.5 kB) suggest a dim room;
  - cost: Python 6% and ffmpeg 22% of one core.
- **Two calls from the app over WireGuard**, the program started as a one-off launchd job:
  - The first call signed in, then the app hung up after 3 s (cause not known on the robot's side).
  - The second lasted 50 s:
    - round trip 19 ms median, 27 ms at most;
    - room audio steady, with 52 frames (about 1 s) dropped while the call began and none after;
    - the owner's voice 4.2 s, one underrun, nothing dropped;
    - video 248 frames, about 5 a second, encoding 5 ms each;
    - the call program used about 10% of one core and 89 MB, ffmpeg 11-21% and 199 MB.
  - Both ended with a clean park and the supervisor listening again.
- **Not checked:**
  - the magenta eyes and the mouth on the strip (only the commands' sending is known);
  - echo during talk-over;
  - a call from off-site.

## Stages 3-5, implemented — November 16

Owner request: "implement the rest of the plan", robot side only (the Mac app is done separately).
**Software and blocked-I/O checks; nothing below has moved the robot yet.**

**What a call is like now.**
- **Starting:**
  - The app asks the supervisor to start a call; it runs the call program only while Rex is asleep.
  - The servos wake, the visor opens and the arms take the **telepresence stance**: the drive stance
    (throttle parked, hero elbow and poker arm up, neck centred, head level), held all call.
  - His own motion stays off for the whole call: idle life, talking motion, the listening cue,
    reflexes, gestures, expressions, and the back-off reflex on the wheels.
- **Aiming the head:**
  - The caller moves the neck, head pitch and tilt, lift and visor at a person's look pace, inside
    the calibrated limits. The arms never move from the stance.
  - A look is refused while the wheels are wanted or turning.
  - Setting off from a standstill first returns the head to the drive stance's level, centred pose.
    The plan's open point, looks while rolling, stays closed. The reason found for the level head:
    the owner's "level stance when moving" ruling. The other reason, camera bearings measured from
    the base, matters only to Rex's own drives.
- **Driving:**
  - It is offered only once the arms hold the stance. If the servos are fitted but the stance
    failed, the call has no wheels.
  - Driving goes through the dashboard joystick's path. Its caps (0.26 m/s, 60°/s) apply, with its
    smooth speed ramps (now done on the robot).
  - The joystick's holds apply: the front path check, the spin check with the arms tucked, the
    firmware's rear block and its 300 ms deadman.
  - The charger lock applies: on the charger the base refuses to move, and the reason reaches the
    caller.
  - Added for the network:
    - only the newest setpoint counts, and an older or repeated sequence number is dropped;
    - a burst after a stall applies only its last setpoint;
    - no fresh setpoint for 0.3 s gives zero at once;
    - forward and turning are refused while the latest round trip, or a ping still unanswered, is
      over 0.4 s, and until the first round trip is measured (a new link is pinged at once);
      backing away still works;
    - Stop zeroes the wheels and holds them until the caller's stick reads zero;
    - a lost link ends the joystick at once (the wheels stop).
- **The status strip:** every second, the status message carries:
  - the head's position;
  - the wheels' state and why a move was refused or held;
  - charge, open space by direction and sensor notes.
- **Ending:** the wheels are handed back, then the servos park from the stance and the lights fade.
  Each run writes `local/telepresence/last-call.json` (how it ended, which parts failed to start) for
  the supervisor.

**The supervisor trigger.**
- **Where it listens:** while `telepresence_listen` and the token are set, the wake-word supervisor
  listens one port above the call (Rex: `192.168.1.100:47801`). It uses the same allowed networks
  and the same sign-in.
- **One request per connection:** `status`, `start` or `stop`.
- **Start:**
  - It launches `python -m r3x telepresence` as its child, with no chime, only while Rex is asleep.
  - A start while Rex is awake (a conversation, Jeopardy, sentry mode, or a hand-started program) is
    refused `rex_awake`.
- **Stop:** sends the call program SIGTERM (park and exit), and only for a call the supervisor started.
- **While a call runs:**
  - The supervisor is dormant ("wake up rex" does nothing), and it answers status and stop at once.
  - Updates are fetched, not applied, until the call ends.
  - A call that exits is never relaunched.
- **Launched by the supervisor:** the call runs under the same login agent as Rex, so the camera's
  macOS permission holds. Started from a coding session, the camera failed; started this way, it
  worked (November 16).
- **Battery:** the battery menu bar now writes `local/operator/battery.json` every 10 s, and status
  reports it when under 60 s old. While a call or Rex holds the drive port, that reading ages out,
  and the call's own status carries the charge.

**Wire messages for the app** (all JSON frames on the call link unless marked):

| Direction | Message | Meaning |
| --- | --- | --- |
| robot → app | welcome `controls` | `head`: null, or `{home, fields}` (home is the level pose on the scale below). `drive`: null, or `{setpoint_hz: 10, stale_ms: 300, link_slow_ms: 400, max_lin_mps, max_turn_dps}`. Null means not offered this call. |
| app → robot | `{"type":"head", "neck":n, "pitch":n, "tilt":n, "lift":n, "visor":n}` | Any subset. neck −1 = fully to Rex's left (the picture's left) … 1 right; pitch −1 down … 1 up; tilt −1 cocked to his left … 1 right; lift 0 lowest … 1 highest; visor 0 shut … 1 fully open. Absolute targets: the newest wins, parts left out stay. |
| robot → app | `{"type":"head_state", "status", "reason", neck, pitch, tilt, lift, visor}` | After each move (`pulses_reached`, or the owner's refusal), or `status: "refused"` at once: `head_held_while_driving`, `not_a_number`, `head_not_available`. |
| app → robot | `{"type":"drive", "seq":n, "lin":x, "ang":y}` | 10 a second while steering, zeros included. seq rises by one per setpoint (a new link may start again). lin and ang are −1 … 1 fractions of the caps; positive ang turns left. |
| app → robot | `{"type":"stop"}` | Space bar: wheels to zero, held until a `drive` with lin and ang both 0. |
| robot → app | `{"type":"drive_state", active, moving, lin_mps, ang_dps, refused, held, stopped}` | Sent when the refusal, holds or moving change. refused is one of `link_not_measured`, `link_too_slow`, `setpoint_stale`, `stopped`, `charging`, `drive_telemetry_stale`, `controller_estop`, `gamepad_in_control`, `front_depth_matrix_unavailable`, or another drive-owner reason. held lists `path_blocked`, `blocked_front`, `blocked_back`, `turn_path_blocked`, `front_clearance_unknown`. |
| robot → app | `status` (each second) | Adds `head` (position, `held_for_driving`, last move), `drive` (as drive_state) and `base`: `{connected, battery_percent, charging, volts, blocked, clearance_m: {front_m, path_m, left_m, right_m, rear_m}, sensor_notes}`. Unknown values are null. |
| app → supervisor | port + 1: sign in as for a call, then one of `{"type":"status"}`, `{"type":"start"}`, `{"type":"stop"}` | Answers: `status` `{state: asleep, awake or telepresence, call_port, battery: {percent, charging, volts, seconds_ago} or null, last_call: {reason, code, failed_starts, seconds_ago} or null}`; `starting` `{call_port, already_running}`; `stopping`; `not_running`; `refused` `{reason: rex_awake, supervisor_busy, launch_failed, unknown_request}`. Then the connection closes. After `starting`, call the call port; the app's 60 s retry covers the wake. A failed start shows in `last_call`. |

`tools/telepresence_trigger.py status|start|stop ROBOT_IP:PORT` asks the supervisor from a Mac with the
token.

**Evidence:** 28 new blocked-I/O checks run the real owners: the software Maestro, the simulated drive
controller, and fake sockets and clocks. They check:
- the stance tucks and holds the arms and refuses Rex's own motion, and the wheels wait for it;
- head moves stay inside the limits and never move an arm;
- the head levels before setting off, and shutdown parks from the stance;
- out-of-order and burst setpoints are dropped;
- a stale setpoint and a lost link zero the wheels at once;
- a slow link refuses forward and turning but not reversing;
- Stop holds until the stick is released;
- an obstacle's hold and the charger's refusal reach the caller;
- the trigger starts only while asleep, stops only its own call, and never lets an outsider or a
  wrong token reach the main loop.

**On the robot (November 16):**
- The supervisor was reloaded and its trigger listened on 192.168.1.100:47801.
- The app asked status every 15 s over WireGuard, from 192.168.1.253.
- The app's first start, at 11:17:
  - the supervisor launched the call program, which ended within a second at the hardware handoff
    (`handoff_failed:SerialOwnershipProbeFailed`);
  - the cause: the radar ring was not plugged in, and lsof reported its missing port name as an
    error;
  - the same check runs at Rex's own startup, so "wake up rex" would have failed the same way;
  - the fix: an unplugged board counts as free (`hardware_ownership.serial_port_free`), its own
    provider then fails to open, and an optional board is left out of that run.
- **The next start (11:25)** woke him and signed the app in, but the app heard nothing and hung up
  ("Lost the connection to Rex").
  - The listener had opened before the stance. The app signed in during the stance (14.3 s; the
    stance ended at 16.9 s), before the call was wired to the link, so 0 bytes went out.
  - The listener now opens only once the stance holds and the call is wired. A caller before that is
    refused by the operating system and the app's 60 s retry calls again.
- **The first working call through the trigger (owner, November 16: "its working as expected"):**
  - The app started it through the supervisor over WireGuard, and Rex took the call once his stance
    held (listening at 16.6 s, the app signed in at 18.8 s).
  - Session 3df6f3a2, after about 4 minutes:
    - round trip 20.5 ms median, 54.7 ms at most;
    - 106 s of the owner's voice played;
    - 57 head moves, none refused or failed;
    - video about 5 frames a second, encoding 6 ms each;
    - the call program used 11% of one core, ffmpeg 14%.
  - The app sent no drive setpoints, so driving over the link is still unused.
  - Someone at home was surprised to see him come alive; the lights were the only cue, as decided.
    That led to the spoken line below.
- **A spoken line at home as a call starts** (owner, November 16).
  - Before the call is taken, he says "Heads up! Telepresence link established. Bret is calling in
    through me." in his own voice, at his usual level. It plays through the call's own speaker stream,
    so his mouth lights follow it; no servo moves.
  - The caller hears none of it: the listener opens only once it has played. That adds 4.4 s to the
    wake, which the app's 60 s retry covers.
  - The clip is kept in the repository at `assets/audio/telepresence/call_connected.wav`, with its
    provenance in `call_connected.json` (ElevenLabs, Rex's voice, eleven_v4_turbo, leveled, 16 kHz).
  - `tools/make_telepresence_clip.py [--text ...]` makes it again, at the cost of one ElevenLabs request.
  - If the clip is missing, the lights stay the only cue and the call goes ahead.
  - Checks: the clip plays whole at its own pace with nothing trimmed; it plays after the lights and
    before the listener opens; a missing clip is skipped. The full suite passed (1,951). Not yet
    heard on the robot.
- **No radar in telepresence** (owner, November 16: there is no point to it for a robot driven by
  hand). The radar ring only tells Rex where people are, for looking and seeking; none of the
  wheels' checks use it. A call never opens or claims its port, and the status no longer has a
  `radar` field. USB showed no ESP32-S3 at all: its old port (`usbmodem11301`, hub 1 port 3) is
  empty, and it last started in Rex's run of November 15, 20:00.

**Open:**
- On the robot:
  - driving over the link;
  - whether the look pace is quick enough (57 moves ran; no verdict on speed);
  - the battery file once the battery menu bar is reloaded.
- Not built:
  - looks while rolling;
  - telling sentry mode apart in status (it reports `awake`);
  - stage 6.

## Stages 3-5 in the Mac app — November 16

Owner request: bring the new robot features into Rex Call ([how it works](../apps/telepresence/README.md)).

- **Calling through the supervisor:**
  - The app asks for status first. Asleep: it starts the call and joins. Already on a call: it joins.
    Awake: it says so and leaves him alone. No supervisor: it calls directly.
  - The connect screen shows the supervisor's state, battery and last call, and can end a call left
    running.
  - A call the supervisor started but that never answers is reported with the last call's reason.
- **Head:**
  - Drag the picture, use the arrow keys and Q/E/R/F, a slider panel, C to centre and V for the visor.
  - Targets go out ten times a second at most, and only the parts that changed.
  - While the caller isn't moving the head, the app adopts the robot's reported position, since the
    robot levels the head itself before driving.
- **Wheels:**
  - W A S D with a speed slider, or a game controller (left stick drives, right stick aims).
  - Setpoints go out ten a second while steering, then half a second of zeros, then nothing.
  - Space or the Stop button sends the wire `stop`: the caller's own control. **There is no spoken
    stop keyword** (owner decision); nothing at home listens for one.
  - The app holds the wheels while it isn't the front window or the picture is over 0.5 s old.
  - Push-to-talk moved from Space to T, since Space is Stop.
- **Status strip:** round trip, picture age, battery, clearance ahead and behind, and the wheels'
  state with the robot's holds and refusals in words.

**Evidence, dev Mac only:**
- 10 Swift core checks, including steering, pacing and head clamps.
- Headless calls with the app's own connection code (`rexcall-probe`) against the fake robot. The
  fake robot runs the real trigger, call loop, `HeadControl` on the simulated servo board after the
  wake and stance, and `DriveControl` on a simulated base. Each call:
  - status said asleep, start woke him and the call joined;
  - both head targets were reached (neck +0.5, then −0.5);
  - the wheels moved, stopped, were held by Stop, then released, with no refusals;
  - about 100 setpoints were taken;
  - the hang-up parked him, and status then reported him asleep with `last_call: hangup`.
- Start while already on a call answered `already_running`; a stop during the wake ended the call;
  a wrong token was refused.

**Not checked:**
- the app's screens and keys by hand;
- a game controller;
- any of this on the robot.

## Risks

- **Drop-offs: not a risk here.** The base cannot sense stairs or ledges, but the owner lives on the
  first floor with none to reach (owner, November 16).
- **Echo.** If the hardware echo cancel does not hold up under talk-over, the mode is half duplex.
- **Motor and servo noise** in the room audio while the owner moves him.
- **Head response.** The Maestro's slow ramps suit Rex's character; aiming a camera by hand may need
  quicker operator speeds, within the same limits. Telepresence uses a person's look pace (speed 40,
  November 16); whether that is quick enough is for a robot run to say.
- **Charging.** The base refuses every move while on the charger (`charging`, the controller's latch
  or a full-charge voltage), so a remote drive cannot leave the charger; someone has to unplug him.
- **Manual override.** The dashboard's joystick works on the robot (owner, November 16); its manual
  servo override, which head control builds on, has no robot report yet.

## Owner decisions (November 16)

| Question | Decision |
| --- | --- |
| Link | Plain streams to start. |
| Cue at home | The lights, and (later the same day, after someone at home was startled) a spoken line as the call starts. |
| Stop keyword | None. No models at all in this mode. |
| Takeover | None. A request starts telepresence only when Rex is asleep. |
| Driving with nobody home | Allowed from the start, with no supervised runs first. |
| App | A folder in this repository. |
| Trigger | A listener in the supervisor. |
