# Voice encoder trial: ERes2NetV2 against CAM++

October 26, 2026. Owner request: investigate replacing CAM++, Rex's voice ID model, with
ERes2NetV2, which is said to be better on short utterances but costs more CPU, and test it on
the robot. **This is an evaluation only. Rex still uses CAM++**; nothing in the live program
loads ERes2NetV2.

## The model

`3dspeaker_speech_eres2netv2_sv_zh-cn_16k-common.onnx` (71,441,526 bytes) from the same
k2-fsa/sherpa-onnx release as Rex's CAM++, pinned to SHA-256 `bf1a75b9…ee2239` as listed by
the Hugging Face mirror (`csukuangfj/speaker-embedding-models`), whose CAM++ digest matches
Rex's pin. Same input and output as CAM++ (80-band features with the mean removed, a 192-number
voice print), so it runs through Rex's own adapter with only its file, digest and namespace
changed. Caveat: it was trained on Chinese speakers only; Rex's CAM++ on Chinese and English.

## The test

`tools/check_voice_encoders.py` (no paid calls):

```sh
.venv/bin/python tools/check_voice_encoders.py prepare   # once: downloads and verifies the model
.venv/bin/python tools/check_voice_encoders.py live      # about 15 minutes
```

- Rex must not be running. The tool takes the microphone from the wake supervisor the way
  `r3x live` does (no serial port is opened, nothing moves) and gives it back when it ends.
  While it runs, "wake up Rex" is not heard and the menu-bar apps step aside.
- Rex's microphone settings, Silero VAD, 0.5 s endpoint and speech recognition are used. The camera's
  face detection runs in the background, as in a conversation, unless `--no-camera`.
- Part 1 makes a voice print with each encoder from the same six read sentences (each needs
  2 s of speech, Rex's own enrollment minimum). Parts 2 and 3: sixteen short lines, eight
  ordinary sentences and six in his own words. Part 4 (optional): a second person makes a
  print from three sentences and says the short lines and four sentences, which measures
  wrong-person naming. `--name` sets the first speaker's name (default Bret).
- Each line runs through both encoders (order alternating) with Rex's 2 s windows, then
  recognition. Logged per line: time, CPU time, how many Mac cores were busy, windows,
  scores. Once a second: whole-Mac and process CPU. Also memory and load time per model.
- Results compare each line with its speaker's print, the other person's, and 25 famous
  reference voices (recorded elsewhere, so easier to tell apart). "Named" applies Rex's own
  naming rule with today's thresholds, which were set for CAM++; ERes2NetV2 would need
  its own if adopted.
- Everything stays in `local/validation/voice-encoders-*/` (clips, `log.jsonl`,
  `summary.json`); Rex's store is never written. `saved DIR` re-scores a run offline.

## Evidence so far (offline, this M2, real models)

The owner's 20 saved voice clips (read-only copies in `local/validation/voice-encoders-stored-1ede5740`),
six as the print, plus the first 0.6 s and 1.2 s of each other clip. No microphone or camera load.

| | CAM++ | ERes2NetV2 |
|---|---|---|
| time per line, median / 1 in 10 / slowest | 0.014 / 0.062 / 0.163 s | 0.068 / 0.313 / 0.827 s |
| 2–4 s of speech, median | 0.059 s | 0.305 s |
| CPU time per line, median | 0.038 s | 0.138 s |
| memory to load and first use | +108 MB | +174 MB |
| short pieces (under 1 s) closer to the owner than any famous voice | 12 of 16 | 16 of 16 |
| 1–2 s lines named with today's rule | 9 of 14 | 11 of 14 |

- CAM++'s offline times match its live record (25 recent sessions: median 0.015 s, 1 in 10
  0.064 s, slowest 0.173 s). Voice analysis runs before transcription, so ERes2NetV2's extra
  (median +0.054 s, 1 in 10 +0.25 s, worst +0.67 s on long lines) adds directly to the reply wait.
- Rex never names a speaker from under 1 s of speech (`min_named_seconds`), with either encoder.
- A stand-in microphone fed saved clips through the whole `live` flow, including retries and
  the second person; its accuracy figures are meaningless (the "guest" was several famous voices).
  The camera load path and the real microphone were not exercised.

## Live run on the robot (October 26, run `4bf966b2`)

The owner alone through the ReSpeaker, camera face detection running, 36 of his lines; for the second
person a YouTube video played through a speaker gave one 9.7 s line before the rest was skipped, so
that voice served only as a same-room rival print.

| | CAM++ | ERes2NetV2 |
|---|---|---|
| time per line, median / 1 in 10 / slowest | 0.010 / 0.084 / 0.169 s | 0.041 / 0.419 / 0.851 s |
| ordinary sentence (2–4 s of speech), median | 0.064 s | 0.319 s |
| CPU time per line, median (camera's share removed) | 0.028 s | 0.083 s |
| memory to load and first use | +103 MB | +166 MB |
| his lines of 1 s or more, named correctly | 9 of 9 | 9 of 9 |
| his lines under 1 s closer to him than to every other print | 14 of 18 | 17 of 18 |
| same, as AUC | 0.98 | 0.99 |
| YouTube pieces of 1 s or more scoring .50 or more against his print | 0 of 17 (highest .46) | 3 of 17 (highest .52) |

- 21 of the 30 test lines were under 1 s; three ("What", "I don't know", "Woot!") had too little
  speech for either model, and none was named by either, because Rex never names from under 1 s.
- ERes2NetV2 scores every voice higher, other people included, so Rex's thresholds (set for CAM++)
  would name a stranger as the owner; with a threshold high enough to prevent that, how many of his
  short lines it would accept is not settled by this run (it depends on which rivals set the bar).
- The whole Mac was 18% busy while listening (30% at most); either model uses two cores while it runs.
- Conclusion: no naming gain under today's rules, an uncertain short-line gain, and about a quarter
  second more wait on every ordinary sentence. Recommendation (not yet the owner's ruling): keep CAM++.

## Open

- A live run with a real second person, if the trial is picked up again (`live` again; `saved` re-scores).
- Real short utterances: the pieces above are cut from clean, longer lines.
- If adopted: thresholds calibrated for ERes2NetV2, re-embedding saved voices from their original
  audio under the new namespace, and the cost of long lines (fewer windows is one option).
