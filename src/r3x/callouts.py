"""Callouts: Rex's standing celebrity bit about one person or saved pet, added by voice.

Owner, October 25: V2's celebrity callouts (Jeff Benziger of History Hunters fame, T'Joy the
world's greatest hair stylist, PJ the major Kings fan, JT the volleyball celebrity) "made people in
the room feel special and the jokes were funny". V2 keyed them to name spellings and picked from
fixed lines that soon repeated. Here anyone tells Rex ("PJ is a huge Kings fan, treat him like
Kings royalty"), the reply model calls `callout`, and the state owner files it on that person's
record (their stable id, so any of their names works) or a saved pet's. The reply model writes the
words every time: an entrance when they arrive, and now and then a callback while they are here.

Owner rulings the same day: anyone can add one; asked to stop, he switches it off (kept, so it can
come back); asked never to call them that again, he deletes it; a callout's angles may bend his
usual joke limits, and its off-limits always hold.

Code keeps only the record and the pacing; it never reads what a person said. Which heard lines
used a callout is judged from Rex's own output. A callout is his material,
never a fact about anyone (V2 stored "JT, major volleyball celebrity" as JT's real interest).
Forgetting a person removes callouts about them and the ones they added; forgetting a pet removes
its own (CoreState.apply_photo).
"""
import json
import re
from uuid import uuid4

from .field_guide import GUIDE
from .memory import name_key, name_words
from .present import ago

SCHEMA = """CREATE TABLE IF NOT EXISTS callouts(
    id TEXT PRIMARY KEY, person_id TEXT UNIQUE REFERENCES people(id) ON DELETE CASCADE, pet_id TEXT UNIQUE,
    famous_for TEXT NOT NULL, angles TEXT NOT NULL, off_limits TEXT NOT NULL, status TEXT NOT NULL,
    added_by TEXT REFERENCES people(id) ON DELETE CASCADE, source_turn TEXT NOT NULL, source_text TEXT NOT NULL,
    created_at REAL NOT NULL, updated_at REAL NOT NULL, CHECK((person_id IS NULL) <> (pet_id IS NULL)));
CREATE TABLE IF NOT EXISTS callout_lines(
    callout_id TEXT NOT NULL REFERENCES callouts(id) ON DELETE CASCADE,
    kind TEXT NOT NULL, line TEXT NOT NULL, said_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS callout_lines_at ON callout_lines(callout_id, said_at);"""

ACTIONS = ('add', 'change', 'stop', 'resume', 'delete_forever')
# An entrance at most this often, so someone who lives here is not announced every time they walk
# back in. V2 greeted each special person once a run.
ENTRANCE_GAP_SECONDS = 3 * 3600.
# A callback at most this often, counted from any heard callout line and from the last change.
RIFF_SECONDS = 15 * 60.
ANGLES_MAX = 6
OFF_LIMITS_MAX = 8
EARLIER_LINES = 3        # Shown, so a new line never reuses an old one's wording.
LINES_KEPT = 24          # Kept per callout.

# Words too common, or too generic about fame, to show that a line used a callout.
COMMON = frozenset("""about after again also always another around back been before being best biggest
both came come could does doing done down even ever every famous fame from give going good great greatest
have here huge into just know legend legendary like look made major make many more most much must never
only other over people person really right same should since some star still such superstar sure take
than that their them then there these they thing things this those time treat treatment very want week
well were what when whenever where which while will with would year your yours celebrity world""".split())


def _keys(text):
    """Comparable stems of a text's distinctive words: "Kings" and "king", "styling" and "stylist"."""
    keys = set()
    for word in re.findall(r"[a-z']+", (text or '').casefold().replace('’', "'")):
        word = word.strip("'")
        if word.endswith("'s"):
            word = word[:-2]
        if len(word) > 4 and word.endswith('s'):
            word = word[:-1]
        if len(word) >= 4 and word not in COMMON:
            keys.add(word[:5])
    return keys


def uses(line, entry, names=()):
    """Whether a heard line of Rex's used this callout: a distinctive word of its claim to fame or an angle
    beside one of their names, or two such words. October 25 (c5ad0deb): one word alone counted lines
    about others ("celebrity-level patience" for Daniel was a riff for PJ and Max), holding back theirs."""
    shared = _keys(line) & _keys(' '.join([entry['famous_for'], *entry['angles']]))
    said = {name_key(w) for w in name_words(line)}
    named = any(name_key(w) in said for n in names for w in name_words(n) if len(name_key(w)) >= 2)
    return len(shared) >= 2 or bool(shared) and named


