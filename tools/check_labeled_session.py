"""Silent, local-model diagnostic of user-numbered clips. Never enrolls profiles.

Usage: .venv/bin/python tools/check_labeled_session.py local/recordings/SESSION
Uses the recorded VAD intervals and playback receipts with the production Endpoint,
Perception and Session. Clip-level labels are used ONLY after predictions for scoring;
missing speaker boundary times do not become invented ground truth.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import resource
import statistics
import time
from uuid import uuid4

from r3x.adapters.asr import QwenASR
from r3x.adapters.voice import CAMPlus,NAMESPACE
from r3x.capture import Endpoint,Frame
from r3x.contracts import Cancellation
from r3x.dataset import read_wav
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.perception import Perception
from r3x.providers import ProviderOwner
from r3x.session import Session
from r3x.speakers import SpeakerTracker
from r3x.state import CoreState
from r3x.trace import Trace,report


def baseline_predictions(events, clips):
    """Join old split turns to their original labeled capture, including legacy traces."""
    turns = {r['turn_id']: r.get('parent_capture_id') or r['capture_id']
             for r in events if r['event'] == 'turn'}
    labels = {c['capture_id']: c['clip'] for c in clips}
    result = []
    for row in events:
        if row['event'] != 'speaker_evidence':
            continue
        capture = turns.get(row['turn_id'], '')
        if capture not in labels:
            parent, separator, part = capture.rpartition('-part')
            if separator and part.isdigit():
                capture = parent
        if capture in labels:
            result.append({'clip': labels[capture], 'attribution': row})
    return result


def playback_intervals(events):
    active={}
    spans=[]
    for row in events:
        if row['event']=='tts_first_pcm':
            active[row['turn_id']]=row['at']
        elif row['event'] in {'phrase_completed','delivery'} and row['turn_id'] in active:
            spans.append((active.pop(row['turn_id']),row.get('completed_at',row['at'])+.3))
    if active:
        raise ValueError('PlaybackEndReceiptMissing')
    return spans


def replay_frames(pcm,meta,playback,session):
    evidence=meta['capture_evidence']
    offset=evidence['started_at']
    voiced=evidence['voiced_intervals']
    endpoint=Endpoint(session)
    for n in range(0,len(pcm),1024):
        block=pcm[n:n+1024]
        start=n/32000
        end=start+len(block)/32000
        speech=any(min(end,b)-max(start,a)>.016 for a,b in voiced)
        echo=any(offset+start<b and offset+end>a for a,b in playback)
        capture=endpoint.push(Frame(block,offset+start,offset+end,echo),speech)
        if capture: yield capture
    tail=endpoint.finish()
    if tail: yield tail


def score(clips,predictions):
    tracks={}
    assigned=0
    eligible=0
    missing=[]
    mixed=[]
    device_only=[]
    device_retained=[]
    for clip in clips:
        rows=[p for p in predictions if p['clip']==clip['clip']]
        labels=clip['speaker_sequence']
        if len(labels)!=1:
            mixed.append({'clip':clip['clip'],'expected_order':labels,
                          'retained_parts':len(rows),'boundaries_verified':False})
            continue
        if labels[0] in {'Rex','Siri'}:
            device_only.append(clip['clip'])
            if rows: device_retained.append(clip['clip'])
            continue
        eligible+=1
        assigned_tracks={r['attribution']['track_id'] for r in rows if r.get('attribution') and r['attribution']['track_id']}
        if assigned_tracks:
            assigned+=1
            for track in assigned_tracks: tracks.setdefault(track,set()).add(labels[0])
        else: missing.append(clip['clip'])
    per_person={name:len([t for t,labels in tracks.items() if name in labels]) for name in sorted({v for labels in tracks.values() for v in labels})}
    return {'single_human_clips':eligible,'assigned_clips':assigned,'track_coverage':assigned/eligible if eligible else None,
            'abstained_or_excluded_clips':missing,'tracks_per_person':per_person,
            'cross_person_tracks':sum(len(v)>1 for v in tracks.values()),
            'device_only_clips':device_only,'device_only_retained_clips':device_retained,
            'mixed_clips':mixed,'named_identity_accuracy':None,'speaker_boundary_accuracy':None,
            'group_acceptance':False,'cohort':'user-labeled tuning session; not held-out acceptance'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('folder',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'local'
    folder=args.folder.absolute()
    if folder.resolve()!=folder or not folder.is_relative_to(root/'recordings'):
        raise ValueError('IndependentV3RecordingFolderRequired')
    raw=(folder/'human-labels.json').read_bytes()
    clips=json.loads(raw)['clips']
    meta={r['id']:r for r in map(json.loads,(folder/'unreviewed.jsonl').read_text().splitlines())}
    events=[json.loads(s) for s in (root/'sessions'/(folder.name+'.jsonl')).read_text().splitlines()]
    spans=playback_intervals(events)
    run_id=uuid4().hex
    from r3x.settings import ScratchPaths
    ScratchPaths(root).prepare()
    directory=root/'validation'/run_id
    directory.mkdir(parents=True)
    trace=Trace(run_id,directory/'session.jsonl',display=lambda row:None)
    asr=QwenASR(root/'models/qwen3-asr',-.35)
    encoder=CAMPlus(root/'models/campplus.onnx')
    owner=ProviderOwner((asr,encoder))
    memory=Memory(':memory:');memory.start()
    state=CoreState(memory,SpeakerTracker(NAMESPACE),trace,frozen=True)
    session=Session(run_id,None,TextOutput(trace),trace,state)
    predictions=[]
    durations=[]
    try:
        if not owner.start():raise ValueError('ProviderStartupFailed')
        perception=Perception(asr,encoder,trace)
        for clip in clips:
            source=folder/clip['file']
            if source.resolve()!=source or source.parent!=folder:raise ValueError('IndependentWaveRequired')
            if hashlib.sha256(source.read_bytes()).hexdigest()!=clip['sha256']:raise ValueError('ChangedLabeledWave')
            pcm=read_wav(source)
            m=meta[clip['capture_id']]
            began=time.monotonic()
            for capture in replay_frames(pcm,m,spans,run_id):
                if capture.echo_risk:
                    trace.emit('capture_rejected',capture_id=capture.capture_id,reason='playback_echo_risk')
                    continue
                for perceived in perception.analyze_many(capture,Cancellation()):
                    part=perceived.capture
                    session.process_turn(part,perceived.transcript,session.revision,perceived.voice)
                    attribution=asdict(state.current[4]) if state.current and state.current[1].capture_id==part.capture_id else None
                    predictions.append({'clip':clip['clip'],'offset':part.started_at-m['capture_evidence']['started_at'],
                        'duration':len(part.pcm)/32000,'parent_capture_id':part.parent_capture_id,
                        'transcript':perceived.transcript.text,'attribution':attribution})
            durations.append(time.monotonic()-began)
            print('Processed clip',clip['clip'],flush=True)
        # Baseline joins the user's labels to actual earlier runtime decisions.
        baseline = baseline_predictions(events, clips)
        result={'source_session':folder.name,'labels_sha256':hashlib.sha256(raw).hexdigest(),
            'inference':'production Endpoint using recorded VAD and reconstructed PCM-ready/playback-end intervals; production Perception/Session',
            'limitations':['Old callbacks did not retain frame-level echo flags; playback mask is reconstructed.',
                          'Speaker order labels lack timing/overlap annotations; no boundary-accuracy claim.',
                          'Threshold development used this session; results are tuning evidence.'],
            'before':score(clips,baseline),'after':score(clips,predictions),'predictions':predictions,
            'models':[p.capability.model for p in owner.providers],
            'clip_processing_seconds':{'median':statistics.median(durations),'max':max(durations)},
            'peak_rss_bytes_macos':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'named_profiles_saved':len(memory.rows('SELECT id FROM recordings')),
            'audio_played':False,'devices_opened':False,'model_calls':0,
            'runtime':report(directory/'session.jsonl')}
        # Trace uses original capture times to preserve acoustic track spacing;
        # the difference from today's wall clock is not an ASR queue latency.
        result['runtime']['observed_seconds'].pop('queue_seconds',None)
        result['limitations'].append('Live queue latency is not measured in this saved-timestamp replay.')
        (directory/'report.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'report':str(directory/'report.json'),'before':result['before'],'after':result['after']},indent=2))
    finally:
        session.close();owner.close();memory.close();trace.close()


if __name__=='__main__':main()
