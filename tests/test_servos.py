"""Synthetic pulse transport only; the normal runner blocks serial/hardware."""
from dataclasses import replace
from queue import Queue
import threading
import unittest

from r3x.contracts import Cancellation, Playback, ProviderFailure
from r3x.output import Delivery, OutputOwner
from r3x.servo_poses import AWAKE_ARMS, HEAD_PARK, NECK_PER_DEG, PARK, POKER, POKER_DRIVE, POKER_UP, REST, FULL_DOWN, THROTTLE, TUCK, UNPOWERED_REST
from r3x.servos import ARM_RETURN
from r3x.servos import ServoOwner, ServoRequest, TalkingMotion


class Clock:
    def __init__(self): self.now = 1.
    def __call__(self): return self.now
    def wait(self, seconds): self.now += seconds


class PulsePort:
    def __init__(self):
        self.pose = dict(UNPOWERED_REST)
        self.packets = []
        self.channel = 0
        self.closed = False
        self.stall = False
        self.short_write = False
        self.short_read = False
        self.on_target = lambda: None
        self.period_reads = 0   # Reads before a target reaches an off channel (the 20 ms period).
        self._pending = {}

    def write(self, packet):
        self.packets.append(packet)
        if self.short_write: return 0
        if packet[0] == 0x90:
            self.channel = packet[1]
        elif packet[0] in (0x84, 0x9F):
            target = ({packet[1]: packet[2] + 128 * packet[3]} if packet[0] == 0x84 else
                      {packet[2] + i: packet[3 + i*2] + 128 * packet[4 + i*2]
                       for i in range(packet[1])})
            if self.period_reads:
                self._pending, self._lag = {**self._pending, **target}, self.period_reads
            elif not self.stall: self.pose.update(target)
            self.on_target()
        return len(packet)

    def read(self, count):
        if self._pending:
            self._lag -= 1
            if self._lag < 0:
                self.pose.update(self._pending)
                self._pending = {}
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
        self.assertEqual(result.completed_lanes, (0, 1, 2))   # Head/visor, throttle, and the poker arm back to its park.
        self.assertEqual(result.status, 'pulses_reached')
        for ch, value in HEAD_PARK + PARK:
            self.assertEqual(self.port.pose[ch], value)
        count = len(self.port.targets)
        self.assertEqual(self.execute().status, 'rejected')
        self.assertEqual(self.execute('talking').status, 'rejected')
        self.assertEqual(len(self.port.targets), count)

    def test_arrival_timeout_names_the_channel_that_never_arrived(self):
        # First live run October 10: shutdown failed with a bare ServoArrivalTimeout, so the
        # channel the board would not move (its stored range clamps targets) was unknown.
        self.owner.start()
        self.execute()
        original_write = self.port.write
        def clamped_write(packet):
            if packet[0] == 0x84 and packet[1] == 3:
                packet = bytes((0x84, 3, 6400 & 127, 6400 >> 7))   # The board stops the visor short.
            return original_write(packet)
        self.port.write = clamped_write
        result = self.execute('shutdown')
        self.assertEqual(result.status, 'failed')
        self.assertEqual(result.reason, 'ServoArrivalTimeout:ch3@6400->6976')
        self.assertEqual(self.owner.state, 'faulted')

    def test_powered_off_robot_holds_unpowered_rest_then_wakes(self):
        self.port.pose = {ch: 0 for ch in self.port.pose}
        self.owner.start()
        self.assertEqual(self.owner.energized_channels, tuple(range(11)) + (11, 12, 13))
        self.assertEqual(self.port.pose, dict(UNPOWERED_REST))
        self.assertEqual(self.owner.commanded, dict(UNPOWERED_REST))
        self.assertEqual(self.execute().status, 'pulses_reached')

    def test_partly_off_arm_keeps_live_joints_where_they_are(self):
        self.port.pose.update({8: 0, 9: 9000})
        self.owner.start()
        self.assertEqual(self.owner.energized_channels, (8,))
        self.assertEqual({ch: self.port.pose[ch] for ch in (8, 9, 10)}, {8: 9088, 9: 9000, 10: 2048})

    def test_wake_waits_for_the_board_to_apply_first_pulses(self):
        # Field September 28: channels 5, 7-10 off; wake read them 4 ms after the
        # first targets, still 0, and rejected InvalidServoPulse.
        self.port.pose.update({5: 0, 7: 0, 8: 0, 9: 0, 10: 0})
        self.port.period_reads = 8
        self.owner.start()
        self.port.period_reads = 0
        self.assertEqual(self.owner.energized_channels, (5, 7, 8, 9, 10))
        self.assertEqual(self.execute().status, 'pulses_reached')

    def test_board_that_never_applies_first_pulses_fails_start(self):
        self.port.pose.update({5: 0})
        self.port.stall = True
        with self.assertRaises(ProviderFailure): self.owner.start()
        self.assertTrue(self.port.closed)

    def test_live_out_of_range_reading_rejects_without_writing(self):
        self.port.pose.update({0: 0, 4: 100})
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
        self.assertTrue(result.reason.startswith('ServoArrivalTimeout:ch'), result.reason)
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
                if packet[0] == 0x84: self.assertNotIn(packet[1], (8,9,10))

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
        self.assertEqual(result.completed_lanes,(0,1,2,3,4))
        def targets(ch):
            return [p[2]+128*p[3] for p in self.port.targets if p[:2]==bytes((0x84,ch))]
        self.assertGreater(len(targets(3)),4)
        self.assertEqual(set(targets(3)),{5612,6132,5492,5932})   # Around the 1433 us open neutral; lower is wider.
        # Owner, October 21: the head moves as one, somewhere different each time, around the
        # anchor (the level startup head), instead of the same three moves over and over.
        from r3x.servo_poses import NECK_PER_DEG,head_pose
        self.assertEqual(len({len(targets(ch)) for ch in (0,1,2,11)}),1)
        self.assertGreater(len(set(targets(0))),4)
        self.assertTrue(all(abs(n-5472)<=7*NECK_PER_DEG+1 and abs(l-3600)<=160 for n,l in zip(targets(0),targets(1))))
        for left,right in zip(targets(2),targets(11)):
            pitch,tilt=head_pose({2:left,11:right})
            self.assertLessEqual(abs(pitch),.07);self.assertLessEqual(abs(tilt),.11)   # A small nod and cock.
        # The arm moves as a whole: elbow and wrist together, between V2's neutral poses.
        self.assertGreater(len(targets(4)),2)
        self.assertEqual(len(targets(4)),len(targets(5)))
        self.assertEqual(set(zip(targets(4),targets(5))),{(7100,7120),(6740,4880)})
        # The head starts first; the visor and arm follow a moment later, not all at once.
        first=lambda ch:next(i for i,p in enumerate(self.port.targets) if p[:2]==bytes((0x84,ch)))
        self.assertLess(first(0),first(3));self.assertLess(first(0),first(4))
        # October 21: the poker arm sways while he speaks (it did nothing before).
        self.assertGreater(len(set(targets(6))),1);self.assertGreater(len(set(targets(12))),1)

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
            anchor = {ch:LIMITS[ch][endpoint] for ch in (0,1,2,11)}
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

    def test_a_finished_line_lets_the_arms_run_on_and_an_interrupted_one_holds_all(self):
        # Owner, October 21: when a line ended every arm froze mid-move, then nothing moved for
        # seconds. A line played to its end leaves the arms and visor running on to the targets
        # already sent; only the head is held. Interrupted (someone speaks), everything holds.
        from r3x.servos import COAST
        self.owner.start()
        self.execute()
        hold = self.owner._hold
        for coasting in (COAST, frozenset()):
            token, sent, held = Cancellation(), [], []
            def cancel_later():
                sent.append(1)
                if len(sent) == 12:
                    self.owner.coasting = coasting
                    token.event.set()
            self.port.on_target = cancel_later
            self.owner._hold = lambda channels: (held.append(set(channels)), hold(channels))
            self.assertEqual(self.owner.execute(self.request('talking'), token).status, 'cancelled')
            self.port.on_target, self.owner._hold = (lambda: None), hold
            self.owner.coasting = frozenset()
            channels = set().union(*held)   # What the cancellation held.
            self.assertTrue(channels & {0, 1, 2, 11})                          # The head is held either way.
            if coasting:
                self.assertFalse(channels & COAST, channels)                       # The arms run on.
                self.assertGreater(self.owner.motion_until, self.clock() + 1.)     # Still motion meanwhile.
            else:
                self.assertTrue(channels & COAST, channels)                        # Everything holds.

    def test_repeated_interrupted_talking_does_not_ratchet_headlift(self):
        self.owner.start()
        self.execute()
        for _ in range(3):
            token = Cancellation()
            self.port.on_target = token.event.set
            result = self.owner.execute(self.request('talking'),token)
            self.assertEqual(result.status,'cancelled')
            # Each emphasis is measured from the startup anchor, never from where the last stopped.
            self.assertLessEqual(abs(self.port.pose[0]-5472),7*NECK_PER_DEG+1)
            self.assertLessEqual(abs(self.port.pose[1]-3600),160)

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


