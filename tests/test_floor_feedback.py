"""September 27 floor-test repairs: clipped onsets, stop words, gaze and seated approach."""
import math
import unittest

from r3x.capture import Endpoint, Frame
from r3x.contracts import Cancellation
from r3x.drive import DrivePlan, DriveStep
from r3x.drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
from r3x.gaze import addressee_neck
from r3x.servo_poses import LIMITS, talking_lanes
from r3x.session import STOP_COMMANDS
from tests.test_drive import base_setup, settle


class OnsetTests(unittest.TestCase):
    def test_one_second_before_detected_speech_is_kept(self):
        endpoint = Endpoint('s', silence=.2)
        step = 512/16000
        at = 0.
        for _ in range(40):   # A soft "You can" the detector does not yet call speech.
            endpoint.push(Frame(b'\x01\x00'*512, at, at+step), False)
            at += step
        endpoint.push(Frame(b'\x02\x00'*512, at, at+step), True)
        capture = None
        while capture is None:
            at += step
            capture = endpoint.push(Frame(b'\x00\x00'*512, at, at+step), False)
        self.assertAlmostEqual(capture.speech_ended_at - capture.started_at, 33*step, places=6)
        self.assertGreaterEqual(capture.speech_ended_at - capture.started_at, 1.)


class StopWordTests(unittest.TestCase):
    def test_shut_up_and_stop_talking_are_explicit_stops_but_discussion_is_not(self):
        for text in ('Shut up.', 'Stop talking!', 'Rex, be quiet.', 'stop'):
            self.assertIn(text.strip().lower().rstrip('.!?').replace(',', ''), STOP_COMMANDS)
        self.assertNotIn('please stop talking about the weather', STOP_COMMANDS)


def face(x, width, identity=None):
    return {'box': [x - width/2, .3, width, width], 'identity': identity}


