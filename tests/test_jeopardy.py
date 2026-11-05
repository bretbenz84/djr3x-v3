"""Jeopardy (owner, October 26): V2's verbal game hosted by Rex. Code keeps the board, turns, clock and
scores; the reply model hears the moves through the game tools. Synthetic clues, a scripted model, fake
clocks and a synthetic sink: these prove the game's contracts, not how well the hosted model judges spoken
responses (tools/check_jeopardy_judging.py), how the music and stings sound in the room, or whether
answers are heard over the think music on the robot."""
import csv
import json
import random
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from r3x.contracts import Cancellation, Cancelled, Capture, Playback, ToolCall, Transcript
from r3x.jeopardy import (WHO_IS_PLAYING, ANSWER_PAUSE_SECONDS, ANSWER_SECONDS, ECHO_TAIL_SECONDS, IDLE_END_SECONDS,
                          SPEECH_GRACE_SECONDS, ClueBank, Jeopardy, as_question, directive, request_view,
                          speak_category, valid_clue)
from r3x.output import Delivery, MusicBed, OutputOwner, Sound, TextOutput, put_phrase
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import TOOLS, grounded_tools, validate
from r3x.session import Session
from tests.test_memory_core import MemoryFixture


def write_bank(root: Path) -> Path:
    """Two rounds of six categories and two Final clues, with one Daily Double per round."""
    path = root / 'clues.tsv'
    with path.open('w', newline='', encoding='utf-8') as f:
        out = csv.writer(f, delimiter='\t')
        out.writerow(['round', 'clue_value', 'daily_double_value', 'category', 'comments', 'answer', 'question',
                      'air_date', 'notes'])
        for round_no, values in ((1, (200, 400, 600, 800, 1000)), (2, (400, 800, 1200, 1600, 2000))):
            for c in 'ABCDEF':
                for v in values:
                    daily = (round_no, c, v) in {(1, 'C', 800), (2, 'D', 1600)}
                    out.writerow([round_no, v, 1000 if daily else 0, f'CAT {c}{round_no}', '',
                                  f'Clue {c}{round_no} for {v} dollars', f'answer {c}{round_no} {v}', '2001-01-01', ''])
        # A Clue Crew demonstration Rex cannot show is never dealt.
        for v in (200, 400, 600, 800, 1000):
            out.writerow([1, v, 0, 'CREW', '(Jimmy of the Clue Crew)', f'He demonstrates this {v}', 'x', '2001-01-01', ''])
        out.writerow([3, 0, 0, 'FINAL CAT', '', 'The final clue text', 'the final answer', '2001-01-01', ''])
    return path


class Clock:
    def __init__(self):
        self.at = 1000.

    def __call__(self):
        return self.at


class GameTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bank = ClueBank(write_bank(Path(self.temp.name)))
        self.clock = Clock()
        self.game = Jeopardy(self.clock, rng=random.Random(0), bank=self.bank)

    def move(self, name, args, **kw):
        parts = self.game.plan(name, args, **kw)
        self.game.commit()
        return parts

    def start(self, *players, screen=False, people=None):
        self.game.screen = screen
        return self.move('jeopardy_setup', {'players': list(players), 'text': None}, people=people or {})

    def square(self, category_name, value):
        board = self.game.context(self.clock())['board']
        cid = next(c['category_id'] for c in board if c['category'] == category_name)
        return self.move('jeopardy_pick', {'category_id': cid, 'value': value})

    def opened(self, parts):
        line = next(v for k, v in parts if k == 'open')
        self.assertTrue(self.game.heard(line, self.clock()))
        return line

    def rule(self, verdict, heard='What is it?', **kw):
        attempt = self.game.context(self.clock())['clue']['attempt_id']
        return self.move('jeopardy_ruling', {'attempt_id': attempt, 'response': True, 'heard': heard, 'comparison': 'x',
                                             'sounds_like_correct': False, 'question_form': True, 'verdict': verdict, 'text': None}, **kw)

    def scores(self):
        return {p['name']: p['score'] for p in self.game.context(self.clock())['players']}

    def says(self, parts):
        return ' '.join(v for k, v in parts if k in {'say', 'open'})

    def test_a_named_roster_deals_the_board_and_the_phrasing_rule_comes_before_the_first_pick(self):
        opened = self.start()
        self.assertEqual(opened, [['sound', 'intro'], ['model', None], ['say', WHO_IS_PLAYING]])   # Code asks, not his line.
        self.assertEqual(self.game.context(self.clock())['phase'], 'players')
        parts = self.move('jeopardy_setup', {'players': ['Bret', 'Jeff'], 'text': None})
        self.assertEqual(parts[1], ['sound', 'board'])
        said = [v for k, v in parts if k == 'say']
        self.assertTrue(said[0].startswith('The categories: '))
        self.assertEqual(said[-2:], ['Remember: phrase your response as a question.', 'Bret, pick a category and a value.'])
        self.assertNotIn('CREW', said[0])
        self.assertEqual(self.game.context(self.clock())['phase'], 'selecting')

    def test_one_person_named_twice_is_one_seat_and_five_players_are_refused(self):
        self.start('Jeremy', 'JT', 'Bret', people={'Jeremy': 'p4', 'JT': 'p4', 'Bret': 'p1'})
        self.assertEqual([p['name'] for p in self.game.context(self.clock())['players']], ['Jeremy', 'Bret'])
        self.assertEqual(self.game.gaze_person(), 'p4')                        # Display and gaze only.
        game = Jeopardy(self.clock, bank=self.bank)
        with self.assertRaises(ValueError) as refused:
            game.plan('jeopardy_setup', {'players': ['A', 'B', 'C', 'D', 'E'], 'text': None})
        self.assertEqual(str(refused.exception), 'AtMostFourPlayers')
        with self.assertRaises(ValueError) as refused:
            self.game.plan('jeopardy_setup', {'players': ['Jeremy', 'me'], 'text': None})   # "Can I join?", unknown voice.
        self.assertEqual(str(refused.exception), 'PlayerNameIsAPronoun')

    def test_the_clock_starts_only_once_the_clue_is_heard_and_a_cut_off_clue_is_read_once_more(self):
        self.start('Bret', 'Jeff')
        parts = self.square('CAT A1', 200)
        line = next(v for k, v in parts if k == 'open')
        self.assertEqual(line, 'CAT A1 for $200. Clue A1 for 200 dollars.')       # No screen: the square first.
        self.assertEqual(self.game.context(self.clock())['clue']['clock'], 'not started: the clue has not been heard yet')
        self.clock.at += 60
        self.assertEqual(self.game.tick(self.clock(), False), [['open', line]])  # Said again, once.
        self.clock.at += 60
        self.assertIsNone(self.game.tick(self.clock(), False))                    # No clock nobody heard start.
        self.game.heard(line, self.clock())
        self.clock.at += ECHO_TAIL_SECONDS + ANSWER_SECONDS - .1
        self.assertIsNone(self.game.tick(self.clock(), False))
        self.clock.at += .2
        up = self.game.tick(self.clock(), False)
        self.assertEqual(up[:2], [['sound', 'timesup'], ['say', "Time's up."]])
        self.assertEqual(up[2], ['open', "Jeff, it's yours. CAT A1 for $200. Clue A1 for 200 dollars."])

    def test_speech_at_the_buzzer_is_judged_before_times_up(self):
        self.start('Bret', 'Jeff')
        self.opened(self.square('CAT A1', 200))
        self.clock.at += ECHO_TAIL_SECONDS + ANSWER_SECONDS + 1
        self.assertIsNone(self.game.tick(self.clock(), True))
        self.game.pause(self.clock())        # The answer's turn: the clock waits for Rex.
        self.rule('correct')
        self.assertEqual(self.scores(), {'Bret': 200, 'Jeff': 0})
        # A room that never stops talking still times out at the ceiling.
        self.opened(self.square('CAT A1', 400))
        self.clock.at += ECHO_TAIL_SECONDS + ANSWER_SECONDS + SPEECH_GRACE_SECONDS + .1
        self.assertEqual(self.game.tick(self.clock(), True)[0], ['sound', 'timesup'])

    def test_a_right_answer_keeps_the_board_and_a_wrong_one_gets_one_rebound_then_the_reveal(self):
        self.start('Bret', 'Jeff', 'Jeremy')
        self.opened(self.square('CAT A1', 200))
        right = self.rule('correct')
        self.assertEqual(right[:2], [['sound', 'right'], ['model', None]])
        self.assertIn('$200 to Bret, who has $200.', self.says(right))
        self.assertTrue(self.says(right).endswith('Bret, your pick.'))
        self.opened(self.square('CAT B1', 400))
        wrong = self.rule('wrong_answer', 'What is London?')
        self.assertEqual(wrong[:3], [['sound', 'wrong'], ['model', None], ['say', "That's $400 off Bret."]])
        self.assertEqual(wrong[-1][0], 'open')
        self.assertEqual(self.game.context(self.clock())['clue']['answering'], 'Jeff')
        self.opened(wrong)
        missed = self.rule('wrong_answer')
        self.assertIn('The correct response: What is answer B1 400?', self.says(missed))   # One rebound only.
        self.assertTrue(self.says(missed).endswith('Jeff, your pick.'))                    # The pick passes on.
        self.assertEqual(self.scores(), {'Bret': -200, 'Jeff': -400, 'Jeremy': 0})

    def test_the_right_answer_said_plainly_counts_wrong_and_nobody_else_gets_it(self):
        self.start('Bret', 'Jeff')
        self.opened(self.square('CAT A1', 400))
        attempt = self.game.context(self.clock())['clue']['attempt_id']
        credited = self.move('jeopardy_ruling', {'attempt_id': attempt, 'response': True, 'heard': 'answer A1 400',
                                                 'comparison': 'x',
                                                 'question_form': False,
                                                 'verdict': 'correct', 'text': None})
        self.assertEqual(credited[0], ['sound', 'wrong'])    # Called right but plainly said: code applies the rule.
        self.assertEqual(self.scores()['Bret'], -400)
        self.opened(self.square('CAT A1', 600))
        parts = self.rule('right_but_not_a_question', 'answer A1 600')
        self.assertIn("That's $600 off Jeff for not phrasing it as a question.", self.says(parts))   # Jeff's pick now.
        self.assertFalse(any(k == 'open' for k, _ in parts))                     # It was said aloud: no rebound.
        self.assertEqual(self.game.context(self.clock())['phase'], 'selecting')
        self.assertEqual(self.scores(), {'Bret': -400, 'Jeff': -600})

    def test_an_answer_that_sounds_like_the_right_one_is_right(self):
        self.start('Bret', 'Jeff')
        self.opened(self.square('CAT A1', 200))
        attempt = self.game.context(self.clock())['clue']['attempt_id']
        parts = self.move('jeopardy_ruling', {'attempt_id': attempt, 'response': True, 'heard': 'What is a night?',
                                              'comparison': 'x', 'sounds_like_correct': True, 'question_form': True,
                                              'verdict': 'wrong_answer', 'text': None})
        self.assertEqual(parts[0], ['sound', 'right'])
        self.assertEqual(self.scores()['Bret'], 200)

    def test_a_pass_costs_nothing_and_unclear_is_asked_again_twice(self):
        self.start('Bret', 'Jeff')
        self.opened(self.square('CAT A1', 200))
        attempt = self.game.context(self.clock())['clue']['attempt_id']
        aside = self.move('jeopardy_ruling', {'attempt_id': attempt, 'response': False, 'heard': 'This board is rigged',
                                              'comparison': 'not an answer',
                                              'question_form': False, 'verdict': 'wrong_answer', 'text': 'Noted.'})
        self.assertEqual(aside, [['model', None]])      # Not an attempt: nothing changes (paid probe, October 26).
        self.assertEqual(self.game.context(self.clock())['clue']['attempt_id'], attempt)
        self.assertEqual(self.rule('unclear'), [['model', None]])
        self.assertEqual(self.rule('unclear'), [['model', None]])
        third = self.rule('unclear')                    # Then it settles as a pass, never a fine (V2).
        self.assertEqual(third[0], ['sound', 'timesup'])
        self.assertEqual(self.scores(), {'Bret': 0, 'Jeff': 0})
        self.assertEqual(self.game.context(self.clock())['clue']['answering'], 'Jeff')

    def test_a_ruling_for_a_settled_clue_or_on_words_from_before_the_clue_is_refused(self):
        self.start('Bret', 'Jeff')
        self.opened(self.square('CAT A1', 200))
        old = self.game.context(self.clock())['clue']['attempt_id']
        self.opened(self.rule('wrong_answer'))
        with self.assertRaises(ValueError) as stale:
            self.game.plan('jeopardy_ruling', {'attempt_id': old, 'response': True, 'heard': 'x', 'comparison': 'x',
                                               'question_form': True,
                                               'verdict': 'correct', 'text': None})
        self.assertEqual(str(stale.exception), 'ClueAlreadySettled')
        with self.assertRaises(ValueError) as early:
            self.rule('correct', speaker_at=self.clock() - 1)   # Began during Bret's turn: not Jeff's answer.
        self.assertEqual(str(early.exception), 'ResponseBeganBeforeThisTurn')
        self.assertTrue(self.game.context(self.clock(), self.clock() - 1)['clue']['response_began_before_this_turn'])

    def test_a_planned_move_that_never_commits_changes_nothing(self):
        self.start('Bret', 'Jeff')
        before = json.dumps(self.game.snapshot())
        self.game.plan('jeopardy_pick', {'category_id': 'c1', 'value': 200})   # The turn was stopped.
        self.assertEqual(json.dumps(self.game.snapshot()), before)
        with self.assertRaises(ValueError):
            self.game.plan('jeopardy_pick', {'category_id': 'c1', 'value': 300})
        self.game.commit()
        self.assertEqual(json.dumps(self.game.snapshot()), before)

    def test_a_daily_double_is_wagered_within_bounds_and_belongs_to_its_picker(self):
        self.start('Bret', 'Jeff')
        parts = self.square('CAT C1', 800)
        self.assertEqual(parts[0], ['sound', 'daily_double'])
        self.assertIn("It's a Daily Double! Bret, you have $0. Wager anything from $5 to $1,000.", self.says(parts))
        with self.assertRaises(ValueError) as high:
            self.game.plan('jeopardy_wager', {'amount': 5000, 'text': None})
        self.assertEqual(str(high.exception), 'WagerOutOfRange:5-1000')
        opened = self.move('jeopardy_wager', {'amount': 1000, 'text': None})
        self.assertTrue(next(v for k, v in opened if k == 'open').startswith('$1,000 on the line. CAT C1 for $800.'))
        self.opened(opened)
        missed = self.rule('wrong_answer')
        self.assertFalse(any(k == 'open' for k, _ in missed))            # No rebound on a Daily Double.
        self.assertTrue(self.says(missed).endswith('Bret, your pick.'))     # The picker keeps the board.
        self.assertEqual(self.scores()['Bret'], -1000)

    def test_rex_replying_pauses_the_clock_and_a_stop_holds_it_until_the_clue_is_asked_for_again(self):
        self.start('Bret', 'Jeff')
        line = self.opened(self.square('CAT A1', 200))
        self.clock.at += 5
        left = self.game.game.attempt.deadline - self.clock()
        self.game.pause(self.clock())
        self.assertFalse(self.game.window_open(self.clock()))
        self.clock.at += 20                       # "What's the score?" answered meanwhile.
        self.game.resume(self.clock())
        self.assertAlmostEqual(self.game.game.attempt.deadline, self.clock() + left)
        self.game.hold(self.clock())              # "Stop!"
        self.clock.at += 300
        self.assertIsNone(self.game.tick(self.clock(), False))
        self.assertEqual(self.game.context(self.clock())['clue']['clock'], 'stopped (a stop was said)')
        again = self.move('jeopardy_control', {'action': 'repeat_clue', 'text': None})
        reread = next(v for k, v in again if k == 'open')
        self.assertNotEqual(reread, line)
        self.assertTrue(reread.startswith('Bret, CAT A1 for $200.'))
        self.assertTrue(self.game.heard(reread, self.clock()))
        self.assertTrue(self.game.window_open(self.clock()))

    def test_with_the_board_on_screen_the_categories_are_read_once_per_board_and_when_asked(self):
        self.start('Bret', 'Jeff', screen=True)
        line = self.opened(self.square('CAT A1', 200))
        self.assertEqual(line, 'Clue A1 for 200 dollars.')     # The square is on screen: just the clue.
        for value in (400, 600, 800, 1000):
            parts = self.rule('correct')
            self.assertNotIn('Remaining categories', self.says(parts))
            self.opened(self.square('CAT A1', value))
        with self.assertRaises(ValueError) as refused:      # Never while a clue is open (a garbled "Make moves.").
            self.move('jeopardy_control', {'action': 'read_board', 'text': None})
        self.assertEqual(str(refused.exception), 'FinishThisSquareFirst')
        self.rule('correct')
        board = self.move('jeopardy_control', {'action': 'read_board', 'text': None})
        self.assertTrue(self.says(board).startswith('Still on the board: '))
        # Without a screen: after each of the first four squares, then every third (V2's curve).
        game = Jeopardy(self.clock, rng=random.Random(0), bank=self.bank)
        self.game = game
        self.start('Bret')
        reads = []
        for value in (200, 400, 600, 800, 1000):
            self.opened(self.square('CAT A1', value))
            reads.append('Remaining categories' in self.says(self.rule('correct')))
        for value in (200, 400):
            self.opened(self.square('CAT B1', value))
            reads.append('Remaining categories' in self.says(self.rule('correct')))
        self.assertEqual(reads, [True, True, True, True, False, False, True])

    def test_rounds_run_through_double_jeopardy_and_final_jeopardy_to_a_winner(self):
        self.start('Bret', 'Jeff', 'Jeremy')
        self.opened(self.square('CAT A1', 1000))
        self.rule('correct')
        self.opened(self.square('CAT B1', 600))
        self.rule('wrong_answer')                      # Bret down; Jeff gets the rebound.
        self.assertEqual(self.game.context(self.clock())['clue']['answering'], 'Jeff')
        self.game.heard(self.game.game.attempt.line, self.clock())
        self.rule('correct')                           # Jeff 600, Bret 400.
        double = self.move('jeopardy_control', {'action': 'next_round', 'text': None})
        self.assertIn('Double Jeopardy! Values start at $400.', self.says(double))
        self.assertEqual(self.game.context(self.clock())['round'], 'Double Jeopardy')
        final = self.move('jeopardy_control', {'action': 'final_jeopardy', 'text': None})
        # Lowest first; Jeremy at $0 rides along for pride.
        self.assertIn('This is Final Jeopardy. The category: FINAL CAT.', self.says(final))
        self.assertIn("Jeremy, you're at $0: you play for pride.", self.says(final))
        self.assertTrue(self.says(final).endswith("Bret, you have $400. What's your wager?"))
        self.move('jeopardy_wager', {'amount': 400, 'text': None})
        locked = self.move('jeopardy_wager', {'amount': 100, 'text': None})
        self.assertEqual([k for k, _ in locked][-4:], ['say', 'say', 'sound', 'open'])
        self.assertIn(['sound', 'final_theme'], locked)
        self.assertEqual(self.game.context(self.clock())['clue']['answering'], 'Jeremy')
        self.opened(locked)
        nxt = self.rule('correct')
        self.assertEqual(nxt, [['model', None], ['open', 'Bret, your response?']])
        self.opened(nxt)
        self.opened(self.rule('right_but_not_a_question'))
        end = self.rule('correct')
        said = self.says(end)
        self.assertIn('The correct response: What is the final answer?', said)
        self.assertIn('Bret missed it: minus $400.', said)
        self.assertIn('Jeff had it: plus $100.', said)
        self.assertTrue(said.endswith('Final scores: Jeff $700, Bret $0, Jeremy $0. Jeff wins!'))
        self.assertEqual(end[-1], ['sound', 'outro'])
        self.assertFalse(self.game.active)
        self.assertEqual(self.game.snapshot()['phase'], 'over')

    def test_an_appeal_overturns_the_last_ruling_once(self):
        self.start('Bret', 'Jeff')
        self.opened(self.square('CAT A1', 400))
        self.opened(self.rule('wrong_answer', 'What is day cart?'))
        parts = self.move('jeopardy_control', {'action': 'overturn_last', 'text': None})
        self.assertIn('Ruling reversed: Bret is now at $400.', self.says(parts))
        self.assertTrue(self.says(parts).endswith('Bret, your pick.'))   # The rebound is moot; he keeps the board.
        self.assertEqual(self.scores(), {'Bret': 400, 'Jeff': 0})
        with self.assertRaises(ValueError):
            self.game.plan('jeopardy_control', {'action': 'overturn_last', 'text': None})

    def test_a_game_nobody_plays_ends_and_end_game_crowns_the_leader(self):
        self.start('Bret', 'Jeff')
        self.clock.at += IDLE_END_SECONDS + 1
        self.assertEqual(self.game.tick(self.clock(), False), [])
        self.assertFalse(self.game.active)
        self.start('Bret', 'Jeff')
        self.opened(self.square('CAT A1', 200))
        self.rule('correct')
        end = self.move('jeopardy_control', {'action': 'end_game', 'text': None})
        self.assertEqual(end[-2:], [['say', 'Final scores: Bret $200, Jeff $0. Bret wins!'], ['sound', 'outro']])

    def test_the_bank_leaves_out_clues_that_need_media_and_reveals_read_as_questions(self):
        self.assertFalse(valid_clue({'answer': 'This howl comes from (audio clue)', 'question': 'wolf', 'category': 'X'}))
        self.assertFalse(valid_clue({'answer': 'Watch me lift this', 'question': 'lever', 'category': 'X',
                                     'comments': '(Sarah of the Clue Crew demonstrates)'}))
        self.assertTrue(valid_clue({'answer': 'If you listen to CDs you use this', 'question': 'player', 'category': 'X'}))
        self.assertEqual(as_question('Edgar Allan Poe', 'This author wrote "The Raven"'), 'Who is Edgar Allan Poe?')
        self.assertEqual(as_question('Paris', 'This capital city'), 'Where is Paris?')
        self.assertEqual(speak_category('COMBINED STATE ABBREV.'), 'COMBINED STATE ABBREVIATIONS')
        from r3x.jeopardy import spelled_response
        self.assertEqual(spelled_response('(2 of) snow, rain, heat & gloom of night'),
                         'any two of: snow, rain, heat, gloom of night')
        self.assertEqual(spelled_response('(Sir Arthur Conan) Doyle'), '(Sir Arthur Conan) Doyle')


class StateTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.bank = ClueBank(write_bank(Path(self.temp.name)))
        self.state.jeopardy._bank = self.bank
        self.state.jeopardy.rng = random.Random(0)

    def test_only_a_person_starts_a_game_and_each_moment_offers_its_own_moves(self):
        self.begin("Let's play Jeopardy!")
        context = self.state.context()
        names = {d['name'] for d in grounded_tools(context)}
        self.assertIn('jeopardy_setup', names)
        self.assertFalse(names & {'jeopardy_pick', 'jeopardy_ruling', 'jeopardy_wager', 'jeopardy_control'})
        self.assertNotIn('jeopardy_setup', {d['name'] for d in grounded_tools({**context, 'trigger': 'reconsideration'})})
        result = self.call('jeopardy_setup', {'players': ['Bret', 'Casey'], 'text': 'Contestants, to your podiums!'})
        self.assertEqual(result.outcome, 'ok', result)
        self.begin('History for two hundred.')
        context = self.state.context()
        self.assertEqual(context['jeopardy']['phase'], 'selecting')
        offered = {d['name']: d for d in grounded_tools(context)}
        self.assertEqual(set(offered), {'jeopardy_setup', 'jeopardy_pick', 'jeopardy_control', 'remain_silent', 'shut_down'})
        self.assertEqual(offered['jeopardy_pick']['parameters']['properties']['category_id']['enum'],
                         [c['category_id'] for c in context['jeopardy']['board']])
        board = context['jeopardy']['board'][0]
        self.call('jeopardy_pick', {'category_id': board['category_id'], 'value': 200})
        self.begin('What is a whale?')
        context = self.state.context()
        offered = {d['name']: d for d in grounded_tools(context)}
        self.assertIn('jeopardy_ruling', offered)
        self.assertEqual(offered['jeopardy_ruling']['parameters']['properties']['attempt_id']['enum'],
                         [context['jeopardy']['clue']['attempt_id']])
        view = request_view(context)
        self.assertEqual(set(view) - {'people_here'}, {'jeopardy', 'speaker', 'current_utterance', 'source_turn_id',
                                                        'state_revision', 'transcript_trusted'})
        self.assertEqual(self.store.claims(include_candidates=True), [])     # A game move is never a fact.

    def test_known_names_are_matched_for_gaze_only_and_a_refused_move_changes_nothing(self):
        bret = self.introduce('Bret Benziger')
        self.begin('Me and Casey.')
        self.call('jeopardy_setup', {'players': ['Bret', 'Casey'], 'text': None})
        game = self.state.jeopardy.game
        self.assertEqual([(p.name, p.person_id) for p in game.players], [('Bret', bret), ('Casey', None)])
        self.assertEqual(self.store.people()[0]['name'], 'Bret Benziger')    # No person is made for a guest.
        self.assertEqual(len(self.store.people()), 1)
        before = json.dumps(self.state.jeopardy.snapshot())
        refused = self.call('jeopardy_pick', {'category_id': 'c9', 'value': 200})
        self.assertEqual((refused.outcome, refused.reason), ('rejected', 'UnknownCategory'))
        self.assertEqual(json.dumps(self.state.jeopardy.snapshot()), before)
        refused = self.call('jeopardy_pick', {'category_id': 'c1', 'value': '200'})
        self.assertEqual(refused.reason, 'InvalidJeopardyAmount')

    def test_a_spoken_stop_holds_the_clock(self):
        self.begin('Jeopardy with Casey.')
        self.call('jeopardy_setup', {'players': ['Casey'], 'text': None})
        self.begin('First one.')
        board = self.state.context()['jeopardy']['board'][0]
        parts = self.call('jeopardy_pick', {'category_id': board['category_id'], 'value': 200}).data['parts']
        self.state.jeopardy.heard(parts[-1][1], self.state.trace.clock())
        self.state.cancel()
        self.assertTrue(self.state.jeopardy.game.held)
        self.assertFalse(self.state.jeopardy.window_open(self.state.trace.clock()))


