"""Synthetic camera sequences through the production scheduler and output owner."""
import unittest

from r3x.contracts import ToolCall
from r3x.output import Delivery, OutputOwner
from r3x.proactive import Arrivals, Opportunity, Initiative
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests.test_memory_core import MemoryFixture
from tests.test_proactive import face
from tests import test_proactive_core
from tests.test_voice_core import Brain


class AudienceContinuityTests(unittest.TestCase):
    """October 2: presence is judged by identity and time, never by face-track IDs."""
    def settled(self, person='person'):
        arrivals=Arrivals()
        for at in (0,.5,1,1.5,2,2.5,3):
            arrivals.observe([face(at,person,'old')],at)
        return arrivals,arrivals.take('session',3,float('-inf'))

    def test_renumbered_track_and_brief_dropouts_are_the_same_visit(self):
        for mode in ('renumbered','unrecognized','dropout'):
            with self.subTest(mode=mode):
                arrivals,event=self.settled()
                rows={'renumbered':[face(3.5,'person','new')],'unrecognized':[face(3.5,None,'new')],'dropout':[]}[mode]
                arrivals.observe(rows,3.5)
                self.assertTrue(arrivals.still_present(event,3.5))

    def test_someone_else_or_a_real_departure_ends_the_greeting(self):
        arrivals,event=self.settled()
        arrivals.observe([face(3.5,'other','new')],3.5)
        self.assertFalse(arrivals.still_present(event,3.5))
        arrivals,event=self.settled()
        arrivals.observe([],20)
        self.assertFalse(arrivals.still_present(event,20))

    def test_unknown_visitor_counts_while_any_face_is_seen(self):
        arrivals,event=self.settled(person=None)
        arrivals.observe([face(4,None,'renumbered')],4)
        self.assertTrue(arrivals.still_present(event,4))
        arrivals.observe([],20)
        self.assertFalse(arrivals.still_present(event,20))


