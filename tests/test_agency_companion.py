"""Embodied agency and conversation continuity through the production owners."""
from copy import deepcopy
from types import SimpleNamespace

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.proactive import Arrivals, Opportunity
from r3x.robot_servos import RobotServos
from r3x.servos import ServoRequest
from tests.test_agency_plan import Planner, draft
from tests import test_agency_plan
from tests.test_memory_core import MemoryFixture
from tests.test_proactive import face


class CompanionTests(MemoryFixture):
    setup_owner = test_agency_plan.PlanTests.setup_owner
    tick = test_agency_plan.PlanTests.tick

    def test_feedback_survives_uncertain_speaker_and_many_camera_receipts(self):
        self.setup_owner()
        words="I'm okay, but annoyed by the forced music bits. Ask about my day."
        self.session.process_turn(Capture('human','session',bytes(1024),97,100,100),
            Transcript(words,-.01,True),0,None)
        for i in range(20):
            self.session.history.append({'role':'developer','content':'Camera receipt '+str(i)})
        self.tick(self.trace.at+9)
        self.assertIn(words,[m['content'] for m in self.planner.requests[-1]['conversation']])
        self.assertEqual(self.planner.requests[-1]['audience']['faces'],[])

    def test_private_planning_survives_camera_churn_but_audience_speech_does_not(self):
        self.setup_owner()
        rows=[face(100)]
        self.runtime.camera=SimpleNamespace(latest=lambda now:[{**f,'at':now} for f in rows])
        for at in range(96,101):self.runtime.arrivals.observe([face(at)],at)
        def choose(facts,cancel):
            self.assertTrue(self.session.preparing_plan)
            rows[:]=[face(100,track='new-view')]
            cancel.check()
            value=draft();value['scope']='audience'
            return value
        self.planner.choose=choose
        result=self.tick(100)
        self.assertEqual(len(self.planner.requests),1)
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(result.completed_text,[])

    def test_same_audience_survives_long_planning_without_intermediate_poll(self):
        self.setup_owner()
        self.runtime.camera=SimpleNamespace(latest=lambda now:[face(now)])
        for at in range(96,101):self.runtime.arrivals.observe([face(at)],at)
        def choose(facts,cancel):
            self.trace.at+=7;self.runtime.audio_seen_at=self.trace.at
            cancel.check()  # Camera cache has the same face, despite a polling gap.
            value=draft();value['scope']='audience';return value
        self.planner.choose=choose
        self.assertEqual(self.tick(100).status,'completed')
        self.assertEqual(self.state.agency.record['cursor'],1)

    def test_general_plan_continues_after_camera_churn(self):
        self.setup_owner()
        rows=[face(100)]
        self.runtime.camera=SimpleNamespace(latest=lambda now:[{**f,'at':now} for f in rows])
        for at in range(96,101):self.runtime.arrivals.observe([face(at)],at)
        def choose(facts,cancel):
            rows[:]=[face(100,track='new-view')]
            cancel.check()
            return draft()
        self.planner.choose=choose
        self.assertEqual(self.tick(100).status,'completed')
        self.assertEqual(self.state.agency.record['cursor'],1)

    def test_ineligible_background_face_does_not_reprepare_same_plan(self):
        self.setup_owner()
        self.runtime.camera=SimpleNamespace(latest=lambda now:[face(now,width=30)])
        value=draft();value['scope']='audience'
        self.planner.choose=lambda facts,cancel:deepcopy(value)
        self.assertEqual(self.tick(2).status,'prepared')
        self.assertIsNone(self.tick(3))
        self.assertEqual(len(self.planner.requests),1)

    def test_failed_planner_uses_silent_body_recovery_when_available(self):
        from r3x.contracts import ProviderFailure
        from tests.test_attention import AttentionTests
        AttentionTests.setup_session(self)
        planner=Planner()
        def fail(facts,cancel):raise ProviderFailure('SyntheticUnavailable')
        planner.choose=fail;self.session.agency_planner=planner
        result=self.session.process_opportunity(Opportunity('fallback','session',100,None,None,(),float('-inf'),False,'reconsideration'),0,lambda:True)
        self.assertEqual(result.completed_text,[])
        self.assertEqual(self.state.agency.record['steps'][0]['kind'],'look')
        self.assertEqual(self.state.agency.record['cursor'],1)


class PresenceTests(MemoryFixture):
    def test_continuously_visible_face_is_audience_even_when_small_in_full_frame(self):
        arrivals=Arrivals()
        for at in range(5):
            row={**face(at,width=70.58),'box':[.263,.525,.03676,.08905],
                 'learning_quality':False,'needs_closer_look':True}
            arrivals.observe([row],at)
        result=arrivals.audience(4)
        self.assertEqual(len(result),1)
        self.assertFalse(result[0]['learning_quality'])
        self.assertIsNone(result[0]['identity'])


