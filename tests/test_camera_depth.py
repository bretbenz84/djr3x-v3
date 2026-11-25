"""What the head camera sees beside his path (owner, November 24, run d085dc5f): rolling toward the hallway's doorway,
steering left, his left side ran into the hall's corner, between the depth grid's view and the side beam.

Synthetic camera views and depth grids, a stub depth model and the simulated base; no camera, model or wheels.
They prove the arithmetic and the drive's decisions, not that Depth Anything reads this house's corners."""
import math
import threading
import time
import unittest
from types import SimpleNamespace

from r3x.camera_depth import CameraDepth, FRESH_S
from r3x.contracts import Cancellation, ProviderFailure
from r3x.drive import DrivePlan, DriveStep
from r3x.drive_space import (CORNER_STOP_M, DEPTH_GRID_HALF_DEG, arc_gap, MATRIX_COLUMN_DEG, RING_RADIUS,
                             camera_fit, camera_points, corner_gaps, corner_steer)
from tests.test_drive import base_setup, settle


def grid(columns_m):
    """A depth grid frame whose eight columns each read one distance (metres from the sensor), every row."""
    return {'grid': [int(columns_m[i % 8] * 1000) for i in range(64)], 'rej': [4095] * 8, 'received_at': 0.}


def view(distance, neck=0., scale=2., shift=.3, at=0.):
    """A camera view whose disparity is scale/distance + shift, distance(bearing) from the ring's centre."""
    columns = []
    for i in range(36):
        bearing = 45. - 2.5 * (i + .5)
        columns.append((bearing, scale / distance(bearing + neck) + shift))
    return {'at': at, 'neck': neck, 'frame_id': 'f', 'columns': columns}


COLUMNS_M = (2.0, 2.2, 2.6, 3.0, 3.0, 2.6, 2.2, 2.0)


def scene(corner=None):
    """Distances by base bearing: the grid's columns in the middle, a corner if any, open floor elsewhere."""
    def distance(bearing):
        if corner is not None and abs(bearing - corner[0]) <= 4.:
            return corner[1]
        if abs(bearing) <= DEPTH_GRID_HALF_DEG:
            column = min(range(8), key=lambda c: abs(MATRIX_COLUMN_DEG[c] - bearing))
            return COLUMNS_M[column] + RING_RADIUS
        return 3.3
    return distance


class CameraArithmeticTests(unittest.TestCase):
    def test_the_depth_grid_puts_real_distances_on_the_camera_view(self):
        frame = view(scene((38., .6)), scale=3.1, shift=-.2)
        a, b = camera_fit(frame, grid(COLUMNS_M))
        self.assertAlmostEqual(1. / (a * (3.1 / 2.27 - .2) + b), 2.27, delta=.02)
        points = camera_points(frame, grid(COLUMNS_M))
        self.assertTrue(points)
        for x, y in points:                                   # Only the corner: open floor is too far to matter.
            self.assertAlmostEqual(math.hypot(x, y), .6, delta=.02)
            self.assertAlmostEqual(math.degrees(math.atan2(y, x)), 38., delta=5.)

    def test_the_head_turned_moves_what_it_sees_with_it(self):
        frame = view(scene((-50., .7)), neck=-20.)
        points = camera_points(frame, grid(COLUMNS_M))
        self.assertTrue(points)
        self.assertTrue(all(-55. < math.degrees(math.atan2(y, x)) < -45. for x, y in points))

    def test_what_the_grid_sees_itself_is_left_to_the_grid(self):
        frame = view(scene((10., .5)))                         # Inside the grid's own view.
        self.assertEqual(camera_points(frame, grid(COLUMNS_M)), [])

    def test_no_distances_without_returns_at_different_ranges(self):
        frame = view(scene((38., .6)))
        self.assertIsNone(camera_fit(frame, grid((3.,) * 8)))   # One wall square-on: scale and offset unknown.
        self.assertIsNone(camera_fit(frame, grid((5.,) * 8)))   # Nothing in range.
        self.assertEqual(camera_points(frame, grid((3.,) * 8)), [])
        # A view that disagrees with the grid (nearer in the picture, farther on the grid) is not used.
        backwards = view(lambda b: 1. / scene()(b))
        self.assertIsNone(camera_fit(backwards, grid(COLUMNS_M)))
        self.assertIsNone(camera_fit(None, grid(COLUMNS_M)))

    def test_a_turn_toward_something_beside_his_corner_is_cut_and_a_turn_away_never_is(self):
        corner = [(.6, .42)]                                  # Front left, a hand's width off the straight path.
        self.assertGreater(arc_gap(corner, 0., .2), CORNER_STOP_M)          # Straight on, he passes it...
        self.assertLess(arc_gap(corner, .3, .1), .02)                        # ...a slow turn left all but touches it.
        steered = corner_steer(.3, .1, corner)
        self.assertLess(steered, .3)
        self.assertGreaterEqual(arc_gap(corner, steered, .1), CORNER_STOP_M)
        self.assertEqual(corner_steer(-.3, .1, corner), -.3)               # Away: untouched.
        self.assertEqual(corner_steer(.3, .1, []), .3)                       # Nothing seen: untouched.
        # Already closer than the margin rolling straight: no turn that brings it closer still.
        tight = [(.3, .3)]
        self.assertGreaterEqual(arc_gap(tight, corner_steer(.2, .1, tight), .1), arc_gap(tight, 0., .1) - 1e-9)
        # The side beam toward his front counts; something on the straight path's centre is the front's business.
        self.assertIn(-1, corner_gaps([], {'rf': 150}))
        self.assertEqual(corner_gaps([(.8, 0.)]), {})


