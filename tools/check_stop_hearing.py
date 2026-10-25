"""Explicit local check: how well Rex hears "stop" over his own drive noise, on saved robot audio.

Run: .venv/bin/python tools/check_stop_hearing.py --local-audio [--trials 6]

Owner, October 25: "I told you to stop many times, and you did not stop... this has been an ongoing
problem" (also October 16 and 19). The only robot-microphone audio kept is the September 27 capture
set under local/recordings (recording has been off since): three real stops (one standing still, two
during a drive), the owner's other utterances, the motor noise captured during those drives, and the
first 30 s of raw input from each run. Real stops are mixed into real drive noise at 0 to -24 dB
(farther away), and each listening arrangement is scored: the speech detector at its threshold, the
recognizer (Qwen3-ASR, no hint), and the owner's stop.onnx through openWakeWord. False halts are
counted on the other utterances in drive noise and on the noise alone. Real local models on saved
audio; no device, network or hosted call. Results go to local/validation/stop-hearing-*/.
Not a live measurement: the September noise, three stops and synthetic mixing stand in for a drive.
"""
import argparse
import glob
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from r3x.adapters.asr import QwenASR
from r3x.adapters.keywords import WORDS, KeywordSpotter
from r3x.contracts import Cancellation, Capture
from r3x.runtime import DRIVE_SPEECH_PROBABILITY
from r3x.session import is_stop_command, stop_heard_over
from r3x.settings import Settings


def read(path):
    import numpy as np
    with wave.open(str(path)) as w:
        if w.getframerate() != 16000 or w.getnchannels() != 1:
            raise ValueError('UnexpectedRecordingFormat')
        return np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768


