# Face attribution and shared vision plan

Status: the dev-Mac face path is implemented with **SCRFD-10G + EdgeFace-S**,
selected with the user's authorization after the saved-media comparison. It is
ready for live testing, not accepted on robot hardware. The robot target remains
**base Apple M2 / 16 GB**; M5 Pro measurements cannot establish M2 readiness.

The September 12 [proactive conversation design](PROACTIVE_CONVERSATION.md) places
face arrivals within a shared policy for replies and initiative. That broader
policy is planned; implemented face gates below remain unchanged. Visibility can
support considering a greeting, but does not prove willingness to engage or identify
the acoustic speaker. Social interpretation belongs to the conversation owner.

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
latest-frame mailbox supports three consumers; each holds at most one in-flight
frame, for up to four distinct full-resolution frames. Consumers hold references to
those frames, with explicit release and bounded in-flight work. A stalled consumer
must not retain arbitrary old frames; new work replaces pending work rather than
extending a FIFO. Four 1080p BGR frames are about **23.7 MiB** before metadata.

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
  M2/combined-load measurements. These are historical preparation results; the implementation below supersedes presence-only behavior.

### First compact challenger: EdgeFace-S

Downloaded only the 14,805,514-byte EdgeFace-S gamma .5 ONNX checkpoint from the
[publisher's release](https://github.com/yakhyo/edgeface-onnx/releases/tag/weights).
The GitHub release API supplies SHA-256
`b850767cf791bda585600b5c4c7d7432b2f998ccd862caae34ef1afa967d2e54`;
the downloaded file matched both that digest and the declared byte count.
`tools/prepare_face_challenger.py` pins these values, checks existing files before
reuse and performs no download on a matching rerun. Provenance is in
`local/models/visual-bench/edgeface_s.json`. No new package or live provider added.

The [authors' preprocessing example](https://github.com/otroshi/edgeface#inference)
normalizes tensors with mean/std .5 after scaling pixels to [0,1], equivalent to
the harness's RGB `(pixel-127.5)/127.5` transform. Real-image output parity against
the original checkpoint and alignment pipeline still needs validation; successful
ONNX inference alone does not prove conversion accuracy.

Matched resource-only comparison: fresh process per model, M5 Pro, ONNX CPU one
thread, 112×112 generated pixels, one first inference followed by 30 timed runs.

| Measurement | V2 ArcFace R50 | EdgeFace-S |
| --- | --- | --- |
| Weights (decimal MB) | 174.38 | 14.81 |
| Warm embedding median | 45.24 ms | 6.71 ms |
| Warm embedding p95 | 55.08 ms | 6.87 ms |
| First embedding | 126.61 ms | 8.73 ms |
| Load time | 70.03 ms | 55.03 ms |
| Lifetime process peak RSS | 485.95 MiB | 123.92 MiB |

In this run, median recognition was 6.74× faster and peak process RSS about 74.5%
lower. These are isolated host measurements, not incremental whole-core RAM,
small-face accuracy, detection cost, M2 performance, or a selected production model.
Reports are `local/validation/arcface-matched-resource-smoke.json` and
`local/validation/edgeface-s-resource-smoke.json`. Pose and object models were not
loaded. No camera, microphone, audio output or personal-image import was used.

At the initial preparation stage, no PNG/JPEG face images had yet been supplied.
Subsequent user uploads enabled the diagnostics below; representative robot gallery/probe images remain pending. Do not invent accuracy from generated pixels, label arbitrary images as
held-out robot data, or silently import v2's personal face database. The next data
step needs separate-session images from the dev camera and wide-angle robot camera,
with source face sizes retained; M2 hardware measurements are also outstanding.

```sh
.venv/bin/python tools/prepare_face_challenger.py
.venv/bin/python tools/benchmark_faces.py \
  --model local/models/visual-bench/edgeface_s_gamma_05.onnx \
  --sha256 b850767cf791bda585600b5c4c7d7432b2f998ccd862caae34ef1afa967d2e54 \
  --smoke --threshold .5 \
  --output local/validation/edgeface-s-resource-smoke-repeat.json
```

### First user-supplied video diagnostic

A user-supplied 1280×720 dev-camera MOV (~55 seconds) was sampled at one-second
positions using file-only OpenCV decoding. `samples/` is now ignored recursively;
no supplied media is tracked. `tools/inspect_face_video.py` adds an explicit offline
SCRFD-10G extraction path and fixed-reference comparison on identical 112×112 crops.
It uses static five-point similarity alignment; no mouth motion or audio is read.
This diagnostic is separate from the cross-session acceptance manifest and cannot
bypass its session-leakage checks.

The opening clear view was selected before recognition scores as the reference;
seconds 1–3 were excluded. At detector input 640, 49/55 samples had one face,
leaving 45 usable probes among 51 later samples. Six samples had no detection,
including absent/cut-off/turned-away views; these are not all false-negative labels.
The smallest detected native face width was about 59 pixels. All extracted crops
were visually reviewed; motion blur, side views and dark/backlit views remain
explicitly included. Both recognizers show weaker reference similarity on difficult
views. Their cosine scales differ, so higher raw scores do not prove greater accuracy.
No other person is present to estimate false accepts; there is no calibrated
operating threshold, persistent enrollment, or cross-session/camera/M2 acceptance.

A 960-input detection diagnostic recovered none of the missing views and added a
tiny background detection at one timestamp, making that sample ambiguous. This
export also emitted output-shape metadata warnings at 960; its timings are not
used for production performance claims. Larger detector input is not assumed better.

Private frames, crops, source/model digests and per-frame comparisons are under
`local/validation/bret-face-video/`, including `review.html`. Synthetic identity
and scale/translation alignment checks passed; the existing 119 blocked-I/O
contracts also passed. Native detector/alignment parity and additional-person,
separate-session evaluation remain required before selecting the face model.


## Implemented dev-Mac face path

`live --camera` now uses SCRFD-10G detection and EdgeFace-S recognition on CPU,
replacing the Haar presence-only adapter. Public model files were independently
copied from the existing comparison cache into `local/models/faces/`; **no additional
download** was needed. Hashes and RGB/static five-landmark alignment are pinned.
The offline video extractor imports the same production detector/alignment code.
ArcFace remains an offline benchmark only; pose/object models are not loaded.

Capture requests 1280×720/30 fps. One reader drains ffmpeg and publishes immutable
BGR frames; the face worker takes the latest at <=2 Hz. Its two ONNX sessions each
use one CPU thread with the CPU memory arena disabled and standard MLAS kernels
(`mlas.disable_kleidiai=1`), following the measured regression described below.
The shared mailbox supports three independently paced consumers, but only faces
are registered today. It does not open additional cameras. Frame timestamps are
monotonic receipt times, not calibrated hardware exposure times. Stop/close wakes
workers, terminates capture and unloads models after the workers have joined.

Visual tracking requires spatial AND embedding compatibility, with ambiguous
crossings/replacements creating fresh tracks. A name requires two compatible
recognition observations spanning >=0.8 s. Provisional EdgeFace thresholds are
cosine **0.55** with **0.10** runner-up margin. Compatible weaker evidence may retain
a recent name for at most two seconds; contradictory evidence removes it. These
are conservative initial operating points, **not calibrated confidence percentages**.

Face enrollment is an independent, transactional state transition:

- An explicit self-introduction can target a sole prominent face if the voice is
  usable and recent competing voices are absent. A supported existing voice can
  establish this same association without requiring another introduction.
- An introducer can name a sole face, one newly presented face, the unique unknown
  beside their recognized face, or the target of a delivered `ask_face_question`.
  Multiple ambiguous unknown faces retain only a hypothesis until clarified.
- Saving requires at least two compatible quality views separated by >=0.8 s,
  from the source utterance/session, with no competing gallery identity. Adjacent
  frames are correlated views, not independent confirmations of the supplied name.
  A provisional association may wait up to 30 s on the same visual track for a
  clearer utterance-bound view. Disappearance/replacement/conflict expires it.
- Quality gates include native width >=96 px, detector score >=0.75, brightness,
  sharpness, crop bounds and coarse frontal geometry. They are provisional filters,
  not validated guarantees of image quality. Recognition may use smaller images
  without allowing them into the gallery. Requesting a closer look is available
  to the single conversational owner; no movement executor was added.
- Store at most six references/person, with aligned PNG, original selected native
  bounding crop (JPEG), image digests, source frame/camera/session/turn, native box,
  dimensions, transform, quality, model namespace and introducer attribution.
  Stored names do not change voice enrollment status. There is no passive gallery
  growth from weak recognized frames and no automatic import of supplied samples.

The `face_schema=1` additive extension migrates an existing core database on normal
startup. Face blobs/vectors commit with the operation result, and survive restart.
Model snapshots contain face IDs/quality/state, never image bytes or embeddings.
`reset_face_identity` clears wrong face references and blocks automatic relearning
until an explicit introduction; it leaves names/voice profiles intact. Forgetting
cascades through face references. Correction invalidates the visual epoch and queued
old observations, preventing a cached name from reappearing. Pending hypotheses are
session-local and do not survive restart.

Crop history is capped at 192 entries / 16 MiB accounted storage, with 32-second
retention for ASR delay. Per-turn private crop evidence is limited to the three
largest visible faces and three views each; other faces retain presence/match context.
Original JPEGs are capped at 160 KB and aligned PNGs at 50 KB; larger samples are
ineligible. Full-frame/derived-storage and total-memory targets still require live
measurement; these bounds do not account for native model/decoder allocators.

Preparation and maintenance:

```sh
# These copies already exist on this dev Mac:
.venv/bin/python tools/prepare_face_models.py
.venv/bin/python -m r3x live --camera --record-evaluation
# Read metadata / correct face-only references using a listed person ID:
.venv/bin/python -m r3x profiles list
.venv/bin/python -m r3x profiles faces PERSON_ID
.venv/bin/python -m r3x profiles reset-face PERSON_ID
```

If the comparison cache is absent, prepare SCRFD using `prepare_visual_assets.py`
and EdgeFace using `prepare_face_challenger.py` first. Preparation verifies independent
copies; runtime has no v2 dependency or silent model download. The old Haar preparation
and runtime path have been retired.

### Implementation evidence and remaining acceptance

**135 blocked-I/O checks pass.** The suite covers natural introductions through the production Session,
third-party targets, delivered questions, ambiguous/off-target rejection, deferred
learning, conflicting gallery references, transaction rollback, idempotence,
restart, schema migration, namespace separation, correction/forgetting, stale epochs,
ASR-delayed crops, track replacement, ambiguity, bounded storage and shared frames.
It uses synthetic voices/images and temporary databases, not proof of recognition
accuracy or language-model interpretation.

On the user-supplied photo probes, the production EdgeFace adapter at 0.55/0.10
correctly assigns **4/5 known faces**, abstains on the remaining known face, and
leaves the one held-out person unassigned. This is stricter than the earlier 0.50
illustration and deliberately preserves abstention. Background faces are unlabeled,
not assumed unknown ground truth. Five known probes and one unknown are too few
to calibrate a release threshold.

The production detector/encoder/tracker, using the separate-photo reference gallery,
processed 110 half-second positions in the supplied dev-camera video: **98 frames
had detections, 55 named Bret, and none assigned another name**. Absent/cut-off views
remain in the 110 denominator; these are not 110 independent identification trials.
Three real crops also passed introduction/save/restart in a temporary database with
explicitly synthetic injected voice and semantics. No live personal profiles changed.
Private reports and reproducible scripts are under `local/validation/still-faces/`.

Outstanding: robot 5–15-foot/wide-lens images; calibrated unknown rejection, crossings
and off-camera attribution; real hosted-model introduction interpretation; camera
format/receipt-time validation; whole-core listening/playback contention and memory
pressure on the M2. Direction/energy/geometry integration and future pose/object
consumers remain deferred. The current face association is contextual evidence,
not proof against an off-camera speaker or a deliberately false introduction.


### Changing-frame memory regression and mitigation

A fixed-image smoke test concealed sustained native memory growth on the dev M5.
With varying saved JPEG frames, the detector-only path grew by roughly 38 MiB per
iteration; excluding the recognizer and image encoding did not remove it. Python
tracemalloc remained around 5 MiB, excluding a Python crop-history explanation.
This resembled the [upstream M5 changing-input report](https://github.com/microsoft/onnxruntime/issues/28231).
Disabling the CPU arena alone was insufficient.

Using ONNX Runtime's documented
[`mlas.disable_kleidiai` session option](https://github.com/microsoft/onnxruntime/blob/main/include/onnxruntime/core/session/onnxruntime_session_options_config_keys.h)
removed that growth in the changing-frame check without switching model/backend.
For 55 varied saved/upscaled JPEG frames through the production encoder/tracker,
lifetime process peak fell from **2,598.27 MiB to 267.84 MiB**. Median full face
analysis changed from **70.34 ms to 271.00 ms**. Upscaling here tests resources,
not small-face accuracy. This measured speed tradeoff preserves the <=2 Hz face
schedule on the dev machine and is preferable to sustained memory growth.

The final 110-position saved-video run with the selected CPU policy measured
median **272.75 ms**, p95 **275.31 ms**,
and lifetime process peak **278.80 MiB**, including file decoding,
with the same recognition/abstention counts and successful real-crop persistence.
Offline video decoding also explicitly uses one FFmpeg decoder thread. These are
isolated M5 process peaks, not incremental whole-core residency or M2 acceptance.
The raw private reports distinguish fixed-input, changing-input and decoder checks.
No new package or model download was needed for this mitigation.

### Contextual arrival greetings (September 11, 2026)

The user-authorized proactive face slice is implemented. A bounded arrival tracker
consumes the existing camera's public observations; it loads no model and opens no
additional camera. The idle conversation worker submits a typed visual event to
production Session. Python owns stability, freshness, size and cooldown gates;
the same conversational LLM owns whether and what to say using recent exchanges,
visible identities and the recognized target's accepted facts. No fabricated user
utterance, acoustic attribution or source recording is created.

Visual events expose only silence and a targeted face-name-question tool. They
cannot write personal facts or enroll identity. Questions become pending through
the existing output receipt only after completed audio delivery; another speaker
can then answer for the target. Target loss, identity/epoch changes, new non-echo
speech and cancellation invalidate generation/output. Frozen evaluation disables
this feature. Actual returns within a run are distinguished from simply recognizing
a saved person, so the model is instructed not to invent prior encounters.

Current pacing: three seconds stable, observations at most one second old, minimum
48 native face pixels and 0.006 normalized frame area; eight seconds after startup,
five seconds without detected speech and ten seconds after an interaction. Queued
capture/transcripts, active ASR, stale microphone frames and pending questions block
opportunities. Attempts have a 45-second global and 300-second per-person cooldown;
a new visit requires 30 seconds absent. Anonymous invitations are once per occupied
scene until the target gains a name or the scene empties for 30 seconds. A silence
choice or stop holds opportunities for 120 seconds. Attempts are consumed even if
suppressed, cancelled or failed, preventing automatic retry chatter.

Evidence: **151 blocked-I/O tests pass**, including shared Session/history/output,
no visual identity writes, third-party answers, cooldowns, stale/replaced faces,
queued speech, cancellation before PCM, and frozen evaluation. Three actual hosted
requests with fictional names and TextOutput produced a known-person greeting, a
targeted unknown-person name question, and silence after a request for quiet. Each
used one generation; no camera, microphone, TTS or personal imagery was used.
The private report is `local/validation/proactive-greetings.json`. No face models,
assets or dependencies changed; prior saved-media resource measurements are not
new measurements of this feature. Live greeting timing, social repetition, overlap
behavior and whole-core M2 resource acceptance remain open. The existing playback
echo window still limits recognition of human speech overlapping Rex's audio.

### Short self-introduction correction (September 11, 2026)

A live run exposed an incorrect dependency: a clean 1.34-second self-introduction
had a usable speaker track but could not save the sole visible face because voice
training requires two seconds per sample. Explicit semantic self-introductions now
allow face learning with an accepted acoustic track and consistent windows without
requiring voice-training duration. Passive voice-to-face learning keeps its existing
gate. Sole-face continuity, frame area, no recent competing speaker, trusted source,
no overlap/echo/direction conflict and compatible quality-gated face samples remain
required. Voice enrollment thresholds and saved identities are unchanged.

153 blocked-I/O tests pass, including short-introduction persistence across restart
without voice enrollment and rejection of ambiguous visual/acoustic evidence. The
regression uses synthetic face/voice vectors and the observed live speech duration;
it does not establish real-model accuracy. No camera or audio output was operated,
and no face was retroactively inserted into the personal database. Live enrollment
with the correction remains to be checked.
