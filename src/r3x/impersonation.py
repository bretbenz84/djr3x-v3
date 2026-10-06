"""Voice impressions: who Rex can imitate, and the reference clip for each voice.

Ported from V2 features/impersonation.py (HEAD fb96f08) with its lessons kept and its
machinery replaced by V3's owners:

- The conversation model writes the parody itself inside the `impersonate` tool call
  (one generation, no separate script call) and decides, from context, whether an
  impression was asked for or is a rare surprise cameo.
- Famous voices are tracked public clips under assets/voices/famous. A known person's
  voice comes from the accepted enrollment recordings the core already keeps; a
  stranger's comes from the clean samples of their own current voice track. Nothing
  is captured specially for impressions and nothing enrolls from them.
- Derived reference WAVs live in ignored local/cache/voice_refs. Person references are
  registered store artifacts so forgetting a person removes them; a stranger's
  reference lasts for the session only.
- Generated parody is performance, never biography: it is recorded as an impression
  with its subject, never as a claim.
"""
from dataclasses import dataclass
from collections import deque
import hashlib
import json
from pathlib import Path
import shutil
import wave

FAMOUS_DIR = Path(__file__).resolve().parents[2] / 'assets' / 'voices' / 'famous'

MIN_VOICED_SECONDS = 6.      # Speech needed before a voice can be cloned (V2 capture floor).
MIN_CLIP_VOICED_SECONDS = 2. # Shorter accepted clips carry too little voice.
MAX_REFERENCE_SECONDS = 20.  # Rex's own V2 reference was 19.5 s; longer buys nothing.
CLIP_GAP_SECONDS = .3
TAIL_PAD_SECONDS = .5
# Script caps at sentence boundaries. V2 allowed 45 words; on the base M2 the engine runs at
# about 1.5 times real time, so 40 words (about 13 s of audio) is roughly 20 s of preparation
# and a cameo, which has no filler to cover it, stays shorter.
MAX_SCRIPT_WORDS = 40
CAMEO_MAX_SCRIPT_WORDS = 28
# Surprise cameos stay rare (V2 organic impressions): one every ten minutes at most, the same
# voice once an hour, four per run. Requested impressions are never limited.
SPONTANEOUS_MIN_GAP_SECONDS = 600.
SPONTANEOUS_VOICE_GAP_SECONDS = 3600.
SPONTANEOUS_MAX_PER_SESSION = 4


@dataclass(frozen=True)
class VoiceReference:
    wav_path: str   # Absolute path; the engine resamples by path (an array would not be resampled).
    text: str       # Exact transcript of the clip, whitespace-normalized.
    key: str        # famous:<slug> | person:<id> | track:<id>
    name: str       # Display name for traces and history notes.


def famous_voices(root: Path = FAMOUS_DIR) -> dict:
    """slug -> VoiceReference for every clip with a transcript; names from index.json."""
    try:
        index = json.loads((root / 'index.json').read_text())
    except (OSError, ValueError):
        index = {}
    voices = {}
    for wav in sorted(root.glob('*.wav')) if root.is_dir() else ():
        if wav.is_symlink():
            continue
        txt = wav.with_suffix('.txt')
        try:
            text = ' '.join(txt.read_text(encoding='utf-8').split())
        except OSError:
            continue
        if not text:
            continue
        entry = index.get(wav.stem, {})
        name = entry.get('name') or ' '.join(part.capitalize() for part in wav.stem.split('-'))
        voices[wav.stem] = VoiceReference(str(wav.resolve()), text, 'famous:' + wav.stem, name)
    return voices


def cap_script(text: str, max_words: int = MAX_SCRIPT_WORDS) -> str:
    """Bound the parody at a sentence boundary; a single long sentence is kept whole."""
    import re
    kept, count = [], 0
    for sentence in re.split(r'(?<=[.!?…])\s+', ' '.join((text or '').split())):
        words = len(sentence.split())
        if kept and count + words > max_words:
            break
        kept.append(sentence)
        count += words
    return ' '.join(kept).strip()


def select_clips(clips):
    """Choose accepted clips (pcm bytes, voiced seconds, transcript) for one reference.

    Keeps V2's rule: each clip at least two voiced seconds, stop once six voiced seconds
    are gathered, never more than twenty seconds of audio. Returns the chosen clips and
    their voiced total; a total under the floor means the voice is not ready.
    """
    chosen, voiced, duration = [], 0., 0.
    for pcm, seconds, text, sample_rate in clips:
        length = len(pcm) / (2 * sample_rate)
        if seconds < MIN_CLIP_VOICED_SECONDS or seconds > length + .1 or not text.strip():
            continue
        if duration + length > MAX_REFERENCE_SECONDS:
            continue
        chosen.append((pcm, seconds, ' '.join(text.split()), sample_rate))
        voiced += seconds
        duration += length
        if voiced >= MIN_VOICED_SECONDS:
            break
    return chosen, voiced


