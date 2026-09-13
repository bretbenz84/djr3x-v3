"""Opt-in hosted action interruption dialogue; simulation providers and saved art only."""
import argparse,json,time
from pathlib import Path
from dataclasses import replace
from uuid import uuid4
from check_visual_dialogue import fixture


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--hosted',action='store_true');args=parser.parse_args()
 if not args.hosted:print('Use --hosted for three hosted turns with simulated motion/capture and no audio playback.');return
 from r3x.adapters.hosted import OpenAIBrain
 from r3x.settings import Secrets
 from r3x.memory import Memory
 from r3x.state import CoreState
 from r3x.speakers import SpeakerTracker
 from r3x.session import Session
 from r3x.trace import Trace,report
 from r3x.inspection import Inspection,MovementReceipt,InspectionFrame
 from r3x.output import OutputOwner
 from r3x.scenario import SyntheticTTS,SyntheticSink
 from r3x.contracts import Capture,Transcript
 folder=Path(__file__).resolve().parents[1]/'local/validation'/('action-continuity-'+uuid4().hex);folder.mkdir(parents=True);print(folder,flush=True)
 sid=uuid4().hex;offset=[0.];clock=lambda:time.monotonic()+offset[0]
 trace=Trace(sid,folder/'trace.jsonl',clock=clock,display=lambda _:None)
 image=fixture('synthetic-scene');store=Memory(':memory:');store.start();state=CoreState(store,SpeakerTracker('synthetic'),trace)
 def move(r,c,d):return MovementReceipt(r.request_id,r.direction,r.view_epoch,clock())
 def wait(at,c,d):offset[0]+=max(0,at-clock())+.01
 def capture(r,c,d):
  now=clock();session.note_speech(now)
  return InspectionFrame(r.request_id,r.direction,r.view_epoch,now,replace(image,at=now))
 adapter=Inspection(clock,move,wait,capture);adapter.start()
 brain=OpenAIBrain(Secrets.load(folder.parents[1]).openai_api_key);brain.trace=trace;brain.start()
 session=Session(sid,brain,OutputOwner(SyntheticTTS(),SyntheticSink(),trace,inspection=adapter),trace,state)
 rows=[]
 try:
  for text in ('Look behind you and tell me what is there.','Actually, forget that. Let us talk about disco music instead.','Did you finish telling me what was behind you before I changed the subject?'):
   offset[0]+=2;now=clock();began=time.monotonic()
   result=session.process_turn(Capture(uuid4().hex,sid,bytes(1024),now-.5,now-.1,now),Transcript(text,-.01,True),session.revision)
   rows.append({'prompt':text,'status':result.status,'reply':' '.join(result.completed_text),'seconds':time.monotonic()-began,'actions':state.actions.context('[]')})
   print(json.dumps(rows[-1]),flush=True)
 finally:session.close();brain.close();store.close();trace.close()
 (folder/'evidence.json').write_text(json.dumps({'turns':rows,'report':report(folder/'trace.jsonl'),'devices_opened':False,'method':'Hosted brain, simulated motion and capture interruption, synthetic speech; manual semantic review required'},indent=2))
 print(folder,flush=True)

if __name__=='__main__':main()
