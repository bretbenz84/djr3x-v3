"""Jeopardy, V2's verbal game hosted by Rex (owner request, October 26).

Ported from V2's features/jeopardy.py and the game half of features/games.py (provenance in
V2_REFERENCE.md). Code keeps the game: the board, whose turn it is, the answer clock, wagers,
scores and a record of every ruling so one can be overturned. Rex's reply model does all the
understanding through the game tools: who is playing, which square was picked, a wager, and
whether a spoken response is right. V2's word matchers are not ported: they credited "Paris or
London" for Paris and could not hear "day cart" as Descartes (V2 review, September 7). Owner
rulings kept from V2: anyone's voice answers for the player whose turn it is (voice ID never
decides a ruling), one rebound to the next player, Daily Doubles and Final Jeopardy. New on
October 26: a response must be phrased as a question; a right answer said plainly counts as
wrong. With the board on screen (the dashboard), the categories are read once per board and
again only when asked.

Nothing here loads at import; the clue bank is read when a game starts.
"""
from __future__ import annotations

import copy
import csv
import random
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / 'assets'
CLUES = ASSETS / 'jeopardy' / 'clues.tsv'
SOUNDS = ASSETS / 'audio' / 'jeopardy'

ANSWER_SECONDS = 12.          # V2's answer clock, from the moment the clue has been heard.
SPEECH_GRACE_SECONDS = 10.    # Speech under way at the buzzer may run this long past it (V2 ceiling).
AFTER_TURN_SECONDS = 2.       # Left on the clock after Rex answered something that was not a response.
ANSWER_PAUSE_SECONDS = 1.     # End-of-speech pause while a clue is open: "What is... Paris?" (V2: 1.1 s).
ECHO_TAIL_SECONDS = .3        # The microphone opens this long after Rex's line (output echo tail).
IDLE_END_SECONDS = 600.       # Nobody heard this long: the game ends by itself.
MAX_PLAYERS = 4
MAX_REBOUNDS = 1              # V2 owner tuning, August 26: one second chance, not lap-the-table.
UNCLEAR_RETRIES = 2           # "Say that again" without a penalty, then it counts as a pass.
DD_MIN_WAGER = 5
SCOREBOARD_EVERY = 4          # Every Nth ruling reads the whole scoreboard (V2: single totals otherwise).
REMINDER_FULL_READS = 4       # Without a screen: the categories after each of the first picks,
REMINDER_EVERY = 3            # then every third (V2's fatigue curve). With a screen: only when asked.
JUMP_OFFER_AFTER = 6          # Squares played before "next round" is mentioned once (V2).
JUMP_OFFER_REMAINING = 15
VERDICTS = ('correct', 'wrong_answer', 'right_but_not_a_question', 'pass', 'unclear')
ACTIONS = ('repeat_clue', 'read_board', 'next_round', 'final_jeopardy', 'skip_clue', 'overturn_last', 'end_game')
# Sound -> (file, gain, longest seconds). V2 played the music beds at 0.22 and stings at 0.75 and cut the
# 12.6 s Daily Double sting to 6 s; the outro is cut so the room is not held silent for 15 s.
SOUND_FILES = {
    'intro': ('jeopardy-intro.wav', .6, None),
    'board': ('jeopardy-board-sms.wav', .6, None),
    'daily_double': ('jeopardy-daily-double.wav', .6, 6.),
    'right': ('jeopardy-rightanswer.wav', .75, None),
    'wrong': ('jeopardy-incorrect-answer.wav', .75, None),
    'timesup': ('jeopardy-timesup.wav', .75, None),
    'theme': ('jeopardy-theme.wav', .22, None),
    'final_theme': ('jeopardy-final-jeopardy-thinking-music.wav', .45, None),
    'outro': ('jeopardy-outro-no-talking.wav', .5, 8.),
}
RATE = 22050


# ── The clue bank (V2's loader) ─────────────────────────────────────────────────────────────────

def _to_int(value, default=0):
    try:
        return int(str(value or '').replace('$', '').replace(',', '').strip())
    except ValueError:
        return default


def clean_category(category: str) -> str:
    # The dataset escapes decorative quotes (`\\"POT"POURRI`); V2 field 2026-08-26 read them aloud.
    cleaned = ' '.join(re.sub(r'[\\"“”]', '', category or '').split())
    return cleaned or 'Potpourri'


def clean_cell(text: str) -> str:
    return ' '.join((text or '').replace('\\"', '"').replace('\\', '').split())


# Clues that need something Rex cannot show or play (V2's markers; the September 7 review found Clue
# Crew demonstrations passing them, so those are left out too).
_MEDIA = ('(audio', '(video', '(image', '(photo', '(shown', '(seen', 'seen here', 'shown here', 'pictured here',
          'clue crew')


def valid_clue(row: dict) -> bool:
    clue, response, category = ((row.get(k) or '').strip() for k in ('answer', 'question', 'category'))
    if not clue or not response or not category or len(clue) < 8 or len(response) < 2:
        return False
    combined = f"{row.get('comments') or ''} {clue}".lower()
    return not any(marker in combined for marker in _MEDIA)


@dataclass
class Clue:
    category: str
    value: int
    clue: str
    response: str
    daily_double: bool = False
    air_date: str = 'unknown'
    wagered: int | None = None      # A Daily Double's wager, once made.


