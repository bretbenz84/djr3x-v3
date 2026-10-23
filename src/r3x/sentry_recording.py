"""Sentry recordings: what the camera saw and the microphone heard while intruders were there.

Owner, October 22: recordings are kept indefinitely, and only when a face arrives. While Rex guards,
the latest ten seconds of camera frames (five a second, 960 px JPEG) and microphone audio wait in
memory; when a face arrives they become the start of a recording that runs until the intruders leave.
Each recording is a folder under local/sentry/recordings/ (ignored by git, never uploaded):
video.mov (Motion-JPEG with the microphone's audio, joined by ffmpeg), photo.jpg (the frame the alert
was sent with) and event.json (when, why, what was heard, who stood him down). If joining fails the
frames and audio.wav stay where they are; nothing is lost.
"""
from collections import deque
from datetime import datetime
import json
from pathlib import Path
import shutil
import threading
import time
import wave

from .contracts import ProviderFailure

FPS = 5.
WIDTH = 960
PHOTO_WIDTH = 1280
PREROLL_SECONDS = 10.
MAX_SECONDS = 1800.            # A longer visit continues in a new recording.
MIN_FREE_BYTES = 2 * 1024 ** 3   # Leave the disk this much room; below it nothing new is recorded.
SAMPLE_RATE = 16000            # The microphone's mono 16-bit stream.


