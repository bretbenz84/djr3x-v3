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
    """Consumes pieces at a pace, like a device; records each clip as it plays."""
    def __init__(self, pace=0.):
        self.played, self.visualized, self.pace = [], [], pace

    def play(self, chunks, cancel, on_start, visualize=True):
        on_start(time.monotonic())
        self.played.append(b'')
        self.visualized.append(visualize)
        for chunk in chunks:
            cancel.check()
            self.played[-1] += chunk
            if self.pace:
                time.sleep(self.pace)
        return Playback(len(self.played[-1])//2, time.monotonic(), time.monotonic(), 0)

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

    def test_whir_then_line_then_filler_which_readiness_drains_gently(self):
        filler = read_clip('droid_startup.wav')
        tts, sink = CountingTTS(), RecordingSink(pace=.004)
        audio = StartupAudio(tts, sink, self.dir, rng=random.Random(1))
        audio.start()
        deadline = time.monotonic() + 3
        while len(sink.played) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        time.sleep(.05)
        began = time.monotonic()
        self.assertTrue(audio.stop())
        self.assertLess(time.monotonic() - began, 1.)             # Readiness does not wait out the clip.
        self.assertFalse(audio._cancel.event.is_set())             # Starved and drained, never force-stopped.
        self.assertEqual(sink.played[0], read_clip('startup_whir.wav'))
        self.assertIn(tts.texts[0], BOOT_LINES)
        self.assertTrue(filler.startswith(sink.played[2]) and 0 < len(sink.played[2]) < len(filler))

    def test_clips_are_fed_in_small_pieces(self):
        pieces = []
        class Sink(RecordingSink):
            def play(inner, chunks, cancel, on_start, visualize=True):
                pieces.extend(len(c) for c in chunks)
                return Playback(0, 0., 0., 0)
        StartupAudio(CountingTTS(), Sink(), self.dir)._play(read_clip('startup_whir.wav'), 'whir')
        self.assertGreater(len(pieces), 10)
        self.assertLessEqual(max(pieces), 4410)

    def test_whir_waits_for_servo_wake_and_the_line_gets_talking_motion(self):
        events = []
        class Talking:
            def start(self, turn_id, cancel): events.append('talk_start')
            def stop(self): events.append('talk_stop')
        class Robot:
            owner = type('Owner', (), {'state': 'ready'})()
            talking = Talking()
        class Sink(RecordingSink):
            def play(inner, chunks, cancel, on_start, visualize=True):
                events.append('play')
                return RecordingSink.play(inner, chunks, cancel, on_start, visualize)
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

    def test_readiness_lets_the_line_finish_but_cuts_the_filler(self):
        line_bytes = b'\x01\x00' * 400
        outcome = {}
        filler = read_clip('droid_startup.wav')
        class Sink(RecordingSink):
            def play(inner, chunks, cancel, on_start, visualize=True):
                data = b''
                for chunk in chunks:
                    data += chunk
                if data == line_bytes:
                    outcome['line_started'] = True
                    time.sleep(.3)   # The line is audible while startup finishes.
                    outcome['line_cut'] = cancel.event.is_set()
                if filler.startswith(data) and data:
                    outcome['filler_started'] = True
                return Playback(len(data)//2, 0., 0., 0)
        audio = StartupAudio(CountingTTS(), Sink(), self.dir)
        audio.start()
        deadline = time.monotonic() + 3
        while 'line_started' not in outcome and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(audio.stop())
        self.assertFalse(outcome['line_cut'])
        self.assertNotIn('filler_started', outcome)   # Readiness arrived before the filler: it never starts.

    def test_line_is_fetched_during_the_whir_and_wake_so_it_follows_them_without_a_pause(self):
        events = []
        class SlowTTS(CountingTTS):
            def synthesize(self, text, cancel):
                events.append(('synthesis_started', time.monotonic()))
                time.sleep(.5)                                  # An uncached line: a hosted round trip.
                return super().synthesize(text, cancel)
        class Robot:
            owner = type('Owner', (), {'state': 'ready'})()
            talking = type('Talking', (), {'start': lambda s, t, c: None, 'stop': lambda s: None})()
        class Sink(RecordingSink):
            def play(inner, chunks, cancel, on_start, visualize=True):
                events.append(('play_started', time.monotonic()))
                result = RecordingSink.play(inner, chunks, cancel, on_start, visualize)
                events.append(('play_finished', time.monotonic()))
                return result
        robot, sink = Robot(), Sink(pace=.02)                   # The whir takes about 0.8 s.
        audio = StartupAudio(SlowTTS(), sink, self.dir, robot=robot)
        audio.start()
        audio.motion_started()
        deadline = time.monotonic() + 3
        while sum(1 for kind, _ in events if kind == 'play_finished') < 1 and time.monotonic() < deadline:
            time.sleep(.01)
        robot.owner.state = 'active'                            # The wake finishes as the whir ends.
        deadline = time.monotonic() + 3
        while sum(1 for kind, _ in events if kind == 'play_started') < 2 and time.monotonic() < deadline:
            time.sleep(.005)
        audio.stop()
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[:4], ['synthesis_started', 'play_started', 'play_finished', 'play_started'])
        whir_finished = events[2][1]
        line_started = events[3][1]
        self.assertLess(line_started - whir_finished, .25)      # No synthesis gap after the whir.

    def test_power_down_clip_follows_stopped_startup_sound_while_the_servos_park(self):
        filler, clip = read_clip('droid_startup.wav'), read_clip('robot_power_down.wav')
        sink = RecordingSink(pace=.004)
        audio = StartupAudio(CountingTTS(), sink, self.dir, rng=random.Random(1))
        audio.start()
        deadline = time.monotonic() + 3
        while len(sink.played) < 3 and time.monotonic() < deadline:
            time.sleep(.01)
        began = time.monotonic()
        audio.shutdown_started()                                  # The servo owner's park hook.
        self.assertLess(time.monotonic() - began, 1.)             # The park never waits for the sound.
        self.assertTrue(audio._shutdown_thread.is_alive())        # It plays while the servos move.
        audio.close()                                             # After the park, before the sink closes.
        self.assertFalse(audio._shutdown_thread.is_alive())
        self.assertFalse(audio._cancel.event.is_set())            # Startup filler was starved, not cut.
        self.assertTrue(filler.startswith(sink.played[2]) and len(sink.played[2]) < len(filler))
        self.assertEqual(sink.played[3], clip)                    # The whole power-down clip, after the filler.
        self.assertEqual(sink.visualized[3], False)               # No speaking lights on a power-down sound.
        self.assertEqual(len(sink.played), 4)
        audio.shutdown_started()                                  # A second park request does not replay it.
        self.assertEqual(len(sink.played), 4)

    def test_startup_lines_are_short(self):
        self.assertTrue(all(len(line.split()) <= 7 for line in BOOT_LINES + READY_LINES))

    def test_lines_do_not_repeat_until_every_line_has_played(self):
        cycle = LineCycle(self.dir / 'state.json', random.Random(3))
        picks = [LineCycle(self.dir / 'state.json').pick('ready', READY_LINES) for _ in READY_LINES]
        self.assertEqual(sorted(picks), sorted(READY_LINES))   # Survives new instances (launches).
        self.assertIn(cycle.pick('ready', READY_LINES), READY_LINES)

    def test_every_boot_and_ready_line_is_cached_in_the_background_without_talking_over_speech(self):
        tts = CountingTTS()
        audio = StartupAudio(tts, RecordingSink(), self.dir)
        audio.speech(BOOT_LINES[0], Cancellation())              # One line already cached by a launch.
        busy = {'speaking': True}
        thread = audio.warm(busy=lambda: busy['speaking'], interval=0.)
        time.sleep(.3)
        self.assertEqual(tts.texts, [BOOT_LINES[0]])             # Nothing is synthesized while Rex speaks.
        busy['speaking'] = False
        thread.join(5.)
        self.assertFalse(thread.is_alive())
        expected = list(BOOT_LINES + READY_LINES)
        self.assertEqual(sorted(tts.texts), sorted(expected))    # Each line exactly once.
        self.assertEqual(len(list(self.dir.glob('startup-tts/*.pcm'))), len(expected))
        self.assertIsNone(StartupAudio(tts, RecordingSink(), self.dir).warm())   # A warm cache: no work.
        again = StartupAudio(tts, RecordingSink(), self.dir)
        for line in expected:
            again.speech(line, Cancellation())
        self.assertEqual(len(tts.texts), len(expected))          # Later launches never synthesize.

    def test_warming_stops_at_close_and_after_a_provider_failure(self):
        class FailingTTS(CountingTTS):
            def synthesize(self, text, cancel):
                self.texts.append(text)
                raise RuntimeError('provider down')
        tts = FailingTTS()
        thread = StartupAudio(tts, RecordingSink(), self.dir).warm(interval=0.)
        thread.join(2.)
        self.assertEqual(len(tts.texts), 1)                      # One failure ends the pass; no retries.
        slow = CountingTTS()
        audio = StartupAudio(slow, RecordingSink(), self.dir)
        audio.warm(interval=.2)
        time.sleep(.05)
        audio.close()
        self.assertFalse(audio._warm_thread.is_alive())
        self.assertLess(len(slow.texts), len(BOOT_LINES + READY_LINES))

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
