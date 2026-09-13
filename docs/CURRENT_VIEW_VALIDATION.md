# Current-view inspection and hosted visual dialogue

The current-camera inspection adapter is implemented. Camera-enabled live startup
with `scene_observations_enabled = true` exposes the LLM's `inspect_view` tool with
only `direction=current`. No regex selects the tool. Base rotation and post-motion
capture remain simulation-only; neither was operated or enabled by this slice.

## Acquisition and reuse

`CurrentView` reuses `SceneObserver`'s bounded, already-encoded <=480-pixel snapshots.
It registers no additional camera consumer, performs no image encoding, loads no
model and opens no device. A frame at most two seconds old can serve an inspection;
otherwise the adapter waits interruptibly for up to two seconds for fresh evidence.
Cancellation, shutdown, source failure and stale/unsupported views return explicit
outcomes. The shared output motion resource still serializes action execution.

Multiple requests can use the same encoded frame; there is no second capture stream
or concurrent inspection planner. If the transmitted image digest exactly matches
an eligible cached visual description in the same session/audience scope, the new
inspection result supplies that description and match evidence without uploading
the image again. Its reply is linked to that description's actual delivery history.
This is exact image reuse, not semantic similarity or an assumption that the unseen
world stayed unchanged. Different pixels do not by themselves prove a meaningful
change. Missing descriptions still require image input.

Image descriptions retain source, capture age, simulation/camera medium, uncertainty
and actual spoken remarks. No source creates accepted biography or speaker identity.
Real camera descriptions use `observed`, not `simulated`; the hosted evaluation below
explicitly labels its separate source `saved_synthetic_image`.

`scene_observations_enabled = false` disables both proactive scene uploads and the
current-view inspection capability. Face detection/enrollment keeps its existing
separate behavior. `doctor`, imports and configuration do not start a camera or
source worker. This change did not launch a live session or alter local settings.

## Automated evidence

**289 blocked-I/O checks pass**, including 10 new current-view tests for frame reuse,
unsupported directions, stale evidence, cancellation, lifecycle/supersession,
current-only tool schema, source failures, inactive App wiring, identical image
digests and production-Session reuse without a duplicate upload. Prior inspection,
visual memory, attribution and delivery tests remain passing. These use synthetic
sources and temporary stores, with network/devices blocked.

## Hosted multi-turn evidence

Run explicitly:

```sh
.venv/bin/python tools/check_visual_dialogue.py --hosted
```

Without `--hosted`, the command writes only the synthetic fixture PNGs. The hosted
mode uses the existing `gpt-5.4-mini` low-reasoning conversation provider, in-memory
SQLite and simulated audio output. No microphone, camera, real playback, robot or
personal photograph is used. The seven turns cover an initial description/save,
a switch to space travel, identical imagery, dimmed imagery, a red-to-blue object
change, correction, and recalling earlier hair color without inspection. The art
is deliberately simple: a brown-haired cartoon figure and a blocky mug shape.
It cannot establish real-person age, appearance or recognition accuracy.

Four completed hosted runs were reviewed, with changes between runs; these are
not independent repeated trials of the final configuration:

- `visual-dialogue-9104e021fe0e4b2caf68447b5490b49f`: recognized color/hair, but invented
  a wider/clearer view from identical pixels and did not retain requested details.
- `visual-dialogue-b96547ebcb7c40078fb0deec4c3d1cab`: saved/retracted a description and
  stopped inventing wider framing; skipped one explicitly requested fresh look.
- `visual-dialogue-85d2c9598beb4262af895cb0f6f0ab39`: still skipped that look and repeated
  the description instead of directly comparing. This motivated exact-frame cache
  reuse and clearer current-question context, rather than trait-specific rules.
- Final: `local/validation/visual-dialogue-a11ad7c9772f4ecd8fa340139c471beb/evidence.json`.
  All four requested inspections executed. One identical view reused its cached
  description, reducing image inputs to three. The initial description was saved,
  the correction retracted it, and earlier hair color was recalled without inspection.
  The space-travel turn did not introduce appearance commentary.

The final seven turns all completed, using 11 model requests (seven primary and
four inspection continuations), three image inputs and 21,414 data-URL bytes.
Reported usage was 88,866 input / 956 output / 89,822 total tokens. The provider's
usage does not separate image tokens from prompt/history tokens; no image-only cost
or dollar estimate is claimed. Median/max turn wall time was 2.744/4.819 seconds.
This includes hosted calls and synthetic delivery, not real ASR/TTS/playback latency.

**Remaining semantic failure:** the color-change reply added that the object looked
"more squared-off" even though only its color changed. Repeated-view answers can
also remain more descriptive than necessary. Completed statuses are not a semantic
pass score. The final run is evidence of tool execution, reuse, correction and these
specific judgments—not general naturalness, privacy, change-detection or humor
acceptance. More varied saved photographs and live group/device testing remain open.
An initial sandbox-blocked network attempt produced no usable hosted evidence.

The implementation retains the existing low-detail image-input path documented in
the [official OpenAI image guide](https://developers.openai.com/api/docs/guides/images-vision).
No model migration or additional resident vision model was introduced.

## Follow-on proactive memory slice

Proactive image-bearing opportunities can now use `describe_view` directly in their
primary generation, including silent retention. This reuses the same bounded store
and actual speech receipts as inspection descriptions. **301 blocked-I/O checks
pass**; this follow-on slice has synthetic contract evidence only. It does not
resolve the hosted semantic failure above. See the
[proactive slice](ATTENTION_IMPLEMENTATION.md#proactive-visual-description-and-silent-retention).
