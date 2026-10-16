"""Production tracking/state/store contracts with synthetic visual evidence only."""
from dataclasses import replace
import json
import unittest
from uuid import uuid4
from r3x.faces import FaceSample, FaceTracker
from r3x.vision import Frame, FrameHub
from r3x.memory import Memory
from r3x.contracts import Capture, Transcript, Cancellation, ToolCall
from r3x.state import CoreState
from r3x.speakers import SpeakerTracker
from tests.test_memory_core import MemoryFixture, observation, A, B, C, NS

FNS = 'synthetic-face:3'


def sample(at,vector=A,eligible=True,session='session'):
    key=uuid4().hex
    return FaceSample(vector,key.encode(),b'original-'+key.encode(),key,'fixture-camera',session,
                      at,150.,.9,100.,100.,(1.,0.,0.,0.,1.,0.),FNS,eligible)


def face(start,vector=A,key='face-a',eligible=True,sole=True,identity=None):
    return {'track_id':key,'at':start+2,'identity':identity,'box':[.2,.2,.3,.3],
            'sole_face_throughout':sole,'_samples':(sample(start+.5,vector,eligible),sample(start+1.5,vector,eligible))}


class FaceStateTests(MemoryFixture):
    def test_short_clean_introduction_saves_face_but_not_voice(self):
        # Face learning must stay independent even with a stricter voice policy.
        self.state.tracker.policy=replace(self.state.tracker.policy,min_learning_seconds=2.)
        capture=Capture(uuid4().hex,'session',b'x'*96000,110.,113.,113.,
                        voiced_intervals=((.3,1.643960874),))
        voice=observation(seconds=1.343960874)
        self.state.begin(uuid4().hex,capture,Transcript('My name is Bret Benziger.',0.,True),voice,[face(110)])
        self.assertFalse(self.state.current[4].learning_eligible)
        result=self.intro_face('Bret',key=None)
        self.assertEqual(result.data['face_status'],'saved')
        person=result.data['person_id']
        self.assertEqual(len(self.store.face_profiles(FNS)[person]),2)
        self.assertEqual(self.store.profiles(NS),{})
        self.store.close();self.store.start()
        self.assertEqual(len(self.store.face_profiles(FNS)[person]),2)
        self.assertEqual(self.store.person(person)['name'],'Bret')

    def test_short_introduction_still_rejects_ambiguous_visual_or_acoustic_evidence(self):
        cases = ('multiple','not_sole','distant','competitor','inconsistent','direction')
        for case in cases:
            with self.subTest(case=case):
                self.state.tracker.reset()
                self.state.enrollments.clear()
                faces=[face(110)]
                voice=observation(seconds=1.34)
                if case=='multiple': faces.append(face(110,B,'second',sole=False))
                if case=='not_sole': faces[0]['sole_face_throughout']=False
                if case=='distant': faces[0]['box']=[.2,.2,.1,.1]
                if case=='competitor': self.state.tracker.observe(observation(B),109.,{})
                if case=='inconsistent': voice=replace(voice,consistency=.5)
                if case=='direction': voice=replace(voice,direction_conflict=True)
                capture=Capture(uuid4().hex,'session',b'x'*96000,110.,113.,113.)
                self.state.begin(uuid4().hex,capture,Transcript('My name is Jordan.',0.,True),voice,faces)
                result=self.intro_face()
                self.assertEqual(self.store.face_profiles(FNS),{})
                self.assertNotEqual(result.data.get('face_status'),'saved')

    def visible_turn(self,text,faces,vector=A,trusted=True,mixed=False):
        start=faces[0]['at']-2
        capture=Capture(uuid4().hex,'session',uuid4().bytes*6000,start,start+3,start+3,
                        voiced_intervals=((0.,3.),))
        self.state.begin(uuid4().hex,capture,Transcript(text,0,trusted),observation(vector,mixed=mixed),faces)

    def intro_face(self,name='Jordan',subject='self',person=None,key='face-a'):
        return self.call('introduce_person',{'name':name,'aliases':[],'subject':subject,'person_id':person,'face_track_id':key})

    def test_introduction_persists_face_after_restart_without_voice_enrollment(self):
        self.visible_turn('My name is Jordan.',[face(110)])
        result=self.intro_face()
        self.assertEqual(result.data['face_status'],'saved')
        person=result.data['person_id']
        self.assertEqual(len(self.store.face_profiles(FNS)[person]),2)
        self.assertEqual(self.store.profiles(NS),{})
        self.assertNotIn('_samples',self.state.context()['faces'][0])
        json.dumps(self.state.context())
        self.store.close();self.store.start()
        self.assertEqual(len(self.store.face_profiles(FNS)[person]),2)
        self.assertEqual(self.store.face_profiles('other-model'),{})
        metadata=json.loads(self.store.rows('SELECT metadata FROM face_references')[0]['metadata'])
        self.assertEqual(metadata['camera_id'],'fixture-camera')
        self.assertEqual(metadata['association']['subject'],'self')

    def test_third_party_answer_names_target_not_introducer_voice(self):
        speaker=self.enroll('Jordan')
        target=face(150,B,'friend',sole=False)
        known=face(150,A,'speaker',sole=False,identity=speaker)
        self.visible_turn("That's my dad Casey.",[known,target])
        result=self.intro_face('Casey','other',key='friend')
        self.assertEqual(result.data['face_status'],'saved')
        person=result.data['person_id']
        self.assertEqual(self.state.current[4].person_id,speaker)
        self.assertNotIn(person,self.store.profiles(NS))
        metadata=json.loads(self.store.rows('SELECT metadata FROM face_references WHERE person_id=?',(person,))[0]['metadata'])
        self.assertEqual(metadata['association']['said_by'],speaker)

    def test_multiple_unknown_faces_need_a_delivered_target_question(self):
        faces=[face(110,A,'left',sole=False),face(110,B,'right',sole=False)]
        self.visible_turn('This is Casey.',faces)
        result=self.intro_face('Casey','other',key='right')
        self.assertEqual(result.data['face_status'],'ambiguous_face_target')
        self.assertEqual(self.store.face_profiles(FNS),{})
        self.state.question_delivered('What is their name?',None,112,'right')
        self.visible_turn("That's Casey.",[face(120,A,'left',sole=False),face(120,B,'right',sole=False)])
        result=self.intro_face('Casey','other',person=result.data['person_id'],key='right')
        self.assertEqual(result.data['face_status'],'saved')

    def test_low_quality_deferred_then_clear_views_and_replacement_aborts(self):
        self.visible_turn('I am Jordan.',[face(110,eligible=False)])
        result=self.intro_face()
        self.assertEqual(result.data['face_status'],'need_clear_compatible_views')
        self.assertEqual(self.store.face_profiles(FNS),{})
        self.visible_turn('Here is a better view.',[face(120)])
        self.assertIn(result.data['person_id'],self.store.face_profiles(FNS))
        self.visible_turn('This is Casey.',[face(130,B,'new',eligible=False)])
        other=self.intro_face('Casey','other',key='new')
        self.visible_turn('Someone else is here.',[face(140,C,'replacement')])
        self.assertNotIn(other.data['person_id'],self.store.face_profiles(FNS))
        self.assertEqual(self.state.face_learning,{})

    def test_face_cannot_override_competing_gallery_and_failed_write_is_atomic(self):
        self.visible_turn('I am Jordan.',[face(110)])
        first=self.intro_face()
        self.visible_turn('This is Casey.',[face(120,A,'new')],B)
        second=self.intro_face('Casey','other',key='new')
        self.assertEqual(second.data['face_status'],'competing_face_identity')
        self.assertNotIn(second.data['person_id'],self.store.face_profiles(FNS))
        self.store.connection.execute("CREATE TRIGGER reject_face BEFORE INSERT ON face_references BEGIN SELECT RAISE(ABORT,'full'); END")
        self.visible_turn('This is Morgan.',[face(130,C,'third')],B)
        result=self.intro_face('Morgan','other',key='third')
        self.assertEqual(result.outcome,'failed')
        self.assertEqual(self.store.resolve('Morgan'),[])
        self.assertEqual(len(self.store.face_profiles(FNS)),1)

    def test_supported_voice_can_learn_sole_face_without_another_introduction(self):
        person=self.enroll('Jordan')
        self.visible_turn('I repaired the radio yesterday.',[face(150)])
        self.assertIn(person,self.store.face_profiles(FNS))

    def test_untrusted_mixed_frozen_and_wrong_session_samples_cannot_enroll(self):
        for trusted,mixed in ((False,False),(True,True)):
            self.visible_turn('I am Jordan.',[face(110)],trusted=trusted,mixed=mixed)
            self.assertEqual(self.intro_face().outcome,'rejected')
        f=face(120);f['_samples']=tuple(replace(s,session_id='old') for s in f['_samples'])
        self.visible_turn('I am Jordan.',[f])
        self.assertEqual(self.intro_face().data['face_status'],'need_clear_compatible_views')
        self.state.frozen=True
        self.visible_turn('I am Jordan.',[face(130)])
        self.assertEqual(self.intro_face().reason,'FrozenEvaluationDisallowsMutation')
        self.assertEqual(self.store.face_profiles(FNS),{})

    def test_correction_and_forgetting_clear_face_references_and_pending_work(self):
        self.visible_turn('I am Jordan.',[face(110)])
        result=self.intro_face();person=result.data['person_id']
        self.visible_turn('That is the wrong face.',[face(120,identity=person)])
        reset=self.call('reset_face_identity',{'person_id':person,'face_track_id':'face-a'})
        self.assertEqual(reset.outcome,'ok')
        self.assertEqual(self.store.face_profiles(FNS),{})
        self.assertIsNotNone(self.store.person(person))
        self.visible_turn('I am Jordan.',[face(130)])
        self.intro_face(person=person)
        with self.store.transaction() as conn:self.store.forget(conn,person)
        self.assertEqual(self.store.rows('SELECT * FROM face_references'),[])

    def test_idempotence_and_stale_revision_never_duplicate_faces(self):
        self.visible_turn('I am Jordan.',[face(110)])
        args={'name':'Jordan','aliases':[],'subject':'self','person_id':None,'face_track_id':'face-a'}
        first=self.call('introduce_person',args,call_id='once')
        self.assertEqual(self.call('introduce_person',args,call_id='once').data,first.data)
        self.assertEqual(len(self.store.rows('SELECT * FROM face_references')),2)
        result=self.call('introduce_person',args,revision=0)
        self.assertEqual(result.reason,'StaleStateRevision')


