"""November 17 (run e7d8c980): a door standing open at an angle, with something beside his left.

The front depth columns read the door as an even slope (0.34 m on the right to 0.76 m, then
nothing on the left), and the navigation kept three "nearest thing" numbers of it. These checks
use two frames the robot's own sensors sent from that spot, and simulated rooms built from them.
"""
import math
import unittest

from r3x.contracts import Cancellation
from r3x.drive import DrivePlan, DriveStep
from r3x.drive_space import (allowed_turn, beside_gap, body_room, clearance, front_surface, out_of_view, steer,
                             ARMS_TUCKED)
from tests.test_drive import settle
from tests.test_floor_feedback import Router, SeekTests, Trace, TuckedArms, walled

# Two frames from the robot where it stopped (read with hello only, nothing commanded). Rows run
# top to bottom, columns from his left to his right; the bottom rows are floor.
REJ = [4095, 4095, 4095, 4095, 1548, 517, 70, 70]
LIVE_TOF = {'fl': 539, 'fl_radial': 1134, 'fr': 337, 'fr_radial': 369, 'lb': 232, 'lf': 182, 'rb': 1056, 'rf': 497,
            'rl': 3500, 'rl_radial': 4000, 'rr': 1154, 'rr_radial': 1487}
LIVE_GRID = [4000, 877, 664, 561, 489, 432, 391, 347,
             4000, 783, 674, 562, 486, 430, 391, 343,
             1280, 799, 654, 564, 504, 440, 395, 344,
             4000, 800, 655, 565, 488, 442, 386, 350,
             1177, 767, 658, 564, 495, 437, 386, 346,
             4000, 681, 4000, 544, 485, 430, 383, 341,
             4000, 4000, 4000, 4000, 451, 421, 382, 343,
             300, 314, 324, 329, 326, 316, 306, 307]
# The same door a moment later: the column on its edge read 0.57-0.64 m instead of 0.78 m.
RAGGED_TOF = {**LIVE_TOF, 'fl_radial': 1076, 'fr_radial': 366, 'lb': 234, 'lf': 181, 'rb': 1048, 'rf': 504}
RAGGED_GRID = [4000, 782, 665, 553, 491, 437, 386, 352,
               4000, 836, 632, 555, 486, 433, 387, 347,
               1224, 810, 645, 552, 504, 437, 400, 345,
               1137, 566, 641, 559, 493, 436, 388, 345,
               1164, 745, 639, 551, 482, 439, 384, 346,
               641, 637, 612, 554, 485, 431, 387, 347,
               4000, 4000, 4000, 4000, 445, 419, 373, 335,
               287, 309, 321, 324, 323, 316, 301, 312]

# That spot as a room (metres; he starts at the origin facing +x). The door hangs from a frame
# 0.32 m ahead of his front and swings away to his left; its hinge end and slope are where the
# columns put them. What stands beside his left is known only at the two points its side sensors
# hit, so its length and its front corner here are a guess.
DOOR = [((.59, -.13), (1.20, .33))]
FRAME = [((.59, -.13), (.59, -.71)), ((.59, -.71), (-.10, -.71))]
BESIDE = [((-.80, .544), (.22, .410)), ((.22, .410), (.30, 1.2))]
SPOT = DOOR + FRAME + BESIDE
OPEN_DOOR = DOOR + [((.59, -.13), (.59, -2.))]    # The same door with nothing beside him.


def frame(grid, tof):
    return {'grid': grid, 'rej': REJ, 'received_at': 0.}, {'tof_mm': tof, 'received_at': 0.}


class Pose:
    """Where a room's sensors are read from."""
    def __init__(self, x=0., y=0., theta=0.):
        self.x, self.y, self.theta = x, y, theta


def seen_from(room, **pose):
    tof, grid = walled(Pose(**pose), room)()
    return {'grid': grid, 'rej': [4095]*8, 'received_at': 0.}, tof


def gap_to(room, fake):
    """Metres between his ring and the nearest wall of the room."""
    def to(a, b):
        ex, ey = b[0] - a[0], b[1] - a[1]
        t = max(0., min(1., ((fake.x - a[0])*ex + (fake.y - a[1])*ey) / (ex*ex + ey*ey)))
        return math.hypot(fake.x - a[0] - t*ex, fake.y - a[1] - t*ey)
    return min(to(a, b) for a, b in room) - .27


