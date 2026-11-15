"""LED contracts: fake serial/audio only, through production state and output owners."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from r3x.leds import LEDs, LightRequest
from r3x.contracts import Cancellation, Cancelled, Capture, Transcript, ToolCall, ProviderFailure
from r3x.output import OutputOwner
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.session import Session
from r3x.settings import Settings
from r3x.semantics import grounded_tools
from r3x.app import App
from r3x.hardware_ownership import HardwareLocks, ControllerHandoff
from tests.test_memory_core import MemoryFixture
from tests.test_voice_core import Brain


class Clock:
    def __init__(self): self.at=100.
    def __call__(self): return self.at
    def wait(self,n): self.at+=n


class Strip:
    """One strip's view of the fake board: the commands addressed to it."""
    def __init__(self,board): self.board=board;self.commands=[]
    closed=property(lambda self:self.board.closed)
    fail=property(lambda self:self.board.fail,lambda self,value:setattr(self.board,'fail',value))


class Board:
    """Fake LED board on one port: answers HELLO, sorts each command by its strip prefix."""
    def __init__(self,reply=b'hello djr3x_leds 0.1.0 head=82@18 chest=98@19\n'):
        self.reply=reply;self.replies=[];self.lines=[];self.closed=False;self.fail=False
        self.strips={'head':Strip(self),'chest':Strip(self)}
    def write(self,data):
        if self.fail: raise OSError('synthetic wire failure')
        for line in data.decode().split('\n'):
            if not line: continue
            self.lines.append(line)
            if line=='HELLO': self.replies.append(self.reply)
            elif line[:2]=='H:': self.strips['head'].commands.append(line[2:])
            elif line[:2]=='C:': self.strips['chest'].commands.append(line[2:])
            else: raise AssertionError('the owner names a strip in every command: '+line)
        return len(data)
    def readline(self): return self.replies.pop(0) if self.replies else b''
    def close(self): self.closed=True


def rig():
    clock=Clock();board=Board()
    led=LEDs('board',opener=lambda path:board,clock=clock,wait=clock.wait)
    led.start()
    # Deterministic ticks without a competing real-time worker.
    led.stop_event.set();led.worker.join(1)
    return led,board.strips,clock


