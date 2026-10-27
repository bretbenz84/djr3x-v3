"""Room sounds (owner, October 26): Rex logs doorbells, knocks, dogs and alarms he hears, and does nothing else."""
from array import array
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from r3x import room_sounds
from r3x.capture import Frame
from r3x.contracts import ProviderFailure
from r3x.dataset import read_wav
from r3x.room_sounds import RoomSounds
from r3x.settings import Settings
from tests import test_voice_core as core


FRAME = 512 / 16000
# A synthetic room: each sound is a constant sample value, and the stand-in model knows them all.
QUIET, BARK, DOORBELL, TALK, FAINT_DOG, ALARM = 0, 100, 200, 300, 400, 500
TAGS = {QUIET: {}, BARK: {75: .8}, DOORBELL: {355: .9}, TALK: {0: .95, 74: .4}, FAINT_DOG: {75: .2},
        ALARM: {399: .85}}


class Tagger:
    """A stand-in CED-Tiny: the highest score of each class among the sounds in the window."""
    def __init__(self, fail=None, everything=None):
        self.fail, self.everything, self.looks = fail, everything, []
    def tags(self, pcm):
        if self.fail:
            raise self.fail
        heard = set(array('h', pcm))
        self.looks.append((len(pcm) / 32000, heard))
        scores = [0.] * 527
        for sound in heard:
            for index, score in (self.everything or TAGS[sound]).items():
                scores[index] = max(scores[index], score)
        return scores


class Trace:
    session_id = 'session'
    def __init__(self):
        self.rows, self.at = [], 0.
    def clock(self): return self.at
    def emit(self, event, **data): self.rows.append({'event': event, **data})


class RoomSoundsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve() / 'room-sounds'
        self.trace, self.tagger = Trace(), Tagger()
        self.at = 1000.

    def listener(self, **kw):
        room = RoomSounds(self.tagger, self.root, threaded=False, wall=lambda: 1_800_014_400. + self.trace.at, **kw)
        room.trace = self.trace
        self.trace.at = self.at
        room.start()
        return room

    def play(self, room, *parts):
        """Feed (sound, seconds[, echo_risk, motion_risk, music, wheels]) to the listener in 32 ms frames."""
        for sound, seconds, *flags in parts:
            echo, motion, music, wheels = (*flags, False, False, False, False)[:4]
            for _ in range(round(seconds / FRAME)):
                frame = Frame(array('h', [sound] * 512).tobytes(), self.at, self.at + FRAME, echo, motion)
                self.at += FRAME
                self.trace.at = self.at
                room.feed(frame, rex_sound=music, wheels=wheels)

    def events(self):
        return [r for r in self.trace.rows if r['event'] == 'room_sound']

    def test_a_bark_is_logged_once_with_a_clip_to_listen_to(self):
        room = self.listener()
        self.play(room, (QUIET, 3), (BARK, 1), (QUIET, 5))
        room.close()
        [event] = self.events()
        self.assertEqual((event['group'], event['peak_score'], event['peak_class']), ('dog', .8, 'Bark'))
        self.assertFalse(event['rex_sound'] or event['servos'] or event['wheels'])
        self.assertTrue(event['local_time'].startswith('2027-01-15T'))   # Wall time, for "the dog barked at 3".
        pcm = read_wav(self.root / 'session' / event['clip'])
        self.assertIn(BARK, set(array('h', pcm)))                       # The bark is in it...
        self.assertLessEqual(len(pcm) / 32000, event['seconds'] + 2 * room_sounds.CLIP_PAD_SECONDS + .1)   # ...and little else.
        [summary] = [r for r in self.trace.rows if r['event'] == 'room_sounds_listened']
        self.assertTrue(summary['final'])
        self.assertEqual(summary['events'], {'doorbell': 0, 'knock': 0, 'dog': 1, 'alarm': 0})
        self.assertAlmostEqual(summary['heard_seconds'], 9, delta=1)
        self.assertEqual(summary['missed_seconds'], 0)

    def test_every_moment_is_heard_twice_in_two_second_looks(self):
        room = self.listener()
        self.play(room, (QUIET, 10))
        room.close()
        self.assertTrue(all(1.9 < seconds <= 2.05 for seconds, _ in self.tagger.looks))
        self.assertIn(len(self.tagger.looks), (8, 9))   # Once a second after the first two.
        self.assertEqual(self.events(), [])

    def test_a_score_under_the_log_level_is_not_logged(self):
        room = self.listener()
        self.play(room, (FAINT_DOG, 6), (QUIET, 3))
        room.close()
        self.assertEqual(self.events(), [])

    def test_barks_close_together_are_one_event_and_far_apart_are_two(self):
        room = self.listener()
        self.play(room, (QUIET, 3), (BARK, .5), (QUIET, 1), (BARK, .5), (QUIET, 8), (BARK, .5), (QUIET, 5))
        room.close()
        self.assertEqual([e['group'] for e in self.events()], ['dog', 'dog'])
        self.assertEqual(len({e['clip'] for e in self.events()}), 2)

    def test_two_kinds_of_sound_are_separate_events(self):
        room = self.listener()
        self.play(room, (QUIET, 3), (DOORBELL, 1), (BARK, 2), (QUIET, 5))
        room.close()
        self.assertEqual(sorted(e['group'] for e in self.events()), ['dog', 'doorbell'])

    def test_his_own_sound_his_movement_and_the_wheels_are_marked(self):
        room = self.listener()
        self.play(room, (QUIET, 3), (DOORBELL, 1, True), (QUIET, 5))           # His own line or sound effect.
        self.play(room, (BARK, 1, False, True, False, True), (QUIET, 5))      # Servos and wheels moving.
        self.play(room, (ALARM, 1, False, False, True), (QUIET, 5))           # His think music under the room.
        room.close()
        marks = {e['group']: (e['rex_sound'], e['servos'], e['wheels']) for e in self.events()}
        self.assertEqual(marks, {'doorbell': (True, False, False), 'dog': (False, True, True),
                                 'alarm': (True, False, False)})

    def test_a_false_alarm_on_talk_shows_the_voice_score(self):
        room = self.listener()
        self.play(room, (QUIET, 3), (TALK, 2), (QUIET, 5))
        room.close()
        [event] = self.events()
        self.assertEqual((event['group'], event['peak_score'], event['voice_score']), ('dog', .4, .95))

    def test_an_alarm_left_sounding_is_logged_in_parts_with_short_clips(self):
        room = self.listener()
        self.play(room, (QUIET, 3), (ALARM, 70), (QUIET, 5))
        room.close()
        parts = self.events()
        self.assertEqual([e['group'] for e in parts], ['alarm'] * 3)
        self.assertTrue(all(e['seconds'] <= room_sounds.EVENT_MAX_SECONDS for e in parts))
        for event in parts:
            self.assertLessEqual(len(read_wav(self.root / 'session' / event['clip'])) / 32000,
                                 room_sounds.CLIP_MAX_SECONDS + FRAME)

    def test_clips_stop_at_the_limit_but_events_are_still_logged(self):
        with patch.object(room_sounds, 'CLIPS_PER_RUN', 1):
            room = self.listener()
            self.play(room, (QUIET, 3), (BARK, 1), (QUIET, 5), (DOORBELL, 1), (QUIET, 5))
            room.close()
        first, second = self.events()
        self.assertIsNotNone(first['clip'])
        self.assertEqual((second['clip'], second['clip_skipped']), (None, 'clip_limit'))
        self.assertEqual(len(list((self.root / 'session').iterdir())), 1)

    def test_totals_are_logged_every_ten_minutes(self):
        room = self.listener()
        self.play(room, (QUIET, 601))
        summaries = [r for r in self.trace.rows if r['event'] == 'room_sounds_listened']
        self.assertEqual([s['final'] for s in summaries], [False])
        self.assertAlmostEqual(summaries[0]['heard_seconds'], 600, delta=2)
        self.assertIsNotNone(summaries[0]['look_ms_median'])
        room.close()

    def test_a_failing_model_stops_the_listener_and_nothing_else(self):
        self.tagger.fail = ProviderFailure('Boom')
        room = self.listener()
        self.play(room, (BARK, 10))
        failures = [r for r in self.trace.rows if r['event'] == 'provider_failed']
        self.assertEqual(len(failures), room_sounds.FAILURES_TO_STOP)   # It stopped trying...
        self.assertIn({'event': 'room_sounds', 'status': 'stopped', 'reason': 'model_failed'}, self.trace.rows)
        room.close()                                                     # ...and closes cleanly.
        self.assertEqual(self.events(), [])

    def test_its_own_thread_looks_without_holding_up_the_listening_loop(self):
        room = RoomSounds(self.tagger, self.root)
        room.trace = self.trace
        room.start()
        began = time.perf_counter()
        self.play(room, (QUIET, 3), (BARK, 1), (QUIET, 5))
        fed = time.perf_counter() - began
        deadline = time.monotonic() + 3
        while not self.tagger.looks and time.monotonic() < deadline:
            time.sleep(.01)
        room.close()
        self.assertTrue(self.tagger.looks)
        self.assertLess(fed, 1.)   # 280 frames handed over; the looks run elsewhere.
        self.assertFalse(room.thread)

    def test_a_linked_clip_folder_is_refused(self):
        target = self.root.parent / 'elsewhere'
        target.mkdir()
        self.root.symlink_to(target)
        room = RoomSounds(self.tagger, self.root, threaded=False)
        room.trace = self.trace
        with self.assertRaisesRegex(ProviderFailure, 'RoomSoundsFolderLinked'):
            room.start()

    def test_live_startup_loads_the_model_then_the_listener_before_the_microphone(self):
        from r3x.app import App
        with tempfile.TemporaryDirectory() as folder:
            for voice_check in (True, False):   # The listener alone still loads the model.
                app = App(Settings(room_sounds_enabled=True, voice_check_enabled=voice_check), Path(folder))
                app.configure_live()
                names = [p.capability.name for p in app.owner.providers]
                self.assertLess(names.index('voice_check'), names.index('room_sounds'))
                self.assertLess(names.index('room_sounds'), names.index('capture'))
                room = next(p for p in app.owner.providers if p.capability.name == 'room_sounds')
                self.assertIs(room.model, next(p for p in app.owner.providers if p.capability.name == 'voice_check'))

    def test_optional_off_unless_chosen_and_needs_the_model(self):
        capability = RoomSounds(self.tagger, self.root).capability
        self.assertFalse(capability.required)
        self.assertEqual(capability.requires, ('voice_check',))
        self.assertFalse(Settings().room_sounds_enabled)
        with self.assertRaisesRegex(ValueError, 'room_sounds_enabled'):
            Settings(room_sounds_enabled='yes')


