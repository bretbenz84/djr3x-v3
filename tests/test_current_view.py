import unittest
from types import SimpleNamespace
from dataclasses import replace
from r3x.inspection import CurrentView,InspectionRequest
from r3x.scene import SceneImage
from r3x.contracts import Cancellation,Cancelled
from r3x.semantics import grounded_tools


class CurrentViewTests(unittest.TestCase):
    def setUp(self):
        self.now=100
        self.image=SceneImage('shared',99.5,'data:image/png;base64,fixture')
        self.scene=SimpleNamespace(snapshot=lambda *args:('scene',(self.image,)))
        self.view=CurrentView(self.scene,clock=lambda:self.now);self.view.start()
        self.addCleanup(self.view.close)
        self.request=InspectionRequest('request','current','What do you see?',1,100)

    def test_repeated_inspection_reuses_same_encoded_frame_without_motion(self):
        first,images=self.view.execute(self.request,Cancellation(),lambda:True)
        second,again=self.view.execute(replace(self.request,request_id='next'),Cancellation(),lambda:True)
        self.assertIs(images[0],again[0])
        self.assertEqual(first['motion_status'],'not_requested')
        self.assertEqual(first['capture_age_seconds'],.5)
        self.assertEqual(second['status'],'observed')
        self.assertEqual(first['medium'],'camera')

    def test_noncurrent_direction_is_rejected(self):
        result,images=self.view.execute(replace(self.request,direction='behind'),Cancellation(),lambda:True)
        self.assertEqual(result['reason'],'MotionUnavailable');self.assertFalse(images)

    def test_stale_frame_cannot_pass_as_fresh(self):
        self.image=replace(self.image,at=90)
        def old(*args):self.now+=3;return 'scene',(self.image,)
        self.scene.snapshot=old
        result,images=self.view.execute(self.request,Cancellation(),lambda:True)
        self.assertEqual(result['reason'],'FreshCurrentViewUnavailable');self.assertFalse(images)

    def test_cancelled_wait_never_returns_an_image(self):
        token=Cancellation();token.event.set()
        with self.assertRaises(Cancelled):self.view.execute(self.request,token,lambda:True)

    def test_stop_and_supersession_reject_acquisition(self):
        result,images=self.view.execute(self.request,Cancellation(),lambda:False)
        self.assertEqual(result['reason'],'InspectionSuperseded')
        self.view.close()
        result,images=self.view.execute(self.request,Cancellation(),lambda:True)
        self.assertEqual(result['reason'],'CurrentViewNotStarted')

    def test_tool_schema_exposes_only_current_view(self):
        tools=grounded_tools({'inspection_enabled':True,'inspection_directions':['current'],'source_turn_id':'t'})
        inspection=next(t for t in tools if t['name']=='inspect_view')
        self.assertEqual(inspection['parameters']['properties']['direction']['enum'],['current'])

    def test_app_wiring_respects_camera_image_upload_setting(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from r3x.app import App
        from r3x.settings import Settings
        with TemporaryDirectory() as folder:
            for camera,images,expected in ((True,True,True),(True,False,False),(False,True,False)):
                app=App(Settings(camera_enabled=camera,camera_device_name="synthetic camera",scene_observations_enabled=images),Path(folder))
                app.configure_live()
                names={p.capability.name for p in app.live_components}
                self.assertEqual('current_view' in names,expected)
                if expected:
                    adapter=next(p for p in app.live_components if p.capability.name=='current_view')
                    self.assertFalse(adapter.started)
                    self.assertIsNone(adapter.scene.worker)
                self.assertFalse((Path(folder)/'local').exists())

    def test_same_pixels_have_same_digest_despite_new_frame_id(self):
        first,_=self.view.execute(self.request,Cancellation(),lambda:True)
        self.image=replace(self.image,frame_id='new')
        second,_=self.view.execute(self.request,Cancellation(),lambda:True)
        self.assertEqual(first['image_digest'],second['image_digest'])
        self.image=replace(self.image,data_url='data:image/png;base64,different')
        third,_=self.view.execute(self.request,Cancellation(),lambda:True)
        self.assertNotEqual(first['image_digest'],third['image_digest'])

    def test_current_image_can_be_reused_without_upload_in_production_session(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from r3x.memory import Memory
        from r3x.state import CoreState
        from r3x.speakers import SpeakerTracker
        from r3x.session import Session
        from r3x.output import OutputOwner
        from r3x.scenario import SyntheticSink,SyntheticTTS
        from r3x.contracts import ToolCall,Capture,Transcript
        from r3x.visual_memory import save
        from tests.test_voice_core import Trace
        from hashlib import sha256
        snapshots=[]
        class Brain:
            def reply(self,snapshot,cancel):
                snapshots.append(snapshot)
                if not snapshot.continuation:
                    yield ToolCall('inspect','inspect_view',{'direction':'current','question':'Anything different?'})
                else:yield 'The supplied image is the same.'
        with TemporaryDirectory() as folder:
            store=Memory(Path(folder).resolve()/'core.sqlite3');store.start()
            try:
                source={'status':'observed','stage':'complete','frame_id':'shared','request_id':'prior',
                        'image_digest':sha256(self.image.data_url.encode()).hexdigest()}
                save(store,'session','[]',source,{'observation_id':'prior','description':'A red cup.',
                     'uncertainty':'Appearance only.'},'scripted',100)
                trace=Trace();state=CoreState(store,SpeakerTracker('synthetic'),trace)
                session=Session('session',Brain(),OutputOwner(SyntheticTTS(),SyntheticSink(),trace,inspection=self.view),trace,state)
                result=session.process_turn(Capture('c','session',bytes(1024),98,99,99.6),Transcript('Look again.',-.01,True),0)
                self.assertEqual(result.status,'completed')
                self.assertFalse(snapshots[-1].images)
                self.assertEqual(snapshots[-1].context['inspection']['cached_description']['id'],'prior')
                self.assertNotEqual(store.rows('SELECT spoken FROM visual_observations')[0]['spoken'],'[]')
                session.close()
            finally:store.close()

    def test_source_failure_is_a_structured_result(self):
        def fail(*args):raise RuntimeError('synthetic failure')
        self.scene.snapshot=fail
        result,images=self.view.execute(self.request,Cancellation(),lambda:True)
        self.assertEqual(result['reason'],'RuntimeError')
        self.assertFalse(images)