class GazeTests(unittest.TestCase):
    def test_looks_at_the_attributed_speaker_before_the_largest_face(self):
        faces = [face(.2, .08, 'bret'), face(.8, .15, None)]   # Bret 27 degrees left; bigger stranger right.
        neck = addressee_neck(faces, 0., 'bret')
        self.assertAlmostEqual((NECK_CENTER - neck)/NECK_PULSES_PER_DEG, 27., delta=.5)
        self.assertGreater(addressee_neck(faces, 0., None), NECK_CENTER)   # Largest face is to the right.

    def test_voice_direction_when_no_speaker_face_and_limits_when_nothing_visible(self):
        neck = addressee_neck([], 0., 'bret', {'status': 'observed', 'bearing_deg': -30.})
        self.assertAlmostEqual((NECK_CENTER - neck)/NECK_PULSES_PER_DEG, -30., delta=.5)
        self.assertIsNone(addressee_neck([], 0., None, {'status': 'ambiguous', 'bearing_deg': None}))
        far = addressee_neck([], 0., None, {'status': 'observed', 'bearing_deg': 170.})
        self.assertAlmostEqual((NECK_CENTER - far)/NECK_PULSES_PER_DEG, 40., delta=.5)

    def test_already_turned_neck_is_added_to_the_camera_bearing(self):
        neck = addressee_neck([face(.5, .1)], 20.)
        self.assertAlmostEqual((NECK_CENTER - neck)/NECK_PULSES_PER_DEG, 20., delta=.5)

    def test_talking_sway_centres_on_the_gaze_anchor(self):
        current = {ch: (lo+hi)//2 for ch, (lo, hi) in LIMITS.items()}
        current.update({8: 4200, 9: 7720, 10: 5120})
        lanes = talking_lanes(current, held=frozenset({8, 9, 10}), neck=4500)
        head = lanes[0]
        self.assertEqual([dict(b.targets)[0] for b in head], [5020, 3980, 4500])


class SeatedApproachTests(unittest.TestCase):
    def test_knees_nearer_than_the_face_do_not_stop_the_approach_early(self):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        world.people.append(('face-1', 'bret', 2.2, 0.))
        world.objects.append(('knees', 'person', 1.9, 0.))   # Seated: knees about 0.3 m in front of the face.
        settle()
        targets = {'face-1': {'target_id': 'face-1', 'kind': 'face', 'person_id': 'bret'}}
        plan = DrivePlan('p', (DriveStep('approach', target='face-1'),), 'requested', 'come here', targets, clock()+60)
        receipt = drive.execute(plan, Cancellation())
        distance = math.hypot(2.2 - fake.x, fake.y)
        self.assertLess(distance, 1.05, receipt)   # V2-style radial personal distance stopped at ~1.4 m.
        self.assertGreater(world.front_obstacle(), .15)
        self.assertIn('clearance_at_stop_m', receipt.steps[0])


if __name__ == '__main__':
    unittest.main()


class ReacquisitionTests(unittest.TestCase):
    def start(self):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        return clock, fake, drive, world

    def test_restarted_object_track_is_refound_by_label_near_its_expected_bearing(self):
        clock, fake, drive, world = self.start()
        world.objects[:] = [('object-new-id', 'clock', 2., .8)]    # ~22 degrees left; the tracker restarted.
        settle()
        offered = {'object-old-id': {'target_id': 'object-old-id', 'kind': 'object', 'label': 'clock', 'bearing_deg': 22}}
        plan = DrivePlan('p', (DriveStep('face', target='object-old-id'),), 'requested', 'look', offered, clock()+60)
        receipt = drive.execute(plan, Cancellation())
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertAlmostEqual(fake.theta, 22., delta=6.)

    def test_same_label_on_the_other_side_is_not_substituted(self):
        clock, fake, drive, world = self.start()
        world.objects[:] = [('object-right', 'clock', 2., -1.2)]   # The only clock is ~31 degrees right.
        settle()
        offered = {'object-left': {'target_id': 'object-left', 'kind': 'object', 'label': 'clock', 'bearing_deg': 40}}
        plan = DrivePlan('p', (DriveStep('approach', target='object-left'),), 'requested', 'find photo', offered, clock()+60)
        receipt = drive.execute(plan, Cancellation())
        self.assertNotEqual(receipt.status, 'completed')
        self.assertEqual(receipt.steps[0]['reason'], 'target_not_found_where_it_was_seen')
        self.assertGreater(fake.theta, 20)   # It turned left toward where it was seen, not right.
        self.assertEqual(fake.x, 0.)

    def test_unseen_target_is_looked_for_where_it_was_offered(self):
        clock, fake, drive, world = self.start()
        world.objects[:] = [('object-plant', 'potted plant', .9, 1.6)]   # ~60 degrees left, out of frame.
        settle()
        offered = {'object-plant': {'target_id': 'object-plant', 'kind': 'object', 'label': 'potted plant', 'bearing_deg': 60}}
        plan = DrivePlan('p', (DriveStep('face', target='object-plant'),), 'requested', 'look', offered, clock()+60)
        receipt = drive.execute(plan, Cancellation())
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertAlmostEqual(fake.theta, 60., delta=8.)


from tests.test_memory_core import MemoryFixture, observation


class DirectionUnderMotionTests(MemoryFixture):
    def test_motion_voids_voice_identity_but_keeps_the_microphone_bearing(self):
        from dataclasses import replace
        from uuid import uuid4
        from r3x.contracts import Capture, Transcript
        bearing = {'status': 'observed', 'bearing_deg': -132., 'samples': 17, 'reason': 'dominant_speech_energy_beam'}
        voice = replace(observation(), direction=bearing)
        capture = Capture(uuid4().hex, 'session', b'\x00'*64000, 110, 112, 112, motion_risk=True)
        self.state.begin(uuid4().hex, capture, Transcript("I'm over here.", -.01, True), voice)
        context = self.state.context()
        self.assertEqual(context['speaker']['direction'], bearing)
        self.assertIsNone(context['speaker']['person_id'])   # Still no identity from moving-robot audio.


class MotionFlagTests(unittest.TestCase):
    def speak(self, motion_pre, motion_voiced):
        endpoint = Endpoint('s', silence=.2)
        step, at = 512/16000, 0.
        for i in range(20):
            endpoint.push(Frame(b'\x01\x00'*512, at, at+step, motion_risk=motion_pre), False)
            at += step
        for i in range(20):
            endpoint.push(Frame(b'\x02\x00'*512, at, at+step, motion_risk=i < motion_voiced), True)
            at += step
        capture = None
        while capture is None:
            capture = endpoint.push(Frame(b'\x00\x00'*512, at, at+step), False)
            at += step
        return capture

    def test_motion_in_the_pre_roll_or_one_beat_does_not_void_identity(self):
        self.assertFalse(self.speak(motion_pre=True, motion_voiced=2).motion_risk)

    def test_motion_through_the_speech_still_voids_identity(self):
        self.assertTrue(self.speak(motion_pre=False, motion_voiced=12).motion_risk)


class MotionConfidenceTests(unittest.TestCase):
    def observe(self, similarity, motion):
        from r3x.speakers import SpeakerTracker, VoiceObservation
        tracker = SpeakerTracker('synthetic:3')
        vector = (similarity, math.sqrt(1 - similarity**2), 0.)
        voice = VoiceObservation('synthetic:3', vector, (), 3., 1., reason='synthetic', motion=motion)
        attribution = tracker.observe(voice, 100., {'bret': [(1., 0., 0.)]})
        return tracker, attribution

    def test_motion_raises_the_bar_but_still_recognizes_a_clear_voice(self):
        self.assertEqual(self.observe(.52, False)[1].person_id, 'bret')
        self.assertIsNone(self.observe(.52, True)[1].person_id)        # Borderline under motion: unresolved.
        tracker, clear = self.observe(.62, True)
        self.assertEqual(clear.person_id, 'bret')
        self.assertEqual(clear.reason, 'acoustic_match_during_motion')
        self.assertFalse(clear.learning_eligible)
        self.assertEqual(tracker.tracks, {})   # Servo-noise audio never becomes a stored reference.


class SeekTests(unittest.TestCase):
    """Vision seek against the simulated room: the fake locator sees only within ±45 degrees."""
    def start(self, things):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        world.objects[:] = things
        self.calls = []
        def look(after):
            return type('Image', (), {'frame_id': 'f', 'detail': 'high'})()
        def locate(description, image, cancel, hint=None):
            self.calls.append(description)
            for _, label, px, py in world.objects:
                distance, bearing, _, _ = world.relative(px, py)
                if label == description and abs(bearing) < 45:
                    return {'found': True, 'x': .5 - bearing/90, 'y': .5, 'width': min(.9, .4/max(distance, .1)),
                            'height': .3, 'confidence': 'high', 'note': 'matched'}
            return {'found': False, 'x': 0, 'y': 0, 'width': 0, 'height': 0, 'confidence': 'low', 'note': 'not visible'}
        drive.look, drive.locator = look, locate
        settle()
        return clock, fake, drive, world

    def run_plan(self, clock, drive, *steps):
        return drive.execute(DrivePlan('p', tuple(steps), 'requested', 'find it', {}, clock()+120), Cancellation())

    def test_thing_in_view_is_faced_after_one_look(self):
        clock, fake, drive, world = self.start([('door', 'door', 2., 1.2)])   # ~31 degrees left
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'))
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertAlmostEqual(fake.theta, 31., delta=5.)
        self.assertEqual(self.calls, ['door'])

    def test_sweeps_the_preferred_way_until_found(self):
        clock, fake, drive, world = self.start([('photo', 'photo of a clock', -.5, -2.)])   # behind on the right
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='photo of a clock', prefer='right'))
        self.assertEqual(receipt.status, 'completed', receipt)
        first = next(c for c in fake.commands if c['cmd'] == 'turn')
        self.assertLess(first['deg'], 0)   # Swept right first, as asked.
        self.assertGreater(len(self.calls), 1)

    def test_not_found_after_four_looks_reports_honestly(self):
        clock, fake, drive, world = self.start([])
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='a unicorn'))
        self.assertEqual(receipt.status, 'partial')   # Turned and looked, found nothing.
        self.assertEqual(receipt.steps[0]['status'], 'not_found')
        self.assertEqual(len(self.calls), 4)

    def test_approach_sought_uses_vision_legs_and_stops_short(self):
        clock, fake, drive, world = self.start([('door', 'door', 2.6, .3)])
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertEqual(receipt.status, 'completed', receipt)
        remaining = math.hypot(2.6 - fake.x, .3 - fake.y)
        self.assertLess(remaining, 1.2)
        self.assertGreater(world.front_obstacle(), .3)
        self.assertLessEqual(len(self.calls), 8)

    def test_validation_requires_vision_and_a_seek_before_sought(self):
        from r3x.semantics import grounded_tools
        from tests.test_drive_session import drive_call, step
        offered = {'source_turn_id': 't', 'state_revision': 0, 'drive': {'available': True, 'origin': 'requested',
                   'targets': [], 'vision_seek': True}}
        tools = {d['name']: d for d in grounded_tools(offered)}
        self.assertIn('sought', tools['drive_base']['parameters']['properties']['steps']['items']['properties']['target']['enum'])
        seek = {**step('seek'), 'description': 'the door'}
        from r3x.memory import Memory
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        from tests.test_voice_core import Trace
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            store = Memory(Path(temp).resolve()/'core.sqlite3')
            store.start()
            try:
                state = CoreState(store, SpeakerTracker('synthetic:3'), Trace())
                offered['state_revision'] = store.revision
                plan = state.propose_drive(drive_call(seek, step('approach', target='sought')), offered, Cancellation())
                self.assertEqual([s.action for s in plan.steps], ['seek', 'approach'])
                with self.assertRaisesRegex(ValueError, 'ApproachSoughtNeedsSeek'):
                    state.propose_drive(drive_call(step('approach', target='sought')), offered, Cancellation())
                with self.assertRaisesRegex(ValueError, 'VisionSeekUnavailable'):
                    state.propose_drive(drive_call(seek), {**offered, 'drive': {**offered['drive'], 'vision_seek': False}}, Cancellation())
            finally:
                store.close()


