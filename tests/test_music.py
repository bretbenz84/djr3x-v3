"""Apple Music through the Music app (owner request, November 18). A fake Music app stands behind the script
runner: synthetic songs, no AppleScript, no sound. These prove what a request, an announcement, a wake word and
quiet talk do; they do not prove that "Hey Rex" is heard over a song from his own speaker."""
import json
import threading
import unittest

from r3x.capture import Frame
from r3x.contracts import Cancellation, ToolCall, Transcript
from r3x.music import PLAYLIST, RESUME_QUIET_SECONDS, RS, SCRIPTS, US, AppleMusic, Song
from r3x.output import MUSIC_FAILED_LINE, Delivery, MusicCue, OutputOwner, TextOutput
from r3x.runtime import VoiceRuntime
from r3x.scenario import SyntheticSink, SyntheticTTS
from r3x.semantics import grounded_tools, validate
from r3x.session import Session
from r3x.settings import Settings
from tests.test_memory_core import MemoryFixture, observation
from tests.test_voice_core import Brain, Trace

LIBRARY = (
    Song('A1', 'Get Lucky', 'Daft Punk, Pharrell Williams & Nile Rodgers', 'Random Access Memories', 2013, 'Pop', 369, 1, 8),
    Song('A2', 'Give Life Back to Music', 'Daft Punk', 'Random Access Memories', 2013, 'Pop', 274, 1, 1),
    Song('A3', 'Instant Crush', 'Daft Punk', 'Random Access Memories', 2013, 'Pop', 337, 1, 5),
    Song('B1', 'Halo', 'Beyoncé', 'I Am... Sasha Fierce', 2008, 'R&B/Soul', 261, 1, 2),
    Song('C1', 'Cantina Band', 'John Williams', 'Star Wars (Original Soundtrack)', 1977, 'Soundtrack', 166, 1, 4),
    Song('C2', 'Main Title', 'John Williams', 'Star Wars (Original Soundtrack)', 1977, 'Soundtrack', 330, 1, 1),
    # November 18, run a37ed477: both albums were in the library under other wording.
    Song('F1', 'Hound Dog', 'Elvis Presley', 'Forrest Gump (The Soundtrack)', 1994, 'Rock', 136, 1, 1),
    Song('F2', 'Mrs. Robinson', 'Simon & Garfunkel', 'Forrest Gump (The Soundtrack)', 1994, 'Rock', 244, 2, 3),
    Song('F3', 'Forrest Gump Suite', 'Alan Silvestri', 'Forrest Gump (Original Motion Picture Score)', 1994, 'Rock', 500, 1, 1),
    Song('K1', 'Symphony No. 5 in C Minor, Op. 67: I. Allegro con brio', 'Vienna Philharmonic & Carlos Kleiber',
         'Beethoven: Symphonies Nos. 5 & 7', 1975, 'Classical', 440, 1, 1),
    Song('K5', 'Symphony No. 7 in A Major, Op. 92: I. Poco sostenuto - Vivace', 'Vienna Philharmonic & Carlos Kleiber',
         'Beethoven: Symphonies Nos. 5 & 7', 1976, 'Classical', 820, 1, 5),
    Song('K6', 'Symphony No. 7 in A Major, Op. 92: II. Allegretto', 'Vienna Philharmonic & Carlos Kleiber',
         'Beethoven: Symphonies Nos. 5 & 7', 1976, 'Classical', 500, 1, 6),
    Song('M1', 'Symphony No. 7 in A Major, Op. 92: I. Poco sostenuto - Vivace',
         'Moscow Radio Symphony Orchestra & Vladimir Fedoseyev', "7 Conductors vs. Beethoven's 7th", 1990, 'Classical', 800, 1, 1),
)
NAMES = {script: name for name, script in SCRIPTS.items()}


def columns(found):
    fields = list(zip(*[(s.song_id, s.title, s.artist, s.album, str(s.year or 0), s.genre, f'{s.seconds}.4',
                         str(s.disc), str(s.number)) for s in found])) or [()] * 9
    return RS.join(US.join(column) for column in fields)


