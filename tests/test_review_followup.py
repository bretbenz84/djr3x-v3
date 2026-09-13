"""External-review regressions: synthetic state, playback, and filesystem inputs."""
from collections import deque
from pathlib import Path
import os
import tempfile
import unittest

from r3x import visual_memory
from r3x.faces import FaceTracker
from r3x.settings import ScratchPaths
from r3x.speakers import SpeakerTracker, Track
from r3x.scenario import SyntheticSink
from tests.test_memory_core import MemoryFixture, observation, NS, B
from tests.test_face_identity import sample
from tests import test_visual_memory
from tools.check_io_policy import check_mutation
from tools.check_labeled_session import baseline_predictions


class ReviewStateTests(MemoryFixture):
    def test_unconfirmed_face_hypothesis_cannot_delete_gallery(self):
        from tests.test_face_identity import FaceStateTests, face
        FaceStateTests.visible_turn(self, 'I am Jordan.', [face(110)])
        result = FaceStateTests.intro_face(self)
        person = result.data['person_id']
        before = self.store.rows('SELECT * FROM face_references')
        self.assertTrue(before)
        self.state.face_associations['face-a'] = person
        result = self.call('reset_face_identity', {'person_id':person,'face_track_id':'face-a'})
        self.assertEqual(result.reason, 'FaceCorrectionNeedsCurrentAssociation')
        self.assertEqual(self.store.rows('SELECT * FROM face_references'), before)

    def test_retained_candidate_does_not_become_recent_when_someone_else_speaks(self):
        self.introduce()
        old = next(iter(self.state.tracker.tracks.values()))
        heard_at = old.last_at
        self.begin('Someone else is speaking.', B)
        self.assertEqual(old.last_at, heard_at)
        self.assertTrue(old.retained)

    def test_restored_candidate_has_no_fabricated_activity_in_new_run(self):
        from r3x.state import CoreState
        self.introduce()
        old_id = next(iter(self.state.tracker.tracks))
        self.state = CoreState(self.store, SpeakerTracker(NS), self.state.trace)
        self.begin('A different speaker after restart.', B)
        self.assertEqual(self.state.tracker.tracks[old_id].last_at, float('-inf'))

    def test_retained_inactive_candidates_do_not_fill_live_capacity(self):
        tracker = SpeakerTracker(NS)
        for i in range(12):
            vector = tuple(float(j == i) for j in range(13))
            tracker.tracks[str(i)] = Track(str(i), float('-inf'), deque([vector], maxlen=5), retained=True)
        vector = tuple(float(j == 12) for j in range(13))
        result = tracker.observe(observation(vector), 1000, {})
        self.assertIsNotNone(result.track_id)
        self.assertEqual(len(tracker.tracks), 13)

    def test_forget_deletes_shared_path_without_reassigning_unique_rows(self):
        person = self.enroll()
        capture = self.store.rows('SELECT capture_id FROM turns LIMIT 1')[0]['capture_id']
        path = Path(self.temp.name).resolve() / 'shared.wav'
        path.write_bytes(b'synthetic shared parent audio')
        with self.store.transaction() as conn:
            self.store.register_artifact(conn, path, person, capture_id=capture)
            self.store.register_artifact(conn, path, None, capture_id=capture)
        with self.store.transaction() as conn:
            self.store.forget(conn, person)
        self.assertFalse(path.exists())
        self.assertFalse(self.store.rows('SELECT * FROM artifacts'))
        self.assertIsNone(self.store.person(person))

    def test_retracted_introductions_do_not_exhaust_active_capacity(self):
        turn = self.begin('A synthetic introduction.')
        with self.store.transaction() as conn:
            for i in range(100):
                self.store.add_person(conn, str(i), [], turn, status='retracted')
            person = self.store.add_person(conn, 'Active', [], turn)
        self.assertEqual(self.store.person(person)['name'], 'Active')


