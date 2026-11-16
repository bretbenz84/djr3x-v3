"""`python -m r3x telepresence`: compose the call from the existing owners, with no models.

Startup: the live lease and the controller handoff (the menu apps and supervisor step aside, and a
running Rex is never taken over), then the speaker, lights, servo wake, wheels (no back-off reflex
and no radar), microphone, camera (frames only), video and the listener. The visor opens, the arms take
the telepresence stance and the telepresence lights come on, with no sound. The caller then aims the
head, lift and visor and drives (controls.py). No startup sounds, spoken lines, recognition,
replies, memory, idle motion or motion of his own. Shutdown stops the wheels, parks the servos and
fades the lights as Rex's own shutdown does. Each run leaves local/telepresence/last-call.json (how
it ended) for the supervisor to report.
"""
from dataclasses import asdict
import json
import math
import os
import shutil
import signal
import threading
import time
from uuid import uuid4

from ..contracts import Cancellation, ProviderFailure
from ..providers import ProviderOwner, dependency_status
from ..settings import ScratchPaths, Secrets
from .call import VIDEO_FPS, VIDEO_WIDTH, Call, VideoStream
from .protocol import AUDIO_RATE, parse_listen, parse_networks, valid_token

SHOWN = {'telepresence_listening', 'telepresence_ready', 'telepresence_client_connected',
         'telepresence_client_refused', 'telepresence_link_closed', 'telepresence_video_failed',
         'telepresence_startup_failed', 'telepresence_shutdown', 'microphone_restarted'}


def console(record):
    """The terminal's view: what happened to the call, one line each; the session file keeps everything."""
    event = record['event']
    if event == 'telepresence_stats':
        resources = record.get('resources') or {}
        print(f"telepresence: {'connected' if record.get('connected') else 'waiting'}"
              f", round trip {record.get('rtt_ms_median')} ms, cpu {resources.get('cpu_percent')}%", flush=True)
    elif event in SHOWN:
        details = {k: v for k, v in record.items() if k not in {'session_id', 'at', 'event', 'stats', 'report'}}
        print(f"telepresence: {event[len('telepresence_'):] if event.startswith('telepresence_') else event}"
              + (' ' + json.dumps(details) if details else ''), flush=True)


def compose(settings, secrets):
    """The providers, in start order; constructing them opens nothing."""
    from ..adapters.audio import Microphone
    from .server import Server
    from .speaker import LiveSpeaker
    speaker = LiveSpeaker(settings.output_device_name)
    mic = Microphone(settings, lambda: False)
    leds = robot = camera = video = drive = None
    if settings.led_port:
        from ..leds import LEDs
        leds = LEDs(settings.led_port)
    if settings.servo_port:
        from ..robot_servos import RobotServos
        robot = RobotServos(settings.servo_port)
    if settings.drive_port:
        # The caller drives (stage 5); he never moves on his own, so the back-off reflex is off. The radar
        # ring only tells Rex where people are to look at and seek (owner, November 16: no point for a
        # robot driven by hand), so it is never opened and an unplugged one changes nothing.
        from ..drive import DriveBase
        drive = DriveBase(settings.drive_port, reflex=False)
    if settings.camera_enabled:
        from ..adapters.camera import Camera
        camera = Camera(settings.camera_device_name, None, width=settings.camera_width, height=settings.camera_height,
                        framerate=settings.camera_framerate, output_width=min(VIDEO_WIDTH, settings.camera_width))
        video = VideoStream(camera)
    host, port = parse_listen(settings.telepresence_listen)

    def welcome():
        live = video is not None and video.thread is not None
        return {'audio': {'rate': AUDIO_RATE, 'channels': 1, 'format': 's16le'},
                'video': {'width': camera.frame_width, 'height': camera.frame_height, 'fps': VIDEO_FPS,
                          'format': 'jpeg'} if live else None,
                'controls': controls_offer(robot, drive)}
    server = Server(host, port, parse_networks(settings.telepresence_allowed_networks),
                    secrets.telepresence_token, welcome=welcome)
    if leds:
        speaker.on_pcm = leds.pcm   # The mouth moves with the owner's voice.
        if robot:
            robot.before_shutdown = leds.cancel   # The fade runs with the park, as at Rex's own shutdown.
    # Closed in reverse: the listener and media first, then the wheels, then the servos park.
    providers = tuple(p for p in (speaker, leds, robot, drive, mic, camera, video, server) if p is not None)
    return providers, {'speaker': speaker, 'mic': mic, 'leds': leds, 'robot': robot,
                       'drive': drive, 'camera': camera, 'video': video, 'server': server}


