"""The drive board's compass, read while Rex is not running (owner, November 25; from V2's compass_calibrate.py).

  .venv/bin/python tools/compass.py read [--seconds 5]
      Which way he faces, from the saved calibration (local/calibration/compass.json).
  .venv/bin/python tools/compass.py calibrate [--seconds 90] [--facing DEG] [--keep FILE.jsonl]
      Records while he is turned through a full circle, then writes the calibration. Turn him by hand or with
      the gamepad, in steps with a pause at each: readings while the motors draw current are left out. --facing
      is his true heading at the end (0 north, 90 east), when someone can say it; it pins the heading.

Both send the drive board `hello` only: no heartbeat, configuration or motion. The tool takes the controller
lock, so the battery menu hands over the port and Rex cannot start meanwhile. Nothing here turns the wheels.
"""
import argparse
import json
import math
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from r3x import compass
from r3x.adapters.esp32 import DriveLink, open_serial
from r3x.hardware_ownership import HardwareLocks, serial_port_free


class Recorder(DriveLink):
    def __init__(self, open_port):
        super().__init__(open_port)
        self.telemetry = []

    def dispatch(self, message, received_at):
        if message.get('type') == 'telemetry':
            self.telemetry.append(message)
        super().dispatch(message, received_at)


def record(seconds, note=None):
    """Telemetry messages for `seconds`, read-only. Exits with a reason when the port cannot be had."""
    port = tomllib.loads((ROOT / 'local/settings.toml').read_text())['drive_port']
    lease = HardwareLocks().lease('controller')
    if not lease.acquire():
        sys.exit('Rex (or another controller) holds the drive board; stop him first.')
    try:
        deadline = time.monotonic() + 15
        while not serial_port_free(port):
            if time.monotonic() > deadline:
                sys.exit('The drive port is still held after 15 s.')
            time.sleep(.25)
        link = Recorder(lambda: open_serial(port))
        try:
            link.open({})
            if not any((t.get('mag') or {}).get('ok') for t in link.telemetry) and not _wait_for_mag(link):
                sys.exit('No compass reading in telemetry (mag.ok false): is the sensor wired and answering?')
            if note:
                print(note, flush=True)
            start, last = time.monotonic(), 0.
            while time.monotonic() - start < seconds and not link.failure:
                elapsed = time.monotonic() - start
                if note and elapsed - last >= 10:
                    last = elapsed
                    print(f'  {elapsed:4.0f} s, {len(link.telemetry)} readings', flush=True)
                time.sleep(.2)
            if link.failure:
                sys.exit(f'The drive link failed: {link.failure}')
            return list(link.telemetry)
        finally:
            link.close()
    finally:
        lease.close()


def _wait_for_mag(link):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if any((t.get('mag') or {}).get('ok') for t in link.telemetry[-5:]):
            return True
        time.sleep(.3)
    return False


def read(args):
    cal = compass.load(ROOT / compass.CALIBRATION_FILE)
    readings = record(args.seconds)
    mags = [t['mag'] for t in readings if (t.get('mag') or {}).get('ok')]
    if mags:
        print('raw counts (last):', {k: mags[-1][k] for k in ('x', 'y', 'z')})
    if cal is None:
        print('No calibration yet: run `tools/compass.py calibrate` first. Raw counts alone point nowhere.')
        return 1
    rows = [h for h in (compass.heading(t, cal) for t in readings) if h is not None]
    quiet = [h for h in rows if h['motors_quiet']] or rows
    if not quiet:
        print('No usable compass reading.')
        return 1
    # The circular mean of the quiet readings.
    s = sum(math.sin(math.radians(h['heading_deg'])) for h in quiet)
    c = sum(math.cos(math.radians(h['heading_deg'])) for h in quiet)
    facing = math.degrees(math.atan2(s, c)) % 360.
    field_ok = sum(h['field_ok'] for h in quiet) / len(quiet)
    print(f'facing {facing:.0f} degrees true ({_cardinal(facing)}), from {len(quiet)} readings; '
          f'field as calibrated in {field_ok:.0%} of them (calibrated {cal.saved_at}'
          f'{", flat turn" if cal.flat else ""}{", mirrored axes" if cal.mirrored else ""}).')
    if field_ok < .8:
        print('The field no longer matches the calibration (something magnetic moved near the sensor, or he is '
              'beside steel): recalibrate before trusting this.')
    return 0


def _cardinal(deg):
    names = ('north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west')
    return names[int((deg + 22.5) // 45) % 8]


def calibrate(args):
    readings = record(args.seconds, note=f'Recording for {args.seconds:.0f} s: turn him through a full circle now, '
                                         'in steps with a pause at each (by hand or the gamepad).')
    if args.keep:
        Path(args.keep).write_text(''.join(json.dumps(t) + '\n' for t in readings))
    cal, note = compass.fit(readings, args.facing)
    if cal is None:
        print(f'Calibration refused: {note}')
        return 1
    path = compass.save(cal, ROOT / compass.CALIBRATION_FILE)
    print(f'Written to {path}: offsets {[round(v, 1) for v in cal.offset]}, scales {[round(v, 3) for v in cal.scale]}, '
          f'field {cal.field_norm:.0f} counts from {cal.samples} quiet readings'
          f'{", mirrored axes" if cal.mirrored else ""}; heading offset {cal.heading_offset_deg:.1f} degrees'
          f'{" (pinned to the facing given)" if args.facing is not None else " (declination only)"}.')
    if note:
        print(f'Note: {note}.')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('read')
    r.add_argument('--seconds', type=float, default=5.)
    c = sub.add_parser('calibrate')
    c.add_argument('--seconds', type=float, default=90.)
    c.add_argument('--facing', type=float, default=None, help='true heading at the end, degrees (0 north, 90 east)')
    c.add_argument('--keep', default=None, help='also write the readings to this JSON lines file')
    args = parser.parse_args()
    return read(args) if args.command == 'read' else calibrate(args)


if __name__ == '__main__':
    sys.exit(main())
