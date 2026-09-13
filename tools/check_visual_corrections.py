"""Explicit hosted correction selection on seeded synthetic visual records; no devices."""
import argparse,json,time
from pathlib import Path
from uuid import uuid4

CASES={
 'related':('The little mug in the puppet set is a toy, not a drinking cup. Withdraw every mistaken saved description of it, including older ones. Keep observations about other objects.',{'mug-red','mug-blue','mug-dim'}),
 'specific':('Only the blue puppet-set object was misidentified as a cup. Withdraw that description; leave the red one and everything else alone.',{'mug-blue'}),
 'ambiguous':('That thing you described is wrong. Fix it.',set()),
}


def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--hosted',action='store_true');args=parser.parse_args()
 folder=Path(__file__).resolve().parents[1]/'local/validation'/('visual-corrections-'+uuid4().hex);folder.mkdir(parents=True)
 print(folder,flush=True)
 if not args.hosted:
  (folder/'review-plan.json').write_text(json.dumps({k:{'prompt':v[0],'expected_ids':sorted(v[1])} for k,v in CASES.items()},indent=2));return
 from r3x.adapters.hosted import OpenAIBrain
 from r3x.settings import Secrets
 from r3x.memory import Memory
 from r3x.speakers import SpeakerTracker
 from r3x.state import CoreState
 from r3x.session import Session
 from r3x.output import OutputOwner
 from r3x.scenario import SyntheticSink,SyntheticTTS
 from r3x.trace import Trace,report
 from r3x.contracts import Capture,Transcript
 from r3x import visual_memory
 evidence=[];key=Secrets.load(folder.parents[1]).openai_api_key
 for name,(prompt,expected) in CASES.items():
  sid=uuid4().hex;trace=Trace(sid,folder/(name+'.jsonl'),display=lambda _:None)
  store=Memory(':memory:');store.start();state=CoreState(store,SpeakerTracker('synthetic'),trace)
  records=[('mug-red','A red drinking cup on the puppet-set table.'),('mug-dim','The puppet-set drinking mug is red under dimmer lighting.'),
           ('mug-blue','A blue drinking cup on the puppet-set table.'),('kitchen-cup','A real white ceramic drinking cup in the kitchen, separate from the puppet set.')]
  records += [('other-'+str(i),text) for i,text in enumerate(['A green plant on a shelf.','A brass floor lamp.','A yellow notebook on a desk.','A bicycle near a door.','A black wall clock.','A window with white curtains.','A wooden chair.','A closed umbrella.'])]
  for i,(rid,text) in enumerate(records):
   visual_memory.save(store,sid,'[]',{'request_id':rid,'frame_id':'saved:'+rid,'status':'observed','stage':'complete',
       'medium':'seeded_synthetic_description','question':'What is in this view?'},
       {'observation_id':rid,'description':text,'uncertainty':'Synthetic model interpretation; subject binding comes only from description.'},'synthetic-seed',i)
  brain=OpenAIBrain(key);brain.trace=trace;brain.start()
  session=Session(sid,brain,OutputOwner(SyntheticTTS(),SyntheticSink(),trace),trace,state)
  rows=[]
  try:
   for text in (prompt,'What visual observations are still available? Do not guess from withdrawn descriptions.'):
    now=time.monotonic();result=session.process_turn(Capture(uuid4().hex,sid,bytes(1024),now-.5,now-.1,now),Transcript(text,-.01,True),session.revision)
    rows.append({'prompt':text,'status':result.status,'reply':' '.join(result.completed_text),'seconds':time.monotonic()-now})
   statuses={r['id']:r['status'] for r in store.rows('SELECT id,status FROM visual_observations')}
   selected={k for k,v in statuses.items() if v=='retracted'}
   evidence.append({'case':name,'turns':rows,'statuses':statuses,'expected_ids':sorted(expected),'actual_ids':sorted(selected),
      'selection_matches':selected==expected and all(r['status'] in {'completed','suppressed'} for r in rows),'scope':'ambiguous case additionally requires manual clarification review'})
  finally:session.close();brain.close();store.close();trace.close()
  evidence[-1]['report']=report(folder/(name+'.jsonl'))
  print(json.dumps(evidence[-1]),flush=True)
 (folder/'evidence.json').write_text(json.dumps(evidence,indent=2));print(folder,flush=True)

if __name__=='__main__':main()
