"""Fresh proactive images share the production description and delivery path."""
from dataclasses import replace
from types import SimpleNamespace
from r3x.contracts import ToolCall, ProviderFailure
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.session import Session
from r3x.semantics import grounded_tools
from r3x.scene import encode_frame
from tests.test_memory_core import MemoryFixture
from tests.test_observations import frame
from tests.test_proactive import event


class ProactiveVisualMemoryTests(MemoryFixture):
    def setup_scene(self, text='That red object looks interesting.'):
        self.valid = True
        self.state.scene_observer = SimpleNamespace(valid=lambda _:self.valid)
        self.snapshots = []
        def reply(snapshot,cancel):
            self.snapshots.append(snapshot)
            yield ToolCall('describe','describe_view',{
                'observation_id':snapshot.context.get('scene_description',{}).get('request_id','unoffered'),
                'description':'A red object is visible.', 'uncertainty':'Its function is unclear.', 'text':text})
        self.brain = SimpleNamespace(reply=reply,close=lambda:None)
        self.output = OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace)
        self.session = Session('session',self.brain,self.output,self.state.trace,self.state)
        self.addCleanup(self.session.close)
        image,_ = encode_frame(frame())
        self.opportunity = replace(event(),kind='ambient',target_track=None,scene_id='scene:test',scene_images=(image,))

    def run_scene(self):
        return self.session.process_opportunity(self.opportunity,self.session.revision,lambda:True)

    def test_primary_image_call_can_save_and_speak_with_both_receipts(self):
        self.setup_scene()
        self.assertEqual(self.run_scene().status,'completed')
        self.assertEqual(len(self.snapshots),1)
        tools={t['name']:t for t in grounded_tools(self.snapshots[0].context)}
        self.assertTrue({'describe_view','remain_silent','use_observation'}<=tools.keys())
        rows=self.store.rows('SELECT * FROM visual_observations')
        self.assertEqual(len(rows),1)
        self.assertIn('proactive_snapshot',rows[0]['source'])
        self.assertIn('That red object',rows[0]['spoken'])
        self.assertEqual(len(self.store.rows('SELECT * FROM observation_delivery')),1)
        self.assertFalse(self.store.claims());self.assertFalse(self.store.people())
        self.assertNotIn('data:image',str(rows)+str(self.state.trace.rows)+str(self.session.history_messages()))

    def test_silent_retention_is_not_delivery_or_long_silence_policy(self):
        self.setup_scene(None)
        self.assertEqual(self.run_scene().status,'suppressed')
        self.assertEqual(self.store.rows('SELECT spoken FROM visual_observations')[0]['spoken'],'[]')
        self.assertFalse(self.store.rows('SELECT * FROM observation_delivery'))
        self.assertEqual(self.session.proactive_hold_until,float('-inf'))

    def test_changed_scene_during_generation_rejects_save(self):
        self.setup_scene();original=self.brain.reply
        def reply(s,c):
            self.valid=False
            yield from original(s,c)
        self.brain.reply=reply
        self.assertEqual(self.run_scene().status,'failed')
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))

    def test_changed_scene_during_synthesis_keeps_observation_but_no_delivery(self):
        self.setup_scene()
        def synthesize(text,cancel):
            self.valid=False
            yield bytes(64)
        self.output.tts.synthesize=synthesize
        self.assertEqual(self.run_scene().status,'interrupted')
        self.assertEqual(self.store.rows('SELECT spoken FROM visual_observations')[0]['spoken'],'[]')
        self.assertFalse(self.store.rows('SELECT * FROM observation_delivery'))

    def test_missing_or_stale_images_offer_no_description_and_cannot_save(self):
        self.setup_scene()
        image=self.opportunity.scene_images[0]
        self.opportunity=replace(self.opportunity,scene_images=(replace(image,at=90),))
        self.assertEqual(self.run_scene().status,'failed')
        self.assertNotIn('describe_view',[t['name'] for t in grounded_tools(self.snapshots[0].context)])
        self.assertFalse(self.snapshots[0].images)
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))

    def test_superseding_state_during_generation_cannot_save(self):
        self.setup_scene();original=self.brain.reply
        def reply(s,c):
            with self.store.transaction() as conn:self.store.advance(conn)
            yield from original(s,c)
        self.brain.reply=reply
        self.assertEqual(self.run_scene().status,'failed')
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))

    def test_failed_audio_does_not_claim_a_remark(self):
        self.setup_scene()
        def fail(*args):raise ProviderFailure('SyntheticFailure')
        self.output.sink.play=fail
        self.assertEqual(self.run_scene().status,'failed')
        self.assertEqual(self.store.rows('SELECT spoken FROM visual_observations')[0]['spoken'],'[]')

    def test_cached_context_available_without_motion_or_inspection_adapter(self):
        self.setup_scene(None);self.run_scene()
        self.run_scene()
        self.assertEqual(len(self.snapshots[-1].context['visual_memory']),1)

    def test_two_frame_description_cannot_be_reused_as_single_image_match(self):
        import json
        self.setup_scene(None)
        image,_=encode_frame(frame(at=99,red=True))
        self.opportunity=replace(self.opportunity,scene_images=(image,*self.opportunity.scene_images))
        self.assertEqual(self.run_scene().status,'suppressed')
        source=json.loads(self.store.rows('SELECT source FROM visual_observations')[0]['source'])
        self.assertEqual(len(source['frames']),2)
        self.assertIsNone(source['image_digest'])

    def test_unoffered_source_id_rejected(self):
        self.setup_scene();original=self.brain.reply
        def reply(s,c):
            for call in original(s,c):
                call.arguments['observation_id']='invented'
                yield call
        self.brain.reply=reply
        self.assertEqual(self.run_scene().status,'failed')
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))

    def test_interruption_during_generation_prevents_save(self):
        self.setup_scene();original=self.brain.reply
        def reply(s,c):
            self.session.cancel()
            yield from original(s,c)
        self.brain.reply=reply
        self.assertEqual(self.run_scene().status,'interrupted')
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))

    def test_expired_acquisition_rejects_save_even_when_provider_still_valid(self):
        self.setup_scene();original=self.brain.reply
        def reply(s,c):
            self.state.trace.at+=16
            yield from original(s,c)
        self.brain.reply=reply
        self.assertEqual(self.run_scene().status,'failed')
        self.assertFalse(self.store.rows('SELECT * FROM visual_observations'))

    def test_hosted_transport_distinguishes_proactive_images_from_a_new_human_request(self):
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Snapshot, Cancellation
        from tests.test_adapters import Client, Response
        self.setup_scene()
        brain=OpenAIBrain('fixture')
        response=lambda:Client(Response([{'type':'response.output_text.delta','delta':'Hello.'},{'type':'response.completed'}]))
        messages=({'role':'user','content':'Please give us a moment.'},)
        brain.client=response()
        snap=Snapshot('session','turn',0,messages,context={'trigger':'ambient'},images=self.opportunity.scene_images)
        list(brain.reply(snap,Cancellation()))
        payload=brain.client.calls[0][1]['json']
        self.assertEqual(payload['input'][-2]['content'][-1]['type'],'input_image')
        self.assertEqual(payload['input'][-1]['role'],'developer')
        self.assertEqual(snap.messages,messages)
        brain.client=response()
        list(brain.reply(replace(snap,context={'inspection_description_allowed':True,'inspection':{'question':'What is here?'}}),Cancellation()))
        self.assertEqual(brain.client.calls[0][1]['json']['input'][-1]['content'][-1]['type'],'input_image')
