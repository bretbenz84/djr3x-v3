"""Explicit paid probe of "Do you ever think..." questions after a long quiet; fictional input.

Run: .venv/bin/python tools/check_hypotheticals.py --live [--runs 2] [--scenario misheard]

Owner, October 23: 45 seconds with nobody talking, Rex included, brings one imaginative "what
if" question, once per quiet stretch. Fictional Sam (known by voice, likes astronomy and old
radios) chats briefly, sits quietly in view for three minutes, answers Rex's question, then sits
quietly again. Production Session, real reply model and goal planner, temporary database,
synthetic audio and a fake clock. Reported: every question asked after a quiet stretch, whether
Rex answered it himself, how many he asked in each stretch, and his reply to Sam's answer. It
shows model choices on text, not hearing or timing.

--scenario misheard replays run b5ef657c (October 24): Sam answers Rex's telescope question with a
misheard "He's at the truth.", that one turn is taken as said to someone else (the live decision,
replayed, not the model's), and Sam then sits quietly in view for three minutes. Reported: how long
until Rex speaks again, each choice the reply model makes on his own beats (silences and their
reasons, the quiet-room retry) and the "what if" he asks.
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

from r3x.adapters.hosted import OpenAIAgencyPlanner
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
from tools.check_impression_beats import Sink

CHAT = ("Hi Rex, it's Sam.", "Not much, just winding down after work.")
ANSWER = "Ha. Honestly? I'd buy a boat and never tell anyone."
CLOUDY = "It's been cloudy, so I haven't used my telescopes."
MISHEARD = "He's at the truth."


class MisheardPaced(Paced):
    """The live decision on the misheard line, replayed: that one turn is taken as said to someone
    else. Every other generation is the real model's; its tool choices are traced."""
    misheard = False

    def reply(self, snapshot, cancel):
        step = snapshot.context.get('agency_step') or {}
        if self.misheard:
            self.misheard = False
            self.trace.emit('probe_choice', forced=True, beat=None, wonder=False, retry=False,
                            name='remain_silent', reason='other_addressee')
            yield ToolCall(uuid4().hex, 'remain_silent', {'reason': 'other_addressee', 'invitation_update': None,
                                                          'intent_update': None, 'next_opportunity': None})
            return
        last = snapshot.messages[-1] if snapshot.messages else {}
        retry = isinstance(last, dict) and str(last.get('content', '')).startswith('Nobody has said a word')
        for item in super().reply(snapshot, cancel):
            if isinstance(item, ToolCall):
                self.trace.emit('probe_choice', forced=False, beat=step.get('instruction'), wonder=bool(step.get('wonder')),
                                retry=retry, name=item.name, reason=item.arguments.get('reason'),
                                text=item.arguments.get('text'))
            yield item


class MisheardPlanner:
    """A fixed goal like the live one, made of spoken beats (the probe has no body): a remark, a
    question about his telescopes, then beats that wait for his answers."""
    capability = SimpleNamespace(model='fixed-misheard-plan')
    reasoning_effort = 'none'
    trace = None
    def start(self): pass
    def plan(self, facts, cancel, images=()):
        step = lambda text, when: {'kind': 'contribute', 'instruction': text, 'evidence_ids': [],
                                   'wake': {'when': when, 'seconds': 5}, 'follows_previous': when == 'answered'}
        return {'goal_kind': 'catch_up', 'subject': 'Sam', 'scope': 'audience', 'narrative': 'Sam is settled in.',
                'objective': 'Catch up with Sam about his telescopes.', 'evidence_ids': [],
                'steps': [step('A short, warm remark about his evening.', 'quiet'),
                          step('Ask Sam one light question about his telescopes.', 'quiet'),
                          step('Pick up whatever he says next.', 'answered'),
                          step('React to whatever he says after that.', 'answered')]}


class QuietPlanner:
    """A fixed goal that leaves the room quiet: one remark, then a beat that waits for an answer.
    The real planner keeps talking into silence when every beat must be spoken (the probe has no
    body for silent moves), so the stretch never came in the first round."""
    capability = SimpleNamespace(model='fixed-quiet-plan')
    reasoning_effort = 'none'
    trace = None
    def start(self): pass
    def plan(self, facts, cancel, images=()):
        step = lambda text, when: {'kind': 'contribute', 'instruction': text, 'evidence_ids': [],
                                   'wake': {'when': when, 'seconds': 5}, 'follows_previous': when == 'answered'}
        return {'goal_kind': 'catch_up', 'subject': 'Sam', 'scope': 'audience', 'narrative': 'Sam is winding down.',
                'objective': 'Keep Sam company without crowding him.', 'evidence_ids': [],
                'steps': [step('A short, warm remark about his evening.', 'quiet'),
                          step('Pick up whatever he says next.', 'answered')]}


