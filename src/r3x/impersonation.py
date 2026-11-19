"""Voice impressions: who Rex can imitate, and the reference clip for each voice.

Ported from V2 features/impersonation.py (HEAD fb96f08) with its lessons kept and its
machinery replaced by V3's owners:

- The conversation model writes the parody itself inside the `impersonate` tool call
  (one generation, no separate script call) and decides, from context, whether an
  impression was asked for or is a rare surprise cameo.
- Famous voices are tracked public clips under assets/voices/famous. A known person's
  voice comes from the accepted enrollment recordings the core already keeps. Both play
  through the local engine (Breeze); nothing enrolls from them.
- "Impersonate me" (owner, November 19) is the speaker's own ElevenLabs clone, made from
  about 30 voiced seconds of short "repeat after me" lines (VoiceSitting), kept in memory,
  joined into one upload and kept in its voice slot until shutdown.
- Derived reference WAVs live in ignored local/cache/voice_refs. Person references are
  registered store artifacts so forgetting a person removes them.
- Generated parody is performance, never biography: it is recorded as an impression
  with its subject, never as a claim.
"""
from .field_guide import GUIDE
from dataclasses import dataclass
from collections import deque
import hashlib
import json
from pathlib import Path
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
# "Impersonate me" (owner, November 19): the speaker's own voice is an ElevenLabs clone made from
# about 15 voiced seconds of short "repeat after me" lines, joined into one upload. The clone
# stays for the run; famous voices and other people's stay on the local engine. Owner, November 19
# evening: half the lines (30 s of fourteen lines was cumbersome), and a known person's joined
# recording is kept in the local store so later runs need no lines at all (CLONE_RECORDINGS).
SITTING_SECONDS = 15.          # Voiced seconds recorded, then the clone is made (about five lines).
SITTING_MIN_SECONDS = 10.      # The floor for lines that run out first.
SITTING_LINES = (4, 8)         # How many lines the model writes (seven covers a fast talker).

SITTING_LINE_WORDS = 10        # Short enough to remember after one hearing.
SITTING_WAIT_SECONDS = 60.     # A line nobody repeats in this long lets the recording go.
OWN_REPEAT_SHARE = .4          # Their own voice: this share of the line's words counts as a repeat.
UNSURE_REPEAT_SHARE = .7       # A voice the tracker did not tie to them must repeat it closely.


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


class ReferenceStore:
    """Derived reference clips under local/cache/voice_refs; never a second voice archive."""
    def __init__(self, root: Path):
        self.root = Path(root)

    def person_reference(self, store, conn, person_id: str, name: str):
        """One reference per person, rebuilt when their accepted recordings change. (The speaker's
        own voice is an ElevenLabs clone instead, from a recording made for it: VoiceSitting.)"""
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

@dataclass(frozen=True)
class CloneReference:
    """The speaker's ElevenLabs clone: their key, a display name and, for a new voice, the recording."""
    key: str              # person:<id> | track:<id>
    name: str
    audio: bytes | None = None


@dataclass
class VoiceSitting:
    """One "repeat after me" recording for an impression of the speaker (owner, November 19).

    Held by the state owner. Only clips Rex heard them repeat count; nothing is written to disk
    and nothing here is identity evidence: the recording only ever becomes their clone."""
    key: str
    name: str
    person_id: str | None
    track_id: str | None
    lines: list
    impression: dict            # The performance waiting for the clone (script, outro, ...).
    index: int = 0              # The line being waited for.
    clips: list = None          # (pcm, voiced seconds, transcript, sample rate)
    voiced: float = 0.
    prompted_at: float | None = None   # When the awaited line was heard; None until it was.

    def __post_init__(self):
        self.clips = [] if self.clips is None else self.clips

    def view(self) -> dict:
        return {'active': True, 'awaiting_line': self.lines[self.index] if self.prompted_at is not None else None,
                'lines_repeated': len(self.clips), 'lines_total': len(self.lines),
                'voiced_seconds': round(self.voiced, 1), 'needed_seconds': SITTING_SECONDS}


