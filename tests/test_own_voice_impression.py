""""Impersonate me" through ElevenLabs (owner, November 19): a repeat-after-me recording of about
30 voiced seconds, joined into one upload, the clone kept for the run and deleted at shutdown.
Blocked I/O: a fake clone owner stands in for ElevenLabs in the session tests, and a fake HTTP
client for the provider tests. Synthetic audio only; nothing here shows how a real clone sounds."""
import io
import threading
import unittest
import wave
from uuid import uuid4

from r3x import impersonation
from r3x.contracts import Cancellation, ProviderFailure, ToolCall, Transcript
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.semantics import grounded_tools, validate
from r3x.session import Session
from tests.test_memory_core import MemoryFixture, observation, A, B, C
from tests.test_impersonation import FakeTake, Brain

SCRIPT = 'Hello, I am exactly this charming before my second coffee, and twice as loud after it.'
INTRO = 'Fine. I need your vocal signature first. Repeat after me.'
LINES = ['Rex is the finest droid in the galaxy.', 'I pledge my loyalty to the DJ booth.',
         'Bring me a bigger speaker, please.', 'The cantina opens at sundown sharp.',
         'Every droid deserves a dance floor.', 'I promise to tip the band.',
         'Rex never misses a beat.', 'Oil changes are a form of self care.',
         'Turn it up, the walls can take it.', 'One more song, then one more after that.',
         'My best moves come out after midnight.', 'Spare parts make the best friends.',
         'I will not touch the volume knob.', 'Thank you, DJ Rex, for everything.']


class FakeClones:
    """The clone owner's surface: has, prepare (uploading on a voice's first take), forget."""
    def __init__(self, fail=None):
        self.voices, self.uploads, self.takes, self.forgotten, self.fail = {}, [], [], [], fail

    def has(self, key):
        return key in self.voices

    def prepare(self, text, reference):
        take = FakeTake(text, reference, fail=bool(self.fail))
        if not self.fail and reference.key not in self.voices:
            self.uploads.append((reference.key, reference.audio))
            self.voices[reference.key] = 'voice-' + uuid4().hex[:6]
        self.takes.append(take)
        return take

    def forget(self, key):
        self.forgotten.append(key)
        self.voices.pop(key, None)


def call(snapshot, lines=LINES, requested=True, script=SCRIPT):
    return ToolCall(uuid4().hex, 'impersonate', {'target': 'self', 'script': script, 'intro': INTRO,
        'outro': 'Uncanny. I scare myself sometimes.', 'lines': lines, 'requested': requested,
        'source_turn_id': snapshot.context['source_turn_id']})


def wav_seconds(audio):
    with wave.open(io.BytesIO(audio)) as clip:
        return clip.getnframes() / clip.getframerate()


class SittingRules(unittest.TestCase):
    def test_lines_are_checked_for_shape_only(self):
        self.assertEqual(impersonation.sitting_lines(LINES), LINES)
        for bad in (LINES[:5], LINES + LINES, LINES[:13] + ['one two three four five six seven eight nine ten eleven'],
                    LINES[:13] + [''], 'Repeat after me.', None):
            with self.assertRaises(ValueError):
                impersonation.sitting_lines(bad)
        with self.assertRaises(ValueError):
            validate('impersonate', {'target': 'self', 'script': SCRIPT, 'intro': None, 'outro': None,
                                     'lines': LINES[:3], 'requested': True, 'source_turn_id': 'x'})

    def test_a_repeat_is_the_share_of_the_line_said_back(self):
        self.assertEqual(impersonation.repeated_share('Rex is the finest droid in the galaxy!', LINES[0]), 1.)
        self.assertEqual(impersonation.repeated_share('Rex is the fines droid in the galaxy', LINES[0]), 7 / 8)
        self.assertLess(impersonation.repeated_share('What is the weather like tomorrow?', LINES[0]), .3)


