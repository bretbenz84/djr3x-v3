"""Where Rex is: which building, which spot he stands on, which way he faces, which room, and which way the
other rooms are.

The one owner of room evidence (photos, spots, passages, the room belief), as the speaker tracker owns voice
evidence. It never speaks or interprets language: the conversation model names rooms and places through the
state owner's `name_room` and `name_place` tools, which call `named()` and `place_named()` here. The drive
owner asks it where he is and how to get to a room (`where()`, `route_to()`), and tells it how a trip ended.

November 22 (owner; docs/ROOM_NAVIGATION_PLAN.md) replaced the room vote that ran here since October 11:
fifteen loose views per room, grown from his own belief, called the hallway the dining room and never knew
the living room. Now:

- Photos file themselves: standing still at a spot, one per 45 degrees the camera faces there, each with
  bearing, compass, depth and a JPEG kept under local/ (owner: kept indefinitely, never committed).
- He knows the spot (and which way he faces in its own frame) from the photos, judged over a run of looks
  against every spot plus "none of them", and from wheel travel: standing still the answer cannot flicker,
  and a spot changes only after he has rolled somewhere.
- Every trip from a spot to another stop becomes a passage, whoever drove it (no tour).
- Rooms come only from people: "this is the kitchen" labels the spot, "the kitchen is to your left" gives a
  bearing from it, and a trip toward a room someone asked for labels where it ended until a person confirms
  or corrects it. Nothing is added to a room from his own belief any more.
- Buildings are told apart by the router's hardware address (never the network name: a campus is one name
  across many buildings) and by views; nothing matching anywhere means a place he has never seen.

The old room vote survives only as the fallback for a spot not yet known (room-labelled photos, including
the October views moved over as `legacy`), with its measured V2 thresholds, and it now drops a belief that
stopped matching. Everyday views and waking up somewhere else are unchanged.
"""
from .field_guide import GUIDE
from array import array
from collections import Counter, deque
import math
from operator import mul
from pathlib import Path
from threading import Event, RLock, Thread
import time
from uuid import uuid4

from .adapters.places import NAMESPACE
from .contracts import ProviderFailure
from .providers import Capability
from .room_map import (SCHEMA as MAP_SCHEMA, BUCKET_DEG, RoomMap, circular_mean, doorways_in, relative_words,
                       reversed_segments, segments_of, separation, thumb_shift, wrap)

SCHEMA = """
CREATE TABLE IF NOT EXISTS places(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL, source_turn TEXT, import_ref TEXT);
CREATE TABLE IF NOT EXISTS place_embeddings(
 id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE,
 namespace TEXT NOT NULL, vector BLOB NOT NULL, heading REAL, captured_at REAL NOT NULL, source TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS place_links(
 a TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE, b TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE,
 transitions INTEGER NOT NULL, last_at REAL NOT NULL, PRIMARY KEY(a, b));
CREATE TABLE IF NOT EXISTS place_views(
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, vector BLOB NOT NULL, day TEXT NOT NULL, captured_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS place_views_at ON place_views(namespace, captured_at);
CREATE TABLE IF NOT EXISTS place_last_views(
 id TEXT PRIMARY KEY, namespace TEXT NOT NULL, vector BLOB NOT NULL, captured_at REAL NOT NULL, room_id TEXT);
""" + MAP_SCHEMA


def migrate(connection):
    """November 22: rooms belong to a building; the October room views become loose `legacy` photos.

    place_embeddings and place_links are no longer read or written (the views are copied to room_photos, and
    the links, mostly flicker while standing still, gave way to passages). Their rows are left as they were."""
    columns = {r[1] for r in connection.execute('PRAGMA table_info(places)')}
    if 'building_id' not in columns:
        connection.execute('ALTER TABLE places ADD COLUMN building_id TEXT REFERENCES buildings(id) ON DELETE CASCADE')
    if connection.execute('SELECT 1 FROM buildings LIMIT 1').fetchone() is not None:
        return
    owned = not connection.in_transaction
    if owned:
        connection.execute('BEGIN IMMEDIATE')   # The views move in one piece or not at all.
    try:
        _move_to_home(connection)
        if owned:
            connection.commit()
    except BaseException:
        if owned:
            connection.rollback()
        raise


def _move_to_home(connection):
    """Every room so far was taught at home. Its first router becomes home's (adopt_network)."""
    rooms = connection.execute('SELECT id FROM places').fetchall()
    if not rooms and not connection.execute('SELECT 1 FROM place_embeddings LIMIT 1').fetchone():
        return
    home = uuid4().hex
    connection.execute("INSERT INTO buildings VALUES(?,'home',?,NULL,0,1)", (home, time.time()))
    connection.execute('UPDATE places SET building_id=? WHERE building_id IS NULL', (home,))
    for key, place_id, namespace, vector, captured_at in connection.execute(
            'SELECT id,place_id,namespace,vector,captured_at FROM place_embeddings').fetchall():
        connection.execute("INSERT OR IGNORE INTO room_photos VALUES(?,?,NULL,?,NULL,?,?,NULL,NULL,NULL,NULL,NULL,?,'legacy')",
                           (key, home, place_id, namespace, vector, captured_at))
    # The old rows stay where they were (nothing reads them now), so going back to older code loses nothing.


# V2 config values (PLACE_*), measured on this robot in July 2026: the fallback room vote.
QUERY_INTERVAL = 1.5
SETTLED_INTERVAL = 6.       # Known spot, wheels still, nothing new to photograph: spare the CPU.
TOPK = 3
CONFIDENT = .80
MINIMUM = .68
MARGIN = .04
SOLO_CONFIDENT = .86        # 2026-07-25: another room in the house scored .75-.82 against a lone gallery.
HYSTERESIS = 5
UNKNOWN_STREAK = 8          # Unrecognized frames after moving before the room counts as unfamiliar.
LOST_STREAK = 16            # ... while believing a room: drop the belief rather than insist.
STATIC_FLIP_STREAK = 10     # Confident votes for another room with no motion signal: he was carried.
# November 22 (run 8cece04a): told "dining room" in the hall, he kept believing it for three minutes in the
# living room while every reading was unsure and most put another room first. A belief its room no longer
# tops this many readings in a row is dropped.
STALE_STREAK = 5
OCCLUSION_FACE_WIDTH = .25  # V2 skipped frames with a person covering >35% of the view; V3 has faces, not
                            # bodies, so a face a quarter of the frame wide stands in for that.

# Spots (November 22). Similarities are MobileCLIP-S2 cosines: on this robot two different rooms' views reach
# .81 at most (October 31) and a view of the same room from another spot or angle is typically about .77.
SPOT_CENTRE = .80           # A spot's best photo here is as likely to be it as not...
SPOT_SCALE = .03            # ...and each .03 above multiplies the odds by e.
NONE_SIM = .79              # "None of these spots": as likely as a spot whose best photo scores this.
SPOT_SURE = .7              # Belief needed to say he is on a spot.
SPOT_FACING_DEG = 40.       # On a spot whose frame is known this run, only photos about this way count.
NEAR_M = .6                 # Back within this of where he stood on a spot this run (odometer): that spot.
NEW_SPOT_READINGS = 4       # Still readings in a row that fit no spot before a new one is made.
STOP_S = 2.                 # Still this long after rolling: a stop.
TRIP_MIN_M = .5             # Shorter trips (and turns in place) leave him on the same spot.
SPOT_PHOTOS = 16            # Two per direction at most (day and night).
FACING_SHIFT_SCORE = .45    # A tiny-copy shift this good refines which way he faces.
DEPTH_DIFFERS_M = 1.        # Depth straight ahead this different from a photo's: a little less like it.
MERGE_SIM = .84             # A spot made this run whose view matches an old spot's photo this well is that spot.
CARRIED_SIM = .70           # On a spot by the wheels, a view this unlike its photo this way...
CARRIED_READINGS = 3        # ...this many times in a row: he was carried somewhere.
# Buildings.
BUILDING_MATCH = .80        # A view this close to a building's photo...
BUILDING_READINGS = 3       # ...this many times: he is there.
NEW_PLACE_SIM = .72         # Nothing in any building this close...
NEW_PLACE_READINGS = 8      # ...this many readings in a row, on a network none knows: a new place.
LEGACY_RETIRE = 8           # A room with this many photos on spots people named drops its October views.
READING_FAILURES = 20       # Readings failing in a row before the worker gives up for the run.
NETWORK_RETRY_S = 60.       # An unreadable router is read again this often...
NETWORK_RETRIES = 5         # ...this many times.
UNKNOWN_ROUTER_SIM = .76    # On a router no building knows, views all under this for NEW_PLACE_READINGS: somewhere new.
GOAL_KEEP_S = 120.          # A trip's room goal not landed on a spot by then is dropped.
# Descriptions: one hosted line per photo, spread out (owner, November 22: the model describes rooms).
DESCRIBE_EVERY_S = 20.
DESCRIBE_PER_RUN = 30

