"""September 27 floor-test repairs: clipped onsets, stop words, gaze and seated approach."""
import math
import time
import unittest

from r3x.capture import Endpoint, Frame
from r3x.contracts import Cancellation
from r3x.drive import DrivePlan, DriveStep
from r3x.drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
from r3x.gaze import addressee_neck
from r3x.servo_poses import LIMITS, talking_lanes
from r3x.session import is_stop_command
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
        for text in ('Shut up.', 'Stop talking!', 'Rex, be quiet.', 'stop', 'Oh, stop!', 'Stop, stop, stop!',
                     'Whoa!', 'Whoa, Rex, stop.', 'No, stop!', 'Hey, stop now.'):
            self.assertTrue(is_stop_command(text), text)
        for text in ('please stop talking about the weather', 'Oh.', "No, don't stop.", 'Wait.', 'Okay.',
                     'Stop by the store later.', 'cancel my trip'):
            self.assertFalse(is_stop_command(text), text)


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

    def test_approach_sought_rolls_continuously_and_stops_short(self):
        from r3x.drive import PLAN_VISION_CALLS
        clock, fake, drive, world = self.start([('door', 'door', 2.6, .3)])
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertEqual(receipt.status, 'completed', receipt)
        remaining = math.hypot(2.6 - fake.x, .3 - fake.y)
        self.assertLess(remaining, 1.2)
        self.assertGreater(world.front_obstacle(), .3)
        self.assertLessEqual(len(self.calls), PLAN_VISION_CALLS)
        self.assertEqual(receipt.steps[-1]['continuous_runs'], 1)      # One smooth run, not legs.
        self.assertFalse(any(c['cmd'] == 'move' for c in fake.commands))

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
    def seen(lo, hi, sensor=None):
        values = []
        for px, py, half in blocks:
            distance, bearing, forward, left = world.relative(px, py)
            if forward <= 0:
                continue
            spread = math.degrees(math.atan2(half, distance))
            if bearing + spread >= lo and bearing - spread <= hi:
                # The matrix reports depth; a radial reports range from its spot on the ring.
                values.append(forward - .27 if sensor is None else
                              math.hypot(forward - sensor[0], left - sensor[1]) - half)
        return int(max(.02, min(values))*1000) if values else 4000
    def sensors():
        columns = [seen(22.5 - 5.625*(c+1), 22.5 - 5.625*c) for c in range(8)]
        spot = (.27*math.cos(math.radians(22.5)), .27*math.sin(math.radians(22.5)))
        left, right = seen(9.5, 35.5, spot), seen(-35.5, -9.5, (spot[0], -spot[1]))
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


