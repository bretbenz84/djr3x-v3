"""November 15 (c36ecba3): told his new wheels were done, Rex saved the news and showed nothing. News he
saves can carry a feeling, a refused save still shows it, and an expression chosen for a line whose
words went ahead starts beside them instead of after them. Scripted replies, synthetic voice, a
simulated gesture adapter and a fake talking motion: these prove the contracts, not what the reply
model chooses or how any of it looks on the robot."""
import threading
import time
import unittest
from queue import Queue

from r3x.contracts import Cancellation, ToolCall
from r3x.output import Delivery, OutputOwner
from r3x.performance import Gesture, Performance, SimulatedGesture
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import SPOKEN_RESULT_TOOLS, TOOLS, validate
from r3x.servo_poses import HEAD_EXPRESSIONS
from r3x.trace import Trace
from tests import test_proactive_core
from tests.test_memory_core import MemoryFixture, C, observation


class SavedNewsTests(MemoryFixture):
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    turn = test_proactive_core.InitiativeCoreTests.turn

    def output(self, body=True):
        gestures = None
        if body:
            gestures = SimulatedGesture(self.state.trace.clock); gestures.start()
        return OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, gestures=gestures)

    def save(self, snapshot, **more):
        return ToolCall('save', 'record_claim', dict(subject_id=None, subject_reference='Bret', predicate='finished',
            value='the new wheels', kind='third_party', source_turn_id=snapshot.context['source_turn_id'], **more))

    def test_every_save_that_carries_his_reply_can_carry_a_feeling(self):
        for definition in TOOLS:
            if definition['name'] in SPOKEN_RESULT_TOOLS:
                spec = definition['parameters']['properties']['expression']
                self.assertEqual(spec['enum'], [*HEAD_EXPRESSIONS, None])
                self.assertIn('expression', definition['parameters']['required'])   # Strict tools list every field.
        fields = dict(claim_id='c', value='v', source_turn_id='t', text='Fine.')
        validate('correct_claim', fields)                                   # Older callers leave it out.
        validate('correct_claim', {**fields, 'expression': None})
        validate('correct_claim', {**fields, 'expression': 'surprised'})
        with self.assertRaises(ValueError):
            validate('correct_claim', {**fields, 'expression': 'cartwheel'})

    def test_news_he_saves_shows_its_feeling_as_the_line_is_said(self):
        self.enroll()
        requests = []
        class News:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                yield self.save(snapshot, text='New wheels? For me?', expression='surprised')
        self.setup_core(News(), self.output())
        result = self.turn('Bret finished your new wheels.', observation(C))
        self.assertEqual(result.completed_text, ['New wheels? For me?'])
        self.assertEqual(len(requests), 1)                                  # Still one generation.
        self.assertEqual([(g['primitive'], g['intensity'], g['timing'], g['status']) for g in result.gestures],
                         [('surprised', 'normal', 'with_speech', 'simulated')])

    def plain(self, expression, body):
        self.enroll()
        class News:
            def reply(_, snapshot, cancel):
                yield self.save(snapshot, text='Noted, new wheels.', expression=expression)
        self.setup_core(News(), self.output(body))
        result = self.turn('Bret finished your new wheels.', observation(C))
        self.assertEqual((result.completed_text, result.gestures), (['Noted, new wheels.'], []))

    def test_a_plain_save_shows_nothing(self):
        self.plain(None, True)

    def test_a_robot_with_no_body_just_says_the_line(self):
        self.plain('surprised', False)

    def test_a_refused_save_still_shows_what_he_felt_and_says_the_line_that_replaces_it(self):
        person = self.enroll()
        requests = []
        class Refused:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield ToolCall('save', 'record_claim', dict(subject_id=person, subject_reference=None,
                        predicate='built', value='new wheels for Rex', kind='self_report',
                        source_turn_id=snapshot.context['source_turn_id'], text='New wheels? Saved forever.',
                        expression='surprised'))
                else:
                    yield 'New wheels? For me? Roll me out.'
        self.setup_core(Refused(), self.output())
        result = self.turn('I made new wheels for you.', observation(C))   # A voice like nobody known: refused.
        self.assertEqual(' '.join(result.completed_text), 'New wheels? For me? Roll me out.')
        self.assertEqual([(g['primitive'], g['status']) for g in result.gestures], [('surprised', 'simulated')])


