"""Dated, source-linked candidates. Language relevance remains with the conversation owner."""
from datetime import date


def validate_date(kind: str, value: str | None) -> None:
    if kind=='callback':
        if value is not None: raise ValueError('CallbackHasNoScheduledDate')
        return
    if value is None: raise ValueError('FollowupDateRequired')
    try:
        parsed=date.fromisoformat('2000-'+value if kind=='birthday' else value)
        if (parsed.strftime('%m-%d') if kind=='birthday' else parsed.isoformat())!=value:
            raise ValueError()
    except ValueError:
        raise ValueError('InvalidFollowupDate') from None


def occasion(kind: str, scheduled: str | None, today: date) -> str | None:
    if kind=='callback':return today.isoformat()
    if kind=='birthday':return today.isoformat() if today.strftime('%m-%d')==scheduled else None
    days=(date.fromisoformat(scheduled)-today).days
    if kind=='celebration':return scheduled if days==0 else None
    return scheduled if -7<=days<=7 else None


def candidates(store, faces, today: date, offset: int = 0, limit: int = 6, *, include_delivered: bool = False) -> list[dict]:
    visible={f.get('identity') for f in faces if f.get('identity')}
    if not visible:return []
    rows=store.rows('''SELECT f.*,c.subject_id,c.predicate,c.value,c.source_turn,c.said_by,
                      t.text AS source_text FROM memory_followups f
                      JOIN claims c ON c.id=f.claim_id JOIN turns t ON t.id=c.source_turn
                      JOIN people p ON p.id=c.subject_id
                      WHERE c.status='accepted' AND c.kind='self_report' AND c.said_by=c.subject_id
                      AND p.status='supported' ORDER BY c.revision DESC LIMIT 128''')
    result=[]
    for row in rows:
        if row['subject_id'] not in visible:continue
        if row['audience']=='subject_only' and (len(faces)!=1 or len(visible)!=1):continue
        key=occasion(row['kind'],row['on_date'],today)
        if key is None:continue
        if not include_delivered and store.rows('SELECT 1 FROM memory_followup_delivery WHERE claim_id=? AND occasion=?',
                      (row['claim_id'],key)):continue
        result.append({**row,'occasion':key})
    # Dated opportunities are time limited; callbacks rotate as receipts are delivered.
    ordered=sorted(result,key=lambda r:(r['kind']=='callback',r['on_date'] or '',r['claim_id']))
    if not ordered:return []
    start=offset % len(ordered)
    return (ordered[start:]+ordered[:start])[:limit]
