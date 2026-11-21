from contextlib import redirect_stdout, redirect_stderr
import importlib
import io
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from r3x.app import App
from r3x.providers import Alongside, Capability, Preload, ProviderOwner
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

    # Startup groups (owner, November 20): the models load beside the servo wake, not after it.

    def test_a_group_starts_beside_the_main_sequence_and_start_returns_only_when_it_is_done(self):
        calls, later_started, group_done = [], threading.Event(), threading.Event()
        class Slow(FakeProvider):
            def start(inner):
                # Finishes only once the main sequence has moved on: it was not waited for.
                self.assertTrue(later_started.wait(2))
                super().start(); group_done.set()
        class Later(FakeProvider):
            def start(inner):
                super().start(); later_started.set()
        first, slow, later = FakeProvider("first", calls), Slow("slow", calls), Later("later", calls)
        owner = ProviderOwner((first, slow, later), (Alongside((slow,), after=first),))
        self.assertTrue(owner.start())
        self.assertTrue(group_done.is_set())                     # Ready means everything has started.
        self.assertEqual(calls, [("first", "start"), ("later", "start"), ("slow", "start")])
        del calls[:]
        owner.close()
        # Closing follows the listed order reversed, whichever thread started what.
        self.assertEqual([c for c in calls if c[1] == "close"], [("later", "close"), ("slow", "close"), ("first", "close")])
        self.assertEqual(owner.state, "closed")

    def test_the_main_sequence_waits_for_a_group_it_must_not_pass(self):
        calls, release = [], threading.Event()
        class Model(FakeProvider):
            def start(inner):
                release.wait(2); super().start()
        class Body(FakeProvider):
            def start(inner):
                super().start(); release.set()   # The group loads while the body wakes.
        first, model, body, microphone = (FakeProvider("first", calls), Model("model", calls),
                                          Body("body", calls), FakeProvider("microphone", calls))
        owner = ProviderOwner((first, body, model, microphone), (Alongside((model,), after=first, before=microphone),))
        self.assertTrue(owner.start()); self.addCleanup(owner.close)
        self.assertEqual(calls, [("first", "start"), ("body", "start"), ("model", "start"), ("microphone", "start")])

    def test_a_required_failure_in_a_group_stops_the_sequence_tells_once_and_unwinds(self):
        import time
        calls, told, body_began = [], [], threading.Event()
        class Broken(FakeProvider):
            def start(inner):
                self.assertTrue(body_began.wait(2)); super().start()   # Fails while the body is mid-wake.
        class Body(FakeProvider):
            def start(inner):
                super().start(); body_began.set()
                deadline = time.monotonic() + 2
                while ("broken", "close") not in calls and time.monotonic() < deadline:
                    time.sleep(.005)
        providers = (FakeProvider("first", calls), Broken("broken", calls, ("start",)), FakeProvider("spared", calls),
                     Body("body", calls), FakeProvider("never", calls))
        owner = ProviderOwner(providers, (Alongside(providers[1:3], after=providers[0]),))
        self.assertFalse(owner.start(on_required_failure=lambda: told.append(list(calls))))
        self.assertEqual(len(told), 1)
        self.assertNotIn(("first", "close"), told[0])            # Said before anything else is released.
        started = [name for name, operation in calls if operation == "start"]
        self.assertEqual(started, ["first", "body", "broken"])   # Nothing new after the failure, in either thread.
        self.assertEqual([c for c in calls if c[1] == "close"], [("broken", "close"), ("body", "close"), ("first", "close")])
        self.assertEqual(owner.state, "closed")

    def test_a_dependent_in_another_group_waits_for_what_it_requires(self):
        for camera_fails in (False, True):
            calls, release = [], threading.Event()
            class Camera(FakeProvider):
                def start(inner):
                    release.wait(2); super().start()
            class Microphone(FakeProvider):
                def start(inner):
                    super().start(); release.set()
            first = FakeProvider("first", calls)
            camera = Camera("camera", calls, ("start",) if camera_fails else (), required=False)
            watcher = FakeProvider("watcher", calls, required=False)
            watcher.capability = Capability("watcher", "synthetic", "lifecycle contract", required=False, requires=("camera",))
            microphone = Microphone("microphone", calls)
            owner = ProviderOwner((first, microphone, camera, watcher),
                                  (Alongside((camera,), after=first), Alongside((watcher,), after=first)))
            self.assertTrue(owner.start())
            started = [name for name, operation in calls if operation == "start"]
            outcome = {r.capability: r for r in owner.results if r.operation == "start"}
            if camera_fails:
                self.assertEqual(started, ["first", "microphone", "camera"])
                self.assertEqual((outcome["watcher"].outcome, outcome["watcher"].reason), ("skipped", "RequiredCapabilityUnavailable"))
            else:
                self.assertEqual(started, ["first", "microphone", "camera", "watcher"])   # Never before the camera is up.
            owner.close()

    def test_an_interrupt_waits_out_the_groups_then_releases_everything(self):
        calls, loading = [], threading.Event()
        class Model(FakeProvider):
            def start(inner):
                loading.set(); super().start()
        first, model, unloaded, body = (FakeProvider("first", calls), Model("model", calls),
                                        FakeProvider("unloaded", calls), FakeProvider("body", calls))
        def interrupted():
            self.assertTrue(loading.wait(2))
            raise KeyboardInterrupt
        owner = ProviderOwner((first, model, unloaded, body), (Alongside((model, unloaded), after=first),))
        with patch.object(body, "start", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                owner.start()
        self.assertEqual(owner.state, "closed")
        started = {name for name, operation in calls if operation == "start"}
        self.assertEqual({name for name, operation in calls if operation == "close"}, started | {"body"})

    def test_each_start_records_when_it_began_and_how_long_it_took(self):
        calls = []
        first, beside = FakeProvider("first", calls), FakeProvider("beside", calls)
        owner = ProviderOwner((first, beside), (Alongside((beside,), after=first),))
        self.assertTrue(owner.start()); self.addCleanup(owner.close)
        timings = owner.timings()
        self.assertEqual(set(timings["steps"]), {"first", "beside"})
        for began, seconds in timings["steps"].values():
            self.assertGreaterEqual(began, 0); self.assertGreaterEqual(seconds, 0)
        self.assertGreaterEqual(timings["seconds"], 0)

    def test_groups_must_be_made_of_owned_providers_and_hang_off_the_main_sequence(self):
        a, b, c = (FakeProvider(name, []) for name in "abc")
        for providers, groups in (((a, b), (Alongside((c,), after=a),)),                         # Not owned here.
                                  ((a, b, c), (Alongside((b,), after=a), Alongside((c,), after=b))),   # Begins after a grouped one.
                                  ((a, b, c), (Alongside((b,), after=a), Alongside((b,), after=a))),   # Listed twice.
                                  ((a, b, c), (Alongside((c,), after=b, before=a),))):          # Awaited before it begins.
            with self.assertRaises(ValueError):
                ProviderOwner(providers, groups)

    def test_models_loaded_early_are_found_by_their_owners_and_closed_if_never_used(self):
        from r3x.contracts import ProviderFailure
        class Model:
            def __init__(inner, fails=False): inner.loads, inner.loaded, inner.fails = 0, False, fails
            def start(inner):
                if inner.loaded: return
                if inner.fails: raise ProviderFailure('ModelDigestMismatch')
                inner.loads += 1; inner.loaded = True
            def close(inner): inner.loaded = False
        class Camera(FakeProvider):
            def __init__(inner, model, calls, failures=()):
                super().__init__('camera', calls, failures, required=False); inner.model = model
            def start(inner): super().start(); inner.model.start()
            def close(inner): super().close(); inner.model.close()
        for camera_fails in (False, True):
            calls, faces, rooms = [], Model(), Model()
            early = Preload('camera_models', (faces, rooms), 'loaded during the wake')
            body = FakeProvider('body', calls)
            camera = Camera(faces, calls, ('start',) if camera_fails else ())
            owner = ProviderOwner((body, early, camera), (Alongside((early,), after=body), Alongside((camera,), after=body)))
            self.assertTrue(owner.start())
            self.assertEqual((faces.loads, rooms.loads), (1, 1))        # Once each, whoever asked first.
            if camera_fails:
                owner.close_only({'camera_models'})                     # What live startup does with no camera.
                self.assertFalse(faces.loaded or rooms.loaded)          # Nothing is left loaded and unused.
            owner.close()
            self.assertFalse(faces.loaded or rooms.loaded)
            self.assertEqual(owner.state, 'closed')
        # One model that cannot load fails the early step by name and leaves nothing loaded by it;
        # startup carries on, and each owner loads and reports its own.
        good, bad = Model(), Model(fails=True)
        owner = ProviderOwner((Preload('camera_models', (good, bad), 'loaded during the wake'),))
        self.assertTrue(owner.start())
        self.assertEqual((owner.results[0].outcome, owner.results[0].reason), ('failed', 'Model:ModelDigestMismatch'))
        self.assertFalse(good.loaded)
        owner.close()

    def test_rex_loads_his_models_while_his_body_wakes_and_listens_after(self):
        import time
        settings = Settings(servo_port='/dev/cu.synthetic', led_port='/dev/cu.leds', drive_port='/dev/cu.wheels',
                            radar_port='/dev/cu.radar', camera_enabled=True, camera_device_name='Fixture',
                            object_memory_enabled=True, smart_turn_enabled=True, voice_check_enabled=True,
                            place_recognition_enabled=True, face_expressions_enabled=True)
        with tempfile.TemporaryDirectory() as folder:
            app = App(settings, Path(folder)); app.configure_live()
            began, ended, patches = {}, {}, []
            def start(name):
                began[name] = time.monotonic()
                if name == 'robot_servos':
                    time.sleep(.3)        # The servo wake: the longest single step.
                elif name in ('asr', 'camera'):
                    time.sleep(.1)
                ended[name] = time.monotonic()
            try:
                for provider in app.owner.providers:
                    for method in ('start', 'cancel', 'close'):
                        p = patch.object(provider, method, side_effect=(lambda name=provider.capability.name: start(name)) if method == 'start' else None)
                        p.start(); patches.append(p)
                with patch('r3x.providers.dependency_status', return_value={}):
                    self.assertTrue(app.owner.start())
                names = [p.capability.name for p in app.owner.providers]
                self.assertEqual(set(began), set(names))                           # Everything started.
                self.assertEqual(names[:5], ['tts', 'playback', 'startup_audio', 'robot_leds', 'robot_servos'])
                wake_ended = ended['robot_servos']
                for model in ('asr', 'vad', 'voice_encoder', 'turn_detection', 'voice_check', 'voice_cloner'):
                    self.assertLess(began[model], wake_ended)                      # Loaded during the wake, not after.
                    self.assertLess(ended[model], began['capture'])                # And done before the microphone opens.
                self.assertLess(ended['tts'], began['asr'])                        # Speech output first: a failure can be said.
                # The camera-side models load during the wake too, ahead of the camera that uses them.
                self.assertLess(began['camera_models'], wake_ended)
                self.assertLess(ended['camera_models'], began['camera'])
                self.assertEqual(app.early.models, (app.owner.providers[names.index('camera')].encoder,
                    app.owner.providers[names.index('expressions')], app.owner.providers[names.index('objects')].detector,
                    app.owner.providers[names.index('places')].encoder))
                self.assertLess(names.index('camera_models'), names.index('camera'))   # So it closes after its owners.
                for after_wake in ('capture', 'camera', 'drive_base', 'radar'):
                    self.assertGreaterEqual(began[after_wake], wake_ended)         # Motors quiet before he hears or sees.
                self.assertLess(began['drive_base'], ended['camera'])              # Wheels beside the camera, not behind it.
                for watcher in ('scene_observations', 'current_view', 'objects', 'places', 'expressions'):
                    self.assertGreaterEqual(began[watcher], ended['camera'])       # What watches through the camera waits for it.
                app.close()
            finally:
                for p in reversed(patches): p.stop()

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