class ArmReturnTests(unittest.TestCase):
    """October 3: after a drive the head and other servos waited for the arm to un-park."""
    setUp, request, execute = ServoTests.setUp, ServoTests.request, ServoTests.execute

    def parked(self):
        self.owner.start()
        self.assertEqual(self.execute().status, 'pulses_reached')
        self.assertEqual(self.execute('base_park').status, 'pulses_reached')
        self.assertTrue({0, 1, 2, 4, 6, *THROTTLE} <= self.owner.held_channels)

    def test_head_is_free_the_moment_the_drive_ends_while_the_arm_is_still_out(self):
        self.parked()
        self.owner.begin_arm_return()
        self.assertEqual(self.owner.held_channels, set(ARM_RETURN))   # The head is free; the arms wait.
        self.assertFalse(self.owner._base_held)
        self.assertTrue(self.owner.motion_risk())         # The arm is about to move: not a still moment.
        look = self.owner.execute(self.request('pose', pose={0: 4000}, hold_pose=False), Cancellation())
        self.assertEqual(look.status, 'pulses_reached')    # Before the arm has moved at all.
        self.assertEqual(self.port.pose[0], 4000)
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(PARK))
        talk = self.owner.execute(self.request('talking'), Cancellation())
        self.assertEqual(talk.status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(PARK))   # Talking left the arm alone.

    def test_nothing_else_commands_the_arm_until_it_is_back_at_rest(self):
        self.parked()
        self.owner.begin_arm_return()
        for kind in ('base_park', 'shutdown'):
            early = self.execute(kind)
            self.assertEqual((early.status, early.reason), ('rejected', 'ServoBusy'))
        self.assertFalse(self.owner.yield_stale_hold())
        receipt = self.owner.return_arm()
        self.assertEqual(receipt.status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(REST))
        # October 10: the hero elbow, the poker arm and its elbow/wrist come back to their
        # awake poses too, not only the throttle.
        self.assertEqual({ch: self.port.pose[ch] for ch in (4, 6)}, dict(AWAKE_ARMS))
        self.assertEqual({ch: self.port.pose[ch] for ch in POKER}, dict(POKER_UP))
        self.assertFalse(self.owner.held_channels & set(ARM_RETURN))
        self.assertFalse(self.owner._arm_returning)
        self.assertEqual(self.owner.execute(self.request('pose', pose='throttle_offer'), Cancellation()).status,
                         'pulses_reached')

    def test_the_arm_returns_beside_running_head_motion(self):
        import threading
        self.parked()
        self.owner.begin_arm_return()
        results = {}
        worker = threading.Thread(target=lambda: results.update(arm=self.owner.return_arm()))
        worker.start()
        for index in range(6):
            pose = self.owner.execute(self.request('pose', pose={0: 4000 + 200*index}, hold_pose=False), Cancellation())
            self.assertEqual(pose.status, 'pulses_reached')
        worker.join(5)
        self.assertEqual(results['arm'].status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(REST))

    def test_a_stop_holds_the_arm_where_it_is(self):
        self.parked()
        self.owner.begin_arm_return()
        self.port.stall = True                              # The arm never arrives...
        self.port.on_target = self.owner.cancel             # ...and a stop comes while it travels.
        receipt = self.owner.return_arm()
        self.assertEqual(receipt.status, 'cancelled')
        self.assertTrue(set(ARM_RETURN) <= self.owner.held_channels)
        self.assertFalse(self.owner._arm_returning)


    def test_a_kept_arm_stays_tucked_through_release_and_aged_holds(self):
        # Owner, October 11: the poker arm stays raised while something is to his left.
        self.parked()
        self.owner.begin_arm_return()
        self.assertEqual(self.owner.return_arm(keep={'poker'}).status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in POKER}, dict(POKER_DRIVE))
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(REST))
        self.assertEqual(self.owner.held_channels, {6, *POKER})
        self.assertFalse(self.owner.yield_stale_hold())       # Nothing but the kept arm is held.
        look = self.owner.execute(self.request('pose', pose='look_left'), Cancellation())
        self.assertEqual(look.status, 'pulses_reached')
        self.owner.pose_held_at = self.clock() - 1000.
        self.assertTrue(self.owner.yield_stale_hold())        # The aged look gives way; the arm does not.
        self.assertEqual(self.owner.held_channels, {6, *POKER})
        self.assertEqual(self.execute_pose('release_poses').status, 'pulses_reached')
        self.assertEqual(self.owner.held_channels, {6, *POKER})
        self.assertEqual({ch: self.port.pose[ch] for ch in POKER}, dict(POKER_DRIVE))
        # The arm comes out later from where it is; a person's held look is not dropped.
        self.assertEqual(self.owner.execute(self.request('pose', pose='look_right'), Cancellation()).status,
                         'pulses_reached')
        self.owner.begin_arm_return()
        self.assertTrue({0} <= self.owner.held_channels)
        self.assertEqual(self.owner.return_arm().status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in POKER}, dict(POKER_UP))
        self.assertEqual(self.owner.kept_arms, frozenset())
        self.assertIn(0, self.owner.held_channels)

    def test_a_kept_arm_coming_out_later_leaves_the_arms_idle_is_moving(self):
        # October 15 (24279950): the throttle came out 7 s after a drive and the walk also
        # sent the hero elbow home while an idle phrase held it at 6845. The return waited
        # 25 s for 6720, timed out, and every servo move was refused until restart.
        self.parked()
        self.owner.begin_arm_return()
        self.assertEqual(self.owner.return_arm(keep={'throttle'}).status, 'pulses_reached')
        moved = self.owner.execute(self.request('pose', pose={4: 6845}, hold_pose=False), Cancellation())
        self.assertEqual(moved.status, 'pulses_reached')               # Idle life moves the elbow.
        self.owner.begin_arm_return()
        talk = self.owner.execute(self.request('talking'), Cancellation())
        self.assertEqual(talk.status, 'pulses_reached')                # Not ServoBusy while the throttle walks.
        elbow = self.port.pose[4]
        poker = {ch: self.port.pose[ch] for ch in (6, *POKER)}
        receipt = self.owner.return_arm()
        self.assertEqual(receipt.status, 'pulses_reached')
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, dict(REST))
        self.assertEqual(self.port.pose[4], elbow)                     # The elbow is left where talking put it,
        self.assertEqual({ch: self.port.pose[ch] for ch in (6, *POKER)}, poker)   # and the poker arm too.
        self.assertEqual(self.owner.kept_arms, frozenset())
        self.assertFalse(self.owner.held_channels & set(ARM_RETURN))

    def test_an_arm_that_never_arrives_is_held_and_the_rest_carries_on(self):
        self.parked()
        original_write = self.port.write
        def stuck_elbow(packet):
            if packet[0] == 0x84 and packet[1] == 4:
                packet = bytes((0x84, 4, 6845 & 127, 6845 >> 7))   # The elbow stops short of 6720.
            return original_write(packet)
        self.port.write = stuck_elbow
        self.owner.begin_arm_return()
        receipt = self.owner.return_arm()
        self.assertEqual((receipt.status, receipt.reason), ('failed', 'ServoArrivalTimeout:ch4@6845->6720'))
        self.assertEqual(self.owner.state, 'active')
        self.assertFalse(self.owner._arm_returning)
        self.assertFalse(self.owner.held_channels & set(ARM_RETURN))   # His arms are his again.
        self.port.write = original_write
        self.assertEqual(self.execute('talking').status, 'pulses_reached')
        self.assertEqual(self.execute('base_park').status, 'pulses_reached')   # The next drive can park.

    def test_a_stalled_awake_move_holds_without_refusing_later_moves(self):
        self.owner.start()
        self.execute()
        self.port.stall = True
        result = self.owner.execute(self.request('pose', pose='look_left', hold_pose=False), Cancellation())
        self.assertEqual(result.status, 'failed')
        self.assertTrue(result.reason.startswith('ServoArrivalTimeout:ch'), result.reason)
        self.assertEqual(self.owner.state, 'active')
        self.port.stall = False
        self.assertEqual(self.owner.execute(self.request('pose', pose='look_right', hold_pose=False),
                                            Cancellation()).status, 'pulses_reached')

    def test_a_stalled_microphone_recentre_does_not_refuse_later_moves(self):
        self.owner.start()
        self.execute()
        self.assertEqual(self.owner.execute(self.request('pose', pose={7: 7000}, hold_pose=False),
                                            Cancellation()).status, 'pulses_reached')
        self.clock.wait(2.)
        self.port.stall = True                         # The array arm never gets back to centre.
        self.owner.refresh_microphone_hold()
        self.assertEqual(self.owner.state, 'active')
        self.assertEqual(self.owner.microphone_hold, (0., 0.))
        self.port.stall = False
        self.assertEqual(self.execute('talking').status, 'pulses_reached')

    def test_the_next_drive_takes_a_kept_arm(self):
        self.parked()
        self.owner.begin_arm_return()
        self.owner.return_arm(keep={'throttle', 'poker'})
        self.assertEqual(self.owner.kept_arms, {'throttle', 'poker'})
        self.assertEqual(self.execute('base_park').status, 'pulses_reached')
        self.assertEqual(self.owner.kept_arms, frozenset())

    def execute_pose(self, name):
        return self.owner.execute(self.request('pose', pose=name), Cancellation())


