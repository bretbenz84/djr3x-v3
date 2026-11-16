"""One telepresence call: the room's audio and video to the client, the client's voice to the speaker.

Nothing here interprets what is said. The call ends on the client's hang-up, when nobody has
connected CONNECT_WAIT_SECONDS after the start, or when the link has been gone RECONNECT_SECONDS
(a link that drops may come back meanwhile); the program then parks him.
"""
import resource
import statistics
import subprocess
import sys
import threading
import time

from ..contracts import Cancelled, ProviderFailure
from ..providers import Capability
from .protocol import AUDIO_RATE, ProtocolError, decode_json, decode_stamped

CONNECT_WAIT_SECONDS = 120.   # Started for a call (later, by the supervisor): nobody by then ends it.
RECONNECT_SECONDS = 30.       # A dropped link may come back this long before he parks.
SILENT_SECONDS = 5.           # Nothing heard from the client this long: that link is closed.
SEND_STALL_SECONDS = 5.       # One send blocked this long: that link is closed.
PING_SECONDS = 1.
STATS_SECONDS = 10.
MAX_VOICE_BYTES = AUDIO_RATE * 2 // 5   # 200 ms in one frame; more is a broken client.
VIDEO_FPS = 15.
VIDEO_WIDTH = 960
VIDEO_QUALITY = 70


def process_usage(pids):
    """{pid: (cpu percent of one core, resident MB)} from ps; missing processes are left out."""
    if not pids:
        return {}
    done = subprocess.run(['/bin/ps', '-o', 'pid=,%cpu=,rss=', '-p', ','.join(map(str, pids))],
                          capture_output=True, text=True, timeout=2.)
    usage = {}
    for line in done.stdout.splitlines():
        fields = line.split()
        if len(fields) == 3:
            usage[int(fields[0])] = (float(fields[1]), round(int(fields[2]) / 1024, 1))
    return usage


class Resources:
    """This process's CPU (share of one core since the last sample) and peak memory, plus helpers' (ffmpeg)."""
    def __init__(self, clock=time.monotonic, usage=process_usage, rusage=resource.getrusage):
        self.clock, self.usage, self.rusage = clock, usage, rusage
        self.last = None

    def sample(self, helpers=()):
        now = self.clock()
        own = self.rusage(resource.RUSAGE_SELF)
        cpu = own.ru_utime + own.ru_stime
        percent = None
        if self.last is not None and now > self.last[0]:
            percent = round(100 * (cpu - self.last[1]) / (now - self.last[0]), 1)
        self.last = (now, cpu)
        peak = own.ru_maxrss / (1024 * 1024 if sys.platform == 'darwin' else 1024)
        result = {'cpu_percent': percent, 'peak_rss_mb': round(peak, 1)}
        pid = next((p for p in helpers if p), None)   # The camera's ffmpeg, when it runs.
        if pid:
            try:
                found = self.usage([pid]).get(pid)
            except (OSError, subprocess.SubprocessError, ValueError):
                result['ffmpeg'] = 'unavailable'
            else:
                if found:
                    result['ffmpeg_cpu_percent'], result['ffmpeg_rss_mb'] = found
        return result


def jpeg_encoder(quality=VIDEO_QUALITY):
    from ..sentry_recording import jpeg
    return lambda frame: jpeg(frame, width=frame.width, quality=quality)


class VideoStream:
    """The camera's newest frame, as JPEG, VIDEO_FPS times a second while a client is connected."""
    capability = Capability('telepresence_video', 'OpenCV JPEG', 'camera frames for the call',
        dependencies=('opencv-python', 'numpy'), resources=(f'{VIDEO_FPS:g} fps, JPEG quality {VIDEO_QUALITY}',),
        required=False, requires=('camera',))

    def __init__(self, camera, *, fps=VIDEO_FPS, encode=None, clock=time.monotonic):
        self.camera, self.interval, self.clock = camera, 1. / fps, clock
        self.encode = encode
        self.target = lambda: None   # The connected link, or None; set by the call.
        self.stop = threading.Event()
        self.thread = None
        self.sequence = 0
        self.encode_seconds = []
        self.failures = 0
        self.trace = None

    def start(self):
        if self.encode is None:
            import cv2, numpy   # noqa: F401  Fail here, not on the first frame.
            self.encode = jpeg_encoder()
        self.thread = threading.Thread(target=self._run, name='telepresence-video', daemon=True)
        self.thread.start()

    def _run(self):
        last = None
        while not self.stop.wait(self.interval):
            frame = self.camera.hub.peek()
            link = self.target()
            if frame is None or link is None or frame.frame_id == last:
                continue
            last = frame.frame_id
            began = self.clock()
            try:
                data = self.encode(frame)
            except Exception as exc:
                self.failures += 1
                if self.trace and self.failures == 1:
                    self.trace.emit('telepresence_video_failed', error_type=type(exc).__name__)
                continue
            self.encode_seconds.append(self.clock() - began)
            del self.encode_seconds[:-300]
            link.send_video(self.sequence, frame.at, data)
            self.sequence += 1

    def summary(self):
        times = sorted(self.encode_seconds)
        return {'frames': self.sequence, 'failures': self.failures,
                'encode_ms_median': round(1000 * statistics.median(times), 1) if times else None,
                'encode_ms_max': round(1000 * times[-1], 1) if times else None}

    def cancel(self):
        self.stop.set()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(2.)
            if self.thread.is_alive():
                raise ProviderFailure('TelepresenceVideoDidNotStop')


