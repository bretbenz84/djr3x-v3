"""October 22 run 1072c10a: Rex spoke less, sat silent between beats, misread action requests as
requests for quiet, could not raise his visor or sparkle just his chest, crashed on looks back at
a person, and filed the owner's partner's birthday as the owner's. Synthetic data and scripted
providers through the production owners; no hardware, network or models."""
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

from r3x.attention import AttentionIntent, SimulatedAttention
from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.memory_opportunities import candidates
from r3x.output import OutputOwner
from r3x.physical_attention import PhysicalAttention
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.servos import ServoRequest
from r3x.session import Session
from tests import test_agency_plan as plans
from tests import test_memory_core as memory_tests
from tests import test_physical_attention, test_servos, test_turn_taking as timing_tests
from tests.test_proactive import face


def plan_of(count, kinds=None):
    kinds = kinds or ('contribute',) * count
    return {**plans.draft(), 'subject': 'the telescope', 'steps': [
        {'kind': kind, 'instruction': f'Telescope beat {i}.', 'evidence_ids': [], 'wake': {'when': 'quiet', 'seconds': 5}}
        for i, kind in enumerate(kinds)]}


def let_go(snapshot, cancel):
    yield ToolCall(uuid4().hex, 'remain_silent', {'reason': 'no_response_needed', 'invitation_update': None,
                                                  'intent_update': None, 'next_opportunity': None})


class PacingTests(memory_tests.MemoryFixture):
    setup_owner, tick = plans.PlanTests.setup_owner, plans.PlanTests.tick

    def say(self, text, chooser=None):
        now = self.trace.at + 1
        self.trace.at = now
        previous = self.executor.choose
        self.executor.choose = chooser
        try:
            return self.session.process_turn(Capture(uuid4().hex, 'session', bytes(96000), now - 3, now, now),
                                             Transcript(text, -.01, True), 0, memory_tests.observation())
        finally:
            self.executor.choose = previous

    def test_a_beat_let_go_does_not_start_a_new_lull(self):
        # Each let-go beat restarted the quiet clock: about ten seconds of silence apiece.
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: plan_of(8)
        self.tick(100)
        self.executor.choose = let_go
        self.assertEqual(self.tick(self.trace.at + 20).status, 'suppressed')
        let_go_at = self.trace.at
        self.executor.choose = None
        self.assertIsNone(self.tick(let_go_at + 1))                # A short pause...
        self.assertEqual(self.tick(let_go_at + 3.5).status, 'completed')   # ...not a whole new lull.
        self.assertEqual(self.state.agency.record['outcomes'][-1]['step'], 2)

    def test_the_next_plan_is_written_ahead_and_follows_the_last_beat_at_once(self):
        # Writing it only after the last beat left 8-13 s of silence.
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: plan_of(5)
        first = None
        for beat in range(5):
            self.tick(100 if beat == 0 else self.trace.at + 9)
            first = first or self.state.agency.record['id']
            self.say('Go on.')                                       # Answered, so never "ignored".
            self.session.join_planning()
        self.assertEqual([r['goal_trigger'] for r in self.planner.requests], ['startup', 'beats_running_out'])
        self.assertEqual(self.planner.requests[1]['current_goal']['cursor'], 3)   # Two beats still to run.
        self.assertEqual(self.state.agency.record['id'], first)     # Written ahead, not installed.
        self.assertTrue(any(r['event'] == 'agency_plan_prepared' for r in self.trace.rows))
        self.assertEqual(self.tick(self.trace.at + 9).status, 'completed')
        self.assertNotEqual(self.state.agency.record['id'], first)  # Followed at once...
        self.assertEqual(self.state.agency.record['outcomes'][-1]['status'], 'completed')
        self.assertEqual(len(self.planner.requests), 2)            # ...with no planner wait or extra call.

    def test_a_goal_that_ends_another_way_drops_the_plan_written_for_after_it(self):
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: plan_of(4)
        for beat in range(3):
            self.tick(100 if beat == 0 else self.trace.at + 9)
            self.say('Go on.')
            self.session.join_planning()
        self.assertIsNotNone(self.state.agency.prepared)
        self.state.agency.replace('revised')
        self.assertIsNone(self.state.agency.prepared)

    def test_quiet_pauses_the_goal_and_resuming_carries_on_with_it(self):
        # Two misread requests for quiet each erased the goal and cost a fresh plan.
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: plan_of(4)
        self.tick(100)
        goal = self.state.agency.record['id']
        def switch(name, **args):
            def choose(snapshot, cancel):
                if snapshot.context.get('initiative_changed'):
                    yield 'Understood.'
                else:
                    yield ToolCall(uuid4().hex, name, {**args, 'source_turn_id': snapshot.context['source_turn_id']})
            return choose
        self.assertEqual(self.say('Stop interrupting for a while.', switch('stop_chiming_in', mode='quiet')).status,
                         'completed')
        self.assertTrue(self.state.initiative.quiet)
        self.assertEqual(self.state.agency.record['id'], goal)
        self.assertIsNone(self.tick(self.trace.at + 30))            # Nothing runs while quiet.
        self.say('Okay, you can talk again.', switch('resume_chiming_in'))
        self.assertFalse(self.state.initiative.quiet)
        self.tick(self.trace.at + 30)
        self.assertEqual(self.state.agency.record['id'], goal)
        self.assertEqual(self.state.agency.record['outcomes'][-1]['step'], 1)
        self.assertEqual(len(self.planner.requests), 1)


