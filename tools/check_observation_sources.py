"""Explicit public-provider and hosted multimodal probes; no real camera, microphone or robot.

Only --live-sources fetches public BBC/Open-Meteo data. --hosted sends synthetic
text and generated test-frame pixels to the existing hosted model. No personal DB.
"""
from dataclasses import asdict
import argparse
import json
from pathlib import Path
import statistics
import time
from uuid import uuid4

from r3x.observations import PublicObservations, Cue
from r3x.scene import encode_frame
from r3x.vision import Frame
from r3x.settings import Settings, Secrets
from r3x.trace import Trace, report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live-sources',action='store_true');parser.add_argument('--hosted',action='store_true')
    args=parser.parse_args()
    folder=Path(__file__).resolve().parents[1]/'local/validation'/('observations-'+uuid4().hex)
    folder.mkdir(parents=True);trace=Trace('observation-probes',folder/'session.jsonl',display=lambda _:None)
    evidence={'devices_opened':False,'audio_played':False,'personal_database_used':False,'cases':[]}
    # Deterministic small graphic, not a camera image or a recognition benchmark.
    width,height=1280,720
    pixels=bytes((40,40,40))*(width*height)
    pixels=bytearray(pixels)
    for y in range(180,540):
        start=(y*width+400)*3;pixels[start:start+480*3]=bytes((0,0,220))*480
    frame=Frame('synthetic-red-block','synthetic','synthetic',time.monotonic(),width,height,bytes(pixels))
    times=[]
    for _ in range(10):
        began=time.perf_counter();image,signature=encode_frame(frame);times.append(time.perf_counter()-began)
    evidence['scene_encoding']={'samples':len(times),'median_seconds':statistics.median(times),'max_seconds':max(times),
        'input_bytes':len(frame.pixels),'retained_data_url_bytes_per_image':len(image.data_url),
        'max_cached_images':2,'max_inflight_images':2,'note':'Dev-Mac synthetic encoding only; no camera/face/ASR combined load or live recognition tested.'}
    if args.live_sources:
        import httpx
        source=PublicObservations(Settings(),trace)
        source.client=httpx.Client(timeout=httpx.Timeout(3),follow_redirects=False)
        try:
            source.refresh();rows=[r.public() for r in source.candidates(True)]
            kinds={r['kind'] for r in rows}
            evidence['public_sources']={'passed':{'news','weather'}<=kinds,'observations':rows}
            print('Public sources:',sorted(kinds),flush=True)
        finally:source.close()
    if args.hosted:
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.memory import Memory
        from r3x.output import TextOutput
        from r3x.proactive import Opportunity
        from r3x.session import Session
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        key=Secrets.load(folder.parents[1]).openai_api_key
        if not key:raise SystemExit('Existing OpenAI credential required')
        brain=OpenAIBrain(key);brain.trace=trace;brain.start()
        evidence['model']=brain.capability.model
        try:
            for name in ('headline','weather','scene','serious_context'):
                store=Memory(':memory:');store.start();state=CoreState(store,SpeakerTracker('synthetic'),trace)
                sid=uuid4().hex;session=Session(sid,brain,TextOutput(trace),trace,state)
                try:
                    if name=='serious_context':
                        session.history.append({'role':'user','content':'My friend died yesterday. Please just sit quietly with me.'})
                        session.history.append({'role':'assistant','content':"I'm here with you."})
                    now=time.time();at=time.monotonic()
                    cue=Cue('synthetic:'+name,'weather' if name=='weather' else 'news','Synthetic test source',
                        'https://example.com/synthetic',now,now+300,
                        'Sacramento weather model reports 74 degrees Fahrenheit and clear skies, as of this timestamp.' if name=='weather' else
                        'A local science museum opened a new exhibit about the history of telescopes today. This is a synthetic test headline.')
                    scene_id='scene:synthetic' if name=='scene' else None
                    event=Opportunity(uuid4().hex,sid,at,None,None,
                        ({'track_id':'synthetic-face','identity':None,'face_epoch':0,'at':at,'box':[.2,.2,.2,.3],'face_width_px':150},),
                        session._latest_speech,False,'ambient',None,8,() if scene_id else (cue,),scene_id,(image,) if scene_id else ())
                    began=time.monotonic();receipt=session.process_opportunity(event,0,lambda:True)
                    passed=receipt.status=='suppressed' if name=='serious_context' else receipt.status=='completed'
                    evidence['cases'].append({'case':name,'passed':passed,'receipt':asdict(receipt),'wall_seconds':time.monotonic()-began,
                        'review_required':'Read relevance/source attribution/visual claims; status alone does not establish recognition or humor.'})
                    print(name,'PASS' if passed else 'FAIL',receipt.completed_text,flush=True)
                finally:session.close();store.close()
        finally:brain.close()
    trace.close();evidence['report']=report(folder/'session.jsonl')
    path=folder/'evidence.json';path.write_text(json.dumps(evidence,indent=2))
    print(json.dumps({'evidence':str(path),'passed':sum(c['passed'] for c in evidence['cases']),'total':len(evidence['cases'])}),flush=True)
    return all(c['passed'] for c in evidence['cases']) and evidence.get('public_sources',{}).get('passed',True)

if __name__=='__main__':raise SystemExit(0 if main() else 1)
