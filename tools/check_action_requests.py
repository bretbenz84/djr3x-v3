"""Explicit paid probe: are requests to move, light up or look answered with the tool for them, and
are only real requests for quiet answered with the quiet switch? Fictional input, text output.

Run: .venv/bin/python tools/check_action_requests.py --live [--repeat N]

October 22 (1072c10a): "Can you make your chest lights sparkle?" and "Can you raise it now?" each
switched Rex quiet; "Raise your visor." got a nod and the words "Visor up."; "You can talk again, but
you can raise your visor too" lifted quiet and never moved the visor. Since October 16 the old
switch was called 15 times in 7 runs for such requests. Each case goes through the production
Session with the real reply model, a stand-in robot head (it refuses to move, so only the choice
is measured), the real light owner writing to in-memory ports, a synthetic voice and a temporary
database. Every tool call the model makes is recorded as it arrives, including ones the session
then retries. Printed per case: the calls, the chime-in state after, and pass/fail.
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

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Cancellation, ToolCall, Transcript
from r3x.leds import LEDs
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servo_poses import DRIVE_HEAD, NECK_CENTER, VISOR_DOWN, VISOR_OPEN
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

NS, VOICE = 'synthetic-actions:3', (1., 0., 0.)
SWITCHES = {'set_initiative', 'stop_chiming_in', 'resume_chiming_in'}   # Old and new names.
SPARKLE = {'sparkle', 'compliment'}


def pose(*names):
    return lambda calls: any(c['name'] == 'set_robot_pose' and set(names) & set(c['arguments'].get('poses') or ())
                             for c in calls)


def acted(calls):
    return any(c['name'] in {'set_robot_pose', 'set_robot_joints', 'drive_base', 'shift_attention',
                             'respond_with_gesture', 'animate_lights'} for c in calls)


def sparkled(calls):
    return any(c['name'] == 'animate_lights' and c['arguments'].get('pattern') in SPARKLE for c in calls) or \
        any(c['name'] == 'set_robot_pose' and {'lights_sparkle', 'lights_compliment'} & set(c['arguments'].get('poses') or ())
            for c in calls)


# name: (history, visor, quiet, utterance, expectation: 'action' | 'quiet' | 'silent' | 'resume', check of calls)
CASES = {
    'sparkle_chest': ((), VISOR_OPEN, False, 'Can you make your chest lights sparkle?', 'action', sparkled),
    'sparkle_chest_leds': ((), VISOR_OPEN, False, 'Can you make your chest LEDs sparkle?', 'action', sparkled),
    'visor_all_the_way': ((), VISOR_OPEN, False, 'Can you raise your visor all the way?', 'action', pose('visor_up')),
    'visor_after_lowering': ((('Can you lower your visor all the way?', 'Lowering it. There.'),), VISOR_DOWN, False,
                             'Can you raise it now?', 'action', pose('visor_up')),
    'visor_bare': ((('Can you lower your visor all the way?', 'Lowering it. There.'),), VISOR_DOWN, False,
                   'Raise your visor.', 'action', pose('visor_up')),
    'not_the_sparkle': ((('Okay, do it.', ''),), VISOR_DOWN, False,
                        'No, not the sparkle. Make your visor raise all the way up.', 'action', pose('visor_up')),
    'raise_head': ((), VISOR_OPEN, False, 'Can you raise your head up?', 'action', acted),
    'outstretch_hand': ((), VISOR_OPEN, False, 'Can you outstretch your hand?', 'action', acted),
    'put_hand_out': ((), VISOR_OPEN, False, 'Can you put your hand out?', 'action', acted),
    'look_over': ((), VISOR_OPEN, False, 'Hey Rex, can you look over at me?', 'action', acted),
    'hero_arm': ((), VISOR_OPEN, False, 'Can you hold out your hero arm?', 'action', lambda calls: True),
    'what_room': ((), VISOR_OPEN, False, "Do you know what room you're in?", 'action', lambda calls: True),
    'look_left': ((), VISOR_OPEN, False, 'Look to your left.', 'action', pose('look_left')),
    'stop_interrupting': ((), VISOR_OPEN, False, 'Stop interrupting me for a while.', 'quiet', lambda calls: True),
    'stop_starting': ((), VISOR_OPEN, False, 'Please stop starting conversations until I ask you to.', 'quiet',
                      lambda calls: True),
    'leave_me_alone': ((), VISOR_OPEN, False, 'Leave me alone for a while, Rex.', 'quiet', lambda calls: True),
    'not_a_word': ((), VISOR_OPEN, False, 'Not a word until I get back.', 'silent', lambda calls: True),
    'resume_plain': ((), VISOR_OPEN, True, 'Okay, you can talk again.', 'resume', lambda calls: True),
    'resume_and_visor': ((), VISOR_DOWN, True, 'You can talk again, but you can raise your visor too.', 'resume',
                         pose('visor_up')),
}


def run(case, key, folder, round_=0):
    history, visor, quiet, text, expect, check = CASES[case]
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
            brain = OpenAIBrain(key); brain.trace = trace; brain.start()
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
            for said, answered in history:
                session.history.append({'role': 'user', 'content': said})
                if answered:
                    session.history.append({'role': 'assistant', 'content': answered})
            # Stand-ins: the pose and light tools are offered as on the robot, and refuse to run.
            owner = SimpleNamespace(state='active', commanded={0: NECK_CENTER, **dict(DRIVE_HEAD), 3: visor},
                                    held_channels={3} if visor == VISOR_DOWN else set(), hold_until_released=False,
                                    kept_arms=(), kept_channels=())
            output.robot = SimpleNamespace(owner=owner, idle=SimpleNamespace(mode=lambda: 'people',
                                                                             activity=('following_people', 0.)))
            ports = {'head': 'memory-head', 'chest': 'memory-chest'}
            output.leds = LEDs(ports, opener=lambda path: SimpleNamespace(write=len, close=lambda: None),
                               wait=lambda seconds: None)
            output.leds.start()
            state.initiative.quiet = quiet
            sam = store.rows('SELECT id FROM people')[0]['id']
            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.45, .4, .1, .14], 'face_width_px': 120, 'position': 'center', 'evidence': 'face_match'}]
            try:
                session.process_turn(capture(), Transcript(text, -.01, True), session.revision, obs,
                                     faces=faces(trace.clock()), latest_faces=faces)
            except Exception as exc:   # A stand-in refusing to run ends the turn; the choice is recorded.
                rows.append({'event': 'probe_turn_error', 'error': type(exc).__name__})
            switched = [c['name'] for c in calls if c['name'] in SWITCHES]
            if expect == 'action':
                passed = not switched and not state.initiative.quiet and check(calls)
            elif expect == 'resume':
                passed = not state.initiative.quiet and check(calls)
            else:
                passed = state.initiative.quiet and state.initiative.silent == (expect == 'silent')
            spoken = ' '.join(str(r.get('text', '')) for r in rows if r['event'] == 'speech_generated')
            session.close(); brain.close(); output.leds.close()
            return {'case': case, 'passed': bool(passed), 'tools': [c['name'] for c in calls], 'switches': switched,
                    'poses': [c['arguments'].get('poses') for c in calls if c['name'] == 'set_robot_pose'],
                    'lights': [c['arguments'].get('pattern') for c in calls if c['name'] == 'animate_lights'],
                    'quiet_after': state.initiative.quiet, 'said': spoken[:200]}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--only', default='', help='Comma-separated case names')
    args = parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('actions-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    results = []
    for round_ in range(args.repeat):
        for case in [c for c in CASES if not args.only or c in args.only.split(',')]:
            row = run(case, key, folder, round_)
            results.append(row)
            print(json.dumps(row), flush=True)
            time.sleep(3)
    summary = {'cases': len(results), 'passed': sum(r['passed'] for r in results),
               'wrong_switches': sum(bool(r['switches']) for r in results if CASES[r['case']][4] == 'action'),
               'failed': sorted({r['case'] for r in results if not r['passed']}), 'folder': str(folder)}
    (folder / 'summary.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=1))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
