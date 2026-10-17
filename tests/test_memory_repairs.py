"""October 16 memory repairs: the wine run asked run after run, and things he was told he
could not find. Temporary databases, synthetic speech and fake clocks only."""
from datetime import date, timedelta
from uuid import uuid4

from r3x import conversation_memory as cm
from r3x.memory_opportunities import candidates
from r3x.speakers import SpeakerTracker
from r3x.state import CoreState
from tests.test_memory_core import MemoryFixture, NS, Trace
from tests.test_memory_opportunities import noon
from tests.test_proactive import face


class FollowupTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.person = self.enroll()
        self.today = date(2026, 10, 14)
        self.state.calendar_today = lambda: self.today
        self.state.wall_clock = lambda: noon(self.today)
        self.faces = (dict(face(100), identity=self.person),)

    def plan_in_five_minutes(self):
        self.begin("In five minutes, I'm going to go to the store to get wine.")
        result = self.call('remember_followup', dict(predicate='plans to buy wine', value='store in five minutes',
            kind='callback', on_date=None, due_in_minutes=5, due_by=None, audience='subject_only',
            source_turn_id=self.state.current[0]))
        self.assertEqual(result.outcome, 'ok', result)
        return result.data['claim_id']

    def offered(self):
        return [r['claim_id'] for r in candidates(self.store, self.faces, self.today, now=self.state.wall_clock())]

    def test_a_beat_that_asked_about_it_marks_the_followup_done(self):
        claim = self.plan_in_five_minutes()
        self.today += timedelta(days=1)                                # The next evening: its time has passed.
        self.assertEqual(self.offered(), [claim])
        self.assertEqual(self.state.followups_asked([claim, 'nearest_person']), [claim])
        self.assertEqual(self.offered(), [])                            # Not offered on later runs.
        self.assertEqual(self.state.followups_asked([claim]), [])       # Recorded once.

    def test_an_asked_plan_is_no_longer_current_knowledge(self):
        claim = self.plan_in_five_minutes()
        self.today += timedelta(days=1)
        self.state.followups_asked([claim])
        fact = next(cm.told(r, self.state.wall_clock()) for r in self.store.claims(self.person) if r['id'] == claim)
        self.assertIn('Do not ask about it again', fact['followup'])
        self.assertIn('not current', fact['planned_time'])

    def test_a_callback_without_a_time_is_brought_up_once_not_every_day(self):
        self.begin('Ask me about my telescope sometime.')
        claim = self.call('remember_followup', dict(predicate='telescope', value='ask about the telescope', kind='callback',
            on_date=None, due_in_minutes=None, due_by=None, audience='subject_only',
            source_turn_id=self.state.current[0])).data['claim_id']
        self.assertEqual(self.offered(), [claim])
        self.state.followups_asked([claim])
        self.today += timedelta(days=1)
        self.assertEqual(self.offered(), [])

    def test_every_saved_fact_reaches_the_reply(self):
        for i in range(8):
            self.begin(f'I like hobby number {i}.')
            self.call('record_claim', dict(subject_id=self.person, predicate=f'likes hobby {i}', value=f'hobby {i}',
                                           kind='self_report', source_turn_id=self.state.current[0]))
        context = cm.context(self.store, {'speaker': {'person_id': self.person, 'state': 'supported'},
                                          'people': [{'id': self.person, 'status': 'supported'}]},
                             'now', self.state.wall_clock())
        self.assertEqual(len(context['interests_and_facts']), 8)


class TopicBoundaryTests(MemoryFixture):
    def test_dont_bring_that_up_lasts_across_runs_until_retracted(self):
        person = self.enroll()
        self.begin('Stop asking me about the wine run.')
        saved = self.call('conversation_feedback', dict(action='topic_avoid', reference_id=None, topic='the wine run',
                                                        source_turn_id=self.state.current[0], text=None))
        self.assertEqual(saved.outcome, 'ok', saved)
        restarted = CoreState(self.store, SpeakerTracker(NS), Trace())
        carried = [r for r in restarted.initiative.context()['feedback'] if r['action'] == 'topic_avoid']
        self.assertEqual([(r['topic'], r['participant']['person_id']) for r in carried], [('the wine run', person)])
        self.state = restarted
        self.begin('Actually, you can ask about the wine again.')
        undone = self.call('conversation_feedback', dict(action='retract', reference_id=saved.data['id'],
                           topic=None, source_turn_id=self.state.current[0], text=None))
        self.assertEqual(undone.outcome, 'ok', undone)
        self.assertFalse(CoreState(self.store, SpeakerTracker(NS), Trace()).initiative.context()['feedback'])


class SearchTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.person = self.enroll()

    def say(self, human, at, heard='Noted.'):
        with self.store.transaction() as conn:
            conn.execute('INSERT INTO conversation_exchanges VALUES(?,?,?,?,?,?,?,?,?)',
                         (uuid4().hex, 'earlier', self.person, None, at, human, heard, 'completed', 'human_turn'))

    def search(self, query):
        return cm.recall(self.store, self.person, 'now', query=query, now=10_000_000.)

    def test_old_conversations_beyond_the_newest_two_hundred_are_found(self):
        self.say('My favorite Star Trek is Star Trek Voyager, followed by The Next Generation.', 1_000.)
        for i in range(330):
            self.say(f'Small talk number {i}.', 2_000. + i)
        found = self.search('favorite TV show Voyager')
        self.assertIn('Voyager', found['exchanges'][0]['human_text'])

    def test_a_near_spelling_still_matches(self):
        self.say('I talked about Sammy Rivera yesterday.', 1_000.)
        self.assertIn('Sammy', self.search('Sam Rivera')['exchanges'][0]['human_text'])

    def test_no_match_says_so_instead_of_returning_unrelated_chats(self):
        self.say('Can you shut down, please?', 1_000., 'Good night.')
        found = self.search('gay')
        self.assertEqual(found['exchanges'], [])
        self.assertIn('No earlier conversation', found['no_match'])

    def test_the_facts_lookup_also_searches_conversations_when_given_a_subject(self):
        self.say('My favorite Star Trek is Voyager.', 1_000.)
        self.begin('Did I ever tell you my favorite TV show?')
        result = self.call('recall', {'subject_id': self.person, 'query': 'favorite TV show'})
        self.assertEqual(result.outcome, 'ok', result)
        self.assertIn('Voyager', result.data['conversations']['exchanges'][0]['human_text'])
        plain = self.call('recall', {'subject_id': self.person})
        self.assertNotIn('conversations', plain.data)
