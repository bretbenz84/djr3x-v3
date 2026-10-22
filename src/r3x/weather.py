"""Weather looked up on request for any named place (October 21 owner request).

October 21 (7f6ce043): asked for the weather, then "Sacramento, California", Rex said he had no
report for Sacramento; news_today had cut the fetched report off the end of fourteen stories.
This lookup asks Open-Meteo (free, no key) for the place the person names, or Rex's home
location, and returns current conditions, the next hours and a seven-day forecast.
"""
from datetime import datetime
import json
import threading
import time
from urllib.parse import urlencode
from .contracts import ProviderFailure
from .providers import Capability

GEOCODE = 'https://geocoding-api.open-meteo.com/v1/search?'
FORECAST = 'https://api.open-meteo.com/v1/forecast?'
CONDITIONS = {0: 'clear', 1: 'mostly clear', 2: 'partly cloudy', 3: 'overcast', 45: 'fog', 48: 'freezing fog',
              51: 'light drizzle', 53: 'drizzle', 55: 'heavy drizzle', 56: 'freezing drizzle', 57: 'heavy freezing drizzle',
              61: 'light rain', 63: 'rain', 65: 'heavy rain', 66: 'freezing rain', 67: 'heavy freezing rain',
              71: 'light snow', 73: 'snow', 75: 'heavy snow', 77: 'snow grains', 80: 'light showers', 81: 'showers',
              82: 'violent showers', 85: 'snow showers', 86: 'heavy snow showers', 95: 'thunderstorms',
              96: 'thunderstorms with hail', 99: 'thunderstorms with heavy hail'}
FRESH_SECONDS = 600


def _conditions(code):
    return CONDITIONS.get(code, 'unknown') if isinstance(code, int) else 'unknown'


def _clock(stamp, minutes=False):
    return datetime.fromisoformat(stamp).strftime('%I:%M %p' if minutes else '%I %p').lstrip('0')


def _matches(wanted, *values):
    wanted = wanted.casefold().strip()
    return any(isinstance(v, str) and (v.casefold() == wanted or v.casefold().startswith(wanted)) for v in values)


def _label(row):
    return ', '.join(dict.fromkeys(p for p in (row.get('name'), row.get('admin1'), row.get('country')) if p))


def choose_place(rows, region=None, country=None):
    """The best geocoding row for the named place, and other places with the same name."""
    rows = [r for r in rows if isinstance(r.get('latitude'), (int, float)) and isinstance(r.get('longitude'), (int, float))]
    if country:
        rows = [r for r in rows if _matches(country, r.get('country_code'), r.get('country'))]
    if region:
        rows = [r for r in rows if _matches(region, r.get('admin1'), r.get('admin2'))]
    if not rows:
        raise ValueError('PlaceNotFound')
    best = rows[0]   # Open-Meteo ranks by relevance and population.
    # Same-named places elsewhere; the search also returns places matched by another name.
    others = [_label(r) for r in rows[1:] if r.get('name') == best.get('name')
              and (r.get('admin1'), r.get('country_code')) != (best.get('admin1'), best.get('country_code'))]
    return best, list(dict.fromkeys(others))[:3]


