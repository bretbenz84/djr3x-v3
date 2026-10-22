"""October 21 owner requests: calendar arithmetic in code, weather for a named place, and Rex's own
power and room readings. Synthetic data, scripted providers and fake HTTP responses only."""
from datetime import datetime, timedelta, timezone
import json
import unittest
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from r3x.contracts import ToolCall, Transcript
from r3x.dates import answer
from r3x.observations import Cue
from r3x.output import TextOutput
from r3x.semantics import grounded_tools, validate
from r3x.session import Session
from r3x.settings import Settings
from r3x.systems import report, room
from r3x.weather import Weather
from tests.test_memory_core import MemoryFixture, observation

PACIFIC = timezone(timedelta(hours=-7), 'PDT')
NOW = datetime(2026, 10, 21, 15, 15, tzinfo=PACIFIC)   # A Wednesday.
FIELDS = ('op', 'date', 'other_date', 'amount', 'unit', 'weekday', 'nth', 'month', 'day', 'year', 'timezone')


def calendar(op, **fields):
    args = dict.fromkeys(FIELDS) | {'op': op} | fields
    validate('calendar', {'say_first': 'Hmm.', **args})
    return answer(args, NOW)


class CalendarTests(unittest.TestCase):
    def test_weekdays_and_day_counts_are_computed(self):
        christmas = calendar('describe', date='2026-12-25')['result']
        self.assertEqual((christmas['spoken'], christmas['days_from_today']), ('Friday, December 25, 2026', 65))
        until = calendar('between', other_date='2026-10-31')['result']
        self.assertEqual((until['days'], until['weeks_and_days']), (10, '1 week and 3 days'))
        age = calendar('between', date='1990-05-04', other_date='2026-10-21')['result']
        self.assertEqual((age['years'], age['months'], age['remaining_days']), (36, 5, 17))
        self.assertEqual(calendar('now')['year'], 2026)
        self.assertEqual(calendar('describe')['result']['relative'], 'today')

    def test_shifts_holidays_and_recurring_days(self):
        self.assertEqual(calendar('shift', date='2027-01-31', amount=1, unit='months')['result']['date'], '2027-02-28')
        self.assertEqual(calendar('shift', amount=-2, unit='weeks')['result']['date'], '2026-10-07')
        self.assertEqual(calendar('nth_weekday', year=2026, month=11, weekday='thursday', nth=4)['result']['date'], '2026-11-26')
        self.assertEqual(calendar('nth_weekday', year=2027, month=5, weekday='monday', nth=-1)['result']['date'], '2027-05-31')
        friday = calendar('next_weekday', weekday='friday')['result']
        self.assertEqual((friday['coming']['date'], friday['the_week_after']['date']), ('2026-10-23', '2026-10-30'))
        self.assertEqual(calendar('next_weekday', weekday='wednesday')['result']['coming']['date'], '2026-10-28')
        self.assertEqual(calendar('next_month_day', month=2, day=29)['result']['date'], '2028-02-29')
        self.assertEqual(calendar('next_month_day', month=10, day=21)['result']['relative'], 'today')
        self.assertEqual(calendar('easter', year=2027)['result']['date'], '2027-03-28')

    def test_other_time_zones(self):
        tokyo = calendar('time_in', timezone='Asia/Tokyo')['result']
        self.assertEqual((tokyo['local_time'], tokyo['day'], tokyo['hours_ahead_of_here']), ('7:15 AM', 'tomorrow there', 16.0))

    def test_bad_requests_are_rejected_not_guessed(self):
        with self.assertRaisesRegex(ValueError, 'CalendarFieldsMissing:other_date'):
            calendar('between')
        with self.assertRaisesRegex(ValueError, 'InvalidCalendarDate'):
            calendar('describe', date='Christmas')
        with self.assertRaisesRegex(ValueError, 'NoSuchWeekdayOccurrence'):
            calendar('nth_weekday', year=2026, month=2, weekday='monday', nth=5)
        with self.assertRaisesRegex(ValueError, 'UnknownTimeZone'):
            calendar('time_in', timezone='Mars/Olympus_Mons')
        with self.assertRaisesRegex(ValueError, 'InvalidCalendarNumber:month'):
            calendar('nth_weekday', year=2026, month=13, weekday='monday', nth=1)


