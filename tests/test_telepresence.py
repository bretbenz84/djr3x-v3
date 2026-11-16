"""Telepresence contracts (stages 0-1, docs/TELEPRESENCE_PLAN.md): the wire format and sign-in, the
listener's refusals, the sender's bounded queues, the live speaker's jitter buffer, the call's endings,
the capture-only camera, the lights and the program's lifecycle. Fake sockets, devices and clocks only:
no network, audio, camera, serial or model."""
from pathlib import Path
import io
import json
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from r3x.capture import Frame as AudioFrame
from r3x.contracts import ProviderFailure
from r3x.providers import Capability
from r3x.settings import Secrets, Settings
from r3x.telepresence import protocol
from r3x.telepresence.call import Call, Resources, VideoStream
from r3x.telepresence.protocol import (Link, ProtocolError, encode, encode_json, encode_stamped, decode_stamped,
                                       parse_listen, parse_networks, read_frame, sign_in, sign_in_client,
                                       source_allowed)
from r3x.telepresence.server import Server
from r3x.telepresence.speaker import LiveSpeaker
from r3x.vision import Frame as VideoFrame

TOKEN = 'synthetic-telepresence-token-0123456789abcdef'
NETWORKS = parse_networks(['10.8.0.0/24'])


# ---------------------------------------------------------------- fakes

class Pipe:
    def __init__(self):
        self.data, self.closed, self.cond = bytearray(), False, threading.Condition()


class FakeSocket:
    """One end of an in-memory stream; its peer's recv sees b'' once it closes."""
    def __init__(self, incoming, outgoing):
        self.incoming, self.outgoing = incoming, outgoing
        self.timeout, self.closed = None, False

    def settimeout(self, value):
        self.timeout = value

    def sendall(self, data):
        if self.closed or self.outgoing.closed:
            raise BrokenPipeError()
        with self.outgoing.cond:
            self.outgoing.data += data
            self.outgoing.cond.notify_all()

    def recv(self, size):
        with self.incoming.cond:
            if not self.incoming.cond.wait_for(lambda: self.incoming.data or self.incoming.closed or self.closed,
                                               self.timeout):
                raise TimeoutError()
            if self.closed:
                raise OSError('closed')
            chunk = bytes(self.incoming.data[:size])
            del self.incoming.data[:size]
            return chunk

    def _end(self):
        self.closed = True
        for pipe in (self.incoming, self.outgoing):
            with pipe.cond:
                pipe.closed = True
                pipe.cond.notify_all()

    shutdown = lambda self, how: self._end()
    close = _end


def socket_pair():
    a, b = Pipe(), Pipe()
    return FakeSocket(a, b), FakeSocket(b, a)


def wait_until(condition, seconds=2.):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError('condition never held')
        time.sleep(.005)


class Clock:
    def __init__(self, now=100.):
        self.now = now

    def __call__(self):
        return self.now


class Recorder:
    def __init__(self):
        self.rows = []

    def emit(self, event, **data):
        self.rows.append({'event': event, **data})

    def events(self, name):
        return [r for r in self.rows if r['event'] == name]


def signed_in_pair(token=TOKEN):
    """(server_link_socket, client_socket, client_welcome) after a real sign-in over the fake stream."""
    robot, client = socket_pair()
    answer = {}
    thread = threading.Thread(target=lambda: answer.update(sign_in(client, token, 'checks')))
    thread.start()
    name = sign_in_client(robot, TOKEN, {'audio': {'rate': 16000}}, nonce=b'n' * 16)
    thread.join(2)
    return robot, client, answer, name


# ---------------------------------------------------------------- wire format and access

