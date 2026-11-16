"""Throwaway telepresence test client (stage 1, docs/TELEPRESENCE_PLAN.md); the Mac app replaces it.

    .venv/bin/python tools/telepresence_client.py ROBOT_IP:PORT [--input NAME] [--output NAME] [--no-video]

Plays the room, shows the camera (OpenCV window) and sends this Mac's microphone while unmuted. It
starts muted. In the video window: m mutes or unmutes, q hangs up. Without video: Enter toggles mute,
q then Enter hangs up. Use headphones: this client has no echo cancelling, so Rex's speaker would
otherwise hear the room twice. The token is telepresence_token in this checkout's local/secrets.toml
(or the TELEPRESENCE_TOKEN environment variable). Prints a summary when the call ends.
"""
from pathlib import Path
import argparse
import json
import queue
import statistics
import sys
import threading
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from r3x.settings import ScratchPaths, Secrets  # noqa: E402
from r3x.telepresence.protocol import AUDIO_RATE, Link, ProtocolError, connect, decode_json, decode_stamped  # noqa: E402
from r3x.telepresence.speaker import LiveSpeaker  # noqa: E402

BLOCK = 320          # 20 ms of microphone per frame.
CONNECT_FOR = 60.    # The robot may still be waking.


def percentile(values, share):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


class Client:
    def __init__(self, link, speaker):
        self.link, self.speaker = link, speaker
        self.muted = True
        self.lock = threading.Lock()
        self.pings, self.ping_id = {}, 0
        self.rtts, self.offsets, self.ages = [], [], []
        self.status = {}
        self.frame = None            # (age seconds, jpeg) newest
        self.frames = 0
        self.room_frames = 0
        self.sent_frames = 0
        self.ended = threading.Event()
        self.started = time.monotonic()

    def offset(self):
        with self.lock:
            return statistics.median(self.offsets[-15:]) if self.offsets else None

    def on_frame(self, link, kind, payload):
        if kind == 'A':
            _, _, pcm = decode_stamped(payload)
            self.speaker.write(pcm)
            self.room_frames += 1
        elif kind == 'V':
            _, at, jpeg = decode_stamped(payload)
            offset = self.offset()
            age = None if offset is None else time.monotonic() + offset - at
            if age is not None:
                self.ages.append(age)
            self.frame = (age, jpeg)
            self.frames += 1
        else:
            message = decode_json(payload)
            if message['type'] == 'ping':
                link.send_json({'type': 'pong', 'id': message.get('id'), 't': message.get('t')})
            elif message['type'] == 'pong':
                now = time.monotonic()
                with self.lock:
                    sent = self.pings.pop(message.get('id'), None)
                    if sent is not None and isinstance(message.get('robot_t'), (int, float)):
                        self.rtts.append(now - sent)
                        self.offsets.append(message['robot_t'] - (sent + now) / 2)
            elif message['type'] == 'status':
                self.status = message

    def on_closed(self, link, reason):
        print(f'link closed: {reason}', flush=True)
        self.ended.set()

    def ping(self):
        with self.lock:
            self.ping_id += 1
            self.pings[self.ping_id] = time.monotonic()
        self.link.send_json({'type': 'ping', 'id': self.ping_id, 't': time.monotonic()})

    def line(self):
        rtt = percentile(self.rtts[-10:], .5)
        age = percentile(self.ages[-30:], .5)
        robot = self.status.get('speaker', {})
        return (f"{'MUTED' if self.muted else 'TALKING'}  round trip {rtt and round(rtt * 1000)} ms"
                f"  video age {age and round(age * 1000)} ms  frames {self.frames}"
                f"  robot speaker underruns {robot.get('underruns')}  room underruns {self.speaker.stats['underruns']}")

    def summary(self):
        ms = lambda v: None if v is None else round(v * 1000, 1)
        seconds = time.monotonic() - self.started
        return {'seconds': round(seconds, 1), 'evidence': 'live link from this Mac',
                'round_trip_ms': {'median': ms(percentile(self.rtts, .5)), 'p95': ms(percentile(self.rtts, .95)),
                                  'max': ms(max(self.rtts) if self.rtts else None), 'samples': len(self.rtts)},
                'video': {'frames': self.frames, 'fps': round(self.frames / seconds, 1) if seconds else None,
                          'age_ms_median': ms(percentile(self.ages, .5)), 'age_ms_p95': ms(percentile(self.ages, .95))},
                'room_audio': {'frames': self.room_frames, **{k: round(v, 3) if isinstance(v, float) else v
                                                              for k, v in self.speaker.stats.items()}},
                'voice_frames_sent': self.sent_frames, 'link': dict(self.link.stats),
                'robot_status': self.status}


