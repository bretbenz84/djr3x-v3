"""Humor bookkeeping and the writer's room; scripted models and temporary stores only."""
import json
import threading
import time
import unittest

from r3x import jokes
from r3x.contracts import ToolCall
from r3x.jokes import JokeRoom, WRITE_EVERY_S, overlaps
from uuid import uuid4
from r3x.contracts import Capture, Transcript
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from tests.test_memory_core import MemoryFixture, observation


class Clock:
    def __init__(self): self.now = 1000.
    def __call__(self): return self.now


class Trace:
    def __init__(self): self.rows = []
    def emit(self, event, **fields): self.rows.append({'event': event, **fields})


class Writer:
    def __init__(self, items=None, gate=None):
        self.items = items if items is not None else [{'premise': 'his salad alibi',
                                                       'line': 'So the salad was just your alibi for the fries.'}]
        self.gate, self.facts = gate, []
    def write(self, facts, cancel):
        self.facts.append(facts)
        if self.gate: self.gate.wait(5)
        return [dict(i) for i in self.items]


def wait_for(room):
    for _ in range(200):
        if room.thread is None or not room.thread.is_alive(): return
        time.sleep(.005)


class JokeRoomRules(unittest.TestCase):
    def test_material_is_paced_fresh_for_its_audience_and_used_once(self):
        clock, trace = Clock(), Trace()
        room = JokeRoom(Writer(), trace, clock)
        self.assertTrue(room.ask('{}', 'person:bret', 1)); wait_for(room)
        self.assertFalse(room.ask('{}', 'person:bret', 2))               # Not again this soon.
        self.assertEqual(room.offer('person:bret', 2)[0]['premise'], 'his salad alibi')
        self.assertIsNone(room.offer('general', 2))                       # Written for someone else.
        self.assertIsNone(room.offer('person:bret', 4))                   # Two turns later: stale.
        self.assertEqual(room.used('Ha. So the salad was just your alibi for the fries, Bret.')['premise'],
                         'his salad alibi')
        self.assertIsNone(room.offer('person:bret', 2))                   # Used up.
        clock.now += WRITE_EVERY_S + 1
        self.assertTrue(room.ask('{}', 'person:bret', 3))

    def test_a_failed_writer_never_reaches_the_conversation(self):
        class Broken:
            def write(self, facts, cancel): raise RuntimeError('boom')
        trace = Trace()
        room = JokeRoom(Broken(), trace, Clock())
        room.ask('{}', 'general', 1); wait_for(room)
        self.assertIsNone(room.offer('general', 1))
        self.assertEqual(trace.rows[-1]['event'], 'joke_writer_failed')

    def test_use_is_judged_on_rex_s_own_wording(self):
        self.assertTrue(overlaps('Well, the salad was your alibi all along.', 'So the salad was your alibi for the fries.'))
        self.assertFalse(overlaps('How was the drive home?', 'So the salad was your alibi for the fries.'))


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

    def test_a_used_bit_is_recorded_and_a_landed_one_becomes_a_running_gag(self):
        self.setup_memory()
        room = JokeRoom(Writer(), self.state.trace, self.state.trace.clock)
        brain = self.scripted('Bold order.', 'So the salad was just your alibi for the fries.',
                              ToolCall('n', 'note_bit', {'premise': 'his salad alibi', 'reaction': 'landed',
                                                         'text': 'I will be bringing that up again.'}))
        session = self.session('evening', brain); session.joke_room = room
        self.turn(session, 'I ordered a salad.')
        wait_for(room)
        self.turn(session, 'Then I ate everybody else\'s fries.')
        self.assertEqual(brain.snapshots[1].context['joke_material'][0]['premise'], 'his salad alibi')
        told = self.store.rows("SELECT premise,reaction,person_id FROM joke_record")
        self.assertEqual([(r['premise'], r['reaction'], r['person_id']) for r in told],
                         [('his salad alibi', 'told', self.person)])
        self.turn(session, 'Ha! Okay, you got me.')
        self.assertIn('his salad alibi', brain.snapshots[2].context['humor']['bits_told_lately'])
        # Next visit: the landed bit is a running gag with this person only.
        later = self.scripted('Hello again.')
        self.turn(self.session('next-day', later), 'Morning, Rex.')
        self.assertEqual(later.snapshots[0].context['humor']['running_gags'], ['his salad alibi'])
        self.assertEqual(jokes.context(self.store, None, 'general', self.clock), {})   # Not the general room's.

    def test_a_reply_never_waits_for_the_writer(self):
        self.setup_memory()
        gate = threading.Event()
        room = JokeRoom(Writer(gate=gate), self.state.trace, self.state.trace.clock)
        session = self.session('evening', self.scripted('Bold order.', 'Sure.')); session.joke_room = room
        self.turn(session, 'I ordered a salad.')
        started = time.monotonic()
        result = self.turn(session, 'With extra croutons.')                   # The writer is still busy.
        self.assertLess(time.monotonic() - started, 2.)
        self.assertEqual(result.completed_text, ['Sure.'])
        gate.set(); wait_for(room)

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


class WriterSummary(unittest.TestCase):
    def test_the_writer_gets_the_hour_what_is_around_and_what_is_known_about_them(self):
        # October 14 (e1f8b2c6): from the conversation alone it wrote "Brett checking in".
        picture = {'local_time': 'Wednesday evening, 6:24 PM, October 14', 'pet': {'seen': 'just now', 'in_view': True},
                   'things_seen': [{'thing': 'pet', 'seen': 'just now', 'where': 'left'}], 'people_here': [{'name': 'Bret'}]}
        facts = json.loads(jokes.summary([{'role': 'user', 'content': 'Hi.'}], picture, [], {},
                                         ['likes: astrophotography']))
        self.assertEqual(facts['local_time'], picture['local_time'])
        self.assertEqual(facts['known_about_them'], ['likes: astrophotography'])
        self.assertTrue(facts['around_them']['pet']['in_view'])
        self.assertIn('never compliments', jokes.WRITER_INSTRUCTIONS)
