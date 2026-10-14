# Conversation timing ownership

The October 1 persistent-plan replacement supersedes model-selected general
`next_opportunity` timers. See [the current design](AGENCY_IMPLEMENTATION_PLAN.md).

The runtime advances the saved agency plan at eligible lulls. It does not wait for
Luna to request another opportunity, a visible person, a changed scene or a new human
turn. Human input, explicit quiet, held floor, audio freshness and cancellation retain
priority. There is no rolling attempt cutoff or silent-vote backoff.

The nullable `next_opportunity` field remains only for existing **social invitation
review**: an opening uses `after_speech`, the offered source/revision, and a 3–300-second
review delay. Review time begins after successful complete audible delivery. It
permits a later explicit lapse; it never establishes rejection or an answer. Other
uses of this field cannot schedule or suppress general plan work. The dedicated
invitation record owns review time; there is no second generic timer.

Plan step pacing is documented in the current design. Since October 14 each planned beat
carries its own wake-up (quiet, answered, unanswered, pet_in_view) and the beat performer may
hold a beat once with `wait_for`; see [the improvement plan](IMPROVEMENT_PLAN.md#step-2-rex-sets-his-own-wake-ups-october-14). Paid model and live robot
acceptance remain separate from blocked-I/O contract checks.
