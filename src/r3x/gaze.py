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
    return aim_at(face, neck_deg)


def aim_at(face, neck_deg):
    return {'neck': addressee_neck([face], neck_deg), 'y': face['box'][1] + face['box'][3]/2, 'at': face['at']}


# Owner, October 17 (three on the couch): "turn to each person periodically, and look away too";
# Jeremy asked "Why are you looking at him?" and "Are you obsessed with me?" while the head held
# the largest face. With two or more people in view the gaze stays with one for a while, then
# moves to whoever it looked at least recently, and goes to someone who just spoke. Looking away
# is the idle motion's own glance (every 7-16 s), unchanged.
DWELL_SECONDS = (4., 9.)     # How long the gaze stays with one person among several.
SPOKE_SECONDS = 6.           # Someone identified as speaking this recently draws the gaze.
SAME_PERSON_DEG = 12.        # Two faces this close in bearing are one person (track ids churn).


class FaceTurns:
    """Which face the idle gaze follows. Aims the head only: it attributes no speaker, and a
    recognized identity is used only to keep looking at the same person."""

    def __init__(self, rng, clock):
        self.rng, self.clock = rng, clock
        self.current = None      # {'bearing', 'identity', 'until'}
        self.visited = []        # [(bearing, identity, at)] of people looked at, newest last.

    def _key(self, face, neck_deg):
        return image_bearing(face['box'], neck_deg), face.get('identity')

    def _same(self, bearing, identity, other_bearing, other_identity):
        if identity and other_identity:
            return identity == other_identity
        return abs(bearing - other_bearing) <= SAME_PERSON_DEG

    def __call__(self, faces, neck_deg, speaker=None):
        """faces: fresh boxes; speaker: (person_id, spoke_at) of the latest identified speaker."""
        if not faces:
            return None
        if len(faces) == 1:
            self.current = None
            return aim_at(faces[0], neck_deg)
        now = self.clock()
        keyed = [(face, *self._key(face, neck_deg)) for face in faces]
        if speaker and speaker[0] and now - speaker[1] <= SPOKE_SECONDS:
            talker = next((row for row in keyed if row[2] == speaker[0]), None)
            if talker is not None and not (self.current and self._same(
                    talker[1], talker[2], self.current['bearing'], self.current['identity'])):
                return self._look(talker, now, neck_deg)
        if self.current is not None:
            here = [row for row in keyed if self._same(row[1], row[2], self.current['bearing'], self.current['identity'])]
            if here and now < self.current['until']:
                face, bearing, identity = min(here, key=lambda row: abs(row[1] - self.current['bearing']))
                self.current['bearing'] = bearing
                return aim_at(face, neck_deg)
        others = [row for row in keyed if not (self.current and self._same(
            row[1], row[2], self.current['bearing'], self.current['identity']))] or keyed
        def last_seen(row):
            return max((at for b, i, at in self.visited if self._same(row[1], row[2], b, i)), default=float('-inf'))
        return self._look(min(others, key=lambda row: (last_seen(row), self.rng.random())), now, neck_deg)

    def _look(self, row, now, neck_deg):
        face, bearing, identity = row
        self.current = {'bearing': bearing, 'identity': identity, 'until': now + self.rng.uniform(*DWELL_SECONDS)}
        self.visited = [v for v in self.visited if not self._same(bearing, identity, v[0], v[1])][-7:]
        self.visited.append((bearing, identity, now))
        return aim_at(face, neck_deg)
