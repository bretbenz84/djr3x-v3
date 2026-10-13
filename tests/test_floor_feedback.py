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

    def test_quiet_phrases_are_told_apart_from_halts(self):
        from r3x.session import is_quiet_command
        for text in ("Be quiet.", "Shut up!", "Rex, be quiet", "Oh, hush", "Quiet, quiet!", "Stop talking"):
            self.assertTrue(is_quiet_command(text), text)
        for text in ("Stop!", "Whoa, Rex, stop", "Freeze", "Stop, be quiet", "Be quiet about the cable repair", "Okay"):
            self.assertFalse(is_quiet_command(text), text)


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
        self.assertLess(distance, 1.25, receipt)   # V2-style radial personal distance stopped at ~1.4 m.
        self.assertGreater(distance, .95, receipt)  # And not at the knees: the default is 1 m by face (October 3).
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
        fake.tof.update(lf=300, lb=300, rf=300, rb=300)    # And nothing open to either side.
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
        fake.tof.update(lf=300, lb=300, rf=300, rb=300)    # Boxed in: no way around either.
        events = []
        class Servos:
            def prepare_base_motion(self, cancel):
                events.append('park')
                return type('R', (), {'status': 'pulses_reached', 'reason': None})()
            def finish_base_motion(self, look_pending=False, keep_parked=frozenset()):
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
        fake.assist_on = False                             # Fixed readings: a pose, not a room.
        receipt = self.run_plan(clock, drive, DriveStep('approach', description='door', target='sought'))
        backward = [c for c in fake.commands if c['cmd'] == 'move' and c['dist'] < 0]
        self.assertEqual(backward, [], receipt)
        self.assertGreater(fake.x, 2.5, receipt)

    def test_a_refused_aim_turn_keeps_going_forward(self):
        clock, fake, drive, world = self.start([('door', 'door', 3., 1.5)])   # ~27 degrees left.
        fake.tof.update(lf=300, lb=300)                    # Left arm swing blocked.
        fake.assist_on = False
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
                # "Come here" alone is valid too: the drive owner looks for the person (October 3).
                alone = state.propose_drive(drive_call(step('approach', target='nearest_person')), offered, Cancellation())
                self.assertEqual([s.action for s in alone.steps], ['approach'])
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
        fake.tof.update(lf=300, lb=300, rf=300, rb=300)     # Walls beside: no way around it.
        settle()
        plan = DrivePlan('p', (DriveStep('approach', target='face-1'),), 'requested', 'come here',
                         {'face-1': {'target_id': 'face-1', 'kind': 'face'}}, clock()+60)
        receipt = drive.execute(plan, Cancellation())
        self.assertNotEqual(receipt.status, 'completed')
        self.assertGreaterEqual(world.front_obstacle(), .3, receipt)   # Not the 0.22 m knee margin.

    def test_a_table_before_the_person_is_passed_when_the_side_is_open(self):
        # October 3 (be8c8d55): he stopped 0.30 m from a coffee table with two metres of open
        # floor on his right, and reported an obstacle in the way.
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3., 0.))
        world.objects.append(('table', 'table', 1.6, 0.))
        fake.tof.update(lf=400, lb=400, rf=2000, rb=2000)   # Only the right side is open.
        drive.servos = TuckedArms()                          # Arms parked for driving, as on the robot.
        settle()
        plan = DrivePlan('p', (DriveStep('approach', target='face-1'),), 'requested', 'come here',
                         {'face-1': {'target_id': 'face-1', 'kind': 'face'}}, clock()+120)
        receipt = drive.execute(plan, Cancellation())
        step = receipt.steps[0]
        self.assertEqual(step['detours'][0]['side'], 'right', receipt)
        self.assertEqual(step['detours'][0]['by'], 'sensors')
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertLess(math.hypot(3. - fake.x, fake.y), 1.6)

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
    def test_idle_never_moves_the_head_while_the_base_holds_it_for_driving(self):
        # Gaze behavior itself is covered in tests/test_idle_motion.py.
        import random
        from r3x.servo_poses import LIMITS, IdlePhrase, idle_lanes
        current = {ch: (low + high)//2 for ch, (low, high) in LIMITS.items()}
        current.update({8: 4200, 9: 7720, 10: 5120})
        driving = idle_lanes(current, random.Random(4), held=frozenset({0, 1, 2, 4, 6, 8, 9, 10}),
                             phrase=IdlePhrase((('head', 0.), ('hero', 0.), ('poker', 0.)), ((0, 4000),)))
        # The hero elbow is held for the drive, so its arm stays still as a whole: no lone wrist.
        self.assertEqual(driving, ())


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

    def test_a_step_pace_is_validated_and_reaches_the_plan(self):
        from tests.test_drive_session import step
        plan = self.propose({**step('turn', degrees=40), 'pace': 'slow'},
                            step('move', meters=.5), {**step('find_person'), 'pace': None})
        self.assertEqual([s.pace for s in plan.steps], ['slow', None, None])
        with self.assertRaises(ValueError):
            self.propose({**step('move', meters=.5), 'pace': 'warp'})

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
            def finish_base_motion(self, look_pending=False, keep_parked=frozenset()):
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
            def finish_base_motion(self, look_pending=False, keep_parked=frozenset()):
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


def hallway(fake, half_width, door=None):
    """Sensors for a straight hallway along the room's x axis, walls at y = +/- half_width.

    door=(x, centre_y, half_gap) adds a wall across the hallway at x with an opening.
    Rays leave from where each sensor sits: the matrix at the front of the ring, the
    radials and side sensors on the ring at their own bearings."""
    segments = [((-9., half_width), (9., half_width)), ((-9., -half_width), (9., -half_width))]
    if door:
        dx, cy, gap = door
        segments += [((dx, -half_width), (dx, cy - gap)), ((dx, cy + gap), (dx, half_width))]
    def ray(origin_deg, direction_deg):
        heading = fake.theta
        ox = fake.x + .27*math.cos(math.radians(heading + origin_deg))
        oy = fake.y + .27*math.sin(math.radians(heading + origin_deg))
        dxr, dyr = math.cos(math.radians(heading + direction_deg)), math.sin(math.radians(heading + direction_deg))
        best = 4.
        for (ax, ay), (bx, by) in segments:
            ex, ey = bx - ax, by - ay
            denom = dxr*ey - dyr*ex
            if abs(denom) < 1e-9:
                continue
            t = ((ax - ox)*ey - (ay - oy)*ex) / denom
            u = ((ax - ox)*dyr - (ay - oy)*dxr) / denom
            if t > 0 and 0 <= u <= 1:
                best = min(best, t)
        return int(max(20, min(4000, best*1000)))
    def sensors():
        columns = [ray(0., 19.6875 - 5.625*col) for col in range(8)]
        ring = {key: ray(bearing, bearing) for key, bearing in
                (('fl_radial', 22.5), ('fr_radial', -22.5), ('lf', 67.5), ('lb', 112.5), ('rf', -67.5), ('rb', -112.5),
                 ('rl', 157.5), ('rr', -157.5))}
        return {**ring, 'fl': ring['fl_radial'], 'fr': ring['fr_radial']}, columns*8
    return sensors


def watched(fake, sensors):
    """Wrap a room's sensors to record the closest any side sensor ever read (metres)."""
    record = {'side_m': 9.}
    def wrapped():
        tof, grid = sensors()
        record['side_m'] = min(record['side_m'], *[tof[k]/1000. for k in ('lf', 'lb', 'rf', 'rb')])
        return tof, grid
    fake.sensors = wrapped
    return record


class Trace:
    def __init__(self):
        self.rows = []
    def emit(self, kind, **fields):
        self.rows.append({'kind': kind, **fields})


# The robot's own readings on October 3, left where it refused "move forward five feet":
# straight ahead and the left open, a wall crossing the right side of its path.
LIVE_TOF = {'fl': 3500, 'fr': 401, 'rl': 463, 'rr': 951, 'lf': 600, 'lb': 261, 'rf': 429, 'rb': 2000,
            'fl_radial': 4000, 'fr_radial': 401}
LIVE_GRID = [4000, 6, 4000, 4000, 4000, 4000, 616, 559, 4000, 4000, 4000, 4000, 4000, 4000, 635, 559,
             4000, 4000, 4000, 4000, 4000, 4000, 646, 561, 4000, 4000, 4000, 4000, 4000, 4000, 659, 566,
             4000, 4000, 4000, 4000, 4000, 4000, 648, 558, 4000, 4000, 4000, 4000, 4000, 4000, 635, 541,
             4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000, 4000]


class TuckedArms:
    """The servo owner parks the arms before the wheels move, as on the robot."""
    def prepare_base_motion(self, cancel):
        return type('R', (), {'status': 'pulses_reached', 'reason': None})()
    def finish_base_motion(self, look_pending=False, keep_parked=frozenset()):
        pass


class HallwayMoveTests(SeekTests):
    """October 3 floor run: hallways blocked every forward move."""
    def start(self, things):
        clock, fake, drive, world = super().start(things)
        drive.servos = TuckedArms()
        return clock, fake, drive, world

    def moves(self, fake):
        return [c for c in fake.commands if c['cmd'] == 'move']

    def test_walls_beside_a_straight_hallway_neither_shorten_nor_refuse_the_move(self):
        clock, fake, drive, world = self.start([])
        fake.sensors = hallway(fake, .45)
        settle()
        space = drive.space()
        self.assertLess(space['front_m'], 1.)          # The walls are seen...
        self.assertGreater(space['path_m'], 3.)        # ...but they are beside the path.
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=2.))
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertAlmostEqual(fake.x, 2., places=1)
        self.assertFalse([c for c in fake.commands if c['cmd'] == 'turn'])
        # 18 cm either side is tight: he rolls slower than the open-room pace.
        self.assertTrue(receipt.steps[0]['slowed_for_tight_space'])
        self.assertTrue(all(m['speed'] <= .15 + 1e-6 for m in self.moves(fake)))

    def test_pointed_at_a_hallway_wall_he_turns_away_first_then_moves(self):
        clock, fake, drive, world = self.start([])
        fake.theta = -20.                               # Angled toward the right wall.
        fake.sensors = hallway(fake, .6)
        settle()
        self.assertEqual(drive._short_side(drive.space(), .8), -1)
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=1.))
        self.assertEqual(receipt.status, 'completed', receipt)
        turns = [c for c in fake.commands if c['cmd'] == 'turn']
        self.assertTrue(turns and all(c['deg'] > 0 for c in turns))    # Left, away from the wall.
        self.assertGreater(receipt.steps[0]['turned_away_from_wall_deg'], 0)
        self.assertAlmostEqual(fake.x, 1., delta=.1)
        self.assertFalse([m for m in self.moves(fake) if m['dist'] < 0])   # Never a reverse.
        self.assertLess(abs(fake.y), .3)                # He went down the hallway, not into the wall.
        # The turn is sized by how far the wall slants across his path, not a fixed step.
        self.assertAlmostEqual(turns[0]['deg'], 20. + 3. - 45*.11, delta=4.)   # Coast allowed for.

    def test_an_open_room_move_is_not_turned(self):
        clock, fake, drive, world = self.start([])
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=1.))
        self.assertEqual(receipt.status, 'completed')
        self.assertFalse([c for c in fake.commands if c['cmd'] == 'turn'])
        self.assertNotIn('slowed_for_tight_space', receipt.steps[0])
        self.assertEqual(self.moves(fake)[0]['speed'], .25)

    def test_the_logged_refusal_now_crawls_instead_of_refusing(self):
        clock, fake, drive, world = self.start([])
        # The left arms are against something, so he cannot turn away; the old code refused.
        fake.sensors = lambda: ({**LIVE_TOF, 'lf': 60, 'lb': 60}, LIVE_GRID)
        fake.assist_on = False
        settle()
        space = drive.space()
        self.assertAlmostEqual(space['path_right_m'], .40, places=2)
        self.assertGreater(space['path_center_m'], 3.)
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=1.52))
        step = receipt.steps[0]
        self.assertNotEqual(step['status'], 'refused', receipt)
        self.assertTrue(step['crawled'] and step['slowed_for_tight_space'], receipt)
        self.assertFalse(self.moves(fake))                                   # A continuous crawl, not legs.
        self.assertTrue([c for c in fake.commands if c['cmd'] == 'drive' and c['lin'] > 0])

    def test_pressed_against_a_hallway_wall_he_eases_off_it_and_carries_on(self):
        clock, fake, drive, world = self.start([])
        fake.y = .28                                     # His left side 8 cm from the wall of a 1.2 m hallway.
        record = watched(fake, hallway(fake, .6))
        settle()
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=2.))
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertGreater(fake.x, 1.8)
        self.assertLess(abs(fake.y), .2, fake.y)         # Off the wall, toward the middle.
        self.assertGreaterEqual(record['side_m'], .05)    # Never scraped along it.

    def test_with_room_to_turn_the_logged_pose_straightens_before_moving(self):
        clock, fake, drive, world = self.start([])
        fake.sensors = lambda: (LIVE_TOF, LIVE_GRID)
        fake.assist_on = False
        settle()
        self.run_plan(clock, drive, DriveStep('move', meters=1.52))
        first = next(c for c in fake.commands if c['cmd'] in {'turn', 'move', 'drive'})
        self.assertEqual(first['cmd'], 'turn')
        self.assertGreater(first['deg'], 0)                         # Left, away from the right wall.
        self.assertAlmostEqual(first['deg'], 12. - 45*.11, places=1)   # The side sensors see no wall: the plain step.

    def test_the_october_5_hallway_pose_is_driven_through(self):
        """Live readings that evening: a 1.2 m hallway, him 7 cm right of centre and angled
        about 11 degrees into the right wall (right side 0.25 m, left 0.34-0.40 m). Every
        leg then stopped within 0.3 m on the hold; twelve feet were asked for."""
        clock, fake, drive, world = self.start([])
        fake.y, fake.theta = -.075, -11.
        record = watched(fake, hallway(fake, .6))
        settle()
        self.assertEqual(drive._short_side(drive.space(), .8), -1)
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=3.66))
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertGreater(fake.x, 3.4, receipt)
        self.assertLess(abs(fake.y), .25)
        self.assertLess(abs(fake.theta), 15.)
        self.assertGreaterEqual(record['side_m'], .12, record)      # Hallway walls never approached.
        self.assertFalse([m for m in self.moves(fake) if m['dist'] < 0])
        step = receipt.steps[0]
        self.assertTrue(step['crawled'] and step['slowed_for_tight_space'], step)

    def test_a_narrower_doorway_is_centred_and_passed(self):
        """The door beyond the hallway: a 0.8 m opening for a 0.54 m body, off his line."""
        clock, fake, drive, world = self.start([])
        door_x, centre, gap = 2.5, .15, .4
        record = watched(fake, hallway(fake, .9, door=(door_x, centre, gap)))
        margins = []
        base_sensors = fake.sensors
        def sensors():
            if abs(fake.x - door_x) < .3:
                margins.append(gap - abs(fake.y - centre) - .27)    # Room between his side and the jamb.
            return base_sensors()
        fake.sensors = sensors
        settle()
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=4.))
        self.assertEqual(receipt.status, 'completed', receipt)
        self.assertGreater(fake.x, 3.8, receipt)
        self.assertTrue(margins)
        self.assertGreater(min(margins), .02, min(margins))          # Through the opening, both sides clear.
        self.assertFalse([m for m in self.moves(fake) if m['dist'] < 0])

    def test_something_across_the_hallway_stops_the_crawl_short(self):
        clock, fake, drive, world = self.start([])
        fake.sensors = hallway(fake, .45, door=(1.2, 0., 0.))        # A wall across it, no opening.
        settle()
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=2.))
        step = receipt.steps[0]
        self.assertEqual((step['status'], step['reason']), ('completed', 'shortened_by_clearance'), receipt)
        self.assertTrue(.3 < fake.x < .9, fake.x)                   # Stopped by the host short of it.

    def test_a_refused_turn_leaves_a_trace(self):
        clock, fake, drive, world = self.start([])
        drive.trace = Trace()
        fake.tof.update(lf=90, lb=90, rf=90, rb=90, fl_radial=90, fr_radial=90, fl=90, fr=90)
        settle()
        receipt = self.run_plan(clock, drive, DriveStep('turn', degrees=90.))
        self.assertEqual(receipt.steps[0]['status'], 'refused', receipt)
        rows = [r for r in drive.trace.rows if r['kind'] == 'drive_step' and r['action'] == 'turn']
        self.assertEqual([r['status'] for r in rows], ['refused'], rows)    # After the escape move, still refused.
        self.assertTrue(rows[0]['reason'].startswith('turn_path_blocked'), rows[0])

    def test_a_real_obstacle_straight_ahead_still_shortens_and_refuses(self):
        clock, fake, drive, world = self.start([])
        fake.sensors = shaped_room(world, [(.55, y/10, .12) for y in range(-12, 13, 2)])   # A wall across the way.
        settle()
        receipt = self.run_plan(clock, drive, DriveStep('move', meters=1.))
        self.assertEqual(receipt.steps[0]['status'], 'refused', receipt)
        self.assertEqual(receipt.steps[0]['reason'], 'path_blocked')
        self.assertFalse(self.moves(fake))

    def test_turn_bookkeeping_follows_the_imu_when_wheels_slip(self):
        clock, fake, drive, world = self.start([])
        fake.odom_slip = 1.                              # Odometry reads twice the real turn.
        receipt = self.run_plan(clock, drive, DriveStep('turn', degrees=30.))
        self.assertGreater(receipt.steps[0]['odometry_deg'], 50.)
        self.assertAlmostEqual(drive._rotation(), 30., delta=2.)   # Where targets are now expected.

    def test_arriving_ends_the_search_so_later_plans_cannot_resume_it(self):
        clock, fake, drive, world = self.start([('door', 'door', 1.6, 0.)])
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertEqual(receipt.steps[-1]['status'], 'completed', receipt)
        self.assertIsNone(drive.context('requested')['last_sought'])


