# Jeopardy

Status, October 26, 2026: **implemented in software; one partial game on the robot (one player, two clues).** Owner request: port
V2's Jeopardy, keep what made it fun and forgiving, require responses phrased as a question, let anyone's
voice answer for the player whose turn it is, let Rex's reply model judge the answers now that replies
are faster, and include the board on the dashboard (the table plays with the Mac mirrored to an Apple TV),
with far fewer category read-backs when the board is on screen.

## How a game goes

- **Starting.** "Let's play Jeopardy" plays V2's intro sting. Unless they already said who is playing ("me,
  Jeff and Jeremy"), code asks after his line ("Who's playing? Tell me everyone's name."; October 26: his
  own line had not asked). Up to four players. "Me" is the speaker when Rex knows their voice; otherwise
  he asks for the name, and code refuses "me" or another pronoun as a player's name (October 26: "Can I
  join?" from a new voice seated a player called "Me"). A name that fits one known person is linked to them for the board and
  for where Rex looks, never as a claim about who is speaking; a guest plays under the name given and no
  person record is made.
- **The board.** A real board from V2's clue bank (6 categories of one air date and round), the board
  sting, and the categories read once. Then "Remember: phrase your response as a question" and the first
  pick.
- **Picking.** Loose is fine: "history for two hundred", "same category, six hundred", "the four hundred
  in pop culture", a category name the recognizer mangled. If two categories fit equally, Rex asks which.
  Anyone may say it for the player whose pick it is. Rex goes straight to the clue with no words of his
  own (owner, October 26, after the first game: he had riffed on each category before reading the clue).
