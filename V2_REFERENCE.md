# V2 reference and extraction map

This is a map to preserve implementation knowledge, not a dependency manifest or
an instruction to copy all of v2. Start with [DESIGN.md](DESIGN.md) and the
[initial test program](docs/INITIAL_TEST_PROGRAM.md). Extract a small integration
only when its implementation slice needs it.

## Reference snapshot

Inspected during the September 9–10, 2026 design session:

- Local source: `/Users/bbenziger/djr3x-v2`.
- V2 HEAD at final verification: `9806c74fd233a7118da88ca5ae55fb7450deec05`
  (`Update identity references and local TTS behavior`).
- Working tree: **clean** at final verification. Recent identity, onboarding,
  nickname, impersonation, and speech-cutoff fixes became committed during this
  documentation session. Use this revision, not its predecessor `53d3c86`, as the
  reference for those fixes. Remote synchronization was not checked.
- V3 started at `3779af19277c35c7dfc66773ef39e0d63bf3b436`, with a placeholder README
  and Python `.gitignore`. This task adds design documents, not a working runtime.

The links below point to local files for convenient inspection. They can change
after this snapshot; check the source and diff again before extraction. Record the
source file digest/revision when code is actually moved. No v2 tests or live devices
were run to validate v3 as part of this documentation task.

Useful orientation: [README](/Users/bbenziger/djr3x-v2/README.md),
[CONTEXT](/Users/bbenziger/djr3x-v2/CONTEXT.md),
[CLAUDE](/Users/bbenziger/djr3x-v2/CLAUDE.md), and
[Lean restructuring plan](/Users/bbenziger/djr3x-v2/docs/lean_brain_restructuring_plan.md).
These contain historical sections. Distinguish a previous plan from current code
and observed behavior; neither an old note nor a passing unit test proves room performance.

## First-program sources

| Capability | Start with | Preserve or investigate | Boundary for v3 |
| --- | --- | --- | --- |
| Microphone and VAD | [audio/stream.py](/Users/bbenziger/djr3x-v2/audio/stream.py), [audio/vad.py](/Users/bbenziger/djr3x-v2/audio/vad.py) | PCM capture, voiced intervals, device handling, endpoint timing | Explicit lifecycle and timestamps; no v2 conversation globals |
| Batch ASR | [audio/transcription.py](/Users/bbenziger/djr3x-v2/audio/transcription.py), especially `_qwen_transcribe` | Qwen3-ASR loading/preprocessing, transcript quality, original audio correlation | One selected backend; report failures instead of silently changing provider or quality semantics |
| CAM++ encoder | [audio/campplus.py](/Users/bbenziger/djr3x-v2/audio/campplus.py), `Encoder`; [CAM++ notes](/Users/bbenziger/djr3x-v2/docs/campplus_voice_id.md) | Exact model, preprocessing, normalized embedding, model namespace | Extract the encoder adapter; do not import all legacy recognition backends |
| Recognition evidence | [audio/speaker_id.py](/Users/bbenziger/djr3x-v2/audio/speaker_id.py), `rank_embedding`, `window_evidence` | Scores against distinct people, margins, sample quality, bounded window analysis | Identification and speaker-change detection require separate calibration |
| Conversational learning | [intelligence/voice_learning.py](/Users/bbenziger/djr3x-v2/intelligence/voice_learning.py), `Learner`, `Policy`, `Sample`; [learning notes](/Users/bbenziger/djr3x-v2/docs/conversational_voice_learning.md) | Provisional anchor, repeated compatible samples, expiry/correction, original recordings | Reuse the evidence contract; integrate into v3's single tracker/state owner |
| Voice archive | [memory/voice_recordings.py](/Users/bbenziger/djr3x-v2/memory/voice_recordings.py), `save_batch`; [recording utility](/Users/bbenziger/djr3x-v2/tools/voice_recordings.py) | Atomic WAV/embedding writes, deduplication, metadata, limits, previewed re-embedding | V3-owned store and independent imports; preserve namespace and deletion behavior |
| Face presence | [vision/face.py](/Users/bbenziger/djr3x-v2/vision/face.py), [vision/face_presence.py](/Users/bbenziger/djr3x-v2/vision/face_presence.py) | Face track continuity, identity alternatives, interval-bound presence | Small faces remain uncertain; no mouth-derived active-speaker evidence |
| Main reply and tools | [intelligence/lean_brain.py](/Users/bbenziger/djr3x-v2/intelligence/lean_brain.py), `stream_reply`; [tool_router.py](/Users/bbenziger/djr3x-v2/intelligence/tool_router.py) | Prompt size, streaming reply path, useful tool schemas | Tools return correlated results; no independent spoken responses or old handler ladder |
| Rex's character | [intelligence/personality.py](/Users/bbenziger/djr3x-v2/intelligence/personality.py) | Review persona guidance and representative dialogue | A compact prompt; do not transplant serial mood/comedy/governor machinery |
| Online TTS | [audio/tts.py](/Users/bbenziger/djr3x-v2/audio/tts.py) | ElevenLabs request shape, settings, cache identity, streaming, drain/cancel behavior | A provider adapter plus the single output owner; no imported servo/LED orchestration |
| Delivery and self-audio | [audio/speech_queue.py](/Users/bbenziger/djr3x-v2/audio/speech_queue.py), [audio/echo_cancel.py](/Users/bbenziger/djr3x-v2/audio/echo_cancel.py) | Queue ordering, cancellation, output timing, echo rejection | Actual delivery events must reach session state; generated text is not proof of playback |
| Names and minimal memory | [memory/people.py](/Users/bbenziger/djr3x-v2/memory/people.py), [memory/facts.py](/Users/bbenziger/djr3x-v2/memory/facts.py), [memory/conversations.py](/Users/bbenziger/djr3x-v2/memory/conversations.py) | Stable IDs, unique canonical/alias/nickname resolution, persistence examples | New attributed-claim schema; language meaning stays with the model |
| Isolated checks and replay | [tools/production_replay.py](/Users/bbenziger/djr3x-v2/tools/production_replay.py), [tools/run_lean_checks.py](/Users/bbenziger/djr3x-v2/tools/run_lean_checks.py) | Temporary stores, blocked external I/O, production-path replay | Build against the new core; do not import v2's monolith to reuse its harness |

