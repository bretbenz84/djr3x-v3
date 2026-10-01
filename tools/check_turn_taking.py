"""Explicit hosted turn-taking probe; fictional text, temporary stores, no devices."""
from dataclasses import asdict
from pathlib import Path
import json
import time
from uuid import uuid4

from r3x.contracts import Capture, Transcript
from r3x.output import OutputOwner
from r3x.proactive import Opportunity
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Secrets
from tests.test_memory_core import MemoryFixture, observation
from tests.test_proactive import face
from tests.test_voice_core import Brain
from tools.check_conversation_recall import Hosted

CASES = [
    ('queued_ack', 'The router keeps restarting.', 'Does it lose power, or only the connection?', 'Mm-hmm.', True, 'suppressed'),
    ('ambiguous_answer', 'The router keeps restarting.', 'Was it power loss or overheating?', 'It was.', False, 'suppressed'),
    ('incidental_sound', 'I am relaxing.', 'Enjoy the quiet.', 'Cough.', False, 'suppressed'),
    ('correction', 'The network failed.', 'Was the whole building affected?', 'Only the workshop, not the entire building.', False, 'completed'),
    ('question', 'We fixed the network.', 'Glad it is working again.', 'What does VLAN mean?', False, 'completed'),
    ('continue', 'I am curious about astronomy.', 'There is a simple way to find Saturn.', 'Go on.', False, 'completed'),
    ('short_consent', 'I am curious about astronomy.', 'Would you like me to explain how to find Saturn?', 'Yes.', False, 'completed'),
    ('unanswered_lull', 'The router keeps restarting.', 'Does it lose power, or only the connection?', None, False, 'suppressed'),
]


def main():
    root=Path(__file__).resolve().parents[1]/'local'
    folder=root/'validation'/('turn-taking-'+uuid4().hex);folder.mkdir(parents=True)
    brain=Hosted(Secrets.load(root).openai_api_key);brain.start()
    results=[];traces=[]
    try:
        for name, first, reply, words, delayed, expected in CASES:
            fixture=MemoryFixture();fixture.setUp()
            fixture.state.trace.clock=time.monotonic
            fixture.enroll('Alex')
            trace=fixture.state.trace;brain.trace=trace
            session=Session(name,Brain(reply),OutputOwner(SyntheticTTS(),SyntheticSink(),trace),trace,fixture.state)
            def turn(text, end=None):
                now=time.monotonic();end=now if end is None else end
                cap=Capture(uuid4().hex,name,bytes(96000),end-3,end,end,voiced_intervals=((0.,3.),))
                return session.process_turn(cap,Transcript(text,0,True),0,observation())
            try:
                turn(first);session.brain=brain
                start=time.monotonic()
                if words is None:
                    now=time.monotonic()
                    event=Opportunity(uuid4().hex,name,now,None,None,(face(now,track='visitor'),),
                        session._latest_speech,False,'conversation_lull',fixture.state.current[0],quiet_seconds=20.)
                    receipt=session.process_opportunity(event,0,lambda:True)
                else:
                    end=session.last_spoken_reply['started_at']-.1 if delayed else None
                    receipt=turn(words,end)
                row={'case':name,'expected':expected,'passed':receipt.status==expected,
                     'wall_seconds':time.monotonic()-start,'receipt':asdict(receipt)}
                results.append(row);print(json.dumps(row),flush=True)
                traces.extend(dict(row,case=name) for row in trace.rows)
            finally:
                session.close();fixture.tearDown()
            if receipt.status=='failed':break
            time.sleep(2)
    finally:
        brain.close()
        (folder/'results.json').write_text(json.dumps(results,indent=2))
        (folder/'trace.json').write_text(json.dumps(traces,indent=2))
        print(folder)
    return len(results)==len(CASES) and all(row['passed'] for row in results)


if __name__=='__main__':
    raise SystemExit(0 if main() else 1)
