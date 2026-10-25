"""Pride mode and AAVE mode (ported from V2 October 25): the reply model switches them, the state owner
keeps ten minutes, and the voice, the request, the lights and pride's body follow. Synthetic providers,
pulse ports and serial ports only: these prove the contracts, not how the camp or AAVE lines sound,
how the owner's chosen ElevenLabs voices sound, or the flourish and rainbow on the robot."""
import json
import random
import tempfile
import unittest
from pathlib import Path

from r3x.adapters.hosted import ElevenLabsTTS, OpenAIBrain
from r3x.contracts import Cancellation, Snapshot, ToolCall
from r3x.output import OutputOwner
from r3x.physical_attention import PhysicalGesture
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import grounded_tools
from r3x.servo_poses import (FLAIR_THROTTLE_WRIST, HEAD_PAIR, LIMITS, THROTTLE, THROTTLE_RELAXED, VISOR_UP,
                             flourish_lanes, head_pose, talking_lanes, throttle_clear, validate_pose)
from r3x.session import Session
from r3x.settings import Secrets
from r3x.voice_modes import MODE_SECONDS, VoiceModes, directive
from tests.test_adapters import Client, Response
from tests.test_context_diet import send
from tests.test_leds import rig
from tests.test_memory_core import MemoryFixture
from tests.test_physical_attention import robot_fixture
from tests import test_attention


def switch(mode, on=True, text=None, turn=None):
    return lambda snapshot: ToolCall('mode', 'voice_mode', {'mode': mode, 'switch': 'on' if on else 'off',
        'source_turn_id': turn or snapshot.context['source_turn_id'], 'text': text})


class Scripted:
    """Answers each request with the next scripted reply (a string, or a function of the snapshot)."""
    def __init__(self, *replies):
        self.replies, self.snapshots = list(replies), []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        yield reply(snapshot) if callable(reply) else reply


class ModeStateTests(MemoryFixture):
    def switch(self, mode, on=True, turn=None):
        return self.call('voice_mode', {'mode': mode, 'switch': 'on' if on else 'off',
                                        'source_turn_id': turn or self.state.current[0], 'text': None})

    def test_asked_again_starts_the_ten_minutes_over_and_off_ends_it(self):
        self.begin('Rex, are you gay?')
        result = self.switch('pride')
        self.assertEqual((result.outcome, result.data['minutes'], result.data['flourish']), ('ok', 10, True))
        self.assertEqual(self.state.context()['voice_mode'], {'pride': {'minutes_left': 10}})
        self.state.trace.at += MODE_SECONDS - 60
        self.begin('Wait, are you actually gay?')
        again = self.switch('pride')
        self.assertFalse(again.data['flourish'])                 # The pose is for coming on, not every renewal.
        self.state.trace.at += MODE_SECONDS - 1
        self.assertTrue(self.state.voice_modes.on('pride', self.state.trace.clock()))
        self.begin('Stand down pride mode.')
        self.assertEqual(self.switch('pride', on=False).outcome, 'ok')
        self.assertNotIn('voice_mode', self.state.context())
        self.assertEqual(self.store.claims(include_candidates=True), [])   # A mode is never a fact.

    def test_ten_minutes_later_the_mode_is_gone(self):
        self.begin("What's up my homie?")
        self.switch('aave')
        self.state.trace.at += MODE_SECONDS
        self.assertNotIn('voice_mode', self.state.context())

    def test_only_a_clearly_heard_request_this_turn_switches_it(self):
        old = self.begin('Are you gay?')
        self.begin('Never mind.')
        self.assertEqual(self.switch('pride', turn=old).reason, 'SourceTurnMismatch')
        self.begin('Wassup homie.', trusted=False)
        self.assertEqual(self.switch('aave').reason, 'UntrustedSource')
        self.assertFalse(self.state.voice_modes.active(self.state.trace.clock()))

    def test_sentry_mode_clears_both_and_refuses_them_while_he_guards(self):
        self.begin('Are you gay?'); self.switch('pride')
        self.begin("What's up my homie?"); self.switch('aave')
        self.begin('Activate sentry mode.')
        args = {'countdown_seconds': None, 'duration_minutes': 60, 'until': None, 'audio_alerts': True}
        from uuid import uuid4
        result = self.state.execute(ToolCall(uuid4().hex, 'sentry_mode', args), self.store.revision, Cancellation(),
                                    context={'sentry_available': True})
        self.assertEqual(result.outcome, 'ok', result)
        self.assertFalse(self.state.voice_modes.active(self.state.trace.clock()))
        self.state.sentry.phase = 'guarding'
        self.begin('Are you gay?')
        self.assertEqual(self.switch('pride').reason, 'SentryModeActive')

    def test_offered_for_a_persons_words_never_for_his_own_turns(self):
        names = lambda context: {d['name'] for d in grounded_tools(context)}
        self.assertIn('voice_mode', names({'source_turn_id': 't'}))
        for trigger in ('conversation_lull', 'visual_arrival', 'ambient'):
            self.assertNotIn('voice_mode', names({'trigger': trigger}))
        self.assertNotIn('voice_mode', names({'trigger': 'conversation_lull',
                                               'agency_step': {'kind': 'contribute'}}))


