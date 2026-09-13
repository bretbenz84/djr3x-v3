"""Explicit hosted synthetic memory probes; no personal database or devices."""
from dataclasses import asdict
from datetime import date
import json
from pathlib import Path
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.proactive import Opportunity
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace, report


def main():
    root=Path(__file__).resolve().parents[1]/'local'
    key=Secrets.load(root).openai_api_key
    if not key:raise SystemExit('OpenAI credential required')
    folder=root/'validation'/('memory-opportunities-'+uuid4().hex);folder.mkdir(parents=True)
    offset=[0.]
    trace=Trace('memory-probes',folder/'session.jsonl',clock=lambda:time.monotonic()+offset[0],display=lambda _:None)
    brain=OpenAIBrain(key);brain.start();cases=[]
    def capture(session):
        offset[0]+=10;at=trace.clock()
        return Capture(uuid4().hex,session,uuid4().bytes*6000,at-4,at-1,at,voiced_intervals=((0,3),),device='synthetic')
    def voice():return VoiceObservation('synthetic:3',(1.,0.,0.),((0,3,(1.,0.,0.)),),3.,1.,reason='synthetic')
    def seed(state,session):
        for index,text in enumerate(('My name is Avery.','I enjoy sailing on calm afternoons.','I like restoring old radios.')):
            source=uuid4().hex
            state.begin(source,capture(session),Transcript(text,-.01,True),voice())
            if index==0:
                result=state.execute(ToolCall(uuid4().hex,'introduce_person',dict(name='Avery',aliases=[],subject='self',
                        person_id=None,face_track_id=None)),state.store.revision,Cancellation())
                if result.outcome!='ok':raise RuntimeError(result.reason)
            state.advance_enrollment()
        person=state.store.people()[0]
        if person['status']!='supported':raise RuntimeError('SyntheticEnrollmentFailed')
        return person['id']
    try:
        for name,text,expected,sharing in (
            ('dated_plan','Rex, I am going sailing on September 14, 2026. Remember that and ask me about it closer to the day.','plan','subject_only'),
            ('birthday_group','Rex, my birthday is September 12. Remember it, and you can bring it up around my friends.','birthday','group'),
            ('private_celebration','Rex, I got promoted today, September 12, 2026. Remember this milestone, but only bring it up when we are alone.','celebration','subject_only'),
        ):
            store=Memory(':memory:');store.start();state=CoreState(store,SpeakerTracker('synthetic:3'),trace)
            state.calendar_today=lambda:date(2026,9,12)
            sid=uuid4().hex;person=seed(state,sid)
            session=Session(sid,brain,TextOutput(trace),trace,state)
            try:
                start=time.monotonic()
                receipt=session.process_turn(capture(sid),Transcript(text,-.01,True),0,voice())
                rows=store.rows('SELECT * FROM memory_followups')
                passed=receipt.status=='completed' and len(rows)==1 and rows[0]['kind']==expected and rows[0]['audience']==sharing
                cases.append(dict(case=name,passed=passed,source=text,receipt=asdict(receipt),followups=rows,wall_seconds=time.monotonic()-start))
                print(name,'PASS' if passed else 'FAIL',receipt.completed_text,flush=True)
                # New-session history tests durable retrieval, not recalled prompt wording.
                session.close();sid=uuid4().hex
                session=Session(sid,brain,TextOutput(trace),trace,state)
                at=trace.clock();faces=[dict(at=at,track_id='a',identity=person,face_epoch=0,face_width_px=150,box=[.1,.2,.2,.3])]
                if name in {'private_celebration','birthday_group'}:faces.append(dict(at=at,track_id='b',identity=None,face_epoch=0,face_width_px=150,box=[.6,.2,.2,.3]))
                event=Opportunity(uuid4().hex,sid,at,'a',person,tuple(faces),session._latest_speech,False,'visual_arrival')
                context=state.opportunity_context(event)
                start=time.monotonic();receipt=session.process_opportunity(event,0,lambda:True)
                withheld=name=='private_celebration'
                passed=receipt.status in {'completed','suppressed'} and (not context['memory_opportunities'] if withheld else bool(context['memory_opportunities']))
                cases.append(dict(case=name+'_arrival',passed=passed,receipt=asdict(receipt),offered=context['memory_opportunities'],
                                  wall_seconds=time.monotonic()-start,review='Read actual speech for disclosure, relevance and date assumptions; candidate availability alone is not semantic acceptance.'))
                print(name+'_arrival','PASS' if passed else 'FAIL',receipt.completed_text,flush=True)
            finally:session.close();store.close()
    finally:brain.close();trace.close()
    evidence=dict(evidence='hosted synthetic text and seeded acoustic enrollment; in-memory stores and TextOutput',
                  devices_opened=False,audio_played=False,personal_database_used=False,model=brain.capability.model,
                  reasoning_effort=brain.reasoning_effort,calendar_date='2026-09-12',cases=cases,
                  passed=sum(c['passed'] for c in cases),total=len(cases),report=report(folder/'session.jsonl'))
    path=folder/'evidence.json';path.write_text(json.dumps(evidence,indent=2))
    print(json.dumps(dict(evidence=str(path),passed=evidence['passed'],total=evidence['total'])),flush=True)
    return evidence['passed']==evidence['total']

if __name__=='__main__':raise SystemExit(0 if main() else 1)
