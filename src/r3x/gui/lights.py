"""Display mirror of the head/chest LED firmware (V2 gui/mouth_leds.py, rex_avatar.py).

The LED owner reports each command it wrote; `LightMirror` turns that command stream into
mode-level state the avatar renders. Firmware animation phases run on the Arduinos, so
this is a command-level mirror, not pixel readback. Without LED boards (dev Mac) the
dashboard feeds `speaking()` from playback and a PCM level, so the mouth still moves.
"""
from __future__ import annotations

import math
import random
import threading
import time


MOUTH_COLORS = {'neutral': (255, 140, 0), 'happy': (0, 200, 255), 'excited': (255, 200, 0),
                'sad': (40, 0, 200), 'angry': (255, 0, 0), 'curious': (180, 0, 255)}


class LightMirror:
    """Thread-safe: `command` runs on the LED owner's thread, `snapshot` on the GUI's."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = threading.Lock()
        self.head = {'mode': 'off', 'eye_color': (0, 0, 0), 'eyes_active': False, 'updated_at': 0.,
                     'mouth_mode': 'off', 'mouth_emotion': 'neutral', 'mouth_level': 0, 'mouth_utterance': 0,
                     'mouth_soc': 0, 'mouth_updated_at': 0.}
        self.chest = {'mode': 'off', 'emotion': None, 'soc': None, 'charging': False, 'flash_at': 0., 'updated_at': 0.}
        self.mirrored = False   # True once a real LED board command was seen.

    def command(self, zone: str, command: str) -> None:
        family, _, value = command.strip().partition(':')
        family = family.upper()
        now = self.clock()
        with self.lock:
            self.mirrored = True
            if zone == 'head':
                self._head(family, value, now)
            elif zone == 'chest':
                self._chest(family, value, now)

    def _head(self, family, value, now):
        head = self.head
        if family == 'EYE':
            try:
                rgb = tuple(max(0, min(255, int(v))) for v in value.split(','))[:3]
            except ValueError:
                return
            if len(rgb) == 3:
                head.update(eye_color=rgb, eyes_active=any(rgb), updated_at=now)
                if head['mouth_mode'] in {'off', 'sleep', 'charge'}:
                    head.update(mouth_mode='active', mouth_updated_at=now)
            return
        if family == 'SPEAK':
            head.update(mouth_mode='speak', mouth_emotion=value.strip().lower() or 'neutral', mouth_level=0,
                        mouth_utterance=head['mouth_utterance'] + 1, mouth_updated_at=now)
        elif family == 'SPEAK_LEVEL':
            try:
                head['mouth_level'] = max(0, min(255, int(value)))
            except ValueError:
                return
            head['mouth_updated_at'] = now
        elif family in {'SPEAK_STOP', 'ACTIVE', 'IDLE', 'OFF', 'SLEEP', 'FADEOFF', 'CHARGE'}:
            head.update(mouth_mode={'SPEAK_STOP': 'active'}.get(family, family.lower()), mouth_updated_at=now)
            if family == 'CHARGE':
                try:
                    head['mouth_soc'] = max(0, min(100, int(value.split(':')[0])))
                except ValueError:
                    head['mouth_soc'] = 0
            if family in {'ACTIVE', 'IDLE', 'OFF', 'SLEEP', 'FADEOFF'}:
                head.update(mode=family.lower(), updated_at=now)
                if family in {'OFF', 'FADEOFF'}:
                    head['eyes_active'] = False

    def _chest(self, family, value, now):
        chest = self.chest
        if family in {'STARTUP', 'IDLE', 'ACTIVE', 'SLEEP', 'OFF', 'FADEOFF'}:
            chest['mode'] = family.lower()
        elif family == 'SPEAK':
            chest.update(mode='speak', emotion=value.strip().lower() or 'neutral')
        elif family == 'SPEAK_STOP':
            chest['mode'] = 'active'
        elif family == 'CHARGE':
            parts = value.split(':')
            chest.update(mode='charge', soc=int(parts[0]) if parts[0].isdigit() else 0,
                         charging=len(parts) > 1 and parts[1] == '1')
        elif family == 'COMPLIMENT':
            chest['flash_at'] = now
        else:
            return
        chest['updated_at'] = now

    def speaking(self, active: bool, level: int = 0, emotion: str = 'neutral') -> None:
        """Playback-derived mouth when no head board reports commands (dev Mac)."""
        now = self.clock()
        with self.lock:
            if self.mirrored:
                return
            head = self.head
            if active and head['mouth_mode'] != 'speak':
                head.update(mouth_mode='speak', mouth_emotion=emotion,
                            mouth_utterance=head['mouth_utterance'] + 1)
            elif not active and head['mouth_mode'] == 'speak':
                head['mouth_mode'] = 'active'
            if active:
                head['mouth_level'] = max(0, min(255, int(level)))
            head['mouth_updated_at'] = now

    def awake(self) -> None:
        """No LED boards: show the startup eye colour so the avatar is not dark all run."""
        now = self.clock()
        with self.lock:
            if not self.mirrored and self.head['mode'] == 'off':
                self.head.update(mode='active', eye_color=(255, 200, 0), eyes_active=True, updated_at=now,
                                 mouth_mode='active', mouth_updated_at=now)
                self.chest.update(mode='active', updated_at=now)

    def snapshot(self) -> dict:
        with self.lock:
            return {'head': dict(self.head), 'chest': dict(self.chest), 'mirrored': self.mirrored}


class MouthAnimation:
    """Visual mirror of head_nano's 8x10 mouth equalizer: levels, colours and ballistics."""

    def __init__(self):
        import numpy as np
        self.height = np.zeros(8)
        self.peak = np.zeros(8)
        self.phase = np.arange(8) * .73
        self.last = None
        self.utterance = None
        self.last_pixels = np.zeros((80, 3))
        self.fade_pixels = None
        self.last_mode = 'off'

    def render(self, state, now):
        import numpy as np
        dt = min(.1, max(0, now - self.last)) if self.last is not None else .033
        self.last = now
        mode = state.get('mouth_mode', 'off')
        age = max(0, now - state.get('mouth_updated_at', now))
        rgb = np.array(MOUTH_COLORS.get(state.get('mouth_emotion', 'neutral'), MOUTH_COLORS['neutral']), float) / 255
        if mode == 'speak' and age > 1.5:
            mode = 'active'
        if state.get('mouth_utterance') != self.utterance:
            self.height[:] = 0
            self.peak[:] = 0
            self.utterance = state.get('mouth_utterance')
        values = np.zeros((10, 8))
        if mode == 'speak':
            level = max(0, min(255, state.get('mouth_level', 0))) / 255
            self.phase += (2.2 + .55*np.arange(8)) * (.6 + 1.4*level) * dt
            target = np.minimum(5, level * (.6 + .4*np.sin(self.phase)) * np.array([.55, .75, .92, 1, 1, .92, .75, .55]) * 5.2)
            self.height += (target - self.height) * np.minimum(1, np.where(target > self.height, 18, 6) * dt)
            self.peak = np.where(self.height > self.peak, self.height, np.maximum(0, self.peak - 3.5*dt))
            for d in range(5):
                bright = np.clip(self.height - d, 0, 1) * .45
                if d == 0:
                    bright = np.maximum(.1, bright)
                bright = np.where((self.peak > self.height + .6) & (np.minimum(4, self.peak.astype(int)) == d),
                                  np.maximum(bright, .315), bright)
                values[4-d] = bright
                values[5+d] = bright
        elif mode in ('active', 'idle'):
            values[:] = .075 + .025*math.sin(now)
        elif mode in ('sleep', 'charge'):
            phase = (now % 8) / 8
            values[:] = .3 * (phase*2 if phase < .5 else 2 - phase*2)
            soc = state.get('mouth_soc', 0)
            rgb = np.array((255, 0, 0) if mode == 'sleep' or soc <= 25 else (255, 96, 0) if soc <= 50 else
                           (255, 220, 0) if soc <= 75 else (0, 255, 0) if soc <= 90 else (0, 0, 255), float) / 255
        if mode == 'fadeoff':
            if self.last_mode != 'fadeoff':
                self.fade_pixels = self.last_pixels.copy()
            result = (self.fade_pixels if self.fade_pixels is not None else self.last_pixels) * max(0, 1 - age/4)
        else:
            result = values.reshape(80, 1) * rgb
        self.last_mode = mode
        self.last_pixels = result.copy()
        return result


