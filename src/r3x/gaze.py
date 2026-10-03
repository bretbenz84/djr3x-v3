"""Where the head points while Rex replies: the addressee, not a separate authority.

Speaker attribution is not changed here; this only aims the talking-motion anchor.
Priority: the attributed speaker's visible face, then the microphone-array bearing
of the last utterance, then the largest visible face. Nothing to look at: None.
"""
from .drive_space import NECK_CENTER, NECK_PULSES_PER_DEG, image_bearing

MAX_GAZE_DEG = 40.


def addressee_neck(faces, neck_deg, speaker_id=None, speaker_direction=None):
    face = next((f for f in faces if speaker_id and f.get('identity') == speaker_id), None)
    if face is not None:
        bearing = image_bearing(face['box'], neck_deg)
    elif speaker_direction and speaker_direction.get('status') == 'observed' and speaker_direction.get('bearing_deg') is not None:
        bearing = float(speaker_direction['bearing_deg'])
    elif faces:
        bearing = image_bearing(max(faces, key=lambda f: f['box'][2]*f['box'][3])['box'], neck_deg)
    else:
        return None
    bearing = max(-MAX_GAZE_DEG, min(MAX_GAZE_DEG, bearing))
    return round(NECK_CENTER - bearing*NECK_PULSES_PER_DEG)


def face_aim(faces, neck_deg):
    """Where the idle gaze finds the nearest (largest) face: the neck pulse centring it,
    how far down the frame it sits (0 top, 1 bottom) and when it was seen."""
    if not faces:
        return None
    face = max(faces, key=lambda f: f['box'][2]*f['box'][3])
    return {'neck': addressee_neck([face], neck_deg), 'y': face['box'][1] + face['box'][3]/2, 'at': face['at']}
