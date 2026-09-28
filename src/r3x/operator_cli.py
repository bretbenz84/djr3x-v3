"""Explicit servo operator commands; importing this module opens no devices.

The wake supervisor and the servo/LED/battery menu-bar apps are the V2 copies in
operator_tools/ (September 27); this module keeps only V3 park and commissioning.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from .contracts import Cancellation, ProviderFailure
from .hardware_ownership import ControllerHandoff, HardwareLocks
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
    parser = argparse.ArgumentParser(description='V3 servo park and commissioning')
    parser.add_argument('operation',choices=('servo-park','servo-commission'))
    parser.add_argument('--config',type=Path,required=True,help='Explicit V3 settings file')
    parser.add_argument('--confirm-rest',action='store_true',help='I physically verified every joint at the documented unpowered rest pose')
    args = parser.parse_args(argv)
    settings = Settings.load(args.config.resolve())
    paths = ScratchPaths.for_project(project)
    paths.prepare()
    locks = HardwareLocks()
    from .adapters.maestro import Maestro,open_serial
    if not settings.servo_port:
        raise ValueError('OperatorServoPortRequired')
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
