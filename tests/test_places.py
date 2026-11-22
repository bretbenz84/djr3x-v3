"""Room map contracts (November 22): buildings, spots, photos by direction, passages, names and directions.

Synthetic fingerprints and tiny grey copies with a fake clock, odometer feed and store; no camera, model or
wheels. They prove the bookkeeping and decisions, not that MobileCLIP tells this house's rooms apart."""
from array import array
import math
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sqlite3
import unittest

from r3x.agency import GOAL_KINDS
from r3x.memory import Memory
from r3x.places import Places, RECOGNIZED, UNFAMILIAR, UNSURE, NEW_PLACE, migrate
from r3x.room_map import (DEG_PER_COLUMN, THUMB_H, THUMB_W, Odometer, doorways_in, relative_words,
                          reversed_segments, segments_of, simplify, thumb_shift, wrap)
from r3x.semantics import grounded_tools
from r3x.vision import Frame
from tests.test_memory_core import MemoryFixture


def unit(seed, mix=None, weight=.0):
    """Synthetic 512-d views, built without numpy (the contract runner blocks native libraries)."""
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(512)]
    norm = sum(x * x for x in v) ** .5
    v = [x / norm for x in v]
    if mix is not None: v = [(1 - weight) * a + weight * b for a, b in zip(v, mix)]
    norm = sum(x * x for x in v) ** .5
    return [x / norm for x in v]


def cv2_available():
    try:
        import cv2  # noqa: F401
        return True
    except Exception:
        return False


def native_available():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


KITCHEN, HALL, OFFICE = unit(1), unit(2), unit(3)


def near(room, seed, weight=.88):
    """A fresh view of `room`: mostly the room, partly something new."""
    return unit(100 + seed, room, weight)


class World:
    """Rooms as panoramas: what the camera sees from a spot depends on which way it faces in the world."""
    def __init__(self):
        self.bases, self.panoramas = {}, {}

    def view(self, spot, world_deg, noise=0):
        bases = self.bases.setdefault(spot, [unit(hash((spot, i)) % 100000) for i in range(8)])
        a = (world_deg % 360.) / 45.
        i, f = int(a) % 8, a - int(a)
        v = [(1 - f) * x + f * y for x, y in zip(bases[i], bases[(i + 1) % 8])]
        if noise:
            v = [x + y for x, y in zip(v, (z * .15 for z in unit(noise)))]
        norm = sum(x * x for x in v) ** .5
        return [x / norm for x in v]

    def thumb(self, spot, world_deg):
        """A smooth panorama (knots every 6 degrees, as real rooms change gradually across a view)."""
        rng = random.Random(hash(('pano', spot)) % 100000)
        knots = self.panoramas.setdefault(spot, [[rng.randint(0, 255) for _ in range(60)] for _ in range(THUMB_H)])
        out = []
        for row in knots:
            for j in range(THUMB_W):
                a = ((world_deg + 45. - (j + .5) * DEG_PER_COLUMN) % 360.) / 6.
                i, f = int(a) % 60, a - int(a)
                out.append(int(round((1 - f) * row[i] + f * row[(i + 1) % 60])))
        return bytes(out)


class Encoder:
    capability = SimpleNamespace(provider='fake', model='fake', dependencies=())
    def __init__(self): self.vectors, self.thumbs = {}, {}
    def start(self): pass
    def close(self): pass
    def embed(self, frame): return self.vectors[frame.frame_id]
    def thumbnail(self, frame): return self.thumbs.get(frame.frame_id)


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now


