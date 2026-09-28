"""V2 startup sounds and canned lines through V3's owners; synthetic TTS and sink only."""
from pathlib import Path
import random
import tempfile
import time
import unittest

from r3x.contracts import Cancellation, Playback
from r3x.output import OutputOwner
from r3x.providers import LifecycleResult
from r3x.settings import Settings
from r3x.startup_audio import ASSETS, BOOT_LINES, READY_LINES, LineCycle, StartupAudio, read_clip
from r3x.startup_notice import announce
from tests.test_voice_core import Trace


class RecordingSink:
    def __init__(self, block_on=None):
        self.played, self.block_on = [], block_on

    def play(self, chunks, cancel, on_start):
        data = b''.join(chunks)
        on_start(time.monotonic())
        self.played.append(data)
        if self.block_on is not None and len(data) == self.block_on:
            while not cancel.event.is_set():   # A long clip playing until interrupted.
                time.sleep(.005)
            cancel.check()
        return Playback(len(data)//2, time.monotonic(), time.monotonic(), 0)

    def cancel(self):
        pass


class CountingTTS:
    voice_id = 'voice'

    class capability:
        model = 'test-model'

    def __init__(self):
        self.texts = []

    def synthesize(self, text, cancel):
        self.texts.append(text)
        yield b'\x01\x00' * 400


class StartupAudioTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_whir_then_please_wait_line_then_filler_which_readiness_interrupts(self):
        filler = read_clip('droid_startup.wav')
        tts, sink = CountingTTS(), RecordingSink(block_on=len(filler))
        audio = StartupAudio(tts, sink, self.dir, rng=random.Random(1))
        audio.start()
        deadline = time.monotonic() + 3
        while len(sink.played) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue(audio.stop())
        self.assertEqual(sink.played[0], read_clip('startup_whir.wav'))
        self.assertIn(tts.texts[0], BOOT_LINES)
        self.assertEqual(sink.played[2], filler)

    def test_whir_waits_for_servo_wake_and_the_line_gets_talking_motion(self):
        events = []
        class Talking:
            def start(self, turn_id, cancel): events.append('talk_start')
            def stop(self): events.append('talk_stop')
        class Robot:
            owner = type('Owner', (), {'state': 'ready'})()
            talking = Talking()
        class Sink(RecordingSink):
            def play(inner, chunks, cancel, on_start):
                events.append('play')
                return RecordingSink.play(inner, chunks, cancel, on_start)
        robot, sink = Robot(), Sink()
        audio = StartupAudio(CountingTTS(), sink, self.dir, robot=robot)
        audio.start()
        time.sleep(.15)
        self.assertEqual(events, [])            # Nothing plays before the servos move.
        audio.motion_started()
        deadline = time.monotonic() + 2
        while 'play' not in events and time.monotonic() < deadline:
            time.sleep(.01)
        time.sleep(.1)
        self.assertEqual(events, ['play'])      # Whir with the wake-up; line waits for it to finish.
        robot.owner.state = 'active'
        deadline = time.monotonic() + 2
        while events.count('play') < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        audio.stop()
        self.assertEqual(events[:4], ['play', 'talk_start', 'play', 'talk_stop'])

    def test_lines_do_not_repeat_until_every_line_has_played(self):
        cycle = LineCycle(self.dir / 'state.json', random.Random(3))
        picks = [LineCycle(self.dir / 'state.json').pick('ready', READY_LINES) for _ in READY_LINES]
        self.assertEqual(sorted(picks), sorted(READY_LINES))   # Survives new instances (launches).
        self.assertIn(cycle.pick('ready', READY_LINES), READY_LINES)

    def test_canned_lines_are_synthesized_once_then_cached(self):
        tts = CountingTTS()
        first = StartupAudio(tts, RecordingSink(), self.dir).speech('Hang tight.', Cancellation())
        again = StartupAudio(tts, RecordingSink(), self.dir).speech('Hang tight.', Cancellation())
        self.assertEqual(first, again)
        self.assertEqual(tts.texts, ['Hang tight.'])

    def test_ready_line_plays_after_the_hardware_notice_with_normal_receipts(self):
        from r3x.scenario import SyntheticSink, SyntheticTTS
        trace = Trace()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
        audio = StartupAudio(CountingTTS(), RecordingSink(), self.dir)
        ready = audio.ready(Cancellation())
        owner = type('Owner', (), {'results': [LifecycleResult('drive_base', 'start', 'failed', 'ProviderFailure'),
                                               LifecycleResult('tts', 'start', 'ok'), LifecycleResult('playback', 'start', 'ok')]})()
        delivery = announce(Settings(), owner, output, trace, ready=ready)
        self.assertEqual(delivery.status, 'completed')
        self.assertEqual(delivery.completed_text, ['Hardware unavailable: wheel controller.', ready.text])
        self.assertIn(ready.text, READY_LINES)
        failed = announce(Settings(), owner, output, trace, failed=True, ready=ready)
        self.assertNotIn(ready.text, failed.completed_text)   # Failed startup never claims readiness.

    def test_bundled_clips_are_22050_hz_mono_16_bit(self):
        for path in sorted(ASSETS.glob('*.wav')):
            self.assertTrue(read_clip(path.name))
        self.assertEqual(len(list(ASSETS.glob('*.wav'))), 9)


if __name__ == '__main__':
    unittest.main()
