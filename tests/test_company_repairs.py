"""October 17 run with the owner, Jeremy and a third person: company, concessions, news and
the quiet-mode no-op; scripted providers and synthetic data only."""
import unittest
from types import SimpleNamespace

from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import TextOutput
from r3x.present import Present, conceded
from r3x.session import Session, company_evidence
from tests.test_adapters import Client, Response
from tests.test_memory_core import MemoryFixture, observation


class CompanyEvidenceTests(unittest.TestCase):
    def test_two_people_around_and_whether_his_name_was_said(self):
        faces = [{'track_id': 'a', 'box': [.1, .1, .1, .1], 'face_width_px': 80},
                 {'track_id': 'b', 'box': [.6, .1, .1, .1], 'face_width_px': 80}]
        self.assertEqual(company_evidence({'current_view': {'faces': faces}}, 'He came from that country.'),
                         {'people_around': 2, 'rex_named': False})
        self.assertTrue(company_evidence({'current_view': {'faces': faces}}, 'Rex, look at me.')['rex_named'])
        self.assertIsNone(company_evidence({'current_view': {'faces': faces[:1]}}, 'Hello.'))
        far = [faces[0], {**faces[1], 'face_width_px': 20}]
        self.assertIsNone(company_evidence({'current_view': {'faces': far}}, 'Hello.'))   # A tiny far face is not company.
        self.assertEqual(company_evidence({'current_view': {'faces': faces}}, None), {'people_around': 2})

    def test_in_company_the_request_names_the_rule_and_the_persona_holds_it(self):
        from r3x.adapters.hosted import PERSONA, OpenAIBrain
        from r3x.contracts import Cancellation, Snapshot
        brain = OpenAIBrain('fixture')
        brain.client = Client(Response([{'type': 'response.output_text.delta', 'delta': 'Hi.'}, {'type': 'response.completed'}]))
        list(brain.reply(Snapshot('s', 't', 0, ({'role': 'user', 'content': 'hi'},), tools_enabled=True,
                                  context={'source_turn_id': 't', 'company': {'people_around': 3, 'rex_named': False}}),
                         Cancellation()))
        note = brain.client.calls[0][1]['json']['input'][1]['content']
        self.assertIn('in_company', note)
        self.assertIn('You got in a conversation that', PERSONA)
        self.assertIn('Never open a line with "You\'re right"', PERSONA)


class ConcessionTests(unittest.TestCase):
    def test_his_concessions_are_counted_and_named_in_the_picture(self):
        self.assertTrue(conceded("You're right—I made up the pyramid."))
        self.assertTrue(conceded('Yeah. I should’ve waited instead of jumping in.'))
        self.assertTrue(conceded('Sorry, I lost track.'))
        self.assertFalse(conceded('Four-fifty. Bargain.'))
        self.assertFalse(conceded('Right turn coming up.'))
        present = Present()
        present.rex_said('Four-fifty. Bargain.', 100)
        self.assertNotIn('concessions', present.describe(101)['conversation'])
        present.rex_said("You're right, I misheard.", 110)
        present.rex_said('My mistake.', 120)
        self.assertIn('2 of his lines', present.describe(121)['conversation']['concessions'])
        self.assertNotIn('concessions', present.describe(121 + 601)['conversation'])   # Ten minutes on, forgotten.


class NewsAndQuietTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        from r3x.observations import Cue
        cue = Cue('n1', 'news', 'BBC News', 'https://example.test', 99., 10**12, 'Flying car prototype makes first test hop')
        rows = {'news': (cue,)}
        self.state.observations = SimpleNamespace(
            listing=lambda topics=(), limit=14: [{'kind': c.kind, 'source': c.source, 'text': c.text}
                                                for group in rows.values() for c in group],
            valid=lambda cue_id: True)

    def turn(self, brain, text):
        self.begin(text)
        session = Session('session', brain, TextOutput(self.state.trace), self.state.trace, self.state)
        self.addCleanup(session.close)
        return session.process_turn(self.state.current[1], Transcript(text, 0, True), 0, observation()), session

    def test_asked_about_the_news_he_reads_what_was_fetched(self):
        from r3x.semantics import grounded_tools
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                if snapshot.continuation:
                    yield 'BBC says a flying car prototype just made its first hop.'
                else:
                    yield ToolCall('n', 'news_today', {'say_first': 'Let me think what I read.'})
        brain = Brain()
        result, _ = self.turn(brain, 'Is there anything in the news today?')
        self.assertIn('news_today', {d['name'] for d in grounded_tools(brain.snapshots[0].context)})
        self.assertIn('Flying car prototype', str(brain.snapshots[1].continuation))
        self.assertEqual(result.completed_text, ['Let me think what I read.',
                                                 'BBC says a flying car prototype just made its first hop.'])

    def test_resuming_is_offered_only_while_quiet_and_the_turn_then_does_the_rest(self):
        # October 22 (1072c10a): set_initiative drew "Can you..." requests (15 wrong calls in 7 runs),
        # and "You can talk again, but you can raise your visor too" lifted quiet and promised a visor
        # that never moved. The switch runs first; the same turn then answers the rest with any tool.
        from r3x.semantics import grounded_tools
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                if len(self.snapshots) == 1:
                    yield ToolCall('q', 'resume_chiming_in', {'source_turn_id': snapshot.context['source_turn_id']})
                else:
                    yield 'Back on the air, and looking right at you.'
        class Free(Brain):
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                yield 'Looking right at you.'
        free = Free()
        self.turn(free, 'Hey Rex, can you look over at me?')
        offered = {d['name'] for d in grounded_tools(free.snapshots[0].context)}
        self.assertIn('stop_chiming_in', offered)
        self.assertNotIn('resume_chiming_in', offered)          # Nothing to resume: never offered.
        self.state.initiative.quiet = True
        brain = Brain()
        result, _ = self.turn(brain, 'You can talk again, and look over at me.')
        self.assertIn('resume_chiming_in', {d['name'] for d in grounded_tools(brain.snapshots[0].context)})
        self.assertFalse(self.state.initiative.quiet)
        self.assertEqual(len(brain.snapshots), 2)
        then = {d['name'] for d in grounded_tools(brain.snapshots[1].context)}
        self.assertFalse(then & {'stop_chiming_in', 'resume_chiming_in'})
        self.assertIn('do it now with its tool', str(brain.snapshots[1].messages))
        self.assertEqual(result.completed_text, ['Back on the air, and looking right at you.'])

