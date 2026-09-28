"""Cross-process controller intent and confirmed resource release.

The intent lock matches V2. V3 companions additionally hold resource locks until
their device has actually closed. No lock is unlinked or recovered from a PID.
"""
import os
from pathlib import Path
import subprocess
import shlex
import tempfile
import time

from .contracts import Cancellation, ProviderFailure


class FileLease:
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def acquire(self) -> bool:
        import fcntl
        if self.handle is not None:
            return True
        if self.path.is_symlink():
            raise ProviderFailure('HardwareLeaseRedirected')
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        handle = os.fdopen(fd, 'a+')
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            return False
        except BaseException:
            handle.close()
            raise
        self.handle = handle
        return True

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class HardwareLocks:
    def __init__(self, controller: Path | None = None):
        # Retain the legacy override, so a manually launched V2 and V3 agree.
        self.controller = controller or Path(os.environ.get('DJR3X_LOCK_PATH') or
                                            Path(tempfile.gettempdir()) / 'djr3x-main.lock')
        self.controller = self.controller.expanduser().absolute()

    def lease(self, resource: str) -> FileLease:
        if resource not in {'controller','microphone','maestro','head_led','chest_led','drive_base','radar'}:
            raise ValueError('UnknownHardwareResource')
        path = self.controller if resource == 'controller' else self.controller.with_name(
            self.controller.name + '.v3-' + resource)
        return FileLease(path)

    def controller_running(self) -> bool:
        import fcntl
        # Shared probe, not a brief exclusive claim (which makes peers yield).
        fd = os.open(self.controller, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            return False
        finally:
            os.close(fd)


def legacy_supervisor_active() -> bool:
    """Detect a loaded or manually started wake supervisor (V2's or its V3 copy).

    Inspect processes, including manually launched supervisors. Unknown probe
    results block activation rather than opening audio optimistically.
    """
    job = subprocess.run(['/bin/launchctl','print',f'gui/{os.getuid()}/com.djr3x.supervisor'],
                         capture_output=True,text=True,timeout=2.)
    if job.returncode == 0:
        return True  # Includes KeepAlive jobs between restarts.
    result = subprocess.run(['/bin/ps','-axo','command='],capture_output=True,text=True,timeout=2.)
    if result.returncode:
        raise ProviderFailure('LegacySupervisorProbeFailed')
    # Any copy: V2's, or V3's operator_tools/rex_supervisor.py (the V2 program, same protocol).
    return any('rex_supervisor.py' in line for line in result.stdout.splitlines())



def legacy_supervisor_ready(locks: HardwareLocks) -> bool:
    """Every live V2 listener must hold its protocol-v1 registration lease.

    No cached PID/status files: stale files and a loaded job between restarts do
    not establish readiness. The microphone lease separately proves release.
    """
    result = subprocess.run(['/bin/ps','-axo','pid=,command='],capture_output=True,text=True,timeout=2.)
    if result.returncode:
        raise ProviderFailure('LegacySupervisorProbeFailed')
    pids = []
    for line in result.stdout.splitlines():
        fields = line.strip().split(None,1)
        if len(fields)!=2: continue
        try: argv = shlex.split(fields[1])
        except ValueError: continue
        if argv and Path(argv[0]).name.startswith('python') and any(
                Path(arg).name=='rex_supervisor.py' for arg in argv[1:]):
            pids.append(int(fields[0]))
    if not pids:
        return False
    for pid in pids:
        probe = FileLease(locks.controller.with_name(locks.controller.name+'.v3-supervisor-v1-'+str(pid)))
        if probe.acquire():
            probe.close()
            return False
    return True


def serial_port_free(path: str) -> bool:
    """Read-only lsof check, including legacy companions; never open to probe."""
    aliases = [path]
    if path.startswith('/dev/cu.'):
        aliases.append(path.replace('/dev/cu.','/dev/tty.',1))
    result = subprocess.run(['/usr/sbin/lsof','-Fpn','--',*aliases],capture_output=True,text=True,timeout=2.)
    if result.returncode not in (0,1) or result.stderr.strip():
        raise ProviderFailure('SerialOwnershipProbeFailed')
    return not any(line.startswith('p') for line in result.stdout.splitlines())


class ControllerHandoff:
    def __init__(self, locks: HardwareLocks, *, port: str | None = None, led_ports=None,
                 port_free=serial_port_free, legacy_active=legacy_supervisor_active, legacy_ready=None,
                 clock=time.monotonic, wait=time.sleep):
        self.locks, self.port, self.port_free = locks, port, port_free
        self.legacy_active, self.clock, self.wait = legacy_active, clock, wait
        self.ports = ({"maestro":port} if port else {}) | dict(led_ports or {})
        self.legacy_ready = legacy_ready or (lambda: legacy_supervisor_ready(locks))
        self.held = []

    def acquire(self, cancel: Cancellation, timeout: float = 8.) -> None:
        if self.held:
            raise ProviderFailure('ControllerHandoffAlreadyAcquired')
        try:
            if self.legacy_active() and not self.legacy_ready():
                raise ProviderFailure('V2SupervisorHandoffUpgradeRequired')
            intent = self.locks.lease('controller')
            if not intent.acquire():
                raise ProviderFailure('AnotherControllerOwnsRobot')
            self.held.append(intent)
            deadline = self.clock() + timeout
            for resource in ('microphone',) + tuple(self.ports):
                lease = self.locks.lease(resource)
                while True:
                    cancel.check()
                    if self.clock() >= deadline:
                        raise ProviderFailure('HardwareHandoffTimeout')
                    if lease.acquire():
                        self.held.append(lease)
                        break
                    self.wait(.05)
            while any(not self.port_free(path) for path in self.ports.values()):
                cancel.check()
                if self.clock() >= deadline:
                    raise ProviderFailure('SerialHandoffTimeout')
                self.wait(.05)
            if self.clock() >= deadline:
                raise ProviderFailure('HardwareHandoffTimeout')
            cancel.check()
            if self.legacy_active() and not self.legacy_ready():
                raise ProviderFailure('V2SupervisorHandoffChanged')
        except BaseException:
            self.close()
            raise

    def close(self):
        # The caller MUST close devices/workers before acknowledging release.
        while self.held:
            self.held.pop().close()


class CompanionResource:
    """A companion yields to intent, and acknowledges only successful close."""
    def __init__(self, locks, resource, open_device):
        self.locks, self.open_device = locks, open_device
        self.lease = locks.lease(resource)
        self.device = None
        self.generation = 0

    def poll(self) -> bool:
        if self.locks.controller_running():
            self.close()
            return False
        if self.device is None:
            if not self.lease.acquire():
                return False
            try:
                if self.locks.controller_running():
                    self.lease.close()
                    return False
                self.device = self.open_device()
                self.generation += 1
            except BaseException:
                self.lease.close()
                raise
        return True

    def close(self):
        if self.device is not None:
            self.device.close()  # Failure retains lease: no false handoff acknowledgment.
            self.device = None
            self.generation += 1
        self.lease.close()
