"""What Rex keeps saying, remembered across days, so the familiar stops being news.

Owner, October 31: "Each time I turn him on he greets me and comments on the telescope in the room.
There is no sense of novelty or familiarity with the robot. It's always the same program each time",
and "we almost need a recency bias as well". From October 21 to 29 the telescope was in about 50 of his
heard lines, opened 8 of 35 runs and came up 9 times in one. Every guard against repeating himself (the
planner's mentioned_lately, spent joke shapes, recent goals) forgot at a restart.

Code counts the words of Rex's own heard lines per day and fades them with a half-life: what he keeps
saying is worn out for days and comes back once it has rested, and the more he said it the longer it
rests (owner default, October 31: off limits at first, fair game after about a week). Only his own output
counts, never a person's words, as mentioned_lately did within one run; this replaces it. Code rather
than the model names what a line was about because 21 of those 52 telescope lines were plain prose that
went through no reply tool. A line said to a private audience stays theirs: offered while they are here,
deleted with them.

How often he has seen the view in front of him is the other half, kept by the room evidence owner
(places.py), which already turns camera frames into fingerprints.

Visits (owner, same day: "Someone rex sees 20 minutes apart vs 20 days apart has no current avenue of
expression"): the state owner keeps when each recognized person's visit began and when they were last
in evidence, across runs, from the picture of the present. A greeting is offered how long it has been
and how often they come by, in words; the model says it its own way. Before visits were kept, the
conversation record (exchanges with that person as the audience) stands in for earlier visits.
"""
from datetime import datetime
import re

from .field_guide import GUIDE

SCHEMA = """CREATE TABLE IF NOT EXISTS talked_about(
    word TEXT NOT NULL, audience TEXT NOT NULL, person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
    day TEXT NOT NULL, lines INTEGER NOT NULL, last_at REAL NOT NULL, said TEXT NOT NULL,
    PRIMARY KEY(word, audience, day));
CREATE INDEX IF NOT EXISTS talked_about_last ON talked_about(last_at);
CREATE TABLE IF NOT EXISTS visits(
    id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    began_at REAL NOT NULL, last_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS visits_person ON visits(person_id, last_at);"""

DAY = 86400.
HALF_LIFE_DAYS = 2.5   # Each line's weight halves every two and a half days.
WORN_OUT = 1.5         # Two lines today, or nine a week ago: off limits.
USED_LATELY = .4       # Two lines about six days ago: only with something new to say.
MIN_LINES = 2          # One mention is not a habit.
KEPT_DAYS = 30
LISTED = 12            # Per list, heaviest first.
GENERAL = 'general'

# Words too common in anyone's speech to name a thing he keeps bringing up. Four letters and up;
# shorter words are never counted.
STOP_WORDS = frozenset('''
about above actually after again against ahead almost alone along already also although always among
another answer anybody anyone anything anyway anywhere apparently around asked asking aside away back
basically because become been before behind being believe below beside best better between beyond
both bring brings brought call calling came cannot can't cant certainly change check clearly come
comes coming could couldn't course current does doesn't doing done don't down during each easy either
else enough even ever every everybody everyone everything exactly except fair feel feels fine first
found from full gave get gets getting give given gives going gone good gonna got gotta great guess
guys had hadn't half happen happened hard has hasn't have haven't having hear heard hearing here
here's hers herself hey himself hmm hold honestly hope how however i'll i'm i've idea instead into
isn't it'll it's itself just keep keeps kind knew know known last later least leave left less let's
like likely listen listening little long look looked looking looks lot lots made make makes making
many maybe mean means meant might mind mine more most mostly much must myself name near need needed
needs never next nice nobody none nothing notice noticed okay once only onto open other others ours
ourselves over own part perhaps pick place plenty point pretty probably put quite rather ready real
really remember right said same saw say saying says seem seemed seems seen self sense several shall
she's should shouldn't show since some somebody someone something sometimes somewhere soon sorry sort
sound sounds start started still stop stuff such sure take taken takes talk talking tell than thank
thanks that that'll that's their theirs them themselves then there there's these they they'll
they're they've thing things think this those though thought through thus till time times today
together told tonight took toward towards tried true truly trying turn turned under unless until upon
used using usually very wait want wanted wants was wasn't watch way ways we'll we're we've well went
were weren't what what's whatever when where where's whether which while whole who's whom whose why
will wish with within without won't wonder wondering word words work would wouldn't yeah year years
yes yet you'd you'll you're you've your yours yourself yourselves
'''.split())

_WORD = re.compile(r"[a-z][a-z'’-]*[a-z]")


def _key(word):
    """One count for telescope and telescopes (plain syntax, no meaning inferred)."""
    if len(word) >= 5 and word.endswith('s') and not word.endswith(('ss', 'us', 'is')):
        return word[:-1]
    return word


def words(text, keep=()):
    """Counted words of one line, {key: the form said}. Names of the people and pets here (keep) are
    never spent by being said."""
    found = {}
    for word in _WORD.findall((text or '').lower().replace('’', "'")):
        word = word.removesuffix("'s")
        if len(word) < 4 or word in STOP_WORDS or word in keep:
            continue
        found.setdefault(_key(word), word)
    return found


