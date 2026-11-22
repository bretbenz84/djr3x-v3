"""Sole executor of LLM-selected drive-base plans, plus the back-off reflex.

The conversation owner proposes a bounded plan; CoreState validates it; this
owner executes one step at a time against V2's unchanged ESP32 firmware. The
firmware keeps its reflex stop, watchdog and drive deadman. Receipts report
controller outcomes, odometry and IMU heading; never visual confirmation.
"""
from collections import deque
from dataclasses import asdict, dataclass, field
import math
import threading
import time
from uuid import uuid4

from .adapters.esp32 import DriveLink, RadarLink, open_serial
from .contracts import Cancellation, Cancelled, ProviderFailure
from .drive_space import (ARMS, ARMS_TUCKED, allowed_turn, arms_blocked, throttle_front_m, beside_gap, clearance, describe_bearing, face_range,
                          front_room, front_surface, image_bearing, move_allowance, out_of_view, radar_bodies,
                          seen_points, steer, wall_angle, wrap, AXLE_AFT, FRONT_MARGIN, MIN_TURN, PERSON_MARGIN,
                          SCRAPE_M, STEER_MAX, SURFACE_HOLD_M, WALL_HOLD_M)
from .field_guide import GUIDE
from .local_map import LocalMap
from .providers import Capability

# Owner, November 14 (first run on the 96 mm wheels): down a hallway into the living room he
# averaged 3 cm/s. Tight space meant a crawl capped at 0.12 m/s, cut further by the model's
# "slow" pace and a tight-space factor (0.05-0.09 m/s), and open floor never went past the
# 0.25 m/s set for the September first test. "If he's in a tight hallway he still goes faster,
# but he can go much faster if there are not obstacles in his way." One rule now sets how fast
# the room lets him roll forward (DriveBase._room_speed): the nearest return beside him or at a
# front corner, and how soon he would reach anything in his path. The pace scales only the top.
#
# Owner, November 15 evening (run 916af362): "his hallway speed is too slow". Down the hall to the
# workshop he averaged 0.1 m/s (3.4 m in 35 s), the other hall trip 0.13-0.26 m/s. That rule slowed
# him for any wall within 0.9 m beside him (0.25 m/s with a wall 0.35 m away, 0.12 m/s hugging one)
# and for anything at the edge of his path as if it were straight ahead. A hallway is open ahead:
# the firmware centres him between its walls (its stop zone does not use the side sensors), and the
# gamepad drives the same halls at 0.60 m/s without touching them. Now a wall beside him slows him
# only as it nears touching, and what is in his path slows him by how soon he would reach it.
OPEN_SPEED = .45          # Nothing near him (the wheels reach 0.61 m/s, the gamepad 0.60).
HALLWAY_SPEED = .25       # Backing up (rear sensing is narrower) and the dashboard joystick.
SQUEEZE_SPEED = .12       # Brushing past a door jamb, or about to reach something in his path.
SIDE_SCRAPE_M = .08       # A side return this near: squeeze speed...
SIDE_FREE_M = .22         # ...rising to full speed here: a 1 m hallway driven down the middle.
CORNER_SCRAPE_M = .15     # The same for a front corner (a jamb he is about to pass)...
CORNER_FREE_M = .5        # ...which slows him from half a metre out.
AHEAD_REACH_S = 1.        # Straight ahead: at least this long before he reaches the front stand-off.
EDGE_REACH_S = 1.         # At one edge of his path (steered past): this long before it is inside the hold.
# Requested plans may cover 10 m in 1.5 m legs (September 27: the 3 m first-test cap cut door
# trips short and refused 'go ten feet').
LIMITS = {
    'requested': {'speed_mps': OPEN_SPEED, 'step_m': 1.5, 'total_m': 10., 'turn_deg': 360., 'total_turn_deg': 720., 'max_steps': 6},
    # October 2 owner ruling: Rex's own moves need no permission and no smaller limits; he
    # only must not drive into things and must respect the time-of-flight boundaries, which
    # the clearance checks enforce for every origin.
    'self_initiated': {'speed_mps': OPEN_SPEED, 'step_m': 1.5, 'total_m': 10., 'turn_deg': 360., 'total_turn_deg': 720., 'max_steps': 6},
}
ACTIONS = ('turn', 'move', 'face', 'approach', 'find_person', 'seek', 'go_to', 'look_around')
# Room trips (owner, November 22; docs/ROOM_NAVIGATION_PLAN.md). Run 8cece04a: told to go into the living
# room, he counted it reached because, seen through its doorway, it filled 60% of the view, and 0.0 m later
# said "Living room". A room is somewhere he goes into, not a thing that fills the picture.
ROOM_ENTER_M = 1.         # Rolled at least this far toward a room before stopping at something counts as in it...
ROOM_FILLS_AFTER_M = 1.5  # ...and this far before the room filling the view does.
ENTER_ON_M = .8           # Through its opening, on into the room this far.
DOOR_SIDE_M = .45         # Both sides this near: a doorway or a hall (his body is 0.54 m, the door 0.8 m)...
DOOR_OPEN_M = .9          # ...and one side this open again: through it.
LOOK_AROUND_DEG = 45.     # A look-around: eight looks, one every 45 degrees, photos at each.
LOOK_AROUND_SETTLE_S = 1.
BEARING_ROLL_M = 3.       # Toward a room known only by its direction from here: at most this far.
SEGMENT_FIX_MIN_DEG = 4.  # A passage's tiny copies turn him back onto the line he drove before by at
SEGMENT_FIX_MAX_DEG = 20. # least this and at most this (a bigger one is a poor match, not a heading).
SEGMENT_FIX_SCORE = .5
# Run 8cece04a: the one look that saw the living room (on his right) was blurred and called low confidence,
# so it counted as nothing and he searched left, the wrong way, until he gave up. A doubtful sighting is now
# looked at again, steadied and turned toward, and it decides which way the search sweeps.
WEAK_RETRY = 1
SEEK_SWEEP_DEG = 80.      # Just under the ~90 degree camera view, so consecutive looks overlap.
SEEK_LOOKS = 4            # Current view plus three turns: about a full circle.
PLAN_VISION_CALLS = 40    # Hosted looks per plan (search, re-locating while rolling, routes around things).
SELF_VISION_CALLS = 8     # Rex's own curiosity gets fewer paid looks than a human request.
# October 3: he saw the microwave over a counter that juts out and could not plan a way
# around it; he stopped at a table with open floor beside it. When an approach is blocked
# he now looks at the floor (head tipped slightly down), and a hosted look at that picture
# plus the range sensors by direction picks the next leg. He keeps his own position as he
# goes, so afterwards he can turn back to where the target should be and check.
ROUTE_LEGS = 8            # Camera-chosen legs per approach (five ran out part-way round a kitchen island).
ROUTE_LEG_M = 1.5
ROUTE_LOOK_DOWN_DEG = 12. # Enough to bring the floor a metre ahead into the picture.
# Owner, October 5: the head needs about 1.5 s after tipping down before it is still
# enough for a clear picture; the 0.3-0.5 s allowed before gave blurred route looks.
HEAD_SETTLE_S = 1.5
ARM_CLEAR_S = 2.            # A kept arm comes out once its space has read clear this long.
ROUTE_BEYOND_VIEW_DEG = 60.   # The open way lies past the picture's edge: turn this far and go.
ROUTE_SLACK_DEG = 60.     # After a leg, before two sightings fix its position, the thing may sit this far off.
SIDE_WAY_M = .8           # A side sensor this open is a way around when nothing in front is.
# Going around what blocks a vision-guided approach (owner request September 27: he stopped
# 15 ft short of a door with open floor beside the obstacle, or pointed slightly into a wall).
DETOUR_DEG = 30.          # The sought thing stays inside the ~90 degree view after the turn.
DETOUR_MORE_DEG = 25.     # Once more when the first turn leaves the obstacle in the path.
# 0.6 m until November 14: after the 30 degree turn that moved him 0.30 m aside, short of his half
# width plus the path margin (0.32 m), and only the slow crawl's drift took him past a thing dead
# ahead. 0.7 m moves him 0.35 m aside at the room's faster pace.
DETOUR_M = .7
MAX_DETOURS = 2
SIDE_OPEN_M = .5          # A front band this much past the stand-off counts as a way around.
PERSON_REACH = .8         # Face range minus a front return within this may be knees, feet or chair legs.
CRUISE_LOOK_S = 2.        # While rolling, a new look starts at most this often (hosted looks take 2-5 s).
AIM_DEG = 15.             # Re-aim only past this while the path ahead is open (legs re-locate anyway).
LOST_AFTER_S = 2.5         # Unseen this long mid-approach: stop and look for them (was 6 s, then gave up).
# Head looks while re-finding a person or thing: as far as the neck turns. October 23: it reaches about
# 25 degrees each way, not the 40 assumed before (drive_space.NECK_PULSES_PER_DEG).
LOOK_NECK_MAX = 25.
MAX_REACQUIRES = 3
VOICE_LEG_M = 2.           # With only a voice to go on, roll this far toward it looking for a face.
# Owner, November 15 (run 7261d8ce): "turn left and come towards me" with him out of face range
# turned as asked, then spun 270-460 degrees looking for a face and never came. A turn the person
# asked for points at them: "come to me" right after it rolls ahead this far looking for a face.
DIRECTED_LEG_M = 3.
# November 15 (7261d8ce): reaching the doorway (and, wrongly, the kitchen far down the hall) ended the
# search, so "just keep going forward" and "keep going towards the kitchen" became an approach to
# nothing, refused, after he had already said "Rolling.". What he last reached stays the thing a
# later approach re-finds for this long.
REACHED_KEEP_S = 180.
# October 3 hallway run: pointed slightly into a hallway wall he refused every forward move,
# though straight ahead was open. One short side beside an open centre is a wall to turn
# away from before moving, not an obstacle; tight spaces are driven slower, never refused
# while the wall stays outside the hold distance.
STRAIGHTEN_DEG = 12.      # One turn away from the wall; repeated while it still crosses the path.
MAX_STRAIGHTEN = 3
STRAIGHTEN_WITHIN_M = .8  # Only walls this near ahead: a far one is met by a later leg.
SIDE_OPEN_MORE_M = .3     # Straight ahead and the far side this much more open than the short side.
# October 5 evening: angled a dozen degrees into a 1.2 m hallway's wall, every guarded leg
# stopped within 0.3 m on its hold, the straighten turn did not fire once the matrix saw
# the wall ahead too, and he ended against the door jamb. The firmware was nudging him
# away from the wall all along (10 degrees over those legs); the host kept stopping him
# first. In tight space the host now drives a slow continuous crawl, lets the firmware's
# steering finish its work, and adds only what the firmware cannot read (drive_space.steer).
# The door beyond is narrower still (about 0.8 m for a 0.54 m body): the same crawl. Its
# speed is the room's (_room_speed; November 14), less while steering hard.
CRAWL_MIN_SPEED = .06
CRAWL_TICK_S = .15        # Setpoint refresh, inside the firmware's 300 ms drive deadman.
CRAWL_STALL_S = 2.        # No wheel progress this long: something holds him.
# October 12: sitting with his back to a pass-through bookshelf on short legs, "come here"
# spun him 72 degrees on the spot. The rear sensors read past the shelf, the swing check
# passed, the body caught the shelf and the wheels pushed for nine seconds until the
# firmware aborted, and he gave up; straight ahead was open (owner: "it would have been
# totally fine to drive forward and bank while driving"). A turn toward someone he is
# going to approach now banks forward when anything is close behind, and any spin that
# is refused or stalls banks before it backs up or gives up.
TURN_STALL_S = 2.         # Owner, October 12: a spin making no headway this long is held by something.
TURN_PROGRESS_DEG = 3.
BANK_FROM_DEG = 30.       # Turns this large toward an approach bank when something is close behind.
BANK_REAR_M = .8          # Anything this near behind (or no rear reading): bank, don't spin.
BANK_ROOM_M = .3          # Open floor past the front stand-off needed to start a bank.
BANK_SPEED = .15
BANK_RADIUS_M = .3        # Wider than half the body, so no corner swings backward.
BANK_LEAD_M = .1          # Nearly straight first, to roll off what is behind.
BANK_LEAD_ANG = .1        # rad/s during the lead.
BANK_MIN_ANG = .15        # rad/s floor while easing out of the turn.
BANK_DONE_DEG = 4.
BANK_MAX_M = .6           # Clear of what was behind by then: any rest of the turn is a checked spin.
BANK_CORNER_M = .3        # A front return this near on the turning side stops the bank.
HALLWAY_M = .5            # A side return within this on either side is tight space: crawl.
STRAIGHTEN_MIN_DEG = 8.   # The turn away from a wall is the angle it converges by, within these.
STRAIGHTEN_MAX_DEG = 30.
STRAIGHTEN_MARGIN_DEG = 3.
STRAIGHTEN_SIDE_M = .5    # A wall this near beside him that converges ahead is turned away from first.
STRAIGHTEN_TOWARD_MIN_M = .3   # ...unless the side he would turn toward is nearer than this (a doorway).
TIGHT_CORNER_M = .5       # A front-corner return this close is tight space too.
# Owner, November 17 (run e7d8c980): beside something on his left and facing a door standing open at
# an angle, every forward move ended "path blocked" within centimetres and each turn toward the open
# side was cut to 7 degrees; he was talked at for three minutes and stayed put. "If he were to turn
# right he'd pass the obstacle on the left but as he turns that way it appears like the door in front
# is a blocker, but it's not. If he then inched forward and turned left he could clear both." A
# slanted surface with an open end (drive_space.front_surface) is something to run along: he creeps up
# to where his body would nearly touch it, steering toward the open end as far as what is beside him
# allows, then turns on the spot toward that end and rolls on, at most this many times in one move.
SURFACE_TURNS = 3
SURFACE_TURN_MIN_DEG = 5. # A turn toward the open end that measures less than this made no difference.
SURFACE_LEGS = 2          # Such legs in one approach before the camera is asked.
SURFACE_LEG_M = 1.        # Past the surface's end by then; the approach re-aims at its target after it.
SURFACE_KEEP_S = .5       # One seen this recently stands in for a frame that misses it...
SURFACE_KEEP_DEG = 5.     # ...while he has not turned more than this since.
# The depth columns see 45 degrees and the ring's eight beams are 45 degrees apart: as he turns along
# a door its near end slides out between them while it is still beside his front, and the corner of
# what stands beside him sits between two side beams. What the range sensors saw stays where it was,
# in the room, for this long and while this near. It counts toward the room ahead (until the columns
# look at that spot again), holds back a turn toward it, and is checked by the spin toward the open end.
SEEN_KEEP_S = 8.
SEEN_KEEP_M = 1.
SEEN_NOTE_S = .1          # Noted at most this often.
# October 3, second run. A voice's direction is remembered as a room heading, so it stays
# right after he turns and for requests spoken while he moves (the model re-used "88 degrees
# left" after he had already turned to face it, and he ended up facing away). Owner, October 20:
# two minutes of that memory is the wrong method (it pointed at whoever spoke last, from wherever
# they were). It now covers only the last few seconds of an exchange; where the speaker is comes
# from their face, this utterance's bearing and the radar (_speaker_target).
VOICE_RECENT_S = 15.
VOICE_RADAR_AGREE_DEG = 30.   # A heard bearing this close to a radar body is aimed at the body.
# October 19 (b4ce6464): "face me" turned 166 degrees on a voice heard 90 s and 2.6 m of
# driving earlier. A heading only points at the person from where he heard it; once he has
# rolled this far the remembered voice no longer says where they are.
VOICE_MOVED_M = .5
# Closing on a target, the firmware's wall-steering treats what is ahead as something to go
# around (owner: "the firmware starts steering him away rather than just slowing him down").
# The host turns that steering off for the last stretch; the firmware still slows and stops.
# October 3, third run: with the owner in view straight ahead, a three-sample bearing 115
# degrees to the left (a recurring phantom near the array's side) became the speaker target
# and he turned away. A bearing that agrees with a visible face is refined to that face (at
# close range the array sits well beside the camera: -25 degrees read for a centred face);
# one that points away from everyone in view moves the remembered voice only when strong.
VOICE_FACE_AGREE_DEG = 50.
VOICE_STRONG_SAMPLES = 8
VOICE_STRONG_SHARE = .8
# November 16: a frame keeps the neck angle it was taken at. This long after a head move ends (the
# board's own ramp has stopped) or the wheels stop (they coast), frames show the new view.
HEAD_VIEW_SETTLE_S = .3
BASE_VIEW_SETTLE_S = 1.
VIEW_OPEN_S = 10.
CLOSING_M = 1.2           # Something this near straight ahead, with the target ahead too.
CLOSING_DEG = 20.
# November 15 evening (916af362): sent through an archway on his left, he rolled 1.2 m while it slid
# from the middle of the picture to 34 degrees left, then stopped: a door 0.62 m straight ahead was
# held at the archway's own 0.6 m stand-off, and the spin toward the archway was refused for the jamb
# beside him, so he called it blocked. With the sought thing this far to one side, what is straight
# ahead is something else and keeps only the obstacle margin, and in tight space he steers toward it
# harder with the firmware's centring off (it pushed him away from the near jamb, the side he was
# turning to), so he creeps on toward the opening and turns into it (owner, by hand: a foot on, then left).
SIDE_TARGET_DEG = CLOSING_DEG
SIDE_AIM_ANG = .3         # rad/s toward a sought thing off to one side in tight space (0.2 elsewhere).
NEAR_AHEAD_M = .4         # Arrival: something straight ahead within this of the stand-off...
NEAR_WIDTH = .3           # ...and the thing at least this wide in view (a box in front of a far door is not it).
# October 3 evening: three microwave trips reached it (30-40% of the view, up from 9%) and
# then spent up to 75 s and 300 degrees looking for a way "around" the counter under it; one
# was reported as a failure. A thing that wide which has at least doubled in the picture
# since he first saw it is near, whatever the sensors are stopping him on.
GROWN_NEAR = 2.
# November 15 (7261d8ce): the kitchen at the end of the hall "doubled" from 0.2 to 0.4 of the view while
# he rolled 0.05 m (the locator's boxes for a room varied 0.2-0.52 standing still), and a hall wall seen
# at an angle 0.7 m ahead, with the left of his path open 3.5 m, counted as "ahead": he reported arriving
# in the doorway twice. Growth counts only after this much travel, and in tight space the return must
# close his whole path.
GROWN_TRAVEL_M = .4
TURN_ARRIVED_DEG = 3.     # A turn whose firmware done never came is arrived within this of its angle.
# November 15 (7261d8ce): one gap in the controller's telemetry, beside a depth sensor's recovery,
# ended a good approach 1.99 m down the hall. The wheels are told to stop (the firmware also stops
# them 0.3 s after its last drive command) and he carries on if telemetry is back within this.
TELEMETRY_GRACE_S = 2.
TELEMETRY_GRACES = 3      # Pauses like that allowed in one roll before it ends as stale.
# Locator boxes are not distances: a hallway door 4 m away came back 40% of the view wide.
FILLS_VIEW = .6
# October 14 plan, step 3: a close reading where the sensors saw open floor a moment ago, with
# nothing there before, is rechecked for this long instead of ending the trip or the leg; one
# that persists stops him as before. The firmware's own stop zone is untouched.
RECHECK_S = .6
# After a camera-chosen leg, further legs around the same obstacle may come from floor the
# sensors already saw open (local_map.LocalMap.open_way) instead of a new hosted look.
MEMORY_LEGS = 4
MEMORY_LEG_M = .8
TURN_RATE = 45.          # V2 overshoot grows with rate; autonomous V2 turns used 40.
# The base coasts after the firmware ends a turn. Thirty-odd logged turns (October 1-3)
# ended 2-10 degrees past the command, more at the faster rate: about 0.11 s of coasting.
# Turns of 12 degrees or more are commanded that much short, so they end near the request
# (owner: "sometimes the turning goes a little too far and then he loses sight").
TURN_COAST_S = .11
TURN_COAST_FROM_DEG = 12.
# Owner request October 3: his moves need not always be the same speed, turns included.
# The model picks a pace per step; these scale the approved speed and turn rate downward
# only. Unverified on the robot: confirm the slow turn does not stall on carpet.
PACE = {'slow': .6, 'easy': .8, 'normal': 1., None: 1.}
# Owner feedback: 4-5 ft was too far (September 27); 0.8 m by face left him at a seated
# person's knees, "a little too close" (October 3); about 3 ft was still too close for
# Jeremy (October 20). Owner, October 25: "4 ft is ideal". Every person kind stops there:
# a voice or radar start keeps its stop for the face approach it hands over to.
DEFAULT_STOP_AT = {'face': 1.2, 'nearest_person': 1.2, 'object': .6, 'radar_body': 1.2, 'voice_direction': 1.2}
# Owner, October 25: a roll toward someone held one speed and then stopped late, which
# looked threatening. Streamed rolls toward a person or thing now speed up at EASE_IN and
# plan their slow-down at EASE_OUT (from about 0.45 m out at full speed). Halts, stop
# words and reflexes keep the firmware's firmer ramp. Unverified on the robot.
EASE_IN = .1    # m/s²: 0.04 to 0.25 m/s takes about 2 s and 0.3 m.
EASE_OUT = .07  # m/s²
# A roll toward a sought thing plans its slow-down to the stand-off at this rate, allowing the same
# 0.3 s reaction as its stop check, which assumes the firmware's 0.35 m/s². November 14: at 0.45 m/s
# the check otherwise ended the roll 0.4 m early, and each restart costs a camera look.
CRUISE_DECEL = .2  # m/s²
# Pushed at every connect; config can only lower firmware hard caps.
CONFIG = {'max_lin': OPEN_SPEED, 'max_ang': round(math.radians(60), 3), 'accel_lin': .35, 'accel_ang': 2.,
          'stop_zone_m': .15, 'slow_zone_m': .6, 'default_turn_rate': TURN_RATE,
          'watchdog_ms': 500, 'drive_expiry_ms': 300,
          # V2 pushes these: firmware defaults keep the gamepad owner until Start is pressed.
          'manual_autoreturn': True, 'manual_idle_return_secs': 4,
          # The firmware's own default wall-steering gain (V2 calib.h ASSIST_GAIN). The host
          # zeroes it while closing on a target; pushing it at every connect means a run
          # that died mid-approach cannot leave the steering off for the next one.
          'assist_gain': 2.}
# Match the firmware's charger-entry threshold and the operator battery gauge.
# A freshly unplugged pack can retain 13.61 V for minutes; 13.6 V reintroduced
# the old surface-charge lockout even after firmware had released its latch.
CHARGER_MV = 14000
# A battery reading older than this (s) does not reach the chest's charge meter at shutdown.
CHARGE_READING_MAX_AGE = 30.
# Dashboard joystick (V2 Motivator console): the September planned-move speed (unchanged when
# Rex's own speeds rose on November 14), a gentle spin, and the swing check one 10 Hz setpoint
# ahead of a spin.
MANUAL_SPEED = HALLWAY_SPEED
MANUAL_TURN = math.radians(60.)
MANUAL_TURN_CHECK_DEG = 15.


def ramp_toward(current: float, target: float, accel_step: float, decel_step: float) -> float:
    """V2 motion_controller.ramp_toward: gentle up, faster (never abrupt) down; a reversal
    decelerates through zero first."""
    if target == current:
        return target
    speeding_up = abs(target) > abs(current) and (current == 0.0 or (target > 0.0) == (current > 0.0))
    step = accel_step if speeding_up else decel_step
    if step <= 0.0:
        return target
    delta = target - current
    if delta > step:
        return current + step
    if delta < -step:
        return current - step
    return target


# The joystick's own easing on top of the firmware's (dashboard and telepresence alike).
RAMP_UP_SECONDS, RAMP_DOWN_SECONDS = 1.2, .5   # V2 MOTION_MANUAL_RAMP_UP/DOWN_SECS

CAMERA_KINDS = {'face', 'nearest_person', 'object'}
# Owner, November 14 (log c85e39dc): both depth matrices stopped answering before startup, the wheels
# were locked out as front_depth_matrix_unavailable, and Rex told him his "drive controller didn't wake
# up". "It would be better if the LLM responded indicating what's wrong rather than just refusing to
# move." Each lockout now reaches the model with what is wrong and what would fix it.
UNAVAILABLE_MEANINGS = {
    'front_depth_matrix_unavailable': (
        'The 8x8 depth sensor on the front of the base has stopped sending readings. It is what sees chair legs '
        'and low things ahead, so the wheels stay locked until it reads again; the drive controller and motors '
        'are fine. Usual causes: its reset button pressed in by the shell, or a loose cable. '
        'Once it is freed the controller sets the sensor up again by itself, usually within 20 seconds, and he '
        'says aloud when it is back; if it stays out, switch the base off and on.'),
    'radial_ring_unavailable': (
        'None of the distance sensors around the base ring is reporting, so the wheels stay locked: he cannot '
        'tell what is beside or behind him. Likely their cable or power.'),
    'drive_telemetry_stale': (
        'The drive controller has stopped sending its status for the moment (cable, power or a reboot); the wheels '
        'wait until it reports again.'),
    'drive_link_failed': 'The cable link to the drive controller failed this run; restarting Rex reconnects it.',
    'drive_not_ready': 'The drive controller is still connecting or has been closed.',
    'gamepad_in_control': ('Someone has the wheels on the gamepad; they come back 4 seconds after the pad is let go, '
                           'or with its Start button.'),
    'charging': 'He is on the charger, and the controller locks the wheels while charging. Unplug to drive.',
    'controller_estop': 'The emergency stop is on (the gamepad B button); Start on the gamepad clears it.',
    'controller_comms_lost': ('The drive controller lost contact with Rex and stopped the wheels as a safety '
                              'measure; it usually recovers within a second.'),
    'controller_fault': 'The drive controller reports a fault (named in unavailable_reason) and has stopped the wheels.',
    'sentry_mode': 'Sentry mode locks the wheels while he guards.',
    'human_pose_held': 'A pose someone asked for is being held, so he does not drive off on his own.',
    'wheel_controller_not_connected': ('The drive controller did not connect when Rex started (unplugged, '
                                       'switched off, or its port busy); restarting Rex once it is on reconnects it.'),
}


