"""Room trips against the simulated base (November 22, run 8cece04a): going into a room through its doorway,
searching toward a doubtful sighting, following a remembered route, a look-around, asking the way, and his own
moves staying out of doorways. Synthetic sensors, a fake locator and a fake room map; no wheels move."""
import math
import unittest

from r3x.contracts import Cancellation
from r3x.drive import DrivePlan, DriveStep
from r3x.room_map import wrap
from tests.test_drive import base_setup, settle


def doorway(fake, at, length=.4, side=.12, room=2.):
    """Side sensors for a doorway that starts `at` metres along x: open room before and after it."""
    def sensors():
        inside = at <= fake.x <= at + length
        mm = int((side if inside else room) * 1000)
        return {'lf': mm, 'lb': mm, 'rf': mm, 'rb': mm}, None
    return sensors


def hall(fake, door=(.6, 1.), end=2.2, side=.12, room=2.):
    """A hall along x with an open door on the left between `door`, opening into a room at `end`."""
    def sensors():
        if fake.x >= end:
            left = right = room
        else:
            right, left = side, (room if door[0] <= fake.x <= door[1] else side)
        return {'lf': int(left * 1000), 'lb': int(left * 1000), 'rf': int(right * 1000), 'rb': int(right * 1000)}, None
    return sensors


class FakeMap:
    """What the drive owner asks the room map: where he is, a route, a look, how a trip went."""
    def __init__(self, fake, route=None, spot='A', end='B'):
        self.fake, self.route, self.spot, self.end = fake, route, spot, end
        self.offsets, self.captures, self.results, self.trips, self.ended = {spot: 0.}, 0, [], [], []

    def where(self):
        return {'spot': self.spot, 'facing': None, 'room_id': None, 'room_sure': False}

    def mapped(self, room_id):
        return self.route is not None

    def route_to(self, room_id):
        return self.route or {'kind': 'none', 'reason': 'way_not_known'}

    def relative(self, spot, bearing):
        offset = self.offsets.get(spot)
        return None if offset is None else wrap(bearing - (self.fake.theta + offset))

    def offset(self, spot): return self.offsets.get(spot)
    def expect(self, spot, offset): self.offsets[spot] = offset
    def thumb_now(self): return None, None
    def in_room(self, room_id): return False
    def passage_result(self, passage, ok): self.results.append((passage, ok))
    def trip_started(self, room_id, how): self.trips.append((room_id, how))
    def trip_ended(self, ok): self.ended.append(ok)

    def capture(self, cancel=None):
        self.captures += 1
        if self.route and self.route['kind'] == 'passages' and self.fake.x > .5:
            self.spot = self.end
        return self.where()


