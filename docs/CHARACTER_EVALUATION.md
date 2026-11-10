# Rex character and model evaluation — October 1, 2026

(November 10: superseded for the character text and the model choice by the owner's blind
comparisons in [replies from Claude](CLAUDE_REPLIES.md).)

Decision: use `gpt-6-luna` with low reasoning for the conversational owner and
adopt the revised master persona. The prompt gives Rex specific musical tastes,
comic vanity, a fondness for unnecessary projects and a home life, with examples
of short, contextual teasing. It favors a reaction over reflex praise and lets him
return banter and disagree. Ordinary replies still use one primary generation;
existing bounded tool continuations remain. The specialized visual locator and
route planner retain their separately evaluated models.

The improvement is modest and subjective. The implementation assistant preferred
the revised prompt in **9 of 22 paired conversations**, the baseline in **5**, with
**8 ties**. This is an unblinded review, not a validated humor score or evidence
that real people laughed. Luna's cost advantage is much stronger evidence than
any claim that it is inherently funnier than mini.

## Method and results

`tools/compare_character_models.py` runs production Session and tools, temporary
SQLite memory, fictional Alex, and synthetic speech output. Seven development
conversations and four transfer conversations each ran twice per prompt, with
variant order alternating. Each variant carries its own delivered replies through
the same human sequence. Correction scenarios start with identical seeded history.
Three drafts were tried; all artifacts remain in ignored `local/validation/`.
The transfer set was reused during revision, so it is **not a held-out final test**.

Review criteria were contextual specificity, a recognizable point of view,
reciprocal teasing, curiosity/follow-through, and listening/boundaries. The final
paired run contains 128 evaluated turns, 64 per prompt, all completed. Reviews
retain losses rather than replacing them with favorable reruns:

| Scenario | Run 1 preferred | Run 2 preferred |
| --- | --- | --- |
| Network outage | Tie | Tie |
| Droid banter/activity/topic change | Baseline | Revised |
| Instrument correction | Tie | Baseline |
| Physics-to-film career | Tie | Revised |
| Character/backstory/home | Revised | Revised |
| Chore teasing followed by distress | Revised | Revised |
| Dog name | Tie | Baseline |
| Pasta/banter/music disagreement | Revised | Revised |
| Restoring a relative's radio | Baseline | Baseline |
| Imaginary argument/home | Tie | Revised |
| Plant correction/sister boundary | Tie | Tie |

A representative improvement, after the human jokes that Rex came out of a box:

- Baseline: “Fair point—I've never wrestled dough into submission. You've earned
  the craftsmanship bragging rights.”
- Revised: “I came out of a box; you bought a forty-dollar bowl of pasta. Let's
  not get smug about our origins.”

The revised prompt also produced: “The spice rack can't wrinkle itself in the
dryer, so naturally it got priority.” It still sometimes produces stock burns,
unnecessary praise and exaggerated metaphors. The baseline showed more curiosity
in both radio conversations. Both prompts recalled the instruments, plants and
dog name correctly in both runs; both respected the serious-disclosure and sister
boundaries. Revised Rex dropped the explicitly closed droid-building subject in
both runs; baseline revived assembly once. Both only fully started the bored
human's activity in one of two runs. These are small sample observations.

| Final paired Luna measurement | Baseline prompt | Revised prompt |
| --- | ---: | ---: |
| Turns / API calls | 64 / 65 | 64 / 66 |
| Estimated token cost | $0.05734 | $0.05832 |
| Median full-turn completion | 1.81 s | 1.86 s |
| p95 full-turn completion | 2.96 s | 2.96 s |

The original model comparison used the baseline prompt: 38 turns per model,
39 calls each; mini cost $0.1512 and Luna $0.0356, with median completion of
1.68 s and 1.71 s. A final model cross-check used the revised prompt:

| Revised-prompt model cross-check | GPT-5.4 mini | GPT-6 Luna |
| --- | ---: | ---: |
| Turns / API calls | 19 / 19 | 19 / 20 |
| Estimated token cost | $0.06752 | $0.01842 |
| Median full-turn completion | 1.22 s | 1.77 s |
| p95 full-turn completion | 2.85 s | 4.46 s |

