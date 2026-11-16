"""Stage 0 telepresence measurement: how much of Rex's own speaker the microphone still hears.

On the robot, with the room quiet and Rex off (the program takes the robot's microphone lease):
    .venv/bin/python tools/measure_talkover_echo.py [--seconds 10] [--level -20] [--wav VOICE.wav] [--save-audio]

Plays a test voice (speech-like noise bursts, or a WAV of real speech) through the configured output
while recording every input channel of the configured microphone, as telepresence would during a
call: the owner's voice coming out while the room channel is sent to them. For each channel it
reports the room's quiet floor, the level while the voice plays, how far that sits above the floor,
and how strongly the channel still matches the voice (correlation at the echo's delay). On the
reSpeaker Flex, channel 0 is what telepresence sends; a raw capsule (channel 2), when the device
exposes it, shows the echo before cancelling. Saves the report (and with --save-audio the recording)
under local/telepresence/. Sound plays from Rex's speaker; nothing moves.
"""
from pathlib import Path
import argparse
import json
import sys
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

RATE = 16000
LEAD = 3.          # Seconds of quiet before the voice: the room's floor.
TAIL = 2.
MAX_DELAY = .5     # Seconds of echo delay searched.
FLEX_CHANNELS = {0: 'conference (AEC, beam, noise suppression, AGC): what telepresence sends',
                 1: 'ASR beam (AEC, beam, no AGC)', 2: 'raw capsule', 3: 'raw capsule', 4: 'raw capsule', 5: 'raw capsule'}


def speechlike(seconds, rng):
    """Pink-ish noise in syllable-length bursts (about four a second), with pauses between phrases."""
    import numpy as np
    n = int(seconds * RATE)
    white = rng.standard_normal(n)
    spectrum = np.fft.rfft(white)
    freqs = np.fft.rfftfreq(n, 1 / RATE)
    shape = 1 / np.sqrt(np.maximum(freqs, 100.))
    shape[freqs > 4000] *= np.exp(-(freqs[freqs > 4000] - 4000) / 800)
    noise = np.fft.irfft(spectrum * shape, n)
    t = np.arange(n) / RATE
    syllables = np.clip(np.sin(2 * np.pi * 4 * t + rng.uniform(0, 6)), 0, None) ** 2
    phrases = (np.sin(2 * np.pi * t / 2.5) > -.6).astype(float)
    return noise * syllables * phrases


def load_wav(path):
    import wave
    import numpy as np
    with wave.open(str(path), 'rb') as handle:
        if handle.getsampwidth() != 2:
            raise SystemExit('The WAV must be 16-bit PCM')
        data = np.frombuffer(handle.readframes(handle.getnframes()), dtype='<i2').astype(float)
        data = data.reshape(-1, handle.getnchannels()).mean(axis=1)
        rate = handle.getframerate()
    if rate != RATE:
        positions = np.arange(0, len(data) - 1, rate / RATE)
        data = np.interp(positions, np.arange(len(data)), data)
    return data


def db(values):
    import numpy as np
    rms = float(np.sqrt(np.mean(np.square(values)))) if len(values) else 0.
    return round(20 * np.log10(max(rms, 1e-9)), 1)