class Call:
    """head and drive are the caller's controls (controls.HeadControl, controls.DriveControl), or None
    when the servos or wheels are not running; base(now) is the status strip's base readings."""
    def __init__(self, server, mic, speaker, trace, *, video=None, camera=None, clock=time.monotonic,
                 resources=None, connect_wait=CONNECT_WAIT_SECONDS, reconnect=RECONNECT_SECONDS,
                 head=None, drive=None, base=None):
        self.server, self.mic, self.speaker, self.trace = server, mic, speaker, trace
        self.video, self.camera, self.clock = video, camera, clock
        self.head, self.drive, self.base = head, drive, base
        self.ping_due = False    # A new link is pinged at once: driving waits for a measured round trip.
        self.resources = resources or Resources(clock)
        self.connect_wait, self.reconnect = connect_wait, reconnect
        self.lock = threading.Lock()
        self.link = None
        self.connected_once = False
        self.lost_at = None
        self.hung_up = False
        self.pings = {}          # id -> when sent
        self.ping_id = 0
        self.rtts = []           # Seconds, this link.
        self.audio_sequence = 0
        self.voice_frames = 0
        self.bad_frames = 0
        server.on_client = self._connected
        server.on_frame = self._frame
        server.on_closed = self._closed
        if video is not None:
            video.target = lambda: self.link
        if drive is not None:
            drive.link_slow, drive.notify = self.link_slow, self._send
        if head is not None:
            head.notify = self._send
            head.wheels_busy = (lambda: drive.engaged) if drive is not None else (lambda: False)

    # Link callbacks (server threads) ----------------------------------------
    def _connected(self, link, source, name):
        with self.lock:
            self.link, self.connected_once, self.lost_at = link, True, None
            self.pings.clear(); self.rtts.clear()
            self.ping_due = True

    def _closed(self, link, reason):
        with self.lock:
            if self.link is not link:
                return
            self.link, self.lost_at = None, self.clock()
            if reason == 'hangup':
                self.hung_up = True
        if self.drive is not None:
            self.drive.link_lost()   # The wheels stop before anything else is tidied.
        if self.head is not None:
            self.head.clear()
        self.speaker.clear()

    def _send(self, message):
        link = self.link
        if link is not None:
            link.send_json(message)

    def link_slow(self, now):
        """Why the link is too slow to steer by, or None: the latest round trip, or a ping still
        unanswered, over LINK_SLOW_SECONDS; nothing measured yet on this link counts as slow."""
        from .controls import LINK_SLOW_SECONDS
        with self.lock:
            if not self.rtts:
                return 'link_not_measured'
            worst = max([self.rtts[-1]] + [now - at for at in self.pings.values()])
        return 'link_too_slow' if worst > LINK_SLOW_SECONDS else None

    def _frame(self, link, kind, payload):
        if link is None or link is not self.link:
            return   # A link that has been replaced or closed is not heard.
        if kind == 'A':
            _, _, pcm = decode_stamped(payload)
            if len(pcm) % 2 or len(pcm) > MAX_VOICE_BYTES:
                self.bad_frames += 1
                return
            self.speaker.write(pcm)
            self.voice_frames += 1
            return
        if kind != 'J':
            raise ProtocolError('client_sent_video')
        message = decode_json(payload)
        kind = message['type']
        if kind == 'drive':
            if self.drive is not None:
                self.drive.setpoint(message)
            return
        if kind == 'stop':
            if self.drive is not None:
                self.drive.stop()
            return
        if kind == 'head':
            reason = self.head.request(message) if self.head is not None else 'head_not_available'
            if reason:
                link.send_json({'type': 'head_state', 'status': 'refused', 'reason': reason})
            return
        ident = message.get('id')
        if kind in ('ping', 'pong') and type(ident) is not int:
            return   # Only whole-number ids are answered or matched.
        if kind == 'ping':
            stamp = message.get('t')
            link.send_json({'type': 'pong', 'id': ident, 't': stamp if isinstance(stamp, (int, float)) else None,
                            'robot_t': self.clock()})
        elif kind == 'pong':
            with self.lock:
                sent = self.pings.pop(ident, None)
                if sent is not None:
                    self.rtts.append(self.clock() - sent)
                    del self.rtts[:-120]
        elif kind == 'hangup':
            with self.lock:
                self.hung_up = True
            link.close('hangup')

    # The call ----------------------------------------------------------------
    def status(self, now):
        with self.lock:
            rtts = sorted(self.rtts[-30:])
        return {'type': 'status', 'robot_t': now,
                'rtt_ms': round(1000 * rtts[len(rtts) // 2], 1) if rtts else None,
                'rtt_ms_max': round(1000 * rtts[-1], 1) if rtts else None,
                'speaker': {'buffered_ms': round(1000 * self.speaker.buffered_seconds()),
                            'underruns': self.speaker.stats['underruns'],
                            'dropped_ms': round(1000 * self.speaker.stats['dropped_seconds'])},
                'microphone_dropped': getattr(self.mic, 'dropped', 0),
                'video': bool(self.video and self.video.thread),
                'head': self.head.status() if self.head is not None else None,
                'drive': self.drive.status() if self.drive is not None else None,
                'base': self.base(now) if self.base is not None else None}

    def _tick(self, now, last_stats):
        link = self.link
        if link is not None:
            if now - link.last_heard > SILENT_SECONDS:
                link.close('silent')
            elif link.stalled_for(now) > SEND_STALL_SECONDS:
                link.close('send_stalled')
            else:
                with self.lock:
                    self.ping_id += 1
                    self.pings[self.ping_id] = now
                    for old in [i for i, at in self.pings.items() if now - at > 10]:
                        del self.pings[old]
                link.send_json({'type': 'ping', 'id': self.ping_id, 't': now})
                link.send_json(self.status(now))
        if now - last_stats < STATS_SECONDS:
            return last_stats
        helpers = [getattr(getattr(self.camera, 'process', None), 'pid', None)] if self.camera else []
        with self.lock:
            rtts = sorted(self.rtts)
        self.trace.emit('telepresence_stats', connected=link is not None,
                        rtt_ms_median=round(1000 * statistics.median(rtts), 1) if rtts else None,
                        rtt_ms_max=round(1000 * rtts[-1], 1) if rtts else None,
                        link=dict(link.stats) if link else None, voice_frames=self.voice_frames,
                        bad_voice_frames=self.bad_frames, speaker=dict(self.speaker.stats),
                        microphone_dropped=getattr(self.mic, 'dropped', 0),
                        video=self.video.summary() if self.video else None,
                        head=dict(self.head.stats) if self.head is not None else None,
                        drive=dict(self.drive.stats) if self.drive is not None else None,
                        resources=self.resources.sample(helpers))
        return now

    def _ended(self, now, started):
        with self.lock:
            if self.hung_up:
                return 'hangup'
            if not self.connected_once and now - started >= self.connect_wait:
                return 'nobody_connected'
            if self.link is None and self.lost_at is not None and now - self.lost_at >= self.reconnect:
                return 'link_lost'
        return None

    def run(self, stop: threading.Event) -> str:
        started = last_tick = self.clock()
        last_stats = started
        self.resources.sample()
        controls = [c for c in (self.drive, self.head) if c is not None]
        for control in controls:
            control.start()
        self.trace.emit('telepresence_ready', connect_wait_seconds=self.connect_wait,
                        head=self.head is not None, drive=self.drive is not None)
        try:
            while not stop.is_set():
                try:
                    frame = self.mic.read()
                except Cancelled:
                    return 'stopped'
                now = self.clock()
                link = self.link
                if frame is not None and link is not None:
                    link.send_audio(self.audio_sequence, frame.started_at, frame.pcm)
                    self.audio_sequence += 1
                if self.ping_due or now - last_tick >= PING_SECONDS:
                    self.ping_due = False
                    last_tick = now
                    last_stats = self._tick(now, last_stats)
                reason = self._ended(now, started)
                if reason:
                    return reason
            return 'stopped'
        finally:
            for control in controls:   # The wheels stop and the head settles before the devices close.
                try:
                    control.close()
                except Exception as exc:   # The drive and servo owners still stop and park at their close.
                    self.trace.emit('telepresence_control_close_failed', control=type(control).__name__,
                                    error_type=type(exc).__name__, reason=str(exc))