class ModeClockTests(unittest.TestCase):
    def test_the_newest_mode_is_last_and_both_can_be_on(self):
        modes = VoiceModes()
        modes.switch('aave', True, 0.)
        modes.switch('pride', True, 5.)
        self.assertEqual(modes.active(10.), ('aave', 'pride'))
        modes.switch('aave', True, 20.)
        self.assertEqual(modes.active(30.), ('pride', 'aave'))
        self.assertEqual(modes.active(5. + MODE_SECONDS), ('aave',))
        note = directive(modes.context(30.))
        self.assertIn('PRIDE MODE', note); self.assertIn('AAVE MODE', note); self.assertIn('Both are on', note)
        with self.assertRaises(ValueError):
            modes.switch('pirate', True, 0.)


class RequestTests(unittest.TestCase):
    def test_a_mode_rides_last_on_replies_and_tool_follow_ups_and_not_otherwise(self):
        payload, _ = send({'source_turn_id': 't', 'voice_mode': {'aave': {'minutes_left': 7}}})
        last = payload['input'][-1]
        self.assertEqual(last['role'], 'developer')
        self.assertIn('African American Vernacular English', last['content'])
        self.assertIn("don't do an impression", last['content'])          # V2's one guard: speak it, never mock it.
        self.assertIn('About 7 more minutes', last['content'])
        self.assertNotIn('PRIDE MODE', json.dumps(payload['input']))
        plain, _ = send({'source_turn_id': 't'})
        self.assertNotIn('AAVE MODE', json.dumps(plain['input']))
        brain = OpenAIBrain('fixture')
        brain.client = Client(Response([{'type': 'response.output_text.delta', 'delta': 'Yasss.'}, {'type': 'response.completed'}]))
        continuation = ({'type': 'function_call', 'call_id': 'c', 'name': 'weather', 'arguments': '{}'},
                        {'type': 'function_call_output', 'call_id': 'c', 'output': '{}'})
        list(brain.reply(Snapshot('s', 't', 0, (), context={'voice_mode': {'pride': {'minutes_left': 3}}},
                                  tools_enabled=True, continuation=continuation), Cancellation()))
        follow_up = brain.client.calls[0][1]['json']['input']
        self.assertIn('PRIDE MODE', follow_up[-1]['content'])

    def test_the_tool_is_in_the_fixed_tool_list(self):
        payload, _ = send({'source_turn_id': 't'})
        tool = next(t for t in payload['tools'] if t['name'] == 'voice_mode')
        self.assertEqual(tool['parameters']['properties']['mode']['enum'], ['pride', 'aave'])
        self.assertIn('text', tool['parameters']['required'])


