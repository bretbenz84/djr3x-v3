"""Songs from the owner's Apple Music library, played by the Music app (owner request, November 18).

"This is the DJ in DJ-R3X's name": asked for a song, an album, an artist, a genre or one of the owner's
playlists, Rex finds it in the library, announces it like a radio DJ and the Music app plays it. Apple Music
tracks are protected streams that only the Music app can play, so nothing is downloaded or decoded here: the
Music app plays them through the Mac's default output, which on the robot is the ReSpeaker board, the same
speaker as his voice (and the board's echo cancelling hears it as his own playback).

Control is AppleScript through `osascript`, one call at a time; a person's words reach a script only as its
arguments, never as script text. Several songs play from one playlist of Rex's own, "DJ R3X" (owner ruling,
November 18: the one playlist he empties and refills; nothing else in the library is changed).

While a song plays the conversation hears nothing but the wake words: the microphone treats the music as his
own playback. "Hey Rex" or "Hey DJ Rex" pauses it so the person can talk, and the song picks up again once the
talk has been quiet for a few seconds, unless they asked him to pause or stop it (owner ruling, November 18).
"""
from dataclasses import dataclass
import random
import re
import shutil
import subprocess
import threading
import time
import unicodedata

from .contracts import Cancelled, ProviderFailure
from .providers import Capability

PLAYLIST = 'DJ R3X'
CALL_SECONDS = 10.          # One script; a playlist refill of 50 songs took about 1 s on the robot.
START_SECONDS = 12.         # A streamed song took about 2 s to start (November 18, robot).
POLL_PLAYING_SECONDS = 2.   # While music plays or waits for talk: notices a song ending or a pause in the app.
POLL_IDLE_SECONDS = 10.     # Otherwise: notices music someone started in the Music app themselves.
RESUME_QUIET_SECONDS = 6.   # Talk quiet this long after a wake word paused the music: it plays again.
QUEUE_LIMIT = 50
ACTIONS = ('play', 'pause', 'resume', 'stop', 'next', 'set_volume', 'now_playing')
US, RS = '\x1f', '\x1e'     # Field and record separators in script output.

_PRELUDE = 'set US to character id 31\nset RS to character id 30\n'
# Every song a reference names, nine properties in nine bulk reads (0.3 s for 909 songs on the robot;
# reading them song by song took 2.1 s for 13). The result is one column per property.
_COLUMNS = ('\t\tset out to {persistent ID, name, artist, album, year, genre, duration, disc number, '
            'track number} of r\n')
_JOINED = ('on joined(out, US, RS)\n'
           '\tset parts to {}\n'
           "\tset AppleScript's text item delimiters to US\n"
           '\trepeat with c in out\n\t\tset end of parts to (c as text)\n\tend repeat\n'
           "\tset AppleScript's text item delimiters to RS\n"
           '\tset t to parts as text\n'
           "\tset AppleScript's text item delimiters to \"\"\n"
           '\treturn t\nend joined\n')
# A whole list joined in one step (adding to text item by item is slow for 17,000 songs).
_JOIN = ("set AppleScript's text item delimiters to RS\nset out to found as text\n"
         "set AppleScript's text item delimiters to \"\"\nreturn out\nend run\n")
_FIND = {'songs': 'name contains q', 'artists': 'artist contains q', 'albums': 'album contains q',
         'genre': 'genre is q', 'all': '(name contains q or artist contains q or album contains q)'}
