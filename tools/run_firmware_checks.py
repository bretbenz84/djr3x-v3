"""Run the drive firmware's native checks with I/O blocked except the C++ toolchain.

Each check compiles pure firmware headers (or battery.cpp with fake hardware) with clang++
into a temporary directory and runs the result. The ordinary runner (run_checks.py) blocks
all subprocesses, so these live in firmware/tests and run here instead. Only clang++ and
programs built under the temporary directory may start; network, devices, v2 files and
real v3 state stay blocked.
"""
from pathlib import Path
import os
import shutil
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
if Path(sys.prefix).resolve() != PROJECT / ".venv":
    raise SystemExit("Use .venv/bin/python tools/run_firmware_checks.py")
sys.path.insert(0, str(PROJECT))

from tools.check_io_policy import TEMPORARY, check_mutation

TESTS = PROJECT / "firmware" / "tests"
COMPILER = shutil.which("clang++")
if COMPILER is None:
    raise SystemExit("clang++ not found (install the Xcode command line tools)")
COMPILER = Path(COMPILER).resolve()
SPAWN_EVENTS = {"subprocess.Popen", "os.posix_spawn", "os.posix_spawnp", "os.exec"}


def allowed_program(program):
    path = Path(os.fsdecode(program))
    if not path.is_absolute():
        found = shutil.which(str(path))
        if found is None:
            return False
        path = Path(found)
    path = path.resolve()
    return path == COMPILER or path.is_relative_to(TEMPORARY)


def block_external_io(event, args):
    if event in SPAWN_EVENTS:
        program = args[0] if event != "subprocess.Popen" else (args[0] or args[1][0])
        if not allowed_program(program):
            raise RuntimeError(f"Only clang++ and temporary test programs may run: {program}")
        return
    if event in {"os.fork", "os.forkpty"}:
        raise RuntimeError(f"Process creation blocked in firmware checks: {event}")
    check_mutation(event, args)
    if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo", "os.system", "ctypes.dlopen"}:
        raise RuntimeError(f"External I/O blocked in firmware checks: {event}")
    if event == "open":
        target = args[0]
        if isinstance(target, int):   # pipes to and from the test programs
            return
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            check_mutation("os.truncate", (target,))
        if isinstance(target, (str, bytes)):
            path = Path(os.fsdecode(target)).resolve()
            if path.is_relative_to(Path("/Users/bbenziger/djr3x-v2")):
                raise RuntimeError("Private baseline access blocked")
            if str(path).startswith("/dev/") and str(path) != "/dev/null":
                raise RuntimeError("Device access blocked")
            if path.is_relative_to(PROJECT / "local"):
                raise RuntimeError("Private v3 state blocked in firmware checks")


sys.dont_write_bytecode = True
sys.addaudithook(block_external_io)
suite = unittest.defaultTestLoader.discover(str(TESTS), top_level_dir=str(TESTS))
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
