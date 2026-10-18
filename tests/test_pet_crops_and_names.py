"""October 18 run (a white chihuahua held in the owner's arms, two black-and-white dogs, and a
friend whose name the transcriber spelled five ways); synthetic frames and scripted calls only."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from r3x.contracts import Cancellation, ToolCall
from r3x.objects import Objects, pet_box
from r3x.photo_memory import PhotoMemory
from r3x.providers import Capability
from r3x.scene import encode_frame
from r3x.vision import Frame
from tests.test_memory_core import MemoryFixture


def image():
    return encode_frame(Frame('test-frame', 'fake', 'session', 100., 4, 4, bytes([20, 80, 100]) * 16))[0]


class PetViewTests(unittest.TestCase):
    def observer(self, clock, detections):
        detector = SimpleNamespace(capability=Capability('test', 'fake', 'test', None, ()), detect=lambda f: list(detections[0]))
        return Objects(SimpleNamespace(session_id='session'), detector, clock=lambda: clock[0])

    def test_each_dog_seen_in_the_last_seconds_gets_its_own_close_crop(self):
        clock = [100.]
        held = {'label': 'dog', 'score': .5, 'box': [.45, .4, .06, .14]}     # Small, in the owner's arms.
        couch = {'label': 'dog', 'score': .8, 'box': [.15, .5, .13, .3]}
        detections = [[held, couch]]
        observer = self.observer(clock, detections)
        observer.observe(Frame('a', 'fake', 'session', clock[0], 2, 2, bytes(12)))
        clock[0] += 1.5; detections[0] = [couch]                               # The held dog flickers out.
        observer.observe(Frame('b', 'fake', 'session', clock[0], 2, 2, bytes(12)))
        clock[0] += 1.5; detections[0] = []                                    # And then both.
        observer.observe(Frame('c', 'fake', 'session', clock[0], 2, 2, bytes(12)))
        with patch('r3x.objects.crop_image', lambda frame, box=None: (frame.frame_id, tuple(box))):
            views = observer.pet_views()
        self.assertEqual([t['where'] for _, t in views], ['left of the view', 'middle of the view'])
        self.assertEqual([crop[0] for crop, _ in views], ['b', 'a'])          # Each from the frame it was seen in.
        self.assertTrue(all(t['target_id'] and t['label'] == 'dog' for _, t in views))
        self.assertEqual(views[1][1]['box'], pet_box(held['box']))             # Widened around the dog.
        clock[0] += 11
        with patch('r3x.objects.crop_image', lambda frame, box=None: None):
            self.assertEqual(observer.pet_views(), [])                         # Old sightings are gone.

    def test_a_pet_box_is_widened_but_stays_inside_the_picture(self):
        self.assertEqual([round(v, 3) for v in pet_box([.45, .4, .08, .2])], [.43, .35, .12, .3])
        self.assertEqual([round(v, 3) for v in pet_box([0., .9, .2, .1])], [0., .875, .25, .125])


class PetPhotoRules(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); self.album.start()

    def teach(self, target, kind='pet', name='Nova'):
        evidence = self.album.offer(image(), target, 100.)
        call = ToolCall('t' + name + str(len(self.album.data['operations'])), 'remember_object',
                        {'evidence_id': evidence, 'entity_id': None, 'name': name, 'kind': kind, 'notes': 'x',
                         'source_turn_id': 'turn', 'text': 'Got it.'})
        return self.album.apply(call, {'turn_id': 'turn', 'session_id': 's', 'text': f'This is {name}.'}, 100.)

    def test_a_pet_is_never_saved_from_the_whole_room(self):
        with self.assertRaisesRegex(ValueError, 'PetPhotoNeedsCloseCrop'):
            self.teach({'label': 'whole view', 'box': [0, 0, 1, 1]})
        self.assertEqual(self.teach({'label': 'dog', 'box': [.4, .3, .1, .2], 'target_id': 'object-1'})['status'], 'saved')
        self.assertEqual(self.teach({'label': 'whole view', 'box': [0, 0, 1, 1]}, kind='object', name='Lamp')['status'],
                         'saved')                                               # Objects and rooms may still use the view.

    def test_old_whole_room_pet_photos_are_never_compared(self):
        self.teach({'label': 'dog', 'box': [.4, .3, .1, .2], 'target_id': 'object-1'})
        key = next(iter(self.album.data['entities']))
        entity = self.album.data['entities'][key]
        entity['references'].append({**entity['references'][0], 'target': {'label': 'whole view', 'box': [0, 0, 1, 1]}})
        images, labels = self.album.references()
        self.assertEqual(len(images), 1)                                       # Only the close crop.
        self.assertEqual(self.album.references(kinds={'object'}), ((), []))


class NearSpellingTests(MemoryFixture):
    def introduce_other(self, text, name, person_id=None):
        self.begin(text)
        return self.call('introduce_person', {'name': name, 'aliases': [], 'subject': 'other', 'person_id': person_id,
                                              'face_track_id': None})

    def test_a_near_spelling_of_someone_named_this_conversation_is_that_person(self):
        self.enroll('Bret')
        self.introduce_other('To your left is my friend Exudica.', 'Exudica')
        friend = self.store.resolve('Exudica')[0]
        result = self.introduce_other('This is Exodia.', 'Exodia')
        self.assertEqual(result.outcome, 'ok')
        self.assertEqual([p['name'] for p in self.store.people() if p['name'].startswith('Ex')], ['Exudica'])
        self.begin('There is no dog named Exotica.')
        self.assertEqual(self.state.context()['name_spellings'],
                         [{'heard': 'Exotica', 'known_as': 'Exudica', 'person_id': friend['id']}])
        self.begin('You keep getting the name wrong. It is Exudica.')
        self.assertEqual(self.state.context()['name_spellings'], [])           # The right spelling needs no hint.

    def test_a_near_spelling_never_reaches_someone_not_named_lately_or_two_people(self):
        self.enroll('Bret')
        self.introduce_other('This is Michael.', 'Michael')
        self.state.trace.at += 4000; self.at += 4000                         # Over an hour later.
        self.introduce_other('This is Michelle.', 'Michelle')
        self.assertEqual(sorted(p['name'] for p in self.store.people() if p['name'].startswith('Mich')),
                         ['Michael', 'Michelle'])
        self.begin('Michael is back too.')                                    # Now both were named lately.
        self.begin('Michel said hi.')
        self.assertEqual(self.state.context()['name_spellings'], [])          # Two could fit: no guess.
        # Known limit: a second, different person with a near name introduced within half an
        # hour is taken as the first ("Exodia" right after "Exudica" looks exactly the same).

    def test_a_pet_may_not_take_a_near_spelling_of_a_person_named_this_conversation(self):
        self.enroll('Bret')
        self.introduce_other('To your left is my friend Exudica.', 'Exudica')
        self.assertIsNotNone(self.state.near_named('Exodia'))
        self.assertIsNone(self.state.near_named('Bret'))                       # Exact names never come here.
        self.assertIsNone(self.state.near_named('Max'))


if __name__ == '__main__':
    unittest.main()
