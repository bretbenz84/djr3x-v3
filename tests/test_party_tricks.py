"""Christmas mode, the fortune teller, the toast and going to check on something (owner, November 26). Fake
serial ports, a fake clock, a fake phone service and scripted model calls only: nothing here moves the robot,
reaches ntfy or ElevenLabs, and whether the reply model picks these tools is a paid probe's job, not this file's.
How Santa's voice sounds, how the raised glass looks with a cup in hand and the trip itself are unchecked."""
import base64
import unittest
from uuid import uuid4

from r3x.contracts import Cancellation, Capture, ToolCall, Transcript
from r3x.leds import CHRISTMAS_EYES, CHRISTMAS_SWAP_SECONDS
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import TOOLS, as_existing_tool, grounded_tools
from r3x.servo_poses import LIMITS, NAMED_POSES, THROTTLE, pose_lanes, validate_pose
from r3x.session import PHOTO_FAILED_LINE, Session
from r3x.voice_modes import MODE_SECONDS, VOICES, VoiceModes, directive
from tests.test_dance import DanceRobot, sent
from tests.test_leds import rig


class ChristmasTests(unittest.TestCase):
    def test_santas_voice_lasts_until_turned_off_and_another_mode_ends_it(self):
        self.assertEqual(VOICES['christmas'], 'ZncHSOpbSyuBVFfZSUAG')
        modes = VoiceModes()
        modes.switch('christmas', True, 0.)
        self.assertTrue(modes.on('christmas', MODE_SECONDS * 30))                 # Hours later, still Santa.
        self.assertEqual(modes.context(5000.), {'christmas': {'until': 'turned off'}})
        self.assertIn('until someone turns it off', directive(modes.context(5000.)))
        self.assertIn('Santa', directive(modes.context(5000.)))
        modes.switch('pirate', True, 6000.)
        self.assertEqual(modes.active(6001.), ('pirate',))                        # One mode at a time.
        modes.switch('christmas', True, 7000.)
        modes.switch('christmas', False, 7001.)
        self.assertEqual(modes.active(7002.), ())

    def test_the_eyes_take_turns_red_and_green_and_come_back_after(self):
        led, strips, clock = rig()
        self.addCleanup(led.close)
        on = [True]
        led.christmas = lambda: on[0]
        eyes = lambda: [c for c in strips['head'].commands if c.startswith('EYE:')]
        start = len(eyes())
        for _ in range(4):
            led.tick(); clock.wait(CHRISTMAS_SWAP_SECONDS)
        shown = eyes()[start:]
        red, green = ('EYE:' + ','.join(map(str, c)) for c in CHRISTMAS_EYES)
        self.assertEqual(shown[:4], [red, green, red, green])
        led.guarding = True                                                         # Sentry mode's red wins.
        led.tick()
        led.guarding, on[0] = False, False
        led.tick()
        self.assertEqual(eyes()[-1], 'EYE:' + ','.join(map(str, led.eye_color)))


class FortuneAndToastToolTests(unittest.TestCase):
    def names(self, **context):
        base = {'event_id': 'e', 'source_turn_id': 't', 'led_enabled': True, 'led_patterns': ['curious', 'happy'],
                'robot_pose_enabled': True}
        return {d['name'] for d in grounded_tools({**base, **context})}

    def test_both_answer_a_persons_request_only_and_need_their_lights_or_arms(self):
        self.assertTrue({'tell_fortune', 'give_toast'} <= self.names())
        self.assertFalse({'tell_fortune', 'give_toast'} & self.names(trigger='conversation_lull'))
        self.assertNotIn('tell_fortune', self.names(led_enabled=False))
        self.assertNotIn('give_toast', self.names(robot_pose_enabled=False))

    def test_the_fortune_is_purple_for_the_whole_line_and_the_toast_ends_on_a_raised_glass(self):
        fortune = as_existing_tool(ToolCall('f', 'tell_fortune', {'text': 'The spirits stir.'}))
        self.assertEqual((fortune.name, fortune.arguments), ('animate_lights', {
            'pattern': 'curious', 'timing': 'during_speech', 'duration': 'normal', 'text': 'The spirits stir.'}))
        toast = as_existing_tool(ToolCall('t', 'give_toast', {'text': 'To Bret. Cheers!'}))
        self.assertEqual((toast.name, toast.arguments), ('set_robot_pose', {
            'say_before': 'To Bret. Cheers!', 'poses': ['raise_glass'], 'look_after': None, 'text': None}))


