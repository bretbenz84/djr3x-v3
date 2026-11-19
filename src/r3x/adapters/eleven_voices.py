"""ElevenLabs instant voice clones for "impersonate me" (owner, November 19).

The person who asks reads Rex's short "repeat after me" lines (about 30 voiced seconds,
gathered by the state owner); those clips are joined into one WAV and uploaded once as an
instant voice clone. The clone then speaks the parody script through ElevenLabs text to
speech. Famous voices and other people's voices stay on the local Breeze engine.

Owner rulings, November 19: the person asking is their consent (no consent or age step);
a clone is kept in its voice slot until the program shuts down, then deleted. Voices this
provider makes carry NAME_PREFIX, so a run that crashed is cleaned up at the next start, and
nothing else in the account (Rex's own voice, the accent voices) is ever deleted here.

Recordings stay in memory: nothing is written to disk. Takes are prepared completely before
playback, like Breeze's, so the speech sink plays them through the same ClonedPhrase path.
"""
import io
import threading
import time
import wave
from uuid import uuid4

from ..contracts import ProviderFailure
from ..providers import Capability

API = 'https://api.elevenlabs.io'
NAME_PREFIX = 'R3X impression '
MODEL = 'eleven_v4_turbo'    # Rex's own speech model; the clone speaks through the same one.
OUTPUT_RATE = 22050          # The speech sink's fixed stream rate.
MAX_TAKE_SECONDS = 40        # The same bound as Rex's own lines (ElevenLabsTTS).


def wav_bytes(samples, rate: int) -> bytes:
    """16-bit mono WAV of int16 samples (array('h')), little-endian on any host."""
    import sys
    if sys.byteorder != 'little':
        samples = samples[:]
        samples.byteswap()
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