if __name__ == '__main__':
    unittest.main()


class October18Repairs(MemoryFixture):
    """October 18 run (owner, a friend on the couch, Max, Toby and Nova)."""

    def propose(self, steps, recent_motion, now):
        from tests.test_drive_session import drive_call
        from r3x.contracts import Cancellation
        self.state.trace.at = now
        drive = {'available': True, 'origin': 'self_initiated', 'vision_seek': True, 'last_sought': None,
                 'recent_motion': recent_motion,
                 'targets': [{'target_id': 'nearest_person', 'kind': 'nearest_person', 'visible_now': True},
                             {'target_id': 'object-door', 'kind': 'object', 'label': 'door', 'bearing_deg': 40}]}
        context = {'source_turn_id': 't', 'state_revision': self.store.revision, 'drive': drive}
        return self.state.propose_drive(drive_call(*steps), context, Cancellation())

    def test_a_position_he_was_asked_for_is_kept_against_his_own_wandering(self):
        from tests.test_drive_session import step
        faced = [{'origin': 'requested', 'status': 'completed', 'finished_at': 100., 'steps_run': ['face:completed']}]
        with self.assertRaisesRegex(ValueError, 'KeepingRequestedPosition'):
            self.propose([step('approach', target='object-door')], faced, 130.)
        self.propose([step('face', target='nearest_person')], faced, 130.)          # Toward a person is fine.
        self.propose([step('approach', target='object-door')], faced, 100. + 121)   # Two minutes on, free again.
        own = [{**faced[0], 'origin': 'self_initiated'}]
        self.propose([step('approach', target='object-door')], own, 130.)           # His own earlier move holds nothing.

    def test_another_name_for_a_known_person_becomes_an_alias(self):
        self.enroll('Bret')
        self.begin('To your left is my friend Exudica.')
        self.call('introduce_person', {'name': 'Exudica', 'aliases': [], 'subject': 'other', 'person_id': None,
                                       'face_track_id': None})
        friend = self.store.resolve('Exudica')[0]
        self.begin("She's also known as Tjoy.")
        self.call('introduce_person', {'name': 'Exudica', 'aliases': ['Tjoy'], 'subject': 'other',
                                       'person_id': friend['id'], 'face_track_id': None})
        self.assertEqual(self.store.person(friend['id'])['aliases'], ['Tjoy'])
        self.assertEqual(self.store.resolve('Tjoy')[0]['id'], friend['id'])

    def test_a_known_persons_name_is_never_saved_as_a_pet(self):
        from r3x.contracts import ToolCall
        self.enroll('Bret')
        self.begin('To your left is my friend Exudica.')
        self.call('introduce_person', {'name': 'Exudica', 'aliases': [], 'subject': 'other', 'person_id': None,
                                       'face_track_id': None})
        self.state.photo_memory = SimpleNamespace(data={'revision': 0})
        context = {'state_revision': self.store.revision, 'photo_memory': {'revision': 0, 'objects': []},
                   'photo_inspection': {'evidence_id': 'e'}}
        result = self.state.apply_photo(ToolCall('p', 'remember_object', {'evidence_id': 'e', 'entity_id': None,
            'name': 'Exudica', 'kind': 'pet', 'notes': 'a dark dog', 'source_turn_id': self.state.current[0],
            'text': 'Got it.'}), context, __import__('r3x.contracts', fromlist=['Cancellation']).Cancellation())
        self.assertEqual((result['outcome'], result.get('reason')), ('rejected', 'NameBelongsToPerson'))

    def test_concessions_after_a_lead_in_are_counted_and_the_reply_is_reminded(self):
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Cancellation, Snapshot
        present = Present()
        present.rex_said("Let me check what's there now. You're right, you're not holding a dog.", 100)
        picture = present.describe(101)
        self.assertIn('concessions', picture['conversation'])
        brain = OpenAIBrain('fixture')
        brain.client = Client(Response([{'type': 'response.output_text.delta', 'delta': 'Hi.'}, {'type': 'response.completed'}]))
        list(brain.reply(Snapshot('s', 't', 0, ({'role': 'user', 'content': 'hi'},), tools_enabled=True,
                                  context={'source_turn_id': 't', 'right_now': picture}), Cancellation()))
        last = brain.client.calls[0][1]['json']['input'][-1]
        self.assertEqual(last['role'], 'developer')
        self.assertIn('No "You\'re right"', last['content'])
