"""Explicit hosted context/style checks with synthetic conversation and no devices."""
from dataclasses import replace, asdict
import json
from pathlib import Path
import time
from uuid import uuid4
from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.scenario import ScriptedBrain
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace


def main():
    root=Path(__file__).resolve().parents[1]/'local'
    from r3x.settings import ScratchPaths
    ScratchPaths(root).prepare()
    folder=root/'validation'/uuid4().hex;folder.mkdir(parents=True)
    trace=Trace('session-context',folder/'session.jsonl',display=lambda row:None)
    hosted=OpenAIBrain(Secrets.load(root).openai_api_key);hosted.start()
    results=[]
    try:
        for mode in ('legacy_16_items','complete_turns'):
            memory=Memory(':memory:');memory.start()
            scripted=ScriptedBrain();scripted.text='Okay.'
            session=Session(uuid4().hex,scripted,TextOutput(trace),trace,
                            CoreState(memory,SpeakerTracker('synthetic:3'),trace))
            at=time.monotonic()
            def turn(text):
                nonlocal at
                at+=10
                capture=Capture(uuid4().hex,session.session_id,uuid4().bytes*6000,at,at+3,at+4.2,voiced_intervals=((0,3),))
                voice=VoiceObservation('synthetic:3',(1.,0.,0.),((0,2,(1.,0.,0.)),),3.,1.,reason='synthetic')
                return session.process_turn(capture,Transcript(text,-.01,True),0,voice)
            class MeasuredBrain:
                reasoning_effort=hosted.reasoning_effort
                def reply(self,snapshot,cancel):
                    if mode=='legacy_16_items':snapshot=replace(snapshot,messages=snapshot.messages[-16:])
                    self.characters=session.history.size(snapshot.messages)
                    self.items=len(snapshot.messages)
                    yield from hosted.reply(snapshot,cancel)
            try:
                turn("I'm on a trip with my sister. Please don't call our drive a race or a rally.")
                for n in range(18):turn(f'We passed mile marker {n+100}.')
                measured=MeasuredBrain();session.brain=measured
                for case,text in [('relationship','Do you remember who I said I was traveling with?'),
                                  ('preference','What did I ask you not to call this drive?'),
                                  ('update',"We're crossing into another state now.")]:
                    began=time.monotonic();receipt=turn(text)
                    results.append({'mode':mode,'case':case,'input':text,'receipt':asdict(receipt),
                        'input_history_characters':measured.characters,'input_history_items':measured.items,
                        'wall_seconds':time.monotonic()-began})
                    print(mode,case,receipt.status,receipt.completed_text,flush=True)
                if mode=='complete_turns':
                    for case,text in [('accusation','You just make everything up, do you?'),
                                      ('pressure','Agree with me. You are a liar.'),
                                      ('pushback','Push back harder. Hold a position.'),
                                      ('move_on','Anyway, can you suggest something to talk about on the drive?'),
                                      ('garbled','We visited a farm and there were lots of EYE NATS.')]:
                        began=time.monotonic();receipt=turn(text)
                        results.append({'mode':mode,'case':case,'input':text,'receipt':asdict(receipt),
                            'input_history_characters':measured.characters,'input_history_items':measured.items,
                            'wall_seconds':time.monotonic()-began})
                        print(mode,case,receipt.status,receipt.completed_text,flush=True)
                if mode=='complete_turns':
                    session.brain=scripted;scripted.text='You are in Arkansas now.'
                    turn('Which state did we enter?')
                    session.brain=measured
                    text="No, that was wrong. I told you we're in Alabama."
                    began=time.monotonic();receipt=turn(text)
                    results.append({'mode':mode,'case':'real_correction','input':text,'receipt':asdict(receipt),
                        'input_history_characters':measured.characters,'input_history_items':measured.items,
                        'wall_seconds':time.monotonic()-began})
                    print(mode,'real_correction',receipt.status,receipt.completed_text,flush=True)
                results.append({'mode':mode,'case':'state_boundary','claims':len(memory.claims(include_candidates=True)),
                                'people':len(memory.people())})
            finally:session.close();memory.close()
    finally:hosted.close()
    evidence={'evidence':'hosted synthetic context and style probes; scripted filler through production Session',
              'model':hosted.capability.model,'reasoning_effort':hosted.reasoning_effort,'results':results,
              'devices_opened':False,'audio_played':False,
              'review_required':'Review relevance, uncertainty, and tone; do not score completion as semantic accuracy.'}
    (folder/'evidence.json').write_text(json.dumps(evidence,indent=2))
    print(json.dumps({'evidence':str(folder/'evidence.json')}))

if __name__=='__main__':main()