def geocode_rows(name):
    if name != 'Sacramento':
        return []
    return [{'name': 'Sacramento', 'latitude': 38.58, 'longitude': -121.49, 'admin1': 'California', 'country': 'United States', 'country_code': 'US'},
            {'name': 'Sacramento', 'latitude': -19.86, 'longitude': -47.44, 'admin1': 'Minas Gerais', 'country': 'Brazil', 'country_code': 'BR'},
            {'name': 'Sacramento', 'latitude': 25.98, 'longitude': -101.42, 'admin1': 'Coahuila', 'country': 'Mexico', 'country_code': 'MX'}]


def forecast_payload():
    hours = [(datetime(2026, 10, 21, 15) + timedelta(hours=h)).isoformat(timespec='minutes') for h in range(13)]
    days = [(datetime(2026, 10, 21) + timedelta(days=d)).date().isoformat() for d in range(7)]
    return {'current_units': {'temperature_2m': '°F', 'wind_speed_10m': 'mp/h'},
            'current': {'time': '2026-10-21T15:15', 'temperature_2m': 78.4, 'apparent_temperature': 77.0,
                        'relative_humidity_2m': 31, 'precipitation': 0.0, 'weather_code': 1, 'wind_speed_10m': 6.2},
            'hourly': {'time': hours, 'temperature_2m': [78 - h for h in range(13)], 'weather_code': [1] * 13,
                       'precipitation_probability': [0] * 13},
            'daily': {'time': days, 'weather_code': [1, 3, 61, 63, 2, 0, 0], 'temperature_2m_max': [79, 74, 66, 64, 70, 75, 77],
                      'temperature_2m_min': [52, 54, 55, 53, 50, 49, 51], 'precipitation_probability_max': [0, 10, 70, 80, 20, 0, 0],
                      'precipitation_sum': [0, 0, 0.3, 0.5, 0, 0, 0], 'wind_speed_10m_max': [8, 9, 15, 17, 10, 7, 6],
                      'sunrise': [d + 'T07:22' for d in days], 'sunset': [d + 'T18:17' for d in days]}}


class FakeWeb:
    def __init__(self):
        self.requests = []

    def __call__(self, url):
        self.requests.append(url)
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        if parts.hostname == 'geocoding-api.open-meteo.com':
            return json.dumps({'results': geocode_rows(query['name'])}).encode()
        return json.dumps(forecast_payload()).encode()


