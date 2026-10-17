"""October 16 reply-speed repairs; scripted providers, synthetic audio and simulated hardware only.

On October 13 moves were half of all replies (median 4.3 s), looks and lookups never used their
optional lead-in (6-10 s), and a save or a quiet request needed a second model call before Rex
said anything.
"""
import time

from r3x.contracts import Capture, ToolCall, ToolText, Transcript
from r3x.output import OutputOwner
from r3x.robot_servos import RobotServos
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import TOOLS
from r3x.servo_poses import NAMED_POSES
from r3x.session import Session
from tests import test_drive_session
from tests.test_drive_session import step
from tests.test_memory_core import MemoryFixture, observation
from tests.test_servos import Clock, PulsePort


def wait_for_audio(session, seconds=3):
    deadline = time.monotonic() + seconds
    while session._delivery.first_audio_at is None and time.monotonic() < deadline:
        time.sleep(.01)
    return session._delivery.first_audio_at is not None


class StreamedMoveBrain:
    """Streams a move's acknowledgment, waits until it is audible, then finishes the call."""
    def __init__(self, call, followup='I could not do that one.'):
        self.call, self.followup, self.snapshots, self.heard_before_call = call, followup, [], None
        self.session = None

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        if snapshot.continuation:
            yield self.followup
            return
        yield ToolText(self.call.call_id, self.call.name, self.call.arguments['say_before'][:4])
        yield ToolText(self.call.call_id, self.call.name, self.call.arguments['say_before'][4:], True)
        self.heard_before_call = wait_for_audio(self.session)
        yield self.call


def drive(*steps, say_before='On my way!'):
    return ToolCall('drive', 'drive_base', {'say_before': say_before, 'steps': list(steps),
                                             'purpose': 'asked to move', 'look_after': None, 'say_after': None})


class StreamedDriveTests(MemoryFixture):
    setup_drive = test_drive_session.DriveSessionTests.setup_drive
    turn = test_drive_session.DriveSessionTests.turn

    def stream(self, call, followup='I could not do that one.'):
        self.setup_drive(call)
        self.brain = StreamedMoveBrain(call, followup)
        self.brain.session = self.session
        self.session.brain = self.brain

    def test_the_acknowledgment_plays_while_the_plan_is_written_and_is_said_once(self):
        self.stream(drive(step('move', meters=.5)))
        delivery = self.turn()
        self.assertTrue(self.brain.snapshots[0].stream_tool_text)
        self.assertTrue(self.brain.heard_before_call)
        self.assertEqual(delivery.completed_text, ['On my way!'])
        self.assertAlmostEqual(self.fake.x, .5, places=2)
        self.assertEqual(len(self.brain.snapshots), 1)

    def test_a_refused_plan_after_a_streamed_acknowledgment_is_explained_without_repeating_it(self):
        self.stream(drive(step('spin', degrees=90)), followup='Actually, I can not spin like that.')
        delivery = self.turn()
        self.assertEqual(delivery.completed_text, ['On my way!', 'Actually, I can not spin like that.'])
        self.assertEqual(self.fake.x, 0)
        notes = [m['content'] for m in self.brain.snapshots[-1].messages if m.get('role') == 'developer']
        self.assertTrue(any('do not repeat an earlier acknowledgment' in n for n in notes))


class StreamedPoseTests(MemoryFixture):
    def test_the_acknowledgment_streams_and_the_arm_moves_after_it_has_played(self):
        clock = Clock(); port = PulsePort()
        robot = RobotServos('fake', open_port=lambda: port, clock=clock, wait=clock.wait)
        robot.owner.wait = clock.wait
        robot.start(); self.addCleanup(robot.close)
        brain = StreamedMoveBrain(ToolCall('pose', 'set_robot_pose', {'say_before': 'Watch this.',
            'poses': ['throttle_offer'], 'look_after': None, 'text': 'Here is my hand.'}))
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, robot=robot)
        session = Session('s', brain, output, self.state.trace, self.state); self.addCleanup(session.close)
        brain.session = session
        result = session.process_turn(Capture('c', 's', bytes(1024), 98, 99, 99.6),
                                      Transcript('Hold out your hand', -.01, True), session.revision)
        self.assertTrue(brain.heard_before_call)
        self.assertEqual(result.completed_text, ['Watch this.', 'Here is my hand.'])
        self.assertEqual(len(brain.snapshots), 1)
        for channel, value in NAMED_POSES['throttle_offer']:
            self.assertEqual(port.pose[channel], value)
        events = [r['event'] for r in self.state.trace.rows]
        self.assertLess(events.index('phrase_completed'), events.index('robot_pose_result'))


class LeadInSchemaTests(MemoryFixture):
    def test_every_look_or_lookup_is_asked_for_a_lead_in(self):
        for name in ('inspect_view', 'inspect_visual_memory', 'recall_conversations', 'personal_history'):
            parameters = next(t for t in TOOLS if t['name'] == name)['parameters']
            self.assertEqual(list(parameters['properties'])[0], 'say_first')
            self.assertEqual(parameters['properties']['say_first']['type'], 'string')
            self.assertIn('say_first', parameters['required'])


