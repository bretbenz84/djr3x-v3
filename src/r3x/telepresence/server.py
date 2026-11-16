"""The robot's listening end: one signed-in client at a time, from the allowed networks only."""
import os
import threading
import time

from ..contracts import ProviderFailure
from ..providers import Capability
from .protocol import Link, ProtocolError, close_socket, refuse, sign_in_client, source_allowed

ACCEPT_POLL_SECONDS = .5


def tcp_listener(host: str, port: int):
    import socket
    family = socket.AF_INET6 if ':' in host else socket.AF_INET
    listener = socket.create_server((host, port), family=family, backlog=2)
    listener.settimeout(ACCEPT_POLL_SECONDS)
    return listener


class Server:
    """Listens on one address. A connection from outside the allowed networks is closed unanswered; a
    second client while one is signed in, a wrong token or a bad sign-in is refused with the reason.
    A signed-in client becomes a Link handed to on_client(link, source, name); its frames go to
    on_frame and its end to on_closed, both set by the session before start."""

    def __init__(self, host, port, networks, token, *, welcome=dict, open_listener=tcp_listener,
                 nonce=lambda: os.urandom(16), clock=time.monotonic):
        self.host, self.port, self.networks, self.token = host, port, tuple(networks), token
        self.welcome, self.open_listener, self.nonce, self.clock = welcome, open_listener, nonce, clock
        self.capability = Capability('telepresence_link', 'TCP over the home WireGuard VPN',
            'one signed-in client: room audio and video out, the owner\'s voice in',
            resources=(f'listen {host}:{port}', 'allowed ' + ', '.join(map(str, self.networks))))
        self.on_client = lambda link, source, name: None
        self.on_frame = lambda link, kind, payload: None
        self.on_closed = lambda link, reason: None
        self.trace = None
        self.listener = None
        self.link = None
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None

    def _emit(self, event, **data):
        if self.trace:
            self.trace.emit(event, **data)

    def start(self):
        """Provider start: checks only. The listener opens (open) once a call can be taken."""
        if not self.networks:
            raise ProviderFailure('TelepresenceNeedsAllowedNetworks')

    def open(self):
        """Start taking calls. November 16: the app called while he was still taking his stance, signed
        in before the call was wired to the link, heard nothing and hung up. Until this runs, a caller is
        refused by the operating system and the app's retry calls again."""
        self.listener = self.open_listener(self.host, self.port)
        self.thread = threading.Thread(target=self._accept, name='telepresence-accept', daemon=True)
        self.thread.start()
        self._emit('telepresence_listening', address=f'{self.host}:{self.port}',
                   allowed=[str(n) for n in self.networks])

    def active(self):
        with self.lock:
            return self.link if self.link is not None and not self.link.closed.is_set() else None

    def _accept(self):
        while not self.stop.is_set():
            try:
                sock, peer = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self.stop.is_set():
                    return
                self.stop.wait(ACCEPT_POLL_SECONDS)   # A transient accept error never spins.
                continue
            source = str(peer[0]) if peer else ''
            try:
                self._admit(sock, source)
            except Exception as exc:   # One bad connection never ends listening.
                close_socket(sock)
                self._emit('telepresence_client_refused', source=source, reason='admit_failed:' + type(exc).__name__)

    def _admit(self, sock, source):
        if not source_allowed(source, self.networks):
            close_socket(sock)   # Unanswered: nothing said to someone outside the allowed networks.
            self._emit('telepresence_client_refused', source=source, reason='source_not_allowed')
            return
        if self.active() is not None:
            refuse(sock, 'busy')
            self._emit('telepresence_client_refused', source=source, reason='busy')
            return
        try:
            setsockopt = getattr(sock, 'setsockopt', None)
            if setsockopt is not None:
                import socket
                setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            name = sign_in_client(sock, self.token, self.welcome(), nonce=self.nonce())
        except ProtocolError as exc:
            refuse(sock, exc.reason)
            self._emit('telepresence_client_refused', source=source, reason=exc.reason)
            return
        except OSError:
            close_socket(sock)
            self._emit('telepresence_client_refused', source=source, reason='sign_in_socket_error')
            return
        link = Link(sock, lambda l, kind, payload: self.on_frame(l, kind, payload),
                    on_closed=self._closed, clock=self.clock)
        with self.lock:
            if self.stop.is_set():
                refuse(sock, 'shutting_down')
                return
            self.link = link
        self._emit('telepresence_client_connected', source=source, client=name)
        self.on_client(link, source, name)
        link.start()

    def _closed(self, link, reason):
        with self.lock:
            if self.link is link:
                self.link = None
        self._emit('telepresence_link_closed', reason=reason, stats=dict(link.stats))
        self.on_closed(link, reason)

    def cancel(self):
        self.stop.set()
        link = self.active()
        if link is not None:
            link.close('shutdown')
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass

    def close(self):
        self.cancel()
        if self.thread is not None:
            self.thread.join(ACCEPT_POLL_SECONDS * 4 + 6.)   # Covers a sign-in in progress.
            if self.thread.is_alive():
                raise ProviderFailure('TelepresenceAcceptDidNotStop')
            self.thread = None