class Scripted:
    def __init__(self, *replies):
        self.replies, self.snapshots = list(replies), []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        yield reply(snapshot) if callable(reply) else reply


def pick_first(value=200):
    return lambda s: ToolCall(uuid4().hex, 'jeopardy_pick', {'category_id': s.context['jeopardy']['board'][0]['category_id'],
                                                             'value': value})


def ruling(verdict, text=None, form=True):
    return lambda s: ToolCall(uuid4().hex, 'jeopardy_ruling', {'attempt_id': s.context['jeopardy']['clue']['attempt_id'],
                                                               'response': True, 'heard': 'What is it?', 'comparison': 'x',
                                                               'sounds_like_correct': False, 'question_form': form,
                                                               'verdict': verdict, 'text': text})


class RecordingSink(SyntheticSink):
    """Notes what played and whether the microphone was gated meanwhile."""
    def __init__(self, output=None):
        self.output, self.played = output, []

    def play(self, chunks, cancel, on_start, visualize=True):
        first = next(iter(chunks), None)
        gated = bool(self.output and self.output.echo_risk())
        rest = SyntheticSink.play(self, chunks, cancel, on_start, visualize)
        self.played.append({'visualize': visualize, 'gated': gated, 'bytes': len(first or b'')})
        return rest


class SessionTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.state.jeopardy._bank = ClueBank(write_bank(Path(self.temp.name)))
        self.state.jeopardy.rng = random.Random(0)

    def session(self, brain, tts=None):
        self.sink = RecordingSink()
        output = OutputOwner(tts or SyntheticTTS(), self.sink, self.state.trace)
        self.sink.output = output
        self.output = output
        self.session_ = Session('session', brain, output, self.state.trace, self.state)
        self.addCleanup(self.session_.close)
        return self.session_

    def turn(self, text, trusted=True):
        self.state.trace.at += 5
        now = self.state.trace.at
        return self.session_.process_turn(Capture(uuid4().hex, 'session', bytes(1024), now - 2, now - 1, now - .5),
                                          Transcript(text, -.01 if trusted else -.94, trusted), self.session_.revision)

    def test_a_move_plays_its_sounds_then_his_words_then_the_facts_and_a_pick_goes_straight_to_the_clue_whose_hearing_starts_the_clock(self):
        brain = Scripted(lambda s: ToolCall('c1', 'jeopardy_setup', {'players': ['Bret', 'Casey'], 'text': "Let's play!"}),
                         pick_first(), ruling('correct', 'Nailed it.'))
        self.session(brain)
        first = self.turn("Let's play Jeopardy, me and Casey. I'm Bret.")
        self.assertEqual(first.completed_text[0], "Let's play!")
        sounds = [r['sound'] for r in self.state.trace.rows if r['event'] == 'sound_played']
        self.assertEqual(sounds, ['intro', 'board'])
        self.assertTrue(all(p['gated'] for p in self.sink.played if not p['visualize']))   # Not heard as a person.
        clue = self.turn('Category one for two hundred.')
        name = brain.snapshots[1].context['jeopardy']['board'][0]['category']
        self.assertEqual(clue.completed_text, [f'{name} for $200. Clue {name[4:]} for 200 dollars.'])   # No riff first.
        pick = next(t for t in TOOLS if t['name'] == 'jeopardy_pick')
        self.assertNotIn('text', pick['parameters']['properties'])   # Nothing of his own to say on a pick.
        game = self.state.jeopardy.context(self.state.trace.clock())['clue']
        self.assertTrue(game['clock'].startswith('running'))
        right = self.turn('What is answer A1 200?')
        self.assertEqual(right.completed_text[:2], ['Nailed it.', '$200 to Bret, who has $200.'])
        self.assertEqual([r['sound'] for r in self.state.trace.rows if r['event'] == 'sound_played'][-1], 'right')
        request = brain.snapshots[-1]
        self.assertNotIn('personal_memory', request.context)        # A game turn is a lean request.
        self.assertIn('jeopardy', request.context)
        self.assertEqual(len(brain.snapshots), 3)                     # One model call per move.

    def test_a_clue_cut_off_by_a_stop_starts_no_clock(self):
        session = None
        class StopAtClue(SyntheticTTS):
            def synthesize(inner, text, cancel, mode=None):
                if text.startswith('CAT '):
                    session.cancel()
                yield from SyntheticTTS.synthesize(inner, text, cancel, mode)
        brain = Scripted(lambda s: ToolCall('c1', 'jeopardy_setup', {'players': ['Casey'], 'text': None}), pick_first())
        session = self.session(brain, StopAtClue())
        self.turn('Jeopardy for Casey.')
        cut = self.turn('First square.')
        self.assertEqual(cut.status, 'interrupted')
        game = self.state.jeopardy.context(self.state.trace.clock())['clue']
        self.assertEqual(game['clock'], 'stopped (a stop was said)')
        self.assertFalse(self.state.jeopardy.window_open(self.state.trace.clock()))

    def test_times_up_comes_from_the_game_clock_without_a_model_call(self):
        brain = Scripted(lambda s: ToolCall('c1', 'jeopardy_setup', {'players': ['Bret', 'Casey'], 'text': None}),
                         pick_first())
        self.session(brain)
        self.turn('Jeopardy, Bret and Casey.')
        self.turn('First square.')
        calls = len(brain.snapshots)
        self.state.trace.at += ANSWER_SECONDS + 1
        said = self.session_.game_tick(self.state.trace.clock(), False)
        self.assertEqual(said.completed_text[0], "Time's up.")
        self.assertTrue(said.completed_text[1].startswith("Casey, it's yours."))
        self.assertEqual(len(brain.snapshots), calls)
        self.assertTrue(self.state.jeopardy.window_open(self.state.trace.clock()))   # Casey's clock is running.
        self.assertIn("Time's up.", self.session_.history_messages()[-1]['content'])

    def test_an_unclear_transcript_during_a_clue_is_ruled_on_but_never_costs_money(self):
        brain = Scripted(lambda s: ToolCall('c1', 'jeopardy_setup', {'players': ['Casey'], 'text': None}), pick_first(),
                         ruling('wrong_answer', 'Nope!'))
        self.session(brain)
        self.turn('Jeopardy for Casey.')
        self.turn('First square.')
        self.state.trace.at += 3                 # Spoken after the clue was heard.
        now = self.state.trace.at
        said = self.session_.process_turn(Capture(uuid4().hex, 'session', bytes(1024), now - 1.5, now - 1, now - .5),
                                          Transcript('What is the hollow?', -.9, False), self.session_.revision)
        self.assertEqual(len(brain.snapshots), 3)                          # It reached the ruling.
        self.assertFalse(brain.snapshots[-1].context['transcript_trusted'])
        self.assertEqual(said.completed_text, ["I didn't catch that. Say it again?"])
        self.assertEqual(self.state.jeopardy.context(self.state.trace.clock())['players'][0]['score'], 0)
        self.assertTrue(self.state.jeopardy.window_open(self.state.trace.clock()))   # The clock runs on.
        self.state.jeopardy.game.phase = 'over'
        self.session_.process_turn(Capture(uuid4().hex, 'session', bytes(1024), now, now + .5, now + 1),
                                   Transcript('Oh.', -.9, False), self.session_.revision)
        self.assertEqual(len(brain.snapshots), 3)                          # Outside a game: never answered.

    def test_a_sound_that_is_not_a_voice_never_reaches_a_ruling(self):
        # Owner, October 26: the voice check keeps his motors and coughs out of rulings, while an
        # unclear real answer still reaches one (the test above).
        brain = Scripted(lambda s: ToolCall('c1', 'jeopardy_setup', {'players': ['Casey'], 'text': None}), pick_first(),
                         ruling('correct', 'Yes!'))
        self.session(brain)
        self.turn('Jeopardy for Casey.')
        self.turn('First square.')
        self.state.trace.at += 3
        now = self.state.trace.at
        said = self.session_.process_turn(Capture(uuid4().hex, 'session', bytes(1024), now - 1.5, now - 1, now - .5),
                                          Transcript('Oh.', -.9, False, not_a_voice=True), self.session_.revision)
        self.assertEqual((said.status, len(brain.snapshots)), ('suppressed', 2))   # No ruling, no model call.
        self.assertTrue(any(r['event'] == 'untrusted_not_answered' and r.get('not_a_voice')
                            for r in self.state.trace.rows))
        self.assertEqual(self.state.jeopardy.context(self.state.trace.clock())['players'][0]['score'], 0)
        self.assertTrue(self.state.jeopardy.window_open(self.state.trace.clock()))   # Casey can still answer.
        right = self.turn('What is the hollow?')
        self.assertEqual((len(brain.snapshots), right.completed_text[0]), (3, 'Yes!'))

    def test_silence_on_words_during_an_open_clue_is_asked_about_once_unless_they_were_shaky(self):
        silent = ToolCall('s', 'remain_silent', {'reason': 'background_conversation'})
        thinking = ToolCall('t', 'remain_silent', {'reason': 'unfinished_thought'})
        brain = Scripted(lambda s: ToolCall('c1', 'jeopardy_setup', {'players': ['Casey'], 'text': None}), pick_first(),
                         thinking, silent, silent, ruling('correct', 'Yes!'), silent, silent)
        self.session(brain)
        self.turn('Jeopardy for Casey.')
        self.turn('First square.')
        self.turn('What is...')
        self.assertEqual(len(brain.snapshots), 3)          # Mid-thought: silence at once.
        self.turn('Okay.', trusted=False)
        self.assertEqual(len(brain.snapshots), 4)          # A shaky sound: silence at once (first robot game).
        right = self.turn('Who is Descartes?')
        self.assertEqual(len(brain.snapshots), 6)          # Paid probe, October 26: right answers got silence.
        self.assertEqual(right.completed_text[0], 'Yes!')
        self.turn('Ok.')                                    # Outside an open clue, silence stands at once.
        self.assertEqual(len(brain.snapshots), 7)

    def test_a_game_asked_for_in_a_dropped_reply_starts_from_the_owed_answer(self):
        from r3x.proactive import Opportunity
        silent = ToolCall('s', 'remain_silent', {'reason': 'no_response_needed'})
        setup = lambda s: ToolCall('c1', 'jeopardy_setup', {'players': [], 'text': 'At last.'})
        brain = Scripted(silent, silent, setup)
        session = self.session(brain)
        self.turn("Let's play Jeopardy.")                  # His reply is dropped as someone comes into view.
        plain = Opportunity('goal', session.session_id, self.state.trace.at, None, None, (), session._latest_speech,
                            False, 'reconsideration')
        session.process_opportunity(plain, session.revision, lambda: True)
        self.assertNotIn('jeopardy_setup', {d['name'] for d in grounded_tools(brain.snapshots[-1].context)})
        session._unanswered_turn = 'dropped'
        owed = Opportunity('owed', session.session_id, self.state.trace.at, None, None, (), session._latest_speech,
                           False, 'reconsideration')
        answer = session.process_opportunity(owed, session.revision, lambda: True)
        self.assertIn('jeopardy_setup', {d['name'] for d in grounded_tools(brain.snapshots[-1].context)})
        self.assertTrue(self.state.jeopardy.active)         # So the dashboard shows the game page.
        self.assertTrue(self.state.jeopardy.snapshot()['active'])
        self.assertEqual(answer.completed_text, ['At last.', WHO_IS_PLAYING])

    def test_game_turns_leave_out_the_joke_writer_and_goals(self):
        class Room:
            def __init__(self): self.offers = 0
            def offer(self, *a): self.offers += 1; return None
        brain = Scripted(lambda s: ToolCall('c1', 'jeopardy_setup', {'players': ['Casey'], 'text': None}), pick_first())
        session = self.session(brain)
        session.joke_room = Room()
        self.turn('Jeopardy for Casey.')
        offers = session.joke_room.offers
        self.turn('First square.')
        self.assertEqual(session.joke_room.offers, offers)
        self.assertNotIn('agency_plan', brain.snapshots[-1].context)


