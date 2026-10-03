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
  overview and optional target crop. Teaching receives only current images and the
  current human statement, with catalog names/IDs but no old appearance notes or
  conversation descriptions. Comparison receives up to two references each for
  at most three selected entities (eight images maximum). An empty candidate list
  automatically includes one reference per entry when the library has at most six
  entries. Ordinary `inspect_view` also includes that small-library comparison,
  preserving its existing description-saving path. Larger libraries still require
  model-selected candidates; an unavailable comparison is explicitly labeled. Per-image labels distinguish current
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

## Teaching and correction safeguards (September 17 follow-up)

The first user-run dog session exposed missing saves, skipped fresh inspections,
and an unnamed continuation overwriting the wrong dog's whole notes. The following
changes are implemented:

- `inspect_visual_memory` now declares `intent: look | teach`. A successful teaching
  inspection offers only `remember_object` and `defer_photo_teaching`, with required
  tool selection. Plain prose is rejected before it reaches output. Saving gets an
  executor acknowledgment; deferral explicitly leaves memory unchanged. Ordinary
  replies still use one primary generation, with no classifier or required preamble.
- A selected write requires the literal existing name in the current human utterance
  (the proposed name for a new reference). This validates an LLM-selected reference;
  it does not interpret language or infer a subject. An unnamed continuation or
  pronoun requires a named clarification. Renames currently require the old name
  too. Merely mentioning a name is necessary, not sufficient authorization: the
  conversational model still must interpret actual teaching/correction intent.
- `edit_object_memory.notes` appends a new detail, or null leaves notes unchanged.
  `remove_notes` must match an existing substring AND text explicitly supplied by
  the user. This prevents an unrelated appearance addition from dropping breed or
  other details. Corrections retain previous notes and original source attribution.
- With multiple dog/cat detections, saving a pet requires an individual target crop;
  the whole multi-pet overview is rejected. This does not prove the selected crop
  is the intended animal. A look continuation can now finish with a detail edit.
- Context explicitly distinguishes the complete saved catalog, unnamed detector
  hints and current photographic evidence. Model guidance calls for fresh whole-view
  comparison for current spatial questions, durable appearance notes instead of
  temporary locations, and clarification of ambiguous size/continuation references.

Validation: **417 blocked-I/O checks pass**. Added production-Session cases cover
preserving unrelated notes, rejecting wrong-subject fragments and unrelated removal,
restart/source history, updating after inspection, refusing a multi-pet overview,
required hosted tool selection, explicit deferral, and blocking premature prose.
No microphone, playback, motion or new camera capture was used.

Several authorized OpenAI replays used previously saved two-dog photos and isolated
libraries. The final nine-turn replay saved both named entries, preserved the breed
when adding color, asked for clarification on the unnamed fragment, and inspected
freshly for both spatial questions. It nevertheless **misidentified the dogs in both
answers** and generated an incorrect appearance description for a reference.
It also returned an empty model response for the unfinished sentence. That replay
sent the unfinished sentence to the model; the original live run interrupted it
before generation, so this is not an exact timing reproduction.
Private receipt: `local/validation/object-memory/hosted-c3e1dd5bf8684d2fa47d26094ed7b09a/`.
Nine primary calls plus four image continuations; typed-input-to-text-completion
median 1.94 s, maximum 6.09 s (includes the failed turn; no ASR/TTS/playback).
This is a small functional probe, not a latency or recognition accuracy benchmark.

Medium reasoning was also tried and removed: some inspections improved, but wrong
identifications/skipped saves persisted and individual teaching turns reached
14.71 s. The existing low reasoning setting remains. No resident models changed.

The live affected record was restored from its last correct saved history, keeping
its photo, recording maintenance provenance and backing up the prior catalog under
ignored validation storage. Test entries were not imported into the live library.

**Remaining limits:** selecting teaching intent and requesting an inspection still
belong to the LLM; the executor cannot validate arbitrary plain-language claims.
The save-or-defer guarantee starts after a teaching inspection is selected. Two-dog
visual matching remains unreliable even with fresh pixels. Detector mistakes can
also hide multiplicity. These changes protect memory and make teaching outcomes
explicit; they do not establish reliable individual-animal recognition.

