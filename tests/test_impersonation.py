"""Voice impressions through the production Session: offered targets, references from
the core's own recordings, intro/clone/outro delivery, failure cover, cancellation and
what is (and is not) remembered. Blocked I/O: a fake cloner stands in for Breeze."""
from array import array
from pathlib import Path
import tempfile
import threading
import time
import unittest
import wave
from uuid import uuid4

from r3x import impersonation
from r3x.contracts import Cancellation, Capture, ProviderFailure, ToolCall, Transcript
from r3x.output import OutputOwner, ClonedPhrase
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.semantics import grounded_tools, validate
from r3x.session import Session
from tests.test_memory_core import MemoryFixture, observation, A, B, C

SCRIPT = 'Well now, I hear a droid has borrowed my voice. I built houses with my own hands; this fella cannot even hold a hammer.'


class FakeTake:
    def __init__(self, text, reference, *, fail=False, delay=0., never=False):
        self.text, self.reference = text, reference
        self.ready = threading.Event()
        self.closed = False
        self.error = None
        self.stats = {'synthesis_seconds': .5, 'audio_seconds': 6.}
        self.pcm = b'\1\0' * 2205 * 6
        def finish():
            if never:
                return
            if delay:
                time.sleep(delay)
            if fail:
                self.error = 'EmptyCloneAudio'
            self.ready.set()
        threading.Thread(target=finish, daemon=True).start()

    def chunks(self, cancel):
        while not self.ready.wait(.01):
            cancel.check()
        if self.error:
            raise ProviderFailure('ImpersonationSynthesisFailed:' + self.error)
        for offset in range(0, len(self.pcm), 4410):
            cancel.check()
            yield self.pcm[offset:offset + 4410]

    def close(self):
        self.closed = True


class FakeCloner:
    def __init__(self, **options):
        self.options, self.takes, self.started = options, [], True

    def prepare(self, text, reference):
        take = FakeTake(text, reference, **self.options)
        self.takes.append(take)
        return take

    def release_idle(self, now=None):
        pass


def impersonate(snapshot, target='jimmy-carter', requested=True, intro='Okay, clearing my vocal buffers. Ahem.',
                outro='Thank you, thank you. You are too kind.', script=SCRIPT):
    return ToolCall(uuid4().hex, 'impersonate', {'target': target, 'script': script, 'intro': intro, 'outro': outro,
                                                  'requested': requested, 'source_turn_id': snapshot.context['source_turn_id']})


class Brain:
    def __init__(self, choose):
        self.choose, self.requests = choose, []

    def reply(self, snapshot, cancel):
        self.requests.append(snapshot)
        if snapshot.continuation:
            yield 'That one did not come together.'
            return
        yield self.choose(snapshot)