### Model and protocol details to retain

The following are **v2 source defaults or implementation details**, not a report
of the effective live configuration and not a mandatory v3 model list. Local
overrides, environment, credentials, availability, and future API changes must be
checked during implementation.

- ASR defaults to Qwen3-ASR. The existing Qwen path preserves token log-probability
  information through its decoding integration; replacing it with a convenient
  wrapper must not silently discard the quality signal used by the turn pipeline.
- CAM++ uses the `speech_campplus_sv_zh_en_16k-common_advanced` ONNX export, 192-dimensional
  unit embeddings, 16 kHz input, and 80-bin Kaldi filterbanks with mean normalization.
  The adapter uses CPU ONNX inference and a Torch/torchaudio feature frontend; this
  dependency and CPU cost still need to be accounted for. The model's minimum input
  length is not a promise of accurate identity on a brief "yes."
- CAM++ recognition defaults include cosine `0.50` and margin `0.07`. Enrollment
  starts with three qualifying turns, at least two voiced seconds each and eight
  total, plus anchor/cluster agreement. These are baselines to evaluate, not calibrated
  probabilities or universally appropriate diarization thresholds.
- V2's reply defaults distinguish `LLM_MODEL` from `LLM_CONVERSATION_MODEL`; the latter
  is `gpt-5.4-mini` in the inspected source. Do not infer the actual conversation
  backend solely from the generic setting, or copy every model switch into v3.
- ElevenLabs defaults to `eleven_v3_conversational` in the inspected source. Review
  `_pin_v3_stability`, `_v3_seed`, `_stitch_previous_text`, `_cache_path`, and
  `_fetch_from_api`. Preserve the chosen stability/tag policy and cache identity
  across model, voice, text, seed, and effective settings. A requested seed does not
  establish bit-for-bit provider reproducibility.
- The current TTS code omits `previous_text` for both v3 model variants. Its recorded
  live `unsupported_model` rejection concerns `eleven_v3`; compatibility with the
  conversational variant was not independently validated. Do not turn the broader
  omission policy into a claim that both APIs were tested. Older nearby comments
  about stitching and whole-reply behavior do not supersede current request code.

Model provenance helper: [tools/download_campplus.py](/Users/bbenziger/djr3x-v2/tools/download_campplus.py).
Keep the checkpoint digest and preprocessing namespace with any imported embeddings.
Do not copy credentials or assume a machine-specific voice ID belongs in tracked docs.

### Existing seams that must not become v3 architecture

- [intelligence/interaction.py](/Users/bbenziger/djr3x-v2/intelligence/interaction.py)
  contains the live orchestration and useful failure evidence, but also many competing
  paths. Its anonymous-slot helper handles unnamed voices separately from recognized
  people. That is not the proposed unified named/unnamed tracker.
