"""November 15 (owner): "if we make him mad by insulting him his eyes could turn red. If he gets excited
or surprised the chest LEDs should transition to an excitement/surprise animation." The feeling an
expression carries shows on the lights too. A fake LED board, synthetic voice and a simulated gesture
adapter: these prove which commands go out and when, not what the reply model chooses or how any of
it looks on the robot."""
from pathlib import Path
import threading
import time
import unittest
from queue import Queue

from r3x.contracts import Cancellation
from r3x.leds import COLORS, FEELING_LIGHTS
from r3x.output import Delivery, OutputOwner
from r3x.performance import Gesture, Performance, SimulatedGesture
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.servo_poses import HEAD_EXPRESSIONS
from r3x.trace import Trace
from tests.test_feelings import HeldSink, Sway
from tests.test_leds import rig

FIRMWARE = Path(__file__).resolve().parents[1] / 'firmware' / 'djr3x_leds'


def eye(pattern):
    return 'EYE:' + ','.join(map(str, COLORS[pattern]))


# What the head is sent once a reply that showed a feeling is over: plain eyes and the plain mouth glow
# (the mouth's level stream left out, as in Stage.sent).
PLAIN_AGAIN = [eye('neutral'), 'SPEAK:neutral', 'SPEAK_STOP', 'ACTIVE']


class Stage:
    """An output owner with a fake LED board and a simulated body."""
    def __init__(self, sink=None):
        self.trace = Trace('feeling-lights', display=lambda row: None)
        self.led, self.ports, _ = rig()
        gestures = SimulatedGesture(self.trace.clock)
        gestures.start()
        self.output = OutputOwner(SyntheticTTS(), sink or SyntheticSink(), self.trace, gestures=gestures,
                                  speech_motion=Sway(), leds=self.led)
        self.cancel, self.delivery = Cancellation(), Delivery()
        for port in self.ports.values():
            port.commands.clear()            # Startup's own commands are not this reply's.

    def feeling(self, primitive, text='', timing='with_speech'):
        return Performance(text, Gesture('g-' + primitive, primitive, 'unfocused', 'normal', self.trace.clock() + 30),
                           timing, lambda: True, lambda: None)

    def say(self, *items):
        phrases = Queue()
        for item in (*items, None):
            phrases.put(item)
        self.delivery = Delivery()
        self.output.run(phrases, self.cancel, 'turn', self.delivery)
        return self.delivery

    def sent(self, zone):
        """Commands to a strip, without the mouth's level stream."""
        return [c for c in self.ports[zone].commands if not c.startswith('SPEAK_LEVEL')]


