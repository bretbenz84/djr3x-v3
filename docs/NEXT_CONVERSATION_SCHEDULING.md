# Next implementation: model-selected conversation timing

Status: agreed next implementation; documented only, not implemented. The current
8/15/30/60-second policy remains active while the live-run repairs are addressed.

Extend the existing structured reply/tool arguments with a strict nullable
`next_opportunity` object. The same primary generation chooses its spoken reply,
optional intention update and either `after_speech` with bounded `delay_seconds`,
or `wait_for_input` with no delay. Python validates and records the proposal; it
never extracts scheduling instructions from English.

```json
{"text":"Birds often return to the same perch.","next_opportunity":{"trigger":"after_speech","delay_seconds":5}}
```

Start the countdown only after successful output completion. Failed, cancelled or
partial speech must not arm its proposed follow-up. A due timer offers the existing
conversation owner a decision with fresh context and delivery receipts; it cannot
play precommitted speech. New human input supersedes the request. Departure,
identity/audience changes, quiet/floor boundaries, shutdown, and expired/abandoned
intentions cancel or invalidate it. `wait_for_input` removes an older pending timer.

Keep one bounded, versioned, session-only pending opportunity. Reject stale sources,
revisions and retries that would rearm it. Reuse the idle runtime and output owner;
no extra planner call, separate scheduler service or English-command parser.
Preserve ordinary streaming on turns without scheduling changes. Define explicit
behavior for silence-only decisions and combining scheduling with other semantic
tools before implementation. Retire the replaced automatic backoff, rather than
run competing schedules.

Choose and validate delay bounds and the overall call budget against conversational
cadence; the current six attempts per five minutes may be too restrictive. Measure
actual end-of-playback to reconsideration/first audio, repeated quiet listening,
interruption, unanswered questions, cancellation, idempotency, startup, provider
failures and model calls. Synthetic contracts precede hosted semantic probes and
explicit live acceptance. Scheduling must not infer engagement or manufacture facts.