class VoiceClones:
    """Owner of the clones Rex made this run: create from a recording, speak, delete at close."""
    def __init__(self, key: str, level=None, client=None):
        self.key = key
        self.level = level          # (sample_rate, text) -> PCM leveler; live composition supplies V2's.
        self.client = client        # Tests pass a fake; start() makes the real one.
        self._own_client = client is None
        self.trace = None
        self.lock = threading.Lock()
        self.voices = {}            # speaker key -> {'voice_id', 'created_at', 'used_at'}
        self.capability = Capability('voice_clones', 'ElevenLabs instant voice cloning',
            '"impersonate me" in the speaker\'s own cloned voice', MODEL, ('httpx',), ('network', 'ElevenLabs voice slots'),
            required=False, startup='delete voices left by an earlier run (name prefix only)',
            shutdown='delete every voice this run made')

    def _emit(self, event, **data):
        if self.trace:
            self.trace.emit(event, **data)

    def _headers(self):
        return {'xi-api-key': self.key}

    def start(self):
        if not self.key:
            raise ProviderFailure('ElevenLabsKeyMissing')
        if self.client is None:
            import httpx
            self.client = httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False)
        left = self._leftovers()
        for voice_id in left:
            self._delete(voice_id)
        self._emit('voice_clones_started', leftovers_deleted=len(left))

    def _leftovers(self):
        response = self.client.get(API + '/v1/voices', headers=self._headers())
        if response.status_code != 200:
            raise ProviderFailure(f'ElevenLabsVoicesHTTP{response.status_code}')
        return [v['voice_id'] for v in response.json().get('voices', ())
                if str(v.get('name', '')).startswith(NAME_PREFIX) and v.get('voice_id')]

    def cancel(self):
        pass   # Each take owns its stop flag.

    def close(self):
        with self.lock:
            voices, self.voices = self.voices, {}
        for key, row in voices.items():
            self._delete(row['voice_id'])
        if voices:
            self._emit('voice_clones_deleted', count=len(voices), reason='shutdown')
        if self.client is not None and self._own_client:
            self.client.close()
            self.client = None

    def has(self, key: str) -> bool:
        with self.lock:
            return key in self.voices

    def forget(self, key: str) -> None:
        """A forgotten person's clone goes at once, not at shutdown."""
        with self.lock:
            row = self.voices.pop(key, None)
        if row:
            threading.Thread(target=self._delete, args=(row['voice_id'],), name='r3x-voice-forget', daemon=True).start()
            self._emit('voice_clone_deleted', voice=key, reason='forgotten')

    def _delete(self, voice_id: str) -> bool:
        try:
            response = self.client.delete(API + '/v1/voices/' + voice_id, headers=self._headers())
        except Exception as exc:
            self._emit('voice_clone_delete_failed', error_type=type(exc).__name__)
            return False
        if response.status_code not in (200, 404):
            self._emit('voice_clone_delete_failed', status=response.status_code)
            return False
        return True

    def _create(self, key: str, audio: bytes) -> str:
        files = [('files', ('recording.wav', audio, 'audio/wav'))]
        data = {'name': NAME_PREFIX + uuid4().hex[:8],
                'description': 'Temporary DJ-R3X impression voice; deleted when the program shuts down.',
                'remove_background_noise': 'false'}
        response = self.client.post(API + '/v1/voices/add', headers=self._headers(), data=data, files=files)
        if response.status_code != 200:
            body = response.text[:400] if hasattr(response, 'text') else ''
            if 'voice_limit' in body or 'voice_add_edit_limit' in body:
                raise ProviderFailure('ElevenLabsVoiceSlotsFull')
            raise ProviderFailure(f'ElevenLabsCloneHTTP{response.status_code}')
        result = response.json()
        voice_id = result.get('voice_id')
        if not voice_id:
            raise ProviderFailure('ElevenLabsCloneMissingId')
        if result.get('requires_verification'):
            self._delete(voice_id)
            raise ProviderFailure('ElevenLabsCloneNeedsVerification')
        return voice_id

    def _make_room(self, key: str) -> bool:
        """Slots full: the clone used longest ago (another person's, from this run) gives way."""
        with self.lock:
            others = sorted(((row['used_at'], k) for k, row in self.voices.items() if k != key))
            if not others:
                return False
            row = self.voices.pop(others[0][1])
        self._delete(row['voice_id'])
        self._emit('voice_clone_deleted', voice=others[0][1], reason='slot_needed')
        return True

    def ensure(self, key: str, audio: bytes | None) -> str:
        with self.lock:
            row = self.voices.get(key)
        if row:
            return row['voice_id']
        if not audio:
            raise ProviderFailure('VoiceCloneRecordingMissing')
        began = time.monotonic()
        try:
            voice_id = self._create(key, audio)
        except ProviderFailure as exc:
            if str(exc) != 'ElevenLabsVoiceSlotsFull' or not self._make_room(key):
                raise
            voice_id = self._create(key, audio)
        now = time.monotonic()
        with self.lock:
            self.voices[key] = {'voice_id': voice_id, 'created_at': now, 'used_at': now}
        self._emit('voice_clone_created', voice=key, upload_bytes=len(audio), seconds=round(now - began, 2))
        return voice_id

    def _speak(self, voice_id: str, text: str, take) -> bytes:
        payload = {'text': text, 'model_id': MODEL, 'voice_settings': {'stability': 0.5}}
        response = self.client.post(API + '/v1/text-to-speech/' + voice_id, headers=self._headers(),
                                    params={'output_format': 'pcm_' + str(OUTPUT_RATE)}, json=payload)
        if take.stop.is_set():
            from ..contracts import Cancelled
            raise Cancelled()
        if response.status_code != 200:
            body = response.content[:400] if hasattr(response, 'content') else b''
            if response.status_code in (401, 402) and b'quota_exceeded' in body:
                raise ProviderFailure('ElevenLabsCreditExhausted')
            raise ProviderFailure(f'ElevenLabsHTTP{response.status_code}')
        pcm = response.content
        if not pcm or len(pcm) % 2:
            raise ProviderFailure('InvalidPCMResponse')
        if len(pcm) > OUTPUT_RATE * 2 * MAX_TAKE_SECONDS:
            raise ProviderFailure('SpeechDurationLimit')
        if self.level:
            leveler = self.level(OUTPUT_RATE, text)
            pcm = leveler.feed(pcm) + leveler.finish()
        return pcm

    def prepare(self, text: str, reference):
        """A take in the speaker's clone; the first take of a voice uploads its recording first.

        reference carries key, name and, for a new voice, the joined recording (audio)."""
        from .breeze import Take
        if self.client is None:
            raise ProviderFailure('VoiceClonesNotStarted')
        text = ' '.join((text or '').split())
        if not text:
            raise ProviderFailure('EmptyImpressionScript')

        def render(take):
            began = time.monotonic()
            new = not self.has(reference.key)
            voice_id = self.ensure(reference.key, getattr(reference, 'audio', None))
            cloned = time.monotonic()
            if take.stop.is_set():
                from ..contracts import Cancelled
                raise Cancelled()
            pcm = self._speak(voice_id, text, take)
            with self.lock:
                if reference.key in self.voices:
                    self.voices[reference.key]['used_at'] = time.monotonic()
            stats = {'engine': 'elevenlabs', 'new_voice': new, 'clone_seconds': round(cloned - began, 2),
                     'synthesis_seconds': round(time.monotonic() - cloned, 2),
                     'audio_seconds': round(len(pcm) / (2 * OUTPUT_RATE), 2), 'words': len(text.split())}
            self._emit('impression_prepared', voice=reference.key, **stats)
            return pcm, stats
        return Take(text, reference, render)
