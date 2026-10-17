"""Speaker attribution layered with presence (owner, October 14); synthetic vectors only.

These prove the contract, not accuracy: synthetic embeddings say nothing about how close
Bret's or J.T.'s real voices score. Live acceptance needs a second person off camera."""
from dataclasses import replace
import math
import unittest

from r3x.speakers import SpeakerTracker, VoiceObservation
from tests.test_memory_core import MemoryFixture, NS, A, C


def vec(*values):
    norm = math.sqrt(sum(v*v for v in values))
    return tuple(v/norm for v in values)


CLOSE = vec(.45, .893, 0)     # .45 to A: under the .50 naming level, over the .40 floor.
FAR = vec(.30, .954, 0)       # .30 to A: under the floor.


def clip(vector, seconds=2., windows=None):
    return VoiceObservation(NS, vector, windows or ((0., seconds, vector),), seconds, 1., False, 'synthetic')


def face(person, track='face', width=150):
    return {'at': 100., 'identity': person, 'track_id': track, 'face_epoch': 0, 'face_width_px': width,
            'box': [.3, .3, .3, .3], 'position': 'center', 'sole_face_throughout': True}


class WindowFloorTests(unittest.TestCase):
    def test_a_clear_clip_with_one_weaker_window_is_named_but_does_not_grow_the_profile(self):
        weak = vec(.42, .908, 0)
        result = SpeakerTracker(NS).observe(clip(vec(.8, .6, 0), 4., ((0, 2, A), (2, 4, weak))), 0, {'p': [A]})
        self.assertEqual((result.person_id, result.reason), ('p', 'acoustic_match'))
        self.assertFalse(result.learning_eligible)

    def test_a_window_below_the_floor_or_preferring_someone_else_still_vetoes(self):
        profiles = {'p': [A], 'q': [C]}
        low = SpeakerTracker(NS).observe(clip(vec(.8, .6, 0), 4., ((0, 2, A), (2, 4, vec(.2, .98, 0)))), 0, profiles)
        other = SpeakerTracker(NS).observe(clip(vec(.8, .6, 0), 4., ((0, 2, A), (2, 4, C))), 0, profiles)
        self.assertEqual(low.reason, 'window_identity_conflict')
        self.assertEqual(other.reason, 'window_identity_conflict')


class OneVoiceTrackTests(unittest.TestCase):
    def test_two_tracks_of_one_voice_are_not_ambiguous_and_the_named_one_wins(self):
        tracker, profiles = SpeakerTracker(NS), {'p': [A]}
        u = vec(.35, .937, 0)
        first = tracker.observe(clip(u, 1.), 0, profiles)            # Unlabeled, sounds like p.
        named = tracker.observe(clip(A), 10, profiles)               # Recognized: p's track.
        self.assertNotEqual(first.track_id, named.track_id)
        between = vec(.45, .312, .775)                               # .45 to both tracks.
        result = tracker.observe(clip(between), 20, profiles)
        self.assertEqual(result.reason, 'tentative_continuity')
        self.assertEqual(result.track_id, named.track_id)

    def test_different_voices_close_together_still_abstain(self):
        tracker, profiles = SpeakerTracker(NS), {'p': [A], 'q': [C]}
        tracker.observe(clip(A), 0, profiles)
        tracker.observe(clip(C), 10, profiles)
        result = tracker.observe(clip(vec(.4, .82, .4)), 20, profiles)
        self.assertEqual(result.reason, 'ambiguous_continuity')

    def test_a_recognized_clip_continues_that_persons_track(self):
        tracker, profiles = SpeakerTracker(NS), {'p': [A]}
        named = tracker.observe(clip(A), 0, profiles)
        again = tracker.observe(clip(vec(.52, .854, 0)), 10, profiles)
        self.assertEqual((again.person_id, again.track_id), ('p', named.track_id))
        self.assertEqual(len(tracker.tracks), 1)