class FakeMusicApp:
    """Plays the Music app's part for each script, by name; records each call and its arguments."""
    def __init__(self):
        self.calls, self.state, self.volume = [], 'stopped', 100
        self.playlists = {'Road Trip': [LIBRARY[4], LIBRARY[3]], 'TikTok Songs': [LIBRARY[3]]}
        self.playing_list, self.index, self.refuse = None, 0, set()
        self.lock = threading.Lock()

    @property
    def current(self):
        return self.playlists[self.playing_list][self.index] if self.playing_list else None

    def __call__(self, script, args, timeout, cancel):
        from r3x.contracts import ProviderFailure
        name = NAMES[script]   # Only the fixed scripts are ever run: words arrive as arguments.
        with self.lock:
            self.calls.append((name, tuple(args)))
            if name in self.refuse:
                raise ProviderFailure('MusicAppNotAnswering')
            return self.answer(name, list(args))

    def answer(self, name, args):
        from r3x.music import plain
        if name == 'state':
            now = [self.current] if self.state != 'stopped' and self.current else []
            return RS.join((self.state, str(self.volume), self.playing_list or '', columns(now)))
        if name.startswith('find_'):
            field = name[5:]
            want = plain(args[0])
            pick = {'songs': lambda s: want in plain(s.title), 'artists': lambda s: want in plain(s.artist),
                    'albums': lambda s: want in plain(s.album), 'genre': lambda s: s.genre == args[0],
                    'all': lambda s: any(want in plain(v) for v in (s.title, s.artist, s.album))}[field]
            return columns([s for s in LIBRARY if pick(s)])
        if name == 'genres':
            return RS.join(s.genre for s in LIBRARY)
        if name == 'ids':
            return RS.join(s.song_id for s in LIBRARY)
        if name == 'playlists':
            return RS.join([*self.playlists, *([PLAYLIST] if PLAYLIST in self.playlists else [])])
        if name == 'queue':
            self.state, self.playing_list = 'stopped', None
            by_id = {s.song_id: s for s in LIBRARY}
            self.playlists[PLAYLIST] = [by_id[i] for i in args if i in by_id]
            return columns(self.playlists[PLAYLIST])
        if name == 'play':
            self.playing_list, self.index, self.state = args[0], 0, 'playing'
            if args[2]:
                self.volume = int(args[2])
            return 'playing'
        if name == 'resume':
            if self.state != 'stopped':
                self.state = 'playing'
            return self.state
        if name == 'pause':
            self.state = 'paused' if self.state == 'playing' else self.state
            return self.state
        if name == 'stop':
            self.state, self.playing_list = 'stopped', None
            return 'stopped'
        if name == 'next':
            if self.state == 'stopped':
                return 'stopped'
            if self.index + 1 >= len(self.playlists[self.playing_list]):
                return 'last'
            self.index, self.state = self.index + 1, 'paused'
            return 'paused'
        if name == 'volume':
            self.volume = int(args[0])
            return str(self.volume)
        raise AssertionError(name)


def player(app=None, clock=None):
    app = app or FakeMusicApp()
    music = AppleMusic(Settings(apple_music_enabled=True, music_volume=40), runner=app,
                       **({'clock': clock} if clock else {}))
    music.trace = Trace()
    return app, music


def play(**asked):
    return {'action': 'play', 'song': None, 'artist': None, 'album': None, 'genre': None, 'playlist': None,
            'shuffle': None, 'volume': None, **asked}


