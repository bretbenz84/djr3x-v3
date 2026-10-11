"""Which room Rex is in: visual place recognition, room naming and which rooms connect.

Ported from V2 `perception/place_recognition.py` (see V2_REFERENCE.md), keeping its measured
thresholds and field repairs: a confident call must beat the runner-up room by a margin, a
lone known room needs a much stricter score, the belief changes only after a majority of
recent frames agree and only after the base has moved (unless the camera insists for a long
time: he was carried), the gallery grows only from frames that clearly belong to the believed
room, and a person's "this isn't the kitchen" outranks any score.

This is the one owner of room evidence (galleries, belief, room-to-room transitions), as the
speaker tracker owns voice evidence. It never speaks or interprets language: the conversation
model asks what room this is and names it through the state owner's `name_room` tool, which
calls `learn()`. V2 left doorways, positions, maps and navigation out of scope; so does this,
apart from remembering which known rooms Rex has moved between.
"""
from array import array
from collections import Counter, deque
import math
from operator import mul
from threading import Event, RLock, Thread
import time
from uuid import uuid4

from .adapters.places import NAMESPACE
from .contracts import ProviderFailure
from .providers import Capability

SCHEMA = """
CREATE TABLE IF NOT EXISTS places(
 id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL, source_turn TEXT, import_ref TEXT);
CREATE TABLE IF NOT EXISTS place_embeddings(
 id TEXT PRIMARY KEY, place_id TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE,
 namespace TEXT NOT NULL, vector BLOB NOT NULL, heading REAL, captured_at REAL NOT NULL, source TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS place_embeddings_place ON place_embeddings(place_id, namespace);
CREATE TABLE IF NOT EXISTS place_links(
 a TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE, b TEXT NOT NULL REFERENCES places(id) ON DELETE CASCADE,
 transitions INTEGER NOT NULL, last_at REAL NOT NULL, PRIMARY KEY(a, b));
"""

# V2 config values (PLACE_*), measured on this robot in July 2026.
QUERY_INTERVAL = 1.5
SETTLED_INTERVAL = 6.       # Recognized, wheels still, not learning: nothing new to see; spare the CPU.
TOPK = 3
CONFIDENT = .80
MINIMUM = .68
MARGIN = .04
SOLO_CONFIDENT = .86        # 2026-07-25: another room in the house scored .75-.82 against a lone gallery.
HYSTERESIS = 5
UNKNOWN_STREAK = 8          # Unrecognized frames after moving before the room counts as unfamiliar.
LOST_STREAK = 16            # ... while believing a room: drop the belief rather than insist.
STATIC_FLIP_STREAK = 10     # Confident votes for another room with no motion signal: he was carried.
OCCLUSION_FACE_WIDTH = .25  # V2 skipped frames with a person covering >35% of the view; V3 has faces, not
                            # bodies, so a face a quarter of the frame wide stands in for that.
ENROLL_TARGET = 8
ENROLL_MIN = 3
ENROLL_HEADING_SEP = 35.
ENROLL_TIME_SEP = 3.
ENROLL_TIMEOUT = 60.
DUPLICATE = .88
REFRESH = (.70, .78)
MAX_EMBEDDINGS = 15
LINK_WINDOW = 15 * 60.      # Two rooms count as connected when he goes from one to the other this quickly.

RECOGNIZED, UNSURE, UNFAMILIAR, LEARNING, NO_ROOMS, STARTING = (
    'recognized', 'unsure', 'unfamiliar', 'learning', 'no_rooms_known', 'starting')


def unit(values):
    """Plain-Python unit vector (the contract checks run without native libraries)."""
    vector = array('f', values)
    norm = math.sqrt(math.fsum(x * x for x in vector))
    if vector.__len__() != 512 or not math.isfinite(norm) or norm < 1e-9: raise ValueError('InvalidPlaceVector')
    return array('f', (x / norm for x in vector))


def dot(a, b):
    return math.fsum(map(mul, a, b))


