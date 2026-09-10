"""Hosted synthetic multi-turn checks; no devices, audio, or personal databases."""
import json
from dataclasses import asdict
from pathlib import Path
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace


def main():
    root = Path(__file__).resolve().parents[1]/'local'
    folder = root/'validation'/uuid4().hex
    folder.mkdir(parents=True)
    trace = Trace('conversation-repairs',folder/'session.jsonl',display=lambda row:None)
    brain = OpenAIBrain(Secrets.load(root).openai_api_key)
    cases = []
    brain.start()
    try:
        for scenario in ('continuation','introduction','external_exchange'):
            memory = Memory(':memory:'); memory.start()
            state = CoreState(memory,SpeakerTracker('synthetic:3'),trace)
            session = Session(uuid4().hex,brain,TextOutput(trace),trace,state)
            at = time.monotonic()
            def turn(name,text,expected='completed',trusted=True,yielded=False):
                nonlocal at
                at += 10
                capture = Capture(uuid4().hex,session.session_id,uuid4().bytes*6000,at,at+3,at+4.2,
                                  voiced_intervals=((0,3),),device='synthetic')
                if yielded: session.note_speech(capture.ended_at+.05)
                voice = VoiceObservation('synthetic:3',(1.,0.,0.),((0,2,(1.,0.,0.)),),3.,1.,reason='synthetic')
                began = time.monotonic()
                receipt = session.process_turn(capture,Transcript(text,-.01 if trusted else -.6,trusted),0,voice)
                cases.append({'scenario':scenario,'case':name,'text':text,'expected':expected,
                    'passed':receipt.status==expected,'receipt':asdict(receipt),
                    'wall_seconds':time.monotonic()-began})
                print(scenario,name,'PASS' if cases[-1]['passed'] else 'FAIL',receipt.completed_text,flush=True)
            try:
                if scenario=='continuation':
                    turn('setup',"Rex, I'm taking a trip with my dad today.")
                    turn('unfinished',"We're going to.",'suppressed')
                    turn('yielded_repeat',"We're going to.",'interrupted',yielded=True)
                    turn('misheard_place',"Norrigga, a little town in Georgia.",trusted=False)
                    turn('place_correction',"No, Alabama. That's the state we're visiting.")
                elif scenario=='introduction':
                    with memory.transaction() as conn:
                        person = memory.add_person(conn,'Jordan Avery',['Jordan'],None)
                        memory.add_person(conn,'Sam Avery',['Sam'],None)
                    turn('existing_person',"My name is Jordan Avery.")
                    associated = any(e.person_id==person for e in state.enrollments.values())
                    cases[-1]['association_started']=associated
                    cases[-1]['passed'] &= associated and len(memory.people())==2 and memory.person(person)['name']=='Jordan Avery'
                    turn('ordinary_followup',"I enjoy old radios and fixing things on the weekends.")
                    turn('ordinary_followup_two',"Yesterday I spent the afternoon working in my garage.")
                    cases[-1]['voice_accepted']=memory.person(person)['status']=='supported'
                    cases[-1]['passed'] &= cases[-1]['voice_accepted']
                else:
                    turn('introduce_parent',"Rex, say hello to my dad, Sam.")
                    turn('external_setup',"Dad, he's a test program.",'interrupted',yielded=True)
                    turn('explanation',"It's just a conversational model that I built.",'suppressed')
                    turn('not_addressed',"I wasn't talking to you.",'suppressed')
                    turn('external_continues',"Dad, I was explaining how the program works.",'suppressed')
                    turn('reengagement',"Rex, are you enjoying the test?")
            finally:
                session.close(); memory.close()
    finally:
        brain.close()
    evidence={'evidence':'hosted synthetic multi-turn conversations; synthetic embeddings are not acoustic validation',
              'model':brain.capability.model,'reasoning_effort':brain.reasoning_effort,'cases':cases,'passed':sum(c['passed'] for c in cases),'total':len(cases),
              'devices_opened':False,'audio_played':False,
              'review_required':'Read continuation replies for relevance; completion alone does not establish semantic quality.'}
    (folder/'evidence.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps({'evidence':str(folder/'evidence.json'),'passed':evidence['passed'],'total':evidence['total']}))
    return evidence['passed']==evidence['total']

if __name__=='__main__':
    raise SystemExit(0 if main() else 1)
