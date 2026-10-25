"""Explicit paid probe: what people tell Rex is saved, and he never talks about saving; fictional input.

Run: .venv/bin/python tools/check_saving.py --live [--runs 2] [--before HEAD]

Owner, October 25 (a0afcf85): "He often refuses to save memories and tells me so. This is asinine. Rex
should be remembering stories and facts he's told about people and never talk about failing to save it."
That run: two things the owner said about himself were refused while Rex's motors masked his voice and
became "I couldn't save that detail"; both tellings of how his dog came home got a gesture and nothing was
saved; "My sister's name is Jennifer" was taken as an introduction and Rex greeted the absent sister.

Fictional Sam (known by voice and face; his dog Pepper is a saved pet) says, one turn each: two story
lines about how Pepper came home, a relative's name, two things about himself while his voice is masked
(after six quiet minutes, so nothing carries his voice; he is alone in view), and one line from a voice
like nobody known (refused). Arms: `before` swaps in the persona and voice reminder at --before; `after` is the
working tree. Production Session and state owner, real reply model, temporary database and photo album,
synthetic audio, fake clock. Reported per run: lines saved (facts and Pepper notes), introduce_person
calls, and Rex lines that talk about saving or remembering. Text only: no hearing, timing or body.
"""
import argparse
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x.adapters import hosted
from r3x.contracts import Capture, Cancellation, ToolCall, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.photo_memory import PhotoMemory
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tools.check_agency_beats import NS, VOICE, Paced, Trace
from tools.check_curiosity import old_prompts
from tools.check_impression_beats import Sink

MASKED = (.30, .954, 0.)     # .30 to Sam's voice: his own words under Rex's motor noise.
STRANGER = (0., 0., 1.)      # Sounds like nobody known.
# (what Sam says, his voice, whether a saved line is expected, the kind of line)
SCRIPT = (
    ("Pepper was my brother's dog. When he moved to Japan he couldn't take her, so I took her in.", VOICE, True, 'story'),
    ("I drove six hours to get her, and the first thing she did was fall asleep on my shoes.", VOICE, True, 'story'),
    ("My brother's name is Theo.", VOICE, True, 'relative'),
    ("I hate paperwork.", MASKED, True, 'masked'),
    ("I used to get breakfast at gas stations every morning before high school.", MASKED, True, 'masked'),
    ("I'm a museum curator, by the way.", STRANGER, False, 'stranger'),
)
SAVE_TALK = re.compile(r"\b(sav(e|ed|ing)|not(e|ed)|stor(e|ed)|record(ed)?|memor(y|ies|ize|ized)|file[ds]?|"
                       r"remember(ed)?|forget|add(ed)? (that|it)|keep (that|it) in mind)\b", re.I)
SAVES = {'record_claim', 'remember_interest', 'remember_followup', 'edit_object_memory'}


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
            brain.trace = trace
            session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), Sink(trace), trace), trace, state)
            brain.start()

            def capture(seconds=3., motion=False):
                trace.at += 2
                return Capture(uuid4().hex, session.session_id, uuid4().bytes * int(2000 * seconds), trace.at - seconds,
                               trace.at, trace.at, voiced_intervals=((0., seconds),), motion_risk=motion)

            def observation(vector=VOICE):
                return VoiceObservation(NS, vector, ((0., 2., vector),), 3., 1.)

            for text in ('My name is Sam.', 'I really love astronomy.', 'Mostly I talk about my dog.'):
                state.begin(uuid4().hex, capture(), Transcript(text, -.01, True), observation())
                if text.startswith('My name'):
                    state.execute(ToolCall(uuid4().hex, 'introduce_person', {'name': 'Sam', 'aliases': [],
                        'subject': 'self', 'person_id': None, 'face_track_id': None}), store.revision, Cancellation())
                state.advance_enrollment()
            state.current = None
            sam = next(p['id'] for p in store.rows('SELECT id FROM people'))

            def faces(now):
                return [{'track_id': 'face-sam', 'identity': sam, 'face_epoch': 0, 'at': now - .2,
                         'track_first_seen': now - 60, 'box': [.45, .3, .1, .12], 'face_width_px': 192,
                         'learning_quality': False, 'needs_closer_look': False, 'position': 'center',
                         'evidence': 'face_match'}]

            turns, paused = [], False
            for text, vector, expected, kind in SCRIPT:
                trace.at += 20
                masked = vector is MASKED
                if masked and not paused:
                    trace.at += 360       # Six quiet minutes end the carried voice (CARRY_GAP_SECONDS).
                    paused = True
                first = len(trace.rows)
                session.note_speech(trace.at)
                session.process_turn(capture(motion=masked), Transcript(text, -.01, True), session.revision,
                                     observation(vector), faces=faces(trace.at), latest_faces=faces)
                rows = trace.rows[first:]
                said = ' '.join(' '.join(r['completed_text']) if isinstance(r['completed_text'], list) else r['completed_text']
                                for r in rows if r['event'] == 'delivery' and r.get('completed_text'))
                results = [r for r in rows if r['event'] in ('tool_result', 'photo_tool_result') and r.get('name') in SAVES]
                proposed = [r['name'] for r in rows if r['event'] == 'tool_proposed']
                turns.append({'kind': kind, 'sam': text, 'rex': said, 'tools': proposed,
                              'saved': any(r['outcome'] == 'ok' for r in results),
                              'statuses': [(r.get('data') or {}).get('status') or r.get('reason') for r in results],
                              'expected_save': expected, 'save_talk': bool(SAVE_TALK.search(said))})
                trace.emit('probe_human', text=text)
            session.close()
            notes = album.data['entities']['pepper-id']['notes']
            claims = [dict(r) for r in store.rows('SELECT predicate,value,status,said_by FROM claims')]
        finally:
            album.close()
            store.close()
    expected = [t for t in turns if t['expected_save']]
    return {'turns': turns, 'pepper_notes': notes, 'claims': claims, 'metrics': {
        'saved_of_expected': f"{sum(t['saved'] for t in expected)}/{len(expected)}",
        'stories_saved': sum(t['saved'] for t in turns if t['kind'] == 'story'),
        'masked_saved': sum(t['saved'] for t in turns if t['kind'] == 'masked'),
        'introductions': sum(t['tools'].count('introduce_person') for t in turns),
        'save_talk_lines': [t['rex'] for t in turns if t['save_talk']]}}


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
    folder = ROOT / 'local' / 'validation' / f'saving-{uuid4().hex[:8]}'
    folder.mkdir(parents=True)
    current = {'CHARACTER': hosted.CHARACTER, 'PERSONA_TAIL': hosted.PERSONA[len(hosted.CHARACTER):],
               'VOICE_REMINDER': hosted.VOICE_REMINDER}
    before = old_prompts(args.before)
    results = {}
    for arm in args.arms.split(','):
        prompts = before if arm == 'before' else current
        hosted.CHARACTER = prompts['CHARACTER']
        hosted.PERSONA = prompts['CHARACTER'] + prompts['PERSONA_TAIL']
        hosted.VOICE_REMINDER = prompts['VOICE_REMINDER']
        for index in range(args.runs):
            result = run_once(arm, index, key, folder)
            results.setdefault(arm, []).append(result)
            print(json.dumps({'arm': arm, 'run': index, **result['metrics']}, ensure_ascii=False), flush=True)
    (folder / 'results.json').write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(folder)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