class LEDTests(unittest.TestCase):
    def setUp(self):
        self.led,self.ports,self.clock=rig()
        self.addCleanup(self.led.close)

    def test_start_and_shutdown_preserve_firmware_effects(self):
        self.assertEqual(self.ports['chest'].commands,['PRIDE:0','STARTUP'])
        self.assertEqual(self.ports['head'].commands[-2:],['ACTIVE','EYE:255,200,0'])
        self.assertEqual(self.ports['head'].board.lines[0],'HELLO')   # Identified before any light command.
        self.assertEqual(self.led.firmware,'0.1.0')
        self.assertEqual(self.clock(),100.)                           # No blind boot wait.
        self.led.close()
        self.assertEqual(self.ports['head'].commands[-1],'FADEOFF')
        self.assertEqual(self.ports['chest'].commands[-1],'FADEOFF')
        self.assertEqual(self.clock(),104.)                           # The four-second fade.
        self.assertTrue(self.ports['head'].closed)

    def test_shutdown_fade_hands_the_chest_to_the_charge_meter(self):
        # Owner, November 15: with the battery level known, the chest's fade ends in the meter.
        self.led.charge_meter = lambda: (97, False)
        self.led.close()
        self.assertEqual(self.ports['head'].commands[-1],'FADEOFF')
        self.assertEqual(self.ports['chest'].commands[-1],'FADEOFF:97:0')
        self.assertEqual(self.clock(),104.)

    def test_without_a_usable_battery_reading_the_fade_is_plain(self):
        def broken(): raise ProviderFailure('DriveLinkClosed')
        for reading in (lambda: None, lambda: (101, True), lambda: ('97', False), broken):
            with self.subTest(reading=reading):
                led,ports,_=rig()
                led.charge_meter=reading
                led.close()
                self.assertEqual(ports['chest'].commands[-1],'FADEOFF')

    def test_a_failed_meter_fade_wakes_no_lights(self):
        clock=Clock();board=Board();writes=[];real=board.write
        def write(data):
            writes.append(data)
            if data.startswith(b'C:FADEOFF'): raise OSError('synthetic wire failure')
            return real(data)
        board.write=write
        led=LEDs('board',opener=lambda path:board,clock=clock,wait=clock.wait)
        led.start();led.stop_event.set();led.worker.join(1)
        led.charge_meter=lambda: (60, True)
        with self.assertRaises(ProviderFailure):
            led.close()
        failed=writes.index(b'C:FADEOFF:60:1\n')
        self.assertEqual(writes[failed+1:],[])     # No SPEAK_STOP / ACTIVE during shutdown.
        self.assertTrue(board.closed)

    def test_pcm_drives_only_playback_and_silence_has_watchdog_heartbeat(self):
        self.led.pcm(b'\x00\x40'*1024,self.clock())
        self.led.tick()
        self.assertFalse(any(c.startswith('SPEAK_LEVEL') for c in self.ports['head'].commands))
        token=self.led.begin(speaking=True)
        self.led.tick()
        self.assertEqual(self.ports['head'].commands[-1],'SPEAK_LEVEL:255')
        self.clock.wait(.3);self.led.tick()
        self.assertEqual(self.ports['head'].commands[-1],'SPEAK_LEVEL:0')
        before=len(self.ports['head'].commands)
        self.clock.wait(.1);self.led.tick()
        self.assertEqual(len(self.ports['head'].commands),before)
        self.clock.wait(.5);self.led.tick()
        self.assertEqual(self.ports['head'].commands[before:],['SPEAK_LEVEL:0'])

    def test_mouth_start_and_stop_are_each_sent_once(self):
        # Only the level stream repeats: it feeds the firmware's 1.5 s mouth watchdog.
        token=self.led.begin(speaking=True)
        head=self.ports['head'].commands
        for _ in range(60):
            self.clock.wait(.05);self.led.pcm(b'\x00\x40'*1024,self.clock());self.led.tick()
        self.assertEqual(head.count('SPEAK:neutral'),1)
        self.assertEqual(self.led.finish(token),'commands_sent')
        self.assertEqual(head[-3:],['SPEAK_LEVEL:0','SPEAK_STOP','ACTIVE'])
        self.assertEqual(head.count('SPEAK_STOP'),1)
        self.assertEqual(self.ports['chest'].commands[-1],'SPEAK_STOP')

    def test_fault_never_reports_display_success_and_no_reconnect(self):
        token=self.led.begin(speaking=True)
        self.ports['head'].fail=True
        with self.assertRaises(ProviderFailure): self.led.tick()
        self.assertEqual(self.led.finish(token),'failed')
        with self.assertRaises(ProviderFailure): self.led.begin()
        with self.assertRaises(ProviderFailure): self.led.close()
        self.assertTrue(self.ports['head'].closed)

    def test_shutdown_wins_over_late_speech_stop(self):
        token=self.led.begin('happy',speaking=True)
        self.led.cancel();before=list(self.ports['head'].commands)
        self.assertEqual(self.led.finish(token),'superseded')
        self.led.tick()
        self.assertEqual(self.ports['head'].commands,before)
        with self.assertRaises(ProviderFailure): self.led.begin()

    def test_stale_expired_and_unknown_requests_rejected(self):
        for req in (LightRequest('x','happy',1,99,0),LightRequest('x','happy',1,200,8)):
            with self.assertRaises(ProviderFailure): self.led.begin(req.pattern,request=req)
        with self.assertRaises(ProviderFailure): self.led.begin('OFF\nCHARGE:100:1')

    def test_a_device_that_does_not_answer_as_the_led_board_is_closed_and_never_commanded(self):
        for reply in (b'',b'hello djr3x_motion 0.2.7\n',b'\xff\x00noise\n'):
            board=Board(reply)
            led=LEDs('board',opener=lambda path:board,wait=lambda n:None)
            with self.assertRaises(ProviderFailure): led.start()
            self.assertTrue(board.closed)
            self.assertEqual(set(board.lines),{'HELLO'})
            led.close()
            self.assertEqual(set(board.lines),{'HELLO'})   # Not even a fade.
        booted=Board(b'ready djr3x_leds 0.1.0 head=82@18 chest=98@19\n')   # The line a fresh boot prints.
        led=LEDs('board',opener=lambda path:booted,wait=lambda n:None)
        led.start();self.addCleanup(led.close)
        self.assertEqual(led.state,'ready')

    def test_failed_open_leaves_nothing_to_close(self):
        def opener(path): raise OSError('failed')
        led=LEDs('board',opener=opener,wait=lambda n:None)
        with self.assertRaises(OSError): led.start()
        led.close();self.assertIsNone(led.device)

    def test_composition_inert_and_ports_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            app=App(Settings(led_port='/dev/cu.leds'),Path(directory))
            app.configure_live()
            self.assertEqual([p.capability.name for p in app.owner.providers[:4]],['tts','playback','startup_audio','robot_leds'])
            self.assertIsNone(app.leds.device)
            retired=Path(directory)/'settings.toml'
            retired.write_text('head_led_port = "/dev/cu.head"\n')
            with self.assertRaisesRegex(ValueError,'replaced by led_port'): Settings.load(retired)
        for kwargs in ({'led_port':'auto'},{'led_port':'/dev/tty.foo'},
                       {'led_port':'/dev/cu.x','servo_port':'/dev/cu.x'}):
            with self.assertRaises(ValueError): Settings(**kwargs)

    def test_handoff_waits_for_the_led_port_without_opening(self):
        with tempfile.TemporaryDirectory() as directory:
            locks=HardwareLocks(Path(directory)/'controller')
            probes=[]
            def free(path):
                probes.append(path)
                return self.clock()>103
            handoff=ControllerHandoff(locks,led_ports={'leds':'board'},
                port_free=free,legacy_active=lambda:False,clock=self.clock,wait=self.clock.wait)
            handoff.acquire(Cancellation())
            self.assertIn('board',probes)
            self.assertFalse(locks.lease('leds').acquire())
            handoff.close()
            self.assertFalse(locks.controller_running())


