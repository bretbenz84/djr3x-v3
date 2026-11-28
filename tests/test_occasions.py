"""Holiday and weekend plans questions (owner, November 27): "a friend might ask a week before
thanksgiving 'any plans for the big turkey day?' and then after it's passed, he should ask how it went
if plans were shared." Calendar and question rules with fixed dates, then the production Session with a
fake clock and a scripted reply model: the question, the saved answer, and the follow-up after. No I/O."""
from datetime import date, datetime
import unittest
from uuid import uuid4

from r3x import curiosity, occasions
from r3x.contracts import ToolCall
from r3x.memory_opportunities import candidates
from r3x.proactive import Opportunity
from tests import test_curiosity as curiosity_tests
from tests.test_proactive import face

say = curiosity_tests.say


def at(text):
    return datetime.fromisoformat(text)


class CalendarTests(unittest.TestCase):
    def test_holidays_fall_on_their_days(self):
        days = {key: day for key, _, day, _ in occasions.holidays(2026)}
        self.assertEqual(days['thanksgiving'], date(2026, 11, 26))
        self.assertEqual(days['easter'], date(2026, 4, 5))
        self.assertEqual(days['memorial_day'], date(2026, 5, 25))
        self.assertEqual(days['labor_day'], date(2026, 9, 7))
        self.assertEqual(days['mothers_day'], date(2026, 5, 10))

    def test_the_picture_names_the_holiday_just_past_and_the_next_ones(self):
        note = occasions.calendar_note(date(2026, 11, 27))
        self.assertEqual(note['holidays'], ['Thanksgiving was yesterday (Thursday, November 26)',
                                            'Christmas Eve is in 27 days (Thursday, December 24)',
                                            'Christmas is in 28 days (Friday, December 25)'])
        self.assertIsNone(occasions.calendar_note(date(2026, 7, 20)))   # Nothing within a month.
        self.assertEqual(occasions.calendar_note(date(2026, 12, 31))['holidays'][:2],
                         ["New Year's Eve is today (Thursday, December 31)",
                          "New Year's Day is tomorrow (Friday, January 1)"])


class PlansQuestionTests(unittest.TestCase):
    def ask(self, when, asked=None, plans=()):
        return occasions.plans_question(at(when), asked or {}, set(plans), 'Bret')

    def test_a_big_holiday_is_asked_about_from_ten_days_out(self):
        self.assertIsNone(self.ask('2026-11-15T18:00'))            # Sunday evening, eleven days out.
        row = self.ask('2026-11-16T18:00')
        self.assertEqual(row['topic'], 'plans_thanksgiving_2026')
        self.assertEqual(row['soon'], 'Thanksgiving is in 10 days (Thursday, November 26)')
        self.assertIn('on_date 2026-11-26', row['when_they_answer'])
        self.assertEqual(self.ask('2026-11-26T09:00')['soon'], 'Thanksgiving is today (Thursday, November 26)')

    def test_a_smaller_holiday_is_asked_about_from_five_days_out(self):
        self.assertIsNone(self.ask('2026-06-28T18:00'))             # Sunday evening, six days out.
        self.assertEqual(self.ask('2026-06-29T18:00')['topic'], 'plans_independence_day_2026')

    def test_once_heard_or_already_known_it_is_not_asked(self):
        self.assertIsNone(self.ask('2026-11-17T18:00', asked={'plans_thanksgiving_2026': 1.}))   # A Tuesday.
        self.assertIsNone(self.ask('2026-11-17T18:00', plans={date(2026, 11, 26)}))
        # The Friday after it was asked: no weekend interview on top of the holiday question.
        heard = at('2026-11-19T18:00').timestamp()
        self.assertIsNone(self.ask('2026-11-20T18:00', asked={'plans_thanksgiving_2026': heard}))

    def test_the_weekend_is_asked_thursday_to_sunday_morning(self):
        self.assertIsNone(self.ask('2026-09-16T18:00'))             # Wednesday.
        row = self.ask('2026-09-17T18:00')
        self.assertEqual(row['topic'], 'plans_weekend_2026-09-20')
        self.assertEqual(row['soon'], 'the weekend is coming (Saturday September 19 and Sunday September 20)')
        self.assertIn('rest of the weekend', self.ask('2026-09-20T10:00')['want_to_know'])
        self.assertIsNone(self.ask('2026-09-20T13:00'))             # Sunday afternoon: too late.
        self.assertIsNone(self.ask('2026-09-18T18:00', asked={'plans_weekend_2026-09-20': 1.}))
        self.assertIsNone(self.ask('2026-09-18T18:00', plans={date(2026, 9, 19)}))

    def test_a_holiday_covers_its_weekend(self):
        row = self.ask('2026-09-03T18:00')                          # Labor Day is Monday the 7th.
        self.assertEqual(row['topic'], 'plans_labor_day_2026')
        self.assertIsNone(self.ask('2026-09-03T18:00', asked={'plans_labor_day_2026': 1.}))
        self.assertIsNone(self.ask('2026-11-27T18:00', asked={'plans_thanksgiving_2026': 1.}))   # Day after.
        asked = {'plans_halloween_2026': at('2026-10-30T18:00').timestamp()}
        self.assertIsNone(self.ask('2026-11-05T12:00', asked))      # Six days after a holiday question.
        self.assertIsNotNone(self.ask('2026-11-05T19:00', asked))


