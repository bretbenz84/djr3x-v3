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
| 1 | One shared picture of the present | Done October 14 (synthetic) |
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

## Step 1: one picture of the present (October 14)

`src/r3x/present.py` is the single owner of presence and the conversation clock. It runs no
model and touches no device; the runtime, the reply path and the planner feed it faces,
detected things, trusted speech and Rex's heard lines, and read it back.

- **People.** Recognition by face or supported voice. A person is *here* for 45 s after
  their last recognition, and stays for up to 180 s while unrecognized faces or speech
  continue and nobody else is recognized. A visit only breaks after 180 s without
  recognition, so a renumbered face, looking away or Rex's own turning is not leaving.
  A known face is greeted as arriving only when their visit began in the last 60 s:
  someone heard or seen two minutes ago is not greeted again (the October 5–13 baseline had
  6 of 8 such re-greetings). This replaces the goal's own presence tracking, the 60-second
  "spoke recently" arrival check and the goal's separate 45/180-second constants.
- **Private audience.** The memory gate's 90-second carry and ten-minute company rule moved
  here unchanged (`PRIVATE_CARRY_SECONDS`, `COMPANY_SECONDS`); privacy stays stricter than
  presence on purpose. Forgetting a person removes them from the picture.
- **Things.** Pet sightings (the cat/dog flicker, short dropouts, settle, return and
  cooldown rules of the October 13 pet arrival) and the last few object labels with when and
  where they were seen. The 10-second pet recall rule is now a constant of this module.
- **Conversation clock.** Who spoke last and when, Rex's last heard line and whether anyone
  answered, how long the quiet has lasted, and his unanswered own lines (moved from the goal).
- **Time in words.** `right_now` goes into every reply, beat and planner request: people
  here and since when, any pet or thing seen lately, and the clock in words ("his question
  went unanswered and that moment has passed: do not repeat it or ask again", "a lull",
  "a long quiet stretch: the last topic has gone cold"). The persona and planner are told to
  trust these timing words over raw timestamps.

Retired: `AgencyPlan` presence tracking and `PRESENCE_GRACE_SECONDS`/`IDENTITY_INHERIT_SECONDS`/
`ALONE_PAUSE_SECONDS`, `spoke_recently`, `CoreState.audience_seen`/`company_at`,
`conversation_memory.VISIT_GRACE_SECONDS`/`COMPANY_SECONDS`, `proactive.PetArrivals`.
Still separate on purpose: the camera's own track bookkeeping (`faces.py`), the arrival
greeter's settle and cooldown rules, and the drive's remembered voice heading (step 3).

Evidence: synthetic only. All existing presence, privacy and pet checks pass unchanged in
substance; nine new checks in `tests/test_present.py` cover the same-visit rule, a real
return after an absence, the question-left-hanging wording, unanswered-line reset, pets,
forgetting, and `right_now` reaching beats and the planner.