class ProtocolTests(unittest.TestCase):
    def test_stamped_frames_round_trip_and_oversize_or_unknown_frames_end_the_read(self):
        robot, client = socket_pair()
        client.sendall(encode_stamped('A', 7, 12.5, b'\x01\x00' * 4))
        kind, payload = read_frame(robot)
        self.assertEqual((kind, decode_stamped(payload)), ('A', (7, 12.5, b'\x01\x00' * 4)))
        client.sendall(protocol.HEADER.pack(ord('A'), protocol.LIMITS[ord('A')] + 1))
        with self.assertRaises(ProtocolError) as caught:
            read_frame(robot)
        self.assertEqual(caught.exception.reason, 'frame_too_large')
        client.sendall(protocol.HEADER.pack(ord('X'), 0))
        with self.assertRaises(ProtocolError):
            read_frame(robot)
        with self.assertRaises(ValueError):
            encode('A', bytes(protocol.LIMITS[ord('A')] + 1))

    def test_sign_in_welcomes_the_token_holder_and_never_sends_the_token(self):
        robot, client = socket_pair()
        seen = bytearray()
        original = client.sendall
        client.sendall = lambda data: (seen.extend(data), original(data))
        answer = {}
        thread = threading.Thread(target=lambda: answer.update(sign_in(client, TOKEN, 'checks')))
        thread.start()
        name = sign_in_client(robot, TOKEN, {'video': None}, nonce=b'n' * 16)
        thread.join(2)
        self.assertEqual(name, 'checks')
        self.assertEqual((answer['type'], answer['video']), ('welcome', None))
        self.assertNotIn(TOKEN.encode(), bytes(seen))
        self.assertIsNone(robot.timeout)   # Back to blocking reads once signed in.

    def test_wrong_token_is_refused_and_the_client_hears_why(self):
        robot, client = socket_pair()
        failure = {}

        def caller():
            try:
                sign_in(client, 'another-synthetic-token-0123456789abcdef', 'checks')
            except ProtocolError as exc:
                failure['reason'] = exc.reason
        thread = threading.Thread(target=caller)
        thread.start()
        with self.assertRaises(ProtocolError) as caught:
            sign_in_client(robot, TOKEN, {}, nonce=b'n' * 16)
        protocol.refuse(robot, caught.exception.reason)
        thread.join(2)
        self.assertEqual(caught.exception.reason, 'wrong_token')
        self.assertEqual(failure['reason'], 'wrong_token')

    def test_sign_in_reads_only_small_json_and_times_out(self):
        robot, client = socket_pair()
        client.sendall(encode_stamped('A', 0, 0., bytes(64)))   # Audio before signing in.
        with self.assertRaises(ProtocolError) as caught:
            sign_in_client(robot, TOKEN, {}, nonce=b'n' * 16)
        self.assertEqual(caught.exception.reason, 'unexpected_frame_kind')
        robot, client = socket_pair()
        with patch.object(protocol, 'HANDSHAKE_SECONDS', .05), self.assertRaises(ProtocolError) as caught:
            sign_in_client(robot, TOKEN, {}, nonce=b'n' * 16)   # The client never answers.
        self.assertEqual(caught.exception.reason, 'sign_in_timed_out')

    def test_listen_address_networks_and_sources(self):
        self.assertEqual(parse_listen('192.168.1.50:47800'), ('192.168.1.50', 47800))
        self.assertEqual(parse_listen('[fd00::5]:47800'), ('fd00::5', 47800))
        for bad in ('0.0.0.0:47800', '[::]:47800', '192.168.1.50:80', 'rex.local:47800', 'fd00::5:47800',
                    '192.168.1.50', ' 192.168.1.50:47800', '192.168.1.50:99999'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_listen(bad)
        self.assertTrue(source_allowed('10.8.0.2', NETWORKS))
        self.assertTrue(source_allowed('::ffff:10.8.0.2', NETWORKS))   # An IPv6 listener's view of IPv4.
        self.assertFalse(source_allowed('10.8.1.2', NETWORKS))
        self.assertFalse(source_allowed('not-an-address', NETWORKS))
        with self.assertRaises(ValueError):
            parse_networks(['10.8.0.0/24'] * 9)

    def test_settings_need_networks_with_an_address_and_the_token_is_optional_but_private(self):
        Settings(telepresence_listen='10.0.0.5:47800', telepresence_allowed_networks=['10.8.0.0/24'])
        with self.assertRaises(ValueError):
            Settings(telepresence_listen='10.0.0.5:47800')
        with self.assertRaises(ValueError):
            Settings(telepresence_allowed_networks=['300.1.1.0/24'])
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            folder = Path(folder).resolve()
            Path(folder, 'secrets.toml').write_text(f'telepresence_token = "{TOKEN}"\n')
            secrets = Secrets.load(folder)
            self.assertEqual(secrets.telepresence_token, TOKEN)
            self.assertNotIn(TOKEN, repr(secrets))
            self.assertNotIn('telepresence_token', secrets.readiness())


# ---------------------------------------------------------------- the sender

class LinkTests(unittest.TestCase):
    def test_json_goes_before_audio_before_the_newest_video_frame_only(self):
        robot, client = socket_pair()
        link = Link(robot, lambda *a: None)
        link.send_video(1, 1., b'old')
        link.send_video(2, 2., b'new')
        link.send_audio(0, 0., b'\0\0')
        link.send_json({'type': 'status'})
        link.start()
        kinds = [read_frame(client) for _ in range(3)]
        self.assertEqual([k for k, _ in kinds], ['J', 'A', 'V'])
        self.assertEqual(decode_stamped(kinds[2][1])[2], b'new')
        self.assertEqual(link.stats['video_superseded'], 1)
        link.close('done')

    def test_a_backed_up_link_drops_the_oldest_audio_instead_of_adding_delay(self):
        robot, client = socket_pair()
        link = Link(robot, lambda *a: None)   # Not started: nothing drains.
        frame = bytes(1024)                     # 32 ms of 16 kHz audio.
        for sequence in range(40):              # 1.28 s offered.
            link.send_audio(sequence, float(sequence), frame)
        self.assertLessEqual(link.pending()['audio_seconds'], protocol.AUDIO_BACKLOG + .05)
        self.assertGreater(link.stats['audio_dropped'], 20)
        link.start()
        first = decode_stamped(read_frame(client)[1])[0]
        self.assertGreater(first, 20)           # What is left is the newest.
        link.close('done')

    def test_close_wakes_both_threads_reports_once_and_the_peer_sees_the_end(self):
        robot, client = socket_pair()
        closed = []
        link = Link(robot, lambda *a: None, on_closed=lambda l, reason: closed.append(reason)).start()
        link.close('shutdown')
        link.close('again')
        self.assertEqual(closed, ['shutdown'])
        self.assertFalse(link.reader.is_alive() or link.writer.is_alive())
        with self.assertRaises(protocol.ConnectionClosed):
            read_frame(client)

    def test_a_broken_frame_from_the_peer_closes_with_the_reason(self):
        robot, client = socket_pair()
        closed = []
        link = Link(robot, lambda *a: None, on_closed=lambda l, reason: closed.append(reason)).start()
        client.sendall(protocol.HEADER.pack(ord('Z'), 0))
        wait_until(lambda: closed)
        self.assertEqual(closed, ['protocol:unexpected_frame_kind'])

    def test_a_failing_handler_closes_the_link_with_its_reason(self):
        robot, client = socket_pair()
        closed = []
        link = Link(robot, Mock(side_effect=KeyError('x')), on_closed=lambda l, reason: closed.append(reason)).start()
        client.sendall(encode_json({'type': 'ping'}))
        wait_until(lambda: closed)
        self.assertEqual(closed, ['handler_failed:KeyError'])

    def test_flush_sends_a_hang_up_before_close(self):
        robot, client = socket_pair()
        link = Link(robot, lambda *a: None).start()
        link.send_json({'type': 'hangup'})
        self.assertTrue(link.flush(1.))
        link.close('hangup_sent')
        self.assertEqual(json.loads(read_frame(client)[1])['type'], 'hangup')


# ---------------------------------------------------------------- the listener

class ServerTests(unittest.TestCase):
    def setUp(self):
        self.server = Server('10.0.0.5', 47800, NETWORKS, TOKEN, open_listener=Mock(), nonce=lambda: b'n' * 16)
        self.server.trace = self.trace = Recorder()
        self.clients = []
        self.server.on_client = lambda link, source, name: self.clients.append((link, source, name))

    def tearDown(self):
        link = self.server.active()
        if link:
            link.close('test_end')

    def admit(self, source, token=TOKEN):
        robot, client = socket_pair()
        result = {}

        def caller():
            try:
                result['welcome'] = sign_in(client, token, 'checks')
            except ProtocolError as exc:
                result['refused'] = exc.reason
            except OSError:
                result['closed'] = True
        thread = threading.Thread(target=caller)
        thread.start()
        self.server._admit(robot, source)
        thread.join(2)
        return result, client

    def test_outside_the_allowed_networks_is_closed_unanswered(self):
        result, client = self.admit('192.168.1.20')
        self.assertEqual(result, {'refused': 'closed_during_sign_in'})   # Closed with nothing said: no challenge.
        self.assertEqual(self.trace.events('telepresence_client_refused')[0]['reason'], 'source_not_allowed')
        self.assertEqual(self.clients, [])

    def test_wrong_token_is_refused_and_the_right_one_becomes_the_client(self):
        result, _ = self.admit('10.8.0.2', token='another-synthetic-token-0123456789abcdef')
        self.assertEqual(result['refused'], 'wrong_token')
        result, client = self.admit('10.8.0.2')
        self.assertEqual(result['welcome']['type'], 'welcome')
        link, source, name = self.clients[0]
        self.assertEqual((source, name), ('10.8.0.2', 'checks'))
        self.assertIs(self.server.active(), link)

    def test_a_second_client_while_one_is_signed_in_is_told_busy(self):
        self.admit('10.8.0.2')
        result, _ = self.admit('10.8.0.3')
        self.assertEqual(result['refused'], 'busy')
        self.assertEqual(len(self.clients), 1)

    def test_frames_reach_the_session_and_the_end_is_reported(self):
        frames, ends = [], []
        self.server.on_frame = lambda link, kind, payload: frames.append(kind)
        self.server.on_closed = lambda link, reason: ends.append(reason)
        _, client = self.admit('10.8.0.2')
        client.sendall(encode_json({'type': 'ping', 'id': 1, 't': 0}))
        wait_until(lambda: frames)
        client.close()
        wait_until(lambda: ends)
        self.assertEqual((frames, ends), (['J'], ['peer_closed']))
        self.assertIsNone(self.server.active())
        self.assertEqual(self.trace.events('telepresence_link_closed')[0]['reason'], 'peer_closed')


# ---------------------------------------------------------------- the live speaker

class FakeStream:
    def __init__(self):
        self.started = self.aborted = self.closed = False

    def start(self): self.started = True
    def abort(self): self.aborted = True
    def close(self): self.closed = True


class Stop(Exception):
    pass


class SpeakerTests(unittest.TestCase):
    def setUp(self):
        self.stream = FakeStream()
        self.speaker = LiveSpeaker(open_stream=lambda index, callback, finished: (self.stream, Stop),
                                   resolve=lambda name: None, clock=Clock())
        self.speaker.start()

    def play(self, frames=320):
        out = bytearray(frames * 2)
        self.speaker._callback(out, frames, SimpleNamespace(outputBufferDacTime=1., currentTime=.99), None)
        return bytes(out)

    def test_silence_until_enough_is_buffered_then_the_voice(self):
        voice = b'\x10\x00' * 320                          # 20 ms
        self.speaker.write(voice)
        self.assertEqual(self.play(), bytes(640))          # Still priming.
        for _ in range(6):
            self.speaker.write(voice)                      # 140 ms buffered.
        self.assertEqual(self.play(), voice)
        self.assertEqual(self.speaker.stats['underruns'], 0)

    def test_running_dry_counts_an_underrun_and_primes_again(self):
        self.speaker.write(b'\x10\x00' * 2000)             # 125 ms
        self.play(); self.play(); self.play(); self.play(); self.play(); self.play()
        self.play()                                        # Short: 5 ms left.
        self.assertEqual(self.speaker.stats['underruns'], 1)
        self.speaker.write(b'\x10\x00' * 320)
        self.assertEqual(self.play(), bytes(640))          # Priming again, not crackling.

    def test_a_backlog_is_cut_to_the_start_level_so_delay_never_builds(self):
        self.speaker.write(b'\x10\x00' * 16000)            # One second at once.
        self.assertAlmostEqual(self.speaker.buffered_seconds(), 0.12, places=3)
        self.assertAlmostEqual(self.speaker.stats['dropped_seconds'], .88, places=2)

    def test_the_lights_hear_what_plays_and_never_stop_it(self):
        heard = []
        self.speaker.on_pcm = lambda data, at: heard.append((len(data), at))
        self.play()
        self.assertEqual(heard, [(640, 100.01)])
        self.speaker.on_pcm = Mock(side_effect=RuntimeError('lights'))
        self.play()
        self.assertEqual(self.speaker.stats['callback_errors'], 1)

    def test_close_stops_on_a_silent_buffer_and_aborts_only_a_stalled_stream(self):
        self.speaker.cancel()
        with self.assertRaises(Stop):
            self.play()
        self.speaker.finished.set()
        self.speaker.close()
        self.assertTrue(self.stream.closed)
        self.assertFalse(self.stream.aborted)
        stalled = FakeStream()
        speaker = LiveSpeaker(open_stream=lambda *a: (stalled, Stop), resolve=lambda name: None)
        speaker.start()
        speaker.close()                                    # No callback ever came.
        self.assertTrue(stalled.aborted and stalled.closed)


# ---------------------------------------------------------------- the call

class FakeMic:
    def __init__(self, clock, frames=()):
        self.clock, self.frames, self.dropped = clock, list(frames), 0

    def read(self):
        self.clock.now += .1
        return self.frames.pop(0) if self.frames else None


class FakeSpeaker:
    def __init__(self):
        self.written, self.cleared = [], 0
        self.stats = {'underruns': 0, 'dropped_seconds': 0.}

    def write(self, pcm): self.written.append(pcm)
    def clear(self): self.cleared += 1
    def buffered_seconds(self): return 0.


class FakeLink:
    def __init__(self, clock):
        self.clock, self.audio, self.json, self.video = clock, [], [], []
        self.closed = threading.Event()
        self.last_heard = clock()
        self.reason = None
        self.stats = {}
        self.on_closed = None

    def send_audio(self, seq, at, pcm): self.audio.append((seq, at, pcm))
    def send_json(self, message): self.json.append(message)
    def send_video(self, seq, at, data): self.video.append((seq, at, data))
    def stalled_for(self, now): return 0.

    def close(self, reason='closed'):
        if not self.closed.is_set():
            self.closed.set()
            self.reason = reason
            self.on_closed(self, reason)


class CallTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.server = SimpleNamespace()
        self.speaker = FakeSpeaker()
        self.trace = Recorder()
        self.resources = Resources(self.clock, usage=lambda pids: {}, rusage=lambda who: SimpleNamespace(
            ru_utime=1., ru_stime=0., ru_maxrss=200 * 1024 * 1024))

    def call(self, mic, **kw):
        call = Call(self.server, mic, self.speaker, self.trace, clock=self.clock, resources=self.resources,
                    connect_wait=5., reconnect=3., **kw)
        return call

    def connect(self, call):
        link = FakeLink(self.clock)
        link.on_closed = self.server.on_closed
        self.server.on_client(link, '10.8.0.2', 'checks')
        return link

    def run_with(self, call, during=lambda now: None):
        stop = threading.Event()
        original = call.mic.read

        def read():
            frame = original()
            during(self.clock.now)
            return frame
        call.mic.read = read
        return call.run(stop)

    def test_nobody_connecting_ends_the_call_after_the_wait(self):
        call = self.call(FakeMic(self.clock))
        self.assertEqual(self.run_with(call), 'nobody_connected')
        self.assertGreaterEqual(self.clock.now, 105.)

    def test_room_audio_goes_out_only_while_connected_and_the_voice_reaches_the_speaker(self):
        pcm = b'\x01\x00' * 512
        frames = [AudioFrame(pcm, 100. + i, 100.03 + i) for i in range(3)]
        mic = FakeMic(self.clock, frames)
        call = self.call(mic)
        self.server.on_frame(None, 'A', encode_stamped('A', 0, 0., pcm)[5:])   # No link yet: ignored.
        mic.frames = frames[:1]
        mic.read()                                                         # Heard with nobody connected.
        mic.frames = frames[1:]
        link = self.connect(call)
        self.server.on_frame(link, 'A', encode_stamped('A', 0, 0., b'\x02\x00' * 320)[5:])
        self.server.on_frame(link, 'A', encode_stamped('A', 1, 0., b'\x02' * 3)[5:])          # Odd: dropped.
        self.server.on_frame(link, 'J', json.dumps({'type': 'hangup'}).encode())
        self.assertEqual(self.run_with(call), 'hangup')
        self.assertEqual([seq for seq, _, _ in link.audio], [])          # Hung up before the loop sent any.
        self.assertEqual(self.speaker.written, [b'\x02\x00' * 320])
        self.assertEqual(call.bad_frames, 1)

    def test_mic_frames_stream_with_sequence_numbers_and_pings_carry_status(self):
        pcm = b'\x01\x00' * 512
        mic = FakeMic(self.clock, [AudioFrame(pcm, 100. + i, 100.03 + i) for i in range(20)])
        call = self.call(mic)
        link = self.connect(call)

        def during(now):
            if now > 101.6 and not link.closed.is_set():
                link.json and self.server.on_frame(link, 'J', json.dumps(
                    {'type': 'pong', 'id': link.json[0]['id']}).encode())
                self.server.on_frame(link, 'J', json.dumps({'type': 'hangup'}).encode())
        self.assertEqual(self.run_with(call, during), 'hangup')
        self.assertEqual([seq for seq, _, _ in link.audio], list(range(len(link.audio))))
        self.assertGreater(len(link.audio), 10)
        self.assertEqual({m['type'] for m in link.json}, {'ping', 'status'})
        status = next(m for m in link.json if m['type'] == 'status')
        self.assertIn('underruns', status['speaker'])

    def test_a_robot_ping_is_answered_and_client_video_ends_the_link(self):
        call = self.call(FakeMic(self.clock))
        link = self.connect(call)
        self.server.on_frame(link, 'J', json.dumps({'type': 'ping', 'id': 3, 't': 1.5}).encode())
        self.assertEqual(link.json[-1], {'type': 'pong', 'id': 3, 't': 1.5, 'robot_t': 100.})
        self.server.on_frame(link, 'J', json.dumps({'type': 'ping', 'id': [1], 't': 1.5}).encode())
        self.assertEqual(len(link.json), 1)   # An id that is not a whole number is not answered.
        with self.assertRaises(ProtocolError):
            self.server.on_frame(link, 'V', encode_stamped('V', 0, 0., b'jpeg')[5:])

    def test_a_dropped_link_may_come_back_and_one_that_stays_gone_parks_him(self):
        call = self.call(FakeMic(self.clock))
        link = self.connect(call)
        state = {'dropped': False, 'back': False}

        def during(now):
            if not state['dropped'] and now > 101.:
                state['dropped'] = True
                link.close('peer_closed')
            elif state['dropped'] and not state['back'] and now > 103.:   # Back within the 3 s window.
                state['back'] = True
                state['link'] = self.connect(call)
            elif state['back'] and now > 104. and not state['link'].closed.is_set():
                state['link'].close('peer_closed')
        self.assertEqual(self.run_with(call, during), 'link_lost')
        self.assertGreaterEqual(self.clock.now, 107.)                       # 104 + 3 s, not 101 + 3 s.
        self.assertEqual(self.speaker.cleared, 2)                           # Nothing stale plays on.

    def test_a_silent_client_is_disconnected(self):
        call = self.call(FakeMic(self.clock))
        link = self.connect(call)
        link.last_heard = 0.
        self.assertEqual(self.run_with(call), 'link_lost')
        self.assertEqual(link.reason, 'silent')

    def test_stats_rows_hold_no_audio_and_say_what_the_process_costs(self):
        call = self.call(FakeMic(self.clock), )
        call.connect_wait = 12.
        self.run_with(call)
        row = self.trace.events('telepresence_stats')[0]
        self.assertEqual(row['resources']['peak_rss_mb'], 200.)
        self.assertNotIn('pcm', json.dumps(row))

    def test_video_sends_each_new_frame_once_to_the_connected_link_only(self):
        frames = iter([VideoFrame('a', 'cam', 's', 1., 2, 2, bytes(12))] * 3 + [VideoFrame('b', 'cam', 's', 2., 2, 2, bytes(12))])
        camera = SimpleNamespace(hub=SimpleNamespace(peek=lambda: next(frames, None)))
        video = VideoStream(camera, fps=1000., encode=lambda frame: frame.frame_id.encode())
        link = FakeLink(self.clock)
        video.target = lambda: link
        video.start()
        wait_until(lambda: len(link.video) == 2)
        video.close()
        self.assertEqual([data for _, _, data in link.video], [b'a', b'b'])
        self.assertEqual([seq for seq, _, _ in link.video], [0, 1])


# ---------------------------------------------------------------- camera, lights and the program

class CaptureOnlyCameraTests(unittest.TestCase):
    def test_no_face_models_and_ffmpeg_scales_the_frames(self):
        from r3x.adapters.camera import Camera
        camera = Camera('Fixture camera', None, width=1920, height=1080, framerate=30., output_width=960)
        self.assertIsNone(camera.encoder)
        self.assertEqual((camera.frame_width, camera.frame_height), (960, 540))
        self.assertEqual((camera.capability.model, camera.capability.dependencies), (None, ()))
        listing = SimpleNamespace(stderr='AVFoundation video devices:\n[0] Fixture camera\nAVFoundation audio devices:')
        with patch('subprocess.run', return_value=listing), patch('subprocess.Popen') as popen, \
                patch('shutil.which', return_value='/fixture/ffmpeg'), patch('threading.Thread') as thread:
            thread.return_value.start.side_effect = camera.ready.set
            camera.start()
        command = popen.call_args.args[0]
        self.assertEqual(command[command.index('-vf') + 1], 'scale=960:540')
        self.assertEqual(command[command.index('-video_size') + 1], '1920x1080')
        self.assertEqual(thread.call_count, 1)                       # The reader only: no face analysis.

    def test_the_first_frame_is_ready_and_frames_carry_the_scaled_size(self):
        from r3x.adapters.camera import Camera
        camera = Camera('Fixture', None, width=320, height=240, output_width=160)
        size = 160 * 120 * 3
        camera.process = Mock(stdout=io.BytesIO(b'a' * size + b'b' * size))
        camera.hub.publish = Mock()
        camera._read()
        frames = [c.args[0] for c in camera.hub.publish.call_args_list]
        self.assertEqual([(f.width, f.height) for f in frames], [(160, 120)] * 2)
        self.assertTrue(camera.ready.is_set())
        self.assertEqual(camera.failure, 'CameraStreamEnded')
        camera.close()                                               # No encoder to close.

    def test_a_scaled_size_needs_a_capture_only_camera(self):
        from r3x.adapters.camera import Camera
        for kwargs in ({'model_path': Path('/models'), 'output_width': 960}, {'model_path': None, 'output_width': 961},
                       {'model_path': None, 'output_width': 2000}):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                Camera('Fixture', kwargs['model_path'], width=1280, height=720, output_width=kwargs['output_width'])


class LightTests(unittest.TestCase):
    def test_magenta_eyes_and_a_mouth_that_follows_the_voice_for_the_whole_call(self):
        from tests.test_leds import rig
        from r3x.leds import TELEPRESENCE_EYES
        led, strips, clock = rig()
        led.telepresence()
        head, chest = strips['head'].commands, strips['chest'].commands
        self.assertIn('EYE:' + ','.join(map(str, TELEPRESENCE_EYES)), head)
        self.assertEqual((head[-1], chest[-1]), ('SPEAK:neutral', 'SPEAK:neutral'))
        led.pcm(b'\xff\x3f' * 512, clock())                          # A loud voice playing now.
        clock.at += .01
        led.tick()
        self.assertTrue(head[-1].startswith('SPEAK_LEVEL:') and int(head[-1].split(':')[1]) > 100)
        before = len([c for c in head if c.startswith('EYE:')])
        clock.at += 3.1
        led.tick()
        self.assertEqual(len([c for c in head if c.startswith('EYE:')]), before + 1)   # Refreshed while it talks.
        led.close()
        self.assertTrue(any(c.startswith('FADEOFF') for c in chest))

    def test_refused_before_the_board_is_ready(self):
        from r3x.leds import LEDs
        with self.assertRaises(ProviderFailure):
            LEDs('/dev/cu.fixture').telepresence()


class Part:
    """A provider double that records the order of its lifecycle calls."""
    def __init__(self, name, log, fail=False, required=True):
        self.capability = Capability(name, 'fake', 'checks', required=required)
        self.log, self.fail, self.trace = log, fail, None

    def start(self):
        self.log.append(('start', self.capability.name))
        if self.fail:
            raise ProviderFailure('Synthetic')

    def cancel(self): self.log.append(('cancel', self.capability.name))
    def close(self): self.log.append(('close', self.capability.name))


class ProgramTests(unittest.TestCase):
    def settings(self):
        return Settings(input_device_name='Fixture mic', telepresence_listen='10.0.0.5:47800',
                        telepresence_allowed_networks=['10.8.0.0/24'])

    def project(self, folder, token=TOKEN):
        folder = Path(folder).resolve()
        root = folder / 'local'
        root.mkdir()
        Path(folder, 'pyproject.toml').write_text('')
        if token:
            (root / 'secrets.toml').write_text(f'telepresence_token = "{token}"\n')
        return folder

    def test_compose_opens_nothing_and_loads_no_model(self):
        from r3x.telepresence.program import compose
        settings = Settings(input_device_name='Fixture mic', servo_port='/dev/cu.servo', led_port='/dev/cu.leds',
                            camera_enabled=True, camera_device_name='Fixture camera', camera_width=1920,
                            camera_height=1080, telepresence_listen='10.0.0.5:47800',
                            telepresence_allowed_networks=['10.8.0.0/24'])
        before = set(sys.modules)
        providers, parts = compose(settings, Secrets(telepresence_token=TOKEN))
        loaded = set(sys.modules) - before
        self.assertEqual([p.capability.name for p in providers],
                         ['telepresence_speaker', 'robot_leds', 'robot_servos', 'capture', 'camera',
                          'telepresence_video', 'telepresence_link'])
        self.assertFalse({m for m in loaded if m.startswith(('r3x.adapters.asr', 'r3x.adapters.voice',
                          'r3x.adapters.hosted', 'r3x.adapters.claude', 'r3x.session', 'r3x.memory', 'onnxruntime'))})
        self.assertIsNone(parts['camera'].encoder)
        self.assertIs(parts['speaker'].on_pcm.__self__, parts['leds'])
        self.assertEqual(parts['robot'].before_shutdown, parts['leds'].cancel)

    def test_readiness_names_what_is_missing_before_any_device_opens(self):
        from r3x.telepresence.program import readiness
        missing = readiness(Settings(camera_enabled=True, camera_device_name='cam'), Secrets(), which=lambda name: None)
        self.assertEqual(missing, ['ffmpeg', 'microphone_selection', 'telepresence_listen', 'telepresence_token'])
        self.assertEqual(readiness(self.settings(), Secrets(telepresence_token='short')), ['telepresence_token'])

    def run_program(self, folder, parts_log, *, fail=None, handoff=None):
        from r3x.telepresence import program
        clock = Clock()
        log = parts_log
        robot = Part('robot_servos', log, fail == 'robot_servos')
        robot.open_eyes = lambda: log.append(('open_eyes',))
        robot.telepresent = False
        robot.telepresence_stance = lambda cancel: log.append(('stance',))   # No head control: none is offered.
        leds = Part('robot_leds', log)
        leds.telepresence = lambda: log.append(('telepresence_lights',))
        speaker = Part('telepresence_speaker', log)
        speaker.stats, speaker.clear = {'underruns': 0, 'dropped_seconds': 0.}, lambda: None
        speaker.buffered_seconds = lambda: 0.
        mic = Part('capture', log)
        mic.read = lambda: (setattr(clock, 'now', clock.now + 200.), None)[1]   # Nobody connects.
        server = Part('telepresence_link', log)
        parts = {'speaker': speaker, 'mic': mic, 'leds': leds, 'robot': robot, 'camera': None, 'video': None,
                 'server': server}
        providers = (speaker, leds, robot, mic, server)
        handoff = handoff or Mock()
        code = program.run(self.settings(), Path(folder), compose=lambda s, secrets: (providers, parts),
                           handoff=handoff, display=lambda record: None, clock=clock)
        return code, handoff

    def test_a_call_wakes_opens_the_visor_lights_up_and_parks_in_reverse(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            log = []
            code, handoff = self.run_program(project, log)
            self.assertEqual(code, 0)
            starts = [name for op, *name in log if op == 'start']
            self.assertEqual([n[0] for n in starts], ['telepresence_speaker', 'robot_leds', 'robot_servos',
                                                      'capture', 'telepresence_link'])
            self.assertLess(log.index(('start', 'telepresence_link')), log.index(('open_eyes',)))
            self.assertLess(log.index(('open_eyes',)), log.index(('stance',)))   # The visor opens, then the stance.
            self.assertLess(log.index(('stance',)), log.index(('telepresence_lights',)))
            closes = [n for op, n in (e for e in log if len(e) == 2) if op == 'close']
            self.assertEqual(closes, ['telepresence_link', 'capture', 'robot_servos', 'robot_leds', 'telepresence_speaker'])
            handoff.acquire.assert_called_once()
            handoff.close.assert_called_once()
            rows = [json.loads(l) for l in next((project / 'local/sessions').glob('*.jsonl')).read_text().splitlines()]
            shutdown = next(r for r in rows if r['event'] == 'telepresence_shutdown')
            self.assertEqual((shutdown['reason'], shutdown['lifecycle']), ('nobody_connected', 'closed'))
            ending = json.loads((project / 'local/telepresence/last-call.json').read_text())
            self.assertEqual((ending['reason'], ending['code'], ending['failed_starts']), ('nobody_connected', 0, []))

    def test_a_failed_required_start_still_parks_and_releases(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            log = []
            code, handoff = self.run_program(project, log, fail='robot_servos')
            self.assertEqual(code, 1)
            self.assertNotIn(('open_eyes',), log)
            self.assertIn(('close', 'robot_leds'), log)
            handoff.close.assert_called_once()

    def test_never_takes_over_a_running_rex(self):
        from r3x.lease import LiveLease
        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            (project / 'local/sessions').mkdir()
            rex = LiveLease(project / 'local/sessions/live.lock')
            rex.acquire()
            try:
                with self.assertRaises(ValueError):
                    self.run_program(project, [])
            finally:
                rex.close()

    def test_a_missing_token_stops_before_any_lease_or_device(self):
        from r3x.telepresence import program
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            project = self.project(folder, token=None)
            handoff = Mock()
            out = io.StringIO()
            with patch('sys.stdout', out):
                code = program.run(self.settings(), project, compose=lambda s, secrets: ((), {}), handoff=handoff)
            self.assertEqual(code, 1)
            self.assertIn('telepresence_token', json.loads(out.getvalue())['missing'])
            handoff.acquire.assert_not_called()
            self.assertFalse((project / 'local/sessions').exists())

    def test_setup_writes_one_private_token_and_keeps_it(self):
        from r3x.telepresence.program import setup
        with tempfile.TemporaryDirectory() as folder, patch.dict('os.environ', {}, clear=True):
            root = Path(folder).resolve()
            (root / 'secrets.toml').write_text('ntfy_topic = "x"')
            message = setup(root)
            token = Secrets.load(root).telepresence_token
            self.assertTrue(protocol.valid_token(token))
            self.assertNotIn(token, message)
            self.assertEqual((root / 'secrets.toml').stat().st_mode & 0o777, 0o600)
            setup(root)
            self.assertEqual(Secrets.load(root).telepresence_token, token)


if __name__ == '__main__':
    unittest.main()
