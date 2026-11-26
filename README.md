# DJ-R3X v3

This is the software that brings Rex to life. Rex is a life-size DJ-R3X droid (the DJ robot
from Star Wars' Oga's Cantina) built on a wheeled base. He listens, talks, recognizes the
people he knows, remembers what they tell him, looks around with his camera, moves his head
and arms, lights up, and drives around the house.

The program runs on a Mac inside the robot. Speech recognition, voice and face recognition and
most vision run on the Mac itself. Rex's replies come from a hosted AI model (OpenAI or Claude,
your choice) and his voice comes from ElevenLabs.

The earlier version of Rex, [djr3x-v2](https://github.com/bretbenz84/djr3x-v2), is no longer
maintained. Its design didn't hold up: many separate pieces each listened and reacted to speech on
their own, so they fought over what Rex said and did and were hard to fix. This version replaces it
from the ground up. It reuses some of v2's sounds, models and ideas but never depends on it while
running.

## What Rex can do

### Talk with people
- Just talk to him. He waits until you've finished your sentence, answers in his own voice, and
  his mouth lights and head move as he speaks.
- He keeps talking until he's finished his line. To cut him off, say "stop", "shut up", "be quiet"
  or "whoa", or say "Hey Rex" to get his attention.
- "Be quiet" or "shut up" also stops him from chatting or wandering off on his own until you invite
  him to talk again. "Stop" pauses that for a couple of minutes.
- He ignores sounds that aren't really speech, like his own motors or the dog barking.
- If one of the paid services runs out of credit, he tells you which account needs topping up
  instead of going silent.

### Know who he's talking to
- He learns people's voices and faces, so he can greet you by name and know who said what.
- Introduce someone ("Rex, this is Jeremy") and he starts learning that person's voice and face.
- He's careful about it: if he isn't sure who's speaking, he doesn't guess.

### Remember things
- Tell him about yourself or someone else ("Jeremy loves the Kings") and he remembers it, along
  with who told him. He brings these things up later, in other conversations.
- You can correct something he got wrong, or ask him to forget it.
- Leave a message for someone ("when Jeremy gets here, tell him dinner's in the fridge") and he passes it on
  the next time he sees or hears them. "Never mind" takes it back.
- Name a pet or an object and he remembers what it looks like.
- He remembers what he's said lately, so he doesn't keep repeating the same topics, and he notices
  how long it has been since he last saw you.

### Have a mind of his own
- When nobody needs him, he picks something to do: catch up with someone he knows, get to know a
  stranger, go look at something interesting in the room, or liven up a quiet moment.
- He's curious about people and pets, asks them about their lives now and then, and remembers the
  answers.
- If you ignore him he moves on, usually with a joke at his own expense.
- He can bring up news headlines, local weather and stories about things you've told him you're
  interested in, but only when a conversation has run out of steam.

### Put on a show
- **Impressions.** "Do an impression of Jimmy Carter" (or of someone he knows, or of you) and he
  writes a short parody and performs it in that person's voice. Every so often he slips a surprise
  cameo into the conversation too.
  - Celebrities and people he knows are voiced on the Mac itself, with no service needed. That's slow:
    a line can take 15 to 30 seconds to make, and he fills the wait with thinking chirps.
  - "Impersonate me" is different. He has you repeat a few lines after him, then makes a copy of your
    voice at ElevenLabs. That's quick, but it takes one of your ElevenLabs account's voice slots
    while Rex is running. He deletes the copy when he shuts down.
- **Accents and characters.** Ask him to talk with an accent (British, French, Irish and many more),
  like a pirate, or like a hype DJ, and he keeps it up for ten minutes. Ask whether he's gay and he
  goes full proud, rainbow lights and all. "Drop the accent" ends it.
- **Jeopardy.** "Let's play Jeopardy" for up to four players, with real boards, music, Daily Doubles
  and Final Jeopardy. Rex hosts and judges the answers. The board can be shown on a TV.
- **Celebrity entrances.** Tell him "treat PJ like Kings royalty whenever he shows up" and that person
  gets a grand entrance when they arrive. "Stop that bit" turns it off.
- **Music.** Ask for a song, album, artist, genre or playlist. He introduces it like a DJ and plays it
  from the Mac's Apple Music library. Say "Hey Rex" to pause it. While a song plays he dances to it.
- **Dancing.** "Dance for us" and he dances for 30 seconds, head, arms and lights. "Stop" or "stop dancing"
  ends it. "Wiggle your fingers" wiggles his fingers and wrists.
- He can tell when someone smiles at his jokes, and reacts accordingly.

### See and move
- He sees through his camera: ask "what do you see?" or "look left and see if the cup is there".
- His head turns toward whoever he's talking to and follows faces. When he's not talking his body
  keeps moving slowly so he feels alive. He can also pose, gesture and dance with his arms.
- The lights in his head and chest show when he's talking and how he's feeling.
- He drives on request ("come here", "turn around", "find the door and go to it") and sometimes on
  his own. He steers around obstacles and stops right away when told to. **He can't see stairs or
  ledges, so keep him away from them.**
- He can tell which room he's in and learns new rooms when you tell him.
- He can answer questions about his battery, charging, tilt, and the room's temperature.

### Help around the house
- **House devices.** Through Homebridge, he can tell you what the thermostat is set to or whether a
  light is on, and switch, dim or color lights and plugs or change the thermostat when asked. Locks,
  garage doors and alarms are look-only. He never changes anything on his own.
- **Sentry mode.** "Activate sentry mode until 7pm" and he guards the room: red lights, sweeping head.
  If a stranger shows up he roasts them, records video and sends a phone alert with a photo. Someone
  he knows can stand him down. When the time is up he shuts down.
- **Telepresence.** Call in from another Mac over the home VPN using the Rex Call app
  ([apps/telepresence](apps/telepresence/README.md)) to see and hear through Rex, talk through his
  speaker, aim his head and drive him.

## Using Rex

**Starting him.** Say **"wake up rex"**. A small background program on the robot listens for that
phrase, starts Rex, and also keeps his software up to date. You can also start him by hand from this
folder:

```sh
.venv/bin/python -m r3x live
```

His body wakes to a rising droid sound while everything loads. When his visor lifts, with the sound's
finishing note, he says a short line, and then he is listening.

**Stopping him.** Say **"shut down"**. In the terminal, `quit` or Ctrl-C also exits, and pressing
Enter cancels the line he's working on.

**Useful options and commands:**

| Command | What it does |
| --- | --- |
| `python -m r3x live --gui` | Opens a dashboard window with the camera view, the transcript, manual control of every motor, and a joystick for driving. |
| `python -m r3x live --camera` | Turns the camera on for this run only. |
| `python -m r3x doctor` | Checks that everything is installed and set up, without touching the hardware. |
| `python -m r3x house` | Lists the house devices Rex can see and how each one reads right now. Changes nothing. |
| `python -m r3x telepresence` | Starts telepresence mode so someone can call in. |
| `python -m r3x profiles list` | Lists the people Rex knows. See [core operations](docs/CORE_OPERATIONS.md) for reviewing and deleting people and recordings. |

The terminal shows the conversation as it happens. A detailed log of each session is saved under
`local/sessions/`.

## Setting up

### What you need
- An Apple Silicon Mac with Python 3.11.
- A microphone array (Rex uses a ReSpeaker) and a speaker.
- An ElevenLabs account (his voice) and an OpenAI or Anthropic account (his brain). See
  [AI and voice services](#ai-and-voice-services) below.
- Optional: a camera, a Pololu Maestro servo controller, the LED board, the drive base and radar,
  Homebridge, and the [ntfy](https://ntfy.sh) phone app for sentry alerts. Rex works without any of
  these; he just won't do the things that need them.
- A copy of [djr3x-v2](https://github.com/bretbenz84/djr3x-v2), set up per its own instructions, but
  only if you want room recognition or smile detection (they reuse its model files).

### AI and voice services

Rex needs two paid services: one AI model to think and talk, and ElevenLabs for his voice.

**OpenAI or Anthropic (Claude).** Either one can run Rex; pick it with `llm_provider` in
`local/settings.toml`. Whichever you choose handles his replies, his plans for what to do next, and
his camera looks. Put that service's API key in `local/secrets.toml`. If you use Claude and also add
an OpenAI key, Rex switches to OpenAI when the Claude credit runs out, and tells you he has.

- **Claude Max subscribers:** the Max plan includes $100 a month of Anthropic API credit, which you
  can use for Rex. Create an API key in the Anthropic Console under the account that has the credit,
  and set `llm_provider = "claude"`.
- **OpenAI:** pay-as-you-go API billing through the OpenAI platform.

**ElevenLabs.** Rex's voice, and his accents and character voices, come from ElevenLabs. A
**Creator** plan is enough. Normal use doesn't usually go over its monthly credits. Leave one voice
slot free for "impersonate me", which makes a temporary copy of your voice there.

### Install

From this folder:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -m pip install -r requirements-live.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python tools/prepare_core_models.py
.venv/bin/python tools/prepare_face_models.py
```

Optional extras:

```sh
# Dashboard window (--gui)
.venv/bin/python -m pip install -r requirements-gui.txt

# Impressions in other people's voices (a download of about 4.6 GB)
.venv/bin/python tools/prepare_voice_cloning.py

# Smile detection (needs the v2 copy; set V2 to its folder)
V2=/path/to/djr3x-v2
.venv/bin/python -m pip install --no-deps -r requirements-expressions.txt
.venv/bin/python tools/prepare_face_models.py --landmarker "$V2/assets/models/face/face_landmarker.task"

# Room recognition (needs the v2 copy)
"$V2/venv/bin/python" -I tools/export_place_model.py --cache "$V2/assets/models/mobileclip" --out local/models/places/mobileclip-s2.onnx

# With wheels: near corners seen by the camera (Depth Anything V2 Small, Apache-2.0, 49.6 MB; checked by digest)
mkdir -p local/models/depth
curl -L -o local/models/depth/depth-anything-v2-small-fp16.onnx \
  https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model_fp16.onnx
```

### Configure
1. Copy [secrets.example.toml](secrets.example.toml) to `local/secrets.toml` and fill in your keys.
2. Copy [settings.example.toml](settings.example.toml) to `local/settings.toml`. Every setting is
   explained in that file. The main ones:
   - `llm_provider`: `"openai"` or `"claude"`, which service writes his replies.
   - `camera_enabled`, `servo_port`, `led_port`, `drive_port`, `radar_port`: which hardware is
     connected. Leave a port empty to run without that part (for example, leave `drive_port` empty to
     keep the wheels off while testing).
   - Optional features such as `face_expressions_enabled` and `place_recognition_enabled`.
3. Run `.venv/bin/python -m r3x doctor` to check everything is in place.

To have "wake up rex" and the menu-bar helper apps run all the time, see
[operator services](docs/OPERATOR_CUTOVER.md). For phone alerts, see [sentry mode](docs/SENTRY_MODE.md).

## Your data stays private

Everything personal lives in the `local/` folder, which is never uploaded to GitHub: settings,
keys, the people Rex knows, their voices and faces, what he remembers, recordings and session logs.
The sounds and famous-voice clips that ship with the project contain nothing personal
([where the voice clips come from](assets/voices/README.md)).

## Testing

```sh
.venv/bin/python tools/run_checks.py              # the full automated test suite (about 2 minutes)
.venv/bin/python tools/run_checks.py --changed    # only the tests affected by your changes
.venv/bin/python tools/run_firmware_checks.py     # tests for the drive and LED board firmware
```

These tests use fake hardware and fake AI services. They never move the robot, use the microphone,
or spend money. Scripts in `tools/` named `check_*.py` that talk to real AI services cost a little
money each run and are run separately.

## How the program works

Rex's brain is a hosted AI model (a large language model, or LLM). The model can't hear, see or move
on its own. The program on the Mac does all of that and tells the model what is going on.

**What happens when you talk to him:**

1. The Mac hears you, works out when you've finished, turns your speech into text, and works out who
   is speaking from your voice and face.
2. The program packs up everything the model needs into one request:
   - **Who Rex is:** his personality and how he talks.
   - **The rules:** what he may and may not do, and how to use his abilities.
   - **His tools:** a list of 63 things he can do besides talk, each with a short description (all
    listed [below](#rexs-tools)).
     Examples: look through his camera, drive, move his arms, change the lights, save a fact about
     someone, play a song, check the thermostat, start Jeopardy or sentry mode.
   - **The conversation so far.**
   - **The moment right now:** who is in the room and who is speaking, what he sees, what he knows
     about the people present, the room he's in, the time and weather, and anything he's in the middle
     of doing.
3. The model sends back either words to say or a tool to use. If it picks a tool, the program checks
   the request is sensible and safe, carries it out, and tells the model how it went. The model then
   decides what to say about it.
4. The words go to ElevenLabs, and Rex speaks them while his mouth lights and head move.

Most lines take one model request. When nobody is talking, a separate, occasional request plans
what Rex might do next on his own.

### Rex's tools

Every request offers the model the full list below. For each moment, the program says which of them it
may actually use; Jeopardy's tools, for example, only work during a game. The program checks every tool
request before carrying it out.

**Talking and taking turns**
- `respond_with_gesture`: say a line with a matching feeling shown on his body (a head tilt, a wink, a gesture, lights).
- `respond_with_intent`: reply, and set or change what this conversation is about.
- `remain_silent`: say nothing, when someone is mid-thought, talking to a pet, or talking to someone else.
- `ask_question`: ask someone a question.
- `settle_question`: mark a question he asked as answered, declined or no longer needed.
- `stop_chiming_in`: stop starting conversations (or stop talking altogether) when asked to.
- `resume_chiming_in`: start talking again when invited.
- `conversation_feedback`: note when someone turns down his idea or closes a topic, so he drops it.
- `note_bit`: note whether a joke landed or flopped, so good ones can become running gags.
- `voice_mode`: switch an accent or character voice on or off.
- `impersonate`: do a voice impression of a celebrity, someone he knows, or the speaker.
- `callout`: set up, pause or delete a celebrity-entrance bit for a person or pet.
- `shut_down`: power down.

**People and identity**
- `introduce_person`: learn someone's name when they are introduced, or add a nickname.
- `ask_face_question`: ask who a face in view belongs to, or ask them to come closer.
- `rename_person`: fix the spelling of someone's name.
- `reset_face_identity`: forget a face that was matched to the wrong person.
- `cancel_association`: stop learning a voice when someone says it isn't who he thought.
- `forget_person`: forget a person entirely, with their recordings and facts.

**Remembering**
- `record_claim`: save a fact about someone, with who said it.
- `correct_claim`: update a saved fact, for example a plan that has now happened.
- `retract_claim`: withdraw a saved fact.
- `recall`: look up what he knows about someone, and who told him.
- `recall_conversations`: look up earlier conversations ("what did we talk about last night?").
- `remember_interest`: save something a person is into, which also gets them their own news.
- `remember_followup`: save a plan, birthday or celebration to ask about later.
- `relay_message`: keep a message for someone, or take one back.
- `deliver_message`: pass on a message to the person it was for, now that they are here.
- `remember_episode`: save a short summary of a shared moment from an earlier conversation.
- `retract_episode`: withdraw one of those summaries.
- `use_memory_opportunity`: bring up something he remembers when the moment suits it.

**Seeing**
- `inspect_view`: take a fresh picture (turning his head first if asked) and answer from it.
- `describe_view`: describe what's in the picture, and optionally remember the description.
- `use_visual_memory`: answer from something he saw recently, without taking a new picture.
- `revise_visual`: correct a description of something he saw.
- `retract_visual`: withdraw a wrong description of something he saw.
- `inspect_visual_memory`: look closely at a pet or object and compare it with ones he's been taught.
- `remember_object`: save a pet, object or room someone teaches him.
- `edit_object_memory`: correct or forget a pet or object he was taught.
- `defer_photo_teaching`: hold off saving a pet or object when it isn't clear which one was meant.
- `name_room`: learn the name of the room he's in.
- `correct_room`: fix or forget a room he got wrong.

**Moving and lights**
- `set_robot_pose`: move his head, visor or arms into named poses, one after another ("look down and wave"). It also holds something light: "can you hold this?" reaches his throttle hand out open while he
  looks down at it and says "Okay, ready."; the fingers close four seconds later (sooner if you say "there you go"),
  and the arm folds in holding it. "I didn't make it" opens it to try again. "Give it back" or "let it go" reaches it back out, and the fingers open when
  you say you have it or someone comes to take it. "Put it on the floor" lowers the hand and lets go. He
  remembers holding something between runs, so a restart never drops it.
- `set_robot_joints`: move any of his 18 servos to an exact position when asked.
- `shift_attention`: glance somewhere on his own, silently.
- `animate_lights`: play a light animation (sparkle, blink, or a mood colour).
- `drive_base`: drive the wheels: turn, move, face or approach a person or thing, or go find something.

**Information and the house**
- `weather`: look up current weather and the forecast.
- `news_today`: read the headlines and stories already fetched today.
- `use_observation`: bring up a news story or the weather in a lull.
- `calendar`: work out dates, weekdays and times exactly ("how many days until Christmas?").
- `systems_status`: read his battery, charging, tilt and the room's temperature and humidity.
- `house_devices`: check or change lights, plugs and the thermostat through Homebridge.
- `music`: play, pause or skip music from Apple Music.
- `personal_history`: look up his own backstory (Star Tours, the Cantina) before talking about it.
- `sentry_mode`: start guarding the room.

**His own goals**
- `wait_for`: hold off on the next step of his goal until the moment is right.
- `revise_agency_plan`: drop his current goal when it no longer fits, so a new one is planned.

**Jeopardy**
- `jeopardy_setup`: start a game and set the players.
- `jeopardy_pick`: take the square a player chose.
- `jeopardy_wager`: take a Daily Double or Final Jeopardy wager.
- `jeopardy_ruling`: rule an answer right or wrong.
- `jeopardy_control`: repeat a clue, read the board, move to the next round, and so on.

### How many tokens a line uses

AI services charge by the **token**, a chunk of text about three-quarters of a word long. Because the
program sends Rex's whole picture of the moment every time, the requests are large. In recent sessions on
Claude:

- A typical request was about **90,000 to 100,000 tokens** going in, and about 200 tokens coming back.
- Averaged over everything (including the background planning), that works out to about **80,000
  tokens sent for each line Rex speaks**.

### How prompt caching keeps that affordable

Most of a request is the same every time: his personality, the rules, the tool list, and the earlier
part of the conversation. Only the end, the moment right now and the latest thing said, is new.

Both OpenAI and Anthropic offer **prompt caching**. The service remembers the start of a recent request,
and when the next request begins exactly the same way it reuses that part instead of reading it again.
Reused tokens cost about a tenth of the normal price. The program is careful to keep the unchanging
parts first and identical between requests so they get reused. On Claude, the personality and tools are
kept for an hour and the conversation for five minutes.

In recent sessions about **three-quarters of the tokens sent were reused from the cache**. Only about
20,000 tokens per request were read fresh. That makes the input side of each request cost roughly a
third of what it would without caching. The first request after Rex starts, or after a long quiet
spell, pays full price to fill the cache.

### Ground rules the code enforces

- **One brain.** A single AI conversation decides what Rex says and does. Separate parts of the
  program handle hearing, recognizing people, remembering, and driving the hardware, and each one
  checks the brain's decisions before acting on them.
- **No pretending.** Rex never says he saved something, moved somewhere or did something until it has
  actually happened.
- **No guessing who's talking.** A voice that only sort of sounds like you doesn't become you.
- **Safety first.** Code, not the AI, enforces physical limits: obstacle checks, arm clearance while
  driving, and an instant stop when told.

## More detail

| Document | About |
| --- | --- |
| [DESIGN.md](DESIGN.md) | How the program is designed: who decides what, and how identity and memory work. |
| [Drive base](docs/DRIVE_BASE_INTEGRATION.md) | Wheels, radar and driving safety. |
| [Servos](docs/SERVO_INTEGRATION.md) | Head and arm movement. |
| [Lights](docs/LED_INTEGRATION.md) | Head and chest lights. |
| [Dashboard](docs/GUI.md) | The `--gui` window. |
| [Conversation memory](docs/CONVERSATION_MEMORY.md) | What Rex remembers and how. |
| [Proactive conversation](docs/PROACTIVE_CONVERSATION.md) | How Rex starts conversations and joins in. |
| [Impressions](docs/IMPERSONATION.md) | Voice impressions and cameos. |
| [Jeopardy](docs/JEOPARDY.md) | The Jeopardy game. |
| [Music](docs/MUSIC.md) | Playing music from Apple Music. |
| [House devices](docs/HOUSE_DEVICES.md) | Homebridge lights, plugs and thermostat. |
| [Sentry mode](docs/SENTRY_MODE.md) | Guarding a room and phone alerts. |
| [Room recognition](docs/ROOM_RECOGNITION.md) | Knowing which room he's in. |
| [Room navigation](docs/ROOM_NAVIGATION_PLAN.md) | Going from room to room: his map of spots, how he learns it, and what the runs showed. |
| [Telepresence](docs/TELEPRESENCE_PLAN.md) | Calling in through Rex. |
| [Replies from Claude](docs/CLAUDE_REPLIES.md) | Using Claude instead of OpenAI. |
| [Core operations](docs/CORE_OPERATIONS.md) | Managing the people Rex knows and their recordings. |
| [Operator services](docs/OPERATOR_CUTOVER.md) | The always-on wake word and menu-bar apps. |
| [Implementation status](docs/IMPLEMENTATION_STATUS.md) | What's finished and what's still being tested. |
| [Future features](docs/FUTURE_FEATURES.md) | Ideas for later. |
| [AGENTS.md](AGENTS.md) | Rules for anyone (or any AI) working on the code. |
