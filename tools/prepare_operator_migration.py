"""Prepare independent wake assets, robot settings and LaunchAgents; never load them."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
from datetime import datetime, timezone

from r3x.settings import Settings, ScratchPaths


def prepare(project: Path, source: Path, servo_port: str, agents: Path) -> Path:
    paths = ScratchPaths.for_project(project)
    paths.prepare()
    settings_path = paths.root/'settings.toml'
    current = Settings.load(settings_path) if settings_path.exists() else Settings()
    values = asdict(current)
    values['servo_port'] = servo_port
    Settings(**values)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    bundle = paths.root/'operator-migration'/stamp
    if bundle.resolve()!=bundle:
        raise ValueError('OperatorBundleRedirected')
    bundle.mkdir(parents=True)
    config = bundle/'robot-settings.toml'
    # All settings are scalar or string arrays; None means omit the optional field.
    config.write_text('\n'.join(f'{key} = {json.dumps(value)}' for key,value in values.items() if value is not None)+'\n')
    Settings.load(config)
    wake = paths.root/'models/wake'
    if wake.resolve()!=wake:
        raise ValueError('WakeDirectoryRedirected')
    wake.mkdir(parents=True,exist_ok=True)
    records = []
    for relative in ('wakeuprex.onnx','_openwakeword_resources/melspectrogram.onnx',
                     '_openwakeword_resources/embedding_model.onnx'):
        original = source/relative
        target = wake/original.name
        digest = hashlib.sha256(original.read_bytes()).hexdigest()
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:
            raise ValueError('RefusingToReplaceDifferentWakeModel')
        if target.is_symlink(): raise ValueError('WakeTargetRedirected')
        if not target.exists(): shutil.copyfile(original,target)
        records.append({'source':str(original),'name':target.name,'sha256':digest})
    (wake/'manifest.json').write_text(json.dumps(records,indent=2)+'\n')
    inventory = []
    for role in ('supervisor','servo','led','battery'):
        old = agents/f'com.djr3x.{role}.plist'
        if old.exists():
            raw = old.read_bytes()
            data = plistlib.loads(raw)
            (bundle/old.name).write_bytes(raw)
            inventory.append({'path':str(old),'label':data['Label'],
                              'sha256':hashlib.sha256(raw).hexdigest()})
    for role,operation in (('supervisor','supervisor'),('servo','servo-console')):
        label = f'com.djr3x.v3.{role}'
        data = {'Label':label,'ProgramArguments':[str(project/'.venv/bin/python'),'-m','r3x',
            'operator',operation,'--config',str(config)],'WorkingDirectory':str(project),
            'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,
            'StandardOutPath':str(paths.root/f'sessions/{role}.out.log'),
            'StandardErrorPath':str(paths.root/f'sessions/{role}.err.log'),
            'EnvironmentVariables':{'PYTHONUNBUFFERED':'1'}}
        (bundle/(label+'.plist')).write_bytes(plistlib.dumps(data))
    (bundle/'inventory.json').write_text(json.dumps({'original_agents':inventory,'wake_models':records},indent=2)+'\n')
    return bundle


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wake-source',type=Path,required=True)
    parser.add_argument('--servo-port',required=True)
    args=parser.parse_args()
    print(prepare(Path(__file__).resolve().parents[1],args.wake_source,args.servo_port,
                  Path.home()/'Library/LaunchAgents'))
