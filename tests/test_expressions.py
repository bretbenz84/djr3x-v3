"""What faces show, for the conversation (owner, November 3: V2's smile awareness); synthetic only."""
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock
from uuid import uuid4

from r3x.adapters.camera import Camera
from r3x.adapters.expression import ExpressionReader, MAX_FACES
from r3x.contracts import Capture, ProviderFailure, Transcript
from r3x.expressions import LOST_SECONDS, ExpressionWatch, looks
from r3x.faces import FaceSample, FaceTracker
from r3x import jokes
from r3x.field_guide import GUIDE, slim
from r3x.output import OutputOwner
from r3x.present import LOOK_KEEP_SECONDS, OWN_A_SMILE_SECONDS, Present
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from r3x.vision import Frame
from tests import test_memory_core as memory_tests
from tests import test_speaker_presence as presence_tests
from tests import test_turn_taking as timing_tests
from tests.test_agency_plan import Executor, Planner
from tests.test_context_diet import send

BOX = (.4, .3, .1, .2)


def smile(value):
    return {'mouthSmileLeft': value, 'mouthSmileRight': value}


def reads(watch, values, start=100., box=BOX, identity='bret'):
    """One reading every half second, the face pass's pace."""
    return [watch.observe(box, identity, smile(v) if v is not None else None, start + i * .5)
            for i, v in enumerate(values)]


def smiling(at, since, moment='m1', person='bret', unprompted=True, look='smile', width=120):
    return {'track_id': 't', 'box': list(BOX), 'at': at, 'identity': person, 'face_width_px': width,
            'expression': look, 'expression_since': since, 'expression_moment': moment,
            'expression_unprompted': unprompted}


class WhenAReadingIsASmile(unittest.TestCase):
    def test_a_face_that_reads_high_at_rest_is_not_smiling(self):
        # V2, 2f9e969: the camera looking up at a seated talker read a resting mouth about .5.
        rows = reads(ExpressionWatch(), [.48] * 30)
        self.assertFalse(any('expression' in row for row in rows))

    def test_a_real_smile_is_a_rise_above_that_same_resting_face(self):
        rows = reads(ExpressionWatch(), [.48] * 20 + [.85, .85, .85])
        self.assertNotIn('expression', rows[20])                 # One reading is not yet a smile.
        self.assertEqual(rows[21]['expression'], 'smile')
        self.assertEqual(rows[21]['expression_since'], 110.)     # It began at the first reading.
        self.assertEqual(rows[22]['expression_moment'], rows[21]['expression_moment'])

    def test_a_modest_smile_counts_on_a_flat_face_but_is_not_strong_enough_unprompted(self):
        rows = reads(ExpressionWatch(), [.02] * 10 + [.6, .6])
        self.assertEqual(rows[-1]['expression'], 'smile')
        self.assertFalse(rows[-1]['expression_unprompted'])
        rows = reads(ExpressionWatch(), [.02] * 10 + [.6, .8])
        self.assertTrue(rows[-1]['expression_unprompted'])

    def test_a_single_spike_is_not_a_smile(self):
        rows = reads(ExpressionWatch(), [.02] * 10 + [.9] + [.02] * 4)
        self.assertFalse(any('expression' in row for row in rows))

    def test_until_the_resting_face_is_known_only_a_broad_smile_counts(self):
        self.assertFalse(any('expression' in row for row in reads(ExpressionWatch(), [.6, .6, .6])))
        self.assertEqual(reads(ExpressionWatch(), [.85, .85])[-1]['expression'], 'smile')

    def test_a_smile_ends_and_the_next_one_is_a_new_moment(self):
        rows = reads(ExpressionWatch(), [.02] * 10 + [.8] * 3 + [.02] * 3 + [.8] * 2)
        self.assertEqual(rows[12]['expression'], 'smile')
        self.assertNotIn('expression', rows[15])
        self.assertNotEqual(rows[-1]['expression_moment'], rows[12]['expression_moment'])

    def test_a_smile_held_for_a_long_time_stops_being_news(self):
        rows = reads(ExpressionWatch(), [.02] * 10 + [.8] * 120)
        self.assertEqual(rows[12]['expression'], 'smile')
        self.assertNotIn('expression', rows[-1])

    def test_a_moment_the_reader_missed_does_not_end_the_smile_but_a_long_gap_does(self):
        watch = ExpressionWatch()
        rows = reads(watch, [.02] * 10 + [.8, .8, None])
        self.assertEqual(rows[-1]['expression'], 'smile')
        later = watch.observe(BOX, 'bret', None, 100 + 11 * .5 + LOST_SECONDS + .1)
        self.assertNotIn('expression', later)

    def test_a_different_recognized_person_never_inherits_a_look_or_resting_face(self):
        watch = ExpressionWatch()
        reads(watch, [.02] * 10 + [.8, .8])
        row = watch.observe(BOX, 'jeff', smile(.02), 106.5)
        self.assertNotIn('expression', row)

    def test_a_name_keeps_the_resting_face_when_the_box_jumps(self):
        watch = ExpressionWatch()
        reads(watch, [.48] * 20)
        row = reads(watch, [.6, .6], start=110., box=(.05, .05, .1, .2))[-1]
        self.assertNotIn('expression', row)      # .6 is below this face's rest + rise, wherever it sits.

    def test_the_furrowed_brow_is_not_a_look(self):
        # October 22 clip: resting browDown read a median .56; V2 had the same trouble.
        self.assertNotIn('brow_furrow', looks({'browDownLeft': .9, 'browDownRight': .9}))
        rows = [ExpressionWatch().observe(BOX, 'bret', {'browDownLeft': .9, 'browDownRight': .9}, 100.)]
        self.assertNotIn('expression', rows[0])

    def test_the_other_looks_use_the_same_rule(self):
        watch = ExpressionWatch()
        frown = {'mouthFrownLeft': .7, 'mouthFrownRight': .7}
        for i in range(10):
            watch.observe(BOX, 'bret', {}, 100 + i * .5)
        watch.observe(BOX, 'bret', frown, 105.)
        self.assertEqual(watch.observe(BOX, 'bret', frown, 105.5)['expression'], 'frown')


