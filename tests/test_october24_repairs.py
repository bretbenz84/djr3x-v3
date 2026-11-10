"""Run b5ef657c (October 24, the owner alone): one line taken as meant for someone else held Rex
silent and still for two minutes; the "what if" then came, was let go as talk between other
people, and held him two minutes more, taking a planned head tilt with it. Production Session,
state and runtime with a fake clock and scripted reply model; no I/O. Scripted silences do not
prove the hosted model's judgment."""
from types import SimpleNamespace
from uuid import uuid4

from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import TextOutput
from r3x.proactive import HUMAN_FLOOR_SECONDS
from r3x.session import Session
from tests import test_memory_core as memory_tests
from tests.test_hypotheticals import LOTTERY, QuietStretchFixture

SILENCES = {'other_addressee', 'background_conversation', 'no_response_needed'}


class QuietRoomTests(QuietStretchFixture):
    def script(self, *wonders, people='other_addressee'):
        """People's words get `people` (taken as meant for someone else); each "what if" the next answer."""
        answers = list(wonders)
        def choose(snapshot, cancel):
            step = snapshot.context.get('agency_step')
            text = (answers.pop(0) if step.get('wonder') else step['instruction']) if step else people
            if text in SILENCES:
                yield ToolCall(uuid4().hex, 'remain_silent', {'reason': text})
            else:
                yield ToolCall(uuid4().hex, 'respond_with_intent', {'text': text, 'intent_update': None, 'invitation_update': None, 'next_opportunity': None})
        self.executor.choose = choose

    def hear(self, at, text):
        self.trace.at = at
        capture = Capture(uuid4().hex, 'session', bytes(96000), at - 3, at, at, voiced_intervals=((0., 3.),))
        self.session.note_speech(at)
        return self.session.process_turn(capture, Transcript(text, -.01, True), self.session.revision,
                                         memory_tests.observation())

    def gates(self):
        return [r['reason'] for r in self.trace.rows if r['event'] == 'initiative_gate']

    def test_a_line_taken_for_someone_else_holds_his_goal_until_twenty_quiet_seconds_not_two_minutes(self):
        self.setup_owner(); self.script()
        self.tick(100)                                   # "Did you ever finish that bookshelf?"
        heard = self.trace.at + 4
        self.assertEqual(self.hear(heard, "He's at the truth.").status, 'suppressed')
        self.assertEqual(self.session.proactive_hold_until, heard + HUMAN_FLOOR_SECONDS)
        self.tick(heard + HUMAN_FLOOR_SECONDS - 5)
        self.assertEqual(self.gates()[-1], 'explicit_stop_or_human_floor')
        self.tick(heard + HUMAN_FLOOR_SECONDS + 1)       # Was a fixed 120 s.
        step = self.executor.snapshots[-1].context['agency_step']
        self.assertEqual(step['instruction'], 'React to their answer.')

    def test_the_what_if_let_go_as_talk_between_others_in_a_quiet_room_is_asked_once_more(self):
        self.setup_owner(); self.script('other_addressee', LOTTERY)
        self.tick(100)
        self.tick(self.trace.at + 46)
        retries = [r for r in self.trace.rows if r.get('purpose') == 'quiet_room_silence_retry']
        self.assertEqual(len(retries), 1)
        note = self.executor.snapshots[-1].messages[-1]['content']
        self.assertIn('Nobody has said a word for', note)
        self.assertEqual([r['text'] for r in self.store.rows('SELECT text FROM hypotheticals_asked')], [LOTTERY])
        self.assertLess(self.session.proactive_hold_until, self.trace.at)

    def test_a_what_if_let_go_for_no_reason_is_asked_once_more(self):
        self.setup_owner(); self.script('no_response_needed', LOTTERY)
        self.tick(100)
        self.tick(self.trace.at + 46)
        self.assertEqual([r['text'] for r in self.store.rows('SELECT text FROM hypotheticals_asked')], [LOTTERY])

    def test_still_let_go_it_holds_nothing_and_keeps_the_plans_own_follow_up(self):
        self.setup_owner(); self.script('other_addressee', 'other_addressee')
        self.tick(100)
        self.tick(self.trace.at + 46)
        self.assertEqual(len(self.wonders()), 2)         # Asked once more, then the silence stands.
        self.assertEqual(self.store.rows('SELECT * FROM hypotheticals_asked'), [])
        self.assertLess(self.session.proactive_hold_until, self.trace.at)
        self.assertFalse([r for r in self.trace.rows if r['event'] == 'agency_step_finished'
                          and r.get('revision_reason') == 'previous_beat_did_not_land'])
        self.assertEqual(self.state.agency.step(self.trace.at)['instruction'], 'React to their answer.')

    def test_a_beat_at_a_lull_soon_after_peoples_words_may_stay_out_without_being_asked_again(self):
        self.setup_owner(); self.script(people='Fair enough.')
        self.tick(100)
        heard = self.trace.at + 4
        self.hear(heard, 'Sam, pass the popcorn.')
        self.executor.choose = lambda snapshot, cancel: iter([ToolCall(uuid4().hex, 'remain_silent',
                                                                        {'reason': 'other_addressee'})])
        self.tick(heard + 15)                            # "React to their answer." at the lull after his reply.
        self.assertEqual(self.executor.snapshots[-1].context['agency_step']['instruction'], 'React to their answer.')
        self.assertFalse([r for r in self.trace.rows if r.get('purpose') == 'quiet_room_silence_retry'])
        self.assertEqual(self.session.proactive_hold_until, heard + HUMAN_FLOOR_SECONDS)


class FloorNoteTests(memory_tests.MemoryFixture):
    def test_the_floor_stays_with_people_while_they_talk_and_returns_twenty_seconds_after(self):
        self.begin('Sam, how was your interview?')
        self.assertEqual(self.call('remain_silent', {'reason': 'other_addressee'}).outcome, 'ok')
        start = self.state.trace.clock()
        self.assertTrue(self.state.initiative.floor_held(start + HUMAN_FLOOR_SECONDS - 1))
        self.assertFalse(self.state.initiative.floor_held(start + HUMAN_FLOOR_SECONDS + 1))
        brain = SimpleNamespace(reply=lambda snapshot, cancel: iter([ToolCall(uuid4().hex, 'remain_silent',
                                                                              {'reason': 'no_response_needed'})]))
        session = Session('session', brain, TextOutput(self.state.trace), self.state.trace, self.state)
        self.addCleanup(session.close)
        at = self.state.trace.at = start + 15         # Their talk goes on.
        capture = Capture(uuid4().hex, 'session', bytes(96000), at - 3, at, at, voiced_intervals=((0., 3.),))
        session.process_turn(capture, Transcript('It went fine, they liked my portfolio.', -.01, True), 0,
                             memory_tests.observation())
        self.assertTrue(self.state.initiative.floor_held(at + HUMAN_FLOOR_SECONDS - 1))
        self.assertFalse(self.state.initiative.floor_held(at + HUMAN_FLOOR_SECONDS + 1))