class SpokenResultBrain:
    """One bookkeeping call carrying its reply; a continuation would say something else."""
    def __init__(self, name, arguments):
        self.name, self.arguments, self.snapshots = name, arguments, []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        if snapshot.continuation:
            yield 'A second answer.'
            return
        yield ToolCall('save', self.name, {**self.arguments, 'source_turn_id': snapshot.context['source_turn_id']})


PLAN = {'predicate': 'trip', 'value': 'Obama library next week', 'kind': 'plan', 'on_date': None,
        'due_in_minutes': None, 'due_by': None, 'audience': 'subject_only',
        'text': 'The Obama library? Bring me back a souvenir.'}


class SpokenResultTests(MemoryFixture):
    def converse(self, name, arguments, said='Next week I am going to the Obama library.'):
        if arguments.get('kind') == 'plan':
            from datetime import timedelta
            arguments = {**arguments, 'on_date': (self.state.calendar_today() + timedelta(days=7)).isoformat()}
        brain = SpokenResultBrain(name, arguments)
        session = Session('s', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        self.addCleanup(session.close)
        cap = Capture('cap', 's', bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        return brain, session, session.process_turn(cap, Transcript(said, 0, True), 0, observation(), ())

    def followups(self):
        with self.store.transaction() as conn:
            return conn.execute('SELECT COUNT(*) FROM memory_followups').fetchone()[0]

    def test_a_saved_plan_is_answered_from_the_same_call(self):
        self.enroll('Sam')
        brain, session, result = self.converse('remember_followup', PLAN)
        self.assertEqual(result.completed_text, [PLAN['text']])
        self.assertEqual(len(brain.snapshots), 1)
        self.assertEqual(self.followups(), 1)
        calls = [m for m in session.history.messages() if m.get('type') == 'function_call']
        self.assertEqual([c['name'] for c in calls], ['remember_followup'])

    def test_a_refused_save_never_speaks_its_acknowledgment(self):
        brain, session, result = self.converse('remember_followup', PLAN)    # Nobody recognized.
        self.assertEqual(result.completed_text, ['A second answer.'])
        self.assertEqual(len(brain.snapshots), 2)
        self.assertEqual(self.followups(), 0)

    def test_while_told_to_stay_silent_the_save_stands_and_the_words_are_withheld(self):
        self.enroll('Sam')
        self.state.initiative.quiet = self.state.initiative.silent = True
        brain, session, result = self.converse('remember_followup', PLAN)
        self.assertEqual(result.completed_text, [])
        self.assertEqual(result.status, 'suppressed')
        self.assertEqual(len(brain.snapshots), 1)
        self.assertEqual(self.followups(), 1)

    def test_going_quiet_still_goes_back_to_the_model(self):
        # 511ce677: with a text field it was used to answer "raise your head" three times.
        self.assertNotIn('text', next(t for t in TOOLS if t['name'] == 'set_initiative')['parameters']['properties'])
        self.enroll('Sam')
        brain, session, result = self.converse('set_initiative', {'mode': 'quiet'},
                                               said='Stop interrupting me for a while.')
        self.assertEqual(result.completed_text, ['A second answer.'])
        self.assertEqual(len(brain.snapshots), 2)
        self.assertTrue(self.state.initiative.quiet)

    def test_without_a_line_the_result_still_goes_back_to_the_model(self):
        self.enroll('Sam')
        brain, session, result = self.converse('remember_followup', {**PLAN, 'text': None})
        self.assertEqual(result.completed_text, ['A second answer.'])
        self.assertEqual(len(brain.snapshots), 2)


class AdapterMoveStreamingTests(MemoryFixture):
    def test_the_hosted_adapter_streams_a_moves_acknowledgment_ahead_of_the_call(self):
        import json
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Cancellation, Snapshot
        from tests.test_adapters import Client, Response
        arguments = json.dumps(drive(step('move', meters=.5)).arguments)
        events = ([{'type': 'response.output_item.added', 'output_index': 0,
                    'item': {'type': 'function_call', 'call_id': 'd', 'name': 'drive_base', 'arguments': ''}}]
                  + [{'type': 'response.function_call_arguments.delta', 'output_index': 0, 'delta': arguments[i:i + 6]}
                     for i in range(0, len(arguments), 6)]
                  + [{'type': 'response.output_item.done', 'output_index': 0,
                      'item': {'type': 'function_call', 'call_id': 'd', 'name': 'drive_base', 'arguments': arguments}},
                     {'type': 'response.completed'}])
        brain = OpenAIBrain('fixture')
        brain.client = Client(Response(events))
        snapshot = Snapshot('s', 't', 0, (), tools_enabled=True, stream_tool_text=True)
        out = list(brain._stream_response({'max_output_tokens': 100}, snapshot, Cancellation()))
        texts = [d for d in out if isinstance(d, ToolText)]
        self.assertEqual(''.join(t.text for t in texts), 'On my way!')
        self.assertTrue(texts[-1].done)
        self.assertLess(out.index(texts[-1]), len(out) - 1)    # Before the finished call.
        self.assertIsInstance(out[-1], ToolCall)


class LookWaitsForLeadInTests(MemoryFixture):
    """511ce677: both looks failed at once because the lead-in's talking motion held the head."""
    class View:
        def __init__(self): self.calls = 0
        def execute(self, request, cancel, valid):
            self.calls += 1
            return {'request_id': request.request_id, 'status': 'observed', 'stage': 'complete'}, ('image',)

    def request(self):
        from r3x.inspection import InspectionRequest
        return InspectionRequest('look', 'current', 'What is there?', 0, 0.)

    def test_a_look_waits_for_words_already_playing(self):
        import threading
        from r3x.contracts import Cancellation
        view = self.View()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, inspection=view)
        output._motion_lock.acquire()
        threading.Timer(.3, output._motion_lock.release).start()      # The lead-in finishes.
        data, images = output.execute_inspection(self.request(), Cancellation(), lambda: True)
        self.assertEqual((data['status'], images, view.calls), ('observed', ('image',), 1))

    def test_a_look_still_gives_up_when_the_head_stays_busy(self):
        from unittest import mock
        from r3x import output as output_module
        from r3x.contracts import Cancellation
        view = self.View()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, inspection=view)
        output._motion_lock.acquire()
        self.addCleanup(output._motion_lock.release)
        with mock.patch.object(output_module, 'INSPECTION_WAIT_SECONDS', .2):
            data, images = output.execute_inspection(self.request(), Cancellation(), lambda: True)
        self.assertEqual((data['reason'], images, view.calls), ('MotionResourceBusy', (), 0))


