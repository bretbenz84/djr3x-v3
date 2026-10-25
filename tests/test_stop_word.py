"""The owner's stop word, listened for in the sound while the wheels run (October 25).

Synthetic frames and a scripted spotter: these prove what a detection does, not how well
stop.onnx hears a real "stop" over the motors (tools/check_stop_hearing.py measures that)."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from r3x.adapters.stop_word import StopSpotter
from r3x.capture import Frame
from r3x.contracts import ProviderFailure, Transcript
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from tests.test_voice_core import Brain, Trace


class Spotter:
    """Fires on the frame numbers given; records what it was fed."""
    def __init__(self, fire_at=(), fail=False):
        self.fire_at, self.fail, self.fed, self.resets = set(fire_at), fail, 0, 0

    def feed(self, pcm):
        self.fed += 1
        if self.fail:
            raise ProviderFailure('Broken')
        return .93 if self.fed in self.fire_at else None

    def reset(self):
        self.resets += 1


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
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), session, trace, drive=drive, stop_word=spotter)
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        return trace.rows, halted

    def test_a_stop_word_the_speech_detector_missed_halts_the_wheels(self):
        rows, halted = self.run_frames(Spotter(fire_at={12}))
        self.assertTrue(halted)
        heard = [r for r in rows if r['event'] == 'stop_word_heard']
        self.assertEqual([(r['status'], r['score']) for r in heard], [('halted', .93)])
        self.assertTrue(any(r['event'] == 'input_invalidated' for r in rows))   # The conversation's stop too.

    def test_standing_still_nothing_is_fed_and_nothing_halts(self):
        spotter = Spotter(fire_at={1, 2, 3})
        rows, halted = self.run_frames(spotter, moving=False)
        self.assertEqual((spotter.fed, halted), (0, []))
        self.assertFalse(any(r['event'] == 'stop_word_heard' for r in rows))

    def test_it_starts_fresh_after_the_wheels_stop_and_logs_how_close_it_came(self):
        spotter = Spotter()
        spotter.last_score = .41
        rows, halted = self.run_frames(spotter, stops_moving_after=10)
        self.assertEqual((spotter.fed, spotter.resets), (10, 1))
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
        self.assertEqual([r['error_type'] for r in rows if r['event'] == 'stop_word_failed'], ['ProviderFailure'])
        self.assertFalse(halted)


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
        for manifest, reason in ((None, 'StopWordManifestUnreadable'),
                                 (good[1:], 'StopWordModelMissing'),
                                 ([{**good[0], 'sha256': '0' * 64}, *good[1:]], 'StopWordModelDigestMismatch')):
            with self.assertRaises(ProviderFailure) as raised:
                StopSpotter(self.folder(files, manifest)).start()
            self.assertEqual(str(raised.exception), reason)

    def test_it_must_be_started_before_it_listens(self):
        with self.assertRaises(ProviderFailure):
            StopSpotter(Path('/nonexistent')).feed(bytes(2560))


if __name__ == '__main__':
    unittest.main()