class ChimeInTests(memory_tests.MemoryFixture):
    def test_the_line_after_going_quiet_or_silent_is_words(self):
        # Told "not a word until I get back", he says one line so they know; the request that
        # carries it must not force a tool.
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Snapshot
        from tests.test_adapters import Client, Response
        brain = OpenAIBrain('fake-key'); brain.client = Client(Response([{'type': 'response.completed', 'response': {}}]))
        context = {'conversation_intent': {'revision': 0}, 'people': [], 'faces': [], 'source_turn_id': 't',
                   'participation': {'initiative_quiet': True, 'silent_until_released': True}}
        # Paid probe, October 22: given tools there, three of four such turns chose remain_silent.
        list(brain.reply(Snapshot('s', 't', 0, (), context={**context, 'initiative_changed': 'silent'},
                                  tools_enabled=True), Cancellation()))
        self.assertEqual(brain.client.calls[-1][1]['json']['tool_choice'], 'none')
        list(brain.reply(Snapshot('s', 't', 0, (), context=context, tools_enabled=True), Cancellation()))
        choice = brain.client.calls[-1][1]['json']['tool_choice']   # Later turns while silent: a tool, always.
        self.assertEqual(choice['mode'], 'required')
        self.assertEqual({t['name'] for t in choice['tools']}, {'remain_silent', 'shut_down', 'resume_chiming_in'})


class LookBackTests(memory_tests.MemoryFixture):
    def test_a_look_back_at_the_people_follows_their_faces_again(self):
        clock = test_servos.Clock()
        robot, _ = test_physical_attention.robot_fixture(clock, clock.wait)
        self.addCleanup(robot.owner.close)
        adapter = PhysicalAttention(robot); adapter.start()
        self.assertIn('people', adapter.targets)
        robot.execute_expression(ServoRequest('away', robot.owner.version, 'attention', clock() + 15, target='left',
                                              hold_seconds=.5), Cancellation())
        self.assertEqual(robot.idle.gaze, 'direction')
        receipt = adapter.execute(AttentionIntent('back', 0, 'people', 'orient', 'Back to Bret', clock(), clock() + 15),
                                  Cancellation())
        self.assertEqual((receipt.outcome, receipt.medium), ('pulses_reached', 'controller'))
        self.assertEqual(robot.idle.gaze, 'people')
        self.assertFalse(robot.owner.held_channels)

    def test_a_look_at_an_invented_person_id_is_let_go_without_failing_the_turn(self):
        trace = self.state.trace
        executor, planner = plans.Executor(), plans.Planner()
        planner.choose = lambda facts, cancel: plan_of(2, ('look', 'contribute'))
        session = Session('session', executor, OutputOwner(SyntheticTTS(), timing_tests.ClockSink(trace), trace,
                                                           attention=SimulatedAttention(trace.clock)), trace, self.state)
        session.output.attention.start()
        session.agency_planner = planner
        self.addCleanup(session.close)
        runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace)
        runtime.started_at = 0
        executor.choose = lambda snapshot, cancel: iter([ToolCall('look', 'shift_attention', {
            'target': 'person:6836c292', 'purpose': 'orient', 'reason': 'Back to Brett', 'hold': 'brief',
            'next_opportunity': None})])
        trace.at = 100; runtime.audio_seen_at = 100
        self.assertEqual(runtime.proactive_tick().status, 'suppressed')
        self.assertFalse(any(r['event'] == 'turn_failed' for r in trace.rows))
        outcome = self.state.agency.record['outcomes'][-1]
        self.assertEqual((outcome['kind'], outcome['status']), ('look', 'skipped'))
        self.assertEqual(outcome['revision_reason'], 'ToolRejected:InvalidAttentionTarget')
        self.assertIsNone(self.state.agency.ending)                 # The goal goes on.