class ReviewVisualDeliveryTests(MemoryFixture):
    setup_session = test_visual_memory.VisualMemoryTests.setup_session
    setup_description = test_visual_memory.VisualMemoryTests.setup_description
    turn = test_visual_memory.VisualMemoryTests.turn
    def test_inspection_guard_waits_for_committed_revision_publication(self):
        import threading
        self.setup_description()
        original = self.state.save_visual
        probes = []
        checked = threading.Event()
        began = threading.Event()
        results = []
        def save(*args):
            revision = original(*args)
            cancel = args[-1]
            def probe():
                began.set()
                results.append(cancel.guard())
                checked.set()
            worker = threading.Thread(target=probe)
            probes.append(worker);worker.start()
            self.assertTrue(began.wait(1))
            self.assertFalse(checked.wait(.02))
            return revision
        self.state.save_visual = save
        self.assertEqual(self.turn().status, 'completed')
        for worker in probes: worker.join(1)
        self.assertEqual(results, [True])

    def test_proactive_inspection_after_forget_closes_each_action(self):
        from tests.test_proactive import event
        self.setup_session()
        self.state.forgotten = True
        for _ in range(2):
            result = self.session.process_opportunity(event(), self.session.revision, lambda: True)
            self.assertEqual(result.status, 'completed')
            self.assertIsNone(self.state.actions.active)
        self.assertEqual(len(self.state.actions.recent), 2)

    def test_completed_visual_speech_retains_receipt_after_late_cancel(self):
        self.setup_description()
        session = self.session
        class LateCancelSink(SyntheticSink):
            def play(self, *args, **kwargs):
                receipt = super().play(*args, **kwargs)
                session.note_speech(200)
                return receipt
        self.output.sink = LateCancelSink()
        result = self.turn()
        self.assertTrue(result.completed_text)
        rows = visual_memory.context(self.store, 'session', '[]')
        self.assertEqual(rows[0]['spoken'][0]['text'], 'A red cup sits on the desk.')


class ReviewBoundaryTests(unittest.TestCase):
    def test_old_face_after_detector_gap_is_not_a_new_presentation(self):
        tracker = FaceTracker()
        box = (.1, .1, .3, .3)
        tracker.update([box], 1., [sample(1.)], {})
        tracker.update([], 1.5, None, {})
        tracker.update([box], 2., [sample(2.)], {})
        rows = tracker.during(1.4, 2.1)
        self.assertEqual(len(rows), 1)
        self.assertFalse(rows[0]['newly_presented'])

    def test_capture_audience_excludes_faces_absent_from_latest_frame(self):
        tracker = FaceTracker()
        tracker.update([(.1,.1,.3,.3),(.6,.1,.2,.2)], 1., None, {})
        latest = tracker.update([(.1,.1,.3,.3)], 1.5, None, {})
        self.assertEqual({f['track_id'] for f in tracker.during(1.,1.5)},
                         {f['track_id'] for f in latest})

    def test_known_person_track_churn_preserves_memory_audience(self):
        self.assertEqual(visual_memory.audience({'faces':[{'track_id':'old','identity':'person'}]}),
                         visual_memory.audience({'faces':[{'track_id':'new','identity':'person'}]}))
        self.assertNotEqual(visual_memory.audience({'faces':[{'track_id':'old'}]}),
                            visual_memory.audience({'faces':[{'track_id':'new'}]}))

    def test_split_baseline_joins_parent_and_skips_unlabeled_turns(self):
        events = [{'event':'turn','turn_id':'t','capture_id':'capture-part1'},
                  {'event':'speaker_evidence','turn_id':'t'},
                  {'event':'speaker_evidence','turn_id':'missing'}]
        self.assertEqual(baseline_predictions(events,[{'capture_id':'capture','clip':7}])[0]['clip'],7)
        events[0]['parent_capture_id']='capture'
        events[0]['capture_id']='opaque-part'
        self.assertEqual(len(baseline_predictions(events,[{'capture_id':'capture','clip':7}])),1)

    def test_validation_symlink_is_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            project = Path(root).resolve()
            (project/'local').mkdir()
            (project/'local/validation').symlink_to(Path(other).resolve(), target_is_directory=True)
            with self.assertRaises(ValueError):
                ScratchPaths.for_project(project).prepare()

    def test_mutation_and_spawn_guards_cover_non_open_operations(self):
        for event, args in [('os.remove',('/Users/bbenziger/private',-1)),
                            ('os.rename',('/tmp/synthetic','/Users/bbenziger/private',-1,-1)),
                            ('os.posix_spawn',('unused',[],{}))]:
            with self.subTest(event=event), self.assertRaises(RuntimeError):
                check_mutation(event,args)
        with tempfile.TemporaryDirectory() as folder:
            fd = os.open(folder, os.O_RDONLY)
            try:
                check_mutation('os.remove',('synthetic',fd))
                with self.assertRaises(RuntimeError):
                    check_mutation('os.remove',('../../../../../../Users/bbenziger/private',fd))
            finally:
                os.close(fd)