class ClueBank:
    """V2's TSV of real clues, grouped into boards of one air date and round."""

    def __init__(self, path: Path = CLUES):
        from collections import defaultdict
        grouped = defaultdict(lambda: defaultdict(list))
        self.finals: list[Clue] = []
        with Path(path).open('r', encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                round_no = _to_int(row.get('round'))
                if not valid_clue(row):
                    continue
                clue = Clue(clean_category(row.get('category') or ''), _to_int(row.get('clue_value')),
                            clean_cell(row.get('answer') or ''), clean_cell(row.get('question') or ''),
                            _to_int(row.get('daily_double_value')) > 0, (row.get('air_date') or 'unknown').strip())
                if round_no == 3:
                    self.finals.append(clue)
                elif round_no in (1, 2) and clue.value > 0:
                    grouped[(clue.air_date, round_no)][clue.category].append(clue)
        self.boards: dict[int, list[list[tuple[str, list[Clue]]]]] = {1: [], 2: []}
        for (_, round_no), categories in grouped.items():
            playable = []
            for name, clues in categories.items():
                by_value = {}
                for clue in clues:
                    by_value.setdefault(clue.value, clue)
                if len(by_value) >= 5:
                    playable.append((name, [by_value[v] for v in sorted(by_value)[:5]]))
            if len(playable) >= 6:
                self.boards[round_no].append(playable)
        # Double Jeopardy boards at doubled values when the dataset has them (V2).
        doubled = [b for b in self.boards[2] if min(c.value for _, clues in b for c in clues) >= 400]
        if doubled:
            self.boards[2] = doubled

    def board(self, round_no: int, rng) -> list[dict]:
        boards = self.boards.get(round_no) or []
        if not boards:
            raise ValueError('NoBoardAvailable')
        chosen = rng.sample(rng.choice(boards), 6)
        categories = [{'id': f'c{i + 1}', 'name': name, 'clues': {c.value: copy.copy(c) for c in clues}}
                      for i, (name, clues) in enumerate(chosen)]
        if not any(c.daily_double for cat in categories for c in cat['clues'].values()):
            # No Daily Double came with these categories: one of the higher squares gets it (V2).
            cat = rng.choice(categories)
            values = sorted(cat['clues'])
            cat['clues'][rng.choice(values[len(values) // 2:])].daily_double = True
        return categories

    def final(self, rng) -> Clue:
        if not self.finals:
            raise ValueError('NoFinalClue')
        return copy.copy(rng.choice(self.finals))


# ── Speaking the bank (V2's formatting of dataset text, never of anything a person said) ───────────

_CATEGORY_EXPANSIONS = {'abbrev': 'abbreviations', 'abbrevs': 'abbreviations', 'anniv': 'anniversary',
                        'categ': 'categories', 'dept': 'department', 'depts': 'departments', 'geog': 'geography',
                        'govt': 'government', 'intl': 'international', 'misc': 'miscellaneous',
                        'natl': 'national', 'vocab': 'vocabulary'}


def speak_category(name: str) -> str:
    """The category as Rex says it: dataset abbreviations spelled out ("ABBREV." was read "abreev")."""
    out = []
    for token in re.sub(r'["“”]', '', name or '').replace('_', ' ').split():
        expansion = _CATEGORY_EXPANSIONS.get(token.rstrip('.').lower())
        out.append(token if expansion is None else expansion.upper() if token.isupper() else expansion)
    return ' '.join(out).rstrip('.') or name or ''


def money(amount: int) -> str:
    return f'negative ${abs(int(amount)):,}' if amount < 0 else f'${int(amount):,}'


def names(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1] if items else ''


_THING = re.compile(r'\b(school|college|university|company|corporation|brand|team|movie|film|book|novel|play|song|'
                    r'album|magazine|newspaper|vehicle|ship|invention|device|tool|machine|gadget|appliance|'
                    r'equipment|instrument|object|item|thing|product|technology|material|substance|chemical|'
                    r'food|drink|dish|type|kind)\b', re.I)
_PLACE = re.compile(r'\b(where|country|city|state|capital|nation|island|continent|province|territory|county|'
                    r'region|located|home\s+to)\b', re.I)
_PERSON = re.compile(r'\b(who|author|wrote|writer|novelist|poet|actor|actress|singer|composer|president|king|'
                     r'queen|emperor|inventor|artist|person|man|woman|he|she|him|her|his|hers|born|died)\b', re.I)
_WEAK_THING = re.compile(r'\b(political\s+part(?:y|ies)|holiday|observance|festival|ceremony)\b', re.I)


def as_question(response: str, clue: str = '', category: str = '') -> str:
    """The correct response read back as a question ("Who is Edgar Allan Poe?"), from V2's reveal."""
    subject = re.sub(r'\s*&\s*', ' and ', clean_cell(response).strip(' .!?')) or 'unknown'
    plain = ' '.join(re.sub(r'[^a-z0-9\s]', ' ', subject.lower()).split())
    words = plain.split()
    plural = bool(re.search(r'\s(?:and)\s', subject, re.I) or plain.startswith(('these ', 'those '))
                  or (words and words[-1].endswith('s') and not words[-1].endswith(('ss', 'us', 'is'))))
    bare = re.sub(r'^(?:the|a|an)\s+', '', subject, flags=re.I)
    person = subject[:1].isupper() and (len(re.findall(r"\b[A-Z][a-zA-Z'.-]+\b", bare)) >= 2
                                        or bool(re.search(r'\b(?:Jr|Sr|II|III|IV)\b', bare)))
    context = f'{category} {clue}'
    kind = ('What' if _THING.search(context) else 'Where' if _PLACE.search(context) else 'Who' if person else
            'What' if _WEAK_THING.search(context) else 'Who' if _PERSON.search(context) else 'What')
    return f"{kind} {'are' if plural else 'is'} {subject}?"


# ── The game ─────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Player:
    name: str
    person_id: str | None = None
    score: int = 0


@dataclass
class Attempt:
    attempt_id: str
    player: int
    kind: str                       # clue, rebound, daily_double or final
    line: str                       # What Rex says to open it; heard, the clock starts.
    opened_at: float | None = None
    deadline: float | None = None
    paused_left: float | None = None
    unclear: int = 0
    reread: bool = False            # The unheard opening line was already said again once.
    created_at: float = 0.          # When the turn passed to this player: words begun earlier were not theirs.


@dataclass
class Game:
    phase: str = 'players'          # players, selecting, wager, answering, final_wager, final_answer, over
    round: int = 1                  # 1, 2, or 3 for Final Jeopardy
    players: list = field(default_factory=list)
    picker: int = 0
    board: list = field(default_factory=list)
    board_size: int = 0
    clue: Clue | None = None
    clue_picker: int = 0
    tried: list = field(default_factory=list)       # Players who already had a go at the open clue.
    attempt: Attempt | None = None
    attempts: int = 0
    wager: dict | None = None       # {'player', 'min', 'max'} while a wager is asked for.
    final: dict | None = None       # {'clue', 'order', 'wagers', 'answers', 'queue'}
    last_category: str | None = None
    played: int = 0
    rulings: int = 0
    reminder_reads: int = 0
    jump_offered: bool = False
    held: bool = False              # A spoken stop paused the clock until someone carries on.
    journal: list = field(default_factory=list)   # Each ruling: who, verdict, the score change, the state after.
    last_heard_at: float = 0.


class Jeopardy:
    """The state owner's game (one at a time). Transitions are planned on a copy (`plan`) and take effect
    only once the tool call commits (`commit`), so a stopped turn never changes a score nobody heard.
    Each transition returns what to deliver, in order: ['sound', name], ['say', words], ['model', None]
    (where Rex's own words go) and ['open', words] (the line whose hearing starts the answer clock)."""

    def __init__(self, clock, *, screen=False, rng=None, bank=None):
        self.clock, self.screen = clock, screen
        self.rng = rng or random.Random()
        self._bank = bank
        self.game: Game | None = None
        self._planned = None
        self._sounds = {}
        self._lock = threading.Lock()   # The dashboard reads snapshots from its own thread.

    # Reads ----------------------------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.game is not None and self.game.phase != 'over'

    def bank(self) -> ClueBank:
        if self._bank is None:
            self._bank = ClueBank()
        return self._bank

    def window_open(self, now: float) -> bool:
        """The answer clock is running (think music, the longer end-of-speech pause)."""
        a = self.game.attempt if self.active else None
        return bool(a and a.deadline is not None and not self.game.held and self.game.phase == 'answering')

    def attempt_id(self) -> str | None:
        return self.game.attempt.attempt_id if self.active and self.game.attempt else None

    def answering(self) -> bool:
        return self.active and self.game.phase in {'answering', 'final_answer'}

    def player_up(self) -> Player | None:
        g = self.game
        if not self.active or not g.players:
            return None
        if g.attempt is not None:
            return g.players[g.attempt.player]
        if g.wager is not None:
            return g.players[g.wager['player']]
        return g.players[g.picker]

    def context(self, now: float, speech_started_at: float | None = None) -> dict:
        """The game as the reply model sees it."""
        g = self.game
        view = {'phase': g.phase, 'round': {1: 'Jeopardy', 2: 'Double Jeopardy', 3: 'Final Jeopardy'}[g.round],
                'board_on_screen': self.screen,
                'players': [{'name': p.name, 'score': p.score} for p in g.players]}
        up = self.player_up()
        if up is not None:
            view['turn_of'] = up.name
        if g.board and g.phase in {'selecting', 'answering', 'wager'}:
            view['board'] = [{'category_id': c['id'], 'category': speak_category(c['name']),
                              'values_left': sorted(c['clues'])} for c in g.board]
            if g.last_category:
                view['last_category'] = speak_category(g.last_category)
        clue = g.final['clue'] if g.phase in {'final_wager', 'final_answer'} else g.clue
        if g.phase == 'final_wager':
            view['final_category'] = speak_category(clue.category)
        if g.attempt is not None and clue is not None:
            a = g.attempt
            clock = ('stopped (a stop was said)' if g.held else
                     'not started: the clue has not been heard yet' if a.opened_at is None else
                     'paused while you reply' if a.deadline is None else
                     f'running, {max(0, round(a.deadline - now))} s left')
            view['clue'] = {'attempt_id': a.attempt_id, 'answering': g.players[a.player].name,
                            'category': speak_category(clue.category), 'clue': clue.clue,
                            'correct_response': spelled_response(clue.response), 'value': self._stake(),
                            'aired': clue.air_date, 'daily_double': a.kind == 'daily_double',
                            'rebound': a.kind == 'rebound', 'final': a.kind == 'final', 'clock': clock}
            if speech_started_at is not None and speech_started_at < a.created_at:
                view['clue']['response_began_before_this_turn'] = True
        if g.wager is not None:
            view['wager'] = {'player': g.players[g.wager['player']].name, 'min': g.wager['min'], 'max': g.wager['max']}
        if g.journal:
            last = g.journal[-1]
            view['last_ruling'] = {k: last[k] for k in ('player', 'heard', 'verdict', 'change', 'overturnable')}
        return view

    def snapshot(self) -> dict:
        """The board for the dashboard (copied under the lock; the GUI thread reads it)."""
        with self._lock:
            g = self.game
            if g is None:
                return {}
            now = self.clock()
            up = self.player_up()
            clue = g.final['clue'] if g.round == 3 and g.final else g.clue
            a = g.attempt
            values = sorted({v for c in g.board for v in c.get('values', ())})
            return {'phase': g.phase, 'round': g.round, 'active': self.active,
                    'players': [{'name': p.name, 'score': p.score, 'up': p is up} for p in g.players],
                    'categories': [{'name': c['name'], 'remaining_values': sorted(c['clues'])} for c in g.board],
                    'values': values,
                    'clue': ({'category': clue.category, 'value': 0 if g.round == 3 else self._stake(), 'clue': clue.clue,
                              'daily_double': bool(a and a.kind == 'daily_double'), 'final': g.round == 3}
                             if clue is not None and g.phase in {'answering', 'final_answer'} else None),
                    'final_category': g.final['clue'].category if g.final and g.phase == 'final_wager' else None,
                    'clock_left': (max(0., a.deadline - now) if a and a.deadline is not None and not g.held else None),
                    'clock_seconds': ANSWER_SECONDS,
                    'wager': ({'player': g.players[g.wager['player']].name, **{k: g.wager[k] for k in ('min', 'max')}}
                              if g.wager else None),
                    'last_ruling': ({k: g.journal[-1][k] for k in ('player', 'heard', 'verdict', 'change')}
                                    if g.journal else None),
                    'reveal': g.final.get('reveal') if g.final else None}

    def gaze_person(self) -> str | None:
        """Whose face Rex looks toward: the player up, when they are a known person."""
        up = self.player_up()
        return up.person_id if up else None

    def sound(self, name: str) -> bytes | None:
        """A sound's PCM (22,050 Hz mono, gain and length applied), or None when its file is missing."""
        if name not in self._sounds:
            self._sounds[name] = load_sound(name)
        return self._sounds[name]

    # Planned transitions (state owner: plan in _apply, commit in _after) --------------------------

    def plan(self, name: str, args: dict, *, speaker_at: float | None = None, people=None, trusted=True) -> list:
        self._planned = None
        self.bank()   # Read once; the trial shares it.
        trial = copy.copy(self)
        trial.game = copy.deepcopy(self.game)
        trial.rng = copy.deepcopy(self.rng)
        trial._planned = None
        handler = {'jeopardy_setup': trial._setup, 'jeopardy_pick': trial._pick, 'jeopardy_wager': trial._wager,
                   'jeopardy_ruling': trial._rule, 'jeopardy_control': trial._control}[name]
        parts = (handler(args, people or {}) if name == 'jeopardy_setup' else
                 handler(args, speaker_at, trusted) if name == 'jeopardy_ruling' else handler(args))
        self._planned = (trial.game, trial.rng)
        return parts

    def commit(self) -> None:
        if self._planned is None:
            return
        with self._lock:
            self.game, self.rng = self._planned
            self._planned = None

    # Clock and delivery (session and runtime) -----------------------------------------------------

    def heard(self, line: str, at: float) -> bool:
        """The opening line was heard to its end: the answer clock starts."""
        a = self.game.attempt if self.active else None
        if a is None or a.line != line:
            return False
        with self._lock:
            a.opened_at = at + ECHO_TAIL_SECONDS
            a.deadline = a.opened_at + ANSWER_SECONDS
            a.paused_left = None
            self.game.held = False
        return True

    def pause(self, now: float) -> None:
        """Rex is handling a turn: the clock waits for him."""
        a = self.game.attempt if self.active else None
        if a is not None and a.deadline is not None:
            with self._lock:
                a.paused_left, a.deadline = max(0., a.deadline - now), None

    def resume(self, now: float) -> None:
        a = self.game.attempt if self.active else None
        if a is not None and a.paused_left is not None and not self.game.held:
            with self._lock:
                a.deadline, a.paused_left = now + max(a.paused_left, AFTER_TURN_SECONDS), None

    def hold(self, now: float) -> None:
        """A spoken stop: the clock stops until someone carries on (a ruling, a repeat, a pick)."""
        if self.active:
            self.pause(now)
            self.game.held = True

    def heard_people(self, at: float) -> None:
        if self.active:
            self.game.last_heard_at = max(self.game.last_heard_at, at)

    def tick(self, now: float, speech_in_flight: bool) -> list | None:
        """Time's up, a cut-off clue said again, or a game nobody is playing any more."""
        g = self.game
        if not self.active:
            return None
        if g.last_heard_at and now - g.last_heard_at > IDLE_END_SECONDS:
            with self._lock:
                g.phase = 'over'
            return []
        a = g.attempt
        if a is None or g.held:
            return None
        if a.opened_at is None and not a.reread and a.paused_left is None and not speech_in_flight:
            a.reread = True
            return [['open', a.line]]
        if a.deadline is None or now < a.deadline:
            return None
        if speech_in_flight and now < a.deadline + SPEECH_GRACE_SECONDS:
            return None   # Someone is answering at the buzzer: their words are judged first.
        with self._lock:
            return self._times_up()

    # Transitions ----------------------------------------------------------------------------------

    def _new_attempt(self, player: int, kind: str, line: str) -> Attempt:
        self.game.attempts += 1
        self.game.held = False
        return Attempt(f'a{self.game.attempts}', player, kind, line, created_at=self.clock())

    def _stake(self) -> int:
        g = self.game
        if g.attempt and g.attempt.kind == 'final':
            return g.final['wagers'].get(g.attempt.player, 0)
        if g.clue is None:
            return 0
        return g.clue.value if g.clue.wagered is None else g.clue.wagered

    def _clue_line(self, clue: Clue, *, full: bool = False) -> str:
        """The clue as read: with no screen to see it, the square first (V2)."""
        text = clue.clue.rstrip(' .')
        if not (full or not self.screen):
            return f'{text}.'
        lead = (f'{speak_category(clue.category)} for ${clue.value:,}. ' if clue.value > 0 else
                f'{speak_category(clue.category)}: ')
        return f'{lead}{text}.'

    def _categories_line(self) -> str:
        return 'The categories: ' + '. '.join(speak_category(c['name']) for c in self.game.board) + '.'

    def _board_readout(self) -> str:
        rows = [(speak_category(c['name']), sorted(c['clues'])) for c in self.game.board if c['clues']]
        if not rows:
            return 'The board is picked clean.'
        if len({tuple(v) for _, v in rows}) == 1:
            return ('Still on the board: ' + '. '.join(n for n, _ in rows) + '. Each has '
                    + ', '.join(f'${v:,}' for v in rows[0][1]) + '.')
        return 'Still on the board: ' + '. '.join(f"{n} for {', '.join(f'${v:,}' for v in vs)}" for n, vs in rows) + '.'

    def _deal(self, round_no: int) -> list:
        g = self.game
        g.board = self.bank().board(round_no, self.rng)
        for category in g.board:
            category['values'] = sorted(category['clues'])
        g.round, g.phase = round_no, 'selecting'
        g.board_size = sum(len(c['clues']) for c in g.board)
        g.played = g.reminder_reads = 0
        g.jump_offered = False
        g.last_category, g.clue, g.attempt, g.wager = None, None, None, None
        low = min(min(c['clues']) for c in g.board)
        lead = (f'Double Jeopardy! Values start at ${low:,}. ' if round_no == 2 else '')
        return [['sound', 'board'], ['say', lead + self._categories_line()],
                ['say', f'{g.players[g.picker].name}, pick a category and a value.']]

    def _first_board(self) -> list:
        deal = self._deal(1)
        return deal[:-1] + [['say', 'Remember: phrase your response as a question.']] + deal[-1:]

    def _setup(self, args, people) -> list:
        """people: {name as said: person_id or None}, resolved by the state owner."""
        wanted = [' '.join(str(n).split()) for n in args.get('players') or [] if str(n).strip()]
        if len(wanted) > MAX_PLAYERS:
            raise ValueError('AtMostFourPlayers')
        roster, seen = [], set()
        for name in wanted:
            person = people.get(name)
            key = person or name.casefold()
            if key in seen:
                continue   # The same person said twice ("Jeremy, JT"): one seat (V2 review, item 7).
            seen.add(key)
            roster.append(Player(display_name(name), person))
        if self.game is None or self.game.phase == 'over':
            self.game = Game(last_heard_at=self.clock())
            parts = [['sound', 'intro'], ['model', None]]
            if not roster:
                return parts
            self.game.players = roster
            return parts + self._first_board()
        g = self.game
        if not roster:
            raise ValueError('NoPlayersNamed')
        if g.phase == 'players':
            g.players = roster
            return [['model', None]] + self._first_board()
        if g.phase not in {'selecting'}:
            raise ValueError('ChangePlayersBetweenSquares')
        old = {(p.person_id or p.name.casefold()): p for p in g.players}
        up = g.players[g.picker] if g.players else None
        g.players = [old.get(p.person_id or p.name.casefold(), p) for p in roster]
        g.picker = g.players.index(up) if up in g.players else 0
        return [['model', None], ['say', self._scores_line()],
                ['say', f'{g.players[g.picker].name}, your pick.']]

    def _pick(self, args) -> list:
        g = self._require('selecting')
        category = next((c for c in g.board if c['id'] == args['category_id']), None)
        if category is None:
            raise ValueError('UnknownCategory')
        value = int(args['value'])
        if value not in category['clues']:
            raise ValueError('SquareGone' if value in category.get('values', ()) else 'NoSuchValue')
        clue = category['clues'].pop(value)
        g.clue, g.clue_picker, g.tried = clue, g.picker, []
        g.last_category = category['name']
        g.played += 1
        if clue.daily_double:
            score = g.players[g.picker].score
            top = max(score, 1000 * g.round)   # Show rules: up to your score, or the round's top value.
            g.wager, g.phase = {'player': g.picker, 'min': DD_MIN_WAGER, 'max': top}, 'wager'
            name = g.players[g.picker].name
            return [['sound', 'daily_double'],
                    ['say', f"It's a Daily Double! {name}, you have {money(score)}. Wager anything from "
                            f"${DD_MIN_WAGER} to ${top:,}."]]
        g.phase = 'answering'
        line = self._clue_line(clue)
        g.attempt = self._new_attempt(g.picker, 'clue', line)
        return [['open', line]]   # Straight to the clue: no words of his own about the category (owner, October 26).

    def _wager(self, args) -> list:
        g = self.game
        if g is None or g.wager is None or g.phase not in {'wager', 'final_wager'}:
            raise ValueError('NoWagerAskedFor')
        amount = int(args['amount'])
        if not g.wager['min'] <= amount <= g.wager['max']:
            raise ValueError(f"WagerOutOfRange:{g.wager['min']}-{g.wager['max']}")
        player = g.wager['player']
        if g.phase == 'wager':
            g.clue.wagered = amount
            g.wager = None
            g.phase = 'answering'
            line = f'${amount:,} on the line. ' + self._clue_line(g.clue)
            g.attempt = self._new_attempt(player, 'daily_double', line)
            return [['model', None], ['open', line]]
        g.final['wagers'][player] = amount
        g.final['queue'].pop(0)
        return [['model', None]] + self._next_final_wager()

    def _rule(self, args, speaker_at, trusted=True) -> list:
        g = self.game
        if g is None or g.attempt is None or g.phase not in {'answering', 'final_answer'}:
            raise ValueError('NoClueOpen')
        a = g.attempt
        if args['attempt_id'] != a.attempt_id:
            raise ValueError('ClueAlreadySettled')
        if speaker_at is not None and speaker_at < a.created_at:
            raise ValueError('ResponseBeganBeforeThisTurn')
        verdict = args['verdict']
        if verdict not in VERDICTS:
            raise ValueError('UnknownVerdict')
        if args.get('response') is False:
            # Not an attempt at the clue (paid probe, October 26: "this board is rigged" was ruled wrong): his
            # words are said and nothing in the game changes; the clock runs on.
            return [['model', None]]
        if verdict in {'wrong_answer', 'unclear'} and args.get('sounds_like_correct') is True:
            # Owner rule from V2: an answer that sounds like the right one is right, whatever the recognizer wrote.
            # Paid probes, October 26: "night" for knight and "symbols" for cymbals were ruled wrong in about half the
            # runs although the instructions named those very cases; asked on its own, the sound decides.
            verdict = 'correct'
        if verdict == 'correct' and args.get('question_form') is False:
            verdict = 'right_but_not_a_question'   # The rule is code's: the right answer said plainly counts wrong.
        # Words the recognizer was unsure of never cost money: they are asked for again, in code's words,
        # since his own were written for the ruling he chose.
        doubted = not trusted and verdict in {'wrong_answer', 'right_but_not_a_question', 'pass'}
        if doubted:
            verdict = 'unclear'
        g.held = False
        if verdict == 'unclear':
            a.unclear += 1
            if a.unclear <= UNCLEAR_RETRIES:
                return [['say', "I didn't catch that. Say it again?"]] if doubted else [['model', None]]
            verdict = 'pass'   # Repeated undecodable audio settles as a pass, never a fine (V2).
        if a.kind == 'final':
            return self._final_answer(a, verdict, args.get('heard') or '')
        player = g.players[a.player]
        stake = self._stake()
        change = stake if verdict == 'correct' else 0 if verdict == 'pass' else -stake
        before = [p.score for p in g.players]
        player.score += change
        g.rulings += 1
        g.journal.append({'player': player.name, 'player_index': a.player, 'heard': args.get('heard') or '',
                          'verdict': verdict, 'change': change, 'stake': stake, 'scores_before': before,
                          'clue_picker': g.clue_picker, 'picker_before': g.picker, 'overturnable': verdict != 'pass'})
        g.tried.append(a.player)
        if verdict == 'correct':
            g.picker = a.player
            return [['sound', 'right'], ['model', None], ['say', self._score_line(player, change)]] + self._after_clue()
        sound = 'timesup' if verdict == 'pass' else 'wrong'
        parts = [['sound', sound], ['model', None]]
        if change:
            parts.append(['say', f'That\'s {money(-change)} off {player.name}'
                                 + (' for not phrasing it as a question.' if verdict == 'right_but_not_a_question' else '.')])
        # The right answer was said aloud: nobody else gets that one. A Daily Double belongs to its picker.
        if verdict != 'right_but_not_a_question' and a.kind != 'daily_double':
            rebound = self._rebound()
            if rebound:
                return parts + rebound
        return parts + self._missed()

    def _rebound(self) -> list | None:
        g = self.game
        if len(g.tried) > MAX_REBOUNDS:
            return None
        for offset in range(1, len(g.players)):
            nxt = (g.clue_picker + offset) % len(g.players)
            if nxt not in g.tried:
                line = f"{g.players[nxt].name}, it's yours." + ('' if self.screen else ' ' + self._clue_line(g.clue))
                g.attempt = self._new_attempt(nxt, 'rebound', line)
                return [['open', line]]
        return None

    def _missed(self) -> list:
        """Nobody got it: the response revealed, and the pick passes on from whoever chose the square."""
        g = self.game
        reveal = 'The correct response: ' + as_question(g.clue.response, g.clue.clue, g.clue.category)
        # A Daily Double's picker keeps the board (show rules); otherwise the pick passes on.
        daily = g.attempt is not None and g.attempt.kind == 'daily_double'
        g.picker = g.clue_picker if daily else (g.clue_picker + 1) % len(g.players)
        return [['say', reveal]] + self._after_clue()

    def _after_clue(self) -> list:
        g = self.game
        g.clue, g.attempt, g.tried = None, None, []
        if not any(c['clues'] for c in g.board):
            return self._round_over()
        g.phase = 'selecting'
        parts = []
        if g.rulings and g.rulings % SCOREBOARD_EVERY == 0:
            parts.append(['say', self._scores_line()])
        if not self.screen:
            g.reminder_reads += 1
            if g.reminder_reads <= REMINDER_FULL_READS or (g.reminder_reads - REMINDER_FULL_READS) % REMINDER_EVERY == 0:
                parts.append(['say', 'Remaining categories: ' + '. '.join(
                    speak_category(c['name']) for c in g.board if c['clues']) + '.'])
        remaining = sum(len(c['clues']) for c in g.board)
        if not g.jump_offered and g.round < 3 and (g.played >= JUMP_OFFER_AFTER or remaining <= JUMP_OFFER_REMAINING):
            g.jump_offered = True
            parts.append(['say', f"Say 'next round' any time and I'll deal "
                                 f"{'Double Jeopardy' if g.round == 1 else 'Final Jeopardy'}."])
        return parts + [['say', f'{g.players[g.picker].name}, your pick.']]

    def _round_over(self) -> list:
        g = self.game
        if g.round == 1:
            g.picker = min(range(len(g.players)), key=lambda i: g.players[i].score)   # Trailing player first.
            return [['say', "That's the round. " + self._scores_line()]] + self._deal(2)
        return self._begin_final("That's the board. ")

    def _begin_final(self, lead: str) -> list:
        g = self.game
        g.clue, g.attempt, g.wager, g.tried = None, None, None, []
        if not any(p.score > 0 for p in g.players):
            return [['say', lead + 'Nobody has money for Final Jeopardy.']] + self._game_over()
        clue = self.bank().final(self.rng)
        order = sorted(range(len(g.players)), key=lambda i: g.players[i].score)   # Lowest score first (V2).
        g.round, g.phase = 3, 'final_wager'
        g.final = {'clue': clue, 'order': order, 'wagers': {}, 'answers': {}, 'queue': list(order)}
        return [['sound', 'board'], ['say', f'{lead}This is Final Jeopardy. The category: {speak_category(clue.category)}.']] \
            + self._next_final_wager()

    def _next_final_wager(self) -> list:
        g = self.game
        queue, parts = g.final['queue'], []
        while queue:
            idx = queue[0]
            player = g.players[idx]
            if player.score <= 0:
                g.final['wagers'][idx] = 0
                parts.append(['say', f"{player.name}, you're at {money(player.score)}: you play for pride."])
                queue.pop(0)
                continue
            g.wager = {'player': idx, 'min': 0, 'max': player.score}
            return parts + [['say', f"{player.name}, you have {money(player.score)}. What's your wager?"]]
        g.wager = None
        g.final['queue'] = list(g.final['order'])
        g.phase = 'final_answer'
        clue = g.final['clue']
        first = g.players[g.final['queue'][0]].name
        line = f"Time's up. {first}, what is your response?"
        g.attempt = self._new_attempt(g.final['queue'][0], 'final', line)
        return parts + [['say', f'Wagers are locked. Here is the clue: {clue.clue.rstrip(" .")}.'],
                        ['say', 'Thirty seconds. Good luck.'], ['sound', 'final_theme'], ['open', line]]

    def _final_answer(self, attempt: Attempt, verdict: str, heard: str) -> list:
        g = self.game
        g.final['answers'][attempt.player] = {'verdict': verdict, 'heard': heard}
        g.final['queue'].pop(0)
        if g.final['queue']:
            nxt = g.final['queue'][0]
            line = f'{g.players[nxt].name}, your response?'
            g.attempt = self._new_attempt(nxt, 'final', line)
            return [['model', None], ['open', line]]
        return [['model', None]] + self._final_reveal()

    def _final_reveal(self) -> list:
        g = self.game
        clue = g.final['clue']
        lines = ['Time to settle up. The correct response: ' + as_question(clue.response, clue.clue, clue.category)]
        reveal = []
        for idx in g.final['order']:
            player, wager = g.players[idx], g.final['wagers'].get(idx, 0)
            answer = g.final['answers'].get(idx, {'verdict': 'pass', 'heard': ''})
            right = answer['verdict'] == 'correct'
            player.score += wager if right else -wager
            reveal.append({'player': player.name, 'heard': answer['heard'], 'right': right, 'wager': wager})
            lines.append(f"{player.name} {'had it' if right else 'missed it'}: "
                         f"{'plus' if right else 'minus'} ${wager:,}." if wager else
                         f"{player.name} {'had it, for pride' if right else 'missed it, but bet nothing'}.")
        g.final['reveal'] = reveal
        g.attempt = None
        return [['say', ' '.join(lines)]] + self._game_over()

    def _game_over(self) -> list:
        g = self.game
        if not g.players:
            g.phase = 'over'   # Ended before anyone joined: nothing to score.
            return []
        top = max(p.score for p in g.players)
        winners = [p.name for p in g.players if p.score == top]
        g.phase, g.attempt, g.wager = 'over', None, None
        crown = f'{winners[0]} wins!' if len(winners) == 1 else f"It's a tie between {names(winners)}."
        return [['say', f'Final scores: {self._scores_line(lead=False)} {crown}'], ['sound', 'outro']]

    def _times_up(self) -> list:
        g = self.game
        a = g.attempt
        player = g.players[a.player]
        if a.kind == 'final':
            g.final['answers'][a.player] = {'verdict': 'pass', 'heard': ''}
            g.final['queue'].pop(0)
            if g.final['queue']:
                nxt = g.final['queue'][0]
                line = f'{g.players[nxt].name}, your response?'
                g.attempt = self._new_attempt(nxt, 'final', line)
                return [['sound', 'timesup'], ['open', line]]
            return [['sound', 'timesup']] + self._final_reveal()
        g.journal.append({'player': player.name, 'player_index': a.player, 'heard': '', 'verdict': 'time_up',
                          'change': 0, 'stake': self._stake(), 'scores_before': [p.score for p in g.players],
                          'clue_picker': g.clue_picker, 'picker_before': g.picker, 'overturnable': False})
        g.tried.append(a.player)
        parts = [['sound', 'timesup'], ['say', "Time's up."]]
        if a.kind != 'daily_double':
            rebound = self._rebound()
            if rebound:
                return parts + rebound
        return parts + self._missed()

    def _control(self, args) -> list:
        g = self.game
        if g is None or g.phase == 'over':
            raise ValueError('NoGame')
        action = args['action']
        if action == 'repeat_clue':
            if g.attempt is None:
                raise ValueError('NoClueOpen')
            a = g.attempt
            name = g.players[a.player].name
            a.line = (self._clue_line(g.final['clue'], full=True) + f' {name}, your response?' if a.kind == 'final'
                      else f'{name}, ' + self._clue_line(g.clue, full=True))
            a.opened_at = a.deadline = a.paused_left = None
            a.reread = True
            g.held = False
            return [['model', None], ['open', a.line]]
        if action == 'read_board':
            if not g.board or g.round == 3:
                raise ValueError('NoBoard')
            return [['model', None], ['say', self._board_readout()]] + (
                [['say', f'{g.players[g.picker].name}, your pick.']] if g.phase == 'selecting' else [])
        if action in {'next_round', 'final_jeopardy'}:
            if g.phase not in {'selecting', 'players'} or not g.players:
                raise ValueError('FinishThisSquareFirst')
            if action == 'next_round' and g.round == 1:
                return [['model', None], ['say', self._scores_line()]] + self._deal(2)
            return [['model', None]] + self._begin_final('')
        if action == 'skip_clue':
            if g.clue is None or g.phase not in {'answering', 'wager'}:
                raise ValueError('NoClueOpen')
            g.picker, g.wager = g.clue_picker, None
            reveal = 'That one is off the board. It was: ' + as_question(g.clue.response, g.clue.clue, g.clue.category)
            g.clue, g.attempt = None, None
            return [['model', None], ['say', reveal]] + self._after_clue()
        if action == 'overturn_last':
            return self._overturn()
        if action == 'end_game':
            return [['model', None]] + self._game_over()
        raise ValueError('UnknownAction')

    def _overturn(self) -> list:
        """An appeal of the last ruling (V2 review, item 10): right becomes wrong or wrong right."""
        g = self.game
        last = g.journal[-1] if g.journal else None
        if last is None or not last['overturnable'] or g.round == 3:
            raise ValueError('NothingToOverturn')
        player = g.players[last['player_index']]
        stake = last['stake']
        now_right = last['verdict'] != 'correct'
        change = stake if now_right else -stake
        player.score += change - last['change']
        last.update(verdict='correct' if now_right else 'wrong_answer', change=change, overturnable=False)
        parts = [['model', None], ['say', f'Ruling reversed: {player.name} is now at {money(player.score)}.']]
        if now_right and (g.phase == 'answering' and g.attempt is not None and g.attempt.kind == 'rebound'):
            # The rebound is moot: the first answer was right after all.
            g.picker = last['player_index']
            return parts + self._after_clue()
        if now_right and g.phase == 'selecting':
            g.picker = last['player_index']   # A right answer keeps control of the board.
            return parts + [['say', f'{player.name}, your pick.']]
        return parts

    # Helpers --------------------------------------------------------------------------------------

    def _require(self, phase: str) -> Game:
        if self.game is None or self.game.phase != phase:
            raise ValueError('NotNow:' + ('no game' if self.game is None else self.game.phase))
        return self.game

    def _score_line(self, player: Player, change: int) -> str:
        if self.game.rulings % SCOREBOARD_EVERY == 0:
            return f'${change:,} to {player.name}.'   # The full scoreboard follows.
        return f'${change:,} to {player.name}, who has {money(player.score)}.'

    def _scores_line(self, lead: bool = True) -> str:
        ranked = sorted(self.game.players, key=lambda p: -p.score)
        return ('Scores: ' if lead else '') + ', '.join(f'{p.name} {money(p.score)}' for p in ranked) + '.'


_CHOICES = re.compile(r'^\((\d) of\)\s*(.+)$', re.I)
_COUNTS = {'1': 'any one', '2': 'any two', '3': 'any three', '4': 'any four'}


def spelled_response(response: str) -> str:
    """The bank's "(2 of) snow, rain & heat" spelled out for the judge ("any two of: snow, rain, heat"). Three paid
    probe runs, October 26: told the format, the reply model still credited a single item."""
    match = _CHOICES.match(response or '')
    if not match:
        return response
    items = [i.strip() for i in re.split(r',|&|\band\b', match.group(2)) if i.strip()]
    return f"{_COUNTS.get(match.group(1), match.group(1))} of: {', '.join(items)}"


def display_name(name: str) -> str:
    """A first name for the board ("T Joy" keeps its initial; V2)."""
    words = re.sub(r"[^\w'\-. ]", ' ', name).split()
    if not words:
        return name.strip()
    shown = ' '.join(words[:2]) if len(words) > 1 and len(words[0].rstrip('.')) == 1 else words[0]
    return shown[:1].upper() + shown[1:]


def load_sound(name: str) -> bytes | None:
    import wave
    from array import array
    filename, gain, longest = SOUND_FILES[name]
    path = SOUNDS / filename
    if not path.exists():
        return None
    with wave.open(str(path), 'rb') as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != RATE:
            return None
        samples = array('h', clip.readframes(clip.getnframes()))
    if longest is not None and len(samples) > longest * RATE:
        samples = samples[:int(longest * RATE)]
        fade = int(RATE * .8)
        for i in range(fade):   # Cut short, it fades out rather than clicking off.
            samples[len(samples) - fade + i] = int(samples[len(samples) - fade + i] * (1 - i / fade))
    for i in range(len(samples)):
        samples[i] = int(samples[i] * gain)
    return samples.tobytes()


def request_view(context: dict) -> dict:
    """What a reply request shows while a game is on: the game and the turn, nothing of his goals,
    memories or jokes (and so a much smaller request)."""
    keep = ('jeopardy', 'speaker', 'current_utterance', 'source_turn_id', 'state_revision', 'transcript_trusted',
            'voice_mode', 'trigger')
    shown = {k: context[k] for k in keep if k in context}
    here = (context.get('right_now') or {}).get('people_here')
    if here:
        shown['people_here'] = here
    return shown


DIRECTIVE = (
    'JEOPARDY IS ON. You are hosting it; jeopardy holds the board, players and scores, and code keeps them, runs '
    'the clock and reads every clue aloud. Keep each line of yours short: a few words of game-show energy, your '
    'humor in a word or two, never a speech. Whatever anyone in the room says counts for the player whose turn it '
    'is: never ask who said it. While a clue is open never say, spell or hint at the correct response. Words that '
    'are not a move (people talking to each other, to a pet, about something else) get remain_silent; the clock '
    'keeps running. A question about the board, the score or whose turn it is gets a few words from jeopardy. '
    'They must phrase responses as a question; code already told them, so you do not remind them before they answer. '
    'Every attempt at a response to an open clue, right or wrong, gets jeopardy_ruling.')


def directive(context: dict) -> str | None:
    game = context.get('jeopardy')
    if not game:
        return None
    extra = ''
    if game.get('phase') == 'final_answer':
        extra = ' FINAL JEOPARDY: rule each response, but your text never says whether it was right; the reveal comes at the end.'
    elif game.get('board_on_screen') and game.get('phase') == 'selecting':
        extra = ' The board is on screen: do not read the categories or repeat the square they picked.'
    return DIRECTIVE + extra


def offered(game: dict) -> set[str]:
    """The game tools for this moment of the game."""
    phase = game.get('phase')
    names = {'jeopardy_control', 'remain_silent', 'shut_down'}
    if phase in {'players', 'selecting'}:
        names |= {'jeopardy_setup'}
    if phase == 'selecting':
        names |= {'jeopardy_pick'}
    if phase in {'wager', 'final_wager'}:
        names |= {'jeopardy_wager'}
    if phase in {'answering', 'final_answer'} and game.get('clue'):
        names |= {'jeopardy_ruling'}
    return names


def ground(definition: dict, game: dict) -> None:
    """Snapshot identifiers for the offered game tools (re-checked when applied)."""
    props = definition['parameters']['properties']
    if definition['name'] == 'jeopardy_pick':
        props['category_id']['enum'] = [c['category_id'] for c in game.get('board', ()) if c['values_left']]
    if definition['name'] == 'jeopardy_ruling' and game.get('clue'):
        props['attempt_id']['enum'] = [game['clue']['attempt_id']]