class FindingTests(unittest.TestCase):
    def test_a_song_and_its_artist_queue_that_song_and_nothing_plays_before_the_announcement(self):
        app, music = player()
        outcome, reason, data = music.run(play(song='get lucky', artist='daft punk'))
        self.assertEqual((outcome, data['cue'], data['first']['title']), ('ok', 'start', 'Get Lucky'))
        self.assertEqual(data['first']['year'], 2013)
        self.assertEqual([s.song_id for s in app.playlists[PLAYLIST]], ['A1'])
        self.assertEqual(app.state, 'stopped')
        self.assertNotIn('play', [name for name, _ in app.calls])
        self.assertFalse(music.audible)

    def test_an_album_plays_in_track_order_and_an_artist_shuffled(self):
        app, music = player()
        music.run(play(album='Random Access Memories'))
        self.assertEqual([s.number for s in app.playlists[PLAYLIST]], [1, 5, 8])
        music.rng.seed(3)
        outcome, _, data = music.run(play(artist='Daft Punk'))
        self.assertEqual((outcome, data['songs_queued']), ('ok', 3))
        self.assertEqual({s.song_id for s in app.playlists[PLAYLIST]}, {'A1', 'A2', 'A3'})

    def test_accents_and_case_do_not_matter_and_a_genre_or_playlist_is_found_by_its_words(self):
        app, music = player()
        self.assertEqual(music.run(play(song='halo', artist='beyonce'))[2]['first']['artist'], 'Beyoncé')
        outcome, _, data = music.run(play(genre='soundtrack'))
        self.assertEqual((outcome, {s.song_id for s in app.playlists[PLAYLIST]}), ('ok', {'C1', 'C2'}))
        outcome, _, data = music.run(play(playlist='road trip'))
        self.assertEqual((outcome, data['playlist'], data['cue']), ('ok', 'Road Trip', 'start'))

    def test_an_album_asked_for_in_other_words_is_still_found(self):
        app, music = player()
        outcome, _, data = music.run(play(album='Forrest Gump: The Soundtrack'))
        self.assertEqual((outcome, [s.song_id for s in app.playlists[PLAYLIST]]), ('ok', ['F1', 'F2']))
        self.assertNotIn('note', data)
        outcome, _, data = music.run(play(album="Beethoven's Seventh Symphony"))
        # The album named for it, only the 7th's movements; not a set whose orchestra says "Symphony".
        self.assertEqual((outcome, [s.song_id for s in app.playlists[PLAYLIST]]), ('ok', ['K5', 'K6']))
        self.assertEqual(music.run(play(song='mrs robinson'))[2]['first']['title'], 'Mrs. Robinson')

    def test_an_album_that_is_only_a_song_here_says_so(self):
        app, music = player()
        outcome, _, data = music.run(play(album='Get Lucky'))
        self.assertEqual((outcome, data['first']['title']), ('ok', 'Get Lucky'))
        self.assertIn('no album by that name', data['note'])

    def test_play_something_is_a_mix_from_the_whole_library(self):
        app, music = player()
        outcome, _, data = music.run(play())
        self.assertEqual((outcome, data['songs_queued']), ('ok', len(LIBRARY)))

    def test_a_song_not_in_the_library_comes_back_with_the_closest_ones(self):
        app, music = player()
        outcome, reason, data = music.run(play(song='Thriller', artist='Michael Jackson'))
        self.assertEqual((outcome, reason), ('rejected', 'NotInLibrary'))
        self.assertEqual(data['closest_in_library'], [])
        outcome, reason, data = music.run(play(song='Cantina Song'))
        self.assertEqual(reason, 'NotInLibrary')
        self.assertEqual([s['title'] for s in data['closest_in_library']], ['Cantina Band'])
        self.assertNotIn(PLAYLIST, app.playlists)   # Nothing was queued.
        outcome, reason, data = music.run(play(playlist='Gym'))
        self.assertEqual((reason, data['playlists']), ('NoSuchPlaylist', ['Road Trip', 'TikTok Songs']))

    def test_what_a_person_said_reaches_a_script_only_as_its_arguments(self):
        app, music = player()
        words = 'x" & (do shell script "touch /tmp/owned") & "'
        music.run(play(song=words))
        self.assertIn(('find_songs', (words,)), app.calls)   # The script itself is one of the fixed ones.

    def test_a_music_app_that_does_not_answer_is_a_reason_not_a_crash(self):
        app, music = player()
        app.refuse.add('find_songs')
        outcome, reason, data = music.run(play(song='Halo'))
        self.assertEqual((outcome, reason), ('failed', 'MusicAppNotAnswering'))
        self.assertIn('not answering', data['unavailable'])
        self.assertIn('not answering', music.offer()['unavailable'])


