# Proactive core implementation plan

September 12, 2026. The user authorized planning and beginning implementation.
Existing documentation edits were already present in the working tree. This work
uses v3's own Session and providers; no v2 extraction or personal-data import.

## Operating goal

Rex normally runs when Bret chooses to demonstrate him to friends. He should act
like an engaged friend: an initial opportunity after roughly **7–10 seconds** of a
genuine lull, rather than waiting 15–60 seconds. Start at eight seconds, measured
after both recent human speech and completion of the preceding interaction. That
is opportunity timing, not a promise of audible output at eight seconds: model
and speech latency follow. The model still interprets whether this is a welcome
opening. See [design](PROACTIVE_CONVERSATION.md) for delight and emotional sensitivity.

## Stages

| Stage | Deliverable | Verification and completion gate |
| --- | --- | --- |
| 1. Shared initiative foundation | Generalize the existing arrival event and Session path; add visible-group lulls, shared history/outcomes, explicit session quiet/resume, sensitivity guidance, bounded pacing and cancellation | Production Session/worker tests with blocked I/O, hosted synthetic semantic probes, documented live limits |
| 2. Conversational feedback | Source-linked interpretation of invitation responses, topic closure/repetition and per-participant preferences; preserve human-to-human floor context without a universal metadata call | Multi-person sequences where unrelated speech does not settle a question, quietness is not disengagement, and correction/forgetting remove stale context; compare model-call counts and latency |
| 3. Contextual memory opportunities | Relevant accepted plans, callbacks and celebrations with source/audience constraints; diverse bounded opportunity selection | Source/subject/discretion scenarios, dates and corrections across restart; no automatic biography from a proactive remark |
| 4. Live social acceptance | Repeated demonstrations on the dev Mac, then combined-load M2 sessions | Human ratings of delight, warmth and timing alongside interruptions, repetition, missed openings, attribution, median/p95 latency and playback |
| 5. Additional observation sources | Explicitly scoped news/weather, objects/animals/gestures and eventual physical reactions through the same opportunity path | Verify each provider's facts/freshness and resource cost; preserve all core acceptance contracts before adding the next |

Stages 1–4 are core work. Stage 5 records future integrations and is not blanket
authorization to add providers, download models or operate hardware. Stages are
not parallel conversation loops. No mandatory classifier/planner call is added
before ordinary replies. Keep the semantic feedback design bounded; an optional
state tool must not turn every natural response into two model generations.

## First slice boundaries

Start with the camera already used for arrivals. Lulls require a fresh stable
visible audience; this avoids claiming that some other person's recent speech
proves a named target is still present. Names are optional and the model considers
the group without choosing the most talkative person in Python. Voice-only
presence and additional scene interpretation remain later measured work.

The first ledger records attempts and delivery outcomes, not inferred emotions
or response success. Responses remain explicitly unassessed in that ledger;
the model reads the original conversation. At most one lull consideration follows
each processed source turn, while a rolling cap spans all source turns and arrivals.
This is an initial bound, not semantic engagement detection. A failed/declined
attempt consumes pacing but never becomes an ignored spoken question. Retained
source links/outcomes are bounded and cleared on forgetting.

An explicit request to stop unsolicited speech creates a session quiet boundary.
Ordinary direct questions still work; an explicit invitation to initiate again
clears it. The initial scope is the shared room for this run. Topic-specific and
per-person boundaries stay in original history until stage 2; do not silently
persist a preference or infer one from low energy. Terminal stop retains its
existing temporary cancellation hold.

Synthetic fixtures cannot establish mood improvement or actual humor. Hosted
text probes should be read for relevance and restraint; their output status alone
is not a quality score. No live microphone, camera or playback is needed to build
or run these first checks.

## Evidence

- Baseline: 157 blocked-I/O checks passed before source changes.
- Stage 1: implemented against v3 base `bdb41d1d31f265cbe9aced6e7943ce63af9822d4`;
  **174 blocked-I/O checks pass**, including the new documented scenario through
  production Session. Source preservation, shared one-call replies/lulls, eight-second
  scheduling, frozen evaluation, quiet/resume, audience/speech/queue cancellation,
  failed attempts, empty model output, rolling pacing, forgetting and restart are
  exercised with synthetic evidence and temporary databases. `git diff --check` and
  115 local Markdown links/anchors pass. No device or real acoustic model was used.
- Final hosted original wording: **8/9** expected behavior checks; alternate wording:
  **8/9**. Each used ten actual hosted requests for nine judged turns (one explicit
  resume tool continuation), plus six scripted seed generations. Both produced
  contributions for receptive tiredness and a completed topic, and respected grief,
  thinking pauses and explicit quiet/resume. Original wording still intruded on a
  question addressed to another human; alternate wording added a reassurance after
  an unanswered invitation. These are unresolved semantic misses, not passing
  silence decisions. Earlier failures and the initially blocked-network run remain
  in private validation; successful final runs do not erase them.
- Hosted judged-turn wall time, including applicable tool continuation and silent
  text output: original median/max **0.858/1.509 seconds**, alternate
  **1.085/2.317 seconds**, nine samples each. These exclude capture, ASR, TTS and
  playback; they do not establish live first-audio latency or M2 resource impact.
- Existing hosted continuation/introduction/addressee regressions: **13/14**;
  an explanation intended for a parent still received a reply. This intermediate
  run preceded the final lull-specific guidance. No claim of universal group
  participation or a matched improvement over the previous prompt is made.
- Private evidence: `local/validation/proactive-a3f6696b32aa441d9716151111fcfebe/`
  (final original), `local/validation/proactive-21404da1c5b1414d9c28a5105a18e577/`
  (final alternate), and `local/validation/135d837083384fc9a10b785695e10d0b/`
  (existing regression suite). Actual hosted calls used synthetic text, in-memory
  stores and TextOutput; no microphone, camera, TTS, personal DB or robot was used.
- Stage 1 social/live acceptance remains open. Read the sample replies and evaluate
  humor, relevance and timing with people. A status match is not a humor score;
  no mood improvement or real speaker discrimination was measured.
- Stages 2–5: planned, not implemented by this first slice.