def _text(value, limit, reason):
    value = ' '.join(str(value or '').split())
    if not value or len(value) > limit:
        raise ValueError(reason)
    return value


def _items(values):
    out = []
    for value in values or ():
        item = _text(value, 100, 'InvalidCalloutItem')
        if item.casefold() not in {o.casefold() for o in out}:
            out.append(item)
    return out


def _blocked(angle, off_limits):
    """An off-limits item drops the angle that names the same thing ("no more cowbells")."""
    return any(_keys(angle) & _keys(o) for o in off_limits)


def _row(conn, subject_kind, subject_id):
    column = 'person_id' if subject_kind == 'person' else 'pet_id'
    return conn.execute(f'SELECT * FROM callouts WHERE {column}=?', (subject_id,)).fetchone()


def apply(conn, subject_kind, subject_id, about, args, *, said_by, turn_id, source_text, now):
    """Add, change, stop, resume or delete the callout of one person or saved pet."""
    action = args['action']
    if action not in ACTIONS:
        raise ValueError('InvalidCalloutAction')
    row = _row(conn, subject_kind, subject_id)
    if action == 'add':
        famous = _text(args.get('famous_for'), 120, 'CalloutNeedsWhatTheyAreFamousFor')
        off = _items(args.get('off_limits'))[:OFF_LIMITS_MAX]
        angles = [a for a in _items(args.get('angles')) if not _blocked(a, off)][:ANGLES_MAX]
        if row is None:
            callout_id = uuid4().hex
            conn.execute('INSERT INTO callouts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', (
                callout_id, subject_id if subject_kind == 'person' else None,
                subject_id if subject_kind == 'pet' else None, famous, json.dumps(angles), json.dumps(off),
                'on', said_by, turn_id, source_text[:600], now, now))
        else:
            # Said again: it starts over, but his earlier lines stay so the next one is new.
            callout_id = row['id']
            conn.execute('UPDATE callouts SET famous_for=?,angles=?,off_limits=?,status=?,added_by=?,source_turn=?,'
                         'source_text=?,updated_at=? WHERE id=?', (famous, json.dumps(angles), json.dumps(off), 'on',
                         said_by, turn_id, source_text[:600], now, callout_id))
        status = 'on'
    else:
        if row is None:
            raise ValueError('NoCalloutForThem')
        callout_id = row['id']
        famous, angles, off = row['famous_for'], json.loads(row['angles']), json.loads(row['off_limits'])
        status = row['status']
        if action == 'change':
            given = [args.get('famous_for'), *(args.get('angles') or ()), *(args.get('off_limits') or ())]
            if not any(isinstance(v, str) and v.strip() for v in given):
                raise ValueError('CalloutChangeNeedsSomething')
            if args.get('famous_for'):
                famous = _text(args['famous_for'], 120, 'CalloutNeedsWhatTheyAreFamousFor')
            off = _items([*off, *(args.get('off_limits') or ())])[-OFF_LIMITS_MAX:]
            angles = [a for a in _items([*angles, *(args.get('angles') or ())]) if not _blocked(a, off)][-ANGLES_MAX:]
            conn.execute('UPDATE callouts SET famous_for=?,angles=?,off_limits=?,updated_at=? WHERE id=?',
                         (famous, json.dumps(angles), json.dumps(off), now, callout_id))
        elif action in {'stop', 'resume'}:
            status = 'stopped' if action == 'stop' else 'on'
            conn.execute('UPDATE callouts SET status=?,updated_at=? WHERE id=?', (status, now, callout_id))
        else:
            conn.execute('DELETE FROM callouts WHERE id=?', (callout_id,))   # Its heard lines go with it.
            status = 'removed'
    meaning = {
        'on': (f'{about} has this callout now: when they arrive you give them an entrance, and now and then a '
               'callback while they are here. It is your material, not a fact about them.'
               if subject_kind == 'person' else
               f'{about} has this callout now: when they wander in you give them an entrance, and now and then a '
               'callback while they are around. It is your material, not a fact about them.'),
        'stopped': f'Switched off: you no longer do this bit for {about}. It is kept in case they want it back.',
        'removed': f'Deleted for good: you never do this bit for {about} again.'}[status]
    return {'callout_id': callout_id, 'action': action, 'about': about, 'subject_kind': subject_kind,
            'status': status, 'famous_for': famous, 'angles': angles, 'off_limits': off,
            'text': args.get('text'), 'meaning': meaning}


