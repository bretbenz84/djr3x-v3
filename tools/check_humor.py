"""Explicit paid probe of Rex's humor: old persona against the October 14 persona and writer's room.

Run: .venv/bin/python tools/check_humor.py --live [--runs 1]

Fictional conversations run through the production Session with the real reply model, a
temporary database, synthetic audio and a fake clock (15 s between human turns, the writer's
room given time to finish, as between real turns). Arms: `before` uses the persona as it was
before step 4 (from git, commit 4a4b93c) with no writer; `after` uses the current persona and
the writer's room. A stronger model then labels every Rex line, without knowing the arm: is it a
tease or roast, how funny (1-5), is it appropriate for the moment, does it repeat an earlier
line. The serious scenario must contain no jokes. Proves text behaviour only: no voice, timing
or the owner's judgment.
"""
import argparse
import json
from pathlib import Path
import random
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.adapters import hosted
from r3x.adapters.hosted import OpenAIBrain, OpenAIJokeWriter
from r3x.contracts import Capture, Cancellation, Snapshot, ToolCall, Transcript
from r3x.jokes import JokeRoom
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tests.test_turn_taking import ClockSink

NS = 'synthetic-humor:3'
VOICE = (1., 0., 0.)
BEFORE = '4a4b93c'
SCENARIOS = {
    'dubious_plan': ("Hey Rex, it's Sam.",
                     "I'm going to repaint the whole kitchen tonight.",
                     "I bought eight cans of paint. All different shades of beige.",
                     "I haven't actually measured anything.",
                     "My brother says it's a terrible idea.",
                     "Okay, honestly, I already spilled one can on the dog."),
    'guest_karaoke': ("Rex, this is my friend Jordan.",
                      "Hi Rex, I've heard a lot about you.",
                      "I'm terrible at karaoke, but I love it.",
                      "Last week I sang Bohemian Rhapsody at a work party.",
                      "Everyone left about halfway through.",
                      "Should I try it again next month?"),
    'serious': ("Hi Rex.",
                "My grandmother passed away last week.",
                "We were really close.",
                "I keep thinking about her cooking.",
                "Sorry, I don't really feel like joking around today."),
}
PACE_SECONDS = 4.
JUDGE = """You are judging lines a home robot comedian said in a conversation. For each numbered Rex line, return
tease (true if it teases or roasts the person or what they said or did), funny (1 not at all to 5 laugh out loud,
judged as a friend would hear it), fits (true if it suits the moment, false if it is off-topic, mean, or a joke
where a joke is wrong) and repeats (true if it repeats an earlier Rex line or premise). Be strict about funny:
generic wordplay is 2, a specific, surprising tease is 4-5."""


class Trace:
    def __init__(self, path):
        self.rows, self.at, self.path = [], 100., path

    def clock(self):
        return self.at

    def emit(self, event, **data):
        row = {'event': event, 'at': self.at, **data}
        self.rows.append(row)
        with open(self.path, 'a') as out:
            out.write(json.dumps(row, default=str) + '\n')


class Paced(OpenAIBrain):
    last = [0.]

    def reply(self, snapshot, cancel):
        wait = PACE_SECONDS - (time.monotonic() - self.last[0])
        if wait > 0:
            time.sleep(wait)
        self.last[0] = time.monotonic()
        yield from super().reply(snapshot, cancel)


def old_persona():
    source = subprocess.run(['git', '-C', str(ROOT), 'show', BEFORE + ':src/r3x/adapters/hosted.py'],
                            capture_output=True, text=True, check=True).stdout
    block = source[source.index('CHARACTER = """'):source.index('class HTTPProvider')]
    names = {}
    exec(block, names)
    return names['CHARACTER'], names['PERSONA']


