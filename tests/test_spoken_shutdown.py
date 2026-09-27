"""Exact trusted shutdown bypasses generation; surrounding discussion cannot exit."""
import threading
import unittest
from r3x.contracts import Capture,Transcript
from r3x.runtime import VoiceRuntime
from r3x.session import Session
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink,SyntheticTTS
from tests.test_voice_core import Brain,Trace


class ShutdownTests(unittest.TestCase):
    def runtime(self,text,trusted=True):
        trace=Trace();brain=Brain()
        session=Session('s',brain,OutputOwner(SyntheticTTS(),SyntheticSink(),trace),trace)
        self.addCleanup(session.close)
        class ASR:
            def transcribe(self,*args): return Transcript(text,-.01,trusted)
        runtime=VoiceRuntime(None,None,ASR(),session,trace)
        runtime.enqueue(runtime.captures,(Capture('c','s',bytes(1024),98,99,99.6),0),'captures')
        return runtime,brain

    def test_trusted_shutdown_stops_runtime_without_model_or_queued_reply(self):
        for text in ('Shut down.','Shutdown!','Rex, shut down.'):
            runtime,brain=self.runtime(text)
            worker=threading.Thread(target=runtime._asr_loop);worker.start()
            self.assertTrue(runtime.stop.wait(1));worker.join(1)
            self.assertFalse(worker.is_alive())
            self.assertTrue(runtime.turns.empty())
            self.assertEqual(brain.calls,0)
            self.assertEqual(runtime.session.revision,1)
            self.assertTrue(any(r['event']=='shutdown_requested' for r in runtime.trace.rows))

    def test_untrusted_and_discussion_remain_conversation(self):
        for text,trusted in (('shut down',False),('Do not shut down.',True),('What happens when you shut down?',True)):
            runtime,_=self.runtime(text,trusted)
            worker=threading.Thread(target=runtime._asr_loop);worker.start()
            try:
                row=runtime.turns.get(timeout=1)
                self.assertEqual(row[1].text,text)
                self.assertFalse(runtime.stop.is_set())
            finally:
                runtime.stop.set();worker.join(1)

    def test_spoken_exit_drains_workers_then_normal_owner_cleanup_parks_robot(self):
        import time
        from unittest.mock import patch
        from r3x.robot_servos import RobotServos
        from r3x.servo_poses import PARK
        from tests.test_servos import Clock,PulsePort
        clock,port=Clock(),PulsePort()
        robot=RobotServos('fixture',open_port=lambda:port,clock=clock,wait=clock.wait)
        robot.owner.wait=clock.wait
        robot.start();self.addCleanup(robot.close)
        runtime,brain=self.runtime('Shut down.')
        class Mic:
            cancelled=False
            def read(self): time.sleep(.001)
            def cancel(self): self.cancelled=True
        runtime.mic=Mic();runtime.robot=robot
        with patch('sys.stdin.isatty',return_value=False):
            runtime.run()
        self.assertTrue(runtime.mic.cancelled)
        self.assertTrue(runtime.session.closed)
        self.assertFalse(any(w.is_alive() for w in runtime.workers))
        robot.close()  # App's existing provider cleanup, after runtime returns.
        self.assertEqual({ch:port.pose[ch] for ch,_ in PARK},dict(PARK))
        self.assertTrue(port.closed)
        self.assertEqual(brain.calls,0)