def choice(timing='during_speech',text='A little gold for the occasion.',pattern='excited'):
    return ToolCall('lights','animate_lights',dict(pattern=pattern,timing=timing,duration='brief',text=text))


class LEDSessionTests(MemoryFixture):
    def setup_session(self,call=None,sink=None):
        self.led,self.ports,self.clock=rig()
        # Match expiry validation to the same session clock.
        self.led.clock=self.state.trace.clock
        self.led.wait=lambda n:setattr(self.state.trace,'at',self.state.trace.at+n)
        self.brain=Brain(call or choice())
        self.output=OutputOwner(SyntheticTTS(),sink or SyntheticSink(),self.state.trace,leds=self.led)
        self.session=Session('session',self.brain,self.output,self.state.trace,self.state)
        self.addCleanup(self.led.close);self.addCleanup(self.session.close)

    def turn(self):
        return self.session.process_turn(Capture('capture','session',bytes(1024),98,99,99.6),
            Transcript('Please show me a happy light animation.',-.01,True),self.session.revision)

    def test_model_style_and_speech_one_generation_real_output_path(self):
        self.setup_session();result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.lights[0]['status'],'commands_sent')
        self.assertEqual(result.lights[0]['evidence'],'commands_only_display_unverified')
        self.assertIn('SPEAK:excited',self.ports['head'].commands)
        self.assertEqual(self.brain.calls,1)
        self.assertFalse(self.store.claims())

    def test_a_wordless_light_reply_is_asked_once_for_words_then_runs_silently(self):
        # October 22 (1072c10a): "Okay, do it." and "No, not the sparkle..." got a silent sparkle.
        self.setup_session(choice('before_speech',None,'sparkle'));result=self.turn()
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(result.completed_text,[])
        self.assertEqual(result.lights[0]['status'],'commands_sent')
        self.assertIn('COMPLIMENT',self.ports['chest'].commands)
        self.assertEqual(self.brain.calls,2)
        self.assertIn('animate_lights needs text',json.dumps(self.brain.snapshots[-1].messages))
        self.assertFalse(any(r['event']=='playback_started' for r in self.state.trace.rows))

    def test_the_retry_brings_the_line_said_after_the_lights(self):
        self.setup_session(choice('before_speech',None,'sparkle'))
        class Sequence(Brain):
            def __init__(self, calls): super().__init__(); self.left=list(calls)
            def reply(self, snapshot, cancel):
                self.calls += 1; self.snapshots.append(snapshot)
                yield self.left.pop(0)
        self.brain=self.session.brain=Sequence([choice('before_speech',None,'sparkle'),
                                                choice('before_speech','There. Dazzled yet?','sparkle')])
        result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.completed_text,['There. Dazzled yet?'])
        self.assertIn('COMPLIMENT',self.ports['chest'].commands)
        self.assertEqual(self.brain.calls,2)

    def test_sparkle_is_the_chest_alone(self):
        # October 22 (1072c10a): "He made your mouth sparkle too."
        led,ports,clock=rig();self.addCleanup(led.close)
        head=list(ports['head'].commands)
        token=led.begin('sparkle')
        led.tick()
        self.assertEqual(ports['chest'].commands[-1],'COMPLIMENT')
        self.assertEqual(led.finish(token),'commands_sent')
        self.assertEqual(ports['head'].commands,head)          # No eye colour, no mouth.
        self.assertEqual(ports['chest'].commands[-1],'SPEAK_STOP')
        # October 22 (0421a222): with words it runs while he speaks, the mouth talking as usual.
        token=led.begin('sparkle',speaking=True)
        self.assertEqual(ports['chest'].commands[-1],'COMPLIMENT')
        self.assertIn('SPEAK:neutral',ports['head'].commands)
        self.assertEqual(led.finish(token),'commands_sent')

    def test_a_sparkle_with_words_starts_speaking_at_once(self):
        self.setup_session(choice('before_speech','There. Dazzled yet?','sparkle'));result=self.turn()
        self.assertEqual(result.status,'completed')
        prepared=next(r for r in self.state.trace.rows if r['event']=='light_request_prepared')
        self.assertEqual(prepared['arguments']['timing'],'during_speech')
        self.assertIn('COMPLIMENT',self.ports['chest'].commands)

    def test_ordinary_reply_automatically_animates_speech(self):
        self.setup_session();self.brain.text='Hello, the lights follow actual playback.'
        result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertIn('SPEAK:neutral',self.ports['head'].commands)
        self.assertEqual(result.lights[0]['status'],'commands_sent')

    def test_playback_failure_does_not_start_selected_animation(self):
        class Fail(SyntheticSink):
            def play(self,*a): raise ProviderFailure('SyntheticFailure')
        self.setup_session(sink=Fail());result=self.turn()
        self.assertEqual(result.status,'failed')
        self.assertEqual(result.lights[0]['status'],'cancelled')
        self.assertNotIn('SPEAK:excited',self.ports['head'].commands)

    def test_unavailable_not_offered_and_invented_call_rejected(self):
        self.setup_session();self.output.leds=None
        result=self.turn()
        self.assertEqual(result.status,'failed')
        self.assertNotIn('animate_lights',{t['name'] for t in grounded_tools(self.brain.snapshots[-1].context)})

    def test_logged_empty_speech_animation_runs_silently(self):
        self.setup_session(choice(text=''))
        result=self.turn()
        self.assertEqual(result.status,'suppressed')
        self.assertEqual(result.lights[0]['status'],'commands_sent')
        self.assertEqual(result.completed_text,[])
        self.assertEqual(self.brain.calls,2)                   # Asked once for words, as above.

    def test_silent_and_standalone_patterns_normalize_to_demo(self):
        for call in (choice(text=None),choice(pattern='sparkle')):
            self.setup_session(call)
            result=self.turn()
            self.assertEqual(result.lights[0]['status'],'commands_sent')
            if call.arguments['pattern']=='sparkle':
                self.assertIn('COMPLIMENT',self.ports['chest'].commands)
            self.session.close();self.led.close()

    def test_unknown_pattern_still_rejected(self):
        self.setup_session(choice(pattern='OFF\nSLEEP'))
        self.assertEqual(self.turn().status,'failed')

    def test_wire_failure_preserves_successful_audio(self):
        self.setup_session();self.ports['head'].fail=True
        result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.lights[0]['status'],'failed')
        self.assertTrue(result.completed_text)
        self.ports['head'].fail=False

    def test_cancel_during_demo_stops_pattern(self):
        self.setup_session(choice('before_speech',None))
        def interrupted(n): self.session.cancel()
        self.led.wait=interrupted
        result=self.turn()
        self.assertEqual(result.status,'interrupted')
        self.assertEqual(result.lights[0]['status'],'cancelled')
        self.assertEqual(self.ports['head'].commands[-1],'ACTIVE')
        self.led.wait=lambda n:None

    def test_revision_change_before_playback_rejects_light_request(self):
        owner=self
        class Changed(SyntheticSink):
            def play(self,chunks,cancel,on_start):
                owner.led.version+=1
                return super().play(chunks,cancel,on_start)
        self.setup_session(sink=Changed());result=self.turn()
        self.assertEqual(result.status,'completed')
        self.assertEqual(result.lights[0]['status'],'failed')
        self.assertNotIn('SPEAK:excited',self.ports['head'].commands)