class HallwayTests(SeekTests):
    """September 27 hallway run: a corner at front-left stopped him 13 ft from the door."""
    def test_recorded_readings_leave_the_hallway_open(self):
        from r3x.drive_space import clearance, move_allowance
        # Where he stopped: centre band 3.34 m, left band 1.65 m, front-left radial 0.67 m.
        tof = {'fl': 670, 'fl_radial': 670, 'fr': 3500, 'rl': 1720, 'rr': 1720, 'lf': 2000, 'lb': 2000, 'rf': 830, 'rb': 830}
        columns = [1650]*3 + [3340]*2 + [3500]*3
        space = clearance({'received_at': 10., 'tof_mm': tof}, {'received_at': 10., 'grid': columns*8, 'rej': [4095]*8}, 10.)
        self.assertAlmostEqual(space['front_m'], .67)          # The corner is still reported...
        self.assertGreater(space['path_m'], 1.5)               # ...but it is beside the path.
        self.assertEqual(move_allowance(.8, space), (.8, None))
        tof.update(fl=400, fl_radial=400)                      # Closer: inside the body's path.
        space = clearance({'received_at': 10., 'tof_mm': tof}, {'received_at': 10., 'grid': columns*8, 'rej': [4095]*8}, 10.)
        self.assertAlmostEqual(space['path_m'], .4)

    def test_corner_beside_the_path_and_an_oversized_door_box_do_not_stop_the_approach(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = shaped_room(world, [(.75, .55, .08)])   # Hallway corner at front-left.
        def locate(description, image, cancel, hint=None):
            distance, bearing, _, _ = world.relative(4.5, 0.)
            return {'found': True, 'x': .5 - bearing/90, 'y': .6, 'width': .4, 'height': .86,
                    'confidence': 'medium', 'note': 'bright white center hallway door'}
        drive.locator = locate
        drive._sought = {'description': 'door'}
        receipt = self.run_plan(clock, drive, DriveStep('approach', target='sought'))   # "Keep going."
        self.assertGreater(fake.x, 2.5, receipt)
        self.assertNotEqual(receipt.steps[0]['reason'], 'reached_stand_off_at_target')


class TightHallwayTests(SeekTests):
    """September 27: near a hallway corner every re-aim reversed to make arm room; he looped."""
    def test_tight_hallway_goes_forward_and_never_reverses(self):
        clock, fake, drive, world = self.start([('door', 'door', 5., .7)])   # ~8 degrees left, far.
        fake.tof.update(lf=300, lb=300, rf=450, rb=450)   # Walls close on both sides; arms on the left.
        receipt = self.run_plan(clock, drive, DriveStep('approach', description='door', target='sought'))
        backward = [c for c in fake.commands if c['cmd'] == 'move' and c['dist'] < 0]
        self.assertEqual(backward, [], receipt)
        self.assertGreater(fake.x, 2.5, receipt)

    def test_a_refused_aim_turn_keeps_going_forward(self):
        clock, fake, drive, world = self.start([('door', 'door', 3., 1.5)])   # ~27 degrees left.
        fake.tof.update(lf=300, lb=300)                    # Left arm swing blocked.
        receipt = self.run_plan(clock, drive, DriveStep('approach', description='door', target='sought'))
        step = receipt.steps[-1]
        self.assertGreater(step.get('aim_turns_refused_for_arm_clearance', 0), 0, receipt)
        self.assertEqual([c for c in fake.commands if c['cmd'] == 'move' and c['dist'] < 0], [])
        self.assertGreater(fake.x, .5, receipt)


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


class TurnAroundAndComeTests(unittest.TestCase):
    """September 27: "back up, turn around and come towards me" lost its last part."""
    def start(self):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        return clock, fake, drive, world

    def test_come_to_me_after_turning_is_offered_validated_and_driven(self):
        from r3x.memory import Memory
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        from tests.test_drive_session import drive_call, step
        from tests.test_voice_core import Trace
        import tempfile
        from pathlib import Path
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, -2.6, 0.))   # Behind him: nobody in view.
        settle()
        context = drive.context('requested')
        nearest = next(t for t in context['targets'] if t['target_id'] == 'nearest_person')
        self.assertFalse(nearest['visible_now'])
        with tempfile.TemporaryDirectory() as temp:
            store = Memory(Path(temp).resolve()/'core.sqlite3')
            store.start()
            try:
                state = CoreState(store, SpeakerTracker('synthetic:3'), Trace())
                offered = {'source_turn_id': 't', 'state_revision': store.revision, 'drive': context}
                with self.assertRaisesRegex(ValueError, 'NearestPersonNeedsTurnOrSearchFirst'):
                    state.propose_drive(drive_call(step('approach', target='nearest_person')), offered, Cancellation())
                plan = state.propose_drive(drive_call(step('move', meters=-.3), step('turn', degrees=180),
                                                      step('approach', target='nearest_person', stop_at_m=1)),
                                           offered, Cancellation())
            finally:
                store.close()
        plan = DrivePlan(plan.request_id, plan.steps, plan.origin, plan.purpose, plan.targets, clock()+60)
        receipt = drive.execute(plan, Cancellation())
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertEqual([r['action'] for r in receipt.steps], ['move', 'turn', 'approach'])
        self.assertLess(math.hypot(-2.6 - fake.x, fake.y), 1.3)
        self.assertEqual(drive.history[-1]['steps_run'], ['move:completed', 'turn:completed', 'approach:completed'])

    def test_furniture_short_of_the_person_keeps_the_normal_margin(self):
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3., 0.))
        world.objects.append(('table', 'table', 1.6, 0.))   # Well short of the face.
        settle()
        plan = DrivePlan('p', (DriveStep('approach', target='face-1'),), 'requested', 'come here',
                         {'face-1': {'target_id': 'face-1', 'kind': 'face'}}, clock()+60)
        receipt = drive.execute(plan, Cancellation())
        self.assertNotEqual(receipt.status, 'completed')
        self.assertGreaterEqual(world.front_obstacle(), .3, receipt)   # Not the 0.22 m knee margin.

    def test_a_stopped_approach_reports_how_far_it_went(self):
        import threading
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3., 0.))
        settle()
        cancel = Cancellation()
        original = drive.wait
        def wait(seconds):
            original(seconds)
            if fake.x > .5:
                cancel.event.set()
        drive.wait = wait
        plan = DrivePlan('p', (DriveStep('approach', target='face-1'),), 'requested', 'come here',
                         {'face-1': {'target_id': 'face-1', 'kind': 'face'}}, clock()+60)
        receipt = drive.execute(plan, cancel)
        self.assertEqual(receipt.status, 'cancelled')
        self.assertEqual(receipt.steps[-1]['status'], 'cancelled')
        self.assertGreater(receipt.travelled_m, .4)