class RoomTripTests(unittest.TestCase):
    def start(self, things=(), locate=None):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        world.objects[:] = list(things)
        self.calls = []
        def look(after):
            return type('Image', (), {'frame_id': 'f', 'detail': 'high'})()
        def seen(description, image, cancel, hint=None):
            self.calls.append(description)
            for _, label, px, py in world.objects:
                distance, bearing, _, _ = world.relative(px, py)
                if label == description and abs(bearing) < 45:
                    return {'found': True, 'x': .5 - bearing/90, 'y': .5, 'width': .62, 'height': .8,
                            'confidence': 'high', 'note': 'room through the doorway'}
            return {'found': False, 'x': 0, 'y': 0, 'width': 0, 'height': 0, 'confidence': 'low', 'note': 'not visible'}
        drive.look, drive.locator = look, (locate(seen, world) if locate else seen)
        settle()
        return clock, fake, drive, world

    def run_plan(self, clock, drive, *steps, origin='requested'):
        return drive.execute(DrivePlan('p', tuple(steps), origin, 'go there', {}, clock() + 120), Cancellation())

    def test_a_room_seen_through_its_doorway_is_gone_into_not_reached(self):
        # Run 8cece04a: the living room filled 62% of the view from outside and he said "Living room" at 0.0 m.
        clock, fake, drive, world = self.start([('room', 'the living room', 9., 0.)])
        fake.sensors = doorway(fake, at=1.)
        fake.assist_on = False
        receipt = self.run_plan(clock, drive, DriveStep('go_to', description='the living room'))
        step = receipt.steps[0]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual(step['reason'], 'entered_room')
        self.assertGreater(fake.x, 1.4 + .7)                    # Through the opening and on into the room.
        self.assertLess(fake.x, 4.)
        self.assertEqual(step['how'], 'sight')
        self.assertEqual(step['ask_if_this_is'], 'the living room')

    def test_a_doubtful_sighting_on_the_right_turns_the_search_right(self):
        # Run 8cece04a: a blurred look saw the living room on his right; it was dropped and he searched left.
        first = [True]
        def doubtful(seen, world):
            def locate(description, image, cancel, hint=None):
                if first[0]:
                    first[0] = False
                    self.calls.append(description)
                    return {'found': True, 'x': .62, 'y': .5, 'width': .7, 'height': .9, 'confidence': 'low',
                            'note': 'room with lamp and sofa to the right; blurry image'}
                return seen(description, image, cancel, hint)
            return locate
        clock, fake, drive, world = self.start([('room', 'the living room', 2., -1.4)], locate=doubtful)   # ~35 right
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='the living room'))
        self.assertEqual(receipt.steps[0]['status'], 'completed', receipt)
        turns = [c['deg'] for c in fake.commands if c['cmd'] == 'turn']
        self.assertLess(turns[0], 0)                             # Right, toward the doubtful sighting.
        self.assertFalse(any(t > 0 and abs(t) > 40 for t in turns))   # Never the big sweep left.
        self.assertLessEqual(len(self.calls), 3)

    def test_a_remembered_route_is_driven_by_its_headings_with_no_looks(self):
        clock, fake, drive, world = self.start()
        route = {'kind': 'passages', 'goal': 'B', 'room_id': 'kitchen',
                 'legs': [{'passage_id': 'p1', 'from': 'A', 'to': 'B', 'arrive': 0., 'marks': [], 'doorways': 0,
                           'segments': [{'heading': 90., 'length_m': 1.}, {'heading': 0., 'length_m': .8}]}]}
        drive.navigator = FakeMap(fake, route)
        receipt = self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen'))
        step = receipt.steps[0]
        self.assertEqual((step['status'], step['reason'], step['how']), ('completed', 'arrived_by_map', 'map'), receipt)
        self.assertAlmostEqual(fake.y, 1., delta=.25)
        self.assertAlmostEqual(fake.x, .8, delta=.25)
        self.assertEqual(self.calls, [])                          # By the map: no paid camera looks.
        self.assertEqual(drive.navigator.results, [('p1', True)])
        self.assertEqual(drive.navigator.trips, [('kitchen', 'map')])
        self.assertEqual(drive.navigator.ended, [True])

    ROUTE = {'kind': 'passages', 'goal': 'B', 'room_id': 'kitchen',
             'legs': [{'passage_id': 'p1', 'from': 'A', 'to': 'B', 'arrive': 0., 'marks': [], 'doorways': 0,
                       'segments': [{'heading': 0., 'length_m': 1.}]}]}

    def test_the_old_room_guess_is_not_already_being_there(self):
        # Second review: the look-alike vote (the one that called the hallway the dining room) counted as being
        # there, so "go to the dining room" could answer "already here" without moving.
        clock, fake, drive, world = self.start()
        nav = drive.navigator = FakeMap(fake, self.ROUTE)
        nav.where = lambda: {'spot': nav.spot, 'facing': None, 'room_id': 'kitchen', 'room_sure': True}
        step = self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen')).steps[0]
        self.assertEqual(step['reason'], 'arrived_by_map', step)
        self.assertGreater(fake.x, .7)
        # On a spot a person named as that room, he is there.
        clock, fake, drive, world = self.start()
        nav = drive.navigator = FakeMap(fake, self.ROUTE)
        nav.in_room = lambda room_id: room_id == 'kitchen'
        step = self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen')).steps[0]
        self.assertEqual(step['reason'], 'already_here', step)
        self.assertLess(fake.x, .1)

    def test_an_arrival_look_that_cannot_place_him_is_not_confirmed(self):
        clock, fake, drive, world = self.start()
        nav = drive.navigator = FakeMap(fake, self.ROUTE)
        nav.capture = lambda cancel=None: {'spot': None, 'facing': None, 'room_id': None, 'room_sure': False}
        step = self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen')).steps[0]
        self.assertEqual((step['status'], step['reason']), ('completed', 'arrived_not_confirmed'), step)
        self.assertTrue(step['ask_if_this_is'])                   # He asks whether this is it.
        self.assertEqual(nav.results, [])                         # The passage is neither proven nor failed.

    def test_an_open_door_along_a_hall_is_not_the_room(self):
        # Second review: one side opening for a moment counted as going in, and he "entered" in the hall.
        small = lambda seen, world: (lambda *a, **k: {**seen(*a, **k), 'width': .15})
        clock, fake, drive, world = self.start([('room', 'the living room', 9., 0.)], locate=small)
        fake.sensors = hall(fake)
        fake.assist_on = False
        step = self.run_plan(clock, drive, DriveStep('go_to', description='the living room')).steps[0]
        self.assertEqual((step['status'], step['reason']), ('completed', 'entered_room'), step)
        self.assertGreater(fake.x, 2.2 + .7)                      # Past the hall's end and on into the room.

    def test_after_dictated_turns_the_person_is_trusted_over_the_map(self):
        clock, fake, drive, world = self.start([('room', 'the living room', -.3, -6.)])   # Behind-right after the turn
        route = {'kind': 'passages', 'goal': 'B', 'room_id': 'lr',
                 'legs': [{'passage_id': 'p1', 'from': 'A', 'to': 'B', 'arrive': 0., 'marks': [], 'doorways': 0,
                           'segments': [{'heading': 180., 'length_m': 1.}]}]}
        drive.navigator = FakeMap(fake, route)
        receipt = self.run_plan(clock, drive, DriveStep('turn', degrees=-90.),
                                DriveStep('go_to', target='lr', description='the living room', prefer='right', directed=True))
        self.assertEqual(drive.navigator.results, [])            # The map's way was never driven.
        self.assertEqual(drive.navigator.trips, [('lr', 'directions')])
        self.assertEqual(receipt.steps[1]['how'], 'directions', receipt)

    def test_a_room_the_map_has_never_reached_is_looked_for_at_once(self):
        # No spin to find himself first when nothing on the map leads there anyway.
        clock, fake, drive, world = self.start([('room', 'the living room', 9., 0.)])
        fake.sensors = doorway(fake, at=1.)
        fake.assist_on = False
        drive.navigator = FakeMap(fake, spot=None)
        receipt = self.run_plan(clock, drive, DriveStep('go_to', target='lr', description='the living room'))
        self.assertEqual(receipt.steps[0]['reason'], 'entered_room', receipt)
        self.assertEqual(drive.navigator.captures, 0)
        self.assertEqual(drive.navigator.trips, [('lr', 'sight')])

    def test_no_way_known_and_nothing_seen_says_so_so_he_can_ask(self):
        clock, fake, drive, world = self.start()
        drive.navigator = FakeMap(fake)
        receipt = self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen'))
        step = receipt.steps[0]
        self.assertEqual(step['status'], 'route_unknown', receipt)
        self.assertEqual(drive.navigator.ended, [False])

    def test_a_look_around_takes_eight_looks_in_a_full_turn(self):
        clock, fake, drive, world = self.start()
        drive.navigator = FakeMap(fake)
        receipt = self.run_plan(clock, drive, DriveStep('look_around'))
        step = receipt.steps[0]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual(drive.navigator.captures, 8)
        self.assertGreater(step['turned_deg'], 290.)

    def with_head(self, fake, drive, works=True):
        """A neck the drive owner can turn; each capture records where the camera pointed (degrees left of
        his heading at the start) and which way the head was turned."""
        from r3x.drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
        head = {'neck': 0., 'moves': []}
        class Owner:
            def neck_degrees(self): return head['neck']
        class Servos:
            owner = Owner()
            def prepare_base_motion(self, cancel):
                return type('R', (), {'status': 'pulses_reached', 'reason': None})()
            def finish_base_motion(self, look_pending=False, keep_parked=frozenset()): pass
            def execute_pose(self, pose, cancel, request_id):
                if not works:
                    return type('R', (), {'status': 'refused'})()
                head['neck'] = (NECK_CENTER - pose[0]) / NECK_PULSES_PER_DEG
                head['moves'].append(round(head['neck'], 1))
                return type('R', (), {'status': 'pulses_reached'})()
        drive.servos = Servos()
        nav, start = FakeMap(fake), fake.theta
        shots = []
        def capture(cancel=None):
            nav.captures += 1
            shots.append((round(wrap(fake.theta - start + head['neck'])), round(head['neck'], 1)))
            return nav.where()
        nav.capture = capture
        drive.navigator = nav
        return head, shots

    def test_a_look_around_turns_the_head_right_and_left_at_four_stops(self):
        # Owner, November 25: "4 moves not 8 of the wheels, but the head moves to fill in the 8 directions."
        clock, fake, drive, world = self.start()
        head, shots = self.with_head(fake, drive)
        receipt = self.run_plan(clock, drive, DriveStep('look_around'))
        step = receipt.steps[0]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual((step['looks'], step['wheel_turns'], step['head']), (8, 3, True))
        self.assertTrue(260. < step['turned_deg'] < 280., step)               # Three quarter turns.
        for (_, neck), side in zip(shots, [-22.5, 22.5] * 4):                   # Right, then left, at each stop.
            self.assertAlmostEqual(neck, side, delta=.5)
        self.assertEqual(len(shots), 8)
        directions = sorted(d % 360 for d, _ in shots)
        gaps = [(b - a) for a, b in zip(directions, directions[1:] + [directions[0] + 360])]
        self.assertTrue(all(abs(g - 45.) <= 6. for g in gaps), directions)   # Eight directions, 45 apart.
        self.assertEqual(head['moves'][-1], 0.)                                # Centred again at the end.

    def test_a_quarter_turn_refused_for_room_is_tried_at_45(self):
        clock, fake, drive, world = self.start()
        head, shots = self.with_head(fake, drive)
        turn, refused = drive._turn, []
        def tight(degrees, *args, **kwargs):
            if degrees > 60 and not refused:
                refused.append(degrees)
                return {'action': 'turn', 'status': 'refused', 'reason': 'turn_path_blocked: test', 'turned_deg': 0.}
            return turn(degrees, *args, **kwargs)
        drive._turn = tight
        step = self.run_plan(clock, drive, DriveStep('look_around')).steps[0]
        self.assertEqual(step['status'], 'completed', step)
        self.assertEqual((step['looks'], step['turns_refused']), (8, 1))
        self.assertNotIn('missed', step)
        directions = sorted(d % 360 for d, _ in shots)
        self.assertTrue(all(abs(b - a - 45.) <= 6. for a, b in zip(directions, directions[1:])), directions)

    def test_a_head_that_will_not_turn_leaves_the_look_around_to_the_wheels(self):
        clock, fake, drive, world = self.start()
        head, shots = self.with_head(fake, drive, works=False)
        step = self.run_plan(clock, drive, DriveStep('look_around')).steps[0]
        self.assertEqual(step['status'], 'completed', step)
        self.assertEqual(step['looks'], 8)
        self.assertFalse(step['head'])
        self.assertTrue(all(n == 0. for _, n in shots))

    def test_his_own_roll_toward_something_past_a_doorway_is_not_an_arrival(self):
        small = lambda seen, world: (lambda *a, **k: {**seen(*a, **k), 'width': .15})   # Far off: small in view.
        clock, fake, drive, world = self.start([('chair', 'the red chair', 6., 0.)], locate=small)
        fake.sensors = doorway(fake, at=1.)
        fake.assist_on = False
        drive._sought = {'description': 'the red chair'}
        receipt = self.run_plan(clock, drive, DriveStep('approach', target='sought'), origin='self_initiated')
        self.assertEqual((receipt.steps[0]['status'], receipt.steps[0]['reason']), ('stopped', 'kept_out_of_doorway'), receipt)
        self.assertIsNone(drive._reached)

    def test_after_a_trip_someone_asked_for_fails_his_own_moves_wait(self):
        # Run e54062b9: the owner's trip failed and, while he was still giving directions, Rex swivelled on his own.
        from r3x.drive import ERRAND_HOLD_S
        clock, fake, drive, world = self.start()
        drive.navigator = FakeMap(fake)
        failed = self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen'))
        self.assertNotEqual(failed.status, 'completed')
        own = self.run_plan(clock, drive, DriveStep('turn', degrees=-40.), origin='self_initiated')
        self.assertEqual((own.status, own.reason), ('refused', 'errand_unfinished'))
        offered = drive.context('self_initiated')
        self.assertFalse(offered['available'])
        self.assertIn('waits for them', offered['unavailable_meaning'])
        self.assertTrue(drive.context('requested')['available'])     # What they ask for still goes.
        clock.now += ERRAND_HOLD_S + 1
        settle()                                                      # Fresh telemetry after the jump in time.
        own = self.run_plan(clock, drive, DriveStep('turn', degrees=-40.), origin='self_initiated')
        self.assertEqual(own.status, 'completed', own)
        # A trip they asked for that completes ends the wait at once.
        self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen'))
        done = self.run_plan(clock, drive, DriveStep('turn', degrees=30.))
        self.assertEqual(done.status, 'completed')
        own = self.run_plan(clock, drive, DriveStep('turn', degrees=-40.), origin='self_initiated')
        self.assertEqual(own.status, 'completed', own)

    def test_his_own_move_stops_where_a_doorway_begins(self):
        clock, fake, drive, world = self.start()
        fake.sensors = doorway(fake, at=1.)
        fake.assist_on = False
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=2.5), origin='self_initiated')
        self.assertEqual(receipt.steps[0]['reason'], 'kept_out_of_doorway', receipt)
        self.assertLess(fake.x, 1.25)
        # Asked by a person, the same move goes through.
        clock, fake, drive, world = self.start()
        fake.sensors = doorway(fake, at=1.)
        fake.assist_on = False
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=2.5))
        self.assertGreater(fake.x, 1.6, receipt)


class RoomTripValidationTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from r3x.memory import Memory
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        from tests.test_voice_core import Trace
        self.temp = tempfile.TemporaryDirectory()
        self.store = Memory(Path(self.temp.name).resolve() / 'core.sqlite3'); self.store.start()
        self.state = CoreState(self.store, SpeakerTracker('synthetic:3'), Trace())

    def tearDown(self):
        self.store.close(); self.temp.cleanup()

    def offered(self, origin='requested'):
        return {'source_turn_id': 't', 'state_revision': self.store.revision,
                'drive': {'available': True, 'origin': origin, 'targets': [], 'vision_seek': True},
                'place': {'known_rooms': [{'room_id': 'lr', 'name': 'living room', 'way_known': False}]}}

    def propose(self, *steps, origin='requested'):
        from tests.test_drive_session import drive_call
        return self.state.propose_drive(drive_call(*steps), self.offered(origin), Cancellation())

    def test_room_trips_are_for_requests_and_follow_dictated_turns(self):
        from tests.test_drive_session import step
        go = {**step('go_to'), 'description': 'the living room', 'target': 'lr'}
        plan = self.propose(step('move', meters=.5),
                            step('turn', degrees=-90), go)
        last = plan.steps[-1]
        self.assertEqual((last.action, last.target, last.prefer, last.directed), ('go_to', 'lr', 'right', True))
        self.assertFalse(self.propose(go).steps[0].directed)
        with self.assertRaisesRegex(ValueError, 'RoomTripsOnlyWhenAsked'):
            self.propose(go, origin='self_initiated')
        with self.assertRaisesRegex(ValueError, 'UnknownRoom'):
            self.propose({**go, 'target': 'nowhere'})
        looks = self.propose(step('look_around'))
        self.assertEqual([s.action for s in looks.steps], ['look_around'])

    def test_plans_the_model_writes_other_ways_still_go_into_the_room(self):
        from tests.test_drive_session import step
        go = {**step('go_to'), 'description': 'the living room', 'target': 'lr'}
        # An approach after a go_to adds nothing; it is dropped rather than refusing the plan.
        self.assertEqual([s.action for s in self.propose(go, step('approach', target='sought')).steps], ['go_to'])
        # An approach to the room itself is going into it.
        plan = self.propose({**step('approach', target='lr'), 'description': None})
        self.assertEqual([(s.action, s.target) for s in plan.steps], [('go_to', 'lr')])
        # "Forward" is not a side to look first; a room's name stands for its id.
        self.assertEqual(self.propose({**go, 'direction': 'forward'}).steps[0].prefer, None)
        self.assertEqual(self.propose({**go, 'target': 'Living Room'}).steps[0].target, 'lr')
        # No id at all, only the name as they said it: a room he knows by that name is that room (his map is used).
        self.assertEqual(self.propose({**go, 'target': None}).steps[0].target, 'lr')
        self.assertEqual(self.propose({**go, 'target': None, 'description': 'living room'}).steps[0].target, 'lr')
        self.assertIsNone(self.propose({**go, 'target': None, 'description': 'the den'}).steps[0].target)

    def test_a_seek_for_a_known_room_becomes_going_into_it(self):
        from tests.test_drive_session import step
        plan = self.propose(step('turn', degrees=-90),
                            {**step('seek'), 'description': 'Living Room'}, step('approach', target='sought'))
        self.assertEqual([s.action for s in plan.steps], ['turn', 'go_to'])
        self.assertEqual(plan.steps[1].target, 'lr')
        # A seek for anything else, after a right turn, looks right first.
        plan = self.propose(step('turn', degrees=-90), {**step('seek'), 'description': 'the door'})
        self.assertEqual(plan.steps[1].prefer, 'right')


