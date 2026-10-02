"""Model-selected drive plans through the production Session, state and output owners."""
import json
import threading
import time
import unittest
from uuid import uuid4

from r3x.contracts import Capture, ProviderFailure, ToolCall, Transcript
from r3x.inspection import CurrentView, InspectionRequest
from r3x.output import OutputOwner
from r3x.proactive import Arrivals, Opportunity
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.scene import SceneImage
from r3x.semantics import grounded_tools, validate
from r3x.session import Session
from r3x.settings import Settings
from r3x.hardware_ownership import HardwareLocks
from r3x.servo_poses import NAMED_POSES, REST, PARK, base_lanes, LIMITS as SERVO_LIMITS
from tests.test_drive import base_setup, settle
from tests.test_memory_core import MemoryFixture
from tests.test_proactive import face


def step(action, degrees=None, meters=None, target=None, stop_at_m=None):
    """Tests write signed robot-frame values; the tool carries direction words."""
    direction = None
    if degrees is not None:
        direction, degrees = ('left' if degrees > 0 else 'right'), abs(degrees)
    if meters is not None:
        direction, meters = ('forward' if meters > 0 else 'backward'), abs(meters)
    return {'action': action, 'direction': direction, 'degrees': degrees, 'meters': meters,
            'target': target, 'stop_at_m': stop_at_m, 'description': None}


def drive_call(*steps, text=None, speak='before_motion', look_after=None, purpose='asked to move'):
    """Tests write a line and when it plays; the tool has separate before/after slots."""
    return ToolCall('drive', 'drive_base', {'steps': list(steps), 'purpose': purpose, 'look_after': look_after,
                                             'say_before': text if speak == 'before_motion' else None,
                                             'say_after': text if speak == 'after_motion' else None})


class DriveBrain:
    """First call selects the tool; a continuation answers from the receipt."""
    def __init__(self, call, followup='I only made it part of the way.'):
        self.call, self.followup, self.snapshots = call, followup, []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        yield self.followup if snapshot.continuation else self.call


class FakeScene:
    def __init__(self, clock):
        self.clock, self.stale = clock, False

    def snapshot(self, include_offered=False):
        if hasattr(self.clock, 'wait'):
            self.clock.wait(.1)   # A new camera frame arrives as time passes.
        at = self.clock() - (5. if self.stale else 0.)
        return 'scene', (SceneImage('frame-'+str(at), at, 'data:image/png;base64,after'),)


