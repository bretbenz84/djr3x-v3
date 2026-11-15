"""November 15 (c9755a3e): "The drivetrain upgrade is complete. I've told you that multiple times."
Two saved facts still had the upgrade planned and waiting on parts, and Rex kept talking about it as
still ahead. News that a saved plan has moved on updates that fact, also when Rex's motors left the
voice unconfirmed. Synthetic vectors, faces and providers only: these prove the contract, not how his
real voice scores or whether the reply model chooses the update."""
import json
import unittest

from r3x import conversation_memory as cm
from r3x.contracts import ToolCall
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests import test_october25_memory as october25, test_proactive_core
from tests.test_memory_core import MemoryFixture, C, observation
from tests.test_october25_memory import MASKED, TOO_FAR, seen
from tests.test_speaker_presence import face

PLAN = 'plans to install an upgraded drivetrain; parts arrive tomorrow'
DONE = 'installed the upgraded drivetrain'


class FinishedPlanTests(MemoryFixture):
    speak = october25.UnconfirmedVoiceFactTests.speak
    sure = october25.UnconfirmedVoiceFactTests.sure
    company = october25.UnconfirmedVoiceFactTests.company

    def setUp(self):
        super().setUp()
        self.person = self.enroll('Bret')

    def plan(self):
        self.assertEqual(self.speak('I am going to upgrade your drivetrain.').state, 'supported')
        result = self.call('record_claim', {'subject_id': self.person, 'predicate': 'upgrading the drivetrain',
                                            'value': PLAN, 'kind': 'self_report', 'source_turn_id': self.state.current[0]})
        self.assertEqual(result.data['status'], 'accepted')
        return result.data['claim_id']

    def update(self, claim, name='correct_claim'):
        args = {'claim_id': claim, 'source_turn_id': self.state.current[0]}
        return self.call(name, dict(args, value=DONE) if name == 'correct_claim' else args)

    def status(self, claim):
        return self.store.rows('SELECT status FROM claims WHERE id=?', (claim,))[0]['status']

    def test_his_masked_news_replaces_the_saved_plan_as_a_candidate(self):
        old = self.plan()
        self.company()
        recordings = self.store.rows('SELECT count(*) AS n FROM recordings')[0]['n']
        self.assertEqual(self.speak('The drivetrain upgrade is complete.', MASKED, motion=True).state, 'unresolved')
        result = self.update(old)
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual((result.data['status'], result.data['supersedes'], result.data['subject_id']),
                         ('candidate', old, self.person))
        new = self.store.rows('SELECT * FROM claims WHERE id=?', (result.data['claim_id'],))[0]
        self.assertEqual((new['said_by'], new['source_turn'], new['kind']), (None, self.state.current[0], 'self_report'))
        self.assertEqual(self.status(old), 'superseded')
        self.assertEqual(self.store.rows('SELECT count(*) AS n FROM recordings')[0]['n'], recordings)   # Never voice learning.
        memory = cm.context(self.store, {'faces': [face(self.person)], 'speaker': {},
                                         'people': [{'id': self.person, 'status': 'supported'}]}, 'session', 1e9)
        self.assertEqual({f['value']: f.get('status') for f in memory['interests_and_facts']}, {DONE: 'candidate'})

    def test_his_confirmed_news_replaces_it_as_an_accepted_fact(self):
        old = self.plan()
        self.speak('I already installed your new motors.')
        result = self.update(old)
        self.assertEqual((result.outcome, result.data['status']), ('ok', 'accepted'))
        self.assertEqual([c['value'] for c in self.store.claims(self.person)], [DONE])

    def test_a_voice_like_nobody_here_or_another_voice_this_visit_leaves_the_plan(self):
        old = self.plan()
        self.speak('It is done.', TOO_FAR, motion=True)
        self.assertEqual(self.update(old).reason, 'CorrectionNeedsSupportedSourceOrSubject')
        self.speak('It was me talking.', C, seconds=3.)                # Somebody else spoke this visit.
        self.assertEqual(self.speak('The upgrade is complete.', MASKED, motion=True).state, 'unresolved')
        self.assertEqual(self.update(old).reason, 'CorrectionNeedsSupportedSourceOrSubject')
        self.assertEqual(self.status(old), 'accepted')

    def test_with_someone_else_recognized_the_plan_stays(self):
        old = self.plan()
        self.speak('It is done.', MASKED, motion=True, faces=lambda now: [seen('jeremy', now)])
        self.assertEqual(self.update(old).reason, 'CorrectionNeedsSupportedSourceOrSubject')
        self.assertEqual(self.status(old), 'accepted')

    def test_his_masked_voice_cannot_update_someone_elses_fact_or_retract_his_own(self):
        other = self.enroll('Jeremy', C)
        self.begin('I am going to repaint the kitchen.', C)
        theirs = self.call('record_claim', {'subject_id': other, 'predicate': 'repainting', 'value': 'plans to repaint',
                                            'kind': 'self_report', 'source_turn_id': self.state.current[0]}).data['claim_id']
        self.at += 1000                                                # Jeremy left long ago.
        old = self.plan()
        self.sure()
        self.company()
        self.assertEqual(self.speak('That is finished.', MASKED, motion=True).speaker_id, None)
        self.assertEqual(self.update(theirs).reason, 'CorrectionNeedsSupportedSourceOrSubject')
        self.assertEqual(self.update(old, 'retract_claim').reason, 'CorrectionNeedsSupportedSourceOrSubject')
        self.assertEqual((self.status(theirs), self.status(old)), ('accepted', 'accepted'))
        self.assertEqual(self.update(old).data['status'], 'candidate')


class RefusedUpdateTalkTests(MemoryFixture):
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    turn = test_proactive_core.InitiativeCoreTests.turn

    def test_a_refused_update_is_answered_without_its_draft_or_any_talk_of_saving(self):
        person = self.enroll()
        self.begin('I am going to upgrade your drivetrain.')
        old = self.call('record_claim', {'subject_id': person, 'predicate': 'upgrading the drivetrain', 'value': PLAN,
                                         'kind': 'self_report', 'source_turn_id': self.state.current[0]}).data['claim_id']
        requests = []
        class Refused:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield ToolCall('update', 'correct_claim', dict(claim_id=old, value=DONE,
                        source_turn_id=snapshot.context['source_turn_id'], text='Noted. Upgrade logged.'))
                else:
                    yield 'New motors. Try to keep up with me now.'
        self.setup_core(Refused(), OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace))
        result = self.turn('The upgrade is complete.', observation(C))   # A voice like nobody known.
        self.assertEqual(' '.join(result.completed_text), 'New motors. Try to keep up with me now.')
        self.assertEqual(json.loads(requests[1].continuation[-1]['output'])['outcome'], 'rejected')
        self.assertIn('Never tell them a save failed', requests[1].messages[-1]['content'])
        self.assertEqual(self.store.rows('SELECT status FROM claims WHERE id=?', (old,))[0]['status'], 'accepted')


if __name__ == '__main__':
    unittest.main()
