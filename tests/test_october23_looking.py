"""October 23 run 1e626e07: asked six times to look left and find the duckling droid on the table.

Fake serial, fake clock, synthetic frames and scripted model calls only. These check what the program
does with a look request; whether the reply model picks the right direction is the paid probe's job
(tools/check_action_requests.py)."""
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.inspection import CurrentView
from r3x.output import OutputOwner, TextOutput
from r3x.photo_memory import PhotoMemory
from r3x.robot_servos import RobotServos
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servo_poses import NECK_LEFT
from r3x.session import LOOK_HOLD_SECONDS, Session
from r3x.semantics import grounded_tools, validate
from tests.test_drive_session import FakeScene
from tests.test_memory_core import MemoryFixture, observation, C
from tests.test_photo_memory import image
from tests.test_servos import Clock, PulsePort


class Scripted:
    """Answers the first request with `call` and any continuation with `answer`."""
    def __init__(self, call, answer='There he is, on the table.'):
        self.call, self.answer, self.snapshots = call, answer, []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        yield self.answer if snapshot.continuation else self.call


class LookingTests(MemoryFixture):
    def setup_robot(self, call, answer='There he is, on the table.'):
        self.clock, self.port = Clock(), PulsePort()
        self.robot = RobotServos('fake', open_port=lambda: self.port, clock=self.clock, wait=self.clock.wait)
        self.robot.owner.wait = self.clock.wait
        self.robot.start()
        self.addCleanup(self.robot.close)
        self.brain = Scripted(call, answer)
        view = CurrentView(FakeScene(self.clock), clock=self.clock)
        view.start()
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, robot=self.robot, inspection=view)
        self.state.trace.clock = self.clock
        self.session = Session('s', self.brain, self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)

    def turn(self, text, start=98.):
        return self.session.process_turn(Capture(uuid4().hex, 's', bytes(1024), start, start + 1, start + 1.6),
                                         Transcript(text, -.01, True), self.session.revision)

    def rows(self, event):
        return [r for r in self.state.trace.rows if r['event'] == event]

    def reflex(self, bearing=-35.):
        outcome = self.robot.reflex.face(bearing)
        if self.robot.reflex._thread:
            self.robot.reflex._thread.join(5)
        return outcome

    def test_a_look_in_a_direction_turns_the_head_first_and_holds_it_against_the_reflex(self):
        self.setup_robot(ToolCall('v', 'inspect_view', {'direction': 'left', 'question': 'Is the duckling droid there?'}))
        result = self.turn('Look to your left and see if you see it.')
        self.assertIn('left', self.brain.snapshots[0].context['inspection_directions'])
        self.assertEqual(self.brain.snapshots[0].context['inspection_mode'], 'head_looks')
        self.assertEqual(result.completed_text, ['There he is, on the table.'])
        self.assertEqual(self.port.pose[0], NECK_LEFT)
        look = self.rows('inspection_result')[-1]
        self.assertEqual((look['head_look'], look['status']), ('left', 'observed'))
        self.assertGreaterEqual(look['captured_at'], self.rows('robot_pose_result')[-1]['at'])
        self.assertTrue(self.brain.snapshots[-1].images)
        # "You're not finding it" no longer swings the head back while the look holds.
        self.assertEqual(self.reflex(), 'holding_requested_look')
        self.assertEqual(self.port.pose[0], NECK_LEFT)
        self.clock.wait(LOOK_HOLD_SECONDS + 1)
        self.assertNotEqual(self.reflex(), 'holding_requested_look')
        self.assertNotEqual(self.port.pose[0], NECK_LEFT)

    def test_a_second_look_the_same_way_looks_from_there_instead_of_swinging_further(self):
        self.setup_robot(ToolCall('v', 'inspect_view', {'direction': 'left', 'question': 'Is the droid there?'}))
        self.turn('Look to your left.')
        moves = len(self.rows('robot_pose_result'))
        self.brain.call = ToolCall('v2', 'inspect_view', {'direction': 'left', 'question': 'The white one?'})
        result = self.turn("He's white.", start=110.)
        self.assertEqual(result.completed_text, ['There he is, on the table.'])
        self.assertEqual(len(self.rows('robot_pose_result')), moves)      # No second move.
        self.assertEqual(self.port.pose[0], NECK_LEFT)
        self.assertEqual(self.rows('inspection_result')[-1]['status'], 'observed')

    def test_a_persons_look_always_ends_in_a_picture_taken_before_any_look_back(self):
        # "Did you forget the part where I told you to look left first and then find him?"
        self.setup_robot(ToolCall('p', 'set_robot_pose', {'poses': ['look_left', 'look_at_people'], 'look_after': None,
                                                          'say_before': 'On it.', 'text': None}))
        result = self.turn('Look left first and then find him.')
        self.assertEqual(result.completed_text, ['On it.', 'There he is, on the table.'])
        look = self.rows('inspection_result')[-1]
        self.assertEqual(look['after_poses'], ['look_left'])
        self.assertIn('Look left first and then find him.', look['question'])
        events = [(r['event'], r.get('pose')) for r in self.state.trace.rows
                  if r['event'] in {'robot_pose_result', 'inspection_result'}]
        self.assertEqual(events, [('robot_pose_result', 'look_left'), ('inspection_result', None),
                                  ('robot_pose_result', 'look_at_people')])
        # Asked to look back, the head follows people again and the reflex may turn it.
        self.assertFalse(self.robot.owner.holding_requested_look())

    def test_his_own_look_back_does_not_end_a_look_a_person_asked_for(self):
        self.setup_robot(ToolCall('p', 'set_robot_pose', {'poses': ['look_left'], 'look_after': None,
                                                          'say_before': None, 'text': None}))
        self.turn('Look to your left.')
        receipt = self.robot.execute_pose('look_at_people', Cancellation(), 'own', hold_pose=False)
        self.assertEqual((receipt.status, receipt.reason), ('rejected', 'HoldingRequestedLook'))
        self.assertEqual(self.port.pose[0], NECK_LEFT)

    def test_he_knows_why_he_looks_there_and_when_his_head_has_turned_away(self):
        self.setup_robot(ToolCall('p', 'set_robot_pose', {'poses': ['look_left'], 'look_after': None,
                                                          'say_before': None, 'text': None}))
        self.turn('Look left first.')
        looking = self.session._looking([], {})
        self.assertIn('asked to: Look left first.', looking['why'])
        self.assertNotIn('earlier_look', looking)
        self.clock.wait(LOOK_HOLD_SECONDS + 1)
        self.assertEqual(self.reflex(), 'started')
        looking = self.session._looking([], {})
        self.assertEqual(looking['why'], 'his head turned toward the voice of whoever spoke')
        self.assertIn('last_look', looking['earlier_look'])
        self.assertIn('last_look', self.session.look_directions())
        # "Do you see it?" after the head turned away: last_look points it back where he was asked.
        self.brain.call = ToolCall('v2', 'inspect_view', {'direction': 'last_look', 'question': 'Do you see it?'})
        self.turn('Do you see it now?', start=140.)
        self.assertEqual(self.port.pose[0], NECK_LEFT)
        self.assertEqual(self.rows('inspection_result')[-1]['head_look'], 'last_look')



