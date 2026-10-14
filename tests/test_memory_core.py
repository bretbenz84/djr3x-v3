from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.memory import Memory
from r3x.speakers import SpeakerTracker, VoiceObservation
from r3x.state import CoreState
from tests.test_voice_core import Trace


NS = "synthetic:3"
A, B, C = (1.,0.,0.), (0.,1.,0.), (0.,0.,1.)


def observation(vector=A, seconds=3., mixed=False):
    return VoiceObservation(NS,vector,((0.,2.,vector),),seconds,1.,mixed,"synthetic")


class MemoryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name).resolve()/"core.sqlite3"
        self.store = Memory(self.path)
        self.store.start()
        self.state = CoreState(self.store,SpeakerTracker(NS),Trace())
        self.at = 100.

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def begin(self,text,vector=A,seconds=3.,trusted=True,mixed=False):
        self.at += 10
        capture = Capture(uuid4().hex,"session",uuid4().bytes*int(2000*seconds),self.at,self.at+seconds,self.at+seconds,
                          voiced_intervals=((0.,seconds),))
        turn = uuid4().hex
        self.state.begin(turn,capture,Transcript(text,-.01,trusted),observation(vector,seconds,mixed))
        return turn

    def call(self,name,args,call_id=None,revision=None):
        if name=="record_claim" and "subject_reference" not in args:
            args = dict(args,subject_reference=self.store.person(args["subject_id"])["name"] if args.get("kind")=="third_party" else None)
        return self.state.execute(ToolCall(call_id or uuid4().hex,name,args),
                                  self.store.revision if revision is None else revision,Cancellation())

    def introduce(self,name="Bret",vector=A):
        self.begin("My name is "+name,vector)
        result = self.call("introduce_person",{"name":name,"aliases":[],"subject":"self","person_id":None,"face_track_id":None})
        self.assertEqual(result.outcome,"ok",result)
        self.state.advance_enrollment()
        return result.data["person_id"]

    def enroll(self,name="Bret",vector=A):
        person = self.introduce(name,vector)
        for text in ("I like old radios and music.","Yesterday I worked on the robot."):
            self.begin(text,vector)
            self.state.advance_enrollment()
        self.assertEqual(self.store.person(person)["status"],"supported")
        return person

