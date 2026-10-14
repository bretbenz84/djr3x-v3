"""Short-term memory of the floor around Rex: what the range sensors saw in the last minute.

October 14 improvement plan, step 3. Every drive decision used to read only the newest
sensor frame: a single phantom return stopped a trip, and each leg around a kitchen island
was stop, hosted look, decide, move. This memory keeps where returns were (and where the
sensors saw open floor) in the room frame of his own tracked position and IMU heading, for
about a minute, so that

- a sudden close reading where open floor was seen a moment ago, with no return there
  before, is rechecked instead of ending the trip (one that persists still stops him), and
- a way around something can come from floor the sensors already saw open, instead of a
  new hosted camera look for every leg.

It is deliberately short-lived: wheel travel drifts and the ring and depth sensors are
sparse, so this is not a floor plan, localization or a map of the house. Nothing here
moves the wheels; every leg still passes the usual clearance, swing and firmware checks.
"""
import math
import threading

from .drive_space import AXLE_AFT, RING_RADIUS, open_directions, wrap

CELL_M = .1             # Grid cell size.
MEMORY_S = 60.          # Observations older than this are forgotten.
REACH_M = 2.            # Only returns and open floor this near are remembered (sparse sensors).
OPEN_M = 3.5            # open_directions' "nothing within range".
OBSERVE_EVERY_S = .1    # At most this often.
# A close reading is doubtful when open floor was seen at that spot within RECENT_FREE_S by
# at least FREE_FRAMES frames and nothing returned there in the last RECENT_HIT_S before it.
RECENT_FREE_S = 3.
FREE_FRAMES = 2
RECENT_HIT_S = 10.
FRESHEST_S = .3         # The doubtful reading's own frames are not evidence against it.
BODY_HALF_M = .32       # Half the body plus the margin a path keeps (drive_space corridor).
CENTRE_SEEN_SHARE = .7  # A way counts as open when most of its centreline was seen open.


class LocalMap:
    def __init__(self, clock):
        self.clock = clock
        self.lock = threading.Lock()
        self.cells = {}          # (ix, iy) -> {'hit': [times], 'free': [times]}
        self.last_observed = float('-inf')

    @staticmethod
    def key(x, y):
        return (math.floor(x / CELL_M), math.floor(y / CELL_M))

    def _note(self, x, y, kind, at):
        cell = self.cells.setdefault(self.key(x, y), {'hit': [], 'free': []})
        times = cell[kind]
        if not times or at - times[-1] >= OBSERVE_EVERY_S / 2:
            times.append(at)
            if len(times) > 12:
                del times[:-12]

    def forget_old(self, now):
        for key in [k for k, cell in self.cells.items()
                    if max(cell['hit'][-1:] + cell['free'][-1:], default=float('-inf')) < now - MEMORY_S]:
            del self.cells[key]

    def observe(self, telemetry, matrix, position, heading, now):
        """One sensor frame. position: his tracked position (rear-axle frame); heading: degrees."""
        if heading is None or position is None or now - self.last_observed < OBSERVE_EVERY_S:
            return
        rows = open_directions(telemetry, matrix, now)
        if not rows:
            return
        with self.lock:
            self.last_observed = now
            cx = position[0] + AXLE_AFT * math.cos(math.radians(heading))
            cy = position[1] + AXLE_AFT * math.sin(math.radians(heading))
            for row in rows:
                angle = math.radians(heading + row['bearing_deg'])
                start = RING_RADIUS if row['sensor'] == 'ring' else RING_RADIUS * .8
                reach = row['open_m']
                hit = reach < OPEN_M and reach <= REACH_M
                free_to = min(reach - CELL_M, REACH_M) if hit else REACH_M
                step = CELL_M / 2
                d = start
                while d <= start + free_to:
                    self._note(cx + d * math.cos(angle), cy + d * math.sin(angle), 'free', now)
                    d += step
                if hit:
                    self._note(cx + (start + reach) * math.cos(angle), cy + (start + reach) * math.sin(angle), 'hit', now)
            self.forget_old(now)

    def doubtful(self, position, heading, bearing, distance, now):
        """Whether a close return at this bearing and distance (from the ring centre) contradicts
        what was just seen: open floor there in the last few seconds, no return there before."""
        if heading is None or position is None or distance is None:
            return False
        cx = position[0] + AXLE_AFT * math.cos(math.radians(heading))
        cy = position[1] + AXLE_AFT * math.sin(math.radians(heading))
        angle = math.radians(heading + bearing)
        x, y = cx + (RING_RADIUS + distance) * math.cos(angle), cy + (RING_RADIUS + distance) * math.sin(angle)
        ix, iy = self.key(x, y)
        with self.lock:
            near = [self.cells.get((ix + dx, iy + dy)) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]
            near = [c for c in near if c]
            free = sorted({t for c in near for t in c['free'] if now - RECENT_FREE_S <= t < now - FRESHEST_S})
            hits = [t for c in near for t in c['hit'] if now - RECENT_HIT_S <= t < now - FRESHEST_S]
        return len(free) >= FREE_FRAMES and not hits

    def open_way(self, position, heading, goal_bearing, length, now, *, side=None, spread=90., step=10.):
        """A direction (degrees relative to the heading, + left) toward the goal over floor the
        sensors saw open in the memory window, with no remembered return across the body; the
        one nearest the goal bearing, or None. side (+1 left, -1 right) keeps going round on the
        side already chosen: without it, legs zig-zagged either side of a counter."""
        if heading is None or position is None:
            return None
        goal = 0. if goal_bearing is None else goal_bearing
        signs = (side,) if side in (1, -1) else (1, -1)
        candidates = sorted((wrap(goal + sign * k * step) for k in range(int(spread / step) + 1) for sign in signs),
                            key=lambda b: abs(wrap(b - goal)))
        seen = set()
        with self.lock:
            for bearing in candidates:
                if round(bearing) in seen or abs(bearing) > 120:
                    continue
                seen.add(round(bearing))
                if self._corridor_open(position, heading, bearing, length, now):
                    return bearing
        return None

    def _corridor_open(self, position, heading, bearing, length, now):
        """Most of the centreline seen open lately and no remembered return anywhere across the
        body-wide corridor. The sensors are sparse rays, so the sides of the corridor may be
        unseen; the move that follows still checks its own clearance."""
        angle = math.radians(heading + bearing)
        ux, uy = math.cos(angle), math.sin(angle)
        px, py = -uy, ux
        cx = position[0] + AXLE_AFT * math.cos(math.radians(heading))
        cy = position[1] + AXLE_AFT * math.sin(math.radians(heading))
        seen = total = 0
        d = RING_RADIUS
        while d <= RING_RADIUS + length:
            for lateral in (-BODY_HALF_M, -BODY_HALF_M / 2, 0., BODY_HALF_M / 2, BODY_HALF_M):
                cell = self.cells.get(self.key(cx + d * ux + lateral * px, cy + d * uy + lateral * py))
                if cell and cell['hit'] and now - cell['hit'][-1] <= MEMORY_S:
                    return False
                if lateral == 0.:
                    total += 1
                    seen += bool(cell and cell['free'] and now - cell['free'][-1] <= MEMORY_S)
            d += CELL_M
        return total > 0 and seen / total >= CENTRE_SEEN_SHARE

    def summary(self, now):
        with self.lock:
            hits = sum(1 for c in self.cells.values() if c['hit'] and now - c['hit'][-1] <= MEMORY_S)
            return {'remembered_returns': hits, 'cells': len(self.cells)}
