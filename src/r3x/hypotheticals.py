""""Do you ever think...": one imaginative question after a long quiet stretch.

Owner request, October 23 (roadmap entry of September 18): when the room has gone quiet, Rex
sparks it with a short thought experiment ("what would happen if everyone won the lottery on
the same day?"). Owner rulings: the stretch is 45 seconds with nobody talking, Rex included
(his silent moves and glances do not count); one question per stretch, and the next only after
someone has spoken.

It is a beat of his current goal, inserted ahead of the rest (agency.insert_beat), so the one
conversation owner writes the question in the beat's single generation: no idea generator,
no extra model call. Only questions actually heard are recorded, so the next one differs.
A hypothetical is make-believe: never a fact about anyone, never an event.
"""
from uuid import uuid4

SCHEMA = """CREATE TABLE IF NOT EXISTS hypotheticals_asked(
    id TEXT PRIMARY KEY, person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
    text TEXT NOT NULL, asked_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS hypotheticals_asked_at ON hypotheticals_asked(asked_at);"""

QUIET_SECONDS = 45.      # Owner, October 23: nobody talking, Rex included, this long.
OPEN_SECONDS = 180.      # A reply this soon after it may take it up.
ASKED_BEFORE = 12        # Recent heard questions offered as spent.

INSTRUCTION = ('The room has gone quiet for a while. Spark it with ONE short, imaginative "what if" for the people '
    'here, the way a friend says "Do you ever think about...": a fun hypothetical about the world, people or droids, '
    'or one of their interests given a twist when you know one. One or two sentences ending in the question, and stop '
    'at the question mark: no punchline, example or answer of your own after it, and no second question. Vary how it '
    'opens. Light, '
    'never grim: nothing about death, illness, disasters, politics or their private lives, and nothing in '
    'hypotheticals.asked_before. Use remain_silent only when they are clearly busy, upset or talking to each other.')


def record(conn, person_id, text, now):
    """A question he was heard asking; person_id when it was asked in someone's private audience."""
    conn.execute('INSERT INTO hypotheticals_asked VALUES(?,?,?,?)', (uuid4().hex, person_id, text[:400], now))


def asked_before(store, person_id=None):
    """What he already asked: everything general, plus what he asked this person."""
    rows = store.rows('SELECT text FROM hypotheticals_asked WHERE person_id IS NULL OR person_id=? '
                      'ORDER BY asked_at DESC LIMIT ?', (person_id, ASKED_BEFORE))
    return [r['text'] for r in rows]
