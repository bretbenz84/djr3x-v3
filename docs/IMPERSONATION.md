# Voice impressions

Implemented October 6, 2026 from the owner's request to bring V2's voice-cloning bits
to V3: "do an impression of Jimmy Carter", "impersonate me", and the surprise cameo when
somebody mentions a president. Everything below is software checked with synthetic
audio, the real local engine synthesizing to files, and a small paced hosted probe.
**Nothing has yet been played through the robot's speaker in a live session.**

## What Rex does

- **Requested impressions.** "Do an impression of Jimmy Carter" (or of a known person, or
  "do me"): Rex says a short line in his own voice ("Okay, clearing my vocal buffers.
  Ahem."), the V2 thinking chirps cover the render, then the parody plays in the cloned
  voice, then he takes a bow in his own voice. The model writes all three lines.
- **Spontaneous cameos.** When someone brings up a person whose voice Rex has and the
  moment is light, he may reply as himself and hand over to that person butting in about
  the specific thing discussed. Since October 23 that can be any voice he has: a famous
  one, someone he knows, or the speaker's own. His line plays first and the thinking
  chirps fill the wait for the voice (live takes needed 13-31 s to render).
- **Impressions of his own (October 23).** Owner: "If I mentioned I was going to a
  particular president's library, he would riff about it in that president's voice." The
  goal planner may plan one `impression` beat in a goal: a voice he has and the thing it
  butts in about ("Ronald Reagan butts in about Bret's trip to his library next month",
  or Bret in his own voice explaining telescope mount number four). At the beat the voice
  is rendered first, before Rex says a word, then his setup line, the voice and his
  reaction play straight through. A failed render says nothing and the goal goes on.
- **One budget for both.** Cameos and his own impression beats are budgeted together (ten
  minutes apart, the same voice once an hour, four per run); requests are never limited.
  A beat whose moment comes while the budget is spent is let go before any model call.
  In the October 6 hosted probe the model declined a cameo on the first opportunity; in
  132 live runs up to October 23 there were five requested impressions and no cameo.
- **Whose voices.** Twenty-six famous clips under `assets/voices/famous`
  ([provenance](../assets/voices/README.md)) and any person with a supported voice profile
  (their accepted enrollment recordings, at least six voiced seconds) play on the local
  engine. The current speaker's own voice ("impersonate me") is an ElevenLabs clone made
  from a short recording for it (November 19, below).
- **What is remembered.** A performed impression is noted in history as performed parody
  (subject, requested or cameo) and kept in a short per-run list so the next bit differs.
  It is never a claim about anyone and never evidence of their voice. The playback is
  echo-gated like any speech, so an impression cannot enroll anyone, including the person
  being imitated.

## Design: one owner each

| Owner | Impressions |
| --- | --- |
| Conversation model | Decides that an impression fits, picks the offered target, writes the script, intro and outro in one `impersonate` call. No separate script call, no regex parsing of who to imitate, no roster scan of utterances. |
| State owner | Validates source trust, offered targets, the cameo budget and the word cap; resolves the target to a reference clip (famous file, derived person reference, session track reference). Writes nothing persistent except the derived person reference's artifact rows. |
| Output owner | Plays intro, cloned take and outro through the same sink, speaking lights, talking motion and echo gate; waits for the take with the filler; the fixed cover line ("my impression module just blew a fuse") on a failed render. |
| Voice cloner (`adapters/breeze.py`) | Breeze TTS 2 8-bit via mlx-audio 0.5.1, loaded by absolute path with Hugging Face access scoped off. Prepares each take completely before playback; never streams a half-rendered take, never discards audio on underruns. Builds the text tokenizer at start (before the microphone opens); loads the model on first use and keeps both until shutdown (owner, October 26: no idle unload). Shares the process-wide MLX lock with Qwen3-ASR (one evaluation at a time, per generated chunk). |

Context offered to the model on human turns when the cloner started: `impersonation`
with `speaker` (ready, voiced seconds on file, basis), `people`, `famous` slugs,
`spontaneous_allowed` and `recent` performed bits. On an impression beat the same block
has no `speaker` (nobody spoke; the person in the room is a `people` entry marked `here`),
and lists people's voices only on a private (audience) beat, so a known person's voice
never carries their talk to a stranger. The planner sees `voices` (famous names, people's
voices on a private plan, whether the budget allows one now). The tool is offered on no
other opportunity.

Tool arguments: `target`, `script`, `intro`, `outro`, `requested`, `source_turn_id`.
Scripts are capped at sentence boundaries to 40 words (requested) or 28 (cameo); more
than 70 words is rejected outright.

## Setup

```sh
.venv/bin/python tools/prepare_voice_cloning.py                       # downloads the pinned snapshot
.venv/bin/python tools/prepare_voice_cloning.py --source /path/to/8bit  # or copies a local one
```

The tool writes `local/models/breeze-tts-8bit/provenance.json` with weight digests;
startup checks the pinned revision, file sizes and recorded digests without rehashing
4.6 GB. `impersonation_enabled = true` (default) composes the cloner; a missing snapshot
leaves the capability failed-but-optional in the startup report and the tool unoffered.
The robot's snapshot was copied from V2's on October 6.

## Verification

Synthetic contracts (`tools/run_checks.py`, blocked I/O, 1052 checks passing including
23 new ones in `tests/test_impersonation.py`): roster loading and link/transcript
exclusion; script capping; person references from accepted recordings with artifact rows
and removal on forget; stranger references from clean track samples, session-scoped;
readiness and offered targets; rejection of unoffered targets, untrusted sources, long
scripts and over-budget cameos; the full Session path (intro, clone, outro in one
generation; cover line and no record on a failed render; stop during preparation closes
the take; a rejected cameo falls back to an ordinary reply; own-voice impression uses
the speaker's recordings); output-owner waiting, filler gating and cloned playback; the
provider's asset checks and the settings switch.

Real model, synthesized to files on the robot's base M2 (`tools/check_voice_cloning.py`,
no playback), measured alone:

| Script | Load | First chunk | Synthesis | Audio |
| --- | --- | --- | --- | --- |
| 23 words, Jimmy Carter (cold) | 2.9–3.7 s | 1.2–2.7 s | 9.2–12.3 s | 6.2 s |
| 23 words, John Kennedy (warm) | – | 2.7 s | 11.1 s | 6.0 s |
| 51 words, Jimmy Carter (cold) | 2.9 s | 1.2 s | 26.2 s | 17.4 s |

Generation runs at about 1.5 times real time with the ported depth-decoder cache (2.9
stock); peak MLX memory 6.4 GB during a take, process RSS 5.2 GB, scratch released after
each take. The same 51-word take once timed out at 45 s while the test suite was running
beside it, which is why the caps were lowered and the timeout raised to 60 s. Live
contention from the camera, detectors and ASR is not yet measured.

Hosted probe (`tools/check_impersonation.py`, gpt-6-luna, five calls, synthetic audio,
fake cloner): "do an impression of Jimmy Carter" produced the tool with a 36-word script,
a stall intro and a bow; the Plains, Georgia mention got an ordinary reply (no cameo);
"do an impression of me" with no voice on file produced a rejected `self` call with an
empty script and then a sensible prose answer (two calls; the tool wording was tightened
afterwards and not re-probed); the dinner question was unaffected.

October 23, impressions of his own: 16 blocked-I/O checks in `tests/test_impression_beats.py`
(planner voices, beat rendering before speech, the shared budget, a failed or stopped render saying
nothing, people's voices only on private beats, spoken beats becoming impressions, the reply cameo
reminder and its chirps). Paid probe `tools/check_impression_beats.py` (real reply model and
planner, fictional Sam, fake cloner): Reagan bits in 3 of the last 6 library runs; Sam's own voice
once in round 1. Results and limits are in [implementation status](IMPLEMENTATION_STATUS.md).

## October 26: the first impression no longer freezes Rex

Run `c5ad0deb`: the first impression of the run never played. Loading the voice held the
interpreter for 0.43 s, so the microphone lost that much audio, the turn was dropped as stale
(`audio_stale`) and a servo send reported a timeout that froze every servo
([servo integration](SERVO_INTEGRATION.md)). Measured on this Mac with the real model and a
2 ms heartbeat thread: mlx-audio's load took 3.45 s with holds of 419 ms; all of it was its
post-load hook parsing the 33 MB `tokenizer.json` (262,158 tokens; `tokenizers` does not release
the interpreter). The weights themselves load with no hold over 0.1 s.

- `VoiceCloner.start()` builds that tokenizer exactly as the hook does (one 0.4 s hold), and the
  cloner now starts before the microphone. The model load attaches it instead of parsing again.
  It stays resident (about 0.4 GB).
- Measured after: start 0.9 s (402 ms hold); first impression load 0.54 s (longest hold 29 ms);
  a 3.6 s take rendered with holds under 60 ms.
- Unloading hands 4.6 GB back in one 0.10-0.13 s hold. The `gc.collect()` after it freed nothing
  (MLX memory was already released) and held another 40-50 ms; it is gone. Owner, later the
  same day: keeping the voice loaded is fine, so the ten-minute idle unload is retired. Once
  the first impression has loaded it, about 5 GB stay resident until shutdown, the only unload.

Real model on saved text and a famous clip, no speaker; startup time and memory with the
full live workload are not yet measured on the robot.

## November 19: "impersonate me" through ElevenLabs

Owner: rebuild "impersonate me" with ElevenLabs, keep it in character, have the person speak
only about 30 seconds in short repeat-after-me lines, join the recordings before uploading,
keep the voice slot until the program shuts down, and no age or consent checks (asking is
consent; no children use the robot). Celebrity impressions stay on the robot.

- **The recording.** When someone asks Rex to do them and he has no clone of them this run,
  the model's one `impersonate` call also writes 8 to 16 short `lines` (up to 10 words each)
  in character. Rex says his setup line and the first line; each time they say it back, code
  keeps that clip and Rex says the next line, with no model call. Once about 30 voiced seconds
  are in (or the lines run out with at least 10), the clips are trimmed, joined into one WAV
  and uploaded as an ElevenLabs instant voice clone; the thinking chirps cover the upload and
  synthesis, then the parody plays in their voice and Rex reacts.
- **Whose clips count.** Only their own voice: the same known person, or the same voice
  track, saying back at least 40% of the line's words. A voice the tracker did not tie to them
  counts only when it repeats the line closely (70%); a known person who is someone else never
  does. Overlapping voices get the line said again. If they say something else, nobody repeats
  the line for a minute, or someone says stop, the recording ends, nothing is kept, and Rex
  answers normally. Rex does not chime in on his own while waiting for a repeat.
- **The clone.** One per person per run: a known person's is theirs across voice tracks, a
  stranger's belongs to their voice track. Asking again plays at once, and a cameo in their own
  voice is possible once the clone exists. The clone stays in its ElevenLabs slot until shutdown,
  when every voice the run made is deleted. Forgetting a person deletes theirs at once. If the
  slots are full, the clone this run used longest ago gives way. Voices are named
  `R3X impression …`, and at startup any left by a crashed run are deleted. No other voice in
  the account is touched.
- **Data.** The clips stay in memory and are never written to disk. They are not enrollment
  evidence, and a clone is never identity evidence. ElevenLabs keeps the uploaded recording
  under its own terms.
- **Code.** `adapters/eleven_voices.py` (`VoiceClones`: create, speak, delete), the recording
  in `impersonation.py` (`VoiceSitting`) and `state.py` (`sitting_heard`), the repeats in
  `session.py` before the model call, and the `lines` field on `impersonate`. The old on-device
  "do me" path (six seconds of the speaker's voice track or profile on Breeze) is retired.
  ElevenLabs uses Rex's own speech model (`eleven_v4_turbo`) for the clone, with background
  noise removal off.

**First live run (November 19, afternoon).** The recording itself worked: Bret repeated ten
lines, each kept with no model call, and Rex moved on line by line. Three things failed:
- The upload was refused (HTTP 401): the new restricted key lacked the voices write permission.
  The trace now keeps ElevenLabs' own error status (`ElevenLabsCloneHTTP401:missing_permissions`).
- The recording was then lost, so a retry would have meant every line again. A recording whose
  upload fails is now held in memory (`VoiceClones.recordings`) until shutdown or forgetting, and
  asking again goes straight to the upload. Only a voice needing verification drops it.
- The model first sent an empty script (rejected) and then the word "Placeholder." as the bit,
  reading the script as something written later. The tool text now says the script is the bit
  played the moment the recording is done, written in full in the same call.
- After the failure Rex talked about a rejected key and payment. The failure note now gives the
  model no cause and tells it not to explain or guess why.

Checks: 16 blocked-I/O checks in `tests/test_own_voice_impression.py` (a fake clone owner
in the session tests, a fake HTTP client for the provider) and the impression tests updated.
Synthetic audio only. Not yet done: a real clone from the robot's microphone, how recognizable
it sounds, upload and synthesis time, whether people find the lines easy to repeat, and whether
a 40% match is the right bar for real speech recognition.

## Open acceptance and limitations

- Live playback on the robot: loudness against ElevenLabs lines (both pass the same
  −20 LUFS leveler), talking motion and lights during a cloned take, the filler's feel,
  and the actual wait with the full workload resident.
- How recognizable the voices are from the speaker, and how often spontaneous cameos and
  his own impression beats land rather than interrupt. Only the model's judgment and the
  budget gate them. A beat's render (13-31 s live for requests) runs while he is silent; a
  person speaking meanwhile cancels it and the beat is retried once.
- A person's reference is rebuilt only when their accepted recordings change; it is
  deleted on forget, not when individual recordings are retired by other means.
- Breeze's generated speech is English/Mandarin only; the engine does not speak Rex's
  own lines (ElevenLabs remains his voice; there is no offline fallback).
- "Impersonate me" needs ElevenLabs: without the key or network it is not offered, and there
  is no local fallback. The ElevenLabs key needs text to speech and voices write (a key without it gets 401 `missing_permissions` on upload).
- The research/non-commercial license of the Breeze weights applies to this home use.
