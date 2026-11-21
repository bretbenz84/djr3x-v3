"""When the throttle hand closes on what it is given, and opens to give it back (owner, November 20).

"Can you hold this?" reaches the hand out with its fingers open (servo_poses.HAND_POSES) while he says
only "Okay, ready.", and the fingers close by themselves CLOSE_AFTER_S later (owner, November 20: Bret
stood holding the book out through a whole sentence; "after 4 seconds the hand closes, no need to speak
more"). Told sooner that it is in, the model closes it at once (hand_grip); told they missed it, hand_take
opens it and reaches out again. Giving it back, HandWatch watches for someone coming to take it: the
front-right range reading (the arm reaches out at his front right) coming in close and nearer than when
the hand arrived, or a face that was not close at the start coming close. Neither the arm itself, already
in the sensor's view, nor a person who was already standing near can open it: each is measured against
how things were once the hand had settled. Nothing here moves a servo; RobotServos acts.
"""
from dataclasses import dataclass, field
import time

# The extended hand needs 0.5 m clear at the front right (drive_space.THROTTLE_EXTEND_CLEAR_M);
# something nearer than this has come within the arm's reach.
NEAR_M = .45
DROP_M = .10          # ...and at least this much nearer than the settled reading.
# ...for this long, so one flicker does not close the hand. Review, November 20: the frame loop looks
# about thirty times a second and the wheel board reports ten times a second, so "two readings in a
# row" were often one report read twice. This spans at least two reports.
NEAR_S = .15
# Faces in 1280-pixel frames: about 140 px at conversational distance in saved runs (median of
# 1627 sightings, November 20), 230 px at the 99th percentile. A face this wide is leaning in.
FACE_NEAR_PX = 300
FACE_LOOKS = 2        # Looks by the camera in a row (about two a second) that show it, not one read again.
SETTLE_S = .6         # After the hand arrives, readings for this long set the baseline.
CLOSE_AFTER_S = 4.    # The open hand closes this long after it is out and he is looking at it.
WAIT_S = 30.          # Held out to give back and nobody takes it in this long: the arm folds back in.
LET_GO_S = 2.5        # After the fingers open to let go, the arm rests this much later.
GRIP_SETTLE_S = 1.     # After the fingers' pulse arrives, they finish closing on it before the arm moves.


@dataclass
class HandWatch:
    """The wait while it is held out to give back: observe() returns 'grip' (someone is at the hand: open it),
    'timeout' or None."""
    opened_at: float
    baseline_m: float | None = None
    baseline_face_px: float = 0.
    near_since: float | None = None
    face_looks: int = 0
    _face_seen: float | None = None
    _settling: list = field(default_factory=list)

    def observe(self, now: float, front_m: float | None, face_px: float | None,
                face_at: float | None = None) -> str | None:
        """face_at: when the camera took the look that gave face_px (None: this very reading)."""
        if now - self.opened_at >= WAIT_S:
            return 'timeout'
        if now - self.opened_at < SETTLE_S:
            if front_m is not None:
                self._settling.append(front_m)
            self.baseline_face_px = max(self.baseline_face_px, face_px or 0.)
            return None
        if self.baseline_m is None and self._settling:
            self.baseline_m = min(self._settling)
        if face_px and face_px >= FACE_NEAR_PX > self.baseline_face_px:
            seen = now if face_at is None else face_at
            if seen != self._face_seen:
                self._face_seen, self.face_looks = seen, self.face_looks + 1
        else:
            self._face_seen, self.face_looks = None, 0
        if self.face_looks >= FACE_LOOKS:
            return 'grip'
        close = (front_m is not None and front_m < NEAR_M
                 and (self.baseline_m is None or front_m <= self.baseline_m - DROP_M))
        if not close:
            self.near_since = None
        elif self.near_since is None:
            self.near_since = now
        return 'grip' if close and now - self.near_since >= NEAR_S else None


# Whether the throttle hand holds something, kept across launches (owner, November 20: "so he doesn't
# drop" it). One row; while it says holding, the wake, the board's first pulses, idle and the shutdown
# park all leave the fingers closed and the arm folded, until he gives it back or puts it down.
SCHEMA = """CREATE TABLE IF NOT EXISTS held_item(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    holding INTEGER NOT NULL CHECK(holding IN (0, 1)),
    changed_at REAL NOT NULL,
    reason TEXT NOT NULL);"""


def held(store) -> bool:
    rows = store.rows("SELECT holding FROM held_item WHERE id = 1")
    return bool(rows and rows[0]['holding'])


def set_held(store, holding: bool, reason: str, at: float | None = None) -> None:
    with store.transaction() as db:
        db.execute("INSERT INTO held_item(id, holding, changed_at, reason) VALUES(1, ?, ?, ?) "
                   "ON CONFLICT(id) DO UPDATE SET holding = excluded.holding, changed_at = excluded.changed_at, "
                   "reason = excluded.reason", (int(bool(holding)), time.time() if at is None else at, reason[:80]))
