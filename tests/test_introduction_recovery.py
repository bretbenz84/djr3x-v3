"""Self-reported identity starts learning; it never manufactures recognition."""
from collections import deque
from dataclasses import replace
import math
from r3x.contracts import Cancellation, ToolCall
from r3x.speakers import Track, SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tests.test_memory_core import MemoryFixture, A, B, NS
from tests.test_face_identity import face


class IntroductionRecoveryTests(MemoryFixture):
    def introduce_current(self, name='Jeff', person=None, call_id=None):
        return self.call('introduce_person',dict(name=name,aliases=[],subject='self',person_id=person,face_track_id=None),call_id=call_id)

    def ambiguous_groups(self):
        for key, vector in [('old-one',(.4,math.sqrt(.84),0.)),('old-two',(.4,0.,math.sqrt(.84)))]:
            self.state.tracker.tracks[key] = Track(key, float('-inf'), deque([vector],maxlen=5),retained=True)

    def test_ambiguous_introduction_enrolls_from_new_speech_preserving_other_profile(self):
        bret = self.enroll('Bret', B)
        original = self.store.profiles(NS)[bret]
        self.ambiguous_groups()
        self.begin('My name is Jeff.', A)
        self.assertEqual(self.state.current[4].reason,'ambiguous_continuity')
        self.state.faces = (face(self.state.current[1].started_at),)
        result = self.introduce_current(call_id='intro')
        self.assertEqual(result.outcome,'ok', result)
        jeff = result.data['person_id'];key = result.data['track_id']
        self.assertEqual(result.data['identity_basis'],'self_report')
        self.assertEqual(result.data['face_status'],'saved')
        self.assertTrue(result.data['fresh_voice_track'])
        self.assertIsNone(self.state.current[4].person_id)
        self.assertEqual(self.state.context()['provisional_person_id'],jeff)
        self.assertNotIn(jeff,self.store.profiles(NS))
        self.assertEqual(len(self.state.enrollments[key].samples),1)
        repeat = self.introduce_current(call_id='intro')
        self.assertEqual(repeat.data, result.data)
        self.assertEqual(len(self.state.enrollments[key].samples),1)
        for text in ['Today we visited a museum.', 'The trip was very enjoyable.']:
            self.begin(text,A);self.state.advance_enrollment()
        self.assertEqual(self.store.person(jeff)['status'],'supported')
        self.assertEqual(self.store.profiles(NS)[bret], original)
        self.store.close();self.store.start()
        self.state = CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        self.begin('Hello again from Jeff today.',A)
        self.assertEqual(self.state.current[4].person_id,jeff)
        self.begin('Bret is speaking again now.',B)
        self.assertEqual(self.state.current[4].person_id,bret)

    def test_ambiguous_introduction_uses_production_session_tool_result(self):
        import json
        from r3x.contracts import Capture, Transcript
        from r3x.session import Session
        from r3x.output import TextOutput
        from tests.test_semantic_session import Brain
        from tests.test_memory_core import observation
        self.ambiguous_groups()
        brain=Brain(lambda snapshot:ToolCall('intro','introduce_person',
            dict(name='Jeff',aliases=[],subject='self',person_id=None,face_track_id=None)))
        session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        self.addCleanup(session.close)
        receipt=session.process_turn(Capture('introduction','session',b'x'*96000,97.,100.,100.),
            Transcript('My name is Jeff.',0.,True),0,observation())
        self.assertEqual(receipt.status,'completed')
        self.assertEqual(len(brain.requests),2)  # Main generation and existing tool-result continuation.
        result=json.loads(brain.requests[1].continuation[-1]['output'])
        self.assertEqual(result['outcome'],'ok')
        self.assertEqual(result['data']['identity_basis'],'self_report')
        self.assertEqual(self.store.people()[0]['name'],'Jeff')
        self.assertFalse(self.store.profiles(NS))

    def test_stalled_provisional_chain_can_be_reintroduced_without_importing_old_audio(self):
        person=self.introduce('Jeff',A)
        old=next(iter(self.state.enrollments))
        weak=(.4,math.sqrt(.84),0.)
        self.begin('I am Jeff, remember me.',weak)
        self.assertEqual(self.state.current[4].reason,'tentative_continuity')
        result=self.introduce_current(person=person)
        self.assertEqual(result.outcome,'ok',result)
        self.assertNotEqual(result.data['track_id'],old)
        self.assertNotIn(old,self.state.enrollments)
        proposal=self.state.enrollments[result.data['track_id']]
        self.assertEqual(len(proposal.samples),1)
        self.assertEqual(proposal.samples[0][1].text,'I am Jeff, remember me.')

    def test_name_survives_missing_voice_encoder_without_fake_voice_or_face_identity(self):
        self.begin('My name is Jeff.',None)
        result=self.introduce_current()
        self.assertEqual(result.outcome,'ok',result)
        self.assertEqual(result.data['voice_status'],'awaiting_usable_audio')
        self.assertIsNone(result.data['track_id'])
        self.assertEqual(self.store.person(result.data['person_id'])['name'],'Jeff')
        self.assertFalse(self.store.profiles(NS))
        self.assertFalse(self.state.enrollments)
        self.store.close();self.store.start()
        self.assertEqual(self.store.people()[0]['name'],'Jeff')

    def test_ambiguous_introduction_cannot_steal_supported_profile(self):
        bret=self.enroll('Bret',B)
        original=self.store.profiles(NS)[bret]
        self.ambiguous_groups();self.begin('I am Bret.',A)
        result=self.introduce_current('Bret',bret)
        self.assertEqual(result.reason,'ExistingProfileNeedsCompatibleVoice')
        self.assertEqual(self.store.profiles(NS)[bret],original)

    def test_cancelled_introduction_creates_neither_person_nor_track(self):
        self.ambiguous_groups();self.begin('My name is Jeff.',A)
        old=set(self.state.tracker.tracks)
        cancel=Cancellation();cancel.event.set()
        from r3x.contracts import Cancelled
        with self.assertRaises(Cancelled):
            self.state.execute(ToolCall('cancelled','introduce_person',dict(name='Jeff',aliases=[],subject='self',person_id=None,face_track_id=None)),self.store.revision,cancel)
        self.assertFalse(self.store.people())
        self.assertEqual(set(self.state.tracker.tracks),old)

    def test_overlapping_introduction_is_not_assigned_to_a_person(self):
        self.begin('My name is Jeff.',A,mixed=True)
        self.assertEqual(self.introduce_current().reason,'UntrustedSource')
        self.assertFalse(self.store.people())
