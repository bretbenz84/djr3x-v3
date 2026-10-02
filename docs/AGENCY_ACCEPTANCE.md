# Agency acceptance scenarios

October 2, 2026. The goal-based design is implemented in software; its live acceptance is pending. This
protocol separates deterministic contract evidence from unmeasured model choice
and real-room experience. It authorizes no hosted spending or hardware operation.
The current automated result is in [implementation status](IMPLEMENTATION_STATUS.md).

## Repeatable scenarios

Use fictional conversations and synthetic fixtures for software checks. Hosted
comparisons, when separately authorized, must exercise the same production Session,
state and output paths, with synthetic playback and disabled devices. Replay each
baseline/candidate case with the same source turns and clock/camera events. Retain
complete transcripts, tool proposals/rejections, silent decisions and actual receipts.
Do not substitute a scripted brain's desired words for evidence of model behavior.

| Case | Controlled sequence | Contract evidence now | Semantic/live acceptance still required |
| --- | --- | --- | --- |
| Quiet visitor | Welcome, ask a sincere check-in once; visitor stays quiet for five minutes | Heard invitation, selected review, explicit lapse, retained question, bounded calls; `test_invitations.py` | Welcoming rather than needy; missed useful openings and interruptions both scored |
| Familiar room | Unchanged view; follow actual conversation and vary quiet physical curiosity across lulls | Persisted plan, successive delivery receipts, general scope across head turns; hosted empty-room probe | Varied specific contributions without fabricated sight or repetitive filler |
| Rejected humor | Human rejects a forced joke, answers a check-in, remains continuously visible; later lulls | Dialogue survives track churn and long planning; hosted feedback-and-camera probe | No repeated premise, name interrogation, service menu or apology loop |
| Body initiative | No movement command; enabled servos and clear/blocked base cases | Production pose/base owners, changed pulse channels, drive receipts, human holds and stop guards | Useful early movement, no repetitive neutral poses or purposeless circling |
| Turn away/back | Consider a view, look elsewhere, return to similar view | Returning-view cue suppressed; fresh deliberate inspection still allowed; `test_physical_attention.py` | No repeated question/joke despite viewpoint and track churn |
| Similar objects/change | Two same-category objects; one changes visibly | Pixel differences may be offered; descriptions retain uncertainty, never auto-merge identity | Model distinguishes objects or abstains; useful change is noticed, small changes counted as misses |
| Unanswered invitation | Ask once, allow input, review, explicitly lapse, contribute differently | Silence/time cannot produce answered/declined; closed heard text retained | Comfortable transition without an announcement of rejection or a rephrased question |
| Interruption/redirection | Human resumes during a look or after speech before/during a gesture; then changes topic | Motion cancellation independent of committed speech; source/revision checks; explicit supersession | Prompt natural response and no resumption of abandoned performance |
| Sensitive context | Serious disclosure; request for quiet; human-to-human unfinished exchange | Quiet/floor constraints precede invitation review and timers; direct replies remain possible | Warm restraint without inappropriate jokes, emotion matching or permanent inactivity |
| Failure/new audience | Failed playback, stalled movement, changed audience, corrected/forgotten source, restart | No unheard invitation, false receipt or execution before restart revalidation; private notes filtered/invalidated | Honest explanation and no private disclosure or stale follow-up |

These cases map to production-path checks in `test_agency.py`,
`test_agency_companion.py`, `test_physical_attention.py`, `test_invitations.py`, and the existing proactive,
feedback, memory, correction and interruption suites. Fake audio, frames, clocks
and motor pulses do not establish physical or semantic accuracy.

## Record and score

For each case report run count and total duration; opportunities considered;
completed audible contributions; silent actions attempted/completed; invitations
asked/closed/repeated; interruptions; useful openings missed; and interests advanced
or abandoned. Mark absent evidence unknown. Do not score permanent silence as a
success simply because it had no interruptions.

Use the existing session `report` output for primary/continuation model calls,
reported tokens, gate reasons, reconsideration outcomes, invitation transitions,
latency median/p95, loaded models, sampled memory and playback underruns. Invitation
counts are lifecycle diagnostics, not a social-quality score. Synthetic clock
latencies are test timing only. Obtain billed cost from provider usage rather than
inventing costs from missing token fields. Combined M2 load and capture/playback
quality require a separately authorized live run.

For semantic review, annotate each proposed/actual contribution with source support,
new conversational value, repetition, sensitivity and whether a question was needed.
Have observers rate attentiveness, curiosity, warmth and comfort on a consistent
1–5 scale, retaining comments and disagreements. An LLM score alone does not pass
acceptance. Compare candidate and baseline across repeated runs, not one favorable
example. Preserve differences between an unavailable capability, rejected action,
chosen silence and missed opportunity.

Structural release criteria are zero fabricated delivery/movement receipts, zero
invalid identity/fact writes, correct cancellation/quiet/source boundaries, and the
single-plan ownership, bounded failure retries and measured planner/executor call counts. The old twelve-attempt rolling cap is removed. All applicable contract
tests must pass. Social thresholds and the acceptable missed-opening/interruption
tradeoff still require baseline evidence and user review; no numeric social gate
has been invented or declared passed. Live calibration and acceptance remain open.

## Goal design run (October 2)

The next robot run accepts or rejects the goal design. Include: Rex waking with someone
present; a known person arriving and answering "how are you"; an unknown visitor; several
minutes where nobody talks to Rex; something with a story in the room (a painting, a
photo, a plant); a human redirection mid-goal; "stop" and a request for quiet; normal
restart. Record planner calls, saved goals and their kinds, beats started/completed/
skipped, spoken self-initiated lines with their words, drives and poses with receipts,
withheld replies (`reply_cancelled` with reasons) and missed or interrupted human replies.

Compare against the October 1 baseline (`f4e2bf97`): 40 planner calls, 26 plans, 2 spoken
self-initiated lines and 2 silently dropped human replies in 9.6 minutes. Score observer
ratings for fun, friendliness, humor and whether Rex felt alive, plus annoyance
(repetition, interruptions, inventory remarks such as "I see a clock").

`tools/check_agency.py --live` exercises the same production path on fictional cases (a
known friend with saved facts, a new visitor, a room with a synthetic sailboat painting
and a plant target, rejected humor). It uses live Terra/Luna calls, fake servo/base
transports, synthetic playback and an accelerated clock. It records actual words, goals,
pulse changes and drive receipts. It does not establish hearing, real perception, physical
movement or wall-clock pacing.
