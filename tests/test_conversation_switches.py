"""October 22 owner review of runs 55db1330 and 193c8df6: "jarring conversation switches that felt
random". Stale goal beats, a head turn taken for a newcomer. Synthetic data, scripted providers."""
from uuid import uuid4
import json
import unittest

from r3x.agency import AgencyPlan
from r3x.contracts import Cancellation, Capture, Snapshot, ToolCall, Transcript
from r3x.present import Present
from r3x.proactive import Arrivals
from r3x.semantics import grounded_tools
from tests import test_memory_core as memory_tests
from tests import test_agency_plan as plans
from tests import test_proactive


def telescope_plan(kinds=('contribute', 'contribute', 'contribute', 'contribute')):
    return {**plans.draft(), 'subject': 'the telescope', 'steps': [
        {'kind': kind, 'instruction': f'Telescope beat {i}.', 'evidence_ids': [], 'wake': {'when': 'quiet', 'seconds': 5}}
        for i, kind in enumerate(kinds)]}


class OvertakenGoalTests(memory_tests.MemoryFixture):
    setup_owner, tick = plans.PlanTests.setup_owner, plans.PlanTests.tick

    def say(self, text):
        now = self.trace.at + 1
        self.trace.at = now
        self.session.process_turn(Capture(uuid4().hex, 'session', bytes(96000), now - 3, now, now),
                                  Transcript(text, -.01, True), 0, memory_tests.observation())

    def test_after_people_talk_on_the_beat_may_only_follow_them_and_letting_go_ends_the_goal(self):
        # 55db1330: a telescope beat after a talk about the dogs, another after where Rex is kept.
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: telescope_plan()
        self.tick(100)                                        # Telescope beat 0 is heard.
        self.say("It's just where it sits.")                  # An answer to the beat: one turn.
        self.say('Those are my dogs, Toby and Max.')          # Then the talk moves on.
        seen = []
        def let_go(snapshot, cancel):
            seen.append(snapshot)
            yield ToolCall('s', 'remain_silent', {'reason': 'no_response_needed', 'invitation_update': None,
                                                   'intent_update': None, 'next_opportunity': None})
        self.executor.choose = let_go
        self.assertEqual(self.tick(self.trace.at + 20).status, 'suppressed')
        step = seen[-1].context['agency_step']
        self.assertTrue(step['overtaken'])
        self.assertNotIn('wait_for', {t['name'] for t in grounded_tools(seen[-1].context)})   # No later comeback.
        self.assertEqual(self.state.agency.ending, 'overtaken_by_conversation')
        self.executor.choose = None
        self.tick(self.trace.at + 20)
        self.assertEqual(len(self.planner.requests), 2)       # The next goal starts from the talk now.
        self.assertEqual(self.planner.requests[-1]['goal_trigger'], 'overtaken_by_conversation')
        self.assertIn('Toby and Max', json.dumps(self.planner.requests[-1]))

    def test_one_answer_to_a_beat_keeps_the_goal(self):
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: telescope_plan()
        self.tick(100)
        self.say('It is a reflector.')
        self.tick(self.trace.at + 20)
        step = self.executor.snapshots[-1].context['agency_step']
        self.assertEqual((step['index'], step['overtaken']), (1, False))
        self.assertIsNone(self.state.agency.ending)
        self.assertEqual(len(self.planner.requests), 1)

    def test_speech_during_planning_counts_toward_overtaking(self):
        plan = AgencyPlan(self.store, self.state.trace, Present())
        plan.plan_started = 50.
        plan.human_spoke(None, 52.)                           # While the planner was working.
        plan.install(telescope_plan(), '[]', {}, {'contribute'}, plan.revision, 60.)
        self.assertFalse(plan.step(61.)['overtaken'])
        plan.human_spoke(None, 62.)
        self.assertTrue(plan.step(63.)['overtaken'])

    def test_an_overtaken_goal_does_not_look_closer_at_its_old_subject(self):
        # 55db1330: "that telescope is enormous" from an inspect beat after the talk had moved on.
        plan = AgencyPlan(self.store, self.state.trace, Present())
        plan.install(telescope_plan(('inspect', 'contribute')), '[]', {}, {'contribute', 'inspect'}, plan.revision, 10.)
        self.assertEqual(plan.step(11.)['kind'], 'inspect')
        plan.human_spoke(None, 12.)
        plan.human_spoke(None, 14.)
        step = plan.step(15.)
        self.assertEqual((step['index'], step['kind'], step['overtaken']), (1, 'contribute', True))
        self.assertEqual(plan.record['outcomes'][0]['revision_reason'], 'overtaken_by_conversation')

    def test_the_performer_is_told_the_subject_is_stale(self):
        from r3x.adapters.hosted import OpenAIBrain
        from tests.test_adapters import Client, Response
        response = Response([{'type': 'response.completed', 'response': {}}])
        brain = OpenAIBrain('fake-key'); brain.client = Client(response)
        context = {'trigger': 'reconsideration', 'conversation_intent': {'revision': 0}, 'people': [], 'faces': [],
                   'agency_step': {'kind': 'contribute', 'scope': 'audience', 'subject': 'the telescope',
                                   'people_spoke_since_planned': True, 'overtaken': True}}
        list(brain.reply(Snapshot('s', 't', 0, (), context=context, tools_enabled=True), Cancellation()))
        notes = json.dumps(brain.client.calls[0][1]['json']['input'])
        self.assertIn('THE CONVERSATION MOVED ON', notes)
        self.assertIn('the telescope', notes)


