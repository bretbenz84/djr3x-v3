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
    return {'scope':'general','narrative':'I wonder what a waltz for three-legged droids would sound like.',
            'objective':'Develop a playful musical idea without interrogating anyone.',
            'evidence_ids':[], 'steps':[
                {'kind':'contribute','instruction':'Offer the waltz idea.','evidence_ids':[], 'delay_seconds':5},
                {'kind':'contribute','instruction':'Develop the rhythm idea without repeating the invitation.',
                 'evidence_ids':[], 'delay_seconds':5}]}


class Planner:
    capability = SimpleNamespace(model='test-planner')
    reasoning_effort = 'medium'
    def __init__(self): self.requests=[]; self.choose=lambda facts,cancel:draft()
    def plan(self,facts,cancel):
        self.requests.append(deepcopy(facts))
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
        self.tick(self.trace.at+9)
        self.assertEqual(len(self.planner.requests),2)
        self.assertEqual(len(self.executor.snapshots),3)
        self.assertEqual(self.store.claims(),[])
        self.assertEqual(self.store.people(),[])

    def test_human_reply_has_no_planner_call_then_lull_revises_previous_plan(self):
        self.setup_owner();self.tick(100)
        now=self.trace.at+2
        capture=Capture(uuid4().hex,'session',bytes(96000),now-3,now,now)
        self.session.note_speech(now)
        self.session.process_turn(capture,Transcript('Tell me about jazz instead.',-.01,True),0,memory_tests.observation())
        self.assertEqual(len(self.planner.requests),1)
        self.tick(self.trace.at+9)
        self.assertEqual(len(self.planner.requests),2)
        self.assertIn('Tell me about jazz instead.',json.dumps(self.planner.requests[-1]))

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

    def test_interruption_during_planning_does_not_save_or_speak(self):
        self.setup_owner()
        def interrupted(facts,cancel):
            self.session.note_speech(101,sustained=True)
            cancel.check()
        self.planner.choose=interrupted
        result=self.tick(100)
        self.assertEqual(result.status,'interrupted')
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
        self.assertEqual(self.state.agency.record['origin'],'degraded_character')
        self.assertEqual(self.state.agency.record['evidence_ids'],[])
        self.assertEqual(self.state.agency.record['cursor'],1)

    def test_revision_abandons_bad_step_and_delivers_a_character_contribution(self):
        self.setup_owner()
        self.executor.choose=lambda snapshot,cancel:iter(['A tuba is a brass instrument with an alibi.'] if
            snapshot.context['agency_step']['origin']=='degraded_character' else
            [ToolCall('revision','revise_agency_plan',{'reason':'The observation no longer supports this premise.'})])
        result=self.tick(100)
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertEqual(self.state.agency.record['previous_outcomes'][-1]['status'],'needs_revision')
        names={tool['name'] for tool in grounded_tools(self.executor.snapshots[-1].context)}
        self.assertNotIn('revise_agency_plan',names)
        self.assertEqual(len(self.executor.snapshots),2)

    def test_planned_execution_tools_do_not_offer_generic_silence_or_wheels(self):
        self.setup_owner();self.tick(100)
        context=self.executor.snapshots[0].context
        names={x['name'] for x in grounded_tools(context)}
        self.assertEqual(names,{'respond_with_intent','revise_agency_plan'})

    def test_conversation_bursts_and_feeds_do_not_exhaust_a_rolling_attempt_budget(self):
        self.setup_owner()
        for i in range(16): self.tick(max(100,self.trace.at+9))
        self.assertEqual(len(self.executor.snapshots),16)
        self.assertEqual(len(self.planner.requests),8)


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
        self.assertEqual(self.state.agency.record['origin'],'degraded_character')
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
        self.assertIsNone(self.planner.requests[0]['previous_plan'])

    def test_session_restart_keeps_plan_but_revalidates_before_speaking(self):
        self.setup_owner();self.tick(100)
        plan_id=self.state.agency.record['id']
        self.session.close()
        from r3x.state import CoreState
        from r3x.speakers import SpeakerTracker
        self.state=CoreState(self.store,SpeakerTracker(self.state.tracker.namespace),self.trace)
        self.setup_owner();self.tick(200)
        self.assertEqual(self.planner.requests[0]['previous_plan']['id'],plan_id)
        self.assertTrue(self.planner.requests[0]['previous_plan']['needs_revalidation'])
        self.assertNotEqual(self.state.agency.record['id'],plan_id)

    def test_revision_reason_and_heard_outcomes_reach_the_next_planner(self):
        self.setup_owner();self.tick(100)
        self.executor.choose=lambda s,c:iter(['I have a new musical idea.'] if
            s.context['agency_step']['origin']=='degraded_character' else
            [ToolCall('r','revise_agency_plan',{'reason':'That object is no longer available.'})])
        self.tick(self.trace.at+9)
        self.executor.choose=None
        self.tick(self.trace.at+9)
        prior=self.planner.requests[-1]['previous_plan']
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
        self.assertEqual(self.state.agency.record['cursor'],0)
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

    def test_personal_plan_is_withheld_from_changed_audience(self):
        plan=draft();plan['scope']='audience'
        agency=self.state.agency;agency.load()
        agency.install(plan,'["person:one"]',{},['contribute'],agency.revision,100)
        self.assertIsNone(agency.context('["person:two"]'))
        self.assertIsNone(agency.step('["person:two"]'))

    def test_personal_evidence_cannot_be_saved_as_general_plan(self):
        self.setup_owner()
        plan=draft();plan['evidence_ids']=['private']
        self.state.agency.install(plan,'["person:one"]',{'private':{'kind':'memory_opportunities'}},
            ['contribute'],self.state.agency.revision,100)
        self.assertEqual(self.state.agency.record['scope'],'audience')
        self.assertIsNotNone(self.state.agency.step('["person:one"]'))
        self.assertIsNone(self.state.agency.step('["person:two"]'))

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

    def test_cancel_during_plan_repair_cannot_install_degraded_work(self):
        self.setup_owner()
        def choose(facts,cancel):
            if 'repair' in facts:
                self.session.note_speech(3,sustained=True)
                cancel.check()
            value=draft();value['evidence_ids']=['invented'];return value
        self.planner.choose=choose
        result=self.tick(2)
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(len(self.planner.requests),2)
        self.assertEqual(self.executor.snapshots,[])
        self.assertIsNone(self.state.agency.record)
        self.assertEqual(self.session.last_interaction_at,float('-inf'))

    def test_human_speech_during_revision_recovery_stops_without_speaking(self):
        self.setup_owner()
        def choose(snapshot,cancel):
            if snapshot.context['agency_step']['origin']=='degraded_character':
                self.session.note_speech(101,sustained=True)
                cancel.check()
            return iter([ToolCall('r','revise_agency_plan',{'reason':'Object no longer available.'})])
        self.executor.choose=choose
        result=self.tick(100)
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(result.completed_text,[])
        self.assertEqual(self.state.agency.record['cursor'],0)
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'needs_revision')

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
        self.assertEqual(self.state.agency.record['scope'],'audience')
        self.assertEqual(self.state.agency.record['cursor'],1)
        self.assertIsNone(self.state.agency.context('["person:new"]'))

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
        self.assertEqual(self.state.agency.record['cursor'],0)
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'needs_revision')

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
        self.assertEqual(self.state.agency.record['cursor'],0)
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'],'needs_revision')
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
        self.assertEqual(self.state.agency.record['cursor'],0)


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
        self.assertEqual(payload['tool_choice'],{'type':'function','name':'compose_agency_plan'})
        self.assertFalse(payload['store'])
        self.assertEqual([t['name'] for t in payload['tools']],['compose_agency_plan'])

    def test_cheaper_execution_requires_step_action_or_explicit_revision(self):
        from r3x.adapters.hosted import OpenAIBrain
        from tests.test_adapters import Client, Response
        from r3x.contracts import Snapshot, Cancellation
        response=Response([{'type':'response.output_item.done','item':{
            'type':'function_call','call_id':'change','name':'revise_agency_plan','arguments':json.dumps({'reason':'A real boundary changed.'})}},
            {'type':'response.completed','response':{}}])
        brain=OpenAIBrain('fake-key');brain.client=Client(response)
        context={'trigger':'reconsideration','agency_step':{'kind':'contribute'},
                 'conversation_intent':{'revision':0},'people':[],'faces':[]}
        result=list(brain.reply(Snapshot('s','t',0,(),context=context,tools_enabled=True),Cancellation()))
        self.assertEqual(result[0].name,'revise_agency_plan')
        payload=brain.client.calls[0][1]['json']
        self.assertEqual(payload['model'],'gpt-6-luna')
        self.assertEqual(payload['tool_choice']['mode'],'required')
        self.assertEqual({t['name'] for t in payload['tool_choice']['tools']},{'respond_with_intent','revise_agency_plan'})