class DamagedTargetTests(unittest.TestCase):
    """October 3: "come towards me" named a face id with characters missing; it was read as
    the microwave sought earlier and he turned away from the person."""
    def propose(self, call, drive):
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
                context = {'source_turn_id': 't', 'state_revision': store.revision, 'drive': drive}
                return state.propose_drive(call, context, Cancellation())
            finally:
                store.close()

    def drive(self):
        face = {'target_id': 'face-404ed8a94f984e8f89fb70bd91f9a53f', 'kind': 'face', 'bearing_deg': 3}
        return {'available': True, 'origin': 'requested', 'vision_seek': True, 'last_sought': 'microwave oven',
                'targets': [face, {'target_id': 'nearest_person', 'kind': 'nearest_person', 'visible_now': True}]}

    def test_a_face_id_with_missing_characters_still_means_that_face(self):
        from tests.test_drive_session import drive_call, step
        plan = self.propose(drive_call(step('approach', target='face-404ed8a94f984e8f89fbf9a53f')), self.drive())
        self.assertEqual(plan.steps[0].target, 'face-404ed8a94f984e8f89fb70bd91f9a53f')

    def test_an_unknown_target_is_refused_rather_than_becoming_the_old_search(self):
        from tests.test_drive_session import drive_call, step
        with self.assertRaisesRegex(ValueError, 'DriveTargetNotOffered'):
            self.propose(drive_call(step('approach', target='face-0000000000000000')), self.drive())

    def test_keep_going_and_a_spelling_of_sought_still_resume_the_search(self):
        from tests.test_drive_session import drive_call, step
        for name in ('sought', 'drive.last_sought'):
            plan = self.propose(drive_call(step('approach', target=name)), self.drive())
            self.assertEqual(plan.steps[0].target, 'sought')



