"""Beats wake on conditions Rex chose, watched by the picture of the present; synthetic only."""
from types import SimpleNamespace

from r3x.agency import GIVE_UP_SECONDS
from r3x.contracts import ToolCall
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.semantics import grounded_tools
from r3x.session import Session
from tests import test_memory_core as memory_tests
from tests import test_turn_taking as timing_tests
from tests.test_agency_plan import Executor, Planner, draft


def beat(instruction, when, seconds, follows=False):
    return {'kind': 'contribute', 'instruction': instruction, 'evidence_ids': [],
            'wake': {'when': when, 'seconds': seconds}, 'follows_previous': follows}


class WakeTests(memory_tests.MemoryFixture):
    def setup_owner(self, steps):
        self.trace = self.state.trace
        self.executor = Executor(); self.planner = Planner()
        self.planner.choose = lambda facts, cancel: {**draft(), 'steps': steps}
        self.session = Session('session', self.executor, OutputOwner(SyntheticTTS(),
            timing_tests.ClockSink(self.trace), self.trace), self.trace, self.state)
        self.session.agency_planner = self.planner
        self.runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, self.session, self.trace)
        self.runtime.started_at = 0
        self.reply_from_someone(90)   # Someone is around and just spoke.

    def tick(self, at):
        self.trace.at = at; self.runtime.audio_seen_at = at
        return self.runtime.proactive_tick()

    def instructions(self):
        return [s.context['agency_step']['instruction'] for s in self.executor.snapshots if s.context.get('agency_step')]

    def reply_from_someone(self, at):
        self.trace.at = at
        self.state.agency.human_spoke(None, at)
        self.session.last_trusted_speech_at = at

    def test_a_follow_up_waits_for_an_answer_then_runs_without_a_fixed_delay(self):
        self.setup_owner([beat('Ask about the trip.', 'quiet', 5),
                          beat('React to the answer.', 'answered', 5, follows=True)])
        self.tick(100)
        self.assertEqual(self.instructions(), ['Ask about the trip.'])
        for at in (130, 160, 190):                     # Nobody answers: it waits, it does not fill the air.
            self.tick(at)
        self.assertEqual(self.instructions(), ['Ask about the trip.'])
        self.reply_from_someone(200)
        self.tick(210)
        self.assertEqual(self.instructions(), ['Ask about the trip.', 'React to the answer.'])

    def test_an_answer_never_given_lets_the_follow_up_go_without_a_model_call(self):
        self.setup_owner([beat('Ask about the trip.', 'quiet', 5),
                          beat('React to the answer.', 'answered', 5, follows=True)])
        self.tick(100)
        calls = len(self.executor.snapshots)
        self.tick(110 + GIVE_UP_SECONDS + 10)
        self.assertEqual(len(self.executor.snapshots), calls)
        reasons = [r['revision_reason'] for r in self.trace.rows if r['event'] == 'agency_step_finished']
        self.assertIn('moment_passed', reasons)

    def test_a_rescue_line_runs_only_if_the_question_fell_flat(self):
        self.setup_owner([beat('Ask about the trip.', 'quiet', 5),
                          beat('Tease the silence lightly.', 'unanswered', 20),
                          beat('Opine about trips.', 'quiet', 5)])
        self.tick(100)
        self.tick(110)
        self.assertEqual(len(self.instructions()), 1)  # Not yet: twenty seconds of no answer.
        self.reply_from_someone(112)
        self.tick(140)
        self.assertNotIn('Tease the silence lightly.', self.instructions())
        self.assertIn('Opine about trips.', self.instructions())

    def test_a_beat_about_the_dog_waits_for_the_dog(self):
        self.setup_owner([beat('Greet the dog.', 'pet_in_view', 3)])
        self.tick(100)
        self.assertEqual(self.instructions(), [])
        self.state.present.saw_things([{'target_id': 'o', 'label': 'dog', 'box': [.4, .4, .2, .2], 'at': 150}], 150)
        self.tick(151)
        self.assertEqual(self.instructions(), ['Greet the dog.'])

    def test_the_performer_can_hold_a_beat_once_for_a_better_moment(self):
        self.setup_owner([beat('Bring up camping.', 'quiet', 30)])
        self.tick(100)                                   # Planned; then people talk before it runs.
        self.assertEqual(self.executor.snapshots, [])
        self.reply_from_someone(120)
        def waits(snapshot, cancel):
            self.assertIn('wait_for', {d['name'] for d in grounded_tools(snapshot.context)})
            yield ToolCall('w', 'wait_for', {'when': 'answered', 'seconds': 5, 'reason': 'They are mid-story.'})
        self.executor.choose = waits
        self.tick(160)
        self.assertEqual(self.state.agency.record['cursor'], 0)            # Not used up, not spoken.
        self.assertEqual([r['wake'] for r in self.trace.rows if r['event'] == 'agency_beat_deferred'],
                         [{'when': 'answered', 'seconds': 5}])
        self.assertFalse([r for r in self.trace.rows if r['event'] == 'phrase_completed'])
        self.executor.choose = None
        self.tick(170)
        self.assertEqual(len(self.executor.snapshots), 1)                  # Waiting for that answer.
        self.reply_from_someone(175)
        self.tick(185)
        step = self.executor.snapshots[-1].context['agency_step']
        self.assertTrue(step['deferred'])
        self.assertNotIn('wait_for', {d['name'] for d in grounded_tools(self.executor.snapshots[-1].context)})

    def test_a_rescue_line_answered_before_any_beat_ran_is_let_go_and_traced(self):
        # October 19: an arrival reset the goal timer, the plan's first beat was a rescue line,
        # the person answered, and skipping it wrote -inf to the trace and stopped the worker.
        self.setup_owner([beat('Rule on the late hour.', 'unanswered', 18),
                          beat('Opine about midnight projects.', 'quiet', 5)])
        self.state.agency.replace('person_arrived', {'person_id': None, 'track_id': None})
        self.tick(100)
        self.reply_from_someone(105)
        self.tick(130)
        finished = [r for r in self.trace.rows if r['event'] == 'agency_step_finished']
        self.assertEqual(finished[0]['revision_reason'], 'moment_passed')
        self.assertNotIn('Rule on the late hour.', self.instructions())
        self.assertIn('Opine about midnight projects.', self.instructions())