## September 20 object teaching and restart repair

The live teaching run saved the blue toy's picture with an unrelated yellow-bone
appearance description; teaching had supplied that older reference alongside the
current view. The restart run used ordinary inspection without any reference
photos, then denied having a saved name despite the entries still being on disk.

Teaching now excludes historical images, appearance notes, prior conversation and
the model-authored inspection question from its continuation. It retains the exact
human statement, current images, catalog identifiers and state validation. Ordinary
inspection now supplies the bounded small gallery automatically, with high-detail
current/reference labels. Exact-image description reuse cannot bypass photographic
comparison. A corrupt reference leaves the current image usable and reports the
comparison failure. Photographic instructions were shortened rather than appended.

**480 blocked-I/O checks pass**, including production-session teaching isolation,
four-entry restart comparison with description retention, unchanged photo state on
recognition, the eight-image bound and corrupt-reference handling. These checks use
synthetic images/providers and temporary databases; they do not prove recognition
accuracy. No camera or playback hardware was operated. The subsequently authorized hosted saved-photo checks are recorded below. Existing live photos and notes were
preserved, including the erroneous toy description, which still needs an explicit
correction. Fresh-angle recognition and multi-pet discrimination remain unvalidated.

### Authorized hosted recheck

The user approved retrying saved-photo checks. Three trials used independent gallery
copies, the production Session and gpt-5.4-mini, with synthetic audio and no devices.
The first exposed a KeyError: comparison metadata incorrectly replaced ordinary
inspection's describe_view tool permissions with photo-save tools. Tool grounding
now preserves the ordinary inspection permissions, and the production-session
regression exercises that grounding. A later trial chose the observation-only tool
for teaching and did not save; its schema now explicitly distinguishes named-photo
teaching. The existing acquisition instruction also covers questions phrased as
remembering the name of a currently held object.

Final trial: current inspection supplied all four references for both objects.
The model described the toy as a blue textured ball (3.80 seconds), named the iPhone
and rose case (3.44 seconds), and used current-only teaching plus remember_object
with blue ball appearance notes (4.65 seconds). Median 3.80 seconds, maximum 4.65;
three cases are too few for a meaningful tail estimate. Each used one primary call
and one continuation. Executor success confirmed the teaching operation in the copy.
Existing-entry teaching adds/deduplicates a reference and intentionally preserves
old notes; it did not repair the already incorrect yellow-bone description.

Private evidence: `local/validation/photo-recheck-1f52a1df40654108a173b441c1c119ee/`.
Earlier failures remain in the other photo-recheck folders. The current images were
identical to saved references, not held-out views. Generic toy description alone is
not proof of named recall. Live recognition and new-view accuracy remain pending;
the live gallery was unchanged. All 480 blocked-I/O tests still pass.

## October 3: "the saved photo had expired" was not the saved photo

Run `be8c8d5584904f49b833fb6c03d33b9d`: after "He barks at every visitor", Rex said he had
tried to check Max's saved photo "but that image reference had expired". Saved photographic
memories do not age: Max's entry and its two reference photos were intact and are kept
until someone asks to forget them. What had lapsed was the live camera target. The detector
gives each thing in view a temporary id; ids restart whenever the head or base moves, and
the look ran 3.8 s after the id was offered (`ObjectTargetExpired`). The only time limit
in this feature is the 45 s a fresh teaching photo may wait before it is saved.

Changes: a look whose target id is gone now uses the one thing of the same kind still in
view (a dog for a dog; two dogs remain ambiguous), and otherwise the whole current view;
teaching still requires the exact target. A look that cannot be taken is not narrated
unless the person asked for one. One new synthetic check; not yet run on the robot.

The same run's stray person record "Max" (created when the dog was introduced by name) was
removed with `r3x profiles forget` at the owner's request, after a database backup in
`local/`. The pet entry and the owner's fact "has a dog named Max" were not touched.
