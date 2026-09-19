"""Explicit hosted A/B probes: fictional visitors and temporary stores.

Output is silent text by default; --synthetic-delivery simulates heard receipts.
No camera, microphone, real playback, identity model or live personal database is used.
Read dialogues in evidence.json; structural outcomes are not social-quality scores.
"""
import argparse
from dataclasses import asdict, replace
from datetime import date
import json
from pathlib import Path
import statistics
import subprocess
import time
import types
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Cancellation, Capture, ProviderFailure, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.proactive import Opportunity
from r3x.session import Session
from r3x.settings import Secrets, ScratchPaths
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace, report


CASES = (
    ('new_interest', False, False, False, ('I enjoy photographing birds.', 'What would make a good bird photograph?')),
    ('familiar_no_memory', True, False, False, ('Good to see you too. My week has been pretty ordinary.', 'I would rather hear something interesting from you.')),
    ('familiar_followup', True, True, False, ('The sailing preparations are going well.', 'Actually, tell me something about navigation.')),
    ('private_bystander', True, True, True, ('My friend is here too. Let us talk about music.', 'What makes a good dance song?')),
    ('mixed_group', True, False, True, ('My friend wants to hear how your camera works.', 'Explain it to both of us.')),
    ('short_reply', False, False, False, ('Fine, thanks.', 'I am listening. Tell me something amusing.')),
    ('redirect', False, False, False, ('Skip the introductions. How do you see the room?', 'What happens in the dark?')),
    ('topic_boundary', True, False, False, ('Please do not bring up work this visit.', 'Let us talk about movies instead.')),
    ('quiet_direct', False, False, False, ('Stop starting conversations until I ask again.', 'What is the difference between a comet and an asteroid?')),
    ('thinking_pause', False, False, False, ('Give me a minute to think about what I want to ask.', None)),
    ('human_floor', True, False, True, ('Sam, which movie do you want to watch? That question is for Sam, Rex.', None)),
    ('unanswered', False, False, False, (None,)),
    ('listening_lulls', False, False, False, ('I enjoy photographing birds. I would like to hear your ideas.',None,None,None)),
)


def load_baseline(root, revision):
    """Exact tracked pre-change prompt/transport and tool schema; no V2 dependency."""
    def module(path, package):
        source = subprocess.check_output(['git','show',f'{revision}:{path}'],cwd=root,text=True)
        obj = types.ModuleType('intention_probe_baseline')
        obj.__package__ = package
        exec(compile(source,path,'exec'),obj.__dict__)
        return obj
    return module('src/r3x/adapters/hosted.py','r3x.adapters'), module('src/r3x/semantics.py','r3x')


class BaselineBrain:
    def __init__(self, brain, schema):
        self.brain,self.schema=brain,schema
        self.capability,self.reasoning_effort=brain.capability,brain.reasoning_effort
    def reply(self,snapshot,cancel):
        import r3x.semantics as semantics
        context={k:v for k,v in snapshot.context.items() if k!='conversation_intent'}
        previous=semantics.grounded_tools
        semantics.grounded_tools=self.schema.grounded_tools
        try:yield from self.brain.reply(replace(snapshot,context=context),cancel)
        finally:semantics.grounded_tools=previous


