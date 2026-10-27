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
    def emit(self, event, **data):
        json.dumps(data, ensure_ascii=False, allow_nan=False)   # What the real trace accepts.
        self.rows.append({"event": event, **data})


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

    def test_low_trust_is_kept_but_not_answered_without_identity_or_writes(self):
        # Live September 28: Rex answered TV fragments heard as "Emma." and "Oh.".
        self.setup_core()
        result = self.session.process_turn(self.capture, Transcript("That's Jeff Benziger.", -.8, False), 0)
        self.assertEqual(result.status, "suppressed")
        self.assertEqual(self.brain.calls, 0)
        self.assertNotIn({'role':'user','content':"That's Jeff Benziger."}, self.session.history)
        self.assertTrue(any(r.get('original_text') == "That's Jeff Benziger." for r in self.trace.rows))
        self.assertTrue(any(r["event"] == "untrusted_not_answered" for r in self.trace.rows))
        self.assertFalse(any(r["event"] == "write" for r in self.trace.rows))

    def test_low_trust_after_yielded_question_still_gets_the_owed_answer(self):
        outer = self
        class ResumingBrain(Brain):
            def reply(self, snapshot, cancel):
                self.calls += 1
                if self.calls == 1: outer.session.note_speech(100.)
                yield 'Max is the dog on the left.'
        self.setup_core(brain=ResumingBrain())
        self.assertEqual(self.turn('Which dog is Max?').status, 'interrupted')
        self.capture = replace(self.capture, capture_id='noise', started_at=100., speech_ended_at=100.2, ended_at=101.4)
        result = self.session.process_turn(self.capture, Transcript('Oh.', -.6, False), 0)
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.completed_text, ['Max is the dog on the left.'])
        self.capture = replace(self.capture, capture_id='later', started_at=110., speech_ended_at=110.2, ended_at=111.4)
        self.assertEqual(self.session.process_turn(self.capture, Transcript('Emma.', -.7, False), 0).status, 'suppressed')
        self.assertEqual(self.brain.calls, 2)  # Owed once, not after it was answered.

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
        for i in range(5): runtime.enqueue(runtime.captures, (replace(self.capture,capture_id=str(i)),0), "captures")
        self.assertEqual(runtime.captures.qsize(), 2)
        self.assertEqual(len([r for r in self.trace.rows if r["event"] == "input_overflow"]), 3)

    def test_echo_boundary_capture_never_queues_as_human_work(self):
        self.setup_core()
        runtime=VoiceRuntime(None,None,None,self.session,self.trace)
        endpoint=Endpoint('session')
        for i in range(8):endpoint.push(Frame(bytes(1024),i*.032,(i+1)*.032,True),True)
        echo=endpoint.push(Frame(bytes(1024),.256,.288,False),False)
        self.assertTrue(echo.echo_risk)
        runtime.enqueue(runtime.captures,(echo,0),'captures')
        self.assertTrue(runtime.captures.empty())
        runtime.enqueue(runtime.captures,(self.capture,0),'captures')
        self.assertEqual(runtime.captures.qsize(),1)

    def test_full_transcript_queue_waits_and_preserves_order(self):
        self.setup_core()
        runtime = VoiceRuntime(None,None,None,self.session,self.trace)
        for i in range(4):runtime.enqueue(runtime.turns,i,'turns')
        # October 16: the fourth waits beside the queue instead of blocking speech recognition
        # (a blocked worker dropped every "stop" during a drive), and moves in as room appears.
        self.assertEqual(list(runtime.waiting_turns),[3])
        self.assertEqual(runtime.turns.get(timeout=1),0)
        runtime.flush_turns()
        self.assertEqual([runtime.turns.get_nowait() for _ in range(3)],[1,2,3])
        self.assertFalse(runtime.waiting_turns)
        self.assertFalse(any(r['event']=='input_overflow' for r in self.trace.rows))

    def test_new_speech_during_outage_retains_old_words_and_answers_latest(self):
        from r3x.adapters.hosted import OpenAIBrain
        from tests.test_adapters import Client,Response
        response=Response();response.status_code=503
        brain=OpenAIBrain('synthetic');brain.client=Client(response)
        self.setup_core(brain=brain)
        def resumed(delay,cancel):
            self.session.note_speech(101)
            cancel.check()
        brain._wait_retry=resumed
        first=self.turn('We visited the museum earlier.')
        self.assertEqual(first.status,'interrupted')
        self.assertFalse(first.completed_text)
        brain.client.response=Response([{'type':'response.output_text.delta','delta':'You visited both places today.'},
                                        {'type':'response.completed'}])
        self.capture=replace(self.capture,capture_id='next',started_at=100,speech_ended_at=101,ended_at=102)
        self.assertEqual(self.turn('Now we are at the cemetery.').status,'completed')
        sent=brain.client.calls[-1][1]['json']['input']
        self.assertEqual([m['content'] for m in sent if m['role']=='user'],
                         ['We visited the museum earlier. Now we are at the cemetery.'])   # One utterance (October 23).

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

