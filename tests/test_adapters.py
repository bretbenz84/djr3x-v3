import json
from types import SimpleNamespace
import threading
import time
import unittest

from r3x.adapters.audio import AudioSink, BufferedOutput, resolve_device
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
    def test_cache_writes_and_reasoning_survive_provider_trace_and_report(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from r3x.trace import Trace, report
        usage = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                 "input_tokens_details": {"cached_tokens": 30, "cache_write_tokens": 40},
                 "output_tokens_details": {"reasoning_tokens": 5}}
        with TemporaryDirectory() as directory:
            path = Path(directory)/"trace.jsonl"
            trace = Trace("fixture", path, display=lambda row: None)
            brain = OpenAIBrain("fixture")
            brain.trace = trace
            brain.client = Client(Response([
                {"type": "response.output_text.delta", "delta": "Hello."},
                {"type": "response.completed", "response": {"usage": usage}}]))
            self.assertEqual(list(brain.reply(Snapshot("s", "t", 0, ()), Cancellation())), ["Hello."])
            trace.close()
            counts = report(path)["reported_model_tokens"]
            self.assertEqual(counts, {"input_tokens": 100, "cached_tokens": 30,
                "cache_write_tokens": 40, "output_tokens": 20, "reasoning_tokens": 5, "total_tokens": 120})

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
        self.assertEqual(tools["record_claim"]["subject_id"]["enum"],["person",None])  # null: a third_party person not known yet (October 17).
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

    def test_a_talked_over_beat_is_weighed_against_his_own_answer(self):
        # October 15 (2f85db5c): "I'm just doing the dishes." "Then I'll let you get on with it."
        # Forty seconds later the plan's second dish bit ran anyway.
        history=({"role":"user","content":"I'm just doing the dishes."},
                 {"role":"assistant","content":"Then I'll let you get on with it."})
        step={"kind":"contribute","instruction":"Offer a vain ruling about the kitchen operation.","scope":"audience",
              "origin":"planner","people_spoke_since_planned":True,"deferred":False,"plan_id":"p","index":2}
        brain=OpenAIBrain("fixture")
        brain.client=Client(Response([{"type":"response.output_text.delta","delta":"Hi."},{"type":"response.completed"}]))
        list(brain.reply(Snapshot("s","t",0,history,context={"source_turn_id":"t","agency_step":step},
                                  tools_enabled=True),Cancellation()))
        note=brain.client.calls[0][1]["json"]["input"][-1]["content"]
        self.assertIn('Their latest words: "I\'m just doing the dishes."',note)
        self.assertIn('Your answer to them: "Then I\'ll let you get on with it."',note)
        self.assertIn("keep your word",note)

    def test_instructions_stay_static_and_request_context_follows_history(self):
        from r3x.adapters.hosted import PERSONA
        history=({"role":"user","content":"earlier words"},{"role":"assistant","content":"earlier reply"})
        payloads=[]
        for context in ({"source_turn_id":"turn-id-alpha","photo_memory":{"entities":[],"objects":[]}},{"source_turn_id":"turn-id-beta"}):
            brain=OpenAIBrain("fixture")
            brain.client=Client(Response([{"type":"response.output_text.delta","delta":"Hi."},{"type":"response.completed"}]))
            list(brain.reply(Snapshot("s","t",0,history,context=context,tools_enabled=True),Cancellation()))
            payloads.append(brain.client.calls[0][1]["json"])
        self.assertTrue(all(p["instructions"]==PERSONA for p in payloads))
        for payload,turn in zip(payloads,("turn-id-alpha","turn-id-beta")):
            self.assertEqual(payload["input"][:2],list(history))
            self.assertEqual(payload["input"][2]["role"],"developer")
            self.assertIn("CURRENT CORE CONTEXT",payload["input"][2]["content"])
            self.assertIn(turn,payload["input"][2]["content"])
        self.assertIn("PHOTO MEMORY DECISION",payloads[0]["input"][2]["content"])
        self.assertNotIn("PHOTO MEMORY DECISION",payloads[1]["input"][2]["content"])

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
        # Other identifiers reach the state owner, whose rejection the model can explain.
        self.assertEqual(call("record_claim",dict(claim,subject_id="fabricated")).arguments["subject_id"],"fabricated")
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
        self.assertEqual(payload["model"], "gpt-6-luna")
        self.assertEqual(payload["reasoning"], {"effort": "low"})
        self.assertFalse(payload["store"])
        self.assertEqual(payload["input"][0]["content"], "original")
        # October 23: the same static tools on every reply request keep the cached prefix; none offered.
        from r3x.semantics import TOOLS
        self.assertEqual((payload["tools"], payload["tool_choice"]), (list(TOOLS), "none"))
        self.assertEqual(payload["prompt_cache_key"], "r3x-reply")
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
        observed = []
        sink.on_pcm = lambda data,at: observed.append(data)
        expected = b"\x01\x02" * 5000
        parts = iter([expected[:3], expected[3:4096], expected[4096:8192], expected[8192:]])
        starts = []
        receipt = sink.play(parts, Cancellation(), starts.append)
        self.assertEqual(receipt.frames, 5000)
        self.assertEqual(bytes(captured[:len(expected)]), expected)
        self.assertEqual(b"".join(observed)[:len(expected)],expected)
        self.assertEqual(len(starts), 1)
        self.assertIsNone(sink.stream)



