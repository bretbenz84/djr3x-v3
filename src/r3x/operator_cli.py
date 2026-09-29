"""Explicit servo operator commands; importing this module opens no devices.

The wake supervisor and the servo/LED/battery menu-bar apps are the V2 copies in
operator_tools/ (September 27); this module keeps only V3 park. Servos that are off
after a power-down are held at their unpowered rest by ordinary startup.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time

from .contracts import Cancellation
from .hardware_ownership import ControllerHandoff, HardwareLocks
from .settings import Settings, ScratchPaths


def main(argv, project: Path):
    parser = argparse.ArgumentParser(description='V3 servo park')
    parser.add_argument('operation',choices=('servo-park',))
    parser.add_argument('--config',type=Path,required=True,help='Explicit V3 settings file')
    args = parser.parse_args(argv)
    settings = Settings.load(args.config.resolve())
    paths = ScratchPaths.for_project(project)
    paths.prepare()
    locks = HardwareLocks()
    from .adapters.maestro import open_serial
    if not settings.servo_port:
        raise ValueError('OperatorServoPortRequired')
    handoff = ControllerHandoff(locks,port=settings.servo_port)
    handoff.acquire(Cancellation())
    released = False
    try:
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
