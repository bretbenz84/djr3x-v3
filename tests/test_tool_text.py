"""A reply tool's words are spoken as they stream; scripted providers and synthetic audio only."""
import json
import time
import unittest

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Cancellation, Capture, Snapshot, ToolCall, ToolText, Transcript
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.tool_text import TextField
from tests.test_adapters import Client, Response
from tests.test_memory_core import MemoryFixture, observation

LINE = 'Eight shades of beige for one kitchen? That is not a kitchen, that is a "neutral" hostage situation.'


def chunks(text, size=7):
    return [text[i:i + size] for i in range(0, len(text), size)]


class TextFieldRules(unittest.TestCase):
    def test_the_leading_text_decodes_across_chunk_and_escape_boundaries(self):
        arguments = json.dumps({'text': LINE + ' Café \\ done', 'intent_update': None})
        field = TextField()
        out = ''.join(field.feed(c) for c in chunks(arguments, 3))
        self.assertEqual(out, LINE + ' Café \\ done')
        self.assertEqual(field.state, 'done')

    def test_no_text_first_means_nothing_is_streamed(self):
        for arguments in ('{"primitive":"nod","text":"Hi."}', '{"text":null,"x":1}'):
            field = TextField()
            self.assertEqual(''.join(field.feed(c) for c in chunks(arguments, 4)), '')


class AdapterStreaming(unittest.TestCase):
    def events(self, name='respond_with_intent'):
        arguments = json.dumps({'text': LINE, 'intent_update': None, 'invitation_update': None, 'next_opportunity': None})
        return ([{'type': 'response.output_item.added', 'output_index': 0,
                  'item': {'type': 'function_call', 'call_id': 'c', 'name': name, 'arguments': ''}}]
                + [{'type': 'response.function_call_arguments.delta', 'output_index': 0, 'delta': c} for c in chunks(arguments)]
                + [{'type': 'response.output_item.done', 'output_index': 0,
                    'item': {'type': 'function_call', 'call_id': 'c', 'name': name, 'arguments': arguments}},
                   {'type': 'response.completed'}])

    def run_stream(self, stream_tool_text, name='respond_with_intent'):
        brain = OpenAIBrain('fixture')
        brain.client = Client(Response(self.events(name)))
        snapshot = Snapshot('s', 't', 0, (), tools_enabled=True, stream_tool_text=stream_tool_text)
        return list(brain._stream_response({'max_output_tokens': 100}, snapshot, Cancellation()))

    def test_words_stream_ahead_of_the_finished_call(self):
        out = self.run_stream(True)
        texts = [d for d in out if isinstance(d, ToolText)]
        self.assertGreater(len(texts), 3)
        self.assertEqual(''.join(t.text for t in texts), LINE)
        self.assertEqual([t.done for t in texts].count(True), 1)
        self.assertTrue(texts[-1].done)
        self.assertIsInstance(out[-1], ToolCall)
        self.assertEqual(out[-1].arguments['text'], LINE)

    def test_nothing_streams_unless_asked_or_for_other_tools(self):
        self.assertFalse([d for d in self.run_stream(False) if isinstance(d, ToolText)])
        self.assertFalse([d for d in self.run_stream(True, 'remember_followup') if isinstance(d, ToolText)])


