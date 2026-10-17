"""Source-linked cross-session recall. Selection never interprets an utterance."""
from datetime import datetime, time, timedelta, timezone
import json
import re

# How long a recognized person remains the private audience while the camera cannot confirm
# them, and the company rule, live with the picture of the present.
from .present import PRIVATE_CARRY_SECONDS

SCHEMA = """
INSERT OR IGNORE INTO meta VALUES('conversation_memory_schema','2');
CREATE TABLE IF NOT EXISTS conversation_exchanges(
 id TEXT PRIMARY KEY,session_id TEXT NOT NULL,person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 source_turn TEXT REFERENCES turns(id),created_at REAL NOT NULL,human_text TEXT NOT NULL,
 heard_text TEXT NOT NULL,status TEXT NOT NULL,origin TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS exchange_person_time ON conversation_exchanges(person_id,created_at);
CREATE TABLE IF NOT EXISTS conversation_episodes(
 id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 source_exchange TEXT NOT NULL REFERENCES conversation_exchanges(id) ON DELETE CASCADE,
 summary TEXT NOT NULL,created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS conversation_exchange_evidence(
 exchange_id TEXT PRIMARY KEY REFERENCES conversation_exchanges(id) ON DELETE CASCADE,
 evidence TEXT NOT NULL);
UPDATE meta SET value='2' WHERE key='conversation_memory_schema';
"""


def evidenced_subject(context):
    """The private audience current evidence supports: a supported speaker or one
    recognized visible person. Unknown/additional faces give none. A visible person is an
    audience, never evidence that they spoke."""
    faces = context.get('current_view', {}).get('faces', context.get('faces', []))
    speaker = context.get('speaker', {})
    who = speaker.get('person_id') if speaker.get('state') == 'supported' else None
    if faces:
        if len(faces) != 1 or not faces[0].get('identity'): return None
        if who and who != faces[0]['identity']: return None
        who = faces[0]['identity']
    return who if any(p['id'] == who and p['status'] == 'supported' for p in context.get('people', [])) else None


def contradicted(context, person):
    """Evidence that the audience is no longer just this person: several faces at once, or
    a face or supported voice recognized as somebody else."""
    faces = context.get('current_view', {}).get('faces', context.get('faces', []))
    speaker = context.get('speaker', {})
    return (len(faces) > 1 or any(f.get('identity') not in (None, person) for f in faces) or
            (speaker.get('state') == 'supported' and speaker.get('person_id') != person))


def subject(context):
    """Who private memory may be used with now: the evidenced audience, else the person
    recognized moments ago while nothing contradicts the same visit.

    The carried person needs an empty view or a single not-yet-recognized face, no supported
    voice of somebody else, and a recognition within PRIVATE_CARRY_SECONDS. Carrying
    authorizes memory use only; it never attributes an utterance or enrolls anything."""
    who = evidenced_subject(context)
    if who: return who
    recent = context.get('recent_audience') or {}
    carried, age = recent.get('person_id'), recent.get('seconds_ago')
    if not carried or type(age) not in (int, float) or not 0 <= age <= PRIVATE_CARRY_SECONDS: return None
    faces = context.get('current_view', {}).get('faces', context.get('faces', []))
    if len(faces) > 1 or any(f.get('identity') for f in faces): return None
    speaker = context.get('speaker', {})
    if speaker.get('state') == 'supported' and speaker.get('person_id') != carried: return None
    return carried if any(p['id'] == carried and p['status'] == 'supported' for p in context.get('people', [])) else None


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def part_of_day(hour):
    return 'night' if hour < 5 or hour >= 21 else 'morning' if hour < 12 else 'afternoon' if hour < 17 else 'evening'


def local_time(value, now=None):
    """Wall-clock label in the robot's local time. October 11: Rex was given UTC times only,
    so a 7 PM chat stamped 02:03 the next day never read as "last night"."""
    when = datetime.fromtimestamp(value)
    label = when.strftime('%a %b %d, %I:%M %p').replace(' 0', ' ')
    if now is None: return label
    days = (datetime.fromtimestamp(now).date() - when.date()).days
    day = 'today' if days == 0 else 'yesterday' if days == 1 else f'{days} days ago'
    return f'{label} ({day}, {part_of_day(when.hour)})'


