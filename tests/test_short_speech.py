"""Sparse VAD evidence must reach ASR even while a model request is waiting."""
import threading
import unittest
from r3x.capture import Endpoint,Frame
from r3x.contracts import Capture,Transcript
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.session import Session
from r3x.scenario import SyntheticTTS,SyntheticSink
from tests.test_voice_core import Trace


def short_capture(speech_frames):
    endpoint=Endpoint('s')
    result=None
    for i in range(8+speech_frames+39):
        frame=Frame(bytes([i%256])*1024,98+i*.032,98+(i+1)*.032)
        item=endpoint.push(frame,8<=i<8+speech_frames)
        if item: result=item
    return result


class ShortSpeechTests(unittest.TestCase):
    def test_one_to_five_vad_frames_retain_preroll_speech_and_endpoint_tail(self):
        for count in range(1,6):
            capture=short_capture(count)
            self.assertIsNotNone(capture,count)
            self.assertEqual(capture.started_at,98)
            self.assertAlmostEqual(sum(b-a for a,b in capture.voiced_intervals),count*.032)
            self.assertGreaterEqual(capture.ended_at-capture.speech_ended_at,.7)
            self.assertEqual(capture.pcm[:9*1024],b''.join(bytes([i])*1024 for i in range(9)))
        self.assertIsNone(short_capture(0))

    def test_short_shutdown_reaches_asr_while_conversation_worker_is_blocked(self):
        entered,release=threading.Event(),threading.Event()
        trace=Trace()
        class WaitingBrain:
            def reply(self,snapshot,cancel):
                entered.set()
                release.wait(3)
                cancel.check()
                yield 'This reply must never play.'
        session=Session('s',WaitingBrain(),OutputOwner(SyntheticTTS(),SyntheticSink(),trace),trace)
        self.addCleanup(session.close)
        busy=threading.Thread(target=lambda:session.process_turn(
            Capture('old','s',bytes(1024),95,96,97),Transcript('Hello.',-.01,True),0))
        seen=[]
        class ASR:
            def transcribe(self,capture,cancel):
                seen.append(capture)
                return Transcript('Shut down.',-.01,True)
        runtime=VoiceRuntime(None,None,ASR(),session,trace)
        worker=threading.Thread(target=runtime._asr_loop)
        busy.start()
        try:
            self.assertTrue(entered.wait(1))
            self.assertTrue(session._processing.locked())
            self.assertFalse(session.output.echo_risk())
            capture=short_capture(1)
            self.assertIsNotNone(capture)
            runtime.enqueue(runtime.captures,(capture,session.revision),'captures')
            worker.start()
            self.assertTrue(runtime.stop.wait(1))
            self.assertEqual(seen,[capture])
            self.assertTrue(runtime.turns.empty())
            self.assertTrue(any(r['event']=='shutdown_requested' for r in trace.rows))
        finally:
            runtime.stop.set();release.set()
            if worker.ident:worker.join(2)
            busy.join(2)
        self.assertFalse(busy.is_alive())
        self.assertFalse(any(r['event']=='playback_started' for r in trace.rows))