# How many days he has seen a view like this one, with no room name needed (owner, October 31: the
# workshop and its telescope greeted as new at every start; room names are flaky). Unlabelled samples
# of what he saw, at most one every two minutes while the wheels are still, plus the photos, each counted
# on its local day. October 31, real fingerprints from this robot: two different rooms' saved views reach
# .81 at most, so a stored view at V2's confident bar is the same view.
VIEW_SAME = CONFIDENT
VIEW_DUPLICATE = .92        # Already stored today: not stored again.
VIEW_CHECK = 10.            # Seconds between familiarity checks.
VIEW_SAMPLE = 120.          # Seconds between stored samples.
VIEW_PER_DAY = 40
VIEW_WINDOW_DAYS = 14
VIEW_EVERYDAY_DAYS = 4      # Seen on this many of the last fourteen days: his everyday surroundings.
VIEW_YOUNG_DAYS = 3         # With samples from fewer days than this, "new" means nothing yet.
VIEW_FRESH = 30.            # An older check (camera gone, driving) says nothing.
VIEW_KEPT_DAYS = 30

# Waking up somewhere else (owner, October 31: "If I woke up in a new room I'd want to know why I switched
# places"). The views of his last couple of minutes awake are kept; the next start compares its first minute
# with them. One at the same-view bar means he woke where he went to sleep. Four frames in a row unlike all of
# them mean he was moved while off: on October 31's real fingerprints two different rooms' views were mostly
# .47-.70 apart. Anything between, or a drive before deciding, claims nothing.
LAST_VIEWS = 12
WAKE_WINDOW = 60.
WAKE_FRAMES = 4
WOKE_MOVED = .70
WOKE_SHOWN = 600.           # The move stays in his picture of the moment this long.

RECOGNIZED, UNSURE, UNFAMILIAR, NO_ROOMS, STARTING, NEW_PLACE = (
    'recognized', 'unsure', 'unfamiliar', 'no_rooms_known', 'starting', 'new_place')
WHERE = {'ahead': 0., 'left': 90., 'right': -90., 'behind': 180.}


def unit(values):
    """Plain-Python unit vector (the contract checks run without native libraries)."""
    vector = array('f', values)
    norm = math.sqrt(math.fsum(x * x for x in vector))
    if vector.__len__() != 512 or not math.isfinite(norm) or norm < 1e-9: raise ValueError('InvalidPlaceVector')
    return array('f', (x / norm for x in vector))


def dot(a, b):
    return math.fsum(map(mul, a, b))


def day_of(wall):
    from datetime import datetime
    return datetime.fromtimestamp(wall).date().isoformat()


def light_of(wall):
    from datetime import datetime
    return 'day' if 7 <= datetime.fromtimestamp(wall).hour < 19 else 'night'


