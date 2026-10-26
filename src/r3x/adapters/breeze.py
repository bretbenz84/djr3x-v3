"""On-device voice cloning for impressions: Breeze TTS 2 (8-bit, mlx-audio 0.5.1).

Ported from V2 audio/local_tts.py (HEAD fb96f08) as a V3 provider with one job: turn a
parody script plus a reference clip into one complete, leveled 22,050 Hz PCM take for
the speech sink. Rex's own voice stays ElevenLabs; this engine never speaks on its own
and never replaces that voice.

Lifecycle: start() verifies the prepared snapshot and builds the text tokenizer (see
text_tokenizer), without loading the model. The model loads
on the first impression (behind Rex's intro line and the thinking filler) and stays
resident until close (owner, October 26: keeping it loaded is fine; the ten-minute idle
release is retired, and with it a 0.1 s interpreter hold mid-run). Every Metal
step takes the shared MLX lock (see mlx_schedule) so transcription and cloning never
evaluate concurrently. Takes are prepared completely before playback (V2's impression
policy): a cloned take is never streamed into the room while generation might still
fall behind, and an underrun never discards remaining audio.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import threading
import time

from ..contracts import Cancellation, Cancelled, ProviderFailure
from ..providers import Capability
from ..mlx_schedule import MLX_LOCK

MODEL_ID = 'mlx-community/Breeze-TTS-2-mlx-8bit'
MODEL_REVISION = 'c6e4a2ff6ab9afba68b7853de802273ffe23fb49'
MODEL_RATE = 24000
OUTPUT_RATE = 22050     # The speech sink's fixed stream rate.
# Files the engine needs; sizes from the pinned snapshot (checked at start, digests by the prepare tool).
REQUIRED_FILES = ('config.json', 'tokenizer.json', 'tokenizer_config.json', 'model.safetensors',
                  'audio_tokenizer/config.json', 'audio_tokenizer/model.safetensors')
WEIGHT_BYTES = {'model.safetensors': 3885721536, 'audio_tokenizer/model.safetensors': 682293092}
WEIGHT_SHA256 = {'model.safetensors': 'd7a82315bcc59c3057f13bb85100743c7cd40692e38145fb255d6c1f0bf447bc',
                 'audio_tokenizer/model.safetensors': '836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258'}
PREPARE_TIMEOUT_SECONDS = 60.   # V2 waited 45 s; the base M2 renders at about 1.5x real time plus load.
# V2's bench settings: steadier sampling, early codec-loop penalty, bounded length.
TEMPERATURE = .7
REPETITION_PENALTY = 1.2
MAX_TOKENS = 750                # 60 s of audio at 12.5 frames/s; the absolute cap.
DURATION_SLACK = 2.             # Word-count duration estimate times this bounds runaway takes.
STREAMING_INTERVAL = .25
PIECE_BYTES = 4410              # 0.1 s of output PCM per yielded piece.


def convert_pcm(samples, text: str, level=None) -> bytes:
    """24 kHz float32 from the model -> trimmed, resampled, leveled 22,050 Hz int16 bytes."""
    import numpy as np
    from scipy.signal import resample_poly
    audio = np.asarray(samples, dtype=np.float32).reshape(-1)
    audio = trim_edges(audio, MODEL_RATE)
    if not audio.size:
        return b''
    audio = resample_poly(audio, 147, 160).astype(np.float32)   # 24000 -> 22050
    pcm = np.clip(audio * 32767., -32768, 32767).astype('<i2').tobytes()
    if level is not None:
        leveler = level(OUTPUT_RATE, text)
        pcm = leveler.feed(pcm) + leveler.finish()
    return pcm


def trim_edges(audio, rate: int, threshold: float = .004, window_ms: float = 20., pad_ms: float = 120.):
    """V2 trim_unit_silence: cut synthesized silence off both ends, keeping onsets/decays."""
    import numpy as np
    if audio.size == 0:
        return audio
    window = max(1, int(rate * window_ms / 1000.))
    pad = int(rate * pad_ms / 1000.)
    loud = np.abs(audio)
    first = last = None
    for i in range(0, audio.size, window):
        if float(loud[i:i + window].max()) >= threshold:
            if first is None:
                first = i
            last = i + window
    if first is None:
        return audio[:0]
    return audio[max(0, first - pad):min(audio.size, last + pad)]


@contextmanager
def offline():
    """Hugging Face network access scoped off while Breeze files load (V2 policy)."""
    saved = {k: os.environ.get(k) for k in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')}
    os.environ['HF_HUB_OFFLINE'] = os.environ['TRANSFORMERS_OFFLINE'] = '1'
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def text_tokenizer(model_dir: Path):
    """The text tokenizer exactly as mlx-audio 0.5.1's Breeze post_load_hook builds it.

    October 25 (c5ad0deb): parsing the 33 MB tokenizer.json holds the interpreter for about
    0.4 s (tokenizers does not release it). Inside the first impression's load that cost the
    microphone 0.43 s of audio, made the impression's turn stale and made a servo send report a
    timeout. The cloner builds it once at start, before the microphone opens, and keeps it
    (about 0.4 GB); the weights load without holds over 0.1 s."""
    from transformers import PreTrainedTokenizerFast
    config = json.loads((model_dir / 'tokenizer_config.json').read_text())
    for key in ('tokenizer_class', 'model_max_length', 'clean_up_tokenization_spaces', 'added_tokens_decoder'):
        config.pop(key, None)
    return PreTrainedTokenizerFast(tokenizer_file=str(model_dir / 'tokenizer.json'), **config)


def load_with_tokenizer(model_dir: Path, tokenizer):
    """mlx-audio 0.5.1's load_model with the text tokenizer already built.

    Its post_load_hook is swapped for this one load (only the cloner loads Breeze, under its
    lock): the start-time tokenizer is attached and the codec loads as the original hook does."""
    from mlx_audio.tts.models.breeze_tts.breeze_tts import Model
    from mlx_audio.tts.utils import load_model
    original = Model.__dict__['post_load_hook']
    def attach(cls, model, model_path):
        model.tokenizer = tokenizer
        model.audio_tokenizer = cls._load_audio_tokenizer(Path(model_path) / 'audio_tokenizer')
        return model
    Model.post_load_hook = classmethod(attach)
    try:
        return load_model(str(model_dir))
    finally:
        Model.post_load_hook = original


def token_cap(text: str) -> int:
    duration = max(2., len(text.split()) / 2.6) * DURATION_SLACK
    return max(1, min(MAX_TOKENS, int(duration * 12.5)))


class Take:
    """One complete cloned utterance rendered in the background; one-shot, never cached."""
    def __init__(self, text: str, reference, render):
        self.text, self.reference = text, reference
        self.ready = threading.Event()
        self.stop = threading.Event()
        self.pcm = b''
        self.error = None
        self.stats = {}
        self.started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, args=(render,), name='r3x-voice-take', daemon=True)
        self._thread.start()

    def _run(self, render):
        try:
            self.pcm, self.stats = render(self)
            if not self.pcm:
                self.error = 'EmptyCloneAudio'
        except Cancelled:
            self.error = 'Cancelled'
        except Exception as exc:
            self.error = str(exc) if isinstance(exc, ProviderFailure) else type(exc).__name__
        finally:
            self.ready.set()

    @property
    def failed(self) -> bool:
        return self.ready.is_set() and self.error is not None

    def chunks(self, cancel: Cancellation):
        """Playable pieces once the whole take exists; waits (cancellably) until then."""
        while not self.ready.wait(.05):
            cancel.check()
            if self.stop.is_set():
                raise ProviderFailure('TakeClosed')
        if self.error is not None:
            raise ProviderFailure('ImpersonationSynthesisFailed:' + self.error)
        for offset in range(0, len(self.pcm), PIECE_BYTES):
            cancel.check()
            yield self.pcm[offset:offset + PIECE_BYTES]

    def close(self) -> None:
        self.stop.set()


class VoiceCloner:
    """Capability owner for cloned-voice impressions; explicit start, lazy load, idle release."""
    def __init__(self, model_dir: Path, level=None):
        self.model_dir = Path(model_dir)
        self.level = level       # (sample_rate, text) -> PCM leveler; live composition supplies V2's.
        self.model = None
        self.tokenizer = None             # Built at start, kept until close (text_tokenizer).
        self.lock = threading.Lock()      # Load/unload and generation are serialized.
        self.trace = None
        self.started = False
        self.loaded_at = None
        self.capability = Capability('voice_cloner', 'Breeze TTS 2 (mlx-audio)',
            'cloned-voice impressions chosen by the conversation model', MODEL_ID + '@' + MODEL_REVISION[:8],
            ('mlx-audio', 'mlx', 'numpy', 'scipy', 'transformers'),
            (str(self.model_dir), 'Metal GPU, about 4.6 GB while loaded', 'text tokenizer, about 0.4 GB from start'),
            required=False, startup='verify prepared snapshot; build the text tokenizer; load on first impression',
            shutdown='close takes; unload at close')

    def _emit(self, event, **data):
        if self.trace:
            self.trace.emit(event, **data)

    def unavailable_reason(self):
        """None when the prepared snapshot and pinned library are present."""
        from importlib.metadata import version, PackageNotFoundError
        try:
            if version('mlx-audio') != '0.5.1':
                return 'BreezeRequiresMlxAudio051'
        except PackageNotFoundError:
            return 'MlxAudioMissing'
        if self.model_dir.resolve() != self.model_dir or not self.model_dir.is_dir():
            return 'BreezeModelMissing'
        for name in REQUIRED_FILES:
            path = self.model_dir / name
            if path.resolve() != path or not path.is_file():
                return 'BreezeAssetMissing:' + name
            if name in WEIGHT_BYTES and path.stat().st_size != WEIGHT_BYTES[name]:
                return 'BreezeWeightSizeMismatch:' + name
        try:
            provenance = json.loads((self.model_dir / 'provenance.json').read_text())
        except (OSError, ValueError):
            return 'BreezeProvenanceMissing'
        if provenance.get('revision') != MODEL_REVISION:
            return 'BreezeRevisionMismatch'
        digests = provenance.get('sha256', {})
        for name, expected in WEIGHT_SHA256.items():
            if digests.get(name) != expected:
                return 'BreezeWeightDigestMismatch:' + name
        return None

    def start(self):
        reason = self.unavailable_reason()
        if reason:
            raise ProviderFailure(reason)
        with offline():
            self.tokenizer = text_tokenizer(self.model_dir)
        self.started = True

    def cancel(self):
        pass   # Each take owns its stop flag; generation checks it between chunks.

    def close(self):
        self.started = False
        self._unload('close')
        self.tokenizer = None

    def _load(self):
        """Load by absolute path with Hugging Face network access scoped off (V2 policy)."""
        began = time.monotonic()
        with offline():
            from .breeze_fast_depth import enable_fast_depth
            with MLX_LOCK:
                enable_fast_depth()
                model = load_with_tokenizer(self.model_dir, self.tokenizer)
                import mlx.core as mx
                mx.synchronize()
        self.model = model
        self.loaded_at = time.monotonic()
        seconds = self.loaded_at - began
        self._emit('voice_cloner_loaded', model=self.capability.model, load_seconds=round(seconds, 2))
        return seconds

    def _unload(self, reason):
        with self.lock:
            if self.model is None:
                return
            with MLX_LOCK:
                # The weights go to MLX's cache the moment the model is dropped; handing the
                # 4.6 GB back holds the interpreter 0.10-0.13 s (measured October 25). A
                # gc.collect() after it freed nothing more and held it another 40-50 ms.
                self.model = None
                try:
                    import mlx.core as mx
                    mx.clear_cache()
                except Exception:
                    pass
        self._emit('voice_cloner_unloaded', reason=reason)

    def prepare(self, text: str, reference) -> Take:
        if not self.started:
            raise ProviderFailure('VoiceClonerNotStarted')
        text = ' '.join((text or '').split())
        if not text:
            raise ProviderFailure('EmptyImpressionScript')
        return Take(text, reference, self._render)

    def _render(self, take: Take):
        import numpy as np
        stats = {}
        with self.lock:
            if self.model is None:
                stats['load_seconds'] = round(self._load(), 2)
            began = time.monotonic()
            chunks = []
            first_chunk_at = None
            with MLX_LOCK:
                if take.stop.is_set():
                    raise Cancelled()
                generator = self.model.generate(text=take.text, ref_audio=take.reference.wav_path,
                    ref_text=take.reference.text, voice='S0', cfg_scale=1., temperature=TEMPERATURE,
                    repetition_penalty=REPETITION_PENALTY, max_tokens=token_cap(take.text),
                    stream=True, streaming_interval=STREAMING_INTERVAL)
            try:
                while True:
                    if take.stop.is_set():
                        raise Cancelled()
                    if time.monotonic() - take.started_at > PREPARE_TIMEOUT_SECONDS:
                        raise ProviderFailure('ImpersonationPreparationTimeout')
                    with MLX_LOCK:   # One compute step per acquisition; transcription interleaves.
                        try:
                            result = next(generator)
                        except StopIteration:
                            break
                        chunk = np.ascontiguousarray(np.asarray(result.audio, dtype=np.float32).reshape(-1))
                    if chunk.size:
                        if first_chunk_at is None:
                            first_chunk_at = time.monotonic()
                        chunks.append(chunk)
            finally:
                with MLX_LOCK:
                    try:
                        generator.close()
                    except Exception:
                        pass
                    try:
                        import mlx.core as mx
                        mx.synchronize()   # Async Metal work must not outlive the generation.
                        mx.clear_cache()   # Generation scratch (about 2 GB measured) goes back; weights stay.
                    except Exception:
                        pass
        audio = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)
        pcm = convert_pcm(audio, take.text, self.level)
        stats.update(synthesis_seconds=round(time.monotonic() - began, 2),
                     first_chunk_seconds=round(first_chunk_at - began, 2) if first_chunk_at else None,
                     generated_seconds=round(audio.size / MODEL_RATE, 2),
                     audio_seconds=round(len(pcm) / (2 * OUTPUT_RATE), 2), words=len(take.text.split()))
        self._emit('impression_prepared', voice=take.reference.key, **stats)
        return pcm, stats
