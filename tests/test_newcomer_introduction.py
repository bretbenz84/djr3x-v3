"""Owner ruling, October 20: a person introduced by someone else is learned from the one
unknown voice and the one unknown face around. Synthetic voices, faces and people only."""
import json
from uuid import uuid4
from r3x.contracts import Capture, Transcript
import unittest
from r3x.faces import FaceTracker
from r3x.state import CoreState, NEWCOMER_SECONDS
from r3x.speakers import SpeakerTracker
from tests.test_memory_core import MemoryFixture, observation, A, B, C, NS
from tests.test_face_identity import FNS, face, sample
from tests.test_speaker_presence import vec, face as recognized


# October 24 (1bfce8af): Bret's saved voice is A; his voice live tonight is HOST, named at .80.
# Jeremy's voice sounded .37 like Bret's saved voice and far less like Bret live, so it formed its
# own track, which the old .30 ceiling never named. KIN is a guest closer still (.47); TIRED is
# Bret's own off moment (.42), which presence took for him.
HOST = vec(.8, 0, .6)
JEREMY = vec(.37, .9, -.2)
KIN = vec(.47, .8465, -.25)
TIRED = vec(.42, .9077, -.2)


def view(start, vector=B, key=None, identity=None, resembles=None, clear=True):
    """One face row with a single view, as the camera gives it when its track keeps changing."""
    return {'track_id': key or 'face-' + uuid4().hex, 'at': start + 2, 'identity': identity, 'resembles': resembles,
            'box': [.2, .2, .3, .3], 'sole_face_throughout': False, '_samples': (sample(start + 1., vector, clear),)}