class DriveSessionTests(MemoryFixture):
    def setup_drive(self, call, followup='I only made it part of the way.', inspection=False):
        self.clock, self.fake, self.drive, self.world = base_setup(self)
        self.state.trace.clock = self.clock   # One clock for plan expiry and receipts.
        self.drive.trace = self.state.trace
        self.drive.start()
        self.addCleanup(lambda: self.drive.state == 'closed' or self.drive.close())
        self.brain = DriveBrain(call, followup)
        view = None
        if inspection:
            view = CurrentView(FakeScene(self.clock), clock=self.clock)
            view.start()
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, drive=self.drive, inspection=view)
        self.session = Session('session', self.brain, self.output, self.state.trace, self.state)
        self.addCleanup(self.session.close)

    def turn(self, text='Roll forward a bit.'):
        return self.session.process_turn(Capture(uuid4().hex, 'session', bytes(1024), 98, 99, 99.6),
                                         Transcript(text, -.01, True), self.session.revision)

    def rows(self, event):
        return [r for r in self.state.trace.rows if r['event'] == event]

    def test_requested_move_speaks_first_then_drives_and_records_the_receipt(self):
        self.setup_drive(drive_call(step('move', meters=.5), text='Rolling forward!'))
        delivery = self.turn()
        self.assertEqual(delivery.status, 'completed')
        self.assertEqual(delivery.completed_text, ['Rolling forward!'])
        self.assertAlmostEqual(self.fake.x, .5, places=2)
        self.assertEqual(len(self.brain.snapshots), 1)  # No extra model call on success.
        offered = self.brain.snapshots[0].context['drive']
        self.assertTrue(offered['available'])
        self.assertEqual(offered['limits']['step_m'], 1.5)
        events = [r['event'] for r in self.state.trace.rows]
        self.assertLess(events.index('phrase_completed'), events.index('drive_receipt'))
        notes = [m['content'] for m in self.session.history.messages() if m.get('role') == 'developer']
        self.assertTrue(any(n.startswith('Drive controller receipt') and '"completed"' in n for n in notes))

    def test_after_motion_text_waits_for_completion_or_the_model_explains_the_receipt(self):
        self.setup_drive(drive_call(step('move', meters=1.), text='Here I am!', speak='after_motion'),
                         followup='Something stopped me after a few inches.')
        self.fake.block_after = .2
        delivery = self.turn()
        self.assertEqual(delivery.completed_text, ['Something stopped me after a few inches.'])
        followup = self.brain.snapshots[-1]
        output = json.loads(followup.continuation[-1]['output'])
        self.assertEqual(output['drive']['status'], 'partial')
        self.assertEqual(output['drive']['steps'][0]['status'], 'blocked')
        self.assertFalse(followup.tools_enabled)

    def test_rejected_plan_never_moves_and_is_explained(self):
        self.setup_drive(drive_call(step('move', meters=12.)), followup='That is farther than I can go in one step.')
        delivery = self.turn('Drive twelve metres forward.')
        self.assertEqual(delivery.completed_text, ['That is farther than I can go in one step.'])
        output = json.loads(self.brain.snapshots[-1].continuation[-1]['output'])
        self.assertEqual((output['outcome'], output['reason']), ('rejected', 'DriveMoveOutOfRange'))
        self.assertFalse(any(c['cmd'] in {'move', 'turn', 'drive'} for c in self.fake.commands))

    def test_unoffered_target_is_rejected(self):
        self.setup_drive(drive_call(step('approach', target='face-invented')), followup='I cannot see you.')
        self.turn('Come here.')
        output = json.loads(self.brain.snapshots[-1].continuation[-1]['output'])
        self.assertEqual(output['reason'], 'DriveTargetNotOffered')

    def test_stacked_request_runs_steps_in_order(self):
        self.setup_drive(drive_call(step('turn', degrees=180.), step('move', meters=-.4), step('turn', degrees=-90.),
                                    text='Spin, back up, and a quarter turn.'))
        delivery = self.turn('Spin around, back up a bit, then turn right.')
        self.assertEqual(delivery.status, 'completed')
        commands = [c['cmd'] for c in self.fake.commands if c['cmd'] in {'turn', 'move'}]
        self.assertEqual(commands[:2], ['turn', 'move'])
        self.assertEqual(commands[-1], 'turn')
        receipt = self.rows('drive_result')[-1]
        self.assertEqual([s['action'] for s in receipt['steps']], ['turn', 'move', 'turn'])

    def test_self_initiated_plans_use_smaller_limits_and_fail_silently(self):
        self.setup_drive(drive_call(step('move', meters=2.), text='Coming over!', purpose='greet the arrival'))
        now = self.clock()
        self.begin("Hello again.")
        self.session._latest_speech = 50.
        event = Opportunity(uuid4().hex, 'session', now, 'face', None, (face(now),), 50., False)
        delivery = self.session.process_opportunity(event, self.session.revision, lambda: True)
        self.assertEqual(self.brain.snapshots[0].context['drive']['origin'], 'self_initiated')
        self.assertEqual(delivery.status, 'failed')
        self.assertEqual(delivery.error, 'DriveMoveOutOfRange')
        self.assertFalse(any(c['cmd'] == 'move' for c in self.fake.commands))
        self.assertIn('drive_base', [d['name'] for d in grounded_tools(self.brain.snapshots[0].context)])

    def test_look_after_answers_from_a_frame_captured_after_the_base_stopped(self):
        self.setup_drive(drive_call(step('move', meters=.3), look_after='What is on the floor?'),
                         followup='A sock, it seems.', inspection=True)
        delivery = self.turn('Go look at that thing on the floor.')
        self.assertEqual(delivery.completed_text, ['A sock, it seems.'])
        followup = self.brain.snapshots[-1]
        self.assertTrue(followup.images)
        inspection = followup.context['inspection']
        self.assertEqual(inspection['motion_status'], 'base_motion_finished')
        receipt = self.rows('drive_result')[-1]
        self.assertGreaterEqual(inspection['captured_at'], receipt['finished_at'])

    def test_direction_words_set_robot_frame_signs_and_mismatches_are_rejected(self):
        self.setup_drive(drive_call(step('turn', degrees=-45.), step('move', meters=-.3)))
        self.turn('Turn right and back up a little.')
        commands = [c for c in self.fake.commands if c['cmd'] in {'turn', 'move'}]
        self.assertLess(commands[0]['deg'], 0)
        self.assertLess(commands[-1]['dist'], 0)
        wrong = {**step('turn', degrees=45.), 'direction': 'forward'}
        from r3x.contracts import Cancellation
        context = self.brain.snapshots[0].context
        with self.assertRaisesRegex(ValueError, 'DriveDirectionMismatch'):
            self.state.propose_drive(drive_call(wrong), {**context, 'state_revision': self.store.revision}, Cancellation())

    def test_long_move_runs_as_checked_legs_within_the_plan_total(self):
        self.setup_drive(drive_call(step('move', meters=2.5)))
        self.turn('Drive two and a half metres forward.')
        moves = [c for c in self.fake.commands if c['cmd'] == 'move']
        self.assertEqual([m['dist'] for m in moves], [1.5, 1.])
        self.assertAlmostEqual(self.fake.x, 2.5, places=2)
        self.assertEqual(len(self.rows('drive_result')[-1]['steps'][0]['legs']), 2)

    def blocked_dog(self, planner):
        self.setup_drive(drive_call(step('approach', target='object-dog'), text='Let me see.'))
        self.world.objects[:] = [('object-box', 'box', .85, 0.), ('object-dog', 'dog', 2.2, .1)]
        self.session.route_planner = planner
        settle()

    def test_blocked_direct_approach_gets_one_route_plan_while_the_line_plays(self):
        planner = FakePlanner({'steps': [step('turn', degrees=45.), step('move', meters=.8), step('face', target='object-dog')]})
        self.blocked_dog(planner)
        delivery = self.turn('Go look at the dog.')
        self.assertEqual(planner.calls, 1)
        self.assertEqual(planner.request, 'Go look at the dog.')
        self.assertEqual(delivery.completed_text[0], 'Let me see.')
        receipt = self.rows('drive_result')[-1]
        self.assertEqual([s['action'] for s in receipt['steps']][:2], ['turn', 'move'])
        self.assertTrue(self.rows('drive_route_planned'))
        planning = [r for r in self.rows('model_request') if r.get('purpose') == 'drive_route_planning']
        self.assertEqual(planning[0]['blocked_targets'], ['object-dog'])

    def test_route_planner_failure_keeps_the_original_plan(self):
        planner = FakePlanner(None)
        self.blocked_dog(planner)
        self.turn('Go look at the dog.')
        self.assertEqual(self.rows('drive_route_failed')[-1]['reason'], 'OpenAIReadTimeout')
        self.assertEqual(self.rows('drive_result')[-1]['steps'][0]['action'], 'approach')

    def test_clear_paths_never_call_the_route_planner(self):
        planner = FakePlanner({'steps': []})
        self.setup_drive(drive_call(step('move', meters=.3)))
        self.session.route_planner = planner
        self.turn()
        self.assertEqual(planner.calls, 0)

    def test_turn_look_then_one_follow_up_plan_from_the_fresh_view(self):
        calls = []
        class Brain:
            def __init__(inner): inner.snapshots = []
            def reply(inner, snapshot, cancel):
                inner.snapshots.append(snapshot)
                calls.append(snapshot)
                if not snapshot.continuation:
                    yield drive_call(step('turn', degrees=90.), look_after='Is the clock photo here?', text='Looking left.')
                elif len(calls) == 2:
                    target = next(t['target_id'] for t in snapshot.context['drive']['targets'] if t.get('label') == 'clock')
                    yield ToolCall('next', 'drive_base', {'steps': [step('approach', target=target)], 'purpose': 'go to the photo',
                                                          'look_after': None, 'say_before': None, 'say_after': 'There it is!'})
                else:
                    yield 'Done.'
        self.setup_drive(drive_call(step('turn', degrees=90.)), inspection=True)
        self.session.brain = Brain()
        self.world.objects[:] = [('object-photo', 'clock', 0., 2.)]   # Directly to Rex's left.
        settle()
        delivery = self.turn('Turn left and find the photo of a clock.')
        self.assertEqual(delivery.completed_text, ['Looking left.', 'There it is!'])
        tools = {d['name'] for d in grounded_tools(calls[1].context)}
        self.assertEqual(tools, {'describe_view', 'drive_base'})
        results = self.rows('drive_result')
        self.assertEqual([r['steps'][0]['action'] for r in results], ['turn', 'approach'])
        self.assertEqual(results[-1]['status'], 'completed')

    def test_stop_during_driving_halts_the_wheels(self):
        self.setup_drive(drive_call(step('move', meters=1.5)))
        result = {}
        worker = threading.Thread(target=lambda: result.update(d=self.turn('Drive forward.')))
        worker.start()
        deadline = time.monotonic() + 3
        while not any(c['cmd'] == 'move' for c in self.fake.commands) and time.monotonic() < deadline:
            time.sleep(.005)
        self.session.cancel()
        worker.join(5)
        self.assertEqual(result['d'].status, 'interrupted')
        after = self.fake.commands[[c['cmd'] for c in self.fake.commands].index('move'):]
        self.assertIn('stop', [c['cmd'] for c in after])
        self.assertLess(self.fake.x, 1.4)
        notes = [m['content'] for m in self.session.history.messages() if m.get('role') == 'developer']
        self.assertTrue(any('cancelled' in n for n in notes if 'Drive controller receipt' in n))

    def test_unavailable_drive_is_offered_as_a_reason_not_a_tool_failure(self):
        self.setup_drive(drive_call(step('turn', degrees=90.)), followup='I am plugged in, so I will stay put.')
        self.fake.charging = True
        settle()
        delivery = self.turn('Turn left.')
        self.assertEqual(self.brain.snapshots[0].context['drive']['unavailable_reason'], 'charging')
        self.assertEqual(delivery.completed_text, ['I am plugged in, so I will stay put.'])
        self.assertFalse(any(c['cmd'] == 'turn' for c in self.fake.commands))


