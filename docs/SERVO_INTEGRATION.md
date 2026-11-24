# Servo integration: software ready for supervised commissioning

September 25, 2026. The owner requested adapting V2 servos, including the current
throttle-arm working tree, preserving recognizable startup/shutdown choreography
and movement while talking. The follow-on adds optional live composition, confirmed
handoff, operator tools and microphone protection. **Not activated or validated on
hardware.** The owner asked to leave V2 running and prepare cutover for later.

## November 23: the snap when the servo rail comes back on (tried, undone)

Owner: the board runs on the Mac's USB, the servos on a switched 7 V rail; switched off the robot
sags, and switched back on every servo snaps at full speed to the parked pulses the board is still
sending. Neither a board speed limit nor its startup/error setting can slow that (the board is not
changing its pulse, and it never sees the rail). Leaving every servo limp after the shutdown park was
tried and undone the same day (owner): with the rail still on, the robot then sagged at every
shutdown and snapped back at every start. Software can only move the one unramped snap in time, not
remove it, since the board cannot know where a servo sits.

## November 23: gentle slider moves in the servo menu

Owner: sliding in the servo menu jerked servos, mostly after a power cycle. The board stores no
speed (software sets one per move, for flexibility), and the menu had sent one only for the throttle
and the hands, so every other slider moved at full speed. **Changed:** every slider move sets speed
20 and acceleration 4 (at most 500 µs a second, easing in and out) unless the servo has its own pace.
The board cannot ramp the first pulse to a limp servo, since it does not know where the servo is, so for a
channel with no pulse yet the menu first sends where Rex's program expects it to hang unpowered
(`UNPOWERED_REST`), then eases to the slider; that slider also shows that position until moved.
The board's startup setting stays "Ignore" (owner): limp at power-up, holding its last pulse after
an error. Synthetic checks only; not yet tried on the board.

## November 23: the board rewired from the top down, by a wiring file

