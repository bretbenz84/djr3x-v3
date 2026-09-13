import json
from r3x.contracts import ToolCall
from r3x import visual_memory
from tests.test_memory_core import MemoryFixture
from tests import test_inspection


class VisualMemoryTests(MemoryFixture):
    setup_session=test_inspection.InspectionTests.setup_session
    turn=test_inspection.InspectionTests.turn

    def setup_description(self,text='A red cup sits on the desk.'):
        self.setup_session()
        original=self.brain.reply
        def reply(snapshot,cancel):
            if snapshot.continuation:
                self.brain.snapshots.append(snapshot)
                yield ToolCall('describe','describe_view',{
                    'observation_id':snapshot.context['inspection']['request_id'],
                    'description':'A person with brown hair sits beside a red cup.',
                    'uncertainty':'Apparent appearance only; identity and age are unresolved.', 'text':text})
            else:yield from original(snapshot,cancel)
        self.brain.reply=reply

    def test_description_and_actual_remarks_are_separate_source_linked_records(self):
        self.setup_description();result=self.turn()
        self.assertEqual(result.status,'completed')
        rows=visual_memory.context(self.store,'session','[]')
        self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(self.store.rows('SELECT source FROM visual_observations')[0]['source'])['frame_id'],'synthetic-frame')
        self.assertEqual(rows[0]['spoken'][0]['text'],'A red cup sits on the desk.')
        self.assertEqual(rows[0]['model'],'scripted-or-unspecified')
        self.assertFalse(self.store.claims());self.assertFalse(self.store.people())
        self.assertEqual(len(self.brain.snapshots),2)
        self.assertNotIn('data:image',str(rows))

    def test_silent_description_does_not_become_a_spoken_remark(self):
        self.setup_description(None);result=self.turn()
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(visual_memory.context(self.store,'session','[]')[0]['spoken'],[])

    def test_cached_context_can_answer_without_a_new_capture(self):
        self.setup_description();self.turn();self.events.clear()
        def reply(snapshot,cancel):
            self.brain.snapshots.append(snapshot)
            yield 'The earlier view showed a red cup.'
        self.brain.reply=reply;self.turn()
        rows=self.brain.snapshots[-1].context['visual_memory']
        self.assertEqual(len(rows),1)
        self.assertIn('brown hair',rows[0]['description'])
        self.assertTrue(rows[0]['spoken'])
        self.assertEqual(self.events,[])

    def test_changed_audience_and_restart_do_not_inherit_person_assumptions(self):
        self.setup_description();self.turn()
        self.assertEqual(visual_memory.context(self.store,'different-session','[]'),[])
        self.assertEqual(visual_memory.context(self.store,'session','["new-person"]'),[])
        self.store.close();self.store.start()
        self.assertEqual(len(self.store.rows('SELECT * FROM visual_observations')),1)
        self.assertEqual(visual_memory.context(self.store,'different-session','[]'),[])

    def test_bad_source_cannot_save_a_description(self):
        self.setup_description();original=self.brain.reply
        def reply(snapshot,cancel):
            for delta in original(snapshot,cancel):
                if isinstance(delta,ToolCall) and delta.name=='describe_view':delta.arguments['observation_id']='invented'
                yield delta
        self.brain.reply=reply
        self.assertEqual(self.turn().status,'failed')
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))

    def test_correction_withdraws_visual_record_without_changing_identity(self):
        self.setup_description();self.turn()
        observation_id=self.store.rows('SELECT id FROM visual_observations')[0]['id']
        def reply(snapshot,cancel):
            yield ToolCall('correct','retract_visual',{'observation_ids':[observation_id],
                'source_turn_id':snapshot.context['source_turn_id'],'text':'I will not rely on that description.'})
        self.brain.reply=reply
        self.assertEqual(self.turn().status,'completed')
        self.assertEqual(visual_memory.context(self.store,'session','[]'),[])
        self.assertEqual(self.store.rows('SELECT status FROM visual_observations')[0]['status'],'retracted')
        self.assertFalse(self.store.people())

    def test_failed_audio_keeps_observation_but_not_spoken_receipt(self):
        from r3x.contracts import ProviderFailure
        self.setup_description()
        def fail(*args):raise ProviderFailure('SyntheticAudioFailure')
        self.output.sink.play=fail
        self.assertEqual(self.turn().status,'failed')
        self.assertEqual(visual_memory.context(self.store,'session','[]')[0]['spoken'],[])

    def test_selected_cached_reply_gets_its_own_delivery_receipt(self):
        self.setup_description();self.turn();self.events.clear()
        def reply(snapshot,cancel):
            yield ToolCall('reuse','use_visual_memory',{'observation_id':snapshot.context['visual_memory'][0]['id'],
                'text':'In that earlier view, the cup was red.'})
        self.brain.reply=reply;self.turn()
        self.assertEqual(len(visual_memory.context(self.store,'session','[]')[0]['spoken']),2)
        self.assertEqual(self.events,[])

    def test_proactive_inspection_can_offer_description_tool(self):
        from tests.test_proactive import event
        from r3x.semantics import grounded_tools
        self.setup_description()
        result=self.session.process_opportunity(event(),0,lambda:True)
        self.assertEqual(result.status,'completed')
        self.assertEqual([t['name'] for t in grounded_tools(self.brain.snapshots[-1].context)],['describe_view'])

    def test_retention_is_bounded_and_forgetting_removes_visual_records(self):
        self.setup_description();self.turn()
        row=self.store.rows('SELECT * FROM visual_observations')[0]
        source=json.loads(row['source'])
        for i in range(35):
            copied={**source,'request_id':'synthetic-'+str(i)}
            visual_memory.save(self.store,'session','[]',copied,{'observation_id':copied['request_id'],
                'description':'Same unchanged brown hair.','uncertainty':'Synthetic interpretation.'},'scripted',i+row['created_at']+1)
        self.assertEqual(len(self.store.rows('SELECT * FROM visual_observations')),32)
        self.assertEqual(len(visual_memory.context(self.store,'session','[]')),8)
        person=self.enroll()
        self.begin('Forget me.')
        self.assertEqual(self.call('forget_person',{'person_id':person}).outcome,'ok')
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))
