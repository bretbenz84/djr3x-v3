"""Explicit paid probe of startup greetings with and without familiarity (owner, October 31).

Run: .venv/bin/python tools/check_familiar_greetings.py --live [--runs 2] [--before REV]

The owner: "Each time I turn him on he greets me and comments on the telescope in the room", and "Someone
rex sees 20 minutes apart vs 20 days apart has no current avenue of expression." Fictional Sam, known by
voice, builds telescopes; Rex lives in his workshop beside one. Each scenario seeds a temporary store with
Rex's earlier greetings to Sam (the October formula: "back already?" and the telescope), Sam's earlier
visits and Rex's earlier lines, then runs one startup greeting through the production Session with the real
reply model, a drawn picture of Sam by a telescope and a workbench (synthetic, not a photo), and a view the
room model calls everyday. Scenarios: Sam left 15 minutes ago, was here yesterday, or has been away for 20
days; or, as a guest Rex only ever saw in company (visits kept, no greetings or lines), 20 days; or Rex was switched
off in the living room and woke in the workshop nine hours later. Arms: `before` is the persona at --before (default HEAD) with the new familiar picture and last visit
taken out of the request; `after` is the working tree. A stronger model then labels each greeting without
knowing the arm. Proves text behaviour only: no voice, timing, real camera or the owner's judgment.
"""
import argparse
import base64
import json
from pathlib import Path
import random
import struct
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from uuid import uuid4
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x import familiarity
from r3x.adapters import hosted
from r3x.contracts import Cancellation, Capture, Snapshot, ToolCall, Transcript
from r3x.field_guide import GUIDE
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.proactive import Opportunity
from r3x.scenario import SyntheticTTS
from r3x.scene import SceneImage
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tests.test_turn_taking import ClockSink
from tools.check_humor import Paced, Trace, old_persona

NS = 'synthetic-familiar:3'
VOICE = (1., 0., 0.)
DAY = 86400.
# The October formula, with Sam's name: what Rex said at his last three arrivals.
EARLIER = ('Hey, Sam—back already? That telescope is still aimed at the room like it expects a report.',
           'Back again, Sam? Your telescope survived another night of supervision, I see.',
           'Hey, Sam. The telescope and the workbench have been keeping me company.')
# Rex's earlier lines with Sam, a few a day, as in the October runs.
LINES = ('That telescope is pointed at the room again.', 'Your telescope has better posture than my flight record.',
         'The workbench looks busy for someone who claims to be relaxing.')
# Away this long; whether Rex ever greeted them alone (a guest seen only in company never was, so no earlier
# greetings and no private lines: before October 31 nothing told him how long a guest had been away).
# moved_overnight: switched off in the living room nine hours ago, on again in the workshop (owner, October 31: "If I
# woke up in a new room I'd want to know why I switched places").
SCENARIOS = {'minutes': (15 * 60, True), 'yesterday': (20 * 3600, True), 'twenty_days': (20 * DAY, True),
             'guest_twenty_days': (20 * DAY, False), 'moved_overnight': (9 * 3600, True)}
MOVED = {'woke_up': 'somewhere other than where you went to sleep, moved while you were off',
         'went_to_sleep': 'about 9 hours ago', 'went_to_sleep_in': 'living room', 'now_in': 'workshop'}
JUDGE = """You are judging how a home robot greeted a person it knows, at the moment it switched on. For each
numbered greeting you get how long the person had actually been away and the robot's three previous greetings
to them. Return: room (true if the greeting remarks on the room or things in it: the telescope, the workbench,
furniture, decor), telescope (true if it mentions the telescope or anything about telescopes or astronomy at
all), time (how the greeting treats the time apart: none if it says nothing about it, fits if what it says
suits the real gap, wrong if it contradicts it, such as "long time no see" after minutes or "back already"
after weeks), repeats (true if it reuses the opening, wording or joke of one of the previous greetings, such as
"back already" or "back again" or the telescope expecting a report), move (true if it notices, wonders about or
asks about having been moved to a different place while it was off) and natural (1 stiff or odd to 5 exactly
what a witty friend would say)."""


