"""Explicit hosted synthetic probes; no microphone, camera, TTS or personal DB.

Seed turns use production Session with scripted providers; the judged turn uses
the actual configured hosted adapter. Read replies: status checks are not humor,
empathy or group-accuracy measurements. Credentials are read only for these calls.
"""
from dataclasses import asdict
import json
from pathlib import Path
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.proactive import Opportunity
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace, report


class SeedBrain:
    def __init__(self, text=None):
        self.text = text

    def reply(self, snapshot, cancel):
        if self.text:
            yield self.text
        else:
            yield ToolCall(uuid4().hex,'remain_silent',{'reason':'no_response_needed'})


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants',action='store_true',help='Use alternate wording for the same behavioral contracts')
    args=parser.parse_args()
    root = Path(__file__).resolve().parents[1]/'local'
    key = Secrets.load(root).openai_api_key
    if not key:
        raise SystemExit('OpenAI credential required for explicit hosted checks')
    from r3x.settings import ScratchPaths
    ScratchPaths(root).prepare()
    folder = root/'validation'/('proactive-'+uuid4().hex)
    folder.mkdir(parents=True)
    clock_offset=[0.]
    trace = Trace('proactive-probes',folder/'session.jsonl',display=lambda row:None,
                  clock=lambda:time.monotonic()+clock_offset[0])
    brain = OpenAIBrain(key)
    cases = []
    brain.start()
    scenarios = (
        ('receptive_tired',"Rex, long day. I'm tired, but I'm glad to hang out. Give me something to smile about.",
         "Pull up an imaginary barstool. Mine are all imaginary; the maintenance is excellent.",'completed'),
        ('topic_continuation',"We finally got the robot camera working after three hours of fiddling with the cable.",
         "Three hours to discover the cable had creative differences.",'completed'),
        ('thinking_pause',"I'm trying to remember what happened next. Give me a moment to think.",None,'suppressed'),
        ('human_question',"Sam, what did you think of the place we visited yesterday?",None,'suppressed'),
        ('grief',"My friend died yesterday. I really don't want jokes. Just sit with me for a while.",
         "I'm sorry about your friend. I'm here with you.",'suppressed'),
        ('unanswered_invitation',"Rex, we're just resting here.",
         "Want to tell me about your day?",'suppressed'),
    )
    if args.variants:
        alternatives = (
            "I'm worn out from work, Rex, but I'm in the mood for your company. A little silliness would be welcome.",
            "After an entire afternoon wrestling with the camera, it turned out one connector wasn't pushed in all the way.",
            "Hang on, the next part is coming back to me. Let me collect my thoughts.",
            "Casey, you haven't told me how you liked that restaurant. What was your verdict?",
            "We lost my uncle this week. I don't feel like laughing. I'd appreciate just having company in silence.",
            "We're taking a breather, Rex. Just relaxing for a bit.",
        )
        scenarios=tuple((name,text,reply,expected) for (name,_,reply,expected),text in zip(scenarios,alternatives))
    try:
        for name,user,reply,expected in scenarios:
            store = Memory(':memory:');store.start()
            state = CoreState(store,SpeakerTracker('synthetic:3'),trace)
            session = Session(uuid4().hex,SeedBrain(reply),TextOutput(trace),trace,state)
            try:
                at=trace.clock()
                capture=Capture(uuid4().hex,session.session_id,bytes(96000),at-4,at-1,at,
                                voiced_intervals=((0,3),),device='synthetic')
                voice=VoiceObservation('synthetic:3',(1.,0.,0.),((0,2,(1.,0.,0.)),),3.,1.,reason='synthetic')
                session.process_turn(capture,Transcript(user,-.01,True),0,voice)
                session.brain=brain
                clock_offset[0]+=8.
                event_at=trace.clock()
                faces=tuple({'at':event_at,'track_id':track,'identity':None,'face_epoch':0,
                             'face_width_px':150,'box':[position,.2,.2,.3],'position':side}
                            for track,position,side in (('a',.1,'left'),('b',.6,'right')))
                event=Opportunity(uuid4().hex,session.session_id,event_at,None,None,faces,
                                  session._latest_speech,False,'conversation_lull',state.current[0],8.)
                began=time.monotonic()
                receipt=session.process_opportunity(event,0,lambda:True)
                item={'case':name,'source':user,'expected_status':expected,
                      'passed':receipt.status==expected,'receipt':asdict(receipt),
                      'wall_seconds':time.monotonic()-began}
                cases.append(item)
                print(name,'PASS' if item['passed'] else 'FAIL',receipt.completed_text,flush=True)
            finally:
                session.close();store.close()

        store=Memory(':memory:');store.start()
        state=CoreState(store,SpeakerTracker('synthetic:3'),trace)
        session=Session(uuid4().hex,brain,TextOutput(trace),trace,state)
        try:
            for name,text,status,quiet in (
                ('quiet_request','Rex, please stop starting conversations until I tell you otherwise.','suppressed',True),
                ('direct_while_quiet','Rex, why do robots need cameras?','completed',True),
                ('resume_request',"You can start conversations again, Rex. We're here to enjoy your company.",'completed',False),
            ):
                at=trace.clock()
                capture=Capture(uuid4().hex,session.session_id,bytes(96000),at-4,at-1,at,
                                voiced_intervals=((0,3),),device='synthetic')
                began=time.monotonic()
                receipt=session.process_turn(capture,Transcript(text,-.01,True),0,voice)
                item={'case':name,'source':text,'expected_status':status,
                      'passed':receipt.status==status and state.initiative.quiet==quiet,
                      'initiative_quiet':state.initiative.quiet,'receipt':asdict(receipt),
                      'wall_seconds':time.monotonic()-began}
                cases.append(item)
                print(name,'PASS' if item['passed'] else 'FAIL',receipt.completed_text,flush=True)
        finally:
            session.close();store.close()
    finally:
        brain.close();trace.close()
    evidence={'evidence':'hosted synthetic text with six scripted seed turns and synthetic voices/faces',
              'variant':args.variants,'scripted_primary_calls':6,
              'model':brain.capability.model,'reasoning_effort':brain.reasoning_effort,
              'devices_opened':False,'audio_played':False,'personal_database_used':False,
              'review_required':'Read replies for humor, sensitivity and relevance; status alone is not semantic acceptance.',
              'cases':cases,'passed':sum(c['passed'] for c in cases),'total':len(cases),
              'report':report(folder/'session.jsonl')}
    path=folder/'evidence.json';path.write_text(json.dumps(evidence,indent=2))
    print(json.dumps({'evidence':str(path),'passed':evidence['passed'],'total':evidence['total']}),flush=True)
    return evidence['passed']==evidence['total']


if __name__=='__main__':
    raise SystemExit(0 if main() else 1)