from tests import test_drive_session as sessions
from tests.test_memory_core import MemoryFixture


class ArrivalQuestionTests(MemoryFixture):
    """Found by sight, he asks once whether this is the room, even with no arrival line planned."""
    setup_drive, turn = sessions.DriveSessionTests.setup_drive, sessions.DriveSessionTests.turn

    def test_a_room_found_by_sight_is_checked_with_the_person(self):
        from tests.test_drive_session import step
        go = {**step('go_to'), 'description': 'the living room'}
        self.setup_drive(sessions.drive_call(go, text='On my way.'), followup='Living room, I think. Is this it?')
        self.world.objects[:] = [('room', 'the living room', 9., 0.)]
        self.fake.sensors = doorway(self.fake, at=1.)
        self.fake.assist_on = False
        self.drive.look = lambda after: type('Image', (), {'frame_id': 'f', 'detail': 'high'})()
        def locate(description, image, cancel, hint=None):
            distance, bearing, _, _ = self.world.relative(9., 0.)
            return {'found': True, 'x': .5 - bearing/90, 'y': .5, 'width': .62, 'height': .8, 'confidence': 'high',
                    'note': 'room through the doorway'}
        self.drive.locator = locate
        settle()
        delivery = self.turn('Go into the living room.')
        self.assertEqual(delivery.completed_text, ['On my way.', 'Living room, I think. Is this it?'])
        notes = [m['content'] for m in self.brain.snapshots[-1].messages if m.get('role') == 'developer']
        self.assertTrue(any('ask them whether this is the living room' in n for n in notes))


