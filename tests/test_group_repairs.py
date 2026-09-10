from dataclasses import replace
import math
from queue import Queue
import unittest

from r3x.capture import Endpoint, Frame
from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.output import OutputOwner, Delivery
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.segmentation import change_boundaries, split_capture
from r3x.session import Session
from r3x.speakers import SpeakerTracker
from tests.test_memory_core import MemoryFixture, observation, A, B, NS
from tests.test_voice_core import Trace


class AcousticRepairs(unittest.TestCase):
    def test_echo_transitions_preserve_human_prefix_and_suffix_pcm(self):
        endpoint=Endpoint("s")
        captures=[]
        for i in range(104):
            frame=Frame(bytes([i,0])*512,i*.032,(i+1)*.032,20<=i<40)
            capture=endpoint.push(frame,i<64)
            if capture: captures.append(capture)
        self.assertEqual([c.echo_risk for c in captures],[False,True,False])
        self.assertEqual(captures[0].pcm,b"".join(bytes([i,0])*512 for i in range(20)))
        self.assertEqual(captures[1].pcm,b"".join(bytes([i,0])*512 for i in range(20,40)))
        self.assertTrue(captures[2].pcm.startswith(bytes([40,0])*512))
        self.assertAlmostEqual(captures[0].ended_at,captures[1].started_at)
        self.assertAlmostEqual(captures[1].ended_at,captures[2].started_at)

    def test_waiting_for_tts_does_not_gate_human_capture(self):
        trace=Trace()
        class TTS:
            def synthesize(_,text,cancel):
                self.assertFalse(owner.echo_risk())
                yield bytes(1024)
        owner=OutputOwner(TTS(),SyntheticSink(),trace)
        phrases=Queue(); phrases.put("A spoken sentence."); phrases.put(None)
        receipt=Delivery()
        owner.run(phrases,Cancellation(),"t",receipt)
        self.assertEqual(receipt.status,"completed")
        self.assertTrue(owner.echo_risk())  # Actual PCM still carries the echo tail.

    def test_recorded_playback_mask_keeps_speech_before_pcm_arrives(self):
        from tools.check_labeled_session import replay_frames,playback_intervals
        events=[{'event':'tts_first_pcm','turn_id':'t','at':1.},
                {'event':'phrase_completed','turn_id':'t','at':2.,'completed_at':2.}]
        spans=playback_intervals(events)
        meta={'capture_evidence':{'started_at':0.,'voiced_intervals':[(0.,3.)]}}
        parts=list(replay_frames(bytes(96000),meta,spans,'s'))
        self.assertEqual([c.echo_risk for c in parts],[False,True,False])
        self.assertEqual(b''.join(c.pcm for c in parts),bytes(96000))
        self.assertLessEqual(parts[0].ended_at,1.)
        self.assertGreaterEqual(parts[-1].started_at,2.3)

    def test_weak_continuity_preserves_track_without_learning_name_or_drift(self):
        tracker=SpeakerTracker(NS)
        first=tracker.observe(observation(A),0,{})
        tracker.associate(first.track_id,"person")
        weak=(.4,math.sqrt(1-.4**2),0.)
        references=list(tracker.tracks[first.track_id].references)
        result=tracker.observe(observation(weak),1,{})
        self.assertEqual(result.track_id,first.track_id)
        self.assertEqual(result.reason,"tentative_continuity")
        self.assertIsNone(result.person_id)
        self.assertFalse(result.learning_eligible)
        self.assertEqual(result.state,"unresolved")
        self.assertEqual(list(tracker.tracks[first.track_id].references),references)
        self.assertEqual(tracker.tracks[first.track_id].last_at,0)
        short=tracker.observe(observation(B,seconds=.6),2,{})
        self.assertIsNone(short.track_id)
        self.assertEqual(len(tracker.tracks),1)

    def test_ambiguous_continuity_abstains(self):
        tracker=SpeakerTracker(NS)
        tracker.observe(observation(A),0,{})
        tracker.observe(observation(B),1,{})
        result=tracker.observe(observation((.4,.4,math.sqrt(.68))),2,{})
        self.assertEqual(result.reason,"ambiguous_continuity")
        self.assertIsNone(result.track_id)

    def test_change_proposal_needs_support_on_both_sides_and_retains_pcm(self):
        voice=replace(observation(A),windows=((0,2,A),(1,3,A),(2,4,B),(3,5,B)))
        cuts=change_boundaries(voice)
        self.assertEqual(cuts,(2.5,))
        capture=Capture("original","s",bytes(range(256))*625,10,15,15,
                        voiced_intervals=((.2,2.4),(2.6,4.8)))
        parts=split_capture(capture,cuts)
        self.assertEqual(b"".join(c.pcm for c in parts),capture.pcm)
        self.assertEqual(parts[1].source_offset,2.5)
        self.assertEqual(parts[1].parent_capture_id,"original")
        self.assertEqual(parts[1].started_at,12.5)
        self.assertAlmostEqual(parts[1].voiced_intervals[0][0],.1)
        self.assertEqual(change_boundaries(replace(voice,windows=voice.windows[:3])),())


