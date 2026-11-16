"""The supervisor's telepresence trigger (stage 4, docs/TELEPRESENCE_PLAN.md): status, start and stop.

The wake-word supervisor listens on the call's address one port up (telepresence_listen's port + 1)
with the same allowed networks and token. A caller signs in exactly as for a call, sends one JSON
request and gets one JSON answer, and the connection closes. Media never passes through here.

  {"type": "status"} -> {"type": "status", "state": "asleep" | "awake" | "telepresence", ...}
  {"type": "start"}  -> {"type": "starting", "call_port": n} or {"type": "refused", "reason": ...}
  {"type": "stop"}   -> {"type": "stopping"}, {"type": "not_running"} or {"type": "refused", ...}

A start runs `python -m r3x telepresence` only while Rex is asleep. It never ends a running
conversation, Jeopardy game or sentry mode, and a stop only ends a call the supervisor started.
The decisions are made by answer() in the supervisor's main loop; this listener's thread only waits
for them. Importing this module opens nothing.
"""
import json
import os
import queue
import threading
import time

from .protocol import (ProtocolError, SIGN_IN_LIMITS, close_socket, decode_json, encode_json, parse_listen,
                       read_frame, refuse, sign_in_client, source_allowed)
from .server import tcp_listener

REQUEST_SECONDS = 5.      # A signed-in caller has this long to send its request.
ANSWER_SECONDS = 5.       # The main loop answers within this (it checks between 80 ms microphone reads).
RETRY_BIND_SECONDS = 30.  # The address may not exist yet (Wi-Fi joining after login).
BATTERY_MAX_AGE = 60.     # The battery menu bar's reading older than this is not reported.
LAST_CALL_MAX_AGE = 3600.


def control_address(listen: str):
    """The trigger's (host, port): the call's address, one port up."""
    host, port = parse_listen(listen)
    if port >= 65535:
        raise ValueError('TelepresenceListenPortLeavesNoTriggerPort')
    return host, port + 1


def read_json(path, max_age, now=None):
    """A small JSON record written by another program, or None when missing, unreadable or older than
    max_age (by its own ended_at or at, wall-clock seconds)."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    stamp = record.get('ended_at', record.get('at'))
    now = time.time() if now is None else now
    if not isinstance(stamp, (int, float)) or not 0 <= now - stamp <= max_age:
        return None
    return {**record, 'seconds_ago': round(now - stamp)}


def answer(request, *, running, call_child, call_port, battery=None, last_call=None):
    """(reply, action) for one request. running: any controller holds the robot (the lock);
    call_child: the supervisor's own telepresence child is alive. action is None, 'launch' or 'stop'."""
    kind = request.get('type') if isinstance(request, dict) else None
    state = 'telepresence' if call_child else 'awake' if running else 'asleep'
    if kind == 'status':
        return {'type': 'status', 'state': state, 'call_port': call_port, 'battery': battery,
                'last_call': last_call}, None
    if kind == 'start':
        if call_child:
            return {'type': 'starting', 'call_port': call_port, 'already_running': True}, None
        if running:
            return {'type': 'refused', 'reason': 'rex_awake'}, None   # Never a takeover.
        return {'type': 'starting', 'call_port': call_port, 'already_running': False}, 'launch'
    if kind == 'stop':
        if call_child:
            return {'type': 'stopping'}, 'stop'
        if running:
            return {'type': 'refused', 'reason': 'rex_awake'}, None
        return {'type': 'not_running'}, None
    return {'type': 'refused', 'reason': 'unknown_request'}, None