class ErrandTests(MemoryFixture):
    """Owner, November 26: "go see if the dog's on the couch". After the look he knows who asked, what, and the
    room he set off from, so he can come back and tell them or send the picture."""
    setup_drive, turn = sessions.DriveSessionTests.setup_drive, sessions.DriveSessionTests.turn

    def test_the_look_after_a_room_trip_carries_the_errand(self):
        from tests.test_drive_session import step
        go = {**step('go_to'), 'description': 'the living room'}
        self.setup_drive(sessions.drive_call(go, text='On my way.', look_after='Is the dog on the couch?'),
                         followup='No dog. Suspicious.', inspection=True)
        self.world.objects[:] = [('room', 'the living room', 9., 0.)]
        self.fake.sensors = doorway(self.fake, at=1.)
        self.fake.assist_on = False
        self.drive.look = lambda after: type('Image', (), {'frame_id': 'f', 'detail': 'high'})()
        def locate(description, image, cancel, hint=None):
            distance, bearing, _, _ = self.world.relative(9., 0.)
            return {'found': True, 'x': .5 - bearing/90, 'y': .5, 'width': .62, 'height': .8, 'confidence': 'high',
                    'note': 'room through the doorway'}
        self.drive.locator = locate
        settle()
        delivery = self.turn('Go see if the dog is on the couch in the living room.')
        self.assertEqual(delivery.completed_text, ['On my way.', 'No dog. Suspicious.'])
        errand = self.brain.snapshots[-1].context['errand']
        self.assertEqual((errand['question'], errand['asked_by']),
                         ('Is the dog on the couch?', self.brain.snapshots[0].context['speaker'].get('display_name')))
        self.assertIn('started_in', errand)
        offered = {d['name'] for d in sessions.grounded_tools(self.brain.snapshots[-1].context)}
        self.assertTrue({'describe_view', 'drive_base'} <= offered)


