"""A depth sensor that goes dark is said aloud, and so is its return (owner, November 15).

"Speak out loud when an 8x8 array fails using pre-canned text, no LLM required, only if the failure
lasts a few seconds or more, and then have him speak if it recovers." A simulated drive controller
stops and resumes its depth frames through the production serial link, drive base, session and output
owner. Synthetic voice and clock: this does not prove the robot's sensors, firmware or audio."""
from types import SimpleNamespace
import time
import unittest

from r3x import depth_watch
from r3x.depth_watch import BACK_AFTER, FAIL_AFTER, LINES, REPEAT_AFTER, DepthSensorWatch, Notice
from r3x.output import OutputOwner
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from tests.test_drive import base_setup, settle
from tests.test_voice_core import Trace

FRESH = depth_watch.FRESH_SECONDS


class WatchRuleTests(unittest.TestCase):
    def setUp(self):
        self.watch = DepthSensorWatch(choose=lambda lines: lines[0])

    def look(self, now, front='streaming', rear='streaming'):
        """What is due at `now`, given when each sensor sent its last frame ('streaming': just now;
        None: never)."""
        age = lambda last: None if last is None else (.1 if last == 'streaming' else now - last)
        return self.watch.due(now, {'front': age(front), 'rear': age(rear)})

    def lose_front(self, last=10.):
        """The front sensor's last frame came at `last`; he says so. When he said it."""
        self.look(last)
        said_at = last + FRESH + FAIL_AFTER
        self.assertIsNone(self.look(said_at - .1, front=last))
        notice = self.look(said_at, front=last)
        self.assertEqual(notice, Notice('failed', ('front',)))
        self.watch.said(notice, said_at)
        return said_at

    def test_a_short_drop_out_passes_in_silence(self):
        self.assertIsNone(self.look(0.))
        self.assertIsNone(self.look(2., front=1.))
        self.assertIsNone(self.look(1. + FRESH + FAIL_AFTER - .1, front=1.))
        self.assertIsNone(self.look(6.))                        # Back before he said anything:
        self.assertIsNone(self.look(6. + BACK_AFTER + 1.))      # ...so no "it's back" either.

    def test_lost_for_a_few_seconds_then_back(self):
        said_at = self.lose_front()
        self.assertIsNone(self.look(said_at + 60., front=10.))             # Said once, not again.
        self.assertIsNone(self.look(said_at + 61.))                        # Just returned: wait.
        back = self.look(said_at + 61. + BACK_AFTER)
        self.assertEqual(back, Notice('recovered', ('front',)))
        self.watch.said(back, said_at + 63.)
        self.assertIsNone(self.look(said_at + 70.))

    def test_a_notice_stays_due_until_it_was_heard(self):
        self.look(0.)
        first = self.look(1. + FRESH + FAIL_AFTER, front=1.)
        self.assertEqual(first, Notice('failed', ('front',)))
        self.assertEqual(self.look(20., front=1.), first)       # He was mid-sentence: still owed.

    def test_a_line_that_was_tried_and_not_heard_waits_before_it_is_tried_again(self):
        # November 20: the line failed in playback each time and was restarted every five seconds.
        self.look(0.)
        first = self.look(1. + FRESH + FAIL_AFTER, front=1.)
        self.watch.tried(10.)
        self.assertIsNone(self.look(15., front=1.))
        self.assertIsNone(self.look(10. + REPEAT_AFTER - 1., front=1.))
        self.assertEqual(self.look(10. + REPEAT_AFTER + 1., front=1.), first)       # Still owed.

    def test_a_loss_first_noticed_late_counts_from_the_last_frame(self):
        self.look(0.)
        # A long reply kept him from looking for half a minute: the sensor has been dark all that time.
        self.assertEqual(self.look(30., front=1.), Notice('failed', ('front',)))

    def test_both_sensors_dropping_seconds_apart_get_one_line(self):
        self.look(0.)
        self.assertIsNone(self.look(2., front=1.))
        # The rear follows three seconds later (November 14: six seconds after the front).
        self.assertIsNone(self.look(1. + FRESH + FAIL_AFTER, front=1., rear=4.))
        both = self.look(4. + FRESH + FAIL_AFTER, front=1., rear=4.)
        self.assertEqual(both, Notice('failed', ('front', 'rear')))
        self.watch.said(both, 9.)
        # The front returns first: the rear is still out, so the front's return is said by itself.
        self.assertIsNone(self.look(20., rear=4.))
        self.assertEqual(self.look(20. + BACK_AFTER, rear=4.), Notice('recovered', ('front',)))

    def test_a_rear_sensor_never_seen_is_not_called_lost_but_a_front_one_is(self):
        self.assertIsNone(self.look(0., front=None, rear=None))
        self.assertEqual(self.look(FAIL_AFTER, front=None, rear=None), Notice('failed', ('front',)))

    def test_a_silent_controller_says_nothing_about_the_sensors(self):
        self.look(0.)
        self.assertIsNone(self.watch.due(3., None))             # Unplugged or rebooting: unknown.
        self.assertIsNone(self.watch.due(9., None))
        # It reports again, its sensors still starting up: the count begins now, not at their last frame.
        self.assertIsNone(self.look(10., front=0., rear=0.))
        self.assertIsNone(self.look(10. + FAIL_AFTER - .1, front=0., rear=0.))
        self.assertEqual(self.look(10. + FAIL_AFTER, front=0., rear=0.).sensors, ('front', 'rear'))

    def test_a_sensor_that_keeps_dropping_is_not_announced_every_time(self):
        said_at = self.lose_front()
        self.look(said_at + 5.)
        back = self.look(said_at + 5. + BACK_AFTER)
        self.watch.said(back, said_at + 7.)
        again = said_at + 8.
        self.assertIsNone(self.look(again + FRESH + FAIL_AFTER, front=again))   # Within half a minute: quiet.
        self.assertEqual(self.look(said_at + REPEAT_AFTER, front=again), Notice('failed', ('front',)))

    def test_every_notice_has_lines_and_all_are_saved_ahead(self):
        for kind in ('failed', 'recovered'):
            for sensors in (('front',), ('rear',), ('front', 'rear')):
                self.assertGreaterEqual(len(LINES[(kind, sensors)]), 2)
        self.assertEqual(len(set(depth_watch.canned_lines())), len(depth_watch.canned_lines()))


