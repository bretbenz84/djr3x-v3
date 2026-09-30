"""Source-linked cross-session recall. Selection never interprets an utterance."""
from datetime import datetime, timezone

SCHEMA = """
INSERT OR IGNORE INTO meta VALUES('conversation_memory_schema','1');
CREATE TABLE IF NOT EXISTS conversation_exchanges(
 id TEXT PRIMARY KEY,session_id TEXT NOT NULL,person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 source_turn TEXT REFERENCES turns(id),created_at REAL NOT NULL,human_text TEXT NOT NULL,
 heard_text TEXT NOT NULL,status TEXT NOT NULL,origin TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS exchange_person_time ON conversation_exchanges(person_id,created_at);
CREATE TABLE IF NOT EXISTS conversation_episodes(
 id TEXT PRIMARY KEY,person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
 source_exchange TEXT NOT NULL REFERENCES conversation_exchanges(id) ON DELETE CASCADE,
 summary TEXT NOT NULL,created_at REAL NOT NULL);
"""


def subject(context):
    """Private recall requires a supported speaker or one recognized visible person.

    Unknown/additional faces prevent private recall. A visible person is an audience,
    never evidence that they spoke.
    """
    faces = context.get('current_view', {}).get('faces', context.get('faces', []))
    speaker = context.get('speaker', {})
    who = speaker.get('person_id') if speaker.get('state') == 'supported' else None
    if faces:
        if len(faces) != 1 or not faces[0].get('identity'): return None
        if who and who != faces[0]['identity']: return None
        who = faces[0]['identity']
    return who if any(p['id'] == who and p['status'] == 'supported' for p in context.get('people', [])) else None


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


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


def recall(store, who, session, *, query='', after=None, before=None, limit=12, now=None):
    if not who: raise ValueError('PrivateRecallRequiresKnownAudience')
    low, high = boundary(after,now), boundary(before,now)
    if type(after) in (int,float) and type(before) in (int,float):
        low, high = sorted((low,high))  # Relative offsets describe an unordered age window.
    if low is not None and high is not None and low >= high: raise ValueError('InvalidRecallInterval')
    sql = 'SELECT * FROM conversation_exchanges WHERE person_id=? AND session_id<>?'
    args = [who, session]
    if low is not None: sql += ' AND created_at>=?'; args.append(low)
    if high is not None: sql += ' AND created_at<?'; args.append(high)
    count = store.rows('SELECT count(*) AS n FROM ('+sql+')', args)[0]['n']
    rows = store.rows(sql+' ORDER BY created_at DESC,id DESC LIMIT 200', args)
    words = set((query or '').casefold().split())
    def relevance(row):
        text = (row['human_text']+' '+row['heard_text']).casefold()
        return sum(word in text for word in words), row['created_at']
    if words: rows.sort(key=relevance, reverse=True)
    selected = rows[:limit]
    for row in selected:
        row['at'] = stamp(row.pop('created_at'))
        row['text_truncated'] = len(row['human_text'])>600 or len(row['heard_text'])>600
        row['human_text'] = row['human_text'][:600]
        row['heard_text'] = row['heard_text'][:600]
    legacy = []
    if low is None and high is None:
        # Existing turns used a monotonic clock. Never invent calendar dates or
        # delivered replies for these pre-upgrade sources.
        legacy = store.rows("""SELECT id,session_id,text AS human_text FROM turns
            WHERE session_id<>? AND json_extract(evidence,'$.attribution.person_id')=?
            AND json_extract(evidence,'$.attribution.state')='supported'
            AND json_extract(evidence,'$.trusted')=1 AND json_extract(evidence,'$.echo_risk')=0
            AND NOT EXISTS(SELECT 1 FROM conversation_exchanges x WHERE x.source_turn=turns.id)
            ORDER BY rowid DESC LIMIT 8""",(session,who))
        for row in legacy:
            row['text_truncated'] = len(row['human_text'])>600
            row['human_text'] = row['human_text'][:600]
    return {'legacy_source_utterances':legacy, 'legacy_caveat':'Undated source-only archive: speaker attribution retained; audience and Rex delivery unavailable. Do not infer a shared episode or date.',
            'exchanges':selected, 'matching_interval_count':count,
            'selection_truncated':count>len(selected), 'searched_latest_limit':200,
            'scope':'this person only; prior sessions; completed audio phrases only',
            'coverage':'New exchange archive only. Older source-only turns lack wall-clock/delivery evidence; absence is not proof nothing happened.'}


def context(store, original, session, now):
    who = subject(original)
    result = {'person_id':who, 'now':stamp(now), 'local_now':datetime.fromtimestamp(now).astimezone().isoformat(), 'recent_exchanges':[], 'interests_and_facts':[], 'episodes':[]}
    if not who: return result
    result['recent_exchanges'] = recall(store,who,session,limit=4)['exchanges']
    result['interests_and_facts'] = store.claims(who)[:6]
    result['current_session_exchanges'] = store.rows('SELECT id,human_text,heard_text FROM conversation_exchanges WHERE person_id=? AND session_id=? ORDER BY created_at DESC LIMIT 3',(who,session))
    result['episodes'] = store.rows('''SELECT e.id,e.summary,e.source_exchange,x.human_text,x.heard_text
        FROM conversation_episodes e JOIN conversation_exchanges x ON x.id=e.source_exchange
        WHERE e.person_id=? ORDER BY e.created_at DESC LIMIT 4''',(who,))
    for row in result['current_session_exchanges'] + result['episodes']:
        row['text_truncated'] = len(row['human_text'])>600 or len(row['heard_text'])>600
        row['human_text'],row['heard_text'] = row['human_text'][:600],row['heard_text'][:600]
    result['coverage'] = 'Prior sessions are partial excerpts, not a complete day or exact greeting count.'
    return result


def record(store, turn_id, session, original, capture, transcript, delivery, now):
    who = subject(original)
    # Do not assign an uncertain voice to a face, or store ASR noise as dialogue.
    if not who: return
    if capture is not None:
        if (not transcript.trusted or capture.echo_risk or capture.truncated or capture.overlap is True or
                original.get('speaker',{}).get('person_id') != who or
                original.get('speaker',{}).get('state') != 'supported'): return
    heard = ' '.join(delivery.completed_text) if delivery.medium == 'audio' else ''
    human = transcript.text if capture is not None else ''
    if not human and not heard: return
    with store.transaction() as conn:
        conn.execute('INSERT OR IGNORE INTO conversation_exchanges VALUES(?,?,?,?,?,?,?,?,?)',
            (turn_id,session,who,turn_id if capture else None,now,human,heard,delivery.status,
             original.get('trigger','human_turn')))


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