class SurfaceReadingTests(unittest.TestCase):
    def test_the_door_he_was_stuck_at_is_one_surface_falling_away_to_his_left(self):
        matrix, telemetry = frame(LIVE_GRID, LIVE_TOF)
        space = clearance(telemetry, matrix, 0.)
        # What the navigation had of it: three nearest-thing numbers.
        self.assertEqual([round(space[k], 2) for k in ('path_left_m', 'path_center_m', 'path_right_m')], [.65, .48, .34])
        surface = front_surface(matrix, LIVE_TOF)
        self.assertEqual(surface['side'], 1)                         # It falls away, and ends, on his left.
        self.assertAlmostEqual(surface['slant_deg'], 54., delta=4.)  # About 54 degrees off square-on,
        self.assertAlmostEqual(surface['edge_bearing_deg'], 10., delta=3.)   # ending 10 degrees to his left
        self.assertAlmostEqual(surface['edge_m'], 1., delta=.1)              # a metre out,
        self.assertAlmostEqual(surface['along_deg'], 44., delta=5.)          # so a 44 degree left turn runs along it.
        # His round body can roll 0.29 m before reaching it; the old rule stopped him after 0.08 m.
        self.assertAlmostEqual(surface['room_m'], .29, delta=.02)
        self.assertAlmostEqual(space['path_center_m'] - .40, .08, delta=.01)

    def test_the_pull_to_its_open_side_no_longer_loses_to_what_is_beside_him(self):
        matrix, telemetry = frame(LIVE_GRID, LIVE_TOF)
        space = clearance(telemetry, matrix, 0.)
        self.assertLess(steer(space, LIVE_TOF), -.15)       # Before: to the right, toward the door's near end.
        # Now: straight on. Not to the left either, while the thing 18 cm beside his left is there.
        self.assertAlmostEqual(steer(space, LIVE_TOF, front_surface(matrix, LIVE_TOF)), 0., delta=.06)
        # With his left clear the same door turns him left along it.
        clear = {**LIVE_TOF, 'lf': 1500, 'lb': 1500}
        self.assertGreater(steer(space, clear, front_surface(matrix, clear)), .2)

    def test_the_unsteady_column_on_the_doors_edge_is_still_its_end(self):
        matrix, _ = frame(RAGGED_GRID, RAGGED_TOF)
        surface = front_surface(matrix, RAGGED_TOF)
        self.assertIsNotNone(surface)
        self.assertEqual(surface['side'], 1)
        self.assertAlmostEqual(surface['slant_deg'], 54., delta=4.)

    def test_only_a_slanted_surface_with_an_end_in_view_counts(self):
        wall = [((.8, -3.), (.8, 3.))]
        self.assertIsNone(front_surface(*seen_from(wall)))                  # A wall facing him.
        self.assertIsNone(front_surface(*seen_from(wall, theta=15.)))       # Not slanted enough to run along.
        # Slanted, but it carries on past the view on the side it falls away to: no end, no way shown.
        self.assertIsNone(front_surface(*seen_from([((.3, -3.), (3., 1.5))])))
        self.assertIsNone(front_surface(*seen_from([((.9, .05), (.9, -.05))])))   # A post: too narrow.
        self.assertIsNone(front_surface(*seen_from([((2.4, -1.), (3.4, 0.))])))    # Too far to matter yet.
        matrix, tof = seen_from(DOOR + FRAME)
        self.assertEqual(front_surface(matrix, tof)['side'], 1)
        # Mirrored, it falls away to his right.
        mirrored = [((ax, -ay), (bx, -by)) for (ax, ay), (bx, by) in DOOR + FRAME]
        surface = front_surface(*seen_from(mirrored))
        self.assertEqual(surface['side'], -1)
        self.assertLess(surface['along_deg'], 0)

    def test_a_door_and_the_frame_it_hangs_from_are_read_apart(self):
        # Turned 8 degrees right he faces the hinge: the frame on the right squarely, the door slanting off left.
        surface = front_surface(*seen_from(DOOR + FRAME, theta=-8.))
        self.assertEqual(surface['side'], 1)
        self.assertGreater(surface['slant_deg'], 35.)

    def test_something_else_straight_ahead_keeps_the_ordinary_stand_off(self):
        from r3x.drive import DriveBase
        legs = [((.62, .0), (.62, -.06))]                 # A leg standing 0.35 m ahead, in front of the door.
        matrix, tof = seen_from(OPEN_DOOR + legs)
        surface = front_surface(matrix, tof)
        self.assertEqual(surface['side'], 1)              # The door is still read behind it,
        self.assertAlmostEqual(surface['ahead_other_m'], .35, delta=.03)
        space = clearance({'tof_mm': tof, 'received_at': 0.}, matrix, 0.)
        self.assertEqual(DriveBase._crawl_hold(space, tof, surface), 'path_blocked')   # but he does not creep up to the leg.
        matrix, tof = seen_from(OPEN_DOOR)
        self.assertIsNone(front_surface(matrix, tof)['ahead_other_m'])
        space = clearance({'tof_mm': tof, 'received_at': 0.}, matrix, 0.)
        self.assertIsNone(DriveBase._crawl_hold(space, tof, front_surface(matrix, tof)))

    def test_what_is_beside_his_path_or_already_being_passed_is_not_in_the_way(self):
        self.assertAlmostEqual(body_room([(.8, 0.)]), .48, places=2)       # Dead ahead: his front reaches it.
        self.assertGreater(body_room([(.6, .2)]), .34)                     # Off to one side his round shoulder has farther to go,
        self.assertGreater(body_room([(.6, .4)]), 3.)                      # and beyond his width it is not in the way,
        self.assertGreater(body_room([(.02, .30)]), 3.)                    # nor is something he is already passing.

    def test_a_point_seen_a_moment_ago_is_kept_only_where_the_columns_cannot_look(self):
        seen = [(1., 0.), (.4, -.3)]                       # In the room: one ahead, one ahead on his right.
        self.assertEqual(out_of_view(seen, (0., 0.), 0.), [(.4, -.3)])     # Facing it, the columns see the first.
        hidden = out_of_view(seen, (0., 0.), 60.)                          # Turned left, both are out of their view.
        self.assertEqual(len(hidden), 2)

    def test_a_corner_between_two_side_beams_stops_a_spin_the_beams_alone_allow(self):
        tof = {'fl': 3000, 'fr': 3000, 'rl': 2000, 'rr': 2000, 'lf': 4000, 'lb': 4000, 'rf': 1500, 'rb': 1500}
        self.assertEqual(allowed_turn(45., {'tof_mm': tof}, ARMS_TUCKED), (45., None))
        corner = [(.04, .34)]        # Seen a moment ago, 7 cm off his left side, between the two left beams.
        allowed, note = allowed_turn(45., {'tof_mm': tof}, ARMS_TUCKED, corner)
        self.assertEqual(allowed, 0.)
        self.assertIn('remembered', note)
        self.assertEqual(allowed_turn(-45., {'tof_mm': tof}, ARMS_TUCKED, corner), (-45., None))   # Away from it is free.
        self.assertAlmostEqual(beside_gap(corner, 1), .07, places=2)
        self.assertIsNone(beside_gap(corner, -1))


