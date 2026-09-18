# Object detection and photographic memory — September 17, 2026

Implemented and enabled in this robot's ignored settings. Run the ordinary camera
conversation with `.venv/bin/python -m r3x live --camera`.

## Runtime

- RF-DETR Nano, exported from the V2 public pretrained checkpoint to ONNX, uses
  V3's existing numpy/OpenCV/ONNX dependencies. No RF-DETR Python package or torch
  detection model is loaded at runtime. One CPU thread, 384×384 input, one pass
  every 1.5 seconds for animals and objects together. No fallback or startup download.
- This is the third consumer of the shared FrameHub. It keeps one latest analyzed
  frame and at most 12 temporary detections. Two compatible observations establish
  a target; observations expire after four seconds. Image-coordinate ranks distinguish
  two nearby objects. A temporary overlap-based track is not an individual identity;
  crossing, occlusion and similar objects can change tracks.
- A newly stable object can offer a scene opportunity through the existing
  conversational owner and its audience, quiet, floor, queue and pacing gates.
  Detection itself never makes a model call, speaks, enrolls or writes a photo.
- Ordinary turns get a small catalog and current detection metadata. The model
  chooses `inspect_visual_memory` for teaching/comparison. It receives a current
  overview, optional target crop, and up to two references each for at most three
  selected entities (eight images maximum). Per-image labels distinguish current
  images from historical references. Crops/overview preserve up to a 960-pixel edge;
  hosted photo comparisons use high detail. A missed detector target can be inspected
  through the full view. Detector dog/cat category is not a memory lookup gate.
- One ordinary primary call remains. A selected photographic inspection adds one
  continuation, which can answer or save the user-taught reference without another
  generation. Correct/forget operations acknowledge executor success directly.
  Visual descriptions and personal claims remain separate from named photo entries.

## Visible files

`local/visual-memory/` is explicitly Git-ignored (also covered by `/local/`).

```
local/visual-memory/
  catalog.json
  <stable entity ID>/
    <image digest>.png
```

The catalog maps IDs to names, pet/object/room kind, appearance notes, original
teaching utterances, uncertain speaker attribution, source frame/digest, references,
corrections and bounded operation receipts. Pictures are ordinary PNG files, not
SQLite blobs. The initial capacity is 32 entities with six confirmed views each;
full galleries reject additions rather than silently dropping a taught view.
Identical images deduplicate. User corrections preserve source history. Forgetting
removes the entry and reference files; restart cleans interrupted orphan writes.
Catalog replacement is atomic; a failed commit is not acknowledged as saved.

Only explicit conversational teaching/confirmation saves a photo. A model guess
never automatically becomes another reference. Unknown acoustic speakers can teach
an object with their original utterance retained as unattributed; that does not
establish ownership, a personal claim, face identity or voice identity. No dedicated
room-recognition model is loaded; an explicitly taught room can have reference photos.

`object_memory_enabled` defaults false, requires camera-enabled live operation and
prepared weights. It independently permits photographic uploads; disabling
`scene_observations_enabled` disables the earlier scene-upload path, not this option.
The robot's local setting is true. Test memories remain in `local/validation/`,
separate from the initially empty live library.

Suggested live checks:

1. Clearly indicate one dog: “This black-and-tan dog is Maxx. Remember what he looks like.”
2. Show a different view and explicitly confirm it is the same dog to add a reference.
3. Restart and ask “Which dog is Maxx?” with both dogs visible.
4. Teach an individual object, correct its name, then request forgetting and inspect
   the directory. A generic silver laptop is not necessarily distinguishable from
   an identical laptop; uncertainty is appropriate.

## Measured evidence and limits

- **413 blocked-I/O synthetic tests passed.** Added tests exercise shared Session
  teaching/save/delivery, restart and reference retrieval, correction, forgetting,
  cancellation, failed writes, expired/unoffered evidence, frozen evaluation,
  strict tool transport, two-object continuity, category changes and expiry.