class VoiceTests(unittest.TestCase):
    def speak(self, tts):
        tts.client = Client(Response(chunks=[b'\x00\x01' * 64]))
        list(tts.synthesize('Ayyy.', Cancellation()))
        return tts.client.calls[0][0][1]

    def test_the_newest_mode_with_a_voice_speaks_and_otherwise_rex(self):
        tts = ElevenLabsTTS('key', 'rex-voice', {'pride': 'pride-voice', 'aave': ''})
        self.assertIn('/rex-voice/', self.speak(tts))
        tts.modes = lambda: ('pride',)
        self.assertIn('/pride-voice/', self.speak(tts))
        tts.modes = lambda: ('pride', 'aave')                 # No AAVE voice configured: the newest with one.
        self.assertIn('/pride-voice/', self.speak(tts))
        tts.mode_voices['aave'] = 'aave-voice'
        self.assertIn('/aave-voice/', self.speak(tts))

    def test_mode_voices_are_optional_local_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root).resolve()
            (root / 'secrets.toml').write_text('elevenlabs_pride_voice_id = "p"\nelevenlabs_aave_voice_id = "a"\n')
            secrets = Secrets.load(root)
            self.assertEqual((secrets.elevenlabs_pride_voice_id, secrets.elevenlabs_aave_voice_id), ('p', 'a'))
            self.assertNotIn('elevenlabs_pride_voice_id', secrets.readiness())
            self.assertNotIn("'p'", repr(secrets))


class SessionTests(MemoryFixture):
    turn = test_attention.AttentionTests.turn

    def test_the_switch_speaks_its_own_line_in_one_call_and_the_next_turn_carries_the_mode(self):
        brain = Scripted(switch('pride', text='Yasss, sis! Gay and fabulous.'), 'Spill the tea, sis.')
        self.session = Session('session', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), self.state.trace),
                               self.state.trace, self.state)
        self.addCleanup(self.session.close)
        result = self.turn('Rex, are you gay?')
        self.assertEqual(result.completed_text, ['Yasss, sis! Gay and fabulous.'])
        self.assertEqual(len(brain.snapshots), 1)
        self.assertNotIn('voice_mode', brain.snapshots[0].context)
        self.assertEqual(result.gestures, [])                  # No body here: words only.
        self.turn('So, any gossip?')
        self.assertEqual(brain.snapshots[1].context['voice_mode'], {'pride': {'minutes_left': 10}})

    def test_pride_coming_on_throws_the_flourish_as_the_line_begins(self):
        trace = self.state.trace
        def wait(seconds): trace.at += seconds
        robot, port = robot_fixture(trace.clock, wait)
        self.addCleanup(robot.owner.close)
        gestures = PhysicalGesture(robot); gestures.start()
        brain = Scripted(switch('pride', text='Yasss!'))
        self.session = Session('session', brain, OutputOwner(SyntheticTTS(), SyntheticSink(), trace, gestures=gestures),
                               trace, self.state)
        self.addCleanup(self.session.close)
        before = dict(port.pose)
        result = self.turn('Are you gay?')
        self.assertEqual(result.completed_text, ['Yasss!'])
        self.assertEqual([(g['primitive'], g['timing'], g['status']) for g in result.gestures],
                         [('pride_flourish', 'with_speech', 'pulses_reached')])
        self.assertEqual(port.pose, before)                                   # Back where it was.
        again = self.turn('Seriously, are you gay?')
        self.assertEqual(again.gestures, [])                                  # Renewed, no second flourish.


