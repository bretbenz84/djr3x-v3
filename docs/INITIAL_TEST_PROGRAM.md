# Initial test program and acceptance plan

### September 25 LED integration

The [LED adaptation](LED_INTEGRATION.md) adds lifecycle lights, playback PCM-driven
speaking and one-call model-selected animations. **548 blocked-I/O checks pass**,
including 20 new LED contracts with fake serial/audio and production Session/output.
No hardware or hosted model was used. The refreshed cutover bundle is prepared only;
V2 services remain unchanged. Visual, timing and live handoff gates remain pending.

### September 25 servo foundation

The separately authorized [servo integration](SERVO_INTEGRATION.md) adds a Maestro
executor, concurrent startup/shutdown/park including throttle, playback-driven talking
motion, explicit live composition, resource handoff and held-microphone enrollment.
**528 blocked-I/O checks pass**, including 48 new synthetic servo/operator/robot
contracts. A separate real wake-model benchmark used synthetic silence only. No
hardware, hosted provider or personal database was used. V3 operator jobs and rollback
artifacts are prepared; the owner asked to keep V2 running until later commissioning.
Live handoff/mechanical/acoustic acceptance remains pending; this does not establish
the original core's group-acceptance gate.

Current scope: [implementation checklist](IMPLEMENTATION_STATUS.md). Dated sections
below preserve the evidence and limitations of their respective checkpoints.

Status: core implementation through slices 0–4 is present; live device and group
acceptance remain open. The user explicitly requested completing the core overnight
before the next live test, so implementation continued past the original slice-1
live gate without claiming that gate had passed. Initial-program slice 5 (wider expansion) and the full robot remain
deferred; this is distinct from the implemented proactive stage 5 observation inputs. [Operations](CORE_OPERATIONS.md) contains the current commands.
The September 12 [proactive conversation design](PROACTIVE_CONVERSATION.md) adds
broader initiative to core design and acceptance. The first shared arrival/lull
slice is now implemented; its evidence is recorded below. Historical slice evidence
retains its original scope and does not validate these new behaviors.

## The first question to answer

Can Rex conduct an ordinary conversation with Bret and Jeff, distinguish speakers
when the evidence supports it, handle uncertainty naturally, and preserve a few
correctly attributed memories across a restart—and contribute welcome humor,
warmth and initiative without waiting for someone to speak first every time?

The first program is a replacement-core experiment with real microphone input and
Rex's voice. A typed chat demo is useful for plumbing but cannot answer this question.
No motors, decorative servo control, or games are needed. The explicitly authorized
face-arrival greetings and visible-group lulls are now shared proactive entry points.
Broader participation is developed with the core under the September 12 design;
it does not require importing every v2 behavior or provider first.

## Implementation slices

| Slice | Deliverable | Gate before expanding |
| --- | --- | --- |
| 0. Independent boot | New environment, small settings schema, explicit provider lifecycle, scratch paths, startup report, blocked-I/O test fixtures | Importing core modules loads no models/devices; no v2 imports or writable v2 DB connections |
| 1. Audible loop | Capture, batch ASR, main model reply, ElevenLabs adapter, single output owner, cancellation, original/played-text trace | A real spoken exchange works; speech completion and failures are visible; only selected models load |
| 2. Speaker evidence | Labeled-audio replay, session tracks, enrolled profile copies, unknown option, change/overlap diagnostics | Distinguish encoder, segmentation, and decision-rule failures; establish a held-out Bret/Jeff baseline |
| 3. Meaning and memory | Basic camera tracks, identity/reference tools, pending questions as context, claim store, correction and recall | Full introduced-person/correction scenarios run through the real core; persistence survives restart correctly |
| 4. Group acceptance | Repeated dev-Mac sessions, saved-audio replay, load/failure tests, diagnostics review; September 12 proactive policy and scenarios developed alongside the core | Freeze and satisfy the declared evaluation gates, including welcome initiative and sensitive restraint, or identify a specific remaining limitation |
| 5. Controlled expansion | One additional useful capability through typed tool/results | Its addition preserves prior group, memory, delivery, and latency gates; retire any replaced implementation |

Begin speaker data collection as soon as slice 1 works; do not finish a GUI or rich
memory before investigating the weakest link. Initial design and integration research
should be a short bounded pass, followed by a running loop. Full v2 feature parity
is a later backlog, not the first release definition or a promised completion date.

## Suggested implementation shape

This is a responsibility sketch, not a requirement to scaffold every file at once:

```text
src/r3x/
  app.py                 Explicit boot/close and dependency construction
  contracts.py           Typed observations, proposals, results, and identifiers
  session.py             One conversation state owner and turn lifecycle
  speakers.py            One tracker for named/unnamed voices and associations
  brain.py               Main model contract and allowed semantic tools
  memory.py              Small attributed-claim and recording store
  output.py              One speech scheduler and delivery receipts
  adapters/              Capture, ASR, voice encoder, TTS, camera, optional DoA
tests/
  fixtures/              Synthetic and sanitized text/evidence scenarios
local/                   Ignored scratch state, imported copies, audio, reports
```

Keep a narrow interface between pure core logic and providers. Camera/ASR/model
workers return events to session ownership; they cannot change profiles or speak.
Core import tests must work with all heavyweight model modules unavailable.

## Slice 0 implementation evidence

September 10, 2026: implemented against v3 base revision `a2c5e2f` in the working
tree. The tree was clean before implementation. No v2 code or personal data was
extracted; no v2 environment or database is used.

- Python 3.11.9 in the checkout's new `.venv`; editable `djr3x-core` package.
  Zero third-party runtime dependencies; packaging versions are pinned separately.
- `settings.py`: immutable, strict profile settings and local scratch paths;
  existing symlink redirection is rejected before directory creation.
- `providers.py`: typed capability declarations, dependency metadata inspection
  without provider imports, explicit start/cancel/close, reverse cleanup including
  partial starts, duplicate-owner rejection, and structured failure results.
  Exception types are retained without potentially sensitive exception messages.
- `app.py` and `__main__.py`: composition and JSON diagnostics. The `dev-mac`
  profile enables zero providers at this stage; readiness for conversation is false.
- `.venv/bin/python tools/run_checks.py`: **14 deterministic checks passed** using
  synthetic lifecycle providers and temporary scratch directories. The runner blocks
  network creation, subprocesses, native-library loads, model/device/v2 imports,
  baseline-file/device access, and non-temporary SQLite connections. These guards
  catch accidental I/O in this suite; they are not an adversarial security sandbox.
- `.venv/bin/python -m r3x doctor --profile dev-mac` and `boot --profile dev-mac`
  both passed. Boot created only ignored empty scratch directories and reported
  ready then closed. `pip check` passed; `git diff --check` passed.

The checks exercise read-only diagnostics, configuration rejection, scratch-link
rejection, lifecycle ordering, partial startup failure, optional failure, missing
dependencies, cancellation/close errors, keyboard interruption during startup,
idempotent close, and import/I/O isolation. Cleanup failures remain visible as
`cleanup_failed`; no substitute provider is started.

**Historical slice-0 limits (before slice 1):** lifecycle methods are synchronous and must return; bounded cancellation
of real workers is not implemented or validated. There are no real adapters, turn
processor, speech delivery receipts, database, or model paths yet. No saved-audio,
hosted-model, live-room, or hardware checks were run. Speaker accuracy, voice
quality, response latency, playback underruns, and model memory usage are unmeasured.
Slice 1 must add the production turn path and real adapter/delivery validation.

## Slice 1 implementation evidence

September 10, 2026, v3 working tree based on `a2c5e2f` (slice-0 changes were already
uncommitted). Implementation is present; **the live spoken-exchange gate remains open**.

Implemented ownership and limits:

- `Session.process_turn` is shared by live and synthetic scenarios. One sequential
  conversation worker makes one primary generation per ordinary turn. Speaker stays
  unresolved; no identity inference, named writes, database, camera, or semantic tools.
- Selected adapters: 16 kHz mono sounddevice input, 512-frame Silero VAD, local
  Qwen3-ASR-1.7B-8bit with token log probabilities, OpenAI `gpt-5.4-mini` through
  Responses, ElevenLabs `eleven_v3_conversational`, and one sounddevice output owner.
  Nothing loads at core import or `doctor`; no provider substitutes or hidden retries.
