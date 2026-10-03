# Review brief: Rex's goal-based initiative

For an independent reviewer (human or AI). October 3, 2026. Read `AGENTS.md` first; its
rules bind any change. The full design is in
[AGENCY_IMPLEMENTATION_PLAN.md](AGENCY_IMPLEMENTATION_PLAN.md); results are in
[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md).

## What to review

Three commits on `main`, compared with the previous design at `0222341`:

| Commit | What it did |
| --- | --- |
| `ab9c594` | Replaced step-script plans with goals (written by Claude Opus 5.5) |
| `44de7eb` | Repairs after a code review (written by Claude Fable 5.1) |
| `79d7ea6` | Removed a roaming limit the owner rejected |

```sh
git diff 0222341 HEAD -- src tests tools      # about 1,700 lines added, 540 removed
.venv/bin/python tools/run_checks.py          # 914 checks, blocked I/O, about 70 s
```

Nothing here has run on the robot. Evidence is the contract suite (fake clocks, providers,
servo and drive transports, synthetic faces) and `tools/check_agency.py --live` (real
GPT-5.6 Terra and GPT-6 Luna on fictional inputs with fake transports).

## What the owner asked for

Rex is a home robot (DJ-R3X). The owner rejected the previous design after a live run made
40 planner calls for two self-initiated lines. His requirements, in priority order:

1. Being spoken to comes first. A goal never delays, replaces or re-drafts a reply.
2. People who arrive get greeted: by name with "how are you" when known, hello and a name
   question when not.
3. When nothing is happening, Rex starts doing things: goes over to something, reaches
   out, looks, asks about its story. Not input-then-react.
4. Curiosity is personable. "What's that painting about?", never "I see a clock".
5. His own motion needs no permission and no caps. Obstacles and time-of-flight limits
   only. Do not propose distance or area limits; one was added and rejected.
6. He must be fun, friendly and humorous, or the project has no point.

Standing owner rulings that also apply: audible speech is never cut off except by stop
phrases; replies should start within four seconds; total running cost about $45 a month,
so stronger-model calls must stay rare; documentation and replies to him in plain language.

## How it works, in one paragraph

One persisted goal (`catch_up`, `get_to_know`, `investigate`, `entertain`) with up to six
beats. The idle tick gates on human activity, greets a settled arrival directly, and
otherwise either starts background planning (stronger model, one camera frame, saved facts
about the person) or performs the next beat at a lull (cheaper model, one tool). Human
turns steer the goal and never discard it. It ends when beats are used, its person leaves,
someone says to drop it, or a new person arrives. Unanswered spoken beats lengthen the
wait before the next one.

## Where to look

| Concern | Location |
| --- | --- |
| Goal record, presence, easing off, beat outcomes | `src/r3x/agency.py` (`AgencyPlan`, `present`, `finish`, `install`, `summary`) |
| Planner prompt | `src/r3x/agency.py` `PLANNER_INSTRUCTIONS` |
| Scheduler and cancellation rules | `src/r3x/runtime.py` `proactive_tick` and its inner `valid()` |
| Background planning thread | `src/r3x/session.py` `_planning_job`, `_start_planning`, `_run_planning` |
| Beat execution, repeat guard, reply guard | `src/r3x/session.py` `_process` (agency block, `_repeats_recent_line`, `memory_audience_valid`) |
| Greeting rule | `src/r3x/semantics.py` `greeting_violation`, `GREETING_TOOLS` |
| Stop versus quiet phrases | `src/r3x/session.py` `is_quiet_command`, `Session.cancel` |
| Pose holds yielding to his own moves | `src/r3x/servos.py` `human_hold_active`, `yield_stale_hold` |
| Reply-model instructions | `src/r3x/adapters/hosted.py` ("YOUR OWN GOALS", "YOUR GOAL, NEXT BEAT", arrival notes) |
| Tests | `tests/test_agency_plan.py` (`PlanTests`, `GoalTests`), `tests/test_agency_companion.py`, `tests/test_audience_recovery.py` |

## Where I am least sure

Please look hardest here.

1. **Threading.** Planning runs on a daemon thread while the conversation thread reads and
   writes `AgencyPlan`. Installs take the session lock; several reads do not. `facts` holds
   references into the turn's context. Look for races, stale installs and shutdown hazards.
2. **`session.py` `_process`.** One function of roughly 1,300 lines with many cancellation
   guards. Two live replies were dropped silently before; the cause was a private-memory
   guard. Check for other paths that cancel a reply to a human, and that each one is traced.
3. **Privacy versus liveliness.** `present()` lets an unrecognized face or voice continue a
   known person's visit for three minutes, so a private beat can reach a stranger in that
   window. `summary()` is stricter than beat execution. Is the asymmetry defensible?
4. **Known open issue.** A reply using private memory is withheld, not regenerated, when
   another substantial face appears before speech starts.
5. **Cadence and cost.** Roughly 40 to 60 planner calls an hour while someone is present
   and answering; easing off reduces it when they are not. Live cost is unmeasured. Check
   for any path that plans or speaks more often than intended.
6. **Prompt conflicts.** The reply-model persona is long and was written for an optional,
   mostly silent initiative. Look for remaining instructions that contradict required
   greetings or beat execution.
7. **Quiet phrases.** "Quiet", "be quiet", "shut up", "hush" and "stop talking" now set a
   lasting quiet that only the model's `set_initiative(available)` clears. Is there a case
   where Rex stays quiet with no natural way back?
8. **Pose holds.** A human-requested pose yields to his own movement after 90 seconds.
   Check interaction with the drive stance and the microphone hold.
9. **Humor.** In hosted runs the cheaper model reused one joke across four beats and once
   recited a description of a painting. Instructions were tightened and not re-measured.

## What the evidence does not show

- Real hearing, face recognition, movement or wall-clock pacing.
- Whether greetings fire reliably on the robot. They work in the hosted probe; in earlier
  live runs arrivals usually ended in silence.
- Billed cost.
- Whether Rex is funny or feels alive to a person in the room.

## What would help most

Concrete defects with file and line, a failing input, and the expected behavior. Then
design risks ranked by how likely they are to show up in the owner's first ten minutes
with the robot. Do not operate hardware, run paid model calls or change `local/` data
without the owner's say-so.
