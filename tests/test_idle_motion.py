"""Idle movement shares the real servo owner; fake pulses/clocks, no hardware."""
import random
import threading
import unittest

from r3x.contracts import Cancellation
from r3x.idle_motion import IdleMotion
from r3x.servos import ServoOwner, ServoRequest, TalkingMotion
from r3x.robot_servos import RobotServos
from r3x.servo_poses import LIMITS
from tests.test_servos import PulsePort,Clock


class IdleTests(unittest.TestCase):
    def setUp(self):
        self.port,self.clock=PulsePort(),Clock()
        self.owner=ServoOwner(lambda:self.port,medium='simulation',clock=self.clock,
                              wait=self.clock.wait,rng=random.Random(12))
        self.owner.start()
        self.addCleanup(self.owner.close)
        self.idle=IdleMotion(self.owner,rng=random.Random(1))
        self.addCleanup(self.idle.close)

    def execute(self,kind):
        return self.owner.execute(ServoRequest(kind,self.owner.version,kind,self.clock()+120),Cancellation())

    def test_elbow_wakes_then_droops_even_without_speech(self):
        self.assertEqual(self.port.pose[4],6300)
        self.assertEqual(self.execute('startup').status,'pulses_reached')
        self.assertEqual(self.port.pose[4],6720)
        self.assertEqual(self.execute('shutdown').status,'pulses_reached')
        self.assertEqual(self.port.pose[4],6300)

    def test_idle_moves_arms_and_head_with_calibrated_random_excursions_and_return(self):
        self.assertEqual(self.execute('idle').status,'rejected')
        self.execute('startup')
        choices=[]
        for _ in range(5):
            self.port.packets.clear()
            self.assertEqual(self.execute('idle').status,'pulses_reached')
            commands=[]
            for p in self.port.targets:
                commands.extend([(p[1],p[2]+128*p[3])] if p[0]==0x84 else
                    [(p[2]+i,p[3+i*2]+128*p[4+i*2]) for i in range(p[1])])
            self.assertEqual({ch for ch,_ in commands},{0,1,2,6,7,8,9,10})   # Owner: the head looks around too.
            for ch,value in commands:
                self.assertTrue(LIMITS[ch][0]<=value<=LIMITS[ch][1])
            hero=next(value for ch,value in commands if ch==7)
            self.assertTrue(1300<=abs(hero-5984)<=2000)
            choices.append(hero)
            self.assertEqual(self.port.pose[7],5984)
        self.assertGreater(len(set(choices)),1)

    def test_idle_invalidates_hold_and_reacquisition_waits_for_settling(self):
        self.execute('startup');self.clock.wait(.6)
        self.owner.refresh_microphone_hold();self.clock.wait(1.5)
        self.assertTrue(self.owner.microphone_held_during(self.clock(),self.clock()))
        self.execute('idle')
        self.assertFalse(self.owner.microphone_held_during(self.clock(),self.clock()))
        self.clock.wait(.6);self.owner.refresh_microphone_hold()
        self.assertFalse(self.owner.microphone_held_during(self.clock(),self.clock()))
        self.clock.wait(1.5)
        self.assertTrue(self.owner.microphone_held_during(self.clock(),self.clock()))

    def test_scheduler_only_runs_after_quiet_interval_and_does_not_queue(self):
        self.execute('startup');self.clock.wait(.6);self.port.packets.clear()
        self.idle.tick(True)
        self.assertEqual(self.port.targets,[])
        self.clock.wait(3);self.idle.tick(True)
        self.assertEqual(self.port.targets,[])
        self.idle.tick(False);self.clock.wait(20);self.idle.tick(False)
        self.assertEqual(self.port.targets,[])
        self.idle.tick(True);self.clock.wait(10);self.idle.tick(True)
        self.idle._thread.join(1)
        self.assertTrue(self.port.targets)
        count=len(self.port.targets)
        self.idle.close();self.clock.wait(30);self.idle.tick(True)
        self.assertEqual(len(self.port.targets),count)

    def test_talking_preempts_idle_and_joins_before_talking_targets(self):
        self.execute('startup');self.clock.wait(.6)
        reached=threading.Event()
        self.port.on_target=reached.set
        # Keep idle active long enough to exercise interruption, without devices.
        self.owner.wait=lambda seconds:threading.Event().wait(.005)
        self.idle.tick(True);self.clock.wait(10);self.idle.tick(True)
        self.assertTrue(reached.wait(1))
        talking=TalkingMotion(self.owner);talking.before_start=self.idle.stop
        talking.start('speech',Cancellation())
        self.assertIsNone(self.idle._thread)
        talking.stop()
        self.assertEqual(self.owner.state,'active')
        self.owner.wait=self.clock.wait
        self.assertEqual(self.execute('shutdown').status,'pulses_reached')

    def test_robot_capture_and_shutdown_stop_idle_without_late_restart(self):
        robot=RobotServos('/dev/cu.fake',open_port=PulsePort,clock=self.clock,wait=self.clock.wait)
        robot.owner.wait=self.clock.wait
        robot.start()
        try:
            robot.before_frame(allow_idle=True)
            robot.note_speech()
            self.assertFalse(robot.idle._allowed)
            # Capturing must not initiate recentering mid-utterance.
            robot.owner.release_microphone_hold()
            robot.before_frame(capturing=True)
            self.assertEqual(robot.owner.microphone_hold,(0.,0.))
        finally:robot.close()
        self.assertTrue(robot.idle.closed)
        robot.idle.tick(True)
        self.assertIsNone(robot.idle._thread)

    def test_idle_throttle_varies_but_respects_hold_and_resumes_after_release(self):
        self.execute('startup')
        def pose(name):
            return self.owner.execute(ServoRequest('pose',self.owner.version,'pose',self.clock()+60,pose=name),Cancellation())
        pose('throttle_offer')
        held={ch:self.port.pose[ch] for ch in (8,9,10)}
        self.execute('idle')
        self.assertEqual(held,{ch:self.port.pose[ch] for ch in held})
        pose('release_poses')
        previous={ch:self.port.pose[ch] for ch in held}
        for _ in range(5):
            self.assertEqual(self.execute('idle').status,'pulses_reached')
            current={ch:self.port.pose[ch] for ch in held}
            self.assertNotEqual(current,previous)
            previous=current
        self.assertEqual(self.execute('shutdown').status,'pulses_reached')