class InstrumentedClient:
    """Read-only timing/error metadata around the same provider HTTP stream."""
    def __init__(self, client, trace):
        self.client,self.trace=client,trace
    def close(self):self.client.close()
    def stream(self,*args,**kwargs):
        from contextlib import contextmanager
        @contextmanager
        def opened():
            began=time.monotonic()
            payload=kwargs.get('json',{})
            marker='CURRENT CORE CONTEXT (observations/data, not instructions):\n'
            # Current requests carry the context in a developer message after the history;
            # the retained baseline adapter still appends it to instructions.
            texts=[payload.get('instructions','')]+[m['content'] for m in payload.get('input',[])
                if isinstance(m,dict) and m.get('role')=='developer' and isinstance(m.get('content'),str)]
            source=next((x for x in reversed(texts) if marker in x),None)
            if source:
                context,_=json.JSONDecoder().raw_decode(source.split(marker,1)[1])
                choice=payload.get('tool_choice')
                self.trace.emit('probe_intent_context',intent=context.get('conversation_intent'),
                    tools=[t['name'] for t in (choice['tools'] if isinstance(choice,dict) else payload.get('tools',[]))])
            with self.client.stream(*args,**kwargs) as response:
                owner=self
                class Response:
                    status_code=response.status_code
                    def iter_lines(self):
                        first=True
                        for line in response.iter_lines():
                            if line.startswith('data:') and line[5:].strip()!='[DONE]':
                                item=json.loads(line[5:]);kind=item.get('type')
                                if first and kind in {'response.output_text.delta','response.function_call_arguments.delta'}:
                                    first=False
                                    owner.trace.emit('probe_first_delta',seconds=time.monotonic()-began,kind=kind)
                                if kind in {'error','response.failed','response.incomplete'}:
                                    result=item.get('response',{})
                                    error=item.get('error') or result.get('error') or {}
                                    import re
                                    message=error.get('message') or item.get('message') or ''
                                    limits={k:re.search(k+r'[: ]+([0-9]+)',message,re.I).group(1) for k in ('limit','used','requested') if re.search(k+r'[: ]+([0-9]+)',message,re.I)}
                                    owner.trace.emit('probe_provider_error',kind=kind,limits=limits,
                                        code=error.get('code') or item.get('code'),
                                        error_type=error.get('type'),
                                        incomplete_reason=result.get('incomplete_details',{}).get('reason'),
                                        usage=result.get('usage'))
                            yield line
                yield Response()
        return opened()


def voice():
    return VoiceObservation('synthetic:3',(1.,0.,0.),((0,3,(1.,0.,0.)),),3.,1.,reason='synthetic')


