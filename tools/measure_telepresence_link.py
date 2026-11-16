"""Stage 0 telepresence measurement: delay and stalls on the real link, with no devices.

On the robot (or any Mac at home), listen with the telepresence settings and token:
    .venv/bin/python tools/measure_telepresence_link.py serve
From off-site over WireGuard (the Mac with the same telepresence_token):
    .venv/bin/python tools/measure_telepresence_link.py call ROBOT_IP:PORT [--seconds 60] [--video-kb 40]

The caller sends what a call carries: a 20 ms audio frame 50 times a second, echoed back whole, and
a video-sized frame VIDEO_FPS times a second, answered with a small acknowledgement, over the same
wire format and sender as the program (r3x.telepresence.protocol). It reports round trips, the
longest gap between echoes and the sender's dropped audio, and saves the report under
local/telepresence/. Echo delay is network and queueing only: microphone, speaker and the jitter
buffer are not in it (half a round trip plus about 0.15 s is a rough mouth-to-ear estimate).
"""
from pathlib import Path
import argparse
import json
import os
import statistics
import sys
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from r3x.settings import ScratchPaths, Secrets, Settings  # noqa: E402
from r3x.telepresence.call import VIDEO_FPS  # noqa: E402
from r3x.telepresence.protocol import (Link, ProtocolError, connect, decode_json, decode_stamped,  # noqa: E402
                                       parse_listen, parse_networks, valid_token)
from r3x.telepresence.server import Server  # noqa: E402

AUDIO_BYTES = 640    # 20 ms at 16 kHz.
LATE_SECONDS = .25


def token(paths):
    value = Secrets.load(paths.root).telepresence_token
    if not valid_token(value):
        raise SystemExit('Set telepresence_token (python -m r3x telepresence-setup), the same on both Macs')
    return value


def serve(args, paths):
    settings_path = paths.root / 'settings.toml'
    settings = Settings.load(settings_path) if settings_path.exists() else Settings()
    listen = args.listen or settings.telepresence_listen
    allowed = args.allow or list(settings.telepresence_allowed_networks)
    if not listen or not allowed:
        raise SystemExit('Set telepresence_listen and telepresence_allowed_networks, or pass --listen and --allow')
    host, port = parse_listen(listen)

    class Echo:
        def emit(self, event, **data):
            print(event, json.dumps(data), flush=True)
    server = Server(host, port, parse_networks(allowed), token(paths), welcome=lambda: {'probe': True})
    server.trace = Echo()

    def on_frame(link, kind, payload):
        if kind == 'A':
            sequence, at, pcm = decode_stamped(payload)
            link.send_audio(sequence, at, pcm)
        elif kind == 'V':
            sequence, at, _ = decode_stamped(payload)
            link.send_json({'type': 'ack', 'seq': sequence, 't': at, 'size': len(payload)})
        else:
            message = decode_json(payload)
            if message['type'] == 'hangup':
                link.close('hangup')
    server.on_frame = on_frame
    server.start()
    server.open()
    print(f'listening on {host}:{port}; Ctrl-C to stop', flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


def call(args, paths):
    sock, welcome = connect(args.address, token(paths), 'link-probe')
    if not welcome.get('probe'):
        print('note: this is the telepresence program, not the probe; only audio echoes are missing', flush=True)
    audio, video, arrivals = [], [], []
    lock = threading.Lock()

    def on_frame(link, kind, payload):
        now = time.monotonic()
        if kind == 'A':
            _, at, _ = decode_stamped(payload)
            with lock:
                audio.append(now - at)
                arrivals.append(now)
        elif kind == 'J':
            message = decode_json(payload)
            if message['type'] == 'ack' and isinstance(message.get('t'), (int, float)):
                with lock:
                    video.append(now - message['t'])
    link = Link(sock, on_frame).start()
    blob = os.urandom(args.video_kb * 1024)
    began = time.monotonic()
    next_audio = next_video = began
    sequence = frames = 0
    print(f'measuring for {args.seconds:g} s...', flush=True)
    while time.monotonic() - began < args.seconds and not link.closed.is_set():
        now = time.monotonic()
        if now >= next_audio:
            link.send_audio(sequence, now, bytes(AUDIO_BYTES))
            sequence += 1
            next_audio += AUDIO_BYTES / 2 / 16000
        if now >= next_video:
            link.send_video(frames, now, blob)
            frames += 1
            next_video += 1 / args.fps
        time.sleep(max(0., min(next_audio, next_video) - time.monotonic()))
    time.sleep(1.)   # The last echoes.
    link.send_json({'type': 'hangup'})
    link.flush(1.)
    stats = dict(link.stats)
    link.close('done')

    def summary(values):
        if not values:
            return None
        values = sorted(values)
        pick = lambda share: round(1000 * values[min(len(values) - 1, int(share * len(values)))], 1)
        return {'samples': len(values), 'median_ms': pick(.5), 'p95_ms': pick(.95), 'p99_ms': pick(.99),
                'max_ms': round(1000 * values[-1], 1), 'late_share': round(sum(v > LATE_SECONDS for v in values) / len(values), 4)}
    gaps = [b - a for a, b in zip(arrivals, arrivals[1:])]
    report = {'evidence': 'live network, no devices', 'address': args.address, 'seconds': args.seconds,
              'audio_frames_sent': sequence, 'video_frames_sent': frames, 'video_kb': args.video_kb, 'fps': args.fps,
              'upload_mbit_s': round((sequence * AUDIO_BYTES + frames * len(blob)) * 8 / args.seconds / 1e6, 2),
              'audio_round_trip': summary(audio), 'video_ack_round_trip': summary(video),
              'longest_audio_gap_ms': round(1000 * max(gaps), 1) if gaps else None,
              'audio_gaps_over_100ms': sum(g > .1 for g in gaps), 'sender': stats}
    out = paths.root / 'telepresence'
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = out / time.strftime('link-%Y%m%d-%H%M%S.json')
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f'saved {path.relative_to(PROJECT)}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='mode', required=True)
    s = sub.add_parser('serve')
    s.add_argument('--listen', default='', help='IP:PORT (default: telepresence_listen)')
    s.add_argument('--allow', action='append', default=[], help='CIDR (default: telepresence_allowed_networks)')
    c = sub.add_parser('call')
    c.add_argument('address')
    c.add_argument('--seconds', type=float, default=60.)
    c.add_argument('--video-kb', type=int, default=40, help='Size of each video-sized frame')
    c.add_argument('--fps', type=float, default=VIDEO_FPS)
    args = parser.parse_args()
    paths = ScratchPaths.for_project(PROJECT)
    try:
        (serve if args.mode == 'serve' else call)(args, paths)
    except ProtocolError as exc:
        raise SystemExit(f'refused: {exc.reason}')


if __name__ == '__main__':
    main()