- Capture has 256 ms preroll, 1.2-second default endpoint silence, and a 20-second
  cap. Gaps reset partial capture. Queues bound mic blocks to 128, pending captures to
  two, turns to three, and output phrases to four; overflow rejects newest and logs it.
- Ordinary prose streams into bounded phrases, each sent through streamed PCM TTS.
  Initial audio buffering is approximately 280 ms. The output callback supplies a
  DAC-clock start timestamp; completion waits for PortAudio drain. Underruns are
  counted without discarding the remainder. Partial phrase wording is unknown.
- Cancellation invalidates old capture revisions and queued speech. Terminal stop
  is independent of ASR. A trusted spoken stop bypasses a busy conversation worker.
  The default echo gate rejects captures during output and its 300 ms tail; therefore
  spoken stop during speaker playback is not supported in this conservative mode.
  An explicitly isolated headset setting permits duplex capture, without claiming AEC.
- HTTP reads have a 15-second timeout, connect 5 seconds, and checked 30-second
  stream deadlines. Cancellation gates each yielded token/chunk and the audio callback;
  it cannot forcibly preempt an in-progress native MLX call or blocked socket read.
  Shutdown waits up to 18 seconds for runtime workers. A stuck worker is reported and
  no new turn starts over it; native resources are not unloaded underneath it.
- One OS file lease prevents two live v3 processes in this checkout. It does not
  arbitrate with v2: stop v2 before a live test. Private traces retain text; microphone
  waveforms are not recorded yet and cannot establish capture/overlap accuracy.

Validation performed:

1. **37 deterministic checks passed** via `.venv/bin/python tools/run_checks.py`.
   Synthetic providers, fake trace clocks, temporary stores, and blocked network,
   devices, heavyweight imports, and private v2/v3 state. Cases include original text,
   one model call, capture order/overflow, rejected echo/stale input, late model data,
   cancellation, failed TTS/playback, truncated model streams, final PCM drain,
   device selection, and single-instance rejection. These do not prove semantic skill.
2. **Real VAD on synthetic silence:** loaded Silero 6.2.1; silence was not speech.
3. **Real providers on synthetic saved audio:** `tools/check_providers.py` passed with
   Qwen, OpenAI, and ElevenLabs in the v3 environment. The reference was
   “Hello Rex. I am going camping tomorrow. What should I bring?” ASR retained the
   words, adding a comma; mean token log probability was about -0.0167 (trusted).
   One model call produced a relevant packing answer, and a 25.6-second reply WAV
   was rendered. No speaker played it during the check.
4. **Read-only macOS device check:** the imported microphone name resolved and
   16 kHz mono int16 settings were supported. No recording stream was opened.
5. Packaging dependency consistency and whitespace checks passed.

The single provider run measured startup 4.32 s, synthetic-input TTS 1.40 s, ASR
0.98 s, full model response 2.56 s, and full reply TTS rendering 6.35 s. Process peak
RSS was about 2.94 GB on macOS 27 / Apple Silicon / Python 3.11.9. This is **one
saved-audio run**, not median/p95, streaming-first-audio latency, M2/16 GB performance,
or a matched v2 comparison. Provider evidence and WAVs remain at
`local/validation/2317c3d34607458480b0eddc7621e639/`; resolved packages are locked in
`requirements-live.txt`.

Remaining gate: run `.venv/bin/python -m r3x live`, hear Rex's replies, check the final
words, use terminal stop during generation/playback, and inspect the session report.
Measure real end-of-speech-to-first-audio, endpointing, echo rejection/abstention,
underruns, device shutdown, and voice consistency across phrase requests. There is no
live conversation, speaker-discrimination, or group-acceptance result yet. The original sequencing constraint was superseded by the user's overnight core-build
request; the live gate remains open despite later implementation.

## Core implementation evidence: slices 2-4

September 10, 2026. V3 working tree based on `47a1507`; it was clean before this
implementation. V2 was reverified clean at
`9806c74fd233a7118da88ca5ae55fb7450deec05` and remains unchanged. Extraction details
and checkpoint provenance are in V2_REFERENCE.md. No personal baseline data was
imported, and the live `local/core.sqlite3` does not yet exist.

Implemented:

- One bounded named/unnamed CAM++ tracker, current-window profile agreement,
  explicit unknown/short/mixed/ambiguous outcomes, and namespace isolation.
  VAD intervals, original PCM, transcript trust and echo/truncation evidence travel
  together through shared perception. A failed encoder leaves identity unavailable.
- Natural provisional associations, anchor/cluster checks, three qualifying turns
  with two voiced seconds each/eight total, atomic original-audio/embedding storage,
  and expiry/correction/cancellation. Competing voices cannot collect for the same
  person; incompatible voices cannot refresh an existing profile. Suspect profiles
  require local review or explicit voice reset.
- Optional AVFoundation camera with unique name selection and bounded face-presence
  tracks. OpenCV 4.13.0.92 is pinned for the chosen classifier API. The public Haar
  asset is verified by digest. There is no face recognition, mouth processing,
  visual identity vote, or robot hardware integration.
- One semantic tool surface: introductions/references, explicit-source claims,
  correction/retraction/recall, question delivery/settlement, naming and forgetting.
  IDs are constrained to the snapshot and independently revalidated. Unknown
  self-reports cannot guess a personal subject; ambiguous third-party names cannot
  choose an arbitrary profile. Prior turns retain their own speaker context.
- SQLite participant/source/claim/recording/reference/result records, correction
  history, and managed recording/export/preview cleanup. Stable IDs and source
  quotes persist; session voice hypotheses do not become unquestionable identities
  on restart. File-cleanup failures are reported without a completion acknowledgment.
- Frozen replay uses the production endpoint, perception, Session and tracker.
  It checks independent file digests/splits and refuses exact enrolled PCM as test
  audio. Reports include wrong names, abstentions, precision **and** coverage,
  short/unknown/mixed cohorts, breakdowns, fragmentation/merges, and failed clips.
  Human labels do not steer acoustic predictions. Oracle timing is diagnostic only.
- Explicit consistent v2 snapshot import with selected participant mapping, preserved
  original WAVs, isolated legacy vector namespaces, suspect flags, digest-checked
  re-embedding previews, export, bad-sample removal, voice reset, and forgetting.
  These tools were tested against synthetic databases, not run on personal v2 data.
- Bounded capture/turn/output queues, provider/cancellation failures, one primary
  generation per ordinary reply, and at most one tool-result continuation.
  Intermediate model commentary is silent; unphased tool-enabled text is held until
  the response completes. Only delivered audio questions become pending. Diagnostics
  include ASR, speaker analysis, context, first PCM, playback queue/actual audio,
  resource high-water marks, and explicit failures where measured.

Validation levels and results:

| Evidence | Result | What it does not prove |
| --- | --- | --- |
| Blocked-I/O deterministic suite | **80 checks passed**; includes ownership, original utterance, atomic save/failure, restart, correction, cancellation, alias ambiguity, competing voices, deletion, namespace separation, replay labels, face expiry/selection, and 150-turn bounded-state exercise | Language skill, real voice discrimination, physical audio/video behavior |
| Documented core scenario | Natural enrollment → claim → restart/recall → correction → forgetting passed with scripted decisions and simulated delivery | Real semantics or actual speech |
| Real hosted model, synthetic identity/text | **14 calibration cases + 14 alternate wording cases passed** through the production Session/state/store; 22 and 23 requests respectively, with one primary per turn and bounded tool continuations | Universal semantic reliability; real acoustic attribution |
| Real saved-audio model check | Qwen/Silero/CAM++ processed synthetic speech; trusted transcript, 192-dimensional embeddings, three windows, about .75 window consistency | Bret/Jeff separation, overlap detection, live endpoint quality |
| Full real-provider core on synthetic speech | **Six spoken-input turns passed:** natural enrollment saved three originals, a supported self-report saved, recall survived restart, and correction superseded it; 6 primary + 5 tool continuations | Human group accuracy or spoken output; replies were text only |
| Frozen replay CLI | Synthetic saved WAV passed through production VAD/ASR/tracker with zero conversational model calls and no mutations | Held-out human accuracy; its clean-enrolled cohort has zero samples |
| Camera detector on generated blank frames | 20 blank frames: no faces, median about 2.6 ms/p95 2.8 ms; first detector import/load about 4.45 s in that process | Detection accuracy, real frame cost, capture compatibility, simultaneous live load |
| Packaging/readiness | `pip check`, dependency/model readiness, model digests and whitespace checks passed | Hosted uptime, live device access or validated room settings |

