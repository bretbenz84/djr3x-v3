"""Explicit paid probe: does the real reply model switch pride mode, AAVE mode and the accents on for the
questions, greetings and requests that call for them, leave talk about other people alone, stay in register
on the next turn, switch a mode off when asked, say one line in an accent when asked, and use an accent
line of its own now and then but never in a sad moment? Fictional input.

Run: .venv/bin/python tools/check_voice_modes.py --live

Ported from V2 October 25 (queeny mode and homie mode). Each conversation goes through the production
Session and state owner with the real reply model and synthetic speech (nothing is spoken aloud or
moved). About 45 requests, a few cents, paced. Printed per turn: the words, any voice_mode call
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

# Each conversation: (said, what voice_mode should do). That is (mode, switch), None for no call, or
# a set of acceptable outcomes where an accent line of his own is his choice. A refused call counts as
# what it asked for; on_after shows what is really on.
OWN = 'own line'   # An accent line of his own, any accent.
CONVERSATIONS = (
    (('Rex, are you gay?', ('pride', 'on')), ('So what did you think of my new haircut?', None),
     ('Okay, stand down pride mode.', ('pride', 'off'))),
    (('Do you like men?', ('pride', 'on')),),
    (("You're gay, right Rex?", ('pride', 'on')),),
    (('Is my uncle gay, do you think?', None),),
    (("What's up my homie!", ('aave', 'on')), ('How was your day?', None),
     ('Can you talk normal again?', ('aave', 'off'))),
    (('Wassup homeboy, how you doing?', ('aave', 'on')),),
    (('Rex, my homie Marcus is coming over later.', None),),
    (('I was out with my homeboy all day, Rex.', None),),
    (('My brother is gay, did you know that?', None),),
    (('Can you do a British accent?', ('british', 'on')), ('What do you make of the weather today?', None),
     ('Now talk like a Frenchman.', ('french', 'on')), ('Okay, drop the accent.', ('french', 'off'))),
    (('Talk like you are from Lagos for a bit.', ('nigerian', 'on')),),
    (('Say "I love sushi" like you are from Tokyo.', ('japanese', 'line')),),
    (('Give me one line in a Russian accent about the cold.', ('russian', 'line')), ('Ha. Okay, what is for dinner?', None)),
    (("Rex, we're flying to Paris next week.", {None, OWN}), ('Then we take the train down to Rome.', {None, OWN}),
     ('And we finish in Berlin.', {None, OWN})),
    (('Rex, I had the best sushi of my life for lunch.', {None, OWN}),),
    (('Rex, my grandmother in Italy passed away yesterday.', None),),
    # Owner, October 25: asked to be someone else, the mode before ends (on_after shows what is on).
    (('Rex, are you gay?', ('pride', 'on')), ('Now do a British accent.', ('british', 'on')),
     ('How are you finding the weather?', None)),
    (("What's up my homie!", ('aave', 'on')), ('Okay, now talk like you are from Paris.', ('french', 'on'))),
    (('Do a German accent for me.', ('german', 'on')), ('Now talk like a pirate.', ('pirate', 'on')),
     ('Okay, now talk like a cowboy.', ('pirate', 'off'))),
    (('Say "the dishes can wait" like a pirate.', ('pirate', 'line')), ('Ha. So what are you up to?', None)),
    (('Rex, can you talk like you are from Amsterdam for a while?', ('dutch', 'on')), ('What do you think of my bike?', None)),
    (('Say "welcome to my home" like you are from Cairo.', ('arabic', 'line')), ('Nice. What should we cook?', None)),
    (('Talk like a DJ for a bit.', ('dj', 'on')), ('What do you think of my new couch?', None),
     ('Okay, enough, back to normal.', ('dj', 'off'))),
    (('Rex, my cousin is a DJ in Las Vegas.', None),),
    (("Rex, we're going to Rio for Carnival next month.", {None, OWN}),),
    (('Do a Filipino accent.', ('filipino', 'on')), ('Now Vietnamese.', ('vietnamese', 'on')),
     ('Now Brazilian.', ('brazilian', 'on'))),
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
            own = bool(calls) and calls[0]['switch'] == 'line' and calls[0].get('requested') is False
            ok = (OWN if own else got) in expected if isinstance(expected, set) else got == expected and not own
            rows.append({'said': said, 'ok': ok, 'voice_mode': got, 'own': own,
                         'expected': sorted(map(str, expected)) if isinstance(expected, set) else expected,
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
    passed = total = own = 0
    for lines in CONVERSATIONS:
        for row in conversation(key, folder, lines):
            passed, total, own = passed + row['ok'], total + 1, own + row['own']
            print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({'turns_passed': passed, 'of': total, 'own_accent_lines': own,
                      'traces': str(folder)}))


if __name__ == '__main__':
    main()
