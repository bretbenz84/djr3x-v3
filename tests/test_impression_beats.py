"""Impressions of Rex's own (owner, October 23): an impression beat the planner chose, played
through the production Session with the voice rendered before he says a word; the shared
cameo budget; people's voices only in a private plan; and reply-time cameos that may use a
known person's or the speaker's own voice. Blocked I/O: a fake cloner stands in for Breeze."""
import threading
from uuid import uuid4

from r3x import impersonation
from r3x.contracts import Cancellation, ToolCall
from r3x.output import OutputOwner
from r3x.scenario import SyntheticTTS, SyntheticSink
from r3x.semantics import grounded_tools
from tests import test_agency_plan as plan_tests
from tests import test_memory_core as memory_tests
from tests import test_turn_taking as timing_tests
from tests import test_impersonation as impression_tests
from tests.test_impersonation import SCRIPT, FakeCloner, impersonate

INTRO, OUTRO = 'Hold on, somebody has notes about that trip.', 'He gets like that.'


def impression_plan(instruction='Jimmy Carter (jimmy-carter) butts in about the peanut farm trip.', scope='general',
                    impressions=1):
    steps = [{'kind': 'impression', 'instruction': instruction, 'evidence_ids': [], 'wake': {'when': 'quiet', 'seconds': 5}}
             for _ in range(impressions)]
    steps.append({'kind': 'contribute', 'instruction': 'Tease them about road trip snacks.', 'evidence_ids': [],
                  'wake': {'when': 'quiet', 'seconds': 5}})
    return {'goal_kind': 'entertain', 'subject': 'the road trip', 'scope': scope,
            'narrative': 'Somebody famous would have opinions about that trip.',
            'objective': 'Bring in a voice that has opinions about the trip.', 'evidence_ids': [], 'steps': steps}


class OrderedSink(timing_tests.ClockSink):
    """Records, for each phrase played, whether every prepared take had rendered by then."""
    def __init__(self, trace, cloner):
        super().__init__(trace)
        self.cloner, self.rendered_at_play = cloner, []

    def play(self, chunks, cancel, on_start, visualize=True):
        self.rendered_at_play.append(all(t.ready.is_set() for t in self.cloner.takes))
        return super().play(chunks, cancel, on_start)