def jpeg(frame, width=WIDTH, quality=75) -> bytes:
    import cv2
    import numpy as np
    image = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    if frame.width > width:
        image = cv2.resize(image, (width, round(frame.height * width / frame.width)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode('.jpg', image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ProviderFailure('JPEGEncodeFailed')
    return data.tobytes()


def ffmpeg_join(folder: Path, frames, audio_offset):
    """Frames (at, name) and audio.wav into video.mov; the JPEGs are copied, not re-encoded."""
    import subprocess
    executable = shutil.which('ffmpeg')
    if not executable:
        raise ProviderFailure('FFmpegMissing')
    listing = folder / 'frames.ffconcat'
    lines = ['ffconcat version 1.0']
    for (at, name), following in zip(frames, [*frames[1:], None]):
        lines += [f"file 'frames/{name}'", f"duration {max(.02, following[0] - at if following else 1 / FPS):.3f}"]
    lines.append(f"file 'frames/{frames[-1][1]}'")   # The concat reader uses the last duration only before another file.
    listing.write_text('\n'.join(lines) + '\n')
    audio = folder / 'audio.wav'
    sound = audio.is_file() and audio.stat().st_size > 44
    command = [executable, '-nostdin', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing)]
    if sound:
        command += ['-itsoffset', f'{audio_offset:.3f}', '-i', str(audio), '-map', '0:v', '-map', '1:a',
                    '-c:a', 'aac', '-b:a', '64k']
    command += ['-c:v', 'copy', '-movflags', '+faststart', str(folder / 'video.mov')]
    done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
    if done.returncode != 0 or not (folder / 'video.mov').is_file():
        raise ProviderFailure('VideoJoinFailed:' + (done.stderr or '').strip()[-200:])
    return folder / 'video.mov'


class SentryRecorder:
    def __init__(self, root: Path, frame, trace=None, *, clock=time.monotonic, wall=time.time,
                 encode=jpeg, join=ffmpeg_join, free=lambda path: shutil.disk_usage(path).free):
        self.root, self.frame, self.trace = root, frame, trace
        self.clock, self.wall, self.encode, self.join, self.free = clock, wall, encode, join, free
        self.lock = threading.Lock()
        self.frames = deque()      # (at, jpeg) of the last PREROLL_SECONDS while nothing records.
        self.sound = deque()       # (at, pcm) likewise.
        self.current = None
        self.latest = None         # (at, frame_id, raw Frame) for the alert photo.
        self._stop = threading.Event()
        self._thread = None

    # Capture -------------------------------------------------------------------
    def start(self):
        if self._thread is not None and self._thread.is_alive():
            if not self._stop.is_set():
                return
            self._thread.join(3.)   # The previous guard's recording is still being put away.
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name='r3x-sentry-recorder', daemon=True)
        self._thread.start()

    def _run(self):
        seen = None
        failures = 0
        while not self._stop.wait(1 / FPS):
            try:
                frame = self.frame()
                if frame is None or frame.frame_id == seen or not 0 <= self.clock() - frame.at <= 1.:
                    continue
                seen = frame.frame_id
                data = self.encode(frame)
                with self.lock:
                    self.latest = (frame.at, frame)
                    if self.current is not None:
                        self._write_frame(frame.at, data)
                    else:
                        self.frames.append((frame.at, data))
                        while self.frames and frame.at - self.frames[0][0] > PREROLL_SECONDS:
                            self.frames.popleft()
                failures = 0
            except Exception as exc:
                failures += 1
                if self.trace and failures in (1, 50):
                    self.trace.emit('sentry_recording', status='frame_failed', error_type=type(exc).__name__)

    def audio(self, pcm, at):
        """A microphone frame (16 kHz mono 16-bit), from the audio loop: cheap, never blocks on disk long."""
        with self.lock:
            if self.current is not None:
                if self.current['audio_at'] is None:
                    self.current['audio_at'] = at
                self.current['wav'].writeframes(pcm)
            else:
                self.sound.append((at, pcm))
                while self.sound and at - self.sound[0][0] > PREROLL_SECONDS:
                    self.sound.popleft()

    def _write_frame(self, at, data):
        current = self.current
        name = f"{len(current['frames']) + 1:06d}.jpg"
        (current['folder'] / 'frames' / name).write_bytes(data)
        current['frames'].append((at, name))

    # Recordings -------------------------------------------------------------------
    def recording(self):
        with self.lock:
            return None if self.current is None else dict(folder=str(self.current['folder']),
                                                          seconds=self.clock() - self.current['began'])

    def begin(self, reason):
        """Start a recording with the last ten seconds in it. Returns its folder, or None."""
        with self.lock:
            if self.current is not None:
                return self.current['folder']
            try:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
                if self.root.resolve() != self.root:
                    raise ProviderFailure('SentryRecordingPathIsALink')
                if self.free(self.root) < MIN_FREE_BYTES:
                    raise ProviderFailure('DiskNearlyFull')
                stamp = datetime.fromtimestamp(self.wall()).strftime('%Y-%m-%d_%H-%M-%S')
                folder = self.root / f'{stamp}_{reason}'
                (folder / 'frames').mkdir(parents=True, mode=0o700)
                wav = wave.open(str(folder / 'audio.wav'), 'wb')
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
            except Exception as exc:
                if self.trace:
                    self.trace.emit('sentry_recording', status='not_started',
                                    reason=str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__)
                return None
            self.current = {'folder': folder, 'frames': [], 'wav': wav, 'audio_at': None,
                            'began': self.clock(), 'wall': self.wall(), 'reason': reason}
            for at, data in self.frames:
                self._write_frame(at, data)
            for at, pcm in self.sound:
                if self.current['audio_at'] is None:
                    self.current['audio_at'] = at
                wav.writeframes(pcm)
            self.frames.clear()
            self.sound.clear()
        if self.trace:
            self.trace.emit('sentry_recording', status='started', folder=str(folder), reason=reason)
        return folder

    def photo(self):
        """The newest camera frame as a JPEG for the alert, or None."""
        with self.lock:
            latest = self.latest
        if latest is None or not 0 <= self.clock() - latest[0] <= 2.:
            return None
        try:
            return self.encode(latest[1], PHOTO_WIDTH, 85)
        except Exception as exc:
            if self.trace:
                self.trace.emit('sentry_recording', status='photo_failed', error_type=type(exc).__name__)
            return None

    def finish(self, details=None):
        """End the recording and join it into video.mov. Returns what was kept."""
        with self.lock:
            current, self.current = self.current, None
            if current is None:
                return None
            try:
                current['wav'].close()
            except Exception:
                pass
        folder, frames = current['folder'], current['frames']
        summary = {'folder': str(folder), 'reason': current['reason'],
                   'started': datetime.fromtimestamp(current['wall']).isoformat(timespec='seconds'),
                   'seconds': round(self.clock() - current['began'], 1), 'frames': len(frames), **(details or {})}
        if frames:
            try:
                offset = (current['audio_at'] - frames[0][0]) if current['audio_at'] is not None else 0.
                self.join(folder, frames, offset)
                shutil.rmtree(folder / 'frames', ignore_errors=True)
                for name in ('frames.ffconcat', 'audio.wav'):
                    (folder / name).unlink(missing_ok=True)
                summary['video'] = str(folder / 'video.mov')
            except Exception as exc:
                summary['video_failed'] = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
        try:
            (folder / 'event.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        except OSError as exc:
            summary['event_file_failed'] = type(exc).__name__
        if self.trace:
            self.trace.emit('sentry_recording', status='finished', **summary)
        return summary

    def save_photo(self, data):
        with self.lock:
            folder = self.current['folder'] if self.current else None
        if folder and data:
            try:
                (folder / 'photo.jpg').write_bytes(data)
            except OSError:
                pass

    def stop(self, details=None):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(3.)
            self._thread = None
        summary = self.finish(details)
        with self.lock:
            self.frames.clear()
            self.sound.clear()
            self.latest = None
        return summary
