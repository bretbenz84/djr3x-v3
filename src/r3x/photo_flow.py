"""Bounded photographic inspection/teaching through the existing Session model."""
import json
from dataclasses import replace
from .contracts import ProviderFailure
from .semantics import validate


def context(state):
    if state.photo_memory is None:return {}
    return {'photo_memory':{'revision':state.photo_memory.data['revision'],'entities':state.photo_memory.catalog(),
        'objects':list(state.objects.snapshot()) if state.objects else [],
        'current_photographic_evidence':False,
        'catalog_scope':'Only these entries are saved. A mentioned name absent here has NO saved photographic memory. Never apply its details to the sole existing entry.',
        'detector_scope':'Unidentified category/position hints only. No named identity or appearance evidence. Inspect fresh pixels and references before answering current pet identity or spatial comparisons.',
        'meaning':'Named objects/pets and reference photos. Detector labels and tracks are uncertain, not permanent identity. No person identity or ownership inferred.'}}


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
            image,target,overview=state.objects.inspect(call.arguments['target_id'])
            references,labels=album.references(call.arguments['entity_ids'])
            evidence_id=album.offer(image,target,session.trace.clock())
            views=(overview,image) if target.get('target_id') else (image,)
            labels_current=[{'role':'current overview','frame_id':overview.frame_id}]
            if len(views)==2:labels_current.append({'role':'current target crop','target':target,'frame_id':image.frame_id})
            images=(*views,*references)
            data={'outcome':'ok','intent':call.arguments['intent'],'evidence_id':evidence_id,'target':target,'question':call.arguments['question'],
                  'images':[*labels_current,*labels]}
            ctx={**snapshot.context,**context(state),'photo_inspection':data}
            ctx['photo_memory']['current_photographic_evidence']=True
        except (ValueError,OSError) as exc:
            data={'outcome':'failed','reason':str(exc)};images=()
            ctx={**snapshot.context,**context(state),'photo_inspection_failed':True}
        session.trace.emit('photo_inspection',turn_id=snapshot.turn_id,**data,image_count=len(images))
    continuation=(*call.preamble,{'type':'function_call','call_id':call.call_id,'name':call.name,'arguments':json.dumps(call.arguments)},
                  {'type':'function_call_output','call_id':call.call_id,'output':json.dumps(data)})
    followup=replace(snapshot,context=ctx,continuation=continuation,images=images,tools_enabled=bool(images) and not ctx.get('trigger'))
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
            if reply[1].get('reason')=='PhotoSubjectNeedsExplicitName':return 'Please say the pet or object’s name with that description so I can save it to the right one.'
            if reply[1].get('reason')=='PhotoTeachingNeedsSinglePetCrop':return 'I haven’t saved that yet. With both dogs in view, please give me the name and a clear position or coat marking so I can isolate the right one.'
            return 'I couldn’t save that picture. Please show me again.'
        return reply
    return None
