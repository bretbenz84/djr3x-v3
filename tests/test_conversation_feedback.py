"""Source/participant/delivery contracts; scripted semantics do not prove LLM judgment."""
from types import SimpleNamespace
from uuid import uuid4

from r3x.contracts import Capture, Transcript, ToolCall
from r3x.output import TextOutput
from r3x.proactive import HUMAN_FLOOR_SECONDS, Opportunity
from r3x.session import Session
from r3x.state import CoreState
from r3x.speakers import SpeakerTracker
from tests.test_memory_core import MemoryFixture, observation, B, NS
from tests.test_voice_core import Brain


class FeedbackTests(MemoryFixture):
    def feedback(self, action='topic_avoid', reference=None, topic='work', text=None, **kw):
        return self.call('conversation_feedback',dict(action=action,reference_id=reference,
            topic=topic,text=text,source_turn_id=self.state.current[0]),**kw)

    def receipt(self, medium='audio', target=None):
        event=Opportunity(uuid4().hex,'session',90,None,target,(),0,False,
                          'conversation_lull','old-source')
        self.state.initiative.finished(event,SimpleNamespace(completed_text=['Want to talk about the trip?'],
                                      medium=medium,status='completed'),90)
        return event.event_id

    def test_other_participant_speech_does_not_settle_invitation_or_question(self):
        reference=self.receipt()
        self.state.question_delivered('What is your name?',None,90)
        self.begin('Rex, is that camera new?',B)
        self.assertFalse(self.state.initiative.feedback)
        self.assertEqual(self.state.initiative.outcomes[-1]['response'],'unknown')
        self.begin('No thanks, I would rather not talk about the trip.',B)
        result=self.feedback('invitation_declined',reference,None)
        self.assertEqual(result.outcome,'ok')  # Meaning is a model proposal, not guessed from speech.
        self.assertEqual(self.state.pending['text'],'What is your name?')
        self.assertEqual(self.state.initiative.feedback[-1]['participant']['track_id'],self.state.current[4].track_id)
        self.assertIsNone(self.state.initiative.feedback[-1]['participant']['person_id'])

    def test_targeted_invitation_cannot_be_answered_as_another_person(self):
        person=self.enroll()
        reference=self.receipt(target=person)
        self.begin('No thanks.',B)
        self.assertEqual(self.feedback('invitation_declined',reference,None).reason,'FeedbackTargetMismatch')

    def test_unheard_expired_and_nonexistent_invitation_rejected(self):
        self.begin('No thanks.')
        for medium in ('text','none'):
            reference=self.receipt(medium)
            self.assertEqual(self.feedback('invitation_declined',reference,None).reason,'FeedbackNeedsAudibleOpportunity')
        self.assertEqual(self.feedback('invitation_answered','fake',None).reason,'FeedbackNeedsAudibleOpportunity')
        reference=self.receipt();self.state.trace.at=400
        self.assertEqual(self.feedback('invitation_answered',reference,None).reason,'FeedbackOpportunityExpired')

    def test_topic_preference_owned_by_speaker_not_mentioned_person(self):
        self.begin('Tell Sam I do not want you bringing up work.')
        source=self.state.current[0];revision=self.store.revision
        result=self.feedback();self.assertEqual(result.outcome,'ok')
        note=self.state.initiative.feedback[-1]
        self.assertEqual(note['source_turn_id'],source)
        self.assertEqual(note['source_text'],'Tell Sam I do not want you bringing up work.')
        self.assertIsNone(note['participant']['person_id'])
        self.assertGreater(self.store.revision,revision)
        self.assertEqual(self.store.claims(),[])
        self.assertFalse(self.state.initiative.quiet)

    def test_only_owner_can_reopen_or_correct_topic_and_retry_is_idempotent(self):
        self.begin('No work talk please.');result=self.feedback(call_id='same')
        self.assertEqual(self.feedback(call_id='same').outcome,'ok')
        self.assertEqual(len(self.state.initiative.feedback),1)
        self.begin('Work is fine with me.',B)
        self.assertEqual(self.feedback('retract',result.data['id'],None).reason,'FeedbackBelongsToAnotherParticipant')
        self.begin('Actually, I am ready to talk about work.')
        self.assertEqual(self.feedback('retract',result.data['id'],None).outcome,'ok')
        self.assertFalse(self.state.initiative.feedback)

    def test_untrusted_stale_frozen_and_invalid_fields_do_not_apply(self):
        self.begin('No work talk please.',trusted=False)
        self.assertEqual(self.feedback().reason,'UntrustedSource')
        self.begin('No work talk please.')
        self.assertEqual(self.feedback(revision=-1).reason,'StaleStateRevision')
        self.state.frozen=True
        self.assertEqual(self.feedback().reason,'FrozenEvaluationDisallowsMutation')
        self.state.frozen=False
        self.assertEqual(self.feedback('topic_avoid','unneeded','work').reason,'FeedbackFieldsMismatch')
        self.assertEqual(self.feedback('topic_avoid',None,None).reason,'FeedbackFieldsMismatch')
        self.assertFalse(self.state.initiative.feedback)

    def test_floor_is_bounded_and_quietness_does_not_create_disengagement(self):
        self.begin('Sam, how did your interview go?')
        self.assertEqual(self.call('remain_silent',{'reason':'other_addressee'}).outcome,'ok')
        self.begin('Rex, what time is it?',B)
        self.assertEqual(self.state.context()['participation']['feedback'][0]['action'],'yield_floor')
        self.state.trace.at=220
        self.assertEqual(self.state.context()['participation']['feedback'],[])
        self.assertFalse(self.state.initiative.quiet)

    def test_forget_and_restart_remove_social_source_context(self):
        person=self.enroll();self.begin('Do not bring up work.')
        self.feedback();self.receipt()
        self.assertEqual(self.call('forget_person',{'person_id':person}).outcome,'ok')
        self.assertFalse(self.state.initiative.feedback)
        self.assertFalse(self.state.initiative.outcomes)
        self.begin('I am done talking about weather.');self.feedback('topic_closed',None,'weather')
        restarted=CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        self.assertFalse(restarted.initiative.feedback)

    def session_turn(self,session,text):
        at=self.state.trace.at
        capture=Capture(uuid4().hex,'session',bytes(96000),at-4,at-1,at,voiced_intervals=((0,3),))
        return session.process_turn(capture,Transcript(text,-.01,True),0,observation())

    def test_feedback_reply_uses_one_generation_and_delivery_owner(self):
        class FeedbackBrain(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall(uuid4().hex,'conversation_feedback',dict(action='topic_closed',
                    reference_id=None,topic='work',source_turn_id=snapshot.context['source_turn_id'],
                    text='Then the workday is officially off duty.'))
        brain=FeedbackBrain();session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        try:
            result=self.session_turn(session,'Enough about work, Rex. Change the subject.')
            self.assertEqual(brain.calls,1)
            self.assertEqual(result.completed_text,['Then the workday is officially off duty.'])
            self.assertEqual(result.status,'completed')
            self.assertNotIn('text',self.state.initiative.feedback[-1])
            session.brain=Brain('I have a deeply unreasonable opinion about elevators.')
            result=self.session_turn(session,'Rex, surprise me.')
            self.assertEqual(session.brain.calls,1)
            self.assertEqual(result.status,'completed')
        finally:session.close()

    def test_silent_feedback_needs_no_continuation_or_false_reply(self):
        class FloorBrain(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall(uuid4().hex,'remain_silent',{'reason':'other_addressee'})
        brain=FloorBrain();session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        try:
            heard=self.state.trace.at-1
            result=self.session_turn(session,'Sam, what happened next?')
            self.assertEqual(result.status,'suppressed');self.assertEqual(brain.calls,1)
            self.assertEqual(result.completed_text,[])
            self.assertEqual(session.proactive_hold_until,heard+HUMAN_FLOOR_SECONDS)
        finally:session.close()

    def test_boundary_survives_feedback_eviction_and_capacity_rejects_without_acknowledging(self):
        self.begin('No work talk please.');boundary=self.feedback().data['id']
        for _ in range(35):
            self.begin('We are done with that topic.');self.feedback('topic_closed',None,'weather')
        self.assertEqual(len(self.state.initiative.feedback),32)
        self.assertIn(boundary,[r['id'] for r in self.state.initiative.feedback])
        self.state.initiative.feedback.clear()
        for i in range(32):
            self.begin('Please avoid that subject.');self.feedback(topic=str(i))
        self.assertEqual(self.feedback(topic='another').reason,'FeedbackCapacityReached')

    def test_identity_association_correction_removes_affected_notes(self):
        person=self.introduce();self.begin('No work talk please.')
        self.feedback();self.assertTrue(self.state.initiative.feedback)
        track=self.state.current[4].track_id
        result=self.call('cancel_association',{'person_id':person,'track_id':None})
        self.assertEqual(result.outcome,'ok')
        self.assertFalse(self.state.initiative.feedback)

    def test_feedback_cannot_use_source_from_before_the_opportunity(self):
        self.begin('Maybe.')
        reference=self.receipt()
        self.state.initiative.outcomes[-1]['source_turn_id']=self.state.current[0]
        self.assertEqual(self.feedback('invitation_answered',reference,None).reason,'FeedbackSourcePrecedesOpportunity')

    def test_failed_output_preserves_observed_boundary_without_claiming_spoken_reply(self):
        from r3x.output import OutputOwner
        from r3x.scenario import SyntheticSink
        class FailedTTS:
            def synthesize(self,text,cancel):
                raise RuntimeError('synthetic failure')
                yield b''
        class FeedbackBrain(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall(uuid4().hex,'conversation_feedback',dict(action='topic_avoid',
                    reference_id=None,topic='work',source_turn_id=snapshot.context['source_turn_id'],text='Work stays off duty.'))
        brain=FeedbackBrain()
        session=Session('session',brain,OutputOwner(FailedTTS(),SyntheticSink(),self.state.trace),self.state.trace,self.state)
        try:
            result=self.session_turn(session,'Please do not bring up work.')
            self.assertEqual(result.status,'failed');self.assertEqual(result.completed_text,[])
            self.assertEqual(brain.calls,1);self.assertTrue(self.state.initiative.feedback)
            self.assertFalse(any(m.get('role')=='assistant' for m in session.history_messages()))
        finally:session.close()

    def test_silence_retained_without_note_for_untrusted_or_frozen_source(self):
        self.begin('Sam, what happened?',trusted=False)
        self.assertEqual(self.call('remain_silent',{'reason':'other_addressee'}).outcome,'ok')
        self.assertFalse(self.state.initiative.feedback)
        self.begin('Sam, what happened?');self.state.frozen=True
        revision=self.store.revision
        self.assertEqual(self.call('remain_silent',{'reason':'other_addressee'}).outcome,'ok')
        self.assertFalse(self.state.initiative.feedback)
        self.assertEqual(self.store.revision,revision)

    def test_null_feedback_is_silent_without_global_quiet_boundary(self):
        class DeclineBrain(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall(uuid4().hex,'conversation_feedback',dict(action='topic_avoid',
                    reference_id=None,topic='work',source_turn_id=snapshot.context['source_turn_id'],text=None))
        brain=DeclineBrain();session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        try:
            result=self.session_turn(session,'No work talk please, no need to respond.')
            self.assertEqual(result.status,'suppressed');self.assertEqual(brain.calls,1)
            self.assertEqual(result.completed_text,[]);self.assertFalse(self.state.initiative.quiet)
        finally:session.close()

    def test_floor_survives_direct_reply_and_history_eviction_then_semantic_answer_releases_it(self):
        self.begin('Sam, how was your interview?')
        self.call('remain_silent',{'reason':'other_addressee'})
        reference=self.state.initiative.feedback[-1]['id']
        brain=Brain('Four.');session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        try:
            self.session_turn(session,'Rex, what is two plus two?')
            self.assertEqual(brain.calls,1);session.history.clear()
            event=Opportunity(uuid4().hex,'session',100,None,None,(),session._latest_speech,False,
                              'conversation_lull',self.state.current[0],8.)
            result=session.process_opportunity(event,0,lambda:True)
            self.assertEqual(result.status,'suppressed');self.assertEqual(brain.calls,1)
            self.assertFalse(self.state.initiative.attempts)
            self.assertFalse(not self.state.initiative.floor_held(108))
            self.begin('The interview went well, thanks for asking.',B)
            result=self.feedback('floor_resolved',reference,None)
            self.assertEqual(result.outcome,'ok')
            self.assertFalse(self.state.initiative.floor_held(108))
            self.assertTrue(not self.state.initiative.floor_held(108))
        finally:session.close()

    def test_floor_resolution_cannot_remove_someone_elses_topic_boundary(self):
        self.begin('No work talk.');reference=self.feedback().data['id']
        self.begin('That interview went well.',B)
        self.assertEqual(self.feedback('floor_resolved',reference,None).reason,'NotAHumanFloorReference')
        self.assertTrue(self.state.initiative.feedback)