class RoomSoundsReportTests(unittest.TestCase):
    """tools/check_room_sounds.py: from the session log to a numbered folder to listen to, and back as counts."""
    def test_logged_sounds_become_a_folder_to_listen_to_and_labels_become_counts(self):
        import contextlib, io, json
        from argparse import Namespace
        from r3x.settings import ScratchPaths
        from r3x.trace import Trace as SessionTrace
        from tools import check_room_sounds as tool
        with tempfile.TemporaryDirectory() as folder:
            paths = ScratchPaths(Path(folder).resolve() / 'local')
            paths.prepare()
            clock = [1000.]
            trace = SessionTrace('run1', paths.root / 'sessions' / 'run1.jsonl', clock=lambda: clock[0], display=lambda row: None)
            room = RoomSounds(Tagger(), paths.root / 'room-sounds', threaded=False)
            room.trace = trace
            room.start()
            for sound, seconds in ((QUIET, 3), (BARK, 1), (QUIET, 5), (TALK, 2), (QUIET, 5)):
                for _ in range(round(seconds / FRAME)):
                    room.feed(Frame(array('h', [sound] * 512).tobytes(), clock[0], clock[0] + FRAME))
                    clock[0] += FRAME
            room.close()
            trace.close()
            out = Path(folder).resolve() / 'listen'
            with contextlib.redirect_stdout(io.StringIO()):
                tool.listen(Namespace(out=out, since=None), paths)
            self.assertEqual(sorted(p.name[:9] for p in out.glob('R*.wav')), ['R001 dog ', 'R002 dog '])
            labels = json.loads((out / 'labels.json').read_text())
            self.assertEqual(len(labels), 2)
            bark, talk = labels                          # The owner listens: one real, one his own voice.
            (out / 'labels.json').write_text(json.dumps({bark: 'real', talk: 'Bret talking'}))
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                tool.report(Namespace(since=None, labels=out / 'labels.json'), paths)
            dog = json.loads(printed.getvalue())['by_kind']['dog']
            self.assertEqual((dog['events'], dog['on_talk']), (2, 1))
            self.assertEqual(dog['real_and_false_at_or_above']['0.3'], [1, 1])
            self.assertEqual(dog['real_and_false_at_or_above']['0.5'], [1, 0])   # At .5 only the bark is left.
            self.assertEqual(dog['false_alarms_were'], {'Bret talking': 1})


class RoomSoundsRuntimeTests(unittest.TestCase):
    """Through the real listening loop and session: a sound is logged, and Rex does nothing about it."""
    run_frames = core.SelfNoiseRuntimeTests.run_frames

    def test_a_dog_heard_by_the_listening_loop_is_logged_and_nothing_else_happens(self):
        with tempfile.TemporaryDirectory() as folder:
            room = RoomSounds(Tagger(everything={75: .7}), Path(folder).resolve() / 'room-sounds', threaded=False)
            self.run_frames([(False, 250, False)], [], room_sounds=room)   # 8 s the speech detector calls quiet.
            room.close()
            [event] = [r for r in self.trace_rows if r['event'] == 'room_sound']
            self.assertEqual(event['group'], 'dog')
            self.assertTrue((Path(folder).resolve() / 'room-sounds' / 'session' / event['clip']).is_file())
        self.assertEqual(self.session.brain.calls, 0)   # No reply...
        self.assertEqual(self.heard, [])                # ...no listening cue...
        self.assertEqual(self.noted, [])                # ...and his idle movement carries on.
        self.assertFalse(any(r['event'] in ('capture', 'transcript') for r in self.trace_rows))


if __name__ == '__main__':
    unittest.main()