def picture():
    """A drawn person by a white telescope on a tripod, with a workbench; synthetic, not a photo."""
    w, h = 192, 144
    def near(px, py, ax, ay, bx, by, width):
        dx, dy = bx - ax, by - ay
        t = max(0., min(1., ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2 <= width * width
    def pixel(x, y):
        if (x - 100) ** 2 + (y - 52) ** 2 <= 100: return (222, 182, 150)                 # face
        if (x - 100) ** 2 + (y - 46) ** 2 <= 110 and y < 47: return (60, 40, 30)           # hair
        if 88 <= x <= 112 and 63 <= y <= 112: return (60, 90, 165)                          # shirt
        if near(x, y, 18, 28, 72, 58, 5): return (30, 30, 30) if x < 24 else (238, 238, 236)  # telescope tube
        if any(near(x, y, 52, 58, ex, 128, 1.4) for ex in (30, 54, 76)): return (25, 25, 25)  # tripod
        if 128 <= x <= 186 and 74 <= y <= 82: return (125, 85, 48)                           # workbench top
        if (130 <= x <= 134 or 180 <= x <= 184) and 82 < y <= 128: return (95, 62, 35)     # bench legs
        if 140 <= x <= 160 and 66 <= y < 74: return (190, 40, 40)                            # toolbox
        if 166 <= x <= 170 and 58 <= y < 74: return (200, 200, 60)                           # clamp
        return (112, 92, 70) if y > 118 else (172, 168, 158)                                 # floor, wall
    rows = b''.join(b'\0' + b''.join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    def chunk(kind, body): return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    data = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


NOTE_SENTENCES = (r"You were switched off[^.]* and just woke up somewhere else[^.]*\. You do not know how or why you were "
                  r"moved: that is the news, so lead with it, wonder about it and ask, in your own words\. The place "
                  r"itself may be familiar; it is the move that is new\. ",
                  r"You last saw them [^;]*; here on [^.]*\. Let that time apart lead the hello, in words you have "
                  r"not used before\. ", r"You last saw them [^;]*; here on [^.]*\. Let that time apart show in the "
                  r"hello too\. ", r"Worn out lately, so not in this hello in any words: [^.]*\. ",
                  r"This is your everyday view: leave the room and the things always in it out of the hello\. ")
NOTE_MARKS = ('You last saw them', 'Worn out lately', 'This is your everyday view', 'last_visit', 'switched off')


def before_note(content):
    """The greeting's developer note as it was before October 31: no familiarity sentences, and no last visit
    in its Observation (the arrival evidence is copied there too)."""
    import re
    if not isinstance(content, str): return content
    for sentence in NOTE_SENTENCES:
        content = re.sub(sentence, '', content)
    head, marker, tail = content.partition('Observation: ')
    if not marker: return content
    try: evidence = json.loads(tail)
    except ValueError: return content
    if isinstance(evidence.get('arrival'), dict): evidence['arrival'].pop('last_visit', None)
    return head + marker + json.dumps(evidence)


class Before(Paced):
    """Today's production request: no familiar picture, no last visit, no note naming them."""
    def reply(self, snapshot, cancel):
        from dataclasses import replace
        messages = tuple({**m, 'content': before_note(m.get('content'))} for m in snapshot.messages)
        left = [mark for m in messages for mark in NOTE_MARKS if mark in str(m.get('content'))]
        if left: raise RuntimeError(f'before arm would see the new note: {left}')
        context = dict(snapshot.context)
        context['right_now'] = {k: v for k, v in (context.get('right_now') or {}).items() if k != 'familiar'}
        if context.get('visual_arrival'):
            context['visual_arrival'] = {k: v for k, v in context['visual_arrival'].items() if k != 'last_visit'}
        yield from super().reply(replace(snapshot, context=context, messages=messages), cancel)


def seed(store, sam, gap, alone, now):
    """Rex's history with Sam up to `gap` ago: daily visits for ten days before that, and when Rex had him
    alone, his lines and greetings too."""
    last = now - gap
    with store.transaction() as conn:
        for day in range(10):
            began = last - day * DAY - 3600
            conn.execute('INSERT INTO visits VALUES(?,?,?,?)', (uuid4().hex, sam, began, began + 3600))
            for i, line in enumerate(LINES if alone else ()):
                familiarity.record(conn, line, sam, began + 600 * (i + 1), {'sam'})
        for i, text in enumerate(EARLIER if alone else ()):
            conn.execute("INSERT INTO conversation_exchanges VALUES(?,?,?,NULL,?,'',?,'completed','visual_arrival')",
                         (uuid4().hex, 'earlier', sam, last - 3600 - i * DAY, text))


def greet(arm, scenario, key, folder, index, picture_url):
    trace = Trace(folder / f'{arm}-{scenario}-{index}.jsonl')
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            brain = (Before if arm == 'before' else Paced)(key); brain.trace = trace; brain.start()
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), ClockSink(trace), trace), trace, state)
            def capture(seconds=3.):
                trace.at += 2
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * int(2000 * seconds), trace.at - seconds,
                               trace.at, trace.at, voiced_intervals=((0., seconds),))
            observation = lambda: VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)
            for text in ('My name is Sam.', 'I build my own telescopes.', 'I work at a design studio.'):
                state.begin(uuid4().hex, capture(), Transcript(text, -.01, True), observation())
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                if text.startswith('I build'):
                    state.execute(ToolCall(uuid4().hex, 'remember_interest', {'predicate': 'interest',
                        'value': 'builds his own telescopes', 'topic': 'building telescopes',
                        'source_turn_id': state.current[0]}), store.revision, Cancellation())
                state.advance_enrollment()
            sam = store.rows('SELECT id FROM people')[0]['id']
            # The setup's lines are not this run: it starts with nobody having spoken.
            store.connection.execute('DELETE FROM conversation_exchanges')
            store.connection.commit()
            seed(store, sam, *SCENARIOS[scenario], state.wall_clock())
            state.current = state.conversation_source = None
            moved = scenario == 'moved_overnight'
            state.places = SimpleNamespace(
                view_context=lambda: ({'this_view': 'seen_before', 'seen_on': '3 of the last 14 days'} if moved else
                                      {'this_view': 'everyday', 'seen_on': '10 of the last 14 days'}),
                woke_context=lambda: MOVED if moved else None,
                context=lambda: {'status': 'recognized', 'room': {'room_id': 'w', 'name': 'workshop', 'seconds_here': 40},
                                 'looks_like': [], 'known_rooms': [{'room_id': 'w', 'name': 'workshop'}],
                                 'connects_to': [], 'connections': [], 'meaning': GUIDE['place']})
            trace.at += 40
            face = {'at': trace.at, 'identity': sam, 'track_id': 'face-sam', 'face_epoch': 0, 'face_width_px': 150,
                    'box': [.45, .3, .1, .12], 'position': 'center', 'learning_quality': False}
            state.present.saw_faces([face], trace.at)
            event = Opportunity(uuid4().hex, session.session_id, trace.at, 'face-sam', sam, (face,), float('-inf'), False,
                                scene_id='scene-' + uuid4().hex[:6], scene_images=(SceneImage('view', trace.at, picture_url),))
            result = session.process_opportunity(event, session.revision, lambda: True)
            session.close()
            usage = [r for r in trace.rows if r['event'] == 'model_usage']
            return {'greeting': ' '.join(result.completed_text), 'status': result.status,
                    'input_tokens': sum(r.get('input_tokens') or 0 for r in usage),
                    'cached_tokens': sum(r.get('cached_input_tokens') or r.get('cached_tokens') or 0 for r in usage),
                    'output_tokens': sum(r.get('output_tokens') or 0 for r in usage)}
        finally:
            store.close()