class DriveRuntimeTests(MemoryFixture):
    def runtime_for(self, session, faces):
        from types import SimpleNamespace
        from r3x.runtime import VoiceRuntime
        from tests.test_agency_plan import Planner
        session.agency_planner = Planner()
        runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, self.state.trace,
                               perception=SimpleNamespace(camera=SimpleNamespace(latest=lambda now: faces())))
        runtime.started_at = 0
        return runtime

    def test_radar_evidence_reaches_planner_without_autonomous_wheel_tools(self):
        from types import SimpleNamespace
        from r3x.output import TextOutput
        from tests.test_voice_core import Brain
        brain = Brain('A rhythm worth considering.')
        output = TextOutput(self.state.trace)
        body = {'target_id': 'radar:+150', 'kind': 'radar_body', 'bearing_deg': 150., 'range_m': 2.,
                'moving': True, 'where': 'behind on the left'}
        output.drive = SimpleNamespace(context=lambda origin, speaker_direction=None: {
            'available': True, 'origin': origin, 'targets': [body], 'limits': {}})
        session = Session('session', brain, output, self.state.trace, self.state)
        self.addCleanup(session.close)
        runtime = self.runtime_for(session, lambda: ())
        runtime.drive = SimpleNamespace(radar=SimpleNamespace(bodies=lambda: [dict(body, frames=4, confidence=.7)]),
                                        availability=lambda now: (True, None), moving=threading.Event(),
                                        motion_risk=lambda: False)
        self.state.trace.at = 200.
        runtime.audio_seen_at = 200.
        runtime.proactive_tick()
        self.assertEqual(brain.calls, 1)
        context = brain.snapshots[0].context
        self.assertEqual(context['trigger'], 'reconsideration')
        self.assertIn('radar:+150',session.agency_planner.requests[0]['evidence'])
        self.assertEqual({d['name'] for d in grounded_tools(context)}, {'respond_with_intent','revise_agency_plan'})
        self.state.trace.at, runtime.audio_seen_at = 230., 230.
        session.proactive_hold_until = float('-inf')
        runtime.proactive_tick()
        self.assertEqual(brain.calls, 2)   # Fresh plan advancement, not a radar polling timer.



