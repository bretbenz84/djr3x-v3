"""Persistent planning through the production conversation owner; no live I/O."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import json

from r3x.agency import AgencyPlan, audience_key
from r3x.contracts import Cancellation, Capture, Cancelled, ToolCall, Transcript, ProviderFailure
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from r3x.semantics import grounded_tools
from tests import test_memory_core as memory_tests
from tests import test_turn_taking as timing_tests


def draft():
    return {'goal_kind':'entertain','subject':'a waltz for three-legged droids',
            'scope':'general','narrative':'I wonder what a waltz for three-legged droids would sound like.',
            'objective':'Develop a playful musical idea without interrogating anyone.',
            'evidence_ids':[], 'steps':[
                {'kind':'contribute','instruction':'Offer the waltz idea.','evidence_ids':[], 'delay_seconds':5},
                {'kind':'contribute','instruction':'Develop the rhythm idea without repeating the invitation.',
                 'evidence_ids':[], 'delay_seconds':5}]}


class Planner:
    capability = SimpleNamespace(model='test-planner')
    reasoning_effort = 'medium'
    def __init__(self): self.requests=[]; self.images=[]; self.choose=lambda facts,cancel:draft()
    def plan(self,facts,cancel,images=()):
        self.requests.append(deepcopy(facts));self.images.append(images)
        return self.choose(facts,cancel)


class Executor:
    def __init__(self): self.snapshots=[]; self.choose=None
    def reply(self,snapshot,cancel):
        self.snapshots.append(snapshot)
        if self.choose:
            yield from self.choose(snapshot,cancel)
        else:
            yield 'A waltz for three-legged droids has excellent footing.'


class PlanTests(memory_tests.MemoryFixture):
    def setup_owner(self):
        self.trace=self.state.trace
        self.executor=Executor();self.planner=Planner()
        self.session=Session('session',self.executor,OutputOwner(SyntheticTTS(),timing_tests.ClockSink(self.trace),self.trace),self.trace,self.state)
        self.session.agency_planner=self.planner
        self.runtime=VoiceRuntime(SimpleNamespace(),SimpleNamespace(),None,self.session,self.trace)
        self.runtime.started_at=0

    def tick(self,at):
        self.trace.at=at;self.runtime.audio_seen_at=at
        return self.runtime.proactive_tick()

    def test_plan_saved_and_two_steps_execute_without_new_human_or_new_scene(self):
        self.setup_owner()
        first=self.tick(100)
        self.assertEqual(first.status,'completed')
        saved=json.loads(self.store.rows('SELECT body FROM agency_plan')[0]['body'])
        self.assertEqual(saved['cursor'],1)
        self.assertEqual(saved['outcomes'][0]['status'],'completed')
        self.assertEqual(len(self.planner.requests),1)
        self.tick(self.trace.at+9)
        self.assertEqual(len(self.planner.requests),1)
        self.assertEqual(self.state.agency.record['cursor'],2)
        # Two unanswered lines: he eases off. The next goal is prepared but not performed yet.
        self.assertEqual(self.tick(self.trace.at+9).status,'prepared')
        self.assertEqual(len(self.executor.snapshots),2)
        self.tick(self.trace.at+30)
        self.assertEqual(len(self.planner.requests),2)
        self.assertEqual(len(self.executor.snapshots),3)
        self.assertEqual(self.store.claims(),[])
        self.assertEqual(self.store.people(),[])

    def test_human_reply_steers_but_never_discards_or_waits_for_the_goal(self):
        # Live October 1: every "Okay." discarded the plan (40 planner calls in ten minutes).
        self.setup_owner();self.tick(100)
        plan_id=self.state.agency.record['id']
        now=self.trace.at+2
        capture=Capture(uuid4().hex,'session',bytes(96000),now-3,now,now)
        self.session.note_speech(now)
        self.session.process_turn(capture,Transcript('Tell me about jazz instead.',-.01,True),0,memory_tests.observation())
        self.assertEqual(len(self.planner.requests),1)
        goal=self.executor.snapshots[-1].context['agency_plan']
        self.assertEqual(goal['next_beat']['instruction'],'Develop the rhythm idea without repeating the invitation.')
        self.tick(self.trace.at+9)
        self.assertEqual(len(self.planner.requests),1)
        self.assertEqual(self.state.agency.record['id'],plan_id)
        self.assertEqual(self.state.agency.record['cursor'],2)
        self.assertIn('Tell me about jazz instead.',json.dumps(self.executor.snapshots[-1].messages))
        self.tick(self.trace.at+9)
        self.assertEqual(len(self.planner.requests),2)  # Beats used: the next goal hears the redirection.
        self.assertIn('Tell me about jazz instead.',json.dumps(self.planner.requests[-1]))
        self.assertEqual(self.planner.requests[-1]['goal_trigger'],'beats_used')

    def test_restart_requires_planner_revalidation_and_audience_scope(self):
        self.setup_owner();self.tick(100)
        restored=AgencyPlan(self.store,self.trace)
        self.assertTrue(restored.needs_plan('[]'))
        self.assertIsNone(restored.step('[]'))
        self.assertTrue(restored.context('[]')['needs_revalidation'])
        self.assertIsNotNone(restored.context('["person:other"]'))  # Impersonal character plan survives head turns.
        self.state.agency.clear('forget_person')
        self.assertEqual(self.store.rows('SELECT * FROM agency_plan'),[])

    def test_quiet_floor_and_stale_audio_never_call_planner(self):
        self.setup_owner()
        self.state.initiative.quiet=True;self.tick(100)
        self.state.initiative.quiet=False
        self.state.initiative.feedback.append({'action':'yield_floor','expires_at':150})
        self.tick(110)
        self.trace.at=160;self.runtime.audio_seen_at=150;self.runtime.proactive_tick()
        self.assertEqual(self.planner.requests,[])
        self.assertEqual(self.executor.snapshots,[])

    def test_human_speech_during_planning_keeps_the_goal_but_waits_for_a_lull(self):
        self.setup_owner()
        def interrupted(facts,cancel):
            self.session.note_speech(101,sustained=True)
            cancel.check()
            return draft()
        self.planner.choose=interrupted
        result=self.tick(100)
        self.assertEqual(result.status,'prepared')
        self.assertEqual(len(self.store.rows('SELECT * FROM agency_plan')),1)
        self.assertEqual(self.executor.snapshots,[])

    def test_explicit_stop_during_planning_installs_nothing(self):
        self.setup_owner()
        def stopped(facts,cancel):
            self.session.cancel()
            cancel.check()
            return draft()
        self.planner.choose=stopped
        self.tick(100)
        self.assertEqual(self.store.rows('SELECT * FROM agency_plan'),[])
        self.assertEqual(self.executor.snapshots,[])

    def test_invalid_evidence_gets_one_repair_then_character_step_not_silence(self):
        self.setup_owner()
        def invalid(facts,cancel):
            value=draft();value['evidence_ids']=['invented-photo'];return value
        self.planner.choose=invalid
        result=self.tick(100)
        self.assertEqual(result.status,'completed')
        self.assertEqual(len(self.planner.requests),2)
        self.assertEqual(self.planner.requests[-1]['repair']['validation_error'],'AgencyEvidenceNotOffered')
        self.assertEqual(self.state.agency.record['origin'],'recovery')
        self.assertEqual(self.state.agency.record['evidence_ids'],[])
        self.assertEqual(self.state.agency.record['cursor'],1)

    def test_revision_abandons_bad_step_and_delivers_a_character_contribution(self):
        self.setup_owner()
        self.executor.choose=lambda snapshot,cancel:iter(['A tuba is a brass instrument with an alibi.'] if
            snapshot.context['agency_step']['origin']=='recovery' else
            [ToolCall('revision','revise_agency_plan',{'reason':'The observation no longer supports this premise.'})])
        result=self.tick(100)
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(self.state.agency.record['previous_outcomes'][-1]['status'],'skipped')
        self.assertEqual(self.state.agency.goals[-1]['ended'],'revised')
        names={tool['name'] for tool in grounded_tools(self.executor.snapshots[-1].context)}
        self.assertNotIn('revise_agency_plan',names)
        self.assertEqual(len(self.executor.snapshots),2)

    def test_planned_execution_tools_do_not_offer_generic_silence_or_wheels(self):
        self.setup_owner();self.tick(100)
        context=self.executor.snapshots[0].context
        names={x['name'] for x in grounded_tools(context)}
        # Only a goal about a person can lose its premise; an impersonal beat is simply done.
        self.assertEqual(names,{'respond_with_intent'} if context['agency_step']['scope']=='general'
                         else {'respond_with_intent','revise_agency_plan'})

    def test_conversation_bursts_and_feeds_do_not_exhaust_a_rolling_attempt_budget(self):
        self.setup_owner()
        for i in range(16):
            self.state.agency.observe_speech(None,self.trace.at)  # Someone is around.
            self.tick(max(100,self.trace.at+9))
        self.assertEqual(len(self.executor.snapshots),16)
        self.assertEqual(len(self.planner.requests),8)

    def test_alone_for_five_minutes_pauses_goals_until_someone_appears(self):
        self.setup_owner()
        self.assertIsNone(self.tick(400))
        self.assertEqual(self.planner.requests,[])
        self.assertEqual(self.trace.rows[-1]['reason'],'nobody_around')
        self.state.agency.observe_faces([{'track_id':'t','identity':None,'at':401}],401)
        self.assertEqual(self.tick(410).status,'completed')
        self.assertEqual(len(self.planner.requests),1)


    def test_provider_failure_does_not_block_character_activity_during_retry_wait(self):
        self.setup_owner()
        def fail(facts,cancel): raise ProviderFailure('SyntheticPlannerUnavailable')
        self.planner.choose=fail
        self.assertEqual(self.tick(100).status,'completed')
        self.assertEqual(len(self.executor.snapshots),1)
        finished=self.trace.at
        self.assertGreater(self.state.agency.planner_retry_at,100)
        self.state.agency.planner_retry_at=finished+30
        self.tick(finished+2);self.assertEqual(len(self.planner.requests),1)
        self.assertEqual(self.state.agency.record['origin'],'recovery')
        self.planner.choose=lambda f,c:draft()
        self.assertEqual(self.tick(finished+9).status,'completed')
        self.assertEqual(len(self.executor.snapshots),2)
        self.assertEqual(len(self.planner.requests),1)  # Prepared character step is still delivered.
        self.tick(finished+31)
        self.assertEqual(len(self.planner.requests),2)

    def test_malformed_saved_record_is_discarded_before_planning(self):
        with self.store.transaction() as db:
            db.execute('INSERT INTO agency_plan VALUES(1,1,?)',(json.dumps({'steps':[]}),))
        self.setup_owner();self.tick(100)
        self.assertEqual(len(self.planner.requests),1)
        self.assertIsNone(self.planner.requests[0]['current_goal'])
        self.assertEqual(self.planner.requests[0]['recent_goals'],[])

    def test_session_restart_keeps_plan_but_revalidates_before_speaking(self):
        self.setup_owner();self.tick(100)
        plan_id=self.state.agency.record['id']
        self.session.close()
        from r3x.state import CoreState
        from r3x.speakers import SpeakerTracker
        self.state=CoreState(self.store,SpeakerTracker(self.state.tracker.namespace),self.trace)
        self.setup_owner();self.tick(200)
        self.assertEqual(self.planner.requests[0]['goal_trigger'],'restart')
        self.assertEqual(self.planner.requests[0]['current_goal']['id'],plan_id)
        self.assertTrue(self.planner.requests[0]['current_goal']['needs_revalidation'])
        self.assertEqual(self.planner.requests[0]['recent_goals'][-1]['ended'],'restart')
        self.assertNotEqual(self.state.agency.record['id'],plan_id)

    def test_revision_reason_and_heard_outcomes_reach_the_next_planner(self):
        self.setup_owner();self.tick(100)
        self.executor.choose=lambda s,c:iter(['I have a new musical idea.'] if
            s.context['agency_step']['origin']=='recovery' else
            [ToolCall('r','revise_agency_plan',{'reason':'That object is no longer available.'})])
        self.tick(self.trace.at+9)
        self.executor.choose=None
        self.tick(self.trace.at+9)
        prior=self.planner.requests[-1]['current_goal']
        self.assertTrue(prior['previous_outcomes'][0]['heard_text'])
        self.assertEqual(prior['previous_outcomes'][1]['revision_reason'],'That object is no longer available.')

    def test_explicit_stop_and_correction_erase_persisted_character_notes(self):
        self.setup_owner();self.tick(100)
        self.session.cancel()
        self.assertEqual(self.store.rows('SELECT * FROM agency_plan'),[])
        self.state.agency.install(draft(),'[]',{},['contribute'],self.state.agency.revision,200)
        self.state.conversation_intent.clear('correct_claim')
        self.assertEqual(self.store.rows('SELECT * FROM agency_plan'),[])

    def test_report_separates_planning_execution_and_actual_step_outcomes(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from r3x.trace import report
        self.setup_owner();self.tick(100);self.tick(self.trace.at+9)
        with TemporaryDirectory() as folder:
            path=Path(folder)/'trace.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in self.trace.rows))
            result=report(path)
        self.assertEqual(result['model_calls_by_purpose']['agency_planning'],1)
        self.assertEqual(result['primary_calls'],2)
        self.assertEqual(result['agency_step_outcomes'],{'completed':2})
        self.assertEqual(result['observed_seconds']['planning_seconds']['n'],1)

    def test_unanswered_invitation_does_not_block_later_nonquestion_step(self):
        from tests.test_invitations import offer
        self.setup_owner()
        def invitation(snapshot,cancel):
            call=offer(snapshot.context)
            call.arguments['next_opportunity']['delay_seconds']=60
            return iter([call])
        self.executor.choose=invitation
        self.tick(100)
        pending=self.state.initiative.invitations.pending
        self.assertIsNotNone(pending)
        self.executor.choose=None
        self.tick(self.trace.at+9)
        self.assertEqual(len(self.executor.snapshots),2)
        self.assertEqual(self.state.agency.record['cursor'],2)
        self.assertEqual(self.state.initiative.invitations.pending['id'],pending['id'])

    def test_normal_shutdown_preserves_plan_even_with_active_conversation_intention(self):
        from tests.test_conversation_intent import proposal
        self.setup_owner();self.tick(100)
        saved=self.state.agency.record['id']
        self.executor.choose=lambda s,c:iter([ToolCall('intent','respond_with_intent',{
            'text':'Let us explore jazz.', 'intent_update':proposal(s.context,target_ids=[])})])
        now=self.trace.at+2
        capture=Capture(uuid4().hex,'session',bytes(96000),now-3,now,now)
        result=self.session.process_turn(capture,Transcript('Let us explore jazz.',-.01,True),0,memory_tests.observation())
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.state.conversation_intent.record.status,'active')
        self.session.close()
        self.assertEqual(json.loads(self.store.rows('SELECT body FROM agency_plan')[0]['body'])['id'],saved)

    def test_rejected_intention_cannot_speak_promise_or_advance(self):
        from tests.test_conversation_intent import proposal
        self.setup_owner()
        def choose(snapshot,cancel):
            if snapshot.continuation:
                return iter(['That idea needs revising.'])
            return iter([ToolCall('bad','respond_with_intent',{'text':'I will inspect that plant.',
                'intent_update':proposal(snapshot.context,source_id='unoffered')})])
        self.executor.choose=choose
        result=self.tick(100)
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.completed_text,[])
        # The failed beat is skipped, not repeated; the goal goes on.
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'skipped')
        self.assertEqual(self.state.agency.record['outcomes'][0]['revision_reason'],'IntentSourceMismatch')

    def test_head_turn_changes_view_but_keeps_general_plan_and_next_step(self):
        self.setup_owner();self.tick(100)
        saved=self.state.agency.record['id']
        next_step=self.state.agency.step('["track:new-camera-track"]')
        self.assertEqual(next_step['plan_id'],saved)
        self.assertEqual(next_step['index'],1)
        self.assertFalse(self.state.agency.needs_plan('["track:new-camera-track"]'))

    def test_general_step_cannot_select_a_private_visual_memory(self):
        self.setup_owner();self.tick(100)
        context=deepcopy(self.executor.snapshots[0].context)
        context['visual_memory']=[{'id':'private','audience':'["person:one"]'}]
        names={tool['name'] for tool in grounded_tools(context)}
        self.assertNotIn('use_visual_memory',names)
        self.assertNotIn('use_memory_opportunity',names)

    def test_personal_goal_survives_track_churn_but_ends_when_its_person_leaves(self):
        plan=draft();plan['scope']='audience'
        agency=self.state.agency;agency.load()
        with self.assertRaisesRegex(ValueError,'AgencyAudienceNotPresent'):
            # A goal for someone who is not here could never run.
            agency.install(plan,'["person:one"]',{},['contribute'],agency.revision,100,subject_person='one')
        agency.observe_faces([{'track_id':'a','identity':'one','at':100}],100)
        agency.install(plan,'["person:one"]',{},['contribute'],agency.revision,100,subject_person='one')
        self.assertIsNotNone(agency.step(100))
        # A renumbered track for the same person is the same visit.
        agency.observe_faces([{'track_id':'b','identity':'one','at':130}],130)
        self.assertIsNotNone(agency.step(150))
        # Someone else in view while the subject is gone past the grace period: withheld.
        agency.observe_faces([{'track_id':'c','identity':'two','at':200}],200)
        self.assertIsNone(agency.context(200))
        self.assertIsNone(agency.step(200))
        self.assertEqual(agency.ending_reason(200),'subject_left')

    def test_personal_evidence_cannot_be_saved_as_general_plan(self):
        self.setup_owner()
        plan=draft();plan['evidence_ids']=['private']
        self.state.agency.observe_speech('one',100)
        self.state.agency.install(plan,'["person:one"]',{'private':{'kind':'memory_opportunities'}},
            ['contribute'],self.state.agency.revision,100,subject_person='one')
        self.assertEqual(self.state.agency.record['scope'],'audience')
        self.assertIsNotNone(self.state.agency.step(110))
        self.assertIsNone(self.state.agency.step(300))

    def test_private_preparation_does_not_count_as_speech_or_consume_history(self):
        self.setup_owner()
        history=self.session.history_messages()
        self.assertEqual(self.tick(2).status,'prepared')
        self.assertEqual(len(self.planner.requests),1)
        self.assertEqual(self.executor.snapshots,[])
        self.assertEqual(self.session.last_interaction_at,float('-inf'))
        self.assertEqual(self.session.history_messages(),history)
        self.assertFalse(any(r['event']=='proactive_outcome' for r in self.trace.rows))
        self.assertIsNone(self.tick(3))
        self.assertEqual(self.tick(8).status,'completed')
        self.assertEqual(len(self.planner.requests),1)

    def test_stop_during_plan_repair_cannot_install_degraded_work(self):
        self.setup_owner()
        def choose(facts,cancel):
            if 'repair' in facts:
                self.session.cancel()
                cancel.check()
            value=draft();value['evidence_ids']=['invented'];return value
        self.planner.choose=choose
        result=self.tick(2)
        self.assertEqual(result.status,'prepared')
        self.assertEqual(len(self.planner.requests),2)
        self.assertEqual(self.executor.snapshots,[])
        self.assertIsNone(self.state.agency.record)
        self.assertEqual(self.session.last_interaction_at,float('-inf'))

    def test_human_speech_during_revision_recovery_stops_without_speaking(self):
        self.setup_owner()
        def choose(snapshot,cancel):
            if snapshot.context['agency_step']['origin']=='recovery':
                self.session.note_speech(101,sustained=True)
                cancel.check()
            return iter([ToolCall('r','revise_agency_plan',{'reason':'Object no longer available.'})])
        self.executor.choose=choose
        result=self.tick(100)
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(result.completed_text,[])
        # The improvised beat yielded to the human; it is retried at the next lull.
        self.assertEqual(self.state.agency.record['origin'],'recovery')
        self.assertEqual(self.state.agency.record['cursor'],0)
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'interrupted')
        self.assertEqual(self.state.agency.record['previous_outcomes'][-1]['status'],'skipped')

    def test_repaired_evidence_plan_executes_without_degraded_mode(self):
        self.setup_owner()
        def choose(facts,cancel):
            value=draft()
            if 'repair' not in facts:value['steps'][0]['evidence_ids']=['invented']
            return value
        self.planner.choose=choose
        self.assertEqual(self.tick(100).status,'completed')
        self.assertEqual(len(self.planner.requests),2)
        self.assertEqual(self.state.agency.record['origin'],'planner')

    def test_realistic_camera_receipt_and_target_refs_are_scoped_and_executed(self):
        from r3x.proactive import Opportunity
        from r3x.scene import SceneImage
        self.setup_owner()
        self.session.output.drive=SimpleNamespace(context=lambda *a,**kw:{'available':False,
            'targets':[{'target_id':'object:cup','kind':'object','label':'cup'}]})
        def choose(facts,cancel):
            value=draft()
            selected=[key for key,row in facts['evidence'].items() if row['kind'] in {'camera','current_target'}]
            self.assertEqual(len(selected),2)
            value['steps'][0]['evidence_ids']=selected
            return value
        self.planner.choose=choose
        self.trace.at=100
        event=Opportunity('mixed','session',100,None,None,(),float('-inf'),False,'reconsideration',
            scene_id='scene',scene_images=(SceneImage('frame',100,'data:image/png;base64,synthetic'),))
        result=self.session.process_opportunity(event,0,lambda:True)
        self.assertEqual(result.status,'completed')
        self.assertEqual(len(self.planner.requests),1)
        self.assertEqual(self.state.agency.record['scope'],'general')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertIsNotNone(self.state.agency.context('["person:new"]'))

    def test_simulated_gesture_receipt_advances_but_failed_gesture_does_not(self):
        from r3x.performance import SimulatedGesture
        self.setup_owner()
        gestures=SimulatedGesture(self.trace.clock);gestures.start()
        self.session.output.gestures=gestures
        def choose(facts,cancel):
            value=draft();value['steps'][0]['kind']='gesture';return value
        self.planner.choose=choose
        self.executor.choose=lambda s,c:iter([ToolCall('g','respond_with_gesture',{
            'text':'That rhythm deserves a nod.','primitive':'nod','target':'forward',
            'intensity':'subtle','timing':'after_speech'})])
        result=self.tick(100)
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(result.gestures[0]['medium'],'simulation')
        self.assertEqual(result.gestures[0]['status'],'simulated')
        self.state.agency.changed('new_idea');gestures.close()
        result=self.tick(self.trace.at+9)
        self.assertEqual(self.state.agency.record['cursor'],1)  # Skipped; never counted as done.
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'skipped')

    def test_rejected_optional_note_recovers_actual_human_reply(self):
        from tests.test_conversation_intent import proposal
        self.setup_owner()
        def choose(snapshot,cancel):
            if snapshot.continuation:return iter(['I meant their enormous mirrors.'])
            return iter([ToolCall('bad','respond_with_intent',{'text':'I will inspect them.',
                'intent_update':proposal(snapshot.context,source_id='unoffered')})])
        self.executor.choose=choose
        result=self.session.process_turn(Capture('human','session',bytes(96000),97,100,100),
            Transcript('Okay, what about them?',-.01,True),0,memory_tests.observation())
        self.assertEqual(result.status,'completed')
        self.assertEqual(' '.join(result.completed_text),'I meant their enormous mirrors.')
        self.assertEqual(len(self.executor.snapshots),2)
        self.assertEqual(self.planner.requests,[])


class GoalTests(memory_tests.MemoryFixture):
    """October 2 owner direction: goals from noticing, steered by conversation, people first."""
    setup_owner=PlanTests.setup_owner
    tick=PlanTests.tick

    def visible(self, person, track, at):
        from tests.test_proactive import face
        self.runtime.camera=SimpleNamespace(latest=lambda now:[face(now,person,track)])
        for t in range(int(at)-4,int(at)+1):self.runtime.arrivals.observe([face(t,person,track)],t)

    def test_arrival_is_greeted_first_then_becomes_the_next_goal(self):
        self.setup_owner()
        person=self.enroll(name='Bret')
        self.visible(person,'bret',100)
        self.tick(100)
        greeting=self.executor.snapshots[0].context
        self.assertEqual(greeting['trigger'],'visual_arrival')
        self.assertNotIn('agency_step',greeting)
        self.assertEqual(self.planner.requests,[])  # A greeting never waits for a planner.
        self.assertEqual(self.state.agency.ending,'person_arrived')
        self.tick(self.trace.at+9)
        request=self.planner.requests[0]
        self.assertEqual(request['goal_trigger'],'person_arrived')
        self.assertEqual(request['subject']['person_id'],person)
        self.assertEqual(request['subject']['display_name'],'Bret')
        self.assertEqual(self.executor.snapshots[-1].context['agency_step']['index'],0)

    def test_planner_sees_the_current_camera_frame(self):
        from r3x.scene import SceneImage
        self.setup_owner()
        self.runtime.scene_observer=SimpleNamespace(
            snapshot=lambda *a:('scene',(SceneImage('frame',self.trace.at,'data:image/png;base64,synthetic'),)),
            attempted=lambda scene_id:None)
        self.tick(100)
        self.assertEqual([i.frame_id for i in self.planner.images[0]],['frame'])
        self.assertTrue(self.planner.requests[0]['room']['image_attached'])

    def test_planning_runs_beside_conversation_without_delaying_a_reply(self):
        import threading
        self.setup_owner()
        started,release=threading.Event(),threading.Event()
        def slow(facts,cancel):
            started.set();release.wait(5);return draft()
        self.planner.choose=slow
        self.assertEqual(self.tick(100).status,'prepared')
        self.assertTrue(started.wait(2))
        self.assertTrue(self.session.planning_active())
        now=self.trace.at+1
        capture=Capture(uuid4().hex,'session',bytes(96000),now-3,now,now)
        reply=self.session.process_turn(capture,Transcript('Rex, what are you up to?',-.01,True),0,memory_tests.observation())
        self.assertEqual(reply.status,'completed')  # Answered while the planner was still working.
        release.set();self.session.join_planning()
        self.assertFalse(self.session.planning_active())
        self.assertEqual(self.state.agency.record['cursor'],0)
        self.assertEqual(len(self.planner.requests),1)

    def test_unheard_beat_yields_to_people_then_is_retried_once_then_skipped(self):
        self.setup_owner()
        def interrupted(snapshot,cancel):
            self.session.note_speech(self.trace.at,sustained=True)
            cancel.check()
            yield 'Never heard.'
        self.executor.choose=interrupted
        self.assertEqual(self.tick(100).status,'interrupted')
        record=self.state.agency.record
        self.assertEqual((record['cursor'],record['outcomes'][-1]['status']),(0,'interrupted'))
        self.assertEqual(self.tick(self.trace.at+20).status,'interrupted')
        self.assertEqual((record['cursor'],record['outcomes'][-1]['status']),(1,'skipped'))
        self.executor.choose=None
        self.assertEqual(self.tick(self.trace.at+20).status,'completed')
        self.assertEqual(len(self.planner.requests),1)  # One goal throughout.

    def test_word_for_word_repeat_gets_one_retry_then_fails_the_beat(self):
        # Hosted probe October 2: a challenge beat was delivered as the previous line, verbatim.
        self.setup_owner()
        line='Running socks are clearly premium loot.'
        self.executor.choose=lambda s,c:iter([ToolCall('a','respond_with_intent',{'text':line,'intent_update':None})])
        self.tick(100)
        replies=iter([line,'Ten-second no-smiling challenge. Starting now.'])
        self.executor.choose=lambda s,c:iter([ToolCall('b','respond_with_intent',{'text':next(replies),'intent_update':None})])
        result=self.tick(self.trace.at+9)
        self.assertEqual(result.completed_text,['Ten-second no-smiling challenge. Starting now.'])
        self.assertIn('new words',self.executor.snapshots[-1].messages[-1]['content'])
        self.state.agency.observe_speech(None,self.trace.at)
        self.executor.choose=lambda s,c:iter([ToolCall('c','respond_with_intent',{'text':'Ten-second no-smiling challenge. Starting now.','intent_update':None})])
        result=self.tick(self.trace.at+9)
        self.assertEqual(result.completed_text,[])
        self.assertEqual(result.error,'AgencyBeatRepeatedRecentLine')

    def test_catch_up_beats_get_no_camera_frame_but_curiosity_beats_do(self):
        from r3x.scene import SceneImage
        self.setup_owner()
        self.runtime.scene_observer=SimpleNamespace(
            snapshot=lambda *a:('scene',(SceneImage('frame',self.trace.at,'data:image/png;base64,synthetic'),)),
            attempted=lambda scene_id:None)
        self.state.observation_available=lambda cue:True
        self.tick(100)
        self.assertEqual(self.executor.snapshots[-1].images,())  # draft() is an entertain goal.
        self.state.agency.changed('new_goal')
        self.planner.choose=lambda f,c:{**draft(),'goal_kind':'investigate'}
        self.tick(self.trace.at+9)
        self.assertEqual([i.frame_id for i in self.executor.snapshots[-1].images],['frame'])

    def test_improvised_beat_stays_general_when_rex_has_wandered_away_from_everyone(self):
        # Hosted probe October 2: after driving off, the fallback beat was scoped to an absent
        # audience, had no runnable beat and crashed the turn.
        from r3x.contracts import ProviderFailure
        self.setup_owner()
        self.trace.at=40  # Someone spoke a minute ago; nobody seen or heard since.
        self.session.process_turn(Capture(uuid4().hex,'session',bytes(96000),37,40,40),
            Transcript('I will be in the kitchen.',-.01,True),0,memory_tests.observation())
        self.assertIsNotNone(self.state.current)
        def fail(facts,cancel): raise ProviderFailure('SyntheticUnavailable')
        self.planner.choose=fail
        self.trace.at=100
        result=self.session.process_opportunity(__import__('r3x.proactive',fromlist=['Opportunity']).Opportunity(
            'alone','session',100,None,None,(),float('-inf'),False,'reconsideration'),0,lambda:True)
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.state.agency.record['scope'],'general')

    def test_ignored_rex_talks_less_but_keeps_doing(self):
        # Owner, October 3: with nobody answering he sat "not doing anything for what feels
        # like forever". Easing off now delays spoken beats only; a silent move is not delayed,
        # the performer is told to keep it wordless, and the planner hears how his lines landed.
        from r3x.agency import SILENT_KINDS
        from r3x.output import Delivery
        self.setup_owner()
        agency=self.state.agency
        steps=[{'kind':'contribute','instruction':'Tease.','evidence_ids':[],'delay_seconds':4},
               {'kind':'pose','instruction':'Look left, silently.','evidence_ids':[],'delay_seconds':4},
               {'kind':'contribute','instruction':'Opine.','evidence_ids':[],'delay_seconds':4}]
        agency.install({**draft(),'steps':steps},'[]',{},['contribute','pose'],agency.revision,100)
        agency.unanswered=3
        self.assertEqual(agency.engagement()['unanswered_lines'],3)
        heard=Delivery(status='completed',medium='audio');heard.completed_text=['A line.']
        agency.finish(agency.step(100),heard,None,100)
        self.assertIn('pose',SILENT_KINDS)
        self.assertEqual(agency.due_at,104)                    # The move comes after its breathing room only.
        self.assertEqual(agency.step(104)['unanswered_lines'],4)
        moved=Delivery(status='suppressed',medium='none')
        agency.finish(agency.step(104),moved,{'kind':'set_robot_pose','executor':{'status':'pulses_reached','changed_channels':[0]}},104)
        self.assertEqual(agency.unanswered,4)                  # A silent move is not another unanswered line.
        self.assertEqual(agency.due_at,104+4+agency.ease_seconds())   # Talking again waits.
        self.assertEqual(agency.ease_seconds(),40.)
        agency.unanswered=20
        self.assertEqual(agency.ease_seconds(),120.)           # Two minutes at most; it was five.

    def test_an_unheard_beat_cut_off_by_a_sound_is_retried_within_seconds(self):
        # Live October 3: a noise cancelled a beat and its 15-second breathing room was then waited.
        from r3x.output import Delivery
        self.setup_owner()
        agency=self.state.agency
        steps=[{'kind':'contribute','instruction':'Ask.','evidence_ids':[],'delay_seconds':15}]
        agency.install({**draft(),'steps':steps},'[]',{},['contribute'],agency.revision,100)
        agency.finish(agency.step(100),Delivery(status='interrupted',medium='none'),None,100)
        self.assertEqual(agency.record['outcomes'][-1]['status'],'interrupted')
        self.assertEqual(agency.due_at,104)

    def test_planner_is_told_how_his_lines_landed_and_to_look_at_people_first(self):
        from r3x.agency import PLANNER_INSTRUCTIONS
        self.setup_owner();self.tick(100)
        self.assertEqual(self.planner.requests[0]['engagement']['unanswered_lines'],0)
        for phrase in ('LOOK AT THE PEOPLE FIRST','NOT wearing','out of the ordinary','never twice running',
                       'completely silent','pace (slow, easy, normal)','look_at_people','unanswered_lines'):
            self.assertIn(phrase,PLANNER_INSTRUCTIONS)

    def test_unanswered_lines_ease_off_and_any_reply_restores_the_pace(self):
        # Review October 3: with a face in view he spoke every 15-25 seconds indefinitely.
        self.setup_owner()
        agency=self.state.agency
        gaps=[]
        self.tick(100)
        for _ in range(6):
            agency.observe_faces([{'track_id':'t','identity':None,'at':self.trace.at}],self.trace.at)
            before=len(self.executor.snapshots); start=self.trace.at
            while len(self.executor.snapshots)==before:
                agency.observe_faces([{'track_id':'t','identity':None,'at':self.trace.at}],self.trace.at)
                self.tick(self.trace.at+5)
            gaps.append(self.trace.at-start)
        self.assertLess(gaps[0],20)
        self.assertGreater(gaps[-1],100)
        self.assertEqual(gaps,sorted(gaps))
        agency.observe_speech(None,self.trace.at)
        self.assertEqual(agency.unanswered,0)
        self.assertLessEqual(agency.due_at,self.trace.at)

    def test_unrecognized_presence_continues_a_visit_only_for_a_bounded_time(self):
        agency=self.state.agency;agency.load()
        agency.observe_faces([{'track_id':'a','identity':'one','at':100}],100)
        # Live sessions: recognition gaps of up to 50 seconds while the owner stayed put.
        agency.observe_faces([{'track_id':'b','identity':None,'at':160}],160)
        self.assertTrue(agency.present('one',160))
        # Still an unrecognized face four minutes later: no longer assumed to be that person.
        agency.observe_faces([{'track_id':'c','identity':None,'at':340}],340)
        self.assertFalse(agency.present('one',340))
        # Someone else recognized ends it at once.
        agency.observe_faces([{'track_id':'d','identity':'two','at':150}],150)
        self.assertFalse(agency.present('one',160))

    def test_yielding_the_floor_pauses_the_goal_without_discarding_it(self):
        # Review October 3: every time people talked to each other the goal was thrown away.
        self.setup_owner();self.tick(100)
        plan_id=self.state.agency.record['id']
        self.state.initiative.cancel_schedule('human_floor')  # What a recorded yield_floor does.
        self.state.initiative.feedback.append({'action':'yield_floor','expires_at':self.trace.at+120})
        self.assertEqual(self.state.agency.record['id'],plan_id)
        self.assertFalse(self.state.agency.dirty)
        self.assertIsNone(self.tick(self.trace.at+30))  # Held while the floor is theirs.
        self.assertEqual(self.tick(self.trace.at+130).status,'completed')
        self.assertEqual(len(self.planner.requests),1)

    def test_goal_about_one_person_is_not_shown_on_someone_elses_turn(self):
        agency=self.state.agency;agency.load()
        agency.observe_faces([{'track_id':'a','identity':'one','at':100}],100)
        plan=draft();plan['scope']='audience'
        agency.install(plan,'["person:one"]',{},['contribute'],agency.revision,100,subject_person='one')
        self.assertIsNotNone(agency.summary(101,audience_person='one',recognized=['one']))
        self.assertIsNone(agency.summary(101,audience_person=None,recognized=[None]))   # A stranger's turn.
        self.assertIsNone(agency.summary(101,audience_person='two',recognized=['two']))
        general=draft()
        agency.install(general,'[]',{},['contribute'],agency.revision,102)
        self.assertIsNotNone(agency.summary(103,audience_person=None,recognized=[None]))

    def test_planner_crash_backs_off_instead_of_retrying_every_few_seconds(self):
        self.setup_owner()
        def crash(facts,cancel): raise RuntimeError('unexpected')
        self.planner.choose=crash
        self.tick(100);self.session.join_planning()
        self.assertGreaterEqual(self.state.agency.planner_retry_at,110)
        self.assertTrue(any(r['event']=='agency_planning_crashed' for r in self.trace.rows))

    def test_be_quiet_lasts_until_invited_back_but_stop_is_a_short_pause(self):
        # Review October 3: "be quiet" bought two minutes before the next unprompted line.
        self.setup_owner();self.tick(100)
        self.session.cancel()  # A plain "stop".
        self.assertFalse(self.state.initiative.quiet)
        self.state.agency.observe_speech(None,self.trace.at)
        self.assertIsNone(self.tick(self.trace.at+60))            # Paused for two minutes...
        self.assertIsNotNone(self.tick(self.trace.at+70))         # ...then he resumes.
        calls=len(self.executor.snapshots)
        self.session.cancel(quiet=True)  # "Be quiet."
        self.assertTrue(self.state.initiative.quiet)
        for wait in (130,600,3000):
            self.state.agency.observe_speech(None,self.trace.at)
            self.assertIsNone(self.tick(self.trace.at+wait))
        self.assertEqual(len(self.executor.snapshots),calls)
        self.assertTrue(any(r['event']=='initiative_quiet' for r in self.trace.rows))
        self.state.initiative.quiet=False  # What set_initiative(available) does on "you can talk again."
        self.state.agency.observe_speech(None,self.trace.at)
        self.assertIsNotNone(self.tick(self.trace.at+20))

    def test_planner_gets_the_current_frame_even_when_the_view_was_already_offered(self):
        from r3x.scene import SceneImage
        self.setup_owner()
        asked=[]
        def snapshot(include_offered=False):
            asked.append(include_offered)
            # The real observer withholds an already-offered or unchanged view.
            return ('scene',(SceneImage('frame',self.trace.at,'data:image/png;base64,synthetic'),)) if include_offered else None
        self.runtime.scene_observer=SimpleNamespace(snapshot=snapshot,attempted=lambda scene_id:None)
        self.tick(100)
        self.assertIn(True,asked)
        self.assertEqual([i.frame_id for i in self.planner.images[0]],['frame'])

    def test_drop_it_ends_the_goal_but_keeps_it_as_context_for_the_next(self):
        self.setup_owner();self.tick(100)
        self.state.initiative.cancel_schedule('participant_boundary')
        self.assertEqual(self.state.agency.ending,'participant_boundary')
        self.tick(self.trace.at+9)
        request=self.planner.requests[-1]
        self.assertEqual(request['goal_trigger'],'participant_boundary')
        self.assertEqual(request['recent_goals'][-1]['ended'],'participant_boundary')
        self.assertEqual(request['recent_goals'][-1]['subject'],'a waltz for three-legged droids')


class PlannedActionTests(memory_tests.MemoryFixture):
    def planned(self,kind):
        planner=Planner()
        def choose(facts,cancel):
            value=draft();value['steps'][0]['kind']=kind
            value['steps'][0]['instruction']='Look left at a bounded direction.' if kind=='look' else 'Inspect the view for a concrete detail.'
            return value
        planner.choose=choose;self.session.agency_planner=planner
        from r3x.proactive import Opportunity
        return self.session.process_opportunity(Opportunity('plan','session',100,None,None,(),float('-inf'),False,'reconsideration'),self.session.revision,lambda:True)

    def test_inspection_step_advances_only_after_acquisition_and_result_delivery(self):
        from tests import test_inspection
        test_inspection.InspectionTests.setup_session(self)
        result=self.planned('inspect')
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.events,['move','settle','capture'])
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertTrue(self.state.agency.record['outcomes'][0]['executor']['images_acquired'])
        self.assertEqual(len(self.brain.snapshots),2)

    def test_blocked_inspection_reply_does_not_masquerade_as_completed_action(self):
        from tests import test_inspection
        test_inspection.InspectionTests.setup_session(self)
        self.adapter.move=lambda r,c,d:test_inspection.MovementReceipt(r.request_id,r.direction,r.view_epoch,100,'blocked')
        result=self.planned('inspect')
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'skipped')
        self.assertFalse(self.state.agency.record['outcomes'][0]['executor']['images_acquired'])

    def test_look_uses_output_owner_and_retains_simulation_receipt(self):
        from tests import test_attention
        test_attention.AttentionTests.setup_session(self)
        result=self.planned('look')
        self.assertEqual(result.medium,'none')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(self.state.agency.record['outcomes'][0]['executor']['outcome'],'simulated')

    def test_spoken_promise_cannot_substitute_for_planned_physical_action(self):
        from tests import test_inspection
        test_inspection.InspectionTests.setup_session(self)
        self.brain.reply=lambda s,c:iter(['I will look behind me.'])
        result=self.planned('inspect')
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.completed_text,[])
        self.assertEqual(self.events,[])
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'skipped')


class PlannerAdapterTests(memory_tests.unittest.TestCase):
    def test_planner_forces_private_structured_plan_and_keeps_selected_model(self):
        from r3x.adapters.hosted import OpenAIAgencyPlanner
        from tests.test_adapters import Client, Response
        from r3x.contracts import Cancellation
        response=Response([{'type':'response.output_item.done','item':{
            'type':'function_call','call_id':'plan','name':'compose_agency_plan','arguments':json.dumps(draft())}},
            {'type':'response.completed','response':{}}])
        from r3x.settings import Settings
        planner=OpenAIAgencyPlanner('fake-key',Settings().agency_planner_model)
        planner.client=Client(response)
        result=planner.plan({'request_id':'p','revision':0,'evidence':{}},Cancellation())
        self.assertEqual(result,draft())
        payload=planner.client.calls[0][1]['json']
        self.assertEqual(payload['model'],'gpt-5.6-terra')
        from r3x.adapters.hosted import CHARACTER
        self.assertTrue(payload['instructions'].startswith(CHARACTER))
        self.assertEqual(payload['tool_choice'],{'type':'function','name':'compose_agency_plan'})
        self.assertFalse(payload['store'])
        self.assertEqual([t['name'] for t in payload['tools']],['compose_agency_plan'])

    def reply_payload(self, context):
        from r3x.adapters.hosted import OpenAIBrain
        from tests.test_adapters import Client, Response
        from r3x.contracts import Snapshot, Cancellation
        response=Response([{'type':'response.output_item.done','item':{
            'type':'function_call','call_id':'quiet','name':'remain_silent','arguments':json.dumps({'reason':'other_addressee'})}},
            {'type':'response.completed','response':{}}])
        brain=OpenAIBrain('fake-key');brain.client=Client(response)
        list(brain.reply(Snapshot('s','t',0,({'role':'user','content':'Hey Rex.'},),context=context,tools_enabled=True),Cancellation()))
        payload=brain.client.calls[0][1]['json']
        return payload,' '.join(str(row.get('content')) for row in payload['input'] if row.get('role')=='developer')

    def test_a_voice_beyond_the_head_turn_asks_for_a_body_turn_and_a_voice_in_front_does_not(self):
        # Owner, October 3: facing away, he answered a voice from the side without turning
        # until told to ("can you turn over to face me?"), then searched the long way round.
        def drive(bearing):
            return {'available':True,'origin':'requested','limits':{},'targets':[
                {'target_id':'speaker','kind':'voice_direction','bearing_deg':bearing,'where':'to your left'}]}
        base={'conversation_intent':{'revision':0},'people':[],'faces':[],'source_turn_id':'t'}
        _,notes=self.reply_payload({**base,'drive':drive(92)})
        self.assertIn('FACE THE SPEAKER',notes)
        self.assertIn('say_before',notes)
        _,notes=self.reply_payload({**base,'drive':drive(20)})
        self.assertNotIn('FACE THE SPEAKER',notes)
        _,notes=self.reply_payload({**base,'drive':{**drive(120),'available':False}})
        self.assertNotIn('FACE THE SPEAKER',notes)

    def test_the_model_can_choose_the_gaze_and_a_drive_pace(self):
        from r3x.semantics import TOOLS, validate
        pose=next(t for t in TOOLS if t['name']=='set_robot_pose')
        self.assertIn('look_at_people',pose['parameters']['properties']['poses']['items']['enum'])
        self.assertIn('follows the nearest face',pose['description'])
        drive=next(t for t in TOOLS if t['name']=='drive_base')
        pace=drive['parameters']['properties']['steps']['items']['properties']['pace']
        self.assertEqual(pace['enum'],['slow','easy','normal',None])
        validate('set_robot_pose',{'poses':['look_left','look_at_people'],'look_after':None,'say_before':None,'text':None})

    def test_cheaper_execution_requires_step_action_or_explicit_revision(self):
        from r3x.adapters.hosted import OpenAIBrain
        from tests.test_adapters import Client, Response
        from r3x.contracts import Snapshot, Cancellation
        response=Response([{'type':'response.output_item.done','item':{
            'type':'function_call','call_id':'change','name':'revise_agency_plan','arguments':json.dumps({'reason':'A real boundary changed.'})}},
            {'type':'response.completed','response':{}}])
        brain=OpenAIBrain('fake-key');brain.client=Client(response)
        context={'trigger':'reconsideration','agency_step':{'kind':'contribute','scope':'audience'},
                 'conversation_intent':{'revision':0},'people':[],'faces':[]}
        result=list(brain.reply(Snapshot('s','t',0,(),context=context,tools_enabled=True),Cancellation()))
        self.assertEqual(result[0].name,'revise_agency_plan')
        payload=brain.client.calls[0][1]['json']
        self.assertEqual(payload['model'],'gpt-6-luna')
        self.assertEqual(payload['tool_choice']['mode'],'required')
        self.assertEqual({t['name'] for t in payload['tool_choice']['tools']},{'respond_with_intent','revise_agency_plan'})
