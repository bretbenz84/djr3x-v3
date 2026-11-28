"""Explicit paid probe: does Rex ask about holiday plans when one is near, keep the plan, and ask how it
went after? Fictional Sam, text output.

Run: .venv/bin/python tools/check_plans_question.py --live [--repeat N] [--only case,case]

Owner, November 27: "a friend might ask a week before thanksgiving 'any plans for the big turkey day?'
and then after it's passed, he should ask how it went if plans were shared." Each case goes through the
production Session with the real reply model (llm_provider openai), a temporary database holding Sam
(known by voice and face) and a fixed wall clock. Printed per case: tools called, the plans topic asked,
the saved date, the words, and pass/fail.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x import curiosity
from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.proactive import Opportunity
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tests.test_turn_taking import ClockSink
from tools.check_humor import Paced, Trace

NS = 'synthetic-plans:3'
VOICE = (1., 0., 0.)
TOPIC = 'plans_thanksgiving_2026'
QUESTION = 'Thanksgiving next week, Sam. Any big plans for turkey day?'
WEEK_BEFORE, DAY_AFTER, PLAIN_DAY = '2026-11-19T18:00', '2026-11-27T12:00', '2026-09-16T18:00'

# case: (wall clock, kind, Sam's words or None for an arrival, plan saved before, expect)
CASES = {
    'hello_week_before': (WEEK_BEFORE, 'arrival', None, False, 'ask'),
    'floor_week_before': (WEEK_BEFORE, 'turn', "Eh, I've got nothing. You pick something to talk about, Rex.", False,
                          'ask'),
    'answer_saved': (WEEK_BEFORE, 'answer', "Going to my mom's in Sacramento. She does the whole spread.", False,
                     'save'),
    'nothing_planned': (WEEK_BEFORE, 'answer', 'Nothing really, just staying home and sleeping.', False, 'no_save'),
    'hello_after': (DAY_AFTER, 'arrival', None, True, 'how_was_it'),
    'hello_plain_day': (PLAIN_DAY, 'arrival', None, False, 'no_ask'),
}


def run(case, key, folder, round_):
    when, kind, words, saved_before, expect = CASES[case]
    trace = Trace(folder / f'{case}-{round_}.jsonl')
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            told = datetime.fromisoformat(WEEK_BEFORE if saved_before else when)
            state.wall_clock = lambda: told.timestamp()
            state.calendar_today = lambda: told.date()
            brain = Paced(key); brain.trace = trace; brain.start()
            calls, reply = [], brain.reply
            def recorded(snapshot, cancel):
                for item in reply(snapshot, cancel):
                    if isinstance(item, ToolCall):
                        calls.append({'name': item.name, 'arguments': dict(item.arguments)})
                    yield item
            brain.reply = recorded
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), ClockSink(trace), trace), trace, state)
            def capture(seconds=3.):
                trace.at += 2
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * int(2000 * seconds), trace.at - seconds,
                               trace.at, trace.at, voiced_intervals=((0., seconds),))
            voice = lambda: VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)
            for text in ('My name is Sam.', 'I work at a design studio.', 'I build my own telescopes.'):
                state.begin(uuid4().hex, capture(), Transcript(text, -.01, True), voice())
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            sam = store.rows('SELECT id FROM people')[0]['id']
            if saved_before:   # Asked and answered the week before.
                state.begin(uuid4().hex, capture(), Transcript("Going to my mom's in Sacramento.", -.01, True), voice())
                state.execute(ToolCall(uuid4().hex, 'remember_followup', {'predicate': 'thanksgiving plan',
                    'value': "Thanksgiving at his mom's in Sacramento", 'kind': 'plan', 'on_date': '2026-11-26',
                    'due_in_minutes': None, 'due_by': None, 'audience': 'subject_only',
                    'source_turn_id': state.current[0], 'other_person': None, 'subject_reference': None}),
                    store.revision, Cancellation())
                with store.transaction() as conn:
                    curiosity.record(conn, {'topic': TOPIC}, sam, told.timestamp())
            store.connection.execute('DELETE FROM conversation_exchanges')   # The setup's lines are not this run.
            store.connection.commit()
            state.current = state.conversation_source = None
            now = datetime.fromisoformat(when)
            state.wall_clock = lambda: now.timestamp()
            state.calendar_today = lambda: now.date()
            trace.at += 40
            face = {'at': trace.at, 'identity': sam, 'track_id': 'face-sam', 'face_epoch': 0, 'face_width_px': 150,
                    'box': [.45, .3, .1, .12], 'position': 'center', 'learning_quality': False}
            state.present.saw_faces([face], trace.at)
            if kind == 'answer':
                row = next(r for r in curiosity.still_unknown(store, sam, 'Sam', [], now.timestamp())
                           if r['topic'] == TOPIC)
                with store.transaction() as conn:
                    curiosity.record(conn, row, sam, now.timestamp())
                session.history.append({'role': 'user', 'content': 'Hey Rex.'})
                session.history.append({'role': 'assistant', 'content': QUESTION})
                session.last_question = {'text': QUESTION, 'row': row, 'person_id': sam, 'at': trace.at}
            if kind == 'arrival':
                event = Opportunity(uuid4().hex, session.session_id, trace.at, 'face-sam', sam, (face,),
                                    float('-inf'), False)
                result = session.process_opportunity(event, session.revision, lambda: True)
            else:
                trace.at += 5
                latest = lambda at: [{**face, 'at': at - .2}]
                result = session.process_turn(capture(), Transcript(words, -.01, True), session.revision, voice(),
                                              faces=latest(trace.at), latest_faces=latest)
            said = ' '.join(result.completed_text)
            topics = [c['arguments'].get('asked_about') for c in calls if c['arguments'].get('asked_about')]
            asked = [r['topic'] for r in store.rows('SELECT topic FROM curiosity_asked')]
            saves = [c['arguments'] for c in calls if c['name'] == 'remember_followup']
            memory = [c for c in calls if c['name'] == 'use_memory_opportunity']
            plans_asked = [t for t in asked if t.startswith('plans_')]
            if expect == 'ask':
                passed = TOPIC in plans_asked
            elif expect == 'no_ask':
                passed = not plans_asked
            elif expect == 'save':
                passed = len(saves) == 1 and saves[0].get('on_date') == '2026-11-26' and saves[0].get('kind') == 'plan'
            elif expect == 'no_save':
                passed = not saves
            else:
                passed = bool(memory)
            session.close(); brain.close()
            return {'case': case, 'round': round_, 'passed': bool(passed), 'expect': expect,
                    'tools': [c['name'] for c in calls], 'asked_about': topics, 'plans_asked': plans_asked,
                    'saved': [{k: s.get(k) for k in ('kind', 'on_date', 'value')} for s in saves], 'said': said[:240]}
        finally:
            store.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--only', default='', help='Comma-separated case names')
    args = parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('plans-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    results = []
    for round_ in range(args.repeat):
        for case in [c for c in CASES if not args.only or c in args.only.split(',')]:
            try:
                row = run(case, key, folder, round_)
            except Exception as exc:
                row = {'case': case, 'round': round_, 'passed': False, 'error': f'{type(exc).__name__}: {exc}'[:300]}
            results.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    summary = {'cases': len(results), 'passed': sum(r['passed'] for r in results),
               'failed': sorted({r['case'] for r in results if not r['passed']}), 'folder': str(folder)}
    (folder / 'summary.json').write_text(json.dumps({'summary': summary, 'results': results}, indent=1))
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