class PhraseTests(unittest.TestCase):
    def test_a_decimal_arriving_in_two_pieces_stays_one_number(self):
        # October 3: "I faced you and rolled 1." / "42 meters" was spoken as "forty-two meters".
        from r3x.output import Phrases
        splitter = Phrases()
        chunks = []
        for delta in ('I faced you and rolled 1.', '42 meters, then stopped.', ' Here I am.'):
            chunks += splitter.add(delta)
        chunks.append(splitter.finish())
        self.assertEqual(chunks, ['I faced you and rolled 1.42 meters, then stopped.', 'Here I am.'])

    def test_a_finished_sentence_is_still_spoken_as_soon_as_the_next_one_starts(self):
        from r3x.output import Phrases
        splitter = Phrases()
        self.assertEqual(splitter.add('This first sentence is complete.'), [])
        self.assertEqual(splitter.add(' And'), ['This first sentence is complete.'])
        self.assertEqual(splitter.finish(), 'And')


class ListeningTests(unittest.TestCase):
    setup_core = CoreTests.setup_core
    turn = CoreTests.turn
    def test_thinking_pause_stays_in_one_capture(self):
        endpoint = Endpoint('session')
        completed = []
        for i in range(100):
            speech = i < 10 or 30 <= i < 40  # 640 ms pause, then continuation.
            capture = endpoint.push(Frame(bytes(1024), i*.032, (i+1)*.032), speech)
            if capture: completed.append(capture)
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(completed[0].voiced_intervals), 2)
        self.assertGreaterEqual(completed[0].ended_at-completed[0].speech_ended_at, endpoint.silence)

    def test_pause_longer_than_endpoint_silence_ends_the_turn(self):
        endpoint = Endpoint('session')
        completed = []
        for i in range(100):
            speech = i < 10 or 40 <= i < 50  # 960 ms pause.
            capture = endpoint.push(Frame(bytes(1024), i*.032, (i+1)*.032), speech)
            if capture: completed.append(capture)
        self.assertEqual(len(completed), 2)

    def test_resuming_during_model_yields_without_resetting_conversation(self):
        outer = self
        class ResumingBrain(Brain):
            def reply(self, snapshot, cancel):
                self.calls += 1
                outer.session.note_speech(100.)
                yield 'This premature answer must never be heard.'
        self.setup_core(brain=ResumingBrain())
        result = self.turn('We are going to')
        self.assertEqual(result.status, 'interrupted')
        self.assertIsNone(result.first_audio_at)
        self.assertEqual(self.session.revision, 0)
        self.assertIn({'role':'user','content':'We are going to'}, self.session.history)
        self.session.brain = Brain('That sounds like a lovely destination.')
        self.capture = replace(self.capture, started_at=100., speech_ended_at=102., ended_at=103.2)
        self.assertEqual(self.turn('the beach.').status, 'completed')
        # October 23: the unanswered piece and the next one are answered as one utterance.
        messages = self.session.brain.snapshots[0].messages
        self.assertEqual([m['content'] for m in messages if m.get('role') == 'user'], ['We are going to the beach.'])
        self.assertNotIn('Rex yielded without speaking', str(messages))

    def test_brief_noise_during_model_does_not_silence_the_reply(self):
        # Live September 28: one noise frame heard as "Oh." dropped the answer to a full sentence.
        outer = self
        class NoisyBrain(Brain):
            def reply(self, snapshot, cancel):
                self.calls += 1
                outer.session.note_speech(100., sustained=False)
                yield 'Fair point, you were first in line.'
        self.setup_core(brain=NoisyBrain())
        result = self.turn('Well, I turned you on, so I would be the one in front of you.')
        self.assertEqual(result.status, 'completed')
        self.assertEqual(result.completed_text, ['Fair point, you were first in line.'])
        self.assertFalse(any(r['event'] == 'reply_yielded' for r in self.trace.rows))
        self.assertEqual(self.session._latest_speech, 100.)  # Still marks activity for lulls.

    def test_runtime_yields_only_after_sustained_voiced_speech(self):
        from unittest.mock import patch
        self.setup_core()
        calls = []
        original = self.session.note_speech
        self.session.note_speech = lambda at, sustained=True: (calls.append((round(at, 3), sustained)), original(at, sustained))
        # Two noise frames, a 1.5 s gap that ends that utterance, then 20 frames (0.64 s) of speech.
        labels = [True]*2 + [False]*47 + [True]*20 + [False]*40
        frames = [Frame(bytes(1024), 200+i*.032, 200+(i+1)*.032) for i in range(len(labels))]
        class Mic:
            dropped = status_errors = 0
            def read(inner):
                if not frames: runtime.stop.set(); return None
                return frames.pop(0)
            def cancel(inner): pass
        class VAD:
            def reset(inner): pass
            def is_speech(inner, pcm): return labels.pop(0)
        class ASR:
            def transcribe(inner, *args): return Transcript('Oh.', -.6, False)
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), self.session, self.trace)
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        self.assertEqual([s for _, s in calls[:2]], [False, False])
        speech = [s for _, s in calls[2:]]
        self.assertEqual(len(speech), 20)
        self.assertFalse(any(speech[:15]))  # Under 0.5 s voiced.
        self.assertTrue(all(speech[15:]))

    def test_resuming_during_tts_prevents_first_pcm(self):
        outer = self
        class ResumingTTS:
            def synthesize(self, text, cancel):
                outer.session.note_speech(100.)
                yield bytes(1024)
        self.setup_core(tts=ResumingTTS())
        result = self.turn('We are going to')
        self.assertEqual(result.status, 'interrupted')
        self.assertIsNone(result.first_audio_at)
        self.assertFalse(self.session.output.active.is_set())

    def test_delayed_old_transcript_is_preserved_without_a_stale_reply(self):
        self.setup_core()
        self.session.note_speech(100.)
        result = self.turn('We are going to')
        self.assertEqual(result.status, 'interrupted')
        self.assertEqual(self.brain.calls, 0)
        self.assertIn({'role':'user','content':'We are going to'}, self.session.history)