class MotionSoundTests(unittest.TestCase):
    """V2's drive sounds: loop while the wheels move, accents at the end, silence on stop."""
    def setUp(self):
        from r3x.motion_sounds import MotionSounds
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        self.clock, self.fake, self.drive, self.world = clock, fake, drive, world
        self.events = []
        test = self
        class Sink:
            def play(self, chunks, cancel, on_start, visualize=True):
                test.assertFalse(visualize)              # Effects never drive the speech LEDs.
                test.events.append(['play', 0])
                for chunk in chunks:
                    cancel.check()
                    test.events[-1][1] += len(chunk)
                    time.sleep(.0005)
            def cancel(self):
                pass
        drive.sounds = MotionSounds(Sink())
        self.moving = []
        original = fake.handle
        def handle(command):
            if command['cmd'] in {'turn', 'move'}:
                self.moving.append(len(self.events))
            original(command)
        fake.handle = handle

    def run_plan(self, *steps, origin='requested', targets=None, cancel=None):
        plan = DrivePlan('p', tuple(steps), origin, 'test', targets or {}, self.clock()+60)
        return self.drive.execute(plan, cancel or Cancellation())

    def test_each_wheel_command_loops_its_sound_and_stops_with_it(self):
        receipt = self.run_plan(DriveStep('turn', degrees=90.), DriveStep('move', meters=.5))
        self.assertEqual(receipt.status, 'completed')
        self.assertEqual(len(self.events), 2)            # One whir per wheel command...
        self.assertTrue(all(n > 0 for _, n in self.events))
        self.assertIsNone(self.drive.sounds._thread)     # ...and none left playing.

    def test_self_initiated_motion_is_quieter(self):
        from r3x.motion_sounds import CLIPS, GAIN, read_motion_clip, shaped
        loud = shaped(read_motion_clip(CLIPS['move']), GAIN['requested'])
        quiet = shaped(read_motion_clip(CLIPS['move']), GAIN['self_initiated'])
        from array import array
        self.assertLess(max(map(abs, array('h', quiet))), max(map(abs, array('h', loud))) / 3)

    def test_arrival_chirp_after_reaching_someone(self):
        import math
        self.world.people.append(('face-1', None, 2., 0.))
        settle()
        receipt = self.run_plan(DriveStep('approach', target='face-1'),
                                targets={'face-1': {'target_id': 'face-1', 'kind': 'face'}})
        self.assertEqual(receipt.status, 'completed', receipt)
        from r3x.motion_sounds import ACCENT_SECONDS, RATE
        self.assertEqual(self.events[-1][1], int(ACCENT_SECONDS*RATE)*2)   # The short, faded accent.

    def test_stop_silences_the_whir_at_once(self):
        import threading
        cancel = Cancellation()
        timer = threading.Timer(.05, lambda: (cancel.event.set(), self.drive.cancel()))
        timer.start()
        receipt = self.run_plan(DriveStep('move', meters=1.5), cancel=cancel)
        timer.join()
        self.assertEqual(receipt.status, 'cancelled')
        self.assertIsNone(self.drive.sounds._thread)


