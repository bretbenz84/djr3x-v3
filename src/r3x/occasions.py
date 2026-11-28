"""Holidays and weekends near enough that a friend asks about plans (owner, November 27).

Owner: "Questions about upcoming events like this shouldn't just be lull speak, they should drive the
conversation when they are near the way a friend might ask a week before thanksgiving 'any plans for
the big turkey day?' and then after it's passed, he should ask how it went if plans were shared."
V2 asked about the big holidays up to 30 days ahead and the weekend Thursday to Sunday; the owner chose
about 10 days ahead for the big ones and 5 for the smaller ones.

Code keeps the calendar (US, computed here, no network) and which plans question is due; the one
conversation owner words it as a curiosity topic. A plan they share is saved with remember_followup
under its date, so the existing follow-up asks how it went in the week after. No model, no device.
"""
from datetime import date, datetime, timedelta

from .dates import easter, nth_weekday
from .field_guide import GUIDE

ASK_AHEAD = {'big': 10, 'small': 5}   # Days before the holiday its plans question may be asked.
AWARE_AHEAD = 30                     # The picture of the present names holidays this close.
AWARE_AFTER = 3                      # ... and ones that passed this recently.
WEEKEND_ASK_DAYS = (3, 4, 5, 6)      # Thursday to Sunday (Monday is 0), Sunday only before noon.
WEEKEND_SUNDAY_UNTIL_HOUR = 12
HOLIDAY_QUIET_DAYS = 6               # No weekend question this soon after a holiday plans question.
PREFIX = 'plans_'
WEEKEND = PREFIX + 'weekend_'


def holidays(year):
    """(key, name, day, ask) for the year. ask is big or small for a plans question, None for one Rex
    knows about without asking anyone's plans."""
    return [
        ('new_years_day', "New Year's Day", date(year, 1, 1), None),
        ('mlk_day', 'Martin Luther King Jr. Day', nth_weekday(year, 1, 0, 3), None),
        ('valentines_day', "Valentine's Day", date(year, 2, 14), None),
        ('presidents_day', "Presidents' Day", nth_weekday(year, 2, 0, 3), None),
        ('st_patricks_day', "St. Patrick's Day", date(year, 3, 17), None),
        ('easter', 'Easter', easter(year), 'small'),
        ('star_wars_day', 'Star Wars Day (May the Fourth)', date(year, 5, 4), None),
        ('mothers_day', "Mother's Day", nth_weekday(year, 5, 6, 2), None),
        ('memorial_day', 'Memorial Day', nth_weekday(year, 5, 0, -1), 'small'),
        ('fathers_day', "Father's Day", nth_weekday(year, 6, 6, 3), None),
        ('juneteenth', 'Juneteenth', date(year, 6, 19), None),
        ('independence_day', 'the Fourth of July', date(year, 7, 4), 'small'),
        ('labor_day', 'Labor Day', nth_weekday(year, 9, 0, 1), 'small'),
        ('columbus_day', "Columbus Day (Indigenous Peoples' Day)", nth_weekday(year, 10, 0, 2), None),
        ('halloween', 'Halloween', date(year, 10, 31), 'small'),
        ('veterans_day', 'Veterans Day', date(year, 11, 11), None),
        ('thanksgiving', 'Thanksgiving', nth_weekday(year, 11, 3, 4), 'big'),
        ('christmas_eve', 'Christmas Eve', date(year, 12, 24), None),
        ('christmas', 'Christmas', date(year, 12, 25), 'big'),
        ('new_years_eve', "New Year's Eve", date(year, 12, 31), 'big'),
    ]


def _around(today):
    return sorted((row for year in (today.year - 1, today.year, today.year + 1) for row in holidays(year)),
                  key=lambda row: row[2])


def _relative(day, today):
    offset = (day - today).days
    when = f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}"
    if offset == 0: return f'is today ({when})'
    if offset == 1: return f'is tomorrow ({when})'
    if offset == -1: return f'was yesterday ({when})'
    return f'is in {offset} days ({when})' if offset > 0 else f'was {-offset} days ago ({when})'


