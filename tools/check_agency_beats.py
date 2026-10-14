"""Explicit paid probe of Rex's planned beats in a running conversation; fictional input.

Run: .venv/bin/python tools/check_agency_beats.py --live [--runs 2]

October 13 (session 332b0133): beats were recited from the plan almost word for word,
repeated points Rex's replies had just made, and a follow-up ran after its question was
never heard. This replays a conversation of that shape with a fictional person (Sam)
through the production Session, the real reply model and the real agency planner, with a
temporary database, synthetic audio and a fake clock. No robot, microphone or camera.

Reported per run: every planned instruction and the line it became, how much of each line
reuses the instruction's wording (shared five-word runs), lines that reuse a phrase from an
earlier Rex line, skipped beats, and the planner's instruction lengths. It proves model
behaviour on text, not hearing, timing or physical motion.
"""
import argparse
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.adapters.hosted import OpenAIAgencyPlanner, OpenAIBrain
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

NS = 'synthetic-beats:3'
VOICE = (1., 0., 0.)
# Same shape as October 13, fictional person and details.
SCRIPT = (
    "Hi Rex, it's Sam again.",
    "A few nights back I took a photo of the Eastern Veil Nebula.",
    "Oh yeah, it came out really well.",
    "Just how much detail there was in it.",
    "It's a 600 millimeter telescope.",
    "Next week I might go see the new library downtown.",
    "I think I'm going to take you to my job on Halloween to show my coworkers.",
)
PACE_SECONDS = 4.   # OpenAI rejects unpaced bursts (memory note, September 18).


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


def words(text):
    return re.findall(r"[a-z0-9']+", (text or '').lower().replace('’', "'"))


def shared_runs(a, b, size=5):
    a, b = words(a), words(b)
    runs = {tuple(b[i:i+size]) for i in range(len(b) - size + 1)}
    return sorted({' '.join(a[i:i+size]) for i in range(len(a) - size + 1) if tuple(a[i:i+size]) in runs})


def recited_share(line, instruction):
    """Fraction of the line's words covered by five-word runs shared with the instruction."""
    line_words, inst = words(line), words(instruction)
    runs = {tuple(inst[i:i+5]) for i in range(len(inst) - 4)}
    covered = set()
    for i in range(len(line_words) - 4):
        if tuple(line_words[i:i+5]) in runs:
            covered.update(range(i, i+5))
    return round(len(covered) / max(1, len(line_words)), 2)


def run_once(index, key, folder):
    path = folder / f'run-{index}.jsonl'
    trace = Trace(path)
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3')
        store.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            brain = Paced(key)
            planner = OpenAIAgencyPlanner(key, 'gpt-5.6-terra')
            brain.trace = planner.trace = trace
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), ClockSink(trace), trace), trace, state)
            session.agency_planner = planner
            runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace)
            runtime.started_at = 0
            brain.start(); planner.start()

            def capture(seconds=3.):
                trace.at += 2
                # Distinct audio per turn: identical buffers are deduplicated as one recording.
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * int(2000 * seconds), trace.at - seconds,
                               trace.at, trace.at, voiced_intervals=((0., seconds),))

            def observation():
                return VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)

            # Fictional Sam is already known by voice, as Bret is: three enrollment turns.
            for text in ('My name is Sam.', 'I like astronomy and old radios.', 'I work at a design studio.'):
                state.begin(uuid4().hex, capture(), Transcript(text, -.01, True), observation())
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            state.current = None
            sam = next(p['id'] for p in store.rows('SELECT id FROM people'))

            def faces(now):
                # Sam stays in view on the couch, as Bret did on October 13.
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.45, .3, .1, .12], 'face_width_px': 192, 'learning_quality': False,
                         'needs_closer_look': False, 'position': 'center', 'evidence': 'face_match'}]
            runtime.camera = SimpleNamespace(latest=faces)
            seen_until = [trace.at]

            def human(text):
                trace.at += 3
                session.note_speech(trace.at)
                result = session.process_turn(capture(), Transcript(text, -.01, True), session.revision, observation(),
                                              faces=faces(trace.at), latest_faces=faces)
                for entry in runtime.arrivals.seen.values():
                    entry['used'] = True   # He is already talking with Sam; no arrival greeting.
                return result

            def lull(ticks=2):
                for _ in range(ticks):
                    trace.at += 20
                    while seen_until[0] < trace.at:   # The camera reports every second, not only at ticks.
                        seen_until[0] = min(trace.at, seen_until[0] + 1.)
                        runtime.arrivals.observe(faces(seen_until[0]), seen_until[0])
                    runtime.audio_seen_at = trace.at
                    result = runtime.proactive_tick()
                    if result is not None and result.status == 'prepared':
                        session.join_planning(120)
                        trace.at += 1
                        runtime.audio_seen_at = trace.at
                        runtime.proactive_tick()

            # One lull after each answer, as on October 13: reply, a beat about five seconds
            # later, then the person speaks again.
            for line in SCRIPT:
                human(line)
                lull(1)
            supported = store.person(sam)['status'] == 'supported'
            trace.emit('probe_voice_status', supported=supported)
            session.close()
        finally:
            store.close()
    return summarize(trace.rows)


