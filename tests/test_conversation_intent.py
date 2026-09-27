"""Session contracts, synthetic decisions/audio only; no semantic accuracy claim."""
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4
import json

from r3x.contracts import Cancellation, Cancelled, Capture, ProviderFailure, ToolCall, Transcript
from r3x.conversation_intent import ConversationIntent, validate_update
from r3x.output import OutputOwner, TextOutput
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import grounded_tools
from r3x.session import Session
from tests.test_memory_core import MemoryFixture, observation
from tests.test_proactive import event, face
from tests.test_voice_core import Brain


def proposal(context, **changes):
    offered = context['conversation_intent']
    result = dict(intent_id=offered['intent_id'], revision=offered['revision'],
                  source_id=offered['source_id'], status='active',
                  objective='Welcome the visitor and follow their chosen topic.',
                  openings=['Offer a low-pressure introduction.'],
                  target_ids=offered['target_ids'][:1], memory_ids=[], reason='A new visit is beginning.')
    result.update(changes)
    return result


class IntentBrain(Brain):
    def __init__(self, choose=None):
        super().__init__()
        self.choose = choose or (lambda c: ToolCall(uuid4().hex, 'respond_with_intent',
            {'text':'Hello! Good to see you.', 'intent_update':proposal(c)}))

    def reply(self, snapshot, cancel):
        self.calls += 1
        self.snapshots.append(snapshot)
        yield self.choose(snapshot.context)