class OtherPeoplesFactsTests(memory_tests.MemoryFixture):
    def setUp(self):
        super().setUp()
        self.bret = self.enroll('Bret')
        self.state.calendar_today = lambda: date(2026, 11, 30)
        self.state.wall_clock = lambda: plans_noon(date(2026, 10, 22))
        self.begin('My partner is Jeremy Thomas.')

    def test_someone_elses_birthday_is_saved_as_theirs_and_comes_back_with_the_teller(self):
        self.begin('And Jeremy Thomas turns thirty-six on November thirtieth.')
        result = self.call('remember_followup', dict(predicate='birthday', value='November 30; turns 36 in 2026',
            kind='birthday', on_date='11-30', due_in_minutes=None, due_by=None, audience='subject_only',
            source_turn_id=self.state.current[0], other_person=None, subject_reference='Jeremy Thomas'))
        self.assertEqual(result.outcome, 'ok', result)
        claim = self.store.rows('SELECT * FROM claims WHERE id=?', (result.data['claim_id'],))[0]
        jeremy = claim['subject_id']
        self.assertNotEqual(jeremy, self.bret)
        self.assertEqual((claim['kind'], claim['said_by']), ('third_party', self.bret))
        self.assertEqual(self.store.person(jeremy)['name'], 'Jeremy Thomas')
        # Bret's own birthday is still free to save: nothing of his conflicts.
        self.begin('My own birthday is June fifth.')
        own = self.call('remember_followup', dict(predicate='birthday', value='June 5', kind='birthday', on_date='06-05',
            due_in_minutes=None, due_by=None, audience='subject_only', source_turn_id=self.state.current[0]))
        self.assertEqual(own.data['status'], 'accepted')
        # On the day, it is offered with Bret, who told it, and names whose it is.
        rows = candidates(self.store, (dict(face(100), identity=self.bret),), date(2026, 11, 30),
                          now=plans_noon(date(2026, 11, 30)))
        self.assertEqual([(r['claim_id'], r['subject_name']) for r in rows], [(result.data['claim_id'], 'Jeremy Thomas')])
        self.assertFalse(candidates(self.store, (dict(face(100), identity=jeremy),), date(2026, 11, 30),
                                    now=plans_noon(date(2026, 11, 30))))
        # And it is part of what Rex knows when talking with Bret, not of what anyone hears about Bret.
        self.assertIn(result.data['claim_id'], [r['id'] for r in self.store.claims(self.bret, told=True)])
        self.assertNotIn(result.data['claim_id'], [r['id'] for r in self.store.claims(self.bret)])

    def test_someone_elses_interest_is_saved_as_theirs(self):
        self.begin('Jeremy Thomas loves to play volleyball.')
        result = self.call('remember_interest', dict(predicate='interest', value='loves to play volleyball',
            topic='volleyball', source_turn_id=self.state.current[0], subject_reference='Jeremy Thomas'))
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual(result.data['subject_name'], 'Jeremy Thomas')
        self.assertNotEqual(result.data['subject_id'], self.bret)
        self.assertEqual(result.data['said_by'], self.bret)
        self.assertFalse([r for r in self.store.claims(self.bret) if r['subject_id'] == self.bret
                          and 'volleyball' in r['value']])


def plans_noon(day):
    from datetime import datetime, time
    return datetime.combine(day, time(12)).timestamp()


