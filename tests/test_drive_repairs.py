"""October 16 evening run (03bc4e1f) drive repairs; scripted providers, simulated base only."""
import threading
import time

from r3x.contracts import Cancellation, ToolCall
from tests import test_drive_session
from tests.test_drive_session import drive_call, step
from tests.test_memory_core import MemoryFixture


class Script:
    def __init__(self, first, *later):
        self.first, self.later, self.snapshots = first, list(later), []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        yield (self.first(snapshot) if callable(self.first) else self.first) if len(self.snapshots) == 1 else self.later.pop(0)


class DriveRepairTests(MemoryFixture):
    setup_drive = test_drive_session.DriveSessionTests.setup_drive
    turn = test_drive_session.DriveSessionTests.turn
    rows = test_drive_session.DriveSessionTests.rows

    def script(self, first, *later):
        self.setup_drive(drive_call(step('move', meters=.5)))
        self.brain = Script(first, *later)
        self.session.brain = self.brain

    def test_the_arrival_line_is_written_on_arrival_not_before_setting_off(self):
        def plan(snapshot):
            target = next(t['target_id'] for t in snapshot.context['drive']['targets'] if t.get('label') == 'doorway')
            return ToolCall('go', 'drive_base', {'say_before': 'On my way.', 'steps': [step('approach', target=target)],
                                                 'purpose': 'to the doorway', 'look_after': None,
                                                 'say_after': "I'm heading toward the doorway."})
        self.script(plan, 'Here I am at the doorway.')
        self.world.objects[:] = [('object-door', 'doorway', 2., 0.)]
        test_drive_session.settle()
        delivery = self.turn('Come toward the doorway.')
        self.assertEqual(delivery.completed_text, ['On my way.', 'Here I am at the doorway.'])
        notes = [m['content'] for m in self.brain.snapshots[-1].messages if m.get('role') == 'developer']
        self.assertTrue(any('You have arrived' in n for n in notes))

    def test_a_plain_move_still_says_its_line_without_another_call(self):
        self.script(drive_call(step('move', meters=.5), text='Here I am!', speak='after_motion'))
        delivery = self.turn('Roll forward a bit.')
        self.assertEqual(delivery.completed_text, ['Here I am!'])
        self.assertEqual(len(self.brain.snapshots), 1)

    def test_an_empty_acknowledgment_does_not_reject_the_move(self):
        self.script(ToolCall('t', 'drive_base', {'say_before': '', 'steps': [step('move', meters=.5)], 'purpose': 'asked',
                                                 'look_after': None, 'say_after': '  '}),
                    'Rolled forward half a metre.')
        delivery = self.turn('Roll forward half a metre.')
        self.assertAlmostEqual(self.fake.x, .5, places=2)
        self.assertFalse(self.rows('drive_rejected'))
        self.assertEqual(delivery.completed_text, ['Rolled forward half a metre.'])   # The wordless move is answered.

    def test_his_own_plans_never_back_up_but_a_request_can(self):
        self.setup_drive(drive_call(step('move', meters=.5)))
        back = ToolCall('b', 'drive_base', {'say_before': None, 'steps': [step('move', meters=-.4)], 'purpose': 'give room',
                                            'look_after': None, 'say_after': None})
        own = {'drive': self.drive.context('self_initiated'), 'state_revision': self.store.revision, 'source_turn_id': 't'}
        with self.assertRaisesRegex(ValueError, 'SelfInitiatedReverse'):
            self.state.propose_drive(back, own, Cancellation())
        asked = {'drive': self.drive.context('requested'), 'state_revision': self.store.revision, 'source_turn_id': 't'}
        self.assertEqual(self.state.propose_drive(back, asked, Cancellation()).steps[0].meters, -.4)

    def test_a_stopped_step_is_logged_with_what_the_sensors_saw(self):
        self.setup_drive(drive_call(step('move', meters=1.5)))
        worker = threading.Thread(target=lambda: self.turn('Drive forward.'))
        worker.start()
        deadline = time.monotonic() + 3
        while not any(c['cmd'] == 'move' for c in self.fake.commands) and time.monotonic() < deadline:
            time.sleep(.005)
        self.session.cancel()
        worker.join(5)
        row = next(r for r in self.rows('drive_step') if r['status'] == 'cancelled')
        self.assertEqual(row['reason'], 'stop_requested')
        self.assertIn('rear_m', row['clearance_m'])
        self.assertIn('seconds', row)
