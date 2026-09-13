from datetime import date
from uuid import uuid4

from r3x.contracts import Cancellation, ToolCall
from r3x.memory_opportunities import candidates
from r3x.output import OutputOwner, TextOutput
from r3x.proactive import Opportunity
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.state import CoreState
from r3x.speakers import SpeakerTracker
from tests.test_memory_core import MemoryFixture, B, NS
from tests.test_proactive import face
from tests.test_voice_core import Brain


class MemoryOpportunityTests(MemoryFixture):
    def setup_person(self):
        self.person=self.enroll()
        self.state.calendar_today=lambda:date(2026,9,12)
        self.faces=(dict(face(100),identity=self.person),)

    def remember(self,kind='plan',on_date='2026-09-14',audience='subject_only',predicate='trip'):
        self.begin('I am taking a trip on September 14. Ask me about it later.')
        return self.call('remember_followup',dict(predicate=predicate,value='Trip on September 14',kind=kind,
            on_date=on_date,audience=audience,source_turn_id=self.state.current[0]))

    def event(self,faces=None):
        return Opportunity(uuid4().hex,'session',100,None,None,faces or self.faces,float('-inf'),False,
                           'conversation_lull',self.state.current[0],8.)

    def offered(self,faces=None):
        return self.state.opportunity_context(self.event(faces))['memory_opportunities']

    def test_private_self_report_is_only_offered_to_sole_visible_subject(self):
        self.setup_person();result=self.remember();self.assertEqual(result.outcome,'ok')
        rows=self.offered();self.assertEqual(rows[0]['claim_id'],result.data['claim_id'])
        self.assertEqual(rows[0]['said_by'],self.person)
        self.assertIn('Ask me about it later',rows[0]['source_text'])
        self.assertFalse(self.offered((*self.faces,face(100,track='visitor'))))
        self.assertFalse(self.offered((face(100,track='visitor'),)))
        self.assertFalse(self.state.opportunity_context(self.event())['claims'])

    def test_explicit_group_scope_still_requires_visible_subject(self):
        self.setup_person();self.remember(audience='group')
        self.assertTrue(self.offered((*self.faces,face(100,track='visitor'))))
        self.assertFalse(self.offered((face(100,track='visitor'),)))

    def test_unknown_speaker_cannot_schedule_named_persons_followup(self):
        self.setup_person();self.begin('Bret has a birthday soon.',B)
        result=self.call('remember_followup',dict(predicate='birthday',value='September 12',kind='birthday',
            on_date='09-12',audience='group',source_turn_id=self.state.current[0]))
        self.assertEqual(result.reason,'FollowupNeedsSupportedSelfIdentity')
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))

    def test_dates_and_restart_use_calendar_not_monotonic_capture_time(self):
        self.setup_person();self.remember('birthday','09-12')
        self.assertTrue(self.offered())
        restarted=CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        self.assertTrue(candidates(restarted.store,self.faces,date(2027,9,12)))
        self.assertFalse(candidates(restarted.store,self.faces,date(2026,9,13)))
        self.assertFalse(candidates(restarted.store,self.faces,date(2027,9,11)))

    def test_invalid_date_or_conflicting_claim_rolls_back_atomically(self):
        self.setup_person()
        for kind,day in [('plan','tomorrow'),('birthday','02-30'),('callback','2026-09-12')]:
            self.assertEqual(self.remember(kind,day).outcome,'rejected')
        self.assertFalse(self.store.claims())
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))

    def test_correction_and_retraction_retire_old_date_and_permission(self):
        self.setup_person();row=self.remember(audience='group')
        self.begin('Actually that trip was cancelled.')
        self.assertEqual(self.call('correct_claim',dict(claim_id=row.data['claim_id'],value='Cancelled',
            source_turn_id=self.state.current[0])).outcome,'ok')
        self.assertFalse(self.offered())
        self.assertEqual(len(self.store.claims()),1)

    def test_delivery_persists_across_restart_but_text_output_is_not_heard(self):
        self.setup_person();self.remember()
        class Choice(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall(uuid4().hex,'use_memory_opportunity',dict(
                    claim_id=snapshot.context['memory_opportunities'][0]['claim_id'],text='How are the trip preparations going?'))
        brain=Choice();session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        try:
            self.assertEqual(session.process_opportunity(self.event(),0,lambda:True).status,'completed')
            self.assertFalse(self.store.rows('SELECT * FROM memory_followup_delivery'))
            session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace)
            self.assertEqual(session.process_opportunity(self.event(),0,lambda:True).status,'completed')
            self.assertEqual(brain.calls,2)
            self.assertEqual(len(self.store.rows('SELECT * FROM memory_followup_delivery')),1)
            self.assertFalse(candidates(self.store,self.faces,date(2026,9,13)))
            self.assertEqual(len(self.store.claims()),1)
        finally:session.close()

    def test_stale_revision_or_changed_audience_cannot_select_private_memory(self):
        self.setup_person();row=self.remember();context=self.state.opportunity_context(self.event())
        context['faces'].append(face(100,track='visitor'))
        call=ToolCall('selection','use_memory_opportunity',dict(claim_id=row.data['claim_id'],text='Your trip!'))
        self.assertEqual(self.state.execute_opportunity(call,context,Cancellation()).reason,'MemoryOpportunityUnavailable')
        context=self.state.opportunity_context(self.event());context['state_revision']=-1
        self.assertEqual(self.state.execute_opportunity(call,context,Cancellation()).reason,'StaleStateRevision')

    def test_bounded_rotation_and_forgetting(self):
        self.setup_person()
        for i in range(8):self.remember('callback',None,predicate='interest'+str(i))
        first={r['claim_id'] for r in self.offered()};second={r['claim_id'] for r in self.offered()}
        self.assertEqual(len(first),6);self.assertEqual(len(first|second),8)
        self.assertEqual(self.call('forget_person',{'person_id':self.person}).outcome,'ok')
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))
        self.assertFalse(self.store.rows('SELECT * FROM memory_followup_delivery'))

    def test_plan_window_celebration_day_and_leap_birthday(self):
        self.setup_person();self.remember('plan','2026-09-20')
        self.assertFalse(self.offered())
        self.assertTrue(candidates(self.store,self.faces,date(2026,9,13)))
        self.assertFalse(candidates(self.store,self.faces,date(2026,9,28)))
        self.remember('birthday','02-29',predicate='birthday')
        self.assertTrue(any(r['kind']=='birthday' for r in candidates(self.store,self.faces,date(2028,2,29))))
        self.assertFalse(candidates(self.store,self.faces,date(2027,3,1)))
        self.remember('celebration','2026-09-12',predicate='milestone')
        self.assertTrue(any(r['kind']=='celebration' for r in self.offered()))
        self.assertFalse(any(r['kind']=='celebration' for r in candidates(self.store,self.faces,date(2027,9,12))))

    def test_existing_database_gains_additive_tables_without_losing_claims(self):
        from r3x.memory import Memory
        self.setup_person();self.remember()
        self.store.connection.execute('DROP TABLE memory_followup_delivery')
        self.store.connection.execute('DROP TABLE memory_followups')
        self.store.close();self.store=Memory(self.path);self.store.start()
        self.assertEqual(len(self.store.claims()),1)
        self.assertEqual(self.store.person(self.person)['status'],'supported')
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))

    def test_midnight_during_tts_cancels_before_audio_and_does_not_consume_birthday(self):
        self.setup_person();self.remember('birthday','09-12')
        state=self.state
        class Choice(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                yield ToolCall(uuid4().hex,'use_memory_opportunity',dict(
                    claim_id=snapshot.context['memory_opportunities'][0]['claim_id'],text='Happy birthday, Avery!'))
        class MidnightTTS:
            def synthesize(self,text,cancel):
                state.calendar_today=lambda:date(2026,9,13)
                yield bytes(64)
        session=Session('session',Choice(),OutputOwner(MidnightTTS(),SyntheticSink(),state.trace),state.trace,state)
        try:
            result=session.process_opportunity(self.event(),0,lambda:True)
            self.assertEqual(result.status,'interrupted');self.assertEqual(result.completed_text,[])
            self.assertFalse(self.store.rows('SELECT * FROM memory_followup_delivery'))
        finally:session.close()

    def test_conflicting_followup_claim_does_not_commit_candidate_or_schedule(self):
        self.setup_person();self.begin('My trip is October 5.')
        self.assertEqual(self.call('record_claim',dict(subject_id=self.person,subject_reference=None,
            predicate='trip',value='Trip on October 5',kind='self_report',source_turn_id=self.state.current[0])).outcome,'ok')
        result=self.remember()
        self.assertEqual(result.reason,'FollowupNeedsAcceptedClaim')
        self.assertEqual(len(self.store.claims(include_candidates=True)),1)
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))