class MemoryTests(MemoryFixture):
    def test_competing_unnamed_voice_cannot_open_a_second_chain_for_one_person(self):
        person=self.introduce()
        self.begin("My name is Bret.",B)
        result=self.call("introduce_person",{"name":"Bret","aliases":[],"subject":"self","person_id":person,"face_track_id":None})
        self.assertEqual(result.reason,"CompetingVoiceAssociation")
        self.assertEqual(len(self.state.enrollments),1)

    def test_existing_profile_cannot_be_relearned_from_an_incompatible_voice(self):
        person=self.enroll()
        self.begin("I'm Bret, remember?",C)
        result=self.call("introduce_person",{"name":"Bret","aliases":[],"subject":"self","person_id":person,"face_track_id":None})
        self.assertEqual(result.reason,"ExistingProfileNeedsCompatibleVoice")
        self.assertEqual(self.state.enrollments,{})

    def test_unknown_self_report_cannot_guess_even_a_candidate_subject(self):
        person=self.enroll()
        source=self.begin("I am a museum curator.",C)
        result=self.call("record_claim",{"subject_id":person,"predicate":"occupation","value":"curator","kind":"self_report","source_turn_id":source})
        self.assertEqual(result.reason,"UnresolvedSelfReportSubject")
        self.assertEqual(self.store.claims(include_candidates=True),[])
        self.assertEqual(self.store.rows("SELECT text FROM turns WHERE id=?",(source,))[0]["text"],"I am a museum curator.")

    def test_natural_enrollment_keeps_original_audio_and_namespace(self):
        person = self.enroll()
        records = self.store.rows("SELECT * FROM recordings WHERE person_id=?",(person,))
        self.assertEqual(len(records),3)
        self.assertTrue(all(r["pcm"] for r in records))
        self.assertEqual(len(self.store.profiles(NS)[person]),3)
        self.assertEqual(self.store.profiles("another:model"),{})

    def test_short_name_anchor_needs_three_long_compatible_turns(self):
        self.begin("I'm Bret.",seconds=.7)
        result=self.call("introduce_person",{"name":"Bret","aliases":[],"subject":"self","person_id":None,"face_track_id":None})
        self.state.advance_enrollment()
        self.assertEqual(self.store.profiles(NS),{})
        for _ in range(2):
            self.begin("Here is a longer natural sentence.")
            self.state.advance_enrollment()
        self.assertEqual(self.store.profiles(NS),{})
        self.begin("Here is the third qualifying turn.")
        self.state.advance_enrollment()
        self.assertIn(result.data["person_id"],self.store.profiles(NS))

    def test_correction_cancels_chain_without_next_answer_handler(self):
        person=self.introduce()
        self.begin("That's not his name.",B)
        result=self.call("cancel_association",{"person_id":person,"track_id":None})
        self.assertEqual(result.outcome,"ok")
        self.assertEqual(self.state.enrollments,{})
        self.assertEqual(self.store.profiles(NS),{})

    def test_foreign_mixed_echo_and_low_trust_never_grow_anchor(self):
        person=self.introduce()
        for vector,trusted,mixed in ((B,True,False),(A,False,False),(A,True,True)):
            self.begin("This sentence cannot qualify for that voice.",vector,trusted=trusted,mixed=mixed)
            self.state.advance_enrollment()
        self.assertEqual(self.store.profiles(NS),{})
        self.assertEqual(len(next(iter(self.state.enrollments.values())).samples),1)

    def test_mentioned_person_is_not_the_speaker(self):
        bret=self.enroll()
        self.begin("That's Jeff Benziger.")
        result=self.call("introduce_person",{"name":"Jeffrey Benziger","aliases":["Jeff","Jeff Benziger"],
                                             "subject":"other","person_id":None,"face_track_id":None})
        jeff=result.data["person_id"]
        self.assertEqual(self.state.current[-1].person_id,bret)
        self.assertNotIn(jeff,self.store.profiles(NS))
        self.assertEqual(self.store.resolve("jeff benziger")[0]["id"],jeff)

    def test_restart_recall_and_correction_preserve_source(self):
        bret=self.enroll()
        source=self.begin("I collect radios.")
        result=self.call("record_claim",{"subject_id":bret,"predicate":"collects","value":"radios","kind":"self_report","source_turn_id":source})
        old=result.data["claim_id"]
        self.store.close()
        self.store=Memory(self.path)
        self.store.start()
        self.state=CoreState(self.store,SpeakerTracker(NS),Trace())
        self.assertEqual(self.store.claims(bret)[0]["source_turn"],source)
        correction=self.begin("I meant clocks, not radios.")
        result=self.call("correct_claim",{"claim_id":old,"value":"clocks","source_turn_id":correction})
        self.assertEqual(result.outcome,"ok",result)
        self.assertEqual([r["value"] for r in self.store.claims(bret)],["clocks"])
        self.assertEqual(self.store.rows("SELECT text FROM turns WHERE id=?",(source,))[0]["text"],"I collect radios.")
        self.assertEqual(self.store.rows("SELECT status FROM claims WHERE id=?",(old,))[0]["status"],"superseded")

    def test_third_party_claim_retains_speaker_and_subject(self):
        bret=self.enroll()
        jeff=self.enroll("Jeff",B)
        source=self.begin("Jeff collects clocks.")
        result=self.call("record_claim",{"subject_id":jeff,"predicate":"collects","value":"clocks","kind":"third_party","source_turn_id":source})
        self.assertEqual(result.data["said_by"],bret)
        self.assertEqual(self.store.claims(jeff)[0]["kind"],"third_party")
        bad=self.call("record_claim",{"subject_id":jeff,"predicate":"collects","value":"clocks","kind":"self_report","source_turn_id":source})
        self.assertEqual(bad.reason,"SelfReportSubjectMismatch")

    def test_uncertain_speaker_claim_is_only_candidate(self):
        bret=self.enroll()
        source=self.begin("Bret has an old radio.",C)
        result=self.call("record_claim",{"subject_id":bret,"predicate":"owns","value":"radio","kind":"third_party","source_turn_id":source})
        self.assertEqual(result.data["status"],"candidate")
        self.assertEqual(self.store.claims(bret),[])

    def test_duplicate_write_stale_revision_and_invented_ids(self):
        bret=self.enroll()
        source=self.begin("I collect radios.")
        args={"subject_id":bret,"predicate":"collects","value":"radios","kind":"self_report","source_turn_id":source}
        result=self.call("record_claim",args,"request")
        duplicate=self.call("record_claim",args,"request",revision=-1)
        self.assertEqual(result.data,duplicate.data)
        self.assertEqual(len(self.store.claims(bret)),1)
        self.assertEqual(self.call("record_claim",args,revision=-1).reason,"StaleStateRevision")
        self.assertEqual(self.call("record_claim",dict(args,subject_id="invented")).reason,"UnknownPersonId")
        self.assertEqual(self.call("record_claim",dict(args,source_turn_id="invented")).reason,"SourceTurnMismatch")
        self.assertEqual(self.call("record_claim",dict(args,value="different"),"request").reason,"RequestIdReusedWithDifferentArguments")

    def test_write_failure_rolls_back_person_and_proposal(self):
        self.begin("My name is Bret.")
        self.store.connection.execute("CREATE TRIGGER fail_person BEFORE INSERT ON people BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        result=self.call("introduce_person",{"name":"Bret","aliases":[],"subject":"self","person_id":None,"face_track_id":None})
        self.assertEqual(result.outcome,"failed")
        self.assertEqual(self.store.people(),[])
        self.assertEqual(self.state.enrollments,{})

    def test_forget_removes_audio_embeddings_claims_and_tracks(self):
        bret=self.enroll()
        source=self.begin("I collect radios.")
        self.call("record_claim",{"subject_id":bret,"predicate":"collects","value":"radios","kind":"self_report","source_turn_id":source})
        self.begin("Forget everything about me.")
        result=self.call("forget_person",{"person_id":bret})
        self.assertEqual(result.outcome,"ok",result)
        self.assertEqual(self.store.people(),[])
        self.assertEqual(self.store.rows("SELECT * FROM recordings"),[])
        self.assertEqual(self.store.rows("SELECT * FROM embeddings"),[])
        self.assertEqual(self.store.claims(),[])
        self.assertEqual(self.state.tracker.tracks,{})
        self.assertTrue(self.state.forgotten)

    def test_question_is_pending_only_after_delivery(self):
        self.begin("Hello.")
        result=self.call("ask_question",{"text":"What should I call you?","addressee_id":None})
        self.assertIsNone(self.state.pending)
        self.state.question_delivered(result.data["question"],None,self.at+4)
        self.begin("I am going camping tomorrow.")
        self.assertEqual(self.store.people(),[])
        self.assertEqual(self.state.pending["text"],"What should I call you?")
        self.call("settle_question",{"status":"superseded"})
        self.assertIsNone(self.state.pending)


class VoiceGrowthTests(MemoryFixture):
    """Owner, October 13: voice evidence should build over time. Bret's saved voice had stayed
    at its three enrollment clips; clean recognized speech now adds to it, within bounds."""
    def recordings(self, person):
        return self.store.rows('SELECT metadata FROM recordings WHERE person_id=?', (person,))

    def say(self, vector, seconds=3., motion=False):
        self.at += 10
        capture = Capture(uuid4().hex, "session", uuid4().bytes*int(2000*seconds), self.at, self.at+seconds,
                          self.at+seconds, voiced_intervals=((0., seconds),), motion_risk=motion)
        self.state.begin(uuid4().hex, capture, Transcript('Tell me about the telescope.', -.01, True),
                         observation(vector, seconds))

    def test_clean_recognized_speech_adds_to_the_saved_voice(self):
        person = self.enroll()
        self.assertEqual(len(self.recordings(person)), 3)
        self.say((.95, .31, 0.))   # Clearly Bret, a little different from what is saved.
        rows = self.recordings(person)
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum('recognized_clean_match' in r['metadata'] for r in rows), 1)
        grown = [r for r in self.state.trace.rows if r['event'] == 'voice_profile_grown']
        self.assertEqual(grown[-1]['recordings'], 4)

    def test_speech_while_moving_short_or_ambiguous_never_grows_it(self):
        person = self.enroll()
        jeff = self.enroll('Jeff', B)
        self.say((.95, .31, 0.), motion=True)              # Servo noise in the clip.
        self.say((.95, .31, 0.), seconds=1.5)              # Too little speech.
        self.say((.7, .65, 0.))                            # Too near Jeff's voice as well.
        self.assertEqual(len(self.recordings(person)), 3)
        self.assertEqual(len(self.recordings(jeff)), 3)
        reasons = [r['reason'] for r in self.state.trace.rows if r['event'] == 'voice_profile_unchanged']
        self.assertIn('too_short_to_grow_profile', reasons)

    def test_growth_is_bounded_per_run_and_skips_what_is_already_saved(self):
        person = self.enroll()
        self.say(A)                                        # Identical to a saved clip.
        self.assertEqual(len(self.recordings(person)), 3)
        for vector in ((.9, .3, .3), (.9, -.3, .3), (.9, .3, -.3), (.9, -.3, -.3), (.8, 0., .6)):
            self.say(vector)
        self.assertEqual(len(self.recordings(person)), 6)  # Three new clips this run, no more.


