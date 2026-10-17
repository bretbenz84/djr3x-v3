"""A spoken stop halts a drive, is always heard, and is confirmed; synthetic audio, simulated base.

October 16 (session 3af18a12): on the way to the microwave the owner said "Rex, stop moving."
and then "stop" several times. The first was transcribed but missed the strict stop list, so it
waited behind the two-minute drive like conversation; speech recognition then blocked on the full
conversation queue and every later capture was dropped untranscribed. Nothing was acknowledged.
"""
import threading
import time
import unittest

from r3x.contracts import Capture, Transcript
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session, is_stop_command
from tests import test_drive_session
from tests.test_drive_session import drive_call, step
from tests.test_memory_core import MemoryFixture
from tests.test_voice_core import Brain, Trace


class StopPhraseTests(unittest.TestCase):
    def test_halts_said_while_he_moves_are_stops(self):
        for text in ('Rex, stop moving.', 'Stop right there!', 'Stop it!', "Don't move!", 'Hold it.',
                     'Whoa, stop driving.', 'Stop that, Rex.', 'Stop, stop, stop!'):
            self.assertTrue(is_stop_command(text), text)

    def test_ordinary_sentences_with_those_words_are_not(self):
        for text in ('Moving on.', 'Is that right?', 'Right there.', 'It.', 'Wait.', 'Stop by the store later.',
                     "No, don't stop.", 'Keep moving.', 'Hold on to that thought.'):
            self.assertFalse(is_stop_command(text), text)


class HeardWhileBusyTests(unittest.TestCase):
    def test_a_stop_is_transcribed_and_obeyed_while_the_conversation_queue_is_full(self):
        trace = Trace()
        session = Session('s', Brain(), OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace)
        self.addCleanup(session.close)
        said = ['You are about to hit the island.', 'Stop!']
        class ASR:
            def transcribe(self, *args): return Transcript(said.pop(0), -.01, True)
        runtime = VoiceRuntime(None, None, ASR(), session, trace)
        for i in range(3):    # A long drive: the conversation worker is busy and its queue is full.
            runtime.turns.put_nowait((Capture(f'old{i}', 's', bytes(1024), 90, 91, 91.5), Transcript('Okay.', -.7, False), 0, None))
        for i in range(2):
            runtime.enqueue(runtime.captures, (Capture(f'c{i}', 's', bytes(1024), 98 + i, 99 + i, 99.6 + i), 0), 'captures')
        worker = threading.Thread(target=runtime._asr_loop); worker.start()
        try:
            deadline = time.monotonic() + 2
            while session.revision == 0 and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(session.revision, 1)                       # The stop cancelled.
            self.assertEqual([w[1].text for w in runtime.waiting_turns], ['You are about to hit the island.'])
        finally:
            runtime.stop.set(); worker.join(2)

    def test_waiting_transcripts_are_bounded_unclear_sounds_first(self):
        trace = Trace()
        session = Session('s', Brain(), OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace)
        self.addCleanup(session.close)
        runtime = VoiceRuntime(None, None, None, session, trace)
        for i in range(3):
            runtime.turns.put_nowait(('busy', i))
        from r3x.runtime import WAITING_TURNS
        for i in range(WAITING_TURNS + 1):
            trusted = i != 2
            runtime.enqueue(runtime.turns, (Capture(f'w{i}', 's', bytes(1024), 98, 99, 99.6),
                                            Transcript(f'line {i}', -.01, trusted), 0, None), 'turns')
        self.assertEqual(len(runtime.waiting_turns), WAITING_TURNS)
        self.assertNotIn('line 2', [w[1].text for w in runtime.waiting_turns])
        runtime.turns.get_nowait()
        runtime.flush_turns()                                          # Order is kept as room appears.
        self.assertEqual(runtime.turns.queue[-1][1].text, 'line 0')


class StopConfirmedTests(MemoryFixture):
    setup_drive = test_drive_session.DriveSessionTests.setup_drive
    turn = test_drive_session.DriveSessionTests.turn

    def stop_mid_drive(self, **cancel):
        self.setup_drive(drive_call(step('move', meters=1.5)))
        result = {}
        worker = threading.Thread(target=lambda: result.update(d=self.turn('Drive forward.')))
        worker.start()
        deadline = time.monotonic() + 3
        while not any(c['cmd'] == 'move' for c in self.fake.commands) and time.monotonic() < deadline:
            time.sleep(.005)
        self.session.cancel(**cancel)
        worker.join(5)
        return result['d']

    def test_a_stop_that_halts_the_wheels_is_said_aloud(self):
        delivery = self.stop_mid_drive()
        self.assertEqual(delivery.status, 'interrupted')
        self.assertLess(self.fake.x, 1.4)
        row = next(r for r in self.state.trace.rows if r['event'] == 'stop_acknowledged')
        self.assertTrue(row['heard'])
        said = [m['content'] for m in self.session.history.messages() if m.get('role') == 'assistant']
        self.assertEqual(said[-1], 'Stopped.')

    def test_be_quiet_halts_without_a_word(self):
        self.stop_mid_drive(quiet=True)
        self.assertFalse(any(r['event'] == 'stop_acknowledged' for r in self.state.trace.rows))