class VisorOverEyesTests(memory_tests.MemoryFixture):
    # Owner, October 22: closing the visor "effectively blinds him"; he should know it and joke
    # about his own dark, never about blind people.
    def context_with_visor(self, pulses, raise_it=False, state='active'):
        from r3x.servo_poses import DRIVE_HEAD, NECK_CENTER
        from r3x.scenario import SyntheticSink
        from tests.test_voice_core import Brain
        brain = Brain('Hello.')
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace)
        output.robot = SimpleNamespace(owner=SimpleNamespace(state=state, held_channels=set(),
            commanded={0: NECK_CENTER, **dict(DRIVE_HEAD), 3: pulses}, hold_until_released=False,
            kept_arms=(), kept_channels=(), may_raise_visor=lambda: raise_it), idle=SimpleNamespace(mode=lambda: 'people'))
        session = Session('session', brain, output, self.state.trace, self.state)
        self.addCleanup(session.close)
        self.begin('Can you lower your visor?')
        session.process_turn(self.state.current[1], Transcript('Can you lower your visor?', -.01, True), 0,
                             memory_tests.observation())
        return brain.snapshots[-1].context

    def test_the_pose_tool_is_offered_while_he_is_moving(self):
        # October 22: the listening cue made the servo owner 'moving' as each request was heard, and the
        # pose tool was offered only when 'active': 15 of 20 visor requests in three runs had no tool.
        from r3x.semantics import grounded_tools
        from r3x.servo_poses import VISOR_DOWN
        context = self.context_with_visor(VISOR_DOWN, state='moving')
        self.assertTrue(context['robot_pose_enabled'])
        self.assertIn('visor_over_eyes', context)
        self.assertIn('set_robot_pose', {t['name'] for t in grounded_tools(context)})
        for asleep in ('parked', 'parking', 'faulted'):
            self.assertFalse(self.context_with_visor(VISOR_DOWN, state=asleep)['robot_pose_enabled'])

    def test_a_shut_visor_tells_him_he_cannot_see(self):
        from r3x.field_guide import GUIDE
        from r3x.semantics import TOOLS
        from r3x.servo_poses import VISOR_DOWN, VISOR_OPEN, VISOR_UP
        self.assertEqual(self.context_with_visor(VISOR_DOWN)['visor_over_eyes']['meaning'], GUIDE['visor_over_eyes'])
        for pulses in (VISOR_OPEN, VISOR_UP):
            self.assertNotIn('visor_over_eyes', self.context_with_visor(pulses))
        later = self.context_with_visor(VISOR_DOWN, raise_it=True)['visor_over_eyes']
        self.assertEqual((later['yours_to_raise'], later['meaning']), (True, GUIDE['visor_over_eyes (yours to raise)']))
        self.assertIn('I kept it down for you', GUIDE['visor_over_eyes (yours to raise)'])
        self.assertIn('never on blind people', GUIDE['visor_over_eyes'])
        pose = next(t for t in TOOLS if t['name'] == 'set_robot_pose')['description']
        # Run 0421a222: worded as blinding him, lowering took four asks. The tool stays neutral; the
        # dark is noticed once it is down (the reminder and the beat that follows it).
        self.assertNotIn('see nothing', pose)
        self.assertIn('do either whenever asked', pose)
        from r3x.session import DARK_BEAT
        self.assertIn('never about blind people', DARK_BEAT)


class RaisingHisOwnVisorTests(memory_tests.MemoryFixture):
    # Owner, October 22: "he should be able to choose to raise his own visor if he wants to after a
    # while" (about 15 s, not the 90 s other poses wait); until then a shut visor stays shut.
    def owner(self):
        from r3x.servos import ServoOwner
        clock = test_servos.Clock()
        port = test_servos.PulsePort()
        owner = ServoOwner(lambda: port, medium='simulation', clock=clock, wait=clock.wait)
        owner.start(); self.addCleanup(owner.close)
        owner.execute(ServoRequest('boot', owner.version, 'startup', clock() + 120), Cancellation())
        return owner, clock, port

    def test_after_a_while_he_may_raise_it_and_until_then_it_stays_shut(self):
        from r3x.servo_poses import VISOR_DOWN, VISOR_RAISE_SECONDS, VISOR_UP
        owner, clock, port = self.owner()
        person = owner.execute(ServoRequest('down', owner.version, 'pose', clock() + 60, pose='visor_down'), Cancellation())
        self.assertEqual(person.status, 'pulses_reached')
        def his_own():
            return owner.execute(ServoRequest(uuid4().hex, owner.version, 'pose', clock() + 60, pose='visor_up',
                                              hold_pose=False), Cancellation())
        clock.wait(VISOR_RAISE_SECONDS - 5)
        self.assertFalse(owner.may_raise_visor())
        self.assertEqual(his_own().reason, 'AgencyCannotOverrideHeldPose')
        clock.wait(6)
        self.assertTrue(owner.may_raise_visor())
        clock.wait(90)                                         # Other holds have long given way...
        self.assertTrue(owner.yield_stale_hold())
        self.assertIn(3, owner.held_channels)                  # ...but idle never drifts it open.
        self.assertEqual(port.pose[3], VISOR_DOWN)
        self.assertEqual(his_own().status, 'pulses_reached')
        self.assertEqual(port.pose[3], VISOR_UP)
        self.assertNotIn(3, owner.held_channels)

    def test_a_beat_offers_it_and_may_only_raise_the_visor(self):
        from r3x.agency import AgencyPlan
        from r3x.present import Present
        from r3x.semantics import grounded_tools
        plan = AgencyPlan(self.store, self.state.trace, Present())
        plan.install(plan_of(2), '[]', {}, {'contribute'}, plan.revision, 10.)
        step = plan.step(11.)
        base = {'agency_step': step, 'robot_pose_enabled': True, 'conversation_intent': {'revision': 0}}
        self.assertNotIn('set_robot_pose', {t['name'] for t in grounded_tools(base)})
        shut = {**base, 'visor_over_eyes': {'closed': True, 'yours_to_raise': True}}
        self.assertIn('set_robot_pose', {t['name'] for t in grounded_tools(shut)})
        call = ToolCall('wave', 'set_robot_pose', {'say_before': None, 'poses': ['wave'], 'look_after': None, 'text': 'Hi.'})
        context = {**shut, 'state_revision': self.store.revision}
        with self.assertRaisesRegex(ValueError, 'OnlyHisOwnVisorThisBeat'):
            self.state.propose_pose(call, context, Cancellation())
        raised = ToolCall('up', 'set_robot_pose', {'say_before': None, 'poses': ['visor_up'], 'look_after': None,
                                                   'text': 'I kept it down for you, but it was dark in here!'})
        self.assertEqual(self.state.propose_pose(raised, context, Cancellation()), ('visor_up',))


