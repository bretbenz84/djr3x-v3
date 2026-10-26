"""The owner's spelling fixes for names the recognizer writes wrong (October 26: "Exodia" for Exudica,
"Brett" for Bret). Pure text and a fake recognizer: these prove what is replaced and what the session
log keeps, not how often the real recognizer misspells a name."""
from uuid import uuid4
import unittest

from r3x.contracts import Cancellation, Capture, Transcript
from r3x.perception import Perception
from r3x.settings import Settings
from r3x.speakers import VoiceObservation
from r3x.spelling import compile_table, correct
from tests.test_voice_core import Trace

OWNER = {'Exodia': 'Exudica', 'Brett': 'Bret'}


class SpellingTests(unittest.TestCase):
    def test_listed_whole_words_are_replaced_in_any_case_and_nothing_else(self):
        pairs = compile_table(OWNER)
        for heard, fixed in (("Exodia is the world's greatest hairstylist.", "Exudica is the world's greatest hairstylist."),
                             ("That's Brett's telescope, brett.", "That's Bret's telescope, Bret."),
                             ('EXODIA!', 'Exudica!'),
                             ('Bretton Woods and Exodias', 'Bretton Woods and Exodias'),
                             ('Nothing to fix here.', 'Nothing to fix here.')):
            self.assertEqual(correct(heard, pairs), fixed)

    def test_a_two_word_spelling_wins_over_one_word_inside_it(self):
        pairs = compile_table({'Tee Joy': "T'Joy", 'Joy': 'Joy'})
        self.assertEqual(correct('Tee  Joy is here, and Joy too.', pairs), "T'Joy is here, and Joy too.")

    def test_settings_refuse_a_bad_table(self):
        self.assertEqual(Settings(transcript_corrections=OWNER).transcript_corrections, OWNER)
        for table in ({'Exodia': ''}, {'Exodia': 'Exu(dica'}, {'Brett': 'Bret', 'brett': 'Bret'},
                      {f'Name{i}': 'X' for i in range(33)}, ['Exodia']):
            with self.assertRaises(ValueError):
                Settings(transcript_corrections=table)

    def test_the_session_log_keeps_what_was_heard_and_rex_reads_the_fix(self):
        class ASR:   # What the Qwen adapter returns once a fix applied.
            def transcribe(self, capture, cancel):
                return Transcript('Exudica is here.', -.1, True, 'Exodia is here.')
        trace = Trace()
        capture = Capture(uuid4().hex, 'session', bytes(32000), 0., 1., 1., voiced_intervals=((0., 1.),))
        perceived = Perception(ASR(), None, trace).analyze(capture, Cancellation(), voice=VoiceObservation('ns'))
        self.assertEqual(perceived.transcript.text, 'Exudica is here.')
        row = next(r for r in trace.rows if r['event'] == 'transcript')
        self.assertEqual((row['original_text'], row['corrected_text']), ('Exodia is here.', 'Exudica is here.'))


if __name__ == '__main__':
    unittest.main()