class LooksOnFaceRows(unittest.TestCase):
    def sample(self):
        return FaceSample(None, b'', b'', 'f', 'c', 's', 100., 120., .9, 80., 100., (), 'n', False)

    def test_a_row_says_nothing_for_a_resting_face_and_nothing_at_all_with_the_reader_off(self):
        tracker = FaceTracker(ExpressionWatch())
        row = tracker.update([BOX], 100., [self.sample()], {}, [smile(.02)])[0]
        self.assertNotIn('expression', row)
        self.assertIn('expression_read', row)
        off = FaceTracker(ExpressionWatch()).update([BOX], 100., [self.sample()], {})[0]
        self.assertFalse([k for k in off if k.startswith('expression')])

    def test_turn_time_faces_carry_the_look_and_a_forget_clears_it(self):
        tracker = FaceTracker(ExpressionWatch())
        for i, value in enumerate([.85, .85]):
            rows = tracker.update([BOX], 100. + i * .5, [self.sample()], None, [smile(value)])
        self.assertEqual(rows[0]['expression'], 'smile')
        self.assertEqual(tracker.during(99., 101.)[0]['expression'], 'smile')
        tracker.clear()
        self.assertEqual(tracker.expressions.watches, [])

    def test_the_model_gets_looks_only_once_through_the_picture_not_on_face_rows(self):
        row = {**smiling(100., 99.), 'expression_read': {'smile': .8}}
        self.assertFalse([k for k in slim(row) if k.startswith('expression')])
        payload, _ = send({'source_turn_id': 't', 'faces': [row], 'right_now': {
            'conversation': {'rex_last_line': {'text': 'A joke.', 'faces_at_it': [{'who': 'Bret', 'look': 'smiled'}]}}}})
        shown = ' '.join(str(item.get('content')) for item in payload['input'])
        self.assertNotIn('expression_moment', shown)
        self.assertIn('"faces_at_it": [{"who": "Bret", "look": "smiled"}]', shown)
        self.assertIn(GUIDE['right_now.expressions'], payload['instructions'])

    def test_a_smile_to_own_is_the_last_thing_he_reads_and_only_then(self):
        def last(conversation):
            payload, _ = send({'source_turn_id': 't', 'right_now': {'conversation': conversation}})
            return payload['input'][-1]['content']
        told = last({'own_it': {'who': 'Bret', 'at_your_line': True}})
        self.assertIn('Bret just smiled at your last line, so it landed.', told)
        self.assertIn('If what they just said is serious, answer that', told)
        self.assertIn('never a report of seeing it', told)
        self.assertIn('you do not know why', last({'own_it': {'who': 'Bret', 'at_your_line': False}}))
        self.assertNotIn('Let that show', last({}))


