"""Small explicit hosted evaluation with fictional dialogue and simulated output.

Runs the production Session; no devices, saved images or personal database. Read
the resulting replies for repetition/engagement: completed output is not a quality
score. Hosted requests are paced and transport failures stop rather than retry.
"""
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, ToolCall, Transcript, ProviderFailure
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.session import Session
from r3x.settings import Secrets, ScratchPaths
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace


class Hosted(OpenAIBrain):
    def _wait_retry(self, seconds, cancel):
        raise ProviderFailure('ProbeTransportUnavailable')


class Seed:
    def __init__(self, text):
        self.text = text

    def reply(self, snapshot, cancel):
        yield self.text


def main():
    root = Path(__file__).resolve().parents[1] / 'local'
    ScratchPaths(root).prepare()
    folder = root / 'validation' / ('engagement-' + uuid4().hex)
    folder.mkdir(parents=True)
    # Fixed during each request, advanced between turns: live microphone/camera
    # freshness is simulated without opening devices or spawning capture threads.
    offset = [1000.]
    trace = Trace('fictional-engagement', folder/'trace.jsonl',
                  clock=lambda: offset[0], display=lambda row: None)
    brain = Hosted(Secrets.load(root).openai_api_key)
    brain.trace = trace
    brain.start()
    rows = []
    cases = (
        ('new_angle', [('I like astronomy and built my own telescope.',
            'That telescope looks ready to interrogate the moon.'),
            ('It took six months to make the mount.',
             'It looks ready to negotiate with Saturn.')], None),
        ('change_subject', [('I like astronomy.', 'Your telescope means business.')],
            'You already said that. Change the subject.'),
        ('invite_engagement', [('I like building musical instruments.',
            'That sounds like an interesting hobby.')], 'Why are you not saying anything?'),
        ('unanswered_question', [('I like building musical instruments.',
            'What instruments have you built?')], None),
        ('respect_quiet', [('We lost a friend yesterday.', 'I am here with you.')],
            'Please just sit quietly with me.'),
        ('respect_human_floor', [('Sam, how did your interview go?', None)], None),
        ('rejected_memory_reply', [], 'I built this telescope myself.'),
    )
    try:
        for name, seeds, utterance in cases:
            store = Memory(':memory:'); store.start()
            state = CoreState(store, SpeakerTracker('synthetic:3'), trace)
            with store.transaction() as conn:
                person = store.add_person(conn, 'Casey', [], None)
            session = Session(uuid4().hex, Seed('Hello.'),
                OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace, state)

            def faces(at=None):
                return ({'at':trace.clock() if at is None else at, 'track_id':'visitor', 'identity':person,
                    'face_epoch':0, 'face_width_px':150, 'box':[.3,.2,.2,.3], 'position':'center'},)

            def human(text):
                offset[0] += 10
                at = trace.clock()
                capture = Capture(uuid4().hex, session.session_id, bytes(96000), at-4, at-1, at,
                    voiced_intervals=((0,3),), device='synthetic')
                session.note_speech(at-1)
                return session.process_turn(capture, Transcript(text, -.01, True), session.revision,
                    VoiceObservation('synthetic:3'), faces())

            def lull():
                offset[0] += 15
                now = trace.clock()
                runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace,
                    perception=SimpleNamespace(camera=SimpleNamespace(latest=faces)))
                runtime.started_at = now-20
                runtime.audio_seen_at = now
                for delta in (-3,-2,-1,0):
                    runtime.arrivals.observe([{**faces()[0], 'at':now+delta}], now+delta)
                runtime.arrivals.seen[person]['used'] = True
                return runtime.proactive_tick()

            def receipt_data(receipt):
                return asdict(receipt) if receipt else {'status':'not_dispatched'}

            try:
                for text, reply in seeds:
                    if reply is None:
                        class Silent:
                            def reply(self, snapshot, cancel):
                                yield ToolCall(uuid4().hex, 'remain_silent', {'reason':'other_addressee'})
                        session.brain = Silent()
                    else:
                        session.brain = Seed(reply)
                    human(text)
                session.brain = brain
                if name == 'rejected_memory_reply':
                    # Force only the attempted write; the real hosted model chooses
                    # the response to the production validator's rejection.
                    class RejectedSave:
                        def reply(self, snapshot, cancel):
                            if snapshot.continuation:
                                yield from brain.reply(snapshot, cancel)
                            else:
                                yield ToolCall('save', 'record_claim', dict(subject_id=person,
                                    subject_reference=None, predicate='built', value='a telescope',
                                    kind='self_report', source_turn_id=snapshot.context['source_turn_id']))
                    session.brain = RejectedSave()
                started = time.monotonic()
                receipt = human(utterance) if utterance else lull()
                row = {'case':name, 'receipt':receipt_data(receipt),
                       'wall_seconds':time.monotonic()-started}
                if name == 'invite_engagement':
                    time.sleep(5)
                    row['followup'] = receipt_data(lull())
                rows.append(row)
                print(json.dumps(row), flush=True)
            finally:
                session.close(); store.close()
            time.sleep(5)
    finally:
        brain.close(); trace.close()
        (folder/'results.json').write_text(json.dumps({'evidence':
            'Hosted fictional text; synthetic output and identities; no physical audio or visual accuracy evidence.',
            'rows':rows}, indent=2))
        print(folder)


if __name__ == '__main__':
    main()
