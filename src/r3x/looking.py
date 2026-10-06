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


STEADY_S = .3          # Frames gathered after the first good one; the least shaken is kept.
SHARPNESS_EDGE = 480   # Sharpness is judged on a small grey copy: cheap, and relative between frames.


def sharpness(frame, long_edge=SHARPNESS_EDGE):
    """Variance of the Laplacian of a small grey copy: higher is sharper. Only comparable
    between frames of the same view (a blank wall scores low however still the head)."""
    import cv2
    import numpy as np
    pixels = np.frombuffer(frame.pixels, dtype=np.uint8).reshape(frame.height, frame.width, 3)
    ratio = min(1., long_edge / max(frame.width, frame.height))
    if ratio < 1:
        pixels = cv2.resize(pixels, (round(frame.width*ratio), round(frame.height*ratio)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(cv2.cvtColor(pixels, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def fresh_look(camera, after, timeout=3., clock=time.monotonic, wait=time.sleep, valid_frame=None, steady=STEADY_S,
               score=None):
    """A sharp image of a frame captured at or after `after` (settled view), or None.

    October 5: pictures taken right after the head tipped down were taken while it was
    still shaking. Once a frame qualifies, frames keep coming for `steady` seconds and the
    least blurred of them is the one used. A frame whose sharpness cannot be judged
    scores zero, so the first good frame still serves."""
    score = score or sharpness
    def judged(frame):
        try:
            return score(frame)
        except Exception:
            return 0.
    deadline = clock() + timeout
    best, best_score, seen, until = None, None, set(), None
    while clock() < deadline:
        frame = camera.hub.peek()
        if (frame is not None and frame.frame_id not in seen and frame.at >= after
                and (valid_frame is None or valid_frame(frame.at))):
            seen.add(frame.frame_id)
            score_value = judged(frame) if steady > 0 else 0.
            if best is None or score_value > best_score:
                best, best_score = frame, score_value
            if until is None:
                until = clock() + steady
        if until is not None and clock() >= until:
            break
        wait(.05)
    return sharp_image(best) if best is not None else None
