"""Robot composition/geometry contracts; synthetic audio, poses and temporary stores."""
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
import tempfile
import unittest
from unittest.mock import patch

from r3x.app import App
from r3x.contracts import Cancellation, Capture, Transcript, ProviderFailure
from r3x.robot_servos import RobotServos
from r3x.servos import ServoRequest
from r3x.settings import Settings
from r3x.state import CoreState
from r3x.speakers import SpeakerTracker
from r3x.providers import ProviderOwner
from tests.test_servos import PulsePort, Clock
from tests.test_memory_core import MemoryFixture,NS,observation


class RobotServoTests(unittest.TestCase):
    def setUp(self):
        self.port,self.clock = PulsePort(),Clock()
        self.robot = RobotServos('/dev/cu.synthetic',open_port=lambda:self.port,
                                 clock=self.clock,wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait

    def tearDown(self):
        if not self.robot.closed:
            self.robot.close()

    def test_wake_and_hold_precede_capture_park_precedes_port_close(self):
        events=[]
        robot=self.robot
        class CaptureProvider:
            from r3x.providers import Capability
            capability=Capability('capture','fake','synthetic')
            def start(self):
                self_test.assertTrue(robot.owner.microphone_held_during(self_test.clock(),self_test.clock()))
                events.append('capture_started')
            def cancel(self): events.append('capture_cancelled')
            def close(self):
                self_test.assertFalse(self_test.port.closed)
                events.append('capture_closed')
        self_test=self
        # Test lifecycle without distribution checks obscuring the fake transport.
        with patch('r3x.providers.dependency_status',return_value={}):
            owner=ProviderOwner((robot,CaptureProvider()))
            self.assertTrue(owner.start())
            owner.close()
        self.assertEqual(events,['capture_started','capture_cancelled','capture_closed'])
        self.assertTrue(self.port.closed)
        self.assertEqual(self.port.pose[1],2600)
        self.assertEqual(self.port.pose[8],9088)

    def test_microphone_hold_expires_and_capture_completion_releases_it(self):
        self.robot.start()
        ready,expires=self.robot.owner.microphone_hold
        self.assertEqual(self.port.pose[7],5984)
        self.assertFalse(self.robot.owner.microphone_held_during(ready-.1,ready+1))
        self.assertTrue(self.robot.owner.microphone_held_during(ready,ready+2))
        self.assertFalse(self.robot.owner.microphone_held_during(expires-1,expires+.01))
        capture=Capture('c','s',b'',ready,ready+1,ready+1)
        self.assertTrue(self.robot.capture_held(capture))
        self.assertFalse(self.robot.owner.microphone_held_during(ready,ready+1))

    def test_talking_invalidates_hold_and_new_lease_waits_for_settling(self):
        self.robot.start()
        request=ServoRequest('talk',self.robot.owner.version,'talking',self.clock()+60)
        self.robot.owner.execute(request,Cancellation())
        self.assertEqual(self.robot.owner.microphone_hold,(0.,0.))
        self.assertTrue(self.robot.owner.motion_risk())
        self.robot.before_frame()
        self.assertEqual(self.robot.owner.microphone_hold,(0.,0.))
        self.clock.wait(.6)
        self.robot.before_frame()
        ready,_=self.robot.owner.microphone_hold
        self.assertGreater(ready,self.clock())
        self.clock.wait(1.5)
        self.assertFalse(self.robot.owner.motion_risk())

    def test_direction_invalidation_occurs_at_motion_start(self):
        invalidated=[]
        self.robot.owner.on_motion=lambda:invalidated.append(self.clock())
        self.robot.start()
        self.assertTrue(invalidated)
        before=len(invalidated)
        self.robot.close()
        self.assertGreater(len(invalidated),before)

    def test_faulted_link_reports_park_failure_but_closes_port(self):
        self.robot.start()
        self.robot.owner.state='faulted'
        with self.assertRaisesRegex(ProviderFailure,'ParkUnavailable'):
            self.robot.close()
        self.assertTrue(self.port.closed)
        self.assertTrue(self.robot.closed)

    def test_doctor_composition_is_inert_and_servo_lifecycle_runs_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            app=App(Settings(servo_port='/dev/cu.synthetic'),Path(temporary))
            app.configure_live()
            self.assertEqual([p.capability.name for p in app.owner.providers[:3]],['tts','playback','robot_servos'])
            self.assertIsNone(app.robot.owner._bus)
            self.assertFalse((Path(temporary)/'local').exists())
        for value in ('/dev/tty.bad','auto','/dev/cu.bad path','/dev/cu.bad/child'):
            with self.assertRaises(ValueError): Settings(servo_port=value)

    def test_isolated_audio_does_not_bypass_servo_noise_exclusion(self):
        import sys
        from types import SimpleNamespace
        from r3x.adapters.audio import Microphone
        class Stream:
            time=1.
            def __init__(self,**kwargs): self.callback=kwargs['callback']
            def start(self): pass
            def abort(self): pass
            def close(self): pass
        sd=SimpleNamespace(RawInputStream=Stream,query_devices=lambda:[{'name':'fake','max_input_channels':1}])
        with patch.dict(sys.modules,{'sounddevice':sd}):
            mic=Microphone(Settings(input_device_name='fake',echo_isolated=True),lambda:False)
            mic.motion_risk=lambda:True
            mic.start()
        mic.stream.callback(bytes(1024),512,SimpleNamespace(inputBufferAdcTime=1.),False)
        frame = mic.read()
        self.assertFalse(frame.echo_risk)
        self.assertTrue(frame.motion_risk)
        mic.close()


class RobotEnrollmentTests(MemoryFixture):
    def held_turn(self,held,text='Here is my useful synthetic voice sample.'):
        self.at+=10
        capture=Capture(uuid4().hex,'session',uuid4().bytes*6000,self.at,self.at+3,self.at+3,
                        robot_microphone_hold=held,voiced_intervals=((0.,3.),))
        self.state.begin(uuid4().hex,capture,Transcript(text,-.01,True),observation())

    def name(self):
        return self.call('introduce_person',{'name':'Test Person','aliases':[],'subject':'self',
                         'person_id':None,'face_track_id':None})

    def test_unheld_robot_intro_saves_name_but_no_voice_candidate(self):
        self.state.require_microphone_hold=True
        for _ in range(3):
            self.held_turn(False)
        self.assertEqual(self.name().outcome,'ok')
        self.assertEqual(self.store.profiles(NS),{})
        self.assertEqual(self.store.rows('SELECT * FROM voice_candidates'),[])

    def test_held_robot_samples_enroll_and_restart_keeps_capture_provenance(self):
        self.state.require_microphone_hold=True
        self.held_turn(True)
        person=self.name().data['person_id']
        self.state=CoreState(self.store,SpeakerTracker(NS),self.state.trace,require_microphone_hold=True)
        self.held_turn(False)
        self.held_turn(True)
        self.assertEqual(self.store.profiles(NS),{})
        self.held_turn(True)
        self.assertEqual(len(self.store.profiles(NS)[person]),3)

    def test_robot_does_not_finish_old_unheld_dev_mac_chain(self):
        self.begin('Here is one old voice sample.')
        person=self.name().data['person_id']
        self.begin('Here is another old voice sample.')
        self.state=CoreState(self.store,SpeakerTracker(NS),self.state.trace,require_microphone_hold=True)
        self.held_turn(True)
        self.assertEqual(self.store.profiles(NS),{})
        proposal=next(iter(self.state.enrollments.values()))
        self.assertEqual(len(proposal.samples),1)
        self.held_turn(True)
        self.held_turn(True)
        self.assertEqual(len(self.store.profiles(NS)[person]),3)
