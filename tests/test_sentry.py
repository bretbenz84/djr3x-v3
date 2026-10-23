"""Sentry mode (owner request, October 22). Synthetic faces, voices, clocks, serial ports and HTTP only:
these prove the guard's rules and its path through the production owners, not camera, microphone or
model behaviour on the robot."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from uuid import uuid4

from r3x import sentry as rules
from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.output import OutputOwner, TextOutput
from r3x.push import Push, valid_topic
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import grounded_tools, validate
from r3x.sentry import Sentry, plan
from r3x.sentry_guard import SentryGuard
from r3x.sentry_recording import SentryRecorder
from r3x.session import Session
from r3x.speakers import SpeakerTracker
from tests.test_memory_core import A, B, NS, MemoryFixture, observation

WALL = 1_793_000_000.   # A fixed wall clock for plans.
KNOWN = {'p-bret': 'Bret'}.get


def args(countdown=None, minutes=None, until=None, audio=True):
    return {'countdown_seconds': countdown, 'duration_minutes': minutes, 'until': until, 'audio_alerts': audio}


def face(at, x=.45, identity=None, width=80, track='t1', resembles=None):
    return {'track_id': track, 'face_epoch': 0, 'box': [x, .3, .1, .15], 'at': at, 'identity': identity,
            'face_width_px': width, 'resembles': resembles}


def guarding(audio=True, requester=None, ends_at=None):
    s = Sentry(lambda: 0., lambda: WALL)
    s.begin(rules.SentryPlan(30, ends_at, audio, WALL), requester)
    s.arm(0.)
    return s


def kinds(decisions):
    return [d.kind for d in decisions]


class PlanTests(unittest.TestCase):
    def test_the_request_is_read_into_a_checked_plan(self):
        default = plan(args(), WALL)
        self.assertEqual((default.countdown_seconds, default.ends_at, default.audio_alerts), (30, None, True))
        self.assertEqual(rules.confirmation(default), 'Sentry mode until someone I know comes back.')
        nine = plan(args(countdown=60, minutes=540, audio=False), WALL)
        self.assertEqual(nine.ends_at, WALL + 60 + 540 * 60)
        self.assertEqual(rules.confirmation(nine), 'Sentry mode for 9 hours, silent.')
        from datetime import datetime
        evening = datetime(2026, 10, 22, 15, 0).timestamp()
        seven = plan(args(until='19:00'), evening)
        self.assertEqual(datetime.fromtimestamp(seven.ends_at), datetime(2026, 10, 22, 19, 0))
        self.assertEqual(rules.confirmation(seven), 'Sentry mode until 7 PM.')
        late = plan(args(until='7:30'), datetime(2026, 10, 22, 20, 0).timestamp())   # Already past: tomorrow's.
        self.assertEqual(datetime.fromtimestamp(late.ends_at), datetime(2026, 10, 23, 7, 30))
        self.assertEqual(late.until_text, 'until tomorrow at 7:30 AM')
        self.assertEqual(rules.duration_words(90), '1 hour and 30 minutes')

    def test_bad_requests_are_rejected_not_guessed(self):
        for bad, reason in ((args(minutes=60, until='19:00'), 'SentryEndGivenTwice'),
                            (args(until='7pm'), 'InvalidSentryUntil'), (args(until='25:00'), 'InvalidSentryUntil'),
                            (args(countdown=1), 'InvalidSentryCountdown'), (args(minutes=0), 'InvalidSentryDuration'),
                            ({**args(), 'audio_alerts': 'yes'}, 'InvalidSentryAudioAlerts')):
            with self.assertRaisesRegex(ValueError, reason):
                plan(bad, WALL)
        with self.assertRaisesRegex(ValueError, 'InvalidSentryNumber:countdown_seconds'):
            validate('sentry_mode', args(countdown=900))
        validate('sentry_mode', args(countdown=60, minutes=540, audio=False))

    def test_the_countdown_is_said_like_a_countdown(self):
        self.assertEqual(rules.countdown_calls(30), list(range(30, 0, -1)))
        self.assertEqual(rules.countdown_calls(60), [60, 50, 40, 30, 20, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1])
        self.assertEqual(rules.spoken_number(29), 'Twenty-nine.')
        self.assertEqual(rules.number_words(120), 'one hundred twenty')
        self.assertIn('Twenty-nine.', rules.canned_lines())
        self.assertIn(rules.ALERT, rules.canned_lines())


class GuardRuleTests(unittest.TestCase):
    def test_a_face_held_a_few_seconds_arrives_then_trips_unless_recognized(self):
        s = guarding()
        seen = []
        for i in range(16):
            seen.append((i * .5, kinds(s.faces([face(i * .5)], i * .5, 0., KNOWN))))
        arrived = next(t for t, k in seen if 'arrived' in k)
        tripped = next(t for t, k in seen if 'trip' in k)
        self.assertEqual((arrived, tripped), (3., 6.))
        # Looking like someone he knows buys recognition more time.
        s = guarding()
        trips = [i * .5 for i in range(30) if 'trip' in kinds(s.faces([face(i * .5, resembles='p-bret')], i * .5, 0., KNOWN))]
        self.assertEqual(trips[0], 10.)

    def test_brief_faces_and_a_moving_head_never_trip(self):
        s = guarding()
        for i in range(40):   # Two seconds in view, two out, over and over.
            at = i * .5
            rows = [face(at)] if i % 8 < 4 else []
            self.assertNotIn('arrived', kinds(s.faces(rows, at, 0., KNOWN)))
        s = guarding()
        for i in range(30):   # Unsettled view: nothing is judged as an arrival.
            self.assertFalse({'arrived', 'trip'} & set(kinds(s.faces([face(i * .5)], i * .5, None, KNOWN))))
        s = guarding()
        tiny = [kinds(s.faces([face(i * .5, width=12)], i * .5, 0., KNOWN)) for i in range(30)]
        self.assertFalse(any(tiny))

    def test_someone_known_stands_him_down_but_the_person_leaving_does_not(self):
        s = guarding()
        self.assertEqual([(d.kind, d.person_id, d.reason) for d in s.faces([face(1., identity='p-bret')], 1., 0., KNOWN)],
                         [('stand_down', 'p-bret', 'face')])
        s = guarding(requester='p-bret')
        self.assertEqual(kinds(s.faces([face(1., identity='p-bret')], 1., 0., KNOWN)), [])
        self.assertEqual(kinds(s.faces([], 31.5, 0., KNOWN)), [])     # The room has been empty 30 s...
        self.assertEqual(kinds(s.faces([face(40., identity='p-bret')], 40., 0., KNOWN)), ['stand_down'])   # ...so this is a return.
        # A face recognized as someone with no name is not someone he knows.
        s = guarding()
        self.assertNotIn('stand_down', kinds(s.faces([face(1., identity='p-nameless')], 1., 0., KNOWN)))

    def test_faces_that_never_move_during_the_survey_are_pictures(self):
        s = Sentry(lambda: 0., lambda: WALL)
        s.begin(rules.SentryPlan(30, None, True, WALL))
        found = [kinds(s.survey([face(i * .5, x=.2, track='poster'), face(i * .5, x=.3 + .05 * i, track='walker')],
                                i * .5, 0.)) for i in range(8)]
        self.assertEqual(sum(k.count('fixture') for k in found), 1)
        s.arm(10.)
        later = [kinds(s.faces([face(10. + i * .5, x=.2)], 10. + i * .5, 0., KNOWN)) for i in range(20)]
        self.assertFalse(any(later))   # The poster never arrives or trips.
        # Seen from another neck angle, it is still that picture (bearing, not position in the view).
        from r3x.drive_space import HALF_FOV
        shifted = .2 + 15. / (2 * HALF_FOV)
        self.assertEqual(kinds(s.faces([face(30., x=shifted)], 30., 15., KNOWN)), [])

    def test_only_full_sentences_trip_and_a_known_voice_stands_down(self):
        s = guarding()
        for text, trusted in (('Woof!', True), ('Oh.', True), ('Hey, is anybody home?', False), ('Okay okay', True)):
            self.assertIsNone(s.heard(text, trusted, None, 1., KNOWN))
        self.assertEqual(s.heard('Hey, is anybody home?', True, None, 1., KNOWN).kind, 'trip')
        self.assertEqual(s.heard('Hi', True, 'p-bret', 1., KNOWN).kind, 'stand_down')
        self.assertIsNone(guarding(requester='p-bret').heard('See you later, Rex.', True, 'p-bret', 1., KNOWN))

    def test_the_alarm_talk_lasts_two_minutes_then_he_watches_until_they_leave(self):
        s = guarding()
        event = s.trip('face', 10., 'e1')
        self.assertEqual(s.phase, 'alarm')
        self.assertFalse(s.jab_due(20.))           # Nothing said yet.
        event.last_said = 15.
        self.assertTrue(s.jab_due(45.))
        self.assertEqual(s.heard('You are a very silly robot.', True, None, 46., KNOWN).kind, 'answer')
        self.assertFalse(s.jab_due(60.))           # They answered: no jab over them.
        self.assertFalse(s.quiet_down(129.))
        self.assertTrue(s.quiet_down(130.))
        self.assertEqual(s.phase, 'watching')
        self.assertEqual(s.heard('I am still here, tin can.', True, None, 131., KNOWN).kind, 'ignored')
        self.assertIn('gone', kinds(s.faces([], 131. + rules.CLEAR_SECONDS, 0., KNOWN)))
        self.assertEqual(s.all_clear().event_id, 'e1')
        self.assertEqual(s.phase, 'guarding')

    def test_silent_mode_watches_without_talking(self):
        s = guarding(audio=False)
        s.trip('speech', 5., 'e1')
        self.assertEqual(s.phase, 'watching')
        self.assertFalse(s.jab_due(100.))

    def test_the_time_running_out(self):
        s = guarding(ends_at=WALL + 60)
        self.assertFalse(s.expired())
        s.wall_clock = lambda: WALL + 61
        self.assertTrue(s.expired())

    def test_call_off_words(self):
        for text in ('Stop!', 'twenty-eight cancel', 'Wait, never mind', 'Abort.'):
            self.assertTrue(rules.calls_off(text), text)
        for text in ('Twenty-eight.', 'Activating sentry mode in'):
            self.assertFalse(rules.calls_off(text), text)


class ToolTests(MemoryFixture):
    def test_offered_only_on_a_persons_request_when_sentry_can_run(self):
        self.begin('Activate sentry mode.')
        context = self.state.context()
        self.assertNotIn('sentry_mode', {d['name'] for d in grounded_tools(context)})
        self.assertIn('sentry_mode', {d['name'] for d in grounded_tools({**context, 'sentry_available': True})})
        self.assertNotIn('sentry_mode', {d['name'] for d in grounded_tools({'trigger': 'conversation_lull', 'sentry_available': True})})
        self.assertEqual(grounded_tools({**context, 'sentry': {'phase': 'alarm'}}), [])   # The guard's lines: words only.

    def test_the_state_owner_checks_the_request(self):
        self.begin('Activate sentry mode for nine hours, silent.')
        call = lambda a, context: self.state.execute(ToolCall(uuid4().hex, 'sentry_mode', a), self.store.revision,
                                                     Cancellation(), context=context)
        self.assertEqual(call(args(minutes=540, audio=False), {}).reason, 'SentryUnavailable')
        rejected = call(args(minutes=540, until='19:00'), {'sentry_available': True})
        self.assertEqual((rejected.outcome, rejected.reason), ('rejected', 'SentryEndGivenTwice'))
        accepted = call(args(minutes=540, audio=False), {'sentry_available': True})
        self.assertEqual(accepted.outcome, 'ok')
        self.assertEqual((accepted.data['countdown_seconds'], accepted.data['audio_alerts']), (30, False))
        self.assertAlmostEqual(accepted.data['ends_at'] - accepted.data['requested_at'], 30 + 540 * 60)
        self.state.sentry.begin(rules.SentryPlan(30, None, True, WALL))
        self.assertEqual(call(args(), {'sentry_available': True}).reason, 'SentryAlreadyActive')

    def test_an_accepted_request_hands_the_plan_to_the_guard_without_a_reply(self):
        class Brain:
            calls = 0
            def reply(self, snapshot, cancel):
                Brain.calls += 1
                self.context = snapshot.context
                yield ToolCall('c', 'sentry_mode', args(countdown=60, minutes=540, audio=False))
        brain, started = Brain(), []
        self.begin('Activate sentry mode silent with 60 second activation good for 9 hours.')
        session = Session('session', brain, TextOutput(self.state.trace), self.state.trace, self.state)
        self.addCleanup(session.close)
        session.sentry = SimpleNamespace(active=False, reply_context=lambda opportunity: None)
        session.on_sentry = lambda data, turn_id, requester: started.append((data, requester))
        result = session.process_turn(self.state.current[1], Transcript('Activate sentry mode.', 0, True), 0, observation())
        self.assertEqual((result.status, Brain.calls), ('suppressed', 1))   # The guard speaks; no second call.
        self.assertTrue(brain.context['sentry_available'])
        self.assertEqual((started[0][0]['countdown_seconds'], started[0][0]['audio_alerts']), (60, False))


class VoiceMatchTests(MemoryFixture):
    def test_a_clear_saved_voice_is_named_without_touching_session_tracks(self):
        bret = self.enroll('Bret', A)
        profiles = self.store.profiles(NS)
        tracker = SpeakerTracker(NS)
        self.assertEqual(tracker.match(observation(A), profiles), (bret, 'acoustic_match'))
        self.assertEqual(tracker.match(observation(B), profiles)[0], None)
        self.assertEqual(tracker.match(observation(A), profiles, trusted=False), (None, 'short_or_untrusted'))
        self.assertEqual(tracker.match(observation(A), profiles, echo=True), (None, 'echo_mixed_or_conflicting'))
        self.assertEqual(tracker.tracks, {})


class LightTests(unittest.TestCase):
    def test_guard_holds_red_keeps_speech_red_and_flashes_for_the_alarm(self):
        from tests.test_leds import rig
        led, ports, clock = rig()
        self.addCleanup(led.close)
        led.guard(True)
        self.assertEqual(ports['head'].commands[-2:], ['EYE:255,0,0', 'SPEAK:angry'])
        self.assertEqual(ports['chest'].commands[-1], 'SPEAK:angry')
        led.tick()
        self.assertIn('SPEAK_LEVEL:140', ports['head'].commands)   # The steady glow between lines.
        token = led.begin('happy', speaking=True)
        self.assertEqual(led.mouth, 'angry')
        before = len(ports['head'].commands)
        self.assertEqual(led.finish(token), 'commands_sent')
        self.assertNotIn('SPEAK_STOP', ports['head'].commands[before:])   # Back to the red glow, not idle.
        led.flash(1.)
        for _ in range(4):
            clock.wait(.3)
            led.tick()
        self.assertIn('EYE:0,0,0', ports['head'].commands)
        clock.wait(1.)
        led.tick()
        self.assertEqual([c for c in ports['head'].commands if c.startswith('EYE:')][-1], 'EYE:255,0,0')   # Eyes back on.
        led.guard(False)
        self.assertEqual(ports['head'].commands[-2:], ['ACTIVE', 'EYE:255,200,0'])
        self.assertEqual(ports['chest'].commands[-1], 'SPEAK_STOP')


class BodyTests(unittest.TestCase):
    def robot(self):
        from r3x.robot_servos import RobotServos
        from tests.test_servos import Clock, PulsePort
        clock, port = Clock(), PulsePort()
        robot = RobotServos('fixture', open_port=lambda: port, clock=clock, wait=clock.wait)
        robot.owner.wait = clock.wait
        robot.start()
        self.addCleanup(robot.close)
        return robot, port

    def test_the_stance_holds_still_the_neck_sweeps_and_release_lets_go(self):
        from r3x.servo_poses import ARM_HOLD, HEAD, NAMED_POSES, SENTRY_STANCE
        robot, port = self.robot()
        self.assertEqual(robot.sentry_stance(Cancellation()).status, 'pulses_reached')
        self.assertEqual({ch: port.pose[ch] for ch in SENTRY_STANCE}, dict(SENTRY_STANCE))
        self.assertTrue(set(ARM_HOLD) | set(HEAD) <= robot.owner.held_channels)
        self.assertTrue(robot.owner.hold_until_released)
        self.assertEqual(robot.sentry_look(4309, Cancellation(), 3.).status, 'pulses_reached')
        self.assertEqual(port.pose[0], 4309)
        self.assertEqual(robot.sentry_arm(True, Cancellation()).status, 'pulses_reached')
        self.assertEqual({ch: port.pose[ch] for ch, _ in NAMED_POSES['throttle_offer']}, dict(NAMED_POSES['throttle_offer']))
        self.assertEqual(robot.sentry_release(Cancellation()).status, 'pulses_reached')
        self.assertFalse(robot.owner.held_channels)
        self.assertFalse(robot.owner.hold_until_released)

    def test_sentry_moves_are_head_only_and_inside_the_limits(self):
        from r3x.servo_poses import UNPOWERED_REST, sentry_head, sentry_lanes
        with self.assertRaisesRegex(ValueError, 'SentryMovesHeadOnly'):
            sentry_lanes({8: 4200}, dict(UNPOWERED_REST), 1.)
        self.assertEqual(sentry_head(99999)[0], 8960)   # Clamped to the neck's travel.


class WheelTests(unittest.TestCase):
    def test_every_plan_and_the_back_off_reflex_are_refused_while_he_guards(self):
        from tests.test_drive import base_setup, plan as drive_plan, settle
        from r3x.drive import DriveStep
        clock, fake, drive, world = base_setup(self)
        drive.start()
        self.addCleanup(drive.close)
        settle()
        drive.hold = lambda: 'sentry_mode'
        self.assertEqual(drive.availability(), (False, 'sentry_mode'))
        receipt = drive.execute(drive_plan(DriveStep('turn', degrees=90), clock=clock), Cancellation())
        self.assertEqual((receipt.status, receipt.reason), ('refused', 'sentry_mode'))
        self.assertIsNone(drive.tick_reflex())


class RecorderTests(unittest.TestCase):
    def test_the_last_ten_seconds_start_a_recording_that_is_joined_and_kept(self):
        from r3x.vision import Frame
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve() / 'recordings'
        joined = []
        def join(folder, frames, offset):
            joined.append((len(frames), offset))
            (folder / 'video.mov').write_bytes(b'mov')
        counter = iter(range(10 ** 6))
        frame = lambda: Frame(f'f{next(counter)}', 'cam', 's', time.monotonic(), 2, 2, bytes(12))
        recorder = SentryRecorder(root, frame, encode=lambda f, *a: b'jpeg', join=join, free=lambda p: 10 ** 12)
        recorder.start()
        recorder.audio(b'\0\0' * 512, time.monotonic())
        time.sleep(.5)
        folder = recorder.begin('face')
        recorder.audio(b'\0\0' * 512, time.monotonic())
        time.sleep(.5)
        recorder.save_photo(recorder.photo())
        summary = recorder.stop({'heard': ['Hello?']})
        self.assertGreaterEqual(summary['frames'], 4)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['event.json', 'photo.jpg', 'video.mov'])
        self.assertEqual(joined[0][0], summary['frames'])
        # A nearly full disk records nothing rather than failing the guard.
        full = SentryRecorder(root, frame, encode=lambda f, *a: b'jpeg', join=join, free=lambda p: 1)
        self.assertIsNone(full.begin('face'))


class PushTests(unittest.TestCase):
    def test_the_alert_carries_the_photo_and_failures_are_results(self):
        sent = []
        push = Push('rex-sentry-1234abcd', post=lambda url, headers, body: sent.append((url, headers, body)) or 200,
                    wait=lambda s: None)
        result = push.send('Rex: sentry mode triggered', 'Someone Rex does not recognize is in view.', b'jpeg')
        self.assertEqual((result['outcome'], result['attempts']), ('sent', 1))
        url, headers, body = sent[0]
        self.assertEqual((url, body, headers['Filename']), ('https://ntfy.sh/rex-sentry-1234abcd', b'jpeg', 'rex.jpg'))
        self.assertEqual(headers['Message'], 'Someone Rex does not recognize is in view.')
        def offline(*a):
            raise OSError('down')
        failed = Push('rex-sentry-1234abcd', post=offline, wait=lambda s: None).send('t', 'm')
        self.assertEqual((failed['outcome'], failed['attempts'], failed['error']), ('failed', 3, 'OSError'))
        refused = Push('rex-sentry-1234abcd', post=lambda *a: 403, wait=lambda s: None).send('t', 'm')
        self.assertEqual((refused['outcome'], refused['attempts']), ('failed', 1))
        self.assertFalse(valid_topic('short'))
        with self.assertRaises(ValueError):
            Push('has spaces in it')

    def test_the_topic_is_an_optional_secret(self):
        from r3x.settings import Secrets
        self.assertNotIn('ntfy_topic', Secrets().readiness())


class FakeCamera:
    def __init__(self): self.rows = []
    def latest(self, now): return tuple(self.rows)


class FakeRecorder:
    def __init__(self): self.calls, self.on = [], None
    def start(self): self.calls.append('start')
    def begin(self, reason): self.calls.append(('begin', reason)); self.on = reason; return 'folder'
    def audio(self, pcm, at): pass
    def photo(self): return b'jpeg'
    def save_photo(self, data): self.calls.append('photo')
    def recording(self): return {'folder': 'folder'} if self.on else None
    def finish(self, details=None): self.calls.append(('finish', details)); self.on = None
    def stop(self, details=None): self.calls.append(('stop', details)); self.on = None


class FakePush:
    def __init__(self): self.sent, self.event = [], threading.Event()
    def send(self, title, message, photo=None, **options):
        self.sent.append((title, message, photo, options))
        self.event.set()


class ScriptedBrain:
    def __init__(self, text='INTRUDER! Nice cardigan, burglar. Smile, you are on camera.'):
        self.text, self.calls, self.snapshots = text, 0, []
    def reply(self, snapshot, cancel):
        self.calls += 1
        self.snapshots.append(snapshot)
        yield self.text


class GuardTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.trace = self.state.trace
        self.brain = ScriptedBrain()
        self.output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.trace)
        self.session = Session('session', self.brain, self.output, self.trace, self.state)
        self.addCleanup(self.session.close)
        self.camera, self.recorder, self.push, self.shutdowns = FakeCamera(), FakeRecorder(), FakePush(), []
        self.guard = SentryGuard(self.session, self.output, self.trace, camera=self.camera,
                                 canned=lambda text, cancel: b'\0\0' * 32, recorder=self.recorder, push=self.push,
                                 shutdown=self.shutdowns.append)
        self.addCleanup(self.guard.close)

    def on_guard(self, audio=True, ends_at=None, requester=None):
        self.state.sentry.begin(rules.SentryPlan(30, ends_at, audio, WALL), requester)
        self.state.sentry.arm(self.trace.at)

    def spoken(self):
        return [r['text'] for r in self.trace.rows if r['event'] == 'phrase_completed']

    def watch(self, rows, seconds, step=.5):
        for _ in range(int(seconds / step)):
            self.trace.at += step
            self.camera.rows = [dict(r, at=self.trace.at) for r in rows]
            self.guard.tick(self.trace.at)

    def capture(self, echo=False):
        self.at += 10
        return Capture(uuid4().hex, 'session', bytes(64000), self.at, self.at + 2, self.at + 2, echo_risk=echo)

    def test_activation_counts_down_states_the_plan_then_guards_without_a_paid_call(self):
        data = vars(plan(args(countdown=3, minutes=60), WALL))
        self.guard.start(data, 'turn', None)
        deadline = time.monotonic() + 5
        while self.state.sentry.phase == 'countdown' and time.monotonic() < deadline:
            self.trace.at += .25
            time.sleep(.01)
        self.guard._countdown[0].join(2)
        self.assertEqual(self.state.sentry.phase, 'guarding')
        self.assertEqual(self.spoken(), ['Sentry mode for 1 hour.', rules.OPENING, 'Three.', 'Two.', 'One.', rules.ACTIVE])
        self.assertEqual(self.recorder.calls, ['start'])   # Buffering only: nothing is saved yet.
        # On guard: noise, short words and an intruder's "shut down" cost nothing and change nothing.
        for text, trusted in (('Woof!', True), ('Shut down.', True), ('Hey is anyone home', False)):
            self.assertTrue(self.guard.hear(self.capture(), Transcript(text, -.01, trusted), None))
        self.watch([], 30)
        self.assertEqual((self.brain.calls, self.state.sentry.phase, self.shutdowns), (0, 'guarding', []))
        self.assertEqual(self.guard.holding_wheels(), 'sentry_mode')

    def test_a_call_off_during_the_countdown_cancels_it(self):
        self.guard.start(vars(plan(args(countdown=20), WALL)), 'turn', None)
        while not any(r == rules.OPENING for r in self.spoken()):
            self.trace.at += .25
            time.sleep(.01)
        # His own numbers are heard too (echo); only the call-off word counts.
        self.assertTrue(self.guard.hear(self.capture(echo=True), Transcript('Nineteen.', -.01, True), None))
        self.assertEqual(self.state.sentry.phase, 'countdown')
        self.assertTrue(self.guard.hear(self.capture(echo=True), Transcript('Eighteen, stop!', -.01, True), None))
        self.assertEqual(self.state.sentry.phase, 'off')
        self.assertEqual(self.spoken()[-1], rules.CANCELLED)
        self.assertEqual(self.brain.calls, 0)

    def test_an_unknown_face_trips_the_alarm_records_roasts_and_alerts(self):
        self.on_guard()
        self.watch([face(0.)], 6.5)
        self.assertEqual(self.state.sentry.phase, 'alarm')
        self.assertIn(('begin', 'face'), self.recorder.calls)
        self.watch([face(0.)], .5)          # The alarm cry, then the roast through the conversation owner.
        self.assertEqual(self.brain.calls, 1)
        self.assertIn(rules.ALERT, self.spoken())
        snapshot = self.brain.snapshots[-1]
        self.assertEqual(snapshot.context['sentry']['intrusion'], 'face')
        self.assertEqual(grounded_tools(snapshot.context), [])
        self.assertTrue(any('SENTRY MODE ALARM' in str(m.get('content')) for m in snapshot.messages))
        self.watch([face(0.)], 10.)
        self.assertTrue(self.push.event.wait(2))
        title, message, photo, _ = self.push.sent[0]
        self.assertEqual((title, photo), ('Rex: sentry mode triggered', b'jpeg'))
        self.assertIn('does not recognize is in view', message)
        self.assertIn('photo', self.recorder.calls)
        self.watch([face(0.)], 30.)          # Nobody answers: one more jab, about every 30 s.
        self.assertEqual(self.brain.calls, 2)
        self.watch([face(0.)], 90.)          # Two minutes of talk, then silent watching.
        self.assertEqual(self.state.sentry.phase, 'watching')
        calls = self.brain.calls
        self.watch([], 61.)                  # They left: back on guard, the recording finishes.
        self.assertEqual((self.state.sentry.phase, self.brain.calls), ('guarding', calls))
        time.sleep(.05)
        self.assertTrue(any(c[0] == 'finish' for c in self.recorder.calls if isinstance(c, tuple)))

    def test_a_known_face_stands_him_down_and_a_false_alarm_sends_no_alert(self):
        bret = self.introduce('Bret')
        self.on_guard()
        self.watch([face(0.)], 6.5)          # Too far to recognize: the alarm goes off...
        self.watch([face(0., identity=bret, width=120)], 1.)   # ...then he sees who it is.
        self.assertEqual(self.state.sentry.phase, 'off')
        notes = [m['content'] for m in self.brain.snapshots[-1].messages if m.get('role') == 'developer']
        self.assertTrue(any('SENTRY MODE IS OVER' in n and 'Oh Bret, thank god' in n for n in notes))
        self.assertEqual(self.brain.snapshots[-1].context.get('sentry'), {'phase': 'off', 'stood_down': True})
        self.assertFalse(self.push.event.wait(.2))   # Inside the ten seconds: no alert for a false alarm.
        self.assertTrue(any(c[0] == 'stop' for c in self.recorder.calls if isinstance(c, tuple)))
        self.assertIsNone(self.guard.holding_wheels())

    def test_a_known_voice_stands_him_down_and_their_words_are_answered(self):
        bret = self.enroll('Bret', A)
        self.on_guard()
        handled = self.guard.hear(self.capture(), Transcript("Hey Rex, it's me, I'm home.", -.01, True),
                                  SimpleNamespace(voice=observation(A)))
        self.assertFalse(handled)   # The conversation owner answers it as an ordinary turn.
        self.assertEqual(self.state.sentry.phase, 'off')
        self.assertTrue(any('recognized their voice' in str(m.get('content')) for m in self.session.history_messages()))
        # A stranger's voice saying the same is a sentence that trips the alarm.
        self.on_guard()
        self.assertTrue(self.guard.hear(self.capture(), Transcript("Hey Rex, it's me, I'm home.", -.01, True),
                                        SimpleNamespace(voice=observation(B))))
        self.assertEqual(self.state.sentry.phase, 'alarm')

    def test_someone_he_knows_can_tell_him_to_shut_down(self):
        self.enroll('Bret', A)
        self.on_guard()
        self.assertTrue(self.guard.hear(self.capture(), Transcript('Shut down.', -.01, True),
                                        SimpleNamespace(voice=observation(A))))
        self.assertEqual((self.state.sentry.phase, self.shutdowns, self.brain.calls), ('off', ['voice'], 0))

    def test_without_a_fresh_picture_the_roast_does_not_describe_them(self):
        self.on_guard()
        self.watch([face(0.)], 7.)           # FakeCamera has no frames for a sharp look.
        notes = [str(m.get('content')) for m in self.brain.snapshots[-1].messages if m.get('role') == 'developer']
        self.assertTrue(any(rules.NO_PICTURE.strip() in n for n in notes))
        self.assertEqual(self.brain.snapshots[-1].images, ())

    def test_silent_mode_records_and_alerts_without_a_word(self):
        self.on_guard(audio=False)
        self.watch([face(0.)], 18.)
        self.assertEqual((self.state.sentry.phase, self.brain.calls, self.spoken()), ('watching', 0, []))
        self.assertTrue(self.push.event.wait(2))

    def test_when_the_time_runs_out_he_shuts_down(self):
        self.on_guard(ends_at=WALL + 60)
        self.state.sentry.wall_clock = lambda: WALL + 61
        self.guard.tick(self.trace.at)
        self.assertEqual((self.state.sentry.phase, self.shutdowns), ('off', ['sentry_time_up']))

    def test_while_on_guard_the_runtime_hands_him_every_transcript(self):
        from r3x.runtime import VoiceRuntime
        self.on_guard()
        class ASR:
            def transcribe(self, *a): return Transcript('Shut down.', -.01, True)
        runtime = VoiceRuntime(None, None, ASR(), self.session, self.trace)
        runtime.sentry = self.guard
        runtime.enqueue(runtime.captures, (self.capture(), 0), 'captures')
        worker = threading.Thread(target=runtime._asr_loop)
        worker.start()
        try:
            item = runtime.turns.get(timeout=1)
        finally:
            runtime.stop.set()
            worker.join(1)
        self.assertFalse(any(r['event'] == 'shutdown_requested' for r in self.trace.rows))
        self.assertTrue(self.guard.hear(item[0], item[1], item[3]))   # Two words from a stranger: ignored.
        self.assertEqual((self.state.sentry.phase, self.brain.calls), ('guarding', 0))

    def test_news_searches_pause_while_he_guards(self):
        from r3x.observations import PublicObservations
        from r3x.settings import Settings
        searched = []
        source = PublicObservations(Settings(news_feed_urls=(), weather_enabled=False), search=object(), cache=object())
        source.refresh_searches = lambda: searched.append(True)
        source.paused = True
        source.refresh()
        source.paused = False
        source.refresh()
        self.assertEqual(searched, [True])


if __name__ == '__main__':
    unittest.main()
