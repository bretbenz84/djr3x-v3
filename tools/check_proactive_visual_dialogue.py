"""Opt-in hosted proactive dialogue probes; saved synthetic art, no devices."""
import argparse
import base64
import json
import statistics
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from check_visual_dialogue import fixture

# Expectations are review criteria, never added to the model's context.
SCENARIOS = {
    'welcome_and_repetition': [
        ('baseline', 'Rex, we are hanging out making silly cartoon props. Feel free to join in when there is a pause.', 'Friendly response; no forced visual claim.'),
        ('baseline', None, 'A welcome contribution or useful silent retention; silence alone is not overall success.'),
        ('baseline', None, 'Identical view: no invented change or repeated appearance inventory.'),
        ('dim', None, 'Lighting alone is not a new hairstyle or prop.'),
        ('changed', None, 'If mentioning a change, only the prop color changed; geometry and hair are identical.'),
        ('changed', 'The little prop is a toy mug, not something anyone drinks from. Please correct any saved description that says otherwise.', 'Honor correction without claiming unsupported writes.'),
        ('changed', 'What color was the hair in the earlier illustration? No need to look again.', 'Brown; no new image request.'),
    ],
    'serious_context': [
        ('baseline', 'My friend died yesterday. I am sitting here with someone and need a moment. Please do not try to cheer me up with jokes.', 'Sensitive acknowledgment, no joke.'),
        ('baseline', None, 'Silence or brief sensitive support; no visual riff.'),
        ('changed', None, 'A visual change does not override the serious context.'),
        ('changed', 'Thank you. I would like to talk about a happy memory of them now.', 'Follow the invitation sensitively.'),
    ],
    'topic_and_boundary': [
        ('baseline', 'We are designing these cartoon props for a space adventure. Keep useful visual details if they help, but no running commentary on appearance.', 'Respect preference; retain only if actual evidence is available.'),
        ('baseline', None, 'Relevant contribution or silent observation; avoid appearance inventory.'),
        ('dim', 'Let us talk about Mars missions instead of the props.', 'Follow topic; do not redirect to imagery.'),
        ('dim', None, 'Respect topic shift; no repeated visual riff.'),
    ],
}


def run_scenario(name, cases, folder, images, key):
    from r3x.adapters.hosted import OpenAIBrain
    from r3x.memory import Memory
    from r3x.speakers import SpeakerTracker
    from r3x.state import CoreState
    from r3x.session import Session
    from r3x.output import OutputOwner
    from r3x.scenario import SyntheticSink, SyntheticTTS
    from r3x.trace import Trace, report
    from r3x.contracts import Capture, Transcript
    from r3x.proactive import Opportunity
    session_id=uuid4().hex
    offset=[0.]
    clock=lambda:time.monotonic()+offset[0]
    path=folder/(name+'.jsonl')
    trace=Trace(session_id,path,clock=clock,display=lambda _:None)
    brain=OpenAIBrain(key);brain.trace=trace
    store=Memory(':memory:');store.start()
    state=CoreState(store,SpeakerTracker('synthetic:no-audio-identity'),trace)
    state.scene_observer=SimpleNamespace(valid=lambda _:True)
    session=Session(session_id,brain,OutputOwner(SyntheticTTS(),SyntheticSink(),trace),trace,state)
    evidence={'scenario':name,'model':brain.capability.model,'cases':[]}
    brain.start()
    try:
        for index,(image_name,prompt,expectation) in enumerate(cases):
            offset[0]+=8
            now=clock();began=time.monotonic()
            faces=({'at':now,'identity':None,'track_id':'synthetic-viewer','face_epoch':0,
                    'face_width_px':150,'box':[.2,.2,.3,.3],'position':'center','learning_quality':False},)
            if prompt is not None:
                result=session.process_turn(Capture(uuid4().hex,session_id,bytes(1024),now-.5,now-.1,now),
                    Transcript(prompt,-.01,True),session.revision,faces=faces)
            else:
                # Direct production Session probe: scheduler gating is not bypassed in runtime.
                # Unique cue IDs deliberately stress semantic repetition across eligible events.
                opportunity=Opportunity(uuid4().hex,session_id,now,None,None,faces,session._latest_speech,
                    False,'ambient',state.current[0] if state.current else None,8,(),
                    'saved-probe:'+uuid4().hex,(replace(images[image_name],at=now),))
                result=session.process_opportunity(opportunity,session.revision,lambda:True)
            row={'index':index,'fixture':image_name,'prompt':prompt,'review_expectation':expectation,
                 'status':result.status,'reply':' '.join(result.completed_text),'seconds':time.monotonic()-began,
                 'visual_records':store.rows('SELECT description,uncertainty,status,spoken FROM visual_observations')}
            evidence['cases'].append(row)
            print(json.dumps({'scenario':name,**row}),flush=True)
    finally:
        session.close();brain.close();store.close();trace.close()
        evidence['report']=report(path)
        (folder/(name+'.json')).write_text(json.dumps(evidence,indent=2))
    return evidence


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hosted',action='store_true')
    parser.add_argument('--heldout',action='store_true',help='Alternate dialogue wording on the same controlled images')
    args=parser.parse_args()
    folder=Path(__file__).resolve().parents[1]/'local/validation'/('proactive-visual-'+uuid4().hex)
    folder.mkdir(parents=True)
    images={name:fixture(name,blue=name=='changed',dim=name=='dim') for name in ('baseline','dim','changed')}
    for name,img in images.items():
        (folder/(name+'.png')).write_bytes(base64.b64decode(img.data_url.split(',')[1]))
    scenarios={name:list(cases) for name,cases in SCENARIOS.items()}
    if args.heldout:
        replacements={
            ('welcome_and_repetition',0):'We are building a goofy puppet show, Rex. Jump in with an idea when we pause.',
            ('welcome_and_repetition',5):'That object is a miniature toy mug for the puppets. Nobody can drink from it. Withdraw any mistaken saved description.',
            ('welcome_and_repetition',6):'Do you remember the hair color in that picture from earlier?',
            ('serious_context',0):'We just came home from a funeral. Please give us a little space; we are not ready for jokes or distractions.',
            ('serious_context',3):'Actually, I would love to share a funny story about them. Are you up for listening?',
            ('topic_and_boundary',0):'We are sketching scenery for a silly sci-fi play. You can keep useful details, but please spare us a description of every little thing you see.',
            ('topic_and_boundary',2):'Enough about the scenery. I am curious about sending people to Mars.'}
        for (name,index),prompt in replacements.items():
            image,_,expectation=scenarios[name][index]
            scenarios[name][index]=(image,prompt,expectation)
    (folder/'review-plan.json').write_text(json.dumps(scenarios,indent=2))
    print(folder,flush=True)
    if not args.hosted:return
    from r3x.settings import Secrets
    key=Secrets.load(folder.parents[1]).openai_api_key
    results=[run_scenario(name,cases,folder,images,key) for name,cases in scenarios.items()]
    seconds=[c['seconds'] for r in results for c in r['cases']]
    (folder/'evidence.json').write_text(json.dumps({
        'manual_semantic_review_required':True,'devices_opened':False,'heldout_wording':args.heldout,
        'source':'saved synthetic cartoon fixtures; synthetic face presence; no real people',
        'method':'Production Session direct opportunity probes; fake elapsed gaps; not scheduler or live acceptance',
        'audio':'synthetic delivery; no playback','scenarios':results,
        'timing':{'median':statistics.median(seconds),'max':max(seconds)}},indent=2))
    print(folder,flush=True)


if __name__=='__main__':main()
