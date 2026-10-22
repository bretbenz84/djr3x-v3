"""Explicit paid probe: once the talk has moved on, does a planned beat still speak, and without
jumping back to the old subject? Fictional input, text output.

Run: .venv/bin/python tools/check_stale_beats.py --live [--runs 2]

October 22 (1072c10a): told "if no natural bridge exists, remain_silent", the performer let 7 of 11
moved-on beats go and Rex sat silent for up to 52 s. Here a fixed eight-beat telescope plan (no
planner call) runs through the production Session and runtime with the real reply model, a
fictional person (Sam), a temporary database, synthetic audio and a fake clock. Sam answers the
first beat, then says two lines on something new (his dogs, work, a film, dinner, a visit) before
each lull, so every later beat comes after the talk moved on. Reported per run: each beat, whether it was
marked moved on, what was said, and whether it named the telescope; then the share of moved-on
spoken beats that spoke and that went back to the telescope. Text behaviour only, not timing.
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.contracts import Capture, Cancellation, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tools.check_agency_beats import NS, VOICE, Paced, Trace
from tests.test_turn_taking import ClockSink

OLD_SUBJECT = ('telescope', 'tube', 'lens', 'eyepiece', 'stargaz', 'mirror', 'optics')
BEATS = ('Tease Sam about owning a telescope this enormous for a living room.',
         'Ask what the telescope is usually pointed at.',
         'Offer a vain pilot opinion about trusting a long tube with precision navigation.',
         'Wonder aloud whether the telescope ever spots anything more interesting than the neighbors.',
         'Share a strong opinion that a telescope this big deserves a dramatic name.',
         'Joke that the telescope gets more attention in this house than Rex does.',
         'Ask whether Sam has ever seen a comet through it.',
         'Tell a short made-up story about a navigation mishap involving a star chart.')
PLAN = {'goal_kind': 'catch_up', 'subject': 'the telescope', 'scope': 'audience',
        'narrative': "Sam's giant telescope is right there and I want to get nosy about it.",
        'objective': 'Keep Sam company by getting nosy about the telescope.', 'evidence_ids': [],
        'steps': [{'kind': 'contribute', 'instruction': text, 'evidence_ids': [],
                   'wake': {'when': 'quiet', 'seconds': 5}, 'follows_previous': False} for text in BEATS]}
NEXT = {**PLAN, 'subject': 'old radios', 'goal_kind': 'entertain',
        'narrative': 'Something new: old radios, one of his interests.', 'objective': 'Talk old radios.',
        'steps': [{**PLAN['steps'][0], 'instruction': "Share a vain opinion about old radios, one of Sam's interests."}]}


TALK = (("Ha, it's just where it fits. Anyway, my dogs have been chewing everything this week.",
         'Toby ate a whole shoe yesterday.'),
        ('Work has been brutal too, three deadlines this week.', 'And my boss wants the whole redesign by Friday.'),
        ('I finally watched that new space movie last night.', 'The ending was a total mess though.'),
        ("I'm thinking about making chili for dinner.", 'My partner says I put too much cumin in it.'),
        ('My sister is visiting next weekend.', "She's bringing her kids, so it'll be loud."))


class FixedPlanner:
    capability = SimpleNamespace(model='fixed-telescope-plan')
    reasoning_effort = 'none'
    trace = None
    def __init__(self): self.calls = 0
    def start(self): pass
    def plan(self, facts, cancel, images=()):
        self.calls += 1
        return json.loads(json.dumps(PLAN if self.calls == 1 else NEXT))


def run_once(index, key, folder):
    trace = Trace(folder / f'stale-{index}.jsonl')
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            brain = Paced(key); brain.trace = trace
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), ClockSink(trace), trace), trace, state)
            session.agency_planner = FixedPlanner()
            runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace)
            runtime.started_at = 0
            brain.start()
            def capture(seconds=3.):
                trace.at += 2
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * int(2000 * seconds), trace.at - seconds,
                               trace.at, trace.at, voiced_intervals=((0., seconds),))
            def observation(): return VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)
            for text in ('My name is Sam.', 'I like old radios.', 'I work at a design studio.'):
                state.begin(uuid4().hex, capture(), Transcript(text, -.01, True), observation())
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            state.current = None
            sam = next(p['id'] for p in store.rows('SELECT id FROM people'))
            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.45, .3, .1, .12], 'face_width_px': 192, 'learning_quality': False,
                         'needs_closer_look': False, 'position': 'center', 'evidence': 'face_match'}]
            runtime.camera = SimpleNamespace(latest=faces)
            seen_until = [trace.at]
            def human(text):
                trace.at += 3; session.note_speech(trace.at)
                session.process_turn(capture(), Transcript(text, -.01, True), session.revision, observation(),
                                     faces=faces(trace.at), latest_faces=faces)
                for entry in runtime.arrivals.seen.values(): entry['used'] = True
            def tick(seconds):
                trace.at += seconds
                while seen_until[0] < trace.at:
                    seen_until[0] = min(trace.at, seen_until[0] + 1.)
                    runtime.arrivals.observe(faces(seen_until[0]), seen_until[0])
                runtime.audio_seen_at = trace.at
                result = runtime.proactive_tick()
                if result is not None and result.status == 'prepared':
                    session.join_planning(120); trace.at += 1; runtime.audio_seen_at = trace.at
                    runtime.proactive_tick()
            human("Hi Rex, it's Sam.")
            tick(10)                                         # The plan, then its first beat.
            for first, second in TALK:                       # Two lines on something new before each lull.
                human(first)
                human(second)
                tick(9)
            session.close()
        finally:
            store.close()
    started, beats = {}, []
    for row in trace.rows:
        if row['event'] == 'agency_step_started':
            started[row['turn_id']] = row
        elif row['event'] == 'delivery' and row.get('turn_id') in started:
            step = started.pop(row['turn_id'])
            text = row.get('completed_text')
            text = ' '.join(text) if isinstance(text, list) else (text or '')
            beats.append({'index': step['index'], 'subject': step['subject'], 'moved_on': bool(step.get('overtaken')),
                          'status': row['status'], 'said': text,
                          'old_subject': any(w in text.lower() for w in OLD_SUBJECT)})
    return beats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--runs', type=int, default=1)
    args = parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('stale-beats-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    beats = []
    for index in range(args.runs):
        run = run_once(index, key, folder)
        beats += run
        for beat in run:
            print(json.dumps(beat), flush=True)
    moved = [b for b in beats if b['moved_on'] and b['subject'] == 'the telescope']
    spoke = [b for b in moved if b['said']]
    summary = {'beats': len(beats), 'moved_on_beats': len(moved), 'moved_on_spoken': len(spoke),
               'moved_on_back_to_telescope': sum(b['old_subject'] for b in spoke),
               'telescope_beats_spoken': sum(bool(b['said']) for b in beats if b['subject'] == 'the telescope'),
               'folder': str(folder)}
    (folder / 'summary.json').write_text(json.dumps({'summary': summary, 'beats': beats}, indent=1))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
