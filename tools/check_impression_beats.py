"""Explicit paid probe of impressions Rex does on his own; fictional input.

Run: .venv/bin/python tools/check_impression_beats.py --live [--runs 2]

Owner, October 23: V2 riffed in a president's voice when told about a trip to that
president's library, and he wants it with other people's voices and his own. In 132 V3 runs
there were five requested impressions and no cameo at all. This replays two conversations
with a fictional person (Sam, known by voice) through the production Session, the real
reply model and the real goal planner, with a temporary database, synthetic audio, a fake
voice cloner (no Breeze) and a fake clock:

- library: Sam is driving to the Reagan library next month.
- own_voice: Sam is rebuilding his telescope mount for the fourth time.

Reported per run: whether a reply chose a cameo, whether the planner planned an impression
beat and with what instruction, and every impression performed (voice, occasion, intro,
script, outro). It shows model choices on text, not voices, timing or hearing.
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import threading
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x import impersonation
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
from tests.test_turn_taking import ClockSink
from tools.check_agency_beats import NS, VOICE, Paced, Trace

SCENARIOS = {
    'library': ("Hi Rex, it's Sam.",
                "Next month I'm finally driving out to the Reagan library in Simi Valley.",
                "Yeah, I've wanted to see the Air Force One pavilion forever."),
    'own_voice': ("Hi Rex, it's Sam.",
                  "I'm rebuilding my telescope mount for the fourth time this year.",
                  "This time I'm sure I've got the balance right. Pretty sure."),
}


class Take:
    def __init__(self, text, reference):
        self.text, self.reference, self.error, self.stats = text, reference, None, {}
        self.ready = threading.Event()
        self.ready.set()
        self.pcm = b'\0\0' * 2205 * 4

    def chunks(self, cancel):
        for offset in range(0, len(self.pcm), 4410):
            yield self.pcm[offset:offset + 4410]

    def close(self):
        pass


class Cloner:
    started = True
    def prepare(self, text, reference):
        return Take(text, reference)
    def release_idle(self, now=None):
        pass


class Sink(ClockSink):
    def play(self, chunks, cancel, on_start, visualize=True):
        return super().play(chunks, cancel, on_start)


def run_once(scenario, index, key, folder):
    trace = Trace(folder / f'{scenario}-{index}.jsonl')
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3')
        store.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            state.voice_references = impersonation.ReferenceStore(Path(tmp).resolve() / 'voice_refs')
            brain = Paced(key)
            planner = OpenAIAgencyPlanner(key, 'gpt-5.6-terra')
            brain.trace = planner.trace = trace
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), Sink(trace), trace, cloner=Cloner()),
                              trace, state)
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

            # Fictional Sam is known by voice, with enough of it on file to be cloned.
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
                    entry['used'] = True   # Already talking with Sam; no arrival greeting.

            def tick(seconds=20):
                trace.at += seconds
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

            for line in SCENARIOS[scenario]:
                human(line)
                tick()
            for _ in range(4):   # A quiet stretch: Rex carries on with his goal.
                tick()
            session.close()
        finally:
            store.close()
    return summarize(trace.rows)


def summarize(rows):
    timeline, impressions, plans, started = [], [], [], {}
    proposed = {}
    for row in rows:
        event = row['event']
        if event == 'agency_plan_saved':
            plans.append({'goal_kind': row['goal_kind'], 'subject': row['subject'],
                          'impression_beats': [s['instruction'] for s in row['steps'] if s['kind'] == 'impression'],
                          'kinds': [s['kind'] for s in row['steps']]})
        elif event == 'agency_step_started':
            started[row['turn_id']] = row
        elif event == 'agency_step_finished' and row.get('revision_reason') == 'impression_unavailable':
            timeline.append(('impression beat let go', 'budget or voice unavailable'))
        elif event == 'tool_proposed' and row['name'] == 'impersonate':
            proposed[row['turn_id']] = row['arguments']
        elif event == 'impression_done':
            args = proposed.get(row['turn_id'], {})
            impressions.append({'occasion': row.get('occasion'), 'voice': row['voice'], 'performed': row['performed'],
                                'intro': args.get('intro'), 'script': args.get('script'), 'outro': args.get('outro')})
        elif event == 'turn' and row.get('trusted'):
            timeline.append(('Sam', row['original_text']))
        elif event == 'delivery' and row.get('completed_text'):
            step = started.pop(row.get('turn_id'), None)
            who = f"Rex beat ({step['kind']})" if step else 'Rex reply'
            text = row['completed_text']
            timeline.append((who, ' | '.join(text) if isinstance(text, list) else text))
    return {'timeline': timeline, 'plans': plans, 'impressions': impressions, 'metrics': {
        'reply_cameo': any(i['occasion'] == 'cameo' and i['performed'] for i in impressions),
        'planned_impression_beats': sum(len(p['impression_beats']) for p in plans),
        'beat_impression_performed': any(i['occasion'] == 'beat' and i['performed'] for i in impressions),
        'voices': [i['voice'] for i in impressions if i['performed']],
        'planner_calls': len(plans)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize paid model calls with fictional inputs.')
    parser.add_argument('--runs', type=int, default=2)
    parser.add_argument('--label', default='candidate')
    parser.add_argument('--scenario', choices=(*SCENARIOS, 'all'), default='all')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this probe makes paid model calls')
    key = Secrets.load(ROOT / 'local').openai_api_key
    if not key:
        raise SystemExit('OpenAI key missing')
    folder = ROOT / 'local' / 'validation' / f'impression-beats-{args.label}-{uuid4().hex[:8]}'
    folder.mkdir(parents=True)
    results = []
    for scenario in (SCENARIOS if args.scenario == 'all' else (args.scenario,)):
        for index in range(args.runs):
            result = {'scenario': scenario, 'run': index, **run_once(scenario, index, key, folder)}
            results.append(result)
            print(json.dumps({'scenario': scenario, 'run': index, **result['metrics']}), flush=True)
    (folder / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