def forecast(payload, place, home, looked_up_at):
    """The model's view of an Open-Meteo forecast response (Fahrenheit, mph, inches, local times)."""
    current, hourly, daily = payload['current'], payload['hourly'], payload['daily']
    units = payload.get('current_units') or {}
    if units.get('temperature_2m') != '°F' or units.get('wind_speed_10m') != 'mp/h':
        raise ProviderFailure('UnexpectedWeatherUnits')
    now = datetime.fromisoformat(current['time'])
    hours = [{'time': _clock(t), 'temperature_f': temp, 'conditions': _conditions(code), 'chance_of_rain_pct': rain}
             for t, temp, code, rain in zip(hourly['time'], hourly['temperature_2m'], hourly['weather_code'],
                                            hourly['precipitation_probability'])
             if datetime.fromisoformat(t) > now][:12:2]
    days = []
    for i, stamp in enumerate(daily['time']):
        day = datetime.fromisoformat(stamp)
        days.append({'day': f"{day.strftime('%A, %B')} {day.day}", 'conditions': _conditions(daily['weather_code'][i]),
                     'high_f': daily['temperature_2m_max'][i], 'low_f': daily['temperature_2m_min'][i],
                     'chance_of_rain_pct': daily['precipitation_probability_max'][i],
                     'rain_in': daily['precipitation_sum'][i], 'wind_max_mph': daily['wind_speed_10m_max'][i],
                     'sunrise': _clock(daily['sunrise'][i], True), 'sunset': _clock(daily['sunset'][i], True)})
    return {'place': place, 'where_you_are': home, 'source': 'Open-Meteo', 'looked_up': looked_up_at,
            'local_time_there': now.strftime('%A %I:%M %p').replace(' 0', ' '),
            'now': {'conditions': _conditions(current['weather_code']), 'temperature_f': current['temperature_2m'],
                    'feels_like_f': current['apparent_temperature'], 'humidity_pct': current['relative_humidity_2m'],
                    'wind_mph': current['wind_speed_10m'], 'precipitation_in': current['precipitation']},
            'next_hours': hours, 'days': days,
            'meaning': 'You just looked this up online. Say so naturally ("I looked it up", "the forecast says"); '
                       'it is not something you sense yourself. days[0] is today there. Round numbers the way people '
                       'say them and give only what they asked about.'}


class Weather:
    """On-request weather lookups; one HTTPS client, short deadlines, ten-minute reuse per place."""

    def __init__(self, settings, clock=time.time, get=None):
        self.settings, self.clock = settings, clock
        self.capability = Capability('weather_lookup', 'Open-Meteo geocoding + forecast',
            'weather and forecasts for a named place when someone asks', None, ('httpx',),
            ('two HTTPS requests per new place; 3 s timeout each; <=1 MB per response; 10-minute reuse',), required=False)
        self.client = None
        self._get = get or self._http_get
        self.lock = threading.Lock()
        self.places, self.reports = {}, {}

    def start(self):
        if self._get == self._http_get:
            import httpx
            self.client = httpx.Client(timeout=httpx.Timeout(3), follow_redirects=False)

    def cancel(self):
        pass

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    def _http_get(self, url):
        if self.client is None:
            raise ProviderFailure('WeatherNotStarted')
        response = self.client.get(url, headers={'User-Agent': 'DJ-R3X-v3/1.0'})
        response.raise_for_status()
        if len(response.content) > 1_000_000:
            raise ProviderFailure('WeatherResponseLimit')
        return response.content

    def lookup(self, city, region=None, country=None, cancel=None):
        home = not city
        if home:
            place, lat, lon, others = (self.settings.weather_location, self.settings.weather_latitude,
                                       self.settings.weather_longitude, [])
        else:
            key = (city.casefold().strip(), (region or '').casefold().strip(), (country or '').casefold().strip())
            with self.lock:
                found = self.places.get(key)
            if found is None:
                query = {'name': city.strip(), 'count': 10, 'language': 'en', 'format': 'json'}
                if country and len(country.strip()) == 2:
                    query['countryCode'] = country.strip().upper()
                rows = json.loads(self._get(GEOCODE + urlencode(query))).get('results') or []
                best, others = choose_place(rows, region, country)
                found = (_label(best), best['latitude'], best['longitude'], others)
                with self.lock:
                    self.places[key] = found
            place, lat, lon, others = found
            if cancel:
                cancel.check()
        now = self.clock()
        with self.lock:
            cached = self.reports.get((lat, lon))
        if cached and now - cached[0] < FRESH_SECONDS:
            payload = cached[1]
        else:
            payload = json.loads(self._get(FORECAST + urlencode(dict(
                latitude=lat, longitude=lon, timezone='auto', forecast_days=7, forecast_hours=13,
                current='temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,weather_code,wind_speed_10m',
                hourly='temperature_2m,precipitation_probability,weather_code',
                daily='weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,'
                      'precipitation_sum,wind_speed_10m_max,sunrise,sunset',
                temperature_unit='fahrenheit', wind_speed_unit='mph', precipitation_unit='inch'))))
            with self.lock:
                self.reports[(lat, lon)] = (now, payload)
        report = forecast(payload, place, home, datetime.fromtimestamp(now).strftime('%I:%M %p').lstrip('0'))
        if others:
            report['other_places_with_this_name'] = others
        return report
