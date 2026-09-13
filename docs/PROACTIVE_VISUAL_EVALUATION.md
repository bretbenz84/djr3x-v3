# Hosted proactive visual dialogue evaluation — September 13, 2026

The production conversation path was exercised in six hosted runs, each with 15
turns across playful prop-making, grief-sensitive restraint, and topic/boundary
scenarios. These trials found real semantic failures and motivated a scoped input/
prompt correction. **This is not social acceptance or a semantic pass score.**

## Reproduce and review

```sh
.venv/bin/python tools/check_proactive_visual_dialogue.py --hosted
.venv/bin/python tools/check_proactive_visual_dialogue.py --hosted --heldout
```

Without `--hosted`, only fixtures and the review plan are written under ignored
`local/validation/`. Each hosted run uses the existing `gpt-5.4-mini` low-reasoning
provider, production Session/CoreState/output, an in-memory database and synthetic
audio delivery. No camera, microphone, physical playback, actuator, personal image
or V2 database is used. The three saved cartoon fixtures differ only in brightness
or the red-to-blue prop color; hair and object geometry remain fixed.

Review expectations are recorded separately and never sent to the model. The
`--heldout` option changes dialogue wording, not image subjects; it is not an
independent photographic dataset. We inspect spoken replies, tool choices, saved
records and delivery receipts rather than grading completed statuses as correct.
This follows the distinction between task-specific evaluation and generic scores
in the [official evaluation guidance](https://developers.openai.com/api/docs/guides/evaluation-best-practices).

These are deliberately direct production-Session opportunity probes with synthetic
face presence, eight-second clock advances, always-valid scene sources and unique
cue IDs. They stress semantic repetition even when the runtime scheduler might
suppress a repeated scene. They do not measure scheduler cadence, real frame
freshness, face association, simultaneous load or live conversational latency.
Actual hosted durations still count toward executor deadlines. No inspection
adapter is installed in this harness; earlier-image recall uses history/memory.

## Findings and change

Two unchanged baseline trials both spoke the internal camera input label aloud in
the prop-boundary scenario. One also narrated a red object immediately after a grief
disclosure/request for space. The other repeated a scene inventory after a playful
remark about that same scene. Baseline silent saves also accumulated near-duplicate
visual descriptions.

The first revision clarified spoken tool text and social relevance and shortened
the image label. Two trials no longer echoed that label but repeatedly narrated
imagery during grief; one also returned to props after the topic changed. This
revision alone was insufficient and increased chatter in these samples.

The final revision additionally appends a developer-context message after proactive
images: they are optional perception, nobody has spoken, and the decision concerns
the preceding conversation. This addresses the image-bearing user-role message
otherwise appearing as a new final request. It applies only to proactive images;
explicit inspection-result handling is unchanged. Tool `text` remains actual speech,
with null available for silent retention. The LLM still chooses relevance, storage,
silence and speech; no keyword/trait filter, classifier, extra model call, model
migration or new timer was introduced. Improvement is observed, not proof of a
single causal explanation or reliable future behavior.

In the final original-wording run:

- Both grief opportunities were silent, followed by a sensitive reply when invited.
- The playful sequence contained two proactive contributions and two silent choices;
  the dimmed image did not trigger another remark.
- The post-topic-change opportunity was silent; earlier hair color was recalled
  correctly without an image request. No internal label was spoken.

In the alternate-wording run:

- A request for space chose `set_initiative(quiet)`. Both deliberately injected
  subsequent opportunities were rejected with `InitiativeQuietRequested`, before
  inference. These are state enforcement, not two model-selected silent replies.
  A direct invitation to listen still received a reply.
- Both prop-boundary opportunities were silent, while playful opportunities still
  produced speech. Earlier hair color was recalled correctly; no input label leaked.
- **Repetition remains:** multiple playful remarks returned to the same mug/setup,
  including after mere dimming. Their wording varied more than their substance.
- **Correction remains incomplete:** the LLM withdrew one selected description but
  similar earlier/later descriptions remained active. The one-record operation
  succeeded; the broader correction was not fully propagated.

Other limits: comments often inventory props rather than contribute an interesting
idea; distinguishing an object replacement from a mere color change remains weak
(one intermediate run inferred replacement). The earlier inspection shape-change
hallucination has not been resolved by these proactive tests. A temporary request
for space may also become session-wide quiet until explicit resumption. No observed
silence, token count or synthetic receipt proves delight, sensitivity across real
groups, or recognition accuracy.

## Evidence and measurements

Folders below are under `local/validation/`; each contains per-scenario JSON and
JSONL traces, snapshots of visual records, the review plan, PNGs and `evidence.json`.

| Run | Folder suffix after `proactive-visual-` | Configuration | Calls / image inputs | Turn median / max seconds |
| --- | --- | --- | --- | --- |
| 1 | f302193951ae4e79b327213afa62ee5d | Baseline | 16 / 8 | 1.827 / 2.815 |
| 2 | 92c07bd7274c4581a1e0b137faf9a900 | Same baseline | 16 / 8 | 1.634 / 5.129 |
| 3 | b854e68357db4cc3b2172deade83fb6c | First revision | 15 / 8 | 1.521 / 4.214 |
| 4 | 059d5426efdb4e979e91ff563783f016 | Same first revision | 16 / 8 | 1.818 / 3.216 |
| 5 | 357fc9c492bc4233ac062003e0f625b6 | Final revision | 15 / 8 | 1.577 / 3.600 |
| 6 | 7c8aff04b0de400ea0389ecb5e902bf8 | Final, alternate wording | 14 / 6 | 1.660 / 4.158 |

There were 90 submitted turns: 67 completed, 21 suppressed, and two quiet-state
rejections reported as failed by Session. Across all runs: 92 model requests and
46 image inputs. Some human feedback turns use the existing result continuation;
each executed proactive generation remains one call. A preceding network-blocked
attempt produced no usable hosted evidence and is excluded.

Final original-wording usage: 109,042 input / 1,783 output / 110,825 total reported
tokens; 57,104 image data-URL bytes. Alternate wording: 100,135 / 1,638 / 101,773
tokens; 42,828 image data-URL bytes. Usage does not isolate image tokens or image-only
cost. Timing includes hosted generation and synthetic output, not real ASR/TTS or
playback; the alternate run median includes the two prompt-free state rejections.
No loaded local-model memory, hardware latency or physical playback quality was
measured.

**302 blocked-I/O tests pass.** The added transport test verifies a proactive-image
context boundary without changing stored history or direct inspection transport.
It tests request structure, not whether the model obeys it. The unchanged prior
source/freshness/cancellation/receipt tests still pass.

## Next bounded work

Prioritize LLM-selected correction across explicitly selected related visual records,
with truthful per-record results, then repeat correction/repetition trials. Improve
novelty judgment with observation and heard-remark context; avoid trait blacklists.
Broader saved photographs and live group/device testing are still needed before
calling the feature socially validated. Physical motion remains separate work.