class FastModeTests(memory_tests.MemoryFixture):
    # Owner, October 22: Rex's reply model runs in OpenAI Fast mode (2x the Standard price).
    def test_the_reply_tier_is_sent_and_the_served_tier_recorded(self):
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Snapshot
        from r3x.settings import Settings
        from tests.test_adapters import Client, Response
        self.assertEqual(Settings().reply_service_tier, 'fast')
        with self.assertRaises(ValueError):
            Settings(reply_service_tier='priority-ish')
        rows = []
        for tier, served in (('fast', 'default'), ('default', 'default')):
            brain = OpenAIBrain('fixture', tier)
            brain.trace = SimpleNamespace(emit=lambda event, **data: rows.append({'event': event, **data}))
            brain.client = Client(Response([{'type': 'response.output_text.delta', 'delta': 'Hi.'},
                {'type': 'response.completed', 'response': {'service_tier': served, 'usage': {'input_tokens': 10}}}]))
            list(brain.reply(Snapshot('s', 't', 0, ()), Cancellation()))
            sent = brain.client.calls[0][1]['json']
            self.assertEqual(sent.get('service_tier'), 'fast' if tier == 'fast' else None)
        usage = [r for r in rows if r['event'] == 'model_usage']
        self.assertEqual([r['served_tier'] for r in usage], ['default', 'default'])   # A fast request served at Standard shows.


