"""V2 operator companions copied into operator_tools/: V3 wiring only, no devices."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'operator_tools'
sys.path.insert(0, str(TOOLS))

import v3_settings  # noqa: E402
import rex_supervisor  # noqa: E402


class OperatorToolsTests(unittest.TestCase):
    def test_v3_settings_supply_the_v2_names_each_program_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.toml'
            path.write_text('input_device_name = "reSpeaker Flex"\noutput_device_name = "reSpeaker Flex"\n'
                            'microphone_mode = "respeaker-flex"\nservo_port = "/dev/cu.maestro"\n'
                            'head_led_port = "/dev/cu.head"\nchest_led_port = "/dev/cu.chest"\n'
                            'drive_port = "/dev/cu.drive"\nradar_port = "/dev/cu.radar"\n')
            with patch.object(v3_settings, 'SETTINGS', path):
                self.assertEqual(v3_settings.env(), {
                    'AUDIO_DEVICE_NAME': 'reSpeaker Flex', 'AUDIO_OUTPUT_DEVICE_NAME': 'reSpeaker Flex',
                    'AUDIO_AEC_INPUT_CHANNEL': '0', 'MAESTRO_PORT': '/dev/cu.maestro',
                    'ARDUINO_HEAD_PORT': '/dev/cu.head', 'ARDUINO_CHEST_PORT': '/dev/cu.chest',
                    'MOTION_ESP32_PORT': '/dev/cu.drive'})
            with patch.object(v3_settings, 'SETTINGS', Path(directory) / 'missing.toml'):
                self.assertEqual(v3_settings.env(), {})

    def test_supervisor_launches_the_v3_controller(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(rex_supervisor, '_CONTROLLER_CONSOLE_LOG', Path(directory) / 'console.log'), \
             patch.object(rex_supervisor.subprocess, 'Popen') as popen:
            rex_supervisor._launch_controller()
        argv, kwargs = popen.call_args.args[0], popen.call_args.kwargs
        self.assertEqual(argv, [str(ROOT / '.venv/bin/python'), '-m', 'r3x', 'live'])
        self.assertEqual(kwargs['cwd'], str(ROOT))

    def test_wedged_microphone_exit_is_relaunched_and_matches_v3(self):
        from r3x.__main__ import MIC_WEDGE_EXIT_CODE
        policy = rex_supervisor.WedgeRelaunchPolicy(enabled=True, max_relaunches=2, window_secs=600)
        self.assertEqual(rex_supervisor.MIC_WEDGE_EXIT_CODE, MIC_WEDGE_EXIT_CODE)
        self.assertFalse(policy.should_relaunch(0, 10.))             # "Shut down": back to listening.
        self.assertFalse(policy.should_relaunch(1, 10.))
        self.assertTrue(policy.should_relaunch(MIC_WEDGE_EXIT_CODE, 10.))
        self.assertTrue(policy.should_relaunch(MIC_WEDGE_EXIT_CODE, 20.))
        self.assertFalse(policy.should_relaunch(MIC_WEDGE_EXIT_CODE, 30.))   # Bounded: no boot loop.

    def test_automatic_updates_target_the_v3_checkout(self):
        from utils import repo_updater
        with patch.object(repo_updater, 'update_repository', return_value='result') as update:
            self.assertEqual(rex_supervisor._check_for_update(apply=False, trigger='test'), 'result')
        self.assertEqual(update.call_args.args[0], ROOT)

    def test_copies_never_reach_back_into_v2(self):
        for path in TOOLS.rglob('*.py'):
            text = path.read_text()
            self.assertNotIn('djr3x-v2', text, path)
            self.assertNotIn('".env"', text, path)
        for name in ('startup_chime', 'droid_gaining_electric', 'droid_losing_electric'):
            self.assertTrue((ROOT / 'assets/audio/operator' / (name + '.wav')).is_file())


if __name__ == '__main__':
    unittest.main()
