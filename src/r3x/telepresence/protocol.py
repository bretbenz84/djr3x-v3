"""The telepresence wire format and one connected peer, for the robot and its clients alike.

Plain streams (owner, November 16): one TCP connection carries everything. A frame is one kind
byte, a four-byte big-endian payload length and the payload:

  J  one JSON object (UTF-8): sign-in, pings, status and hang-up
  A  audio: u32 sequence, f64 sender clock in seconds, then 16 kHz mono s16le PCM
  V  video: u32 sequence, f64 robot clock when the frame was captured, then one JPEG

Sign-in: the robot sends a challenge nonce, the client answers with HMAC-SHA256 of it under the
shared token, and the robot welcomes it or says why not. Until then only small J frames are read.
The token never crosses the wire. WireGuard already encrypts the link; this decides who may use it.
"""
from collections import deque
import hashlib
import hmac
import ipaddress
import json
import struct
import threading
import time

VERSION = 1
AUDIO_RATE = 16000
HEADER = struct.Struct('>BI')
STAMP = struct.Struct('>Id')
# Largest payload read for each kind; anything bigger ends the connection.
LIMITS = {ord('J'): 16 * 1024, ord('A'): 16 * 1024, ord('V'): 2 * 1024 * 1024}
SIGN_IN_LIMITS = {ord('J'): 4 * 1024}
HANDSHAKE_SECONDS = 5.
# Room audio waiting to be sent: beyond this the oldest is dropped, so a slow link costs words, not delay.
AUDIO_BACKLOG = .5
TOKEN_MIN_LENGTH = 32
_DOMAIN = b'r3x-telepresence-v1:'


class ProtocolError(Exception):
    """The peer broke the wire format or failed sign-in; the reason is safe to log."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class ConnectionClosed(Exception):
    pass


# ---------------------------------------------------------------- frames

def encode(kind: str, payload: bytes) -> bytes:
    code = ord(kind)
    if code not in LIMITS or len(payload) > LIMITS[code]:
        raise ValueError('FrameTooLargeOrUnknown')
    return HEADER.pack(code, len(payload)) + payload


def encode_json(message: dict) -> bytes:
    return encode('J', json.dumps(message, separators=(',', ':'), allow_nan=False).encode())


def encode_stamped(kind: str, sequence: int, at: float, data: bytes) -> bytes:
    return encode(kind, STAMP.pack(sequence & 0xFFFFFFFF, at) + data)


def decode_stamped(payload: bytes):
    if len(payload) < STAMP.size:
        raise ProtocolError('short_stamped_frame')
    sequence, at = STAMP.unpack_from(payload)
    return sequence, at, payload[STAMP.size:]


def decode_json(payload: bytes) -> dict:
    try:
        message = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError('invalid_json') from None
    if not isinstance(message, dict) or not isinstance(message.get('type'), str):
        raise ProtocolError('json_without_type')
    return message


def _recv_exact(sock, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionClosed()
        data += chunk
    return bytes(data)


def read_frame(sock, limits=LIMITS):
    """(kind letter, payload), blocking. Raises ConnectionClosed at the peer's end of stream."""
    code, size = HEADER.unpack(_recv_exact(sock, HEADER.size))
    if code not in limits:
        raise ProtocolError('unexpected_frame_kind')
    if size > limits[code]:
        raise ProtocolError('frame_too_large')
    return chr(code), _recv_exact(sock, size) if size else b''


# ---------------------------------------------------------------- access

def proof(token: str, nonce: bytes) -> str:
    return hmac.new(token.encode(), _DOMAIN + nonce, hashlib.sha256).hexdigest()


def valid_token(token) -> bool:
    return isinstance(token, str) and len(token) >= TOKEN_MIN_LENGTH and token.isascii() and token.isprintable() \
        and not any(c.isspace() for c in token)


def parse_listen(text: str):
    """'192.168.1.50:47800' or '[fd00::5]:47800' -> (host, port). A specific address only, never every
    interface, and a port from 1024."""
    if not isinstance(text, str) or text != text.strip() or ':' not in text:
        raise ValueError('TelepresenceListenNeedsAddressAndPort')
    host, _, port = text.rpartition(':')
    if host.startswith('[') and host.endswith(']'):
        host = host[1:-1]
    elif ':' in host:
        raise ValueError('TelepresenceIPv6NeedsBrackets')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError('TelepresenceListenNeedsIPAddress') from None
    if address.is_unspecified or address.is_multicast:
        raise ValueError('TelepresenceListenNeedsOneAddress')
    if not port.isdigit() or not 1024 <= int(port) <= 65535:
        raise ValueError('TelepresencePortOutOfRange')
    return str(address), int(port)