# Everyday phrases the model names; code does the local clock arithmetic. October 11: asked
# "Do you remember what I told you last night?", the model searched the words "last night"
# with no time bounds and missed the chat from 7 PM the evening before.
PERIODS = ('earlier_today', 'this_morning', 'last_night', 'yesterday')


def period_window(period, now):
    local = datetime.fromtimestamp(now)
    today = local.date()
    at = lambda day, hour: datetime.combine(day, time(hour)).timestamp()
    if period == 'earlier_today': return at(today, 0), now
    if period == 'this_morning': return at(today, 5), min(now, at(today, 12))
    if period == 'yesterday': return at(today - timedelta(days=1), 0), at(today, 0)
    if period == 'last_night':
        # Before 5 AM the current night is still "tonight"; "last night" is the one before.
        anchor = today if local.hour >= 5 else today - timedelta(days=1)
        return at(anchor - timedelta(days=1), 17), at(anchor, 5)
    raise ValueError('InvalidRecallPeriod')


def compact_attribution(evidence):
    """Who spoke and who was present, without per-utterance microphone geometry."""
    speaker = evidence.get('speaker') or {}
    return {'speaker': {k: speaker.get(k) for k in ('person_id', 'state', 'reason', 'display_name')} if speaker else {},
            'audience_basis': evidence.get('audience_basis'),
            'visible_people': [{'identity': f.get('identity')} for f in evidence.get('visible_people', [])]}


def attach_evidence(store, rows):
    """Audience scope authorizes recall; it does not certify who spoke."""
    for row in rows:
        source = row.get('source_exchange', row['id'])
        saved = store.rows('''SELECT e.evidence, t.evidence AS original_evidence,
            x.person_id, x.source_turn FROM conversation_exchanges x
            LEFT JOIN conversation_exchange_evidence e ON e.exchange_id=x.id
            LEFT JOIN turns t ON t.id=x.source_turn WHERE x.id=?''', (source,))
        if not saved: continue
        item = saved[0]
        row['audience_person_id'] = item['person_id']
        if item['evidence']:
            row['attribution'] = compact_attribution(json.loads(item['evidence']))
        else:
            # Version 1 only wrote supported human voices. Preserve actual source
            # evidence where available rather than inventing camera observations.
            row['attribution'] = compact_attribution({
                'speaker': json.loads(item['original_evidence'] or '{}').get('attribution', {}),
                'audience_basis': 'legacy_supported_speaker' if item['source_turn'] else 'legacy_arrival'})


def boundary(value, now=None):
    if value is None: return None
    if type(value) in (int,float):
        import math
        if now is None or not math.isfinite(value) or not 0 <= value <= 100*366*86400:
            raise ValueError("InvalidRecallOffset")
        return now-value
    date = datetime.fromisoformat(value)
    if date.tzinfo is None: raise ValueError('RecallTimeRequiresUTCOffset')
    return date.timestamp()


# A requested period returns that whole stretch of conversation, oldest first. October 11:
# the latest twelve lines of a 43-exchange evening left out the one being asked about.
WINDOW_LIMIT = 60


def words(text):
    return re.findall(r"[\w']+", text.casefold())


# Words too common to say what a search is about.
QUERY_STOP = frozenset({'a','an','the','and','or','of','to','in','on','at','for','with','about','my','me','i',
                        'you','your','we','our','is','was','are','were','be','do','did','have','has','had','it',
                        'that','this','what','who','whether','ever','told','tell','said','say','know'})


def _form(word):
    """One spelling for plurals and common endings: "stars" and "star", "camping" and "camp"."""
    word = word.removesuffix("'s")
    for ending in ('ing', 'ed', 'es', 's'):
        if len(word) > len(ending) + 3 and word.endswith(ending):
            return word[:-len(ending)]
    return word