class ScriptBrain:
    """Replies in order; each item is a ToolCall or words."""
    def __init__(self, *replies):
        self.replies, self.snapshots = list(replies), []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        yield self.replies.pop(0)


class RefusedMoveRetryTests(MemoryFixture):
    """511ce677: "go look around" ended at one refused turn though the right side was open."""
    setup_drive = test_drive_session.DriveSessionTests.setup_drive
    turn = test_drive_session.DriveSessionTests.turn

    def drive_brain(self, *replies):
        self.setup_drive(drive(step('move', meters=12.), say_before=None))
        self.brain = ScriptBrain(*replies)
        self.session.brain = self.brain

    def test_a_refused_move_may_be_replanned_once_and_runs(self):
        self.drive_brain(drive(step('move', meters=12.), say_before=None),
                         ToolCall('again', 'drive_base', {'say_before': 'Half a metre, then.', 'steps': [step('move', meters=.5)],
                                  'purpose': 'what fits', 'look_after': None, 'say_after': None}))
        delivery = self.turn('Drive twelve metres forward.')
        self.assertEqual(delivery.completed_text, ['Half a metre, then.'])
        self.assertAlmostEqual(self.fake.x, .5, places=2)
        self.assertEqual(len(self.brain.snapshots), 2)
        self.assertTrue(any(r['event'] == 'drive_replanned' for r in self.state.trace.rows))

    def test_a_second_refusal_is_explained_without_another_plan(self):
        self.drive_brain(drive(step('move', meters=12.), say_before=None),
                         ToolCall('again', 'drive_base', {'say_before': None, 'steps': [step('move', meters=11.)],
                                  'purpose': 'still too far', 'look_after': None, 'say_after': None}),
                         'That is still farther than I can go.')
        delivery = self.turn('Drive twelve metres forward.')
        self.assertEqual(delivery.completed_text, ['That is still farther than I can go.'])
        self.assertEqual(self.fake.x, 0)
        self.assertEqual(len(self.brain.snapshots), 3)
        self.assertFalse(self.brain.snapshots[-1].tools_enabled)


class FailedLookTests(MemoryFixture):
    def test_a_look_that_took_no_picture_is_answered_as_failed(self):
        from r3x.session import LOOK_FAILED
        class View:
            directions = ('current',)
            def close(self): pass
            def execute(self, request, cancel, valid):
                return {'request_id': request.request_id, 'status': 'failed', 'stage': 'capture',
                        'reason': 'CameraUnavailable'}, ()
        brain = ScriptBrain(ToolCall('look', 'inspect_view', {'say_first': 'Let me look.', 'direction': 'current',
                                     'question': 'Is the path clear?', 'next_opportunity': None}),
                            'I could not look just now.')
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, inspection=View())
        session = Session('s', brain, output, self.state.trace, self.state); self.addCleanup(session.close)
        self.enroll('Sam')
        cap = Capture('cap', 's', bytes(96000), 96, 99, 100, voiced_intervals=((0., 3.),))
        result = session.process_turn(cap, Transcript('Is anything in front of you?', 0, True), 0, observation(), ())
        self.assertEqual(result.completed_text, ['Let me look.', 'I could not look just now.'])
        notes = [m['content'] for m in brain.snapshots[-1].messages if m.get('role') == 'developer']
        self.assertIn(LOOK_FAILED, notes)
