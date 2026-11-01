"""October 23 run 1e626e07, the repairs besides looking: a dropped reply, the eyes and pet facts
(repeats moved to test_familiarity.py, October 31).

Synthetic faces, fake LED ports, scripted model calls and temporary stores only."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.leds import EYE_REFRESH_SECONDS
from r3x.output import OutputOwner, TextOutput
from r3x.photo_memory import PhotoMemory
from r3x.proactive import Opportunity
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from tests.test_leds import rig
from tests.test_memory_core import MemoryFixture, observation, C
from tests.test_photo_memory import image
from tests.test_proactive import face
from tests.test_voice_core import Brain


class NewcomerTests(MemoryFixture):
    """"There's also a marionette puppet next to him" lost its reply to one blurry frame."""
    def setUp(self):
        super().setUp()
        self.person = self.enroll('Alex')
        self.state.wall_clock = lambda: 1800000000.
        self.turn(self.session(Brain('Noted.')), 'A private project.')

    def session(self, brain):
        return Session(uuid4().hex, brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                       self.state.trace, self.state)

    def turn(self, session, text, current=None):
        current = current if current is not None else [face(100, self.person)]
        cap = Capture(uuid4().hex, session.session_id, bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        return session.process_turn(cap, Transcript(text, 0, True), 0, observation(), tuple(current),
                                    latest_faces=lambda now: tuple(current))

    def appearing(self, extra):
        current = [face(100, self.person)]
        class Changed:
            snapshots = []
            def reply(inner, snapshot, cancel):
                inner.snapshots.append(snapshot)
                current.append(extra)
                yield 'Your private project was a telescope.'
        return Changed(), current

    def test_a_single_weak_frame_is_not_someone_new(self):
        blur = {**face(100, None, 'blur'), 'detector_score': .51, 'track_first_seen': 100}
        brain, current = self.appearing(blur)
        result = self.turn(self.session(brain), 'What did we discuss?', current)
        self.assertEqual(result.completed_text, ['Your private project was a telescope.'])

    def test_someone_really_new_withholds_private_words_and_the_reply_is_owed(self):
        visitor = {**face(100, None, 'visitor'), 'detector_score': .85, 'track_first_seen': 98}
        brain, current = self.appearing(visitor)
        session = self.session(brain)
        result = self.turn(session, 'What did we discuss?', current)
        self.assertFalse(result.completed_text)
        self.assertEqual([r['reason'] for r in self.state.trace.rows if r['event'] == 'reply_cancelled'][-1],
                         'private_memory_audience_changed')
        self.assertTrue(any('dropped unheard' in str(m.get('content')) for m in session.history))
        # His next lull answers them; his own goal waits (the planner must not be asked).
        session.brain = Brain('We talked about your telescope.')
        session.agency_planner = SimpleNamespace()   # Any use of it would fail the turn.
        event = Opportunity('owed', session.session_id, 101, None, None, (), session._latest_speech, False,
                            'reconsideration')
        answer = session.process_opportunity(event, session.revision, lambda: True)
        self.assertEqual(answer.completed_text, ['We talked about your telescope.'])
        self.assertTrue(any(r['event'] == 'owed_answer' for r in self.state.trace.rows))
        self.assertIsNone(session._unanswered_turn)


class PetFactTests(MemoryFixture):
    """"He's very feisty, and he likes to bark a lot" about Max was refused as a fact about a person."""
    def setUp(self):
        super().setUp()
        self.album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); self.album.start()
        self.state.photo_memory = self.album
        for name in ('Max', 'Toby'):
            evidence = self.album.offer(image(), {'label': 'dog', 'box': [.4, .3, .1, .2], 'target_id': 'object-' + name}, 100.)
            self.album.apply(ToolCall('t' + name, 'remember_object', {'evidence_id': evidence, 'entity_id': None,
                'name': name, 'kind': 'pet', 'notes': 'Boston terrier', 'source_turn_id': 'turn', 'text': 'Got it.'}),
                {'turn_id': 'turn', 'session_id': 's', 'text': f'This is {name}.'}, 100.)
        self.max = next(k for k, e in self.album.data['entities'].items() if e['name'] == 'Max')

    def claim(self, text, said_before='Max is a very surprising animal.'):
        self.begin(said_before, C)
        self.begin(text, C)
        call = ToolCall(uuid4().hex, 'record_claim', {'subject_id': None, 'subject_reference': 'Max',
            'predicate': 'temperament', 'value': 'feisty and barks a lot', 'kind': 'third_party',
            'source_turn_id': self.state.current[0], 'other_person': None})
        return self.state.execute(call, self.store.revision, Cancellation())

    def test_a_fact_about_a_pet_named_a_moment_ago_goes_on_its_record(self):
        result = self.claim("He's very feisty, and he likes to bark a lot.")
        self.assertEqual((result.outcome, result.reason), ('ok', 'saved_on_pet_record'))
        self.assertIn('feisty and barks a lot', self.album.data['entities'][self.max]['notes'])
        self.assertEqual(self.store.claims(), [])                       # Never a person's claim.

    def test_words_that_name_another_pet_are_not_filed_on_this_one(self):
        result = self.claim("Toby is very feisty, and he likes to bark a lot.")
        self.assertNotEqual(result.reason, 'saved_on_pet_record')
        self.assertNotIn('feisty', self.album.data['entities'][self.max]['notes'])


    def another_photo(self, said_before, text):
        from r3x.scene import encode_frame
        from r3x.vision import Frame
        self.begin(said_before, C)
        self.begin(text, C)
        other = encode_frame(Frame('other-frame', 'fake', 'session', 100., 4, 4, bytes([90, 30, 10]) * 16))[0]
        evidence = self.album.offer(other, {'label': 'dog', 'box': [.5, .3, .1, .2], 'target_id': 'object-9'},
                                    self.state.trace.clock())
        call = ToolCall(uuid4().hex, 'remember_object', {'evidence_id': evidence, 'entity_id': self.max, 'name': 'Max',
            'kind': 'pet', 'notes': 'Held up.', 'source_turn_id': self.state.current[0], 'text': 'Got another.'})
        context = {'state_revision': self.store.revision, 'photo_memory': {'revision': self.album.data['revision']},
                   'photo_inspection': {'evidence_id': evidence}}
        return self.state.apply_photo(call, context, Cancellation())

    def test_another_photo_of_a_pet_just_named_needs_no_name_again(self):
        # Owner ruling October 23 (run 8e11d468): "Take another picture of him to add to the memory".
        result = self.another_photo('No, you are wrong. This is Max.', 'Take another picture of him to add to the memory.')
        self.assertEqual(result['outcome'], 'ok', result)
        self.assertEqual(len(self.album.data['entities'][self.max]['references']), 2)

    def test_words_naming_another_pet_never_add_a_photo_to_this_one(self):
        result = self.another_photo('This is Max.', 'Take a picture of Toby too.')
        self.assertEqual((result['outcome'], result['reason']), ('rejected', 'PhotoSubjectNeedsExplicitName'))


class EyeRefreshTests(MemoryFixture):
    def test_between_lines_the_eye_colour_is_sent_again_but_never_over_a_blink(self):
        led, ports, clock = rig()
        self.addCleanup(led.close)
        def eyes():
            return [c for c in ports['head'].commands if c.startswith('EYE:')]
        start = len(eyes())
        led.tick(); clock.wait(1); led.tick()
        self.assertEqual(len(eyes()), start + 1)                        # Once, then not again for a while.
        clock.wait(EYE_REFRESH_SECONDS); led.tick()
        self.assertEqual(eyes()[-1], 'EYE:255,200,0')
        self.assertEqual(len(eyes()), start + 2)
        led.blinking = True
        clock.wait(EYE_REFRESH_SECONDS); led.tick()
        self.assertEqual(len(eyes()), start + 2)                        # A requested blink stays dark.
