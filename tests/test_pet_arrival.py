"""An animal coming into view starts a reaction, like a person walking in; synthetic only."""
from pathlib import Path
from types import SimpleNamespace
import unittest

from r3x.contracts import ToolCall
from r3x.output import TextOutput
from r3x.photo_memory import PhotoMemory
from r3x.present import Pets
from r3x.runtime import VoiceRuntime
from r3x.semantics import grounded_tools
from r3x.session import Session
from tests.test_memory_core import MemoryFixture
from tests.test_photo_memory import image
from tests.test_proactive_core import face


def dog(at, label='dog', target='object-dog'):
    return {'target_id': target, 'label': label, 'score': .6, 'box': [.4, .4, .2, .2], 'at': at, 'samples': 2}


class PetArrivalRules(unittest.TestCase):
    def test_a_flickering_cat_dog_detection_is_one_settled_arrival(self):
        # October 13: Max in Bret's arms was "cat", "dog" or nothing from one second to the next.
        pets = Pets()
        for at, label in ((100, 'cat'), (101, 'dog'), (103, None), (104, 'cat')):
            pets.observe([dog(at, label)] if label else [], at)
        event = pets.take('session', 104, 0.)
        self.assertEqual((event.kind, event.target_track), ('pet_arrival', 'object-dog'))
        self.assertIsNone(pets.take('session', 105, 0.))           # Once per visit.

    def test_a_glimpse_is_not_an_arrival_and_a_return_after_a_while_is(self):
        pets = Pets()
        pets.observe([dog(100)], 100)
        self.assertIsNone(pets.take('session', 101, 0.))           # Not settled yet.
        for at in (101, 102, 103):
            pets.observe([dog(at)], at)
        self.assertIsNotNone(pets.take('session', 103, 0.))
        for at in (200, 202, 204):                                  # Back within the cooldown.
            pets.observe([dog(at)], at)
        self.assertIsNone(pets.take('session', 204, 0.))
        for at in (700, 702, 704):                                  # Gone a long while, back again.
            pets.observe([dog(at)], at)
        self.assertIsNotNone(pets.take('session', 704, 0.))

    def test_a_remembered_pet_between_detections_does_not_start_an_arrival(self):
        pets = Pets()
        for at in (100, 101, 103):
            pets.observe([{**dog(at), 'recently_seen': True}], at)
        self.assertIsNone(pets.take('session', 103, 0.))


class PetArrivalTurn(MemoryFixture):
    def test_max_coming_into_view_gets_looked_at_and_greeted_by_name(self):
        album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); album.start()
        self.state.photo_memory = album
        album.data['entities']['max-id'] = {'name': 'Max', 'kind': 'pet', 'notes': 'Dark coat, white blaze.',
                                            'references': [], 'history': []}
        rows = [dog(0)]
        self.state.objects = SimpleNamespace(snapshot=lambda: tuple(rows),
            inspect=lambda target: (image(), {'target_id': 'object-dog', 'label': 'dog', 'box': [.4, .4, .2, .2]}, image()))
        seen = []
        class Brain:
            def reply(brain, snapshot, cancel):
                seen.append(snapshot)
                if not snapshot.continuation:
                    assert snapshot.context['trigger'] == 'pet_arrival'
                    assert 'inspect_visual_memory' in {d['name'] for d in grounded_tools(snapshot.context)}
                    yield ToolCall('look', 'inspect_visual_memory', {'intent': 'look', 'target_id': 'object-dog',
                        'entity_ids': ['max-id'], 'question': 'Which saved pet is this?'})
                else:
                    yield "Oh look, it's Max! How's he behaving tonight?"
        trace = self.state.trace
        session = Session('session', Brain(), TextOutput(trace), trace, self.state)
        self.addCleanup(session.close)
        from tests.test_agency_plan import Planner
        session.agency_planner = Planner()
        runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, session, trace,
                               perception=SimpleNamespace(camera=SimpleNamespace()))
        runtime.started_at = 0
        runtime.camera.latest = lambda now: runtime.arrivals.faces
        for at in (197, 198, 199, 200):
            runtime.arrivals.observe([face(at, track='bret')], at)
            rows[:] = [dog(at, 'cat' if at % 2 else 'dog')]
            runtime.session.state.present.saw_things(rows, at)
        runtime.arrivals.seen['bret']['used'] = True   # Bret was already greeted.
        trace.at = runtime.audio_seen_at = 200
        result = runtime.proactive_tick()
        self.assertEqual(result.completed_text, ["Oh look, it's Max! How's he behaving tonight?"])
        self.assertEqual(seen[0].context['pet_arrival']['saved_pets'], [{'entity_id': 'max-id', 'name': 'Max'}])
        self.assertEqual(len(seen[1].images), 2)          # The view and the dog, compared with Max.


class AnimalAside(MemoryFixture):
    def test_talking_to_the_dog_quiets_rex_briefly_not_for_two_minutes(self):
        # October 13: "Max, why are you crying? Stop crying, Bubba." was correctly left alone,
        # then held Rex silent for two minutes until Bret said goodnight.
        from r3x.contracts import Transcript
        from tests.test_memory_core import observation
        self.begin('Max, why are you crying? Stop crying, Bubba.')
        brain = SimpleNamespace(reply=lambda snapshot, cancel: iter([ToolCall('q', 'remain_silent', {
            'reason': 'talking_to_animal', 'invitation_update': None, 'intent_update': None, 'next_opportunity': None})]))
        session = Session('session', brain, TextOutput(self.state.trace), self.state.trace, self.state)
        self.addCleanup(session.close)
        receipt = session.process_turn(self.state.current[1], Transcript('Max, why are you crying? Stop crying, Bubba.', -.01, True),
                                       0, observation())
        self.assertEqual(receipt.status, 'suppressed')
        now = self.state.trace.clock()
        self.assertLessEqual(session.proactive_hold_until - now, 20.)
        self.assertFalse(self.state.initiative.floor_held(now + 21))
