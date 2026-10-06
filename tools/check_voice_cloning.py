"""Real-model voice-cloning check: synthesize to WAV files, play nothing.

Loads the prepared Breeze snapshot through the production VoiceCloner, renders one
short line in each requested famous voice (default: Jimmy Carter and John Kennedy)
and writes the 22,050 Hz takes plus a JSON report under local/validation/voice-cloning/.
Measures load time, first-chunk and total synthesis time and peak memory. Metal GPU
work only; no microphone, speaker, hosted model or robot hardware.
"""
import argparse
import json
from pathlib import Path
import resource
import sys
import time
import wave

from r3x import impersonation
from r3x.adapters.breeze import VoiceCloner, OUTPUT_RATE
from r3x.contracts import Cancellation
from r3x.settings import ScratchPaths
from r3x.speech_level import PCMLeveler

LINE = "Well, I'd like to say it's an honor to be imitated by a droid, but I've met better pilots in a peanut field."


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('voices', nargs='*', default=['jimmy-carter', 'john-kennedy'])
    parser.add_argument('--text', default=LINE)
    args = parser.parse_args(argv)
    def offline(event, args):   # Any network attempt fails loudly: this check is offline.
        if event in {'socket.connect', 'socket.getaddrinfo'}:
            raise RuntimeError('NetworkBlockedInOfflineCheck')
    sys.addaudithook(offline)
    paths = ScratchPaths.for_project(Path(__file__).resolve().parents[1])
    paths.prepare()
    out = paths.root / 'validation' / 'voice-cloning'
    out.mkdir(parents=True, exist_ok=True)
    cloner = VoiceCloner(paths.root / 'models/breeze-tts-8bit', level=PCMLeveler)
    cloner.start()
    voices = impersonation.famous_voices()
    report = {'model': cloner.capability.model, 'text': args.text, 'takes': [], 'evidence': 'real model, synthesized to files, no playback'}
    for slug in args.voices:
        reference = voices[slug]
        began = time.monotonic()
        take = cloner.prepare(args.text, reference)
        pcm = b''.join(take.chunks(Cancellation()))
        wall = time.monotonic() - began
        path = out / f'{slug}.wav'
        with wave.open(str(path), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(OUTPUT_RATE)
            handle.writeframes(pcm)
        row = {'voice': reference.key, 'file': str(path), 'wall_seconds': round(wall, 2), **take.stats,
               'peak_rss_bytes_macos': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
        report['takes'].append(row)
        print(json.dumps(row))
    cloner.close()
    (out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({'report': str(out / 'report.json'), 'devices_opened': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