class Scripted:
    def __init__(self, call):
        self.call = call

    def reply(self, snapshot, cancel):
        yield self.call


class RaiseGlassTests(DanceRobot):
    def test_the_glass_goes_up_as_for_a_high_five_and_the_arm_comes_back_with_the_fingers_untouched(self):
        lanes = pose_lanes('raise_glass', dict(self.robot.owner.commanded))
        for beat in lanes[0]:
            validate_pose({**self.robot.owner.commanded, **dict(beat.targets)})
        before = dict(self.port.pose)
        since = len(self.port.packets)
        receipt = self.robot.execute_pose('raise_glass', Cancellation(), 'g')
        self.assertEqual(receipt.status, 'pulses_reached', receipt.reason)
        shoulder = [v for ch, v in sent(self.port, since) if ch == 8]
        self.assertIn(dict(NAMED_POSES['throttle_high_five'])[8], shoulder)       # Up...
        self.assertEqual({ch: self.port.pose[ch] for ch in THROTTLE}, {ch: before[ch] for ch in THROTTLE})   # ...and back.
        self.assertNotIn(15, {ch for ch, _ in sent(self.port, since)})            # A held cup stays held.
        self.assertFalse(self.robot.owner.held_channels - self.robot.owner.kept_channels)

    def test_asked_for_a_toast_he_says_it_then_raises_the_glass(self):
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace, robot=self.robot)
        self.state.trace.clock = self.clock
        toast = 'To the cook, who burned nothing important this year. Cheers!'
        session = Session('s', Scripted(ToolCall('t', 'give_toast', {'text': toast})), output, self.state.trace, self.state)
        self.addCleanup(session.close)
        since = len(self.port.packets)
        result = session.process_turn(Capture(uuid4().hex, 's', bytes(1024), 98., 99., 99.6),
                                      Transcript('Rex, give us a toast!', -.01, True), session.revision)
        self.assertEqual(result.completed_text, [toast])
        self.assertIn(dict(NAMED_POSES['throttle_high_five'])[8], [v for ch, v in sent(self.port, since) if ch == 8])


class Push:
    def __init__(self, outcome='sent'):
        self.outcome, self.sent = outcome, []

    def send(self, title, message, photo=None, **options):
        self.sent.append((title, message, photo, options))
        return {'outcome': self.outcome, 'status': 200 if self.outcome == 'sent' else None}


class Image:
    data_url = 'data:image/jpeg;base64,' + base64.b64encode(b'\xff\xd8jpeg').decode()


class SendPhotoTests(unittest.TestCase):
    def test_offered_only_right_after_a_persons_look_with_a_phone_to_send_to(self):
        names = lambda **c: {d['name'] for d in grounded_tools({'event_id': 'e', 'source_turn_id': 't', **c})}
        look = {'inspection_description_allowed': True, 'inspection': {'request_id': 'r'}, 'photo_push': True}
        self.assertIn('send_photo', names(**look))
        self.assertNotIn('send_photo', names(**{**look, 'photo_push': False}))
        self.assertNotIn('send_photo', names(**{**look, 'agency_step': {'kind': 'look'}}))
        self.assertNotIn('send_photo', names(photo_push=True))                    # No look, no picture.
        self.assertIn('send_photo', {d['name'] for d in TOOLS})

    def test_the_picture_goes_with_his_caption_and_he_speaks_only_once_it_has(self):
        session = Session('s', None, OutputOwner(SyntheticTTS(), SyntheticSink(), None), Rows())
        session.photo_push = Push()
        call = ToolCall('p', 'send_photo', {'caption': 'Max is on the couch. Again.', 'text': 'Sent you the evidence.'})
        self.assertEqual(session._send_photo(call, (Image(),), 't'), 'Sent you the evidence.')
        title, caption, photo, _ = session.photo_push.sent[0]
        self.assertEqual((caption, photo), ('Max is on the couch. Again.', b'\xff\xd8jpeg'))
        session.photo_push = Push('failed')
        self.assertEqual(session._send_photo(call, (Image(),), 't'), PHOTO_FAILED_LINE)
        session.photo_push = None
        self.assertEqual(session._send_photo(call, (Image(),), 't'), PHOTO_FAILED_LINE)
        session.close()


class Rows:
    clock = staticmethod(lambda: 0.)
    def __init__(self): self.rows = []
    def emit(self, event, **data): self.rows.append({'event': event, **data})
