"""CED-Tiny voice check (owner, October 26): a brief sound that is not a voice is kept but never answered."""
from pathlib import Path
import tempfile
import unittest

from r3x.adapters.voice_check import VoiceCheck
from r3x.contracts import ProviderFailure, Transcript
from r3x.settings import Settings
from tests import test_voice_core as core


class Judge:
    """A stand-in CED-Tiny: a fixed voice score, or a failure."""
    def __init__(self, score):
        self.score, self.heard = score, []
    def voice(self, pcm):
        self.heard.append(len(pcm))
        if isinstance(self.score, Exception):
            raise self.score
        return self.score


class VoiceCheckRuntimeTests(unittest.TestCase):
    """The September captures the owner listened to: 43 motor sounds, coughs, Max, Rex's own sound
    effects and his own speech. Five non-voices became turns; two of them (motor sounds while Rex was
    logged as still) got a reply, "All right.", because only motion made a brief sound suspect."""
    run_frames = core.SelfNoiseRuntimeTests.run_frames   # The real runtime, frames and session.

    def run_checked(self, parts, transcripts, score):
        self.judge = Judge(score)
        return self.run_frames(parts, transcripts, voice_check=self.judge)

    def checks(self):
        return [r for r in self.trace_rows if r['event'] == 'voice_check']

    def test_a_brief_sound_that_is_not_a_voice_is_kept_but_never_answered(self):
        # Still, so the motion rule does not apply: the recognizer trusted "Okay." (B01, B02).
        self.run_checked([(True, 3, False), (False, 50, False)], [Transcript('Okay.', -.125, True)], .03)
        distrusted = [r for r in self.trace_rows if r['event'] == 'transcript_distrusted']
        self.assertEqual([(r['reason'], r['voice_score']) for r in distrusted], [('not_a_voice', .03)])
        self.assertEqual(self.session.brain.calls, 0)    # Not sent to the reply model.
        self.assertEqual(self.heard, [])                 # No listening cue either.
        self.assertTrue(any(r['event'] == 'untrusted_not_answered' for r in self.trace_rows))
        self.assertEqual([r['score'] for r in self.checks()], [.03])
        self.assertIn('Okay.', [r.get('original_text') for r in self.trace_rows if r['event'] == 'transcript'])

    def test_a_brief_word_that_is_a_voice_is_answered_as_before(self):
        self.run_checked([(True, 3, False), (False, 50, False)], [Transcript('Yes.', -.05, True)], .86)
        self.assertEqual(self.session.brain.calls, 1)
        self.assertFalse(any(r['event'] == 'transcript_distrusted' for r in self.trace_rows))

    def test_stop_and_shut_down_never_wait_on_the_check(self):
        self.run_checked([(True, 2, True), (False, 50, True)], [Transcript('Stop.', -.25, True)], .22)
        self.assertTrue(any(r['event'] == 'input_invalidated' for r in self.trace_rows))
        self.assertEqual(self.judge.heard, [])
        self.run_checked([(True, 3, False), (False, 50, False)], [Transcript('Shut down.', -.1, True)], .1)
        self.assertTrue(any(r['event'] == 'shutdown_requested' for r in self.trace_rows))

    def test_longer_speech_is_not_checked(self):
        self.run_checked([(True, 40, False), (False, 50, False)], [Transcript('Tell me about your day.', -.05, True)], .1)
        self.assertEqual(self.judge.heard, [])
        self.assertEqual(self.session.brain.calls, 1)

    def test_a_failed_check_keeps_the_usual_trust(self):
        self.run_checked([(True, 3, False), (False, 50, False)], [Transcript('Okay.', -.125, True)], ProviderFailure('Boom'))
        self.assertTrue(any(r['event'] == 'provider_failed' and r['capability'] == 'voice_check' for r in self.trace_rows))
        self.assertEqual(self.session.brain.calls, 1)

    def test_a_sound_already_distrusted_by_his_own_motion_is_not_checked_again(self):
        self.run_checked([(True, 3, True), (False, 50, True)], [Transcript('Okay.', -.25, True)], .9)
        self.assertEqual(self.judge.heard, [])
        self.assertEqual([r['reason'] for r in self.trace_rows if r['event'] == 'transcript_distrusted'],
                         ['brief_sound_during_own_motion'])


class VoiceCheckAdapterTests(unittest.TestCase):
    def test_unverified_model_or_unstarted_check_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder).resolve() / 'model.onnx'
            path.write_bytes(b'not the pinned model')
            with self.assertRaisesRegex(ProviderFailure, 'VoiceCheckModelDigestMismatch'):
                VoiceCheck(path).start()
            with self.assertRaisesRegex(ProviderFailure, 'VoiceCheckModelMissingOrLinked'):
                VoiceCheck(path.with_name('absent.onnx')).start()
        with self.assertRaisesRegex(ProviderFailure, 'VoiceCheckNotStarted'):
            VoiceCheck(path).voice(bytes(3200))

    def test_optional_and_off_unless_chosen(self):
        self.assertFalse(VoiceCheck(Path('/absent')).capability.required)   # Rex runs without it.
        self.assertFalse(Settings().voice_check_enabled)
        with self.assertRaisesRegex(ValueError, 'voice_check_enabled'):
            Settings(voice_check_enabled='yes')


if __name__ == '__main__':
    unittest.main()