class VoiceReflexRuntimeTests(unittest.TestCase):
    def test_reflex_fires_once_per_utterance_from_its_first_clear_bearing(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        trace = Trace(); faced = []
        session = Session("session", Brain(), OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace)
        self.addCleanup(session.close)
        labels = [True]*30 + [False]*50 + [True]*30 + [False]*50
        frames = [Frame(bytes(1024), 200+i*.032, 200+(i+1)*.032) for i in range(len(labels))]
        class Mic:
            dropped = status_errors = 0
            def read(inner):
                if not frames: runtime.stop.set(); return None
                return frames.pop(0)
            def cancel(inner): pass
        class VAD:
            def reset(inner): pass
            def is_speech(inner, pcm): return labels.pop(0)
        class ASR:
            def transcribe(inner, *args): return Transcript('Hi Rex.', -.01, True)
        class Direction:
            def during(inner, start, end):
                return {'status': 'observed' if end-start > .5 else 'unavailable', 'bearing_deg': 25.}
        class Robot:
            reflex = SimpleNamespace(face=lambda bearing: faced.append(bearing) or 'started', stop=lambda: None)
            idle = SimpleNamespace(stop=lambda: None)
            owner = SimpleNamespace(release_microphone_hold=lambda: None)
            def note_speech(inner): pass
            def heard(inner): return 'started'
            def before_frame(inner, **kw): pass
            def capture_held(inner, capture): return False
        perception = SimpleNamespace(direction=Direction(), camera=None,
                                     analyze_many=lambda capture, cancel: (None,))
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), session, trace, perception=perception, robot=Robot())
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        self.assertEqual(faced, [25., 25.])   # One turn per utterance, none per frame.


