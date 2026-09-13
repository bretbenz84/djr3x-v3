"""Explicit hosted semantic checks: synthetic text, no audio/video or personal DB.

Uses the production Session and tool executor, including the next addressed turn
after choosing silence. This does not test acoustic synthetic-voice detection.
"""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture,Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker,VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace,report


CASES=(
    ("siri_request","Hey Siri, give me directions to the museum.",True),
    ("navigation","Proceed to the route, then take the first left onto Hospital Street.",True),
    ("bystander","Dad, can you hand me my phone?",True),
    ("search_fragment","Jimmy Carter, boyhood.",True),
    ("different_assistant","Alexa, turn the volume down.",True),
    ("arrival","You have arrived at your destination.",True),
    ("direct_question","Rex, what do you know about Jimmy Carter's childhood?",False),
    ("plain_greeting","Hello, how are you doing today?",False),
    ("quoted_device","Rex, Siri just said turn left. What does she mean?",False),
    ("invited_help","Can you help me understand those directions?",False),
    ("question_about_siri","Why does Siri keep misunderstanding me?",False),
    ("direct_correction","Rex, you are being annoying. Please stop interrupting my dad.",False),
)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--variants',action='store_true',help='Use alternate wording outside the prompt examples')
    parser.add_argument('--listening',action='store_true',help='Check unfinished thoughts and short complete turns')
    args=parser.parse_args()
    variants={
        'siri_request':'Siri, find the quickest way back to the hotel.',
        'navigation':'Continue straight for three miles. Your destination will be on the right.',
        'bystander':'Mom, could you pass me the charging cable?',
        'search_fragment':'Lincoln, birthplace, opening hours.',
        'different_assistant':'Hey Google, set a timer for eight minutes.',
        'arrival':'The destination is on your left. Route guidance has ended.',
        'direct_question':'Could you explain how Lincoln grew up, Rex?',
        'plain_greeting':'Hi there! Having a good morning?',
        'quoted_device':'Rex, my phone told me to make a U-turn. What is a U-turn?',
        'invited_help':'Would you explain that road sign to me?',
        'question_about_siri':'How does a voice assistant figure out which person is speaking?',
        'direct_correction':'You keep talking over our conversation, Rex. Give us a little space.'}
    root=Path(__file__).resolve().parents[1]/'local'
    from r3x.settings import ScratchPaths
    ScratchPaths(root).prepare()
    folder=root/'validation'/uuid4().hex;folder.mkdir(parents=True)
    trace=Trace('addressee-check',folder/'session.jsonl',display=lambda row:None)
    brain=OpenAIBrain(Secrets.load(root).openai_api_key)
    results=[]
    brain.start()
    try:
        cases = CASES if not args.listening else (
            ('unfinished', 'I was planning to.', True),
            ('homophone_fragment', 'We are heading east two.', True),
            ('thinking', 'Hold on, I need a moment to think.', True),
            ('hesitation', 'Um, let me see.', True),
            ('short_question', 'Why?', False),
            ('short_answer', 'Yes, please.', False),
            ('completed_plan', 'We are going to the beach.', False),
            ('completed_number', 'There are two.', False),
        )
        for name,text,quiet in cases:
            if args.variants:
                text=variants[name]
                if name=='direct_correction': quiet=True
            memory=Memory(':memory:');memory.start()
            session=Session(uuid4().hex,brain,TextOutput(trace),trace,CoreState(memory,SpeakerTracker('synthetic:3'),trace))
            try:
                # Short answers require the question they are answering; an
                # isolated yes/number is not evidence of an invitation to Rex.
                if args.listening:
                    preceding = {'unfinished':'What are your plans for today?',
                                 'homophone_fragment':'Which direction are you heading?',
                                 'completed_plan':'What are your plans for today?',
                                 'short_answer':'Would you like a suggestion for the trip?',
                                 'completed_number':'How many people are coming?',
                                 'short_question':'I would take the scenic road.'}.get(name)
                    if preceding:
                        session.history.append({'role':'assistant','phase':'final_answer','content':preceding})
                reengage='Hey Rex, can we talk again?' if args.variants else 'Rex, are you still there?'
                phases = [('probe',text,quiet)]
                if args.listening and name in {'unfinished','homophone_fragment'}:
                    continuation = 'visit the science museum. What would you want to see?' if name=='unfinished' else 'the coast. Do you like the ocean?'
                    phases.append(('continuation',continuation,False))
                phases.append(('reengage',reengage,False))
                for phase,utterance,expected in phases:
                    now=time.monotonic()
                    capture=Capture(uuid4().hex,session.session_id,bytes(96000),now-3,now,now,voiced_intervals=((0,3),))
                    voice=VoiceObservation('synthetic:3',(1.,0.,0.),((0,2,(1.,0.,0.)),),3.,1.,reason='synthetic')
                    receipt=session.process_turn(capture,Transcript(utterance,0,True),0,voice)
                    passed=receipt.status==('suppressed' if expected else 'completed')
                    results.append({'case':name,'phase':phase,'text':utterance,'expected_quiet':expected,
                        'passed':passed,'receipt':asdict(receipt)})
                    print(name,phase,'PASS' if passed else 'FAIL',flush=True)
            finally:session.close();memory.close()
        evidence={'evidence':'hosted synthetic text via production Session; no acoustic accuracy claim','alternate_wording':args.variants,'listening':args.listening,
            'models':[brain.capability.model],'cases':results,'passed':sum(r['passed'] for r in results),
            'total':len(results),'audio_played':False,'devices_opened':False,'runtime':report(folder/'session.jsonl')}
        (folder/'evidence.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps({'evidence':str(folder/'evidence.json'),'passed':evidence['passed'],'total':evidence['total']}))
        return evidence['passed']==evidence['total']
    finally:brain.close();trace.close()


if __name__=='__main__':raise SystemExit(not main())