class NeckCalibrationTests(MemoryFixture):
    setup_robot, reflex = LookingTests.setup_robot, LookingTests.reflex

    def test_the_voice_reflex_lands_on_the_speakers_face_seen_at_the_edge_of_his_view(self):
        # 4:29 PM: head looking left (2944), Bret's face at the right edge (x 0.94) and his voice 35 degrees
        # right. With the old neck figure the reflex aimed at 8 degrees right (6116) and missed him.
        self.setup_robot('unused')
        self.robot.owner.commanded[0] = NECK_LEFT
        self.port.pose[0] = NECK_LEFT
        self.robot.reflex.faces = lambda: [[.92, .42, .04, .11]]
        self.assertEqual(self.reflex(-35.), 'started')
        self.assertGreater(self.port.pose[0], 7800)   # About 24 degrees right, at his face.


class SplitSentenceTests(MemoryFixture):
    setup_robot, turn, rows = LookingTests.setup_robot, LookingTests.turn, LookingTests.rows

    def test_an_unanswered_piece_and_the_next_one_are_answered_as_one_utterance(self):
        self.setup_robot('unused')
        session = self.session
        class Yields:
            snapshots = []
            def reply(inner, snapshot, cancel):
                inner.snapshots.append(snapshot)
                if len(inner.snapshots) == 1:
                    session.note_speech(100.)   # He kept talking: this reply yields unheard.
                    cancel.check()
                yield 'Ooh, let me look.'
        self.brain = self.session.brain = Yields()
        first = self.turn('Look to your left and notice there is a.', start=97.)
        self.assertEqual(first.status, 'interrupted')
        self.turn('Duckling droid on the counter.', start=99.)
        joined = 'Look to your left and notice there is a. Duckling droid on the counter.'
        last = self.brain.snapshots[-1]
        self.assertEqual(last.context['current_utterance'], joined)
        users = [m['content'] for m in last.messages if m.get('role') == 'user']
        self.assertEqual(users[-1], joined)
        self.assertNotIn('Look to your left and notice there is a.', users)
        self.assertEqual(self.rows('fragments_joined')[-1]['utterance'], joined)

    def test_a_piece_he_answered_is_not_joined(self):
        self.setup_robot('Sure.')
        self.turn('Look to your left.', start=97.)
        self.turn('Thanks.', start=99.)
        self.assertFalse(self.rows('fragments_joined'))


