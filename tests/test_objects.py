from types import SimpleNamespace
import unittest
from r3x.objects import Objects
from r3x.vision import Frame
from r3x.providers import Capability

class ObjectTests(unittest.TestCase):
    def test_two_objects_stable_without_category_identity_and_expire(self):
        clock=[100.];detections=[{'label':'dog','score':.9,'box':[.1,.1,.2,.2]},
                                {'label':'dog','score':.8,'box':[.6,.1,.2,.2]}]
        detector=SimpleNamespace(capability=Capability('test','fake','test',None,()),detect=lambda f:detections)
        observer=Objects(SimpleNamespace(session_id='session'),detector,clock=lambda:clock[0])
        def update():observer.observe(Frame(str(clock[0]),'fake','session',clock[0],2,2,bytes(12)))
        update();self.assertFalse(observer.snapshot())
        clock[0]+=1.5;update();rows=observer.snapshot();self.assertEqual(len(rows),2)
        ids=[r['target_id'] for r in rows];self.assertNotEqual(*ids)
        detections[0]['label']='cat';clock[0]+=1.5;update()
        self.assertEqual([r['target_id'] for r in observer.snapshot()],ids)
        self.assertEqual(observer.snapshot()[0]['label'],'cat')
        clock[0]+=5;self.assertFalse(observer.snapshot())
        with self.assertRaisesRegex(ValueError,'FreshObjectViewUnavailable'):observer.inspect(ids[0])
        update();self.assertFalse(observer.snapshot()) # reappearance must stabilize again

    def test_missing_targets_and_provider_shutdown_do_not_reuse_pixels(self):
        detector=SimpleNamespace(capability=Capability('test','fake','test',None,()),detect=lambda f:[])
        observer=Objects(SimpleNamespace(session_id='session'),detector,clock=lambda:100.)
        observer.observe(Frame('f','fake','session',100.,2,2,bytes(12)))
        with self.assertRaisesRegex(ValueError,'ObjectTargetExpired'):observer.inspect('missing')
        observer.cancel()
        with self.assertRaisesRegex(ValueError,'FreshObjectViewUnavailable'):observer.inspect()