class InferredSpeakerTests(MemoryFixture):
    def setup_alone(self):
        self.person = self.enroll('Bret')
        self.state.present.saw_faces([face(self.person)], 100.)

    def speak(self, text, vector=CLOSE, faces=None, seconds=2.):
        self.at += 10
        from uuid import uuid4
        from r3x.contracts import Capture, Transcript
        capture = Capture(uuid4().hex, 'session', uuid4().bytes*int(2000*seconds), self.at, self.at+seconds,
                          self.at+seconds, voiced_intervals=((0., seconds),))
        self.state.begin(uuid4().hex, capture, Transcript(text, -.01, True), clip(vector, seconds),
                         [face(self.person)] if faces is None else faces)
        return self.state.current[4]

    def test_close_voice_alone_with_his_face_is_inferred_and_can_save_his_plan(self):
        self.setup_alone()
        recordings = self.store.rows('SELECT count(*) AS n FROM recordings')[0]['n']
        attribution = self.speak("I'm going camping this weekend.")
        self.assertEqual((attribution.state, attribution.reason), ('inferred', 'alone_with_recognized_face'))
        self.assertEqual((attribution.person_id, attribution.inferred_person_id), (None, self.person))
        self.assertFalse(attribution.learning_eligible)
        speaker = self.state.context()['speaker']
        self.assertEqual((speaker['speaker_id'], speaker['display_name']), (self.person, 'Bret'))
        saved = self.call('remember_followup', dict(predicate='trip', value='Camping on October 18', kind='plan',
                                                    on_date='2026-10-18', due_in_minutes=None, due_by=None,
                                                    audience='subject_only', source_turn_id=self.state.current[0]))
        self.assertEqual(saved.outcome, 'ok')
        claim = self.store.rows('SELECT said_by,subject_id,status FROM claims WHERE predicate="trip"')[0]
        self.assertEqual(dict(claim), {'said_by': self.person, 'subject_id': self.person, 'status': 'accepted'})
        # Never voice or face learning, never identity administration.
        self.assertEqual(self.store.rows('SELECT count(*) AS n FROM recordings')[0]['n'], recordings)
        self.assertEqual(self.state.face_learning, {})
        self.assertEqual(self.state.identity_targets('forget_person'), [])
        self.assertEqual(self.state.identity_targets('rename_person'), [])

    def test_no_inference_with_company_a_far_voice_or_another_voice_this_visit(self):
        self.setup_alone()
        self.assertEqual(self.speak('Hello.', FAR).state, 'unresolved')
        two = [face(self.person), face(None, 'guest')]
        self.assertNotEqual(self.speak('Hello.', faces=two).state, 'inferred')
        self.assertNotEqual(self.speak('Hello.', faces=[face(None)]).state, 'inferred')

    def test_another_voice_this_visit_blocks_inference(self):
        self.setup_alone()
        self.assertNotEqual(self.speak('It was me talking.', C, seconds=3.).state, 'inferred')
        self.assertNotEqual(self.speak("I'm going camping.").state, 'inferred')

    def test_recent_company_blocks_inference(self):
        self.setup_alone()
        self.state.present.note_audience({'faces': [face(self.person), face(None, 'guest')]}, 100.)
        self.assertNotEqual(self.speak("I'm going camping.").state, 'inferred')


MOTION_MASKED = vec(.30, .954, 0)   # .30 to A: Bret's own voice under servo noise.
TOO_FAR = vec(.15, .989, 0)         # .15 to A: how other voices and the television scored.


