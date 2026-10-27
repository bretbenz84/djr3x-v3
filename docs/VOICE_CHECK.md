# Voice check: CED-Tiny on brief sounds

October 26, 2026. Owner request, after listening to 60 doubtful saved clips: a brief sound that a stronger
sound model does not hear as a human voice is not sent to the reply model. **Implemented and checked on
saved robot audio. The owner turned it on the same day; it has not run on the robot yet.**

## What it does

- **What gets checked.** A trusted transcript of a capture with under 1 s of detected voice
  (`VOICE_CHECK_MAX_VOICED`) is scored by CED-Tiny. The score is the highest of AudioSet's "Speech",
  "Conversation" and "Narration, monologue".
- **Below 0.5** (`NOT_SPEECH_SCORE`), the transcript is marked unreliable. It stays in the trace and
  history, but Rex never answers it, shows no listening cue, and does not interrupt a beat for it.
  The trace records `voice_check` (score, time) and `transcript_distrusted` with reason `not_a_voice`.
- **It applies whether or not Rex is moving.** The October 3 motion rule still runs first, and a sound it
  already distrusts is not scored again.
- **Never checked:** stop and shutdown commands, collision warnings, and longer speech. A failed check
  leaves the usual trust in place.
- **The model:** CED-Tiny (Xiaomi, Apache-2.0 weights), the sherpa-onnx export `model.onnx`
  (22,256,662 bytes, SHA-256 `afe72c12…dd2a4df`), with CED's own mel front end through torchaudio.
  It is optional (`voice_check_enabled`, off by default). Rex runs without it if the model is missing.
- **Not in replay.** The identity replay (`replay.py`) does not apply this rule; it skips the motion
  rule too.

## Evidence (real models on saved audio, this M2)

The owner listened to the 60 doubtful clips among the 172 saved September 20 and 27 captures:

- 43 were his motor sounds;
- 4 were coughs or throat clears, two of them with the TV;
- 2 were Max barking;
- 1 was Rex's movement sound effect;
- 10 were the owner speaking.

The other 112 trusted lines are unlistened.

| Score under 0.5 | Motor sounds | Owner's lines |
|---|---|---|
| YAMNet (v2) | 35 of 43 | 0 of 10 |
| **CED-Tiny** | **43 of 43** | 1 of 10 (a 0.03 s tail of a shouted stop, exempt as a stop) |
| NVIDIA MarbleNet v2.0 | 20 of 43 | 0 of 10 |

- **Gap between motors and stops.** CED-Tiny's highest motor score was 0.44. The owner's real stops
  scored 0.59 and 0.77 ("Oh, stop!").
- **What Rex's rule held back** (`tools/check_voice_check.py saved --labels`):
  - every non-voice the recognizer had trusted: 3 motor sounds, Max's bark and the movement sound
    effect. In September two of those motor sounds got a reply ("All right.").
  - 3 unlistened lines, scoring 0.25–0.31: "How do you like the show?", "First, you check out." and
    "Cough. Cough. Cough."
- **What it kept.** All 10 of the owner's lines are still answered.
- **Cost.** Median 6.5–8.5 ms a capture (slowest 24 ms) on two threads, and about 44 MB of memory.

## Not chosen

- **YAMNet** scored a motor sound 0.94, above a real "Oh, stop!" (0.77). Its dog class fired on all
  7 of the owner's sampled lines.
- **MarbleNet v2.0** was fooled by the motors (median peak 0.56) and by Rex's movement sound effect (0.95).
- **Doorbell and dog alerts** are not part of this. At 0.35, CED-Tiny's dog class raised 1 true and
  3 false alarms and missed a quiet bark. Household sounds need recordings from this house, and the
  feature freeze applies.

The research comparison is private, in `local/reports/Sound models better than YAMNet.md`.

## Open

- **A robot run:** how often `not_a_voice` fires, the live check time, and whether a real short reply
  is ever held back.
- **Tuned on the same clips.** The 0.5 line was chosen on these clips, with 10 labelled lines.
- **Old audio.** All of it is September audio, from before continuous idle motion.
- **Three unchecked lines.** The three unlistened lines it holds back still need someone to listen.
- **The moving flag ends too early.** Five motor sounds came while Rex was logged as still: the flag
  ends 0.5 s after the servo controller's last pulse. Not changed here.

```sh
.venv/bin/python tools/check_voice_check.py prepare     # once: download and verify the model
.venv/bin/python tools/check_voice_check.py saved --labels local/validation/doubtful-clips-20261026/labels.json
```