class HostedRequestTests(unittest.TestCase):
    def test_the_hosting_rules_ride_last_and_company_rules_are_not_named(self):
        from tests.test_context_diet import send
        game = {'phase': 'selecting', 'board_on_screen': True, 'players': [{'name': 'Bret', 'score': 0}]}
        payload, _ = send({'jeopardy': game, 'source_turn_id': 't'})
        last = payload['input'][-1]['content']
        self.assertTrue(last.startswith('JEOPARDY IS ON.'))
        self.assertIn('The board is on screen: do not read the categories', last)
        self.assertNotIn('in_company', json.dumps(payload['input']))
        self.assertIsNone(directive({}))


class RuntimeTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.state.jeopardy._bank = ClueBank(write_bank(Path(self.temp.name)))
        self.state.jeopardy.rng = random.Random(0)

    def runtime(self):
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace)
        output.bed = MusicBed(SlowSink(), self.state.trace)
        session = Session('session', Scripted('Hi.'), output, self.state.trace, self.state)
        self.addCleanup(session.close)
        runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, self.state.trace, silence=.5)
        runtime.proactive_tick = lambda *a: self.fail('his own initiative ran during a game')
        return runtime

    def open_clue(self):
        game = self.state.jeopardy
        self.begin('Jeopardy for Casey.')
        game.plan('jeopardy_setup', {'players': ['Casey'], 'text': None}); game.commit()
        line = game.plan('jeopardy_pick', {'category_id': 'c1', 'value': 200})[-1][1]
        game.commit()
        game.heard(line, self.state.trace.clock())

    def test_a_game_runs_its_clock_and_music_instead_of_his_goals_and_lengthens_the_pause_in_a_response(self):
        runtime = self.runtime()
        self.open_clue()
        self.assertTrue(runtime.gaming())
        runtime.game_tick()
        self.assertEqual(runtime.endpoint.silence, ANSWER_PAUSE_SECONDS)
        self.assertFalse(runtime.endpoint.early)   # Smart Turn never ends "What is..." at a short pause.
        bed = runtime.session.output.bed
        self.assertTrue(bed.playing)
        runtime.voice_over_music()                 # Someone starts answering.
        self.assertFalse(bed.playing)
        runtime.game_tick()
        self.assertFalse(bed.playing)              # It stays off for this clue.
        bed.stop()
        self.state.jeopardy.game.phase = 'over'
        self.assertFalse(runtime.gaming())


    def test_the_think_music_plays_through_a_turn_he_lets_pass_and_picks_up_after_his_line(self):
        runtime = self.runtime()
        self.open_clue()
        game, bed = self.state.jeopardy, runtime.session.output.bed
        runtime.game_tick()
        self.assertTrue(bed.playing)
        game.pause(self.state.trace.clock())      # A stray "Okay." is being weighed: the clock waits for him,
        runtime.speech_in_flight = lambda: True
        runtime.game_tick()
        self.assertTrue(bed.playing)              # and the music plays on (first robot game, October 26).
        for _ in range(1000):
            if bed.position >= 64 * MusicBed.PIECE:
                break
            threading.Event().wait(.002)
        bed.stop()                                # The output owner stops it for his line.
        where = bed.position
        runtime.speech_in_flight = lambda: False
        game.resume(self.state.trace.clock())
        runtime.game_tick()
        self.assertTrue(bed.playing)
        self.assertGreaterEqual(bed.position, where)   # Picked up where it was, not from the top.
        played = [r for r in self.state.trace.rows if r['event'] == 'jeopardy_music']
        self.assertEqual([(r['status'], r.get('resumed')) for r in played], [('playing', False), ('playing', True)])
        bed.stop()


