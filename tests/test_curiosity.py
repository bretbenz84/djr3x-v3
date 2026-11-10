"""Curiosity about people and pets (owner, October 24): "True friends ask to know more, then remember it and
use that info for future conversations." Rex is offered what he does not know yet about the person he is with
and the saved pets; a question he was heard asking is spent for a while and stays open for the answer; the
record holds no words and goes with a forgotten person or pet. Production Session with a fake clock and a
scripted reply model; no I/O."""
from types import SimpleNamespace
from uuid import uuid4

from r3x import curiosity, jokes
from r3x.contracts import Capture, ToolCall, Transcript
from r3x.output import OutputOwner
from r3x.photo_memory import PhotoMemory
from r3x.scenario import SyntheticTTS
from r3x.session import FAR_PERSON_M, Session
from tests import test_memory_core as memory_tests
from tests import test_photo_memory as photo_tests
from tests import test_turn_taking as timing_tests
from tests import test_agency_plan as agency_tests


def say(text, asked_about=None):
    return ToolCall(uuid4().hex, 'respond_with_intent', {'text': text, 'intent_update': None,
                                                         'asked_about': asked_about, 'invitation_update': None,
                                                         'next_opportunity': None})


class Brain:
    """The reply model: the next scripted line for each human turn."""
    def __init__(self, *lines):
        self.lines, self.snapshots = list(lines), []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        yield self.lines.pop(0) if self.lines else say('Huh.')


class CuriosityFixture(memory_tests.MemoryFixture):
    def setUp(self):
        super().setUp()
        self.album = PhotoMemory(self.path.parent / 'photos'); self.album.start()
        self.album.data['entities'] = {'max-id': {'name': 'Max', 'kind': 'pet', 'notes': 'Small dark dog.',
                                                  'references': [], 'history': []},
                                       'toby-id': {'name': 'Toby', 'kind': 'pet', 'notes': 'Boston Terrier.',
                                                   'references': [], 'history': []}}
        self.state.photo_memory = self.album
        self.trace = self.state.trace
        self.trace.at = 100.

    def start(self, *lines):
        self.brain = Brain(*lines)
        self.session = Session('session', self.brain, OutputOwner(SyntheticTTS(), timing_tests.ClockSink(self.trace),
                               self.trace), self.trace, self.state)

    def hear(self, text, vector=memory_tests.A):
        self.trace.at += 10
        at = self.trace.at
        capture = Capture(uuid4().hex, 'session', bytes(96000), at - 3, at, at, voiced_intervals=((0., 3.),))
        self.session.note_speech(at)
        return self.session.process_turn(capture, Transcript(text, -.01, True), self.session.revision,
                                         memory_tests.observation(vector))

    def topics(self, snapshot):
        return [r['topic'] for r in (snapshot.context.get('curiosity') or {}).get('still_unknown', [])]


class StillUnknownTests(CuriosityFixture):
    def test_he_is_offered_what_he_does_not_know_about_the_owner_and_each_pet(self):
        bret = self.enroll('Bret')
        turn = self.begin('I love Star Trek.')
        self.assertEqual(self.call('remember_interest', {'predicate': 'interest', 'value': 'loves Star Trek',
            'topic': 'Star Trek', 'source_turn_id': turn, 'subject_reference': None}).outcome, 'ok')
        pets = self.album.catalog()
        rows = curiosity.still_unknown(self.store, bret, 'Bret', pets, 1000., pets_first=True)
        self.assertLessEqual(len(rows), curiosity.OFFERED)
        self.assertEqual(rows[0]['about'], 'Max')                 # A pet in view: its story comes first.
        self.assertEqual({r['about'] for r in rows}, {'Max', 'Toby', 'Bret'})
        self.assertIn('max_came_home', [r['topic'] for r in rows])
        self.assertIn('toby_came_home', [r['topic'] for r in rows])  # Each pet has its own topics.
        everything = curiosity.still_unknown(self.store, bret, 'Bret', [], 1000.)
        self.assertEqual(everything[0]['about'], 'Bret')
        wanted = {r['topic']: r['want_to_know'] for r in curiosity.still_unknown(self.store, bret, 'Bret', [], 1000.)}
        self.assertIn('work', wanted)
        # Only eight at a time; the interest story is among the person's topics once the first are asked.
        for topic in list(curiosity.PERSON_TOPICS):
            with self.store.transaction() as conn:
                curiosity.record(conn, {'topic': topic}, bret, 1000.)
        later = {r['topic']: r['want_to_know'] for r in curiosity.still_unknown(self.store, bret, 'Bret', [], 1001.)}
        self.assertEqual(list(later), [curiosity.interest_key('Star Trek')])
        self.assertIn('star trek', later[curiosity.interest_key('Star Trek')].lower())

    def test_no_known_private_audience_gets_no_topics(self):
        self.start(say('Hello there.'))
        self.hear('Hello, droid.', memory_tests.C)                 # A voice nobody knows.
        self.assertNotIn('curiosity', self.brain.snapshots[0].context)


