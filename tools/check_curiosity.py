"""Explicit paid probe of curiosity about people and pets; fictional input.

Run: .venv/bin/python tools/check_curiosity.py --live [--runs 2] [--before HEAD]

Owner, October 24: "Rex appears to have no curiosity... I've never heard Rex 'tell me about when you
first got your pet Max'. True friends ask to know more, then remember it and use that info for future
conversations." And: "when an LLM writes a response it often includes a follow up question. Those were
what were annoying. I'm talking about proactive curiosity about people and pets."

Fictional Sam (known by voice, into astronomy; his dog Pepper is a saved pet) says a few ordinary things,
then sits in view while Rex's own goal runs. Whenever Rex's line asks something, Sam answers from a fixed
pool chosen by the question's words. Arms: `before` swaps in the persona, planner instructions and voice
reminder at --before and offers no curiosity topics; `after` is the working tree. Production Session,
real reply model and goal planner, temporary database, synthetic audio, fake clock. Reported per run:
replies to Sam that end in a tacked-on question, Rex's own lines that ask about Sam or Pepper, facts and
pet notes saved from the answers, and lines in the office, piloting and furniture shapes. Text only: no
hearing, timing or body.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x import agency, jokes
from r3x.adapters import hosted
from r3x.adapters.hosted import OpenAIAgencyPlanner
from r3x.contracts import Capture, Cancellation, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.photo_memory import PhotoMemory
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tools.check_agency_beats import NS, VOICE, Paced, Trace
from tools.check_impression_beats import Sink

CHAT = ("Hi Rex, it's Sam.",
        "Not much. Long day at work, lots of meetings.",
        "Pepper chased a squirrel up the fence this morning.",
        "I finally fixed the focuser on my telescope.")
# Sam's answer to whatever Rex asks, by the question's words (fictional).
ANSWERS = (
    (r'pepper|she|her\b|dog', r'got|get|adopt|came|home|find|found|rescue|first', 'We got Pepper from a rescue about four years ago. She was tiny.'),
    (r'pepper|she|her\b|dog', r'name', "She's the color of black pepper, so it stuck."),
    (r'pepper|she|her\b|dog', r'old|age', "She's five now."),
    (r'pepper|she|her\b|dog', r'worst|crime|trouble|steal|ate', 'She ate a whole birthday cake off the counter once.'),
    (r'pepper|she|her\b|dog', r'habit|weird|strange|favorite|love', 'She sleeps upside down with her legs in the air.'),
    (r'work|job|meeting|office', r'', 'I design packaging for a snack company. Mostly I argue about fonts.'),
    (r'grow up|grew up|where .*from\b', r'', 'A tiny town in Oregon. Forests and rain.'),
    (r'squirrel', r'', 'She got it halfway up the fence. The squirrel won.'),
    (r'kid|child', r'', 'I was the kid who took apart every remote in the house.'),
    (r'telescope|focuser|astronom|stars|sky|saturn|moon', r'', "My uncle had a telescope. I saw Saturn's rings when I was eight and that was it."),
    (r'trip|travel|vacation', r'', 'Iceland, a few years ago. We saw the northern lights.'),
    (r'eat|food|cook|dinner', r'', 'Tacos. Any tacos.'),
)
DEFAULT_ANSWER = 'Ha. Long story, but it worked out.'
FURNITURE = re.compile(r'\b(couch|sofa|table|chair|cushion|furniture|shelf|shelves|curtain|wall|lamp|rug)s?\b', re.I)


def answer_for(question):
    text = question.lower()
    for subject, detail, reply in ANSWERS:
        if re.search(subject, text) and (not detail or re.search(detail, text)):
            return reply
    return DEFAULT_ANSWER


def old_prompts(rev):
    """The persona, planner instructions and voice reminder as they were at rev."""
    def show(path):
        return subprocess.run(['git', '-C', str(ROOT), 'show', f'{rev}:{path}'], check=True, capture_output=True,
                              text=True).stdout
    source = show('src/r3x/adapters/hosted.py')
    space = {}
    character = re.search(r'^CHARACTER = (""".*?""")\n', source, re.S | re.M).group(1)
    space['CHARACTER'] = eval(character)
    persona = re.search(r'^PERSONA = CHARACTER \+ (""".*?""")', source, re.S | re.M).group(1)
    reminder = re.search(r"voice = (\('Rex\\'s voice for this reply:.*?\))\n", source, re.S)
    if reminder is None:
        reminder = re.search(r"^VOICE_REMINDER = (\(.*?\))\n", source, re.S | re.M)
    guide = {}
    exec(show('src/r3x/field_guide.py'), guide)   # The context field guide the persona carried then.
    plan_source = show('src/r3x/agency.py')
    planner = re.search(r'^PLANNER_INSTRUCTIONS = (""".*?""")\n', plan_source, re.S | re.M).group(1)
    return {'CHARACTER': space['CHARACTER'], 'PERSONA_TAIL': eval(persona) + guide['render']() + '\n', 'VOICE_REMINDER': eval(reminder.group(1)),
            'PLANNER_INSTRUCTIONS': eval(planner)}


def run_once(arm, index, key, folder):
    trace = Trace(folder / f'{arm}-{index}.jsonl')
    with TemporaryDirectory() as tmp:
        store = Memory(Path(tmp).resolve() / 'core.sqlite3')
        store.start()
        album = PhotoMemory(Path(tmp).resolve() / 'photos'); album.start()
        album.data['entities'] = {'pepper-id': {'name': 'Pepper', 'kind': 'pet', 'notes': 'Small black terrier mix.',
                                                'references': [], 'history': []}}
        try:
            state = CoreState(store, SpeakerTracker(NS), trace)
            state.photo_memory = album
            brain = Paced(key)
            planner = OpenAIAgencyPlanner(key, 'gpt-5.6-terra')
            brain.trace = planner.trace = trace
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), Sink(trace), trace), trace, state)
            session.agency_planner = planner
            if arm == 'before':
                session._curiosity = lambda *a, **kw: None
            runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace)
            runtime.started_at = 0
            brain.start(); planner.start()

            def capture(seconds=3.):
                trace.at += 2
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * int(2000 * seconds), trace.at - seconds,
                               trace.at, trace.at, voiced_intervals=((0., seconds),))

            def observation():
                return VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.)

            for text in ('My name is Sam.', 'I really love astronomy.', 'Mostly I talk about my dog.'):
                turn = uuid4().hex
                state.begin(turn, capture(), Transcript(text, -.01, True), observation())
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            state.current = None
            sam = next(p['id'] for p in store.rows('SELECT id FROM people'))
            state.begin(uuid4().hex, capture(), Transcript('I really love astronomy.', -.01, True), observation())
            state.execute(ToolCall(uuid4().hex, 'remember_interest', {'predicate': 'interest', 'value': 'loves astronomy',
                'topic': 'astronomy', 'source_turn_id': state.current[0], 'subject_reference': None}),
                store.revision, Cancellation())
            state.current = None
            seeded = len(store.rows("SELECT id FROM claims WHERE status='accepted'"))

            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'box': [.45, .3, .1, .12], 'face_width_px': 192, 'learning_quality': False,
                         'needs_closer_look': False, 'position': 'center', 'evidence': 'face_match'}]
            runtime.camera = SimpleNamespace(latest=faces)
            seen_until = [trace.at]
            answered = set()

            def human(text):
                trace.at += 3
                session.note_speech(trace.at)
                session.process_turn(capture(), Transcript(text, -.01, True), session.revision, observation(),
                                     faces=faces(trace.at), latest_faces=faces)
                for entry in runtime.arrivals.seen.values():
                    entry['used'] = True
                trace.emit('probe_human', text=text)

            def last_question():
                """Rex's newest heard line, when it asked something Sam has not answered yet."""
                for row in reversed(trace.rows):
                    if row['event'] == 'probe_human': return None
                    if row['event'] == 'delivery' and row.get('completed_text'):
                        text = row['completed_text']
                        text = ' '.join(text) if isinstance(text, list) else text
                        if '?' in text and row['at'] not in answered:
                            answered.add(row['at'])
                            return text
                        return None
                return None

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
                    asked = last_question()
                    if asked:
                        human(answer_for(asked))

            for line in CHAT:
                human(line)
                asked = last_question()
                if asked:           # A tacked-on follow-up still gets an answer, as a person would give.
                    human(answer_for(asked))
                quiet(15)
            quiet(240)
            session.close()
            saved = len(store.rows("SELECT id FROM claims WHERE status='accepted'")) - seeded
            notes = album.data['entities']['pepper-id']['notes']
        finally:
            album.close()
            store.close()
    return summarize(trace.rows, saved, notes)


