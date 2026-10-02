"""Explicit paid agency acceptance probe; fictional input, no robot or audio I/O.

Run: .venv/bin/python -m tools.check_agency --live
Uses production scheduler, Session, Terra planner and Luna executor, a temporary
database, synthetic microphone health, synthetic playback and simulated gestures.
This proves model integration, not actual hearing, perception or physical movement.
"""
import argparse
import base64
from dataclasses import asdict
import json
from pathlib import Path
import struct
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
import zlib

from r3x.adapters.hosted import OpenAIBrain, OpenAIAgencyPlanner
from r3x.contracts import Capture, Transcript, ProviderFailure
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.performance import SimulatedGesture
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.scene import SceneImage
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace, report


class Hosted(OpenAIBrain):
    def _wait_retry(self, seconds, cancel):
        raise ProviderFailure('ProbeTransportUnavailable')


class SyntheticRuntime(VoiceRuntime):
    @property
    def audio_seen_at(self): return self.trace.clock()-.01

    @audio_seen_at.setter
    def audio_seen_at(self, value): pass


class SyntheticScene:
    """An unchanged blue square; current receipts without fabricated object labels."""
    def __init__(self):
        def chunk(kind, body):
            return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
        png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',64,64,8,2,0,0,0))
        png+=chunk(b'IDAT',zlib.compress((b'\0'+b'\x20\x60\xa0'*64)*64))+chunk(b'IEND',b'')
        self.url='data:image/png;base64,'+base64.b64encode(png).decode()

    def snapshot(self): return ('synthetic-blue', (SceneImage('blue-frame',time.monotonic(),self.url),))
    def attempted(self, scene_id): pass
    def valid(self, scene_id): return scene_id=='synthetic-blue'
    def continuity(self): return {'status':'fresh','view_epoch':0,'relation':'same_view'}


def run_case(name, folder, key):
    session_id=uuid4().hex
    path=folder/(name+'.jsonl')
    rows=[]
    def record(row):
        rows.append(row)
        if row['event'] in {'agency_plan_saved','agency_plan_failed','agency_scope_narrowed',
                            'agency_step_finished','turn_failed'}:
            print(json.dumps({'case':name,**row}),flush=True)
    trace=Trace(session_id,path,display=record)
    brain=Hosted(key);planner=OpenAIAgencyPlanner(key,'gpt-5.6-terra')
    brain.trace=planner.trace=trace
    gesture=SimulatedGesture(trace.clock);gesture.start()
    session=None
    receipts=[]
    try:
        with TemporaryDirectory() as tmp:
            store=Memory(Path(tmp).resolve()/'memory.sqlite3');store.start()
            try:
                state=CoreState(store,SpeakerTracker('synthetic-agency:3'),trace)
                output=OutputOwner(SyntheticTTS(),SyntheticSink(),trace,gestures=gesture)
                session=Session(session_id,brain,output,trace,state)
                session.agency_planner=planner
                scene=SyntheticScene() if name=='conversation_and_camera' else None
                state.scene_observer=scene
                runtime=SyntheticRuntime(None,None,None,session,trace,scene_observer=scene)
                brain.start();planner.start()
                trace.emit('probe_start',evidence='live_models_synthetic_inputs_and_output',case=name)
                if scene:
                    now=trace.clock()
                    words='I am trying to invent a musical instrument for a droid. Brass seems too dignified.'
                    session.note_speech(now)
                    result=session.process_turn(Capture('fictional-human',session_id,bytes(96000),now-3,now,now),
                        Transcript(words,-.01,True),session.revision,
                        VoiceObservation('synthetic-agency:3',(1.,0.,0.),((0.,2.,(1.,0.,0.)),),3.,1.))
                    receipts.append({'kind':'human_reply',**asdict(result)})
                    if result.status!='completed':raise RuntimeError('HumanReplyFailed')
                deadline=time.monotonic()+180
                while time.monotonic()<deadline:
                    result=runtime.proactive_tick()
                    if result and result.status!='prepared':receipts.append({'kind':'proactive',**asdict(result)})
                    completed=[r for r in rows if r['event']=='agency_step_finished' and r['status']=='completed']
                    if len(completed)>=3:break
                    if sum(r['event']=='model_request' for r in rows)>=12:break
                    if result and result.status=='failed':break
                    time.sleep(.1)
                session.close();session=None
            finally:store.close()
    finally:
        if session:session.close()
        gesture.close();brain.close();planner.close();trace.close()
    result={'case':name,'receipts':receipts,'report':report(path),
            'completed_steps':sum(r['event']=='agency_step_finished' and r['status']=='completed' for r in rows)}
    (folder/(name+'-result.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps({'case':name,'completed_steps':result['completed_steps'],'trace':str(path)}),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Authorize paid model calls with fictional inputs.')
    parser.add_argument('--case',choices=('empty_room','conversation_and_camera'),action='append',
                        help='Run only a selected scenario (default: both).')
    args=parser.parse_args()
    if not args.live:parser.error('--live is required; this probe makes paid model calls')
    root=Path(__file__).resolve().parents[1]/'local'
    folder=root/'validation'/('agency-'+uuid4().hex);folder.mkdir(parents=True)
    key=Secrets.load(root).openai_api_key
    if not key:raise SystemExit('OpenAI key missing')
    results=[]
    for name in args.case or ('empty_room','conversation_and_camera'):
        results.append(run_case(name,folder,key))
        time.sleep(2)
    print(folder,flush=True)
    return 0 if all(r['completed_steps']>=3 for r in results) else 1


if __name__=='__main__':raise SystemExit(main())