def _close(a, b):
    """Near spellings of one word: speech recognition misheard a name by a letter or two."""
    if len(a) < 4 or len(b) < 4 or a[0] != b[0] or abs(len(a) - len(b)) > 2:
        return False
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= .7


def match_score(wanted, text):
    """How well text matches the wanted word forms: 1 per exact form, half per near spelling."""
    have = {_form(w) for w in words(text)}
    return sum(1. if w in have else .5 if any(_close(w, h) for h in have) else 0. for w in wanted)


def recall(store, who, session, *, query='', after=None, before=None, period=None, limit=12, now=None):
    if not who: raise ValueError('PrivateRecallRequiresKnownAudience')
    if period is not None:
        if after is not None or before is not None: raise ValueError('RecallPeriodConflictsWithBounds')
        if now is None: raise ValueError('InvalidRecallPeriod')
        low, high = period_window(period, now)
    else:
        low, high = boundary(after,now), boundary(before,now)
        if type(after) in (int,float) and type(before) in (int,float):
            low, high = sorted((low,high))  # Relative offsets describe an unordered age window.
    if low is not None and high is not None and low >= high: raise ValueError('InvalidRecallInterval')
    windowed = low is not None or high is not None
    sql = 'SELECT * FROM conversation_exchanges WHERE person_id=? AND session_id<>?'
    args = [who, session]
    if low is not None: sql += ' AND created_at>=?'; args.append(low)
    if high is not None: sql += ' AND created_at<?'; args.append(high)
    count = store.rows('SELECT count(*) AS n FROM ('+sql+')', args)[0]['n']
    wanted = {_form(w) for w in words(query or '') if w not in QUERY_STOP}
    # October 16 (3af18a12): only the newest 200 of 660 exchanges were searched, so "Voyager",
    # said 329 exchanges earlier, could not be found. A search with words reads them all.
    rows = store.rows(sql+' ORDER BY created_at DESC,id DESC LIMIT '+('5000' if wanted else '200'), args)
    def relevance(row):
        # Word forms and near spellings; what the person said counts double.
        return 2*match_score(wanted, row['human_text']) + match_score(wanted, row['heard_text'])
    if wanted:
        if not windowed:
            rows = [row for row in rows if relevance(row)]   # A period is kept whole, as a conversation.
        rows.sort(key=lambda row: (relevance(row), bool(row['human_text']), row['created_at']), reverse=True)
    elif windowed:
        rows.sort(key=lambda row: (bool(row['human_text']), row['created_at']), reverse=True)
    selected = rows[:WINDOW_LIMIT if windowed else limit]
    matched = not wanted or any(relevance(row) for row in selected)
    if windowed: selected.sort(key=lambda row: (row['created_at'], row['id']))
    attach_evidence(store, selected)
    for row in selected:
        row['local_time'] = local_time(row['created_at'], now)
        row['at'] = stamp(row.pop('created_at'))
        row['text_truncated'] = len(row['human_text'])>600 or len(row['heard_text'])>600
        row['human_text'] = row['human_text'][:600]
        row['heard_text'] = row['heard_text'][:600]
    result = {'exchanges':selected, 'matching_interval_count':count,
              'selection_truncated':count>len(selected), 'searched_latest_limit':5000 if wanted else 200,
              'order':'oldest first: the requested period as a conversation' if windowed else
                      'best word matches first' if wanted else 'newest first',
              'scope':'conversations scoped to this audience; speaker attribution is separate; prior sessions; completed audio phrases only',
              'coverage':'Dated archive of prior sessions; local_time is the robot\'s local clock. Absence of an excerpt is not proof nothing happened.'}
    if wanted:
        result['searched'] = 'every earlier conversation with this person, by word forms and near spellings'
        if not matched:
            # October 16: with no match, the newest unrelated chats ("Can you shut down?") came back.
            result['no_match'] = ('No earlier conversation mentions those words. Say so plainly; do not '
                                  'present unrelated conversations as an answer.')
    if windowed:
        result['window'] = {'from': local_time(low, now) if low is not None else None,
                            'until': local_time(high, now) if high is not None else None}
    if not windowed and (not selected or not matched):
        # Existing turns used a monotonic clock. Never invent calendar dates or
        # delivered replies for these pre-upgrade sources. October 11: offered beside
        # dated results, its "undated" note became Rex's reason to say he could not remember.
        legacy = store.rows("""SELECT id,session_id,text AS human_text FROM turns
            WHERE session_id<>? AND json_extract(evidence,'$.attribution.person_id')=?
            AND json_extract(evidence,'$.attribution.state')='supported'
            AND json_extract(evidence,'$.trusted')=1 AND json_extract(evidence,'$.echo_risk')=0
            AND NOT EXISTS(SELECT 1 FROM conversation_exchanges x WHERE x.source_turn=turns.id)
            ORDER BY rowid DESC LIMIT """+('2000' if wanted else '8'),(session,who))
        if wanted:
            legacy = sorted((row for row in legacy if match_score(wanted, row['human_text'])),
                            key=lambda row: match_score(wanted, row['human_text']), reverse=True)[:8]
        for row in legacy:
            row['text_truncated'] = len(row['human_text'])>600
            row['human_text'] = row['human_text'][:600]
        result['legacy_source_utterances'] = legacy
        result['legacy_caveat'] = ('Supplementary words from before conversations were dated; they cannot be '
            'placed at a particular time. The dated exchanges above are the main record.')
    return result


