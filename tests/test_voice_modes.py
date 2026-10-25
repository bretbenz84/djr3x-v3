"""Speaking modes (October 25): pride and AAVE (ported from V2) and eleven accents. The reply model
switches them or says one line in an accent, the state owner keeps ten minutes and the rarity of his own
accent lines, and the voice, the request, the lights and pride's body follow. Synthetic providers, pulse
ports and serial ports only: these prove the contracts, not how the lines sound, how the owner's chosen
ElevenLabs voices sound, or the flourish and rainbow on the robot."""
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
from r3x.voice_modes import ACCENTS, LINE_SECONDS, MODE_SECONDS, MODES, VoiceModes, directive
from tests.test_adapters import Client, Response
from tests.test_context_diet import send
from tests.test_leds import rig
from tests.test_memory_core import MemoryFixture
from tests import test_proactive_core
from tests.test_physical_attention import robot_fixture
from tests import test_attention


def switch(mode, on=True, text=None, turn=None, requested=True):
    return lambda snapshot: ToolCall('mode', 'voice_mode', {'mode': mode, 'switch': on if isinstance(on, str) else
        'on' if on else 'off', 'requested': requested, 'source_turn_id': turn or snapshot.context.get('source_turn_id', 'x'),
        'text': text})


class Scripted:
    """Answers each request with the next scripted reply (a string, or a function of the snapshot)."""
    def __init__(self, *replies):
        self.replies, self.snapshots = list(replies), []

    def reply(self, snapshot, cancel):
        self.snapshots.append(snapshot)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        yield reply(snapshot) if callable(reply) else reply


