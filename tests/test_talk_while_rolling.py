"""Owner, October 24: he talks while he rolls. Visor and mouth lights only, head and arms still,
the whir quiet while he talks, short lines only, and a stop still heard over his voice.

Synthetic speech, a simulated base and synthetic servo pulses; nothing here is live audio.
"""
import threading
import time
import unittest
from queue import Queue

from r3x.contracts import Cancellation, Playback, Transcript
from r3x.drive import DriveAction, DrivePlan, DriveReceipt, DriveStep
from r3x.output import Delivery, OutputOwner, ROLLING_WORDS, put_phrase
from r3x.robot_servos import RobotServos
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servos import BASE_HELD, ServoOwner, ServoRequest
from r3x.session import Session, stop_heard_over
from tests import test_drive_session
from tests.test_drive_session import drive_call, step
from tests.test_memory_core import MemoryFixture
from tests.test_servos import Clock as ServoClock, PulsePort
from tests.test_voice_core import Brain, Trace


LONG_LINE = ('Make way, make way, the finest pilot in the whole galaxy is coming through this '
             'very living room right now.')


class Speech:
    """Synthetic TTS and sink that record what the wheels were doing as each line was said."""
    def __init__(self, drive, fake, hold=None, hold_seconds=3.):
        self.drive, self.fake = drive, fake
        self.hold, self.hold_seconds = hold, hold_seconds
        self.said = []          # (text, wheels commanded yet, wheels of a plan commanded, drive still running)
        self.held = {}          # line -> whether the wheels started while it played
        self.current = None

    def synthesize(self, text, cancel):
        self.current = text
        wheels = any(c['cmd'] in {'move', 'turn', 'drive'} for c in self.fake.commands)
        self.said.append((text, wheels, self.drive.rolled is not None, self.drive.moving.is_set()))
        yield b'\0\0' * 32

    def play(self, chunks, cancel, on_start, visualize=True):
        list(chunks)
        began = time.monotonic()
        on_start(began)
        if self.current == self.hold:
            # A line that takes a while to say: do the wheels start before it ends?
            deadline = time.monotonic() + self.hold_seconds
            while self.drive.rolled is None and time.monotonic() < deadline:
                time.sleep(.005)
            self.held[self.current] = self.drive.rolled is not None
        return Playback(32, began, time.monotonic(), 0)

    def cancel(self):
        pass


