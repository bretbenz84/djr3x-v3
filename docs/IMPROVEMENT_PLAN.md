# October 14 improvement plan

Owner-approved plan from the October 14, 2026 architecture review. The working copy lives
in the owner's plan document; this file records what landed in the repository and its
evidence. Diagnosis: Rex lives in the instant. The language model has no feeling of time,
vision has no object permanence, and driving has no memory of space. One-on-one
conversation works because the chat history is a memory carried forward.

Owner rulings (October 14): improve V3; no new features until steps 1–3 land (the pet
noticing work is the one kept exception); Rex teases and roasts everyone, guests included,
but not always: the conversation decides; a smarter model may write joke material on the
side, never delaying a reply; the camera stays; no lidar; the front-right ToF sensor will be
repaired. The owner asked for the whole plan to be implemented without robot runs; every
result below is synthetic, saved-log or hosted-model evidence until he tests it live.

| Step | What | Status |
| --- | --- | --- |
| 0 | Baseline numbers from session logs | Done October 14 |
| 1 | One shared picture of the present | Pending |
| 2 | Rex sets his own wake-ups | Pending |
| 3 | Short-term memory of space for driving | Pending |
| 4 | Make him funny again | Pending |
| 5 | Faster replies | Pending |

## Step 0: baseline (October 14)

`tools/baseline_report.py` reads the private session traces (read-only) and prints the
numbers each later step is judged by; `--since`/`--until` pick sessions by file date.
Replies now also log `model_first_output` (first prose or finished tool call), because 170
of 205 recent replies went through a tool and had no model timing at all.

Baseline: the 18 robot sessions of October 5–13 (2.2 hours, real hardware, owner present).
The private JSON is kept in `local/validation/plan-baseline/`.

| Measure | Baseline |
| --- | --- |
| Reply wait, end of speech to first audio | median 4.99 s, p90 7.45 s; 189 of 205 over 4 s |
| Model share (prose replies only) | median 3.2 s; ASR 0.37 s; first voice audio 0.38 s; endpoint 1.22 s (now 0.7 s) |
| Planned beats | 148 started: 46 heard, 27 silent moves done, 47 talked over, 10 let go, 43 skipped |
| Face tracks | 32 new track IDs per minute with a face in view |
| Re-greeted someone recognized 31–120 s earlier | 6 of 8 arrival greetings |
| Drive trips | 68: 51 completed, 10 partial; median 9.5 s, p90 40 s; 15 blocked or refused steps; 57 hosted looks |
| Near-repeated lines | 0 of 262 (word overlap ≥ 0.7); no joke record existed |
| Model cost | 262 calls per hour, about $1.36 per hour (Terra priced at list input rate: an upper bound) |

No robot run was made for this step (owner's instruction): the recent untested changes
were not run first, so the baseline predates the October 11–13 changes for many sessions.
