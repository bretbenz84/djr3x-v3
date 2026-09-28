"""Numeric speech-leveling checks (numpy/scipy), outside the dlopen-blocked contract runner.

Run: .venv/bin/python tools/check_speech_level.py
V2's leveler contract tests plus V3's ElevenLabs integration; no network or audio I/O.
"""
from contextlib import contextmanager
import unittest

import numpy as np

from r3x.contracts import Cancellation, Cancelled
from r3x.speech_level import SpeechLeveler, level_audio, speech_leveler


def speech(sr=22050, seconds=2.2, amplitude=0.06):
    t = np.arange(round(sr * seconds)) / sr
    envelope = (0.35 + 0.65 * np.sin(2 * np.pi * 2.3 * t) ** 2)
    envelope[:round(sr * .1)] = 0
    envelope[-round(sr * .15):] = 0
    return (amplitude * envelope * (np.sin(2*np.pi*210*t) + .3*np.sin(2*np.pi*1900*t))).astype(np.float32)


class LevelerTests(unittest.TestCase):
    def test_quiet_and_loud_takes_reach_the_same_level(self):
        source = speech(amplitude=.1)
        quiet, loud = level_audio(source * .25, 22050), level_audio(source * 6.25, 22050)
        delta = 20 * np.log10(np.linalg.norm(quiet) / np.linalg.norm(loud))
        self.assertLess(abs(delta), .2)  # 28 dB input difference

    def test_network_chunk_sizes_do_not_change_sound_or_length(self):
        for sr in (16000, 22050, 44100, 48000):
            with self.subTest(sr=sr):
                source = speech(sr, seconds=.877)
                expected = level_audio(source, sr)
                processor = SpeechLeveler(sr)
                chunks = [processor.process(source[i:i+137]) for i in range(0, len(source), 137)]
                chunks.append(processor.process(np.empty(0), final=True))
                np.testing.assert_array_equal(np.concatenate(chunks), expected)
                self.assertEqual(len(expected), len(source))

    def test_empty_short_and_silent_inputs_flush_without_added_samples(self):
        for n in (0, 1, 220, 22050):
            out = level_audio(np.zeros(n), 22050)
            np.testing.assert_array_equal(out, np.zeros(n))
            self.assertEqual(out.dtype, np.float32)
        tiny = speech(seconds=.11)
        self.assertEqual(len(level_audio(tiny, 22050)), len(tiny))

    def test_preroll_is_bounded_and_emits_before_end_of_take(self):
        processor = SpeechLeveler(22050)
        data = speech()
        self.assertEqual(processor.process(data[:2205]).size, 0)
        self.assertGreater(processor.process(data[2205:8820]).size, 0)

    def test_pauses_do_not_raise_noise_or_change_gain(self):
        processor = SpeechLeveler(22050)
        processor.process(speech())
        before = processor._gain_db
        pause = processor.process(np.zeros(22050 * 4, dtype=np.float32))
        self.assertEqual(processor._gain_db, before)
        self.assertEqual(float(np.max(np.abs(pause[1000:]))), 0)

    def test_same_phrase_after_long_pause_does_not_swell(self):
        source = speech()
        gap = np.zeros(22050 * 4, dtype=np.float32)
        out = level_audio(np.concatenate((source, gap, source)), 22050)
        first, repeated = out[:len(source)], out[-len(source):]
        self.assertLess(abs(20 * np.log10(np.linalg.norm(repeated) / np.linalg.norm(first))), 1.5)

    def test_transients_are_limited_without_flattening_samples(self):
        source = speech(amplitude=.008)
        source[22050:22150] = np.linspace(-.9, .9, 100)
        out = level_audio(source, 22050)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertLessEqual(float(np.max(np.abs(out))), 10 ** (-3 / 20) + 1e-6)
        self.assertGreater(np.unique(out[22050:22150]).size, 90)

    def test_maximum_gain_and_nonfinite_inputs_are_bounded(self):
        source = speech(amplitude=.00001)
        out = level_audio(source, 22050, max_gain_db=12)
        self.assertLessEqual(np.linalg.norm(out) / np.linalg.norm(source), 10 ** (12 / 20) + 1e-4)
        bad = np.array([float('nan'), float('inf'), -float('inf')], dtype=np.float32)
        np.testing.assert_array_equal(level_audio(bad, 22050), np.zeros(3))




class FakeResponse:
    def __init__(self, data, size, cancel=None):
        self.data, self.size, self.cancel, self.status_code = data, size, cancel, 200

    def iter_bytes(self, chunk_size=4096):
        for i in range(0, len(self.data), self.size):
            if self.cancel and i >= 4000:
                self.cancel.event.set()
            yield self.data[i:i+self.size]


class FakeClient:
    def __init__(self, response):
        self.response = response

    @contextmanager
    def stream(self, *args, **kwargs):
        yield self.response


class ElevenLabsLevelingTests(unittest.TestCase):
    def synthesize(self, samples, text='Hello there.', size=731, cancel=None):
        from r3x.adapters.hosted import ElevenLabsTTS
        from r3x.speech_level import PCMLeveler
        tts = ElevenLabsTTS('key', 'voice')
        tts.level = PCMLeveler
        raw = np.clip(np.round(samples * 32768), -32768, 32767).astype('<i2').tobytes()
        tts.client = FakeClient(FakeResponse(raw, size, cancel))
        out = b''.join(tts.synthesize(text, cancel or Cancellation()))
        return np.frombuffer(out, dtype='<i2').astype(np.float32) / 32768

    def test_quiet_and_loud_sentences_come_out_at_the_same_level(self):
        source = speech(amplitude=.1)
        quiet, loud = self.synthesize(source * .2), self.synthesize(source * 5)
        self.assertEqual(len(quiet), len(source))
        self.assertLess(abs(20 * np.log10(np.linalg.norm(quiet) / np.linalg.norm(loud))), .3)   # 28 dB apart before

    def test_odd_byte_chunks_match_whole_take_leveling(self):
        source = speech(amplitude=.05)
        expected = level_audio(np.round(source * 32768) / 32768, 22050)
        np.testing.assert_allclose(self.synthesize(source, size=731), expected, atol=2/32768)

    def test_whisper_tag_keeps_a_lower_target(self):
        source = speech(amplitude=.05)
        ordinary, whisper = self.synthesize(source), self.synthesize(source, '[whispers] Hello there.')
        self.assertAlmostEqual(20 * np.log10(np.linalg.norm(whisper) / np.linalg.norm(ordinary)), -4, delta=.2)

    def test_cancelled_take_does_not_flush_buffered_speech(self):
        cancel = Cancellation()
        with self.assertRaises(Cancelled):
            self.synthesize(speech(amplitude=.05), size=2000, cancel=cancel)

    def test_policy_matches_v2_defaults(self):
        leveler = speech_leveler(22050, 'hi')
        self.assertEqual((leveler.target_lufs, leveler.max_gain_db, leveler.preroll_frames), (-20., 26., 15))


if __name__ == '__main__':
    unittest.main()