class FaceTrackingTests(unittest.TestCase):
    def test_stability_replacement_and_conflict_do_not_transfer_names(self):
        tracker=FaceTracker();box=(.1,.1,.3,.3)
        for at in (1.,1.5,2.): rows=tracker.update([box],at,[sample(at)],{'a':[A],'b':[B]})
        self.assertEqual(rows[0]['identity'],'a')
        old=rows[0]['track_id']
        weak=(.5,.8660254,0.)
        # A competing observation at exactly the same position must not inherit A.
        rows=tracker.update([box],2.5,[sample(2.5,B)],{'a':[A],'b':[B]})
        self.assertNotEqual(rows[0]['track_id'],old)
        self.assertIsNone(rows[0]['identity'])
        self.assertEqual(tracker.during(4.,5.),())
        tracker.clear();self.assertEqual(tracker.during(1.,3.),())

    def test_unknown_and_ambiguous_gallery_abstain_and_samples_are_bounded(self):
        tracker=FaceTracker();box=(.1,.1,.3,.3)
        for i in range(100): rows=tracker.update([box],i/2,[sample(i/2)],{'a':[A],'also-a':[A]})
        self.assertIsNone(rows[0]['identity'])
        self.assertLessEqual(len(tracker.history),64)
        self.assertLessEqual(len(next(iter(tracker.tracks.values())).samples),3)
        self.assertTrue(all('_samples' not in row for _,rs in tracker.history for row in rs))

    def test_latest_shared_frames_skip_stale_work_and_close_releases(self):
        hub=FrameHub()
        for name in ('faces','scene','objects','places'):hub.register(name)   # Every live consumer fits.
        with self.assertRaises(ValueError):hub.register('extra')
        for i in range(30):hub.publish(Frame(str(i),'cam','session',i,2,2,b'x'*12))
        first=hub.take('faces');second=hub.take('scene')
        self.assertIs(first,second)
        self.assertEqual(first.frame_id,'29')
        self.assertIsNone(hub.take('faces','29',timeout=0))
        hub.close();self.assertIsNone(hub.take('objects'))

