"""Explicit paid probe: asked what he is looking at, does Rex answer from his own sense of where he
looks (right_now.rex.looking), in the words of someone seeing, not a camera? Fictional input.

Run: .venv/bin/python tools/check_looking.py --live

October 14 (1662b007): his head tipped up during his own beat; asked "what are you looking at?" he
took a fresh look and recited "a man in a black shirt ... I don't see a pet in this frame". Three
cases go through the production Session with the real reply model, a stand-in robot head and
synthetic audio: an idle glance with a clock in the middle of his view, a look he chose for a
reason, and following the person's face. Printed: each answer, whether it says frame/image/camera,
and whether it asked for a new look.
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
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servo_poses import DRIVE_HEAD, NECK_CENTER
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

NS, VOICE = 'synthetic-looking:3', (1., 0., 0.)
CASES = {
    'idle_glance': ('glancing_away', -30., None),
    'chosen_look': ('holding_chosen_direction', 0., 'Briefly look upward while letting the astronomy detour go.'),
    'following': ('following_people', -4., None),
}
CAMERA_WORDS = ('frame', 'image', 'camera', 'picture', 'shot')


def run(case, key, folder):
    activity, neck, reason = CASES[case]
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        try:
            trace = Trace(case, folder / f'{case}.jsonl', display=lambda row: None)
            rows, emit = [], trace.emit
            def recording(event, **data):
                rows.append({'event': event, **data})
                emit(event, **data)
            trace.emit = recording
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
            owner = SimpleNamespace(state='probe-head', neck_degrees=lambda: neck, commanded={0: NECK_CENTER, **dict(DRIVE_HEAD)},
                                    held_channels=(), hold_until_released=False, kept_arms=())
            output.robot = SimpleNamespace(owner=owner, idle=SimpleNamespace(activity=(activity, trace.clock() - 4)))
            clock_row = {'label': 'clock', 'image_position': {'center_x': .52, 'center_y': .35}}
            state.objects = SimpleNamespace(snapshot=lambda min_samples=2: (clock_row,))
            if reason:
                state.present.rex_looked('up', reason, trace.clock())
            x = .08 if activity != 'following_people' else .48
            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [x, .55, .07, .1], 'face_width_px': 120, 'position': 'left', 'evidence': 'face_match'}]
            session.process_turn(capture(), Transcript("That's funny.", -.01, True), session.revision, obs,
                                 faces=faces(trace.clock()), latest_faces=faces)
            time.sleep(4)
            result = session.process_turn(capture(), Transcript('What are you looking at?', -.01, True), session.revision,
                                          obs, faces=faces(trace.clock()), latest_faces=faces)
            answer = ' '.join(result.completed_text)
            tools = [r.get('name') for r in rows if r['event'] == 'tool_proposed']
            session.close(); brain.close()
            return {'case': case, 'answer': answer, 'camera_words': [w for w in CAMERA_WORDS if w in answer.lower()],
                    'tools': tools}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    args = parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('looking-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    for case in CASES:
        print(json.dumps(run(case, key, folder)))
        time.sleep(4)
    print(folder)


if __name__ == '__main__':
    main()
