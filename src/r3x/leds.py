"""One explicit LED owner; firmware renders patterns, host owns their lifetime."""
from array import array
from collections import deque
from dataclasses import dataclass
import math
import sys
import threading
import time
from typing import Callable, Mapping, Protocol

from .contracts import ProviderFailure
from .providers import Capability

COLORS = {'neutral':(0,180,255), 'happy':(0,255,100), 'excited':(255,200,0),
          'sad':(0,50,200), 'angry':(255,0,0), 'curious':(180,0,255)}
# sparkle is the chest firmware's COMPLIMENT effect. October 22 (1072c10a): named for its purpose, the
# model told the owner it could not make the chest sparkle; it also turned the eyes green and ran the
# mouth, so "he made your mouth sparkle too". It is named for what people see and is the chest's alone.
PATTERNS = (*COLORS, 'sparkle', 'blink')
# Light steps a requested movement sequence may include, e.g. "... and blink your eyes".
LIGHT_STEPS = {'blink': 'blink', **{'lights_'+p: p for p in PATTERNS if p != 'blink'}}
# Sentry mode (owner, October 22): eyes, mouth and chest held red for as long as he guards. The mouth
# glows at this steady level between lines, and speech keeps the same red.
GUARD_LEVEL = 140
AWAKE_EYES = (255,200,0)   # Warm gold, the eyes of an ordinary awake Rex.
FLASH_SECONDS = .25        # The alarm flash: eyes and mouth on and off this often.
# October 23 (1e626e07): the eyes went dark for several seconds between lines with no command sent and no
# board reset in the system log, and stayed dark until his next line set them. Between lines the eye
# colour is sent again this often, so a dropped repaint or power dip heals within seconds.
EYE_REFRESH_SECONDS = 3.


@dataclass(frozen=True)
class LightRequest:
    request_id: str
    pattern: str
    duration: float
    expires_at: float
    version: int


@dataclass(frozen=True)
class LightPerformance:
    request: LightRequest
    text: str | None
    timing: str
    valid: Callable[[], bool]


class LEDPort(Protocol):
    def write(self, data: bytes) -> int: ...
    def close(self) -> None: ...


def open_serial(path: str) -> LEDPort:
    import serial
    return serial.Serial(path,115200,timeout=.1,write_timeout=.1,exclusive=True)


