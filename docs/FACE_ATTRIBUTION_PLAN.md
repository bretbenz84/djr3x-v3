# Face attribution and shared vision plan

Status: design and offline recognizer-comparison preparation only. No face ID
provider, shared-frame replacement, or persistent face enrollment has been added
to the live core. The robot is a **base Apple M2 with 16 GB unified memory**. The
development Mac reports Apple M5 Pro; its measurements cannot establish M2 readiness.

## Behavior to build

Maintain separate people, acoustic groups, visual tracks, and face/voice/name
association hypotheses. A conversation can name someone before their voice is
known. Automatic association is the normal path when independent evidence aligns;
a targeted question is for consequential ambiguity, not a mandatory enrollment
ritual. No mouth motion is used, including indirect active-speaker signals.

- One prominent clear face, one consistent voice and a self-introduction can link
  the existing face and voice groups to the name. Absence of other voices is
  supporting evidence, not proof that nobody is off camera.
- An introducer may answer for a shy friend. “That's my dad Jeff” can name the
  face Rex was asking about while the answer remains attributed to the introducer.
  Jeff's voice is still unknown. A relationship retains its source and subject.
- “I'd like you to meet X” plus camera reorientation can refer to the newly
  presented face. Preserve the before/after visual observations and the utterance;
  do not transfer the old face's identity to the replacement based on position.
- A known face/voice plus a new face/voice strengthens a pairing hypothesis. Do
  not force a one-to-one match when people can be silent or off camera.
- Recent strong face evidence should survive brief weak frames. Switching a name
  requires stronger contradictory evidence than maintaining a compatible track.
  Crossings, camera movement, disappearance and scene replacement weaken continuity.
  Apply recency to compatible observations, never use it to hide genuine conflict.
- Separate conversational belief, face/voice association, and permission to save
  training images. A distant face may remain tentatively recognized without its
  poor image entering a persistent gallery. Repeated adjacent frames are correlated
  evidence, not hundreds of independent confirmations.
- Unknown distant faces can prompt a natural request for a closer look when useful.
  Future movement planning may approach with obstacle checks; the face module never
  commands motors. On the Mac, asking is the available action.
- Robot direction evidence will use samples from the utterance itself, competing
  bearing clusters, energy and spread, plus calibrated camera/microphone geometry.
  Stronger energy is supporting evidence; a weaker bearing is not automatically a
  reflection. Head/microphone movement invalidates stale geometry. The Mac supplies
  missing direction evidence, never a fictional center bearing.

Persistent face references must keep person ID, original selected crop, source
frame/camera/session, crop transform, image quality, digest, embedding namespace,
association source turns and status. Camera provenance survives cross-camera
recognition. Corrections and forgetting remove dependent references and hypotheses.
Names can change without changing a person's underlying face or voice identity.

## V2 inventory, verified September 10, 2026

Reference revision: `9806c74fd233a7118da88ca5ae55fb7450deec05`, clean worktree.
These are source defaults, not verification of the robot's active environment or
measured simultaneous runtime memory. No v2 settings module was imported.

| Consumer | V2 asset / configuration | Scheduling or resource implication |
| --- | --- | --- |
| Camera | ELP-USBFHD01M-L21; 1920×1080, 30 fps | Preserve actual source pixels for distant faces; verify negotiated format on the robot |
| Face detection | SCRFD-10G, 16,923,827 bytes; detector input default 640 | Test 640 versus higher detail separately; resizing a wide frame may lose small faces |
| Face recognition | ArcFace R50, 174,383,860 bytes | Baseline challenger only; weights are not total runtime RAM |
| Pose | MediaPipe Pose Landmarker Lite, 5,777,746 bytes; up to 3 people | V2 requests an analysis every 0.2 s; not a measured guaranteed 5 fps |
| Objects/animals | RF-DETR Nano, 366,287,238 bytes | V2 default; objects every 2.5 s, animals every 2.0 s; shares a model but can perform separate passes |
| Legacy objects | EfficientDet Lite0, 4,602,795 bytes | V2 fallback, not the current default |
| Other future vision | MobileCLIP-S2 place recognition, expression/appearance paths | Inventory only; not loaded or copied into this face evaluation |

