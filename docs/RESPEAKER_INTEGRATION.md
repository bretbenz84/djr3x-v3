# ReSpeaker microphone and direction — September 17, 2026

V3 adopts the validated V2 Flex microphone path and supplies direction evidence
to the existing conversation owner. No V2 runtime imports or data dependencies.

## Microphone

`microphone_mode = "respeaker-flex"` opens channels 0–1 at 16 kHz and selects
**only channel 0**: Conference output with hardware AEC, beamforming, noise
suppression and AGC. No mixing with raw capsules. Input and output must resolve
to the same named XVF3800 device so playback follows its echo-reference path.
`input_gain = 1.5` restores V2's saturating host gain. Since October 25 channel 1 (ASR output: AEC
and beamforming, no AGC, not ducked while Rex plays) also rides along on each frame for the keyword
spotter ("stop", "Hey Rex"). Since October 26 it is also what Rex listens to while a Jeopardy clue waits
for its answer under the think music (`VoiceRuntime._listening_frame`, level matched by `BeamGain`;
captures marked `channel=1` and never learned from); channel 0 carries all other listening. Standard input defaults to
unity gain. Mode/channel/gain are logged and retained in capture provenance.
The robot's local settings enable this mode and direction. Existing V3 VAD,
endpoint and preroll settings remain unchanged to isolate this integration.

Channel-0 hardware AEC was validated in V2 and reaffirmed by the owner. V3's
playback exclusion remains active; reliable talk-over and residual-echo rejection
need a separate combined-path check. `echo_isolated` is still a headset/test
setting, not a substitute for implementing barge-in.

## Direction and evidence

`direction_enabled = true` starts an explicit read-only USB provider. It verifies
channel-0 routing `(8,0)` and that processing is not bypassed, then reads basic
DoA, selected-beam azimuth and speech energy at up to 10 Hz. No model load,
firmware/DSP writes or physical movement. Failure is logged and direction becomes
unavailable; it does not silently replace the provider. Restart retries it.

Preserved V2 findings: selected beam leads the lagging DoA register; readings
below 50,000 speech energy cannot vote; circular clustering handles ±180°;
energy and recency weight the result. Adaptations: exact capture windows without
neighboring-utterance padding, bounded USB retries, strict VID/PID selection,
explicit shutdown, and no fallback from missing energy to stale DoA. Three
agreeing samples and 70% weighted share are required; competing clusters remain
ambiguous. These are provisional thresholds, not probabilities. Speakers within
the 20° clustering radius may remain merged.

Each utterance's status, bearing, samples, clusters and timestamps accompany its
acoustic attribution and public face tracks in LLM context and bounded history.
Direction is also retained in source records and accepted recording metadata.
Raw telemetry and timing stay in private traces. No extra model call is added.
Ambiguous directions use the existing tracker/enrollment conflict guard. Missing
direction does not block standard Mac conversations. Direction never creates an
identity or overrides incompatible voices. The model interprets references and
introductions; state validation still controls associations and writes. Existing
multi-face learning permissions are not relaxed by uncalibrated bearings.

Coordinates are **microphone-array relative**, positive left/counterclockwise.
No current arm/base measurement or exact camera transform exists. Old bearings
describe old utterances, not current locations. The robot enrollment arm hold
remains unimplemented; this work does not satisfy that geometry requirement.

## Verification

### Coordinate descriptions (September 17 follow-up)

The room run exposed an LLM sign error: -97° and -122° were described as left
despite rightward sensor evidence. `coordinates` now accompanies the full bearing
and every cluster: explicit side, unrounded angular distance from forward and an
arithmetic description (e.g. -97.4° → "97.4 degrees right of microphone-array
forward (7.4 degrees behind its right side)"). This is not another observation
or a quadrant-based identity rule. Full precision, spread, sample counts and
clustering remain unchanged. Ambiguous/unavailable results have no overall label.
Historical location corrections remain tied to their source utterance; the model
is instructed to use new evidence when asked about a new location.

401 blocked-I/O checks pass, including both live sign regressions, nearby front
bearings, changed location windows, missing/ambiguous evidence, cardinal axes and
angle wraparound. No live hardware or hosted model was used for this follow-up;
the next room run must verify the model uses the descriptions correctly.

The next room run confirmed correct rightward interpretation but described -8.2°
as "right" when the owner was basically ahead. Descriptions now lead with
"nearly straight ahead" within 10° of forward (and "nearly directly behind"
within 10° of rear). This is a wording boundary only: signed side, full bearing,
spread, clustering and identity evidence are unchanged. 402 blocked-I/O checks
pass, including this near-forward regression; revised wording awaits live use.

### Initial integration

- 398 blocked-I/O checks: synthetic PCM selection/clipping, wrong-output rejection,
  stale/weak/playback rejection, circular/competing clusters, USB errors and close,
  two voices at one bearing plus an unknown third voice, combined LLM context,
  source persistence and competing-direction learning rejection.
- Live **read-only USB**: 48 polls over 5.09 s; median 31.6 ms, max 63.0 ms per
  three-register poll; zero failures. 0.042 CPU seconds including startup (0.83%
  of one core); standalone peak RSS 25.0 MiB. Routing/bypass checks passed.
  No microphone/camera stream, audio playback or movement. Private receipt:
  `local/validation/flex-integration/usb-benchmark.json`.
- Dependency readiness and `pip check` pass. Combined live latency, room bearings,
  two-person accuracy/abstention and playback remain acceptance work. Synthetic
  checks do not establish Bret/JT discrimination or acoustic AEC quality.
- Synthetic PCM microbenchmark: 10,000 stereo blocks processed in 1.10 s,
  about 0.11 ms per 32 ms block (0.34% of one core at real-time rate).
