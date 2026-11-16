"""`python -m r3x telepresence`: compose the call from the existing owners, with no models.

Startup: the live lease and the controller handoff (the menu apps and supervisor step aside, and a
running Rex is never taken over), then the speaker, lights, servo wake, microphone, camera (frames
only), video and the listener. The visor opens and the telepresence lights come on, with no sound.
No startup sounds, spoken lines, recognition, replies, memory or idle motion. Shutdown parks the
servos and fades the lights as Rex's own shutdown does.
"""
from dataclasses import asdict
import json
import shutil
import signal
import threading
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
    leds = robot = camera = video = None
    if settings.led_port:
        from ..leds import LEDs
        leds = LEDs(settings.led_port)
    if settings.servo_port:
        from ..robot_servos import RobotServos
        robot = RobotServos(settings.servo_port)
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
                          'format': 'jpeg'} if live else None}
    server = Server(host, port, parse_networks(settings.telepresence_allowed_networks),
                    secrets.telepresence_token, welcome=welcome)
    if leds:
        speaker.on_pcm = leds.pcm   # The mouth moves with the owner's voice.
        if robot:
            robot.before_shutdown = leds.cancel   # The fade runs with the park, as at Rex's own shutdown.
    providers = tuple(p for p in (speaker, leds, robot, mic, camera, video, server) if p is not None)
    return providers, {'speaker': speaker, 'mic': mic, 'leds': leds, 'robot': robot, 'camera': camera,
                       'video': video, 'server': server}


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
        return 1
    providers, parts = compose(settings, secrets)
    missing = readiness(settings, secrets, providers)
    if missing:
        print(json.dumps({'outcome': 'failed', 'missing': missing}))
        return 1
    paths.prepare()
    lease = LiveLease(paths.root / 'sessions/live.lock')
    lease.acquire()   # A running Rex (or another call) owns the checkout: no takeover.
    handoff = handoff or ControllerHandoff(HardwareLocks(), port=settings.servo_port or None,
                                           led_ports={'leds': settings.led_port} if settings.led_port else {})
    try:
        handoff.acquire(Cancellation())
    except BaseException:
        lease.close()
        raise
    session_id = uuid4().hex
    log_path = paths.root / 'sessions' / (session_id + '.jsonl')
    trace = Trace(session_id, log_path, display=display)
    for part in parts.values():
        if part is not None:
            part.trace = trace
    owner = ProviderOwner(providers)
    call = Call(parts['server'], parts['mic'], parts['speaker'], trace, video=parts['video'], camera=parts['camera'],
                **({'clock': clock} if clock else {}))
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
            if leds is not None and 'robot_leds' in active:
                leds.telepresence()
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
    return code if owner.state == 'closed' else 1