class Sway:
    """A line's talking motion: counts its starts and stops."""
    def __init__(self):
        self.events = []
    def start(self, turn_id, cancel, visor_only=False):
        self.events.append('start')
    def stop(self, finished=False):
        self.events.append('stop')
    def cancel(self):
        pass


class HeldSink(SyntheticSink):
    """A line that keeps playing until the test lets it end."""
    def __init__(self):
        self.playing, self.end = threading.Event(), threading.Event()
    def play(self, chunks, cancel, on_start, visualize=True):
        def began(at):
            on_start(at)
            self.playing.set()
        receipt = super().play(chunks, cancel, began, visualize)
        self.end.wait(5)
        return receipt


class BesideTheLineTests(unittest.TestCase):
    def test_an_expression_starts_beside_a_line_already_playing_and_the_sway_takes_over_again(self):
        trace = Trace('feelings', display=lambda row: None)
        gestures, sway, sink = SimulatedGesture(trace.clock), Sway(), HeldSink()
        gestures.start()
        output = OutputOwner(SyntheticTTS(), sink, trace, gestures=gestures, speech_motion=sway)
        phrases, cancel, delivery = Queue(), Cancellation(), Delivery()
        worker = threading.Thread(target=output.run, args=(phrases, cancel, 'turn', delivery), daemon=True)
        worker.start()
        phrases.put('Two million? That buys a very nice used shuttle.')   # Its words streamed ahead.
        self.assertTrue(sink.playing.wait(5))
        self.assertEqual(sway.events, ['start'])
        # The rest of the tool call arrives while the line is still being said.
        output.express(Performance('', Gesture('g', 'surprised', 'unfocused', 'normal', trace.clock() + 30),
                                   'with_speech', lambda: True, lambda: None), cancel, 'turn', delivery)
        deadline = time.monotonic() + 5
        while not delivery.gestures and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual([(g['primitive'], g['status']) for g in delivery.gestures], [('surprised', 'simulated')])
        self.assertEqual(delivery.completed_text, [])                      # Shown while the line was still playing.
        while sway.events != ['start', 'stop', 'start'] and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(sway.events, ['start', 'stop', 'start'])           # It gave way, then took over again.
        sink.end.set()
        phrases.put(None)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(sway.events, ['start', 'stop', 'start', 'stop'])   # And stopped when the line ended.
        self.assertEqual(delivery.status, 'completed')
        self.assertTrue(output._motion_lock.acquire(blocking=False))        # Nothing left holding the body.
        output._motion_lock.release()

    def test_an_expression_chosen_before_the_voice_starts_leads_the_line(self):
        trace = Trace('feelings', display=lambda row: None)
        gestures, sway = SimulatedGesture(trace.clock), Sway()
        gestures.start()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace, gestures=gestures, speech_motion=sway)
        phrases, cancel, delivery = Queue(), Cancellation(), Delivery()
        output.express(Performance('', Gesture('g', 'surprised', 'unfocused', 'normal', trace.clock() + 30),
                                   'with_speech', lambda: True, lambda: None), cancel, 'turn', delivery)
        phrases.put('New wheels? For me?'); phrases.put(None)
        output.run(phrases, cancel, 'turn', delivery)
        self.assertEqual([g['status'] for g in delivery.gestures], ['simulated'])
        self.assertEqual(delivery.completed_text, ['New wheels? For me?'])
        self.assertEqual(sway.events.count('start'), sway.events.count('stop'))
        self.assertTrue(output._motion_lock.acquire(blocking=False))
        output._motion_lock.release()


if __name__ == '__main__':
    unittest.main()
