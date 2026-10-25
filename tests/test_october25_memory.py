"""October 25 (a0afcf85): what Bret tells Rex about himself is kept, and Rex never talks about a
failed save. Synthetic vectors, faces and providers only: these prove the contract, not how his real
voice scores under Rex's motors or how often the camera flickers."""
import json
import unittest
from uuid import uuid4

from r3x import conversation_memory as cm
from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import OutputOwner
from r3x.present import Present
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests import test_proactive_core
from tests.test_memory_core import MemoryFixture, A, C, observation
from tests.test_speaker_presence import clip, face, vec

MASKED = vec(.30, .954, 0)     # .30 to A: his voice under Rex's own motor noise.
TOO_FAR = vec(.15, .989, 0)    # .15 to A: how other voices and the television scored.


def seen(person, at, track='face'):
    """A face the camera has followed for a while."""
    return {**face(person, track), 'at': at, 'track_first_seen': at - 5}


def flicker(at, track='flicker'):
    """A detection in one frame only: the faint face at the edge of his view that evening."""
    return {**face(None, track), 'at': at, 'track_first_seen': at, 'box': [.7, .1, .22, .52], 'position': 'right'}


class PhantomFaceTests(unittest.TestCase):
    def test_a_face_in_a_single_frame_is_not_company_but_a_followed_one_is(self):
        present = Present()
        present.saw_faces([seen('bret', 100.), flicker(100.)], 100.)
        self.assertEqual(present.company_at, float('-inf'))
        present.saw_faces([seen('bret', 101.), seen(None, 101., 'guest')], 101.)
        self.assertEqual(present.company_at, 101.)

    def test_a_flicker_ends_the_private_carry_without_making_company(self):
        present = Present()
        people = [{'id': 'bret', 'status': 'supported'}]
        present.note_audience({'faces': [seen('bret', 99.)], 'speaker': {}, 'people': people}, 99.)
        context = present.note_audience({'faces': [seen(None, 100.), flicker(100.)], 'speaker': {}, 'people': people}, 100.)
        self.assertIsNone(present.audience_seen)          # Any doubt ends the carry at once.
        self.assertNotIn('recent_audience', context)
        self.assertEqual(present.company_at, float('-inf'))
        present.note_audience({'faces': [seen(None, 101.), seen(None, 101., 'guest')], 'speaker': {}, 'people': people}, 101.)
        self.assertEqual(present.company_at, 101.)


class UnconfirmedVoiceFactTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.person = self.enroll('Bret')

    def speak(self, text, vector=A, faces=(), seconds=2., motion=False):
        self.at += 10
        capture = Capture(uuid4().hex, 'session', uuid4().bytes*int(2000*seconds), self.at, self.at+seconds,
                          self.at+seconds, voiced_intervals=((0., seconds),), motion_risk=motion)
        self.state.trace.at = self.at + seconds
        faces = faces(self.at + seconds) if callable(faces) else list(faces)
        self.state.begin(uuid4().hex, capture, Transcript(text, -.01, True), clip(vector, seconds), faces)
        return self.state.current[4]

    def sure(self):
        for text in ('I am an IT systems administrator.', 'I answer tickets and patch servers.'):
            self.assertEqual(self.speak(text).state, 'supported')

    def company(self):
        """A guest stood beside him for a moment and never spoke."""
        self.assertEqual(self.speak('Working on websites.', faces=lambda now: [seen(self.person, now),
                                                                              seen(None, now, 'guest')]).state,
                         'supported')

    def save(self, value, subject_id=None):
        return self.call('record_claim', {'subject_id': subject_id, 'predicate': 'dislikes', 'value': value,
                                          'kind': 'self_report', 'source_turn_id': self.state.current[0]})

    def recordings(self):
        return self.store.rows('SELECT count(*) AS n FROM recordings')[0]['n']

    def test_his_masked_voice_is_carried_past_a_face_in_one_frame_and_his_fact_accepted(self):
        self.sure()
        attribution = self.speak('I hate paperwork.', MASKED, motion=True,
                                 faces=lambda now: [seen(self.person, now), flicker(now)])
        self.assertEqual((attribution.state, attribution.inferred_person_id), ('inferred', self.person))
        self.assertEqual(self.speak('Every morning, too.', MASKED, motion=True).state, 'inferred')   # No company.
        result = self.save('hates paperwork')
        self.assertEqual((result.outcome, result.data['status'], result.data['said_by']), ('ok', 'accepted', self.person))

    def test_after_company_his_masked_fact_is_kept_as_his_candidate_and_reaches_his_memory(self):
        self.sure()
        self.company()
        recordings = self.recordings()
        self.assertEqual(self.speak('I hate paperwork.', MASKED, motion=True).state, 'unresolved')
        result = self.save('hates paperwork')
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual((result.data['status'], result.data['said_by'], result.data['subject_id']),
                         ('candidate', None, self.person))
        claim = self.store.rows('SELECT source_turn,said_by FROM claims WHERE id=?', (result.data['claim_id'],))[0]
        self.assertEqual((claim['source_turn'], claim['said_by']), (self.state.current[0], None))
        self.assertEqual(self.recordings(), recordings)              # Never voice learning.
        self.assertEqual(self.state.identity_targets('forget_person'), [])
        self.assertEqual(self.speak('I collect radios.').state, 'supported')
        self.assertEqual(self.save('radios').data['status'], 'accepted')
        memory = cm.context(self.store, {'faces': [face(self.person)], 'speaker': {},
                                         'people': [{'id': self.person, 'status': 'supported'}]}, 'session', 1e9)
        facts = {f['value']: f.get('status') for f in memory['interests_and_facts']}
        self.assertEqual(facts, {'hates paperwork': 'candidate', 'radios': None})
        self.assertEqual([c['value'] for c in self.store.claims(self.person)], ['radios'])   # Accepted ones alone.

    def test_after_a_quiet_spell_his_face_alone_files_his_masked_fact(self):
        self.sure()
        self.at += 400                                                # The carried voice has lapsed.
        attribution = self.speak('I used to get breakfast at gas stations.', MASKED, motion=True,
                                 faces=lambda now: [seen(self.person, now)])
        self.assertEqual(attribution.state, 'unresolved')
        result = self.save('gas-station breakfasts before high school')
        self.assertEqual((result.outcome, result.data['status'], result.data['subject_id']),
                         ('ok', 'candidate', self.person))
        self.speak('Me again.', MASKED, motion=True, faces=lambda now: [seen(None, now)])
        self.assertEqual(self.save('likes mornings').reason, 'UnresolvedSelfReportSubject')   # Nobody known in view.

    def test_a_clear_speakers_own_fact_with_the_subject_left_out_is_his(self):
        self.sure()
        result = self.save('hates paperwork')
        self.assertEqual((result.outcome, result.data['subject_id'], result.data['status']),
                         ('ok', self.person, 'accepted'))

    def test_a_voice_like_nobody_here_or_another_voice_this_visit_stays_off_his_record(self):
        self.sure()
        self.speak('Who are you?', TOO_FAR, motion=True)
        self.assertEqual(self.save('hates paperwork').reason, 'UnresolvedSelfReportSubject')
        self.speak('It was me talking.', C, seconds=3.)                # Somebody else spoke this visit.
        self.assertEqual(self.speak('I hate paperwork.', MASKED, motion=True).state, 'unresolved')
        self.assertEqual(self.save('hates paperwork', self.person).reason, 'UnresolvedSelfReportSubject')
        self.assertEqual(self.store.claims(include_candidates=True), [])

    def test_with_someone_else_recognized_the_masked_fact_is_not_his(self):
        self.sure()
        self.speak('Look over here.', MASKED, motion=True, faces=lambda now: [seen('jeremy', now)])
        self.assertEqual(self.save('hates paperwork').reason, 'UnresolvedSelfReportSubject')
        self.assertEqual(self.store.claims(include_candidates=True), [])

    def test_a_masked_interest_is_kept_but_starts_no_news_search(self):
        self.sure()
        self.company()
        self.speak('I love the Kings.', MASKED, motion=True)
        result = self.call('remember_interest', dict(predicate='interest', value='loves the Sacramento Kings',
                                                     topic='Sacramento Kings basketball', source_turn_id=self.state.current[0]))
        self.assertEqual((result.outcome, result.data['status'], result.data['subject_id']),
                         ('ok', 'candidate', self.person))
        self.assertFalse(self.store.rows('SELECT * FROM interests'))


