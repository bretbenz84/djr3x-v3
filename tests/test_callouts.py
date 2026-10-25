"""Callouts (owner, October 25): V2's celebrity bits (Jeff of History Hunters fame, T'Joy, PJ, JT), added by
voice. Anyone can add one, about themself, someone else or a saved pet; asked to stop, Rex switches it off;
asked never to call them that again, he deletes it. He is offered an entrance when they arrive (at most every
three hours) and a callback at most every fifteen minutes, written fresh by the reply model. Production state
owner, Session and request builder with a fake clock, scripted replies, made-up people and temporary stores:
these prove what is kept, shown and recorded, not how funny the lines are."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from r3x.adapters.hosted import PERSONA
from r3x.agency import PLANNER_INSTRUCTIONS
from r3x.callouts import ENTRANCE_GAP_SECONDS, RIFF_SECONDS
from r3x.contracts import ToolCall
from r3x.field_guide import GUIDE
from r3x.jokes import WRITER_INSTRUCTIONS
from r3x.output import OutputOwner
from r3x.photo_memory import PhotoMemory
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import TOOLS, grounded_tools
from r3x.session import Session
from tests import test_photo_memory as photo_tests
from tests.test_context_diet import send
from tests.test_memory_core import C, MemoryFixture
from tests.test_proactive import event
from tests.test_voice_core import Brain


class CalloutFixture(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.clock = 1800000000.
        self.state.wall_clock = lambda: self.clock

    def callout(self, action, subject='person', name=None, famous_for=None, angles=(), off_limits=(),
                callout_id=None, text='Noted.'):
        return self.call('callout', {'action': action, 'callout_id': callout_id, 'subject': subject, 'name': name,
                                     'famous_for': famous_for, 'angles': list(angles), 'off_limits': list(off_limits),
                                     'source_turn_id': self.state.current[0], 'text': text})

    def here(self, **context):
        return (self.state.callout_context(context) or {}).get('here', [])

    def person(self, name):
        return self.store.resolve(name)[0]['id']


class CalloutStateTests(CalloutFixture):
    def test_a_callout_about_someone_else_is_kept_on_their_record_as_his_material(self):
        bret = self.enroll('Bret')
        self.begin('PJ is a huge Kings fan. Whenever he shows up, treat him like Kings royalty.')
        result = self.callout('add', name='PJ', famous_for='Sacramento Kings royalty',
                              angles=['lighting the beam', 'cowbells'])
        self.assertEqual((result.outcome, result.data['status'], result.data['about']), ('ok', 'on', 'PJ'), result)
        row = self.store.rows('SELECT * FROM callouts')[0]
        self.assertEqual((row['person_id'], row['added_by']), (self.person('PJ'), bret))   # A name-only record for PJ.
        self.assertEqual(self.store.claims(include_candidates=True), [])   # Never a fact about him (V2's JT bug).
        self.begin("What's PJ's bit again?")
        recall = self.call('recall', {'subject_id': None, 'query': 'PJ'})
        self.assertEqual(recall.data['people'][0]['callout']['famous_for'], 'Sacramento Kings royalty')

    def test_anyone_can_add_one_even_a_voice_he_does_not_know(self):
        self.enroll('Bret')
        self.begin('My buddy JT is a major volleyball celebrity. Make a fuss whenever he is here.', C)
        result = self.callout('add', name='JT', famous_for='major volleyball celebrity', angles=['aging-athlete bones'])
        self.assertEqual(result.outcome, 'ok', result)
        self.assertIsNone(self.store.rows('SELECT added_by FROM callouts')[0]['added_by'])

    def test_their_own_and_a_change_that_adds_an_angle_and_drops_an_off_limits_one(self):
        self.enroll('Bret')
        self.begin('Treat me like a celebrity: I am the reigning cornhole champion of Elk Grove.')
        self.assertEqual(self.callout('add', subject='speaker', famous_for='reigning cornhole champion of Elk Grove',
                                      angles=['bean bags']).outcome, 'ok')
        self.begin('Also roast my terrible throwing arm, but no more bean bag jokes.')
        changed = self.callout('change', subject='speaker', angles=['his terrible throwing arm'], off_limits=['bean bags'])
        self.assertEqual((changed.data['famous_for'], changed.data['angles'], changed.data['off_limits']),
                         ('reigning cornhole champion of Elk Grove', ['his terrible throwing arm'], ['bean bags']))
        self.begin('Change it.')
        self.assertEqual(self.callout('change', subject='speaker').reason, 'CalloutChangeNeedsSomething')

    def test_stop_switches_it_off_resume_brings_it_back_and_remove_deletes_it(self):
        self.enroll('Bret')
        self.begin('PJ is Kings royalty, give him the treatment.')
        self.callout('add', name='PJ', famous_for='Sacramento Kings royalty')
        pj = self.person('PJ')
        seen = lambda: [e['about'] for e in self.here(faces=[{'identity': pj}]) if e.get('status') != 'stopped']
        self.assertEqual(seen(), ['PJ'])
        self.begin('Rex, stop the Kings stuff.')
        self.assertEqual(self.callout('stop', name='PJ').data['status'], 'stopped')
        self.assertEqual(seen(), [])
        listed = self.here(faces=[{'identity': pj}])
        self.assertEqual(listed, [{'callout_id': listed[0]['callout_id'], 'about': 'PJ', 'status': 'stopped'}])
        self.begin('Fine, you can do the Kings bit again.')
        callout_id = listed[0]['callout_id']                                   # Listed so resume can find it.
        self.assertEqual(self.callout('resume', callout_id=callout_id).data['status'], 'on')
        self.assertEqual(seen(), ['PJ'])
        self.begin('Never call him that again.')
        self.assertEqual(self.callout('remove', callout_id=callout_id).data['status'], 'removed')
        self.assertEqual((self.store.rows('SELECT * FROM callouts'), seen()), ([], []))
        self.begin('Stop calling PJ that.')
        self.assertEqual(self.callout('stop', name='PJ').reason, 'NoCalloutForThem')

    def test_someone_talked_about_lately_is_shown_as_not_here_so_follow_ups_find_them(self):
        # Hosted probe, October 25: with PJ out of view, "also work in the cowbells" and "PJ says never call
        # him that again" named nobody the state owner could find.
        self.enroll('Bret')
        self.begin('PJ is a huge Kings fan. Treat him like Kings royalty.')
        self.callout('add', name='PJ', famous_for='Sacramento Kings royalty')
        self.begin('Also work in the cowbells.')
        entry = self.here()[0]
        self.assertEqual((entry['about'], entry['here']), ('PJ', False))
        self.assertEqual(self.callout('change', callout_id=entry['callout_id'], angles=['cowbells']).data['angles'],
                         ['cowbells'])
        self.assertNotIn('here', self.here(faces=[{'identity': self.person('PJ')}])[0])
        self.state.trace.at += 601
        self.assertEqual(self.here(), [])                                      # Not talked about for ten minutes.

    def test_forgetting_someone_removes_callouts_about_them_and_the_ones_they_added(self):
        bret = self.enroll('Bret')
        self.begin('PJ is Kings royalty.')
        self.callout('add', name='PJ', famous_for='Sacramento Kings royalty')
        self.begin('Treat me like the cornhole champion.')
        self.callout('add', subject='speaker', famous_for='cornhole champion')
        self.begin('Forget PJ.')
        self.assertEqual(self.call('forget_person', {'person_id': self.person('PJ')}).outcome, 'ok')
        self.assertEqual([r['person_id'] for r in self.store.rows('SELECT person_id FROM callouts')], [bret])
        self.begin('Forget me.')
        self.assertEqual(self.call('forget_person', {'person_id': bret}).outcome, 'ok')
        self.assertEqual(self.store.rows('SELECT * FROM callouts'), [])

    def test_a_saved_pet_gets_one_and_an_unsaved_pet_never_becomes_a_person(self):
        album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); album.start()
        self.state.photo_memory = album
        album.data['entities']['max-id'] = {'name': 'Max', 'kind': 'pet', 'notes': '', 'references': [], 'history': []}
        self.enroll('Bret')
        self.begin('Max is the most spoiled dog in Sacramento. Give him the red carpet.')
        result = self.callout('add', subject='pet', name='Max', famous_for='the most spoiled dog in Sacramento',
                              angles=['the red carpet'])
        self.assertEqual((result.outcome, result.data['subject_kind'], result.data['about']), ('ok', 'pet', 'Max'))
        self.begin('My dog Biscuit is famous too.')
        self.assertEqual(self.callout('add', subject='pet', name='Biscuit', famous_for='famous').reason,
                         'NoSavedPetByThatName')
        self.assertEqual(self.store.resolve('Biscuit'), [])
        self.begin('Where did Max go?')
        self.assertEqual([e['about'] for e in self.here()], ['Max'])          # Named lately.
        arrival = self.here(trigger='pet_arrival')[0]
        self.assertEqual((arrival['pet'], arrival['entrance_due']), (True, True))

    def test_the_entrance_is_due_only_for_whoever_arrives_and_at_most_every_three_hours(self):
        self.enroll('Bret')
        self.begin('PJ is Kings royalty, and JT is a volleyball celebrity.')
        self.callout('add', name='PJ', famous_for='Sacramento Kings royalty', angles=['lighting the beam'])
        self.callout('add', name='JT', famous_for='major volleyball celebrity')
        pj, jt = self.person('PJ'), self.person('JT')
        arriving = {'faces': [{'identity': pj}, {'identity': jt}], 'trigger': 'visual_arrival',
                    'visual_arrival': {'person_id': pj}}
        offered = self.state.callout_context(arriving)
        entries = {e['about']: e for e in offered['here']}
        self.assertTrue(entries['PJ']['entrance_due'])
        self.assertNotIn('entrance_due', entries['JT'])                        # JT was already here.
        heard = self.state.callout_heard(offered, 'Hold on. Sacramento Kings royalty just walked in. Light the beam.')
        self.assertEqual(heard, [(entries['PJ']['callout_id'], 'entrance')])  # Nothing about JT was said.
        pj_entry = lambda: next(e for e in self.here(**arriving) if e['about'] == 'PJ')
        self.clock += ENTRANCE_GAP_SECONDS - 60
        self.assertFalse(pj_entry()['entrance_due'])
        self.clock += 60
        self.assertTrue(pj_entry()['entrance_due'])

    def test_a_callback_waits_fifteen_minutes_after_any_callout_line_or_change(self):
        self.enroll('Bret')
        self.begin('PJ is Kings royalty.')
        self.callout('add', name='PJ', famous_for='Sacramento Kings royalty', angles=['cowbells'])
        around = {'faces': [{'identity': self.person('PJ')}]}
        self.assertFalse(self.here(**around)[0]['riff_now'])                   # Just set up: that was the riff.
        self.clock += RIFF_SECONDS
        offered = self.state.callout_context(around)
        self.assertTrue(offered['here'][0]['riff_now'])
        self.assertEqual(self.state.callout_heard(offered, 'Nice weather tonight.'), [])
        self.assertEqual(len(self.state.callout_heard(offered, 'Somebody hand the king his cowbell.')), 1)
        entry = self.here(**around)[0]
        self.assertFalse(entry['riff_now'])
        self.assertEqual(entry['earlier_lines'][0]['line'], 'Somebody hand the king his cowbell.')
        self.clock += RIFF_SECONDS
        self.assertTrue(self.here(**around)[0]['riff_now'])

    def test_none_while_he_guards_in_sentry_mode(self):
        self.enroll('Bret')
        self.begin('PJ is Kings royalty.')
        self.callout('add', name='PJ', famous_for='Sacramento Kings royalty')
        self.state.sentry.phase = 'guarding'
        self.assertEqual(self.here(faces=[{'identity': self.person('PJ')}]), [])

    def test_an_introduced_newcomer_with_a_callout_gets_the_entrance_in_the_introduction_result(self):
        self.enroll('Bret')
        self.begin('My dad Jeff is famous for History Hunters. Whenever he comes over, treat him like a celebrity.')
        self.callout('add', name='Jeff', famous_for='History Hunters fame', angles=['forgotten cemeteries'])
        self.begin('Rex, this is my dad, Jeff.')
        result = self.call('introduce_person', {'name': 'Jeff', 'aliases': [], 'subject': 'other', 'person_id': None,
                                                'face_track_id': None})
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual(result.data['person_id'], self.person('Jeff'))
        self.assertEqual((result.data['callout']['famous_for'], result.data['callout']['entrance_due']),
                         ('History Hunters fame', True))


class PetCalloutForgetTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); self.album.start()
        self.state.photo_memory = self.album
        cat = {'target_id': 'object-1', 'label': 'cat', 'box': [0, 0, .5, .5]}
        self.state.objects = SimpleNamespace(snapshot=lambda: ({**cat, 'score': .7},),
                                             inspect=lambda target: (photo_tests.image(), cat, photo_tests.image()))

    teach = photo_tests.PhotoTests.teach
    session = photo_tests.PhotoTests.session

    def test_forgetting_a_pet_removes_its_callout(self):
        key = self.teach()
        self.begin('Max is the most spoiled dog in Sacramento.')
        self.assertEqual(self.call('callout', {'action': 'add', 'callout_id': None, 'subject': 'pet', 'name': 'Max',
            'famous_for': 'the most spoiled dog in Sacramento', 'angles': [], 'off_limits': [],
            'source_turn_id': self.state.current[0], 'text': None}).outcome, 'ok')
        class Forget:
            def reply(brain, snapshot, cancel):
                yield ToolCall('forget', 'edit_object_memory', {'entity_id': key, 'action': 'forget', 'name': 'Max',
                    'notes': None, 'remove_notes': None, 'source_turn_id': snapshot.context['source_turn_id'],
                    'text': 'Done.'})
        session, receipt = self.session(Forget(), 'Forget Max.')
        session.close()
        self.assertEqual(receipt.completed_text, ['Done.'])
        self.assertEqual(self.store.rows('SELECT * FROM callouts'), [])


class CalloutSessionTests(CalloutFixture):
    def greet(self, person, line):
        brain = Brain(line)
        session = Session('later', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                          self.state.trace, self.state)
        self.addCleanup(session.close)
        result = session.process_opportunity(replace(event(person), session_id='later'), 0, lambda: True)
        self.assertEqual(result.status, 'completed')
        return brain.snapshots[0].context['callouts']['here'][0]

    def test_an_arrival_gets_the_entrance_and_the_heard_greeting_is_kept_for_variety(self):
        jt = self.enroll('JT')
        self.begin('Treat me like a major volleyball celebrity.')
        self.callout('add', subject='speaker', famous_for='major volleyball celebrity', angles=['aging-athlete bones'])
        line = 'JT detected. Volleyball royalty in the building; somebody bubble-wrap the bones.'
        self.assertTrue(self.greet(jt, line)['entrance_due'])
        self.assertEqual([(r['kind'], r['line']) for r in self.store.rows('SELECT kind,line FROM callout_lines')],
                         [('entrance', line)])
        self.clock += 20 * 60
        again = self.greet(jt, 'Back already?')
        self.assertFalse(again['entrance_due'])
        self.assertEqual(again['earlier_lines'][0]['line'], line)


class CalloutRequestTests(unittest.TestCase):
    def test_offered_on_a_persons_turn_only_with_the_shown_callouts_as_its_ids(self):
        names = lambda context: {d['name'] for d in grounded_tools({'event_id': 'e', **context})}
        self.assertIn('callout', names({'source_turn_id': 't'}))
        for trigger in ('visual_arrival', 'pet_arrival', 'conversation_lull', 'ambient'):
            self.assertNotIn('callout', names({'trigger': trigger}))
        offered = next(d for d in grounded_tools({'source_turn_id': 't', 'callouts': {'here': [{'callout_id': 'c1'}]}})
                       if d['name'] == 'callout')
        self.assertEqual(offered['parameters']['properties']['callout_id']['enum'], ['c1', None])
        self.assertIn('text', next(t for t in TOOLS if t['name'] == 'callout')['parameters']['required'])

    def test_a_due_entrance_names_its_situation_and_the_fields_meaning_travels_once(self):
        entry = {'callout_id': 'c1', 'about': 'PJ', 'famous_for': 'Sacramento Kings royalty', 'angles': [],
                 'off_limits': [], 'added_by': 'Bret', 'riff_now': False, 'earlier_lines': [], 'entrance_due': True}
        payload, _ = send({'event_id': 'e', 'trigger': 'visual_arrival',
                           'callouts': {'here': [entry], 'meaning': GUIDE['callouts']}})
        sent = json.dumps(payload['input'])
        self.assertIn('callout_entrance', sent)
        self.assertNotIn(GUIDE['callouts'], sent)
        self.assertIn(GUIDE['callouts'], payload['instructions'])
        plain, _ = send({'source_turn_id': 't', 'callouts': {'here': [{**entry, 'entrance_due': False}]}})
        self.assertNotIn('callout_entrance', json.dumps(plain['input']))

    def test_bodies_are_fair_game_in_every_joke_rule(self):
        # Owner, October 25: "I don't know why bodies are off limits... He should be able to joke about people's bodies."
        impersonate = next(t for t in TOOLS if t['name'] == 'impersonate')['description']
        for text in (PERSONA, WRITER_INSTRUCTIONS, PLANNER_INSTRUCTIONS, impersonate):
            for forbidding in ('about bodies', 'bodies, identity', 'never their body', 'Never bodies', 'health, bodies'):
                self.assertNotIn(forbidding, text)
        for text in (PERSONA, WRITER_INSTRUCTIONS):
            self.assertIn('Bodies are fair game', text)


if __name__ == '__main__':
    unittest.main()
