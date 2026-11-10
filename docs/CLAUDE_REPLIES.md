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
- Out of credit (`ClaudeCreditExhausted`) is not retried: Rex says a saved line, as for OpenAI.
  Subscriber API credit stops requests when it runs out; switching back is `llm_provider`.

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