- [intelligence/voice_learning_runtime.py](/Users/bbenziger/djr3x-v2/intelligence/voice_learning_runtime.py)
  shows where the learner is invoked and how original capture evidence is carried.
  It depends on the old interaction state; copy the contract, not the global bridge.
- [intelligence/attribution.py](/Users/bbenziger/djr3x-v2/intelligence/attribution.py)
  still contains mouth-derived attribution decisions despite conversational learning's
  mouth-free intent. V3 excludes those inputs from every identity and learning path.
  This observation does not establish mouth tracking as the cause of every v2 incident.
- [intelligence/onboarding.py](/Users/bbenziger/djr3x-v2/intelligence/onboarding.py)
  and [memory/name_validation.py](/Users/bbenziger/djr3x-v2/memory/name_validation.py)
  contain recent defensive fixes for literal names and misfiled answers. Preserve
  their regression scenarios; do not rebuild a growing regex-based semantic interpreter.
- [memory/retrieval.py](/Users/bbenziger/djr3x-v2/memory/retrieval.py) is a reference
  for later retrieval, not an initial dependency. Measure synchronous embedding work
  before putting it on the response path.

## Later integrations worth preserving

| Capability | Sources | Constraints or lessons |
| --- | --- | --- |
| ReSpeaker direction | [hardware/flex_doa.py](/Users/bbenziger/djr3x-v2/hardware/flex_doa.py), [tools/flex_doa.py](/Users/bbenziger/djr3x-v2/tools/flex_doa.py), [tools/flex_ctl.py](/Users/bbenziger/djr3x-v2/tools/flex_ctl.py) | Timestamped capture evidence; direction is supporting evidence, not identity. Missing data stays missing. Camera left/center/right is coarse context |
| Enrollment mic hold | [hardware/servos.py](/Users/bbenziger/djr3x-v2/hardware/servos.py), `hold_voice_enrollment_mic`, `release_voice_enrollment_hold` | Midpoint of configured safe range, settling, bounded lease, release/invalidate on movement and cancellation; required before enrollment on the physical robot |
| Local TTS | [audio/local_tts.py](/Users/bbenziger/djr3x-v2/audio/local_tts.py), [local TTS notes](/Users/bbenziger/djr3x-v2/docs/local_tts_backends.md) | Breeze 8-bit is the chosen voice; preserve its verified reference pair and API contract. Offline mode should be a deliberate capability profile |
| Local scheduling | [utils/mlx_lock.py](/Users/bbenziger/djr3x-v2/utils/mlx_lock.py), [utils/local_work.py](/Users/bbenziger/djr3x-v2/utils/local_work.py), [audio/breeze_fast_depth.py](/Users/bbenziger/djr3x-v2/audio/breeze_fast_depth.py), [audio/breeze_ref_cache.py](/Users/bbenziger/djr3x-v2/audio/breeze_ref_cache.py) | Coordinate ASR/TTS and optional local work; do not assume Python threads make MLX workloads independent |
| Impersonations | [features/impersonation.py](/Users/bbenziger/djr3x-v2/features/impersonation.py), [impersonation plan](/Users/bbenziger/djr3x-v2/docs/local_tts_impersonation_plan.md) | Reuse eligible natural recordings, match transcript to reference audio, keep useful partial captures, resolve nicknames, and use supported biographical material |
| Single-instance handling | [utils/single_instance.py](/Users/bbenziger/djr3x-v2/utils/single_instance.py) | Useful lifecycle example; avoid two processes competing for the same microphone or database |

Breeze currently uses `mlx-community/Breeze-TTS-2-mlx-8bit` with the v2 integration
pinned to `mlx-audio[tts]==0.5.1`. That version records a known integration, not a
claim that it is the latest library. The user's 8-bit choice should not be replaced
by 4-bit or mixed precision without a new decision.

Current ordinary Breeze speech uses 1.5 seconds of preroll; the impersonation path
prepares a complete take. A previous rule deliberately aborted impressions after
three playback underruns and was removed in the latest working tree. **Do not restore
that rule from an older note.** An underrun reports starvation; it does not mean all
remaining generated audio should be thrown away. Validate complete content and live
playback separately, with the camera and local model workload present.

[tools/check_breeze_speech.py](/Users/bbenziger/djr3x-v2/tools/check_breeze_speech.py)
helps check generated speech content. Passing that check is not proof of uninterrupted
speaker playback. Original user recordings and voice-reference assets remain private;
this source map does not import them.

## Regression cases to bring forward