class LEDs:
    capability = Capability('robot_leds','Arduino head/chest firmware',
        'startup, playback amplitude, bounded model-selected light animations and fade',
        dependencies=('pyserial',), resources=('exclusive configured LED serial ports',),
        shutdown='latch shutdown, join level worker, fade then close')

    def __init__(self, ports: Mapping[str, str], *, opener: Callable[[str], LEDPort] = open_serial,
                 clock=time.monotonic, wait=time.sleep):
        self.ports, self.opener, self.clock, self.wait = dict(ports), opener, clock, wait
        self.devices = {}
        self.lock = threading.RLock()
        self.state, self.version = 'new', 0
        self.speaking = False
        self.demo = False
        self.sample = (b'',0.)
        # Startup sound is written up to about 1.5 s ahead (BufferedOutput), 46 ms per sample.
        self.samples = deque(maxlen=48)
        self.last_level, self.last_sent = -1, float('-inf')
        self.stop_event = threading.Event()
        self.worker = None
        self.fade_at = None
        self.eye_color = AWAKE_EYES
        self.mouth = 'neutral'            # Emotion of the running mouth animation.
        self.chest_only = False           # The running animation leaves the head as it is.
        self.guarding = False             # Sentry mode's held red (guard).
        self.flash_until = float('-inf')  # The alarm's flashing, until then.
        self._flash_on = True
        self._flash_at = float('-inf')
        self.speak_sent, self.speak_sends = float('-inf'), 0
        self.eyes_sent = float('-inf')    # When the eye colour was last sent between lines.
        self.blinking = False             # A requested blink is dark on purpose: no refresh over it.
        self.trace = None
        self.on_command = []              # Observers (zone, command) of each command written; the dashboard mirror.

    def _send(self, zone, command):
        device = self.devices.get(zone)
        if device is None:
            return
        data = (command+'\n').encode('ascii')
        try:
            if device.write(data) != len(data):
                raise OSError('short write')
        except Exception:
            shutting_down = command=='FADEOFF'
            self.state = 'faulted'
            self.version += 1
            # Best-effort stop each healthy board after a partial paired write.
            # No reconnect or success claim; firmware watchdog is the head backstop.
            self.speaking = self.demo = False
            for peer in (() if shutting_down else self.devices.values()):
                try: peer.write(b'SPEAK_STOP\nACTIVE\n')
                except Exception: pass
            raise ProviderFailure('LEDWriteFailed') from None
        for hook in self.on_command:
            try:
                hook(zone, command)
            except Exception:
                pass   # A display observer never affects the lights.

    def _both(self, command):
        for zone in self.devices:
            self._send(zone,command)

    def start(self):
        if self.state != 'new':
            raise ProviderFailure('LEDAlreadyStarted')
        for zone,path in self.ports.items():
            self.devices[zone] = self.opener(path)
        self.wait(2.)  # Arduino DTR reset/boot, before any command.
        with self.lock:
            self._both('PRIDE:0')  # Clear an inherited firmware overlay.
            self._send('head','ACTIVE')
            self._send('head','EYE:'+','.join(map(str,AWAKE_EYES)))
            self._send('chest','STARTUP')
            self.state = 'ready'
            if self.trace: self.trace.emit('led_lifecycle',kind='startup',status='commands_sent',evidence='display_unverified')
        self.worker = threading.Thread(target=self._levels,name='led-levels',daemon=True)
        self.worker.start()

    def pcm(self, data, at):
        # Called on the audio callback: a bounded timestamped mailbox, no serial,
        # locks, amplitude computation, logging or unbounded queue.
        self.samples.append((bytes(data[:4096]),at))

    def clear_pcm(self):
        with self.lock:
            self.samples.clear()
            self.sample = (b'',0.)

    def tick(self):
        with self.lock:
            if self.state != 'ready':
                return
            now = self.clock()
            if not (self.speaking or self.demo):
                if ('head' in self.devices and not self.chest_only and not self.blinking
                        and now - self.eyes_sent >= EYE_REFRESH_SECONDS):
                    self._send('head', 'EYE:' + ','.join(map(str, self.eye_color)))
                    self.eyes_sent = now
                return
            while self.samples and self.samples[0][1]<=now:
                self.sample = self.samples.popleft()
            # The head board loses serial bytes while it repaints its LEDs (V2 sends
            # SPEAK_STOP three times for that reason). SPEAK: was sent once per phrase,
            # so a lost one left the mouth dark for the whole line while levels kept
            # arriving (owner, October 3: the mouth animation fired inconsistently).
            # Repeat it shortly after the start and then every second; it restarts the
            # level from zero, so the current level is sent again right after.
            if now-self.speak_sent >= (.12 if self.speak_sends < 3 else 1.):
                self._send('head','SPEAK:'+self.mouth)
                self.speak_sent, self.speak_sends = now, self.speak_sends+1
                self.last_level, self.last_sent = -1, float('-inf')
            pcm,at = self.sample
            level = (GUARD_LEVEL if self.guarding else 180) if self.demo else 0
            if self.speaking and pcm and 0 <= now-at < .2:
                samples = array('h',pcm[:len(pcm)//2*2])
                if sys.byteorder != 'little': samples.byteswap()
                level = min(255,int(math.sqrt(sum(v*v for v in samples)/len(samples))/32768*800))
            if now < self.flash_until:
                if now-self._flash_at >= FLASH_SECONDS:
                    self._flash_on, self._flash_at = not self._flash_on, now
                    self._send('head','EYE:'+(','.join(map(str,self.eye_color)) if self._flash_on else '0,0,0'))
                if not self.speaking:
                    level = 255 if self._flash_on else 0
            elif not self._flash_on:
                self._flash_on = True   # The flash is over: the eyes come back on.
                self._send('head','EYE:'+','.join(map(str,self.eye_color)))
            if abs(level-self.last_level)>=8 or now-self.last_sent>=.5:
                self._send('head',f'SPEAK_LEVEL:{level}')
                self.last_level,self.last_sent = level,now

    def _levels(self):
        while not self.stop_event.wait(.05):
            try:
                self.tick()
            except Exception as exc:
                if self.trace:
                    self.trace.emit('led_fault',reason=type(exc).__name__)
                return

    def blink(self, request, cancel, valid):
        with self.lock:
            if self.state!='ready' or 'head' not in self.devices: raise ProviderFailure('HeadLEDUnavailable')
            if request.pattern!='blink' or request.version!=self.version or self.clock()>=request.expires_at:
                raise ProviderFailure('StaleLEDRequest')
            cancel.check()
            if not valid(): raise ProviderFailure('StaleLEDRequest')
            self.version += 1
            token = self.version
            restore = 'EYE:'+','.join(map(str,self.eye_color))
            self.blinking = True
        try:
            for _ in range(3):
                for command,seconds in (('EYE:0,0,0',.15),(restore,.2)):
                    with self.lock:
                        cancel.check()
                        if not valid() or self.state!='ready' or token!=self.version or self.clock()>=request.expires_at:
                            raise ProviderFailure('StaleLEDRequest')
                        self._send('head',command)
                    deadline=self.clock()+seconds
                    while self.clock()<deadline:
                        cancel.check()
                        self.wait(min(.025,deadline-self.clock()))
            return 'commands_sent'
        finally:
            with self.lock:
                self.blinking = False
                # Restore an interrupted blink, but never override shutdown/new work.
                if self.state=='ready' and token==self.version:
                    self._send('head',restore)
                    self.version += 1

    def guard(self, on):
        """Sentry mode on: eyes, mouth and chest red until it ends (speech stays red). Off: the
        ordinary awake lights. Returns the version, like begin."""
        with self.lock:
            if self.state != 'ready': raise ProviderFailure('LEDUnavailable')
            self.version += 1
            self.guarding, self.flash_until, self._flash_on = on, float('-inf'), True
            if on:
                self.speaking, self.demo, self.chest_only = False, True, False
                self.last_level, self.last_sent = -1, float('-inf')
                self.eye_color, self.mouth = COLORS['angry'], 'angry'
                self._send('head','EYE:'+','.join(map(str,self.eye_color)))
                self._send('head','SPEAK:angry')
                self.speak_sent, self.speak_sends = self.clock(), 1
                self._send('chest','SPEAK:angry')
            else:
                self.speaking = self.demo = False
                self.eye_color = AWAKE_EYES
                self._send('head','SPEAK_LEVEL:0')
                for index in range(3):
                    self._send('head','SPEAK_STOP')
                    if index<2: self.wait(.025)
                self._send('head','ACTIVE')
                self._send('head','EYE:'+','.join(map(str,self.eye_color)))
                self._send('chest','SPEAK_STOP')
            if self.trace: self.trace.emit('led_lifecycle',kind='sentry_on' if on else 'sentry_off',
                                           status='commands_sent',evidence='display_unverified')
            return self.version

    def flash(self, seconds):
        """The alarm: while guarding, the red eyes and mouth flash on and off for this long."""
        with self.lock:
            if self.state == 'ready' and self.guarding:
                self.flash_until = self.clock() + seconds

    def begin(self, pattern='neutral', *, speaking=False, request=None):
        with self.lock:
            if self.state != 'ready': raise ProviderFailure('LEDUnavailable')
            if pattern not in PATTERNS or pattern=='blink': raise ProviderFailure('UnknownLEDPattern')
            if self.guarding:
                pattern = 'angry'   # Sentry mode: every line in red.
            if pattern=='sparkle' and 'chest' not in self.devices:
                raise ProviderFailure('ChestLEDUnavailable')
            if request and (request.version!=self.version or self.clock()>=request.expires_at):
                raise ProviderFailure('StaleLEDRequest')
            self.version += 1
            self.chest_only = pattern=='sparkle' and not speaking
            if self.chest_only:
                self.speaking = self.demo = False   # No mouth levels: the head is not part of it.
                self._send('chest','COMPLIMENT')
                return self.version
            if pattern=='sparkle':
                # October 22 (0421a222): run before his words, the sparkle added 2.5 s before he spoke.
                # With words, the chest sparkles while the mouth talks as it always does.
                self.speaking,self.demo = True,False
                self.last_level,self.last_sent = -1,float('-inf')
                self.eye_color = COLORS['neutral']
                self._send('head','EYE:'+','.join(map(str,self.eye_color)))
                self._send('head','SPEAK:neutral')
                self.mouth, self.speak_sent, self.speak_sends = 'neutral', self.clock(), 1
                self._send('chest','COMPLIMENT')
                return self.version
            self.speaking,self.demo = speaking,not speaking
            self.last_level,self.last_sent = -1,float('-inf')
            self.eye_color = COLORS[pattern]
            self._send('head','EYE:'+','.join(map(str,self.eye_color)))
            self._send('head','SPEAK:'+pattern)
            self.mouth, self.speak_sent, self.speak_sends = pattern, self.clock(), 1
            self._send('chest','SPEAK:'+pattern)
            return self.version

    def finish(self, token):
        with self.lock:
            if token != self.version or self.state != 'ready':
                return 'failed' if self.state=='faulted' else 'superseded'
            if self.guarding:
                # Back to the held red glow between lines; the chest stays red.
                self.speaking, self.demo = False, True
                self.last_level, self.last_sent = -1, float('-inf')
                self.version += 1
                return 'commands_sent'
            self.speaking = self.demo = False
            if not self.chest_only:
                self._send('head','SPEAK_LEVEL:0')
                for index in range(3):
                    self._send('head','SPEAK_STOP')
                    if index<2: self.wait(.025)
                self._send('head','ACTIVE')  # SPEAK_STOP suspends firmware blinking.
            self.chest_only = False
            self._send('chest','SPEAK_STOP')
            self.version += 1
            return 'commands_sent'

    def cancel(self):
        # Lifecycle shutdown wins over late speech cleanup and queued tools.
        with self.lock:
            if self.state in {'closing','closed'}: return
            self.stop_event.set()
            self.speaking = self.demo = False
            self.version += 1
            failed = False
            self.state = 'closing'
            try:
                for zone in self.devices:
                    try: self._send(zone,'FADEOFF')
                    except ProviderFailure: failed = True
                if failed: raise ProviderFailure('LEDFadeCommandFailed')
                if self.trace: self.trace.emit('led_lifecycle',kind='shutdown',status='commands_sent',evidence='display_unverified')
            finally:
                self.fade_at = self.clock()
                self.state = 'closing'

    def close(self):
        if self.state=='closed': return
        try:
            self.cancel()
        finally:
            if self.worker:
                self.worker.join(timeout=2.)
                if self.worker.is_alive(): raise ProviderFailure('LEDWorkerDidNotStop')
            if self.fade_at is not None and self.devices:
                self.wait(max(0.,4.-(self.clock()-self.fade_at)))
            for zone in tuple(self.devices):
                self.devices[zone].close()
                del self.devices[zone]
            self.state = 'closed'
