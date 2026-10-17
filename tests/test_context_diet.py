"""October 17 reply context diet: fixed meanings ride once in the cached persona, per-run
constants sit before the history, and the data rows carry what the model needs; synthetic only."""
import json
import unittest

from r3x.adapters.hosted import PERSONA, OpenAIBrain
from r3x.contracts import Cancellation, Snapshot
from r3x.field_guide import GUIDE, GUIDE_TEXTS, slim
from tests import test_memory_core as memory_tests
from tests.test_adapters import Client, Response


def send(context, history=({"role": "user", "content": "earlier words"}, {"role": "assistant", "content": "earlier reply"})):
    brain = OpenAIBrain("fixture")
    brain.client = Client(Response([{"type": "response.output_text.delta", "delta": "Hi."}, {"type": "response.completed"}]))
    list(brain.reply(Snapshot("s", "t", 0, history, context=context, tools_enabled=True), Cancellation()))
    return brain.client.calls[0][1]["json"], list(history)


class GuideTests(unittest.TestCase):
    def test_fixed_meanings_travel_once_in_the_persona_not_in_every_request(self):
        context = {'source_turn_id': 'turn-1',
                   'right_now': {'people_here': [], 'meaning': GUIDE['right_now']},
                   'drive': {'targets': [], 'clearance_meaning': GUIDE['drive.clearance_m'], 'meaning': GUIDE['drive'],
                             'limits': {'max_move_m': 10, 'meaning': GUIDE['drive.limits']}},
                   'visual_memory': [{'id': 'v1', 'meaning': GUIDE['visual_memory (each row)']}],
                   'odd': {'meaning': 'A fresh explanation nobody registered yet.'}}
        payload, history = send(context)
        self.assertTrue(payload['instructions'].startswith(PERSONA[:200]))
        for text in GUIDE_TEXTS:
            self.assertIn(text, payload['instructions'])
        self.assertEqual(payload['input'][:2], history)                       # No static block without constants.
        shown = payload['input'][2]['content']
        for text in (GUIDE['right_now'], GUIDE['drive'], GUIDE['drive.clearance_m'], GUIDE['drive.limits'],
                     GUIDE['visual_memory (each row)']):
            self.assertNotIn(text, shown)
        self.assertIn('A fresh explanation nobody registered yet.', shown)       # Unregistered text still travels.
        self.assertIn('"max_move_m": 10', shown)                                   # Data stays.
        self.assertIn('SITUATIONS NOW: ordinary_human_turn', shown)
        self.assertNotIn('TURN TAKING: Respond to new meaning', shown)
        self.assertIn('TURN TAKING: Respond to new meaning', payload['instructions'])

    def test_per_run_constants_sit_before_the_history(self):
        context = {'source_turn_id': 'turn-2',
                   'impersonation': {'available': True, 'famous': ['jimmy-carter', 'john-kennedy'],
                                     'people': [], 'meaning': GUIDE['impersonation']}}
        payload, history = send(context)
        self.assertEqual(payload['input'][0]['role'], 'developer')
        self.assertIn('STATIC CONTEXT', payload['input'][0]['content'])
        self.assertIn('jimmy-carter', payload['input'][0]['content'])
        self.assertEqual(payload['input'][1:3], history)
        shown = payload['input'][3]['content']
        self.assertIn('CURRENT CORE CONTEXT', shown)
        self.assertNotIn('jimmy-carter', shown)
        self.assertIn('"available": true', shown)

    def test_slim_leaves_the_original_untouched(self):
        context = {'drive': {'meaning': GUIDE['drive'], 'targets': [1]}}
        self.assertEqual(slim(context), {'drive': {'targets': [1]}})
        self.assertIn('meaning', context['drive'])