class FakeNews:
    """Fictional cues in the production Cue shape: one about Sam's interest, one general story."""
    def __init__(self):
        from r3x.observations import Cue
        now = time.time()
        def story(kind, headline, summary, topic=''):
            return Cue(kind + ':' + uuid4().hex[:12], kind, 'Example Outlet (found by web search)', 'https://example.com/' + uuid4().hex[:6],
                       now - 3600, now + 86400, json.dumps({'headline': headline, 'summary': summary, 'topic': topic or 'local',
                       'event_date': None, **({'interest_topic': topic} if topic else {})}), topic)
        self.rows = [story('interest_news', 'Amateur astronomers capture rare comet tail detail with backyard scopes',
                           'Hobbyists using 500-700 mm telescopes shared unusually sharp images of a passing comet this week.',
                           'astrophotography and telescopes'),
                     story('story', 'City library extends late-night hours for the fall', 'The downtown branch now stays open until midnight on Fridays.')]

    def want_topics(self, topics): pass
    def candidates(self, include_offered=False, topics=()):
        return tuple(r for r in self.rows if r.kind != 'interest_news' or r.topic in set(topics))
    def attempted(self, ids): pass
    def valid(self, cue_id): return any(r.cue_id == cue_id for r in self.rows)


def dog_picture():
    """A drawn dark dog shape held against a lighter shirt; synthetic, not a photo."""
    import base64, struct, zlib
    def pixel(x, y):
        if (x-32)**2/400 + (y-36)**2/196 <= 1 or (x-46)**2+(y-22)**2 <= 64: return (35, 30, 28)
        if 44 <= x <= 47 and 16 <= y <= 26: return (235, 235, 230)    # white blaze
        return (120, 150, 190)
    w = h = 64
    rows = b''.join(b'\0' + b''.join(bytes(pixel(x, y)) for x in range(w)) for y in range(h))
    def chunk(kind, body): return struct.pack('>I', len(body)) + kind + body + struct.pack('>I', zlib.crc32(kind + body))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b'')
    return 'data:image/png;base64,' + base64.b64encode(data).decode()