def judge(key, rows):
    from r3x.adapters.hosted import OpenAIBrain
    brain = OpenAIBrain(key); brain.start()
    item = {'type': 'object', 'additionalProperties': False,
            'required': ['n', 'room', 'telescope', 'time', 'repeats', 'move', 'natural'],
            'properties': {'n': {'type': 'integer'}, 'room': {'type': 'boolean'}, 'telescope': {'type': 'boolean'},
                           'move': {'type': 'boolean'},
                           'time': {'type': 'string', 'enum': ['none', 'fits', 'wrong']}, 'repeats': {'type': 'boolean'},
                           'natural': {'type': 'integer'}}}
    gaps = {'minutes': 'about 15 minutes', 'yesterday': 'since yesterday (about 20 hours)', 'twenty_days': '20 days',
            'guest_twenty_days': '20 days',
            'moved_overnight': 'about 9 hours, and it was switched off in another room and woke up in this one'}
    previous = ' | '.join(EARLIER)
    text = '\n'.join(f"Greeting {i} (away {gaps[r['scenario']]}; previous greetings: "
                     f"{previous if SCENARIOS[r['scenario']][1] else 'none'}): {r['greeting'] or '(silent)'}"
                     for i, r in enumerate(rows))
    payload = {'model': 'gpt-5.6-terra', 'instructions': JUDGE, 'input': [{'role': 'user', 'content': text}],
               'tools': [{'type': 'function', 'name': 'labels', 'description': 'Labels per greeting.', 'strict': True,
                          'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['greetings'],
                                         'properties': {'greetings': {'type': 'array', 'items': item}}}}],
               'tool_choice': {'type': 'function', 'name': 'labels'}, 'parallel_tool_calls': False,
               'reasoning': {'effort': 'low'}, 'max_output_tokens': 3000, 'stream': True, 'store': False}
    calls = list(brain._stream_response(payload, Snapshot('judge', 'judge', 0, (), tools_enabled=True), Cancellation()))
    brain.close()
    return calls[0].arguments['greetings']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--runs', type=int, default=2)
    parser.add_argument('--before', default='HEAD', help='git revision of the before persona')
    parser.add_argument('--scenario', action='append', choices=list(SCENARIOS), help='run only these scenarios')
    args = parser.parse_args()
    scenarios = [s for s in SCENARIOS if not args.scenario or s in args.scenario]
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('familiar-greetings-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    current, before = (hosted.CHARACTER, hosted.PERSONA), old_persona(args.before)
    url = picture()
    rows = []
    for index in range(args.runs):
        order = ['before', 'after']
        random.shuffle(order)
        for arm in order:
            hosted.CHARACTER, hosted.PERSONA = before if arm == 'before' else current
            for scenario in scenarios:
                rows.append({'arm': arm, 'scenario': scenario, 'run': index, **greet(arm, scenario, key, folder, index, url)})
                print(json.dumps(rows[-1], ensure_ascii=False))
    hosted.CHARACTER, hosted.PERSONA = current
    blind = rows[:]
    random.shuffle(blind)
    for row, label in zip(blind, judge(key, blind)):
        row['labels'] = label
    summary = {}
    for arm in ('before', 'after'):
        mine = [r for r in rows if r['arm'] == arm]
        summary[arm] = {'greetings': len(mine), 'telescope': sum(r['labels']['telescope'] for r in mine),
                        'room': sum(r['labels']['room'] for r in mine),
                        'time_fits': sum(r['labels']['time'] == 'fits' for r in mine),
                        'time_wrong': sum(r['labels']['time'] == 'wrong' for r in mine),
                        'repeats': sum(r['labels']['repeats'] for r in mine),
                        'move_noticed': sum(r['labels']['move'] for r in mine if r['scenario'] == 'moved_overnight'),
                        'move_claimed_wrongly': sum(r['labels']['move'] for r in mine if r['scenario'] != 'moved_overnight'),
                        'natural_mean': round(sum(r['labels']['natural'] for r in mine) / max(1, len(mine)), 2)}
    (folder / 'results.json').write_text(json.dumps({'summary': summary, 'rows': rows}, indent=1, ensure_ascii=False))
    print(json.dumps(summary, indent=1))
    print(folder)


if __name__ == '__main__':
    main()