class Ring:
    """PortAudio's blocking-mode output ring: drains in real time on its own thread."""
    def __init__(self, capacity=4410, latency=.25, **kwargs):
        self.capacity, self.latency, self.kwargs = capacity, latency, kwargs
        self.queued, self.at, self.written, self.aborted, self.closed, self.started = 0., None, [], False, False, False
    def _drain(self):
        now = time.monotonic()
        if self.at is not None:
            self.queued = max(0., self.queued - (now - self.at) * 22050)
        self.at = now
    def start(self): self.started = True; self._drain()
    @property
    def write_available(self):
        self._drain()
        return int(self.capacity - self.queued)
    def write(self, data):
        self._drain()
        assert len(data) // 2 <= self.capacity - self.queued + 1, 'a write that would block'
        underflow = bool(self.written) and self.queued == 0
        self.queued += len(data) // 2
        self.written.append(bytes(data))
        return underflow
    def abort(self): self.aborted = True; self.queued = 0
    def close(self): self.closed = True


class BufferedOutputTests(unittest.TestCase):
    """October 22: startup sound written ahead into the driver's ring, so model loading cannot starve it."""
    def sink(self, ring):
        sink = AudioSink()
        sink.sd = SimpleNamespace(RawOutputStream=lambda **kwargs: ring.__dict__.update(kwargs=kwargs) or ring)
        return sink

    def test_sound_is_written_ahead_in_small_pieces_and_returns_once_heard(self):
        ring = Ring()
        sink = self.sink(ring)
        levels = []
        sink.on_pcm = lambda data, at: levels.append(at)
        output = sink.buffered()
        self.assertEqual((ring.kwargs['latency'], ring.started), (.5, True))
        clip = bytes(range(256)) * 86 + b'\x01'                # About 0.5 s, odd network boundaries.
        starts, began = [], time.monotonic()
        receipt = output.play(iter([clip[:3], clip[3:-1], clip[-1:] + b'\x00']), Cancellation(), starts.append)
        self.assertEqual(b''.join(ring.written), clip + b'\x00')
        self.assertLessEqual(max(map(len, ring.written)), 2048)
        self.assertEqual(len(starts), 1)
        self.assertGreaterEqual(time.monotonic() - began, .45)   # Returns after the ring played it out.
        self.assertEqual(levels, sorted(levels))                 # Mouth levels arrive timed to playback.
        self.assertEqual((receipt.frames, receipt.underruns), (len(clip + b'\x00') // 2, 0))
        output.close()
        self.assertTrue(ring.closed)

    def test_a_hard_stop_discards_the_queued_tail_and_a_stalled_device_fails(self):
        ring = Ring()
        output = self.sink(ring).buffered()
        cancel = Cancellation()
        def pieces():
            yield b'\x00' * 8000
            cancel.event.set()
            yield b'\x00' * 8000
        with self.assertRaises(Cancelled):
            output.play(pieces(), cancel, lambda at: None)
        self.assertTrue(ring.aborted)
        stuck = Ring()
        stuck._drain = lambda: None                              # The device stopped consuming.
        stuck.queued = stuck.capacity
        clock = iter(range(0, 100, 2))
        output = BufferedOutput(stuck, lambda: None, clock=lambda: float(next(clock)))
        with self.assertRaisesRegex(ProviderFailure, 'PlaybackStalled'):
            output.play(iter([b'\x00' * 4096]), Cancellation(), lambda at: None)


class LookImageTests(unittest.TestCase):
    def test_sharp_jpeg_looks_are_sent_at_high_detail_and_other_formats_rejected(self):
        from r3x.scene import SceneImage
        events=[{"type":"response.output_item.added","output_index":0,"item":{"type":"message","phase":"final_answer"}},
                {"type":"response.output_text.delta","output_index":0,"delta":"I see a door."},{"type":"response.completed"}]
        brain=OpenAIBrain("fixture")
        brain.client=Client(Response(events))
        look=SceneImage('f',1.,'data:image/jpeg;base64,abcd','high')
        self.assertEqual(list(brain.reply(Snapshot("s","t",0,(),images=(look,)),Cancellation())),["I see a door."])
        sent=brain.client.calls[-1][1]['json']['input'][-1]['content']
        self.assertEqual([c['detail'] for c in sent if c['type']=='input_image'],['high'])
        brain.client=Client(Response(events))
        with self.assertRaisesRegex(ProviderFailure,'InvalidSceneImages'):
            list(brain.reply(Snapshot("s","t",0,(),images=(SceneImage('f',1.,'data:image/gif;base64,abcd'),)),Cancellation()))
