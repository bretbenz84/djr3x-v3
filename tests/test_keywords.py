"""The owner's keywords heard in the sound (October 25): "stop" while the wheels run, "Hey Rex"
over his voice. Synthetic frames and a scripted spotter: these prove what a detection does, not
how well the models hear the owner (tools/check_stop_hearing.py measures stop)."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from r3x.adapters.keywords import KeywordSpotter
from r3x.capture import Frame
from r3x.contracts import ProviderFailure, Transcript
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from tests.test_voice_core import Brain, Trace


class Spotter:
    """Fires `word` on the frame numbers given; records what it was fed."""
    def __init__(self, fire_at=(), fail=False, word='stop'):
        self.fire_at, self.fail, self.word, self.fed = set(fire_at), fail, word, 0
        self.last_scores = {}

    def feed(self, pcm):
        self.fed += 1
        if self.fail:
            raise ProviderFailure('Broken')
        return {self.word: .93} if self.fed in self.fire_at else {}


class StopWordRuntimeTests(unittest.TestCase):
    def run_frames(self, spotter, moving=True, own=None, stops_moving_after=None, count=40):
        trace = Trace()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
        session = Session('s', Brain(), output, trace)
        self.addCleanup(session.close)
        if own:
            output.lines.append([200., None, own])
        frames = [Frame(bytes(1024), 200 + i * .032, 200 + (i + 1) * .032) for i in range(count)]
        halted = []
        class Drive:
            def __init__(inner): inner.moving = threading.Event()
            def cancel(inner): halted.append(True); inner.moving.clear()
        drive = Drive()
        if moving:
            drive.moving.set()
        class Mic:
            dropped = status_errors = 0
            def read(inner):
                if not frames:
                    time.sleep(.2); runtime.stop.set(); return None
                if stops_moving_after is not None and count - len(frames) == stops_moving_after:
                    drive.moving.clear()
                return frames.pop(0)
            def cancel(inner): pass
        class VAD:   # Motor noise the speech detector does not take for speech.
            last_probability = .1
            def reset(inner): pass
            def is_speech(inner, pcm): return False
        class ASR:
            def transcribe(inner, capture, cancel): return Transcript('Oh.', -.9, False)
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), session, trace, drive=drive, keywords=spotter)
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        return trace.rows, halted

    def test_a_stop_word_the_speech_detector_missed_halts_the_wheels(self):
        rows, halted = self.run_frames(Spotter(fire_at={12}))
        self.assertTrue(halted)
        heard = [r for r in rows if r['event'] == 'stop_word_heard']
        self.assertEqual([(r['status'], r['score']) for r in heard], [('halted', .93)])
        self.assertTrue(any(r['event'] == 'input_invalidated' for r in rows))   # The conversation's stop too.

    def test_standing_still_a_stop_word_halts_nothing(self):
        rows, halted = self.run_frames(Spotter(fire_at={1, 2, 3}), moving=False)
        self.assertEqual(halted, [])
        self.assertFalse(any(r['event'] == 'stop_word_heard' for r in rows))

    def test_each_drive_logs_how_close_a_stop_came(self):
        spotter = Spotter()
        spotter.last_scores = {'stop': .41}
        rows, halted = self.run_frames(spotter, stops_moving_after=10)
        listened, = [r for r in rows if r['event'] == 'stop_word_listened']
        self.assertEqual((listened['peak_score'], listened['seconds']), (.41, .35))

    def test_his_own_line_saying_stop_does_not_stop_him(self):
        rows, halted = self.run_frames(Spotter(fire_at={12}), own="I'll stop right at the door.")
        self.assertFalse(halted)
        self.assertEqual([r['status'] for r in rows if r['event'] == 'stop_word_heard'], ['his_own_line'])

    def test_a_broken_spotter_is_reported_once_and_listening_goes_on(self):
        spotter = Spotter(fail=True)
        rows, halted = self.run_frames(spotter)
        self.assertEqual(spotter.fed, 1)
        self.assertEqual([r['error_type'] for r in rows if r['event'] == 'keywords_failed'], ['ProviderFailure'])
        self.assertFalse(halted)


class WakeWordTests(unittest.TestCase):
    """Owner, October 25: "Hey Rex" or "Hey DJ Rex" said over him cuts his line so he can listen."""
    def speak_and_wake(self, moving=False, line='The thing about hyperdrives is that nobody reads the manual.'):
        from r3x.contracts import Capture, Transcript as T
        trace = Trace()
        played, cut = threading.Event(), threading.Event()
        class Sink:   # Plays until the reply's own cancellation ends it, as the device callback does.
            def play(inner, chunks, cancel, on_start, visualize=True):
                for _ in chunks: pass
                on_start(trace.clock()); played.set()
                if not cancel.event.wait(5): raise AssertionError('never cut')
                cut.set()
                from r3x.contracts import Cancelled
                raise Cancelled()
            def cancel(inner): pass
        output = OutputOwner(SyntheticTTS(), Sink(), trace)
        brain = Brain(line)
        session = Session('s', brain, output, trace)
        self.addCleanup(session.close)
        halted = []
        class Drive:
            def __init__(inner): inner.moving = threading.Event()
            def cancel(inner): halted.append(True)
        drive = Drive()
        if moving:
            drive.moving.set()
        reply = threading.Thread(target=lambda: session.process_turn(
            Capture('c', 's', bytes(9600), 99., 99.5, 99.6), T('Tell me about hyperdrives.', -.01, True), 0))
        reply.start()
        self.assertTrue(played.wait(5))
        frames = [Frame(bytes(1024), 200 + i * .032, 200 + (i + 1) * .032, echo_risk=True) for i in range(30)]
        class Mic:
            dropped = status_errors = 0
            def read(inner):
                if not frames:
                    time.sleep(.2); runtime.stop.set(); return None
                return frames.pop(0)
            def cancel(inner): pass
        class VAD:
            last_probability = 0.
            def reset(inner): pass
            def is_speech(inner, pcm): return False
        class ASR:
            def transcribe(inner, capture, cancel): return T('', None, False)
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), session, trace, drive=drive,
                               keywords=Spotter(fire_at={5}, word='Hey_rex'))
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        reply.join(5)
        rows = trace.rows
        closing = next(i for i, r in enumerate(rows) if r['event'] == 'cancel')   # The runtime's own close.
        cut_first = any(r['event'] == 'delivery' and r['status'] == 'interrupted' for r in rows[:closing])
        return rows, cut_first, halted, session

    def test_hey_rex_over_his_line_cuts_it_and_he_listens(self):
        rows, cut, halted, session = self.speak_and_wake()
        self.assertTrue(cut)
        self.assertEqual([(r['word'], r['status']) for r in rows if r['event'] == 'wake_word_heard'],
                         [('Hey_rex', 'interrupted')])
        self.assertEqual(halted, [])
        self.assertEqual(halted, [])                                  # The wheels are left alone.
        self.assertTrue(any(m.get('content', '').startswith('They said "Hey Rex" over you')
                            for m in session.history.messages()))

    def test_while_the_wheels_run_it_waits(self):
        rows, cut, halted, session = self.speak_and_wake(moving=True)
        self.assertFalse(cut)
        self.assertEqual([r['status'] for r in rows if r['event'] == 'wake_word_heard'], ['driving'])

    def test_his_own_line_saying_rex_does_not_cut_it(self):
        rows, cut, halted, session = self.speak_and_wake(line='Hey, Rex here, reporting for duty as always.')
        self.assertEqual([r['status'] for r in rows if r['event'] == 'wake_word_heard'], ['his_own_line'])
        self.assertFalse(cut)


class QuietStopWhileDrivingTests(unittest.TestCase):
    """A short burst while the wheels run, scored by a scripted detector and recognizer."""
    def burst(self, probability, transcript, moving=True):
        trace = Trace()
        session = Session('s', Brain(), OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace)
        self.addCleanup(session.close)
        scores = [probability] * 10 + [.05] * 30          # About 0.3 s of sound, then quiet.
        frames = [Frame(bytes(1024), 200 + i * .032, 200 + (i + 1) * .032) for i in range(len(scores))]
        heard, halted = [], []
        class Drive:
            def __init__(inner): inner.moving = threading.Event()
            def cancel(inner): halted.append(True); inner.moving.clear()
        drive = Drive()
        if moving:
            drive.moving.set()
        class Mic:
            dropped = status_errors = 0
            def read(inner):
                if not frames:
                    time.sleep(.3); runtime.stop.set(); return None
                return frames.pop(0)
            def cancel(inner): pass
        class VAD:
            last_probability = None
            def reset(inner): pass
            def is_speech(inner, pcm):
                inner.last_probability = scores.pop(0)
                return inner.last_probability >= .5
        class ASR:
            def transcribe(inner, capture, cancel):
                heard.append(capture); return transcript
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), session, trace, drive=drive)
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        return [r for r in trace.rows if r['event'] == 'stop_peek'], heard, halted

    def test_a_faint_stop_while_driving_is_still_checked_and_halts(self):
        peeks, heard, halted = self.burst(.35, Transcript('Stop.', -.2, True))
        self.assertEqual([p['status'] for p in peeks], ['halted'])
        self.assertTrue(halted)

    def test_standing_still_the_usual_speech_level_applies(self):
        peeks, heard, halted = self.burst(.35, Transcript('Stop.', -.2, True), moving=False)
        self.assertEqual((peeks, heard, halted), ([], [], []))

    def test_an_unconfident_whoa_out_of_motor_noise_does_not_halt(self):
        peeks, heard, halted = self.burst(.6, Transcript('Whoa!', -.5, False))
        self.assertEqual([p['status'] for p in peeks], ['unsure_whoa'])
        self.assertFalse(halted)

    def test_an_unconfident_stop_or_a_clear_whoa_still_halts(self):
        for transcript in (Transcript('Stop.', -.6, False), Transcript('Whoa!', -.1, True)):
            peeks, heard, halted = self.burst(.6, transcript)
            self.assertEqual([p['status'] for p in peeks], ['halted'], transcript)
            self.assertTrue(halted)


class StopSpotterStartTests(unittest.TestCase):
    def folder(self, files, manifest):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        for name, data in files.items():
            (root / name).write_bytes(data)
        if manifest is not None:
            (root / 'manifest.json').write_text(json.dumps(manifest))
        return root

    def test_models_must_match_the_manifest_before_anything_loads(self):
        files = {name: name.encode() for name in ('stop.onnx', 'melspectrogram.onnx', 'embedding_model.onnx')}
        good = [{'name': n, 'sha256': hashlib.sha256(d).hexdigest()} for n, d in files.items()]
        for manifest, reason in ((None, 'KeywordManifestUnreadable'),
                                 (good[1:], 'KeywordModelMissing'),
                                 ([{**good[0], 'sha256': '0' * 64}, *good[1:]], 'KeywordModelDigestMismatch')):
            with self.assertRaises(ProviderFailure) as raised:
                KeywordSpotter(self.folder(files, manifest)).start()
            self.assertEqual(str(raised.exception), reason)

    def test_it_must_be_started_before_it_listens(self):
        with self.assertRaises(ProviderFailure):
            KeywordSpotter(Path('/nonexistent')).feed(bytes(2560))


if __name__ == '__main__':
    unittest.main()