class ConversationTests(curiosity_tests.CuriosityFixture):
    def clock(self, when):
        moment = at(when)
        self.state.wall_clock = lambda: moment.timestamp()
        self.state.calendar_today = lambda: moment.date()

    def test_asked_a_week_before_saved_and_asked_how_it_went_after(self):
        bret = self.enroll('Bret')
        self.clock('2026-11-19T18:00')

        class Brain(curiosity_tests.Brain):
            def reply(brain, snapshot, cancel):
                brain.snapshots.append(snapshot)
                if len(brain.snapshots) == 1:
                    yield say('Any big plans for turkey day?', asked_about='plans_thanksgiving_2026')
                else:
                    yield ToolCall(uuid4().hex, 'remember_followup', {
                        'predicate': 'thanksgiving plan', 'value': "Thanksgiving at his mom's", 'kind': 'plan',
                        'on_date': '2026-11-26', 'due_in_minutes': None, 'due_by': None, 'audience': 'subject_only',
                        'source_turn_id': snapshot.context['source_turn_id'], 'other_person': None,
                        'subject_reference': None, 'text': "Your mom's? Bring me a drumstick."})
        self.brain = Brain()
        self.start_with(self.brain)
        self.hear("So what's up?")
        first = self.brain.snapshots[0].context
        lead = first['curiosity']['still_unknown'][0]
        self.assertEqual((lead['topic'], lead['soon']),
                         ('plans_thanksgiving_2026', 'Thanksgiving is in 7 days (Thursday, November 26)'))
        self.assertIn('soon_meaning', first['curiosity'])
        self.assertIn('Thanksgiving is in 7 days (Thursday, November 26)', first['right_now']['calendar']['holidays'])
        self.assertEqual([r['topic'] for r in self.store.rows('SELECT topic FROM curiosity_asked')],
                         ['plans_thanksgiving_2026'])
        self.hear("Going to my mom's.")
        answer = self.brain.snapshots[1].context
        self.assertIn('on_date 2026-11-26', answer['your_question']['when_they_answer'])
        self.assertNotIn('plans_thanksgiving_2026', self.topics(self.brain.snapshots[1]))
        self.assertEqual(len(self.store.rows('SELECT * FROM memory_followups')), 1)

        faces = (dict(face(100), identity=bret),)
        self.assertFalse(candidates(self.store, faces, date(2026, 11, 19), now=self.state.wall_clock()))   # Just told.
        before = candidates(self.store, faces, date(2026, 11, 24), now=at('2026-11-24T18:00').timestamp())
        self.assertEqual([r['occasion'] for r in before], ['before_due'])
        self.clock('2026-11-27T12:00')
        event = Opportunity(uuid4().hex, 'session', 100, 'face', bret, faces, float('-inf'), False)
        after = self.state.opportunity_context(event)['memory_opportunities']
        self.assertEqual([(r['occasion'], r['moment']) for r in after], [('after_due', 'passed')])
        # Thanksgiving week's weekend is covered by the holiday; it is not asked about again either.
        self.assertIsNone(occasions.plans_question(at('2026-11-27T12:00'),
                          {'plans_thanksgiving_2026': at('2026-11-19T18:00').timestamp()}, set(), 'Bret'))

    def test_nothing_planned_saves_nothing_and_nothing_is_asked_after(self):
        bret = self.enroll('Bret')
        self.clock('2026-11-19T18:00')
        self.start(say('Any big plans for turkey day?', asked_about='plans_thanksgiving_2026'),
                   say('A quiet one. Respect.'))
        self.hear("So what's up?")
        self.hear('Nothing much, staying home.')
        self.assertEqual(self.store.rows('SELECT * FROM memory_followups'), [])
        self.clock('2026-11-27T12:00')
        faces = (dict(face(100), identity=bret),)
        self.assertEqual(candidates(self.store, faces, date(2026, 11, 27), now=self.state.wall_clock()), [])
        rows = curiosity.still_unknown(self.store, bret, 'Bret', [], self.state.wall_clock())
        self.assertFalse([r for r in rows if r['topic'].startswith('plans_')])

    def test_a_pet_in_view_keeps_its_story_first(self):
        bret = self.enroll('Bret')
        rows = curiosity.still_unknown(self.store, bret, 'Bret', self.album.catalog(),
                                       at('2026-11-19T18:00').timestamp(), pets_first=True)
        self.assertEqual(rows[0]['about'], 'Max')
        self.assertEqual(rows[1]['topic'], 'plans_thanksgiving_2026')
        self.assertLessEqual(len(rows), curiosity.OFFERED)

    def start_with(self, brain):
        from r3x.output import OutputOwner
        from r3x.scenario import SyntheticTTS
        from r3x.session import Session
        self.session = Session('session', brain, OutputOwner(SyntheticTTS(), curiosity_tests.timing_tests.ClockSink(
            self.trace), self.trace), self.trace, self.state)


if __name__ == '__main__':
    unittest.main()