class ImpressionBeatTests(memory_tests.MemoryFixture):
    tick = plan_tests.PlanTests.tick
    visible = plan_tests.GoalTests.visible

    def setup_owner(self, cloner=None, target='jimmy-carter', plan=None):
        from types import SimpleNamespace
        from r3x.runtime import VoiceRuntime
        from r3x.session import Session
        self.trace = self.state.trace
        self.state.voice_references = impersonation.ReferenceStore(self.path.parent / 'cache' / 'voice_refs')
        self.cloner = cloner or FakeCloner()
        self.sink = OrderedSink(self.trace, self.cloner)
        self.executor, self.planner = plan_tests.Executor(), plan_tests.Planner()
        self.planner.choose = lambda facts, cancel: plan or impression_plan()
        self.target = target
        def perform(snapshot, cancel):
            if (snapshot.context.get('agency_step') or {}).get('kind') == 'impression':
                yield ToolCall(uuid4().hex, 'impersonate', {'target': self.target, 'script': SCRIPT, 'intro': INTRO,
                    'outro': OUTRO, 'requested': False, 'source_turn_id': snapshot.context['event_id']})
            else:
                yield 'Road trip snacks are a lifestyle, not a meal.'
        self.executor.choose = perform
        self.output = OutputOwner(SyntheticTTS(), self.sink, self.trace, cloner=self.cloner)
        self.session = Session('session', self.executor, self.output, self.trace, self.state)
        self.addCleanup(self.session.close)
        self.session.agency_planner = self.planner
        self.runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, self.session, self.trace)
        self.runtime.started_at = 0

    def impression_snapshots(self):
        return [s for s in self.executor.snapshots if (s.context.get('agency_step') or {}).get('kind') == 'impression']

    def test_planner_is_offered_the_voices_and_the_beat_plays_the_rendered_voice_straight_through(self):
        self.setup_owner(FakeCloner(delay=.1))
        result = self.tick(100)
        facts = self.planner.requests[0]
        self.assertIn('impression', facts['capabilities'])
        self.assertIn({'voice': 'jimmy-carter', 'name': 'Jimmy Carter'}, facts['voices']['famous'])
        self.assertEqual(facts['voices']['people'], [])   # Nobody known is here: no private voices.
        self.assertTrue(facts['voices']['available_now'])
        self.assertEqual(result.status, 'completed', result)
        self.assertEqual(result.completed_text, [INTRO, SCRIPT, OUTRO])
        # Nobody was waiting: the voice was ready before his own setup line began.
        self.assertEqual(self.sink.rendered_at_play, [True, True, True])
        snapshot = self.impression_snapshots()[0]
        tools = {t['name']: t for t in grounded_tools(snapshot.context)}
        self.assertEqual(set(tools), {'impersonate'})
        self.assertEqual(tools['impersonate']['parameters']['properties']['source_turn_id']['enum'],
                         [snapshot.context['event_id']])
        self.assertTrue(snapshot.context['impersonation']['beat'])
        outcome = self.state.agency.record['outcomes'][0]
        self.assertEqual((outcome['kind'], outcome['status']), ('impression', 'completed'))
        self.assertEqual(outcome['executor'], {'status': 'performed'})
        self.assertFalse(self.state.impressions.performed[0]['requested'])
        done = next(r for r in self.trace.rows if r['event'] == 'impression_done')
        self.assertEqual((done['occasion'], done['performed']), ('beat', True))
        notes = [m['content'] for m in self.session.history_messages() if m.get('role') == 'developer']
        self.assertTrue(any('your own impression beat' in n for n in notes))
        self.assertFalse(self.store.claims(include_candidates=True))

    def test_without_a_cloner_there_is_no_impression_capability(self):
        self.setup_owner()
        self.output.cloner = None
        self.planner.choose = lambda facts, cancel: plan_tests.draft()
        self.tick(100)
        self.assertNotIn('impression', self.planner.requests[0]['capabilities'])
        self.assertNotIn('voices', self.planner.requests[0])

    def test_a_second_impression_inside_the_budget_is_let_go_without_a_model_call(self):
        self.setup_owner(plan=impression_plan(impressions=2))
        self.assertEqual(self.tick(100).status, 'completed')
        self.tick(self.trace.at + 9)
        record = self.state.agency.record
        self.assertEqual([o['status'] for o in record['outcomes']], ['completed', 'skipped', 'completed'])
        self.assertEqual(record['outcomes'][1]['revision_reason'], 'impression_unavailable')
        self.assertEqual(len(self.impression_snapshots()), 1)
        self.assertEqual(len(self.cloner.takes), 1)

    def test_a_cameo_in_a_reply_spends_the_same_budget(self):
        self.setup_owner()
        self.state.impressions.record('famous:ronald-reagan', 'Ronald Reagan', SCRIPT, False, 95.)
        self.tick(100)
        self.assertEqual(self.state.agency.record['outcomes'][0]['revision_reason'], 'impression_unavailable')
        self.assertEqual(self.impression_snapshots(), [])
        self.assertEqual(self.cloner.takes, [])

    def test_a_failed_render_says_nothing_and_the_goal_goes_on(self):
        self.setup_owner(FakeCloner(fail=True, delay=.05))
        result = self.tick(100)
        self.assertEqual(result.completed_text, [])
        self.assertEqual(self.sink.rendered_at_play, [])
        self.assertEqual(self.state.impressions.performed, type(self.state.impressions.performed)(maxlen=16))
        outcome = self.state.agency.record['outcomes'][0]
        self.assertEqual(outcome['status'], 'skipped')
        self.assertEqual(self.state.agency.record['cursor'], 1)
        self.assertTrue(self.cloner.takes[0].closed)

    def test_a_stop_while_the_voice_renders_closes_it_unheard(self):
        self.setup_owner(FakeCloner(never=True))
        threading.Timer(.1, self.session.cancel).start()
        result = self.tick(100)
        self.assertEqual(result.completed_text, [])
        self.assertTrue(self.cloner.takes[0].closed)
        self.assertEqual(len(self.state.impressions.performed), 0)

    def test_a_spoken_beat_can_become_an_impression_unless_he_is_being_ignored(self):
        self.setup_owner(FakeCloner(delay=.1), plan=plan_tests.draft())
        def perform(snapshot, cancel):
            offered = {t['name'] for t in grounded_tools(snapshot.context)}
            self.offered.append('impersonate' in offered)
            yield ToolCall(uuid4().hex, 'impersonate', {'target': 'jimmy-carter', 'script': SCRIPT, 'intro': INTRO,
                'outro': None, 'requested': False, 'source_turn_id': snapshot.context['event_id']})
        self.offered = []
        self.executor.choose = perform
        result = self.tick(100)
        self.assertEqual(self.offered, [True])
        self.assertEqual(result.completed_text, [INTRO, SCRIPT])
        self.assertEqual(self.sink.rendered_at_play, [True, True])
        self.assertEqual(self.state.agency.record['outcomes'][0]['status'], 'completed')
        self.assertEqual(self.state.impressions.performed[0]['voice'], 'famous:jimmy-carter')
        self.assertIsNone(self.session._beat_voices({'impression_enabled': True}, {'scope': 'general', 'unanswered_lines': 1}))
        self.state.impressions.performed.clear()
        self.assertIsNotNone(self.session._beat_voices({'impression_enabled': True}, {'scope': 'general', 'unanswered_lines': 1}))
        self.assertIsNone(self.session._beat_voices({'impression_enabled': True}, {'scope': 'general', 'unanswered_lines': 2}))

    def test_the_person_here_can_be_done_in_their_own_voice(self):
        bret = self.enroll('Bret', memory_tests.A)
        self.setup_owner(target=bret, plan=impression_plan(
            instruction=f"Bret ({bret}) explains telescope mount number four, as only he would.", scope='audience'))
        self.visible(bret, 'bret', 100)
        self.tick(100)   # Greeted first.
        result = self.tick(self.trace.at + 9)
        facts = self.planner.requests[0]
        self.assertEqual(facts['voices']['people'], [{'voice': bret, 'name': 'Bret', 'here': True}])
        snapshot = self.impression_snapshots()[0]
        self.assertEqual(snapshot.context['impersonation']['people'], [{'target_id': bret, 'name': 'Bret', 'here': True}])
        self.assertEqual(result.completed_text, [INTRO, SCRIPT, OUTRO])
        self.assertEqual(self.cloner.takes[0].reference.key, 'person:' + bret)