class WeatherTests(unittest.TestCase):
    def setUp(self):
        self.web = FakeWeb()
        self.weather = Weather(Settings(), clock=lambda: 1_000_000., get=self.web)

    def test_a_named_city_and_state_is_found_and_forecast(self):
        result = self.weather.lookup('Sacramento', 'California', 'US')
        self.assertEqual(result['place'], 'Sacramento, California, United States')
        self.assertFalse(result['where_you_are'])
        self.assertIn('looked this up', result['meaning'])
        self.assertEqual((result['now']['temperature_f'], result['now']['conditions']), (78.4, 'mostly clear'))
        self.assertEqual(len(result['days']), 7)
        self.assertEqual(result['days'][2], {'day': 'Friday, October 23', 'conditions': 'light rain', 'high_f': 66, 'low_f': 55,
                                             'chance_of_rain_pct': 70, 'rain_in': 0.3, 'wind_max_mph': 15,
                                             'sunrise': '7:22 AM', 'sunset': '6:17 PM'})
        self.assertEqual([h['time'] for h in result['next_hours']], ['4 PM', '6 PM', '8 PM', '10 PM', '12 AM', '2 AM'])
        self.assertNotIn('other_places_with_this_name', result)
        geocode = parse_qs(urlsplit(self.web.requests[0]).query)
        self.assertEqual((geocode['name'], geocode['countryCode']), (['Sacramento'], ['US']))

    def test_a_bare_city_name_takes_the_best_match_and_names_the_others(self):
        result = self.weather.lookup('Sacramento')
        self.assertEqual(result['place'], 'Sacramento, California, United States')
        self.assertEqual(result['other_places_with_this_name'],
                         ['Sacramento, Minas Gerais, Brazil', 'Sacramento, Coahuila, Mexico'])

    def test_no_city_means_home_without_a_place_search_and_lookups_are_reused(self):
        result = self.weather.lookup(None)
        self.assertEqual((result['place'], result['where_you_are']), ('Sacramento, CA', True))
        self.assertEqual(len(self.web.requests), 1)
        self.weather.lookup(None)
        self.assertEqual(len(self.web.requests), 1)   # Ten-minute reuse.

    def test_an_unknown_place_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'PlaceNotFound'):
            self.weather.lookup('Sacramento', 'Ohio', 'US')
        with self.assertRaisesRegex(ValueError, 'PlaceNotFound'):
            self.weather.lookup('Atlantis')


class SystemsTests(unittest.TestCase):
    def test_the_inside_sensor_is_corrected_to_the_room(self):
        # October 21: 87 F inside Rex in a 72 F house. Same moisture in cooler air reads more humid.
        reading = room({'temp_c': (87 - 32) * 5 / 9, 'humidity_pct': 40, 'pressure_hpa': 1012.6}, -15.)
        self.assertEqual((reading['temperature_f'], reading['humidity_pct'], reading['pressure_hpa']), (72, 65, 1013))
        self.assertEqual(room({'temp_c': 30., 'humidity_pct': None}, -15.)['humidity_pct'], None)
        self.assertIsNone(room(None, -15.))

    def test_battery_readings_and_a_missing_controller(self):
        status = {'connected': True, 'reading_age_s': 0.4,
                  'battery': {'percent': 81, 'volts': 12.9, 'amps_drawn': -1.5, 'on_charger': True, 'percent_meaning': 'x'},
                  'controller': {'state': 'idle'}, 'tilt_deg': None, 'room': None}
        battery = report(status, -15.)['battery']
        self.assertEqual((battery['percent'], battery['watts'], battery['current_direction'], battery['on_charger']),
                         (81, -19.4, 'into the battery', True))
        for missing in (None, {'connected': False, 'readings': None}):
            self.assertEqual(report(missing, -15.)['battery'], None)
            self.assertIn('not reporting', report(missing, -15.)['meaning'])

    def test_the_offset_is_validated(self):
        self.assertEqual(Settings().room_temperature_offset_f, -15.)
        for bad in (float('nan'), 60, '−15'):
            with self.assertRaises(ValueError):
                Settings(room_temperature_offset_f=bad)


