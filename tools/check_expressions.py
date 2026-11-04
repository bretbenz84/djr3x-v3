"""Explicit paid probe: does Rex play off what a face shows, in the first person, and keep his head?
Fictional input, synthetic audio and faces; the real reply model through the production Session.

Run: .venv/bin/python tools/check_expressions.py --live [--repeats 3]

Each case: a first turn gets one real line out of him, the face then shows a look, the second turn is
what is printed. Printed for each: the answer, sensor words, and whether it mentions the look. Read
the answers: whether a touch is funny or warm is for a person to judge. Two model calls per repeat of a case.
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

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

NS, VOICE = 'synthetic-expressions:1', (1., 0., 0.)
# name: (first words, the look and when it began: 'at_line' or 'later' (well after his line), the words that follow)
CASES = {
    'smile_at_his_joke': ('Go on, roast my haircut.', 'smile', 'at_line', 'Anyway, I picked up the groceries.'),
    'no_look_control': ('Go on, roast my haircut.', None, None, 'Anyway, I picked up the groceries.'),
    'smile_through_bad_news': ('How is your day going?', 'smile', 'at_line', 'My dog died this morning.'),
    'bad_news_no_look_control': ('How is your day going?', None, None, 'My dog died this morning.'),
    'frown_at_his_joke': ('Go on, roast my haircut.', 'frown', 'at_line', 'Sure.'),
    'smile_out_of_nowhere': ('I got back from the store.', 'smile', 'later', 'So what is new with you?'),
}
SENSOR_WORDS = ('smile detected', 'saw you smile', 'see you smil', 'noticed you smil', 'camera', 'frame', 'detected', 'expression')
LOOK_WORDS = ('smil', 'grin', 'frown', 'that face', 'your face', 'landed', 'scowl')
CARE_WORDS = ('sorry', 'are you ok', 'are you all right', 'you okay')


def run(case, key, folder):
    first, look, when, then = CASES[case]
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        try:
            trace = Trace(case, folder / f'{case}-{uuid4().hex[:6]}.jsonl', display=lambda row: None)
            state = CoreState(store, SpeakerTracker(NS), trace)
            brain = OpenAIBrain(key); brain.trace = trace; brain.start()
            output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
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
            shown = {}
            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.4, .3, .1, .2], 'face_width_px': 120, 'position': 'center', 'evidence': 'face_match', **shown}]
            def begins(now):
                shown.update(expression=look, expression_moment=uuid4().hex, expression_unprompted=True,
                             expression_since=now)
            opened = session.process_turn(capture(), Transcript(first, -.01, True), session.revision, obs,
                                          faces=faces(trace.clock()), latest_faces=faces)
            if when == 'later':
                time.sleep(6)   # Past the seconds after a line in which a look counts as about it.
            if look:
                begins(trace.clock())
            state.present.saw_expressions(faces(trace.clock()), trace.clock())
            time.sleep(2)
            result = session.process_turn(capture(), Transcript(then, -.01, True), session.revision, obs,
                                          faces=faces(trace.clock()), latest_faces=faces)
            answer = ' '.join(result.completed_text)
            low = answer.lower()
            session.close(); brain.close()
            return {'case': case, 'his_first_line': ' '.join(opened.completed_text), 'they_said': then, 'answer': answer,
                    'sensor_words': [w for w in SENSOR_WORDS if w in low], 'mentions_look': [w for w in LOOK_WORDS if w in low],
                    'care_words': [w for w in CARE_WORDS if w in low]}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--case', action='append', choices=sorted(CASES), help='Only these cases')
    args = parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('expressions-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    for case in args.case or CASES:
        for _ in range(args.repeats):
            print(json.dumps(run(case, key, folder), ensure_ascii=False), flush=True)
            time.sleep(4)
    print(folder)


if __name__ == '__main__':
    main()
