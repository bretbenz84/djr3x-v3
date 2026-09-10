import json
from types import SimpleNamespace
import threading
import unittest

from r3x.adapters.audio import AudioSink, resolve_device
from r3x.adapters.hosted import OpenAIBrain, ElevenLabsTTS
from r3x.contracts import Cancellation, Cancelled, ProviderFailure, Snapshot


class Response:
    status_code = 200
    def __init__(self, events=(), chunks=()):
        self.events, self.chunks, self.closed = events, chunks, False
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def iter_lines(self):
        for item in self.events: yield "data: " + json.dumps(item)
    def iter_bytes(self, chunk_size): yield from self.chunks


class Client:
    def __init__(self, response): self.response, self.calls = response, []
    def stream(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class AdapterTests(unittest.TestCase):
    def test_openai_one_stream_preserves_text_and_checks_completion(self):
        response = Response([{"type":"response.output_text.delta", "delta":"Hello."}, {"type":"response.completed"}])
        brain = OpenAIBrain("synthetic-key")
        brain.client = Client(response)
        result = list(brain.reply(Snapshot("s", "t", 0, ({"role":"user", "content":"original"},)), Cancellation()))
        self.assertEqual(result, ["Hello."])
        self.assertEqual(len(brain.client.calls), 1)
        payload = brain.client.calls[0][1]["json"]
        self.assertFalse(payload["store"])
        self.assertEqual(payload["input"][0]["content"], "original")
        self.assertNotIn("tools", payload)
        self.assertTrue(response.closed)

    def test_model_truncation_failure_and_cancellation_are_not_success(self):
        for events in ([], [{"type":"response.incomplete"}], [{"type":"error"}]):
            brain = OpenAIBrain("synthetic")
            brain.client = Client(Response(events))
            with self.assertRaises(ProviderFailure): list(brain.reply(Snapshot("s", "t", 0, ()), Cancellation()))
        cancel = Cancellation()
        cancel.event.set()
        with self.assertRaises(Cancelled): list(brain.reply(Snapshot("s", "t", 0, ()), cancel))

    def test_tts_request_retains_v2_voice_policy_and_rejects_bad_audio(self):
        tts = ElevenLabsTTS("synthetic-key", "synthetic-voice")
        tts.client = Client(Response(chunks=[b"\0\0"] * 3))
        self.assertEqual(b"".join(tts.synthesize("[curious] Hello.", Cancellation())), b"\0\0" * 3)
        request = tts.client.calls[0][1]
        self.assertEqual(request["params"]["output_format"], "pcm_22050")
        self.assertEqual(request["json"]["voice_settings"], {"stability": .5})
        self.assertEqual(request["json"]["seed"], 1440639067)
        self.assertNotIn("previous_text", request["json"])
        tts.client = Client(Response(chunks=[b"x"]))
        with self.assertRaises(ProviderFailure): list(tts.synthesize("Hello", Cancellation()))

    def test_http_failure_has_no_secret_and_no_retry(self):
        response = Response()
        response.status_code = 401
        tts = ElevenLabsTTS("private-value", "private-voice")
        tts.client = Client(response)
        with self.assertRaisesRegex(ProviderFailure, "ElevenLabsHTTP401") as caught:
            list(tts.synthesize("Hi", Cancellation()))
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(len(tts.client.calls), 1)

    def test_device_name_wins_and_missing_name_does_not_fall_back(self):
        devices = [{"name":"Mic", "max_input_channels":1, "max_output_channels":0},
                   {"name":"Speaker", "max_input_channels":0, "max_output_channels":2}]
        self.assertEqual(resolve_device(devices, "Mic", 1, "input"), 0)
        with self.assertRaises(ProviderFailure): resolve_device(devices, "Old mic", 0, "input")
        with self.assertRaises(ProviderFailure): resolve_device(devices*2, "Mic", 0, "input")
        self.assertIsNone(resolve_device(devices, "", None, "output"))

    def test_sink_drains_the_final_phrase_and_handles_odd_network_boundaries(self):
        class Stop(Exception): pass
        class Abort(Exception): pass
        captured = bytearray()
        class Stream:
            def __init__(self, **kwargs): self.kwargs, self.worker = kwargs, None
            def start(self):
                def run():
                    for _ in range(1000):
                        data = bytearray(2048)
                        try:
                            self.kwargs["callback"](data, 1024,
                                SimpleNamespace(outputBufferDacTime=10, currentTime=10),
                                SimpleNamespace(output_underflow=False))
                        except Stop:
                            captured.extend(data)
                            self.kwargs["finished_callback"]()
                            return
                        except Abort:
                            self.kwargs["finished_callback"]()
                            return
                        captured.extend(data)
                        threading.Event().wait(.001)
                self.worker = threading.Thread(target=run)
                self.worker.start()
            def abort(self): pass
            def close(self): self.worker.join(2)
        sink = AudioSink()
        sink.sd = SimpleNamespace(RawOutputStream=Stream, CallbackStop=Stop, CallbackAbort=Abort)
        expected = b"\x01\x02" * 5000
        parts = iter([expected[:3], expected[3:4096], expected[4096:8192], expected[8192:]])
        starts = []
        receipt = sink.play(parts, Cancellation(), starts.append)
        self.assertEqual(receipt.frames, 5000)
        self.assertEqual(bytes(captured[:len(expected)]), expected)
        self.assertEqual(len(starts), 1)
        self.assertIsNone(sink.stream)
