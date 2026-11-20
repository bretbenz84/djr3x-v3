# Replies from Claude, a slim character, and the caching leak — November 10, 2026

Owner, November 9: "My main issue with the program is that it's not very fun." Seven sessions
(October 31 – November 9, about 260 Rex lines) showed formula quips, missed gifts, assistant
habits the prompt forbade, and 39% of non-Jeopardy replies over the four-second line. Three
changes followed, at the owner's request. **Not yet run on the robot.**

## What changed

- **Whose models Rex uses is one setting.** `llm_provider = "claude"` sends Rex's replies to
  `claude_reply_model` (`claude-sonnet-5-5`, or the cheaper `claude-haiku-5-5`) through
  `ClaudeBrain` (`src/r3x/adapters/claude.py`); `"openai"`, the code default, keeps `gpt-6-luna`.
  The reply request is the one `OpenAIBrain` builds; only the wire form differs. The goal
  planner, picture locator and news search follow the same setting (below). Only the chosen
  provider's key is needed in `local/secrets.toml`; readiness reports it missing.
- **The character is 3,700 characters instead of 16,700** (`CHARACTER` in `hosted.py`): who Rex
  is and how he talks, with no example lines (owner: examples "will end up being what gets
  repeated") and no dated incident rules. Kept by name: bodies are fair game, no "You're right"
  openers, no tacked-on question, callouts, curiosity on his own turns, the clock as a
  once-a-visit joke, about fifteen words a line. The planner shares this text.
  The operating rules after it and the tool descriptions are unchanged.
- **The caching leak.** In a month of logs 832 of 2,350 replies reused none of the stored prompt,
  768 of them within a minute of the previous reply. The famous-voices block opened a person's
  turns and was missing from Rex's own, so the start of the request kept changing. Once seen it
  now opens every request of the run. On Claude the tools and persona are stored for an hour
  and the history up to the newest heard line for five minutes; the notes for the moment follow
  and are never stored.

## Claude differences that matter

- Claude cannot be offered a subset of stored tools, and Sonnet 5.5 cannot be forced to call
  one. The moment's tools are named in the last note and the answer is checked: a tool that is
  not on offer is refused and asked again once (`tool_unoffered`), then fails the turn; a turn
  that must use a tool and answers in words fails (`ToolRequired`). Nothing is said or done first.
- With tools on offer a prose reply is spoken when it is complete, not word by word: Claude may
  write words and then call a tool, which the session treats as a broken turn once words are
  heard (`reply_text_beside_tool` counts it: 1 of 46 calls). Reply-tool text still streams.
- A refusal (`ClaudeRefusal`) is a silent failed turn; no fallback model is configured.
- Out of Claude credit (`ClaudeCreditExhausted`), Rex moves to OpenAI (owner, November 10: the Claude
  credit comes with his Max plan, so it is used first, then the prepaid OpenAI balance). The first model
  to find the credit gone flips one shared switch (`ModelSwitch`, trace `model_provider_switched`) and
  every hosted model uses its OpenAI counterpart, with the same slim character, for the rest of the run;
  a restart tries Claude again. A reply that had said nothing yet is asked again of OpenAI, so the turn
  is still answered; one cut off after words were heard is not repeated. After that turn Rex says a
  saved line once a run (`ClaudeCreditSwitchedToOpenAI`). Without an OpenAI key there is no switch
  and he says the old out-of-credit line. Checked with synthetic models only.

## Evidence

All hosted runs used the production Session, temporary stores, synthetic identities and text
output. No microphone, camera, voice or robot hardware. Private receipts:
`local/validation/fun-comparison/`.

**Blind comparisons, judged by the owner** on 26 of his own lines from six evenings, replies
unlabelled and shuffled; each arm carried its own conversation, once.

| Round one | Picks | First word, median | Per 1,000 replies |
| --- | ---: | ---: | ---: |
| Old character, `gpt-6-luna` (fast tier) | 3 | 1.6 s | $3.67 |
| Slim character and 22 tools, `gpt-6-luna` | 5 | 1.0 s | $2.23 |
| Same, `gpt-5.4-mini` | 6 | 1.2 s | $8.77 |
| Same, `gpt-5.6-terra` | 12 | 1.0 s | $44.19 |

| Round two (same slim arm) | Picks | First word, median | Per 1,000 replies |
| --- | ---: | ---: | ---: |
| `gpt-5.6-terra` (standard price) | 7 | 1.1 s | $22.19 |
| `claude-haiku-5-5` | 6 | 0.8 s | $0.92 |
| `claude-sonnet-5-5` | 8 | 0.8 s | $14.65 |
| `claude-opus-5-5` | 4 | 2.3 s | $32.33 |

Twenty-six picks do not separate 8, 7 and 6. Claude replies averaged 23–29 words against 11;
the character now asks for about fifteen. The blind arms sent 22 tools; production sends all 59.

**Production connection, `claude-sonnet-5-5`, the same 26 lines:** no silent turns, 20 words a
reply on average, complete reply in 1.5 s median, 2.7 s at the 90th percentile and 3.4 s at
worst, 69,900 input tokens a call with 95% reused inside a conversation. The live robot's
`gpt-6-luna` took 2.0 s median and 4.3 s at the 90th percentile to its first word.

**Behaviour probes** (single runs unless two are shown; single-case differences are noise):

| Probe | `gpt-6-luna`, old character | `gpt-6-luna`, slim | `claude-sonnet-5-5`, slim |
| --- | ---: | ---: | ---: |
| `check_meaning.py` | not run | 13/14 | 13/14, 13/14 |
| `check_addressee.py` | not run | 21/24 | 23/24, 22/24 |
| `check_proactive_conversation.py` | 5/9 | 5/9, 5/9 | 8/9 |
| `check_action_requests.py --live` | not run | 31/31 | 31/31, 31/31 |

`natural_introduction` fails in every arm, as before. Claude's proactive miss is a spoken
acknowledgement of a quiet request where the probe expects none. Before the refuse-and-ask-again
step Claude scored 6/9 there and 12/14 on meaning.

**1,799 blocked-I/O checks pass**, ten of them new (`tests/test_claude_replies.py`): the
request's stored and unstored parts, earlier tool calls and camera views, streamed reply-tool
words, the unoffered-tool retry, required tools, refusals, out of credit, the settings choice,
and the famous-voices block opening every request.

## Everything on Claude, and the joke writer retired (owner, later on November 10)

Owner: "I wanted you to switch everything to Claude", and "let's also retire the expensive joke
writer, as it isn't used much and wastes tokens."

- **Goal planner:** `ClaudeAgencyPlanner`, `claude-sonnet-5-5`, medium effort. It asks for one
  JSON plan in a fixed shape (`output_config.format`) rather than a forced tool call, which
  Sonnet 5.5 refuses. Claude's fixed shapes refuse size limits, so those are sent as written
  notes and the plan owner's own validation still decides. One paid run of
  `check_agency_beats.py` (fictional conversation, replies also on Claude): one planner call,
  a valid seven-step plan, 7 of 7 beats spoken, none recited or repeated; 12,100 input and 870
  output tokens for the plan.
- **Picture locator:** `ClaudeVisualLocator`, `claude-haiku-5-5`, no thinking. Four paid looks at
  one saved room photo: a chair and the dog found, a purple elephant reported absent, a route
  proposed; 1.2–2.1 s each. Box accuracy against the previous model was not measured, and no
  drive used it.
- **News search:** `ClaudeNewsSearch`, `claude-sonnet-5-5` with Anthropic's web search
  (`web_search_20250305`, at most six searches). Paid runs: 5 of 5 daily stories and 3 of 3 topic
  stories kept by `news.stories`, 19–22 s. The newer filtering search returned 1 and 2 of 5
  daily stories and is not used. About 45,000–70,000 input tokens a search, a few a day.
- **Joke writer retired:** `JokeRoom`, its prompt, `joke_writer_enabled`, the `joke_material`
  context and the reply tools' `used_material` field are gone. The record of his bits
  (`note_bit`, running gags, bits that flopped, spent joke shapes) stays. `check_humor.py` now
  compares personas only.
- 1,797 blocked-I/O checks passed after the change (writer tests removed, six added for the
  Claude helpers); `tests.test_claude_replies` was rerun after the last search adjustment.
- With `llm_provider = "claude"` nothing calls OpenAI. Speech is still ElevenLabs; hearing,
  faces and voices are local.

## Cost, estimated

From October 10 – November 9 (2,350 replies, 79 sessions, test runs included): `gpt-6-luna`
replies cost about $14 and the planner and writer about $9. Sonnet replies at this connection's
size are estimated at $55–60 a month: about $31 for 2,350 replies with the prompt reused and
about $26 for roughly a hundred cold starts, each storing 66,000 tokens for an hour. Haiku would
be a few dollars. Nothing here is measured on the robot; the Anthropic console is the record.
Tool descriptions are about 60% of each request and are the next thing to trim.
The planner, locator and news search now draw on the same Anthropic credit (a plan is about three
cents at list prices; a news search about ten to fifteen); their monthly total is not estimated.

## Not done

- No live conversation: humor, length, speed with voice, and group behaviour are unjudged.
- Jeopardy, sentry mode, photo teaching, driving, impressions and callouts were not probed on Claude.
- The hosted check tools still build the OpenAI classes themselves; Claude runs used a private launcher.
- The Claude locator has not guided a drive, and the planner was run once.

## November 16: Claude's own prompt and quicker replies

Owner, after run `dc1543d7`: the time to first speech "has gotten considerably worse", Rex speaks "random
news headlines out of context a lot", and "The switch away from OpenAI is starting to show lots of
issues." Ruling: "keep the ChatGPT optimized one and make the program use the new Claude one, copy the
prompt instead and branch it depending on the setting of which LLM to use. Don't remove the good prompt
for ChatGPT."

**What the logs showed** (ordinary replies, no driving or Jeopardy, end of speech to first sound):

| | OpenAI, Oct 25 – Nov 9 | Claude, Nov 14 – 16 |
| --- | ---: | ---: |
| Replies | 183 | 68 |
| Median | 3.7 s | 4.7 s |
| Over four seconds | 37% | 69% |
| Slowest tenth from | 6.1 s | 7.2 s |
| First words from the model, median | 2.0 s | 2.7 s |
| Output tokens before the first reply ended, median | 86 | 147 |

Claude thought before answering; requests were ~96,000 tokens with ~23,000 not reused. The goal planner
on Claude built 12 of 34 goals on headlines (OpenAI: 1 of 22), and three per-turn notes told a beat that
no longer fit to say "a news item" instead, which Claude did ("Meanwhile, WDW News Today says..." in the
middle of a story about the dog; snow in northern England after "hello").

**What changed**

- **`claude_prompt.py`** holds Claude's copy of the character, persona, voice reminder and planner
  instructions, and Claude's versions of the notes named `agency_beat`, `beat_before_tail` and
  `beat_moved_on`. `OpenAIBrain._prompt(name, text)` returns OpenAI's own text; `ClaudeBrain._prompt`
  returns the copy's. A note the copy does not name is the shared one. Four built OpenAI requests (plain
  turn, beat, inspect beat, an answered question) hash identically before and after.