def run_once(index, key, folder, fixed_plan=False, scenario='quiet'):
    trace = Trace(folder / f'run-{index}.jsonl')
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3')
        store.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            misheard = scenario == 'misheard'
            brain = (MisheardPaced if misheard else Paced)(key)
            planner = (MisheardPlanner() if misheard else QuietPlanner() if fixed_plan else
                       OpenAIAgencyPlanner(key, 'gpt-5.6-terra'))
            brain.trace = planner.trace = trace
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), Sink(trace), trace), trace, state)
            session.agency_planner = planner
            runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace)
            runtime.started_at = 0
            brain.start(); planner.start()

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
            sam = next(p['id'] for p in store.rows('SELECT id FROM people'))

            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.45, .3, .1, .12], 'face_width_px': 192, 'learning_quality': False,
                         'needs_closer_look': False, 'position': 'center', 'evidence': 'face_match'}]
            runtime.camera = SimpleNamespace(latest=faces)
            seen_until = [trace.at]

            def human(text):
                trace.at += 3
                session.note_speech(trace.at)
                session.process_turn(capture(), Transcript(text, -.01, True), session.revision, observation(),
                                     faces=faces(trace.at), latest_faces=faces)
                for entry in runtime.arrivals.seen.values():
                    entry['used'] = True
                trace.emit('probe_human', text=text)

            def quiet(seconds, step=15):
                for _ in range(int(seconds // step)):
                    trace.at += step
                    while seen_until[0] < trace.at:
                        seen_until[0] = min(trace.at, seen_until[0] + 1.)
                        runtime.arrivals.observe(faces(seen_until[0]), seen_until[0])
                    runtime.audio_seen_at = trace.at
                    result = runtime.proactive_tick()
                    if result is not None and result.status == 'prepared':
                        session.join_planning(120)
                        trace.at += 1
                        runtime.audio_seen_at = trace.at
                        runtime.proactive_tick()

            if misheard:
                human(CHAT[0]); quiet(15)
                human(CLOUDY); quiet(15)
                brain.misheard = True
                human(MISHEARD)
                trace.emit('probe_hold', seconds=round(session.proactive_hold_until - trace.at, 1))
                trace.emit('probe_stretch', number=1)
                quiet(180)
                session.close()
                return summarize(trace.rows)
            for line in CHAT:
                human(line)
                quiet(15)
            trace.emit('probe_stretch', number=1)
            quiet(180)
            human(ANSWER)
            quiet(15)
            trace.emit('probe_stretch', number=2)
            quiet(180)
            session.close()
        finally:
            store.close()
    return summarize(trace.rows)


def summarize(rows):
    timeline, asked, started, stretch = [], [], {}, 0
    reply_to_answer = None
    for row in rows:
        event = row['event']
        if event == 'probe_stretch':
            stretch = row['number']
            timeline.append((f'-- quiet stretch {stretch} --', ''))
        elif event == 'probe_human':
            timeline.append(('Sam', row['text']))
        elif event == 'agency_step_started':
            started[row['turn_id']] = row
        elif event == 'hypothetical_asked':
            asked.append({'stretch': stretch, 'text': row['text']})
        elif event == 'delivery' and row.get('completed_text'):
            step = started.pop(row.get('turn_id'), None)
            text = row['completed_text']
            text = ' '.join(text) if isinstance(text, list) else text
            who = ('Rex what-if' if step and step.get('wonder') else f"Rex beat ({step['kind']})" if step else 'Rex reply')
            timeline.append((who, text))
        if event == 'probe_human' and row['text'] == ANSWER:
            reply_to_answer = next((t for w, t in reversed(timeline[:-1]) if w == 'Rex reply'), None)
    misheard_at = next((r['at'] for r in rows if r['event'] == 'probe_human' and r['text'] == MISHEARD), None)
    spoken_after = [r['at'] for r in rows if misheard_at is not None and r['event'] == 'delivery'
                    and r.get('completed_text') and r['at'] > misheard_at]
    choices = [{k: r.get(k) for k in ('wonder', 'retry', 'name', 'reason', 'text', 'beat')}
               for r in rows if r['event'] == 'probe_choice' and not r['forced'] and r['at'] > (misheard_at or float('inf'))]
    misheard = {} if misheard_at is None else {
        'hold_seconds': next((r['seconds'] for r in rows if r['event'] == 'probe_hold'), None),
        'seconds_to_next_line': round(spoken_after[0] - misheard_at, 1) if spoken_after else None,
        'lines_in_three_minutes': len(spoken_after),
        'own_beat_silences': [c['reason'] for c in choices if c['name'] == 'remain_silent'],
        'quiet_room_retries': sum(1 for r in rows if r.get('purpose') == 'quiet_room_silence_retry'),
        'choices': choices}
    return {'timeline': timeline, 'asked': asked, 'reply_to_answer': reply_to_answer, 'misheard': misheard, 'metrics': {
        'questions': len(asked),
        'per_stretch': [sum(1 for a in asked if a['stretch'] == n) for n in (1, 2)],
        'all_end_in_question': all(a['text'].rstrip().endswith('?') for a in asked),
        'openings': [' '.join(a['text'].split()[:4]) for a in asked]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize paid model calls with fictional inputs.')
    parser.add_argument('--runs', type=int, default=2)
    parser.add_argument('--label', default='candidate')
    parser.add_argument('--fixed-plan', action='store_true', help='A fixed quiet goal instead of the paid planner.')
    parser.add_argument('--scenario', choices=('quiet', 'misheard'), default='quiet')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this probe makes paid model calls')
    key = Secrets.load(ROOT / 'local').openai_api_key
    if not key:
        raise SystemExit('OpenAI key missing')
    folder = ROOT / 'local' / 'validation' / f'hypotheticals-{args.label}-{uuid4().hex[:8]}'
    folder.mkdir(parents=True)
    results = []
    for index in range(args.runs):
        result = run_once(index, key, folder, args.fixed_plan, args.scenario)
        results.append(result)
        misheard = {k: v for k, v in result['misheard'].items() if k != 'choices'}
        print(json.dumps({'run': index, **result['metrics'], **misheard}), flush=True)
    (folder / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
