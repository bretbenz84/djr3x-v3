"""A pretend robot for trying the Rex Call app or a client on a Mac with no robot: the real call code
(r3x.telepresence's listener, call loop and video sender) with a synthetic room and camera, and no
devices, servos or lights.

    TELEPRESENCE_TOKEN=... .venv/bin/python tools/telepresence_fake_robot.py [--listen 127.0.0.1:47800] [--allow 127.0.0.1/32]

The room is a quiet 220 Hz hum that swells every few seconds; the camera is a drawn test picture with
a moving bar and the robot's clock. The caller's voice is measured, not played: the session ends with
a summary of how much arrived and how loud it was. The call ends as the robot's does (hang-up, nobody
in 120 s, link gone 30 s) or on Ctrl-C.
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
from r3x.telepresence.protocol import parse_listen, parse_networks, valid_token  # noqa: E402
from r3x.telepresence.server import Server  # noqa: E402
from r3x.vision import Frame as VideoFrame  # noqa: E402

RATE = 16000
BLOCK = 512      # 32 ms, the robot microphone's block.
WIDTH, HEIGHT = 960, 540


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


class Camera:
    """Stands in for the camera's frame hub: a new drawn picture on every look."""
    def __init__(self):
        import numpy as np
        self.np = np
        self.count = 0

    @property
    def hub(self):
        return self

    def peek(self):
        import cv2
        np = self.np
        image = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
        image[:, :, 0] = np.linspace(40, 120, WIDTH, dtype=np.uint8)[None, :]
        image[:, :, 1] = np.linspace(20, 80, HEIGHT, dtype=np.uint8)[:, None]
        x = int((time.monotonic() * 200) % WIDTH)
        image[:, max(0, x - 20):x + 20] = (255, 0, 255)
        cv2.putText(image, f'fake robot  {time.strftime("%H:%M:%S")}  frame {self.count}', (30, 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 2)
        self.count += 1
        return VideoFrame(f'f{self.count}', 'fake', 'fake', time.monotonic(), WIDTH, HEIGHT, image.tobytes())


class Printer:
    def emit(self, event, **data):
        if event != 'telepresence_stats' or data.get('connected'):
            data.pop('link', None)
            print(f'{event} {json.dumps(data)}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--listen', default='127.0.0.1:47800')
    parser.add_argument('--allow', action='append', default=[])
    parser.add_argument('--no-video', action='store_true')
    args = parser.parse_args()
    token = Secrets.load(ScratchPaths.for_project(PROJECT).root).telepresence_token
    if not valid_token(token):
        raise SystemExit('Set TELEPRESENCE_TOKEN (or telepresence_token in local/secrets.toml), 32+ characters')
    host, port = parse_listen(args.listen)
    video = None if args.no_video else VideoStream(Camera())
    server = Server(host, port, parse_networks(args.allow or ['127.0.0.1/32']), token,
                    welcome=lambda: {'audio': {'rate': RATE, 'channels': 1, 'format': 's16le'}, 'fake': True,
                                     'video': None if video is None else {'width': WIDTH, 'height': HEIGHT, 'fps': 15,
                                                                          'format': 'jpeg'}})
    trace = Printer()
    server.trace = trace
    voice = Voice()
    call = Call(server, Room(), voice, trace, video=video)
    if video:
        video.trace = trace
        video.start()
    server.start()
    server.open()
    print(f'fake robot listening on {host}:{port}', flush=True)
    stop = threading.Event()
    reason = 'interrupted'
    try:
        reason = call.run(stop)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        if video:
            video.close()
    print(json.dumps({'ended': reason, 'voice_seconds_received': round(voice.seconds, 2),
                      'voice_seconds_loud': round(voice.loud_seconds, 2), 'voice_peak': voice.peak,
                      'video': video.summary() if video else None}), flush=True)


if __name__ == '__main__':
    main()