class CameraDepthWorkerTests(unittest.TestCase):
    def test_a_model_file_that_is_not_the_checked_one_is_refused(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / 'model.onnx'
            path.write_bytes(b'not a model')
            with self.assertRaises(ProviderFailure):
                CameraDepth(SimpleNamespace(hub=None), path).start()

    def test_it_reads_frames_only_while_the_wheels_roll_and_a_view_goes_stale(self):
        frames = iter(range(10 ** 6))
        camera = SimpleNamespace(hub=SimpleNamespace(peek=lambda: SimpleNamespace(frame_id=next(frames), at=time.monotonic() - .01)))
        depth = CameraDepth(camera, None)
        depth.session = object()                               # Loaded (the stub below never uses it).
        depth.columns = lambda frame: [(0., 1.)]
        rolling = threading.Event()
        depth.active, depth.neck = rolling.is_set, lambda: 12.
        depth.start()
        self.addCleanup(depth.close)
        time.sleep(.3)
        self.assertIsNone(depth.latest())                      # Standing still: no work at all.
        self.assertEqual(depth.frames, 0)
        rolling.set()
        deadline = time.monotonic() + 3.
        while depth.frames == 0 and time.monotonic() < deadline:
            time.sleep(.02)
        latest = depth.view
        self.assertEqual((latest['neck'], latest['columns']), (12., [(0., 1.)]))
        rolling.clear()
        time.sleep(.2)
        self.assertIsNone(depth.latest(latest['at'] + FRESH_S + .1))


class CornerReplayTests(unittest.TestCase):
    """The hallway corner, replayed in the simulated base: a doorway ahead and a little to his left, a corner just
    off his straight path. The depth grid and the side beam each see it only inside their own cones; between them it
    is out of their sight. Steering left toward the doorway sweeps him into it."""
    CORNER = (.6, .34)       # Room metres: just to his left, a few centimetres outside the straight path.
    TARGET = (1.6, .5)       # The doorway, 17 degrees to his left and near: he crawls, steering ever more left.

    def replay(self, camera):
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        closest = [float('inf')]
        def corner():
            distance, bearing, forward, _ = world.relative(*self.CORNER)
            return (bearing, distance) if forward > 0 else None
        def sensors():
            closest[0] = min(closest[0], math.hypot(self.CORNER[0] - fake.x, self.CORNER[1] - fake.y))
            seen = corner()
            beams = {}
            for key, centre in (('lf', 67.5), ('rf', -67.5), ('fl_radial', 22.5), ('fr_radial', -22.5)):
                beams[key] = (int((seen[1] - RING_RADIUS) * 1000) if seen and abs(seen[0] - centre) <= 12.5
                              else 3000 if 'radial' in key else 1500)
            columns = [seen[1] - RING_RADIUS if seen and abs(seen[0] - MATRIX_COLUMN_DEG[c]) <= 3. else COLUMNS_M[c]
                       for c in range(8)]
            return beams, [int(columns[i % 8] * 1000) for i in range(64)]
        fake.sensors = sensors
        fake.tof.update(rf=400, rb=400)   # A wall 0.4 m to his right (run d085dc5f: tight space, a slow crawl).
        fake.assist_on = False
        drive.look = lambda after: SimpleNamespace(frame_id='f', detail='high')
        def locate(description, image, cancel, hint=None):
            distance, bearing, _, _ = world.relative(*self.TARGET)
            return {'found': abs(bearing) < 45, 'x': .5 - bearing / 90, 'y': .5, 'width': .12, 'height': .5,
                    'confidence': 'high', 'note': 'an open doorway'}
        drive.locator = locate
        if camera:
            drive.camera_depth = SimpleNamespace(latest=lambda now=None: view(scene(corner()), at=clock()))
        settle()
        drive._sought = {'description': 'the doorway', 'heading': 17., 'seen_at': clock()}
        receipt = drive.execute(DrivePlan('p', (DriveStep('approach', target='sought', description='the doorway'),),
                                          'requested', 'go to the doorway', {}, clock() + 120), Cancellation())
        return receipt, closest[0]

    def test_without_the_camera_the_turn_toward_the_doorway_sweeps_him_into_the_corner(self):
        # The side beam sees it only once it is beside him: too late, as in run d085dc5f.
        receipt, closest = self.replay(camera=False)
        self.assertLess(closest, RING_RADIUS, receipt)

    def test_with_the_camera_he_does_not_turn_into_what_only_it_sees(self):
        receipt, closest = self.replay(camera=True)
        self.assertGreater(closest, RING_RADIUS + .03, receipt)