class LooksInThePicture(unittest.TestCase):
    NAMES = {'bret': 'Bret'}

    def said(self, present, started=100., at=103.):
        present.rex_said('You call that a haircut?', at, started=started)

    def test_a_smile_starting_during_his_line_is_about_that_line(self):
        present = Present()
        present.saw_expressions([smiling(102., 101.5, unprompted=False)], 102.)
        self.said(present)
        picture = present.describe(104., self.NAMES)
        self.assertEqual(picture['conversation']['rex_last_line']['faces_at_it'], [{'who': 'Bret', 'look': 'smiled'}])
        self.assertNotIn('expressions', picture)

    def test_a_smile_just_after_his_line_is_about_it_and_later_is_not(self):
        present = Present()
        self.said(present)
        present.saw_expressions([smiling(107., 106.5, unprompted=False)], 107.)
        self.assertIn('faces_at_it', present.describe(107.5, self.NAMES)['conversation']['rex_last_line'])
        present = Present()
        self.said(present)
        present.saw_expressions([smiling(120., 119.5, unprompted=False)], 120.)
        picture = present.describe(120.5, self.NAMES)
        self.assertNotIn('faces_at_it', picture['conversation']['rex_last_line'])
        self.assertNotIn('expressions', picture)     # And too faint to mention by itself.

    def test_a_face_already_smiling_when_he_started_did_not_react_to_him(self):
        # V2 test_watch_not_armed_when_person_is_already_smiling.
        present = Present()
        present.saw_expressions([smiling(99.8, 95.)], 99.8)
        self.said(present)
        present.saw_expressions([smiling(103., 95.)], 103.)
        picture = present.describe(103.5, self.NAMES)
        self.assertNotIn('faces_at_it', picture['conversation']['rex_last_line'])
        self.assertEqual(picture['expressions']['faces'],
                         [{'who': 'Bret', 'look': 'smiling', 'since': 'a few seconds ago'}])

    def test_it_is_one_beat_shown_then_spent_by_the_next_heard_line_used_or_not(self):
        present = Present()
        present.saw_expressions([smiling(102., 101.5)], 102.)
        self.said(present)
        present.describe(104., self.NAMES)                  # Reply written with it in front of him.
        present.rex_said('Anyway.', 108., started=106.)
        picture = present.describe(109., self.NAMES)
        self.assertNotIn('faces_at_it', picture['conversation']['rex_last_line'])
        self.assertNotIn('expressions', picture)

    def test_a_cancelled_reply_does_not_spend_it_and_a_line_written_before_it_does_not_either(self):
        present = Present()
        present.saw_expressions([smiling(102., 101.5)], 102.)
        present.describe(102.2, self.NAMES)                 # A reply was begun and never heard.
        self.assertTrue(present.describe(102.4, self.NAMES)['expressions'])
        present = Present()
        present.saw_expressions([smiling(102., 101.5)], 102.)
        self.said(present)                                  # That line was written before the smile.
        self.assertIn('faces_at_it', present.describe(104., self.NAMES)['conversation']['rex_last_line'])

    def test_a_look_over_says_when_and_expires(self):
        present = Present()
        present.saw_expressions([smiling(100., 99.5)], 100.)
        self.assertEqual(present.describe(110., self.NAMES)['expressions']['faces'],
                         [{'who': 'Bret', 'look': 'smiled', 'when': 'a few seconds ago'}])
        self.assertNotIn('expressions', present.describe(100. + LOOK_KEEP_SECONDS + 1, self.NAMES))

    def test_an_unnamed_face_is_someone_and_a_name_learned_mid_smile_sticks(self):
        present = Present()
        present.saw_expressions([smiling(100., 99.5, person=None)], 100.)
        self.assertEqual(present.describe(100.2, self.NAMES)['expressions']['faces'][0]['who'], 'someone')
        present.saw_expressions([smiling(100.5, 99.5)], 100.5)
        present.saw_expressions([smiling(101., 99.5, person=None)], 101.)
        self.assertEqual(present.describe(101.2, self.NAMES)['expressions']['faces'][0]['who'], 'Bret')

    def test_a_far_face_is_not_read_into_the_picture(self):
        present = Present()
        present.saw_expressions([smiling(100., 99.5, width=30)], 100.)
        self.assertNotIn('expressions', present.describe(100.2, self.NAMES))

    def test_forgetting_someone_removes_their_looks(self):
        present = Present()
        present.saw_expressions([smiling(100., 99.5)], 100.)
        present.forget('bret')
        self.assertNotIn('expressions', present.describe(100.2, self.NAMES))

    def test_turn_time_faces_reach_the_picture_too(self):
        present = Present()
        present.saw_faces([smiling(100., 99.5)], 100.)
        self.assertTrue(present.describe(100.2, self.NAMES)['expressions'])

    def test_looks_are_no_evidence_of_presence(self):
        present = Present()
        present.saw_expressions([smiling(100., 99.5)], 100.)
        self.assertFalse(present.here('bret', 100.))
        self.assertEqual(present.describe(100.2, self.NAMES)['people_here'], [])

    def test_he_is_told_to_own_a_smile_at_most_every_so_often(self):
        present = Present()
        present.saw_expressions([smiling(102., 101.5)], 102.)
        self.said(present)
        picture = present.describe(104., self.NAMES)
        self.assertEqual(picture['conversation']['own_it'], {'who': 'Bret', 'at_your_line': True})
        present.rex_said('Anyway.', 108., started=106.)
        present.saw_expressions([smiling(107., 106.5, moment='m2')], 107.)
        picture = present.describe(109., self.NAMES)
        self.assertIn('faces_at_it', picture['conversation']['rex_last_line'])   # The fact, without the push.
        self.assertNotIn('own_it', picture['conversation'])
        later = 108. + OWN_A_SMILE_SECONDS
        present.saw_expressions([smiling(later, later - 40, moment='m3')], later)
        self.assertEqual(present.describe(later + 1, self.NAMES)['conversation']['own_it'],
                         {'who': 'Bret', 'at_your_line': False})

    def test_smiles_he_was_not_told_to_own_do_not_keep_pushing_the_next_one_back(self):
        present = Present()
        present.saw_expressions([smiling(102., 101.5)], 102.)
        self.said(present)
        present.describe(104., self.NAMES)                  # Told to own it.
        present.rex_said('Anyway.', 108., started=106.)     # The clock starts here.
        present.saw_expressions([smiling(109., 108.5, moment='m2')], 109.)
        self.assertNotIn('own_it', present.describe(110., self.NAMES)['conversation'])
        present.rex_said('And another.', 170., started=168.)
        present.saw_expressions([smiling(171., 170.5, moment='m3')], 171.)
        picture = present.describe(184., self.NAMES)         # 76 s after the last push, though 14 s after a smile.
        self.assertEqual(picture['conversation']['own_it']['at_your_line'], True)

    def test_only_a_smile_is_pushed_and_a_look_already_over_is_not(self):
        present = Present()
        present.saw_expressions([smiling(102., 101.5, look='frown')], 102.)
        self.said(present)
        self.assertNotIn('own_it', present.describe(104., self.NAMES)['conversation'])
        present = Present()
        present.saw_expressions([smiling(100., 99.5)], 100.)
        self.assertNotIn('own_it', present.describe(110., self.NAMES)['conversation'])

    def test_a_smile_at_his_line_is_an_answer_so_he_is_not_being_ignored(self):
        present = Present()
        self.said(present)
        present.beat_heard(); present.beat_heard()
        present.saw_expressions([smiling(104., 103.5, look='frown')], 104.)
        self.assertEqual(present.unanswered, 2)             # A frown is not an answer.
        present.saw_expressions([smiling(105., 104.5, moment='m2')], 105.)
        self.assertEqual(present.unanswered, 0)

    def test_a_smile_during_the_line_counts_whichever_way_the_bookkeeping_lands(self):
        present = Present()
        present.saw_expressions([smiling(102., 101.5)], 102.)
        present.beat_heard()
        self.assertEqual(present.unanswered, 1)             # No line of his to smile at yet.
        self.said(present)
        self.assertEqual(present.unanswered, 0)


