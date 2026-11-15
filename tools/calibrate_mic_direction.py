"""Measure where Rex's microphone array places a voice, position by position, while you walk round him.

    .venv/bin/python tools/calibrate_mic_direction.py            one lap of eight positions
    .venv/bin/python tools/calibrate_mic_direction.py --laps 2   two laps, for a repeatability check

Owner, November 15, 2026: the array put his voice about 26 degrees off and Rex drove the wrong way;
"run a program where I move in quadrants and speak while it gathers data so you can get a directional
offset". Rex stays still: nothing moves and no serial port is opened. The program takes the microphone
from the wake-word listener (Rex must be shut down), speaks each instruction through the Mac's speaker,
waits while you walk to the spot, then records the array's direction readings while you count aloud,
using Rex's own direction reader. It writes every reading and a summary to
local/calibration/mic-direction-<time>.json, which is what the correction is fitted from.

Directions are Rex's own: front is where his base faces, and his left is the side with his arms.
Stand about two metres from his body and talk at your normal height and volume.
"""
import argparse
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

from r3x.contracts import Cancellation, ProviderFailure
from r3x.direction import Direction, summarize, wrap
from r3x.settings import ScratchPaths

# Rex's frame: degrees from straight ahead, positive to his left (the array's own convention).
POSITIONS = (
    ('front', 'straight in front of Rex', 0.),
    ('front-left', "in front of Rex and to his left, halfway between his front and his arm side", 45.),
    ('left', "on Rex's left, the side with his arms, level with the middle of his body", 90.),
    ('back-left', 'behind Rex and to his left, halfway between his arm side and his back', 135.),
    ('back', 'straight behind Rex', 180.),
    ('back-right', 'behind Rex and to his right, halfway between his back and his right side', -135.),
    ('right', "on Rex's right, the side without arms, level with the middle of his body", -90.),
    ('front-right', 'in front of Rex and to his right, halfway between his right side and his front', -45.),
)
WALK_S = 6.       # Time to get to the next spot after the instruction ends; readings are ignored.
LISTEN_S = 8.     # Then this long of counting aloud is recorded.
RETRIES = 2       # A position with too little speech is asked again this many times.


def say(text, speaking):
    """Print the instruction and speak it through the Mac's speaker (macOS say), flagging playback
    so the array's readings of Rex's own speaker are left out."""
    print('\n' + text, flush=True)
    if shutil.which('say') is None:
        return
    speaking.set()
    try:
        subprocess.run(['say', '-r', '185', text], check=False, timeout=30)
    finally:
        time.sleep(.6)   # The room's echo of the prompt dies away before readings count again.
        speaking.clear()


def circular_mean(values):
    return wrap(math.degrees(math.atan2(sum(math.sin(math.radians(v)) for v in values),
                                        sum(math.cos(math.radians(v)) for v in values))))


def record(direction, seconds):
    began = time.monotonic()
    time.sleep(seconds)
    ended = time.monotonic()
    with direction.lock:
        window = [s for s in direction.samples if began <= s.at <= ended]
    return began, ended, window


def run(args, paths):
    from r3x.adapters.flex import FlexControl
    from r3x.hardware_ownership import ControllerHandoff, HardwareLocks
    handoff = ControllerHandoff(HardwareLocks())   # The microphone only: no serial port is opened.
    try:
        handoff.acquire(Cancellation())
    except ProviderFailure as exc:
        print(f'Could not take the microphone ({exc}). If Rex is running, tell him to shut down first.')
        return 1
    speaking = threading.Event()
    direction = Direction(FlexControl(), echo_risk=speaking.is_set)
    out = paths.root / 'calibration'
    out.mkdir(parents=True, exist_ok=True)
    path = out / time.strftime('mic-direction-%Y%m%d-%H%M%S.json')
    results = []
    try:
        direction.start()
        say('Microphone direction check. Rex stays still. At each spot, stand about two metres from his '
            'body and count slowly to ten in your normal voice. Front is where his base faces; his left '
            'is the side with his arms.', speaking)
        stops = [p for _ in range(args.laps) for p in POSITIONS] + [POSITIONS[0]]   # Front again: did anything drift?
        for number, (name, where, true_deg) in enumerate(stops, 1):
            row = None
            for attempt in range(RETRIES + 1):
                if direction.failure:
                    raise ProviderFailure(direction.failure)
                prompt = (f'Spot {number} of {len(stops)}: go {where}. When you get there, count to ten.'
                          if attempt == 0 else "I didn't hear enough. Count to ten again, a little louder.")
                say(prompt, speaking)
                time.sleep(WALK_S if attempt == 0 else 0.)
                print('  listening...', flush=True)
                began, ended, window = record(direction, LISTEN_S)
                evidence = summarize(tuple(window), began, ended)
                # Rex's reader declines a reading with a competing or mirrored cluster; for calibration
                # the strongest cluster still says where the array heard most of the voice.
                strongest = evidence['clusters'][0]['bearing_deg'] if evidence['clusters'] else None
                row = {'spot': number, 'position': name, 'true_deg': true_deg, 'attempt': attempt + 1,
                       'status': evidence['status'], 'reason': evidence['reason'],
                       'measured_deg': evidence['bearing_deg'] if evidence['bearing_deg'] is not None else strongest,
                       'clusters': evidence['clusters'],
                       'samples': [{'at': round(s.at - began, 2), 'beam_deg': round(s.beam, 1), 'doa_deg': s.doa,
                                    'energy': round(s.energy), 'eligible': s.eligible} for s in window]}
                if evidence['status'] != 'unavailable':
                    break
            results.append(row)
            shown = 'no reading' if row['measured_deg'] is None else f"heard at {row['measured_deg']:+.0f} degrees"
            print(f"  {name}: true {true_deg:+.0f}, {shown} ({row['status']})", flush=True)
            path.write_text(json.dumps({'positions': results}, indent=1))   # Kept even if stopped part-way.
        say('Done. Thank you.', speaking)
    except KeyboardInterrupt:
        print('\nStopped; readings so far are saved.')
    finally:
        try:
            direction.close()
        finally:
            handoff.close()
    errors = [wrap(r['measured_deg'] - r['true_deg']) for r in results if r['measured_deg'] is not None]
    summary = {'readings': len(errors), 'of': len(results),
               'mean_error_deg': round(circular_mean(errors), 1) if errors else None,
               'worst_error_deg': round(max(errors, key=abs), 1) if errors else None}
    path.write_text(json.dumps({'when': time.strftime('%Y-%m-%d %H:%M:%S'), 'positive': 'left of straight ahead',
                                'summary': summary, 'positions': results}, indent=1))
    print('\nPosition       true  heard  error')
    for r in results:
        heard = '   --' if r['measured_deg'] is None else f"{r['measured_deg']:+5.0f}"
        error = '   --' if r['measured_deg'] is None else f"{wrap(r['measured_deg'] - r['true_deg']):+5.0f}"
        print(f"{r['position']:12s} {r['true_deg']:+5.0f}  {heard}  {error}")
    print(f"\nSaved to {path}")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--laps', type=int, default=1, choices=(1, 2, 3), help='times round the eight positions')
    args = parser.parse_args()
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    return run(args, paths)


if __name__ == '__main__':
    sys.exit(main())