def sitting_lines(lines) -> list:
    """The model's "repeat after me" lines, checked for shape only; their wording is the model's."""
    from .output import plain_text
    if not isinstance(lines, list) or not SITTING_LINES[0] <= len(lines) <= SITTING_LINES[1]:
        raise ValueError('InvalidRecordingLines')
    kept = []
    for line in lines:
        text = ' '.join(plain_text(line).split()) if isinstance(line, str) else ''
        if not text or len(text.split()) > SITTING_LINE_WORDS:
            raise ValueError('InvalidRecordingLines')
        kept.append(text)
    return kept


def repeated_share(heard: str, line: str) -> float:
    """How much of the line they said back: the share of its words found in what was heard."""
    import re
    from collections import Counter
    said = Counter(re.findall(r"[a-z0-9']+", (heard or '').lower()))
    wanted = re.findall(r"[a-z0-9']+", (line or '').lower())
    if not wanted:
        return 0.
    found = 0
    for word in wanted:
        if said[word]:
            said[word] -= 1
            found += 1
    return found / len(wanted)


def sitting_key(state, attribution):
    """Whose clone this would be: a known person's for the run, else this voice track's."""
    if attribution is None:
        return None
    if attribution.person_id:
        person = state.store.person(attribution.person_id)
        if person and person['status'] == 'supported':
            return 'person:' + attribution.person_id
    if attribution.track_id and attribution.reason != 'tentative_continuity':
        return 'track:' + attribution.track_id
    return None


CLONE_RECORDINGS = """
CREATE TABLE IF NOT EXISTS clone_recordings(
 person_id TEXT PRIMARY KEY REFERENCES people(id) ON DELETE CASCADE,
 audio BLOB NOT NULL,voiced_seconds REAL NOT NULL,lines INTEGER NOT NULL,recorded_at REAL NOT NULL);
"""


def saved_recording(store, person_id):
    """A known person's joined "repeat after me" recording from an earlier run, or None."""
    if not person_id:
        return None
    rows = store.rows('SELECT audio FROM clone_recordings WHERE person_id=?', (person_id,))
    return bytes(rows[0]['audio']) if rows else None


def save_recording(store, person_id, audio, voiced_seconds, lines, at):
    """Kept in the local store (never committed) until they record again or are forgotten."""
    with store.transaction() as conn:
        conn.execute('INSERT OR REPLACE INTO clone_recordings VALUES(?,?,?,?,?)',
                     (person_id, audio, float(voiced_seconds), int(lines), float(at)))


def drop_recording(store, person_id):
    with store.transaction() as conn:
        conn.execute('DELETE FROM clone_recordings WHERE person_id=?', (person_id,))


def recording_audio(clips) -> bytes:
    """The repeated lines joined into one WAV (silence trimmed, short gaps) for the upload."""
    from .adapters.eleven_voices import wav_bytes
    samples, rate, _ = reference_audio(clips)
    return wav_bytes(samples, rate)


class Impressions:
    """What Rex actually performed this run, for variety and for the cameo budget.

    A cameo in a reply and an impression beat of his own (October 23) are both unrequested and
    share the one budget."""
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

    def wait_seconds(self, now: float):
        """Seconds until the next unrequested impression may run: 0 now, None for no more this run."""
        cameos = [r for r in self.performed if not r['requested']]
        if len(cameos) >= SPONTANEOUS_MAX_PER_SESSION:
            return None
        return max(0, round(cameos[-1]['at'] + SPONTANEOUS_MIN_GAP_SECONDS - now)) if cameos else 0

    def recent(self, limit: int = 4):
        return [{'voice': r['voice'], 'script': r['script']} for r in list(self.performed)[-limit:]]


def people_with_voices(store):
    """(person id, name) for every supported person with enough accepted voice to clone."""
    rows = []
    for person in store.people():
        if person['status'] != 'supported':
            continue
        _, seconds = select_clips(recording_clips(store, person['id']))
        if seconds >= MIN_VOICED_SECONDS:
            rows.append((person['id'], person['name']))
    return rows


