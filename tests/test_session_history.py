import unittest
from r3x.history import TurnHistory
from r3x.contracts import ProviderFailure, Transcript
from r3x.session import Session
from r3x.output import TextOutput
from tests.test_memory_core import MemoryFixture, observation, B
from tests.test_voice_core import Brain


class HistoryTests(unittest.TestCase):
    def test_budget_evicts_whole_turn_with_evidence_and_tool_receipt(self):
        h=TurnHistory()
        h.begin_turn()
        h.extend([{'role':'developer','content':'old speaker evidence'},
                  {'role':'user','content':'old utterance'},
                  {'type':'function_call','call_id':'old'},
                  {'type':'function_call_output','call_id':'old','output':'{}'}])
        for i in range(30):
            h.begin_turn()
            h.extend([{'role':'developer','content':f'speaker {i}'},
                      {'role':'user','content':str(i)+'x'*1000},
                      {'role':'assistant','content':'heard answer'}])
        messages=h.messages()
        self.assertLessEqual(h.size(messages),h.max_characters)
        self.assertNotIn('old',str(messages))
        for turn in h.turns:
            self.assertEqual([m['role'] for m in turn],['developer','user','assistant'])
        h.clear()
        self.assertEqual(h.messages(),())
        self.assertEqual(TurnHistory().messages(),())

    def test_oversized_current_turn_is_not_silently_clipped(self):
        h=TurnHistory()
        text='x'*(h.max_characters+1)
        h.append({'role':'user','content':text})
        with self.assertRaises(ProviderFailure):h.messages()
        self.assertEqual(h[0]['content'],text)


class SessionContextTests(MemoryFixture):
    def test_relationship_survives_twenty_turns_without_becoming_identity_or_fact(self):
        brain=Brain('Thanks for telling me.')
        session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        for i in range(21):
            text="I'm traveling with my dad. Please skip the story metaphors." if i==0 else f'We passed rest stop number {i}.'
            self.begin(text)
            session.process_turn(self.state.current[1],Transcript(text,-.01,True),0,observation())
        self.begin('What did the other speaker say?',B)
        session.process_turn(self.state.current[1],self.state.current[2],0,observation(B))
        snapshot=brain.snapshots[-1]
        self.assertIn("I'm traveling with my dad.",str(snapshot.messages))
        self.assertIn('skip the story metaphors',str(snapshot.messages))
        self.assertEqual(snapshot.speaker,'unresolved')
        self.assertIn('source_turn_id',str(snapshot.messages))
        self.assertEqual(self.store.claims(include_candidates=True),[])
        self.assertEqual(self.store.people(),[])
        self.assertEqual(brain.calls,22)
        fresh=Session('next',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        self.assertEqual(fresh.history_messages(),())
        session.close()

    def test_forget_clears_long_context_before_result_continuation(self):
        from r3x.contracts import ToolCall
        from tests.test_semantic_session import Brain as ToolBrain
        person=self.enroll()
        brain=ToolBrain(lambda snapshot:ToolCall('forget','forget_person',{'person_id':person}))
        session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        for i in range(24):
            session.history.begin_turn()
            session.history.append({'role':'user','content':f'private-context-marker {i}'})
        self.begin('Forget me.')
        result=session.process_turn(self.state.current[1],self.state.current[2],0,observation())
        self.assertEqual(result.status,'completed')
        self.assertEqual(len(brain.requests),2)
        self.assertNotIn('private-context-marker',str(brain.requests[-1].messages))
        self.assertNotIn('private-context-marker',str(session.history_messages()))
        session.close()