class ConversationIntentTests(MemoryFixture):
    def setup_session(self, brain=None, output=None):
        self.brain = brain or IntentBrain()
        self.session = Session('session', self.brain, output or
            OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace), self.state.trace, self.state)
        return self.session

    def rejections(self):
        return [r['reason'] for r in self.state.trace.rows if r['event']=='conversation_intent_rejected']

    def greet(self, brain=None, output=None):
        self.setup_session(brain, output)
        result = self.session.process_opportunity(event(), 0, lambda:True)
        self.assertEqual(result.status, 'completed', result)
        return result

    def human(self, text, faces=None, latest_faces=None):
        at = self.state.trace.at = self.state.trace.at + 10
        capture = Capture(uuid4().hex,'session',uuid4().bytes*6000,at-4,at-1,at,
                          voiced_intervals=((0,3),))
        self.session.note_speech(at-1)
        return self.session.process_turn(capture,Transcript(text,-.01,True),self.session.revision,
            observation(),(face(at),) if faces is None else faces,latest_faces=latest_faces)

    def test_greeting_creates_session_intent_without_database_mutation_or_extra_call(self):
        revision = self.store.revision
        result = self.greet()
        self.assertEqual(self.brain.calls,1)
        self.assertEqual(self.store.revision,revision)
        for table in ('turns','claims','recordings','operations'):
            self.assertEqual(self.store.rows('SELECT * FROM '+table),[])
        row = self.state.conversation_intent.record
        self.assertEqual(row.status,'active')
        self.assertTrue(row.delivery['heard'])
        self.assertEqual(result.completed_text,['Hello! Good to see you.'])
        self.assertFalse(any(m.get('role')=='user' for m in self.brain.snapshots[0].messages))

    def test_human_direction_replaces_intention_in_same_call(self):
        self.greet()
        self.session.brain=IntentBrain(lambda c:ToolCall(uuid4().hex,'respond_with_intent',dict(
            text='The camera helps me see the room.',intent_update=proposal(c,
            objective='Discuss the camera because the visitor asked.',openings=[],reason='Follow their question.'))))
        result=self.human('Actually, tell me how your camera works.')
        self.assertEqual(result.status,'completed',result)
        self.assertEqual(self.session.brain.calls,1)
        self.assertIn('camera',self.state.conversation_intent.record.objective)
        self.assertEqual(self.store.claims(),[])

    def test_plain_prose_keeps_streaming_and_retains_intention(self):
        self.greet();before=self.state.conversation_intent.revision
        self.session.brain=Brain('I can see shapes and faces.')
        result=self.human('What can your camera see?')
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.session.brain.calls,1)
        self.assertEqual(self.state.conversation_intent.revision,before)
        self.assertIsNotNone(self.session.brain.snapshots[0].context['conversation_intent']['current'])

    def test_name_question_combines_plan_but_pending_requires_delivery(self):
        choose=lambda c:ToolCall(uuid4().hex,'ask_face_question',dict(text='Hello! What should I call you?',
            face_track_id='face',intent_update=proposal(c)))
        self.greet(IntentBrain(choose))
        self.assertEqual(self.state.pending['face_track_id'],'face')
        self.assertIsNone(self.state.pending['addressee_id'])
        self.assertEqual(self.store.people(),[])
        self.state.pending=None
        self.greet(IntentBrain(choose),TextOutput(self.state.trace))
        self.assertIsNone(self.state.pending)
        self.assertFalse(self.state.conversation_intent.record.delivery['heard'])

    def test_failed_greeting_is_not_heard_and_is_not_automatically_replayed(self):
        class Failed:
            def synthesize(self,text,cancel):
                raise ProviderFailure('SyntheticFailure')
                yield b''
        self.setup_session(output=OutputOwner(Failed(),SyntheticSink(),self.state.trace))
        result=self.session.process_opportunity(event(),0,lambda:True)
        self.assertEqual(result.status,'failed')
        self.assertFalse(self.state.conversation_intent.record.delivery['heard'])
        self.assertEqual(self.brain.calls,1)

    def test_cancel_before_commit_preserves_no_intent(self):
        self.setup_session();ctx=self.state.intent_context(self.state.opportunity_context(event()))
        cancel=Cancellation();cancel.event.set()
        with self.assertRaises(Cancelled):
            self.state.execute_opportunity(ToolCall('x','respond_with_intent',dict(text='Hello',intent_update=proposal(ctx))),ctx,cancel)
        self.assertIsNone(self.state.conversation_intent.record)

    def test_cancel_after_commit_records_unheard_greeting(self):
        self.setup_session()
        session=self.session
        class Stop:
            def synthesize(self,text,cancel):
                session.cancel();cancel.check()
                yield bytes(64)
        session.output=OutputOwner(Stop(),SyntheticSink(),self.state.trace)
        result=session.process_opportunity(event(),0,lambda:True)
        self.assertEqual(result.status,'interrupted')
        self.assertFalse(self.state.conversation_intent.record.delivery['heard'])

    def test_changed_audience_withholds_old_free_text_and_requires_fresh_decision(self):
        self.greet()
        ctx={'faces':[face(100,track='new')],'participation':{}}
        self.state.conversation_intent.refresh(ctx,[])
        view=self.state.conversation_intent.context(ctx)
        self.assertIsNone(view['current'])
        self.assertNotIn('Welcome',json.dumps(view))
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')
        old={'faces':[face(100)],'participation':{}}
        self.state.conversation_intent.refresh(old,[])
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')

    def test_new_audience_during_generation_rejects_before_commit(self):
        self.greet();version=self.state.conversation_intent.revision
        current=[face(110)]
        class Changed(IntentBrain):
            def reply(self,snapshot,cancel):
                current.append(face(110,track='new'))
                yield ToolCall('changed','respond_with_intent',dict(text='Hello',intent_update=proposal(snapshot.context)))
        self.session.brain=Changed()
        result=self.human('Tell me more.',latest_faces=lambda now:current)
        self.assertEqual(result.error,'IntentAudienceChanged')
        self.assertEqual(self.state.conversation_intent.revision,version)
        self.assertEqual(result.completed_text,[])

    def test_invalid_plan_is_dropped_without_losing_the_boundary_or_reply(self):
        # An optional planning note must not cost the person their boundary or Rex's reply.
        self.greet();before=self.state.conversation_intent.record
        def choose(c):
            return ToolCall('bad','conversation_feedback',dict(action='topic_avoid',reference_id=None,
                topic='work',source_turn_id=c['source_turn_id'],text='Okay.',
                intent_update=proposal(c,target_ids=['person:invented'])))
        self.session.brain=IntentBrain(choose)
        result=self.human('Do not talk about work.')
        self.assertIsNone(result.error)
        self.assertEqual([r['action'] for r in self.state.initiative.feedback],['topic_avoid'])
        self.assertEqual(result.completed_text,['Okay.'])
        self.assertEqual(self.session.brain.calls,1)
        self.assertEqual(self.state.conversation_intent.record.objective,before.objective)

    def test_topic_boundary_suspends_even_without_a_plan_update(self):
        self.greet()
        self.session.brain=IntentBrain(lambda c:ToolCall('boundary','conversation_feedback',dict(
            action='topic_avoid',reference_id=None,topic='work',source_turn_id=c['source_turn_id'],text='Of course.')))
        self.assertEqual(self.human('Do not bring up work.').status,'completed')
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')

    def test_silent_floor_update_cannot_reactivate_plan_or_leak_it_into_history(self):
        self.greet()
        self.session.brain=IntentBrain(lambda c:ToolCall('floor','remain_silent',dict(
            reason='unfinished_thought',intent_update=proposal(c,objective='Leave time for their explanation.'))))
        result=self.human('Give me a minute to think.')
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')
        self.assertNotIn('Leave time for their explanation',json.dumps(self.session.history_messages()))

    def test_quiet_survives_direct_reply_and_resume_does_not_reactivate(self):
        self.greet()
        self.session.brain=IntentBrain(lambda c:ToolCall('quiet','set_initiative',dict(mode='quiet',source_turn_id=c['source_turn_id'])))
        self.assertEqual(self.human('Please stop starting conversations.').status,'suppressed')
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')
        self.session.brain=Brain('Sure, I can answer that.')
        self.assertEqual(self.human('What is two plus two?').status,'completed')
        self.assertTrue(self.state.initiative.quiet)
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')

    def test_stale_revision_source_and_fabricated_memory_leave_intention_unchanged(self):
        self.greet()
        for changes,reason in [({'revision':0},'StaleIntentRevision'),
                               ({'source_id':'invented'},'IntentSourceMismatch'),
                               ({'memory_ids':['invented']},'IntentMemoryNotOffered')]:
            ctx=self.state.intent_context(self.state.opportunity_context(event()))
            revision=self.state.conversation_intent.revision
            result=self.state.execute_opportunity(ToolCall(uuid4().hex,'respond_with_intent',dict(
                text='Hello',intent_update=proposal(ctx,**changes))),ctx,Cancellation())
            self.assertEqual(result.outcome,'ok')
            self.assertEqual(self.state.conversation_intent.revision,revision)
            self.assertEqual(self.rejections()[-1],reason)

    def test_retry_is_idempotent_and_different_payload_cannot_reuse_id(self):
        self.setup_session();ctx=self.state.intent_context(self.state.opportunity_context(event()))
        call=ToolCall('one','respond_with_intent',dict(text='Hello',intent_update=proposal(ctx)))
        first=self.state.execute_opportunity(call,ctx,Cancellation());revision=self.state.conversation_intent.revision
        self.assertEqual(first,self.state.execute_opportunity(call,ctx,Cancellation()))
        self.assertEqual(revision,self.state.conversation_intent.revision)
        second=replace(call,arguments=dict(call.arguments,text='Different'))
        self.assertEqual(self.state.execute_opportunity(second,ctx,Cancellation()).reason,'RequestIdReusedWithDifferentArguments')

    def test_frozen_replay_rejects_attached_update(self):
        self.greet();self.begin('Please keep talking.')
        ctx=self.state.intent_context(self.state.context());self.state.frozen=True
        result=self.state.execute(ToolCall('freeze','remain_silent',dict(reason='no_response_needed',
            intent_update=proposal(ctx))),self.store.revision,Cancellation(),context=ctx)
        self.assertEqual(result.reason,'FrozenEvaluationDisallowsMutation')

    def test_expiry_happens_before_new_human_input_can_refresh(self):
        self.greet();self.state.trace.at=1000
        self.state.conversation_intent.refresh({'faces':[face(1000)]},[],human=True)
        self.assertIsNone(self.state.conversation_intent.record)

    def test_camera_gap_suspends_then_absence_clears(self):
        self.greet();self.state.trace.at=105
        self.state.conversation_intent.refresh({'faces':[]},[])
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')
        self.state.trace.at=131;self.state.conversation_intent.refresh({'faces':[]},[])
        self.assertIsNone(self.state.conversation_intent.record)

    def test_removed_memory_clears_intent_and_receipts(self):
        self.greet();self.state.conversation_intent.record.memory_ids=['old']
        self.state.conversation_intent.refresh({'faces':[face(100)]},[])
        self.assertIsNone(self.state.conversation_intent.record)
        self.assertEqual(list(self.state.conversation_intent.recent),[])
        self.assertEqual(self.state.conversation_intent.requests,{})

    def test_new_session_even_with_reused_state_clears_intention(self):
        self.greet()
        Session('new-session',Brain(),TextOutput(self.state.trace),self.state.trace,self.state)
        self.assertIsNone(self.state.conversation_intent.record)

    def test_completion_removes_openings_without_adding_personal_facts(self):
        self.greet()
        self.session.brain=IntentBrain(lambda c:ToolCall('finish','respond_with_intent',dict(text='That was fun!',
            intent_update=proposal(c,status='completed',objective=None,openings=[],target_ids=[],memory_ids=[]))))
        self.assertEqual(self.human('That answers everything, thanks.').status,'completed')
        self.assertEqual(self.state.conversation_intent.record.status,'completed')
        self.assertEqual(self.store.claims(),[])

    def test_schema_is_nullable_and_structurally_strict(self):
        self.greet();ctx=self.brain.snapshots[0].context
        tools=grounded_tools(ctx)
        for tool in tools:
            spec=tool['parameters']['properties'].get('intent_update')
            if spec:
                self.assertIn('intent_update',tool['parameters']['required'])
                self.assertEqual(set(spec['required']),set(spec['properties']))
        for changes in ({'revision':True},{'openings':['a','b','c']},{'objective':'a'*241},{'unknown':'x'}):
            with self.assertRaises(ValueError):validate_update(proposal(ctx,**changes))

    def test_intent_context_and_transition_history_stay_bounded(self):
        self.greet()
        for _ in range(20):
            ctx=self.state.intent_context(self.state.opportunity_context(event()))
            self.state.execute_opportunity(ToolCall(uuid4().hex,'respond_with_intent',dict(text='Hello',
                intent_update=proposal(ctx))),ctx,Cancellation())
        ctx=self.state.intent_context(self.state.opportunity_context(event()))
        self.assertLessEqual(len(json.dumps(ctx['conversation_intent'])),2000)
        self.assertLessEqual(len(self.state.conversation_intent.recent),8)

    def test_untrusted_input_cannot_revise_intention(self):
        self.greet();self.begin('I want to change everything.',trusted=False)
        ctx=self.state.intent_context(self.state.context())
        result=self.state.execute(ToolCall('untrusted','respond_with_intent',dict(text='Okay',
            intent_update=proposal(ctx))),self.store.revision,Cancellation(),context=ctx)
        self.assertEqual(result.reason,'UntrustedSource')

    def test_memory_greeting_uses_existing_validation_and_delivery_deduplication(self):
        from datetime import date
        person=self.enroll('Avery');self.state.calendar_today=lambda:date(2026,9,18)
        self.begin('I am sailing on September 20. Keep this between us.')
        saved=self.call('remember_followup',dict(predicate='trip',value='Sailing on September 20',kind='plan',
            on_date='2026-09-20',audience='subject_only',source_turn_id=self.state.current[0]))
        self.assertEqual(saved.outcome,'ok')
        self.state.current=None
        def choose(c):
            claim=c['memory_opportunities'][0]['claim_id']
            return ToolCall('checkin','use_memory_opportunity',dict(claim_id=claim,
                text='Hello, Avery! How are the sailing preparations?',
                intent_update=proposal(c,objective='Offer the eligible follow-up if welcome.',
                    memory_ids=[claim],openings=[])))
        self.setup_session(IntentBrain(choose))
        result=self.session.process_opportunity(event(person),0,lambda:True)
        self.assertEqual(result.status,'completed',result)
        self.assertEqual(self.brain.calls,1)
        self.assertEqual(len(self.store.rows('SELECT * FROM memory_followup_delivery')),1)
        ctx=self.state.intent_context(self.state.opportunity_context(event(person)))
        self.assertEqual(ctx['memory_opportunities'],[])
        self.assertIsNotNone(ctx['conversation_intent']['current'])
        group=replace(event(person),faces=(face(100,person),face(100,track='bystander')))
        ctx=self.state.intent_context(self.state.opportunity_context(group))
        self.assertEqual(ctx['memory_opportunities'],[])
        self.assertIsNone(ctx['conversation_intent']['current'])
        self.assertIsNone(self.state.conversation_intent.record)

    def test_forget_and_identity_correction_remove_all_intent_sources(self):
        person=self.enroll('Avery')
        self.greet()
        self.begin('Forget me, please.')
        result=self.call('forget_person',dict(person_id=person))
        self.assertEqual(result.outcome,'ok')
        self.assertIsNone(self.state.conversation_intent.record)
        self.assertFalse(self.state.conversation_intent.recent)
        self.assertFalse(self.state.conversation_intent.requests)

    def test_replace_for_new_group_does_not_expose_previous_transition_text(self):
        self.greet()
        self.state.conversation_intent.recent.append(dict(reason='old group private context'))
        changed=replace(event(),faces=(face(100),face(100,track='new')))
        ctx=self.state.intent_context(self.state.opportunity_context(changed))
        result=self.state.execute_opportunity(ToolCall('new-group','respond_with_intent',dict(text='Hello everyone!',
            intent_update=proposal(ctx))),ctx,Cancellation())
        self.assertEqual(result.outcome,'ok')
        self.assertNotIn('private context',json.dumps(self.state.conversation_intent.context(ctx)))

    def test_other_tool_takes_precedence_without_intention_update(self):
        self.greet()
        class Introduction(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                if snapshot.tools_enabled:
                    yield ToolCall('intro','introduce_person',dict(name='Avery',aliases=[],subject='self',person_id=None,face_track_id=None))
                else:yield 'Nice to meet you, Avery.'
        self.session.brain=Introduction()
        result=self.human('My name is Avery.')
        self.assertEqual(result.status,'completed')
        self.assertEqual(self.store.people()[0]['name'],'Avery')
        self.assertEqual(self.session.brain.calls,2)  # Existing identity continuation, no planning call.

    def test_silent_edits_do_not_extend_expiry(self):
        self.greet();self.state.trace.at=800
        ctx=self.state.intent_context(self.state.opportunity_context(replace(event(at=800),speech_marker=0)))
        result=self.state.execute_opportunity(ToolCall('silent','remain_silent',dict(reason='no_response_needed',
            intent_update=proposal(ctx,status='suspended'))),ctx,Cancellation())
        self.assertEqual(result.outcome,'ok')
        self.state.trace.at=1001
        self.state.conversation_intent.refresh({'faces':[face(1001)]},[])
        self.assertIsNone(self.state.conversation_intent.record)

    def test_evicted_request_cannot_recreate_an_old_intention(self):
        self.setup_session();ctx=self.state.intent_context(self.state.opportunity_context(event()))
        call=ToolCall('first','respond_with_intent',dict(text='Hello',intent_update=proposal(ctx)))
        self.assertEqual(self.state.execute_opportunity(call,ctx,Cancellation()).outcome,'ok')
        for _ in range(33):
            fresh=self.state.intent_context(self.state.opportunity_context(event()))
            self.state.execute_opportunity(ToolCall(uuid4().hex,'respond_with_intent',dict(text='Hello',
                intent_update=proposal(fresh))),fresh,Cancellation())
        self.assertLessEqual(len(self.state.conversation_intent.requests),32)
        current=self.state.conversation_intent.record
        self.state.execute_opportunity(call,ctx,Cancellation())
        self.assertIs(self.state.conversation_intent.record,current)

    def test_cached_result_cannot_cross_audience_or_frozen_boundary(self):
        self.setup_session();ctx=self.state.intent_context(self.state.opportunity_context(event()))
        call=ToolCall('retry','respond_with_intent',dict(text='Hello',intent_update=proposal(ctx)))
        self.assertEqual(self.state.execute_opportunity(call,ctx,Cancellation()).outcome,'ok')
        changed=dict(ctx,faces=[face(100,track='other')])
        self.assertEqual(self.state.execute_opportunity(call,changed,Cancellation()).reason,'IntentAudienceChanged')
        self.state.frozen=True
        self.assertEqual(self.state.execute_opportunity(call,ctx,Cancellation()).reason,'FrozenEvaluationDisallowsMutation')

    def test_tts_audience_change_cancels_ordinary_intent_reply(self):
        self.greet();current=[face(110)]
        class Arriving:
            def synthesize(self,text,cancel):
                current.append(face(110,track='bystander'))
                cancel.check()
                yield bytes(64)
        self.session.output=OutputOwner(Arriving(),SyntheticSink(),self.state.trace)
        self.session.brain=IntentBrain()
        result=self.human('Tell me something.',latest_faces=lambda now:current)
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(result.completed_text,[])
        self.assertFalse(self.state.conversation_intent.record.delivery['heard'])

    def test_selected_visitor_departure_clears_even_when_bystander_stays(self):
        self.setup_session()
        group=replace(event(),faces=(face(100),face(100,track='bystander')))
        self.assertEqual(self.session.process_opportunity(group,0,lambda:True).status,'completed')
        self.state.trace.at=110
        self.state.conversation_intent.refresh({'faces':[face(110,track='bystander')]},[])
        self.assertEqual(self.state.conversation_intent.record.status,'suspended')
        self.state.trace.at=131
        self.state.conversation_intent.refresh({'faces':[face(131,track='bystander')]},[])
        self.assertIsNone(self.state.conversation_intent.record)

    def test_cached_human_turn_result_rejects_after_new_source(self):
        self.greet();self.begin('Tell me something.')
        ctx=self.state.intent_context(self.state.context((face(100),)))
        call=ToolCall('cached','respond_with_intent',dict(text='Hello',intent_update=proposal(ctx)))
        self.assertEqual(self.state.execute(call,self.store.revision,Cancellation(),context=ctx).outcome,'ok')
        self.begin('A newer request.')
        result=self.state.execute(call,self.store.revision,Cancellation(),context=ctx)
        self.assertEqual(result.reason,'IntentSourceMismatch')

    def test_general_intent_reply_finishes_when_camera_drops_after_audio_starts(self):
        self.greet();current=[face(110)]
        class DisappearingSink(SyntheticSink):
            def play(self,chunks,cancel,on_start):
                def started(at):
                    on_start(at);current.clear();cancel.check()
                return super().play(chunks,cancel,started)
        self.session.output=OutputOwner(SyntheticTTS(),DisappearingSink(),self.state.trace)
        self.session.brain=IntentBrain()
        result=self.human('Hello again.',latest_faces=lambda now:current)
        self.assertEqual(result.status,'completed')
        self.assertTrue(result.completed_text)

    def test_audible_targeted_question_finishes_if_audience_leaves(self):
        self.greet();current=[face(110)]
        class DisappearingSink(SyntheticSink):
            def play(self,chunks,cancel,on_start):
                def started(at):
                    on_start(at);current.clear();cancel.check()
                return super().play(chunks,cancel,started)
        self.session.output=OutputOwner(SyntheticTTS(),DisappearingSink(),self.state.trace)
        self.session.brain=IntentBrain(lambda c:ToolCall('ask','ask_face_question',dict(
            text='What should I call you?',face_track_id='face',intent_update=proposal(c))))
        result=self.human('Hello again.',latest_faces=lambda now:current)
        self.assertEqual(result.status,'completed')