class ContinuousSoughtTests(SeekTests):
    def test_stop_mid_run_halts_at_once(self):
        import threading
        clock, fake, drive, world = self.start([('door', 'door', 4., 0.)])
        drive._sought = {'description': 'door'}
        cancel = Cancellation()
        original = drive.wait
        def wait(seconds):
            original(seconds)
            if fake.x > .6 and not cancel.event.is_set():
                cancel.event.set()
        drive.wait = wait
        began = time.monotonic()
        receipt = drive.execute(DrivePlan('p', (DriveStep('approach', target='sought'),), 'requested', 'x', {}, clock()+60), cancel)
        self.assertEqual(receipt.status, 'cancelled')
        self.assertLess(time.monotonic() - began, 3.)
        self.assertEqual(fake.commands[-1]['cmd'], 'stop')
        self.assertGreater(receipt.travelled_m, .5)


class BaseStatusTests(unittest.TestCase):
    def test_battery_and_sensors_reach_the_model_and_unknowns_stay_unknown(self):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        settle()
        status = drive.context('requested')['base_status']
        self.assertTrue(status['connected'])
        self.assertEqual(status['battery']['volts'], 12.8)
        self.assertIsNone(status['battery']['percent'])        # The fake controller reports no estimate.
        self.assertFalse(status['battery']['on_charger'])
        self.assertIsNone(status['room'])                      # No climate sensor: nothing invented.



class SurroundingsTests(unittest.TestCase):
    def test_obstructions_and_radar_status_reach_the_model(self):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        world.objects.append(('box', 'box', 1.2, 0.))
        settle()
        context = drive.context('requested')
        self.assertAlmostEqual(context['clearance_m']['path_m'], .9, delta=.1)
        self.assertIn('path_m', context['clearance_meaning'])
        self.assertEqual(context['radar_view']['status'], 'not_configured')
        self.assertEqual(context['radar_view']['bodies'], [])