- Two authorized camera-only samples: six frames with one dog/laptop, then six
  with two dogs/laptop. Both dogs were detected separately in every two-dog frame.
  Single-dog detection median 0.372 s; two-dog median 0.376 s alongside face analysis.
  Six frames per setting are a smoke test, not a detection accuracy benchmark.
- Saved-camera-image plus locally synthesized speech load probe, with real Qwen,
  CAM++, EdgeFace/SCRFD and RF-DETR: six warm ASR samples per condition. ASR median
  0.575 s baseline / 0.578 s with objects; maximum 0.588 / 0.598 s. Face analysis
  median 0.437 s. Two overlapping object passes took 0.399 and 0.423 s. Peak process
  RSS reached 3.50 GB. This short probe excludes live capture, network, TTS/playback
  and system/GPU memory pressure; it does not establish a tail-latency guarantee.
- Hosted gpt-5.4-mini trials used saved authorized camera photos and typed teaching
  through the real Session with silent TextOutput and an isolated library. Teaching
  Max, correcting to Maxx, recognition after restart and saving the laptop passed.
  These took 4.93, 1.89, 3.18 and 4.18 seconds respectively before the later overview
  refinement; they exclude speech capture/ASR/TTS. Notes can still be inaccurate
  (one laptop description called an open laptop closed).
- Final explicit two-dog hosted check: whole-view comparison favored the black-and-tan
  dog and noted blur; the explicitly identified black-and-white lap dog was correctly
  rejected as Maxx. Two turns took 4.40 and 4.77 s. Receipt:
  `local/validation/object-memory/hosted-d90eeb929a054583ba32b63eb17d535d/`.
  One positive comparison and one negative comparison are not an accuracy estimate.
- Production camera, scene and object workers started together, shared exactly three
  FrameHub consumers, produced detections and closed cleanly with no provider errors
  in a final eight-second camera-only lifecycle check.
- Initial two-dog trials made wrong matches and confused historical references
  with current sightings. Explicit inline current/reference labels, high detail,
  an overview beside the crop, and position metadata improve the evidence supplied.
  Whole-view trials then distinguished the black-and-tan dog from the black-and-white
  dog. Ambiguous spatial follow-ups still produced wrong target selection or skipped
  fresh inspection. This is an outstanding semantic reliability limitation, not
  validated individual-animal recognition. Use explicit teaching and multiple views;
  do not treat an LLM match as verified identity.

Private receipts and scripts: `local/validation/object-memory/`. No live microphone,
playback or motion was used. The camera samples and hosted uploads were explicitly
authorized; production conversation acceptance remains pending.

## Model preparation / reference

The installed export is `local/models/objects/rfdetr-nano.onnx`, SHA-256
`c5bf86667ae59f6d10d508fcb0af90da0478a4eb22d0d9f0e051a217896509b9`.
`tools/prepare_object_models.py --onnx /path/to/export.onnx` verifies and copies
that export into a fresh V3 installation. It never accesses V2 by default.

The one-time export used RF-DETR 1.8.3, torch 2.12.0, ONNX 1.22.0, CPU, and
`RFDETRNano(pretrain_weights=<public checkpoint>, device='cpu').export(output_dir=<V3 destination>, verbose=False)`.
Prepare an isolated export environment with those versions if reproducing it;
export dependencies do not belong in the live runtime. Source checkpoint SHA-256:
`d8d6b9ee57d4d0ed2b1f305163624712a0532cb7bce0c747317984fc5457440d`.
The graph takes `input` [1,3,384,384], returns `dets` [1,300,4] normalized
center/size boxes and `labels` [1,300,91] logits using sparse pretrained COCO IDs.
RGB/ImageNet normalization, sigmoid scores and bounded duplicate suppression are
implemented locally. Model digest and shapes are checked; mismatches fail startup.

References: [RF-DETR export](https://rfdetr.roboflow.com/1.6.2/learn/export/),
[OpenAI image inputs](https://developers.openai.com/api/docs/guides/images-vision).
