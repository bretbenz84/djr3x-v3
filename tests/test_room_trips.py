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