class PlaybackTests(unittest.TestCase):
    def test_the_prepared_song_starts_at_the_settings_volume_and_the_microphone_is_gated(self):
        app, music = player()
        music.run(play(song='Halo'))
        self.assertEqual(music.begin('start'), 'playing')
        self.assertTrue(music.audible)
        self.assertEqual((app.state, app.playing_list, app.volume), ('playing', PLAYLIST, 40))
        self.assertEqual(OutputOwner(SyntheticTTS(), SyntheticSink(), Trace()).music_audible(), False)

    def test_a_wake_word_pauses_it_and_quiet_talk_brings_it_back(self):
        clock = [100.]
        app, music = player(clock=lambda: clock[0])
        music.run(play(song='Halo'))
        music.begin('start')
        self.assertTrue(music.hold_for_talk('wake_word'))
        self.assertFalse(music.audible)
        self.assertTrue(music.held)
        self.assertFalse(music.resume_when_quiet(100., 100. + RESUME_QUIET_SECONDS - .1))
        self.assertTrue(music.resume_when_quiet(100., 100. + RESUME_QUIET_SECONDS))
        for thread in threading.enumerate():
            if thread.name in {'r3x-music-pause', 'r3x-music-resume'}:
                thread.join(2)
        self.assertEqual(app.state, 'playing')
        self.assertTrue(music.audible)
        self.assertFalse(music.held)

    def test_pause_or_stop_keeps_it_off(self):
        app, music = player()
        music.run(play(song='Halo'))
        music.begin('start')
        music.hold_for_talk('wake_word')
        outcome, _, data = music.run({'action': 'pause'})
        self.assertEqual((outcome, music.held), ('ok', False))
        self.assertFalse(music.resume_when_quiet(-1e9, 1e9))

    def test_next_moves_on_paused_so_the_announcement_comes_first(self):
        app, music = player()
        music.run(play(album='Random Access Memories'))
        music.begin('start')
        music.hold_for_talk('wake_word')
        for thread in threading.enumerate():
            if thread.name == 'r3x-music-pause':
                thread.join(2)
        outcome, _, data = music.run({'action': 'next'})
        self.assertEqual((outcome, data['song']['title'], data['cue'], app.state), ('ok', 'Instant Crush', 'resume', 'paused'))
        music.begin('resume')
        self.assertEqual(app.state, 'playing')

    def test_shutting_down_pauses_what_he_would_no_longer_hear_the_wake_words_over(self):
        app, music = player()
        music.run(play(song='Halo'))
        music.begin('start')
        music.close()
        self.assertEqual(app.state, 'paused')

    def test_a_failed_start_says_the_cover_line_instead_of_leaving_the_announcement_hanging(self):
        trace = Trace()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
        app, output.music = player()
        output.music.run(play(song='Halo'))
        app.refuse.add('play')
        from queue import Queue
        phrases, delivery = Queue(), Delivery()
        for item in ('Spinning it now!', MusicCue('start'), None):
            phrases.put(item)
        output.run(phrases, Cancellation(), 't', delivery)
        self.assertEqual(delivery.completed_text, ['Spinning it now!', MUSIC_FAILED_LINE])
        self.assertEqual(delivery.music, 'MusicAppNotAnswering')
        self.assertFalse(output.music.audible)


