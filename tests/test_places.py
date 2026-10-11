from array import array
from pathlib import Path
import random
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import sqlite3
import unittest

from r3x.agency import GOAL_KINDS
from r3x.places import Places, RECOGNIZED, UNFAMILIAR, UNSURE
from r3x.semantics import grounded_tools
from r3x.vision import Frame
from tests.test_memory_core import MemoryFixture


def unit(seed, mix=None, weight=.0):
    """Synthetic 512-d room views, built without numpy (the contract runner blocks native libraries)."""
    rng = random.Random(seed)
    v = [rng.gauss(0, 1) for _ in range(512)]
    norm = sum(x * x for x in v) ** .5
    v = [x / norm for x in v]
    if mix is not None: v = [(1 - weight) * a + weight * b for a, b in zip(v, mix)]
    norm = sum(x * x for x in v) ** .5
    return [x / norm for x in v]


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


class Encoder:
    capability = SimpleNamespace(provider='fake', model='fake', dependencies=())
    def start(self): pass
    def close(self): pass
    def embed(self, frame): return self.vectors[frame.frame_id]


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now


class PlaceFixture(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.clock, self.moving = Clock(), [False]
        self.encoder = Encoder(); self.encoder.vectors = {}
        camera = SimpleNamespace(session_id='s', latest=lambda now: self.faces, hub=None)
        self.faces = []
        self.places = Places(camera, self.encoder, self.store, motion=lambda: self.moving[0],
                             clock=self.clock, wall_clock=lambda: 1.7e9 + self.clock.now)
        self.state.places = self.places
        self.places.load()

    def room(self, name, vector, views=6):
        place_id = name.replace(' ', '-')
        with self.store.transaction() as c:
            c.execute('INSERT INTO places VALUES(?,?,?,NULL,NULL)', (place_id, name, 0.))
        for i in range(views):
            self.places._store_vector(place_id, near(vector, hash((name, i)) % 997, .95), None, 'taught')
        self.places.load()
        return place_id

    def see(self, vector, times=1):
        for _ in range(times):
            self.clock.now += 1.5
            key = str(self.clock.now)
            self.encoder.vectors[key] = vector
            self.places.observe(Frame(key, 'c', 's', self.clock.now, 4, 4, bytes(48)))
            self.places.tick()


class RecognitionTests(PlaceFixture):
    def test_confidence_needs_a_margin_and_a_lone_room_needs_more(self):
        self.assertEqual(Places.classify([('a', 'a', .83), ('b', 'b', .81)]), UNSURE)
        self.assertEqual(Places.classify([('a', 'a', .85), ('b', 'b', .79)]), RECOGNIZED)
        self.assertEqual(Places.classify([('a', 'a', .84)]), UNSURE)      # V2 2026-07-25: lone gallery.
        self.assertEqual(Places.classify([('a', 'a', .87)]), RECOGNIZED)
        self.assertEqual(Places.classify([('a', 'a', .60), ('b', 'b', .5)]), UNFAMILIAR)

    def test_belief_forms_by_majority_and_changes_rooms_only_after_moving(self):
        kitchen, hall = self.room('kitchen', KITCHEN), self.room('hallway', HALL)
        self.see(near(KITCHEN, 1), 2); self.assertIsNone(self.places.current)
        self.see(near(KITCHEN, 2)); self.assertEqual(self.places.current['place_id'], kitchen)
        self.see(near(HALL, 3), 5)                       # Camera sees the hallway, wheels say still.
        self.assertEqual(self.places.current['place_id'], kitchen)
        self.moving[0] = True; self.see(near(HALL, 4), 3)
        self.assertEqual(self.places.current['place_id'], hall)
        # The move is remembered as a connection between the two rooms.
        self.assertEqual(self.places.context()['connects_to'], ['kitchen'])

    def test_a_drive_between_samples_still_counts_as_moving(self):
        kitchen, hall = self.room('kitchen', KITCHEN), self.room('hallway', HALL)
        self.see(near(KITCHEN, 1), 3)
        self.places.note_motion()                        # The drive owner's callback; wheels now still.
        self.see(near(HALL, 2), 3)
        self.assertEqual(self.places.current['place_id'], hall)

    def test_carried_robot_flips_after_sustained_evidence_without_a_link(self):
        kitchen, hall = self.room('kitchen', KITCHEN), self.room('hallway', HALL)
        self.see(near(KITCHEN, 1), 3)
        self.see(near(HALL, 2), 12)
        self.assertEqual(self.places.current['place_id'], hall)
        self.assertFalse(self.store.rows('SELECT * FROM place_links'))

    def test_unfamiliar_after_moving_and_lost_belief_is_dropped(self):
        self.room('kitchen', KITCHEN); self.room('hallway', HALL)
        self.see(near(KITCHEN, 1), 3)
        self.moving[0] = True
        self.see(OFFICE, 16)
        context = self.places.context()
        self.assertIsNone(self.places.current)
        self.assertEqual(context['status'], 'unfamiliar')
        self.assertIsNotNone(context['unfamiliar_seconds'])

    def test_close_person_skips_frames_and_lone_room_never_grows_its_gallery(self):
        self.room('kitchen', KITCHEN)
        self.faces = [{'box': [.3, .2, .3, .4]}]
        self.see(near(KITCHEN, 1), 4); self.assertIsNone(self.places.last_query)
        self.faces = []
        before = len(self.store.rows('SELECT * FROM place_embeddings'))
        self.see(near(KITCHEN, 2, .80), 4)
        self.assertEqual(len(self.store.rows('SELECT * FROM place_embeddings')), before)


class NamingTests(PlaceFixture):
    def name(self, text, room):
        self.begin(text)
        return self.call('name_room', dict(name=room, source_turn_id=self.state.current[0]))

    def test_named_room_collects_views_then_is_recognized_and_linked(self):
        kitchen = self.room('kitchen', KITCHEN); self.room('hallway', HALL)
        self.see(near(KITCHEN, 1), 3); self.moving[0] = True; self.see(OFFICE, 16)
        result = self.name("This is Bret's office.", "Bret's office")
        self.assertEqual(result.outcome, 'ok')
        self.assertEqual(self.places.context()['status'], 'learning')
        for i in range(8): self.clock.now += 3; self.see(near(OFFICE, 10 + i))
        context = self.places.context()
        self.assertEqual(context['last_learning']['outcome'], 'learned')
        self.assertEqual(context['room']['name'], "Bret's office")
        self.assertEqual(context['connects_to'], ['kitchen'])
        self.assertEqual(len(self.store.rows('SELECT * FROM place_embeddings WHERE place_id=?', (result.data['room_id'],))), 8)

    def test_too_few_views_forgets_the_new_name_and_reports_failure(self):
        result = self.name('This is the den.', 'the den')
        self.see(near(OFFICE, 1)); self.clock.now += 61; self.places.tick()
        self.assertEqual(self.places.context()['last_learning']['outcome'], 'failed')
        self.assertFalse(self.store.rows('SELECT * FROM places WHERE id=?', (result.data['room_id'],)))

    def test_known_name_adds_views_and_untrusted_speech_names_nothing(self):
        kitchen = self.room('kitchen', KITCHEN, views=3)
        self.assertEqual(self.name('Yes, this is the kitchen.', 'Kitchen').data['room_id'], kitchen)
        self.begin('this is the garage', trusted=False)
        self.assertEqual(self.call('name_room', dict(name='garage', source_turn_id=self.state.current[0])).reason, 'UntrustedSource')

    def test_not_this_room_rename_and_forget(self):
        kitchen, hall = self.room('kitchen', KITCHEN), self.room('hallway', HALL)
        self.see(near(KITCHEN, 1), 3); self.moving[0] = True; self.see(near(HALL, 2), 3)
        self.begin("This isn't the hallway.")
        result = self.call('correct_room', dict(action='not_this_room', room_id=hall, new_name=None,
                                                source_turn_id=self.state.current[0]))
        self.assertTrue(result.data['belief_dropped']); self.assertIsNone(self.places.current)
        self.begin('Call the hallway the entry.')
        self.call('correct_room', dict(action='rename', room_id=hall, new_name='entry', source_turn_id=self.state.current[0]))
        self.assertIn('entry', [r['name'] for r in self.places.context()['known_rooms']])
        self.begin('Forget the kitchen.')
        self.call('correct_room', dict(action='forget', room_id=kitchen, new_name=None, source_turn_id=self.state.current[0]))
        self.assertFalse(self.store.rows('SELECT * FROM place_embeddings WHERE place_id=?', (kitchen,)))
        self.assertFalse(self.store.rows('SELECT * FROM place_links'))
        self.assertNotIn('kitchen', [r['name'] for r in self.places.context()['known_rooms']])

    def test_room_tools_are_for_human_turns_only(self):
        self.room('kitchen', KITCHEN)
        place = self.places.context()
        names = lambda context: {d['name'] for d in grounded_tools({'source_turn_id': 't', **context})}
        self.assertTrue({'name_room', 'correct_room'} <= names({'place': place}))
        self.assertFalse({'name_room', 'correct_room'} & names({'place': place, 'trigger': 'conversation_lull'}))
        self.assertNotIn('correct_room', names({'place': {**place, 'known_rooms': []}}))
        self.assertIn('explore', GOAL_KINDS)


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