class ReviewTransportTests(unittest.TestCase):
    def test_sink_returns_drain_receipt_when_cancellation_arrives_after_finished(self):
        from types import SimpleNamespace
        from r3x.adapters.audio import AudioSink
        from r3x.contracts import Cancellation
        class Stop(Exception): pass
        class Abort(Exception): pass
        cancel = Cancellation()
        class Stream:
            def __init__(self, **kwargs): self.kwargs = kwargs
            def start(self):
                try:
                    self.kwargs['callback'](bytearray(2048),1024,
                        SimpleNamespace(outputBufferDacTime=0,currentTime=0),
                        SimpleNamespace(output_underflow=False))
                except Stop:
                    self.kwargs['finished_callback']()
                    cancel.event.set()
            def abort(self): pass
            def close(self): pass
        sink = AudioSink()
        sink.sd = SimpleNamespace(RawOutputStream=Stream,CallbackStop=Stop,CallbackAbort=Abort)
        receipt = sink.play(iter([b'00'*10]), cancel, lambda at: None)
        self.assertEqual(receipt.frames, 10)
        self.assertTrue(cancel.event.is_set())

    def test_playback_backpressure_does_not_consume_tts_transport_deadline(self):
        from unittest.mock import patch
        from r3x.adapters.hosted import ElevenLabsTTS
        from r3x.contracts import Cancellation
        from tests.test_adapters import Client, Response
        tts = ElevenLabsTTS('fake', 'fake')
        tts.client = Client(Response(chunks=[b'00', b'00']))
        now = [0.]
        with patch('r3x.adapters.hosted.time.monotonic', side_effect=lambda: now[0]):
            stream = tts.synthesize('Synthetic phrase', Cancellation())
            self.assertEqual(next(stream), b'00')
            now[0] = 35.  # Consumer is occupied with playback, not waiting on transport.
            self.assertEqual(next(stream), b'00')
            with self.assertRaises(StopIteration): next(stream)

    def test_model_backpressure_is_separate_but_stalled_transport_still_fails(self):
        from unittest.mock import patch
        from r3x.adapters.hosted import OpenAIBrain, ElevenLabsTTS
        from r3x.contracts import Cancellation, Snapshot, ProviderFailure
        from tests.test_adapters import Client, Response
        now = [0.]
        brain = OpenAIBrain('fake')
        brain.client = Client(Response(events=[{'type':'response.output_text.delta','delta':'Hello.'},
                                               {'type':'response.completed'}]))
        with patch('r3x.adapters.hosted.time.monotonic', side_effect=lambda: now[0]):
            stream = brain.reply(Snapshot('s','t',0,()), Cancellation())
            self.assertEqual(next(stream), 'Hello.')
            now[0] = 90.
            with self.assertRaises(StopIteration): next(stream)
        class Stalled(Response):
            def iter_bytes(self, chunk_size):
                now[0] += 31.
                yield b'00'
        tts = ElevenLabsTTS('fake','fake');tts.client = Client(Stalled())
        with patch('r3x.adapters.hosted.time.monotonic', side_effect=lambda: now[0]):
            with self.assertRaisesRegex(ProviderFailure, 'TTSDeadline'):
                list(tts.synthesize('Synthetic', Cancellation()))

    def test_runtime_shutdown_reports_output_worker_even_without_runtime_workers(self):
        from types import SimpleNamespace
        from threading import Event
        from r3x.runtime import VoiceRuntime
        from r3x.contracts import ProviderFailure
        from tests.test_voice_core import Trace
        runtime = VoiceRuntime.__new__(VoiceRuntime)
        runtime.stop = Event();runtime.cancel = lambda: None
        runtime.mic = SimpleNamespace(cancel=lambda: None)
        runtime.recorder = None;runtime.workers = [];runtime.trace = Trace()
        runtime.session = SimpleNamespace(output_worker=SimpleNamespace(name='output',is_alive=lambda:True),closed=False)
        with self.assertRaisesRegex(ProviderFailure,'RuntimeWorkersDidNotStop'): runtime.close()
        self.assertFalse(runtime.session.closed)
        self.assertIn('output', runtime.trace.rows[-1]['workers'])