class ComeHereTests(unittest.TestCase):
    """October 3, run aa6782c0: "come over here" was refused, then a second turn left him
    facing away and the approach gave up; near the person the firmware steered him aside."""
    def start(self, person, heard_bearing):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        world.people.append(('face-1', 'person-1', *person))
        settle()
        context = drive.context('requested', {'status': 'observed', 'bearing_deg': heard_bearing, 'ended_at': 1.})
        return clock, fake, drive, world, context

    def run_plan(self, clock, drive, context, *steps):
        targets = {t['target_id']: t for t in context['targets']}
        return drive.execute(DrivePlan('p', tuple(steps), 'requested', 'come here', targets, clock()+120), Cancellation())

    def distance(self, fake, person):
        return math.hypot(person[0] - fake.x, person[1] - fake.y)

    def test_come_here_with_nobody_in_view_turns_to_the_voice_and_arrives(self):
        person = (2.5*math.cos(math.radians(88)), 2.5*math.sin(math.radians(88)))   # Beside him, out of view.
        clock, fake, drive, world, context = self.start(person, 88.)
        nearest = next(t for t in context['targets'] if t['target_id'] == 'nearest_person')
        self.assertFalse(nearest['visible_now'])
        receipt = self.run_plan(clock, drive, context, DriveStep('approach', target='nearest_person'))
        self.assertEqual(receipt.status, 'completed', receipt)
        first = next(c for c in fake.commands if c['cmd'] == 'turn')
        self.assertAlmostEqual(first['deg'], 83., delta=1.)       # Toward the voice first (88, less the coast), not a sweep.
        self.assertLess(self.distance(fake, person), 1.35)

    def test_the_voice_stays_where_it_was_heard_after_he_turns_to_face_it(self):
        person = (2.5*math.cos(math.radians(88)), 2.5*math.sin(math.radians(88)))
        clock, fake, drive, world, context = self.start(person, 88.)
        self.run_plan(clock, drive, context, DriveStep('face', target='speaker'))
        self.assertAlmostEqual(fake.theta, 88., delta=3.)
        # The next request was spoken during the turn: no new bearing, the remembered one is offered.
        later = drive.context('requested', {'status': 'unavailable', 'bearing_deg': None, 'ended_at': 2.})
        speaker = next(t for t in later['targets'] if t['target_id'] == 'speaker')
        self.assertLess(abs(speaker['bearing_deg']), 5)           # Straight ahead now, not "88 degrees left".
        self.assertEqual(speaker['where'], 'straight ahead')
        # The same utterance's bearing is never applied twice against a new heading.
        again = drive.context('requested', {'status': 'observed', 'bearing_deg': 88., 'ended_at': 1.})
        self.assertLess(abs(next(t for t in again['targets'] if t['target_id'] == 'speaker')['bearing_deg']), 5)
        self.assertNotIn('speaker', [t['target_id'] for t in drive.context('self_initiated')['targets']])

    def test_the_logged_extra_turn_no_longer_strands_him_facing_away(self):
        person = (2.6, 0.)                                         # Already facing the person.
        clock, fake, drive, world, context = self.start(person, 0.)
        receipt = self.run_plan(clock, drive, context, DriveStep('turn', degrees=88.),
                                DriveStep('approach', target='nearest_person', stop_at_m=1.))
        self.assertEqual(receipt.status, 'completed', receipt)     # Was: refused, target_not_currently_detected.
        self.assertLess(self.distance(fake, person), 1.5)
        self.assertLess(abs(fake.theta), 25.)                      # Facing the person again.

    def test_nobody_anywhere_is_reported_after_looking_around(self):
        clock, fake, drive, world, context = self.start((2.5, 0.), 0.)
        world.people.clear()
        settle()
        receipt = self.run_plan(clock, drive, drive.context('requested'), DriveStep('approach', target='nearest_person'))
        self.assertEqual(receipt.steps[0]['reason'], 'no_person_found_after_looking_around', receipt)
        self.assertGreater(receipt.turned_deg, 90.)                # He did look around.
        self.assertEqual([c for c in fake.commands if c['cmd'] in {'move', 'drive'} and (c.get('lin') or c.get('dist'))], [])

    def test_firmware_steering_is_off_for_the_last_stretch_and_restored(self):
        person = (3.4, 0.)
        clock, fake, drive, world, context = self.start(person, 0.)
        gain = drive._assist_gain
        self.assertIsNotNone(gain)
        receipt = self.run_plan(clock, drive, context, DriveStep('approach', target='nearest_person', stop_at_m=1.))
        self.assertEqual(receipt.status, 'completed', receipt)
        steering = [(i, c['assist_gain']) for i, c in enumerate(fake.commands) if c['cmd'] == 'config' and 'max_lin' not in c]
        self.assertEqual([g for _, g in steering], [0., gain])     # Off once when close, back on at the end.
        first_drive = next(i for i, c in enumerate(fake.commands) if c['cmd'] == 'drive' and c['lin'] > 0)
        self.assertGreater(steering[0][0], first_drive)            # Far away the firmware still steers.
        rolled = sum(c['lin']*.15 for c in fake.commands[:steering[0][0]] if c['cmd'] == 'drive')
        self.assertGreater(rolled, .8)                             # Switched off about 1.2 m from the stop.
        self.assertTrue(drive._assist_on)

    def test_a_faint_bearing_pointing_away_from_the_person_in_view_does_not_move_the_target(self):
        # Run 30cbd025: "You tell me." gave three samples at 115 degrees left while the owner
        # sat straight ahead; he turned 115 degrees away and lost him.
        person = (2.5*math.cos(math.radians(73)), 2.5*math.sin(math.radians(73)))
        clock, fake, drive, world, context = self.start(person, 73.)
        self.run_plan(clock, drive, context, DriveStep('face', target='speaker'))
        settle()
        phantom = {'status': 'observed', 'bearing_deg': 115., 'ended_at': 2., 'samples': 6,
                   'clusters': [{'bearing_deg': 115., 'samples': 3, 'weight_share': .72}]}
        speaker = next(t for t in drive.context('requested', phantom)['targets'] if t['target_id'] == 'speaker')
        self.assertLess(abs(speaker['bearing_deg']), 10)           # Still where he is looking.
        before = fake.theta
        self.run_plan(clock, drive, drive.context('requested', phantom), DriveStep('face', target='speaker'))
        self.assertAlmostEqual(fake.theta, before, delta=10.)      # He does not turn away.
        # A strong, single-direction voice from elsewhere is believed: somebody else may be there.
        strong = {'status': 'observed', 'bearing_deg': -100., 'ended_at': 3., 'samples': 14,
                  'clusters': [{'bearing_deg': -100., 'samples': 14, 'weight_share': 1.}]}
        speaker = next(t for t in drive.context('requested', strong)['targets'] if t['target_id'] == 'speaker')
        self.assertAlmostEqual(speaker['bearing_deg'], -100, delta=3)

    def test_a_voice_that_agrees_with_a_visible_face_is_aimed_at_the_face(self):
        # At close range the array heard a centred face 25 to 38 degrees to the right, and
        # "face speaker" turned him off the person he was looking at.
        clock, fake, drive, world, context = self.start((1.2, 0.), -35.)
        speaker = next(t for t in context['targets'] if t['target_id'] == 'speaker')
        self.assertLess(abs(speaker['bearing_deg']), 5)
        self.assertEqual(speaker['basis'], 'voice_and_face')
        self.run_plan(clock, drive, context, DriveStep('face', target='speaker'))
        self.assertLess(abs(fake.theta), 8.)                        # No swing away from them.