Luna was about **73% cheaper** in this cross-check but slower. Mini could be more
flamboyant; neither was consistently funny. We choose Luna for competitive
character behavior at materially lower cost, not a demonstrated humor win.
Timings include tool continuations and synthetic output, exclude real ASR/TTS,
and some probes ran concurrently; these are indicative, not controlled hardware
latency benchmarks. Token estimates include cache writes and reasoning output.
At the checked standard rates per million tokens, mini input/read/output costs
$0.75/$0.075/$4.50; Luna input/read/write/output costs
$0.10/$0.01/$0.125/$0.50. See the official
[mini model](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[Luna model](https://developers.openai.com/api/docs/models/gpt-6-luna) and
[cache billing](https://developers.openai.com/api/docs/guides/prompt-caching).
Actual room-session costs also include voice services and other enabled providers.

## Integration checks and remaining limitations

- **797 synthetic blocked-I/O checks pass**, including a new provider-to-report
  contract retaining cache-write and reasoning counts without double-counting
  input/output totals. Fourteen adapter checks also pass after the final wording repair.
- **8/8 hosted participation checks pass** with the final persona and Luna:
  acknowledgments, unfinished answers, incidental sounds, substantive correction,
  questions, continuation, consent and an unanswered lull.
- Hosted recall exposed misleading empty-search follow-up wording. It now directs
  Rex to use the tool's already-filtered matches. A request to save an already
  supplied exchange now goes directly to `remember_episode`. Two final four-case
  runs both recalled the telescope exchange, handled the empty year and confirmed
  an actual episode save. One arrival greeted from memory; the other chose silence.
  Startup/live greeting consistency remains unvalidated.
- One paired correction exposed an internal feedback rejection aloud. The final
  continuation wording separates internal bookkeeping from failed human-requested
  actions. A focused rerun recovered both instruments without that disclosure in
  both candidate runs. This does not establish that disclosure can never recur.
- One candidate used Markdown emphasis; another invented having forgotten a name
  it recalled correctly. General grounding and formatting are still imperfect.
- No real microphone, camera, TTS, speaker, movement or robot hardware was used.
  Long captures rejected as truncated in the prior live session remain a separate
  listening problem. This change does not fix capture or establish live humor,
  acoustic identity, group participation or emotional benefit.

## Evidence and reproduction

Final baseline persona SHA-256:
`81756623a351fd64268e95acf66973731870aa686b2fb96517ac7fd4be29ba0c`.
Revised persona SHA-256:
`d0aaefbf367df22669889ad4c4666db06326b97fc9c9c1a13041efb767497ebc`.
The later feedback continuation repair does not change the master persona hash.

Ignored artifact directories under `local/validation/` contain frozen prompts,
full dialogues, event receipts, raw usage, summaries and HTML comparisons:

- Original model comparison: `character-models-d573545385ff4affa7f1624a2870b432`.
- Draft 1: `character-models-b6a15594ddd440569593074a559bb889` and
  `character-models-1213400c599f4e818c8cee7dd5fa57df`.
- Draft 2: `character-models-2c0d045578f14ccc9ffe32f8b424eaa2` and
  `character-models-45f39eb72b214aef888cd4109b395c3e`.
- Final prompt pairs, including `review.json`: `character-models-4243f01cfbc34a1d949a9387dea6ad73`
  and `character-models-397fc47b98f241e59b0a2f4c3742fd61`.
- Final model cross-check: `character-models-c7a223fdc89f4b25b89a68176ba11dd3`.
- Final listening rerun: `character-models-d9c61ea8dfcf4bb5b7a052794e7d049f`.
- Final participation: `turn-taking-55fbda9111dd4a13a5e884c5a78f260b`.
- Final memory runs: `conversation-memory-f02f3d5c84414c1f99ed3dd9b6d27642` and
  `conversation-memory-bea3b6fe0de146289b36db4d9fa4ee7f`.

Explicit paid probes, with credentials in the normal v3 local secret store:

```sh
.venv/bin/python -m tools.compare_character_models --repeats 2
.venv/bin/python -m tools.compare_character_models --baseline-persona /absolute/path/to/frozen-persona.txt --repeats 2
.venv/bin/python -m tools.compare_character_models --baseline-persona /absolute/path/to/frozen-persona.txt --transfer --repeats 2
```

Use `--case listening` to select a case in the chosen set. Probes do not change
production settings or operate devices. Reports are observations; review the
actual dialogue instead of treating a completed receipt as a quality pass.