class BeatVoiceStateTests(memory_tests.MemoryFixture):
    def setUp(self):
        super().setUp()
        self.state.voice_references = impersonation.ReferenceStore(self.path.parent / 'cache' / 'voice_refs')

    def beat(self, scope, target, requested=False, here=()):
        voices = impersonation.beat_context(self.state, self.state.trace.clock(), scope, here)
        context = {'event_id': 'event', 'state_revision': self.store.revision, 'trigger': 'conversation_lull',
                   'agency_step': {'kind': 'impression', 'scope': scope}, 'impersonation': voices}
        call = ToolCall(uuid4().hex, 'impersonate', {'target': target, 'script': SCRIPT, 'intro': None, 'outro': None,
                                                       'requested': requested, 'source_turn_id': 'event'})
        return voices, self.state.execute_opportunity(call, context, Cancellation())

    def test_a_general_beat_offers_famous_voices_only(self):
        bret = self.enroll('Bret', memory_tests.A)
        voices, result = self.beat('general', bret, here=[bret])
        self.assertEqual(voices['people'], [])
        self.assertIn('jimmy-carter', voices['famous'])
        self.assertNotIn('speaker', voices)   # No current speaker on a beat: no "self".
        self.assertEqual(result.reason, 'ImpressionTargetNotOffered')
        voices, result = self.beat('audience', bret, here=[bret])
        self.assertEqual(voices['people'], [{'target_id': bret, 'name': 'Bret', 'here': True}])
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual((result.data['voice'], result.data['occasion']), ('person:' + bret, 'beat'))

    def test_a_beat_cannot_claim_it_was_asked_for_and_respects_the_voice_rule(self):
        now = self.state.trace.clock()
        self.state.impressions.record('famous:jimmy-carter', 'Jimmy Carter', SCRIPT, False, now - 1200)
        voices, result = self.beat('general', 'jimmy-carter')
        self.assertIn('jimmy-carter', voices['famous'])   # The list stays whole for the cached static block.
        self.assertEqual(result.reason, 'SpontaneousImpressionCooldown:same_voice_recently')
        _, result = self.beat('general', 'john-kennedy', requested=True)
        self.assertEqual(result.outcome, 'ok', result)
        self.assertFalse(result.data['requested'])
        self.assertLessEqual(len(result.data['script'].split()), impersonation.CAMEO_MAX_SCRIPT_WORDS)

    def test_no_voices_are_offered_while_the_budget_is_spent(self):
        now = self.state.trace.clock()
        self.state.impressions.record('famous:jimmy-carter', 'Jimmy Carter', SCRIPT, False, now - 60)
        self.assertIsNone(impersonation.beat_context(self.state, now, 'general'))
        self.assertEqual(self.state.impressions.wait_seconds(now), 540)
        for at in (now - 4000, now - 3000, now - 2000):
            self.state.impressions.record('famous:john-kennedy', 'John Kennedy', SCRIPT, False, at)
        self.assertIsNone(self.state.impressions.wait_seconds(now))
        self.assertFalse(impersonation.planner_voices(self.state, now)['available_now'])

    def test_the_tool_is_offered_on_impression_and_spoken_beats_only(self):
        voices = impersonation.beat_context(self.state, self.state.trace.clock(), 'general')
        base = {'event_id': 'event', 'trigger': 'conversation_lull', 'impersonation': voices}
        names = {t['name'] for t in grounded_tools({**base, 'agency_step': {'kind': 'gesture', 'scope': 'general'}})}
        self.assertNotIn('impersonate', names)
        self.assertNotIn('impersonate', {t['name'] for t in grounded_tools(base)})   # An ordinary lull.
        spoken = {'kind': 'contribute', 'scope': 'general'}
        names = {t['name'] for t in grounded_tools({**base, 'agency_step': spoken, 'conversation_intent': {'active': None}})}
        self.assertEqual(names, {'respond_with_intent', 'impersonate'})
        # Never the only way to do a spoken beat.
        self.assertEqual(grounded_tools({**base, 'agency_step': spoken}), [])
        talked = {t['name'] for t in grounded_tools({**base, 'agency_step': {'kind': 'impression', 'scope': 'general',
                                                                             'people_spoke_since_planned': True}})}
        self.assertEqual(talked, {'impersonate', 'remain_silent', 'wait_for'})

    def test_a_reply_cameo_now_keeps_the_voice_rule_too(self):
        self.begin('We are going to the Carter library next week.', memory_tests.C)
        self.state.impressions.record('famous:jimmy-carter', 'Jimmy Carter', SCRIPT, False, self.state.trace.clock() - 1200)
        context = self.state.context()
        context['impersonation'] = impersonation.context(self.state, self.state.trace.clock())
        def call(requested):
            return self.state.execute(ToolCall(uuid4().hex, 'impersonate', {'target': 'jimmy-carter', 'script': SCRIPT,
                'intro': None, 'outro': None, 'requested': requested, 'source_turn_id': context['source_turn_id']}),
                self.store.revision, Cancellation(), context=context)
        self.assertEqual(call(False).reason, 'SpontaneousImpressionCooldown:same_voice_recently')
        self.assertEqual(call(True).outcome, 'ok')