def _separation(a, b):
    d = abs((a - b) % 360.)
    return min(d, 360. - d)


class Places:
    def __init__(self, camera, encoder, store, *, trace=None, motion=None, heading=None,
                 clock=time.monotonic, wall_clock=time.time):
        self.camera, self.encoder, self.store, self.trace = camera, encoder, store, trace
        # motion() -> True/False, or None with no trustworthy base signal (disables the freeze gate).
        self.motion = motion or (lambda: None)
        self.heading = heading or (lambda: None)
        self.clock, self.wall_clock = clock, wall_clock
        self.namespace = NAMESPACE
        self.capability = Capability('places', encoder.capability.provider, 'which known room Rex is in and how rooms connect',
                                     encoder.capability.model, encoder.capability.dependencies,
                                     ('one shared frame; one embedding every 1.5 s; <=15 views per room',), required=False)
        self.lock = RLock(); self.stop = Event(); self.worker = None; self.failure = None
        self.rooms = {}              # place_id -> name
        self.vectors = []            # (embedding_id, place_id, vector, captured_at)
        self.history = deque(maxlen=HYSTERESIS)
        self.current = None          # {'place_id','name','score','since'}
        self.previous = None         # (place_id, left_at) of the last room believed, for connections
        self.moved = False
        self.unknown_streak = 0
        self.unfamiliar_since = None
        self.flip = (None, 0)
        self.last_query = None
        self.learning = None         # {'place_id','name','started','vectors','headings','last','created'}
        self.last_learning = None    # outcome of the last enrollment, for an honest reply
        self.started = False

    # Lifecycle ---------------------------------------------------------------------------
    def start(self):
        self.encoder.start()
        self.load()
        self.camera.hub.register('places')
        self.worker = Thread(target=self._run, name='r3x-places', daemon=True); self.worker.start()

    def _run(self):
        last = None
        try:
            while not self.stop.is_set():
                began = self.clock(); frame = self.camera.hub.take('places', last)
                if frame:
                    last = frame.frame_id; self.observe(frame)
                self.tick()
                with self.lock:
                    settled = self.current is not None and not self.learning and self.motion() is False
                self.stop.wait(max(.05, (SETTLED_INTERVAL if settled else QUERY_INTERVAL) - (self.clock() - began)))
        except Exception as exc:
            self.failure = type(exc).__name__
            if self.trace: self.trace.emit('provider_failed', capability='places', error_type=self.failure)
            self.cancel()

    def note_motion(self, *_):
        """The base started a move (drive owner callback): a room change is now possible."""
        with self.lock: self.moved = True

    def cancel(self): self.stop.set()

    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(5)
            if self.worker.is_alive(): raise ProviderFailure('PlaceWorkerDidNotStop')
        self.encoder.close()

    # Gallery -----------------------------------------------------------------------------
    def load(self):
        with self.lock:
            self.rooms = {r['id']: r['name'] for r in self.store.rows('SELECT id,name FROM places')}
            self.vectors = []
            for r in self.store.rows('SELECT id,place_id,vector,captured_at FROM place_embeddings '
                                     'WHERE namespace=? ORDER BY captured_at', (self.namespace,)):
                vector = array('f'); vector.frombytes(r['vector'])
                if len(vector) == 512: self.vectors.append((r['id'], r['place_id'], vector, r['captured_at']))
            if self.current and self.current['place_id'] not in self.rooms:
                self.current = None; self.history.clear()
            self.started = True

    def _scores(self, q):
        """Each room scores the mean of its TOPK best view similarities (V2 PLACE_TOPK)."""
        by_room = {}
        for _, pid, vector, _ in self.vectors:
            by_room.setdefault(pid, []).append(dot(q, vector))
        scores = [(pid, self.rooms[pid], sum(sorted(sims)[-TOPK:]) / min(TOPK, len(sims)))
                  for pid, sims in by_room.items() if pid in self.rooms]
        return sorted(scores, key=lambda s: s[2], reverse=True)

    @staticmethod
    def classify(scores):
        if not scores or scores[0][2] < MINIMUM: return UNFAMILIAR
        if len(scores) == 1: return RECOGNIZED if scores[0][2] >= SOLO_CONFIDENT else UNSURE
        return RECOGNIZED if scores[0][2] >= CONFIDENT and scores[0][2] - scores[1][2] >= MARGIN else UNSURE

    def _store_vector(self, place_id, vector, heading, source, conn=None):
        key = uuid4().hex
        row = (key, place_id, self.namespace, array('f', vector).tobytes(), heading, self.wall_clock(), source)
        if conn is None:
            with self.store.transaction() as c: c.execute('INSERT INTO place_embeddings VALUES(?,?,?,?,?,?,?)', row)
        else:
            conn.execute('INSERT INTO place_embeddings VALUES(?,?,?,?,?,?,?)', row)
        # Oldest views beyond the cap are pruned (V2 PLACE_MAX_EMBEDDINGS).
        with self.store.transaction() as c:
            stale = c.execute('SELECT id FROM place_embeddings WHERE place_id=? AND namespace=? ORDER BY captured_at DESC '
                              'LIMIT -1 OFFSET ?', (place_id, self.namespace, MAX_EMBEDDINGS)).fetchall()
            c.executemany('DELETE FROM place_embeddings WHERE id=?', [(r[0],) for r in stale])

    # Observation ---------------------------------------------------------------------------
    def occluded(self, now):
        faces = self.camera.latest(now) if hasattr(self.camera, 'latest') else ()
        return any((f.get('box') or [0, 0, 0, 0])[2] > OCCLUSION_FACE_WIDTH for f in faces or ())

    def observe(self, frame):
        if frame.session_id != getattr(self.camera, 'session_id', frame.session_id) or not 0 <= self.clock() - frame.at <= 1:
            return None
        now = self.clock()
        if self.occluded(now):
            if self.trace: self.trace.emit('place_skipped', reason='person_close')
            return None
        with self.lock: learning = self.learning
        heading = self.heading()
        if learning and not self._diverse(learning, heading, now):
            return None
        vector = unit(self.encoder.embed(frame))        # Slow work outside the lock.
        with self.lock:
            if learning:
                if self.learning is learning and self._diverse(learning, heading, now):
                    learning['vectors'].append(vector); learning['headings'].append(heading); learning['last'] = now
                    if len(learning['vectors']) >= ENROLL_TARGET: self._finish_learning(now, 'target')
                return None
            return self._query(vector, now, heading)

    def _diverse(self, learning, heading, now):
        if heading is not None and not any(_separation(heading, h) < ENROLL_HEADING_SEP for h in learning['headings'] if h is not None):
            return True
        return learning['last'] is None or now - learning['last'] >= ENROLL_TIME_SEP

    def _query(self, q, now, heading):
        moving = self.motion()
        if moving is None or moving: self.moved = True
        scores = self._scores(q)
        status = self.classify(scores)
        self.last_query = {'at': now, 'status': status, 'top': [(n, round(s, 3)) for _, n, s in scores[:2]]}
        if self.trace: self.trace.emit('place_sample', status=status, top=self.last_query['top'])
        # Grow only the believed room, only when it is also clearly the top match (V2 field repairs).
        if (self.current and len(scores) >= 2 and scores[0][0] == self.current['place_id'] and
                REFRESH[0] <= scores[0][2] <= REFRESH[1] and scores[0][2] - scores[1][2] >= MARGIN):
            self._store_vector(scores[0][0], q, heading, 'refresh'); self.load()
        self._update(scores, status, now)
        return status

    def _update(self, scores, status, now):
        confident = status == RECOGNIZED
        self.history.append(scores[0][0] if confident else None)
        votes = [p for p in self.history if p]
        attempted = False
        if votes:
            candidate, n = Counter(votes).most_common(1)[0]
            current = self.current['place_id'] if self.current else None
            if n >= HYSTERESIS // 2 + 1 and candidate != current:
                if self.current is None or self.moved:
                    self._confirm(candidate, now)
                else:
                    attempted = True
                    pid, streak = self.flip
                    self.flip = (candidate, streak + 1 if pid == candidate else 1)
                    if self.flip[1] >= STATIC_FLIP_STREAK:
                        self.moved = True; self._confirm(candidate, now, link=False)   # Carried: not a doorway.
        if not attempted: self.flip = (None, 0)
        if status == UNFAMILIAR:
            self.unknown_streak += 1
            if self.current and self.unknown_streak >= LOST_STREAK:
                self._leave(now, 'lost')
            if self.unknown_streak >= UNKNOWN_STREAK and self.moved and self.unfamiliar_since is None and self.current is None:
                self.unfamiliar_since = now
                if self.trace: self.trace.emit('place_unfamiliar', streak=self.unknown_streak)
        else:
            self.unknown_streak = 0
            if confident: self.unfamiliar_since = None

    def _confirm(self, place_id, now, link=True):
        name = self.rooms.get(place_id)
        # The room he came from: the one believed until now, or one he left (lost) recently.
        came_from = (self.current['place_id'] if self.current else
                     self.previous[0] if self.previous and now - self.previous[1] <= LINK_WINDOW else None)
        if link and came_from and came_from != place_id and self.moved:
            self._link(came_from, place_id)
        self.current = {'place_id': place_id, 'name': name, 'since': now}
        self.previous = None
        self.moved, self.unknown_streak, self.unfamiliar_since, self.flip = False, 0, None, (None, 0)
        self.history.clear()
        if self.trace: self.trace.emit('place_confirmed', place_id=place_id, name=name)

    def _leave(self, now, reason):
        if self.current: self.previous = (self.current['place_id'], now)
        self.current = None; self.history.clear(); self.moved = True; self.flip = (None, 0)
        if self.trace: self.trace.emit('place_left', reason=reason)

    def _link(self, a, b):
        a, b = sorted((a, b))
        try:
            with self.store.transaction() as c:
                c.execute('INSERT INTO place_links VALUES(?,?,1,?) ON CONFLICT(a,b) DO UPDATE SET '
                          'transitions=transitions+1,last_at=excluded.last_at', (a, b, self.wall_clock()))
        except Exception as exc:   # A room forgotten mid-move; the link is not worth a failure.
            if self.trace: self.trace.emit('place_link_failed', error_type=type(exc).__name__)

    # Learning a room -------------------------------------------------------------------------
    def tick(self):
        with self.lock:
            if self.learning and self.clock() - self.learning['started'] >= ENROLL_TIMEOUT:
                self._finish_learning(self.clock(), 'timeout')

    def learn(self, place_id, name, created):
        """Start collecting views for a room the state owner has just named (row already written)."""
        with self.lock:
            self.rooms[place_id] = name
            self.learning = {'place_id': place_id, 'name': name, 'started': self.clock(), 'vectors': [],
                             'headings': [], 'last': None, 'created': created}
            self.last_learning = None

    def _finish_learning(self, now, reason):
        session, self.learning = self.learning, None
        if session is None: return
        if len(session['vectors']) < ENROLL_MIN:
            if session['created']:
                with self.store.transaction() as c:
                    if not c.execute('SELECT 1 FROM place_embeddings WHERE place_id=?', (session['place_id'],)).fetchone():
                        c.execute('DELETE FROM places WHERE id=?', (session['place_id'],))
            self.last_learning = {'name': session['name'], 'outcome': 'failed', 'views': len(session['vectors']), 'reason': reason}
            if self.trace: self.trace.emit('place_learning', **self.last_learning)
            self.load(); return
        similar = None
        pairs = [(dot(new, old), pid) for new in session['vectors'] for _, pid, old, _ in self.vectors
                 if pid != session['place_id']]
        if pairs and max(pairs)[0] > DUPLICATE:
            # V2 kept a told room as its own even when it resembled another; say so instead.
            similar = {'name': self.rooms.get(max(pairs)[1]), 'similarity': round(max(pairs)[0], 3)}
        with self.store.transaction() as c:
            if not c.execute('SELECT 1 FROM places WHERE id=?', (session['place_id'],)).fetchone():
                self.last_learning = {'name': session['name'], 'outcome': 'forgotten_meanwhile'}
                self.load(); return
        for vector, heading in zip(session['vectors'], session['headings']):
            self._store_vector(session['place_id'], vector, heading, 'taught')
        self.last_learning = {'name': session['name'], 'outcome': 'learned', 'views': len(session['vectors']),
                              **({'looks_like': similar} if similar else {})}
        if self.trace: self.trace.emit('place_learning', **self.last_learning)
        self.load()
        # He was just told where he is: adopt it (V2 did the same after enrollment).
        self._confirm(session['place_id'], now)

    # Human corrections (called by the state owner after its transaction commits) ----------
    def reject(self, place_id=None):
        with self.lock:
            if not self.current or (place_id and place_id != self.current['place_id']): return False
            self._leave(self.clock(), 'told_not_this_room')
            self.unknown_streak, self.unfamiliar_since = 0, None
            return True

    def forgotten(self, place_id):
        with self.lock:
            if self.learning and self.learning['place_id'] == place_id: self.learning = None
            if self.previous and self.previous[0] == place_id: self.previous = None
            self.load()

    # Conversation context --------------------------------------------------------------------
    def context(self):
        with self.lock:
            now = self.clock()
            status = (STARTING if not self.started or self.failure else LEARNING if self.learning else
                      RECOGNIZED if self.current else NO_ROOMS if not self.rooms else
                      UNFAMILIAR if self.unfamiliar_since is not None else UNSURE)
            room = self.current
            neighbors = []
            if room:
                rows = self.store.rows('SELECT a,b,transitions FROM place_links WHERE a=? OR b=? ORDER BY transitions DESC',
                                       (room['place_id'], room['place_id']))
                neighbors = [self.rooms.get(r['b'] if r['a'] == room['place_id'] else r['a']) for r in rows][:6]
            links = self.store.rows('SELECT a,b,transitions FROM place_links ORDER BY transitions DESC LIMIT 20')
            result = {
                'status': status,
                'room': {'room_id': room['place_id'], 'name': room['name'], 'seconds_here': round(now - room['since'])} if room else None,
                'looks_like': [n for n, _ in (self.last_query or {}).get('top', [])] if status == UNSURE else [],
                'unfamiliar_seconds': round(now - self.unfamiliar_since) if self.unfamiliar_since is not None else None,
                'learning_room': self.learning['name'] if self.learning else None,
                'last_learning': self.last_learning,
                'known_rooms': [{'room_id': k, 'name': v} for k, v in sorted(self.rooms.items(), key=lambda kv: kv[1])][:24],
                'connects_to': [n for n in neighbors if n],
                'connections': [[self.rooms.get(r['a']), self.rooms.get(r['b'])] for r in links
                                if r['a'] in self.rooms and r['b'] in self.rooms],
                'meaning': ('Room recognition from the camera, not a map or a position. recognized: Rex knows this room. '
                            'unsure: it may be one of looks_like; say so if it matters, never claim it. unfamiliar: Rex has '
                            'moved and does not recognize the room. Unfamiliar, or unsure for a while: when people are here and '
                            'it fits, ask once what room this is (a guess from looks_like is fine; do not ask again if it went '
                            'unanswered) and use name_room when told, including a confirmation such as "yes, the workshop". learning: views '
                            'of a just-named room are being collected; it is not learned until last_learning says learned. '
                            'connects_to/connections: rooms Rex has gone between, not distances or directions.')}
            return result
