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
    topic: str = ''   # interest_news only: the saved interest it answers; offered only with that person present.

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
    def __init__(self, settings, trace=None, clock=time.time, search=None, cache=None):
        self.settings,self.trace,self.clock=settings,trace,clock
        self.capability=Capability('public_observations','RSS + Open-Meteo + web search',
            'dated top headlines, daily searched stories, interest news and local weather',
            None,('httpx',),('one network worker; refresh >=15 minutes; <=1 MB per response; '
                             'searches: one daily set, <=4 interest topics a day',),required=False)
        self.lock=threading.RLock();self.stop=threading.Event();self.wake=threading.Event()
        self.worker=None;self.client=None
        self.rows={};self.offered={};self.cursor=0
        # Hosted web search (news.py) is optional; its JSON cache survives restarts on the same day.
        self.search,self.cache=search,cache
        self.wanted=frozenset();self.searched=(None,frozenset());self.searching=False

    def start(self):
        import httpx
        self.client=httpx.Client(timeout=httpx.Timeout(3),follow_redirects=False)
        if self.search:self.search.start()
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
        if self.search and self.cache and not self.stop.is_set():
            self.refresh_searches()

    def _search(self,prompt,source,topic,today,limit):
        from .news import stories
        began=time.monotonic()
        try:
            self.searching=True
            answer,sources,usage=self.search.search(prompt)
            found=stories(answer,sources,today,limit,ai_tech_limit=limit if topic else 1)
        except Exception as exc:
            if self.trace:self.trace.emit('observation_refresh',source=source,topic=topic,outcome='failed',error_type=type(exc).__name__)
            return None
        finally:
            self.searching=False
        if self.stop.is_set():return None
        if self.trace:
            self.trace.emit('observation_refresh',source=source,topic=topic,outcome='ok',candidates=len(found),
                offered_by_model=len(answer.get('stories') or []),sources=len(sources),
                seconds=time.monotonic()-began,usage=usage)
        return {'date':today.isoformat(),'fetched_at':self.clock(),'stories':found}

    def refresh_searches(self):
        """Once-a-day stories and budgeted interest topics. Attempts are recorded before paying."""
        from datetime import date
        from .news import (GENERAL_ATTEMPTS_PER_DAY, RETRY_SECONDS, STORY_COUNT, TOPIC_SEARCHES_PER_DAY,
                           TOPIC_STORY_COUNT, general_prompt, local_day, topic_prompt)
        now=self.clock();today=local_day(now);data=self.cache.load()
        day=data.get('day') if isinstance(data.get('day'),dict) and data['day'].get('date')==today else \
            {'date':today,'general_attempts':0,'last_general_attempt':None,'topic_searches':0,'topics_tried':[]}
        data['day']=day;topics=data.setdefault('topics',{})
        general=data.get('general')
        if ((not general or general.get('date')!=today) and day['general_attempts']<GENERAL_ATTEMPTS_PER_DAY and
                (day['last_general_attempt'] is None or now-day['last_general_attempt']>=RETRY_SECONDS)):
            day['general_attempts']+=1;day['last_general_attempt']=now;self.cache.save(data)
            entry=self._search(general_prompt(date.fromisoformat(today)),'search','',date.fromisoformat(today),STORY_COUNT)
            if entry and entry['stories']:data['general']=entry
        with self.lock:wanted=self.wanted
        for topic in sorted(wanted):
            if self.stop.is_set():break
            entry=topics.get(topic)
            if (entry and entry.get('date')==today) or topic in day['topics_tried']:continue
            if day['topic_searches']>=TOPIC_SEARCHES_PER_DAY:
                if self.trace:self.trace.emit('observation_refresh',source='interest',topic=topic,outcome='daily_budget_spent')
                break
            day['topic_searches']+=1;day['topics_tried'].append(topic);self.cache.save(data)
            entry=self._search(topic_prompt(topic,date.fromisoformat(today)),'interest',topic,date.fromisoformat(today),TOPIC_STORY_COUNT)
            if entry is not None:topics[topic]=entry
        # Interest entries older than two days are no longer news; drop them from the file.
        data['topics']={k:v for k,v in topics.items() if isinstance(v,dict) and now-v.get('fetched_at',0)<2*86400}
        self.cache.save(data)
        with self.lock:self.searched=(today,frozenset(day['topics_tried']))
        self._load_searched(data)

    def _load_searched(self,data):
        from .news import cues
        now=self.clock();rows={}
        general=data.get('general')
        try:
            if general:rows['story']=cues(general,'story')
            for topic,entry in data.get('topics',{}).items():
                rows['interest:'+topic]=cues(entry,'interest_news',topic)
        except (KeyError,TypeError,ValueError):
            rows={}   # A damaged cache offers nothing rather than half-trusted stories.
        with self.lock:
            for key in [k for k in self.rows if k=='story' or k.startswith('interest:')]:del self.rows[key]
            self.rows.update({k:tuple(r for r in v if r.expires_at>now) for k,v in rows.items()})

    def want_topics(self,topics):
        """Interest topics of the people in view; a topic not yet searched today wakes the worker."""
        from .news import local_day
        topics=frozenset(topics)
        with self.lock:
            self.wanted=topics
            day,tried=self.searched
            new=day==local_day(self.clock()) and bool(topics-tried)
        if new and self.search:self.wake.set()

    def _run(self):
        while not self.stop.is_set():
            self.refresh();self.wake.wait(900);self.wake.clear()

    def candidates(self, include_offered=False, topics=()):
        """Interest news is offered only for topics of people in view (`topics`)."""
        from datetime import date
        from .news import local_day, with_timing
        with self.lock:
            now=self.clock();topics=set(topics)
            rows=[r for group in self.rows.values() for r in group if r.expires_at>now and
                  (r.kind!='interest_news' or r.topic in topics) and
                  (include_offered or now-self.offered.get(r.cue_id,float('-inf'))>=900)]
            # Interleave sources so a long news feed cannot hide weather or another feed.
            groups={k:[r for r in rows if r.kind==k] for k in ('interest_news','story','news','weather')}
            merged=[]
            while any(groups.values()):
                for group in groups.values():
                    if group:merged.append(group.pop(0))
            if not merged:return ()
            start=self.cursor%len(merged)
            today=date.fromisoformat(local_day(now))
            return tuple(with_timing(c,today) for c in (merged[start:]+merged[:start])[:4])

    def valid(self,cue_id):
        with self.lock:
            return any(r.cue_id==cue_id and r.expires_at>self.clock() for group in self.rows.values() for r in group)

    def attempted(self,ids):
        with self.lock:
            now=self.clock();self.offered={key:at for key,at in self.offered.items() if now-at<900}
            self.offered.update({key:now for key in ids});self.cursor+=len(ids)

    def cancel(self):self.stop.set();self.wake.set()
    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(12)
            # A hosted search can take up to 90 s. It holds no spoken or personal state and only
            # rewrites the news cache, so shutdown does not wait for it (the thread is a daemon).
            if self.worker.is_alive() and not self.searching:raise ProviderFailure('ObservationWorkerDidNotStop')
        if self.client:self.client.close()
        if self.search and not self.searching:self.search.close()
        with self.lock:self.rows.clear();self.offered.clear()