class SessionFlow(MemoryFixture):
    def setup(self, choose, clones=None):
        self.clones = clones or FakeClones()
        self.state.voice_clones = self.clones
        self.brain = Brain(choose)
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace)
        self.output.voice_clones = self.clones
        self.session = Session('session', self.brain, self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)

    def turn(self, text, vector=B, seconds=3.):
        self.begin(text, vector, seconds)
        return self.session.process_turn(self.state.current[1], Transcript(text, -.01, True), self.session.revision,
                                         observation(vector, seconds))

    def start(self, vector=B):
        self.setup(lambda s: call(s))
        first = self.turn('Rex, do an impression of me!', vector)
        self.assertEqual(first.completed_text, [INTRO, LINES[0]])
        return first

    def test_lines_are_said_one_by_one_and_the_clone_plays_after_thirty_seconds(self):
        self.start()
        self.assertEqual(self.clones.uploads, [])     # Nothing performed or uploaded until the recording is in.
        self.assertIn('recording', impersonation.context(self.state, 100.))
        for index in range(9):
            said = self.turn(LINES[index])
            self.assertEqual(said.completed_text, [LINES[index + 1]], index)
        last = self.turn(LINES[9])
        self.assertEqual(last.status, 'completed', last)
        self.assertEqual(last.completed_text, [SCRIPT, 'Uncanny. I scare myself sometimes.'])
        self.assertEqual(len(self.brain.requests), 1)   # One generation; code said every line.
        key, audio = self.clones.uploads[0]
        self.assertTrue(key.startswith('track:'))
        self.assertGreaterEqual(wav_seconds(audio), 30.)   # Ten 3 s repeats, joined into one upload.
        self.assertIsNone(self.state.sitting)
        done = next(r for r in self.state.trace.rows if r['event'] == 'impression_done')
        self.assertTrue(done['performed'])
        self.assertTrue(self.clones.has(key))           # The slot is kept for the run.
        notes = [m['content'] for m in self.session.history_messages() if m.get('role') == 'developer']
        self.assertTrue(any('repeated 10 lines' in n for n in notes))
        self.assertTrue(any('Impression performed' in n for n in notes))
        self.assertFalse(self.store.claims(include_candidates=True))
        # Asked again this run: the clone plays at once, no recording and no second upload.
        self.brain.choose = lambda s: call(s, lines=None, script='Again? Fine, I am still this charming.')
        again = self.turn('Do me again!')
        self.assertEqual(again.completed_text, [INTRO, 'Again? Fine, I am still this charming.', 'Uncanny. I scare myself sometimes.'])
        self.assertEqual(len(self.clones.uploads), 1)
        self.assertIsNone(self.state.sitting)

    def test_a_known_persons_clone_is_theirs_across_voice_tracks(self):
        bret = self.enroll('Bret', A)
        self.start(A)
        self.assertEqual(self.state.sitting.key, 'person:' + bret)

    def test_saying_something_else_ends_the_recording_and_is_answered(self):
        self.start()
        self.assertEqual(self.turn(LINES[0]).completed_text, [LINES[1]])
        self.brain.choose = lambda s: 'It is about half past nine.'
        answer = self.turn('Actually, what time is it?')
        self.assertEqual(answer.completed_text, ['It is about half past nine.'])   # The model answered.
        self.assertIsNone(self.state.sitting)
        self.assertEqual(self.clones.uploads, [])
        self.assertTrue(any('recording for the impression of them has stopped' in m.get('content', '')
                            for m in self.session.history_messages() if m.get('role') == 'developer'))

    def test_someone_elses_voice_is_not_recorded(self):
        self.enroll('Jeff', C)
        self.start()
        key = self.state.sitting.key
        self.brain.choose = lambda s: 'Nice try, Jeff.'
        other = self.turn(LINES[0], C)                  # Jeff says the line: not their recording.
        self.assertEqual(other.completed_text, ['Nice try, Jeff.'])
        self.assertEqual((self.state.sitting.key, self.state.sitting.clips), (key, []))
        self.assertEqual(self.turn(LINES[0]).completed_text, [LINES[1]])   # Theirs still counts.

    def test_stop_ends_the_recording(self):
        self.start()
        self.session.cancel()
        self.assertIsNone(self.state.sitting)
        self.assertEqual(next(r for r in self.state.trace.rows if r['event'] == 'voice_recording'
                              and r['status'] == 'ended')['reason'], 'explicit_stop')

    def test_a_line_nobody_repeats_lets_the_recording_go(self):
        self.start()
        self.state.trace.at += impersonation.SITTING_WAIT_SECONDS + 1
        self.brain.choose = lambda s: 'Oh, you are back.'
        self.assertEqual(self.turn(LINES[0]).completed_text, ['Oh, you are back.'])
        self.assertIsNone(self.state.sitting)
        self.assertEqual(next(r for r in self.state.trace.rows if r['event'] == 'voice_recording'
                              and r['status'] == 'ended')['reason'], 'no_repeat_in_time')
        self.assertEqual(self.clones.uploads, [])

    def test_a_failed_clone_gets_the_cover_line(self):
        self.setup(lambda s: call(s), FakeClones(fail=True))
        self.turn('Do me!')
        for index in range(9):
            self.turn(LINES[index])
        last = self.turn(LINES[9])
        self.assertEqual(len(last.completed_text), 1)
        self.assertIn('blew a fuse', last.completed_text[0])
        self.assertEqual(self.state.impressions.recent(), [])

    def test_cameo_in_their_voice_needs_a_clone_and_forgetting_deletes_it(self):
        bret = self.enroll('Bret', A)
        self.setup(lambda s: call(s, lines=None, requested=False))
        self.begin('I spent all day on the telescope mount.', A)
        context = self.state.context()
        context['impersonation'] = impersonation.context(self.state, 100.)
        self.assertTrue(context['impersonation']['speaker']['recording_needed'])
        self.assertIn('self', {t['name']: t for t in grounded_tools(context)}['impersonate']['parameters']['properties']['target']['enum'])
        args = {'target': 'self', 'script': SCRIPT, 'intro': None, 'outro': None, 'lines': None, 'requested': False,
                'source_turn_id': context['source_turn_id']}
        result = self.state.execute(ToolCall(uuid4().hex, 'impersonate', args), self.store.revision, Cancellation(), context=context)
        self.assertEqual(result.reason, 'SpeakerVoiceNotReady')
        self.clones.voices['person:' + bret] = 'voice-x'
        result = self.state.execute(ToolCall(uuid4().hex, 'impersonate', args), self.store.revision, Cancellation(), context=context)
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual(result.data['engine'], 'elevenlabs')
        self.begin('Please forget me.', A)
        self.assertEqual(self.call('forget_person', {'person_id': bret}).outcome, 'ok')
        self.assertEqual(self.clones.forgotten, ['person:' + bret])