class LEDTimingTests(unittest.TestCase):
    def test_future_dac_samples_wait_and_mailbox_is_bounded(self):
        led,ports,clock=rig()
        self.addCleanup(led.close)
        led.begin(speaking=True)
        led.pcm(b'\x00\x40'*1024,clock()+.1)
        led.tick()
        self.assertEqual(ports['head'].commands[-1],'SPEAK_LEVEL:0')
        clock.wait(.1);led.tick()
        self.assertEqual(ports['head'].commands[-1],'SPEAK_LEVEL:255')
        for _ in range(100): led.pcm(bytes(8192),clock()+1)
        self.assertEqual(len(led.samples),48)   # Bounded; holds the ~1.5 s buffered startup sound writes ahead.
        self.assertEqual(len(led.samples[0][0]),4096)

    def test_failed_fade_still_closes_the_port(self):
        led,ports,clock=rig()
        ports['head'].fail=True
        with self.assertRaises(ProviderFailure): led.close()
        self.assertTrue(ports['head'].closed)
        self.assertEqual(led.state,'closed')

    def test_one_busy_led_port_releases_all_leases_on_timeout(self):
        clock=Clock()
        with tempfile.TemporaryDirectory() as directory:
            locks=HardwareLocks(Path(directory)/'controller')
            handoff=ControllerHandoff(locks,led_ports={'leds':'board','drive_base':'drive'},
                legacy_active=lambda:False,port_free=lambda path:path=='drive',
                clock=clock,wait=clock.wait)
            with self.assertRaises(ProviderFailure): handoff.acquire(Cancellation(),timeout=.2)
            self.assertFalse(locks.controller_running())
            for name in ('microphone','leds','drive_base'):
                probe=locks.lease(name)
                self.assertTrue(probe.acquire());probe.close()
