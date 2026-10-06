"""Deliberate camera looks pick a still frame: the least shaken of a short run."""
import unittest

from r3x.looking import fresh_look, sharpness
from r3x.vision import Frame


def frame(frame_id, at, noise, width=64, height=48):
    """A frame with fine detail (noise) is sharp; a flat one is what shake looks like.
    Pixels are built without numpy: the contract runner blocks native libraries."""
    import random
    rng = random.Random(1)
    pixels = bytes(min(255, max(0, 120 + (rng.randint(-noise, noise) if noise else 0)))
                   for _ in range(width*height*3))
    return Frame(frame_id, 'cam', 'session', at, width, height, pixels)


def detail(frame):
    """A stand-in sharpness score: how varied the pixels are."""
    values = frame.pixels[::7]
    mean = sum(values)/len(values)
    return sum((v - mean)**2 for v in values)/len(values)


def native_available():
    try:
        import cv2, numpy   # noqa: F401
        return True
    except Exception:       # Blocked by the contract runner, or not installed.
        return False


class Clock:
    def __init__(self):
        self.now = 10.
    def __call__(self):
        return self.now
    def wait(self, seconds):
        self.now += seconds


class Hub:
    """Frames appear at their own times, like the camera's latest-frame mailbox."""
    def __init__(self, clock, frames):
        self.clock, self.frames = clock, frames
    def peek(self):
        ready = [f for f in self.frames if f.at <= self.clock()]
        return ready[-1] if ready else None


class Camera:
    def __init__(self, hub):
        self.hub = hub


class FreshLookTests(unittest.TestCase):
    def setUp(self):
        # Encoding needs cv2, which the contract runner blocks: the frame is passed through.
        from unittest.mock import patch
        from r3x.scene import SceneImage
        patcher = patch('r3x.looking.sharp_image', side_effect=lambda f: SceneImage(f.frame_id, f.at, 'synthetic', 'high'))
        patcher.start()
        self.addCleanup(patcher.stop)

    @unittest.skipUnless(native_available(), 'cv2/numpy unavailable (blocked under the contract runner)')
    def test_sharpness_orders_a_detailed_frame_above_a_flat_one(self):
        self.assertGreater(sharpness(frame('a', 0., 40)), sharpness(frame('b', 0., 0)))

    def test_a_frame_whose_sharpness_cannot_be_judged_still_serves(self):
        clock = Clock()
        def broken(frame):
            raise RuntimeError('native library blocked')
        image = fresh_look(Camera(Hub(clock, [frame('only', 10.1, 10)])), after=10., timeout=2., clock=clock,
                           wait=clock.wait, score=broken)
        self.assertEqual(image.frame_id, 'only')

    def test_the_least_shaken_frame_of_the_steady_window_is_used(self):
        clock = Clock()
        frames = [frame('old', 9.9, 60), frame('shaky', 10.5, 2), frame('still', 10.6, 50), frame('late', 11.5, 80)]
        image = fresh_look(Camera(Hub(clock, frames)), after=10.4, timeout=3., clock=clock, wait=clock.wait, score=detail)
        self.assertEqual(image.frame_id, 'still')          # Not the first good frame, not one before `after`.
        self.assertLess(clock.now, 11.5)                   # And it did not wait for the whole timeout.

    def test_only_settled_frames_count_and_nothing_within_the_timeout_is_none(self):
        clock = Clock()
        frames = [frame('moving', 10.2, 50), frame('settled', 10.9, 30)]
        image = fresh_look(Camera(Hub(clock, frames)), after=10., timeout=3., clock=clock, wait=clock.wait,
                           valid_frame=lambda at: at >= 10.8, score=detail)
        self.assertEqual(image.frame_id, 'settled')
        clock = Clock()
        self.assertIsNone(fresh_look(Camera(Hub(clock, [frame('x', 20., 50)])), after=10., timeout=1., clock=clock,
                                     wait=clock.wait, score=detail))


if __name__ == '__main__':
    unittest.main()