class RowTrimTests(memory_tests.MemoryFixture):
    def test_claims_people_and_exchanges_carry_what_the_model_needs(self):
        from r3x import conversation_memory
        person = self.enroll('Bret')
        self.begin("I love the Sacramento Kings.")
        self.call('record_claim', dict(subject_id=person, subject_reference=None, predicate='interest',
                                       value='loves the Kings', kind='self_report',
                                       source_turn_id=self.state.current[0], other_person=None, text=None))
        context = self.state.context()
        claim = next(c for c in context['claims'] if c['predicate'] == 'interest')
        self.assertEqual(set(claim) - {'mentions_id'}, {'id', 'subject_id', 'said_by', 'predicate', 'value', 'kind', 'status', 'told'})
        self.assertNotIn('source_track', claim)
        self.assertIn('ago', claim['told'])
        self.assertEqual(set(context['people'][0]), {'id', 'name', 'aliases', 'status'})
        row = {'id': 'x', 'session_id': 's', 'person_id': person, 'source_turn': 't', 'origin': 'human_turn',
               'human_text': 'a' * 500, 'heard_text': 'Bold order.', 'local_time': 'Fri 7 PM', 'at': '2026-10-17',
               'audience_person_id': person, 'status': 'completed',
               'attribution': {'speaker': {'person_id': person, 'state': 'supported', 'display_name': 'Bret'},
                               'audience_basis': 'supported_speaker', 'visible_people': []}}
        trimmed = conversation_memory.public_exchange(row)
        self.assertEqual(set(trimmed), {'id', 'local_time', 'origin', 'human_text', 'heard_text', 'speaker', 'text_truncated'})
        self.assertEqual(len(trimmed['human_text']), conversation_memory.EXCHANGE_TEXT)
        self.assertEqual(trimmed['speaker'], {'person_id': person, 'display_name': 'Bret', 'state': 'supported'})

    def test_recent_opportunities_are_bounded_and_their_words_shortened(self):
        from r3x.output import Delivery
        from r3x.proactive import Initiative, OUTCOME_TEXT, RECENT_OUTCOMES
        from tests.test_proactive import event
        initiative = Initiative(self.state.trace)
        for n in range(RECENT_OUTCOMES + 4):
            heard = Delivery(status='completed', medium='audio'); heard.completed_text = [f'Line {n}. ' + 'x' * 600]
            initiative.finished(event(None, at=100 + n), heard, 100 + n)
        rows = initiative.context(200)['recent_opportunities']
        self.assertEqual(len(rows), RECENT_OUTCOMES)
        self.assertTrue(all(len(r['delivered_text']) <= OUTCOME_TEXT for r in rows))
        self.assertTrue(rows[-1]['delivered_text'].startswith(f'Line {RECENT_OUTCOMES + 3}.'))   # The newest kept.
        self.assertEqual(set(rows[0]), {'event_id', 'kind', 'status', 'heard_complete_phrases', 'target_person_id',
                                        'response', 'delivered_text'})

    def test_every_request_logs_how_big_each_part_of_the_context_was(self):
        from r3x.session import context_sizes
        sizes = context_sizes({'a': {'x': 1}, 'b': 'y' * 50, 'c': object()})
        self.assertEqual(sizes['total'], sizes['a'] + sizes['b'] + sizes['c'])
        self.assertEqual(list(sizes)[:2], ['total', 'b'])


class LongerPlansTests(memory_tests.MemoryFixture):
    def test_a_plan_may_carry_ten_beats_but_not_eleven(self):
        from r3x.agency import MAX_STEPS
        from tests.test_agency_plan import draft
        agency = self.state.agency
        beat = lambda n: {'kind': 'contribute', 'instruction': f'Beat {n}.', 'evidence_ids': [],
                          'wake': {'when': 'quiet', 'seconds': 5}}
        agency.install({**draft(), 'steps': [beat(n) for n in range(MAX_STEPS)]}, '[]', {}, ['contribute'], agency.revision, 100)
        self.assertEqual(len(agency.record['steps']), 10)
        with self.assertRaises(ValueError):
            agency.install({**draft(), 'steps': [beat(n) for n in range(MAX_STEPS + 1)]}, '[]', {}, ['contribute'], agency.revision, 101)


if __name__ == '__main__':
    unittest.main()