class SaveTalkTests(MemoryFixture):
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    turn = test_proactive_core.InitiativeCoreTests.turn

    def test_a_refused_save_is_answered_without_its_draft_or_any_talk_of_saving(self):
        person = self.enroll()
        requests = []
        class Refused:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield ToolCall('save', 'record_claim', dict(subject_id=person, subject_reference=None,
                        predicate='built', value='a homemade telescope', kind='self_report',
                        source_turn_id=snapshot.context['source_turn_id'], text="Noted. I'll remember that."))
                else:
                    yield 'A telescope you printed yourself? Bold.'
        self.setup_core(Refused(), OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace))
        result = self.turn('I printed a telescope.', observation(C))   # A voice like nobody known.
        self.assertEqual(' '.join(result.completed_text), 'A telescope you printed yourself? Bold.')
        self.assertEqual(json.loads(requests[1].continuation[-1]['output'])['outcome'], 'rejected')
        note = requests[1].messages[-1]
        self.assertEqual(note['role'], 'developer')
        self.assertIn('Never tell them a save failed', note['content'])
        self.assertIn('NOT spoken', note['content'])

    def test_a_refused_pet_note_is_answered_without_talking_about_saving(self):
        from pathlib import Path
        from r3x.photo_memory import PhotoMemory
        album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); album.start()
        album.data['entities'] = {'max-id': {'name': 'Max', 'kind': 'pet', 'notes': 'Small dark dog.',
                                             'references': [], 'history': []}}
        self.state.photo_memory = album
        requests = []
        class Refused:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield ToolCall('note', 'edit_object_memory', {'entity_id': 'max-id', 'action': 'correct', 'name': 'Max',
                        'notes': 'Brown paws.', 'remove_notes': None, 'source_turn_id': snapshot.context['source_turn_id'],
                        'text': "Brown paws. I'll add that."})
                else:
                    yield 'Brown paws on a dark dog. Very coordinated.'
        try:
            self.setup_core(Refused(), OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace))
            result = self.turn('He has brown paws.', observation(C))   # No pet named: the note is refused.
            self.assertEqual(' '.join(result.completed_text), 'Brown paws on a dark dog. Very coordinated.')
            self.assertEqual(album.data['entities']['max-id']['notes'], 'Small dark dog.')
            self.assertIn('Never tell them a save failed', requests[1].messages[-1]['content'])
        finally:
            album.close()

    def test_a_candidate_save_speaks_its_own_reply_without_a_second_call(self):
        self.enroll()
        requests = []
        class Candidate:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                yield ToolCall('save', 'record_claim', dict(subject_id=None, subject_reference='Bret',
                    predicate='owns', value='an old radio', kind='third_party',
                    source_turn_id=snapshot.context['source_turn_id'], text='An old radio. Of course he does.'))
        self.setup_core(Candidate(), OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace))
        result = self.turn('Bret has an old radio.', observation(C))
        self.assertEqual(result.completed_text, ['An old radio. Of course he does.'])
        self.assertEqual(len(requests), 1)
        self.assertEqual([c['status'] for c in self.store.claims(include_candidates=True)], ['candidate'])


if __name__ == '__main__':
    unittest.main()
