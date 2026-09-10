from dataclasses import replace
from queue import Queue
import threading
import unittest
from pathlib import Path
import tempfile
import json

from r3x.capture import Endpoint, Frame
from r3x.contracts import Cancellation, Cancelled, Capture, Playback, ProviderFailure, Transcript
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.lease import LiveLease
from r3x.trace import report


class Trace:
    def __init__(self):
        self.rows = []
        self.at = 100.
    def clock(self): return self.at
    def emit(self, event, **data): self.rows.append({"event": event, **data})


class Brain:
    def __init__(self, text="That sounds like a pleasant plan. Enjoy the trip!"):
        self.text, self.calls, self.snapshots = text, 0, []
    def reply(self, snapshot, cancel):
        self.calls += 1
        self.snapshots.append(snapshot)
        yield self.text


class CoreTests(unittest.TestCase):
    def setup_core(self, brain=None, sink=None, tts=None):
        self.trace = Trace()
        self.brain = brain or Brain()
        self.session = Session("session", self.brain,
            OutputOwner(tts or SyntheticTTS(), sink or SyntheticSink(), self.trace), self.trace)
        self.capture = Capture("capture", "session", bytes(1024), 98, 99, 99.6)
        return self.session

    def turn(self, text="I'm going camping tomorrow."):
        return self.session.process_turn(self.capture, Transcript(text, -.01, True), self.session.revision)

    def test_original_utterance_one_generation_and_delivered_history(self):
        self.setup_core()
        result = self.turn()
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.brain.calls, 1)
        self.assertEqual(self.brain.snapshots[0].messages[-1]["content"], "I'm going camping tomorrow.")
        self.assertEqual(self.brain.snapshots[0].speaker, "unresolved")
        self.assertEqual(self.session.history[-1]["content"], " ".join(result.completed_text))
        self.assertEqual(len([r for r in self.trace.rows if r["event"] == "model_request"]), 1)

    def test_stale_session_revision_echo_and_empty_rejected_without_model(self):
        self.setup_core()
        for capture, transcript, revision in (
            (replace(self.capture, session_id="old"), Transcript("hello", 0, True), 0),
            (self.capture, Transcript("hello", 0, True), -1),
            (replace(self.capture, echo_risk=True), Transcript("hello", 0, True), 0),
            (self.capture, Transcript("", None, False), 0)):
            self.assertEqual(self.session.process_turn(capture, transcript, revision).status, "rejected")
        self.assertEqual(self.brain.calls, 0)

    def test_low_trust_can_converse_without_identity_or_writes(self):
        self.setup_core()
        result = self.session.process_turn(self.capture, Transcript("That's Jeff Benziger.", -.8, False), 0)
        self.assertEqual(result.status, "completed")
        self.assertEqual(self.brain.snapshots[0].speaker, "unresolved")
        self.assertFalse(any(r["event"] == "write" for r in self.trace.rows))

    def test_empty_and_failed_tts_cannot_complete_or_enter_history(self):
        class BadTTS:
            def synthesize(self, text, cancel):
                raise ProviderFailure("HTTPFailure")
                yield b""
        self.setup_core(tts=BadTTS())
        result = self.turn()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.completed_text, [])
        self.assertFalse(any(m["role"] == "assistant" for m in self.session.history))

    def test_playback_failure_keeps_unheard_question_out_of_history(self):
        class BadSink(SyntheticSink):
            def play(self, chunks, cancel, on_start):
                raise ProviderFailure("DeviceFailed")
        self.setup_core(brain=Brain("What is your name?"), sink=BadSink())
        result = self.turn()
        self.assertEqual(result.status, "failed")
        self.assertNotIn("What is your name?", str(self.session.history))

    def test_cancel_during_playback_drops_all_queued_tails(self):
        began = threading.Event()
        class BlockingSink(SyntheticSink):
            def play(self, chunks, cancel, on_start):
                began.set()
                cancel.event.wait(1)
                cancel.check()
                raise AssertionError("Cancellation never arrived")
        self.setup_core(brain=Brain("Here is the first sentence. Here is an unheard second sentence."), sink=BlockingSink())
        results = []
        worker = threading.Thread(target=lambda: results.append(self.turn()))
        worker.start()
        self.assertTrue(began.wait(1))
        self.session.cancel()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].status, "interrupted")
        self.assertEqual(results[0].completed_text, [])

    def test_late_model_delta_after_cancel_cannot_speak(self):
        began, release = threading.Event(), threading.Event()
        class LateBrain(Brain):
            def reply(self, snapshot, cancel):
                began.set()
                release.wait(1)
                yield "This must never be spoken."
        self.setup_core(brain=LateBrain())
        results = []
        worker = threading.Thread(target=lambda: results.append(self.turn()))
        worker.start()
        self.assertTrue(began.wait(1))
        self.session.cancel()
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0].completed_text, [])
        self.assertFalse(any(r["event"] == "playback_started" for r in self.trace.rows))

    def test_strict_cancel_control_and_normal_discussion_of_stop(self):
        self.setup_core()
        self.assertEqual(self.turn("Stop!").status, "interrupted")
        self.assertEqual(self.brain.calls, 0)
        self.assertEqual(self.turn("Why did the bus stop?").status, "completed")
        self.assertEqual(self.brain.calls, 1)

    def test_input_overflow_is_explicit(self):
        self.setup_core()
        runtime = VoiceRuntime(None, None, None, self.session, self.trace)
        for i in range(5): runtime.enqueue(runtime.turns, i, "turns")
        self.assertEqual(runtime.turns.qsize(), 3)
        self.assertEqual(len([r for r in self.trace.rows if r["event"] == "input_overflow"]), 2)

    def test_endpoint_preserves_preroll_and_endpoint_timestamps(self):
        endpoint = Endpoint("session", .2)
        frames = []
        result = None
        for i in range(25):
            frame = Frame(bytes([i, 0]) * 512, i*.032, (i+1)*.032)
            frames.append(frame)
            value = endpoint.push(frame, 8 <= i < 16)
            result = value or result
        self.assertIsNotNone(result)
        self.assertEqual(result.started_at, 0)
        self.assertAlmostEqual(result.speech_ended_at, .512)
        self.assertGreaterEqual(result.ended_at - result.speech_ended_at, .2)
        self.assertTrue(result.pcm.startswith(frames[0].pcm))

    def test_endpoint_reset_discards_discontinuous_partial_audio(self):
        endpoint = Endpoint("session", .2)
        for i in range(8): endpoint.push(Frame(bytes(1024), i*.032, (i+1)*.032), True)
        endpoint.reset()
        for i in range(12):
            self.assertIsNone(endpoint.push(Frame(bytes(1024), 1+i*.032, 1+(i+1)*.032), False))

    def test_endpoint_echo_and_duration_bound(self):
        endpoint = Endpoint("session")
        result = None
        for i in range(630):
            result = endpoint.push(Frame(bytes(1024), i*.032, (i+1)*.032, True), True)
            if result: break
        self.assertTrue(result.truncated)
        self.assertTrue(result.echo_risk)
        self.assertLessEqual(len(result.pcm), 640*1024)

    def test_second_live_owner_rejected_until_first_releases(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = LiveLease(Path(directory).resolve()/"lease"), LiveLease(Path(directory).resolve()/"lease")
            first.acquire()
            try:
                with self.assertRaises(ValueError): second.acquire()
            finally:
                first.close()
            second.acquire()
            second.close()

    def test_model_failure_after_partial_text_is_not_a_completed_turn(self):
        class FailingBrain(Brain):
            def reply(self, snapshot, cancel):
                yield "This is a partial response."
                raise ProviderFailure("ModelStreamTruncated")
        self.setup_core(brain=FailingBrain())
        result = self.turn()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "ModelStreamTruncated")

    def test_production_queue_processes_captured_turns_in_order(self):
        self.setup_core()
        completed = threading.Event()
        base_process = self.session.process_turn
        seen = []
        def process(capture, transcript, revision, **evidence):
            seen.append(transcript.text)
            result = base_process(capture, transcript, revision, **evidence)
            if len(seen) == 3: completed.set()
            return result
        self.session.process_turn = process
        runtime = VoiceRuntime(None, None, None, self.session, self.trace)
        for text in ("first", "second", "third"):
            runtime.enqueue(runtime.turns, (self.capture, Transcript(text, 0, True), 0, None), "turns")
        worker = threading.Thread(target=runtime._turn_loop)
        worker.start()
        self.assertTrue(completed.wait(2))
        runtime.stop.set()
        worker.join(1)
        self.assertEqual(seen, ["first", "second", "third"])

    def test_spoken_stop_bypasses_busy_conversation_worker(self):
        self.setup_core()
        processed = threading.Event()
        class StopASR:
            def transcribe(self, capture, cancel):
                processed.set()
                return Transcript("Stop!", -.01, True)
        runtime = VoiceRuntime(None, None, StopASR(), self.session, self.trace)
        runtime.enqueue(runtime.captures, (self.capture, 0), "captures")
        stopped = threading.Event()
        old_cancel = runtime.cancel
        def cancel():
            old_cancel()
            stopped.set()
        runtime.cancel = cancel
        worker = threading.Thread(target=runtime._asr_loop)
        worker.start()
        self.assertTrue(stopped.wait(1))
        runtime.stop.set()
        worker.join(1)
        self.assertEqual(self.brain.calls, 0)
        self.assertEqual(self.session.revision, 1)

    def test_report_keeps_failures_and_missing_timings(self):
        rows = [
            {"event":"model_request"},
            {"event":"transcript", "asr_seconds":1., "queue_seconds":.2},
            {"event":"turn_done", "status":"failed", "timings":{"total":4., "first_audio":None}},
            {"event":"turn_done", "status":"completed", "timings":{"total":2., "first_audio":1.}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"evidence.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in rows)+"\n{\"event\":")
            result = report(path)
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["turn_outcomes"]["failed"], 1)
        self.assertEqual(result["observed_seconds"]["total"]["median"], 3.)
        self.assertEqual(result["observed_seconds"]["first_audio"]["n"], 1)
        self.assertIsNone(result["speaker_accuracy"])
        self.assertEqual(result["failures"], [{"event":"trace_parse_failed","line":5}])
