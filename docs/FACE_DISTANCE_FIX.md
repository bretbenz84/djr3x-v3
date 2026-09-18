# Fresh camera frames and higher-detail detection — September 17, 2026

## Failure and cause

The owner's six-foot seated test logged 138 analyzed frames and zero detected
faces. Hosted inspection described someone standing, although the owner confirmed
being seated. No old source pixels were retained, so that description alone cannot
establish pose. A subsequent authorized camera sample contained three byte-identical
images: one unique view, not three accuracy trials.

The raw-video transport reproduced the fault: 10 output frames had **one unique
pixel hash**, and FFmpeg reported `dup=201032`. AVFoundation advertised a bogus
`1000k` frame rate. Automatic frame synchronization duplicated the early frame;
V3 assigned fresh receive times and IDs to copies, making an old view look current.
A framemd5 muxer did not reproduce it; testing the actual raw-video path mattered.

`-fps_mode passthrough` produced **10 unique frames out of 10**. The reader now
uses that mode and refuses to publish identical consecutive pixels as new evidence.
Duplicate/resumption events are logged. Stale views expire through existing
freshness checks instead of repeatedly confirming a person.

The driver still reports falling back from the requested capture mode; measured
cadence was about five unique frames/second, not the requested 30. Pixels were
verified as 1920×1080. Frame timestamps remain receive times, not device PTS; this
repair does not certify sample-accurate audiovisual alignment.

## Higher detector resolution

SCRFD now uses a **960-pixel long edge**, padded only to stride-32 boundaries:
960×544 for this 1920×1080 camera. Formerly it used a 640×640 padded square.
Anchors use actual rectangular dimensions and map back to native pixels.
EdgeFace aligns/crops from the full-resolution source. Recognition/enrollment
thresholds and model namespaces are unchanged.

Same saved seated view, real models, three processing measurements per setting
(one unique image; no accuracy-rate claim):

| Detector input | Median detection + recognition | Similarity to saved profile |
| --- | ---: | ---: |
| Original 640×640 | 0.330 s | 0.882 |
| 960×960 square experiment | 0.724 s | 0.886 |
| **960×544 rectangular, selected** | **0.414 s** | **0.886** |
| 1280×736 rectangular experiment | 0.736 s | 0.884 |

The original detector found a roughly 114-pixel native face with a match above
the 0.55 recognition threshold. Distance alone was not the reproduced failure.
Higher resolution preserves detector detail; this view does not establish general
small-face accuracy. The model export has stale 640-square output metadata despite
supporting dynamic input. Known shape warnings are suppressed for this detector,
with actual output counts validated against each stride. Model bytes are unchanged.

## Diagnostics and verification

Camera logs now include source pixel digest, detector dimensions/maximum score,
candidate/detection counts and per-face size, detection score, sharpness,
brightness, embedding availability, similarity, margin and learning eligibility.
Routine JSON traces contain no images or embeddings.

- **403 blocked-I/O tests passed**, including passthrough selection and duplicate
  frames not refreshing evidence. Existing identity/learning guards pass.
- Authorized production camera-only check with read-only profiles: **11/11 unique
  frames, 11/11 detections, 7/11 recognized frames**, including initial track
  confirmation. Median analysis **0.425 s**, max **0.460 s**, no provider errors.
- No sound, movement, enrollment, profile writes or hosted calls were made during
  diagnostics. Saved-image real-model analysis and live camera checks are distinct
  from synthetic tests. Private receipts: `local/validation/face-distance/`.
- Full conversation load, two nearby people, difficult pose/lighting and longer
  distances remain acceptance work. The fixed feed changes what face/scene consumers
  see; their former apparent stability was not validation.

## Reply-time face evidence (September 17)

The subsequent live run `8ea29aa3483b47cfa964dcc3baa8ca93` exposed a timing
problem: at the questions around 47 and 162 seconds, face confirmation completed
after speech ended but before the conversational model request. The model received
only the earlier unresolved speech-window evidence.

Ordinary live turns now include a separate `current_view` snapshot, read from the
camera cache immediately before the primary request and refreshed for state-tool
continuations. The model uses it for current visibility. Speech-window `faces`
remain the source for attribution, introductions and learning. No extra inference,
frame wait or model request is introduced. Rows older than one second, future
rows and invalidated epochs are excluded; forgotten identities are removed.
An empty current view cannot establish that the room is empty or justify reusing
an old face. `reply_visual_context` traces record exactly the supplied snapshot.

Validation: **405 blocked-I/O tests passed**. Added synthetic session regressions
cover recognition arriving after the speech snapshot, unchanged speaker/learning
evidence, one primary request, and stale/future/invalidated/empty current views.
Live conversational acceptance of this timing fix remains pending; no hardware
or hosted providers were operated for these tests.