class Brain:
    """A reply model that must not be asked."""
    calls = 0

    def reply(self, snapshot, cancel):
        self.calls += 1
        yield 'No.'


class SpokenNoticeTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.fake, self.drive, _ = base_setup(self)
        self.trace = Trace()
        self.drive.trace = self.trace
        self.drive.start()
        self.addCleanup(self.drive.close)
        self.brain, self.saved = Brain(), []
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.trace)
        self.session = Session('session', self.brain, output, self.trace)
        self.addCleanup(self.session.close)
        def canned(text, cancel):
            self.saved.append(text)
            return b'\x01\x00' * 400
        self.session.canned = canned
        self.runtime = VoiceRuntime(SimpleNamespace(), SimpleNamespace(), None, self.session, self.trace, drive=self.drive)
        self.runtime.depth_watch = DepthSensorWatch(choose=lambda lines: lines[0])

    def pass_time(self, seconds):
        self.clock.wait(seconds)
        settle()
        return self.runtime.sensor_tick()

    def spoken(self):
        return [r['text'] for r in self.trace.rows if r['event'] == 'phrase_completed']

    def notices(self):
        return [(r['kind'], r['sensors'], r['heard']) for r in self.trace.rows if r['event'] == 'hardware_notice']

    def test_front_sensor_lost_and_back_is_said_in_saved_lines_without_the_model(self):
        self.assertFalse(self.pass_time(1.))                    # Streaming: nothing to say.
        self.fake.matrix = False                                # Its reset button is pressed in.
        self.assertFalse(self.pass_time(1.5))
        self.assertFalse(self.pass_time(FAIL_AFTER - 2.))       # Not yet "a few seconds".
        self.assertTrue(self.pass_time(2.5))
        failed = LINES[('failed', ('front',))][0]
        self.assertEqual(self.spoken(), [failed])
        self.assertEqual(self.saved, [failed])                  # From the saved audio, not new speech.
        self.assertFalse(self.pass_time(20.))                   # Said once.
        self.fake.matrix = True                                 # Let go: the controller set it up again.
        self.assertFalse(self.pass_time(.3))
        self.assertTrue(self.pass_time(BACK_AFTER + .5))
        self.assertEqual(self.spoken(), [failed, LINES[('recovered', ('front',))][0]])
        self.assertEqual(self.notices(), [('depth_sensor_failed', ['front'], True),
                                          ('depth_sensor_recovered', ['front'], True)])
        self.assertEqual(self.brain.calls, 0)
        # The conversation knows what he said.
        self.assertEqual([m['content'] for m in self.session.history if m['role'] == 'assistant'], self.spoken())

    def test_both_sensors_lost_together_get_one_line(self):
        self.fake.rear_grid = [2000] * 64
        self.assertFalse(self.pass_time(1.))
        self.fake.matrix, self.fake.rear_grid = False, None
        self.assertTrue(self.pass_time(FAIL_AFTER + 1.5))
        self.assertEqual(self.spoken(), [LINES[('failed', ('front', 'rear'))][0]])

    def test_a_notice_cut_off_in_playback_is_not_said_again_at_once(self):
        self.fake.matrix = False
        self.pass_time(1.)
        real = self.session.output.run
        def failing(*args, **kwargs):
            raise RuntimeError('PlaybackDrainTimeout')
        self.session.output.run = failing
        self.assertFalse(self.pass_time(6.))
        self.assertEqual([heard for _, _, heard in self.notices()], [False])
        for _ in range(5):
            self.assertFalse(self.pass_time(2.))
        self.assertEqual(len(self.notices()), 1)                 # Not restarted every tick.
        self.session.output.run = real
        self.assertTrue(self.pass_time(30.))
        self.assertEqual([heard for _, _, heard in self.notices()], [False, True])

    def test_he_waits_for_a_free_moment(self):
        self.assertFalse(self.pass_time(1.))                    # Streaming.
        self.fake.matrix = False
        self.runtime.asr_busy.set()                             # Someone is speaking to him.
        self.assertFalse(self.pass_time(FAIL_AFTER + 2.))
        self.assertTrue(self.session._processing.acquire(blocking=False))   # A turn is being answered.
        self.runtime.asr_busy.clear()
        self.assertFalse(self.pass_time(1.))
        self.session._processing.release()
        self.assertEqual(self.spoken(), [])
        self.assertTrue(self.pass_time(1.))                     # Free: the notice he still owed.
        self.assertEqual(len(self.spoken()), 1)

    def test_a_controller_that_stops_reporting_is_not_called_a_sensor_failure(self):
        self.assertFalse(self.pass_time(1.))
        self.fake.read = lambda size: (time.sleep(.0005), b'')[1]   # Unplugged: no telemetry, no frames.
        self.assertFalse(self.pass_time(FAIL_AFTER + 5.))
        self.assertEqual(self.spoken(), [])

    def test_the_controllers_own_log_lines_join_the_session_log(self):
        with self.fake.lock:
            self.fake.emit(type='log', t=12075, lvl='warn', msg='tof_matrix front: not answering - setting it up again')
        settle()
        rows = [(r['level'], r['text']) for r in self.trace.rows if r['event'] == 'drive_firmware_log']
        self.assertEqual(rows, [('warn', 'tof_matrix front: not answering - setting it up again')])