class TheNextLineHasIt(memory_tests.MemoryFixture):
    def setup_owner(self):
        self.trace = self.state.trace
        self.executor = Executor(); self.planner = Planner()
        self.session = Session('session', self.executor, OutputOwner(SyntheticTTS(),
            timing_tests.ClockSink(self.trace), self.trace), self.trace, self.state)
        self.session.agency_planner = self.planner
        self.runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, self.session, self.trace)
        self.runtime.started_at = 0

    def tick(self, at):
        self.trace.at = at; self.runtime.audio_seen_at = at
        return self.runtime.proactive_tick()

    def reply(self, text):
        now = self.trace.clock()
        capture = Capture(uuid4().hex, 'session', uuid4().bytes * 6000, now - 3, now, now, voiced_intervals=((0., 3.),))
        return self.session.process_turn(capture, Transcript(text, -.01, True), self.session.revision,
                                         memory_tests.observation(memory_tests.A, 3., False), faces=())

    def test_a_smile_at_his_joke_reaches_his_next_reply_once(self):
        self.setup_owner()
        bret = self.enroll('Bret')
        self.trace.at = 300.
        heard = self.reply('Tell me something.')
        line = self.state.present.last_rex
        self.assertEqual(line['started'], heard.first_audio_at)   # Measured from when he began, not finished.
        self.state.present.saw_expressions([smiling(line['at'] + 1, line['at'] + .5, person=bret)], line['at'] + 1)
        self.trace.at = line['at'] + 2
        self.reply('Anyway, groceries.')
        said = self.executor.snapshots[-1].context['right_now']['conversation']['rex_last_line']
        self.assertEqual(said['faces_at_it'], [{'who': 'Bret', 'look': 'smiled'}])
        self.reply('And then I came home.')
        said = self.executor.snapshots[-1].context['right_now']['conversation']['rex_last_line']
        self.assertNotIn('faces_at_it', said)                     # One beat to enjoy it.

    def test_the_planner_hears_a_line_landed_and_the_writer_too(self):
        self.setup_owner()
        bret = self.enroll('Bret')
        self.state.agency.human_spoke(bret, 90)
        self.state.present.rex_said('You call that a haircut?', 95, started=92)
        self.state.present.saw_expressions([smiling(96, 95.5, person=bret)], 96)
        self.tick(100)
        picture = self.planner.requests[0]['right_now']
        self.assertEqual(picture['conversation']['rex_last_line']['faces_at_it'], [{'who': 'Bret', 'look': 'smiled'}])
        written = json.loads(jokes.summary([], picture, [], {}))
        self.assertEqual(written['moment']['rex_last_line']['faces_at_it'], [{'who': 'Bret', 'look': 'smiled'}])