class SemanticRepairs(MemoryFixture):
    def test_weak_track_does_not_inherit_provisional_self_but_can_mention_someone_else(self):
        person=self.introduce()
        weak=(.4,math.sqrt(1-.4**2),0.)
        self.begin("That is Casey over there.",vector=weak)
        self.assertEqual(self.state.current[4].reason,"tentative_continuity")
        self.assertIsNone(self.state.context()["provisional_person_id"])
        result=self.call("introduce_person",{"name":"Casey","aliases":[],"subject":"other","person_id":None,"face_track_id":None})
        self.assertEqual(result.outcome,"ok")
        self.assertIsNone(result.data["track_id"])
        result=self.call("rename_person",{"person_id":person,"name":"Wrong","aliases":[]})
        self.assertEqual(result.outcome,"rejected")

    def test_quiet_decision_makes_one_call_no_speech_no_enrollment_and_next_turn_can_reply(self):
        class Brain:
            calls=0
            def reply(_,snapshot,cancel):
                _.calls+=1
                if _.calls==1:
                    yield ToolCall("quiet","remain_silent",{"reason":"other_addressee"})
                else:
                    yield "Yes, I am here."
        class NoSpeech:
            def synthesize(_,text,cancel):
                raise AssertionError("Silence must not invoke TTS")
        self.begin("Hey Siri, show me the route.")
        brain=Brain()
        session=Session("session",brain,OutputOwner(NoSpeech(),SyntheticSink(),self.state.trace),self.state.trace,self.state)
        receipt=session.process_turn(self.state.current[1],self.state.current[2],0,observation())
        self.assertEqual((receipt.status,receipt.medium),("suppressed","none"))
        self.assertEqual(brain.calls,1)
        self.assertEqual(receipt.completed_text,[])
        self.assertFalse(any(r.get("event")=="speech_generated" for r in self.state.trace.rows))
        self.assertFalse(any(r.get("event")=="voice_learning_progress" for r in self.state.trace.rows))
        self.assertEqual(self.store.claims(),[])
        session.output=OutputOwner(SyntheticTTS(),SyntheticSink(),self.state.trace)
        self.begin("Rex, are you there?")
        receipt=session.process_turn(self.state.current[1],self.state.current[2],0,observation())
        self.assertEqual(receipt.status,"completed")
        self.assertEqual(brain.calls,2)

    def test_bounded_history_keeps_only_complete_silent_tool_exchanges(self):
        session=Session("s",None,None,self.state.trace)
        session.history.extend((
            {"type":"function_call","call_id":"old","name":"remain_silent","arguments":"{}"},
            {"type":"function_call_output","call_id":"old","output":"{}"}))
        for i in range(32):
            session.history.begin_turn()
            session.history.append({"role":"user","content":str(i)})
        self.assertFalse(any(r.get("type")=="function_call_output" for r in session.history_messages()))
        session.history.append({"type":"function_call","call_id":"unfinished","name":"remain_silent","arguments":"{}"})
        self.assertFalse(any(r.get("type")=="function_call" for r in session.history_messages()))

    def test_derived_boundary_cannot_enroll_or_write_personal_facts(self):
        person=self.enroll()
        self.begin("Please remember I collect records.")
        turn,capture,transcript,voice,attribution=self.state.current
        turn += "-derived"
        self.state.begin(turn,replace(capture,capture_id="derived",parent_capture_id="original"),transcript,voice)
        self.assertFalse(self.state.current[4].learning_eligible)
        result=self.state.execute(ToolCall("claim","record_claim",{"subject_id":person,"subject_reference":None,
            "predicate":"collects","value":"records","kind":"self_report","source_turn_id":turn}),
            self.store.revision,Cancellation())
        self.assertEqual(result.outcome,"rejected")
        self.assertEqual(self.store.claims(),[])
