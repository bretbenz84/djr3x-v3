"""November 24 (run ab1cd64f): Rex asked "What's the weirdest thing that dog does?" about Toby; the owner's
"Yeah, he licks my face." was refused for not naming Toby, and the answer after the refusal began "The write
failed, so I'll just reply. Say [laughs] a dog that...". Synthetic voices and fake providers only: these prove
the contract, not what a live Claude model now says after a refused save."""
import json
import unittest
from pathlib import Path

from r3x.contracts import Cancellation, Snapshot, ToolCall
from r3x.output import OutputOwner
from r3x.photo_memory import PhotoMemory
from r3x.scenario import SyntheticSink, SyntheticTTS
from tests import test_proactive_core
from tests.test_claude_replies import HEARD, brain, sent, stream
from tests.test_memory_core import MemoryFixture, A, observation


class PetNoteTests(MemoryFixture):
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    turn = test_proactive_core.InitiativeCoreTests.turn

    def album(self):
        album = PhotoMemory(Path(self.temp.name).resolve() / 'photos'); album.start()
        album.data['entities'] = {
            'toby-id': {'name': 'Toby', 'kind': 'pet', 'notes': 'Raids the trash.', 'references': [], 'history': []},
            'max-id': {'name': 'Max', 'kind': 'pet', 'notes': 'Small dark dog.', 'references': [], 'history': []}}
        self.state.photo_memory = album
        self.addCleanup(album.close)
        return album

    def run_exchange(self, gap=10.):
        """Rex asks about Toby by name; the answer leaves the name out and the model notes it on Toby."""
        self.enroll()
        album, requests = self.album(), []
        class Brain:
            def reply(_, snapshot, cancel):
                requests.append(snapshot)
                if len(requests) == 1:
                    yield "Toby gets through every door first. What's the weirdest thing that dog does?"
                elif len(requests) == 2:
                    yield ToolCall('note', 'edit_object_memory', {'entity_id': 'toby-id', 'action': 'correct',
                        'name': 'Toby', 'notes': "Licks Bret's face.", 'remove_notes': None,
                        'source_turn_id': snapshot.context['source_turn_id'],
                        'text': "A dog that raids the trash then kisses you. That's a biohazard with a tail."})
                else:
                    yield 'Trash first, then your face. Romantic.'
        self.setup_core(Brain(), OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace))
        self.trace.at = 100.
        self.turn('What do you think of my dogs?', observation(A))
        self.trace.at += gap
        return album, requests, self.turn('Yeah, he licks my face.', observation(A))

    def test_the_answer_to_his_own_question_about_a_pet_goes_on_that_pet(self):
        album, requests, result = self.run_exchange()
        self.assertEqual(' '.join(result.completed_text),
                         "A dog that raids the trash then kisses you. That's a biohazard with a tail.")
        self.assertEqual(album.data['entities']['toby-id']['notes'], "Raids the trash.; Licks Bret's face.")
        self.assertEqual(album.data['entities']['max-id']['notes'], 'Small dark dog.')
        self.assertEqual(len(requests), 2)   # Saved at once: no second answer about a refusal.
        # Only a note: a photo of "him" still needs a person to have named the pet.
        self.assertEqual(self.state.recently_named_pets(), ())
        self.assertEqual(self.state.recently_named_pets(notes=True), ('toby-id',))

    def test_long_after_his_question_the_note_is_refused_and_never_talked_about(self):
        album, requests, result = self.run_exchange(gap=200.)
        self.assertEqual(album.data['entities']['toby-id']['notes'], 'Raids the trash.')
        self.assertEqual(' '.join(result.completed_text), 'Trash first, then your face. Romantic.')
        self.assertIn('Never tell them a save failed', requests[2].messages[-1]['content'])
        self.assertEqual(json.loads(requests[2].continuation[-1]['output'])['outcome'], 'rejected')


class RefusedSaveAnswerTests(unittest.TestCase):
    """Claude's answer after a refused save thinks first, so its working-out is not spoken as it streams."""
    def continuation(self, outcome, name='edit_object_memory'):
        return ({'type': 'function_call', 'call_id': 'c1', 'name': name, 'arguments': '{}'},
                {'type': 'function_call_output', 'call_id': 'c1',
                 'output': json.dumps({'outcome': outcome, 'reason': 'PhotoSubjectNeedsExplicitName'})})

    def thinking(self, continuation, context=None):
        made = brain(stream(('text', 'Trash first, then your face. Romantic.')))
        said = ''.join(made.reply(Snapshot('s', 't', 0, HEARD, context={'source_turn_id': 't', **(context or {})},
                                           continuation=continuation), Cancellation()))
        self.assertEqual(said, 'Trash first, then your face. Romantic.')
        return sent(made)['thinking']

    def test_a_refused_save_thinks_first_and_other_results_start_straight_away(self):
        self.assertEqual(self.thinking(self.continuation('rejected')), {'type': 'adaptive'})
        self.assertEqual(self.thinking(self.continuation('rejected', 'record_claim')), {'type': 'adaptive'})
        self.assertEqual(self.thinking(self.continuation('ok')), {'type': 'between_tools'})
        self.assertEqual(self.thinking(self.continuation('rejected', 'name_room')), {'type': 'between_tools'})

    def test_claude_is_not_told_to_ask_for_the_name_and_openai_keeps_its_wording(self):
        from r3x.adapters.claude import ClaudeBrain
        from r3x.adapters.hosted import OpenAIBrain
        from tests.test_claude_replies import PromptCopyTests
        context = {'source_turn_id': 't', 'state_revision': 0,
                   'photo_memory': {'revision': 0, 'objects': [],
                                    'entities': [{'entity_id': 'toby-id', 'name': 'Toby'}]}}
        claude = PromptCopyTests.payload(self, ClaudeBrain('c'), context)
        openai = PromptCopyTests.payload(self, OpenAIBrain('o'), context)
        asks = 'When PhotoSubjectNeedsExplicitName rejects a write, ask for the name'
        self.assertIn(asks, openai)
        self.assertNotIn(asks, claude)
        self.assertIn('A note that did not go through is never mentioned', claude)


if __name__ == '__main__':
    unittest.main()