class NoSayInWhoIsSpeaking(memory_tests.MemoryFixture):
    """Mouth and expression are never speaker evidence (AGENTS.md: excluded from ALL attribution paths)."""
    def speak(self, vector, row):
        self.at += 10
        capture = Capture(uuid4().hex, 'session', uuid4().bytes * 4000, self.at, self.at + 2., self.at + 2.,
                          voiced_intervals=((0., 2.),))
        self.state.begin(uuid4().hex, capture, Transcript('Hello.', -.01, True), presence_tests.clip(vector, 2.), [row])
        return self.state.current[4]

    def look(self, person, **fields):
        return {**presence_tests.face(person), 'expression': 'smile', 'expression_since': 100.,
                'expression_moment': uuid4().hex, 'expression_unprompted': True, **fields}

    def test_a_smile_does_not_change_the_deduction_or_open_any_learning(self):
        plain, smiling_turn = [], []
        person = self.enroll('Bret')
        self.state.present.saw_faces([presence_tests.face(person)], 100.)
        for store, row in ((plain, presence_tests.face(person)), (smiling_turn, self.look(person))):
            attribution = self.speak(presence_tests.CLOSE, row)
            store.append((attribution.state, attribution.reason, attribution.inferred_person_id,
                          attribution.learning_eligible))
        self.assertEqual(smiling_turn, plain)
        self.assertEqual(smiling_turn[0][:2], ('inferred', 'alone_with_recognized_face'))
        self.assertFalse(smiling_turn[0][3])

    def test_a_smile_never_names_a_voice_too_far_from_anyone(self):
        person = self.enroll('Bret')
        self.state.present.saw_faces([presence_tests.face(person)], 100.)
        attribution = self.speak(presence_tests.FAR, self.look(person))
        self.assertNotEqual(attribution.state, 'inferred')
        self.assertIsNone(attribution.inferred_person_id)
        self.assertNotEqual(attribution.person_id, person)


