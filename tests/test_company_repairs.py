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

    def test_a_no_op_quiet_mode_change_is_asked_again_without_it(self):
        from r3x.semantics import grounded_tools
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                if len(self.snapshots) == 1:
                    yield ToolCall('q', 'set_initiative', {'mode': 'available',
                                                           'source_turn_id': snapshot.context['source_turn_id']})
                else:
                    yield 'Looking right at you.'
        brain = Brain()
        result, _ = self.turn(brain, 'Hey Rex, can you look over at me?')
        self.assertEqual(result.completed_text, ['Looking right at you.'])
        self.assertEqual(len(brain.snapshots), 2)
        self.assertNotIn('set_initiative', {d['name'] for d in grounded_tools(brain.snapshots[1].context)})
        self.assertTrue(any(r['event'] == 'initiative_noop_retried' for r in self.state.trace.rows))
        self.assertFalse(self.state.initiative.quiet)


if __name__ == '__main__':
    unittest.main()