class Router:
    """Stands in for the hosted look at the floor: scripted choices, recorded facts."""
    def __init__(self, *choices):
        self.choices, self.facts = list(choices), []

    @staticmethod
    def toward(bearing, distance, note='open floor beside it'):
        return {'way': True, 'x': .5 - bearing/90, 'y': .8, 'distance_m': distance, 'beyond_view': 'none',
                'confidence': 'high', 'note': note}

    NO_WAY = {'way': False, 'x': 0, 'y': 0, 'distance_m': 0, 'beyond_view': 'none', 'confidence': 'high',
              'note': 'no open floor in view'}

    def __call__(self, image, facts, cancel):
        self.facts.append(facts)
        return self.choices.pop(0) if self.choices else self.NO_WAY


def give_head(test, drive, world):
    """A head for the simulated robot: the camera turns with the neck, the locator sees
    through it, and every head pose is recorded (test.poses, test.looks)."""
    from r3x.drive_space import NECK_CENTER, NECK_PULSES_PER_DEG
    test.poses, test.looks = [], []
    class Head(TuckedArms):
        owner = type('Owner', (), {'neck_degrees': staticmethod(lambda: world.neck), 'held_channels': ()})()
        poses = test.poses
        def execute_pose(self, pose, cancel, request_id, **kwargs):
            test.poses.append(dict(pose))
            if 0 in pose:
                world.neck = (NECK_CENTER - pose[0]) / NECK_PULSES_PER_DEG
                test.looks.append(round(world.neck))
            return type('R', (), {'status': 'pulses_reached', 'reason': None})()
    drive.servos = Head()
    def locate(description, image, cancel, hint=None):
        test.calls.append(description)
        for _, label, px, py in world.objects:
            distance, bearing, _, _ = world.relative(px, py)
            bearing -= world.neck                       # The camera turns with the head.
            if label == description and abs(bearing) < 45:
                return {'found': True, 'x': .5 - bearing/90, 'y': .5, 'width': min(.9, .4/max(distance, .1)),
                        'height': .3, 'confidence': 'high', 'note': 'matched'}
        return {'found': False, 'x': 0, 'y': 0, 'width': 0, 'height': 0, 'confidence': 'low', 'note': 'not visible'}
    drive.locator = locate