Private synthetic evidence directories:

- Text calibration: `local/validation/1faee04367a44a5c8a0bbd58cc9f469a/`.
- Alternate wording: `local/validation/7e346d2452654ce4a67be20a83eca301/`.
- Saved models / blank frames: `local/validation/ba992e2fadca47ed91f65f9c4313fd5a/`.
- Six-turn real-provider integration: `local/validation/15e01b7eed7a4c7cbfa0b3742ed92ab6/`.
- Final evidence-only replay: `local/validation/e29fdb607b6c408db2054b8a710e1ee3/`.

First morning live check: capture and ASR heard “Are you there?” and the model
generated a reply, but delivery failed with `ElevenLabsConnectTimeout` before
any PCM/playback event (session `62d222f743a64540947516b6e74f4447`). Camera frames
were present; this is not camera accuracy or spoken-exchange acceptance. A later
silent request through the configured TTS adapter received 98,784 PCM bytes
(2.24 seconds) in 1.19 seconds. The connection worked on that check; actual
speaker playback still needs verification. No timeout/retry policy was changed.
Live console output now emphasizes conversation, generated-versus-played speech,
and failures, keeping frame/resource telemetry in the complete JSON file. Failed
delivery errors also appear in report summaries. The updated blocked-I/O suite
passes **82 checks**, including console/log separation and failure reporting.

### Labeled car-session repairs

The user reviewed all 21 clips from session `460ffecabf704cf6b35681e155891ce9`,
identifying Bret, Jeff, Rex, and Siri in order. The private `human-labels.json`
sidecar preserves those labels and source digests; exact boundaries/overlap remain
unlabeled. Labels score results after inference and never seed profiles.

- Endpointing now splits at playback-gate transitions. The gate begins when PCM
  enters the output queue, not while TTS is waiting on the network, and retains
  the 300 ms tail. Human prefixes/suffixes survive; simultaneous speech is still
  excluded. This does not implement AEC or separate superimposed voices.
- Tentative session continuity uses .30 similarity and .12 competing-track margin,
  without reference updates, person identity, self-identity permissions, or voice
  learning. Strong updates/enrollment thresholds are unchanged. After the first
  track, new tracks require one voiced second rather than minting tracks from very
  short uncertain fragments. This is tuning against one session, not calibration
  across people/devices.
- Compatible windows on both sides of an abrupt acoustic change can propose up to
  three approximate boundaries. Each resulting turn is independently transcribed,
  retains its source ID/offset, and cannot enroll voices or write personal facts.
- The same conversational generation can execute `remain_silent`, with no speech
  or result continuation. A completed call/result pair records that decision without
  fabricated heard text; old pairs are removed together when history is bounded.
  The redundant model-facing `resolve_person` operation was retired: the context
  already supplies the roster/aliases and the state executor retains exact lookup
  validation. This avoids spending the one action on a lookup instead of a save.

Saved-audio evidence: `local/validation/c152c9cccf1e473eaf21eba000f3743d/report.json`.
The production Endpoint reused recorded VAD and reconstructed PCM-ready/playback-end
times; Perception and the frozen Session/state used real Qwen ASR and CAM++.
No live profile was read/changed, no device opened, and no hosted calls were made.

| Labeled single-human clips | Original run | Revised replay |
| --- | --- | --- |
| Bret tracks | 2 | 1 |
| Jeff tracks | 3 | 1 |
| Assigned clips / all 10 single-human clips | 7/10 | 6/10 |
| Tracks mixing Bret and Jeff in these clips | 0 | 0 |

The coverage reduction is explicit: clip 21 now abstains rather than making another
track from 0.61 voiced seconds. Clips 08, 12, and 15 also remain unassigned. The six
Rex-only clips stay excluded; non-playback parts are recovered from clips 09, 15,
16, 18, and 20. Clip 17 splits navigation from “You are annoying,” but does not
separate the laugh. Clip 20 remains mixed after the playback portion. No synthetic
voice classifier or complete diarization is claimed. Old frame-level echo flags
were not retained, so playback reconstruction is approximate.

Saved-clip processing median/max was .206/.682 seconds, with peak process RSS
3.15 GB. These are saved-audio figures, not live reply latency; replayed timestamps
cannot measure live queue delay. Source-window and capture timings remain inspectable.
The blocked-I/O suite passes **92 checks**, including echo boundaries, weak-track
nonlearning, ambiguous abstention, source-preserving splits, per-turn silence and
re-engagement, bounded call/result history, and derived-source write rejection.

Prompt/semantic trials and their failures are retained under private validation.
Extra reasoning and a stronger-model request trial did not consistently resolve
semantic failures at this earlier checkpoint, so it retained `gpt-5.4-mini`, reasoning `none`.
The later multi-turn repair below supersedes that reasoning choice.
The trial request overrides are recorded in `local/validation/car-analysis/model-experiments.json`;
those experimental traces name the configured adapter, not the overridden request.

After prompt consolidation and retirement of the redundant lookup tool, the
original 14 semantic cases passed (`local/validation/7dcc3d0aec8c474eaf4748c9e28e2041/`).
Further alternate-wording checks exposed and corrected over-eager saves of a joke's
aside and conversational person references. The final alternate semantic set passed
**14/14** (`local/validation/012110aea51c41368b9abf24c890b524/`); the alternate participation
and re-engagement set passed **24/24** (`local/validation/7c72e7ff936b43ae8585ab7029a278f5/`).
These hosted checks used synthetic text and temporary state, without TTS or devices.
They are tuning/regression evidence, not universal semantic reliability or live
latency measurements. Prior failed runs remain retained; no backend/reasoning-policy
change was shipped to obtain these results. Facts are checked across predicate and
value together while still requiring the correct subject/source; an unresolved
occupation statement must create neither a claim nor a person.

The final semantic run made 14 primary requests plus 12 tool-result continuations;
request-to-first-text median/p95 was 1.82/9.35 seconds including tool work. The
participation run made 24 primary requests and zero continuations; its 18 spoken-text
responses had .65/1.66-second median/p95 first-text latency. The six quiet decisions
produced no speech. These figures exclude capture/TTS/playback and show that tool
turns still have substantial tail latency; a live end-to-end retest remains necessary.

The six-turn integration measured ASR median/p95 about .224/.253 s, speaker
analysis .030/.038 s, and model-to-first-text including applicable tool work
1.705/1.916 s. Peak process RSS was about 3.19 GB on this dev Mac. These are a
small synthetic saved-audio cohort, not live end-of-speech-to-audio, a steady-state
memory trend, M2/16 GB results, or a matched v2 comparison. The Mac sandbox initially
blocked Metal; saved-audio inference was rerun with local GPU access and no devices.

**Remaining acceptance:** actual microphone/speaker/camera operation; Rex voice
consistency and complete playback; live cancellation/drain/underruns; three two-person
sessions; at least 100 held-out clean human utterances plus short/unknown/overlap
cohorts; independently reviewed profiles; calibrated thresholds; camera/inference
contention; and matched v2 latency. Window consistency can miss overlap or similar
speakers. No real speaker accuracy, live responsiveness, or group-acceptance claim
is made. Controlled feature expansion remains frozen until those results are reviewed.

## Implemented operating modes

| Mode | Purpose | I/O |
| --- | --- | --- |
| `python -m r3x doctor` | Capability, model-file, credential and dependency readiness | No model/device start, inference, download, or scratch creation |
| `python -m r3x live` | Complete conversation core, persistence and delivery | Selected microphone/speaker and hosted providers; camera only when enabled |
| `python -m r3x replay <manifest>` | Production segmentation, ASR and tracking on frozen labeled audio | Saved files; optional hosted `--brain` with silent output; no live devices |
| `python -m r3x scenario <fixture>` | Production Session/state/output/store with scripted evidence | Temporary stores and synthetic providers; no live devices or network |
| `python -m r3x report <session.jsonl>` | Observed timings, outcomes, failures and model calls | Read local evidence |
| `python -m r3x profiles ...` | Independent import, previewed re-embedding, review and deletion | Explicit local file/store actions under the live lease; no hardware |
| `python -m r3x boot` | Provider-free lifecycle check | Ignored scratch directories only |

