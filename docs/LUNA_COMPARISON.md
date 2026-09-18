# GPT-5.6 Luna trial — September 17, 2026

User-requested comparison against the current GPT-5.4 Mini. Production model and
reasoning remain unchanged (`gpt-5.4-mini`, low). No hardware was operated and no
validation entries were imported into the live gallery.

## Method

The production Session, OpenAI adapter, state executor and photo tools ran with
silent TextOutput, an in-memory core database and separate file-backed galleries.
Both models received the same five typed turns: explicit coat/location teaching
for two dogs, a breed update, then two spatial recognition questions. The session
and gallery were reopened before recognition, using a different saved camera frame.
Both used low reasoning and the same prompts, tools and output limits. Tool choices
and subsequent history differed naturally. The two test processes ran concurrently;
this is a small functional comparison, not a randomized latency benchmark.

The HTTP stream was instrumented without changing production code. First-token
time runs from sending the request to the first text or function-argument delta;
it excludes connection events and internal reasoning events. Tool argument tokens
are not audible speech. Completion times include any image follow-up but exclude
microphone endpointing, ASR and TTS/playback. Model memory and CPU use were not
measured. No on-device models were loaded for inference; saved detections were used.

## Paired results

| Measure | GPT-5.4 Mini | GPT-5.6 Luna |
| --- | ---: | ---: |
| Human turns | 5 | 5 |
| API calls, including image continuations | 8 | 10 |
| First streamed token, median per call | 1.01 s | 1.51 s |
| First streamed token, maximum | 1.60 s | 2.27 s |
| Turn completion, median | 3.16 s | 4.81 s |
| Turn completion, maximum | 4.26 s | 6.21 s |
| Input tokens | 78,023 | 99,133 |
| Cached input tokens | 0 | 0 |
| Output tokens | 1,178 | 1,313 |
| Estimated total API cost | $0.06382 | $0.02140 |
| Named entries saved | 2 | 2 |
| Spatial answers correct | 2/2 | 2/2 |
| Fresh inspections for spatial answers | 1/2 | 2/2 |
| Breed update actually saved | Yes | No |

Costs use the standard published input/output rates; this is an estimate from
returned usage, not an account invoice. Luna used about 66% less money, despite two
additional image continuations. First-token samples include both tool and text
outputs. Five turns cannot establish tail latency or general recognition accuracy.

Luna's breed update selected photographic teaching instead of a note edit. The
existing-reference save path deliberately preserves notes, yet Luna's supplied
acknowledgment claimed the breed had been remembered. It also echoed the user's
teaching sentence as one save acknowledgment. These are unresolved model/tool
interaction failures. Mini skipped a fresh inspection for the final spatial answer;
its correct answer therefore does not prove fresh visual recognition on that turn.

An earlier nine-turn Luna replay used the original ambiguous size descriptions.
It asked for clarification, later saved one reference after explicit coat teaching,
handled the unfinished clause silently, and did not apply the unnamed continuation
to the wrong record. This does not establish acoustic performance: all inputs were
text, and the original live interruption timing was not recreated.

## Receipts and conclusion

Private scripts and results remain Git-ignored:

- `local/validation/object-memory/model_comparison.py`
- `local/validation/object-memory/compare-gpt-5.4-mini-69e3d0a4946c4618a47f4a1df8eff5a8/`
- `local/validation/object-memory/compare-gpt-5.6-luna-c6efc17acb1b42f887eb50527129c26e/`
- `local/validation/object-memory/luna-7fa6cc85efb840c981331a2e7019929b/`

Luna is cheaper and showed more consistent fresh inspection in this small trial,
but was slower and failed the breed-update contract. The trial does not justify
claiming it is an overall improvement. Production was not switched automatically.

Pricing sources: [Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna),
[GPT-5.4 Mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini).

## Terra follow-up — September 17

The user next requested GPT-5.6 Terra. The same five-turn script, saved images,
restart procedure, isolated storage and low reasoning setting were used. Terra ran
later and alone; the original Mini/Luna trials ran concurrently. This is not a
randomized or statistically powered comparison. Production remains unchanged.

| Measure | GPT-5.4 Mini | GPT-5.6 Luna | GPT-5.6 Terra |
| --- | ---: | ---: | ---: |
| API calls | 8 | 10 | 9 |
| First streamed token, median | 1.01 s | 1.51 s | 1.20 s |
| First streamed token, maximum | 1.60 s | 2.27 s | 1.92 s |
| Turn completion, median | 3.16 s | 4.81 s | 3.27 s |
| Turn completion, maximum | 4.26 s | 6.21 s | 5.32 s |
| Estimated five-turn cost | $0.06382 | $0.02140 | $0.18589 |
| Spatial answers correct | 2/2 | 2/2 | 2/2 |
| Fresh inspections for spatial answers | 1/2 | 2/2 | 2/2 |
| Breed update saved | Yes | No | Yes |

Terra saved both reference entries, preserved appearance when adding breed, and
used fresh inspection for both correct recognition answers after restart. All five
turns completed. It had the cleanest behavior on this small set, with median turn
time close to Mini. Its observed cost was about 2.9 times Mini and 8.7 times Luna.
This does not establish general two-dog accuracy or live speech latency.

Returned Terra usage: 87,889 input tokens, zero cached tokens, 843 output tokens.
Cost estimate uses standard $2/M input and $12/M output rates from the
[Terra model page](https://developers.openai.com/api/docs/models/gpt-5.6-terra).
Private receipt and summary:
`local/validation/object-memory/compare-gpt-5.6-terra-1897d60749ed43bcab74512e1fbe724f/`.
No microphone, fresh camera capture, playback or robot motion was used; the live
photo library was not modified.