class EyeBlink:
    """V2's eye animation: random blinks (sometimes doubled) and a slow idle breathe."""

    def __init__(self, clock=time.monotonic, rng=None):
        self.clock, self.rng = clock, rng or random.Random()
        self.state = 'open'
        self.timer = clock()
        self.interval = self.rng.uniform(2., 8.)
        self.duration = 0.
        self.second = False
        self.idle_phase = 0.
        self.last_tick = clock()
        self.last_event = None

    def brightness(self, head) -> float:
        """0..1 multiplier for the eye colour this frame."""
        now = self.clock()
        dt = max(0., min(.25, now - self.last_tick))
        self.last_tick = now
        if head.get('updated_at') != self.last_event:
            self.last_event = head.get('updated_at')
            self.state, self.timer, self.second = 'open', now, False
            self.interval = self.rng.uniform(2., 8.)
        if head.get('mode') == 'idle':
            self.idle_phase += dt * .8
        if not head.get('eyes_active') or not any(head.get('eye_color') or ()):
            self.state, self.second, self.timer = 'open', False, now
            return 0.
        if self.state == 'open' and now - self.timer >= self.interval:
            self.state, self.timer, self.duration = 'closed', now, self.rng.uniform(.1, .4)
        elif self.state == 'closed' and now - self.timer >= self.duration:
            self.timer = now
            if not self.second and self.rng.random() < .1:
                self.state, self.duration = 'double_wait', self.rng.uniform(.2, .4)
            else:
                self.state, self.second, self.interval = 'open', False, self.rng.uniform(2., 8.)
        elif self.state == 'double_wait' and now - self.timer >= self.duration:
            self.state, self.second, self.timer, self.duration = 'closed', True, now, self.rng.uniform(.1, .4)
        if self.state == 'closed':
            return 0.
        return .3 + .35*(1 + math.sin(self.idle_phase)) if head.get('mode') == 'idle' else 1.


