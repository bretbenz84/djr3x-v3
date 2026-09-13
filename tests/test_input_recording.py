import json
from pathlib import Path
import tempfile
import unittest
from r3x.capture import Endpoint,Frame
from r3x.dataset import CaptureRecorder,read_wav


class InputRecordingTests(unittest.TestCase):
    def test_rejected_speech_is_retained_separately_on_shutdown(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=CaptureRecorder(Path(directory).resolve(),'session')
            endpoint=Endpoint('session');pcm=b'\x00\x10'*512
            for i in range(10):
                frame=Frame(pcm,i*.032,(i+1)*.032)
                self.assertIsNone(endpoint.push(frame,False))
                self.assertIsNone(recorder.record_input(frame))
            path=recorder.close()
            self.assertEqual(read_wav(path),pcm*10)
            metadata=json.loads(path.with_suffix('.json').read_text())
            self.assertEqual(len(metadata['frames']),10)
            self.assertFalse((recorder.root/'unreviewed.jsonl').exists())
            self.assertIsNone(recorder.close())

    def test_recording_is_bounded_and_does_not_resume(self):
        with tempfile.TemporaryDirectory() as directory:
            recorder=CaptureRecorder(Path(directory).resolve(),'session')
            pcm=b'\x00\x10'*512
            saved=[]
            for i in range(1100):
                path=recorder.record_input(Frame(pcm,i*.032,(i+1)*.032,True))
                if path:saved.append(path)
            self.assertEqual(len(saved),1)
            self.assertEqual(len(read_wav(saved[0])),30*32000)
            self.assertFalse(recorder.input_pcm)
            self.assertFalse(recorder.input_frames)
            self.assertIsNone(recorder.close())
