"""A pretend robot for trying the Rex Call app (or rexcall-probe) on a Mac with no robot. It runs the
robot's real code with no devices:

- a pretend supervisor: the real trigger listener and answer() on the call's port + 1, so the app's
  status, start and stop work. Rex is "asleep" until a start, unless --call-now;
- the real call loop, listener and video sender;
- the real HeadControl on the dashboard's simulated servo board, after the call's real wake (the
  drive stance and the visor up);
- the real DriveControl on a small simulated base with a wall 2 m ahead of where it starts.

    TELEPRESENCE_TOKEN=... .venv/bin/python tools/telepresence_fake_robot.py [--listen 127.0.0.1:47800] [--call-now]

The room is a quiet 220 Hz hum that swells every few seconds; the camera is a drawn picture showing the
head's position and how far the base has rolled. The caller's voice is measured, not played. Each call
ends as the robot's does (hang-up, nobody in 120 s, link gone 30 s, a stop) and the pretend supervisor
then waits for the next; Ctrl-C ends everything. Not the robot: no servos, wheels, camera or lights move.
"""
from pathlib import Path
import argparse
import json
import math
import sys
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from r3x.capture import Frame as AudioFrame  # noqa: E402
from r3x.settings import ScratchPaths, Secrets  # noqa: E402
from r3x.telepresence.call import Call, VideoStream  # noqa: E402
from r3x.telepresence.program import call_controls, controls_offer  # noqa: E402
from r3x.telepresence.protocol import parse_listen, parse_networks, valid_token  # noqa: E402
from r3x.telepresence.server import Server  # noqa: E402
from r3x.telepresence.trigger import Requests, TriggerListener, answer  # noqa: E402
from r3x.vision import Frame as VideoFrame  # noqa: E402

RATE = 16000
BLOCK = 512      # 32 ms, the robot microphone's block.
WIDTH, HEIGHT = 960, 540
WALL_M = 2.0     # A wall this far ahead of where the base starts.


class Room:
    """Stands in for the microphone: a paced hum, block by block."""
    dropped = 0

    def __init__(self):
        self.started = time.monotonic()
        self.samples = 0

    def read(self):
        due = self.started + self.samples / RATE
        wait = due - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        import array
        pcm = array.array('h')
        for i in range(BLOCK):
            t = (self.samples + i) / RATE
            swell = .5 + .5 * math.sin(2 * math.pi * t / 4)
            pcm.append(int(2500 * swell * math.sin(2 * math.pi * 220 * t)))
        began = self.started + self.samples / RATE
        self.samples += BLOCK
        return AudioFrame(pcm.tobytes(), began, began + BLOCK / RATE)


class Voice:
    """Stands in for the speaker: counts and measures what the caller sends."""
    def __init__(self):
        self.stats = {'underruns': 0, 'dropped_seconds': 0.}
        self.seconds = 0.
        self.peak = 0
        self.loud_seconds = 0.

    def write(self, pcm):
        import array
        samples = array.array('h', pcm)
        self.seconds += len(samples) / RATE
        if samples:
            peak = max(abs(v) for v in samples)
            self.peak = max(self.peak, peak)
            if peak > 1000:
                self.loud_seconds += len(samples) / RATE

    def clear(self):
        pass

    def buffered_seconds(self):
        return 0.


