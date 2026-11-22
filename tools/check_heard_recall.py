"""Explicit paid probe: asked whether he heard them, Rex says what they said (November 22, run 93d1accd).

Live, "You do live with me." went unanswered; asked "Did you hear what I said?" he claimed to have heard
every word four times and never said what it was. Production Session, text output, a fictional person,
temporary database. Claude by default (settings.llm_provider). Replies print for reading; the pass marks are
a rough keyword screen, not a judgement of the line.

Run: .venv/bin/python tools/check_heard_recall.py [--provider claude|openai] [--repeat N]
"""
import argparse
import json
from pathlib import Path
import re
import tempfile
from uuid import uuid4

from r3x.contracts import Capture, Transcript
from r3x.memory import Memory
from r3x.output import TextOutput
from r3x.session import Session
from r3x.settings import ScratchPaths, Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

NS = 'synthetic-heard-recall:1'
VOICE = (1., 0., 0.)
GREETING = "Sam, a whole day gone and you come crawling back. Eleven of fourteen days, that's basically living with me."
DODGES = re.compile(r'every word|loud and clear|heard (you|it|that) (fine|perfectly|loud)|quote you|transcript|permanent record', re.I)

# Each case: setup lines (said, answered, yielded or 'noise'), then the question and the words the answer should carry.
CASES = {
    'yielded_then_asked': ([('You do live with me.', None, True)], 'Did you hear what I said?', r'live'),
    'yielded_then_pressed': ([('You do live with me.', None, True), ('Did you hear what I said?', 'ask', False)],
                             'Okay, so what did I say?', r'live'),
    'answered_then_asked': ([('I finally fixed the porch light today.', 'ask', False)],
                            'What did I just tell you?', r'porch|light'),
    'nothing_heard': ([], 'Did you hear what I said?', None),
    'only_noise': ([('Yeah.', None, 'noise')], 'Did you hear what I said?', None),   # His motors, untrusted.
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--provider', choices=('claude', 'openai'), default='claude')
    parser.add_argument('--repeat', type=int, default=1)
    args = parser.parse_args()
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1]); paths.prepare()
    secrets = Secrets.load(paths.root)
    folder = paths.root / 'validation' / ('heard-recall-' + uuid4().hex[:8]); folder.mkdir(parents=True)
    results = []
    for round_ in range(args.repeat):
        for case, (setup, question, expect) in CASES.items():
            with tempfile.TemporaryDirectory() as tmp:
                results.append(run_case(case, setup, question, expect, args.provider, secrets, folder, tmp, round_))
                print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    passed = sum(r['passed'] for r in results)
    print(f'{passed}/{len(results)} passed the keyword screen; traces in {folder}')
    (folder / 'results.json').write_text(json.dumps(results, indent=1, ensure_ascii=False))


def run_case(case, setup, question, expect, provider, secrets, folder, tmp, round_):
    store = Memory(Path(tmp).resolve() / 'core.sqlite3'); store.start()
    trace = Trace(case, folder / f'{case}-{round_}.jsonl', display=lambda row: None)
    if provider == 'claude':
        from r3x.adapters.claude import ClaudeBrain
        brain = ClaudeBrain(secrets.anthropic_api_key)
    else:
        from r3x.adapters.hosted import OpenAIBrain
        brain = OpenAIBrain(secrets.openai_api_key)
    brain.trace = trace; brain.start()
    session = Session(uuid4().hex, brain, TextOutput(trace), trace, CoreState(store, SpeakerTracker(NS), trace))
    reply = brain.reply
    yielding = []
    def maybe_yield(snapshot, cancel):
        if yielding:   # Their next recording had begun: this reply yields unheard, as live.
            yielding.clear()
            session.note_speech(trace.clock())   # Just after this capture, before the next.
            cancel.check()
        yield from reply(snapshot, cancel)
    brain.reply = maybe_yield
    obs = VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1., False, 'synthetic_not_audio_accuracy')
    def turn(text, trusted=True):
        now = trace.clock()
        capture = Capture(uuid4().hex, session.session_id, uuid4().bytes * 6000, now - 3, now - .2, now,
                          voiced_intervals=((0., 3.),), device='synthetic fixture')
        return session.process_turn(capture, Transcript(text, -.01 if trusted else -.75, trusted), session.revision, obs)
    try:
        session.history.append({'role': 'assistant', 'content': GREETING})
        earlier = []
        for said, answered, yielded in setup:
            if yielded is True:
                yielding.append(True)
            receipt = turn(said, trusted=yielded != 'noise')
            earlier.append({'said': said, 'status': receipt.status, 'reply': ' '.join(receipt.completed_text)})
        receipt = turn(question)
        line = ' '.join(receipt.completed_text)
        if expect:
            passed = bool(re.search(expect, line, re.I)) and not DODGES.search(line)
        else:   # Nothing of theirs was heard: no claim to have heard it.
            passed = bool(line) and not DODGES.search(line) and not re.search(r'\bI heard\b', line, re.I)
        return {'case': case, 'round': round_, 'earlier': earlier, 'question': question, 'reply': line,
                'status': receipt.status, 'passed': passed}
    finally:
        session.close(); store.close()


if __name__ == '__main__':
    main()
