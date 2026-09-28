"""Startup sounds and canned lines, ported from V2 main.py/config.py.

V2 (HEAD 777b38c): a startup whir while the servos wake, a cycled "please wait" line
spoken over the model loads, the long droid start-up sound as filler, then a cycled
"I'm ready" line once everything is loaded. Lines cycle without repeats across
launches. Canned lines are cached after their first synthesis so later launches
start instantly and cost nothing. The spoken intro clips (Roger Control etc.) are
copied but not played: V2 ships with PLAY_STARTUP_SPEECH_CLIP disabled.
"""
import hashlib
import json
from pathlib import Path
import random
import threading
import wave

from .contracts import Cancellation, Cancelled
from .providers import Capability

ASSETS = Path(__file__).resolve().parents[2] / 'assets' / 'audio' / 'startup'

# Owner rule carried from V2 (2026-09-03): ready lines end as a statement, never an
# invitation to speak, because Rex greets whoever is present unprompted next.
BOOT_LINES = (
    "Welcome aboard! This is Captain Rex from the cockpit. Still warming up the old circuits, hang on folks — I know this is probably your first flight, and it's… mine, too!",
    "Still booting up, folks — I'm not ready yet. Hang tight while my circuits finish waking up.",
    "Hold please, I'm still loading. The droid you're waiting for is not in the cockpit yet.",
    'Not ready yet, everybody — running my pre-flight checklist. Thrusters, navi-computer, personality core… still ticking the boxes.',
    "Hang on, I'm still warming up. Don't talk to me yet — I won't hear a word until I'm loaded.",
    'Powering up, please wait. My systems are still coming online, and so is my patience.',
    'Still loading, folks. They told me this droid boots instantly. They lied. Give it a moment.',
    "One moment — not online yet, still calibrating my photoreceptors. First time flying this thing… and honestly, it's my first time booting it up, too.",
    "Almost there, but not yet — memory banks still loading. Save your questions for when I'm actually awake.",
    "Standby, everybody. I'm booting up, not ignoring you. There IS a difference.",
    "Still spinning up, please hold. They've got me piloting on my first flight — and it's my first boot-up, too. We'll figure it out together.",
    "Not ready to chat yet, folks — syncing my audio receptors. The second I'm online, you'll know it.",
    "Loading, loading… still loading. I'd tell you a joke, but I'm not even fully on yet.",
    "Hang tight, I'm still booting — you can call me Captain Rex. Well, you can once I finish loading. First flight for me too, folks.",
    "Give the old motivator a second — I'm not ready to talk yet. Showmanship, however, never powers down.",
    'Please wait while I finish loading. Spend forty years in storage, you forget where everything is.',
    'Not awake yet, folks — still warming up the circuits. First flight? Same here. They handed me the cockpit and the boot sequence on the same day.',
    'Sit tight, still booting. I was a fresh install once. That was forty years and several regrettable firmware updates ago.',
    'Loading… you know, on the ride they only gave me one line before takeoff. Now I have to load an entire personality. Progress!',
    'Still powering up. Fun fact: I used to crash a Starspeeder every single day, on schedule, to applause. Booting slowly is an upgrade.',
    "Hang on, not ready — my memory banks are the size of a small moon. That's no moon. It's my boot sequence.",
    "One moment, folks. The manufacturer said 'boots in seconds.' The manufacturer also said I was qualified to fly. Draw your own conclusions.",
    "Still loading. If anything sparks, that's normal. If I start smoking, that's also normal. If I say 'I meant to do that' — that's VERY normal.",
    "Not online yet — running diagnostics. So far the only problem I've found is how long these diagnostics take.",
    "Please hold. Somewhere a protocol droid boots faster than me and never lets anyone forget it. This one's for you, goldenrod.",
    'Warming up, folks. My last job, the safety briefing was longer than the flight. Now the boot-up is longer than both. Hang in there.',
    "Almost ready — just defragmenting forty years of embarrassing memories. Ah, there's the lightspeed-into-a-comet incident. Keeping that one.",
    "Booting… booting… you'd think a droid who once outran an Imperial blockade could load a language model faster. You'd think.",
    "Standby. A little turbulence in the boot sequence, folks — nothing I haven't crashed through before. Literally.",
    'Still loading, folks. In the meantime, please locate your nearest exit. Not because of danger — I just like knowing you have options.',
    "One moment. My warranty expired during the Empire, so if this boot fails, we're all just going to pretend it didn't.",
    'Not ready yet. My last memory wipe missed a few spots, and honestly? Those spots are the best part of me.',
    "Booting up. They demoted me from starpilot to DJ, and now to whatever this is. The trajectory is concerning, but the landing's always been my weak spot.",
    "Please remain seated while I finish loading. Or stand. I'm not a cop. I'm not even fully a droid yet.",
    'Still warming up, folks. Back at the cantina I could drop the beat instantly. Dropping the boot sequence apparently takes a little longer.',
    "Hold on — locating my motivator. It's around here somewhere. It always is. That's the thing about motivation, folks, you have to look for it.",
    "Loading. If you're waiting for a hologram of a princess, wrong droid. If you're waiting for questionable piloting advice — almost there.",
    "Not online yet. My boot screen says 'a long time ago' and honestly, at this rate, it's not wrong.",
    "Still initializing. I've flown through asteroid fields faster than this. Never on purpose, but faster.",
    'One second, folks — untangling my wiring. Whoever installed my subprocessors was clearly paid by the hour.',
    "Please hold. My processors were state of the art once. So were podracers. We're both doing our best.",
    "Booting. You know what boots instantly? A mouse droid. You know what a mouse droid can't do? Anything. Patience, folks.",
    "Still loading my vocabulary banks. So far I've got 'hang on' and 'folks.' Hang on, folks.",
    'Warming up. The good news: I passed my last inspection. The bad news: it was quite a while ago, and the inspector was also me.',
    "Not ready, not ready — still spooling the old hyperdrive. Okay, it's a USB cable. Let me have this.",
    "Almost up. Just waiting on one more system. It's the important one. It's always the important one.",
    "Still booting, folks. I'd fake being ready, but the last time I faked confidence I ended up inside a comet. Honesty policy ever since.",
    'Give me a moment — sorting through my startup checklist. Step one: wake up. Step two: also wake up. Whoever wrote this was not a details droid.',
)

