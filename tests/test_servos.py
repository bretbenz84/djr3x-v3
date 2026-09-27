"""Synthetic pulse transport only; the normal runner blocks serial/hardware."""
from dataclasses import replace
from queue import Queue
import threading
import unittest

from r3x.contracts import Cancellation, Playback, ProviderFailure
from r3x.output import Delivery, OutputOwner
from r3x.servo_poses import HEAD_PARK, PARK, REST, FULL_DOWN, TUCK
from r3x.servos import ServoOwner, ServoRequest, TalkingMotion


class Clock:
    def __init__(self): self.now = 1.
    def __call__(self): return self.now
    def wait(self, seconds): self.now += seconds


class PulsePort:
    def __init__(self):
        self.pose = {**dict(HEAD_PARK), 5: 6000, 6: 6000, 7: 5984, **dict(PARK)}
        self.packets = []
        self.channel = 0
        self.closed = False
        self.stall = False
        self.short_write = False
        self.short_read = False
        self.on_target = lambda: None

    def write(self, packet):
        self.packets.append(packet)
        if self.short_write: return 0
        if packet[0] == 0x90:
            self.channel = packet[1]
        elif packet[0] in (0x84, 0x9F):
            target = ({packet[1]: packet[2] + 128 * packet[3]} if packet[0] == 0x84 else
                      {packet[2] + i: packet[3 + i*2] + 128 * packet[4 + i*2]
                       for i in range(packet[1])})
            if not self.stall: self.pose.update(target)
            self.on_target()
        return len(packet)

    def read(self, count):
        return b'' if self.short_read else self.pose[self.channel].to_bytes(2, 'little')

    def close(self): self.closed = True

    @property
    def targets(self): return [p for p in self.packets if p[0] in (0x84, 0x9F)]


