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

    def test_rolling_windows_are_one_second_and_never_mix_with_standing(self):
        # October 19: 5 s summaries could not show whether a shouted stop reached the
        # detector while the wheels ran.
        rows=[];clock=[0.]
        trace=SimpleNamespace(clock=lambda:clock[0],emit=lambda event,**data:rows.append({'event':event,**data}))
        health=InputHealth(trace,'Synthetic microphone')
        health.observe(Frame(bytes(1024),0,.032),False,False)
        clock[0]=2
        health.observe(Frame(b'\x00\x40'*512,1.968,2),True,False,vad_probability=.9,wheels_moving=True)
        self.assertEqual([r['wheels_moving'] for r in rows],[False])   # The still part closes first.
        clock[0]=3.1
        health.observe(Frame(bytes(1024),3.068,3.1),False,False,wheels_moving=True)
        self.assertEqual(rows[1]['wheels_moving'],True)
        self.assertEqual((rows[1]['speech_frames'],rows[1]['peak_vad_probability']),(1,.9))