def gap(seconds):
    """How long ago, in words, at the scale of visits rather than moments."""
    minutes = max(seconds, 0) / 60
    if minutes < 3: return 'a minute or two ago'
    if minutes < 55: return f'about {5*round(minutes/5) or 5} minutes ago'
    if minutes < 90: return 'about an hour ago'
    if minutes < 36*60: return f'about {round(minutes/60)} hours ago'
    return f'about {round(minutes/1440)} days ago'


def greetings(store, who, now, limit=3):
    """Rex's last heard arrival greetings to this person, across runs, newest first.

    October 14: restarted twice in one evening, Rex opened both runs with "Hey, Brett! Good
    to see you. How's your night going?". V2 kept "oh hey, you're back!" alive by reading the
    persisted last greeting after a reboot; here the model reads the same fact and chooses
    the words."""
    rows = store.rows("""SELECT heard_text, created_at FROM conversation_exchanges
        WHERE person_id=? AND origin='visual_arrival' AND heard_text<>''
        ORDER BY created_at DESC LIMIT ?""", (who, limit))
    return [{'said': row['heard_text'][:300], 'ago': gap(now - row['created_at']),
             'local_time': local_time(row['created_at'], now)} for row in rows]


def told(row, now):
    """When a fact was told and, for a plan with a time, whether that time has passed, in words
    the code works out. October 15 (2f85db5c): "in five minutes, I'm going to the store to get
    wine", told the night before, was still offered as tonight's wine run in 6 of 13 joke
    batches; nothing said when it was told."""
    from .memory_opportunities import clock, span, timing
    row = dict(row)
    at, due = row.pop('told_at', None), row.pop('due_at', None)
    kind = row.pop('followup_kind', None)
    asked = row.pop('followup_asked_on', None)
    row['told'] = span(now - at) + ' ago (' + clock(at) + ')' if at is not None else 'unknown'
    if due is not None:
        row['planned_time'] = timing({'said_at': at, 'due_at': due, 'kind': kind}, now)['due']
    if asked:
        # October 16: the wine run was asked about three runs in a row; nothing said it had been.
        row['followup'] = ('already asked about (' + asked + '); their answer is in the conversation record. '
                           'Do not ask about it again.')
        if due is not None and now >= due:
            row['planned_time'] = 'a past plan, already asked about: not current'
    return row


