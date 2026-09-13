"""Atomic model-selected visual correction; no semantic inference in the executor."""
from r3x import visual_memory
from r3x.contracts import Cancellation, ToolCall, Cancelled
from r3x.semantics import grounded_tools, validate
from tests.test_memory_core import MemoryFixture


class VisualCorrectionTests(MemoryFixture):
    def seed(self, key, description='A red drinking mug.', session='session', audience='[]'):
        visual_memory.save(self.store,session,audience,{'request_id':key,'status':'observed','stage':'complete',
            'frame_id':'frame:'+key,'medium':'saved_synthetic_image'},
            {'observation_id':key,'description':description,'uncertainty':'Illustration.'},'scripted',100)

    def correct(self, ids, call_id=None, revision=None):
        return self.call('retract_visual',{'observation_ids':ids,'source_turn_id':self.state.current[0],
                                         'text':'I withdrew the mistaken descriptions.'},call_id,revision)

    def statuses(self):
        return {r['id']:r['status'] for r in self.store.rows('SELECT id,status FROM visual_observations')}

    def test_selected_related_records_withdraw_together_unrelated_remains(self):
        self.seed('red');self.seed('blue','A blue drinking mug.');self.seed('plant','A plant on another shelf.')
        self.begin('Both colors are the same toy mug, not a drinking mug.')
        result=self.correct(['red','blue'])
        self.assertEqual(result.outcome,'ok')
        self.assertEqual(result.data['records'],[{'observation_id':x,'status':'retracted'} for x in ('red','blue')])
        self.assertFalse(result.data['replacement_saved'])
        self.assertEqual(self.statuses(),{'red':'retracted','blue':'retracted','plant':'observed'})
        self.assertFalse(self.store.people());self.assertFalse(self.store.claims())

    def test_one_invalid_id_rejects_entire_selection(self):
        self.seed('red');self.begin('That is a toy mug.')
        self.assertEqual(self.correct(['red','invented']).outcome,'rejected')
        self.assertEqual(self.statuses()['red'],'observed')

    def test_other_audience_or_session_cannot_be_withdrawn(self):
        self.seed('red');self.seed('other',audience='["other"]');self.seed('prior',session='prior')
        self.begin('Correct the mug.')
        for key in ('other','prior'):
            self.assertEqual(self.correct(['red',key]).outcome,'rejected')
        self.assertTrue(all(v=='observed' for v in self.statuses().values()))

    def test_old_records_beyond_recall_window_are_offered_and_correctable(self):
        for i in range(12):self.seed(str(i))
        self.assertEqual(len(visual_memory.context(self.store,'session','[]')),8)
        offered=visual_memory.correction_candidates(self.store,'session','[]')
        self.assertEqual(len(offered),12)
        tool=next(t for t in grounded_tools({'visual_correction_candidates':offered,'source_turn_id':'t'}) if t['name']=='retract_visual')
        self.assertEqual(set(tool['parameters']['properties']['observation_ids']['items']['enum']),{str(i) for i in range(12)})
        self.begin('Every saved drinking mug description is mistaken.')
        self.assertEqual(self.correct([str(i) for i in range(12)]).outcome,'ok')
        self.assertFalse(visual_memory.correction_candidates(self.store,'session','[]'))

    def test_retry_returns_original_results_without_second_mutation(self):
        self.seed('red');self.begin('It is a toy.')
        first=self.correct(['red'],call_id='same');revision=self.store.revision
        again=self.correct(['red'],call_id='same')
        self.assertEqual(first.data,again.data);self.assertEqual(self.store.revision,revision)

    def test_stale_revision_and_untrusted_source_reject(self):
        self.seed('red');self.begin('It is a toy.')
        self.assertEqual(self.correct(['red'],revision=self.store.revision-1).reason,'StaleStateRevision')
        self.begin('It is a toy.',trusted=False)
        self.assertEqual(self.correct(['red']).outcome,'rejected')
        self.assertEqual(self.statuses()['red'],'observed')

    def test_cancelled_write_keeps_all_records(self):
        self.seed('red');self.seed('blue');self.begin('Both are toys.')
        cancel=Cancellation();cancel.event.set()
        with self.assertRaises(Cancelled):
            self.state.execute(ToolCall('cancelled','retract_visual',{'observation_ids':['red','blue'],
                'source_turn_id':self.state.current[0],'text':None}),self.store.revision,cancel)
        self.assertTrue(all(v=='observed' for v in self.statuses().values()))

    def test_empty_duplicate_oversized_and_retired_single_id_schema_reject(self):
        for ids in ([],['red','red'],[str(i) for i in range(33)]):
            with self.assertRaises(ValueError):validate('retract_visual',{'observation_ids':ids,'source_turn_id':'t','text':None})
        with self.assertRaises(ValueError):validate('retract_visual',{'observation_id':'red','source_turn_id':'t','text':None})

    def test_restart_keeps_retractions_and_source_linked_operation(self):
        self.seed('red');self.seed('blue');self.begin('Both are toys.')
        result=self.correct(['red','blue'])
        self.store.close();self.store.start()
        self.assertFalse(visual_memory.context(self.store,'session','[]'))
        row=self.store.rows('SELECT result FROM operations WHERE request_id=?',(result.request_id,))[0]
        self.assertIn(self.state.current[0],row['result'])
        self.assertIn('replacement_saved',row['result'])

    def test_session_acknowledges_batch_after_commit_in_one_generation(self):
        from types import SimpleNamespace
        from r3x.contracts import Capture, Transcript
        from r3x.output import OutputOwner
        from r3x.scenario import SyntheticTTS, SyntheticSink
        from r3x.session import Session
        self.seed('red');self.seed('blue');snapshots=[]
        def reply(snapshot,cancel):
            snapshots.append(snapshot)
            yield ToolCall('batch','retract_visual',{'observation_ids':['red','blue'],
                'source_turn_id':snapshot.context['source_turn_id'],'text':'I withdrew both mistaken descriptions.'})
        tts=SyntheticTTS();original=tts.synthesize
        def synthesize(text,cancel):
            self.assertTrue(all(v=='retracted' for v in self.statuses().values()))
            yield from original(text,cancel)
        tts.synthesize=synthesize
        session=Session('session',SimpleNamespace(reply=reply),OutputOwner(tts,SyntheticSink(),self.state.trace),self.state.trace,self.state)
        self.addCleanup(session.close)
        result=session.process_turn(Capture('c','session',bytes(1024),98,99,99.6),Transcript('Both mugs are toys.',-.01,True),session.revision)
        self.assertEqual(result.status,'completed');self.assertEqual(len(snapshots),1)
        self.assertEqual(result.completed_text,['I withdrew both mistaken descriptions.'])
        self.assertEqual(len(snapshots[0].context['visual_correction_candidates']),2)
        self.assertIn('Entire selected descriptions were withdrawn',str(session.history_messages()))
        self.assertNotIn('A red drinking mug',str(session.history_messages()))
