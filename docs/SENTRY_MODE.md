# Sentry mode

Owner request and rulings, October 22, 2026.

- **First robot run** (session `a45983c1`): worked as designed except the alarm talk, which spoke as
  ordinary Rex. Repaired the same night.
- **Second run** (`dc9f7480`): the alarm talk stayed on duty, but Bret, who had armed him and stayed,
  was ignored by face and voice after the trip, and could not shut him down. Repaired too.

Neither repair has run on the robot yet.

Rex guards a room while he is left alone. He holds still with red lights and a slowly sweeping
neck, and costs nothing while nothing happens. A stranger's face, or a full sentence from a voice he
doesn't know, sets off an intruder alarm: he roasts them, records them, and sends your phone an
alert with a photo. Someone he knows by face or voice stands him down.

## Turning it on

Ask in your own words. The reply model reads the request and calls the `sentry_mode` tool; code
checks the numbers and works out the clock.

| You say | Countdown | Ends | Alarm talk |
| --- | --- | --- | --- |
| "Activate sentry mode." | 30 s | when someone he knows comes back | on |
| "Activate sentry mode until 7pm." | 30 s | 7 PM (tomorrow's if 7 PM has passed) | on |
| "Activate sentry mode for 1 hour." | 30 s | an hour after the countdown | on |
| "Activate sentry mode with no audio alerts." | 30 s | when someone he knows comes back | off |
| "Activate sentry mode silent with 60 second activation good for 9 hours." | 60 s | 9 hours after the countdown | off |

A question about sentry mode ("what would sentry mode look like?") is answered, not obeyed.

**Countdown.** He first states the settings from the checked plan ("Sentry mode until 7 PM.",
"Sentry mode for 9 hours, silent."), then "Activating sentry mode in thirty, twenty-nine, …, one",
then "Sentry mode active." For 31 seconds or more he calls the starting number, every ten, and each
of the last ten. The numbers and the other fixed lines are synthesized once in his voice at startup
and kept on disk, so later countdowns cost nothing.

During the countdown his head surveys the room, stop by stop. An unrecognized face that does not
move at all for 2.5 seconds is taken to be a picture, and is ignored for the rest of the guard.

**Calling it off.** Say "stop", "cancel", "abort", "wait" or "never mind" during the countdown. He
says "Sentry mode cancelled." and goes back to normal. Recognition hears his own numbers too, so
during the count only those words (and an exact "shut down") count. Nothing else is answered.

## On guard

- **Lights:** eyes, mouth and chest red; the mouth glows steadily between lines, and any line he
  speaks stays red.
- **Arms:** the throttle arm rests, the poker arm lowers, and every arm is held still. A held-out
  throttle arm strains its servo for hours, so it swings out only at an intruder.
- **Head:** level and straight. The neck sweeps between five stops (30 and 15 degrees either side,
  and centre), holding each for 15 seconds and taking about 3 seconds to move. A voice in front can
  still turn his head; the sweep then waits 20 seconds.
- **Wheels:** locked. Every drive plan, his own or one asked for, is refused. So is the back-off
  reflex, which would otherwise back him away from someone stepping close.
- **Quiet and free:** no idle movement, no listening head-cock, no goals or lull talk, no planner,
  no paid news searches. Faces, voices and speech are judged on the robot. The
  only hosted calls are the ones an intrusion or a stand-down needs.

## What sets off the alarm

- **A face held in view for about 3 seconds.** He turns to face it, and the recording starts. If it
  isn't recognized within 3 more seconds (7 more when it looks a little like someone he knows),
  the alarm goes off. Faces must be at least 24 pixels wide; a face that flickers in and out for
  under 3 seconds never trips it.
- **A full sentence:** at least four words that speech recognition marks reliable. Barks, clatter,
  his own motors (usually heard as "Oh.") and short words never trip it.

Whoever was around while he armed (the person who asked, anyone recognized during the countdown)
does not count as arriving while they leave. Once the room has been empty for 30 seconds, anyone
he knows who shows up stands him down.

That exemption ends the moment the alarm goes off: from then on, anyone he knows in view or clearly
heard stands him down, including whoever armed him. On the second run, Bret armed him and stayed;
after the trip his recognized face and matched voice were ignored for two minutes, and Rex called
him a ruse.

## When the alarm goes off

With alarm talk on, he plays a recorded "Intruder alert! Intruder alert!", his eyes and mouth flash
and the throttle arm swings out at them. Then the conversation model speaks as Rex on guard duty.

- **When he can see them,** he roasts what they look like: their clothes, hair or what they carry,
  never their skin, size, age or health. The picture is cropped to the stranger, head to waist.
- **When he only heard a voice,** he calls out to the hidden intruder to show themselves and mocks
  what they said.
- **Either way,** he tells them they are being recorded and the owner has been alerted. He answers
  anything they say, in character, and adds another jab about every 30 seconds when nobody talks
  back. After 2 minutes he goes quiet, lowers the arm and keeps watching and recording.

These lines are written from a short guard-duty brief and this intrusion's own exchange only: what
he and they said since the alarm. None of ordinary Rex's context goes with them: not the earlier
conversation, saved memories, room memory, joke material or the view of the room. The brief forbids
mentioning the room, the earlier conversation or anyone he knows by name.

On the first robot run they were sent with all of that, and the alarm roasted "a telescope in the
room and not a face in sight" and a closet instead of the intruder.

Nothing an intruder says changes anything. A name, "I know Bret", "stand down" or "shut down"
from an unknown voice is ignored or teased. Only a recognized face or voice ends sentry mode.

An exact "shut down" always works when it comes from a voice he clearly matches, or while someone he
knows is in view, even before a trip. On the second run, Bret said "Shut down." in view and
recognized, but his voice didn't match clearly, and Rex stayed on until his time ran out. Typing
`quit` in his terminal, Ctrl-C or the dashboard's Shut Down button also end the program.

In silent mode none of this is said: the arm still swings out, and the recording and the alert
still happen.

He is back on guard once nobody has been seen or heard for a minute. A later visitor sets off a
new alarm, with a new alert.

## Standing down

A face he recognizes, of someone with a saved name, stands him down at any point: "Oh Bret, thank
god it's you." So does a clear match on a saved voice; the reply then also answers what they said.
His lights, arms and wheels go back to normal, and so does his ordinary life. Before the alarm cry
and before every alarm line, he checks who is in view. On the first run he recognized Bret 0.6
seconds before the roast and still said "Oh, Brett—it's you. Stand-down…" in alarm mode.

If the alarm went off less than 10 seconds earlier, the phone alert is never sent, so walking in
too far away to be recognized costs no false alarm on your phone. If the alert already went out, a
second one says who stood him down. The alert has its own timer, so his talking never delays it.
On the first run it waited behind the first alarm line and went out after 22 seconds.

## When the time runs out

He shuts down, the same as "shut down": the servos park, the lights fade and he waits for "wake up
rex". If an alarm is still running at the end time, he first waits up to 10 minutes for the
intruders to leave, so the recording finishes.

## Recordings

Only an arriving face starts a recording (owner ruling). A sentence alone sets off the alarm and
the alert, but records nothing until a face arrives.

While he guards, the last 10 seconds of camera frames (5 a second, 960 pixels wide) and microphone
audio wait in memory. When a face arrives, they become the start of a recording that runs until
the intruders leave, in 30-minute parts for a longer visit. Each recording is a folder in
`local/sentry/recordings/`, named for its date and time:

- `video.mov`: the frames with the microphone's audio.
- `photo.jpg`: the frame the alert was sent with.
- `event.json`: when it happened, why, what intruders said, and who stood him down.

Recordings are kept indefinitely (owner ruling) and never uploaded. Nothing new is recorded when
less than 2 GB of disk is free. If ffmpeg can't join a recording, its frames and `audio.wav` stay
in the folder.

## Phone alerts (ntfy)

Alerts go to the free **ntfy** app on your phone. One-time setup:

1. Install **ntfy** from the App Store, open it and allow notifications.
2. On the robot, make Rex's private topic name. It is printed and saved in `local/secrets.toml`
   (`ntfy_topic`):

   ```bash
   .venv/bin/python -m r3x push-setup
   ```

3. In the ntfy app, tap **+**, type the printed topic name exactly, keep the server as `ntfy.sh` and
   subscribe.
4. Send a test. Your phone should show "Rex: test alert":

   ```bash
   .venv/bin/python -m r3x push-test
   ```

5. Restart Rex; the startup trace's `sentry` row then says `push_alerts: true`.

An alert reads like "Rex: sentry mode triggered. 3:14 PM: someone Rex does not recognize is in
view. Recording.", with the photo attached. For a voice it includes the first thing they said.

**Privacy:** ntfy.sh is a public service, so anyone who knows the topic name can read the alerts.
That is why the name is long and random and lives only in the ignored secrets file. Photos stay on
ntfy's server for a few hours. Without a topic, sentry mode still works; alerts are only logged.

## Costs

Guarding costs nothing: no hosted request is made. Per intrusion with alarm talk, there are about 4
to 8 reply-model lines with a picture each, at roughly a tenth of a cent each in Fast mode with the
cached prompt, plus about 100 to 600 ElevenLabs characters. A stand-down greeting is one reply-model
line. The countdown's fixed lines are synthesized once, about 400 characters in total. The
settings line costs about 40 characters each time.

## Evidence

- **Contract checks** (`tests/test_sentry.py`, 40 tests, all synthetic: fake camera rows, voices,
  clocks, serial ports, HTTP and recorder) cover:
  - request parsing and rejection, the spoken settings and the countdown wording;
  - arrival and trip timing, brief faces and moving views, tiny faces;
  - pictures found in the survey and recognized again from another neck angle;
  - known faces and voices, and the person leaving;
  - sentences against noise, the two-minute talk, jabs, watching and the all-clear;
  - silent mode and the time running out;
  - the red lights and flash on a fake LED board, the stance and sweep on a simulated Maestro;
  - refused drive plans and reflex on a simulated base;
  - the recorder's pre-roll and kept files, and push retries and refusals;
  - the full guard through the production session:
    - no model call while guarding; the roast with no tools;
    - no alert for a false alarm; an intruder's "shut down" ignored; a known voice's obeyed;
    - alarm lines carrying only their brief and this intrusion's exchange;
    - standing down, with no cry, for someone recognized after the trip;
    - the picture cropped to the stranger;
    - the second run replayed: whoever armed him and stayed stands him down after a trip, by face
      or voice, and "Shut down." in view works. These three checks fail on the code that ran.
- **Full suite:** 1,474 blocked-I/O checks pass (5 skipped as before).
- **Video join:** ffmpeg on this Mac turned 60 synthetic 1920×1080 frames and a 440 Hz tone into
  a 12-second `video.mov` (Motion-JPEG and AAC).
- **Paid probe** (`tools/check_sentry.py --live`, real reply model, fictional input): 7 of 7
  requests correct. The six activation phrases above, and "10 second countdown, until midnight",
  got the right countdown, end and alarm setting; the question was answered without the tool. The
  alarm line and the stand-down greeting came out in character with no tool ("Oh, Bret—thank
  goodness it's you."). Without a picture the first alarm line invented an outfit; the request now
  says when no picture came, and the recheck roasted their nerve instead. About 20 requests, roughly
  8 cents.
- **First robot run** (session `a45983c1`, October 22):
  - Two activations went as designed: "for 10 minutes with a 60-second countdown" and "ten-second
    countdown, five-minute duration". The countdown was called correctly; tens, then each second.
  - Both trips were speech. The first was a device voice ("I'm sorry, but you don't have enough
    credits available."), with a TV seen on his left earlier. The second was Bret's own voice,
    which did not match his saved voice clearly enough.
  - The cry played, and the phone alert arrived with a photo, plus a stood-down follow-up. The
    all-clear came a minute after the room went quiet. Bret's face stood him down both times:
    "Oh, Brett, thank the stars it's you."
  - Wrong: the alarm lines were about the telescope and a closet, and once greeted Bret in alarm
    mode; the alert came 22 s after the trip. Repaired as described above.
- **Repair probe** (real reply model): three alarm lines for a voice nobody could see, with a
  telescope conversation in his history. All three stayed on guard duty ("I hear you, sneak.
  You're being recorded, and the owner's been alerted. Show yourself…"). No telescope.
- **Second robot run** (session `dc9f7480`, October 22):
  - Bret armed him for 5 minutes with a 10-second countdown and stayed in the room.
  - "I think we gotta check out the fire alarm." (no clear voice match) tripped it.
  - The alarm talk stayed on duty ("Show yourself, you suspicious little fire-safety enthusiast!").
  - The alert arrived with a photo, and a recording was made and joined (139 s, 575 frames).
  - Wrong: Bret was recognized in nearly every frame and matched by voice on "It's okay, Rex. It's
    me." four times, all ignored as "leaving". "Shut down." was ignored too. He shut down only when
    the 5 minutes ran out.

## Not yet checked on the robot

- The red mouth and chest on the real boards. The chest gets no levels; whether its firmware holds
  red in speaking mode for hours is unknown.
- Stance, sweep, aim and throttle-arm moves on the servos, and how noisy the sweep is to his own
  microphone.
- Face trip timing and recognition distance with the real camera, and whether real pictures are
  caught by the survey.
- The repaired alarm talk, and the cropped picture of a real stranger.
- Whether a dog or household noise ever reaches four trusted words. A device voice did, on the
  first run.
- Hearing "stop" over his own countdown.
- Recording CPU and disk use on the M2 over hours, and playback of `video.mov` in QuickTime. Both
  first-run trips were speech, so nothing was recorded.
- The time-up shutdown and wake.

## Code

- `src/r3x/sentry.py`: the plan, the guard's state and its rules (the state owner's part), and the
  words the model is given.
- `src/r3x/sentry_guard.py`: runs it: countdown, body, alarm, stand-down, alerts and the end.
- `src/r3x/sentry_recording.py` and `src/r3x/push.py`: recordings and ntfy alerts.
- Hooks:
  - `semantics.py` (tool), `state.py` (check), `session.py` (alarm context, no tools, plan hand-off)
    and `runtime.py` (hearing routed to the guard, no idle motion, audio to the recorder).
  - `leds.guard`, the servos' `sentry` moves, `drive.hold`, the observations pause, and
    `speakers.SpeakerTracker.match` (a read-only voice match).
