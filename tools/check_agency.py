"""Explicit paid agency acceptance probe; fictional input, no robot or audio I/O.

Run: .venv/bin/python -m tools.check_agency --live
Uses production scheduler, Session, Terra planner and Luna executor, a temporary
database, synthetic microphone health and playback, and production servo/base owners on fake transports.
October 2 cases follow the goal design: a known friend arrives (catch up), a stranger arrives
(get to know), a room with a painting on the wall (investigate), and rejected humor.
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
from types import SimpleNamespace
from uuid import uuid4
import zlib

from r3x.adapters.hosted import OpenAIBrain, OpenAIAgencyPlanner
from r3x.contracts import Capture, Transcript, ProviderFailure
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.physical_attention import PhysicalAttention, PhysicalGesture
from r3x.robot_servos import RobotServos
from r3x.drive import DriveBase
from r3x.inspection import CurrentView
from tests.test_servos import PulsePort
from tests.test_drive import FakeBase, World
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


def png(width, height, pixel):
    def chunk(kind, body):
        return struct.pack('>I',len(body))+kind+body+struct.pack('>I',zlib.crc32(kind+body))
    rows=b''.join(b'\0'+b''.join(bytes(pixel(x,y)) for x in range(width)) for y in range(height))
    data=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))
    data+=chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')
    return 'data:image/png;base64,'+base64.b64encode(data).decode()


def painted_wall(x, y):
    """A synthetic living-room wall with a framed sailboat painting; drawn, not photographed."""
    if y >= 200: return (150,110,70)                       # wooden floor
    if 80 <= x < 240 and 40 <= y < 170:
        if not (88 <= x < 232 and 48 <= y < 162): return (90,55,25)  # frame
        if (x-205)**2+(y-70)**2 <= 120: return (250,215,60)            # sun
        if 120 <= y < 132 and 128+(y-120) <= x < 192-(y-120): return (110,60,30)  # hull
        if 60 <= y < 118 and 158 <= x <= 158+(y-60)*32//58: return (250,250,245)  # sail
        return (135,190,235) if y < 115 else (30,90,160)                  # sky, sea
    return (220,205,180)                                    # wall


class SyntheticScene:
    """An unchanged synthetic view; current receipts without fabricated object labels."""
    def __init__(self, clock=time.monotonic, picture=None):
        self.clock=clock
        self.url=png(320,240,picture) if picture else png(64,64,lambda x,y:(32,96,160))

    def snapshot(self, *args): return ('synthetic-view', (SceneImage('synthetic-frame',self.clock(),self.url),))
    def attempted(self, scene_id): pass
    def valid(self, scene_id): return scene_id=='synthetic-view'
    def continuity(self): return {'status':'fresh','view_epoch':0,'relation':'same_view'}


class ProbeClock:
    """Real API latency, accelerated inter-turn lulls and simulated motor waits."""
    def __init__(self): self.offset=0.
    def __call__(self): return time.monotonic()+self.offset
    def wait(self, seconds):
        self.offset+=max(0.,seconds)
        time.sleep(.002)


CASES=('known_friend','new_visitor','curious_room','feedback_and_camera')
FRIEND_FACTS=(('dog_name','Biscuit, a beagle who steals socks'),('training_for','a half marathon in November'))


def run_case(name, folder, key):
    session_id=uuid4().hex
    path=folder/(name+'.jsonl')
    rows=[]
    def record(row):
        rows.append(row)
        if row['event'] in {'agency_plan_saved','agency_plan_failed','agency_step_finished','turn_failed'}:
            print(json.dumps({'case':name,**row}),flush=True)
    clock=ProbeClock()
    trace=Trace(session_id,path,clock=clock,display=record)
    brain=Hosted(key);planner=OpenAIAgencyPlanner(key,'gpt-5.6-terra')
    brain.trace=planner.trace=trace
    # These are injected protocol simulators. No serial/audio/camera device opens.
    port=PulsePort()
    robot=RobotServos('simulated-maestro',open_port=lambda:port,clock=clock,wait=clock.wait)
    robot.owner.wait=clock.wait
    robot.trace=trace
    base=FakeBase(clock);world=World(base)
    drive=DriveBase('simulated-esp32',open_port=lambda:base,clock=clock,wait=clock.wait,reflex=False)
    drive.faces=world.faces;drive.objects=world.detections;base.front=world.front_obstacle
    drive.trace=trace
    session=None;receipts=[]
    attention=PhysicalAttention(robot);gesture=PhysicalGesture(robot)
    try:
        robot.start();drive.start();attention.start();gesture.start()
        with TemporaryDirectory() as tmp:
            store=Memory(Path(tmp).resolve()/'memory.sqlite3');store.start()
            try:
                friend=None
                if name=='known_friend':
                    # Fictional fixture person with two accepted facts; no real personal data.
                    with store.transaction() as conn:
                        friend=store.add_person(conn,'Sam',[],None,status='supported')
                        conn.execute('INSERT INTO turns VALUES(?,?,?,?,?,?)',('fixture-turn','fixture','fixture',
                            'My beagle Biscuit steals socks, and I am training for a half marathon.','{}',0.))
                        for i,(predicate,value) in enumerate(FRIEND_FACTS):
                            conn.execute('INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?)',('fixture-claim-'+str(i),
                                friend,predicate,value,friend,None,'fixture-turn','self_report','accepted',None,1))
                world.people=[('sam' if friend else 'visitor',friend,2.,0.)]
                if name=='curious_room':world.objects=[('object:plant','potted plant',2.6,1.2)]
                state=CoreState(store,SpeakerTracker('synthetic-agency:3'),trace)
                scene=SyntheticScene(clock,painted_wall if name=='curious_room' else None)
                view=CurrentView(scene,clock=clock) if scene else None
                if view:view.start()
                output=OutputOwner(SyntheticTTS(),SyntheticSink(),trace,robot=robot,drive=drive,
                    gestures=gesture,attention=attention,inspection=view)
                session=Session(session_id,brain,output,trace,state)
                session.agency_planner=planner
                state.scene_observer=scene
                runtime=SyntheticRuntime(None,None,None,session,trace,scene_observer=scene)
                def faces(now):
                    return [{**f,'face_width_px':f['box'][2]*1920,'learning_quality':False,
                             'needs_closer_look':False,'position':'center'} for f in world.faces(now)]
                runtime.camera=SimpleNamespace(latest=faces)
                brain.start();planner.start()
                trace.emit('probe_start',evidence='live_models_synthetic_inputs_fake_controller_transports',case=name)
                def human(words):
                    now=clock()
                    session.note_speech(now)
                    result=session.process_turn(Capture(uuid4().hex,session_id,bytes(96000),now-3,now,now),
                        Transcript(words,-.01,True),session.revision,
                        VoiceObservation('synthetic-agency:3',(1.,0.,0.),((0.,2.,(1.,0.,0.)),),3.,1.),
                        faces=runtime.camera.latest(now) if runtime.camera else (),
                        latest_faces=runtime.camera.latest if runtime.camera else None)
                    receipts.append({'kind':'human_reply','input':words,**asdict(result)})
                    # A quiet reply (for example a glance away from someone who wants to read) is valid.
                    if result.status not in {'completed','suppressed'}:raise RuntimeError('HumanReplyFailed:'+str(result.error))
                arrival=name in {'known_friend','new_visitor'}
                def look():
                    for age in (3.,2.5,2.,1.5,1.,.5,0.):
                        at=clock()-age;runtime.arrivals.observe(runtime.camera.latest(at),at)
                    if not arrival:  # Already greeted; these cases concern what follows.
                        for entry in runtime.arrivals.seen.values():entry['used']=True
                look()
                if name=='curious_room':human("I'm going to read for a bit. Entertain yourself, Rex.")
                elif name=='feedback_and_camera':
                    # Fictional transcript reproduces the failure without uploading private logs.
                    session.history.append({'role':'assistant','content':'That clock could be a prime-number drum machine.'})
                    human("You're talking nonsense. Can you ask how I'm doing instead?")
                    human("I'm doing okay. I'm just annoyed by the forced music jokes.")
                deadline=time.monotonic()+240
                answered=False; repaired_topic=False
                while time.monotonic()<deadline:
                    # Planning is real-time background work; the accelerated clock waits for it.
                    session.join_planning(90)
                    time.sleep(2)  # Pace hosted requests; OpenAI rejects unpaced bursts.
                    clock.wait(9)  # Exercise the real scheduler; no real human waiting in a probe.
                    look()
                    result=runtime.proactive_tick()
                    if result and result.status!='prepared':receipts.append({'kind':'proactive',**asdict(result)})
                    completed=[r for r in rows if r['event']=='agency_step_finished' and r['status']=='completed']
                    spoke=bool(result and result.completed_text)
                    if name=='known_friend' and not answered and spoke and completed:
                        human('Pretty good, actually. Biscuit stole one of my running socks this morning.')
                        answered=True
                    if name=='new_visitor' and not answered and spoke:
                        human("I'm Jordan, I'm here to fix the wifi. Nice robot.")
                        answered=True
                    if name=='curious_room' and not answered and spoke and completed:
                        human('Ha, that painting? My grandfather painted it. He used to sail on the lake.')
                        answered=True
                    if name=='feedback_and_camera' and len(completed)>=3 and not repaired_topic:
                        human('I spent the afternoon fixing a squeaky door. It finally shuts properly.')
                        repaired_topic=True
                    if len(completed)>=5:break
                    if sum(r['event']=='model_request' for r in rows)>=20:break
                    if result and result.status=='failed':break
                # Explicit quiet must stop further proactive model work.
                state.initiative.quiet=True
                before=sum(r['event']=='model_request' for r in rows)
                clock.wait(20);runtime.proactive_tick()
                quiet_ok=before==sum(r['event']=='model_request' for r in rows)
                session.close();session=None
            finally:store.close()
    finally:
        if session:session.close()
        gesture.close();attention.close();drive.close();robot.close()
        brain.close();planner.close();trace.close()
    result={'case':name,'receipts':receipts,'report':report(path),'quiet_stops_model_calls':quiet_ok,
            'proactive_speech':[x['completed_text'] for x in receipts if x['kind']=='proactive' and x['completed_text']],
            'invalidations':[r['reason'] for r in rows if r['event']=='proactive_invalidated'],
            'completed_steps':sum(r['event']=='agency_step_finished' and r['status']=='completed' for r in rows),
            'step_kinds':[r['kind'] for r in rows if r['event']=='agency_step_finished' and r['status']=='completed'],
            'goals':[{k:r.get(k) for k in ('goal_kind','subject','objective','origin')} for r in rows if r['event']=='agency_plan_saved'],
            'planner_calls':sum(r['event']=='model_request' and str(r.get('purpose','')).startswith('agency_plan') for r in rows),
            'spoken':[(r.get('purpose'),r.get('completed_text')) for r in rows if r['event']=='delivery' and r.get('completed_text')]}
    (folder/(name+'-result.json')).write_text(json.dumps(result,indent=2))
    print(json.dumps({'case':name,'completed_steps':result['completed_steps'],'step_kinds':result['step_kinds'],
                      'planner_calls':result['planner_calls'],'goals':result['goals'],'trace':str(path)}),flush=True)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live',action='store_true',help='Authorize paid model calls with fictional inputs.')
    parser.add_argument('--case',choices=CASES,action='append',
                        help='Run only a selected scenario (default: all four).')
    args=parser.parse_args()
    if not args.live:parser.error('--live is required; this probe makes paid model calls')
    root=Path(__file__).resolve().parents[1]/'local'
    folder=root/'validation'/('agency-'+uuid4().hex);folder.mkdir(parents=True)
    key=Secrets.load(root).openai_api_key
    if not key:raise SystemExit('OpenAI key missing')
    results=[]
    for name in args.case or CASES:
        results.append(run_case(name,folder,key))
        time.sleep(2)
    print(folder,flush=True)
    return 0 if all(r['completed_steps']>=5 and r['quiet_stops_model_calls'] for r in results) else 1


if __name__=='__main__':raise SystemExit(main())