class BodyTests(unittest.TestCase):
    def setUp(self):
        from tests.test_servos import PulsePort
        self.current = dict(PulsePort().pose)

    def test_the_flourish_goes_right_down_wide_open_and_all_the_way_over_then_back(self):
        head, visor, hand = flourish_lanes(self.current)
        out = {**dict(head[0].targets), **dict(visor[0].targets), **dict(hand[0].targets)}
        self.assertAlmostEqual(head_pose(out)[0], -1., delta=.02)
        self.assertEqual((out[3], out[5]), (VISOR_UP, LIMITS[5][1]))
        self.assertEqual({lane[0].seconds for lane in (head, visor, hand)}, {head[0].seconds})   # Arrive together.
        self.assertTrue(all(lane[0].dwell == 1. for lane in (head, visor, hand)))
        back = {**dict(head[1].targets), **dict(visor[1].targets), **dict(hand[1].targets)}
        self.assertEqual(back, {ch: self.current[ch] for ch in (*HEAD_PAIR, 3, 5)})
        self.assertNotIn(7, out)                                              # Never the microphone arm.
        for lane in (head, visor, hand):
            for beat in lane:
                validate_pose(dict(beat.targets))

    def test_pride_talks_with_bigger_quicker_hands_and_a_curled_throttle_wrist(self):
        for relaxed in THROTTLE_RELAXED:
            start = {**self.current, **dict(relaxed)}
            plain = talking_lanes(start, start, rng=random.Random(4))
            camp = talking_lanes(start, start, rng=random.Random(4), flair=True)
            arm = lambda lanes: next(l for l in lanes if set(dict(l[0].targets)) == {4, 5})
            spread = lambda lane, ch: max(dict(b.targets)[ch] for b in lane) - min(dict(b.targets)[ch] for b in lane)
            for ch in (4, 5):
                self.assertGreater(spread(arm(camp), ch), 2 * spread(arm(plain), ch) - 1)
            self.assertGreater(len(arm(camp)), len(arm(plain)))
            self.assertLess(max(b.seconds for b in arm(camp)), min(b.seconds for b in arm(plain)))
            throttle = next(l for l in camp if set(dict(l[0].targets)) == set(THROTTLE))
            self.assertEqual([dict(b.targets)[10] for b in throttle][-2:], [FLAIR_THROTTLE_WRIST] * 2)
            path = [dict(relaxed)] + [dict(b.targets) for b in throttle]
            self.assertTrue(all(throttle_clear(a, b) for a, b in zip(path, path[1:])))


class LightTests(unittest.TestCase):
    def setUp(self):
        self.led, self.ports, self.clock = rig()
        self.addCleanup(self.led.close)
        self.on = [False]
        self.led.pride = lambda: self.on[0]

    def pride(self, zone='chest'):
        return [c for c in self.ports[zone].commands if c.startswith('PRIDE')]

    def test_rainbow_is_renewed_while_pride_is_on_and_cleared_once_when_it_ends(self):
        self.led.tick()
        self.assertEqual(self.pride(), ['PRIDE:0'])                           # Startup's clear only.
        self.on[0] = True
        self.led.tick(); self.clock.at += 1; self.led.tick()
        self.assertEqual((self.pride(), self.pride('head')), (['PRIDE:0', 'PRIDE:1'], ['PRIDE:0', 'PRIDE:1']))
        self.clock.at += 3; self.led.tick()                                   # Inside the firmware's 10 s lease.
        self.assertEqual(self.pride()[-1:], ['PRIDE:1']); self.assertEqual(len(self.pride()), 3)
        self.on[0] = False
        self.led.tick(); self.clock.at += 5; self.led.tick()
        self.assertEqual(self.pride()[-1:], ['PRIDE:0']); self.assertEqual(len(self.pride()), 4)

    def test_sentry_red_wins_over_the_rainbow(self):
        self.on[0] = True
        self.led.tick()
        self.led.guard(True)
        self.led.tick(); self.clock.at += 5; self.led.tick()
        self.assertEqual(self.pride()[-2:], ['PRIDE:1', 'PRIDE:0'])


if __name__ == '__main__':
    unittest.main()