class SpeakingWhileStreaming(MemoryFixture):
    def converse(self, call_name='respond_with_intent', extra=None):
        test = self
        class Brain:
            def __init__(self): self.snapshots, self.heard_before_call = [], None
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                if len(self.snapshots) > 1:
                    yield 'A second answer.'
                    return
                for piece in chunks(LINE, 9):
                    yield ToolText('c', call_name, piece)
                yield ToolText('c', call_name, '', True)
                deadline = time.monotonic() + 3
                while session._delivery.first_audio_at is None and time.monotonic() < deadline:
                    time.sleep(.01)          # The words are playing before the call is finished.
                self.heard_before_call = session._delivery.first_audio_at is not None
                yield ToolCall('c', call_name, {'text': LINE, **(extra or {'intent_update': None,
                                                'invitation_update': None, 'next_opportunity': None})})
        brain = Brain()
        session = Session('s', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        self.enroll('Sam')
        cap = Capture('cap', 's', bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        result = session.process_turn(cap, Transcript('I bought eight cans of beige.', 0, True), 0, observation(), ())
        return brain, result

    def test_a_reply_tool_starts_speaking_before_its_call_is_complete_and_says_it_once(self):
        brain, result = self.converse()
        self.assertTrue(brain.snapshots[0].stream_tool_text)
        self.assertTrue(brain.heard_before_call)
        self.assertEqual(' '.join(result.completed_text), LINE)
        self.assertEqual(len(brain.snapshots), 1)

    def test_a_one_sentence_reply_is_spoken_as_soon_as_its_text_is_complete(self):
        global LINE
        saved, LINE = LINE, 'Eight shades of beige is not a palette, Sam.'
        try:
            brain, result = self.converse()
        finally:
            LINE = saved
        self.assertTrue(brain.heard_before_call)
        self.assertEqual(result.completed_text, ['Eight shades of beige is not a palette, Sam.'])

    def test_a_rejected_streamed_reply_is_not_answered_twice(self):
        brain, result = self.converse('respond_with_gesture', {'primitive': 'no-such-gesture', 'target': None,
            'intensity': 'medium', 'timing': 'with_speech', 'invitation_update': None, 'next_opportunity': None})
        self.assertEqual(' '.join(result.completed_text), LINE)
        self.assertEqual(len(brain.snapshots), 1)                     # No "A second answer."
        self.assertTrue(any(r['event'] == 'streamed_reply_tool_rejected' for r in self.state.trace.rows))


if __name__ == '__main__':
    unittest.main()


class StreamedWordsOnFailure(MemoryFixture):
    """October 17 review: once words have streamed to the voice, a response that then breaks the
    protocol (two tool calls) used to cut them off mid-word; only stop words may do that."""
    def test_streamed_words_finish_when_the_response_then_fails(self):
        class SlowSink(SyntheticSink):
            def play(self, chunks, cancel, on_start, visualize=True):
                def paced():
                    for chunk in chunks:
                        time.sleep(.05)
                        yield chunk
                return super().play(paced(), cancel, on_start, visualize)
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                if len(self.snapshots) > 1:
                    yield 'A second answer.'
                    return
                for piece in chunks(LINE, 9):
                    yield ToolText('c', 'respond_with_intent', piece)
                yield ToolText('c', 'respond_with_intent', '', True)
                deadline = time.monotonic() + 3
                while session._delivery.first_audio_at is None and time.monotonic() < deadline:
                    time.sleep(.01)          # The first phrase is playing; the second is queued.
                yield ToolCall('c', 'respond_with_intent', {'text': LINE, 'intent_update': None})
                yield ToolCall('d', 'note_bit', {'premise': 'beige', 'reaction': 'landed', 'text': None})
        brain = Brain()
        session = Session('s', brain, OutputOwner(SyntheticTTS(), SlowSink(), self.state.trace),
                          self.state.trace, self.state)
        self.enroll('Sam')
        cap = Capture('cap', 's', bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        result = session.process_turn(cap, Transcript('I bought eight cans of beige.', 0, True), 0, observation(), ())
        self.assertEqual(' '.join(result.completed_text), LINE)       # Both phrases were heard in full.
        self.assertEqual(result.status, 'failed')                       # The turn still counts as failed.
        self.assertEqual(len(brain.snapshots), 1)                      # Nothing is said twice.
        self.assertTrue(any(r['event'] == 'turn_failed' for r in self.state.trace.rows))


class LeadInTests(MemoryFixture):
    """October 14 (e1f8b2c6): a look or a memory lookup left 6-10 s of silence before the answer."""
    def test_a_lookup_says_a_few_words_first_then_answers_without_repeating_them(self):
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                if snapshot.continuation:
                    yield 'Last time we talked about your porch light.'
                    return
                yield ToolText('r', 'recall_conversations', 'Hang on, checking ')
                yield ToolText('r', 'recall_conversations', 'my memory.', True)
                yield ToolCall('r', 'recall_conversations', {'say_first': 'Hang on, checking my memory.', 'query': None,
                                                             'period': None, 'after': None, 'before': None})
        brain = Brain()
        session = Session('s', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        self.enroll('Sam')
        cap = Capture('cap', 's', bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        result = session.process_turn(cap, Transcript('What did we talk about last time?', 0, True), 0, observation(), ())
        self.assertEqual(result.completed_text, ['Hang on, checking my memory.', 'Last time we talked about your porch light.'])
        notes = [m['content'] for m in brain.snapshots[-1].messages if m.get('role') == 'developer']
        self.assertTrue(any('Already said aloud while checking' in n for n in notes))

    def test_older_tool_arguments_without_a_lead_in_still_validate(self):
        from r3x.semantics import validate
        validate('inspect_view', {'direction': 'current', 'question': 'What is that?', 'next_opportunity': None})