class LateOctober22Tests(memory_tests.MemoryFixture):
    # Run 0421a222 (October 22, 2:37 PM): seventeen issues; each test names its own.
    setup_owner, tick = plans.PlanTests.setup_owner, plans.PlanTests.tick

    def servo_owner(self):
        return RaisingHisOwnVisorTests.owner(self)

    def test_visor_normal_puts_it_back_and_lets_it_move_again(self):
        # "You can put it back to the way it normally goes now" got words, then "the visor stays up".
        from r3x.servo_poses import VISOR_OPEN
        owner, clock, port = self.servo_owner()
        owner.execute(ServoRequest('up', owner.version, 'pose', clock() + 60, pose='visor_up'), Cancellation())
        self.assertIn(3, owner.held_channels)
        owner.execute(ServoRequest('normal', owner.version, 'pose', clock() + 60, pose='visor_normal'), Cancellation())
        self.assertEqual(port.pose[3], VISOR_OPEN)
        self.assertNotIn(3, owner.held_channels)

    def test_after_the_visor_closes_his_next_beat_is_about_the_dark(self):
        from r3x.session import DARK_BEAT
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: plan_of(6)
        self.tick(100)
        goal = self.state.agency.record['id']
        self.assertTrue(self.state.agency.insert_beat(DARK_BEAT, self.trace.at, requires_visor_shut=True))
        step = self.state.agency.step(self.trace.at)
        self.assertEqual((step['instruction'], step['requires_visor_shut']), (DARK_BEAT, True))
        self.tick(self.trace.at + 9)                 # No visor stand-in: it is not shut, so the beat is let go.
        outcomes = self.state.agency.record['outcomes']
        self.assertEqual(outcomes[-2]['revision_reason'], 'visor_open')
        self.assertEqual(self.state.agency.record['id'], goal)   # The goal carries on.

    def test_the_no_answer_wait_counts_from_his_last_line_not_a_silent_glance(self):
        from r3x.agency import AgencyPlan
        from r3x.present import Present
        present = Present()
        plan = AgencyPlan(self.store, self.state.trace, present)
        value = plan_of(2)
        value['steps'][1]['wake'] = {'when': 'unanswered', 'seconds': 15}
        plan.install(value, '[]', {}, {'contribute'}, plan.revision, 10.)
        present.rex_said('Is it the 600 millimeter one?', 12.)
        plan._skip_current('silent_glance', 20.)    # A glance between his line and this beat.
        self.assertEqual(plan._wake(26., 14.)[0], 'waiting')
        self.assertEqual(plan._wake(27.5, 15.)[0], 'ready')       # 15 s after the line, not after the glance.

    def test_a_closer_look_right_after_another_is_let_go(self):
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: plan_of(3, ('inspect', 'contribute', 'contribute'))
        self.session.output.inspection = SimpleNamespace(directions=('current',))   # Looks are possible...
        self.session.last_look_at = 95.                                             # ...and one just happened.
        self.tick(100)
        first = self.state.agency.record['outcomes'][0]
        self.assertEqual((first['kind'], first['revision_reason']), ('inspect', 'looked_just_now'))

    def test_a_persons_look_request_goes_to_the_pose_tool_and_down_is_a_direction(self):
        from r3x.semantics import grounded_tools
        base = {'attention_enabled': True, 'robot_pose_enabled': True, 'conversation_intent': {'revision': 0},
                'attention': {'vision': {'targets': []}}, 'source_turn_id': 't'}
        names = {t['name'] for t in grounded_tools(base)}
        self.assertIn('set_robot_pose', names)
        self.assertNotIn('shift_attention', names)               # Human turn: not the silent glance.
        beat = {t['name']: t for t in grounded_tools({**base, 'trigger': 'reconsideration'})}
        self.assertIn('down', beat['shift_attention']['parameters']['properties']['target']['enum'])
        self.assertIn('down', PhysicalAttention.targets)

    def test_a_misheard_name_still_shuts_him_down(self):
        from r3x.runtime import SHUTDOWN_COMMANDS
        for said in ('red shut down', 'red, shut down', 'rex shut down', 'shut down', 'wrecks shut down'):
            self.assertIn(said, SHUTDOWN_COMMANDS)
        self.assertNotIn('shut that idea down', SHUTDOWN_COMMANDS)

    def test_a_beat_opening_like_his_last_line_is_a_repeat(self):
        self.setup_owner()
        self.session.history.append({'role': 'assistant', 'content': "Mouth sparkle isn't a separate setting."})
        call = ToolCall('b', 'respond_with_intent', {'text': "Mouth sparkle isn't on the controls, but Voyager...",
                                                      'intent_update': None,
                                                      'invitation_update': None, 'next_opportunity': None})
        self.assertEqual(self.session._repeats_recent_line(call), "mouth sparkle isn't")

    def test_a_moved_on_beat_that_picks_silence_is_asked_once_more(self):
        self.setup_owner()
        self.planner.choose = lambda facts, cancel: plan_of(6)
        self.tick(100)
        PacingTests.say(self, 'My dogs chewed a shoe.')
        PacingTests.say(self, 'Toby ate the laces too.')
        seen = []
        def choose(snapshot, cancel):
            seen.append(snapshot)
            if len(seen) == 1:
                yield from let_go(snapshot, cancel)
            else:
                yield 'Toby has better taste in laces than most pilots.'
        self.executor.choose = choose
        self.assertEqual(self.tick(self.trace.at + 20).status, 'completed')
        self.assertEqual(len(seen), 2)
        self.assertIn('Silence needs a real reason', str(seen[1].messages))

    def test_at_startup_someone_in_view_is_greeted_before_he_plans(self):
        self.setup_owner()
        self.runtime.camera = SimpleNamespace(latest=lambda at: [face(at, None, 'newcomer')])
        self.runtime.started_at = 95.
        self.assertIsNone(self.tick(100))                       # The face has not settled into a greeting yet.
        self.assertFalse(self.planner.requests)
        self.assertIn('greeting_first', [r.get('reason') for r in self.trace.rows if r['event'] == 'initiative_gate'])