def trim_silence(samples, sample_rate: int):
    """Cut room tone off both ends (30 ms frames, 10% of peak RMS floor, 150 ms margin).

    Pure Python over 16-bit samples: the state owner must not load native libraries,
    and a reference is at most twenty seconds long.
    """
    import math
    frame = max(1, int(.03 * sample_rate))
    count = len(samples) // frame
    if count <= 0:
        return samples
    rms = [math.sqrt(sum(v * v for v in samples[i * frame:(i + 1) * frame]) / frame) / 32768. for i in range(count)]
    peak = max(rms)
    if peak <= 0.:
        return samples
    floor = max(.004, .1 * peak)
    voiced = [i for i, value in enumerate(rms) if value >= floor]
    if not voiced:
        return samples
    margin = int(.15 * sample_rate)
    return samples[max(0, voiced[0] * frame - margin):min(len(samples), (voiced[-1] + 1) * frame + margin)]


def reference_audio(chosen):
    """Join trimmed clips with short gaps and a padded tail; (int16 samples, rate, transcript)."""
    from array import array
    import sys
    rate = chosen[0][3]
    joined = array('h')
    for index, (pcm, _, _, sample_rate) in enumerate(chosen):
        if sample_rate != rate:
            raise ValueError('MixedReferenceSampleRates')
        if index:
            joined.extend(array('h', bytes(2 * int(CLIP_GAP_SECONDS * rate))))
        samples = array('h', pcm[:len(pcm) - len(pcm) % 2])
        if sys.byteorder != 'little':
            samples.byteswap()
        joined.extend(trim_silence(samples, rate))
    trailing = 0
    for value in reversed(joined):
        if abs(value) > 32:   # About -60 dBFS.
            break
        trailing += 1
    shortfall = int(TAIL_PAD_SECONDS * rate) - trailing
    if shortfall > 0:
        joined.extend(array('h', bytes(2 * shortfall)))
    return joined, rate, ' '.join(text for _, _, text, _ in chosen)