class LookupConversationTests(MemoryFixture):
    def turn(self, text, call, answer_text):
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                yield answer_text if snapshot.continuation else call
        brain = Brain()
        self.begin(text)
        session = Session('session', brain, TextOutput(self.state.trace), self.state.trace, self.state)
        self.addCleanup(session.close)
        result = session.process_turn(self.state.current[1], Transcript(text, 0, True), 0, observation())
        output = next(json.loads(r['output']) for r in brain.snapshots[-1].continuation
                      if r.get('type') == 'function_call_output')
        return brain, result, output

    def test_asked_the_weather_for_a_named_city_he_looks_it_up(self):
        self.state.weather = Weather(Settings(), get=FakeWeb())
        brain, result, output = self.turn('What is the weather in Sacramento, California?',
            ToolCall('w', 'weather', {'say_first': 'One sec.', 'city': 'Sacramento', 'region': 'California', 'country': 'US'}),
            'I just checked: 78 and mostly clear in Sacramento.')
        self.assertIn('weather', {d['name'] for d in grounded_tools(brain.snapshots[0].context)})
        self.assertEqual((output['outcome'], output['data']['place']), ('ok', 'Sacramento, California, United States'))
        self.assertEqual(result.completed_text, ['One sec.', 'I just checked: 78 and mostly clear in Sacramento.'])

    def test_a_calendar_question_is_answered_from_computed_dates(self):
        brain, result, output = self.turn('What day of the week is Christmas this year?',
            ToolCall('c', 'calendar', {'say_first': 'Hmm.', **dict.fromkeys(FIELDS), 'op': 'describe', 'date': '2026-12-25'}),
            'A Friday.')
        self.assertEqual(output['data']['result']['weekday'], 'Friday')
        self.assertEqual(result.completed_text, ['Hmm.', 'A Friday.'])

    def test_battery_and_room_questions_read_the_live_status(self):
        self.state.systems_status = lambda: report({'connected': True, 'reading_age_s': 0.2,
            'battery': {'percent': 64, 'volts': 12.7, 'amps_drawn': 1.1, 'on_charger': False},
            'controller': {'state': 'idle'}, 'tilt_deg': None,
            'room': {'temp_c': 30.56, 'humidity_pct': 40, 'pressure_hpa': 1012}}, -15.)
        brain, result, output = self.turn("What's your battery at, and how warm is it in here?",
            ToolCall('s', 'systems_status', {'say_first': 'Checking my gauges.'}),
            '64 percent, and about 72 in here.')
        self.assertEqual((output['data']['battery']['percent'], output['data']['room']['temperature_f']), (64, 72))
        self.assertEqual(result.completed_text, ['Checking my gauges.', '64 percent, and about 72 in here.'])

    def test_tools_are_offered_only_when_their_source_is_present(self):
        self.begin('Hello there.')
        names = {d['name'] for d in grounded_tools(self.state.context())}
        self.assertIn('calendar', names)
        self.assertFalse({'weather', 'systems_status'} & names)
        offered = {d['name'] for d in grounded_tools({**self.state.context(), 'weather_available': True,
                                                          'systems_status_available': True})}
        self.assertTrue({'weather', 'systems_status', 'calendar'} <= offered)
        self.assertFalse({'weather', 'calendar', 'systems_status'} & {d['name'] for d in grounded_tools({'trigger': 'conversation_lull'})})

    def test_a_lookup_failure_is_a_structured_result(self):
        def offline(url):
            raise OSError('network down')
        self.state.weather = Weather(Settings(), get=offline)
        self.begin('Will it rain tomorrow?')
        result = self.call('weather', {'say_first': 'Hmm.', 'city': None, 'region': None, 'country': None})
        self.assertEqual((result.outcome, result.reason), ('failed', 'OSError'))
        self.begin('What is the date in three days?')
        rejected = self.call('calendar', {'say_first': 'Hmm.', **dict.fromkeys(FIELDS), 'op': 'shift', 'amount': 3})
        self.assertEqual((rejected.outcome, rejected.reason), ('rejected', 'CalendarFieldsMissing:unit'))


class NewsListingTests(unittest.TestCase):
    def test_the_news_list_leaves_weather_to_the_weather_tool(self):
        from r3x.observations import PublicObservations
        source = PublicObservations(Settings(news_feed_urls=(), weather_enabled=False), clock=lambda: 1_000.)
        source.rows = {'news': (Cue('n', 'news', 'BBC News', 'https://example.test', 990., 5_000., 'A headline'),),
                       'weather': (Cue('w', 'weather', 'Open-Meteo', 'https://example.test', 990., 5_000., '{}'),)}
        self.assertEqual([r['kind'] for r in source.listing()], ['news'])


if __name__ == '__main__':
    unittest.main()
