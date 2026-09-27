"""Deterministic startup health speech through the existing output owner."""
from dataclasses import asdict
from queue import Queue

from .contracts import Cancellation
from .output import Delivery

HARDWARE = {'camera':'camera', 'capture':'microphone', 'playback':'audio output',
            'direction':'microphone direction sensor', 'robot_servos':'servo controller',
            'robot_leds':'LED controllers'}


def notice_text(settings, results, *, failed=False):
    unavailable = list(dict.fromkeys(HARDWARE[r.capability] for r in results
        if r.operation=='start' and r.outcome=='failed' and r.capability in HARDWARE))
    parts = []
    if 'camera' in unavailable and not failed:
        parts.append("Oh boy, I'm blind, my photoreceptors are malfunctioning. "
                     "I can still hear you though, are you there?")
        unavailable.remove('camera')
    if unavailable:
        names = unavailable[0] if len(unavailable)==1 else ', '.join(unavailable[:-1])+' and '+unavailable[-1]
        parts.append('Hardware unavailable: '+names+'.')
    if failed:
        parts.append("Startup failed. Please check the startup report.")
    return ' '.join(parts)


def announce(settings, owner, output, trace, *, failed=False):
    text = notice_text(settings,owner.results,failed=failed)
    if not text:
        return None
    trace.emit('startup_notice',text=text,startup_failed=failed)
    started = {r.capability for r in owner.results if r.operation=='start' and r.outcome=='ok'}
    if not {'tts','playback'}.issubset(started):
        trace.emit('startup_notice_delivery',status='unavailable',reason='SpeechOutputNotReady')
        return None
    phrases = Queue()
    phrases.put(text)
    phrases.put(None)
    delivery = Delivery()
    # No model generation, alternate voice or device writer. Live composition adds
    # decorative motion only after startup health speech has finished.
    output.run(phrases,Cancellation(),'startup-health',delivery)
    trace.emit('startup_notice_delivery',**asdict(delivery))
    return delivery
