"""The startup sound, the ready line, the power-down clip and saved canned lines through V3's owners;
synthetic TTS and sink only."""
from array import array
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
from r3x.startup_audio import ASSETS, READY_LINES, STARTUP_CLIP, LineCycle, StartupAudio, read_clip, split_at_pause
from r3x.startup_notice import announce
from tests.test_voice_core import Trace


BUILD, FINISH = split_at_pause(read_clip(STARTUP_CLIP))   # The owner's startup sound, cut at its pause.


def tone(seconds, level=3000):
    return array('h', [level if i % 40 < 20 else -level for i in range(int(22050 * seconds))]).tobytes()


def quiet(seconds):
    return bytes(2 * int(22050 * seconds))


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

    def wait_for(self, done, seconds=3.):
        deadline = time.monotonic() + seconds
        while not done() and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertTrue(done())

    def test_the_build_plays_while_he_starts_and_the_finish_waits_until_he_is_ready(self):
        # Owner, November 21: his own startup sound. The please-wait line and the droid filler are retired.
        sink = RecordingSink(pace=.001)
        audio = StartupAudio(CountingTTS(), sink, self.dir)
        audio.start()
        self.wait_for(lambda: sink.played == [BUILD])               # The whole build, while he starts.
        time.sleep(.15)
        self.assertEqual(sink.played, [BUILD])                     # Still starting: quiet, the finish held back.
        began = time.monotonic()
        audio.land()                                               # He is ready.
        self.assertLess(time.monotonic() - began, .3)              # Returns as the finish begins to sound,
        self.assertEqual(len(sink.played), 2)
        self.assertTrue(audio.stop())                              # and stop waits for it to end.
        self.assertEqual(sink.played, [BUILD, FINISH])
        self.assertFalse(audio._cancel.event.is_set())             # Nothing was cut.

    def test_ready_before_the_build_is_over_ends_it_and_plays_the_finish(self):
        sink = RecordingSink(pace=.01)                             # The build takes over a second here.
        audio = StartupAudio(CountingTTS(), sink, self.dir)
        audio.start()
        self.wait_for(lambda: bool(sink.played) and len(sink.played[0]) > 4410)
        audio.land()
        self.assertTrue(audio.stop())
        self.assertEqual(len(sink.played), 2)
        self.assertTrue(BUILD.startswith(sink.played[0]) and len(sink.played[0]) < len(BUILD))   # No more was fed.
        self.assertEqual(sink.played[1], FINISH)                   # The finish still plays whole.

    def test_a_start_that_ends_without_being_ready_plays_no_finish(self):
        sink = RecordingSink(pace=.01)
        audio = StartupAudio(CountingTTS(), sink, self.dir)
        audio.start()
        self.wait_for(lambda: bool(sink.played) and len(sink.played[0]) > 4410)
        began = time.monotonic()
        self.assertTrue(audio.stop())                              # A failed start, or shutdown during it.
        self.assertLess(time.monotonic() - began, .5)
        self.assertEqual(len(sink.played), 1)
        self.assertLess(len(sink.played[0]), len(BUILD))
        audio.land()                                               # Too late to matter, and never a wait.
        self.assertEqual(len(sink.played), 1)

    def test_the_startup_sound_is_cut_at_its_pause(self):
        self.assertAlmostEqual(len(BUILD) / 44100, 11.8, delta=.3)          # The owner's clip: a build of about twelve
        self.assertAlmostEqual(len(FINISH) / 44100, 1.4, delta=.3)          # seconds, a pause, a short finish.
        build, finish = split_at_pause(quiet(.3) + tone(1.) + quiet(.6) + tone(.4) + quiet(.5))
        self.assertAlmostEqual(len(build) / 44100, 1.05, delta=.06)         # Its silent ends are dropped.
        self.assertAlmostEqual(len(finish) / 44100, .45, delta=.06)
        self.assertEqual(build[-2:], b'\x00\x00')                           # A cut end fades out; no click.
        # The longest pause is the cut; a shorter gap inside the build stays in it.
        build, finish = split_at_pause(tone(.5) + quiet(.45) + tone(.5) + quiet(.9) + tone(.3))
        self.assertAlmostEqual(len(build) / 44100, 1.5, delta=.06)
        self.assertAlmostEqual(len(finish) / 44100, .35, delta=.06)
        # No pause: all build. Nothing but silence: nothing to play.
        build, finish = split_at_pause(tone(1.) + quiet(.2) + tone(1.))
        self.assertAlmostEqual(len(build) / 44100, 2.25, delta=.06)
        self.assertEqual(finish, b'')
        self.assertEqual(split_at_pause(quiet(2.)), (b'', b''))

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

    def test_the_sound_waits_for_the_servo_wake(self):
        sink = RecordingSink()
        audio = StartupAudio(CountingTTS(), sink, self.dir, wake_motion=True)
        audio.start()
        time.sleep(.15)
        self.assertEqual(sink.played, [])       # Nothing plays before the servos move.
        audio.motion_started()
        self.wait_for(lambda: sink.played == [BUILD])
        audio.land()
        self.assertTrue(audio.stop())
        self.assertEqual(sink.played, [BUILD, FINISH])

    def test_power_down_clip_follows_the_startup_sound_while_the_servos_park(self):
        clip = read_clip('robot_power_down.wav')
        sink = RecordingSink(pace=.004)
        audio = StartupAudio(CountingTTS(), sink, self.dir)
        audio.start()
        self.wait_for(lambda: bool(sink.played) and len(sink.played[0]) > 4410)
        began = time.monotonic()
        audio.shutdown_started()                                  # The servo owner's park hook, mid-start.
        self.assertLess(time.monotonic() - began, 1.)             # The park never waits for the sound.
        self.assertTrue(audio._shutdown_thread.is_alive())        # It plays while the servos move.
        audio.close()                                             # After the park, before the sink closes.
        self.assertFalse(audio._shutdown_thread.is_alive())
        self.assertFalse(audio._cancel.event.is_set())            # The build was ended, not cut off.
        self.assertTrue(BUILD.startswith(sink.played[0]) and len(sink.played[0]) < len(BUILD))
        self.assertEqual(sink.played[1:], [clip])                 # No finishing sound; the whole power-down clip.
        self.assertEqual(sink.visualized[1], False)               # No speaking lights on a power-down sound.
        audio.shutdown_started()                                  # A second park request does not replay it.
        self.assertEqual(len(sink.played), 2)

    def test_the_startup_sound_uses_one_buffered_stream_closed_before_live_speech(self):
        # October 22: startup sound stuttered while models loaded; it is buffered by the driver.
        sink = RecordingSink(pace=.001)
        audio = StartupAudio(CountingTTS(), sink, self.dir)
        audio.start()
        self.wait_for(lambda: sink.played == [BUILD])
        audio.land()
        self.assertTrue(audio.stop())
        self.assertEqual(len(sink.streams), 1)
        self.assertEqual(sink.streams[0].clips, 2)              # The build and the finish.
        self.assertTrue(sink.streams[0].closed)                 # Closed before any live speech.
        audio.shutdown_started()
        audio.close()
        self.assertEqual(sink.streams[0].clips, 2)              # Power-down uses ordinary playback.
        self.assertEqual(len(sink.played), 3)

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
        self.assertEqual(len(list(ASSETS.glob('*.wav'))), 10)


if __name__ == '__main__':
    unittest.main()