The [initial test plan](docs/INITIAL_TEST_PROGRAM.md#conversation-and-memory-scenarios) translates
these into v3 acceptance scenarios. Logs describe what text the system displayed or
heard; without the corresponding waveforms they cannot prove segmentation or playback.

| Field evidence | Lesson to preserve |
| --- | --- |
| [16:45 conversation](/Users/bbenziger/djr3x-v2/logs/conversation-2026-09-09-16-45-24.log) | Rex greeted Bret by name, then reflexively asked who was speaking. Carry the recent exchange as contextual evidence without treating it as confirmed voice enrollment |
| [21:11 conversation](/Users/bbenziger/djr3x-v2/logs/conversation-2026-09-09-21-11-40.log) | Jeff's introduction and partial impersonation samples exposed competing enrollment/capture paths. Preserve eligible natural data and handle a decline without discarding useful accepted material |
| User's report of truncated impressions and current local-TTS notes | Generated text can outlive the audio that played. Track delivery and verify the distinctive final phrase; never report a take complete solely from the GUI transcript |
| User's Jeff nickname and "Boogers/creepy" reports; current impersonation fixes | Resolve explicit nicknames. A question, joke, or comment aimed at Rex is not the speaker's biography; generated parody must not become later factual evidence |
| [22:31 conversation](/Users/bbenziger/djr3x-v2/logs/conversation-2026-09-09-22-31-23.log) | "That's Jeff Benziger" became a literal name. The speaker, referenced person, and uncertain visible face need separate representation before any write |

Useful existing tests to read for contracts, not transplant wholesale:

- Capture/encoder: [test_transcription_backend](/Users/bbenziger/djr3x-v2/tests/test_transcription_backend.py),
  [test_campplus](/Users/bbenziger/djr3x-v2/tests/test_campplus.py),
  [test_speaker_segments](/Users/bbenziger/djr3x-v2/tests/test_speaker_segments.py).
- Attribution/learning: [test_voice_learning](/Users/bbenziger/djr3x-v2/tests/test_voice_learning.py),
  [test_voice_primary_identity](/Users/bbenziger/djr3x-v2/tests/test_voice_primary_identity.py),
  [test_person_reference_identity](/Users/bbenziger/djr3x-v2/tests/test_person_reference_identity.py),
  [test_face_tracking](/Users/bbenziger/djr3x-v2/tests/test_face_tracking.py).
- Meaning/output: [test_onboarding](/Users/bbenziger/djr3x-v2/tests/test_onboarding.py),
  [test_tool_router](/Users/bbenziger/djr3x-v2/tests/test_tool_router.py),
  [test_impersonation_recordings](/Users/bbenziger/djr3x-v2/tests/test_impersonation_recordings.py),
  [test_impersonation_take](/Users/bbenziger/djr3x-v2/tests/test_impersonation_take.py),
  [test_breeze_tts](/Users/bbenziger/djr3x-v2/tests/test_breeze_tts.py).

The latest reference commit includes `test_person_reference_identity.py` and the
recent changes to these tests. No test counts or green-suite claims are implied by
this catalog. V2 tests require its own venv and isolated runner; v3 needs its own environment.

## Research pointers, not selected new dependencies

- [3D-Speaker](https://github.com/modelscope/3D-Speaker): primary toolkit reference
  for speaker models and diarization. Evaluate any added model on the actual workload.
- [Overlap-aware low-latency online speaker diarization](https://arxiv.org/abs/2109.06483):
  an incremental clustering/segmentation approach worth comparing with the simple
  baseline. This paper is not a benchmark of Rex, CAM++, or the M2 robot.
- [MLX](https://github.com/ml-explore/mlx) and
  [MLX Swift](https://github.com/ml-explore/mlx-swift): native computation and alternate
  language interfaces. They do not establish a whole-program Swift speedup.
- [Qwen3-ASR Swift adapter](https://github.com/Blaizzy/mlx-audio-swift/blob/main/Sources/MLXAudioSTT/Models/Qwen3ASR/README.md)
  and [AVAudioEngine](https://developer.apple.com/documentation/avfaudio/avaudioengine):
  examples for a future measured native port, not prerequisites for this prototype.

Verify current primary documentation when implementing a changed external API. Preserve
the v2 lessons while measuring whether its particular adapter still meets v3's contract.

## Record each extraction

Append a brief entry when code is actually reused:

1. Capability and v3 owner; source paths, commit, dirty-file digest if applicable.
2. Exact behavior retained, dependencies/settings required, and code deliberately retired.
3. Adaptation to v3 IDs, lifecycle, cancellation, outcomes, storage, and error reporting.
4. Focused automated evidence, saved-audio evidence, live validation still required.
5. Any model/API behavior still assumed rather than verified.

Slice 0 (September 10, 2026) was implemented independently with Python standard-library
boot, configuration, and lifecycle code. It required no v2 source extraction or
baseline-data access.

### Slice 1: audible-loop adapters (September 10, 2026)

Source reverified: v2 `9806c74fd233a7118da88ca5ae55fb7450deec05`, clean working tree.
V3 does not import v2 modules or connect to its databases. Dependencies were installed
in v3, with the resolved environment in `requirements-live.txt`.

| Source / owner | Retained behavior and adaptation | Evidence / remaining validation |
| --- | --- | --- |
| `audio/transcription.py::_qwen_transcribe` → `adapters/asr.py` | Qwen3-ASR-1.7B-8bit, mono 16 kHz, batch decode through `stream_generate`, 256-token cap, token log probabilities, -0.35 initial trust floor. One instance-owned lock; explicit lifecycle/cancel token. No v2 globals, lexical bias, recent-Rex prompt, text correction ladder, or Whisper fallback. | Real synthetic saved-audio decode passed with the words preserved; room-quality thresholds and native cancellation still need live evaluation. |
| `audio/stream.py`, `audio/vad.py` → `adapters/audio.py`, `capture.py` | 512-frame callbacks, timestamped PCM, Silero 6.2.1, explicit selected mic, bounded buffers, reset on gaps. VAD loads only at start. Name resolution refuses missing/ambiguous matches rather than falling back to an unrelated index. No servo/AEC/watchdog startup chain copied. | Synthetic endpoint/gap/echo/duration tests passed; selected Mac microphone metadata validated without recording. Real endpointing, mic stalls, and output overlap remain live checks. |
| `intelligence/lean_brain.py::stream_reply`, `config.py::REX_CORE_PROMPT` → `adapters/hosted.py` | One main streaming generation with the existing `gpt-5.4-mini` model; a compact adapted Rex persona. Responses API with `store=false` and no retries replaces the old completion wrapper. No mood/governor/handler stack, planner, identity tools, or memory writer was copied. | One real reply request passed; production Session one-call and stale/cancel contracts passed with fixtures. Natural conversation and meaning remain unvalidated. |
| `audio/tts.py`, selected `TTS_V3_*` constants → `adapters/hosted.py`, `output.py` | `eleven_v3_conversational`, selected Rex voice, `pcm_22050`, natural stability .5, seed 1440639067, approved tag palette, and no `previous_text`. No silent buffered/local fallback, motors, LEDs, or cache. The sole output owner emits drain-based completion; incomplete phrases never enter history as fully delivered. | Real synthesis to WAV and synthetic PCM-final-tail checks passed. Actual playback, underruns, cancellation, and consistent voice across phrase requests remain live checks. |

Source SHA-256 for the two directly adapted protocol implementations:

- `audio/transcription.py`: `873e5cba07de8a4f5b6f3f0d29453d90c85ccab52ae72c548e01ef28e474be85`
- `audio/tts.py`: `7719de3988515d0decb451355bd7317db3c7eb155c878e19401c7e66cfc81356`

Deliberate speech scheduling choice: the v3 design streams ordinary prose. This
slice therefore synthesizes bounded phrases while the one reply generation continues,
unlike v2's whole-reply v3-voice path. It retains the v3 request settings but does not
claim equivalent timbre between phrases. Evaluate that tradeoff in the live gate;
there is no unmeasured second TTS mode to silently fall back to.

The user explicitly authorized the v2 device configuration and API credentials.
`tools/import_v2_setup.py` read selected `.env` entries and literal values from
`apikeys.py` / `config.py` without executing them. It wrote independent ignored
settings and secrets, retained camera selection as deferred metadata, and copied
only the nonpersonal ASR model files with matching SHA-256 digests. The private
manifest is `local/v2-setup-import.json`. No v2 data was modified, and no people,
voiceprint, original-person-audio, or claim import occurred.

Current protocol references checked during implementation:
[OpenAI streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses),
[ElevenLabs streamed speech](https://elevenlabs.io/docs/api-reference/text-to-speech/stream),
and [sounddevice raw streams](https://python-sounddevice.readthedocs.io/en/0.5.5/api/raw-streams.html).
Package-specific Qwen stream details were checked in the installed v2 mlx-audio
0.5.1 implementation, then exercised independently in v3. The
[slice-1 evidence](docs/INITIAL_TEST_PROGRAM.md#slice-1-implementation-evidence)
distinguishes synthetic contracts, real providers on saved audio, and pending live work.