SCRIPTS = {
    # Never launches the Music app: a closed app is simply stopped.
    'state': _JOINED + 'on run argv\n' + _PRELUDE +
             'if application "Music" is not running then return "stopped" & RS & "" & RS & ""\n'
             'tell application "Music"\n'
             '\tset s to player state as text\n\tset t to ""\n\tset p to ""\n\tset v to (sound volume) as text\n'
             '\tif s is not "stopped" then\n'
             '\t\ttry\n\t\t\tset c to persistent ID of current track\n'
             '\t\t\tset r to a reference to (every track of library playlist 1 whose persistent ID is c)\n' + _COLUMNS.replace('\t\t', '\t\t\t') +
             '\t\t\tset t to my joined(out, US, RS)\n\t\tend try\n'
             '\t\ttry\n\t\t\tset p to name of current playlist\n\t\tend try\n'
             '\tend if\n'
             'end tell\n'
             'return s & RS & v & RS & p & RS & t\nend run\n',
    # argv: songs, artists, albums, genre or all; the words. Accents and punctuation do not count.
    **{f'find_{k}': _JOINED + 'on run argv\n' + _PRELUDE + 'set q to item 1 of argv\n'
       'tell application "Music"\n\tignoring diacriticals, punctuation and hyphens\n'
       f'\t\tset r to a reference to (every track of library playlist 1 whose {w})\n' + _COLUMNS +
       '\tend ignoring\nend tell\nreturn my joined(out, US, RS)\nend run\n' for k, w in _FIND.items()},
    'genres': 'on run argv\n' + _PRELUDE + 'tell application "Music" to set found to genre of every track of library playlist 1\n'
              + _JOIN,
    # Every song's id, for "play something" (16,894 ids in about 0.2 s on the robot).
    'ids': 'on run argv\n' + _PRELUDE + 'tell application "Music" to set found to persistent ID of every track of library playlist 1\n'
           + _JOIN,
    'playlists': 'on run argv\n' + _PRELUDE + 'tell application "Music" to set found to name of every user playlist whose special kind is none\n'
                 + _JOIN,
    # argv: persistent ids in order. Rex's playlist is emptied and refilled; nothing plays yet.
    'queue': _JOINED + 'on run argv\n' + _PRELUDE +
             'tell application "Music"\n'
             '\tif player state is not stopped then stop\n'
             f'\tif not (exists user playlist "{PLAYLIST}") then make new user playlist with properties {{name:"{PLAYLIST}"}}\n'
             f'\tset p to user playlist "{PLAYLIST}"\n'
             '\tdelete every track of p\n'
             '\trepeat with i in argv\n'
             '\t\ttry\n\t\t\tduplicate (first track of library playlist 1 whose persistent ID is (i as text)) to p\n'
             '\t\tend try\n\tend repeat\n'
             '\tset r to a reference to (every track of p)\n' + _COLUMNS.replace('\t\t', '\t') +
             'end tell\nreturn my joined(out, US, RS)\nend run\n',
    # argv: playlist name, shuffle (true/false), volume or "". Waits until it is heard playing.
    'play': 'on run argv\n' +
            'tell application "Music"\n'
            '\tset shuffle enabled to ((item 2 of argv) is "true")\n\tset song repeat to off\n'
            '\tif (item 3 of argv) is not "" then set sound volume to ((item 3 of argv) as integer)\n'
            '\tplay user playlist (item 1 of argv)\n'
            '\trepeat 40 times\n\t\tif player state is playing then exit repeat\n\t\tdelay 0.25\n\tend repeat\n'
            '\treturn player state as text\nend tell\nend run\n',
    'resume': 'on run argv\ntell application "Music"\n'
              '\tif player state is not stopped then play\n'
              '\trepeat 20 times\n\t\tif player state is not paused then exit repeat\n\t\tdelay 0.25\n\tend repeat\n'
              '\treturn player state as text\nend tell\nend run\n',
    'pause': 'on run argv\nif application "Music" is not running then return "stopped"\n'
             'tell application "Music"\n\tif player state is playing then pause\n'
             '\treturn player state as text\nend tell\nend run\n',
    'stop': 'on run argv\nif application "Music" is not running then return "stopped"\n'
            'tell application "Music"\n\tstop\n'
            '\trepeat 8 times\n\t\tif player state is stopped then exit repeat\n\t\tdelay 0.125\n\tend repeat\n'
            '\treturn player state as text\nend tell\nend run\n',
    # Moves on and stays paused: his announcement plays first, then the music resumes. The new song is
    # read by the next state call.
    'next': 'on run argv\ntell application "Music"\n'
            '\tif player state is stopped then return "stopped"\n'
            '\tset previousId to persistent ID of current track\n'
            '\tif player state is playing then pause\n'
            '\tnext track\n\tdelay 0.3\n'
            '\tif player state is playing then pause\n'
            '\tif player state is stopped then return "stopped"\n'
            '\tif persistent ID of current track is previousId then return "last"\n'
            '\treturn player state as text\nend tell\nend run\n',
    'volume': 'on run argv\ntell application "Music"\n\tset sound volume to ((item 1 of argv) as integer)\n'
              '\treturn (sound volume) as text\nend tell\nend run\n',
}