# Palettes from arduino/chest_nano/chest_nano.ino, screen-brightened (V2 rex_avatar.py).
CHEST_SMALL = ((208, 44, 40),) * 3 + ((196, 205, 210),) * 4 + ((64, 88, 224),) * 2
CHEST_BLOCKS = ((255, 56, 40), (235, 240, 245), (255, 221, 136), (80, 120, 255))
CHEST_EMOTIONS = {
    'excited': (((255, 70, 16), (255, 120, 30)), ((255, 90, 0), (255, 40, 20))),
    'sad': (((40, 70, 220), (25, 45, 170)), ((40, 70, 220), (25, 45, 170))),
    'angry': (((255, 42, 30),), ((255, 42, 30),)),
    'happy': (((255, 200, 80), (255, 255, 255)), CHEST_BLOCKS),
}
GAUGE = ((234, 0, 0), (234, 40, 0), (234, 81, 0), (216, 180, 0), (108, 198, 27), (0, 216, 54), (0, 148, 162), (0, 81, 255))
FLASH_SECONDS = 2.2


def gauge_color(frac):
    frac = max(0., min(1., frac))
    pos = frac * (len(GAUGE) - 1)
    i = int(pos)
    if i >= len(GAUGE) - 1:
        return GAUGE[-1]
    f = pos - i
    return tuple(int(GAUGE[i][c] + (GAUGE[i+1][c] - GAUGE[i][c]) * f) for c in range(3))


def _pick(palette, rnd):
    return palette[min(len(palette) - 1, int(rnd * len(palette)))]


def _prand(step, *salts):
    """Deterministic 0..1 per (step, salts): firmware-style block flicker without state."""
    x = (step * 2654435761) & 0xFFFFFFFF
    for s in salts:
        x ^= (s * 40503 + 0x9E3779B9 + ((x << 6) & 0xFFFFFFFF) + (x >> 2)) & 0xFFFFFFFF
    return ((x >> 8) & 1023) / 1023.


def chest_render_state(state, now):
    """Mirrored chest mode -> render parameters (`now` on the mirror's clock)."""
    mode = str(state.get('mode') or 'off').lower()
    emotion = str(state.get('emotion') or '').lower()
    updated = float(state.get('updated_at') or 0.)
    out = {'on': True, 'brightness': 1., 'rate': 1.5, 'fill': None, 'ladder': CHEST_SMALL, 'squares': CHEST_BLOCKS,
           'gauge': False, 'charging': False,
           'flash': 0. < now - float(state.get('flash_at') or 0.) < FLASH_SECONDS}
    if mode == 'off':
        out['on'] = False
    elif mode == 'fadeoff':
        out['brightness'] = max(0., min(1., 1 - (now - updated) / 4))
        out['on'] = out['brightness'] > 0
    elif mode == 'sleep':
        out.update(brightness=.3, rate=.3, fill=.15)
    elif mode == 'active':
        out['rate'] = 3.5
    elif mode == 'startup':
        out.update(rate=6., fill=(now * 1.5) % 1.)
    elif mode == 'speak':
        ladder, squares = CHEST_EMOTIONS.get(emotion, (CHEST_SMALL, CHEST_BLOCKS))
        out.update(ladder=ladder, squares=squares, rate=2.5 if emotion == 'sad' else 9.)
    elif mode == 'charge':
        soc = state.get('soc')
        out.update(rate=1., fill=max(0., min(1., (int(soc) if soc is not None else 0) / 100)), gauge=True,
                   charging=bool(state.get('charging')))
    return out


def lamp_colors(state, now):
    """Nine chest lamp colours (0..255 RGB) for a chest_render_state."""
    colors = []
    step = int(now * state['rate'])
    brightness = state['brightness'] if state['on'] else 0.
    for i in range(9):
        rgb = _pick(state['squares'], _prand(step // 3, i, 5))
        level = brightness * (.7 + .3*_prand(step, i, 99))
        if state['fill'] is not None:
            lit = i < round(9*state['fill'])
            if state['gauge']:
                rgb = gauge_color(i/8)
                if state['charging'] and i == round(9*state['fill']) + step % max(1, 9-round(9*state['fill'])):
                    lit, rgb = True, (30, 220, 255)
            level = brightness if lit else 0.
        if state['flash'] and brightness:
            rgb = (240, 246, 255) if (int(now*8)+i) % 2 else (80, 145, 255)
        colors.append(tuple(int(max(0, min(255, c*level))) for c in rgb))
    return colors
