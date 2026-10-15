"""Dated, source-linked candidates. Language relevance remains with the conversation owner."""
from datetime import date, datetime, time


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


# A follow-up with a time is offered in the week before it (once) and the week after it (once).
AFTER_SECONDS = 7*86400


def resolve_due(kind: str, on_date: str | None, due_in_minutes, due_by: str | None, now: float, today: date) -> float | None:
    """The wall-clock moment a plan's time has come, or None when nobody gave one.

    The model names the time when it is said ("in five minutes" is 5 minutes, "this weekend" is
    Sunday's date); code does the clock. A date alone means the end of that day."""
    if due_in_minutes is not None and due_by is not None:raise ValueError('FollowupDueTwice')
    if kind in {'birthday','celebration'}:
        if due_in_minutes is not None or due_by is not None:raise ValueError('FollowupDueNotForOccasions')
        return None
    if due_in_minutes is not None:
        if type(due_in_minutes) is not int or not 1<=due_in_minutes<=30*1440:raise ValueError('InvalidFollowupDueMinutes')
        return now+60*due_in_minutes
    if due_by is None:
        return end_of_day(date.fromisoformat(on_date)) if on_date else None
    try:
        if len(due_by)==10:
            day=date.fromisoformat(due_by)
            if day.isoformat()!=due_by:raise ValueError()
            if day<today:raise ValueError('FollowupDueInPast')
            return end_of_day(day)
        moment=datetime.fromisoformat(due_by)
        if moment.tzinfo is not None or moment.strftime('%Y-%m-%dT%H:%M')!=due_by:raise ValueError()
    except ValueError as exc:
        if str(exc)=='FollowupDueInPast':raise
        raise ValueError('InvalidFollowupDue') from None
    if moment.timestamp()<now-3600:raise ValueError('FollowupDueInPast')
    return moment.timestamp()


def end_of_day(day: date) -> float:
    return datetime.combine(day, time(23,59)).timestamp()


def occasion(kind: str, scheduled: str | None, today: date, due_at: float | None = None, now: float = 0.) -> str | None:
    if due_at is not None:
        # Once before its time (looking forward to it), once after (asking how it went).
        if now<due_at:return 'before_due' if (date.fromtimestamp(due_at)-today).days<=7 else None
        return 'after_due' if now-due_at<=AFTER_SECONDS else None
    if kind=='callback':return today.isoformat()
    if kind=='birthday':return today.isoformat() if today.strftime('%m-%d')==scheduled else None
    days=(date.fromisoformat(scheduled)-today).days
    if kind=='celebration':return scheduled if days==0 else None
    return scheduled if -7<=days<=7 else None


def clock(at: float) -> str:
    return datetime.fromtimestamp(at).strftime('%A %-I:%M %p').replace('AM','a.m.').replace('PM','p.m.')


def span(seconds: float) -> str:
    minutes, hours, days = round(seconds/60), round(seconds/3600), round(seconds/86400)
    return ('under a minute' if seconds<45 else 'about a minute' if seconds<90 else
            f'about {minutes} minutes' if seconds<3300 else 'about an hour' if seconds<5400 else
            f'about {hours} hours' if seconds<129600 else f'{days} days')


def timing(row: dict, now: float) -> dict:
    """When it was said and whether its time has passed, computed here so the model never does
    clock arithmetic (October 14: "in five minutes" was teased as still ahead after it passed)."""
    said, due = row['said_at'], row['due_at']
    result = {'said': span(now-said)+' ago ('+clock(said)+')' if said is not None else
                      'unknown: saved before Rex kept the time'}
    if due is None:
        result['moment'] = 'no_time_given' if row['kind'] in {'callback','plan'} else 'occasion_today'
    elif now>=due:
        result['moment'] = 'passed'
        result['due'] = ('its planned time was '+span(now-due)+' ago ('+clock(due)+'); '
                         'whether it happened is unknown')
    else:
        result['moment'] = 'ahead'
        result['due'] = 'not yet: due in '+span(due-now)+' ('+clock(due)+')'
    return result


def candidates(store, faces, today: date, offset: int = 0, limit: int = 6, *, now: float,
               include_delivered: bool = False) -> list[dict]:
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
        key=occasion(row['kind'],row['on_date'],today,row['due_at'],now)
        if key is None:continue
        if not include_delivered and store.rows('SELECT 1 FROM memory_followup_delivery WHERE claim_id=? AND occasion=?',
                      (row['claim_id'],key)):continue
        result.append({**row,'occasion':key})
    # Due first: a plan whose time just passed (most recent first), then today's birthdays and
    # celebrations. Those always lead; the rest rotate as receipts are delivered.
    def rank(r):
        if r['due_at'] is not None and now>=r['due_at']:return (0,-r['due_at'])
        if r['kind'] in {'birthday','celebration'}:return (1,0.)
        if r['due_at'] is not None:return (2,r['due_at'])
        return (3,r['on_date'] or '',-(r['said_at'] or float('-inf')))
    ordered=sorted(result,key=lambda r:(rank(r),r['claim_id']))
    due=[r for r in ordered if rank(r)[0]<2]
    rest=[r for r in ordered if rank(r)[0]>=2]
    if rest:
        start=offset % len(rest)
        rest=rest[start:]+rest[:start]
    chosen=(due+rest)[:limit]
    for row in chosen:
        row.update(timing(row,now))
        del row['said_at'],row['due_at']
    return chosen
