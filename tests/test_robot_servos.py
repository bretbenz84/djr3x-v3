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
        self.assertEqual(self.port.pose[1],2624)
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

    def test_talking_keeps_the_hold_and_an_array_arm_move_makes_a_new_lease_wait_for_settling(self):
        self.robot.start()
        self.clock.wait(1.6)
        hold=self.robot.owner.microphone_hold
        request=ServoRequest('talk',self.robot.owner.version,'talking',self.clock()+60)
        self.robot.owner.execute(request,Cancellation())
        # Talking never moves the array arm, so the arm's hold stands; the servo noise is
        # still reported per frame and keeps noisy captures out of voice learning.
        self.assertEqual(self.robot.owner.microphone_hold,hold)
        self.assertTrue(self.robot.owner.motion_risk())
        capture=Capture('c','s',b'',self.clock(),self.clock()+1,self.clock()+1,motion_risk=True)
        self.assertFalse(self.robot.capture_held(capture))
        wave=self.robot.execute_pose('wave',Cancellation(),'wave',hold_pose=False)
        self.assertEqual(wave.status,'pulses_reached')
        self.assertEqual(self.robot.owner.microphone_hold,(0.,0.))
        self.robot.before_frame()
        ready,_=self.robot.owner.microphone_hold
        self.assertGreater(ready,self.clock())      # Settling after the arm stopped.
        self.clock.wait(1.6)
        self.assertTrue(self.robot.owner.microphone_held_during(self.clock(),self.clock()+1))

    def test_look_at_people_turns_to_the_face_follows_and_never_holds(self):
        self.robot.start()
        self.robot.idle.face=lambda:{'neck':4300,'y':.4,'at':self.clock()}
        held=self.robot.execute_pose('look_right',Cancellation(),'look')        # A person asked.
        self.assertEqual(held.status,'pulses_reached')
        self.assertEqual(self.robot.owner.held_channels,{0})
        self.assertEqual(self.robot.idle.mode(),'direction')
        own=self.robot.execute_pose('look_at_people',Cancellation(),'own',hold_pose=False)
        self.assertEqual((own.status,own.reason),('rejected','AgencyCannotOverrideHeldPose'))
        self.assertEqual(self.robot.idle.mode(),'direction')                    # His own cannot override it.
        asked=self.robot.execute_pose('look_at_people',Cancellation(),'asked')
        self.assertEqual(asked.status,'pulses_reached')
        self.assertEqual(self.port.pose[0],4300)
        self.assertEqual(self.robot.owner.held_channels,set())
        self.assertEqual(self.robot.idle.mode(),'people')
        # His own look somewhere becomes the chosen direction; a voice returns him to people.
        self.robot.execute_pose('look_left',Cancellation(),'own-look',hold_pose=False)
        self.assertEqual(self.robot.idle.mode(),'direction')
        self.assertEqual(self.robot.owner.held_channels,set())
        self.assertEqual(self.robot.reflex.face(20.),'started')
        self.robot.reflex.stop()
        self.assertEqual(self.robot.idle.mode(),'people')

    def test_direction_invalidation_only_when_the_microphone_arm_moves(self):
        from r3x.contracts import Cancellation
        invalidated=[]
        self.robot.owner.on_motion=lambda:invalidated.append(self.clock())
        self.robot.start()                      # Head/throttle choreography: array unmoved.
        self.assertFalse(invalidated)
        self.assertFalse(self.robot.owner.microphone_moving())
        self.robot.execute_pose({7:5000},Cancellation(),'mic-arm')
        self.assertEqual(len(invalidated),1)    # Hero arm carries the array.
        self.assertTrue(self.robot.owner.microphone_moving())
        self.robot.close()

    def test_power_down_hook_runs_as_the_park_begins_and_never_blocks_it(self):
        events=[]
        self.robot.start()
        awake=dict(self.port.pose)
        self.robot.before_shutdown=lambda: events.append(('lights',dict(self.port.pose)))
        self.robot.on_shutdown=lambda: events.append(('sound',dict(self.port.pose)))
        self.robot.close()
        self.assertEqual([kind for kind,_ in events],['lights','sound'])
        self.assertEqual(events[1][1][1],awake[1])    # The sound starts with the head still up: it plays over the park.
        self.assertEqual(self.port.pose[1],2624)
        self.assertTrue(self.port.closed)
        def broken(): raise RuntimeError('no speaker')
        robot=RobotServos('/dev/cu.synthetic',open_port=lambda:self.port,clock=self.clock,wait=self.clock.wait)
        robot.owner.wait=self.clock.wait
        self.port.closed=False
        robot.start()
        robot.on_shutdown=broken
        robot.close()                                 # A failed sound never stops the park.
        self.assertTrue(robot.closed)
        self.assertEqual(self.port.pose[1],2624)

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
            self.assertEqual([p.capability.name for p in app.owner.providers[:4]],['tts','playback','startup_audio','robot_servos'])
            self.assertEqual(app.robot.on_startup,app.startup_audio.motion_started)
            self.assertEqual(app.robot.on_shutdown,app.startup_audio.shutdown_started)   # Playback outlives the park.
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


    def test_silent_microphone_is_reopened_once_then_reported(self):
        import sys
        from types import SimpleNamespace
        from r3x.adapters.audio import Microphone
        opened=[]
        class Stream:
            time=1.
            def __init__(self,**kwargs): opened.append(self)
            def start(self): pass
            def abort(self): self.aborted=True
            def close(self): pass
        now=[100.]
        events=[]
        sd=SimpleNamespace(RawInputStream=Stream,query_devices=lambda:[{'name':'fake','max_input_channels':1}])
        with patch.dict(sys.modules,{'sounddevice':sd}):
            mic=Microphone(Settings(input_device_name='fake'),lambda:False,clock=lambda:now[0])
            mic.trace=SimpleNamespace(emit=lambda kind,**data:events.append(kind))
            mic.start()
            now[0]+=4                           # No callbacks: the input went silent.
            self.assertIsNone(mic.read())
            self.assertEqual(len(opened),2)     # Reopened instead of ending the run.
            self.assertTrue(opened[0].aborted)
            self.assertEqual(events.count('microphone_restarted'),1)
            now[0]+=4                           # Silent again right away: a real failure.
            with self.assertRaisesRegex(ProviderFailure,'MicrophoneStalled'):
                mic.read()
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
