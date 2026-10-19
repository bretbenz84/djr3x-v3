"""The picture of the present: one owner for presence and the conversation clock; synthetic only."""
from types import SimpleNamespace
import unittest

from r3x.output import OutputOwner
from r3x.present import ARRIVAL_SECONDS, STAYS_SECONDS, Present, ago, quiet_meaning
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.session import Session
from tests import test_memory_core as memory_tests
from tests import test_turn_taking as timing_tests
from tests.test_agency_plan import Executor, Planner
from tests.test_proactive import face


class PresentRules(unittest.TestCase):
    def test_time_is_put_in_words(self):
        self.assertEqual([ago(s) for s in (1, 8, 40, 100, 300, 1200)],
            ['just now', 'a few seconds ago', 'under a minute ago', 'a couple of minutes ago',
             'several minutes ago', 'a while ago'])
        self.assertEqual(quiet_meaning(30), 'a lull')

    def test_a_visit_survives_looking_away_and_ends_after_a_real_absence(self):
        present = Present()
        present.saw_faces([face(100, 'bret', 'a')], 100)
        self.assertTrue(present.arriving('bret', 100))
        present.saw_faces([face(170, 'bret', 'b')], 170)          # A renumbered track, 70 s later.
        self.assertFalse(present.arriving('bret', 170))
        later = 170 + STAYS_SECONDS + 1                            # Gone longer than a visit lasts.
        present.saw_faces([face(later, 'bret', 'c')], later)
        self.assertTrue(present.arriving('bret', later))
        self.assertFalse(present.arriving('bret', later + ARRIVAL_SECONDS + 1))

    def test_a_question_left_hanging_is_described_as_a_passed_moment(self):
        present = Present()
        present.heard('bret', 100)
        present.rex_said('Got any camping plans?', 105)
        line = present.describe(108, {'bret': 'Bret'})['conversation']['rex_last_line']
        self.assertIn('give them time', line['what_became_of_it'])
        line = present.describe(160, {'bret': 'Bret'})['conversation']['rex_last_line']
        self.assertIn('moment has passed', line['what_became_of_it'])
        present.heard('bret', 161)
        picture = present.describe(162, {'bret': 'Bret'})
        self.assertEqual(picture['conversation']['rex_last_line']['what_became_of_it'], 'someone has spoken since')
        self.assertEqual(picture['conversation']['last_human_words'], 'Bret spoke just now')

    def test_his_own_unanswered_lines_reset_when_anyone_speaks(self):
        present = Present()
        present.beat_heard(); present.beat_heard()
        self.assertIn('2 of his own lines', present.describe(10)['conversation']['unanswered_lines'])
        present.heard(None, 11)
        self.assertEqual(present.unanswered, 0)

    def test_a_pet_seen_lately_is_part_of_the_picture(self):
        present = Present()
        present.saw_things([{'target_id': 'o', 'label': 'cat', 'box': [.4, .4, .2, .2], 'at': 100}], 100)
        picture = present.describe(103)
        self.assertEqual(picture['pet'], {'seen': 'a few seconds ago', 'in_view': True})
        self.assertEqual(picture['things_seen'], [])   # The pet has its own place; not a thing too.

    def test_forgetting_someone_removes_them_from_the_picture(self):
        present = Present()
        present.heard('jeff', 100)
        present.forget('jeff')
        self.assertEqual(present.describe(101, {'jeff': 'Jeff'})['people_here'], [])
        self.assertTrue(present.arriving('jeff', 102))