class FakePlanner:
    class capability:
        model = 'planner-test'

    def __init__(self, route):
        self.route, self.calls, self.request = route, 0, None

    def available(self):
        return True

    def plan(self, request, draft, drive, cancel):
        self.calls += 1
        self.request = request
        assert any(t.get('path', {}).get('direct_approach') == 'blocked_early' for t in drive['targets'])
        if self.route is None:
            raise ProviderFailure('OpenAIReadTimeout')
        return self.route, {'input_tokens': 1}


class WheelsDownTests(MemoryFixture):
    def test_configured_wheels_that_failed_to_start_are_explained_not_denied(self):
        brain = DriveBrain(drive_call(step('move', meters=1.5)), followup='My wheels are not connected right now.')
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace)
        output.drive_unavailable = 'wheel_controller_not_connected:DriveConfigUnacknowledged'
        session = Session('session', brain, output, self.state.trace, self.state)
        self.addCleanup(session.close)
        delivery = session.process_turn(Capture(uuid4().hex, 'session', bytes(1024), 98, 99, 99.6),
                                        Transcript('Move forward five feet.', -.01, True), session.revision)
        drive = brain.snapshots[0].context['drive']
        self.assertFalse(drive['available'])
        self.assertIn('not_connected', drive['unavailable_reason'])
        output_json = json.loads(brain.snapshots[-1].continuation[-1]['output'])
        self.assertTrue(output_json['reason'].startswith('DriveUnavailable:wheel_controller_not_connected'))
        self.assertEqual(delivery.completed_text, ['My wheels are not connected right now.'])