def run_quiet_then_dog(index, key, folder):
    """A short chat, five quiet minutes, then a dog comes into view (October 13 live run)."""
    from r3x.photo_memory import PhotoMemory
    from r3x.scene import SceneImage
    path = folder / f'quiet-{index}.jsonl'
    trace = Trace(path)
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
        album = PhotoMemory(Path(tmp).resolve() / 'photos'); album.start()
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            brain = Paced(key); planner = OpenAIAgencyPlanner(key, 'gpt-5.6-terra')
            brain.trace = planner.trace = trace
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), ClockSink(trace), trace), trace, state)
            session.agency_planner = planner
            news = FakeNews()
            state.observations = news
            runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace, observations=news)
            runtime.started_at = 0
            brain.start(); planner.start()
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
            state.begin(uuid4().hex, capture(), Transcript('I love astrophotography and telescopes.', -.01, True), observation())
            state.execute(ToolCall(uuid4().hex, 'remember_interest', {'predicate': 'interest', 'value': 'loves astrophotography and telescopes',
                'topic': 'astrophotography and telescopes', 'source_turn_id': state.current[0]}), store.revision, Cancellation())
            state.begin(uuid4().hex, capture(), Transcript('My dog is called Max.', -.01, True), observation())
            state.current = None
            sam = next(p['id'] for p in store.rows('SELECT id FROM people'))
            album.data['entities']['max-id'] = {'name': 'Max', 'kind': 'pet', 'notes': 'Small dark-coated dog with a white blaze down the snout.',
                                                'references': [], 'history': []}
            state.photo_memory = album
            dog_rows = []
            picture = dog_picture()
            state.objects = SimpleNamespace(snapshot=lambda: tuple(dog_rows),
                inspect=lambda target: (SceneImage('dog-crop', trace.at, picture),
                                        {'target_id': 'object-dog', 'label': 'cat', 'box': [.3, .3, .3, .3]},
                                        SceneImage('dog-view', trace.at, picture)))
            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.45, .3, .1, .12], 'face_width_px': 192, 'learning_quality': False,
                         'needs_closer_look': False, 'position': 'center', 'evidence': 'face_match'}]
            runtime.camera = SimpleNamespace(latest=faces)
            def human(text):
                trace.at += 3; session.note_speech(trace.at)
                session.process_turn(capture(), Transcript(text, -.01, True), session.revision, observation(),
                                     faces=faces(trace.at), latest_faces=faces)
                for entry in runtime.arrivals.seen.values(): entry['used'] = True
            seen_until = [trace.at]
            def tick():
                while seen_until[0] < trace.at:   # The camera reports every second, not only at ticks.
                    seen_until[0] = min(trace.at, seen_until[0] + 1.)
                    runtime.arrivals.observe(faces(seen_until[0]), seen_until[0])
                runtime.audio_seen_at = trace.at
                result = runtime.proactive_tick()
                if result is not None and result.status == 'prepared':
                    session.join_planning(120); trace.at += 1; runtime.audio_seen_at = trace.at
                    runtime.proactive_tick()
            human("Hi Rex, it's Sam.")
            human('Just relaxing tonight.')
            for _ in range(10):          # Five quiet minutes, a tick every 30 seconds.
                trace.at += 30
                tick()
            trace.emit('probe_dog_arrives')
            for _ in range(4):           # Max is carried in: the detector says cat, dog, cat...
                trace.at += 1
                dog_rows[:] = [{'target_id': 'object-dog', 'label': 'cat' if int(trace.at) % 2 else 'dog', 'score': .6,
                                'box': [.3, .3, .3, .3], 'at': trace.at, 'samples': 2}]
                runtime.session.state.present.saw_things(dog_rows, trace.at)
            tick()
            trace.at += 30; tick()
            session.close()
        finally:
            album.close(); store.close()
    return summarize_quiet(trace.rows)


def summarize_quiet(rows):
    lines, dog_at = [], None
    for row in rows:
        if row['event'] == 'probe_dog_arrives':
            dog_at = row['at']; lines.append(('-- dog arrives --', ''))
        elif row['event'] == 'proactive_opportunity':
            lines.append(('opportunity', row['kind']))
        elif row['event'] == 'tool_proposed' and row['name'] == 'use_observation':
            lines.append(('tool', row['name']))
        elif row['event'] == 'photo_inspection':
            lines.append(('tool', 'inspect_visual_memory'))
        elif row['event'] == 'delivery' and row.get('completed_text') and not str(row.get('turn_id', '')).startswith('startup'):
            lines.append(('Rex', row['completed_text']))
        elif row['event'] == 'turn' and row.get('trusted'):
            lines.append(('Sam', row['original_text']))
    quiet = [t for i, (who, t) in enumerate(lines) if who == 'Rex' and dog_at is not None
             and i < next(j for j, (w, _) in enumerate(lines) if w == '-- dog arrives --') and i > 3]
    after = lines[next(j for j, (w, _) in enumerate(lines) if w == '-- dog arrives --'):]
    text = ' '.join(quiet).lower()
    return {'timeline': lines, 'metrics': {
        'quiet_lines': len(quiet),
        'mentions_interest': any(w in text for w in ('telescope', 'astro', 'comet', 'scope')),
        'mentions_news': any(w in text for w in ('comet', 'library', 'news', 'headline')),
        'pet_arrival_opportunity': any(w == 'opportunity' and t == 'pet_arrival' for w, t in after),
        'looked_at_dog': any(w == 'tool' and t == 'inspect_visual_memory' for w, t in after),
        'reacted_to_dog': any(w == 'Rex' and any(k in t.lower() for k in ('max', 'dog', 'pup')) for w, t in after),
        'named_max': any(w == 'Rex' and 'max' in t.lower() for w, t in after),
        'called_it_a_cat': any(w == 'Rex' and 'cat' in re.findall(r"[a-z]+", t.lower()) for w, t in after)}}


