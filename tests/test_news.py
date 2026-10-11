from datetime import date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4
import json
import unittest

from r3x.agency import evidence_scope
from r3x.contracts import Cancellation, ToolCall
from r3x.news import NewsCache, interest_topics, stories, timing
from r3x.observations import PublicObservations
from r3x.proactive import Opportunity
from r3x.settings import Settings
from tests.test_memory_core import MemoryFixture, B
from tests.test_proactive import face

NOW = datetime(2026, 10, 11, 9).timestamp()   # Local time: the daily budget follows the local calendar.
TODAY = date(2026, 10, 11)
SOURCES = ['https://apnews.com/article/one', 'https://www.formula1.com/en/latest']


def story(url='https://apnews.com/article/one', event_date='2026-10-10', headline='Marathon record falls', category='sports'):
    return {'headline': headline, 'summary': '<b>Kipruto</b> ran 2:01:09.', 'category': category, 'topic': 'Sports',
            'event_date': event_date, 'outlet': 'Associated Press', 'url': url}


class Search:
    """A paid web search stand-in: records each prompt and returns grounded stories."""
    def __init__(self, fail=False):
        self.prompts, self.fail, self.started, self.closed = [], fail, False, False

    def start(self): self.started = True
    def close(self): self.closed = True

    def search(self, prompt):
        self.prompts.append(prompt)
        if self.fail: raise RuntimeError('synthetic search outage')
        return ({'stories': [story(headline=f'Story {len(self.prompts)}')]}, SOURCES,
                {'input_tokens': 21000, 'web_search_calls': 2})


def source(path, search, clock):
    return PublicObservations(Settings(news_feed_urls=(), weather_enabled=False), clock=lambda: clock[0],
                              search=search, cache=NewsCache(Path(path) / 'news.json'))


class StoryTests(unittest.TestCase):
    def test_only_stories_from_searched_pages_with_real_dates_are_kept(self):
        rows = stories({'stories': [story(), story('https://invented.example/x'), story(event_date='2026-02-30'),
                                    story('http://apnews.com/article/one'), {'headline': 'missing fields'}]},
                       SOURCES, TODAY, 5)
        self.assertEqual([r['event_date'] for r in rows], ['2026-10-10', None])
        self.assertNotIn('<b>', rows[0]['summary'])
        # Another page on a searched site is accepted: the model may open a result's article.
        self.assertTrue(stories({'stories': [story('https://formula1.com/race')]}, SOURCES, TODAY, 5))

    def test_general_news_keeps_at_most_one_ai_tech_story(self):
        answer = {'stories': [story(headline=f'AI launch {i}', category='ai_tech') for i in range(3)] +
                             [story(headline='Eclipse'), story(headline='Bad', category='gossip')]}
        self.assertEqual([r['headline'] for r in stories(answer, SOURCES, TODAY, 5)], ['AI launch 0', 'Eclipse'])
        # An interest the person stated (say, AI) is not capped: they asked for it.
        self.assertEqual(len(stories(answer, SOURCES, TODAY, 5, ai_tech_limit=5)), 4)

    def test_timing_states_when_the_event_happens(self):
        self.assertIn('yesterday', timing('2026-10-10', TODAY))
        self.assertIn('in 3 days, so it has not happened yet', timing('2026-10-14', TODAY))
        self.assertIn('already past', timing('2026-10-01', TODAY))
        self.assertIn('Do not say "today"', timing(None, TODAY))


class DailySearchTests(unittest.TestCase):
    def test_one_search_a_day_survives_restart_and_carries_timing(self):
        with TemporaryDirectory() as path:
            clock, search = [NOW], Search()
            first = source(path, search, clock); first.refresh()
            cue, = first.candidates()
            self.assertEqual((cue.kind, cue.source_url), ('story', 'https://apnews.com/article/one'))
            self.assertIn('yesterday', json.loads(cue.text)['timing'])
            restarted = source(path, search, clock); restarted.refresh()
            self.assertEqual(len(search.prompts), 1)          # Same day: the cached stories, no new search.
            self.assertEqual(restarted.candidates()[0].cue_id, cue.cue_id)
            clock[0] += 86400; restarted.refresh()
            self.assertEqual(len(search.prompts), 2)          # A new day searches again.
            self.assertIn('2 days ago', json.loads(restarted.candidates(True)[0].text)['timing'])

    def test_failed_search_retries_hourly_at_most_three_times_a_day(self):
        with TemporaryDirectory() as path:
            clock, search = [NOW], Search(fail=True)
            observations = source(path, search, clock)
            for _ in range(6):
                observations.refresh(); clock[0] += 1800
            self.assertEqual(len(search.prompts), 3)
            self.assertFalse(observations.candidates())

    def test_interest_topics_have_a_daily_budget_and_are_offered_only_to_their_people(self):
        with TemporaryDirectory() as path:
            clock, search = [NOW], Search()
            observations = source(path, search, clock); observations.refresh()
            topics = [f'topic {i}' for i in range(6)]
            observations.want_topics(topics)
            self.assertTrue(observations.wake.is_set())       # New topics wake the worker.
            observations.refresh()
            self.assertEqual(len(search.prompts), 1 + 4)
            self.assertEqual(sum('topic 5' in p for p in search.prompts), 0)
            self.assertFalse([c for c in observations.candidates() if c.kind == 'interest_news'])
            offered = [c for c in observations.candidates(topics={'topic 0'}) if c.kind == 'interest_news']
            self.assertEqual([c.topic for c in offered], ['topic 0'])
            self.assertEqual(json.loads(offered[0].text)['interest_topic'], 'topic 0')
            observations.wake.clear(); observations.want_topics(['topic 0'])
            self.assertFalse(observations.wake.is_set())      # Already searched today: no wake.
            observations.refresh(); self.assertEqual(len(search.prompts), 5)

    def test_shutdown_does_not_wait_on_a_search_in_flight(self):
        with TemporaryDirectory() as path:
            observations = source(path, Search(), [NOW])
            observations.worker = SimpleNamespace(join=lambda timeout: None, is_alive=lambda: True)
            observations.searching = True
            observations.close()                               # No ObservationWorkerDidNotStop.
            self.assertTrue(observations.stop.is_set())

    def test_setting_must_be_boolean_and_interest_news_is_private_planner_evidence(self):
        with self.assertRaises(ValueError): Settings(news_search_enabled='yes')
        self.assertEqual(evidence_scope({'kind': 'observations', 'data': {'kind': 'interest_news'}}), 'audience')
        self.assertEqual(evidence_scope({'kind': 'observations', 'data': {'kind': 'story'}}), 'general')


