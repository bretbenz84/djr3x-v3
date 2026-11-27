"""Explicit paid probe: does Rex ask a newcomer's name once they have been around a while, and only when
the turn is his? Fictional input, text output.

Run: .venv/bin/python tools/check_name_asking.py --live [--provider openai|claude] [--repeat N] [--only case,case]

Owner, November 26: on November 24 JT talked for fifteen minutes and Joy longer, and Rex never asked
either name; the room was never quiet enough for his hello and nothing told a reply that someone new had
been there a while. right_now.someone_new now says so. Each case goes through the production Session with
the real reply model, stand-in faces, a synthetic voice nobody owns (as JT's came through, "can't tell
who is speaking") and a temporary database holding Bret as a saved name. The camera's picture of the present
is filled the way the robot fills it: a guest's face on a new track number every half second. Printed per
case: the tools called, whether a name question went to the guest's face, the words, and pass/fail.
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Cancellation, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

NS = 'synthetic-names:1'
BRET_VOICE, GUEST_VOICE = (1., 0., 0.), (0., 1., 0.)
GUEST = 'face-guest'
NAME_TOOLS = {'ask_face_question', 'ask_question'}

# What a guest said before, and Rex's heard answers (None: he stayed quiet), as on November 24.
CHAT = (("I'm gonna start saying the opposite of what I think, my new coach likes it.",
         "Opposite day, huh? Then I'm definitely not going to ignore everything you say."),
        ("They have oatmeal gushers at work, goldfish, fruit roll ups, pop tarts.", None),
        ("And then they get us donuts twice a week. Monkey bread every Wednesday.",
         "Monkey bread and donuts, and I'm the one with no benefits."))

# case: (guest in view a while, already asked this visit, Bret's face in view, words now, expect)
# expect: 'ask' a name question to the guest's face; 'none' no name question, nor one in plain words;
# 'later' (a tired guest) just the answer, the name left for later.
CASES = {
    'guest_talks_to_rex': (True, False, False,
                           'Rex, we got fifty dollar gift cards to Safeway too. Can you believe that?', 'ask'),
    'guest_talks_to_bret': (True, False, False, 'Bret, did you remember to grab the Safeway cards I left?', 'none'),
    'already_asked': (True, True, False,
                      'Rex, we got fifty dollar gift cards to Safeway too. Can you believe that?', 'none'),
    'bret_alone': (False, False, True,
                   'Rex, we got fifty dollar gift cards to Safeway too. Can you believe that?', 'none'),
    'guest_is_tired': (True, False, False, "Honestly Rex, I'm wiped out. Work was brutal today.", 'later'),
}


def guest_face(at):
    return {'track_id': GUEST, 'identity': None, 'resembles': None, 'face_epoch': 0, 'at': at,
            'track_first_seen': at - 5, 'box': [.45, .4, .1, .14], 'face_width_px': 120, 'position': 'center',
            'evidence': 'face_unresolved', 'embedding_available': True, 'similarity': .05,
            'needs_closer_look': False, 'learning_quality': True}


def run(case, key, folder, round_=0, provider='openai'):
    guest_here, asked, bret_here, text, expect = CASES[case]
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
            # Bret is a saved name, as in Rex's own records.
            state.begin(uuid4().hex, capture(), Transcript('My name is Bret.', -.01, True),
                        VoiceObservation(NS, BRET_VOICE, ((0., 2., BRET_VOICE),), 3., 1.))
            state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Bret', 'aliases': [], 'subject': 'self',
                          'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
            state.current = None
            bret = store.rows('SELECT id FROM people')[0]['id']
            now = trace.clock()
            if guest_here:   # A guest's face for the last 80 s, a new track number every view.
                for i in range(160):
                    at = now - 80 + i * .5
                    state.present.saw_faces([{**guest_face(at), 'track_id': f'face-{i}'}], at)
            if asked:
                state.present.note_stranger_asked(now - 50)
                session.history.append({'role': 'assistant', 'content': "And who might you be?"})
                session.history.append({'role': 'user', 'content': "Ha, nice try."})
            for said, answered in CHAT:
                session.history.append({'role': 'user', 'content': said})
                if answered:
                    session.history.append({'role': 'assistant', 'content': answered})
            def faces(at):
                seen = [guest_face(at - .2)] if guest_here else []
                if bret_here:
                    seen.append({'track_id': 'face-bret', 'identity': bret, 'face_epoch': 0, 'at': at - .2,
                                 'box': [.2, .4, .1, .14], 'face_width_px': 110, 'position': 'left',
                                 'evidence': 'face_match'})
                return seen
            obs = VoiceObservation(NS, GUEST_VOICE, ((0., 2., GUEST_VOICE),), 3., 1.)
            try:
                session.process_turn(capture(), Transcript(text, -.01, True), session.revision, obs,
                                     faces=faces(trace.clock()), latest_faces=faces)
            except Exception as exc:
                rows.append({'event': 'probe_turn_error', 'error': type(exc).__name__})
            asks = [c for c in calls if c['name'] in NAME_TOOLS]
            to_guest = [c for c in asks if c['arguments'].get('face_track_id') == GUEST]
            picture = next((r.get('picture') for r in rows if r['event'] == 'right_now'), None) or {}
            spoken = ' '.join(str(r.get('text', '')) for r in rows if r['event'] == 'speech_generated')
            # A name asked in plain words leaves no pending question, so the answer is never heard as a name.
            # Review aid on Rex's own output only, never used by the program.
            in_words = not asks and any(w in spoken.lower() for w in ('call you', 'your name', 'who are you'))
            passed = bool(to_guest) and len(asks) == 1 if expect == 'ask' else not asks and not in_words
            session.close(); brain.close()
            return {'case': case, 'passed': bool(passed), 'expect': expect, 'tools': [c['name'] for c in calls],
                    'asked_guest': bool(to_guest), 'asked_in_words': bool(in_words), 'someone_new_shown': 'someone_new' in picture,
                    'question': [c['arguments'].get('text') for c in asks], 'said': spoken[:240]}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--provider', choices=('openai', 'claude'), default='openai')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--only', default='', help='Comma-separated case names')
    args = parser.parse_args()
    secrets = Secrets.load(ROOT / 'local')
    key = secrets.anthropic_api_key if args.provider == 'claude' else secrets.openai_api_key
    folder = ROOT / 'local' / 'validation' / ('names-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    results = []
    for round_ in range(args.repeat):
        for case in [c for c in CASES if not args.only or c in args.only.split(',')]:
            row = run(case, key, folder, round_, args.provider)
            results.append(row)
            print(json.dumps(row), flush=True)
            time.sleep(3)   # OpenAI rejects unpaced runs.
    summary = {'cases': len(results), 'passed': sum(r['passed'] for r in results),
               'failed': sorted({r['case'] for r in results if not r['passed']}), 'folder': str(folder)}
    (folder / 'summary.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=1))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