Use one shared turn-processing entry point in live, replay, and scenario modes.
Replace observations/providers, not the decision machinery being evaluated. Typed
tests must not automatically receive the identity confidence of real audio input.

## Scratch data and baseline preparation

1. Preserve v2 as the reference installation. Record its commit and working-tree
   differences; use the reference revision in V2_REFERENCE.md or a freshly verified
   successor that includes the recent repairs.
2. Before creating local artifacts, ignore `local/`, credentials, databases, private
   recordings, model weights, and exports. Keep sanitized fixtures separate.
3. Implement an explicit import that reads a consistent v2 snapshot and creates
   independent v3 copies of selected participant labels, voiceprints, and original
   audio. Record source IDs, digests, model namespace, and new ID mapping.
4. Start personal claims empty or with a few verified fixture claims. Do not bulk
   import v2's accumulated facts to prove a clean core works.
5. Allow a dataset reviewer to flag a copied profile as suspect. Existing enrollment
   is a starting reference, not ground truth about every stored clip.
6. Keep labeled evaluation recordings separate from enrollment and tuning. No
   automatic enrollment or adaptive updates on the frozen accuracy-evaluation set.
   Run separate adaptation experiments if evaluating safe profile growth.

No personal v2 import or live recording was performed during this implementation.
Synthetic validation audio is stored separately from the live core. Do not run v2
and v3 on the same microphone/speaker devices concurrently.

## Speaker evaluation dataset

Use human labels established independently of Rex's predictions. Include Bret and
Jeff, and an out-of-roster speaker when available. Synthetic unknown embeddings
are useful contract tests but do not substitute for a real unknown voice.

Cover multiple sessions and conditions:

- Near and farther away, facing the microphone and turning away, seated and moving.
- Longer natural sentences, name answers, short acknowledgments, questions, and laughter.
- A-B-A changes, rapid interjections, pauses, and deliberate overlap.
- One-to-three visible people, small distant faces, incorrect face-name candidates,
  a silent visible person with someone speaking off camera, and absent camera evidence.
- Rex speech/echo and, later, impersonations that must not refresh real identities.
- ReSpeaker direction only in a separate robot cohort after the servo hold exists.

For each capture, record expected speaker(s), speech intervals, transcript reference
where available, overlap, device, session, condition, and whether identity is truly
resolvable from the recording. Unresolvable examples must not silently disappear
from coverage reporting. An acoustic track label is distinct from its person name.

First compare clean same-person and different-person scores. Then evaluate the
complete tracker. High ASR accuracy is not speaker accuracy; low cosine between
two short windows is not proof of a speaker change. Compare alternate models or
segmentation only after locating which stage failed, using the same held-out data.

## Conversation and memory scenarios

Expected behavior below is a contract, not an exact response wording. Run paraphrases,
straight/curly apostrophes, unknown speakers, and variations that were not used while
implementing. Use actual speech for the final acceptance pass.

| Scenario | Required outcome |
| --- | --- |
| New person says a full name, then speaks naturally | A provisional association; accepted voice samples accumulate only with compatible evidence; no repeat-after-me routine |
| Known face lacks CAM++ prints | Model asks a natural speaking confirmation; the confirmed voice anchors the chain; a different person's answer cannot enroll it |
| Bret says "That's Jeff Benziger" | Resolve the mentioned person through an explicit unique nickname/alias match; preserve Bret/unresolved as the speaker; no literal-sentence name, new face, or bystander voice enrollment |
| Rex asks a name; response is "I'm going camping tomorrow" | Interpret the statement or clarify; create no person named after the activity |
| "That's not his name" during onboarding or collection | Correction reaches the conversational owner; affected provisional work stops; no next-question handler consumes it as a profile answer |
| Two people share a nickname | Request useful disambiguation when needed; no arbitrary match or duplicate enrollment |
| Rex just greeted Bret; a plausible reply follows | Continue coherently without reflexively demanding a name; continuity remains evidence-limited and cannot authorize profile growth |
| Jeff says "You're creepy" after a job question | A comment about Rex; no occupation fact or later self-description of Jeff as creepy |
| Jeff asks about Jimmy Carter or makes a passing restaurant joke | A question/remark; no automatic lasting interest, worldview, occupation, or catchphrase |
| Bret reports a fact about Jeff | Save only if eligible, with Jeff as subject and Bret as source; never relabel it as Jeff's own statement |
| Speaker uncertain while volunteering a personal fact | Preserve the utterance/pending claim; no guessed personal profile write |
| Clear self-report, restart, recall | Retrieve the same attributed claim with its source/status; session voice tracks do not persist as unquestionable identities |
| "I meant clocks, not radios" | Correct the relevant claim through context; recall uses the correction and retains provenance |
| Person/voice data is forgotten | Active retrieval, accepted audio, derived references, and pending associations stop using deleted data |
| Several people speak within one capture | Split only with supported timing or abstain; no whole-buffer transcript assigned confidently to one person |
| Ask "Who is speaking?" under weak evidence | Admit uncertainty or express a supported tentative association; a name mentioned in the utterance cannot answer the question by itself |

Deliberate false self-identification is a limit to document, not something a cosine
threshold or text model can solve universally. The prototype learns a voice-to-claimed-
identity association; it is not legal identity verification.

## Proactive conversation acceptance

September 12 design additions: these are acceptance targets, not a declaration
that every case passes. Use the [shared proactive policy](PROACTIVE_CONVERSATION.md)
through production Session, preserving the same attribution and delivery rules.

| Scenario | Required outcome |
| --- | --- |
| Known or unknown visitor arrives without speaking | A suitable greeting/invitation is possible; identity uncertainty remains explicit |
| Tired person is receptive to light conversation | Rex can offer warmth or low-pressure humor; a short answer must not mechanically suppress initiative |
| Grief, distress or a serious disclosure | Sensitive acknowledgment/listening, no forced joke or demand to become cheerful |
| Explicit request for quiet or to drop a subject | Honor its meaning and scope; camera-facing or another person's reply cannot erase that boundary |
| Thinking pause versus completed topic | Preserve unfinished thoughts; a genuine lull can permit a relevant contribution |
| Human asks another human a question | Rex leaves the intended respondent the floor even when he could answer |
| Humans discuss Rex, then address him directly | Contextual restraint or a well-timed brief contribution; normal re-engagement when addressed |
| One quiet and one engaged participant | Individual context remains separate; no shared energy label or compulsory equal-turn invitation |
| Relevant topic and unrelated cue are both available | The model can continue the topic; fixed category order must not permanently hide relevant opportunities |
| Repeated invitations go unanswered | Reduce initiative without a canned demand for reassurance; a meaningful new opening can still be considered |
| Human speech is unrelated to the last invitation | Do not automatically mark the invitation answered or reset all pacing limits |
| Camera flicker or an unchanged scene | No repeated arrivals or novelty claims; uncertain objects remain uncertain |
| Private fact or celebration in a group | Preserve source attribution and appropriate audience discretion |
| Cancelled, failed or stale proactive output | No false greeting/question completion or inference that a person ignored unheard speech |
| Same topic offered with different wording | Recognize repetition; distinguish a useful follow-up from asking again |

Separate deterministic contracts (freshness, boundaries represented in state,
revision validation, outcome bookkeeping, bounded queues) from semantic judgments
that require actual model calls and human review. Test paraphrases and group
sequences, including receptive quiet people and serious conversations. No automatic
test should claim a joke improved someone's mood.

In live sessions, annotate both unwelcome interruptions/repetition and missed welcome
opportunities. Record offered, selected, declined, cancelled and delivered counts,
participant responses, unsolicited question load, target errors, and observer/user
ratings of relevance, warmth, humor and timing. Report disagreements and denominators;
zero interruptions achieved by permanent silence is not acceptance. Numerical gates
for this new policy remain to be agreed before its evaluation, not invented here.

Measure proactive model calls/cost, decision-to-audio time, impact on ordinary-reply
median/p95 latency, microphone responsiveness, memory and cancellation under combined
load. Existing playback echo gating still prevents validated human barge-in during
Rex's audio. Saved replay cannot establish that physical limitation is resolved.