def shaped_room(world, blocks):
    """Front sensors for boxes at room positions: (x, y, half-width in metres).

    The 8x8 matrix spans about 45 degrees (column 0 is the left edge); the radial front
    sensors look 22.5 degrees either side with a cone of about 13 degrees."""
    def seen(lo, hi):
        values = []
        for px, py, half in blocks:
            distance, bearing, forward, _ = world.relative(px, py)
            if forward <= 0:
                continue
            spread = math.degrees(math.atan2(half, forward))
            if bearing + spread >= lo and bearing - spread <= hi:
                values.append(forward - .27)
        return int(min(values)*1000) if values else 4000
    def sensors():
        columns = [seen(22.5 - 5.625*(c+1), 22.5 - 5.625*c) for c in range(8)]
        left, right = seen(9.5, 35.5), seen(-35.5, -9.5)
        return {'fl': left, 'fr': right, 'fl_radial': left, 'fr_radial': right}, columns*8
    return sensors


class GoingAroundTests(SeekTests):
    """September 27 floor run: stopped 15 ft short of a door and called it arrived."""
    def test_obstacle_slightly_right_is_passed_on_the_left(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = shaped_room(world, [(1.0, -.2, .2)])   # A box just right of the path.
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        step = receipt.steps[-1]
        self.assertIn('detours', step, receipt)
        self.assertEqual(step['detours'][0]['side'], 'left')
        self.assertGreater(fake.x, 1.3)   # Past the box, not parked in front of it.
        self.assertNotEqual(step['reason'], 'reached_stand_off_near_target')

    def test_pointed_slightly_into_a_wall_re_aims_before_calling_it_blocked(self):
        clock, fake, drive, world = self.start([('door', 'door', 4., .4)])   # ~6 degrees left.
        wall = [(1.1, -.3 - i*.2, .12) for i in range(6)]    # Wall ahead-right; the door's line is open.
        fake.sensors = shaped_room(world, wall)
        drive.execute(DrivePlan('p', (DriveStep('turn', degrees=-2.),), 'requested', 'x', {}, clock()+60), Cancellation())
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertGreater(fake.x, 1.2, receipt)

    def test_blocked_with_no_way_around_is_reported_not_claimed_as_arrival(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = shaped_room(world, [(.9, y/10, .12) for y in range(-12, 13, 2)])   # A wide wall.
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertEqual(receipt.steps[-1]['status'], 'blocked', receipt)
        self.assertEqual(receipt.steps[-1]['reason'], 'obstacle_before_target')
        self.assertTrue(receipt.steps[-1]['target_still_far'])
        self.assertNotEqual(receipt.status, 'completed')

    def test_a_different_door_off_to_the_side_is_not_chased(self):
        clock, fake, drive, world = self.start([('door-a', 'door', 3., -.3), ('door-b', 'door', 1.5, 1.2)])
        hints, answers = [], iter(range(100))
        def locate(description, image, cancel, hint=None):
            hints.append(hint)
            rows = [world.relative(px, py) for _, _, px, py in world.objects]
            visible = [(d, b) for d, b, _, _ in rows if abs(b) < 45]
            d, b = visible[next(answers) % len(visible)]   # An inconsistent locator alternates doors.
            return {'found': True, 'x': .5 - b/90, 'y': .5, 'width': min(.9, .4/d), 'height': .3,
                    'confidence': 'high', 'note': 'white door'}
        drive.locator = locate
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertTrue(any(h and 'white door' in h for h in hints))
        self.assertLess(fake.theta, 15., receipt)   # Never swung toward door B on the left.
        self.assertLess(math.hypot(3. - fake.x, -.3 - fake.y), 1.6, receipt)

    def test_arm_parks_only_when_the_wheels_are_about_to_move(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = shaped_room(world, [(.9, y/10, .12) for y in range(-12, 13, 2)])
        events = []
        class Servos:
            def prepare_base_motion(self, cancel):
                events.append('park')
                return type('R', (), {'status': 'pulses_reached', 'reason': None})()
            def finish_base_motion(self):
                events.append('release')
        drive.servos = Servos()
        drive._sought = {'description': 'door'}
        receipt = self.run_plan(clock, drive, DriveStep('approach', target='sought'))   # "Keep going" into a wall.
        self.assertEqual(receipt.travelled_m, 0.)
        self.assertEqual(events, [])      # No arm pull-back for a trip that goes nowhere.
        world.objects[:] = [('door', 'door', 1.5, 1.2)]
        fake.sensors = None
        settle()
        self.run_plan(clock, drive, DriveStep('seek', description='door'))
        self.assertEqual(events, ['park', 'release'])


class SoughtAcrossPlansTests(SeekTests):
    def test_keep_going_re_finds_the_last_sought_thing_in_a_new_plan(self):
        clock, fake, drive, world = self.start([('door', 'door', 3.2, 0.)])
        first = self.run_plan(clock, drive, DriveStep('seek', description='door'))
        self.assertEqual(first.status, 'completed')
        self.assertEqual(drive.context('requested')['last_sought'], 'door')
        later = self.run_plan(clock, drive, DriveStep('approach', target='sought'))   # "Keep going."
        self.assertEqual(later.steps[0]['status'], 'completed', later)
        self.assertGreater(fake.x, .3)

    def test_validation_accepts_keep_going_and_a_described_approach(self):
        from tests.test_drive_session import drive_call, step
        from r3x.memory import Memory
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        from tests.test_voice_core import Trace
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            store = Memory(Path(temp).resolve()/'core.sqlite3')
            store.start()
            try:
                state = CoreState(store, SpeakerTracker('synthetic:3'), Trace())
                drive = {'available': True, 'origin': 'requested', 'targets': [], 'vision_seek': True, 'last_sought': 'door'}
                context = {'source_turn_id': 't', 'state_revision': store.revision, 'drive': drive}
                plan = state.propose_drive(drive_call(step('approach', target='sought')), context, Cancellation())
                self.assertEqual(plan.steps[0].target, 'sought')
                described = {**step('approach', target='sought'), 'description': 'the door'}
                plan = state.propose_drive(drive_call(described), {**context, 'drive': {**drive, 'last_sought': None}}, Cancellation())
                self.assertEqual(plan.steps[0].description, 'the door')
            finally:
                store.close()


class SoughtTargetSpellingTests(unittest.TestCase):
    def test_an_unrecognized_approach_target_after_seek_means_the_sought_thing(self):
        from tests.test_drive_session import drive_call, step
        from r3x.memory import Memory
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        from tests.test_voice_core import Trace
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as temp:
            store = Memory(Path(temp).resolve()/'core.sqlite3')
            store.start()
            try:
                state = CoreState(store, SpeakerTracker('synthetic:3'), Trace())
                drive = {'available': True, 'origin': 'requested', 'targets': [], 'vision_seek': True, 'last_sought': None}
                context = {'source_turn_id': 't', 'state_revision': store.revision, 'drive': drive}
                seek = {**step('seek'), 'description': 'door'}
                plan = state.propose_drive(drive_call(step('turn', degrees=90.), seek,
                                                      step('approach', target='drive.last_sought')), context, Cancellation())
                self.assertEqual([s.action for s in plan.steps], ['turn', 'seek', 'approach'])
                self.assertEqual(plan.steps[-1].target, 'sought')
                with self.assertRaisesRegex(ValueError, 'DriveTargetNotOffered'):   # No seek: still rejected.
                    state.propose_drive(drive_call(step('approach', target='face-invented')), context, Cancellation())
            finally:
                store.close()