class InterestMemoryTests(MemoryFixture):
    def remember(self, text='I love the Sacramento Kings.', vector=None, topic='Sacramento Kings basketball'):
        self.begin(text, *(() if vector is None else (vector,)))
        return self.call('remember_interest', dict(predicate='interest', value='loves the Sacramento Kings',
                                                   topic=topic, source_turn_id=self.state.current[0]))

    def test_own_stated_interest_is_saved_as_a_sourced_claim_with_a_search_topic(self):
        person = self.enroll()
        result = self.remember()
        self.assertEqual(result.outcome, 'ok')
        self.assertEqual(result.data['interest_topic'], 'sacramento kings basketball')
        claim, = [c for c in self.store.claims() if c['id'] == result.data['claim_id']]
        self.assertEqual((claim['said_by'], claim['subject_id'], claim['status']), (person, person, 'accepted'))
        self.assertEqual(interest_topics(self.store, [{'identity': person}]),
                         {'sacramento kings basketball': [person]})
        self.assertEqual(interest_topics(self.store, [{'identity': None}]), {})

    def test_unknown_speaker_cannot_save_an_interest(self):
        self.enroll()
        result = self.remember('Bret loves the Kings.', B)
        self.assertEqual(result.reason, 'InterestNeedsSupportedSelfIdentity')
        self.assertFalse(self.store.rows('SELECT * FROM interests'))

    def test_retraction_and_forgetting_stop_the_interests_news(self):
        person = self.enroll()
        claim = self.remember().data['claim_id']
        self.begin('Actually I do not follow the Kings anymore.')
        self.assertEqual(self.call('retract_claim', dict(claim_id=claim, source_turn_id=self.state.current[0])).outcome, 'ok')
        self.assertEqual(interest_topics(self.store, [{'identity': person}]), {})
        self.remember()
        self.assertTrue(interest_topics(self.store, [{'identity': person}]))
        with self.store.transaction() as conn: self.store.forget(conn, person)
        self.assertFalse(self.store.rows('SELECT * FROM interests'))

    def test_interest_news_needs_its_person_in_view_and_a_spoken_story_stays_spoken(self):
        person = self.enroll(); self.remember()
        self.state.wall_clock = lambda: NOW
        news = {'cue_id': 'interest_news:x', 'kind': 'interest_news', 'expires_at': NOW + 3600,
                'topic': 'sacramento kings basketball'}
        here, stranger = [dict(face(100), identity=person)], [face(100, track='visitor')]
        self.assertTrue(self.state.observation_available(news, here))
        self.assertFalse(self.state.observation_available(news, stranger))
        self.assertFalse(self.state.observation_available(news))
        self.state.observation_delivered(news)
        self.state.wall_clock = lambda: NOW + 3 * 86400
        self.assertFalse(self.state.observation_available({**news, 'expires_at': NOW + 4 * 86400}, here))

    def test_lull_offers_and_executes_interest_news_only_with_its_person(self):
        person = self.enroll(); self.remember()
        from r3x.observations import Cue
        cue = Cue('interest_news:k', 'interest_news', 'AP (found by web search)', 'https://apnews.com/k',
                  NOW, NOW + 3600, json.dumps({'headline': 'Kings win'}), 'sacramento kings basketball')
        self.state.wall_clock = lambda: NOW
        self.state.observations = SimpleNamespace(valid=lambda key: key == cue.cue_id)

        def event(faces):
            return Opportunity(uuid4().hex, 'session', 100, None, None, faces, float('-inf'), False,
                               'conversation_lull', self.state.current[0], 8., public_cues=(cue,))
        self.assertFalse(self.state.opportunity_context(event((face(100, track='visitor'),)))['observations'])
        context = self.state.opportunity_context(event((dict(face(100), identity=person),)))
        self.assertEqual([c['cue_id'] for c in context['observations']], [cue.cue_id])
        result = self.state.execute_opportunity(ToolCall('x', 'use_observation',
            dict(cue_id=cue.cue_id, text='Did you see the Kings won?')), context, Cancellation())
        self.assertEqual(result.outcome, 'ok')