class PresentInConversation(memory_tests.MemoryFixture):
    def setup_owner(self):
        self.trace = self.state.trace
        self.executor = Executor(); self.planner = Planner()
        self.session = Session('session', self.executor, OutputOwner(SyntheticTTS(),
            timing_tests.ClockSink(self.trace), self.trace), self.trace, self.state)
        self.session.agency_planner = self.planner
        self.runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, self.session, self.trace)
        self.runtime.started_at = 0

    def tick(self, at):
        self.trace.at = at; self.runtime.audio_seen_at = at
        return self.runtime.proactive_tick()

    def show(self, person, track, at):
        self.runtime.camera = SimpleNamespace(latest=lambda now: [face(now, person, track)])
        for t in range(int(at) - 4, int(at) + 1):
            self.runtime.arrivals.observe([face(t, person, track)], t)

    def test_someone_heard_a_minute_and_a_half_ago_is_not_greeted_as_arriving(self):
        # October 5-13: 6 of 8 arrival greetings went to someone recognized under two minutes before.
        self.setup_owner()
        bret = self.enroll(name='Bret')
        self.state.agency.human_spoke(bret, 100)    # His voice was recognized; his face was not in view.
        self.show(bret, 'later-track', 190)
        self.tick(190)
        self.assertNotIn('visual_arrival', [s.context.get('trigger') for s in self.executor.snapshots])
        self.assertEqual([r['reason'] for r in self.trace.rows if r['event'] == 'arrival_skipped'], ['same_visit'])

    def test_someone_back_after_a_real_absence_is_greeted(self):
        self.setup_owner()
        bret = self.enroll(name='Bret')
        self.state.agency.human_spoke(bret, 100)
        back = 100 + STAYS_SECONDS + 30
        self.show(bret, 'back', back)
        self.tick(back)
        self.assertEqual(self.executor.snapshots[0].context['trigger'], 'visual_arrival')

    def test_every_reply_and_plan_sees_the_moment_in_words(self):
        self.setup_owner()
        bret = self.enroll(name='Bret')
        self.state.agency.human_spoke(bret, 90)
        self.tick(100)                               # A goal is planned, then its first beat runs.
        self.tick(110)
        self.assertEqual(self.planner.requests[0]['right_now']['people_here'][0]['name'], 'Bret')
        beat = self.executor.snapshots[-1].context['right_now']
        self.assertTrue(beat['conversation']['last_human_words'].startswith('Bret spoke '))
        self.assertEqual(beat['people_here'][0]['name'], 'Bret')


if __name__ == '__main__':
    unittest.main()


class PresentRepairs(unittest.TestCase):
    """October 14 live run (e1f8b2c6)."""
    def test_someone_he_already_spoke_with_is_not_arriving_even_in_their_first_minute(self):
        present = Present()
        present.saw_faces([face(100, 'bret', 'a')], 100)
        present.rex_said("Brett, what's tonight's operation?", 105, answering='bret')
        self.assertFalse(present.arriving('bret', 130))
        self.assertTrue(Present().arriving('bret', 130))

    def test_the_clock_and_his_earlier_lines_are_in_the_picture(self):
        from datetime import datetime
        present = Present()
        for at, line in ((100, 'I misspoke about the clock.'), (120, 'That clock correction was fair.'), (140, 'Nice vape.')):
            present.rex_said(line, at)
        picture = present.describe(150, wall=datetime(2026, 10, 14, 18, 24))
        self.assertEqual(picture['local_time'], 'Wednesday evening, 6:24 PM, October 14')
        earlier = picture['conversation']['his_earlier_lines']
        self.assertEqual([l['text'] for l in earlier['lines']], ['I misspoke about the clock.', 'That clock correction was fair.'])
        self.assertIn('never bring back a mistake', earlier['meaning'])


class PlanReviewRepairs(unittest.TestCase):
    """October 17 review of the improvement plan."""
    def test_company_is_noticed_by_the_pictures_own_eyes(self):
        from r3x.present import COMPANY_SECONDS
        present = Present()
        present.saw_faces([face(100, 'bret', 'a')], 100)
        self.assertTrue(present.alone_with('bret', 101))
        # A second face between turns, with no reply or beat context built meanwhile.
        present.saw_faces([face(120, 'bret', 'a'), face(120, None, 'b')], 120)
        present.saw_faces([face(130, 'bret', 'a')], 130)
        self.assertFalse(present.alone_with('bret', 131))
        later = 120 + COMPANY_SECONDS + 1
        present.saw_faces([face(later, 'bret', 'a')], later)
        self.assertTrue(present.alone_with('bret', later + 1))

    def test_a_known_guest_arriving_mid_reply_is_still_greeted(self):
        present = Present()
        present.saw_faces([face(100, 'bret', 'a')], 100)
        present.heard('bret', 101)
        present.saw_faces([face(103, 'bret', 'a'), face(103, 'jeff', 'b')], 103)   # Jeff walks in.
        present.rex_said('Bold plan, Brett.', 105, answering='bret')              # Rex finishes answering Bret.
        self.assertFalse(present.arriving('bret', 110))
        self.assertTrue(present.arriving('jeff', 110))
        present.rex_said('Jeff! Back already?', 112, answering='jeff')
        self.assertFalse(present.arriving('jeff', 115))

    def test_a_thing_is_placed_across_his_view_not_by_a_ranking_phrase(self):
        present = Present()
        present.saw_things([
            {'target_id': 'c', 'label': 'clock', 'box': [.05, .3, .2, .2], 'at': 100, 'samples': 9,
             'image_position': {'center_x': .15, 'center_y': .4, 'description': 'only detected object of this category'}},
            {'target_id': 'b', 'label': 'bottle', 'box': [.4, .3, .2, .2], 'at': 100, 'samples': 5}], 100)
        things = {row['thing']: row['where'] for row in present.describe(101)['things_seen']}
        self.assertEqual(things, {'clock': 'on his left', 'bottle': 'in the middle of his view'})

    def test_a_flickering_detection_is_not_a_thing_in_the_room(self):
        # October 18 (6aaf22aa): a "cake" the detector saw in 14 of 671 looks, three in a row at
        # most, reached the picture and the writer's room; Rex teased the owner about it and the
        # owner had to say there was no cake. Only a steadily seen thing joins the picture; once
        # there, it stays known even when the detector drops it for a look.
        from r3x.present import STEADY_SAMPLES
        from r3x.jokes import summary
        present = Present()
        clock = {'target_id': 'c', 'label': 'clock', 'box': [.05, .3, .2, .2], 'at': 100, 'samples': STEADY_SAMPLES}
        for n in range(1, STEADY_SAMPLES):
            present.saw_things([{'target_id': 'k', 'label': 'cake', 'box': [.4, .3, .2, .2], 'at': 100, 'samples': n},
                                clock], 100)
        picture = present.describe(101)
        self.assertEqual([row['thing'] for row in picture['things_seen']], ['clock'])
        self.assertNotIn('cake', summary([], picture, [], None))
        present.saw_things([{**clock, 'samples': 1, 'at': 130}], 130)   # The clock's track restarted.
        self.assertEqual([row['thing'] for row in present.describe(131)['things_seen']], ['clock'])
        present.saw_things([{'target_id': 'k', 'label': 'cake', 'box': [.4, .3, .2, .2], 'at': 140,
                             'samples': STEADY_SAMPLES}], 140)
        self.assertIn('cake', [row['thing'] for row in present.describe(141)['things_seen']])