- **The clue.** Code reads it word for word. Without a screen the square comes first ("U.S. CITIES for
  $400."); with the board on screen just the clue. The answer clock (12 s, V2's) starts only once the clue
  has been heard to its end; a clue cut off is read once more, and a spoken "stop" holds the clock until
  someone carries on ("say that again"). From then until the ruling V2's thinking music plays quietly
  (the clip is 30.7 s, the wait 12 s) and the end-of-speech pause lengthens to 1 s for "What is... Paris?".
- **Ruling.** Anyone's response counts for the player whose turn it is. The right or wrong sting plays
  the moment Rex has ruled, then a few words of his own, then code's facts ("$400 to Jeff, who has
  $1,200").
  - Forgiving about what speech recognition does: sound-alikes (knight/night, symbol/cymbal), garbled
    names ("day cart" for Descartes), years and regnal numbers said as words, a surname alone, filler.
  - Strict about substance: a different answer, a broader category, half of a two-part answer, two
    guesses ("Paris or London") or a negated one are wrong; a self-correction counts as the last answer.
  - Question form required: "what is / who's / where are / is it" and the like; an opener the recognizer
    cut short ("is Paris") still counts. The right answer said plainly counts as wrong (owner ruling,
    October 26) and nobody else gets that clue, since the answer was said aloud.
  - "I don't know" or "pass" costs nothing. A response Rex cannot make out ("the Percy crab") is asked
    again, twice, with no penalty, then counts as a pass. Words the recognizer itself was unsure of,
    which Rex otherwise never answers, still reach the ruling while a clue is open: they never cost
    money and are asked for again unless plainly right. A brief sound the voice check finds is not a
    voice (his motors, a cough) never reaches a ruling (owner, October 26; [voice check](VOICE_CHECK.md)).
  - Talk that is not a response (people to each other, the dog, "this board is rigged", "what's the
    score?") is never ruled on; the clock waits while Rex answers and keeps running otherwise.
- **After a clue.** A right answer keeps the board for that player. A wrong one gives the next player one
  rebound (V2's limit); then the correct response is read as a question and the pick passes on from
  whoever chose the square. Time's up works the same way and needs no model call. Every fourth ruling
  reads the whole scoreboard. Without a screen the remaining categories follow each of the first four
  squares, then every third (V2's curve); with the board on screen, only when someone asks. After six
  squares Rex mentions once that "next round" deals the next board.
- **Daily Double.** The sting (cut to 6 s), the wager (from $5 up to the player's score or the round's top
  value; "everything" or "a true Daily Double" is all in), then the clue. It belongs to its picker: no
  rebound, and the picker keeps the board.
- **Double Jeopardy and Final Jeopardy.** When the board is empty (or on "next round"), Double
  Jeopardy at doubled values, trailing player first. Then Final Jeopardy: the category, wagers lowest
  score first (players at $0 or less play for pride), the clue, V2's 30-second think music, then each
  response in turn. Rex never says whether one was right until the reveal, which settles the wagers,
  reads the final scores, crowns the winner and plays the outro.
- **Fixing a bad ruling.** "Rex, that was right!" lets him overturn his last ruling once; scores, a moot
  rebound and the pick follow. A clue that cannot work aloud (it needs a picture or a sound) can be
  skipped with no score change.
- **Ending.** "Let's stop playing" (Rex checks they mean it unless it is clear), the end of Final
  Jeopardy, or ten minutes with nobody heard. During a game his goals, greetings, news, curiosity
  questions, impressions and the joke writer pause, and he does not drive; his head turns to the player
  whose turn it is when he recognizes their face.

## The dashboard

With `live --gui` the window switches to V2's game page while a game is on and back 25 s after it ends:
the board (categories light up one by one as Rex reads them), the open clue large with a countdown bar,
the Daily Double and Final Jeopardy cards, the last ruling and what Rex heard, the four player podiums
with scores, and the final scores. V2's 3D droid in the side column is left out: a second Qt Quick 3D view
would double the render cost on the robot's Mac.

## How it works

- **Game state** (`src/r3x/jeopardy.py`): the board from `assets/jeopardy/clues.tsv` (read when a game
  starts, about 0.1 s), turns, attempts, the clock, wagers, scores and a record of every ruling. Each move
  is planned on a copy and takes effect only when the state owner commits the tool call, so a turn cut
  off by a stop changes nothing nobody heard. A move returns what to deliver in order: sounds, Rex's own
  words, code's lines and the line whose hearing starts the clock.
- **The reply model decides the moves** through five tools: `jeopardy_setup`, `jeopardy_pick`,
  `jeopardy_wager`, `jeopardy_ruling` and `jeopardy_control` (repeat the clue, read the board, next round,
  Final Jeopardy, skip a clue, overturn the last ruling, end the game). The judging rules live in
  `jeopardy_ruling`'s description, part of the cached prompt. A ruling answers, in order: were the words an
  attempt at the clue at all; what was meant (the recognizer's spelling of a sound-alike fixed); a few words
  weighing it against the correct response; does it sound the same as the correct response; was it phrased
  as a question; and the verdict on the answer alone. Code applies the owner's rules from those answers:
  not an attempt changes nothing, a sound-alike is right, and the right answer said plainly counts as
  wrong. Each of these came from a failure in the paid probe (below). If Rex goes silent on someone's words
  while a clue is open (other than a cut-off "What is..." or words the recognizer was unsure of), he is
  asked once more whether that was a response; that is the one case with a second model call. Each moment offers only its own moves plus
  silence and shutdown. One model call per move; no separate judge call, no word matching. None of V2's
  matchers (fuzzy ratios, Soundex, CMUdict) or its regex lanes for picks, wagers, scores and table talk
  came over: the model hears those.
- **A lean request.** While a game is on, the request shows the game and the turn only (no memories,
  goals, jokes, curiosity or camera notes), and a short hosting note rides last.
- **Code's own clock** runs from the conversation loop in place of his initiative: time's up, the
  unheard clue read again, a game nobody plays ending. Speech still under way at the buzzer is judged
  first, for up to 10 s.
- **Sounds.** V2's nine clips are 22,050 Hz mono WAVs in `assets/audio/jeopardy/`. Stings play between
  lines through the one output owner, with the microphone gated like his speech. The thinking music is a
  low-volume loop (V2's 0.22 gain) that leaves the microphone open. The board's usual channel turns a voice
  down about 20 dB while anything plays (V2's Flex measurement), so from the moment a clue has been heard
  until its ruling Rex listens on channel 1 (echo-cancelled, not turned down), as he does for "stop" and
  "Hey Rex", brought up to the usual channel's level by a ratio learned from speech heard on both while
  nothing plays (until then about 12 dB). Those answers never teach his voice memory. The music stops once
  someone is talking and stays off for that clue, so less of it reaches recognition. Rex weighing a sound or remark he then lets pass does not stop it; when
  he speaks mid-clue it gives way and then picks up where it was, under half a second on (owner, October 26,
  after the first game: it had stopped and started again from the top around a stray "Okay.").

## Checks and evidence

- `tests/test_jeopardy.py` (synthetic clues, scripted model, fake clocks and sinks): rules, rebounds,
  Daily Doubles, both rounds and Final Jeopardy, the clock (unheard clue, speech at the buzzer, pauses,
  stop), uncommitted moves, the state owner's checks, the session's order of sounds and lines, time's up
  without a model call, the lean request, the runtime's clock, music and longer pause, and the output
  owner's sounds and music.
- `tools/check_jeopardy_judging.py --live` (paid, fictional input): about 60 responses through the real
  reply model. Results are below.
- First game on the robot (October 26, partial, without the dashboard): two picks, one ruling, one time's
  up. The wrong-answer buzz began 3.5 s after the response ended (1 s end-of-speech pause, 0.4 s
  recognition, 1.8 s model). Rex riffed on the category before each clue, which the owner did not want;
  picks now carry no words of his. On the second clue a brief sound while his head servos moved was heard
  as "Okay." (untrusted, no usable voice); the think music stopped while he weighed it (two model calls,
  about 4 s, the clock waiting) and restarted from the top. It now plays on through such a turn, and words the recognizer was unsure of get no second call.
- Not yet: a full game, several players, whether answers are heard over the thinking music and stings,
  the clip volumes beside Rex's voice, and the dashboard on the Apple TV.

### Paid judging check

`tools/check_jeopardy_judging.py --live`, October 26, fictional typed transcripts through the production
Session and state owner, the real reply model on the Fast tier as on the robot, 68 responses per run
(30 right, 26 wrong, 7 not responses, the rest phrasing, passes and unclear ones). About $0.0012 per ruling
(most of each request is cached), so a full game's rulings cost roughly 8-10 cents; the seven runs and the
held-out run cost about 60 cents together.

| Run | Change before it | Right | Right refused | Wrong credited | Talk ruled on |
| --- | --- | --- | --- | --- | --- |
| 2 | One verdict carrying phrasing too | 59/68 | 4 of 30 | 1 of 26 | 3 of 7 |
| 3 | Judge by sound, with examples | 62/68 | 1 | 2 | 3 (two cost money) |
| 4 | "Was this an attempt at all?" first | 57/68 | 2 | 5 ("Who is Ford?" for Nixon) | 1 |
| 5 | A written comparison first (normal effort; medium effort scored the same and was slower) | 60/68 | 4 | 1 | 0 |
| 6 | The attempt flag reworded; "(2 of)" answers spelled out | 62/68 | 3 | 2 | 0 |
| 7 | A separate "does it sound the same?" | **65/68** | **0** | **0** | **0** |

Run 1 stopped early on a harness error. Run 7's misses: "Who is Kareem?" (wrong) was taken as not an
attempt (no money, the clock ran on), and "our Kansas" for Arkansas and "the Percy crab" were ruled wrong.
The probe's sound-alikes had by then become the instructions' own examples, so 14 held-out responses named
nowhere in them followed: 13 right, no wrong answer credited; "What is a corn?" for an acorn was refused
(real-word sound-alikes are the hardest; "that was right!" overturns it). The same probe varied by several
cases from run to run with nothing changed, so single cases are noise; the counts are the evidence.

Model time per ruling in run 7 (request to the finished ruling): median 2.2 s, 90% within 2.7 s, longest
5.4 s. Add the 1 s end-of-speech pause and about 0.4 s of recognition: the right or wrong sound should come
about 3.5-4 s after someone stops talking. Not yet measured in a game.

## Not done

- Second robot game (October 26, three players, dashboard on): on the usual channel the room read -40 to
  -50 dB under the music instead of the usual -27 to -32, only fragments came through, and time ran out.
  Third game (`ff7fdce4`, three players): answers on channel 1 came through (mosquito, potato, Taj Mahal,
  each ruled within about 3 s), with `beam_gain` at its 16x ceiling throughout, so channel 1 sits at least
  24 dB below channel 0. Four failures, each since changed and not yet replayed on the robot:
  - **Freeze.** Stopping the think music for a voice cut its stream off mid-play; the board's microphone
    went silent, its automatic restart hung, and his time's-up line, his idle motion and shutdown all waited
    behind it. The music now ends by draining like any line; the cut-off is only for a stream that will not.
  - **Board read mid-clue.** A garbled "Make moves." during a rebound read out every remaining square,
    and "Nobody asked you what's on the board" read it again. The board, the next round and Final
    Jeopardy are now offered (and accepted) only between squares.
  - **Players lost.** A lone "Bret." was too brief for the voice check; "Exzunica and Jeremy." was cancelled
    because a face changed, by the guard that keeps private memory from a new audience, which a game turn
    never carries (now skipped in a game); and "Exudica. Bret Exudica and Jeremy." seated only Exudica and
    Jeremy. The setup tool now says names in a row are separate people.
  - **"United Kingdom" ruled wrong** for "Britain (or England)" (overturned on appeal). The judging rules
    now say another name for the same thing (official, short, nickname) is right.
- Fourth game (`0c8be385`, October 27, three players, about 48 squares): the music fix held, but the robot
  froze again at the end of the round when "Hey Rex" cut his score reading (likely the start of a score
  correction): the same microphone stall, so it is any sound cut off mid-play, not the music. Changed:
  - **Every cut-off line now stops like a finished one** (the sink ends the stream on a silent buffer
    instead of aborting it), for stop words, "Hey Rex" and the music alike.
  - **"Pass." was thrown out** four times as "not a voice" (scores .09-.3 on channel 1), so the clock ran
    out on players who had passed. Words recognized clearly on channel 1 now skip the voice check.
  - **"It's fantastic" opened IN THE AFRICAN COUNTRY** twice; picks now prefer the category that shares
    their words, and "that's the wrong category" puts the square back (`wrong_square`) for the real pick.
  - **"Skip." skipped the clue** (off the board, no rebound) when the player meant pass; a player's skip
    or pass is now a pass ruling, and skip_clue is only for a clue that cannot be played aloud.
  - **"Because he's an ally."**, said before the clue, was refused as a ruling and Rex announced "That
    ruling didn't stick; the clue is still open." Such refusals are now silent.
  - Not changed: rulings took longer than in the probe (sting about 3.5-4.5 s after speech, his words 5-7
    s), one request took over 12 s, and "Two hundred." between squares (channel 0) was also judged not a
    voice (.45).
- Boards repeat: the clue file has 97 complete opening boards (44 for Double Jeopardy), each game draws
  one at random and nothing remembers earlier games; the second robot game drew the first one's board.
- Resume after a restart (V2 had none either); a game lives only in memory.
- In Final Jeopardy responses are spoken in turn, so later players hear earlier ones.
- An answer shouted while Rex is still talking is not heard (the microphone is gated while he speaks);
  "stop" and "Hey Rex" still cut him off.
- A bare one-word answer that is a stop word ("Freeze!", "Quiet!") acts as a stop; the question form
  ("What is freeze?") avoids it.
