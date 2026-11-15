"""Explicit live show: every light feeling on the real LED board, one after another, named as it runs.

Run with Rex off: .venv/bin/python tools/show_light_feelings.py --live [--seconds 6] [--only angry]

For each feeling the eyes take its colour, the mouth shows its colour at a steady level and the chest
runs its pattern, through the production LED owner (the same commands as a light request before a
line); then the lights settle for two seconds. It begins with the usual startup sparkle and ends with
the shutdown fade. The controller lock is taken first, so the menu bar apps let go of the board and
the wake word cannot start Rex meanwhile; it is given back at the end. Lights only: nothing moves.
Whether each feeling looks right is for a person to judge.
"""
import argparse
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from r3x.contracts import Cancellation
from r3x.hardware_ownership import ControllerHandoff, HardwareLocks
from r3x.leds import COLORS, FEELING_LIGHTS, LEDs
from r3x.settings import Settings

STARTUP_SECONDS = 14.   # The chest's startup sparkle; an ordinary panel pattern waits for it to end.


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--seconds', type=float, default=6., help='How long each feeling shows')
    parser.add_argument('--only', action='append', choices=sorted(COLORS), help='Only these feelings')
    args = parser.parse_args()
    settings = Settings.load(ROOT / 'local' / 'settings.toml')
    if not settings.led_port:
        raise SystemExit('led_port is not set in local/settings.toml')
    bodies = {}
    for expression, pattern in FEELING_LIGHTS.items():
        bodies.setdefault(pattern, []).append(expression)
    handoff = ControllerHandoff(HardwareLocks(), led_ports={'leds': settings.led_port})
    handoff.acquire(Cancellation())   # Refused while Rex is running.
    led = LEDs(settings.led_port)
    try:
        led.start()
        print(f'LED board firmware {led.firmware}. Startup sparkle, {STARTUP_SECONDS:.0f} seconds.', flush=True)
        time.sleep(STARTUP_SECONDS)
        for pattern in args.only or COLORS:
            shown_for = ', '.join(bodies.get(pattern, ())) or 'an ordinary line'
            print(f'{pattern:12} eyes {COLORS[pattern]}, with: {shown_for}', flush=True)
            token = led.begin(pattern, speaking=False)
            time.sleep(args.seconds)
            led.finish(token)
            time.sleep(2.)
        print('Fading out.', flush=True)
    finally:
        try:
            led.close()
        finally:
            handoff.close()


if __name__ == '__main__':
    main()