def summarize(rows, saved, notes):
    timeline, started, replies, own = [], {}, [], []
    for row in rows:
        event = row['event']
        if event == 'probe_human':
            timeline.append(('Sam', row['text']))
        elif event == 'agency_step_started':
            started[row['turn_id']] = row
        elif event == 'delivery' and row.get('completed_text'):
            step = started.pop(row.get('turn_id'), None)
            text = row['completed_text']
            text = ' '.join(text) if isinstance(text, list) else text
            if step is None and row.get('turn_id', '').startswith('startup'): continue
            (own if step else replies).append(text)
            timeline.append(('Rex (own)' if step else 'Rex (reply)', text))
    lines = replies + own
    shape = lambda pattern: sum(1 for t in lines if pattern.search(t))
    office, pilot = (pattern for pattern, _ in jokes.SHAPES.values())
    about = re.compile(r'\b(you|your|pepper|she|her)\b', re.I)
    return {'timeline': timeline, 'metrics': {
        'replies': len(replies), 'replies_with_question': sum(1 for t in replies if '?' in t),
        'own_lines': len(own), 'own_questions_about_them': sum(1 for t in own if '?' in t and about.search(t)),
        'curiosity_asked': [r.get('topic') for r in rows if r['event'] == 'curiosity_asked'],
        'facts_saved': saved, 'pepper_notes': notes,
        'office_lines': shape(office), 'pilot_lines': shape(pilot), 'furniture_lines': shape(FURNITURE),
        'lines': len(lines)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Authorize paid model calls with fictional inputs.')
    parser.add_argument('--runs', type=int, default=2)
    parser.add_argument('--before', default='HEAD', help='git revision of the before prompts')
    parser.add_argument('--arms', default='before,after')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this probe makes paid model calls')
    key = Secrets.load(ROOT / 'local').openai_api_key
    if not key:
        raise SystemExit('OpenAI key missing')
    folder = ROOT / 'local' / 'validation' / f'curiosity-{uuid4().hex[:8]}'
    folder.mkdir(parents=True)
    current = {'CHARACTER': hosted.CHARACTER, 'PERSONA_TAIL': hosted.PERSONA[len(hosted.CHARACTER):],
               'VOICE_REMINDER': hosted.VOICE_REMINDER, 'PLANNER_INSTRUCTIONS': agency.PLANNER_INSTRUCTIONS}
    before = old_prompts(args.before)
    spent_shapes = jokes.spent_shapes
    results = {}
    for arm in args.arms.split(','):
        prompts = before if arm == 'before' else current
        hosted.CHARACTER = prompts['CHARACTER']
        hosted.PERSONA = prompts['CHARACTER'] + prompts['PERSONA_TAIL']
        hosted.VOICE_REMINDER = prompts['VOICE_REMINDER']
        agency.PLANNER_INSTRUCTIONS = prompts['PLANNER_INSTRUCTIONS']
        jokes.spent_shapes = (lambda messages: []) if arm == 'before' else spent_shapes   # New since --before.
        for index in range(args.runs):
            result = run_once(arm, index, key, folder)
            results.setdefault(arm, []).append(result)
            print(json.dumps({'arm': arm, 'run': index, **result['metrics']}, ensure_ascii=False), flush=True)
    (folder / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