class RollingSpeechTests(MemoryFixture):
    setup_drive = test_drive_session.DriveSessionTests.setup_drive
    turn = test_drive_session.DriveSessionTests.turn

    def drive(self, call, **speech):
        self.setup_drive(call)
        self.speech = Speech(self.drive, self.fake, **speech)
        self.output.tts = self.output.sink = self.speech
        delivery = self.turn('Come over here.')
        receipt = next(r for r in self.state.trace.rows if r['event'] == 'drive_receipt')
        return delivery, receipt

    def test_the_wheels_start_while_his_opening_line_is_still_playing(self):
        delivery, receipt = self.drive(drive_call(step('move', meters=1.), text='On my way.'), hold='On my way.')
        self.assertEqual(receipt['status'], 'completed')
        self.assertTrue(self.speech.held['On my way.'])   # Before: the line ended, then the wheels.
        self.assertEqual(delivery.status, 'completed')

    def test_a_rolling_line_starts_once_the_wheels_do(self):
        call = drive_call(step('move', meters=3.), text='On my way.')
        call.arguments['say_while'] = 'Make way, important droid coming through.'
        delivery, receipt = self.drive(call)
        self.assertEqual(receipt['status'], 'completed')
        said = {text: (wheels, rolled, moving) for text, wheels, rolled, moving in self.speech.said}
        self.assertEqual(said['Make way, important droid coming through.'][:2], (True, True))
        rows = [r['event'] + ':' + str(r.get('text', '')) for r in self.state.trace.rows
                if r['event'] in {'speech_generated', 'drive_receipt'}]
        self.assertLess(rows.index('speech_generated:Make way, important droid coming through.'),
                        rows.index('drive_receipt:'))   # While he rolls, not after he arrives.
        self.assertIn('Make way, important droid coming through.', ' '.join(delivery.completed_text))

    def test_a_rolling_line_is_never_said_when_the_wheels_do_not_move(self):
        call = drive_call(step('move', meters=1.), text='On my way.')
        call.arguments['say_while'] = 'Make way, important droid coming through.'
        self.setup_drive(call)
        self.drive.hold = lambda: 'sentry_mode'   # Every plan is refused before any wheel command.
        self.speech = Speech(self.drive, self.fake)
        self.output.tts = self.output.sink = self.speech
        self.turn('Come over here.')
        self.assertNotIn('Make way, important droid coming through.', [s[0] for s in self.speech.said])

    def test_a_line_too_long_for_rolling_is_said_before_he_moves(self):
        self.assertGreater(len(LONG_LINE.split()), ROLLING_WORDS)
        call = drive_call(step('move', meters=1.), text=None)
        call.arguments['say_while'] = LONG_LINE
        delivery, receipt = self.drive(call)
        self.assertEqual(receipt['status'], 'completed')
        said = {text: wheels for text, wheels, rolled, moving in self.speech.said}
        self.assertFalse(said[LONG_LINE])   # Said in full before any wheel command.

    def test_a_long_opening_line_keeps_the_wheels_waiting_for_it(self):
        delivery, receipt = self.drive(drive_call(step('move', meters=1.), text=LONG_LINE),
                                       hold=LONG_LINE, hold_seconds=.3)
        self.assertEqual(receipt['status'], 'completed')
        self.assertFalse(self.speech.held[LONG_LINE])


class RolledPlanTests(unittest.TestCase):
    def test_only_a_plan_whose_wheels_moved_counts_as_rolled(self):
        from tests.test_drive import base_setup
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(lambda: drive.state == 'closed' or drive.close())
        moved = DrivePlan('first', (DriveStep('move', meters=.3),), 'requested', 'test', {}, clock() + 60)
        self.assertEqual(drive.execute(moved, Cancellation()).status, 'completed')
        self.assertEqual(drive.rolled, 'first')
        drive.hold = lambda: 'sentry_mode'   # Refused before any wheel command: its rolling line is never said.
        refused = DrivePlan('second', (DriveStep('move', meters=.3),), 'requested', 'test', {}, clock() + 60)
        self.assertEqual(drive.execute(refused, Cancellation()).status, 'refused')
        self.assertNotEqual(drive.rolled, 'second')


class Motion:
    """Talking motion that records how each line moved him."""
    def __init__(self):
        self.calls = []
    def start(self, turn_id, cancel, visor_only=False):
        self.calls.append('visor' if visor_only else 'full')
    def stop(self, finished=False):
        self.calls.append('stop')


class Sounds:
    def __init__(self):
        self.calls = []
    def hush(self): self.calls.append('hush')
    def resume(self): self.calls.append('resume')


class Drive:
    """A drive owner whose plan runs until released."""
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.sounds = Sounds()
    def execute(self, plan, cancel):
        self.entered.set()
        self.release.wait(3.)
        return DriveReceipt(plan.request_id, 'completed')


def plan():
    return DriveAction(DrivePlan('p', (DriveStep('move', meters=1.),), 'requested', 'test', {}, time.monotonic() + 60))


