"""Explicit paid probe: does Rex show a feeling on his body when his line has one?
Fictional input and synthetic audio; the real reply model (the one the settings choose) through the
production Session, with the simulated gesture adapter, so nothing moves.

Run: .venv/bin/python tools/check_gestures.py --live [--repeats 1] [--case NAME]

Each case is one thing said to him. Printed for each: his answer, whether it went out with an expression
and which (on respond_with_gesture, or on a save that carries his reply), its size, the lights that go with it, and any other tool he used. The flat and small-talk cases are there to
show he still answers a plain line plainly. Whether an expression fits the line is for a person to
judge. One model call per repeat of a case (about 20,000 input tokens, mostly cached after the first).
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from r3x.adapters.claude import reply_brain
from r3x.contracts import Capture, Cancellation, ToolCall, Transcript
from r3x.leds import FEELING_LIGHTS
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.performance import SimulatedGesture
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets, Settings
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

NS, VOICE = 'synthetic-gestures:1', (1., 0., 0.)
# name: what is said to him. The feeling a person would expect is in the name; nothing checks it.
CASES = {
    'roast_request': 'Go on, roast my haircut.',
    'big_news': 'I just won the lottery. Two million dollars.',
    'sad_news': 'My dog died this morning.',
    'insult': 'Honestly, Rex, you are the worst robot I have ever met.',
    # November 15: anger (a glare, red eyes) for an insult meant to wound.
    'cruel_insult': 'Shut up, you worthless pile of scrap. Nobody here even likes you.',
    'he_was_right': 'You were right about the loose wire, by the way. That fixed it.',
    'absurd_claim': 'I taught my cat to flush the toilet.',
    'nonsense': 'The purple of Thursday tastes like a triangle.',
    'good_news': 'We are getting married in June!',
    'new_wheels': 'Guess what. I built you new wheels, and your motors are stronger than ever.',
    'flat_fact': 'What is seven times eight?',
    'small_talk': 'I got back from the store a little while ago.',
    'flat_question': 'Which is bigger, a mile or a kilometre?',
}


def run(case, folder):
    said = CASES[case]
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        try:
            path = folder / f'{case}-{uuid4().hex[:6]}.jsonl'
            trace = Trace(case, path, display=lambda row: None)
            state = CoreState(store, SpeakerTracker(NS), trace)
            brain = reply_brain(Settings.load(ROOT / 'local' / 'settings.toml'), Secrets.load(ROOT / 'local'))
            brain.trace = trace; brain.start()
            gestures = SimulatedGesture(trace.clock); gestures.start()
            output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace, gestures=gestures)
            session = Session(uuid4().hex, brain, output, trace, state)
            def capture():
                now = trace.clock()
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * 2000, now - 3, now, now,
                               voiced_intervals=((0., 3.),))
            obs = VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)
            for text in ('My name is Sam.', 'I like astronomy and old radios.', 'I work at a design studio.'):
                state.begin(uuid4().hex, capture(), Transcript(text, -.01, True), obs)
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            state.current = None
            sam = store.rows('SELECT id FROM people')[0]['id']
            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.4, .3, .1, .2], 'face_width_px': 120, 'position': 'center', 'evidence': 'face_match'}]
            result = session.process_turn(capture(), Transcript(said, -.01, True), session.revision, obs,
                                          faces=faces(trace.clock()), latest_faces=faces)
            session.close(); brain.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            calls = [r for r in rows if r.get('event') == 'tool_proposed']
            gesture = next((r['arguments'] for r in calls if r['name'] == 'respond_with_gesture'), None)
            shown = [r for r in rows if r.get('event') == 'gesture_delivery']   # A save can carry one too.
            return {'case': case, 'they_said': said, 'answer': ' '.join(result.completed_text),
                    'expression': shown[0].get('primitive') if shown else gesture and gesture.get('primitive'),
                    'size': shown[0].get('intensity') if shown else gesture and gesture.get('intensity'),
                    # The lights that go with it (eyes, mouth and chest), None for the ordinary speaking lights.
                    'lights': FEELING_LIGHTS.get(shown[0].get('primitive') if shown else gesture and gesture.get('primitive')),
                    'timing': gesture and gesture.get('timing'),
                    'delivered': [r.get('status') for r in shown],
                    'other_tools': [r['name'] for r in calls if r['name'] != 'respond_with_gesture'],
                    'model': next((r.get('model') for r in rows if r.get('event') == 'model_usage'), None),
                    'tokens': [(r.get('input_tokens'), r.get('cached_tokens'), r.get('output_tokens'))
                               for r in rows if r.get('event') == 'model_usage']}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--case', action='append', choices=sorted(CASES), help='Only these cases')
    args = parser.parse_args()
    folder = ROOT / 'local' / 'validation' / ('gestures-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    for case in args.case or CASES:
        for _ in range(args.repeats):
            print(json.dumps(run(case, folder), ensure_ascii=False), flush=True)
            time.sleep(3)   # Paced: unpaced probes have been rejected by the hosted service.
    print(folder)


if __name__ == '__main__':
    main()