class DirectionsAndMapTests(unittest.TestCase):
    """Owner, November 24 (run d085dc5f): told "turn left, go forward like ten feet, and you'll be in the workshop", he
    rolled 3.1 m into it and said he got lost; the map's way back to the kitchen met the island and the trip ended in
    sight of it; the workshop seen through the hall's far door was searched for by turning away from it."""
    start, run_plan = RoomTripTests.start, RoomTripTests.run_plan

    def test_after_rolling_where_a_person_said_the_room_is_he_asks_and_never_gets_lost(self):
        clock, fake, drive, world = self.start()                 # Nothing the picture search can name.
        drive.navigator = FakeMap(fake)
        receipt = self.run_plan(clock, drive, DriveStep('turn', degrees=90.), DriveStep('move', meters=1.5),
                                DriveStep('go_to', target='ws', description='the workshop', directed=True))
        step = receipt.steps[-1]
        self.assertEqual((step['status'], step['reason']), ('completed', 'directions_followed'), receipt)
        self.assertEqual(step['ask_if_this_is'], 'the workshop')   # "Is this the workshop?"
        self.assertEqual(len(self.calls), 1)                      # One look ahead, no search spin.
        turns = [c for c in fake.commands if c['cmd'] == 'turn']
        self.assertEqual(len(turns), 1)                           # Only the person's own turn.

    def test_the_distance_rolled_on_their_directions_counts_toward_being_in_the_room(self):
        # The whole view is the workshop once he has rolled where they said: there, not "fills the view from its door".
        clock, fake, drive, world = self.start([('room', 'the workshop', 3.5, 0.)])
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=1.6),
                                DriveStep('go_to', target='ws', description='the workshop', directed=True))
        step = receipt.steps[-1]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual(step['how'], 'directions')
        self.assertEqual(step['ask_if_this_is'], 'the workshop')
        self.assertLess(step.get('travelled_m', 0.), 1.)          # No second long approach first.

    def test_without_their_distance_a_room_filling_the_view_from_its_door_is_still_not_arrival(self):
        # A turn alone ("go right, into the living room") is not having rolled into it.
        clock, fake, drive, world = self.start([('room', 'the living room', 9., 0.)])
        fake.sensors = doorway(fake, at=1.)
        fake.assist_on = False
        receipt = self.run_plan(clock, drive, DriveStep('turn', degrees=10.), DriveStep('turn', degrees=-10.),
                                DriveStep('go_to', target='lr', description='the living room', directed=True))
        self.assertEqual(receipt.steps[-1]['reason'], 'entered_room', receipt)
        self.assertGreater(fake.x, 1.4)

    def test_a_mapped_way_that_is_blocked_goes_on_by_sight(self):
        clock, fake, drive, world = self.start([('box', 'a box', 1.2, 0.), ('room', 'the kitchen', 2.5, 2.)])
        route = {'kind': 'passages', 'goal': 'B', 'room_id': 'kitchen',
                 'legs': [{'passage_id': 'p1', 'from': 'A', 'to': 'B', 'arrive': 0., 'marks': [], 'doorways': 0,
                           'segments': [{'heading': 0., 'length_m': 2.4}]}]}
        drive.navigator = nav = FakeMap(fake, route)
        receipt = self.run_plan(clock, drive, DriveStep('go_to', target='kitchen', description='the kitchen'))
        step = receipt.steps[0]
        self.assertEqual(step['how'], 'map_then_sight', receipt)
        self.assertTrue(step['map_blocked'].startswith('blocked_on_the_way'))
        self.assertEqual(nav.trips, [('kitchen', 'map'), ('kitchen', 'sight')])
        self.assertIn('the kitchen', self.calls)                  # He looked for it once the map's way was blocked.
        self.assertEqual(nav.results, [('p1', False)])

    def test_a_picture_search_hears_what_this_houses_room_looks_like(self):
        hints = []
        def remembering(seen, world):
            def locate(description, image, cancel, hint=None):
                hints.append(hint)
                return seen(description, image, cancel, hint)
            return locate
        clock, fake, drive, world = self.start(locate=remembering)
        drive.navigator = nav = FakeMap(fake)
        nav.room_looks = lambda room_id: ['Workshop: white shelves of spray cans, a model train on top, a telescope.']
        self.run_plan(clock, drive, DriveStep('go_to', target='ws', description='the workshop'))
        self.assertTrue(hints and all('spray cans' in (h or '') for h in hints), hints)
        self.assertIsNone(drive._room_looks)                      # Only for that trip.

    def test_a_search_for_something_seen_moments_ago_starts_facing_it(self):
        clock, fake, drive, world = self.start()
        drive._sought = {'description': 'the workshop', 'heading': -70., 'seen_at': clock()}
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='the workshop'))
        turns = [c['deg'] for c in fake.commands if c['cmd'] == 'turn']
        self.assertLess(turns[0], -40, receipt)                   # Right, toward where it was, first.
        self.assertFalse(any(t > 0 for t in turns))               # And the sweep goes on that way.


