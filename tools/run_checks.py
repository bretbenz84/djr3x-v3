"""Run v3 contract tests with external I/O blocked, using the v3 environment.

    .venv/bin/python tools/run_checks.py                     every check
    .venv/bin/python tools/run_checks.py --changed           only the test files that use what changed
    .venv/bin/python tools/run_checks.py --changed --since REF   changes since a commit, not just uncommitted
    .venv/bin/python tools/run_checks.py --changed --list    say which would run, run nothing

The tests themselves run in a child process with network, audio, serial, device, subprocess and
private-state access blocked (owner, November 10: a changed-only run must not loosen any of that).
`--changed` picks the test files that ran a changed function, from a map of which of Rex's functions
each test file uses (local/checks/test-map.json, ignored). Anything it cannot place (a change outside a
function, a file the map has never seen, the test runner itself) widens the choice, up to every check.
Noting the functions slows checks by about two thirds, so a changed-only run notes (and refreshes) only
the files it runs, and a full run rebuilds the whole map only when it is missing or a week old.
"""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
if Path(sys.prefix).resolve() != PROJECT / ".venv":
    raise SystemExit("Use .venv/bin/python tools/run_checks.py")
sys.path.insert(0, str(PROJECT))
sys.dont_write_bytecode = True

MAP = PROJECT / "local" / "checks" / "test-map.json"
SRC = PROJECT / "src" / "r3x"
# Changes here touch no check: documentation, firmware (tools/run_firmware_checks.py has its own),
# hardware notes and editor droppings.
NO_CHECKS = (".md", ".txt", ".DS_Store", ".gitignore")
NO_CHECKS_DIRS = ("docs/", "firmware/")
WHOLE_SUITE = ("tools/run_checks.py", "tools/check_io_policy.py", "pyproject.toml", "tests/__init__.py")
MAP_DAYS = 7          # A full run rebuilds a map older than this (about a minute more for that run).


# ---------------------------------------------------------------- choosing (no I/O blocks yet)

def _git(*args):
    import subprocess
    done = subprocess.run(("git", *args), cwd=PROJECT, capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"git {' '.join(args)} failed: {done.stderr.strip()}")
    return done.stdout


def changed_paths(since):
    tracked = _git("diff", "--name-only", since, "--").split()
    untracked = _git("ls-files", "--others", "--exclude-standard").split()
    return sorted(set(tracked) | set(untracked))