def controls_offer(robot, drive):
    """What the caller may steer this call, in the welcome (the wire messages: docs/TELEPRESENCE_PLAN.md)."""
    from ..drive import MANUAL_SPEED, MANUAL_TURN
    from ..servo_poses import TELEPRESENCE_DRIVE_HEAD, telepresence_reading
    from .controls import LINK_SLOW_SECONDS, SETPOINT_HZ, SETPOINT_STALE_SECONDS
    head = robot is not None and robot.telepresent
    wheels = drive is not None and drive.state == 'ready' and (robot is None or robot.telepresent)
    return {'head': {'home': telepresence_reading(dict(TELEPRESENCE_DRIVE_HEAD)),
                     'fields': ['neck', 'pitch', 'tilt', 'lift', 'visor']} if head else None,
            'drive': {'setpoint_hz': SETPOINT_HZ, 'stale_ms': round(SETPOINT_STALE_SECONDS * 1000),
                      'link_slow_ms': round(LINK_SLOW_SECONDS * 1000), 'max_lin_mps': MANUAL_SPEED,
                      'max_turn_dps': round(math.degrees(MANUAL_TURN))} if wheels else None}


def record_ending(root, ending):
    """local/telepresence/last-call.json: how the last run ended, for the supervisor's status."""
    folder = root / 'telepresence'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        temporary = folder / 'last-call.json.tmp'
        temporary.write_text(json.dumps({**ending, 'ended_at': time.time()}))
        os.replace(temporary, folder / 'last-call.json')
    except OSError:
        pass   # The call's own session log keeps the ending.


def readiness(settings, secrets, providers=(), which=shutil.which):
    missing = []
    if not settings.telepresence_listen:
        missing.append('telepresence_listen')
    if not valid_token(secrets.telepresence_token):
        missing.append('telepresence_token')
    if not settings.input_device_name and settings.input_device_index is None:
        missing.append('microphone_selection')
    if settings.camera_enabled and not which('ffmpeg'):
        missing.append('ffmpeg')
    for provider in providers:
        missing.extend(name for name, version in dependency_status(provider.capability).items() if version is None)
    return sorted(set(missing))


def setup(root):
    """Make the token once, in local/secrets.toml (python -m r3x telepresence-setup)."""
    import secrets as random
    current = Secrets.load(root).telepresence_token
    if valid_token(current):
        return 'local/secrets.toml already has a telepresence_token.'
    if current:
        raise ValueError('telepresence_token in local/secrets.toml is not 32+ printable characters without spaces')
    path = root / 'secrets.toml'
    existing = path.read_text() if path.exists() else ''
    with path.open('a', encoding='utf-8') as handle:
        handle.write(('' if not existing or existing.endswith('\n') else '\n')
                     + f'telepresence_token = "{random.token_urlsafe(32)}"\n')
    path.chmod(0o600)
    return 'Added telepresence_token to local/secrets.toml. Copy that line into the calling Mac\'s local/secrets.toml.'


