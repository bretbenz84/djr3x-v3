"""Explicit operator entry points; importing this module opens no devices."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import signal
import subprocess
import threading
import time

from .contracts import Cancellation, ProviderFailure
from .hardware_ownership import ControllerHandoff, HardwareLocks, legacy_supervisor_active, serial_port_free
from .settings import Settings, ScratchPaths


def commission_rest(bus, *, confirmed: bool):
    """One-time pulse initialization after a human verifies every unpowered joint.

    Does not create a durable park-truth marker. Requires ALL outputs off; a mixed
    or already energized pose must be inspected instead of being overwritten.
    """
    from .servo_poses import LIMITS, HEAD_PARK, PARK
    if not confirmed:
        raise ValueError('PhysicalRestConfirmationRequired')
    current = bus.positions(LIMITS)
    if any(current.values()):
        raise ValueError('CommissioningRequiresAllOutputsOff')
    targets = {**dict(HEAD_PARK),5:6000,6:6000,7:5984,**dict(PARK)}
    bus.profiles({ch:(10,2) for ch in targets})
    bus.targets(targets)
    return {'status':'rest_pulses_commanded','targets_quarter_us':targets,
            'physical_pose_verified_by':'operator','evidence':'not_shaft_feedback'}


def main(argv, project: Path):
    parser = argparse.ArgumentParser(description='V3 servo console and wake supervisor')
    parser.add_argument('operation',choices=('servo-console','supervisor','servo-park','servo-commission'))
    parser.add_argument('--config',type=Path,required=True,help='Explicit V3 settings file')
    parser.add_argument('--confirm-rest',action='store_true',help='I physically verified every joint at the documented unpowered rest pose')
    args = parser.parse_args(argv)
    settings = Settings.load(args.config.resolve())
    paths = ScratchPaths.for_project(project)
    paths.prepare()
    locks = HardwareLocks()
    if args.operation in {'supervisor','servo-console'} and legacy_supervisor_active():
        raise ProviderFailure('V2SupervisorMustBeStoppedBeforeOperatorMigration')
    from .adapters.maestro import Maestro,open_serial
    if args.operation != 'supervisor' and not settings.servo_port:
        raise ValueError('OperatorServoPortRequired')
    if args.operation in {'servo-park','servo-commission'}:
        if args.operation=='servo-commission' and not args.confirm_rest:
            raise ValueError('PhysicalRestConfirmationRequired')
        handoff = ControllerHandoff(locks,port=settings.servo_port)
        handoff.acquire(Cancellation())
        released = False
        try:
            if args.operation=='servo-commission':
                bus = Maestro(open_serial(settings.servo_port))
                try:
                    print(json.dumps(commission_rest(bus,confirmed=args.confirm_rest)))
                finally:
                    bus.close()
                    released = True
            else:
                from .servos import ServoOwner,ServoRequest
                owner = ServoOwner(lambda:open_serial(settings.servo_port),medium='controller')
                try:
                    owner.start()
                    result = owner.execute(ServoRequest('operator-park',owner.version,'park',time.monotonic()+120),Cancellation())
                    print(json.dumps(asdict(result)))
                    return 0 if result.status=='pulses_reached' else 1
                finally:
                    owner.close()
                    released = True
        finally:
            if released:
                handoff.close()
        return 0
    singleton = locks.lease('console' if args.operation=='servo-console' else 'supervisor')
    if not singleton.acquire():
        raise ProviderFailure('OperatorCompanionAlreadyRunning')
    try:
        if args.operation=='servo-console':
            from .servo_console import ServoConsole,run_menu
            def connect():
                if not serial_port_free(settings.servo_port):
                    raise ProviderFailure('ServoPortStillOwned')
                return open_serial(settings.servo_port)
            run_menu(ServoConsole(locks,connect))
        else:
            from .adapters.wake import WakeListener
            from .supervisor import Supervisor
            def launch():
                with (paths.root/'sessions/controller-console.log').open('a') as log:
                    return subprocess.Popen([str(project/'.venv/bin/python'),'-m','r3x','live',
                        '--config',str(args.config.resolve())],cwd=project,stdin=subprocess.DEVNULL,
                        stdout=log,stderr=log,start_new_session=True)
            supervisor = Supervisor(locks,lambda:WakeListener(settings,paths.root/'models/wake').start(),launch)
            stop = threading.Event()
            old = {sig:signal.signal(sig,lambda *_:stop.set()) for sig in (signal.SIGINT,signal.SIGTERM)}
            try:
                while not stop.is_set():
                    status = supervisor.tick()
                    stop.wait(.08 if status=='listening' else .2)
            finally:
                supervisor.close()
                for sig,handler in old.items(): signal.signal(sig,handler)
    finally:
        singleton.close()
    return 0
