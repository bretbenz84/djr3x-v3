"""Rex says when a depth sensor goes dark and when it comes back (owner, November 15, 2026).

"Speak out loud when an 8x8 array fails using pre-canned text, no LLM required, only if the
failure lasts a few seconds or more, and then have him speak if it recovers."

The two 8x8 depth sensors (front and rear of the base) stream frames through the drive
controller. A sensor is lost when its frames stop while the controller itself still reports.
The lines are the code's own, saved as audio like the out-of-credit notices; no model is
asked. This module only decides when a line is due. The runtime says it at an idle moment and
confirms with said(), so a line that was not heard is tried again."""
from dataclasses import dataclass
import random

FRESH_SECONDS = 1.       # A frame this recent means the sensor is streaming (drive_space.clearance).
FAIL_AFTER = 4.          # Lost this long before he says so: shorter drop-outs pass in silence.
BACK_AFTER = 1.5         # Streaming again this long before he says it is back.
REPEAT_AFTER = 30.       # A sensor that keeps dropping is announced at most this often.

LINES = {
    ('failed', ('front',)): (
        "Uh oh. My front depth sensor just went dark, so my wheels are locked. "
        "Somebody check its little reset button.",
        "Whoa, my front sensor's out. I don't drive blind anymore; Star Tours made me promise. "
        "Check the reset button on my front sensor.",
        "Bad news from down low: my front depth sensor quit. I'm parked until it wakes up. "
        "Its reset button might be getting squished.",
    ),
    ('failed', ('rear',)): (
        "Heads up: my rear depth sensor just dropped out. No backing up until it's back.",
        "My rear sensor's gone dark. Reverse is off the menu, which, given my record, "
        "is probably for the best.",
    ),
    ('failed', ('front', 'rear')): (
        "Well, that's new: both my depth sensors just went dark, front and back. "
        "My wheels are locked until they wake up.",
        "Double trouble: my front and rear depth sensors are both out. I'm staying right here. "
        "Check their cables and reset buttons.",
    ),
    ('recovered', ('front',)): (
        "And we're back! My front sensor's online and the wheels are unlocked. "
        "Nobody panic. I certainly didn't.",
        "Front depth sensor's back online. Wheels unlocked. The furniture may resume being nervous.",
    ),
    ('recovered', ('rear',)): (
        "My rear sensor's back. Reverse is on the menu again.",
        "My rear depth sensor just woke up. I can back up without the suspense now.",
    ),
    ('recovered', ('front', 'rear')): (
        "Both depth sensors are back online, front and rear. Wheels unlocked. Let's roll.",
        "And we're back! Front and rear sensors both reporting. "
        "I'm cleared for takeoff, loosely speaking.",
    ),
}
SENSORS = ('front', 'rear')


def canned_lines() -> tuple[str, ...]:
    """Every line, to be synthesized once ahead of need (StartupAudio.warm)."""
    return tuple(line for lines in LINES.values() for line in lines)


@dataclass(frozen=True)
class Notice:
    kind: str                 # 'failed' or 'recovered'
    sensors: tuple            # ('front',), ('rear',) or ('front', 'rear')


class _Sensor:
    def __init__(self):
        self.seen = False               # Streamed at least once this run.
        self.lost_since = None
        self.fresh_since = None
        self.announced = False          # He has said it is out and not yet that it is back.
        self.failed_said_at = float('-inf')


class DepthSensorWatch:
    """Which notice is due, from how long ago each sensor last sent a frame."""
    def __init__(self, choose=random.choice):
        self.choose = choose
        self._sensors = {name: _Sensor() for name in SENSORS}
        self._reporting_since = None    # When the controller's current spell of reporting was first seen.

    def due(self, now: float, ages) -> Notice | None:
        """ages: {'front': seconds since its last frame or None (never), 'rear': likewise}, or None
        while the drive controller itself is not reporting (then nothing is concluded)."""
        if ages is None:
            self._reporting_since = None
            for sensor in self._sensors.values():
                sensor.fresh_since = None
                if not sensor.announced:
                    sensor.lost_since = None
            return None
        if self._reporting_since is None:
            self._reporting_since = now
        for name, sensor in self._sensors.items():
            age = ages.get(name)
            if age is not None and 0 <= age <= FRESH_SECONDS:
                sensor.seen, sensor.lost_since = True, None
                if sensor.fresh_since is None:
                    sensor.fresh_since = now
            elif name == 'rear' and not sensor.seen:
                continue   # Never seen this run: not fitted, as far as anyone can tell.
            else:
                sensor.fresh_since = None
                if sensor.lost_since is None:
                    # From its last frame, not from this look: he may have been mid-sentence while
                    # it went dark. Never from before the controller itself was reporting.
                    stopped = now if age is None else now - (age - FRESH_SECONDS)
                    sensor.lost_since = max(self._reporting_since, stopped)
        # Both usually drop together (one cable pair) or seconds apart: one line for both, so a
        # sensor already on its way to a notice makes the other wait for it.
        failing = [name for name, s in self._sensors.items() if not s.announced and s.lost_since is not None
                   and now - s.failed_said_at >= REPEAT_AFTER]
        if failing and all(now - self._sensors[name].lost_since >= FAIL_AFTER for name in failing):
            return Notice('failed', tuple(failing))
        returning = [name for name, s in self._sensors.items() if s.announced and s.fresh_since is not None]
        if returning and all(now - self._sensors[name].fresh_since >= BACK_AFTER for name in returning):
            return Notice('recovered', tuple(returning))
        return None

    def line(self, notice: Notice) -> str:
        return self.choose(LINES[(notice.kind, notice.sensors)])

    def said(self, notice: Notice, now: float) -> None:
        """The notice was heard."""
        for name in notice.sensors:
            sensor = self._sensors[name]
            if notice.kind == 'failed':
                sensor.announced, sensor.failed_said_at = True, now
            else:
                sensor.announced = False
