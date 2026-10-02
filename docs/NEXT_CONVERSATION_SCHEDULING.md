# Model-selected conversation timing

Status: first increment implemented October 1, 2026. The user rejected both a
visible-person prerequisite and the fixed 20/60-second unsolicited-speech waits.
Those gates and the automatic 8/15/30/60 continuing-lull backoff are removed.
See the [agency plan and evidence](AGENCY_IMPLEMENTATION_PLAN.md).

The same primary generation may include nullable `next_opportunity` on
`respond_with_intent`, `remain_silent`, face questions, memory/observation/visual
contributions, conversational feedback, inspection and attention tools. Source ID
and scheduling revision come from the supplied `reconsideration` context.

```json
{
  "source_id": "offered-source-id",
  "revision": 7,
  "trigger": "after_speech",
  "delay_seconds": 3
}
```

- `after_speech`: arm after successful complete audible output. Text-only, partial,
  cancelled and failed speech do not arm it.
- `after_decision`: arm after successful completion of a silent decision or
  contribution. An inspection must acquire evidence successfully; a failed
  acquisition does not schedule another attempt even if its explanation plays.
  Existing inspection-result continuations remain bounded and visible.
- `wait_for_input`: null delay, no timer; remove any pending request and suppress
  seed lulls/ambient cues until input. New arrivals/radar events remain distinct cues.
- Null or omitted proposal: no timer update. A consumed opportunity does not
  automatically regenerate itself. Ordinary replies retain their streaming path.

A due timer offers a fresh decision to the sole conversation owner. It cannot play
precommitted words or execute an old action. One versioned session request is kept;
source/revision validation rejects stale or duplicate rearming. Human speech,
explicit stop, relevant intention invalidation, forgetting and shutdown cancel it.
Missing visual audience evidence hides scoped notes without cancelling a generic
reconsideration; it does not authorize private speech. Quiet/floor state, targeted
pending questions, input health and the shared call
budget still constrain consideration. Audience access is revalidated in fresh
context; a missing face never provides permission to expose personal information.

Delay bounds are 3–300 seconds. A pending timer expires five minutes after its due
time. The shared resource cap is twelve proactive attempts per rolling five minutes,
with a minimum three-second attempt spacing. Repeated unchanged silent decisions
increase their minimum requested wait through 3/6/12/24/48/96/180 seconds. This limits
empty polling; these numbers are not validated social cadence or speech deadlines.

Without a model request, one startup consideration is available after eight seconds,
and one lull after each trusted human source uses `proactive_lull_seconds` (default
eight). A bounded unheard camera-cancellation retry retains its separate one-attempt
recovery contract. No continuing timer loop is inferred from an active intention.

Synthetic contract evidence and remaining hosted/live acceptance are in the
[implementation status](IMPLEMENTATION_STATUS.md). Future increments add physical
attention and invitation-lapse behavior; this timer does not implement those.


October 1 increment 3: opening a tracked social invitation requires an explicit
`after_speech` request through this same scheduler. Complete audible delivery commits
both the waiting invitation and its review delay. Before that time it yields space;
at review time the model may wait, act silently or explicitly lapse it. Time never
resolves an invitation. No second timer queue or automatic periodic follow-up exists.
New human input cancels the timer; closed heard history prevents treating it as a
fresh invitation. Identity clarifications and human floor holds remain separate.
