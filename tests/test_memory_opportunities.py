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


def noon(day):
    from datetime import datetime, time
    return datetime.combine(day,time(12)).timestamp()


class MemoryOpportunityTests(MemoryFixture):
    def setup_person(self):
        self.person=self.enroll()
        self.state.calendar_today=lambda:date(2026,9,12)
        self.state.wall_clock=lambda:noon(date(2026,9,12))
        self.faces=(dict(face(100),identity=self.person),)

    def remember(self,kind='plan',on_date='2026-09-14',audience='subject_only',predicate='trip',
                 due_in_minutes=None,due_by=None,text='I am taking a trip on September 14. Ask me about it later.'):
        self.begin(text)
        return self.call('remember_followup',dict(predicate=predicate,value='Trip on September 14',kind=kind,
            on_date=on_date,due_in_minutes=due_in_minutes,due_by=due_by,audience=audience,
            source_turn_id=self.state.current[0]))

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
            on_date='09-12',due_in_minutes=None,due_by=None,audience='group',source_turn_id=self.state.current[0]))
        self.assertEqual(result.reason,'FollowupNeedsSupportedSelfIdentity')
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))

    def test_dates_and_restart_use_calendar_not_monotonic_capture_time(self):
        self.setup_person();self.remember('birthday','09-12')
        self.assertTrue(self.offered())
        restarted=CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        self.assertTrue(candidates(restarted.store,self.faces,date(2027,9,12),now=noon(date(2027,9,12))))
        self.assertFalse(candidates(restarted.store,self.faces,date(2026,9,13),now=noon(date(2026,9,13))))
        self.assertFalse(candidates(restarted.store,self.faces,date(2027,9,11),now=noon(date(2027,9,11))))

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

    def test_a_five_minute_plan_reads_as_over_the_next_day(self):
        # October 15 (2f85db5c): "In five minutes, I'm going to go to the store to get wine", told
        # the night before, was still offered as tonight's wine run; nothing said when it was told.
        from r3x import conversation_memory as cm
        self.setup_person()
        self.remember(kind='callback',on_date=None,predicate='plans to go to the store to buy wine',due_in_minutes=5,
                      text="In five minutes, I'm going to go to the store to get wine.")
        now=noon(date(2026,9,13))
        fact=cm.context(self.store,{**self.state.context(),'faces':list(self.faces)},'later-session',now)['interests_and_facts'][0]
        self.assertEqual(fact['told'],'about 24 hours ago (Saturday 12:00 p.m.)')
        self.assertTrue(fact['planned_time'].startswith('its planned time was about 24 hours ago'),fact['planned_time'])
        self.assertNotIn('told_at',fact);self.assertNotIn('due_at',fact)

    def test_a_correction_that_changes_nothing_is_refused_and_keeps_its_source(self):
        self.setup_person();row=self.remember()
        source=self.store.claims()[0]['source_turn']
        self.begin('Do you know what room this is?')
        result=self.call('correct_claim',dict(claim_id=row.data['claim_id'],value='Trip on September 14',
                                              source_turn_id=self.state.current[0]))
        self.assertEqual((result.outcome,result.reason),('rejected','CorrectionUnchanged'))
        self.assertEqual(self.store.claims()[0]['source_turn'],source)
        self.assertTrue(self.offered())

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
            self.assertFalse(candidates(self.store,self.faces,date(2026,9,13),now=noon(date(2026,9,13))))
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
        self.assertTrue(candidates(self.store,self.faces,date(2026,9,13),now=noon(date(2026,9,13))))
        self.assertFalse(candidates(self.store,self.faces,date(2026,9,28),now=noon(date(2026,9,28))))
        self.remember('birthday','02-29',predicate='birthday')
        self.assertTrue(any(r['kind']=='birthday' for r in candidates(self.store,self.faces,date(2028,2,29),now=noon(date(2028,2,29)))))
        self.assertFalse(candidates(self.store,self.faces,date(2027,3,1),now=noon(date(2027,3,1))))
        self.remember('celebration','2026-09-12',predicate='milestone')
        self.assertTrue(any(r['kind']=='celebration' for r in self.offered()))
        self.assertFalse(any(r['kind']=='celebration' for r in candidates(self.store,self.faces,date(2027,9,12),now=noon(date(2027,9,12)))))

    def test_existing_database_gains_additive_tables_without_losing_claims(self):
        from r3x.memory import Memory
        self.setup_person();self.remember()
        self.store.connection.execute('DROP TABLE memory_followup_delivery')
        self.store.connection.execute('DROP TABLE memory_followups')
        self.store.close();self.store=Memory(self.path);self.store.start()
        self.assertEqual(len(self.store.claims()),1)
        self.assertEqual(self.store.person(self.person)['status'],'supported')
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))

    def arrival(self,state):
        return state.opportunity_context(Opportunity(uuid4().hex,'session',100,'face',self.person,self.faces,
                                                     float('-inf'),False))['memory_opportunities']

    def test_wine_run_in_five_minutes_is_asked_about_once_after_restart(self):
        # Live October 14: "in five minutes" was saved without a time; after a restart five minutes
        # later Rex planned to tease the "imminent wine mission" instead of asking how it went.
        self.setup_person();said=noon(date(2026,9,12))
        result=self.remember('callback',None,predicate='wine run',due_in_minutes=5,
                             text='In five minutes I am going to the store to get wine.')
        self.assertEqual(result.outcome,'ok');self.assertEqual(result.data['due'],'Saturday 12:05 p.m.')
        row=self.offered()[0]
        self.assertEqual((row['moment'],row['occasion']),('ahead','before_due'))
        self.assertEqual(row['due'],'not yet: due in about 5 minutes (Saturday 12:05 p.m.)')
        self.assertEqual(row['said'],'under a minute ago (Saturday 12:00 p.m.)')
        restarted=CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        restarted.calendar_today=self.state.calendar_today;restarted.wall_clock=lambda:said+8*60
        row=self.arrival(restarted)[0]
        self.assertEqual((row['moment'],row['occasion']),('passed','after_due'))
        self.assertEqual(row['due'],'its planned time was about 3 minutes ago (Saturday 12:05 p.m.); '
                                    'whether it happened is unknown')
        self.assertEqual(row['said'],'about 8 minutes ago (Saturday 12:00 p.m.)')
        class Ask(Brain):
            def reply(self,snapshot,cancel):
                yield ToolCall(uuid4().hex,'use_memory_opportunity',dict(
                    claim_id=snapshot.context['memory_opportunities'][0]['claim_id'],text='Did the wine run happen?'))
        session=Session('session',Ask(),OutputOwner(SyntheticTTS(),SyntheticSink(),restarted.trace),restarted.trace,restarted)
        try:
            event=Opportunity(uuid4().hex,'session',100,'face',self.person,self.faces,float('-inf'),False)
            self.assertEqual(session.process_opportunity(event,0,lambda:True).completed_text,['Did the wine run happen?'])
        finally:session.close()
        self.assertEqual([r['occasion'] for r in self.store.rows('SELECT * FROM memory_followup_delivery')],['after_due'])
        self.assertFalse(self.arrival(restarted))
        restarted.wall_clock=lambda:said+8*86400;self.assertFalse(self.arrival(restarted))

    def test_this_weekend_is_due_at_the_end_of_sunday(self):
        self.setup_person()   # Saturday, September 12, 2026.
        self.assertEqual(self.remember('callback',None,due_by='2026-09-13',text='I am camping this weekend.').data['due'],
                         'Sunday 11:59 p.m.')
        self.assertEqual(self.offered()[0]['moment'],'ahead')
        self.state.wall_clock=lambda:noon(date(2026,9,14))
        self.assertEqual(self.offered()[0]['due'],'its planned time was about 12 hours ago (Sunday 11:59 p.m.); '
                                                  'whether it happened is unknown')

    def test_dated_plan_counts_as_due_at_the_end_of_its_day(self):
        self.setup_person();self.remember()   # Trip on September 14, no explicit time.
        self.assertEqual(self.offered()[0]['moment'],'ahead')
        self.state.wall_clock=lambda:noon(date(2026,9,15));self.state.calendar_today=lambda:date(2026,9,15)
        self.assertEqual(self.offered()[0]['moment'],'passed')
        self.remember('callback',None,predicate='someday',text='Maybe I will repaint the den someday.')
        self.assertEqual({r['predicate']:r['moment'] for r in self.offered()}['someday'],'no_time_given')

    def test_due_time_needs_one_clear_future_time_and_rejects_atomically(self):
        self.setup_person()
        for kind,on_date,minutes,by,reason in [
                ('callback',None,5,'2026-09-13','FollowupDueTwice'),
                ('birthday','09-12',None,'2026-09-13','FollowupDueNotForOccasions'),
                ('callback',None,None,'2026-09-11','FollowupDueInPast'),
                ('callback',None,None,'Sunday','InvalidFollowupDue'),
                ('callback',None,None,'2026-09-13T25:00','InvalidFollowupDue'),
                ('callback',None,0,None,'InvalidFollowupDueMinutes')]:
            with self.subTest(reason=reason):
                self.assertEqual(self.remember(kind,on_date,due_in_minutes=minutes,due_by=by).reason,reason)
        self.assertFalse(self.store.claims())
        self.assertFalse(self.store.rows('SELECT * FROM memory_followups'))
        self.assertEqual(self.remember('callback',None,due_by='2026-09-12T15:30').data['due'],'Saturday 3:30 p.m.')

    def test_passed_followup_leads_every_rotation(self):
        self.setup_person()
        for i in range(8):self.remember('callback',None,predicate='interest'+str(i))
        self.remember('callback',None,predicate='wine run',due_in_minutes=5)
        self.state.wall_clock=lambda:noon(date(2026,9,12))+600
        for _ in range(3):
            rows=self.offered();self.assertEqual(rows[0]['predicate'],'wine run');self.assertEqual(len(rows),6)

    def test_existing_followups_gain_said_and_due_columns(self):
        from r3x.memory import Memory
        self.setup_person();self.remember('callback',None)
        for column in ('said_at','due_at'):
            self.store.connection.execute(f'ALTER TABLE memory_followups DROP COLUMN {column}')
        self.store.close();self.store=Memory(self.path);self.store.start();self.state.store=self.store
        row=self.offered()[0]
        self.assertEqual((row['said'],row['moment']),('unknown: saved before Rex kept the time','no_time_given'))

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