class SelfNoiseRuntimeTests(unittest.TestCase):
    """October 3 run 30ec1b04: Rex's own servo noise was heard as "Okay." and "Oh."; each
    froze his idle movement, several cancelled his own beats and two cost a reply call."""
    def run_frames(self, parts, transcripts, voice_check=None, room_sounds=None):
        from unittest.mock import patch
        from types import SimpleNamespace
        import time
        trace = Trace()
        session = Session("session", Brain(), OutputOwner(SyntheticTTS(), SyntheticSink(), trace), trace)
        self.addCleanup(session.close)
        labels, frames = [], []
        for speech, count, motion in parts:
            for _ in range(count):
                i = len(frames)
                labels.append(speech)
                frames.append(Frame(bytes(1024), 200+i*.032, 200+(i+1)*.032, False, motion))
        self.noted, self.yielded, self.idle_allowed, self.heard = [], [], [], []
        outer = self
        answers = list(transcripts)
        class Mic:
            dropped = status_errors = 0
            def read(inner):
                if not frames:
                    deadline = time.monotonic()+3
                    while time.monotonic() < deadline and (not runtime.captures.empty() or runtime.asr_busy.is_set()
                            or not runtime.turns.empty() or session._processing.locked()):
                        time.sleep(.01)
                    runtime.stop.set(); return None
                frame = frames.pop(0)
                trace.at = frame.ended_at
                return frame
            def cancel(inner): pass
        class VAD:
            def reset(inner): pass
            def is_speech(inner, pcm): return labels.pop(0)
        class ASR:
            def transcribe(inner, *args): return answers.pop(0)
        class Robot:
            reflex = SimpleNamespace(stop=lambda: None)
            idle = SimpleNamespace(stop=lambda: None)
            owner = SimpleNamespace(release_microphone_hold=lambda: None)
            def note_speech(inner): outer.noted.append(trace.at)
            def heard(inner): outer.heard.append(trace.at); return 'started'
            def before_frame(inner, **kw): outer.idle_allowed.append((trace.at, kw['allow_idle']))
            def capture_held(inner, capture): return False
        original = session.note_speech
        session.note_speech = lambda at, sustained=True: (self.yielded.append(at), original(at, sustained))
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), session, trace, robot=Robot(), voice_check=voice_check)
        if room_sounds is not None:
            trace.session_id = 'session'
            room_sounds.trace = trace
            room_sounds.start()
            runtime.room_sounds = room_sounds
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        self.session, self.trace_rows = session, trace.rows
        return [r for r in trace.rows if r['event'] == 'capture']

    def test_a_brief_sound_during_his_own_motion_neither_stills_him_nor_becomes_a_turn(self):
        captures = self.run_frames([(True, 3, True), (False, 50, True)], [Transcript('Okay.', -.25, True)])
        self.assertEqual([c['self_noise_risk'] for c in captures], [True])    # Still captured and transcribed.
        self.assertEqual(self.noted, [])                 # Idle movement was never stopped for it...
        self.assertTrue(all(allowed for _, allowed in self.idle_allowed[1:]))
        self.assertEqual(self.yielded, [])               # ...and no beat or reply yielded to it.
        distrusted = [r for r in self.trace_rows if r['event'] == 'transcript_distrusted']
        self.assertEqual(len(distrusted), 1)
        self.assertEqual(self.session.brain.calls, 0)    # No reply call for his own noise.
        self.assertEqual(self.heard, [])                 # Nor a listening cue.
        self.assertTrue(any(r['event'] == 'untrusted_not_answered' for r in self.trace_rows))

    def test_speech_during_his_motion_is_confirmed_within_a_quarter_second_and_answered(self):
        captures = self.run_frames([(True, 30, True), (False, 50, False)], [Transcript('Tell me a joke.', -.05, True)])
        self.assertEqual([c['self_noise_risk'] for c in captures], [False])
        self.assertTrue(self.noted)
        self.assertAlmostEqual(self.noted[0]-200, .256, delta=.04)    # He goes still to listen.
        self.assertFalse(any(allowed for at, allowed in self.idle_allowed if 200.4 < at < 200.9))
        self.assertEqual(self.session.brain.calls, 1)
        self.assertFalse(any(r['event'] == 'transcript_distrusted' for r in self.trace_rows))
        self.assertEqual(len(self.heard), 1)             # His head cocks once, before the reply.

    def test_a_sound_in_stillness_and_a_clearly_heard_word_keep_their_usual_trust(self):
        # Not during his motion: confirmed at once, as before.
        captures = self.run_frames([(True, 2, False), (False, 50, False)], [Transcript('Yes.', -.25, True)])
        self.assertEqual([c['self_noise_risk'] for c in captures], [False])
        self.assertEqual(len(self.noted), 2)
        self.assertEqual(self.session.brain.calls, 1)
        # During his motion but recognized clearly: still a turn.
        self.run_frames([(True, 4, True), (False, 50, True)], [Transcript('Yes.', -.02, True)])
        self.assertEqual(self.session.brain.calls, 1)

    def test_a_short_stop_during_his_motion_still_stops_him(self):
        self.run_frames([(True, 5, True), (False, 50, True)], [Transcript('Stop.', -.3, True)])
        self.assertFalse(any(r['event'] == 'transcript_distrusted' for r in self.trace_rows))
        self.assertTrue(any(r['event'] == 'input_invalidated' for r in self.trace_rows))

    def test_a_suspected_capture_that_stays_open_gets_a_still_moment_to_end(self):
        # Blips every half second keep the capture open; after 3.5 s idle pauses so it can close.
        parts = []
        for _ in range(12):
            parts += [(True, 1, True), (False, 15, True)]
        self.run_frames(parts + [(False, 50, True)], [Transcript('Oh.', -.8, False)])
        early = [allowed for at, allowed in self.idle_allowed if at < 203.]
        late = [allowed for at, allowed in self.idle_allowed if 204.5 < at < 206.]
        self.assertTrue(all(early[1:]))
        self.assertFalse(any(late))