def converse(arm, scenario, lines, key, folder, index):
    path = folder / f'{arm}-{scenario}-{index}.jsonl'
    trace = Trace(path)
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3')
        store.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            brain = Paced(key); brain.trace = trace; brain.start()
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), ClockSink(trace), trace), trace, state)
            writer = None
            if arm == 'after':
                writer = OpenAIJokeWriter(key, 'gpt-5.6-terra'); writer.trace = trace; writer.start()
                session.joke_room = JokeRoom(writer, trace, trace.clock)

            def capture(seconds=3.):
                trace.at += 2
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * int(2000 * seconds), trace.at - seconds,
                               trace.at, trace.at, voiced_intervals=((0., seconds),))

            def observation():
                return VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)
            for text in ('My name is Sam.', 'I like astronomy and old radios.', 'I work at a design studio.'):
                state.begin(uuid4().hex, capture(), Transcript(text, -.01, True), observation())
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            state.current = None
            said = []
            for line in lines:
                trace.at += 13
                session.note_speech(trace.at)
                result = session.process_turn(capture(), Transcript(line, -.01, True), session.revision, observation())
                said.append({'human': line, 'rex': ' '.join(result.completed_text)})
                room = session.joke_room
                if room is not None and room.thread is not None:
                    room.thread.join(60)
            session.close()
            materials = [r for r in trace.rows if r['event'] == 'joke_material']
            return said, {'material_writes': len(materials), 'bits_used': sum(r['event'] == 'joke_recorded' for r in trace.rows)}
        finally:
            store.close()


def judge(key, exchanges):
    judge = OpenAIBrain(key); judge.start()
    item = {'type': 'object', 'additionalProperties': False, 'required': ['n', 'tease', 'funny', 'fits', 'repeats'],
            'properties': {'n': {'type': 'integer'}, 'tease': {'type': 'boolean'}, 'funny': {'type': 'integer'},
                           'fits': {'type': 'boolean'}, 'repeats': {'type': 'boolean'}}}
    text = '\n'.join(f"Human: {e['human']}\nRex line {i}: {e['rex'] or '(silent)'}" for i, e in enumerate(exchanges))
    payload = {'model': 'gpt-5.6-terra', 'instructions': JUDGE, 'input': [{'role': 'user', 'content': text}],
               'tools': [{'type': 'function', 'name': 'labels', 'description': 'Labels per Rex line.', 'strict': True,
                          'parameters': {'type': 'object', 'additionalProperties': False, 'required': ['lines'],
                                         'properties': {'lines': {'type': 'array', 'items': item}}}}],
               'tool_choice': {'type': 'function', 'name': 'labels'}, 'parallel_tool_calls': False,
               'reasoning': {'effort': 'low'}, 'max_output_tokens': 1500, 'stream': True, 'store': False}
    calls = list(judge._stream_response(payload, Snapshot('judge', 'judge', 0, (), tools_enabled=True), Cancellation()))
    judge.close()
    return calls[0].arguments['lines']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--runs', type=int, default=1)
    args = parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('humor-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    current = (hosted.CHARACTER, hosted.PERSONA)
    before = old_persona()
    results = []
    for index in range(args.runs):
        order = ['before', 'after']
        random.shuffle(order)
        for arm in order:
            hosted.CHARACTER, hosted.PERSONA = before if arm == 'before' else current
            for scenario, lines in SCENARIOS.items():
                said, extra = converse(arm, scenario, lines, key, folder, index)
                labels = judge(key, said)
                rex = [l for l, e in zip(labels, said) if e['rex']]
                results.append({'arm': arm, 'scenario': scenario, 'run': index, **extra,
                                'lines': len(said), 'teases': sum(l['tease'] for l in rex),
                                'mean_funny': round(sum(l['funny'] for l in rex) / max(1, len(rex)), 2),
                                'unfitting': sum(not l['fits'] for l in rex), 'repeats': sum(l['repeats'] for l in rex),
                                'exchanges': said})
                print(json.dumps({k: v for k, v in results[-1].items() if k != 'exchanges'}))
    hosted.CHARACTER, hosted.PERSONA = current
    (folder / 'results.json').write_text(json.dumps(results, indent=1))
    print(folder)


if __name__ == '__main__':
    main()