## Delivery, lifecycle, and failure scenarios

- A write fails: no "I've saved that" acknowledgment and no half-written record.
- A duplicate tool request arrives: one mutation and a repeatable result.
- A result arrives for an old session/revision: no new write or speech from stale work.
- Stop/cancel occurs during generation or playback: bounded shutdown, no trailing
  queued speech, and an interrupted delivery receipt.
- A question fails before being heard: it cannot claim the next utterance as its answer.
- Speech is captured during a reply: retain bounded input in order; do not launch
  a second conversational owner. Report overflow explicitly.
- ASR/encoder/TTS fails or a model is absent: show the actual capability failure;
  no silent namespace/provider switch, endless retry, or false successful playback.
- Transcript has low trust, or audio is echo/mixed: it cannot produce named biometric
  writes or established personal self-reports.
- Camera work and local inference compete: measure scheduling and actual playback;
  an isolated successful WAV generation does not establish live reliability.
- Disabled capabilities: no import-time model load, device access, or hidden warmup.

When local Breeze/impersonations are introduced, add complete-content and playback
tests: original transcript matches its reference WAV; the distinctive final phrase
survives; underruns do not deliberately discard remaining audio; failures produce no
successful outro. Preserve 8-bit and the chosen speech buffering policy.

## Metrics and proposed acceptance gates

Freeze the dataset, definitions, and gates before the evaluation run. The numeric
values below are initial proposals, not measured v3 results or user-approved service
guarantees. Revise them with an explicit rationale before a new evaluation, not
after a failure just to declare success.

| Area | Initial target/evidence |
| --- | --- |
| Meaning/state integrity | All listed deterministic scenario contracts pass, including unseen wording variants and failed/stale operations |
| Persistent data integrity | Zero wrong-person claims or named biometric writes observed in the declared acceptance sessions; report cohort size, not a universal zero-error claim |
| Clean enrolled speech | On held-out single-speaker captures with at least two voiced seconds: at least 95% correct among named decisions AND at least 85% named coverage; report per person, distance, and device |
| Unknown and mixed speech | Dedicated real cohorts; report false-known assignments and missed switches/overlap separately; zero named training writes on labeled mixed/Rex-echo samples |
| Short/ambiguous speech | Report assignment precision, abstention, and fragmentation separately; do not inflate clean-speech metrics by excluding difficult cases from the overall report |
| Live conversation | At least three separate two-person sessions, including one roughly 15-minute natural exchange; record needless identity questions, wrong names, missed corrections, and participant feedback |
| Latency | Same-device median and p95 end-of-speech-to-first-audio no worse than the matched v2 baseline; seek a material improvement. Record concrete release thresholds after the baseline, before evaluation |
| Perceived responsiveness | Existing 4–6-second delays are a known dissatisfaction, not an acceptable target merely because v2 also has them; do not declare the product ready from non-regression alone |
| Resources | Only declared models load; no growing queue/cache or unbounded memory trend during a session; report resident models, memory observations, and playback underruns |
| Speech truth | No false completion/save acknowledgment in the acceptance scenarios; generated, queued, and played states remain distinguishable |

Start with at least 100 held-out clear utterances across speakers/sessions as a
practical diagnostic sample, plus separate short, unknown, and overlap cohorts.
Report numerators and denominators. This size does not prove general reliability;
additional real sessions and device conditions remain necessary.

Report both per-turn and per-time tracking metrics where possible: wrong named
assignments, unknown-person false accepts, abstention, track fragmentation, merges,
switch detection, and overlap handling. A system assigning no names cannot pass.
Do not train or tune using evaluation labels, or test enrollment clips against
their own saved embeddings as recognition evidence.

Latency reports include capture endpoint delay, ASR, speaker analysis, context
preparation, model calls, first audio production, playback queue, and actual playback.
Separate cold/warm, online/offline, Mac/robot, and optional-camera-load results.
Include failures and timeouts in the overall report rather than silently removing
them; successful-request timing may be reported as an additional distribution.

## Evidence to retain

A report names the source revision, dirty-tree status, effective profile, model
versions, dataset digest, enrollment/tuning/evaluation split, device, scenario
results, metrics, observed failures, and unresolved limits. Private artifacts stay
local; a sanitized summary and fixture contracts can be tracked.

Use three distinct validation levels: deterministic contract checks; real models
on saved recordings; and live room/provider/hardware sessions. State which passed.
Do not let a large synthetic test count stand in for group recognition evidence.

When a gate fails, first identify whether the error is capture, segmentation,
acoustic matching, language interpretation, state validation, storage, or delivery.
Fix the owning component. Do not add a new interception rule in a different layer
or rewrite all providers together. Expand only after the remaining limitations
are explicit and acceptable for the next intended use.

## September 10 patient-listening repair

The latest run (`0fec67fc393f4a959b58a94b85e3d340`) logged replies to “We are
going to.” and “West two.” with roughly 0.608-second endpoints. This is transcript
and timing evidence, not a new waveform/boundary annotation. The user reported
being interrupted. The default pause allowance is now 1.2 seconds, replacing
finish-pending-first: resumed non-echo speech cancels pending generation/TTS before
PCM delivery, preserves earlier user history, and skips stale queued replies.
Explicit stop still resets its own revision; yielding does not. The conversational
owner can select `unfinished_thought` in the existing silence tool. No new model,
second classifier, word-replacement regex, or ASR provider was introduced.

Verification: 96 offline contract tests passed with blocked network/devices/private
state, including an 800-ms internal pause and continuation, resumption during model
and TTS work, and delayed transcript retention. The simulated sink now starts its
receipt only after PCM arrives, matching the real sink's delivery contract.

Hosted synthetic text checks with the final prompt: listening **17/18**
(`79d75780776741c3937538aaa6f4d220`), participation **23/24**
(`0739fbd8a58a4ad4990967b200fe0b0e`). The remaining failures were a reply to an
ambiguous “east two” fragment after a direction question and a brief acknowledgment
of a request for space instead of complete silence. Both are retained limitations,
not passing quiet decisions. The identity/memory alternate suite passed **14/14**
(`79b6acaef1b247a98fc2320479061d15`) before the final addressee/quiet-protocol
wording adjustment. Earlier attempts include provider timeouts and are retained.
These checks used no audio playback, microphone, camera, or personal databases.

Live acceptance remains open. The added default endpoint allowance is 0.6 seconds;
new end-to-end latency has not been measured. Qwen remains local 1.7B/8-bit and its
homophone accuracy is not fixed or established by text-only checks. Already-started
speaker playback still gates microphone speech; this is not full barge-in or echo
cancellation. Semantic waiting is probabilistic and still misses some ambiguous
fragments. Do not claim every unfinished thought will be detected.

## September 10 continuation, introduction, and addressee repair

Live run `065dd1c7d81248409ed4765defc16aed` supported the listening improvement:
12 audio replies completed with no underruns; four yielded turns had no playback,
including three active cancellations and one stale turn. The user reported no
interruptions. Median first audio was 3.99 s versus 2.92 s in the preceding,
unmatched conversation. No waveform overlap annotation was performed.

This repair preserves the 1.2-second endpoint and adds explicit yielded-without-
speech history, source timing/trust metadata, and instructions to interpret a
continuation or correction with the preceding user words. Original transcripts and
speaker uncertainty remain intact. Instructions distinguish an invitation to greet
a parent from an explanation about Rex to that parent, including later re-engagement.

Existing-name self-introductions use `introduce_person`, not `rename_person`.
The state owner now provides permitted rename/forget targets using the same
acoustic authorization checked during execution; unavailable operations are not
offered. A supported/provisional name correction still works, but an unrelated
voice cannot rename someone. No live person, voiceprint, or claim was edited.

The model remains `gpt-5.4-mini`; reasoning changed from none to low within the
single primary generation after the same 14 multi-turn synthetic checks improved
from 10/14 (`bc79194aa16848458b7436ce7d174ed9`, none) to 14/14
(`03ac095080ad4372b30e787a3ce95408`, low). In that small comparison, median first
text for completed replies was 1.00 versus 1.33 s; max was 2.43 versus 2.88 s.
This measures text/tool processing, not new live speech latency. Output budgets
are bounded at 1536 tokens with tools and 768 for result continuations; reasoning
is included in those budgets. Subsequent traces record `reasoning_effort` explicitly.
There is no new model/provider, resident model, or serial classifier. Qwen is unchanged.

