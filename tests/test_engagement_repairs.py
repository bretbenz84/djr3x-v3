"""September 29 regressions: production owners, synthetic audio and decisions."""
from uuid import uuid4
import json

from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests.test_memory_core import MemoryFixture, observation, C
from tests.test_proactive import face
from tests import test_proactive_core
from tests.test_conversation_intent import IntentBrain, proposal


class EngagementRepairs(MemoryFixture):
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    turn = test_proactive_core.InitiativeCoreTests.turn
    visible = test_proactive_core.InitiativeCoreTests.visible

    def noise(self, at):
        self.trace.at = at
        capture = Capture(uuid4().hex, 'session', bytes(9600), at-.3, at-.1, at)
        self.session.note_speech(at-.1, sustained=False)
        return self.session.process_turn(capture, Transcript('Okay.', -.8, False), 0, observation())

    def test_rejected_audio_is_archived_without_resetting_lull_or_evicting_dialogue(self):
        self.setup_core(); self.turn('Rex, let us talk about homemade instruments.')
        source = self.state.conversation_source
        original = self.session.history.messages()
        for i in range(40):
            self.assertEqual(self.noise(100 + (i+1)*.19).status, 'suppressed')
        self.assertEqual(self.session.last_interaction_at, 100)
        self.assertEqual(self.state.conversation_source, source)
        self.assertEqual(self.session.history.messages(), original)
        self.assertEqual(len(self.store.rows('SELECT * FROM turns')), 41)
        self.visible(108); self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls, 2)
        self.assertEqual(self.brain.snapshots[-1].context['opportunity']['source_turn_id'], source)
        self.assertEqual(self.brain.snapshots[-1].context['opportunity']['quiet_seconds'], 8)
        continuity = self.brain.snapshots[-1].context['conversation_continuity']
        self.assertEqual(continuity['last_completed_reply'], original[-1]['content'])
        self.assertFalse(continuity['new_human_utterance'])

    def test_unresolved_microphone_activity_still_defers_an_opportunity(self):
        self.setup_core(); self.turn(); self.visible(108)
        self.session.note_speech(107.9, sustained=False)
        self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls, 1)
        self.noise(108)
        self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls, 2)

    def test_trusted_short_reply_resets_lull_and_source(self):
        self.setup_core(); self.turn(); old = self.state.conversation_source
        self.visible(107); self.turn('Yes.')
        self.assertNotEqual(self.state.conversation_source, old)
        self.visible(114.9); self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls, 2)
        self.visible(115); self.runtime.proactive_tick()
        self.assertEqual(self.brain.calls, 3)

    def test_missing_intention_does_not_end_conversation_and_budget_still_caps_calls(self):
        self.setup_core(); self.turn()
        for at, calls in ((108,2),(115.9,2),(116,3),(130.9,3),(131,4),
                          (161,5),(221,6),(281,7),(341,7)):
            self.visible(at); self.runtime.proactive_tick()
            self.assertEqual(self.brain.calls, calls)
        self.assertIsNone(self.state.conversation_intent.record)
        self.assertEqual(len(self.state.initiative.attempts), 6)

    def test_empty_rejected_write_continuation_recovers_once_without_reexecuting(self):
        person = self.enroll()
        requests = []
        class Recovery:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield ToolCall('save', 'record_claim', dict(subject_id=person,
                        subject_reference=None, predicate='built', value='a homemade telescope',
                        kind='self_report', source_turn_id=snapshot.context['source_turn_id']))
                elif len(requests) == 3:
                    yield 'Did you print the mirror mount too?'
        self.setup_core(Recovery(), OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace))
        result = self.turn('I printed a telescope.', observation(C))
        self.assertEqual(result.completed_text, ['Did you print the mirror mount too?'])
        self.assertEqual(len(requests), 3)
        self.assertFalse(requests[-1].tools_enabled)
        self.assertEqual(requests[1].continuation, requests[2].continuation)
        self.assertEqual(json.loads(requests[1].continuation[-1]['output'])['reason'], 'UnresolvedSelfReportSubject')
        self.assertEqual(len([r for r in self.trace.rows if r['event']=='tool_executed']), 1)
        self.assertEqual(self.store.claims(include_candidates=True), [])

    def test_empty_recovery_is_bounded_and_does_not_fabricate_speech(self):
        requests = []
        class Empty:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield ToolCall('recall', 'recall', {'subject_id': None})
        self.setup_core(Empty())
        result = self.turn('What do you remember?')
        self.assertEqual(len(requests), 3)
        self.assertEqual(result.error, 'EmptyToolContinuation')
        self.assertEqual(result.completed_text, [])

    def test_new_speech_cancels_empty_continuation_recovery(self):
        requests = []
        outer = self
        class Interrupted:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield ToolCall('recall', 'recall', {'subject_id': None})
                else:
                    outer.session.note_speech(200)
                    cancel.check()
        self.setup_core(Interrupted())
        result = self.turn('What do you remember?')
        self.assertEqual(len(requests), 2)
        self.assertEqual(result.status, 'interrupted')
        self.assertEqual(result.completed_text, [])

    def test_targeted_question_still_rejects_camera_change_before_speech(self):
        current = [face(100)]
        class Changed(IntentBrain):
            def reply(_, snapshot, cancel):
                current.clear()
                yield ToolCall('question', 'ask_face_question', dict(
                    text='What should I call you?', face_track_id='face',
                    intent_update=proposal(snapshot.context)))
        self.setup_core(Changed())
        capture = Capture('c', 'session', bytes(96000), 96, 99, 100)
        result = self.session.process_turn(capture, Transcript('Hello.', 0, True), 0,
            observation(), tuple(current), latest_faces=lambda now: current)
        self.assertEqual(result.error, 'IntentAudienceChanged')
        self.assertEqual(result.completed_text, [])
        self.assertIsNone(self.state.pending)

    def test_general_greeting_survives_continuous_recognized_track_replacement(self):
        from r3x.proactive import Arrivals
        arrivals = Arrivals()
        for at in (0,.5,1,1.5,2,2.5,3):
            arrivals.observe([face(at,'person','old')], at)
        event = arrivals.take('session', 3, float('-inf'))
        self.assertIsNotNone(event)
        arrivals.observe([face(3.5,'person','replacement')], 3.5)
        self.assertTrue(arrivals.valid(event, 3.5, audience_bound=False))
        self.assertFalse(arrivals.valid(event, 3.5, audience_bound=True))
        arrivals.observe([face(4,'other','replacement')], 4)
        self.assertFalse(arrivals.valid(event, 4, audience_bound=False))
