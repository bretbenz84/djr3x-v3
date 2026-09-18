"""Bounded photographic inspection/teaching through the existing Session model."""
import json
from dataclasses import replace
from .contracts import ProviderFailure
from .semantics import validate


def context(state):
    if state.photo_memory is None:return {}
    return {'photo_memory':{'revision':state.photo_memory.data['revision'],'entities':state.photo_memory.catalog(),
        'objects':list(state.objects.snapshot()) if state.objects else [],
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
            data={'outcome':'ok','evidence_id':evidence_id,'target':target,'question':call.arguments['question'],
                  'images':[*labels_current,*labels]}
            ctx={**snapshot.context,**context(state),'photo_inspection':data}
        except (ValueError,OSError) as exc:
            data={'outcome':'failed','reason':str(exc)};images=()
            ctx={**snapshot.context,**context(state),'photo_inspection_failed':True}
        session.trace.emit('photo_inspection',turn_id=snapshot.turn_id,**data,image_count=len(images))
    continuation=(*call.preamble,{'type':'function_call','call_id':call.call_id,'name':call.name,'arguments':json.dumps(call.arguments)},
                  {'type':'function_call_output','call_id':call.call_id,'output':json.dumps(data)})
    followup=replace(snapshot,context=ctx,continuation=continuation,images=images,tools_enabled=bool(images) and not ctx.get('trigger'))
    session.trace.emit('model_request',turn_id=snapshot.turn_id,primary_calls=0,purpose='photo_memory_continuation',
                       image_count=len(images),image_bytes=sum(len(i.data_url) for i in images))
    calls=generate(followup)
    if calls:
        if len(calls)!=1 or calls[0].name!='remember_object' or not images:raise ProviderFailure('PhotoToolContinuationLimit')
        reply=apply(calls[0],ctx)
        if isinstance(reply,tuple):return 'I couldn’t save that picture. Please show me again.'
        return reply
    return None