def analyze(reference, recording, voice_start, voice_seconds):
    """Per channel: quiet floor, level during the voice, its rise above the floor, and the best
    correlation with the played voice at an echo delay of 0-0.5 s. reference: what was played,
    full-length float in -1..1; recording: samples x channels, float in -1..1."""
    import numpy as np
    start, stop = int(voice_start * RATE), int((voice_start + voice_seconds) * RATE)
    played = reference[start:stop]
    result = {}
    for channel in range(recording.shape[1]):
        heard = recording[:, channel]
        floor = heard[int(.5 * RATE):start]
        best, delay = 0., None
        span = len(played)
        for lag in range(0, int(MAX_DELAY * RATE), 16):
            segment = heard[start + lag:start + lag + span]
            if len(segment) < span:
                break
            denominator = np.linalg.norm(segment) * np.linalg.norm(played)
            if denominator:
                r = abs(float(np.dot(segment, played)) / denominator)
                if r > best:
                    best, delay = r, lag
        during = heard[start + (delay or 0):stop + (delay or 0)]
        result[channel] = {'floor_dbfs': db(floor), 'during_voice_dbfs': db(during),
                           'above_floor_db': round(db(during) - db(floor), 1),
                           'voice_correlation': round(best, 3),
                           'echo_delay_ms': None if delay is None else round(1000 * delay / RATE, 1)}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--seconds', type=float, default=10., help='How long the test voice plays')
    parser.add_argument('--level', type=float, default=-20., help='Test voice level, dBFS RMS')
    parser.add_argument('--wav', type=Path, help='Play this 16-bit WAV of speech instead')
    parser.add_argument('--save-audio', action='store_true', help='Keep the recording (local/telepresence/)')
    args = parser.parse_args()
    import numpy as np
    import sounddevice as sd
    from r3x.adapters.audio import resolve_device
    from r3x.hardware_ownership import HardwareLocks
    from r3x.settings import ScratchPaths, Settings
    paths = ScratchPaths.for_project(PROJECT)
    settings_path = paths.root / 'settings.toml'
    settings = Settings.load(settings_path) if settings_path.exists() else Settings()
    locks = HardwareLocks()
    if locks.controller_running():
        raise SystemExit('Rex (or telepresence) is running and owns the microphone; stop it first')
    devices = sd.query_devices()
    source = resolve_device(devices, settings.input_device_name, settings.input_device_index, 'input')
    sink = resolve_device(devices, settings.output_device_name, None, 'output')
    channels = min(6, int(devices[source]['max_input_channels']))
    voice = load_wav(args.wav)[:int(args.seconds * RATE)] if args.wav else speechlike(args.seconds, np.random.default_rng(7))
    voice = voice / max(np.sqrt(np.mean(voice ** 2)), 1e-9) * 10 ** (args.level / 20)
    voice = np.clip(voice, -.99, .99)
    reference = np.concatenate([np.zeros(int(LEAD * RATE)), voice, np.zeros(int(TAIL * RATE))])
    intent = locks.lease('controller')
    if not intent.acquire():
        raise SystemExit('Another controller owns the robot')
    try:
        mic_lease = locks.lease('microphone')
        deadline = time.monotonic() + 8
        while not mic_lease.acquire():   # The supervisor releases its microphone when it sees the intent.
            if time.monotonic() > deadline:
                raise SystemExit('The supervisor did not release the microphone')
            time.sleep(.05)
        print(f'playing {len(voice) / RATE:.1f} s at {args.level:g} dBFS on {devices[sink]["name"]}, '
              f'recording {channels} channels of {devices[source]["name"]}...', flush=True)
        recording = sd.playrec((reference * 32767).astype('int16'), RATE, channels=channels, dtype='int16',
                               device=(source, sink), blocking=True)
        mic_lease.close()
    finally:
        intent.close()
    heard = recording.astype(float) / 32768
    flex = settings.microphone_mode == 'respeaker-flex'
    channels_report = analyze(reference, heard, LEAD, len(voice) / RATE)
    for channel, values in channels_report.items():
        values['name'] = FLEX_CHANNELS.get(channel, '') if flex else ('microphone' if channel == 0 else '')
    report = {'evidence': 'live devices on this Mac', 'input': devices[source]['name'], 'output': devices[sink]['name'],
              'microphone_mode': settings.microphone_mode, 'input_gain': settings.input_gain,
              'voice': str(args.wav) if args.wav else 'speech-like noise bursts', 'voice_dbfs': args.level,
              'seconds': round(len(voice) / RATE, 1), 'channels': channels_report,
              'note': 'Proposal, not a calibrated gate: channel 0 within about 6 dB of its floor and correlation '
                      'under 0.1 suggests full duplex will do; clearly more suggests half duplex.'}
    if flex and 2 in channels_report:
        report['cancelled_db_vs_raw_capsule'] = {c: round(channels_report[2]['above_floor_db'] - channels_report[c]['above_floor_db'], 1)
                                                for c in (0, 1)}
    out = paths.root / 'telepresence'
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    (out / f'echo-{stamp}.json').write_text(json.dumps(report, indent=2))
    if args.save_audio:
        import wave
        with wave.open(str(out / f'echo-{stamp}.wav'), 'wb') as handle:
            handle.setnchannels(channels); handle.setsampwidth(2); handle.setframerate(RATE)
            handle.writeframes(recording.astype('<i2').tobytes())
    print(json.dumps(report, indent=2))
    print(f'saved local/telepresence/echo-{stamp}.json')


if __name__ == '__main__':
    main()