class DriveContractTests(unittest.TestCase):
    def context(self, **extra):
        return {'source_turn_id': 't', 'drive': {'available': True, 'origin': 'requested', 'targets': [
            {'target_id': 'face-1', 'kind': 'face'}, {'target_id': 'radar:+120', 'kind': 'radar_body'}]}, **extra}

    def test_targets_are_grounded_and_look_after_needs_inspection(self):
        tools = {d['name']: d for d in grounded_tools(self.context())}
        target = tools['drive_base']['parameters']['properties']['steps']['items']['properties']['target']
        self.assertEqual(target['enum'], ['face-1', 'radar:+120', None])
        self.assertEqual(tools['drive_base']['parameters']['properties']['look_after']['enum'], [None])
        self.assertNotIn('drive_base', {d['name'] for d in grounded_tools({'source_turn_id': 't'})})
        unseen = {d['name'] for d in grounded_tools(self.context(trigger='unseen_presence'))}
        self.assertEqual(unseen, {'drive_base','remain_silent'})

    def test_step_syntax_is_validated(self):
        validate('drive_base', drive_call(step('turn', degrees=45.)).arguments)
        for bad in ({**step('turn', degrees=10.), 'action': 'teleport'},
                    {**step('turn', degrees=10.), 'degrees': float('nan')},
                    {**step('turn', degrees=10.), 'direction': 'up'},
                    {'action': 'turn', 'degrees': 1}):
            with self.assertRaises(ValueError):
                validate('drive_base', drive_call(bad).arguments)

    def test_settings_require_distinct_explicit_ports_and_radar_needs_wheels(self):
        Settings(drive_port='/dev/cu.usbserial-110', radar_port='/dev/cu.usbmodem2301')
        for values in ({'radar_port': '/dev/cu.usbmodem2301'}, {'drive_port': 'usbserial-110'},
                       {'drive_port': '/dev/cu.a', 'servo_port': '/dev/cu.a'}):
            with self.assertRaises(ValueError):
                Settings(**values)

    def test_hardware_leases_cover_the_base_and_radar(self):
        locks = HardwareLocks()
        self.assertTrue(str(locks.lease('drive_base').path).endswith('.v3-drive_base'))
        self.assertTrue(str(locks.lease('radar').path).endswith('.v3-radar'))

    def test_current_view_rejects_frames_from_before_motion(self):
        from r3x.contracts import Cancellation
        clock = lambda: 200.
        scene = FakeScene(clock)
        scene.stale = True
        view = CurrentView(scene, clock=clock)
        view.start()
        data, images = view.execute(InspectionRequest('r', 'current', 'q', 0, 200., captured_after=199.), Cancellation(), lambda: True)
        self.assertEqual((data['reason'], images), ('FreshCurrentViewUnavailable', ()))

    def test_faces_reframed_by_base_motion_are_not_new_arrivals(self):
        arrivals = Arrivals()
        arrivals.note_base_motion(10.)
        for i in range(10):
            at = 11 + i*.5
            arrivals.observe([face(at, None, 'new-track')], at)
        self.assertIsNone(arrivals.take('s', 16, float('-inf')))
        investigating = Arrivals()
        investigating.note_base_motion(10., allow_new=True)
        for i in range(10):
            at = 11 + i*.5
            investigating.observe([face(at, None, 'new-track')], at)
        self.assertIsNotNone(investigating.take('s', 16, float('-inf')))

    def test_base_lanes_route_every_named_pose_to_park_and_back(self):
        from r3x.servo_poses import HEAD_PARK
        base = {ch: (low + high)//2 for ch, (low, high) in SERVO_LIMITS.items()}
        for name, pose in NAMED_POSES.items():
            current = {**base, **dict(REST), **dict(pose)}
            lanes = base_lanes('base_park', current)
            throttle = [lane for lane in lanes if any(ch in {8, 9, 10} for ch, _ in lane[0].targets)]
            final = dict(throttle[0][-1].targets) if throttle else {ch: current[ch] for ch in (8, 9, 10)}
            self.assertEqual(final, dict(PARK), name)
        arms = [dict(lane[-1].targets) for lane in base_lanes('base_park', {**base, **dict(REST)})
                if any(ch in {4, 6} for ch, _ in lane[0].targets)]
        self.assertEqual(arms, [{4: 7424, 6: 8000}])   # Hero elbow fully up, poker arm highest.
        from r3x.servos import BASE_HELD
        self.assertTrue({4, 6} <= BASE_HELD)            # Held until the drive finishes.
        parked = {**base, **dict(PARK)}
        back = base_lanes('base_release', parked)
        self.assertEqual(dict(back[0][-1].targets), dict(REST))

    def test_startup_notice_names_the_wheels(self):
        from r3x.providers import LifecycleResult
        from r3x.startup_notice import notice_text
        text = notice_text(Settings(), [LifecycleResult('drive_base', 'start', 'failed', 'ProviderFailure')])
        self.assertIn('wheel controller', text)

    def test_general_opportunities_need_no_face_but_lulls_still_need_a_source(self):
        from dataclasses import replace
        import tempfile
        from pathlib import Path
        from r3x.memory import Memory
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        from tests.test_voice_core import Trace
        with tempfile.TemporaryDirectory() as temp:
            store = Memory(Path(temp).resolve()/'core.sqlite3')
            store.start()
            try:
                state = CoreState(store, SpeakerTracker('synthetic:3'), Trace())
                unseen = Opportunity('e1', 's', 100., None, None, (), float('-inf'), False, 'unseen_presence')
                self.assertEqual(state.opportunity_context(unseen)['opportunity']['audience'], 'nobody_visible')
                lull = Opportunity('e2', 's', 100., None, None, (), float('-inf'), False, 'conversation_lull')
                with self.assertRaisesRegex(ValueError, 'LullRequiresConversationOrActiveIntent'):
                    state.opportunity_context(lull)
                ambient = replace(unseen, kind='ambient')
                self.assertEqual(state.opportunity_context(ambient)['faces'], [])
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
