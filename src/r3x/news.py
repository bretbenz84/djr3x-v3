"""Searched daily stories and interest news: provenance, dates and budgets, never speech.

Adapted from V2 `awareness/current_events.py` (see V2_REFERENCE.md). One hosted web search
per day picks a handful of concrete, varied stories; people's saved interests get one search
per topic per day under a daily budget. Results are cached under `local/cache` so a restart
on the same day does not pay again. Stories become ordinary observation cues; the one
conversation model decides whether to mention one, and the event date travels with the cue
so Rex states when it happens instead of calling everything "today".
"""
from dataclasses import replace
from datetime import date, datetime
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

from .memory import label

STORY_COUNT = 5
TOPIC_STORY_COUNT = 3
TOPIC_SEARCHES_PER_DAY = 4      # V2 INTEREST_NEWS_MAX_TOPICS_PER_DAY.
GENERAL_ATTEMPTS_PER_DAY = 3    # A failed daily fetch retries, but never without bound.
RETRY_SECONDS = 3600
STORY_LIFETIME = 30 * 3600      # Day-old stories beat none; older ones are not "news".

# V2 2026-07-18: daily news kept featuring AI-company launches (the search runs on OpenAI's own
# model). A prompt rule alone is a request; the category is structured and code keeps at most one.
CATEGORIES = ("world", "science", "sports", "culture", "weather", "odd", "business", "ai_tech")
STORY_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["stories"], "properties": {
    "stories": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "required": ["headline", "summary", "category", "topic", "event_date", "outlet", "url"],
        "properties": {"headline": {"type": "string"}, "summary": {"type": "string"},
                       "category": {"type": "string", "enum": list(CATEGORIES)},
                       "topic": {"type": "string"}, "event_date": {"type": ["string", "null"]},
                       "outlet": {"type": "string"}, "url": {"type": "string"}}}}}}

_SHAPE = ("Each story: headline (short); category (ai_tech for anything about AI, AI companies such as "
          "OpenAI, chatbots, AI models or tech-company product launches, whatever else it touches); summary (one or two plain sentences with the concrete facts, no "
          "links); topic (one or two words); event_date (YYYY-MM-DD of the event itself: the day it "
          "happened or will happen, which may differ from the publication day; null when the article "
          "does not say); outlet (who reported it); url (the article you actually read).")


def general_prompt(today: date) -> str:
    when = today.strftime('%B %-d, %Y')
    return (f"Today is {when}. Search the web (several searches if needed) and return the {STORY_COUNT} most "
            "notable or talked-about stories right now. Every story must be a concrete event: a launch, a "
            "ruling, a record, a discovery, a release, a win, an outage. Never describe news outlets, "
            "homepages, calendars or roundup pages; if a search finds only those, search again. Mix hard "
            "news with lighter or delightfully odd items, not several angles on one story. Spread the "
            "topics: at most one ai_tech story, and none is fine; do not favor OpenAI or any AI company's products. "
            "The rest come from world events, science, sports, culture, weather and the genuinely weird. Skip paywalled minutiae, gossip about "
            "minors and graphic tragedy. " + _SHAPE)


def topic_prompt(topic: str, today: date) -> str:
    when = today.strftime('%B %-d, %Y')
    return (f"Today is {when}. Search the web for the most recent notable news about: {topic}. Return up to "
            f"{TOPIC_STORY_COUNT} of the freshest concrete items (a release, an episode, a match result, a "
            "discovery, an announcement, an event); prefer this week over this month. Return fewer, or none, "
            "rather than stale or off-topic items. Never describe outlets, homepages or coverage itself. "
            "Skip rumors and paywalled minutiae. " + _SHAPE)


def timing(event_date: str | None, today: date) -> str:
    """When the story's event happens relative to today, stated so the model need not subtract dates."""
    if not event_date:
        return ('The report gives no event date: you do not know when it happened or will happen. Do not say '
                '"today", "tonight" or "just now".')
    days = (date.fromisoformat(event_date) - today).days
    relative = ('today' if days == 0 else 'tomorrow, so it has not happened yet' if days == 1 else
                'yesterday' if days == -1 else f'in {days} days, so it has not happened yet' if days > 1 else
                f'{-days} days ago, so it is already past')
    return (f'The event is on {date.fromisoformat(event_date).strftime("%B %-d, %Y")}, which is {relative}. '
            'Never call a future event "today" or "tonight".')


