# Current implementation status

Updated September 17, 2026. This is the consolidated checklist for current scope.
The linked plans retain dated implementation history and test results. An earlier
slice's limitations or test count do not describe the final implementation.
Documentation does not authorize new hardware operation or feature expansion.

## Completed software

| Status | Capability | Implementation and evidence |
| --- | --- | --- |
| Implemented | Microphone capture, transcription, one conversation owner, streamed speech, cancellation, delivery receipts, bounded history and network retry | [Operations](CORE_OPERATIONS.md), [initial slices and evidence](INITIAL_TEST_PROGRAM.md) |
| Implemented; combined live acceptance pending | ReSpeaker channel-0 capture, V2 host gain, read-only direction provider, source-linked voice/face/direction context and competing-direction learning guard | [ReSpeaker evidence and limits](RESPEAKER_INTEGRATION.md) |
| Implemented; live reliability incomplete | Persistent face/voice profiles, self-introduced names, compatible conversational enrollment, restart, correction and forgetting | [Face path](FACE_ATTRIBUTION_PLAN.md), [introduction-first recovery](INTRODUCTION_RECOVERY.md) |
| Implemented | Proactive arrivals/lulls, shared reply/initiative context, quiet/resume, conversational feedback, dated memory opportunities, news/weather and scene inputs | [Proactive stages 1, 2, 3 and 5](PROACTIVE_IMPLEMENTATION.md), [sources](OBSERVATION_SOURCES.md) |
| Implemented | Tool-selected current-camera inspection, rich visual descriptions, bounded visual memory and exact-image description reuse | [Current-view inspection](CURRENT_VIEW_VALIDATION.md), [attention slices](ATTENTION_IMPLEMENTATION.md) |
| Implemented | Related-record withdrawal, detail-preserving revisions, visual novelty context and actual spoken-remark records | [Visual correction](VISUAL_CORRECTION.md), [integration pass](CORE_INTEGRATION_PASS.md) |
| Implemented with simulated movement | Grounded attention intents, before/after speech gestures, movement/settle/capture sequencing, exclusive motion ownership and separate executor/speech outcomes | [Attention slices 1–3](ATTENTION_IMPLEMENTATION.md) |
| Implemented | Action continuity after interruption, bounded outcome history, no automatic resumption of abandoned work | [Integration pass](CORE_INTEGRATION_PASS.md) |
| Implemented | External-review repairs, scene-change cutoff repair and introduction recovery | [Review follow-up](REVIEW_2026_09_13.md), [introduction recovery](INTRODUCTION_RECOVERY.md) |

The agreed core software slices have no outstanding feature implementation stage.
Implemented does not mean acoustically reliable, socially accepted or free of bugs.
The latest blocked-I/O suite has **386 passing checks** after the
[robot camera mode repair](CORE_OPERATIONS.md#robot-camera-startup-september-17).
Prior counts in linked documents describe their dated checkpoints.

## Acceptance and defect investigation still open

- Live enrollment, alternating speakers, wrong-person assignments and abstention,
  including recognition across launches and room/device changes. Successful
  recognition in individual runs is progress, not broad group acceptance.
- Missed speech under difficult acoustic conditions and combined model/camera load.
  The historical raw-input/VAD failure is not declared fixed by later successful runs.
- Interruption and playback behavior after the latest repairs, including long replies
  and unreliable network conditions.
- Proactive timing, welcome participation, restraint, repetition, humor and visual
  description/correction quality with varied real scenes.
- End-to-end latency, tail latency, memory use and playback under the robot's base
  Apple M2 / 16 GB workload. Measurements on other Macs do not certify that target.

[Proactive stage 4](SOCIAL_ACCEPTANCE.md) is this live acceptance work, not another
coding slice. Failures found during acceptance can require scoped code fixes.
Synthetic tests, hosted synthetic-image probes and live hardware evidence remain
separate categories.

## Future coding requiring separate scope

| Future capability | Boundary of current implementation |
| --- | --- |
| Physical motion and expression | Attention slice 4: calibrated drivers, trajectories, limits, feedback, obstruction/manual takeover and movement/audio exclusion are not implemented. Current gestures and turning are simulated. |
| Continuous speech/motion coordination | Before/after simulated gestures exist. Word-level alignment, overlapping trajectories and continuous gaze blending require hardware integration and measured timing. |
| Live tracked-object attention after movement | Target/version contracts exist in simulation. Physical turning, tracked room-object targets and verified post-movement observations need real adapters. Current-camera descriptions do not supply those controllers. |
| Optional vision extensions | Adaptive image detail, general inspection-request coalescing and supported cross-session visual association are future options, not missing requirements of the completed core. Exact-image description reuse already exists. |
| Wider roadmap | Games, physical LEDs, richer relationship/biography systems, semantic memory search, offline conversational providers and a polished GUI remain separate future work. |

Mouth-motion/active-speaker research is not an active implementation task. The user
declined that experiment; no mouth-motion model or enrollment dependency was added.

## Reading the historical plans

Use this checklist for current completion status; use each feature document for
contracts, design rationale and dated evidence. The initial program's numbered
slices and the proactive plan's numbered stages are separate sequences. In
particular, the initial program's deferred expansion must not be confused with
proactive stage 5, whose news/weather/scene inputs are implemented.
