"""Explicit hosted checks: fictional identity, production Session, synthetic playback."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import json
import time
from uuid import uuid4

from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import ProviderFailure
from r3x.settings import Secrets
from tests.test_conversation_memory import ConversationMemoryTests
from tests.test_proactive import event


class Hosted(OpenAIBrain):
    def _wait_retry(self, seconds, cancel):
        raise ProviderFailure('ProbeTransportUnavailable')


def main():
    root=Path(__file__).resolve().parents[1]/'local'
    folder=root/'validation'/('conversation-memory-'+uuid4().hex)
    folder.mkdir(parents=True)
    fixture=ConversationMemoryTests(); fixture.setUp(); fixture.setup_memory()
    fixture.clock=datetime(2026,9,30,9,tzinfo=timezone.utc).timestamp()
    fixture.state.calendar_today=lambda:datetime.fromtimestamp(fixture.clock).date()
    brain=Hosted(Secrets.load(root).openai_api_key)
    brain.trace=fixture.state.trace
    results=[]
    try:
        fixture.turn(fixture.session('prior'),'I finished the telescope mount, but the tracking motor still needs testing.')
        fixture.begin('I like astronomy.')
        fixture.call('record_claim',dict(subject_id=fixture.person,predicate='likes',value='astronomy',kind='self_report',source_turn_id=fixture.state.current[0]))
        fixture.clock += 7200
        brain.start()
        for name, words in [('recent','What did we talk about two hours ago?'),
                            ('missing','What did we discuss a year ago?'),('greeting',None),
                            ('episode','Save that prior telescope exchange as a shared memory.')]:
            session=fixture.session('new-'+name,brain)
            start=time.monotonic()
            try:
                if words: receipt=fixture.turn(session,words)
                else: receipt=session.process_opportunity(replace(event(fixture.person),session_id=session.session_id),0,lambda:True)
                row={'case':name,'wall_seconds':time.monotonic()-start,'receipt':asdict(receipt)}
                results.append(row);print(json.dumps(row),flush=True)
                if receipt.status=='failed': break
            finally: session.close()
            time.sleep(2)
    finally:
        brain.close()
        (folder/'results.json').write_text(json.dumps(results,indent=2))
        (folder/'trace.json').write_text(json.dumps(fixture.state.trace.rows,indent=2))
        fixture.tearDown()
        print(folder)


if __name__=='__main__': main()