def osascript(script: str, args=(), timeout: float = CALL_SECONDS, cancel=None) -> str:
    """Run one AppleScript with its arguments; the person's words only ever arrive as argv."""
    try:
        process = subprocess.Popen(['/usr/bin/osascript', '-', *map(str, args)], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError:
        raise ProviderFailure('MusicScriptUnavailable')
    deadline, sent = time.monotonic() + timeout, script
    while True:
        try:
            out, err = process.communicate(sent, timeout=.05)
            break
        except subprocess.TimeoutExpired:
            sent = None
        if (cancel is not None and cancel.event.is_set()) or time.monotonic() > deadline:
            process.kill()
            process.communicate()
            if cancel is not None and cancel.event.is_set():
                raise Cancelled()
            raise ProviderFailure('MusicAppNotAnswering')
    if process.returncode:
        # -1743: macOS has not let this program control the Music app; -1712: the app did not answer
        # (a dialog waiting on the robot's screen, November 18).
        if '-1743' in err:
            raise ProviderFailure('MusicControlNotAllowed')
        if '-1712' in err:
            raise ProviderFailure('MusicAppNotAnswering')
        raise ProviderFailure('MusicScriptFailed')
    return out.rstrip('\n')


@dataclass(frozen=True)
class Song:
    song_id: str
    title: str
    artist: str
    album: str
    year: int | None
    genre: str
    seconds: int | None
    disc: int = 0
    number: int = 0

    def view(self):
        minutes = None if self.seconds is None else f'{self.seconds // 60}:{self.seconds % 60:02d}'
        return {k: v for k, v in (('title', self.title), ('artist', self.artist), ('album', self.album),
                                  ('year', self.year), ('genre', self.genre), ('length', minutes)) if v}


def songs(output: str) -> list[Song]:
    """Songs from a script's nine columns (persistent ID, name, artist, album, year, genre, seconds, disc,
    track number), each a list joined by US, the columns joined by RS."""
    columns = [c.split(US) for c in output.split(RS)] if output else []
    if len(columns) != 9 or len({len(c) for c in columns}) != 1:
        return []
    def whole(text):
        try:
            return int(round(float(text.replace(',', '.'))))
        except ValueError:
            return None
    return [Song(i, n, a, b, whole(y) or None, g, whole(d), whole(c) or 0, whole(k) or 0)
            for i, n, a, b, y, g, d, c, k in zip(*columns) if i]


def plain(text) -> str:
    """Words as a person would say them: no accents, case or punctuation."""
    text = unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().lower()
    text = re.sub(r'\s*[\(\[](feat|ft|with|remaster|live|from)[^\)\]]*[\)\]]', '', text)
    text = re.sub(r'&', ' and ', text)
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def _fits(asked, have) -> bool:
    asked, have = plain(asked), plain(have)
    return bool(asked) and (asked == have or asked in have or (len(have) >= 3 and have in asked))


def pick_playlist(asked, names):
    """The owner's playlist they named: exact words first, then one that contains them."""
    want = plain(asked)
    exact = [n for n in names if plain(n) == want]
    if exact:
        return exact[0]
    near = [n for n in names if want and (want in plain(n) or plain(n) in want)]
    return near[0] if len(near) == 1 else None


def choose(found, song=None, artist=None, album=None):
    """The songs a request means, from the Music app's search; exact titles first."""
    chosen = [s for s in found if (not artist or _fits(artist, s.artist))
              and (not album or _fits(album, s.album)) and (not song or _fits(song, s.title))]
    if song:
        exact = [s for s in chosen if plain(s.title) == plain(song)]
        return (exact or chosen)[:1]
    if album:
        exact = [s for s in chosen if plain(s.album) == plain(album)]
        chosen = exact or chosen
        first = chosen[0].album if chosen else None
        return sorted((s for s in chosen if s.album == first), key=lambda s: (s.disc, s.number))
    return chosen


class AppleMusic:
    """The Music app on this Mac: the owner's library searched and played on request. One script at a time.
    Starting reads nothing from the app; a background poll notices songs ending and music started by hand."""

    def __init__(self, settings, runner=osascript, clock=time.monotonic, rng=None):
        self.volume = settings.music_volume
        self.runner, self.clock = runner, clock
        self.rng = rng or random.Random()
        self.capability = Capability('apple_music', 'Music app (AppleScript)',
            "songs from the owner's Apple Music library, announced and played on request", None, (),
            ('osascript, one script at a time; state every 2 s while music plays, every 10 s otherwise',),
            required=False)
        self.trace = None
        self.lock = threading.Lock()        # One script at a time.
        self.guard = threading.Lock()       # The fields below.
        self.stop_event, self.wake = threading.Event(), threading.Event()
        self.worker = None
        self.audible = False     # Music is coming out of the speaker: read by the microphone on every frame.
        self.state = None        # 'playing', 'paused', 'stopped' after the first read.
        self.now = None          # The Song the app is on.
        self.playlist = None
        self.app_volume = None
        self.status = None       # Why the Music app could not be used at the last try.
        self.held_at = None      # A wake word paused it for talk: it resumes when the talk is quiet.
        self._volume_set = False
        self._pending = None     # (playlist, shuffle) prepared by a play request, started after his line.

    # ------------------------------------------------------------------ lifecycle

    def start(self):
        if shutil.which('osascript') is None and self.runner is osascript:
            raise ProviderFailure('MusicScriptUnavailable')
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._poll, name='r3x-music', daemon=True)
        self.worker.start()

    def cancel(self):
        self.stop_event.set()
        self.wake.set()

    def close(self):
        """Rex going to sleep pauses the music he would no longer hear the wake words over."""
        self.cancel()
        if self.worker:
            self.worker.join(CALL_SECONDS + 1)
            self.worker = None
        if self.audible or self.held_at is not None:
            try:
                self._script('pause', timeout=3.)
            except (ProviderFailure, Cancelled):
                pass
        self.audible, self.held_at = False, None

    def _emit(self, event, **fields):
        if self.trace:
            self.trace.emit(event, **fields)

    def _script(self, name, args=(), timeout=CALL_SECONDS, cancel=None):
        with self.lock:
            try:
                out = self.runner(SCRIPTS[name], args, timeout, cancel)
            except ProviderFailure as exc:
                with self.guard:
                    self.status = str(exc)
                raise
        with self.guard:
            self.status = None
        return out

    def _poll(self):
        while not self.stop_event.is_set():
            try:
                self.refresh()
            except (ProviderFailure, Cancelled):
                pass
            with self.guard:
                busy = self.audible or self.held_at is not None
            self.wake.wait(POLL_PLAYING_SECONDS if busy else POLL_IDLE_SECONDS)
            self.wake.clear()

    def refresh(self):
        parts = self._script('state', timeout=5.).split(RS)
        state = parts[0] if parts and parts[0] in {'playing', 'paused', 'stopped'} else 'stopped'
        with self.guard:
            was = self.state
            self.state = state
            self.app_volume = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else self.app_volume
            self.playlist = parts[2] if len(parts) > 2 and parts[2] else None
            current = songs(RS.join(parts[3:]))
            self.now = current[0] if current else None if state == 'stopped' else self.now
            if self.held_at is None:
                self.audible = state == 'playing'
            elif state == 'stopped':
                self.held_at = None   # Ended or stopped in the app while the talk went on.
        if was != state:
            self._emit('music_state', state=state, song=self.now.view() if self.now else None)
        return state

    # ------------------------------------------------------------------ the conversation's view

    def offer(self):
        """The reply context's music entry on a person's turn."""
        from .field_guide import GUIDE
        with self.guard:
            state, now, held, status = self.state, self.now, self.held_at is not None, self.status
            volume = self.app_volume
        entry = {'meaning': GUIDE['music']}
        if status:
            entry['unavailable'] = UNAVAILABLE.get(status, 'the Music app is not working right now')
            return entry
        entry['state'] = 'paused for this talk' if held else state or 'stopped'
        if now and state != 'stopped':
            entry['song'] = now.view()
        if volume is not None:
            entry['volume'] = volume
        return entry

    # ------------------------------------------------------------------ requests (the state owner)

    def run(self, arguments, cancel=None):
        """(outcome, reason, data) for one music call. Playing is prepared here and started by the output
        owner once his announcement has been heard (data['cue'])."""
        action = arguments.get('action')
        try:
            if action not in ACTIONS:
                return 'rejected', 'UnknownMusicAction', {}
            if action == 'play':
                return self._play(arguments, cancel)
            if action == 'now_playing':
                self.refresh()
                with self.guard:
                    now, state = self.now, self.state
                if now is None or state == 'stopped':
                    return 'ok', 'NothingPlaying', {'state': 'stopped'}
                return 'ok', 'executed', {'state': 'paused for this talk' if self.held_at else state, 'song': now.view()}
            if action in {'pause', 'stop'}:
                with self.guard:
                    self.held_at, self.audible = None, False
                state = self._script(action, cancel=cancel)
                self.wake.set()
                return 'ok', 'executed', {'state': state}
            if action == 'set_volume':
                value = arguments.get('volume')
                if not isinstance(value, (int, float)) or not 0 <= value <= 100:
                    return 'rejected', 'VolumeOutOfRange', {'range': '0-100'}
                now = self._script('volume', (round(value),), cancel=cancel)
                with self.guard:
                    self.app_volume, self._volume_set = int(now) if now.isdigit() else round(value), True
                return 'ok', 'executed', {'volume': self.app_volume, 'cue': self._resume_cue()}
            if action == 'resume':
                state = self.refresh()
                if state == 'stopped':
                    return 'rejected', 'NothingToResume', {}
                with self.guard:
                    self.held_at = None
                return 'ok', 'executed', {'song': self.now.view() if self.now else None, 'cue': 'resume'}
            if action == 'next':
                with self.guard:
                    self.held_at = None
                    self.audible = False
                if self._script('next', cancel=cancel) in {'stopped', 'last'}:
                    self.wake.set()
                    return 'rejected', 'NoNextSong', {}
                self.refresh()
                with self.guard:
                    song = self.now
                return 'ok', 'executed', {'song': song.view() if song else None, 'cue': 'resume'}
        except ProviderFailure as exc:
            return 'failed', str(exc), {'unavailable': UNAVAILABLE.get(str(exc), 'the Music app is not working')}
        return 'rejected', 'UnknownMusicAction', {}

    def _resume_cue(self):
        with self.guard:
            return 'resume' if self.held_at is not None else None

    def _search(self, field, words, cancel):
        return songs(self._script('find_' + field, (words,), cancel=cancel))

    def _play(self, arguments, cancel):
        song, artist, album = arguments.get('song'), arguments.get('artist'), arguments.get('album')
        playlist, genre = arguments.get('playlist'), arguments.get('genre')
        shuffle = bool(arguments.get('shuffle'))
        if playlist:
            names = [n for n in self._script('playlists', cancel=cancel).split(RS) if n and n != PLAYLIST]
            name = pick_playlist(playlist, names)
            if name is None:
                return 'rejected', 'NoSuchPlaylist', {'playlists': names[:40]}
            self._script('stop', cancel=cancel)
            return self._prepared(None, name, shuffle, {'playlist': name})
        if song:
            found = self._search('songs', song, cancel)
            chosen = choose(found, song=song, artist=artist, album=album)
            if not chosen and artist:   # "Hey Ya by OutKast" said as the artist heard it.
                chosen = choose(self._search('artists', artist, cancel), song=song)
        elif album:
            chosen = choose(self._search('albums', album, cancel), artist=artist, album=album)
        elif artist:
            chosen = choose(self._search('artists', artist, cancel), artist=artist)
            shuffle = True if arguments.get('shuffle') is None else shuffle
        elif genre:
            genres = sorted({g for g in self._script('genres', cancel=cancel).split(RS) if g})
            match = next((g for g in genres if plain(g) == plain(genre)), None) or next(
                (g for g in genres if _fits(genre, g)), None)
            if match is None:
                return 'rejected', 'NoSuchGenre', {'genres': genres[:60]}
            chosen, shuffle = songs(self._script('find_genre', (match,), cancel=cancel)), True
        else:   # "Play some music": a mix from the whole library.
            ids = [i for i in self._script('ids', cancel=cancel).split(RS) if i]
            if not ids:
                return 'rejected', 'LibraryEmpty', {}
            chosen = [Song(i, '', '', '', None, '', None) for i in self.rng.sample(ids, min(QUEUE_LIMIT, len(ids)))]
            shuffle = False
        if not chosen:
            words = song or album or artist
            close = self._closest(words, cancel) if words else []
            return 'rejected', 'NotInLibrary', {'asked': {k: v for k, v in (('song', song), ('artist', artist),
                                                ('album', album)) if v},
                                                'closest_in_library': close}
        if shuffle:
            chosen = list(chosen)
            self.rng.shuffle(chosen)
        chosen = list(dict((s.song_id, s) for s in chosen).values())[:QUEUE_LIMIT]
        queued = songs(self._script('queue', [s.song_id for s in chosen], cancel=cancel))
        if not queued:
            return 'failed', 'QueueEmpty', {}
        return self._prepared(queued, PLAYLIST, False, {})

    def _closest(self, words, cancel):
        """What the library has near a request it lacks: all its words, else its longest single words."""
        found = self._search('all', words, cancel)
        for word in sorted({w for w in plain(words).split() if len(w) >= 4}, key=len, reverse=True)[:3]:
            if found:
                break
            found = self._search('all', word, cancel)
        return [s.view() for s in found[:6]]

    def _prepared(self, queued, playlist, shuffle, extra):
        with self.guard:
            self.held_at, self.audible = None, False
            self._pending = (playlist, shuffle)
        data = {**extra, 'cue': 'start'}
        if queued:
            data['first'] = queued[0].view()
            if len(queued) > 1:
                data['songs_queued'] = len(queued)
                data['then'] = [s.view() for s in queued[1:4]]
        self.wake.set()
        return 'ok', 'executed', data

    # ------------------------------------------------------------------ playback (the output owner)

    def begin(self, cue, cancel=None):
        """Start what was prepared ('start') or carry on with what was paused ('resume'), after his line.
        Returns the app's state; raises ProviderFailure when the music did not start."""
        with self.guard:
            pending = self._pending
            if cue == 'start' and pending is None:
                raise ProviderFailure('NothingPrepared')
            self._pending = None if cue == 'start' else pending
            first_volume = None if self._volume_set else self.volume
            self._volume_set = True
            self.held_at = None
            self.audible = True   # Gate the microphone before the first note, not after.
        try:
            if cue == 'start':
                playlist, shuffle = pending
                state = self._script('play', (playlist, 'true' if shuffle else 'false',
                                              '' if first_volume is None else first_volume),
                                     timeout=START_SECONDS, cancel=cancel)
            else:
                if first_volume is not None:
                    self._script('volume', (first_volume,), cancel=cancel)
                state = self._script('resume', cancel=cancel)
        except (ProviderFailure, Cancelled):
            with self.guard:
                self.audible = False
            raise
        with self.guard:
            self.state = state
            self.audible = state == 'playing'
        self.wake.set()
        if state != 'playing':
            raise ProviderFailure('MusicDidNotStart')
        return state

    def hold_for_talk(self, reason):
        """A wake word (or a line of his own) needs the room: the music pauses at once and comes back
        when the talk is quiet. False when nothing was playing."""
        with self.guard:
            if not self.audible:
                return False
            self.audible = False
            self.held_at = self.clock()
        self._emit('music_paused', reason=reason)
        def pause():
            try:
                self._script('pause', timeout=5.)
            except (ProviderFailure, Cancelled) as exc:
                self._emit('music_pause_failed', reason=str(exc) or type(exc).__name__)
        threading.Thread(target=pause, name='r3x-music-pause', daemon=True).start()
        return True

    @property
    def held(self) -> bool:
        return self.held_at is not None

    def release_hold(self, reason):
        """Stop said, sentry mode, a game: what the wake word paused stays paused."""
        with self.guard:
            held, self.held_at = self.held_at is not None, None
        if held:
            self._emit('music_hold_released', reason=reason)

    def resume_when_quiet(self, quiet_since, now=None):
        """The runtime's tick: once the talk a wake word paused the music for has been quiet for
        RESUME_QUIET_SECONDS, the song picks up again."""
        now = self.clock() if now is None else now
        with self.guard:
            held_at = self.held_at
        if held_at is None or now - max(held_at, quiet_since) < RESUME_QUIET_SECONDS:
            return False
        with self.guard:
            if self.held_at != held_at:
                return False
            self.held_at = None
        self._emit('music_resuming', reason='talk_quiet')
        def resume():
            try:
                self.begin('resume')
            except (ProviderFailure, Cancelled) as exc:
                self._emit('music_resume_failed', reason=str(exc) or type(exc).__name__)
        threading.Thread(target=resume, name='r3x-music-resume', daemon=True).start()
        return True


UNAVAILABLE = {
    'MusicControlNotAllowed': "macOS has not allowed Rex to control the Music app yet (the owner has to click "
                              "Allow on the robot's screen, or turn it on in System Settings, Privacy & Security, "
                              "Automation)",
    'MusicAppNotAnswering': 'the Music app is not answering (perhaps a window is waiting on the robot\'s screen)',
    'MusicScriptUnavailable': 'this Mac cannot run AppleScript',
    'MusicScriptFailed': 'the Music app refused that',
}
