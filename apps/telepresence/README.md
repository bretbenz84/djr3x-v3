# Rex Call

The telepresence Mac app (stage 2 of [the plan](../../docs/TELEPRESENCE_PLAN.md)): call Rex, see
through his camera, hear the room and talk through his speaker. He must be running
`python -m r3x telepresence`.

```sh
apps/telepresence/build.sh                       # builds build/Rex Call.app (ignored by git)
open "apps/telepresence/build/Rex Call.app"
```

- **First call:** enter the robot's address (its `telepresence_listen`, such as `192.168.1.50:47800`) and paste
  the `telepresence_token` value from the robot's `local/secrets.toml`. The token is saved in this Mac's
  keychain. macOS asks once for the microphone.
- **Calling:** Call first asks Rex's supervisor (the call's port + 1).
  - Asleep: it asks the supervisor to start the call, then joins once he has woken. It keeps calling
    for up to 60 s.
  - Already on a call: it joins.
  - Awake (talking, playing or on guard): it says so and does nothing. A call never takes over.
  - No supervisor answering: it calls directly, for a call started on the robot by hand.
  - The connect screen shows what the supervisor says: asleep or not, battery, and how the last call
    ended. "End that call" stops a call left running.
- **On a call:** you start muted. M mutes and unmutes; hold T to talk. Hang Up (or Esc, or quitting)
  tells Rex to park.
- **The head**, when the robot offers it: drag the picture (right looks right, up looks up), or use
  the arrows, Q/E to cock, R/F to raise and lower, C to centre and V for the visor. The Head panel
  has a slider for each. The head stays level while the wheels move (the robot's rule).
- **The wheels**, when the robot offers them: hold W A S D. The speed slider sets the share of his
  caps.
  - Space or the Stop button halts the wheels until you let go of the keys. This is your button on
    this Mac; nothing listens for a spoken "stop" at home (the owner's decision: no stop keyword).
  - The app holds the wheels itself while it isn't the front window or the picture is over 0.5 s
    old, and the strip says why.
  - The robot's own holds show on the strip: something ahead, a slow link. On the charger A and D
    still turn him in place; W and S are held ("on the charger, turning only").
- **A game controller:** the left stick drives, the right stick aims, B stops, Y centres, X works the
  visor and A talks.
- **The keyboard button** lists every key.
- **Sound devices:** pick the microphone and the speaker before a call, or from Audio during one.
  A change takes effect at once without dropping the call. A device that disappears falls back to
  System default.
- **Echo cancelling** (macOS voice processing, on by default) lets you use speakers.
  - macOS only runs it on the system's default microphone and speaker. So it is available only
    with both pickers on System default; to use other devices with it, choose them in
    System Settings → Sound.
  - With a specific device picked, or echo cancelling off, there is no echo cancelling: use
    headphones. The call view says which devices are in use and whether echo cancelling is on.
- **If sound will not start,** the app says why (with the macOS error code) and keeps the robot from
  waiting on a dead call.

Try the sound with no robot (the microphone opens for a few seconds; nothing is sent or saved):

```sh
apps/telepresence/.build/debug/rexcall-audio-check --list
apps/telepresence/.build/debug/rexcall-audio-check [--input NAME] [--output NAME] [--no-echo-cancelling] [--silent]
```

It plays a quiet tone as "the room" (`--silent` plays silence) and reports the devices used, echo
cancelling and the microphone's peak.

The icon is an original droid face drawn from Rex's own lights: the visor, the eyes in the
telepresence magenta and his bar mouth. `icon/make_icon.py` draws it with the v3 environment's
Pillow and writes `Resources/AppIcon.icns`.

## Layout and checks

- `Sources/RexCallCore`: the wire format, sign-in, connection and jitter buffer, matching
  `src/r3x/telepresence/` on the robot.
- `Sources/RexCallAudio`: this Mac's sound: device lists, echo cancelling, the plain microphone and speaker.
- `Sources/RexCall`: the app.
- `Sources/rexcall-audio-check`: tries the call's sound on chosen devices.
- `Sources/rexcall-probe`: a headless caller that prints what it measured.

```sh
cd apps/telepresence && swift test               # 6 core checks against the Python robot's bytes
TELEPRESENCE_TOKEN=... .venv/bin/python tools/telepresence_fake_robot.py --listen 127.0.0.1:47800
TELEPRESENCE_TOKEN=... apps/telepresence/.build/debug/rexcall-probe 127.0.0.1:47800 6
```

`tools/telepresence_fake_robot.py` runs the robot's real code with no devices:
- the trigger and its `answer()`, as a pretend supervisor that starts calls;
- the call loop;
- `HeadControl` on the dashboard's simulated servo board, after the real wake and stance;
- `DriveControl` on a simulated base with a wall 2 m ahead.

Its camera picture shows where the head points and how far the base has rolled, so the app can be
tried end to end on any Mac. `rexcall-probe ... --start --steer` does that headless;
`--trigger status|start|stop` asks the supervisor.
