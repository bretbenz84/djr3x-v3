"""Rex's map of each building: spots he has stood on, photos with bearings, and the ways between them.

Owner request, November 22 (docs/ROOM_NAVIGATION_PLAN.md): room-to-room navigation with no lidar, learned
from what happens anyway (no tour), rooms divided by direction in the open living/dining/kitchen space,
photos kept with bearing, compass and depth, a map per building told apart by router hardware, and trips
to other rooms only when someone asks.

A topological map, not a floor plan:

- **Building**: home, Dad's house, work. Rooms, spots, photos and passages belong to one building.
- **Spot**: a point on the floor where Rex stood still. Each spot keeps its own sense of direction (its
  frame): bearing 0 is wherever his base faced when the spot was first made. Matching a photo at the spot
  tells him which way he faces in that frame, so no global "north" has to stay right across runs.
- **Photo**: one look from a spot in one direction (camera bearing in the spot's frame), with the room
  model's fingerprint, a tiny grey copy for measuring small turns, the compass and depth readings, and a
  JPEG on his own disk under local/.
- **Room bearing**: from this spot, that room lies that way (told by a person, or learned from a trip).
- **Passage**: a way he actually travelled from one spot to another, as straight segments with headings in
  the start spot's frame, plus tiny grey copies along it to steer back onto the line he drove before.

Pure Python (the contract checks block native libraries). Nothing here moves the wheels or speaks.
"""
import json
import math
from array import array
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS buildings(
 id TEXT PRIMARY KEY, name TEXT, created_at REAL NOT NULL, source_turn TEXT,
 provisional INTEGER NOT NULL DEFAULT 0, adopt_network INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS building_networks(
 building_id TEXT NOT NULL REFERENCES buildings(id) ON DELETE CASCADE, network TEXT NOT NULL,
 first_at REAL NOT NULL, last_at REAL NOT NULL, PRIMARY KEY(building_id, network));
CREATE TABLE IF NOT EXISTS spots(
 id TEXT PRIMARY KEY, building_id TEXT NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
 room_id TEXT REFERENCES places(id) ON DELETE SET NULL, room_source TEXT, room_turn TEXT,
 created_at REAL NOT NULL, last_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS spots_building ON spots(building_id);
CREATE TABLE IF NOT EXISTS room_photos(
 id TEXT PRIMARY KEY, building_id TEXT NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
 spot_id TEXT REFERENCES spots(id) ON DELETE CASCADE, room_id TEXT REFERENCES places(id) ON DELETE CASCADE,
 bearing REAL, namespace TEXT NOT NULL, vector BLOB NOT NULL, thumb BLOB, compass TEXT, depth TEXT,
 file TEXT, description TEXT, captured_at REAL NOT NULL, source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS room_photos_spot ON room_photos(spot_id);
CREATE INDEX IF NOT EXISTS room_photos_building ON room_photos(building_id, namespace);
CREATE TABLE IF NOT EXISTS room_bearings(
 id TEXT PRIMARY KEY, spot_id TEXT NOT NULL REFERENCES spots(id) ON DELETE CASCADE,
 room_id TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE, bearing REAL NOT NULL,
 source TEXT NOT NULL, source_turn TEXT, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS room_bearings_spot ON room_bearings(spot_id);
CREATE TABLE IF NOT EXISTS passages(
 id TEXT PRIMARY KEY, building_id TEXT NOT NULL REFERENCES buildings(id) ON DELETE CASCADE,
 from_spot TEXT NOT NULL REFERENCES spots(id) ON DELETE CASCADE,
 to_spot TEXT NOT NULL REFERENCES spots(id) ON DELETE CASCADE,
 segments TEXT NOT NULL, arrive REAL NOT NULL, length_m REAL NOT NULL, doorways INTEGER NOT NULL,
 created_at REAL NOT NULL, last_at REAL NOT NULL, uses INTEGER NOT NULL DEFAULT 0,
 failures INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS passages_from ON passages(from_spot);
CREATE INDEX IF NOT EXISTS passages_to ON passages(to_spot);
CREATE TABLE IF NOT EXISTS passage_marks(
 passage_id TEXT NOT NULL REFERENCES passages(id) ON DELETE CASCADE, idx INTEGER NOT NULL,
 along_m REAL NOT NULL, bearing REAL NOT NULL, thumb BLOB NOT NULL, PRIMARY KEY(passage_id, idx));
"""

# What the hosted picture model is asked about each room photo (owner, November 22: the model describes the
# rooms' contents, linked to where and which way the photo was taken). Words for the map only: never names.
PHOTO_DESCRIBER = """You see one photo from a small home robot's head camera, taken from where it stood in a room.
Describe in one plain line (at most 20 words) what is there that would let someone recognise this view or this
room later: the main furniture and fixtures, a doorway or hall and where it is, and which kind of room it looks
like (kitchen, living room, bedroom, hallway, workshop...). No people, no names, no guesses about who lives
there, nothing about image quality."""

# Tiny grey copies (adapters.places.thumbnail): 48 columns across the ~90 degree view.
THUMB_W, THUMB_H = 48, 27
DEG_PER_COLUMN = 90. / THUMB_W
SHIFT_COLUMNS = 12            # About 22 degrees each way.
BUCKET_DEG = 45.              # Photos at a spot: one per 45 degrees (the lens sees 90, neighbours overlap).
SEGMENT_TOLERANCE_M = .15     # A recorded trail becomes straight segments within this.
MIN_SEGMENT_M = .2
MARK_EVERY_M = .4             # Tiny grey copies kept along a passage at most this often.
# A way out of a spot that has failed this many more times than it worked is not offered.
FAILURE_MARGIN = 2


def wrap(degrees):
    return (degrees + 180.) % 360. - 180.


def separation(a, b):
    return abs(wrap(a - b))


def circular_mean(angles, weights=None):
    weights = weights or [1.] * len(angles)
    x = sum(w * math.cos(math.radians(a)) for a, w in zip(angles, weights))
    y = sum(w * math.sin(math.radians(a)) for a, w in zip(angles, weights))
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    return math.degrees(math.atan2(y, x))


def relative_words(relative):
    """A base-relative bearing (+left) in words a person would use."""
    r = wrap(relative)
    if abs(r) <= 25: return 'ahead'
    if abs(r) >= 150: return 'behind you'
    side = 'left' if r > 0 else 'right'
    if abs(r) <= 65: return f'ahead to your {side}'
    if abs(r) <= 115: return f'to your {side}'
    return f'behind you to your {side}'


def vector_bytes(vector):
    return array('f', vector).tobytes()


def vector_from(blob):
    vector = array('f'); vector.frombytes(blob)
    return vector


def dot(a, b):
    return math.fsum(x * y for x, y in zip(a, b))


# Image shift (visual compass) -------------------------------------------------------------------------
def thumb_shift(current, stored, max_columns=SHIFT_COLUMNS):
    """How far the view turned between two tiny grey copies: (degrees, score).

    +degrees: the current view is turned LEFT of the stored one (things in it moved right). Zero-mean
    correlation of the overlapping columns at each horizontal shift; score is the best correlation
    (-1..1), so a poor match can be ignored. Krajník et al. 2018 and Dall'Osto et al. 2021 steer a taught
    route this way with tiny pictures. None for missing or malformed copies."""
    if not current or not stored or len(current) != len(stored) or len(current) != THUMB_W * THUMB_H:
        return None
    rows = [(current[r * THUMB_W:(r + 1) * THUMB_W], stored[r * THUMB_W:(r + 1) * THUMB_W]) for r in range(THUMB_H)]
    best = None
    for shift in range(-max_columns, max_columns + 1):
        # Current column c shows what the stored copy had at column c - shift.
        lo, hi = max(0, shift), min(THUMB_W, THUMB_W + shift)
        if hi - lo < THUMB_W // 2:
            continue
        sa = sb = saa = sbb = sab = 0.
        n = 0
        for a, b in rows:
            for c in range(lo, hi):
                x, y = a[c], b[c - shift]
                sa += x; sb += y; saa += x * x; sbb += y * y; sab += x * y
            n += hi - lo
        va, vb = saa - sa * sa / n, sbb - sb * sb / n
        if va <= 1e-6 or vb <= 1e-6:
            continue
        score = (sab - sa * sb / n) / math.sqrt(va * vb)
        if best is None or score > best[1]:
            best = (shift, score)
    if best is None:
        return None
    # Things moved right in the picture (positive column shift) when the camera turned left.
    return best[0] * DEG_PER_COLUMN, best[1]


# Trails ------------------------------------------------------------------------------------------------
def simplify(points, tolerance=SEGMENT_TOLERANCE_M):
    """Douglas-Peucker over (x, y) points: the corners of a driven trail."""
    if len(points) < 3:
        return list(points)
    (ax, ay), (bx, by) = points[0], points[-1]
    length = math.hypot(bx - ax, by - ay)
    worst, index = -1., 0
    for i, (px, py) in enumerate(points[1:-1], 1):
        if length < 1e-9:
            d = math.hypot(px - ax, py - ay)
        else:
            d = abs((bx - ax) * (ay - py) - (ax - px) * (by - ay)) / length
        if d > worst:
            worst, index = d, i
    if worst <= tolerance:
        return [points[0], points[-1]]
    return simplify(points[:index + 1], tolerance)[:-1] + simplify(points[index:], tolerance)


def segments_of(trail, offset):
    """A driven trail [(t, x, y, yaw)] as straight segments [{'heading', 'length_m'}] in a spot's frame.

    x, y are the run's own odometer (metres, IMU heading); offset turns run headings into the spot's frame.
    Headings are the direction travelled, so a stretch driven backward is repeated facing the way it went."""
    points = [(p[1], p[2]) for p in trail]
    corners = simplify(points)
    segments = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:]):
        length = math.hypot(bx - ax, by - ay)
        if length < MIN_SEGMENT_M:
            if segments:
                segments[-1]['length_m'] = round(segments[-1]['length_m'] + length, 2)
            continue
        heading = wrap(math.degrees(math.atan2(by - ay, bx - ax)) + offset)
        segments.append({'heading': round(heading, 1), 'length_m': round(length, 2)})
    return segments


def reversed_segments(segments, arrive):
    """The same passage driven back, in the far spot's frame (arrive: far frame minus near frame)."""
    return [{'heading': round(wrap(s['heading'] + 180. + arrive), 1), 'length_m': s['length_m']}
            for s in reversed(segments)]


def doorways_in(trail_sides, squeeze_m=.45, open_m=.8):
    """Doorways passed along a trail of (left_m, right_m): both sides close, then open again."""
    count, squeezed = 0, False
    for left, right in trail_sides:
        if left is None or right is None:
            continue
        if left <= squeeze_m and right <= squeeze_m:
            squeezed = True
        elif squeezed and (left >= open_m or right >= open_m):
            count, squeezed = count + 1, False
    return count


# Odometer ----------------------------------------------------------------------------------------------
class Odometer:
    """His position this run from wheel travel along the IMU heading, kept from every telemetry frame.

    The drive owner's own `_at` is folded only while a plan runs; this one also follows the gamepad and
    being pushed, so a trip driven by hand is a passage too. Run-local: it means nothing after a restart.
    The trail keeps a point every 5 cm or 5 degrees with the side clearances (doorways)."""

    TRAIL = 4000

    def __init__(self, clock):
        from collections import deque
        from threading import Lock
        self.clock = clock
        self.lock = Lock()
        self.x = self.y = 0.
        self.yaw = None
        self.mark = None
        self.moved_at = float('-inf')
        self.trail = deque(maxlen=self.TRAIL)   # (t, x, y, yaw, left_m, right_m)

    def update(self, telemetry, sides=(None, None)):
        imu = telemetry.get('imu') or {}
        yaw = float(imu['yaw']) if imu.get('ok') and isinstance(imu.get('yaw'), (int, float)) else None
        odom = telemetry.get('odom') or {}
        if yaw is None:
            theta = odom.get('theta')
            yaw = math.degrees(float(theta)) if isinstance(theta, (int, float)) else None
        if yaw is None:
            return
        xy = (float(odom.get('x', 0.)), float(odom.get('y', 0.)))
        now = self.clock()
        with self.lock:
            mark, self.mark = self.mark, xy
            turned = self.yaw is not None and separation(yaw, self.yaw) >= .5
            self.yaw = yaw
            if mark is not None:
                dx, dy = xy[0] - mark[0], xy[1] - mark[1]
                distance = math.hypot(dx, dy)
                if distance >= 1e-4:
                    theta = odom.get('theta')
                    if isinstance(theta, (int, float)) and dx * math.cos(theta) + dy * math.sin(theta) < 0:
                        distance = -distance
                    self.x += distance * math.cos(math.radians(yaw))
                    self.y += distance * math.sin(math.radians(yaw))
                    self.moved_at = now
                elif turned:
                    self.moved_at = now
            last = self.trail[-1] if self.trail else None
            if (last is None or math.hypot(self.x - last[1], self.y - last[2]) >= .05
                    or separation(yaw, last[3]) >= 5.):
                self.trail.append((now, self.x, self.y, yaw, sides[0], sides[1]))

    def pose(self):
        with self.lock:
            return None if self.yaw is None else (self.x, self.y, self.yaw)

    def since(self, t):
        with self.lock:
            return [p for p in self.trail if p[0] >= t]

    def still_for(self):
        return self.clock() - self.moved_at


# The store ---------------------------------------------------------------------------------------------
class RoomMap:
    """Reads and writes the map tables in the core store. Callers hold no lock of their own around it."""

    def __init__(self, store, wall_clock):
        self.store, self.wall_clock = store, wall_clock

    # Buildings
    def buildings(self):
        return self.store.rows('SELECT * FROM buildings ORDER BY created_at')

    def building(self, building_id):
        rows = self.store.rows('SELECT * FROM buildings WHERE id=?', (building_id,))
        return rows[0] if rows else None

    def buildings_on(self, network):
        return [r['building_id'] for r in self.store.rows('SELECT building_id FROM building_networks WHERE network=?', (network,))]

    def add_building(self, name=None, provisional=False, source_turn=None):
        key = uuid4().hex
        with self.store.transaction() as c:
            c.execute('INSERT INTO buildings(id,name,created_at,source_turn,provisional,adopt_network) VALUES(?,?,?,?,?,0)',
                      (key, name, self.wall_clock(), source_turn, int(provisional)))
        return key

    def note_network(self, building_id, network):
        now = self.wall_clock()
        with self.store.transaction() as c:
            c.execute('INSERT INTO building_networks VALUES(?,?,?,?) ON CONFLICT(building_id,network) DO UPDATE SET '
                      'last_at=excluded.last_at', (building_id, network, now, now))
            c.execute('UPDATE buildings SET adopt_network=0 WHERE id=?', (building_id,))

    def merge_building(self, source, target):
        """A provisional building turned out to be a known one: its rooms, spots and photos move over."""
        with self.store.transaction() as c:
            names = {r[1].lower(): r[0] for r in c.execute('SELECT id,name FROM places WHERE building_id=?', (target,))}
            for room_id, name in c.execute('SELECT id,name FROM places WHERE building_id=?', (source,)).fetchall():
                same = names.get(name.lower())
                if same:
                    for table in ('spots', 'room_photos', 'room_bearings'):
                        c.execute(f'UPDATE {table} SET room_id=? WHERE room_id=?', (same, room_id))
                    c.execute('DELETE FROM places WHERE id=?', (room_id,))
                else:
                    c.execute('UPDATE places SET building_id=? WHERE id=?', (target, room_id))
            for table in ('spots', 'room_photos', 'passages'):
                c.execute(f'UPDATE {table} SET building_id=? WHERE building_id=?', (target, source))
            c.execute('UPDATE OR IGNORE building_networks SET building_id=? WHERE building_id=?', (target, source))
            c.execute('DELETE FROM buildings WHERE id=?', (source,))

    # Spots
    def add_spot(self, building_id):
        key, now = uuid4().hex, self.wall_clock()
        with self.store.transaction() as c:
            c.execute('INSERT INTO spots VALUES(?,?,NULL,NULL,NULL,?,?)', (key, building_id, now, now))
        return key

    def spots(self, building_id):
        return self.store.rows('SELECT * FROM spots WHERE building_id=?', (building_id,))

    def spot(self, spot_id):
        rows = self.store.rows('SELECT * FROM spots WHERE id=?', (spot_id,))
        return rows[0] if rows else None

    def touch_spot(self, spot_id):
        with self.store.transaction() as c:
            c.execute('UPDATE spots SET last_at=? WHERE id=?', (self.wall_clock(), spot_id))

    def label_spot(self, spot_id, room_id, source, turn=None):
        """A person's words outrank a trip's guess, which outranks a look-alike; never the other way."""
        rank = {'told': 3, 'trip': 2, 'seen': 1, None: 0}
        with self.store.transaction() as c:
            row = c.execute('SELECT room_id,room_source FROM spots WHERE id=?', (spot_id,)).fetchone()
            if row is None:
                return False
            if source != 'told' and row[0] and rank.get(row[1], 0) >= rank[source] and row[0] != room_id:
                return False
            c.execute('UPDATE spots SET room_id=?,room_source=?,room_turn=? WHERE id=?', (room_id, source, turn, spot_id))
        return True

    def unlabel_spot(self, spot_id):
        with self.store.transaction() as c:
            c.execute('UPDATE spots SET room_id=NULL,room_source=NULL,room_turn=NULL WHERE id=?', (spot_id,))

    def merge_spot(self, source, target, delta):
        """Two spots turned out to be one place: source's photos, bearings and passages join target,
        with bearings turned by delta (target frame minus source frame)."""
        with self.store.transaction() as c:
            for key, bearing in c.execute('SELECT id,bearing FROM room_photos WHERE spot_id=?', (source,)).fetchall():
                c.execute('UPDATE room_photos SET spot_id=?,bearing=? WHERE id=?',
                          (target, None if bearing is None else round(wrap(bearing + delta), 1), key))
            for key, bearing in c.execute('SELECT id,bearing FROM room_bearings WHERE spot_id=?', (source,)).fetchall():
                c.execute('UPDATE room_bearings SET spot_id=?,bearing=? WHERE id=?', (target, wrap(bearing + delta), key))
            for key, segments, arrive in c.execute('SELECT id,segments,arrive FROM passages WHERE from_spot=?', (source,)).fetchall():
                turned = [{**s, 'heading': round(wrap(s['heading'] + delta), 1)} for s in json.loads(segments)]
                c.execute('UPDATE passages SET from_spot=?,segments=?,arrive=? WHERE id=?',
                          (target, json.dumps(turned), wrap(arrive - delta), key))
                for idx, bearing in c.execute('SELECT idx,bearing FROM passage_marks WHERE passage_id=?', (key,)).fetchall():
                    c.execute('UPDATE passage_marks SET bearing=? WHERE passage_id=? AND idx=?',
                              (round(wrap(bearing + delta), 1), key, idx))
            for key, arrive in c.execute('SELECT id,arrive FROM passages WHERE to_spot=?', (source,)).fetchall():
                c.execute('UPDATE passages SET to_spot=?,arrive=? WHERE id=?', (target, wrap(arrive + delta), key))
            c.execute('DELETE FROM passages WHERE from_spot=to_spot')
            here = c.execute('SELECT room_id,room_source FROM spots WHERE id=?', (target,)).fetchone()
            other = c.execute('SELECT room_id,room_source FROM spots WHERE id=?', (source,)).fetchone()
            if here and other and not here[0] and other[0]:
                c.execute('UPDATE spots SET room_id=?,room_source=? WHERE id=?', (other[0], other[1], target))
            c.execute('DELETE FROM spots WHERE id=?', (source,))

    # Photos
    def add_photo(self, building_id, spot_id, bearing, namespace, vector, *, thumb=None, compass=None, depth=None,
                  file=None, source='seen', room_id=None, at=None):
        key = uuid4().hex
        with self.store.transaction() as c:
            c.execute('INSERT INTO room_photos VALUES(?,?,?,?,?,?,?,?,?,?,?,NULL,?,?)',
                      (key, building_id, spot_id, room_id, None if bearing is None else round(wrap(bearing), 1), namespace,
                       vector_bytes(vector), bytes(thumb) if thumb else None,
                       json.dumps(compass) if compass else None, json.dumps(depth) if depth else None, file,
                       self.wall_clock() if at is None else at, source))
            if spot_id:
                c.execute('UPDATE spots SET last_at=? WHERE id=?', (self.wall_clock(), spot_id))
        return key

    def photos(self, building_id, namespace):
        """Every photo of a building, with the room it shows: its spot's room, or its own (older views)."""
        rows = self.store.rows(
            'SELECT p.id,p.spot_id,p.bearing,p.vector,p.thumb,p.depth,p.captured_at,p.source,p.description,p.file,'
            'COALESCE(s.room_id,p.room_id) AS room_id, s.room_source AS room_source '
            'FROM room_photos p LEFT JOIN spots s ON s.id=p.spot_id WHERE p.building_id=? AND p.namespace=?',
            (building_id, namespace))
        for r in rows:
            r['vector'] = vector_from(r['vector'])
            r['depth'] = json.loads(r['depth']) if r['depth'] else None
        return [r for r in rows if len(r['vector']) == 512]

    def all_photo_vectors(self, namespace):
        """(building_id, vector, captured_at) of every photo: which building a view belongs to, and familiarity."""
        out = []
        for r in self.store.rows('SELECT building_id,vector,captured_at FROM room_photos WHERE namespace=?', (namespace,)):
            v = vector_from(r['vector'])
            if len(v) == 512:
                out.append((r['building_id'], v, r['captured_at']))
        return out

    def undescribed(self, limit=1):
        return self.store.rows('SELECT id,file FROM room_photos WHERE description IS NULL AND file IS NOT NULL '
                               'ORDER BY captured_at LIMIT ?', (limit,))

    def describe(self, photo_id, text):
        with self.store.transaction() as c:
            c.execute('UPDATE room_photos SET description=? WHERE id=?', (text, photo_id))

    def retire_legacy(self, room_id, keep_from=8):
        """A room with this many of its own spot photos no longer needs the older loose views."""
        with self.store.transaction() as c:
            own = c.execute('SELECT COUNT(*) FROM room_photos p JOIN spots s ON s.id=p.spot_id '
                            "WHERE s.room_id=? AND s.room_source='told'", (room_id,)).fetchone()[0]
            if own >= keep_from:
                c.execute("DELETE FROM room_photos WHERE room_id=? AND spot_id IS NULL AND source='legacy'", (room_id,))

    # Room bearings
    def add_bearing(self, spot_id, room_id, bearing, source, turn=None):
        with self.store.transaction() as c:
            c.execute('INSERT INTO room_bearings VALUES(?,?,?,?,?,?,?)',
                      (uuid4().hex, spot_id, room_id, round(wrap(bearing), 1), source, turn, self.wall_clock()))
            # The latest few say where it is; a person's word outlives older guesses (owner: trust the person).
            rows = c.execute('SELECT id,source FROM room_bearings WHERE spot_id=? AND room_id=? ORDER BY created_at DESC',
                             (spot_id, room_id)).fetchall()
            stale = [r[0] for r in rows[4:]]
            if source == 'told':
                stale += [r[0] for r in rows[1:4] if r[1] != 'told']
            c.executemany('DELETE FROM room_bearings WHERE id=?', [(k,) for k in stale])

    def bearings(self, spot_id):
        """{room_id: bearing in the spot's frame}: the latest told one, else the mean of the latest few."""
        by_room = {}
        for r in self.store.rows('SELECT room_id,bearing,source FROM room_bearings WHERE spot_id=? ORDER BY created_at DESC',
                                 (spot_id,)):
            by_room.setdefault(r['room_id'], []).append(r)
        out = {}
        for room_id, rows in by_room.items():
            told = [r['bearing'] for r in rows if r['source'] == 'told']
            out[room_id] = told[0] if told else circular_mean([r['bearing'] for r in rows])
        return {k: v for k, v in out.items() if v is not None}

    def drop_bearings(self, spot_id, room_id):
        with self.store.transaction() as c:
            c.execute('DELETE FROM room_bearings WHERE spot_id=? AND room_id=?', (spot_id, room_id))

    # Passages
    def add_passage(self, building_id, from_spot, to_spot, segments, arrive, doorways, marks, source):
        key, now = uuid4().hex, self.wall_clock()
        length = round(sum(s['length_m'] for s in segments), 2)
        with self.store.transaction() as c:
            c.execute('INSERT INTO passages VALUES(?,?,?,?,?,?,?,?,?,?,0,0,?)',
                      (key, building_id, from_spot, to_spot, json.dumps(segments), round(wrap(arrive), 1), length,
                       doorways, now, now, source))
            c.executemany('INSERT INTO passage_marks VALUES(?,?,?,?,?)',
                          [(key, i, round(m['along_m'], 2), round(wrap(m['bearing']), 1), bytes(m['thumb']))
                           for i, m in enumerate(marks)])
            # Keep the newest three per direction: a way driven again replaces older, rougher ones.
            old = c.execute('SELECT id FROM passages WHERE from_spot=? AND to_spot=? ORDER BY created_at DESC LIMIT -1 OFFSET 3',
                            (from_spot, to_spot)).fetchall()
            c.executemany('DELETE FROM passages WHERE id=?', old)
        return key

    def passages(self, building_id):
        rows = self.store.rows('SELECT id,from_spot,to_spot,segments,arrive,length_m,doorways,uses,failures,last_at '
                               'FROM passages WHERE building_id=?', (building_id,))
        for r in rows:
            r['segments'] = json.loads(r['segments'])
        return rows

    def marks(self, passage_id):
        return self.store.rows('SELECT idx,along_m,bearing,thumb FROM passage_marks WHERE passage_id=? ORDER BY idx',
                               (passage_id,))

    def passage_result(self, passage_id, ok):
        with self.store.transaction() as c:
            column = 'uses' if ok else 'failures'
            c.execute(f'UPDATE passages SET {column}={column}+1, last_at=? WHERE id=?', (self.wall_clock(), passage_id))

    # Routes
    def route(self, building_id, start, goal_spots):
        """The shortest chain of passages from start to any goal spot: [(passage, reversed)], or None.

        Both directions of a passage count; driven back, its tiny copies do not apply (the camera faces the
        other way). A passage that failed clearly more often than it worked is left out."""
        if start in goal_spots:
            return []
        edges = {}
        for p in self.passages(building_id):
            if p['failures'] - p['uses'] >= FAILURE_MARGIN:
                continue
            cost = p['length_m'] + 2. * p['failures']
            edges.setdefault(p['from_spot'], []).append((p['to_spot'], cost, p, False))
            edges.setdefault(p['to_spot'], []).append((p['from_spot'], cost * 1.15, p, True))
        import heapq
        best = {start: 0.}
        queue = [(0., 0, start, [])]
        counter = 1
        while queue:
            cost, _, spot, path = heapq.heappop(queue)
            if spot in goal_spots:
                return path
            if cost > best.get(spot, float('inf')):
                continue
            for target, step, passage, back in edges.get(spot, ()):
                total = cost + step
                if total < best.get(target, float('inf')):
                    best[target] = total
                    heapq.heappush(queue, (total, counter, target, path + [(passage, back)]))
                    counter += 1
        return None

    def reachable(self, building_id, start):
        """Spot ids a chain of passages reaches from start (start included)."""
        edges = {}
        for p in self.passages(building_id):
            if p['failures'] - p['uses'] >= FAILURE_MARGIN:
                continue
            edges.setdefault(p['from_spot'], set()).add(p['to_spot'])
            edges.setdefault(p['to_spot'], set()).add(p['from_spot'])
        seen, todo = {start}, [start]
        while todo:
            for nxt in edges.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt); todo.append(nxt)
        return seen