# Owner, November 21 (run b9f6091f): both depth matrices went quiet together for a moment half a
# second before "go through the door and down the hall"; Rex refused ("my front sensor's gone dark")
# and the sensors were back within 12 s. "Can we have him try again before refusing?" A sensor that
# was reading within SENSOR_GRACE_S stays offered to the model, and a drive waits up to
# SENSOR_WAIT_S for it before refusing. Nothing moves until the readings are back; an outage older
# than the grace is refused at once, as before.
SENSOR_DROPOUTS = frozenset({'front_depth_matrix_unavailable', 'radial_ring_unavailable',
                             'drive_telemetry_stale', 'controller_comms_lost'})
SENSOR_GRACE_S = 30.
SENSOR_WAIT_S = 12.     # Plans expire 20 s after they are made (state.propose_drive).
SENSOR_RECOVERING_MEANING = (
    'A sensor dropped out a moment ago ({reason}); these dropouts usually clear within seconds. Drive '
    'tools stay offered: if someone asks you to move, call the drive as asked and say in a few words '
    'that you are waiting a second for your sensors. He waits up to {wait} seconds for the readings '
    'before moving and never moves blind; if they stay out the receipt is refused with the reason, '
    'and then you say what is wrong.')


def unavailable_meaning(reason):
    """What a wheel lockout means in words, for the model to tell people; None when wheels are free."""
    if not reason:
        return None
    for key in (reason, reason.split(':')[0], 'controller_fault' if reason.startswith('controller_fault') else None):
        if key in UNAVAILABLE_MEANINGS:
            return UNAVAILABLE_MEANINGS[key]
    return f'The wheels are locked out ({reason}).'


# November 19 (run a5963bfa): with no clear fold the park leaves the throttle arm out and the drive
# goes ahead; turns then check the room for the arms-out reach instead of the tucked one.
ARM_LEFT_OUT = 'ThrottleArmLeftWhereItWas'


class ArmParkFailed(ProviderFailure):
    """The throttle arm did not park before wheel motion; the plan is refused unmoved."""


@dataclass(frozen=True)
class DriveStep:
    action: str
    degrees: float | None = None
    meters: float | None = None
    target: str | None = None
    stop_at_m: float | None = None
    description: str | None = None   # seek: what to look for, in the person's words.
    prefer: str | None = None        # seek, go_to: sweep left or right first.
    pace: str | None = None          # slow, easy or normal (None): scales this step's speed and turn rate.
    directed: bool = False           # go_to after turns or moves a person dictated: their way, not the map's.


@dataclass(frozen=True)
class DrivePlan:
    request_id: str
    steps: tuple
    origin: str
    purpose: str
    targets: dict
    expires_at: float
    look_after: str | None = None
    allow_new_arrivals: bool = False
    park_arm: bool = True


@dataclass
class DriveReceipt:
    request_id: str
    status: str = 'failed'
    reason: str | None = None
    steps: list = field(default_factory=list)
    started_at: float = 0.
    finished_at: float | None = None
    travelled_m: float = 0.
    turned_deg: float = 0.
    medium: str = 'drive_controller'
    evidence: str = 'firmware done results, odometry and IMU heading; no visual confirmation'


class DriveAction:
    """A plan the output owner runs beside speech (start_drive); done is set when it is over."""
    def __init__(self, plan: DrivePlan):
        self.plan = plan
        self.receipt: DriveReceipt | None = None
        self.done = threading.Event()


class Flinch:
    """V2 back-off reflex: per-side adaptive baselines, radial corroboration.

    A reading inside the trigger that also dropped sharply from its own open
    baseline, held for three ticks, fires. Baselines freeze while intruded and
    rise only after sustained clear readings, so a slow approach cannot hide.
    """
    trigger, drop, minimum, adapt, confirm, cooldown = .25, .12, .02, .12, 3, 8.

    def __init__(self):
        self.baseline, self.clear, self.ticks, self.last = {}, {}, 0, float('-inf')

    def observe(self, readings: dict, now: float) -> bool:
        intruding = set()
        for key, value in readings.items():
            if value is None:
                continue
            base = self.baseline.get(key)
            if base is None:
                self.baseline[key] = value
                continue
            if self.minimum <= value <= self.trigger and base - value >= self.drop:
                intruding.add(key)
                self.clear[key] = 0
                continue
            if value < base:
                self.baseline[key] = max(value, base - self.adapt)
                self.clear[key] = 0
            else:
                self.clear[key] = self.clear.get(key, 0) + 1
                if self.clear[key] >= 3:
                    self.baseline[key] = value
        # Corroboration: an independent radial sensor must see its own intrusion.
        self.ticks = self.ticks + 1 if intruding & {'fl_radial', 'fr_radial'} else 0
        if self.ticks >= self.confirm and now - self.last >= self.cooldown:
            self.last, self.ticks = now, 0
            return True
        return False


def least(*limits):
    """The slowest of several (speed, what sets it) limits."""
    return min(limits, key=lambda row: row[0])


