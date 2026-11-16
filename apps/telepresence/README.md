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
- **On a call:** you start muted. Use the button or M to mute and unmute, and hold Space to talk.
  Hang Up (or Esc, or quitting) tells Rex to park.
- **While he wakes:** the app keeps calling for up to 60 s, as his wake takes a few seconds.
- **Echo:** your microphone goes through macOS voice processing (echo cancelling), so speakers work.
  If the Mac cannot turn it on, the call view says to use headphones.

## Layout and checks

- `Sources/RexCallCore`: the wire format, sign-in, connection and jitter buffer, matching
  `src/r3x/telepresence/` on the robot.
- `Sources/RexCall`: the app.
- `Sources/rexcall-probe`: a headless caller that prints what it measured.

```sh
cd apps/telepresence && swift test               # 6 core checks against the Python robot's bytes
TELEPRESENCE_TOKEN=... .venv/bin/python tools/telepresence_fake_robot.py --listen 127.0.0.1:47800
TELEPRESENCE_TOKEN=... apps/telepresence/.build/debug/rexcall-probe 127.0.0.1:47800 6
```

`tools/telepresence_fake_robot.py` runs the robot's real call code with a synthetic room and camera
and no devices. You can open the app against it too.
