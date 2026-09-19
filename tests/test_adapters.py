import json
from types import SimpleNamespace
import threading
import unittest

from r3x.adapters.audio import AudioSink, resolve_device
from r3x.adapters.hosted import OpenAIBrain, ElevenLabsTTS
from r3x.contracts import Cancellation, Cancelled, ProviderFailure, Snapshot,ToolCall
from r3x.semantics import grounded_tools


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
    def test_intermediate_commentary_is_not_speech_and_retains_its_phase(self):
        item={"type":"message","id":"msg","role":"assistant","phase":"commentary","status":"completed",
              "content":[{"type":"output_text","text":"I will save that.","annotations":[]}]}
        events=[{"type":"response.output_item.added","output_index":0,"item":item},
                {"type":"response.output_text.delta","output_index":0,"delta":"I will save that."},
                {"type":"response.output_item.done","output_index":0,"item":item},
                {"type":"response.output_item.done","item":{"type":"function_call","call_id":"call","name":"recall","arguments":"{\"subject_id\":null}"}},
                {"type":"response.completed"}]
        brain=OpenAIBrain("fixture")
        brain.client=Client(Response(events))
        result=list(brain.reply(Snapshot("s","t",0,(),context={"source_turn_id":"t"},tools_enabled=True),Cancellation()))
        self.assertEqual(len(result),1)
        self.assertIsInstance(result[0],ToolCall)
        self.assertEqual(result[0].preamble[0]["phase"],"commentary")
        brain.client=Client(Response(events[:-1]))
        with self.assertRaises(ProviderFailure):list(brain.reply(Snapshot("s","t",0,()),Cancellation()))

    def test_final_answer_streams_before_completion_and_unphased_tool_text_is_held(self):
        events=[{"type":"response.output_item.added","output_index":0,"item":{"type":"message","phase":"final_answer"}},
                {"type":"response.output_text.delta","output_index":0,"delta":"Hello there."},{"type":"response.completed"}]
        brain=OpenAIBrain("fixture")
        brain.client=Client(Response(events))
        stream=brain.reply(Snapshot("s","t",0,(),context={"source_turn_id":"t"},tools_enabled=True),Cancellation())
        self.assertEqual(next(stream),"Hello there.")
        self.assertFalse(brain.client.response.closed)
        self.assertEqual(list(stream),[])
        events.pop(0)
        events.insert(1,{"type":"response.output_item.done","item":{"type":"function_call","call_id":"c","name":"recall","arguments":"{\"subject_id\":null}"}})
        brain.client=Client(Response(events))
        self.assertTrue(all(isinstance(x,ToolCall) for x in brain.reply(Snapshot("s","t",0,(),context={"source_turn_id":"t"},tools_enabled=True),Cancellation())))

    def test_grounded_identifiers_do_not_constrain_unrelated_text_fields(self):
        context={"people":[{"id":"person"}],"claims":[{"id":"claim"}],"source_turn_id":"turn","faces":[],"enrollment":[]}
        tools={tool["name"]:tool["parameters"]["properties"] for tool in grounded_tools(context)}
        self.assertEqual(tools["record_claim"]["source_turn_id"]["enum"],["turn"])
        self.assertEqual(tools["record_claim"]["subject_id"]["enum"],["person"])
        self.assertNotIn("enum",tools["record_claim"]["value"])
        self.assertNotIn("enum",tools["record_claim"]["predicate"])
        self.assertEqual(tools["introduce_person"]["face_track_id"]["enum"],[None])
        self.assertEqual(tools["introduce_person"]["person_id"]["enum"],["person",None])
        self.assertNotIn("enum",tools["introduce_person"]["name"])
        next_tools=grounded_tools(dict(context,source_turn_id="next"))
        self.assertEqual(tools["record_claim"]["source_turn_id"]["enum"],["turn"])

    def test_transmitted_tools_stay_byte_identical_while_offers_and_identifiers_change(self):
        import json
        payloads=[]
        for context in ({"source_turn_id":"turn-id-alpha","people":[{"id":"person"}],"claims":[{"id":"claim"}]},
                        {"source_turn_id":"second","trigger":"conversation_lull"}):
            brain=OpenAIBrain("fixture")
            brain.client=Client(Response([{"type":"response.output_text.delta","delta":"Hi."},{"type":"response.completed"}]))
            list(brain.reply(Snapshot("s","t",0,(),context=context,tools_enabled=True),Cancellation()))
            payloads.append(brain.client.calls[0][1]["json"])
        self.assertEqual(json.dumps(payloads[0]["tools"]),json.dumps(payloads[1]["tools"]))
        self.assertNotIn("turn-id-alpha",json.dumps(payloads[0]["tools"]))
        offered=[{t["name"] for t in p["tool_choice"]["tools"]} for p in payloads]
        self.assertEqual(offered[0],{t["name"] for t in grounded_tools({"source_turn_id":"turn-id-alpha","people":[{"id":"person"}],"claims":[{"id":"claim"}]})})
        self.assertIn("record_claim",offered[0])
        self.assertEqual(offered[1],{"remain_silent"})
        self.assertTrue(all(p["tool_choice"]["mode"]=="auto" and p["tool_choice"]["type"]=="allowed_tools" for p in payloads))

    def test_returned_identifiers_are_grounded_locally(self):
        context={"source_turn_id":"turn","people":[{"id":"person"},{"id":"other"}],"claims":[{"id":"claim"}]}
        def call(name,arguments):
            import json
            brain=OpenAIBrain("fixture")
            brain.client=Client(Response([{"type":"response.output_item.done","item":{"type":"function_call","call_id":"c","name":name,"arguments":json.dumps(arguments)}},
                                          {"type":"response.completed"}]))
            return list(brain.reply(Snapshot("s","t",0,(),context=context,tools_enabled=True),Cancellation()))[0]
        claim={"subject_id":"person","subject_reference":None,"predicate":"hobby","value":"sailing","kind":"self_report","source_turn_id":"invented"}
        # One permitted value is no model choice: it is supplied, as the strict enum forced it.
        self.assertEqual(call("record_claim",claim).arguments["source_turn_id"],"turn")
        with self.assertRaisesRegex(ProviderFailure,"UngroundedToolValue"):call("record_claim",dict(claim,subject_id="fabricated"))
        with self.assertRaisesRegex(ProviderFailure,"UngroundedToolValue"):call("recall",{"subject_id":"fabricated"})
        with self.assertRaisesRegex(ProviderFailure,"ToolNotOffered"):call("inspect_view",{"direction":"current","question":"What is there?"})
        self.assertIsNone(call("recall",{"subject_id":None}).arguments["subject_id"])

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
