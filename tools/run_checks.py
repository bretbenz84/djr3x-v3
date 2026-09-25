"""Run v3 contract tests with external I/O blocked, using the v3 environment."""
from pathlib import Path
from importlib.abc import MetaPathFinder
import sys
import os
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
if Path(sys.prefix).resolve() != PROJECT / ".venv":
    raise SystemExit("Use .venv/bin/python tools/run_checks.py")
sys.path.insert(0, str(PROJECT))

from tools.check_io_policy import check_mutation

BLOCKED_MODULES = {
    "sounddevice", "pyaudio", "serial", "cv2", "torch", "torchaudio",
    "mlx", "mlx_audio", "silero_vad", "onnxruntime", "openai", "elevenlabs", "config",
    "intelligence", "audio", "memory", "hardware", "vision",
    "usb", "libusb_package",
    "openwakeword", "rumps", "AppKit", "Foundation",
}


class BlockProviderImports(MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in BLOCKED_MODULES:
            raise RuntimeError(f"Device/model/v2 import blocked: {fullname}")
        return None


def block_external_io(event, args):
    check_mutation(event, args)
    if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo",
                 "subprocess.Popen", "os.system", "ctypes.dlopen"}:
        raise RuntimeError(f"External I/O blocked in contract tests: {event}")
    if event == "import" and args[0].split(".")[0] in BLOCKED_MODULES:
        raise RuntimeError(f"Device/model/v2 import blocked: {args[0]}")
    if event == "open":
        target = args[0]
        if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
            check_mutation("os.truncate", (target,))
        if isinstance(target, (str, bytes)):
            path = Path(target.decode() if isinstance(target, bytes) else target).resolve()
            if path.is_relative_to(Path("/Users/bbenziger/djr3x-v2")) or str(path).startswith("/dev/"):
                raise RuntimeError("Private baseline/device access blocked")
            if path.is_relative_to(PROJECT / "local"):
                raise RuntimeError("Private v3 state blocked in contract tests")
    if event == "sqlite3.connect":
        target = str(args[0])
        if target != ":memory:":
            import tempfile
            if target.startswith("file:"):
                from urllib.parse import urlparse,unquote
                target = unquote(urlparse(target).path)
            if not Path(target).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                raise RuntimeError("Only temporary test databases are allowed")


sys.dont_write_bytecode = True
sys.addaudithook(block_external_io)
sys.meta_path.insert(0, BlockProviderImports())
with tempfile.TemporaryDirectory() as temporary_cwd:
    # Directory-relative shutil cleanup opens must not resolve against real local/.
    original_cwd = Path.cwd()
    os.chdir(temporary_cwd)
    try:
        suite = unittest.defaultTestLoader.discover(str(PROJECT / "tests"), top_level_dir=str(PROJECT))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    finally:
        os.chdir(original_cwd)
raise SystemExit(not result.wasSuccessful())