def day_of(wall):
    return datetime.fromtimestamp(wall).date().isoformat()


def record(conn, text, person_id, wall_now, keep=()):
    """One heard line of Rex's: each counted word once, in this audience, on this local day."""
    audience = 'person:' + person_id if person_id else GENERAL
    day = day_of(wall_now)
    for key, said in words(text, keep).items():
        conn.execute('INSERT INTO talked_about VALUES(?,?,?,?,1,?,?) ON CONFLICT(word, audience, day) DO UPDATE '
                     'SET lines=lines+1, last_at=excluded.last_at, said=excluded.said',
                     (key, audience, person_id, day, wall_now, said))
    conn.execute('DELETE FROM talked_about WHERE last_at<?', (wall_now - KEPT_DAYS * DAY,))


def weights(store, here, wall_now):
    """{key: (faded weight, lines, the form said)}: lines said to anyone, and to the people here."""
    here = sorted({p for p in here if p})
    marks = ','.join('?' * len(here))
    rows = store.rows('SELECT word, lines, last_at, said FROM talked_about WHERE last_at>=? AND (audience=?'
                      + (f' OR person_id IN ({marks})' if here else '') + ') ORDER BY last_at',
                      (wall_now - KEPT_DAYS * DAY, GENERAL, *here))
    out = {}
    for row in rows:
        age = max(0., wall_now - row['last_at']) / DAY
        weight, lines, _ = out.get(row['word'], (0., 0, None))
        out[row['word']] = (weight + row['lines'] * .5 ** (age / HALF_LIFE_DAYS), lines + row['lines'], row['said'])
    return out


def context(store, here, wall_now):
    """Worn out and used lately, heaviest first, or None when he has worn nothing out."""
    ranked = sorted(((w, said) for w, lines, said in weights(store, here, wall_now).values()
                     if lines >= MIN_LINES and w >= USED_LATELY), key=lambda r: -r[0])
    worn = [said for w, said in ranked if w >= WORN_OUT][:LISTED]
    used = [said for w, said in ranked if w < WORN_OUT][:LISTED]
    if not worn and not used:
        return None
    return {**({'worn_out': worn} if worn else {}), **({'used_lately': used} if used else {})}


VISIT_NOTE_SECONDS = 30.   # How often visits are written while someone is around.
VISITS_KEPT_DAYS = 365
REGULAR_DAYS = 14

# How long they were gone, in the words a greeting needs (the gap itself comes in words too).
ABSENCES = ((20 * 60, 'barely gone: they were here minutes ago'),
            (6 * 3600, 'back the same day'),
            (36 * 3600, 'about a day'),
            (6 * DAY, 'a few days'),
            (14 * DAY, 'a while: a week or more'),
            (float('inf'), 'a long time: two weeks or more'))   # Owner's example: 20 days is "a long time".


def note_visits(conn, rows, wall_now):
    """rows: (visit_id, person_id, began_at, last_at) in wall-clock seconds."""
    for row in rows:
        conn.execute('INSERT INTO visits VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET last_at=excluded.last_at', row)
    conn.execute('DELETE FROM visits WHERE last_at<?', (wall_now - VISITS_KEPT_DAYS * DAY,))


def last_visit(store, person_id, began_at, wall_now):
    """When Rex last saw this person before the visit that began at began_at, and how often they come
    by, or None with no record of an earlier visit."""
    from .conversation_memory import gap
    before = began_at - 1
    since = wall_now - REGULAR_DAYS * DAY
    seen = [r['last_at'] for r in store.rows('SELECT last_at FROM visits WHERE person_id=? AND began_at<? '
                                             'ORDER BY last_at DESC LIMIT 1', (person_id, before))]
    seen += [r['at'] for r in store.rows('SELECT MAX(created_at) AS at FROM conversation_exchanges WHERE person_id=? '
                                         'AND created_at<?', (person_id, before)) if r['at'] is not None]
    if not seen:
        return None
    last = min(max(seen), before)
    days = {day_of(r['began_at']) for r in store.rows('SELECT began_at FROM visits WHERE person_id=? AND began_at<? '
                                                      'AND last_at>=?', (person_id, before, since))}
    days |= {day_of(r['created_at']) for r in store.rows('SELECT created_at FROM conversation_exchanges WHERE person_id=? '
                                                         'AND created_at<? AND created_at>=?', (person_id, before, since))}
    return {'last_here': gap(wall_now - last),
            'absence': next(words for limit, words in ABSENCES if wall_now - last < limit),
            'days_here_lately': f'{len(days)} of the last {REGULAR_DAYS} days'}


def familiar(talked, view):
    """The picture of the present's familiar part, or None."""
    part = {**({'talked_about': talked} if talked else {}), **({'view': view} if view else {})}
    return {**part, 'meaning': GUIDE['right_now.familiar']} if part else None
