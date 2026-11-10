"""Humor bookkeeping (his bits and how they landed); scripted models and temporary stores only."""
import unittest

from r3x import jokes
from r3x.contracts import ToolCall
from uuid import uuid4
from r3x.contracts import Capture, Transcript
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from tests.test_memory_core import MemoryFixture, observation


class HumorInConversation(MemoryFixture):
    def setup_memory(self):
        self.person = self.enroll('Alex')
        self.clock = 1800000000.
        self.state.wall_clock = lambda: self.clock

    def session(self, name, brain):
        return Session(name, brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                       self.state.trace, self.state)

    def turn(self, session, text):
        cap = Capture(uuid4().hex, session.session_id, bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        return session.process_turn(cap, Transcript(text, 0, True), 0, observation(), ())

    def scripted(self, *replies):
        test = self
        class Brain:
            def __init__(self): self.snapshots = []; self.replies = list(replies)
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                reply = self.replies.pop(0) if self.replies else 'Fair enough.'
                yield reply(snapshot) if callable(reply) else reply
        return Brain()

    def test_a_bit_noted_as_landed_becomes_a_running_gag_with_that_person_only(self):
        self.setup_memory()
        brain = self.scripted('So the salad was just your alibi for the fries.',
                              ToolCall('n', 'note_bit', {'premise': 'his salad alibi', 'reaction': 'landed',
                                                         'text': 'I will be bringing that up again.'}))
        session = self.session('evening', brain)
        self.turn(session, "I ordered a salad, then ate everybody else's fries.")
        self.turn(session, 'Ha! Okay, you got me.')
        noted = self.store.rows("SELECT premise,reaction,person_id FROM joke_record")
        self.assertEqual([(r['premise'], r['reaction'], r['person_id']) for r in noted],
                         [('his salad alibi', 'landed', self.person)])
        # Next visit: the landed bit is a running gag with this person only.
        later = self.scripted('Hello again.')
        self.turn(self.session('next-day', later), 'Morning, Rex.')
        self.assertEqual(later.snapshots[0].context['humor']['running_gags'], ['his salad alibi'])
        self.assertEqual(jokes.context(self.store, None, 'general', self.clock), {})   # Not the general room's.

    def test_forgetting_a_person_removes_their_running_gags(self):
        self.setup_memory()
        with self.store.transaction() as conn:
            jokes.record(conn, 's', self.person, jokes.audience_key(self.person), 'his salad alibi', 'x', 'landed', self.clock)
        self.assertTrue(jokes.context(self.store, self.person, jokes.audience_key(self.person), self.clock))
        with self.store.transaction() as conn:
            self.store.forget(conn, self.person)
        self.assertEqual(self.store.rows('SELECT * FROM joke_record'), [])


if __name__ == '__main__':
    unittest.main()