Owner: the Maestro is being rewired so that the lower a servo sits on Rex the higher its channel
(visor, head pair, neck and lift, then the hero, throttle and poker arms, each from the body out to
the hand), and asked for the layout to live in a settings file rather than in the code. **Changed:**
the program keeps naming each servo by a fixed id, its channel before the rewire (`LIMITS`, poses,
holds and logs such as `ServoArrivalTimeout:ch15` are by id). `servo_wiring.toml` at the repository
root gives the board channel for each id; `servo_wiring.load` refuses a file that misses or repeats an
id or channel, or splits the throttle's shoulder, elbow and wrist (one SET MULTIPLE TARGETS command
needs them on consecutive channels). `adapters.maestro.Maestro` translates every target, profile and
reading on the wire, and its observers (the dashboard's mirror) still see ids. `RobotServos` and the
operator park load the file for the real board; injected ports and simulations speak ids. The servo
menu translates on the wire too, and lists and labels its sliders by board channel.

| Board channel | Servo (id) |
| --- | --- |
| 0–4 | visor (3), head left (2), head right (11), neck (0), head lift (1) |
| 5–8 | hero arm (7), hero elbow (4), hero hand wrist (5), hero fingers (17) |
| 9–13 | throttle shoulder (8), elbow (9), wrist (10), hand twist (14), fingers (15) |
| 14–17 | poker arm (6), poker elbow (12), poker wrist (13), poker hand twist (16) |

The board's own per-channel settings (names, ranges, speed, home) move with the wiring and are
the owner's to copy over. Synthetic checks only; nothing has moved on the new wiring.

## November 22: the hero hand's wrist and fingers

Owner: channel 5 is the hero hand's wrist, `hero_hand_wrist` in the joint tool (was `hero_wrist`) and
in the servo menu (was `hand`), 768–2100 µs (`LIMITS[5]` 3072–8400; was 496–2496) with its mid at
1436 µs (`HERO_WRIST_MID`, 5744): where it is held when switched on and where the wave centres and
ends. Pride mode's wide swing reached 2175 µs and now stops at the top, 2100 µs; idle and talking
bends already lay inside the range. The hero fingers (channel 17) are 1242–1748 µs (`LIMITS[17]`
4968–6992) with their mid at 1500 µs (`TIPS_PARK`), and their idle and talking moves are shares of
that range around it. The Maestro's stored ranges for channels 5 and 17 must allow these.
Synthetic checks only; nothing has moved.

## November 22: the throttle shoulder's range is 1070–1960 µs

Owner: channel 8 runs 1070–1960 µs (`LIMITS[8]` 4280–7840; was 906–2000 µs). Straight down
(1850 µs), the 45° rest (1240 µs) and every other shoulder pose already lie inside it. Fully up
(`RAISED`) is the new top, 1070 µs: `throttle_offer`, `throttle_high_five` and the raised steps
out of the lowered `throttle_down` pose go there instead of 906 µs. The clearance rule still treats
fully up as clear for any elbow and wrist, which lets the wrist fold back (to 2048) on the way up
out of `throttle_down`; that was measured at 906 µs and is unchecked at 1070 µs. The servo menu's
slider matches, and the Maestro's stored range and home for channel 8 must lie within 1070–1960 µs.
Synthetic checks only; nothing has moved.

## November 22: the poker hand's twist (ch 16) and the hero fingers (ch 17)

Owner: two more servos, channel 16 ("pokerhandtwst", the poker hand's twist) and channel 17
("herofingers", the hero hand's fingers), each 496–2496 µs (`LIMITS[16]`, `LIMITS[17]` 1984–9984).
Which end turns or closes which way is not yet measured, so both rest at the middle, 1496 µs
(`TIPS_PARK`): where they are held when switched on (`UNPOWERED_REST`), and where the wake, park and
shutdown bring them. The model reaches them as `poker_hand_twist` and `hero_fingers` in the joint tool
(and sees them among held parts); `hold_arms` holds them; the dashboard sliders follow `JOINT_RANGES`.
The servo menu has `pokerhandtwst` and `herofingers` sliders (speed 20, acceleration 4) with nudges.
The board is an 18-channel Maestro (owner); both channels' stored ranges must allow 496–2496 µs, or
targets never arrive.

Later the same day (owner: add motion), they move like the throttle hand. *Idle:* each moves with its
own arm, the poker twist whenever the poker arm moves and the hero fingers whenever the hero arm does,
one eased move within a quarter of the half range either side of the middle (about ±250 µs), in a lane
of their own (`TIPS_IDLE_REACH`). *Speaking:* both together, three or four quicker moves within 40%
(about ±400 µs, `TIPS_TALK_REACH`) and back to where they began, so the lane repeats smoothly; they
run on to their targets when a line ends (`COAST`). A held channel stays still. While he drives the
hero and poker arms are held, so neither moves idly; speaking still moves them, as it does the
throttle hand. Synthetic checks only; nothing has moved.

## November 21 (night): run `5aa92add`, three seconds from the arm, and the let-go that never finished

Owner: the four seconds to closing began only once "Okay, ready." had played, though the arm was out well
before (out at 211.3 s, the line 213.5-215.1 s, closed 219.1 s); and after "let go" he froze. **Found:** the
countdown restarted at the end of the line (`hand_ready_said`). The fingers' closed target (1530 µs) sat below
the Maestro's stored minimum (1536 µs; the board stores limits in 16 µs steps, and it reports only the pulse it
sends, never the joint), so the close waited 25 s for a pulse that never came (`ServoArrivalTimeout:ch15@6144->6120`)
and "let go" waited behind it. Then logging the arm's rest passed the receipt's `status` and `reason` beside its
own, a `TypeError`, so the hand stayed `letting_go` and every frame sent the arm to rest again (about 200 times)
until shutdown. **Changed:** `CLOSE_AFTER_S` is 3 s, counted from the arm's arrival at the grab position (before
the look at the hand); `hand_ready_said` is retired and the session no longer waits for the line; the model's
wording says three seconds; the rest is logged as `move_status`/`move_reason`. The owner then lowered the
Maestro's channel 15 minimum and set the fingers' closed (lowest) pulse to 1500 µs (`LIMITS[15]` 6000-7900). Synthetic checks (two new: the countdown with a slow look, and a
traced let-go that ends `empty` with one rest); not yet run on the robot.

## November 21 (evening): run `6549d643`, the book, the poker arm and a new grab

Owner: he never held the book, repeated that he could not do it, and the poker arm never came out
though his left side had room. **Found:** the fingers' new open pulse (1975 µs) was beyond the
Maestro's own stored channel 15 maximum (1856 µs), so every `hand_take` and `hand_let_go` waited for
an arrival that never came (`ServoArrivalTimeout:ch15@7424->7900`, 25 s each; the first try was then
cancelled by a stray "Oh."). Each failure, and the kept poker arm's refusal, was spoken as "I couldn't
complete that movement." with nothing for the model to go on. Since November 17 the poker arm had come
out at only 3 of 25 wakes; the left pair are short-range sensors (reliable to about 1.2 m) and the
keep-clear was 1.07 m. **Changed:** the owner raised channel 15's Maestro maximum; the arm was moved
to the grab position with Rex off (owner request; fingers reached 1975 µs) and tuned in the servo
menu: `THROTTLE_GRAB` is now shoulder 1136, elbow 1554, wrist 743 µs (routes from and to `PARK`,
`REST` and `TUCK` checked). `POKER_KEEP_CLEAR_M` is 0.7 m (owner). A kept arm is traced once per
change with what it saw (`arm_clearance` `kept_blocked`, `left_m`, `front_right_m`). The refusal table
(`session.POSE_REFUSALS`, was `HAND_REFUSALS`) adds a kept poker ("Something's too close on my left
for that arm."), a kept throttle (the step-back line) and `ServoArrivalTimeout` ("That move got stuck
partway.", with a note not to apologise again). Synthetic checks; not yet run on the robot.

## November 21: new throttle hand mids and finger range

Owner: the wrist (channel 14) keeps 560–1856 µs but its mid, where the hand is level, is 1451 µs; the
fingers (channel 15) are now 1530–1975 µs (low end 1530 later the same day) with their mid at 1706 µs. `LIMITS[15]` is 6120–7900,
`HAND_PARK` 5804 / 6824, and the servo menu matches. The fingers still close at the low end, so
`FINGERS_CLOSED` is 1530 µs and `FINGERS_OPEN` 1975 µs; idle and talking reach follow the new mids.
The Maestro's own stored range for channel 15 must allow 1530–1975 µs or targets never arrive.
Synthetic checks only; nothing has moved.

## November 20: holding something in the throttle hand

Owner: the fingers close at the lowest pulse, so `FINGERS_CLOSED` is `LIMITS[15][0]` (1264 µs) and
`FINGERS_OPEN` `LIMITS[15][1]` (1856 µs). `HAND_POSES` adds three pose steps: `hand_take` (the
owner's grab position `THROTTLE_GRAB`, shoulder 1242, elbow 1720, wrist 887 µs (1136 / 1554 / 743 since November 21), with its own front-right
check; the hand level at its park, fingers open; routed by `throttle_route`, the hand in its own lane at
speed 20), `hand_grip` and `hand_let_go` (channel 15 only). `RobotServos` follows `hand_take` with
`look_right` and `look_down`; each frame `before_frame` closes the fingers in a worker thread once the hand has been
open `hand_hold.CLOSE_AFTER_S` (4 s from the session's "Okay, ready.", `hand_ready_said`; owner, later November 20; the reach out to `THROTTLE_GRAB` needs only `THROTTLE_KEEP_CLEAR_M`), and `hand_take` reopens a hand closed that way
(`hand_closed_on_time`). Held out to give back, it feeds the front-right reading and the widest face to
`hand_hold.HandWatch` and opens the fingers when it says so. `ServoOwner.grip_channels` (14 and 15 while gripping) survive `yield_stale_hold` and
`release_poses`, and the drive stance's lanes leave them out. After a let go the arm goes to
`REST` and the hand to `HAND_PARK` (not while `hold_arms` holds). Later the same day: a closed hand folds the
arm to `PARK` and `grip_channels` covers the arm too; `return_arm` leaves a holding throttle at `PARK`; `grip_strip`
removes the gripped channels from every request but the hand's own (`HAND_STEPS`, ids `hand:`) and park/shutdown
(a person's arm pose and the drive stance may still move the arm, never the fingers). `hand_let_go` while holding
goes out to `THROTTLE_GRAB` first and opens on `HandWatch`. The `held_item` row (`hand_hold.held`/`set_held`,
wired by the app as `RobotServos.held_memory`) sets `grip_channels` before the board starts: `_energize_unpowered`
sends `FINGERS_CLOSED`, `grip_strip` takes the arm and hand out of the wake and the hand out of park/shutdown.
`hand_put_down` goes to `FULL_DOWN` (front-right check), then opens. Synthetic checks only; nothing has moved.

Review, the same day: the hand's own moves (`_hand_worker`: the fold after a sensor grip, the return to rest)
no longer refuse or undo a request. `execute_pose` retries `ServoBusy` for up to `HAND_YIELD_S` while that
worker is alive, and a request that takes the arm (`_takes_arm`: a hand step, `release_poses`, `hold_arms`, an
arm pose) or a drive (`prepare_base_motion`) calls `_hand_gives_way` first: a move under way is joined, one still
waiting in `_hand_execute` returns `HandMoveSuperseded` and is not retried. `hand_grip` with the hand not out
(state `empty`, arm not at `THROTTLE_GRAB`) is refused as `HandNotOut`. `release_poses` while offering folds the
arm back to `PARK`. `grip_strip` treats `sentry:` requests as his own, so the arm stays folded on guard.
`HandWatch` wants the range near for `NEAR_S` (0.15 s, two 10 Hz reports) and the face close in `FACE_LOOKS`
looks (`hand_faces` now gives each width with the time the camera saw it). The session excepts `grip_channels`
from the held channels that switch his own poses off.

## November 20: the throttle hand's real limits

Owner, with the hand installed and its limits programmed into the Maestro: channel 14
("throttlehandwrst", the wrist twist) 560–1856 µs and channel 15 ("throttlefingers", the
pincher) 1264–1856 µs. `LIMITS[14]` is now 2240–7424 and `LIMITS[15]` 5056–7424, and the servo
menu's sliders match. The wrist parks at 1415 µs, level (owner, later November 20; the range's middle is 1208 µs); the fingers at 1525 µs from the owner's
notes (their middle is 1560). Idle moves the wrist up to about ±194 µs and the fingers ±74 µs
around the park; speaking up to ±324 and ±133 µs. Which
end opens the fingers or turns the wrist which way is still unmeasured. Synthetic checks only;
the hand has not been moved by Rex.

## November 19: a hand for the throttle arm (code only, not assembled)

Owner: the throttle arm gains a hand with two servos, a wrist twist on channel 14 and a
two-finger pincher on 15. Neither is assembled or set up on the Maestro yet; the owner will
measure the real range once it is built. Until then both run a conservative 1000–2000 µs
(`LIMITS[14]`, `LIMITS[15]` 4000–8000), and the park is the middle of the range (`HAND_PARK`,
derived from the limits, so it follows them when they change).

- *Where it rests:* the hand parks at the middle when switched on (`UNPOWERED_REST`), at the
  wake, at park and shutdown, for the drive stance and in the sentry stance. It moves on its
  own, outside the throttle clearance rule (shoulder, elbow, wrist bend), which does not know
  the hand: no collision between the hand and the body is measured.
- *Idle:* it moves with its arm (as every arm's joints move together, October 12): each idle
  throttle move also twists the wrist and flexes the fingers a little (within 30% and 25% of
  their half range either side of the middle), and when the throttle lowers to rest the hand
  eases back to the middle. As a part of its own it took phrase slots from the other arms.
- *Speaking:* a lane of its own, three or four quicker moves (within 50% and 45%) and back to
  where it began, so it repeats smoothly while slower lanes finish; it runs on after a line ends.
- *Holds:* part of the throttle arm everywhere a hold applies: `hold_arms`, a person's pose,
  the drive stance (still until its arm has walked back), and an arm kept in beside an obstacle
  (`REACHING_ARMS['throttle']`). The model reaches it as `throttle_twist` and `throttle_pincher`
  in the joint tool (0–100 of the range; which end is which way is not yet known) and sees
  `throttle_hand` among held parts. The dashboard sliders pick up both from `JOINT_RANGES`.
- *Servo menu:* `throttle_twist` (ch 14) and `throttle_pincher` (ch 15) sliders at 1000–2000 µs,
  speed 20 and acceleration 4, with nudges in measurement mode for finding the range.

The Maestro's stored range for channels 14 and 15 must allow 1000–2000 µs (its factory range
does) or targets outside it never arrive (`ServoArrivalTimeout`). When the real range is known,
update `LIMITS[14]`/`LIMITS[15]` and the menu's `_SERVO_DEFAULTS`; idle and talking reach, and
the park, are shares of the range and follow. Synthetic checks only; nothing has moved.

## November 21: the wake is a struggle, and a clear arm joins it

Owner: the wake should look "like a person waking up and stretching their arms", in step with the startup
sound and lights, the visor lifting a little without showing his eyes; then, after seeing it (run
`9ac3697f`), one more round so that it lasts until the chime.

**The moves** (`wake_lanes`; four lanes on one clock, every move eased with `eased_beat`). Seconds are
from the wake's start; the sound is heard from 1.5 s and the chime comes at 13.3 s.

| When | Head (pitch, tilt, pole) | Hero elbow | Visor | Neck |
| --- | --- | --- | --- | --- |
| 1.5-3.4 | a first stir, then back down | a twitch (2.0-3.2) | shut | still |
| 3.5-5.0 | a real try: up, cocked left, pole lifts | half a stretch | cracks to 1700 µs | still |
| 5.15-7.5 | droops, then shifts about | sags; full stretch from 6.5 | cracks to 1680 µs | looks to the first side (5.9) and the other (7.0) |
| 7.7-9.3 | another try, cocked right | holds, then sags (8.2) | cracks to 1680 µs | holds |
| 9.5-11.1 | sags again, shakes it off | one more reach (9.6) | cracks to 1700 µs | a smaller look (9.5) |
| 11.3-12.8 | up, level, at the normal height | down to its awake neutral | shut | back to the centre |

Beats carry `at`, the earliest second of the request at which they are sent, so a lane that arrives late
does not push its later moves (the first version used waits between moves and ran 9.3 s for a planned
8.6 s). The visor never passes 1680 µs (6720); `VISOR_DOWN` is 1744 µs and the open neutral 1433 µs. The
throttle and poker lanes of the wake are unchanged.

**The arms.** Since November 17 the wake leaves both reaching arms in when he has wheels, and the drive
owner brings each out once its space has read clear for 2 s. That could only happen after the wake,
because the wheel board connected after it. The owner: "if there's computed clearance", the poker arm
should start to come down and the throttle wrist move while he wakes. Now the wheel board starts with
the wake, `ServoOwner` sets `kept_arms` when the wake is accepted (not when it ends), and
`RobotServos.extend_cleared_arms` is allowed while the wake runs (`return_arm` already runs beside other
requests and commands only the kept arm's channels). The board connects in about 1.4 s and needs 2 s of
clear readings on a 1 s tick, so an arm can be cleared from about 4.3 s in. An arm with something in its
space stays in exactly as before, and comes out later, the usual way, when the space clears.

**The arms' stretch.** In the first run with this (`de486648`) both arms were cleared 4.5 s in and went
straight through their whole unfold, done at 9.6 s. Owner: "a smaller stretch routine on those that match
the startup sound clip rather than a full movement. It would eventually get to the standard poses." An
arm cleared while he wakes now follows `wake_arm_lanes` (tables `WAKE_THROTTLE`, `WAKE_POKER`), on the
wake's clock, in place of `base_lanes('base_release')`:

| When | Throttle arm | Poker arm |
| --- | --- | --- |
| 5.8-6.8 | a shrug: the shoulder a third of the way up | the elbow dips a little from straight up |
| 6.9-7.7 | most of the way back down | most of the way back up |
| 7.8-9.1 | with the second try, the shoulder up past the tuck | further down, the wrist opening |
| 9.1-10.9 | the wrist flexes a little (338 µs), then the arm sags | part of the way back (9.5) |
| 11.2-12.6 | out to its awake rest | down to the awake 45 degrees, hand level |

Every pose is inside the arms' own clearance rules: the throttle's wrist stays folded while the shoulder
is below the tuck (`throttle_clear`, checked again against the board's positions as each move is sent),
and the poker wrist stays where the elbow's ceiling is flat. An arm cleared later in the wake joins the
routine where it then is, leaving out the moves already over when the rest is still a clear route from
its park; cleared after the last move is due (11.2 s), or found away from its park, it comes out the
usual way. The `servo_base_release` log row carries `route` (`wake_stretch` or `direct`).

**Evidence:** synthetic checks only (the lanes' shape, limits, end pose and timing against the clip; the
moves kept to their clock on the simulated board; both arms stretching from 4.3 s into a wake and ending
at their awake poses; a blocked poker arm staying kept while the throttle stretches; every stretch pose
and late join inside the clearance rules). The wake without the arms' stretch ran once on the robot (run
`de486648`: motion ended at 12.9 s, chime at 13.6 s). **Not yet seen on the robot:** the arms' stretch.

## November 17: the arms wait for the range sensors at the wake

Owner: at startup the poker arm extended until it hit something. The wake's lanes moved the
poker elbow/wrist from `POKER_PARK` to `POKER_UP` and the throttle from `PARK` to `REST`
unconditionally, and the wheel board (the only range sensors) connects after the wake, so
nothing could see. Runs `e7d8c980` and `ec829d6b` both changed channels 12 and 13 at about 9 s.

Now `RobotServos(arm_guard=True)` (the app sets it whenever `drive_port` is configured) sends
the startup with `keep_arms` = both `REACHING_ARMS`: `lifecycle_lanes` leaves out the throttle
lane and the poker lane, and on success the owner sets `kept_arms` and holds their channels,
exactly as `return_arm(keep=...)` does after a drive. The head, neck, visor and hero elbow wake
as before. `DriveBase.tick_arm_clearance` then brings each arm out by `base_lanes('base_release')`
(the same throttle route as the wake, `POKER_UP` for the poker) once `arms_blocked` has read its
space clear for 2 s; in practice that started once the wheel board had connected, about when
the ready line plays (since November 21 it connects with the wake; above). No reading counts as blocked, so with wheels that
fail to connect the arms stay in for the run (traced `arm_clearance` `drive_base_unavailable`).
A throttle the wake finds away from `PARK` and `REST` now returns by `throttle_route`'s checked
waypoints instead of being refused. Without a drive base, and in telepresence calls (their own
wake goes to the drive stance), nothing changes. Limits: only what the side and front range
sensors see is guarded (nothing above or below their beams), and an arm already out is still
not pulled in when something comes close. Synthetic checks only; not yet run on the robot.

## November 15: feelings on his body

**Later the same day: `angry`, and the lights follow.** Owner: "if we make him mad by insulting him
his eyes could turn red." An eleventh expression, `angry`: a glare, the head down 0.2 at speed 30
with the visor eased to `VISOR_SQUINT` (speed 30, acceleration 6), held 1.1 s, the pole still. Every expression
but the nod now also sets his eyes, mouth and chest lights
([lights](LED_INTEGRATION.md#november-15-feelings-on-the-lights)). Wording: a tease gets skeptical,
an insult that is meant gets angry, one that truly hurts gets deflated. Probe and checks are
recorded with the lights. The glare is **not yet seen on the robot**.

Owner: "I'd like the servo animations to be more emotionally based. I'm not sure the LLM is choosing
stuff like that much." Measured from the session logs: under the earlier reply model he sent a line
with a head expression in 360 of about 4,200 replies (about one in eight in October), all but four
at the `subtle` half size, never `surprised`; 284 reached the servos. Since replies moved to Claude
(November 14–15): none in 119 replies. An expression only happens when the model sends its line
through `respond_with_gesture` instead of plain text, and the wording told it to "keep most lines
plain", "about one line in four at most".

- **Wording.** The tool and the per-turn note now say the opposite: a line carrying a feeling that
  someone watching would name goes out with the matching expression, `normal` the usual size; a plain
  answer, small talk and everyday attitude stay plain text. Still one generation per reply.
- **Four more feelings**, each a finite move that returns (`HEAD_EXPRESSIONS`, also `set_robot_pose`
  steps and plan gestures): `laugh` (the head rocks back twice, eased, the pole 200 pulses higher;
  about 2.4 s), `skeptical` (a slow sideways cock with the visor half shut, `VISOR_SQUINT` 6350, held
  1.1 s), `deflated` (head down 0.45, pole 500 lower, visor half shut, slow, held 1.2 s; about 3.3 s)
  and `proud` (chin up 0.3, pole 700 higher, held 1 s). An expression may now move the visor and the
  lift pole in lanes of their own, which stay until the head starts back; `subtle` halves everything.
  The half-shut visor covers part of his view for that second or two.
- **Probe** (`tools/check_gestures.py --live`, real Claude Sonnet 5.5 replies through the production
  session, simulated gestures, made-up lines; 18 paid replies in all). First wording: 9 of 10 lines
  got an expression, `proud` on five of them including "what is seven times eight". After adding
  that plain lines stay plain: 5 of 8, the three plain lines plain, and lottery news → `surprised`,
  an insult and a nonsense claim → `skeptical`, being proved right → `proud`, a roast → `laugh`
  (earlier run: a death → `sympathetic`). One reply each, one person, no history: how often he
  does it in a real evening, and whether he varies it, is **unmeasured**.

**Later the same day, run `c36ecba3`:** "I just told Rex he got new wheels and he showed no
surprise." He answered with a save (`correct_claim`, his line inside it), which had nowhere to put a
feeling; the save was refused (his voice was masked by his own motors) and the line that replaced it
was plain text. The October logs also show the feeling arriving late: a line's words stream out
ahead of the rest of its tool call, the gesture was queued behind those words, and in 109 of 225
`with_speech` gestures it finished after the first phrase had ended.

- **A save carries the feeling.** Every tool whose text is his whole reply (`SPOKEN_RESULT_TOOLS`)
  has an `expression` field (one of the head expressions or null); the line goes out as a
  `with_speech` performance. A refused memory save still shows it, at once, while the replacing line
  is written.
- **The feeling starts when it is chosen** (`OutputOwner.express`): for a line whose words streamed
  ahead, the expression starts beside the line being said instead of after it. The line's talking
  motion gives way and takes over again when the expression ends.
- **Examples of the behaviour** (owner: "not just patching the prompt but ... examples to follow for
  behavior"): the tool lists situations and what he does (told he has new wheels: surprised; their
  pet died: sympathetic; a sum: plain), with no words to say, since quoted lines get repeated
  (owner, November 9). The note adds that news about himself he did not know is a jolt first.
- **Probe**, 20 more paid replies (38 in all): wedding news was saved with a `wiggle` on the save;
  "I built you new wheels" got `proud` or `wiggle` until the last two changes and `surprised` in
  3 of 3 after; insult → `skeptical`, absurd claim → `laugh`, proved right → `proud`, and the plain
  question and small talk stayed plain. In the real run he had also been told about the wheels
  several times ("I've heard that three times today"), so there a doubtful look would be fair.

Synthetic pulses and a simulated gesture adapter only: how the four new moves look, whether they
creak, the pole's travel in `deflated`/`proud`, and an expression beginning in the middle of a line
are **not yet seen on the robot**.

**Later still: a gentler surprised visor.** Owner: the visor "is shooting up way too fast". It went to
`VISOR_SURPRISE` at speed 70 with unlimited acceleration, about 0.2 s, while the lift took 1.5 s.
It now goes at speed 20, acceleration 4: it eases in and out and takes about 1 s from the open
neutral (1.2 s from the most-closed talking position), still a little ahead of the lift. The way back
eases the same way at that pace (it was speed 20, acceleration 5). **Not yet seen on the robot.**

**November 21, run `c5de246d`: money news got no surprise.** Owner: "I tried to elicit a surprise
emotion, but it didn't fire." "Guess what? You want a million dollars", "Guess what?" and "You've got a
million dollars in your bank account today" all got plain-text bank-error jokes, so no expression
ran. The feeling note named only gifts and upgrades as a jolt. Claude now reads its own copy
(`claude_prompt.NOTES['showing_feeling']`; OpenAI's wording unchanged): big unexpected news is
`surprised` first even when the words doubt it or joke, with situations (money or a prize; a gift
or new parts; a twist or sudden big change, sympathetic when it is sad for them; "guess what" alone
is a tilt; the same news again is skeptical) and that speech-to-text hears "won" as "want". Probe
(`tools/check_gestures.py --live`, Claude Sonnet 5.5, simulated servos, one person, no history; 20
paid replies): before the "won"/"want" line, the bank-account line 2 of 2 surprised and "you want a
million dollars" 1 of 2 (the other skeptical); after it, 4 of 4. Lottery, new wheels, and a
friend moving abroad: surprised 6 of 6; "Guess what?" alone: tilt 2 of 2; a sum and small talk stayed
plain 4 of 4. A long evening's history may still dull it; **not yet seen live**.

### Unexplained: the hero elbow chattered in the drive stance (run `c36ecba3`)

Owner: after Rex tucked his arms to drive on his own (and never moved, on thick carpet) the hero
arm's elbow flickered 4–5 times a second until shutdown. The log: `base_park` at 4249.2 sent the
elbow (channel 4) to 7424, the top of its range and the Maestro's stored limit, with the other
stance channels; nothing was sent to channel 4 again until `base_release` at 4265.8 (it is held
against idle and talking in the stance). Meanwhile the approach looked for the person four times
with the head, travelled 0.0 m and ended `lost`. The microphone, which rides on that arm, read between
−26 and −43 dBFS through those 16 s (−59 dBFS just before the tuck) and three motor-noise "Oh."
captures were thrown away. So the host commanded one steady position and the servo did not hold it
steadily. Not established: why (pressed against its end stop at the limit, supply sag, or the
servo); there is no servo current or voltage reading. A hand test at 1856 µs from the servo menu,
and a little below it, would tell.

## November 15: he wakes with the visor shut

Owner: "the visor stays down over the eyes, and only open the visor when he says his TTS ready
lines"; later the same day: "the visor goes up right before the ready lines". The startup
choreography sends the visor to closed (`VISOR_DOWN`) instead of open, and `RobotServos.start`
holds channel 3, so the please-wait line's talking motion moves the head and arms but not the visor.
Once everything has loaded and the ready line's audio is in hand, `App` calls
`RobotServos.open_eyes`: the hold ends and the visor goes up to its usual open point (board speed
20, about a second), and then the ready line (or a hardware notice before it) is spoken with its
ordinary talking motion. If that move is refused the hold is gone all the same, so the line's
talking motion opens the visor; a failed startup leaves it shut. His eyes see nothing until then
(the camera runs but looks at the inside of the visor). First live start (run `c36ecba3`, the
earlier version that opened it during the line): the wake left channel 3 alone and the line opened
it. The before-the-line order is covered at the robot level by synthetic checks; the `App` call
order itself is **not yet run on the robot**.

November 20: the please-wait line is retired and the models load during the wake
([startup](STARTUP_HEALTH.md)). The visor still stays shut from the wake until everything has loaded,
then goes up just before the ready line, which is now one of the owner's fifteen new lines.

## November 15: head-lift normal height, surprise rise and pace

Owner: the head lift's normal height is 1093 µs (`HEAD_LIFT_NORMAL` 4372; was the range's midpoint,
1664 µs). Startup, idle, talking, driving, sentry and `look_level` stand there; `look_down`/`look_up`
stay 400/600 pulses below/above it. The model is told `head_lift` 16 % is the normal height (was 50),
and the servo menu's neutral follows. The range (832–2496 µs) and the park at its low end are unchanged.

The `surprised` expression now also raises the lift to 2310 µs (`HEAD_LIFT_SURPRISE` 9240) from
wherever it stands, at board speed 45 / acceleration 8 (about 1.5 s from the normal height). The
visor and the tipped-up head hold until the lift is up, all hold 0.8 s, and the lift comes back at
its usual pace (about 2.3 s); `subtle` goes half as far. Like every expression it is refused while
a channel it moves is held (a person's `neck_up`/`neck_down` now included).

Owner: "increase that servo's speed slightly". The lift's own pace is board speed 25 (was 20,
`HEAD_LIFT_SPEED`), acceleration 5 as before. It applies wherever the lift moves in a lane of its
own: `neck_up`/`neck_down`, the lift part of `look_up`/`look_down`/`look_level`, a `head_lift` joint
request (now always its own lane, beside whatever else the request moves) and the way down from a
surprise. Startup/park (12), the drive stance (20, shared with the neck and head pair), and the
eased idle and talking sways are unchanged.

The Maestro's stored home for channel 1 is not in this repository: the servo menu's "Restart
Pololu" sends the lift to that home, not to 1093 µs, until the owner changes it in Maestro Control
Center. Synthetic checks only (simulated board); the height, the 1.5 s rise and how the surprise
looks are **not yet seen on the robot**.

## November 14: the reworked throttle shoulder

Owner, after reworking the throttle arm's shoulder (channel 8): lower limit 2000 µs, straight down
1850 µs (the lower rest), 45° up 1240 µs, upper limit 906 µs (`LIMITS[8]` 3624–8000; was
544–2272 µs). Low pulses still raise it. Park, `throttle_down` and the relaxed rest stand straight
down (`SHOULDER_DOWN`, 7400), not at the limit; the awake rest (`REST`) is the 45° point
(`SHOULDER_45`, 4960); `throttle_offer`, `throttle_high_five` and the raised steps of the park
route use the new top (`RAISED`, 3624). Only `set_robot_joints` (100 is the 2000 µs limit) and the
servo menu reach past straight down.

There were no old angle readings, so the owner chose to scale every other shoulder position, and
the clearance rule's shoulder thresholds, to the same place in the swing, between old fully up
544 → 906, old awake rest 1050 → 1240 and old fully down 2272 → 1850 µs: tuck 6544 → 6132,
talking 3360/4000 → 4404/4828, idle 3920–4480 → 4776–5100, the startup route's 4800/3680 →
5260/4616, thresholds 5540/6546/6810 → 5629/6134/6263. The elbow and wrist are unchanged.

Unseen: whether the scaled poses look right and the clearance rule still keeps the reworked arm
clear. The Maestro's stored range and home for channel 8 must lie within 906–2000 µs: targets
outside the range never arrive, and the servo menu's "Restart Pololu" sends every channel to its
stored home. Not checked against the board. Synthetic checks only; not yet run on the robot.

## November 12: a new head-lift range, park and normal height

Owner: the head lift (channel 1) runs 832–2496 µs (`LIMITS[1]` 3328–9984; was 656–1936 µs).
Startup begins from, and shutdown parks at, the new low (`HEAD_PARK`, 3328). The normal height
(`HEAD_LIFT_NORMAL`, the midpoint, 6656 = 1664 µs; was 3600) is where startup, idle, talking,
driving and `look_level` stand; `look_down`/`look_up` keep their 400/600 pulses below/above it.
`neck_up`/`neck_down` and `set_robot_joints` percentages follow the new range (the model is told
50 is the normal height). The servo menu bar app shows the same range. The Maestro's stored range
for channel 1 must allow 832–2496 µs or targets outside it never arrive (`ServoArrivalTimeout`);
not checked against the board. Synthetic checks only; not yet run on the robot.

## October 31: a person's head pose lets go, and an arm dance

Run `f6d5a0b7`. A person's `look_*`, `tilt_*`, `look_level`, `neck_*` or head joint request put the head
channels in `held_channels` until `release_poses`, so talking and idle never moved the head again on their own.
`ServoOwner.hold_look` now records those channels in `look_channels` with at least `LOOK_HOLD_SECONDS` (20 s) on
`look_hold_until`, which the session extends while he answers; once it passes, `release_expired_look` (each frame,
`RobotServos.before_frame`) drops them and the idle gaze returns to people. Drive looks and the drive stance are
unchanged, and arm and visor holds still wait for `release_poses`. `RobotServos.keep_view` holds the head where
it points while he answers "what are you looking at?".

`arm_dance` (a one-shot `GESTURES` step) runs three lanes: the hero elbow and wrist swing between pride mode's
`FLAIR_ARM` poses seven times at 0.5 s; the throttle takes its clearance route to `throttle_high_five`, pumps four
times between it and `THROTTLE_IDLE[1]` at 0.8 s, and routes back; the poker swing, elbow and wrist alternate two
high/low poses inside the elbow ceiling at 0.7 s, routed in and out. Every arm ends where it started; channel 7
does not move. From rest the throttle shoulder never goes below rest (4960 since November 14, was 4200) nor the elbow below 7720. Synthetic
only (simulated board); physical clearance of these moves is unseen.

## October 26: a send that "times out" no longer freezes every servo

Run `c5ad0deb` (October 25, 7:32 PM): five minutes in, every servo froze until shutdown and the
park at "shut down" was refused (`ServoParkUnavailableAfterFault`). An idle move's send raised
`SerialTimeoutException` at the moment the impression voice was loading; the microphone lost
0.43 s at the same instant. The voice load held the interpreter 0.4 s (see
[impressions](IMPERSONATION.md)), and pyserial 3.5 checks its 100 ms write deadline after the
bytes have gone out, so a stalled thread reports a timeout for a send the board received. The
same happened October 17 (`2b044e51`) as the voice unloaded at shutdown. Afterwards the board
answered in 0.3 ms with no error flags, still holding the run's last pulses (no reset or power
loss); the operator park then completed.

- A send timeout or a short reply (`LINK_HICCUPS`) now asks the board for every channel (three
  tries, 0.2 s apart). If it answers, the moving joints are held where it says they are and Rex
  carries on: awake moves stay `active`; a startup, park or shutdown is left `stopped`, so a
  fresh park still runs; nothing sent leaves the earlier state. Nothing is resent. The receipt
  says `SerialTimeoutException:BoardAnswered`, or `:NoAnswer` when the board is gone, which
  faults as before. This covers requests, the arm return, the talking visor and the microphone
  recentre.
- A position query that fails part-way drops any late reply (50 ms, then the input buffer is
  cleared) before another thread may use the wire; otherwise the next query reads that reply
  as its own (checked: the head lift read as the neck's 5472).

Synthetic checks (simulated board) only; not yet run on the robot.

## October 21 (last): a smaller poker sway while talking

Owner: the poker arm moved too much while he talked. Its talking sway (full idle size at 0.6
of the idle timing, back to back) is now about a third of the idle size (`TALK_TAIL_REACH`
0.35, `poker_tail(reach=)`), at the idle pace, with a 0.4-1.2 s pause after each half sway.
Idle and the after-speech sway are unchanged. Synthetic only.

## October 21 (later still): the listening cue eases in, varies and stays on one side

Owner, on the robot: while he is spoken to the head tilts left and right, not smoothly, so the
whole picture wobbles, and it repeats itself. That is the listening cue (October 11): on each
trusted transcript the two head servos snapped to a 0.35 cock at board speed 35, the same
every time, and each new cue cocked away from the lean the last one left, so a person's run of
sentences swung the head across and back.

- `listening_lanes` now eases the cock and its return on the head's minimum-jerk curve
  (`LISTEN_EASE`: 0.6 s + 0.05 s per degree, about 0.8-1 s; both servos arrive together, board
  speeds of 12 or less), with a different amount (0.2-0.35) and dip (0.03-0.08) each time.
- A head already leaning (more than 0.1) cocks on that same side; a straight head picks either.
  The 3 s hold, the stop-where-it-is when the person speaks during the hold, and the gating are
  unchanged. Small cocks no longer count as a new camera view (`head_reframes`), as in idle.

A flaky check from the previous change (which channels a cancelled talking cycle holds) now
records the hold itself instead of reading writes after the cancel. Synthetic only.

## October 21 (late night): the throttle keeps going, a gentler wrist, a fully relaxed shoulder

Owner, on the robot: the throttle arm still stopped the moment he stopped speaking, its wrist was
too fast, and at rest the shoulder should relax fully while the elbow stays partway.

- *No stop:* the talking throttle paused 1 s at each pose (now 0.2-0.5 s), so a line often ended
  with it already still, and idle gave it a new move only 1.5 s later, after the first idle phrase.
  Now `after_speech` puts it in that first phrase (which may take four parts while energy is high):
  it finishes the talking move at the board speed and acceleration talking left it (the servo
  owner keeps `profiles_sent`), then goes straight on with no pause.
- *Wrist:* during talking and idle the throttle wrist (channel 10) is capped at board speed 12 and
  acceleration 5 (`Beat.wrist_speed`), after the move is timed, so the shoulder and elbow keep
  their pace and the wrist may arrive after them. Talking had it at about 27.
- *Rest:* `THROTTLE_RELAXED` is now the shoulder fully down (9088) with the elbow partway (8400 or
  8000). Fully down, the clearance rule requires the wrist folded (2048), so these are reached and
  left through the tuck. `throttle_route` now plans every throttle move (named poses, idle,
  talking): direct when clear, else through rest and/or the tuck; talking from the rest rises
  through the tuck first.

Synthetic only (simulated servo board). How the run-on and the relaxed arm look, and the wrist's
new pace, are unseen until a run on the robot.

## October 21 (night): arms run on after speech, a resting throttle, a swaying poker arm

Owner, on the robot: the talking and idle head are much improved, but as a line ends the throttle
and hero arms freeze for seconds until idle moves them ("alive, then suddenly his arms freeze");
the throttle's stiff joints slip during slow idle glides; held half-raised, the big throttle arm
looks like someone straining; and the poker arm does nothing while he speaks.

- *Run on, then wind down:* a line that plays to its end (`OutputOwner` passes
  `stop(finished=True)`) no longer holds the arms and visor (`servos.COAST`): they run on to the
  targets already sent, with motion risk covering 2 s more; the head and microphone arm are held.
  Interrupted lines, poses and drives still hold everything. `IdleMotion.after_speech` then makes
  the hero arm, poker sway and visor due at once (the throttle 1.5 s later, after its run-on) with
  energy 1, fading as e^(-t/6 s): part rhythms are up to 70% shorter and arm moves up to 40%
  quicker at first, at idle pace again after about 20 s.
- *Throttle:* idle moves come in bouts of one to three (every 3-7 s, was 5-11 s), then the arm
  lowers to the halfway-down or tucked waypoint of the startup/park route (`THROTTLE_RELAXED`) and
  rests 10-25 s. After a line it makes one or two moves, then lowers. Idle moves take 1.3-2 s
  (were 2-3.2 s) at the talking pace, include talking's two poses, and no moving throttle joint
  goes slower than board speed 4 (`THROTTLE_MIN_SPEED`); the clearance rule already allows joints
  to progress independently. The `throttle_idle` profile is retired. Fully parked is not used:
  talking cannot start from it.
- *Poker arm while talking:* its cat's-tail sway at 0.6 of the idle timing, about 6 s per cycle,
  starting 0.2-0.5 s after the head; held or kept arms are left alone, and an arm outside the
  sway takes the staged route back first.

Synthetic only (simulated servo board, which arrives instantly). The run-on's look on the robot,
the throttle's resting poses, noise, and whether more arm motion stills him more often are unseen.

## October 21 (later): livelier idle, eased talking

Owner, after running the eased idle head on the robot: more fluid, but slower and hardly
looking around, and talking was rapid and robotic beside it, so the start of a reply jarred.

- *Idle:* moves take 0.35 s plus 0.035 s per degree (was 0.4 + 0.05); glances away from a face
  every 4-10 s (7-16) and 15-30 degrees (12-24); room looks every 3.5-7 s (6-13); drift up to
  about 5 degrees of neck (3.4), and ±300 pulses inside the face dead zone (±160). The head keeps
  drifting until the rest of the phrase finishes (it moved at most twice per phrase), never past it.
- *Talking:* the neck, lift, nod and cock are one lane on the same eased curve
  (`servo_poses.TALK_EASE`, 0.3 s + 0.025 s per degree: moves of 0.3-0.65 s, peak board speed
  up to 33, was a fixed 45). Each cycle plans about 6 s of moves to alternating sides of the
  addressee (3-7 degrees), a quarter of them back to the addressee, never the same three moves;
  offsets stay measured from the anchor. Elbow and wrist move together on a curve (were two
  lanes at speed 35). The visor, arm and throttle start 0.1-0.6 s after the head.
- *Idle hero arm* moves on the eased curve too; `_together` and the plain `Beat.seconds` pacing
  are retired. The throttle arm keeps V2's pacing; the poker tail already eased.

Synthetic only: 1,369 blocked-I/O checks pass (simulated servo board). How lively it reads, the noise and whether more motion
stills him more often (his own servo noise) are unseen until a run on the robot.

## October 21: idle head moves on one smooth curve

Owner, after a fluid robot-head demo (Microduck's idle look-around): Rex's random head motion
is not fluid. Every idle head move gave all four head servos the same board speed (10-18) and
acceleration 3 whatever their distance, so the head cocked quickly while the neck kept
swinging, and long moves ran at constant speed. Now (`servo_poses.eased_head_beat`):

- *One curve, one arrival:* a move takes 0.4 s plus 0.05 s per degree of neck, pitch and tilt
  together (a full side tilt is taken as 20 degrees, unmeasured). Each servo gets the whole-number
  board speed and acceleration whose ramp stays closest to a minimum-jerk curve over that time.
- *Measured on a model of the board's ramp* (servos travelling 500 pulses or more, 400 random
  moves): the gap between the first and last servo to arrive fell from a median 51% of the move
  to 4%, and time at constant speed from 43% to 28%. Short, slow moves stay partly at constant
  speed: the board's gentlest acceleration (1250 pulses/s²) is too steep for them.
- *Room looks:* each goes at least 12 degrees from the last one, and about one in four comes back
  to straight ahead. Speeds no longer come from the gaze code (`IdlePhrase.head_speed` retired).

Talking sway, poses, gestures and the listening cue are unchanged. Face-following, glances,
holds and drift keep their targets and timing; only how each move travels changed. Synthetic
only: 1,368 blocked-I/O checks pass (simulated servo board). The board's real ramp, the head's
weight and linkage, how it looks and how loud it is are unseen until a run on the robot.

## October 13 (later): sway, then rest

Owner, after seeing it: it moved too much, too constantly. The tail now alternates: a bout of
steady back and forth for 10-25 s (`TAIL_SWAY_SECONDS`, about 4-10 half sways), a last move
easing back to the awake pose itself (the wrist still trailing), then 15-40 s of stillness
(`TAIL_REST_SECONDS`) before the next bout. During a bout everything below still applies, and
face-following re-aims at its old pace whenever the tail rests. 1,135 blocked-I/O checks
pass (simulated servo board only). Not yet run on the robot.

## October 13: the poker arm sways like a cat's tail

Owner: the poker arm's idle should be steadier, almost like a cat's tail waving: not fast,
just more constant, with the wrist following the elbow in the opposite direction, trailing
it. It used to jump to a random spot every 3-7 s. Now (`servo_poses.poker_tail`):

- *Every phrase:* the arm is due in every idle phrase (it takes one of the three parts) and
  each move is half a sway, across to the other side of its awake pose (swing 6000, elbow
  6000, wrist 3768). Side to side every time, never back to the same side.
- *The shape:* the swing goes with the elbow (±300-600 and ±350-550); the wrist bends the
  other way (±500-800). All three stay inside the old idle range, under the elbow's
  clearance ceiling.
- *The trail:* the elbow and wrist still start in the same command, because the clearance
  check needs them together. The wrist eases in at a third of the elbow's acceleration and
  arrives 0.5-0.8 s after it, so the hand picks up speed late and follows through. Each half
  sway takes 1.7-2.3 s at board speed 8 or less (new per-joint `Beat.profiles`).
- *No stopping:* when another part in the same phrase takes longer (the hero arm's reach, up
  to about 8 s), the tail adds more half sways until it is about done. A held or tucked poker
  arm does not sway. An arm that starts where rising into the sway could meet the wrist takes
  the staged route back to the awake pose first.

Phrases now last as long as a half sway (about 2.2-3.1 s), so the head's face-following
re-aims about every 2.5-3 s rather than every 1.2-2.8 s; it keeps drifting in between. 1,134
blocked-I/O checks pass (simulated servo board, which arrives instantly). How it looks, the
real acceleration curve, the pause at each end of a sway, its noise and servo heat under
constant motion are unseen until a run on the robot.

## October 12 (later): livelier, more continuous idle

Owner: idle looked frozen with a small move now and then; talking is vivacious; idle should sit
in between. Idle now runs at board speeds up to 18 (was 5-12; talking uses 20-45):

- *Cadence:* hero arm every 2.5-6 s (was 5-12), poker arm 3-7 s (6-15), throttle 5-11 s (9-20),
  visor 4-9 s (6-14). Parts in one burst start 0.2-0.8 s apart (0.4-1.3), and the pause between
  bursts is 0.15-0.35 s (0.3-0.7; the microphone hold still renews in it).
- *Size and pace:* arm moves are about 40% larger and take 1.1-2.2 s (1.6-3.2); the throttle arm
  2-3.2 s (3-4.6). The hero shoulder swing (microphone array) keeps its size, pace and 30-60 s rhythm.
- *Head:* when it is not re-aiming, it wanders about ±3.5 degrees of neck, with a small nod and
  lean, at speed 10-14 (was ±2 degrees at 5-9). While following a face it stays in the dead zone
  with a little more life (±2 degrees of neck, a small nod and lean). When a burst also moves
  an arm or the visor, the head keeps wandering until they finish instead of stopping to wait.
  All of these stay inside the camera's "same view" limits, so they do not reset scene settling.

Synthetic only: 1,113 blocked-I/O checks pass, but the simulated board arrives instantly, so
how continuous it looks, how loud it is, and whether his own servo noise now stills him more
often must be judged on the robot. In the October 12 live session idle ran only 2-8 bursts per
30 s, many cut short by speech in the room; this change does not alter that gating.

## October 12: idle arms move as a whole

Owner: in idle life the arm joints moved one at a time, while a real arm moves several joints
at once; two arms moving together is fine, but an arm that moves should move all its joints a
little. Idle life (`idle_motion.py`, `servo_poses.idle_lanes`) now treats each arm as one part:

- *Hero arm* (every 5-12 s): elbow and wrist move together, every time. Its shoulder carries
  the microphone array and each swing pauses voice learning, so it still joins only every
  30-60 s (`SWING_SECONDS`, not while he listens or works, nor while a person holds his arms);
  then the whole arm reaches out, pauses and comes back with the array at centre.
- *Poker arm* (6-15 s): swing, elbow and wrist in one move. Inside its idle range the elbow
  never nears its clearance ceiling, so the staged one-joint-at-a-time route is used only
  when an arm starts outside that range.
- *Throttle arm*: already moved its three joints together; unchanged.

Within an arm all joints start at the same moment and are paced to arrive together (new
`Beat.seconds`). Board speeds are whole numbers, so each joint's travel is adjusted slightly to
a whole speed over the beat's time; over 400 random moves every joint moved at least 40 µs and
all finished within 0.01 s of each other. Moves take 1.6-3.2 s at board speed 7 or less. A
held elbow or wrist now keeps that whole arm still (no lone wrist wiggle). 1,110 blocked-I/O
checks pass (synthetic, simulated servo board); how it looks and sounds on the robot is unseen.

## October 11: the listening cue (head cock while he thinks)

Owner: show that Rex heard you during the wait before his answer, with a head cock at the end
of transcription. When a trusted transcript comes back (`VoiceRuntime._asr_loop`, once per
capture), `RobotServos.heard` steps calm idle movement aside and the voice-reflex worker runs
a `listening` request (`servo_poses.listening_lanes`): the two head servos cock to one side
(tilt 0.35, a touch down) at speed 35, hold for 3 s and return to where the head was. The side
is random, or away from a lean the head already has. Neck, lift and the array arm do not move.
Untrusted transcripts (his own motors heard as "Oh."), stop and shutdown commands give no cue.
It is skipped (`listening_cue_skipped`) while he is already talking or a person's held pose or
the drive stance holds the head. Talking, a pose, a gesture or a drive stops it first, like the
voice turn; talking sway then straightens the head as the reply plays. The cue arrives about
2 s before first audio (ASR about 0.5 s after the end of speech, model and voice about 2.5 s
more). No setting. 1,109 blocked-I/O checks pass (synthetic, simulated servo board); how it looks, how loud the
head servos are, and whether it still fires during a reply that streams quickly are unseen on
the robot.

October 20 (`515d71db`): people answered during the 3 s hold, so 41 of 87 longer clips counted
as heard while Rex moved; such clips never start a new voice, and the owner's clips during the
cue matched his voice at a median .39 (.57 still, across 15 runs). The hold with every servo at
its target is no longer motion (`QUIET_HOLD_KINDS`); speech during it stops the cue where it is
with no return beat and no settle tail (`VoiceReflex.settle` from `RobotServos.note_speech`, never
while the cock travels, whose whir can sound like speech); and no cue starts within 0.3 s of
speech. Synthetic checks only; how the frozen cock looks on the robot is unseen.

## October 15: one stuck joint no longer freezes every servo

Run `24279950`: after a 45° turn the throttle stayed tucked beside an obstacle. Seven seconds
later its space cleared and the late walk-out also sent the hero elbow (channel 4) to its awake
6720, while an idle phrase already moving that elbow left it at 6845. The walk-out waited its
25 s, timed out (`ServoArrivalTimeout:ch4@6845->6720`) and marked the owner faulted, so every
later servo request was refused (`ServoUnavailableOrParked`) until restart: no talking motion
or head turns, a refused "come here" drive (`ArmParkFailed`), and no park at shutdown. The
link was fine throughout. Two changes:

- A kept arm coming out later moves only its own channels, which are still held, so nothing
  else is commanding them; the hero elbow and arms already out stay with idle and talking.
  While it walks, only those channels are refused (`ServoBusy`), not every arm channel, so
  talking motion carries on.
- A joint that does not arrive during his awake motion (talking, idle, poses, looks,
  gestures, drive park and release, the walk-out) is held where it stopped and the owner stays
  active. A failed hold or a broken link still faults, as do startup and shutdown timeouts.
  A slow or stopped microphone-arm recentre now skips that hold instead of faulting.

Synthetic checks (simulated servo board) reproduce the October 15 collision and the stuck
elbow; not yet run on the robot.

## October 11: arms stay tucked beside obstacles

Owner: the poker arm reaches about 2 ft out from the left side sensors and the throttle arm
about 2 ft ahead of the front sensors; after a drive, neither should come out into something.
When the wheels stop, the drive owner reads the ring (`drive_space.arms_blocked`): the poker
arm (swing 6, elbow/wrist 12/13) stays at its drive stance (8000, `POKER_PARK`) while the
nearer left side sensor reads under 1.07 m (3.5 ft; 0.7 m since November 21), and the throttle stays at `PARK` while
the nearest front reading (ring radials and depth matrix) is under 0.61 m (2 ft). No reading
counts as blocked. `return_arm(keep=...)` walks only the other arms back; the kept arms'
channels stay held (`kept_arms`), so idle and talking skip them, an aged hold or
`release_poses` does not free them, and any pose for them is refused as
`ArmKeptClearOfObstacle`. Each second the drive owner rechecks; once an arm's space has read
clear for 2 s it walks to its awake pose (not while driving, and not while a person's
`hold_arms` stands). The next drive's park takes kept arms as usual. The reply model sees
`robot_pose_state.arms_tucked_for_obstacle`. Not covered: an arm already out when someone
steps up beside him is not pulled in, and requested poses with no drive before them are not
gated. Startup has been gated since November 17 (below). Synthetic checks only (simulated base
and servo board).

## October 10: two-servo head, poker elbow and wrist, inverted visor

The owner reworked the head and the poker arm and measured the new positions in the
Pololu Maestro Control Center (microseconds; the code uses quarter-microseconds, x4).
The measured extremes are the calibrated limits. **Not yet run on the robot.**

**Head.** The single head-tilt servo is gone. Two servos in the head face each other,
each with a linkage onto one centre post: `head_left` (channel 2, the old channel) and
`head_right` (channel 11). Driven against each other they pitch the head; driven the same
way they shift the post sideways and cock the head to one side (called *tilt* below; the
owner also called it yaw). Readings (left / right, µs):

| pose | left | right |
|---|---|---|
| level | 1458 | 1620 |
| level, fully down | 1013 | 1904 |
| level, fully up | 2032 | 1312 |
| down, tilted right | 1008 | 1702 |
| down, tilted left | 1350 | 1849 |
| up, tilted left | 2032 | 1587 |
| up, tilted right | 1659 | 1312 |

`servo_poses.head_pulses(pitch, tilt)` turns a pitch (-1 fully down .. 1 fully up) and a
tilt (-1 cocked to Rex's left .. 1 to his right) into the pair of pulses: pitch moves the
servos against each other along the measured level/up/down line (the travel is asymmetric),
and a full tilt shifts both the same way by about 275 µs in total, each within its limits,
the other taking what one cannot. Tilting right lowers both pulses. `head_pose` is the
inverse (a least-error fit); it reproduces all seven readings exactly. Every head command
sets both channels (`IncompleteHeadPose` otherwise), and a pair that no pitch/tilt
explains within 150 µs is refused as `HeadPairOutsideEnvelope`, so the two servos cannot
be driven into the corners the linkage never saw (for example both at their highest).
Where one servo sits at its limit the decomposition is ambiguous (fully down tilted right
also reads as a lesser pitch with more tilt); poses are still exact, only the reading of
them is not. Pitch in degrees still uses V2's 20 down / 25 up estimate, unverified here.

What uses it: the level drive stance and startup head are the owner's level reading; park
is fully down. `look_down`/`look_up` pitch by 0.8 and keep the tilt; new `tilt_left`,
`tilt_right` and `tilt_straight` steps cock the head by 0.8 and keep the pitch and neck;
`look_level` levels and straightens. `set_robot_joints` offers `head_pitch` and
`head_tilt` (0-100, 50 level/straight) instead of a raw servo. The talking sway nods
slightly with a slight cock to each side; idle glances away carry a small cock; room scans
a slight random one; face following moves the pitch and keeps the tilt. The `tilt`
gesture is now a real side cock (0.6, or 0.3 subtle) that straightens again, and `nod`
pitches both servos. The camera reframe rule counts both head servos.

*Expressions (October 10 evening; owner: "dogs cock their heads left and right when they're
perplexed", use the freedom for more expression).* `HEAD_EXPRESSIONS` adds `perplexed`
(cock 0.6 to one side, swing to the other, slightly down, 0.7 s each; eased since October 24),
`wiggle` (three
0.35 cocks side to side; since October 24 each swing eases in and out, about 3 s in all, see
below) and `sympathetic` (a slow 0.45 cock, slightly down, held
1.2 s). All return the head to where it was and hold nothing. They are offered to the
reply model as `respond_with_gesture` primitives (with guidance on when each fits and to
keep most lines plain) and, apart from the plain `tilt`, as `set_robot_pose` steps for
requests and Rex's own plans. The choice stays with the conversation model: no event is
mapped to an animation. Idle life carries a little more tilt: drift ±0.06, glances away
cock 0.1-0.25, room scans ±0.3, and while following a face the head settles into a gentle
0.15-0.3 cock for 3-8 s every 10-25 s, then straightens.

*The wiggle eases (October 24; owner: "too rapid and creaks", seen right before shutdown in
`aedd9ce6`).* It had three reversals in about 1.5 s (1.9 s measured live) at board speed 50 and
acceleration 10 on both head servos, the steepest head ramp of any expression. A
`HEAD_EXPRESSIONS` entry may now carry `ease` (least seconds, seconds per degree): each step
then moves on one smooth curve timed by its size, as idle and talking head moves do. The
wiggle uses (0.35 s, 0.03 s per degree) with 0.15 s pauses: half swings about 0.56 s, full
swings 0.77 s, 3.1 s in all (2.5 s subtle); the board ramp is acceleration 2 and speed at most
10, under half the old peak speed. At the owner's request the `perplexed` double cock eases
the same way, keeping its 0.7 s holds: 3.9 s in all (was about 3.0 s; 3.2 s subtle), board
acceleration 1-3 (was 6) and speed at most 13 (was 25). Nod, tilt, sympathetic and surprised
are unchanged. Synthetic pulses only: how they look and whether the creak is gone are
**not yet checked on the robot**.

**Poker arm.** Channel 6 still swings the arm. New `poker_elbow` (12: 864 µs down, 1920
up) and `poker_wrist` (13: 496 down, 1440 straight, 2486 curled up and inward). The elbow
rises fully only with the wrist at or below 1564 µs; curled further in, the measured ceiling
falls (wrist 1894 → elbow 1807, 2136 → 1709, 2496 → 1597; linear between). A pair above
the ceiling is refused (`PokerElbowWristCollision`) before anything moves, and every
elbow/wrist move is staged one joint at a time (elbow down to the lower of the two, wrist,
elbow up) so the ceiling is never crossed on the way. Named poses from the readings:
`poker_out` (1057/1389), `poker_up` (1500/942, 45 degrees up with the hand level),
`poker_down` (elbow down, wrist straight). The drive stance adds elbow 1920 / wrist 1564
beside the raised arm. Elbow 1920 / wrist 1564 is also the arm's park (`POKER_PARK`): the
cold-start hold for its two channels, and where shutdown returns it in clearance stages.
Awake, the arm sits at `poker_up` (45 degrees up, hand level): startup takes it there from
the park in clearance stages beside the head and throttle lanes, and idle life wanders the
elbow and wrist slowly around that pose (owner, October 10 evening: after the wake the arm
stayed at its park and looked wrong). First live start,
October 10 evening: the code had assumed an elbow-down rest, so the arm, which stands at
its park when switched on, dropped at once and shutdown left it down; corrected the same
day.

**Visor.** The servo now runs inverted: low pulses raise it, high pulses lower it, within
the same 4544-6976 travel. Owner's readings, October 11: the neutral open point is 1433 µs
(5732; startup) and the surprise position is 1136 µs (4544, the channel's
low limit, the widest it opens). Closed and park stay 1744 µs (6976), reached on the robot.
Speaking (5492-6132) and idle (5382-6082, half-blink to 6132) keep the September offsets
around the open neutral, mirrored; the old 400-pulse closing excursion partly hid the camera
then, so check the camera view at 6132. The new `surprised` expression snaps the visor to
the surprise position with unlimited acceleration while the head lifts 0.15, holds 0.8 s,
and eases both back; it is a reply-model gesture and a `set_robot_pose` step. The first
mirrored guess (open 5120) was replaced by these readings. October 22 (run 1072c10a): `visor_up` went to the open neutral, inside
the range the visor already wanders while he talks and idles, so "raise your visor all the way"
moved nothing visible. It now holds 1150 µs (4600), fully open and a little short of the surprise
limit so the held servo is not pressed against its stop. Shut by a person, the visor stays shut when
their hold ages out (idle motion would otherwise drift it open), and after 15 s with nothing newer
asked Rex may raise it himself (`ServoOwner.may_raise_visor`, `VISOR_RAISE_SECONDS`); other holds
still yield after 90 s. `visor_normal` returns it to the open neutral and releases it to speech and
idle motion (run 0421a222: asked for "the way it normally goes", it stayed fully open). Not yet checked on the robot.

**First live run (session `f07e53af`, October 10 evening).** The wake choreography,
`tilt_left`, `tilt_right`, `look_level`, `look_up`, `look_down` and `poker_up` all reached
their pulses on the robot. Three findings:

- The poker arm dropped at startup and stayed down at shutdown: the cold-start hold had
  assumed an elbow-down rest while the arm stands at its park when switched on. Fixed above.
- "Can you look left?" moved the head to the right. Rex was already following Bret at the
  neck's left limit (the voice reflex aimed at 1984 twice; he stood 55-65 degrees left), and
  `look_left` was a fixed 33-degree pose, so it swung back toward centre; "turn your head
  left" then found him already there and moved nothing. Side looks are now relative: from
  nearer the centre they go to the 33-degree pose; already there or beyond, a person's
  request goes on to the neck's limit on that side, and Rex's own repeated step moves
  nothing (`neck_target`; `already_at` reports a side look at or beyond its pose).
- Shutdown failed with `ServoArrivalTimeout` on the head/visor lane after 25 s; the throttle
  parked. The trace did not say which channel never arrived. A timeout now names each
  channel still short of its target with its pulse (for example `ch3@6400->6976`). The
  likely cause is the Maestro's own stored channel range clamping a target it has never been
  sent before: the visor park 6976 (1744 µs) is the only new lifecycle value outside anything
  the September code commanded; the head pair's park (1013/1904 µs) is the owner's reading.
  Confirm from the next shutdown's reason, then either widen the board's range in the
  Maestro Control Center or lower the park pulse to the board's limit.

**Second live run (sessions `6b8fa000` and `b954c14c`, October 10 afternoon).** Two more
findings, both fixed the same evening (pulse simulator only):

- **The poker arm did not come back after driving.** The drive stance raised the arm (8000),
  put its elbow up and wrist at 1564 µs, and when the wheels stopped only the throttle arm
  walked back; the hero elbow and the poker channels were merely let go, and idle life
  never carried them back (the next drive's park changed none of them). `return_arm` now
  walks every arm the stance moved: the throttle to rest, the hero elbow to its awake
  6720, the poker arm to mid swing (6000) and its elbow/wrist to `poker_up` by the staged
  clearance route, all beside one another and beside head motion, which is free at once.
  Those channels stay held until they are back; a request for them meanwhile is
  `ServoBusy`, as the throttle already was.
- **Shutdown timed out again, now named: `ch1@2624->2600`.** The head-lift park of 2600
  never arrives because the board's own range stops at 2624 (656 µs); the lift's minimum
  and park are 2624 now. The visor park reached 6976 this time.

**Also.** `IncompletePokerPose` mirrors the throttle's batch rule; held parts report
`head_pitch_and_tilt` and `poker_elbow_and_wrist`; `robot_pose_state.joint_percent` is
now by joint name with the head as pitch/tilt; the servo menu-bar console lists all 14
channels with the new limits and direction hints. Confirmed on the robot (owner, October
10 evening): `tilt_left` cocks the head to Rex's own left. Unverified: how far a 0.8 tilt
visibly cocks the head and whether the linkage binds anywhere inside the envelope, the poker arm's
clearance of the body and other arms, the visor's real endpoints, and whether the
motion reads as expressive. Synthetic checks only.

## Implemented

- `servo_poses.py` owns pulse limits and small lifecycle/talking sequences.
  `servos.py` owns execution and versioned, expiring requests; `adapters/maestro.py`
  encodes the compact protocol. No V2 runtime import, configuration load, global
  state, automatic reconnect, semantic router or additional model is used.
- Startup raises the head and opens the visor while the neck scans left/right
  (either order) and the throttle arm unfolds through its established waypoints.
  One executor advances independent lanes, rather than spawning competing writers.
- Shutdown/park concurrently droops the head, recenters the neck, closes the visor,
  lowers the elbow to 6300, and routes the throttle through tuck to park. It waits
  for output-pulse readback and settling. A completed park rejects subsequent
  animation requests; close itself sends no motion. Channels 5–7 stay where they
  are, matching V2 shutdown rather than the broader V2 test-only reset helper.
  V2's power-down sound starts with the park (October 10): the servo owner's park
  hook plays it through the speech sink after the LED fade command, the park never
  waits for it, and playback closes only after the clip finishes.
- The throttle always moves as a three-channel batch. Every route is checked
  before any lane moves and rechecked against fresh pulses before each target.
  The latest full-down retraction corridor is included. Lifecycle profiles retain
  the current V2 throttle pace. The directional full-down-to-high-five exception
  is not used: named high-five requests use the validated retraction bridge.
- The production `OutputOwner` accepts optional talking motion. Actual playback
  start launches its worker; normal completion, failure and cancellation stop it.
  TTS generation and queued PCM do not start movement. The worker shares the servo
  executor and output's motion reservation; callbacks never write serial. A stopped
  phrase holds current output pulses rather than issuing a late neutral/visor reset.
  Motion receipts and errors are recorded separately from speech delivery.
- Talking now includes neck/lift emphasis, a slow modest headtilt sway, a speaking
  visor lane, hero-arm
  elbow (channel 4), wrist (channel 5) and two V2 throttle poses. Independent lanes
  repeat while slower lanes finish; sent targets drain before a successful cycle
  receipt. Head anchors are captured after startup, preventing interrupted phrases
  from ratcheting the pose. Channel 7 stays fixed for the microphone hold. This
  preserves visible speaking movement with bounded beats, not V2's random per-frame,
  emotion-dependent or amplitude-driven body policy.
- Cancellation best-effort holds touched channels at fresh controller pulses;
  partial transport failure faults the owner without retry/replay. Requests have
  no deferred queue. Stop/join talking motion before submitting park. A busy owner
  rejects concurrent requests; callers must not treat rejection as a completed park.
- `servo_port` explicitly opts live startup into the robot provider. Wake finishes
  before capture/camera startup. Reverse cleanup stops core workers and capture,
  joins talking motion, parks, closes serial, then acknowledges handoff. SIGTERM
  uses graceful runtime shutdown. A failed closer retains ownership until process
  exit rather than telling companions it is safe to reopen.
- Every live run now takes the cross-version controller lock and the V3 microphone
  resource lease. Servo-enabled runs also acquire the Maestro lease and check both
  serial aliases with read-only `lsof`. The old V2 supervisor must be unloaded;
  its lock polling does not acknowledge actual microphone release. `doctor` remains
  inert and does not inspect jobs or open devices.
- Robot capture obtains bounded 30-second hero-arm holds at its calibrated midpoint
  (5984), with 1.5-second settling. Capture completion, cancellation and any servo
  sequence release the hold. Samples must be wholly covered to qualify for voice
  enrollment. Renewing never retroactively qualifies audio. A robot chain discards
  unheld pending sample references before collecting held ones; accepted profiles
  are unchanged. Motion and settling exclude speaker identification/enrollment even with isolated audio;
  transcription retains human speech through movement, with separate motion-risk evidence;
  servo movement also invalidates old direction samples.

## Calibration and evidence boundaries

All positions use Maestro quarter-microseconds. Source constants are an initial
robot-specific calibration, not an import of effective local/environment overrides.
The V2 animation still declares headlift floor 1984; its current configuration
raises the minimum to 2600 and clamps writes. V3 explicitly parks at **2600**.
Throttle bounds intersect source configuration with V2's measured board endpoints:
shoulder 2176–9088, elbow/wrist 2048–9984. Park is 9088/9984/2048. Since November 14 the reworked
shoulder runs 3624–8000 and parks straight down at 7400.

Maestro readback describes generated pulses, **not shaft feedback or obstruction
detection**. Receipts say `pulses_reached`, with the simulation/controller medium
and evidence type explicit. Timeouts cannot prove where the physical mechanism is.
A channel reading 0 is off since power-up (the robot was switched off). As in V2,
startup first holds each such channel at its unpowered rest pose (`UNPOWERED_REST`:
head park, arms at rest, throttle park) with bounded profiles, then runs the normal
wake choreography; the trace records `servo_unpowered_rest`. The board applies the
first pulses on its next 20 ms period, so startup polls (up to 1 s) until those
channels stop reading 0; V2 never re-read, V3 validates every move from readback. Live channels keep
their readings (a partly-off throttle arm is sent as one batch with its live joints
unchanged). A live reading outside the calibrated limits still rejects startup
before any write. No V2 park marker is copied or trusted. The Maestro jumps to the
first pulse after power-up, so a joint not hanging at rest snaps there, as in V2.
This replaced the manual `servo-commission` command (September 28), which blocked
every start after a power-off.

The serial opener uses exclusive access and 100-ms read/write timeouts, with no
discovery or reconnect. Optional operator dependencies are in `requirements-robot.txt`
and installed in V3's independent environment. The default servo port remains empty;
the prepared robot configuration is a separate ignored file. The existing
attention/gesture simulations remain simulations. October 1 adds separately started
physical adapters after successful robot startup, accepting only controller pulse
receipts. See [agency increment 2](AGENCY_IMPLEMENTATION_PLAN.md#increment-2-implementation--october-1-2026)
for finite trajectories, held-pose priority, cancellation and camera settling limits.

## October 3: continuous idle life and gaze

Supersedes the scheduling and head behaviour in the two September sections below (their
ranges and the single servo owner are kept). **Not yet run on the robot.**

Live run `30ec1b04` (10.4 minutes): the idle scheduler waited 4-9 quiet seconds, then one
request moved eight channels together. It ran 15 times; 5 were cut short by his own beats
and 6 by a phantom transcript of their own noise. The owner: "He sits motionless for a
while, then suddenly all the servos move at the same time", and asked for slow, measured
movement that is always going on and is not just back and forth.

**Idle life** (`idle_motion.py`, `servo_poses.idle_lanes`). Each part keeps its own rhythm:
head every 1-3 s, wrist 4-10 s, elbow 5-12 s, visor 6-14 s, poker arm 6-15 s, throttle arm
9-20 s, the microphone-carrying hero arm 30-60 s. A phrase moves the one to three parts
that are due, each starting 0.4-1.3 s after the last, then pauses 0.3-0.7 s. Every move
goes somewhere new (a bounded step from where the joint is, not a swing between two ends)
at Maestro speeds 5-18, a third to a half of the September idle; the visor sometimes does a
slow half-blink and the wrist sometimes rolls past and eases back. The visor stays in its
speaking range (6000-6750), above the camera. Hero-arm excursions are smaller (700-1400
from centre, was 1300-2000) and slower. No new servo writer: one request, one executor;
lanes may now start after a wait.

**Gaze.** Two modes, chosen by the conversation model:

- *Following people* (default). With a face in view the head moves half of the way toward
  centring it on each step (V2's centring gain 0.5 and dead zone), on the neck and the
  tilt, aiming to keep the face 40% of the way down the picture so the body is in view (in
  the live run the owner's face sat about 75% down, his body below the frame). Every 7-16 s
  it glances 12-24 degrees aside for 1.4-2.8 s and comes back, as V2's gaze engine did;
  the owner found V2's constant tracking creepy. With nobody in view it looks around the
  room every 6-13 s and drifts in between.
- *Holding a chosen direction.* After Rex's own `look_*` pose or silent look, the head
  stays there with small living movement for up to 30 s, or until the model uses the new
  `look_at_people` step, someone speaks (the voice reflex), or a drive ends. A look a
  person asked for is still a held pose (90 s against his own moves, until released).

`robot_pose_state.gaze` tells the model which mode is active. Talking sways around the
pitch the gaze chose instead of snapping back to level for each line.

**Camera and listening.** Head moves under about 5 degrees no longer count as a new camera
view, so scene settling, planner frames and arrival greetings are not reset by small
movement; larger looks still do. While Rex is transcribing or working on a reply, idle life
continues but stays calm: no view-changing head moves and no hero-arm swings. Confirmed
speech still stops idle movement at once.

**Microphone hold.** The hold that qualifies a capture for voice learning now tracks the
array arm (channel 7) only: other joints moving no longer release it, since with continuous
idle movement it would never have been valid. Their noise still marks the capture
(`motion_risk`) and keeps it out of learning. A hero-arm move ends the hold and a new one
waits 1.5 s after the arm stops.

**His own noise.** See [core operations](CORE_OPERATIONS.md) and `runtime.py`: a sound that
starts while his servos move is suspected self-noise until it has 0.25 s of continuous
voiced audio or 0.4 s within a second. Until then it does not stop idle movement, yield a
beat or hold up his initiative. The capture is still transcribed; a trusted transcript of
an unconfirmed capture is marked untrusted unless recognized clearly (average log
probability at least -0.1) or it is a stop or shutdown command. A suspected capture open
3.5 s gets a still moment so it can end.

Evidence: 935 blocked-I/O checks, with fake pulses and clocks covering staggered starts,
speed and range bounds, small versus view-changing head moves, face following with dead
zone, glances and stale-frame rejection, room scanning, held directions, calm mode, the
array-arm hold and the self-noise rules. In a simulated ten minutes the scheduler started
464 phrases with at most 3.7 s between starts; real moves take longer, so the robot will
run fewer. **Unverified on the robot:** how the motion looks, whether following is
comfortable, whether slow moves are quiet enough for the microphone, vertical aim (the
tilt scale is V2's estimate), clearance of the hero wrist (4600-7400, just beyond its talking
range) and poker arm while the throttle arm moves, servo temperature under continuous use, and voice-learning coverage.

## October 3: the head is free the moment a drive ends

Owner: after driving, "the head and other servos wait for the arm to un-park before he
turns his head or does any other motion". The un-park was one servo request of about five
seconds, and the owner runs one request at a time, so idle gaze, talking sway and gestures
were refused (`ServoBusy`) and the drive stance stayed held until it finished.

Now `begin_arm_return` releases the neck and head the instant the wheels stop, and
`return_arm` walks the throttle arm back to rest outside the single-request lock, using
the same staged route and clearance checks (since October 10 the hero elbow and poker
arm, elbow and wrist walk back beside it). Only those arm channels stay held; a request
that would command them, and park or shutdown, are refused until they are back (the next
drive and shutdown already wait for it). The Maestro
adapter takes each wire exchange whole so the two cannot interleave. A stop holds the arm
where it is. Checked with the pulse simulator only (four new checks, one with head moves
running in another thread during the return); on the robot, confirm the head resumes
following at once and the arm return is unchanged.

Second run the same day (`aa6782c0`): the owner still saw everything wait for the arm. The
log shows the servos were free, but idle movement waited about two seconds to resume (a
one-second after-drive hold plus its usual pause) and stayed still whenever he was
listening or answering, which was most moments after a drive: once nothing moved for the
whole five-second return because the owner was speaking. `IdleMotion.wake` now starts a
head-led phrase the instant the wheels stop and keeps idle movement going for six seconds
regardless of listening or working (the arm is moving then anyway); it never competes with
talking motion, which already moves the head. Two new pulse-simulator checks.

## Idle arms and elbow lifecycle

The user requested random hero/poker arm movement while awake. A low-priority
`IdleMotion` scheduler now waits a random 4–9 seconds of eligible quiet, then submits
one finite request through the existing ServoOwner. V2's broad hero excursion range
(1300–2000 quarter-microseconds from center) and quieter poker range (800–1500) are
retained, clamped to calibrated bounds. Positions cross center when an arm is already
offset. No new service, device owner, model or configuration switch is introduced.

Idle moves yield to VAD activity, an active capture, queued recognition/conversation,
model/output work and shutdown. Talking joins idle before its first target; shutdown
joins both workers before park. An idle request cannot queue behind speech or restart
after lifecycle cancellation. The normal hero excursion returns channel 7 to 5984;
cancellation holds current pulses, and the ordinary microphone hold recenters later.
Recentring does not begin mid-capture or while a reply is being generated/played.

September 27 (owner): each idle request also moves the head. With a face in view it
looks at the largest face three times in four (small jitter) and glances elsewhere
otherwise; with none it looks within ±35° of centre and up/down around the level drive
pose, biased downward as in V2's room scan. A head look marks the camera as re-framed
so the same people are not treated as arrivals. Head channels are held level and
centred while the base drives. Synthetic only; live cadence and feel are unverified.
Idle motion invalidates direction/hold evidence and retains existing motion-noise
exclusion. Input overlapping motion/settling may be rejected or lack enrollment
permission; validate the resulting listening gaps and movement feel on the robot.

Startup now raises the hero-arm elbow (channel 4) to V2's 6720 neutral alongside the
slow head wake. Shutdown already targets its 6300 gravity rest; the new startup rise
makes that droop visible even if Rex never spoke. The slower lifecycle profiles are
retained.

**561 blocked-I/O checks pass**, including six new idle/elbow contracts using fake
pulses/clocks: random bounds/return, startup-to-shutdown elbow, quiet scheduling,
speech preemption, hold invalidation/settling and shutdown latching. No additional
hardware run was performed. Live range/clearance, servo noise, interruption response,
voice-learning coverage, latency and CPU cost remain acceptance checks.

## Next integration gates

1. Perform the prepared [operator-tools cutover](OPERATOR_TOOLS_MIGRATION.md) with
   the owner present. V3's supervisor and console are implemented; installed jobs
   remain V2. Verify manual/supervised startup and actual microphone/serial handoff.
2. Commission the source calibration and cold-start pose evidence. Verify
   finite serial timeouts, manual takeover, interrupted park and transport faults.
3. Validate live talking motion, motion-audio exclusion, direction invalidation and
   held-microphone enrollment. These are implemented contracts, not yet measured
   mechanical/acoustic guarantees. Quiet interruptions can extend exclusion while
   the hold reacquires and settles; measure this conversational cost.
4. Validate the implemented finite model-selected attention/gestures through the
   same owner, with truthful pulse receipts and settled post-motion vision. Adapt richer speech expression
   from actual audio timing; avoid independent mood/idle motion authorities.

## September 25 live-feedback repair

The owner reported that the hero-arm elbow/wrist and head did not visibly move
while talking. The live trace showed the talking worker executing and being
cancelled at playback completion; this is the normal stop mechanism, not evidence
of a rejected start. Inspection found that the first adaptation never targeted
channels 4/5 or the neck, and its tiny headlift cycle waited for the slow throttle
lane before repeating. The repaired sequence restores those channels and lets
shorter lanes repeat independently. No V2 source or running service changed for
this repair, and no additional hardware test was operated by the agent.

**555 blocked-I/O checks pass** after this fix. Three added contracts cover repeated
head/elbow/wrist beats, drain completion with delayed fake pulse arrival, and bounds;
existing tests now require all five expressive head/arm channels through the actual
OutputOwner/TalkingMotion path and check interrupted head anchors. Live confirmation
of visibility, comfortable pace and acoustic impact remains pending.

### Speaking visor addition

At the owner's request, speaking now includes a separate visor lane between 6000
and 6640 quarter-microseconds, based on V2's 6000 speech floor. It repeats alongside
the existing gentle headtilt sway (startup anchor ±120), neck turns, elbow/wrist and
throttle. Neck turns retain their accepted pattern. Production playback coverage
now requires channels 0–5 plus throttle, with cancellation and delayed arrival
checks covering the additional lane. The 555-check blocked-I/O suite passes; live
visor visibility/camera occlusion and motion feel remain to be judged on the robot.

### Lifecycle pace adjustment after live feedback

The owner found startup/shutdown head movement much faster than V2. V2 combines
Maestro caps with software-stepped targets; V3 sends final targets directly. The
startup head/visor and neck sweep, and shutdown/park head/visor/elbow lane, now use
speed 12 and acceleration 3 instead of startup 30/28 with acceleration 8 and
shutdown 70/14. Positions and dwell times are retained. These lower controller
caps produce a slower glide without adding a second interpolation writer. This is
a first live-feedback tuning pass, not measured timing parity with V2; the owner
still needs to judge the physical pace.

## Verification (initial servo/operator checkpoint)

`.venv/bin/python tools/run_checks.py`: **528 checks passed**, including 48 new
servo/output, handoff, operator and robot-enrollment contracts across both slices.
`git diff --check` and dependency consistency checks passed.

The blocked-I/O suite exercises synthetic pulse readback, concurrent lifecycle lanes,
atomic throttle targets, profile application, stale requests, timeout, cancellation,
transport failures, full-down retraction, park latching and actual-playback hooks.
It cannot establish mechanical clearance, motion feel, audio contamination, physical
settling, obstruction handling or live console handoff. No hardware,
hosted provider or personal database was used. Live latency, median/tail movement
timing, combined-load memory and playback impact remain unmeasured.

Separate real-model check: the independently copied V2 wake classifier and its two
feature models processed 150 synthetic-silence frames (12 seconds) without opening
audio. One dev-Mac run measured 17.54 s model/import startup, 1.85 ms median and
2.19 ms p95 inference per 80-ms frame, and 190,627,840 bytes (~182 MiB) process peak
RSS. Maximum silence score was 0.000763, below the retained 0.92/four-frame trigger.
This is overhead/silence evidence, not positive wake accuracy or a live-M2 benchmark.

## September 25: model-selected held poses and capture repair

October 3: a held pose blocks Rex's own (self-initiated) poses and drives for 90 seconds
(`HOLD_YIELD_SECONDS`), then yields to his initiative. Before that, one "look right" in
conversation refused all of his own movement until someone asked for a release. Requests
from people, talking/idle lanes and `release_poses` are unchanged.

`set_robot_pose` is available for human turns when the live robot owner is active.
CoreState validates the named command and revision; OutputOwner reserves movement,
stops idle/talking, and delegates to the same versioned ServoOwner. There are no
raw model pulse targets or additional model calls. Commands are `visor_down`,
`visor_up`, `throttle_offer` (hold out the hand), `throttle_high_five`,
`throttle_down`, `throttle_rest`, and `release_poses` (resume normal movement).
Requested joints remain held through subsequent speech; other speaking and idle
lanes continue. Release routes the throttle to rest before re-enabling its speech
lane. Shutdown overrides held poses and parks normally. Cancellation holds current
pulses; a failed action cannot speak its proposed success acknowledgment.

V2's offer/high-five/rest/down targets are retained. Full-down transitions use
shoulder-only raising and established retraction waypoints, with all stages checked
before execution; V2's special coordinated direct high-five exception is not used.
Readback establishes controller pulse arrival, never observed shaft position.

The reported clipped requests coincided with `playback_echo_risk` rejection after
idle motion. Motion had been folded into echo, causing endpoint splits when motion
stopped. Frames/captures now carry separate `motion_risk`; endpointing preserves
speech across that transition and ASR can process the whole utterance. Perception
skips voice embedding, CoreState abstains attribution/learning, and microphone-hold
qualification remains false for motion-contaminated audio. Actual playback echo
still follows its existing rejection path. This fixes an identified clipping path;
logs without retained waveforms cannot explain every missed word.

Blocked-I/O verification: **573 checks passed** with synthetic devices, speech and
embeddings, including pose transitions/holding/release, cancellation/rejection,
execution-before-acknowledgment, blink shutdown and complete capture across motion.
Live ASR accuracy, natural-language model selection, physical clearance and animation
feel remain pending. No hardware was operated for this update; no added dependency
or resident model, and no measured live latency/resource figures.

### Resume-motion follow-up

Live user feedback confirmed eye blinks and offering the hand. The next request,
“You can resume normal motion now,” was correctly transcribed, but the model
answered in prose without executing `release_poses`; no release receipt exists
for that turn. The controller correctly retained its previous hold.

Each human-turn snapshot now includes current held body parts, and pose history
names the executed pose. The semantic tool description and hosted request guidance
explicitly require `release_poses` for requests to resume/relax after a hold,
including permission phrasing. No transcript regex or extra classifier was added.
A production-session regression covers offer, intervening conversation, release
before acknowledgment, renewed throttle speech movement, and cleared hold context.
574 blocked-I/O checks pass; scripted choices verify execution/context, not live
LLM selection. A fresh live language test after restarting V3 remains pending.

### Throttle idle and spoken shutdown

Throttle channels 8–10 now join idle hero/poker movement, using V2's five idle
poses, excluding the current pose, with 2.3–3.3-second motion and lower idle
profiles. The existing single idle scheduler owns cadence and cancellation;
there is no new writer. Held throttle poses remain fixed. Releasing poses restores
both idle and speech motion. Speech/capture still preempts idle, and shutdown parks.

A trusted standalone “shut down” or “shutdown” (also “Rex, shut down”) requests
normal runtime exit without an LLM call. It cancels ongoing work, lets the main
loop drain workers, then uses existing application cleanup for LED fade, servo
park, port closure and V2 handoff. It does not power off macOS. “Stop” remains
cancellation; longer discussion or untrusted transcripts do not trigger shutdown.
Existing playback-echo rejection still applies; reliable voice barge-in is not
newly provided. Terminal `shut down`/`shutdown` also exit normally.

588 blocked-I/O checks pass, including the shared runtime exit followed by park
and closed fake transport. All new evidence is synthetic; no robot or hosted
provider was operated. User confirmed the preceding resume/camera repairs worked.

### September 27: quieter throttle wrist

User reported throttle-wrist noise. Channel 10's pace multiplier is reduced from
2.2 to 1.1, scaling both speed and acceleration caps and proportional travel to
roughly half their prior values. This applies to idle, speech, requested poses,
startup and parking; shoulder/elbow multipliers and all position/clearance limits
are retained. Existing pulse-arrival waits still gate sequence progression.
This is a physical tuning change based on feedback, not measured acoustic evidence.
590 synthetic blocked-I/O checks pass; actual noise reduction and live motion pace
remain to be evaluated. No hardware was operated for this adjustment.

### September 27: all eleven model-selected joints

Session `6e3cf34ffd9b48ebbc26f83a79806aaa` started the servo provider,
but the correctly transcribed head-turn request received a refusal: the tool only
exposed visor/throttle poses. `set_robot_pose` now adds head left/right/center using
the existing startup neck targets (4000/8000/5472). `set_robot_joints` exposes neck,
head lift/tilt, visor, hero elbow/wrist, poker arm, microphone-bearing hero arm,
and throttle shoulder/elbow/wrist. Each field is null (unchanged) or a finite
0–100 percentage of the existing calibrated pulse range, not an angle. No raw
serial command, speed override, extra model or V2 extraction was added.

Partial throttle requests fill unspecified throttle axes from fresh controller
readback and retain the three-channel batch, existing bridge routes and clearance
validation. The whole route is validated before any joint writes. Arbitrary
combinations can be rejected; calibrated endpoints do not guarantee every combined
pose is mechanically clear. Acknowledgment follows pulse arrival, not visual proof.

Every selected joint holds through speech and idle motion. Holding channel 7 also
prevents microphone recentering; identity enrollment remains unavailable until
release and a newly settled microphone hold. `release_poses` clears all holds;
shutdown overrides them. Context names all held body parts. The model uses these
tools for explicit human requests; autonomous physical gaze remains separate.

597 blocked-I/O checks pass with synthetic serial/PCM, fake clocks and scripted
model choices. New coverage exercises every joint through Session, head motion
before speech, hold persistence, partial throttle batches, unsafe-route rejection
without writes, cancellation, release/reacquisition and park. No hardware or hosted
model calls were made. Live natural-language selection, directions, clearance and
motion feel still require validation. The reported clipped transcription remains
unresolved without recorded audio; this servo change does not claim an ASR fix.

### September 28: look directions

Session `7db71dda77e2445ea7daff18f4e8fb49`: "look down and to your left" became
`set_robot_joints` neck 20%, head_lift 30%. Lift 30% (4143) is above the level stance
(3600), so the head rose instead of lowering; tilt, the main downward axis, was untouched.
"Look to your left" then chose `head_left` (4000, about 19 degrees) and was spoken as
"Turning left." Because 4000 is right of the earlier 3379, the head swung back right.

Look poses (renamed `look_*` below) now include down, up and level. Down/up use
idle_head's conventions (lower lift, higher tilt looks down) at 3200/5300 and 4200/4000;
level is the drive stance. `look_left` mirrors `look_right` about NECK_CENTER (2944,
about 33 degrees); the startup scan keeps 4000. The new down/up values and the wider left
are unverified on the robot: confirm the look is visible and clear of the body.

### September 28 (second run): chained steps, wave, look then describe

Session `af692e206e1e4716a86936530266b9c8`: "look to your right and then down" ran only
`head_right`, spoken "Turning right."; "can you wave your hand?" had no wave, so Rex held
out the throttle hand; "look down and tell me what you see" was first answered from the
old view, then repeated, moving the head with nothing said.

`set_robot_pose` now takes `poses`, an ordered list of steps the conversation model
composes from the request ("look right, then left, then up, then down" is four steps).
Fixed combined poses were removed: side steps keep up/down and vice versa, so chaining
covers them. Steps run in order after each arrives; the first failure stops the rest and
Rex says the movement did not complete. Poses are named `look_*` so the model describes
them as looking; the wheels remain drive_base. `look_after` takes a fresh camera frame
0.3 s after the last step and makes one bounded continuation call to answer it.

`wave` extracts V2's wave-back (`sequences/animations.py` at fb96f08, clean tree): hero
arm to 4800 and elbow to 6300, the wrist sweeps its full calibrated travel four times at
V2's auto speed for a 0.32 s swing, then channels 4/5/7 return to where they started.
Nothing is held. V2's in-sync elbow bob is omitted (one speed per beat). Moving
channel 7 moves the microphone array, so bearings are invalidated as for any arm move.
Synthetic serial and a fake camera cover these contracts. Live wave speed,
visible sweep amplitude, arm clearance and the model's step choices remain unverified.

### September 28 (owner requests): no caps, mixed sequences, voice reflex

The owner does not want caps on motion commands. A step list has no maximum length, and
the Maestro adapter now accepts speed/acceleration 0 (the board's own "no limit") when
choreography chooses it; the wave's wrist sweeps use it, as V2 did. Existing profiles are
unchanged. Calibrated pulse limits and throttle clearance checks remain.

Steps may mix moves and lights in order: `blink` (three eye blinks) and `lights_<pattern>`
(about 2.5 s of an LED animation) run through the LED owner between servo steps, only
when that pattern is available. "Look left then right then up then down then hold your
hand out and blink your eyes" is one call of six steps and one model generation.

Voice reflex (`voice_reflex.py`): once an utterance has 0.3 s of voiced, non-echo speech
and the array reports one dominant bearing (3+ strong samples), the neck turns to face it
at speed 60/acceleration 15 before recognition or any model call. Only the front half
(within 90 degrees) moves the head; bearings beyond the neck's travel turn it fully to
that side. It fires once per utterance, holds nothing, re-frames the camera like an idle
glance, and yields to an explicit held neck pose. A model move, base motion or talking
stops an unfinished reflex instead of failing busy. It assumes array forward is body
forward, as drive already does. In session af692e20 Bret's clear bearings were 13-39
degrees left and the TV's -142, so the TV would not have turned the head. Neck noise
during the utterance is flagged as motion (lower voice confidence, not discarded).
Synthetic checks only; live latency, aim and feel are unverified.

### September 28 (third run): acknowledgment order and reflex aim

Session `5e97b13a661b4a6b88a45faecebed371`: a five-step sequence (hand, look left, visor
down/up, blink) ran correctly, but "On it." played only after it. `set_robot_pose` now has
`say_before`, spoken and heard before the first step (talking motion and the steps share
the servo owner); `text` still waits for every step.

The reflex fired once and moved about 6 degrees. The array put Bret 6-9 degrees right in
every clear utterance while the camera saw his face 32-38 degrees left, at the frame edge;
the previous session's array and camera agreed within about 10 degrees. The cause (array
or arm mounting, or beam selection) is not established from the logs. When a fresh face
is within 60 degrees of the voice bearing, the reflex now aims at the face; otherwise at
the voice. "I'm over here" after a held look left was skipped as `neck_held`; a new voice
now takes the neck back from an earlier held look. Synthetic checks only.

### October 23 (run 1e626e07): looks a person asks for, and the neck's real angle

- A look a person asks for (`set_robot_pose` look step, or a look tool's direction) holds until
  he has answered from it and about 20 s after (`ServoOwner.look_hold_until`): the voice reflex
  returns `holding_requested_look`, the idle gaze keeps the direction and his own beats cannot
  move it. A person's `look_at_people`, `release_poses`, a drive or sentry ends it. Where it
  pointed is kept for `last_look`.
- Owner ruling: those looks are brisk, neck and head servos at speed 40 (lift pole unchanged).
- The neck turns about 0.57 of V2's assumed degrees (fitted from logged faces in four runs, not
  measured by eye). `drive_space.NECK_PULSES_PER_DEG` (camera geometry) is corrected and clamps
  to the neck's travel; `servo_poses.NECK_PER_DEG` stays as the size unit for idle and talking
  motion, which were tuned by eye. The "33-degree" look poses are about 19 degrees.
Synthetic serial only; not yet run on the robot.

## November 19: the shutdown park finds a route for the throttle arm

Runs `6c1f1cdf`, `89d66b1c` and `45da7ce9` all ended with the shutdown park refused
(`ThrottleRouteOutsideClearance`) before anything moved, head and visor included. The park had one throttle
route, straight to the tuck and then to `PARK`, and the arm sat where that first move is not clear. The wake
leaves the arms where they are until the range sensors read clear (November 17), so the arm never moved and
every later shutdown failed the same way. The park and shutdown now try that route first, then through
`REST`, then `throttle_route`'s waypoints (`throttle_park_stages`); each stage passes the same clearance rule.
If none is clear, everything else parks, the throttle arm stays exactly where it is and the receipt says
`ThrottleArmLeftWhereItWas`.

**Evidence:** synthetic Maestro checks only; the stranded live pose was not read. **Still open:** the first
real shutdown after this change, and where the arm sits now.

## November 20: the drive stance uses the same throttle routes, and goes ahead without them

Run `a5963bfa`: both asked-for turns were refused (`ArmParkFailed:ThrottleRouteOutsideClearance`) because
`base_lanes('base_park')` still had its own single fallback. It now uses `throttle_park_stages`. With no
clear route the rest of the stance still runs, the throttle stays where it is and the receipt says
`ThrottleArmLeftWhereItWas`; the drive owner then checks turns with the arms-out reach (`drive_space.ARMS`),
and `base_release` leaves an arm with no clear way to rest where it is. Synthetic checks only; nothing moved.

## November 20: the throttle arm's keep-clear is its front right

Owner: the throttle arm is on his right and reaches straight ahead; the right side sensors are flush with it.
`arms_blocked` keeps it in only while the front-right reading (`throttle_front_m`) is under 0.3 m (it was the
nearest of the whole front under 0.61 m); `RobotServos.execute_pose` refuses a fully extended throttle pose
under 0.5 m. The throttle hand (14, 15) is out of `REACHING_ARMS`, `BASE_HELD` and `ARM_RETURN`, and idle moves
it alone while its arm is held. Synthetic checks only.
