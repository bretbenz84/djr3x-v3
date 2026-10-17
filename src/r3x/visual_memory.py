"""Bounded model-authored visual observations, never accepted biography or raw images."""
from .field_guide import GUIDE
import json


def audience(context):
    return json.dumps(sorted({('person:'+f['identity']) if f.get('identity') else f['track_id']
                              for f in context.get('faces',[])}))


def context(store, session_id, audience_key):
    rows = store.rows('SELECT * FROM visual_observations WHERE session_id=? AND (audience=? OR audience=?) AND status=? ORDER BY created_at DESC,rowid DESC LIMIT 8',
                      (session_id,audience_key,'general','observed'))
    for row in rows:
        row['source'] = json.loads(row['source'])
        row['heard_remark_count'] = len(json.loads(row['spoken']))
        row['spoken'] = [entry for entry in json.loads(row['spoken']) if row['audience']!='general' or entry.get('audience')==audience_key or entry.get('general') is True]
        if row['audience']=='general':
            row['source']['corrections'] = [{k:v for k,v in c.items() if k!='source_text'} for c in row['source'].get('corrections',[])]
        if 'corrections' in row['source']:
            row['source']['corrections'] = [{k:v for k,v in c.items() if k not in {'previous_description','previous_uncertainty'}} for c in row['source']['corrections']]
        row['meaning'] = GUIDE['visual_memory (each row)']
    return rows


def correction_candidates(store, session_id, audience_key):
    """All bounded eligible records, including those outside the eight-item recall window."""
    rows = store.rows('SELECT id,description,uncertainty,source,created_at FROM visual_observations WHERE session_id=? AND (audience=? OR audience=?) AND status=? ORDER BY created_at DESC,rowid DESC LIMIT 32',
                      (session_id,audience_key,'general','observed'))
    for row in rows:
        source = json.loads(row.pop('source'))
        row['source'] = {key:source.get(key) for key in ('event_id','source_turn_id','frame_id','captured_at','direction','question','medium','interpretation_revision')}
    return rows


def save(store, session_id, audience_key, source, args, model, now):
    if source.get('status') not in {'simulated','observed'} or source.get('stage')!='complete' or not source.get('frame_id'):
        raise ValueError('VisualEvidenceUnavailable')
    if args['observation_id']!=source['request_id']:
        raise ValueError('VisualSourceMismatch')
    if args.get('description_scope')=='general':
        audience_key='general'
        # General history cannot leak the personal question or source transcript via metadata.
        source={k:v for k,v in source.items() if k not in {'question','camera_continuity'}}
    with store.transaction() as conn:
        conn.execute('INSERT INTO visual_observations(id,session_id,audience,source,description,uncertainty,model,created_at,status,spoken) VALUES(?,?,?,?,?,?,?,?,?,?)',
            (source['request_id'],session_id,audience_key,json.dumps(source),args['description'],args['uncertainty'],model,now,'observed','[]'))
        conn.execute('DELETE FROM visual_observations WHERE id NOT IN (SELECT id FROM visual_observations ORDER BY created_at DESC,rowid DESC LIMIT 32)')
        revision=store.advance(conn)
    return revision


def delivered(store, observation_id, turn_id, text, now, audience_key=None, general=False):
    with store.transaction() as conn:
        row=conn.execute('SELECT spoken,source FROM visual_observations WHERE id=? AND status=?',(observation_id,'observed')).fetchone()
        if row:
            entries=json.loads(row[0])
            entries.append({'turn_id':turn_id,'text':text,'at':now,'audience':audience_key,'general':general,'interpretation_revision':json.loads(row[1]).get('interpretation_revision',0)})
            conn.execute('UPDATE visual_observations SET spoken=? WHERE id=?',(json.dumps(entries[-8:]),observation_id))
            store.advance(conn)


def revise(conn, entries, source_turn_id, source_text, now):
    """Called within the state owner's validated transaction; never infer edits."""
    results=[]
    for entry in entries:
        row=conn.execute('SELECT description,uncertainty,source FROM visual_observations WHERE id=?',(entry['observation_id'],)).fetchone()
        source=json.loads(row['source'])
        version=source.get('interpretation_revision',0)+1
        correction={'source_turn_id':source_turn_id,'source_text':source_text[:500],'at':now,
                    'interpretation_revision':version,'previous_description':row['description'],
                    'previous_uncertainty':row['uncertainty']}
        source['corrections']=(source.get('corrections',[])+[correction])[-8:]
        source['interpretation_revision']=version
        conn.execute('UPDATE visual_observations SET description=?,uncertainty=?,source=? WHERE id=?',
                     (entry['description'],entry['uncertainty'],json.dumps(source),entry['observation_id']))
        results.append({'observation_id':entry['observation_id'],'status':'revised','interpretation_revision':version,
                        'description':entry['description'],'uncertainty':entry['uncertainty']})
    return results


def novelty(records, images=()):
    """Evidence for model judgment, not a semantic novelty score or speaking trigger."""
    from hashlib import sha256
    digests={sha256(i.data_url.encode()).hexdigest() for i in images}
    return {'meaning':GUIDE['visual_novelty'],
            'observations':[{'observation_id':r['id'],'interpretation_revision':r['source'].get('interpretation_revision',0),
                'image_relation':'identical_supplied_image' if r['source'].get('image_digest') in digests else 'not_established',
                'heard_remarks':r['spoken'], 'has_heard_remark':bool(r.get('heard_remark_count',len(r['spoken'])))} for r in records]}