- **In the copy only:** a headline is not a reason to speak; at most one a visit, only with nobody mid-
  subject and touching the people here or what was said, never in a greeting, to an empty room or as a
  "meanwhile"; a beat that no longer fits never falls back on a headline; the planner makes no goal of a
  headline that touches nobody here, none with nobody here, and plans a roll toward a person only when
  that person is in view; a lone word after a sentence is not an introduction, `NameHeardOverWheels`
  means ask again, and a name the person rejects is a mishearing, never another person.
- **Replies start without thinking first:** `thinking: {"type": "between_tools"}` for Sonnet 5.5 replies
  (the earlier note that Sonnet 5.5 could not switch thinking off was out of date). The planner, news
  search and locator still think.
- **Not changed:** plain words still wait for the end of the answer when tools are on offer. Most lines
  are one sentence, so speaking them as they come would gain about 0.2 s, and without thinking first a
  short "Let me look." before a tool would be heard and the look dropped as a broken turn.

**Evidence:** blocked-I/O checks only (1,962 pass). Neither the speed nor the wording was measured on a
paid model or the robot; quality without thinking is unjudged. The planner's change takes effect on its
next plan; Claude's stored prompt is rebuilt once (the first reply of a run pays for it either way).

**Still open:** the ~23,000 tokens read fresh on every request (the notes for the moment, ~50,000
characters) are the next latency and cost lever. Claude's per-moment notes other than the three above
are still the shared ones.

