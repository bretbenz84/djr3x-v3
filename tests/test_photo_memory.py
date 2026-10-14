"""Synthetic end-to-end photographic teaching; network and devices blocked."""
import base64
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from r3x.photo_memory import PhotoMemory
from r3x.scene import SceneImage,encode_frame
from r3x.vision import Frame
from r3x.contracts import ToolCall,Transcript,Cancellation
from r3x.session import Session
from r3x.output import TextOutput
from r3x.semantics import grounded_tools
from tests.test_memory_core import MemoryFixture,observation,C


def image():return encode_frame(Frame('test-frame','fake','session',100.,4,4,bytes([20,80,100])*16))[0]


class PhotoTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.album=PhotoMemory(Path(self.temp.name).resolve()/'photos');self.album.start()
        self.state.photo_memory=self.album
        self.state.objects=SimpleNamespace(snapshot=lambda:({'target_id':'object-1','label':'cat','score':.7,'box':[0,0,.5,.5]},),
            inspect=lambda target:(image(),{'target_id':'object-1','label':'cat','box':[0,0,.5,.5]},image()))

    def session(self,brain,text):
        self.begin(text,C)
        session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        receipt=session.process_turn(self.state.current[1],Transcript(text,0,True),0,observation(C))
        return session,receipt

    def teach(self):
        requests=[]
        class Brain:
            def reply(brain,snapshot,cancel):
                requests.append(snapshot)
                tools={d['name'] for d in grounded_tools(snapshot.context)}
                if not snapshot.continuation:
                    assert 'inspect_visual_memory' in tools
                    yield ToolCall('look','inspect_visual_memory',{'intent':'look','target_id':'object-1','entity_ids':[],'question':'Remember this dog as Max.'})
                else:
                    assert tools=={'remember_object'}
                    yield ToolCall('save','remember_object',{'evidence_id':snapshot.context['photo_inspection']['evidence_id'],
                        'entity_id':None,'name':'Max','kind':'pet','notes':'Small dark dog; detector cat label uncertain.',
                        'source_turn_id':snapshot.context['source_turn_id'],'text':'I will remember Max.'})
        session,receipt=self.session(Brain(),'This is Max.')
        self.assertEqual(receipt.status,'completed')
        self.assertEqual(receipt.completed_text,['I will remember Max.'])
        self.assertEqual(len(requests),2)
        self.assertEqual(len(requests[1].images),2)
        self.assertIsNone(self.state.current[4].person_id)
        self.assertFalse(self.store.claims())
        self.assertEqual(len(self.album.catalog()),1)
        session.close()
        return self.album.catalog()[0]['entity_id']

    def test_teaching_excludes_old_reference_notes_and_history(self):
        key=self.teach()
        class Brain:
            def reply(brain,snapshot,cancel):
                if not snapshot.continuation:
                    yield ToolCall('teach','inspect_visual_memory',{'intent':'teach','target_id':None,
                        'entity_ids':[key],'question':'Remember the old dark dog appearance.'})
                else:
                    assert len(snapshot.images)==2
                    assert all(x.get('role','').startswith('current') for x in snapshot.context['photo_inspection']['images'])
                    assert 'notes' not in snapshot.context['photo_memory']['entities'][0]
                    assert snapshot.messages==({'role':'user','content':'This different object is dog toy. Remember it.'},)
                    assert snapshot.context['photo_inspection']['question']=='This different object is dog toy. Remember it.'
                    yield ToolCall('save-toy','remember_object',{'evidence_id':snapshot.context['photo_inspection']['evidence_id'],
                        'entity_id':None,'name':'dog toy','kind':'object','notes':'Blue round toy.',
                        'source_turn_id':snapshot.context['source_turn_id'],'text':'Saved dog toy.'})
        utterance='This different object is dog toy. Remember it.'
        self.begin(utterance,C)
        session=Session('session',Brain(),TextOutput(self.state.trace),self.state.trace,self.state)
        session.history.append({'role':'assistant','content':'The previous object is a small yellow bone-shaped toy.'})
        receipt=session.process_turn(self.state.current[1],Transcript(utterance,0,True),0,observation(C));session.close()
        self.assertEqual(receipt.completed_text,['Saved dog toy.'])
        restarted=PhotoMemory(self.album.root);restarted.start()
        self.assertEqual(restarted.catalog()[-1]['notes'],'Blue round toy.')
        self.assertEqual(restarted.catalog()[0]['entity_id'],key)

    def test_a_saved_pets_name_is_never_introduced_as_a_person(self):
        # October 3 (be8c8d55): "This is Max" about the dog on the couch created a person
        # named Max beside the saved pet.
        self.teach()
        class Brain:
            def __init__(brain):brain.requests=[]
            def reply(brain,snapshot,cancel):
                brain.requests.append(snapshot)
                if snapshot.continuation:yield 'Max the dog, of course.'
                else:yield ToolCall('intro','introduce_person',{'name':'Max','aliases':[],'subject':'other','person_id':None,'face_track_id':None})
        brain=Brain()
        session,receipt=self.session(brain,'This is Max.')
        self.assertEqual(receipt.completed_text,['Max the dog, of course.'])
        self.assertIn('NameBelongsToSavedPet',str(brain.requests[-1].continuation))
        self.assertFalse(self.store.resolve('Max'))
        session.close()
        # A person who happens to share no saved pet's name is still introduced.
        brain=Brain()
        brain.reply=lambda snapshot,cancel:iter(['Hello, Jamie.'] if snapshot.continuation else
            [ToolCall('intro','introduce_person',{'name':'Jamie','aliases':[],'subject':'other','person_id':None,'face_track_id':None})])
        session,receipt=self.session(brain,'This is Jamie.')
        self.assertEqual(len(self.store.resolve('Jamie')),1)
        session.close()

    def test_ordinary_inspection_compares_restart_gallery_and_saves_description(self):
        from copy import deepcopy
        from r3x.inspection import CurrentView
        from r3x.output import OutputOwner
        from r3x.scenario import SyntheticTTS,SyntheticSink
        key=self.teach()
        data=deepcopy(self.album.data)
        for index in range(3):
            data['entities']['other-'+str(index)]=deepcopy(data['entities'][key])
            data['entities']['other-'+str(index)]['name']='Object '+str(index)
        self.album._commit(data)
        self.album.close();self.album=PhotoMemory(self.album.root);self.album.start();self.state.photo_memory=self.album
        before=deepcopy(self.album.data)
        requests=[]
        class Brain:
            def reply(brain,snapshot,cancel):
                requests.append(snapshot)
                if not snapshot.continuation:
                    yield ToolCall('inspect','inspect_view',{'direction':'current','question':'What is in my hand?'})
                else:
                    photo=snapshot.context['photo_inspection']
                    assert {t['name'] for t in grounded_tools(snapshot.context)}=={'describe_view'}
                    assert len(snapshot.images)==5
                    assert photo['reference_coverage']=='complete'
                    assert {x['entity_id'] for x in photo['images'][1:]}==set(before['entities'])
                    yield ToolCall('description','describe_view',{'observation_id':snapshot.context['inspection']['request_id'],
                        'description':'A toy matching a saved reference.','uncertainty':'Synthetic comparison.',
                        'text':'That matches the saved toy.'})
        view=CurrentView(SimpleNamespace(snapshot=lambda *args:('scene',(image(),))),clock=lambda:100.)
        view.start();self.addCleanup(view.close)
        self.begin('What am I holding?',C)
        session=Session('session',Brain(),OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace,inspection=view),self.state.trace,self.state)
        receipt=session.process_turn(self.state.current[1],Transcript('What am I holding?',0,True),0,observation(C))
        session.close()
        self.assertEqual(receipt.completed_text,['That matches the saved toy.'])
        self.assertEqual(self.album.data,before)
        self.assertEqual(len(self.store.rows('SELECT * FROM visual_observations')),1)
        self.assertEqual(len(requests),2)

    def test_automatic_comparison_bounds_and_reference_failure_keep_current_view(self):
        from copy import deepcopy
        from r3x.photo_flow import comparison
        key=self.teach()
        entity=self.album.data['entities'][key]
        for index in range(5):self.album.data['entities'][str(index)]=deepcopy(entity)
        ctx,images=comparison(self.state,(image(),image()))
        self.assertEqual(len(images),8)
        self.assertEqual(ctx['photo_inspection']['reference_coverage'],'complete')
        self.album.data['entities']['sixth']=deepcopy(entity)
        ctx,images=comparison(self.state,(image(),))
        self.assertEqual(len(images),1)
        self.assertEqual(ctx['photo_inspection']['reference_coverage'],'unavailable')
        self.album.data['entities']={key:entity}
        (self.album.root/entity['references'][0]['file']).write_bytes(b'changed')
        ctx,images=comparison(self.state,(image(),))
        self.assertEqual(len(images),1)
        self.assertEqual(ctx['photo_inspection']['reference_error'],'PhotoReferenceChanged')

    def test_teach_restart_compare_correct_and_forget_visible_files(self):
        key=self.teach()
        files=list(self.album.root.glob('*/*.png'));self.assertEqual(len(files),1)
        self.assertEqual(files[0].read_bytes(),base64.b64decode(image().data_url.split(',')[1]))
        self.album.close();self.album=PhotoMemory(self.album.root);self.album.start();self.state.photo_memory=self.album
        requests=[]
        class Compare:
            def reply(brain,snapshot,cancel):
                requests.append(snapshot)
                if not snapshot.continuation:
                    yield ToolCall('compare','inspect_visual_memory',{'intent':'look','target_id':None,'entity_ids':[key],'question':'Is this Max?'})
                else:
                    assert len(snapshot.images)==3
                    assert snapshot.context['photo_inspection']['images'][2]['name']=='Max'
                    yield 'It looks like Max.'
        s,r=self.session(Compare(),'Who is that dog?');self.assertEqual(r.completed_text,['It looks like Max.']);s.close()
        for action,name,text in [('correct','Maxx','Max is spelled Maxx.'),('forget','Maxx','Forget Maxx.')]:
            class Edit:
                def reply(brain,snapshot,cancel):
                    yield ToolCall(action,'edit_object_memory',{'entity_id':key,'action':action,'name':name,'notes':None,'remove_notes':None,
                        'source_turn_id':snapshot.context['source_turn_id'],'text':'Done.'})
            s,r=self.session(Edit(),text);self.assertEqual(r.completed_text,['Done.']);s.close()
            if action=='correct':self.assertEqual(self.album.catalog()[0]['name'],'Maxx')
        self.assertFalse(self.album.catalog());self.assertFalse(list(self.album.root.glob('*/*.png')))
        restarted=PhotoMemory(self.album.root);restarted.start();self.assertFalse(restarted.catalog())

    def test_details_preserved_and_other_dog_fragment_rejected_across_restart(self):
        key=self.teach()
        # Replay writes through the same state and delivery path as the live run.
        def edit(utterance,notes,remove=None):
            class Edit:
                def reply(brain,snapshot,cancel):
                    if snapshot.continuation:
                        yield 'Which named dog does that description belong to?'
                    else:
                        yield ToolCall(snapshot.turn_id,'edit_object_memory',{
                            'entity_id':key,'action':'correct','name':'Max','notes':notes,
                            'remove_notes':remove,'source_turn_id':snapshot.context['source_turn_id'],'text':'Saved the detail.'})
            session,receipt=self.session(Edit(),utterance);session.close();return receipt
        edit('Max is a terrier.','Terrier.')
        edit('Max has white and black fur.','White and black fur.')
        expected=self.album.catalog()[0]['notes']
        self.assertIn('Terrier.',expected)
        self.assertIn('White and black fur.',expected)
        for text in ('Toby has dark black fur that.','Has gray with brown paws.','Maxx has brown paws.'):
            receipt=edit(text,'Gray with brown paws.')
            self.assertNotIn('Saved the detail.',receipt.completed_text)
            self.assertEqual(self.album.catalog()[0]['notes'],expected)
        edit('Max is not White and black fur. Max has brown and black fur.','Brown and black fur.','White and black fur.')
        receipt=edit('Max has brown fur.','Brown fur.','Terrier.')
        self.assertNotIn('Saved the detail.',receipt.completed_text)
        self.assertIn('Terrier.',self.album.catalog()[0]['notes'])
        receipt=edit('Max has brown fur.','Brown fur.','invented previous detail')
        self.assertNotIn('Saved the detail.',receipt.completed_text)
        restarted=PhotoMemory(self.album.root);restarted.start()
        notes=restarted.catalog()[0]['notes']
        self.assertIn('Terrier.',notes)
        self.assertIn('Brown and black fur.',notes)
        self.assertNotIn('White and black fur.',notes)
        self.assertNotIn('Gray with brown paws.',notes)
        source=restarted.data['entities'][key]['history'][-1]['source']
        self.assertEqual(source['text'],'Max is not White and black fur. Max has brown and black fur.')

    def test_teaching_requires_save_or_explicit_defer_before_speech(self):
        for defer in (False,True):
            class Brain:
                def reply(brain,snapshot,cancel):
                    if not snapshot.continuation:
                        yield ToolCall('look','inspect_visual_memory',{'intent':'teach','target_id':'object-1','entity_ids':[],'question':'Remember Max.'})
                    elif defer:
                        yield ToolCall('unclear','defer_photo_teaching',{'text':'I have not saved it. Which dog is Max?'})
                    else:yield 'I remember Max now.'
            session,receipt=self.session(Brain(),'That dog is Max.');session.close()
            self.assertFalse(self.album.catalog())
            self.assertNotIn('I remember Max now.',receipt.completed_text)
            if defer:self.assertEqual(receipt.completed_text,['I have not saved it. Which dog is Max?'])

    def test_inspection_can_finish_with_detail_update(self):
        key=self.teach()
        class Brain:
            def reply(brain,snapshot,cancel):
                if not snapshot.continuation:
                    yield ToolCall('inspect-detail','inspect_visual_memory',{'intent':'look','target_id':'object-1','entity_ids':[key],'question':'Inspect Max.'})
                else:
                    assert 'edit_object_memory' in {d['name'] for d in grounded_tools(snapshot.context)}
                    yield ToolCall('breed','edit_object_memory',{'entity_id':key,'action':'correct','name':'Max',
                        'notes':'Boston Terrier.','remove_notes':None,'source_turn_id':snapshot.context['source_turn_id'],'text':'Saved the breed.'})
        session,receipt=self.session(Brain(),'Max is a Boston Terrier.');session.close()
        self.assertEqual(receipt.completed_text,['Saved the breed.'])
        self.assertIn('Boston Terrier.',self.album.catalog()[0]['notes'])
        self.assertIn('Small dark dog',self.album.catalog()[0]['notes'])

    def test_multi_pet_overview_cannot_be_saved_as_one_pet(self):
        self.begin('This is Max.',C)
        evidence=self.album.offer(image(),{},self.state.trace.clock())
        from r3x.photo_flow import context
        ctx={**self.state.context(),**context(self.state),'photo_inspection':{'evidence_id':evidence,'target':{}}}
        ctx['photo_memory']['objects']=[{'label':'dog'},{'label':'cat'}]
        args={'evidence_id':evidence,'entity_id':None,'name':'Max','kind':'pet','notes':'Dog.',
              'source_turn_id':self.state.current[0],'text':'Saved.'}
        result=self.state.apply_photo(ToolCall('whole-view','remember_object',args),ctx,Cancellation())
        self.assertEqual(result,{'outcome':'rejected','reason':'PhotoTeachingNeedsSinglePetCrop'})
        self.assertFalse(self.album.catalog())

    def test_invalid_source_expired_unoffered_and_frozen_writes_rejected(self):
        self.begin('This is Max.',C);turn=self.state.current[0]
        evidence=self.album.offer(image(),{},self.state.trace.clock())
        args={'evidence_id':evidence,'entity_id':None,'name':'Max','kind':'pet','notes':'Dark dog.',
              'source_turn_id':turn,'text':'Saved.'}
        from r3x.photo_flow import context
        ctx={**self.state.context(),**context(self.state),'photo_inspection':{'evidence_id':evidence}}
        for mode in ('extra_field','wrong_source','unoffered','expired','frozen'):
            a=dict(args);self.state.frozen=False;self.state.trace.at=100.
            if mode=='extra_field':a['unexpected']='Not a valid tool field.'
            if mode=='wrong_source':a['source_turn_id']='other'
            if mode=='unoffered':a['evidence_id']='other'
            if mode=='expired':self.state.trace.at=200.
            if mode=='frozen':self.state.frozen=True
            result=self.state.apply_photo(ToolCall(mode,'remember_object',a),ctx,Cancellation())
            self.assertEqual(result['outcome'],'rejected',mode)
        self.assertFalse(self.album.catalog());self.assertFalse(list(self.album.root.glob('*/*.png')))

    def test_failed_catalog_commit_does_not_ack_or_leave_photo(self):
        with patch('r3x.photo_memory.os.replace',side_effect=OSError('write failed')):
            class Brain:
                def reply(brain,snapshot,cancel):
                    if not snapshot.continuation:yield ToolCall('look','inspect_visual_memory',{'intent':'look','target_id':None,'entity_ids':[],'question':'Remember Max'})
                    else:yield ToolCall('save','remember_object',{'evidence_id':snapshot.context['photo_inspection']['evidence_id'],
                        'entity_id':None,'name':'Max','kind':'pet','notes':'Dog.', 'source_turn_id':snapshot.context['source_turn_id'],
                        'text':'Saved Max.'})
            s,r=self.session(Brain(),'This is Max.')
            self.assertNotIn('Saved Max.',r.completed_text);s.close()
        self.assertFalse(self.album.catalog());self.assertFalse(list(self.album.root.glob('*/*.png')))

    def test_inspection_failure_has_no_reference_images_or_save_tool(self):
        def fail(target):raise ValueError('FreshObjectViewUnavailable')
        self.state.objects.inspect=fail
        class Brain:
            def reply(brain,snapshot,cancel):
                if not snapshot.continuation:yield ToolCall('look','inspect_visual_memory',{'intent':'look','target_id':None,'entity_ids':[],'question':'Who is that?'})
                else:
                    assert not snapshot.images and not grounded_tools(snapshot.context)
                    yield 'I cannot get a fresh picture.'
        s,r=self.session(Brain(),'Who is that?');self.assertEqual(r.completed_text,['I cannot get a fresh picture.']);s.close()

    def test_hosted_photo_continuation_transmits_save_tool_and_reference_images(self):
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Snapshot
        from tests.test_adapters import Client,Response
        brain=OpenAIBrain('test')
        brain.client=Client(Response([{'type':'response.output_text.delta','delta':'A dog.'},{'type':'response.completed'}]))
        ctx={'source_turn_id':'turn','photo_memory':{'entities':[],'objects':[]},
             'photo_inspection':{'intent':'teach','evidence_id':'photo-test','target':{},'images':[{'role':'current view'},{'name':'test reference'}]}}
        request=Snapshot('session','turn',0,(),context=ctx,tools_enabled=True,
                         continuation=({'type':'function_call_output','call_id':'look','output':'{}'},),images=(image(),image()))
        list(brain.reply(request,Cancellation()))
        payload=brain.client.calls[0][1]['json']
        self.assertEqual({d['name'] for d in payload['tool_choice']['tools']},{'remember_object','defer_photo_teaching'})
        self.assertEqual(payload['tool_choice']['mode'],'required')
        self.assertEqual(sum(c['type']=='input_image' for c in payload['input'][-1]['content']),2)

    def test_cancelled_inspection_never_saves_or_delivers(self):
        class Brain:
            def reply(brain,snapshot,cancel):
                if not snapshot.continuation:yield ToolCall('look','inspect_visual_memory',{'intent':'look','target_id':None,'entity_ids':[],'question':'Remember Max'})
                else:
                    cancel.event.set();cancel.check()
        s,r=self.session(Brain(),'This is Max.')
        self.assertEqual(r.status,'interrupted');self.assertFalse(self.album.catalog());self.assertFalse(r.completed_text);s.close()

    def test_a_look_that_tries_to_save_without_a_name_just_answers(self):
        # October 13: "Yeah, but you know which dog" got a save attempt, then "Please say the pet
        # or object's name with that description so I can save it".
        key=self.teach()
        calls=[]
        class Brain:
            def reply(brain,snapshot,cancel):
                calls.append(snapshot)
                if len(calls)==1:
                    yield ToolCall('look','inspect_visual_memory',{'intent':'look','target_id':'object-1','entity_ids':[key],'question':'Which dog is it?'})
                elif len(calls)==2:
                    yield ToolCall('save-unnamed','remember_object',{'evidence_id':snapshot.context['photo_inspection']['evidence_id'],
                        'entity_id':key,'name':'Max','kind':'pet','notes':'Held against his chest.',
                        'source_turn_id':snapshot.context['source_turn_id'],'text':'Saved Max.'})
                else:
                    assert not snapshot.tools_enabled
                    yield "That's Max."
        session,receipt=self.session(Brain(),'Yeah, but you know which dog.')
        self.assertEqual(receipt.completed_text,["That's Max."])
        self.assertEqual(self.album.catalog()[0]['reference_count'],1)   # Nothing saved.
        session.close()
