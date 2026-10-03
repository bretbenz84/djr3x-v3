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

    def test_a_restarted_track_is_refound_as_the_one_thing_of_its_kind_in_view(self):
        # October 3 (be8c8d55): a look at the dog failed with ObjectTargetExpired a few seconds
        # after the dog was offered, and Rex said his saved photo had expired.
        clock=[100.];detections=[{'label':'dog','score':.9,'box':[.1,.1,.2,.2]}]
        detector=SimpleNamespace(capability=Capability('test','fake','test',None,()),detect=lambda f:detections)
        observer=Objects(SimpleNamespace(session_id='session'),detector,clock=lambda:clock[0])
        def update():observer.observe(Frame(str(clock[0]),'fake','session',clock[0],2,2,bytes(12)))
        update();clock[0]+=1.5;update()
        old=observer.snapshot()[0]['target_id']
        detections[0]['box']=[.6,.5,.2,.2]            # The head moved: same dog, new track.
        clock[0]+=1.5;update();clock[0]+=1.5;update()
        new=observer.snapshot()[0]['target_id'];self.assertNotEqual(old,new)
        from unittest.mock import patch
        with patch('r3x.objects.crop_image',lambda frame,box=None:'pixels'):   # No image library in checks.
            self.assertEqual(observer.inspect(old)[1]['target_id'],new)
        detections.append({'label':'dog','score':.8,'box':[.1,.1,.2,.2]})   # Two dogs: which one is unknown.
        clock[0]+=1.5;update();clock[0]+=1.5;update()
        with self.assertRaisesRegex(ValueError,'ObjectTargetExpired'):observer.inspect(old)
        with self.assertRaisesRegex(ValueError,'ObjectTargetExpired'):observer.inspect('never-offered')