class Approach:
    """Mac-streamed approach (adapted from V2 intelligence/approach.py).

    Face width gives an approximate person range; objects have none, so their
    arrival is a centred target at the front stand-off. Blocked never becomes
    completed. V2's fixed 0.55 m fused-front subtraction (which stalled field
    approaches at 26 cm) is replaced by the shared FRONT_MARGIN stand-off.
    """
    def __init__(self, now, kind, stop_at, speed, max_travel, accel=.35):
        self.started, self.kind, self.stop_at, self.speed = now, kind, stop_at, speed
        self.max_travel, self.accel = max(.05, max_travel), accel
        self.last_seen, self.ranges, self.samples = now, [], 0
        self.near_since = self.near_sample = self.block_since = self.front_near_since = self.clear_since = None
        self.travel, self.last_xy = 0., None

    def step(self, now, telemetry, space, target):
        odom = telemetry.get('odom') or {}
        xy = (float(odom.get('x', 0)), float(odom.get('y', 0)))
        if self.last_xy is not None:
            self.travel += math.hypot(xy[0]-self.last_xy[0], xy[1]-self.last_xy[1])
        self.last_xy = xy
        if now - self.started >= max(20., self.max_travel / .08):
            # Was a flat 20 s: at the easy pace that is four metres, and a 6.9 m "come here"
            # ended "timed out" 0.9 m from the person (October 3).
            return 0., 0., 'timeout', 'approach time limit'
        if self.travel >= self.max_travel:
            return 0., 0., 'distance_limit', 'distance limit reached before the target'
        # What the body would drive into; a hallway wall beside the path is not in the way.
        front = space['path_m'] if space.get('path_m') is not None else space['front_m']
        if front is None:
            return 0., 0., 'aborted', 'front clearance unavailable'
        speed_now = max(0., float(odom.get('lin', 0)))
        if target is not None:
            self.last_seen = now
            self.samples += 1
            if target.get('range_m') is not None:
                self.ranges = (self.ranges + [target['range_m']])[-3:]
        blocked = (telemetry.get('state') == 'blocked' and telemetry.get('blocked_dir') in ('front', 'both')) or front <= .2
        if blocked:
            self.near_since = self.clear_since = None
            self.block_since = self.block_since or now
            return 0., 0., ('blocked' if now - self.block_since >= 5 else None), 'front obstacle; holding'
        self.block_since = None
        brake = speed_now*speed_now/(2*self.accel) + speed_now*.3
        # Front sensors see knees and chair legs, not the face: they bound collision
        # only. Personal distance comes from the calibrated face range below. The closer
        # knee margin applies only when the return could be the person; a return well
        # short of the face is furniture (September 27: nearly drove into something).
        person_ahead = (self.kind == 'face' and
                        (not self.ranges or sorted(self.ranges)[len(self.ranges)//2] - front <= PERSON_REACH))
        budget = front - (PERSON_MARGIN if person_ahead else FRONT_MARGIN)
        if budget <= max(.03, brake):
            self.front_near_since = self.front_near_since or now
            if now - self.front_near_since >= .6 and speed_now <= .08:
                centred = target is not None and abs(target['bearing_deg']) <= 15
                if self.kind != 'face' and centred:
                    return 0., 0., 'completed', 'reached stand-off in front of target'
                return 0., 0., 'blocked', 'front stand-off reached before the target'
            return 0., 0., None, 'braking at front stand-off'
        self.front_near_since = None
        if now - self.last_seen >= LOST_AFTER_S:
            return 0., 0., 'lost', 'target lost from camera'
        if now - self.last_seen > 1.2:
            self.near_since = None
            return 0., 0., None, 'waiting for target in camera'
        if self.kind == 'face':
            if not self.ranges:
                return 0., 0., None, 'waiting for face range'
            remaining = sorted(self.ranges)[len(self.ranges)//2] - self.stop_at - .08
            if remaining <= 0:
                if target is None:
                    self.near_since = None
                    return 0., 0., None, 'confirming at stand-off'
                if self.near_since is None:
                    self.near_since, self.near_sample = now, self.samples
                if now - self.near_since >= .6 and self.samples - self.near_sample >= 2 and speed_now <= .08:
                    return 0., 0., 'completed', 'person at camera stand-off'
                return 0., 0., None, 'settling at person'
            self.near_since = None
        elif target is not None and target.get('box_width', 0) >= .5:
            return 0., 0., 'completed', 'target fills the camera view'
        else:
            remaining = budget
        if self.max_travel - self.travel - .03 <= .02:
            return 0., 0., 'distance_limit', 'distance limit reached before the target'
        # Whichever stop comes first (the person, knees or furniture, the distance limit)
        # is eased into; the linear terms still bound the last few centimetres.
        room = min(remaining, budget, self.max_travel - self.travel - .03)
        lin = min(self.speed, max(.04, math.sqrt(2*EASE_OUT*max(0., room))),
                  max(0., budget)/.6, max(0., self.max_travel - self.travel - .03)/.6)
        clear = front >= 1. and all(v is not None and v >= .4 for v in (space['left_m'], space['right_m']))
        if not clear:
            self.clear_since = None
        elif self.clear_since is None:
            self.clear_since = now
        ang = 0.
        if target is not None and self.clear_since is not None and now - self.clear_since >= .4:
            # Firmware adds hallway avoidance; steer only after a clear corridor persists.
            ang = max(-.25, min(.25, math.radians(target['bearing_deg'])*1.2))
        return lin, ang, None, 'approaching'


class RollSpeeds:
    """The forward speeds a step's rolls were sent and what set each one, for its receipt.

    November 15 (916af362): the log said a hallway trip was slow but not what held it down,
    so the next run could only be guessed at. held_by is the most common reason over the
    roll: pace, sides, front_corner, ahead, path_edge, steering, stand_off, distance, easing_in
    (a roll toward a person speeding up gently) or reverse."""
    def __init__(self):
        self.sent, self.why = [], {}

    def add(self, lin, why):
        if lin > 0:
            self.sent.append(lin)
            self.why[why] = self.why.get(why, 0) + 1

    def summary(self):
        if not self.sent:
            return None
        ordered = sorted(self.sent)
        return {'median_mps': round(ordered[len(ordered)//2], 2), 'top_mps': round(ordered[-1], 2),
                'held_by': max(self.why, key=self.why.get)}


class Ease:
    """Gradual speed-up for a streamed roll: each command rises at most EASE_IN per second
    above the last one sent or the measured speed, whichever is higher, so a roll that
    resumes or takes over from another keeps going instead of dropping to a crawl."""
    def __init__(self):
        self.sent, self.at = 0., None

    def __call__(self, now, lin, speed_now):
        base = max(self.sent, speed_now)
        if lin > base:
            lin = min(lin, max(.04, base + (EASE_IN * (now - self.at) if self.at is not None else 0.)))
        self.sent, self.at = lin, now
        return lin


class Radar:
    """Optional LD2450 ring; its bodies are bearing priors for where to look."""
    capability = Capability('radar', 'ESP32-S3 LD2450 radar ring (V2 protocol v1)',
        'bearing prior for people outside the camera view', dependencies=('pyserial',),
        resources=('exclusive radar ESP32 serial',), required=False, shutdown='close port')

    def __init__(self, port, *, open_port=None, clock=time.monotonic):
        self.link = RadarLink(open_port or (lambda: open_serial(port)), clock=clock)
        self.clock = clock
        self.state = 'new'

    def start(self):
        hello = self.link.open({})
        if 'radar' not in (hello.get('caps') or ()):
            raise ProviderFailure('RadarCapabilityMissing')
        self.state = 'ready'

    def bodies(self, window=1.5):
        if self.state != 'ready' or self.link.failure:
            return None
        return radar_bodies(self.link.recent(self.clock() - window))

    def cancel(self):
        pass

    def close(self):
        self.state = 'closed'
        self.link.close()


class DriveBase:
    capability = Capability('drive_base', 'ESP32 differential base (V2 protocol v1)',
        'LLM-selected turns, moves and approaches; back-off reflex', dependencies=('pyserial',),
        resources=('exclusive drive ESP32 serial; 150 ms heartbeat',), required=False,
        shutdown='halt plan; stop; end heartbeat; close port')

    def __init__(self, port, *, open_port=None, clock=time.monotonic, wait=None, radar=None, reflex=True):
        self.link = DriveLink(open_port or (lambda: open_serial(port)), clock=clock)
        self.clock = clock
        self._closing = threading.Event()
        self._halt = threading.Event()
        self.wait = wait or self._closing.wait
        self.radar = radar
        self.servos = None          # RobotServos: throttle park and neck centering
        self.faces = None           # camera.latest(now) -> rows
        self.objects = None         # objects.snapshot() -> rows
        self.on_motion = []         # callables(plan_or_none), before and after base motion
        self.sounds = None          # MotionSounds: whir while the wheels move, accents at the end
        self._origin = 'requested'
        self._pace = 1.
        self._turn_moved = 0.   # Metres driven inside the current step's turns (banks, back-ups).
        self._speeds = RollSpeeds()   # Forward speeds sent during the current step, for its receipt.
        self.trace = None
        self.reflex_allowed = lambda: True
        # A reason the wheels must not move at all (sentry mode, owner October 22), or None. Every
        # plan, his own or asked for, and the back-off reflex are refused while it holds.
        self.hold = lambda: None
        self._lock = threading.Lock()
        self._view_lock = threading.Lock()
        self._view_moves = deque(maxlen=24)   # [started, neck before (None: the wheels), settled]
        self._threads = []
        self._reflex = reflex
        self.state = 'new'
        self.moving = threading.Event()
        # The plan (request id) whose wheels were last commanded; moving is set from a plan's start,
        # before the arms tuck. A line meant for rolling waits for its plan here (owner, October 24).
        self.rolled = None
        self._plan_id = None
        self.motion_until = 0.
        self.history = deque(maxlen=6)
        self.flinch = Flinch()
        self._arm_clear_since = {}    # Kept arm -> when its space was first seen clear.
        self._arm_blocked_shown = frozenset()   # Kept arms last traced as blocked, with their readings.
        self.origin = None
        self.object_tracks = None     # objects including single-frame re-detections after motion
        self.locator = None           # (description, image, cancel) -> hosted vision geometry
        self.router = None            # (image, facts, cancel) -> hosted choice of open floor to drive to
        self._at = [0., 0.]           # His own position in metres since connect, kept from wheel travel along the IMU heading.
        self._dead_ends = []          # (x, y, room heading) of legs that went nowhere in this plan.
        self._odom_mark = None
        self.look = None              # (after) -> fresh sharp camera image, or None
        self._sought = None           # description, note and absolute heading of the last thing sought
        self._vision_calls = 0
        self._directed = False        # The step before this one was a turn a person asked for: they are ahead.
        self._reached = None          # (description, when) of the last sought thing he arrived at.
        self._parked = False
        self._plan_parks = False
        self._throttle_out = False    # The park found no clear fold for the throttle arm; it stayed out.
        self._view_since = float('-inf')
        self._start_heading = None
        self._voice = None            # Room heading and time of the last located voice.
        self._voice_key = None
        self._assist_gain = None      # The firmware's wall-steering gain, as acknowledged at connect.
        self._assist_on = True
        self._surface_seen = None    # (when, heading, front_surface) last seen.
        self._seen = deque(maxlen=1600)   # (when, room x, room y) of what the range sensors saw.
        self._seen_noted = float('-inf')
        self.local_map = LocalMap(clock)   # What the range sensors saw around him in the last minute.
        self._manual = None           # Dashboard teleop holding the plan lock: {'parked', 'moving'}.
        self._offered_reason = None   # The lockout last told to the model, traced when it changes.
        self._sensors_ok_at = None    # Last time no sensor dropout locked the wheels (SENSOR_GRACE_S).
        from .room_map import Odometer
        self.odometer = Odometer(clock)   # Wheel travel from every telemetry frame, gamepad included.
        self.navigator = None         # places.Places: where he is, routes to rooms, photos at look-around stops.
        self._weak = None             # The last doubtful sighting: {'bearing', 'note'}.
        self._room_goal = None        # A room being gone into: {'room_id', 'start', 'squeezed', 'entered_at'}.

    # Lifecycle -----------------------------------------------------------
    def start(self):
        hello = self.link.open({})
        if not {'drive', 'turn', 'move', 'stop'}.issubset(hello.get('caps') or ()):
            raise ProviderFailure('DriveCapabilitiesMissing')
        # Startup is real serial I/O: bound its waits by wall time, not the injected clock.
        ack = self._await_ack(self.link.send({'cmd': 'config', **CONFIG}), 1.5, self._closing.wait, time.monotonic)
        if ack is None:
            raise ProviderFailure('DriveConfigUnacknowledged')
        if not ack.get('accepted'):
            raise ProviderFailure('DriveConfigRejected:' + str(ack.get('reason')))
        # Only firmware that reports the steering gain can have it switched (0.2.x does).
        self._assist_gain = CONFIG['assist_gain'] if 'assist_gain' in (ack.get('config') or {}) else None
        self.link.on_log = self._firmware_log
        self.link.on_telemetry = lambda t: self.odometer.update(t, self._sides(t))
        self._spawn('heartbeat', self._heartbeat)
        deadline = time.monotonic() + 1.5
        while self.link.snapshot()[0] is None:
            if self.link.failure or time.monotonic() >= deadline:
                raise ProviderFailure(self.link.failure or 'DriveTelemetryMissing')
            self._closing.wait(.02)
        odom = (self.link.snapshot()[0].get('odom') or {})
        self.origin = (float(odom.get('x', 0)), float(odom.get('y', 0)), self._heading(self.link.snapshot()[0]))
        self.state = 'ready'
        if self._reflex:
            self._spawn('reflex', self._reflex_loop)
        self._spawn('arm-clearance', self._arm_clearance_loop)

    def _firmware_log(self, level, text):
        """The controller's own log lines (a sensor lost, set up again, a bus restart) join the session log."""
        if self.trace:
            self.trace.emit('drive_firmware_log', level=level, text=text)

    def depth_sensor_ages(self, now=None):
        """Seconds since each 8x8 depth sensor last sent a frame (None: never this run), or None while
        the controller itself is not reporting, when silence says nothing about the sensors."""
        now = self.clock() if now is None else now
        telemetry, front = self.link.snapshot()
        if (self.state != 'ready' or self.link.failure or not telemetry
                or not 0 <= now - telemetry['received_at'] <= .5):
            return None
        rear = self.link.rear_snapshot()
        return {'front': now - front['received_at'] if front else None,
                'rear': now - rear['received_at'] if rear else None}

    def _spawn(self, name, target):
        thread = threading.Thread(target=target, name='r3x-drive-'+name, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _heartbeat(self):
        while not self._closing.wait(.15):
            try:
                self.link.send({'cmd': 'ping'})
            except ProviderFailure:
                self.state = 'faulted'
                return

    def cancel(self):
        """Explicit stop: halt the current plan and command a controlled stop now."""
        self._halt.set()
        if self.sounds:
            self.sounds.stop(now=True)   # A stop means silence now, not at the clip's end (V2).
        try:
            if self.link.port is not None and not self.link.failure:
                self.link.send({'cmd': 'stop'})
        except ProviderFailure:
            pass

    def close(self):
        self._closing.set()
        self.cancel()
        if self._manual is not None:
            self.end_manual()   # The joystick's hold on the plan lock ends with the base.
        self._assist(True)
        if not self._lock.acquire(timeout=5.):
            raise ProviderFailure('DrivePlanStillActive')
        try:
            for thread in self._threads:
                thread.join(2.)
            if any(t.is_alive() for t in self._threads):
                raise ProviderFailure('DriveWorkerDidNotStop')
            self.link.close()
            self.state = 'closed'
        finally:
            self._lock.release()

    # Dashboard teleop ----------------------------------------------------
    def begin_manual(self, *, stance_held=False, turns_on_charger=False):
        """The dashboard joystick takes the wheels: a running plan halts, and the plan lock is
        held until end_manual, so Rex's own and requested drives are refused (DriveBusy) and
        the back-off reflex waits. Returns (ok, reason).

        stance_held: the arms already hold the drive stance for the whole session (a telepresence
        call: its wake takes the drive stance, servo_poses.telepresence_wake_lanes), so no park
        runs before moving, the spin check uses the tucked arms, and the arms are not returned at
        end_manual.

        turns_on_charger: on the charger the stick still turns him in place; forward and back are
        held as 'charging' (owner, November 18, for telepresence callers: a spin cannot pull the
        charging cord, travel can). The firmware turns the wheels on the charger only for a drive
        with zero linear."""
        if self._manual is not None:
            return True, None
        self._halt.set()
        if not self._lock.acquire(timeout=5.):
            return False, 'DrivePlanStillActive'
        self._halt.clear()
        ok, reason = self.availability(turns_on_charger=turns_on_charger)
        if not ok:
            self._lock.release()
            return False, reason
        self._manual = {'parked': stance_held, 'moving': False, 'stance_held': stance_held,
                        'turns_on_charger': turns_on_charger}
        if self.trace:
            self.trace.emit('operator_drive', status='started', stance_held=stance_held)
        return True, None

    def manual(self, lin, ang, cancel):
        """One joystick setpoint (send about 10 times a second; the firmware's 300 ms deadman
        stops the wheels if they stop coming). Clamped to the planned-move speed; forward is
        held at the front path check, spins at the arm-swing check, reverse at the firmware's
        rear block, and forward and back on the charger for a turns_on_charger driver. The arms
        park before the first non-reverse motion. Returns what was sent."""
        manual = self._manual
        if manual is None:
            return {'status': 'refused', 'reason': 'manual_not_active'}
        lin = max(-MANUAL_SPEED, min(MANUAL_SPEED, float(lin) if math.isfinite(lin) else 0.))
        ang = max(-MANUAL_TURN, min(MANUAL_TURN, float(ang) if math.isfinite(ang) else 0.))
        held = []
        if self._halt.is_set():
            # A stop (spoken, Stop button, shutdown) holds the wheels until the stick is released.
            if lin or ang:
                self._manual_stop()
                return {'status': 'refused', 'reason': 'stop_requested', 'lin': 0., 'ang': 0.}
            self._halt.clear()
        if lin or ang:
            ok, reason = self.availability(turns_on_charger=manual['turns_on_charger'])
            if not ok:
                self._manual_stop()
                return {'status': 'refused', 'reason': reason, 'lin': 0., 'ang': 0.}
            if lin and manual['turns_on_charger'] and self.on_charger(self.link.snapshot()[0] or {}):
                held.append('charging')   # Turning in place only: the cord stays where it is.
                lin = 0.
            if (lin > 0 or ang) and not manual['parked'] and self.servos is not None:
                park = self.servos.prepare_base_motion(cancel)
                if park.status != 'pulses_reached':
                    return {'status': 'refused', 'reason': 'ArmParkFailed:' + str(park.reason or park.status),
                            'lin': 0., 'ang': 0.}
                manual['parked'] = 'arm_out' if park.reason == ARM_LEFT_OUT else True
            telemetry = self.link.snapshot()[0] or {}
            blocked = telemetry.get('blocked_dir')
            if lin > 0:
                why = self._crawl_hold(self.space(), telemetry.get('tof_mm'))
                if why in {'path_blocked', 'front_clearance_unknown'} or blocked in ('front', 'both'):
                    held.append(why or 'blocked_front')
                    lin = 0.
            elif lin < 0 and blocked in ('back', 'both'):
                held.append('blocked_back')
                lin = 0.
            if ang:
                arms = ARMS_TUCKED if manual['parked'] is True else ARMS
                if not allowed_turn(math.copysign(MANUAL_TURN_CHECK_DEG, ang), telemetry, arms)[0]:
                    held.append('turn_path_blocked')
                    ang = 0.
        moving = bool(lin or ang)
        if moving != manual['moving']:
            manual['moving'] = moving
            if moving:
                self.moving.set()
            else:
                self.moving.clear()
                self.motion_until = self.clock() + .5
            self._notify(None)
        self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
        return {'status': 'sent', 'lin': lin, 'ang': ang, 'held': held}

    def _manual_stop(self):
        try:
            self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
            self.link.send({'cmd': 'stop'})
        except ProviderFailure:
            pass
        if self._manual and self._manual['moving']:
            self._manual['moving'] = False
            self.moving.clear()
            self.motion_until = self.clock() + .5
            self._notify(None)

    def end_manual(self):
        manual = self._manual
        if manual is None:
            return
        try:
            self._manual_stop()
            if manual['parked'] and self.servos is not None and not manual['stance_held']:
                self.servos.finish_base_motion(keep_parked=arms_blocked(self.space()))
        finally:
            self._manual = None
            self._lock.release()
            if self.trace:
                self.trace.emit('operator_drive', status='ended')

    @property
    def manual_active(self):
        return self._manual is not None

    # Evidence ------------------------------------------------------------
    def motion_risk(self):
        return self.moving.is_set() or self.clock() < self.motion_until

    def space(self, now=None):
        telemetry, matrix = self.link.snapshot()
        now = self.clock() if now is None else now
        self._remember(telemetry, matrix, now)
        return clearance(telemetry, matrix, now, rear_matrix=self.link.rear_snapshot())

    def _surface(self, now=None):
        """The slanted surface with an open end that the front depth columns see now, or None.

        room_m counts what the sensors saw a moment ago and cannot see from here, and beside_m is
        the gap to the nearest such point beside him on the surface's open side. One seen within
        SURFACE_KEEP_S stands in for a frame that does not show it (live, November 17: 3 of 42
        frames of a still door did not)."""
        now = self.clock() if now is None else now
        telemetry, matrix = self.link.snapshot()
        if not matrix or not telemetry or not 0 <= now - matrix['received_at'] <= 1.:
            self._surface_seen = None
            return None
        self._remember(telemetry, matrix, now)
        remembered = self._remembered(telemetry, now)
        hidden = out_of_view(remembered, (0., 0.), 0.)
        surface = front_surface(matrix, telemetry.get('tof_mm'), hidden)
        heading = self._heading(telemetry)
        if surface is not None:
            self._surface_seen = (now, heading, surface)
        elif self._surface_seen is not None:
            at, was, kept = self._surface_seen
            turned = wrap(heading - was) if heading is not None and was is not None else 0.
            if now - at <= SURFACE_KEEP_S and abs(turned) <= SURFACE_KEEP_DEG:
                surface = {**kept, 'room_m': round(front_room(matrix, telemetry.get('tof_mm'), hidden), 2), 'kept': True}
        if surface is None:
            return None
        gap = beside_gap(remembered, surface['side'])
        return {**surface, 'beside_m': round(gap, 2) if gap is not None else None}

    def _ring_centre(self, heading):
        """The ring's centre in the room: his tracked point is the axle's middle, behind it."""
        h = math.radians(heading)
        return self._at[0] + AXLE_AFT*math.cos(h), self._at[1] + AXLE_AFT*math.sin(h)

    def _remembered(self, telemetry, now):
        """What the range sensors saw in the last SEEN_KEEP_S within SEEN_KEEP_M, in his frame now."""
        heading = self._heading(telemetry) if telemetry else None
        if heading is None:
            return []
        cx, cy = self._ring_centre(heading)
        h = math.radians(heading)
        points = []
        for at, px, py in self._seen:
            dx, dy = px - cx, py - cy
            if now - at <= SEEN_KEEP_S and math.hypot(dx, dy) <= SEEN_KEEP_M:
                points.append((dx*math.cos(h) + dy*math.sin(h), -dx*math.sin(h) + dy*math.cos(h)))
        return points

    def _remember(self, telemetry, matrix, now):
        """Fold this sensor frame into the short-term memory of the floor around him."""
        if telemetry:
            self.local_map.observe(telemetry, matrix, tuple(self._at), self._heading(telemetry), now,
                                   rear_matrix=self.link.rear_snapshot())
            heading = self._heading(telemetry)
            if heading is not None and matrix and now - self._seen_noted >= SEEN_NOTE_S:
                points = seen_points(matrix, telemetry.get('tof_mm')) if 0 <= now - matrix['received_at'] <= 1. else None
                if points is not None:
                    self._seen_noted = now
                    cx, cy = self._ring_centre(heading)
                    h = math.radians(heading)
                    self._seen.extend((now, cx + x*math.cos(h) - y*math.sin(h), cy + x*math.sin(h) + y*math.cos(h))
                                            for x, y in points)

    def _doubtful(self, space, now):
        """The closest return on his straight path contradicts what was just seen there."""
        sides = {'left': 14., 'center': 0., 'right': -14.}
        rooms = {side: space.get(f'path_{side}_m') for side in sides}
        rooms = {side: room for side, room in rooms.items() if room is not None}
        if not rooms:
            return False
        side = min(rooms, key=rooms.get)
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        doubtful = self.local_map.doubtful(tuple(self._at), heading, sides[side], rooms[side], now)
        if not doubtful and self.trace:
            # Why a block was taken at face value, so a floor run shows whether the rule is too
            # strict or simply never met (October 17: 0 rechecks in 34 live trips, no trace).
            self.trace.emit('drive_doubtful_checked', side=side, distance_m=round(rooms[side], 2),
                            **(self.local_map.last_check or {}))
        return doubtful

    def _path_blocked(self, space, need):
        """A forward move would be refused for something in the path itself."""
        surface = self._surface()
        if surface is not None or self._tight(space, need):
            return self._crawl_hold(space, (self.link.snapshot()[0] or {}).get('tof_mm'), surface) == 'path_blocked'
        return move_allowance(need, space)[1] == 'path_blocked'

    def _recheck(self, where, cancel):
        """Hold still briefly and read again; the caller decides on the fresh reading."""
        if self.trace:
            self.trace.emit('drive_reading_rechecked', where=where, outcome='holding', seconds=RECHECK_S)
        self._sleep(RECHECK_S, cancel)
        return self.space()

    @staticmethod
    def on_charger(telemetry):
        """The controller's charger latch, or the pack at full-charge voltage."""
        return bool(telemetry.get('charging')) or (telemetry.get('batt_mv') or 0) >= CHARGER_MV

    def availability(self, now=None, *, turns_on_charger=False):
        """(ok, reason). turns_on_charger: the charger alone does not refuse, for a driver that
        then holds all travel and only turns in place (a telepresence caller, owner November 18)."""
        now = self.clock() if now is None else now
        if self.state != 'ready':
            return False, 'drive_not_ready'
        held = self.hold()
        if held:
            return False, held
        if self.link.failure:
            return False, 'drive_link_failed'
        telemetry, _ = self.link.snapshot()
        if not telemetry or not 0 <= now - telemetry['received_at'] <= .5:
            return False, 'drive_telemetry_stale'
        if telemetry.get('owner') == 'manual':
            return False, 'gamepad_in_control'
        if telemetry.get('state') in {'estop', 'fault', 'comms_lost'}:
            return False, 'controller_' + telemetry['state']
        if telemetry.get('fault'):
            return False, 'controller_fault_' + str(telemetry['fault'])
        if self.on_charger(telemetry) and not turns_on_charger:
            return False, 'charging'
        space = self.space(now)
        for reason in ('front_depth_matrix_unavailable', 'radial_ring_unavailable'):
            if reason in space['health']:
                return False, reason
        self._sensors_ok_at = now
        return True, None

    def recovering(self, reason, now=None):
        """A sensor dropout that began within SENSOR_GRACE_S of the last good reading."""
        now = self.clock() if now is None else now
        return (reason in SENSOR_DROPOUTS and self._sensors_ok_at is not None
                and 0 <= now - self._sensors_ok_at <= SENSOR_GRACE_S)

    def _await_sensors(self, reason, cancel, deadline):
        """Hold still while a recent dropout clears; (ok, reason) once it does or time is up."""
        started = self.clock()
        if self.trace:
            self.trace.emit('drive_sensor_wait', reason=reason, outcome='waiting')
        while not self.clock() >= min(deadline, started + SENSOR_WAIT_S):
            self._sleep(.25, cancel)
            ok, now_reason = self.availability()
            if ok or now_reason not in SENSOR_DROPOUTS:
                break
            reason = now_reason
        else:
            ok, now_reason = False, reason
        if self.trace:
            self.trace.emit('drive_sensor_wait', reason=reason, outcome='recovered' if ok else 'still_out',
                            seconds=round(self.clock() - started, 2))
        return ok, now_reason

    @staticmethod
    def _sides(telemetry):
        """(left, right) metres from the ring's side beams, for doorways along a trail."""
        tof = telemetry.get('tof_mm') or {}
        def nearest(keys):
            values = [tof[k] / 1000. for k in keys if isinstance(tof.get(k), (int, float)) and tof[k] >= 0]
            return min(values) if values else None
        return nearest(('lf', 'lb')), nearest(('rf', 'rb'))

    @staticmethod
    def _heading(telemetry):
        imu = telemetry.get('imu') or {}
        if imu.get('ok') and isinstance(imu.get('yaw'), (int, float)):
            return float(imu['yaw'])
        theta = (telemetry.get('odom') or {}).get('theta')
        return math.degrees(float(theta)) if isinstance(theta, (int, float)) else None

    def _neck(self):
        """Current neck angle; plans centre the neck before resolving camera targets."""
        try:
            return self.servos.owner.neck_degrees() if self.servos is not None else 0.
        except Exception:
            return 0.

    def note_head_motion(self, moving):
        """The head camera began or ended a move that changes the view (the servo owner's notice, sent
        before the new neck target is commanded, so the angle read on a start is the one it leaves)."""
        now = self.clock()
        with self._view_lock:
            if moving:
                self._view_moves.append([now, self._neck(), None])
            elif self._view_moves and self._view_moves[-1][2] is None:
                self._view_moves[-1][2] = now + HEAD_VIEW_SETTLE_S

    def view_still(self, at, margin=.2):
        """A frame taken at `at` shows a still view: no head or base move overlapped it (a room photo's bearing)."""
        with self._view_lock:
            # A move noticed as started but never as ended (a head move's end can close a base move's entry)
            # stops counting after VIEW_OPEN_S; a head move never lasts that long, and a drive is motion anyway.
            return not any(started <= at and (settled >= at - margin if settled is not None else at - started < VIEW_OPEN_S)
                           for started, _, settled in self._view_moves)

    def moved_during(self, start, end):
        """Whether the wheels turned at any time between start and end (a capture's speech)."""
        with self._view_lock:
            return any(before is None and started <= end and (settled is None or settled - BASE_VIEW_SETTLE_S >= start)
                       for started, before, settled in self._view_moves)

    def _note_base_view(self):
        """The wheels started or stopped: a frame from before or during a base move shows another direction."""
        now = self.clock()
        with self._view_lock:
            if self.moving.is_set():
                if not (self._view_moves and self._view_moves[-1][1] is None and self._view_moves[-1][2] is None):
                    self._view_moves.append([now, None, None])
            else:
                for move in reversed(self._view_moves):
                    if move[1] is None and move[2] is None:
                        move[2] = now + BASE_VIEW_SETTLE_S
                        break

    def _seen_bearing(self, row):
        """Base-relative bearing of something in a camera frame, placed with the neck angle the head had
        when that frame was taken; None for a frame taken while the head or the wheels moved, or before
        the wheels last moved.

        November 16 (dc1543d7): a face box from a frame taken while the voice reflex was still turning the
        head toward Bret was placed with the head's new angle. The turn counted twice, and the base swung
        62 degrees for a man about 24 degrees off and lost him."""
        at, neck = row.get('at'), self._neck()
        if at is not None:
            with self._view_lock:
                for started, before, settled in reversed(self._view_moves):
                    if settled is not None and at >= settled:
                        break            # Taken after this move and every earlier one: the angle stands.
                    if before is None:
                        if settled is None:
                            continue     # A drive under way re-finds its targets after each of its own motions.
                        return None      # Taken before or while the wheels last turned him: another direction.
                    if at >= started:
                        return None      # Taken while the head moved.
                    neck = before        # Taken before this head move: the head pointed where it was then.
        return image_bearing(row['box'], neck)

    def _fresh_faces(self, now):
        from .faces import VISIBLE_FACE_MAX_AGE
        if self.faces is None:
            return []
        return [f for f in self.faces(now) if 0 <= now - f['at'] <= VISIBLE_FACE_MAX_AGE and f.get('box')]

    def context(self, origin, speaker_direction=None, speaker_person=None):
        """Robot-relative targets and clearance for the conversation model. speaker_person is
        who the state owner identified as speaking (acoustic or inferred), for the face fallback."""
        now = self.clock()
        ok, reason = self.availability(now)
        if origin=='self_initiated' and self._pose_held():
            ok, reason = False, 'human_pose_held'
        recovering = not ok and origin == 'requested' and self.recovering(reason, now)
        if reason != self._offered_reason and self.trace:
            # November 14: the log never said why the wheels were locked out all run.
            self.trace.emit('drive_availability', available=ok, reason=reason, origin=origin,
                            **({'offered_while_recovering': True} if recovering else {}))
        self._offered_reason = reason
        space = self.space(now)
        targets = []
        faces = self._fresh_faces(now)
        def path(bearing, distance=None):
            # Front sensors cover roughly +/-25 degrees: which part of the path a straight
            # approach crosses. Walls beside the path (a hallway) are not in its way.
            band = 'path_center_m' if abs(bearing) <= 8 else 'path_left_m' if bearing > 0 else 'path_right_m'
            clear = space.get(band) if space.get(band) is not None else space['front_m']
            if clear is None:
                return {'clear_to_m': None, 'direct_approach': 'unknown'}
            blocked = clear - FRONT_MARGIN < (distance - 1. if distance else 1.)
            return {'clear_to_m': round(clear, 1), 'direct_approach': 'blocked_early' if blocked else 'clear'}
        for face in faces:
            bearing = self._seen_bearing(face)
            if bearing is None:
                continue   # Seen mid-move: where it is now is not known until the next settled frame.
            rng = face_range(face['box'])
            targets.append({'target_id': face['track_id'], 'kind': 'face', 'person_id': face.get('identity'),
                            'bearing_deg': round(bearing), 'where': describe_bearing(bearing),
                            'approx_range_m': round(rng, 1) if rng else None, 'path': path(bearing, rng)})
        # Offered even with nobody in view: "turn around and come to me" resolves it after the
        # turn (September 27: with no face visible the model silently dropped "come to me").
        targets.append({'target_id': 'nearest_person', 'kind': 'nearest_person', 'visible_now': bool(faces),
                        'where': 'largest visible face, resolved when the step runs' if faces else
                                 'nobody in view now; on your own you look only toward a voice from the last few seconds '
                                 'or a radar body, never around the room, and with neither it does not move' if origin == 'self_initiated' else
                                 'nobody in view now; facing or approaching it looks for the person first '
                                 '(toward the last voice, radar bodies, then around); right after a turn they asked '
                                 'for ("turn left and come to me") it rolls straight ahead looking for them instead'})
        for row in (self.objects() if self.objects else ())[:8]:
            bearing = self._seen_bearing(row)
            if bearing is None:
                continue
            targets.append({'target_id': row['target_id'], 'kind': 'object', 'label': row['label'],
                            'bearing_deg': round(bearing), 'where': describe_bearing(bearing), 'path': path(bearing)})
        bodies = self.radar.bodies() if self.radar else None
        for body in bodies or ():
            targets.append({'target_id': body['target_id'], 'kind': 'radar_body', 'bearing_deg': body['bearing_deg'],
                            'range_m': body['range_m'], 'moving': body['moving'], 'where': describe_bearing(body['bearing_deg'])})
        telemetry, _ = self.link.snapshot()
        facing = self._heading(telemetry) if telemetry else None
        heard = (speaker_direction and speaker_direction.get('status') == 'observed'
                 and speaker_direction.get('bearing_deg') is not None)
        if heard and facing is not None and speaker_direction.get('ended_at') != self._voice_key:
            # Each utterance's bearing is taken once, against the heading he had when it was heard.
            self._voice_key = speaker_direction.get('ended_at')
            self._note_voice(speaker_direction, facing, faces, bodies or (), now, speaker_person)
        if origin == 'requested':
            speaker = self._speaker_target(now, speaker_direction if heard else None, faces, bodies or (),
                                           speaker_person)
            if speaker is not None:
                targets.append(speaker)
        displacement = None
        if telemetry and self.origin:
            odom = telemetry.get('odom') or {}
            dx, dy = float(odom.get('x', 0)) - self.origin[0], float(odom.get('y', 0)) - self.origin[1]
            heading = self._heading(telemetry)
            displacement = {'distance_m': round(math.hypot(dx, dy), 2),
                            'heading_change_deg': round(wrap(heading - self.origin[2])) if heading is not None and self.origin[2] is not None else None}
        offered = {'available': True, 'unavailable_reason': None, 'unavailable_meaning': None,
                   'sensor_recovering': SENSOR_RECOVERING_MEANING.format(reason=reason, wait=int(SENSOR_WAIT_S))
                   } if recovering else {
                   'available': ok, 'unavailable_reason': reason, 'unavailable_meaning': unavailable_meaning(reason)}
        return {**offered,
                'clearance_m': {k: (round(v, 1) if isinstance(v, float) else v) for k, v in space.items() if k != 'health'},
                'clearance_meaning': GUIDE['drive.clearance_m'],
                'radar_view': {'status': 'unavailable' if self.radar and bodies is None else ('connected' if self.radar else 'not_configured'),
                    'bodies': [{k: b.get(k) for k in ('bearing_deg', 'range_m', 'moving')} | {'where': describe_bearing(b['bearing_deg'])}
                               for b in bodies or ()],
                    'meaning': GUIDE['drive.radar_view']},
                'sensor_notes': space['health'], 'targets': targets,
                'radar': 'unavailable' if self.radar and bodies is None else ('connected' if self.radar else 'not_configured'),
                'vision_seek': self.locator is not None and self.look is not None,
                'last_sought': self._last_sought(),
                # step_m is an internal leg length; live October 1 the model read it as the most it
                # could ever move and answered "back up five feet" with 1.5 m.
                'limits': {'max_move_m': LIMITS[origin]['total_m'], 'max_turn_deg': LIMITS[origin]['turn_deg'],
                           'max_total_turn_deg': LIMITS[origin]['total_turn_deg'], 'max_steps': LIMITS[origin]['max_steps'],
                           'speed_mps': LIMITS[origin]['speed_mps'],
                           'meaning': GUIDE['drive.limits']},
                'origin': origin,
                'displacement_since_startup': displacement,
                'recent_motion': list(self.history),
                'meaning': GUIDE['drive']}

    def _note_voice(self, direction, facing, faces, bodies, now, person=None):
        """Remember where a voice came from, weighing it against who is in view and what the radar sees.

        This places a direction to turn toward. It never says that the visible person
        spoke: attribution stays with the speaker tracker."""
        bearing = float(direction['bearing_deg'])
        seen = [b for b in (self._seen_bearing(f) for f in faces) if b is not None]
        nearest = min(seen, key=lambda b: abs(wrap(bearing - b)), default=None)
        body = min((b['bearing_deg'] for b in bodies), key=lambda b: abs(wrap(bearing - b)), default=None)
        basis = 'voice'
        if nearest is not None and abs(wrap(bearing - nearest)) <= VOICE_FACE_AGREE_DEG:
            bearing, basis = nearest, 'voice_and_face'
        elif nearest is None and body is not None and abs(wrap(bearing - body)) <= VOICE_RADAR_AGREE_DEG:
            bearing, basis = body, 'voice_and_radar'   # The radar places a body more tightly than the array.
        elif nearest is not None and self._voice_bearing(now) is not None:
            clusters = direction.get('clusters') or [{}]
            strong = ((direction.get('samples') or 0) >= VOICE_STRONG_SAMPLES and
                      (clusters[0].get('weight_share') or 0.) >= VOICE_STRONG_SHARE)
            if not strong:
                # Kept as evidence in the turn's record; it only does not move the target.
                if self.trace:
                    self.trace.emit('voice_direction_not_adopted', bearing_deg=round(bearing),
                                    samples=direction.get('samples'), nearest_face_deg=round(nearest),
                                    kept_deg=round(self._voice_bearing(now)))
                return
        self._voice = {'heading': wrap(facing + bearing), 'at': now, 'basis': basis, 'from': tuple(self._at),
                       'person': person}

    def _voice_bearing(self, now=None, person=None):
        """Base-relative bearing of the last located voice while it is the latest few seconds of talk,
        he has not driven away from where he heard it, and (when both are known) it was the same person."""
        now = self.clock() if now is None else now
        telemetry, _ = self.link.snapshot()
        facing = self._heading(telemetry) if telemetry else None
        if self._voice is None or facing is None or now - self._voice['at'] > VOICE_RECENT_S:
            return None
        if math.dist(self._voice['from'], self._at) > VOICE_MOVED_M:
            return None
        if person and self._voice.get('person') and self._voice['person'] != person:
            return None   # Somebody else's voice says nothing about where this speaker is.
        return wrap(self._voice['heading'] - facing)

    def _speaker_target(self, now, heard, faces, bodies, person):
        """Where whoever just spoke is, from the camera, the microphone and the radar together.

        Owner, October 20: remembering the last voice's direction for two minutes is the wrong method; it
        sent him toward Jeremy, who had spoken from behind 70 s earlier, when the array could not place
        Bret. November 16 (dc1543d7): "What are you doing, Rex?" from the next room, unplaced, spun him 169
        degrees toward where the owner had spoken 100 s before. The speaker's own recognized face comes
        first; then this utterance's bearing, or the last few seconds' from the same person, each refined
        to a face or a radar body that agrees with it (_note_voice). Otherwise nothing: an unplaced voice
        gives no direction to turn."""
        own = [f for f in faces if person and f.get('identity') == person]
        bearing = self._seen_bearing(own[0]) if len(own) == 1 else None
        if bearing is not None:
            # October 17: the microphone gave no bearing on most turns, so "turn to face me" was refused
            # twice while the owner, identified by voice, sat recognized in view. It is re-found by
            # identity as he turns.
            return {'target_id': 'speaker', 'kind': 'face', 'person_id': person, 'bearing_deg': round(bearing),
                    'where': describe_bearing(bearing), 'basis': 'speaker_recognized_face'}
        voice = self._voice_bearing(now, person)
        if voice is None and heard is None:
            return None
        bearing = voice if voice is not None else float(heard['bearing_deg'])
        if voice is None:
            body = min((b['bearing_deg'] for b in bodies), key=lambda b: abs(wrap(bearing - b)), default=None)
            if body is not None and abs(wrap(bearing - body)) <= VOICE_RADAR_AGREE_DEG:
                bearing = body
        return {'target_id': 'speaker', 'kind': 'voice_direction', 'bearing_deg': round(bearing),
                'where': describe_bearing(bearing),
                'heard_seconds_ago': round(now - self._voice['at']) if voice is not None else 0,
                'basis': self._voice.get('basis', 'voice') if voice is not None else 'voice'}

    def _assist(self, on):
        """Firmware wall-steering on or off (its own slow and stop zones are untouched).

        Off sets the steering gain to zero: the firmware keeps only its last-resort push
        away from a wall inside 13 cm. A partial config is applied at once and does not
        interrupt motion (V2 config_params.cpp)."""
        if self._assist_gain is None or on == self._assist_on:
            return
        try:
            self.link.send({'cmd': 'config', 'assist_gain': self._assist_gain if on else 0.})
            self._assist_on = on
        except ProviderFailure:
            pass

    @staticmethod
    def _near_target(space, bearing, range_m=None, stop_at=0.):
        """The target is ahead and near: what the front sensors see now is the target or
        stands right before it, so slowing and stopping is wanted, not steering around."""
        if bearing is None or abs(bearing) > CLOSING_DEG:
            return False
        ahead = space.get('front_center_m')
        return (range_m is not None and range_m - stop_at <= CLOSING_M) or (ahead is not None and ahead <= CLOSING_M)

    # Execution -----------------------------------------------------------
    def _check(self, cancel):
        cancel.check()
        if self._halt.is_set() or self._closing.is_set():
            raise Cancelled()

    def _sleep(self, seconds, cancel):
        deadline = self.clock() + seconds
        while self.clock() < deadline:
            self._check(cancel)
            self.wait(min(.05, deadline - self.clock()))

    def _await_ack(self, seq, timeout, wait, clock=None):
        clock = clock or self.clock
        deadline = clock() + timeout
        while clock() < deadline:
            ack = self.link.ack(seq)
            if ack is not None or self.link.failure:
                return ack
            wait(.02)
        return None

    def _finite(self, command, timeout, cancel, guard=None):
        """Run one firmware turn or move to its done. guard() may name a reason to stop early."""
        seq = self.link.send(command)
        ack = self._await_ack(seq, .5, self.wait)
        if ack is not None and not ack.get('accepted'):
            return 'refused', 'controller_refused_' + str(ack.get('reason')), None
        deadline = self.clock() + timeout
        self._wheels_on('turn' if command['cmd'] == 'turn' else 'move', self._origin)
        try:
            while True:
                done = self.link.done(seq)
                if done is not None:
                    self._view_since = self.clock() + .3   # The camera view changed; wait for it.
                    result = done.get('result')
                    return ({'completed': 'completed', 'blocked': 'blocked', 'aborted': 'aborted'}.get(result, 'stopped'),
                            result, done)
                if self.link.failure:
                    raise ProviderFailure(self.link.failure)
                if self.clock() >= deadline:
                    self.link.send({'cmd': 'stop'})
                    return 'timeout', 'no_done_before_deadline', None
                self._track()
                why = guard() if guard else None
                if why:
                    self.link.send({'cmd': 'stop'})
                    self._view_since = self.clock() + .3
                    self._sleep(.3, cancel)   # Let the base settle before odometry is read.
                    return ('completed' if why == 'turn_arrived' else 'blocked'), why, None
                self._check(cancel)
                self.wait(.05)
        except Cancelled:
            try:
                self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            raise
        finally:
            if self.sounds:
                self.sounds.stop()   # The wheels reported done: the whir ends with them.

    def _telemetry_back(self, cancel, used):
        """Stopped for stale telemetry: wait briefly for it to return. used is this roll's count."""
        if used[0] >= TELEMETRY_GRACES or self.link.failure:
            return False
        used[0] += 1
        try:
            self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
        except ProviderFailure:
            return False
        until = self.clock() + TELEMETRY_GRACE_S
        while self.clock() < until:
            self._check(cancel)
            telemetry, _ = self.link.snapshot()
            if telemetry and 0 <= self.clock() - telemetry['received_at'] <= .5:
                if self.trace:
                    self.trace.emit('drive_telemetry_resumed', pause=used[0])
                return True
            self.wait(.05)
        return False

    def _telemetry(self):
        telemetry, _ = self.link.snapshot()
        if not telemetry or not 0 <= self.clock() - telemetry['received_at'] <= .5:
            raise ProviderFailure('DriveTelemetryStale')
        self._track(telemetry)
        return telemetry

    def _track(self, telemetry=None):
        """Fold wheel travel since the last call into his own position.

        Distance comes from the wheels; direction from the IMU heading, because wheel
        odometry's own heading over-reads turns. Forward or backward is judged inside the
        odometry's frame, which is consistent with itself. Good for metres, not a map."""
        telemetry = telemetry or self.link.snapshot()[0]
        if not telemetry:
            return
        self._remember(telemetry, self.link.snapshot()[1], self.clock())
        odom = telemetry.get('odom') or {}
        xy = (float(odom.get('x', 0)), float(odom.get('y', 0)))
        mark, self._odom_mark = self._odom_mark, xy
        heading = self._heading(telemetry)
        if mark is None or heading is None:
            return
        dx, dy = xy[0] - mark[0], xy[1] - mark[1]
        distance = math.hypot(dx, dy)
        if distance < 1e-4:
            return
        theta = odom.get('theta')
        if isinstance(theta, (int, float)) and dx*math.cos(theta) + dy*math.sin(theta) < 0:
            distance = -distance
        self._at[0] += distance * math.cos(math.radians(heading))
        self._at[1] += distance * math.sin(math.radians(heading))

    def _bearing_to(self, point):
        """Base-relative bearing and distance of a remembered room position from where he is now."""
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        if point is None or heading is None:
            return None, None
        self._track(telemetry)
        dx, dy = point[0] - self._at[0], point[1] - self._at[1]
        return wrap(math.degrees(math.atan2(dy, dx)) - heading), math.hypot(dx, dy)

    @staticmethod
    def _odom_heading(telemetry):
        theta = (telemetry.get('odom') or {}).get('theta')
        return math.degrees(float(theta)) if isinstance(theta, (int, float)) else None

    def _escape_for_turn(self, degrees, plan, cancel, why):
        """V2 swing escape, generalized: earn room by backing up (or pulling forward), then retry once.

        why goes into the receipt: October 13 the owner asked "why did you back up?" and the
        model, told only that a move happened, called it "a little safety repositioning"."""
        space = self.space()
        rear, front = space['rear_m'], space.get('path_m')
        if rear is not None and rear - .35 >= .2:
            leg = -min(.5, rear - .35)
        elif front is not None and front - FRONT_MARGIN >= .3:
            leg = min(.4, front - FRONT_MARGIN)
        else:
            # October 13: a refused "face the person" turn left no trace of why no room was
            # earned first; the log now keeps what the sensors saw.
            self._step_trace({'action': 'turn_escape', 'status': 'unavailable', 'reason': 'no_room_behind_or_ahead',
                              'requested_deg': round(degrees, 1)}, space)
            return None
        moved = self._move(leg, plan, cancel)
        side = 'backed up' if leg < 0 else 'pulled forward'
        return {**moved, 'why': f"{side} {abs(moved.get('travelled_m', 0.)):.1f} m to make room to turn: {why}"}

    def _turn(self, degrees, plan, cancel, verify=True, escape=True, bank=False, remembered=()):
        """One turn. bank: the turn heads an approach, so with something close behind he
        drives forward round the curve instead of spinning on the spot. escape: a spin that
        is refused or stalls banks if the front is open, else backs up and spins once more."""
        telemetry = self._telemetry()
        row = {'action': 'turn', 'requested_deg': round(degrees, 1)}
        if bank and escape and abs(degrees) >= BANK_FROM_DEG:
            space = self.space()
            if (space['rear_m'] is None or space['rear_m'] < BANK_REAR_M) and self._bank_room(space, degrees):
                banked = self._banked(degrees, plan, cancel, row, 'something_close_behind')
                if banked is not None:
                    return banked
        allowed, note = allowed_turn(degrees, telemetry, self._arms(), remembered)
        if not allowed and escape and note and note.startswith('turn_path_blocked'):
            if self._bank_room(self.space(), degrees):
                banked = self._banked(degrees, plan, cancel, row, 'spin_refused')
                if banked is not None:
                    return banked
            moved = self._escape_for_turn(degrees, plan, cancel, 'something was too close to spin on the spot ('
                                          + note.removeprefix('turn_path_blocked: ') + ')')
            if moved is not None:
                self._turn_moved += abs(moved.get('travelled_m', 0.))
            if moved is not None and moved['status'] == 'completed':
                retry = self._turn(degrees, plan, cancel, verify, escape=False)
                return {**retry, 'escape': moved, 'travelled_m': moved.get('travelled_m', 0.)}
        if not allowed:
            return self._refused_turn(row, note)
        if not self._parked and self.servos is not None and self._plan_parks:
            self._ensure_parked(cancel)
            telemetry = self._telemetry()
            allowed, note = allowed_turn(degrees, telemetry, self._arms(), remembered)
            if not allowed:
                return self._refused_turn(row, note)
        before, before_odom = self._heading(telemetry), self._odom_heading(telemetry)
        space = self.space()
        rate = round(TURN_RATE * self._pace, 1)
        command = allowed
        if abs(allowed) >= TURN_COAST_FROM_DEG:
            command = math.copysign(abs(allowed) - rate * TURN_COAST_S, allowed)
        progress = [before, self.clock()]
        def stalled():
            heading = self._heading(self.link.snapshot()[0] or {})
            if heading is None or progress[0] is None:
                return None
            if abs(wrap(heading - progress[0])) >= TURN_PROGRESS_DEG:
                progress[:] = [heading, self.clock()]
            elif self.clock() - progress[1] > TURN_STALL_S:
                # November 15 (7261d8ce): an 18.9 degree turn measured 24.2, the firmware never said
                # done, and "slight left, then down the hall" ended as a stalled turn. Turned that far
                # (or past it) is arrived; a stall short of it still stops.
                if before is not None and abs(wrap(heading - before)) >= abs(allowed) - TURN_ARRIVED_DEG:
                    return 'turn_arrived'
                return 'turn_stalled'
            return None
        status, reason, _ = self._finite({'cmd': 'turn', 'deg': round(command, 1), 'rate': rate},
                                         max(8., abs(allowed)/rate + 8.), cancel, guard=stalled)
        row.update(commanded_deg=round(allowed, 1), status=status, reason=note if status == 'completed' else reason)
        measured = odom = None
        held = status in {'blocked', 'aborted'}
        if (status == 'completed' and verify or held) and before is not None and abs(allowed) <= 170:
            self._sleep(.4, cancel)
            after = self._telemetry()
            measured = wrap(self._heading(after) - before) if self._heading(after) is not None else None
            if before_odom is not None and self._odom_heading(after) is not None:
                odom = wrap(self._odom_heading(after) - before_odom)
            # No host correction: the firmware closes turns on its own IMU loop. The owner saw
            # turns reach the right angle and then backtrack under September 27 host corrections
            # driven by a drifting telemetry heading. Both readings are recorded for diagnosis.
        row['measured_deg'] = round(measured, 1) if measured is not None else None
        row['odometry_deg'] = round(odom, 1) if odom is not None else None
        # Report the controller's closed-loop turn unless both sensors agree otherwise.
        agreed = measured is not None and odom is not None and abs(measured - odom) <= 5
        row['turned_deg'] = abs(measured if agreed or held and measured is not None
                                else allowed if status == 'completed' else 0.)
        if status != 'completed':
            row['clearance_m'] = self._room_row(space)
        self._step_trace(row, space)
        if not (held and escape):
            return row
        # Stuck part-way: what is left of the turn, banked forward if the floor ahead is open.
        rest = allowed - (measured if measured is not None else 0.)
        if abs(rest) < MIN_TURN:
            return row
        stuck = {k: row[k] for k in ('status', 'reason', 'turned_deg', 'clearance_m')}
        if self._bank_room(self.space(), rest):
            banked = self._banked(rest, plan, cancel, {'action': 'turn', 'requested_deg': round(rest, 1)}, 'spin_stalled')
            if banked is not None:
                return {**banked, 'requested_deg': round(degrees, 1), 'stalled_spin': stuck,
                        'turned_deg': round(row['turned_deg'] + banked.get('turned_deg', 0.), 1)}
        moved = self._escape_for_turn(rest, plan, cancel, 'the spin caught on something part-way round')
        if moved is None or moved['status'] != 'completed':
            return row
        self._turn_moved += abs(moved.get('travelled_m', 0.))
        retry = self._turn(rest, plan, cancel, verify, escape=False)
        return {**retry, 'requested_deg': round(degrees, 1), 'stalled_spin': stuck, 'escape': moved,
                'travelled_m': moved.get('travelled_m', 0.),
                'turned_deg': round(row['turned_deg'] + retry.get('turned_deg', 0.), 1)}

    @staticmethod
    def _bank_hold(space, sign, tof=None):
        """Why a bank may not start or go on: the way ahead or the front corner on the turning
        side closing, or the side it turns toward about to touch."""
        centre = space.get('path_center_m')
        if centre is None or 'front_depth_matrix_unavailable' in space['health']:
            return 'front_clearance_unknown'
        if centre - FRONT_MARGIN < .05:
            return 'path_blocked'
        key = 'left' if sign > 0 else 'right'
        corners = [v for v in (space.get('front_radial_' + key + '_m'), space.get('front_band_' + key + '_m')) if v is not None]
        if corners and min(corners) < BANK_CORNER_M:
            return 'turn_side_blocked_' + key
        near = (tof or {}).get('lf' if sign > 0 else 'rf')
        if isinstance(near, (int, float)) and 0 <= near < SCRAPE_M * 1000:
            return 'wall_too_close_' + key
        return None

    def _bank_room(self, space, degrees):
        """Open floor ahead for a bank toward degrees: the way ahead well past the stand-off."""
        sign = 1. if degrees > 0 else -1.
        if self._bank_hold(space, sign, (self.link.snapshot()[0] or {}).get('tof_mm')):
            return False
        sides = [space.get('path_center_m'), space.get('path_left_m' if sign > 0 else 'path_right_m')]
        return all(v is not None for v in sides) and min(sides) - FRONT_MARGIN >= BANK_ROOM_M

    def _banked(self, degrees, plan, cancel, row, why):
        """A bank, then any rest of the turn as a checked spin once clear of what was behind.
        None when there is no heading to steer the bank by (the caller spins as before)."""
        bank = self._bank(degrees, plan, cancel, {**row, 'banked': why})
        if bank is None:
            return None
        self._turn_moved += bank.get('travelled_m', 0.)
        if bank['status'] != 'completed' and bank['reason'] != 'bank_distance_limit':
            return bank
        rest = math.copysign(max(0., abs(degrees) - bank['turned_deg']), degrees)
        if abs(rest) < MIN_TURN:
            return {**bank, 'status': 'completed', 'reason': None}
        spin = self._turn(rest, plan, cancel, escape=False)
        return {**bank, 'status': spin['status'], 'reason': spin.get('reason'), 'finished_with_spin': spin,
                'turned_deg': round(bank['turned_deg'] + spin.get('turned_deg', 0.), 1)}

    def _bank(self, degrees, plan, cancel, row):
        """Drive forward while turning: an arc instead of a spin on the spot.

        Nearly straight for the first ten centimetres to roll off what is behind, then a
        0.3 m radius curve, easing off as the heading closes in, steered by the IMU heading.
        The front sensors and the turning side watch the floor it sweeps; it stops on a
        return there, the firmware's block, no headway for TURN_STALL_S or BANK_MAX_M."""
        start = self._telemetry()
        if self._heading(start) is None:
            return None
        self._ensure_parked(cancel)
        start = self._telemetry()
        last = self._heading(start)
        start_odom = dict(start.get('odom') or {})
        sign = 1. if degrees > 0 else -1.
        speed = min(BANK_SPEED, LIMITS[plan.origin]['speed_mps'] * self._pace)
        full = speed / BANK_RADIUS_M
        space = self.space()
        row = {**row, 'commanded_deg': round(degrees, 1), 'clearance_m': self._room_row(space)}
        def travelled_from(odom):
            return math.hypot(float(odom.get('x', 0)) - float(start_odom.get('x', 0)),
                              float(odom.get('y', 0)) - float(start_odom.get('y', 0)))
        status, reason = 'completed', None
        turned = travelled = 0.
        mark, progressed_at = (0., 0.), self.clock()
        self._wheels_on('turn', self._origin)
        try:
            seq = self.link.send({'cmd': 'drive', 'lin': round(speed, 3), 'ang': round(sign * BANK_LEAD_ANG, 3)})
            ack = self._await_ack(seq, .5, self.wait)
            if ack is not None and not ack.get('accepted'):
                return {**row, 'status': 'refused', 'reason': 'controller_refused_' + str(ack.get('reason')),
                        'turned_deg': 0., 'travelled_m': 0.}
            while True:
                self._check(cancel)
                if self.link.failure:
                    raise ProviderFailure(self.link.failure)
                telemetry, _ = self.link.snapshot()
                if not telemetry or not 0 <= self.clock() - telemetry['received_at'] <= .5:
                    status, reason = 'blocked', 'drive_telemetry_stale'
                    break
                self._track(telemetry)
                heading = self._heading(telemetry)
                if heading is not None:
                    turned += sign * wrap(heading - last)
                    last = heading
                travelled = travelled_from(telemetry.get('odom') or {})
                remaining = abs(degrees) - turned
                if remaining <= BANK_DONE_DEG:
                    break
                if travelled >= BANK_MAX_M:
                    status, reason = 'blocked', 'bank_distance_limit'
                    break
                if turned - mark[0] >= TURN_PROGRESS_DEG or travelled - mark[1] >= .01:
                    mark, progressed_at = (turned, travelled), self.clock()
                elif self.clock() - progressed_at > TURN_STALL_S:
                    status, reason = 'blocked', 'no_progress'
                    break
                if telemetry.get('blocked_dir') in ('front', 'both'):
                    status, reason = 'blocked', 'blocked'
                    break
                space = self.space()
                why = self._bank_hold(space, sign, telemetry.get('tof_mm'))
                if why:
                    status, reason = 'blocked', why
                    row['stopped_by'], row['clearance_m'] = why, self._room_row(space)
                    break
                ang = BANK_LEAD_ANG if travelled < BANK_LEAD_M else min(full, max(BANK_MIN_ANG, 2. * math.radians(remaining)))
                self.link.send({'cmd': 'drive', 'lin': round(speed, 3), 'ang': round(sign * ang, 3)})
                self.wait(CRAWL_TICK_S)
        finally:
            try:
                self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
                self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            if self.sounds:
                self.sounds.stop()
        self._view_since = self.clock() + .3
        self._sleep(.3, cancel)   # Let the base settle before heading and odometry are read.
        self._track()
        after = self.link.snapshot()[0] or {}
        heading = self._heading(after) if after else None
        if heading is not None:
            turned += sign * wrap(heading - last)
        travelled = max(travelled, travelled_from(after.get('odom') or {}))
        row.update(status=status, reason=reason, measured_deg=round(sign * turned, 1),
                   turned_deg=round(max(0., turned), 1), travelled_m=round(travelled, 2))
        self._step_trace(row)
        return row

    def _refused_turn(self, row, note):
        """October 5: refused turns left no trace, so a hallway run could not be read."""
        row = {**row, 'status': 'refused', 'reason': note}
        self._step_trace(row, self.space())   # October 13: refused turns kept no clearance to read.
        return row

    def _refused_move(self, row, note, space):
        """October 13: an escape back-up refused before it started left no drive_step at all."""
        row = {**row, 'status': 'refused', 'reason': note, 'travelled_m': 0., 'clearance_m': self._room_row(space)}
        self._step_trace(row)
        return row

    @staticmethod
    def _room_row(space):
        """The clearance that decided a step, kept in its receipt row (October 10: a crawl
        stopped after six centimetres and the log could not say which reading stopped it)."""
        keys = ('path_m', 'path_left_m', 'path_center_m', 'path_right_m', 'front_m', 'front_radial_left_m',
                'front_radial_right_m', 'left_m', 'right_m', 'rear_m')
        return {k: (round(space[k], 2) if isinstance(space.get(k), float) else space.get(k)) for k in keys}

    def _step_trace(self, row, space=None):
        """Every turn and move with where it left him: October 3, a trip turned 657 degrees
        and the log held only the total. space: the clearance it started from, for the log
        only (October 12: a turn caught a bookshelf and nothing said what the sensors saw)."""
        if self.trace:
            telemetry, _ = self.link.snapshot()
            heading = self._heading(telemetry) if telemetry else None
            extra = {'clearance_m': self._room_row(space)} if space is not None and 'clearance_m' not in row else {}
            self.trace.emit('drive_step', **{k: v for k, v in row.items() if k != 'legs'}, **extra,
                            heading_deg=round(heading, 1) if heading is not None else None,
                            at_m=[round(v, 2) for v in self._at])

    def _arms(self):
        """Arm reach for the swing check: tucked when the servo owner parks them before any
        wheel command of this plan; V2's arms-out estimate when their position is unknown."""
        return ARMS_TUCKED if self.servos is not None and self._plan_parks and not self._throttle_out else ARMS

    def _wheels_on(self, kind, origin):
        """A wheel command is going out: the whir loops while they run, and the plan is rolling."""
        self.rolled = self._plan_id
        if self.sounds:
            self.sounds.start(kind, origin)

    def _ensure_parked(self, cancel):
        """Arm in and neck centred right before the wheels first move, not at plan start:
        a plan that ends without moving makes no false 'about to go' gesture (September 27)."""
        if self._parked or self.servos is None or not self._plan_parks:
            return
        park = self.servos.prepare_base_motion(cancel)
        if park.status != 'pulses_reached':
            raise ArmParkFailed('ArmParkFailed:' + str(park.reason or park.status))
        self._parked = True
        self._throttle_out = park.reason == ARM_LEFT_OUT
        self._view_since = self.clock() + .3   # Only detections of the centred-neck view.
        self._sleep(.6, cancel)

    def _pose_held(self):
        owner = getattr(self.servos,'owner',None)
        active = getattr(owner,'human_hold_active',None)
        return active() if active else bool(getattr(owner,'held_channels',()))

    def _vision_budget(self):
        return SELF_VISION_CALLS if self._origin == 'self_initiated' else PLAN_VISION_CALLS

    @staticmethod
    def _short_side(space, need):
        """+1 left / -1 right when only that side of the straight path is short of need while
        straight ahead and the other side are clearly more open: a wall beside, not ahead."""
        rooms = {1: space.get('path_left_m'), 0: space.get('path_center_m'), -1: space.get('path_right_m')}
        if any(value is None for value in rooms.values()):
            return None
        for side in (1, -1):
            if (rooms[side] - FRONT_MARGIN < need and
                    min(rooms[0], rooms[-side]) >= rooms[side] + SIDE_OPEN_MORE_M):
                return side
        return None

    def _wall_turn(self, space, need):
        """Degrees to turn in place before rolling (+ left), 0 for none.

        Away from a wall that crosses one side of the path (_short_side), or from a wall
        within half a metre beside him that the side sensors say converges ahead. The size
        is the convergence angle plus a margin, between 8 and 30 degrees; 12 when the side
        sensors cannot tell (a door jamb ahead rather than a wall beside)."""
        tof = (self.link.snapshot()[0] or {}).get('tof_mm')
        side = self._short_side(space, need)
        surface = self._surface()
        if surface is not None and side in (None, surface['side']):
            # A slanted surface ahead with its open end on this side: turning away from what is
            # beside him here turns him to face the surface and hides its end from the depth
            # columns (November 17: "as he turns that way it appears like the door in front is a
            # blocker, but it's not"). The crawl's steering holds him off the side instead.
            return 0.
        if side is None:
            for candidate in (1, -1):
                near = space.get('left_m' if candidate > 0 else 'right_m')
                angle = wall_angle(tof, candidate)
                if (near is not None and near <= STRAIGHTEN_SIDE_M and angle is not None
                        and angle >= STRAIGHTEN_MIN_DEG - STRAIGHTEN_MARGIN_DEG):
                    side = candidate
                    break
            if side is None:
                return 0.
        # The front corner swings toward the side he turns to: its own sensor says how near that is.
        front_side = (tof or {}).get('rf' if side > 0 else 'lf')
        toward = (front_side / 1000. if isinstance(front_side, (int, float)) and front_side > 0
                  else space.get('right_m' if side > 0 else 'left_m'))
        if toward is not None and toward < STRAIGHTEN_TOWARD_MIN_M:
            # November 15 (7261d8ce): after the owner's "slight right" in a doorway, the right jamb in
            # the path turned him 8 degrees back left with the left jamb 0.23 m away, and the crawl
            # then stopped 6 cm from it. That close ahead on the side he would turn to, the crawl
            # steers between them instead (a 1.2 m hallway, 0.34-0.40 m a side, still straightens first).
            return 0.
        angle = wall_angle(tof, side)
        degrees = STRAIGHTEN_DEG if angle is None or angle <= 0 else angle + STRAIGHTEN_MARGIN_DEG
        return -side * max(STRAIGHTEN_MIN_DEG, min(STRAIGHTEN_MAX_DEG, degrees))

    def _straighten(self, need, plan, cancel):
        """Turn away from a wall beside the path before rolling forward.

        Owner, October 3: he should orient himself to avoid the walls before he moves.
        Turns in place, re-reading the sensors after each; never a reverse, and it stops
        as soon as no wall needs it, the turn is refused or the other side closes. A
        refused turn is not the end: the crawl that follows steers."""
        turned, first = 0., None
        for _ in range(MAX_STRAIGHTEN):
            degrees = self._wall_turn(self.space(), need)
            if not degrees or (first is not None and (degrees > 0) != (first > 0)):
                break
            first = degrees
            turn = self._turn(degrees, plan, cancel, escape=False)
            if turn['status'] != 'completed':
                break
            turned += math.copysign(turn.get('turned_deg', 0.), degrees)
        return turned

    @staticmethod
    def _speed_limit(space, top):
        """(how fast the room lets him roll forward, what set it), never above top (the step's
        paced speed); what set it is 'pace', 'sides', 'front_corner', 'ahead' or 'path_edge'.

        Owner, October 3: slower in tight hallways; November 14: much faster with nothing in the
        way; November 15: still too slow in the hallway. A wall beside him slows him only as it
        nears touching (SIDE_SCRAPE_M to SIDE_FREE_M), a front corner from CORNER_FREE_M; with no
        side readings he goes at HALLWAY_SPEED. In his path, something straight ahead allows only
        a speed that takes AHEAD_REACH_S to reach the front stand-off, and something at one edge
        only (a jamb or a corner the steering takes him past) EDGE_REACH_S to come inside the hold
        distance, so he slows while closing on a wall and while steering round a corner. The
        firmware still slows and stops him by its own zones."""
        def ramp(value, low, high):
            share = min(1., max(0., value - low) / (high - low))
            return SQUEEZE_SPEED + share * (OPEN_SPEED - SQUEEZE_SPEED)
        sides = [v for v in (space.get('left_m'), space.get('right_m')) if v is not None]
        corners = [v for v in (space.get('front_left_m'), space.get('front_right_m')) if v is not None]
        limits = [(top, 'pace'), (ramp(min(sides), SIDE_SCRAPE_M, SIDE_FREE_M) if sides else HALLWAY_SPEED, 'sides')]
        if corners:
            limits.append((ramp(min(corners), CORNER_SCRAPE_M, CORNER_FREE_M), 'front_corner'))
        centre, path = space.get('path_center_m'), space.get('path_m')
        if centre is not None:
            limits.append((max(SQUEEZE_SPEED, (centre - FRONT_MARGIN) / AHEAD_REACH_S), 'ahead'))
        if path is not None and (centre is None or path < centre):
            limits.append((max(SQUEEZE_SPEED, (path - WALL_HOLD_M) / EDGE_REACH_S), 'path_edge'))
        speed, why = min(limits, key=lambda row: row[0])
        return min(top, speed), why

    @classmethod
    def _room_speed(cls, space, top):
        return cls._speed_limit(space, top)[0]

    @classmethod
    def _crawl_speed(cls, space, top, ang):
        """(the room's speed for a steered roll through tight space, what set it), eased while
        steering hard so the turn takes effect over less distance."""
        speed, why = cls._speed_limit(space, top)
        return (speed, why) if abs(ang) < .3 else (max(CRAWL_MIN_SPEED, speed * .6), 'steering')

    @staticmethod
    def _tight(space, need):
        """Tight space for a forward move: a wall within half a metre of either side, a front
        corner close, or one side of the path limited while straight ahead is more open. The
        move is then a steered crawl rather than one firmware leg."""
        sides = [v for v in (space.get('left_m'), space.get('right_m')) if v is not None]
        corners = [v for v in (space.get('front_left_m'), space.get('front_right_m')) if v is not None]
        path, centre = space.get('path_m'), space.get('path_center_m')
        return bool((sides and min(sides) < HALLWAY_M) or (corners and min(corners) < TIGHT_CORNER_M)
                    or DriveBase._short_side(space, need) is not None
                    or (path is not None and centre is not None and centre - path >= SIDE_OPEN_MORE_M
                        and path - FRONT_MARGIN < need))

    @staticmethod
    def _crawl_hold(space, tof, surface=None):
        """Why a crawl may not go on: something in the path straight ahead, a return inside
        the hold distance on the path, or a side wall about to touch while it closes in.

        surface: what is ahead is one slanted surface with an open end (drive_space.front_surface).
        That is run along, not stopped short of like a wall facing him: the crawl goes on until
        his body would be SURFACE_HOLD_M from touching anything in front. Something else straight
        ahead (not part of that surface) keeps the ordinary front stand-off."""
        centre, path = space.get('path_center_m'), space.get('path_m')
        if centre is None or 'front_depth_matrix_unavailable' in space['health']:
            return 'front_clearance_unknown'
        if surface is not None:
            other = surface.get('ahead_other_m')
            if surface['room_m'] < SURFACE_HOLD_M or (other is not None and other - FRONT_MARGIN < .05):
                return 'path_blocked'
        elif centre - FRONT_MARGIN < .05:
            return 'path_blocked'
        elif path is not None and path < WALL_HOLD_M:
            return 'wall_beside_path_too_close'
        for side, key in ((1, 'left'), (-1, 'right')):
            near, corner = space.get(key + '_m'), space.get('front_radial_' + key + '_m')
            if near is not None and near < SCRAPE_M and ((corner is not None and corner < .25)
                                                         or (wall_angle(tof, side) or 0.) > 3.):
                return 'wall_too_close_' + key
        return None

    def _crawl_past(self, meters, plan, cancel, top, row):
        """A crawl that, stopped at a slanted surface with an open end, turns on the spot toward
        that end and rolls on (owner, November 17: inch forward, then turn, and he clears it).

        The turn is the one that leaves him running along the surface, cut to what the arm-swing
        check allows beside whatever stands next to him, and never a reverse. It ends when the
        distance is made, the crawl stops for anything else, a turn is refused or changes
        nothing, or SURFACE_TURNS are spent; the last stop is then the step's outcome."""
        out, passed = None, []
        while True:
            space, surface = self.space(), self._surface()
            held = self._crawl_hold(space, (self.link.snapshot()[0] or {}).get('tof_mm'), surface)
            if held and (held != 'path_blocked' or surface is None):
                break
            if not held:
                more = self._crawl(meters - (out or {}).get('travelled_m', 0.), plan, cancel, top, row)
                out = more if out is None else {
                    **more, 'commanded_m': out['commanded_m'],
                    'travelled_m': round(out['travelled_m'] + more.get('travelled_m', 0.), 2),
                    'steered_deg': round(out.get('steered_deg', 0.) + more.get('steered_deg', 0.), 1)}
                at_surface = more.get('stopped_by') == 'path_blocked' or (more['status'], more['reason']) == ('blocked', 'blocked')
                if not at_surface or meters - out['travelled_m'] <= .05:
                    break
                surface = self._surface()
            if surface is None or len(passed) >= SURFACE_TURNS:
                break
            turn = self._turn(surface['along_deg'], plan, cancel, escape=False,
                              remembered=self._remembered(self.link.snapshot()[0], self.clock()))
            turned = math.copysign(turn.get('turned_deg', 0.), surface['along_deg'])
            passed.append({'slant_deg': surface['slant_deg'], 'open_side': 'left' if surface['side'] > 0 else 'right',
                           'turned_deg': round(turned, 1)})
            if turn['status'] != 'completed' or abs(turned) < SURFACE_TURN_MIN_DEG:
                passed[-1]['reason'] = turn.get('reason') or turn['status']
                break
        if out is None:
            out = self._refused_move(row, held, space)
        if passed:
            out['turned_along_surface'] = passed
            out['turned_deg'] = round(out.get('turned_deg', 0.) + sum(abs(p['turned_deg']) for p in passed), 1)
        return out

    def _crawl(self, meters, plan, cancel, top, row):
        """A forward crawl through tight space as a continuous drive setpoint, at the room's
        speed under top (the step's paced speed).

        The firmware keeps steering him away from walls as it does on every move; the host
        no longer stops him for a wall beside the path and adds each tick what the firmware
        cannot read (drive_space.steer): the slant of a wall beside him and what the depth
        matrix sees crossing the path ahead. It runs until the distance is made, something
        is in the path ahead, a wall is about to touch, the firmware blocks, or the wheels
        stop making headway."""
        self._ensure_parked(cancel)
        start = self._telemetry()
        start_odom, start_heading = dict(start.get('odom') or {}), self._heading(start)
        row = {**row, 'commanded_m': round(meters, 2), 'slowed_for_tight_space': True, 'crawled': True}
        speeds = RollSpeeds()
        def travelled_from(odom):
            return math.hypot(float(odom.get('x', 0)) - float(start_odom.get('x', 0)),
                              float(odom.get('y', 0)) - float(start_odom.get('y', 0)))
        status, reason = 'completed', None
        travelled, progressed_at, last_mark = 0., self.clock(), 0.
        graces = [0]
        began_in_doorway = self._doorway(self.space())
        self._assist(True)
        self._wheels_on('move', self._origin)
        try:
            lin, why = self._speed_limit(self.space(), top)
            speeds.add(lin, why)
            self._speeds.add(lin, why)
            seq = self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': 0.})
            ack = self._await_ack(seq, .5, self.wait)
            if ack is not None and not ack.get('accepted'):
                return {**row, 'status': 'refused', 'reason': 'controller_refused_' + str(ack.get('reason')), 'travelled_m': 0.}
            while True:
                self._check(cancel)
                if self.link.failure:
                    raise ProviderFailure(self.link.failure)
                telemetry, _ = self.link.snapshot()
                if not telemetry or not 0 <= self.clock() - telemetry['received_at'] <= .5:
                    if self._telemetry_back(cancel, graces):
                        progressed_at = self.clock()   # The pause is not a lack of headway.
                        continue
                    status, reason = 'blocked', 'drive_telemetry_stale'
                    break
                self._track(telemetry)
                travelled = travelled_from(telemetry.get('odom') or {})
                if travelled >= meters - .02:
                    break
                if travelled - last_mark >= .01:
                    last_mark, progressed_at = travelled, self.clock()
                elif self.clock() - progressed_at > CRAWL_STALL_S:
                    status, reason = 'blocked', 'no_progress'
                    break
                if telemetry.get('blocked_dir') in ('front', 'both'):
                    status, reason = 'blocked', 'blocked'
                    break
                space = self.space()
                self._room_door(space)
                if self._kept_out(space, began_in_doorway):
                    status, reason = 'completed', 'kept_out_of_doorway'
                    break
                surface = self._surface()
                why = self._crawl_hold(space, telemetry.get('tof_mm'), surface)
                if why:
                    status, reason = 'blocked', why
                    row['stopped_by'], row['clearance_m'] = why, self._room_row(space)
                    break
                # Running along a slanted surface, the firmware's centring between the side sensors
                # outweighs anything the front can add (18 cm on his left against open floor on his
                # right is a 0.5 rad/s turn to the right, into the door): as when he turns into an
                # opening beside him, it is off for that stretch. Its push inside 13 cm stays, and so
                # does the host's own share from the side sensors.
                self._assist(surface is None)
                ang = steer(space, telemetry.get('tof_mm'), surface)
                lin, why = self._crawl_speed(space, top, ang)
                speeds.add(lin, why)
                self._speeds.add(lin, why)
                self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                self.wait(CRAWL_TICK_S)
        finally:
            try:
                self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
                self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            self._assist(True)
            if self.sounds:
                self.sounds.stop()
        self._view_since = self.clock() + .3
        self._sleep(.3, cancel)   # Let the base settle before odometry is read.
        self._track()
        after = self.link.snapshot()[0] or {}
        travelled = max(travelled, travelled_from(after.get('odom') or {}))
        if reason == 'path_blocked' and travelled >= .05:
            status, reason = 'completed', 'shortened_by_clearance'   # Went as far as the room ahead allowed.
        row.update(travelled_m=round(travelled, 2), status=status, reason=reason)
        if speeds.summary():
            row['roll_speed'] = speeds.summary()
        heading = self._heading(after) if after else None
        if start_heading is not None and heading is not None:
            row['steered_deg'] = round(wrap(heading - start_heading), 1)
        self._step_trace(row)
        return row

    def _move(self, meters, plan, cancel, straighten=True):
        row = {'action': 'move', 'requested_m': round(meters, 2)}
        need = min(meters, STRAIGHTEN_WITHIN_M)
        straightened = self._straighten(need, plan, cancel) if meters > 0 and straighten else 0.
        if straightened:
            # Reported so the model can say he lined up with the hallway first.
            row['turned_away_from_wall_deg'] = round(straightened, 1)
            row['turned_deg'] = abs(round(straightened, 1))
        top = LIMITS[plan.origin]['speed_mps'] * self._pace
        space = self.space()
        if meters > 0 and self._path_blocked(space, need) and self._doubtful(space, self.clock()):
            # Open floor was seen there a moment ago and nothing before: look again first.
            space = self._recheck('move', cancel)
            if self.trace:
                self.trace.emit('drive_reading_rechecked', where='move',
                                outcome='persisted' if self._path_blocked(space, need) else 'cleared')
        surface = self._surface() if meters > 0 else None
        if meters > 0 and (surface is not None or self._tight(space, need)):
            return self._crawl_past(meters, plan, cancel, top, row)
        allowed, note = move_allowance(meters, space)
        if not allowed:
            return self._refused_move(row, note, space)
        self._ensure_parked(cancel)
        space = self.space()
        allowed, note = move_allowance(meters, space)   # Clearance after the park settles.
        if not allowed:
            return self._refused_move(row, note, space)
        speed, why = self._speed_limit(space, top) if meters > 0 else (min(top, HALLWAY_SPEED), 'reverse')
        self._speeds.add(speed, why)
        start = self._telemetry().get('odom') or {}
        began = self._doorway(space)
        guard = ((lambda: 'kept_out_of_doorway' if self._kept_out(self.space(), began) else None)
                 if plan.origin == 'self_initiated' and meters > 0 else None)
        status, reason, done = self._finite({'cmd': 'move', 'dist': round(allowed, 2), 'speed': round(speed, 3)},
                                            max(8., abs(allowed)/speed + 10.), cancel, guard)
        if reason == 'kept_out_of_doorway':
            status, note = 'completed', reason   # His own move ends where a doorway or hall begins (November 22).
        end = (done or {}).get('odom') or self._telemetry().get('odom') or {}
        travelled = math.hypot(float(end.get('x', 0)) - float(start.get('x', 0)), float(end.get('y', 0)) - float(start.get('y', 0)))
        row = {**row, 'commanded_m': round(allowed, 2), 'travelled_m': round(travelled, 2), 'status': status,
               'reason': note if status == 'completed' else reason}
        if note or status != 'completed':
            row['clearance_m'] = self._room_row(space)
        self._track()
        self._step_trace(row)
        return row

    def _legs(self, meters, plan, cancel):
        """A long move as firmware legs of at most step_m, each against fresh clearance."""
        step = LIMITS[plan.origin]['step_m']
        if abs(meters) <= .05:
            return {'action': 'move', 'requested_m': round(meters, 2), 'travelled_m': 0., 'status': 'completed',
                    'reason': 'nothing_to_move'}
        remaining, rows = abs(meters), []
        while remaining > .05 and len(rows) < 16:
            leg = math.copysign(min(step, remaining), meters)
            rows.append(self._move(leg, plan, cancel))
            last = rows[-1]
            if last['status'] != 'completed' or last['reason'] in {'shortened_by_clearance', 'kept_out_of_doorway'}:
                break
            if last.get('crawled'):
                # A crawl through tight space: carry on from fresh readings while it makes headway.
                if last.get('travelled_m', 0.) < .05:
                    break
                remaining -= last['travelled_m']
                continue
            remaining -= abs(leg)
        row = {**rows[-1], 'requested_m': round(meters, 2), 'travelled_m': round(sum(r['travelled_m'] for r in rows), 2)}
        turned = sum(r.get('turned_deg', 0.) for r in rows)
        if turned:
            row['turned_deg'] = round(turned, 1)
            row['turned_away_from_wall_deg'] = round(sum(r.get('turned_away_from_wall_deg', 0.) for r in rows), 1)
        if any(r.get('slowed_for_tight_space') for r in rows):
            row['slowed_for_tight_space'] = True
        if any(r.get('crawled') for r in rows):
            row['crawled'] = True
            row['steered_deg'] = round(sum(r.get('steered_deg', 0.) for r in rows), 1)
        if len(rows) > 1:
            row['legs'] = rows
        if row['status'] == 'completed' and remaining > .05 and row.get('reason') != 'kept_out_of_doorway':
            row['reason'] = 'shortened_by_clearance'
        return row

    def _rotation(self):
        """Base rotation since the plan started, from the IMU heading (odometry without one).

        October 3: across twenty logged turns the IMU read the commanded angle plus a 3-10
        degree overshoot, while wheel odometry read up to twice that on left turns (wheel
        slip). Predicting where a target should be from odometry then turned him the wrong
        amount ("turned 86 degrees" looking for something 30 degrees away)."""
        telemetry, _ = self.link.snapshot()
        now = self._heading(telemetry) if telemetry else None
        return wrap(now - self._start_heading) if now is not None and self._start_heading is not None else 0.

    def _last_sought(self):
        """What an approach to "sought" re-finds: the thing being sought, else one just reached."""
        if self._sought:
            return self._sought['description']
        if self._reached and self.clock() - self._reached[1] <= REACHED_KEEP_S:
            return self._reached[0]
        return None

    def _predicted(self, offered):
        return wrap(offered['bearing_deg'] - self._rotation()) if offered.get('bearing_deg') is not None else None

    def _resolve(self, target, plan, now, fresh=True):
        """Re-find an offered target after motion: same ID, else same identity/label near
        where it should now be. Detections from before the latest motion are ignored."""
        offered = plan.targets.get(target) or {}
        kind = offered.get('kind') or ('nearest_person' if target == 'nearest_person' else None)
        since = self._view_since if fresh else float('-inf')
        predicted = self._predicted(offered)
        def near(rows, key):
            if predicted is None:
                return rows[0] if len(rows) == 1 else None
            placed = [(b, r) for r in rows if (b := self._seen_bearing(r)) is not None]
            close = [(b, r) for b, r in placed if abs(wrap(b - predicted)) <= 20]
            return min(close, key=lambda c: abs(wrap(c[0] - predicted)), default=(None, None))[1]
        if kind in {'face', 'nearest_person'}:
            faces = [f for f in self._fresh_faces(now) if f['at'] >= since]
            if kind == 'nearest_person':
                match = max(faces, key=lambda f: f['box'][2]*f['box'][3], default=None)
            else:
                match = next((f for f in faces if f['track_id'] == target), None)
                identity = offered.get('person_id')
                if match is None and identity:
                    same = [f for f in faces if f.get('identity') == identity]
                    match = same[0] if len(same) == 1 else near(same, 'identity')
                if match is None and not identity:
                    match = near(faces, 'face')  # Track IDs change as the view rotates.
            bearing = self._seen_bearing(match) if match is not None else None
            if bearing is None:
                return None
            return {'kind': 'face', 'bearing_deg': bearing, 'range_m': face_range(match['box']),
                    'box_width': match['box'][2], 'track_id': match['track_id'], 'person_id': match.get('identity')}
        if kind == 'object':
            source = self.object_tracks or self.objects
            rows = [r for r in (source() if source else ()) if r.get('at', now) >= since]
            match = next((r for r in rows if r['target_id'] == target), None)
            if match is None:
                match = near([r for r in rows if r['label'] == offered.get('label')], 'label')
            bearing = self._seen_bearing(match) if match is not None else None
            if bearing is None:
                return None
            return {'kind': 'object', 'bearing_deg': bearing, 'range_m': None,
                    'box_width': match['box'][2], 'label': match['label']}
        if kind == 'radar_body':
            bodies = (self.radar.bodies() if self.radar else None) or []
            expected = predicted if predicted is not None else offered['bearing_deg']
            match = min((b for b in bodies if abs(wrap(b['bearing_deg'] - expected)) <= 25),
                        key=lambda b: abs(wrap(b['bearing_deg'] - expected)), default=None)
            return {'kind': 'radar_body', 'bearing_deg': match['bearing_deg'] if match else expected,
                    'range_m': (match or offered).get('range_m'), 'current': match is not None}
        if kind == 'voice_direction':
            return {'kind': 'voice_direction', 'bearing_deg': predicted, 'range_m': None}
        return None

    def _acquire(self, step, plan, cancel, timeout=2.5):
        """Wait for a detection from after the latest motion (objects refresh every 1.5 s)."""
        deadline = self.clock() + timeout
        while True:
            target = self._resolve(step.target, plan, self.clock())
            if target is not None or self.clock() >= deadline:
                return target
            self._sleep(.25, cancel)

    def _find_offered(self, step, plan, cancel):
        """Acquire a camera target; if unseen, turn to where it should be and look again."""
        offered = plan.targets.get(step.target) or {}
        anyone = step.target == 'nearest_person'
        target = self._acquire(step, plan, cancel, timeout=1.2 if anyone and not offered.get('visible_now', True) else 2.5)
        if target is not None:
            return target, 0., None
        if anyone and self._directed and offered.get('visible_now') is False:
            # "Turn right and come to me": the turn they asked for points at them; nobody in view
            # means they are too far for a face, not somewhere else. Straight ahead, never a search.
            # A person in view when the plan was made is looked for again instead (October: a turn
            # the model added before "come here" left him facing away from someone he had seen).
            return {'kind': 'voice_direction', 'bearing_deg': 0., 'range_m': None, 'basis': 'directed_turn'}, 0., None
        if anyone:
            # "Come here" with nobody in view is a request to find the person, never a
            # refusal (October 3: refused once, then given up on after a wrong turn).
            # His own beat looks only where a voice or the radar says someone is (owner, November 16:
            # "driving in circles for no reason"; a planned roll toward Bret, who had left the room,
            # swung him 356 degrees).
            search = self._find_person(DriveStep('find_person'), plan, cancel)
            target = self._acquire(step, plan, cancel) if search['status'] == 'completed' else None
            missing = (None if target else 'nobody_in_view_to_go_to' if search['reason'] == 'nowhere_to_look'
                       else 'no_person_found_after_looking_around')
            return target, search.get('turned_deg', 0.), missing
        predicted = self._predicted(offered)
        if predicted is None or offered.get('kind') not in {'face', 'object'}:
            return None, 0., 'target_not_currently_detected'
        turned = 0.
        if abs(predicted) > 10:
            turn = self._turn(max(-180., min(180., predicted)), plan, cancel, bank=step.action == 'approach')
            turned = turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                return None, turned, 'turn_toward_target_' + turn['status']
        target = self._acquire(step, plan, cancel, timeout=3.)
        return target, turned, None if target else 'target_not_found_where_it_was_seen'

    def _face_target(self, step, plan, cancel):
        row = {'action': 'face', 'target': step.target}
        target, turned, missing = self._find_offered(step, plan, cancel)
        if target is None:
            return {**row, 'status': 'lost' if turned else 'refused', 'reason': missing, 'turned_deg': turned}
        for attempt in range(3):
            bearing = wrap(target['bearing_deg'])
            if abs(bearing) <= 10:
                break
            turn = self._turn(max(-180., min(180., bearing)), plan, cancel, bank=step.action == 'approach')
            turned += turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                return {**row, **{k: v for k, v in turn.items() if k != 'action'}, 'turned_deg': turned}
            if target['kind'] not in {'face', 'object'}:
                self._sleep(1., cancel)
                break  # Priors: one turn toward them, then report what the camera sees.
            target = self._acquire(step, plan, cancel)
            if target is None:
                return {**row, 'status': 'lost', 'reason': 'target_not_redetected_after_turn', 'turned_deg': turned}
        faces = self._fresh_faces(self.clock())
        reason = None
        if target['kind'] not in CAMERA_KINDS and not faces:
            # October 19 (b4ce6464): a 0 degree "face me" on a remembered voice with nobody in
            # view became "There, I've turned to face you". The receipt says what was done.
            reason = ('already_facing_voice_direction_no_face_in_view' if abs(turned) < 5
                      else 'turned_toward_voice_direction_no_face_in_view')
        return {**row, 'status': 'completed', 'reason': reason, 'turned_deg': turned,
                'final_bearing_deg': round(wrap(target['bearing_deg'])) if target['kind'] in CAMERA_KINDS else None,
                'faces_visible_after': len(faces)}

    def _approach(self, step, plan, cancel, budget_m):
        row = {'action': 'approach', 'target': step.target}
        target, turned, missing = self._find_offered(step, plan, cancel)
        if target is None:
            return {**row, 'status': 'lost' if turned else 'refused', 'reason': missing, 'travelled_m': 0., 'turned_deg': turned}
        kind = target['kind']
        stop_at = step.stop_at_m or DEFAULT_STOP_AT[kind]
        travelled, extra = 0., {}
        if kind in {'radar_body', 'voice_direction'}:
            # Priors: turn toward them, then approach a face the camera actually sees.
            facing = ({'status': 'completed', 'turned_deg': 0.} if target.get('basis') == 'directed_turn'
                      else self._face_target(step, plan, cancel))
            turned = facing.get('turned_deg', 0.)
            if facing['status'] != 'completed':
                return {**row, **{k: v for k, v in facing.items() if k not in {'action', 'target'}}, 'travelled_m': 0.}
            ahead = self._faces_ahead()
            if not ahead and kind == 'voice_direction':
                # Owner, September 27: "come towards me" needs only a voice. Roll toward it and
                # switch to the face approach as soon as a face comes into view.
                directed = target.get('basis') == 'directed_turn'
                moved, outcome = self._toward_voice(plan, cancel, min(budget_m, DIRECTED_LEG_M if directed else VOICE_LEG_M))
                travelled += moved
                ahead = self._faces_ahead()
                if not ahead:
                    where = 'ahead where they turned me' if directed else 'toward the voice'
                    return {**row, 'status': 'blocked' if outcome == 'blocked' else 'completed',
                            'reason': (f'path blocked on the way {where}; no face seen' if outcome == 'blocked'
                                       else f'moved {where}; no face seen'),
                            'travelled_m': round(travelled, 2), 'turned_deg': turned,
                            **({'directed_by_turn': True} if directed else {'toward_voice': True})}
                extra['toward_voice_m'] = round(moved, 2)
            if ahead:
                step = DriveStep('approach', target=max(ahead, key=lambda f: f['box'][2])['track_id'], stop_at_m=stop_at)
                plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose,
                                 {**plan.targets, step.target: {'kind': 'face'}}, plan.expires_at)
                target, kind = self._resolve(step.target, plan, self.clock()), 'face'
                stop_at = step.stop_at_m or DEFAULT_STOP_AT['face']
            elif target.get('range_m'):
                distance = min(budget_m, target['range_m'] - stop_at)
                if distance < .05:
                    return {**row, 'status': 'completed', 'reason': 'already near radar range; no face in view',
                            'travelled_m': 0., 'turned_deg': turned}
                move = self._move(distance, plan, cancel)
                return {**row, 'status': move['status'], 'reason': 'no face in view; moved toward radar range',
                        'travelled_m': move.get('travelled_m', 0.), 'turned_deg': turned}
            else:
                return {**row, 'status': 'lost', 'reason': 'no_face_in_view_after_turn', 'travelled_m': 0., 'turned_deg': turned}
        if target is None:
            return {**row, 'status': 'lost', 'reason': 'target_not_redetected', 'travelled_m': round(travelled, 2), 'turned_deg': turned}
        if abs(target['bearing_deg']) > 12:
            facing = self._turn(max(-180., min(180., target['bearing_deg'])), plan, cancel, bank=True)
            turned += facing.get('turned_deg', 0.)
            if facing['status'] != 'completed':
                return {**row, **{k: v for k, v in facing.items() if k not in {'action'}}, 'travelled_m': 0., 'turned_deg': turned}
            self._sleep(.8, cancel)
        detours, reacquired, tried = [], [], []
        while True:
            result, reason, moved, last = self._run_approach(step, plan, cancel, kind, stop_at, budget_m - travelled)
            travelled += moved
            if (result == 'lost' and kind in {'face', 'nearest_person'} and len(reacquired) < MAX_REACQUIRES
                    and budget_m - travelled > .1):
                # Owner, September 27: after the firmware nudged him around an obstacle the person
                # left the frame. Look with the head first, then turn the body, then carry on.
                found = self._reacquire(plan, cancel, (last or {}).get('heading'))
                reacquired.append(found['how'] if found else 'not_found')
                turned += found['turned_deg'] if found else 0.
                if found:
                    step = DriveStep('approach', target=found['track_id'], stop_at_m=stop_at)
                    plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose,
                                     {**plan.targets, step.target: {'kind': 'face'}}, plan.expires_at)
                    kind = 'face'
                    continue
                reason = 'person not found again after looking'
            far = (last is not None and last.get('range_m') is not None and last['range_m'] - stop_at > .5)
            if result != 'blocked' or kind not in {'face', 'nearest_person'} or not far:
                break
            # September 27: a trash can ahead-left stopped "come to me" with the right side open.
            # October 3: a table across the whole front, open floor beside it. Go around on a
            # leg the camera or the sensors choose, then turn back to where the person is.
            telemetry, _ = self.link.snapshot()
            heading = self._heading(telemetry) if telemetry else None
            person_heading = wrap(heading + last['bearing_deg']) if heading is not None else None
            detour = self._go_around('the person he is driving to', last['bearing_deg'], self.space(), FRONT_MARGIN,
                                     plan, cancel, budget_m - travelled, detours, tried)
            if detour is None:
                break
            turned += detour['turned_deg']
            travelled += detour['travelled_m']
            detours.append({k: detour[k] for k in ('side', 'travelled_m', 'status', 'by', 'note', 'escape') if k in detour})
            if detour['by'] == 'sensors' and detour['status'] != 'completed':
                break
            back, _ = self._bearing_to(last.get('position'))
            if back is None:
                now_heading = self._heading(self.link.snapshot()[0] or {}) if self.link.snapshot()[0] else None
                back = wrap(person_heading - now_heading) if person_heading is not None and now_heading is not None else None
            if back is not None and abs(back) > 10:
                facing = self._turn(max(-180., min(180., back)), plan, cancel, escape=False)
                turned += facing.get('turned_deg', 0.)
            found = self._acquire(step, plan, cancel) or self._face_near(0., cancel)
            if found is None:
                result, reason = 'lost', 'person_not_refound_after_going_around'
                break
            if abs(found['bearing_deg']) > 12:
                # The turn back may have been cut short beside something: square up on the face itself.
                facing = self._turn(max(-90., min(90., found['bearing_deg'])), plan, cancel, escape=False)
                turned += facing.get('turned_deg', 0.)
                self._sleep(.8, cancel)
            if found.get('track_id') and found['track_id'] != step.target and step.target != 'nearest_person':
                # The track restarted while he moved: carry on with the face now ahead.
                step = DriveStep('approach', target=found['track_id'], stop_at_m=stop_at)
                plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose,
                                 {**plan.targets, step.target: {'kind': 'face'}}, plan.expires_at)
                kind = 'face'
        final = self._resolve(step.target, plan, self.clock(), fresh=False)
        space = self.space()
        reach = (final or last or {}).get('range_m')
        if result in {'blocked', 'timeout'} and kind in {'face', 'nearest_person'} and reach is not None and reach - stop_at <= .3:
            # Stopped by a table, knees or the clock while within arm's reach of the stop
            # distance: he is there. October 3: three such arrivals (0.9-1.2 m from the person)
            # were told as "I didn't make it to you".
            result, reason = 'completed', 'stopped within reach of the person: ' + str(reason)
        out = {**row, 'status': result, 'reason': reason, 'travelled_m': round(travelled, 2),
               'turned_deg': turned, 'stop_at_m': stop_at, **extra,
               'clearance_at_stop_m': {k: (round(v, 2) if isinstance(v, float) else v) for k, v in space.items()},
               'final_approx_range_m': round(final['range_m'], 1) if final and final.get('range_m') else None}
        if detours:
            out['detours'] = detours
        if reacquired:
            out['refound_by'] = reacquired
        return out

    def _head(self, pose, cancel, request_id):
        """A head move the drive owner makes for its own looking. It is not a person's pose:
        October 3, each such look started the 90-second "a person posed me" hold, which then
        refused Rex's own moves and the back-off reflex after every route look."""
        move = getattr(self.servos, 'drive_look', None) or self.servos.execute_pose
        return move(pose, cancel, request_id)

    def _look(self, degrees, cancel):
        """Point the head (neck) degrees left of the base (0 recentres); False without servos."""
        if self.servos is None or not hasattr(self.servos, 'execute_pose'):
            return False
        from .drive_space import neck_pulse
        degrees = max(-LOOK_NECK_MAX, min(LOOK_NECK_MAX, degrees))
        receipt = self._head({0: neck_pulse(degrees)}, cancel, 'drive-look')
        self._view_since = self.clock() + HEAD_SETTLE_S   # A hosted look waits for the head to be still.
        return getattr(receipt, 'status', None) == 'pulses_reached'

    def _face_near(self, expected, cancel, wait=1.5):
        """A fresh face near the expected base-relative bearing, waiting for detections."""
        deadline = self.clock() + wait
        while True:
            faces = [f for f in self._fresh_faces(self.clock()) if f['at'] >= self._view_since]
            placed = [(b, f) for f in faces if (b := self._seen_bearing(f)) is not None]
            rows = [(abs(wrap(b - expected)), b, f) for b, f in placed]
            rows = [r for r in rows if r[0] <= 45]
            if rows:
                _, bearing, face = min(rows, key=lambda r: r[0])
                return {'track_id': face['track_id'], 'bearing_deg': bearing}
            if self.clock() >= deadline:
                return None
            self._sleep(.25, cancel)

    def _reacquire(self, plan, cancel, person_heading):
        """Find a person lost mid-approach: head look toward where they should be, then glances,
        then a body turn; on success face them with the body and recentre the head."""
        telemetry = self._telemetry()
        heading = self._heading(telemetry)
        expected = wrap(person_heading - heading) if person_heading is not None and heading is not None else 0.
        found, how, turned = None, None, 0.
        for degrees in (expected, expected + 30., expected - 30.):
            if abs(degrees) > LOOK_NECK_MAX + 10 or not self._look(degrees, cancel):
                continue
            found = self._face_near(expected, cancel)
            if found:
                how = 'head_look'
                break
        self._look(0., cancel)   # Recentred: bearings below are base-relative.
        if found is None and abs(expected) > 10:
            turn = self._turn(max(-180., min(180., expected)), plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            if turn['status'] == 'completed':
                self._sleep(.8, cancel)
                found = self._face_near(0., cancel)
                how = 'body_turn'
        if found is None:
            return None
        if how == 'head_look' and abs(found['bearing_deg']) > 12:
            turn = self._turn(max(-180., min(180., found['bearing_deg'])), plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            self._sleep(.8, cancel)
            refound = self._face_near(0., cancel)
            if refound is None:
                return None
            found = refound
        return {**found, 'how': how, 'turned_deg': turned}

    def _faces_ahead(self):
        return [f for f in self._fresh_faces(self.clock())
                if (b := self._seen_bearing(f)) is not None and abs(b) <= 20]

    def _run_approach(self, step, plan, cancel, kind, stop_at, budget_m):
        """One closed-loop run toward a camera target; returns (result, reason, travel, last seen)."""
        self._ensure_parked(cancel)
        controller = Approach(self.clock(), 'face' if kind in {'face', 'nearest_person'} else 'object',
                              stop_at, LIMITS[plan.origin]['speed_mps'] * self._pace, budget_m)
        result, reason, last = None, None, None
        ease = Ease()
        self._wheels_on('move', self._origin)
        try:
            while result is None:
                self._check(cancel)
                now = self.clock()
                telemetry = self._telemetry()
                ok, why = self.availability(now)
                if not ok:
                    result, reason = 'stopped', why
                    break
                self._view_since = now - 1.6   # Continuous motion: the latest detections are the view.
                current = self._resolve(step.target, plan, now)
                if current is not None:
                    heading = self._heading(telemetry)
                    # Absolute heading of the person: firmware steering around obstacles
                    # turns the base, and this says which way to look when they drop out.
                    last = {**current, 'heading': wrap(heading + current['bearing_deg']) if heading is not None else None}
                    if last['heading'] is not None and current.get('range_m'):
                        # Where the person is in the room, so he can turn back to them after going around something.
                        last['position'] = (self._at[0] + current['range_m']*math.cos(math.radians(last['heading'])),
                                            self._at[1] + current['range_m']*math.sin(math.radians(last['heading'])))
                space = self.space(now)
                lin, ang, result, reason = controller.step(now, telemetry, space, current)
                room_speed, why = self._speed_limit(space, controller.speed)
                if lin < room_speed:
                    why = 'stand_off'
                wanted = min(lin, max(.05, room_speed))
                lin = ease(now, wanted, max(0., float((telemetry.get('odom') or {}).get('lin', 0))))
                if result is None:
                    self._speeds.add(lin, why if lin >= .95 * wanted else 'easing_in')
                if current is not None:
                    # Near the target the firmware must slow him, not steer him off it.
                    self._assist(not self._near_target(space, current['bearing_deg'], current.get('range_m'), stop_at))
                if result is None:
                    self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                    self.wait(.15)
        finally:
            self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
            self.link.send({'cmd': 'stop'})
            self._assist(True)
            if self.sounds:
                self.sounds.stop()
        return result, reason, controller.travel, last

    def _detour(self, side, plan, cancel, budget_m):
        """Turn toward the open side (30°, 55°, then 85° while it is still in the path) and roll past."""
        turned = 0.
        for degrees in (DETOUR_DEG, DETOUR_MORE_DEG, DETOUR_DEG):
            # October 13: a turn toward open floor refused for a return 0.55 m off the front
            # corner ended the approach; told to back up and turn, he made it. Earn room first.
            turn = self._turn(side * degrees, plan, cancel)
            turned += turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                return {'side': 'left' if side > 0 else 'right', 'status': 'blocked', 'travelled_m': 0., 'turned_deg': turned}
            if move_allowance(DETOUR_M, self.space())[0] >= .15:
                break
        move = self._move(min(DETOUR_M, budget_m), plan, cancel)
        return {'side': 'left' if side > 0 else 'right', 'status': move['status'],
                'travelled_m': move.get('travelled_m', 0.), 'turned_deg': turned}

    def _route_look(self, cancel):
        """A fresh sharp picture with the head tipped slightly down, so the floor and what
        stands on it are in view; the head returns to its level drive stance afterwards."""
        tipped = False
        owner = self.servos
        if owner is not None and hasattr(owner, 'execute_pose'):
            from .servo_poses import DRIVE_HEAD, HEAD_PAIR, NECK_CENTER, head_pulses, pitch_from_degrees
            level = dict(DRIVE_HEAD)
            down = {0: NECK_CENTER, **head_pulses(pitch_from_degrees(-ROUTE_LOOK_DOWN_DEG), 0.)}
            tipped = getattr(self._head(down, cancel, 'drive-route-look'), 'status', None) == 'pulses_reached'
            self._view_since = self.clock() + HEAD_SETTLE_S
        self._sleep(HEAD_SETTLE_S if tipped else .5, cancel)
        try:
            return self.look(max(self._view_since, self.clock() - .5))
        finally:
            if tipped:
                self._head({ch: level[ch] for ch in HEAD_PAIR}, cancel, 'drive-route-look')
                self._view_since = self.clock() + .3

    def _route_leg(self, goal, goal_bearing, plan, cancel, budget_m, tried, in_view=True):
        """One leg around what blocks the way, chosen from a picture of the floor and the
        range sensors by direction. None when no look is possible or it shows no way.

        The hosted look only proposes a direction and a rough distance. The turn still
        passes the arm-swing check and the move the same clearance, wall and firmware
        guards as any other, so a wrong proposal ends as a short or refused leg."""
        if (self.router is None or self.look is None or budget_m < .2
                or self._vision_calls >= self._vision_budget()):
            return None
        image = self._route_look(cancel)
        if image is None:
            return None
        from .drive_space import HALF_FOV, open_directions
        now = self.clock()
        telemetry, matrix = self.link.snapshot()
        space = self.space(now)
        facts = {'goal': goal, 'goal_in_view': bool(in_view),
                 'goal_bearing_deg': round(goal_bearing) if goal_bearing is not None else None,
                 'goal_image_x': (round(.5 - goal_bearing/(2*HALF_FOV), 2)
                                  if goal_bearing is not None and abs(goal_bearing) < HALF_FOV else None),
                 'blocked_ahead_m': space.get('path_m'), 'robot_width_m': .55,
                 'path_by_side_m': {side: space.get(f'path_{side}_m') for side in ('left', 'center', 'right')},
                 'open_by_direction': open_directions(telemetry, matrix, now),
                 'already_tried': tried[-3:]}
        self._vision_calls += 1
        try:
            choice = self.router(image, facts, cancel)
        except ProviderFailure as exc:
            tried.append('the look failed: ' + str(exc))
            return None
        if self.trace:
            self.trace.emit('drive_route_look', goal=goal, facts=facts, choice=choice)
        if not choice.get('way') or choice.get('confidence') == 'low':
            tried.append('no way seen: ' + str(choice.get('note') or '')[:80])
            return None
        beyond = choice.get('beyond_view')
        if beyond in ('left', 'right'):
            bearing, distance = (ROUTE_BEYOND_VIEW_DEG if beyond == 'left' else -ROUTE_BEYOND_VIEW_DEG), ROUTE_LEG_M
        else:
            bearing = (.5 - float(choice['x'])) * 2 * HALF_FOV
            distance = max(.3, min(ROUTE_LEG_M, float(choice.get('distance_m') or ROUTE_LEG_M)))
        said = f"{abs(bearing):.0f} degrees {'left' if bearing > 0 else 'right'}, {distance:.1f} m"
        heading = self._heading(telemetry) if telemetry else None
        way = wrap(heading + bearing) if heading is not None else None
        self._track(telemetry)
        if way is not None and any(math.hypot(self._at[0] - x, self._at[1] - y) < .3 and abs(wrap(way - failed)) < 20.
                                   for x, y, failed in self._dead_ends):
            # October 3 evening: sent right three times from one spot after that way had
            # already been refused. The same way from the same place is not tried again.
            tried.append(said + ': already failed from here; not repeated')
            return None
        row = {'side': 'left' if bearing > 0 else 'right', 'by': 'camera', 'note': str(choice.get('note') or '')[:120],
               'travelled_m': 0., 'turned_deg': 0.}
        if abs(bearing) > 8:
            # Banks forward or backs up to earn room when the spin is refused (October 13: "go
            # right around the couch" was refused at 0.55 m and the approach gave up 1.7 m short).
            turn = self._turn(bearing, plan, cancel)
            row['turned_deg'] = turn.get('turned_deg', 0.)
            if turn.get('escape'):
                row['escape'] = turn['escape'].get('why')   # Its distance is counted with the step's turns.
            if turn['status'] != 'completed':
                tried.append(said + ': could not turn that way (' + str(turn.get('reason')) + ')')
                if way is not None:
                    self._dead_ends.append((*self._at, way))
                return {**row, 'status': 'blocked'}
        # No straightening here: the look chose this direction, and turning away from the
        # wall beside it only undid the turn (three 12 degree turns back, then no move).
        move = self._move(min(distance, budget_m), plan, cancel, straighten=False)
        row['travelled_m'] += move.get('travelled_m', 0.)
        row['turned_deg'] += move.get('turned_deg', 0.)
        if row['travelled_m'] < .25 and way is not None:
            self._dead_ends.append((self._at[0] - row['travelled_m']*math.cos(math.radians(way)),
                                    self._at[1] - row['travelled_m']*math.sin(math.radians(way)), way))
        tried.append(said + f": rolled {row['travelled_m']:.1f} m, {move['status']}"
                     + (' (' + str(move['reason']) + ')' if move.get('reason') else ''))
        return {**row, 'status': move['status']}

    def _surface_leg(self, plan, cancel, budget_m, tried):
        """A leg along a slanted surface toward its open end, from the depth columns alone (owner,
        November 14: the camera is for obstacles and path changes; November 17: the door standing
        open at an angle had room on the left that the columns already showed). None when they
        show no such surface or the leg makes no headway; the camera is asked then, as before."""
        surface = self._surface()
        if surface is None or budget_m < .2:
            return None
        side = 'left' if surface['side'] > 0 else 'right'
        if self.trace:
            self.trace.emit('drive_surface_leg', surface=surface)
        move = self._move(min(SURFACE_LEG_M, budget_m), plan, cancel)
        said = f"along a surface slanted {abs(surface['slant_deg']):.0f} degrees toward its open {side} end"
        tried.append(said + f": rolled {move.get('travelled_m', 0.):.1f} m, {move['status']}"
                     + (' (' + str(move['reason']) + ')' if move.get('reason') else ''))
        if move.get('travelled_m', 0.) < .05 and not move.get('turned_along_surface'):
            return None
        return {'side': side, 'by': 'surface', 'status': move['status'], 'travelled_m': move.get('travelled_m', 0.),
                'turned_deg': move.get('turned_deg', 0.), 'note': said}

    def _memory_leg(self, goal_bearing, plan, cancel, budget_m, tried, side):
        """A leg around the same obstacle over floor the sensors already saw open, nearest the
        target's direction, without a new hosted look. None when memory shows no such way."""
        if budget_m < .3:
            return None
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        self._track(telemetry)
        now = self.clock()
        bearing = self.local_map.open_way(tuple(self._at), heading, goal_bearing, MEMORY_LEG_M, now, side=side)
        if bearing is None:
            return None
        way = wrap(heading + bearing)
        if any(math.hypot(self._at[0] - x, self._at[1] - y) < .3 and abs(wrap(way - failed)) < 20.
               for x, y, failed in self._dead_ends):
            return None
        said = f"{abs(bearing):.0f} degrees {'left' if bearing > 0 else 'right'} over floor already seen open"
        if self.trace:
            self.trace.emit('drive_memory_leg', goal_bearing_deg=goal_bearing, bearing_deg=round(bearing, 1),
                            memory=self.local_map.summary(now))
        row = {'side': 'left' if bearing > 0 else 'right', 'by': 'memory', 'travelled_m': 0., 'turned_deg': 0.}
        if abs(bearing) > 8:
            turn = self._turn(bearing, plan, cancel)
            row['turned_deg'] = turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                tried.append(said + ': could not turn that way')
                self._dead_ends.append((*self._at, way))
                return {**row, 'status': 'blocked'}
        move = self._move(min(MEMORY_LEG_M, budget_m), plan, cancel, straighten=False)
        row['travelled_m'] += move.get('travelled_m', 0.)
        if row['travelled_m'] < .2:
            self._dead_ends.append((*self._at, way))
        tried.append(said + f": rolled {row['travelled_m']:.1f} m, {move['status']}")
        return {**row, 'status': move['status']}

    def _go_around(self, goal, bearing, space, stand_off, plan, cancel, budget_m, detours, tried, in_view=True):
        """The next leg around an obstacle: along a slanted surface toward its open end when the
        depth columns show one, else chosen by the camera when it can be, otherwise from the
        range sensors alone. None when none shows a way or the tries are spent."""
        if sum(d.get('by') == 'surface' for d in detours) < SURFACE_LEGS:
            leg = self._surface_leg(plan, cancel, budget_m, tried)
            if leg is not None:
                return leg
        if (any(d.get('by') == 'camera' for d in detours)
                and sum(d.get('by') == 'memory' for d in detours) < MEMORY_LEGS):
            side = 1 if next(d for d in reversed(detours) if d.get('by') in {'camera', 'memory'})['side'] == 'left' else -1
            leg = self._memory_leg(bearing, plan, cancel, budget_m, tried, side)
            if leg is not None:
                return leg
        if sum(d.get('by') == 'camera' for d in detours) < ROUTE_LEGS:
            leg = self._route_leg(goal, bearing, plan, cancel, budget_m, tried, in_view)
            if leg is not None:
                return leg
        if sum(d.get('by') != 'camera' for d in detours) >= MAX_DETOURS:
            return None
        side = self._open_side(space, stand_off, bearing if bearing is not None else 0.)
        if side is None:
            return None
        return {**self._detour(side, plan, cancel, budget_m), 'by': 'sensors'}

    def _toward_voice(self, plan, cancel, distance_m):
        """Roll straight on the current heading toward a voice until a face comes into view,
        the path closes, or distance_m; returns (travelled, outcome)."""
        self._ensure_parked(cancel)
        start = self._telemetry().get('odom') or {}
        hold = self._heading(self._telemetry())
        speed = LIMITS[plan.origin]['speed_mps'] * self._pace
        outcome, travelled = 'distance', 0.
        ease = Ease()
        graces = [0]
        self._wheels_on('move', self._origin)
        try:
            while True:
                self._check(cancel)
                now = self.clock()
                ok, why = self.availability(now)
                if not ok and why == 'drive_telemetry_stale' and self._telemetry_back(cancel, graces):
                    continue
                if not ok:
                    outcome = 'stopped'
                    break
                telemetry = self._telemetry()
                odom = telemetry.get('odom') or {}
                travelled = math.hypot(float(odom.get('x', 0)) - float(start.get('x', 0)),
                                       float(odom.get('y', 0)) - float(start.get('y', 0)))
                if travelled >= distance_m - .05:
                    break
                self._view_since = now - 1.6
                if self._faces_ahead():
                    outcome = 'face'
                    break
                room = (self.space(now).get('path_m') or 0.) - FRONT_MARGIN
                speed_now = max(0., float(odom.get('lin', 0)))
                if room <= max(.03, speed_now*speed_now/(2*CONFIG['accel_lin']) + speed_now*.3):
                    outcome = 'blocked'
                    break
                heading = self._heading(telemetry)
                ang = 0. if hold is None or heading is None else max(-.2, min(.2, math.radians(wrap(hold - heading))*1.2))
                lin, why = least(self._speed_limit(self.space(now), speed),
                                 (math.sqrt(2*EASE_OUT*max(0., min(room, distance_m - travelled))), 'stand_off'),
                                 ((distance_m - travelled)/.6, 'distance'))
                wanted = max(.05, lin)
                lin = ease(now, wanted, speed_now)
                self._speeds.add(lin, why if lin >= .95 * wanted else 'easing_in')
                self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                self.wait(.15)
        finally:
            self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
            self.link.send({'cmd': 'stop'})
            if self.sounds:
                self.sounds.stop()
        return travelled, outcome

    def _locate(self, description, cancel, sought=None, settle=True):
        """One fresh sharp look plus one hosted locate; bearing is base-relative.

        With a remembered sighting, the locator is told which match it chose and where it
        should now be, and a match far from there is a different one (September 27: three
        looks for "door" chose three different doors and the base zig-zagged between them)."""
        if self._vision_calls >= self._vision_budget():
            raise ProviderFailure('PlanVisionBudgetReached')
        if settle:
            self._sleep(.4, cancel)   # Let the head camera settle after motion.
        image = self.look(max(self._view_since, self.clock() - .5))
        if image is None:
            return None, 'no_fresh_camera_frame'
        from .drive_space import HALF_FOV
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        expected = None
        if sought and sought is self._sought:
            expected = self._expected_sought()
        elif sought and sought.get('heading') is not None and heading is not None:
            expected = wrap(sought['heading'] - heading)
        self._vision_calls += 1
        if sought and sought.get('note'):
            hint = 'Earlier you matched it as: ' + sought['note'] + '.'
            if expected is not None and abs(expected - self._neck()) < HALF_FOV:
                hint += f' It should now be near x={.5 - (expected - self._neck())/(2*HALF_FOV):.2f}; if several match, report the one nearest there.'
            result = self.locator(description, image, cancel, hint=hint)
        else:
            result = self.locator(description, image, cancel)
        if result.get('found') and result.get('confidence') == 'low':
            self._weak = {'bearing': (.5 - float(result.get('x', .5))) * 2 * HALF_FOV + self._neck(),
                          'note': result.get('note')}
        if not result.get('found') or result.get('confidence') == 'low':
            return None, result.get('note') or 'not_found'
        bearing = (.5 - float(result['x'])) * 2 * HALF_FOV + self._neck()
        if expected is not None and abs(wrap(bearing - expected)) > (sought or {}).get('slack', 25.):
            return None, 'a_different_match_off_to_the_side'
        self._track()
        return {'bearing_deg': bearing, 'box_width': float(result['width']), 'note': result.get('note'),
                'heading': wrap(heading + bearing) if heading is not None else None,
                'from': tuple(self._at)}, None

    def _find_again(self, plan, cancel, shifted=0):
        """Look for the remembered thing: where it should be first, then to either side,
        with the head before the body. Returns (found, why not, degrees the base turned).

        October 3: after each leg he swung his whole body back to a predicted bearing,
        looked twice at that one spot, and gave up; the predictions were off and the turns
        overshot. The head covers LOOK_NECK_MAX either side at once, so the body turns only
        when the thing lies beyond that, and once more, directly, when it has been found."""
        description = self._sought['description']
        expected, turned = self._expected_sought(), 0.
        can_look = self.servos is not None and hasattr(self.servos, 'execute_pose')
        # Beyond what a turned head sees (its reach plus the 45 degree half view, less a margin), the body turns.
        if expected is not None and abs(expected) > (LOOK_NECK_MAX + 40. if can_look else 35.):
            # Never back up to make room here (September 27: a hallway corner beside the
            # arms turned every re-aim into a reverse, and he looped).
            turn = self._turn(max(-120., min(120., expected)), plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            expected = self._expected_sought()
        if not can_look:
            # No head to look around with: a second look from the same spot, as before.
            found, why = self._locate(description, cancel, self._sought)
            if found is None and self._vision_calls < self._vision_budget():
                found, why = self._locate(description, cancel, self._sought)
            return found, why, turned
        centre = max(-LOOK_NECK_MAX, min(LOOK_NECK_MAX, expected or 0.))
        first = -shifted or (-1 if centre > 0 else 1)   # After moving left, look right of the old heading first.
        angles = []
        for angle in (centre, centre + first*LOOK_NECK_MAX, centre - first*LOOK_NECK_MAX):
            angle = max(-LOOK_NECK_MAX, min(LOOK_NECK_MAX, angle))
            if all(abs(angle - seen) >= 15. for seen in angles):
                angles.append(angle)
        found, why, moved = None, 'not_found', False
        for index, angle in enumerate(angles):
            if self._vision_calls >= self._vision_budget():
                break
            if abs(angle - self._neck()) > 2.:
                moved = self._look(angle, cancel) or moved
            if index or shifted:
                self._sought['slack'] = 90.   # Anywhere in this view: the note says which one it is.
            found, why = self._locate(description, cancel, self._sought)
            if found is not None:
                break
        if moved:
            self._look(0., cancel)   # Level and centred again: bearings below are base-relative.
        if (found is None and not turned and expected is not None and abs(expected) > LOOK_NECK_MAX + 20.
                and self._vision_calls < self._vision_budget()):
            # October 23: the neck reaches about 25 degrees, not 40, so a thing predicted well to the side
            # can sit just past what the turned head sees. The body turns toward it for one more look.
            turn = self._turn(max(-120., min(120., expected)), plan, cancel, escape=False)
            turned += turn.get('turned_deg', 0.)
            self._sought['slack'] = 90.
            found, why = self._locate(description, cancel, self._sought)
        return found, why, turned

    def _expected_sought(self):
        """Base-relative bearing where the remembered thing should be now.

        Once two sightings from different places have fixed its position, the bearing is
        to that position from where he stands; until then it is the heading he last saw it
        at, which is right only while he has not moved far sideways."""
        if not self._sought:
            return None
        bearing, _ = self._bearing_to(self._sought.get('position'))
        if bearing is not None:
            return bearing
        telemetry, _ = self.link.snapshot()
        heading = self._heading(telemetry) if telemetry else None
        if self._sought.get('heading') is None or heading is None:
            return None
        return wrap(self._sought['heading'] - heading)

    def _remember_sought(self, description, found):
        """The thing's room heading survives this plan, so "keep going" finds the same one.

        Sightings from different places can also fix where it is, but only when at least
        three of them agree: October 3, a position taken from two sight lines a metre apart
        sent him turning the wrong way after every leg (657 degrees in one trip)."""
        previous = self._sought if self._sought and self._sought.get('description') == description else {}
        sightings = list(previous.get('sightings', ()))
        if found.get('heading') is not None and found.get('from') is not None:
            sightings = (sightings + [(*found['from'], found['heading'])])[-6:]
        self._sought = {'description': description, 'note': found.get('note'),
                        'heading': found.get('heading'), 'box_width': found['box_width'],
                        'first_width': previous.get('first_width') or found['box_width'],
                        'first_at': previous.get('first_at') or tuple(self._at),
                        'sightings': sightings, 'position': self._fixed_position(sightings)}

    @staticmethod
    def _fixed_position(sightings):
        """The point nearest all sight lines, when three or more lines from spread-out
        places pass within 8 degrees of it; otherwise None (a direction is all he has)."""
        if len(sightings) < 3:
            return None
        spread = max(abs(wrap(a[2] - b[2])) for a in sightings for b in sightings)
        if spread < 15.:
            return None
        # Least squares over the lines' normals: sum((p - p_i) . n_i)^2 is smallest at p.
        a11 = a12 = a22 = b1 = b2 = 0.
        for x, y, heading in sightings:
            nx, ny = -math.sin(math.radians(heading)), math.cos(math.radians(heading))
            a11 += nx*nx; a12 += nx*ny; a22 += ny*ny
            along = nx*x + ny*y
            b1 += nx*along; b2 += ny*along
        det = a11*a22 - a12*a12
        if abs(det) < 1e-6:
            return None
        point = ((b1*a22 - b2*a12)/det, (a11*b2 - a12*b1)/det)
        for x, y, heading in sightings:
            dx, dy = point[0] - x, point[1] - y
            if math.hypot(dx, dy) < .5 or abs(wrap(math.degrees(math.atan2(dy, dx)) - heading)) > 8.:
                return None
        return point

    def _seek(self, step, plan, cancel):
        """Look for a described thing the detector cannot name; sweep until found."""
        row = {'action': 'seek', 'description': step.description}
        if self.locator is None or self.look is None:
            return {**row, 'status': 'refused', 'reason': 'vision_seek_unavailable', 'turned_deg': 0.}
        sign = -1. if step.prefer == 'right' else 1.
        turned, looks, notes, retried = 0., 0, [], 0
        while looks < SEEK_LOOKS:
            self._weak = None
            found, why = self._locate(step.description, cancel)
            looks += 1
            if found is None and self._weak is not None and retried < WEAK_RETRY and self._vision_calls < self._vision_budget():
                # A doubtful sighting (often a blurred frame): turn toward it, let the head settle, look again.
                weak, retried = self._weak, retried + 1
                if step.prefer is None:
                    sign = 1. if weak['bearing'] > 0 else -1.   # And the search goes that way.
                if abs(weak['bearing']) > 8:
                    turn = self._turn(max(-90., min(90., weak['bearing'])), plan, cancel)
                    turned += turn.get('turned_deg', 0.)
                    if turn['status'] not in {'completed', 'refused'}:
                        return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': turned, 'looks': looks}
                self._sleep(HEAD_SETTLE_S, cancel)
                found, why = self._locate(step.description, cancel)
                if self.trace:
                    self.trace.emit('drive_weak_sighting', description=step.description, bearing_deg=round(weak['bearing']),
                                    found_again=found is not None)
            if found is not None:
                if abs(found['bearing_deg']) > 8:
                    turn = self._turn(max(-180., min(180., found['bearing_deg'])), plan, cancel)
                    turned += turn.get('turned_deg', 0.)
                    if turn['status'] != 'completed':
                        return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': turned, 'looks': looks}
                self._remember_sought(step.description, found)
                return {**row, 'status': 'completed', 'reason': 'found', 'note': found['note'],
                        'turned_deg': turned, 'looks': looks, 'vision_calls': self._vision_calls}
            notes.append(why)
            if looks < SEEK_LOOKS:
                turn = self._turn(sign * SEEK_SWEEP_DEG, plan, cancel)
                turned += turn.get('turned_deg', 0.)
                if turn['status'] != 'completed':
                    return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': turned, 'looks': looks}
        return {**row, 'status': 'not_found', 'reason': 'not_seen_in_any_look', 'turned_deg': turned,
                'looks': looks, 'notes': notes[-2:], 'vision_calls': self._vision_calls}

    def _ahead(self, space, telemetry):
        """(forward room that counts for a roll, whether it is in tight space).

        In tight space the roll steers round what limits only one side of the path (a door
        jamb, a hallway corner), as a crawl does, so the centre of the path counts, and what
        would hold a crawl leaves no room (owner, November 14: an approach down a hallway
        stopped for a floor look before every 1.2 m leg and never rolled on). Elsewhere the
        whole width of the path counts. A front block the firmware holds leaves no room
        whatever the host reads, so a roll is not restarted into it."""
        tight = space.get('path_center_m') is not None and self._tight(space, STRAIGHTEN_WITHIN_M)
        if telemetry.get('state') == 'blocked' and telemetry.get('blocked_dir') in ('front', 'both'):
            return 0., tight
        if tight:
            return (0. if self._crawl_hold(space, telemetry.get('tof_mm')) else space['path_center_m']), True
        return space.get('path_m'), False

    def _room(self, stand_off):
        space = self.space()
        front, _ = self._ahead(space, self.link.snapshot()[0] or {})
        return space, (front if front is not None else 0.) - stand_off

    @staticmethod
    def _open_side(space, stand_off, bearing):
        """+1 left / -1 right: a front band with a way around, preferring the target's side."""
        room = {1: space.get('front_left_m'), -1: space.get('front_right_m')}
        open_sides = [side for side, value in room.items() if value is not None and value - stand_off >= SIDE_OPEN_M]
        if len(open_sides) == 2:
            if abs(bearing) > 2:
                return 1 if bearing > 0 else -1
            return max(open_sides, key=lambda side: room[side])
        if open_sides:
            return open_sides[0]
        # Nothing open in front (a table across the whole view): the ring's side sensors may
        # still show floor to one side. October 3: 0.3 m ahead, two metres open on the right.
        beside = {1: space.get('left_m'), -1: space.get('right_m')}
        wide = [side for side, value in beside.items() if value is not None and value >= SIDE_WAY_M]
        return max(wide, key=lambda side: beside[side]) if wide else None

    @staticmethod
    def _hold_for(bearing, stand_off):
        """The stand-off kept from what is straight ahead: the sought thing's own while it is ahead
        (or its bearing unknown), only the obstacle margin while it is off to one side."""
        return stand_off if bearing is None or abs(bearing) <= SIDE_TARGET_DEG else min(stand_off, FRONT_MARGIN)

    def _cruise(self, plan, cancel, stand_off, budget_m):
        """Roll toward the sought thing while the next locate runs (owner, September 27: one
        continuous motion instead of drive-stop-look legs). Steering follows the remembered
        absolute heading, which each locate refreshes. Returns (travelled, outcome, found):
        found is the locate that was in flight when the wheels stopped, if it finished."""
        from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
        description = self._sought['description']
        self._ensure_parked(cancel)
        start = self._telemetry().get('odom') or {}
        speed = LIMITS[plan.origin]['speed_mps'] * self._pace
        pool = ThreadPoolExecutor(1, thread_name_prefix='r3x-cruise-locate')
        future, latest, misses, outcome, travelled = None, None, 0, None, 0.
        looked_at = float('-inf')
        recheck_until = None
        graces = [0]
        cruise_began = self._doorway(self.space())
        self._wheels_on('move', plan.origin)
        try:
            while outcome is None:
                self._check(cancel)
                now = self.clock()
                ok, why = self.availability(now)
                if not ok and why == 'drive_telemetry_stale' and self._telemetry_back(cancel, graces):
                    continue
                if not ok:
                    outcome = ('stopped', why)
                    break
                telemetry = self._telemetry()
                if future is None and self._vision_calls < self._vision_budget() and now - looked_at >= CRUISE_LOOK_S:
                    future, looked_at = pool.submit(self._locate, description, cancel, self._sought, False), now
                if future is not None and future.done():
                    found, why = future.result()
                    future = None
                    if found is None:
                        misses += 1
                        if misses >= 2:
                            outcome = ('lost', why)
                            break
                    else:
                        misses, latest = 0, found
                        self._remember_sought(description, found)
                        if found['box_width'] >= FILLS_VIEW and self._fills_count():
                            outcome = ('arrived', 'target_fills_the_view')
                            break
                odom = telemetry.get('odom') or {}
                travelled = math.hypot(float(odom.get('x', 0)) - float(start.get('x', 0)),
                                       float(odom.get('y', 0)) - float(start.get('y', 0)))
                if budget_m - travelled <= .05:
                    outcome = ('distance_limit', 'distance_limit_reached')
                    break
                space = self.space(now)
                self._room_door(space)
                inside = self._room_inside()
                if inside:
                    outcome = ('arrived', inside)
                    break
                if self._kept_out(space, cruise_began):
                    outcome = ('stopped', 'kept_out_of_doorway')   # Not there: kept out of the doorway.
                    break
                ahead, tight = self._ahead(space, telemetry)
                expected = self._expected_sought()
                room = (ahead if ahead is not None else 0.) - self._hold_for(expected, stand_off)
                speed_now = max(0., float(odom.get('lin', 0)))
                brake = speed_now*speed_now/(2*CONFIG['accel_lin']) + speed_now*.3
                firmware_blocked = telemetry.get('state') == 'blocked' and telemetry.get('blocked_dir') in ('front', 'both')
                if firmware_blocked or room <= max(.03, brake):
                    if recheck_until is None and self._doubtful(space, now):
                        # A return where open floor was just seen: stop rolling and read again
                        # before giving up the run (October 14 plan: no dead stop on one reading).
                        recheck_until = now + RECHECK_S
                        if self.trace:
                            self.trace.emit('drive_reading_rechecked', where='cruise', outcome='holding', seconds=RECHECK_S)
                    if recheck_until is not None and now < recheck_until:
                        self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
                        self.wait(.15)
                        continue
                    if recheck_until is not None and self.trace:
                        self.trace.emit('drive_reading_rechecked', where='cruise', outcome='persisted')
                    outcome = ('blocked', 'front_stand_off')
                    break
                if recheck_until is not None:
                    if self.trace:
                        self.trace.emit('drive_reading_rechecked', where='cruise', outcome='cleared')
                    recheck_until = None
                if expected is not None and abs(expected) > 40:
                    outcome = ('off_course', 'target_outside_steering_range')
                    break
                # Turning into an opening to one side, the firmware's centring pushes him off the near jamb,
                # which is the way he is going: as on the last stretch to a target, its steering is off and
                # only its push inside 13 cm stays (the hold below still stops him short of a scrape).
                aside = tight and expected is not None and abs(expected) > SIDE_TARGET_DEG
                self._assist(not (aside or self._near_target(space, expected)))
                aim = SIDE_AIM_ANG if aside else .2
                ang = 0. if expected is None else max(-aim, min(aim, math.radians(expected)*1.2))
                if tight:
                    # What the firmware cannot read, as in a crawl: a wall slanting in beside him
                    # and what the depth matrix sees across one side of the path.
                    ang = max(-STEER_MAX, min(STEER_MAX, ang + steer(space, telemetry.get('tof_mm'))))
                slowing = CRUISE_DECEL * (math.sqrt(.09 + 2*max(0., room)/CRUISE_DECEL) - .3)
                lin, why = least(self._crawl_speed(space, speed, ang) if tight else self._speed_limit(space, speed),
                                 (slowing, 'stand_off'), ((budget_m - travelled)/.6, 'distance'))
                lin = max(.05, lin)
                self._speeds.add(lin, why)
                self.link.send({'cmd': 'drive', 'lin': round(lin, 3), 'ang': round(ang, 3)})
                self.wait(.15)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)   # A stop never waits on a look in flight.
            raise
        finally:
            try:
                self.link.send({'cmd': 'drive', 'lin': 0., 'ang': 0.})
                self.link.send({'cmd': 'stop'})
                self._assist(True)
            finally:
                if self.sounds:
                    self.sounds.stop()
        carried = None
        if future is not None:
            try:
                found, _ = future.result(timeout=8.)   # Reuse the look already paid for.
                if found is not None:
                    self._remember_sought(description, found)
                    carried = found
            except (FutureTimeout, Cancelled, ProviderFailure):
                pass
        pool.shutdown(wait=False, cancel_futures=True)
        self._view_since = self.clock() + .3
        end = (self.link.snapshot()[0] or {}).get('odom') or {}
        travelled = math.hypot(float(end.get('x', 0)) - float(start.get('x', 0)),
                               float(end.get('y', 0)) - float(start.get('y', 0))) if end else travelled
        return travelled, outcome, carried or (latest if outcome[0] == 'arrived' else None)

    def _approach_sought(self, step, plan, cancel, budget_m):
        """Vision-guided legs toward what seek found: re-locate, re-aim, move, repeat.

        An obstacle is not arrival: while the thing still looks far away, a blocked leg first
        re-aims precisely (pointed slightly into a wall beside a door), then goes around on
        an open front band, and otherwise reports blocked."""
        row = {'action': 'approach', 'target': 'sought'}
        if step.description and (self._sought is None or self._sought['description'] != step.description):
            self._sought = {'description': step.description}
        if self._sought is None and self._last_sought():
            self._sought = {'description': self._reached[0]}   # "Keep going": on toward what he reached.
        if self._sought is None:
            return {**row, 'status': 'refused', 'reason': 'nothing_sought_yet', 'travelled_m': 0.}
        stand_off = max(FRONT_MARGIN, step.stop_at_m or DEFAULT_STOP_AT['object'])
        travelled, turned, misses, detours, swing_refusals, carried, cruises = 0., 0., 0, [], 0, None, 0
        # A roll that ended blocked without headway is not restarted until he has gone round
        # something: each restart costs a look, a firmware hold can clear once he stops, and a
        # re-aim of a few degrees from the same spot meets the same hold (a counter's corner).
        held_here = False
        tried = []   # What each leg around something did, in words, for the next look.
        shifted = 0  # +1 after a leg off to the left (the thing has slid right of its old heading), -1 after one to the right.
        def went(detour):
            nonlocal travelled, turned, shifted, held_here
            held_here = False
            shifted = 1 if detour['side'] == 'left' else -1
            turned += detour['turned_deg']
            travelled += detour['travelled_m']
            detours.append({k: detour[k] for k in ('side', 'travelled_m', 'status', 'by', 'note', 'escape') if k in detour})
            if detour.get('by') == 'camera' and self._sought.get('position') is None:
                # A sighting gives a direction, not a place: after moving aside the thing may
                # sit well off the old heading until enough sightings fix where it is.
                self._sought['slack'] = ROUTE_SLACK_DEG
        def result(status, reason, **extra):
            if status == 'completed':
                # Arrived: the search is over. October 3: a later, unrelated "come towards me"
                # was steered back to the microwave he had already reached. Only an approach to
                # "sought" itself goes on toward it, for a few minutes ("keep going").
                self._reached = (self._sought['description'], self.clock())
                self._sought = None
            out = {**row, 'status': status, 'reason': reason, 'travelled_m': round(travelled, 2),
                   'turned_deg': round(turned, 1), **extra}
            if swing_refusals:
                out['aim_turns_refused_for_arm_clearance'] = swing_refusals
            out['continuous_runs'] = cruises
            if detours:
                out['detours'] = detours
            return out
        while True:
            if self._vision_calls >= self._vision_budget():
                return result('look_limit', 'vision_look_limit_reached')
            if carried is not None:
                found, why, carried = carried, None, None
            else:
                found, why, swung = self._find_again(plan, cancel, shifted)
                turned += swung
            inside = self._room_inside()
            if inside:
                return result('completed', inside)
            if found is None and self._room_goal is not None and self._room_travel() >= ROOM_ENTER_M:
                # Gone a metre and more toward a room that is no longer "over there": it is all around him.
                return result('completed', 'room_all_around')
            if found is None:
                # Out of sight even after looking to both sides (a corner, a cabinet): one
                # more leg toward where it should be, then he looks for it again.
                onward = (self._route_leg(self._sought['description'], self._expected_sought(), plan, cancel,
                                          budget_m - travelled, tried, in_view=False)
                          if any(d.get('by') == 'camera' for d in detours)
                          and sum(d.get('by') == 'camera' for d in detours) < ROUTE_LEGS else None)
                if onward is None or not onward['travelled_m']:
                    return result('lost', why)
                went(onward)
                continue
            shifted = 0
            self._remember_sought(self._sought['description'], found)
            if found['box_width'] >= FILLS_VIEW and self._fills_count():
                return result('completed', 'target_fills_the_view')
            if budget_m - travelled < .1:
                return result('distance_limit', 'distance_limit_reached')
            bearing = found['bearing_deg']
            space, room = self._room(self._hold_for(bearing, stand_off))
            if abs(bearing) > AIM_DEG or (room < .1 and abs(bearing) > 3):
                turn = self._turn(max(-90., min(90., bearing)), plan, cancel, escape=False)
                turned += turn.get('turned_deg', 0.)
                if turn['status'] == 'completed':
                    bearing = 0.
                elif turn['status'] != 'refused':
                    return result(turn['status'], turn['reason'])
                else:
                    swing_refusals += 1   # Arms near something: keep going forward and aim once past it.
                space, room = self._room(self._hold_for(bearing, stand_off))
            if not held_here and min(budget_m - travelled, room) >= .2:   # Braking stops ~0.1 m early; no creeping restarts.
                moved, outcome, carried = self._cruise(plan, cancel, stand_off, budget_m - travelled)
                travelled += moved
                cruises += 1
                # Also a roll that could not steer that far round: the same look from the same spot would
                # start it again and again.
                held_here = outcome[0] in {'blocked', 'off_course'} and moved < .05
                if outcome[0] == 'arrived':
                    return result('completed', outcome[1])
                if outcome[0] == 'stopped':
                    return result('stopped', outcome[1])
                continue   # Blocked, off course, lost or budget: re-evaluate from the latest look.
            clearance = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in space.items()}
            ahead = space.get('front_center_m')
            if ahead is not None and self._tight(space, STRAIGHTEN_WITHIN_M):
                # A doorway or hall: a jamb or a wall at an angle in one band is not the thing.
                bands = [space.get(k) for k in ('path_left_m', 'path_center_m', 'path_right_m')]
                ahead = max([ahead, *(b for b in bands if b is not None)])
            wide = found['box_width'] >= NEAR_WIDTH and abs(bearing) <= 8
            grown = (found['box_width'] >= GROWN_NEAR * (self._sought.get('first_width') or found['box_width'])
                     and math.dist(self._sought.get('first_at') or self._at, self._at) >= GROWN_TRAVEL_M)
            if wide and (grown or (ahead is not None and ahead - stand_off < NEAR_AHEAD_M)) and (
                    self._room_goal is None or self._room_travel() >= ROOM_ENTER_M):
                # Blocked with the thing ahead and near: arrived. Near means either something
                # straight ahead at the stand-off, or the thing itself has at least doubled in
                # the picture since first seen (it sits above or behind what stops him). A
                # side return beside a thing that only looks big from afar is not arrival.
                return result('completed', 'reached_stand_off_at_target', clearance_at_stop_m=clearance,
                              grew_in_view=round(found['box_width'] / (self._sought.get('first_width') or found['box_width']), 1))
            detour = self._go_around(self._sought['description'], bearing, space, stand_off, plan, cancel,
                                     budget_m - travelled, detours, tried)
            if detour is None:
                return result('blocked', 'still_blocked_after_detours' if detours else 'obstacle_before_target',
                              clearance_at_stop_m=clearance, target_still_far=True)
            went(detour)
            if (detour['by'] == 'sensors' and detour['status'] == 'blocked' and not detour['travelled_m']
                    and detour['turned_deg'] == 0.):
                return result('blocked', 'detour_turn_refused', clearance_at_stop_m=clearance, target_still_far=True)
            if detour['status'] not in {'completed', 'blocked', 'refused'}:
                return result(detour['status'], 'detour_' + detour['status'])

    # Rooms (owner, November 22; docs/ROOM_NAVIGATION_PLAN.md) ---------------------------------------------
    @staticmethod
    def _doorway(space):
        """Both sides within a doorway's width of him (a doorway, or a hall)."""
        left, right = space.get('left_m'), space.get('right_m')
        return left is not None and right is not None and left <= DOOR_SIDE_M and right <= DOOR_SIDE_M

    def _kept_out(self, space, began_in_doorway):
        """Owner, November 22: on his own he does not go into other rooms yet, and must not end up blocking a
        hall or a door nobody expects him in. A move of his own ends where a doorway or hall begins."""
        return self._origin == 'self_initiated' and not began_in_doorway and self._doorway(space)

    def _room_travel(self):
        goal = self._room_goal
        return math.dist(self._at, goal['start']) if goal else 0.

    def _room_door(self, space):
        """Going into a room: squeezed between both sides, then open again, is through its opening."""
        goal = self._room_goal
        if goal is None or goal['entered_at'] is not None:
            return
        left, right = space.get('left_m'), space.get('right_m')
        if self._doorway(space):
            goal['squeezed'] = True
        elif goal['squeezed'] and ((left or 0.) >= DOOR_OPEN_M or (right or 0.) >= DOOR_OPEN_M):
            goal['entered_at'] = tuple(self._at)
            if self.trace:
                self.trace.emit('drive_room_entered', room=goal.get('name'), travelled_m=round(self._room_travel(), 2))

    def _room_inside(self):
        """Why he is now in the room he is going into, or None: through its opening and on a little, or the
        room model sure of it after he has moved."""
        goal = self._room_goal
        if goal is None:
            return None
        if goal['entered_at'] is not None and math.dist(self._at, goal['entered_at']) >= ENTER_ON_M:
            return 'entered_room'
        if (goal.get('room_id') and self.navigator is not None and self._room_travel() >= ROOM_ENTER_M
                and self.navigator.in_room(goal['room_id'])):
            return 'room_recognized'
        return None

    def _fills_count(self):
        """A thing filling the view is reached; a room fills it from its doorway, so only once he is in."""
        return self._room_goal is None or self._room_travel() >= ROOM_FILLS_AFTER_M

    def _look_around(self, plan, cancel, until_placed=False):
        """A full turn in eight looks, a photo at each (requested, or to find himself on a trip someone asked
        for). until_placed: stop once the room map knows the spot and two looks are in."""
        row = {'action': 'look_around'}
        nav = self.navigator
        if nav is None:
            return {**row, 'status': 'refused', 'reason': 'room_map_unavailable', 'turned_deg': 0.}
        turned, looks, refused, where = 0., 0, 0, nav.where()
        owed = 0.
        for index in range(int(round(360. / LOOK_AROUND_DEG))):
            if index:
                owed += LOOK_AROUND_DEG
                turn = self._turn(owed, plan, cancel, escape=False)
                if turn['status'] == 'refused':
                    refused += 1
                    continue   # Too close to something to swing that far: try the next step's angle.
                if turn['status'] != 'completed':
                    return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': round(turned, 1),
                            'looks': looks}
                moved = turn.get('measured_deg') if turn.get('measured_deg') is not None else turn.get('commanded_deg', owed)
                turned += abs(moved)
                owed = max(0., owed - abs(moved))
            self._sleep(LOOK_AROUND_SETTLE_S, cancel)
            where = nav.capture(cancel)
            looks += 1
            if until_placed and where.get('spot') and looks >= 2:
                break
        out = {**row, 'status': 'completed', 'reason': 'placed' if where.get('spot') else 'spot_not_known',
               'looks': looks, 'turned_deg': round(turned, 1)}
        if refused:
            out['turns_refused'] = refused
        self._step_trace(out)
        return out

    def _go_to(self, step, plan, cancel, budget_m):
        """Go to a room: by the map when it knows the way, else by sight (find it and go in through its
        opening). After turns or moves a person dictated, their directions decide: by sight, toward the side
        they named, never the map's way. With neither a way nor a sighting he says so, so he can ask."""
        nav = self.navigator
        row = {'action': 'go_to', 'room': step.description}
        room_id = step.target
        travelled, turned, ok = 0., 0., False
        try:
            if nav is not None and room_id and not step.directed:
                where = nav.where()
                if where.get('room_id') == room_id and where.get('room_sure'):
                    ok = True
                    return {**row, 'status': 'completed', 'reason': 'already_here', 'how': 'map', 'travelled_m': 0.}
                if where.get('spot') is None and nav.mapped(room_id):
                    # A look from where he stands first; a spin only when that does not place him. A room the
                    # map has never been to or seen the way to goes straight to looking for it (no spin).
                    where = nav.capture(cancel)
                    if where.get('spot') is None:
                        look = self._look_around(plan, cancel, until_placed=True)
                        turned += look.get('turned_deg', 0.)
                        if look['status'] not in {'completed', 'refused'}:
                            return {**row, 'status': look['status'], 'reason': look.get('reason'), 'turned_deg': turned}
                        row['looked_around'] = look.get('looks')
                route = nav.route_to(room_id)
                row['route'] = route['kind']
                if route['kind'] == 'here':
                    ok = True
                    return {**row, 'status': 'completed', 'reason': 'already_here', 'how': 'map', 'turned_deg': turned,
                            'travelled_m': 0.}
                if route['kind'] in {'passages', 'bearing'}:
                    nav.trip_started(room_id, 'map')
                    out = (self._follow(route, plan, cancel, budget_m) if route['kind'] == 'passages'
                           else self._toward_bearing(route, plan, cancel, budget_m))
                    turned += out.get('turned_deg', 0.)
                    travelled += out.get('travelled_m', 0.)
                    ok = out['status'] == 'completed'
                    return {**row, **out, 'how': 'map', 'turned_deg': round(turned, 1), 'travelled_m': round(travelled, 2)}
            how = 'directions' if step.directed else 'sight'
            if nav is not None:
                nav.trip_started(room_id, how)
            out = self._enter_by_sight(step, plan, cancel, budget_m)
            ok = out['status'] == 'completed'
            out = {**row, **out, 'how': how, 'turned_deg': round(turned + out.get('turned_deg', 0.), 1)}
            if ok:
                out['ask_if_this_is'] = step.description   # Found by sight or directions: he checks once.
            return out
        finally:
            if nav is not None:
                nav.trip_ended(ok)

    def _enter_by_sight(self, step, plan, cancel, budget_m):
        seek = self._seek(DriveStep('seek', description=step.description, prefer=step.prefer, pace=step.pace), plan, cancel)
        if seek['status'] != 'completed':
            status = 'route_unknown' if seek['status'] == 'not_found' else seek['status']
            return {'status': status, 'reason': 'not_in_view_and_no_way_known' if status == 'route_unknown' else seek.get('reason'),
                    'turned_deg': seek.get('turned_deg', 0.), 'looks': seek.get('looks'), 'notes': seek.get('notes'),
                    'travelled_m': 0.}
        self._room_goal = {'room_id': step.target, 'name': step.description, 'start': tuple(self._at),
                           'squeezed': self._doorway(self.space()), 'entered_at': None}
        try:
            approach = self._approach_sought(DriveStep('approach', target='sought', description=step.description,
                                                       pace=step.pace), plan, cancel, budget_m)
        finally:
            self._room_goal = None
        out = {**approach, 'turned_deg': round(seek.get('turned_deg', 0.) + approach.get('turned_deg', 0.), 1),
               'found_by': seek.get('note')}
        out.pop('action', None); out.pop('target', None)
        return out

    def _follow(self, route, plan, cancel, budget_m):
        """Drive the passages of a route: each segment's heading in its spot's own frame, its length with the
        usual clearance handling, a small correction from the tiny copies taken when it was first driven, and
        a look at the end of each passage to check he is where it leads."""
        nav = self.navigator
        travelled, turned, replanned = 0., 0., False
        legs, goal = list(route['legs']), route['goal']
        while legs:
            leg = legs.pop(0)
            along = 0.
            for segment in leg['segments']:
                relative = nav.relative(leg['from'], segment['heading'])
                if relative is None:
                    return {'status': 'lost', 'reason': 'direction_unknown_on_route', 'travelled_m': round(travelled, 2),
                            'turned_deg': round(turned, 1)}
                if abs(relative) >= 3.:
                    turn = self._turn(relative, plan, cancel)
                    turned += turn.get('turned_deg', 0.)
                    if turn['status'] != 'completed':
                        nav.passage_result(leg['passage_id'], False)
                        return {'status': 'blocked' if turn['status'] == 'refused' else turn['status'],
                                'reason': turn.get('reason'), 'travelled_m': round(travelled, 2), 'turned_deg': round(turned, 1)}
                fix = self._segment_fix(leg, segment, along)
                if fix:
                    turn = self._turn(fix, plan, cancel, escape=False)
                    turned += turn.get('turned_deg', 0.)
                if budget_m - travelled < .1:
                    return {'status': 'distance_limit', 'reason': 'distance_limit_reached', 'travelled_m': round(travelled, 2),
                            'turned_deg': round(turned, 1)}
                want = min(segment['length_m'], budget_m - travelled)
                move = self._legs(want, plan, cancel)
                travelled += move.get('travelled_m', 0.)
                along += move.get('travelled_m', 0.)
                turned += move.get('turned_deg', 0.)
                short = want - move.get('travelled_m', 0.)
                if move['status'] != 'completed' or (move.get('reason') == 'shortened_by_clearance' and short > .4):
                    nav.passage_result(leg['passage_id'], False)
                    return {'status': 'blocked' if move['status'] in {'completed', 'refused', 'blocked'} else move['status'],
                            'reason': 'blocked_on_the_way: ' + str(move.get('reason')), 'travelled_m': round(travelled, 2),
                            'turned_deg': round(turned, 1), 'clearance_m': move.get('clearance_m')}
            start_offset = nav.offset(leg['from'])
            if start_offset is not None:
                nav.expect(leg['to'], start_offset + leg['arrive'])
            self._sleep(BASE_VIEW_SETTLE_S, cancel)
            where = nav.capture(cancel)
            nav.passage_result(leg['passage_id'], where.get('spot') in (None, leg['to']))
            if where.get('spot') not in (None, leg['to']) and not replanned:
                # Somewhere else he knows: once, a new route from there.
                replanned = True
                again = nav.route_to(route.get('room_id')) if route.get('room_id') else None
                if again and again['kind'] == 'passages':
                    legs, goal = list(again['legs']), again['goal']
                elif again and again['kind'] == 'here':
                    legs, goal = [], where.get('spot')
        where = nav.where()
        arrived = where.get('spot') == goal
        out = {'status': 'completed', 'reason': 'arrived_by_map' if arrived else 'arrived_not_confirmed',
               'travelled_m': round(travelled, 2), 'turned_deg': round(turned, 1)}
        if not arrived:
            out['ask_if_this_is'] = True
        return out

    def _segment_fix(self, leg, segment, along):
        """Degrees to turn so the view matches the tiny copy taken here when the passage was driven, or None."""
        marks = [m for m in leg.get('marks') or () if abs(m['along_m'] - along) <= .5]
        if not marks:
            return None
        from .room_map import thumb_shift
        mark = min(marks, key=lambda m: abs(m['along_m'] - along))
        thumb, neck = self.navigator.thumb_now()
        shift = thumb_shift(thumb, mark['thumb'])
        if shift is None or shift[1] < SEGMENT_FIX_SCORE:
            return None
        facing = wrap(mark['bearing'] + shift[0] - (neck or 0.))
        fix = wrap(segment['heading'] - facing)
        if self.trace:
            self.trace.emit('drive_route_fix', degrees=round(fix, 1), score=round(shift[1], 2), along_m=round(along, 2))
        return fix if SEGMENT_FIX_MIN_DEG <= abs(fix) <= SEGMENT_FIX_MAX_DEG else None

    def _toward_bearing(self, route, plan, cancel, budget_m):
        """A room known only as a direction from this spot (the open living, dining and kitchen space): face
        it and roll that way as far as the floor goes."""
        nav = self.navigator
        relative = nav.relative(route['spot'], route['bearing'])
        turned = 0.
        if relative is None:
            return {'status': 'lost', 'reason': 'direction_unknown', 'travelled_m': 0., 'turned_deg': 0.}
        if abs(relative) >= 3.:
            turn = self._turn(relative, plan, cancel)
            turned += turn.get('turned_deg', 0.)
            if turn['status'] != 'completed':
                return {'status': 'blocked' if turn['status'] == 'refused' else turn['status'], 'reason': turn.get('reason'),
                        'travelled_m': 0., 'turned_deg': round(turned, 1)}
        if budget_m < .1:
            return {'status': 'distance_limit', 'reason': 'distance_limit_reached', 'travelled_m': 0.,
                    'turned_deg': round(turned, 1)}
        move = self._legs(min(BEARING_ROLL_M, budget_m), plan, cancel)
        moved = move.get('travelled_m', 0.)
        if moved < .3 and move['status'] != 'completed':
            return {'status': 'blocked', 'reason': move.get('reason'), 'travelled_m': moved, 'turned_deg': round(turned, 1),
                    'clearance_m': move.get('clearance_m')}
        far = move['status'] != 'completed' or move.get('reason') == 'shortened_by_clearance'
        return {'status': 'completed', 'reason': 'as_far_as_fits' if far else 'rolled_toward_room',
                'travelled_m': moved, 'turned_deg': round(turned + move.get('turned_deg', 0.), 1)}

    def _find_person(self, step, plan, cancel):
        """Turn until a face is in view: first where the model said, the last voice and radar bodies, then,
        when someone asked, a look to each side and behind."""
        row = {'action': 'find_person'}
        if self._fresh_faces(self.clock()):
            return {**row, 'status': 'completed', 'reason': 'person_already_visible', 'turned_deg': 0.}
        candidates = [b['bearing_deg'] for b in ((self.radar.bodies() if self.radar else None) or [])]
        # Whoever just spoke is the likeliest person to find: look there before radar bodies.
        voice = self._voice_bearing()
        if voice is None and plan.targets.get('speaker'):
            voice = self._predicted(plan.targets['speaker'])   # Offered at plan time; he may have turned since.
        if voice is not None:
            candidates.insert(0, voice)
        prior = self._resolve(step.target, plan, self.clock()) if step.target else None
        if prior is not None:
            candidates.insert(0, prior['bearing_deg'])  # The model's chosen first place to look.
        if plan.origin != 'self_initiated':
            candidates += [90., -90., 180.]
        if not candidates:
            return {**row, 'status': 'not_found', 'reason': 'nowhere_to_look', 'turned_deg': 0.}
        heading, turned, looked = 0., 0., []
        for bearing in candidates:
            if len(looked) >= 4:
                break
            if any(abs(wrap(bearing - seen)) <= 30 for seen in looked + [0.]):
                continue
            delta = wrap(bearing - heading)
            turn = self._turn(max(-180., min(180., delta)), plan, cancel)
            if turn['status'] != 'completed':
                if turn['status'] == 'refused':
                    looked.append(bearing)
                    continue
                return {**row, 'status': turn['status'], 'reason': turn['reason'], 'turned_deg': turned}
            moved = turn['measured_deg'] if turn.get('measured_deg') is not None else turn['commanded_deg']
            heading, turned = wrap(heading + moved), turned + abs(moved)
            looked.append(bearing)
            self._sleep(1.2, cancel)
            faces = self._fresh_faces(self.clock())
            if faces:
                return {**row, 'status': 'completed', 'reason': 'person_in_view', 'turned_deg': turned,
                        'faces_visible_after': len(faces)}
        return {**row, 'status': 'not_found', 'reason': 'no_face_after_search', 'turned_deg': turned}

    def execute(self, plan: DrivePlan, cancel: Cancellation) -> DriveReceipt:
        receipt = DriveReceipt(plan.request_id, started_at=self.clock())
        if not self._lock.acquire(blocking=False):
            receipt.status, receipt.reason = 'refused', 'DriveBusy'
            return receipt
        self._parked = False
        current = step_odom = step_heading = None
        step_started = self.clock()
        try:
            self._halt.clear()
            ok, reason = self.availability()
            if plan.origin=='self_initiated' and self._pose_held():
                ok, reason = False, 'human_pose_held'
            if not ok and self.recovering(reason):
                ok, reason = self._await_sensors(reason, cancel, plan.expires_at)
            if not ok or self.clock() >= plan.expires_at:
                receipt.status, receipt.reason = 'refused', reason or 'StaleDrivePlan'
                return receipt
            self._plan_id = plan.request_id
            self.moving.set()
            self._notify(plan)
            self._vision_calls = 0   # The last sought thing persists across plans ("keep going").
            self._dead_ends = []
            self._directed = False
            start = self.link.snapshot()[0]
            self._start_heading = self._heading(start) if start else None
            self._plan_parks = plan.park_arm   # The arm parks right before the first wheel command.
            self._throttle_out = False
            self._origin = plan.origin
            self._view_since = self.clock()
            budget = LIMITS[plan.origin]['total_m']
            for step in plan.steps:
                self._check(cancel)
                current, step_odom = step, (self.link.snapshot()[0] or {}).get('odom') or {}
                step_heading, step_started = self._heading(self.link.snapshot()[0] or {}), self.clock()
                self._pace = PACE.get(step.pace, 1.)
                self._turn_moved = 0.
                self._speeds = RollSpeeds()
                ok, reason = self.availability()
                if not ok and self.recovering(reason):
                    # Between steps the plan's age no longer matters: he is already under way.
                    ok, reason = self._await_sensors(reason, cancel, self.clock() + SENSOR_WAIT_S)
                if not ok:
                    receipt.steps.append({'action': step.action, 'status': 'refused', 'reason': reason})
                    break
                if step.action == 'turn':
                    row = self._turn(step.degrees, plan, cancel)
                elif step.action == 'move':
                    row = self._legs(step.meters, plan, cancel)
                elif step.action == 'face':
                    row = self._face_target(step, plan, cancel)
                elif step.action == 'approach' and step.target == 'sought':
                    row = self._approach_sought(step, plan, cancel, max(0., budget - receipt.travelled_m))
                elif step.action == 'approach':
                    # Closed-loop approach is bounded by the plan total, not the straight-move step cap.
                    row = self._approach(step, plan, cancel, max(0., budget - receipt.travelled_m))
                elif step.action == 'seek':
                    row = self._seek(step, plan, cancel)
                elif step.action == 'go_to':
                    row = self._go_to(step, plan, cancel, max(0., budget - receipt.travelled_m))
                elif step.action == 'look_around':
                    row = self._look_around(plan, cancel)
                else:
                    row = self._find_person(step, plan, cancel)
                if step.action != 'turn' and self._turn_moved:
                    # Banks and back-ups inside this step's turns moved him too.
                    row = {**row, 'travelled_m': round(row.get('travelled_m', 0.) + self._turn_moved, 2)}
                if self._speeds.summary():
                    row = {**row, 'roll_speed': self._speeds.summary()}   # Every roll in the step, legs and detours too.
                receipt.steps.append(row)
                # A turn a person asked for, then "come to me" / "face me": they are where it pointed.
                self._directed = plan.origin == 'requested' and step.action == 'turn' and row['status'] == 'completed'
                if self.sounds and step.action in {'approach', 'go_to'} and row['status'] == 'completed' and row.get('travelled_m'):
                    self.sounds.accent('arrived', plan.origin)
                elif self.sounds and row['status'] == 'blocked':
                    self.sounds.accent('blocked', plan.origin)
                receipt.travelled_m = round(receipt.travelled_m + row.get('travelled_m', 0.), 2)
                receipt.turned_deg = round(receipt.turned_deg + row.get('turned_deg', 0.), 1)
                self._check(cancel)  # A stop's 'superseded' done is a cancellation, not a partial plan.
                if row['status'] != 'completed':
                    break
            done = [r['status'] == 'completed' for r in receipt.steps]
            if len(done) == len(plan.steps) and all(done):
                receipt.status = 'completed'
            elif any(done) or receipt.travelled_m or receipt.turned_deg:
                receipt.status, receipt.reason = 'partial', receipt.steps[-1].get('reason')
            else:
                last = receipt.steps[-1] if receipt.steps else {}
                receipt.status = 'refused' if last.get('status') == 'refused' else last.get('status', 'failed')
                receipt.reason = last.get('reason')
        except Cancelled:
            receipt.status, receipt.reason = 'cancelled', 'stop_requested'
            if current is not None:
                # The interrupted step still moved: report its odometry, not zero.
                end = (self.link.snapshot()[0] or {}).get('odom') or {}
                moved = math.hypot(float(end.get('x', 0)) - float(step_odom.get('x', 0)),
                                   float(end.get('y', 0)) - float(step_odom.get('y', 0))) if end and step_odom else 0.
                receipt.steps.append({'action': current.action, 'status': 'cancelled', 'reason': 'stop_requested',
                                      'travelled_m': round(moved, 2)})
                receipt.travelled_m = round(receipt.travelled_m + moved, 2)
                # October 16 (03bc4e1f): a turn stopped after 9 s at 0 degrees left no trace of what
                # the sensors or the heading did. Keep the stopped step like a finished one.
                heading = self._heading(self.link.snapshot()[0] or {})
                self._step_trace({'action': current.action, 'status': 'cancelled', 'reason': 'stop_requested',
                                  'travelled_m': round(moved, 2), 'seconds': round(self.clock() - step_started, 1),
                                  'measured_deg': round(wrap(heading - step_heading), 1)
                                  if heading is not None and step_heading is not None else None,
                                  'odometry': {k: end.get(k) for k in ('x', 'y', 'theta')} if end else None},
                                 self.space())
        except ArmParkFailed as exc:
            receipt.status, receipt.reason = 'refused', str(exc)   # Nothing moved: the park precedes wheels.
        except ProviderFailure as exc:
            receipt.status, receipt.reason = 'failed', str(exc)
        finally:
            try:
                if not self.link.failure and self.link.port is not None:
                    self.link.send({'cmd': 'stop'})
            except ProviderFailure:
                pass
            if self.sounds:
                self.sounds.stop(now=True)
            self._assist(True)   # Never left off after a plan, however it ended.
            # He moved, so what the front sensors saw before is no baseline: October 3, ending
            # 11 cm from a kitchen island looked like "someone came very close" and he backed
            # away on his own, through his spoken report, which then failed to finish.
            self.flinch.baseline.clear()
            self.flinch.clear.clear()
            self.flinch.ticks = 0
            if self._parked:
                self._parked = False
                # Owner, October 11: an arm with something within its reach stays tucked.
                try:
                    keep = arms_blocked(self.space())
                except Exception:
                    keep = frozenset({'poker', 'throttle'})
                if keep and self.trace:
                    self.trace.emit('arm_clearance', status='kept_parked', arms=sorted(keep))
                # A picture is wanted after this drive: the head stays still for it first.
                self.servos.finish_base_motion(look_pending=bool(plan.look_after), keep_parked=keep)
            self.motion_until = self.clock() + 1.
            self.moving.clear()
            self._notify(plan)
            receipt.finished_at = self.clock()
            self.history.append({'request_id': plan.request_id, 'origin': plan.origin, 'purpose': plan.purpose,
                                 'steps_run': [f"{r['action']}:{r['status']}" for r in receipt.steps],
                                 'status': receipt.status, 'reason': receipt.reason,
                                 'travelled_m': receipt.travelled_m, 'turned_deg': receipt.turned_deg,
                                 'finished_at': receipt.finished_at})
            if self.trace:
                self.trace.emit('drive_receipt', **asdict(receipt), origin=plan.origin, purpose=plan.purpose)
            self._lock.release()
        return receipt

    def charge_reading(self, now=None):
        """(percent, charging) for the chest's charge meter, from the latest telemetry, or None.

        Owner, November 15: Rex's shutdown fade ends in the meter. Both values are decided as the
        battery menu bar app decides them for the same meter: a whole percent, and charging when
        the controller's charger flag is set or the pack is at full-charge voltage. A level not
        yet known, or a reading older than CHARGE_READING_MAX_AGE, gives None."""
        now = self.clock() if now is None else now
        telemetry, _ = self.link.snapshot()
        if not telemetry or now - telemetry['received_at'] > CHARGE_READING_MAX_AGE:
            return None
        soc, mv = telemetry.get('batt_soc'), telemetry.get('batt_mv')
        if not isinstance(soc, (int, float)) or soc < 0:
            return None
        full = isinstance(mv, (int, float)) and mv >= CHARGER_MV
        return min(100, int(soc)), bool(telemetry.get('charging')) or full

    def base_status(self, now=None):
        """Live wheel-controller readings for honest answers (battery, charging, climate).

        Values the firmware marks unknown (-1, ok false) stay None; nothing is estimated."""
        now = self.clock() if now is None else now
        telemetry, _ = self.link.snapshot()
        if not telemetry:
            return {'connected': self.state == 'ready', 'readings': None}
        known = lambda v, low=0: v if isinstance(v, (int, float)) and v >= low else None
        mv, ma, soc = known(telemetry.get('batt_mv')), telemetry.get('batt_ma'), known(telemetry.get('batt_soc'))
        imu, env = telemetry.get('imu') or {}, telemetry.get('env') or {}
        celsius = env.get('t') if env.get('ok') else None
        return {
            'connected': self.state == 'ready', 'reading_age_s': round(max(0., now - telemetry['received_at']), 1),
            'battery': {'percent': soc, 'volts': round(mv/1000, 2) if mv else None,
                        # Positive draws from the pack; negative flows into it (V2 menu-bar convention).
                        'amps_drawn': round(ma/1000, 2) if isinstance(ma, (int, float)) and ma else None,
                        'on_charger': bool(telemetry.get('charging')),
                        'percent_meaning': 'coulomb-counted estimate from the controller; null means not yet known'},
            'controller': {'state': telemetry.get('state'), 'owner': telemetry.get('owner'), 'fault': telemetry.get('fault'),
                           'error_count': telemetry.get('errs'), 'gamepad_connected': (telemetry.get('gp') or {}).get('connected')},
            'tilt_deg': {'pitch': imu.get('pitch'), 'roll': imu.get('roll')} if imu.get('ok') else None,
            'room': {'temp_c': celsius, 'temp_f': round(celsius*9/5 + 32, 1) if celsius is not None else None,
                     'pressure_hpa': env.get('hpa'), 'humidity_pct': env.get('rh')} if env.get('ok') else None,
        }

    def _notify(self, plan):
        self._note_base_view()
        for hook in self.on_motion:
            try:
                hook(plan)
            except Exception as exc:
                if self.trace:
                    self.trace.emit('drive_hook_failed', error_type=type(exc).__name__)

    # Reflex --------------------------------------------------------------
    def tick_reflex(self, now=None):
        """One back-off evaluation. Idle only; plans and the gamepad take priority."""
        now = self.clock() if now is None else now
        telemetry, _ = self.link.snapshot()
        if (not telemetry or not self.reflex_allowed() or self.moving.is_set() or self._manual is not None
                or not self.availability(now)[0]):
            self.flinch.ticks = 0
            return None
        tof = telemetry.get('tof_mm') or {}
        readings = {k: (tof[k]/1000. if isinstance(tof.get(k), (int, float)) and tof[k] >= 0 else None)
                    for k in ('fl', 'fr', 'fl_radial', 'fr_radial') if k in tof}
        if not self.flinch.observe(readings, now):
            return None
        rear = self.space(now)['rear_m']
        distance = min(.30, (rear or 0.) - .30)
        if rear is None or distance < .10:
            if self.trace:
                self.trace.emit('drive_reflex', status='held', reason='rear_blind_or_cornered')
            return None
        # Straight reverse needs no throttle park (V2 interlock rule).
        plan = DrivePlan('reflex:' + uuid4().hex, (DriveStep('move', meters=-distance),), 'self_initiated',
                         'back off: someone came very close in front', {}, now + 5., park_arm=False)
        return self.execute(plan, Cancellation())

    def tick_arm_clearance(self, now=None):
        """Bring out an arm kept tucked after a drive, or left in at the wake (owner, November 17),
        once its space has stayed clear for ARM_CLEAR_S. Nothing while the base moves; the next
        drive decides afresh."""
        kept = getattr(self.servos, 'kept_arms', None) or frozenset()
        if not kept or self.moving.is_set():
            self._arm_clear_since.clear()
            return None
        now = self.clock() if now is None else now
        space = self.space(now)
        blocked = arms_blocked(space)
        if self.trace and kept & blocked != self._arm_blocked_shown and kept & blocked:
            # Run 6549d643 (November 21): the poker arm stayed in all run and nothing said what it saw.
            self.trace.emit('arm_clearance', status='kept_blocked', arms=sorted(kept & blocked),
                            left_m=space.get('left_m'), front_right_m=throttle_front_m(space))
        self._arm_blocked_shown = kept & blocked
        for arm in set(self._arm_clear_since) - (kept - blocked):
            del self._arm_clear_since[arm]
        for arm in kept - blocked:
            self._arm_clear_since.setdefault(arm, now)
        ready = frozenset(arm for arm, since in self._arm_clear_since.items() if now - since >= ARM_CLEAR_S)
        if not ready or not self.servos.extend_cleared_arms(kept - ready):
            return None
        for arm in ready:
            del self._arm_clear_since[arm]
        if self.trace:
            self.trace.emit('arm_clearance', status='extended', arms=sorted(ready))
        return ready

    def _arm_clearance_loop(self):
        while not self._closing.wait(1.):
            try:
                self.tick_arm_clearance()
            except Exception as exc:
                if self.trace:
                    self.trace.emit('arm_clearance', status='failed', error_type=type(exc).__name__)

    def _reflex_loop(self):
        while not self._closing.wait(1.):
            try:
                self.tick_reflex()
            except Exception as exc:
                if self.trace:
                    self.trace.emit('drive_reflex', status='failed', error_type=type(exc).__name__)