class AskedTests(CuriosityFixture):
    def test_a_heard_question_is_spent_and_stays_open_for_the_answer(self):
        bret = self.enroll('Bret')
        self.start(say('Tell me about when you first got Max.', asked_about='max_came_home'),
                   say('A shelter? So he picked you.'), say('Fair.'))
        self.hear("So what's up?")
        offered = self.topics(self.brain.snapshots[0])
        self.assertIn('max_came_home', offered)
        self.assertIn('work', offered)
        rows = self.store.rows('SELECT person_id, pet_id, topic FROM curiosity_asked')
        self.assertEqual([dict(r) for r in rows], [{'person_id': None, 'pet_id': 'max-id', 'topic': 'max_came_home'}])
        self.hear('We got him from a shelter three years ago.')
        answer = self.brain.snapshots[1].context
        self.assertEqual(answer['your_question']['asked'], 'Tell me about when you first got Max.')
        self.assertEqual(answer['your_question']['about'], 'Max')
        self.assertNotIn('max_came_home', self.topics(self.brain.snapshots[1]))
        self.assertIn('toby_came_home', self.topics(self.brain.snapshots[1]))
        self.trace.at += curiosity.OPEN_SECONDS
        self.hear('Anyway.')
        self.assertNotIn('your_question', self.brain.snapshots[2].context)
        # Unanswered or not, it comes back only after the cooldown.
        max_only = [p for p in self.album.catalog() if p['name'] == 'Max']
        soon = curiosity.still_unknown(self.store, None, None, max_only, self.state.wall_clock() + 60)
        self.assertNotIn('max_came_home', [r['topic'] for r in soon])
        later = curiosity.still_unknown(self.store, None, None, max_only,
                                        self.state.wall_clock() + curiosity.ASKED_COOLDOWN + 1)
        self.assertEqual([r['topic'] for r in later][-1], 'max_came_home')   # Back, after the never-asked ones.

    def test_a_topic_never_offered_is_traced_and_not_recorded(self):
        self.enroll('Bret')
        self.start(say('What was your first car?', asked_about='first_car'))
        self.hear("So what's up?")
        self.assertEqual(self.store.rows('SELECT * FROM curiosity_asked'), [])
        self.assertTrue([r for r in self.trace.rows if r['event'] == 'curiosity_topic_not_offered'])

    def test_a_reply_that_names_no_topic_spends_nothing(self):
        self.enroll('Bret')
        self.start(say('Nice of you to check in.'))
        self.hear("So what's up?")
        self.assertEqual(self.store.rows('SELECT * FROM curiosity_asked'), [])

    def test_forgetting_the_person_removes_what_he_asked_them(self):
        bret = self.enroll('Bret')
        self.start(say('What do you do for work, anyway?', asked_about='work'))
        self.hear("So what's up?")
        self.assertEqual(len(self.store.rows('SELECT * FROM curiosity_asked WHERE person_id=?', (bret,))), 1)
        self.begin('Please forget me.')
        self.assertEqual(self.call('forget_person', {'person_id': bret}).outcome, 'ok')
        self.assertEqual(self.store.rows('SELECT * FROM curiosity_asked'), [])


class PetForgetTests(memory_tests.MemoryFixture):
    def setUp(self):
        super().setUp()
        self.album = PhotoMemory(self.path.parent / 'photos'); self.album.start()
        self.state.photo_memory = self.album
        self.state.objects = SimpleNamespace(snapshot=lambda: ({'target_id': 'object-1', 'label': 'cat', 'score': .7,
                                                                 'box': [0, 0, .5, .5]},),
            inspect=lambda target: (photo_tests.image(), {'target_id': 'object-1', 'label': 'cat', 'box': [0, 0, .5, .5]},
                                    photo_tests.image()))

    teach = photo_tests.PhotoTests.teach
    session = photo_tests.PhotoTests.session

    def test_forgetting_a_pet_removes_what_he_asked_about_it(self):
        key = self.teach()
        with self.store.transaction() as conn:
            curiosity.record(conn, {'topic': 'max_came_home', 'pet_id': key}, None, 10.)
        class Forget:
            def reply(brain, snapshot, cancel):
                yield ToolCall('forget', 'edit_object_memory', {'entity_id': key, 'action': 'forget', 'name': 'Max',
                    'notes': None, 'remove_notes': None, 'source_turn_id': snapshot.context['source_turn_id'],
                    'text': 'Done.'})
        session, receipt = self.session(Forget(), 'Forget Max.')
        session.close()
        self.assertEqual(receipt.completed_text, ['Done.'])
        self.assertEqual(self.store.rows('SELECT * FROM curiosity_asked'), [])


