""""Do you ever think..." after a long quiet stretch (owner, October 23): 45 seconds with nobody
talking, Rex included, brings one imaginative question for someone in view, once per quiet
stretch. Production Session and runtime with a fake clock and scripted reply model; no I/O."""
from types import SimpleNamespace

from r3x import hypotheticals
from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticTTS
from r3x.semantics import grounded_tools
from r3x.session import Session
from tests import test_memory_core as memory_tests
from tests import test_turn_taking as timing_tests
from tests.test_agency_plan import Executor, Planner, draft
from tests.test_wake import beat

LOTTERY = 'Do you ever think about what would happen if everyone won the lottery on the same day?'
DOGS = 'What if dogs could talk, but only for five minutes a day?'


class QuietStretchFixture(memory_tests.MemoryFixture):
    def setup_owner(self, wonder_lines=(LOTTERY, DOGS), first='Did you ever finish that bookshelf?'):
        self.trace = self.state.trace
        self.executor = Executor(); self.planner = Planner()
        # The second beat waits for an answer nobody gives, so the room really goes quiet.
        self.planner.choose = lambda facts, cancel: {**draft(), 'steps': [
            beat(first, 'quiet', 5), beat('React to their answer.', 'answered', 5, follows=True)]}
        lines = list(wonder_lines)
        def choose(snapshot, cancel):
            step = snapshot.context.get('agency_step') or {}
            text = lines.pop(0) if step.get('wonder') else step.get('instruction') or 'Fair enough.'
            if text is None:
                yield ToolCall('silent', 'remain_silent', {'reason': 'no_response_needed'})
            else:   # A spoken beat comes as respond_with_intent, as the hosted model sends it.
                yield ToolCall('say', 'respond_with_intent', {'text': text, 'intent_update': None, 'used_material': None,
                                                              'invitation_update': None, 'next_opportunity': None})
        self.executor.choose = choose
        self.session = Session('session', self.executor, OutputOwner(SyntheticTTS(),
            timing_tests.ClockSink(self.trace), self.trace), self.trace, self.state)
        self.session.agency_planner = self.planner
        self.runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, self.session, self.trace)
        self.runtime.started_at = 0
        self.speak(90)

    def speak(self, at):
        self.trace.at = at
        self.state.agency.human_spoke(None, at)
        self.session.last_trusted_speech_at = at

    def tick(self, at, seen=True):
        self.trace.at = at; self.runtime.audio_seen_at = at
        if seen:   # Someone sits in view without a word.
            self.state.present.saw_faces([{'at': at, 'identity': None, 'track_id': 'face', 'face_width_px': 120}], at)
        return self.runtime.proactive_tick()

    def wonders(self):
        return [s for s in self.executor.snapshots if (s.context.get('agency_step') or {}).get('wonder')]


class QuietStretchTests(QuietStretchFixture):
    def test_forty_five_quiet_seconds_bring_one_what_if_and_no_second_until_someone_speaks(self):
        self.setup_owner()
        self.tick(100)                       # His own beat, then nothing: the follow-up waits for an answer.
        spoke_at = self.trace.at
        self.tick(spoke_at + 40)
        self.assertEqual(self.wonders(), [])  # Not yet 45 s.
        self.tick(spoke_at + 46)
        first = self.wonders()
        self.assertEqual(len(first), 1)
        step = first[0].context['agency_step']
        self.assertEqual(step['instruction'], hypotheticals.INSTRUCTION)
        self.assertEqual(first[0].context['hypotheticals']['asked_before'], [])
        self.assertIn('remain_silent', {t['name'] for t in grounded_tools(first[0].context)})
        self.assertEqual(self.store.rows('SELECT text FROM hypotheticals_asked')[0]['text'], LOTTERY)
        for gap in (60, 120, 200):           # Nobody answers: no prodding, no second question.
            self.tick(self.trace.at + gap)
        self.assertEqual(len(self.wonders()), 1)
        self.speak(self.trace.at + 5)        # Someone speaks; a new quiet stretch begins after it.
        self.tick(self.trace.at + 50)
        later = self.wonders()
        self.assertEqual(len(later), 2)
        self.assertEqual(later[1].context['hypotheticals']['asked_before'], [LOTTERY])
        self.assertEqual([r['text'] for r in self.store.rows('SELECT text FROM hypotheticals_asked ORDER BY asked_at')],
                         [LOTTERY, DOGS])

    def test_nobody_in_view_gets_no_question(self):
        self.setup_owner()
        self.tick(100)
        self.tick(self.trace.at + 60, seen=False)
        self.assertEqual(self.wonders(), [])

    def test_his_own_unanswered_question_does_not_block_it(self):
        # His last line was a question (the bookshelf); 45 s on, the "what if" is the one new question.
        self.setup_owner()
        self.tick(100)
        self.tick(self.trace.at + 46)
        self.assertEqual(len(self.wonders()), 1)
        self.assertFalse([r for r in self.trace.rows if r['event'] == 'agency_question_stacked'])

    def test_let_go_in_silence_it_is_not_recorded_or_asked_again_that_stretch(self):
        # October 24 (b5ef657c): let go without a reason, it is asked once more; then the silence stands.
        self.setup_owner(wonder_lines=(None, None))
        self.tick(100)
        self.tick(self.trace.at + 46)
        self.tick(self.trace.at + 60)
        self.assertEqual(len(self.wonders()), 2)
        self.assertEqual(self.store.rows('SELECT * FROM hypotheticals_asked'), [])

    def test_a_reply_soon_after_can_take_it_up(self):
        self.setup_owner()
        self.tick(100)
        self.tick(self.trace.at + 46)
        at = self.trace.at + 4
        capture = Capture('c', 'session', bytes(96000), at - 3, at, at, voiced_intervals=((0., 3.),))
        self.session.note_speech(at)
        self.session.process_turn(capture, Transcript('Honestly, I would buy a boat.', -.01, True), self.session.revision,
                                  memory_tests.observation())
        self.assertEqual(self.executor.snapshots[-1].context['your_hypothetical']['asked'], LOTTERY)
        self.trace.at += hypotheticals.OPEN_SECONDS + 1
        capture = Capture('d', 'session', bytes(96000), self.trace.at - 3, self.trace.at, self.trace.at,
                          voiced_intervals=((0., 3.),))
        self.session.process_turn(capture, Transcript('Anyway.', -.01, True), self.session.revision,
                                  memory_tests.observation())
        self.assertNotIn('your_hypothetical', self.executor.snapshots[-1].context)


class RecordTests(memory_tests.MemoryFixture):
    def test_questions_asked_privately_go_with_their_person_and_forgetting_removes_them(self):
        bret = self.enroll('Bret', memory_tests.A)
        with self.store.transaction() as conn:
            hypotheticals.record(conn, None, LOTTERY, 10.)
            hypotheticals.record(conn, bret, 'What if your telescope could talk back?', 20.)
        self.assertEqual(hypotheticals.asked_before(self.store), [LOTTERY])
        self.assertEqual(hypotheticals.asked_before(self.store, bret), ['What if your telescope could talk back?', LOTTERY])
        self.begin('Please forget me.', memory_tests.A)
        self.assertEqual(self.call('forget_person', {'person_id': bret}).outcome, 'ok')
        self.assertEqual(hypotheticals.asked_before(self.store, bret), [LOTTERY])