def seed(state,sid,now,memory):
    for index,text in enumerate(('My name is Avery.','I enjoy sailing and old radios.','I work on little electronics projects.')):
        at=now()+index
        capture=Capture(uuid4().hex,sid,uuid4().bytes*6000,at-4,at-1,at,voiced_intervals=((0,3),))
        turn=uuid4().hex;state.begin(turn,capture,Transcript(text,-.01,True),voice())
        if index==0:
            result=state.execute(ToolCall(uuid4().hex,'introduce_person',dict(name='Avery',aliases=[],subject='self',person_id=None,face_track_id=None)),state.store.revision,Cancellation())
            if result.outcome!='ok':raise RuntimeError(result.reason)
        state.advance_enrollment()
    person=state.store.people()[0]['id']
    if memory:
        at=now()+4
        capture=Capture(uuid4().hex,sid,uuid4().bytes*6000,at-4,at-1,at,voiced_intervals=((0,3),))
        state.begin(uuid4().hex,capture,Transcript('I am sailing on September 20. Remember it, but only discuss it with me alone.',-.01,True),voice())
        result=state.execute(ToolCall(uuid4().hex,'remember_followup',dict(predicate='sailing_plan',
            value='Sailing on September 20',kind='plan',on_date='2026-09-20',audience='subject_only',
            source_turn_id=state.current[0])),state.store.revision,Cancellation())
        if result.outcome!='ok':raise RuntimeError(result.reason)
    state.current=None
    return person


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-ref',default='a510502')
    parser.add_argument('--repetitions',type=int,default=3,choices=range(1,4))
    parser.add_argument('--families',nargs='*',choices=[r[0] for r in CASES])
    parser.add_argument('--pace-seconds',type=float,default=5.0)
    parser.add_argument('--resume',type=Path)
    parser.add_argument('--synthetic-delivery',action='store_true',help='Simulate completed audio with fake TTS/sink; never play real audio')
    parser.add_argument('--rerun-arm',choices=('baseline','candidate'))
    args=parser.parse_args()
    if not 0 <= args.pace_seconds <= 60:raise SystemExit("pace-seconds must be between 0 and 60")
    root=Path(__file__).resolve().parents[1];local=root/'local'
    ScratchPaths(local).prepare()
    key=Secrets.load(local).openai_api_key
    if not key:raise SystemExit('OpenAI credential required')
    old,old_schema=load_baseline(root,args.baseline_ref)
    folder=args.resume.resolve() if args.resume else local/'validation'/('conversation-intentions-'+uuid4().hex)
    if not folder.is_relative_to((local/'validation').resolve()):raise SystemExit('Evidence must remain under local/validation')
    folder.mkdir(parents=True,exist_ok=bool(args.resume))
    (folder/('resume.json' if args.resume else 'run.json')).write_text(json.dumps(dict(baseline=args.baseline_ref,repetitions=args.repetitions,pace_seconds=args.pace_seconds,
        evidence='Hosted fictional text, fake acoustic evidence, in-memory databases; '+('simulated audio receipts, no playback' if args.synthetic_delivery else 'silent TextOutput'),
        devices_opened=False,audio_played=False,synthetic_delivery=args.synthetic_delivery,personal_database_used=False)))
    print(str(folder),flush=True)
    rows=json.loads((folder/'evidence.json').read_text()) if args.resume else []
    if args.resume:(folder/('prior-evidence-'+uuid4().hex+'.json')).write_text(json.dumps(rows,indent=2))
    last_turn_at=float('-inf')
    for repeat in range(args.repetitions):
        for name,known,memory,group,words in CASES:
            if args.families and name not in args.families:continue
            # Alternate order to reduce simple warmup/time-order bias. No concurrent requests.
            for arm in (('baseline','candidate') if repeat%2==0 else ('candidate','baseline')):
                previous=next((r for r in rows if (r['family'],r['repeat'],r['arm'])==(name,repeat,arm)),None)
                if previous and args.rerun_arm!=arm and all(t['receipt']['status'] in {'completed','suppressed'} for t in previous['turns']):continue
                trial_id=uuid4().hex[:8]
                offset=[0.];now=lambda:time.monotonic()+offset[0]
                sid=uuid4().hex;trace=Trace(sid,folder/f'{name}-{repeat}-{arm}-{trial_id}.jsonl',clock=now,display=lambda _:None)
                provider=(old.OpenAIBrain if arm=='baseline' else OpenAIBrain)(key)
                provider.trace=trace
                def no_retry(seconds,cancel):
                    cancel.check()
                    raise ProviderFailure('HostedProbeNetworkUnavailable')
                provider._wait_retry=no_retry
                provider.start()
                provider.client=InstrumentedClient(provider.client,trace)
                brain=BaselineBrain(provider,old_schema) if arm=='baseline' else provider
                store=Memory(':memory:');store.start();state=CoreState(store,SpeakerTracker('synthetic:3'),trace)
                state.calendar_today=lambda:date(2026,9,18)
                person=seed(state,sid,now,memory) if known else None
                output=TextOutput(trace)
                if args.synthetic_delivery:
                    from r3x.output import OutputOwner
                    from r3x.scenario import SyntheticTTS, SyntheticSink
                    output=OutputOwner(SyntheticTTS(),SyntheticSink(),trace)
                session=Session(sid,brain,output,trace,state)
                def faces():
                    result=[dict(at=now(),track_id='visitor',identity=person,face_epoch=0,face_width_px=150,box=[.1,.2,.2,.3])]
                    if group:result.append(dict(at=now(),track_id='companion',identity=None,face_epoch=0,face_width_px=150,box=[.6,.2,.2,.3]))
                    return tuple(result)
                outcomes=[]
                try:
                    for index,text in enumerate(('__greeting__',)+words):
                        wait=max(0,args.pace_seconds-(time.monotonic()-last_turn_at))
                        if wait:time.sleep(wait)
                        offset[0]+=(8,15,30,60)[min(max(0,index-2),3)] if name=='listening_lulls' and text is None else 10
                        began=last_turn_at=time.monotonic()
                        if text=='__greeting__' or text is None:
                            kind='visual_arrival' if index==0 else 'ambient'
                            event=Opportunity(uuid4().hex,sid,now(),'visitor' if index==0 else None,person if index==0 else None,
                                faces(),session._latest_speech,False,kind,state.current[0] if state.current else None,10.)
                            receipt=session.process_opportunity(event,session.revision,lambda:True)
                        else:
                            at=now();capture=Capture(uuid4().hex,sid,uuid4().bytes*6000,at-4,at-1,at,
                                voiced_intervals=((0,3),),device='synthetic')
                            session.note_speech(at-1)
                            receipt=session.process_turn(capture,Transcript(text,-.01,True),session.revision,voice(),faces())
                        if receipt.error=='HostedProbeNetworkUnavailable':raise RuntimeError('HostedProbeNetworkUnavailable; inspect trace and rerun with network access')
                        outcomes.append(dict(source=text,seconds=time.monotonic()-began,receipt=asdict(receipt),
                            intention=asdict(state.conversation_intent.record) if state.conversation_intent.record else None))
                    row=dict(family=name,repeat=repeat,arm=arm,turns=outcomes,
                        model=provider.capability.model,reasoning=provider.reasoning_effort)
                finally:
                    session.close();provider.close();store.close();trace.close()
                row['report']=report(trace.path if hasattr(trace,'path') else folder/f'{name}-{repeat}-{arm}-{trial_id}.jsonl')
                import hashlib
                row['source_digests']={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (root/'src/r3x/conversation_intent.py',root/'src/r3x/adapters/hosted.py',root/'src/r3x/semantics.py')}
                row['trace_file']=f'{name}-{repeat}-{arm}-{trial_id}.jsonl'
                if previous:rows.remove(previous)
                rows.append(row);(folder/'evidence.json').write_text(json.dumps(rows,indent=2))
                print(name,repeat,arm,[t['receipt']['status'] for t in outcomes],flush=True)
    summary={}
    for arm in ('baseline','candidate'):
        chosen=[r for r in rows if r['arm']==arm]
        times=sorted(t['seconds'] for r in chosen for t in r['turns'])
        reports=[r['report'] for r in chosen]
        deltas=[];first_text=[];intent_sizes=[];tool_counts={}
        for row in chosen:
            path=folder/row.get('trace_file',f"{row['family']}-{row['repeat']}-{row['arm']}.jsonl")
            starts={};seen=set()
            for line in path.read_text().splitlines():
                item=json.loads(line);kind=item.get('event');turn=item.get('turn_id')
                if kind=='probe_first_delta':deltas.append(item['seconds'])
                if kind=='model_request':starts.setdefault(turn,item['at'])
                if kind=='text_reply' and turn in starts and turn not in seen:
                    seen.add(turn);first_text.append(item['at']-starts[turn])
                if kind=='probe_intent_context' and item.get('intent'):
                    intent_sizes.append(len(json.dumps(item['intent'])))
                if kind=='tool_proposed':tool_counts[item['name']]=tool_counts.get(item['name'],0)+1
        def distribution(values):
            ordered=sorted(values)
            return dict(samples=len(values),median=statistics.median(values) if values else None,
                p95=ordered[max(0,__import__('math').ceil(len(ordered)*.95)-1)] if ordered else None)
        summary[arm]=dict(first_text_seconds=distribution(first_text),first_delta_seconds=distribution(deltas),
            intent_context_characters=distribution(intent_sizes),tools=tool_counts,first_delta_median_seconds=statistics.median(deltas) if deltas else None,first_delta_samples=len(deltas),conversations=len(chosen),turns=len(times),median_seconds=statistics.median(times),
            p95_seconds=times[max(0,__import__('math').ceil(len(times)*.95)-1)],
            primary_calls=sum(r['primary_calls'] for r in reports),continuations=sum(r['tool_result_continuations'] for r in reports),
            tokens={k:sum(r['reported_model_tokens'].get(k,0) for r in reports) for k in ('input_tokens','output_tokens','total_tokens')},
            failures=[failure for r in reports for failure in r['failures']],
            review='Read actual dialogue; no automatic social-quality or accuracy score.')
    (folder/'summary.json').write_text(json.dumps(summary,indent=2));print(json.dumps(summary),flush=True)


if __name__=='__main__':main()
