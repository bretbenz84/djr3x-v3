"""Cross-session recall through production owners; synthetic identity/audio only."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from r3x import conversation_memory as cm
from r3x.contracts import Capture, Transcript, ToolCall, Cancellation
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.state import CoreState
from r3x.speakers import SpeakerTracker
from r3x.memory import Memory
from tests.test_memory_core import MemoryFixture, observation, NS
from tests.test_voice_core import Brain
from tests.test_proactive import face, event


class ConversationMemoryTests(MemoryFixture):
    def setup_memory(self):
        self.person = self.enroll('Alex')
        self.clock = 1800000000.
        self.state.wall_clock = lambda:self.clock

    def session(self, name, brain=None, sink=None):
        return Session(name,brain or Brain('That telescope project sounds fun.'),
            OutputOwner(SyntheticTTS(),sink or SyntheticSink(),self.state.trace),self.state.trace,self.state)

    def turn(self, session, text, faces=(), voice=None):
        cap = Capture(uuid4().hex,session.session_id,bytes(96000),96,99,100,voiced_intervals=((0.,3.),))
        return session.process_turn(cap,Transcript(text,0,True),0,voice or observation(),faces)

    def test_restart_restores_original_exchange_and_only_delivered_speech(self):
        self.setup_memory()
        self.turn(self.session('yesterday'),'I finished the telescope mount.')
        self.store.close()
        self.store = Memory(self.path); self.store.start()
        self.state = CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        self.state.wall_clock = lambda:self.clock+3600
        brain = Brain('How is the mount working?')
        self.turn(self.session('today',brain),'Hello again.')
        memory = brain.snapshots[0].context['personal_memory']
        self.assertEqual(memory['person_id'],self.person)
        self.assertEqual(memory['recent_exchanges'][0]['human_text'],'I finished the telescope mount.')
        self.assertEqual(memory['recent_exchanges'][0]['heard_text'],'That telescope project sounds fun.')
        self.assertEqual(memory['recent_exchanges'][0]['session_id'],'yesterday')

    def test_interruption_does_not_remember_generated_speech_as_delivered(self):
        from r3x.contracts import Cancelled
        class Sink:
            def play(self, chunks, cancel, on_start):
                list(chunks)
                raise Cancelled()
        self.setup_memory()
        self.turn(self.session('old',sink=Sink()),'I built a telescope.')
        rows = cm.recall(self.store,self.person,'new')['exchanges']
        self.assertEqual(rows[0]['heard_text'],'')
        self.assertEqual(rows[0]['status'],'interrupted')

    def test_llm_recall_tool_returns_evidence_in_same_conversation_owner(self):
        self.setup_memory(); self.turn(self.session('old'),'I finished the telescope mount.')
        outer = self
        class Recall:
            snapshots=[]
            def reply(self,snapshot,cancel):
                self.snapshots.append(snapshot)
                if len(self.snapshots)==1:
                    yield ToolCall('recall-old','recall_conversations',{'query':'telescope','after':None,'before':None})
                else:
                    yield 'We discussed the telescope mount.'
        brain=Recall()
        result=self.turn(self.session('new',brain),'What did we discuss earlier?')
        self.assertEqual(result.status,'completed')
        self.assertEqual(len(brain.snapshots),2)
        self.assertIn('I finished the telescope mount.',str(brain.snapshots[-1].continuation))
        self.assertNotIn('recent_exchanges',brain.snapshots[-1].context['personal_memory'])

    def test_greeting_receives_accepted_interests_without_followup_registration(self):
        self.setup_memory()
        self.begin('I like astronomy.')
        self.call('record_claim',dict(subject_id=self.person,predicate='likes',value='astronomy',kind='self_report',source_turn_id=self.state.current[0]))
        brain=Brain('Been doing any astronomy lately?')
        greeting=replace(event(self.person),session_id='new')
        result=self.session('new',brain).process_opportunity(greeting,0,lambda:True)
        self.assertEqual(result.status,'completed')
        self.assertEqual(brain.snapshots[0].context['personal_memory']['interests_and_facts'][0]['value'],'astronomy')
        self.assertEqual(self.store.rows('SELECT * FROM memory_followups'),[])

    def test_unknown_or_changed_group_cannot_read_private_exchanges(self):
        self.setup_memory();self.turn(self.session('old'),'A private unfinished project.')
        base=self.state.context()
        self.assertEqual(cm.subject(base),self.person)
        group=dict(base,faces=[face(100,self.person),face(100,None,'visitor')])
        self.assertIsNone(cm.subject(group))
        self.assertEqual(cm.context(self.store,group,'new',self.clock)['recent_exchanges'],[])
        with self.assertRaises(ValueError):cm.recall(self.store,None,'new')

    def test_uncertain_voice_is_saved_with_audience_without_certifying_speaker(self):
        from r3x.speakers import VoiceObservation
        self.setup_memory()
        self.turn(self.session('old'),'Uncertain words.',faces=[face(100,self.person)],voice=VoiceObservation(NS))
        row = cm.recall(self.store,self.person,'new')['exchanges'][0]
        self.assertEqual(row['human_text'],'Uncertain words.')
        self.assertEqual(row['audience_person_id'],self.person)
        self.assertIsNone(row['attribution']['speaker']['person_id'])
        self.assertEqual(row['attribution']['speaker']['state'],'unresolved')
        self.assertEqual(row['attribution']['audience_basis'],'recognized_visible_person')
        self.assertEqual(self.store.claims(self.person),[])

    def test_window_abstention_story_survives_restart_and_production_recall(self):
        from r3x.speakers import VoiceObservation
        from tests.test_memory_core import A, B
        self.setup_memory()
        old=self.session('old')
        visible=[face(100,self.person)]
        self.turn(old,'I am relaxing.',faces=visible)
        voice=VoiceObservation(NS,A,((0.,2.,A),(2.,3.,B)),3.,.7)
        self.turn(old,'Our office switch kept disconnecting for three hours.',faces=visible,voice=voice)
        self.assertEqual(self.state.current[4].reason,'window_identity_conflict')
        self.turn(old,'It turned out to be a firmware uptime bug.',faces=visible,voice=voice)
        self.turn(old,'That cough was Jamie, not me.',faces=visible,voice=VoiceObservation(NS))
        self.store.close()
        self.store=Memory(self.path); self.store.start()
        self.state=CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        self.clock+=300
        self.state.wall_clock=lambda:self.clock
        class Recall:
            snapshots=[]
            def reply(self,snapshot,cancel):
                self.snapshots.append(snapshot)
                if len(self.snapshots)==1:
                    yield ToolCall('earlier','recall_conversations',{'query':'switch firmware','after':3600,'before':0})
                else:
                    yield 'We discussed an office switch outage caused by an uptime bug.'
        brain=Recall()
        result=self.turn(self.session('new',brain),'What was the work story?',faces=visible,voice=VoiceObservation(NS))
        self.assertEqual(result.status,'completed')
        evidence=str(brain.snapshots[-1].continuation)
        for text in ('three hours','uptime bug','Jamie','window_identity_conflict'):
            self.assertIn(text,evidence)
        prior=brain.snapshots[0].context['personal_memory']['recent_exchanges']
        story=next(row for row in prior if 'office switch' in row['human_text'])
        self.assertIsNone(story['attribution']['speaker']['person_id'])
        self.assertEqual(self.store.claims(self.person),[])
        self.assertTrue(any(row['event']=='conversation_memory_record' and row['status']=='recorded'
                            for row in self.state.trace.rows))

    def test_visible_audience_does_not_bypass_transcript_quality(self):
        from r3x.speakers import VoiceObservation
        self.setup_memory()
        for reason, changes, trusted in (
                ('untrusted_transcript',{},False),('echo_risk',{'echo_risk':True},True),
                ('truncated_capture',{'truncated':True},True),('overlap',{'overlap':True},True)):
            with self.subTest(reason=reason):
                cap=Capture(uuid4().hex,'old',bytes(96000),96,99,100,voiced_intervals=((0.,3.),),**changes)
                self.session('old').process_turn(cap,Transcript('Unusable words.',0,trusted),0,
                    VoiceObservation(NS),[face(100,self.person)])
                self.assertEqual(self.store.rows('SELECT * FROM conversation_exchanges'),[])
                if reason!='echo_risk':  # Echo is rejected before state admission.
                    receipts=[row for row in self.state.trace.rows if row['event']=='conversation_memory_record']
                    self.assertEqual(receipts[-1]['reason'],reason)

    def test_uncertain_memory_episode_keeps_source_evidence_and_forgets_sources(self):
        from r3x.speakers import VoiceObservation
        self.setup_memory()
        self.turn(self.session('old'),'A switch outage.',faces=[face(100,self.person)],voice=VoiceObservation(NS))
        source=cm.recall(self.store,self.person,'new')['exchanges'][0]['id']
        self.begin('Remember our switch conversation.')
        result=self.call('remember_episode',{'source_exchange_id':source,'summary':'We discussed a switch outage.'})
        self.assertEqual(result.outcome,'ok')
        episode=cm.context(self.store,self.state.context(),'new',self.clock)['episodes'][0]
        self.assertEqual(episode['attribution']['speaker']['state'],'unresolved')
        with self.store.transaction() as conn:self.store.forget(conn,self.person)
        self.assertEqual(self.store.rows('SELECT * FROM conversation_exchange_evidence'),[])
        self.assertEqual(self.store.rows('SELECT * FROM conversation_episodes'),[])
        self.assertEqual(self.store.rows('SELECT text FROM turns WHERE id=?',(source,))[0]['text'],'[forgotten]')

    def test_no_audience_or_group_does_not_assign_unknown_words_to_a_person(self):
        from r3x.speakers import VoiceObservation
        self.setup_memory()
        for faces in ([], [face(100,None)], [face(100,self.person),face(100,None,'visitor')]):
            self.turn(self.session('old'),'Unknown speaker.',faces=faces,voice=VoiceObservation(NS))
        self.assertEqual(self.store.rows('SELECT * FROM conversation_exchanges'),[])

    def test_known_different_voice_does_not_join_visible_persons_private_memory(self):
        from tests.test_memory_core import B
        self.setup_memory()
        self.enroll('Jamie',B)
        self.turn(self.session('old'),'My separate story.',faces=[face(100,self.person)],voice=observation(B))
        self.assertEqual(cm.recall(self.store,self.person,'new')['exchanges'],[])

    def test_archive_retains_capture_audience_when_camera_changes_during_reply(self):
        from r3x.speakers import VoiceObservation
        self.setup_memory()
        visible=[face(100,self.person)]
        class Changed:
            def reply(self,snapshot,cancel):
                visible[:]=[face(100,None,'visitor')]
                yield 'I heard the project update.'
        cap=Capture(uuid4().hex,'old',bytes(96000),96,99,100,voiced_intervals=((0.,3.),))
        self.session('old',Changed()).process_turn(cap,Transcript('A private project update.',0,True),0,
            VoiceObservation(NS),tuple(visible),latest_faces=lambda now:tuple(visible))
        row=cm.recall(self.store,self.person,'new')['exchanges'][0]
        self.assertEqual(row['attribution']['visible_people'][0]['identity'],self.person)

    def test_version_one_archive_upgrades_without_changing_original_attribution(self):
        self.setup_memory();self.turn(self.session('old'),'Original supported exchange.')
        with self.store.transaction() as conn:
            conn.execute('DROP TABLE conversation_exchange_evidence')
            conn.execute("UPDATE meta SET value='1' WHERE key='conversation_memory_schema'")
        self.store.close();self.store=Memory(self.path);self.store.start()
        row=cm.recall(self.store,self.person,'new')['exchanges'][0]
        self.assertEqual(row['attribution']['speaker']['person_id'],self.person)
        self.assertEqual(row['attribution']['speaker']['state'],'supported')
        self.assertEqual(self.store.rows("SELECT value FROM meta WHERE key='conversation_memory_schema'")[0]['value'],'2')

    def test_date_range_excludes_other_runs_and_people_and_reports_truncation(self):
        self.setup_memory()
        for i in range(15):
            self.clock=1800000000+i*3600
            self.turn(self.session(str(i)),'Project '+str(i))
        result=cm.recall(self.store,self.person,'new',after=cm.stamp(1800000000),before=cm.stamp(1800000000+15*3600))
        self.assertEqual(result['matching_interval_count'],15)
        self.assertTrue(result['selection_truncated'])
        self.assertEqual(len(result['exchanges']),12)
        self.assertEqual(cm.recall(self.store,self.person,'new',after=cm.stamp(self.clock+1))['exchanges'],[])
        with self.assertRaises(ValueError):cm.recall(self.store,self.person,'new',after='2026-09-30')

    def test_episode_source_retraction_and_forgetting(self):
        self.setup_memory();self.turn(self.session('old'),'We finished a telescope mount.')
        source=self.store.rows('SELECT id FROM conversation_exchanges')[0]['id']
        self.begin('Remember our telescope project.')
        result=self.call('remember_episode',{'source_exchange_id':source,'summary':'We discussed finishing a telescope mount.'})
        self.assertEqual(result.outcome,'ok',result)
        saved=result.data['episode_id']
        self.assertEqual(self.call('retract_episode',{'episode_id':saved}).outcome,'ok')
        self.assertEqual(self.store.rows('SELECT * FROM conversation_episodes'),[])
        self.call('remember_episode',{'source_exchange_id':source,'summary':'Telescope mount discussion.'})
        with self.store.transaction() as conn:self.store.forget(conn,self.person)
        self.assertEqual(self.store.rows('SELECT * FROM conversation_exchanges'),[])
        self.assertEqual(self.store.rows('SELECT * FROM conversation_episodes'),[])

    def test_old_source_archive_is_explicitly_undated_and_not_fabricated_audio(self):
        self.setup_memory();self.begin('Earlier source-only words.')
        result=cm.recall(self.store,self.person,'new')
        self.assertTrue(result['legacy_source_utterances'])
        self.assertEqual(result['exchanges'],[])
        self.assertNotIn('heard_text',result['legacy_source_utterances'][0])

    def test_audience_change_before_speech_prevents_private_recall_delivery(self):
        self.setup_memory(); self.turn(self.session('old'),'A private project.')
        current=[face(100,self.person)]
        class Changed:
            def reply(self,snapshot,cancel):
                current.append(face(100,None,'visitor'))
                yield 'Your private project was a telescope.'
        session=self.session('new',Changed())
        cap=Capture(uuid4().hex,'new',bytes(96000),96,99,100,voiced_intervals=((0.,3.),))
        result=session.process_turn(cap,Transcript('What did we discuss?',0,True),0,observation(),
                                    tuple(current),latest_faces=lambda now:tuple(current))
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(result.completed_text,[])

    def test_face_flicker_or_renumbered_track_never_drops_a_reply(self):
        # Live October 1 (f4e2bf97): "Back up five feet" and a direct question lost their
        # replies with no logged reason while the owner's face briefly went unrecognized.
        for change in ('unrecognized','renumbered','gone'):
            with self.subTest(change=change):
                self.setup_memory(); self.turn(self.session('old'),'A private project.')
                current=[face(100,self.person)]
                person=self.person
                class Flicker:
                    def reply(self,snapshot,cancel):
                        current[:]=([face(100,None)] if change=='unrecognized' else
                                    [face(100,person,'renumbered')] if change=='renumbered' else [])
                        yield 'Your private project was a telescope.'
                session=self.session('new-'+change,Flicker())
                cap=Capture(uuid4().hex,'new-'+change,bytes(96000),96,99,100,voiced_intervals=((0.,3.),))
                result=session.process_turn(cap,Transcript('What did we discuss?',0,True),0,observation(),
                                            tuple(current),latest_faces=lambda now:tuple(current))
                self.assertEqual(result.status,'completed')
                self.assertEqual(result.completed_text,['Your private project was a telescope.'])

    def test_withheld_reply_always_states_its_reason(self):
        self.setup_memory(); self.turn(self.session('old'),'A private project.')
        current=[face(100,self.person)]
        class Changed:
            def reply(self,snapshot,cancel):
                current.append(face(100,None,'visitor'))
                yield 'Your private project was a telescope.'
        session=self.session('new',Changed())
        cap=Capture(uuid4().hex,'new',bytes(96000),96,99,100,voiced_intervals=((0.,3.),))
        session.process_turn(cap,Transcript('What did we discuss?',0,True),0,observation(),
                             tuple(current),latest_faces=lambda now:tuple(current))
        rows=[r for r in self.state.trace.rows if r['event']=='reply_cancelled']
        self.assertEqual(rows[-1]['reason'],'private_memory_audience_changed')

    def test_episode_cannot_point_to_another_person_or_missing_source(self):
        self.setup_memory();self.begin('Remember that conversation.')
        result=self.call('remember_episode',{'source_exchange_id':'missing','summary':'Invented.'})
        self.assertEqual(result.outcome,'rejected')
        self.assertEqual(self.store.rows('SELECT * FROM conversation_episodes'),[])

    def test_another_persons_exchange_cannot_be_read_or_summarized(self):
        self.setup_memory();self.begin('Remember our discussion.')
        with self.store.transaction() as conn:
            other=self.store.add_person(conn,'Other person',[],None)
            conn.execute('INSERT INTO conversation_exchanges VALUES(?,?,?,?,?,?,?,?,?)',
                ('other-exchange','old',other,None,self.clock,'Other private words.','', 'completed','human_turn'))
        self.assertEqual(cm.recall(self.store,self.person,'new')['exchanges'],[])
        result=self.call('remember_episode',{'source_exchange_id':'other-exchange','summary':'Not my conversation.'})
        self.assertEqual(result.reason,'EpisodeSourceUnavailable')

    def test_relative_recall_bounds_are_computed_by_code(self):
        self.setup_memory();self.turn(self.session('old'),'The motor needs testing.')
        self.clock+=7200
        self.begin('What did we discuss two hours ago?')
        result=self.call('recall_conversations',{'query':None,'after':9000,'before':5400})
        self.assertEqual(result.outcome,'ok',result)
        self.assertEqual(result.data['exchanges'][0]['human_text'],'The motor needs testing.')
        reversed_result=self.call('recall_conversations',{'query':None,'after':5400,'before':9000})
        self.assertEqual(reversed_result.data['exchanges'],result.data['exchanges'])