class OutputBesideDriveTests(unittest.TestCase):
    def setUp(self):
        self.trace, self.motion, self.drive = Trace(), Motion(), Drive()
        self.sink_hook = lambda: None
        case = self
        class Sink:
            def play(self, chunks, cancel, on_start, visualize=True):
                list(chunks)
                on_start(1.)
                case.sink_hook()
                return Playback(32, 1., 2., 0)
            def cancel(self): pass
        self.output = OutputOwner(SyntheticTTS(), Sink(), self.trace, speech_motion=self.motion, drive=self.drive)
        self.phrases, self.cancel, self.delivery = Queue(), Cancellation(), Delivery()
        self.worker = threading.Thread(target=self.output.run, args=(self.phrases, self.cancel, 'turn', self.delivery))

    def finish(self):
        self.phrases.put(None)
        self.worker.join(3.)
        self.assertFalse(self.worker.is_alive())
        self.assertFalse(self.output._motion_lock.locked())   # Nothing kept the motion resource.
        self.assertIsNone(self.output._talk)

    def test_a_drive_starting_mid_line_takes_the_head_and_arms_and_leaves_the_visor_talking(self):
        action = plan()
        def mid_line():
            self.output.start_drive(action, self.phrases, self.cancel, 'turn')
            self.assertTrue(self.drive.entered.wait(2.))   # The drive runs while the line plays.
        self.sink_hook = mid_line
        self.worker.start()
        put_phrase(self.phrases, 'On my way.', self.cancel)
        deadline = time.monotonic() + 2
        while self.motion.calls.count('stop') < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.motion.calls, ['full', 'stop', 'visor', 'stop'])
        self.drive.release.set()
        self.assertTrue(action.done.wait(2.))
        self.assertEqual(action.receipt.status, 'completed')
        self.finish()

    def test_a_line_said_while_rolling_moves_only_the_visor_and_hushes_the_whir(self):
        action = plan()
        self.worker.start()
        self.output.start_drive(action, self.phrases, self.cancel, 'turn')
        self.assertTrue(self.drive.entered.wait(2.))
        put_phrase(self.phrases, 'Make way!', self.cancel)
        deadline = time.monotonic() + 2
        while 'stop' not in self.motion.calls and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.motion.calls, ['visor', 'stop'])
        self.assertEqual(self.drive.sounds.calls[:2], ['hush', 'resume'])
        self.drive.release.set()
        self.assertTrue(action.done.wait(2.))
        put_phrase(self.phrases, 'Here I am.', self.cancel)   # Standing still again: full talking motion.
        deadline = time.monotonic() + 2
        while self.motion.calls.count('stop') < 2 and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(self.motion.calls[2:], ['full', 'stop'])
        self.finish()

    def test_more_than_a_short_line_ahead_is_said_before_the_wheels_start(self):
        action = plan()
        started_during = []
        self.sink_hook = lambda: started_during.append(self.drive.entered.wait(.2))
        put_phrase(self.phrases, LONG_LINE, self.cancel)
        self.output.start_drive(action, self.phrases, self.cancel, 'turn')
        self.worker.start()
        self.assertTrue(self.drive.entered.wait(2.))
        self.assertEqual(started_during, [False])
        self.drive.release.set()
        self.assertTrue(action.done.wait(2.))
        self.finish()