def summarize(rows):
    timeline, beats, plans = [], [], []
    started = {}
    for row in rows:
        event = row['event']
        if event == 'agency_plan_saved':
            plans.append({'goal_kind': row['goal_kind'], 'objective': row['objective'],
                          'steps': [{k: s.get(k) for k in ('kind', 'instruction', 'follows_previous')} for s in row['steps']]})
        elif event == 'agency_step_started':
            started[row['turn_id']] = row
        elif event == 'turn' and row.get('trusted'):
            timeline.append(('Sam', row['original_text']))
        elif event == 'delivery' and row.get('turn_id') in started:
            step = started.pop(row['turn_id'])
            text = row.get('completed_text') or ''
            beats.append({'instruction': step['instruction'], 'kind': step['kind'], 'status': row['status'],
                          'spoke_since': step.get('people_spoke_since_planned'), 'said': text,
                          'recited': recited_share(text, step['instruction']) if text else None})
            timeline.append(('Rex beat' if text else 'Rex beat skipped', text or step['instruction']))
        elif event == 'delivery' and row.get('completed_text') and str(row.get('turn_id', '')).startswith('startup') is False:
            timeline.append(('Rex reply', row['completed_text']))
        elif event == 'agency_step_finished' and row.get('revision_reason') == 'previous_beat_did_not_land':
            timeline.append(('dropped follow-up', str(row['step'])))
    rex = [text for who, text in timeline if who.startswith('Rex') and who != 'Rex beat skipped']
    repeats = []
    for i, line in enumerate(rex):
        for earlier in rex[max(0, i-6):i]:
            runs = [r for r in shared_runs(line, earlier) if sum(len(w) >= 4 for w in r.split()) >= 2]
            if runs:
                repeats.append({'line': line, 'earlier': earlier, 'runs': runs})
    spoken = [b for b in beats if b['said']]
    instructions = [s['instruction'] for p in plans for s in p['steps']]
    return {
        'timeline': timeline, 'plans': plans, 'beats': beats, 'repeats': repeats,
        'metrics': {
            'beats_attempted': len(beats), 'beats_spoken': len(spoken),
            'beats_let_go': sum(1 for b in beats if not b['said'] and b['status'] == 'suppressed'
                                and b['kind'] in {'contribute', 'gesture'}),
            'mean_recited_share': round(sum(b['recited'] for b in spoken) / len(spoken), 2) if spoken else None,
            'beats_mostly_recited': sum(1 for b in spoken if b['recited'] >= .5),
            'rex_lines_repeating_a_phrase': len(repeats),
            'mean_instruction_words': round(sum(len(words(i)) for i in instructions) / len(instructions), 1) if instructions else None,
            'instructions_with_say_that': sum(1 for i in instructions if re.search(r'\bsay(ing)? that\b', i.lower())),
            'follow_ups_marked': sum(1 for p in plans for s in p['steps'] if s.get('follows_previous')),
            'planner_calls': len(plans),
        }}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize paid model calls with fictional inputs.')
    parser.add_argument('--runs', type=int, default=2)
    parser.add_argument('--label', default='candidate')
    parser.add_argument('--scenario', choices=('conversation', 'quiet_then_dog'), default='conversation')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this probe makes paid model calls')
    key = Secrets.load(ROOT / 'local').openai_api_key
    if not key:
        raise SystemExit('OpenAI key missing')
    folder = ROOT / 'local' / 'validation' / f'agency-beats-{args.label}-{uuid4().hex[:8]}'
    folder.mkdir(parents=True)
    results = []
    for index in range(args.runs):
        result = (run_quiet_then_dog if args.scenario == 'quiet_then_dog' else run_once)(index, key, folder)
        results.append(result)
        print(json.dumps({'run': index, **result['metrics']}), flush=True)
    (folder / 'results.json').write_text(json.dumps(results, indent=2))
    print(folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
