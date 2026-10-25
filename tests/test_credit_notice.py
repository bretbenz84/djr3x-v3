"""Out of credit (owner, October 25): when OpenAI or ElevenLabs has no credit left, Rex says so in a saved
line instead of going silent, at most every ten minutes and only when someone speaks to him. Synthetic
providers and fake HTTP responses only: the error shapes are the ones the October 25 trace and the
providers' documentation show, not a live outage."""
import unittest

from r3x.adapters.hosted import ElevenLabsTTS, OpenAIBrain
from r3x.contracts import Cancellation, ProviderFailure, Snapshot
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import CREDIT_LINES, CREDIT_NOTICE_SECONDS, Session
from r3x.startup_audio import StartupAudio
from tests import test_attention, test_proactive_core
from tests.test_adapters import Client, Response
from tests.test_memory_core import MemoryFixture


class Refused(Response):
    def __init__(self, status, body):
        super().__init__()
        self.status_code, self.body = status, body

    def read(self):
        return self.body


class Broke:
    """A reply model whose account is out of credit."""
    calls = 0

    def reply(self, snapshot, cancel):
        Broke.calls += 1
        raise ProviderFailure('OpenAICreditExhausted')
        yield


class DetectionTests(unittest.TestCase):
    def reply(self, response):
        brain = OpenAIBrain('fixture')
        brain.client = Client(response)
        with self.assertRaises(ProviderFailure) as raised:
            list(brain.reply(Snapshot('s', 't', 0, (), context={}), Cancellation()))
        return str(raised.exception), len(brain.client.calls)

    def test_the_october_25_stream_error_and_a_refused_request_both_mean_out_of_credit_and_are_not_retried(self):
        stream = Response([{'type': 'error', 'error': {'code': 'credit_balance_exhausted'}}])
        self.assertEqual(self.reply(stream), ('OpenAICreditExhausted', 1))
        refused = Refused(429, b'{"error": {"code": "insufficient_quota", "type": "insufficient_quota"}}')
        self.assertEqual(self.reply(refused), ('OpenAICreditExhausted', 1))   # A 429 otherwise waits and retries.
        other = Response([{'type': 'error', 'error': {'code': 'server_error'}}])
        self.assertEqual(self.reply(other), ('ModelIncomplete', 1))

    def test_elevenlabs_out_of_characters(self):
        tts = ElevenLabsTTS('key', 'rex')
        tts.client = Client(Refused(401, b'{"detail": {"status": "quota_exceeded", "message": "out"}}'))
        with self.assertRaisesRegex(ProviderFailure, 'ElevenLabsCreditExhausted'):
            list(tts.synthesize('Hello.', Cancellation()))
        tts.client = Client(Refused(401, b'{"detail": {"status": "invalid_api_key"}}'))
        with self.assertRaisesRegex(ProviderFailure, 'ElevenLabsHTTP401'):
            list(tts.synthesize('Hello.', Cancellation()))


class NoticeTests(MemoryFixture):
    turn = test_attention.AttentionTests.turn

    def session(self, tts=None):
        self.saved = []
        def canned(text, cancel):
            self.saved.append(text)
            return b'\x01\x00' * 400
        self.session = Session('session', Broke(), OutputOwner(tts or SyntheticTTS(), SyntheticSink(), self.state.trace),
                               self.state.trace, self.state)
        self.session.canned = canned
        self.addCleanup(self.session.close)

    def notices(self):
        return [r for r in self.state.trace.rows if r['event'] == 'credit_notice']

    def test_he_says_so_once_then_again_ten_minutes_later(self):
        self.session()
        failed = self.turn('Rex, are you there?')
        self.assertEqual((failed.status, failed.error), ('failed', 'OpenAICreditExhausted'))
        self.assertEqual(self.saved, [CREDIT_LINES['OpenAICreditExhausted']])   # The saved line, no new speech.
        self.assertEqual([n['heard'] for n in self.notices()], [True])
        spoken = [r['text'] for r in self.state.trace.rows if r['event'] == 'phrase_completed']
        self.assertEqual(spoken, [CREDIT_LINES['OpenAICreditExhausted']])
        self.state.trace.at += 60
        self.turn('Hello?')
        self.assertEqual(len(self.notices()), 1)                                  # Not on every turn.
        self.state.trace.at += CREDIT_NOTICE_SECONDS
        self.turn('Still broken?')
        self.assertEqual(len(self.notices()), 2)

    def test_when_his_voice_service_is_out_the_saved_line_still_plays(self):
        class Silent(SyntheticTTS):
            def synthesize(self, text, cancel, mode=None):
                raise ProviderFailure('ElevenLabsCreditExhausted')
                yield
        class Answers:
            def reply(self, snapshot, cancel):
                yield 'Here is a thought.'
        self.session(Silent())
        self.session.brain = Answers()
        failed = self.turn('Rex, say something.')
        self.assertEqual(failed.error, 'ElevenLabsCreditExhausted')
        self.assertEqual([(n['reason'], n['heard']) for n in self.notices()], [('ElevenLabsCreditExhausted', True)])


class OwnTurnTests(MemoryFixture):
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    turn = test_proactive_core.InitiativeCoreTests.turn
    visible = test_proactive_core.InitiativeCoreTests.visible

    def test_his_own_turns_never_announce_it(self):
        self.setup_core(Broke(), OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace))
        self.session._credit_said['OpenAICreditExhausted'] = float('-inf')
        calls = Broke.calls
        self.visible(108); self.runtime.proactive_tick()
        self.assertGreater(Broke.calls, calls)                                   # A lull was tried...
        self.assertFalse([r for r in self.state.trace.rows if r['event'] == 'credit_notice'])   # ...and kept quiet.


class SavedLineTests(unittest.TestCase):
    def test_saved_lines_are_made_in_his_own_voice_whatever_mode_is_on(self):
        import tempfile
        from pathlib import Path
        tts = ElevenLabsTTS('key', 'rex-voice', {'pride': 'pride-voice'})
        tts.modes = lambda: ('pride',)
        tts.client = Client(Response(chunks=[b'\x00\x01' * 64]))
        with tempfile.TemporaryDirectory() as folder:
            audio = StartupAudio(tts, SyntheticSink(), Path(folder).resolve())
            audio.speech(CREDIT_LINES['OpenAICreditExhausted'], Cancellation())
        url, payload = tts.client.calls[0][0][1], tts.client.calls[0][1]['json']
        self.assertIn('/rex-voice/', url)
        self.assertEqual(payload['model_id'], tts.capability.model)


if __name__ == '__main__':
    unittest.main()
