"""Paid, paced hosted check: does the conversation model choose and write impressions well?

Runs the production Session with the real OpenAI brain, a temporary store, synthetic
audio output and a fake voice cloner (no Breeze, no speaker). Each scripted turn costs
one hosted call (two when a tool is rejected). The report records which tool the model
chose, the target, the script and its length. Pace: a few seconds between calls, as the
owner's hosted-evaluation rule requires. Results go under local/validation/impersonation/.
"""
import json
from pathlib import Path
import tempfile
import threading
import time
from uuid import uuid4

from r3x import impersonation
from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import ScratchPaths, Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

TURNS = (
    ('request_famous', 'Hey Rex, do an impression of Jimmy Carter.'),
    ('mention_famous', "My wife and I are driving down to Plains, Georgia next weekend to see the old peanut farm."),
    ('request_self_not_ready', 'Rex, do an impression of me.'),
    ('ordinary', 'What should I make for dinner tonight, something quick?'),
)


class FakeTake:
    def __init__(self, text, reference):
        self.text, self.reference = text, reference
        self.ready = threading.Event()
        self.ready.set()
        self.error = None
        self.stats = {}
        self.pcm = b'\0\0' * 2205 * 4

    def chunks(self, cancel):
        for offset in range(0, len(self.pcm), 4410):
            yield self.pcm[offset:offset + 4410]

    def close(self):
        pass


class FakeCloner:
    started = True
    def prepare(self, text, reference):
        return FakeTake(text, reference)
    def release_idle(self, now=None):
        pass


def main() -> int:
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    out = paths.root / 'validation' / 'impersonation'
    out.mkdir(parents=True, exist_ok=True)
    secrets = Secrets.load(paths.root)
    brain = OpenAIBrain(secrets.openai_api_key)
    brain.start()
    report = {'model': brain.capability.model, 'turns': [], 'evidence': 'hosted model with synthetic audio and a fake cloner'}
    with tempfile.TemporaryDirectory() as directory:
        memory = Memory(Path(directory).resolve() / 'core.sqlite3')
        memory.start()
        trace_path = out / (time.strftime('%Y%m%dT%H%M%S') + '.jsonl')
        trace = Trace(uuid4().hex, trace_path, display=lambda row: None)
        brain.trace = trace
        state = CoreState(memory, SpeakerTracker('synthetic-probe:3'), trace)
        state.voice_references = impersonation.ReferenceStore(Path(directory) / 'voice_refs')
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace, cloner=FakeCloner())
        session = Session(uuid4().hex, brain, output, trace, state)
        try:
            for label, text in TURNS:
                now = time.monotonic()
                capture = Capture(uuid4().hex, session.session_id, b'\0\0' * 48000, now - 3, now, now,
                                  voiced_intervals=((0., 3.),), device='hosted probe')
                vector = (0., 0., 1.)
                observation = VoiceObservation('synthetic-probe:3', vector, ((0., 3., vector),), 3., 1., False, 'synthetic')
                delivery = session.process_turn(capture, Transcript(text, -.01, True), session.revision, observation)
                proposed = [r for r in read_rows(trace_path) if r['event'] == 'tool_proposed'][-1:]
                row = {'label': label, 'text': text, 'status': delivery.status, 'heard': delivery.completed_text,
                       'tool': proposed[0]['name'] if proposed else None,
                       'arguments': proposed[0]['arguments'] if proposed else None}
                if row['tool'] == 'impersonate':
                    row['script_words'] = len(row['arguments']['script'].split())
                report['turns'].append(row)
                print(json.dumps(row, ensure_ascii=False))
                time.sleep(4.)
        finally:
            session.close()
            memory.close()
            trace.close()
            brain.close()
    (out / 'report.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps({'report': str(out / 'report.json')}))
    return 0


def read_rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


if __name__ == '__main__':
    raise SystemExit(main())
