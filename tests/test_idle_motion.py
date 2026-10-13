"""Idle movement shares the real servo owner; fake pulses/clocks, no hardware."""
import random
import threading
import unittest

from r3x.contracts import Cancellation
from r3x.idle_motion import IdleMotion
from r3x.servos import ServoOwner, ServoRequest, TalkingMotion
from r3x.robot_servos import RobotServos
from r3x.servo_poses import HEAD_SMALL, LIMITS, NECK_CENTER, NECK_PER_DEG, IdlePhrase
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

    def execute(self,kind,phrase=None):
        return self.owner.execute(ServoRequest(kind,self.owner.version,kind,self.clock()+120,idle=phrase),Cancellation())

    def commands(self):
        rows=[]
        for p in self.port.targets:
            rows.extend([(p[1],p[2]+128*p[3])] if p[0]==0x84 else
                [(p[2]+i,p[3+i*2]+128*p[4+i*2]) for i in range(p[1])])
        return rows

    def test_elbow_wakes_then_droops_even_without_speech(self):
        self.assertEqual(self.port.pose[4],6300)
        self.assertEqual(self.execute('startup').status,'pulses_reached')
        self.assertEqual(self.port.pose[4],6720)
        self.assertEqual(self.execute('shutdown').status,'pulses_reached')
        self.assertEqual(self.port.pose[4],6300)

    def test_idle_moves_every_part_slowly_within_calibrated_ranges(self):
        self.assertEqual(self.execute('idle').status,'rejected')
        self.execute('startup')
        choices=[]
        for _ in range(5):
            self.port.packets.clear()
            self.assertEqual(self.execute('idle').status,'pulses_reached')
            commands=self.commands()
            self.assertEqual({ch for ch,_ in commands},set(LIMITS))
            for ch,value in commands:
                self.assertTrue(LIMITS[ch][0]<=value<=LIMITS[ch][1])
            # The visor stays in its speaking range, above the camera.
            self.assertTrue(all(5382<=value<=6132 for ch,value in commands if ch==3))   # Around the open neutral, never the surprise.
            # The arm that carries the microphone swings less far than before and returns.
            hero=next(value for ch,value in commands if ch==7)
            self.assertTrue(700<=abs(hero-5984)<=1400)
            choices.append(hero)
            self.assertEqual(self.port.pose[7],5984)
            # Slow and measured: no idle profile is faster than the old idle head glance.
            speeds=[p[2]+128*p[3] for p in self.port.packets if p[0]==0x87 and p[1] not in (8,9,10)]
            self.assertTrue(speeds and max(speeds)<=18)
        self.assertGreater(len(set(choices)),1)

    def test_parts_start_at_different_moments_instead_of_all_at_once(self):
        self.execute('startup');self.port.packets.clear()
        sent=[]
        self.port.on_target=lambda:sent.append((self.clock(),self.commands()[-1][0]))
        began=self.clock()
        self.assertEqual(self.execute('idle',IdlePhrase((('hero',0.),('poker',1.),('visor',2.2)))).status,'pulses_reached')
        first={}
        for at,ch in sent:first.setdefault(ch,at-began)
        self.assertEqual(set(first),{4,5,6,12,13,3})
        self.assertLess(first[4],.1)
        self.assertGreaterEqual(first[6],1.)
        self.assertGreaterEqual(first[3],2.2)
        # Within one arm every joint starts at the same moment.
        self.assertEqual(first[4],first[5])
        self.assertEqual(first[6],first[12]);self.assertEqual(first[6],first[13])

    def test_an_arm_moves_all_its_joints_together(self):
        # Owner, October 12: the arm joints moved one at a time; a real arm moves several
        # joints at once. Each arm's joints now start together, move a little each, and are
        # paced to arrive together.
        self.execute('startup');self.clock.wait(.6)
        for seed in range(12):
            self.owner.rng=random.Random(seed)
            for part,joints in (('hero',(4,5)),('poker',(6,12,13)),('throttle',(8,9,10))):
                before={ch:self.port.pose[ch] for ch in joints}
                self.port.packets.clear()
                sent={}
                self.port.on_target=lambda:[sent.setdefault(ch,self.clock()) for ch,_ in self.commands()]
                self.assertEqual(self.execute('idle',IdlePhrase(((part,0.),))).status,'pulses_reached')
                self.assertEqual(len({sent[ch] for ch in joints}),1,(part,sent))
                self.assertTrue(all(abs(self.port.pose[ch]-before[ch])>2 for ch in joints),(part,seed))
                if part!='throttle':
                    profiles,aims={},{}
                    for p in reversed(self.port.packets):   # The first beat: first speed and target per joint.
                        if p[0]==0x87:profiles[p[1]]=p[2]+128*p[3]
                    for ch,value in reversed(self.commands()):aims[ch]=value
                    times=[abs(aims[ch]-before[ch])/(100*profiles[ch]) for ch in joints]
                    self.assertLess(max(times)-min(times),.8,(part,seed,times))

    def test_small_head_moves_keep_the_camera_view_and_large_ones_reframe_it(self):
        self.execute('startup')
        events=[]
        self.owner.on_camera_motion=lambda moving,request,kind:events.append(moving)
        head={ch:self.port.pose[ch] for ch in (0,1,2,11)}
        moved=self.owner.head_moved_at=float('-inf')
        small=tuple((ch,head[ch]+HEAD_SMALL[ch]-10) for ch in head)
        self.assertEqual(self.execute('idle',IdlePhrase((('head',0.),),small)).status,'pulses_reached')
        self.assertEqual(events,[])
        self.assertEqual(self.owner.head_moved_at,moved)
        self.assertEqual(self.execute('idle',IdlePhrase((('head',0.),),((0,head[0]+1500),))).status,'pulses_reached')
        self.assertEqual(events,[True,False])
        self.assertEqual(self.owner.head_moved_at,self.clock())

    def test_only_the_array_arm_ends_the_microphone_hold(self):
        # With continuous idle movement the hold would otherwise never be valid, and no
        # voice could be learned. Other joints' noise is judged per capture (motion_risk).
        self.execute('startup');self.clock.wait(.6)
        self.owner.refresh_microphone_hold();self.clock.wait(1.5)
        hold=self.owner.microphone_hold
        self.execute('idle',IdlePhrase((('head',0.),('hero',.3),('throttle',.6))))
        self.assertEqual(self.owner.microphone_hold,hold)
        self.assertTrue(self.owner.microphone_held_during(self.clock(),self.clock()))
        self.clock.wait(31.);self.owner.refresh_microphone_hold()      # Renewed without moving anything.
        self.assertTrue(self.owner.microphone_held_during(self.clock(),self.clock()+1))
        self.execute('idle',IdlePhrase((('hero',0.),),swing=True))
        self.assertEqual(self.owner.microphone_hold,(0.,0.))

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

    def run_scheduler(self,seconds,calm=False,step=.1):
        """Tick like the audio loop; each phrase runs on the fake clock in its own thread."""
        phrases=[]
        end=self.clock()+seconds
        while self.clock()<end:
            before=len(self.port.packets)
            self.idle.tick(True,calm)
            thread=self.idle._thread
            if thread is not None:
                thread.join(2)
                channels={ch for ch,_ in self.commands_since(before)}
                if channels:phrases.append((self.clock(),channels))
            self.clock.wait(step)
        return phrases

    def commands_since(self,index):
        packets,self.port.packets=self.port.packets,self.port.packets[index:]
        try:return self.commands()
        finally:self.port.packets=packets

    def test_scheduler_keeps_something_moving_and_never_everything_at_once(self):
        self.execute('startup');self.clock.wait(.6);self.port.packets.clear()
        self.idle.tick(True)
        self.assertIsNone(self.idle._thread)      # A short pause before movement resumes.
        phrases=self.run_scheduler(120.)
        self.assertGreater(len(phrases),30)       # Was 15 moves in ten minutes of the October 3 run.
        gaps=[b[0]-a[0] for a,b in zip(phrases,phrases[1:])]
        self.assertLess(max(gaps),8.)             # Never "motionless for a while".
        parts={0:'head',1:'head',2:'head',11:'head',3:'visor',4:'hero',5:'hero',6:'poker',12:'poker',13:'poker',7:'hero',8:'throttle',9:'throttle',10:'throttle'}
        for _,channels in phrases:
            self.assertLessEqual(len({parts[ch] for ch in channels}),3)   # Never every servo at once.
        moved=set().union(*(channels for _,channels in phrases))
        self.assertEqual(moved,set(LIMITS))       # Over time every part takes its turn.
        self.idle.tick(False);self.clock.wait(20)
        count=len(self.port.targets)
        self.idle.tick(False)
        self.assertEqual(len(self.port.targets),count)
        self.idle.close();self.clock.wait(30);self.idle.tick(True)
        self.assertEqual(len(self.port.targets),count)

    def test_waking_after_a_drive_moves_at_once_even_while_he_listens_or_works(self):
        # October 3 (aa6782c0): the servos were free when the wheels stopped, but idle waited
        # two seconds to resume and stayed still while the owner spoke; the head looked locked
        # until the arm had finished returning.
        self.execute('startup');self.port.packets.clear()
        self.idle.tick(False)                      # He is listening: normally still.
        self.assertIsNone(self.idle._thread)
        self.idle.wake()
        self.assertIsNotNone(self.idle._thread)    # No pause: the first phrase starts now.
        self.idle._thread.join(5)
        moved={ch for ch,_ in self.commands()}
        self.assertTrue(moved & {0,1,2})           # The head leads.
        before=len(self.port.targets)
        for _ in range(40):                        # Still "listening" (allowed False) for 4 s...
            self.idle.tick(False,True)
            if self.idle._thread: self.idle._thread.join(5)
            self.clock.wait(.1)
        self.assertGreater(len(self.port.targets),before)   # ...and he keeps moving.
        self.clock.wait(6.)
        self.idle.tick(False)
        count=len(self.port.targets)
        self.clock.wait(5.);self.idle.tick(False)
        self.assertEqual(len(self.port.targets),count)      # Afterwards listening stills him again.

    def test_a_picture_wanted_after_the_drive_keeps_the_head_still_first(self):
        # October 5 (4153bcbc): the look asked for after a drive failed because idle head
        # motion set off 10 ms after the wheels stopped and the camera counted as moving
        # for the whole capture window.
        self.execute('startup');self.port.packets.clear()
        self.idle.wake(delay=4.)
        self.assertIsNone(self.idle._thread)
        before=len(self.port.targets)
        for _ in range(30):                        # 3 s: the picture's window, nothing moves...
            self.idle.tick(True)
            if self.idle._thread: self.idle._thread.join(5)
            self.clock.wait(.1)
        self.assertEqual(len(self.port.targets),before)
        self.clock.wait(1.5);self.idle.tick(False) # ...then the delayed wake applies even while he listens.
        if self.idle._thread: self.idle._thread.join(5)
        self.assertGreater(len(self.port.targets),before)
        self.execute('startup');self.port.packets.clear()
        self.idle.wake(delay=4.);self.idle.wake()  # The picture was taken early: wandering resumes now.
        self.assertIsNotNone(self.idle._thread)

    def test_waking_never_competes_with_talking_motion(self):
        self.execute('startup');self.port.packets.clear()
        self.idle.busy=lambda:True
        self.idle.wake()
        self.assertIsNone(self.idle._thread)
        self.idle.tick(False)
        self.assertIsNone(self.idle._thread)

    def test_calm_keeps_the_view_settled_and_the_array_still(self):
        # While Rex listens for a reply or works on one, idle life continues without
        # view-changing head moves or microphone-arm swings.
        self.execute('startup');self.clock.wait(.6);self.port.packets.clear()
        events=[]
        self.owner.on_camera_motion=lambda moving,request,kind:events.append(kind)
        phrases=self.run_scheduler(90.,calm=True)
        self.assertGreater(len(phrases),20)
        self.assertEqual(events,[])
        self.assertFalse(any(7 in channels for _,channels in phrases))

    def test_follows_a_face_part_of_the_way_keeps_it_high_in_frame_and_glances_away(self):
        self.execute('startup');self.clock.wait(.5)
        face={'neck':4000,'y':.72}
        self.idle.face=lambda:{**face,'at':self.clock()}
        from r3x.servo_poses import head_pose
        neck,pitch=self.port.pose[0],head_pose(self.port.pose)[0]
        targets,speed,_=self.idle._head(self.clock(),False)
        target=dict(targets)
        # V2 centring gain 0.5: half the way to the face, not a snap.
        self.assertAlmostEqual(target[0],neck+.5*(4000-neck),delta=2)
        # A face low in the picture: both head servos pitch the head further down (left falls, right rises).
        self.assertLess(target[2],self.port.pose[2]);self.assertGreater(target[11],self.port.pose[11])
        self.assertLess(head_pose(target)[0],pitch)
        # The framing pitch, with at most a small living nod around it (October 12).
        self.assertAlmostEqual(self.owner.gaze_pitch,head_pose(target)[0],delta=.045)
        self.owner.commanded.update({0:4000,2:target[2],11:target[11]})
        face['y']=.40
        targets,_,_=self.idle._head(self.clock(),False)
        self.assertLessEqual(abs(dict(targets)[0]-4000),160)    # Inside the dead zone: only a little life.
        self.assertAlmostEqual(head_pose(dict(targets))[0],head_pose(target)[0],delta=.045)
        # Constant tracking was creepy in V2: within 16 seconds he looks aside, then comes back.
        looked=[]
        for _ in range(200):
            self.clock.wait(.1)
            targets,_,_=self.idle._head(self.clock(),False)
            if targets and abs(dict(targets)[0]-4000)>=12*NECK_PER_DEG-1:looked.append(self.clock())
        self.assertGreaterEqual(len(looked),1)
        self.assertLessEqual(len(looked),3)
        # A face seen before the head last moved says nothing about where it is now.
        self.owner.head_moved_at=self.clock()
        self.idle._glance_until=float('-inf')
        self.idle.face=lambda:{**face,'neck':7000,'at':self.clock()-1.}
        self.assertEqual(self.idle._head(self.clock(),False)[0],())

    def test_looks_around_the_room_when_nobody_is_in_view(self):
        self.execute('startup')
        looks=[]
        for _ in range(400):
            self.clock.wait(.5)
            targets,_,_=self.idle._head(self.clock(),False)
            if targets:looks.append(dict(targets))
        self.assertGreater(len(looks),10)
        self.assertLess(len(looks),40)            # A look every 6-13 seconds, drifting in between.
        self.assertGreater(max(h[0] for h in looks)-min(h[0] for h in looks),2500)   # Left and right.
        self.assertGreater(max(h[2] for h in looks)-min(h[2] for h in looks),400)    # Up and down.
        self.assertTrue(all(abs(h[0]-NECK_CENTER)<=35*NECK_PER_DEG+1 for h in looks))

    def test_following_a_face_now_and_then_settles_into_a_gentle_head_cock(self):
        # Owner, October 10 evening: a little more tilt in idle life, used like a listening dog.
        from r3x.servo_poses import head_pose
        self.execute('startup');self.clock.wait(.5)
        self.idle.face=lambda:{'neck':self.port.pose[0],'y':.4,'at':self.clock()}
        tilts=[]
        for _ in range(900):
            self.clock.wait(.2)
            targets,_,_=self.idle._head(self.clock(),False)
            if targets and self.clock()>=self.idle._glance_until:
                tilt=head_pose(dict(targets))[1]
                tilts.append(round(tilt,2))
                self.owner.commanded.update(dict(targets))
        cocked=[t for t in tilts if abs(t)>=.14]
        self.assertGreater(len(cocked),10)                      # Cocks happen...
        self.assertTrue(all(abs(t)<=.36 for t in tilts))       # ...gently (a cock plus a little living lean)...
        self.assertGreater(len([t for t in tilts if abs(t)<.07]),len(cocked))   # ...and mostly it is straight.
        self.assertTrue(any(t>0 for t in cocked) and any(t<0 for t in cocked))  # Both sides.

    def test_a_chosen_direction_is_kept_until_people_are_chosen_a_voice_or_time(self):
        self.execute('startup')
        self.idle.face=lambda:{'neck':4000,'y':.4,'at':self.clock()}
        pose=lambda name,hold:self.owner.execute(ServoRequest('p',self.owner.version,'pose',self.clock()+60,
            pose=name,hold_pose=hold),Cancellation())
        self.assertEqual(self.idle.mode(),'people')
        self.assertEqual(pose('look_right',False).status,'pulses_reached')
        self.idle.posed((0,),own=True)
        self.assertEqual(self.idle.mode(),'direction')
        for _ in range(20):
            self.clock.wait(1.)
            targets,_,_=self.idle._head(self.clock(),False)
            self.assertLessEqual(abs(dict(targets)[0]-8000),60)   # Alive, but still looking there.
        self.idle.look_at_people()
        self.assertEqual(self.idle.mode(),'people')
        self.assertLess(dict(self.idle._head(self.clock(),False)[0])[0],8000)   # Back toward the face.
        self.idle.hold_direction()
        self.clock.wait(31.)
        self.assertEqual(self.idle.mode(),'people')              # Not left staring at a wall.

    def test_own_arm_pose_rests_briefly_before_idle_takes_it_again(self):
        self.execute('startup');self.clock.wait(.6)
        self.idle.posed((8,9,10),own=True)
        phrases=self.run_scheduler(7.)
        self.assertFalse(any({8,9,10}.intersection(channels) for _,channels in phrases))
        phrases=self.run_scheduler(40.)
        self.assertTrue(any({8,9,10}.intersection(channels) for _,channels in phrases))

    def test_talking_preempts_idle_and_joins_before_talking_targets(self):
        self.execute('startup');self.clock.wait(.6)
        reached=threading.Event()
        self.port.on_target=reached.set
        # Keep idle active long enough to exercise interruption, without devices.
        self.owner.wait=lambda seconds:threading.Event().wait(.005)
        self.idle.tick(True);self.clock.wait(2);self.idle.tick(True);self.clock.wait(20);self.idle.tick(True)
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
