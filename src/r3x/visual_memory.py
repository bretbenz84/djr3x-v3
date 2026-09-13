"""Bounded model-authored visual observations, never accepted biography or raw images."""
import json


def audience(context):
    return json.dumps(sorted(f['track_id'] for f in context.get('faces',[])))


def context(store, session_id, audience_key):
    rows = store.rows('SELECT * FROM visual_observations WHERE session_id=? AND audience=? AND status=? ORDER BY created_at DESC,rowid DESC LIMIT 8',
                      (session_id,audience_key,'observed'))
    for row in rows:
        row['source'] = json.loads(row['source'])
        row['spoken'] = json.loads(row['spoken'])
        row['meaning'] = 'Historical model interpretation, not current vision or verified personal fact. Do not infer a change from a new frame or rewording.'
    return rows


def correction_candidates(store, session_id, audience_key):
    """All bounded eligible records, including those outside the eight-item recall window."""
    rows = store.rows('SELECT id,description,uncertainty,source,created_at FROM visual_observations WHERE session_id=? AND audience=? AND status=? ORDER BY created_at DESC,rowid DESC LIMIT 32',
                      (session_id,audience_key,'observed'))
    for row in rows:
        source = json.loads(row.pop('source'))
        row['source'] = {key:source.get(key) for key in ('event_id','source_turn_id','frame_id','captured_at','direction','question','medium')}
    return rows


def save(store, session_id, audience_key, source, args, model, now):
    if source.get('status') not in {'simulated','observed'} or source.get('stage')!='complete' or not source.get('frame_id'):
        raise ValueError('VisualEvidenceUnavailable')
    if args['observation_id']!=source['request_id']:
        raise ValueError('VisualSourceMismatch')
    with store.transaction() as conn:
        conn.execute('INSERT INTO visual_observations(id,session_id,audience,source,description,uncertainty,model,created_at,status,spoken) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (source['request_id'],session_id,audience_key,json.dumps(source),args['description'],args['uncertainty'],model,now,'observed','[]'))
        conn.execute('DELETE FROM visual_observations WHERE id NOT IN (SELECT id FROM visual_observations ORDER BY created_at DESC,rowid DESC LIMIT 32)')
        revision=store.advance(conn)
    return revision


def delivered(store, observation_id, turn_id, text, now):
    with store.transaction() as conn:
        row=conn.execute('SELECT spoken FROM visual_observations WHERE id=? AND status=?',(observation_id,'observed')).fetchone()
        if row:
            entries=json.loads(row[0])
            entries.append({'turn_id':turn_id,'text':text,'at':now})
            conn.execute('UPDATE visual_observations SET spoken=? WHERE id=?',(json.dumps(entries[-8:]),observation_id))
            store.advance(conn)
