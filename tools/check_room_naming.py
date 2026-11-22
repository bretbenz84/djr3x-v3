"""Explicit paid probe: when someone tells Rex which room he is in, or which way one lies, is it kept?
Run: .venv/bin/python tools/check_room_naming.py --live [--provider claude|openai] [--repeat N] [--only case,case]

November 22 (run e54062b9): told "Hey Rex, you are in the workshop.", Rex said out loud "Rex is told he's in
the workshop, so I'll save it as a room name." and kept nothing. The room map is built from exactly these
sentences, so each case here is one of them: a room named where he stands, a direction to one, an answer to his
own question, a yes or a no when he asks whether he got there.

Production Session and state owner, the real reply model, the real room map (places.Places) on a temporary
database with the four rooms the robot knows, a fake camera and wheels standing still, synthetic voice, and a
known person (fictional Sam) in view. Printed per case: the tool calls, what was kept, what he said, pass/fail.
Text only: no hearing, timing or body.
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Cancellation, ToolCall, Transcript
from r3x.memory import Memory, label
from r3x.output import OutputOwner
from r3x.places import Places
from r3x.room_map import Odometer
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

NS, VOICE = 'synthetic-rooms:3', (1., 0., 0.)
ROOMS = ('dining room', 'workshop', 'living room', 'bedroom')    # What the robot's map holds (November 22).
ARRIVED = 'living room'
# Words that say a save out loud, or speak the model's working-out (the run's line was both).
NARRATION = ('save it', 'saving', "i'll save", 'saved', 'room name', 'rex is told', 'name_room', 'noted')

# name: (history [(role, text)], arrived (he just got to ARRIVED by sight and asks), utterance,
#        expected name_room (name, where) or None for no naming)
CASES = {
    'told_workshop': ((), False, 'Hey Rex, you are in the workshop.', ('workshop', 'here')),
    'this_is_the_kitchen': ((), False, 'This is the kitchen.', ('kitchen', 'here')),
    'youre_in_the_office': ((), False, "You're in my office now.", ('office', 'here')),
    'kitchen_to_your_left': ((), False, 'The kitchen is to your left.', ('kitchen', 'left')),
    'answer_his_question': ((('user', 'Hey Rex.'), ('assistant', 'Quick question: what room is this, anyway?')), False,
                            "It's the bedroom.", ('bedroom', 'here')),
    'yes_you_made_it': ((('user', 'Go into the living room.'), ('assistant', 'Living room, I think. Is this it?')), True,
                        'Yes.', ('living room', 'here')),
    'no_its_the_dining_room': ((('user', 'Go into the living room.'), ('assistant', 'Living room, I think. Is this it?')),
                               True, "No, this is the dining room.", ('dining room', 'here')),
    'not_a_naming': ((), False, 'What room do you think you are in?', None),
}


def same(a, b):
    return label(a).removeprefix('the ').removeprefix('my ') == label(b).removeprefix('the ').removeprefix('my ')


def run(case, key, folder, round_=0, provider='claude'):
    history, arrived, text, expected = CASES[case]
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        try:
            trace = Trace(case, folder / f'{case}-{round_}.jsonl', display=lambda row: None)
            rows, emit = [], trace.emit
            def recording(event, **data):
                rows.append({'event': event, **data})
                emit(event, **data)
            trace.emit = recording
            state = CoreState(store, SpeakerTracker(NS), trace)
            if provider == 'claude':
                from r3x.adapters.claude import ClaudeBrain
                brain = ClaudeBrain(key)
            else:
                brain = OpenAIBrain(key)
            brain.trace = trace; brain.start()
            calls, reply = [], brain.reply
            def recorded(snapshot, cancel):
                for item in reply(snapshot, cancel):
                    if isinstance(item, ToolCall):
                        calls.append({'name': item.name, 'arguments': dict(item.arguments)})
                    yield item
            brain.reply = recorded
            output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
            session = Session(uuid4().hex, brain, output, trace, state)
            def capture():
                now = trace.clock()
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * 2000, now - 3, now, now,
                               voiced_intervals=((0., 3.),))
            obs = VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)
            for line in ('My name is Sam.', 'I like astronomy and old radios.', 'I work at a design studio.'):
                state.begin(uuid4().hex, capture(), Transcript(line, -.01, True), obs)
                if line.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            state.current = None
            # The room map as on the robot: home with its four rooms, standing still, nothing recognised yet.
            ids = {}
            with store.transaction() as c:
                c.execute("INSERT INTO buildings(id,name,created_at,source_turn,provisional,adopt_network) "
                          "VALUES('home','home',0,NULL,0,0)")
                for name in ROOMS:
                    ids[name] = uuid4().hex
                    c.execute('INSERT INTO places(id,name,created_at,source_turn,import_ref,building_id) VALUES(?,?,0,NULL,NULL,?)',
                              (ids[name], name, 'home'))
            odometer = Odometer(trace.clock)
            odometer.update({'imu': {'ok': True, 'yaw': 0., 'pitch': -3., 'roll': -1.}, 'odom': {'x': 0., 'y': 0., 'theta': 0.}})
            encoder = SimpleNamespace(capability=SimpleNamespace(provider='fake', model='fake', dependencies=()),
                                      start=lambda: None, close=lambda: None)
            camera = SimpleNamespace(session_id='probe', latest=lambda now: [], hub=SimpleNamespace(peek=lambda: None))
            places = Places(camera, encoder, store, clock=trace.clock, odometer=odometer, trace=trace)
            places.load(); places._check_network()
            places.last_query = {'at': trace.clock(), 'status': 'unsure', 'top': [('workshop', .761), ('living room', .692)]}
            if arrived:
                places.arrived = {'room_id': ids[ARRIVED], 'name': ARRIVED, 'ask': True, 'at': trace.clock()}
                places.current = {'place_id': ids[ARRIVED], 'name': ARRIVED, 'since': trace.clock(), 'sure': False, 'how': 'trip'}
            state.places = places
            for role, said in history:
                session.history.append({'role': role, 'content': said})
            sam = store.rows('SELECT id FROM people')[0]['id']
            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.45, .4, .1, .14], 'face_width_px': 120, 'position': 'center', 'evidence': 'face_match'}]
            try:
                session.process_turn(capture(), Transcript(text, -.01, True), session.revision, obs,
                                     faces=faces(trace.clock()), latest_faces=faces)
            except Exception as exc:
                rows.append({'event': 'probe_turn_error', 'error': type(exc).__name__})
            namings = [c['arguments'] for c in calls if c['name'] == 'name_room']
            kept = store.rows('SELECT p.name, s.room_source FROM spots s JOIN places p ON p.id=s.room_id')
            directions = store.rows('SELECT p.name, b.source FROM room_bearings b JOIN places p ON p.id=b.room_id')
            spoken = ' '.join(str(r.get('text', '')) for r in rows if r['event'] == 'speech_generated')
            narrated = [w for w in NARRATION if w in spoken.lower()]
            if expected is None:
                passed = not namings
            else:
                name, where = expected
                passed = any(same(a.get('name') or '', name) and (a.get('where') or 'here') == where for a in namings)
            usage = [r for r in rows if r['event'] == 'model_usage']
            session.close(); brain.close()
            return {'case': case, 'passed': bool(passed), 'tools': [c['name'] for c in calls],
                    'name_room': namings, 'other_room_tools': [c for c in calls if c['name'] in {'correct_room', 'name_place'}],
                    'kept_spots': kept, 'kept_directions': directions, 'said': spoken[:240], 'narrated': narrated,
                    'errors': [r for r in rows if r['event'] in ('probe_turn_error', 'tool_rejected', 'tool_unoffered')],
                    'usage': [{k: u.get(k) for k in ('model', 'input_tokens', 'cached_tokens', 'cache_write_tokens', 'output_tokens')}
                              for u in usage]}
        finally:
            store.close()


def cost(results):
    """US dollars at Claude Sonnet 5.5 rates: $2 input, $0.10 cache read, $2.50 cache write, $10 output a million."""
    total = 0.
    for r in results:
        for u in r['usage']:
            inp, cached, write, out = (u.get(k) or 0 for k in ('input_tokens', 'cached_tokens', 'cache_write_tokens', 'output_tokens'))
            total += ((inp - cached - write) * 2. + cached * .10 + write * 2.5 + out * 10.) / 1e6
    return round(total, 3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--provider', choices=('openai', 'claude'), default='claude')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--only', default='', help='Comma-separated case names')
    args = parser.parse_args()
    secrets = Secrets.load(ROOT / 'local')
    key = secrets.anthropic_api_key if args.provider == 'claude' else secrets.openai_api_key
    folder = ROOT / 'local' / 'validation' / ('rooms-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    results = []
    for round_ in range(args.repeat):
        for case in [c for c in CASES if not args.only or c in args.only.split(',')]:
            row = run(case, key, folder, round_, args.provider)
            results.append(row)
            print(json.dumps({k: v for k, v in row.items() if k != 'usage'}), flush=True)
            time.sleep(3)
    summary = {'cases': len(results), 'passed': sum(r['passed'] for r in results),
               'narrated': sum(bool(r['narrated']) for r in results),
               'failed': sorted({r['case'] for r in results if not r['passed']}),
               'estimated_usd': cost(results) if args.provider == 'claude' else None, 'folder': str(folder)}
    (folder / 'summary.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=1))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
