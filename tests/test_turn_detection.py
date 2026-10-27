"""Smart Turn next to Silero (owner request, October 26): a finished-sounding pause ends speech early."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from r3x.adapters.turn import SAMPLES, SmartTurn, model_input
from r3x.capture import Endpoint, Frame
from r3x.contracts import ProviderFailure
from r3x.dataset import segment
from r3x.runtime import VoiceRuntime
from r3x.settings import Settings


FRAME = .032


def native_available():
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


class Judge:
    """Stands in for Smart Turn: a fixed answer, and what it was given."""
    def __init__(self, *answers):
        self.answers, self.heard = list(answers), []
    def __call__(self, pcm):
        self.heard.append(pcm)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def listen(endpoint, speech, echo=False):
    """Push one frame per entry of `speech`; return the captures and the frame each ended on."""
    ended = []
    for i, voiced in enumerate(speech):
        capture = endpoint.push(Frame(bytes([i % 256, 0]) * 512, i*FRAME, (i+1)*FRAME, echo), voiced)
        if capture:
            ended.append((i, capture))
    return ended


class EndpointTurnTests(unittest.TestCase):
    def test_finished_sounding_speech_ends_at_a_fifth_of_a_second(self):
        judge = Judge(.9)
        ended = listen(Endpoint('s', .5, judge), [True]*10 + [False]*30)
        self.assertEqual(len(ended), 1)
        frame, capture = ended[0]
        self.assertEqual(capture.ended_by, 'finished')
        self.assertAlmostEqual(capture.ended_at - capture.speech_ended_at, 7*FRAME)   # First frame past .2 s.
        self.assertEqual(len(judge.heard), 1)
        self.assertEqual(judge.heard[0], capture.pcm)   # The whole capture so far, not just the last word.

    def test_unfinished_pause_keeps_listening_and_the_whole_turn_is_judged_again(self):
        judge = Judge(.1, .1)
        # "So, ... what was the command after that?": a 0.38 s pause, then the rest.
        ended = listen(Endpoint('s', .5, judge), [True]*10 + [False]*12 + [True]*20 + [False]*30)
        self.assertEqual(len(ended), 1)
        capture = ended[0][1]
        self.assertEqual(capture.ended_by, 'silence')
        self.assertEqual(len(capture.voiced_intervals), 2)
        self.assertGreaterEqual(capture.ended_at - capture.speech_ended_at, .5)
        self.assertEqual(len(judge.heard), 2)   # Once per pause, never every frame.
        self.assertGreater(len(judge.heard[1]), len(judge.heard[0]))
        self.assertTrue(judge.heard[1].startswith(judge.heard[0]))

    def test_a_longer_wait_holds_an_unfinished_thinking_pause_together(self):
        speech = [True]*10 + [False]*24 + [True]*10 + [False]*40   # A 0.77 s mid-thought pause.
        self.assertEqual(len(listen(Endpoint('s', .5), speech)), 2)
        ended = listen(Endpoint('s', 1., Judge(.1)), speech)
        self.assertEqual(len(ended), 1)
        self.assertEqual(len(ended[0][1].voiced_intervals), 2)

    def test_no_judgement_falls_back_to_the_full_silence(self):
        ended = listen(Endpoint('s', .5, Judge(None)), [True]*10 + [False]*30)
        self.assertEqual([c.ended_by for _, c in ended], ['silence'])
        self.assertGreaterEqual(ended[0][1].ended_at - ended[0][1].speech_ended_at, .5)

    def test_his_own_voice_and_a_game_answer_are_never_judged(self):
        judge = Judge(.9)
        ended = listen(Endpoint('s', .5, judge), [True]*10 + [False]*30, echo=True)
        self.assertEqual((judge.heard, ended[0][1].ended_by), ([], 'silence'))
        endpoint = Endpoint('s', 1.1, judge)
        endpoint.early = False   # "What is... Paris?"
        ended = listen(endpoint, [True]*10 + [False]*12 + [True]*10 + [False]*40)
        self.assertEqual((judge.heard, len(ended)), ([], 1))

    def test_without_a_judge_endpointing_is_unchanged(self):
        ended = listen(Endpoint('s', .5), [True]*10 + [False]*30)
        self.assertEqual(ended[0][1].ended_by, 'silence')
        self.assertAlmostEqual(ended[0][1].ended_at - ended[0][1].speech_ended_at, 16*FRAME)

    def test_saved_audio_replay_uses_the_same_judge(self):
        class VAD:
            def reset(self): pass
            def is_speech(self, block): return any(block)
        pcm = b'\x00\x10' * 16000 + bytes(32000)   # A second of "speech", then a second of silence.
        captures = list(segment(pcm, 's', VAD(), .5, turn=Judge(.95)))
        self.assertEqual([c.ended_by for c in captures], ['finished'])
        self.assertEqual([c.ended_by for c in segment(pcm, 's', VAD(), .5)], ['silence'])


class RuntimeTurnTests(unittest.TestCase):
    class Trace:
        def __init__(self): self.rows = []
        def emit(self, event, **data): self.rows.append({'event': event, **data})

    def test_while_the_wheels_run_only_silence_ends_speech(self):
        import threading
        moving = threading.Event()
        moving.set()
        runtime = SimpleNamespace(trace=self.Trace(), drive=SimpleNamespace(moving=moving),
                                  turn=SimpleNamespace(finished=lambda pcm: .99))
        # The stop check that looks at a short burst after 0.2 s keeps that moment to itself.
        self.assertIsNone(VoiceRuntime.turn_check(runtime, bytes(64000)))
        moving.clear()
        self.assertEqual(VoiceRuntime.turn_check(runtime, bytes(64000)), .99)

    def test_each_check_is_traced_and_a_failure_degrades_to_silence(self):
        trace = self.Trace()
        runtime = SimpleNamespace(trace=trace, drive=None, turn=SimpleNamespace(finished=lambda pcm: .83))
        self.assertEqual(VoiceRuntime.turn_check(runtime, bytes(64000)), .83)
        self.assertEqual((trace.rows[0]['event'], trace.rows[0]['probability'], trace.rows[0]['audio_seconds']),
                         ('turn_check', .83, 2.))
        def broken(pcm): raise ProviderFailure('InvalidTurnProbability')
        runtime.turn = SimpleNamespace(finished=broken)
        self.assertIsNone(VoiceRuntime.turn_check(runtime, bytes(64000)))
        self.assertEqual(trace.rows[-1], {'event': 'provider_failed', 'capability': 'turn_detection',
                                          'error_type': 'ProviderFailure'})


class AdapterTests(unittest.TestCase):
    @unittest.skipUnless(native_available(), 'numpy unavailable (blocked under the contract runner)')
    def test_the_model_hears_the_last_eight_seconds_with_silence_in_front(self):
        short = model_input(b'\x00\x40' * 16000)   # One second at half scale.
        self.assertEqual(len(short), SAMPLES)
        self.assertEqual((float(abs(short[:SAMPLES-16000]).max()), float(short[-1])), (0., .5))
        long = model_input(b'\x00\x40' * (2*SAMPLES) + b'\x00\xc0' * SAMPLES)
        self.assertEqual((len(long), float(long.min()), float(long.max())), (SAMPLES, -.5, -.5))

    def test_unverified_model_or_unstarted_judge_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / 'smart-turn.onnx'
            path.write_bytes(b'not the pinned model')
            with self.assertRaisesRegex(ProviderFailure, 'TurnModelDigestMismatch'):
                SmartTurn(path).start()
            with self.assertRaisesRegex(ProviderFailure, 'TurnModelMissingOrLinked'):
                SmartTurn(path.with_name('absent.onnx')).start()
        with self.assertRaisesRegex(ProviderFailure, 'TurnDetectionNotStarted'):
            SmartTurn(path).finished(bytes(3200))

    def test_the_setting_is_an_explicit_choice(self):
        self.assertFalse(Settings().smart_turn_enabled)
        with self.assertRaisesRegex(ValueError, 'smart_turn_enabled'):
            Settings(smart_turn_enabled='yes')


if __name__ == '__main__':
    unittest.main()