class Wheels:
    """A simulated base behind the real DriveControl: the drive owner's manual-joystick surface, a pose
    that integrates what it is sent, and a wall WALL_M ahead that holds forward motion at 0.4 m."""
    state = 'ready'

    def __init__(self):
        self.lock = threading.Lock()
        self.manual_active = False
        self.lin = self.ang = 0.
        self.x = self.y = self.heading = 0.
        self.at = time.monotonic()
        self.charging = False

    def _advance(self):
        now = time.monotonic()
        dt, self.at = now - self.at, now
        self.heading += self.ang * dt
        self.x += self.lin * math.cos(self.heading) * dt
        self.y += self.lin * math.sin(self.heading) * dt

    def front_m(self):
        ahead = math.cos(self.heading)
        return max(0., (WALL_M - self.x) / ahead) if ahead > .2 else 3.

    def begin_manual(self, *, stance_held=False, turns_on_charger=False):
        if self.charging and not turns_on_charger:
            return False, 'charging'
        self.manual_active = True
        return True, None

    def manual(self, lin, ang, cancel):
        with self.lock:
            if not self.manual_active:
                return {'status': 'refused', 'reason': 'manual_not_active'}
            self._advance()
            held = []
            if lin and self.charging:   # On the charger he only turns in place.
                held.append('charging')
                lin = 0.
            if lin > 0 and self.front_m() < .4:
                held.append('path_blocked')
                lin = 0.
            self.lin, self.ang = lin, ang
            return {'status': 'sent', 'lin': lin, 'ang': ang, 'held': held}

    def end_manual(self):
        with self.lock:
            self._advance()
            self.manual_active = False
            self.lin = self.ang = 0.

    def cancel(self):
        with self.lock:
            self._advance()
            self.lin = self.ang = 0.

    def motion_risk(self):
        return bool(self.lin or self.ang)

    def readings(self, now):
        with self.lock:
            self._advance()
            front = self.front_m()
        return {'connected': True, 'battery_percent': 82, 'charging': self.charging, 'volts': 12.6, 'blocked': None,
                'clearance_m': {'front_m': round(front, 2), 'path_m': round(front, 2), 'left_m': 1.2, 'right_m': 1.1,
                                'rear_m': .8},
                'sensor_notes': [], 'radar': []}