class NamingBrain:
    """Told the room, the reply model names it with a line."""
    def __init__(self):
        self.snapshots = []

    def reply(self, snapshot, cancel):
        from r3x.contracts import ToolCall
        self.snapshots.append(snapshot)
        if snapshot.continuation:
            yield 'Got it.'
            return
        yield ToolCall('call-' + snapshot.turn_id, 'name_room',
                       {'name': 'kitchen', 'where': 'here', 'source_turn_id': snapshot.turn_id,
                        'text': 'The kitchen. Let me have a look round.', 'expression': None})


class TakeInTheRoomTests(unittest.TestCase):
    """Owner, November 22 and 24: told which room he is in, he turns once in place to photograph it from every side,
    so he knows it whichever way he comes back facing. Through the production session, state and output owners, the
    real room map and the simulated base."""

    def setUp(self):
        from tests.test_places import PlaceFixture
        fixture = type('Fixture', (PlaceFixture,), {'runTest': lambda self: None})()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.building()
        self.places, self.state, self.fixture = fixture.places, fixture.state, fixture

    def say(self, text):
        from uuid import uuid4
        from r3x.contracts import Capture, Transcript
        return self.session.process_turn(Capture(uuid4().hex, 'session', bytes(1024), 98, 99, 99.6),
                                         Transcript(text, -.01, True), self.session.revision)

    def start(self):
        from r3x.output import OutputOwner
        from r3x.scenario import SyntheticSink, SyntheticTTS
        from r3x.session import Session
        clock, fake, drive, world = base_setup(self)
        self.state.trace.clock = clock
        drive.trace = self.state.trace
        drive.start()
        self.addCleanup(lambda: drive.state == 'closed' or drive.close())
        drive.navigator = FakeMap(fake)                    # The look-around's own looks.
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, drive=drive)
        self.session = Session('session', NamingBrain(), self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)
        settle()
        return fake, drive

    def test_named_where_he_has_few_photos_he_turns_once_and_only_once(self):
        self.fixture.look(5)
        fake, drive = self.start()
        delivery = self.say('You are in the kitchen.')
        self.assertEqual(delivery.completed_text, ['The kitchen. Let me have a look round.'])
        taken = [r for r in self.state.trace.rows if r['event'] == 'room_taken_in']
        self.assertEqual(len(taken), 1, taken)
        self.assertEqual(taken[0]['status'], 'completed')
        turns = [c['deg'] for c in fake.commands if c['cmd'] == 'turn']
        self.assertGreater(sum(turns), 240.)                         # Eight looks round a full turn.
        # Told again on the same spot this run: no second turn.
        self.say('Yes, this is the kitchen.')
        self.assertEqual(len([r for r in self.state.trace.rows if r['event'] == 'room_taken_in']), 1)
