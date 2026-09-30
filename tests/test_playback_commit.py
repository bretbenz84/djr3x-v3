"""Device callback/start receipt races, with synthetic PCM and no device I/O."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from r3x.adapters.audio import AudioSink
from r3x.capture import Frame
from r3x.contracts import Cancellation, Cancelled, Playback
from r3x.proactive import OpportunityCancellation
from r3x.runtime import VoiceRuntime
from tests import test_voice_core


class PlaybackCommitTests(unittest.TestCase):
    def playback(self, cancel, during_callback, visualize=True):
        starts, streams = [], []
        class Stop(Exception): pass
        class Abort(Exception): pass
        class Stream:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.closed = False
                streams.append(self)
            def start(self):
                # Small one-chunk input: EOF already set; callback submits PCM
                # before AudioSink can announce it on its producer thread.
                try:
                    self.kwargs['callback'](bytearray(2048), 1024,
                        SimpleNamespace(outputBufferDacTime=10., currentTime=10.),
                        SimpleNamespace(output_underflow=False))
                except Stop:
                    pass
                self.kwargs['finished_callback']()
                self.assert_before_announce()
                during_callback()
            def assert_before_announce(self):
                if starts: raise AssertionError('Receipt already announced')
            def abort(self): pass
            def close(self): self.closed = True
        sink = AudioSink(clock=lambda:100.)
        sink.sd = SimpleNamespace(RawOutputStream=Stream, CallbackStop=Stop, CallbackAbort=Abort)
        try:
            result = sink.play(iter([bytes(1024)]), cancel, starts.append, visualize=visualize)
            self.assertEqual(result.frames, 512)
        finally:
            self.assertEqual(starts, [100.])
            self.assertTrue(streams[0].closed)
        return result

    def test_callback_commits_before_delayed_notification_for_both_guards(self):
        for proactive in (False, True):
            valid = [True]
            token = OpportunityCancellation(lambda:valid[0]) if proactive else Cancellation(guard=lambda:valid[0])
            def changed():
                valid[0] = False
                token.check()
            self.playback(token, changed)
            self.assertTrue(token.is_committed())
            self.assertFalse(token.event.is_set())

    def test_explicit_stop_still_cancels_and_retains_start_receipt(self):
        token = Cancellation()
        def stop():
            token.event.set()
            token.check()
        with self.assertRaises(Cancelled):
            self.playback(token, stop)

    def test_effect_pcm_does_not_commit_a_future_spoken_reply(self):
        token = Cancellation()
        self.playback(token, lambda:self.assertFalse(token.is_committed()), visualize=False)
        self.assertFalse(token.is_committed())


class SessionCommitTests(unittest.TestCase):
    setup_core = test_voice_core.CoreTests.setup_core
    turn = test_voice_core.CoreTests.turn

    def test_speech_activity_between_pcm_and_receipt_does_not_cut_reply(self):
        outer = self
        class Sink:
            def play(self, chunks, cancel, on_start):
                list(chunks)
                cancel.playback_started.set()
                outer.session.note_speech(101.)
                cancel.check()
                on_start(100.)
                return Playback(512, 100., 101., 0)
        self.setup_core(sink=Sink())
        result = self.turn()
        self.assertEqual(result.status, 'completed')
        self.assertEqual(len(result.completed_text), 2)
        self.assertFalse(any(r['event']=='reply_yielded' for r in self.trace.rows))

    def test_runtime_bounds_future_audio_liveness_without_changing_capture_times(self):
        for end, expected in ((100.037,100.), (98.,98.)):
            self.setup_core()
            frame = Frame(bytes(1024), end-.032, end)
            frames = [frame]
            seen = []
            class Mic:
                dropped = status_errors = 0
                def read(self):
                    if frames: return frames.pop()
                    runtime.stop.set()
                def cancel(self): pass
            class VAD:
                def reset(self): pass
                def is_speech(self, pcm): return False
            runtime = VoiceRuntime(Mic(), VAD(), None, self.session, self.trace)
            runtime.endpoint.push = lambda original, speech: seen.append(original)
            with patch('sys.stdin.isatty', return_value=False):
                runtime.run()
            self.assertEqual(runtime.audio_seen_at, expected)
            self.assertEqual(seen, [frame])
            self.assertEqual(frame.ended_at, end)