class PlaceFixture(MemoryFixture):
    """A robot standing in a world: telemetry feeds the odometer, frames feed the recognizer."""
    def setUp(self):
        super().setUp()
        self.clock, self.moving = Clock(), [False]
        self.encoder = Encoder()
        self.faces, self.router = [], None
        self.world = World()
        self.yaw0 = 0.            # The world heading of this run's IMU zero.
        self.x = self.y = 0.      # Odometry, in the run's own frame.
        self.yaw = 0.             # IMU yaw this run.
        self.spot_at = 'A'        # Which world spot the robot really stands on (for the views).
        self.start_places()

    def start_places(self, network=None):
        self.odometer = Odometer(self.clock)
        camera = SimpleNamespace(session_id='s', latest=lambda now: self.faces, hub=None)
        self.places = Places(camera, self.encoder, self.store, motion=lambda: self.moving[0], clock=self.clock,
                             wall_clock=lambda: 1.7e9 + self.clock.now, odometer=self.odometer,
                             network=network)
        self.state.places = self.places
        self.places.load()
        self.places._check_network()
        self.telemetry()

    def telemetry(self):
        theta = math.radians(self.yaw)
        self.odometer.update({'imu': {'ok': True, 'yaw': wrap(self.yaw)}, 'odom': {'x': self.x, 'y': self.y, 'theta': theta}})

    def building(self, name='home'):
        with self.store.transaction() as c:
            c.execute("INSERT INTO buildings VALUES('home',?,0,NULL,0,0)", (name,))
        self.places.building_id, self.places.building_how = 'home', 'told'
        self.places._reload()

    def room(self, name, vector, views=6):
        """A room with loose October-style views (legacy photos), for the fallback vote."""
        if self.places.building_id is None:
            if not self.store.rows("SELECT 1 FROM buildings WHERE id='home'"):
                with self.store.transaction() as c:
                    c.execute("INSERT INTO buildings VALUES('home','home',0,NULL,0,0)")
            self.places.building_id, self.places.building_how = 'home', 'told'
        place_id = name.replace(' ', '-')
        with self.store.transaction() as c:
            c.execute('INSERT INTO places(id,name,created_at,source_turn,import_ref,building_id) VALUES(?,?,?,NULL,NULL,?)',
                      (place_id, name, 0., self.places.building_id))
        for i in range(views):
            self.places.map.add_photo(self.places.building_id, None, None, self.places.namespace,
                                      near(vector, hash((name, i)) % 997, .95), source='legacy', room_id=place_id)
        self.places._reload()
        return place_id

    def frame(self, vector, thumb=None):
        key = str(self.clock.now) + ':' + str(random.random())
        self.encoder.vectors[key] = vector
        if thumb is not None: self.encoder.thumbs[key] = thumb
        return Frame(key, 'c', 's', self.clock.now, 4, 4, bytes(48))

    def see(self, vector, times=1, thumb=None):
        for _ in range(times):
            self.clock.now += 1.5
            self.telemetry()
            self.places.observe(self.frame(vector, thumb))
            self.places.tick()

    def look(self, times=1, noise=0):
        """Readings of what the robot really sees, standing where it stands."""
        for i in range(times):
            world = self.yaw0 + self.yaw
            self.see(self.world.view(self.spot_at, world, noise + i if noise else 0), thumb=self.world.thumb(self.spot_at, world))

    def turn(self, degrees):
        steps = max(1, int(abs(degrees) // 10))
        for _ in range(steps):
            self.yaw += degrees / steps; self.clock.now += .1; self.telemetry()
        self.clock.now += 2.; self.telemetry()

    def drive(self, path, arrive_at):
        """Roll along [(dx, dy)] world-frame steps (metres), wheels reporting, and end on spot arrive_at."""
        self.moving[0] = True
        for dx, dy in path:
            heading = math.degrees(math.atan2(dy, dx)) - self.yaw0
            self.yaw = heading
            steps = max(1, int(math.hypot(dx, dy) / .05))
            for _ in range(steps):
                rx, ry = dx / steps, dy / steps
                c, s = math.cos(math.radians(-self.yaw0)), math.sin(math.radians(-self.yaw0))
                self.x += rx * c - ry * s; self.y += rx * s + ry * c
                self.clock.now += .05; self.telemetry()
            self.see(unit(hash((self.clock.now, 'blur')) % 9999))   # A reading while rolling.
        self.moving[0] = False
        self.spot_at = arrive_at
        self.clock.now += 3.; self.telemetry()


class FingerprintTests(unittest.TestCase):
    def test_image_shift_reads_a_small_turn_both_ways(self):
        world = World()
        stored = world.thumb('A', 90.)
        for turned in (-15., -6., 0., 8., 18.):
            degrees, score = thumb_shift(world.thumb('A', 90. + turned), stored)
            self.assertAlmostEqual(degrees, turned, delta=DEG_PER_COLUMN)
            self.assertGreater(score, .9)
        self.assertLess(thumb_shift(world.thumb('B', 90.), stored)[1], .3)   # Another place: no match.
        self.assertIsNone(thumb_shift(None, stored))

    def test_trails_become_segments_in_a_spot_frame_and_back(self):
        trail = [(i, x / 10., 0., 0., None, None) for i, x in enumerate(range(0, 21))]
        trail += [(30 + i, 2., y / 10., 90., None, None) for i, y in enumerate(range(1, 16))]
        segments = segments_of(trail, offset=10.)
        self.assertEqual([round(s['heading']) for s in segments], [10, 100])
        self.assertEqual([round(s['length_m'], 1) for s in segments], [2.0, 1.5])
        back = reversed_segments(segments, arrive=30.)
        self.assertEqual([round(s['heading']) for s in back], [wrap(100 + 180 + 30), wrap(10 + 180 + 30)])
        self.assertEqual(len(simplify([(0, 0), (1, .05), (2, 0)])), 2)

    def test_doorways_are_squeezes_that_open_again(self):
        self.assertEqual(doorways_in([(1.5, 1.2), (.2, .25), (.15, .2), (1.4, .3), (.3, .3), (.3, .2), (2., 2.)]), 2)
        self.assertEqual(doorways_in([(.3, .3)] * 10), 0)   # A hall he never left.

    def test_relative_words(self):
        self.assertEqual(relative_words(5), 'ahead')
        self.assertEqual(relative_words(-90), 'to your right')
        self.assertEqual(relative_words(170), 'behind you')
        self.assertEqual(relative_words(45), 'ahead to your left')


class FallbackVoteTests(PlaceFixture):
    def setUp(self):
        super().setUp()
        self.building()

    def test_confidence_needs_a_margin_and_a_lone_room_needs_more(self):
        self.assertEqual(Places.classify([('a', 'a', .83), ('b', 'b', .81)]), UNSURE)
        self.assertEqual(Places.classify([('a', 'a', .85), ('b', 'b', .79)]), RECOGNIZED)
        self.assertEqual(Places.classify([('a', 'a', .84)]), UNSURE)      # V2 2026-07-25: lone gallery.
        self.assertEqual(Places.classify([('a', 'a', .87)]), RECOGNIZED)
        self.assertEqual(Places.classify([('a', 'a', .60), ('b', 'b', .5)]), UNFAMILIAR)

    def test_a_room_belief_that_stops_matching_is_dropped(self):
        # Run 8cece04a: "dining room" from the hall stayed believed for three minutes in the living room.
        kitchen, hall = self.room('kitchen', KITCHEN), self.room('hallway', HALL)
        self.places.odometer = None   # The vote alone, no spots.
        self.see(near(KITCHEN, 1), 3)
        self.assertEqual(self.places.current['place_id'], kitchen)
        self.see(near(HALL, 2, .5), 5)   # Unsure readings that put the hallway first.
        self.assertIsNone(self.places.vote)

    def test_close_person_skips_frames(self):
        self.room('kitchen', KITCHEN)
        self.faces = [{'box': [.3, .2, .3, .4]}]
        self.see(near(KITCHEN, 1), 4); self.assertIsNone(self.places.last_query)


class SpotTests(PlaceFixture):
    def setUp(self):
        super().setUp()
        self.building()

    def photos(self, spot=None):
        rows = self.store.rows('SELECT spot_id,bearing FROM room_photos WHERE spot_id IS NOT NULL')
        return [r for r in rows if spot is None or r['spot_id'] == spot]

    def test_standing_still_makes_one_spot_and_photos_by_direction(self):
        self.look(5)
        spot = self.places.here
        self.assertIsNotNone(spot)
        self.assertEqual(len(self.photos()), 1)
        for _ in range(7):
            self.turn(45); self.look(2)
        self.assertEqual(self.places.here, spot)              # Turning in place never moves him off the spot.
        self.assertEqual(len(self.photos(spot)), 8)            # One per 45 degrees.
        self.assertEqual(len(self.store.rows('SELECT * FROM spots')), 1)

    def test_a_later_run_knows_the_spot_and_which_way_it_faces(self):
        self.look(5)
        for _ in range(7):
            self.turn(45); self.look(1)
        spot = self.places.here
        first_frame = self.places.anchors[spot]['offset']
        # Next run: the IMU's zero points somewhere else; the photos say where he is and which way he faces.
        self.yaw0, self.yaw, self.x, self.y = 70., 0., 0., 0.
        self.start_places()
        self.places.building_id, self.places.building_how = 'home', 'told'; self.places._reload()
        self.turn(12)          # Facing 82 degrees from the first run's zero: between two photos.
        self.look(3)
        self.assertEqual(self.places.here, spot)
        facing = self.places._facing(spot)
        expected = wrap(82. + first_frame - 0.)   # In the spot's own frame.
        self.assertLess(abs(wrap(facing - expected)), 6.)
        self.assertEqual(len(self.store.rows('SELECT * FROM spots')), 1)

    def test_a_trip_between_spots_is_a_passage_and_a_route(self):
        self.look(5)
        a = self.places.here
        self.drive([(2., 0.), (0., 1.5)], 'B')
        self.look(5)
        b = self.places.here
        self.assertNotEqual(a, b)
        passages = self.store.rows('SELECT * FROM passages')
        self.assertEqual(len(passages), 1)
        self.assertEqual((passages[0]['from_spot'], passages[0]['to_spot']), (a, b))
        self.assertAlmostEqual(passages[0]['length_m'], 3.5, delta=.2)
        # Named, B is somewhere to go; from A the route is that passage, and from B back it reversed.
        self.begin('This is the kitchen.')
        self.call('name_room', dict(name='kitchen', where='here', source_turn_id=self.state.current[0]))
        kitchen = self.store.rows("SELECT id FROM places WHERE name='kitchen'")[0]['id']
        self.assertEqual(self.store.rows('SELECT room_id,room_source FROM spots WHERE id=?', (b,))[0],
                         {'room_id': kitchen, 'room_source': 'told'})
        self.assertEqual(self.places.route_to(kitchen)['kind'], 'here')
        self.places.here = a
        route = self.places.route_to(kitchen)
        self.assertEqual(route['kind'], 'passages')
        headings = [s['heading'] for s in route['legs'][0]['segments']]
        self.assertEqual(len(headings), 2)
        self.assertLess(abs(headings[0]), 3.); self.assertLess(abs(headings[1] - 90.), 3.)
        known = {r['name']: r['way_known'] for r in self.places.context()['known_rooms']}
        self.assertTrue(known['kitchen'])

    def test_a_room_that_way_is_a_direction_from_this_spot(self):
        self.look(5)
        self.begin('The dining room is to your left.')
        result = self.call('name_room', dict(name='dining room', where='left', source_turn_id=self.state.current[0]))
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual(result.data['kept']['outcome'], 'that_way')
        context = self.places.context()
        self.assertEqual([(r['room'], r['where']) for r in context['this_way']], [('dining room', 'to your left')])
        self.turn(90)
        self.assertEqual(self.places.context()['this_way'][0]['where'], 'ahead')
        route = self.places.route_to(result.data['room_id'])
        self.assertEqual(route['kind'], 'bearing')
        self.assertLess(abs(self.places.relative(route['spot'], route['bearing'])), 3.)

    def test_a_trip_someone_asked_for_labels_where_it_ended_until_a_person_says(self):
        self.look(5)
        self.begin('This is the hallway.')
        self.call('name_room', dict(name='hallway', where='here', source_turn_id=self.state.current[0]))
        with self.store.transaction() as c:
            c.execute("INSERT INTO places(id,name,created_at,source_turn,import_ref,building_id) VALUES('lr','living room',0,NULL,NULL,'home')")
        self.places._reload()
        self.places.trip_started('lr', 'directions')
        self.drive([(1., 0.), (0., -1.)], 'B')
        self.places.trip_ended(True)
        self.look(5)
        context = self.places.context()
        self.assertEqual(context['just_arrived'], {'room': 'living room', 'room_id': 'lr', 'ask_if_this_is_it': True})
        self.assertEqual(context['room']['name'], 'living room')
        self.assertFalse(context['room']['sure'])            # A trip's guess, not a person's word.
        self.begin('No, this is the dining room.')
        rejected = self.call('correct_room', dict(action='not_this_room', room_id='lr', new_name=None,
                                                  source_turn_id=self.state.current[0]))
        self.assertTrue(rejected.data['belief_dropped'])
        self.assertIsNone(self.store.rows('SELECT room_id FROM spots WHERE id=?', (self.places.here,))[0]['room_id'])

    def test_carried_off_a_spot_is_noticed(self):
        self.look(5)
        spot = self.places.here
        self.spot_at = 'Z'          # Picked up and set down elsewhere: the wheels never turned.
        self.look(4)
        self.assertNotEqual(self.places.here, spot)
        self.assertFalse(self.store.rows('SELECT * FROM passages'))


class BuildingTests(PlaceFixture):
    def test_october_rooms_and_views_move_to_home(self):
        with TemporaryDirectory() as folder:
            path = Path(folder).resolve() / 'core.sqlite3'
            with sqlite3.connect(path) as db:
                db.executescript("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);")
            store = Memory(Path(folder).resolve() / 'other.sqlite3'); store.start()
            with store.transaction() as c:
                c.execute("INSERT INTO places(id,name,created_at) VALUES('k','kitchen',0)")
                c.execute("INSERT INTO place_embeddings VALUES('e','k','mobileclip_s2_v1',?,NULL,0,'refresh')",
                          (array('f', KITCHEN).tobytes(),))
                c.execute('DELETE FROM buildings')
                migrate(c)
            home = store.rows('SELECT * FROM buildings')
            self.assertEqual([(b['name'], b['adopt_network']) for b in home], [('home', 1)])
            self.assertEqual(store.rows('SELECT building_id FROM places')[0]['building_id'], home[0]['id'])
            self.assertEqual(store.rows('SELECT room_id,source,spot_id FROM room_photos'),
                             [{'room_id': 'k', 'source': 'legacy', 'spot_id': None}])
            self.assertEqual(len(store.rows('SELECT * FROM place_embeddings')), 1)   # Left where it was.
            store.close()

    def test_home_adopts_its_first_router_then_knows_it(self):
        with self.store.transaction() as c:
            c.execute("INSERT INTO buildings VALUES('home','home',0,NULL,0,1)")
        self.start_places(network=lambda: 'router-a')
        self.assertEqual((self.places.building_id, self.places.building_how), ('home', 'network'))
        self.start_places(network=lambda: 'router-a')
        self.assertEqual(self.places.building_how, 'network')

    def test_somewhere_never_seen_on_an_unknown_router_is_a_new_place_that_gets_a_name(self):
        with self.store.transaction() as c:
            c.execute("INSERT INTO buildings VALUES('home','home',0,NULL,0,0)")
            c.execute("INSERT INTO building_networks VALUES('home','router-a',0,0)")
        self.start_places(network=lambda: 'router-a')
        self.look(5)                                    # A spot and a photo at home.
        self.spot_at = 'dads-den'
        self.start_places(network=lambda: 'router-dad')
        self.assertIsNone(self.places.building_id if self.places.building_how != 'only' else None)
        self.look(9)
        self.assertEqual(self.places.context()['status'], NEW_PLACE)
        self.assertTrue(self.places.context()['place']['never_seen_before'])
        names = {d['name'] for d in grounded_tools({'source_turn_id': 't', 'place': self.places.context()})}
        self.assertIn('name_place', names)
        self.begin("You're at my dad's house.")
        result = self.call('name_place', dict(name="Dad's house", action='this_is', source_turn_id=self.state.current[0]))
        self.assertEqual(result.outcome, 'ok', result)
        context = self.places.context()
        self.assertEqual(context['place']['name'], "Dad's house")
        self.assertFalse(context['place']['never_seen_before'])
        self.assertEqual(self.places.map.buildings_on('router-dad'), [self.places.building_id])
        # Rooms named here are this place's own.
        self.begin('This is the den.')
        self.call('name_room', dict(name='den', where='here', source_turn_id=self.state.current[0]))
        self.assertEqual(self.store.rows("SELECT building_id FROM places WHERE name='den'")[0]['building_id'],
                         self.places.building_id)
        # Back home the router says so, and the den is not among home's rooms.
        self.spot_at = 'A'
        self.start_places(network=lambda: 'router-a')
        self.assertEqual(self.places.building_id, 'home')
        self.assertNotIn('den', [r['name'] for r in self.places.context()['known_rooms']])


class NamingTests(PlaceFixture):
    def setUp(self):
        super().setUp()
        self.building()

    def test_not_this_room_rename_and_forget(self):
        kitchen, hall = self.room('kitchen', KITCHEN), self.room('hallway', HALL)
        self.places.odometer = None
        self.see(near(HALL, 2), 3)
        self.begin("This isn't the hallway.")
        result = self.call('correct_room', dict(action='not_this_room', room_id=hall, new_name=None,
                                                source_turn_id=self.state.current[0]))
        self.assertTrue(result.data['belief_dropped']); self.assertIsNone(self.places.current)
        self.begin('Call the hallway the entry.')
        self.call('correct_room', dict(action='rename', room_id=hall, new_name='entry', source_turn_id=self.state.current[0]))
        self.assertIn('entry', [r['name'] for r in self.places.context()['known_rooms']])
        self.begin('Forget the kitchen.')
        self.call('correct_room', dict(action='forget', room_id=kitchen, new_name=None, source_turn_id=self.state.current[0]))
        self.assertFalse(self.store.rows('SELECT * FROM room_photos WHERE room_id=?', (kitchen,)))
        self.assertNotIn('kitchen', [r['name'] for r in self.places.context()['known_rooms']])

    def test_untrusted_speech_names_nothing_and_room_tools_are_for_human_turns(self):
        self.room('kitchen', KITCHEN)
        self.begin('this is the garage', trusted=False)
        self.assertEqual(self.call('name_room', dict(name='garage', where='here', source_turn_id=self.state.current[0])).reason,
                         'UntrustedSource')
        place = self.places.context()
        names = lambda context: {d['name'] for d in grounded_tools({'source_turn_id': 't', **context})}
        self.assertTrue({'name_room', 'correct_room', 'name_place'} <= names({'place': place}))
        self.assertFalse({'name_room', 'correct_room', 'name_place'} & names({'place': place, 'trigger': 'conversation_lull'}))
        self.assertNotIn('correct_room', names({'place': {**place, 'known_rooms': []}}))
        self.assertIn('explore', GOAL_KINDS)

    def test_a_name_while_rolling_lands_where_he_stops(self):
        self.look(5)
        self.moving[0] = True
        for _ in range(4):
            self.x += .05; self.clock.now += .05; self.telemetry()   # Rolling as it is said.
        self.begin('This is the hall.')
        result = self.call('name_room', dict(name='hall', where='here', source_turn_id=self.state.current[0]))
        self.assertEqual(result.data['kept']['outcome'], 'when_he_stops')
        self.assertIn('not kept yet', result.data['meaning'])        # Nothing is claimed kept before it is.
        self.drive([(1.5, 0.)], 'B')
        self.look(5)
        self.assertEqual(self.places.context()['room']['name'], 'hall')


@unittest.skipUnless(native_available(), 'numpy unavailable (blocked under the contract runner)')
class ImportAndPreprocessTests(unittest.TestCase):
    def test_v2_snapshot_is_read_only_validated_and_compared(self):
        from tools.import_v2_places import look_alikes, read, snapshot
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'places.db'
            with sqlite3.connect(path) as db:
                db.executescript('CREATE TABLE places(place_id INTEGER PRIMARY KEY,name TEXT,created_at TEXT,notes TEXT);'
                                 'CREATE TABLE place_embeddings(embedding_id INTEGER PRIMARY KEY,place_id INTEGER,vector BLOB,'
                                 'dim INTEGER,heading_deg REAL,captured_at TEXT,model_tag TEXT);')
                db.execute("INSERT INTO places VALUES(1,'workshop','2026-07-21T00:00:00+00:00',NULL)")
                db.execute("INSERT INTO places VALUES(2,'den','2026-07-21T00:00:00+00:00',NULL)")
                for pid, v in ((1, KITCHEN), (2, HALL), (2, unit(9))):
                    db.execute('INSERT INTO place_embeddings VALUES(NULL,?,?,512,NULL,?,?)',
                               (pid, array('f', v).tobytes(), '2026-07-21T00:00:00+00:00', 'mobileclip_s2_v1'))
                db.execute('INSERT INTO place_embeddings VALUES(NULL,1,?,4,NULL,?,?)',
                           (array('f', [1.] * 4).tobytes(), '2026-07-21T00:00:00+00:00', 'other_model'))
            copy, digest = snapshot(path)
            rooms = read(copy); copy.close()
            self.assertEqual([(r['name'], len(r['views'])) for r in rooms], [('workshop', 1), ('den', 2)])
            self.assertEqual(len(digest), 64)
            self.assertEqual(look_alikes(rooms)[0][:2], ('workshop', 'den'))

    def test_preprocess_matches_open_clip_shape_and_range_for_any_orientation(self):
        import numpy as np
        from r3x.adapters.places import preprocess
        for width, height in ((1920, 1080), (480, 640), (256, 256)):
            pixels = np.random.default_rng(width).integers(0, 255, (height, width, 3), dtype=np.uint8)
            x = preprocess(Frame('f', 'c', 's', 0, width, height, pixels.tobytes()))
            self.assertEqual(x.shape, (1, 3, 256, 256)); self.assertEqual(x.dtype, np.float32)
            self.assertTrue(0 <= x.min() and x.max() <= 1)
        # A flat colour stays that colour, channel order RGB from BGR input.
        bgr = np.zeros((300, 400, 3), np.uint8); bgr[:, :, 2] = 200
        x = preprocess(Frame('f', 'c', 's', 0, 400, 300, bgr.tobytes()))
        self.assertAlmostEqual(float(x[0, 0].mean()), 200 / 255, places=5); self.assertEqual(float(x[0, 2].max()), 0.)

    @unittest.skipUnless(cv2_available(), 'OpenCV blocked under the contract runner')
    def test_tiny_copy(self):
        import numpy as np
        from r3x.adapters.places import jpeg, thumbnail
        pixels = np.random.default_rng(1).integers(0, 255, (1080, 1920, 3), dtype=np.uint8)
        frame = Frame('f', 'c', 's', 0, 1920, 1080, pixels.tobytes())
        self.assertEqual(len(thumbnail(frame)), THUMB_W * THUMB_H)
        self.assertTrue(jpeg(frame).startswith(b'\xff\xd8'))


class RouterTests(unittest.TestCase):
    def test_router_reading(self):
        from r3x.adapters.places import router_identity
        outputs = {'/sbin/route': '   route to: default\n    gateway: 192.168.1.1\n',
                   '/usr/sbin/arp': '? (192.168.1.1) at a4:5e:60:1:2:3 on en1 ifscope [ethernet]\n'}
        one = router_identity(run=lambda args: outputs[args[0]])
        self.assertEqual(len(one), 32)
        self.assertIsNone(router_identity(run=lambda args: ''))


class ReviewRepairTests(PlaceFixture):
    """November 22 review: the cases that could stop room tracking or label the wrong stop."""
    def setUp(self):
        super().setUp()
        self.building()

    def test_a_spot_made_this_run_merges_into_the_old_one_with_a_trip_under_way(self):
        self.look(5)                                   # The old spot, photographed facing 0.
        old = self.places.here
        self.yaw0, self.yaw, self.x, self.y = 180., 0., 0., 0.
        self.start_places()
        self.places.building_id, self.places.building_how = 'home', 'told'; self.places._reload()
        self.look(5)                                   # Facing the other way: no photo there, a new spot.
        made = self.places.here
        self.assertNotEqual(made, old)
        self.drive([(1., 0.), (-1., 0.)], 'A')         # Out and back, the trip starting on the new spot.
        self.turn(180.)                                # Now facing the way the old spot was photographed.
        self.look(6)
        self.assertEqual(self.places.here, old)
        self.assertEqual(len(self.store.rows('SELECT * FROM spots')), 1)
        self.assertIsNone(self.places.failure)

    def test_a_trip_goal_with_no_trip_labels_nothing_later(self):
        self.look(5)
        with self.store.transaction() as c:
            c.execute("INSERT INTO places(id,name,created_at,source_turn,import_ref,building_id) VALUES('k','kitchen',0,NULL,NULL,'home')")
        self.places._reload()
        self.places.trip_started('k', 'map')
        self.places.trip_ended(True)                   # "Already here": no trip at all.
        self.assertIsNone(self.places.goal)
        self.drive([(1.5, 0.)], 'B')                   # A later drive of his own...
        self.look(5)
        self.assertNotIn('just_arrived', self.places.context())   # ...is not labelled the kitchen.
        self.assertIsNone(self.store.rows('SELECT room_id FROM spots WHERE id=?', (self.places.here,))[0]['room_id'])

    def test_a_slow_crawl_off_a_spot_is_still_a_trip(self):
        self.look(5)
        a = self.places.here
        self.moving[0] = True
        for _ in range(16):                            # 0.09 m between readings, as through a doorway.
            for _ in range(3):
                self.x += .03; self.clock.now += .5; self.telemetry()
            self.see(unit(hash((self.clock.now, 'crawl')) % 9999))
        self.moving[0] = False
        self.spot_at = 'B'
        self.clock.now += 3.; self.telemetry()
        self.look(6)
        self.assertNotEqual(self.places.here, a)
        self.assertEqual(len(self.store.rows('SELECT * FROM passages')), 1)

    def test_only_a_named_spot_counts_as_in_the_room_for_a_trip(self):
        kitchen = self.room('kitchen', KITCHEN)
        self.places.odometer = None
        self.see(near(KITCHEN, 1), 3)
        self.assertEqual(self.places.current['place_id'], kitchen)   # The look-alike vote believes it...
        self.assertFalse(self.places.in_room(kitchen))                # ...but that is no arrival.

    def describing(self, folder, describer, files=1):
        self.places.photos_dir, self.places.describer = Path(folder), describer
        (Path(folder) / 'home').mkdir(exist_ok=True)
        for i in range(files):
            name = f'home/there{i}.jpg'
            if not self.store.rows('SELECT 1 FROM room_photos WHERE file=?', (name,)):
                self.places.map.add_photo('home', None, None, self.places.namespace, unit(900 + i), file=name)
            (Path(folder) / name).write_bytes(b'\xff\xd8')

    def undescribed(self):
        return self.store.rows('SELECT COUNT(*) n FROM room_photos WHERE description IS NULL')[0]['n']

    def test_a_failed_description_waits_for_another_run(self):
        # Second review: any failure blanked the photo for good (ten minutes offline cost thirty photos).
        calls = []
        def failing(image, cancel):
            calls.append(image); raise RuntimeError('no network')
        with TemporaryDirectory() as folder:
            self.describing(folder, failing)
            long_ago = 1.7e9 + self.clock.now - 600
            self.places.map.add_photo('home', None, None, self.places.namespace, KITCHEN, file='home/missing.jpg', at=long_ago - 1)
            for _ in range(4):
                self.clock.now += 30; self.places._describe_one()
            self.assertEqual(len(calls), 1)                     # The missing file was blanked; the other tried once.
            self.assertEqual(self.undescribed(), 1)             # Still waiting for its description.
            self.start_places()                                 # Another run, the network back.
            self.places.building_id, self.places.building_how = 'home', 'told'; self.places._reload()
            self.describing(folder, lambda image, cancel: {'text': 'a workbench under a window'})
            self.clock.now += 30; self.places._describe_one()
            self.assertEqual(self.undescribed(), 0)
            self.assertEqual(self.store.rows("SELECT description d FROM room_photos WHERE file='home/there0.jpg'")[0]['d'],
                             'a workbench under a window')

    def test_descriptions_rest_after_failures_in_a_row(self):
        calls = []
        def failing(image, cancel):
            calls.append(image); raise RuntimeError('no credit')
        with TemporaryDirectory() as folder:
            self.describing(folder, failing, files=6)
            for _ in range(8):
                self.clock.now += 30; self.places._describe_one()
            self.assertEqual(len(calls), 3)
            self.assertEqual(self.undescribed(), 6)


class BuildingRepairTests(PlaceFixture):
    def test_a_router_confirmed_home_keeps_its_router_when_someone_names_another_place(self):
        with self.store.transaction() as c:
            c.execute("INSERT INTO buildings VALUES('home','home',0,NULL,0,0)")
            c.execute("INSERT INTO building_networks VALUES('home','router-a',0,0)")
        self.start_places(network=lambda: 'router-a')
        self.begin("This is my office.")
        self.call('name_place', dict(name='office', action='this_is', source_turn_id=self.state.current[0]))
        self.assertEqual(self.places.context()['place']['name'], 'office')   # Trusted for this run...
        self.assertEqual(self.places.map.buildings_on('router-a'), ['home'])  # ...the router stays home's.

    def test_a_new_place_that_turns_out_known_joins_its_map(self):
        with self.store.transaction() as c:
            c.execute("INSERT INTO buildings VALUES('home','home',0,NULL,0,0)")
            c.execute("INSERT INTO building_networks VALUES('home','router-a',0,0)")
        self.start_places(network=lambda: 'router-a')
        self.look(5)
        for _ in range(3):
            self.turn(45); self.look(1)
        self.spot_at = 'elsewhere'
        self.start_places(network=lambda: 'router-new')    # A new router at home...
        self.look(9)
        provisional = self.places.building_id
        self.assertEqual(self.places.context()['status'], NEW_PLACE)
        self.spot_at = 'A'; self.yaw = 0.
        self.look(4)                                         # ...and then a view of a room he knows there.
        self.assertEqual(self.places.building_id, 'home')
        self.assertFalse(self.store.rows('SELECT * FROM buildings WHERE id=?', (provisional,)))
        self.assertIn('home', self.places.map.buildings_on('router-new'))


class RouteArrivalTests(PlaceFixture):
    def setUp(self):
        super().setUp()
        self.building()

    def test_after_a_route_the_wheels_place_him_on_the_spot_it_leads_to(self):
        self.look(5)
        a = self.places.here
        self.drive([(2., 0.)], 'B')
        self.look(5)
        b = self.places.here
        self.drive([(-2., 0.)], 'A')
        self.look(3)
        self.assertEqual(self.places.here, a)
        # A map trip under way: the plan still runs (wheels just stopped), the drive owner expects B.
        self.moving[0] = True
        for _ in range(40):
            self.x += .05; self.clock.now += .05; self.telemetry()
        self.clock.now += 1.; self.telemetry()
        offset = self.places.offset(a)
        self.places.expect(b, offset)
        frame = self.frame(unit(4242))                 # A view his photos do not settle either way.
        self.places.observe(frame, deliberate=True)
        self.assertEqual(self.places.where()['spot'], b)


def at_similarity(base, similarity, seed):
    """A view exactly this like `base` (cosine)."""
    r = unit(seed)
    d = sum(x * y for x, y in zip(r, base))
    r = [x - d * y for x, y in zip(r, base)]
    n = sum(x * x for x in r) ** .5
    return [similarity * b + (1 - similarity * similarity) ** .5 * x / n for b, x in zip(base, r)]


class SecondReviewTests(PlaceFixture):
    """November 22, second review (a fresh read of the room map): what it got wrong with nobody watching."""
    def setUp(self):
        super().setUp()
        self.lean = None
        self.building()

    def telemetry(self):
        imu = {'ok': True, 'yaw': wrap(self.yaw)}
        if getattr(self, 'lean', None) is not None:
            imu.update(pitch=self.lean, roll=0.)
        self.odometer.update({'imu': imu, 'odom': {'x': self.x, 'y': self.y, 'theta': math.radians(self.yaw)}})

    def trip_to_the_living_room(self):
        self.look(5)
        self.begin('This is the hallway.')
        self.call('name_room', dict(name='hallway', where='here', source_turn_id=self.state.current[0]))
        hallway = self.places.here
        with self.store.transaction() as c:
            c.execute("INSERT INTO places(id,name,created_at,source_turn,import_ref,building_id) VALUES('lr','living room',0,NULL,NULL,'home')")
        self.places._reload()
        self.places.trip_started('lr', 'directions')
        self.drive([(1., 0.), (0., -1.)], 'B')
        self.places.trip_ended(True)
        return hallway

    def test_two_spots_he_stood_on_this_run_stay_two(self):
        # Two metres apart, the same far wall from both: they became one spot, losing a room name and a passage.
        self.world.view('A', 0); self.world.view('B', 0)
        self.world.bases['B'][2] = self.world.bases['A'][2]
        self.look(6)
        self.turn(90); self.look(3)
        a = self.places.here
        self.drive([(2., 0.)], 'B')
        self.look(8)
        b = self.places.here
        self.assertNotEqual(a, b)
        self.turn(90); self.look(3)                     # The far wall he photographed from A.
        self.assertEqual(len(self.store.rows('SELECT * FROM spots')), 2)
        self.assertEqual(self.places.here, b)
        self.assertTrue(self.store.rows('SELECT * FROM passages'))

    def test_a_view_unlike_the_spot_a_route_led_to_is_not_an_arrival(self):
        self.look(5)
        a = self.places.here
        self.drive([(2., 0.)], 'B')
        self.look(5)
        self.moving[0] = True                           # A route back toward A that went wrong: he is somewhere else.
        for _ in range(40):
            self.x += .05; self.clock.now += .05; self.telemetry()
        self.clock.now += 1.; self.telemetry()
        self.spot_at = 'Z'
        self.places.expect(a, 0.)
        world = self.yaw0 + self.yaw
        self.places.observe(self.frame(self.world.view('Z', world), self.world.thumb('Z', world)), deliberate=True)
        self.assertIsNone(self.places.where()['spot'])           # Not there: the drive owner says so and he asks.
        self.assertIsNotNone(self.places.offset(a))              # Its frame stays for the rest of a longer route.
        self.spot_at = 'A'                                       # The same look where the view does fit.
        self.places.expect(a, 0.)
        self.clock.now += 1.5; self.telemetry()
        self.places.observe(self.frame(self.world.view('A', world), self.world.thumb('A', world)), deliberate=True)
        self.assertEqual(self.places.where()['spot'], a)

    def test_an_unconfirmed_route_spot_is_his_only_when_the_person_names_its_room(self):
        self.look(5)
        self.begin('This is the kitchen.')
        kitchen = self.call('name_room', dict(name='kitchen', where='here', source_turn_id=self.state.current[0])).data['room_id']
        a = self.places.here
        self.drive([(3., 0.)], 'Z')
        self.places.expect(a, 0.)                               # A route "arrived"; the look did not confirm it.
        self.begin('No, this is the hallway.')
        self.call('name_room', dict(name='hallway', where='here', source_turn_id=self.state.current[0]))
        self.assertNotEqual(self.places.here, a)                # A new spot is the hallway...
        self.assertEqual(self.store.rows('SELECT room_id FROM spots WHERE id=?', (a,))[0]['room_id'], kitchen)   # ...A stays the kitchen.

    def test_no_said_before_the_stop_is_a_spot_ends_the_trips_goal(self):
        self.trip_to_the_living_room()
        self.begin('No.')                                       # Answered at once, before the stop became a spot.
        rejected = self.call('correct_room', dict(action='not_this_room', room_id='lr', new_name=None,
                                                  source_turn_id=self.state.current[0]))
        self.assertTrue(rejected.data['belief_dropped'])
        self.look(5)
        self.assertIsNone(self.store.rows('SELECT room_id FROM spots WHERE id=?', (self.places.here,))[0]['room_id'])
        self.assertNotIn('just_arrived', self.places.context())
        self.assertFalse(self.store.rows('SELECT * FROM room_bearings'))

    def test_a_trip_teaches_the_way_only_once_someone_says_this_is_it(self):
        hallway = self.trip_to_the_living_room()
        self.look(5)
        self.assertFalse(self.store.rows('SELECT * FROM room_bearings'))   # Stopped, asked, not yet answered.
        self.begin('Yes, this is the living room.')
        self.call('name_room', dict(name='living room', where='here', source_turn_id=self.state.current[0]))
        rows = self.store.rows('SELECT spot_id,room_id,source FROM room_bearings')
        self.assertEqual([(r['spot_id'], r['room_id'], r['source']) for r in rows], [(hallway, 'lr', 'trip')])

    def test_a_long_trip_keeps_its_goal_until_after_it_ends(self):
        self.look(5)
        with self.store.transaction() as c:
            c.execute("INSERT INTO places(id,name,created_at,source_turn,import_ref,building_id) VALUES('lr','living room',0,NULL,NULL,'home')")
        self.places._reload()
        self.places.trip_started('lr', 'sight')
        self.clock.now += 200.; self.places.tick()              # Searching and detours: over two minutes.
        self.drive([(1.5, 0.)], 'B')
        self.places.trip_ended(True)
        self.look(5)
        self.assertEqual(self.places.context()['just_arrived']['room'], 'living room')

    def test_someone_in_the_way_is_not_being_carried_but_a_lift_is(self):
        self.lean = 0.
        self.look(5)
        spot = self.places.here
        self.spot_at = 'Z'                              # The view no longer fits: a person stands close in front.
        self.look(6)
        self.assertEqual(self.places.here, spot)
        self.assertEqual(len(self.store.rows('SELECT * FROM spots')), 1)
        self.lean = 20.; self.clock.now += .1; self.telemetry()   # Picked up...
        self.lean = 0.
        self.look(4)                                    # ...and set down where nothing fits.
        self.assertNotEqual(self.places.here, spot)

    def test_a_building_needs_its_matching_views_close_together(self):
        with self.store.transaction() as c:
            c.execute("INSERT INTO building_networks VALUES('home','router-a',0,0)")
        self.look(5)
        photo = self.world.view('A', self.yaw0 + self.yaw)
        self.start_places(network=lambda: 'router-other')    # Somewhere on a router no place knows.
        self.assertEqual(self.places.building_how, 'only')
        seed = 0
        for _ in range(3):                                   # Three look-alike views, minutes apart.
            self.see(at_similarity(photo, .9, 7000 + seed)); seed += 1
            for _ in range(6):
                self.see(at_similarity(photo, .78, 7000 + seed)); seed += 1
        self.assertEqual(self.places.building_how, 'only')   # Not home on that.
        self.assertFalse(self.places.map.buildings_on('router-other'))
        for _ in range(3):
            self.see(at_similarity(photo, .9, 7000 + seed)); seed += 1
        self.assertEqual(self.places.building_how, 'views')  # Three together are.

    def test_joined_spots_keep_the_name_a_person_gave(self):
        with self.store.transaction() as c:
            for key, name in (('k', 'kitchen'), ('b', 'bedroom')):
                c.execute('INSERT INTO places(id,name,created_at,source_turn,import_ref,building_id) VALUES(?,?,0,NULL,NULL,?)',
                          (key, name, 'home'))
        made, old = self.places.map.add_spot('home'), self.places.map.add_spot('home')
        self.places.map.label_spot(old, 'b', 'trip')
        self.places.map.label_spot(made, 'k', 'told', 'turn-1')
        self.places.map.merge_spot(made, old, 0.)
        row = self.store.rows('SELECT room_id,room_source,room_turn FROM spots WHERE id=?', (old,))[0]
        self.assertEqual((row['room_id'], row['room_source'], row['room_turn']), ('k', 'told', 'turn-1'))


class DriftingGyroTests(PlaceFixture):
    """Run e54062b9 (November 22): the gyroscope's zero was 19.7 degrees a second off, every reading looked like a turn,
    he never counted as standing still, and the room map filed nothing in seven minutes."""
    DRIFT = 19.7

    def setUp(self):
        self.reported = None
        super().setUp()
        self.building()

    def telemetry(self):
        # As the controller reports it: wheel speeds and drive levels, and a heading that drifts by itself.
        turning = self.reported is not None and abs(self.yaw - self.reported[0]) > 1e-9
        rolling = self.reported is not None and (self.x, self.y) != self.reported[1]
        self.reported = (self.yaw, (self.x, self.y))
        drifting = wrap(self.yaw + self.DRIFT * (self.clock.now - 1000.))
        moving = turning or rolling or self.moving[0]
        self.odometer.update({'imu': {'ok': True, 'yaw': drifting, 'pitch': -3., 'roll': -1.},
                              'odom': {'x': self.x, 'y': self.y, 'theta': math.radians(self.yaw),
                                       'lin': .2 if rolling else 0., 'ang': .5 if turning else 0.},
                              'wheels': {'vl': .1 if moving else 0., 'vr': .1 if moving else 0.,
                                         'dl': 300 if moving else 0, 'dr': 300 if moving else 0}})

    def test_standing_still_with_a_drifting_gyroscope_still_makes_a_spot_and_files_photos(self):
        self.look(6)
        self.assertIsNotNone(self.places.here)
        self.assertTrue(self.store.rows('SELECT * FROM room_photos WHERE spot_id IS NOT NULL'))
        self.assertAlmostEqual(self.odometer.drift_at_rest(), self.DRIFT, delta=.5)
        self.assertAlmostEqual(self.odometer.pose()[2], 0., delta=.5)        # His heading did not run away.

    def test_turns_the_wheels_make_still_count(self):
        self.look(5)
        a = self.places.here
        self.turn(90); self.look(3)
        self.assertAlmostEqual(wrap(self.odometer.pose()[2] - 90.), 0., delta=3.)
        self.assertEqual(self.places.here, a)                                  # Turning in place: the same spot.
        self.assertEqual(len({round(r['bearing']) for r in self.store.rows('SELECT bearing FROM room_photos WHERE spot_id=?', (a,))}), 2)

    def test_without_wheel_readings_the_heading_is_taken_as_it_comes(self):
        odometer = Odometer(self.clock)
        for i in range(30):
            self.clock.now += .1
            odometer.update({'imu': {'ok': True, 'yaw': i * 2.}, 'odom': {'x': 0., 'y': 0., 'theta': 0.}})
        self.assertAlmostEqual(odometer.pose()[2], 58., delta=.1)
        self.assertIsNone(odometer.drift_at_rest())
