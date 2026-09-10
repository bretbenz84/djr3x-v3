from contextlib import redirect_stdout, redirect_stderr
import importlib
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

from r3x.app import App
from r3x.providers import Capability, ProviderOwner
from r3x.settings import ScratchPaths, Settings
from r3x.__main__ import main
from r3x.settings import Secrets


class FakeProvider:
    """Lifecycle-only fixture, not a substitute conversation brain."""
    def __init__(self, name, calls, failures=(), required=True, dependencies=()):
        self.capability = Capability(name, "synthetic", "lifecycle contract", required=required,
                                     dependencies=dependencies)
        self.calls = calls
        self.failures = failures

    def _run(self, operation):
        self.calls.append((self.capability.name, operation))
        if operation in self.failures:
            raise RuntimeError("secret fixture value must not enter reports")

    def start(self): self._run("start")
    def cancel(self): self._run("cancel")
    def close(self): self._run("close")


class BootTests(unittest.TestCase):
    def test_core_imports_without_model_or_device_modules(self):
        import pkgutil
        import r3x
        for module in pkgutil.walk_packages(r3x.__path__, "r3x."):
            importlib.import_module(module.name)
        self.assertNotIn("torch", sys.modules)
        self.assertNotIn("sounddevice", sys.modules)

    def test_test_runner_blocks_network_and_devices(self):
        with self.assertRaisesRegex(RuntimeError, "blocked"):
            socket.socket()
        with self.assertRaisesRegex(RuntimeError, "blocked"):
            importlib.import_module("sounddevice")

    def test_doctor_does_not_start_or_create_scratch(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            owner = ProviderOwner((FakeProvider("fake", calls),))
            app = App(Settings(), Path(directory), owner)
            report = app.doctor()
            self.assertEqual(calls, [])
            self.assertFalse(app.paths.root.exists())
            self.assertFalse(report["conversation_ready"])
            self.assertEqual(report["capabilities"][0]["provider"], "synthetic")

    def test_boot_creates_only_local_scratch_and_closes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(Settings(), Path(directory))
            self.assertTrue(app.start())
            self.assertEqual({p.name for p in Path(directory).iterdir()}, {"local"})
            self.assertTrue((app.paths.root / "recordings").is_dir())
            app.close()
            self.assertEqual(app.doctor()["lifecycle"], "closed")

    def test_scratch_links_cannot_redirect_writes(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as other:
            root = Path(directory)
            (root / "local").symlink_to(other)
            with self.assertRaises(ValueError):
                ScratchPaths.for_project(root)
            (root / "local").unlink()
            paths = ScratchPaths.for_project(root)
            paths.root.mkdir()
            (paths.root / "recordings").symlink_to(other)
            with self.assertRaises(ValueError):
                paths.prepare()
            self.assertEqual(list(Path(other).iterdir()), [])

    def test_configuration_rejects_unknown_settings_and_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "settings.toml"
            for invalid in ('profile = "robot"', 'provider = "silent-fallback"', 'profile = 1'):
                file.write_text(invalid)
                with self.assertRaises(ValueError):
                    Settings.load(file)
            file.write_text('profile = "dev-mac"')
            self.assertEqual(Settings.load(file).profile, "dev-mac")

    def test_start_cancel_close_order_and_idempotent_close(self):
        calls = []
        owner = ProviderOwner(tuple(FakeProvider(name, calls) for name in ("a", "b")))
        self.assertTrue(owner.start())
        owner.cancel()
        owner.close()
        before = list(calls)
        owner.close()
        self.assertEqual(calls, before)
        self.assertEqual(calls, [("a", "start"), ("b", "start"), ("b", "cancel"),
                                 ("a", "cancel"), ("b", "cancel"), ("b", "close"),
                                 ("a", "cancel"), ("a", "close")])
        with self.assertRaises(RuntimeError):
            owner.start()

    def test_failed_partial_start_unwinds_all_allocations(self):
        calls = []
        owner = ProviderOwner((FakeProvider("a", calls), FakeProvider("b", calls, ("start",)),
                               FakeProvider("never", calls)))
        self.assertFalse(owner.start())
        self.assertEqual(calls, [("a", "start"), ("b", "start"), ("b", "cancel"),
                                 ("b", "close"), ("a", "cancel"), ("a", "close")])
        self.assertEqual(owner.state, "closed")
        self.assertEqual(owner.results[1].error_type, "RuntimeError")
        self.assertNotIn("secret fixture", repr(owner.results))

    def test_cleanup_continues_after_cancel_and_close_failures(self):
        calls = []
        owner = ProviderOwner((FakeProvider("a", calls), FakeProvider("b", calls, ("cancel", "close"))))
        owner.start()
        owner.close()
        self.assertEqual(calls[-2:], [("a", "cancel"), ("a", "close")])
        self.assertEqual(sum(r.outcome == "failed" for r in owner.results), 2)
        self.assertEqual(owner.state, "cleanup_failed")

    def test_keyboard_interrupt_during_start_releases_partial_provider(self):
        calls = []
        provider = FakeProvider("interrupted", calls)
        owner = ProviderOwner((provider,))
        with patch.object(provider, "start", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                owner.start()
        self.assertEqual(calls, [("interrupted", "cancel"), ("interrupted", "close")])
        self.assertEqual(owner.state, "closed")

    def test_optional_failure_is_visible_without_substitution(self):
        calls = []
        owner = ProviderOwner((FakeProvider("optional", calls, ("start",), required=False),
                               FakeProvider("required", calls)))
        self.assertTrue(owner.start())
        self.assertEqual(owner.results[0].outcome, "failed")
        owner.close()

    def test_missing_dependency_never_starts_provider(self):
        calls = []
        owner = ProviderOwner((FakeProvider("absent", calls, dependencies=("r3x-nonexistent-fixture",)),))
        self.assertFalse(owner.start())
        self.assertEqual(calls, [])
        self.assertEqual(owner.results[0].error_type, "MissingDependency")

    def test_duplicate_capability_owners_rejected(self):
        with self.assertRaises(ValueError):
            ProviderOwner((FakeProvider("same", []), FakeProvider("same", [])))

    def test_cli_doctor_and_invalid_configuration(self):
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            good = Path(directory) / "good.toml"
            good.write_text('profile = "dev-mac"')
            with patch.object(sys, "argv", ["r3x", "doctor", "--config", str(good)]), redirect_stdout(output), patch("r3x.app.Secrets.load", return_value=Secrets()):
                self.assertEqual(main(), 1)
            result = json.loads(output.getvalue())
            self.assertIn("openai_api_key", result["missing"])
            self.assertFalse(result["conversation_ready"])
            config = Path(directory) / "bad.toml"
            config.write_text("secret = 'should not print'")
            output = io.StringIO()
            with patch.object(sys, "argv", ["r3x", "doctor", "--config", str(config)]), redirect_stderr(output):
                self.assertEqual(main(), 1)
            self.assertNotIn("should not print", output.getvalue())
