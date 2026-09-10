from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from r3x.contracts import Capture,Cancellation,Transcript
from r3x.dataset import load_manifest,read_wav,write_wav,segment
from r3x.evaluation import Evaluation
from r3x.faces import FaceTracker
from r3x.perception import Perception
from r3x.speakers import SpeakerTracker,SpeakerPolicy,Attribution
from tests.test_memory_core import A,B,C,NS,observation
from tests.test_voice_core import Trace


class EvidenceTests(unittest.TestCase):
    def test_camera_selection_uses_unique_video_name_and_ignores_audio_indices(self):
        from r3x.adapters.camera import select_camera
        from r3x.contracts import ProviderFailure
        listing="[avfoundation] AVFoundation video devices:\n[avfoundation] [0] Dev Camera\n[avfoundation] [1] Other Camera\n[avfoundation] AVFoundation audio devices:\n[avfoundation] [0] Dev Camera"
        self.assertEqual(select_camera(listing,"Dev Camera"),0)
        with self.assertRaises(ProviderFailure):select_camera(listing,"Camera")
        with self.assertRaises(ProviderFailure):select_camera(listing,"Missing")

    def test_unknown_abstention_and_named_coverage_both_count(self):
        tracker=SpeakerTracker(NS)
        profiles={"a":[A],"b":[B]}
        known=tracker.observe(observation(A),0,profiles)
        unknown=tracker.observe(observation(C),10,profiles)
        self.assertEqual(known.person_id,"a")
        self.assertIsNone(unknown.person_id)
        self.assertIsNotNone(unknown.track_id)
        self.assertNotEqual(known.track_id,unknown.track_id)
        eval=Evaluation(profiles)
        clip={"id":"clip","session":"session","device":"synthetic","condition":"near","resolvable":True,
              "overlap":False,"segments":[{"start":0.,"end":3.,"speakers":["a"]}]}
        capture=Capture("c","s",b"",0,3,3)
        for prediction in (known,Attribution(),Attribution(person_id="b")):
            eval.add(clip,capture,prediction,3.,0.)
        report=eval.report()
        self.assertEqual(report["overall"]["named_precision"],.5)
        self.assertEqual(report["overall"]["named_coverage"],2/3)
        self.assertEqual(report["overall"]["wrong_named"],1)
        self.assertFalse(report["proposed_clean_gate_passed"])

    def test_always_abstaining_never_passes_the_gate(self):
        eval=Evaluation(["a"])
        clip={"id":"clip","session":"s","device":"d","condition":"near","resolvable":True,"overlap":False,
              "segments":[{"start":0.,"end":3.,"speakers":["a"]}]}
        for _ in range(100): eval.add(clip,Capture("c","s",b"",0,3,3),Attribution(),3.,0)
        report=eval.report()
        self.assertIsNone(report["clean_enrolled"]["named_precision"])
        self.assertEqual(report["clean_enrolled"]["named_coverage"],0)
        self.assertFalse(report["proposed_clean_gate_passed"])

    def test_window_switch_echo_stale_and_namespace_are_not_named(self):
        tracker=SpeakerTracker(NS)
        profiles={"a":[A],"b":[B]}
        switch=replace(observation(A),windows=((0,2,A),(2,4,B)))
        self.assertEqual(tracker.observe(switch,0,profiles).reason,"window_identity_conflict")
        self.assertIsNone(tracker.observe(observation(A),1,profiles,echo=True).person_id)
        self.assertIsNone(tracker.observe(replace(observation(A),namespace="other"),2,profiles).person_id)
        old=tracker.observe(observation(A),3,profiles).track_id
        new=tracker.observe(observation(A),400,profiles).track_id
        self.assertNotEqual(old,new)
        tracker.associate(new,"a")
        no_profile=tracker.observe(observation(A),401,{})
        self.assertEqual(no_profile.state,"hypothesis")
        self.assertIsNone(no_profile.person_id)

    def test_ambiguous_profiles_and_direction_conflict_abstain(self):
        tracker=SpeakerTracker(NS)
        self.assertEqual(tracker.observe(observation(A),0,{"a":[A],"b":[A]}).reason,"ambiguous_profiles")
        self.assertIsNone(tracker.observe(replace(observation(A),direction_conflict=True),1,{"a":[A]}).track_id)

    def test_faces_expire_and_never_derive_identity(self):
        tracker=FaceTracker()
        first=tracker.update([(0.1,.1,.2,.2)],1.)[0]
        second=tracker.update([(0.11,.1,.2,.2)],1.5)[0]
        self.assertEqual(first["track_id"],second["track_id"])
        self.assertEqual(second["position"],"left")
        self.assertIsNone(second["identity"])
        self.assertTrue(tracker.during(1,2))
        self.assertEqual(tracker.during(2,3),())
        self.assertNotEqual(tracker.update([(.11,.1,.2,.2)],4)[0]["track_id"],first["track_id"])

    def test_saved_segmentation_uses_production_endpoint_and_preserves_pcm(self):
        class VAD:
            def reset(self): pass
            def is_speech(self,pcm): return pcm[:2]==b"\1\0"
        pcm=b"\0\0"*1024+b"\1\0"*16000+b"\0\0"*16000
        captures=list(segment(pcm,"s",VAD()))
        self.assertEqual(len(captures),1)
        self.assertEqual(captures[0].pcm[:1024],pcm[:1024])
        self.assertGreater(sum(b-a for a,b in captures[0].voiced_intervals),.9)
        self.assertIsNone(captures[0].overlap)

    def test_perception_encoder_failure_keeps_text_but_no_identity(self):
        class ASR:
            def transcribe(self,capture,cancel): return Transcript("The original utterance.",-.1,True)
        class Encoder:
            class capability: model=NS
            def analyze(self,capture,cancel): raise RuntimeError("unavailable")
        perceived=Perception(ASR(),Encoder(),Trace()).analyze(Capture("c","s",b"",0,1,1),Cancellation())
        self.assertEqual(perceived.transcript.text,"The original utterance.")
        self.assertIsNone(perceived.voice.embedding)
        self.assertEqual(perceived.voice.reason,"encoder_failed")

    def test_manifest_requires_reviewed_labels_and_detects_data_leakage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp).resolve()
            wav=root/"sample.wav"
            write_wav(wav,b"\1\0"*16000)
            clip={"id":"a","file":wav.name,"sha256":hashlib.sha256(wav.read_bytes()).hexdigest(),"session":"s","device":"d",
                  "condition":"synthetic","split":"evaluation","overlap":False,"resolvable":True,
                  "segments":[{"start":0,"end":1,"speakers":["p"]}]}
            data={"version":1,"roster":{"p":"Participant"},"clips":[clip]}
            path=root/"manifest.json"
            path.write_text(json.dumps(data))
            self.assertEqual(load_manifest(path)[0]["clips"][0]["id"],"a")
            data["clips"].append(dict(clip,id="training",split="enrollment"))
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,"CrossSplitLeakage"): load_manifest(path)
            data["clips"]=[dict(clip,resolvable=None)]
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,"IndependentResolvability"): load_manifest(path)
            data["clips"]=[dict(clip,sha256="bad")]
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,"DigestMismatch"): load_manifest(path)