def tone(seconds, rate=16000):
    """Loud square-ish audio with silent edges, so trimming has something to cut."""
    samples = array('h', bytes(2 * int(.4 * rate)))
    samples.extend(array('h', [12000 if (i // 40) % 2 else -12000 for i in range(int(seconds * rate))]))
    samples.extend(array('h', bytes(2 * int(.4 * rate))))
    return samples.tobytes()


class RosterTests(unittest.TestCase):
    def test_repository_roster_has_named_public_voices(self):
        voices = impersonation.famous_voices()
        self.assertIn('jimmy-carter', voices)
        self.assertEqual(voices['jimmy-carter'].name, 'Jimmy Carter')
        self.assertEqual(voices['jimmy-carter'].key, 'famous:jimmy-carter')
        self.assertTrue(Path(voices['jimmy-carter'].wav_path).is_file())
        self.assertTrue(voices['jimmy-carter'].text.startswith('Good evening.'))
        self.assertGreaterEqual(len(voices), 20)

    def test_clip_without_transcript_or_through_a_link_is_not_offered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for stem in ('alpha-one', 'beta-two'):
                (root / (stem + '.wav')).write_bytes(b'RIFF')
            (root / 'alpha-one.txt').write_text('Hello there.')
            (root / 'gamma.wav').symlink_to(root / 'alpha-one.wav')
            (root / 'gamma.txt').write_text('Hello there.')
            voices = impersonation.famous_voices(root)
        self.assertEqual(sorted(voices), ['alpha-one'])
        self.assertEqual(voices['alpha-one'].name, 'Alpha One')

    def test_script_is_capped_at_a_sentence_boundary(self):
        text = ' '.join(f'Sentence number {i} has exactly six words.' for i in range(12))
        capped = impersonation.cap_script(text, 20)
        self.assertLessEqual(len(capped.split()), 20)
        self.assertTrue(capped.endswith('.'))
        self.assertEqual(impersonation.cap_script('one long sentence ' * 30, 20).split(), ('one long sentence ' * 30).split())


class ReferenceTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.refs = impersonation.ReferenceStore(self.path.parent / 'cache' / 'voice_refs')

    def test_person_reference_comes_from_accepted_recordings_and_forgetting_removes_it(self):
        person = self.enroll()
        with self.store.transaction() as conn:
            reference = self.refs.person_reference(self.store, conn, person, 'Bret')
        self.assertIsNotNone(reference)
        self.assertEqual(reference.key, 'person:' + person)
        with wave.open(reference.wav_path) as clip:
            self.assertEqual((clip.getnchannels(), clip.getsampwidth(), clip.getframerate()), (1, 2, 16000))
            seconds = clip.getnframes() / 16000
        self.assertTrue(6 <= seconds <= 20.5)
        self.assertIn('old radios', reference.text)
        rows = self.store.rows('SELECT path FROM artifacts WHERE person_id=?', (person,))
        self.assertEqual(sorted(Path(r['path']).suffix for r in rows), ['.txt', '.wav'])
        self.begin('Please forget me.', A)
        self.assertEqual(self.call('forget_person', {'person_id': person}).outcome, 'ok')
        self.assertFalse(Path(reference.wav_path).exists())
        self.assertFalse(self.store.rows('SELECT path FROM artifacts'))

    def test_too_little_accepted_voice_yields_no_reference(self):
        person = self.introduce()
        with self.store.transaction() as conn:
            self.assertIsNone(self.refs.person_reference(self.store, conn, person, 'Bret'))
        ready, voiced, basis = impersonation.speaker_readiness(self.state)
        self.assertFalse(ready)

    def test_stranger_reference_uses_their_own_clean_track_samples_for_this_session_only(self):
        for text in ('I drove up from the valley this morning, took the back roads the whole way.',
                     'The bakery on the corner was already sold out of everything worth eating.',
                     'So I am running on coffee and spite, which is my natural state anyway.'):
            self.begin(text, B, seconds=3.)
        ready, voiced, basis = impersonation.speaker_readiness(self.state)
        self.assertTrue(ready, (voiced, basis))
        self.assertEqual(basis, 'this_voice_track')
        track = self.state.current[4].track_id
        reference = self.refs.track_reference(self.state.evidence[track].samples, track, 'session', 'you')
        self.assertEqual(reference.key, 'track:' + track)
        self.assertTrue(Path(reference.wav_path).is_file())
        self.assertFalse(self.store.rows('SELECT path FROM artifacts'))
        self.refs.close()
        self.assertFalse(Path(reference.wav_path).exists())

    def test_reference_audio_trims_silence_joins_with_gaps_and_pads_the_tail(self):
        clips = [(tone(2.5), 2.5, 'first clip', 16000), (tone(1.0), 1.0, 'too short', 16000), (tone(4.), 4., 'second clip', 16000)]
        chosen, voiced = impersonation.select_clips(clips)
        self.assertEqual([c[2] for c in chosen], ['first clip', 'second clip'])
        self.assertEqual(voiced, 6.5)
        samples, rate, text = impersonation.reference_audio(chosen)
        self.assertEqual(text, 'first clip second clip')
        seconds = len(samples) / rate
        # 2.5 + 4 s of signal, 0.15 s margins, a 0.3 s gap and a 0.5 s padded tail; the 0.4 s silent edges are gone.
        self.assertTrue(7.6 < seconds < 8.4, seconds)
        self.assertTrue(all(v == 0 for v in samples[-int(.4 * rate):]))


class StateTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.state.voice_references = impersonation.ReferenceStore(self.path.parent / 'cache' / 'voice_refs')

    def context(self):
        context = self.state.context()
        context['impersonation'] = impersonation.context(self.state, self.state.trace.clock())
        return context

    def call_impersonate(self, context, **overrides):
        args = {'target': 'jimmy-carter', 'script': SCRIPT, 'intro': None, 'outro': None, 'requested': True,
                'source_turn_id': context['source_turn_id'], **overrides}
        return self.state.execute(ToolCall(uuid4().hex, 'impersonate', args), self.store.revision, Cancellation(), context=context)

    def test_famous_target_resolves_to_the_tracked_clip_without_any_write(self):
        self.begin('Do an impression of Jimmy Carter.', C)
        context = self.context()
        self.assertIn('jimmy-carter', context['impersonation']['famous'])
        result = self.call_impersonate(context)
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual(result.data['voice'], 'famous:jimmy-carter')
        self.assertEqual(result.data['subject'], 'Jimmy Carter')
        self.assertEqual(result.data['script'], SCRIPT)
        self.assertFalse(result.data['script_capped'])
        self.assertEqual(result.revision, context['state_revision'])
        self.assertFalse(self.store.claims(include_candidates=True))

    def test_unoffered_target_untrusted_source_and_long_script_are_rejected(self):
        self.begin('Do Abraham Lincoln.', C)
        context = self.context()
        self.assertEqual(self.call_impersonate(context, target='abraham-lincoln').reason, 'ImpressionTargetNotOffered')
        self.assertEqual(self.call_impersonate(context, target='self').reason, 'SpeakerVoiceNotReady')
        with self.assertRaises(ValueError):
            validate('impersonate', {'target': 'jimmy-carter', 'script': 'word ' * 71, 'intro': None, 'outro': None,
                                     'requested': True, 'source_turn_id': 'x'})
        with self.assertRaises(ValueError):
            validate('impersonate', {'target': 'jimmy-carter', 'script': SCRIPT, 'intro': None, 'outro': None,
                                     'requested': 'yes', 'source_turn_id': 'x'})
        long = ' '.join(f'Sentence number {i} is here for the count.' for i in range(8))
        result = self.call_impersonate(context, script=long)
        self.assertEqual(result.outcome, 'ok')
        self.assertTrue(result.data['script_capped'])
        self.assertLessEqual(len(result.data['script'].split()), 40)
        cameo = self.call_impersonate(context, script=long, requested=False)
        self.assertLessEqual(len(cameo.data['script'].split()), 28)
        self.begin('Do Jimmy Carter.', C, trusted=False)
        context = self.context()
        self.assertEqual(self.call_impersonate(context).reason, 'UntrustedSource')

    def test_spontaneous_cameos_are_budgeted_but_requests_are_not(self):
        self.begin('We are driving to Plains, Georgia next week.', C)
        context = self.context()
        self.assertTrue(context['impersonation']['spontaneous_allowed'])
        self.assertEqual(self.call_impersonate(context, requested=False).outcome, 'ok')
        self.state.impressions.record('famous:jimmy-carter', 'Jimmy Carter', SCRIPT, False, self.state.trace.clock())
        self.begin('And then on to Atlanta.', C)
        context = self.context()
        self.assertFalse(context['impersonation']['spontaneous_allowed'])
        self.assertEqual(context['impersonation']['recent'][0]['voice'], 'famous:jimmy-carter')
        self.assertEqual(self.call_impersonate(context, requested=False).reason, 'SpontaneousImpressionCooldown:recent_cameo')
        self.assertEqual(self.call_impersonate(context, requested=True).outcome, 'ok')

    def test_self_and_known_people_are_offered_only_with_enough_accepted_voice(self):
        bret = self.enroll('Bret', A)
        self.begin('Do an impression of me.', A)
        context = self.context()
        offered = context['impersonation']
        self.assertTrue(offered['speaker']['ready'])
        self.assertEqual(offered['speaker']['basis'], 'accepted_recordings')
        self.assertEqual(offered['speaker']['name'], 'Bret')
        self.assertEqual(offered['people'], [])
        result = self.call_impersonate(context, target='self')
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual(result.data['voice'], 'person:' + bret)
        self.assertEqual(result.data['subject'], 'Bret')
        tools = {t['name']: t for t in grounded_tools(context)}
        self.assertEqual(tools['impersonate']['parameters']['properties']['target']['enum'][0], 'self')
        # Somebody else asks for Bret: his voice is a listed person, not "self".
        self.begin('Do an impression of Bret.', C)
        context = self.context()
        self.assertFalse(context['impersonation']['speaker']['ready'])
        self.assertEqual(context['impersonation']['people'], [{'target_id': bret, 'name': 'Bret'}])
        self.assertEqual(self.call_impersonate(context, target=bret).data['voice'], 'person:' + bret)
        enum = {t['name']: t for t in grounded_tools(context)}['impersonate']['parameters']['properties']['target']['enum']
        self.assertNotIn('self', enum)
        self.assertIn(bret, enum)

    def test_tool_is_not_offered_without_a_cloner_or_during_opportunities(self):
        self.begin('Hello there.', C)
        context = self.state.context()
        self.assertNotIn('impersonate', {t['name'] for t in grounded_tools(context)})
        context['impersonation'] = impersonation.context(self.state, 100.)
        self.assertIn('impersonate', {t['name'] for t in grounded_tools(context)})
        self.assertNotIn('impersonate', {t['name'] for t in grounded_tools({**context, 'trigger': 'conversation_lull'})})


class SessionTests(MemoryFixture):
    def setup_session(self, choose, cloner=None, sink=None):
        self.state.voice_references = impersonation.ReferenceStore(self.path.parent / 'cache' / 'voice_refs')
        self.cloner = cloner or FakeCloner()
        self.brain = Brain(choose)
        self.output = OutputOwner(SyntheticTTS(), sink or SyntheticSink(), self.state.trace, cloner=self.cloner)
        self.session = Session('session', self.brain, self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)
        return self.session

    def turn(self, text='Rex, do an impression of Jimmy Carter.', vector=C):
        self.begin(text, vector)
        return self.session.process_turn(self.state.current[1], Transcript(text, -.01, True), self.session.revision, observation(vector))

    def test_requested_impression_plays_intro_clone_and_outro_in_one_generation(self):
        self.setup_session(lambda s: impersonate(s))
        result = self.turn()
        self.assertEqual(result.status, 'completed', result)
        self.assertEqual(result.completed_text, ['Okay, clearing my vocal buffers. Ahem.', SCRIPT, 'Thank you, thank you. You are too kind.'])
        self.assertEqual(len(self.brain.requests), 1)
        rows = self.state.trace.rows
        generated = [r for r in rows if r['event'] == 'speech_generated']
        self.assertEqual([r.get('voice') for r in generated], [None, 'famous:jimmy-carter', None])
        done = next(r for r in rows if r['event'] == 'impression_done')
        self.assertTrue(done['performed'])
        self.assertEqual(done['subject'], 'Jimmy Carter')
        self.assertEqual(self.state.impressions.recent(), [{'voice': 'famous:jimmy-carter', 'script': SCRIPT}])
        self.assertTrue(self.cloner.takes[0].closed)
        notes = [m['content'] for m in self.session.history_messages() if m.get('role') == 'developer']
        self.assertTrue(any('Impression performed' in n and 'parody' in n for n in notes))
        self.assertEqual(self.session.history_messages()[-2]['content'], ' '.join(result.completed_text))
        self.assertFalse(self.store.claims(include_candidates=True))
        self.assertEqual(self.state.store.revision, 0)

    def test_failed_render_gets_a_cover_line_and_is_not_counted(self):
        self.setup_session(lambda s: impersonate(s), FakeCloner(fail=True, delay=.05))
        result = self.turn()
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.completed_text[0], 'Okay, clearing my vocal buffers. Ahem.')
        self.assertIn('blew a fuse', result.completed_text[1])
        self.assertEqual(len(result.completed_text), 2)
        self.assertEqual(self.state.impressions.recent(), [])
        done = next(r for r in self.state.trace.rows if r['event'] == 'impression_done')
        self.assertFalse(done['performed'])
        self.assertEqual(done['reason'], 'EmptyCloneAudio')
        self.assertTrue(any('could not be rendered' in m.get('content', '') for m in self.session.history_messages() if m.get('role') == 'developer'))

    def test_stop_during_preparation_closes_the_take_and_speaks_nothing_more(self):
        owner = self
        class StoppingSink(SyntheticSink):
            def play(self, chunks, cancel, on_start, visualize=True):
                receipt = super().play(chunks, cancel, on_start, visualize)
                threading.Timer(.05, owner.session.cancel).start()
                return receipt
        self.setup_session(lambda s: impersonate(s), FakeCloner(never=True), StoppingSink())
        result = self.turn()
        self.assertEqual(result.status, 'interrupted')
        self.assertEqual(result.completed_text, ['Okay, clearing my vocal buffers. Ahem.'])
        self.assertTrue(self.cloner.takes[0].closed)
        self.assertEqual(self.state.impressions.recent(), [])

    def test_rejected_cameo_falls_back_to_an_ordinary_reply(self):
        self.state.impressions.record('famous:jimmy-carter', 'Jimmy Carter', SCRIPT, False, 100.)
        self.setup_session(lambda s: impersonate(s, requested=False, intro=None, outro=None))
        result = self.turn('We are driving down to Plains, Georgia.')
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.completed_text, ['That one did not come together.'])
        self.assertEqual(len(self.brain.requests), 2)
        self.assertEqual(self.cloner.takes, [])
        self.assertEqual(next(r for r in self.state.trace.rows if r['event'] == 'tool_result' and r['name'] == 'impersonate')['reason'],
                         'SpontaneousImpressionCooldown:recent_cameo')

    def test_cameo_without_intro_plays_the_clone_then_the_reaction(self):
        self.setup_session(lambda s: impersonate(s, requested=False, intro=None, outro='Sorry, he gets like that.'))
        result = self.turn('We are driving down to Plains, Georgia.')
        self.assertEqual(result.completed_text, [SCRIPT, 'Sorry, he gets like that.'])
        self.assertFalse(self.state.impressions.performed[0]['requested'])

    def test_own_voice_impression_uses_the_speakers_accepted_recordings(self):
        self.enroll('Bret', A)
        self.setup_session(lambda s: impersonate(s, target='self', intro='Oh, this is going to be good.', outro=None))
        result = self.turn('Do an impression of me.', A)
        self.assertEqual(result.status, 'completed', result)
        self.assertEqual(result.completed_text, ['Oh, this is going to be good.', SCRIPT])
        take = self.cloner.takes[0]
        self.assertTrue(take.reference.key.startswith('person:'))
        self.assertTrue(Path(take.reference.wav_path).is_file())
        self.assertEqual(take.reference.name, 'Bret')


class OutputTests(unittest.TestCase):
    def test_await_take_returns_when_ready_and_fails_on_timeout(self):
        from tests.test_voice_core import Trace
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), Trace())
        take = FakeTake('x', None, delay=.05)
        self.assertTrue(output.await_take(take, Cancellation(), filler=False))
        slow = FakeTake('x', None, never=True)
        self.assertFalse(output.await_take(slow, Cancellation(), filler=False, timeout=.1))
        self.assertEqual(slow.error, 'ImpersonationPreparationTimeout')
        self.assertTrue(slow.closed)

    def test_filler_plays_only_while_waiting_and_only_when_the_sink_is_free(self):
        from tests.test_voice_core import Trace
        played = []
        class CountingSink(SyntheticSink):
            def play(self, chunks, cancel, on_start, visualize=True):
                played.append(visualize)
                return super().play(chunks, cancel, on_start, visualize)
        output = OutputOwner(SyntheticTTS(), CountingSink(), Trace())
        self.assertTrue(output.await_take(FakeTake('x', None, delay=.15), Cancellation(), filler=True))
        self.assertEqual(played, [False])
        played.clear()
        output.active.set()
        self.assertTrue(output.await_take(FakeTake('x', None, delay=.1), Cancellation(), filler=True))
        self.assertEqual(played, [])

    def test_cloned_phrase_plays_through_the_ordinary_speech_path(self):
        from queue import Queue
        from r3x.output import Delivery
        from tests.test_voice_core import Trace
        trace = Trace()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
        phrases, delivery = Queue(), Delivery()
        take = FakeTake(SCRIPT, None)
        phrases.put(ClonedPhrase(SCRIPT, take, 'famous:jimmy-carter'))
        phrases.put(None)
        output.run(phrases, Cancellation(), 'turn', delivery)
        self.assertEqual(delivery.status, 'completed')
        self.assertEqual(delivery.completed_text, [SCRIPT])
        self.assertEqual(next(r for r in trace.rows if r['event'] == 'phrase_completed')['voice'], 'famous:jimmy-carter')
        self.assertGreater(next(r for r in trace.rows if r['event'] == 'phrase_completed')['frames'], 0)