class AudienceRecoveryTests(MemoryFixture):
    setup_core=test_proactive_core.InitiativeCoreTests.setup_core
    turn=test_proactive_core.InitiativeCoreTests.turn
    visible=test_proactive_core.InitiativeCoreTests.visible

    def start(self):
        self.state.trace.at=100
        self.setup_core();self.turn();self.visible(108)
        self.session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.trace)

    def churn_camera(self):
        runtime=self.runtime
        from tests.test_agency_plan import draft
        self.session.agency_planner.choose=lambda f,c:{**draft(),'scope':'audience'}
        class Changed(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                runtime.arrivals.observe([face(108,track='replacement')],108)
                cancel.check()
                yield 'Same visitor, same goal.'
        self.session.brain=Changed()
        self.runtime.proactive_tick()

    def test_unsettled_face_membership_allows_generic_context_without_personal_audience(self):
        self.start()
        latest=[face(108,track='visitor'),dict(face(108,track='noise'),learning_quality=False,
                                            detector_score=.54,embedding_available=False)]
        self.runtime.camera.latest=lambda now:tuple(latest)
        self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls,2)
        self.assertEqual(len(self.state.initiative.attempts),1)
        context=self.brain.snapshots[-1].context
        self.assertEqual(context['faces'],[])
        self.assertEqual(context['memory_opportunities'],[])
        self.assertIsNone(context['personal_memory']['person_id'])

    def test_anonymous_track_replacement_neither_cancels_nor_replans_the_goal(self):
        # Live October 1: a present owner's renumbered face cancelled steps and forced replans.
        self.start();self.churn_camera()
        self.assertEqual(self.state.initiative.outcomes[-1]['status'],'completed')
        self.assertFalse(self.state.agency.dirty)
        plan_id=self.state.agency.record['id']
        follow=Brain('A newly considered follow-up.')
        self.session.brain=follow
        self.visible(124,track='replacement');self.runtime.proactive_tick()
        self.assertEqual(follow.calls,1)
        self.assertEqual(follow.snapshots[0].context['faces'][0]['track_id'],'replacement')
        self.assertEqual(self.state.agency.record['id'],plan_id)
        self.assertEqual(self.state.agency.record['cursor'],2)
        self.assertEqual(len(self.session.agency_planner.requests),1)



    def test_general_output_survives_supported_named_track_replacement(self):
        self.start()
        with self.store.transaction() as conn:person=self.store.add_person(conn,'Jordan',[],None)
        for at in (105,106,107,108):self.runtime.arrivals.observe([face(at,person,'known')],at)
        self.runtime.arrivals.seen[person]['used']=True
        runtime=self.runtime
        class Replacement(Brain):
            def reply(self,snapshot,cancel):
                self.calls+=1
                runtime.trace.at=108.1;runtime.audio_seen_at=108.1
                runtime.arrivals.observe([face(108.1,person,'new-known')],108.1)
                yield 'The spaceship probably runs on toast.'
        self.session.brain=Replacement();runtime.proactive_tick()
        self.assertTrue(self.state.initiative.outcomes[-1]['heard_complete_phrases'])
        self.assertFalse(any(r['event']=='proactive_retry_pending' for r in self.trace.rows))

    def test_targeted_question_follows_its_person_not_the_track_number(self):
        # Review October 3: the camera renumbers one face every few seconds, which cancelled
        # name questions before they were heard. A different person in their place still does.
        for replacement,expected in (('same','completed'),('other','interrupted'),('gone','interrupted')):
            with self.subTest(replacement=replacement):
                self.start()
                with self.store.transaction() as conn:
                    person=self.store.add_person(conn,'Jordan '+replacement,[],None)
                    other=self.store.add_person(conn,'Other '+replacement,[],None)
                for at in (105,106,107,108):self.runtime.arrivals.observe([face(at,person,'known')],at)
                self.runtime.arrivals.seen[person]['used']=True
                runtime=self.runtime
                rows={'same':[face(108.1,person,'new-known')],'other':[face(108.1,other,'new-known')],'gone':[]}[replacement]
                class Replacement(Brain):
                    def reply(self,snapshot,cancel):
                        runtime.trace.at=108.1;runtime.audio_seen_at=108.1
                        runtime.arrivals.observe(rows,108.1)
                        yield ToolCall('question','ask_face_question',dict(face_track_id='known',text='What is your name?'))
                self.session.brain=Replacement();runtime.proactive_tick()
                self.assertEqual(self.state.initiative.outcomes[-1]['status'],expected)
                self.state.initiative.forget();self.state.initiative.attempts.clear();self.state.pending=None

    def test_private_memory_stops_for_new_face_before_and_during_audio(self):
        from tests import test_memory_opportunities
        person=self.enroll(name='Avery')
        for during_audio in (False,True):
            with self.subTest(during_audio=during_audio):
                self.start()
                from tests.test_agency_plan import draft
                self.session.agency_planner.choose=lambda facts,cancel:dict(draft(),scope='audience')
                # Each arm has a separate cue within this temporary store.
                self.person=person
                from datetime import date
                self.state.calendar_today=lambda:date(2026,9,12)
                self.state.wall_clock=lambda:test_memory_opportunities.noon(date(2026,9,12))
                result=test_memory_opportunities.MemoryOpportunityTests.remember(self,predicate='test_plan_'+str(during_audio))
                self.assertEqual(result.outcome,'ok')
                runtime=self.runtime
                for at in (105,106,107,108):runtime.arrivals.observe([face(at,self.person,'known')],at)
                runtime.arrivals.seen[self.person]['used']=True
                person=self.person
                def new_face():
                    runtime.arrivals.observe([face(108,person,'known'),
                        dict(face(108,track='new'),learning_quality=False,detector_score=.54)],108)
                class Choice(Brain):
                    def reply(self,snapshot,cancel):
                        self.calls+=1
                        yield ToolCall('private-'+str(during_audio),'use_memory_opportunity',dict(
                            claim_id=snapshot.context['memory_opportunities'][0]['claim_id'],
                            text='How are preparations for your private plan going?'))
                class ChangedTTS(SyntheticTTS):
                    def synthesize(self,text,cancel):
                        new_face()
                        yield bytes(64)
                class ChangedSink(SyntheticSink):
                    def play(self,chunks,cancel,on_start):
                        def started(at):
                            on_start(at);new_face();cancel.check()
                        return super().play(chunks,cancel,started)
                self.session.brain=Choice()
                self.session.output=OutputOwner(SyntheticTTS() if during_audio else ChangedTTS(),
                    ChangedSink() if during_audio else SyntheticSink(),self.trace)
                runtime.proactive_tick()
                self.assertEqual(self.session.brain.calls,1)
                # Before audio a newcomer still withholds the private memory. Once audible,
                # the owner's September 27 rule lets the sentence finish.
                self.assertEqual(self.state.initiative.outcomes[-1]['status'],'completed' if during_audio else 'interrupted')
                if not during_audio:
                    self.assertFalse(self.store.rows('SELECT * FROM memory_followup_delivery'))
                self.assertFalse(self.state.agency.dirty)  # An unheard beat waits; the goal stays.
                self.state.initiative.forget();self.state.initiative.attempts.clear()