class ModeStateTests(MemoryFixture):
    def switch(self, mode, on=True, turn=None, requested=True, text=None):
        return self.call('voice_mode', {'mode': mode, 'switch': on if isinstance(on, str) else 'on' if on else 'off',
                                        'requested': requested, 'source_turn_id': turn or self.state.current[0],
                                        'text': text})

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

    def test_offered_for_a_persons_words_and_on_his_own_turns_only_for_an_allowed_line(self):
        names = lambda context: {d['name'] for d in grounded_tools({'event_id': 'e', **context})}
        self.assertIn('voice_mode', names({'source_turn_id': 't'}))
        for trigger in ('conversation_lull', 'visual_arrival', 'ambient'):
            self.assertNotIn('voice_mode', names({'trigger': trigger}))
        self.assertNotIn('voice_mode', names({'trigger': 'conversation_lull', 'agency_step': {'kind': 'contribute'}}))
        for trigger in ('conversation_lull', 'ambient', 'reconsideration'):
            self.assertIn('voice_mode', names({'trigger': trigger, 'accent_line_allowed': True}))
        self.assertIn('voice_mode', names({'trigger': 'conversation_lull', 'accent_line_allowed': True,
                                           'agency_step': {'kind': 'contribute'}}))
        self.assertNotIn('voice_mode', names({'trigger': 'visual_arrival', 'accent_line_allowed': True}))
        self.assertNotIn('voice_mode', names({'trigger': 'conversation_lull', 'accent_line_allowed': True,
                                              'agency_step': {'kind': 'look'}}))

    def test_switching_to_someone_else_ends_whoever_he_was(self):
        # Owner, October 25: asked to be a different identity, gay and homie modes and the rest switch off.
        for said, mode in (('Are you gay?', 'pride'), ("What's up my homie?", 'aave'), ('Do a British accent.', 'british'),
                           ('Now talk like a Frenchman.', 'french'), ('Are you gay, though?', 'pride')):
            self.begin(said)
            self.assertEqual(self.switch(mode).outcome, 'ok')
            self.assertEqual(self.state.voice_modes.active(self.state.trace.clock()), (mode,))
            self.assertEqual(set(self.state.context()['voice_mode']), {mode})
            self.assertNotIn('accent_line_allowed', self.state.context())   # The voice is already changed.
        self.begin('Now talk like a pirate.')
        self.assertEqual(self.switch('pirate').outcome, 'ok')                      # A mode of its own too.
        self.assertEqual(self.state.voice_modes.active(self.state.trace.clock()), ('pirate',))
        self.begin('Okay, now talk like a cowboy.')                                # No mode for that: off.
        self.assertEqual(self.switch('pirate', on=False).outcome, 'ok')
        self.assertFalse(self.state.voice_modes.active(self.state.trace.clock()))

    def test_a_line_he_is_asked_for_is_always_said_and_switches_nothing(self):
        self.begin('Say that like a German.')
        result = self.switch('german', 'line', text='Ja, genau. Wunderbar engineering.')
        self.assertEqual((result.outcome, result.data['switch'], result.data['requested']), ('ok', 'line', True))
        self.assertFalse(self.state.voice_modes.active(self.state.trace.clock()))
        self.begin('Again!')
        self.assertEqual(self.switch('german', 'line', text='Natürlich.').outcome, 'ok')
        self.begin('Now like a pirate.')
        self.assertEqual(self.switch('pirate', 'line', text='Arr, natürlich, matey.').outcome, 'ok')
        self.begin('And like a DJ!')
        self.assertEqual(self.switch('dj', 'line', text='Yo yo yo, natürlich!').outcome, 'ok')
        self.begin('Now in your gay voice.')
        self.assertEqual(self.switch('pride', 'line', text='Yasss.').reason, 'LineIsForAnAccent')
        self.begin('Say it French.')
        self.assertEqual(self.switch('french', 'line', text=None).reason, 'AccentLineNeedsItsWords')

    def test_a_line_of_his_own_comes_at_most_every_five_minutes_and_not_while_a_mode_is_on(self):
        self.begin("We're flying to Paris next week.")
        self.assertTrue(self.state.context()['accent_line_allowed'])
        self.assertEqual(self.switch('french', 'line', requested=False, text='Ah, Paris, mon ami!').outcome, 'ok')
        self.begin('And then Rome.')
        self.assertNotIn('accent_line_allowed', self.state.context())
        self.assertEqual(self.switch('italian', 'line', requested=False, text='Mamma mia.').reason, 'AccentLineNotNow')
        self.assertEqual(self.switch('dj', 'line', requested=False, text='Make some noise!').reason,
                         'OnlyWhenAskedFor')   # Never a DJ line of his own (owner, October 20).
        self.assertEqual(self.switch('italian', 'line', text='Mamma mia.').outcome, 'ok')   # Asked for: always.
        self.state.trace.at += LINE_SECONDS
        self.begin('Do a British accent.'); self.switch('british')
        self.assertNotIn('accent_line_allowed', self.state.context())
        self.assertEqual(self.switch('french', 'line', requested=False, text='Bon.').reason, 'AccentLineNotNow')
        self.switch('british', on=False)
        self.assertTrue(self.state.context()['accent_line_allowed'])

    def test_his_own_turn_cannot_switch_a_mode_on(self):
        from uuid import uuid4
        context = {'event_id': 'e', 'state_revision': self.store.revision, 'trigger': 'conversation_lull',
                   'accent_line_allowed': True, 'faces': []}
        def own(**args):
            return self.state.execute_opportunity(ToolCall(uuid4().hex, 'voice_mode', {
                'mode': 'french', 'switch': 'line', 'requested': False, 'source_turn_id': 'x',
                'text': 'Voilà.', **args}), context, Cancellation())
        self.assertEqual(own(switch='on').reason, 'VoiceModeNeedsAPersonsWords')
        self.assertEqual(own(requested=True).reason, 'VoiceModeNeedsAPersonsWords')
        self.assertEqual(own().outcome, 'ok')
        self.assertEqual(own().reason, 'AccentLineNotNow')
        self.assertFalse(self.state.voice_modes.active(self.state.trace.clock()))


