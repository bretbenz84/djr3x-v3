import json
from r3x import visual_memory
from r3x.contracts import Cancellation, ToolCall
from r3x.semantics import validate,grounded_tools
from tests import test_visual_correction as correction_tests
from tests.test_memory_core import MemoryFixture


class VisualRevisionTests(MemoryFixture):
    seed=correction_tests.VisualCorrectionTests.seed

    def amend(self, ids=('red','blue'), call_id=None, revision=None):
        return self.call('revise_visual',{'revisions':[{'observation_id':key,
            'description':'A toy mug beside a brown-haired figure in a blue shirt.',
            'uncertainty':'Mug purpose corrected by user; remaining appearance is an earlier model interpretation.'} for key in ids],
            'source_turn_id':self.state.current[0],'text':'I corrected the mug descriptions and kept the other details.'},call_id,revision)

    def test_revisions_preserve_other_details_original_source_and_audible_history(self):
        self.seed('red','A drinking cup beside a brown-haired figure in a blue shirt.');self.seed('blue');self.seed('plant','A green plant.')
        visual_memory.delivered(self.store,'red','heard','The cup is red.',101)
        self.begin('The puppet mug is a toy. Keep the other details.')
        result=self.amend();self.assertEqual(result.outcome,'ok')
        raw=self.store.rows("SELECT * FROM visual_observations WHERE id='red'")[0]
        self.assertIn('brown-haired',raw['description']);self.assertEqual(raw['status'],'observed')
        source=json.loads(raw['source']);self.assertEqual(source['frame_id'],'frame:red')
        self.assertEqual(source['interpretation_revision'],1)
        self.assertIn('drinking cup',source['corrections'][0]['previous_description'])
        self.assertEqual(source['corrections'][0]['source_turn_id'],self.state.current[0])
        self.assertEqual(json.loads(raw['spoken'])[0]['text'],'The cup is red.')
        context=visual_memory.context(self.store,'session','[]')
        self.assertNotIn('previous_description',str(context))
        self.assertEqual(next(r for r in context if r['id']=='plant')['description'],'A green plant.')
        self.assertFalse(self.store.people());self.assertFalse(self.store.claims())

    def test_atomic_invalid_scope_stale_revision_and_untrusted_source(self):
        self.seed('red');self.seed('blue',session='elsewhere');self.begin('Correct both.')
        self.assertEqual(self.amend().outcome,'rejected')
        self.assertEqual(self.amend(('red',),revision=self.store.revision-1).outcome,'rejected')
        self.begin('Correct it.',trusted=False)
        self.assertEqual(self.amend(('red',)).outcome,'rejected')
        self.assertEqual(self.store.rows("SELECT description FROM visual_observations WHERE id='red'")[0]['description'],'A red drinking mug.')

    def test_retry_and_restart_keep_one_revision(self):
        self.seed('red');self.begin('Correct it.')
        result=self.amend(('red',),'same');again=self.amend(('red',),'same')
        self.assertEqual(result.data,again.data)
        self.store.close();self.store.start()
        self.assertEqual(visual_memory.context(self.store,'session','[]')[0]['source']['interpretation_revision'],1)

    def test_nested_schema_rejects_missing_extra_duplicate_and_empty_edits(self):
        good={'observation_id':'x','description':'Toy.','uncertainty':'User correction.'}
        for entries in ([],[good,good],[{'observation_id':'x'}],[{**good,'extra':'x'}],[{**good,'description':''}]):
            with self.assertRaises(ValueError):validate('revise_visual',{'revisions':entries,'source_turn_id':'t','text':None})
        tool=next(t for t in grounded_tools({'source_turn_id':'t','visual_correction_candidates':[{'id':'x'}]}) if t['name']=='revise_visual')
        properties=tool['parameters']['properties']['revisions']['items']['properties']
        self.assertEqual(properties['observation_id']['enum'],['x'])
        self.assertNotIn('enum',properties['description']);self.assertNotIn('enum',properties['uncertainty'])

    def test_correction_audit_is_bounded_while_version_increases(self):
        self.seed('red')
        for _ in range(10):self.begin('Correct it.');self.amend(('red',))
        source=json.loads(self.store.rows('SELECT source FROM visual_observations')[0]['source'])
        self.assertEqual(source['interpretation_revision'],10);self.assertEqual(len(source['corrections']),8)

    def test_novelty_separates_exact_image_match_and_heard_version(self):
        from hashlib import sha256
        from r3x.scene import SceneImage
        image=SceneImage('frame',100,'data:image/png;base64,synthetic')
        source={'request_id':'image','status':'observed','stage':'complete','frame_id':'frame',
                'image_digest':sha256(image.data_url.encode()).hexdigest()}
        visual_memory.save(self.store,'session','[]',source,{'observation_id':'image','description':'Cup and brown hair.', 'uncertainty':'Synthetic.'},'test',100)
        before=visual_memory.novelty(visual_memory.context(self.store,'session','[]'),(image,))
        self.assertEqual(before['observations'][0]['image_relation'],'identical_supplied_image')
        self.assertFalse(before['observations'][0]['has_heard_remark'])
        visual_memory.delivered(self.store,'image','heard','A cup.',101)
        self.begin('It is a toy.');self.amend(('image',))
        after=visual_memory.novelty(visual_memory.context(self.store,'session','[]'),(image,))
        self.assertEqual(after['observations'][0]['interpretation_revision'],1)
        self.assertEqual(after['observations'][0]['heard_remarks'][0]['interpretation_revision'],0)

    def test_full_bounded_batch_has_atomic_storage_and_transport_budget(self):
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Snapshot
        from tests.test_adapters import Client,Response
        for i in range(32):self.seed(str(i))
        self.begin('Correct all toy mugs, keeping other details.')
        candidates=visual_memory.correction_candidates(self.store,'session','[]')
        brain=OpenAIBrain('synthetic');brain.client=Client(Response([{'type':'response.output_text.delta','delta':'Hello.'},{'type':'response.completed'}]))
        snap=Snapshot('session','turn',0,(),context={'source_turn_id':'turn','visual_correction_candidates':candidates},tools_enabled=True)
        list(brain.reply(snap,Cancellation()))
        self.assertEqual(brain.client.calls[0][1]['json']['max_output_tokens'],16896)
        self.assertEqual(self.amend(tuple(str(i) for i in range(32))).outcome,'ok')
        self.assertTrue(all('toy mug' in r['description'] for r in self.store.rows('SELECT description FROM visual_observations')))
