"""Timestamped microphone-relative evidence, never an identity or camera bearing."""
from collections import deque
from dataclasses import dataclass
import math
import threading
import time

from .contracts import ProviderFailure
from .providers import Capability


def wrap(degrees):
    return (degrees + 180) % 360 - 180


def coordinates(bearing):
    """Explain an array-relative angle without quantizing the measurement."""
    if type(bearing) not in (int, float) or not math.isfinite(bearing):
        raise ValueError('InvalidDirectionBearing')
    angle = wrap(bearing)
    magnitude = abs(angle)
    if angle == 0:
        side, description = 'center', 'directly ahead of the microphone array'
    elif magnitude == 180:
        side, description = 'center', 'directly behind the microphone array'
    else:
        side = 'left' if angle > 0 else 'right'
        description = f'{magnitude:.1f} degrees {side} of microphone-array forward'
        if magnitude <= 10:
            description = f'nearly straight ahead, {magnitude:.1f} degrees to the microphone array\'s {side}'
        elif magnitude >= 170:
            description = f'nearly directly behind, {180-magnitude:.1f} degrees toward the microphone array\'s {side} from rear'
        elif magnitude > 90:
            description += f' ({magnitude-90:.1f} degrees behind its {side} side)'
        elif magnitude == 90:
            description += f' (directly to its {side})'
    return {'side': side, 'degrees_from_forward': magnitude, 'description': description}


@dataclass(frozen=True)
class DirectionSample:
    at: float
    doa: float
    beam: float
    energy: float
    eligible: bool


def summarize(samples, start, end):
    """Use only this speech window, excluding playback and low-energy polls.

    V2's beam energy floor and circular clustering survive; competing clusters
    are exposed rather than accepting a 40% plurality as one speaker's bearing.
    Array motion and close speakers remain unmeasured, not inferred identities.
    """
    rows = [r for r in samples if start <= r.at <= end and r.eligible and r.energy >= 50000]
    result = {'status': 'unavailable', 'reason': 'too_few_speech_energy_samples',
              'frame': 'microphone_array', 'positive': 'left_counterclockwise',
              'started_at': start, 'ended_at': end, 'samples': len(rows),
              'bearing_deg': None, 'coordinates': None, 'clusters': [], 'identity_evidence': False,
              'geometry': 'Arm/base motion not measured; no exact camera transform'}
    if len(rows) < 3:
        return result
    span = max(.001, rows[-1].at - rows[0].at)
    weights = [r.energy * (.5 + .5 * (r.at - rows[0].at) / span) for r in rows]
    remaining = set(range(len(rows)))
    groups = []
    while remaining:
        indices = max(([j for j in remaining if abs(wrap(rows[j].beam-rows[i].beam)) <= 20]
                       for i in sorted(remaining)), key=lambda js: (sum(weights[j] for j in js), max(js)))
        weight = sum(weights[j] for j in indices)
        centre = wrap(math.degrees(math.atan2(
            sum(weights[j]*math.sin(math.radians(rows[j].beam)) for j in indices),
            sum(weights[j]*math.cos(math.radians(rows[j].beam)) for j in indices))))
        groups.append({'bearing_deg': centre, 'samples': len(indices),
                       'coordinates': coordinates(centre),
                       'weight_share': weight / sum(weights),
                       'spread_deg': max(abs(wrap(rows[j].beam-centre)) for j in indices)})
        remaining.difference_update(indices)
    result['clusters'] = groups[:3]
    first = groups[0]
    if first['samples'] < 3:
        return result
    if first['weight_share'] < .7:
        result.update(status='ambiguous', reason='competing_directions')
    else:
        result.update(status='observed', reason='dominant_speech_energy_beam',
                      bearing_deg=first['bearing_deg'], coordinates=first['coordinates'])
    return result


class Direction:
    def __init__(self, control, echo_risk=lambda: False, trace=None, clock=time.monotonic):
        self.control, self.echo_risk, self.trace, self.clock = control, echo_risk, trace, clock
        self.capability = Capability('direction', 'ReSpeaker Flex USB',
            'time-aligned speech direction; supporting evidence only',
            dependencies=('pyusb', 'libusb-package'), resources=('read-only USB; 10 Hz worker',),
            required=False)
        self.samples = deque(maxlen=400)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.worker = None
        self.failure = None
        self.last_echo = float('-inf')

    def start(self):
        self.control.start()
        self.poll()
        self.worker = threading.Thread(target=self.run, name='r3x-direction', daemon=True)
        self.worker.start()

    def poll(self):
        began = self.clock()
        before_echo = self.echo_risk()
        doa, flag = self.control.read('doa')
        energy = self.control.read('energy')[3]
        radians = self.control.read('azimuth')[3]
        if not all(math.isfinite(v) for v in (doa, energy, radians)) or energy < 0:
            raise ProviderFailure('FlexNonfiniteOrNegativeEvidence')
        now = self.clock()
        if before_echo or self.echo_risk():
            self.last_echo = now
        beam = wrap(math.degrees(radians))
        sample = DirectionSample((began+now)/2, float(doa), beam, energy,
                                 now-began <= .15 and now-self.last_echo > .4)
        with self.lock:
            self.samples.append(sample)
        if self.trace:
            self.trace.emit('direction_sample', sampled_at=sample.at, doa_deg=doa,
                speech_flag=bool(flag), beam_deg=beam, speech_energy=energy,
                eligible=sample.eligible, read_seconds=now-began)

    def run(self):
        while not self.stop.is_set():
            began = self.clock()
            try:
                self.poll()
            except Exception as exc:
                self.failure = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
                with self.lock:
                    self.samples.clear()
                if self.trace:
                    self.trace.emit('provider_failed', capability='direction', reason=self.failure)
                self.stop.set()
            self.stop.wait(max(0, .1 - (self.clock()-began)))

    def during(self, start, end):
        with self.lock:
            evidence = summarize(tuple(self.samples), start, end)
        if self.stop.is_set():
            evidence.update(status='unavailable', reason='direction_provider_stopped',
                            bearing_deg=None, coordinates=None, clusters=[])
        return evidence

    def cancel(self):
        self.stop.set()

    def close(self):
        self.cancel()
        if self.worker:
            self.worker.join(2)
            if self.worker.is_alive():
                raise ProviderFailure('DirectionWorkerDidNotStop')
        self.control.close()
        with self.lock:
            self.samples.clear()