class Places:
    def __init__(self, camera, encoder, store, *, trace=None, motion=None, heading=None,
                 clock=time.monotonic, wall_clock=time.time, odometer=None, neck=None, view_settled=None,
                 depth=None, compass=None, network=None, photos_dir=None, describer=None):
        self.camera, self.encoder, self.store, self.trace = camera, encoder, store, trace
        # motion() -> True/False, or None with no trustworthy base signal (disables the freeze gate).
        self.motion = motion or (lambda: None)
        self.heading = heading or (lambda: None)
        self.clock, self.wall_clock = clock, wall_clock
        self.odometer = odometer              # room_map.Odometer the drive owner feeds; None without wheels.
        self.neck = neck or (lambda: 0.)      # Degrees the head is turned from the base (+left).
        self.view_settled = view_settled or (lambda at: True)   # A frame taken with head and base still.
        self.depth = depth or (lambda: None)  # Range readings for a photo, or None.
        self.compass = compass or (lambda: None)
        self.network = network                # () -> router hardware digest, or None.
        self.photos_dir = Path(photos_dir) if photos_dir else None
        self.describer = describer            # (image, cancel) -> {'text'}: one line per photo, hosted.
        self.namespace = NAMESPACE
        self.capability = Capability('places', encoder.capability.provider,
                                     'which building, spot and room Rex is in, and the ways between rooms',
                                     encoder.capability.model, encoder.capability.dependencies,
                                     ('one shared frame; one embedding every 1.5 s; photos kept under local/',),
                                     required=False)
        self.lock = RLock(); self.stop = Event(); self.worker = None; self.failure = None
        self.map = None
        self.started = False
        # Building.
        self.building_id = None      # Read without the lock by the state owner (an atomic attribute).
        self.building_how = None     # network / views / told / new / only (a guess until confirmed)
        self.router = None
        self.network_checked = False
        self.network_tries, self.network_at = 0, float('-inf')
        self.building_votes = Counter()
        self.nowhere_streak = 0
        self._building_cache = None
        # Rooms and photos of the current building.
        self.rooms = {}              # place_id -> name
        self.photos = []             # photo rows (room_map.RoomMap.photos)
        self.by_spot = {}            # spot_id -> [photo rows]
        self.spots = {}              # spot_id -> spot row
        # Spot belief.
        self.belief = {}             # spot_id -> probability; None is "none of them"
        self.anchors = {}            # spot_id -> {'offset', 'x', 'y', 'at'}: spot frame = yaw + offset, this run
        self.here = None             # The spot he stands on, when sure.
        self.unplaced = 0            # Still readings in a row that fit no spot.
        self.unlike_spot = 0         # Readings in a row unlike the spot the wheels say he is on.
        self.made = set()            # Spots made this run (they may turn out to be an old one).
        self._last_thumb = None
        self.last_pose = None        # Odometer pose at the last reading...
        self.last_pose_at = None     # ...and when its frame was taken.
        self.belief_pose = None      # Where the spot belief was last spread by rolling.
        self.still_pose = self.still_at = None   # Where and when he last stood still.
        self.trip = None             # {'from_spot', 'from_offset', 'started', 'marks', 'stopped'}
        self.pending = None          # A label told while rolling: (room_id, where, turn), for the next stop.
        self.goal = None             # {'room_id', 'name', 'how'}: a trip someone asked for, until its stop.
        self.arrived = None          # {'room_id', 'name', 'ask', 'at'}: just got there by sight or directions.
        self.spot_note = None        # Last traced spot state.
        # Fallback room vote.
        self.history = deque(maxlen=HYSTERESIS)
        self.vote = None             # {'place_id','name','since'}: the room the vote believes
        self.previous = None
        self.moved = False
        self.unknown_streak = 0
        self.stale_streak = 0
        self.unfamiliar_since = None
        self.flip = (None, 0)
        self.last_query = None
        self.current = None          # {'place_id','name','since','sure','how'}: the room he believes he is in
        self.last_named = None       # Outcome of the last naming, for an honest reply.
        # Descriptions.
        self.described_at = float('-inf'); self.described = 0
        # Familiarity and waking up.
        self.views = []              # (vector, day, captured_at): stored samples and photos
        self.view = None             # {'days', 'best', 'at'}: the latest familiarity check
        self.view_checked = self.view_stored = float('-inf')
        self.run_began = None
        self.run_clock = None
        self.slept = None
        self.woke = None
        self.unlike = 0
        self.drove = False
        self.files = []              # (path, jpeg bytes) waiting to be written outside the lock

    # Lifecycle ---------------------------------------------------------------------------
    def start(self):
        self.encoder.start()
        self.load()
        self.camera.hub.register('places')
        self.worker = Thread(target=self._run, name='r3x-places', daemon=True); self.worker.start()

    def _run(self):
        last, failures = None, 0
        try:
            self._check_network()
            while not self.stop.is_set():
                began = self.clock(); frame = self.camera.hub.take('places', last)
                if frame:
                    last = frame.frame_id
                    try:
                        self.observe(frame)
                        failures = 0
                    except ProviderFailure:
                        raise   # The model itself failed: the capability is down, as before.
                    except Exception as exc:
                        # Review, November 22: one bad reading must not end room tracking for the run.
                        failures += 1
                        if self.trace: self.trace.emit('place_reading_failed', error_type=type(exc).__name__, failures=failures)
                        if failures >= READING_FAILURES:
                            raise
                self.tick()
                self._retry_network()
                self._describe_one()
                with self.lock:
                    settled = (self.current is not None and self.here is not None and self.motion() is False
                               and not self._wants_photo())
                self.stop.wait(max(.05, (SETTLED_INTERVAL if settled else QUERY_INTERVAL) - (self.clock() - began)))
        except Exception as exc:
            self.failure = type(exc).__name__
            if self.trace: self.trace.emit('provider_failed', capability='places', error_type=self.failure)
            self.cancel()

    def note_motion(self, *_):
        """The base started a move (drive owner callback): a room change is now possible."""
        with self.lock: self.moved = self.drove = True

    def cancel(self): self.stop.set()

    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(5)
            if self.worker.is_alive(): raise ProviderFailure('PlaceWorkerDidNotStop')
        self.encoder.close()

    # Loading -----------------------------------------------------------------------------
    def load(self):
        with self.lock:
            if self.map is None:
                self.map = RoomMap(self.store, self.wall_clock)   # The store's start ran migrate().
            if self.building_id is None:
                buildings = self.map.buildings()
                if len(buildings) == 1:
                    self.building_id, self.building_how = buildings[0]['id'], 'only'
            self._load_building()
            if self.run_began is None:
                self.run_began, self.run_clock = self.wall_clock(), self.clock()
                rows = self.store.rows('SELECT vector,captured_at,room_id FROM place_last_views WHERE namespace=? '
                                       'ORDER BY captured_at', (self.namespace,))
                views = [array('f', r['vector']) for r in rows]
                if rows and all(len(v) == 512 for v in views):
                    self.slept = {'views': views, 'ended_at': rows[-1]['captured_at'], 'room_id': rows[-1]['room_id']}
            since = self.wall_clock() - VIEW_KEPT_DAYS * 86400
            self.views = [(v, day_of(at), at) for _, v, at in self.map.all_photo_vectors(self.namespace) if at >= since]
            for r in self.store.rows('SELECT vector,day,captured_at FROM place_views WHERE namespace=? AND captured_at>=? '
                                     'ORDER BY captured_at', (self.namespace, since)):
                vector = array('f'); vector.frombytes(r['vector'])
                if len(vector) == 512: self.views.append((vector, r['day'], r['captured_at']))
            self.started = True

    def _load_building(self):
        b = self.building_id
        self.rooms = {r['id']: r['name'] for r in self.store.rows('SELECT id,name FROM places WHERE building_id=?', (b,))} if b else {}
        self.photos = self.map.photos(b, self.namespace) if b else []
        self.spots = {s['id']: s for s in self.map.spots(b)} if b else {}
        self.by_spot = {}
        for p in self.photos:
            if p['spot_id']:
                self.by_spot.setdefault(p['spot_id'], []).append(p)
        for gone in [k for k in self.belief if k is not None and k not in self.spots]:
            del self.belief[gone]
        for gone in [k for k in self.anchors if k not in self.spots]:
            del self.anchors[gone]
        if self.here and self.here not in self.spots:
            self.here = None
        for room in (self.current, self.vote):
            if room and room['place_id'] not in self.rooms:
                self.current = self.vote = None; self.history.clear()

    # Buildings ---------------------------------------------------------------------------------
    def _retry_network(self):
        """An unreadable router (Wi-Fi still joining at start) is read again a few times."""
        if (self.router or self.network is None or self.network_tries >= NETWORK_RETRIES
                or self.clock() - self.network_at < NETWORK_RETRY_S):
            return
        self.network_checked = False
        self._check_network()

    def _check_network(self):
        """The router's hardware address, at the start and a few times more while unreadable (owner: never the
        network name)."""
        self.network_tries += 1
        self.network_at = self.clock()
        if self.network_checked or self.network is None:
            self.network_checked = True
            return
        try:
            router = self.network()
        except Exception as exc:
            router = None
            if self.trace: self.trace.emit('place_network', outcome='unreadable', error_type=type(exc).__name__)
        with self.lock:
            self.network_checked, self.router = True, router
            if not router:
                return
            known = self.map.buildings_on(router)
            if len(known) == 1 and self.building_how not in ('told', 'network'):
                if self.building_how == 'new' and self.building_id != known[0]:
                    self.merge_into(self.building_id, known[0])   # A provisional place that was this one.
                self._settle(known[0], 'network')
            elif not known:
                adopt = [b for b in self.map.buildings() if b['adopt_network']]
                if len(adopt) == 1:
                    self.map.note_network(adopt[0]['id'], router)
                    self._settle(adopt[0]['id'], 'network')
            if self.trace: self.trace.emit('place_network', outcome='known' if known else 'new_network', buildings=len(known))

    def _settle(self, building_id, how, note_network=True):
        changed = building_id != self.building_id
        self.building_id, self.building_how = building_id, how
        if self.router and how != 'network' and note_network:
            self.map.note_network(building_id, self.router)
        self._building_cache = None
        if changed:
            self.belief, self.anchors, self.here, self.trip, self.unplaced = {}, {}, None, None, 0
            self.current = self.vote = None; self.history.clear()
            self._load_building()
        if self.trace:
            b = self.map.building(building_id)
            self.trace.emit('place_building', building_id=building_id, name=(b or {}).get('name'), how=how)

    def _settled(self):
        """The building is known well enough to file photos and spots in it."""
        if self.building_id is None:
            return False
        return self.building_how != 'only' or (self.network_checked and self.router is None)

    def _building_row(self):
        if self.building_id is None or self.map is None:
            return None
        if self._building_cache is None or self._building_cache['id'] != self.building_id:
            self._building_cache = self.map.building(self.building_id)
        return self._building_cache

    def _decide_building(self, q):
        """Views decide the building when the router could not: a known one, or somewhere new.

        On a network no building knows, a view unlike every building's photos for NEW_PLACE_READINGS readings
        is a place he has never seen; with no router reading at all, only views far from everything count."""
        provisional = self.building_how == 'new' and (self._building_row() or {}).get('provisional')
        if (self.building_how not in (None, 'only') and not provisional) or not self.network_checked:
            return
        best_by = {}
        for building_id, v, _ in self.map.all_photo_vectors(self.namespace):
            if provisional and building_id == self.building_id:
                continue
            s = dot(q, v)
            if s > best_by.get(building_id, -1.): best_by[building_id] = s
        best = max(best_by.values(), default=0.)
        top = max(best_by, key=best_by.get) if best_by else None
        if top is not None and best >= BUILDING_MATCH:
            self.building_votes[top] += 1
            self.nowhere_streak = 0
            if self.building_votes[top] >= BUILDING_READINGS:
                if provisional:
                    # The place he thought new looks like one he knows after all: its map joins that one.
                    self.merge_into(self.building_id, top, 'views')
                else:
                    self._settle(top, 'views')
            return
        if provisional:
            return
        far = best < (UNKNOWN_ROUTER_SIM if self.router else NEW_PLACE_SIM)
        self.nowhere_streak = self.nowhere_streak + 1 if far else 0
        if self.nowhere_streak >= NEW_PLACE_READINGS:
            if self.building_how == 'only' and not self.photos:
                # The only building has no photos yet: nothing to tell it from, so it is still home.
                self._settle(self.building_id, 'only_no_photos')
                return
            new = self.map.add_building(provisional=True)
            self._settle(new, 'new')
            if self.trace: self.trace.emit('place_new', building_id=new, best=round(best, 3))

    # Observation ---------------------------------------------------------------------------
    def occluded(self, now):
        faces = self.camera.latest(now) if hasattr(self.camera, 'latest') else ()
        return any((f.get('box') or [0, 0, 0, 0])[2] > OCCLUSION_FACE_WIDTH for f in faces or ())

    def observe(self, frame, *, deliberate=False):
        if frame.session_id != getattr(self.camera, 'session_id', frame.session_id) or not 0 <= self.clock() - frame.at <= 1:
            return None
        now = self.clock()
        if not deliberate and self.occluded(now):
            if self.trace: self.trace.emit('place_skipped', reason='person_close')
            return None
        # Where the head points and whether the view was still, read as close to the frame as possible.
        meta = {'at': frame.at, 'neck': self._safe(self.neck, 0.), 'deliberate': deliberate,
                'settled': deliberate or self._safe(lambda: self.view_settled(frame.at), True),
                'depth': self._safe(self.depth, None), 'compass': self._safe(self.compass, None)}
        vector = unit(self.encoder.embed(frame))        # Slow work outside the lock.
        thumb = self._safe(lambda: self.encoder.thumbnail(frame), None) if hasattr(self.encoder, 'thumbnail') else None
        with self.lock:
            status = self._reading(vector, thumb, meta, now)
            files, self.files = self.files, []
        self._write(files, frame)
        return status

    @staticmethod
    def _safe(read, default):
        try:
            value = read()
            return default if value is None and default is not None else value
        except Exception:
            return default

    def _write(self, files, frame):
        for path in files:
            try:
                data = self.encoder.jpeg(frame) if hasattr(self.encoder, 'jpeg') else None
                if data:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
            except Exception as exc:
                if self.trace: self.trace.emit('place_photo_unsaved', error_type=type(exc).__name__)

    def _reading(self, q, thumb, meta, now):
        moving = self.motion()
        if moving is None or moving: self.moved = True
        if moving is True: self.drove = True
        self._decide_building(q)
        # The fallback room vote over every room-labelled photo of this building.
        scores = self._scores(q)
        status = self.classify(scores)
        self.last_query = {'at': now, 'status': status, 'top': [(n, round(s, 3)) for _, n, s in scores[:2]]}
        self._update(scores, status, now)
        if self._settled() and self.odometer is not None:
            self._locate(q, thumb, meta, now, moving)
        self._believe(now)
        if self.trace:
            self.trace.emit('place_sample', status=self.context_status(), top=self.last_query['top'],
                            **({'spot': self.here[:8]} if self.here else {}))
        self._woke_check(q, now)
        if moving is not True: self._note_view(q, now)
        return status

    # The fallback room vote ------------------------------------------------------------------------
    def _scores(self, q):
        """Each room scores the mean of its TOPK best photo similarities (V2 PLACE_TOPK)."""
        by_room = {}
        for p in self.photos:
            room = p['room_id']
            if room in self.rooms and (p['spot_id'] is None or p['room_source'] == 'told'):
                by_room.setdefault(room, []).append(dot(q, p['vector']))
        scores = [(pid, self.rooms[pid], sum(sorted(sims)[-TOPK:]) / min(TOPK, len(sims))) for pid, sims in by_room.items()]
        return sorted(scores, key=lambda s: s[2], reverse=True)

    @staticmethod
    def classify(scores):
        if not scores or scores[0][2] < MINIMUM: return UNFAMILIAR
        if len(scores) == 1: return RECOGNIZED if scores[0][2] >= SOLO_CONFIDENT else UNSURE
        return RECOGNIZED if scores[0][2] >= CONFIDENT and scores[0][2] - scores[1][2] >= MARGIN else UNSURE

    def _update(self, scores, status, now):
        confident = status == RECOGNIZED
        self.history.append(scores[0][0] if confident else None)
        votes = [p for p in self.history if p]
        attempted = False
        if votes:
            candidate, n = Counter(votes).most_common(1)[0]
            current = self.vote['place_id'] if self.vote else None
            if n >= HYSTERESIS // 2 + 1 and candidate != current:
                if self.vote is None or self.moved:
                    self._confirm(candidate, now)
                else:
                    attempted = True
                    pid, streak = self.flip
                    self.flip = (candidate, streak + 1 if pid == candidate else 1)
                    if self.flip[1] >= STATIC_FLIP_STREAK:
                        self.moved = True; self._confirm(candidate, now)   # Carried.
        if not attempted: self.flip = (None, 0)
        if self.vote and scores:
            self.stale_streak = 0 if scores[0][0] == self.vote['place_id'] else self.stale_streak + 1
            if self.stale_streak >= STALE_STREAK:
                self._leave(now, 'stopped_matching')
        if status == UNFAMILIAR:
            self.unknown_streak += 1
            if self.vote and self.unknown_streak >= LOST_STREAK:
                self._leave(now, 'lost')
            if self.unknown_streak >= UNKNOWN_STREAK and self.moved and self.unfamiliar_since is None and self.vote is None:
                self.unfamiliar_since = now
                if self.trace: self.trace.emit('place_unfamiliar', streak=self.unknown_streak)
        else:
            self.unknown_streak = 0
            if confident: self.unfamiliar_since = None

    def _confirm(self, place_id, now):
        self.vote = {'place_id': place_id, 'name': self.rooms.get(place_id), 'since': now}
        self.previous = None
        self.moved, self.unknown_streak, self.unfamiliar_since, self.flip, self.stale_streak = False, 0, None, (None, 0), 0
        self.history.clear()
        if self.trace: self.trace.emit('place_confirmed', place_id=place_id, name=self.rooms.get(place_id))

    def _leave(self, now, reason):
        if self.vote: self.previous = (self.vote['place_id'], now)
        self.vote = None; self.history.clear(); self.flip = (None, 0); self.stale_streak = 0
        if reason == 'lost': self.moved = True
        if self.trace: self.trace.emit('place_left', reason=reason)

    # Spots --------------------------------------------------------------------------------------
    def _pose(self):
        return self.odometer.pose() if self.odometer is not None else None

    def _still(self, moving):
        return moving is False and self.odometer is not None and self.odometer.still_for() >= 1.

    def _facing(self, spot_id):
        """Which way the base faces in the spot's frame now, when that frame is known this run."""
        pose, anchor = self._pose(), self.anchors.get(spot_id)
        return None if pose is None or anchor is None else wrap(pose[2] + anchor['offset'])

    def _spot_sims(self, q, camera_by_spot, depth_ahead):
        """{spot: (best similarity, photo)}; a spot whose frame is known counts only photos this way."""
        out = {}
        for spot_id, photos in self.by_spot.items():
            camera = camera_by_spot.get(spot_id)
            best, chosen = None, None
            for p in photos:
                if camera is not None and (p['bearing'] is None or separation(p['bearing'], camera) > SPOT_FACING_DEG):
                    continue
                s = dot(q, p['vector'])
                if depth_ahead is not None and (p['depth'] or {}).get('front_m') is not None \
                        and abs(p['depth']['front_m'] - depth_ahead) > DEPTH_DIFFERS_M:
                    s -= .02   # Depth breaks ties between look-alike rooms ("this one is smaller").
                if best is None or s > best:
                    best, chosen = s, p
            if best is not None:
                out[spot_id] = (best, chosen)
        return out

    def _locate(self, q, thumb, meta, now, moving):
        pose = self._pose()
        if pose is None:
            return
        self._last_thumb = thumb
        # A deliberate look from the drive owner (a look-around stop) counts once the wheels themselves are still.
        still = (self.odometer.still_for() >= .8 if meta['deliberate'] else self._still(moving)) and meta['settled']
        self._track_trip(pose, moving, thumb, meta, now)
        # Motion: rolling spreads the belief; standing or turning in place keeps it. Distances count from where
        # the belief was last spread and from the spot itself, so a slow crawl (0.1 m a reading) counts too.
        if self.belief_pose is None:
            self.belief_pose = pose
        rolled = math.hypot(pose[0] - self.belief_pose[0], pose[1] - self.belief_pose[1])
        if rolled >= .2:
            self.belief_pose = pose
            mix = min(.9, rolled / 1.5)
            n = len(self.spots) + 1
            self.belief = {k: (1 - mix) * v + mix / n for k, v in self.belief.items()}
            for k in list(self.spots) + [None]:
                self.belief.setdefault(k, mix / n)
            for k, a in self.anchors.items():
                # Where he stood on it this run is known: rolled well away, he is not there.
                if k in self.belief and math.hypot(pose[0] - a['x'], pose[1] - a['y']) > 2 * NEAR_M:
                    self.belief[k] *= .1
        if self.here is not None:
            a = self.anchors.get(self.here)
            if a is None or math.hypot(pose[0] - a['x'], pose[1] - a['y']) > NEAR_M:
                self._off_spot('rolled')
        self.last_pose, self.last_pose_at = pose, meta['at']
        if not still:
            return
        self.still_pose, self.still_at = pose, meta['at']
        # Back where he stood on a spot this run: that spot, by the wheels.
        near = [(math.hypot(pose[0] - a['x'], pose[1] - a['y']), k) for k, a in self.anchors.items() if k in self.spots]
        near = min(near, default=None)
        camera_by_spot = {k: wrap(pose[2] + a['offset'] + meta['neck']) for k, a in self.anchors.items()}
        depth_ahead = (meta['depth'] or {}).get('front_m')
        sims = self._spot_sims(q, camera_by_spot, depth_ahead)
        # Observation: each spot by its best photo against "none of these".
        weight = lambda s: math.exp(max(-5., min(5., (s - SPOT_CENTRE) / SPOT_SCALE)))
        none = weight(NONE_SIM)
        if not self.belief:
            self.belief = {k: 1. / (len(self.spots) + 1) for k in list(self.spots) + [None]}
        posterior = {}
        for k, prior in self.belief.items():
            if k is None:
                posterior[k] = prior * none
            else:
                # No photo of it this way: no evidence for it, a little less likely than somewhere new.
                posterior[k] = prior * (weight(sims[k][0]) if k in sims else none * .8)
        total = sum(posterior.values()) or 1.
        self.belief = {k: max(1e-4, v / total) for k, v in posterior.items()}
        total = sum(self.belief.values())
        self.belief = {k: v / total for k, v in self.belief.items()}
        if self._merge_duplicate(near, sims, meta, pose):
            return
        trip = self.trip
        expected = near is not None and meta['deliberate'] and self.anchors[near[1]].get('expected')
        if near is not None and near[0] <= NEAR_M and (
                trip is None or (trip['stopped'] and trip['length'] <= 6.) or expected) \
                and self._not_carried(sims.get(near[1])):
            spot = near[1]
            self.belief = {k: (.95 if k == spot else .05 / max(1, len(self.belief) - 1)) for k in self.belief}
            self._on_spot(spot, None, q, thumb, meta, now)
        else:
            best = max((k for k in self.belief if k is not None), key=self.belief.get, default=None)
            if best is not None and self.belief[best] >= SPOT_SURE and best in sims:
                self._on_spot(best, sims[best][1], q, thumb, meta, now)
            elif self.belief.get(None, 0.) >= .6 and (self.here is None):
                self.unplaced += 1
                if self.unplaced >= NEW_SPOT_READINGS:
                    self._new_spot(pose, now)
            else:
                self.unplaced = 0
        if self.here is not None:
            self._maybe_photo(q, thumb, meta, now)

    def _merge_duplicate(self, near, sims, meta, pose):
        """A spot made this run (he faced a way the old photos never showed) that turns out to be an old spot:
        once he faces a way the old spot was photographed, they become one, its photos turned into the old
        spot's frame."""
        if near is None or near[0] > NEAR_M or near[1] not in self.made:
            return False
        made = near[1]
        others = [(sim, k) for k, (sim, photo) in sims.items() if k != made and photo['bearing'] is not None]
        if not others:
            return False
        sim, old = max(others)
        if sim < MERGE_SIM or (sims.get(made) and sims[made][0] >= sim):
            return False
        photo = sims[old][1]
        shift = thumb_shift(self._last_thumb, photo['thumb']) if self._last_thumb and photo.get('thumb') else None
        camera = wrap(photo['bearing'] + (shift[0] if shift and shift[1] >= FACING_SHIFT_SCORE else 0.))
        offset_old = wrap(camera - meta['neck'] - pose[2])
        delta = wrap(offset_old - self.anchors[made]['offset'])
        self.map.merge_spot(made, old, delta)
        if self.trip is not None and self.trip['from_spot'] == made:
            # Review, November 22: the trip under way started on the spot just merged away.
            self.trip['from_spot'] = old
            if self.trip['from_offset'] is not None:
                self.trip['from_offset'] = wrap(self.trip['from_offset'] + delta)
        self.made.discard(made)
        self.anchors.pop(made, None)
        self.anchors[old] = {'offset': offset_old, 'x': pose[0], 'y': pose[1], 'at': self.clock()}
        self.belief.pop(made, None)
        self.belief[old] = max(self.belief.get(old, 0.), .9)
        if self.trace: self.trace.emit('place_spot', status='merged', spot=made[:8], into=old[:8], similarity=round(sim, 3))
        self.here = old
        self._reload()
        return True

    def _not_carried(self, sim):
        """By the wheels he is still on the spot; a view unlike its photo this way, again and again, means he was
        picked up and carried (the wheels never turned)."""
        if sim is None or sim[0] >= CARRIED_SIM:
            self.unlike_spot = 0
            return True
        self.unlike_spot += 1
        if self.unlike_spot < CARRIED_READINGS:
            return True
        self.unlike_spot = 0
        if self.trace: self.trace.emit('place_spot', status='carried', spot=(self.here or '')[:8], best=round(sim[0], 3))
        self.anchors.clear(); self.trip = None; self._off_spot('carried')
        return False

    def _on_spot(self, spot_id, photo, q, thumb, meta, now):
        pose = self._pose()
        if photo is not None and photo['bearing'] is not None:
            camera = photo['bearing']
            shift = thumb_shift(thumb, photo['thumb']) if thumb and photo.get('thumb') else None
            if shift and shift[1] >= FACING_SHIFT_SCORE:
                camera = wrap(camera + shift[0])
            offset = wrap(camera - meta['neck'] - pose[2])
            old = self.anchors.get(spot_id)
            if old is not None and self.here == spot_id:
                offset = circular_mean([old['offset'], offset], [.7, .3])
            self.anchors[spot_id] = {'offset': offset, 'x': pose[0], 'y': pose[1], 'at': now}
        elif spot_id not in self.anchors:
            return   # No photo this way and no frame yet: wait for a reading that has one.
        else:
            self.anchors[spot_id] = {'offset': self.anchors[spot_id]['offset'], 'x': pose[0], 'y': pose[1], 'at': now}
        arriving = self.here != spot_id
        self.here, self.unplaced = spot_id, 0
        if arriving or (self.trip and self.trip['stopped']):
            if arriving: self.map.touch_spot(spot_id)
            self._arrive(spot_id, now)

    def _new_spot(self, pose, now):
        """Nowhere he has stood before: a new spot, its frame his heading now."""
        spot_id = self.map.add_spot(self.building_id)
        self.spots[spot_id] = self.map.spot(spot_id)
        self.made.add(spot_id)
        self.anchors[spot_id] = {'offset': 0., 'x': pose[0], 'y': pose[1], 'at': now}
        self.belief = {k: v * .1 for k, v in self.belief.items()}
        self.belief[spot_id] = .9
        self.here, self.unplaced = spot_id, 0
        if self.trace: self.trace.emit('place_spot', status='new', spot=spot_id[:8])
        self._arrive(spot_id, now)

    def _off_spot(self, why):
        if self.here is not None and self.trace:
            self.trace.emit('place_spot', status='left', spot=self.here[:8], why=why)
        self.here = None

    def _arrive(self, spot_id, now):
        """On a spot: after a trip, the trip becomes a passage and a trip's goal labels where it ended; a name
        told while rolling lands here. A look in the middle of a trip (a plan still running) leaves it going."""
        trip = self.trip
        if trip is not None and not trip['stopped']:
            trip = None
        else:
            self.trip = None
        if trip and trip['from_spot'] and trip['from_spot'] != spot_id:
            self._record_passage(trip, spot_id)
        if self.pending:
            room_id, where, turn = self.pending
            self.pending = None
            self._apply_name(room_id, where, turn, spot_id)
        goal = self.goal
        if goal and goal.get('ended') and trip:
            self.goal = None
            spot = self.spots.get(spot_id) or {}
            told = spot.get('room_source') == 'told'
            if not told:
                self.map.label_spot(spot_id, goal['room_id'], 'trip')
            if not (told and spot.get('room_id') == goal['room_id']):
                # By sight or by someone's directions: he asks once whether this is it.
                self.arrived = {'room_id': goal['room_id'], 'name': goal['name'], 'ask': goal['how'] != 'map' or told, 'at': now}
            if (trip['from_spot'] in self.spots and trip['from_spot'] != spot_id
                    and trip.get('first_heading') is not None):
                self.map.add_bearing(trip['from_spot'], goal['room_id'], trip['first_heading'], 'trip')
        self._reload()
        if self.trace: self.trace.emit('place_spot', status='on', spot=spot_id[:8],
                                       room=self.rooms.get((self.spots.get(spot_id) or {}).get('room_id')))

    def _reload(self):
        self._building_cache = None
        self._load_building()

    def _record_passage(self, trip, to_spot):
        from_offset = trip['from_offset']
        to_anchor = self.anchors.get(to_spot)
        if from_offset is None or to_anchor is None or trip['from_spot'] not in self.spots:
            return
        trail = self.odometer.since(trip['started'])
        if len(trail) < 2:
            return
        segments = segments_of(trail, from_offset)
        if not segments or sum(s['length_m'] for s in segments) < TRIP_MIN_M:
            return
        # Tiny copies along the way, about every MARK_EVERY_M, with how far along each was.
        along, marks, last, prev = 0., [], -1., (trail[0][1], trail[0][2])
        distance_at = []
        for p in trail:
            along += math.hypot(p[1] - prev[0], p[2] - prev[1]); prev = (p[1], p[2])
            distance_at.append((p[0], along))
        for m in trip['marks']:
            at = max((d for t, d in distance_at if t <= m['t']), default=0.)
            if at - last >= .4 and m['thumb']:
                marks.append({'along_m': at, 'bearing': wrap(m['camera'] + from_offset), 'thumb': m['thumb']})
                last = at
        doorways = doorways_in([(p[4], p[5]) for p in trail])
        passage = self.map.add_passage(self.building_id, trip['from_spot'], to_spot, segments,
                                       wrap(to_anchor['offset'] - from_offset), doorways, marks, 'trip')
        trip['first_heading'] = segments[0]['heading']
        if self.trace:
            self.trace.emit('place_passage', passage=passage[:8], from_spot=trip['from_spot'][:8], to_spot=to_spot[:8],
                            length_m=round(sum(s['length_m'] for s in segments), 2), segments=len(segments),
                            doorways=doorways, marks=len(marks))

    def _track_trip(self, pose, moving, thumb, meta, now):
        """A trip starts when he rolls off a spot and ends when he has stood still a moment."""
        if self.trip is not None and self.trip['stopped'] and math.hypot(
                pose[0] - self.trip['stop_pose'][0], pose[1] - self.trip['stop_pose'][1]) >= .15:
            # He rolled on before the last stop was placed: that trip (and any room goal on it) is dropped.
            self.trip, self.goal = None, None
        if self.trip is None:
            origin = self.still_pose or self.last_pose
            origin_at = self.still_at if self.still_pose else self.last_pose_at
            if origin is not None and math.hypot(pose[0] - origin[0], pose[1] - origin[1]) >= .15:
                self.trip = {'from_spot': self.here, 'from_offset': (self.anchors.get(self.here) or {}).get('offset'),
                             'started': (origin_at or now) - .5, 'start': pose, 'marks': [], 'stopped': False}
            return
        if moving and thumb:
            self.trip['marks'].append({'t': meta['at'], 'camera': pose[2] + meta['neck'], 'thumb': thumb})
            del self.trip['marks'][:-80]
        if moving is False and self.odometer.still_for() >= STOP_S:
            self._close_trip()

    def _close_trip(self):
        """The wheels have stopped: a trip long enough to have left the spot ends here."""
        trip = self.trip
        if trip is None or trip['stopped']:
            return
        trail = self.odometer.since(trip['started'])
        travelled = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(trail, trail[1:]))
        if travelled < TRIP_MIN_M:
            self.trip = None   # A turn or a nudge: still on the same spot.
            return
        trip['stopped'], trip['length'] = True, travelled
        trip['stop_pose'] = self._pose() or (trail[-1][1], trail[-1][2], trail[-1][3])
        start = trip['from_spot']
        if self.here is not None and (self.here == start or start is None):
            self._off_spot('trip')

    def _wants_photo(self):
        return self.here is not None and self._photo_gap() is not None

    def _photo_gap(self, camera=None):
        """The camera bearing (spot frame) still missing a photo at this spot, or None."""
        spot = self.here
        pose, anchor = self._pose(), self.anchors.get(spot)
        if spot is None or pose is None or anchor is None:
            return None
        camera = wrap(pose[2] + anchor['offset'] + self._safe(self.neck, 0.)) if camera is None else camera
        photos = self.by_spot.get(spot, [])
        if len(photos) >= SPOT_PHOTOS:
            return None
        light = light_of(self.wall_clock())
        near = [p for p in photos if p['bearing'] is not None and separation(p['bearing'], camera) < BUCKET_DEG / 2]
        if not near or (len(near) < 2 and all(light_of(p['captured_at']) != light for p in near)):
            return camera
        return None

    def _maybe_photo(self, q, thumb, meta, now):
        pose, anchor = self._pose(), self.anchors.get(self.here)
        camera = wrap(pose[2] + anchor['offset'] + meta['neck'])
        if self._photo_gap(camera) is None:
            return
        key = uuid4().hex
        path = self.photos_dir / self.building_id / (key + '.jpg') if self.photos_dir else None
        self.map.add_photo(self.building_id, self.here, camera, self.namespace, q, thumb=thumb, compass=meta['compass'],
                           depth=meta['depth'], file=str(path.relative_to(self.photos_dir)) if path else None, source='seen')
        if path: self.files.append(path)
        self._reload()
        if self.trace: self.trace.emit('place_photo', spot=self.here[:8], bearing=round(camera), photos=len(self.by_spot.get(self.here, [])))

    def _believe(self, now):
        """The room he is in: his spot's, else the vote's."""
        spot = self.spots.get(self.here) if self.here else None
        if spot and spot.get('room_id') in self.rooms:
            room_id, how = spot['room_id'], spot['room_source']
            sure = how == 'told'
        elif self.vote:
            room_id, how, sure = self.vote['place_id'], 'looks', True
        else:
            room_id = None
        if room_id is None:
            self.current = None
            return
        if not self.current or self.current['place_id'] != room_id:
            self.current = {'place_id': room_id, 'name': self.rooms[room_id], 'since': now, 'sure': sure, 'how': how}
        else:
            self.current.update(sure=sure, how=how)

    # Deliberate looks from the drive owner -------------------------------------------------------
    def capture(self, cancel=None, wait=3.):
        """A reading of a fresh frame now (a look-around stop, an arrival check); returns where()."""
        after = self.clock()
        for _ in range(max(1, int(wait / .05))):
            frame = self.camera.hub.peek()
            if frame is not None and frame.at >= after:
                break
            if cancel is not None: cancel.check()
            time.sleep(.05)
        else:
            return self.where()
        try:
            self.observe(frame, deliberate=True)
        except ProviderFailure:
            raise
        except Exception as exc:
            if self.trace: self.trace.emit('place_reading_failed', error_type=type(exc).__name__, deliberate=True)
        return self.where()

    def thumb_now(self):
        """(tiny grey copy, neck degrees) of the newest frame, for steering along a passage."""
        frame = self.camera.hub.peek()
        if frame is None or not hasattr(self.encoder, 'thumbnail'):
            return None, None
        try:
            return self.encoder.thumbnail(frame), self._safe(self.neck, 0.)
        except Exception:
            return None, None

    # The drive owner's questions -------------------------------------------------------------------
    def where(self):
        with self.lock:
            spot = self.here
            room = self.current
            return {'building_id': self.building_id, 'spot': spot, 'facing': self._facing(spot) if spot else None,
                    'room_id': room['place_id'] if room else None, 'room_sure': bool(room and room['sure'])}

    def relative(self, spot_id, bearing):
        """How far to turn now (+left) to face bearing in spot's frame, or None without that frame."""
        with self.lock:
            facing = self._facing(spot_id)
            return None if facing is None else wrap(bearing - facing)

    def expect(self, spot_id, offset):
        """Dead reckoning after a passage: he should now be on spot_id with this frame offset."""
        with self.lock:
            pose = self._pose()
            if pose is not None and spot_id in self.spots:
                self.anchors[spot_id] = {'offset': wrap(offset), 'x': pose[0], 'y': pose[1], 'at': self.clock(),
                                         'expected': True}

    def offset(self, spot_id):
        with self.lock:
            return (self.anchors.get(spot_id) or {}).get('offset')

    def route_to(self, room_id):
        """How to get to a room from where he stands: here / passages / bearing / none (with why)."""
        with self.lock:
            if room_id not in self.rooms:
                return {'kind': 'none', 'reason': 'unknown_room'}
            spot = self.here
            if spot is None:
                return {'kind': 'none', 'reason': 'not_on_a_known_spot'}
            here = self.spots.get(spot) or {}
            if here.get('room_id') == room_id:
                return {'kind': 'here', 'spot': spot}
            goals = {k for k, s in self.spots.items() if s.get('room_id') == room_id}
            path = self.map.route(self.building_id, spot, goals) if goals else None
            if path:
                legs, at = [], spot
                for passage, back in path:
                    if back:
                        segments = reversed_segments(passage['segments'], passage['arrive'])
                        legs.append({'passage_id': passage['id'], 'from': passage['to_spot'], 'to': passage['from_spot'],
                                     'segments': segments, 'arrive': wrap(-passage['arrive']), 'marks': [],
                                     'doorways': passage['doorways']})
                    else:
                        legs.append({'passage_id': passage['id'], 'from': passage['from_spot'], 'to': passage['to_spot'],
                                     'segments': passage['segments'], 'arrive': passage['arrive'],
                                     'marks': self.map.marks(passage['id']), 'doorways': passage['doorways']})
                return {'kind': 'passages', 'legs': legs, 'goal': legs[-1]['to'], 'room_id': room_id}
            bearing = self.map.bearings(spot).get(room_id)
            if bearing is not None:
                return {'kind': 'bearing', 'spot': spot, 'bearing': bearing, 'room_id': room_id}
            return {'kind': 'none', 'reason': 'way_not_known'}

    def mapped(self, room_id):
        """Whether the map has anything that leads to this room: a spot in it or a direction to it."""
        with self.lock:
            if any(s.get('room_id') == room_id for s in self.spots.values()):
                return True
            return bool(self.store.rows('SELECT 1 FROM room_bearings b JOIN spots s ON s.id=b.spot_id '
                                        'WHERE b.room_id=? AND s.building_id=? LIMIT 1', (room_id, self.building_id)))

    def trip_started(self, room_id, how):
        """A trip someone asked for, toward room_id: where it ends is labelled for it until a person says."""
        with self.lock:
            self.goal = ({'room_id': room_id, 'name': self.rooms[room_id], 'how': how, 'at': self.clock()}
                         if room_id in self.rooms else None)
            self.arrived = None

    def trip_ended(self, ok):
        """The drive owner's go_to is over: a goal it reached waits for the stop; a failed one is dropped."""
        with self.lock:
            if self.goal is None:
                return
            if ok and self.trip is not None:
                self._close_trip()
            if not ok or self.trip is None or not self.trip['stopped']:
                self.goal = None   # Failed, or no trip long enough to have left the spot: nothing to label.
            else:
                self.goal['ended'] = True

    def passage_result(self, passage_id, ok):
        with self.lock:
            self.map.passage_result(passage_id, ok)

    def in_room(self, room_id):
        """On a spot a person named as this room (review, November 22: not the look-alike vote, which sees the
        room through its doorway)."""
        with self.lock:
            return bool(self.current and self.current['place_id'] == room_id and self.current['how'] == 'told')

    # Naming (called by the state owner after its transaction commits) -------------------------------
    def named(self, room_id, name, where, turn):
        """A person said what room this is ("here") or which way one lies (ahead/left/right/behind)."""
        with self.lock:
            self.rooms[room_id] = name
            moving = self.motion() and self.odometer is not None and self.odometer.still_for() < 1.
            spot = None if moving else self.here
            if spot is None and not moving:
                pose = self._pose()
                near = min(((math.hypot(pose[0] - a['x'], pose[1] - a['y']), k) for k, a in self.anchors.items()
                            if k in self.spots), default=None) if pose else None
                if near is not None and near[0] <= NEAR_M:
                    spot = near[1]
                    if self.trip is not None:
                        self._close_trip()
                    self.here = spot
                    self._arrive(spot, self.clock())
            if spot is None and not moving:
                best = max((k for k in self.belief if k is not None), key=self.belief.get, default=None)
                if best is not None and self.belief[best] >= .5 and self.trip is None:
                    spot = best
            if spot is None:
                pose = self._pose()
                if self.trip is not None and self.odometer is not None and self.odometer.still_for() >= 1.:
                    self._close_trip()
                if moving or pose is None or not self._settled():
                    self.pending = (room_id, where, turn)
                    self.last_named = {'name': name, 'outcome': 'when_he_stops' if moving else 'not_placed_yet'}
                    if self.vote is None and where == 'here' and not moving:
                        self._confirm(room_id, self.clock())
                    self._believe(self.clock())
                    return self.last_named
                self._new_spot(pose, self.clock())
                spot = self.here
            self._apply_name(room_id, where, turn, spot)
            self._believe(self.clock())
            return self.last_named

    def _apply_name(self, room_id, where, turn, spot):
        name = self.rooms.get(room_id)
        if where == 'here':
            self.map.label_spot(spot, room_id, 'told', turn)
            self.map.retire_legacy(room_id, LEGACY_RETIRE)
            self.arrived = None   # Answered, whichever room it turned out to be.
            self.last_named = {'name': name, 'outcome': 'this_spot', 'photos_here': len(self.by_spot.get(spot, []))}
        else:
            facing = self._facing(spot)
            if facing is None:
                self.last_named = {'name': name, 'outcome': 'direction_unknown'}
                return
            self.map.add_bearing(spot, room_id, wrap(facing + WHERE[where]), 'told', turn)
            self.last_named = {'name': name, 'outcome': 'that_way', 'where': where}
        self._reload()
        if self.trace: self.trace.emit('place_named', name=name, where=where, spot=spot[:8], outcome=self.last_named['outcome'])

    def reject(self, place_id=None):
        """"This isn't the hallway": the belief goes, and so does that label on this spot."""
        with self.lock:
            if not self.current or (place_id and place_id != self.current['place_id']): return False
            spot = self.spots.get(self.here) if self.here else None
            if spot and spot.get('room_id') == self.current['place_id']:
                self.map.unlabel_spot(spot['id'])
            if self.vote: self._leave(self.clock(), 'told_not_this_room')
            self.unknown_streak, self.unfamiliar_since, self.arrived = 0, None, None
            self._reload(); self.current = None
            return True

    def forgotten(self, place_id):
        with self.lock:
            if self.pending and self.pending[0] == place_id: self.pending = None
            if self.goal and self.goal['room_id'] == place_id: self.goal = None
            if self.previous and self.previous[0] == place_id: self.previous = None
            self.load()

    def place_named(self, building_id, name, action):
        """The state owner wrote the building's name; switching buildings resets where he stands."""
        with self.lock:
            if building_id != self.building_id:
                # Review, November 22: where the router confirmed the building, a person's other name holds for
                # this run but leaves the router with that building (a wrong tool choice must not move home's).
                confirmed = self.building_how == 'network'
                if self.router and not confirmed:
                    with self.store.transaction() as c:
                        c.execute('DELETE FROM building_networks WHERE network=?', (self.router,))
                self._settle(building_id, 'told', note_network=not confirmed)
            else:
                self.building_how = 'told'
            self._reload()

    def merge_into(self, source, target, how='told'):
        with self.lock:
            self.map.merge_building(source, target)
            self.building_id = None   # The provisional building is gone; settle afresh on the known one.
            self._settle(target, how)
            if self.trace: self.trace.emit('place_merged', into=target)

    # Descriptions -----------------------------------------------------------------------------------
    def _describe_one(self):
        """One hosted line for one photo, now and then, while the wheels are still (owner, November 22)."""
        if (self.describer is None or self.photos_dir is None or self.described >= DESCRIBE_PER_RUN
                or self.clock() - self.described_at < DESCRIBE_EVERY_S or self.motion() is not False):
            return
        self.described_at = self.clock()
        rows = self.map.undescribed() if self.map else []
        if not rows:
            return
        row = rows[0]
        if not (self.photos_dir / row['file']).is_file():
            self.map.describe(row['id'], '')   # Its JPEG was never written: nothing to describe, ever.
            return
        try:
            import base64
            from .scene import SceneImage
            data = (self.photos_dir / row['file']).read_bytes()
            image = SceneImage('room-photo:' + row['id'], self.clock(), 'data:image/jpeg;base64,' + base64.b64encode(data).decode(), 'low')
            from .contracts import Cancellation
            text = ' '.join(str(self.describer(image, Cancellation()).get('text') or '').split())[:160]
        except Exception as exc:
            self.described += 1
            self.map.describe(row['id'], '')   # Not tried again: one bad photo must not use every attempt.
            if self.trace: self.trace.emit('place_described', outcome='failed', error_type=type(exc).__name__)
            return
        self.described += 1
        self.map.describe(row['id'], text)
        if text:
            with self.lock: self._reload()
        if self.trace: self.trace.emit('place_described', outcome='ok' if text else 'empty')

    # Waking up somewhere else ------------------------------------------------------------------
    def _woke_check(self, q, now):
        """In the first minute: did he wake where he went to sleep, or was he moved while off?"""
        if self.slept is None or self.woke is not None: return
        best = max(dot(q, v) for v in self.slept['views'])
        if self.drove: verdict = 'drove'                     # He went somewhere himself: not a wake question.
        elif best >= VIEW_SAME: verdict = 'same_place'
        elif best < WOKE_MOVED and self.unlike + 1 >= WAKE_FRAMES: verdict = 'moved'
        elif now - self.run_clock > WAKE_WINDOW: verdict = 'unsure'
        else:
            self.unlike = self.unlike + 1 if best < WOKE_MOVED else 0
            return
        self.woke = {'verdict': verdict, 'best': round(best, 3), 'at': now}
        if self.trace:
            self.trace.emit('woke_up', verdict=verdict, best=round(best, 3), asleep_seconds=round(self.run_began - self.slept['ended_at']),
                            went_to_sleep_in=self.rooms.get(self.slept.get('room_id')))

    def woke_context(self):
        """Woken somewhere other than where he went to sleep, in words, for the first ten minutes; else None."""
        with self.lock:
            if not self.woke or self.woke['verdict'] != 'moved' or self.clock() - self.run_clock > WOKE_SHOWN: return None
            from .conversation_memory import gap
            out = {'woke_up': 'somewhere other than where you went to sleep, moved while you were off',
                   'went_to_sleep': gap(self.run_began - self.slept['ended_at'])}
            if self.rooms.get(self.slept.get('room_id')): out['went_to_sleep_in'] = self.rooms[self.slept['room_id']]
            if self.current: out['now_in'] = self.current['name']
            return out

    # Everyday views --------------------------------------------------------------------------
    def _note_view(self, q, now):
        """How many earlier days (this run excluded) held a view like this one; now and then keep a sample."""
        if now - self.view_checked < VIEW_CHECK: return
        self.view_checked = now
        wall = self.wall_clock(); today = day_of(wall)
        since = wall - VIEW_WINDOW_DAYS * 86400
        days, best, stored_today, kept_today = set(), 0., 0., 0
        for vector, day, at in self.views:
            if day == today and at >= self.run_began:
                kept_today += 1
                stored_today = max(stored_today, dot(q, vector))
                continue
            if at < since: continue
            similarity = dot(q, vector)
            best = max(best, similarity)
            if similarity >= VIEW_SAME: days.add(day)
        with self.store.transaction() as c:   # The last couple of minutes awake, for the next start.
            c.execute('INSERT INTO place_last_views VALUES(?,?,?,?,?)', (uuid4().hex, self.namespace, array('f', q).tobytes(),
                      wall, self.current['place_id'] if self.current else None))
            c.execute('DELETE FROM place_last_views WHERE namespace=? AND id NOT IN (SELECT id FROM place_last_views '
                      'WHERE namespace=? ORDER BY captured_at DESC LIMIT ?)', (self.namespace, self.namespace, LAST_VIEWS))
        before = self.view_kind()
        self.view = {'days': len(days), 'best': round(best, 3), 'at': now}
        if self.trace and self.view_kind() != before:
            self.trace.emit('view_familiarity', kind=self.view_kind(), days=len(days), best=round(best, 3))
        if wall - self.view_stored >= VIEW_SAMPLE and stored_today < VIEW_DUPLICATE and kept_today < VIEW_PER_DAY:
            self.view_stored = wall
            row = (uuid4().hex, self.namespace, array('f', q).tobytes(), today, wall)
            with self.store.transaction() as c:
                c.execute('INSERT INTO place_views VALUES(?,?,?,?,?)', row)
                c.execute('DELETE FROM place_views WHERE captured_at<?', (wall - VIEW_KEPT_DAYS * 86400,))
            self.views.append((array('f', q), today, wall))

    def view_kind(self):
        if self.view is None: return None
        if self.view['days'] >= VIEW_EVERYDAY_DAYS: return 'everyday'
        if self.view['days']: return 'seen_before'
        return 'new' if len({day for _, day, at in self.views if at < self.run_began}) >= VIEW_YOUNG_DAYS else None

    def view_context(self):
        """How familiar the view in front of him is, in words, or None (no recent check, or too little history)."""
        with self.lock:
            kind = self.view_kind()
            if kind is None or self.clock() - self.view['at'] > VIEW_FRESH: return None
            return {'this_view': kind, 'seen_on': f"{self.view['days']} of the last {VIEW_WINDOW_DAYS} days"}

    def tick(self):
        with self.lock:
            if self.arrived and self.clock() - self.arrived['at'] > 180.:
                self.arrived = None
            if self.goal and self.clock() - self.goal.get('at', self.clock()) > GOAL_KEEP_S:
                self.goal = None

    # Conversation context --------------------------------------------------------------------
    def context_status(self):
        if not self.started or self.failure: return STARTING
        building = self._building_row()
        if building and building['provisional'] and not building['name']: return NEW_PLACE
        if self.current: return RECOGNIZED if self.current['sure'] else UNSURE
        if not self.rooms: return NO_ROOMS
        return UNFAMILIAR if self.unfamiliar_since is not None else UNSURE

    def context(self):
        with self.lock:
            now = self.clock()
            status = self.context_status()
            building = self._building_row()
            room = self.current
            spot = self.here
            facing = self._facing(spot) if spot else None
            this_way = []
            if spot and facing is not None:
                for room_id, bearing in self.map.bearings(spot).items():
                    if room_id in self.rooms and not (room and room['place_id'] == room_id):
                        this_way.append({'room': self.rooms[room_id], 'room_id': room_id,
                                         'where': relative_words(bearing - facing)})
            reach = self.map.reachable(self.building_id, spot) if spot else set()
            rooms_on_route = {s['room_id'] for k, s in self.spots.items() if k in reach and s.get('room_id')}
            bearing_rooms = set(self.map.bearings(spot)) if spot else set()
            known = [{'room_id': k, 'name': v, 'way_known': k in rooms_on_route or k in bearing_rooms}
                     for k, v in sorted(self.rooms.items(), key=lambda kv: kv[1])][:24]
            photos_here = self.by_spot.get(spot, []) if spot else []
            result = {
                'status': status,
                'place': {'name': (building or {}).get('name'), 'never_seen_before': status == NEW_PLACE,
                          'known_places': [b['name'] for b in self.map.buildings() if b['name']][:8] if self.map else []},
                'room': {'room_id': room['place_id'], 'name': room['name'], 'sure': room['sure'],
                         'seconds_here': round(now - room['since'])} if room else None,
                'looks_like': [n for n, _ in (self.last_query or {}).get('top', [])] if status in (UNSURE, UNFAMILIAR) else [],
                'unfamiliar_seconds': round(now - self.unfamiliar_since) if self.unfamiliar_since is not None else None,
                'this_way': this_way[:6],
                'known_rooms': known,
                'last_named': self.last_named,
                'meaning': GUIDE['place']}
            descriptions = [p['description'] for p in photos_here if p.get('description')][:3]
            if descriptions:
                result['around_this_spot'] = descriptions
            if self.arrived:
                result['just_arrived'] = {'room': self.arrived['name'], 'room_id': self.arrived['room_id'],
                                          'ask_if_this_is_it': self.arrived['ask']}
            return result
