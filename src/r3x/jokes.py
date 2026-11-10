"""Rex's humor bookkeeping: his bits and how they landed, and running gags.

October 14 improvement plan, step 4 (owner: V2 "joked more, roasted people more", but from
hard-coded lines that soon repeated). None of it sits in front of a reply:

- A joke record (`joke_record`): bits Rex told and, when the model notes it with `note_bit`,
  how they landed (landed, flopped, asked to stop). Landed bits about a person become running
  gags offered back, in that person's private audience only, so he can call back to last week;
  flopped and stopped bits are offered as things to leave alone.
- Joke shapes he has used up this run (`spent_shapes`), found in his own heard lines.
- Nothing here interprets what a person said; the conversation model judges reactions.

The writer's room (a second model writing joke angles between turns) was retired by the owner on
November 10: little of its material was used and every call was paid for.
"""
from .field_guide import GUIDE
import re
from uuid import uuid4

SCHEMA = """CREATE TABLE IF NOT EXISTS joke_record(
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
    person_id TEXT REFERENCES people(id) ON DELETE CASCADE, audience TEXT NOT NULL,
    premise TEXT NOT NULL, line TEXT NOT NULL, reaction TEXT NOT NULL, created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS joke_record_person ON joke_record(person_id, created_at);"""

REACTIONS = ('told', 'landed', 'flopped', 'asked_to_stop')
GAG_DAYS = 60            # Landed bits about a person stay running-gag material this long.
AVOID_DAYS = 30          # Flopped or stopped bits are left alone this long.
DAY_S = 86400.


def audience_key(person_id):
    """Bits about a known person stay with that person; others belong to the general room."""
    return 'person:' + person_id if person_id else 'general'


def record(conn, session_id, person_id, audience, premise, line, reaction, now):
    conn.execute('INSERT INTO joke_record VALUES(?,?,?,?,?,?,?,?)',
                 (uuid4().hex, session_id, person_id, audience, premise[:200], line[:400], reaction, now))


def context(store, person_id, audience, now):
    """What Rex should know about his own material for this audience."""
    rows = store.rows('SELECT premise,line,reaction,created_at,person_id FROM joke_record '
                      'WHERE (person_id=? OR (person_id IS NULL AND audience=?)) AND created_at>=? '
                      'ORDER BY created_at DESC LIMIT 40', (person_id, audience, now - GAG_DAYS * DAY_S))
    gags = [r['premise'] for r in rows if r['reaction'] == 'landed' and person_id and r['person_id'] == person_id]
    avoid = [r['premise'] for r in rows if r['reaction'] in {'flopped', 'asked_to_stop'}
             and now - r['created_at'] <= AVOID_DAYS * DAY_S]
    told = [r['premise'] for r in rows if now - r['created_at'] <= 2 * DAY_S]
    result = {}
    if gags:
        result['running_gags'] = list(dict.fromkeys(gags))[:6]
    if avoid:
        result['bits_that_flopped'] = list(dict.fromkeys(avoid))[:6]
    if told:
        result['bits_told_lately'] = list(dict.fromkeys(told))[:10]
    if result:
        result['meaning'] = GUIDE['humor']
    return result


# Joke shapes he leaned on until they were all anyone heard (owner, October 24: "the repeat jokes told
# different ways is annoying"). In the ten runs of October 22-24, 23 of 138 lines turned the person's
# day into an office and 16 reached for his piloting record. Found in Rex's own heard lines only, never
# in anything a person said: the office is spent once used at all, the piloting record after one a visit.
SHAPES = {
    'treating them like an office (departments, logistics, paperwork, compliance, committees, complaints, '
    'case files, briefings, operations)': (re.compile(
        r"\b(department|logistic|paperwork|compliance|committee|case file|clipboard|briefing|deposition|probation|"
        r"performance review|qa department|filed? a complaint|files a complaint|command (?:center|post)|operation)", re.I), 1),
    'your bad piloting (flight record, navigation, landings, overshooting Endor)': (re.compile(
        r"\b(pilot|flight (?:record|plan|instructor)|navigation|landing record|overshot|overshoot|endor|cockpit|"
        r"cleared to (?:fly|pilot))", re.I), 1),
}


def spent_shapes(messages):
    """The joke shapes his own lines this run have already used up."""
    said = [m['content'] for m in messages if m.get('role') == 'assistant' and isinstance(m.get('content'), str)]
    return [shape for shape, (pattern, allowed) in SHAPES.items()
            if sum(1 for line in said if pattern.search(line)) >= allowed]