def run(settings, project, *, compose=compose, handoff=None, display=console, clock=None) -> int:
    """compose, handoff, display and clock are the checks' seams; the defaults are the robot's."""
    from ..hardware_ownership import ControllerHandoff, HardwareLocks
    from ..lease import LiveLease
    from ..trace import Trace
    paths = ScratchPaths.for_project(project)
    secrets = Secrets.load(paths.root)
    if not settings.telepresence_listen:
        print(json.dumps({'outcome': 'failed', 'missing': ['telepresence_listen']}))
        record_ending(paths.root, {'reason': 'not_ready', 'code': 1, 'missing': ['telepresence_listen']})
        return 1
    providers, parts = compose(settings, secrets)
    missing = readiness(settings, secrets, providers)
    if missing:
        print(json.dumps({'outcome': 'failed', 'missing': missing}))
        record_ending(paths.root, {'reason': 'not_ready', 'code': 1, 'missing': missing})
        return 1
    paths.prepare()
    lease = LiveLease(paths.root / 'sessions/live.lock')
    try:
        lease.acquire()   # A running Rex (or another call) owns the checkout: no takeover.
    except ValueError:
        record_ending(paths.root, {'reason': 'rex_awake', 'code': 1})
        raise
    handoff = handoff or ControllerHandoff(HardwareLocks(), port=settings.servo_port or None,
        led_ports={zone: port for zone, port in (('leds', settings.led_port), ('drive_base', settings.drive_port))
                   if port})
    try:
        handoff.acquire(Cancellation())
    except BaseException as exc:
        lease.close()
        record_ending(paths.root, {'reason': 'handoff_failed:' + (str(exc) or type(exc).__name__), 'code': 1})
        raise
    session_id = uuid4().hex
    log_path = paths.root / 'sessions' / (session_id + '.jsonl')
    trace = Trace(session_id, log_path, display=display)
    for part in parts.values():
        if part is not None:
            part.trace = trace
    owner = ProviderOwner(providers)
    stop = threading.Event()
    main_thread = threading.current_thread() is threading.main_thread()

    def terminate(*_):
        raise KeyboardInterrupt()
    previous = signal.signal(signal.SIGTERM, terminate) if main_thread else None
    reason, code = 'startup_failed', 1
    try:
        trace.emit('telepresence_starting', log_path=str(log_path), evidence='live',
                   capabilities=[p.capability.name for p in providers])
        if not owner.start():
            trace.emit('telepresence_startup_failed', results=[asdict(r) for r in owner.results])
        else:
            active = {r.capability for r in owner.results if r.operation == 'start' and r.outcome == 'ok'}
            robot, leds = parts['robot'], parts['leds']
            if robot is not None and 'robot_servos' in active:
                try:
                    robot.open_eyes()   # The visor opens for the call (owner, November 16).
                except Exception as exc:
                    trace.emit('servo_lifecycle', kind='open_eyes', status='failed', error_type=type(exc).__name__)
                try:
                    robot.telepresence_stance(Cancellation())   # Arms tucked and held; only the caller moves him.
                except Exception as exc:
                    trace.emit('servo_telepresence', kind='stance', status='failed', error_type=type(exc).__name__)
            if leds is not None and 'robot_leds' in active:
                leds.telepresence()
            call = Call(parts['server'], parts['mic'], parts['speaker'], trace, video=parts['video'],
                        camera=parts['camera'], **({'clock': clock} if clock else {}),
                        **call_controls(parts, active, clock))
            try:
                parts['server'].open()   # Only now: the stance holds and the call is wired to the link.
            except OSError as exc:
                reason = 'listen_failed:' + type(exc).__name__
                trace.emit('telepresence_startup_failed', reason=reason)
            else:
                try:
                    reason = call.run(stop)
                    code = 0
                except ProviderFailure as exc:
                    reason = 'failed:' + str(exc)
    except KeyboardInterrupt:
        reason, code = 'interrupted', 0
    finally:
        stop.set()
        owner.close()
        trace.emit('telepresence_shutdown', reason=reason, lifecycle=owner.state,
                   results=[asdict(r) for r in owner.results if r.outcome != 'ok'])
        if owner.state == 'closed':
            handoff.close()   # Only after every device closed: a stuck closer keeps the robot's leases.
            lease.close()
        trace.close()
        if main_thread:
            signal.signal(signal.SIGTERM, previous)
        failures = [r for r in owner.results if r.outcome != 'ok' and r.operation == 'start']
        record_ending(paths.root, {'session_id': session_id, 'reason': reason,
                                   'code': code if owner.state == 'closed' else 1,
                                   'failed_starts': [{'capability': r.capability, 'reason': r.reason or r.error_type}
                                                     for r in failures]})
    return code if owner.state == 'closed' else 1


def call_controls(parts, active, clock=None):
    """The caller's head and wheel controls for the parts that started; none for parts that did not."""
    from .controls import DriveControl, HeadControl, base_readings
    robot, drive = parts.get('robot'), parts.get('drive')
    stance = robot is not None and 'robot_servos' in active and robot.telepresent
    head = HeadControl(robot) if stance else None
    wheels = None
    # The wheels need the arms tucked: with servos fitted, only once the stance holds them. With no
    # servo board configured there are no arms to tuck and the spin check uses their untucked reach.
    if drive is not None and 'drive_base' in active and (robot is None or stance):
        wheels = DriveControl(drive, head=head, stance_held=stance, **({'clock': clock} if clock else {}))
    base = (lambda now: base_readings(drive, now)) if drive is not None and 'drive_base' in active else None
    return {'head': head, 'drive': wheels, 'base': base}