def _host(url: str) -> str:
    return (urlsplit(url).hostname or '').lower().removeprefix('www.')


def stories(answer: dict, sources: list[str], today: date, limit: int, ai_tech_limit: int = 1) -> list[dict]:
    """Validated stories whose article was among the pages the search actually returned.

    A link the search never surfaced may be invented, so its story is dropped rather than
    offered as news. Matching the site, not the exact page, tolerates a page the model opened
    from a search result; the source list itself is the evidence."""
    from .observations import Plain, https_url
    hosts = {_host(u) for u in sources if isinstance(u, str)}
    out, ai_tech = [], 0
    for item in (answer.get('stories') or [])[:limit]:
        try:
            if item['category'] not in CATEGORIES:
                continue
            if item['category'] == 'ai_tech':
                if ai_tech >= ai_tech_limit:
                    continue
                ai_tech += 1
            url = https_url(item['url'])
            if url not in sources and _host(url) not in hosts:
                continue
            headline, summary = Plain.text(item['headline'])[:200], Plain.text(item['summary'])[:500]
            if not headline or not summary:
                continue
            when = item['event_date']
            try:
                # An impossible or far-off date is dropped, not the story: timing then says "unknown".
                if when is not None and (date.fromisoformat(when).isoformat() != when or
                                         abs((date.fromisoformat(when) - today).days) > 366):
                    when = None
            except (TypeError, ValueError):
                when = None
            out.append({'headline': headline, 'summary': summary, 'category': item['category'], 'topic': Plain.text(item['topic'])[:40],
                        'event_date': when, 'outlet': Plain.text(item['outlet'])[:80] or _host(url), 'url': url})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def normalize_topic(topic: str) -> str:
    return label(topic)[:80]


def cues(entry: dict, kind: str, topic: str = '') -> tuple:
    from .observations import Cue
    fetched = entry['fetched_at']
    rows = []
    for story in entry['stories']:
        key = kind + ':' + sha256((topic + '\n' + story['url'] + '\n' + story['headline']).encode()).hexdigest()[:24]
        text = json.dumps({k: story[k] for k in ('headline', 'summary', 'topic', 'event_date')} |
                          ({'interest_topic': topic} if topic else {}), ensure_ascii=False)
        rows.append(Cue(key, kind, story['outlet'] + ' (found by web search)', story['url'], fetched,
                        fetched + STORY_LIFETIME, text, topic))
    return tuple(rows)


def with_timing(cue, today: date):
    """Add the event's timing at offer time, so a story kept overnight is not still 'tomorrow'."""
    if cue.kind not in {'story', 'interest_news'}:
        return cue
    data = json.loads(cue.text)
    data['timing'] = timing(data.get('event_date'), today)
    return replace(cue, text=json.dumps(data, ensure_ascii=False))


class NewsCache:
    """One small JSON file; atomic replacement, no torn reads."""
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + '\n')
        tmp.replace(self.path)


def interest_topics(store, faces) -> dict[str, list[str]]:
    """Search topics of the recognized people in view, from their accepted self-reported interests."""
    visible = {f.get('identity') for f in faces if f.get('identity')}
    if not visible:
        return {}
    rows = store.rows('''SELECT i.topic, c.subject_id FROM interests i JOIN claims c ON c.id=i.claim_id
                         JOIN people p ON p.id=c.subject_id
                         WHERE c.status='accepted' AND c.kind='self_report' AND c.said_by=c.subject_id
                         AND p.status='supported' ORDER BY c.revision DESC LIMIT 64''')
    topics: dict[str, list[str]] = {}
    for row in rows:
        if row['subject_id'] in visible:
            topics.setdefault(row['topic'], []).append(row['subject_id'])
    return topics


def local_day(at: float) -> str:
    return datetime.fromtimestamp(at).date().isoformat()
