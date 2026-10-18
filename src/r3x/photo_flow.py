"""Bounded photographic inspection/teaching through the existing Session model."""
from .field_guide import GUIDE
import json
from dataclasses import replace
from .contracts import ProviderFailure
from .semantics import validate


# The session refuses more than eight images in one photo request.
PHOTO_IMAGE_LIMIT = 8


def context(state):
    if state.photo_memory is None:return {}
    return {'photo_memory':{'revision':state.photo_memory.data['revision'],'entities':state.photo_memory.catalog(),
        'objects':list(state.objects.snapshot()) if state.objects else [],
        'current_photographic_evidence':False,
        'catalog_scope':GUIDE['photo_memory.catalog_scope'],
        'detector_scope':GUIDE['photo_memory.detector_scope'],
        'meaning':GUIDE['photo_memory']}}


def comparison(state, images):
    """Give ordinary current-view inspection the same bounded reference evidence."""
    if state.photo_memory is None or not images:return {},images
    try:
        references,labels=state.photo_memory.references()
        error=None
    except (ValueError,OSError) as exc:
        references,labels=(),[];error=str(exc)
    entries=state.photo_memory.catalog()
    data={'intent':'look','images':[{'role':'current overview','frame_id':i.frame_id} for i in images]+labels,
          'reference_coverage':'complete' if len(labels)==len(entries) and error is None else 'unavailable',
          'reference_error':error}
    return {'photo_inspection':data},(*images,*references)


