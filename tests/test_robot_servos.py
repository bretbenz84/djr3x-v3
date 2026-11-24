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
from r3x.servo_poses import LIMITS, PARK
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

    def test_shutdown_leaves_every_servo_limp_and_the_next_start_brings_them_in(self):
        # Owner, November 23: the board stays on the Mac's USB while the servo rail is switched off;
        # pulses still going out snapped every servo when the rail came back on.
        self.robot.start()
        self.robot.close()
        self.assertEqual(self.port.packets[-len(LIMITS):], [bytes((0x84, ch, 0, 0)) for ch in sorted(LIMITS)])
        self.assertEqual(set(self.port.pose.values()), {0})
        port, clock = self.port, Clock()
        again = RobotServos('/dev/cu.synthetic', open_port=lambda: port, clock=clock, wait=clock.wait)
        again.owner.wait = clock.wait
        again.start()
        self.assertEqual(again.owner.energized_channels, tuple(sorted(LIMITS)))
        again.close()

    def test_a_hand_still_holding_stays_powered_after_shutdown(self):
        from r3x.servo_poses import THROTTLE, THROTTLE_HAND
        self.robot.start()
        self.robot.owner.grip_channels = frozenset((*THROTTLE, *THROTTLE_HAND))
        self.robot.close()
        off = {packet[1] for packet in self.port.packets if packet[0] == 0x84 and packet[2:] == bytes((0, 0))}
        self.assertEqual(off, set(LIMITS) - {*THROTTLE, *THROTTLE_HAND})
        self.assertTrue(all(self.port.pose[ch] for ch in (*THROTTLE, *THROTTLE_HAND)))

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
        self.assertEqual(self.port.last_pulse[1],LIMITS[1][0])   # Parked, then left limp.
        self.assertEqual(self.port.last_pulse[8],dict(PARK)[8])
        self.assertEqual(self.port.pose[1],0)

    def test_he_wakes_with_the_visor_shut_and_it_goes_up_just_before_the_ready_line(self):
        # Owner, November 15: the visor stays down over his eyes through the wake and the
        # please-wait line, and goes up right before he says his ready line.
        import random
        from r3x.servo_poses import VISOR_DOWN, VISOR_OPEN, talking_lanes
        self.robot.start()
        owner = self.robot.owner
        self.assertEqual(self.port.pose[3], VISOR_DOWN)
        moved = lambda: {ch for lane in talking_lanes(dict(self.port.pose), None, owner.held_channels, rng=random.Random(1))
                         for beat in lane for ch, _ in beat.targets}
        self.assertNotIn(3, moved())            # The please-wait line moves the rest of him, not the visor.
        self.assertIn(0, moved())
        self.port.packets.clear()
        self.assertEqual(self.robot.open_eyes().status, 'pulses_reached')   # Everything has loaded.
        self.assertEqual(self.port.pose[3], VISOR_OPEN)                     # Up, before a word of the ready line.
        self.assertEqual({p[1] for p in self.port.targets}, {3})            # Only the visor moved.
        self.assertFalse(owner.held_channels)
        self.assertIn(3, moved())               # The ready line then moves it like any other line.
        self.assertIsNone(self.robot.open_eyes())                           # Already open: nothing more to do.

    def test_a_refused_visor_move_still_lets_the_ready_line_open_it(self):
        from r3x.servo_poses import VISOR_DOWN
        self.robot.start()
        self.robot.owner.state = 'stopped'      # The board will take no pose now.
        self.assertIsNone(self.robot.open_eyes())
        self.assertEqual(self.port.pose[3], VISOR_DOWN)
        self.assertNotIn(3, self.robot.owner.held_channels)   # The hold is gone: talking motion may open it.
        self.robot.owner.state = 'active'

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
        self.robot.start();self.robot.open_eyes()   # Past the ready line: the visor is open.
        self.robot.idle.face=lambda:{'neck':4300,'y':.4,'at':self.clock()}
        held=self.robot.execute_pose('look_right',Cancellation(),'look')        # A person asked.
        self.assertEqual(held.status,'pulses_reached')
        self.assertEqual(self.robot.owner.held_channels,{0})
        self.assertEqual(self.robot.idle.mode(),'direction')
        own=self.robot.execute_pose('look_at_people',Cancellation(),'own',hold_pose=False)
        self.assertEqual((own.status,own.reason),('rejected','HoldingRequestedLook'))
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
        self.assertEqual(self.robot.reflex.face(-20.),'started')   # A voice on his right (look_left is ~19 degrees left).
        self.robot.reflex.stop()
        self.assertEqual(self.robot.idle.mode(),'people')

    def test_a_persons_head_pose_lasts_as_their_look_does_while_arm_poses_hold(self):
        # Owner, October 31 (f6d5a0b7): asked to look left and up, the head stayed there through his
        # answer and on until "resume normal operations". It is now his again once the look is over.
        from r3x.servo_poses import HEAD
        from r3x.servos import LOOK_HOLD_SECONDS
        self.robot.start();self.robot.open_eyes()   # Past the ready line: the visor is open.
        for pose in ('look_left','look_up','tilt_right','throttle_high_five'):
            self.assertEqual(self.robot.execute_pose(pose,Cancellation(),pose).status,'pulses_reached')
        self.assertEqual(self.robot.idle.mode(),'direction')
        self.assertTrue(set(HEAD)<=self.robot.owner.held_channels)
        self.clock.wait(LOOK_HOLD_SECONDS-1)
        self.robot.before_frame()
        self.assertTrue(set(HEAD)<=self.robot.owner.held_channels)      # Still their look.
        self.clock.wait(2)
        self.robot.before_frame()
        self.assertEqual(self.robot.owner.held_channels,{8,9,10})        # The head is free; the raised hand holds.
        self.assertEqual(self.robot.idle.mode(),'people')
        self.assertFalse(self.robot.owner.look_channels)

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
        self.assertEqual(self.port.last_pulse[1],LIMITS[1][0])
        self.assertTrue(self.port.closed)
        def broken(): raise RuntimeError('no speaker')
        robot=RobotServos('/dev/cu.synthetic',open_port=lambda:self.port,clock=self.clock,wait=self.clock.wait)
        robot.owner.wait=self.clock.wait
        self.port.closed=False
        robot.start()
        robot.on_shutdown=broken
        robot.close()                                 # A failed sound never stops the park.
        self.assertTrue(robot.closed)
        self.assertEqual(self.port.last_pulse[1],LIMITS[1][0])

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

    def test_wheels_range_sensors_guard_the_arms_at_the_wake(self):
        # Owner, November 17: with a drive base the wake leaves the reaching arms in for its sensors.
        with tempfile.TemporaryDirectory() as temporary:
            for drive_port, guarded in (('', False), ('/dev/cu.synthetic-drive', True)):
                app=App(Settings(servo_port='/dev/cu.synthetic',drive_port=drive_port),Path(temporary))
                app.configure_live()
                self.assertEqual(app.robot.arm_guard,guarded)
                if guarded:
                    # Owner, November 20: joined before startup, so the drive owner can bring the
                    # throttle out as soon as the wheels connect, right after the wake (b048ba2f: 27.7 s).
                    self.assertIs(app.drive.servos,app.robot)
                    self.assertEqual(app.robot.arm_space,app.drive.space)
                    names=[p.capability.name for p in app.owner.providers]
                    self.assertEqual(names.index('drive_base'),names.index('robot_servos')+1)
        from r3x.servo_poses import POKER,POKER_UP,REST,THROTTLE
        self.robot.start()   # No wheels: the wake brings both arms out, as before.
        self.assertEqual({ch:self.port.pose[ch] for ch in THROTTLE},dict(REST))
        self.assertEqual({ch:self.port.pose[ch] for ch in POKER},dict(POKER_UP))
        self.assertEqual(self.robot.kept_arms,frozenset())

    def test_an_arm_whose_space_reads_clear_stretches_while_he_wakes(self):
        # Owner, November 21: "if there's computed clearance ... make the poker arm start to come down
        # and the throttle wrist could move a little". The wheel board's range sensors now come up beside
        # the wake, and the drive owner's usual rule (clear for two seconds) may act during it. After
        # seeing both arms unfold all the way (run de486648): "a smaller stretch routine on those that
        # match the startup sound clip rather than a full movement. It would eventually get to the
        # standard poses".
        from r3x.servo_poses import DRIVE_HEAD,PARK,POKER,POKER_PARK,POKER_UP,REACHING_ARMS,REST,THROTTLE,WAKE_THROTTLE
        def waking(still_blocked):
            port=PulsePort()
            robot=RobotServos('/dev/cu.synthetic',open_port=lambda:port,clock=self.clock,wait=self.clock.wait,arm_guard=True)
            robot.owner.wait=self.clock.wait
            self.addCleanup(lambda: robot.closed or robot.close())
            began,asked,shoulder,traced=self.clock(),[],[],[]
            robot.trace=type('Trace',(),{'emit':lambda self,event,**row: traced.append((event,row))})()
            self.assertFalse(robot.extend_cleared_arms(frozenset()))   # Nothing is kept before the wake begins.
            def sensors():
                if not asked and self.clock()-began>4.3:
                    # 4.3 s in, as in the run: the wheel board has read the space clear for two seconds.
                    self.assertEqual(robot.kept_arms,frozenset(REACHING_ARMS))
                    asked.append(robot.extend_cleared_arms(still_blocked))
                packet=port.packets[-1]
                if packet[0]==0x9F and packet[2]==8:
                    shoulder.append((round(self.clock()-began,1),packet[3]+128*packet[4]))
            port.on_target=sensors
            robot.start()
            self.assertEqual(asked,[True])
            self.assertTrue(robot.awake)
            robot._arm_return.join(5)
            self.assertEqual({ch:port.pose[ch] for ch in (1,2,11)},dict(DRIVE_HEAD))   # The wake finished as ever.
            release=[row for event,row in traced if event=='servo_base_release']
            self.assertEqual([(row['status'],row['route']) for row in release],[('pulses_reached','wake_stretch')])
            return robot,port,shoulder
        # Both clear: each arm stretches a little in the wake's time and ends at its awake pose.
        robot,port,shoulder=waking(frozenset())
        self.assertEqual([pulse for _,pulse in shoulder],[pose[0] for _,_,pose in WAKE_THROTTLE])
        self.assertGreaterEqual(shoulder[0][0],5.8)          # Not the moment it clears: on the wake's clock,
        self.assertGreaterEqual(shoulder[-1][0],11.2)        # and out to rest only with the head's last rise.
        self.assertGreater(shoulder[1][1],shoulder[0][1])    # Up a little, and down again, before it goes further.
        self.assertEqual({ch:port.pose[ch] for ch in THROTTLE},dict(REST))
        self.assertEqual({ch:port.pose[ch] for ch in POKER},dict(POKER_UP))
        self.assertEqual(robot.kept_arms,frozenset())
        self.assertFalse(robot.owner.held_channels&{ch for arm in REACHING_ARMS.values() for ch in arm})
        # Something is still beside the poker arm: it stays in, kept, and only the throttle stretches.
        robot,port,shoulder=waking(frozenset({'poker'}))
        self.assertEqual({ch:port.pose[ch] for ch in THROTTLE},dict(REST))
        self.assertEqual({ch:port.pose[ch] for ch in POKER},dict(POKER_PARK))
        self.assertEqual(robot.kept_arms,frozenset({'poker'}))
        self.assertFalse(robot.owner.held_channels&set(THROTTLE))
        self.assertTrue(set(REACHING_ARMS['poker'])<=robot.owner.held_channels)
        # Cleared once he is awake, the poker arm comes out the usual way, all at once.
        self.assertTrue(robot.extend_cleared_arms(frozenset()))
        robot._arm_return.join(5)
        self.assertEqual(robot.owner.arm_return_route,'direct')
        self.assertEqual({ch:port.pose[ch] for ch in POKER},dict(POKER_UP))

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
