# Room sounds: doorbells, knocks, dogs and alarms

Status, October 26, 2026: **implemented, logging only; not yet run on the robot.** Owner request: have
Rex run CED-Tiny on the room all the time and log any doorbell, knock, dog bark or alarm, with no
reactions yet, so false alarms can be measured.

## What it does

- **Listening.** Every second, CED-Tiny (the [voice check](VOICE_CHECK.md)'s model, loaded once for
  both) scores the last 2 s of the microphone's listening channel, so every moment is heard twice. The
  looks run on their own thread; the listening loop only hands over each frame.
- **What counts.** Four kinds, each the highest of its AudioSet classes:
  - doorbell: Doorbell, Ding-dong;
  - knock: Knock;
  - dog: Dog, Bark, Yip, Howl, Bow-wow, Growling, Whimper (dog);
  - alarm: Alarm, Alarm clock, Smoke detector, Fire alarm.
- **An event.** A kind scoring 0.3 or more (`LOG_SCORE`) starts one. Later looks join it until 2 s pass
  without one; one longer than 30 s (an alarm left sounding) is logged in parts. 0.3 sits below the 0.35
  alarm V2 used for dogs, so near misses are kept and a real alarm level can be chosen from this house.
- **What is logged.** One `room_sound` record per event in the session log:
  - the kind, the time of day, how long it lasted, the highest score and its class;
  - the voice score (talk mistaken for a dog shows up here) and the music score;
  - whether Rex was making sound (his voice, a sound effect, the Jeopardy think music) or moving his
    servos or wheels.
  - **A clip:** the event plus 1 s either side, at most 12 s, in `local/room-sounds/<session>/`. A run
    keeps at most 200 clips (about 77 MB). Later events are still logged, without a clip.
- **Totals.** Every 10 minutes and at shutdown, `room_sounds_listened` records:
  - how long it has heard, and how long it missed if it fell behind;
  - the time a look took;
  - events so far by kind.
- **No reactions.** Nothing it hears reaches the conversation, the speech detector, his movement or his
  lights. If the model fails three times in a row, the listener stops for the run and Rex carries on.
- **Setting.** `room_sounds_enabled`, off by default; the owner turned it on October 26. It needs the
  voice check's model file (`tools/check_voice_check.py prepare`) but not the voice check itself.
- **Privacy.** The clips are room audio and may hold talk, wherever talk scored as one of the four
  kinds. They stay in the ignored `local/room-sounds` folder and are tied to nobody's record, so
  forgetting a person does not remove them. Deleting the folder removes them all.

## Evidence so far (real model on saved audio, this M2)

The owner's 63 labelled September clips, each followed by 6 s of real room sound from Rex's microphone
(the raw input recordings), went through the listener exactly as live frames would (9 minutes):

| What was there | Logged |
|---|---|
| Max's clear bark (B05) | dog, 0.64 |
| Max's quiet bark (A06) | nothing (never above 0.01) |
| A motor sound (A25) | dog, 0.41 (false) |
| The owner talking (C03) | dog, 0.43 (false; voice 0.58) |
| TV and coughs, then motors (D01) | dog, 0.44 (false) |
| Everything else, and the room recordings | nothing |

- **Score levels.** At 0.5 only the real bark would have been an alarm. That is one bark, so it is not
  a threshold yet.
- **Untested sounds.** No doorbell, knock or alarm occurs in any saved audio.
- **Cost.** A look took a median 5.4 ms (slowest 8.8 ms) on two threads: about 1% of one core. No
  extra model memory, and about 1.2 MB of sound is kept.
- **A test pitfall.** Perfect digital silence between the clips hid even the real bark. Rex's microphone
  never delivers it: none of the 5,622 frames in six raw recordings was silent.

## Measuring false alarms

```sh
.venv/bin/python tools/check_room_sounds.py report                     # hours heard, events by kind and score
.venv/bin/python tools/check_room_sounds.py listen local/validation/room-sounds-YYYYMMDD
.venv/bin/python tools/check_room_sounds.py report --labels local/validation/room-sounds-YYYYMMDD/labels.json
```

`listen` copies every logged clip into a new folder: numbered, with an index (time, kind, score, what
Rex was doing) and an empty `labels.json`. After the owner listens, each event is labelled `real` or
with what it was. `report --labels` then counts real and false alarms at each score from 0.3 to 0.8.

## Open

- **A robot run:** how many events an hour, of what, and how many were real.
- **Doorbells.** The house's doorbell has never been recorded. Ringing it a few times near Rex would test it.
- **Over his voice.** The listening channel is turned down about 20 dB while Rex plays, so a doorbell
  over his voice is quieter. Such events are marked.
- **First choices, untuned:** the 2 s look, the 0.3 log level and the class groups.
- **Reactions** wait for the counts and the owner's decision. Any reaction would reach the conversation
  owner as something he noticed, not a canned line.
