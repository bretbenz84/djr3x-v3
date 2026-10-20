"""Rex looks up his own past instead of calling it fictional (session 34d84a03, October 14)."""
import json

from r3x.contracts import Capture, ToolCall, ToolText, Transcript
from r3x.output import OutputOwner
from r3x.personal_history import SECTIONS, SUMMARIES, lookup
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import grounded_tools, validate
from r3x.session import Session
from tests.test_memory_core import MemoryFixture, observation


class PersonalHistoryTests(MemoryFixture):
    def ask(self, text, call):
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                if snapshot.continuation:
                    yield 'R2-D2, of course. He kept that ship in one piece.'
                    return
                yield ToolText('h', 'personal_history', 'Ah, the Endor Express.', True)
                yield call
        brain = Brain()
        session = Session('s', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        self.enroll('Sam')
        capture = Capture('cap', 's', bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        try:
            return brain, session.process_turn(capture, Transcript(text, 0, True), 0, observation(), ())
        finally:
            session.close()

    def test_a_star_tours_question_is_answered_from_his_own_memory(self):
        claims, people = self.store.claims(), len(self.store.people())
        brain, result = self.ask('Who was your navigation computer on Star Tours?',
            ToolCall('h', 'personal_history', {'say_first': 'Ah, the Endor Express.', 'section': 'crew'}))
        self.assertEqual(result.completed_text,
                         ['Ah, the Endor Express.', 'R2-D2, of course. He kept that ship in one piece.'])
        output = next(json.loads(r['output']) for r in brain.snapshots[-1].continuation
                      if r.get('type') == 'function_call_output')
        self.assertEqual((output['outcome'], output['data']['section']), ('ok', 'crew'))
        self.assertIn('R2-D2: his navigator', output['data']['memory'])
        self.assertIn('never as fictional backstory', output['data']['how_to_use'])
        self.assertEqual(self.store.claims(), claims)          # His memories are not facts about anyone.
        self.assertEqual(len(self.store.people()), people + 1)  # Only the enrolled speaker.

    def test_unknown_section_is_rejected_and_the_lookup_is_offered_only_in_conversation(self):
        with self.assertRaises(ValueError):
            validate('personal_history', {'say_first': None, 'section': 'tatooine'})
        with self.assertRaisesRegex(ValueError, 'UnknownHistorySection'):
            lookup('tatooine')
        self.begin('Tell me about your old trips to Endor.')
        self.assertIn('personal_history', {t['name'] for t in grounded_tools(self.state.context())})
        self.assertNotIn('personal_history', {t['name'] for t in grounded_tools({'trigger': 'visual_arrival'})})

    def test_every_section_is_described_and_the_owners_details_are_there(self):
        self.assertEqual(set(SECTIONS), set(SUMMARIES))
        history = '\n'.join(SECTIONS.values())
        for detail in ("I've got a very bad feeling about this!", 'Oga\'s Cantina', 'Black Spire Outpost',
                       'Star Wars land', 'January 9, 1987', 'Paul Reubens'):
            self.assertIn(detail, history)
        self.assertNotIn('Bret', history)

    def test_a_section_already_told_this_conversation_comes_back_marked(self):
        # October 20 probes: the Endor story was looked up and retold in three replies running.
        self.begin('What was flying like?')
        first = self.call('personal_history', {'say_first': 'Hmm.', 'section': 'the_flight'})
        self.begin('So what else is up with you?')
        again = self.call('personal_history', {'say_first': 'Hmm.', 'section': 'the_flight'})
        other = self.call('personal_history', {'say_first': 'Hmm.', 'section': 'retirement'})
        self.assertNotIn('already_told', first.data)
        self.assertIn('Do not retell it', again.data['already_told'])
        self.assertEqual(again.data['memory'], first.data['memory'])
        self.assertNotIn('already_told', other.data)
