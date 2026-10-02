"""First agency increment: production Session/runtime, synthetic providers only."""
from dataclasses import replace
from uuid import uuid4

from r3x.contracts import ToolCall, ProviderFailure
from r3x.output import OutputOwner, TextOutput
from r3x.proactive import Opportunity, Initiative
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from tests.test_memory_core import MemoryFixture
from tests import test_proactive_core
from tests.test_conversation_intent import proposal, IntentBrain
from tests.test_voice_core import Brain
from tests.test_turn_taking import ClockSink


def next_request(context, trigger='after_decision', delay=3):
    offered = context['reconsideration']
    return dict(source_id=offered['source_id'], revision=offered['revision'],
                trigger=trigger, delay_seconds=None if trigger=='wait_for_input' else delay)


def interest(context, **changes):
    row = dict(id=None, topic='A musical thought experiment',
               curiosity='What would a rhythm made of prime numbers sound like?',
               next_action='Offer a brief hypothetical when welcome.', kind='character_idea',
               evidence_ids=[], status='active')
    row.update(changes)
    return row


class AgencyTests(MemoryFixture):
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    turn = test_proactive_core.InitiativeCoreTests.turn
    visible = test_proactive_core.InitiativeCoreTests.visible

    def tick(self, at):
        self.trace.at = at
        self.runtime.audio_seen_at = at
        return self.runtime.proactive_tick()

    def no_camera(self, choose=None, audio=False):
        self.setup_core(IntentBrain(choose) if choose else Brain('An unsolicited thought.'))
        self.runtime.camera = None
        if audio:
            self.session.output = OutputOwner(SyntheticTTS(), ClockSink(self.trace), self.trace)

    def scheduled_brain(self, trigger='after_decision', delay=3, silent=False):
        return IntentBrain(lambda c: ToolCall(uuid4().hex,
            'remain_silent' if silent else 'respond_with_intent', {
                **({'reason':'no_response_needed'} if silent else {'text':'A new musical idea.'}),
                'intent_update':None, 'next_opportunity':next_request(c, trigger, delay)}))

    def test_camera_free_startup_offers_real_opportunity_without_invented_human(self):
        self.no_camera()
        result = self.tick(100)
        self.assertEqual(result.status, 'completed')
        context = self.brain.snapshots[-1].context
        self.assertEqual(context['trigger'], 'reconsideration')
        self.assertEqual(context['faces'], [])
        self.assertEqual(context['speaker']['state'], 'unresolved')
        self.assertEqual(context['memory_opportunities'], [])
        self.assertIsNone(context['personal_memory']['person_id'])
        self.assertFalse(any(m.get('role')=='user' for m in self.brain.snapshots[-1].messages))
        self.assertEqual(self.store.rows('SELECT * FROM turns'), [])
        self.assertEqual(self.tick(200).status, 'completed')  # Plan advancement does not need new input.

    def test_human_reply_is_immediate_and_repeated_plan_steps_need_no_camera(self):
        self.no_camera()
        self.turn('Rex, tell me about rhythm.')
        self.assertEqual(self.tick(107).status, 'prepared')
        self.assertEqual(self.brain.calls, 1)
        self.assertEqual(self.tick(108).status, 'completed')
        self.assertEqual(self.brain.calls, 2)
        self.assertEqual(self.tick(300).status, 'completed')








    def test_stale_schedule_is_rejected_without_losing_reply(self):
        self.no_camera(lambda c: ToolCall('reply','respond_with_intent', dict(
            text='Here is the answer.', intent_update=None,
            next_opportunity={**next_request(c),'revision':-1})))
        result = self.turn()
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.completed_text, ['Here is the answer.'])
        self.assertTrue(any(r['event']=='reconsideration_rejected' for r in self.trace.rows))

    def test_quiet_and_frozen_still_prevent_camera_free_initiative(self):
        self.no_camera()
        self.state.initiative.quiet = True
        self.assertIsNone(self.tick(100))
        self.state.initiative.quiet = False; self.state.frozen = True
        self.assertIsNone(self.tick(200))
        self.assertEqual(self.brain.calls, 0)

    def test_gate_trace_is_transition_only(self):
        self.no_camera(); self.runtime.audio_seen_at = 0
        for _ in range(20): self.runtime.proactive_tick()
        rows = [r for r in self.trace.rows if r['event']=='initiative_gate']
        self.assertEqual([r['reason'] for r in rows], ['audio_unavailable'])
        self.tick(100)
        self.assertEqual([r['reason'] for r in self.trace.rows if r['event']=='initiative_gate'],
                         ['audio_unavailable','considering'])

    def test_generic_working_interest_survives_camera_free_reconsideration(self):
        def choose(c):
            current = c['conversation_intent']['current']
            update = proposal(c, target_ids=[], interests=[interest(c)] if current is None else None)
            return ToolCall(uuid4().hex,'remain_silent', dict(reason='no_response_needed',
                intent_update=update, next_opportunity=next_request(c)))
        self.no_camera(choose)
        self.tick(100)
        before = self.state.conversation_intent.record.interests[0]
        self.assertEqual(before['delivery']['status'], 'suppressed')
        self.tick(108)
        row = self.state.conversation_intent.record
        self.assertEqual(row.interests[0]['id'], before['id'])
        self.assertEqual(self.brain.calls, 2)
        self.assertEqual(self.store.claims(), [])
        self.assertEqual(self.store.people(), [])

    def test_interest_revise_resolve_and_closed_context_prevent_immediate_forgetting(self):
        self.no_camera(lambda c: ToolCall(uuid4().hex,'remain_silent', dict(reason='no_response_needed',
            intent_update=proposal(c,target_ids=[],interests=[interest(c)]))))
        self.tick(100)
        old = self.state.conversation_intent.record.interests[0]
        self.session.brain = IntentBrain(lambda c: ToolCall('resolve','remain_silent', dict(reason='no_response_needed',
            intent_update=proposal(c,target_ids=[],interests=[interest(c,id=old['id'],status='resolved')]))))
        event = Opportunity('new','session',110,None,None,(),self.session._latest_speech,False,'reconsideration')
        self.session.process_opportunity(event,0,lambda:True)
        row = self.state.conversation_intent.record
        self.assertEqual(row.interests, [])
        self.assertEqual(row.closed_interests[-1]['id'], old['id'])
        self.assertEqual(row.closed_interests[-1]['status'], 'resolved')

    def test_unoffered_interest_evidence_rejects_unbacked_promise(self):
        self.no_camera(lambda c: ToolCall('reply','respond_with_intent', dict(text='An ordinary reply.',
            intent_update=proposal(c, target_ids=[], interests=[interest(c,kind='observation',evidence_ids=['invented'])]))))
        self.assertEqual(self.tick(100).status, 'failed')
        self.assertNotIn('An ordinary reply.', self.state.agency.record['outcomes'][-1]['heard_text'])
        self.assertIsNone(self.state.conversation_intent.record)
        self.assertTrue(any(r.get('reason')=='WorkingInterestEvidenceNotOffered' for r in self.trace.rows))

    def test_new_session_clears_working_notes_and_timer(self):
        self.no_camera(lambda c: ToolCall('note','remain_silent', dict(reason='no_response_needed',
            intent_update=proposal(c,target_ids=[],interests=[interest(c)]),next_opportunity=next_request(c))))
        self.tick(100)
        Session('next-session', Brain(), TextOutput(self.trace), self.trace, self.state)
        self.assertIsNone(self.state.conversation_intent.record)


    def test_new_audience_cannot_receive_old_working_interests(self):
        self.setup_core(IntentBrain(lambda c: ToolCall('note','respond_with_intent', dict(
            text='Let us explore that idea.', intent_update=proposal(c,interests=[interest(c)])))))
        self.visible(100)
        event = Opportunity('hello','session',100,'visitor',None,self.runtime.arrivals.faces,
                            self.session._latest_speech,False)
        self.session.process_opportunity(event,0,lambda:True)
        self.session.brain = Brain('A general thought.')
        self.visible(110,'other')
        result = self.session.process_opportunity(replace(event,event_id='later',kind='reconsideration',
            faces=self.runtime.arrivals.faces,at=110),0,lambda:True)
        self.assertEqual(result.status,'completed')
        self.assertIsNone(self.session.brain.snapshots[0].context['conversation_intent']['current'])

    def test_general_interest_survives_head_turn_while_personal_note_is_withheld(self):
        from tests.test_proactive import face
        self.no_camera(lambda c: ToolCall('note','remain_silent', dict(reason='no_response_needed',
            intent_update=proposal(c,scope='general',target_ids=[],interests=[interest(c)]))))
        event = Opportunity('look','session',100,None,None,(face(100),),float('-inf'),False,'ambient')
        self.session.process_opportunity(event,0,lambda:True)
        row = self.state.conversation_intent.record
        self.assertEqual(row.audience, ())
        self.session.brain = Brain('A fresh general thought.')
        self.session.process_opportunity(replace(event,event_id='turned',at=110,faces=()),0,lambda:True)
        shown = self.session.brain.snapshots[0].context['conversation_intent']['current']
        self.assertEqual(shown['interests'][0]['id'], row.interests[0]['id'])
        self.assertEqual(shown['scope'], 'general')

    def test_private_memory_and_person_targets_cannot_be_marked_general(self):
        from tests.test_proactive import face
        self.no_camera(lambda c: ToolCall('note','respond_with_intent', dict(text='Hello there.',
            intent_update=proposal(c,scope='general',target_ids=['face:face'],interests=[interest(c)]))))
        event = Opportunity('look','session',100,None,None,(face(100),),float('-inf'),False,'ambient')
        self.assertEqual(self.session.process_opportunity(event,0,lambda:True).status,'failed')
        self.assertIsNone(self.state.conversation_intent.record)

    def test_stale_interest_revision_does_not_replace_active_interest(self):
        self.no_camera(lambda c: ToolCall('note','remain_silent', dict(reason='no_response_needed',
            intent_update=proposal(c,target_ids=[],interests=[interest(c)]))))
        self.tick(100)
        before = self.state.conversation_intent.record
        self.session.brain = IntentBrain(lambda c: ToolCall('stale','respond_with_intent', dict(text='A useful reply.',
            intent_update=proposal(c,revision=0,target_ids=[],interests=[]))))
        event = Opportunity('new','session',110,None,None,(),float('-inf'),False,'reconsideration')
        self.assertEqual(self.session.process_opportunity(event,0,lambda:True).status,'failed')
        self.assertIs(self.state.conversation_intent.record,before)

    def test_source_correction_clears_interest_and_its_pending_schedule(self):
        self.no_camera(lambda c: ToolCall('note','remain_silent', dict(reason='no_response_needed',
            intent_update=proposal(c,target_ids=[],interests=[interest(c)]),next_opportunity=next_request(c))))
        self.tick(100)
        self.state.conversation_intent.clear('correct_claim')
        self.assertIsNone(self.state.conversation_intent.record)


    def test_public_sources_can_be_configured_without_camera_and_do_not_start_on_configuration(self):
        from r3x.app import App
        from r3x.settings import Settings
        from pathlib import Path
        app = App(Settings(camera_enabled=False),Path(self.temp.name))
        app.configure_live()
        public = next(p for p in app.live_components if p.capability.name=='public_observations')
        self.assertIsNone(public.worker)
        self.assertFalse(any(p.capability.name=='scene_observations' for p in app.live_components))

    def test_report_explains_gate_transitions_and_scheduling_without_fabricating_metrics(self):
        import json
        from pathlib import Path
        from r3x.trace import report
        path = Path(self.temp.name)/'trace.jsonl'
        path.write_text('\n'.join(json.dumps(row) for row in [
            {'event':'initiative_gate','reason':'waiting_for_input'},
            {'event':'reconsideration_armed','source_id':'synthetic','due_at':103},
            {'event':'reconsideration_cancelled','reason':'human_speech'}]))
        result = report(path)
        self.assertEqual(result['initiative_gate_transitions'], {'waiting_for_input':1})
        self.assertEqual(len(result['reconsideration_outcomes']),2)
        self.assertEqual(result['model_calls'],0)
        self.assertIsNone(result['playback_underruns'])


    def test_audio_only_conversation_intent_does_not_reintroduce_camera_timer_gate(self):
        self.no_camera(audio=True)
        self.session.brain = IntentBrain(lambda c: ToolCall(uuid4().hex,'respond_with_intent', dict(
            text='That sounds like an interesting instrument.', intent_update=proposal(c,
                interests=[interest(c,kind='conversation',evidence_ids=[c['source_turn_id']])]),
            next_opportunity=next_request(c,'after_speech'))))
        self.turn('I am building a new instrument.')
        self.session.brain = self.scheduled_brain('after_speech')
        result = self.tick(113)
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.session.brain.calls,1)
        # No camera confirms the old audience: hide their note, but allow a new
        # generic decision. Do not silently grant private memory access.
        context = self.session.brain.snapshots[0].context
        self.assertIsNone(context['conversation_intent']['current'])
        self.assertEqual(context['memory_opportunities'],[])