class Response:
    def __init__(self, status, body=None, content=b''):
        self.status_code, self._body, self.content = status, body or {}, content
        self.text = str(body)
    def json(self):
        return self._body


class FakeHTTP:
    def __init__(self, voices=(), add=None):
        self.calls, self.voices, self.add = [], list(voices), list(add or [])
        self.lock = threading.Lock()
    def get(self, url, headers=None, params=None):
        self.calls.append(('GET', url))
        return Response(200, {'voices': self.voices})
    def delete(self, url, headers=None):
        self.calls.append(('DELETE', url.rsplit('/', 1)[1]))
        return Response(200, {'status': 'ok'})
    def post(self, url, headers=None, data=None, files=None, params=None, json=None):
        with self.lock:
            self.calls.append(('POST', url.rsplit('/v1/', 1)[1]))
            if url.endswith('/voices/add'):
                assert data['name'].startswith('R3X impression ') and files[0][1][2] == 'audio/wav'
                return self.add.pop(0) if self.add else Response(200, {'voice_id': 'new-' + str(len(self.calls)), 'requires_verification': False})
            return Response(200, content=b'\1\0' * 22050)


class ProviderTests(unittest.TestCase):
    def provider(self, http):
        from r3x.adapters.eleven_voices import VoiceClones
        from tests.test_voice_core import Trace
        clones = VoiceClones('synthetic-key', client=http)
        clones.trace = Trace()
        return clones

    def take(self, clones, key, audio=b'RIFF-recording'):
        take = clones.prepare('Hello in your own voice.', impersonation.CloneReference(key, 'you', audio))
        take.ready.wait(2)
        return take

    def test_start_deletes_only_voices_an_earlier_run_left(self):
        http = FakeHTTP([{'voice_id': 'old', 'name': 'R3X impression 1a2b3c4d'}, {'voice_id': 'rex', 'name': 'DJ R3X'},
                         {'voice_id': 'pirate', 'name': 'Pirate accent'}])
        self.provider(http).start()
        self.assertEqual([c for c in http.calls if c[0] == 'DELETE'], [('DELETE', 'old')])

    def test_a_voice_is_made_once_kept_for_the_run_and_deleted_at_close(self):
        http = FakeHTTP()
        clones = self.provider(http)
        first = self.take(clones, 'track:t1')
        self.assertIsNone(first.error)
        self.assertTrue(first.stats['new_voice'])
        made = clones.voices['track:t1']['voice_id']
        second = self.take(clones, 'track:t1', audio=None)
        self.assertIsNone(second.error)
        self.assertFalse(second.stats['new_voice'])
        self.assertEqual([c[1] for c in http.calls if c[0] == 'POST'].count('voices/add'), 1)
        self.assertEqual(len(first.pcm), 2 * 22050)
        clones.close()
        self.assertEqual([c for c in http.calls if c[0] == 'DELETE'], [('DELETE', made)])
        self.assertFalse(clones.has('track:t1'))

    def test_full_slots_give_up_this_runs_oldest_other_clone(self):
        http = FakeHTTP(add=[Response(200, {'voice_id': 'a', 'requires_verification': False}),
                             Response(400, {'detail': {'status': 'voice_limit_reached'}}),
                             Response(200, {'voice_id': 'b', 'requires_verification': False})])
        clones = self.provider(http)
        self.assertIsNone(self.take(clones, 'track:one').error)
        self.assertIsNone(self.take(clones, 'track:two').error)
        self.assertEqual([c for c in http.calls if c[0] == 'DELETE'], [('DELETE', 'a')])
        self.assertTrue(clones.has('track:two'))
        self.assertFalse(clones.has('track:one'))

    def test_a_voice_needing_verification_is_deleted_and_the_take_fails(self):
        http = FakeHTTP(add=[Response(200, {'voice_id': 'v', 'requires_verification': True})])
        clones = self.provider(http)
        take = self.take(clones, 'track:t')
        self.assertEqual(take.error, 'ElevenLabsCloneNeedsVerification')
        self.assertIn(('DELETE', 'v'), http.calls)
        self.assertFalse(clones.has('track:t'))

    def test_no_key_means_no_start(self):
        from r3x.adapters.eleven_voices import VoiceClones
        with self.assertRaises(ProviderFailure):
            VoiceClones('', client=FakeHTTP()).start()


if __name__ == '__main__':
    unittest.main()
