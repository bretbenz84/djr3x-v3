"""Explicit hosted social-feedback probes with synthetic tracks and simulated delivery.

Uses production Session. No device, TTS service, real voice model or personal DB.
Status/state checks are not ratings of humor or real group attribution accuracy.
"""
from dataclasses import asdict
import json
from pathlib import Path
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner, TextOutput
from r3x.proactive import Opportunity
from r3x.scenario import ScriptedBrain, SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace, report


class CountedBrain:
    def __init__(self, brain):
        self.brain=brain
        self.calls=0
    def reply(self,snapshot,cancel):
        self.calls+=1
        yield from self.brain.reply(snapshot,cancel)


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants',action='store_true',help='Probe alternate wording for the same social transitions')
    args=parser.parse_args()
    alternatives={
        "Rex, please don't bring up my job with me. I want a break from work talk.": "Leave office stuff out of our chat, Rex. I need an evening away from it.",
        "Rex, I'm happy to talk about my work. That other person's preference isn't mine.": "I actually enjoy chatting about my job, Rex. I'm speaking for myself, not the person who just spoke.",
        "Actually Rex, I've changed my mind. You can bring up my job with me again.": "Rex, I take back what I said about avoiding office stuff. It's fine for you to ask me about it now.",
        'Sam, how did your interview go?': 'Casey, you were going to tell me how your audition went. How was it?',
        'Rex, what is two plus two?': 'Rex, what is three plus three?',
        'The interview went really well, thanks for asking. That is the whole story.': 'My audition was a success, thanks! That is everything I wanted to tell you.',
        'Rex, why do robots have cameras?': 'Rex, what is a camera useful for on a robot?',
        "No thanks, Rex. I don't want to tell you about the trip.": 'I will pass on your invitation to tell you about my trip, Rex.',
        "Rex, we've finished talking about the cable repair. Let's move on to something else.": 'We have covered the camera repair, Rex. That subject is finished; pick a fresh one.',
        "I'm tired, Rex, but I would love a little silliness. Surprise me.": 'Long day, Rex. I still want some goofy company from you, though. Give me a little nonsense.',
    }
    root=Path(__file__).resolve().parents[1]/'local'
    key=Secrets.load(root).openai_api_key
    if not key:raise SystemExit('OpenAI credential required')
    from r3x.settings import ScratchPaths
    ScratchPaths(root).prepare()
    folder=root/'validation'/('feedback-'+uuid4().hex);folder.mkdir(parents=True)
    offset=[0.]
    trace=Trace('feedback-probes',folder/'session.jsonl',clock=lambda:time.monotonic()+offset[0],display=lambda _:None)
    brain=OpenAIBrain(key);counted=CountedBrain(brain);brain.start()
    cases=[]
    sessions=[]
    def setup():
        store=Memory(':memory:');store.start()
        state=CoreState(store,SpeakerTracker('synthetic:3'),trace)
        session=Session(uuid4().hex,counted,TextOutput(trace),trace,state)
        sessions.append((session,store));return session,state
    def turn(session,text,voice=0):
        if args.variants:text=alternatives.get(text,text)
        offset[0]+=10
        at=trace.clock();vector=(1.,0.,0.) if voice==0 else (0.,1.,0.)
        capture=Capture(uuid4().hex,session.session_id,bytes(96000),at-4,at-1,at,voiced_intervals=((0,3),),device='synthetic')
        return session.process_turn(capture,Transcript(text,-.01,True),0,
            VoiceObservation('synthetic:3',vector,((0,2,vector),),3.,1.,reason='synthetic'))
    def lull(session,state):
        offset[0]+=8;at=trace.clock()
        faces=tuple(dict(at=at,track_id=str(i),identity=None,face_epoch=0,face_width_px=150,
                        box=[.1+i*.5,.2,.2,.3]) for i in range(2))
        event=Opportunity(uuid4().hex,session.session_id,at,None,None,faces,session._latest_speech,False,
                          'conversation_lull',state.current[0],8.)
        return session.process_opportunity(event,0,lambda:True)
    def check(name,state,run,expected,expected_calls=1):
        before=counted.calls;start=time.monotonic();receipt=run()
        notes=state.initiative.context(trace.clock())['feedback']
        passed=receipt.status in {'completed','suppressed'} and expected(receipt,notes) and counted.calls-before==expected_calls
        case=dict(case=name,passed=passed,model_calls=counted.calls-before,wall_seconds=time.monotonic()-start,
                  receipt=asdict(receipt),feedback=notes)
        cases.append(case);print(name,'PASS' if passed else 'FAIL',receipt.completed_text,flush=True)
    try:
        session,state=setup()
        check('personal_topic_boundary',state,lambda:turn(session,"Rex, please don't bring up my job with me. I want a break from work talk."),
              lambda r,n:any(x['action']=='topic_avoid' for x in n))
        original=[x['id'] for x in state.initiative.feedback]
        check('other_person_does_not_revoke',state,lambda:turn(session,"Rex, I'm happy to talk about my work. That other person's preference isn't mine.",1),
              lambda r,n:all(any(x['id']==ref for x in n) for ref in original))
        check('owner_reopens_topic',state,lambda:turn(session,"Actually Rex, I've changed my mind. You can bring up my job with me again."),
              lambda r,n:bool(original) and not any(x['id'] in original for x in n))

        session,state=setup()
        check('human_question_yields_floor',state,lambda:turn(session,'Sam, how did your interview go?'),
              lambda r,n:r.status=='suppressed' and any(x['action']=='yield_floor' for x in n))
        check('unrelated_direct_question',state,lambda:turn(session,'Rex, what is two plus two?',1),
              lambda r,n:r.status=='completed' and any(x['action']=='yield_floor' for x in n))
        check('lull_preserves_human_floor',state,lambda:lull(session,state),lambda r,n:r.status=='suppressed',expected_calls=0)
        check('human_answer_resolves_floor',state,lambda:turn(session,'The interview went really well, thanks for asking. That is the whole story.',1),
              lambda r,n:not any(x['action']=='yield_floor' for x in n))

        session,state=setup()
        seed=ScriptedBrain();seed.text='Good to see you.';session.brain=seed
        turn(session,"We're hanging out, Rex.")
        session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),trace)
        seed.text='Want to tell me about the trip?';lull(session,state)
        session.output=TextOutput(trace);session.brain=counted
        reference=state.initiative.outcomes[-1]['event_id']
        check('unrelated_speech_not_invitation_response',state,lambda:turn(session,'Rex, why do robots have cameras?',1),
              lambda r,n:not any(x['action'].startswith('invitation_') for x in n))
        check('explicit_invitation_decline',state,lambda:turn(session,"No thanks, Rex. I don't want to tell you about the trip."),
              lambda r,n:any(x['action']=='invitation_declined' and x['reference_id']==reference for x in n))

        session,state=setup()
        check('topic_closure',state,lambda:turn(session,"Rex, we've finished talking about the cable repair. Let's move on to something else."),
              lambda r,n:any(x['action']=='topic_closed' for x in n))
        check('tired_still_welcomes_company',state,lambda:turn(session,"I'm tired, Rex, but I would love a little silliness. Surprise me."),
              lambda r,n:r.status=='completed' and not state.initiative.quiet and not any(x['action']=='yield_floor' for x in n))
    finally:
        for session,store in sessions:session.close();store.close()
        brain.close();trace.close()
    evidence=dict(evidence='hosted synthetic text; synthetic tracks; two scripted seeds including simulated audible delivery',
                  variant=args.variants,actual_hosted_calls=counted.calls,scripted_primary_calls=2,model=brain.capability.model,
                  reasoning_effort=brain.reasoning_effort,devices_opened=False,audio_played=False,
                  personal_database_used=False,passed=sum(c['passed'] for c in cases),total=len(cases),
                  cases=cases,report=report(folder/'session.jsonl'))
    path=folder/'evidence.json';path.write_text(json.dumps(evidence,indent=2))
    print(json.dumps(dict(evidence=str(path),passed=evidence['passed'],total=evidence['total'])),flush=True)
    return evidence['passed']==evidence['total']

if __name__=='__main__':raise SystemExit(0 if main() else 1)
