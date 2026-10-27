# Turn detection: Smart Turn v3.2 next to Silero

October 26, 2026. Owner request: add Smart Turn v3.2 beside Silero, so Rex knows when a sentence
has ended, and find out whether the 0.5 s end-of-speech silence can be shortened. **Implemented
and checked on saved robot audio. The owner turned it on the same day, with a 1.0 s longest wait;
not yet run on the robot.**

## What it does

Smart Turn (Pipecat, BSD-2-Clause) is an 8M-parameter model built on a Whisper Tiny encoder. It
listens to the last 8 seconds of someone's speech and judges from pitch and pace, not words,
whether they have finished. Rex uses the 8.7 MB int8 build, `smart-turn-v3.2-cpu.onnx` (Hugging Face
revision f766f81d3cfd, SHA-256 `2bb026316b14…cca967e4f`), in `local/models/smart-turn`.

Silero still decides what is speech. With `smart_turn_enabled = true`:

- After 0.2 s of silence, Smart Turn hears everything said so far in the capture. If it is at least
  70% sure the speaker has finished, the capture ends there.
- Otherwise Rex keeps listening. Speech resumes in the same capture, and the next pause is judged
  again on the whole thing. Or the full `end_silence_seconds` passes and the capture ends as before.
- Each pause is judged once. His own voice is never judged (those captures are discarded anyway),
  and neither is a Jeopardy answer, which keeps its 1.1 s thinking pause ("What is... Paris?").
- Nor while the wheels run: that speech waits for the drive to end anyway, and a short burst then
  gets its own quick stop check at the same 0.2 s.
- If a check fails, the full silence applies. Each check is traced as `turn_check` (probability,
  time); a capture's `ended_by` says `finished`, `silence`, `truncated` or `playback_boundary`.

With it on, `end_silence_seconds` becomes the longest wait, used only when the speaker sounds
unfinished, so it can be longer than today at little cost (below).

## Evidence (real models on saved audio, this M2)

`tools/check_turn_detection.py saved` re-cuts saved captures through Rex's own Silero endpoint
twice, with and without Smart Turn. The saved captures are the 163 September 20 and 27 robot turns
not discarded as echo (the owner, mostly commands; 94 while Rex moved). They were recorded with a
1.2 s endpoint, so their pauses are intact.

| longest wait | without Smart Turn | with Smart Turn |
|---|---|---|
| 0.5 s: ended 0.2 s after the last word | 0 of 163 | 158 (91 of 94 while moving) |
| 0.5 s: mean wait after the last word | 0.512 s | 0.233 s |
| 0.5 s: pieces | 193 | 221 (22 turns split more) |
| 1.0 s: mean wait after the last word | 1.024 s | 0.258 s |
| 1.0 s: pieces | 165 | 217 |

- **Speed:** 223 checks took a median 21 ms (slowest 31 ms) on two cores, with no camera load. The
  model adds 41 MB with speech recognition loaded, which already provides its audio features.
- **Short answers:** every short turn ("Yes.", "Shut down.", "Hey Rex.") ended at the check, in
  September and in the October 26 voice-trial clips through today's microphone (33 of 36 of the
  owner's lines; misses: "…taking your wheels away", "…my last month trip?", "Woot!").
- **What splits more**, of the 22 turns at 0.5 s:
  - 10 between two complete sentences ("Rex, let's switch topics. | What is seven plus five?").
  - 3 inside a sentence after a phrase that sounds final ("Except you're still about. | Fifteen
    feet from the door.", "Can you? | Come over here, please.").
  - 8 where a sound just before or after the words, usually Rex's own servos, became its own piece.
    The recognizer marked 6 unreliable (Rex never answers those); 2 came out as a clear "Yeah."
  - 1 short command said while Rex moved fell apart ("Keep kicking down").
- **What the model holds:** hand-labeled from transcripts of the words before each pause, it judged
  6 of 9 mid-sentence pauses unfinished ("So, |", "do you remember |", "a picture that is |").
  Today's 0.5 s splits the 3 longest of those 9 (0.67–0.77 s); a 1.0 s longest wait keeps them whole.

A split piece is not answered alone when the rest arrives before Rex is heard. His first sound comes
2–3 s after the words end, and he drops the reply he was preparing and answers both pieces as one
(owner, October 20). Each split costs one more recognition pass and a discarded model call. A
continuation under half a second of speech ("Photo.") does not stop the prepared reply, so it gets
its own answer afterwards. Voice ID sees shorter pieces, and Rex never names anyone from under 1 s.

## Not chosen

- The fp32 build (32 MB): 43 ms a check, about 1% more accurate by Pipecat's account.
- CoreML: ONNX Runtime's CoreML route split the model into 44 parts and ran slower (40 ms int8,
  66 ms fp32). A third-party CoreML conversion claims 3.5 ms on an M5 Pro, but needs `coremltools`,
  a new dependency, to save about 17 ms.

## Owner's choice and open

Owner, October 26: on, with the longest wait raised to 1.0 s (`smart_turn_enabled = true`,
`end_silence_seconds = 1.0` in the robot's local settings). The
average wait after the last word falls from 0.512 s to 0.258 s, and mid-thought pauses of up to
a second stay in one piece. The 7 of 163 turns that sounded unfinished at their end waited 1.0 s.

- A robot run: reply wait, `turn_check` time under live load (camera and speech recognition busy),
  how often speech splits (`fragments_joined`, `reply_yielded`), and stray "Yeah." turns.
- Group talk: three people splitting into more pieces was why 0.35 s was rejected (October 17). These
  recordings are the owner alone.
- `--words` transcribes each changed split for review; results stay in `local/validation/turn-detection-*`.

```sh
.venv/bin/python tools/check_turn_detection.py prepare            # once: download and verify the model
.venv/bin/python tools/check_turn_detection.py saved --silence 1.0 --words
```