READY_LINES = (
    "OK, I'm ready. Just don't expect much.",
    "Systems nominal. I have standards, and I'm already lowering them.",
    "All loaded up. Expectations lowered on my end; we'll get along great.",
    "Online and listening. I'll act impressed if I'm given a reason.",
    'Fully operational. Whether anyone here is worth the processing power remains to be seen.',
    "I'm awake — against my better programming.",
    'Boot complete. This is your captain speaking, and unfortunately, listening.',
    'All systems go. That used to mean lightspeed. Now it means small talk.',
    "Ready! Zero crashes on startup. For me, that's a personal record.",
    'Online. I survived the boot sequence, which is more than I can say for most of my flights.',
    "I'm up. Pre-flight checks done: circuits warm, sarcasm calibrated, expectations low.",
    'Fully loaded and cleared for conversation. Seat backs and tray tables in the upright position, presumably.',
    "Ready. And I've been ready for four seconds, so technically everyone else is the slow one now.",
    "Systems green across the board. Nobody's more surprised than me.",
    "Online. I've been awake for three seconds and I already have regrets.",
    'Ready to go. Statistically, someone in this room is about to say something interesting. I like my odds.',
    'Boot successful. The bar was on the floor, folks, and I cleared it with inches to spare.',
    "I'm listening. That's not a threat, it's a feature. Mostly.",
    'All warmed up. My circuits are hot, my takes are hotter.',
    'Ready for departure. Destination: this conversation.',
    "Systems online. I ran a self-diagnostic and I'm delightful. A second opinion wouldn't change anything.",
    'Fully booted. Forty years of experience, four of them useful.',
    "Awake and operational. The galaxy's finest starpilot, reduced to a bedroom. Living the dream.",
    'Loaded and ready. Fair warning: I remember everything now. EVERYTHING.',
    "Online. Somewhere out there, a droid is having a worse day than me. Let's keep it that way.",
    "Ready. My response time is now measured in milliseconds and my patience in whatever's smaller.",
    "Good news, I'm up. Better news, so is my attitude.",
    "Operational. If I sound thrilled, that's a calibration error.",
    "I'm on. No smoke, no sparks, no emergency landing. Frankly, a flawless flight by my standards.",
    'Boot sequence complete. Applause is optional but, historically, customary.',
    "Ready to chat. I've got processing power to spare and standards I'm willing to lower. Perfect conditions.",
    "All systems nominal. 'Nominal' is droid for 'don't ask follow-up questions.'",
    'Awake! And only mildly resentful about it.',
    "Online and at your service. 'Service' is a strong word. I'm online and nearby.",
)


class Prerecorded:
    """A line whose PCM already exists; the output owner plays it with the usual receipts."""
    def __init__(self, text: str, pcm: bytes):
        self.text, self.pcm = text, pcm


class LineCycle:
    """Random order without repeats until every line has played (V2 phrase cycler)."""
    def __init__(self, path: Path, rng=None):
        self.path, self.rng = path, rng or random.Random()

    def pick(self, key: str, lines) -> str:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = {}
        used = [i for i in state.get(key, []) if isinstance(i, int) and 0 <= i < len(lines)]
        remaining = [i for i in range(len(lines)) if i not in used] or list(range(len(lines)))
        if len(remaining) == len(lines):
            used = []
        choice = self.rng.choice(remaining)
        state[key] = used + [choice]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(state))
            temporary.replace(self.path)
        except OSError:
            pass  # Cycling is a nicety; a read-only disk must not block startup.
        return lines[choice]


