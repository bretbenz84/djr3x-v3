import math
from dataclasses import replace
from r3x.speakers import window_consistency, centroid, SpeakerTracker
from r3x.state import CoreState
from tests.test_memory_core import MemoryFixture, observation, A, B, NS


def vector(degrees):
    radians=math.radians(degrees)
    return (math.cos(radians),math.sin(radians),0.)


def windows(vectors):
    return tuple((i,i+2,v) for i,v in enumerate(vectors))


class VoiceConsensusTests(MemoryFixture):
    def test_gradual_variation_retains_every_window_and_can_learn(self):
        ws=windows([vector(a) for a in (0,15,30,45,65)])
        score,mixed=window_consistency(ws)
        self.assertGreaterEqual(score,.55)
        self.assertFalse(mixed)
        obs=replace(observation(seconds=6),embedding=centroid([w[2] for w in ws]),
                    windows=ws,consistency=score,mixed=mixed)
        self.assertTrue(SpeakerTracker(NS).observe(obs,1,{}).learning_eligible)

    def test_two_coherent_voices_and_single_foreign_window_abstain(self):
        for vectors in ([A,A,vector(75),vector(75)],[A,A,A,A,B],[A,B]):
            ws=windows(vectors);score,mixed=window_consistency(ws)
            obs=replace(observation(seconds=6),embedding=centroid(vectors),windows=ws,
                        consistency=score,mixed=mixed)
            result=SpeakerTracker(NS).observe(obs,1,{})
            self.assertIsNone(result.track_id)
            self.assertFalse(result.learning_eligible)

    def test_competing_window_still_blocks_a_good_overall_match(self):
        tracker=SpeakerTracker(NS)
        tracker.observe(observation(A),1,{})
        tracker.observe(observation(B),2,{})
        obs=replace(observation(A),windows=windows([A,A,B]))
        self.assertEqual(tracker.observe(obs,3,{}).reason,'window_track_conflict')

    def test_short_replies_accumulate_across_restart_then_long_sentence_enrolls(self):
        self.begin('My name is Jordan.',seconds=1.3)
        result=self.call('introduce_person',{'name':'Jordan','aliases':[],
                         'subject':'self','person_id':None,'face_track_id':None})
        person=result.data['person_id']
        self.begin('Yes I built this myself.',vector=vector(35),seconds=1.7)
        self.assertEqual(self.store.profiles(NS),{})
        self.store.close();self.store.start()
        self.state=CoreState(self.store,SpeakerTracker(NS),self.state.trace)
        self.begin('Today we are visiting the historic sites around town.',vector=vector(-25),seconds=5.3)
        self.assertEqual(len(self.store.profiles(NS)[person]),3)
        self.assertEqual(self.store.rows('SELECT count(*) n FROM voice_candidates')[0]['n'],0)
