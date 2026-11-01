"""Familiarity (owner, October 31): "Each time I turn him on he greets me and comments on the telescope in
the room. There is no sense of novelty or familiarity", "we almost need a recency bias as well", and "Someone
rex sees 20 minutes apart vs 20 days apart has no current avenue of expression." What Rex keeps saying wears
out across days and rests; a view he sees most days is everyday; a greeting knows how long it has been.
Production Session, state owner and room evidence owner with fake clocks, synthetic fingerprints and a
scripted reply model; no I/O."""
import json
from types import SimpleNamespace

from r3x import familiarity, jokes
from r3x.output import TextOutput
from r3x.places import Places, VIEW_EVERYDAY_DAYS
from r3x.session import Session
from r3x.speakers import SpeakerTracker
from r3x.state import CoreState
from tests import test_memory_core as memory_tests
from tests.test_curiosity import Brain, CuriosityFixture, say
from tests.test_places import PlaceFixture, KITCHEN, near, unit
from tests.test_proactive import event, face
from tests.test_voice_core import Trace

DAY = familiarity.DAY
T0 = 1.7e9


def familiar(snapshot):
    return snapshot.context['right_now'].get('familiar') or {}


class TalkedAboutTests(memory_tests.MemoryFixture):
    def said(self, *lines, person=None, keep=('bret',)):
        with self.store.transaction() as conn:
            for text, at in lines:
                familiarity.record(conn, text, person, at, set(keep))

    def test_what_he_keeps_saying_wears_out_then_rests(self):
        self.said(('That telescope is pointed at the room again, Bret.', T0),
                  ('Your telescope survives another inspection.', T0 + 60), ('A lonely kettle.', T0 + 90))
        now = familiarity.context(self.store, [], T0 + 120)
        self.assertEqual(now, {'worn_out': ['telescope']})          # Once is not a habit; names never count.
        self.assertEqual(familiarity.context(self.store, [], T0 + 2 * DAY), {'used_lately': ['telescope']})
        self.assertIsNone(familiarity.context(self.store, [], T0 + 7 * DAY))   # Rested: fair game again.
        # The more he said it, the longer it rests.
        self.said(*[('The telescope again.', T0 + 10 * DAY + i) for i in range(9)])
        self.assertEqual(familiarity.context(self.store, [], T0 + 15 * DAY), {'worn_out': ['telescope']})
        self.assertIsNone(familiarity.context(self.store, [], T0 + 23 * DAY))

    def test_a_private_line_stays_with_its_audience_and_goes_when_they_are_forgotten(self):
        bret = self.enroll('Bret')
        self.said(('Still building that telescope mount?', T0), ('The telescope mount lives.', T0 + 5), person=bret)
        self.assertIsNone(familiarity.context(self.store, [], T0 + 10))     # A guest alone is not shown them.
        self.assertIn('telescope', familiarity.context(self.store, [bret], T0 + 10)['worn_out'])
        self.begin('Please forget me.')
        self.assertEqual(self.call('forget_person', {'person_id': bret}).outcome, 'ok')
        self.assertEqual(self.store.rows('SELECT * FROM talked_about'), [])

    def test_the_writer_is_given_what_is_worn_out(self):
        picture = {'familiar': {'talked_about': {'worn_out': ['telescope']}}}
        self.assertEqual(json.loads(jokes.summary([], picture, [], {}))['worn_out'], ['telescope'])