def run(session,call,snapshot,generate,cancel):
    """Returns executor-authorized acknowledgment or lets generate stream the reply."""
    state=session.state;album=state.photo_memory
    if album is None:raise ProviderFailure('PhotoMemoryUnavailable')
    def apply(call,ctx):
        cancel.check()
        session.trace.emit('tool_proposed',turn_id=snapshot.turn_id,call_id=call.call_id,name=call.name,arguments=call.arguments)
        with session._lock:result=state.apply_photo(call,ctx,cancel)
        session.trace.emit('photo_tool_result',turn_id=snapshot.turn_id,name=call.name,**result)
        session.history.append({'role':'developer','content':'Photographic memory executor result: '+json.dumps(result)})
        if result['outcome']=='ok':return call.arguments['text']
        return None,result
    validate(call.name,call.arguments)
    if call.name!='inspect_visual_memory':
        reply=apply(call,snapshot.context)
        if not isinstance(reply,tuple):return reply
        data=reply[1];images=();ctx={**snapshot.context,**context(state),'photo_inspection_failed':True}
    else:
        try:
            if not state.objects:raise ValueError('ObjectCameraUnavailable')
            teaching=call.arguments['intent']=='teach'
            try:
                image,target,overview=state.objects.inspect(call.arguments['target_id'])
            except ValueError as exc:
                # A look (never a teaching save, which needs the exact subject) falls back to
                # the whole current view when the chosen thing's track is gone.
                if teaching or str(exc)!='ObjectTargetExpired':raise
                image,target,overview=state.objects.inspect(None)
            # October 18: with nothing chosen, every animal seen in the last few seconds gets its own
            # close crop; a pet is taught and compared from those, never from the whole room.
            pets=[] if target.get('target_id') else list(getattr(state.objects,'pet_views',lambda:[])())
            now=session.trace.clock()
            evidence_id=album.offer(image,target,now)
            views=(overview,image) if target.get('target_id') else (image,)
            labels_current=[{'role':'current overview','frame_id':overview.frame_id,'evidence_id':evidence_id}]
            if len(views)==2:labels_current.append({'role':'current target crop','target':target,'frame_id':image.frame_id,'evidence_id':evidence_id})
            for crop,pet in pets:
                views=(*views,crop)
                labels_current.append({'role':'current pet crop','target':pet,'frame_id':crop.frame_id,
                                       'evidence_id':album.offer(crop,pet,now)})
            if teaching:
                references,labels=(),[]
            else:
                references,labels=album.references(call.arguments['entity_ids'] or None,kinds={'pet'} if pets else None)
                room=PHOTO_IMAGE_LIMIT-len(views)
                if len(references)>room:references,labels=references[:max(room,0)],labels[:max(room,0)]
            images=(*views,*references)
            data={'outcome':'ok','intent':call.arguments['intent'],'evidence_id':evidence_id,
                  'evidence_ids':[row['evidence_id'] for row in labels_current],'target':target,
                  'question':call.arguments['question'],'images':[*labels_current,*labels]}
            ctx={**snapshot.context,**context(state),'photo_inspection':data}
            ctx['photo_memory']['current_photographic_evidence']=True
        except (ValueError,OSError) as exc:
            data={'outcome':'failed','reason':str(exc)};images=()
            ctx={**snapshot.context,**context(state),'photo_inspection_failed':True}
        session.trace.emit('photo_inspection',turn_id=snapshot.turn_id,**data,image_count=len(images))
    continuation=(*call.preamble,{'type':'function_call','call_id':call.call_id,'name':call.name,'arguments':json.dumps(call.arguments)},
                  {'type':'function_call_output','call_id':call.call_id,'output':json.dumps(data)})
    messages=snapshot.messages
    if data['outcome']=='failed':
        # October 3: "He barks at every visitor" was answered with "I tried to check Max's
        # saved photo, but that image reference had expired".
        messages=(*messages,{'role':'developer','content':
            'The close look could not be taken. Do not mention the attempt or why it failed unless they '
            'asked you to look at or identify something; just answer what they said.'})
    if data.get('outcome')=='ok' and snapshot.context.get('trigger')=='pet_arrival':
        # October 13 probe: the first reaction to the dog walking in was a report on image clarity.
        messages=(*messages,{'role':'developer','content':
            'This look is at an animal that just came into view. Greet it the way a friend would when the dog walks '
            'in, then ask the person something about it. If it clearly is a saved pet, use its name ("Oh look, it\'s '
            'Max! How\'s he behaving tonight?"); if you are not sure, ask playfully ("Is that Max? Hey there, buddy!"). '
            'Never talk about image quality or how confident the match is.'})
    elif data.get('outcome')=='ok' and data.get('intent')=='look':
        # October 13: shown Max's saved photo while Bret held him, Rex said "a small dark-colored
        # dog", then tried to save a photo to answer "you know which dog" and asked for the name.
        messages=(*messages,{'role':'developer','content':
            'Compare the current view with the saved references; for an animal, compare its own current pet crop with '
            'the saved pet crops, never the whole room (October 18). When one saved pet or object clearly matches, '
            'say its name plainly ("That\'s Max"); hedge only when it genuinely could be more than one, and then '
            'say which you lean toward. Answer their question in words; save a photo only when they name it to teach you.'})
    if data.get('intent')=='teach':
        # Teaching starts from the human's words and current pixels. Old model prose,
        # appearance notes and historical photos must not describe a different object.
        ctx={k:ctx[k] for k in ('state_revision','source_turn_id','current_utterance','photo_memory','photo_inspection','trigger') if k in ctx}
        ctx['photo_memory']={**ctx['photo_memory'],'entities':[
            {k:v for k,v in entry.items() if k!='notes'} for entry in ctx['photo_memory']['entities']]}
        data['question']=ctx.get('current_utterance','')
        messages=({'role':'user','content':ctx.get('current_utterance','')},)
        continuation=({'role':'developer','content':'Current photographic teaching evidence: '+json.dumps(data)+
                       ' A pet is saved from its own current pet crop (that crop\'s evidence_id), never the overview: pick '
                       'the crop of the animal they mean (held in their arms, beside them, the one they point out). '
                       'With no pet crop, ask them to hold the animal up toward you instead of saving.'},)
    followup=replace(snapshot,messages=messages,context=ctx,continuation=continuation,images=images,tools_enabled=bool(images) and not ctx.get('trigger'))
    session.trace.emit('model_request',turn_id=snapshot.turn_id,primary_calls=0,purpose='photo_memory_continuation',
                       image_count=len(images),image_bytes=sum(len(i.data_url) for i in images),
                       reasoning_effort=getattr(session.brain,'reasoning_effort',None))
    calls=generate(followup)
    if calls:
        if len(calls)==1 and calls[0].name=='defer_photo_teaching' and data.get('intent')=='teach':
            validate(calls[0].name,calls[0].arguments)
            session.trace.emit('photo_tool_result',turn_id=snapshot.turn_id,name=calls[0].name,outcome='deferred')
            session.history.append({'role':'developer','content':'Photographic teaching deferred: no photo or notes were saved.'})
            return calls[0].arguments['text']
        allowed={'remember_object'} if data.get('intent')=='teach' else {'remember_object','edit_object_memory'}
        if len(calls)!=1 or calls[0].name not in allowed or not images:raise ProviderFailure('PhotoToolContinuationLimit')
        reply=apply(calls[0],ctx)
        if isinstance(reply,tuple):
            if reply[1].get('reason')=='PhotoSubjectNeedsExplicitName' and data.get('intent')=='look':
                # October 13: asked "you know which dog?", Rex tried to save a photo and then asked
                # Bret for the name. Nothing was being taught: answer the question.
                session.trace.emit('model_request',turn_id=snapshot.turn_id,primary_calls=0,purpose='photo_look_answer',
                                   image_count=len(images),reasoning_effort=getattr(session.brain,'reasoning_effort',None))
                if generate(replace(followup,tools_enabled=False,messages=(*followup.messages,{'role':'developer','content':
                        'Nothing was saved and nothing needs saving. Answer their question in words from the images, '
                        'naming the saved pet or object that matches.'}))):
                    raise ProviderFailure('PhotoToolContinuationLimit')
                return None
            if reply[1].get('reason')=='PhotoSubjectNeedsExplicitName':return 'Please say the pet or object’s name with that description so I can save it to the right one.'
            if reply[1].get('reason')=='PetPhotoNeedsCloseCrop':return 'I can’t get a close enough look yet. Can you hold them up toward me for a second?'
            return 'I couldn’t save that picture. Please show me again.'
        return reply
    return None
