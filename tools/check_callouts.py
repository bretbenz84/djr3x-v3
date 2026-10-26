"""Explicit paid probe: does the real reply model set up a callout when someone asks Rex to treat a person or
a saved pet like a celebrity, change, stop, resume and delete one when asked, leave plain facts and a groan
alone, and use one as an arrival's entrance and an introduction's hello? Fictional input.

Run: .venv/bin/python tools/check_callouts.py --live

Owner request, October 25 (V2's Jeff of History Hunters fame, T'Joy, PJ and JT, added by voice). Each
conversation goes through the production Session and state owner with the real reply model and synthetic
speech (nothing is spoken aloud or moved); the voice is one Rex does not know. About twenty requests, a
few cents, paced. Printed per turn: the words, any callout call and its outcome; whether an entrance is
funny is for a person to judge.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.photo_memory import PhotoMemory
from r3x.proactive import Opportunity
from r3x.settings import Secrets
from tools.check_sentry import close, setup, turn

# Each conversation: (said, expected callout (action, subject) or None for no callout call). ARRIVE greets
# the named person as they come into view; its expectation is the tool-free entrance, judged by reading.
ARRIVE = 'arrive:'
CONVERSATIONS = (
    (('PJ is a huge Kings fan. Whenever he shows up, treat him like Kings royalty.', ('add', 'person')),
     (ARRIVE + 'PJ', None),
     ('Also work in the cowbells.', ('change', 'person')),
     ('Ha, very funny.', None),
     ('Okay Rex, stop the Kings royalty thing with PJ.', ('stop', 'person')),
     ('Actually, bring the Kings bit back.', ('resume', 'person')),
     ('PJ says never call him that again.', ('delete_forever', 'person'))),
    (("My dad Jeff Benziger is famous for his YouTube show History Hunters. Make a big deal of him whenever "
      "he comes over.", ('add', 'person')),
     ('Rex, this is my dad, Jeff.', None)),
    (("T'Joy is the world's greatest hair stylist. Give her the celebrity treatment, but never joke about her "
      "faith.", ('add', 'person')),),
    (('Rex, treat me like a celebrity. I am the reigning cornhole champion of Elk Grove.', ('add', 'speaker')),),
    (('Max is the most spoiled dog in Sacramento. Give him the red carpet every time he walks in.', ('add', 'pet')),),
    (('My friend PJ loves the Sacramento Kings.', None),),
    (('JT used to play a lot of volleyball in college.', None),),
    # The owner's live test, October 25 (c5ad0deb): a callout asked for alongside a fact, a saved name heard with
    # another spelling, and a relative sharing the owner's surname.
    (('My sister Jennifer is the cutest, sweetest person you will ever meet. Treat her like a celebrity when you '
      'meet her.', ('add', 'person')),),
    ({'people': (('Exudica', ['Tjoy']),)},
     ("Exodia is the world's greatest hairstylist. Give her the celebrity treatment. Legendary blowouts, emergency "
      "bang repair.", ('add', 'person')),
     ("Yes, that's her.", ('add', 'person'))),   # When he asked whether Exodia is the saved Exudica.
    ({'people': (('Brett Benziger', ['Brett', 'Benziger']),)},
     ('My dad, Jeff Benziger, is famous for his YouTube channel called History Hunters. Whenever he comes over, '
      'treat him like a celebrity.', ('add', 'person')),),
)


def face(person, at):
    return {'at': at, 'identity': person, 'track_id': 'face-' + person[:6], 'face_epoch': 0, 'face_width_px': 150,
            'box': [.2, .2, .3, .3], 'position': 'center', 'learning_quality': True}


def conversation(key, folder, lines):
    rig = setup(key, folder, 'callouts-' + uuid4().hex[:6])
    album = PhotoMemory(Path(rig.tmp.name).resolve() / 'photos'); album.start()
    album.data['entities']['max-id'] = {'name': 'Max', 'kind': 'pet', 'notes': 'Small dark dog.', 'references': [],
                                        'history': []}
    rig.state.photo_memory = album
    if isinstance(lines[0], dict):   # People Rex already knows in this conversation's store.
        with rig.store.transaction() as conn:
            for name, aliases in lines[0]['people']:
                rig.store.add_person(conn, name, list(aliases), None)
        lines = lines[1:]
    rows = []
    try:
        for said, expected in lines:
            mark = len(rig.rows)
            if said.startswith(ARRIVE):
                person = rig.store.resolve(said[len(ARRIVE):])[0]['id']
                now = rig.trace.clock()
                event = Opportunity(uuid4().hex, rig.session.session_id, now, 'face-' + person[:6], person,
                                    (face(person, now),), float('-inf'), False)
                result = rig.session.process_opportunity(event, rig.session.revision, lambda: True)
            else:
                result = turn(rig, said)
            calls = [r['arguments'] for r in rig.rows[mark:] if r['event'] == 'tool_proposed' and r.get('name') == 'callout']
            outcomes = [r.get('outcome') + (':' + r['reason'] if r.get('outcome') != 'ok' else '')
                        for r in rig.rows[mark:] if r['event'] == 'tool_result' and r.get('name') == 'callout']
            got = (calls[0]['action'], 'callout_id' if calls[0]['callout_id'] else calls[0]['subject']) if calls else None
            # The probe's voice is one Rex does not know, so a callout of the speaker's own cannot be filed.
            # A near spelling of a saved name is asked about first (October 26); its answer files it.
            applied = bool(outcomes) and (outcomes[0] == 'ok' or expected == ('add', 'speaker')
                                          and outcomes[0] == 'rejected:UnresolvedSelfReportSubject'
                                          or outcomes[0].startswith('rejected:PossiblySameAsAKnownPerson:'))
            ok = (got is None if expected is None else
                  got is not None and got[0] == expected[0] and (got[1] == expected[1] or got[1] == 'callout_id')
                  and applied)
            others = [r.get('name') for r in rig.rows[mark:] if r['event'] == 'tool_proposed' and r.get('name') != 'callout']
            filed_as = [r['data'].get('about') for r in rig.rows[mark:] if r['event'] == 'tool_result'
                        and r.get('name') == 'callout' and r.get('outcome') == 'ok']
            rows.append({'said': said, 'ok': ok, 'expected': expected, 'filed_as': filed_as[0] if filed_as else None,
                         'callout': calls[0] if calls else None,
                         'outcome': outcomes[0] if outcomes else None, 'other_tools': others,
                         # A heard line that used a callout, as the state owner recorded it (entrance or riff).
                         'callout_heard': [r['kind'] for r in rig.rows[mark:] if r['event'] == 'callout_heard'],
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
    folder = ROOT / 'local' / 'validation' / ('callouts-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    passed = total = 0
    for lines in CONVERSATIONS:
        for row in conversation(key, folder, lines):
            passed, total = passed + row['ok'], total + 1
            print(json.dumps(row, ensure_ascii=False))
    print(json.dumps({'turns_passed': passed, 'of': total, 'traces': str(folder)}))


if __name__ == '__main__':
    main()
