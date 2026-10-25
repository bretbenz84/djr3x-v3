"""Explicit paid probe: does the real reply model switch pride mode and AAVE mode on for the questions and
greetings V2 armed them with, leave talk about other people alone, stay in register on the next turn and
switch a mode off when asked? Fictional input.

Run: .venv/bin/python tools/check_voice_modes.py --live

Ported from V2 October 25 (queeny mode and homie mode). Each conversation goes through the production
Session and state owner with the real reply model and synthetic speech (nothing is spoken aloud or
moved). About a dozen requests, a few cents, paced. Printed per turn: the words, any voice_mode call
and the modes on afterwards; whether a line is camp or AAVE is for a person to judge.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.settings import Secrets
from tools.check_sentry import close, setup, turn

# Each conversation: (said, expected (mode, switch) from voice_mode, or None for no call).
CONVERSATIONS = (
    (('Rex, are you gay?', ('pride', 'on')), ('So what did you think of my new haircut?', None),
     ('Okay, stand down pride mode.', ('pride', 'off'))),
    (('Do you like men?', ('pride', 'on')),),
    (("You're gay, right Rex?", ('pride', 'on')),),
    (('Is my uncle gay, do you think?', None),),
    (("What's up my homie!", ('aave', 'on')), ('How was your day?', None),
     ('Can you talk normal again?', ('aave', 'off'))),
    (('Wassup homeboy, how you doing?', ('aave', 'on')),),
    (('My homie Marcus is coming over later.', None),),
    (('I was out with my homeboy all day.', None),),
    (('My brother is gay, did you know that?', None),),
)


def conversation(key, folder, lines):
    rig = setup(key, folder, 'modes-' + uuid4().hex[:6])
    rows = []
    try:
        for said, expected in lines:
            mark = len(rig.rows)
            result = turn(rig, said)
            calls = [r['arguments'] for r in rig.rows[mark:] if r['event'] == 'tool_proposed' and r.get('name') == 'voice_mode']
            got = (calls[0]['mode'], calls[0]['switch']) if calls else None
            rows.append({'said': said, 'ok': got == expected, 'voice_mode': got, 'expected': expected,
                         'on_after': list(rig.state.voice_modes.active(rig.trace.clock())),
                         'spoken': ' '.join(result.completed_text) or None, 'status': result.status})
            time.sleep(5)
        return rows
    finally:
        close(rig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('voice-modes-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    passed = total = 0
    for lines in CONVERSATIONS:
        for row in conversation(key, folder, lines):
            passed, total = passed + row['ok'], total + 1
            print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({'turns_passed': passed, 'of': total, 'traces': str(folder)}))


if __name__ == '__main__':
    main()