class IdleHeadTests(unittest.TestCase):
    def test_looks_at_the_face_in_front_or_around_and_never_while_driving(self):
        import random
        from r3x.servo_poses import LIMITS, NECK_CENTER, idle_lanes
        current = {ch: (low + high)//2 for ch, (low, high) in LIMITS.items()}
        current.update({8: 4200, 9: 7720, 10: 5120})
        rng = random.Random(4)
        heads = lambda face: [dict(l[0].targets) for l in idle_lanes(current, rng, face_neck=face) if 0 in dict(l[0].targets)]
        facing = [heads(4000)[0][0] for _ in range(40)]
        self.assertGreater(sum(abs(n - 4000) <= 40 for n in facing), 20)      # Mostly on the face...
        self.assertTrue(any(abs(n - 4000) > 40 for n in facing))               # ...with glances away.
        around = [heads(None)[0] for _ in range(40)]
        self.assertGreater(max(h[0] for h in around) - min(h[0] for h in around), 3000)   # Left and right.
        self.assertGreater(max(h[2] for h in around) - min(h[2] for h in around), 500)    # Up and down.
        self.assertTrue(all(abs(h[0] - NECK_CENTER) <= 35*77.6 for h in around))
        driving = idle_lanes(current, rng, held=frozenset({0, 1, 2, 4, 6, 8, 9, 10}), face_neck=4000)
        self.assertFalse(any({0, 1, 2}.intersection(dict(l[0].targets)) for l in driving))

    def test_idle_forwards_visible_face_to_the_servo_owner(self):
        from r3x.idle_motion import IdleMotion
        calls = []
        class Owner:
            state, version, held_channels = 'active', 1, set()
            clock = staticmethod(lambda: 100.)
            def motion_risk(self): return False
            def execute(self, request, cancel):
                calls.append(('execute', request.face_neck))
        idle = IdleMotion(Owner())
        idle.face = lambda: 4100
        idle._allowed, idle._next = True, 0.
        idle.tick(True)
        idle._thread.join(1.)
        self.assertEqual(calls, [('execute', 4100)])


class PlanValidationRepairTests(unittest.TestCase):
    """September 27 run with Jeremy: plans refused for fixable reasons, and the 3 m cap."""
    def propose(self, *steps, drive_extra=None):
        from r3x.memory import Memory
        from r3x.speakers import SpeakerTracker
        from r3x.state import CoreState
        from tests.test_drive_session import drive_call
        from tests.test_voice_core import Trace
        import tempfile
        from pathlib import Path
        drive = {'available': True, 'origin': 'requested', 'vision_seek': True, 'last_sought': None,
                 'targets': [{'target_id': 'nearest_person', 'kind': 'nearest_person', 'visible_now': False}],
                 **(drive_extra or {})}
        with tempfile.TemporaryDirectory() as temp:
            store = Memory(Path(temp).resolve()/'core.sqlite3')
            store.start()
            try:
                state = CoreState(store, SpeakerTracker('synthetic:3'), Trace())
                return state.propose_drive(drive_call(*steps), {'source_turn_id': 't', 'state_revision': store.revision,
                                                                'drive': drive}, Cancellation())
            finally:
                store.close()

    def test_a_seek_named_in_target_becomes_its_description(self):
        from tests.test_drive_session import step
        seek = {**step('seek', target='white door on the left')}
        plan = self.propose(seek, step('approach', target='sought'))
        self.assertEqual(plan.steps[0].description, 'white door on the left')

    def test_find_person_may_aim_at_nearest_person_before_anyone_is_visible(self):
        from tests.test_drive_session import step
        plan = self.propose(step('find_person', target='nearest_person'), step('approach', target='nearest_person'))
        self.assertEqual([s.action for s in plan.steps], ['find_person', 'approach'])

    def test_a_foot_then_ten_feet_fits_in_one_requested_plan(self):
        from tests.test_drive_session import step
        plan = self.propose(step('move', meters=.3048), step('turn', degrees=-90), step('move', meters=3.048))
        self.assertEqual(len(plan.steps), 3)


class TuckedArmTurnTests(unittest.TestCase):
    """September 27: V2's arms-out reach refused left turns beside walls after the arms tucked."""
    WALL_LEFT = {'tof_mm': {'fl': 4000, 'fr': 230, 'fl_radial': 4000, 'fr_radial': 230, 'lf': 430, 'lb': 870,
                            'rf': 1030, 'rb': 1030, 'rl': 1340, 'rr': 1340}}

    def test_tucked_arms_turn_beside_a_left_wall_that_arms_out_could_not(self):
        from r3x.drive_space import ARMS, ARMS_TUCKED, allowed_turn
        self.assertEqual(allowed_turn(180., self.WALL_LEFT, ARMS)[0], 0.)
        self.assertEqual(allowed_turn(180., self.WALL_LEFT, ARMS_TUCKED), (180., None))
        close = {'tof_mm': {**self.WALL_LEFT['tof_mm'], 'lf': 60, 'lb': 60}}
        self.assertEqual(allowed_turn(180., close, ARMS_TUCKED)[0], 0.)   # A wall at the body still blocks.

    def test_drive_uses_tucked_reach_only_when_its_servos_park_the_arms(self):
        from r3x.drive_space import ARMS, ARMS_TUCKED
        clock, fake, drive, world = base_setup(self)
        drive._plan_parks = True
        self.assertIs(drive._arms(), ARMS)          # No servo owner: arm position unknown.
        drive.servos = object()
        self.assertIs(drive._arms(), ARMS_TUCKED)
        drive._plan_parks = False                   # Straight reflex reverse: arms not parked.
        self.assertIs(drive._arms(), ARMS)


class VoiceAndObstacleApproachTests(unittest.TestCase):
    """September 27: "come towards my voice" needed a face; a trash can stopped "come to me"."""
    def start(self):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        return clock, fake, drive, world

    def run_plan(self, clock, drive, targets, step):
        return drive.execute(DrivePlan('p', (step,), 'requested', 'come to me', targets, clock()+120), Cancellation())

    def test_a_voice_alone_is_enough_to_come_closer(self):
        clock, fake, drive, world = self.start()
        settle()
        receipt = self.run_plan(clock, drive, {'speaker': {'target_id': 'speaker', 'kind': 'voice_direction', 'bearing_deg': 30}},
                                DriveStep('approach', target='speaker'))
        step = receipt.steps[-1]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertTrue(step['toward_voice'])
        self.assertAlmostEqual(fake.theta, 30., delta=6.)
        self.assertGreater(math.hypot(fake.x, fake.y), 1.5)

    def test_a_face_seen_on_the_way_takes_over_and_stops_near_the_person(self):
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3.6*math.cos(math.radians(30)), 3.6*math.sin(math.radians(30))))
        seen = world.faces
        drive.faces = lambda now: [f for f in seen(now) if f['box'][2] > FACE_WIDTH_AT(2.4)]   # Too far to detect at first.
        settle()
        receipt = self.run_plan(clock, drive, {'speaker': {'target_id': 'speaker', 'kind': 'voice_direction', 'bearing_deg': 30}},
                                DriveStep('approach', target='speaker'))
        self.assertEqual(receipt.steps[-1]['status'], 'completed', receipt)
        self.assertIn('toward_voice_m', receipt.steps[-1])
        px, py = world.people[0][2:]
        self.assertLess(math.hypot(px - fake.x, py - fake.y), 1.4)

    def test_goes_around_a_trash_can_on_the_open_side_to_reach_the_person(self):
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3.2, 0.))
        fake.sensors = shaped_room(world, [(0.9, .15, .18)])    # Can ahead and slightly left; right open.
        class Servos:   # As on the robot: arms tucked before the wheels move.
            def prepare_base_motion(self, cancel):
                return type('R', (), {'status': 'pulses_reached', 'reason': None})()
            def finish_base_motion(self):
                pass
        drive.servos = Servos()
        settle()
        receipt = self.run_plan(clock, drive, {'face-1': {'target_id': 'face-1', 'kind': 'face', 'bearing_deg': 0}},
                                DriveStep('approach', target='face-1'))
        step = receipt.steps[-1]
        self.assertIn('detours', step, receipt)
        self.assertEqual(step['detours'][0]['side'], 'right')
        self.assertLess(math.hypot(3.2 - fake.x, fake.y), 1.5, receipt)