def subject_of(conn, callout_id):
    row = conn.execute('SELECT person_id,pet_id FROM callouts WHERE id=?', (callout_id,)).fetchone()
    if row is None:
        raise ValueError('UnknownCallout')
    return ('pet', row['pet_id']) if row['pet_id'] else ('person', row['person_id'])


def with_callout(conn, person_ids):
    """The people among these who have a callout, on or stopped."""
    return [p for p in person_ids if conn.execute('SELECT 1 FROM callouts WHERE person_id=?', (p,)).fetchone()]


def entry(conn, row, about, now, *, arrival=False):
    """One callout as the reply model sees it."""
    lines = conn.execute('SELECT kind,line,said_at FROM callout_lines WHERE callout_id=? ORDER BY said_at DESC LIMIT ?',
                         (row['id'], EARLIER_LINES)).fetchall()
    last = max([r['said_at'] for r in lines] + [row['updated_at']])
    added_by = (conn.execute('SELECT name FROM people WHERE id=?', (row['added_by'],)).fetchone()
                if row['added_by'] else None)
    result = {'callout_id': row['id'], 'about': about, **({'pet': True} if row['pet_id'] else {}),
              'famous_for': row['famous_for'], 'angles': json.loads(row['angles']),
              'off_limits': json.loads(row['off_limits']), 'added_by': added_by['name'] if added_by else None,
              'riff_now': now - last >= RIFF_SECONDS,
              'earlier_lines': [{'line': r['line'], 'said': ago(now - r['said_at'])} for r in lines]}
    if arrival:
        entrance = conn.execute("SELECT max(said_at) FROM callout_lines WHERE callout_id=? AND kind='entrance'",
                                (row['id'],)).fetchone()[0]
        result['entrance_due'] = entrance is None or now - entrance >= ENTRANCE_GAP_SECONDS
    return result


def context(conn, people, pets, arriving, now, away=()):
    """The callouts of the people (person_id -> name) and pets (pet_id -> name) around now.

    `arriving` holds the ids being greeted: only their entries say whether an entrance is due. `away`
    holds people only talked about lately, not around: their entries say here false. A stopped one is
    listed by id and name only, so "bring it back" can find it (hosted probe, October 25)."""
    here = []
    for column, subjects in (('person_id', people), ('pet_id', pets)):
        for subject_id, about in subjects.items():
            row = conn.execute(f'SELECT * FROM callouts WHERE {column}=?', (subject_id,)).fetchone()
            if row is None:
                continue
            shown = (entry(conn, row, about, now, arrival=subject_id in arriving) if row['status'] == 'on' else
                     {'callout_id': row['id'], 'about': about, 'status': 'stopped'})
            here.append({**shown, **({'here': False} if subject_id in away else {})})
    return {'here': here, 'meaning': GUIDE['callouts']} if here else None


def describe(conn, person_id):
    """A person's callout for recall ("what's PJ's bit?"), on or stopped."""
    row = _row(conn, 'person', person_id)
    if row is None:
        return None
    return {'callout_id': row['id'], 'status': row['status'], 'famous_for': row['famous_for'],
            'angles': json.loads(row['angles']), 'off_limits': json.loads(row['off_limits'])}


def heard(conn, offered, line, now):
    """Record the offered callouts a heard line of Rex's used: (callout_id, kind) for each."""
    used = []
    for item in offered or ():
        row = conn.execute('SELECT person_id FROM callouts WHERE id=?', (item['callout_id'],)).fetchone()
        if item.get('status') == 'stopped' or row is None:
            continue
        person = row['person_id'] and conn.execute('SELECT name,aliases FROM people WHERE id=?', (row['person_id'],)).fetchone()
        names = [person['name'], *json.loads(person['aliases'])] if person else [item['about']]
        if not uses(line, item, names):
            continue
        kind = 'entrance' if item.get('entrance_due') else 'riff'
        conn.execute('INSERT INTO callout_lines VALUES(?,?,?,?)', (item['callout_id'], kind, line[:400], now))
        conn.execute('DELETE FROM callout_lines WHERE callout_id=? AND said_at NOT IN (SELECT said_at FROM callout_lines '
                     'WHERE callout_id=? ORDER BY said_at DESC LIMIT ?)', (item['callout_id'], item['callout_id'], LINES_KEPT))
        used.append((item['callout_id'], kind))
    return used


def forget_pet(conn, pet_id):
    conn.execute('DELETE FROM callouts WHERE pet_id=?', (pet_id,))