class LookingAwareness(memory_tests.MemoryFixture):
    """October 14 (1662b007): his own beat tipped his head up; asked "what are you looking at?" he
    took a new look and recited the view, though the owner sat at the edge of his sight."""
    def session_with_head(self, activity, neck=-30., objects=()):
        from r3x.servo_poses import DRIVE_HEAD, NECK_CENTER
        session = Session('s', Executor(), OutputOwner(SyntheticTTS(), timing_tests.ClockSink(self.state.trace),
                          self.state.trace), self.state.trace, self.state)
        owner = SimpleNamespace(state='probe-head', neck_degrees=lambda: neck,
                                commanded={0: NECK_CENTER, **dict(DRIVE_HEAD)})
        session.output.robot = SimpleNamespace(owner=owner, idle=SimpleNamespace(activity=(activity, self.state.trace.at - 4)))
        self.state.objects = SimpleNamespace(snapshot=lambda min_samples=2: tuple(objects))
        return session

    def test_an_idle_glance_knows_who_is_at_the_edge_and_what_is_in_the_middle(self):
        bret = self.enroll('Bret')
        clock = {'label': 'clock', 'image_position': {'center_x': .55, 'center_y': .4}}
        session = self.session_with_head('glancing_away', objects=[clock])
        looking = session._right_now([{'identity': bret, 'box': [.05, .6, .06, .1]}])['rex']['looking']
        self.assertEqual(looking['toward'], 'to his right')
        self.assertIn('eyes wandered', looking['why'])
        self.assertEqual(looking['people_in_his_view'], ['Bret at the left edge of his view'])
        self.assertEqual(looking['in_the_middle_of_his_view'], ['clock'])
        self.assertIn('stretching my neck', looking['if_asked'])

    def test_a_look_he_chose_carries_its_reason(self):
        session = self.session_with_head('holding_chosen_direction', neck=0.)
        self.state.present.rex_looked('up', 'Briefly look upward while letting the astronomy detour go.', self.state.trace.at)
        looking = session._right_now(())['rex']['looking']
        self.assertEqual(looking['why'], 'Briefly look upward while letting the astronomy detour go.')
        self.assertNotIn('if_asked', looking)

    def test_he_speaks_of_his_eyes_not_a_camera_frame(self):
        from r3x.adapters.hosted import PERSONA
        self.assertIn('The camera is your eyes', PERSONA)
        self.assertIn('Never say frame, image, picture', PERSONA)

    def test_a_recognized_face_he_sees_is_named_not_a_stranger(self):
        from r3x.adapters.hosted import PERSONA
        self.assertIn('a person you see whose face is\n  recognized', PERSONA)
        self.assertIn('never "a man" or "someone"', PERSONA)
