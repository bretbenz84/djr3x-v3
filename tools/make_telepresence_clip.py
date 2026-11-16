"""Make the line Rex says at home when a telepresence call starts (owner, November 16).

    .venv/bin/python tools/make_telepresence_clip.py [--text "..."]

Synthesizes the line once in Rex's own ElevenLabs voice, leveled like his ordinary speech, converts it
to the call's 16 kHz mono and writes assets/audio/telepresence/call_connected.wav (kept in the
repository, owner request) with its provenance in call_connected.json beside it. One paid ElevenLabs
request, with the key from local/secrets.toml; nothing else is sent. The call program plays the clip
as it starts, before it takes the call; regenerate here to change the words.
"""
from datetime import date
from pathlib import Path
import argparse
import json
import sys
import wave

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from r3x.telepresence.program import CALL_CLIP  # noqa: E402
from r3x.telepresence.protocol import AUDIO_RATE  # noqa: E402

TEXT = 'Heads up! Telepresence link established. Bret is calling in through me.'


def to_call_rate(pcm: bytes, rate: int) -> bytes:
    """16-bit mono PCM at rate, resampled to the call's AUDIO_RATE."""
    import numpy as np
    from math import gcd
    from scipy.signal import resample_poly
    samples = np.frombuffer(pcm, dtype='<i2').astype(np.float64)
    step = gcd(AUDIO_RATE, rate)
    out = resample_poly(samples, AUDIO_RATE // step, rate // step)
    return np.clip(np.round(out), -32768, 32767).astype('<i2').tobytes()


def write_clip(path: Path, pcm: bytes, provenance: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(AUDIO_RATE)
        clip.writeframes(pcm)
    path.with_suffix('.json').write_text(json.dumps(
        {**provenance, 'sample_rate': AUDIO_RATE, 'seconds': round(len(pcm) / 2 / AUDIO_RATE, 2)}, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--text', default=TEXT, help='What he says (default: %(default)r)')
    args = parser.parse_args()
    from r3x.adapters.hosted import ElevenLabsTTS
    from r3x.contracts import Cancellation
    from r3x.settings import ScratchPaths, Secrets
    from r3x.speech_level import PCMLeveler
    from r3x.voice_modes import REX_VOICE
    key = Secrets.load(ScratchPaths.for_project(PROJECT).root).elevenlabs_api_key
    if not key:
        raise SystemExit('Set elevenlabs_api_key in local/secrets.toml')
    tts = ElevenLabsTTS(key, REX_VOICE)
    tts.level = PCMLeveler   # Leveled as his live lines are.
    tts.start()
    try:
        pcm = b''.join(tts.synthesize_own(args.text, Cancellation()))
    finally:
        tts.close()
    clip = to_call_rate(pcm, tts.sample_rate)
    write_clip(CALL_CLIP, clip, {'text': args.text, 'source': 'ElevenLabs text-to-speech, Rex\'s own voice',
                                 'voice_id': REX_VOICE, 'model': tts.capability.model,
                                 'leveled': 'speech_level.PCMLeveler', 'made': date.today().isoformat(),
                                 'made_by': 'tools/make_telepresence_clip.py'})
    print(f'wrote {CALL_CLIP.relative_to(PROJECT)} ({len(clip) / 2 / AUDIO_RATE:.1f} s)')


if __name__ == '__main__':
    main()