The user reports frequent RF-DETR mistakes. It is not selected for v3; object
model comparison is deferred. Face gaze-loop timing (0.08 s) must not be reported
as the face recognizer's measured execution rate. Avoid transplanting v2 fallback
chains, world-state globals, or expression/mouth processing.

## Shared visual pipeline design

One capture owner publishes immutable frame handles with camera ID, frame ID,
monotonic capture time, dimensions, format and camera-pose/scene epoch. One bounded
pool holds three full-resolution frames initially. Consumers hold references to
those frames, with explicit release and bounded in-flight work. A stalled consumer
must not retain arbitrary old frames; new work replaces pending work rather than
extending a FIFO. A three-frame 1080p BGR pool is about **17.8 MiB** before metadata.

Face detection, pose and object consumers choose independently scheduled frames.
Each consumer records its source frame ID, capture and completion time, source-to-
input transform and quality. Shared frame access does not require simultaneous
inference or identical sampling rates. Results can be joined only within declared
age/geometry bounds. Recognition crops come from the original source pixels;
resized detector/pose/object views are derived per job and promptly released.
No consumer owns another capture device or full-resolution historical queue.

Before rollout, compare CPU scheduling, Core ML and MLX with the *whole core*
active. Selective per-track recognition and best-image selection should avoid
embedding every detected face on every capture. GPU work shares unified memory
and may compete with Qwen; no accelerator is presumed free or faster.

## Initial resource targets — proposals to test, not runtime settings

- Aim for incremental face recognition residency below **256 MiB** and a
  face-path transient increment below **512 MiB**, measured against the same
  core without the face path. This is a candidate-screening target, not permission
  to sacrifice recognition accuracy to meet a number.
- Aim for all visual consumers together below **1 GiB incremental steady memory**;
  reserve capacity within that envelope for pose and a future object detector.
  Their weights alone do not establish feasibility.
- Cap full-frame and derived image storage at **64 MiB** initially. Account
  separately for native model tensors, allocator caches, gallery and results.
- Target the entire application below **8 GiB steady residency** on the 16 GB M2,
  leaving room for macOS and transient allocations. Measure total memory pressure,
  compression and swap growth during sustained conversation; process RSS alone
  cannot establish this gate.
- Listen first: compare ASR and end-of-speech-to-first-audio median/p95 against a
  matched camera-disabled M2 run. Record stalls, skips, dropped frames and playback
  underruns. Exact allowed degradation must be frozen from that baseline before
  acceptance. No long-running combined-load acceptance has happened yet.

## Comparison sequence

1. Reuse independently copied v2 detection/recognition assets as the baseline.
   No new weights need downloading for harness preparation.
2. Collect/review close gallery images and separate-session probes from both
   cameras, including 5/10/15-foot robot views, poor lighting, profiles, unknown
   people, crossings and laptop reorientation. Native face pixel width is the
   principal size grouping; estimated physical distance is supplementary.
3. First compare recognizers on **identical saved, aligned crops**. Candidate
   shortlist: ArcFace baseline, EdgeFace-S, FaceLiVTv2-S, AdaFace. Download only the
   next selected checkpoint. Every model gets its own preprocessing contract,
   digest, namespace and separately tuned threshold. Do not reuse an ArcFace
   threshold blindly or compare embeddings across models.
4. Separately compare full-frame detection and alignment, preserving missed faces
   in denominators. Then run chronological tracking/association scenarios with
   and without continuity. Report wrong-person switches, flicker, abstention,
   off-camera attribution and incorrect enrollment, not only still-image accuracy.
5. Screen on the dev Mac, then measure candidate finalists on the base M2 with
   ASR active. Later add pose/object load using the shared frame pipeline. A
   recognizer-only benchmark cannot choose the production winner.

