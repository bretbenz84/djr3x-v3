"""Filesystem mutation boundary for the isolated contract-test runner."""
import os
from pathlib import Path
import sys
import tempfile

TEMPORARY = Path(tempfile.gettempdir()).resolve()


def resolved_target(target, directory_fd=None):
    path = Path(os.fsdecode(target))
    if not path.is_absolute() and directory_fd is not None and directory_fd >= 0:
        if sys.platform == 'darwin':
            import fcntl
            directory = os.fsdecode(fcntl.fcntl(directory_fd, 50, bytes(1024)).split(b'\0', 1)[0])
        else:
            directory = os.readlink(f'/proc/self/fd/{directory_fd}')
        path = Path(directory) / path
    return path.resolve()


def check_mutation(event, args):
    if event in {'os.posix_spawn', 'os.posix_spawnp', 'os.fork', 'os.forkpty', 'os.exec'}:
        raise RuntimeError(f'Process creation blocked in contract tests: {event}')
    single = {'os.remove': 1, 'os.rmdir': 1, 'os.mkdir': 2, 'os.chmod': 2,
              'os.chown': 3, 'os.utime': 3}
    targets = []
    if event in single:
        index = single[event]
        targets = [(args[0], args[index] if len(args) > index else None)]
    elif event in {'os.rename', 'os.link'}:
        targets = [(args[0], args[2]), (args[1], args[3])]
    elif event == 'os.symlink':
        # The destination is the mutation; targets are checked again at access time.
        targets = [(args[1], args[2])]
    elif event == 'os.truncate':
        targets = [(args[0], None)]
    if not targets:
        return
    temporary = TEMPORARY
    for target, directory_fd in targets:
        if isinstance(target, int):
            target = resolved_target('.', target)  # Resolve the descriptor itself.
        if not resolved_target(target, directory_fd).is_relative_to(temporary):
            raise RuntimeError(f'Non-temporary mutation blocked in contract tests: {event}')