def write_reference(path: Path, samples, rate: int, text: str) -> None:
    import sys
    if sys.byteorder != 'little':
        samples = samples[:]
        samples.byteswap()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix('.tmp')
    with wave.open(str(temporary), 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    temporary.replace(path)
    path.with_suffix('.txt').write_text(text, encoding='utf-8')


def recording_clips(store, person_id):
    rows = store.rows('SELECT pcm,sample_rate,metadata FROM recordings WHERE person_id=? ORDER BY rowid', (person_id,))
    clips = []
    for row in rows:
        meta = json.loads(row['metadata'])
        clips.append((row['pcm'], float(meta.get('voiced_seconds') or 0.), str(meta.get('transcript') or ''), int(row['sample_rate'])))
    return clips


def sample_clips(samples):
    """Clean conversational samples of one unnamed track (Enrollment.samples rows)."""
    return [(capture.pcm, float(observation.voiced_seconds), transcript.text, capture.sample_rate)
            for capture, transcript, observation, _ in samples]


class ReferenceStore:
    """Derived reference clips under local/cache/voice_refs; never a second voice archive."""
    def __init__(self, root: Path):
        self.root = Path(root)
        self._session_dirs = set()

    def person_reference(self, store, conn, person_id: str, name: str):
        """One reference per person, rebuilt when their accepted recordings change."""
        chosen, voiced = select_clips(recording_clips(store, person_id))
        if voiced < MIN_VOICED_SECONDS:
            return None
        digest = hashlib.sha256(b''.join(hashlib.sha256(pcm).digest() for pcm, *_ in chosen)).hexdigest()[:16]
        path = self.root / f'person-{person_id}-{digest}.wav'
        if path.resolve() != path.absolute():
            raise ValueError('LinkedVoiceReference')
        if not path.is_file() or not path.with_suffix('.txt').is_file():
            samples, rate, text = reference_audio(chosen)
            write_reference(path, samples, rate, text)
            # Older derived copies of this voice go with their artifact rows.
            for stale in self.root.glob(f'person-{person_id}-*'):
                if stale.stem != path.stem:
                    conn.execute('DELETE FROM artifacts WHERE path=?', (str(stale),))
                    stale.unlink(missing_ok=True)
            store.register_artifact(conn, path, person_id)
            store.register_artifact(conn, path.with_suffix('.txt'), person_id)
        text = ' '.join(path.with_suffix('.txt').read_text(encoding='utf-8').split())
        return VoiceReference(str(path), text, 'person:' + person_id, name)

    def track_reference(self, samples, track_id: str, session_id: str, name: str):
        """A stranger's own voice, from this session's clean samples; removed at close()."""
        chosen, voiced = select_clips(sample_clips(samples))
        if voiced < MIN_VOICED_SECONDS:
            return None
        directory = self.root / ('session-' + session_id)
        self._session_dirs.add(directory)
        digest = hashlib.sha256(b''.join(hashlib.sha256(pcm).digest() for pcm, *_ in chosen)).hexdigest()[:16]
        path = directory / f'track-{track_id}-{digest}.wav'
        if not path.is_file() or not path.with_suffix('.txt').is_file():
            audio, rate, text = reference_audio(chosen)
            write_reference(path, audio, rate, text)
        text = ' '.join(path.with_suffix('.txt').read_text(encoding='utf-8').split())
        return VoiceReference(str(path), text, 'track:' + track_id, name)

    def forget_track(self, track_id: str) -> None:
        for directory in self._session_dirs:
            for path in directory.glob(f'track-{track_id}-*'):
                path.unlink(missing_ok=True)

    def close(self) -> None:
        for directory in self._session_dirs:
            shutil.rmtree(directory, ignore_errors=True)
        self._session_dirs.clear()


class Impressions:
    """What Rex actually performed this run, for variety and for the cameo budget."""
    def __init__(self):
        self.performed = deque(maxlen=16)

    def record(self, voice: str, subject: str, script: str, requested: bool, at: float) -> None:
        self.performed.append({'voice': voice, 'subject': subject, 'script': script,
                               'requested': requested, 'at': at})

    def spontaneous_allowed(self, now: float, voice: str | None = None):
        """(allowed, reason): the V2 cameo budget, independent of requested impressions."""
        cameos = [r for r in self.performed if not r['requested']]
        if len(cameos) >= SPONTANEOUS_MAX_PER_SESSION:
            return False, 'session_cameo_limit'
        if cameos and now - cameos[-1]['at'] < SPONTANEOUS_MIN_GAP_SECONDS:
            return False, 'recent_cameo'
        if voice and any(r['voice'] == voice and now - r['at'] < SPONTANEOUS_VOICE_GAP_SECONDS for r in cameos):
            return False, 'same_voice_recently'
        return True, None

    def recent(self, limit: int = 4):
        return [{'voice': r['voice'], 'script': r['script']} for r in list(self.performed)[-limit:]]


def speaker_readiness(state):
    """How much clean voice the current speaker has on file: supported recordings, or
    the clean samples of their own unnamed track. (ready, voiced seconds, basis)."""
    if state.current is None:
        return False, 0., 'no_speech'
    attribution = state.current[4]
    if attribution.person_id:
        person = state.store.person(attribution.person_id)
        if person and person['status'] == 'supported':
            _, voiced = select_clips(recording_clips(state.store, attribution.person_id))
            return voiced >= MIN_VOICED_SECONDS, round(voiced, 1), 'accepted_recordings'
    proposal = state.evidence.get(attribution.track_id) if attribution.track_id else None
    if proposal is None or attribution.reason == 'tentative_continuity':
        return False, 0., 'no_clean_track_samples'
    _, voiced = select_clips(sample_clips(proposal.samples))
    return voiced >= MIN_VOICED_SECONDS, round(voiced, 1), 'this_voice_track'


def context(state, now: float) -> dict:
    """The impersonation block of the conversation context; offered only with a cloner."""
    ready, voiced, basis = speaker_readiness(state)
    attribution = state.current[4] if state.current else None
    speaker_name = None
    if attribution and attribution.person_id:
        person = state.store.person(attribution.person_id)
        speaker_name = person['name'] if person else None
    people = []
    if attribution is not None:
        for person in state.store.people():
            if person['status'] != 'supported' or person['id'] == attribution.person_id:
                continue
            _, seconds = select_clips(recording_clips(state.store, person['id']))
            if seconds >= MIN_VOICED_SECONDS:
                people.append({'target_id': person['id'], 'name': person['name']})
    allowed, reason = state.impressions.spontaneous_allowed(now)
    return {'available': True,
            'speaker': {'target_id': 'self', 'ready': ready, 'name': speaker_name,
                        'voiced_seconds_on_file': voiced, 'required_voiced_seconds': MIN_VOICED_SECONDS,
                        'basis': basis},
            'people': people,
            'famous': sorted(famous_voices()),
            'spontaneous_allowed': allowed, 'spontaneous_blocked_reason': reason,
            'recent': state.impressions.recent(),
            'meaning': 'Voices Rex can perform through the local cloning engine. self is the current speaker and '
                       'is usable only when ready is true (otherwise Rex needs to hear more of them in ordinary '
                       'conversation first). people are known voices on file; famous are target ids. Parody is '
                       'never a fact about anyone.'}