class FeelingLightTests(unittest.TestCase):
    def test_every_feeling_of_the_body_has_lights_and_the_board_knows_them(self):
        # A nod is plain agreement; a wink keeps the line's own colours and darkens one eye.
        self.assertEqual(set(HEAD_EXPRESSIONS) - set(FEELING_LIGHTS), {'nod', 'wink'})
        self.assertTrue(set(FEELING_LIGHTS) <= set(HEAD_EXPRESSIONS))
        self.assertTrue(set(FEELING_LIGHTS.values()) <= set(COLORS))
        self.assertEqual(FEELING_LIGHTS['angry'], 'angry')
        self.assertEqual(COLORS['angry'], (255, 0, 0))                           # Red eyes when he is mad.
        # Every light feeling but the plain one has a mouth colour and a chest pattern in the firmware.
        head, chest = (FIRMWARE / 'head.cpp').read_text(), (FIRMWARE / 'chest.cpp').read_text()
        for name in set(COLORS) - {'neutral'}:
            self.assertIn(f'strcmp(s, "{name}")', head.replace('   ', ' ').replace('  ', ' '), name)
            self.assertIn(f'strcmp(emotion, "{name}")', chest, name)

    def test_a_line_with_a_feeling_is_spoken_in_its_lights(self):
        for primitive, pattern in (('angry', 'angry'), ('surprised', 'surprised'), ('wiggle', 'excited'),
                                   ('deflated', 'sad'), ('proud', 'proud'), ('perplexed', 'curious')):
            with self.subTest(primitive=primitive):
                stage = Stage()
                delivery = stage.say(stage.feeling(primitive, 'Say that again.'))
                self.assertEqual(delivery.status, 'completed')
                self.assertEqual(stage.sent('head'), [eye(pattern), 'SPEAK:' + pattern, 'SPEAK_STOP', 'ACTIVE',
                                                      *PLAIN_AGAIN])   # The reply over, the head is plain again.
                self.assertEqual(stage.sent('chest'), ['SPEAK:' + pattern, 'SPEAK_STOP'])
                self.assertEqual([(row['pattern'], row['status']) for row in delivery.lights], [(pattern, 'commands_sent')])
                self.assertEqual([g['primitive'] for g in delivery.gestures], [primitive])

    def test_a_wink_darkens_one_eye_as_the_head_cocks_and_keeps_the_ordinary_lights(self):
        # Owner, November 21: a wink the model can choose: one eye dark for a moment with a slight head tilt.
        stage = Stage()
        delivery = stage.say(stage.feeling('wink', 'Just kidding.'))
        self.assertEqual(delivery.status, 'completed')
        self.assertEqual(stage.sent('head').count('WINK'), 1)                  # The board times the wink itself.
        self.assertNotIn('WINK', stage.sent('chest'))
        self.assertEqual(stage.sent('chest'), ['SPEAK:neutral', 'SPEAK_STOP'])  # No feeling's colours: an ordinary line.
        self.assertEqual([g['primitive'] for g in delivery.gestures], ['wink'])
        self.assertIn(('wink', 'commands_sent'), [(row['pattern'], row['status']) for row in delivery.lights])
        other = Stage()
        other.say(other.feeling('tilt', 'Oh?'))
        self.assertNotIn('WINK', other.sent('head'))                           # Only a wink winks.

    def test_a_dark_led_board_does_not_stop_the_winks_head_tilt(self):
        stage = Stage()
        stage.ports['head'].fail = True
        delivery = stage.say(stage.feeling('wink', 'Just kidding.'))
        self.assertEqual([g['primitive'] for g in delivery.gestures], ['wink'])
        self.assertEqual(delivery.gestures[0]['status'], 'simulated')          # The head still moved.
        self.assertIn(('wink', 'failed'), [(row['pattern'], row['status']) for row in delivery.lights])

    def test_a_plain_line_and_a_nod_keep_the_ordinary_speaking_lights(self):
        stage = Stage()
        stage.say('Fifty-six.')
        stage.say(stage.feeling('nod', 'Exactly.'))
        self.assertEqual(stage.sent('chest'), ['SPEAK:neutral', 'SPEAK_STOP'] * 2)
        self.assertNotIn(eye('angry'), stage.sent('head'))

    def test_a_feeling_arriving_beside_a_line_turns_its_lights_and_lasts_the_reply(self):
        sink = HeldSink()
        stage = Stage(sink)
        phrases = Queue()
        worker = threading.Thread(target=stage.output.run, args=(phrases, stage.cancel, 'turn', stage.delivery), daemon=True)
        worker.start()
        phrases.put('New wheels?')                                   # Its words streamed ahead: plain so far.
        self.assertTrue(sink.playing.wait(5))
        self.assertEqual(stage.sent('chest'), ['SPEAK:neutral'])
        stage.output.express(stage.feeling('surprised'), stage.cancel, 'turn', stage.delivery)
        deadline = time.monotonic() + 5
        while 'SPEAK:surprised' not in stage.sent('chest') and time.monotonic() < deadline:
            time.sleep(.005)
        self.assertEqual(stage.sent('chest'), ['SPEAK:neutral', 'SPEAK:surprised'])
        self.assertEqual(stage.sent('head')[-2:], [eye('surprised'), 'SPEAK:surprised'])
        sink.playing.clear(); sink.end.set()
        phrases.put('For me?')                                       # The rest of the reply keeps the feeling.
        phrases.put(None)
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(stage.sent('chest'), ['SPEAK:neutral', 'SPEAK:surprised', 'SPEAK_STOP',
                                               'SPEAK:surprised', 'SPEAK_STOP'])
        self.assertIn(('surprised', 'commands_sent'), [(row['pattern'], row['status']) for row in stage.delivery.lights])
        # The next reply is plain again.
        for port in stage.ports.values():
            port.commands.clear()
        stage.say('Anyway.')
        self.assertEqual(stage.sent('chest'), ['SPEAK:neutral', 'SPEAK_STOP'])

    def test_a_feeling_with_no_line_under_it_shows_on_eyes_glow_and_chest_without_moving_the_mouth(self):
        stage = Stage()
        delivery = stage.say(stage.feeling('deflated', 'Fine.', timing='after_speech'))
        head, chest = stage.ports['head'].commands, stage.sent('chest')
        self.assertEqual(chest, ['SPEAK:neutral', 'SPEAK_STOP', 'SPEAK:sad', 'SPEAK_STOP'])
        after = head[head.index('ACTIVE') + 1:]                       # Everything after the line's own lights ended.
        self.assertEqual(after, [eye('sad'), 'SPEAK:sad', 'SPEAK_LEVEL:0', 'SPEAK_STOP', 'ACTIVE',
                                 eye('neutral'), 'SPEAK:neutral', 'SPEAK_LEVEL:0', 'SPEAK_STOP', 'ACTIVE'])
        self.assertEqual([(row['pattern'], row['status']) for row in delivery.lights],
                         [('neutral', 'commands_sent'), ('sad', 'commands_sent')])
        # Chosen before any line, it reacts at once and the line that follows speaks in it.
        stage = Stage()
        stage.output.express(stage.feeling('laugh'), stage.cancel, 'turn', stage.delivery)
        for worker in stage.output._expressing:
            worker.join(5)
        self.assertEqual(stage.sent('chest'), ['SPEAK:happy', 'SPEAK_STOP'])
        stage.say('That is the funniest thing I have heard all week.')
        self.assertEqual(stage.sent('chest'), ['SPEAK:happy', 'SPEAK_STOP', 'SPEAK:happy', 'SPEAK_STOP'])

    def test_the_eyes_do_not_keep_a_feeling_once_the_reply_is_over(self):
        # Owner, November 15: after an angry line his eyes stayed red, refreshed every 3 s, until he
        # next spoke. The reply over, they and the mouth's glow go back to what a plain line leaves.
        for timing in ('with_speech', 'after_speech'):
            with self.subTest(timing=timing):
                stage = Stage()
                stage.say(stage.feeling('angry', 'Say that again.', timing=timing))
                self.assertEqual(stage.sent('head')[-4:], PLAIN_AGAIN)
                stage.ports['head'].commands.clear()
                for _ in range(20):                                  # A minute of quiet.
                    stage.led.clock.wait(3.1)
                    stage.led.tick()
                self.assertEqual(set(stage.sent('head')), {eye('neutral')})
        # A plain reply sends nothing more than its own line's lights.
        stage = Stage()
        stage.say('Fifty-six.')
        self.assertEqual(stage.sent('head'), [eye('neutral'), 'SPEAK:neutral', 'SPEAK_STOP', 'ACTIVE'])

    def test_sentry_red_stays_and_an_unknown_feeling_is_refused(self):
        stage = Stage()
        stage.led.guard(True)
        for port in stage.ports.values():
            port.commands.clear()
        stage.say(stage.feeling('laugh', 'Ha.'))
        self.assertEqual([c for c in stage.sent('chest') if c.startswith('SPEAK:')], ['SPEAK:angry'])   # Every line in red.
        self.assertEqual(stage.led.eye_color, COLORS['angry'])       # And the reply over, still red.
        self.assertNotIn(eye('neutral'), stage.sent('head'))
        self.assertIsNone(stage.led.react('happy'))
        self.assertFalse(stage.led.feel('happy'))
        from r3x.contracts import ProviderFailure
        stage.led.guard(False)
        with self.assertRaises(ProviderFailure):
            stage.led.react('furious')
        before = list(stage.ports['head'].commands)
        self.assertFalse(stage.led.feel('proud'))                     # No line is being spoken: nothing is sent.
        self.assertEqual(stage.ports['head'].commands, before)


if __name__ == '__main__':
    unittest.main()