class InterestBackfillTests(MemoryFixture):
    """October 13: Bret's likes were plain facts, so no news about them was ever searched."""
    def test_an_accepted_self_report_can_be_marked_as_an_interest_and_nothing_else_can(self):
        import io, json
        from contextlib import redirect_stdout
        from r3x.management import main
        from r3x.settings import ScratchPaths
        person = self.enroll()
        source = self.begin('I like astrophotography, telescopes, and astronomy.')
        result = self.call('record_claim', {'subject_id': person, 'predicate': 'likes',
            'value': 'astrophotography, telescopes, and astronomy', 'kind': 'self_report', 'source_turn_id': source})
        claim = result.data['claim_id']
        self.store.close()
        paths = ScratchPaths(self.path.parent)
        out = io.StringIO()
        with redirect_stdout(out):
            main(['mark-interest', claim, 'astrophotography and telescopes'], paths)
        self.assertEqual(json.loads(out.getvalue())['topic'], 'astrophotography and telescopes')
        with self.assertRaisesRegex(ValueError, 'UnknownClaimId'):
            main(['mark-interest', 'missing', 'anything'], paths)
        self.store = Memory(self.path); self.store.start()
        from r3x.news import interest_topics
        self.assertEqual(interest_topics(self.store, [{'identity': person}]), {'astrophotography and telescopes': [person]})