class HoldArmsTests(unittest.TestCase):
    """October 3 (eef0a6ef): "leave that hand there until I say you can move it" was answered
    by moving the hand to another pose."""
    setUp, request, execute = ServoTests.setUp, ServoTests.request, ServoTests.execute

    def test_hold_arms_moves_nothing_and_has_no_time_limit(self):
        self.owner.start()
        self.execute()
        self.assertEqual(self.owner.execute(self.request('pose', pose='throttle_down'), Cancellation()).status, 'pulses_reached')
        before, sent = dict(self.port.pose), len(self.port.targets)
        held = self.owner.execute(self.request('pose', pose='hold_arms'), Cancellation())
        self.assertEqual(held.status, 'pulses_reached')
        self.assertEqual((self.port.pose, len(self.port.targets)), (before, sent))     # Not one servo command.
        self.assertTrue({4, 5, 6, *THROTTLE} <= self.owner.held_channels)
        self.clock.wait(600.)                                                           # Ten minutes later...
        self.assertTrue(self.owner.human_hold_active())                                 # ...it still stands,
        self.assertFalse(self.owner.yield_stale_hold())                                 # and his own beats cannot clear it.
        for kind in ('talking', 'idle'):
            self.execute(kind)
            self.assertEqual({ch: self.port.pose[ch] for ch in (4, 5, 6, *THROTTLE)},
                             {ch: before[ch] for ch in (4, 5, 6, *THROTTLE)})
        nudge = self.owner.execute(self.request('pose', pose={4: 7000}, hold_pose=False), Cancellation())
        self.assertEqual((nudge.status, nudge.reason), ('rejected', 'AgencyCannotOverrideHeldPose'))

    def test_only_being_told_releases_it_and_an_ordinary_pose_still_yields(self):
        self.owner.start()
        self.execute()
        self.owner.execute(self.request('pose', pose='hold_arms'), Cancellation())
        self.owner.execute(self.request('pose', pose='release_poses'), Cancellation())
        self.assertFalse(self.owner.hold_until_released)
        self.assertFalse(self.owner.held_channels)
        self.owner.execute(self.request('pose', pose='throttle_down'), Cancellation())   # An ordinary requested pose...
        self.clock.wait(91.)
        self.assertFalse(self.owner.human_hold_active())                                 # ...still gives way after 90 s.


class TalkingOutputTests(unittest.TestCase):
    def test_actual_audio_start_and_finally_stop_own_talking_motion(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                calls, ended = [], []
                class TTS:
                    def synthesize(self, text, cancel):
                        self_test.assertEqual(calls, [])
                        yield b'pcm'
                class Motion:
                    def start(self, turn, cancel): calls.append('start')
                    def stop(self, finished=False): calls.append('stop'); ended.append(finished)
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
                self.assertEqual(ended, [not fail])   # Only a line played to its end lets the arms run on.
                self.assertEqual(delivery.status, 'failed' if fail else 'completed')
                self.assertTrue(owner._motion_lock.acquire(blocking=False))
                owner._motion_lock.release()