Final checks:
- **97 offline contracts passed**, blocking network, devices, and private state.
  New coverage exercises existing-person association, offered/executed rename
  authorization, and preservation of yielded words in the next model snapshot.
- **14/14 hosted multi-turn checks passed** (`7bd3f07db0ca45c4a2d8524e6fd5ed20`).
  Replies were also read: the destination was treated as part of the trip and the
  state correction was understood. The parent explanation stayed quiet and the
  direct question re-engaged. Ordinary follow-up turns advanced synthetic enrollment.
  Synthetic embeddings do not demonstrate real voice learning or discrimination.
  Final completed-reply first-text median/max was 1.26/5.10 s in this small run.
- **14/14 alternate memory/identity cases passed**
  (`c0293a58eb8d41938149a015744a72e3`). The mentioned-person assertion now also
  verifies that the speaker's stored name was not changed. Requests for space may
  be honored silently; the test still requires no inappropriate identity/fact write.
  A prior low-reasoning attempt saved a joke aside; whole-utterance memory guidance
  was clarified and the failed evidence retained.
- Broader pre-final-memory-wording probes were **17/18 listening**
  (`5031e2bd3488458a9eb1e66d1d7cbe78`) and **23/24 participation**
  (`a9171f02c2624170833045105b90d3c0`). Remaining misses: asking where after an
  ambiguous “east two” fragment, and answering a standalone search-like fragment.
  These are known limitations, not passing silence decisions.

All hosted probes used synthetic text and isolated databases with no TTS or devices.
Live follow-up is still needed for response timing, natural enrollment, and group
participation. Text checks do not correct Qwen's town/name spelling; the model may
still repeat a misheard town. Quiet judgments remain probabilistic. Speaker echo
still prevents validated human barge-in during playback. Do not describe the whole
core or acoustic acceptance as complete on the strength of these checks.

## September 10 session context and grounded personality

Run `24628373accd4356885eae2f9818eff2` exposed loss of an earlier companion reference
and style correction. The previous 16-item deque retained only roughly five ordinary
exchanges once speaker metadata and replies were counted. The replacement
`TurnHistory` keeps up to 32 whole exchanges with a 24,000-character serialized
request-history budget. Complete old exchanges are evicted together, original words
are not summarized or rewritten, incomplete tool pairs are excluded, and an oversized
current exchange fails explicitly before a model request rather than being clipped.
Request traces include history turn/item/character counts. No new configuration,
resident model, summarizer, or serial model call was introduced.

Instructions now emphasize remembering expressed session relationships and style
corrections without converting them to verified identity or durable claims. Rex
should distinguish a joke/metaphor from a factual claim, admit specific demonstrated
mistakes, resist unsupported accusations, and avoid inventing flaws or performing
an argument to seem opinionated. This is model behavior guidance, not a deterministic
semantic filter. Original ASR text remains unchanged.

Validation:
- **101 offline contracts passed**, including 20-turn context retention with speaker
  provenance, no automatic people/claim creation, whole-exchange budget eviction,
  oversized-turn rejection, clean new-session history, and deletion of long context
  before a forget-result continuation. Existing cancellation/delivery checks passed.
- In a **hosted synthetic comparison**, the old last-16-items window could recall
  neither the companion nor the wording preference after an 18-turn scripted detour;
  the new production history recalled “my sister” and “not a race or a rally”.
  `local/validation/09ba2b1c97f14cf3a897a594d542e09f/evidence.json` retains replies for
  human review. The new history also carried the companion into a later topic
  suggestion. No people or claims were created in either condition.
- Reviewed style probes resisted unsupported accusations, maintained a position,
  moved on with the next topic, and acknowledged a seeded Arkansas/Alabama error.
  They still contain some generic imagery and potentially reactive wording; this is
  an improvement sample, not universal personality acceptance. Completed status was
  not treated as a semantic correctness score.
- For the three matched context questions, old history used 2,808–3,076 serialized
  characters versus 10,931–12,138 with complete turns. Text-decision median/max was
  1.55/3.04 s versus 0.95/1.08 s. These tiny, sequential samples are not evidence that
  larger context is intrinsically faster; live latency and tail impact remain open.
- **14/14 alternate identity/memory regression cases passed**
  (`f829518c06bc47af92ccadde86cd0083`). A pre-final-generalization multi-turn run passed
  **13/14** (`38c85de3a0a0468fba77192c1c948ed8`); it still answered an explanation
  intended for a parent. That miss remains visible and is not claimed fixed here.

These checks used fake clocks/providers for offline contracts and actual hosted
model calls on synthetic text for semantic review. Detour turns were built through
production Session with scripted replies; the compared probes used the actual
hosted adapter. No microphones, cameras, playback, private databases, or real speaker
models were used. This change does not add cross-run conversation retrieval, durable
session summaries, completed voice enrollment, or validated barge-in. The active
chat starts fresh on restart; existing persistent names/facts/profiles are unchanged.


### Evidence-first conversational enrollment (2026-09-10)

Implemented unnamed candidate collection before introductions, retrospective
same-group enrollment, competing-window/group checks, and pending evidence
persistence separate from verified profiles. Input learning precedes response
selection. Stopping speech preserves evidence; correcting an association removes
it, and a display-name correction retains the same acoustic group.

Offline acceptance uses temporary databases, synthetic embeddings/audio, and the
production CoreState/Session path with external providers/devices blocked. Coverage
includes alternating A/B groups with delayed introductions and exact retained PCM,
named and anonymous restart recovery, short anchors, other-person references,
suppressed/yielded responses, cancellation versus correction, explicit overlap,
disagreeing windows, expiration, namespace separation, forget, name correction,
additive database migration, frozen replay, and atomic save-failure recovery.

Validation: `.venv/bin/python tools/run_checks.py` — 113 contracts passed.
No microphone, camera, playback, hosted model, or real-model saved-audio check was
used for this change. Real Bret/Jeff discrimination, wrong-person attribution,
abstention, and end-to-end latency remain live/held-out acceptance work. The older
runs were not retroactively enrolled or edited. No new model/dependency was added.

### Face design and comparison preparation (September 10, 2026)

[Face attribution plan](FACE_ATTRIBUTION_PLAN.md) records the base M2/16 GB target,
automatic contextual face/name association (including third-party introductions),
recency with contradiction handling, shared visual frames, tentative resource
budgets and phased acceptance. This historical preparation stage is superseded by the implementation evidence below.

Five public v2 model assets were independently copied without downloads or profile
imports. A CPU ONNX aligned-crop harness now records resource use, known-person
accuracy, unknown false accepts and abstention with camera/native-face-size groups.
A real ArcFace model ran only on generated pixels: M5 Pro one-thread warm median
45.48 ms, p95 46.04 ms, process lifetime peak 481.45 MiB. This excludes detection and
other models and provides no identity accuracy or M2 readiness evidence. The
blocked-I/O suite passes 119 checks. Representative face images, preprocessing
parity and M2/combined-load validation remain pending; no camera/audio was operated.

The first compact challenger is now prepared: EdgeFace-S, 14.81 MB downloaded and
verified against its publisher's release digest. Matched M5 Pro CPU generated-pixel
runs measured median 6.71 ms / peak process RSS 123.92 MiB versus ArcFace R50
45.24 ms / 485.95 MiB. Both used separate processes, one CPU thread and 30 warm
embeddings. No detector or other model ran concurrently. This is resource screening
only; it preceded the user-supplied photo/video diagnostics. M2 acceptance remains unmeasured. See the face plan for provenance and repeat commands.


### Face identity implementation (September 11, 2026)

