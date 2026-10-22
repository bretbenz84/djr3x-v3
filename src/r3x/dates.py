"""Calendar and clock arithmetic for the calendar tool (October 21 owner request).

The model names the operation and the dates it means ("Christmas" is 2026-12-25, "Thanksgiving"
is the fourth Thursday of November); weekdays, day counts, month lengths, leap years and time
zones are computed here, never guessed by the model.
"""
from datetime import date, datetime, timedelta
import calendar

WEEKDAYS = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
OPERATIONS = ('now', 'describe', 'between', 'shift', 'nth_weekday', 'next_weekday', 'next_month_day', 'easter', 'time_in')
UNITS = ('days', 'weeks', 'months', 'years')


def spoken(day):
    return f"{day.strftime('%A, %B')} {day.day}, {day.year}"


def _parse(value, field):
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError('InvalidCalendarDate:' + field) from None


def _span(days):
    days = abs(days)
    weeks, rest = divmod(days, 7)
    if not weeks:
        return f'{days} day' + ('s' if days != 1 else '')
    text = f'{weeks} week' + ('s' if weeks != 1 else '')
    return text + (f' and {rest} day' + ('s' if rest != 1 else '') if rest else '')


def describe(day, today):
    offset = (day - today).days
    return {'date': day.isoformat(), 'spoken': spoken(day), 'weekday': day.strftime('%A'),
            'days_from_today': offset,
            'relative': 'today' if offset == 0 else 'tomorrow' if offset == 1 else 'yesterday' if offset == -1
                        else f'in {offset} days ({_span(offset)})' if offset > 0 else f'{-offset} days ago ({_span(offset)})',
            'day_of_year': day.timetuple().tm_yday, 'iso_week': day.isocalendar()[1],
            'days_in_month': calendar.monthrange(day.year, day.month)[1], 'leap_year': calendar.isleap(day.year)}


def add_months(day, months):
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    if not 1 <= year <= 9999:
        raise ValueError('CalendarDateOutOfRange')
    return date(year, month + 1, min(day.day, calendar.monthrange(year, month + 1)[1]))


def between(start, end):
    """Whole years, months and days from start to end (end may be earlier)."""
    sign, (a, b) = (1, (start, end)) if end >= start else (-1, (end, start))
    months = (b.year - a.year) * 12 + b.month - a.month
    if add_months(a, months) > b:
        months -= 1
    rest = (b - add_months(a, months)).days
    total = (end - start).days
    return {'from': spoken(start), 'to': spoken(end), 'days': total,
            'weeks_and_days': _span(total), 'years': sign * (months // 12), 'months': sign * (months % 12),
            'remaining_days': sign * rest, 'end_is_before_start': sign < 0}


def nth_weekday(year, month, weekday, nth):
    days = [d for d in calendar.Calendar().itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
    if nth == -1:
        return days[-1]
    if not 1 <= nth <= len(days):
        raise ValueError('NoSuchWeekdayOccurrence')
    return days[nth - 1]


def easter(year):
    """Western (Gregorian) Easter Sunday, anonymous Gregorian algorithm."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def answer(args, now):
    """The calendar tool's result. now is the robot's local wall-clock datetime (aware)."""
    op = args['op']
    today = now.date()

    def need(*keys):
        missing = [k for k in keys if args.get(k) is None]
        if missing:
            raise ValueError('CalendarFieldsMissing:' + ','.join(missing))
        return [args[k] for k in keys]

    result = {'today': spoken(today)}
    if op == 'now':
        offset = now.utcoffset() or timedelta()
        hours = offset.total_seconds() / 3600
        result.update(local_time=now.strftime('%I:%M %p').lstrip('0'), date=today.isoformat(),
                      time_zone=now.tzname(), utc_offset_hours=hours, year=today.year)
    elif op == 'describe':
        result['result'] = describe(_parse(args.get('date'), 'date') or today, today)
    elif op == 'between':
        start = _parse(args.get('date'), 'date') or today
        result['result'] = between(start, _parse(need('other_date')[0], 'other_date'))
    elif op == 'shift':
        amount, unit = need('amount', 'unit')
        start = _parse(args.get('date'), 'date') or today
        try:
            moved = (start + timedelta(days=amount * (7 if unit == 'weeks' else 1)) if unit in {'days', 'weeks'}
                     else add_months(start, amount * (12 if unit == 'years' else 1)))
        except OverflowError:
            raise ValueError('CalendarDateOutOfRange') from None
        result['result'] = {'start': spoken(start), **describe(moved, today)}
    elif op == 'nth_weekday':
        year, month, weekday, nth = need('year', 'month', 'weekday', 'nth')
        result['result'] = describe(nth_weekday(year, month, WEEKDAYS.index(weekday), nth), today)
    elif op == 'next_weekday':
        weekday = WEEKDAYS.index(need('weekday')[0])
        start = _parse(args.get('date'), 'date') or today
        ahead = (weekday - start.weekday()) % 7 or 7
        first = start + timedelta(days=ahead)
        # "Next Friday" is ambiguous in English: the coming one and the one after are both given.
        result['result'] = {'coming': describe(first, today), 'the_week_after': describe(first + timedelta(days=7), today),
                            'meaning': 'coming is the first one after the start date; some people mean the_week_after by "next".'}
    elif op == 'next_month_day':
        month, day = need('month', 'day')
        start = _parse(args.get('date'), 'date') or today
        year = start.year
        for _ in range(9):
            if day <= calendar.monthrange(year, month)[1] and date(year, month, day) >= start:
                break
            year += 1
        else:
            raise ValueError('NoSuchCalendarDay')
        result['result'] = describe(date(year, month, day), today)
    elif op == 'easter':
        result['result'] = describe(easter(args.get('year') or today.year), today)
    elif op == 'time_in':
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        zone = need('timezone')[0]
        try:
            there = now.astimezone(ZoneInfo(zone))
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('UnknownTimeZone') from None
        ahead = ((there.utcoffset() or timedelta()) - (now.utcoffset() or timedelta())).total_seconds() / 3600
        day = (there.date() - today).days
        result['result'] = {'time_zone': args['timezone'], 'local_time': there.strftime('%I:%M %p').lstrip('0'),
                            'date': spoken(there.date()),
                            'day': 'same day' if day == 0 else 'tomorrow there' if day > 0 else 'yesterday there',
                            'hours_ahead_of_here': ahead}
    else:
        raise ValueError('UnknownCalendarOperation')
    return result
