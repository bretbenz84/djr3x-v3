"""What Rex does not know yet about the people and pets here, so he asks like a friend.

Owner, October 24, after runs e72ab522..27e8b12a: "Rex appears to have no curiosity. Most things
are statements or jokes about what he sees... He almost never asks me personal questions or
tries to get to know me better... I've never heard Rex 'tell me about when you first got your pet
Max'. True friends ask to know more, then remember it and use that info for future
conversations." In those ten runs 4 of his 143 lines asked about the owner's life, the dogs only
got "Is he behaving?", and nothing new about the owner was saved.

Code only offers the topics and remembers which ones he asked; the one conversation owner
judges whether saved facts or pet notes already answer one, words the question, and saves the
answer with the existing memory tools. Only questions actually heard are recorded, and the record
holds no words: a topic, and the person or pet it was about. Forgetting a person cascades;
forgetting a pet deletes its rows (CoreState.apply_photo).
"""
from uuid import uuid4

from .field_guide import GUIDE

SCHEMA = """CREATE TABLE IF NOT EXISTS curiosity_asked(
    id TEXT PRIMARY KEY, person_id TEXT REFERENCES people(id) ON DELETE CASCADE,
    pet_id TEXT, topic TEXT NOT NULL, asked_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS curiosity_asked_at ON curiosity_asked(asked_at);"""

# What a friend wants to know about someone. Owner defaults, October 24: work, partner, family,
# childhood, friends, pets, trips, hobbies and their day are fine; health, money and politics only
# when they bring them up (those topics are simply not here).
PERSON_TOPICS = {
    'work': 'what they do for work, and what a day of it is actually like',
    'grew_up': 'where they grew up, and what it was like there',
    'as_a_kid': 'what they were like as a kid',
    'family': 'their family: parents, brothers or sisters',
    'partner_story': 'how they met their partner (only when Rex knows they have one)',
    'friends': 'who their closest friends are, and how they met',
    'best_trip': 'the best trip they ever took',
    'day_off': 'what a perfect day off looks like for them',
    'food': 'their favorite thing to eat, or what they cook',
    'proudest': 'the thing they made or did that they are proudest of',
}
PET_TOPICS = {
    'came_home': 'when and how {pet} came home to them',
    'name_story': 'how {pet} got the name',
    'age': 'how old {pet} is',
    'worst_crime': 'the worst thing {pet} has ever done',
    'strange_habit': "{pet}'s strangest habit",
    'favorite_thing': "{pet}'s favorite thing in the world",
}
INTEREST_TOPIC = 'how they got into {interest}, and their best moment with it'
ASKED_COOLDOWN = 3 * 86400.   # Unanswered, the same topic may come back a few days later.
OFFERED = 8                  # Topics offered at a time, never-asked ones first.
OPEN_SECONDS = 180.          # His question stays open for an answer this long.

MEANING = GUIDE['curiosity']


def _interests(store, person_id):
    rows = store.rows("""SELECT i.topic FROM interests i JOIN claims c ON c.id=i.claim_id
        WHERE c.subject_id=? AND c.status='accepted' ORDER BY c.said_at DESC""", (person_id,))
    seen, out = set(), []
    for row in rows:
        topic = ' '.join(str(row['topic'] or '').split())
        if topic and topic.lower() not in seen:
            seen.add(topic.lower())
            out.append(topic)
    return out[:4]


def slug(text, limit=48):
    return ''.join(ch if ch.isalnum() else '_' for ch in text.lower())[:limit]


def pet_key(key, name):
    """Each pet's topics have their own ids: Max's came_home is not Toby's."""
    return slug(name, 24) + '_' + key


def interest_key(topic):
    return 'interest_' + slug(topic)


def _asked(store, wall_now):
    rows = store.rows('SELECT person_id, pet_id, topic, MAX(asked_at) AS at FROM curiosity_asked '
                      'GROUP BY person_id, pet_id, topic')
    return {(r['person_id'], r['pet_id'], r['topic']): r['at'] for r in rows}


def still_unknown(store, person_id, name, pets, wall_now, pets_first=False):
    """Topics to ask about, never-asked ones first, then the longest ago. person_id may be None (pets
    only); pets are saved pet entities ({'entity_id', 'name'}). Topics asked within the cooldown are
    left out; whether a saved fact already answers one is the model's judgment."""
    asked = _asked(store, wall_now)
    def add(rows, key, who, about, pet_id=None):
        at = asked.get((None, pet_id, key) if pet_id else (person_id, None, key))
        if at is not None and wall_now - at < ASKED_COOLDOWN: return
        rows.append(({'topic': key, 'about': who, 'want_to_know': about,
                      **({'pet_id': pet_id} if pet_id else {})}, at is None, at or 0.))
    groups = []
    for pet in pets:
        if pet.get('entity_id') and pet.get('name'):
            rows = []
            for key, about in PET_TOPICS.items():
                add(rows, pet_key(key, pet['name']), pet['name'], about.format(pet=pet['name']), pet_id=pet['entity_id'])
            groups.append(rows)
    if person_id:
        rows = []
        for key, about in PERSON_TOPICS.items():
            add(rows, key, name, about)
        for topic in _interests(store, person_id):
            add(rows, interest_key(topic), name, INTEREST_TOPIC.format(interest=topic))
        groups.insert(len(groups) if pets_first else 0, rows)
    for rows in groups:
        rows.sort(key=lambda r: (not r[1], r[2]))   # Never asked first, then the oldest ask.
    # Round the subjects so a pet in view does not crowd out the person, nor one pet the other.
    out = []
    while any(groups) and len(out) < OFFERED:
        for rows in groups:
            if rows and len(out) < OFFERED: out.append(rows.pop(0)[0])
    return out


def context(store, person_id, name, pets, wall_now, pets_first=False):
    rows = still_unknown(store, person_id, name, pets, wall_now, pets_first=pets_first)
    return {'still_unknown': rows, 'meaning': MEANING} if rows else None


def offered(context_value, topic):
    """The offered row for a topic id the model named, or None."""
    for row in (context_value or {}).get('still_unknown', ()):
        if row['topic'] == topic: return row
    return None


def record(conn, row, person_id, wall_now):
    """A question he was heard asking: about a pet (pet_id) or the person."""
    conn.execute('INSERT INTO curiosity_asked VALUES(?,?,?,?,?)',
                 (uuid4().hex, None if row.get('pet_id') else person_id, row.get('pet_id'), row['topic'], wall_now))


def forget_pet(conn, pet_id):
    conn.execute('DELETE FROM curiosity_asked WHERE pet_id=?', (pet_id,))