def microphone(name, client):
    import sounddevice as sd
    from r3x.adapters.audio import resolve_device
    index = resolve_device(sd.query_devices(), name, None, 'input') if name else None
    sequence = [0]

    def callback(data, frames, timing, status):
        if not client.muted:
            client.link.send_audio(sequence[0], time.monotonic(), bytes(data))
            sequence[0] += 1
            client.sent_frames += 1
    stream = sd.RawInputStream(device=index, samplerate=AUDIO_RATE, channels=1, dtype='int16',
                               blocksize=BLOCK, latency='low', callback=callback)
    stream.start()
    return stream


def video_loop(client):
    import cv2
    import numpy as np
    cv2.namedWindow('Rex', cv2.WINDOW_NORMAL)
    last = None
    while not client.ended.is_set():
        frame = client.frame
        if frame is not None and frame is not last:
            last = frame
            image = cv2.imdecode(np.frombuffer(frame[1], dtype=np.uint8), cv2.IMREAD_COLOR)
            if image is not None:
                cv2.putText(image, client.line()[:90], (10, 24), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 0, 255), 2)
                cv2.imshow('Rex', image)
        key = cv2.waitKey(15) & 0xFF
        if key == ord('m'):
            client.muted = not client.muted
            print('muted' if client.muted else 'talking', flush=True)
        elif key == ord('q'):
            break
    cv2.destroyAllWindows()


def terminal_loop(client):
    keys = queue.Queue()
    threading.Thread(target=lambda: [keys.put(l.strip()) for l in sys.stdin], daemon=True).start()
    while not client.ended.is_set():
        try:
            key = keys.get(timeout=.2)
        except queue.Empty:
            continue
        if key == 'q':
            break
        client.muted = not client.muted
        print('muted' if client.muted else 'talking', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('address', help='ROBOT_IP:PORT, as telepresence_listen on the robot')
    parser.add_argument('--input', default='', help='Microphone name on this Mac (default: the system input)')
    parser.add_argument('--output', default='', help='Speaker name on this Mac (default: the system output)')
    parser.add_argument('--no-video', action='store_true')
    args = parser.parse_args()
    token = Secrets.load(ScratchPaths.for_project(PROJECT).root).telepresence_token
    if not token:
        raise SystemExit('Set telepresence_token in local/secrets.toml (the same line as on the robot)')
    deadline = time.monotonic() + CONNECT_FOR
    while True:
        try:
            sock, welcome = connect(args.address, token, 'test-client')
            break
        except ProtocolError as exc:
            raise SystemExit(f'refused: {exc.reason}')
        except OSError as exc:
            if time.monotonic() > deadline:
                raise SystemExit(f'could not connect: {type(exc).__name__}')
            print('waiting for the robot...', flush=True)
            time.sleep(2)
    print('connected: ' + json.dumps(welcome), flush=True)
    speaker = LiveSpeaker(args.output, name='client_speaker')
    speaker.start()
    client = Client(None, speaker)
    client.link = Link(sock, client.on_frame, on_closed=client.on_closed)
    client.link.start()
    mic = microphone(args.input, client)
    ticker = threading.Event()

    def tick():
        count = 0
        while not ticker.wait(1.):
            client.ping()
            count += 1
            if count % 5 == 0:
                print(client.line(), flush=True)
    threading.Thread(target=tick, daemon=True).start()
    print('muted; ' + ("press m in the video window to talk, q to hang up" if welcome.get('video') and not args.no_video
                       else "press Enter to talk, q then Enter to hang up"), flush=True)
    try:
        if welcome.get('video') and not args.no_video:
            video_loop(client)
        else:
            terminal_loop(client)
    except KeyboardInterrupt:
        pass
    finally:
        ticker.set()
        client.link.send_json({'type': 'hangup'})
        client.link.flush(1.)
        client.link.close('hangup_sent')
        mic.stop(); mic.close()
        speaker.close()
        print(json.dumps(client.summary(), indent=2))


if __name__ == '__main__':
    main()