class SlantedDoorTests(SeekTests):
    def room(self, walls, **pose):
        clock, fake, drive, world = self.start([])
        drive.servos = TuckedArms()
        drive.trace = Trace()
        fake.axle_aft = .23            # He turns about his axle, as on the floor.
        for key, value in pose.items():
            setattr(fake, key, value)
        fake.front = None
        base, gaps = walled(fake, walls), []
        def sensors():
            gaps.append(gap_to(walls, fake))
            return base()
        fake.sensors = sensors
        settle()
        return clock, fake, drive, world, gaps

    @staticmethod
    def steering(fake):
        return [c['assist_gain'] for c in fake.commands if c['cmd'] == 'config' and 'assist_gain' in c]

    def test_an_angled_door_with_room_past_its_end_is_rolled_along(self):
        """Before: refused, "path blocked", without rolling, because the door's middle was under
        0.40 m away. (From 8 cm farther back the old code also rolled along it: the firmware leans
        away from the nearer front half. Inside 0.40 m, or with something beside him, it refused.)"""
        clock, fake, drive, world, gaps = self.room(OPEN_DOOR, x=.08)
        space = drive.space()
        self.assertLess(space['path_center_m'], .40)                       # A block like any wall, under the old rule.
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=2.))
        step = receipt.steps[0]
        self.assertEqual((receipt.status, step['reason']), ('completed', None), receipt)
        self.assertAlmostEqual(step['travelled_m'], 2., delta=.1)
        self.assertGreater(step['steered_deg'], 30.)                       # Left, along the door,
        self.assertGreater(fake.y, .8)                                     # and past its end.
        self.assertGreater(min(gaps), .10, min(gaps))                      # Never nearer to it than this.
        self.assertFalse([c for c in fake.commands if c['cmd'] == 'move' and c['dist'] < 0])
        self.assertFalse([c for c in fake.commands if c['cmd'] == 'turn'])   # One roll, no stopping to turn.
        turns = [c['ang'] for c in fake.commands if c['cmd'] == 'drive' and c['lin'] > 0]
        self.assertLess(max(abs(b - a) for a, b in zip(turns, turns[1:])), .45)   # No swinging from side to side.
        gains = self.steering(fake)
        self.assertIn(0., gains)               # The firmware's centring was off while he ran along it,
        self.assertEqual(gains[-1], 2.)        # and back on afterwards.

    def test_where_he_stopped_that_night_he_creeps_up_and_turns_only_as_far_as_is_safe(self):
        """Before, in this room: a turn to the right away from the thing beside him, into the corner
        where the door hangs, then "path blocked" without rolling. Now he creeps up to the door and
        turns toward its open end. Here that turn is cut to 14 degrees and the next one refused:
        the corner of the thing beside him, seen by his left front sensor as he passed it, sits
        between the two left beams by then, and a wider spin would swing his side into it."""
        clock, fake, drive, world, gaps = self.room(SPOT)
        tof = drive.link.snapshot()[0]['tof_mm']
        self.assertAlmostEqual(tof['lf'], LIVE_TOF['lf'], delta=10)        # The room reads as the robot did:
        self.assertAlmostEqual(tof['lb'], LIVE_TOF['lb'], delta=10)        # the thing beside him,
        self.assertAlmostEqual(tof['fr_radial'], LIVE_TOF['fr_radial'], delta=10)   # the frame at his right front,
        columns = drive.link.snapshot()[1]['grid'][:8]
        for col in range(1, 8):                                             # and the door's slope.
            self.assertAlmostEqual(columns[col], LIVE_GRID[24 + col], delta=45)
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=3.05))
        step = receipt.steps[0]
        self.assertGreater(step['travelled_m'], .2, receipt)               # He inched forward (0.0 before),
        self.assertGreater(fake.theta, 10.)                                # turned left toward the open end, not right,
        self.assertGreater(min(gaps), .06, min(gaps))                      # and touched nothing.
        self.assertEqual(step['stopped_by'], 'path_blocked')
        first, second = step['turned_along_surface']
        self.assertEqual(first['open_side'], 'left')
        self.assertGreater(first['turned_deg'], 10.)
        self.assertEqual(second['turned_deg'], 0.)
        self.assertIn('remembered', second['reason'])                      # The refusal says what it was for.
        self.assertFalse([c for c in fake.commands if c['cmd'] == 'turn' and c['deg'] < 0])
        self.assertFalse([c for c in fake.commands if c['cmd'] == 'move' and c['dist'] < 0])
        self.assertEqual(self.steering(fake)[-1], 2.)

    def test_with_a_little_more_room_beside_him_he_turns_at_the_door_and_gets_through(self):
        """The same door with the thing beside him 12 cm farther off: creep up, turn, roll on.
        Before: the same right turn into the corner and "path blocked" without rolling."""
        farther = [((ax, ay + .12), (bx, by + .12)) for (ax, ay), (bx, by) in BESIDE]
        walls = DOOR + FRAME + farther
        clock, fake, drive, world, gaps = self.room(walls)
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=2.))
        step = receipt.steps[0]
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertGreater(step['travelled_m'], 1.5, receipt)
        self.assertGreater(fake.y, .6)                                     # Past the door's end, on its open side.
        self.assertGreater(min(gaps), .04, min(gaps))
        self.assertFalse([c for c in fake.commands if c['cmd'] == 'move' and c['dist'] < 0])

    def test_come_to_me_past_an_angled_door_needs_no_camera_look(self):
        """Before: two paid looks at the floor and two detours, 160-170 degrees of turning."""
        clock, fake, drive, world, gaps = self.room(OPEN_DOOR)
        drive.router = Router()
        world.people.append(('face-1', 'person-1', 3.4, .5))
        targets = {'nearest_person': {'target_id': 'nearest_person', 'kind': 'nearest_person', 'visible_now': True}}
        receipt = drive.execute(DrivePlan('p', (DriveStep('approach', target='nearest_person'),), 'requested', 'come',
                                          targets, clock() + 300), Cancellation())
        step = receipt.steps[0]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual([d['by'] for d in step['detours']], ['surface'])
        self.assertEqual(step['detours'][0]['side'], 'left')
        self.assertEqual(drive.router.facts, [])                           # The depth columns were enough.
        self.assertLess(step['turned_deg'], 120.)
        self.assertAlmostEqual(math.hypot(fake.x - 3.4, fake.y - .5), 1.25, delta=.2)   # At the 4 ft stop.
        self.assertGreater(min(gaps), .12, min(gaps))


if __name__ == '__main__':
    unittest.main()
