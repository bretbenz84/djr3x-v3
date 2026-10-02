# Proactive observation sources

The user requested completion of the proactive-conversation software before live
testing, choosing Sacramento, CA weather and top headline news. These inputs now
join arrivals, conversational feedback and memory in the same Session/model/output
path. No additional resident model or second language classifier was added.

## Configuration and lifecycle

The defaults in `settings.example.toml` are consumed by live runs; scene inputs require a camera:

- BBC News top headlines from `https://feeds.bbci.co.uk/news/rss.xml`.
- Open-Meteo current conditions for Sacramento (38.5816, -121.4944), in Fahrenheit,
  mph and inches. No inferred geolocation or weather credential is used.
- Scene observations from the existing camera: at most two small ordered images
  are attached to a proactive model request. `scene_observations_enabled = false`
  disables these image uploads while retaining the existing face-camera path.
- `news_feed_urls = []` disables news, and `weather_enabled = false` disables weather.
  At most four HTTPS RSS feeds are accepted; URLs cannot include credentials.

Since the October 1 agency increment, configured public observations start in live
mode with or without a camera; initiative does not require a visible audience. A single worker refreshes enabled
public sources every 15 minutes. Refresh does not run inside the conversation
worker or add requests to ordinary replies. `doctor`, imports and frozen replay
start no source workers, network calls or cameras. Optional failures are logged
and do not substitute another source.

Requests have socket timeouts, an eight-second streamed-response deadline and a
one-megabyte body limit. News requires a publication date within 48 hours; it expires
at the earlier of 48 hours after publication or six hours after retrieval. A failed
refresh can retain the last successful evidence only until that original expiry.
Weather is eligible for at most 30 minutes from its reported time. A weather change
can be described only against an actual previous report with its timestamp.

Feed publication time is not asserted to be the event date. Source URLs, attribution
and dates accompany each cue. Titles and descriptions are bounded, markup is removed,
and XML entities/DTDs are rejected. Feed text and text seen in images are evidence,
not model instructions or authorization to act.

## Shared selection and delivery

Up to four public cues rotate and interleave news/weather so one long feed cannot
hide another source. Cues that were offered back off for 15 minutes; repeated
headlines use stable article URLs rather than changing refresh timestamps.
Weather repetition uses coarse condition changes rather than its fetch time.

A new cue or significant visual change may create an `ambient` opportunity without
a new human utterance. Quiet/floor holds, microphone/queue gates and the shared attempt cap still apply.
No visible audience is required for a generic opportunity. See the current
[scheduling contract](NEXT_CONVERSATION_SCHEDULING.md). These providers cannot speak independently. The one model can select
an observation with `use_observation`, choose another contribution or remain silent.

The executor checks that the selected cue was offered and is still available.
New speech, changed audience, changed selected evidence, expired data or state
revision can cancel output. Only completed audio records a delivery receipt;
text-only, interrupted and failed output does not mark a cue heard. Receipts suppress
that public/scene cue for 24 hours across restart and expire from storage after a
week. No personal fact, emotion or identity is saved from a cue or generated remark.

## Scene evidence

A second paced consumer shares the existing camera's bounded FrameHub. It samples
at most twice per second, encodes images with a maximum dimension of 480 pixels,
and retains two cached images plus up to two images in a current model request.
There is no accumulating image history or full-resolution scene queue. The new
scene path does not write images to logs, databases or conversation history;
ordinary face-enrollment storage follows its existing rules.

Low-resolution pixel differences indicate possible change, not a detected object,
wave or emotion. The existing hosted conversation model interprets the supplied
images. A stable scene is not repeatedly offered; a new significant change permits
another consideration after at least 15 seconds. Small/distant gestures can be
missed, lighting/camera motion can create candidates, and two frames cannot guarantee
motion recognition. Scene remarks must preserve uncertainty. Mouth motion is not
used for attribution, learning, vetoes or identity evidence. Images never enter the
speaker tracker or ordinary-turn enrollment tool context.

The implemented reactions are conversational acknowledgments/remarks. Motor/servo
reactions, sound-event classifiers and autonomous physical behavior belong to the
wider robot roadmap, excluded by the user's scope clarification. No actuator
executor or physical-movement acknowledgment was added.

## Verification

`tools/run_checks.py` now passes **221 blocked-I/O checks** (205 before these
sources). New checks cover feed dates/provenance, markup and entity rejection,
weather freshness/units/change evidence, bounded refresh responses, source rotation,
worker shutdown, unchanged/stale scenes, image transport, source-only turns,
actual delivery, audience/floor guards and selected-evidence cancellation.

Explicit validation, without devices or personal databases:

```sh
.venv/bin/python tools/check_observation_sources.py --live-sources --hosted
```

The final run fetched dated BBC headlines and Sacramento weather successfully.
Four synthetic hosted cases produced the expected completion/silence status:
headline, weather, a red-rectangle test image and a serious-context silence case.
The three contributions selected the observation tool; the serious case stayed
silent. These are not real object/animal/gesture accuracy or humor measurements.
Source naming in prose remains imperfect; the headline reply did not explicitly
name its synthetic source. Existing social-semantic limitations remain open.

Final evidence: `local/validation/observations-7c365c1aeb8f4dfaa194e6ea65ba9277/`.
Four hosted requests used one image input and reported 21,847 input / 400 output
tokens in total. Reported usage covers completed responses; it is not a billing
estimate for cancelled requests. Hosted case wall time median/max was 1.375/1.636
seconds (four samples), excluding real capture, ASR, TTS and playback.

Ten synthetic 1280×720 frame encodes on the dev Mac took median/max 13.64/13.78 ms.
The simple compressed test image used 3,334 data-URL bytes; real scenes may be much
larger. This does not measure camera/ASR contention, native memory, M2 combined load
or live group behavior. No microphone, camera, speaker or robot was operated.

## Implementation references

The image request uses the documented Responses `input_image` data-URL path in the
[OpenAI image-input guide](https://developers.openai.com/api/docs/guides/images-vision).
Current-condition fields and units follow the
[Open-Meteo forecast API](https://open-meteo.com/en/docs).
The exact configured source feed is the
[BBC News RSS feed](https://feeds.bbci.co.uk/news/rss.xml).

## Tool-selected current-view questions

The shared scene cache also supplies optional `inspect_view(current)` calls during
ordinary conversation. This adds no camera consumer and can reuse an exact-image
cached description without another upload. The scene-upload setting controls both
paths. See [current-view configuration and evaluation](CURRENT_VIEW_VALIDATION.md).


## October 1 moving-camera continuity

All commanded head/base motion now clears pre-motion snapshots and gates new ones
through settling. The scene observer keeps 32 compact view signatures and 32 considered
IDs; a similar returning view does not generate another ambient cue. Different
pixels remain evidence for model judgment, not a new object or meaningful change.
Explicit inspections can revisit familiar views. General impersonal descriptions
and their delivered remarks can survive audience changes; person/private details
remain scoped. No extra model call or image retention was added. See
[implementation and limitations](AGENCY_IMPLEMENTATION_PLAN.md#increment-2-implementation--october-1-2026).
