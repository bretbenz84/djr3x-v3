"""October 17 (owner, Jeremy and a third person on the couch): the idle gaze shares itself among
the people in view, turns to whoever just spoke, and keeps its glances away; synthetic boxes."""
import random
import unittest

from r3x.gaze import DWELL_SECONDS, SPOKE_SECONDS, FaceTurns, face_aim


class Clock:
    def __init__(self): self.now = 100.
    def __call__(self): return self.now


def face(x, identity=None, track=None, width=.1):
    return {'box': [x, .3, width, .15], 'identity': identity, 'track_id': track or f'f-{x}', 'at': 100.}


class FaceTurnsTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.turns = FaceTurns(random.Random(7), self.clock)

    def watched(self, faces, seconds, speaker=None, step=1.):
        """Which face (by left edge) the gaze aims at, second by second."""
        seen = []
        for _ in range(int(seconds / step)):
            aim = self.turns(faces, 0., speaker)
            seen.append(min(faces, key=lambda f: abs(face_aim([f], 0.)['neck'] - aim['neck']))['box'][0])
            self.clock.now += step
        return seen

    def test_one_person_is_simply_followed(self):
        only = [face(.4, 'bret')]
        self.assertEqual(self.turns(only, 0.), face_aim(only, 0.))

    def test_with_three_people_each_gets_a_turn_and_none_is_stared_at(self):
        people = [face(.1), face(.45, 'bret', width=.2), face(.8)]   # The largest is not the only one looked at.
        seen = self.watched(people, 40.)
        self.assertEqual(set(seen), {.1, .45, .8})
        runs, last = [], None
        for x in seen:
            if x != last: runs.append(0); last = x
            runs[-1] += 1
        self.assertLessEqual(max(runs), DWELL_SECONDS[1] + 1)          # Nobody held longer than a dwell.
        self.assertGreaterEqual(min(runs[1:-1] or [DWELL_SECONDS[0]]), DWELL_SECONDS[0] - 1)   # Nor flicked past.

    def test_renumbered_tracks_are_still_the_same_person(self):
        first = self.turns([face(.1, track='a'), face(.8, track='b')], 0.)
        self.clock.now += 1
        again = self.turns([face(.1, track='c'), face(.8, track='d')], 0.)   # New track ids, same seats.
        self.assertEqual(first['neck'], again['neck'])

    def test_whoever_just_spoke_draws_the_gaze_then_it_moves_on(self):
        people = [face(.1, 'jeremy'), face(.5, 'bret'), face(.8)]
        self.watched(people, 3.)
        spoke = ('bret', self.clock.now)
        aim = self.turns(people, 0., spoke)
        self.assertEqual(aim['neck'], face_aim([people[1]], 0.)['neck'])
        seen = self.watched(people, SPOKE_SECONDS + DWELL_SECONDS[1] + 2, spoke)
        self.assertNotEqual(set(seen), {.5})                           # Then the others get their turn.

    def test_nobody_in_view_means_no_face_to_follow(self):
        self.assertIsNone(self.turns([], 0.))


if __name__ == '__main__':
    unittest.main()