class ServoTests(unittest.TestCase):
    def setUp(self):
        self.port, self.clock = PulsePort(), Clock()
        self.owner = ServoOwner(lambda: self.port, medium='simulation',
                                clock=self.clock, wait=self.clock.wait)
        self.addCleanup(self.owner.close)

    def request(self, kind='startup', **kwargs):
        return ServoRequest('request', self.owner.version, kind, self.clock() + 120., **kwargs)

    def execute(self, kind='startup'):
        return self.owner.execute(self.request(kind), Cancellation())

    def test_start_only_reads_and_close_does_not_park(self):
        self.assertEqual(self.port.packets, [])
        self.owner.start()
        self.owner.close()
        self.owner.close()
        self.assertEqual(self.port.targets, [])
        self.assertTrue(self.port.closed)

    def test_wake_overlaps_lanes_then_shutdown_parks_and_latches(self):
        self.owner.start()
        wake = self.execute()
        self.assertEqual(wake.status, 'pulses_reached')
        self.assertEqual(wake.medium, 'simulation')
        self.assertEqual(wake.evidence, 'controller_output_pulses_only')
        self.assertEqual({ch:self.port.pose[ch] for ch in (8,9,10)}, dict(REST))
        self.assertEqual(self.port.pose[0], 5472)
        self.assertEqual(self.port.pose[1], 3600)
        targets = self.port.targets
        first_throttle = next(i for i,p in enumerate(targets) if p[0] == 0x9F)
        neck = [i for i,p in enumerate(targets) if p[:2] == bytes((0x84,0))]
        self.assertLess(first_throttle, neck[1])
        result = self.execute('shutdown')
        self.assertEqual(result.completed_lanes, (0, 1))
        self.assertEqual(result.status, 'pulses_reached')
        for ch, value in HEAD_PARK + PARK:
            self.assertEqual(self.port.pose[ch], value)
        count = len(self.port.targets)
        self.assertEqual(self.execute().status, 'rejected')
        self.assertEqual(self.execute('talking').status, 'rejected')
        self.assertEqual(len(self.port.targets), count)

    def test_zero_or_partial_cold_pose_never_assumes_old_park(self):
        self.port.pose[8] = 0
        with self.assertRaises(ValueError): self.owner.start()
        self.assertTrue(self.port.closed)
        self.assertEqual(self.port.targets, [])

    def test_unsafe_throttle_rejects_before_head_moves(self):
        self.port.pose.update({8:7200,9:5600,10:8800})
        self.owner.start()
        self.assertEqual(self.execute().status, 'rejected')
        self.assertEqual(self.port.targets, [])

    def test_full_down_retracts_shoulder_before_tuck_and_park(self):
        self.port.pose.update(dict(FULL_DOWN))
        self.owner.start()
        self.assertEqual(self.execute('park').status, 'pulses_reached')
        packets = [p for p in self.port.targets if p[0] == 0x9F]
        poses = [{8+i:p[3+i*2]+128*p[4+i*2] for i in range(3)} for p in packets]
        self.assertEqual(poses[0], {**dict(FULL_DOWN),8:2176})
        self.assertEqual(poses[-2:], [dict(TUCK),dict(PARK)])

    def test_stale_expired_and_duplicate_requests_do_not_replay(self):
        self.owner.start()
        request = self.request()
        for bad in (replace(request,version=99),replace(request,expires_at=0.),
                    replace(request,expires_at=float('nan'))):
            self.assertEqual(self.owner.execute(bad,Cancellation()).status, 'rejected')
        self.assertEqual(self.port.targets, [])
        self.owner.execute(request,Cancellation())
        before = len(self.port.targets)
        self.assertEqual(self.owner.execute(request,Cancellation()).status, 'rejected')
        self.assertEqual(len(self.port.targets), before)

    def test_cancellation_holds_then_requires_fresh_park(self):
        self.owner.start()
        token = Cancellation()
        self.port.on_target = token.event.set
        result = self.owner.execute(self.request(),token)
        self.assertEqual(result.status, 'cancelled')
        self.assertEqual(self.owner.state, 'stopped')
        self.assertEqual(self.execute().status, 'rejected')
        self.port.on_target = lambda: None
        self.assertEqual(self.execute('park').status, 'pulses_reached')

    def test_short_write_faults_without_retry(self):
        self.owner.start()
        self.port.short_write = True
        result = self.execute()
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.reason, 'MaestroShortWrite')
        before = len(self.port.packets)
        self.assertEqual(self.execute('park').status, 'rejected')
        self.assertEqual(len(self.port.packets), before)

    def test_short_read_closes_partial_start(self):
        self.port.short_read = True
        with self.assertRaises(ProviderFailure): self.owner.start()
        self.assertTrue(self.port.closed)
        self.assertEqual(self.port.targets, [])

    def test_stalled_pulses_timeout_without_completion(self):
        self.owner.start()
        self.port.stall = True
        result = self.execute()
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.reason, 'ServoArrivalTimeout')
        self.assertLess(self.clock(), 27.)
        self.assertEqual(self.owner.state, 'faulted')

    def test_profiles_precede_targets_and_throttle_targets_are_atomic(self):
        self.owner.start()
        self.execute()
        speeds, accels = set(), set()
        for packet in self.port.packets:
            if packet[0] == 0x87: speeds.add(packet[1])
            if packet[0] == 0x89: accels.add(packet[1])
            if packet[0] in (0x84,0x9F):
                channels = {packet[1]} if packet[0]==0x84 else {8,9,10}
                self.assertTrue(channels <= speeds & accels)
                if packet[0] == 0x84: self.assertLess(packet[1], 8)

    def test_busy_owner_rejects_other_writer(self):
        self.owner.start()
        with self.owner._lock:
            self.assertEqual(self.execute('park').reason, 'ServoBusy')
        self.assertEqual(self.port.targets, [])

    def test_talking_requires_wake_and_keeps_microphone_axis_fixed(self):
        self.owner.start()
        self.assertEqual(self.execute('talking').status, 'rejected')
        self.execute()
        self.port.packets.clear()
        self.assertEqual(self.execute('talking').status, 'pulses_reached')
        self.assertTrue(any(p[0]==0x9F for p in self.port.targets))
        channels = {p[1] for p in self.port.targets if p[0]==0x84}
        self.assertTrue({0,1,2,3,4,5} <= channels)
        self.assertNotIn(7,channels)

    def test_head_elbow_and_wrist_repeat_without_waiting_for_throttle_lane(self):
        self.owner.start()
        self.execute()
        self.port.packets.clear()
        result = self.execute('talking')
        self.assertEqual(result.status,'pulses_reached')
        self.assertEqual(result.completed_lanes,(0,1,2,3,4,5))
        def targets(ch):
            return [p[2]+128*p[3] for p in self.port.targets if p[:2]==bytes((0x84,ch))]
        self.assertGreater(len(targets(0)),3)
        self.assertGreater(len(targets(3)),4)
        self.assertEqual(set(targets(3)),{6000,6200,6520,6640})
        self.assertEqual(set(targets(2)),{4200,4320,4440})
        self.assertGreater(len(targets(4)),2)
        self.assertGreater(len(targets(5)),2)
        self.assertGreater(len(set(targets(0))),1)
        self.assertEqual(set(targets(4)),{7100,6740})
        self.assertEqual(set(targets(5)),{7120,4880})

    def test_repeating_lanes_drain_sent_targets_before_success(self):
        self.owner.start()
        self.execute()
        pending = {}
        original_write,original_read = self.port.write,self.port.read
        def delayed_write(packet):
            previous = dict(self.port.pose)
            result = original_write(packet)
            if packet[0] in (0x84,0x9F):
                channels = (packet[1],) if packet[0]==0x84 else range(packet[2],packet[2]+packet[1])
                for ch in channels:
                    pending[ch] = [self.port.pose[ch],20 if ch==5 else 3]
                    self.port.pose[ch] = previous[ch]
            return result
        def delayed_read(count):
            ch = self.port.channel
            if ch in pending:
                pending[ch][1] -= 1
                if pending[ch][1]<=0:
                    self.port.pose[ch] = pending.pop(ch)[0]
            return original_read(count)
        self.port.write,self.port.read = delayed_write,delayed_read
        self.assertEqual(self.execute('talking').status,'pulses_reached')
        self.assertEqual(pending,{})

    def test_talking_head_offsets_clamp_at_calibrated_limits(self):
        from r3x.servo_poses import talking_lanes,LIMITS
        current = {**self.port.pose,**dict(REST)}
        for endpoint in (0,1):
            anchor = {ch:LIMITS[ch][endpoint] for ch in (0,1,2)}
            lanes = talking_lanes(current,anchor)
            for lane in lanes[:2]:
                for beat in lane:
                    for ch,value in beat.targets:
                        self.assertLessEqual(LIMITS[ch][0],value)
                        self.assertLessEqual(value,LIMITS[ch][1])

    def test_talking_worker_stops_before_park_and_never_resumes(self):
        self.owner.start()
        self.execute()
        motion = TalkingMotion(self.owner)
        reached = threading.Event()
        self.port.on_target = reached.set
        motion.start('turn', Cancellation())
        self.assertTrue(reached.wait(1.))
        motion.stop()
        self.assertEqual(self.owner.state, 'active')
        self.assertEqual(self.execute('park').status, 'pulses_reached')
        before = len(self.port.targets)
        motion.start('late',Cancellation())
        motion.stop()
        self.assertEqual(len(self.port.targets), before)

    def test_cancel_before_request_sends_no_targets(self):
        self.owner.start()
        token = Cancellation()
        token.event.set()
        self.assertEqual(self.owner.execute(self.request(), token).status, 'cancelled')
        self.assertEqual(self.port.targets, [])

    def test_expiry_during_profile_writes_cannot_send_target(self):
        self.owner.start()
        write = self.port.write
        def delayed(packet):
            if packet[0] == 0x87:
                self.clock.now += 121.
            return write(packet)
        self.port.write = delayed
        self.assertEqual(self.execute().reason, 'ServoRequestExpired')
        self.assertEqual(self.port.targets, [])

    def test_repeated_interrupted_talking_does_not_ratchet_headlift(self):
        self.owner.start()
        self.execute()
        for _ in range(3):
            token = Cancellation()
            self.port.on_target = token.event.set
            result = self.owner.execute(self.request('talking'),token)
            self.assertEqual(result.status,'cancelled')
            self.assertEqual(self.port.pose[1],3760)
            self.assertEqual(self.port.pose[0],5732)

    def test_production_output_drives_real_executor_with_synthetic_port(self):
        self.owner.start()
        self.execute()
        self.port.packets.clear()
        moved = threading.Event()
        def observe():
            channels = {p[1] for p in self.port.targets if p[0]==0x84}
            if {0,1,2,3,4,5} <= channels:
                moved.set()
        self.port.on_target = observe
        motion = TalkingMotion(self.owner)
        case = self
        class TTS:
            def synthesize(self, text, cancel): yield b'pcm'
        class Sink:
            def play(self, chunks, cancel, started):
                list(chunks)
                case.assertEqual(case.port.targets, [])
                started(1.)
                case.assertTrue(moved.wait(1.))
                return Playback(1,1.,2.,0)
        class Trace:
            clock = staticmethod(lambda: 1.)
            def __init__(self): self.rows = []
            def emit(self, event, **data): self.rows.append((event,data))
        trace = Trace()
        output = OutputOwner(TTS(),Sink(),trace,speech_motion=motion)
        queue = Queue()
        queue.put('A synthetic spoken phrase.')
        queue.put(None)
        delivery = Delivery()
        output.run(queue,Cancellation(),'turn',delivery)
        self.assertEqual(delivery.status,'completed')
        self.assertTrue(any(event=='talking_motion' and row['medium']=='simulation'
                            for event,row in trace.rows))
        self.assertEqual(self.owner.state,'active')
        self.assertEqual(self.execute('shutdown').status,'pulses_reached')