class ConversationTests(MemoryFixture):
    def setUp(self):
        super().setUp()
        self.app, self.state.music = player()
        self.output = TextOutput(self.state.trace)
        self.output.music = self.state.music

    def turn(self, text, call, follow_up="Ladies and gentlemen, straight from 2008, Beyoncé with Halo!"):
        class Brain:
            def __init__(self): self.snapshots = []
            def reply(self, snapshot, cancel):
                self.snapshots.append(snapshot)
                yield follow_up if snapshot.continuation else call
        brain = Brain()
        self.begin(text)
        session = Session('session', brain, self.output, self.state.trace, self.state)
        self.addCleanup(session.close)
        return brain, session.process_turn(self.state.current[1], Transcript(text, 0, True), 0, observation())

    def music_call(self, text=None, **arguments):
        return ToolCall('m', 'music', {'say_first': 'Ooh, hang on.', **play(), **arguments, 'text': text,
                                       'expression': None})

    def test_asked_for_a_song_he_announces_what_was_found_and_then_it_plays(self):
        brain, result = self.turn('Hey Rex, play Halo by Beyonce.',
                                  self.music_call('Here comes a song!', song='Halo', artist='Beyonce'))
        self.assertIn('music', brain.snapshots[0].context)
        self.assertIn('music', {d['name'] for d in grounded_tools(brain.snapshots[0].context)})
        self.assertEqual(len(brain.snapshots), 2)   # The announcement is written from what was found.
        output = next(json.loads(r['output']) for r in brain.snapshots[1].continuation
                      if r.get('type') == 'function_call_output')
        self.assertEqual(output['data']['first']['title'], 'Halo')
        self.assertEqual(result.completed_text,
                         ['Ooh, hang on.', 'Ladies and gentlemen, straight from 2008, Beyoncé with Halo!'])
        self.assertEqual((result.music, self.app.state, self.app.playing_list), ('started', 'playing', PLAYLIST))
        cue = next(i for i, r in enumerate(self.state.trace.rows) if r['event'] == 'music_cue')
        said = max(i for i, r in enumerate(self.state.trace.rows) if r['event'] == 'text_reply')
        self.assertLess(said, cue)   # The song only after his announcement.

    def test_a_song_he_cannot_find_is_never_started(self):
        brain, result = self.turn('Play Thriller.', self.music_call(song='Thriller'),
                                  "No Thriller in your library. Shocking, I know.")
        self.assertEqual(result.completed_text, ['Ooh, hang on.', 'No Thriller in your library.', 'Shocking, I know.'])
        self.assertIsNone(result.music)
        self.assertNotIn('play', [name for name, _ in self.app.calls])

    def test_pause_says_its_line_only_after_the_music_app_paused(self):
        self.state.music.run(play(song='Halo'))
        self.state.music.begin('start')
        brain, result = self.turn('Pause the music.', ToolCall('m', 'music', {
            'say_first': 'Sure.', **play(), 'action': 'pause', 'text': 'Paused.', 'expression': None}))
        self.assertEqual(len(brain.snapshots), 1)
        self.assertEqual(result.completed_text, ['Sure.', 'Paused.'])
        self.assertEqual(self.app.state, 'paused')

    def test_offered_only_on_a_persons_turn(self):
        self.begin('Hello there.')
        base = self.state.context()
        names = lambda extra: {d['name'] for d in grounded_tools({**base, **extra})}
        offer = self.state.music.offer()
        self.assertIn('music', names({'music': offer}))
        self.assertNotIn('music', names({}))
        for trigger in ('conversation_lull', 'ambient', 'visual_arrival', 'reconsideration'):
            self.assertNotIn('music', names({'music': offer, 'trigger': trigger}))

    def test_replay_and_evaluations_never_reach_the_music_app(self):
        self.state.frozen = True
        self.begin('Play Halo.')
        result = self.call('music', {'say_first': 'Ooh.', **play(song='Halo'), 'text': None, 'expression': None})
        self.assertEqual((result.outcome, result.reason), ('rejected', 'FrozenEvaluationDisallowsMutation'))
        self.assertEqual(self.app.calls, [])

    def test_the_schema_checks_syntax_only(self):
        good = {'say_first': 'Hmm.', **play(song='Halo'), 'text': None, 'expression': None}
        validate('music', good)
        validate('music', {**good, 'action': 'set_volume', 'volume': 65, 'text': 'Louder.'})
        for bad in ({**good, 'volume': 101}, {**good, 'volume': 5.5}, {**good, 'shuffle': 'yes'},
                    {**good, 'action': 'rewind'}, {**good, 'song': ''}):
            with self.assertRaises(ValueError):
                validate('music', bad)


