import unittest
from types import SimpleNamespace
from r3x.capture import Frame
from r3x.input_health import InputHealth


class InputHealthTests(unittest.TestCase):
    def test_signal_and_detector_are_reported_separately_without_audio(self):
        rows=[];clock=[0.]
        trace=SimpleNamespace(clock=lambda:clock[0],emit=lambda event,**data:rows.append({'event':event,**data}))
        health=InputHealth(trace,'Synthetic microphone')
        health.observe(Frame(bytes(1024),0,.032),False,False,vad_probability=.12)
        clock[0]=5
        health.observe(Frame(b'\x00\x40'*512,4.968,5),False,False,vad_probability=.42)
        self.assertAlmostEqual(rows[0]['peak_dbfs'],-6.0206,places=3)
        self.assertEqual(rows[0]['speech_frames'],0)
        self.assertEqual(rows[0]['peak_vad_probability'],.42)
        self.assertNotIn('pcm',rows[0])
        clock[0]=10
        health.observe(Frame(bytes(1024),9.968,10),False,False)
        self.assertIsNone(rows[1]['peak_dbfs'])
        self.assertIsNone(rows[1]['peak_vad_probability'])
        self.assertEqual(rows[1]['frames'],1)
