"""Robot camera mode and startup failure contracts; no real devices."""
from contextlib import redirect_stdout
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from r3x.app import App
from r3x.contracts import ProviderFailure
from r3x.settings import Settings
from r3x.terminal import LiveConsole


class RobotCameraTests(unittest.TestCase):
    def test_selected_mode_reaches_capture_without_starting_other_providers(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)/'settings.toml'
            config.write_text('camera_enabled = true\ncamera_device_name = "Fixture camera"\n'
                              'camera_width = 1920\ncamera_height = 1080\ncamera_framerate = 30.00003\n')
            app = App(Settings.load(config), Path(folder))
            app.configure_live()
            camera = next(p for p in app.live_components if p.capability.name == 'camera')
            listing = SimpleNamespace(stderr='AVFoundation video devices:\n[0] Fixture camera\nAVFoundation audio devices:')
            with patch.object(camera.encoder, 'start'), patch('subprocess.run', return_value=listing), \
                 patch('subprocess.Popen') as popen, patch('shutil.which', return_value='/fixture/ffmpeg'), \
                 patch('threading.Thread') as thread:
                thread.return_value.start.side_effect = camera.ready.set
                camera.start()
            command = popen.call_args.args[0]
            self.assertEqual(command[command.index('-video_size')+1], '1920x1080')
            self.assertEqual(command[command.index('-framerate')+1], '30.00003')
            self.assertEqual(command[command.index('-i')+1], '0:none')
            self.assertEqual(command[command.index('-fps_mode')+1], 'passthrough')
            self.assertFalse(app.paths.root.exists())

    def test_duplicate_pixels_do_not_refresh_camera_evidence(self):
        from r3x.adapters.camera import Camera
        camera = Camera('Fixture', Path('/fixture/models'), trace=Mock(),width=2,height=2)
        first,changed=b'a'*12,b'b'*12
        camera.process=Mock(stdout=io.BytesIO(first+first+first+changed))
        camera.hub.publish=Mock()
        camera._read()
        frames=[call.args[0] for call in camera.hub.publish.call_args_list]
        self.assertEqual([f.pixels for f in frames],[first,changed])
        self.assertNotEqual(frames[0].frame_id,frames[1].frame_id)
        events=camera.trace.emit.call_args_list
        self.assertEqual(sum(c.args[0]=='camera_duplicate_frame' for c in events),1)
        resumed=next(c for c in events if c.args[0]=='camera_frames_resumed')
        self.assertEqual(resumed.kwargs['skipped_duplicates'],2)
        self.assertEqual(camera.failure,'CameraStreamEnded')

    def test_invalid_modes_rejected_before_startup(self):
        for values in ({'camera_width':1921}, {'camera_height':0}, {'camera_width':True},
                       {'camera_framerate':float('nan')}, {'camera_framerate':0}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Settings(**values)

    def test_camera_stream_failure_retains_safe_reason(self):
        from r3x.adapters.camera import Camera
        camera = Camera('Fixture', Path('/fixture/models'), trace=Mock())
        camera._failed(ProviderFailure('CameraStreamEnded'))
        self.assertEqual(camera.failure, 'CameraStreamEnded')
        self.assertTrue(camera.stop.is_set())
        self.assertEqual(camera.trace.emit.call_args.kwargs['reason'], 'CameraStreamEnded')

    def test_startup_failure_does_not_claim_cleanup_failed(self):
        for operation, expected in [('start',False), ('cancel',True), ('close',True)]:
            output = io.StringIO()
            with redirect_stdout(output):
                LiveConsole()({'event':'shutdown','report':{'results':[
                    {'operation':operation, 'outcome':'failed'}]}})
            self.assertEqual('Cleanup error' in output.getvalue(), expected)

    def test_presence_cache_covers_analysis_interval_but_expires_and_preserves_empty_frame(self):
        from r3x.adapters.camera import Camera
        from tests.test_proactive import face
        camera=Camera('fixture',Path('/unused'))
        rows=(face(100),)
        camera.tracker.history.append((100,rows))
        self.assertEqual(camera.latest(101.1),rows)
        self.assertEqual(camera.latest(101.51),())
        camera.tracker.history.append((101.2,()))
        self.assertEqual(camera.latest(101.3),())