class PlannedQuestionTests(memory_tests.MemoryFixture):
    """A goal beat that asks a still-unknown topic names it; a far-off person gets a goal that rolls over."""
    setup_owner = agency_tests.PlanTests.setup_owner
    tick = agency_tests.PlanTests.tick
    visible = agency_tests.GoalTests.visible

    def test_the_planner_sees_what_he_does_not_know_and_how_far_away_they_are(self):
        self.setup_owner()
        person = self.enroll(name='Bret')
        self.visible(person, 'bret', 100)
        self.session.output.drive = SimpleNamespace(cancel=lambda *a, **kw: None, context=lambda *a, **kw: {'available': True, 'targets': [
            {'target_id': 'bret', 'kind': 'face', 'person_id': person, 'approx_range_m': FAR_PERSON_M + 1.}]})
        def plan(facts, cancel):
            value = agency_tests.draft()
            value['scope'] = 'audience'
            value['steps'][0] = {'kind': 'contribute', 'instruction': 'Ask what he does for work.', 'evidence_ids': [],
                                 'wake': {'when': 'quiet', 'seconds': 5}, 'follows_previous': False,
                                 'curious_about': 'work'}
            return value
        self.planner.choose = plan
        self.executor.choose = lambda snapshot, cancel: iter([say(
            'So what do you actually do all day at work?' if snapshot.context.get('agency_step') else 'Hey, Bret.')])
        self.tick(100)                       # The greeting.
        self.tick(self.trace.at + 9)         # The goal: its first beat asks.
        subject = self.planner.requests[0]['subject']
        self.assertIn('work', [r['topic'] for r in subject['curiosity']['still_unknown']])
        self.assertTrue(subject['distance']['far'])
        self.assertEqual(subject['distance']['target_id'], 'bret')
        beat = self.executor.snapshots[-1].context
        self.assertEqual(beat['agency_step']['curious_about'], 'work')
        self.assertEqual([r['topic'] for r in self.store.rows('SELECT topic FROM curiosity_asked')], ['work'])

    def test_a_beat_naming_its_topic_badly_is_rejected(self):
        from r3x.agency import AgencyPlan
        value = agency_tests.draft()
        value['steps'][0]['curious_about'] = 7
        agency = AgencyPlan(self.store, self.state.trace, self.state.present)
        with self.assertRaisesRegex(ValueError, 'InvalidAgencyCuriosity'):
            agency.install(value, '[]', {}, ['contribute'], agency.revision, 100.)


class JokeShapeTests(memory_tests.unittest.TestCase):
    def test_his_own_office_joke_is_spent_at_once_and_a_persons_words_never_count(self):
        own = lambda text: {'role': 'assistant', 'content': text}
        self.assertEqual(jokes.spent_shapes([own('Hey, Bret.')]), [])
        spent = jokes.spent_shapes([own('You arranged those snacks like it needs a logistics department.')])
        self.assertEqual(len(spent), 1)
        self.assertIn('office', spent[0])
        self.assertEqual(jokes.spent_shapes([{'role': 'user', 'content': 'My department has a new compliance officer.'},
                                             {'role': 'user', 'content': 'I used to want to be a pilot.'}]), [])
        both = jokes.spent_shapes([own('Given my landing record, I object.'), own('Very efficient operation.')])
        self.assertEqual(len(both), 2)


