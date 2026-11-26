"""Messages passed on (owner, November 25): "Rex, when Jeremy gets here, tell him dinner's in the fridge."

The reply model calls `relay_message` and the state owner files the message on the recipient's record (their
stable id, so any of their names works) with who left it and the turn it came from. Whenever the recipient is
recognized here (a face, a supported voice, or their arrival), the waiting message is in the reply context and
the model passes it on in its own words with `deliver_message`; it counts as delivered only once that line was
heard in full. Code keeps the record and the receipt; it never reads what was said.

A message is the teller's words for someone, never a fact about anyone. Forgetting the recipient or the person
who left it removes it (people rows cascade).
"""
from uuid import uuid4

from .field_guide import GUIDE
from .present import ago

SCHEMA = """CREATE TABLE IF NOT EXISTS relay_messages(
    id TEXT PRIMARY KEY, recipient_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    left_by TEXT REFERENCES people(id) ON DELETE CASCADE, message TEXT NOT NULL,
    source_turn TEXT NOT NULL, source_text TEXT NOT NULL, created_at REAL NOT NULL,
    status TEXT NOT NULL, settled_at REAL, delivered_line TEXT);
CREATE INDEX IF NOT EXISTS relay_messages_for ON relay_messages(recipient_id, status);"""

ACTIONS = ('leave', 'cancel')
MESSAGE_MAX = 400            # Characters kept of one message.
WAITING_MAX = 6              # Messages one person may have waiting for them.
SHOWN_LEFT = 4               # The speaker's own latest messages shown, so they can ask or take one back.


def leave(conn, recipient_id, message, *, left_by, turn_id, source_text, now) -> dict:
    message = ' '.join(str(message or '').split())[:MESSAGE_MAX]
    if not message:
        raise ValueError('MessageNeedsWords')
    waiting = conn.execute("SELECT COUNT(*) FROM relay_messages WHERE recipient_id=? AND status='waiting'",
                           (recipient_id,)).fetchone()[0]
    if waiting >= WAITING_MAX:
        raise ValueError('TooManyMessagesWaitingForThem')
    message_id = 'msg_' + uuid4().hex[:12]
    conn.execute('INSERT INTO relay_messages VALUES(?,?,?,?,?,?,?,?,?,?)',
                 (message_id, recipient_id, left_by, message, turn_id, source_text, now, 'waiting', None, None))
    return {'message_id': message_id, 'status': 'waiting', 'meaning': 'Saved. It waits until they are here; '
            'then you pass it on yourself. Nothing has been passed on yet.'}


def cancel(conn, message_id, *, by) -> dict:
    row = conn.execute('SELECT status, left_by FROM relay_messages WHERE id=?', (message_id,)).fetchone()
    if row is None:
        raise ValueError('NoSuchMessage')
    if row[0] != 'waiting':
        raise ValueError('MessageAlreadyPassedOn' if row[0] == 'delivered' else 'MessageAlreadyCancelled')
    if row[1] is not None and row[1] != by:
        raise ValueError('OnlyWhoeverLeftItCanTakeItBack')
    conn.execute("UPDATE relay_messages SET status='cancelled' WHERE id=?", (message_id,))
    return {'message_id': message_id, 'status': 'cancelled'}


def waiting_for(conn, person_ids) -> list[str]:
    """Ids of messages waiting for any of these people."""
    ids = sorted(p for p in person_ids if p)
    if not ids:
        return []
    marks = ','.join('?' * len(ids))
    return [r[0] for r in conn.execute(
        f"SELECT id FROM relay_messages WHERE status='waiting' AND recipient_id IN ({marks})", ids)]


def context(conn, here, speaker, names, now):
    """The reply context's messages: those waiting for someone here, and the speaker's own latest."""
    def entry(row):
        out = {'message_id': row[0], 'for': names(row[1]), 'from': names(row[2]) if row[2] else 'someone you did not recognize',
               'message': row[3], 'left': ago(now - row[4])}
        if row[5] != 'waiting':
            out['status'] = {'delivered': 'passed on', 'cancelled': 'taken back'}[row[5]]
            if row[6]:
                out['settled'] = ago(now - row[6])
        return out
    columns = 'id, recipient_id, left_by, message, created_at, status, settled_at'
    ids = sorted(p for p in here if p)
    found = []
    if ids:
        marks = ','.join('?' * len(ids))
        found = [entry(r) for r in conn.execute(
            f"SELECT {columns} FROM relay_messages WHERE status='waiting' AND recipient_id IN ({marks}) "
            "ORDER BY created_at", ids)]
    left = []
    if speaker:
        left = [entry(r) for r in conn.execute(
            f'SELECT {columns} FROM relay_messages WHERE left_by=? ORDER BY created_at DESC LIMIT ?',
            (speaker, SHOWN_LEFT))]
    if not found and not left:
        return None
    out = {'meaning': GUIDE['messages']}
    if found:
        out['for_someone_here'] = found
    if left:
        out['left_by_speaker'] = left
    return out


def delivered(conn, message_id, line, now) -> bool:
    written = conn.execute("UPDATE relay_messages SET status='delivered', settled_at=?, delivered_line=? "
                           "WHERE id=? AND status='waiting'", (now, line, message_id))
    return bool(written.rowcount)