class PhysicalPlanTests(MemoryFixture):
    def perform(self,kind):
        planner=Planner()
        value=draft();value['steps']=[{**value['steps'][0],'kind':kind,'instruction':'One small silent curious movement.'}]
        planner.choose=lambda facts,cancel:deepcopy(value)
        self.session.agency_planner=planner
        result=self.session.process_opportunity(Opportunity('physical','session',self.state.trace.clock(),None,None,(),float('-inf'),False,'reconsideration'),0,lambda:True)
        return result,planner

    def setup_pose(self):
        from tests.test_servos import Clock, PulsePort
        from tests.test_voice_core import Brain
        from r3x.output import OutputOwner
        from r3x.scenario import SyntheticSink,SyntheticTTS
        from r3x.session import Session
        self.clock=Clock();self.port=PulsePort()
        self.robot=RobotServos('fake',open_port=lambda:self.port,clock=self.clock,wait=self.clock.wait)
        self.robot.owner.wait=self.clock.wait
        self.robot.start();self.addCleanup(self.robot.close)
        self.state.trace.clock=self.clock
        self.brain=Brain(ToolCall('pose','set_robot_pose',{'poses':['look_left'],'look_after':None,'say_before':None,'text':None}))
        output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace,robot=self.robot)
        self.session=Session('session',self.brain,output,self.state.trace,self.state)
        self.addCleanup(self.session.close)

    def test_silent_pose_reaches_pulses_without_creating_a_permanent_hold(self):
        self.setup_pose()
        result,planner=self.perform('pose')
        self.assertEqual(result.completed_text,[])
        self.assertIn('pose',planner.requests[0]['capabilities'])
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(self.state.agency.record['outcomes'][0]['executor']['status'],'pulses_reached')
        self.assertEqual(self.robot.owner.held_channels,set())
        self.assertTrue(any(r['event']=='robot_pose_result' for r in self.state.trace.rows))

    def test_silent_pose_survives_its_own_head_camera_reframe(self):
        from r3x.runtime import VoiceRuntime
        self.setup_pose()
        trace=self.state.trace
        class HealthyRuntime(VoiceRuntime):
            @property
            def audio_seen_at(inner):return trace.clock()
            @audio_seen_at.setter
            def audio_seen_at(inner,value):pass
        runtime=HealthyRuntime(None,None,None,self.session,trace)
        now=self.clock();runtime.started_at=now-10
        visible=[face(now)]
        runtime.camera=SimpleNamespace(latest=lambda at:[{**f,'at':at} for f in visible])
        for age in (3,2,1,0):runtime.arrivals.observe([face(now-age)],now-age)
        for entry in runtime.arrivals.seen.values():entry['used']=True
        value=draft();value['scope']='audience'
        value['steps']=[{**value['steps'][0],'kind':'pose','instruction':'Quietly look left.'}]
        planner=Planner();planner.choose=lambda f,c:deepcopy(value)
        self.session.agency_planner=planner
        self.robot.owner.on_camera_motion=lambda moving,*args:visible.clear() if moving else None
        result=runtime.proactive_tick()
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(result.completed_text,[])
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(visible,[])

    def test_repeating_an_unchanged_pose_is_not_completed_agency_or_camera_motion(self):
        self.setup_pose()
        self.perform('pose')
        camera=[]
        self.robot.owner.on_camera_motion=lambda *args:camera.append(args)
        self.state.agency.changed('new_plan')
        result,planner=self.perform('pose')
        self.assertIn('look_left',planner.requests[0]['capability_contract']['pose']['already_at'])
        self.assertEqual(self.state.agency.record['cursor'],0)
        self.assertEqual(self.state.agency.record['outcomes'][0]['executor']['changed_channels'],[])
        self.assertEqual(camera,[])

    def test_autonomous_pose_cannot_override_a_human_hold_at_execution(self):
        self.setup_pose()
        held=self.robot.execute_pose('look_right',Cancellation(),'human')
        self.assertEqual(held.status,'pulses_reached')
        pulse=self.port.pose[0]
        result=self.robot.execute_pose('look_left',Cancellation(),'agency',hold_pose=False)
        self.assertEqual(result.reason,'AgencyCannotOverrideHeldPose')
        self.assertEqual(self.port.pose[0],pulse)
        self.assertIn(0,self.robot.owner.held_channels)
        release=self.robot.execute_pose('release_poses',Cancellation(),'agency',hold_pose=False)
        self.assertEqual(release.status,'rejected')
        self.assertIn(0,self.robot.owner.held_channels)

    def test_drive_plan_executes_early_without_speech_or_visible_person(self):
        from tests.test_drive_session import DriveSessionTests,drive_call,step
        DriveSessionTests.setup_drive(self,drive_call(step('turn',degrees=20),purpose='curious orientation'))
        result,planner=self.perform('drive')
        self.assertEqual(result.completed_text,[])
        self.assertIn('drive',planner.requests[0]['capabilities'])
        self.assertEqual(planner.requests[0]['capability_contract']['drive']['origin'],'self_initiated')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertAlmostEqual(self.fake.theta,20,delta=1)

    def test_blocked_drive_never_counts_as_completed_agency(self):
        from tests.test_drive_session import DriveSessionTests,drive_call,step
        DriveSessionTests.setup_drive(self,drive_call(step('move',meters=.5)))
        self.fake.block_after=.05
        result,planner=self.perform('drive')
        self.assertEqual(result.completed_text,[])
        self.assertEqual(self.state.agency.record['cursor'],0)
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'needs_revision')

    def test_drive_with_post_move_inspection_retains_motion_receipt(self):
        from tests.test_drive_session import DriveSessionTests,drive_call,step
        DriveSessionTests.setup_drive(self,drive_call(step('turn',degrees=20),look_after='What is there?'),
            followup='There is a window.',inspection=True)
        result,planner=self.perform('drive')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(self.state.agency.record['outcomes'][0]['executor']['status'],'completed')
