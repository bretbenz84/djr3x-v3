"""Display-only body for the dashboard when no servo controller is configured.

V2's GUI_SERVO_SIM equivalent: the droid in the window moves as the robot would (wake and
park choreography, idle life, the head cock while he thinks, talking sway), because V3's
own servo owner and motion generators drive a software Maestro. Nothing here reaches the
conversation: Rex is not given this body (no pose tools, no motion flags on speech, no
trace rows), so he never believes he moved. The feed only shows it.
"""
from __future__ import annotations

import queue
import threading
import time

from ..contracts import Cancellation
from ..servo_poses import UNPOWERED_REST

FRAME_SECONDS = .05   # The runtime's own servo tick runs once per 32 ms audio frame; close enough.
# Speed 0 is the Maestro's "no limit", but a servo still takes time to get there: about 0.15 s
# per 60 degrees for hobby servos, roughly this many quarter-microseconds a second. An estimate.
PHYSICAL_UNITS_PER_SECOND = 15000.


class SimulatedMaestro:
    """Maestro compact protocol in software. Targets are approached at the commanded speed
    (quarter-microseconds per 10 ms; 0 = unlimited) and acceleration (speed units per 80 ms),
    so arrival takes as long as on the board and the owner's arrival checks pace the lanes."""

    def __init__(self, pose=None, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.position = {ch: float(v) for ch, v in dict(pose or UNPOWERED_REST).items()}
        self.target = dict(self.position)
        self.speed, self.accel, self.velocity = {}, {}, {}
        self.channel = 0
        self.targeted = set()   # Channels given a target; the rest may be seeded from readback.
        self.last = clock()
        self.closed = False

    def _advance(self):
        now = self.clock()
        dt, self.last = max(0., now - self.last), now
        for ch, target in self.target.items():
            here = self.position.get(ch, target)
            distance = target - here
            if not distance:
                self.velocity[ch] = 0.
                continue
            limit = self.speed.get(ch, 0)
            cap = min(limit * dt / .01 if limit else float('inf'), PHYSICAL_UNITS_PER_SECOND * dt)
            accel = self.accel.get(ch, 0)
            if accel:
                rate = accel / .08 / .01            # Units per second, per second.
                speed = min(cap / dt if dt else 0., self.velocity.get(ch, 0.) + rate * dt,
                            (2 * rate * abs(distance)) ** .5)   # Ease in and out, as the board ramps.
                step = max(min(speed * dt, cap), min(abs(distance), 1.))
                self.velocity[ch] = speed
            else:
                step = cap
            self.position[ch] = target if abs(distance) <= step else here + step * (1 if distance > 0 else -1)

    def write(self, packet):
        with self.lock:
            self._advance()
            command = packet[0]
            if command == 0x90:
                self.channel = packet[1]
            elif command in (0x84, 0x87, 0x89):
                channel, value = packet[1], packet[2] + 128 * packet[3]
                {0x84: self.target, 0x87: self.speed, 0x89: self.accel}[command][channel] = value
                if command == 0x84:
                    self.targeted.add(channel)
            elif command == 0x9F:
                for i in range(packet[1]):
                    self.target[packet[2] + i] = packet[3 + 2*i] + 128 * packet[4 + 2*i]
                    self.targeted.add(packet[2] + i)
        return len(packet)

    def read(self, count):
        with self.lock:
            self._advance()
            return int(round(self.position.get(self.channel, 0))).to_bytes(2, 'little')

    def positions(self):
        """Every channel's current output pulse, as the board would read back now."""
        with self.lock:
            self._advance()
            return {ch: int(round(v)) for ch, v in self.position.items()}

    def seed(self, pulses):
        """Start untargeted channels where the owner read them (a mirror joining late)."""
        with self.lock:
            for ch, value in pulses.items():
                if ch not in self.targeted:
                    self.position[ch] = self.target[ch] = float(value)

    def close(self):
        self.closed = True


class SimulatedBody:
    """RobotServos on a SimulatedMaestro, ticked like the runtime ticks the real one.

    The feed posts what happened (a heard turn, speech starting and ending, shutdown);
    this worker applies it, so the trace emitter never waits on servo work."""

    def __init__(self, clock=time.monotonic, faces=None):
        from ..robot_servos import RobotServos
        self.port = SimulatedMaestro(clock=clock)
        self.robot = RobotServos('simulation', open_port=lambda: self.port, clock=clock)
        self.faces = faces            # () -> current face rows, to aim like the real robot
        self.events = queue.Queue()
        self.thread = None
        self.speaking = self.thinking = False
        self.state = 'new'

    def start(self):
        self.thread = threading.Thread(target=self._run, name='r3x-gui-body', daemon=True)
        self.thread.start()

    def post(self, event, turn_id=None):
        self.events.put((event, turn_id))

    def _aim(self):
        from ..gaze import addressee_neck, face_aim
        from ..faces import VISIBLE_FACE_MAX_AGE
        owner = self.robot.owner
        def faces():
            now = time.monotonic()
            return [f for f in (self.faces() if self.faces else ()) if 0 <= now - f['at'] <= VISIBLE_FACE_MAX_AGE
                    and f.get('box')]
        self.robot.idle.face = lambda: face_aim(faces(), owner.neck_degrees())
        self.robot.talking.gaze = lambda: addressee_neck(faces(), owner.neck_degrees(), None, None)

    def _run(self):
        robot = self.robot
        try:
            if self.faces:
                self._aim()
            self.state = 'starting'
            robot.start()   # The wake choreography, in real time, as on the robot.
            self.state = 'active'
            while True:
                try:
                    event, turn_id = self.events.get(timeout=FRAME_SECONDS)
                except queue.Empty:
                    event = None
                if event == 'shutdown':
                    break
                if event == 'heard':
                    self.thinking = True
                    robot.heard()
                elif event == 'thinking':
                    self.thinking = True
                elif event == 'speaking' and not self.speaking:
                    self.speaking = True
                    robot.talking.start(turn_id or 'reply', Cancellation())
                elif event == 'done':
                    self.speaking = self.thinking = False
                    robot.talking.stop()
                robot.before_frame(allow_idle=not self.speaking, calm=self.thinking and not self.speaking)
        except Exception:
            self.state = 'failed'
            return
        self.state = 'parking'
        try:
            robot.close()   # Park choreography: visor down, head lowered.
        except Exception:
            pass
        self.state = 'closed'

    def close(self, wait=0.):
        self.post('shutdown')
        if wait and self.thread is not None:
            self.thread.join(wait)