def FACE_WIDTH_AT(distance):
    from r3x.drive_space import FACE_WIDTH, HALF_FOV
    return FACE_WIDTH / (2*math.tan(math.radians(HALF_FOV))*distance)


class ReacquireAfterNudgeTests(unittest.TestCase):
    """September 27: firmware steering around an obstacle turned the person out of frame."""
    def start(self):
        from r3x.drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        self.looks = []
        test = self
        class Owner:
            def neck_degrees(self):
                return world.neck
        class Servos:
            owner = Owner()
            def prepare_base_motion(self, cancel):
                world.neck = 0.
                return type('R', (), {'status': 'pulses_reached', 'reason': None})()
            def finish_base_motion(self):
                pass
            def execute_pose(self, pose, cancel, request_id):
                world.neck = (NECK_CENTER - pose[0]) / NECK_PULSES_PER_DEG
                test.looks.append(round(world.neck))
                return type('R', (), {'status': 'pulses_reached'})()
        drive.servos = Servos()
        return clock, fake, drive, world

    def nudge_once(self, fake, degrees, after_x):
        original = fake.handle
        state = {'done': False}
        def handle(command):
            original(command)
            if not state['done'] and fake.x > after_x:
                state['done'] = True
                fake.theta += degrees   # Firmware assist steered the base.
        fake.handle = handle

    def approach(self, clock, drive):
        plan = DrivePlan('p', (DriveStep('approach', target='nearest_person', stop_at_m=1.),), 'requested', 'come here',
                         {'nearest_person': {'target_id': 'nearest_person', 'kind': 'nearest_person'}}, clock()+120)
        return drive.execute(plan, Cancellation())

    def test_a_head_look_refinds_the_person_and_the_approach_continues(self):
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3.5, 0.))
        self.nudge_once(fake, -50., .6)
        settle()
        receipt = self.approach(clock, drive)
        step = receipt.steps[-1]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual(step['refound_by'], ['head_look'])
        self.assertEqual(self.looks[0], 40)             # Looked left toward where he should be first.
        self.assertLess(math.hypot(3.5 - fake.x, fake.y), 1.4)

    def test_a_body_turn_when_the_head_cannot_reach(self):
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3.5, 0.))
        self.nudge_once(fake, -100., .6)
        settle()
        receipt = self.approach(clock, drive)
        self.assertEqual(receipt.steps[-1]['refound_by'], ['body_turn'], receipt)
        self.assertEqual(receipt.steps[-1]['status'], 'completed')