class Requests:
    """Hands each signed-in request to the main loop and waits for its answer."""
    def __init__(self):
        self.queue = queue.Queue()
        self.wake = threading.Event()   # Set when a request is waiting: the dormant loop wakes for it.

    def ask(self, request, timeout=ANSWER_SECONDS):
        holder = {'event': threading.Event(), 'reply': None}
        self.queue.put((request, holder))
        self.wake.set()
        if not holder['event'].wait(timeout):
            return {'type': 'refused', 'reason': 'supervisor_busy'}
        return holder['reply']

    def serve(self, decide):
        """Main loop: answer every waiting request with decide(request) -> reply."""
        self.wake.clear()
        while True:
            try:
                request, holder = self.queue.get_nowait()
            except queue.Empty:
                return
            try:
                holder['reply'] = decide(request)
            except Exception as exc:
                holder['reply'] = {'type': 'refused', 'reason': 'supervisor_failed:' + type(exc).__name__}
            holder['event'].set()


class TriggerListener:
    """The listening end: allowed networks only, the call's token, one request per connection."""
    def __init__(self, host, port, networks, token, ask, *, open_listener=tcp_listener,
                 nonce=lambda: os.urandom(16), log=None):
        self.host, self.port, self.networks, self.token = host, port, tuple(networks), token
        self.ask, self.open_listener, self.nonce = ask, open_listener, nonce
        self.log = log or (lambda level, message, *args: None)
        self.listener = None
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self._run, name='telepresence-trigger', daemon=True)
        self.thread.start()

    def _run(self):
        while not self.stop.is_set():
            if self.listener is None:
                try:
                    self.listener = self.open_listener(self.host, self.port)
                except OSError as exc:
                    self.log('warning', 'Telepresence trigger cannot listen on %s:%s (%s); retrying in %.0fs.',
                             self.host, self.port, exc, RETRY_BIND_SECONDS)
                    self.stop.wait(RETRY_BIND_SECONDS)
                    continue
                self.log('info', 'Telepresence trigger listening on %s:%s.', self.host, self.port)
            try:
                sock, peer = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                if self.stop.is_set():
                    return
                self.stop.wait(.5)
                continue
            source = str(peer[0]) if peer else ''
            try:
                self.serve(sock, source)
            except Exception as exc:   # One bad connection never ends listening.
                close_socket(sock)
                self.log('warning', 'Telepresence trigger request from %s failed (%s).', source, type(exc).__name__)

    def serve(self, sock, source):
        if not source_allowed(source, self.networks):
            close_socket(sock)   # Unanswered, as the call's own listener does.
            self.log('info', 'Telepresence trigger: %s is outside the allowed networks.', source)
            return
        try:
            sign_in_client(sock, self.token, {'service': 'telepresence_trigger'}, nonce=self.nonce())
            sock.settimeout(REQUEST_SECONDS)
            request = decode_json(read_frame(sock, SIGN_IN_LIMITS)[1])
        except ProtocolError as exc:
            refuse(sock, exc.reason)
            self.log('info', 'Telepresence trigger refused %s (%s).', source, exc.reason)
            return
        except (OSError, TimeoutError):
            close_socket(sock)
            return
        reply = self.ask(request)
        self.log('info', 'Telepresence trigger: %s asked %s, answered %s.', source, request.get('type'),
                 reply.get('reason') or reply.get('state') or reply.get('type'))
        try:
            sock.sendall(encode_json(reply))
        except OSError:
            pass
        close_socket(sock)

    def close(self):
        self.stop.set()
        if self.listener is not None:
            try:
                self.listener.close()
            except OSError:
                pass
        if self.thread is not None:
            self.thread.join(REQUEST_SECONDS + ANSWER_SECONDS + 2.)
            self.thread = None


def request(address, token, message, *, client='trigger-client', timeout=5.):
    """Client side: one request to the supervisor's trigger at the call's 'host:port' (one port up)."""
    import socket
    from .protocol import LIMITS, sign_in
    host, port = control_address(address)
    sock = socket.create_connection((host, port), timeout=timeout)
    try:
        sign_in(sock, token, client)
        sock.sendall(encode_json(message))
        sock.settimeout(timeout + ANSWER_SECONDS)
        return decode_json(read_frame(sock, LIMITS)[1])
    finally:
        close_socket(sock)