class ProviderTests(unittest.TestCase):
    def test_cloner_reports_missing_assets_without_loading_anything(self):
        from r3x.adapters.breeze import VoiceCloner, token_cap
        with tempfile.TemporaryDirectory() as directory:
            cloner = VoiceCloner(Path(directory).resolve() / 'missing')
            self.assertEqual(cloner.unavailable_reason(), 'BreezeModelMissing')
            with self.assertRaises(ProviderFailure):
                cloner.start()
            root = Path(directory).resolve() / 'present'
            (root / 'audio_tokenizer').mkdir(parents=True)
            (root / 'config.json').write_text('{}')
            self.assertEqual(cloner.__class__(root).unavailable_reason(), 'BreezeAssetMissing:tokenizer.json')
            with self.assertRaises(ProviderFailure):
                VoiceCloner(root).prepare('Hello', None)
        self.assertEqual(token_cap('one two three'), int(2. * 2. * 12.5))
        self.assertEqual(token_cap(' '.join(['word'] * 200)), 750)
        self.assertFalse(VoiceCloner(Path('/nonexistent')).capability.required)

    def test_settings_switch_must_be_boolean(self):
        from r3x.settings import Settings
        self.assertTrue(Settings().impersonation_enabled)
        with self.assertRaises(ValueError):
            Settings(impersonation_enabled='yes')