class CameraPass(unittest.TestCase):
    def camera(self):
        camera = Camera('fixture', Path('/unused'), trace=mock.Mock())
        sample = FaceSample(None, b'', b'', 'f', 'c', 's', 0., 120., .9, 80., 100., (), 'n', False, (0., 0., 120., 120.))
        camera.encoder = mock.Mock(analyze=mock.Mock(return_value=[(BOX, sample)]), detector=None)
        return camera

    def run_frames(self, camera, count):
        import time
        frames = [Frame(str(i), 'fixture', 's', time.monotonic() + i * .01, 2, 2, bytes(12)) for i in range(count)]
        def take(*_):
            if frames:
                return frames.pop(0)
            camera.stop.set()
        camera.hub.take = take
        with mock.patch.object(camera.stop, 'wait'):
            camera._analyze()

    def test_looks_reach_the_face_rows_and_the_picture_between_turns(self):
        camera = self.camera()
        camera.expressions = mock.Mock(read_faces=mock.Mock(return_value=[smile(.9)]))
        told = []
        camera.on_faces = lambda rows, at: told.append(rows)
        self.run_frames(camera, 2)
        self.assertEqual(told[-1][0]['expression'], 'smile')
        camera.expressions.read_faces.assert_called_with(mock.ANY, [(0., 0., 120., 120.)])

    def test_a_reader_that_fails_costs_the_looks_never_the_faces(self):
        camera = self.camera()
        camera.expressions = mock.Mock(read_faces=mock.Mock(side_effect=RuntimeError('native')))
        self.run_frames(camera, 2)
        self.assertEqual(len(camera.tracker.history), 2)
        self.assertIsNone(camera.expressions)
        self.assertIsNone(camera.failure)
        camera.trace.emit.assert_any_call('provider_failed', capability='expressions', error_type='RuntimeError')

    def test_bookkeeping_that_fails_does_not_stop_the_eyes(self):
        camera = self.camera()
        camera.on_faces = mock.Mock(side_effect=ValueError('boom'))
        self.run_frames(camera, 2)
        self.assertIsNone(camera.failure)
        self.assertEqual(len(camera.tracker.history), 2)


class Reader(unittest.TestCase):
    def test_nothing_loads_until_start_and_a_missing_or_wrong_model_is_a_clean_failure(self):
        reader = ExpressionReader(Path('/nonexistent/face_landmarker.task'))
        self.assertIsNone(reader.landmarker)
        with self.assertRaisesRegex(ProviderFailure, 'ExpressionModelMissingOrDigestMismatch'):
            reader.start()
        reader.close()

    def test_it_declares_itself_optional_and_dependent_on_the_camera(self):
        capability = ExpressionReader(Path('/x')).capability
        self.assertFalse(capability.required)
        self.assertEqual(capability.requires, ('camera',))
        self.assertIn('mediapipe', capability.dependencies)

    def test_a_crowd_reads_only_the_largest_faces_wide_enough(self):
        reader = ExpressionReader(Path('/x'))
        read = []
        reader.read = lambda image, box: read.append(box) or smile(.1)
        frame = SimpleNamespace(pixels=bytes(1200), width=20, height=20)
        boxes = [(0., 0., w, w) for w in (30., 200., 60., 150., 100., 49.)]
        numpy = SimpleNamespace(uint8=int, frombuffer=lambda *_, **__: SimpleNamespace(reshape=lambda *_: None))
        with mock.patch.dict('sys.modules', {'numpy': numpy}):   # The blocked runner has no native libraries.
            result = reader.read_faces(frame, boxes)
        self.assertEqual(sorted(b[2] for b in read), [100., 150., 200.])
        self.assertEqual(len(read), MAX_FACES)
        self.assertEqual([r is not None for r in result], [False, True, False, True, True, False])

    def test_a_read_after_close_is_no_reading(self):
        reader = ExpressionReader(Path('/x'))
        self.assertIsNone(reader.landmarker)
        cv2 = SimpleNamespace(cvtColor=lambda crop, code: crop, COLOR_BGR2RGB=4)
        with mock.patch('r3x.adapters.expression.face_crop', return_value=object()):
            with mock.patch.dict('sys.modules', {'cv2': cv2}):
                self.assertIsNone(reader.read(None, (0, 0, 1, 1)))


if __name__ == '__main__':
    unittest.main()
