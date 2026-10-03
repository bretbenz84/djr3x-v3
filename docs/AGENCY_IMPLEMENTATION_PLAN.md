# Rex's own goals

Status, October 3, 2026: implemented in software, reviewed and repaired (see "Review
repairs" below), and checked with the hosted probe on fictional inputs. The first live run
of this design (`30ec1b04`) was judged a big improvement by the owner; his criticisms are
addressed under "Live run October 3" below and **those changes have not yet run on the
robot.** This replaces the October 1
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
2. A settled new arrival is greeted directly by GPT-6 Luna (no planner wait). Greeting is
   required: optional lull activities and "nothing to add" silence are not offered, and
   silence is accepted only for a stated reason such as people talking to each other. The
   goal then ends with `person_arrived` and the next goal is about that person. Someone who
   spoke to Rex in the last minute is not re-greeted when their face was briefly unrecognized.
3. Otherwise, with no live goal, GPT-5.6 Terra plans one **in the background**. It sees
   the recent dialogue, the subject (name, saved facts and recent exchanges for a known
   person), visible people, offered objects and visual memory, previous goals, capabilities
   and one current low-detail camera frame (the latest settled frame, even when that view
   was already offered or is unchanged). A reply never waits for planning and human
   speech never cancels it; an explicit stop does.
4. With a live goal, one beat runs at each eligible lull (five seconds of quiet by default,
   plus the beat's own 3-45 second delay). Luna performs it with fresh context and the
   offered tool, adapting it to whatever was just said.
5. **Easing off.** Each spoken beat nobody answers adds to the wait before the next
   spoken beat (0, 10, 20, 40, 80 seconds, then two minutes). A beat that needs no words
   (pose, look, drive) is not delayed, and it is performed silently once two lines have
   gone unanswered. Any human speech or a new arrival restores the lively pace. A new goal
   is planned at most 20 seconds before it is due.

**What steers it.** Human turns no longer end the goal. The reply model sees the goal
(`agency_plan`: objective and next beat) and may move it along when it fits. A goal about
one person is shown only on that person's turns.

**What ends it.** Beats used up (the next plan continues the visit or picks a new goal),
its person gone for 45 seconds, a human redirection (`conversation_feedback` topic closed
or avoided, or a declined invitation; it stays as context), a failed premise
(`revise_agency_plan`, which also improvises one beat now), or a new arrival. Yielding the
floor to people talking to each other only pauses it. Stop, quiet, forgetting and
corrections erase it.

**Who counts as still here.** A known person is present while recognized by face or
supported voice within 45 seconds. Live sessions showed recognition gaps of up to 50
seconds with the owner sitting still, so an unrecognized face or trusted speech continues
their visit, but only for three minutes after the last positive recognition and only while
nobody else is recognized. After that an unrecognized person is treated as someone else.

**Quiet.** "Stop", "whoa" and similar halt speech and wheels and pause initiative for two
minutes. "Be quiet", "quiet", "shut up", "hush" and "stop talking" also leave Rex quiet
(no self-started talk or movement) until someone invites him to talk again; he still
answers when spoken to.

**Beat outcomes.** Completed beats need their receipts (heard speech, gesture pulses,
changed pose channels, a completed drive, an acquired image). An unheard beat that yields
to a human is retried once at the next lull, then skipped. A failed or refused beat is
skipped; the goal goes on. A beat that repeats one of Rex's last four lines word for word
gets one retry, then fails.

**Presence and privacy.** Track IDs no longer decide anything. Rex's own audience-scoped
beat waits when a recognized different person replaces its subject or one more substantial
face appears than at the start (tiny or weak detections do not count). A question aimed at
one face follows that person through track renumbering and is dropped when they are gone
or replaced by someone else. Replies that use
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
misread live. A move under way is cancelled only by sustained speech or a stop phrase, not
by a blip of its own servo noise or its camera reframe. Charging, manual control and
clearance checks still refuse moves.

**No roaming limit.** A home range for his own moves was added on October 3 because the
base has no drop-off sensing, and removed the same day at the owner's direction: he never
places Rex where a drop is possible, and his own moves are limited only by obstacles and
the time-of-flight boundaries. His own "find that thing" drives get four paid vision looks
instead of twelve.

**Pose holds.** A pose a person asked for blocks Rex's own poses and drives for 90
seconds, then yields to his initiative. "Release" and new requests work as before.

## Live run October 3: feeling alive

Session `30ec1b046fb749558934ca55b50ffc06` (10.4 minutes, real hardware, the owner alone):
8 planner calls, 11 saved goals (3 were improvised after a revision), 19 beats started,
12 self-initiated lines heard, 29 spoken phrases in all, one self-initiated drive. Against
the October 1 baseline (40 planner calls, 2 lines) the owner said Rex "finally feels like
he has agency". What he still disliked, what the trace showed, and what changed:

| Owner's criticism | In the trace | Change |
| --- | --- | --- |
| "Sits there not doing anything for what feels like forever" when nobody talks | Gaps of 24-65 s between his lines; two beats were cancelled by phantom "Okay."/"What." (his own servo noise) and each then waited its full 15 s pause | Lull 5 s (was 8); planner pauses usually 3-8 s; an interrupted beat retries in 4 s; easing off caps at 2 minutes and delays talking only; brief sounds during his own motion no longer cancel a beat |
| Moved on his own once | 1 drive beat in 19; the planner preferred poses and looks | Most goals include a drive beat; a plain turn or short roll counts; moves may be silent |
| "Doesn't always have to announce why" | The one drive was followed by an explanation | Silent drive, pose and look beats are encouraged; required once two lines go unanswered |
| Did not turn toward a voice from the side | One utterance at 92° was skipped by the head reflex as "behind"; when asked to turn he searched 250° the long way | A voice 50° or more off-centre prompts a body turn (`face` the `speaker` target) with the reply spoken first; the model may skip it when something else has his interest |
| Room and furniture instead of people; never mentioned the owner standing there in his underwear | Goals: the couch, wall art, a chair, a window (twice); the camera sat high, with the face about three quarters down the frame | Planner and reply instructions put people first and anything out of the ordinary about them above all; decor is a last resort and never twice running; the idle gaze frames the face in the upper part of the picture so the camera sees the person |
| Wheel moves always at one speed | Fixed 0.25 m/s and 45°/s | Each drive step takes a pace: slow (60%), easy (80%) or normal |

Also found in the trace and fixed: the performer ended three impersonal goals with
`revise_agency_plan` for no real reason, and each fell back to the same remark about the
owner's car bumper (three times). Only a goal about a person can now be revised (they left
or refused); the improvised beat is told not to return to the subject of his last two lines.

Not verified: none of this has run on the robot or against the hosted models. Whether the
planner now notices people, how often it drives, whether silent moves read as alive rather
than aimless, and what the changes cost are all open until the next run.

## Review repairs (October 3)

A code review after the first implementation found, and this revision fixed: optional
greetings with contradictory instructions; no easing off when ignored; "be quiet" lasting two minutes; any held pose
disabling his own movement indefinitely; a person's goal shown on other people's turns and
private beats inherited by unrecognized strangers without limit; the planner usually
getting no camera frame; goals discarded whenever people talked to each other; name
questions cancelled by track renumbering; his own servo noise cancelling pose sequences;
no backoff after a planner crash; and twelve paid vision looks per self-initiated seek.

Still open: a reply that uses private memory is withheld, not regenerated, when one more
substantial face appears before Rex starts speaking (rare: 1 extra-face frame in about
3,000 in recent sessions).

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
planner pacing and failures, the repeat guard, frame filtering, equal drive limits, the
dropped-reply regression, required greetings, easing off, lasting quiet,
aged pose holds, goal privacy by turn, bounded identity inheritance, floor yields and
servo-noise handling. Results are in [implementation status](IMPLEMENTATION_STATUS.md).

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