class CarriedSpeakerTests(MemoryFixture):
    """Owner, October 16: once Rex is sure it is Bret, it stays Bret for the conversation."""
    def setUp(self):
        super().setUp()
        self.person = self.enroll('Bret')

    def speak(self, text, vector=A, faces=(), seconds=2., motion=False):
        self.at += 10
        from uuid import uuid4
        from r3x.contracts import Capture, Transcript
        capture = Capture(uuid4().hex, 'session', uuid4().bytes*int(2000*seconds), self.at, self.at+seconds,
                          self.at+seconds, voiced_intervals=((0., seconds),), motion_risk=motion)
        self.state.trace.at = self.at + seconds
        self.state.begin(uuid4().hex, capture, Transcript(text, -.01, True), clip(vector, seconds), list(faces))
        return self.state.current[4]

    def sure(self):
        for text in ('Can you turn around and face me?', 'Do you know what room this is?'):
            self.assertEqual(self.speak(text).state, 'supported')

    def test_after_two_clear_matches_his_masked_voice_stays_his_without_a_face(self):
        self.sure()
        recordings = self.store.rows('SELECT count(*) AS n FROM recordings')[0]['n']
        attribution = self.speak("You're in the living room.", MOTION_MASKED, motion=True)
        self.assertEqual((attribution.state, attribution.reason), ('inferred', 'same_voice_this_conversation'))
        self.assertEqual((attribution.person_id, attribution.inferred_person_id), (None, self.person))
        self.assertFalse(attribution.learning_eligible)
        self.assertEqual(self.state.context()['speaker']['speaker_id'], self.person)
        self.assertEqual(self.speak("I'm just doing the dishes.", MOTION_MASKED, motion=True).inferred_person_id,
                         self.person)                                   # And on through the conversation.
        self.assertEqual(self.store.rows('SELECT count(*) AS n FROM recordings')[0]['n'], recordings)
        self.assertEqual(self.state.identity_targets('forget_person'), [])

    def test_one_match_is_not_sure_enough(self):
        self.assertEqual(self.speak('Hello Rex.').state, 'supported')
        self.assertEqual(self.speak("You're in the living room.", MOTION_MASKED, motion=True).state, 'unresolved')

    def test_a_voice_that_scores_like_someone_else_is_not_his(self):
        self.sure()
        self.assertEqual(self.speak('Hey Rex, who are you?', TOO_FAR, motion=True).state, 'unresolved')

    def test_another_voice_in_the_conversation_ends_the_carry(self):
        self.sure()
        self.assertNotEqual(self.speak('It was me talking.', C, seconds=3.).state, 'inferred')
        self.assertEqual(self.speak("You're in the living room.", MOTION_MASKED, motion=True).state, 'unresolved')

    def test_someone_else_recognized_ends_the_carry(self):
        self.sure()
        self.assertEqual(self.speak('Look over here.', MOTION_MASKED, faces=[face('jeremy')], motion=True).state,
                         'unresolved')

    def test_five_quiet_minutes_end_the_conversation(self):
        self.sure()
        self.at += 400
        self.assertEqual(self.speak("You're in the living room.", MOTION_MASKED, motion=True).state, 'unresolved')

    # October 17 review of the improvement plan.
    def test_a_new_voice_whose_track_was_never_his_is_not_credited_to_him(self):
        self.sure()
        newcomer = vec(.22, .975, 0)             # Under the floor: not his, like the television.
        first = self.speak('Hi there.', newcomer, seconds=2.)
        self.assertNotEqual(first.state, 'inferred')
        # Their next sentence joins that same session track but happens to score .30 against him.
        second = self.speak('Is this the famous robot?', MOTION_MASKED, seconds=2.)
        self.assertEqual(second.track_id, first.track_id)
        self.assertNotEqual(second.state, 'inferred')

    def test_two_faces_in_view_or_recent_company_end_the_carry_but_one_far_face_does_not(self):
        self.sure()
        two = [face(self.person), face(None, 'guest')]
        self.assertEqual(self.speak('Look over here.', MOTION_MASKED, faces=two, motion=True).state, 'unresolved')
        self.assertEqual(self.speak('Look over here.', MOTION_MASKED, faces=[face(None, 'far')], motion=True).state,
                         'unresolved')                        # Company was just seen: ten quiet minutes first.
        fresh = CarriedSpeakerTests('test_one_match_is_not_sure_enough'); fresh.setUp()
        try:
            fresh.sure()
            self.assertEqual(fresh.speak('Look over here.', MOTION_MASKED, faces=[face(None, 'far')], motion=True).state,
                             'inferred')                      # Owner, October 16: too far for a face match is still him.
        finally:
            fresh.tearDown()


if __name__ == '__main__':
    unittest.main()