class VisorBesideDriveTests(unittest.TestCase):
    """The drive stance holds the head and arms; the visor alone talks, beside the drive's own moves."""
    def setUp(self):
        self.port, self.clock = PulsePort(), ServoClock()
        self.owner = ServoOwner(lambda: self.port, medium='simulation', clock=self.clock, wait=self.clock.wait)
        self.addCleanup(self.owner.close)
        # The visor's own pauses pass in real time, as on the robot: a busy thread advancing the
        # shared fake clock would expire the other requests' deadlines.
        self.owner._visor_wait = lambda seconds: time.sleep(.001)
        self.owner.start()
        self.assertEqual(self.execute('startup').status, 'pulses_reached')
        self.by_thread = []
        write = self.port.write
        def tagged(packet):
            self.by_thread.append((threading.current_thread().name, bytes(packet)))
            return write(packet)
        self.port.write = tagged

    def execute(self, kind):
        return self.owner.execute(ServoRequest(kind, self.owner.version, kind, self.clock() + 120.), Cancellation())

    def talk(self):
        token, result = Cancellation(), {}
        worker = threading.Thread(target=lambda: result.update(r=self.owner.talk_visor(token)), name='visor')
        worker.start()
        deadline = time.monotonic() + 2
        while not any(name == 'visor' and p[:2] == bytes((0x84, 3)) for name, p in self.by_thread) and time.monotonic() < deadline:
            time.sleep(.005)
        return token, worker, result

    def test_the_visor_talks_while_the_arms_tuck_and_nothing_else_moves_with_it(self):
        token, worker, result = self.talk()
        park = self.execute('base_park')
        self.assertEqual(park.status, 'pulses_reached')     # Not ServoBusy: the visor runs beside it.
        self.assertTrue(BASE_HELD <= self.owner.held_channels)
        token.event.set()
        worker.join(2.)
        self.assertEqual(result['r'].status, 'cancelled')
        visor = [p for name, p in self.by_thread if name == 'visor' and p[0] == 0x84]
        self.assertTrue(visor)
        self.assertEqual({p[1] for p in visor}, {3})           # Only the visor, never the head or an arm.
        self.assertFalse(any(p[0] == 0x84 and p[1] == 3 for name, p in self.by_thread if name != 'visor'))
        self.assertNotIn(3, self.owner.held_channels)

    def test_a_visor_a_person_posed_is_left_alone(self):
        self.owner.held_channels.add(3)
        receipt = self.owner.talk_visor(Cancellation())
        self.assertEqual((receipt.status, receipt.reason), ('rejected', 'VisorHeld'))
        self.assertIn(3, self.owner.held_channels)

    def test_a_stop_ends_it_and_shutdown_still_parks(self):
        token, worker, result = self.talk()
        self.owner.cancel()
        worker.join(2.)
        self.assertEqual(result['r'].status, 'cancelled')
        self.assertFalse(self.owner.visor_talking)
        self.assertEqual(self.execute('shutdown').status, 'pulses_reached')


class RobotStanceTests(unittest.TestCase):
    def test_the_arm_tuck_and_a_drive_look_leave_a_rolling_line_talking(self):
        port, clock = PulsePort(), ServoClock()
        robot = RobotServos('fake', open_port=lambda: port, clock=clock, wait=clock.wait)
        robot.owner.wait = clock.wait
        robot.owner._visor_wait = lambda seconds: time.sleep(.001)
        self.addCleanup(lambda: robot.closed or robot.close())
        robot.start()
        robot.talking.start('turn', Cancellation(), visor_only=True)
        self.assertEqual(robot.prepare_base_motion(Cancellation()).status, 'pulses_reached')
        self.assertIsNotNone(robot.talking._thread)
        self.assertTrue(robot.talking._thread.is_alive())
        from r3x.servo_poses import NECK_CENTER
        self.assertEqual(robot.drive_look({0: NECK_CENTER + 400}, Cancellation(), 'drive-look').status, 'pulses_reached')
        self.assertTrue(robot.talking._thread.is_alive())
        robot.talking.stop(finished=True)
        self.assertEqual(robot.talking.result.status, 'pulses_reached')
        self.assertNotIn(3, robot.owner.held_channels)


class WhirTests(unittest.TestCase):
    """The whir and his voice share one sink: it pauses for a line and comes back after."""
    def setUp(self):
        from r3x.motion_sounds import MotionSounds
        self.plays = []
        case = self
        class Sink:
            def play(self, chunks, cancel, on_start, visualize=True):
                case.plays.append(visualize)
                for _ in chunks:
                    if cancel.event.is_set():
                        return Playback(0, 0., 0., 0)
                    time.sleep(.002)
                return Playback(0, 0., 0., 0)
        self.sounds = MotionSounds(Sink())
        self.addCleanup(self.sounds.stop, True)

    def playing(self):
        thread = self.sounds._thread
        return thread is not None and thread.is_alive()

    def test_hush_stops_the_whir_and_resume_brings_it_back_while_the_wheels_run(self):
        self.sounds.start('move', 'requested')
        self.assertTrue(self.playing())
        self.sounds.hush()
        self.assertFalse(self.playing())
        self.sounds.start('turn', 'requested')   # A new wheel command while he talks: still quiet.
        self.assertFalse(self.playing())
        self.sounds.resume()
        self.assertTrue(self.playing())

    def test_wheels_that_stopped_while_he_talked_stay_quiet(self):
        self.sounds.start('move', 'requested')
        self.sounds.hush()
        self.sounds.stop()
        self.sounds.resume()
        self.assertFalse(self.playing())