class CameraRouteTests(SeekTests):
    """October 3: he saw the microwave over a counter that juts out and could not plan a way
    around it; he stopped at a table with open floor beside it."""
    def start(self, things):
        clock, fake, drive, world = super().start(things)
        give_head(self, drive, world)
        fake.tof.update(lf=600, lb=600, rf=600, rb=600)   # The range sensors alone show no way round.
        return clock, fake, drive, world

    def counter(self, world):
        # Juts out from the right across his path; open floor begins about 0.3 m to his left.
        return shaped_room(world, [(.9, y/10, .12) for y in range(-12, 3, 2)])

    def test_a_counter_between_him_and_the_thing_is_passed_on_the_floor_the_camera_shows(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = self.counter(world)
        settle()
        drive.router = Router(Router.toward(42., 1.3, 'open floor left of the counter'))
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        step = receipt.steps[-1]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual((step['detours'][0]['by'], step['detours'][0]['side']), ('camera', 'left'))
        self.assertGreater(fake.x, 3., receipt)                   # Past the counter and on to the door.
        facts = drive.router.facts[0]
        self.assertEqual(facts['goal'], 'door')
        self.assertLess(facts['blocked_ahead_m'], .8)
        bearings = {row['bearing_deg'] for row in facts['open_by_direction']}
        self.assertTrue({22.5, -22.5, 67.5, -157.5} <= bearings)   # Front, sides and behind, by direction.
        self.assertTrue(any(row['image_x'] is not None for row in facts['open_by_direction']))

    def test_without_a_way_in_the_picture_he_reports_blocked_and_does_not_wander(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = self.counter(world)
        settle()
        drive.router = Router()                                    # Always "no way".
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertEqual(receipt.steps[-1]['status'], 'blocked', receipt)
        self.assertEqual(len(drive.router.facts), 1)               # One look, not a look per try.
        self.assertLess(fake.x, .6)

    def test_he_tips_his_head_down_for_the_floor_look_and_levels_it_again(self):
        from r3x.servo_poses import DRIVE_HEAD
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = self.counter(world)
        settle()
        seen = []
        router = Router(Router.toward(42., 1.3))
        def watching(image, facts, cancel):
            seen.append(list(self.poses))
            return router(image, facts, cancel)
        drive.router = watching
        tipped, looked = [], []
        head, look = drive._head, drive.look
        def timed_head(pose, cancel, request_id):
            if 2 in pose and pose[2] < dict(DRIVE_HEAD)[2]:   # Head left falls when the head pitches down.
                tipped.append(clock())
            return head(pose, cancel, request_id)
        def timed_look(after):
            looked.append((after, clock()))
            return look(after)
        drive._head, drive.look = timed_head, timed_look
        self.run_plan(clock, drive, DriveStep('seek', description='door'), DriveStep('approach', target='sought'))
        level = dict(DRIVE_HEAD)[2]
        self.assertLess(seen[0][-2][2], level)                     # Head-left pulse below level looks down...
        self.assertEqual(seen[0][-1][2], level)                    # ...and is level again before he drives on.
        # Owner, October 5: the head needs about 1.5 s after tipping before the picture is clear.
        after, at = next((a, t) for a, t in looked if tipped and a >= tipped[0])
        self.assertGreaterEqual(after, tipped[0] + 1.5 - 1e-6)
        self.assertGreaterEqual(at, tipped[0] + 1.5 - 1e-6)

    def test_a_picture_asked_for_after_the_drive_holds_idle_motion_until_it_is_taken(self):
        clock, fake, drive, world = self.start([])
        flags = []
        class Head(TuckedArms):
            def finish_base_motion(self, look_pending=False, keep_parked=frozenset()):
                flags.append(look_pending)
        drive.servos = Head()
        plan = DrivePlan('p', (DriveStep('move', meters=.3),), 'requested', 'go', {}, clock()+60, look_after='What is there?')
        drive.execute(plan, Cancellation())
        self.run_plan(clock, drive, DriveStep('move', meters=.3))
        self.assertEqual(flags, [True, False])

    def test_a_leg_that_fails_is_told_to_the_next_look(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = self.counter(world)
        settle()
        # First he is sent right, into the counter; the second look gets told how that went.
        drive.router = Router(Router.toward(-30., 1.), Router.toward(42., 1.3))
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        self.assertGreaterEqual(len(drive.router.facts), 2, receipt)
        self.assertIn('30 degrees right', drive.router.facts[1]['already_tried'][0])

    def test_a_table_before_the_person_is_passed_and_he_turns_back_to_them(self):
        clock, fake, drive, world = self.start([])
        world.people.append(('face-1', None, 3., 0.))
        world.objects.append(('table', 'table', 1.6, 0.))
        settle()
        drive.router = Router(Router.toward(-40., 1.2, 'open floor right of the table'))
        plan = DrivePlan('p', (DriveStep('approach', target='face-1'),), 'requested', 'come here',
                         {'face-1': {'target_id': 'face-1', 'kind': 'face'}}, clock()+120)
        receipt = drive.execute(plan, Cancellation())
        step = receipt.steps[0]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertEqual([(d['by'], d['side']) for d in step['detours']], [('camera', 'right')])
        self.assertEqual(drive.router.facts[0]['goal'], 'the person he is driving to')
        self.assertLess(math.hypot(3. - fake.x, fake.y), 1.5)
        self.assertLess(fake.y, -.3)                               # He came round the right-hand side.
        bearing = world.relative(3., 0.)[1]
        self.assertLess(abs(bearing), 20., receipt)                # And ended facing the person.

    def test_route_looks_count_against_the_plans_look_allowance(self):
        from r3x.drive import PLAN_VISION_CALLS
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = self.counter(world)
        settle()
        drive.router = Router(Router.toward(42., 1.3))
        self.run_plan(clock, drive, DriveStep('seek', description='door'), DriveStep('approach', target='sought'))
        self.assertEqual(drive._vision_calls, len(self.calls) + len(drive.router.facts))
        self.assertLessEqual(drive._vision_calls, PLAN_VISION_CALLS)


class OwnPositionTests(SeekTests):
    """He keeps his own position and heading as he moves, so he can turn back to a target."""
    def test_position_follows_turns_and_moves_even_when_wheel_heading_slips(self):
        clock, fake, drive, world = self.start([])
        fake.odom_slip = .8                                         # Wheel odometry over-reads every turn.
        self.run_plan(clock, drive, DriveStep('move', meters=1.), DriveStep('turn', degrees=90.),
                      DriveStep('move', meters=.5), DriveStep('turn', degrees=90.), DriveStep('move', meters=.4))
        drive._track()
        self.assertAlmostEqual(drive._at[0], fake.x, delta=.08)     # About (0.6, 0.5).
        self.assertAlmostEqual(drive._at[1], fake.y, delta=.08)
        bearing, distance = drive._bearing_to((0., 0.))
        self.assertAlmostEqual(distance, math.hypot(fake.x, fake.y), delta=.1)
        self.assertAlmostEqual(bearing, world.relative(0., 0.)[1], delta=6.)   # Where the start is from here.

    def test_three_agreeing_sightings_fix_where_the_thing_is_and_two_do_not(self):
        # October 3 (eef0a6ef): a position from two sight lines a metre apart sent him turning
        # the wrong way after every leg. Three lines that agree are needed; noisy ones give none.
        from r3x.drive import DriveBase
        thing = (3., 1.)
        def line(x, y, error=0.):
            return (x, y, math.degrees(math.atan2(thing[1] - y, thing[0] - x)) + error)
        self.assertIsNone(DriveBase._fixed_position([line(0., 0.), line(1., -1.)]))
        met = DriveBase._fixed_position([line(0., 0.), line(1., -1.), line(1.5, .8)])
        self.assertAlmostEqual(met[0], thing[0], places=2)
        self.assertAlmostEqual(met[1], thing[1], places=2)
        self.assertIsNone(DriveBase._fixed_position([line(0., 0.), line(1., -1., 25.), line(1.5, .8)]))   # They disagree.
        self.assertIsNone(DriveBase._fixed_position([line(0., 0.), line(.1, 0.), line(.2, 0.)]))          # No spread.

    def test_once_its_place_is_fixed_he_knows_where_it_is_from_anywhere(self):
        clock, fake, drive, world = self.start([('door', 'door', 3., 0.)])
        self.run_plan(clock, drive, DriveStep('seek', description='door'))            # First sighting, from the start.
        for sideways in (1.2, 1.0):                                                    # Two more, each further left.
            self.run_plan(clock, drive, DriveStep('turn', degrees=90.), DriveStep('move', meters=sideways),
                          DriveStep('turn', degrees=-90.))
            drive._sought['slack'] = 90.
            found, why = drive._locate('door', Cancellation(), drive._sought)
            self.assertIsNotNone(found, why)
            drive._remember_sought('door', found)
        self.assertAlmostEqual(drive._sought['position'][0], 3., delta=.35)
        self.assertAlmostEqual(drive._sought['position'][1], 0., delta=.35)
        self.run_plan(clock, drive, DriveStep('turn', degrees=30.))                    # Look away...
        self.assertAlmostEqual(drive._expected_sought(), world.relative(3., 0.)[1], delta=8.)   # ...and know where it is.


class OpenDirectionTests(unittest.TestCase):
    def test_every_sensor_is_placed_by_bearing_and_where_it_falls_in_the_picture(self):
        from r3x.drive_space import open_directions
        rows = open_directions({'received_at': 0., 'tof_mm': LIVE_TOF}, {'received_at': 0., 'grid': LIVE_GRID, 'rej': [4095]*8}, 0.)
        by = {row['bearing_deg']: row for row in rows}
        self.assertEqual(len(rows), 16)                             # Eight matrix columns and eight ring sensors.
        self.assertEqual(by[-22.5]['open_m'], .4)                   # The wall at his front right...
        self.assertEqual(by[-22.5]['image_x'], .75)                 # ...three quarters of the way across the picture.
        self.assertGreaterEqual(by[2.8]['open_m'], 3.5)             # Straight ahead is open.
        self.assertIsNone(by[112.5]['image_x'])                     # Behind-left is outside the camera view.
        self.assertEqual(by[112.5]['open_m'], .26)
        self.assertEqual(open_directions(None, None, 0.), [])


class LookBackTests(SeekTests):
    """October 3 (eef0a6ef): after each leg he swung his whole body back to a predicted
    bearing, looked twice at that one spot and gave up; 657 degrees of turning in one trip."""
    def start(self, things):
        clock, fake, drive, world = super().start(things)
        give_head(self, drive, world)
        return clock, fake, drive, world

    def aside(self, clock, drive, degrees):
        """Seen straight ahead, then turned away as a leg around something would leave him."""
        self.run_plan(clock, drive, DriveStep('seek', description='door'))
        self.run_plan(clock, drive, DriveStep('turn', degrees=degrees))
        return DrivePlan('p', (), 'requested', 'x', {}, clock()+120)

    def test_the_head_looks_back_and_the_body_does_not_turn(self):
        clock, fake, drive, world = self.start([('door', 'door', 3., 0.)])
        plan = self.aside(clock, drive, 50.)
        turns = len([c for c in fake.commands if c['cmd'] == 'turn'])
        found, why, swung = drive._find_again(plan, Cancellation(), shifted=1)
        self.assertIsNotNone(found, why)
        self.assertAlmostEqual(found['bearing_deg'], -50., delta=4.)       # Base-relative, though seen with the head turned.
        self.assertEqual((swung, len([c for c in fake.commands if c['cmd'] == 'turn'])), (0., turns))
        self.assertEqual(self.looks, [-40, 0])                              # Looked right, then centred again.

    def test_when_it_is_not_where_expected_he_looks_to_the_other_side_before_giving_up(self):
        clock, fake, drive, world = self.start([('door', 'door', 3., 0.)])
        plan = self.aside(clock, drive, 50.)
        world.objects[:] = [('door', 'door', 3.*math.cos(math.radians(70)), 3.*math.sin(math.radians(70)))]   # 20 degrees LEFT of him.
        found, why, swung = drive._find_again(plan, Cancellation(), shifted=1)
        self.assertIsNotNone(found, why)
        self.assertAlmostEqual(found['bearing_deg'], 20., delta=4.)
        self.assertEqual(self.looks[:2], [-40, 0])                          # Right first, then the other way.
        self.assertEqual(swung, 0.)

    def test_far_behind_him_the_body_turns_once_then_the_head_looks(self):
        clock, fake, drive, world = self.start([('door', 'door', 3., 0.)])
        plan = self.aside(clock, drive, 110.)
        found, why, swung = drive._find_again(plan, Cancellation(), shifted=1)
        self.assertIsNotNone(found, why)
        self.assertGreater(swung, 90.)
        self.assertLess(abs(found['bearing_deg']), 15.)

    def test_nowhere_in_three_looks_is_reported_once_not_looked_at_twice_in_one_spot(self):
        clock, fake, drive, world = self.start([('door', 'door', 3., 0.)])
        plan = self.aside(clock, drive, 50.)
        world.objects.clear()
        before = len(self.calls)
        found, why, swung = drive._find_again(plan, Cancellation(), shifted=1)
        self.assertIsNone(found)
        self.assertEqual(len(self.calls) - before, 2)                       # Two different views cover both sides.
        self.assertEqual(world.neck, 0.)

    def test_a_drive_look_starts_no_pose_hold(self):
        from tests.test_servos import Clock as ServoClock, PulsePort
        from r3x.robot_servos import RobotServos
        servo_clock, port = ServoClock(), PulsePort()
        robot = RobotServos('fake', open_port=lambda: port, clock=servo_clock, wait=servo_clock.wait)
        robot.owner.wait = servo_clock.wait
        robot.start()
        self.addCleanup(robot.close)
        receipt = robot.drive_look({0: 4000}, Cancellation(), 'drive-look')
        self.assertEqual(receipt.status, 'pulses_reached')
        self.assertFalse(robot.owner.human_hold_active())                   # Rex's own moves are not locked out.

    def test_turns_end_where_asked_despite_the_coast(self):
        clock, fake, drive, world = self.start([])
        for degrees in (90., -45., 30., -20.):
            before = fake.theta
            receipt = self.run_plan(clock, drive, DriveStep('turn', degrees=degrees))
            self.assertAlmostEqual(fake.theta - before, degrees, delta=1.5, msg=receipt)
        self.assertTrue(all(abs(c['deg']) < abs(want) for c, want in
                            zip([c for c in fake.commands if c['cmd'] == 'turn'], (90., 45., 30., 20.))))

    def test_his_own_arrival_near_something_is_not_someone_stepping_close(self):
        clock, fake, drive, world = self.start([])
        fake.tof.update(fl=1500, fr=1500, fl_radial=1500, fr_radial=1500)
        settle()
        for _ in range(3):
            drive.tick_reflex(); clock.wait(1.)
        self.run_plan(clock, drive, DriveStep('turn', degrees=30.))        # He moved himself...
        fake.tof.update(fl=120, fr=120, fl_radial=120, fr_radial=120)       # ...and now stands 12 cm from a counter.
        settle()
        moves = len([c for c in fake.commands if c['cmd'] == 'move'])
        for _ in range(5):
            self.assertIsNone(drive.tick_reflex()); clock.wait(1.); settle()
        self.assertEqual(len([c for c in fake.commands if c['cmd'] == 'move']), moves)   # No backing away on his own.



class ArrivalTests(SeekTests):
    """October 3 evening (b0d3ef1d, 7af54a94): he reached the microwave, then spent up to 75
    seconds looking for a way round the counter beneath it; one arrival was told as a failure."""
    def start(self, things):
        clock, fake, drive, world = super().start(things)
        give_head(self, drive, world)
        fake.tof.update(lf=600, lb=600, rf=600, rb=600)
        return clock, fake, drive, world

    def test_a_thing_that_has_grown_large_in_view_is_reached_whatever_stops_him(self):
        clock, fake, drive, world = self.start([('microwave', 'microwave', 4., 0.)])
        # A counter under it juts in from the right: a side return, never the centre band.
        fake.sensors = shaped_room(world, [(3.3, y/10, .12) for y in range(-12, -3, 2)])
        settle()
        drive.router = Router()
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='microwave'),
                                DriveStep('approach', target='sought'))
        step = receipt.steps[-1]
        self.assertEqual((step['status'], step['reason']), ('completed', 'reached_stand_off_at_target'), receipt)
        self.assertGreaterEqual(step['grew_in_view'], 2.)
        self.assertGreater(step['clearance_at_stop_m']['front_center_m'], 3.)   # Nothing straight ahead: the old rule said "go around".
        self.assertLessEqual(len(drive.router.facts), 1)           # No hunting for a way round what it sits on.

    def test_a_thing_that_only_looks_big_from_afar_is_still_not_arrival(self):
        clock, fake, drive, world = self.start([('door', 'door', 4., 0.)])
        seen = drive.locator
        drive.locator = lambda *a, **k: {**seen(*a, **k), 'width': .4} if seen(*a, **k)['found'] else seen(*a, **k)
        fake.sensors = shaped_room(world, [(.9, y/10, .12) for y in range(-12, -2, 2)])   # A corner in the right of his path, near the start.
        settle()
        drive.router = Router()
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        claimed = receipt.steps[-1]['reason'] == 'reached_stand_off_at_target'
        self.assertFalse(claimed and fake.x < 2., receipt)         # Never "arrived" back at the corner, 3 m short.

    def test_the_same_failed_way_from_the_same_spot_is_not_tried_twice(self):
        clock, fake, drive, world = self.start([('door', 'door', 4.5, 0.)])
        fake.sensors = shaped_room(world, [(.9, y/10, .12) for y in range(-12, 13, 2)])   # A wall straight across.
        settle()
        drive.router = Router(*[Router.toward(-30., 1.)]*4)        # It keeps saying "right".
        receipt = self.run_plan(clock, drive, DriveStep('seek', description='door'),
                                DriveStep('approach', target='sought'))
        turns = [c for c in fake.commands if c['cmd'] == 'turn' and c['deg'] < -20]
        self.assertLessEqual(len(turns), 1, receipt)               # Turned that way once, not four times.
        self.assertTrue(any('not repeated' in line for facts in drive.router.facts for line in facts['already_tried']) or
                        len(drive.router.facts) <= 2)


class WithinReachTests(unittest.TestCase):
    def start(self):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        drive.servos = TuckedArms()
        return clock, fake, drive, world

    def test_stopped_by_a_table_a_step_from_the_person_is_arrival(self):
        # October 3: 1.0, 1.2 and 0.9 m from the person were each reported as not making it.
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 3., 0.))
        world.objects.append(('table', 'table', 2.4, 0.))          # Its near edge stops him about 1.2 m from the face.
        fake.tof.update(lf=300, lb=300, rf=300, rb=300)
        settle()
        plan = DrivePlan('p', (DriveStep('approach', target='face-1'),), 'requested', 'come here',
                         {'face-1': {'target_id': 'face-1', 'kind': 'face'}}, clock()+120)
        receipt = drive.execute(plan, Cancellation())
        step = receipt.steps[0]
        self.assertEqual(step['status'], 'completed', receipt)
        self.assertIn('within reach', step['reason'])
        self.assertLessEqual(step['final_approx_range_m'], 1.3)

    def test_a_long_approach_is_given_time_to_finish(self):
        clock, fake, drive, world = self.start()
        world.people.append(('face-1', None, 7.5, 0.))
        drive.faces = lambda now: [f for f in world.faces(now)]
        settle()
        plan = DrivePlan('p', (DriveStep('approach', target='face-1', pace='slow'),), 'requested', 'come here',
                         {'face-1': {'target_id': 'face-1', 'kind': 'face'}}, clock()+300)
        receipt = drive.execute(plan, Cancellation())
        self.assertEqual(receipt.steps[0]['status'], 'completed', receipt)   # 6.5 m at the slow pace: over 40 s.
        self.assertGreater(receipt.finished_at - receipt.started_at, 25.)