def calendar_note(today):
    """Holidays near today for the picture of the present, or None: the recent past, today, and the
    next two within AWARE_AHEAD days."""
    passed, ahead = [], []
    for _, name, day, _ in _around(today):
        offset = (day - today).days
        if -AWARE_AFTER <= offset <= 0:
            passed.append(f'{name} {_relative(day, today)}')
        elif 0 < offset <= AWARE_AHEAD and len(ahead) < 2:
            ahead.append(f'{name} {_relative(day, today)}')
    lines = passed + ahead
    return {'holidays': lines, 'meaning': GUIDE['right_now.calendar']} if lines else None


def _known_near(plan_days, low, high):
    return any(low <= day <= high for day in plan_days)


def plans_question(now, asked, plan_days, name):
    """The one plans question due for this person now, as a curiosity row, or None.

    now is the local wall-clock datetime; asked maps this person's curiosity topic ids to when he was
    heard asking them; plan_days are the dates of plans already saved for them (then the saved plan's
    own follow-up covers it). A plans question once heard is spent for good: its id carries the date."""
    today = now.date()
    for key, holiday, day, ask in _around(today):
        offset = (day - today).days
        if ask is None or not 0 <= offset <= ASK_AHEAD[ask]:
            continue
        topic = f'{PREFIX}{key}_{day.year}'
        if topic in asked or _known_near(plan_days, day - timedelta(days=1), day + timedelta(days=1)):
            continue
        return {'topic': topic, 'about': name, 'want_to_know': f'what they have planned for {holiday}',
                'soon': f'{holiday} {_relative(day, today)}',
                'when_they_answer': (f'A plan they share for it: save it with remember_followup kind plan, on_date '
                                     f'{day.isoformat()} (or the day they name), so you ask how it went afterwards. '
                                     'Nothing planned: react, with nothing to save.')}
    weekday = today.weekday()
    if weekday not in WEEKEND_ASK_DAYS or (weekday == 6 and now.hour >= WEEKEND_SUNDAY_UNTIL_HOUR):
        return None
    sunday = today + timedelta(days=6 - weekday)
    friday, saturday, monday = sunday - timedelta(days=2), sunday - timedelta(days=1), sunday + timedelta(days=1)
    topic = f'{WEEKEND}{sunday.isoformat()}'
    if topic in asked or _known_near(plan_days, friday, sunday):
        return None
    # A holiday that makes this its long weekend, or a holiday plans question this week, covers it.
    if any(ask and friday - timedelta(days=1) <= day <= monday for _, _, day, ask in _around(today)):
        return None
    cutoff = now.timestamp() - HOLIDAY_QUIET_DAYS * 86400
    if any(t.startswith(PREFIX) and not t.startswith(WEEKEND) and at >= cutoff for t, at in asked.items()):
        return None
    rest = weekday >= 5
    return {'topic': topic, 'about': name,
            'want_to_know': 'what they have going on for the rest of the weekend' if rest
                            else 'what they have going on this weekend',
            'soon': ('it is the weekend now' if rest else 'the weekend is coming') +
                    f" (Saturday {saturday.strftime('%B')} {saturday.day} and Sunday {sunday.strftime('%B')} {sunday.day})",
            'when_they_answer': ('A plan they share: save it with remember_followup kind plan, on_date the day they '
                                 f'name (Sunday, {sunday.isoformat()}, when they just say the weekend), so you ask how '
                                 'it went afterwards. Nothing planned: react, with nothing to save.')}


def plan_days(store, person_id):
    """Dates of the plans already saved for this person, told by them or about them."""
    rows = store.rows("""SELECT f.on_date, f.due_at FROM memory_followups f JOIN claims c ON c.id=f.claim_id
        WHERE c.status='accepted' AND f.kind IN ('plan','callback') AND (c.said_by=? OR c.subject_id=?)""",
                      (person_id, person_id))
    days = set()
    for row in rows:
        if row['on_date']:
            try: days.add(date.fromisoformat(row['on_date']))
            except ValueError: pass
        elif row['due_at'] is not None:
            days.add(datetime.fromtimestamp(row['due_at']).date())
    return days