def context(store, original, session, now):
    who = subject(original)
    result = {'person_id':who, 'now':stamp(now), 'local_now':datetime.fromtimestamp(now).astimezone().isoformat(), 'recent_exchanges':[], 'interests_and_facts':[], 'episodes':[]}
    if not who: return result
    result['recent_exchanges'] = recall(store,who,session,limit=4,now=now)['exchanges']
    result['earlier_greetings'] = greetings(store, who, now)
    # October 16: only the newest 6 of 8 saved facts reached each reply.
    result['interests_and_facts'] = [told(row, now) for row in store.claims(who)]
    result['current_session_exchanges'] = store.rows('SELECT id,human_text,heard_text FROM conversation_exchanges WHERE person_id=? AND session_id=? ORDER BY created_at DESC LIMIT 3',(who,session))
    result['episodes'] = store.rows('''SELECT e.id,e.summary,e.source_exchange,x.human_text,x.heard_text
        FROM conversation_episodes e JOIN conversation_exchanges x ON x.id=e.source_exchange
        WHERE e.person_id=? ORDER BY e.created_at DESC LIMIT 4''',(who,))
    attach_evidence(store, result['current_session_exchanges'])
    attach_evidence(store, result['episodes'])
    for row in result['current_session_exchanges'] + result['episodes']:
        row['text_truncated'] = len(row['human_text'])>600 or len(row['heard_text'])>600
        row['human_text'],row['heard_text'] = row['human_text'][:600],row['heard_text'][:600]
    result['coverage'] = 'Prior sessions are partial excerpts, not a complete day or exact greeting count.'
    return result


def record(store, turn_id, session, original, capture, transcript, delivery, now):
    who = subject(original)
    # A recognized audience can retain a conversation even when individual voice
    # windows abstain. Store the uncertainty, never turn visibility into identity.
    if not who: return {'status':'skipped', 'reason':'no_known_private_audience'}
    if capture is not None:
        if not transcript.trusted: return {'status':'skipped', 'reason':'untrusted_transcript'}
        if capture.echo_risk: return {'status':'skipped', 'reason':'echo_risk'}
        if capture.truncated: return {'status':'skipped', 'reason':'truncated_capture'}
        if capture.overlap is True: return {'status':'skipped', 'reason':'overlap'}
    heard = ' '.join(delivery.completed_text) if delivery.medium == 'audio' else ''
    human = transcript.text if capture is not None else ''
    if not human and not heard: return {'status':'skipped', 'reason':'empty_exchange'}
    speaker = original.get('speaker', {}) if capture is not None else {}
    faces = original.get('current_view', {}).get('faces', original.get('faces', []))
    evidence = {'speaker':speaker,
                'audience_basis':('supported_speaker' if speaker.get('state') == 'supported' else
                                  'recognized_visible_person' if evidenced_subject(original) else 'recently_recognized_person'),
                'visible_people':[{'track_id':f.get('track_id'), 'identity':f.get('identity')} for f in faces]}
    with store.transaction() as conn:
        conn.execute('INSERT OR IGNORE INTO conversation_exchanges VALUES(?,?,?,?,?,?,?,?,?)',
            (turn_id,session,who,turn_id if capture else None,now,human,heard,delivery.status,
             original.get('trigger','human_turn')))
        conn.execute('INSERT OR IGNORE INTO conversation_exchange_evidence VALUES(?,?)',
                     (turn_id,json.dumps(evidence)))
    return {'status':'recorded', 'exchange_id':turn_id, 'audience_person_id':who,
            'speaker_state':speaker.get('state','not_applicable'),
            'audience_basis':evidence['audience_basis']}


def episode(store, conn, who, args, request_id, now):
    if not who: raise ValueError('PrivateRecallRequiresKnownAudience')
    source = store.rows('SELECT * FROM conversation_exchanges WHERE id=? AND person_id=?',
                        (args['source_exchange_id'],who))
    if not source: raise ValueError('EpisodeSourceUnavailable')
    summary = args['summary'].strip()
    if not summary or len(summary)>600: raise ValueError('InvalidEpisodeSummary')
    conn.execute('INSERT INTO conversation_episodes VALUES(?,?,?,?,?)',
                 (request_id,who,args['source_exchange_id'],summary,now))
    return {'episode_id':request_id,'summary':summary,'source_exchange_id':args['source_exchange_id']}