def parse_networks(values) -> tuple:
    if not isinstance(values, (list, tuple)) or len(values) > 8:
        raise ValueError('TelepresenceNetworksAtMostEight')
    networks = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError('TelepresenceNetworkMustBeText')
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            raise ValueError('TelepresenceInvalidNetwork') from None
    return tuple(networks)


def source_allowed(source: str, networks) -> bool:
    try:
        address = ipaddress.ip_address(source.split('%', 1)[0])
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return any(address.version == n.version and address in n for n in networks)


def refuse(sock, reason: str) -> None:
    """Best effort: say why, then hang up."""
    try:
        sock.sendall(encode_json({'type': 'refused', 'reason': reason}))
    except OSError:
        pass
    close_socket(sock)


def close_socket(sock) -> None:
    try:
        sock.shutdown(2)   # socket.SHUT_RDWR: also wakes a reader blocked in recv.
    except (OSError, AttributeError):
        pass
    try:
        sock.close()
    except OSError:
        pass


def sign_in_client(sock, token: str, welcome: dict, *, nonce: bytes) -> str:
    """Robot side: challenge, check the proof, welcome. Returns the client's name; raises ProtocolError."""
    try:
        sock.settimeout(HANDSHAKE_SECONDS)
        sock.sendall(encode_json({'type': 'challenge', 'version': VERSION, 'nonce': nonce.hex()}))
        kind, payload = read_frame(sock, SIGN_IN_LIMITS)
        hello = decode_json(payload)
        if hello['type'] != 'hello':
            raise ProtocolError('expected_hello')
        if hello.get('version') != VERSION:
            raise ProtocolError('unsupported_version')
        given = hello.get('proof')
        if not isinstance(given, str) or not hmac.compare_digest(given, proof(token, nonce)):
            raise ProtocolError('wrong_token')
        name = hello.get('client', '')
        name = name[:64] if isinstance(name, str) and name.isprintable() else ''
        sock.sendall(encode_json({'type': 'welcome', 'version': VERSION, **welcome}))
        sock.settimeout(None)
        return name
    except ConnectionClosed:
        raise ProtocolError('closed_during_sign_in') from None
    except TimeoutError:
        raise ProtocolError('sign_in_timed_out') from None


def sign_in(sock, token: str, client: str) -> dict:
    """Client side: answer the challenge. Returns the welcome; raises ProtocolError with the robot's reason."""
    try:
        sock.settimeout(HANDSHAKE_SECONDS)
        challenge = decode_json(read_frame(sock, SIGN_IN_LIMITS)[1])
        if challenge['type'] == 'refused':
            raise ProtocolError(str(challenge.get('reason', 'refused')))
        if challenge['type'] != 'challenge' or challenge.get('version') != VERSION:
            raise ProtocolError('unexpected_challenge')
        nonce = bytes.fromhex(challenge['nonce'])
        sock.sendall(encode_json({'type': 'hello', 'version': VERSION, 'proof': proof(token, nonce), 'client': client}))
        answer = decode_json(read_frame(sock, LIMITS)[1])
        if answer['type'] != 'welcome':
            raise ProtocolError(str(answer.get('reason', answer['type'])))
        sock.settimeout(None)
        return answer
    except ConnectionClosed:
        raise ProtocolError('closed_during_sign_in') from None
    except TimeoutError:
        raise ProtocolError('sign_in_timed_out') from None
    except (KeyError, ValueError, TypeError):
        raise ProtocolError('unexpected_challenge') from None


def connect(address: str, token: str, client: str = 'test-client', *, timeout: float = 5.):
    """Client side: (socket, welcome) for 'host:port'."""
    import socket
    host, port = parse_listen(address)
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock, sign_in(sock, token, client)
    except BaseException:
        close_socket(sock)
        raise


# ---------------------------------------------------------------- one connected peer