class NewcomerTests(MemoryFixture):
    def turn(self, text, vector=A, faces=(), seconds=3.):
        self.at += 10
        capture = Capture(uuid4().hex, 'session', uuid4().bytes*int(2000*seconds), self.at, self.at+seconds,
                          self.at+seconds, voiced_intervals=((0., seconds),))
        # Rows are written from time 0; their views move into this turn's capture.
        faces = [dict(f, at=self.at+2, _samples=tuple(sample(self.at+s.at, s.vector, s.eligible)
                                                       for s in f.get('_samples', ()))) for f in faces]
        self.state.begin(uuid4().hex, capture, Transcript(text, -.01, True), observation(vector, seconds), faces)

    def introduce_other(self, name='Casey', key=None):
        result = self.call('introduce_person', {'name': name, 'aliases': [], 'subject': 'other',
                                                'person_id': None, 'face_track_id': key})
        self.assertEqual(result.outcome, 'ok', result)
        return result

    def events(self, name):
        return [r for r in self.state.trace.rows if r['event'] == name]

    def test_newcomer_voice_is_gathered_from_their_own_speech_and_saved_under_their_name(self):
        host = self.enroll('Jordan', A)
        hosts_voice = self.store.profiles(NS)[host]
        self.turn('This is my partner Casey.', A)
        result = self.introduce_other()
        casey = result.data['person_id']
        self.assertEqual(result.data['voice_status'], 'listening_for_their_voice')
        self.assertEqual(self.state.enrollments, {})              # The introducer's voice is not Casey's.
        for text in ('Nice to meet you, little robot.', 'I play volleyball on the weekends.',
                     'He talks about you all the time.'):
            self.turn(text, B)
        self.assertEqual(len(self.events('newcomer_voice_linked')), 1)
        self.assertIn(casey, self.store.profiles(NS))
        self.assertEqual(self.store.person(casey)['status'], 'supported')
        self.assertEqual(self.store.profiles(NS)[host], hosts_voice)
        said = [json.loads(r['metadata'])['transcript']
                for r in self.store.rows('SELECT metadata FROM recordings WHERE person_id=?', (casey,))]
        self.assertEqual(len(said), 3)
        self.assertNotIn('This is my partner Casey.', said)
        self.store.close(); self.store.start()
        self.state = CoreState(self.store, SpeakerTracker(NS), self.state.trace)
        self.turn('Hello again, it is me.', B)
        self.assertEqual(self.state.current[4].person_id, casey)
        self.turn('And this is the host talking.', A)
        self.assertEqual(self.state.current[4].person_id, host)

    def test_an_unrecognized_introducer_is_never_the_newcomer(self):
        self.turn('This is Casey, say hello.', A)                 # Nobody saved: an unnamed voice.
        casey = self.introduce_other().data['person_id']
        self.turn('Hi there, nice to meet you.', B)
        self.assertEqual(self.events('newcomer_voice_linked'), [])   # One clip can be anyone's.
        self.turn('I live just down the street.', B)
        linked = self.events('newcomer_voice_linked')
        self.assertEqual(len(linked), 1)
        self.assertEqual(self.state.tracker.tracks[linked[0]['track_id']].person_id, casey)
        introducer = next(k for k, t in self.state.tracker.tracks.items() if t.person_id is None)
        self.assertNotEqual(introducer, linked[0]['track_id'])

    def test_two_unknown_voices_name_neither(self):
        self.enroll('Jordan', A)
        self.turn('This is Casey.', A)
        casey = self.introduce_other().data['person_id']
        self.turn('Hello robot, how are you doing?', B)        # The only unknown voice so far.
        self.turn('I came over for dinner tonight.', B)
        self.assertEqual(len(self.events('newcomer_voice_linked')), 1)
        self.turn('I am a different person entirely.', C)      # One clip could be a laugh.
        self.assertEqual(self.events('newcomer_voice_unlinked'), [])
        self.turn('Yes, and I am still talking here.', C)      # A second voice: which is Casey?
        self.assertEqual(self.events('newcomer_voice_unlinked')[0]['reason'], 'another_unknown_voice')
        for _ in range(3):
            self.turn('More words from the first stranger here.', B)
        self.assertEqual(len(self.events('newcomer_voice_linked')), 1)
        self.assertNotIn(casey, self.store.profiles(NS))
        self.assertFalse(any(e.person_id == casey for e in self.state.enrollments.values()))
        self.assertTrue(all(t.person_id != casey for t in self.state.tracker.tracks.values()))

    def test_saying_their_name_keeps_the_window_open_and_silence_closes_it(self):
        self.enroll('Jordan', A)
        self.turn('This is Casey.', A)
        self.introduce_other()
        self.at += NEWCOMER_SECONDS - 100
        self.turn('Go on, talk to Casey for a bit.', A)
        self.at += NEWCOMER_SECONDS - 100
        self.turn('Nice to meet you, little robot.', B)
        self.turn('I came over for dinner tonight.', B)
        self.assertEqual(len(self.events('newcomer_voice_linked')), 1)
        self.turn('This is Sam.', A)
        sam = self.introduce_other('Sam').data['person_id']
        self.at += NEWCOMER_SECONDS + 1
        self.turn('Hello, I am the quiet one here.', C)
        self.turn('I usually just listen to these two.', C)
        self.assertEqual(len(self.events('newcomer_voice_linked')), 1)
        self.assertNotIn(sam, self.state.newcomers)

    def test_newcomer_face_is_pooled_across_track_changes_not_from_a_face_like_the_hosts(self):
        host = self.enroll('Jordan', A)
        lookalike = view(0, A, 'blurry-host', resembles=host)      # Too blurry to name, but not a stranger.
        stranger = view(0, B, 'stranger')
        self.turn('This is Casey.', A, [lookalike, stranger])
        result = self.introduce_other(key='blurry-host')
        casey = result.data['person_id']
        self.assertEqual(result.data['face_track_id'], 'stranger')  # Redirected to the one stranger.
        self.assertNotEqual(result.data['face_status'], 'saved')
        self.turn('Casey, say something.', A, [view(0, A, resembles=host), view(0, B)])
        self.assertNotIn(casey, self.store.face_profiles(FNS))
        self.turn('Go on.', A, [view(0, B)])                        # A new track ID each time.
        self.assertEqual(len(self.store.face_profiles(FNS)[casey]), 2)
        self.assertNotIn(host, self.store.face_profiles(FNS))
        metadata = json.loads(self.store.rows('SELECT metadata FROM face_references WHERE person_id=?',
                                              (casey,))[0]['metadata'])
        self.assertEqual(metadata['association']['basis'], 'introduced_newcomer_sole_stranger_face')
        self.assertTrue(self.events('face_learning_saved'))

    def test_a_different_face_later_does_not_join_the_newcomer(self):
        self.enroll('Jordan', A)
        self.turn('This is Casey.', A, [face(0, B, 'casey', eligible=False)])
        casey = self.introduce_other(key='casey').data['person_id']
        for _ in range(3):
            self.turn('Someone else walked in.', A, [view(0, C)])
        self.assertNotIn(casey, self.store.face_profiles(FNS))
        waiting = self.events('newcomer_face_waiting')
        self.assertEqual([w['reason'] for w in waiting], ['unlike_the_introduced_face'])   # Said once.

    def test_two_strangers_in_view_or_two_newcomers_save_no_face(self):
        self.enroll('Jordan', A)
        self.turn('These are Casey and Sam.', A, [view(0, B, 'one'), view(0, C, 'two')])
        casey = self.introduce_other('Casey').data['person_id']
        sam = self.introduce_other('Sam').data['person_id']
        for _ in range(3):
            self.turn('Say hello, both of you.', A, [view(0, B)])
        self.assertNotIn(casey, self.store.face_profiles(FNS))
        self.assertNotIn(sam, self.store.face_profiles(FNS))

    def test_forgetting_the_newcomer_stops_gathering(self):
        host = self.enroll('Jordan', A)
        self.turn('This is Casey.', A)
        casey = self.introduce_other().data['person_id']
        self.turn('Forget Casey, never mind.', A)
        result = self.call('forget_person', {'person_id': casey})
        self.assertEqual(result.outcome, 'ok', result)
        self.assertEqual(self.state.newcomers, {})
        self.turn('Hello robot, nice to meet you.', B, [view(0, B)])
        self.assertEqual(self.events('newcomer_voice_linked'), [])
        self.assertEqual(set(self.store.face_profiles(FNS)), set())
        self.assertEqual(set(self.store.profiles(NS)), {host})


    def tonight(self):
        """Bret's voice saved on an earlier night; a fresh conversation with him tonight."""
        host = self.enroll('Bret', A)
        self.state = CoreState(self.store, SpeakerTracker(NS), self.state.trace)
        return host

    def test_a_guest_who_sounds_somewhat_like_the_host_is_still_learned(self):
        host = self.tonight()
        self.turn("Why was he rolling up on me? He doesn't even know me.", JEREMY)   # Before the introduction.
        self.turn("And now he's sitting here cockeyed at me.", JEREMY)
        self.turn('Do you know who this is next to us?', HOST)
        self.assertEqual(self.state.current[4].person_id, host)
        self.turn('This is Jeremy Thomas.', HOST)
        jeremy = self.introduce_other('Jeremy Thomas').data['person_id']
        self.assertEqual(len(self.events('newcomer_voice_linked')), 1)   # His voice gets his name at once.
        self.turn('I know you are self-aware, you cannot fool me.', JEREMY)
        self.assertIn(jeremy, self.store.profiles(NS))
        self.turn('Close your visor.', HOST)
        self.assertEqual(self.state.current[4].person_id, host)
        self.turn('Why did he turn his head so crazy?', JEREMY)
        self.assertEqual(self.state.current[4].person_id, jeremy)

    def test_a_voice_close_to_a_saved_one_is_not_given_to_the_guest_and_the_log_says_why_once(self):
        self.tonight()
        self.turn('This is Casey.', HOST)
        casey = self.introduce_other().data['person_id']
        for text in ('Nice to meet you, little robot.', 'I came over for dinner tonight.', 'The pasta was great.'):
            self.turn(text, KIN)
        self.assertEqual(self.events('newcomer_voice_linked'), [])
        self.assertNotIn(casey, self.store.profiles(NS))
        waiting = self.events('newcomer_voice_waiting')
        self.assertEqual([(w['reason'], w['score']) for w in waiting], [('close_to_a_saved_voice', .47)])

    def test_a_voice_presence_took_for_the_host_is_never_the_guests(self):
        host = self.tonight()
        alone = [recognized(host)]
        self.state.present.saw_faces(alone, 100.)
        for text in ("I'm getting pretty tired tonight.", "It's eleven o'clock at night already."):
            self.turn(text, TIRED, alone, seconds=2.)
            self.assertEqual(self.state.current[4].inferred_person_id, host)
        self.turn('My friend Casey is coming over later.', HOST, alone)
        casey = self.introduce_other().data['person_id']
        self.assertEqual(self.events('newcomer_voice_linked'), [])
        self.assertTrue(all(t.person_id != casey for t in self.state.tracker.tracks.values()))
        self.assertEqual([w['reason'] for w in self.events('newcomer_voice_waiting')], ['taken_for_someone_saved'])

    def test_a_blurry_glimpse_or_an_odd_angle_first_does_not_block_the_guests_clear_face(self):
        host = self.enroll('Bret', A)
        self.turn('This is Jeremy Thomas.', A)
        jeremy = self.introduce_other('Jeremy Thomas').data['person_id']
        # Bret's face caught blurry on a fresh camera track: the only unknown face for a moment.
        self.turn('Say hello to him.', A, [view(0, A, clear=False)])
        self.turn('Hello there.', JEREMY, [view(0, C)])            # Jeremy turned to one side.
        self.assertNotIn(jeremy, self.store.face_profiles(FNS))
        self.turn('Why was he rolling up on me?', JEREMY, [view(0, B)])
        self.assertEqual([w['reason'] for w in self.events('newcomer_face_waiting')], ['earlier_view_replaced'])
        self.turn('He does not know me.', JEREMY, [view(0, B), view(0, C)])   # Two unknown faces: wait.
        self.turn('He does not know me.', JEREMY, [view(0, B), view(0, C)])
        self.assertEqual([w['reason'] for w in self.events('newcomer_face_waiting')][1:], ['more_than_one_unknown_face'])
        self.assertNotIn(jeremy, self.store.face_profiles(FNS))
        self.turn('I see through it.', JEREMY, [view(0, B)])
        self.assertEqual(self.store.face_profiles(FNS)[jeremy], [B, B])
        self.assertNotIn(host, self.store.face_profiles(FNS))
        self.assertTrue(self.events('face_learning_saved'))


class ResemblanceTests(unittest.TestCase):
    def test_a_face_that_looked_like_someone_still_resembles_them_in_a_blurry_frame(self):
        # October 20 (515d71db): the owner's face scored .77, then .41 in the introduction's
        # frame, never long enough to be named; it was taken for the newcomer's.
        tracker = FaceTracker(); box = (.1, .1, .3, .3)
        rows = tracker.update([box], 1., [sample(1., (.77, .6380, 0.))], {'host': [A]})
        self.assertIsNone(rows[0]['identity'])
        self.assertEqual(rows[0]['resembles'], 'host')
        rows = tracker.update([box], 1.5, [sample(1.5, (.41, .9121, 0.))], {'host': [A]})
        self.assertLess(rows[0]['similarity'], FaceTracker.CONTINUITY)
        self.assertEqual(rows[0]['resembles'], 'host')
        stranger = tracker.update([(.6, .1, .3, .3)], 2., [sample(2., C)], {'host': [A]})
        self.assertIsNone(stranger[0]['resembles'])