class HeardLinesTests(CuriosityFixture):
    def test_his_heard_lines_count_across_sessions_and_nobody_elses_words_do(self):
        self.enroll('Bret')
        self.start(say('That telescope is aimed at the couch again.'), say('Max is guarding the telescope, I see.'))
        self.hear('Telescope, telescope, telescope. Look at my bicycle.')
        self.hear('Ha.')
        words = {r['word'] for r in self.store.rows('SELECT word FROM talked_about')}
        self.assertIn('telescope', words)
        self.assertNotIn('bicycle', words)        # A person's words are never his.
        self.assertNotIn('max', words)            # Names of pets and people are not spent by being said.
        self.start(say('Hello.'))                 # A new conversation reads it back from the store.
        self.hear('Morning.')
        self.assertEqual(familiar(self.brain.snapshots[0])['talked_about']['worn_out'][0], 'telescope')

    def test_a_line_nobody_heard_counts_for_nothing(self):
        self.enroll('Bret')
        self.brain = Brain(say('The telescope, the telescope.'))
        self.session = Session('session', self.brain, TextOutput(self.trace), self.trace, self.state)
        self.hear('Hi.')
        self.assertEqual(self.store.rows('SELECT * FROM talked_about'), [])

    def test_the_picture_says_how_familiar_the_view_is(self):
        self.enroll('Bret')
        self.state.places = SimpleNamespace(view_context=lambda: {'this_view': 'everyday', 'seen_on': '9 of the last 14 days'},
                                            context=lambda: {'status': 'recognized'})
        self.start(say('Hi.'))
        self.hear('Morning.')
        self.assertEqual(familiar(self.brain.snapshots[0])['view']['this_view'], 'everyday')


class ArrivalNoteTests(CuriosityFixture):
    def test_the_hello_is_told_the_time_apart_what_is_worn_out_and_that_the_view_is_everyday(self):
        # October 31: the note is written before the picture of the present; it first went out without
        # the worn-out words or the view, and 5 of 6 paid hellos opened on the telescope.
        bret = self.enroll('Bret')
        wall = self.state.wall_clock()
        with self.store.transaction() as conn:
            for line in ('That telescope again.', 'The telescope, still standing guard.'):
                familiarity.record(conn, line, bret, wall - 3600, set())
            conn.execute('INSERT INTO visits VALUES(?,?,?,?)', ('v1', bret, wall - 20 * DAY - 3600, wall - 20 * DAY))
        self.state.places = SimpleNamespace(view_context=lambda: {'this_view': 'everyday', 'seen_on': '9 of the last 14 days'},
                                            context=lambda: {'status': 'recognized'})
        self.state.current = self.state.conversation_source = None
        self.start('Bret! Twenty days. I counted.')
        self.trace.at += 400
        self.state.present.saw_faces([face(self.trace.at, bret)], self.trace.at)
        result = self.session.process_opportunity(event(bret, at=self.trace.at), self.session.revision, lambda: True)
        self.assertEqual(result.status, 'completed')
        note = next(m['content'] for m in self.brain.snapshots[0].messages if 'Startup greeting' in str(m.get('content')))
        self.assertIn('You last saw them about 20 days ago (a long time: two weeks or more)', note)
        self.assertIn('Worn out lately, so not in this hello in any words: telescope', note)
        self.assertIn('This is your everyday view', note)


class ViewTests(PlaceFixture):
    def restart(self, days=1):
        """The next day's run: a fresh room evidence owner over the same store."""
        self.clock.now += days * DAY
        camera = SimpleNamespace(session_id='s', latest=lambda now: self.faces, hub=None)
        self.places = Places(camera, self.encoder, self.store, motion=lambda: self.moving[0],
                             clock=self.clock, wall_clock=lambda: T0 + self.clock.now)
        self.places.load()

    def look(self, vector, seconds):
        self.see(vector, int(seconds / 1.5))

    def test_a_view_seen_on_earlier_days_is_familiar_then_everyday_and_a_new_one_is_new(self):
        self.restart(0)
        desk = unit(7)
        self.look(desk, 300)
        self.assertIsNone(self.places.view_context())       # His own run never makes a view familiar.
        for day in range(1, VIEW_EVERYDAY_DAYS + 1):
            self.restart()
            self.look(near(desk, day), 30)
            if day == 1:
                self.assertEqual(self.places.view_context(), {'this_view': 'seen_before', 'seen_on': '1 of the last 14 days'})
        self.assertEqual(self.places.view_context(), {'this_view': 'everyday', 'seen_on': '4 of the last 14 days'})
        self.look(unit(8), 30)                                # Somewhere he has never been.
        self.assertEqual(self.places.view_context()['this_view'], 'new')
        self.restart(VIEW_EVERYDAY_DAYS * 5)                  # Weeks away: the old days no longer count.
        self.look(near(desk, 99), 30)
        self.assertEqual(self.places.view_context()['this_view'], 'new')

    def test_one_view_all_day_is_one_sample_and_moving_or_a_stale_check_says_nothing(self):
        self.restart(0)
        self.look(unit(9), 3600)
        self.assertEqual(len(self.store.rows('SELECT * FROM place_views')), 1)
        self.restart()
        self.moving[0] = True
        self.look(unit(9), 60)
        self.assertIsNone(self.places.view_context())        # Driving: no check.
        self.moving[0] = False
        self.look(unit(9), 15)
        self.assertEqual(self.places.view_context()['this_view'], 'seen_before')
        self.clock.now += 60
        self.assertIsNone(self.places.view_context())         # An old check is not about now.

    def test_a_rooms_saved_views_count_as_days_he_was_there(self):
        self.restart(0)
        self.room('kitchen', KITCHEN)
        self.restart()
        self.look(near(KITCHEN, 5), 15)
        self.assertEqual(self.places.view_context()['this_view'], 'seen_before')