def _units(source):
    """(function qualified name -> its code, everything outside functions) for one Python file.
    Comments and spacing do not count; a docstring does."""
    import ast
    tree = ast.parse(source)
    functions = {}

    def visit(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions[prefix + node.name] = ast.dump(node)
            elif isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")

    visit(tree.body, "")

    class Outside(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            return None
        visit_AsyncFunctionDef = visit_FunctionDef

    return functions, ast.dump(Outside().visit(tree))


def _old_source(since, path):
    import subprocess
    done = subprocess.run(("git", "show", f"{since}:{path}"), cwd=PROJECT, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else None


def _imports_of(name, prefix):
    """Test modules whose source imports `prefix + name` (tests.test_x, tools.check_y)."""
    import re
    pattern = re.compile(rf"\b{re.escape(prefix)}\.{re.escape(name)}\b|from {re.escape(prefix)} import [^\n]*\b{re.escape(name)}\b")
    return {f.stem for f in (PROJECT / "tests").glob("test_*.py") if pattern.search(f.read_text())}


def choose(since):
    """(test modules to run or None for all of them, reasons) for what changed since `since`."""
    import json, time
    if not MAP.exists():
        return None, ["no test map yet (this full run writes one)"]
    saved = json.loads(MAP.read_text())
    known = saved["modules"]
    days = (time.time() - saved["at"]) / 86400
    every = {f.stem for f in (PROJECT / "tests").glob("test_*.py")}
    chosen, reasons = set(), []

    def pick(modules, why):
        modules = set(modules) & every
        if modules - chosen:
            reasons.append(f"{why}: {', '.join(sorted(modules - chosen))}")
        chosen.update(modules)

    for path in changed_paths(since):
        if path.endswith(NO_CHECKS) or path.startswith(NO_CHECKS_DIRS):
            continue
        if path in WHOLE_SUITE:
            return None, [f"{path} changed"]
        file = PROJECT / path
        if path.startswith("src/r3x/") and path.endswith(".py"):
            key = path[len("src/"):]
            users = {m for m, ran in known.items() if any(u.startswith(key + "::") for u in ran)}
            old, new = _old_source(since, path), file.read_text() if file.exists() else None
            if old is None:
                reasons.append(f"{key} is new: checked through whatever calls it")
                continue
            if new is None:
                pick(users, f"{key} removed")
                continue
            (was, outside_was), (now, outside_now) = _units(old), _units(new)
            if outside_was != outside_now:
                pick(users, f"{key} changed outside its functions")
                continue
            touched = {name for name in was.keys() | now.keys() if was.get(name) != now.get(name)}
            for name in sorted(touched):
                pick({m for m, ran in known.items() if f"{key}::{name}" in ran}, f"{key} {name}")
        elif path.startswith("tests/") and path.endswith(".py"):
            name = Path(path).stem
            if name.startswith("test_") and file.exists():
                pick({name}, "test file changed")
            seen, todo = set(), [name]
            while todo:   # Test files that borrow its helpers, and theirs in turn.
                for user in _imports_of(todo.pop(), "tests") - seen:
                    seen.add(user)
                    todo.append(user)
            pick(seen, f"uses tests/{name}.py")
        elif path.startswith("tools/") and path.endswith(".py"):
            pick(_imports_of(Path(path).stem, "tools"), f"uses {path}")
        elif path.startswith("src/r3x/"):
            return None, [f"{path} changed (not Python: every check)"]
        else:
            mentioning = {f.stem for f in (PROJECT / "tests").glob("test_*.py") if Path(path).name in f.read_text()}
            if not mentioning and not path.startswith(("operator_tools/",)):
                return None, [f"{path} changed and no rule covers it"]
            if path.startswith("operator_tools/"):
                mentioning |= {"test_operator_tools"}
            pick(mentioning, f"mentions {path}")
    pick(every - set(known), "not in the test map yet")
    reasons.insert(0, f"test map from commit {saved['commit']}, {days:.1f} days old")
    return sorted(chosen), reasons


def save_map(trace_file, full):
    """Fold one run's record into the map: a full run replaces it, a partial run updates its files."""
    import json, os, time
    ran = json.loads(Path(trace_file).read_text())
    known = {} if full or not MAP.exists() else json.loads(MAP.read_text())["modules"]
    known.update({m: sorted(names) for m, names in ran.items()})
    every = {f.stem for f in (PROJECT / "tests").glob("test_*.py")}
    MAP.parent.mkdir(parents=True, exist_ok=True)
    temporary = MAP.with_suffix(".tmp")
    temporary.write_text(json.dumps({"commit": _git("rev-parse", "--short", "HEAD").strip(), "at": time.time(),
                                     "modules": {m: v for m, v in sorted(known.items()) if m in every}}))
    os.replace(temporary, MAP)


def main():
    import argparse, subprocess, tempfile
    parser = argparse.ArgumentParser(description="Blocked-I/O contract checks.")
    parser.add_argument("--changed", action="store_true", help="only test files that use what changed")
    parser.add_argument("--since", default="HEAD", help="with --changed: compare with this commit (default: HEAD)")
    parser.add_argument("--list", action="store_true", help="with --changed: say which would run, run nothing")
    parser.add_argument("--child", help=argparse.SUPPRESS)
    parser.add_argument("modules", nargs="*", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return run_blocked(args.modules, args.child)
    modules = None
    if args.changed:
        modules, reasons = choose(args.since)
        for reason in reasons:
            print("  " + reason)
        if modules is None:
            print("Running every check.", flush=True)
        elif not modules:
            print("No check uses what changed.")
            return 0
        else:
            total = len(list((PROJECT / "tests").glob("test_*.py")))
            print(f"Running {len(modules)} of {total} test files.", flush=True)
        if args.list:
            return 0
    import json, time
    stale = not MAP.exists() or time.time() - json.loads(MAP.read_text())["at"] > MAP_DAYS * 86400
    note = modules is not None or stale
    if modules is None and stale:
        print("Rebuilding the test map during this run (it is missing or a week old).", flush=True)
    with tempfile.TemporaryDirectory() as folder:
        trace_file = str(Path(folder) / "ran.json") if note else "-"
        code = subprocess.run((sys.executable, __file__, "--child", trace_file, *(modules or ()))).returncode
        if note and Path(trace_file).exists():
            save_map(trace_file, full=modules is None)
    return code


# ---------------------------------------------------------------- running (inside the blocks)

def run_blocked(modules, trace_file):
    from importlib.abc import MetaPathFinder
    import collections, json, os, tempfile, threading, unittest
    from tools.check_io_policy import check_mutation

    blocked_modules = {
        "sounddevice", "pyaudio", "serial", "cv2", "torch", "torchaudio",
        "mlx", "mlx_audio", "silero_vad", "onnxruntime", "mediapipe", "matplotlib", "openai", "elevenlabs", "config",
        "intelligence", "audio", "memory", "hardware", "vision",
        "usb", "libusb_package",
        "openwakeword", "rumps", "AppKit", "Foundation",
    }

    class BlockProviderImports(MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in blocked_modules:
                raise RuntimeError(f"Device/model/v2 import blocked: {fullname}")
            return None

    def block_external_io(event, args):
        check_mutation(event, args)
        if event in {"socket.__new__", "socket.connect", "socket.getaddrinfo",
                     "subprocess.Popen", "os.system", "ctypes.dlopen"}:
            raise RuntimeError(f"External I/O blocked in contract tests: {event}")
        if event == "import" and args[0].split(".")[0] in blocked_modules:
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
                if target.startswith("file:"):
                    from urllib.parse import urlparse, unquote
                    target = unquote(urlparse(target).path)
                if not Path(target).resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()):
                    raise RuntimeError("Only temporary test databases are allowed")

    sys.addaudithook(block_external_io)
    sys.meta_path.insert(0, BlockProviderImports())

    # Which of Rex's functions each test file runs: function entries only, no line tracing.
    source = str(SRC) + os.sep
    current, ran = [None], collections.defaultdict(set)

    def note(frame, event, arg):   # Called as each Python function starts; returning None skips its lines.
        code = frame.f_code
        if current[0] is not None and code.co_filename.startswith(source):
            ran[current[0]].add("r3x/" + code.co_filename[len(source):] + "::" + code.co_qualname.split(".<locals>")[0])

    class Noted(unittest.TextTestResult):
        def startTest(self, test):
            current[0] = test.id().split(".")[1]
            ran[current[0]]   # A test file that uses none of Rex's functions is still on the map.
            super().startTest(test)

    with tempfile.TemporaryDirectory() as temporary_cwd:
        # Directory-relative shutil cleanup opens must not resolve against real local/.
        original_cwd = Path.cwd()
        os.chdir(temporary_cwd)
        try:
            loader = unittest.defaultTestLoader
            suite = (loader.loadTestsFromNames(["tests." + m for m in modules]) if modules else
                     loader.discover(str(PROJECT / "tests"), top_level_dir=str(PROJECT)))
            if trace_file != "-":
                sys.settrace(note)
                threading.settrace(note)
            result = unittest.TextTestRunner(verbosity=2, resultclass=Noted).run(suite)
            sys.settrace(None)
            threading.settrace(None)
        finally:
            os.chdir(original_cwd)
    if trace_file != "-" and result.wasSuccessful():   # A failed run is no record of what checks use.
        Path(trace_file).write_text(json.dumps({m: sorted(v) for m, v in ran.items()}))
    return not result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(main())