Primary references: [InsightFace packs](https://github.com/deepinsight/insightface/blob/master/python-package/README.md),
[EdgeFace](https://github.com/otroshi/edgeface),
[FaceLiVT](https://github.com/novendrastywn/FaceLiVT),
[AdaFace](https://github.com/mk-minchul/AdaFace),
[Core ML execution provider](https://onnxruntime.ai/docs/execution-providers/CoreML-ExecutionProvider.html).
These informed the research shortlist, not a benchmark result on Rex.

## Prepared offline tools

`tools/prepare_visual_assets.py` copies only five allowlisted public model files
into ignored `local/models/visual-bench/`. It verifies independent files and SHA-256,
refuses mismatched existing assets, and records provenance in `assets.json`.
There are no runtime v2 imports, downloads, biometric imports, or model loads.
Pose/object assets are copied for later resource work but not loaded by the harness.

```sh
.venv/bin/python tools/prepare_visual_assets.py --source /Users/bbenziger/djr3x-v2/assets/models
.venv/bin/python tools/benchmark_faces.py --help
```

`tools/benchmark_faces.py` runs **one CPU ONNX recognizer per process** using the
existing v3 environment. It supports one float NCHW 112×112 input, explicit RGB
normalization, and normalized output embeddings. Other contracts/backends need
explicit adapters and parity checks before comparison. No model auto-downloads.

Use `--smoke` for generated-pixel load/latency/memory checks, or `--manifest` for
saved aligned images. Reports refuse overwrites. All reports should live under
ignored `local/validation/`. In restrictive sandboxes, reading this process's RSS
through `ps` may require execution permission; it never reads other process data.

```sh
.venv/bin/python tools/benchmark_faces.py \
  --model local/models/visual-bench/arcface_r50.onnx \
  --sha256 4c06341c33c2ca1f86781dab0e829f88ad5b64be9fba56e56bc9ebdefc619e43 \
  --smoke --threshold .5 \
  --output local/validation/visual-baseline-smoke-repeat.json
```

For real probes replace `--smoke` with `--manifest local/face-evaluation/manifest.json`.
Supply thresholds frozen from a separate tuning set; .5 above is only a smoke
argument and not a selected face-recognition operating point. No “pass” decision is
computed from that arbitrary number.

The manifest uses `version: 1`, `alignment: "arcface-112-v1"`, and an `images` list.
Every entry provides relative `path`, `sha256`, `person_id` (null for an unknown
probe), `split` (`gallery` or `probe`), `session`, `camera`, and `face_width_px` from
the **original image before alignment/resizing**. Images must already be aligned
112×112 crops. Gallery/probe sessions and file contents must be disjoint. Bounds
and path checks reject external/linked images and duplicate content. Human labels
are used only for scoring, never passed to the recognizer.

This stage does not generate crops, capture frames, run detection, evaluate temporal
flicker, enroll personal profiles, or run pose/objects/ASR. Those are subsequent
comparison stages, not hidden success claims from a crop-only tool.

## Current evidence

- Five public files independently copied and SHA-256 verified; **zero download
  bytes**. No v2 data changed and no personal images were imported.
- Real ArcFace R50 on generated pixels, CPU one thread, development **M5 Pro**:
  30 warm embeddings, median **45.48 ms**, p95 **46.04 ms**; load **60.07 ms**,
  first embedding **125.16 ms**. Process RSS before imports **27.34 MiB**, after
  load **364.30 MiB**, after run **480.58 MiB**, lifetime peak **481.45 MiB**.
  This includes the Python/native runtime but excludes detection and all other
  models. It is not a face-accuracy result, incremental whole-core measurement,
  or M2 acceptance. Report: `local/validation/visual-baseline-smoke.json`.
- Contract checks cover dataset leakage, unknown/ambiguous rejection, accuracy
  alongside abstention, and independent repeatable model copies. Current suite:
  **119 checks passed** with network/devices/private state blocked.
- Still required: representative labeled images, alignment/preprocessing parity
  against the reference, additional candidate models, full-frame evaluation and
  M2/combined-load measurements. Live face behavior remains unchanged.