class HeadTurnArrivalTests(unittest.TestCase):
    def visible(self, arrivals, start, end, faces):
        for i in range(int((end - start) * 2) + 1):
            at = start + i * .5
            arrivals.observe([test_proactive.face(at, person, track) for person, track in faces], at)

    def test_the_only_face_right_after_a_known_one_is_them_turning_their_head(self):
        # 193c8df6: the owner, recognized until 119.0 s, turned his head; at 123.6 s the
        # unrecognized view was asked "what should I call you?".
        a = Arrivals()
        self.visible(a, 0, 4, [(None, 'side-view')])
        self.assertIsNone(a.take('s', 4, 0, known_just_now=True))
        self.assertEqual(a.passed_over, ['side-view'])
        self.assertFalse(a.unknown_attempted)                  # A real newcomer later is still asked.
        self.visible(a, 4.5, 60, [(None, 'side-view')])
        self.assertIsNone(a.take('s', 60, 0))                  # That view is never asked later either.

    def test_a_second_face_beside_a_known_one_is_still_a_newcomer(self):
        a = Arrivals()
        self.visible(a, 0, 4, [('bret', 'face'), (None, 'guest')])
        a.take('s', 4, 0, known_just_now=True)                 # Bret's own arrival comes first.
        self.visible(a, 4.5, 60, [('bret', 'face'), (None, 'guest')])
        event = a.take('s', 60, 0, known_just_now=True)
        self.assertEqual((event.target_track, event.person_id), ('guest', None))

    def test_an_unknown_face_with_nobody_known_around_is_greeted(self):
        a = Arrivals()
        self.visible(a, 0, 4, [(None, 'visitor')])
        self.assertEqual(a.take('s', 4, 0, known_just_now=False).target_track, 'visitor')

    def test_recently_recognized_covers_face_and_voice(self):
        present = Present()
        self.assertFalse(present.recently_recognized(100, 60))
        present.heard('bret', 50.)
        self.assertTrue(present.recently_recognized(100, 60))
        self.assertFalse(present.recently_recognized(120, 60))
        present.saw_faces([test_proactive.face(115, 'bret')], 115)
        self.assertTrue(present.recently_recognized(120, 60))


if __name__ == '__main__':
    unittest.main()
