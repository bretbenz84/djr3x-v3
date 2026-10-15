"""Cross-session recall through production owners; synthetic identity/audio only."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

from r3x import conversation_memory as cm
from r3x.present import COMPANY_SECONDS, PRIVATE_CARRY_SECONDS
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
                    yield ToolCall('recall-old','recall_conversations',{'query':'telescope','period':None,'after':None,'before':None})
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

    def test_restart_greeting_sees_the_last_runs_heard_greeting(self):
        self.setup_memory()
        first=self.session('first',Brain('Hey Alex, good to see you.'))
        self.assertEqual(first.process_opportunity(replace(event(self.person),session_id='first'),0,lambda:True).status,'completed')
        self.clock += 20*60
        brain=Brain('Back already?')
        again=self.session('again',brain).process_opportunity(replace(event(self.person),session_id='again'),0,lambda:True)
        self.assertEqual(again.status,'completed')
        earlier=brain.snapshots[0].context['personal_memory']['earlier_greetings']
        self.assertEqual([(g['said'],g['ago']) for g in earlier],[('Hey Alex, good to see you.','about 20 minutes ago')])
        # An unheard greeting is not something he said.
        self.assertEqual([g['said'] for g in cm.greetings(self.store,self.person,self.clock)],['Back already?','Hey Alex, good to see you.'])
        self.assertEqual([cm.gap(s) for s in (60,3600,5*3600,3*86400)],
                         ['a minute or two ago','about an hour ago','about 5 hours ago','about 3 days ago'])

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
                    yield ToolCall('earlier','recall_conversations',{'query':'switch firmware','period':None,'after':3600,'before':0})
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
        self.assertFalse(result['selection_truncated'])
        # A requested period reads as a conversation, oldest first.
        self.assertEqual([r['human_text'] for r in result['exchanges']],['Project '+str(i) for i in range(15)])
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

    def recall_brain(self):
        class Recall:
            snapshots=[]
            def reply(self,snapshot,cancel):
                self.snapshots.append(snapshot)
                if snapshot.continuation: yield 'You were going to take the bumper off.'
                else: yield ToolCall('recall-plans','recall_conversations',{'query':'car bumper plan','period':None,'after':None,'before':None})
        return Recall()

    def test_restarted_face_track_is_the_same_visit_for_recall(self):
        # Live October 3 (d4634610): Rex greeted the owner by name, then answered "I don't
        # have a record of you mentioning the car": his own movement had restarted the face
        # track, unrecognized, a second before the lookup (PrivateRecallRequiresKnownAudience).
        from r3x.speakers import VoiceObservation
        self.setup_memory(); self.turn(self.session('morning'),'I will take the bumper off my car today.')
        session=self.session('noon')
        self.turn(session,'Good morning.',faces=[face(100,self.person)],voice=VoiceObservation(NS))
        self.state.trace.at += 30
        brain=session.brain=self.recall_brain()
        unrecognized=[face(130,None,'restarted')]
        cap=Capture(uuid4().hex,'noon',bytes(96000),96,99,100,voiced_intervals=((0.,3.),))
        result=session.process_turn(cap,Transcript('Do you remember what I planned today?',0,True),0,
            VoiceObservation(NS),tuple(unrecognized),latest_faces=lambda now:tuple(unrecognized))
        self.assertEqual(result.completed_text,['You were going to take the bumper off.'])
        first=brain.snapshots[0].context
        self.assertEqual(first['personal_memory']['person_id'],self.person)        # The morning is in view...
        self.assertIn('bumper',first['personal_memory']['recent_exchanges'][0]['human_text'])
        self.assertIn('bumper off my car',str(brain.snapshots[-1].continuation))   # ...and the lookup works.
        self.assertFalse([r for r in self.state.trace.rows if r.get('reason')=='PrivateRecallRequiresKnownAudience'])
        row=self.store.rows("SELECT evidence FROM conversation_exchange_evidence WHERE exchange_id IN "
                            "(SELECT id FROM conversation_exchanges WHERE human_text LIKE 'Do you remember%')")[0]
        self.assertIn('recently_recognized_person',row['evidence'])
        self.assertEqual(self.state.current[4].person_id,None)   # Memory access never names the speaker.

    def test_the_carried_visit_ends_with_time_company_or_somebody_else(self):
        from r3x.speakers import VoiceObservation
        from tests.test_memory_core import B
        self.setup_memory(); self.turn(self.session('morning'),'A private plan.')
        other=self.enroll('Jamie',B)
        session=self.session('noon')
        def seen(faces, voice=None):
            self.turn(session,'Okay.',faces=faces,voice=voice or VoiceObservation(NS))
            return cm.subject(self.state.context())
        def stranger():
            self.state.faces=[face(100,None,'unknown')]
            self.state.begin(uuid4().hex,Capture(uuid4().hex,'noon',bytes(96000),96,99,100,voiced_intervals=((0.,3.),)),
                             Transcript('Hello.',0,True),VoiceObservation(NS),tuple(self.state.faces))
            return cm.subject(self.state.context(self.state.faces))
        self.assertEqual(seen([face(100,self.person)]),self.person)
        self.assertEqual(stranger(),self.person)                 # A fresh, not yet recognized track.
        self.state.trace.at += PRIVATE_CARRY_SECONDS + 1
        self.assertIsNone(stranger())                            # Too long unconfirmed.
        self.assertEqual(seen([face(100,self.person)]),self.person)
        self.assertIsNone(seen([face(100,self.person),face(100,None,'visitor')]))
        self.assertIsNone(stranger())                            # Company ended the carry.
        self.assertEqual(seen([face(100,self.person)]),self.person)
        self.assertEqual(seen([face(100,other)]),other)          # Somebody else, recognized.
        self.state.faces=[]
        self.assertEqual(self.state.present.audience_seen[0],other)

    def test_with_company_an_unrecognized_face_never_inherits_the_visit(self):
        # October 3 (be8c8d55): a guest sat beside the owner; alone in view for a moment, the
        # guest was treated as the owner ("Nice to meet you too, Brett") with his memory in reach.
        from r3x.speakers import VoiceObservation
        self.setup_memory(); self.turn(self.session('morning'),'A private plan.')
        session=self.session('afternoon')
        def seen(faces):
            self.turn(session,'Okay.',faces=faces,voice=VoiceObservation(NS))
            return cm.subject(self.state.context())
        def unrecognized():
            self.state.faces=[face(100,None,'guest')]
            self.state.begin(uuid4().hex,Capture(uuid4().hex,'afternoon',bytes(96000),96,99,100,voiced_intervals=((0.,3.),)),
                             Transcript('Nice to meet you.',0,True),VoiceObservation(NS),tuple(self.state.faces))
            context=self.state.context(self.state.faces)
            return cm.subject(context), cm.context(self.store,context,'afternoon',self.clock)['recent_exchanges']
        self.assertIsNone(seen([face(100,self.person),face(100,None,'guest')]))     # Both in view.
        self.assertEqual(seen([face(100,self.person)]),self.person)                 # The owner alone: recognized.
        self.assertEqual(unrecognized(),(None,[]))                                  # The guest alone: nobody's memory.
        self.state.trace.at += COMPANY_SECONDS + 1
        self.assertEqual(seen([face(100,self.person)]),self.person)
        self.assertEqual(unrecognized()[0],self.person)                             # Long alone again: the carry returns.

    def test_face_analysis_never_freezes_a_reply_holding_private_memory(self):
        # October 12 (566c9154): with the owner recognized and his memory in reach, Rex chose
        # silence; saving that checked the faces in view inside the memory transaction while
        # the face worker held the camera lock waiting for memory. Both waited forever, then
        # the microphone loop and shutdown waited on the conversation lock.
        import threading, time
        from pathlib import Path
        from unittest.mock import Mock
        from r3x.adapters.camera import Camera
        from r3x.vision import Frame
        self.setup_memory(); self.turn(self.session('old'),'A private plan.')
        now = self.state.trace.at
        current = [face(now,self.person)]
        camera = Camera('fixture',Path('/unused'))
        camera.encoder = Mock(analyze=Mock(return_value=[]))
        camera.tracker.history.append((now,tuple(current)))
        camera.hub.take = Mock(side_effect=lambda *_: Frame(uuid4().hex,'fixture','new',time.monotonic(),2,2,bytes(12)))
        reading = threading.Event()
        def profiles():
            reading.set()                      # Inside the worker, about to read memory.
            try: return self.store.face_profiles('fixture')
            finally: camera.stop.set()         # One frame is enough.
        camera.profiles = profiles
        analyzer = threading.Thread(target=camera._analyze,daemon=True)
        def latest(at):
            if self.store.lock._is_owned() and not analyzer.is_alive() and not camera.stop.is_set():
                analyzer.start()               # The worker's next frame lands mid-transaction.
                reading.wait(2)
            return camera.latest(at)
        silent = Brain(ToolCall('quiet','remain_silent',dict(reason='no_response_needed',
            invitation_update=None,intent_update=None,next_opportunity=None)))
        session = self.session('new',silent)
        cap = Capture(uuid4().hex,'new',bytes(96000),96,99,100,voiced_intervals=((0.,3.),))
        turn = threading.Thread(target=lambda: session.process_turn(cap,Transcript('Talking.',0,True),0,
            observation(),tuple(current),latest_faces=latest),daemon=True)
        turn.start(); turn.join(5); analyzer.join(5)
        self.assertTrue(analyzer.ident, 'the face worker never ran inside the memory transaction')
        if turn.is_alive():
            self.store = Mock()   # The frozen reply keeps the memory lock; closing would hang too.
            self.fail('reply froze: memory and camera locks taken in opposite orders')
        self.assertFalse(analyzer.is_alive())
        self.assertTrue(silent.snapshots[0].context['personal_memory'].get('recent_exchanges'))
        self.assertTrue([r for r in self.state.trace.rows if r.get('event')=='tool_result'
                         and r.get('name')=='remain_silent' and r.get('outcome')=='ok'])

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
        result=self.call('recall_conversations',{'query':None,'period':None,'after':9000,'before':5400})
        self.assertEqual(result.outcome,'ok',result)
        self.assertEqual(result.data['exchanges'][0]['human_text'],'The motor needs testing.')
        reversed_result=self.call('recall_conversations',{'query':None,'period':None,'after':5400,'before':9000})
        self.assertEqual(reversed_result.data['exchanges'],result.data['exchanges'])

    def test_last_night_is_found_on_the_first_lookup(self):
        """October 11: "Do you remember what I told you last night?" searched the words
        "last night", returned Rex's "tonight" questions and today's chat, missed the 7 PM
        drugs remark, and the undated archive's caveat became the stated reason."""
        self.setup_memory();self.begin('Earlier source-only words.')
        local=lambda day,hour,minute=0:datetime(2026,10,day,hour,minute).timestamp()
        rows=[('rex-tonight',local(10,19,2),'','Brett, what are you up to tonight?','reconsideration'),
              ('drugs',local(10,19,3),"I think I'm gonna do some drugs.",'What do you mean by drugs?','human_turn')]
        rows+=[(f'filler-{i}',local(10,19,4)+i,f'Rex, small talk number {i}.','Sure.','human_turn') for i in range(40)]
        rows+=[('astronomy',local(11,14,55),'','Brett, is tonight an astronomy night or an indoors night?','reconsideration'),
               ('indoors',local(11,14,56),'Indoors.','I will save the astronomy for a night with fewer walls.','human_turn')]
        with self.store.transaction() as conn:
            for key,at,human,heard,origin in rows:
                conn.execute('INSERT INTO conversation_exchanges VALUES(?,?,?,?,?,?,?,?,?)',
                             (key,'yesterday' if key!='indoors' else 'today',self.person,None,at,human,heard,'completed',origin))
        self.clock=local(11,18,2)
        self.begin('Do you remember what I told you last night?')
        result=self.call('recall_conversations',{'query':None,'period':'last_night','after':None,'before':None})
        self.assertEqual(result.outcome,'ok',result)
        found=result.data['exchanges']
        ids=[r['id'] for r in found]
        self.assertIn('drugs',ids)
        self.assertNotIn('indoors',ids)
        self.assertNotIn('legacy_source_utterances',result.data)
        self.assertEqual(len(found),42)
        self.assertFalse(result.data['selection_truncated'])
        self.assertEqual(ids,sorted(ids,key=lambda i:[r[1] for r in rows if r[0]==i][0]))
        drugs=found[ids.index('drugs')]
        self.assertEqual(drugs['local_time'],'Sat Oct 10, 7:03 PM (yesterday, evening)')
        self.assertNotIn('direction',str(drugs['attribution']))
        # Time words mistakenly left in query: whole words only, and the dated record stays primary.
        loose=cm.recall(self.store,self.person,'new',query='last night',now=self.clock)
        self.assertEqual({r['id'] for r in loose['exchanges'][:2]},{'indoors','astronomy'})
        self.assertNotEqual(loose['exchanges'][2]['id'],'rex-tonight','"night" is not "tonight"')
        self.assertNotIn('legacy_source_utterances',loose)
        self.assertTrue(all('local_time' in r for r in loose['exchanges']))
        self.assertEqual(cm.recall(self.store,self.person,'new',query='drugs',now=self.clock)['exchanges'][0]['id'],'drugs')
        self.assertIn('legacy_source_utterances',cm.recall(self.store,self.person,'new',query='bicycle',now=self.clock))
        conflict=self.call('recall_conversations',{'query':None,'period':'last_night','after':3600,'before':None})
        self.assertEqual(conflict.outcome,'rejected')
        self.assertEqual(conflict.reason,'RecallPeriodConflictsWithBounds')

    def test_periods_follow_the_local_clock(self):
        local=lambda day,hour:datetime(2026,10,day,hour).timestamp()
        self.assertEqual(cm.period_window('last_night',local(11,18)),(local(10,17),local(11,5)))
        self.assertEqual(cm.period_window('last_night',local(11,2)),(local(9,17),local(10,5)))
        self.assertEqual(cm.period_window('yesterday',local(11,18)),(local(10,0),local(11,0)))
        self.assertEqual(cm.period_window('this_morning',local(11,9)),(local(11,5),local(11,9)))
        self.assertEqual(cm.period_window('earlier_today',local(11,18)),(local(11,0),local(11,18)))
