import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from r3x.adapters.audio import Microphone
from r3x.settings import Settings


class MicrophoneClockTests(unittest.TestCase):
    def test_callback_delay_preserves_adc_continuity_and_real_gaps(self):
        now=[100.]
        class Stream:
            time=10.
            def __init__(self,**kwargs):self.callback=kwargs['callback']
            def start(self):pass
            def abort(self):pass
            def close(self):pass
        sd=SimpleNamespace(RawInputStream=Stream,query_devices=lambda:[{'name':'Synthetic microphone','max_input_channels':1}])
        with patch.dict(sys.modules,{'sounddevice':sd}):
            mic=Microphone(Settings(input_device_name='Synthetic microphone'),lambda:False,clock=lambda:now[0])
            mic.start()
        frames=[]
        for delivered,adc,current in [(100.04,10.,10.032),(100.25,10.032,10.064),(100.35,10.164,10.196)]:
            now[0]=delivered
            mic.stream.callback(bytes(1024),512,SimpleNamespace(inputBufferAdcTime=adc,currentTime=current),False)
            frames.append(mic.read())
        self.assertAlmostEqual(frames[1].started_at,frames[0].ended_at)
        self.assertAlmostEqual(frames[2].started_at-frames[1].ended_at,.1)
        mic.close()
