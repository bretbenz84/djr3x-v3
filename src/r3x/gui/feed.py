"""Qt-free dashboard feed: trace rows in, polled snapshots out (V2 gui/state_bridge.py).

The feed observes; it never decides. Conversation lines come from the same trace the
terminal shows, live readings from the owners' own snapshots (frame mailbox peek, face
and object rows, commanded pulses, LED commands written, wheel telemetry). The dashboard's
buttons go to the existing owners: stop and shutdown through the runtime, manual servo
poses through the servo owner's operator mode, the joystick through the drive owner's
teleop. Blocking owner calls run on the feed's two worker threads, never on Qt's.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import threading
import time
from uuid import uuid4

from ..contracts import Cancellation
from ..terminal import clean
from .lights import LightMirror

TRANSCRIPT_LINES = 300   # V2 GUI_CONVERSATION_LOG_MAX_LINES
LOG_LINES = 600          # V2 GUI_LOG_PANEL_MAX_LINES
SETPOINT_STALE = .3      # A joystick setpoint older than this (frozen window) sends zero.
QUIET_LOG_EVENTS = {'resources', 'idle_motion_summary', 'scene_sample', 'object_sample', 'place_sample',
                    'direction_sample', 'camera_frame', 'camera_duplicate_frame', 'audio_input_health'}


@dataclass
class LiveParts:
    """References App.live hands over once composition is done (any may be None)."""
    runtime: object = None
    settings: object = None
    owner: object = None       # ProviderOwner, for start results
    memory: object = None
    camera: object = None
    objects: object = None
    places: object = None
    robot: object = None
    leds: object = None
    drive: object = None
    radar: object = None
    sink: object = None
    log_path: str | None = None


def _level(event):
    if event.endswith('_failed') or event in {'worker_stuck', 'input_overflow', 'led_fault', 'model_stream_error'}:
        return 'ERROR'
    if any(word in event for word in ('rejected', 'refused', 'cancel', 'invalid', 'gap', 'waiting', 'skipped')):
        return 'WARNING'
    return 'INFO'


def _log_text(row):
    fields = ' '.join(f'{k}={clean(v)[:120]}' for k, v in row.items()
                      if k not in {'session_id', 'at', 'event', 'report'} and v not in (None, '', [], {}))
    return (row['event'] + ' ' + fields).strip()


class DashboardFeed:
    def __init__(self, console=None, clock=time.monotonic, wall=time.time):
        self.console, self.clock, self.wall = console, clock, wall
        self.lock = threading.RLock()
        self.parts = LiveParts()
        self.lights = LightMirror(clock)
        self.status = 'booting'    # booting | online | failed | stopping | stopped
        self.activity = 'starting'
        self.lines: list[dict] = []
        self.logs = deque(maxlen=LOG_LINES)
        self.seq = 0
        self.revision = 0
        self.turn_lines: dict[str, dict] = {}   # turn_id -> human line (speaker label arrives later)
        self.reply_lines: dict[str, dict] = {}  # turn_id -> Rex line growing phrase by phrase
        self.goal = None
        self.speaking = False
        self.pcm = (b'', 0.)
        self.names: dict[str, str] = {}
        self.names_at = float('-inf')
        self.room, self.room_at = None, float('-inf')
        self.frames = deque(maxlen=30)          # (received, frame_id) for a measured camera rate
        self.on_closed = []                     # Qt asks to close the window once Rex stopped.
        self.body = None                        # SimulatedBody when no servo controller is configured.
        self.ramps = None                       # SimulatedMaestro replaying the real robot's packets.
        self.shutdown_pending = False           # Asked to stop before the conversation was up.
        self.hooked = set()                     # Owners whose observer hooks are installed.
        self.servos = OperatorServos(self)
        self.teleop = Teleop(self)

    # Trace display ---------------------------------------------------------
    def __call__(self, row):
        if self.console:
            self.console(row)
        try:
            self.observe(row)
        except Exception:
            pass   # The dashboard must never break the trace.

    def observe(self, row):
        event = row['event']
        with self.lock:
            if event not in QUIET_LOG_EVENTS:
                self.seq += 1
                self.logs.append({'seq': self.seq, 'ts': self.wall(), 'level': _level(event), 'text': _log_text(row)})
            if event == 'starting':
                self.parts.log_path = row.get('log_path')
            elif event == 'startup':
                self.status = 'online'
                self.activity = 'listening'
            elif event == 'startup_failed':
                self.status = 'failed'
                self.system('Startup failed — see the Logs tab.')
            elif event == 'startup_notice':
                self.system('Startup notice: ' + clean(row.get('text', '')))
            elif event == 'listening':
                self.activity = 'listening'
            elif event == 'turn':
                self.turn_lines[row['turn_id']] = self.add('user', 'Unknown speaker', clean(row.get('original_text', '')))
            elif event == 'speaker_evidence':
                line = self.turn_lines.get(row.get('turn_id'))
                if line:
                    name, state = row.get('display_name'), row.get('state', 'unresolved')
                    line['speaker'] = (name if state in ('supported', 'inferred') else name + '?') if name else 'Unknown speaker'
                    line['note'] = ('voice close, alone in view' if state == 'inferred' else
                                    'voice: ' + state.replace('_', ' '))
                    self.revision += 1
            elif event == 'model_request' and row.get('primary_calls'):
                self.activity = 'thinking'
            elif event == 'speech_generated':
                line = self.reply_lines.get(row.get('turn_id'))
                text = clean(row.get('text', ''))
                speaker = 'R3X as ' + clean(row['voice']) if row.get('voice') else 'R3X'
                if line and line['speaker'] == speaker and line is self.lines[-1]:
                    line['text'] = (line['text'] + ' ' + text).strip()
                    self.revision += 1
                else:
                    self.reply_lines[row.get('turn_id')] = self.add('rex', speaker, text)
            elif event == 'playback_started':
                self.activity = 'speaking'
                self.speaking = True
            elif event == 'delivery':
                self.speaking = False
                self.activity = 'listening' if self.status == 'online' else self.activity
                line = self.reply_lines.get(row.get('turn_id'))
                status = row.get('status')
                if line and status in {'interrupted', 'failed'}:
                    line['note'] = status + ('; heard: ' + clean(row['completed_text']) if row.get('completed_text') else '; nothing heard')
                    self.revision += 1
                elif status == 'failed':
                    self.system('Speech failed: ' + clean(row.get('error') or 'unknown'))
            elif event == 'agency_plan_saved':
                self.goal = clean(row.get('objective', ''))
            elif event == 'agency_cleared':
                self.goal = None
            elif event == 'initiative_quiet':
                self.system('Rex will stay quiet until someone asks him to talk again.')
            elif event in {'operator_servos', 'operator_drive'}:
                what = 'Manual servo override' if event == 'operator_servos' else 'Manual driving'
                self.system(f"{what} {row.get('status')}.")
            elif event == 'shutdown_requested':
                self.status = 'stopping'
                self.activity = 'stopping'
                self.system('Shutting down (' + clean(row.get('source', '')) + ').')
            elif event in {'shutdown', 'shutdown_failed'}:
                self.status = 'stopped' if self.status != 'failed' else 'failed'
                self.activity = 'stopped'
        body = self.body
        if body is not None:
            posted = {'turn': 'heard', 'playback_started': 'speaking', 'delivery': 'done',
                      'shutdown_requested': 'shutdown'}.get(event)
            if event == 'model_request' and row.get('primary_calls'):
                posted = 'thinking'
            if posted:
                body.post(posted, row.get('turn_id'))
        if event in {'shutdown', 'shutdown_failed'}:
            self.detach()
            for hook in tuple(self.on_closed):
                hook()

    def add(self, kind, speaker, text, note=None):
        self.seq += 1
        line = {'seq': self.seq, 'ts': self.wall(), 'kind': kind, 'speaker': speaker, 'text': text, 'note': note}
        self.lines.append(line)
        del self.lines[:-TRANSCRIPT_LINES]
        self.revision += 1
        return line

    def system(self, text):
        return self.add('system', 'SYSTEM', text)

    # Composition -----------------------------------------------------------
    def attach(self, parts: LiveParts):
        """Called by App.live once the owners are composed; nothing starts here."""
        with self.lock:
            parts.log_path = parts.log_path or self.parts.log_path
            self.parts = parts
        if parts.leds is not None and id(parts.leds) not in self.hooked:
            self.hooked.add(id(parts.leds))
            parts.leds.on_command.append(self.lights.command)
        if parts.sink is not None and id(parts.sink) not in self.hooked:
            self.hooked.add(id(parts.sink))
            previous = parts.sink.on_pcm
            def tap(data, at):
                if previous:
                    previous(data, at)
                self.pcm = (bytes(data[:2048]), at)   # One bounded mailbox, as LEDs.pcm.
            parts.sink.on_pcm = tap
        if parts.robot is not None and id(parts.robot) not in self.hooked:
            # The board ramps every move at its commanded speed and acceleration; replaying the
            # packets the owner writes shows where each joint is now, with no serial reads.
            from .sim import SimulatedMaestro
            self.hooked.add(id(parts.robot))
            self.ramps = SimulatedMaestro(clock=self.clock)
            parts.robot.owner.on_packet.append(self.ramps.write)
        settings = parts.settings
        if (self.body is None and parts.robot is None and settings is not None
                and not getattr(settings, 'servo_port', None)):
            from .sim import SimulatedBody
            def faces():
                camera = self.parts.camera
                return camera.latest(self.clock()) if camera is not None else ()
            self.body = SimulatedBody(faces=faces)
            self.body.start()
        if parts.runtime is not None and self.shutdown_pending:
            parts.runtime.request_shutdown('dashboard')

    def body_robot(self):
        """The real RobotServos, or the display-only simulated one (never both)."""
        if self.parts.robot is not None:
            return self.parts.robot
        body = self.body
        return body.robot if body is not None and body.state in {'starting', 'active'} else None

    def detach(self):
        self.teleop.close()
        self.servos.close()
        if self.body is not None:
            self.body.close()
        with self.lock:
            self.parts = LiveParts(log_path=self.parts.log_path, settings=self.parts.settings)

    # Buttons -----------------------------------------------------------------
    def stop_reply(self):
        runtime = self.parts.runtime
        if runtime is not None:
            threading.Thread(target=runtime.cancel, name='r3x-gui-stop', daemon=True).start()
        drive = self.parts.drive
        if drive is not None:
            drive.cancel()
        return runtime is not None

    def stop_wheels(self):
        """Motivator STOP: zero the joystick and command a controlled stop now."""
        self.teleop.set(0., 0.)
        drive = self.parts.drive
        if drive is not None:
            drive.cancel()

    def snapshot_drive(self):
        return self._drive(self.parts, self.clock())

    def shutdown(self):
        runtime = self.parts.runtime
        if runtime is None:
            with self.lock:
                if self.status == 'booting' and not self.shutdown_pending:
                    self.shutdown_pending = True
                    self.system('Rex will shut down as soon as startup finishes.')
            return False
        threading.Thread(target=runtime.request_shutdown, args=('dashboard',), name='r3x-gui-shutdown',
                         daemon=True).start()
        return True

    # Snapshot ------------------------------------------------------------------
    def snapshot(self) -> dict:
        parts = self.parts
        now = self.clock()
        with self.lock:
            snap = {'status': self.status, 'activity': self.activity, 'lines': [dict(l) for l in self.lines],
                    'revision': self.revision, 'logs': list(self.logs), 'goal': self.goal,
                    'log_path': parts.log_path}
        runtime = parts.runtime
        try:
            initiative = runtime.session.state.initiative if runtime is not None else None
        except AttributeError:
            initiative = None
        snap['quiet'] = bool(initiative and initiative.quiet)
        if snap['quiet'] and snap['activity'] == 'listening':
            snap['activity'] = 'quiet'
        snap['camera'] = self._camera(parts, now)
        snap['objects'] = [dict(r) for r in (parts.objects.snapshot() if parts.objects else ())]
        if now - self.room_at >= 2.:   # places.context() queries the store; the room changes slowly.
            self.room, self.room_at = self._room(parts), now
        snap['room'] = self.room
        robot = parts.robot or (self.body.robot if self.body is not None else None)
        if robot is not None:
            owner = robot.owner
            commanded = dict(owner.commanded)
            if parts.robot is None:
                positions = self.body.port.positions()
            elif self.ramps is not None:
                self.ramps.seed(commanded)
                positions = self.ramps.positions()
            else:
                positions = commanded
            snap['servos'] = {'commanded': commanded, 'positions': positions, 'state': owner.state,
                              'operator': owner.operator, 'held': sorted(owner.held_channels.copy()),
                              'controller': True, 'simulated': parts.robot is None}
        else:
            snap['servos'] = {'commanded': {}, 'state': None, 'operator': False, 'held': [], 'controller': False,
                              'simulated': False}
        snap['servos']['last'] = self.servos.last
        self._mouth(parts, now)
        snap['lights'] = self.lights.snapshot()
        snap['drive'] = self._drive(parts, now)
        snap['devices'] = self._devices(parts)
        try:   # The Jeopardy board (October 26), copied under the game's own lock.
            snap['jeopardy'] = runtime.session.state.jeopardy.snapshot() if runtime is not None else {}
        except AttributeError:
            snap['jeopardy'] = {}
        return snap

    def _camera(self, parts, now):
        camera = parts.camera
        if camera is None:
            enabled = bool(parts.settings and parts.settings.camera_enabled)
            return {'frame': None, 'faces': [], 'fps': None, 'state': 'starting' if enabled and self.status == 'booting'
                    else 'unavailable' if enabled else 'disabled'}
        frame = camera.hub.peek()
        if frame is not None and (not self.frames or self.frames[-1][1] != frame.frame_id):
            self.frames.append((now, frame.frame_id))
        recent = [at for at, _ in self.frames if now - at <= 2.]
        fps = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 2 and recent[-1] > recent[0] else None
        faces = []
        for row in camera.latest(now):
            person = row.get('identity')
            faces.append({'box': row.get('box'), 'track_id': row.get('track_id'), 'person_id': person,
                          'name': self._name(parts, person) if person else None, 'position': row.get('position')})
        return {'frame': frame, 'age': now - frame.at if frame else None, 'faces': faces, 'fps': fps,
                'state': 'failed' if camera.failure else 'live' if frame else 'waiting',
                'failure': camera.failure}

    def _name(self, parts, person_id):
        now = self.clock()
        if parts.memory is not None and (person_id not in self.names or now - self.names_at > 10.):
            try:
                self.names = {p['id']: p['name'] for p in parts.memory.people()}
            except Exception:
                pass
            self.names_at = now
        return self.names.get(person_id)

    @staticmethod
    def _room(parts):
        if parts.places is None:
            return None
        try:
            context = parts.places.context()
        except Exception:
            return None
        room = context.get('room')
        return {'status': context.get('status'), 'name': room['name'] if room else None,
                'looks_like': context.get('looks_like') or []}

    def _mouth(self, parts, now):
        """Without head-board commands, playback drives the mirror (dev Mac)."""
        if parts.leds is not None:
            return
        if self.status == 'online':
            self.lights.awake()
        pcm, at = self.pcm
        level = 0
        if self.speaking and pcm and 0 <= now - at < .25:
            from array import array
            import sys
            samples = array('h', pcm[:len(pcm)//2*2])
            if sys.byteorder != 'little':
                samples.byteswap()
            if samples:
                level = min(255, int(math.sqrt(sum(v*v for v in samples) / len(samples)) / 32768 * 800))
        self.lights.speaking(self.speaking, level)

    def _drive(self, parts, now):
        drive, radar = parts.drive, parts.radar
        result = {'connected': False, 'telemetry': None, 'matrix': None, 'radar': None,
                  'manual': self.teleop.state(), 'available': (False, 'drive_not_configured')}
        if drive is not None:
            telemetry, matrix = drive.link.snapshot()
            result.update(connected=drive.state == 'ready' and not drive.link.failure, telemetry=telemetry,
                          matrix=matrix, available=self._available(drive, telemetry, now), moving=drive.moving.is_set())
            if telemetry:
                result['telemetry_age'] = now - telemetry['received_at']
        if radar is not None and radar.state == 'ready' and not radar.link.failure:
            with radar.link._state:
                telemetry = dict(radar.link.telemetry) if radar.link.telemetry else None
            result['radar'] = {'telemetry': telemetry, 'hello': dict(radar.link.hello or {}), 'connected': True}
        return result

    @staticmethod
    def _available(drive, telemetry, now):
        """Display-only subset of DriveBase.availability. That method folds each reading into
        the drive's floor memory, which only the drive owner's own callers may do."""
        if drive.state != 'ready' or drive.link.failure:
            return False, 'drive_not_ready'
        if not telemetry or not 0 <= now - telemetry['received_at'] <= .5:
            return False, 'drive_telemetry_stale'
        if telemetry.get('owner') == 'manual':
            return False, 'gamepad_in_control'
        if telemetry.get('state') in {'estop', 'fault', 'comms_lost'} or telemetry.get('fault'):
            return False, 'controller_' + str(telemetry.get('fault') or telemetry['state'])
        if telemetry.get('charging'):
            return False, 'charging'
        return True, None

    def _devices(self, parts):
        settings = parts.settings
        started = {}
        if parts.owner is not None:
            for r in parts.owner.results:
                if r.operation == 'start':
                    started[r.capability] = r.outcome == 'ok'
        def status(configured, capability, healthy):
            if not configured:
                return 'disabled'
            if capability not in started:
                return 'starting' if self.status == 'booting' else 'offline'
            return 'connected' if started[capability] and healthy() else 'offline'
        leds = parts.leds
        robot, drive, radar, camera = parts.robot, parts.drive, parts.radar, parts.camera
        get = (lambda name: getattr(settings, name, None)) if settings else (lambda name: None)
        return {
            'Chest LEDs': status(get('chest_led_port'), 'robot_leds', lambda: leds and 'chest' in leds.devices and leds.state == 'ready'),
            'Head LEDs': status(get('head_led_port'), 'robot_leds', lambda: leds and 'head' in leds.devices and leds.state == 'ready'),
            'Maestro': status(get('servo_port'), 'robot_servos', lambda: robot and robot.owner.state not in {'faulted', 'closed'}),
            'ESP32 Motor': status(get('drive_port'), 'drive_base', lambda: drive and drive.state == 'ready' and not drive.link.failure),
            'Radar': status(get('radar_port'), 'radar', lambda: radar and radar.state == 'ready' and not radar.link.failure),
            'Camera': status(get('camera_enabled'), 'camera', lambda: camera and not camera.failure),
        }


class OperatorServos:
    """Manual override worker: the newest slider values win; nothing queues behind them.

    Sliders give percentages of each joint's calibrated range (servo_poses.JOINT_RANGES,
    the same scale as the model's set_robot_joints). Turning the override off drops any
    pending values, so a stale slider can never move a joint once Rex has control back."""

    def __init__(self, feed):
        self.feed = feed
        self.lock = threading.Lock()
        self.pending: dict[str, float] = {}
        self.wake = threading.Event()
        self.thread = None
        self.cancel = Cancellation()
        self.active = False
        self.last = None   # Last receipt or failure, for the Actuators tab.

    def enable(self, on: bool):
        robot = self.feed.body_robot()
        if robot is None:
            return False, 'servo_controller_not_connected'
        if on:
            try:
                robot.begin_operator()
            except Exception as exc:
                self.last = {'status': 'rejected', 'reason': str(exc) or type(exc).__name__}
                return False, self.last['reason']
            with self.lock:
                self.pending.clear()
                self.active = True
                self.cancel = Cancellation()
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name='r3x-gui-servos', daemon=True)
                self.thread.start()
            return True, None
        with self.lock:
            self.pending.clear()
            self.active = False
            self.cancel.event.set()
        robot.end_operator()
        return True, None

    def set(self, joint: str, percent: float):
        from ..servo_poses import JOINT_RANGES
        if joint not in JOINT_RANGES or not math.isfinite(percent):
            return
        with self.lock:
            if not self.active:
                return
            self.pending[joint] = max(0., min(100., float(percent)))
        self.wake.set()

    def _run(self):
        from ..servo_poses import HEAD_PAIR, JOINT_RANGES, head_pose, head_pulses
        while True:
            self.wake.wait(.5)
            self.wake.clear()
            with self.lock:
                if not self.active:
                    return
                joints, self.pending = self.pending, {}
                cancel = self.cancel
            robot = self.feed.body_robot()
            if not joints or robot is None:
                continue
            commanded = dict(robot.owner.commanded)
            pose, head = {}, {}
            for joint, percent in joints.items():
                channel, low, high = JOINT_RANGES[joint]
                value = low + (high - low) * percent / 100
                if isinstance(channel, str):
                    head[channel] = value
                else:
                    pose[channel] = round(value)
            if head:
                try:
                    pitch, tilt = head_pose({ch: commanded[ch] for ch in HEAD_PAIR})
                except (KeyError, ValueError):
                    pitch, tilt = 0., 0.
                pose.update(head_pulses(head.get('pitch', pitch), head.get('tilt', tilt)))
            try:
                receipt = robot.operator_pose(pose, cancel, 'operator:' + uuid4().hex)
                self.last = {'status': receipt.status, 'reason': receipt.reason}
            except Exception as exc:
                self.last = {'status': 'failed', 'reason': type(exc).__name__}
            if self.last['status'] == 'rejected' and self.last['reason'] == 'ServoBusy':
                with self.lock:   # Retry the same values unless newer ones arrived.
                    self.pending = {**joints, **self.pending}
                time.sleep(.05)
                self.wake.set()

    def close(self):
        if self.active:
            try:
                self.enable(False)
            except Exception:
                pass


class Teleop:
    """Joystick worker: sends the latest setpoint about ten times a second while enabled."""

    def __init__(self, feed):
        self.feed = feed
        self.lock = threading.Lock()
        self.setpoint = (0., 0., float('-inf'))
        self.active = False
        self.stop_event = threading.Event()
        self.thread = None
        self.last = None
        self.cancel = Cancellation()

    def enable(self, on: bool):
        drive = self.feed.parts.drive
        if drive is None:
            return False, 'wheels_not_connected'
        if not on:
            self.close()
            return True, None
        ok, reason = drive.begin_manual()
        if not ok:
            self.last = {'status': 'refused', 'reason': reason}
            return False, reason
        with self.lock:
            self.active = True
            self.setpoint = (0., 0., self.feed.clock())
            self.cancel = Cancellation()
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, args=(drive,), name='r3x-gui-teleop', daemon=True)
        self.thread.start()
        return True, None

    def set(self, lin, ang):
        with self.lock:
            self.setpoint = (float(lin), float(ang), self.feed.clock())

    def _run(self, drive):
        try:
            while not self.stop_event.wait(.1):
                with self.lock:
                    lin, ang, at = self.setpoint
                if self.feed.clock() - at > SETPOINT_STALE:
                    lin = ang = 0.
                try:
                    self.last = drive.manual(lin, ang, self.cancel)
                except Exception as exc:
                    self.last = {'status': 'failed', 'reason': type(exc).__name__}
        finally:
            drive.end_manual()

    def state(self):
        return {'active': self.active, 'last': self.last}

    def close(self):
        with self.lock:
            was = self.active
            self.active = False
            self.cancel.event.set()
        self.stop_event.set()
        thread = self.thread
        if was and thread is not None and thread is not threading.current_thread():
            thread.join(3.)
        self.thread = None