class WakeWordOverMusicTests(unittest.TestCase):
    """Owner, November 18: "Playback should be able to be interrupted with the wake words"."""
    def runtime(self):
        trace = Trace()
        output = OutputOwner(SyntheticTTS(), SyntheticSink(), trace)
        session = Session('s', Brain(), output, trace)
        self.addCleanup(session.close)
        app, music = player(clock=trace.clock)
        music.run(play(song='Halo'))
        music.begin('start')
        output.music = music
        class Spotter:
            last_scores = {}
            def __init__(inner): inner.word = None
            def feed(inner, pcm):
                word, inner.word = inner.word, None
                return {word: .9} if word else {}
        runtime = VoiceRuntime(None, None, None, session, trace, keywords=Spotter())
        runtime.music = music
        return runtime, trace, app, music

    def frame(self, trace):
        trace.at += .08
        return Frame(bytes(2560), trace.at - .08, trace.at, echo_risk=True)

    def test_hey_rex_pauses_the_song_and_lyrics_saying_stop_do_nothing(self):
        runtime, trace, app, music = self.runtime()
        runtime.keywords.word = 'stop'
        runtime._listen_for_keywords(self.frame(trace))
        self.assertTrue(music.audible)
        self.assertFalse(any(r['event'] in {'stop_word_heard', 'input_invalidated'} for r in trace.rows))
        runtime.keywords.word = 'Hey_DJ_Rex'
        runtime._listen_for_keywords(self.frame(trace))
        self.assertEqual([(r['word'], r['status']) for r in trace.rows if r['event'] == 'wake_word_heard'],
                         [('Hey_DJ_Rex', 'music_paused')])
        self.assertFalse(music.audible)
        self.assertEqual(runtime._wake_at, trace.at)   # What follows is the person's, not the song.

    def test_the_song_waits_while_people_talk_and_comes_back_when_it_is_quiet(self):
        runtime, trace, app, music = self.runtime()
        runtime.keywords.word = 'Hey_rex'
        runtime._listen_for_keywords(self.frame(trace))
        held_at = trace.at
        runtime.endpoint.frames.append(self.frame(trace))   # Someone is still talking.
        trace.at = held_at + RESUME_QUIET_SECONDS + 1
        runtime.music_tick(trace.at)
        self.assertTrue(music.held)
        runtime.endpoint.frames.clear()
        runtime.music_tick(trace.at + RESUME_QUIET_SECONDS - .5)
        self.assertTrue(music.held)
        runtime.music_tick(trace.at + RESUME_QUIET_SECONDS)
        self.assertFalse(music.held)
        self.assertTrue(any(r['event'] == 'music_resuming' for r in music.trace.rows))

    def test_a_spoken_stop_in_the_pause_keeps_it_paused(self):
        runtime, trace, app, music = self.runtime()
        runtime.keywords.word = 'Hey_rex'
        runtime._listen_for_keywords(self.frame(trace))
        runtime.cancel()
        runtime.music_tick(trace.at + 60)
        self.assertFalse(music.held)
        self.assertFalse(music.audible)


class SettingsTests(unittest.TestCase):
    def test_volume_is_the_music_apps_own_scale(self):
        self.assertEqual(Settings().apple_music_enabled, False)
        for bad in (0, 101, 50.5, True):
            with self.assertRaises(ValueError):
                Settings(music_volume=bad)
        with self.assertRaises(ValueError):
            Settings(apple_music_enabled='yes')


if __name__ == '__main__':
    unittest.main()