class ModeClockTests(unittest.TestCase):
    def test_one_mode_at_a_time_for_ten_minutes(self):
        modes = VoiceModes()
        modes.switch('aave', True, 0.)
        modes.switch('pride', True, 5.)
        self.assertEqual(modes.active(10.), ('pride',))
        self.assertEqual(directive(modes.context(10.)).count(' MODE:'), 1)
        self.assertNotIn('AAVE', directive(modes.context(10.)))
        self.assertEqual(modes.active(5. + MODE_SECONDS), ())
        modes.switch('german', False, 20.)                       # Off for a mode that is not on changes nothing.
        self.assertEqual(modes.active(30.), ('pride',))
        with self.assertRaises(ValueError):
            modes.switch('cowboy', True, 0.)

    def test_every_accent_speaks_english_and_never_mocks(self):
        self.assertEqual(len(ACCENTS), 18)
        for accent in ACCENTS:
            note = directive({accent: {'minutes_left': 4}})
            self.assertIn(accent.upper() + ' ACCENT', note)
            for words in ('in English', 'never a caricature, broken English'):
                self.assertIn(words, note)


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
        self.assertEqual(tool['parameters']['properties']['mode']['enum'], list(MODES))
        self.assertEqual(tool['parameters']['properties']['switch']['enum'], ['on', 'off', 'line'])
        self.assertIn('text', tool['parameters']['required'])
        for accent in ACCENTS:
            self.assertIn(accent + ' (', tool['description'])

    def test_his_own_accent_line_is_suggested_only_while_one_is_allowed(self):
        payload, _ = send({'source_turn_id': 't', 'accent_line_allowed': True})
        self.assertIn('one-line accent (voice_mode line, requested false)', payload['input'][-1]['content'])
        payload, _ = send({'source_turn_id': 't'})
        self.assertNotIn('one-line accent', json.dumps(payload['input']))


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
        tts.modes = lambda: ('aave',)                         # No AAVE voice configured: Rex's own.
        self.assertIn('/rex-voice/', self.speak(tts))
        tts.mode_voices['aave'] = 'aave-voice'
        self.assertIn('/aave-voice/', self.speak(tts))
        tts.mode_voices['french'] = 'french-voice'
        tts.client = Client(Response(chunks=[b'\x00\x01' * 64]))
        list(tts.synthesize('Voilà.', Cancellation(), mode='french'))            # One line in an accent.
        self.assertIn('/french-voice/', tts.client.calls[0][0][1])
        self.assertIn('/aave-voice/', self.speak(tts))                           # Then back to the mode on.

    def test_mode_voices_are_optional_local_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root).resolve()
            (root / 'secrets.toml').write_text('elevenlabs_voice_id = "rex"\n[mode_voices]\npride = "p"\nfrench = " f "\naave = ""\n')
            secrets = Secrets.load(root)
            self.assertEqual(secrets.mode_voices, {'pride': 'p', 'french': 'f'})   # Empty keeps Rex's voice.
            self.assertNotIn('mode_voices', secrets.readiness())
            self.assertNotIn("'p'", repr(secrets))
            (root / 'secrets.toml').write_text('[mode_voices]\ncowboy = "x"\n')
            with self.assertRaises(ValueError):
                Secrets.load(root)


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


class AccentLineSessionTests(MemoryFixture):
    turn = test_attention.AttentionTests.turn
    setup_core = test_proactive_core.InitiativeCoreTests.setup_core
    visible = test_proactive_core.InitiativeCoreTests.visible

    def test_an_asked_for_line_is_said_in_that_voice_and_the_next_in_his_own(self):
        tts = SyntheticTTS()
        brain = Scripted(switch('british', 'line', text='Brilliant, absolutely brilliant, mate.'), 'Back to me.')
        self.session = Session('session', brain, OutputOwner(tts, SyntheticSink(), self.state.trace), self.state.trace, self.state)
        self.addCleanup(self.session.close)
        result = self.turn('Say that like a Brit.')
        self.assertEqual(result.completed_text, ['Brilliant, absolutely brilliant, mate.'])
        self.assertEqual(len(brain.snapshots), 1)
        self.turn('Thanks.')
        self.assertEqual(tts.modes, ['british', None])
        said = [r for r in self.state.trace.rows if r['event'] == 'speech_generated']
        self.assertEqual([r.get('voice_mode') for r in said], ['british', None])

    def test_his_own_lull_line_can_come_in_an_accent(self):
        tts = SyntheticTTS()
        brain = Scripted(lambda snapshot: switch('italian', 'line', requested=False, text='Allora, the pasta question.')(snapshot)
                         if snapshot.context.get('trigger') else 'Pasta tonight, then.')
        self.setup_core(brain, OutputOwner(tts, SyntheticSink(), self.state.trace))
        self.turn('We are making pasta tonight.')
        self.visible(108); self.runtime.proactive_tick()
        lull = brain.snapshots[-1].context
        self.assertTrue(lull.get('trigger') and lull.get('accent_line_allowed'))
        self.assertEqual(tts.modes[-1], 'italian')
        self.assertFalse(self.state.voice_modes.line_allowed(self.state.trace.clock()))


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