class Link:
    """A signed-in connection. A writer thread sends JSON first, then audio, then only the newest video
    frame; a reader thread hands each frame to on_frame(link, kind, payload). Sending never blocks the
    caller. close() is idempotent, wakes both threads and calls on_closed(link, reason) once."""

    def __init__(self, sock, on_frame, *, on_closed=None, clock=time.monotonic, audio_rate=AUDIO_RATE):
        self.sock, self.on_frame, self.on_closed, self.clock = sock, on_frame, on_closed, clock
        self.audio_bytes_per_second = audio_rate * 2
        self.audio_backlog_bytes = int(AUDIO_BACKLOG * self.audio_bytes_per_second)
        self.condition = threading.Condition()
        self.control, self.audio, self.video = deque(), deque(), None
        self.audio_bytes = 0
        self.closed = threading.Event()
        self.reason = None
        self.last_heard = clock()
        self.sending_since = None
        self.stats = {'bytes_out': 0, 'bytes_in': 0, 'audio_out': 0, 'audio_dropped': 0, 'video_out': 0,
                      'video_superseded': 0, 'audio_in': 0, 'json_in': 0}
        self.reader = threading.Thread(target=self._read, name='telepresence-read', daemon=True)
        self.writer = threading.Thread(target=self._write, name='telepresence-write', daemon=True)

    def start(self):
        self.reader.start()
        self.writer.start()
        return self

    # Sending (any thread) ------------------------------------------------
    def send_json(self, message: dict):
        data = encode_json(message)
        with self.condition:
            if not self.closed.is_set():
                self.control.append(data)
                self.condition.notify()

    def send_audio(self, sequence: int, at: float, pcm: bytes):
        data = encode_stamped('A', sequence, at, pcm)
        with self.condition:
            if self.closed.is_set():
                return
            self.audio.append(data)
            self.audio_bytes += len(data)
            while self.audio_bytes > self.audio_backlog_bytes and len(self.audio) > 1:
                self.audio_bytes -= len(self.audio.popleft())
                self.stats['audio_dropped'] += 1
            self.condition.notify()

    def send_video(self, sequence: int, at: float, jpeg: bytes):
        data = encode_stamped('V', sequence, at, jpeg)
        with self.condition:
            if self.closed.is_set():
                return
            if self.video is not None:
                self.stats['video_superseded'] += 1
            self.video = data
            self.condition.notify()

    def flush(self, timeout: float = 1.) -> bool:
        """Wait until queued JSON has gone out (a hang-up before close); True when it has."""
        deadline = time.monotonic() + timeout
        with self.condition:
            while (self.control or self.sending_since is not None) and not self.closed.is_set():
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self.condition.wait(min(left, .05))
            return not self.control

    def stalled_for(self, now: float) -> float:
        since = self.sending_since
        return 0. if since is None else now - since

    def pending(self) -> dict:
        with self.condition:
            return {'control': len(self.control), 'audio_seconds': round(self.audio_bytes / self.audio_bytes_per_second, 3),
                    'video': self.video is not None}

    # Threads ---------------------------------------------------------------
    def _next(self):
        with self.condition:
            while not (self.closed.is_set() or self.control or self.audio or self.video is not None):
                self.condition.wait()
            if self.closed.is_set():
                return None, None
            if self.control:
                return 'control', self.control.popleft()
            if self.audio:
                data = self.audio.popleft()
                self.audio_bytes -= len(data)
                return 'audio', data
            data, self.video = self.video, None
            return 'video', data

    def _write(self):
        while True:
            kind, data = self._next()
            if data is None:
                return
            self.sending_since = self.clock()
            try:
                self.sock.sendall(data)
            except OSError:
                self.close('send_failed')
                return
            finally:
                self.sending_since = None
            self.stats['bytes_out'] += len(data)
            if kind == 'audio':
                self.stats['audio_out'] += 1
            elif kind == 'video':
                self.stats['video_out'] += 1

    def _read(self):
        try:
            while not self.closed.is_set():
                kind, payload = read_frame(self.sock)
                self.last_heard = self.clock()
                self.stats['bytes_in'] += HEADER.size + len(payload)
                if kind in ('A', 'J'):
                    self.stats['audio_in' if kind == 'A' else 'json_in'] += 1
                self.on_frame(self, kind, payload)
        except ConnectionClosed:
            self.close('peer_closed')
        except ProtocolError as exc:
            self.close('protocol:' + exc.reason)
        except OSError:
            self.close('receive_failed')
        except Exception as exc:   # A handler's fault ends this link, never silently the reader alone.
            self.close('handler_failed:' + type(exc).__name__)

    def close(self, reason: str = 'closed'):
        with self.condition:
            if self.closed.is_set():
                return
            self.reason = reason
            self.closed.set()
            self.control.clear(); self.audio.clear(); self.video = None; self.audio_bytes = 0
            self.condition.notify_all()
        close_socket(self.sock)
        current = threading.current_thread()
        for thread in (self.reader, self.writer):
            if thread is not current and thread.is_alive():
                thread.join(2.)
        if self.on_closed:
            self.on_closed(self, reason)