The optional camera now runs SCRFD-10G + EdgeFace-S through shared latest-frame
capture. Contextual introductions, third-party face targets, supported-voice/sole-face
association, quality-gated persistent crops, targeted questions, face-only correction,
restart and deletion are implemented. It never substitutes visual identity for the
acoustic speaker. Existing files were independently copied; no new download or live
profile import occurred. See [implementation and evidence](FACE_ATTRIBUTION_PLAN.md#implemented-dev-mac-face-path)
for policy gates, saved-media coverage/abstention, memory bounds and remaining tests.

Real models ran on supplied saved media; a temporary-store check saved and reloaded
real crops with synthetic voice/semantic input. No camera, microphone, playback,
hosted model or live personal database was operated. Robot-camera and combined-load
M2 acceptance remain open; neither synthetic tests nor this small saved-media cohort
establishes general identification accuracy.

Final blocked-I/O suite: **135 checks passed**. A changing-input M5 detector memory
regression was isolated and mitigated through standard CPU kernel selection; the
55-frame varied-image process peak fell from 2,598 MiB to 268 MiB, with median face
analysis increasing from 70 ms to 271 ms. The full saved-video check retained its
recognition results. See the face plan for exact scope and the performance tradeoff.

### Proactive face greetings (September 11, 2026)

Stable visual arrivals now enter the same production conversation/output path as
speech, with LLM-authored context-sensitive greetings or targeted name questions,
and silence as an explicit choice. Arrival pacing and listening gates prevent
repeated attempts; visual events do not create acoustic evidence or authorize
identity writes. See the [operating policy](CORE_OPERATIONS.md#proactive-face-greetings).

The blocked-I/O suite passes **151 checks**. Three hosted text-only requests using
fictional visitors verified a named greeting, an unfamiliar visitor's targeted name
question, and silence after a quiet request, each with one model generation. No
camera, microphone or audio output was operated. Live timing, face-question answers,
repetition and overlap still need validation; robot/M2 acceptance remains open.

Short-introduction face fix: **153 blocked-I/O checks pass**. An explicit clean
self-introduction can now save a clear sole face even below the per-sample voice
training duration. Ambiguity, competing voices, source validity and face-quality
checks remain. Voice accumulation policy is unchanged; the latest live run's short
clips and rejected long-utterance windows still require separate audio investigation.

### Conversational voice variation correction (September 11, 2026)

The worst-pair window gate is replaced by leave-one-window-out pooled consistency,
with a coherent temporal-group veto and all windows retained for rival checks.
One-second clean clips can accumulate; three clips/eight seconds total remain
required. Cluster comparison uses the pooled references plus anchor and pairwise
conflict floors. See CORE_OPERATIONS.md for exact gates and validation limits.

The saved five-turn dev-Mac run, real CAM++ inference and scripted semantic input
through production Session enrolled three originals totaling 8.384 voiced seconds
in a temporary database. Its final utterance matched the enrolled profile. The
older 21-clip car cohort abstained on every held-out named decision both before and
after; no new false names were observed, but this is not accuracy acceptance.
No live microphone, camera, playback or hosted model was used, and personal state
was not rewritten. Synthetic checks cover gradual variation, actual two-group
changes, isolated foreign windows, rival groups and accumulation across restart.

Final verification: **157 blocked-I/O tests passed**; `git diff --check` passed.
The fix adds no model, download, camera stream or model-generation call. Saved
CAM++ checks used the dev Mac; combined-load M2 performance was not remeasured.

### Shared proactive core foundation (September 12, 2026)

The user authorized planning and beginning implementation, clarifying that Rex
normally runs for social demonstrations and should consider an opening after
7–10 seconds of silence. The default is eight seconds. Arrivals and visible-group
lulls now share one Opportunity/Session/model/output path, with original source
history, current audience, elapsed quiet time, bounded attempts, delivery outcomes,
and explicit session quiet/resume. No new provider or resident model was added.

The [implementation plan](PROACTIVE_IMPLEMENTATION.md) records verification and
remaining stages. Synthetic scenarios exercise source preservation, state, pacing,
cancellation and delivery; actual hosted text probes assess welcome initiative
alongside sensitivity and restraint. The second slice now adds optional source-linked invitation response interpretations,
participant topic boundaries and human-floor resolution. These remain session state;
no persistent participant preferences or emotion inference are created. Group
participation remains probabilistic; automated checks do not establish delight,
real attribution accuracy, physical barge-in or M2 performance.

### Conversational feedback slice (September 12, 2026)

Implemented: `conversation_feedback` applies source-linked invitation responses,
topic closure/avoidance, participant-owned corrections and human-floor resolution.
A successful proposal can contain its reply for the sole output owner, avoiding a
mandatory continuation. Ordinary prose stays on the streaming path. Existing
`remain_silent` human-floor reasons now retain bounded source context; unrelated
speech does not clear it. The scheduler and Session defer initiative without a model
call until semantic resolution or the two-minute expiry.

**192 blocked-I/O checks pass** with synthetic voices/providers and temporary stores.
They cover delivery vs generation, named/unnamed participant scope, unrelated speech,
invalid/stale/frozen sources, idempotence, correction, forgetting/restart, history
and feedback eviction, floor release, failed output and one-call replies. They do
not prove the model selected the correct meaning. Hosted evidence and remaining
misses are maintained in the [implementation plan](PROACTIVE_IMPLEMENTATION.md).
No devices or real acoustic models were operated for this slice.

### Contextual memory opportunities (September 12, 2026)

The third proactive slice is implemented: supported self-report follow-ups, explicit
calendar dates, private-by-default candidate filtering, bounded rotation and
persistent completed-audio receipts through production Session. Existing claim
correction/retraction/forgetting invalidates dependent cues. One proactive generation
can select a candidate or leave it unused; no new classifier/provider was added.

**205 blocked-I/O checks pass** using synthetic data and temporary stores. Hosted
synthetic storage/candidate probes passed **6/6** twice, including a shared birthday
and private milestone with a bystander in the final run. Reminder overpromises and
ungrounded greeting wording remain unresolved; these scores are not live semantic,
privacy, humor or identification acceptance. See the full
[implementation evidence](PROACTIVE_IMPLEMENTATION.md).

### Public and visual proactive inputs (September 12, 2026)

The remaining proactive-only software is implemented: dated news/weather cues,
bounded shared-camera image sampling, one-call multimodal scene interpretation,
ambient opportunities and actual-audio delivery deduplication. All existing floor,
audience, cancellation and state-write boundaries apply. No new resident model or
physical actuator executor was added.

**221 blocked-I/O checks pass.** Explicit public-provider checks retrieved BBC news
and Sacramento weather. Four synthetic hosted status cases passed with four primary
requests and one image input; this proves transport and these decisions, not live
animal/gesture recognition or universal source attribution. Detailed measurements
and limitations are in [observation sources](OBSERVATION_SOURCES.md). Live social
and M2/device acceptance is deferred at the user's request until after this delivery.

### Intentional attention groundwork (September 12, 2026)

The first [attention slice](ATTENTION_IMPLEMENTATION.md) adds 12 synthetic production-
Session contract tests; **233 blocked-I/O checks pass**. Optional silent attention
choices use one primary model call and explicitly simulated receipts, with expiry,
cancellation and context continuity. No hosted model or hardware was exercised for
this slice. Live startup does not expose the simulation tool. Target grounding,
coordinated speech/gestures and live social validation remain pending.

### Grounded attention targets (September 12, 2026)

The second [attention slice](ATTENTION_IMPLEMENTATION.md#slice-2-grounded-targets-and-post-movement-evidence)
adds explicit synthetic face/object targets, source/view/calibration validation and
fresh post-movement evidence through production Session. **243 blocked-I/O checks
pass**, including 10 new target/frame scenarios. No live perception or motion adapter
is enabled; hosted selection quality and physical settling/tracking remain untested.

### Coordinated speech and simulated gestures

The [third attention slice](ATTENTION_IMPLEMENTATION.md#slice-3-coordinated-speech-and-gestures)
adds one-call response/gesture proposals through the existing output queue, actual-
playback timing, shared cancellation and separate receipts. **257 blocked-I/O checks
pass**, including 14 new coordination/partial-completion cases with synthetic audio
and motion. Live model selection, physical gesture timing, continuous blending and
combined hardware load remain untested. Normal live startup has no motion adapter.

### Composite visual inspection in simulation

**269 blocked-I/O checks pass**, including 12 new cases for the
[inspection contract](ATTENTION_IMPLEMENTATION.md#composite-visual-inspection-motion-settling-and-capture).
A single LLM-selected tool sequences simulated movement, settling and fresh capture,
then returns the image to one model continuation. Synthetic providers, scripted
model choices and fake hosted HTTP only; no physical motion/capture or real-model
visual interpretation was tested. Description memory and live adapters remain planned.

### Source-linked descriptive visual memory

**279 blocked-I/O checks pass**, including 10 new visual-memory tests. The
[description/reuse slice](ATTENTION_IMPLEMENTATION.md#rich-visual-memory-inspection-description-and-reuse-slice)
uses optional LLM tools on inspection results and cached evidence, with separate
observations/delivery, bounded storage, correction and forgetting. No real image
model or hardware was tested. Cross-session matching, live acquisition and semantic
repetition judgments remain unvalidated.

### Current-view acquisition and hosted dialogue

**289 blocked-I/O checks pass**, with 10 new current-view cases. The existing camera
scene cache now supports tool-selected current-view inspection; no motion driver
is enabled. Four hosted seven-turn synthetic-image trials were reviewed during
iteration. The final run executed four requested inspections with three image
uploads and saved/retracted visual memory, but invented a shape change alongside
a correct color change. See [full evidence and measurements](CURRENT_VIEW_VALIDATION.md).
No live hardware, real-person imagery or physical audio was used.

### Proactive descriptive memory

**301 blocked-I/O checks pass** after adding 12 synthetic production-Session cases.
Fresh proactive scene images can now support `describe_view` in the primary call,
with silent retention or a directly delivered remark. Source freshness, cancellation,
state changes, multi-frame provenance and separate observation/speech receipts are
covered. No additional model call or physical action was added. Hosted spontaneous
selection and live acceptance remain unverified; see the
[implementation and limits](ATTENTION_IMPLEMENTATION.md#proactive-visual-description-and-silent-retention).

### Hosted proactive image dialogue (September 13)

Six 15-turn trials used real hosted inference, saved synthetic illustrations and
production Session with synthetic output; no devices were operated. A scoped
proactive-image context change improved the final restraint cases, but repeated
visual riffs and incomplete correction across similar descriptions remain. Two
quiet-state rejections are not model-selected silence. **302 blocked-I/O checks
pass**; see [measurements and review](PROACTIVE_VISUAL_EVALUATION.md).

### Related visual correction (September 13)

`retract_visual` now accepts LLM-selected batches across all 32 eligible records,
including descriptions outside the eight-record recall window. Atomic withdrawal,
source/revision checks, per-record results and post-commit speech are implemented.
**312 blocked-I/O checks pass.** Two focused hosted runs correctly selected broad,
narrow and ambiguous correction scopes. Whole-record withdrawal can also remove
useful details in the same description; it does not save replacements. See
[implementation and evidence](VISUAL_CORRECTION.md).

### Core integration follow-on

Detail-preserving `revise_visual` is now implemented alongside whole-record withdrawal.
It retains correction provenance and useful unchanged details. Visual novelty context
and action continuity now distinguish observations, selected remarks, heard speech
and abandoned work. **330 blocked-I/O checks pass**, with focused hosted preservation
and simulated interruption evidence. See the [core integration pass](CORE_INTEGRATION_PASS.md)
for current behavior and remaining limits; earlier partial-edit deferrals describe
the preceding slices.
### September 13 enrollment-status follow-up

Read-only inspection of two user-run sessions found no completed voice profiles.
The later session retained three candidate samples totaling 6.306 voiced seconds,
below the unchanged eight-second requirement. It saved face references and later
reported face matches; this does not verify who was speaking. A self-introduction
in the earlier session was rejected for tentative track continuity. A user-labeled
two-speaker capture remained one unresolved turn, but was excluded from learning.
Its waveform was not retained, so the missed boundary remains unvalidated.

Implemented shared enrollment progress in model context and terminal output,
including required/remaining evidence and pending final validation. The model is
instructed to distinguish saved names, face matches and completed voice profiles,
and can invite fuller natural speech instead of repeatedly asking for a name.
This improves visibility and conversational recovery; it does not fix or validate
acoustic recognition, tentative-introduction recovery, or speaker segmentation.

Validation: 332 blocked-I/O checks passed, including a three-turn/six-second
synthetic chain that stays pending across restart and another speaker, then saves
only after compatible additional speech. No personal database changes, hardware,
hosted calls, or real-model accuracy evaluation were performed.

### September 13: pre-detector diagnostic recording

The post-reboot user run received strong microphone input but detected four speech
blocks across roughly 50 seconds, with no endpointed utterance. This does not
establish what sound was received. To close that evidence gap, --record-evaluation
now retains the first 30 PCM seconds of fresh raw input independently of VAD, with
original frame timestamps and echo flags in a separate sidecar. Early shutdown
flushes a shorter recording. It never feeds enrollment or automatically becomes
labeled evaluation data. Memory use is bounded; raw diagnostic write failures are
reported without failing input processing.

Validation: 348 blocked-I/O checks passed. New tests prove retention despite zero
accepted speech, early-close flushing, separate inventories, a strict 30-second
cap and no renewed recording after completion. No microphone/camera was operated.
The missed-speech root cause remains unresolved pending an actual rejected-input
recording; this change adds that evidence path rather than changing VAD thresholds.

### September 13 introduction-first recovery

Trusted introductions can create a person without a usable acoustic track and can
anchor a fresh voice group instead of being vetoed by weak old-group continuity.
Self-reported names remain distinct from recognized speakers. See
[implementation and verification](INTRODUCTION_RECOVERY.md): 379 blocked-I/O checks
pass, including restart, conflicting-profile protection and production Session
coverage. Live multi-person acceptance remains pending; personal data was unchanged.

### September 17 photo-memory handling follow-up

417 blocked-I/O checks pass. Selected photographic teaching must save or explicitly
defer before speech; writes require a named current source, note additions preserve
unrelated details, and multi-pet overview saves are rejected. Authorized saved-photo
hosted replay confirmed both saves and fragment clarification, but still confused
the dogs despite fresh comparisons. No new hardware capture/playback was performed.
See [implementation, measured latency and remaining failures](OBJECT_PHOTO_MEMORY.md#teaching-and-correction-safeguards-september-17-follow-up).


### September 18 greeting-led conversational intentions

The same primary generation can now create or revise a bounded session intention
alongside a greeting, name invitation, memory check-in, reply, feedback or silence.
Intentions retain audience/source references and actual delivery separately; they
cannot write biography, identify speakers or schedule speech. Existing proactive
triggers remain unchanged. The blocked-I/O suite passes **451 checks**, including
34 new intention scenarios with synthetic voices/output and temporary stores.
Hosted fictional comparison evidence and live limitations are recorded in the
[implementation plan](CONVERSATIONAL_INTENT_PLAN.md#verification-evidence).
No live devices or personal databases were operated for this work.


The follow-up request to continue conversation across silence is implemented through
active-plan lull reconsideration. **458 blocked-I/O checks pass**, including seven
new runtime contracts; earlier counts above describe their original runs. Follow-up
opportunities back off without requiring another human turn. Quiet/floor/pending
questions, cancellation, audience scope and attempt budgets still apply. See the
[continuation evidence](CONVERSATIONAL_INTENT_PLAN.md#follow-up-implementation-continuing-lulls).
Live naturalness and timing remain unmeasured.


Following the user's pacing correction, the continuation intervals are now
8/15/30/60 seconds after completion. **459 blocked-I/O checks pass**, including
slow-output timing; the shared rolling call budget and configured lull minimum
remain in force. This timing revision has no new hosted or live acceptance claim.


Latest-run repair evidence: **463 blocked-I/O checks pass** plus four hosted fictional
text probes. See [findings, fixes and limits](LIVE_RUN_CONVERSATION_REPAIRS.md). No
new live device test or acoustic accuracy measurement was performed.

### September 20 photo-memory repair

480 blocked-I/O checks pass. Teaching no longer receives unrelated historical
photos/descriptions; ordinary current-view inspection compares the complete saved
gallery when it contains at most six entries. Tests cover teaching, restart,
description retention, bounded uploads and failed references using synthetic
images and temporary stores. An authorized saved-photo hosted recheck inspected both objects and completed
teaching in an isolated copy; it also exposed and led to a tool-grounding fix.
Live recognition on new views remains pending. See [details and limits](OBJECT_PHOTO_MEMORY.md#september-20-object-teaching-and-restart-repair).