class ThingLookTests(MemoryFixture):
    """"He's white" (the droid) was answered with both dogs' crops and photos: "that's Toby"."""
    def setUp(self):
        super().setUp()
        self.album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); self.album.start()
        self.state.photo_memory = self.album
        evidence = self.album.offer(image(), {'label': 'dog', 'box': [.4, .3, .1, .2], 'target_id': 'object-1'}, 100.)
        self.album.apply(ToolCall('t', 'remember_object', {'evidence_id': evidence, 'entity_id': None, 'name': 'Toby',
                                  'kind': 'pet', 'notes': 'Boston terrier', 'source_turn_id': 'turn', 'text': 'Got it.'}),
                         {'turn_id': 'turn', 'session_id': 's', 'text': 'This is Toby.'}, 100.)
        self.toby = next(iter(self.album.data['entities']))
        pet = {'label': 'dog', 'box': [.6, .6, .1, .2], 'target_id': 'object-2', 'where': 'right of the view'}
        self.state.objects = SimpleNamespace(snapshot=lambda: (), pet_views=lambda: [(image(), pet)],
            inspect=lambda target: (image(), {'label': 'whole view', 'box': [0, 0, 1, 1]}, image()))

    def look(self, subject):
        snapshots = []
        toby = self.toby
        class Brain:
            def reply(brain, snapshot, cancel):
                snapshots.append(snapshot)
                yield ('I see it.' if snapshot.continuation else
                       ToolCall('l', 'inspect_visual_memory', {'intent': 'look', 'target_id': None, 'entity_ids': [toby],
                                'question': 'Is the white duckling droid there?', 'direction': 'current', 'subject': subject}))
        self.begin('He is white.', C)
        session = Session('session', Brain(), TextOutput(self.state.trace), self.state.trace, self.state)
        receipt = session.process_turn(self.state.current[1], Transcript('He is white.', 0, True), 0, observation(C))
        self.assertEqual(receipt.status, 'completed')
        return snapshots[-1]

    def test_a_look_about_a_thing_gets_no_pet_crops_or_pet_photos(self):
        followup = self.look('thing')
        roles = [i.get('role') or i.get('kind') for i in followup.context['photo_inspection']['images']]
        self.assertEqual(roles, ['current overview'])
        self.assertEqual(len(followup.images), 1)
        self.assertTrue(any('not an animal' in m.get('content', '') for m in followup.messages if m.get('role') == 'developer'))

    def test_a_look_about_an_animal_still_compares_pets(self):
        followup = self.look('animal')
        roles = [i.get('role') or i.get('kind') for i in followup.context['photo_inspection']['images']]
        self.assertEqual(roles, ['current overview', 'current pet crop', 'pet'])

    def test_direction_and_subject_are_required_of_the_model_and_optional_for_old_callers(self):
        definition = next(d for d in grounded_tools({'source_turn_id': 't', 'photo_memory': {'objects': [], 'entities': []}})
                          if d['name'] == 'inspect_visual_memory')
        self.assertIn('direction', definition['parameters']['required'])
        self.assertEqual(definition['parameters']['properties']['direction']['enum'], ['current'])
        validate('inspect_visual_memory', {'intent': 'look', 'target_id': None, 'entity_ids': [], 'question': 'What is it?'})