class Camera:
    """Stands in for the camera's frame hub: a new drawn picture on every look, showing the head and wheels."""
    def __init__(self, robot, wheels):
        self.robot, self.wheels, self.count = robot, wheels, 0

    @property
    def hub(self):
        return self

    def peek(self):
        import cv2
        import numpy as np
        from r3x.servo_poses import telepresence_reading
        head = telepresence_reading(dict(self.robot.owner.commanded))
        image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        image[:, :, 0] = np.linspace(40, 120, WIDTH, dtype=np.uint8)[None, :]
        image[:, :, 1] = np.linspace(20, 80, HEIGHT, dtype=np.uint8)[:, None]
        # Where the head points: a cross that moves with neck (left/right) and pitch (up/down).
        cx = int(WIDTH / 2 + (head['neck'] or 0) * WIDTH * .4)
        cy = int(HEIGHT / 2 - (head['pitch'] or 0) * HEIGHT * .4)
        cv2.line(image, (cx - 40, cy), (cx + 40, cy), (255, 0, 255), 4)
        cv2.line(image, (cx, cy - 40), (cx, cy + 40), (255, 0, 255), 4)
        fmt = lambda v: '-' if v is None else f'{v:+.2f}'
        lines = [f'fake robot  {time.strftime("%H:%M:%S")}  frame {self.count}',
                 'head  ' + '  '.join(f'{k} {fmt(v)}' for k, v in head.items()),
                 f'base  {self.wheels.x:+.2f} m ahead  heading {math.degrees(self.wheels.heading):+.0f} deg'
                 f'  {self.wheels.lin:.2f} m/s  wall {self.wheels.front_m():.2f} m']
        for i, text in enumerate(lines):
            cv2.putText(image, text, (24, 44 + 36 * i), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
        self.count += 1
        return VideoFrame(f'f{self.count}', 'fake', 'fake', time.monotonic(), WIDTH, HEIGHT, image.tobytes())


class Printer:
    def emit(self, event, **data):
        if event != 'telepresence_stats' or data.get('connected'):
            data.pop('link', None)
            print(f'{event} {json.dumps(data, default=str)}', flush=True)


def run_call(host, port, networks, token, trace, stop):
    """One call, as the robot's program runs it: the call's wake, then the call until it ends."""
    from r3x.gui.sim import SimulatedMaestro
    from r3x.robot_servos import RobotServos
    board = SimulatedMaestro()
    robot = RobotServos('simulation', open_port=lambda: board, telepresence=True)
    robot.trace = trace
    robot.start()   # The call's short wake: the drive stance, visor up.
    wheels = Wheels()
    video = VideoStream(Camera(robot, wheels))
    server = Server(host, port, networks, token, welcome=lambda: {
        'audio': {'rate': RATE, 'channels': 1, 'format': 's16le'}, 'fake': True,
        'video': {'width': WIDTH, 'height': HEIGHT, 'fps': 15, 'format': 'jpeg'},
        'controls': controls_offer(robot, wheels)})
    server.trace = video.trace = trace
    controls = call_controls({'robot': robot, 'drive': wheels, 'radar': None}, {'robot_servos', 'drive_base'})
    controls['base'] = wheels.readings
    voice = Voice()
    call = Call(server, Room(), voice, trace, video=video, **controls)
    video.start()
    server.start()
    server.open()   # Calls are taken only once the call is wired, as on the robot.
    reason = 'interrupted'
    try:
        reason = call.run(stop)
    finally:
        server.close()
        video.close()
        robot.close()   # Parks, the raised poker arm too.
    print(json.dumps({'call_ended': reason, 'voice_seconds_received': round(voice.seconds, 2),
                      'voice_peak': voice.peak, 'base_rolled_m': round(wheels.x, 2),
                      'head_stats': dict(controls['head'].stats), 'drive_stats': dict(controls['drive'].stats)}),
          flush=True)
    return reason




def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--listen', default='127.0.0.1:47800')
    parser.add_argument('--allow', action='append', default=[])
    parser.add_argument('--call-now', action='store_true', help='Start a call at once instead of waiting asleep')
    args = parser.parse_args()
    token = Secrets.load(ScratchPaths.for_project(PROJECT).root).telepresence_token
    if not valid_token(token):
        raise SystemExit('Set TELEPRESENCE_TOKEN (or telepresence_token in local/secrets.toml), 32+ characters')
    host, port = parse_listen(args.listen)
    networks = parse_networks(args.allow or ['127.0.0.1/32'])
    trace = Printer()
    state = {'call': None, 'last': None, 'stop': None}

    def launch():
        stop = threading.Event()   # Made now, so a stop during the wake still ends the call.
        state['stop'] = stop

        def body():
            reason = run_call(host, port, networks, token, trace, stop)
            state['last'] = {'reason': reason, 'code': 0, 'failed_starts': [], 'ended_at': time.time()}
        state['call'] = threading.Thread(target=body, name='fake-call', daemon=True)
        state['call'].start()

    def decide(request):
        running = state['call'] is not None and state['call'].is_alive()
        last = state['last']
        last_call = {**last, 'seconds_ago': round(time.time() - last['ended_at'])} if last else None
        reply, action = answer(request, running=running, call_child=running, call_port=port,
                               battery={'percent': 82, 'charging': False, 'volts': 12.6, 'seconds_ago': 2},
                               last_call=last_call)
        if action == 'launch':
            launch()
        elif action == 'stop' and state['stop'] is not None:
            state['stop'].set()
        print(f'supervisor {json.dumps(request)} -> {json.dumps(reply)}', flush=True)
        return reply

    requests = Requests()
    listener = TriggerListener(host, port + 1, networks, token, requests.ask,
                               log=lambda level, message, *a: print('supervisor ' + message % a, flush=True))
    listener.start()
    print(f'fake robot: calls on {host}:{port}, supervisor on {host}:{port + 1}; '
          + ('calling now' if args.call_now else 'asleep until a start'), flush=True)
    if args.call_now:
        launch()
    try:
        while True:
            requests.wake.wait(.2)
            requests.serve(decide)
    except KeyboardInterrupt:
        pass
    finally:
        if state['stop'] is not None:
            state['stop'].set()
        if state['call'] is not None:
            state['call'].join(15)
        listener.close()


if __name__ == '__main__':
    main()