## November 19: news in a stalled conversation, ride lines, the first-flight goodbye

Told in the copy was not enough: run `a37ed477` planned a goal on a headline with nobody in the room. Code now
offers headlines only once a conversation has stalled (`observations.news_moment`), so neither provider sees
one at startup or in a greeting. Claude's copy asks him to share one as "have you heard about...?" rather than
reading it with a joke, and to give a shutdown goodbye in the shape of the ride's closing apology. Two notes
of Claude's own, `signature_line_bad_feeling` and `signature_line_meant_to`, ride on the voice reminder only
while `signature_lines` says that line has rested; OpenAI's `_prompt` returns nothing for them.

## November 19: his working-out is never spoken

Answering without thinking first, Claude now and then writes its reasoning ahead of the line, a paragraph and
a blank line before what Rex says. Run `45da7ce9` read out "The speaker is unresolved... It's addressed to me in
context." before the joke; runs `ea8011d1` and `3a09bdde` had the same shape. Of 928 replies since October 25,
those three were the only ones with a paragraph break. When a reply waits for the end of the answer (tools on
offer) and has a paragraph break, only the last paragraph is spoken; the rest goes to the log as
`reply_working_out_unsaid`. Claude's copy also says decisions about who spoke, who is addressed and which tools
are offered are made silently. OpenAI's wording is unchanged.

**Evidence:** blocked-I/O checks with a faked stream only. **Still open:** a reply with no tools on offer is
spoken as it arrives and is not filtered; check `reply_working_out_unsaid` rows in coming runs for any real
line that was dropped.