class VisitTests(memory_tests.MemoryFixture):
    def setUp(self):
        super().setUp()
        self.state.wall_clock = lambda: T0 + self.state.trace.at
        with self.store.transaction() as conn:
            self.jordan = self.store.add_person(conn, 'Jordan', [], None)

    def stay(self, state, person, seconds):
        """Recognized by face every ten seconds for this long."""
        for _ in range(int(seconds / 10) + 1):
            state.trace.at += 10
            state.present.saw_faces([face(state.trace.at, person)], state.trace.at)
            state.note_visits(state.trace.at)

    def test_a_greeting_hears_how_long_it_has_been_and_how_often_they_come(self):
        self.stay(self.state, self.jordan, 120)
        self.state.trace.at += 20 * DAY
        self.stay(self.state, self.jordan, 20)
        self.assertEqual(self.state.last_visit(self.jordan), {'last_here': 'about 20 days ago',
            'absence': 'a long time: two weeks or more', 'days_here_lately': '0 of the last 14 days'})
        self.stay(self.state, self.jordan, 60)
        self.state.trace.at += 600                            # Gone ten minutes, then back.
        self.stay(self.state, self.jordan, 20)
        back = self.state.last_visit(self.jordan)
        self.assertEqual(back['absence'], 'barely gone: they were here minutes ago')
        self.assertEqual(back['days_here_lately'], '1 of the last 14 days')

    def test_visits_outlast_a_restart(self):
        self.stay(self.state, self.jordan, 60)
        later = CoreState(self.store, SpeakerTracker(memory_tests.NS), Trace())
        later.trace.at = self.state.trace.at + 4 * 3600
        later.wall_clock = lambda: T0 + later.trace.at
        self.stay(later, self.jordan, 20)
        self.assertEqual(later.last_visit(self.jordan)['absence'], 'back the same day')

    def test_the_arrival_carries_it_and_no_record_claims_nothing(self):
        self.assertNotIn('last_visit', self.state.opportunity_context(event(self.jordan))['visual_arrival'])
        self.stay(self.state, self.jordan, 60)
        self.state.trace.at += 3 * DAY
        self.stay(self.state, self.jordan, 10)
        arrival = self.state.opportunity_context(event(self.jordan, at=self.state.trace.at))['visual_arrival']
        self.assertEqual(arrival['last_visit']['absence'], 'a few days')

    def test_the_conversation_record_stands_in_before_visits_were_kept(self):
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO conversation_exchanges VALUES('x','old',?,NULL,?,'Hi.','Hello.','completed','conversation')",
                         (self.jordan, T0 + self.state.trace.at - 2 * DAY))
        self.stay(self.state, self.jordan, 10)
        self.assertEqual(self.state.last_visit(self.jordan)['last_here'], 'about 2 days ago')

    def test_forgetting_someone_removes_their_visits(self):
        bret = self.enroll('Bret')
        self.stay(self.state, bret, 30)
        self.assertTrue(self.store.rows('SELECT * FROM visits WHERE person_id=?', (bret,)))
        self.begin('Please forget me.')
        self.assertEqual(self.call('forget_person', {'person_id': bret}).outcome, 'ok')
        self.assertEqual(self.store.rows('SELECT * FROM visits WHERE person_id=?', (bret,)), [])