class FaceCorrectionTests(MemoryFixture):
    def test_reset_blocks_automatic_relearning_and_discards_queued_old_epoch(self):
        from tests.test_face_identity import FaceStateTests
        person=self.enroll('Jordan')
        visible=FaceStateTests.visible_turn.__get__(self)
        visible('I fixed the radio.',[face(150)])
        self.assertIn(person,self.store.face_profiles(FNS))
        self.state.face_invalidator=lambda:1
        f=face(160,identity=person)
        visible('That is the wrong face.',[f])
        result=self.call('reset_face_identity',{'person_id':person,'face_track_id':'face-a'})
        self.assertEqual(result.outcome,'ok')
        visible('Here is another sentence.',[face(170)])
        self.assertEqual(self.state.faces,())
        f=face(180);f['face_epoch']=1
        visible('Here is another sentence.',[f])
        self.assertEqual(self.store.face_profiles(FNS),{})
        self.store.close();self.store.start()
        self.assertTrue(self.store.rows('SELECT person_id FROM face_learning_blocks'))

    def test_face_introduction_via_real_session_has_no_biometrics_in_model_context(self):
        from r3x.session import Session
        from r3x.output import TextOutput
        from tests.test_semantic_session import Brain
        face_row=face(110)
        capture=Capture(uuid4().hex,'session',b'x'*96000,110.,113.,113.,voiced_intervals=((0.,3.),))
        brain=Brain(lambda snap:ToolCall('face','introduce_person',{'name':'Jordan','aliases':[],
            'subject':'self','person_id':None,'face_track_id':snap.context['faces'][0]['track_id']}))
        session=Session('session',brain,TextOutput(self.state.trace),self.state.trace,self.state)
        receipt=session.process_turn(capture,Transcript('My name is Jordan.',0.,True),0,observation(),[face_row])
        self.assertEqual(receipt.status,'completed')
        self.assertEqual(len(self.store.rows('SELECT id FROM face_references')),2)
        self.assertEqual(len(brain.requests),2)
        for request in brain.requests:
            self.assertNotIn('_samples',json.dumps(request.context))

    def test_schema_extension_migrates_old_core_and_rejects_future_face_version(self):
        self.store.connection.execute('DROP TABLE face_references')
        self.store.connection.execute('DROP TABLE face_learning_blocks')
        self.store.connection.execute("DELETE FROM meta WHERE key='face_schema'")
        self.store.close();self.store.start()
        self.assertEqual(self.store.rows('SELECT * FROM face_references'),[])
        self.store.connection.execute("UPDATE meta SET value='99' WHERE key='face_schema'")
        self.store.close()
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'UnsupportedFaceSchema'):self.store.start()
        self.assertEqual(self.path.read_bytes(),before)

class DelayedVisualEvidenceTests(unittest.TestCase):
    def test_asr_delay_keeps_source_crops_and_excludes_later_camera_views(self):
        tracker=FaceTracker();box=(.1,.1,.3,.3)
        for i in range(20):tracker.update([box],i*.5,[sample(i*.5)],{})
        rows=tracker.during(0.,3.)
        self.assertEqual(len(rows[0]['_samples']),3)
        self.assertTrue(all(0<=s.at<=3 for s in rows[0]['_samples']))
        self.assertTrue(all(not s.original for t in tracker.tracks.values() for s in t.samples))
        self.assertLessEqual(tracker.sample_bytes,16*1024*1024)
        tracker.update([],50.,[],{})
        self.assertEqual(tracker.sample_bytes,0)
        self.assertEqual(len(tracker.sample_history),0)
