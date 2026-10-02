"""Deliberate camera looks: a fresh, sharp frame for hosted vision.

Background scene snapshots stay at 480 px, low detail (cost). A look that Rex chose
(an inspection, a look after driving, a search) needs detail: on the wide-angle
head camera a framed photo across the room is only a few pixels in a 480 px image.
"""
import base64
import time

from .scene import SceneImage

LONG_EDGE = 1280
JPEG_QUALITY = 85


def sharp_image(frame, long_edge=LONG_EDGE):
    import cv2
    import numpy as np
    pixels = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    ratio = min(1., long_edge / max(frame.width, frame.height))
    if ratio < 1:
        pixels = cv2.resize(pixels, (round(frame.width*ratio), round(frame.height*ratio)), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode('.jpg', pixels, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    if not ok or len(data) > 1_100_000:
        raise ValueError('LookImageTooLarge')
    return SceneImage(frame.frame_id, frame.at, 'data:image/jpeg;base64,' + base64.b64encode(data).decode(), 'high')


def fresh_look(camera, after, timeout=3., clock=time.monotonic, wait=time.sleep, valid_frame=None):
    """A sharp image of a frame captured at or after `after` (settled view), or None."""
    deadline = clock() + timeout
    while clock() < deadline:
        frame = camera.hub.peek()
        if frame is not None and frame.at >= after and (valid_frame is None or valid_frame(frame.at)):
            return sharp_image(frame)
        wait(.05)
    return None
