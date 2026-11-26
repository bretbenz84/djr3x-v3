"""Messages passed on (owner, November 25): "Rex, when Jeremy gets here, tell him dinner's in the fridge."

Production state owner, Session and tool gating with a fake wall clock, scripted model calls, made-up
people and temporary stores. These prove what is kept, offered and counted as passed on, not how
naturally the reply model words the line."""
from dataclasses import replace
import unittest
from uuid import uuid4

from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import OutputOwner, TextOutput
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import grounded_tools
from r3x.session import Session
from tests.test_memory_core import B, C, MemoryFixture, observation
from tests.test_proactive import event


class Scripted:
    """Answers with one tool call and records what it was shown."""
    def __init__(self, call):
        self.call, self.snapshots = call, []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        yield self.call


class RelayFixture(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.clock = 1800000000.
        self.state.wall_clock = lambda: self.clock

    def leave(self, recipient, message, text="Got it, I'll tell him."):
        return self.call('relay_message', {'action': 'leave', 'recipient': recipient, 'message': message,
                                           'message_id': None, 'source_turn_id': self.state.current[0], 'text': text})

    def person(self, name):
        return self.store.resolve(name)[0]['id']

    def left(self):
        """Bret leaves Jeremy a message; returns (bret, jeremy, message id)."""
        bret = self.enroll('Bret')
        jeremy = self.enroll('Jeremy', B)
        self.begin("When Jeremy gets here, tell him dinner's in the fridge.")
        result = self.leave('Jeremy', "Dinner's in the fridge.")
        self.assertEqual(result.outcome, 'ok', result)
        return bret, jeremy, result.data['message_id']

    def greet(self, person, call, output=None):
        brain = Scripted(call)
        session = Session('later', brain, output or OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        self.addCleanup(session.close)
        result = session.process_opportunity(replace(event(person), session_id='later'), 0, lambda: True)
        return brain.snapshots[0].context, result

    def status(self, message_id):
        return self.store.rows('SELECT status FROM relay_messages WHERE id=?', (message_id,))[0]['status']


class RelayStateTests(RelayFixture):
    def test_a_message_is_kept_for_the_recipient_with_who_left_it_and_never_as_a_fact(self):
        bret, jeremy, message_id = self.left()
        row = self.store.rows('SELECT * FROM relay_messages')[0]
        self.assertEqual((row['recipient_id'], row['left_by'], row['message'], row['status']),
                         (jeremy, bret, "Dinner's in the fridge.", 'waiting'))
        self.assertEqual(row['source_text'], "When Jeremy gets here, tell him dinner's in the fridge.")
        self.assertEqual(self.store.claims(include_candidates=True), [])
        # Bret sees what he left, so he can ask about it or take it back.
        shown = self.state.relay_context({'speaker': {'speaker_id': bret}})
        self.assertEqual(shown['left_by_speaker'][0]['for'], 'Jeremy')
        self.assertNotIn('for_someone_here', shown)

    def test_someone_not_met_yet_gets_a_name_only_record_and_the_speaker_cannot_message_himself(self):
        self.enroll('Bret')
        self.begin('Tell my sister Dana I said hi when you meet her.')
        self.assertEqual(self.leave('Dana', 'Bret says hi.').outcome, 'ok')
        self.assertTrue(self.person('Dana'))
        self.begin('Bret, remind yourself... no wait, tell Bret to call mom.')
        refused = self.leave('Bret', 'Call mom.')
        self.assertEqual((refused.outcome, refused.reason), ('rejected', 'MessageForTheSpeakerThemself'))

    def test_only_whoever_left_it_takes_it_back(self):
        bret, jeremy, message_id = self.left()
        self.begin('Never mind about the fridge thing.', C)
        stranger = self.call('relay_message', {'action': 'cancel', 'recipient': None, 'message': None,
                                               'message_id': message_id, 'source_turn_id': self.state.current[0],
                                               'text': None})
        self.assertEqual(stranger.reason, 'OnlyWhoeverLeftItCanTakeItBack')
        self.begin('Never mind, do not tell Jeremy about dinner.')
        taken = self.call('relay_message', {'action': 'cancel', 'recipient': None, 'message': None,
                                            'message_id': message_id, 'source_turn_id': self.state.current[0],
                                            'text': 'Okay, forgotten.'})
        self.assertEqual(taken.outcome, 'ok', taken)
        self.assertEqual(self.status(message_id), 'cancelled')
        self.assertIsNone((self.state.relay_context({'faces': [{'identity': jeremy}]}) or {}).get('for_someone_here'))

    def test_forgetting_either_person_removes_the_message(self):
        bret, jeremy, _ = self.left()
        with self.store.transaction() as conn:
            self.store.forget(conn, jeremy)
        self.assertEqual(self.store.rows('SELECT * FROM relay_messages'), [])
        self.enroll('Jeremy', C)
        self.begin("Tell Jeremy dinner's in the fridge.")   # Bret's voice again.
        self.assertEqual(self.leave('Jeremy', "Dinner's in the fridge.").outcome, 'ok')
        with self.store.transaction() as conn:
            self.store.forget(conn, bret)
        self.assertEqual(self.store.rows('SELECT * FROM relay_messages'), [])


class RelayDeliveryTests(RelayFixture):
    def test_his_arrival_offers_it_and_the_heard_line_passes_it_on_once(self):
        bret, jeremy, message_id = self.left()
        line = "Jeremy! Message from Bret: dinner's in the fridge."
        context, result = self.greet(jeremy, ToolCall('d', 'deliver_message', {'message_id': message_id, 'text': line}))
        self.assertEqual(result.status, 'completed', result)
        offered = context['messages']['for_someone_here'][0]
        self.assertEqual((offered['message_id'], offered['from'], offered['for']), (message_id, 'Bret', 'Jeremy'))
        self.assertEqual(result.completed_text, [line])
        self.assertEqual(self.status(message_id), 'delivered')
        self.assertFalse((self.state.relay_context({'faces': [{'identity': jeremy}]}) or {}).get('for_someone_here'))

    def test_his_voice_alone_is_enough_when_he_talks_to_rex(self):
        bret, jeremy, message_id = self.left()
        line = "Hey Jeremy. Bret says dinner's in the fridge."
        brain = Scripted(ToolCall('d', 'deliver_message', {'message_id': message_id, 'text': line}))
        session = Session('s2', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        self.addCleanup(session.close)
        capture = Capture(uuid4().hex, 's2', bytes(96000), 300., 303., 303., voiced_intervals=((0., 3.),))
        result = session.process_turn(capture, Transcript("Hey Rex, what's up?", -.01, True), 0, observation(B), ())
        self.assertEqual(brain.snapshots[0].context['messages']['for_someone_here'][0]['message_id'], message_id)
        self.assertEqual(result.completed_text, [line])
        self.assertEqual(self.status(message_id), 'delivered')

    def test_a_line_never_heard_leaves_it_waiting(self):
        bret, jeremy, message_id = self.left()
        self.greet(jeremy, ToolCall('d', 'deliver_message', {'message_id': message_id, 'text': 'Message for you!'}),
                   output=TextOutput(self.state.trace))
        self.assertEqual(self.status(message_id), 'waiting')

    def test_it_is_not_passed_to_someone_else(self):
        bret, jeremy, message_id = self.left()
        joy = self.enroll('Joy', C)
        context, result = self.greet(joy, ToolCall('d', 'deliver_message', {'message_id': message_id, 'text': 'Psst.'}))
        self.assertNotIn('for_someone_here', context.get('messages') or {})
        self.assertEqual(self.status(message_id), 'waiting')


class RelayToolTests(unittest.TestCase):
    def names(self, **context):
        return {d['name'] for d in grounded_tools({'event_id': 'e', 'source_turn_id': 't', **context})}

    def test_leaving_one_is_a_persons_request_and_passing_one_on_needs_one_waiting(self):
        self.assertIn('relay_message', self.names())
        self.assertNotIn('relay_message', self.names(trigger='visual_arrival'))
        self.assertNotIn('deliver_message', self.names())
        waiting = {'messages': {'for_someone_here': [{'message_id': 'msg_1'}]}}
        for context in ({}, {'trigger': 'visual_arrival'}, {'trigger': 'conversation_lull'}):
            self.assertIn('deliver_message', self.names(**context, **waiting))
        offered = next(d for d in grounded_tools({'event_id': 'e', 'source_turn_id': 't', **waiting})
                       if d['name'] == 'deliver_message')
        self.assertEqual(offered['parameters']['properties']['message_id']['enum'], ['msg_1'])