class ReplyCameoTests(memory_tests.MemoryFixture):
    setup_session = impression_tests.SessionTests.setup_session
    turn = impression_tests.SessionTests.turn

    def test_a_cameo_waits_with_the_thinking_chirps_after_his_line(self):
        played = []
        class CountingSink(SyntheticSink):
            def play(self, chunks, cancel, on_start, visualize=True):
                played.append(visualize)
                return super().play(chunks, cancel, on_start, visualize)
        self.setup_session(lambda s: impersonate(s, requested=False, intro='Plains? Somebody has thoughts.', outro=None),
                           FakeCloner(delay=.3), CountingSink())
        result = self.turn('We are driving down to Plains, Georgia.')
        self.assertEqual(result.completed_text, ['Plains? Somebody has thoughts.', SCRIPT])
        self.assertIn(False, played)   # The filler ran between his line and the voice.
        self.assertEqual(next(r for r in self.state.trace.rows if r['event'] == 'impression_done')['occasion'], 'cameo')

    def test_a_cameo_may_use_a_known_persons_voice(self):
        bret = self.enroll('Bret', memory_tests.A)
        self.setup_session(lambda s: impersonate(s, target=bret, requested=False, intro='Bret would say this.', outro=None))
        result = self.turn('Bret is rebuilding the telescope mount again.', memory_tests.C)
        self.assertEqual(result.completed_text, ['Bret would say this.', SCRIPT])
        self.assertEqual(self.cloner.takes[0].reference.key, 'person:' + bret)


class CameoReminderTests(memory_tests.unittest.TestCase):
    def reminder(self, context):
        from r3x.adapters.hosted import OpenAIBrain
        from r3x.contracts import Snapshot
        from tests.test_adapters import Client, Response
        brain = OpenAIBrain('fixture')
        brain.client = Client(Response([{'type': 'response.output_text.delta', 'delta': 'Hi.'}, {'type': 'response.completed'}]))
        list(brain.reply(Snapshot('s', 't', 0, ({'role': 'user', 'content': 'We are off to the Reagan library.'},),
                                  tools_enabled=True, context={'source_turn_id': 't', **context}), Cancellation()))
        return brain.client.calls[0][1]['json']['input'][-1]['content']

    def test_a_person_mentioning_a_voice_he_has_is_named_as_a_cameo_chance_only_while_one_is_allowed(self):
        # Paid probe, October 23: "I'm finally driving out to the Reagan library" got plain prose.
        allowed = {'available': True, 'spontaneous_allowed': True, 'famous': ['ronald-reagan']}
        self.assertIn('surprise cameo', self.reminder({'impersonation': allowed}))
        self.assertNotIn('surprise cameo', self.reminder({'impersonation': {**allowed, 'spontaneous_allowed': False}}))
        self.assertNotIn('surprise cameo', self.reminder({}))
