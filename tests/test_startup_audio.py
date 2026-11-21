"""The startup whir, the ready line, the power-down clip and saved canned lines through V3's owners;
synthetic TTS and sink only."""
import json
from pathlib import Path
import random
import tempfile
import time
import unittest

from r3x.contracts import Cancellation, Playback
from r3x.output import OutputOwner
from r3x.providers import LifecycleResult
from r3x.settings import Settings
from r3x.startup_audio import ASSETS, READY_LINES, LineCycle, StartupAudio, read_clip
from r3x.startup_notice import announce
from tests.test_voice_core import Trace


class RecordingSink:
    """Consumes pieces at a pace, like a device; records each clip as it plays. Its buffered
    stream records into the same list, so a test sees every clip in order either way."""
    def __init__(self, pace=0.):
        self.played, self.visualized, self.pace = [], [], pace
        self.streams = []   # One entry per buffered stream: [clips played through it, closed]

    def buffered(self):
        sink = self
        class Stream:
            latency = .1
            def __init__(stream): sink.streams.append(stream); stream.clips, stream.closed = 0, False
            def play(stream, chunks, cancel, on_start, visualize=True):
                stream.clips += 1
                return sink.play(chunks, cancel, on_start, visualize)
            def close(stream): stream.closed = True
        return Stream()

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

    def test_only_the_whir_plays_while_he_starts(self):
        # Owner, November 20: the please-wait line and the droid filler are retired.
        tts, sink = CountingTTS(), RecordingSink(pace=.004)
        audio = StartupAudio(tts, sink, self.dir)
        audio.start()
        self.assertTrue(audio.stop())                              # Waits the whir out; never cuts it.
        self.assertFalse(audio._cancel.event.is_set())
        self.assertEqual(sink.played, [read_clip('startup_whir.wav')])   # Nothing is said while he starts.

    def test_the_ready_line_is_chosen_and_fetched_while_he_starts(self):
        # Said the moment he can listen: its sound must already be in hand, even a line never made before.
        class SlowTTS(CountingTTS):
            def synthesize(self, text, cancel):
                time.sleep(.3)                                     # The voice service's round trip.
                return super().synthesize(text, cancel)
        tts = SlowTTS()
        audio = StartupAudio(tts, RecordingSink(pace=.004), self.dir, rng=random.Random(2))
        began = time.monotonic()
        audio.start()
        self.assertLess(time.monotonic() - began, .2)              # Startup never waits for the voice service.
        time.sleep(.5)                                             # Everything else starts meanwhile.
        began = time.monotonic()
        ready = audio.ready()
        self.assertLess(time.monotonic() - began, .1)              # In hand: no pause before he speaks.
        self.assertIn(ready.text, READY_LINES)
        self.assertEqual(ready.pcm, b'\x01\x00' * 400)
        self.assertEqual(tts.texts, [ready.text])                  # Made once.
        self.assertEqual(audio.ready().text, ready.text)           # One line a launch.
        audio.stop()
        again = StartupAudio(tts, RecordingSink(), self.dir, rng=random.Random(2))
        self.assertNotEqual(again.ready().text, ready.text)        # The next launch says a different one.

    def test_a_ready_line_that_cannot_be_made_is_reported_not_waited_for(self):
        class DownTTS(CountingTTS):
            def synthesize(self, text, cancel):
                raise RuntimeError('provider down')
        audio = StartupAudio(DownTTS(), RecordingSink(), self.dir)
        audio.start()
        with self.assertRaises(RuntimeError):
            audio.ready()                                          # Live startup logs it and goes on in silence.
        audio.stop()

    def test_ready_lines_are_the_owners_fifteen_and_short(self):
        self.assertEqual(len(READY_LINES), 15)
        self.assertEqual(len(set(READY_LINES)), 15)
        self.assertIn('Please state the nature of the medical emergency.', READY_LINES)
        self.assertIn('First flight for me too, folks.', READY_LINES)
        self.assertTrue(all(len(line.split()) <= 8 for line in READY_LINES))   # He cannot listen while he says one.

    def test_lines_do_not_repeat_until_every_line_has_played(self):
        state = self.dir / 'state.json'
        state.write_text(json.dumps({'ready': [3, 7, 1], 'boot': [2]}))       # The retired lists' numbered state.
        picks = [LineCycle(state).pick('ready', READY_LINES) for _ in READY_LINES]
        self.assertEqual(sorted(picks), sorted(READY_LINES))    # Every line once, across new instances (launches).
        self.assertIn(LineCycle(state, random.Random(3)).pick('ready', READY_LINES), READY_LINES)   # Then round again.
        fewer = READY_LINES[:3]
        picks = [LineCycle(state).pick('ready', fewer) for _ in fewer]
        self.assertTrue(set(picks) <= set(fewer))                # A changed list is followed, never an old line.

    def test_clips_are_fed_in_small_pieces(self):
        pieces = []
        class Sink(RecordingSink):
            def play(inner, chunks, cancel, on_start, visualize=True):
                pieces.extend(len(c) for c in chunks)
                return Playback(0, 0., 0., 0)
        StartupAudio(CountingTTS(), Sink(), self.dir)._play(read_clip('startup_whir.wav'), 'whir')
        self.assertGreater(len(pieces), 10)
        self.assertLessEqual(max(pieces), 4410)

    def test_whir_waits_for_the_servo_wake(self):
        sink = RecordingSink()
        audio = StartupAudio(CountingTTS(), sink, self.dir, wake_motion=True)
        audio.start()
        time.sleep(.15)
        self.assertEqual(sink.played, [])       # Nothing plays before the servos move.
        audio.motion_started()
        self.assertTrue(audio.stop())
        self.assertEqual(sink.played, [read_clip('startup_whir.wav')])

    def test_power_down_clip_follows_the_whir_while_the_servos_park(self):
        whir, clip = read_clip('startup_whir.wav'), read_clip('robot_power_down.wav')
        sink = RecordingSink(pace=.004)
        audio = StartupAudio(CountingTTS(), sink, self.dir)
        audio.start()
        deadline = time.monotonic() + 3
        while not sink.played and time.monotonic() < deadline:
            time.sleep(.01)
        began = time.monotonic()
        audio.shutdown_started()                                  # The servo owner's park hook.
        self.assertLess(time.monotonic() - began, 1.)             # The park never waits for the power-down sound.
        self.assertTrue(audio._shutdown_thread.is_alive())        # It plays while the servos move.
        audio.close()                                             # After the park, before the sink closes.
        self.assertFalse(audio._shutdown_thread.is_alive())
        self.assertFalse(audio._cancel.event.is_set())            # The whir finished; it was not cut.
        self.assertEqual(sink.played, [whir, clip])               # One clip at a time, each whole.
        self.assertEqual(sink.visualized[1], False)               # No speaking lights on a power-down sound.
        audio.shutdown_started()                                  # A second park request does not replay it.
        self.assertEqual(len(sink.played), 2)

    def test_the_whir_uses_one_buffered_stream_closed_before_live_speech(self):
        # October 22: startup sound stuttered while models loaded; it is buffered by the driver.
        sink = RecordingSink(pace=.004)
        audio = StartupAudio(CountingTTS(), sink, self.dir)
        audio.start()
        self.assertTrue(audio.stop())
        self.assertEqual(len(sink.streams), 1)
        self.assertEqual(sink.streams[0].clips, 1)              # The whir.
        self.assertTrue(sink.streams[0].closed)                 # Closed before any live speech.
        audio.shutdown_started()
        audio.close()
        self.assertEqual(sink.streams[0].clips, 1)              # Power-down uses ordinary playback.
        self.assertEqual(len(sink.played), 2)

    def test_canned_lines_are_cached_in_the_background_without_talking_over_speech(self):
        lines = ('Out of credit.', 'Intruder alert.', 'A sensor went dark.')
        tts = CountingTTS()
        audio = StartupAudio(tts, RecordingSink(), self.dir)
        audio.speech(lines[0], Cancellation())                   # One line already cached by a launch.
        busy = {'speaking': True}
        thread = audio.warm(lines + lines[:1], busy=lambda: busy['speaking'], interval=0.)
        time.sleep(.3)
        self.assertEqual(tts.texts, [lines[0]])                  # Nothing is synthesized while Rex speaks.
        busy['speaking'] = False
        thread.join(5.)
        self.assertFalse(thread.is_alive())
        self.assertEqual(sorted(tts.texts), sorted(lines))       # Each line exactly once.
        self.assertEqual(len(list(self.dir.glob('startup-tts/*.pcm'))), len(lines))
        self.assertIsNone(StartupAudio(tts, RecordingSink(), self.dir).warm(lines))   # A warm cache: no work.
        again = StartupAudio(tts, RecordingSink(), self.dir)
        for line in lines:
            again.speech(line, Cancellation())
        self.assertEqual(len(tts.texts), len(lines))             # Later launches never synthesize.

    def test_warming_stops_at_close_and_after_a_provider_failure(self):
        lines = tuple('Line %d.' % n for n in range(12))
        class FailingTTS(CountingTTS):
            def synthesize(self, text, cancel):
                self.texts.append(text)
                raise RuntimeError('provider down')
        tts = FailingTTS()
        thread = StartupAudio(tts, RecordingSink(), self.dir).warm(lines, interval=0.)
        thread.join(2.)
        self.assertEqual(len(tts.texts), 1)                      # One failure ends the pass; no retries.
        slow = CountingTTS()
        audio = StartupAudio(slow, RecordingSink(), self.dir)
        audio.warm(lines, interval=.2)
        time.sleep(.05)
        audio.close()
        self.assertFalse(audio._warm_thread.is_alive())
        self.assertLess(len(slow.texts), len(lines))

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
        ready = audio.ready()
        healthy = type('Owner', (), {'results': [LifecycleResult('tts', 'start', 'ok'), LifecycleResult('playback', 'start', 'ok')]})()
        delivery = announce(Settings(), healthy, output, trace, ready=ready)
        self.assertEqual(delivery.status, 'completed')
        self.assertEqual(delivery.completed_text, [ready.text])              # A healthy start says only that he is ready.
        self.assertIsNone(announce(Settings(), healthy, output, trace))      # No line in hand: silence, not a stall.
        owner = type('Owner', (), {'results': healthy.results + [LifecycleResult('drive_base', 'start', 'failed', 'ProviderFailure')]})()
        delivery = announce(Settings(), owner, output, trace, ready=ready)
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
