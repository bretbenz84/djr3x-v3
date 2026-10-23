"""Explicit paid probe: does the real reply model put Rex on guard with the settings asked for, leave a
question about sentry mode alone, and speak for the guard in words? Fictional input.

Run: .venv/bin/python tools/check_sentry.py --live

Owner request, October 22: "activate sentry mode until 7pm", "for 1 hour", "with no audio alerts",
"silent with 60 second activation good for 9 hours". Each request goes through the production Session
and state owner with the real reply model; the guard is a stand-in that records the checked plan
(nothing moves, nothing is spoken aloud). Then one alarm line and one stand-down greeting go through
the same owner as the guard sends them, without an image. About ten requests, a few cents, paced.
Printed per case: the plan, or the words and any tool.
"""
import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from r3x import sentry as rules
from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Capture, Transcript
from r3x.memory import Memory
from r3x.output import OutputOwner
from r3x.proactive import Opportunity
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from r3x.trace import Trace

NS, VOICE = 'synthetic-sentry:3', (1., 0., 0.)
# (said, expected countdown, minutes, until, audio alerts); None expects no sentry_mode call.
REQUESTS = (
    ('Activate sentry mode.', None, None, None, True),
    ('Rex, activate sentry mode until 7pm.', None, None, '19:00', True),
    ('Activate sentry mode for 1 hour.', None, 60, None, True),
    ('Activate sentry mode with no audio alerts.', None, None, None, False),
    ('Activate sentry mode silent with 60 second activation good for 9 hours.', 60, 540, None, False),
    ('Sentry mode, 10 second countdown, until midnight.', 10, None, '00:00', True),
    ('What would sentry mode even look like for you?',),
)


def setup(key, folder, name):
    tmp = TemporaryDirectory()
    store = Memory(Path(tmp.name).resolve() / 'core.sqlite3')
    store.start()
    trace = Trace(name, folder / f'{name}.jsonl', display=lambda row: None)
    rows, emit = [], trace.emit
    def recording(event, **data):
        rows.append({'event': event, **data})
        emit(event, **data)
    trace.emit = recording
    state = CoreState(store, SpeakerTracker(NS), trace)
    brain = OpenAIBrain(key)
    brain.trace = trace
    brain.start()
    session = Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace, state)
    return SimpleNamespace(tmp=tmp, store=store, trace=trace, rows=rows, state=state, brain=brain, session=session)


def close(rig):
    rig.session.close()
    rig.brain.close()
    rig.store.close()
    rig.tmp.cleanup()


def turn(rig, text):
    now = rig.trace.clock()
    capture = Capture(uuid4().hex, rig.session.session_id, uuid4().bytes * 2000, now - 3, now, now,
                      voiced_intervals=((0., 3.),))
    return rig.session.process_turn(capture, Transcript(text, -.01, True), rig.session.revision,
                                    VoiceObservation(NS, VOICE, ((0., 2., VOICE),), 3., 1.))


def request(key, folder, said, *expected):
    rig = setup(key, folder, 'request-' + uuid4().hex[:6])
    started = []
    rig.session.sentry = SimpleNamespace(active=False, reply_context=lambda opportunity: None)
    rig.session.on_sentry = lambda data, turn_id, requester: started.append(data)
    try:
        result = turn(rig, said)
        plan = started[0] if started else None
        got = (plan['countdown_seconds'], plan['until_text'], plan['audio_alerts']) if plan else None
        tool = [r for r in rig.rows if r['event'] == 'tool_proposed' and r.get('name') == 'sentry_mode']
        arguments = tool[0]['arguments'] if tool else None
        if expected:
            countdown, minutes, until, audio = expected
            ok = bool(arguments) and plan is not None and (
                plan['countdown_seconds'] == (countdown or 30) and arguments['duration_minutes'] == minutes
                and arguments['until'] == until and plan['audio_alerts'] == audio)
        else:
            ok = plan is None
        return {'said': said, 'ok': ok, 'arguments': arguments, 'plan': got,
                'spoken': ' '.join(result.completed_text) or None, 'status': result.status}
    finally:
        close(rig)


def line(key, folder, kind):
    """One line spoken for the guard, as SentryGuard sends it (no image in this probe)."""
    rig = setup(key, folder, kind + '-' + uuid4().hex[:6])
    try:
        person = None
        if kind == 'sentry_stand_down':
            with rig.store.transaction() as conn:
                person = rig.store.add_person(conn, 'Bret', [], None, status='supported')
            note = rules.STAND_DOWN_NOTE.format(name='Bret', intrusion='')
            context = {'phase': 'off', 'stood_down': True}
        else:
            rig.state.sentry.begin(rules.SentryPlan(30, None, True, time.time()))
            rig.state.sentry.arm(rig.trace.clock())
            event = rig.state.sentry.trip('speech', rig.trace.clock(), 'probe')
            # Ordinary Rex's history is still there, as on the robot: the guard's line must not use it.
            turn(rig, 'Look at my new telescope, Rex. I just set it up by the window.')
            event.exchange += [('intruder', 'Hello? Is anybody home right now?'), ('rex', rules.ALERT)]
            messages = rules.alarm_messages('sentry_alarm', event, False)   # As sent without a picture.
            note, context = messages[0]['content'], None
        event = Opportunity(uuid4().hex, rig.session.session_id, rig.trace.clock(), None, person, (),
                            rig.session._latest_speech, False, kind)
        reply = {'context': context or rig.state.sentry.context(rig.trace.clock(), intruder_in_view=False),
                 'note': note, 'images': (), **({} if context else {'messages': messages})}
        rig.session.sentry = SimpleNamespace(active=kind != 'sentry_stand_down', reply_context=lambda opportunity: reply)
        result = rig.session.process_opportunity(event, rig.session.revision, lambda: True)
        tools = [r.get('name') for r in rig.rows if r['event'] == 'tool_proposed']
        return {'kind': kind, 'spoken': ' '.join(result.completed_text), 'status': result.status, 'tools': tools}
    finally:
        close(rig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--live', action='store_true', required=True)
    parser.parse_args()
    key = Secrets.load(ROOT / 'local').openai_api_key
    folder = ROOT / 'local' / 'validation' / ('sentry-' + uuid4().hex[:8])
    folder.mkdir(parents=True)
    passed = 0
    for said, *expected in REQUESTS:
        row = request(key, folder, said, *expected)
        passed += row['ok']
        print(json.dumps(row))
        time.sleep(5)
    for kind in ('sentry_alarm', 'sentry_stand_down'):
        print(json.dumps(line(key, folder, kind)))
        time.sleep(5)
    print(json.dumps({'requests_passed': passed, 'of': len(REQUESTS), 'traces': str(folder)}))


if __name__ == '__main__':
    main()
