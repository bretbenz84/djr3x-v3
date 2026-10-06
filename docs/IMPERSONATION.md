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
  moment is light, he may, rarely, reply as himself and hand over to that person butting
  in about the specific thing discussed. Cameos are budgeted (ten minutes apart, the same
  voice once an hour, four per run); requests are never limited. In the hosted probe the
  model declined a cameo on the first opportunity, which is the intended default.
- **Whose voices.** Twenty-six famous clips under `assets/voices/famous`
  ([provenance](../assets/voices/README.md)); any person with a supported voice profile
  (their accepted enrollment recordings, at least six voiced seconds); and the current
  speaker, including a stranger, once their own voice track holds six clean voiced
  seconds this session. Until then "do me" gets a plain answer that Rex needs to hear
  more of them, and ordinary conversation supplies it. Nothing is recorded for impressions
  specifically.
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
| Voice cloner (`adapters/breeze.py`) | Breeze TTS 2 8-bit via mlx-audio 0.5.1, loaded by absolute path with Hugging Face access scoped off. Prepares each take completely before playback; never streams a half-rendered take, never discards audio on underruns. Loads on first use, unloads after ten idle minutes. Shares the process-wide MLX lock with Qwen3-ASR (one evaluation at a time, per generated chunk). |

Context offered to the model on human turns when the cloner started: `impersonation`
with `speaker` (ready, voiced seconds on file, basis), `people`, `famous` slugs,
`spontaneous_allowed` and `recent` performed bits. The tool is not offered during
proactive opportunities; a lull-time impression beat is possible future work.

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

## Open acceptance and limitations

- Live playback on the robot: loudness against ElevenLabs lines (both pass the same
  −20 LUFS leveler), talking motion and lights during a cloned take, the filler's feel,
  and the actual wait with the full workload resident.
- How recognizable the voices are from the speaker, and how often spontaneous cameos
  land rather than interrupt. Only the model's judgment and the budget gate cameos.
- A person's reference is rebuilt only when their accepted recordings change; it is
  deleted on forget, not when individual recordings are retired by other means.
- Breeze's generated speech is English/Mandarin only; the engine does not speak Rex's
  own lines (ElevenLabs remains his voice; there is no offline fallback).
- The research/non-commercial license of the Breeze weights applies to this home use.
