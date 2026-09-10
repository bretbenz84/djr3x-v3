"""One live v3 process per checkout; operating-system lease, no stale PID logic."""
from pathlib import Path


class LiveLease:
    def __init__(self, path: Path):
        self.path, self.handle = path, None

    def acquire(self):
        import fcntl
        if self.path.resolve() != self.path:
            raise ValueError("Live lease must not be redirected")
        self.handle = self.path.open("a")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.close()
            raise ValueError("Another v3 live process owns this checkout") from None

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None
