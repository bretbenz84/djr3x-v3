"""ReSpeaker contracts with synthetic PCM/USB/evidence; no devices or models."""
from dataclasses import replace
import json
import math
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from r3x.adapters.audio import Microphone, selected_pcm
from r3x.adapters.flex import FlexControl
from r3x.contracts import Capture, Cancellation, ProviderFailure, Transcript
from r3x.direction import Direction, DirectionSample, summarize
from r3x.output import TextOutput
from r3x.perception import Perception
from r3x.session import Session
from r3x.settings import Settings
from tests.test_memory_core import MemoryFixture, observation, A, B, C, NS
from tests.test_voice_core import Brain, Trace


class MicrophoneTests(unittest.TestCase):
    def test_only_processed_channel_survives_with_saturating_gain(self):
        data = struct.pack('<hhhhhh', 100, 30000, -30000, 32000, 30000, -32000)
        result = selected_pcm(data, 2, 1.5)
        self.assertEqual(struct.unpack('<hhh', result), (150, -32768, 32767))

    def test_live_adapter_opens_stereo_and_emits_only_channel_zero(self):
        config = Settings(microphone_mode='respeaker-flex', input_gain=1.5,
                          input_device_name='ReSpeaker Flex XVF3800',
                          output_device_name='ReSpeaker Flex XVF3800')
        stream = Mock(time=10.)
        sd = SimpleNamespace(query_devices=lambda: [dict(name=config.input_device_name,
            max_input_channels=6, max_output_channels=2)], RawInputStream=Mock(return_value=stream))
        mic = Microphone(config, lambda: False, clock=lambda: 10.)
        with patch.dict('sys.modules', sounddevice=sd):
            mic.start()
        self.assertEqual(sd.RawInputStream.call_args.kwargs['channels'], 2)
        callback = sd.RawInputStream.call_args.kwargs['callback']
        callback(struct.pack('<hhhh', 100, 30000, -100, 30000), 2,
                 SimpleNamespace(inputBufferAdcTime=10.), False)
        frame = mic.read()
        self.assertEqual(struct.unpack('<hh', frame.pcm), (150, -150))
        self.assertFalse(frame.echo_risk)
        self.assertIn('channel=0; gain=1.5', mic.source_device)
        mic.cancel()
        mic.close()

    def test_wrong_output_fails_before_opening_stream(self):
        config = Settings(microphone_mode='respeaker-flex', input_device_name='Flex XVF3800',
                          output_device_name='Speakers')
        sd = SimpleNamespace(query_devices=lambda: [dict(name='Flex XVF3800',max_input_channels=6,max_output_channels=2),
            dict(name='Speakers',max_input_channels=0,max_output_channels=2)], RawInputStream=Mock())
        with patch.dict('sys.modules', sounddevice=sd), self.assertRaises(ProviderFailure):
            Microphone(config, lambda: False).start()
        sd.RawInputStream.assert_not_called()

    def test_invalid_configuration(self):
        for args in ({'input_gain':float('nan')}, {'input_gain':True}, {'microphone_mode':'auto'},
                     {'direction_enabled':True}, {'direction_enabled':1}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                Settings(**args)


class DirectionTests(unittest.TestCase):
    def samples(self, bearings, energy=100000):
        return [DirectionSample(1+i*.1, 90, b, energy, True) for i,b in enumerate(bearings)]

    def test_wraparound_and_stale_window(self):
        rows = self.samples([179,-179,180,-178])
        result = summarize(rows,1,2)
        self.assertEqual(result['status'], 'observed')
        self.assertGreater(abs(result['bearing_deg']), 175)
        self.assertEqual(summarize(rows,3,4)['status'], 'unavailable')

    def test_low_energy_repetition_and_playback_cannot_vote(self):
        rows = self.samples([90]*16,20000)
        rows += [replace(r,eligible=False) for r in self.samples([-90]*10)]
        self.assertIsNone(summarize(rows,0,5)['bearing_deg'])

    def test_competing_directions_remain_ambiguous(self):
        result = summarize(self.samples([90,-90]*8),0,5)
        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(result['bearing_deg'])
        self.assertEqual(len(result['clusters']),2)

    def test_fresh_beam_beats_lagging_doa_and_self_audio_is_excluded(self):
        control = Mock()
        control.read.side_effect=lambda name: {'doa':(90,1),'energy':(0,0,0,100000),
                                                'azimuth':(0,0,0,3*math.pi/2)}[name]
        trace = Trace()
        direction = Direction(control, clock=trace.clock)
        for _ in range(3):
            direction.poll(); trace.at += .1
        self.assertAlmostEqual(direction.during(99,101)['bearing_deg'],-90)
        direction.echo_risk=lambda: True
        direction.poll()
        self.assertFalse(direction.samples[-1].eligible)
        direction.cancel()
        self.assertEqual(direction.during(99,101)['status'],'unavailable')
        direction.close()
        control.close.assert_called_once()

    def test_control_transport_is_read_only_and_bounded(self):
        control=FlexControl()
        control.device=Mock()
        control.device.ctrl_transfer.return_value=b'\0'+struct.pack('<HH',270,1)
        self.assertEqual(control.read('doa'),(270,1))
        self.assertEqual(control.device.ctrl_transfer.call_args.args,(0xC0,0,0x92,20,5))
        control.device.ctrl_transfer.return_value=b'\x40'
        with patch('time.sleep'), self.assertRaises(ProviderFailure): control.read('doa')
        self.assertEqual(control.device.ctrl_transfer.call_count,4)

    def test_provider_failure_drops_old_evidence(self):
        direction=Direction(Mock(),trace=Trace())
        direction.samples.extend(self.samples([90]*5))
        direction.control.read.side_effect=ProviderFailure('FlexInvalidResponse')
        direction.run()
        self.assertEqual(direction.during(0,5)['status'],'unavailable')
        self.assertEqual(len(direction.samples),0)


class DirectionContextTests(MemoryFixture):
    def test_competing_directions_reject_learning_but_missing_direction_does_not(self):
        capture=Capture('competing','session',b'\0'*96000,110,113,114)
        asr=SimpleNamespace(transcribe=lambda c,k: Transcript('My name is Alice.',0.,True))
        encoder=SimpleNamespace(analyze=lambda c,k: observation(A))
        direction=Mock()
        for status in ('ambiguous','unavailable'):
            direction.during.return_value={'status':status,'bearing_deg':None}
            perceived=Perception(asr,encoder,Trace(),direction=direction).analyze(capture,Cancellation())
            self.state.begin(status,replace(capture,capture_id=status),perceived.transcript,perceived.voice)
            self.assertEqual(self.state.current[4].learning_eligible,status=='unavailable')
            if status=='ambiguous':
                result=self.call('introduce_person',{'name':'Alice','aliases':[],
                    'subject':'self','person_id':None,'face_track_id':None})
                self.assertEqual(result.outcome,'ok')  # A claimed name can still be heard.
                self.assertIsNone(result.data['track_id'])
                self.assertEqual(self.state.enrollments,{})
                self.assertEqual(self.store.profiles(NS),{})

    def test_two_voices_share_a_direction_without_becoming_one_person(self):
        first=self.enroll('Alice',A)
        second=self.enroll('Bob',B)
        trace=Trace()
        brain=Brain('I hear you.')
        session=Session('session',brain,TextOutput(trace),trace,state=self.state)
        bearings={'status':'observed','bearing_deg':30.,'frame':'microphone_array',
                  'started_at':110.,'ended_at':113.,'identity_evidence':False}
        for i,(vector,person) in enumerate(((A,first),(B,second),(C,None))):
            capture=Capture('d'+str(i),'session',b'\0'*96000,110+i*10,113+i*10,114+i*10)
            evidence=dict(bearings,started_at=capture.started_at,ended_at=capture.speech_ended_at)
            asr=SimpleNamespace(transcribe=lambda c,k: Transcript('Hello from this side.',0.,True))
            encoder=SimpleNamespace(analyze=lambda c,k: observation(vector))
            direction=Mock()
            direction.during.return_value=evidence
            perceived=Perception(asr,encoder,trace,direction=direction).analyze(capture,Cancellation())
            result=session.process_turn(capture,perceived.transcript,session.revision,observation=perceived.voice,
                faces=({'track_id':'face-a','identity':first,'position':'left'},
                       {'track_id':'face-b','identity':second,'position':'right'}))
            self.assertEqual(result.status,'completed')
            snapshot=brain.snapshots[-1]
            self.assertEqual(snapshot.context['speaker']['person_id'],person)
            self.assertEqual(snapshot.context['speaker']['direction'],evidence)
            historical=json.loads(snapshot.messages[-2]['content'].split(': ',1)[1])
            self.assertEqual(len(historical['faces']),2)
            self.assertEqual(historical['speaker']['direction'],evidence)
        self.assertEqual(brain.calls,3)
        self.store.close(); self.store.start()
        rows=self.store.rows("SELECT evidence FROM turns WHERE capture_id IN ('d0','d1','d2')")
        self.assertEqual(len(rows),3)
        self.assertTrue(all(json.loads(r['evidence'])['direction']['bearing_deg']==30 for r in rows))