class TalkingOutputTests(unittest.TestCase):
    def test_actual_audio_start_and_finally_stop_own_talking_motion(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                calls = []
                class TTS:
                    def synthesize(self, text, cancel):
                        self_test.assertEqual(calls, [])
                        yield b'pcm'
                class Motion:
                    def start(self, turn, cancel): calls.append('start')
                    def stop(self): calls.append('stop')
                    def cancel(self): calls.append('cancel')
                class Sink:
                    def play(self, chunks, cancel, started):
                        list(chunks)
                        self_test.assertEqual(calls, [])
                        started(1.)
                        self_test.assertEqual(calls, ['start'])
                        if fail: raise ProviderFailure('SyntheticPlaybackFailure')
                        return Playback(1,1.,2.,0)
                class Trace:
                    clock = staticmethod(lambda: 1.)
                    def emit(self, *args, **kwargs): pass
                self_test = self
                owner = OutputOwner(TTS(),Sink(),Trace(),speech_motion=Motion())
                queue = Queue()
                queue.put('Hello there.')
                queue.put(None)
                delivery = Delivery()
                owner.run(queue,Cancellation(),'turn',delivery)
                self.assertEqual(calls, ['start','stop'])
                self.assertEqual(delivery.status, 'failed' if fail else 'completed')
                self.assertTrue(owner._motion_lock.acquire(blocking=False))
                owner._motion_lock.release()
