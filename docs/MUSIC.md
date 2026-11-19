# Music from Apple Music

Owner request, November 18, 2026: "a way for the robot to access music in apple music (in my library)...
This is the 'DJ' in DJ-R3X's name. So it needs to be like a DJ announcer upon request for a song. Playback
should be able to be interrupted with the wake words."

**Status, November 18:** built. The blocked-I/O checks use a fake Music app. The finished module has also been run
against the real Music app on the robot, nearly silent (volume 1): it searched, queued, played, paused, resumed and
stopped. Not yet tried in a conversation; nobody has said "Hey Rex" over a song yet.

## What Rex does

- **Asked for music, he plays it.** A song ("play Get Lucky by Daft Punk"), an album, an artist (shuffled),
  a genre, one of the owner's playlists, or just "play some music" (50 songs drawn from the whole library).
  Accents and capitals do not matter ("beyonce" finds Beyoncé).
- **He announces it like a DJ.** A short lead-in is said at once while he looks ("Ooh, hang on"). Then the
  reply model writes the announcement from what was actually found: song, artist and a hook from its year or
  album. The song starts the moment the announcement has played, never before.
- **Not in the library:** he says so and offers the closest titles the library has. Nothing plays.
- **While a song plays he hears only "Hey Rex" or "Hey DJ Rex".** Lyrics are never transcribed or answered.
  He starts no talk or goals of his own, and greets nobody, while music plays.
- **"Hey Rex" pauses the song** at once and he listens. The song plays again 6 s after the talk goes quiet
  (owner ruling, November 18), unless the person asked him to pause or stop it, or said a stop phrase.
  A plain "stop" in a lyric does nothing to the music.
- **Pause, resume, stop, next song, louder/quieter, "what song is this?"** go through the same tool. "Next"
  is announced like the first song.
- **Shutting down pauses the music**, since the sleeping Rex would no longer hear the wake words over it.
  A Jeopardy game or sentry mode keeps a paused song paused.
- **Only when asked:** the tool is offered on people's turns, never in a lull, a goal of his own, an arrival
  greeting, sentry mode, a Jeopardy game, replay or an evaluation. His dislike of unprompted music talk is
  unchanged.

## How it works

- **No download needed.** Apple Music songs are protected streams that only the Music app can play, so Rex
  plays nothing himself: he scripts the Music app (`osascript`, one script at a time, each with a time
  limit). On November 18 a streamed song from the library started in about 0.5–2 s. Downloading songs
  would only help when the internet is out.
- **Sound path.** The Music app plays to the Mac's default output, which on the robot is the ReSpeaker Flex,
  the same speaker as his voice. The board's echo cancelling therefore hears the music as his own playback.
  The board runs at 16 kHz, so music sounds like AM radio, with nothing above about 8 kHz.
- **His playlist.** Several songs play from one playlist of his own in the library, **DJ R3X** (owner
  ruling, November 18: the one playlist he empties and refills). He changes nothing else in the library.
  Asked for one of the owner's playlists, he plays it directly.
- **Searching** reads the library in bulk: about 0.3 s even for 900 matches; a whole request took 0.4–1.5 s
  on the robot.
- **Safety of words.** What a person said only ever reaches a script as an argument, never as script text.
- **Hearing.** The microphone treats "the Music app is playing" like his own speech. Speech heard during a
  song is set aside as playback, while the wake-word spotter keeps listening on the board's channel 1. A wake
  word pauses the song and marks the following sound as the person's, the same way "Hey Rex" over his own
  line does.
- **Code.** `src/r3x/music.py` controls the Music app. The state owner runs the `music` tool. The output owner
  starts the song (`MusicCue`) after the announcement and says a fixed cover line if it fails to start. Any
  line of his pauses a playing song first. The runtime pauses for wake words and resumes when the talk is quiet.

## Setup

1. The Music app is signed in to Apple Music on the robot, with the songs in the library.
2. `local/settings.toml`: `apple_music_enabled = true` (set on the robot November 18) and `music_volume`
   (default 50: the Music app's own volume the first time he plays; "louder"/"quieter" change it after that).
3. The first time Rex controls the Music app, macOS asks on the robot's screen whether to allow it. Click
   **Allow**. Until then, the Music app does not answer and Rex says so. To allow it later: System
   Settings → Privacy & Security → Automation.

## Evidence (November 18)

- **Synthetic:** 23 checks in `tests/test_music.py` against a fake Music app. Covered: finding songs, albums,
  artists, genres, playlists and a mix; a song that is missing; words reaching scripts only as arguments; the
  start waiting for the announcement; a failed start saying the cover line; pause and stop; next; shutdown; whole
  turns through the production session; "Hey Rex" pausing a song while a lyric "stop" is ignored; the 6 s
  quiet resume; a stop phrase keeping it paused; the settings. Full blocked-I/O suite: 2,038 pass.
- **Real Music app, nearly silent:** the module's own requests on the owner's library (16,894 songs), at
  volume 1, with the volume put back to 100 afterwards. Get Lucky, Random Access Memories (13 songs in
  order), Beyoncé (50, shuffled), Disney, the TikTok Songs playlist, Thriller and a 50-song mix were each
  found and queued in 0.4–1.5 s. Get Lucky started in 0.5 s, paused and resumed, then stopped.

## Open

- **Hearing "Hey Rex" over a song** from his own speaker is unmeasured. `wake_word_listened` events with
  `over: "music"` log how close each wake word came, for tuning `music_volume`.
- A conversation test with the owner present: a song, an album, "next", "louder", a song not in the
  library, and "Hey Rex, what's the weather?" over a song (it should come back afterwards).
- How the announcement sounds, and its latency (two model calls: the request, then the announcement).
- The first Automation prompt when Rex is started by the wake supervisor (it may name Python rather than
  the app you started it from).
- Not built: DJ talk between songs, and dancing or moving to the music.
