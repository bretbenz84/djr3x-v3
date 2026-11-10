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


class Model:
    """A reply model or helper that answers, or is out of credit, as told; it counts its calls."""
    def __init__(self, name, answer=None, fails=None, after_words=False):
        from r3x.providers import Capability
        self.capability = Capability('brain', name, 'test', name, (), ())
        self.answer, self.fails, self.after_words, self.calls, self.started = answer, fails, after_words, 0, False
    def start(self): self.started = True
    def cancel(self): pass
    def close(self): self.started = False
    def reply(self, snapshot, cancel):
        self.calls += 1
        if self.after_words: yield 'Well,'
        if self.fails: raise ProviderFailure(self.fails)
        yield self.answer
    def plan(self, facts, cancel, images=()):
        self.calls += 1
        if self.fails: raise ProviderFailure(self.fails)
        return {'plan': self.answer}


def pair(claude, openai):
    from r3x.adapters.claude import CreditFallback, ModelSwitch
    from tests.test_claude_replies import Rows
    switch = ModelSwitch()
    made = CreditFallback(claude, openai, switch)
    made.trace = Rows()
    return made, switch


class SwitchToOpenAITests(unittest.TestCase):
    """Owner, November 10: Claude's monthly credit first, then the prepaid OpenAI balance."""

    def test_out_of_claude_credit_the_same_reply_comes_from_openai_and_later_ones_skip_claude(self):
        claude, openai = Model('claude', fails='ClaudeCreditExhausted'), Model('openai', 'Still here.')
        made, switch = pair(claude, openai)
        made.start()
        self.assertTrue(claude.started and openai.started)
        snapshot = Snapshot('s', 't', 0, (), context={})
        self.assertEqual(list(made.reply(snapshot, Cancellation())), ['Still here.'])
        self.assertTrue(switch.on_openai and switch.notice_owed)
        self.assertEqual(made.capability.name, 'brain')
        self.assertEqual(list(made.reply(snapshot, Cancellation())), ['Still here.'])
        self.assertEqual((claude.calls, openai.calls), (1, 2))
        self.assertEqual([r['event'] for r in made.trace.rows], ['model_provider_switched'])

    def test_other_failures_are_not_a_reason_to_switch(self):
        claude, openai = Model('claude', fails='ClaudeHTTP500'), Model('openai', 'Hi.')
        made, switch = pair(claude, openai)
        with self.assertRaisesRegex(ProviderFailure, 'ClaudeHTTP500'):
            list(made.reply(Snapshot('s', 't', 0, (), context={}), Cancellation()))
        self.assertEqual((switch.on_openai, openai.calls), (False, 0))

    def test_a_reply_cut_off_after_words_were_heard_is_not_said_again(self):
        claude, openai = Model('claude', fails='ClaudeCreditExhausted', after_words=True), Model('openai', 'Hi.')
        made, switch = pair(claude, openai)
        said = []
        with self.assertRaisesRegex(ProviderFailure, 'ClaudeCreditSwitchedToOpenAI'):
            for words in made.reply(Snapshot('s', 't', 0, (), context={}), Cancellation()):
                said.append(words)
        self.assertEqual((said, openai.calls, switch.on_openai), (['Well,'], 0, True))

    def test_a_helper_that_finds_the_credit_gone_moves_every_model_over(self):
        from r3x.adapters.claude import CreditFallback
        planner, backup = Model('claude', fails='ClaudeCreditExhausted'), Model('openai', 'walk')
        made, switch = pair(planner, backup)
        brain = CreditFallback(Model('claude', 'Hi.'), Model('openai', 'Hello.'), switch)
        self.assertEqual(made.plan({}, Cancellation()), {'plan': 'walk'})
        self.assertEqual(list(brain.reply(Snapshot('s', 't', 0, (), context={}), Cancellation())), ['Hello.'])

    def test_without_an_openai_key_there_is_nothing_to_switch_to(self):
        from r3x.adapters.claude import ClaudeBrain, CreditFallback, ModelSwitch, reply_brain
        from r3x.settings import Secrets, Settings
        on_claude = Settings(llm_provider='claude')
        self.assertIsInstance(reply_brain(on_claude, Secrets(anthropic_api_key='a'), ModelSwitch()), ClaudeBrain)
        self.assertIsInstance(reply_brain(on_claude, Secrets(anthropic_api_key='a', openai_api_key='o'), ModelSwitch()),
                              CreditFallback)


class SwitchNoticeTests(MemoryFixture):
    turn = test_attention.AttentionTests.turn

    def test_he_says_once_that_he_switched_and_answers_the_turn_meanwhile(self):
        from r3x.adapters.claude import ModelSwitch
        saved = []
        def canned(text, cancel):
            saved.append(text)
            return b'\x01\x00' * 400
        made, switch = pair(Model('claude', fails='ClaudeCreditExhausted'), Model('openai', 'Still here.'))
        made.trace = self.state.trace
        session = Session('session', made, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        session.canned, session.model_switch = canned, switch
        self.addCleanup(session.close)
        self.session = session
        self.turn('Rex, are you there?')
        spoken = [r['text'] for r in self.state.trace.rows if r['event'] == 'phrase_completed']
        self.assertEqual(spoken, ['Still here.', CREDIT_LINES['ClaudeCreditSwitchedToOpenAI']])
        self.assertEqual(saved, [CREDIT_LINES['ClaudeCreditSwitchedToOpenAI']])   # The saved line, not new speech.
        self.state.trace.at += 2 * CREDIT_NOTICE_SECONDS
        self.turn('And now?')
        notices = [r['reason'] for r in self.state.trace.rows if r['event'] == 'credit_notice']
        self.assertEqual(notices, ['ClaudeCreditSwitchedToOpenAI'])                 # Said once a run.
        self.assertIn('model_provider_switched', [r['event'] for r in self.state.trace.rows])


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
