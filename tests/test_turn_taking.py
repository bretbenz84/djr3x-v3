"""Production turn ordering and listening; scripted semantics, synthetic audio."""
from uuid import uuid4

from r3x.contracts import Capture, Transcript, Playback, ToolCall
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from tests.test_memory_core import MemoryFixture, observation
from tests.test_voice_core import Brain


class ClockSink:
    def __init__(self,trace): self.trace=trace
    def play(self,chunks,cancel,on_start):
        data=list(chunks);cancel.check()
        start=self.trace.clock();on_start(start)
        self.trace.at+=5
        return Playback(sum(len(chunk)//2 for chunk in data),start,self.trace.clock(),0)
    def cancel(self): pass


class TurnTakingTests(MemoryFixture):
    def session(self):
        return Session('test',Brain('Was it power or overheating?'),
            OutputOwner(SyntheticTTS(),ClockSink(self.state.trace),self.state.trace),self.state.trace,self.state)

    def turn(self,session,text,end):
        capture=Capture(uuid4().hex,'test',bytes(96000),end-3,end,end,voiced_intervals=((0.,3.),))
        return session.process_turn(capture,Transcript(text,0,True),0,observation())

    def test_queued_acknowledgment_is_marked_as_predating_the_question_and_retained(self):
        session=self.session();self.turn(session,'The router kept restarting.',99)
        class Listen(Brain):
            def reply(self,snapshot,cancel):
                self.snapshots.append(snapshot)
                yield ToolCall('listen','remain_silent',{'reason':'no_response_needed'})
        brain=Listen();session.brain=brain
        result=self.turn(session,'Mm-hmm.',99.5)
        timing=brain.snapshots[0].context['reply_timing']
        self.assertTrue(timing['human_preceded_previous_reply'])
        self.assertEqual(timing['started_at'],100)
        self.assertEqual(timing['finished_at'],105)
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(result.completed_text,[])
        self.assertEqual(len(brain.snapshots),1)
        self.assertTrue(any('Mm-hmm.'==r['text'] for r in self.store.rows('SELECT text FROM turns')))
        self.assertEqual(session.proactive_hold_until,float('-inf'))
        self.assertIn('human_preceded_previous_reply',str(brain.snapshots[0].messages))

    def test_actual_reply_after_playback_is_not_labeled_a_queued_acknowledgment(self):
        session=self.session();self.turn(session,'The router kept restarting.',99)
        brain=Brain('Only the workshop, understood.');session.brain=brain
        self.state.trace.at=110
        result=self.turn(session,'Only the workshop was affected.',109)
        self.assertFalse(brain.snapshots[0].context['reply_timing']['human_preceded_previous_reply'])
        self.assertEqual(result.status,'completed')
        self.assertEqual(brain.calls,1)
