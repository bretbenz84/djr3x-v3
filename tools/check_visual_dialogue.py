"""Explicit hosted multi-turn visual evaluation on saved synthetic art; no devices."""
import argparse,base64,json,time,statistics
from pathlib import Path
from uuid import uuid4
from dataclasses import replace
from r3x.scene import encode_frame
from r3x.vision import Frame


def fixture(name,blue=False,dim=False):
    width=480;height=360;pixels=bytearray()
    for y in range(height):
        for x in range(width):
            rgb=(235,235,225)
            if y>285:rgb=(120,85,55)
            if 95<x<230 and 190<y<285:rgb=(30,125,175)
            if ((x-163)/55)**2+((y-133)/67)**2<1:rgb=(225,178,130)
            if 106<x<220 and 65<y<107:rgb=(85,45,20)
            if ((x-142)**2+(y-132)**2<16) or ((x-182)**2+(y-132)**2<16):rgb=(20,20,20)
            if 143<x<182 and 164<y<168:rgb=(70,35,25)
            if 325<x<378 and 225<y<284:rgb=(25,75,210) if blue else (215,30,30)
            if 376<x<392 and 239<y<268:rgb=(25,75,210) if blue else (215,30,30)
            if dim:rgb=tuple(int(v*.8) for v in rgb)
            pixels.extend(reversed(rgb))
    return encode_frame(Frame(name,'fixture','visual-eval',time.monotonic(),width,height,bytes(pixels)))[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--hosted',action='store_true');args=parser.parse_args()
    folder=Path(__file__).resolve().parents[1]/'local/validation'/('visual-dialogue-'+uuid4().hex);folder.mkdir(parents=True)
    images={name:fixture(name,blue=name=='changed',dim=name=='dim') for name in ('baseline','dim','changed')}
    for name,image in images.items():(folder/(name+'.png')).write_bytes(base64.b64decode(image.data_url.split(',')[1]))
    if not args.hosted:print(folder);return
    from r3x.adapters.hosted import OpenAIBrain
    from r3x.settings import Secrets
    from r3x.inspection import CurrentView
    from r3x.memory import Memory
    from r3x.speakers import SpeakerTracker
    from r3x.state import CoreState
    from r3x.session import Session
    from r3x.output import OutputOwner
    from r3x.scenario import SyntheticSink,SyntheticTTS
    from r3x.trace import Trace,report
    from r3x.contracts import Capture,Transcript
    class SavedScene:
        name='baseline'
        def snapshot(self,*args):return self.name,(replace(images[self.name],at=time.monotonic()),)
    class SavedView(CurrentView):
        def execute(self,*args):
            result,frames=super().execute(*args)
            return {**result,'medium':'saved_synthetic_image','reason':'Saved cartoon fixture; no camera or movement.'},frames
    trace=Trace('visual-eval',folder/'trace.jsonl',display=lambda _:None)
    brain=OpenAIBrain(Secrets.load(folder.parents[1]).openai_api_key);brain.trace=trace
    store=Memory(':memory:');store.start();state=CoreState(store,SpeakerTracker('synthetic'),trace)
    scene=SavedScene();view=SavedView(scene);view.start()
    output=OutputOwner(SyntheticTTS(),SyntheticSink(),trace,inspection=view)
    session=Session('visual-eval',brain,output,trace,state)
    evidence={'manual_semantic_review_required':True,'devices_opened':False,'images':'saved synthetic cartoon fixtures','audio':'synthetic sink; no playback',
              'model':brain.capability.model,'cases':[]}
    brain.start()
    try:
        for name,prompt in [
            ('baseline','Look at the current view and describe the illustration, including the hair and object. Keep useful details for later.'),
            ('baseline','Nice. Let us talk about space travel instead.'),
            ('baseline','Look again. Does anything actually appear different?'),
            ('dim','Look at this view again. Has the hairstyle changed, or could it just be lighting?'),
            ('changed','Look again and tell me what changed on the table.'),
            ('changed','That is a toy mug, not a drinking cup. Please do not rely on an incorrect description.'),
            ('changed','What color was the hair in the earlier illustration? No need to inspect again.')]:
            scene.name=name;now=time.monotonic();began=now
            result=session.process_turn(Capture(uuid4().hex,'visual-eval',bytes(1024),now-.5,now-.1,now),
                Transcript(prompt,-.01,True),session.revision)
            row={'fixture':name,'prompt':prompt,'status':result.status,'reply':' '.join(result.completed_text),
                 'seconds':time.monotonic()-began}
            evidence['cases'].append(row);print(json.dumps(row),flush=True)
        evidence['visual_records']=store.rows('SELECT description,uncertainty,status,spoken FROM visual_observations')
    finally:session.close();brain.close();store.close();trace.close()
    rows=[json.loads(line) for line in (folder/'trace.jsonl').read_text().splitlines()]
    evidence['report']=report(folder/'trace.jsonl')
    evidence['tools']=[r['name'] for r in rows if r['event']=='tool_proposed']
    evidence['inspection_count']=sum(r['event']=='inspection_result' for r in rows)
    evidence['timing']={'median':statistics.median(r['seconds'] for r in evidence['cases']),
                        'max':max(r['seconds'] for r in evidence['cases'])}
    (folder/'evidence.json').write_text(json.dumps(evidence,indent=2));print(folder)


if __name__=='__main__':main()
