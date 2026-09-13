"""Bounded public observations: provenance, freshness and offers, never speech."""
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from html.parser import HTMLParser
import json
import math
import threading
import time
from urllib.parse import urlsplit, urlencode
from xml.etree import ElementTree as ET
from .contracts import ProviderFailure
from .providers import Capability


def https_url(value):
    if not isinstance(value,str) or len(value)>2048 or any(ord(c)<32 for c in value):raise ValueError('InvalidPublicSourceURL')
    p=urlsplit(value)
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.fragment:
        raise ValueError('PublicSourceRequiresHTTPS')
    return value


class Plain(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,data):self.parts.append(data)
    @classmethod
    def text(cls,value):
        parser=cls();parser.feed(value)
        return ' '.join(' '.join(parser.parts).split())[:500]


@dataclass(frozen=True)
class Cue:
    cue_id: str
    kind: str
    source: str
    source_url: str
    published_at: float
    expires_at: float
    text: str

    def public(self):return {**asdict(self),'published_utc':datetime.fromtimestamp(self.published_at,timezone.utc).isoformat()}


def news_items(data: bytes, source_url: str, now: float) -> tuple[Cue,...]:
    if len(data)>1_000_000 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('InvalidBoundedFeed')
    root=ET.fromstring(data)
    source=Plain.text(root.findtext('./channel/title') or urlsplit(source_url).hostname)[:120]
    rows=[]
    for item in root.findall('.//item')[:40]:
        try:
            title=Plain.text(item.findtext('title') or '')
            link=https_url(item.findtext('link') or '')
            published=parsedate_to_datetime(item.findtext('pubDate') or '')
            if published.tzinfo is None:continue
            at=published.timestamp()
            if not title or not -300<=now-at<=48*3600:continue
            summary=Plain.text(item.findtext('description') or '')
            key='news:'+sha256(link.encode()).hexdigest()[:24]
            rows.append(Cue(key,'news',source,link,at,min(now+6*3600,at+48*3600),
                            (title+'. '+summary)[:650]))
        except (ValueError,TypeError,OverflowError):continue
    return tuple(dict((r.cue_id,r) for r in rows).values())[:16]


def weather_item(data: bytes, location: str, source_url: str, now: float, previous: Cue | None = None) -> Cue:
    payload=json.loads(data);current=payload['current'];units=payload['current_units']
    at=float(current['time'])
    if not -300<=now-at<=1800:raise ValueError('StaleWeather')
    fields=('temperature_2m','precipitation','weather_code','wind_speed_10m')
    values={k:current[k] for k in fields}
    if not all(type(v) in (float,int) and math.isfinite(v) for v in values.values()):raise ValueError('InvalidWeatherValues')
    if units.get('temperature_2m')!='°F' or units.get('wind_speed_10m')!='mp/h' or units.get('precipitation')!='inch':
        raise ValueError('UnexpectedWeatherUnits')
    # Coarse conditions, not the provider's refresh timestamp, define repetition.
    signature=(datetime.fromtimestamp(at,timezone.utc).date().isoformat(),round(values['temperature_2m']/5),
               values['weather_code'],values['precipitation']>0,round(values['wind_speed_10m']/5))
    key='weather:'+sha256((location+repr(signature)).encode()).hexdigest()[:24]
    context={'location':location,'current_conditions':values,'units':{k:units[k] for k in fields}}
    if previous and 0<at-previous.published_at<=86400:
        context['previous_conditions']=json.loads(previous.text)['current_conditions']
        context['previous_reported_utc']=datetime.fromtimestamp(previous.published_at,timezone.utc).isoformat()
    text=json.dumps(context,ensure_ascii=False)
    return Cue(key,'weather','Open-Meteo',source_url,at,min(now+1800,at+1800),text)


class PublicObservations:
    """One optional I/O worker; deadlines and byte limits bound refresh work."""
    def __init__(self, settings, trace=None, clock=time.time):
        self.settings,self.trace,self.clock=settings,trace,clock
        self.capability=Capability('public_observations','RSS + Open-Meteo','dated top headlines and local weather',
            None,('httpx',),('one network worker; refresh >=15 minutes; <=1 MB per response',),required=False)
        self.lock=threading.RLock();self.stop=threading.Event();self.worker=None;self.client=None
        self.rows={};self.offered={};self.cursor=0

    def start(self):
        import httpx
        self.client=httpx.Client(timeout=httpx.Timeout(3),follow_redirects=False)
        self.worker=threading.Thread(target=self._run,name='r3x-public-observations',daemon=True);self.worker.start()

    def _get(self,url):
        deadline=time.monotonic()+8;body=bytearray()
        with self.client.stream('GET',https_url(url),headers={'User-Agent':'DJ-R3X-v3/1.0'}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if self.stop.is_set():raise ProviderFailure('ObservationCancelled')
                if time.monotonic()>deadline or len(body)+len(chunk)>1_000_000:raise ProviderFailure('ObservationResponseLimit')
                body.extend(chunk)
        return bytes(body)

    def refresh(self):
        jobs=[('news:'+url,url) for url in self.settings.news_feed_urls]
        if self.settings.weather_enabled:
            query=urlencode(dict(latitude=self.settings.weather_latitude,longitude=self.settings.weather_longitude,
                current='temperature_2m,precipitation,weather_code,wind_speed_10m',temperature_unit='fahrenheit',
                wind_speed_unit='mph',precipitation_unit='inch',timeformat='unixtime',timezone='UTC'))
            jobs.append(('weather','https://api.open-meteo.com/v1/forecast?'+query))
        for name,url in jobs:
            if self.stop.is_set():return
            began=time.monotonic()
            try:
                raw=self._get(url);now=self.clock()
                with self.lock:previous=next(iter(self.rows.get('weather',())),None)
                rows=news_items(raw,url,now) if name.startswith('news:') else (weather_item(raw,self.settings.weather_location,url,now,previous),)
                with self.lock:self.rows[name]=rows
                if self.trace:self.trace.emit('observation_refresh',source=name.split(':')[0],outcome='ok',candidates=len(rows),seconds=time.monotonic()-began)
            except Exception as exc:
                if self.trace:self.trace.emit('observation_refresh',source=name.split(':')[0],outcome='failed',error_type=type(exc).__name__)
                # Last successful evidence remains usable only until its original expiry.

    def _run(self):
        while not self.stop.is_set():
            self.refresh();self.stop.wait(900)

    def candidates(self, include_offered=False):
        with self.lock:
            now=self.clock()
            rows=[r for group in self.rows.values() for r in group if r.expires_at>now and
                  (include_offered or now-self.offered.get(r.cue_id,float('-inf'))>=900)]
            # Interleave sources so a long news feed cannot hide weather or another feed.
            groups={k:[r for r in rows if r.kind==k] for k in ('news','weather')}
            merged=[]
            while any(groups.values()):
                for group in groups.values():
                    if group:merged.append(group.pop(0))
            if not merged:return ()
            start=self.cursor%len(merged)
            return tuple((merged[start:]+merged[:start])[:4])

    def valid(self,cue_id):
        with self.lock:
            return any(r.cue_id==cue_id and r.expires_at>self.clock() for group in self.rows.values() for r in group)

    def attempted(self,ids):
        with self.lock:
            now=self.clock();self.offered={key:at for key,at in self.offered.items() if now-at<900}
            self.offered.update({key:now for key in ids});self.cursor+=len(ids)

    def cancel(self):self.stop.set()
    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(12)
            if self.worker.is_alive():raise ProviderFailure('ObservationWorkerDidNotStop')
        if self.client:self.client.close()
        with self.lock:self.rows.clear();self.offered.clear()