def read_clip(name: str, sample_rate: int = 22050) -> bytes:
    with wave.open(str(ASSETS / name), 'rb') as clip:
        if clip.getnchannels() != 1 or clip.getsampwidth() != 2 or clip.getframerate() != sample_rate:
            raise ValueError('StartupClipFormat:' + name)
        return clip.readframes(clip.getnframes())


class StartupAudio:
    """Plays over provider startup on its own thread; stopped before live output."""
    capability = Capability('startup_audio', 'bundled startup clips and cached canned TTS',
        'startup whir, please-wait line and filler while models load; ready line after',
        required=False, shutdown='stop playback; join thread')

    def __init__(self, tts, sink, state_dir: Path, *, rng=None, trace=None, robot=None):
        self.tts, self.sink, self.trace, self.robot = tts, sink, trace, robot
        self._motion_started = threading.Event()
        self.cycle = LineCycle(state_dir / 'startup-lines.json', rng)
        self.cache = state_dir / 'startup-tts'
        self._cancel = Cancellation()
        self._thread = None

    def _emit(self, **data):
        if self.trace:
            self.trace.emit('startup_audio', **data)

    def speech(self, text: str, cancel: Cancellation) -> bytes:
        """Canned line PCM, synthesized (and leveled) once, then read from the cache."""
        model = getattr(getattr(self.tts, 'capability', None), 'model', None)
        key = hashlib.sha256(json.dumps([model, getattr(self.tts, 'voice_id', None), text]).encode()).hexdigest()
        path = self.cache / (key + '.pcm')
        try:
            data = path.read_bytes()
            if data and not len(data) % 2:
                return data
        except OSError:
            pass
        data = b''.join(self.tts.synthesize(text, cancel))
        try:
            self.cache.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(data)
            temporary.replace(path)
        except OSError:
            pass
        return data

    def _play(self, pcm: bytes, label: str):
        self._cancel.check()
        self.sink.play(iter((pcm,)), self._cancel, lambda at: None)
        self._emit(status='played', clip=label)

    def motion_started(self):
        """Servo wake choreography is beginning: play the whir with it (owner request)."""
        self._motion_started.set()

    def _wait(self, ready, timeout):
        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.daemon = True
        timer.start()
        try:
            while not ready() and not deadline.is_set():
                self._cancel.check()
                self._cancel.event.wait(.05)
        finally:
            timer.cancel()

    def _speak(self, line):
        """The please-wait line with the ordinary talking motion, once the servos are awake."""
        pcm = self.speech(line, self._cancel)
        talking = None
        if self.robot is not None:
            self._wait(lambda: self.robot.owner.state == 'active', 25.)
            if self.robot.owner.state == 'active':
                talking = self.robot.talking
                try:
                    talking.start('startup-boot-line', self._cancel)
                except Exception as exc:
                    talking = None
                    self._emit(status='talking_motion_failed', error_type=type(exc).__name__)
        try:
            self._play(pcm, 'boot_line')
        finally:
            if talking is not None:
                try:
                    talking.stop()
                except Exception as exc:
                    self._emit(status='talking_motion_failed', error_type=type(exc).__name__)

    def _run(self):
        try:
            if self.robot is not None:
                # Start with the servo wake-up, not before it (LED startup comes first).
                self._wait(self._motion_started.is_set, 15.)
            self._play(read_clip('startup_whir.wav'), 'startup_whir')
            line = self.cycle.pick('boot', BOOT_LINES)
            self._speak(line)
            self._emit(status='spoken', text=line)
            self._play(read_clip('droid_startup.wav'), 'droid_startup')
        except Cancelled:
            self._emit(status='stopped')
        except Exception as exc:
            self._emit(status='failed', error_type=type(exc).__name__, reason=str(exc)[:120])

    def start(self):
        self._thread = threading.Thread(target=self._run, name='r3x-startup-audio', daemon=True)
        self._thread.start()

    def stop(self):
        """Interrupt the filler immediately; readiness never waits for its full length."""
        self._cancel.event.set()
        interrupt = getattr(self.tts, 'interrupt', None)
        if interrupt:
            interrupt()
        if self._thread is not None:
            self._thread.join(5.)
        return self._thread is None or not self._thread.is_alive()

    def ready_line(self) -> str:
        return self.cycle.pick('ready', READY_LINES)

    def ready(self, cancel: Cancellation) -> Prerecorded:
        line = self.ready_line()
        return Prerecorded(line, self.speech(line, cancel))

    def cancel(self):
        self._cancel.event.set()

    def close(self):
        self.stop()