def saved_audio():
    """Captures with their transcripts, marked by whether a drive was under way."""
    rows = []
    for folder in sorted(glob.glob(str(ROOT / 'local/recordings/*/'))):
        log = ROOT / 'local/sessions' / (os.path.basename(folder.rstrip('/')) + '.jsonl')
        if not log.exists():
            continue
        events = [json.loads(l) for l in open(log)
                  if any(k in l for k in ('"transcript"', '"action_started"', '"action_finished"'))]
        drives, began = [], None
        for e in events:
            if e['event'] == 'action_started' and e.get('kind') == 'drive_base': began = e['at']
            if e['event'] == 'action_finished' and e.get('kind') == 'drive_base' and began is not None:
                drives.append((began, e['at'])); began = None
        for e in events:
            wav = Path(folder) / (e.get('capture_id', '') + '.wav')
            if e['event'] == 'transcript' and wav.exists():
                rows.append({'text': e['original_text'], 'trusted': e['trusted'], 'wav': wav,
                             'drive': any(a <= e['at'] <= b + 1 for a, b in drives)})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-audio', action='store_true', help='Read the private saved recordings and load local models.')
    parser.add_argument('--trials', type=int, default=6)
    args = parser.parse_args()
    if not args.local_audio:
        parser.error('--local-audio is required; this check reads private recordings and loads local models')
    import numpy as np
    import torch
    from silero_vad import load_silero_vad
    torch.set_num_threads(1)
    vad = load_silero_vad()
    settings = Settings.load(ROOT / 'local/settings.toml')
    asr = QwenASR(ROOT / 'local/models/qwen3-asr', settings.asr_trust_logprob); asr.start()
    spotter = KeywordSpotter(ROOT / 'local/models/wake', {'stop': WORDS['stop']}); spotter.start()

    def detector(audio):
        vad.reset_states(); out = []
        for i in range(0, len(audio) - 511, 512):
            with torch.inference_mode():
                out.append(vad(torch.from_numpy(audio[i:i + 512].copy()), 16000).item())
        return max(out)

    timings = []
    def spot(audio):
        spotter.reset(); pcm = (np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes(); fired = False
        for i in range(0, len(pcm), 2 * 1280):
            began = time.perf_counter()
            fired = 'stop' in spotter.feed(pcm[i:i + 2 * 1280]) or fired
            timings.append(time.perf_counter() - began)
        return fired

    def judge(audio):
        peak = detector(audio)
        heard = None
        if peak >= DRIVE_SPEECH_PROBABILITY:
            pcm = (np.clip(audio, -1, 1) * 32767).astype('<i2').tobytes()
            heard = asr.transcribe(Capture('x', 's', pcm, 0., len(audio) / 16000, len(audio) / 16000), Cancellation())
        word = bool(heard and stop_heard_over(heard.text, ''))
        return {'detector': round(peak, 3), 'text': heard.text if heard else None, 'trusted': bool(heard and heard.trusted),
                'word': word, 'spotter': spot(audio)}

    def unsure(j):   # As VoiceRuntime._stop_peek: an unconfident lone "Whoa" does not halt.
        return not j['trusted'] and set(re.findall(r'[a-z]+', (j['text'] or '').lower())) <= {'whoa', 'oh'}
    heard = lambda j: j['detector'] >= DRIVE_SPEECH_PROBABILITY and j['word'] and not unsure(j)
    arms = {
        'before October 25 (detector .5, recognizer)': lambda j: j['detector'] >= settings.vad_threshold and j['word'],
        'detector %.2f while driving, recognizer, no unsure Whoa' % DRIVE_SPEECH_PROBABILITY: heard,
        'stop.onnx alone (%.2f x%d)' % WORDS['stop']: lambda j: j['spotter'],
        'all, as now': lambda j: heard(j) or j['spotter'],
    }
    rows = saved_audio()
    noise = [read(r['wav']) for r in rows if r['drive'] and not r['trusted']]
    if not noise:
        raise SystemExit('No saved drive noise under local/recordings')
    bed = np.concatenate(noise)
    stops = [r for r in rows if is_stop_command(r['text']) and r['drive']]
    others = [r for r in rows if r['trusted'] and not is_stop_command(r['text'])]
    rng = np.random.default_rng(5)

    def mixed(clip, db):
        start = rng.integers(16000, len(bed) - len(clip) - 16000)
        out = bed[start - 16000:start + len(clip) + 8000].copy()
        out[16000:16000 + len(clip)] += clip * 10 ** (db / 20)
        return out

    results = {'stops': [], 'speech': [], 'noise': []}
    for r in stops:
        clip = read(r['wav'])
        for db in (0, -6, -12, -18, -24):
            for _ in range(args.trials):
                results['stops'].append({**judge(mixed(clip, db)), 'db': db, 'said': r['text']})
    for r in others:
        clip = read(r['wav'])
        for db in (0, -12):
            results['speech'].append({**judge(mixed(clip, db)), 'db': db, 'said': r['text']})
    for audio in [bed, *(read(p) for p in glob.glob(str(ROOT / 'local/recordings/*/input-diagnostic.wav')))]:
        for i in range(16000, len(audio) - 31999, 32000):
            results['noise'].append(judge(audio[i - 16000:i + 32000]))
    summary = {'stops': len(results['stops']), 'other_speech': len(results['speech']),
               'noise_seconds': 3 * len(results['noise']), 'arms': {},
               'spotter_chunk_ms': {'median': round(1000 * float(np.median(timings)), 2),
                                    'p99': round(1000 * float(np.percentile(timings, 99)), 2)}}
    for name, rule in arms.items():
        by_db = {}
        for j in results['stops']:
            by_db.setdefault(j['db'], [0, 0]); by_db[j['db']][0] += rule(j); by_db[j['db']][1] += 1
        summary['arms'][name] = {'stops_heard': sum(rule(j) for j in results['stops']),
                                 'by_db': {db: f'{a}/{b}' for db, (a, b) in sorted(by_db.items(), reverse=True)},
                                 'false_halts_speech': sum(rule(j) for j in results['speech']),
                                 'false_halts_noise': sum(rule(j) for j in results['noise'])}
    folder = ROOT / 'local' / 'validation' / f'stop-hearing-{uuid4().hex[:8]}'
    folder.mkdir(parents=True)
    (folder / 'results.json').write_text(json.dumps({'summary': summary, **results}, indent=1, default=str))
    print(json.dumps(summary, indent=1))
    print(folder)
    spotter.close(); asr.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