class SlowSink:
    """Plays until cancelled, like a looping bed on the real sink."""
    def play(self, chunks, cancel, on_start, visualize=True):
        for _ in chunks:
            if cancel.event.wait(.001):
                raise Cancelled()
        return Playback(0, None, 0., 0)

    def cancel(self):
        pass


class OutputTests(unittest.TestCase):
    def test_a_sound_gates_the_microphone_is_not_heard_text_and_the_music_gives_way_to_speech(self):
        from tests.test_voice_core import Trace
        trace = Trace()
        sink = RecordingSink()
        output = OutputOwner(SyntheticTTS(), sink, trace)
        sink.output = output
        output.bed = MusicBed(SlowSink(), trace)
        self.assertTrue(output.bed.start(b'\0\0' * 4096))
        from queue import Queue
        phrases, delivery, cancel = Queue(), Delivery(), Cancellation()
        for item in (Sound('right', b'\1\0' * 2000), 'Nailed it.', None):
            put_phrase(phrases, item, cancel)
        output.run(phrases, cancel, 't', delivery)
        self.assertEqual(delivery.completed_text, ['Nailed it.'])
        self.assertFalse(output.bed.playing)
        self.assertEqual([(p['visualize'], p['gated']) for p in sink.played], [(False, True), (True, True)])
        replay = Delivery()
        TextOutput(trace).run(_queue(Sound('right', b'\0\0'), 'Hi.'), Cancellation(), 't', replay)
        self.assertEqual(replay.completed_text, ['Hi.'])     # Replay hears no sounds as words.


def _queue(*items):
    from queue import Queue
    q = Queue()
    for item in (*items, None):
        q.put(item)
    return q


if __name__ == '__main__':
    unittest.main()
