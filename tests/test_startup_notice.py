"""Startup warnings use synthetic providers and the real speech output path."""
from dataclasses import replace
from queue import Queue
import unittest
from r3x.settings import Settings
from r3x.providers import ProviderOwner, LifecycleResult
from r3x.startup_notice import announce, notice_text
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests.test_boot import FakeProvider
from tests.test_voice_core import Trace


class StartupNoticeTests(unittest.TestCase):
    def test_disabled_camera_is_not_claimed_disconnected(self):
        text=notice_text(Settings(),[])
        self.assertEqual(text,"")
        self.assertNotIn('missing',text)

    def test_healthy_enabled_camera_does_not_warn(self):
        self.assertEqual(notice_text(Settings(camera_enabled=True,camera_device_name="Fixture"),[]),'')

    def test_configured_device_failure_is_named_without_private_exception(self):
        rows=[LifecycleResult('camera','start','failed','ProviderFailure','CameraNameMissingOrAmbiguous'),
              LifecycleResult('direction','start','failed','RuntimeError','private text')]
        text=notice_text(Settings(camera_enabled=True,camera_device_name="Fixture"),rows,failed=True)
        self.assertIn('camera and microphone direction sensor',text)
        self.assertIn('Startup failed',text)
        self.assertNotIn('private',text)
        self.assertNotIn('disabled',text)

    def test_failure_speaks_before_audio_cleanup_and_closes_every_provider(self):
        calls=[]
        owner=ProviderOwner(tuple(FakeProvider(name,calls,('start',) if name=='camera' else ())
                                  for name in ('tts','playback','camera')))
        trace=Trace()
        class Sink(SyntheticSink):
            def play(inner,*args):
                self.assertNotIn(('playback','close'),calls)
                self.assertNotIn(('tts','close'),calls)
                return super().play(*args)
        output=OutputOwner(SyntheticTTS(),Sink(),trace)
        self.assertFalse(owner.start(on_required_failure=lambda:announce(Settings(camera_enabled=True,camera_device_name="Fixture"),owner,output,trace,failed=True)))
        receipt=next(r for r in trace.rows if r['event']=='startup_notice_delivery')
        self.assertEqual(receipt['status'],'completed')
        self.assertIn('camera',' '.join(receipt['completed_text']))
        self.assertEqual(owner.state,'closed')
        self.assertIn(('tts','close'),calls)

    def test_missing_output_is_logged_without_attempting_tts(self):
        calls=[]
        owner=ProviderOwner((FakeProvider('tts',calls,('start',)),))
        trace=Trace()
        class TTS:
            def synthesize(self,*a): raise AssertionError('must not synthesize')
        output=OutputOwner(TTS(),SyntheticSink(),trace)
        self.assertFalse(owner.start(on_required_failure=lambda:announce(Settings(),owner,output,trace,failed=True)))
        self.assertEqual(trace.rows[-1]['status'],'unavailable')
        self.assertEqual(owner.state,'closed')

    def test_optional_hardware_failure_warns_once_and_can_continue(self):
        calls=[]
        owner=ProviderOwner((FakeProvider('tts',calls),FakeProvider('playback',calls),
            FakeProvider('direction',calls,('start',),required=False)))
        self.assertTrue(owner.start());self.addCleanup(owner.close)
        trace=Trace()
        receipt=announce(Settings(camera_enabled=True,camera_device_name="Fixture"),owner,OutputOwner(SyntheticTTS(),SyntheticSink(),trace),trace)
        self.assertEqual(receipt.status,'completed')
        self.assertEqual(owner.state,'ready')
        self.assertNotIn('Startup failed',' '.join(receipt.completed_text))

    def test_warning_failure_does_not_skip_cleanup(self):
        calls=[]
        owner=ProviderOwner((FakeProvider('tts',calls),FakeProvider('camera',calls,('start',))))
        def failed(): raise RuntimeError('private error')
        self.assertFalse(owner.start(on_required_failure=failed))
        self.assertEqual(owner.state,'closed')
        self.assertIn(('tts','close'),calls)
        self.assertNotIn('private error',repr(owner.results))

    def test_startup_audio_is_discarded_before_listening(self):
        from r3x.adapters.audio import Microphone
        mic=Microphone(Settings(),lambda:False)
        mic.frames.put(object());mic.frames.put(object())
        mic.discard_pending()
        self.assertTrue(mic.frames.empty())

    def test_missing_camera_skips_visual_workers_and_connected_next_launch_starts_them(self):
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from r3x.app import App
        from r3x.contracts import ProviderFailure
        settings=Settings(camera_enabled=True,camera_device_name='Fixture',object_memory_enabled=True)
        with tempfile.TemporaryDirectory() as folder:
            for absent in (True,False):
                app=App(settings,Path(folder));app.configure_live()
                patches=[]
                calls=[]
                def start(name):
                    calls.append(name)
                    if name=='camera' and absent: raise ProviderFailure('CameraNameMissingOrAmbiguous')
                try:
                    for provider in app.owner.providers:
                        for method in ('start','cancel','close'):
                            p=patch.object(provider,method,side_effect=(lambda name=provider.capability.name:start(name)) if method=='start' else None)
                            p.start();patches.append(p)
                    with patch('r3x.providers.dependency_status',return_value={}):
                        self.assertTrue(app.owner.start())
                    for name in ('scene_observations','current_view','objects'):
                        self.assertEqual(name in calls,not absent)
                    text=notice_text(settings,app.owner.results)
                    self.assertEqual('photoreceptors' in text,absent)
                    self.assertTrue(settings.camera_enabled)
                    app.close()
                finally:
                    for p in reversed(patches):p.stop()

    def test_notice_remains_visible_when_speech_unavailable(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from r3x.terminal import LiveConsole
        text=StringIO()
        with redirect_stdout(text):
            console=LiveConsole()
            console({'event':'startup_notice','text':'Hardware unavailable: camera.'})
            console({'event':'startup_notice_delivery','status':'unavailable'})
        self.assertIn('Hardware unavailable: camera.',text.getvalue())
        self.assertIn('could not be spoken',text.getvalue())
