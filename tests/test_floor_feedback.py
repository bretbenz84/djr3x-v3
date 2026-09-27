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