def planner_voices(state, now: float, here=(), private: bool = False) -> dict:
    """What the goal planner may build an impression beat on (October 23, owner: V2 riffed in a
    president's voice about his library trip; "with other people's voices too, and my own").

    People's voices are listed only for a private plan (someone Rex knows is here): a known
    person's voice could carry their talk to whoever else is listening."""
    allowed, reason = state.impressions.spontaneous_allowed(now)
    return {'available_now': allowed, 'blocked_reason': reason,
            'next_in_seconds': state.impressions.wait_seconds(now),
            'famous': [{'voice': slug, 'name': reference.name} for slug, reference in sorted(famous_voices().items())],
            'people': [{'voice': person_id, 'name': name, 'here': person_id in here}
                       for person_id, name in (people_with_voices(state.store) if private else ())],
            'recent': state.impressions.recent(),
            'meaning': ('Voices Rex can perform in an impression beat. available_now false or next_in_seconds over a '
                        'few minutes: plan none. people.here marks who is in the room; parody is never a fact.')}


def beat_context(state, now: float, scope: str, here=()):
    """The voices an impression beat may use right now, or None when it cannot run.

    Nobody asked, so the cameo budget applies, voice by voice. A beat has no current speaker,
    so there is no 'self': the person here is offered by their own id. A general-scope beat
    gets famous voices only. The famous list stays whole, as on a person's turn, so the hosted
    request's static block (and its cached prefix) is the same; the state owner refuses a
    famous voice used within the hour."""
    if not state.impressions.spontaneous_allowed(now)[0]:
        return None
    famous = sorted(famous_voices())
    people = [{'target_id': person_id, 'name': name, 'here': person_id in here}
              for person_id, name in (people_with_voices(state.store) if scope == 'audience' else ())
              if state.impressions.spontaneous_allowed(now, 'person:' + person_id)[0]]
    if not famous and not people:
        return None
    return {'available': True, 'beat': True, 'people': people, 'famous': famous,
            'spontaneous_allowed': True, 'spontaneous_blocked_reason': None,
            'recent': state.impressions.recent(), 'meaning': GUIDE['impersonation']}


def context(state, now: float) -> dict:
    """The impersonation block of the conversation context. Famous voices and people's voices
    come with the local engine (voice_references); the speaker's own, with ElevenLabs clones."""
    attribution = state.current[4] if state.current else None
    local = state.voice_references is not None
    clones = state.voice_clones
    key = sitting_key(state, attribution) if clones is not None else None
    speaker_name = None
    if attribution and attribution.person_id:
        person = state.store.person(attribution.person_id)
        speaker_name = person['name'] if person else None
    ready = bool(key and (clones.has(key) or (key.startswith('person:')
                                              and saved_recording(state.store, key.split(':', 1)[1]) is not None)))
    speaker = {'target_id': 'self', 'name': speaker_name, 'ready': ready,
               'recording_needed': bool(key) and not ready,
               'available': bool(key),
               'record_seconds': SITTING_SECONDS, 'lines': list(SITTING_LINES), 'line_max_words': SITTING_LINE_WORDS}
    people = []
    if attribution is not None and local:
        for person in state.store.people():
            if person['status'] != 'supported' or person['id'] == attribution.person_id:
                continue
            _, seconds = select_clips(recording_clips(state.store, person['id']))
            if seconds >= MIN_VOICED_SECONDS:
                people.append({'target_id': person['id'], 'name': person['name']})
    allowed, reason = state.impressions.spontaneous_allowed(now)
    block = {'available': True, 'speaker': speaker, 'people': people,
             'famous': sorted(famous_voices()) if local else [],
             'spontaneous_allowed': allowed, 'spontaneous_blocked_reason': reason,
             'recent': state.impressions.recent(),
             'meaning': GUIDE['impersonation']}
    if state.sitting is not None:
        block['recording'] = state.sitting.view()
    return block
