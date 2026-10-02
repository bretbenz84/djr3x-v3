# Rex's own goals

Status, October 2, 2026: implemented in software and checked with the hosted probe on
fictional inputs. **Live acceptance on the robot is pending.** This replaces the October 1
"persistent narrative and step plan" design, whose history is summarized at the end.

## Why the October 1 design was replaced

Live session `f4e2bf9716d3485e96a7ca6837935c43` (9.6 minutes, real hardware) made 40
planner calls and saved 26 plans for 11 started steps, 4 completed steps and two
self-initiated spoken lines. 17 opportunities were cancelled by human speech and 8 as
`audience_changed_or_stale` while the owner stayed in view (the camera renumbers one face
many times a minute). Every human turn, even "Okay.", discarded the plan. Most plans became
silent head turns. Two trusted human requests lost their replies with no logged reason.
Earlier runs failed the opposite way: repeated clock-and-music riffs the owner called nonsense.

The owner rejected the mechanism, not its tuning. A plan was meant to be a goal Rex adopts
because he noticed something and pursues across many exchanges, the way people act in the
world, not input-then-react.

## Owner requirements (October 2)

1. **Being spoken to comes first.** Rex answers through the ordinary reply path. A goal never
   takes over, delays or re-drafts a reply to a human.
2. **People he notices get greeted.** By name and "how are you" when known; hello and a name
   question when new.
3. **If nothing happens, he starts doing things.** He goes toward something, reaches out his
   hand, crosses the room, looks down and comments.
4. **Interest is personable, not inventory.** Anything can catch his eye; he asks about its
   story ("What's that wall painting about?", "Tell me more about this photo"), never
   announces the obvious ("I see a clock").
5. **Motion needs no permission.** He must not drive into things and must respect the
   time-of-flight boundaries; there are no smaller caps for his own moves.
6. Fun, friendly and humorous throughout.

## How it works

**A goal.** One persisted record in the `agency_plan` table (format 2): `goal_kind`
(`catch_up`, `get_to_know`, `investigate`, `entertain`), a subject, a short first-person
narrative, an objective and up to six beats. Beat kinds are contribute, gesture, pose
(named poses, wave, `throttle_offer` to reach out, `look_after` to look and comment),
look, inspect and drive.

**What starts one.** The runtime's idle tick, in priority order:

1. Human work pending, quiet requests, the human floor and recent speech gate everything.
2. A settled new arrival is greeted directly by GPT-6 Luna (no planner wait). The goal then
   ends with `person_arrived` and the next goal is about that person. Someone who spoke to
   Rex in the last minute is not re-greeted when their face was briefly unrecognized.
3. Otherwise, with no live goal, GPT-5.6 Terra plans one **in the background**. It sees
   the recent dialogue, the subject (name, saved facts and recent exchanges for a known
   person), visible people, offered objects and visual memory, previous goals, capabilities
   and one current low-detail camera frame. A reply never waits for planning and human
   speech never cancels it; an explicit stop does.
4. With a live goal, one beat runs at each eligible lull (eight seconds of quiet by default,
   plus the beat's own 3-45 second delay). Luna performs it with fresh context and the
   offered tool, adapting it to whatever was just said.

**What steers it.** Human turns no longer end the goal. The reply model sees the goal
(`agency_plan`: objective and next beat) and may move it along when it fits.

**What ends it.** Beats used up (the next plan continues the visit or picks a new goal),
its person gone for 45 seconds (judged by face identity or supported speech; trusted
speech with no one else recognized counts as them), a human redirection
(`conversation_feedback` topic closed or avoided ends it but keeps it as context), a
failed premise (`revise_agency_plan`, which also improvises one beat now), or a new
arrival. Stop, quiet, forgetting and corrections erase it.

**Beat outcomes.** Completed beats need their receipts (heard speech, gesture pulses,
changed pose channels, a completed drive, an acquired image). An unheard beat that yields
to a human is retried once at the next lull, then skipped. A failed or refused beat is
skipped; the goal goes on. A beat that repeats one of Rex's last four lines word for word
gets one retry, then fails.

**Presence and privacy.** Track IDs no longer decide anything. Rex's own audience-scoped
beat waits when a recognized different person replaces its subject or one more face
appears than at the start; a question aimed at one face needs that face. Replies that use
private memory follow the same rule, so a flickering or renumbered face never drops a
reply, and any withheld reply is traced as `reply_cancelled` with its reason. A planner
goal about "the person here" cannot be saved when nobody is present.

**Camera frames.** The planner gets one frame. Beats of `get_to_know` and `investigate`
goals keep the proactive frame; catch-up and entertain beats do not (a probe beat opened
with a remark about the frame instead of its question). Looks and inspections fetch their
own fresh images.

**Cost controls.** At least eight seconds between planner calls whatever goes wrong; one
repair per plan; failures back off 10-60 seconds while Rex still improvises spoken beats;
no new goals after five minutes with nobody seen or heard. Live cost is unmeasured.

**Motion.** Self-initiated drive limits equal requested ones (0.25 m/s, 10 m per plan,
360° turns, six steps). The model sees `max_move_m` rather than the 1.5 m leg length it
misread live. A committed silent move is not cancelled by its own motor noise or camera
reframe; a spoken stop halts it. Charging, manual control, held poses and clearance
checks still refuse moves.

## Retired

The two-second quiet preparation trigger, discarding the plan on every human turn,
replanning after every interrupted step, track-ID audience invalidation of goal steps,
silent-head-turn recovery (recovery now speaks), the smaller self-initiated drive limits
and the October 1 step-script format (old saved records are discarded at startup).

## Evidence

Contract checks (`tools/run_checks.py`, blocked I/O, fake clocks, synthetic faces and
audio) cover: greeting before goal planning, the arrival becoming the next goal, planner
input with a camera frame, planning beside a human reply, human turns steering without
replanning, interrupted beats retried then skipped, failed beats skipped, track churn of
the same visit, newcomers and changed audiences, absent subjects, the five-minute pause,
planner pacing and failures, the repeat guard, frame filtering, equal drive limits and the
dropped-reply regression. Results are in [implementation status](IMPLEMENTATION_STATUS.md).

`tools/check_agency.py --live` runs the production scheduler, Session, Terra planner and
Luna executor on fictional cases (a known friend with two saved facts, a new visitor, a
room with a synthetic sailboat painting, rejected humor) with fake servo/base transports,
synthetic playback and an accelerated clock. Results are in implementation status. It
does not establish hearing, real perception, physical movement or wall-clock pacing.

## Remaining acceptance

The owner's next robot run decides it. Check that people get answered and greeted, that
Rex starts doing things when nothing happens (including driving over to something and
asking about it), that goals survive conversation, that humor lands, and that quiet and
stop still work. Count planner calls, completed beats and spoken self-initiated lines
against the October 1 baseline (40 planner calls, 2 lines in ten minutes).

## History

September 30 to October 1: three increments added working interests with self-scheduled
reconsideration, physical looks and gestures with view continuity, and an invitation
lifecycle. The October 1 replacement added a Terra-written narrative with one to four
steps executed by Luna. Live runs `c0ecb89d` (zero saved plans), `299c989c` (repeated
music riffs) and `f4e2bf97` (above) failed. The invitation lifecycle, physical executors
and view continuity remain in use.