class PetWalksInTests(memory_tests.MemoryFixture):
    def test_a_pet_walking_in_gets_a_hello_then_his_own_question_about_it(self):
        from r3x.runtime import VoiceRuntime
        from tests.test_pet_arrival import dog
        from tests.test_proactive import face
        bret = self.enroll('Bret')
        album = PhotoMemory(self.path.parent / 'photos'); album.start()
        self.state.photo_memory = album
        album.data['entities']['max-id'] = {'name': 'Max', 'kind': 'pet', 'notes': 'Dark coat.', 'references': [],
                                            'history': []}
        rows = [dog(0)]
        self.state.objects = SimpleNamespace(snapshot=lambda: tuple(rows), inspect=lambda target: (
            photo_tests.image(), {'target_id': 'object-dog', 'label': 'dog', 'box': [.4, .4, .2, .2]}, photo_tests.image()))
        class Brain:
            def reply(brain, snapshot, cancel):
                step = snapshot.context.get('agency_step') or {}
                if snapshot.context.get('trigger') == 'pet_arrival' and not snapshot.continuation:
                    yield ToolCall('look', 'inspect_visual_memory', {'intent': 'look', 'target_id': 'object-dog',
                        'entity_ids': ['max-id'], 'question': 'Which saved pet is this?'})
                elif snapshot.continuation:
                    yield "Oh look, it's Max, back to inspect the premises."
                elif step.get('pet_question'):
                    assert 'max_came_home' in [r['topic'] for r in snapshot.context['curiosity']['still_unknown']]
                    yield say('Tell me about when you first got Max.', asked_about='max_came_home')
                else:
                    yield say('Evening, Bret.')
        trace = self.state.trace
        session = Session('session', Brain(), OutputOwner(SyntheticTTS(), timing_tests.ClockSink(trace), trace),
                          trace, self.state)
        self.addCleanup(session.close)
        session.agency_planner = agency_tests.Planner()
        runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace,
                               perception=SimpleNamespace(camera=SimpleNamespace()))
        runtime.started_at = 0
        runtime.camera.latest = lambda now: [face(now, bret, 'bret')]
        trace.at = 150
        self.state.agency.install(agency_tests.draft(), '[]', {}, ['contribute'], self.state.agency.revision, 150.)
        for at in (197, 198, 199, 200):
            runtime.arrivals.observe([face(at, bret, 'bret')], at)
            rows[:] = [dog(at)]
            self.state.present.saw_things(rows, at)
        for entry in runtime.arrivals.seen.values():
            entry['used'] = True                      # Bret was already greeted.
        trace.at = runtime.audio_seen_at = 200
        self.assertEqual(runtime.proactive_tick().completed_text, ["Oh look, it's Max, back to inspect the premises."])
        record = self.state.agency.record
        self.assertTrue(record['steps'][record['cursor']].get('pet_question'))
        result = None
        for _ in range(6):                            # The next lull.
            trace.at = runtime.audio_seen_at = trace.at + 5
            result = runtime.proactive_tick() or result
            if result and result.completed_text: break
        self.assertEqual(result.completed_text, ['Tell me about when you first got Max.'])
        self.assertEqual([dict(r) for r in self.store.rows('SELECT pet_id, topic FROM curiosity_asked')],
                         [{'pet_id': 'max-id', 'topic': 'max_came_home'}])


class SpentQuestionBeatTests(memory_tests.MemoryFixture):
    setup_owner = agency_tests.PlanTests.setup_owner
    tick = agency_tests.PlanTests.tick
    visible = agency_tests.GoalTests.visible

    def test_a_planned_question_already_asked_is_let_go_without_a_model_call(self):
        # Paid probe, October 24: "What do you do for work, Sam?" came again after Sam had answered.
        self.setup_owner()
        person = self.enroll(name='Bret')
        self.visible(person, 'bret', 100)
        def plan(facts, cancel):
            value = agency_tests.draft()
            value['scope'] = 'audience'
            value['steps'] = [{'kind': 'contribute', 'instruction': 'Ask what he does for work.', 'evidence_ids': [],
                               'wake': {'when': 'quiet', 'seconds': 5}, 'follows_previous': False,
                               'curious_about': 'work'},
                              {'kind': 'contribute', 'instruction': 'A jab about his commute.', 'evidence_ids': [],
                               'wake': {'when': 'quiet', 'seconds': 5}, 'follows_previous': False,
                               'curious_about': None}]
            return value
        self.planner.choose = plan
        self.executor.choose = lambda snapshot, cancel: iter([say(
            (snapshot.context.get('agency_step') or {}).get('instruction') or 'Hey, Bret.')])
        with self.store.transaction() as conn:   # Asked a moment ago, by a reply.
            curiosity.record(conn, {'topic': 'work'}, person, self.state.wall_clock())
        self.tick(100)
        self.tick(self.trace.at + 9)
        steps = [s.context['agency_step']['instruction'] for s in self.executor.snapshots if s.context.get('agency_step')]
        self.assertEqual(steps, ['A jab about his commute.'])
        self.assertTrue([r for r in self.trace.rows if r['event'] == 'agency_step_finished'
                         and r.get('revision_reason') == 'curiosity_already_asked'])