class StopOverHisVoiceTests(unittest.TestCase):
    def test_his_own_words_never_stop_him(self):
        self.assertFalse(stop_heard_over('Whoa.', 'Whoa, look at that thing!'))
        self.assertFalse(stop_heard_over('Next stop.', 'Next stop, the kitchen!'))

    def test_a_person_stopping_him_over_his_line_is_heard(self):
        self.assertTrue(stop_heard_over('Make way stop', 'Make way, important droid coming through.'))
        self.assertTrue(stop_heard_over('Oh, stop!', 'Make way, important droid coming through.'))
        self.assertTrue(stop_heard_over("droid you're gonna hit the island", 'Make way, important droid coming through.'))
        self.assertFalse(stop_heard_over('Make way, important droid coming through.', 'Make way, important droid coming through.'))

    def test_without_his_voice_the_strict_rule_is_unchanged(self):
        self.assertTrue(stop_heard_over('Rex, stop moving.', ''))
        self.assertFalse(stop_heard_over('Stop by the store later.', ''))

    def run_capture(self, text, moving, own):
        """A whole capture taken while he spoke: the conversation rejects it as echo."""
        from unittest.mock import patch
        from r3x.capture import Frame
        trace = Trace()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
        session = Session('s', Brain(), output, trace)
        self.addCleanup(session.close)
        if own:
            output.lines.append([199., None, own])
        labels = [True]*60 + [False]*40   # Too long for the short-burst check.
        frames = [Frame(bytes(1024), 200+i*.032, 200+(i+1)*.032, echo_risk=True) for i in range(len(labels))]
        heard, halted = [], []
        class Mic:
            dropped = status_errors = 0
            def read(inner):
                if not frames:
                    time.sleep(.2); runtime.stop.set(); return None
                return frames.pop(0)
            def cancel(inner): pass
        class VAD:
            def reset(inner): pass
            def is_speech(inner, pcm): return labels.pop(0)
        class ASR:
            def transcribe(inner, capture, cancel):
                heard.append(capture); return Transcript(text, -.3, True)
        class Drive:
            def __init__(inner): inner.moving = threading.Event()
            def cancel(inner): halted.append(True); inner.moving.clear()
        drive = Drive()
        if moving:
            drive.moving.set()
        runtime = VoiceRuntime(Mic(), VAD(), ASR(), session, trace, drive=drive)
        with patch('sys.stdin.isatty', return_value=False):
            runtime.run()
        return trace.rows, heard, halted

    def test_stop_said_over_his_line_while_rolling_halts_the_wheels(self):
        rows, heard, halted = self.run_capture('Make way stop', True, 'Make way, important droid coming through.')
        peek = next(r for r in rows if r['event'] == 'stop_peek')
        self.assertEqual(peek['status'], 'halted')
        self.assertTrue(peek['over_own_speech'] and peek['echo_risk'])
        self.assertTrue(halted)
        self.assertTrue(any(r['event'] == 'capture_rejected' and r['reason'] == 'playback_echo_risk' for r in rows))

    def test_his_own_line_heard_back_does_not_stop_him(self):
        rows, heard, halted = self.run_capture('Whoa.', True, 'Whoa, look at that thing!')
        self.assertEqual(next(r for r in rows if r['event'] == 'stop_peek')['status'], 'not_a_stop')
        self.assertFalse(halted)

    def test_standing_still_his_own_speech_is_not_transcribed(self):
        rows, heard, halted = self.run_capture('Stop.', False, 'Look at that thing!')
        self.assertEqual(heard, [])
        self.assertFalse(any(r['event'] == 'stop_peek' for r in rows))


if __name__ == '__main__':
    unittest.main()
