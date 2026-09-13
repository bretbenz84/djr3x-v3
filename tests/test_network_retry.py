import unittest
from types import SimpleNamespace
from r3x.adapters.hosted import OpenAIBrain
from r3x.contracts import Cancellation, Cancelled, ProviderFailure, Snapshot
from tests.test_adapters import Response, Client


class NetworkRetryTests(unittest.TestCase):
    def brain(self,client):
        brain=OpenAIBrain('synthetic');brain.client=client
        self.rows=[];self.delays=[]
        brain.trace=SimpleNamespace(emit=lambda event,**data:self.rows.append(dict(event=event,**data)))
        brain._wait_retry=lambda delay,cancel:(self.delays.append(delay),cancel.check())
        return brain

    def test_transport_failure_retries_same_source_then_recovers(self):
        class WriteTimeout(Exception):pass
        class Flaky(Client):
            def stream(self,*args,**kwargs):
                self.calls.append((args,kwargs))
                if len(self.calls)<3:raise WriteTimeout('private transport details')
                return self.response
        client=Flaky(Response([{'type':'response.output_text.delta','delta':'I heard you.'},
                               {'type':'response.completed'}]))
        brain=self.brain(client)
        snapshot=Snapshot('s','t',0,({'role':'user','content':'Please remember what I said.'},))
        self.assertEqual(list(brain.reply(snapshot,Cancellation())),['I heard you.'])
        self.assertEqual(self.delays,[2,4])
        self.assertEqual([c[1]['json']['input'] for c in client.calls],[list(snapshot.messages)]*3)
        self.assertEqual(self.rows[-1]['event'],'openai_recovered')
        self.assertNotIn('private transport details',str(self.rows))

    def test_auth_failure_does_not_loop(self):
        response=Response();response.status_code=401
        brain=self.brain(Client(response))
        with self.assertRaisesRegex(ProviderFailure,'OpenAIHTTP401'):
            list(brain.reply(Snapshot('s','t',0,()),Cancellation()))
        self.assertFalse(self.delays)

    def test_cancel_during_retry_does_not_submit_again(self):
        response=Response();response.status_code=503
        brain=self.brain(Client(response))
        def stop(delay,cancel):cancel.event.set();cancel.check()
        brain._wait_retry=stop
        with self.assertRaises(Cancelled):list(brain.reply(Snapshot('s','t',0,()),Cancellation()))
        self.assertEqual(len(brain.client.calls),1)

    def test_partial_reply_is_never_replayed(self):
        class ReadTimeout(Exception):pass
        class Partial(Response):
            def iter_lines(self):
                yield 'data: {"type":"response.output_text.delta","delta":"Already released."}'
                raise ReadTimeout()
        brain=self.brain(Client(Partial()))
        stream=brain.reply(Snapshot('s','t',0,()),Cancellation())
        self.assertEqual(next(stream),'Already released.')
        with self.assertRaisesRegex(ProviderFailure,'OpenAIReadTimeout'):next(stream)
        self.assertFalse(self.delays)
